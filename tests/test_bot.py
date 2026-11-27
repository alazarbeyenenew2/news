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

    chat.tap(f"tp:o:{cid('cybersecurity')}")
    api = chat.tap(f"tp:o:{cid('sports')}")
    buttons = [b["text"] for row in api.last("editMessageText")["reply_markup"]["inline_keyboard"] for b in row]
    assert "✅ 🔐 Cybersecurity" in buttons and "✅ ⚽ Sports" in buttons and "⬜ 🏛 Politics" in buttons
    assert "Selected: 2" in api.last("editMessageText")["text"]

    api = chat.tap("tps:o")
    assert "How should news reach you" in api.last("editMessageText")["text"]
    assert not db.get_user(USER).onboarding_completed

    api = chat.tap("ob:instant")
    done = api.last("sendMessage")
    assert "You're all set" in done["text"] and "Cybersecurity" in done["text"]
    assert done["reply_markup"]["keyboard"][0][0]["text"] == texts_en.BTN_TOPICS
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
    assert "Choose your topics" in api.last("sendMessage")["text"] and "Welcome" not in api.last("sendMessage")["text"]
    chat.tap(f"tp:i:{cid('cybersecurity')}")  # remove
    chat.tap(f"tp:i:{cid('ethiopia')}")       # add
    api = chat.tap("tps:i")
    assert "Topics saved" in api.last("editMessageText")["text"]
    assert set(db.interests(USER)) == {cid("sports"), cid("ethiopia")}
    # Cancel leaves everything as it was.
    chat.send("/interests")
    chat.tap("tpn:i")
    chat.tap("tpx:i")
    assert set(db.interests(USER)) == {cid("sports"), cid("ethiopia")}


def test_pause_and_resume(chat):
    onboard(chat)
    api = chat.send("/pause")
    assert not db.get_user(USER).notifications_enabled
    assert api.last("sendMessage")["reply_markup"]["keyboard"][1][0]["text"] == texts_en.BTN_RESUME
    api = chat.send(texts_en.BTN_RESUME)
    assert db.get_user(USER).notifications_enabled
    assert api.last("sendMessage")["reply_markup"]["keyboard"][1][0]["text"] == texts_en.BTN_PAUSE


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


def test_delete_my_data(chat):
    onboard(chat)
    chat.tap("st:del")
    assert db.get_user(USER) is not None
    chat.tap("st:delok")
    assert db.get_user(USER) is None
    assert db._one("SELECT COUNT(*) FROM user_interests") == 0


def test_sources_and_help(chat):
    onboard(chat)
    text = chat.send("/sources").last("sendMessage")["text"]
    assert "BBC News" in text and "The Hacker News" in text and "Addis Standard" not in text  # disabled
    assert "/interests" in chat.send("/help").last("sendMessage")["text"]


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
