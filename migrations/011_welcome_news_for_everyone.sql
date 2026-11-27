-- Everyone should have seen at least 10 stories. Users who have received fewer (counting alerts and
-- 📥 Latest news) get their welcome news (10 of the latest stories) once more: the bot's background loop
-- sends it to onboarded, active users whose welcome_news_at is empty, a few users a minute.
UPDATE users u SET welcome_news_at = NULL
 WHERE (SELECT COUNT(*) FROM notifications n
         WHERE n.user_id = u.telegram_id AND n.status IN ('sent', 'pulled')) < 10;
