"""CVE data: adapters for NIST's National Vulnerability Database and CISA's Known Exploited Vulnerabilities
catalog. Both turn CVEs into RawArticles carrying the CVE details in extra["cve"]; the collector stores
them as articles plus a row in `cves`, and alerts users whose technologies they mention.
"""
import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

import config
from db import Provider, now_utc, parse_iso
from netsafe import safe_get
from news import FetchError, FetchResult, RawArticle, clean_text

NVD_PAGE_SIZE = 2000
NVD_MAX_PAGES = 5
NVD_FIRST_WINDOW = timedelta(hours=24)  # a new NVD provider looks back this far (stored, not alerted)
NVD_OVERLAP = timedelta(minutes=30)     # re-ask a little before the last success, in case NVD was late
NVD_PAUSE_SECONDS = 6.5                 # without an API key NVD allows 5 requests per 30 seconds
KEV_RECENT_DAYS = 3                     # KEV entries added this recently are new to us
METRIC_KEYS = ("cvssMetricV40", "cvssMetricV31", "cvssMetricV30", "cvssMetricV2")
SKIP_STATUSES = {"Rejected", "Reserved"}
MAX_BYTES = 60 * 1024 * 1024


def nvd_url(cve_id: str) -> str:
    return f"https://nvd.nist.gov/vuln/detail/{cve_id}"


def _first_sentence(text: str, limit: int = 160) -> str:
    sentence = text.split(". ")[0].strip()
    return sentence if len(sentence) <= limit else sentence[: limit - 1].rstrip() + "…"


def cvss(metrics: dict) -> tuple[float | None, str | None, str | None]:
    """(score, severity, version) from NVD's metrics, preferring the newest CVSS version and the primary
    (NVD's own) score."""
    for key in METRIC_KEYS:
        entries = metrics.get(key) or []
        if not entries:
            continue
        entry = next((e for e in entries if e.get("type") == "Primary"), entries[0])
        data = entry.get("cvssData", {})
        severity = data.get("baseSeverity") or entry.get("baseSeverity")
        return data.get("baseScore"), (severity or "").upper() or None, data.get("version")
    return None, None, None


def products(configurations: list) -> list[str]:
    """'vendor product' names from the CPE entries NVD adds once it has analysed a CVE."""
    names = []
    for config_ in configurations or []:
        for node in config_.get("nodes", []):
            for match in node.get("cpeMatch", []):
                parts = (match.get("criteria") or "").split(":")
                if len(parts) > 4:
                    name = f"{parts[3]} {parts[4]}".replace("_", " ")
                    if name not in names:
                        names.append(name)
    return names[:30]


def parse_nvd_cve(cve: dict) -> RawArticle | None:
    """One CVE from the NVD 2.0 API as a RawArticle, or None for rejected / reserved ones."""
    if cve.get("vulnStatus") in SKIP_STATUSES:
        return None
    cve_id = cve.get("id") or ""
    description = next((d["value"] for d in cve.get("descriptions", []) if d.get("lang") == "en"), "")
    description = clean_text(description)
    if not cve_id.startswith("CVE-") or not description or description.lower().startswith("rejected reason"):
        return None
    score, severity, version = cvss(cve.get("metrics") or {})
    names = products(cve.get("configurations"))
    published = parse_iso(cve.get("published"))
    if published and published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    return RawArticle(
        external_id=cve_id, title=f"{cve_id} · {_first_sentence(description)}", description=description,
        url=nvd_url(cve_id), published_at=published, language="en", categories=names,
        extra={"cve": {"cve_id": cve_id, "description": description[:2000], "cvss_score": score,
                       "severity": severity if severity in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "NONE") else None,
                       "cvss_version": version, "products": names}})


class NvdAdapter:
    """CVEs published since the last successful fetch, from https://services.nvd.nist.gov/rest/json/cves/2.0.
    Set NVD_API_KEY for NIST's higher rate limit (optional)."""

    async def fetch(self, provider: Provider) -> FetchResult:
        end = now_utc()
        last = parse_iso(provider.last_success_at)
        start = (last - NVD_OVERLAP) if last else end - NVD_FIRST_WINDOW
        start = max(start, end - timedelta(days=119))  # NVD allows at most 120 days per request
        headers = {"Accept": "application/json"}
        if config.NVD_API_KEY:
            headers["apiKey"] = config.NVD_API_KEY
        items: list[RawArticle] = []
        index = 0
        for page in range(NVD_MAX_PAGES):
            if page:
                await asyncio.sleep(1 if config.NVD_API_KEY else NVD_PAUSE_SECONDS)
            query = urlencode({"pubStartDate": _nvd_time(start), "pubEndDate": _nvd_time(end),
                               "resultsPerPage": NVD_PAGE_SIZE, "startIndex": index})
            response = await safe_get(f"{provider.feed_url}?{query}", headers=headers,
                                      timeout=provider.timeout_seconds, max_bytes=MAX_BYTES)
            if response.status_code != 200:
                raise FetchError(f"HTTP {response.status_code}")
            try:
                data = json.loads(response.content)
            except ValueError:
                raise FetchError("NVD did not return JSON") from None
            for entry in data.get("vulnerabilities", []):
                item = parse_nvd_cve(entry.get("cve") or {})
                if item:
                    items.append(item)
            index += int(data.get("resultsPerPage") or NVD_PAGE_SIZE)
            if index >= int(data.get("totalResults") or 0):
                break
        return FetchResult(items=items)


def _nvd_time(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def parse_kev(catalog: dict, today: date) -> list[RawArticle]:
    """Entries CISA added to its Known Exploited Vulnerabilities catalog in the last KEV_RECENT_DAYS days."""
    items = []
    since = today - timedelta(days=KEV_RECENT_DAYS)
    for entry in catalog.get("vulnerabilities", []):
        try:
            added = date.fromisoformat(entry.get("dateAdded", ""))
        except ValueError:
            continue
        cve_id = entry.get("cveID") or ""
        if added < since or not cve_id.startswith("CVE-"):
            continue
        product = clean_text(f"{entry.get('vendorProject', '')} {entry.get('product', '')}")
        name = clean_text(entry.get("vulnerabilityName") or "")
        description = clean_text(entry.get("shortDescription") or name)
        items.append(RawArticle(
            external_id=f"kev:{cve_id}", title=f"{cve_id} · {name or product}", description=description,
            url=nvd_url(cve_id), published_at=None, language="en", categories=[product] if product else [],
            extra={"cve": {"cve_id": cve_id, "description": f"{name}. {description}".strip(". ")[:2000],
                           "products": [product] if product else [], "kev": True, "kev_date": added.isoformat(),
                           "kev_ransomware": (entry.get("knownRansomwareCampaignUse") or "").lower() == "known"}}))
    return items


class KevAdapter:
    """CISA's Known Exploited Vulnerabilities catalog (JSON), fetched with conditional GET."""

    async def fetch(self, provider: Provider) -> FetchResult:
        headers = {"Accept": "application/json"}
        if provider.etag:
            headers["If-None-Match"] = provider.etag
        if provider.last_modified:
            headers["If-Modified-Since"] = provider.last_modified
        response = await safe_get(provider.feed_url, headers=headers, timeout=provider.timeout_seconds,
                                  max_bytes=MAX_BYTES)
        if response.status_code == 304:
            return FetchResult(not_modified=True)
        if response.status_code != 200:
            raise FetchError(f"HTTP {response.status_code}")
        try:
            catalog = json.loads(response.content)
        except ValueError:
            raise FetchError("CISA KEV did not return JSON") from None
        return FetchResult(items=parse_kev(catalog, now_utc().date()), etag=response.headers.get("etag"),
                           last_modified=response.headers.get("last-modified"))
