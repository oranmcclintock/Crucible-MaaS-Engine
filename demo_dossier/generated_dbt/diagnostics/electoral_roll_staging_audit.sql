-- ============================================================================
-- SchemaFirst Diagnostic SQL Pack: electoral_roll_staging
-- Target Database: Source Legacy Replica
-- Read-Only DBA Triage Queries
-- ============================================================================

-- Flaw: impossible_or_sentinel_date [WARN] on column "reg_date"
-- Evidence: [impossible_or_sentinel_date] 66/1000 (6.6%) sampled rows in column 'reg_date' contain sentinel or impossible dates
-- Advice: Map legacy sentinel dates (1900-01-01, 9999-12-31) to NULL and enforce ISO-8601 calendar validation.
SELECT "reg_date" FROM "electoral_roll_staging" WHERE "reg_date" IN ('1900-01-01', '1970-01-01', '9999-12-31', '0000-00-00');

-- Flaw: postgres_null_byte_poison [CRITICAL] on column "raw_import_line"
-- Evidence: [postgres_null_byte_poison] 47/1000 (4.7%) sampled rows contain ASCII NUL (0x00) bytes
-- Advice: Strip ASCII NUL (0x00) bytes using REPLACE to prevent PostgreSQL wire termination and AWS DMS CDC replication task crashes.
SELECT "raw_import_line" FROM "electoral_roll_staging" WHERE "raw_import_line" LIKE '%' || CHR(0) || '%';
