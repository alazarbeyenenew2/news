-- How a CVE alert matched the user's technology: 'product' (NIST's product data, or CISA's vendor/product)
-- or 'description' (named in the description, before NIST has analysed the CVE).
ALTER TABLE notifications ADD COLUMN match_source TEXT;
