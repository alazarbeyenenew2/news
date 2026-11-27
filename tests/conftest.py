"""Test setup: a throwaway database file and a fake Telegram Bot API.

Environment variables are set before any project module is imported, because config.py and db.py read
them at import time.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="newsbot-test-")
os.environ.update({
    "DATABASE_PATH": os.path.join(_tmp, "test.db"),
    "BOT_TOKEN": "123456:TEST-TOKEN",
    "SUPER_ADMIN_IDS": "999",
    "TIMEZONE": "UTC",
    "ADMIN_PASSWORD": "test-password",
    "SECRET_KEY": "test-secret-key-test-secret-key-0000",
    "MESSAGES_PER_SECOND": "1000",
    "DEFAULT_MAX_PER_DAY": "30",
})
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from telegram.request import BaseRequest, RequestData  # noqa: E402

from db import db, load_seed  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
TABLES = ["notifications", "article_categories", "articles", "user_interests", "users", "providers",
          "categories", "settings"]


@pytest.fixture(autouse=True)
def fresh_db():
    """Every test starts with an empty database plus the seed topics and providers."""
    for table in TABLES:
        db.conn.execute(f"DELETE FROM {table}")
    db.conn.execute("DELETE FROM sqlite_sequence")
    db.conn.commit()
    db.seed(*load_seed())
    yield db


class FakeTelegram(BaseRequest):
    """Answers Bot API calls like Telegram would and records them. Set `fail` to a list of
    (method, exception) to make the next matching call raise."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.fail: list[tuple[str, Exception]] = []
        self._message_id = 100

    @property
    def read_timeout(self):
        return 5

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    def methods(self) -> list[str]:
        return [m for m, _ in self.calls]

    def last(self, method: str) -> dict:
        return [p for m, p in self.calls if m == method][-1]

    def sent_texts(self) -> list[str]:
        return [p.get("text") or p.get("caption") or "" for m, p in self.calls
                if m in ("sendMessage", "sendPhoto", "editMessageText")]

    def clear(self):
        self.calls.clear()

    async def do_request(self, url, method, request_data: RequestData | None = None, read_timeout=None,
                         write_timeout=None, connect_timeout=None, pool_timeout=None):
        name = url.rsplit("/", 1)[-1]
        params = request_data.parameters if request_data else {}
        self.calls.append((name, params))
        for i, (failing, error) in enumerate(self.fail):
            if failing == name:
                self.fail.pop(i)
                raise error
        if name == "getMe":
            result = {"id": 1, "is_bot": True, "first_name": "NewsBot", "username": "news_test_bot",
                      "can_join_groups": False, "can_read_all_group_messages": False,
                      "supports_inline_queries": False}
        elif name in ("sendMessage", "sendPhoto", "editMessageText", "sendDocument"):
            self._message_id += 1
            result = {"message_id": self._message_id, "date": 0,
                      "chat": {"id": int(params.get("chat_id", 0) or 0), "type": "private"},
                      "text": params.get("text") or params.get("caption") or ""}
        else:
            result = True
        return 200, json.dumps({"ok": True, "result": result}).encode()


@pytest.fixture
def telegram():
    return FakeTelegram()


def message_update(update_id: int, user_id: int, text: str, first_name: str = "Abebe") -> dict:
    entities = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}] if text.startswith("/") else []
    return {"update_id": update_id, "message": {
        "message_id": update_id, "date": 0, "chat": {"id": user_id, "type": "private"},
        "from": {"id": user_id, "is_bot": False, "first_name": first_name}, "text": text, "entities": entities}}


def callback_update(update_id: int, user_id: int, data: str, first_name: str = "Abebe") -> dict:
    return {"update_id": update_id, "callback_query": {
        "id": f"cb{update_id}", "chat_instance": "ci", "data": data,
        "from": {"id": user_id, "is_bot": False, "first_name": first_name},
        "message": {"message_id": 50, "date": 0, "chat": {"id": user_id, "type": "private"}, "text": "menu"}}}
