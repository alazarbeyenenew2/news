"""PostgreSQL storage: users, interests, categories, providers, articles and notifications.

The schema lives in migrations/*.sql, applied in order and recorded in schema_migrations.
Everything runs on the bot's event loop thread, so one autocommit connection is shared; multi-step
changes use explicit transactions. Times are passed in and handed out as UTC ISO-8601 strings.
"""
import json
import logging
import time as _time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

import config

log = logging.getLogger(__name__)
MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now() -> str:
    return now_utc().isoformat()


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def parse_iso(value: str | datetime | None) -> datetime | None:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value) if value else None


def to_local(value: str | datetime) -> datetime:
    return parse_iso(value).astimezone(config.TIMEZONE)


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


def plain(row: dict) -> dict:
    """A row with datetimes as UTC ISO strings and JSON text decoded, so the rest of the app sees
    the same values whatever the driver returns."""
    out = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            v = iso(v)
        elif k in ("keywords", "categories", "tags") and isinstance(v, str):
            v = json.loads(v)
        out[k] = v
    return out


def _row(cls, row: dict):
    data = plain(row)
    return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})


class Database:
    def __init__(self, conninfo: dict | None = None, wait_seconds: int = 60):
        self.conninfo = conninfo or config.DATABASE
        self._in_transaction = False
        self.conn = self._connect(wait_seconds)

    def _connect(self, wait_seconds: int = 0) -> psycopg.Connection:
        """Connect, retrying while the database container starts up."""
        deadline = _time.monotonic() + wait_seconds
        while True:
            try:
                conn = psycopg.connect(**self.conninfo, autocommit=True, row_factory=dict_row,
                                       connect_timeout=5, application_name="news-bot")
                conn.execute("SET TIME ZONE 'UTC'")
                return conn
            except psycopg.OperationalError as e:
                if _time.monotonic() >= deadline:
                    raise
                log.info("Waiting for the database: %s", str(e).strip().splitlines()[0] if str(e) else e)
                _time.sleep(1)

    def execute(self, sql: str, params=None) -> psycopg.Cursor:
        """Run one statement. If the connection was lost (the database restarted), reconnect and retry
        once, except inside a transaction, where the error is raised so nothing is half-applied."""
        try:
            return self.conn.execute(sql, params)
        except psycopg.OperationalError:
            if self._in_transaction or not (self.conn.closed or self.conn.broken):
                raise
            log.warning("Database connection lost; reconnecting")
            self.conn = self._connect(wait_seconds=30)
            return self.conn.execute(sql, params)

    @contextmanager
    def transaction(self):
        if self.conn.closed or self.conn.broken:
            self.conn = self._connect(wait_seconds=30)
        outer = not self._in_transaction
        self._in_transaction = True
        try:
            with self.conn.transaction():
                yield
        finally:
            if outer:
                self._in_transaction = False

    def _one(self, sql: str, params=None):
        row = self.execute(sql, params).fetchone()
        if row is None:
            return None
        value = next(iter(row.values()))
        return iso(value) if isinstance(value, datetime) else value

    def close(self) -> None:
        self.conn.close()

    # ---------- migrations ----------
    def migrate(self, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
        """Bring the tables up to date: run every migrations/NNN_name.sql not run before, in order, and
        record it in schema_migrations. Called first thing at startup. All pending migrations run in one
        transaction, so a failing one leaves the database exactly as it was (and the bot doesn't start).
        Several copies starting at once wait for each other. Returns the versions applied."""
        applied = []
        with self.transaction():
            self.execute("SELECT pg_advisory_xact_lock(4242)")
            self.execute("CREATE TABLE IF NOT EXISTS schema_migrations"
                         " (version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL)")
            done = {r["version"] for r in self.execute("SELECT version FROM schema_migrations")}
            for path in sorted(migrations_dir.glob("*.sql")):
                if path.stem in done:
                    continue
                log.info("Applying migration %s", path.stem)
                self.execute(path.read_text(encoding="utf-8"))
                self.execute("INSERT INTO schema_migrations VALUES (%s, %s)", (path.stem, _now()))
                applied.append(path.stem)
        return applied

    def schema_version(self) -> str | None:
        """The newest migration applied, or None if none has been."""
        if not self._one("SELECT to_regclass('schema_migrations') IS NOT NULL"):
            return None
        return self._one("SELECT MAX(version) FROM schema_migrations")

    def ping(self) -> bool:
        return self._one("SELECT 1") == 1

    # ---------- seed ----------
    def seed(self, categories: list[dict], providers: list[dict]) -> tuple[int, int]:
        """Add categories / providers whose key isn't in the database yet. Existing rows are left alone,
        so changes made on the admin page survive restarts. Returns how many of each were added."""
        now = _now()
        added_c = added_p = 0
        with self.transaction():
            for i, c in enumerate(categories):
                cur = self.execute(
                    """INSERT INTO categories (key, emoji, name_en, name_am, description, keywords,
                                               sort_order, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (key) DO NOTHING""",
                    (c["key"], c.get("emoji", "📰"), c["name_en"], c.get("name_am") or c["name_en"],
                     c.get("description"), json.dumps(c.get("keywords", {}), ensure_ascii=False),
                     c.get("sort_order", i), now, now))
                added_c += cur.rowcount
            for p in providers:
                cur = self.execute(
                    """INSERT INTO providers (key, name, base_url, provider_type, feed_url, adapter_key,
                           polling_interval_seconds, timeout_seconds, is_enabled, language, categories, notes,
                           created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (key) DO NOTHING""",
                    (p["key"], p["name"], p["base_url"], p.get("type", "rss"), p["feed_url"], p.get("adapter", "rss"),
                     int(p.get("interval", 600)), int(p.get("timeout", config.FETCH_TIMEOUT_SECONDS)),
                     bool(p.get("enabled", False)), p.get("language", "en"),
                     json.dumps(p.get("categories", {})), p.get("notes"), now, now))
                added_p += cur.rowcount
        return added_c, added_p

    # ---------- users ----------
    def upsert_user(self, telegram_id: int, chat_id: int, username: str | None, first_name: str | None) -> User:
        now = _now()
        self.execute(
            """
            INSERT INTO users (telegram_id, chat_id, username, first_name, max_per_day,
                               created_at, updated_at, last_seen_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (telegram_id) DO UPDATE SET
                chat_id = excluded.chat_id,
                username = excluded.username,
                first_name = excluded.first_name,
                last_seen_at = excluded.last_seen_at,
                -- Writing to us again means they unblocked the bot.
                status = CASE WHEN users.status = 'blocked' THEN 'active' ELSE users.status END
            """,
            (telegram_id, chat_id, username, first_name, config.DEFAULT_MAX_PER_DAY, now, now, now))
        return self.get_user(telegram_id)

    def get_user(self, telegram_id: int) -> User | None:
        row = self.execute("SELECT * FROM users WHERE telegram_id = %s", (telegram_id,)).fetchone()
        return _row(User, row) if row else None

    USER_FIELDS = {"language", "role", "status", "onboarding_completed", "notifications_enabled",
                   "delivery_mode", "digest_hour", "last_digest_date", "max_per_day", "news_language"}

    def update_user(self, telegram_id: int, **values) -> User:
        unknown = set(values) - self.USER_FIELDS
        if unknown:
            raise ValueError(f"Unknown user fields: {unknown}")
        if values.get("status") == "disabled" and telegram_id in config.SUPER_ADMIN_IDS:
            raise ValueError("Super admins can't be disabled.")
        sets = ", ".join(f"{k} = %s" for k in values)  # keys checked against USER_FIELDS above
        self.execute(f"UPDATE users SET {sets}, updated_at = %s WHERE telegram_id = %s",
                     list(values.values()) + [_now(), telegram_id])
        return self.get_user(telegram_id)

    def list_users(self, search: str = "") -> list[dict]:
        """Users with their topic count and alerts sent today (for the admin page)."""
        search = search.strip()
        like = f"%{search}%"
        rows = self.execute(
            """
            SELECT u.*,
                   (SELECT COUNT(*) FROM user_interests i WHERE i.user_id = u.telegram_id AND i.is_enabled)
                       AS topics,
                   (SELECT COUNT(*) FROM notifications n WHERE n.user_id = u.telegram_id AND n.status = 'sent'
                        AND n.sent_at >= %s) AS sent_today
            FROM users u
            WHERE %s = '' OR u.first_name ILIKE %s OR u.username ILIKE %s OR u.telegram_id::text LIKE %s
            ORDER BY u.created_at DESC
            """,
            (day_start_utc(local_today()), search, like, like, like)).fetchall()
        return [{"user": _row(User, r), "topics": r["topics"], "sent_today": r["sent_today"]} for r in rows]

    def admin_ids(self) -> list[int]:
        rows = self.execute("SELECT telegram_id, role FROM users WHERE status = 'active'").fetchall()
        return [r["telegram_id"] for r in rows
                if r["role"] == "admin" or r["telegram_id"] in config.SUPER_ADMIN_IDS]

    def delete_user(self, telegram_id: int) -> None:
        """Delete a user and everything stored about them (interests and notifications cascade)."""
        self.execute("DELETE FROM users WHERE telegram_id = %s", (telegram_id,))

    # ---------- categories ----------
    def categories(self, active_only: bool = True) -> list[Category]:
        where = "WHERE is_active" if active_only else ""
        rows = self.execute(f"SELECT * FROM categories {where} ORDER BY sort_order, id").fetchall()
        return [_row(Category, r) for r in rows]

    def get_category(self, category_id: int) -> Category | None:
        row = self.execute("SELECT * FROM categories WHERE id = %s", (category_id,)).fetchone()
        return _row(Category, row) if row else None

    def category_by_key(self, key: str) -> Category | None:
        row = self.execute("SELECT * FROM categories WHERE key = %s", (key,)).fetchone()
        return _row(Category, row) if row else None

    def set_category_active(self, category_id: int, active: bool) -> None:
        self.execute("UPDATE categories SET is_active = %s, updated_at = %s WHERE id = %s",
                     (bool(active), _now(), category_id))

    def set_category_keywords(self, category_id: int, keywords: dict) -> None:
        self.execute("UPDATE categories SET keywords = %s, updated_at = %s WHERE id = %s",
                     (json.dumps(keywords, ensure_ascii=False), _now(), category_id))

    def category_followers(self) -> dict[int, int]:
        rows = self.execute("SELECT category_id, COUNT(*) AS n FROM user_interests WHERE is_enabled"
                            " GROUP BY category_id").fetchall()
        return {r["category_id"]: r["n"] for r in rows}

    # ---------- interests ----------
    def interests(self, user_id: int) -> dict[int, bool]:
        """{category_id: enabled} for a user's topics (muted ones have False). Inactive categories left out."""
        rows = self.execute(
            """SELECT i.category_id, i.is_enabled FROM user_interests i
               JOIN categories c ON c.id = i.category_id AND c.is_active
               WHERE i.user_id = %s""", (user_id,)).fetchall()
        return {r["category_id"]: bool(r["is_enabled"]) for r in rows}

    def set_interests(self, user_id: int, category_ids: set[int]) -> None:
        """Make the user's topics exactly these. Kept topics keep their mute state and start date."""
        valid = {c.id for c in self.categories()}
        wanted = set(category_ids) & valid
        now = _now()
        current = set(self.interests(user_id))
        with self.transaction():
            for cid in current - wanted:
                self.execute("DELETE FROM user_interests WHERE user_id = %s AND category_id = %s", (user_id, cid))
            for cid in wanted - current:
                self.execute(
                    """INSERT INTO user_interests (user_id, category_id, is_enabled, created_at, updated_at)
                       VALUES (%s, %s, TRUE, %s, %s) ON CONFLICT DO NOTHING""", (user_id, cid, now, now))

    def set_interest_enabled(self, user_id: int, category_id: int, enabled: bool) -> bool:
        """Mute / unmute a topic. Returns False if the user doesn't follow it."""
        cur = self.execute(
            "UPDATE user_interests SET is_enabled = %s, updated_at = %s WHERE user_id = %s AND category_id = %s",
            (bool(enabled), _now(), user_id, category_id))
        return cur.rowcount > 0

    # ---------- providers ----------
    def providers(self, enabled_only: bool = False) -> list[Provider]:
        where = "WHERE is_enabled" if enabled_only else ""
        rows = self.execute(f"SELECT * FROM providers {where} ORDER BY name, key").fetchall()
        return [_row(Provider, r) for r in rows]

    def get_provider(self, provider_id: int) -> Provider | None:
        row = self.execute("SELECT * FROM providers WHERE id = %s", (provider_id,)).fetchone()
        return _row(Provider, row) if row else None

    def due_providers(self, now: datetime) -> list[Provider]:
        rows = self.execute(
            "SELECT * FROM providers WHERE is_enabled AND (next_fetch_at IS NULL OR next_fetch_at <= %s)"
            " ORDER BY next_fetch_at NULLS FIRST", (iso(now),)).fetchall()
        return [_row(Provider, r) for r in rows]

    PROVIDER_FIELDS = {"is_enabled", "polling_interval_seconds", "timeout_seconds", "categories", "notes",
                       "next_fetch_at", "name", "language"}

    def update_provider(self, provider_id: int, **values) -> None:
        unknown = set(values) - self.PROVIDER_FIELDS
        if unknown:
            raise ValueError(f"Unknown provider fields: {unknown}")
        if "categories" in values:
            values["categories"] = json.dumps(values["categories"])
        sets = ", ".join(f"{k} = %s" for k in values)  # keys checked against PROVIDER_FIELDS above
        self.execute(f"UPDATE providers SET {sets}, updated_at = %s WHERE id = %s",
                     list(values.values()) + [_now(), provider_id])

    def record_fetch(self, provider_id: int, ok: bool, error: str | None, next_fetch_at: datetime,
                     etag: str | None = None, last_modified: str | None = None, new_articles: int = 0) -> None:
        now = _now()
        if ok:
            self.execute(
                """UPDATE providers SET last_fetched_at = %s, last_success_at = %s, last_error = NULL,
                       consecutive_failures = 0, next_fetch_at = %s, etag = COALESCE(%s, etag),
                       last_modified = COALESCE(%s, last_modified), articles_total = articles_total + %s
                   WHERE id = %s""",
                (now, now, iso(next_fetch_at), etag, last_modified, new_articles, provider_id))
        else:
            self.execute(
                """UPDATE providers SET last_fetched_at = %s, last_error = %s,
                       consecutive_failures = consecutive_failures + 1, next_fetch_at = %s
                   WHERE id = %s""", (now, (error or "")[:500], iso(next_fetch_at), provider_id))

    # ---------- articles ----------
    def article_by_external_id(self, provider_id: int, external_id: str) -> Article | None:
        row = self.execute("SELECT * FROM articles WHERE provider_id = %s AND external_id = %s",
                           (provider_id, external_id)).fetchone()
        return _row(Article, row) if row else None

    def article_by_url(self, url: str) -> Article | None:
        row = self.execute("SELECT * FROM articles WHERE canonical_url = %s", (url,)).fetchone()
        return _row(Article, row) if row else None

    def get_article(self, article_id: int) -> Article | None:
        row = self.execute("SELECT * FROM articles WHERE id = %s", (article_id,)).fetchone()
        return _row(Article, row) if row else None

    def recent_titles(self, since: datetime) -> list[tuple[int, str, str, int]]:
        """(id, normalized_title, title_hash, cluster_id) of articles fetched since a time."""
        rows = self.execute(
            "SELECT id, normalized_title, title_hash, COALESCE(story_cluster_id, id) AS cluster FROM articles"
            " WHERE fetched_at >= %s ORDER BY id DESC LIMIT 5000", (iso(since),)).fetchall()
        return [(r["id"], r["normalized_title"], r["title_hash"], r["cluster"]) for r in rows]

    def insert_article(self, provider_id: int, a: dict) -> int | None:
        """Store a new article. Returns its id, or None if it's already stored (same feed id or URL)."""
        row = self.execute(
            """INSERT INTO articles (provider_id, external_id, title, normalized_title, title_hash, summary,
                   canonical_url, image_url, author, language, published_at, fetched_at, content_hash,
                   story_cluster_id, is_backlog, tags, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT DO NOTHING RETURNING id""",
            (provider_id, a.get("external_id"), a["title"], a["normalized_title"], a["title_hash"], a.get("summary"),
             a["canonical_url"], a.get("image_url"), a.get("author"), a["language"], a.get("published_at"),
             a["fetched_at"], a["content_hash"], a.get("story_cluster_id"), bool(a.get("is_backlog", False)),
             json.dumps(a.get("tags", []), ensure_ascii=False), _now())).fetchone()
        return row["id"] if row else None

    def recent_articles(self, limit: int = 50, offset: int = 0, low_confidence: bool = False,
                        provider_id: int | None = None) -> list[dict]:
        """Newest articles with their categories, for the admin page. low_confidence: articles with no
        category at or above the threshold."""
        where, args = [], []
        if provider_id:
            where.append("a.provider_id = %s"); args.append(provider_id)
        if low_confidence:
            where.append("NOT EXISTS (SELECT 1 FROM article_categories ac WHERE ac.article_id = a.id"
                         " AND ac.confidence >= %s)"); args.append(config.CLASSIFY_MIN_CONFIDENCE)
        sql = ("SELECT a.*, p.name AS provider_name FROM articles a JOIN providers p ON p.id = a.provider_id"
               + (" WHERE " + " AND ".join(where) if where else "")
               + " ORDER BY a.id DESC LIMIT %s OFFSET %s")
        rows = self.execute(sql, args + [limit, offset]).fetchall()
        return [{"article": _row(Article, r), "provider_name": r["provider_name"],
                 "categories": self.article_categories(r["id"])} for r in rows]

    def count_articles(self) -> int:
        return self._one("SELECT COUNT(*) FROM articles")

    # ---------- article categories ----------
    def article_categories(self, article_id: int) -> list[dict]:
        rows = self.execute(
            """SELECT ac.*, c.key, c.emoji, c.name_en FROM article_categories ac
               JOIN categories c ON c.id = ac.category_id WHERE ac.article_id = %s ORDER BY ac.confidence DESC""",
            (article_id,)).fetchall()
        return [plain(r) for r in rows]

    def add_article_categories(self, article_id: int, results: list[tuple[int, float, str, str]]) -> None:
        """(category_id, confidence, method, reason). Keeps the higher confidence; manual rows are never
        overwritten by automatic ones."""
        now = _now()
        with self.transaction():
            for category_id, confidence, method, reason in results:
                self.execute(
                    """INSERT INTO article_categories (article_id, category_id, confidence, method, reason, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s)
                       ON CONFLICT (article_id, category_id) DO UPDATE SET
                           confidence = excluded.confidence, method = excluded.method, reason = excluded.reason
                       WHERE article_categories.method <> 'manual'
                         AND excluded.confidence > article_categories.confidence""",
                    (article_id, category_id, round(confidence, 3), method, reason, now))

    def set_manual_categories(self, article_id: int, category_ids: set[int]) -> None:
        """An admin's correction: exactly these categories, at full confidence."""
        now = _now()
        with self.transaction():
            self.execute("DELETE FROM article_categories WHERE article_id = %s", (article_id,))
            for cid in category_ids:
                self.execute(
                    "INSERT INTO article_categories VALUES (%s, %s, 1.0, 'manual', 'set by an admin', %s)",
                    (article_id, cid, now))

    # ---------- matching ----------
    def matching_users(self, article: Article) -> list[tuple[int, int]]:
        """(user_id, category_id) for every user who should get this article: active, not paused, onboarded,
        following one of its confident categories (not muted) since before the article was found, and reading
        its language. The category is the article's most confident one among the user's topics."""
        rows = self.execute(
            """
            SELECT i.user_id, ac.category_id, ac.confidence
            FROM article_categories ac
            JOIN categories c ON c.id = ac.category_id AND c.is_active
            JOIN user_interests i ON i.category_id = ac.category_id AND i.is_enabled
            JOIN users u ON u.telegram_id = i.user_id
            WHERE ac.article_id = %s AND ac.confidence >= %s
              AND u.status = 'active' AND u.notifications_enabled AND u.onboarding_completed
              AND (u.news_language = 'any' OR u.news_language = %s)
              AND i.created_at <= %s
            ORDER BY ac.confidence DESC, i.user_id
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
                today = self._one(
                    "SELECT COUNT(*) FROM notifications WHERE user_id = %s AND status IN ('queued', 'sent')"
                    " AND created_at >= %s", (user_id, since))
                if today >= user.max_per_day:
                    status, error = "skipped", "daily limit reached"
            cur = self.execute(
                """INSERT INTO notifications (user_id, article_id, cluster_id, category_id, status,
                       next_attempt_at, last_error, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                (user_id, article.id, article.cluster_id, category_id, status, now, error, now, now))
            queued += cur.rowcount if status != "skipped" else 0
        return queued

    # ---------- notifications ----------
    def due_notifications(self, limit: int = 30) -> list[Notification]:
        rows = self.execute(
            "SELECT * FROM notifications WHERE status = 'queued' AND next_attempt_at <= %s ORDER BY id LIMIT %s",
            (_now(), limit)).fetchall()
        return [_row(Notification, r) for r in rows]

    def get_notification(self, notification_id: int) -> Notification | None:
        row = self.execute("SELECT * FROM notifications WHERE id = %s", (notification_id,)).fetchone()
        return _row(Notification, row) if row else None

    def mark_notification(self, notification_id: int, status: str, error: str | None = None,
                          message_id: int | None = None, retry_at: datetime | None = None,
                          count_attempt: bool = True) -> None:
        now = _now()
        self.execute(
            """UPDATE notifications SET status = %s, last_error = %s,
                   telegram_message_id = COALESCE(%s, telegram_message_id),
                   attempt_count = attempt_count + %s, next_attempt_at = COALESCE(%s, next_attempt_at),
                   sent_at = CASE WHEN %s = 'sent' THEN %s::timestamptz ELSE sent_at END, updated_at = %s
               WHERE id = %s""",
            (status, error[:500] if error else None, message_id, int(count_attempt), iso(retry_at),
             status, now, now, notification_id))

    def skip_queued_for_user(self, user_id: int, reason: str) -> int:
        cur = self.execute(
            "UPDATE notifications SET status = 'skipped', last_error = %s, updated_at = %s"
            " WHERE user_id = %s AND status IN ('queued', 'digest')", (reason, _now(), user_id))
        return cur.rowcount

    def set_delivery_mode(self, user_id: int, mode: str) -> User:
        """Instant -> digest: waiting alerts are held for the digest. Digest -> instant: the held ones are
        dropped rather than sent as a burst."""
        now = _now()
        with self.transaction():
            if mode == "digest":
                self.execute("UPDATE notifications SET status = 'digest', updated_at = %s"
                             " WHERE user_id = %s AND status = 'queued'", (now, user_id))
            else:
                self.execute("UPDATE notifications SET status = 'skipped', last_error = 'switched to instant',"
                             " updated_at = %s WHERE user_id = %s AND status = 'digest'", (now, user_id))
            self.execute("UPDATE users SET delivery_mode = %s, updated_at = %s WHERE telegram_id = %s",
                         (mode, now, user_id))
        return self.get_user(user_id)

    def digest_users_due(self, local_hour: int, local_date: str) -> list[User]:
        rows = self.execute(
            """SELECT * FROM users WHERE delivery_mode = 'digest' AND status = 'active' AND notifications_enabled
                   AND digest_hour = %s AND (last_digest_date IS NULL OR last_digest_date <> %s)""",
            (local_hour, local_date)).fetchall()
        return [_row(User, r) for r in rows]

    def digest_items(self, user_id: int) -> list[tuple[Notification, Article]]:
        rows = self.execute(
            """SELECT n.id AS n_id, a.* FROM notifications n JOIN articles a ON a.id = n.article_id
               WHERE n.user_id = %s AND n.status = 'digest' ORDER BY a.id DESC""", (user_id,)).fetchall()
        return [(self.get_notification(r["n_id"]), _row(Article, r)) for r in rows]

    def failed_notifications(self, limit: int = 100) -> list[dict]:
        rows = self.execute(
            """SELECT n.*, a.title, u.first_name, u.username FROM notifications n
               JOIN articles a ON a.id = n.article_id JOIN users u ON u.telegram_id = n.user_id
               WHERE n.status = 'failed' ORDER BY n.updated_at DESC LIMIT %s""", (limit,)).fetchall()
        return [plain(r) for r in rows]

    def retry_notification(self, notification_id: int) -> bool:
        cur = self.execute(
            "UPDATE notifications SET status = 'queued', attempt_count = 0, next_attempt_at = %s, updated_at = %s"
            " WHERE id = %s AND status = 'failed'", (_now(), _now(), notification_id))
        return cur.rowcount > 0

    # ---------- stats ----------
    def stats(self) -> dict:
        since = day_start_utc(local_today())
        one = self._one
        return {
            "users": one("SELECT COUNT(*) FROM users"),
            "active_users": one("SELECT COUNT(*) FROM users WHERE status = 'active' AND notifications_enabled"
                                " AND onboarding_completed"),
            "paused_users": one("SELECT COUNT(*) FROM users WHERE NOT notifications_enabled"),
            "blocked_users": one("SELECT COUNT(*) FROM users WHERE status = 'blocked'"),
            "providers_enabled": one("SELECT COUNT(*) FROM providers WHERE is_enabled"),
            "providers_failing": one("SELECT COUNT(*) FROM providers WHERE is_enabled AND consecutive_failures > 0"),
            "articles_today": one("SELECT COUNT(*) FROM articles WHERE fetched_at >= %s", (since,)),
            "articles_total": one("SELECT COUNT(*) FROM articles"),
            "duplicates_today": one("SELECT COUNT(*) FROM articles WHERE fetched_at >= %s"
                                    " AND story_cluster_id IS NOT NULL", (since,)),
            "low_confidence_today": one(
                "SELECT COUNT(*) FROM articles a WHERE a.fetched_at >= %s AND NOT EXISTS (SELECT 1 FROM"
                " article_categories ac WHERE ac.article_id = a.id AND ac.confidence >= %s)",
                (since, config.CLASSIFY_MIN_CONFIDENCE)),
            "queued": one("SELECT COUNT(*) FROM notifications WHERE status = 'queued'"),
            "queue_oldest": one("SELECT MIN(next_attempt_at) FROM notifications WHERE status = 'queued'"),
            "held_for_digest": one("SELECT COUNT(*) FROM notifications WHERE status = 'digest'"),
            "sent_today": one("SELECT COUNT(*) FROM notifications WHERE status = 'sent' AND sent_at >= %s", (since,)),
            "failed_today": one("SELECT COUNT(*) FROM notifications WHERE status = 'failed' AND updated_at >= %s",
                                (since,)),
            "skipped_today": one("SELECT COUNT(*) FROM notifications WHERE status = 'skipped' AND updated_at >= %s",
                                 (since,)),
        }

    # ---------- retention ----------
    def delete_old_articles(self, older_than: datetime) -> int:
        return self.execute("DELETE FROM articles WHERE fetched_at < %s", (iso(older_than),)).rowcount

    # ---------- settings ----------
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.execute("SELECT value FROM settings WHERE key = %s", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.execute("INSERT INTO settings (key, value) VALUES (%s, %s)"
                     " ON CONFLICT (key) DO UPDATE SET value = excluded.value", (key, value))

    # ---------- backup ----------
    BACKUP_TABLES = ("categories", "providers", "users", "user_interests", "articles", "article_categories",
                     "notifications", "settings", "schema_migrations")

    def export_table_csv(self, table: str) -> bytes:
        """One table as CSV with a header (restorable with COPY … FROM … CSV HEADER)."""
        if table not in self.BACKUP_TABLES:
            raise ValueError(table)
        out = bytearray()
        with self.conn.cursor() as cur:
            with cur.copy(f"COPY {table} TO STDOUT WITH (FORMAT csv, HEADER true)") as copy:
                for chunk in copy:
                    out += chunk
        return bytes(out)


def load_seed() -> tuple[list[dict], list[dict]]:
    base = Path(config.SEED_DIR)
    if not base.is_absolute():
        base = Path(__file__).parent / base
    categories = json.loads((base / "categories.json").read_text(encoding="utf-8"))["categories"]
    providers = json.loads((base / "providers.json").read_text(encoding="utf-8"))["providers"]
    return categories, providers


db = Database()

if __name__ == "__main__":
    # Migrate without starting the bot: python db.py
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    done = db.migrate()
    print(f"Applied {', '.join(done)}." if done else "Nothing to apply.", "Schema version:", db.schema_version())
