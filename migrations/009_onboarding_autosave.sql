-- When a user got the one-time batch of today's news after onboarding (manual or auto-saved), so it's
-- never sent twice.
ALTER TABLE users ADD COLUMN welcome_news_at TIMESTAMPTZ;
