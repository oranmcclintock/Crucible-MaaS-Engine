"""
codegen.py
==========
Deterministic dbt & Diagnostic SQL Compiler (v2.0).
Reads profile.json and compiles:
  1. Sanitized dbt staging models with multi-flaw nested expression chaining:
     generated_dbt/models/staging/stg_{table}.sql
  2. dbt schema and quarantine test assertions (unique, not_null, relationships):
     generated_dbt/models/staging/src_legacy.yml
  3. Diagnostic SQL queries for DBA triage and row quarantine:
     generated_dbt/diagnostics/{table}_audit.sql
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

from jinja2 import Environment, FileSystemLoader


def resolve_templates_dir() -> str:
    """Resolves templates/ via package dir -> sys.prefix share -> CWD."""
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates"),
        os.path.join(sys.prefix, "share", "schemafirst", "templates"),
        os.path.join(os.getcwd(), "templates"),
    ]
    for candidate in candidates:
        if os.path.isdir(candidate):
            return candidate
    return candidates[0]


def flaw_signature(table_profile: dict[str, Any]) -> str:
    """Stable signature of flaws detected in this table for provenance auditing."""
    ids = []
    for col, meta in sorted(table_profile.get("columns", {}).items()):
        for f in meta.get("flaws", []):
            ids.append(f"{col}:{f['flaw_id']}")
    return ",".join(ids) if ids else "clean"


def compile_column_sql(col_name: str, col_meta: dict) -> tuple[str, str, str]:
    """
    Returns (sql_expression, target_sql_type, inline_comment).
    Guarantees type safety:
      Stage 1: TEXT -> TEXT normalizers (nested inner-to-outer)
      Stage 2: At most ONE TEXT -> DATE or TEXT -> NUMERIC coercion
      Stage 3: Domain range guards on typed values (e.g., negative_unsigned_field)
    """
    quoted_col = f'"{col_name}"'
    flaws = col_meta.get("flaws", [])
    declared_type = col_meta.get("declared_type", "TEXT")
    recommended_type = col_meta.get("recommended_target_type", declared_type)

    if not flaws:
        return quoted_col, recommended_type, f"Direct pass-through ({declared_type})"

    flaw_ids = {f["flaw_id"] for f in flaws}
    ordered_flaw_names = [f["flaw_id"] for f in flaws]
    expr = quoted_col

    # ------------------------------------------------------------------
    # STAGE 1: TEXT -> TEXT Sanitizers (Always returns TEXT)
    # ------------------------------------------------------------------
    if "postgres_null_byte_poison" in flaw_ids:
        expr = f"REPLACE({expr}, CHR(0), '')"

    if "control_chars" in flaw_ids:
        expr = f"REGEXP_REPLACE({expr}, '[\\x01-\\x08\\x0b\\x0c\\x0e-\\x1f]', '', 'g')"

    if "cp1252_encoding_ghosts" in flaw_ids:
        expr = (
            f"REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE({expr}, "
            f"'â€™', ''''), 'â€“', '-'), 'â€œ', '\"'), 'Ã©', 'é'), 'Ã£', 'ã'), CHR(65533), '')"
        )

    if "whitespace_padded_keys" in flaw_ids:
        expr = f"TRIM({expr})"

    if "legacy_redaction_markers" in flaw_ids:
        expr = f"REGEXP_REPLACE({expr}, '(?i)\\y(REDACTED|GDPR_DELETED|DECEASED|DO NOT CONTACT|TEST TEST|XXXX+)\\y', '', 'g')"

    if "shadow_uk_nino" in flaw_ids:
        expr = (
            f"REGEXP_REPLACE({expr}, "
            f"'(?i)\\y(?:QQ|(?!BG|GB|NK|KN|TN|NT|ZZ)[A-CEGHJ-PR-TW-ZQ][A-CEGHJ-NPR-TW-ZQ])\\s*\\d{{2}}\\s*\\d{{2}}\\s*\\d{{2}}\\s*[A-D]\\y', "
            f"'[REDACTED_NINO]', 'g')"
        )

    if "shadow_nhs_number" in flaw_ids:
        expr = f"REGEXP_REPLACE({expr}, '\\y\\d{{3}}\\s?\\d{{3}}\\s?\\d{{4}}\\y', '[REDACTED_NHS]', 'g')"

    # ------------------------------------------------------------------
    # STAGE 2: Terminal Type Coercion (TEXT -> DATE or TEXT -> NUMERIC)
    # ------------------------------------------------------------------
    # 2A: Date Coercion (Merged if both sentinel and mixed locales fire)
    if "mixed_date_locales" in flaw_ids and "impossible_or_sentinel_date" in flaw_ids:
        expr = (
            f"CASE "
            f"WHEN {expr} IN ('1900-01-01', '1970-01-01', '9999-12-31', '0000-00-00') THEN NULL "
            f"WHEN {expr} ~ '^\\d{{4}}-\\d{{2}}-\\d{{2}}$' THEN CAST({expr} AS DATE) "
            f"ELSE NULL END"
        )
        recommended_type = "DATE"
    elif "mixed_date_locales" in flaw_ids:
        expr = f"CASE WHEN {expr} ~ '^\\d{{4}}-\\d{{2}}-\\d{{2}}$' THEN CAST({expr} AS DATE) ELSE NULL END"
        recommended_type = "DATE"
    elif "impossible_or_sentinel_date" in flaw_ids:
        expr = (
            f"CASE WHEN {expr} IN ('1900-01-01', '1970-01-01', '9999-12-31', '0000-00-00') "
            f"THEN NULL ELSE CAST({expr} AS DATE) END"
        )
        recommended_type = "DATE"

    # 2B: Numeric / Currency Coercion (Single unified cast even if both fire)
    elif "text_currency" in flaw_ids or "non_numeric_in_numeric_col" in flaw_ids:
        expr = f"CAST(NULLIF(REGEXP_REPLACE({expr}, '[^0-9.-]', '', 'g'), '') AS NUMERIC(18,2))"
        recommended_type = "NUMERIC(18,2)"

    elif "legacy_proprietary_datatype" in flaw_ids:
        expr = f"CAST({expr} AS NUMERIC(18,4))"
        recommended_type = "NUMERIC(18,4)"

    # ------------------------------------------------------------------
    # STAGE 3: Post-Coercion Domain Range Guards
    # ------------------------------------------------------------------
    if "negative_unsigned_field" in flaw_ids:
        expr = f"CASE WHEN {expr} < 0 THEN NULL ELSE {expr} END"

    comment = f"Sanitized: {', '.join(ordered_flaw_names)} -> {recommended_type}"
    return expr, recommended_type, comment


def generate_dbt_staging_sql(table_name: str, table_profile: dict[str, Any], env: Environment) -> str:
    """Renders the dbt staging model stg_{table}.sql with chained sanitized expressions."""
    template = env.get_template("dbt_staging_model.sql.j2")
    sig = flaw_signature(table_profile)

    columns = []
    for col_name, col_meta in table_profile.get("columns", {}).items():
        sql_expr, _recommended_type, comment = compile_column_sql(col_name, col_meta)

        columns.append({
            "name": col_name,
            "sql_expression": sql_expr,
            "comment": comment,
        })

    return template.render(
        table=table_name,
        sig=sig,
        columns=columns,
    )


def generate_dbt_schema_yml(profile: dict[str, Any], env: Environment) -> str:
    """Renders src_legacy.yml with quarantine tests and relationship assertions."""
    template = env.get_template("dbt_schema.yml.j2")
    tables_data = {}

    for table_name, tprofile in profile.get("tables", {}).items():
        declared_pk = set(tprofile.get("declared_pk") or [])
        col_list = []

        # Build relationships tests map {(col): [(parent_table, parent_col)]}
        rel_map: dict[str, list[tuple[str, str]]] = {}
        for fk in tprofile.get("declared_fks", []):
            ccols = fk.get("constrained_columns") or []
            rtable = fk.get("referred_table")
            rcols = fk.get("referred_columns") or []
            if ccols and rtable and rcols:
                rel_map.setdefault(ccols[0], []).append((rtable, rcols[0]))

        for inf in tprofile.get("inferred_fks", []):
            ccol = inf.get("child_col")
            ptable = inf.get("parent_table")
            pcol = inf.get("parent_col")
            if ccol and ptable and pcol:
                rel_map.setdefault(ccol, []).append((ptable, pcol))

        for col_name, col_meta in tprofile.get("columns", {}).items():
            tests = []
            if col_name in declared_pk:
                tests.append({"type": "not_null"})
                tests.append({"type": "unique"})

            if col_name in rel_map:
                for ptable, pcol in rel_map[col_name]:
                    tests.append({
                        "type": "relationships",
                        "parent_table": ptable,
                        "parent_col": pcol,
                    })

            col_list.append({
                "name": col_name,
                "recommended_target_type": col_meta.get("recommended_target_type", "TEXT"),
                "tests": tests,
            })

        tables_data[table_name] = {
            "columns": col_list,
        }

    return template.render(tables=tables_data)


def generate_diagnostic_sql_pack(table_name: str, table_profile: dict[str, Any]) -> str:
    """Emits DBA diagnostic queries for isolating corrupt or orphaned rows."""
    lines = [
        "-- ============================================================================",
        f"-- SchemaFirst Diagnostic SQL Pack: {table_name}",
        "-- Target Database: Source Legacy Replica",
        "-- Read-Only DBA Triage Queries",
        "-- ============================================================================",
        "",
    ]

    # Column Flaw queries
    flaw_found = False
    for col_name, col_meta in table_profile.get("columns", {}).items():
        for f in col_meta.get("flaws", []):
            flaw_found = True
            lines.append(f"-- Flaw: {f['flaw_id']} [{f['severity']}] on column \"{col_name}\"")
            lines.append(f"-- Evidence: {f.get('evidence', '')}")
            lines.append(f"-- Advice: {f.get('remediation_advice', '')}")
            diag_sql = f.get("diagnostic_sql")
            if not diag_sql and f.get("pg_pattern"):
                pg_pat = str(f["pg_pattern"]).replace(r"\b", r"\y").replace("'", "''")
                diag_sql = f'SELECT "{col_name}" FROM "{table_name}" WHERE "{col_name}" ~* \'{pg_pat}\';'
            if not diag_sql:
                diag_sql = f'SELECT "{col_name}" FROM "{table_name}" WHERE "{col_name}" IS NOT NULL;'
            lines.append(diag_sql.replace(r"\b", r"\y"))
            lines.append("")

    # Referential Integrity queries
    dangling = table_profile.get("dangling_fk_findings", [])
    if dangling:
        lines.append("-- ----------------------------------------------------------------------------")
        lines.append("-- Referential Integrity & Orphan Key Findings")
        lines.append("-- ----------------------------------------------------------------------------")
        for dang in dangling:
            lines.append(
                f"-- Orphan Rows: {dang.get('orphan_row_count', 0)} | "
                f"Distinct Orphan Keys: {dang.get('distinct_orphan_keys', 0)} "
                f"(Inferred: {dang.get('is_inferred')})"
            )
            lines.append(f"-- Foreign Key: {dang.get('child_col')} -> {dang.get('parent_table')}.{dang.get('parent_col')}")
            lines.append(dang.get("diagnostic_sql", ""))
            lines.append("")

    if not flaw_found and not dangling:
        lines.append(f"-- Table \"{table_name}\" has zero detected column flaws or referential integrity failures.")
        lines.append(f'SELECT COUNT(*) FROM "{table_name}";')
        lines.append("")

    return "\n".join(lines)


def compile_dbt_package(profile_path: str = "profile.json", out_dir: str = "generated_dbt") -> list[str]:
    """Compiles profile.json into a production-ready dbt staging project and diagnostic packs."""
    with open(profile_path, "r", encoding="utf-8") as f:
        profile = json.load(f)

    templates_dir = resolve_templates_dir()
    env = Environment(loader=FileSystemLoader(templates_dir))

    models_dir = os.path.join(out_dir, "models", "staging")
    diagnostics_dir = os.path.join(out_dir, "diagnostics")
    os.makedirs(models_dir, exist_ok=True)
    os.makedirs(diagnostics_dir, exist_ok=True)

    generated_files = []

    # 1. Staging models
    for table_name, tprofile in profile.get("tables", {}).items():
        staging_sql = generate_dbt_staging_sql(table_name, tprofile, env)
        model_path = os.path.join(models_dir, f"stg_{table_name}.sql")
        with open(model_path, "w", encoding="utf-8") as f:
            f.write(staging_sql)
        generated_files.append(model_path)

        # Diagnostics SQL pack
        diag_sql = generate_diagnostic_sql_pack(table_name, tprofile)
        diag_path = os.path.join(diagnostics_dir, f"{table_name}_audit.sql")
        with open(diag_path, "w", encoding="utf-8") as f:
            f.write(diag_sql)
        generated_files.append(diag_path)

    # 2. Schema YAML
    schema_yml = generate_dbt_schema_yml(profile, env)
    schema_path = os.path.join(models_dir, "src_legacy.yml")
    with open(schema_path, "w", encoding="utf-8") as f:
        f.write(schema_yml)
    generated_files.append(schema_path)

    return generated_files


if __name__ == "__main__":
    prof = sys.argv[1] if len(sys.argv) > 1 else "profile.json"
    dest = sys.argv[2] if len(sys.argv) > 2 else "generated_dbt"
    files = compile_dbt_package(prof, dest)
    print(f"Compiled {len(files)} dbt and diagnostic assets to {dest}/")