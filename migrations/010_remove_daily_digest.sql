-- The daily digest is gone: every alert is instant. Digest users switch to instant alerts. Stories that
-- were being held for their next digest are marked skipped rather than sent as a burst; they can still
-- get today's with 📥 Latest news. The digest columns stay (unused) so older backups still restore.
UPDATE notifications SET status = 'skipped', last_error = 'daily digest removed', updated_at = now()
 WHERE status = 'digest';
UPDATE users SET delivery_mode = 'instant', updated_at = now() WHERE delivery_mode = 'digest';
