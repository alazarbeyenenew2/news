"""CVE reports: parsing NVD and CISA KEV, matching users' technologies and severity, alerts, CSV."""
import asyncio
import csv
import io
import json
from datetime import date, datetime, timedelta, timezone

import pytest
from telegram import Bot

import config
import cves
import texts_en
from collector import Collector
from db import db, now_utc
from delivery import Sender
from news import FetchResult, cve_term_matches
from tests.conftest import FIXTURES
from tests.test_bot import Chat

NVD = json.loads((FIXTURES / "nvd_sample.json").read_text())
KEV = json.loads((FIXTURES / "kev_sample.json").read_text())


def nvd_items():
    """The fixture's CVEs, published "now" so they count as new."""
    items = [cves.parse_nvd_cve(v["cve"]) for v in NVD["vulnerabilities"]]
    items = [i for i in items if i]
    for i in items:
        i.published_at = now_utc() - timedelta(minutes=5)
    return items


def provider(key: str):
    p = next(p for p in db.providers() if p.key == key)
    db.record_fetch(p.id, ok=True, error=None, next_fetch_at=now_utc())  # not a first fetch: alerts go out
    return db.get_provider(p.id)


def make_user(telegram_id: int, terms=(), techs=(), severity="HIGH", all_tech=False, on=True, topics=()):
    db.upsert_user(telegram_id, telegram_id, None, f"U{telegram_id}")
    db.update_user(telegram_id, language="en", onboarding_completed=True, cve_alerts=on,
                   cve_enabled_at=(now_utc() - timedelta(hours=1)).isoformat(), cve_min_severity=severity,
                   cve_all_technologies=all_tech)
    for term in terms:
        db.add_user_technology(telegram_id, term)
    for key in techs:
        tech = next(t for t in db.technologies() if t.key == key)
        db.add_user_technology(telegram_id, f"tech:{key}", tech.id)
    if topics:
        db.set_interests(telegram_id, {db.category_by_key(k).id for k in topics})
    # Terms count from before the CVEs are found.
    db.execute("UPDATE user_technologies SET created_at = %s WHERE user_id = %s",
               ((now_utc() - timedelta(hours=1)).isoformat(), telegram_id))


def alerts(user_id: int) -> dict[str, tuple[str, str]]:
    rows = db.execute("""SELECT c.cve_id, n.status, n.matched FROM notifications n
                         JOIN cves c ON c.article_id = n.article_id WHERE n.user_id = %s""", (user_id,))
    return {r["cve_id"]: (r["status"], r["matched"]) for r in rows}


# ---------- parsing ----------
def test_parse_nvd():
    items = {i.external_id: i for i in nvd_items()}
    assert "CVE-2025-49999" not in items  # rejected
    nginx = items["CVE-2026-50001"].extra["cve"]
    assert (nginx["severity"], nginx["cvss_score"], nginx["cvss_version"]) == ("CRITICAL", 9.8, "3.1")
    windows = items["CVE-2026-50002"].extra["cve"]
    assert windows["severity"] == "MEDIUM" and windows["cvss_score"] == 5.5  # NVD's own (Primary) score wins
    assert windows["products"] == ["microsoft windows 11 24h2"]
    assert items["CVE-2026-50003"].extra["cve"]["severity"] is None  # not rated yet
    assert items["CVE-2026-50004"].extra["cve"]["cvss_version"] == "4.0"
    assert items["CVE-2026-50001"].url == "https://nvd.nist.gov/vuln/detail/CVE-2026-50001"


def test_parse_kev_recent_only():
    items = cves.parse_kev(KEV, date(2026, 11, 28))
    assert [i.external_id for i in items] == ["kev:CVE-2026-50002"]
    cve = items[0].extra["cve"]
    assert cve["kev"] and cve["kev_ransomware"] and cve["kev_date"] == "2026-11-27"


@pytest.mark.parametrize("text,aliases,hit", [
    ("NGINX Plus before 1.29 allows…", ["nginx"], True),
    ("the nginxy-admin plugin", ["nginx"], False),
    ("ASP.NET Core 9 denial of service", [".net core", "asp.net"], True),
    ("Fortinet FortiOS SSL-VPN", ["fortios"], True),
    ("Microsoft Windows Server 2022", ["windows server 2022"], True),
    ("Biosphere chips", ["ios"], False),
])
def test_term_matching(text, aliases, hit):
    assert cve_term_matches(text, aliases) is hit


# ---------- matching ----------
def collect(items, key="nvd"):
    return Collector().process(provider(key), items, now_utc())


def test_cve_matching_rules():
    make_user(1, techs=["nginx"])                                # High and above
    make_user(2, techs=["windows"], severity="MEDIUM")
    make_user(3, all_tech=True, severity="CRITICAL")
    make_user(4, techs=["nginx"], on=False)                      # CVE reports off
    make_user(5, techs=["moodle"], severity="HIGH")              # Moodle CVE isn't rated yet
    make_user(6, terms=["moodle"], severity="ALL")               # typed in; All severities
    make_user(7, topics=["cybersecurity"])                       # follows the topic, no CVE reports
    counts = collect(nvd_items())
    assert counts["new"] == 4 and counts["queued"] == 4
    assert alerts(1) == {"CVE-2026-50001": ("queued", "nginx")}          # not the 'nginxy' one
    assert alerts(2) == {"CVE-2026-50002": ("queued", "Windows")}
    assert alerts(3) == {"CVE-2026-50001": ("queued", "*")}              # all technologies, critical only
    assert alerts(4) == {} and alerts(5) == {} and alerts(7) == {}
    assert alerts(6) == {"CVE-2026-50003": ("queued", "moodle")}
    # CVEs never enter topics, so Cybersecurity followers aren't flooded.
    assert db._one("SELECT COUNT(*) FROM article_categories") == 0


def test_actively_exploited_is_always_sent_and_merged():
    make_user(1, techs=["windows"], severity="CRITICAL")  # the Windows CVE is only Medium…
    collect(nvd_items())
    assert alerts(1) == {}
    kev_items = cves.parse_kev(KEV, now_utc().date())
    for i in kev_items:
        i.extra["cve"]["kev_date"] = now_utc().date().isoformat()
    counts = collect(kev_items, "cisa_kev")                # …until CISA lists it as exploited
    assert counts == {"new": 0, "duplicate": 1, "invalid": 0, "queued": 1}
    assert alerts(1) == {"CVE-2026-50002": ("queued", "Windows")}
    article = db.article_by_url("https://nvd.nist.gov/vuln/detail/CVE-2026-50002")
    cve = db.get_cve(article.id)
    assert cve.kev and cve.kev_ransomware and cve.severity == "MEDIUM"  # one CVE, details merged
    assert db._one("SELECT COUNT(*) FROM cves") == 4


def test_technologies_added_later_only_get_later_cves():
    make_user(1)
    collect(nvd_items())
    db.add_user_technology(1, "nginx")  # added after the CVE was found
    article = db.article_by_url("https://nvd.nist.gov/vuln/detail/CVE-2026-50001")
    assert Collector().match(article.id) == 0


# ---------- delivery ----------
def test_cve_alert_message(telegram):
    make_user(1, techs=["nginx"])
    collect(nvd_items())
    bot = Bot(config.BOT_TOKEN, request=telegram, get_updates_request=telegram)
    asyncio.run(bot.initialize())
    sender = Sender(bot)

    async def send():
        return [await sender.send_one(n) for n in db.due_notifications()]
    assert asyncio.run(send()) == ["sent"]
    message = telegram.last("sendMessage")
    text = message["text"]
    assert "🛡 <b>CVE report</b>" in text and "🔴 <b>Critical 9.8</b>" in text
    assert "<b>CVE-2026-50001</b>" in text and "🧩 Matches: nginx" in text
    buttons = [b for row in message["reply_markup"]["inline_keyboard"] for b in row]
    assert buttons[0]["url"] == "https://nvd.nist.gov/vuln/detail/CVE-2026-50001"
    assert buttons[1]["callback_data"] == "cv:menu"


def test_nvd_adapter(monkeypatch):
    async def fake_get(url, **kwargs):
        assert "pubStartDate=" in url and "pubEndDate=" in url
        return type("R", (), {"status_code": 200, "content": json.dumps(NVD).encode(), "headers": {},
                              "url": url})()
    monkeypatch.setattr(cves, "safe_get", fake_get)
    nvd = next(p for p in db.providers() if p.key == "nvd")
    result = asyncio.run(cves.NvdAdapter().fetch(nvd))
    assert [i.external_id for i in result.items] == ["CVE-2026-50001", "CVE-2026-50002", "CVE-2026-50003",
                                                     "CVE-2026-50004"]


# ---------- bot ----------
@pytest.fixture
def chat(telegram):
    c = Chat(telegram)
    c.send("/start")
    c.tap("lang:en")
    c.tap(f"tp:o:{db.category_by_key('sports').id}")
    c.tap("tps:o")
    c.tap("srx:o")
    c.tap("ob:instant")
    yield c
    c.close()


def _buttons(message):
    return [b for row in message["reply_markup"]["inline_keyboard"] for b in row]


def test_cve_screen_and_picker(chat):
    screen = chat.send(texts_en.BTN_CVE).last("sendMessage")
    assert "CVE reports" in screen["text"] and "none yet" in screen["text"]
    page = chat.tap("cv:p:0").last("editMessageText")
    assert "Operating systems" in page["text"]
    windows = next(t for t in db.technologies() if t.key == "windows")
    api = chat.tap(f"cv:t:0:{windows.id}")
    assert api.last("answerCallbackQuery")["text"] == "✅ Windows added"
    user = db.get_user(100)
    assert user.cve_alerts and [t["name"] for t in db.user_technologies(100)] == ["Windows"]  # turned on
    assert any(b["text"] == "✅ Windows" for b in _buttons(api.last("editMessageText")))
    assert chat.tap(f"cv:t:0:{windows.id}").last("answerCallbackQuery")["text"] == "⬜ Windows removed"
    last = chat.tap("cv:p:99").last("editMessageText")  # clamps to the last page: your own
    assert "Your own" in last["text"]


def test_add_your_own_technologies(chat):
    chat.send("/cve")
    chat.tap("cv:add")
    api = chat.send("nginx, Windows Server, my-portal 2.1\nx, ok?")
    reply = api.calls[0][1]["text"]
    assert "Added:</b> nginx, Windows Server, my-portal 2.1" in reply
    assert "x, ok?" in reply  # too short / odd characters
    mine = {t["name"]: t["technology_id"] for t in db.user_technologies(100)}
    assert mine["nginx"] and mine["Windows Server"] and mine["my-portal 2.1"] is None  # catalog vs own
    # Without ✍️ first, free text isn't taken as technologies.
    assert texts_en.UNKNOWN_MESSAGE in chat.send("apache").last("sendMessage")["text"]
    # Own words can be removed from the last picker page.
    pages = len(db.technologies()) and chat.tap("cv:p:99").last("editMessageText")
    own = next(b for b in _buttons(pages) if b["text"] == "✅ my-portal 2.1")
    assert chat.tap(own["callback_data"]).last("answerCallbackQuery")["text"] == "⬜ my-portal 2.1 removed"


def test_severity_all_and_off(chat):
    chat.send("/cve")
    assert chat.tap("cv:sev:CRITICAL").last("answerCallbackQuery")["text"] == "🔴 Critical only"
    assert db.get_user(100).cve_min_severity == "CRITICAL"
    chat.tap("cv:sev:NOPE")  # not a level: just shows the choices
    assert db.get_user(100).cve_min_severity == "CRITICAL"
    chat.tap("cv:all")
    assert db.get_user(100).cve_all_technologies and db.get_user(100).cve_alerts
    chat.tap("cv:off")
    assert not db.get_user(100).cve_alerts


def test_csv_report(chat):
    assert texts_en.CVE_CSV_EMPTY in chat.tap("cv:csv").last("answerCallbackQuery")["text"]
    nginx = next(t for t in db.technologies() if t.key == "nginx")
    chat.tap(f"cv:t:0:{nginx.id}")
    db.execute("UPDATE users SET cve_enabled_at = %s WHERE telegram_id = 100",
               ((now_utc() - timedelta(hours=1)).isoformat(),))
    db.execute("UPDATE user_technologies SET created_at = %s", ((now_utc() - timedelta(hours=1)).isoformat(),))
    collect(nvd_items())
    api = chat.tap("cv:csv")
    doc = api.last("sendDocument")
    file_name, content, _ = next(iter(doc["_files"].values()))
    assert file_name.startswith("cve-report_") and file_name.endswith(".csv")
    assert "1 CVE alert" in doc["caption"]
    rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
    assert rows[0]["cve_id"] == "CVE-2026-50001" and rows[0]["severity"] == "CRITICAL"
    assert rows[0]["matched"] == "nginx" and rows[0]["nvd_link"].endswith("CVE-2026-50001")
