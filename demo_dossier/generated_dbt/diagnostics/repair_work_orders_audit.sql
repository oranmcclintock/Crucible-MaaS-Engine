-- ============================================================================
-- SchemaFirst Diagnostic SQL Pack: repair_work_orders
-- Target Database: Source Legacy Replica
-- Read-Only DBA Triage Queries
-- ============================================================================

-- Flaw: text_currency [WARN] on column "estimated_cost"
-- Evidence: [text_currency] 1000/1000 (100.0%) sampled rows contain formatted currency strings
-- Advice: Strip formatting currency symbols/suffixes and cast text values to NUMERIC(18,2).
SELECT "estimated_cost" FROM "repair_work_orders" WHERE "estimated_cost" ~ '[$£€]' OR "estimated_cost" ~* '(USD|EUR|GBP)';

-- Flaw: cp1252_encoding_ghosts [HIGH] on column "dispatch_notes"
-- Evidence: [cp1252_encoding_ghosts] 52/1000 (5.2%) sampled rows contain UTF-8/CP1252 mojibake artifacts
-- Advice: Re-encode CP1252/Windows-1252 mojibake artifacts back to valid UTF-8 equivalents.
SELECT "dispatch_notes" FROM "repair_work_orders" WHERE "dispatch_notes" LIKE '%â%' OR "dispatch_notes" LIKE '%�%';
