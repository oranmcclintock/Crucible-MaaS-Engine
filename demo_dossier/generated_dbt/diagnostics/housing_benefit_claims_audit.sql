-- ============================================================================
-- SchemaFirst Diagnostic SQL Pack: housing_benefit_claims
-- Target Database: Source Legacy Replica
-- Read-Only DBA Triage Queries
-- ============================================================================

-- Flaw: text_currency [WARN] on column "weekly_award_amt"
-- Evidence: [text_currency] 1000/1000 (100.0%) sampled rows contain formatted currency strings
-- Advice: Strip formatting currency symbols/suffixes and cast text values to NUMERIC(18,2).
SELECT "weekly_award_amt" FROM "housing_benefit_claims" WHERE "weekly_award_amt" ~ '[$£€]' OR "weekly_award_amt" ~* '(USD|EUR|GBP)';

-- Flaw: impossible_or_sentinel_date [WARN] on column "assessment_date"
-- Evidence: [impossible_or_sentinel_date] 50/1000 (5.0%) sampled rows in column 'assessment_date' contain sentinel or impossible dates
-- Advice: Map legacy sentinel dates (1900-01-01, 9999-12-31) to NULL and enforce ISO-8601 calendar validation.
SELECT "assessment_date" FROM "housing_benefit_claims" WHERE "assessment_date" IN ('1900-01-01', '1970-01-01', '9999-12-31', '0000-00-00');

-- Flaw: postgres_null_byte_poison [CRITICAL] on column "assessor_notes"
-- Evidence: [postgres_null_byte_poison] 41/1000 (4.1%) sampled rows contain ASCII NUL (0x00) bytes
-- Advice: Strip ASCII NUL (0x00) bytes using REPLACE to prevent PostgreSQL wire termination and AWS DMS CDC replication task crashes.
SELECT "assessor_notes" FROM "housing_benefit_claims" WHERE "assessor_notes" LIKE '%' || CHR(0) || '%';

-- Flaw: shadow_uk_nino [CRITICAL] on column "assessor_notes"
-- Evidence: [shadow_uk_nino] 58/1000 (5.8%) sampled rows in column 'assessor_notes' match UK National Insurance Number pattern
-- Advice: UK National Insurance Number detected in general text. Quarantine or tokenize under UK GDPR.
SELECT "assessor_notes" FROM "housing_benefit_claims" WHERE "assessor_notes" ~* '\y[A-Z]{2}\s?[0-9]{2}\s?[0-9]{2}\s?[0-9]{2}\s?[A-D]\y';
