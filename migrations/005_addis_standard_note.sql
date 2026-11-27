-- Addis Standard sits behind Cloudflare, which blocks automated feed readers. Keep it off and say
-- why; Ethiopian sources that can be read are added from seed/providers.json at startup.
UPDATE providers SET is_enabled = FALSE,
    notes = 'Tested 2026-11-27: Cloudflare returns 403 to automated clients. Left disabled; do not work around it. '
         || 'Ask the publisher for access. Fana, Ethiopia Observer, Capital, Addis Fortune, Ethiopia Insight and '
         || 'allAfrica cover Ethiopia instead.'
WHERE key = 'addis_standard';
