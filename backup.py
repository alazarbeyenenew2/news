"""Builds a zipped backup of everything the bot stores.

The zip holds:
  news.db         a full copy of the SQLite database (restore by putting it back as DATABASE_PATH)
  users.csv       every user and their settings, readable in Excel / Google Sheets
  providers.csv   every provider and its fetch status
  README.txt      what the files are
"""
import csv
import io
import os
import tempfile
import zipfile
from datetime import datetime, timedelta

import config
from db import db

TELEGRAM_UPLOAD_LIMIT = 49 * 1024 * 1024  # bots can send files up to 50 MB

README = """News bot backup
===============

news.db        Full copy of the database. To restore, stop the bot and replace the database file
               (DATABASE_PATH, /app/data/news.db in Docker) with this file.
users.csv      Every user: language, status, settings and number of topics.
providers.csv  Every news provider: feed, on/off, last success and last error.
"""


def next_midnight(after: datetime | None = None) -> datetime:
    """The next 00:00 in TIMEZONE."""
    after = after or datetime.now(config.TIMEZONE)
    return datetime.combine(after.date() + timedelta(days=1), datetime.min.time(), tzinfo=config.TIMEZONE)


def _csv(rows: list[dict], columns: list[str]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([row.get(c, "") if row.get(c) is not None else "" for c in columns])
    # A BOM makes Excel read the file as UTF-8, so Amharic shows correctly.
    return ("﻿" + out.getvalue()).encode("utf-8")


def make_backup() -> tuple[bytes, str, dict]:
    """Returns (zip bytes, file name, counts)."""
    users = [dict(r) for r in db.conn.execute(
        """SELECT u.*, (SELECT COUNT(*) FROM user_interests i WHERE i.user_id = u.telegram_id) AS topics
           FROM users u ORDER BY u.created_at""")]
    providers = [dict(r) for r in db.conn.execute("SELECT * FROM providers ORDER BY key")]
    articles = db.conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    stamp = datetime.now(config.TIMEZONE).strftime("%Y-%m-%d_%H%M")
    buffer = io.BytesIO()
    with tempfile.TemporaryDirectory() as tmp:
        db_copy = os.path.join(tmp, "news.db")
        db.copy_to(db_copy)
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.write(db_copy, "news.db")
            z.writestr("users.csv", _csv(users, [
                "telegram_id", "username", "first_name", "language", "role", "status", "onboarding_completed",
                "notifications_enabled", "delivery_mode", "digest_hour", "max_per_day", "news_language", "topics",
                "created_at", "last_seen_at"]))
            z.writestr("providers.csv", _csv(providers, [
                "key", "name", "feed_url", "is_enabled", "language", "polling_interval_seconds", "categories",
                "last_success_at", "last_error", "consecutive_failures", "articles_total", "notes"]))
            z.writestr("README.txt", README)
    return buffer.getvalue(), f"news-bot-backup_{stamp}.zip", {"users": len(users), "articles": articles}
