"""In-process counters, served at /metrics in Prometheus text format. They reset when the process
restarts; the admin page's numbers come from the database instead and survive restarts."""
import threading
from collections import defaultdict
from datetime import datetime

_lock = threading.Lock()
_counters: dict[str, float] = defaultdict(float)
_labelled: dict[tuple[str, str, str], float] = defaultdict(float)
_delivery_seconds = {"count": 0, "sum": 0.0}

HELP = {
    "provider_fetch_success": "Provider fetches that succeeded",
    "provider_fetch_failure": "Provider fetches that failed",
    "articles_fetched": "Items read from provider feeds",
    "articles_accepted": "New articles stored",
    "articles_duplicate": "Items that were already stored (same id or URL)",
    "articles_same_story": "New articles grouped with an earlier story",
    "articles_invalid": "Items dropped by validation",
    "articles_low_confidence": "New articles with no confident category",
    "notifications_queued": "Alerts queued",
    "notifications_sent": "Alerts sent",
    "notifications_failed": "Alerts that failed permanently",
    "notifications_retried": "Alerts scheduled for another attempt",
    "notifications_skipped": "Alerts skipped at send time",
    "telegram_rate_limited": "Telegram retry_after responses",
    "image_fallbacks": "Photo alerts sent as text because the image failed",
    "news_pulled": "Stories sent because a user tapped 📥 Latest news",
    "cves_stored": "New CVEs stored",
    "cves_updated": "Stored CVEs whose products, severity or exploited flag changed",
}


def inc(name: str, value: float = 1, provider: str | None = None) -> None:
    with _lock:
        _counters[name] += value
        if provider:
            _labelled[(name, "provider", provider)] += value


def observe_delivery(article) -> None:
    """Seconds from publication (or from when we found it) to delivery."""
    start = article.published_at or article.fetched_at
    try:
        seconds = (datetime.now().astimezone() - datetime.fromisoformat(start)).total_seconds()
    except (TypeError, ValueError):
        return
    with _lock:
        _delivery_seconds["count"] += 1
        _delivery_seconds["sum"] += max(seconds, 0)


def render(gauges: dict[str, float]) -> str:
    lines = []
    with _lock:
        for name in sorted(set(HELP) | set(_counters)):
            metric = f"newsbot_{name}_total"
            lines += [f"# HELP {metric} {HELP.get(name, name)}", f"# TYPE {metric} counter",
                      f"{metric} {_counters.get(name, 0):g}"]
            for (n, label, value), count in sorted(_labelled.items()):
                if n == name:
                    lines.append(f'{metric}{{{label}="{value}"}} {count:g}')
        lines += ["# HELP newsbot_delivery_latency_seconds Publication-to-delivery time",
                  "# TYPE newsbot_delivery_latency_seconds summary",
                  f"newsbot_delivery_latency_seconds_count {_delivery_seconds['count']}",
                  f"newsbot_delivery_latency_seconds_sum {_delivery_seconds['sum']:.1f}"]
    for name, value in sorted(gauges.items()):
        lines += [f"# TYPE newsbot_{name} gauge", f"newsbot_{name} {value:g}"]
    return "\n".join(lines) + "\n"
