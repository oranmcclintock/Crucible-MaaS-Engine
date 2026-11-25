"""
tests/test_v2_hardening.py
==========================
Mandatory v2.0.0 hardening & verification contract test suite:
  1. Zero-PII Output Contract across profile.json, HTML dossier, and all dbt/diagnostic SQL & YAML.
  2. Stored XSS Escaping in HTML dossier + strict brand color/logo validation + unescaped SQL codegen.
  3. PostgreSQL POSIX word-boundary (\\y) regex compliance (never PCRE \\b), including custom_rules.
  4. DMS Single-Counting (readiness score & remediation hours) and Orphan Row Count vs Distinct Keys.
  5. White-Label Branding CLI/Config precedence and External rules.yaml pack toggles & custom_rules.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

import cli
import flaw_catalog as fc
import profiler
import reportgen
import seed_dirty_gov_db


def _run_cli_audit(monkeypatch: pytest.MonkeyPatch, args: list[str]) -> None:
    """Invoke cli.main() in-process with controlled sys.argv."""
    monkeypatch.setattr(sys, "argv", ["schemafirst", *args])
    cli.main()


def _collect_all_artifact_texts(out_dir: Path) -> dict[str, str]:
    """Reads profile.json, HTML dossier, and all generated .sql/.yml files."""
    artifacts: dict[str, str] = {}
    for root, _, files in os.walk(out_dir):
        for fname in files:
            if fname.endswith((".json", ".html", ".sql", ".yml", ".yaml")):
                fpath = Path(root) / fname
                artifacts[str(fpath.relative_to(out_dir))] = fpath.read_text(encoding="utf-8")
    return artifacts


def _assert_key_absent_recursive(obj: Any, forbidden_key: str, path: str = "$") -> None:
    """Recursively verifies that forbidden_key never appears in any dict inside obj."""
    if isinstance(obj, dict):
        assert forbidden_key not in obj, f"Forbidden key {forbidden_key!r} found at {path}"
        for k, v in obj.items():
            _assert_key_absent_recursive(v, forbidden_key, f"{path}.{k}")
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            _assert_key_absent_recursive(item, forbidden_key, f"{path}[{idx}]")


# =============================================================================
# 1. Zero-PII Output Contract Proof
# =============================================================================

def test_zero_pii_output_contract_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    Runs full `schemafirst audit` on a dirty DB containing raw citizen names, UK NINOs,
    NHS numbers, mixed DOBs, packed free-text notes, and orphan FK keys.
    Proves all detectors fire while ZERO raw values or partial PII masks appear in ANY artifact.
    """
    db_file = tmp_path / "dirty_council.db"
    out_dir = tmp_path / "audit_out"
    seed_dirty_gov_db.seed_database(str(db_file))

    # Augment seeded DB with additional explicit PII / raw-value probes
    conn = sqlite3.connect(str(db_file))
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO citizens VALUES (?, ?, ?, ?)",
        (
            9991,
            "Mary O'Neill",
            "15/03/1982",
            "Compact NINO QQ123456C and NHS 9434765919 | KEY=VAL; SECRET_NOTE=998877",
        ),
    )
    cur.execute(
        "INSERT INTO citizens VALUES (?, ?, ?, ?)",
        (
            9992,
            "Mary Oâ€™Neill",
            "03/25/1982",
            "Standard follow-up\x00note with KEY=VAL; PRIVATE_REF=554433",
        ),
    )
    conn.commit()
    conn.close()

    _run_cli_audit(
        monkeypatch,
        ["audit", "--db", f"sqlite:///{db_file}", "--out-dir", str(out_dir)],
    )

    artifacts = _collect_all_artifact_texts(out_dir)
    assert "profile.json" in artifacts
    assert "migration_readiness_report.html" in artifacts
    assert any(k.endswith(".sql") for k in artifacts), "Expected generated .sql artifacts"
    assert any(k.endswith(".yml") for k in artifacts), "Expected generated .yml artifacts"

    profile = json.loads(artifacts["profile.json"])

    # Non-tautological check: confirm detectors actually fired on the dirty data
    cit_cols = profile["tables"]["citizens"]["columns"]
    assert "cp1252_encoding_ghosts" in [f["flaw_id"] for f in cit_cols["full_name"]["flaws"]]
    assert "mixed_date_locales" in [f["flaw_id"] for f in cit_cols["dob_str"]["flaws"]]
    assert "impossible_or_sentinel_date" in [f["flaw_id"] for f in cit_cols["dob_str"]["flaws"]]
    notes_flaw_ids = {f["flaw_id"] for f in cit_cols["legacy_notes"]["flaws"]}
    assert {"shadow_uk_nino", "shadow_nhs_number", "postgres_null_byte_poison", "legacy_redaction_markers"} <= notes_flaw_ids

    ten_cols = profile["tables"]["tenancies"]["columns"]
    assert "overloaded_freetext_column" in [f["flaw_id"] for f in ten_cols["tenancy_memo"]["flaws"]]
    assert len(profile["tables"]["tenancies"]["dangling_fk_findings"]) >= 1

    # Zero-PII Contract: NONE of these raw values OR partial masks may appear in ANY artifact
    forbidden_substrings = [
        # Raw citizen names
        "Mary Oâ€™Neill",
        "Oâ€™Neill",
        "Mary O'Neill",
        "O'Neill",
        # Raw UK NINOs & partial mask
        "QQ 12 34 56 C",
        "QQ123456C",
        "QQ12****56C",
        # Raw UK NHS numbers & partial mask
        "943 476 5919",
        "9434765919",
        "943****919",
        # Raw DOBs
        "15/03/1982",
        "03/25/1982",
        "24/05/1978",
        "05/24/1978",
        # Packed free-text previews
        "KEY=VAL",
        "SECRET_NOTE=998877",
        "PRIVATE_REF=554433",
        "audit_ref:TEN-1",
        # Orphan FK keys
        "STF-DEAD-01",
        "STF-DEAD-02",
    ]
    for artifact_name, content in artifacts.items():
        for secret in forbidden_substrings:
            assert secret not in content, (
                f"Zero-PII violation: forbidden string {secret!r} leaked into {artifact_name}"
            )

    # sample_orphan_values key must NOT exist anywhere in profile.json
    _assert_key_absent_recursive(profile, "sample_orphan_values")


# =============================================================================
# 2. Stored XSS Escaping, Brand Validation, and Unescaped SQL Codegen Proof
# =============================================================================

def test_stored_xss_escaping_and_unescaped_sql_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    Creates a SQLite DB with XSS payloads in table and column names and runs full `schemafirst audit`
    with XSS payloads in --brand-name and --brand-footer.
    Verifies HTML autoescaping while keeping generated dbt SQL unescaped.
    """
    db_file = tmp_path / "xss_source.db"
    out_dir = tmp_path / "xss_out"
    conn = sqlite3.connect(str(db_file))
    cur = conn.cursor()

    table_xss = "tbl_<img src=x onerror=alert(1)>"
    col_script_notes = "notes_<script>alert(1)</script>"
    col_script_arrears = "arrears_<script>alert(1)</script>_months"

    cur.execute(
        f'CREATE TABLE "{table_xss}" ('
        f'id INTEGER PRIMARY KEY, '
        f'"{col_script_notes}" TEXT, '
        f'"{col_script_arrears}" INTEGER'
        f");"
    )
    cur.execute(
        f'INSERT INTO "{table_xss}" VALUES (?, ?, ?)',
        (1, "corrupt\x00payload", -7),
    )
    conn.commit()
    conn.close()

    _run_cli_audit(
        monkeypatch,
        [
            "audit",
            "--db",
            f"sqlite:///{db_file}",
            "--out-dir",
            str(out_dir),
            "--brand-name",
            "<script>alert(1)</script> Advisory",
            "--brand-footer",
            "Footer <script>alert(1)</script>",
        ],
    )

    html = (out_dir / "migration_readiness_report.html").read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<img src=x onerror=alert(1)>" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html

    # Generated dbt staging SQL must NOT be HTML-escaped
    stg_sql = (out_dir / "generated_dbt" / "models" / "staging" / f"stg_{table_xss}.sql").read_text(encoding="utf-8")
    assert "CHR(0), ''" in stg_sql
    assert "< 0 THEN NULL" in stg_sql
    assert "&lt;" not in stg_sql
    assert "&gt;" not in stg_sql
    assert "&#39;" not in stg_sql


def test_stored_xss_escaping_in_reportgen_table_name_and_evidence(tmp_path: Path):
    """
    Directly tests reportgen.generate_dossier with <script>alert(1)</script> in table name,
    column name, flaw evidence, brand_name, and brand_footer.
    """
    xss = "<script>alert(1)</script>"
    profile = {
        "metadata": {
            "schemafirst_version": "2.0.0",
            "generated_at": "2026-01-01T00:00:00+00:00",
            "source_redacted_url": f"sqlite:///{xss}.db",
            "dialect": "sqlite",
            "sample_size_per_table": 100,
        },
        "executive_summary": {
            "overall_readiness_score": 75,
            "readiness_grade": "AMBER - MODERATE REMEDIATION REQUIRED",
            "total_tables": 1,
            "total_columns": 1,
            "estimated_remediation_hours": 4.0,
            "blocker_counts": {
                "dms_replication_blockers": 1,
                "referential_integrity_failures": 0,
                "semantic_data_rot": 0,
                "shadow_pii_exposures": 0,
                "custom_rules": 0,
            },
        },
        "topology": {
            "load_order": [f"t_{xss}"],
            "fk_cycle_warning": [],
            "mermaid_dag": f"graph TD;\n  t_{xss};",
            "edges": [],
        },
        "tables": {
            f"t_{xss}": {
                "row_count_estimate": 10,
                "row_sample_size": 10,
                "table_readiness_score": 75,
                "declared_pk": ["id"],
                "dms_blockers": [
                    {
                        "blocker_id": "postgres_null_byte_poison",
                        "severity": "CRITICAL",
                        "column": f"c_{xss}",
                        "description": f"Blocker on {xss}",
                    }
                ],
                "declared_fks": [],
                "inferred_fks": [],
                "dangling_fk_findings": [],
                "columns": {
                    f"c_{xss}": {
                        "declared_type": "TEXT",
                        "recommended_target_type": "TEXT",
                        "nullable": True,
                        "is_declared_pk": False,
                        "null_rate": 0.0,
                        "distinct_rate": 1.0,
                        "flaws": [
                            {
                                "flaw_id": "postgres_null_byte_poison",
                                "category": "dms_replication_blockers",
                                "severity": "CRITICAL",
                                "confidence": 1.0,
                                "affected_rate": 0.1,
                                "evidence": f"Evidence with {xss}",
                                "remediation_advice": "Strip NUL bytes.",
                                "dbt_sql_expr": f'REPLACE("c_{xss}", CHR(0), \'\')',
                                "diagnostic_sql": f'SELECT "c_{xss}" FROM "t_{xss}";',
                            }
                        ],
                    }
                },
            }
        },
    }

    out_html = tmp_path / "xss_dossier.html"
    reportgen.generate_dossier(
        profile=profile,
        output_html_path=str(out_html),
        dbt_dir=None,
        branding={
            "brand_name": f"Brand {xss}",
            "brand_color": "#0F172A",
            "brand_footer": f"Footer {xss}",
        },
    )

    html = out_html.read_text(encoding="utf-8")
    assert xss not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


@pytest.mark.parametrize(
    "bad_color",
    [
        "red; background:url(javascript:alert(1))",
        "#12",
        "#1234567",
        "javascript:alert(1)",
        "#GGGGGG",
        "",
    ],
)
def test_invalid_brand_color_raises_value_error(bad_color: str):
    with pytest.raises(ValueError, match="Invalid brand_color"):
        reportgen.resolve_branding(brand_color=bad_color)


@pytest.mark.parametrize(
    "bad_logo",
    [
        "javascript:alert(1)",
        "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
        "ftp://example.com/logo.png",
        "file:///etc/passwd",
        "//evil.example.com/logo.png",
    ],
)
def test_invalid_brand_logo_raises_value_error(bad_logo: str):
    with pytest.raises(ValueError, match="Invalid brand_logo"):
        reportgen.resolve_branding(brand_logo=bad_logo)


# =============================================================================
# 3. PostgreSQL \y Regex Dialect Proof
# =============================================================================

def test_postgresql_word_boundary_regexes_across_catalog_and_generated_sql(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    Verifies REMEDIATION_CATALOG and ALL generated .sql files (staging models & diagnostics)
    use PostgreSQL POSIX word boundaries (\\y) and NEVER PCRE \\b, including custom_rules
    authored with \\b.
    """
    # 1. Inspect REMEDIATION_CATALOG
    for flaw_id, entry in fc.REMEDIATION_CATALOG.items():
        assert r"\b" not in entry["dbt_sql_expr"], f"\\b in REMEDIATION_CATALOG[{flaw_id}].dbt_sql_expr"
        assert r"\b" not in entry["diagnostic_sql"], f"\\b in REMEDIATION_CATALOG[{flaw_id}].diagnostic_sql"
    for wb_rule in ("shadow_uk_nino", "shadow_nhs_number", "legacy_redaction_markers"):
        assert r"\y" in fc.REMEDIATION_CATALOG[wb_rule]["dbt_sql_expr"]
        assert r"\y" in fc.REMEDIATION_CATALOG[wb_rule]["diagnostic_sql"]

    # 2. Run full audit with a custom_rules entry using Python \b (no pg_pattern override)
    db_file = tmp_path / "pg_regex_test.db"
    out_dir = tmp_path / "pg_regex_out"
    seed_dirty_gov_db.seed_database(str(db_file))

    conn = sqlite3.connect(str(db_file))
    conn.execute(
        "INSERT INTO citizens VALUES (?, ?, ?, ?)",
        (5001, "Test User", "1985-05-05", "Account ref ACCT-99999 noted on call"),
    )
    conn.commit()
    conn.close()

    rules_file = tmp_path / "custom_regex_rules.yaml"
    rules_file.write_text(
        yaml.safe_dump(
            {
                "packs": {
                    "aws_dms_cdc": True,
                    "uk_shadow_pii": True,
                    "schema_integrity": True,
                    "data_hygiene": True,
                },
                "custom_rules": [
                    {
                        "rule_id": "account_token_leak",
                        "category": "custom_rules",
                        "severity": "HIGH",
                        "pattern": r"\bACCT-\d{5}\b",
                        "description": "Account token detected in free text.",
                        "remediation_hours": 1.5,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    _run_cli_audit(
        monkeypatch,
        [
            "audit",
            "--db",
            f"sqlite:///{db_file}",
            "--out-dir",
            str(out_dir),
            "--rules",
            str(rules_file),
        ],
    )

    sql_files = list((out_dir / "generated_dbt").rglob("*.sql"))
    assert len(sql_files) >= 8, "Expected staging and diagnostic SQL files for all tables"

    found_y_in_staging = False
    found_y_in_diagnostics = False
    for sql_path in sql_files:
        sql_text = sql_path.read_text(encoding="utf-8")
        assert r"\b" not in sql_text, f"Found PCRE \\b in generated SQL file: {sql_path}"
        if sql_path.parent.name == "staging" and r"\y" in sql_text:
            found_y_in_staging = True
        if sql_path.parent.name == "diagnostics" and r"\y" in sql_text:
            found_y_in_diagnostics = True

    assert found_y_in_staging, "Expected PostgreSQL \\y word boundaries in staging SQL"
    assert found_y_in_diagnostics, "Expected PostgreSQL \\y word boundaries in diagnostic SQL"

    citizens_diag = (out_dir / "generated_dbt" / "diagnostics" / "citizens_audit.sql").read_text(encoding="utf-8")
    assert r"\yACCT-\d{5}\y" in citizens_diag
    assert r"\b" not in citizens_diag


# =============================================================================
# 4. DMS Single-Counting & Orphan Row Count vs Distinct Keys Proof
# =============================================================================

def test_dms_promoted_column_flaw_is_single_counted(tmp_path: Path):
    """
    Minimal 1-table DB with a Primary Key and a single promoted CRITICAL DMS column flaw
    (postgres_null_byte_poison). Asserts table_readiness_score == 75 (one -25 deduction, NOT -50)
    and estimated_remediation_hours == 4.0 (one 4.0h charge, NOT 8.0h).
    """
    db_file = tmp_path / "dms_single.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE dms_single_test (id INTEGER NOT NULL PRIMARY KEY, payload TEXT);")
    conn.execute("INSERT INTO dms_single_test VALUES (?, ?);", (1, "clean_row"))
    conn.execute("INSERT INTO dms_single_test VALUES (?, ?);", (2, "bad\x00row"))
    conn.commit()
    conn.close()

    profile = profiler.run_profile(
        db_url=f"sqlite:///{db_file}",
        out_path=str(tmp_path / "profile.json"),
    )

    tbl = profile["tables"]["dms_single_test"]
    assert len(tbl["dms_blockers"]) == 1
    assert tbl["dms_blockers"][0]["blocker_id"] == "postgres_null_byte_poison"
    assert [f["flaw_id"] for f in tbl["columns"]["payload"]["flaws"]] == ["postgres_null_byte_poison"]

    # Single-counted: 100 - 25 = 75 (NOT 50), 4.0h (NOT 8.0h)
    assert tbl["table_readiness_score"] == 75
    assert profile["executive_summary"]["overall_readiness_score"] == 75
    assert profile["executive_summary"]["estimated_remediation_hours"] == 4.0
    assert profile["executive_summary"]["blocker_counts"]["dms_replication_blockers"] == 1


@pytest.mark.parametrize("declared", [True, False], ids=["declared_fk", "inferred_shadow_fk"])
def test_orphan_row_count_vs_distinct_orphan_keys(tmp_path: Path, declared: bool):
    """
    Constructs a parent/child schema where 2 distinct orphan keys appear across 10 child rows
    (7 rows with ORPHAN-A, 3 rows with ORPHAN-B), testing both declared FKs and inferred shadow FKs.
    Asserts distinct_orphan_keys == 2 and orphan_row_count == 10 in both topology.edges and
    dangling_fk_findings.
    """
    db_file = tmp_path / f"orphans_{'declared' if declared else 'inferred'}.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("PRAGMA foreign_keys = OFF;")

    # Parent table with 10 valid keys (10/12 = 83.3% distinct overlap >= 80% threshold)
    conn.execute("CREATE TABLE parents (parent_code VARCHAR(20) NOT NULL PRIMARY KEY, label VARCHAR(50));")
    conn.executemany(
        "INSERT INTO parents VALUES (?, ?)",
        [(f"P-{i:03d}", f"Parent {i}") for i in range(1, 11)],
    )

    fk_clause = ", FOREIGN KEY (parent_code) REFERENCES parents(parent_code)" if declared else ""
    conn.execute(
        f"CREATE TABLE children ("
        f"child_id INTEGER NOT NULL PRIMARY KEY, "
        f"parent_code VARCHAR(20){fk_clause});"
    )
    child_rows = [(i, f"P-{i:03d}") for i in range(1, 11)]
    child_rows += [(100 + i, "ORPHAN-A") for i in range(7)]
    child_rows += [(200 + i, "ORPHAN-B") for i in range(3)]
    conn.executemany("INSERT INTO children VALUES (?, ?)", child_rows)

    conn.commit()
    conn.close()

    profile_path = tmp_path / f"orphan_profile_{'declared' if declared else 'inferred'}.json"
    profile = profiler.run_profile(db_url=f"sqlite:///{db_file}", out_path=str(profile_path))
    raw_json = profile_path.read_text(encoding="utf-8")

    # Zero-PII: orphan key literals must not leak
    assert "ORPHAN-A" not in raw_json
    assert "ORPHAN-B" not in raw_json
    _assert_key_absent_recursive(profile, "sample_orphan_values")

    finding = next(
        d
        for d in profile["tables"]["children"]["dangling_fk_findings"]
        if d["child_col"] == "parent_code" and d["parent_table"] == "parents"
    )
    assert finding["is_inferred"] is (not declared)
    assert finding["distinct_orphan_keys"] == 2
    assert finding["orphan_row_count"] == 10

    edge = next(
        e
        for e in profile["topology"]["edges"]
        if e["child_table"] == "children" and e["parent_table"] == "parents"
    )
    expected_rel = "DECLARED_FK" if declared else "INFERRED_SHADOW_FK"
    assert edge["relationship_type"] == expected_rel
    assert edge["distinct_orphan_keys"] == 2
    assert edge["orphan_row_count"] == 10
    assert edge["status"] == "ORPHANED_RECORDS_DETECTED"


# =============================================================================
# 5. White-Label Branding & External rules.yaml Overrides End-to-End Proof
# =============================================================================

def test_white_label_branding_cli_overrides_config_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    Runs `schemafirst audit` with both --brand-config (YAML) and CLI branding override flags.
    Proves CLI flags override config values while non-overridden fields (brand_logo) are inherited.
    """
    db_file = tmp_path / "brand_test.db"
    out_dir = tmp_path / "brand_out"
    seed_dirty_gov_db.seed_database(str(db_file))

    brand_cfg = tmp_path / "branding.yaml"
    brand_cfg.write_text(
        yaml.safe_dump(
            {
                "branding": {
                    "brand_name": "Config File Consultancy",
                    "brand_color": "#111111",
                    "brand_logo": "https://example.com/client-logo.png",
                    "brand_footer": "Config File Footer",
                }
            }
        ),
        encoding="utf-8",
    )

    _run_cli_audit(
        monkeypatch,
        [
            "audit",
            "--db",
            f"sqlite:///{db_file}",
            "--out-dir",
            str(out_dir),
            "--brand-config",
            str(brand_cfg),
            "--brand-name",
            "CLI Override Ltd",
            "--brand-color",
            "#1E40AF",
            "--brand-footer",
            "Confidential Client Audit",
        ],
    )

    html = (out_dir / "migration_readiness_report.html").read_text(encoding="utf-8")
    assert "CLI Override Ltd" in html
    assert "#1E40AF" in html
    assert "Confidential Client Audit" in html
    assert "https://example.com/client-logo.png" in html

    # Overridden config values must NOT appear
    assert "Config File Consultancy" not in html
    assert "#111111" not in html
    assert "Config File Footer" not in html


def test_external_rules_yaml_pack_toggles_and_custom_rules_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    Runs `schemafirst audit --rules <custom_rules.yaml>` with packs.uk_shadow_pii: false
    and a custom_rules entry matching ACCT-99999.
    Asserts UK Shadow PII rules are suppressed while the custom rule fires with Zero-PII
    count/rate evidence and valid PostgreSQL \\y diagnostic SQL.
    """
    db_file = tmp_path / "rules_override.db"
    out_dir = tmp_path / "rules_out"
    seed_dirty_gov_db.seed_database(str(db_file))

    conn = sqlite3.connect(str(db_file))
    conn.execute(
        "INSERT INTO citizens VALUES (?, ?, ?, ?)",
        (7777, "Alice Example", "1990-01-01", "Migrated account ACCT-99999 pending review"),
    )
    conn.commit()
    conn.close()

    custom_rules_yaml = tmp_path / "custom_rules.yaml"
    custom_rules_yaml.write_text(
        yaml.safe_dump(
            {
                "profiling_sample_size": 1000,
                "packs": {
                    "aws_dms_cdc": True,
                    "uk_shadow_pii": False,
                    "schema_integrity": True,
                    "data_hygiene": True,
                },
                "custom_rules": [
                    {
                        "rule_id": "legacy_account_token",
                        "category": "custom_rules",
                        "severity": "HIGH",
                        "pattern": r"\bACCT-\d{5}\b",
                        "column_name_keywords": ["notes"],
                        "description": "Legacy account token exposed in free-text notes.",
                        "remediation_hours": 1.5,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    _run_cli_audit(
        monkeypatch,
        [
            "audit",
            "--db",
            f"sqlite:///{db_file}",
            "--out-dir",
            str(out_dir),
            "--rules",
            str(custom_rules_yaml),
        ],
    )

    artifacts = _collect_all_artifact_texts(out_dir)
    profile = json.loads(artifacts["profile.json"])

    # Collect all fired flaw_ids across the entire database
    all_flaw_ids = {
        f["flaw_id"]
        for tbl in profile["tables"].values()
        for col in tbl["columns"].values()
        for f in col["flaws"]
    }
    assert "shadow_uk_nino" not in all_flaw_ids
    assert "shadow_nhs_number" not in all_flaw_ids
    assert "legacy_redaction_markers" not in all_flaw_ids
    assert profile["executive_summary"]["blocker_counts"]["shadow_pii_exposures"] == 0

    # Custom rule must fire on citizens.legacy_notes
    notes_flaws = profile["tables"]["citizens"]["columns"]["legacy_notes"]["flaws"]
    custom_flaw = next((f for f in notes_flaws if f["flaw_id"] == "legacy_account_token"), None)
    assert custom_flaw is not None, "Custom rule legacy_account_token must fire on citizens.legacy_notes"
    assert "[legacy_account_token]" in custom_flaw["evidence"]
    assert "1/201" in custom_flaw["evidence"]
    assert r"\yACCT-\d{5}\y" in custom_flaw["diagnostic_sql"]
    assert r"\b" not in custom_flaw["diagnostic_sql"]
    assert profile["executive_summary"]["blocker_counts"]["custom_rules"] == 1

    # Zero-PII: matched token literal must never appear in any artifact
    for artifact_name, content in artifacts.items():
        assert "ACCT-99999" not in content, f"Custom rule token leaked into {artifact_name}"

    # Verify diagnostic SQL file uses \y and not \b
    diag_sql = artifacts[os.path.join("generated_dbt", "diagnostics", "citizens_audit.sql")]
    assert r"\yACCT-\d{5}\y" in diag_sql
    assert r"\b" not in diag_sql
    assert re.search(r"~[*]\s*'[^']*\\yACCT-\\d\{5\}\\y[^']*'", diag_sql)
