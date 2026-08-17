"""
profiler.py
===========
Phase 1 of the pipeline. Fully static / read-only:
  - Reflects schema via SQLAlchemy (tables, columns, declared FKs and PKs)
  - Builds a dependency graph from declared FKs and topologically sorts it
    to get a safe load order
  - Pulls a bounded sample of rows per table
  - Runs every detector in flaw_catalog.py against each column's sample
  - Emits a single profile.json: the complete, human-readable evidence base
    that codegen.py will consume in the next phase.

No code is generated here. No LLM is called here. This step only observes
and records. Its entire output is a plain JSON file a human can read before
anything is generated from it.
"""
import json
import networkx as nx
from sqlalchemy import create_engine, inspect, MetaData

import flaw_catalog as fc

SAMPLE_SIZE = fc.PROFILING_SAMPLE_SIZE


def reflect_schema(db_url: str):
    engine = create_engine(db_url)
    inspector = inspect(engine)
    metadata = MetaData()
    metadata.reflect(bind=engine)
    return engine, inspector, metadata


def build_dependency_graph(inspector, table_names):
    """Graph edges point child -> parent (child depends on parent existing first)."""
    g = nx.DiGraph()
    g.add_nodes_from(table_names)
    declared_fks = {}
    for t in table_names:
        fks = inspector.get_foreign_keys(t)
        declared_fks[t] = fks
        for fk in fks:
            ref_table = fk.get("referred_table")
            if ref_table and ref_table in table_names:
                g.add_edge(t, ref_table)
    return g, declared_fks


def safe_topological_order(g: nx.DiGraph):
    """
    Returns (load_order, cycle_warning). If the declared-FK graph has cycles
    (self-referencing tables, circular legacy FKs), we do NOT silently break
    the cycle -- we surface it. A human decides how to sequence those tables
    (e.g. defer-constraint load, or split into two passes).
    """
    try:
        # reverse so parents come before children in the returned order
        order = list(reversed(list(nx.topological_sort(g))))
        return order, None
    except nx.NetworkXUnfeasible:
        cycles = list(nx.simple_cycles(g))
        # fall back to a best-effort order via condensation, but flag loudly
        condensed = nx.condensation(g)
        order = list(reversed(list(nx.topological_sort(condensed))))
        flat_order = []
        for node in order:
            flat_order.extend(condensed.nodes[node]["members"])
        return flat_order, cycles


def sample_rows(engine, table_name, limit=SAMPLE_SIZE):
    with engine.connect() as conn:
        result = conn.exec_driver_sql(f'SELECT * FROM "{table_name}" LIMIT {limit}')
        cols = result.keys()
        rows = [dict(zip(cols, row)) for row in result.fetchall()]
    return rows





def check_dangling_fks(engine, table, fk, sample_limit=1000):
    """Empirically check whether declared/assumed FK values actually resolve."""
    constrained_cols = fk.get("constrained_columns", [])
    ref_table = fk.get("referred_table")
    ref_cols = fk.get("referred_columns", [])
    if not constrained_cols or not ref_table or not ref_cols:
        return None
    ccol, rcol = constrained_cols[0], ref_cols[0]
    with engine.connect() as conn:
        child_vals = conn.exec_driver_sql(
            f'SELECT DISTINCT "{ccol}" FROM "{table}" WHERE "{ccol}" IS NOT NULL LIMIT {sample_limit}'
        ).fetchall()
        child_vals = {r[0] for r in child_vals}
        if not child_vals:
            return None
        parent_vals = conn.exec_driver_sql(
            f'SELECT DISTINCT "{rcol}" FROM "{ref_table}"'
        ).fetchall()
        parent_vals = {r[0] for r in parent_vals}
    dangling = child_vals - parent_vals
    if dangling:
        return {
            "child_table": table, "child_col": ccol,
            "parent_table": ref_table, "parent_col": rcol,
            "dangling_count": len(dangling),
            "sample_dangling_values": list(dangling)[:5],
        }
    return None


def profile_column(col_name, sample_rows_list, is_pk_col):
    values = [r.get(col_name) for r in sample_rows_list]
    matches = []
    for detector, extra_args in [
        (fc.detect_control_chars, ()),
        (fc.detect_text_currency, ()),
        (fc.detect_non_numeric_in_numeric_col, (col_name,)),
        (fc.detect_impossible_or_sentinel_dates, (col_name,)),
        (fc.detect_overloaded_freetext, (col_name,)),
        (fc.detect_duplicate_declared_key, (is_pk_col,)),
        (fc.detect_negative_where_unsigned_expected, (col_name,)),
    ]:
        result = detector(values, *extra_args)
        if result:
            matches.append({
                "flaw_id": result.flaw_id,
                "confidence": round(result.confidence, 2),
                "evidence": result.evidence,
                "severity": fc.FLAW_REGISTRY[result.flaw_id][0],
                "description": fc.FLAW_REGISTRY[result.flaw_id][1],
            })
    return matches


def run_profile(db_url: str, out_path="profile.json"):
    engine, inspector, metadata = reflect_schema(db_url)
    table_names = inspector.get_table_names()

    columns_by_table = {t: inspector.get_columns(t) for t in table_names}
    pk_by_table = {t: (inspector.get_pk_constraint(t).get("constrained_columns") or None) for t in table_names}

    dep_graph, declared_fks = build_dependency_graph(inspector, table_names)

    all_fks = {t: list(declared_fks.get(t, [])) for t in table_names}

    load_order, cycle_warning = safe_topological_order(dep_graph)

    profile = {
        "source": db_url,
        "load_order": load_order,
        "fk_cycle_warning": cycle_warning,
        "tables": {},
    }

    for table in table_names:
        pk_cols = set(pk_by_table.get(table) or [])
        cols_meta = columns_by_table[table]
        rows = sample_rows(engine, table)

        col_profiles = {}
        for col in cols_meta:
            cname = col["name"]
            col_profiles[cname] = {
                "declared_type": str(col["type"]),
                "nullable": col.get("nullable", True),
                "is_declared_pk": cname in pk_cols,
                "flaws": profile_column(cname, rows, cname in pk_cols),
            }

        dangling = []
        for fk in all_fks.get(table, []):
            d = check_dangling_fks(engine, table, fk)
            if d:
                d["inferred"] = fk.get("inferred", False)
                dangling.append(d)

        profile["tables"][table] = {
            "row_sample_size": len(rows),
            "declared_pk": list(pk_cols) or None,
            "declared_fks": declared_fks.get(table, []),
            "inferred_fks": [],
            "dangling_fk_findings": dangling,
            "columns": col_profiles,
        }

    with open(out_path, "w") as f:
        json.dump(profile, f, indent=2, default=str)

    return profile


if __name__ == "__main__":
    import sys
    db_url = sys.argv[1] if len(sys.argv) > 1 else "sqlite:///legacy_source.db"
    p = run_profile(db_url)
    print(json.dumps({"load_order": p["load_order"], "fk_cycle_warning": p["fk_cycle_warning"]}, indent=2))
    print(f"\nFull profile written to profile.json")