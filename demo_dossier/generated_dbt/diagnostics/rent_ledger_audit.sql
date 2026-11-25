-- ============================================================================
-- SchemaFirst Diagnostic SQL Pack: rent_ledger
-- Target Database: Source Legacy Replica
-- Read-Only DBA Triage Queries
-- ============================================================================

-- Flaw: text_currency [WARN] on column "balance_amt"
-- Evidence: [text_currency] 1000/1000 (100.0%) sampled rows contain formatted currency strings
-- Advice: Strip formatting currency symbols/suffixes and cast text values to NUMERIC(18,2).
SELECT "balance_amt" FROM "rent_ledger" WHERE "balance_amt" ~ '[$£€]' OR "balance_amt" ~* '(USD|EUR|GBP)';

-- Flaw: negative_unsigned_field [HIGH] on column "arrears_months"
-- Evidence: [negative_unsigned_field] 125/1000 (12.5%) sampled values in unsigned column 'arrears_months' are negative
-- Advice: Flag or nullify negative values in columns where unsigned positive quantities are required.
SELECT "arrears_months" FROM "rent_ledger" WHERE CAST("arrears_months" AS NUMERIC) < 0;
