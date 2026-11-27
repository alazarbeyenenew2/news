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
from datetime import date, datetime, time, timedelta, timezone
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
    cve_alerts: bool = False
    cve_enabled_at: str | None = None
    cve_all_technologies: bool = False
    cve_min_severity: str = "HIGH"
    welcome_news_at: str | None = None

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
    matched: str | None = None  # CVE alerts: which of the user's technologies matched
    match_source: str | None = None  # CVE alerts: 'product' or 'description' 


# CVE severity, lowest to highest; a user's minimum is one of CVE_MIN_SEVERITIES.
SEVERITY_RANK = {None: 0, "NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
CVE_MIN_SEVERITIES = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "ALL": 0}


@dataclass
class Cve:
    article_id: int
    cve_id: str
    description: str
    cvss_score: float | None
    severity: str | None
    cvss_version: str | None
    products: list
    kev: bool
    kev_date: str | None
    kev_ransomware: bool
    created_at: str
    updated_at: str


@dataclass
class Technology:
    id: int
    key: str
    name: str
    grp: str
    aliases: list
    sort_order: int
    is_active: bool
    created_at: str


def plain(row: dict) -> dict:
    """A row with datetimes as UTC ISO strings and JSON text decoded, so the rest of the app sees
    the same values whatever the driver returns."""
    out = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            v = iso(v)
        elif isinstance(v, date):
            v = v.isoformat()
        elif k in ("keywords", "categories", "tags", "products", "aliases") and isinstance(v, str):
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
                   "delivery_mode", "digest_hour", "last_digest_date", "max_per_day", "news_language",
                   "cve_alerts", "cve_enabled_at", "cve_all_technologies", "cve_min_severity", "welcome_news_at"}

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

    def list_users(self, search: str = "", limit: int | None = None, offset: int = 0) -> tuple[list[dict], int]:
        """A page of users (newest first) with their topic count and alerts sent today, and how many users
        match the search in all (for the admin page)."""
        search = search.strip()
        like = f"%{search}%"
        rows = self.execute(
            """
            SELECT u.*,
                   (SELECT COUNT(*) FROM user_interests i WHERE i.user_id = u.telegram_id AND i.is_enabled)
                       AS topics,
                   (SELECT COUNT(*) FROM notifications n WHERE n.user_id = u.telegram_id AND n.status = 'sent'
                        AND n.sent_at >= %s) AS sent_today,
                   COUNT(*) OVER () AS total
            FROM users u
            WHERE %s = '' OR u.first_name ILIKE %s OR u.username ILIKE %s OR u.telegram_id::text LIKE %s
            ORDER BY u.created_at DESC, u.telegram_id
            LIMIT %s OFFSET %s
            """,
            (day_start_utc(local_today()), search, like, like, like, limit, max(offset, 0))).fetchall()
        total = rows[0]["total"] if rows else (self._one(
            """SELECT COUNT(*) FROM users u WHERE %s = '' OR u.first_name ILIKE %s OR u.username ILIKE %s
               OR u.telegram_id::text LIKE %s""", (search, like, like, like)) if offset else 0)
        return [{"user": _row(User, r), "topics": r["topics"], "sent_today": r["sent_today"]} for r in rows], total

    def delete_user(self, telegram_id: int) -> bool:
        """Remove a user and everything about them (topics, sources, CVE technologies, alert history:
        all ON DELETE CASCADE). They can start again with /start as a new user."""
        return self.execute("DELETE FROM users WHERE telegram_id = %s", (telegram_id,)).rowcount > 0

    def user_preferences(self, user_ids: list[int]) -> dict[int, dict]:
        """For the admin page: each user's topics (with mute state), switched-off sources and CVE
        technologies, in three queries for the whole list."""
        prefs = {uid: {"topics": [], "sources_off": [], "technologies": []} for uid in user_ids}
        if not user_ids:
            return prefs
        for r in self.execute(
                """SELECT i.user_id, c.emoji, c.name_en, i.is_enabled FROM user_interests i
                   JOIN categories c ON c.id = i.category_id WHERE i.user_id = ANY(%s) ORDER BY c.sort_order""",
                (user_ids,)):
            prefs[r["user_id"]]["topics"].append({"label": f"{r['emoji']} {r['name_en']}", "muted": not r["is_enabled"]})
        for r in self.execute(
                """SELECT x.user_id, p.name FROM user_source_exclusions x JOIN providers p ON p.id = x.provider_id
                   WHERE x.user_id = ANY(%s) ORDER BY p.name""", (user_ids,)):
            if r["name"] not in prefs[r["user_id"]]["sources_off"]:
                prefs[r["user_id"]]["sources_off"].append(r["name"])
        for r in self.execute(
                """SELECT ut.user_id, COALESCE(t.name, ut.term) AS name FROM user_technologies ut
                   LEFT JOIN technologies t ON t.id = ut.technology_id WHERE ut.user_id = ANY(%s)
                   ORDER BY ut.technology_id NULLS LAST, ut.created_at""", (user_ids,)):
            prefs[r["user_id"]]["technologies"].append(r["name"])
        return prefs

    def onboarding_idle_users(self, idle_since: str) -> list[User]:
        """Users who chose something during onboarding (a topic or CVE reports) but never finished it,
        and haven't touched the bot since idle_since: their choices count as saved."""
        rows = self.execute(
            """SELECT u.* FROM users u
               WHERE NOT u.onboarding_completed AND u.status = 'active' AND u.language IS NOT NULL
                 AND u.last_seen_at < %s
                 AND (u.cve_alerts OR EXISTS (SELECT 1 FROM user_interests i JOIN categories c ON c.id = i.category_id
                                              WHERE i.user_id = u.telegram_id AND c.is_active))""",
            (idle_since,)).fetchall()
        return [_row(User, r) for r in rows]

    def admin_ids(self) -> list[int]:
        rows = self.execute("SELECT telegram_id, role FROM users WHERE status = 'active'").fetchall()
        return [r["telegram_id"] for r in rows
                if r["role"] == "admin" or r["telegram_id"] in config.SUPER_ADMIN_IDS]

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

    # ---------- CVE reports ----------
    def upsert_cve(self, article_id: int, data: dict) -> None:
        """Store or merge a CVE's details. Merging keeps what's known: a severity found later, and the
        CISA "actively exploited" flag once it's set."""
        now = _now()
        self.execute(
            """INSERT INTO cves (article_id, cve_id, description, cvss_score, severity, cvss_version, products,
                   kev, kev_date, kev_ransomware, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (article_id) DO UPDATE SET
                   cvss_score = COALESCE(excluded.cvss_score, cves.cvss_score),
                   severity = COALESCE(excluded.severity, cves.severity),
                   cvss_version = COALESCE(excluded.cvss_version, cves.cvss_version),
                   products = CASE WHEN jsonb_array_length(excluded.products) > 0 THEN excluded.products
                                   ELSE cves.products END,
                   kev = cves.kev OR excluded.kev,
                   kev_date = COALESCE(cves.kev_date, excluded.kev_date),
                   kev_ransomware = cves.kev_ransomware OR excluded.kev_ransomware,
                   updated_at = excluded.updated_at""",
            (article_id, data["cve_id"], data.get("description") or "", data.get("cvss_score"), data.get("severity"),
             data.get("cvss_version"), json.dumps(data.get("products") or []), bool(data.get("kev")),
             data.get("kev_date"), bool(data.get("kev_ransomware")), now, now))

    def get_cve(self, article_id: int) -> Cve | None:
        row = self.execute("SELECT * FROM cves WHERE article_id = %s", (article_id,)).fetchone()
        return _row(Cve, row) if row else None

    def cve_matching_users(self, article: Article, cve: Cve) -> list[tuple[int, None, str, str | None]]:
        """(user_id, None, matched technology, match source) for everyone who should get this CVE: CVE
        reports on, account active, alerts not paused, severity at or above their minimum (or actively
        exploited), and one of their technologies (added before the CVE was found) in it — or they chose
        all technologies. Once the CVE has product data (from NIST's analysis or CISA), only the products
        count ('product'): that's precise. Before that, the description is used ('description')."""
        from news import cve_term_matches  # avoid a circular import at load time
        rank = SEVERITY_RANK.get(cve.severity, 0)
        users = self.execute(
            """SELECT telegram_id, cve_all_technologies, cve_min_severity, cve_enabled_at FROM users
               WHERE cve_alerts AND status = 'active' AND notifications_enabled AND cve_enabled_at <= %s""",
            (article.fetched_at,)).fetchall()
        if not users:
            return []
        terms = self.execute(
            """SELECT ut.user_id, ut.term, ut.created_at, t.name, t.aliases FROM user_technologies ut
               LEFT JOIN technologies t ON t.id = ut.technology_id
               WHERE ut.user_id = ANY(%s) AND ut.created_at <= %s AND (t.id IS NULL OR t.is_active)""",
            ([u["telegram_id"] for u in users], article.fetched_at)).fetchall()
        by_user: dict[int, list] = {}
        for t in terms:
            aliases = plain(t)["aliases"] if t["name"] else [t["term"]]
            by_user.setdefault(t["user_id"], []).append((t["name"] or t["term"], aliases))
        if cve.products:
            text, source = " | ".join(cve.products), "product"
        else:
            text, source = " ".join([article.title, cve.description]), "description"
        matches = []
        for u in users:
            if not cve.kev and rank < CVE_MIN_SEVERITIES.get(u["cve_min_severity"], 3):
                continue
            hit = next((name for name, aliases in by_user.get(u["telegram_id"], [])
                        if cve_term_matches(text, aliases)), None)
            if hit is not None:
                matches.append((u["telegram_id"], None, hit, source))
            elif u["cve_all_technologies"]:
                matches.append((u["telegram_id"], None, "*", None))
        return matches

    def seed_technologies(self, technologies: list[dict]) -> int:
        """Add catalog technologies whose key isn't in the database yet. Returns how many were added."""
        now, added = _now(), 0
        for i, t in enumerate(technologies):
            cur = self.execute(
                """INSERT INTO technologies (key, name, grp, aliases, sort_order, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (key) DO NOTHING""",
                (t["key"], t["name"], t["group"], json.dumps(t.get("aliases") or [t["name"].lower()]), i, now))
            added += cur.rowcount
        return added

    def technologies(self) -> list[Technology]:
        rows = self.execute("SELECT * FROM technologies WHERE is_active ORDER BY sort_order, id").fetchall()
        return [_row(Technology, r) for r in rows]

    def user_technologies(self, user_id: int) -> list[dict]:
        """The user's technologies: {term, technology_id, name} (name is the catalog name or the word)."""
        rows = self.execute(
            """SELECT ut.term, ut.technology_id, COALESCE(t.name, ut.term) AS name FROM user_technologies ut
               LEFT JOIN technologies t ON t.id = ut.technology_id
               WHERE ut.user_id = %s ORDER BY ut.technology_id NULLS LAST, ut.created_at""", (user_id,)).fetchall()
        return [dict(r) for r in rows]

    def add_user_technology(self, user_id: int, term: str, technology_id: int | None = None) -> bool:
        cur = self.execute(
            "INSERT INTO user_technologies VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (user_id, term, technology_id, _now()))
        return cur.rowcount > 0

    def remove_user_technology(self, user_id: int, term: str) -> bool:
        cur = self.execute("DELETE FROM user_technologies WHERE user_id = %s AND term = %s", (user_id, term))
        return cur.rowcount > 0

    def clear_user_technologies(self, user_id: int) -> None:
        self.execute("DELETE FROM user_technologies WHERE user_id = %s", (user_id,))

    def recent_cves(self, user: User, limit: int = 200) -> list[dict]:
        """The user's CVE alerts (sent, pulled or waiting), newest first, for the CSV report."""
        rows = self.execute(
            """SELECT c.*, a.title, a.canonical_url, a.published_at, a.fetched_at, n.matched, n.match_source, n.status
               FROM notifications n JOIN cves c ON c.article_id = n.article_id JOIN articles a ON a.id = n.article_id
               WHERE n.user_id = %s ORDER BY a.id DESC LIMIT %s""", (user.telegram_id, limit)).fetchall()
        return [plain(r) for r in rows]

    # ---------- the user's sources ----------
    def source_exclusions(self, user_id: int) -> set[int]:
        """Provider ids the user switched off."""
        rows = self.execute("SELECT provider_id FROM user_source_exclusions WHERE user_id = %s", (user_id,))
        return {r["provider_id"] for r in rows}

    def set_source_exclusions(self, user_id: int, provider_ids: set[int]) -> None:
        """Make the user's switched-off sources exactly these (unknown ids are ignored)."""
        valid = {p.id for p in self.providers()}
        now = _now()
        with self.transaction():
            self.execute("DELETE FROM user_source_exclusions WHERE user_id = %s", (user_id,))
            for pid in set(provider_ids) & valid:
                self.execute("INSERT INTO user_source_exclusions VALUES (%s, %s, %s)", (user_id, pid, now))

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
        if low_confidence:  # CVEs have no topics on purpose, so they're left out
            where.append("NOT EXISTS (SELECT 1 FROM article_categories ac WHERE ac.article_id = a.id"
                         " AND ac.confidence >= %s) AND NOT EXISTS (SELECT 1 FROM cves c WHERE c.article_id = a.id)")
            args.append(config.CLASSIFY_MIN_CONFIDENCE)
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
            JOIN articles a ON a.id = ac.article_id
            WHERE ac.article_id = %s AND ac.confidence >= %s
              AND NOT EXISTS (SELECT 1 FROM user_source_exclusions x
                              WHERE x.user_id = i.user_id AND x.provider_id = a.provider_id)
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

    def queue_notifications(self, article: Article, matches: list[tuple]) -> int:
        """Queue one notification per (user, category[, matched technology[, match source]]). Users
        over their daily limit get it marked skipped. Unique constraints make this safe to repeat
        (same article, or another article of the same story). Returns how many were newly queued."""
        if not matches:
            return 0
        now = _now()
        since = day_start_utc(local_today())
        queued = 0
        for match in matches:
            user_id, category_id = match[0], match[1]
            matched = match[2] if len(match) > 2 else None
            source = match[3] if len(match) > 3 else None
            user = self.get_user(user_id)
            if not user:
                continue
            status = "queued"
            error = None
            if status == "queued" and user.max_per_day:
                today = self._one(
                    "SELECT COUNT(*) FROM notifications WHERE user_id = %s AND status IN ('queued', 'sent')"
                    " AND created_at >= %s", (user_id, since))
                if today >= user.max_per_day:
                    status, error = "skipped", "daily limit reached"
            cur = self.execute(
                """INSERT INTO notifications (user_id, article_id, cluster_id, category_id, status,
                       next_attempt_at, last_error, matched, match_source, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                (user_id, article.id, article.cluster_id, category_id, status, now, error, matched, source,
                 now, now))
            queued += cur.rowcount if status != "skipped" else 0
        return queued

    # ---------- pulling today's news on demand ----------
    def pull_candidates(self, user: User, since: str, limit: int,
                        any_topic: bool = False) -> tuple[list[tuple[Article, int]], int]:
        """Stories from `since` (start of today) for the user's topics (confident, not muted, topic on) in
        their news language that they haven't received yet, newest first: one article per story.
        Unlike alerts, this includes stories found while they were paused, over their daily limit, held
        for their digest, or found before they chose the topic. any_topic: stories from every topic except
        ones the user muted (to fill up the welcome batch). Returns ([(article, category_id)], total)."""
        rows = self.execute(
            """
            WITH eligible AS (
                SELECT DISTINCT ON (COALESCE(a.story_cluster_id, a.id))
                       a.*, ac.category_id AS pull_category_id
                FROM articles a
                JOIN article_categories ac ON ac.article_id = a.id AND ac.confidence >= %(threshold)s
                JOIN categories c ON c.id = ac.category_id AND c.is_active
                LEFT JOIN user_interests i ON i.category_id = ac.category_id AND i.user_id = %(user)s
                WHERE (i.is_enabled OR (%(any_topic)s AND i.user_id IS NULL))
                  AND COALESCE(a.published_at, a.fetched_at) >= %(since)s
                  AND (%(language)s = 'any' OR a.language = %(language)s)
                  AND NOT EXISTS (SELECT 1 FROM user_source_exclusions x
                                  WHERE x.user_id = %(user)s AND x.provider_id = a.provider_id)
                  -- Not already received, and not an alert the sender is about to deliver.
                  AND NOT EXISTS (SELECT 1 FROM notifications n
                                  WHERE n.user_id = %(user)s
                                    AND n.cluster_id = COALESCE(a.story_cluster_id, a.id)
                                    AND (n.status IN ('sent', 'pulled')
                                         OR (n.status = 'queued' AND n.next_attempt_at <= %(now)s)))
                ORDER BY COALESCE(a.story_cluster_id, a.id), ac.confidence DESC, a.id DESC
            )
            SELECT *, COUNT(*) OVER () AS total FROM eligible
            ORDER BY COALESCE(published_at, fetched_at) DESC, id DESC LIMIT %(limit)s
            """,
            {"threshold": config.CLASSIFY_MIN_CONFIDENCE, "user": user.telegram_id, "since": since, "now": _now(),
             "language": user.news_language, "limit": limit, "any_topic": any_topic}).fetchall()
        total = rows[0]["total"] if rows else 0
        return [(_row(Article, r), r["pull_category_id"]) for r in rows], total

    def claim_pulled(self, user_id: int, article: Article, category_id: int | None) -> bool:
        """Record that the user is getting this story now, before it's sent, so a second tap (or the
        sender) can't send it again. A held, skipped, failed or later-retry alert for the story becomes the
        pulled one; an alert the sender is due to send now is left to the sender. Returns False if the
        user already has the story (or is about to get it as an alert)."""
        now = _now()
        with self.transaction():
            cur = self.execute(
                """INSERT INTO notifications (user_id, article_id, cluster_id, category_id, status,
                       next_attempt_at, sent_at, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, 'pulled', %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                (user_id, article.id, article.cluster_id, category_id, now, now, now, now))
            if cur.rowcount:
                return True
            cur = self.execute(
                """UPDATE notifications SET status = 'pulled', sent_at = %s, last_error = NULL, updated_at = %s
                   WHERE user_id = %s AND cluster_id = %s AND status NOT IN ('sent', 'pulled')
                     AND NOT (status = 'queued' AND next_attempt_at <= %s)""",
                (now, now, user_id, article.cluster_id, now))
            return cur.rowcount > 0

    def unclaim_pulled(self, user_id: int, article: Article, error: str) -> None:
        """Sending a pulled story failed: mark it failed so the next tap can try again."""
        self.execute("UPDATE notifications SET status = 'failed', last_error = %s, updated_at = %s"
                     " WHERE user_id = %s AND cluster_id = %s AND status = 'pulled'",
                     (error[:500], _now(), user_id, article.cluster_id))

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
                " article_categories ac WHERE ac.article_id = a.id AND ac.confidence >= %s)"
                " AND NOT EXISTS (SELECT 1 FROM cves c WHERE c.article_id = a.id)",
                (since, config.CLASSIFY_MIN_CONFIDENCE)),
            "queued": one("SELECT COUNT(*) FROM notifications WHERE status = 'queued'"),
            "queue_oldest": one("SELECT MIN(next_attempt_at) FROM notifications WHERE status = 'queued'"),
            "sent_today": one("SELECT COUNT(*) FROM notifications WHERE status = 'sent' AND sent_at >= %s", (since,)),
            "pulled_today": one("SELECT COUNT(*) FROM notifications WHERE status = 'pulled' AND sent_at >= %s",
                                (since,)),
            "failed_today": one("SELECT COUNT(*) FROM notifications WHERE status = 'failed' AND updated_at >= %s",
                                (since,)),
            "cves_today": one("SELECT COUNT(*) FROM cves c JOIN articles a ON a.id = c.article_id"
                              " WHERE a.fetched_at >= %s", (since,)),
            "cve_users": one("SELECT COUNT(*) FROM users WHERE cve_alerts"),
            "skipped_today": one("SELECT COUNT(*) FROM notifications WHERE status = 'skipped' AND updated_at >= %s",
                                 (since,)),
        }

    def dashboard(self, days: int = 14) -> dict:
        """Numbers for the admin overview's charts, by local day (TIMEZONE)."""
        tz = config.TIMEZONE.key
        start_day = local_today() - timedelta(days=days - 1)
        since = day_start_utc(start_day)
        rows = self.execute(
            """
            WITH d AS (SELECT generate_series(%(start)s::date, %(today)s::date, interval '1 day')::date AS day),
            a AS (SELECT (a.fetched_at AT TIME ZONE %(tz)s)::date AS day,
                         COUNT(*) FILTER (WHERE c.article_id IS NULL) AS articles,
                         COUNT(*) FILTER (WHERE c.article_id IS NOT NULL) AS cves
                  FROM articles a LEFT JOIN cves c ON c.article_id = a.id
                  WHERE a.fetched_at >= %(since)s GROUP BY 1),
            n AS (SELECT (sent_at AT TIME ZONE %(tz)s)::date AS day, COUNT(*) AS sent
                  FROM notifications WHERE status IN ('sent', 'pulled') AND sent_at >= %(since)s GROUP BY 1)
            SELECT d.day, COALESCE(a.articles, 0) AS articles, COALESCE(a.cves, 0) AS cves,
                   COALESCE(n.sent, 0) AS sent
            FROM d LEFT JOIN a USING (day) LEFT JOIN n USING (day) ORDER BY d.day
            """, {"start": start_day.isoformat(), "today": local_today().isoformat(), "since": since, "tz": tz}).fetchall()
        activity = [{"day": r["day"].isoformat(), "label": r["day"].strftime("%d %b"), "weekday": r["day"].strftime("%a"),
                     "articles": r["articles"], "cves": r["cves"], "sent": r["sent"]} for r in rows]
        today = day_start_utc(local_today())
        topics = [dict(r) for r in self.execute(
            """SELECT c.emoji, c.name_en AS name, COUNT(DISTINCT ac.article_id) AS n
               FROM categories c LEFT JOIN article_categories ac ON ac.category_id = c.id AND ac.confidence >= %s
                    AND ac.article_id IN (SELECT id FROM articles WHERE fetched_at >= %s)
               WHERE c.is_active GROUP BY c.id ORDER BY n DESC, c.sort_order""",
            (config.CLASSIFY_MIN_CONFIDENCE, today)).fetchall()]
        severity = {r["severity"] or "NONE": r["n"] for r in self.execute(
            """SELECT c.severity, COUNT(*) AS n FROM cves c JOIN articles a ON a.id = c.article_id
               WHERE a.fetched_at >= %s GROUP BY c.severity""", (today,)).fetchall()}
        kev_today = self._one("SELECT COUNT(*) FROM cves c JOIN articles a ON a.id = c.article_id"
                              " WHERE a.fetched_at >= %s AND c.kev", (today,))
        followers = self._one("SELECT COUNT(DISTINCT user_id) FROM user_interests WHERE is_enabled")
        return {"activity": activity, "topics": topics, "severity": severity, "kev_today": kev_today,
                "followers": followers}

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
    BACKUP_TABLES = ("categories", "providers", "users", "user_interests", "user_source_exclusions", "technologies",
                     "user_technologies", "articles", "article_categories", "cves", "notifications", "settings",
                     "schema_migrations")

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


def load_technologies() -> tuple[dict, list[dict]]:
    """(groups {key: {en, am}}, technologies) from seed/technologies.json."""
    base = Path(config.SEED_DIR)
    if not base.is_absolute():
        base = Path(__file__).parent / base
    data = json.loads((base / "technologies.json").read_text(encoding="utf-8"))
    return data["groups"], data["technologies"]


db = Database()

if __name__ == "__main__":
    # Migrate without starting the bot: python db.py
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    done = db.migrate()
    print(f"Applied {', '.join(done)}." if done else "Nothing to apply.", "Schema version:", db.schema_version())
