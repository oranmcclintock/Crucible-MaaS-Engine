-- ============================================================================
-- SchemaFirst Diagnostic SQL Pack: properties
-- Target Database: Source Legacy Replica
-- Read-Only DBA Triage Queries
-- ============================================================================

-- Flaw: cp1252_encoding_ghosts [HIGH] on column "address_line1"
-- Evidence: [cp1252_encoding_ghosts] 125/1000 (12.5%) sampled rows contain UTF-8/CP1252 mojibake artifacts
-- Advice: Re-encode CP1252/Windows-1252 mojibake artifacts back to valid UTF-8 equivalents.
SELECT "address_line1" FROM "properties" WHERE "address_line1" LIKE '%â%' OR "address_line1" LIKE '%�%';

-- Flaw: cp1252_encoding_ghosts [HIGH] on column "inspection_notes"
-- Evidence: [cp1252_encoding_ghosts] 90/1000 (9.0%) sampled rows contain UTF-8/CP1252 mojibake artifacts
-- Advice: Re-encode CP1252/Windows-1252 mojibake artifacts back to valid UTF-8 equivalents.
SELECT "inspection_notes" FROM "properties" WHERE "inspection_notes" LIKE '%â%' OR "inspection_notes" LIKE '%�%';

-- Flaw: overloaded_freetext_column [WARN] on column "inspection_notes"
-- Evidence: [overloaded_freetext_column] 1000/1000 (100.0%) sampled rows in column 'inspection_notes' contain packed key:value pairs
-- Advice: Unpack packed key:value pairs into structured downstream staging columns or JSON.
SELECT "inspection_notes" FROM "properties" WHERE "inspection_notes" ~ '[A-Za-z_]+:[A-Za-z0-9_]+';
