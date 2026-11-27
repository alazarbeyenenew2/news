-- News sources a user switched off. Everything not listed is on, so sources added later reach
-- everyone by default.
CREATE TABLE user_source_exclusions (
    user_id      BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    provider_id  BIGINT NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (user_id, provider_id)
);
CREATE INDEX idx_source_exclusions_provider ON user_source_exclusions(provider_id);
