"""SQLite storage: users, interests, categories, providers, articles and notifications.

The schema lives in migrations/*.sql, applied in order and recorded in schema_migrations.
Everything runs on the bot's event loop thread, so one connection is shared.
"""
import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import config

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now() -> str:
    return now_utc().isoformat()


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def parse_iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def to_local(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(config.TIMEZONE)


def local_today() -> date:
    return datetime.now(config.TIMEZONE).date()


def day_start_utc(day: date) -> str:
    return datetime.combine(day, time.min, tzinfo=config.TIMEZONE).astimezone(timezone.utc).isoformat()


# ---------- records ----------
@dataclass
class User:
    telegram_id: int
    chat_id: int
    username: str | None
    first_name: str | None
    language: str | None
    role: str
    status: str
    onboarding_completed: bool
    notifications_enabled: bool
    delivery_mode: str
    digest_hour: int
    last_digest_date: str | None
    max_per_day: int
    news_language: str
    created_at: str
    updated_at: str
    last_seen_at: str

    @property
    def is_super_admin(self) -> bool:
        return self.telegram_id in config.SUPER_ADMIN_IDS

    @property
    def is_admin(self) -> bool:
        return (self.is_super_admin or self.role == "admin") and self.status != "disabled"

    @property
    def display_name(self) -> str:
        name = self.first_name or "Unknown"
        return f"{name} (@{self.username})" if self.username else name


@dataclass
class Category:
    id: int
    parent_id: int | None
    key: str
    emoji: str
    name_en: str
    name_am: str
    description: str | None
    keywords: dict
    sort_order: int
    is_active: bool
    created_at: str
    updated_at: str

    def name(self, language: str | None) -> str:
        return self.name_am if language == "am" else self.name_en

    def label(self, language: str | None) -> str:
        return f"{self.emoji} {self.name(language)}"


@dataclass
class Provider:
    id: int
    key: str
    name: str
    base_url: str
    provider_type: str
    feed_url: str
    adapter_key: str
    polling_interval_seconds: int
    timeout_seconds: int
    is_enabled: bool
    language: str
    categories: dict
    notes: str | None
    etag: str | None
    last_modified: str | None
    last_fetched_at: str | None
    last_success_at: str | None
    last_error: str | None
    consecutive_failures: int
    next_fetch_at: str | None
    articles_total: int
    created_at: str
    updated_at: str


@dataclass
class Article:
    id: int
    provider_id: int
    external_id: str | None
    title: str
    normalized_title: str
    title_hash: str
    summary: str | None
    canonical_url: str
    image_url: str | None
    author: str | None
    language: str
    published_at: str | None
    fetched_at: str
    content_hash: str
    story_cluster_id: int | None
    is_backlog: bool
    tags: list = field(default_factory=list)
    created_at: str = ""

    @property
    def cluster_id(self) -> int:
        return self.story_cluster_id or self.id


@dataclass
class Notification:
    id: int
    user_id: int
    article_id: int
    cluster_id: int
    category_id: int | None
    status: str
    attempt_count: int
    next_attempt_at: str
    telegram_message_id: int | None
    last_error: str | None
    sent_at: str | None
    created_at: str
    updated_at: str


def _row(cls, row: sqlite3.Row):
    data = {k: row[k] for k in cls.__dataclass_fields__ if k in row.keys()}
    for name, f in cls.__dataclass_fields__.items():
        if name not in data:
            continue
        if f.type in ("bool", bool):
            data[name] = bool(data[name])
        elif f.type in ("dict", dict, "list", "list | None") or name in ("keywords", "categories", "tags"):
            data[name] = json.loads(data[name] or ("[]" if name == "tags" else "{}"))
    return cls(**data)


class Database:
    def __init__(self, path: str):
        if path != ":memory:" and os.path.dirname(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self._migrate()

    # ---------- migrations ----------
    def _migrate(self) -> None:
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        done = {r["version"] for r in self.conn.execute("SELECT version FROM schema_migrations")}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.stem in done:
                continue
            try:
                self.conn.executescript("BEGIN;" + path.read_text(encoding="utf-8") + ";"
                                        f"INSERT INTO schema_migrations VALUES ('{path.stem}', '{_now()}');COMMIT;")
            except Exception:
                if self.conn.in_transaction:
                    self.conn.rollback()
                raise

    def ping(self) -> bool:
        return self.conn.execute("SELECT 1").fetchone()[0] == 1

    # ---------- seed ----------
    def seed(self, categories: list[dict], providers: list[dict]) -> tuple[int, int]:
        """Add categories / providers whose key isn't in the database yet. Existing rows are left alone,
        so changes made on the admin page survive restarts. Returns how many of each were added."""
        now = _now()
        added_c = added_p = 0
        for i, c in enumerate(categories):
            cur = self.conn.execute(
                """INSERT OR IGNORE INTO categories (key, emoji, name_en, name_am, description, keywords,
                                                     sort_order, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (c["key"], c.get("emoji", "📰"), c["name_en"], c.get("name_am") or c["name_en"],
                 c.get("description"), json.dumps(c.get("keywords", {}), ensure_ascii=False),
                 c.get("sort_order", i), now, now),
            )
            added_c += cur.rowcount
        for p in providers:
            cur = self.conn.execute(
                """INSERT OR IGNORE INTO providers (key, name, base_url, provider_type, feed_url, adapter_key,
                       polling_interval_seconds, timeout_seconds, is_enabled, language, categories, notes,
                       created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (p["key"], p["name"], p["base_url"], p.get("type", "rss"), p["feed_url"], p.get("adapter", "rss"),
                 int(p.get("interval", 600)), int(p.get("timeout", config.FETCH_TIMEOUT_SECONDS)),
                 int(bool(p.get("enabled", False))), p.get("language", "en"),
                 json.dumps(p.get("categories", {})), p.get("notes"), now, now),
            )
            added_p += cur.rowcount
        self.conn.commit()
        return added_c, added_p

    # ---------- users ----------
    def upsert_user(self, telegram_id: int, chat_id: int, username: str | None, first_name: str | None) -> User:
        now = _now()
        self.conn.execute(
            """
            INSERT INTO users (telegram_id, chat_id, username, first_name, max_per_day,
                               created_at, updated_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                chat_id = excluded.chat_id,
                username = excluded.username,
                first_name = excluded.first_name,
                last_seen_at = excluded.last_seen_at,
                -- Writing to us again means they unblocked the bot.
                status = CASE WHEN users.status = 'blocked' THEN 'active' ELSE users.status END
            """,
            (telegram_id, chat_id, username, first_name, config.DEFAULT_MAX_PER_DAY, now, now, now),
        )
        self.conn.commit()
        return self.get_user(telegram_id)

    def get_user(self, telegram_id: int) -> User | None:
        row = self.conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        return _row(User, row) if row else None

    USER_FIELDS = {"language", "role", "status", "onboarding_completed", "notifications_enabled",
                   "delivery_mode", "digest_hour", "last_digest_date", "max_per_day", "news_language"}

    def update_user(self, telegram_id: int, **values) -> User:
        unknown = set(values) - self.USER_FIELDS
        if unknown:
            raise ValueError(f"Unknown user fields: {unknown}")
        if values.get("status") == "disabled" and telegram_id in config.SUPER_ADMIN_IDS:
            raise ValueError("Super admins can't be disabled.")
        sets = ", ".join(f"{k} = ?" for k in values)
        args = [int(v) if isinstance(v, bool) else v for v in values.values()]
        self.conn.execute(f"UPDATE users SET {sets}, updated_at = ? WHERE telegram_id = ?",
                          args + [_now(), telegram_id])
        self.conn.commit()
        return self.get_user(telegram_id)

    def list_users(self, search: str = "") -> list[dict]:
        """Users with their topic count and alerts sent today (for the admin page)."""
        like = f"%{search.strip()}%"
        rows = self.conn.execute(
            """
            SELECT u.*,
                   (SELECT COUNT(*) FROM user_interests i WHERE i.user_id = u.telegram_id AND i.is_enabled = 1)
                       AS topics,
                   (SELECT COUNT(*) FROM notifications n WHERE n.user_id = u.telegram_id AND n.status = 'sent'
                        AND n.sent_at >= ?) AS sent_today
            FROM users u
            WHERE ? = '%%' OR u.first_name LIKE ? OR u.username LIKE ? OR CAST(u.telegram_id AS TEXT) LIKE ?
            ORDER BY u.created_at DESC
            """,
            (day_start_utc(local_today()), like, like, like, like),
        ).fetchall()
        return [{"user": _row(User, r), "topics": r["topics"], "sent_today": r["sent_today"]} for r in rows]

    def admin_ids(self) -> list[int]:
        rows = self.conn.execute("SELECT telegram_id, role FROM users WHERE status = 'active'").fetchall()
        return [r["telegram_id"] for r in rows
                if r["role"] == "admin" or r["telegram_id"] in config.SUPER_ADMIN_IDS]

    def delete_user(self, telegram_id: int) -> None:
        """Delete a user and everything stored about them (interests and notifications cascade)."""
        self.conn.execute("DELETE FROM users WHERE telegram_id = ?", (telegram_id,))
        self.conn.commit()

    # ---------- categories ----------
    def categories(self, active_only: bool = True) -> list[Category]:
        where = "WHERE is_active = 1" if active_only else ""
        rows = self.conn.execute(f"SELECT * FROM categories {where} ORDER BY sort_order, id").fetchall()
        return [_row(Category, r) for r in rows]

    def get_category(self, category_id: int) -> Category | None:
        row = self.conn.execute("SELECT * FROM categories WHERE id = ?", (category_id,)).fetchone()
        return _row(Category, row) if row else None

    def category_by_key(self, key: str) -> Category | None:
        row = self.conn.execute("SELECT * FROM categories WHERE key = ?", (key,)).fetchone()
        return _row(Category, row) if row else None

    def set_category_active(self, category_id: int, active: bool) -> None:
        self.conn.execute("UPDATE categories SET is_active = ?, updated_at = ? WHERE id = ?",
                          (int(active), _now(), category_id))
        self.conn.commit()

    def set_category_keywords(self, category_id: int, keywords: dict) -> None:
        self.conn.execute("UPDATE categories SET keywords = ?, updated_at = ? WHERE id = ?",
                          (json.dumps(keywords, ensure_ascii=False), _now(), category_id))
        self.conn.commit()

    # ---------- interests ----------
    def interests(self, user_id: int) -> dict[int, bool]:
        """{category_id: enabled} for a user's topics (muted ones have False). Inactive categories left out."""
        rows = self.conn.execute(
            """SELECT i.category_id, i.is_enabled FROM user_interests i
               JOIN categories c ON c.id = i.category_id AND c.is_active = 1
               WHERE i.user_id = ?""", (user_id,)).fetchall()
        return {r["category_id"]: bool(r["is_enabled"]) for r in rows}

    def set_interests(self, user_id: int, category_ids: set[int]) -> None:
        """Make the user's topics exactly these. Kept topics keep their mute state and start date."""
        valid = {c.id for c in self.categories()}
        wanted = set(category_ids) & valid
        now = _now()
        current = set(self.interests(user_id))
        for cid in current - wanted:
            self.conn.execute("DELETE FROM user_interests WHERE user_id = ? AND category_id = ?", (user_id, cid))
        for cid in wanted - current:
            self.conn.execute(
                """INSERT OR IGNORE INTO user_interests (user_id, category_id, is_enabled, created_at, updated_at)
                   VALUES (?, ?, 1, ?, ?)""", (user_id, cid, now, now))
        self.conn.commit()

    def set_interest_enabled(self, user_id: int, category_id: int, enabled: bool) -> bool:
        """Mute / unmute a topic. Returns False if the user doesn't follow it."""
        cur = self.conn.execute(
            "UPDATE user_interests SET is_enabled = ?, updated_at = ? WHERE user_id = ? AND category_id = ?",
            (int(enabled), _now(), user_id, category_id))
        self.conn.commit()
        return cur.rowcount > 0

    # ---------- providers ----------
    def providers(self, enabled_only: bool = False) -> list[Provider]:
        where = "WHERE is_enabled = 1" if enabled_only else ""
        rows = self.conn.execute(f"SELECT * FROM providers {where} ORDER BY name, key").fetchall()
        return [_row(Provider, r) for r in rows]

    def get_provider(self, provider_id: int) -> Provider | None:
        row = self.conn.execute("SELECT * FROM providers WHERE id = ?", (provider_id,)).fetchone()
        return _row(Provider, row) if row else None

    def due_providers(self, now: datetime) -> list[Provider]:
        rows = self.conn.execute(
            "SELECT * FROM providers WHERE is_enabled = 1 AND (next_fetch_at IS NULL OR next_fetch_at <= ?)"
            " ORDER BY next_fetch_at", (iso(now),)).fetchall()
        return [_row(Provider, r) for r in rows]

    PROVIDER_FIELDS = {"is_enabled", "polling_interval_seconds", "timeout_seconds", "categories", "notes",
                       "next_fetch_at", "name", "language"}

    def update_provider(self, provider_id: int, **values) -> None:
        unknown = set(values) - self.PROVIDER_FIELDS
        if unknown:
            raise ValueError(f"Unknown provider fields: {unknown}")
        if "categories" in values:
            values["categories"] = json.dumps(values["categories"])
        sets = ", ".join(f"{k} = ?" for k in values)
        args = [int(v) if isinstance(v, bool) else v for v in values.values()]
        self.conn.execute(f"UPDATE providers SET {sets}, updated_at = ? WHERE id = ?", args + [_now(), provider_id])
        self.conn.commit()

    def record_fetch(self, provider_id: int, ok: bool, error: str | None, next_fetch_at: datetime,
                     etag: str | None = None, last_modified: str | None = None, new_articles: int = 0) -> None:
        now = _now()
        if ok:
            self.conn.execute(
                """UPDATE providers SET last_fetched_at = ?, last_success_at = ?, last_error = NULL,
                       consecutive_failures = 0, next_fetch_at = ?, etag = COALESCE(?, etag),
                       last_modified = COALESCE(?, last_modified), articles_total = articles_total + ?
                   WHERE id = ?""",
                (now, now, iso(next_fetch_at), etag, last_modified, new_articles, provider_id))
        else:
            self.conn.execute(
                """UPDATE providers SET last_fetched_at = ?, last_error = ?,
                       consecutive_failures = consecutive_failures + 1, next_fetch_at = ?
                   WHERE id = ?""", (now, (error or "")[:500], iso(next_fetch_at), provider_id))
        self.conn.commit()

    # ---------- articles ----------
    def article_by_external_id(self, provider_id: int, external_id: str) -> Article | None:
        row = self.conn.execute("SELECT * FROM articles WHERE provider_id = ? AND external_id = ?",
                                (provider_id, external_id)).fetchone()
        return _row(Article, row) if row else None

    def article_by_url(self, url: str) -> Article | None:
        row = self.conn.execute("SELECT * FROM articles WHERE canonical_url = ?", (url,)).fetchone()
        return _row(Article, row) if row else None

    def get_article(self, article_id: int) -> Article | None:
        row = self.conn.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
        return _row(Article, row) if row else None

    def recent_titles(self, since: datetime) -> list[tuple[int, str, str, int]]:
        """(id, normalized_title, title_hash, cluster_id) of articles fetched since a time."""
        rows = self.conn.execute(
            "SELECT id, normalized_title, title_hash, COALESCE(story_cluster_id, id) AS cluster FROM articles"
            " WHERE fetched_at >= ? ORDER BY id DESC LIMIT 5000", (iso(since),)).fetchall()
        return [(r["id"], r["normalized_title"], r["title_hash"], r["cluster"]) for r in rows]

    def insert_article(self, provider_id: int, a: dict) -> int:
        cur = self.conn.execute(
            """INSERT INTO articles (provider_id, external_id, title, normalized_title, title_hash, summary,
                   canonical_url, image_url, author, language, published_at, fetched_at, content_hash,
                   story_cluster_id, is_backlog, tags, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (provider_id, a.get("external_id"), a["title"], a["normalized_title"], a["title_hash"], a.get("summary"),
             a["canonical_url"], a.get("image_url"), a.get("author"), a["language"], a.get("published_at"),
             a["fetched_at"], a["content_hash"], a.get("story_cluster_id"), int(a.get("is_backlog", False)),
             json.dumps(a.get("tags", []), ensure_ascii=False), _now()))
        self.conn.commit()
        return cur.lastrowid

    def recent_articles(self, limit: int = 50, offset: int = 0, low_confidence: bool = False,
                        provider_id: int | None = None) -> list[dict]:
        """Newest articles with their categories, for the admin page. low_confidence: articles with no
        category at or above the threshold."""
        where, args = [], []
        if provider_id:
            where.append("a.provider_id = ?"); args.append(provider_id)
        if low_confidence:
            where.append("NOT EXISTS (SELECT 1 FROM article_categories ac WHERE ac.article_id = a.id"
                         " AND ac.confidence >= ?)"); args.append(config.CLASSIFY_MIN_CONFIDENCE)
        sql = ("SELECT a.*, p.name AS provider_name FROM articles a JOIN providers p ON p.id = a.provider_id"
               + (" WHERE " + " AND ".join(where) if where else "")
               + " ORDER BY a.id DESC LIMIT ? OFFSET ?")
        rows = self.conn.execute(sql, args + [limit, offset]).fetchall()
        return [{"article": _row(Article, r), "provider_name": r["provider_name"],
                 "categories": self.article_categories(r["id"])} for r in rows]

    # ---------- article categories ----------
    def article_categories(self, article_id: int) -> list[dict]:
        rows = self.conn.execute(
            """SELECT ac.*, c.key, c.emoji, c.name_en FROM article_categories ac
               JOIN categories c ON c.id = ac.category_id WHERE ac.article_id = ? ORDER BY ac.confidence DESC""",
            (article_id,)).fetchall()
        return [dict(r) for r in rows]

    def add_article_categories(self, article_id: int, results: list[tuple[int, float, str, str]]) -> None:
        """(category_id, confidence, method, reason). Keeps the higher confidence; manual rows are never
        overwritten by automatic ones."""
        now = _now()
        for category_id, confidence, method, reason in results:
            self.conn.execute(
                """INSERT INTO article_categories (article_id, category_id, confidence, method, reason, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(article_id, category_id) DO UPDATE SET
                       confidence = excluded.confidence, method = excluded.method, reason = excluded.reason
                   WHERE article_categories.method NOT LIKE 'manual%'
                     AND excluded.confidence > article_categories.confidence""",
                (article_id, category_id, round(confidence, 3), method, reason, now))
        self.conn.commit()

    def set_manual_categories(self, article_id: int, category_ids: set[int]) -> None:
        """An admin's correction: exactly these categories, at full confidence."""
        now = _now()
        self.conn.execute("DELETE FROM article_categories WHERE article_id = ?", (article_id,))
        for cid in category_ids:
            self.conn.execute(
                "INSERT INTO article_categories VALUES (?, ?, 1.0, 'manual', 'set by an admin', ?)",
                (article_id, cid, now))
        self.conn.commit()

    # ---------- matching ----------
    def matching_users(self, article: Article) -> list[tuple[int, int]]:
        """(user_id, category_id) for every user who should get this article: active, not paused, onboarded,
        following one of its confident categories (not muted) since before the article was found, and reading
        its language. The category is the article's most confident one among the user's topics."""
        rows = self.conn.execute(
            """
            SELECT i.user_id, ac.category_id, ac.confidence
            FROM article_categories ac
            JOIN categories c ON c.id = ac.category_id AND c.is_active = 1
            JOIN user_interests i ON i.category_id = ac.category_id AND i.is_enabled = 1
            JOIN users u ON u.telegram_id = i.user_id
            WHERE ac.article_id = ? AND ac.confidence >= ?
              AND u.status = 'active' AND u.notifications_enabled = 1 AND u.onboarding_completed = 1
              AND (u.news_language = 'any' OR u.news_language = ?)
              AND i.created_at <= ?
            ORDER BY ac.confidence DESC
            """,
            (article.id, config.CLASSIFY_MIN_CONFIDENCE, article.language, article.fetched_at)).fetchall()
        best: dict[int, int] = {}
        for r in rows:
            best.setdefault(r["user_id"], r["category_id"])
        return list(best.items())

    def queue_notifications(self, article: Article, matches: list[tuple[int, int]]) -> int:
        """Queue one notification per (user, category). Users in digest mode get it held for their digest;
        users over their daily limit get it marked skipped. Unique constraints make this safe to repeat
        (same article, or another article of the same story). Returns how many were newly queued."""
        if not matches:
            return 0
        now = _now()
        since = day_start_utc(local_today())
        queued = 0
        for user_id, category_id in matches:
            user = self.get_user(user_id)
            if not user:
                continue
            status = "digest" if user.delivery_mode == "digest" else "queued"
            error = None
            if status == "queued" and user.max_per_day:
                today = self.conn.execute(
                    "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND status IN ('queued', 'sent')"
                    " AND created_at >= ?", (user_id, since)).fetchone()[0]
                if today >= user.max_per_day:
                    status, error = "skipped", "daily limit reached"
            cur = self.conn.execute(
                """INSERT OR IGNORE INTO notifications (user_id, article_id, cluster_id, category_id, status,
                       next_attempt_at, last_error, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, article.id, article.cluster_id, category_id, status, now, error, now, now))
            queued += cur.rowcount if status != "skipped" else 0
        self.conn.commit()
        return queued

    # ---------- notifications ----------
    def due_notifications(self, limit: int = 30) -> list[Notification]:
        rows = self.conn.execute(
            "SELECT * FROM notifications WHERE status = 'queued' AND next_attempt_at <= ? ORDER BY id LIMIT ?",
            (_now(), limit)).fetchall()
        return [_row(Notification, r) for r in rows]

    def get_notification(self, notification_id: int) -> Notification | None:
        row = self.conn.execute("SELECT * FROM notifications WHERE id = ?", (notification_id,)).fetchone()
        return _row(Notification, row) if row else None

    def mark_notification(self, notification_id: int, status: str, error: str | None = None,
                          message_id: int | None = None, retry_at: datetime | None = None,
                          count_attempt: bool = True) -> None:
        now = _now()
        self.conn.execute(
            """UPDATE notifications SET status = ?, last_error = ?, telegram_message_id = COALESCE(?, telegram_message_id),
                   attempt_count = attempt_count + ?, next_attempt_at = COALESCE(?, next_attempt_at),
                   sent_at = CASE WHEN ? = 'sent' THEN ? ELSE sent_at END, updated_at = ?
               WHERE id = ?""",
            (status, (error or None) and error[:500], message_id, int(count_attempt), iso(retry_at),
             status, now, now, notification_id))
        self.conn.commit()

    def skip_queued_for_user(self, user_id: int, reason: str) -> int:
        cur = self.conn.execute(
            "UPDATE notifications SET status = 'skipped', last_error = ?, updated_at = ?"
            " WHERE user_id = ? AND status IN ('queued', 'digest')", (reason, _now(), user_id))
        self.conn.commit()
        return cur.rowcount

    def move_held_notifications(self, user_id: int, to_status: str) -> None:
        """When a user switches between instant and digest, their waiting alerts follow."""
        src = "queued" if to_status == "digest" else "digest"
        self.conn.execute("UPDATE notifications SET status = ?, next_attempt_at = ?, updated_at = ?"
                          " WHERE user_id = ? AND status = ?", (to_status, _now(), _now(), user_id, src))
        self.conn.commit()

    def digest_users_due(self, local_hour: int, local_date: str) -> list[User]:
        rows = self.conn.execute(
            """SELECT * FROM users WHERE delivery_mode = 'digest' AND status = 'active' AND notifications_enabled = 1
                   AND digest_hour = ? AND (last_digest_date IS NULL OR last_digest_date != ?)""",
            (local_hour, local_date)).fetchall()
        return [_row(User, r) for r in rows]

    def digest_items(self, user_id: int) -> list[tuple[Notification, Article]]:
        rows = self.conn.execute(
            """SELECT n.id AS n_id, a.* FROM notifications n JOIN articles a ON a.id = n.article_id
               WHERE n.user_id = ? AND n.status = 'digest' ORDER BY a.id DESC""", (user_id,)).fetchall()
        return [(self.get_notification(r["n_id"]), _row(Article, r)) for r in rows]

    def failed_notifications(self, limit: int = 100) -> list[dict]:
        rows = self.conn.execute(
            """SELECT n.*, a.title, u.first_name, u.username FROM notifications n
               JOIN articles a ON a.id = n.article_id JOIN users u ON u.telegram_id = n.user_id
               WHERE n.status = 'failed' ORDER BY n.updated_at DESC LIMIT ?""", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def retry_notification(self, notification_id: int) -> bool:
        cur = self.conn.execute(
            "UPDATE notifications SET status = 'queued', attempt_count = 0, next_attempt_at = ?, updated_at = ?"
            " WHERE id = ? AND status = 'failed'", (_now(), _now(), notification_id))
        self.conn.commit()
        return cur.rowcount > 0

    # ---------- stats ----------
    def stats(self) -> dict:
        since = day_start_utc(local_today())
        one = lambda sql, *a: self.conn.execute(sql, a).fetchone()[0]  # noqa: E731
        oldest = one("SELECT MIN(next_attempt_at) FROM notifications WHERE status = 'queued'")
        return {
            "users": one("SELECT COUNT(*) FROM users"),
            "active_users": one("SELECT COUNT(*) FROM users WHERE status = 'active' AND notifications_enabled = 1"
                                " AND onboarding_completed = 1"),
            "paused_users": one("SELECT COUNT(*) FROM users WHERE notifications_enabled = 0"),
            "blocked_users": one("SELECT COUNT(*) FROM users WHERE status = 'blocked'"),
            "providers_enabled": one("SELECT COUNT(*) FROM providers WHERE is_enabled = 1"),
            "providers_failing": one("SELECT COUNT(*) FROM providers WHERE is_enabled = 1 AND consecutive_failures > 0"),
            "articles_today": one("SELECT COUNT(*) FROM articles WHERE fetched_at >= ?", since),
            "articles_total": one("SELECT COUNT(*) FROM articles"),
            "duplicates_today": one("SELECT COUNT(*) FROM articles WHERE fetched_at >= ? AND story_cluster_id IS NOT NULL",
                                    since),
            "low_confidence_today": one(
                "SELECT COUNT(*) FROM articles a WHERE a.fetched_at >= ? AND NOT EXISTS (SELECT 1 FROM"
                " article_categories ac WHERE ac.article_id = a.id AND ac.confidence >= ?)",
                since, config.CLASSIFY_MIN_CONFIDENCE),
            "queued": one("SELECT COUNT(*) FROM notifications WHERE status = 'queued'"),
            "queue_oldest": oldest,
            "held_for_digest": one("SELECT COUNT(*) FROM notifications WHERE status = 'digest'"),
            "sent_today": one("SELECT COUNT(*) FROM notifications WHERE status = 'sent' AND sent_at >= ?", since),
            "failed_today": one("SELECT COUNT(*) FROM notifications WHERE status = 'failed' AND updated_at >= ?", since),
            "skipped_today": one("SELECT COUNT(*) FROM notifications WHERE status = 'skipped' AND updated_at >= ?", since),
        }

    # ---------- retention ----------
    def delete_old_articles(self, older_than: datetime) -> int:
        cur = self.conn.execute("DELETE FROM articles WHERE fetched_at < ?", (iso(older_than),))
        self.conn.commit()
        return cur.rowcount

    # ---------- settings ----------
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value))
        self.conn.commit()

    # ---------- backup ----------
    def copy_to(self, path: str) -> None:
        """Consistent copy of the whole database file (safe while the bot is running)."""
        target = sqlite3.connect(path)
        try:
            self.conn.backup(target)
        finally:
            target.close()


def load_seed() -> tuple[list[dict], list[dict]]:
    base = Path(config.SEED_DIR)
    if not base.is_absolute():
        base = Path(__file__).parent / base
    categories = json.loads((base / "categories.json").read_text(encoding="utf-8"))["categories"]
    providers = json.loads((base / "providers.json").read_text(encoding="utf-8"))["providers"]
    return categories, providers


db = Database(config.DATABASE_PATH)
