"""Collection -> categorization -> matching -> delivery, end to end (fake feeds, fake Telegram)."""
import asyncio
from datetime import datetime, timedelta

import pytest
from telegram import Bot
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter

import config
from collector import Collector, FetchResult
from db import Database, db, now_utc
from delivery import Digester, Sender
from news import RawArticle

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def make_user(telegram_id: int, topics: list[str], **settings) -> None:
    db.upsert_user(telegram_id, telegram_id, None, f"User{telegram_id}")
    db.update_user(telegram_id, language="en", onboarding_completed=True, **settings)
    db.set_interests(telegram_id, {db.category_by_key(k).id for k in topics})


def provider(key: str, fetched_before: bool = True):
    p = next(p for p in db.providers() if p.key == key)
    if fetched_before:
        db.record_fetch(p.id, ok=True, error=None, next_fetch_at=now_utc())
    return db.get_provider(p.id)


def raw(guid: str, title: str, summary: str = "", url: str = "", image: str | None = None, minutes_ago: int = 10):
    return RawArticle(external_id=guid, title=title, description=summary, url=url, image_url=image,
                      published_at=now_utc() - timedelta(minutes=minutes_ago))


def statuses(user_id: int) -> list[str]:
    return [r["status"] for r in db.execute("SELECT status FROM notifications WHERE user_id = %s ORDER BY id",
                                            (user_id,))]


@pytest.fixture
def bot_and_api(telegram):
    bot = Bot(config.BOT_TOKEN, request=telegram, get_updates_request=telegram)
    asyncio.run(bot.initialize())
    return bot, telegram


def send_all(sender: Sender) -> list[str]:
    async def run():
        return [await sender.send_one(n) for n in db.due_notifications(100)]
    return asyncio.run(run())


CYBER = ("Critical VPN vulnerability exploited by ransomware gang, vendor warns",
         "Attackers exploited a zero-day flaw in VPN appliances. A patch is out.")


def test_end_to_end_scenario(bot_and_api):
    """The scenario from the plan's testing strategy, steps 1-10."""
    bot, api = bot_and_api
    sender, collector = Sender(bot), Collector()
    make_user(100, ["cybersecurity", "sports"])                         # 1-2
    thn = provider("thehackernews")

    counts = collector.process(thn, [raw("thn-1", *CYBER, url="https://thehackernews.com/2026/11/vpn.html",
                                         image="https://img.example.com/vpn.jpg")], now_utc())   # 3
    assert counts == {"new": 1, "duplicate": 0, "invalid": 0, "queued": 1}
    article = db.recent_articles(1)[0]
    assert any(c["key"] == "cybersecurity" and c["confidence"] >= 0.6 for c in article["categories"])  # 4

    assert send_all(sender) == ["sent"]                                 # 5
    photo = api.last("sendPhoto")
    assert photo["photo"] == "https://img.example.com/vpn.jpg"
    assert "🔐 <b>Cybersecurity</b>" in photo["caption"]
    assert "Source: The Hacker News" in photo["caption"] and "Published:" in photo["caption"]
    assert photo["reply_markup"]["inline_keyboard"][0][0]["url"] == "https://thehackernews.com/2026/11/vpn.html"

    # 6: the same feed item again, and the same story from another provider: no second alert.
    assert collector.process(thn, [raw("thn-1", *CYBER, url="https://thehackernews.com/2026/11/vpn.html")],
                             now_utc())["queued"] == 0
    bleeping = provider("bleepingcomputer")
    again = collector.process(bleeping, [raw("bc-9", CYBER[0] + " - BleepingComputer", CYBER[1],
                                             url="https://www.bleepingcomputer.com/news/vpn-flaw/")], now_utc())
    assert again["new"] == 1 and again["queued"] == 0
    assert db._one("SELECT COUNT(*) FROM articles WHERE story_cluster_id IS NOT NULL") == 1
    assert statuses(100) == ["sent"]

    # 7-8: drop Cybersecurity, keep Sports: later cyber news isn't delivered.
    db.set_interests(100, {db.category_by_key("sports").id})
    assert collector.process(thn, [raw("thn-2", "New malware botnet targets routers worldwide", "",
                                       url="https://thehackernews.com/2026/11/botnet.html")], now_utc())["queued"] == 0

    # 9: paused users get nothing.
    db.update_user(100, notifications_enabled=False)
    sport = provider("bbc_sport")
    assert collector.process(sport, [raw("s-1", "Striker scores twice in Premier League derby", "",
                                         url="https://www.bbc.com/sport/football/1")], now_utc())["queued"] == 0
    # 10: resumed users get new eligible alerts.
    db.update_user(100, notifications_enabled=True)
    assert collector.process(sport, [raw("s-2", "Marathon record falls in Berlin", "",
                                         url="https://www.bbc.com/sport/athletics/2")], now_utc())["queued"] == 1
    assert send_all(sender) == ["sent"]
    assert "⚽" in api.last("sendMessage")["text"]  # no image: text alert


def test_first_fetch_is_backlog_and_new_subscribers_get_no_old_news(bot_and_api):
    collector = Collector()
    make_user(100, ["world", "africa"])
    world = provider("bbc_world", fetched_before=False)
    counts = collector.process(world, [raw("w-1", "Leaders meet to discuss regional plan", "",
                                           url="https://www.bbc.com/news/world-1")], now_utc())
    assert counts["new"] == 1 and counts["queued"] == 0  # first fetch: stored, not alerted
    assert db.recent_articles(1)[0]["article"].is_backlog


def test_same_url_in_two_feeds_adds_category_once(bot_and_api):
    collector = Collector()
    make_user(100, ["africa"])
    make_user(101, ["world", "africa"])
    item = lambda guid: raw(guid, "Leaders meet to discuss regional plan", "",  # noqa: E731
                            url="https://www.bbc.com/news/articles/abc?at_medium=RSS")
    assert collector.process(provider("bbc_world"), [item("w-1")], now_utc())["queued"] == 1  # user 101 (world)
    assert collector.process(provider("bbc_africa"), [item("a-1")], now_utc())["queued"] == 1  # user 100 (africa)
    assert statuses(100) == ["queued"] and statuses(101) == ["queued"]
    assert db._one("SELECT COUNT(*) FROM articles") == 1


def test_subscribing_later_does_not_deliver_earlier_articles():
    collector = Collector()
    make_user(101, ["world"])
    item = lambda guid: raw(guid, "Leaders meet to discuss regional plan", "",  # noqa: E731
                            url="https://www.bbc.com/news/articles/xyz")
    collector.process(provider("bbc_world"), [item("w-1")], now_utc())
    make_user(100, ["africa"])  # subscribes after the article was found
    assert collector.process(provider("bbc_africa"), [item("a-1")], now_utc())["queued"] == 0


def test_low_confidence_is_held_for_review():
    collector = Collector()
    make_user(100, ["cybersecurity", "technology", "world", "business"])
    db.seed([], [{"key": "t_general", "name": "General", "base_url": "https://g.example.com",
                  "feed_url": "https://g.example.com/rss", "enabled": True, "categories": {}}])
    p = provider("t_general")  # no default topic: only keywords count
    counts = collector.process(p, [raw("r-1", "Company releases quarterly update",
                                       "The new patch fixes small issues.", url="https://example.et/1")], now_utc())
    assert counts["new"] == 1 and counts["queued"] == 0  # weak evidence: nobody alerted
    low = db.recent_articles(10, low_confidence=True)
    assert [r["article"].title for r in low] == ["Company releases quarterly update"]
    article_id = low[0]["article"].id
    db.set_manual_categories(article_id, {db.category_by_key("business").id})
    assert collector.match(article_id) == 1  # an admin's correction delivers it
    assert collector.match(article_id) == 0  # and only once
    assert db.recent_articles(10, low_confidence=True) == []


def test_daily_limit_and_muted_topics():
    collector = Collector()
    make_user(100, ["sports"], max_per_day=1)
    sport = provider("bbc_sport")
    collector.process(sport, [raw("1", "Arsenal win again in cup tie", "", url="https://e.com/1"),
                              raw("2", "Tennis star reaches Wimbledon final", "", url="https://e.com/2")], now_utc())
    assert statuses(100) == ["queued", "skipped"]
    db.set_interest_enabled(100, db.category_by_key("sports").id, False)
    collector.process(sport, [raw("3", "Basketball finals open tonight", "", url="https://e.com/3")], now_utc())
    assert statuses(100) == ["queued", "skipped"]  # muted: nothing new


def test_photo_failure_falls_back_to_text(bot_and_api):
    bot, api = bot_and_api
    make_user(100, ["cybersecurity"])
    Collector().process(provider("thehackernews"), [raw("1", *CYBER, url="https://e.com/1",
                                                        image="https://img.example.com/broken.jpg")], now_utc())
    api.fail.append(("sendPhoto", BadRequest("Wrong file identifier/http url specified")))
    assert send_all(Sender(bot)) == ["sent"]
    assert api.methods()[-2:] == ["sendPhoto", "sendMessage"]


def test_retry_after_is_respected(bot_and_api):
    bot, api = bot_and_api
    make_user(100, ["cybersecurity"])
    Collector().process(provider("thehackernews"), [raw("1", *CYBER, url="https://e.com/1")], now_utc())
    api.fail.append(("sendMessage", RetryAfter(30)))
    sender = Sender(bot)
    assert send_all(sender) == ["queued"]
    row = db.execute("SELECT attempt_count, next_attempt_at FROM notifications").fetchone()
    assert row["attempt_count"] == 0 and row["next_attempt_at"] > now_utc() + timedelta(seconds=25)
    assert db.due_notifications() == []


def test_temporary_errors_retry_then_give_up(bot_and_api):
    bot, api = bot_and_api
    make_user(100, ["cybersecurity"])
    Collector().process(provider("thehackernews"), [raw("1", *CYBER, url="https://e.com/1")], now_utc())
    sender = Sender(bot)
    api.fail.append(("sendMessage", NetworkError("connection reset")))
    assert send_all(sender) == ["queued"]
    assert db.due_notifications() == []  # backoff: not due yet
    db.execute("UPDATE notifications SET next_attempt_at = %s, attempt_count = %s",
               (now_utc().isoformat(), config.MAX_SEND_ATTEMPTS - 1))
    api.fail.append(("sendMessage", NetworkError("connection reset")))
    assert send_all(sender) == ["failed"]
    assert len(db.failed_notifications()) == 1


def test_blocked_user_is_marked_and_not_retried(bot_and_api):
    bot, api = bot_and_api
    make_user(100, ["cybersecurity"])
    Collector().process(provider("thehackernews"), [raw("1", *CYBER, url="https://e.com/1"),
                                                    raw("2", "Phishing kit sold on forums", "", url="https://e.com/2")],
                        now_utc())
    api.fail.append(("sendMessage", Forbidden("Forbidden: bot was blocked by the user")))
    assert send_all(Sender(bot)) == ["failed", "skipped"]  # the second one is skipped, not sent
    assert db.get_user(100).status == "blocked"
    assert statuses(100) == ["failed", "skipped"]


def test_eligibility_is_rechecked_at_send_time(bot_and_api):
    bot, _ = bot_and_api
    make_user(100, ["cybersecurity"])
    Collector().process(provider("thehackernews"), [raw("1", *CYBER, url="https://e.com/1")], now_utc())
    db.update_user(100, notifications_enabled=False)
    assert send_all(Sender(bot)) == ["skipped"]


def test_digest_mode(bot_and_api):
    bot, api = bot_and_api
    make_user(100, ["sports"], delivery_mode="digest", digest_hour=8)
    Collector().process(provider("bbc_sport"), [raw("1", "Arsenal win again in cup tie", "", url="https://e.com/1"),
                                                raw("2", "Tennis star reaches Wimbledon final", "",
                                                    url="https://e.com/2")], now_utc())
    assert statuses(100) == ["digest", "digest"]
    assert db.due_notifications() == []
    digester = Digester(bot)
    morning = datetime(2026, 11, 27, 8, 0, tzinfo=config.TIMEZONE)
    assert asyncio.run(digester.run_once(morning.replace(hour=7))) == 0  # not their hour
    assert asyncio.run(digester.run_once(morning)) == 1
    text = api.last("sendMessage")["text"]
    assert "Your daily digest" in text and 'href="https://e.com/1"' in text and "BBC Sport" in text
    assert statuses(100) == ["sent", "sent"]
    assert asyncio.run(digester.run_once(morning.replace(minute=30))) == 0  # once a day


def test_one_provider_failing_does_not_stop_others():
    class Ok:
        async def fetch(self, p):
            return FetchResult(items=[raw("ok-1", "Leaders meet to discuss regional plan", "",
                                          url="https://ok.example.com/1")])

    class Boom:
        async def fetch(self, p):
            raise RuntimeError("feed is down")

    db.execute("UPDATE providers SET is_enabled = FALSE")
    db.seed([], [{"key": "t_ok", "name": "OK", "base_url": "https://ok.example.com", "feed_url": "https://ok.example.com/rss",
                  "adapter": "ok", "enabled": True, "categories": {"world": 0.9}},
                 {"key": "t_boom", "name": "Boom", "base_url": "https://b.example.com", "feed_url": "https://b.example.com/rss",
                  "adapter": "boom", "enabled": True}])
    collector = Collector(adapters={"ok": Ok(), "boom": Boom()})
    summary = asyncio.run(collector.run_once())
    assert summary["t_ok"]["new"] == 1
    assert "feed is down" in summary["t_boom"]["error"]
    boom = next(p for p in db.providers() if p.key == "t_boom")
    assert boom.consecutive_failures == 1 and boom.last_error
    assert datetime.fromisoformat(boom.next_fetch_at) > now_utc() + timedelta(seconds=boom.polling_interval_seconds)
    assert asyncio.run(collector.run_once()) == {}  # nothing due right after


def test_preferences_survive_a_restart():
    make_user(100, ["cybersecurity", "sports"], delivery_mode="digest")
    reopened = Database()  # a new connection, as after a restart
    assert reopened.get_user(100).delivery_mode == "digest"
    assert len(reopened.interests(100)) == 2
    reopened.close()


def test_backup_zip_has_every_table():
    import io
    import zipfile

    import backup
    make_user(100, ["sports"])
    data, name, counts = backup.make_backup()
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert set(names) == {f"{t}.csv" for t in db.BACKUP_TABLES} | {"README.txt"}
    assert counts["users"] == 1 and name.endswith(".zip")
    users_csv = zipfile.ZipFile(io.BytesIO(data)).read("users.csv").decode()
    assert users_csv.startswith("telegram_id,") and "User100" in users_csv


def test_reconnects_after_the_database_drops_the_connection():
    other = Database()
    other.execute("SELECT pg_terminate_backend(%s)", (db.conn.info.backend_pid,))
    other.close()
    assert db.ping()  # reconnected on its own
