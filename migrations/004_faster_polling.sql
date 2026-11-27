-- Check feeds more often so stories arrive sooner (conditional GET keeps unchanged feeds cheap).
-- Only providers still on their original interval change; an interval an admin set is kept.
UPDATE providers p SET polling_interval_seconds = v.new_interval, next_fetch_at = NULL
FROM (VALUES
    ('bbc_world', 600, 180), ('bbc_africa', 600, 180), ('bbc_technology', 900, 180),
    ('bbc_business', 900, 180), ('bbc_politics', 900, 180), ('bbc_science', 1800, 180),
    ('bbc_health', 1800, 180), ('bbc_entertainment', 1800, 180), ('bbc_sport', 600, 180),
    ('bbc_amharic', 900, 180), ('aljazeera', 600, 180), ('thehackernews', 900, 180),
    ('bleepingcomputer', 900, 180), ('reporter_et', 1800, 180), ('addis_standard', 1800, 180),
    ('ars_technica', 1800, 180), ('espn', 1800, 180), ('un_news', 1800, 300), ('cisa', 1800, 300),
    ('krebs', 3600, 600), ('who', 3600, 600), ('nasa', 3600, 600)
) AS v(key, old_interval, new_interval)
WHERE p.key = v.key AND p.polling_interval_seconds = v.old_interval;
