-- Users can no longer pause alerts: everyone who had paused gets alerts again (from now on; nothing found
-- while they were paused is sent). Users disabled by an admin stay disabled.
UPDATE users SET notifications_enabled = TRUE, updated_at = now()
 WHERE NOT notifications_enabled AND status <> 'disabled';
