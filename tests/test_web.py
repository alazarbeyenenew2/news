"""Web admin page and health endpoints. The app's lifespan (Telegram, workers) isn't started."""
import re

import pytest
from fastapi.testclient import TestClient

from db import db, now_utc
from web import create_web_app


@pytest.fixture
def client():
    return TestClient(create_web_app())


def login(client) -> str:
    r = client.post("/login", data={"username": "admin", "password": "test-password"}, follow_redirects=True)
    assert r.status_code == 200
    return re.search(r'name="csrf" value="([^"]+)"', r.text).group(1)


def test_health_and_metrics(client):
    assert client.get("/health").json() == {"status": "ok"}
    ready = client.get("/ready")
    assert ready.status_code == 503 and ready.json()["checks"]["database"] is True  # no bot running here
    assert "newsbot_notifications_sent_total" in client.get("/metrics").text


def test_login_required_and_wrong_password(client):
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"
    assert client.post("/login", data={"username": "admin", "password": "nope"}).status_code == 401


def test_pages_render(client):
    login(client)
    for path in ("/", "/categories", "/articles", "/articles?low=1", "/notifications", "/users"):
        r = client.get(path)
        assert r.status_code == 200, path
    assert "The Hacker News" in client.get("/").text


def test_csrf_is_checked(client):
    login(client)
    provider = db.providers()[0]
    r = client.post(f"/providers/{provider.id}/toggle", data={"csrf": "wrong"})
    assert r.status_code == 400


def test_provider_and_category_management(client):
    csrf = login(client)
    krebs = next(p for p in db.providers() if p.key == "krebs")
    client.post(f"/providers/{krebs.id}/toggle", data={"csrf": csrf})
    assert db.get_provider(krebs.id).is_enabled
    r = client.post(f"/providers/{krebs.id}/edit", data={"csrf": csrf, "interval": 1200,
                                                         "categories": '{"cybersecurity": 0.7}', "notes": "ok"})
    assert "saved" in r.text
    assert db.get_provider(krebs.id).categories == {"cybersecurity": 0.7}
    r = client.post(f"/providers/{krebs.id}/edit", data={"csrf": csrf, "interval": 1200, "categories": '{"x": 5}'})
    assert "must be a number from 0 to 1" in r.text
    # New providers are added disabled; unsafe feed URLs are refused.
    client.post("/providers", data={"csrf": csrf, "key": "new_feed", "name": "New", "base_url": "https://n.example.com",
                                    "feed_url": "https://n.example.com/rss"})
    assert not next(p for p in db.providers() if p.key == "new_feed").is_enabled
    r = client.post("/providers", data={"csrf": csrf, "key": "evil", "name": "Evil", "base_url": "https://e.example.com",
                                        "feed_url": "http://169.254.169.254/"})
    assert "Not added" in r.text
    cyber = db.category_by_key("cybersecurity")
    client.post(f"/categories/{cyber.id}/keywords", data={"csrf": csrf, "keywords": '{"ransomware": 0.95}'})
    assert db.get_category(cyber.id).keywords == {"ransomware": 0.95}


def test_article_correction(client):
    csrf = login(client)
    provider = db.providers()[0]
    article_id = db.insert_article(provider.id, {
        "external_id": "x", "title": "Something", "normalized_title": "something", "title_hash": "h",
        "canonical_url": "https://e.com/x", "language": "en", "fetched_at": now_utc().isoformat(),
        "content_hash": "c"})
    sports = db.category_by_key("sports").id
    r = client.post(f"/articles/{article_id}/categories", data={"csrf": csrf, "category": [str(sports), "99999"]})
    assert r.status_code == 200
    cats = db.article_categories(article_id)
    assert [(c["category_id"], c["method"]) for c in cats] == [(sports, "manual")]


def test_user_management(client):
    csrf = login(client)
    db.upsert_user(100, 100, "abebe", "Abebe")
    client.post("/users/100/disable", data={"csrf": csrf})
    assert db.get_user(100).status == "disabled"
    client.post("/users/100/enable", data={"csrf": csrf})
    assert db.get_user(100).status == "active"
    db.upsert_user(999, 999, None, "Super")
    r = client.post("/users/999/disable", data={"csrf": csrf})
    assert "be changed here" in r.text and db.get_user(999).status == "active"


def test_admin_can_delete_a_user(client):
    csrf = login(client)
    db.upsert_user(100, 100, "abebe", "Abe'be")
    db.set_interests(100, {db.category_by_key("sports").id})
    db.add_user_technology(100, "nginx")
    html = client.get("/users").text
    assert 'action="/users/100/delete"' in html and "Delete Abebe and all their data" in html  # quote stripped
    r = client.post("/users/100/delete", data={"csrf": csrf}, follow_redirects=True)
    assert "was deleted" in r.text and db.get_user(100) is None
    assert db.interests(100) == {} and db.user_technologies(100) == []
    assert client.post("/users/100/delete", data={"csrf": csrf}).status_code == 404
    db.upsert_user(999, 999, None, "Super")  # super admins can't be deleted
    r = client.post("/users/999/delete", data={"csrf": csrf}, follow_redirects=True)
    assert "be changed here" in r.text and db.get_user(999) is not None
    assert client.post("/users/999/delete", data={"csrf": "wrong"}).status_code == 400


def test_users_page_lists_preferences(client):
    login(client)
    db.upsert_user(100, 100, "abebe", "Abebe")
    db.update_user(100, language="am", onboarding_completed=True, delivery_mode="digest", digest_hour=20,
                   cve_alerts=True, cve_enabled_at=now_utc().isoformat(), cve_min_severity="CRITICAL")
    sports, health = db.category_by_key("sports").id, db.category_by_key("health").id
    db.set_interests(100, {sports, health})
    db.set_interest_enabled(100, health, False)
    db.set_source_exclusions(100, {next(p.id for p in db.providers() if p.key == "espn_soccer")})
    db.add_user_technology(100, "nginx")
    html = client.get("/users").text
    assert "⚽ Sports" in html and "🩺 Health" in html and "line-through" in html  # muted topic struck out
    assert "📰 digest 20:00" in html and "news in" not in html
    assert "1 sources off" in html and "ESPN" in html
    assert "Critical only" in html and "nginx" in html


class FakeFile:
    file_size = 4

    async def download_as_bytearray(self):
        return bytearray(b"\xff\xd8jp")


class FakeBot:
    """getChat/getFile like Telegram: user 100 has a photo and a bio, others fail."""

    def __init__(self):
        self.calls = []

    async def get_chat(self, chat_id):
        from types import SimpleNamespace
        from telegram.error import BadRequest
        self.calls.append(("getChat", chat_id))
        if chat_id != 100:
            raise BadRequest("Chat not found")
        return SimpleNamespace(bio="Coffee & <b>news</b>", first_name="Abebe", last_name="Kebede", username="abebe_k",
                               photo=SimpleNamespace(small_file_id="f1", small_file_unique_id="u1"))

    async def get_file(self, file_id):
        self.calls.append(("getFile", file_id))
        return FakeFile()


def test_users_page_shows_telegram_profiles(client):
    from types import SimpleNamespace
    bot = FakeBot()
    client.app.state.telegram = SimpleNamespace(bot=bot, running=True)
    assert client.get("/users/100/photo").status_code == 401  # admins only
    login(client)
    db.upsert_user(100, 100, "abebe", "Abebe")
    db.upsert_user(200, 200, None, "Sara")
    html = client.get("/users").text
    assert "Abebe Kebede" in html and "@abebe_k" in html and 'href="https://t.me/abebe_k"' in html
    assert "Coffee &amp; &lt;b&gt;news&lt;/b&gt;" in html  # bio is escaped
    assert 'src="/users/100/photo?v=u1"' in html
    assert 'href="tg://user?id=200"' in html and "Telegram profile unavailable" in html
    r = client.get("/users/100/photo")
    assert r.status_code == 200 and r.content == b"\xff\xd8jp" and r.headers["content-type"] == "image/jpeg"
    client.get("/users/100/photo")
    client.get("/users")
    assert bot.calls.count(("getFile", "f1")) == 1 and bot.calls.count(("getChat", 100)) == 1  # cached
    assert client.get("/users/200/photo").status_code == 404
    assert client.get("/users/999/photo").status_code == 404
