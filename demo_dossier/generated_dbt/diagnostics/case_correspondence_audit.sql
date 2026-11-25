-- ============================================================================
-- SchemaFirst Diagnostic SQL Pack: case_correspondence
-- Target Database: Source Legacy Replica
-- Read-Only DBA Triage Queries
-- ============================================================================

-- Flaw: impossible_or_sentinel_date [WARN] on column "sent_date"
-- Evidence: [impossible_or_sentinel_date] 40/1000 (4.0%) sampled rows in column 'sent_date' contain sentinel or impossible dates
-- Advice: Map legacy sentinel dates (1900-01-01, 9999-12-31) to NULL and enforce ISO-8601 calendar validation.
SELECT "sent_date" FROM "case_correspondence" WHERE "sent_date" IN ('1900-01-01', '1970-01-01', '9999-12-31', '0000-00-00');

-- Flaw: postgres_null_byte_poison [CRITICAL] on column "body_text"
-- Evidence: [postgres_null_byte_poison] 54/1000 (5.4%) sampled rows contain ASCII NUL (0x00) bytes
-- Advice: Strip ASCII NUL (0x00) bytes using REPLACE to prevent PostgreSQL wire termination and AWS DMS CDC replication task crashes.
SELECT "body_text" FROM "case_correspondence" WHERE "body_text" LIKE '%' || CHR(0) || '%';

-- Flaw: shadow_uk_nino [CRITICAL] on column "body_text"
-- Evidence: [shadow_uk_nino] 76/1000 (7.6%) sampled rows in column 'body_text' match UK National Insurance Number pattern
-- Advice: UK National Insurance Number detected in general text. Quarantine or tokenize under UK GDPR.
SELECT "body_text" FROM "case_correspondence" WHERE "body_text" ~* '\y[A-Z]{2}\s?[0-9]{2}\s?[0-9]{2}\s?[0-9]{2}\s?[A-D]\y';

-- Flaw: shadow_nhs_number [CRITICAL] on column "body_text"
-- Evidence: [shadow_nhs_number] 76/1000 (7.6%) sampled rows in column 'body_text' match Modulus-11 verified UK NHS Number pattern
-- Advice: UK NHS Number detected in general text. Quarantine under UK Data Protection Act.
SELECT "body_text" FROM "case_correspondence" WHERE "body_text" ~ '\y[0-9]{3}\s?[0-9]{3}\s?[0-9]{4}\y';

-- ----------------------------------------------------------------------------
-- Referential Integrity & Orphan Key Findings
-- ----------------------------------------------------------------------------
-- Orphan Rows: 272 | Distinct Orphan Keys: 1 (Inferred: True)
-- Foreign Key: handled_by_code -> staff.staff_code
SELECT c."handled_by_code", COUNT(*) FROM "case_correspondence" c LEFT JOIN "staff" p ON c."handled_by_code" = p."staff_code" WHERE c."handled_by_code" IS NOT NULL AND p."staff_code" IS NULL GROUP BY 1;
