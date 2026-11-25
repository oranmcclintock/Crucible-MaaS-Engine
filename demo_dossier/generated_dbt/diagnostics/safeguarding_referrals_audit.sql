-- ============================================================================
-- SchemaFirst Diagnostic SQL Pack: safeguarding_referrals
-- Target Database: Source Legacy Replica
-- Read-Only DBA Triage Queries
-- ============================================================================

-- Flaw: mixed_date_locales [CRITICAL] on column "referral_date"
-- Evidence: [mixed_date_locales] Column 'referral_date' mixes date locales across 300/900 (33.3%) sampled rows (150 DD/MM/YYYY, 150 MM/DD/YYYY)
-- Advice: Cannot auto-parse ambiguous dates (e.g. 04/05/1990). Quarantine ambiguous rows and enforce ISO-8601.
SELECT "referral_date" FROM "safeguarding_referrals" WHERE "referral_date" IS NOT NULL;

-- Flaw: cp1252_encoding_ghosts [HIGH] on column "case_summary"
-- Evidence: [cp1252_encoding_ghosts] 128/900 (14.2%) sampled rows contain UTF-8/CP1252 mojibake artifacts
-- Advice: Re-encode CP1252/Windows-1252 mojibake artifacts back to valid UTF-8 equivalents.
SELECT "case_summary" FROM "safeguarding_referrals" WHERE "case_summary" LIKE '%â%' OR "case_summary" LIKE '%�%';

-- Flaw: shadow_nhs_number [CRITICAL] on column "case_summary"
-- Evidence: [shadow_nhs_number] 128/900 (14.2%) sampled rows in column 'case_summary' match Modulus-11 verified UK NHS Number pattern
-- Advice: UK NHS Number detected in general text. Quarantine under UK Data Protection Act.
SELECT "case_summary" FROM "safeguarding_referrals" WHERE "case_summary" ~ '\y[0-9]{3}\s?[0-9]{3}\s?[0-9]{4}\y';
