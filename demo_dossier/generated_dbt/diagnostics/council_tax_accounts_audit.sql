-- ============================================================================
-- SchemaFirst Diagnostic SQL Pack: council_tax_accounts
-- Target Database: Source Legacy Replica
-- Read-Only DBA Triage Queries
-- ============================================================================

-- Flaw: text_currency [WARN] on column "annual_liability"
-- Evidence: [text_currency] 1000/1000 (100.0%) sampled rows contain formatted currency strings
-- Advice: Strip formatting currency symbols/suffixes and cast text values to NUMERIC(18,2).
SELECT "annual_liability" FROM "council_tax_accounts" WHERE "annual_liability" ~ '[$£€]' OR "annual_liability" ~* '(USD|EUR|GBP)';

-- Flaw: impossible_or_sentinel_date [WARN] on column "billing_start_date"
-- Evidence: [impossible_or_sentinel_date] 52/1000 (5.2%) sampled rows in column 'billing_start_date' contain sentinel or impossible dates
-- Advice: Map legacy sentinel dates (1900-01-01, 9999-12-31) to NULL and enforce ISO-8601 calendar validation.
SELECT "billing_start_date" FROM "council_tax_accounts" WHERE "billing_start_date" IN ('1900-01-01', '1970-01-01', '9999-12-31', '0000-00-00');

-- Flaw: mixed_date_locales [CRITICAL] on column "billing_start_date"
-- Evidence: [mixed_date_locales] Column 'billing_start_date' mixes date locales across 212/1000 (21.2%) sampled rows (106 DD/MM/YYYY, 106 MM/DD/YYYY)
-- Advice: Cannot auto-parse ambiguous dates (e.g. 04/05/1990). Quarantine ambiguous rows and enforce ISO-8601.
SELECT "billing_start_date" FROM "council_tax_accounts" WHERE "billing_start_date" IS NOT NULL;

-- ----------------------------------------------------------------------------
-- Referential Integrity & Orphan Key Findings
-- ----------------------------------------------------------------------------
-- Orphan Rows: 291 | Distinct Orphan Keys: 1 (Inferred: True)
-- Foreign Key: collector_code -> case_correspondence.handled_by_code
SELECT c."collector_code", COUNT(*) FROM "council_tax_accounts" c LEFT JOIN "case_correspondence" p ON c."collector_code" = p."handled_by_code" WHERE c."collector_code" IS NOT NULL AND p."handled_by_code" IS NULL GROUP BY 1;
