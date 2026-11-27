-- Initial schema. Applied once; db.py records it in schema_migrations.
-- Times are UTC ISO-8601 strings.

CREATE TABLE users (
    telegram_id            INTEGER PRIMARY KEY,           -- stable; usernames can change or be missing
    chat_id                INTEGER NOT NULL,
    username               TEXT,
    first_name             TEXT,
    language               TEXT,                          -- bot language: 'am' / 'en'; NULL until chosen
    role                   TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
    status                 TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'blocked', 'disabled')),
    -- blocked: the user blocked the bot (Telegram says so). disabled: an admin turned them off.
    onboarding_completed   INTEGER NOT NULL DEFAULT 0,
    notifications_enabled  INTEGER NOT NULL DEFAULT 1,    -- 0 = paused
    delivery_mode          TEXT NOT NULL DEFAULT 'instant' CHECK (delivery_mode IN ('instant', 'digest')),
    digest_hour            INTEGER NOT NULL DEFAULT 8 CHECK (digest_hour BETWEEN 0 AND 23),
    last_digest_date       TEXT,                          -- local date the last digest went out
    max_per_day            INTEGER NOT NULL DEFAULT 30,   -- 0 = no limit
    news_language          TEXT NOT NULL DEFAULT 'any',   -- 'any' or a language code articles must match
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL,
    last_seen_at           TEXT NOT NULL
);

CREATE TABLE categories (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id    INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    key          TEXT NOT NULL UNIQUE,
    emoji        TEXT NOT NULL DEFAULT '📰',
    name_en      TEXT NOT NULL,
    name_am      TEXT NOT NULL,
    description  TEXT,
    keywords     TEXT NOT NULL DEFAULT '{}',              -- JSON {"phrase": weight 0..1}
    sort_order   INTEGER NOT NULL DEFAULT 0,
    is_active    INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE user_interests (
    user_id      INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    category_id  INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    is_enabled   INTEGER NOT NULL DEFAULT 1,              -- 0 = muted (kept, but no alerts)
    created_at   TEXT NOT NULL,                           -- only articles found after this are sent
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (user_id, category_id)
);
CREATE INDEX idx_interests_category ON user_interests(category_id);

CREATE TABLE providers (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    key                       TEXT NOT NULL UNIQUE,
    name                      TEXT NOT NULL,              -- attribution shown to users
    base_url                  TEXT NOT NULL,
    provider_type             TEXT NOT NULL DEFAULT 'rss' CHECK (provider_type IN ('rss', 'atom', 'api', 'html')),
    feed_url                  TEXT NOT NULL,
    adapter_key               TEXT NOT NULL DEFAULT 'rss',
    polling_interval_seconds  INTEGER NOT NULL DEFAULT 600,
    timeout_seconds           INTEGER NOT NULL DEFAULT 20,
    is_enabled                INTEGER NOT NULL DEFAULT 0,
    language                  TEXT NOT NULL DEFAULT 'en',
    categories                TEXT NOT NULL DEFAULT '{}', -- JSON {"category_key": confidence} for every item
    notes                     TEXT,                       -- licensing / access review notes
    etag                      TEXT,
    last_modified             TEXT,
    last_fetched_at           TEXT,
    last_success_at           TEXT,
    last_error                TEXT,
    consecutive_failures      INTEGER NOT NULL DEFAULT 0,
    next_fetch_at             TEXT,
    articles_total            INTEGER NOT NULL DEFAULT 0,
    created_at                TEXT NOT NULL,
    updated_at                TEXT NOT NULL
);

CREATE TABLE articles (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id       INTEGER NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    external_id       TEXT,
    title             TEXT NOT NULL,
    normalized_title  TEXT NOT NULL,
    title_hash        TEXT NOT NULL,
    summary           TEXT,
    canonical_url     TEXT NOT NULL UNIQUE,
    image_url         TEXT,
    author            TEXT,
    language          TEXT NOT NULL,
    published_at      TEXT,
    fetched_at        TEXT NOT NULL,
    content_hash      TEXT NOT NULL,
    story_cluster_id  INTEGER,                            -- id of the first article of the same story
    is_backlog        INTEGER NOT NULL DEFAULT 0,         -- too old when found: stored, never alerted
    tags              TEXT NOT NULL DEFAULT '[]',         -- provider tags, JSON list
    created_at        TEXT NOT NULL,
    UNIQUE (provider_id, external_id)
);
CREATE INDEX idx_articles_fetched ON articles(fetched_at);
CREATE INDEX idx_articles_title_hash ON articles(title_hash);
CREATE INDEX idx_articles_cluster ON articles(story_cluster_id);

CREATE TABLE article_categories (
    article_id   INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    category_id  INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    confidence   REAL NOT NULL,
    method       TEXT NOT NULL,                           -- provider / keywords / tags / manual / ai, plus version
    reason       TEXT,                                    -- why (matched keywords), for debugging
    created_at   TEXT NOT NULL,
    PRIMARY KEY (article_id, category_id)
);
CREATE INDEX idx_article_categories_category ON article_categories(category_id);

CREATE TABLE notifications (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id              INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    article_id           INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    cluster_id           INTEGER NOT NULL,                -- story cluster: one alert per story per user
    category_id          INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    status               TEXT NOT NULL CHECK (status IN ('queued', 'digest', 'sent', 'failed', 'skipped')),
    attempt_count        INTEGER NOT NULL DEFAULT 0,
    next_attempt_at      TEXT NOT NULL,
    telegram_message_id  INTEGER,
    last_error           TEXT,
    sent_at              TEXT,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    UNIQUE (user_id, article_id),
    UNIQUE (user_id, cluster_id)
);
CREATE INDEX idx_notifications_due ON notifications(status, next_attempt_at);
CREATE INDEX idx_notifications_user ON notifications(user_id, created_at);

-- Small key/value settings, e.g. whether the nightly backup is on.
CREATE TABLE settings (
    key    TEXT PRIMARY KEY,
    value  TEXT
);
