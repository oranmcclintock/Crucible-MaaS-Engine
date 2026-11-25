"""
profiler.py
===========
Deterministic Read-Only Profiler & Shadow FK Engine (v2.0).
Features:
  - Dialect-agnostic schema reflection (PostgreSQL, MSSQL, Oracle, SQLite)
  - Safe credential masking with sqlalchemy.engine.make_url
  - Bounded memory sampling with SQLAlchemy Core
  - In-database SQL pushdown for dangling foreign key checks (anti-OOM)
  - Empirical Shadow Foreign Key inference engine
  - AWS DMS CDC replication blocker audit
  - Mathematical readiness scoring and remediation effort estimation
  - Emits profile.json strictly adhering to Crucible v2.0 Data Contract
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import networkx as nx
import sqlalchemy as sa
from sqlalchemy import func, inspect, select

import flaw_catalog as fc


def mask_db_url(db_url: str) -> str:
    """Safely masks passwords and sensitive credentials in database URLs."""
    try:
        url_obj = sa.engine.make_url(db_url)
        return url_obj.render_as_string(hide_password=True)
    except (sa.exc.ArgumentError, ValueError):
        # Fallback regex masking if URL cannot be parsed by SQLAlchemy
        import re
        return re.sub(r":([^/@:]+)@", ":***@", db_url)


def reflect_schema(db_url: str) -> tuple[sa.Engine, Any, sa.MetaData]:
    """Reflects schema using SQLAlchemy Core."""
    engine = sa.create_engine(db_url)
    inspector = inspect(engine)
    metadata = sa.MetaData()
    metadata.reflect(bind=engine)
    return engine, inspector, metadata


def sample_rows(engine: sa.Engine, metadata: sa.MetaData, table_name: str, limit: int = 1000) -> list[dict[str, Any]]:
    """Dialect-safe bounded row sampling using SQLAlchemy Core select(table).limit(n)."""
    table_obj = metadata.tables.get(table_name)
    if table_obj is None:
        return []
    stmt = select(table_obj).limit(limit)
    with engine.connect() as conn:
        result = conn.execute(stmt)
        return [dict(r) for r in result.mappings().fetchall()]


def get_table_row_count(engine: sa.Engine, metadata: sa.MetaData, table_name: str) -> int:
    """Calculates row count estimate using COUNT(*)."""
    table_obj = metadata.tables.get(table_name)
    if table_obj is None:
        return 0
    try:
        with engine.connect() as conn:
            cnt = conn.execute(select(func.count()).select_from(table_obj)).scalar()
            return int(cnt or 0)
    except sa.exc.SQLAlchemyError:
        return 0


def check_dangling_fks(
    engine: sa.Engine,
    metadata: sa.MetaData,
    child_table_name: str,
    ccol: str,
    parent_table_name: str,
    rcol: str,
    is_inferred: bool = False,
) -> dict[str, Any] | None:
    """
    Pushes dangling foreign key verification down to SQL via outer joins,
    avoiding in-memory loading of unbounded parent primary keys.
    Returns strictly Zero-PII counts (orphan_row_count and distinct_orphan_keys).
    """
    child_table = metadata.tables.get(child_table_name)
    parent_table = metadata.tables.get(parent_table_name)
    if child_table is None or parent_table is None:
        return None
    if ccol not in child_table.c or rcol not in parent_table.c:
        return None

    c_tbl = child_table.alias("c")
    p_tbl = parent_table.alias("p")
    c_col = c_tbl.c[ccol]
    p_col = p_tbl.c[rcol]

    def _run_orphan_counts(join_cond: Any) -> tuple[int, int]:
        stmt = (
            select(func.count(), func.count(func.distinct(c_col)))
            .select_from(c_tbl.outerjoin(p_tbl, join_cond))
            .where(c_col.is_not(None), p_col.is_(None))
        )
        with engine.connect() as conn:
            row = conn.execute(stmt).one()
            return int(row[0] or 0), int(row[1] or 0)

    used_text_cast = False
    try:
        orphan_row_count, distinct_orphan_keys = _run_orphan_counts(c_col == p_col)
    except sa.exc.SQLAlchemyError:
        try:
            orphan_row_count, distinct_orphan_keys = _run_orphan_counts(
                sa.cast(c_col, sa.Text) == sa.cast(p_col, sa.Text)
            )
            used_text_cast = True
        except sa.exc.SQLAlchemyError:
            return None

    if orphan_row_count == 0:
        return None

    join_sql = (
        f'CAST(c."{ccol}" AS TEXT) = CAST(p."{rcol}" AS TEXT)'
        if used_text_cast
        else f'c."{ccol}" = p."{rcol}"'
    )
    diag_sql = (
        f'SELECT c."{ccol}", COUNT(*) '
        f'FROM "{child_table_name}" c '
        f'LEFT JOIN "{parent_table_name}" p ON {join_sql} '
        f'WHERE c."{ccol}" IS NOT NULL AND p."{rcol}" IS NULL '
        f'GROUP BY 1;'
    )

    return {
        "child_table": child_table_name,
        "child_col": ccol,
        "parent_table": parent_table_name,
        "parent_col": rcol,
        "is_inferred": is_inferred,
        "orphan_row_count": orphan_row_count,
        "distinct_orphan_keys": distinct_orphan_keys,
        "diagnostic_sql": diag_sql,
    }


def extract_stem(col_name: str, suffixes: list[str], min_stem_len: int = 3) -> str:
    """Extracts column name stem by stripping known identifier suffixes."""
    c_lower = col_name.lower()
    for s in suffixes:
        if c_lower.endswith(s) and len(c_lower) - len(s) >= min_stem_len:
            return c_lower[:-len(s)]
    return c_lower


def infer_foreign_keys(
    engine: sa.Engine,
    metadata: sa.MetaData,
    table_names: list[str],
    declared_fks: dict[str, list[dict[str, Any]]],
    samples_by_table: dict[str, list[dict[str, Any]]],
    rules: dict,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    """
    Empirical Shadow Foreign Key inference engine:
    Discovers unconstrained relational dependencies by matching stems/suffixes and sample overlaps.
    Returns (inferred_fks_by_table, dangling_findings_by_table).
    """
    fk_cfg = rules.get("fk_inference", {})
    suffixes = fk_cfg.get("id_suffixes", ["_id", "_ref", "_code", "_no", "_num", "_key", "_fk"])
    min_stem_len = fk_cfg.get("min_stem_length", 3)
    min_overlap = fk_cfg.get("min_overlap_ratio", 0.80)

    # Build declared FK lookup {(child_table, child_col): True}
    covered_declared: set[tuple[str, str]] = set()
    for t, fk_list in declared_fks.items():
        for fk in fk_list:
            c_cols = fk.get("constrained_columns") or []
            if c_cols:
                covered_declared.add((t, c_cols[0]))

    # Gather candidate parent columns (single PKs or unique/key columns) with non-null sample sets
    parent_candidates: dict[tuple[str, str], set[Any]] = {}
    for p_table in table_names:
        table_obj = metadata.tables.get(p_table)
        if table_obj is None:
            continue
        p_samples = samples_by_table.get(p_table, [])
        pk_cols = [c.name for c in table_obj.primary_key.columns] if table_obj.primary_key is not None else []
        for c in table_obj.columns:
            cname = c.name
            is_pk = cname in pk_cols
            has_suffix = any(cname.lower().endswith(sfx) for sfx in suffixes)
            if is_pk or has_suffix:
                try:
                    non_null_vals = {r[cname] for r in p_samples if r.get(cname) is not None}
                except TypeError:
                    non_null_vals = {str(r[cname]) for r in p_samples if r.get(cname) is not None}
                if non_null_vals:
                    parent_candidates[(p_table, cname)] = non_null_vals

    inferred_by_table: dict[str, list[dict[str, Any]]] = {t: [] for t in table_names}
    dangling_by_table: dict[str, list[dict[str, Any]]] = {t: [] for t in table_names}

    # Semantic stem aliases for public sector / municipal schemas
    stem_synonyms = {
        "officer": ["staff", "officers", "employees", "users"],
        "housing_officer": ["staff", "officers"],
        "citizen": ["citizens", "residents", "people", "clients"],
        "claimant": ["citizens", "residents", "clients"],
        "property": ["properties", "property_master", "units", "dwellings"],
        "tenant": ["tenants", "citizens"],
    }

    for c_table in table_names:
        c_samples = samples_by_table.get(c_table, [])
        table_obj = metadata.tables.get(c_table)
        if table_obj is None:
            continue

        c_pk_cols = [c.name for c in table_obj.primary_key.columns] if table_obj.primary_key is not None else []
        for col in table_obj.columns:
            c_col = col.name
            if c_col in c_pk_cols:
                continue
            if (c_table, c_col) in covered_declared:
                continue

            has_suffix = any(c_col.lower().endswith(sfx) for sfx in suffixes)
            if not has_suffix:
                continue

            c_stem = extract_stem(c_col, suffixes, min_stem_len)
            try:
                child_vals = {r[c_col] for r in c_samples if r.get(c_col) is not None}
            except TypeError:
                child_vals = {str(r[c_col]) for r in c_samples if r.get(c_col) is not None}
            if not child_vals:
                continue

            best_match: dict[str, Any] | None = None
            highest_score: float = 0.0

            for (p_table, p_col), p_vals in parent_candidates.items():
                if p_table == c_table:
                    continue  # skip self-reference in inference heuristic for safety

                p_stem = extract_stem(p_col, suffixes, min_stem_len)
                p_table_clean = p_table.lower()

                # Check naming alignment heuristics
                name_match = False
                match_reason_str = ""

                # 1. Direct column name match (e.g. citizen_id == citizen_id)
                if c_col.lower() == p_col.lower():
                    name_match = True
                    match_reason_str = "exact_column_name_match"
                # 2. Stem matches parent table name (singular/plural)
                elif c_stem == p_table_clean or c_stem + "s" == p_table_clean or c_stem.rstrip("s") == p_table_clean:
                    name_match = True
                    match_reason_str = "table_stem_match"
                # 3. Domain synonyms match (e.g. officer -> staff)
                elif c_stem in stem_synonyms and p_table_clean in stem_synonyms[c_stem]:
                    name_match = True
                    match_reason_str = f"domain_synonym_match ({c_stem} -> {p_table})"
                # 4. Both end with identical suffix and stems match
                elif c_stem == p_stem:
                    name_match = True
                    match_reason_str = "column_stem_match"
                # 5. Same suffix and both parent/child share common string patterns
                elif any(c_col.lower().endswith(s) and p_col.lower().endswith(s) for s in suffixes):
                    name_match = True
                    match_reason_str = "suffix_match"

                if not name_match:
                    continue

                overlap_count = len(child_vals & p_vals)
                overlap_ratio = overlap_count / len(child_vals)

                if overlap_ratio >= float(min_overlap):
                    conf = min(0.99, max(0.70, overlap_ratio * 0.95 + (0.05 if "stem" in match_reason_str else 0.0)))
                    score = overlap_ratio * (1.2 if "table_stem" in match_reason_str or "domain_synonym" in match_reason_str else 1.0)
                    if score > highest_score:
                        highest_score = score
                        best_match = {
                            "child_table": c_table,
                            "child_col": c_col,
                            "parent_table": p_table,
                            "parent_col": p_col,
                            "confidence": round(conf, 2),
                            "match_reason": f"{match_reason_str} + {overlap_ratio * 100:.1f}% sample value overlap",
                        }

            if best_match:
                inferred_by_table[c_table].append(best_match)
                # Check for dangling orphaned records on the inferred FK
                dangling = check_dangling_fks(
                    engine,
                    metadata,
                    c_table,
                    best_match["child_col"],
                    best_match["parent_table"],
                    best_match["parent_col"],
                    is_inferred=True,
                )
                if dangling:
                    dangling_by_table[c_table].append(dangling)

    return inferred_by_table, dangling_by_table


def audit_dms_table_blockers(
    table_name: str,
    declared_pk: list[str],
    col_profiles: dict[str, dict[str, Any]],
    dialect: str = "sqlite",
    rules: dict | None = None,
) -> list[dict[str, Any]]:
    """Evaluates AWS DMS CDC replication blockers at the table and column levels."""
    if rules is not None and not fc.is_pack_enabled("aws_dms_cdc", rules):
        return []

    blockers: list[dict[str, Any]] = []
    has_pk = bool(declared_pk and len(declared_pk) > 0)

    # Blocker 1: Missing PK for CDC replication
    if not has_pk:
        blockers.append({
            "blocker_id": "missing_primary_key_for_cdc",
            "severity": "CRITICAL",
            "column": None,
            "description": "Table has no declared Primary Key or Unique Index. AWS DMS Full Load + CDC cannot replicate incremental UPDATE or DELETE events without a Primary Key.",
        })
    elif len(declared_pk) > 1:
        # True Composite Primary Key
        for pk_col in declared_pk:
            c_prof = col_profiles.get(pk_col, {})
            is_nullable = c_prof.get("null_rate", 0.0) > 0.0 or (dialect != "sqlite" and c_prof.get("nullable") is True)
            if is_nullable:
                blockers.append({
                    "blocker_id": "composite_pk_with_nullable_member",
                    "severity": "HIGH",
                    "column": pk_col,
                    "description": f"Composite primary key member '{pk_col}' is nullable or contains NULLs, violating replication engine constraints.",
                })
    else:
        # Single-Column Primary Key: only emit if actual NULL values exist in the sample
        pk_col = declared_pk[0]
        c_prof = col_profiles.get(pk_col, {})
        if c_prof.get("null_rate", 0.0) > 0.0:
            blockers.append({
                "blocker_id": "nullable_primary_key_with_nulls",
                "severity": "CRITICAL",
                "column": pk_col,
                "description": f"Primary key column '{pk_col}' contains NULL values in sample, violating entity integrity.",
            })

    # Blocker 3: LOB column without Primary Key
    lob_types = {"TEXT", "CLOB", "BLOB", "IMAGE", "NVARCHAR(MAX)", "BYTEA"}
    for col_name, c_prof in col_profiles.items():
        dt = c_prof.get("declared_type", "").upper()
        if any(lt in dt for lt in lob_types) and not has_pk:
            blockers.append({
                "blocker_id": "lob_column_without_pk",
                "severity": "CRITICAL",
                "column": col_name,
                "description": f"Table contains LOB column '{col_name}' without a Primary Key. AWS DMS CDC replication fails on keyless LOB tables.",
            })

        # Promote critical column flaws into DMS blockers (sanitized rule/count-level description only)
        for f in c_prof.get("flaws", []):
            if f["flaw_id"] in {"postgres_null_byte_poison", "cp1252_encoding_ghosts", "legacy_proprietary_datatype"}:
                rate_pct = round(float(f.get("affected_rate", 0.0)) * 100.0, 1)
                sanitized_desc = (
                    f"[{f['flaw_id']}] Column '{col_name}' triggered DMS replication blocker "
                    f"({rate_pct:.1f}% affected sample rate)."
                )
                blockers.append({
                    "blocker_id": f["flaw_id"],
                    "severity": f["severity"],
                    "column": col_name,
                    "description": sanitized_desc,
                })

    return blockers


def profile_column(
    table_name: str,
    col_name: str,
    declared_type: str,
    nullable: bool,
    is_pk: bool,
    sample_rows_list: list[dict[str, Any]],
    rules: dict,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Profiles a single column for statistical rates and corruption flaws."""
    values = [r.get(col_name) for r in sample_rows_list]
    total_samples = len(sample_rows_list)

    null_count = sum(1 for v in values if v is None)
    null_rate = round(null_count / max(total_samples, 1), 2)

    non_null_values = [v for v in values if v is not None]
    try:
        distinct_count = len(set(non_null_values))
    except TypeError:
        distinct_count = len({str(v) for v in non_null_values})
    distinct_rate = round(distinct_count / max(len(non_null_values), 1), 2)

    flaws = fc.detect_column_flaws(
        values=values,
        col_name=col_name,
        declared_type=declared_type,
        is_pk=is_pk,
        rules_dict=rules,
        table_name=table_name,
    )
    recommended_target_type = declared_type

    # Pick recommended target type from highest severity flaw or sensible standard
    if flaws:
        sev_priority = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "WARN": 1, "LOW": 1}
        sorted_flaws = sorted(flaws, key=lambda f: sev_priority.get(str(f["severity"]).upper(), 0), reverse=True)
        top_art = fc.build_remediation_artifacts(
            sorted_flaws[0]["flaw_id"], table_name, col_name, declared_type, rules_dict=rules
        )
        recommended_target_type = top_art["recommended_target_type"]
    else:
        dt_up = declared_type.upper()
        if "INT" in dt_up:
            recommended_target_type = "BIGINT" if is_pk else "INTEGER"
        elif "CHAR" in dt_up:
            recommended_target_type = "VARCHAR(255)"
        elif "FLOAT" in dt_up or "DOUBLE" in dt_up or "REAL" in dt_up:
            recommended_target_type = "NUMERIC(18,4)"
        elif "DATE" in dt_up or "TIME" in dt_up:
            recommended_target_type = "TIMESTAMP"
        else:
            recommended_target_type = declared_type

    col_meta = {
        "declared_type": declared_type,
        "recommended_target_type": recommended_target_type,
        "nullable": nullable,
        "is_declared_pk": is_pk,
        "null_rate": null_rate,
        "distinct_rate": distinct_rate,
        "flaws": flaws,
    }

    return col_meta, flaws


def build_dependency_graph(
    table_names: list[str],
    declared_fks: dict[str, list[dict[str, Any]]],
    inferred_fks: dict[str, list[dict[str, Any]]],
) -> tuple[nx.DiGraph, list[str], list[list[str]], str]:
    """
    Builds topological graph with edges: child -> parent (child depends on parent existing).
    Computes safe load order, flags circular dependencies, and renders Mermaid diagram.
    """
    g = nx.DiGraph()
    g.add_nodes_from(table_names)

    mermaid_edges: list[str] = []

    for c_table, fk_list in declared_fks.items():
        for fk in fk_list:
            ref_table = fk.get("referred_table")
            if ref_table and ref_table in table_names:
                g.add_edge(c_table, ref_table)
                mermaid_edges.append(f"  {c_table} --> {ref_table};")

    for c_table, inf_list in inferred_fks.items():
        for inf in inf_list:
            ref_table = inf.get("parent_table")
            if ref_table and ref_table in table_names and not g.has_edge(c_table, ref_table):
                g.add_edge(c_table, ref_table)
                mermaid_edges.append(f"  {c_table} -.->|inferred| {ref_table};")

    mermaid_dag = "graph TD;\n" + ("\n".join(mermaid_edges) if mermaid_edges else "\n".join(f"  {t};" for t in table_names))

    cycles: list[list[str]] = []
    try:
        # Parents first: reverse topological order
        load_order = list(reversed(list(nx.topological_sort(g))))
    except nx.NetworkXUnfeasible:
        cycles = list(nx.simple_cycles(g))
        condensed = nx.condensation(g)
        cond_order = list(reversed(list(nx.topological_sort(condensed))))
        load_order = []
        for n in cond_order:
            load_order.extend(condensed.nodes[n]["members"])

    return g, load_order, cycles, mermaid_dag


def calculate_readiness_scores(
    tables_profile: dict[str, dict[str, Any]],
    has_fk_cycles: bool,
    rules: dict,
) -> tuple[int, str, float, dict[str, int]]:
    """
    Computes deterministic readiness score (0-100), readiness grade,
    remediation hours, and pillar blocker counts without double-counting promoted DMS flaws.
    """
    weights = rules.get("remediation_hours_weights", {"CRITICAL": 4.0, "HIGH": 2.0, "MEDIUM": 1.0, "WARN": 0.5})
    crit_w = float(weights.get("CRITICAL", 4.0))
    high_w = float(weights.get("HIGH", 2.0))
    med_w = float(weights.get("MEDIUM", 1.0))
    warn_w = float(weights.get("WARN", 0.5))

    blocker_counts = {
        "dms_replication_blockers": 0,
        "referential_integrity_failures": 0,
        "semantic_data_rot": 0,
        "shadow_pii_exposures": 0,
        "custom_rules": 0,
    }

    total_table_score = 0
    total_remediation_hours = 0.0

    for tdata in tables_profile.values():
        # Start table at 100 points
        tscore = 100

        # DMS blockers
        dms_blockers = tdata.get("dms_blockers", [])
        promoted_dms_flaws: set[tuple[str | None, str | None]] = set()
        blocker_counts["dms_replication_blockers"] += len(dms_blockers)
        for b in dms_blockers:
            if b.get("column") is not None:
                promoted_dms_flaws.add((b.get("column"), b.get("blocker_id")))
            sev = str(b.get("severity", "CRITICAL")).upper()
            if sev == "CRITICAL":
                tscore -= 25
                total_remediation_hours += crit_w
            elif sev == "HIGH":
                tscore -= 12
                total_remediation_hours += high_w
            elif sev == "MEDIUM":
                tscore -= 8
                total_remediation_hours += med_w
            else:
                tscore -= 4
                total_remediation_hours += warn_w

        # Dangling FK findings
        dangling = tdata.get("dangling_fk_findings", [])
        blocker_counts["referential_integrity_failures"] += len(dangling)
        for _ in dangling:
            tscore -= 20
            total_remediation_hours += high_w  # 2.0h per dangling FK fix

        # Column flaws (skip DMS column flaws already scored/accounted in dms_blockers)
        for col_name, c_prof in tdata.get("columns", {}).items():
            for f in c_prof.get("flaws", []):
                cat = f.get("category", "")
                flaw_id = f.get("flaw_id")
                if (col_name, flaw_id) in promoted_dms_flaws or cat == "dms_replication_blockers":
                    continue

                sev = str(f.get("severity", "WARN")).upper()

                if cat == "shadow_pii_exposures":
                    blocker_counts["shadow_pii_exposures"] += 1
                elif cat == "semantic_data_rot":
                    blocker_counts["semantic_data_rot"] += 1
                elif cat == "referential_and_key_integrity":
                    blocker_counts["referential_integrity_failures"] += 1
                else:
                    blocker_counts["custom_rules"] += 1

                custom_hours = f.get("remediation_hours")
                if sev == "CRITICAL":
                    tscore -= 25
                    total_remediation_hours += float(custom_hours) if custom_hours is not None else crit_w
                elif sev == "HIGH":
                    tscore -= 12
                    total_remediation_hours += float(custom_hours) if custom_hours is not None else high_w
                elif sev == "MEDIUM":
                    tscore -= 8
                    total_remediation_hours += float(custom_hours) if custom_hours is not None else med_w
                else:
                    tscore -= 4
                    total_remediation_hours += float(custom_hours) if custom_hours is not None else warn_w

        tdata["table_readiness_score"] = max(0, tscore)
        total_table_score += tdata["table_readiness_score"]

    num_tables = max(len(tables_profile), 1)
    avg_score = round(total_table_score / num_tables)
    if has_fk_cycles:
        avg_score = max(0, avg_score - 15)

    overall_score = max(0, min(100, avg_score))

    # Determine grade
    if overall_score >= 85:
        grade = "GREEN - CLOUD READY"
    elif overall_score >= 60:
        grade = "AMBER - MODERATE REMEDIATION REQUIRED"
    else:
        grade = "RED - CRITICAL MIGRATION BLOCKERS DETECTED"

    return overall_score, grade, round(total_remediation_hours, 1), blocker_counts


def run_profile(db_url: str, out_path: str = "profile.json", rules_path: str | None = None) -> dict:
    """
    Executes the full read-only profiling scan against db_url and outputs profile.json.
    """
    rules = fc.load_rules(rules_path)
    sample_size = rules.get("profiling_sample_size", 1000)

    engine, inspector, metadata = reflect_schema(db_url)
    try:
        dialect_name = engine.dialect.name
        redacted_url = mask_db_url(db_url)

        table_names = inspector.get_table_names()

        # Pre-collect declared PKs and FKs
        pk_by_table: dict[str, list[str]] = {}
        declared_fks_by_table: dict[str, list[dict[str, Any]]] = {}
        columns_meta_by_table: dict[str, list[dict[str, Any]]] = {}
        samples_by_table: dict[str, list[dict[str, Any]]] = {}
        row_counts_by_table: dict[str, int] = {}

        for t in table_names:
            pk_constraint = inspector.get_pk_constraint(t) or {}
            pk_by_table[t] = pk_constraint.get("constrained_columns") or []
            declared_fks_by_table[t] = inspector.get_foreign_keys(t) or []
            columns_meta_by_table[t] = inspector.get_columns(t) or []
            samples_by_table[t] = sample_rows(engine, metadata, t, limit=sample_size)
            row_counts_by_table[t] = get_table_row_count(engine, metadata, t)

        # Infer Shadow FKs
        inferred_fks_by_table, inferred_dangling = infer_foreign_keys(
            engine, metadata, table_names, declared_fks_by_table, samples_by_table, rules
        )

        # Build Dependency Topology
        _, load_order, cycles, mermaid_dag = build_dependency_graph(
            table_names, declared_fks_by_table, inferred_fks_by_table
        )

        tables_profile: dict[str, dict[str, Any]] = {}
        topology_edges: list[dict[str, Any]] = []
        total_cols = 0

        for t in table_names:
            declared_pk = pk_by_table.get(t, [])
            pk_set = set(declared_pk)
            cols_meta = columns_meta_by_table.get(t, [])
            sample_rows_list = samples_by_table.get(t, [])

            col_profiles: dict[str, dict[str, Any]] = {}
            for col_info in cols_meta:
                cname = col_info["name"]
                total_cols += 1
                col_prof, _ = profile_column(
                    table_name=t,
                    col_name=cname,
                    declared_type=str(col_info["type"]),
                    nullable=col_info.get("nullable", True),
                    is_pk=(cname in pk_set),
                    sample_rows_list=sample_rows_list,
                    rules=rules,
                )
                col_profiles[cname] = col_prof

            # Check declared dangling FKs and record topology edges
            dangling_list: list[dict[str, Any]] = []
            for fk in declared_fks_by_table.get(t, []):
                c_cols = fk.get("constrained_columns") or []
                r_table = fk.get("referred_table")
                r_cols = fk.get("referred_columns") or []
                if c_cols and r_table and r_cols:
                    dang = check_dangling_fks(
                        engine, metadata, t, c_cols[0], r_table, r_cols[0], is_inferred=False
                    )
                    orphan_count = dang["orphan_row_count"] if dang else 0
                    distinct_orphans = dang["distinct_orphan_keys"] if dang else 0
                    diag_sql = (
                        dang["diagnostic_sql"]
                        if dang
                        else (
                            f'SELECT c."{c_cols[0]}", COUNT(*) FROM "{t}" c '
                            f'LEFT JOIN "{r_table}" p ON c."{c_cols[0]}" = p."{r_cols[0]}" '
                            f'WHERE c."{c_cols[0]}" IS NOT NULL AND p."{r_cols[0]}" IS NULL GROUP BY 1;'
                        )
                    )
                    if dang:
                        dangling_list.append(dang)

                    topology_edges.append({
                        "child_table": t,
                        "child_col": c_cols[0],
                        "parent_table": r_table,
                        "parent_col": r_cols[0],
                        "relationship_type": "DECLARED_FK",
                        "confidence": 1.0,
                        "orphan_row_count": orphan_count,
                        "distinct_orphan_keys": distinct_orphans,
                        "status": "HEALTHY" if orphan_count == 0 else "ORPHANED_RECORDS_DETECTED",
                        "diagnostic_sql": diag_sql,
                    })

            # Append inferred dangling findings and record inferred topology edges
            for inf in inferred_fks_by_table.get(t, []):
                c_col = inf.get("child_col")
                p_table = inf.get("parent_table")
                p_col = inf.get("parent_col")
                conf = inf.get("confidence", 0.90)
                matched_dang = next(
                    (
                        d
                        for d in inferred_dangling.get(t, [])
                        if d["child_col"] == c_col
                        and d["parent_table"] == p_table
                        and d["parent_col"] == p_col
                    ),
                    None,
                )
                orphan_count = matched_dang["orphan_row_count"] if matched_dang else 0
                distinct_orphans = matched_dang["distinct_orphan_keys"] if matched_dang else 0
                diag_sql = (
                    matched_dang["diagnostic_sql"]
                    if matched_dang
                    else (
                        f'SELECT c."{c_col}", COUNT(*) FROM "{t}" c '
                        f'LEFT JOIN "{p_table}" p ON c."{c_col}" = p."{p_col}" '
                        f'WHERE c."{c_col}" IS NOT NULL AND p."{p_col}" IS NULL GROUP BY 1;'
                    )
                )

                topology_edges.append({
                    "child_table": t,
                    "child_col": c_col,
                    "parent_table": p_table,
                    "parent_col": p_col,
                    "relationship_type": "INFERRED_SHADOW_FK",
                    "confidence": conf,
                    "orphan_row_count": orphan_count,
                    "distinct_orphan_keys": distinct_orphans,
                    "status": "HEALTHY" if orphan_count == 0 else "ORPHANED_RECORDS_DETECTED",
                    "diagnostic_sql": diag_sql,
                })

            dangling_list.extend(inferred_dangling.get(t, []))

            # DMS table-level blocker audit
            dms_blockers = audit_dms_table_blockers(
                t, declared_pk, col_profiles, dialect=dialect_name, rules=rules
            )

            tables_profile[t] = {
                "row_count_estimate": row_counts_by_table.get(t, len(sample_rows_list)),
                "row_sample_size": len(sample_rows_list),
                "table_readiness_score": 100,  # calculated below
                "declared_pk": declared_pk,
                "dms_blockers": dms_blockers,
                "declared_fks": declared_fks_by_table.get(t, []),
                "inferred_fks": inferred_fks_by_table.get(t, []),
                "dangling_fk_findings": dangling_list,
                "columns": col_profiles,
            }

        # Calculate overall scores and remediation efforts
        overall_score, readiness_grade, est_hours, blocker_counts = calculate_readiness_scores(
            tables_profile, bool(cycles), rules
        )

        profile_data = {
            "metadata": {
                "schemafirst_version": "2.0.0",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "source_redacted_url": redacted_url,
                "dialect": dialect_name,
                "sample_size_per_table": sample_size,
            },
            "executive_summary": {
                "overall_readiness_score": overall_score,
                "readiness_grade": readiness_grade,
                "total_tables": len(table_names),
                "total_columns": total_cols,
                "estimated_remediation_hours": est_hours,
                "blocker_counts": blocker_counts,
            },
            "topology": {
                "load_order": load_order,
                "fk_cycle_warning": cycles,
                "mermaid_dag": mermaid_dag,
                "edges": topology_edges,
            },
            "tables": tables_profile,
        }

        if out_path:
            os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(profile_data, f, indent=2, default=str)

        return profile_data
    finally:
        engine.dispose()


if __name__ == "__main__":
    import sys
    target_db = sys.argv[1] if len(sys.argv) > 1 else "sqlite:///demo_legacy_council.db"
    out_file = sys.argv[2] if len(sys.argv) > 2 else "profile.json"
    p = run_profile(target_db, out_file)
    print(f"Audit completed: {p['executive_summary']['readiness_grade']} ({p['executive_summary']['overall_readiness_score']}/100)")
    print(f"Profile saved to {out_file}")