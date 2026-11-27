"""Telegram handlers, driven through the real python-telegram-bot application with a fake Bot API."""
import asyncio

import pytest
from telegram import Update

import texts
import texts_am
import texts_en
from bot import build_application
from db import db
from tests.conftest import callback_update, message_update

USER = 100
SUPER = 999


class Chat:
    """Feeds updates to the bot and remembers what it answered."""

    def __init__(self, telegram):
        self.api = telegram
        self.app = build_application(request=telegram)
        self.loop = asyncio.new_event_loop()
        self.loop.run_until_complete(self.app.initialize())
        self.n = 0

    def send(self, text: str, user: int = USER):
        return self._process(message_update(self._next(), user, text))

    def tap(self, data: str, user: int = USER):
        return self._process(callback_update(self._next(), user, data))

    def _next(self) -> int:
        self.n += 1
        return self.n

    def _process(self, data: dict):
        self.api.clear()
        self.loop.run_until_complete(self.app.process_update(Update.de_json(data, self.app.bot)))
        return self.api

    def close(self):
        self.loop.run_until_complete(self.app.shutdown())
        self.loop.close()


@pytest.fixture
def chat(telegram):
    c = Chat(telegram)
    yield c
    c.close()


def cid(key: str) -> int:
    return db.category_by_key(key).id


def onboard(chat: Chat, user: int = USER, topics=("cybersecurity", "sports")):
    chat.send("/start", user)
    chat.tap("lang:en", user)
    for t in topics:
        chat.tap(f"tp:o:{cid(t)}", user)
    chat.tap("tps:o", user)
    chat.tap("srx:o", user)  # sources step: keep them all
    chat.tap("ob:instant", user)


def test_texts_define_the_same_names():
    names = lambda m: {n for n in dir(m) if not n.startswith("_") and n != "config"}  # noqa: E731
    assert names(texts_en) == names(texts_am)
    assert texts.get(None) is texts_en and texts.get("am") is texts_am


def test_new_user_onboarding(chat):
    api = chat.send("/start")
    first = api.last("sendMessage")
    assert "Choose your language" in first["text"]
    assert [b["callback_data"] for b in first["reply_markup"]["inline_keyboard"][0]] == ["lang:am", "lang:en"]

    api = chat.tap("lang:en")
    assert "setMyCommands" in api.methods()
    picker = api.last("sendMessage")
    assert "Choose your topics" in picker["text"] and "Welcome to" in picker["text"]

    # Save with nothing selected is refused in onboarding.
    api = chat.tap("tps:o")
    assert texts_en.TOPIC_NEED_ONE in api.last("answerCallbackQuery")["text"]

    api = chat.tap(f"tp:o:{cid('cybersecurity')}")
    assert api.last("answerCallbackQuery")["text"] == "✅ 🔐 Cybersecurity added"  # a pop-up for each tap
    assert set(db.interests(USER)) == {cid("cybersecurity")}  # saved straight away
    api = chat.tap(f"tp:o:{cid('sports')}")
    buttons = [b["text"] for row in api.last("editMessageText")["reply_markup"]["inline_keyboard"] for b in row]
    assert "✅ 🔐 Cybersecurity" in buttons and "✅ ⚽ Sports" in buttons and "⬜ 🏛 Politics" in buttons
    assert "Selected: 2" in api.last("editMessageText")["text"]

    api = chat.tap("tps:o")
    step = api.last("editMessageText")
    assert "Choose your news sources" in step["text"]
    labels = [b["text"] for row in step["reply_markup"]["inline_keyboard"] for b in row]
    assert any(t.startswith("✅ BBC Sport") for t in labels)          # covers Sports, on by default
    assert any(t.startswith("✅ The Hacker News") for t in labels)    # covers Cybersecurity
    assert not any("World Health" in t for t in labels)               # Health wasn't chosen
    assert texts_en.NEXT in labels
    assert any(t.startswith(texts_en.SOURCES_SKIP) for t in labels)

    api = chat.tap("srs:o")  # ➡️ Next with the defaults
    assert "How should news reach you" in api.last("editMessageText")["text"]
    assert db.source_exclusions(USER) == set()
    assert not db.get_user(USER).onboarding_completed

    api = chat.tap("ob:instant")
    done = api.last("sendMessage")
    assert "You're all set" in done["text"] and "Cybersecurity" in done["text"]
    assert done["reply_markup"]["keyboard"][0][0]["text"] == texts_en.BTN_PULL
    assert done["reply_markup"]["keyboard"][1][0]["text"] == texts_en.BTN_TOPICS
    user = db.get_user(USER)
    assert user.onboarding_completed and user.delivery_mode == "instant" and user.language == "en"
    assert set(db.interests(USER)) == {cid("cybersecurity"), cid("sports")}

    # Returning user: no onboarding again, preferences kept.
    api = chat.send("/start")
    assert "Welcome back" in api.last("sendMessage")["text"]
    assert set(db.interests(USER)) == {cid("cybersecurity"), cid("sports")}


def test_skip_onboarding_explains_how_to_choose_later(chat):
    chat.send("/start")
    chat.tap("lang:am")
    api = chat.tap("tpx:o")
    assert texts_am.ONBOARDING_SKIPPED in api.last("editMessageText")["text"]
    assert db.get_user(USER).onboarding_completed and db.interests(USER) == {}


def test_callback_data_is_validated(chat):
    onboard(chat)
    chat.send("/interests")
    api = chat.tap("tp:i:99999")
    assert texts_en.CATEGORY_GONE in api.last("answerCallbackQuery")["text"]
    db.set_category_active(cid("politics"), False)  # a topic turned off by an admin can't be picked
    chat.tap(f"tp:i:{cid('politics')}")
    chat.tap("tps:i")
    assert cid("politics") not in db.interests(USER)
    api = chat.tap("lang:xx")  # doesn't match the handler pattern: ignored
    assert db.get_user(USER).language == "en"


def test_update_interests_without_onboarding_again(chat):
    onboard(chat)
    api = chat.send(texts_en.BTN_TOPICS)
    picker = api.last("sendMessage")
    assert "Your topics" in picker["text"] and "Welcome" not in picker["text"]
    assert [b["text"] for b in picker["reply_markup"]["inline_keyboard"][-1]] == [texts_en.DONE]
    api = chat.tap(f"tp:i:{cid('cybersecurity')}")
    assert api.last("answerCallbackQuery")["text"] == "⬜ 🔐 Cybersecurity removed"
    api = chat.tap(f"tp:i:{cid('ethiopia')}")
    assert api.last("answerCallbackQuery")["text"] == "✅ 🇪🇹 Ethiopia added"
    assert "Selected: 2" in api.last("editMessageText")["text"]  # the count follows every tap
    assert set(db.interests(USER)) == {cid("sports"), cid("ethiopia")}  # saved before Done
    api = chat.tap("tps:i")
    assert "Topics saved" in api.last("editMessageText")["text"]
    # Select all / Clear apply at once too.
    assert chat.tap("tpa:i").last("answerCallbackQuery")["text"] == texts_en.TOPICS_ALL_ON
    assert len(db.interests(USER)) == len(db.categories())
    assert chat.tap("tpn:i").last("answerCallbackQuery")["text"] == texts_en.TOPICS_CLEARED
    assert db.interests(USER) == {}
    # An older message's ✖️ Cancel button works like Done.
    assert "No topics selected" in chat.tap("tpx:i").last("editMessageText")["text"]


def test_pause_and_resume(chat):
    onboard(chat)
    api = chat.send("/pause")
    assert not db.get_user(USER).notifications_enabled
    assert api.last("sendMessage")["reply_markup"]["keyboard"][2][0]["text"] == texts_en.BTN_RESUME
    api = chat.send(texts_en.BTN_RESUME)
    assert db.get_user(USER).notifications_enabled
    assert api.last("sendMessage")["reply_markup"]["keyboard"][2][0]["text"] == texts_en.BTN_PAUSE


def test_settings_digest_limit_language_mute(chat):
    onboard(chat)
    api = chat.send("/settings")
    assert "Daily limit" in api.last("sendMessage")["text"]
    chat.tap("st:limit:10")
    assert db.get_user(USER).max_per_day == 10
    chat.tap("st:limit:7")  # not one of the choices
    assert db.get_user(USER).max_per_day == 10
    chat.tap("st:nl:am")
    assert db.get_user(USER).news_language == "am"
    chat.tap("st:mode:digest")
    assert db.get_user(USER).delivery_mode == "digest"
    chat.tap("dg:h:20")
    assert db.get_user(USER).digest_hour == 20
    chat.tap("dg:instant")
    assert db.get_user(USER).delivery_mode == "instant"
    chat.tap(f"st:mute:{cid('sports')}")
    assert db.interests(USER)[cid("sports")] is False
    chat.tap(f"st:mute:{cid('sports')}")
    assert db.interests(USER)[cid("sports")] is True
    api = chat.tap(f"mua:{cid('sports')}")  # 🔕 under an alert
    assert "muted" in api.last("answerCallbackQuery")["text"]
    assert db.interests(USER)[cid("sports")] is False


def test_there_is_no_delete_my_data(chat):
    onboard(chat)
    labels = " ".join(" ".join(row) for row in _rows(chat.send("/settings").last("sendMessage")))
    assert "Delete" not in labels
    api = chat.tap("st:delok")  # an old button still in someone's chat just shows Settings
    assert db.get_user(USER) is not None and len(db.interests(USER)) == 2
    assert "Daily limit" in api.last("editMessageText")["text"]


def test_sources_and_help(chat):
    onboard(chat)
    message = chat.send("/sources").last("sendMessage")
    labels = " ".join(b["text"] for row in message["reply_markup"]["inline_keyboard"] for b in row)
    assert "News sources" in message["text"]
    assert "BBC News" in labels and "The Hacker News" in labels and "Addis Standard" not in labels  # disabled
    assert "/interests" in chat.send("/help").last("sendMessage")["text"]


def test_support_contact_is_clickable(chat):
    onboard(chat)
    from bot import main_keyboard
    rows = [[b.text for b in row] for row in main_keyboard(db.get_user(USER)).keyboard]
    assert ["❓ Help", "💬 Contact us"] in rows
    for message in (chat.send("/help").last("sendMessage"), chat.send("/support").last("sendMessage"),
                    chat.send("💬 Contact us").last("sendMessage")):
        assert '<a href="https://t.me/alazarbeyene">@alazarbeyene</a>' in message["text"]
        button = message["reply_markup"]["inline_keyboard"][0][0]
        assert button["url"] == "https://t.me/alazarbeyene" and "support" in button["text"].lower()


def test_admin_tools_are_admin_only(chat):
    onboard(chat)
    assert texts_en.ONLY_ADMINS in chat.send("/admin").last("sendMessage")["text"]
    provider = next(p for p in db.providers() if p.key == "krebs")
    chat.tap(f"ad:pt:{provider.id}:0")  # a normal user's tap does nothing
    assert not db.get_provider(provider.id).is_enabled

    onboard(chat, SUPER)
    keyboard = chat.api.last("sendMessage")["reply_markup"]["keyboard"]
    assert texts_en.BTN_ADMIN in [b["text"] for row in keyboard for b in row]
    api = chat.send("/admin", SUPER)
    assert "Users:" in api.last("sendMessage")["text"]
    chat.tap(f"ad:pt:{provider.id}:0", SUPER)
    assert db.get_provider(provider.id).is_enabled
    chat.tap(f"ad:ct:{cid('entertainment')}", SUPER)
    assert not db.get_category(cid("entertainment")).is_active
    api = chat.tap("ad:fail", SUPER)
    assert "Failed alerts" in api.last("editMessageText")["text"]


def test_blocking_the_bot_is_recorded(chat):
    onboard(chat)
    member = {"update_id": 500, "my_chat_member": {
        "chat": {"id": USER, "type": "private"}, "from": {"id": USER, "is_bot": False, "first_name": "A"},
        "date": 0,
        "old_chat_member": {"status": "member", "user": {"id": 1, "is_bot": True, "first_name": "Bot"}},
        "new_chat_member": {"status": "kicked", "until_date": 0, "user": {"id": 1, "is_bot": True, "first_name": "Bot"}}}}
    chat._process(member)
    assert db.get_user(USER).status == "blocked"
    chat.send("/start")  # writing to the bot again means they unblocked it
    assert db.get_user(USER).status == "active"


# ---------- 📥 Latest news ----------
def _stories(provider_key: str, prefix: str, titles: list[str], minutes_ago: int = 5):
    from datetime import timedelta

    from collector import Collector
    from db import now_utc
    from news import RawArticle
    provider = next(p for p in db.providers() if p.key == provider_key)
    items = [RawArticle(f"{prefix}-{i}", t, "", f"https://example.com/{prefix}/{i}",
                        published_at=now_utc() - timedelta(minutes=minutes_ago + i)) for i, t in enumerate(titles)]
    return Collector().process(db.get_provider(provider.id), items, now_utc())


def _alerts(api) -> list[str]:
    return [p.get("text") or p.get("caption") for m, p in api.calls if m in ("sendMessage", "sendPhoto")
            and "Read full article" in str(p.get("reply_markup"))]


def _statuses() -> dict[str, str]:
    return {r["title"]: r["status"] for r in db.execute(
        "SELECT a.title, n.status FROM notifications n JOIN articles a ON a.id = n.article_id WHERE n.user_id = %s",
        (USER,))}


SPORTS = ["Arsenal win the north London derby", "Marathon record falls in Berlin race",
          "Tennis star reaches Wimbledon final", "NBA finals open with overtime thriller",
          "Rugby World Cup squad named by coach", "Cricket test ends in dramatic draw",
          "Formula 1 title race goes to final round"]


def test_pull_latest_news_pages_and_never_repeats(chat):
    onboard(chat, topics=("sports",))
    _stories("bbc_sport", "s", SPORTS)  # first fetch of the feed: stored as backlog, not alerted
    _stories("thehackernews", "c", ["Ransomware gang hits hospital network"])  # not their topic
    assert _statuses() == {}

    api = chat.send(texts_en.BTN_PULL)
    shown = _alerts(api)
    assert len(shown) == 5 and SPORTS[0] in shown[0]  # newest first
    assert not any("Ransomware" in t for t in shown)
    summary = api.last("sendMessage")
    assert "2 more waiting" in summary["text"]
    assert summary["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "pull:more"

    api = chat.tap("pull:more")
    assert len(_alerts(api)) == 2 and "That's everything" in api.last("sendMessage")["text"]
    assert set(_statuses().values()) == {"pulled"} and len(_statuses()) == 7

    api = chat.send("/latest")  # nothing new: nothing repeated
    assert _alerts(api) == [] and "up to date" in api.last("sendMessage")["text"]


def test_pull_skips_stories_already_received_and_takes_over_waiting_ones(chat):
    from datetime import timedelta

    from db import now_utc
    onboard(chat, topics=("sports",))
    sport = next(p for p in db.providers() if p.key == "bbc_sport")
    db.record_fetch(sport.id, ok=True, error=None, next_fetch_at=now_utc())  # not a first fetch: alerts queue
    _stories("bbc_sport", "s", SPORTS[:2])
    first, second = SPORTS[0], SPORTS[1]
    assert _statuses() == {first: "queued", second: "queued"}
    db.execute("UPDATE notifications n SET status = 'sent' FROM articles a WHERE a.id = n.article_id AND a.title = %s",
               (first,))

    # An alert due right now is left to the sender (which is about to send it), not pulled.
    assert _alerts(chat.send(texts_en.BTN_PULL)) == []
    # An alert waiting for a later retry is taken over by the pull.
    db.execute("UPDATE notifications SET next_attempt_at = %s WHERE status = 'queued'",
               ((now_utc() + timedelta(minutes=5)).isoformat(),))
    api = chat.send(texts_en.BTN_PULL)
    shown = _alerts(api)
    assert len(shown) == 1 and second in shown[0]  # the one already sent isn't repeated
    assert _statuses() == {first: "sent", second: "pulled"}
    assert db.execute("SELECT COUNT(*) AS n FROM notifications WHERE status = 'queued'").fetchone()["n"] == 0


def test_pull_only_today_and_only_followed_unmuted_topics(chat):
    onboard(chat, topics=("sports", "cybersecurity"))
    _stories("bbc_sport", "old", ["Old match report from two days ago"], minutes_ago=60 * 48)
    _stories("thehackernews", "c", ["Ransomware gang hits hospital network"])
    db.set_interest_enabled(USER, cid("cybersecurity"), False)  # muted
    api = chat.send(texts_en.BTN_PULL)
    assert _alerts(api) == [] and "up to date" in api.last("sendMessage")["text"]
    db.set_interest_enabled(USER, cid("cybersecurity"), True)
    assert len(_alerts(chat.send(texts_en.BTN_PULL))) == 1


def test_pull_works_while_paused_and_needs_topics(chat):
    onboard(chat, topics=("sports",))
    _stories("bbc_sport", "s", SPORTS[:1])
    chat.send("/pause")
    assert len(_alerts(chat.send(texts_en.BTN_PULL))) == 1
    db.set_interests(USER, set())
    assert "Choose your topics first" in chat.send("/latest").last("sendMessage")["text"]


def test_failed_pull_can_be_tried_again(chat):
    from telegram.error import BadRequest
    onboard(chat, topics=("sports",))
    _stories("bbc_sport", "s", SPORTS[:1])
    chat.api.fail.append(("sendMessage", BadRequest("Chat not found")))
    chat.send(texts_en.BTN_PULL)
    assert _statuses() == {SPORTS[0]: "failed"}
    assert len(_alerts(chat.send(texts_en.BTN_PULL))) == 1
    assert _statuses() == {SPORTS[0]: "pulled"}


def _rows(message) -> list[list[str]]:
    return [[b["text"] for b in row] for row in message["reply_markup"]["inline_keyboard"]]


def test_menus_are_checkbox_grids(chat):
    """Buttons sit 2-3 to a row when their text fits, and every row fits the screen width."""
    from keyboards import MAX_PER_ROW, fits
    onboard(chat, SUPER)
    for language in ("en", "am"):
        db.update_user(SUPER, language=language)
        screens = {"topics": chat.send("/interests", SUPER).last("sendMessage"),
                   "settings": chat.send("/settings", SUPER).last("sendMessage"),
                   "digest": chat.send("/digest", SUPER).last("sendMessage"),
                   "sources": chat.send("/sources", SUPER).last("sendMessage"),
                   "admin": chat.send("/admin", SUPER).last("sendMessage")}
        for name, message in screens.items():
            rows = _rows(message)
            assert all(len(row) <= MAX_PER_ROW and fits(row) for row in rows), (language, name, rows)
            assert not any("⠀" in label for row in rows for label in row)  # no invisible padding
        topic_rows = [[b["text"] for b in row] for row in screens["topics"]["reply_markup"]["inline_keyboard"]
                      if row[0]["callback_data"].startswith("tp:")]
        assert sum(len(row) for row in topic_rows) == len(db.categories()) + 1  # + 🛡 CVE reports
        assert sum(1 for row in topic_rows if len(row) >= 2) >= 4  # mostly 2-3 per row
        assert any(len(row) == 3 for row in _rows(screens["digest"]))  # short labels: 3 per row


def test_grid_layout_rules():
    from telegram import InlineKeyboardButton

    from keyboards import grid
    b = lambda text: InlineKeyboardButton(text, callback_data="x")  # noqa: E731
    rows = grid([b("⬜ 06:00"), b("⬜ 07:00"), b("⬜ 08:00"), b("⬜ 09:00")])
    assert [len(r) for r in rows] == [3, 1]  # short: three to a row
    rows = grid([b("✅ Cybersecurity"), b("⬜ Technology"), b("⬜ Sports")])
    assert [len(r) for r in rows] == [2, 1]  # medium: two to a row
    long = b("⬜ A label much too long to share a row with anything")
    assert [[x.text for x in r] for r in grid([b("⬜ One"), long, b("⬜ Two")], wide_last=True)][-1] == [long.text]


# ---------- 📚 Sources ----------
def test_sources_can_be_switched_off(chat):
    onboard(chat, topics=("sports", "cybersecurity"))
    chat.send("/sources")
    bbc_sport = min(p.id for p in db.providers() if p.name == "BBC Sport")
    api = chat.tap(f"sr:s:{bbc_sport}")
    assert api.last("answerCallbackQuery")["text"] == "⬜ BBC Sport off"
    assert bbc_sport in db.source_exclusions(USER)  # saved straight away
    api = chat.tap("srs:s")  # ✅ Done
    assert "Sources saved" in api.last("editMessageText")["text"]

    # Alerts and 📥 Latest news both leave out BBC Sport now.
    _stories("bbc_sport", "s", SPORTS[:2])
    _stories("thehackernews", "c", ["Ransomware gang hits hospital network"])
    shown = _alerts(chat.send(texts_en.BTN_PULL))
    assert len(shown) == 1 and "Ransomware" in shown[0]

    # The last source left on can't be turned off.
    chat.send("/sources")
    groups = {}
    for p in db.providers(enabled_only=True):
        if p.adapter_key not in ("nvd", "kev"):  # CVE providers are managed on the CVE screen
            groups.setdefault(p.name, set()).add(p.id)
    refused = None
    for name, ids in sorted(groups.items()):
        if ids <= db.source_exclusions(USER):
            continue  # already off (BBC Sport)
        api = chat.tap(f"sr:s:{min(ids)}")
        if api.last("answerCallbackQuery").get("show_alert"):
            refused = name
            break
    assert refused is not None and texts_en.SOURCES_NEED_ONE in api.last("answerCallbackQuery")["text"]
    on = [n for n, ids in groups.items() if not ids <= db.source_exclusions(USER)]
    assert on == [refused]
    assert chat.tap("sra:s").last("answerCallbackQuery")["text"] == texts_en.SOURCES_ALL_ON
    assert db.source_exclusions(USER) == set()


def test_onboarding_source_choice_and_skip(chat):
    chat.send("/start")
    chat.tap("lang:en")
    chat.tap(f"tp:o:{cid('cybersecurity')}")
    chat.tap("tps:o")
    hacker_news = next(p.id for p in db.providers() if p.key == "thehackernews")
    chat.tap(f"sr:o:{hacker_news}")
    api = chat.tap("srs:o")
    assert "How should news reach you" in api.last("editMessageText")["text"]
    assert db.source_exclusions(USER) == {hacker_news}
    chat.tap("ob:instant")
    assert db.get_user(USER).onboarding_completed
    # Skip in onboarding means every source on.
    db.update_user(USER, onboarding_completed=False)
    chat.send("/start")
    chat.tap("tps:o")
    chat.tap("srx:o")
    assert db.source_exclusions(USER) == set()


def test_latest_news_checks_the_sources_first(chat):
    """📥 Latest news fetches the user's sources that weren't checked in the last minute, then shows
    what it found, so the newest stories come straight away."""
    from collector import Collector, FetchResult
    from db import now_utc
    from news import RawArticle
    fetched = []

    class Feed:
        async def fetch(self, provider):
            fetched.append(provider.key)
            if provider.key != "bbc_sport":
                return FetchResult()
            return FetchResult(items=[RawArticle("brand-new", "Champions League final goes to penalties", "",
                                                 "https://example.com/new", published_at=now_utc())])

    onboard(chat, topics=("sports",))
    chat.app.bot_data["collector"] = Collector(adapters={"rss": Feed()})
    api = chat.send(texts_en.BTN_PULL)
    assert "sendChatAction" in api.methods()
    assert "bbc_sport" in fetched and "thehackernews" not in fetched  # only sources for their topics
    shown = _alerts(api)
    assert len(shown) == 1 and "Champions League final" in shown[0]
    fetched.clear()
    chat.send(texts_en.BTN_PULL)
    assert fetched == []  # checked less than a minute ago: not fetched again


# ---------- onboarding auto-save and today's news ----------
def _idle(user_id: int, minutes: int):
    from datetime import timedelta

    from db import now_utc
    db.execute("UPDATE users SET last_seen_at = %s WHERE telegram_id = %s",
               ((now_utc() - timedelta(minutes=minutes)).isoformat(), user_id))


def test_finishing_onboarding_sends_todays_news(chat):
    _stories("bbc_sport", "s", SPORTS[:3])  # found earlier today (first fetch: no alerts)
    chat.send("/start")
    chat.tap("lang:en")
    chat.tap(f"tp:o:{cid('sports')}")
    chat.tap("tps:o")
    chat.tap("srx:o")
    api = chat.tap("ob:instant")
    texts_sent = [p.get("text") or "" for m, p in api.calls if m == "sendMessage"]
    assert any("You're all set" in t for t in texts_sent)
    assert any(texts_en.WELCOME_NEWS_INTRO in t for t in texts_sent)
    assert len(_alerts(api)) == 3 and set(_statuses().values()) == {"pulled"}
    assert db.get_user(USER).welcome_news_at is not None
    # Only once: tapping an old delivery button again sends no second batch.
    _stories("bbc_sport", "t", ["Late goal seals derby win at the death"])
    assert _alerts(chat.tap("ob:instant")) == []


def test_onboarding_choices_auto_save_after_ten_minutes(chat):
    from bot import OnboardingAutosave
    _stories("bbc_sport", "s", SPORTS[:2])
    chat.send("/start")
    chat.tap("lang:en")
    chat.tap(f"tp:o:{cid('sports')}")  # picks a topic, then walks away without ➡️ Next
    chat.send("/start", user=200)
    chat.tap("lang:en", user=200)      # picked nothing: stays in onboarding
    autosave = OnboardingAutosave(chat.app.bot)

    _idle(USER, 5)
    assert chat.loop.run_until_complete(autosave.run_once()) == 0  # not 10 minutes yet
    assert not db.get_user(USER).onboarding_completed

    _idle(USER, 11)
    _idle(200, 30)
    chat.api.clear()
    assert chat.loop.run_until_complete(autosave.run_once()) == 1
    user = db.get_user(USER)
    assert user.onboarding_completed and user.delivery_mode == "instant"
    assert set(db.interests(USER)) == {cid("sports")}
    saved = chat.api.calls[0][1]
    assert "Your choices are saved" in saved["text"] and "Sports" in saved["text"]
    assert saved["reply_markup"]["keyboard"][0][0]["text"] == texts_en.BTN_PULL  # the main keyboard
    assert len(_alerts(chat.api)) == 2  # today's news they hadn't received
    assert not db.get_user(200).onboarding_completed
    chat.api.clear()
    assert chat.loop.run_until_complete(autosave.run_once()) == 0 and _alerts(chat.api) == []  # once
