"""Configuration loaded from environment variables (or a .env file)."""
import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def _int_list(value: str) -> set[int]:
    return {int(x) for x in value.replace(" ", "").split(",") if x}


def _bool(name: str, default: str) -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes", "on")


BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BOT_NAME = os.getenv("BOT_NAME", "NewsPulse")

# Telegram user IDs that are always admins ("super admins"). Set only here, never from the bot or web page.
SUPER_ADMIN_IDS = _int_list(os.getenv("SUPER_ADMIN_IDS", ""))

# Web admin page login
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
SECRET_KEY = os.getenv("SECRET_KEY", "change-me")

# Times shown to users (publication time, digest hour) and the meaning of "today".
TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "UTC"))

# PostgreSQL. In production the user and password come from GitHub secrets (see README).
DATABASE = {
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "port": int(os.getenv("POSTGRES_PORT", "5432")),
    "user": os.getenv("POSTGRES_USER", "newsbot"),
    "password": os.getenv("POSTGRES_PASSWORD", ""),
    "dbname": os.getenv("POSTGRES_DB", "newsbot"),
}

# Categories and providers are copied from these files into the database the first time the bot
# starts (and when new keys are added). After that they're managed from the admin page / bot.
SEED_DIR = os.getenv("SEED_DIR", "seed")

WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("WEB_PORT", "8000"))

# ---------- news collection ----------
# Turn the collector / sender off, e.g. to run a second copy of the web page only.
COLLECTOR_ENABLED = _bool("COLLECTOR_ENABLED", "true")
DELIVERY_ENABLED = _bool("DELIVERY_ENABLED", "true")
FETCH_TIMEOUT_SECONDS = float(os.getenv("FETCH_TIMEOUT_SECONDS", "20"))
FETCH_MAX_BYTES = int(os.getenv("FETCH_MAX_BYTES", str(5 * 1024 * 1024)))
FETCH_CONCURRENCY = int(os.getenv("FETCH_CONCURRENCY", "4"))
USER_AGENT = os.getenv("USER_AGENT", "NewsPulseBot/1.0 (RSS reader; links to original articles)")
# Articles older than this when we first see them are stored but never alerted.
MAX_ARTICLE_AGE_HOURS = int(os.getenv("MAX_ARTICLE_AGE_HOURS", "12"))
# An article goes to a category only at or above this confidence (0..1). Below it, it waits for review.
CLASSIFY_MIN_CONFIDENCE = float(os.getenv("CLASSIFY_MIN_CONFIDENCE", "0.6"))
# Near-identical headlines within this window are treated as the same story.
DEDUP_WINDOW_HOURS = int(os.getenv("DEDUP_WINDOW_HOURS", "48"))
DEDUP_TITLE_SIMILARITY = float(os.getenv("DEDUP_TITLE_SIMILARITY", "0.88"))
# How long articles are kept (notifications for them are deleted too). 0 = forever.
ARTICLE_RETENTION_DAYS = int(os.getenv("ARTICLE_RETENTION_DAYS", "30"))

# Optional NIST NVD API key for CVE reports (higher rate limit): https://nvd.nist.gov/developers/request-an-api-key
NVD_API_KEY = os.getenv("NVD_API_KEY", "")

# ---------- delivery ----------
# Telegram allows about 30 messages per second overall; stay under it.
MESSAGES_PER_SECOND = float(os.getenv("MESSAGES_PER_SECOND", "20"))
MAX_SEND_ATTEMPTS = int(os.getenv("MAX_SEND_ATTEMPTS", "5"))
# Default daily alert limit for new users (0 = no limit). Users can change theirs in ⚙️ Settings.
DEFAULT_MAX_PER_DAY = int(os.getenv("DEFAULT_MAX_PER_DAY", "30"))
DIGEST_MAX_ARTICLES = int(os.getenv("DIGEST_MAX_ARTICLES", "15"))
