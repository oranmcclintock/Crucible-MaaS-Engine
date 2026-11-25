-- ============================================================================
-- SchemaFirst Diagnostic SQL Pack: legacy_audit_log
-- Target Database: Source Legacy Replica
-- Read-Only DBA Triage Queries
-- ============================================================================

-- Flaw: postgres_null_byte_poison [CRITICAL] on column "payload_blob"
-- Evidence: [postgres_null_byte_poison] 40/1000 (4.0%) sampled rows contain ASCII NUL (0x00) bytes
-- Advice: Strip ASCII NUL (0x00) bytes using REPLACE to prevent PostgreSQL wire termination and AWS DMS CDC replication task crashes.
SELECT "payload_blob" FROM "legacy_audit_log" WHERE "payload_blob" LIKE '%' || CHR(0) || '%';

-- ----------------------------------------------------------------------------
-- Referential Integrity & Orphan Key Findings
-- ----------------------------------------------------------------------------
-- Orphan Rows: 180 | Distinct Orphan Keys: 1 (Inferred: True)
-- Foreign Key: actor_code -> case_correspondence.handled_by_code
SELECT c."actor_code", COUNT(*) FROM "legacy_audit_log" c LEFT JOIN "case_correspondence" p ON c."actor_code" = p."handled_by_code" WHERE c."actor_code" IS NOT NULL AND p."handled_by_code" IS NULL GROUP BY 1;
