"""Collecting news: poll each enabled provider when it's due, turn its items into articles, drop
duplicates, categorize, and queue alerts for matching users.

Providers are adapters behind one small interface (NewsProvider), so new kinds of source (an official
API, say) can be added without touching the processing below. Only RSS/Atom is built in.
"""
import asyncio
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Protocol

import httpx

import config
import metrics
from db import Provider, db, now_utc, parse_iso
from netsafe import TooLarge, UnsafeURL, safe_get
from news import Classifier, CategoryRule, RawArticle, RuleClassifier, find_similar, normalize, parse_feed

log = logging.getLogger(__name__)

LOOP_SECONDS = 15  # how often due providers are looked for (each has its own polling interval)
MAX_BACKOFF = timedelta(hours=6)


class FetchError(Exception):
    pass


@dataclass
class FetchResult:
    items: list[RawArticle] = field(default_factory=list)
    etag: str | None = None
    last_modified: str | None = None
    not_modified: bool = False


class NewsProvider(Protocol):
    """An adapter: fetch a provider's newest items and return them in the common RawArticle shape."""

    async def fetch(self, provider: Provider) -> FetchResult: ...


class RssAdapter:
    """RSS and Atom feeds, fetched with conditional GET (ETag / Last-Modified) so unchanged feeds cost
    the publisher almost nothing."""

    async def fetch(self, provider: Provider) -> FetchResult:
        headers = {"Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.5"}
        if provider.etag:
            headers["If-None-Match"] = provider.etag
        if provider.last_modified:
            headers["If-Modified-Since"] = provider.last_modified
        response = await safe_get(provider.feed_url, headers=headers, timeout=provider.timeout_seconds)
        if response.status_code == 304:
            return FetchResult(not_modified=True)
        if response.status_code != 200:
            raise FetchError(f"HTTP {response.status_code}")
        items = await asyncio.to_thread(parse_feed, response.content, response.url)
        if not items and b"<rss" not in response.content[:2000] and b"<feed" not in response.content[:2000]:
            raise FetchError("not an RSS/Atom feed")
        return FetchResult(items=items, etag=response.headers.get("etag"),
                           last_modified=response.headers.get("last-modified"))


ADAPTERS: dict[str, NewsProvider] = {"rss": RssAdapter(), "atom": RssAdapter()}


class Collector:
    def __init__(self, adapters: dict[str, NewsProvider] | None = None, classifier: Classifier | None = None,
                 on_queued: Callable[[], None] | None = None):
        self.adapters = adapters or ADAPTERS
        self.classifier = classifier or RuleClassifier()
        self.on_queued = on_queued  # wakes the sender when alerts are queued
        self._wake = asyncio.Event()
        self._last_cleanup: datetime | None = None
        self._in_flight: set[int] = set()  # providers being fetched right now
        self.last_loop_at: datetime | None = None

    def wake(self) -> None:
        self._wake.set()

    async def run_forever(self) -> None:
        log.info("Collector started")
        while True:
            self.last_loop_at = now_utc()
            try:
                await self.run_once()
                self._cleanup()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Collector loop error")
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=LOOP_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def run_once(self, now: datetime | None = None) -> dict:
        """Fetch every due provider (a few at a time) and process the results one provider at a time.
        One provider failing never stops the others."""
        now = now or now_utc()
        return await self._fetch_all(db.due_providers(now), now)

    async def refresh(self, provider_ids: set[int], stale_seconds: int = 60) -> dict:
        """Fetch these providers now unless they were fetched in the last stale_seconds (used by
        📥 Latest news so people see the newest stories without waiting for the next poll)."""
        now = now_utc()
        stale = [p for p in db.providers(enabled_only=True) if p.id in provider_ids
                 and (not p.last_fetched_at or (now - parse_iso(p.last_fetched_at)).total_seconds() > stale_seconds)]
        return await self._fetch_all(stale, now)

    async def _fetch_all(self, providers: list[Provider], now: datetime) -> dict:
        providers = [p for p in providers if p.id not in self._in_flight]  # already being fetched
        if not providers:
            return {}
        self._in_flight |= {p.id for p in providers}
        semaphore = asyncio.Semaphore(config.FETCH_CONCURRENCY)

        async def fetch(provider: Provider):
            async with semaphore:
                adapter = self.adapters.get(provider.adapter_key)
                if not adapter:
                    return provider, FetchError(f"no adapter '{provider.adapter_key}'")
                try:
                    return provider, await asyncio.wait_for(adapter.fetch(provider), provider.timeout_seconds + 10)
                except Exception as e:  # noqa: BLE001 - every failure is recorded on the provider
                    return provider, e

        summary = {}
        try:
            for provider, result in await asyncio.gather(*(fetch(p) for p in providers)):
                summary[provider.key] = self._record(provider, result, now)
        finally:
            self._in_flight -= {p.id for p in providers}
        return summary

    def _record(self, provider: Provider, result, now: datetime) -> dict:
        if isinstance(result, Exception):
            failures = provider.consecutive_failures + 1
            backoff = min(timedelta(seconds=provider.polling_interval_seconds) * 2 ** min(failures, 8), MAX_BACKOFF)
            error = _describe(result)
            db.record_fetch(provider.id, ok=False, error=error, next_fetch_at=now + backoff)
            metrics.inc("provider_fetch_failure", provider=provider.key)
            log.warning("Fetch %s failed (%s in a row, next in %s): %s", provider.key, failures, backoff, error)
            return {"error": error}
        counts = {"new": 0, "duplicate": 0, "invalid": 0, "queued": 0}
        if not result.not_modified:
            counts = self.process(provider, result.items, now)
        jitter = timedelta(seconds=random.uniform(0, min(60, provider.polling_interval_seconds * 0.1)))
        db.record_fetch(provider.id, ok=True, error=None,
                        next_fetch_at=now + timedelta(seconds=provider.polling_interval_seconds) + jitter,
                        etag=result.etag, last_modified=result.last_modified, new_articles=counts["new"])
        metrics.inc("provider_fetch_success", provider=provider.key)
        if counts["new"] or counts["queued"]:
            log.info("Fetched %s: %s", provider.key, counts)
        if counts["queued"] and self.on_queued:
            self.on_queued()
        return counts

    def _rules(self) -> list[CategoryRule]:
        return [CategoryRule(c.id, c.key, c.keywords) for c in db.categories()]

    def process(self, provider: Provider, items: list[RawArticle], now: datetime) -> dict:
        """Store new items, categorize them and queue alerts. The first fetch of a new provider only
        stores its items (backlog), so subscribers don't get a burst of old news."""
        first_fetch = provider.last_success_at is None
        max_age = timedelta(hours=config.MAX_ARTICLE_AGE_HOURS)
        rules = self._rules()
        recent = db.recent_titles(now - timedelta(hours=config.DEDUP_WINDOW_HOURS))
        counts = {"new": 0, "duplicate": 0, "invalid": 0, "queued": 0}
        metrics.inc("articles_fetched", len(items), provider=provider.key)
        for raw in items:
            article = normalize(raw, provider.language, now, max_age)
            if not article:
                counts["invalid"] += 1
                metrics.inc("articles_invalid")
                continue
            if first_fetch:
                article["is_backlog"] = True
            status, queued = self._store(provider, article, rules, recent)
            counts[status] += 1
            counts["queued"] += queued
        metrics.inc("articles_accepted", counts["new"], provider=provider.key)
        metrics.inc("articles_duplicate", counts["duplicate"])
        return counts

    def _store(self, provider: Provider, article: dict, rules: list[CategoryRule],
               recent: list[tuple[int, str, str, int]]) -> tuple[str, int]:
        # 1. Same item from the same feed: the usual case on every poll.
        if article["external_id"] and db.article_by_external_id(provider.id, article["external_id"]):
            return "duplicate", 0
        # 2. Same URL from another feed (e.g. a story in both BBC World and BBC Africa): one article, but
        #    it also gets this feed's categories, and people following those hear about it once.
        existing = db.article_by_url(article["canonical_url"])
        if existing:
            if existing.provider_id != provider.id:
                db.add_article_categories(existing.id, self.classifier.classify(
                    {"title": existing.title, "summary": existing.summary, "tags": existing.tags},
                    provider.categories, rules))
                if not existing.is_backlog and not article["is_backlog"]:
                    return "duplicate", self.match(existing.id)
            return "duplicate", 0
        # 3. Near-identical headline from another provider: a new article in the same story cluster.
        cluster = find_similar(article["normalized_title"], recent, config.DEDUP_TITLE_SIMILARITY)
        article["story_cluster_id"] = cluster
        article_id = db.insert_article(provider.id, article)
        if article_id is None:  # stored meanwhile (same id or URL)
            return "duplicate", 0
        recent.insert(0, (article_id, article["normalized_title"], article["title_hash"], cluster or article_id))
        if cluster:
            metrics.inc("articles_same_story")
        results = self.classifier.classify(article, provider.categories, rules)
        db.add_article_categories(article_id, results)
        if not any(confidence >= config.CLASSIFY_MIN_CONFIDENCE for _, confidence, _, _ in results):
            metrics.inc("articles_low_confidence")
        if article["is_backlog"]:
            return "new", 0
        return "new", self.match(article_id)

    def match(self, article_id: int) -> int:
        """Queue alerts for everyone who should get this article. Safe to call again (e.g. after an admin
        corrects its categories): nobody gets the same story twice."""
        article = db.get_article(article_id)
        if not article or article.is_backlog:
            return 0
        queued = db.queue_notifications(article, db.matching_users(article))
        metrics.inc("notifications_queued", queued)
        return queued

    def _cleanup(self) -> None:
        """Once an hour, delete articles older than ARTICLE_RETENTION_DAYS (their alerts go with them)."""
        now = now_utc()
        if not config.ARTICLE_RETENTION_DAYS or (self._last_cleanup and now - self._last_cleanup < timedelta(hours=1)):
            return
        self._last_cleanup = now
        deleted = db.delete_old_articles(now - timedelta(days=config.ARTICLE_RETENTION_DAYS))
        if deleted:
            log.info("Deleted %s articles older than %s days", deleted, config.ARTICLE_RETENTION_DAYS)


def _describe(error: Exception) -> str:
    if isinstance(error, (UnsafeURL, TooLarge, FetchError)):
        return str(error)
    if isinstance(error, (asyncio.TimeoutError, httpx.TimeoutException)):
        return "timed out"
    return f"{type(error).__name__}: {error}"

