-- ============================================================================
-- SchemaFirst Diagnostic SQL Pack: tenancies
-- Target Database: Source Legacy Replica
-- Read-Only DBA Triage Queries
-- ============================================================================

-- Flaw: overloaded_freetext_column [WARN] on column "tenancy_memo"
-- Evidence: [overloaded_freetext_column] 1000/1000 (100.0%) sampled rows in column 'tenancy_memo' contain packed key:value pairs
-- Advice: Unpack packed key:value pairs into structured downstream staging columns or JSON.
SELECT "tenancy_memo" FROM "tenancies" WHERE "tenancy_memo" ~ '[A-Za-z_]+:[A-Za-z0-9_]+';

-- ----------------------------------------------------------------------------
-- Referential Integrity & Orphan Key Findings
-- ----------------------------------------------------------------------------
-- Orphan Rows: 668 | Distinct Orphan Keys: 2 (Inferred: True)
-- Foreign Key: officer_code -> staff.staff_code
SELECT c."officer_code", COUNT(*) FROM "tenancies" c LEFT JOIN "staff" p ON c."officer_code" = p."staff_code" WHERE c."officer_code" IS NOT NULL AND p."staff_code" IS NULL GROUP BY 1;
