-- ============================================================================
-- SchemaFirst Diagnostic SQL Pack: citizens
-- Target Database: Source Legacy Replica
-- Read-Only DBA Triage Queries
-- ============================================================================

-- Flaw: cp1252_encoding_ghosts [HIGH] on column "full_name"
-- Evidence: [cp1252_encoding_ghosts] 17/200 (8.5%) sampled rows contain UTF-8/CP1252 mojibake artifacts
-- Advice: Re-encode CP1252/Windows-1252 mojibake artifacts back to valid UTF-8 equivalents.
SELECT "full_name" FROM "citizens" WHERE "full_name" LIKE '%â%' OR "full_name" LIKE '%�%';

-- Flaw: impossible_or_sentinel_date [WARN] on column "dob_str"
-- Evidence: [impossible_or_sentinel_date] 8/200 (4.0%) sampled rows in column 'dob_str' contain sentinel or impossible dates
-- Advice: Map legacy sentinel dates (1900-01-01, 9999-12-31) to NULL and enforce ISO-8601 calendar validation.
SELECT "dob_str" FROM "citizens" WHERE "dob_str" IN ('1900-01-01', '1970-01-01', '9999-12-31', '0000-00-00');

-- Flaw: mixed_date_locales [CRITICAL] on column "dob_str"
-- Evidence: [mixed_date_locales] Column 'dob_str' mixes date locales across 36/200 (18.0%) sampled rows (16 DD/MM/YYYY, 20 MM/DD/YYYY)
-- Advice: Cannot auto-parse ambiguous dates (e.g. 04/05/1990). Quarantine ambiguous rows and enforce ISO-8601.
SELECT "dob_str" FROM "citizens" WHERE "dob_str" IS NOT NULL;

-- Flaw: postgres_null_byte_poison [CRITICAL] on column "legacy_notes"
-- Evidence: [postgres_null_byte_poison] 7/200 (3.5%) sampled rows contain ASCII NUL (0x00) bytes
-- Advice: Strip ASCII NUL (0x00) bytes using REPLACE to prevent PostgreSQL wire termination and AWS DMS CDC replication task crashes.
SELECT "legacy_notes" FROM "citizens" WHERE "legacy_notes" LIKE '%' || CHR(0) || '%';

-- Flaw: shadow_uk_nino [CRITICAL] on column "legacy_notes"
-- Evidence: [shadow_uk_nino] 1/200 (0.5%) sampled rows in column 'legacy_notes' match UK National Insurance Number pattern
-- Advice: UK National Insurance Number detected in general text. Quarantine or tokenize under UK GDPR.
SELECT "legacy_notes" FROM "citizens" WHERE "legacy_notes" ~* '\y[A-Z]{2}\s?[0-9]{2}\s?[0-9]{2}\s?[0-9]{2}\s?[A-D]\y';

-- Flaw: shadow_nhs_number [CRITICAL] on column "legacy_notes"
-- Evidence: [shadow_nhs_number] 1/200 (0.5%) sampled rows in column 'legacy_notes' match Modulus-11 verified UK NHS Number pattern
-- Advice: UK NHS Number detected in general text. Quarantine under UK Data Protection Act.
SELECT "legacy_notes" FROM "citizens" WHERE "legacy_notes" ~ '\y[0-9]{3}\s?[0-9]{3}\s?[0-9]{4}\y';

-- Flaw: legacy_redaction_markers [WARN] on column "legacy_notes"
-- Evidence: [legacy_redaction_markers] 1/200 (0.5%) sampled rows in column 'legacy_notes' contain legacy redaction or deletion tombstones
-- Advice: Clean legacy manual redaction and GDPR tombstone markers.
SELECT "legacy_notes" FROM "citizens" WHERE "legacy_notes" ~* '\y(REDACTED|GDPR_DELETED|DECEASED|DO NOT CONTACT|TEST TEST|XXXX+)\y';
