"""Normalization, URLs, duplicate detection and categorization (pure functions, no network)."""
from datetime import datetime, timedelta, timezone

import pytest

import config
from db import db
from news import (CategoryRule, RawArticle, RuleClassifier, canonical_url, find_similar, normalize, normalize_title,
                  parse_feed, short_summary, title_hash, titles_similar)
from tests.conftest import FIXTURES

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
MAX_AGE = timedelta(hours=12)


# ---------- feeds ----------
def test_parse_rss_fixture():
    items = parse_feed((FIXTURES / "rss_sample.xml").read_bytes())
    assert len(items) == 5
    first = items[0]
    assert first.title.startswith("Critical vulnerability CVE-2026-1234")
    assert first.external_id == "example-1001"
    assert first.image_url == "https://img.example.com/large.jpg"  # the largest thumbnail
    assert first.published_at == datetime(2026, 11, 27, 10, 30, tzinfo=timezone.utc)
    assert first.language == "en"
    assert "Vulnerability" in first.categories
    assert items[1].image_url is None  # never invented
    assert items[4].image_url == "https://img.example.com/enclosure.png"


def test_parse_atom_fixture():
    items = parse_feed((FIXTURES / "atom_sample.xml").read_bytes())
    assert len(items) == 1
    assert items[0].url == "https://sport.example.org/football/derby-report"
    assert items[0].external_id == "urn:example:sports:42"
    assert items[0].categories == ["Football"]


def test_normalize_valid_and_invalid_items():
    items = parse_feed((FIXTURES / "rss_sample.xml").read_bytes())
    results = [normalize(i, "en", NOW, MAX_AGE) for i in items]
    good, second, bad_link, no_title, enclosure = results
    assert good["canonical_url"] == "https://news.example.com/2026/11/vpn-flaw"  # tracking + fragment gone
    assert good["summary"].startswith("Attackers are actively exploiting")
    assert "appeared first on" not in good["summary"]
    assert "<" not in good["summary"]
    assert good["published_at"] == "2026-11-27T10:30:00+00:00"
    assert good["author"] == "Jane Reporter"
    assert good["is_backlog"] is False
    assert second["summary"] == "Short description."
    assert bad_link is None and no_title is None
    assert enclosure["published_at"] is None  # no date given: none invented


def test_normalize_drops_unsafe_and_future_dates():
    raw = RawArticle("x", "Title here", "", "http://169.254.169.254/latest/meta-data")
    assert normalize(raw, "en", NOW, MAX_AGE) is None
    raw = RawArticle("x", "Title here", "", "https://ok.example.com/a", image_url="file:///etc/passwd",
                     published_at=NOW + timedelta(days=2))
    article = normalize(raw, "en", NOW, MAX_AGE)
    assert article["image_url"] is None
    assert article["published_at"] is None


def test_old_items_are_backlog():
    raw = RawArticle("x", "Old story", "", "https://ok.example.com/old", published_at=NOW - timedelta(days=2))
    assert normalize(raw, "en", NOW, MAX_AGE)["is_backlog"] is True


# ---------- summaries ----------
def test_short_summary_cuts_at_sentences_and_adds_nothing():
    text = "One. Two is here. Three is longer than the others. Four. Five should not appear."
    assert short_summary(text) == "One. Two is here. Three is longer than the others. Four."
    assert short_summary("") == ""
    assert short_summary("Same as title", "Same as title") == ""
    long = "word " * 200
    out = short_summary(long, limit=100)
    assert len(out) <= 100 and out.endswith("…")


# ---------- URLs ----------
@pytest.mark.parametrize("url,expected", [
    ("HTTPS://Example.COM:443/a/b/?utm_source=x&b=2&a=1#frag", "https://example.com/a/b?a=1&b=2"),
    ("http://example.com:80/", "http://example.com/"),
    ("https://example.com/x?fbclid=1&gclid=2&at_medium=rss", "https://example.com/x"),
    ("https://example.com:8443/x", "https://example.com:8443/x"),
])
def test_canonical_url(url, expected):
    assert canonical_url(url) == expected


# ---------- duplicates ----------
def test_titles_similar_matches_near_identical_only():
    a = normalize_title("Critical VPN flaw exploited by hackers, vendor warns - BBC News")
    b = normalize_title("Critical VPN flaw exploited by hackers, vendor warns")
    c = normalize_title("Critical VPN flaw exploited by hackers vendor warns users")
    unrelated = normalize_title("Critical election results announced by electoral board")
    assert a == b
    assert titles_similar(b, c, 0.88)
    assert not titles_similar(b, unrelated, 0.88)
    # Short generic headlines are never merged.
    assert not titles_similar("live updates", "live updates today", 0.88)


def test_find_similar_returns_cluster():
    title = normalize_title("Ethiopia and Kenya sign new trade agreement in Nairobi")
    recent = [(7, title, title_hash(title), 3)]
    assert find_similar(normalize_title("Ethiopia and Kenya sign new trade agreement in Nairobi"), recent, 0.88) == 3
    assert find_similar(normalize_title("Kenya marathon runners win in Berlin"), recent, 0.88) is None


# ---------- categorization ----------
def _rules():
    return [CategoryRule(c.id, c.key, c.keywords) for c in db.categories()]


def _classify(title, summary="", provider=None, tags=None):
    results = RuleClassifier().classify({"title": title, "summary": summary, "tags": tags or []},
                                        provider or {}, _rules())
    ids = {c.id: c.key for c in db.categories()}
    return {ids[cid]: conf for cid, conf, _, _ in results}


def _confident(scores: dict) -> set:
    return {k for k, v in scores.items() if v >= config.CLASSIFY_MIN_CONFIDENCE}


def test_classify_examples_from_the_plan():
    assert "cybersecurity" in _confident(_classify("Critical vulnerability CVE-2026-1234 disclosed in Apache"))
    assert "sports" in _confident(_classify("Arsenal striker scores in Premier League win"))
    assert "politics" in _confident(_classify("Opposition wins parliamentary election in Ghana"))
    assert "war_conflict" in _confident(_classify("Airstrikes hit capital as ceasefire talks stall"))
    assert "ethiopia" in _confident(_classify("Addis Ababa light rail to expand, city says"))


def test_weak_keyword_alone_is_not_enough():
    scores = _classify("Company releases quarterly update", "The new patch fixes small issues.")
    assert "cybersecurity" not in _confident(scores)
    assert 0 < scores.get("cybersecurity", 0) < config.CLASSIFY_MIN_CONFIDENCE


def test_provider_default_and_keywords_combine():
    scores = _classify("ዜና", "በኢትዮጵያ የተካሄደ ስብሰባ", provider={"ethiopia": 0.5})
    assert scores["ethiopia"] >= config.CLASSIFY_MIN_CONFIDENCE  # 0.5 + Amharic keyword in the summary
    assert _classify("ዜና", "ሌላ ርዕስ", provider={"ethiopia": 0.5})["ethiopia"] < config.CLASSIFY_MIN_CONFIDENCE


def test_keywords_match_whole_words_only():
    assert "war_conflict" not in _classify("Warner Music signs new artist")  # 'war' inside a word
    assert "technology" not in _confident(_classify("Said the chair at the meeting"))  # 'ai' inside 'Said'


def test_reason_is_recorded():
    results = RuleClassifier().classify({"title": "Ransomware hits hospital", "summary": "", "tags": []},
                                        {}, _rules())
    reasons = {r[0]: r[3] for r in results}
    cyber = db.category_by_key("cybersecurity").id
    assert "title 'ransomware'" in reasons[cyber]
