-- 'pulled': the user asked for the story with the 📥 Latest news button and got it.
-- Counts as delivered (never sent again), but not toward the daily alert limit.
ALTER TABLE notifications DROP CONSTRAINT notifications_status_check;
ALTER TABLE notifications ADD CONSTRAINT notifications_status_check
    CHECK (status IN ('queued', 'digest', 'sent', 'pulled', 'failed', 'skipped'));
