"""Builds the zipped backup sent to super admins on Telegram.

The zip holds one CSV per table (with a header row, restorable with COPY … CSV HEADER) and a README
with the restore steps. Full pg_dump backups are made on the server by the deploy workflow and can be
made any time with `docker compose exec postgres pg_dump` (see README.md).
"""
import io
import zipfile
from datetime import datetime, timedelta

import config
from db import db

TELEGRAM_UPLOAD_LIMIT = 49 * 1024 * 1024  # bots can send files up to 50 MB

README = """News bot backup
===============

One CSV file per database table. users.csv and providers.csv open in Excel / Google Sheets.

To restore into an empty database (the bot stopped):
  1. Create the tables:   psql -f migrations/001_initial.sql   (every file in migrations/, in order)
  2. Load the tables in this order, each with:
       \\copy <table> FROM '<table>.csv' WITH (FORMAT csv, HEADER true)
     categories, providers, users, user_interests, user_source_exclusions, articles, article_categories, notifications, settings,
     schema_migrations
  3. Move the id counters past the loaded rows, for each of categories, providers, articles, notifications:
       SELECT setval(pg_get_serial_sequence('articles', 'id'), (SELECT COALESCE(MAX(id), 1) FROM articles));
  4. Start the bot.
"""


def next_midnight(after: datetime | None = None) -> datetime:
    """The next 00:00 in TIMEZONE."""
    after = after or datetime.now(config.TIMEZONE)
    return datetime.combine(after.date() + timedelta(days=1), datetime.min.time(), tzinfo=config.TIMEZONE)


def make_backup() -> tuple[bytes, str, dict]:
    """Returns (zip bytes, file name, counts). Runs on the event loop thread (it shares the connection)."""
    stamp = datetime.now(config.TIMEZONE).strftime("%Y-%m-%d_%H%M")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for table in db.BACKUP_TABLES:
            # Exactly as COPY wrote them (UTF-8, no BOM), so they restore cleanly.
            z.writestr(f"{table}.csv", db.export_table_csv(table))
        z.writestr("README.txt", README)
    users = db._one("SELECT COUNT(*) FROM users")
    return buffer.getvalue(), f"news-bot-backup_{stamp}.zip", {"users": users, "articles": db.count_articles()}
