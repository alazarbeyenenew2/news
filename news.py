"""News processing that doesn't touch the network or the database: turning provider items into clean
articles, canonical URLs, duplicate detection and categorization. Kept pure so it's easy to test.
"""
import calendar
import difflib
import hashlib
import html
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser

from netsafe import UnsafeURL, check_url_syntax

SUMMARY_MAX_CHARS = 420
TITLE_MAX_CHARS = 300
CLASSIFIER_VERSION = "rules-v1"


# ---------- the common shape every provider adapter returns ----------
@dataclass
class RawArticle:
    external_id: str | None
    title: str
    description: str
    url: str
    image_url: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    language: str | None = None
    categories: list[str] = field(default_factory=list)  # provider tags
    extra: dict = field(default_factory=dict)  # adapter-specific data, e.g. {"cve": {...}} from the NVD adapter


class FetchError(Exception):
    """A provider answered, but not with something usable (e.g. HTTP 500, not a feed)."""


@dataclass
class FetchResult:
    """What an adapter returns: the items, plus caching headers for the next conditional request."""
    items: list[RawArticle] = field(default_factory=list)
    etag: str | None = None
    last_modified: str | None = None
    not_modified: bool = False


# ---------- RSS / Atom ----------
def parse_feed(content: bytes, base_url: str = "") -> list[RawArticle]:
    """RSS 0.9x/1.0/2.0 and Atom, via feedparser (which also defuses XML entity tricks)."""
    parsed = feedparser.parse(content, response_headers={"content-location": base_url} if base_url else None)
    language = (parsed.feed.get("language") or "").split("-")[0].lower() or None
    items = []
    for e in parsed.entries:
        link = e.get("link") or ""
        if not link:
            for candidate in e.get("links", []):
                if candidate.get("rel", "alternate") == "alternate" and candidate.get("href"):
                    link = candidate["href"]
                    break
        published = e.get("published_parsed") or e.get("updated_parsed")
        description = e.get("summary") or ""
        if not description and e.get("content"):
            description = e["content"][0].get("value", "")
        items.append(RawArticle(
            external_id=(e.get("id") or e.get("guid") or link or None),
            title=e.get("title") or "",
            description=description,
            url=link,
            image_url=_feed_image(e),
            author=e.get("author"),
            published_at=datetime.fromtimestamp(calendar.timegm(published), timezone.utc) if published else None,
            language=language,
            categories=[t.get("term") for t in e.get("tags", []) if t.get("term")],
        ))
    return items


def _feed_image(entry) -> str | None:
    """The image the feed itself gives for an item (we never scrape the article page for one)."""
    candidates = []
    for thumb in entry.get("media_thumbnail", []) or []:
        candidates.append((int(thumb.get("width") or 0), thumb.get("url")))
    for media in entry.get("media_content", []) or []:
        if (media.get("medium") == "image" or (media.get("type") or "").startswith("image/")
                or re.search(r"\.(jpe?g|png|webp|gif)(\?|$)", media.get("url") or "", re.I)):
            candidates.append((int(media.get("width") or 0), media.get("url")))
    for link in entry.get("links", []) or []:
        if link.get("rel") == "enclosure" and (link.get("type") or "").startswith("image/"):
            candidates.append((0, link.get("href")))
    candidates = [(w, u) for w, u in candidates if u]
    if not candidates:
        return None
    return max(candidates, key=lambda c: c[0])[1]  # the largest one


# ---------- cleaning ----------
_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"\s+")
_SENTENCE_END = re.compile(r"(?<=[.!?።])\s+")


def clean_text(value: str | None) -> str:
    """Plain text from provider HTML: tags dropped, entities decoded, whitespace collapsed."""
    if not value:
        return ""
    value = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", value)
    value = _TAGS.sub(" ", value)
    value = html.unescape(value)
    value = "".join(ch for ch in value if unicodedata.category(ch)[0] != "C" or ch in "\n\t")
    return _SPACES.sub(" ", value).strip()


def short_summary(description: str, title: str = "", limit: int = SUMMARY_MAX_CHARS) -> str:
    """At most ~4 sentences / limit characters of the provider's own description, cut at a sentence end
    when possible. Nothing is added: if the provider gives no description, the summary is empty."""
    text = clean_text(description)
    # Feeds often end descriptions with boilerplate like "The post X appeared first on Y."
    text = re.sub(r"\s*The post .{0,300}? appeared first on .{0,100}?\.?$", "", text)
    text = re.sub(r"\s*\[(…|\.\.\.)\]\s*$", "…", text)
    if not text or text.casefold() == clean_text(title).casefold():
        return ""
    sentences = _SENTENCE_END.split(text)
    out = ""
    for sentence in sentences[:4]:
        candidate = f"{out} {sentence}".strip()
        if len(candidate) > limit:
            break
        out = candidate
    if not out:
        cut = text[:limit - 1]
        out = (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip(",;:-– ") + "…"
    return out


# ---------- URLs ----------
TRACKING_PARAMS = re.compile(
    r"^(utm_[a-z_]+|fbclid|gclid|dclid|msclkid|igshid|mc_cid|mc_eid|ocid|cmpid|cmp|_ga|at_[a-z_]+|"
    r"ito|xtor|ns_[a-z_]+|rss|feed|feedname|src_?id|spm)$", re.I)


def canonical_url(url: str) -> str:
    """Same article, same string: lower-case scheme and host, no default port, no fragment, no tracking
    parameters, remaining parameters sorted, no trailing slash."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if parts.port and not ((scheme == "http" and parts.port == 80) or (scheme == "https" and parts.port == 443)):
        host = f"{host}:{parts.port}"
    query = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not TRACKING_PARAMS.match(k))
    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    return urlunsplit((scheme, host, path, urlencode(query), ""))


# ---------- titles ----------
_SOURCE_SUFFIX = re.compile(r"\s+[-–—|]\s+[^-–—|]{2,40}$")
_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)


def normalize_title(title: str) -> str:
    """Lower-case, no punctuation or accents, single spaces. Used for duplicate detection."""
    text = clean_text(title)
    text = _SOURCE_SUFFIX.sub("", text) if len(text) > 30 else text
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = _NON_WORD.sub(" ", text.casefold())
    return _SPACES.sub(" ", text).strip()


def title_hash(normalized: str) -> str:
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def titles_similar(a: str, b: str, threshold: float) -> bool:
    """Near-identical normalized headlines. Short headlines ("Live updates") never match, and word overlap
    is checked first, so unrelated stories that merely share a few words aren't merged."""
    ta, tb = set(a.split()), set(b.split())
    if min(len(ta), len(tb)) < 5:
        return a == b and len(ta) >= 3
    jaccard = len(ta & tb) / len(ta | tb)
    if jaccard < 0.6:
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= threshold


def find_similar(normalized: str, recent: list[tuple[int, str, str, int]], threshold: float) -> int | None:
    """The story cluster of a recent article with the same or a near-identical headline, if any.
    recent: (id, normalized_title, title_hash, cluster_id), newest first."""
    digest = title_hash(normalized)
    for _, other, other_hash, cluster in recent:
        if other_hash == digest and len(normalized.split()) >= 3:
            return cluster
    for _, other, _, cluster in recent:
        if titles_similar(normalized, other, threshold):
            return cluster
    return None


# ---------- validation / normalization ----------
def normalize(raw: RawArticle, provider_language: str, now: datetime, max_age: timedelta) -> dict | None:
    """A provider item as an article row, or None if it can't be used (no title, unsafe or missing link).
    Missing details stay missing: no invented images, dates or summaries."""
    title = clean_text(raw.title)[:TITLE_MAX_CHARS].strip()
    if not title or not raw.url:
        return None
    try:
        url = canonical_url(check_url_syntax(raw.url))
    except (UnsafeURL, ValueError):
        return None
    image = None
    if raw.image_url:
        try:
            image = check_url_syntax(raw.image_url, https_only=False)
        except UnsafeURL:
            image = None
    published = raw.published_at
    if published and published > now + timedelta(hours=1):
        published = None  # a date in the future is a feed error; don't show it
    summary = short_summary(raw.description, title)
    normalized = normalize_title(title)
    language = (provider_language or raw.language or "en")[:5]
    reference = published or now
    return {
        "external_id": (raw.external_id or "")[:500] or None,
        "title": title,
        "normalized_title": normalized,
        "title_hash": title_hash(normalized),
        "summary": summary or None,
        "canonical_url": url,
        "image_url": image,
        "author": clean_text(raw.author)[:200] or None,
        "language": language,
        "published_at": published.astimezone(timezone.utc).isoformat() if published else None,
        "fetched_at": now.isoformat(),
        "content_hash": hashlib.sha256(f"{title}\n{summary}\n{url}".encode("utf-8")).hexdigest(),
        "is_backlog": now - reference > max_age,
        "tags": [clean_text(t)[:80] for t in raw.categories if clean_text(t)][:20],
        "extra": raw.extra,
    }


# ---------- categorization ----------
@dataclass
class CategoryRule:
    id: int
    key: str
    keywords: dict  # phrase -> weight


class Classifier(Protocol):
    """Anything that can put an article into categories. The rule classifier below is the default; an AI
    classifier can implement the same method and be combined with it in collector.py."""

    def classify(self, article: dict, provider_categories: dict, rules: list[CategoryRule]
                 ) -> list[tuple[int, float, str, str]]: ...


def _keyword_pattern(phrase: str) -> re.Pattern:
    escaped = re.escape(phrase.casefold()).replace(r"\ ", r"\s+")
    if phrase.isascii():
        return re.compile(rf"(?<![\w-]){escaped}(?![\w])")
    return re.compile(escaped)  # Ge'ez script: prefixes / suffixes attach to the word


class RuleClassifier:
    """Provider defaults + keyword rules + provider tags, combined with a noisy-OR:
    confidence = 1 - Π(1 - evidence). A keyword in the headline counts in full, in the summary at 60%,
    in a provider tag at 80%. Each piece of evidence is kept in `reason` for debugging."""

    SUMMARY_FACTOR = 0.6
    TAG_FACTOR = 0.8

    def __init__(self):
        self._patterns: dict[str, re.Pattern] = {}

    def _pattern(self, phrase: str) -> re.Pattern:
        if phrase not in self._patterns:
            self._patterns[phrase] = _keyword_pattern(phrase)
        return self._patterns[phrase]

    def classify(self, article: dict, provider_categories: dict, rules: list[CategoryRule]
                 ) -> list[tuple[int, float, str, str]]:
        title = clean_text(article.get("title")).casefold()
        summary = clean_text(article.get("summary")).casefold()
        tags = " | ".join(article.get("tags") or []).casefold()
        results = []
        for rule in rules:
            evidence: list[tuple[float, str]] = []
            base = float(provider_categories.get(rule.key, 0) or 0)
            if base > 0:
                evidence.append((min(base, 1.0), f"provider {base:g}"))
            for phrase, weight in rule.keywords.items():
                weight = float(weight)
                if weight <= 0:
                    continue
                pattern = self._pattern(phrase)
                if pattern.search(title):
                    evidence.append((weight, f"title '{phrase}'"))
                elif pattern.search(summary):
                    evidence.append((weight * self.SUMMARY_FACTOR, f"summary '{phrase}'"))
                elif tags and pattern.search(tags):
                    evidence.append((weight * self.TAG_FACTOR, f"tag '{phrase}'"))
            if not evidence:
                continue
            remaining = 1.0
            for value, _ in evidence:
                remaining *= 1.0 - min(max(value, 0.0), 1.0)
            confidence = round(1.0 - remaining, 3)
            evidence.sort(key=lambda e: -e[0])
            reason = "; ".join(f"{label} ({value:.2f})" for value, label in evidence[:8])
            results.append((rule.id, confidence, CLASSIFIER_VERSION, reason))
        return sorted(results, key=lambda r: -r[1])


# ---------- CVE technology matching ----------
_TERM_PATTERNS: dict[str, re.Pattern] = {}


def cve_term_matches(text: str, aliases: list[str]) -> bool:
    """Whether any of a technology's names appears in the text as a whole word / phrase, any case
    ("nginx" matches "NGINX Plus" but not "nginxy")."""
    folded = text.casefold()
    for alias in aliases:
        alias = (alias or "").strip()
        if not alias:
            continue
        if alias not in _TERM_PATTERNS:
            _TERM_PATTERNS[alias] = _keyword_pattern(alias)
        if _TERM_PATTERNS[alias].search(folded):
            return True
    return False
