-- ============================================================================
-- SchemaFirst Diagnostic SQL Pack: payment_transactions
-- Target Database: Source Legacy Replica
-- Read-Only DBA Triage Queries
-- ============================================================================

-- Flaw: mixed_date_locales [CRITICAL] on column "payment_date"
-- Evidence: [mixed_date_locales] Column 'payment_date' mixes date locales across 250/1000 (25.0%) sampled rows (125 DD/MM/YYYY, 125 MM/DD/YYYY)
-- Advice: Cannot auto-parse ambiguous dates (e.g. 04/05/1990). Quarantine ambiguous rows and enforce ISO-8601.
SELECT "payment_date" FROM "payment_transactions" WHERE "payment_date" IS NOT NULL;

-- Flaw: text_currency [WARN] on column "amount_paid"
-- Evidence: [text_currency] 1000/1000 (100.0%) sampled rows contain formatted currency strings
-- Advice: Strip formatting currency symbols/suffixes and cast text values to NUMERIC(18,2).
SELECT "amount_paid" FROM "payment_transactions" WHERE "amount_paid" ~ '[$£€]' OR "amount_paid" ~* '(USD|EUR|GBP)';

-- Flaw: postgres_null_byte_poison [CRITICAL] on column "remittance_memo"
-- Evidence: [postgres_null_byte_poison] 34/1000 (3.4%) sampled rows contain ASCII NUL (0x00) bytes
-- Advice: Strip ASCII NUL (0x00) bytes using REPLACE to prevent PostgreSQL wire termination and AWS DMS CDC replication task crashes.
SELECT "remittance_memo" FROM "payment_transactions" WHERE "remittance_memo" LIKE '%' || CHR(0) || '%';
