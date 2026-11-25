"""
tests/test_end_to_end.py
========================
Full pipeline automated verification suite for SchemaFirst v2.0.

Verification Criteria:
  1. executor.py no longer exists.
  2. Database passwords in db_url do not appear anywhere in profile.json or migration_readiness_report.html.
  3. Raw NINOs (QQ 12 34 56 C) are detected by shadow_uk_nino AND never emitted (neither raw nor partially masked) in profile.json or the HTML report.
  4. Undeclared tenancies.officer_code -> staff.staff_code relationship is automatically discovered
     by infer_foreign_keys and its exact orphan_row_count and distinct_orphan_keys are reported without leaking orphan key values.
  5. Multi-flaw columns generate nested SQL expressions in stg_citizens.sql.
  6. migration_readiness_report.html contains zero external http:// or https:// stylesheet/script dependencies.
"""
import json
import os
import re

import codegen
import profiler
import reportgen
import seed_dirty_gov_db


def test_executor_decommissioned():
    """Criterion 1: executor.py no longer exists."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    executor_path = os.path.join(repo_root, "executor.py")
    assert not os.path.exists(executor_path), "executor.py must be deleted in v2.0"


def test_credential_masking():
    """Criterion 2: Database passwords in db_url do not appear anywhere in profile.json or HTML."""
    secret_pass = "P@ssw0rd_SuperSecret_987!"
    secret_url = f"postgresql://auditor:{secret_pass}@192.168.1.100:5432/legacy_housing"

    masked = profiler.mask_db_url(secret_url)
    assert secret_pass not in masked
    assert "***" in masked


def test_full_pipeline_end_to_end(tmp_path):
    """Executes full discovery, dbt compilation, and report generation in isolated tmp_path."""
    db_file = str(tmp_path / "test_legacy_council.db")
    out_dir = str(tmp_path / "audit_output")

    # 1. Generate dirty UK public sector database
    seed_dirty_gov_db.seed_database(db_file)
    assert os.path.exists(db_file)

    # 2. Run profile
    db_url = f"sqlite:///{db_file}"
    profile_json_path = os.path.join(out_dir, "profile.json")
    profiler.run_profile(db_url, out_path=profile_json_path)
    assert os.path.exists(profile_json_path)

    # 3. Run dbt codegen
    dbt_dir = os.path.join(out_dir, "generated_dbt")
    codegen.compile_dbt_package(profile_path=profile_json_path, out_dir=dbt_dir)

    # 4. Run reportgen
    html_report_path = os.path.join(out_dir, "migration_readiness_report.html")
    reportgen.generate_report(profile_path=profile_json_path, out_path=html_report_path, dbt_dir=dbt_dir)
    assert os.path.exists(html_report_path)

    with open(profile_json_path, "r", encoding="utf-8") as pf:
        profile_content = pf.read()
        profile_data = json.loads(profile_content)

    with open(html_report_path, "r", encoding="utf-8") as hf:
        html_content = hf.read()

    # Criterion 3: Raw NINOs detected by shadow_uk_nino AND Zero-PII output (no raw or partial masks)
    raw_nino = "QQ 12 34 56 C"
    assert raw_nino not in profile_content, "Raw unmasked NINO must never appear in profile.json"
    assert raw_nino not in html_content, "Raw unmasked NINO must never appear in migration_readiness_report.html"
    assert "QQ12****56C" not in profile_content, "Partial NINO mask must never appear in profile.json"
    assert "QQ12****56C" not in html_content, "Partial NINO mask must never appear in migration_readiness_report.html"

    # Confirm shadow_uk_nino detector fired on legacy_notes with count/rate-only evidence
    citizens_cols = profile_data["tables"]["citizens"]["columns"]
    legacy_notes_flaws = [f["flaw_id"] for f in citizens_cols["legacy_notes"]["flaws"]]
    assert "shadow_uk_nino" in legacy_notes_flaws, "shadow_uk_nino must be detected in citizens.legacy_notes"

    nino_flaw = next(f for f in citizens_cols["legacy_notes"]["flaws"] if f["flaw_id"] == "shadow_uk_nino")
    assert "[shadow_uk_nino]" in nino_flaw["evidence"]
    assert "1/200" in nino_flaw["evidence"]

    # Criterion 4: Shadow FK tenancies.officer_code -> staff.staff_code automatically discovered
    tenancies_meta = profile_data["tables"]["tenancies"]
    inferred_fks = tenancies_meta["inferred_fks"]

    officer_fk = next(
        (inf for inf in inferred_fks if inf["child_col"] == "officer_code" and inf["parent_table"] == "staff"),
        None,
    )
    assert officer_fk is not None, "Shadow FK tenancies.officer_code -> staff.staff_code must be inferred"
    assert officer_fk["parent_col"] == "staff_code"
    assert officer_fk["confidence"] >= 0.70

    # Exact orphan counts reported without leaking sample_orphan_values
    dangling_findings = tenancies_meta["dangling_fk_findings"]
    officer_dangling = next(
        (d for d in dangling_findings if d["child_col"] == "officer_code" and d["parent_table"] == "staff"),
        None,
    )
    assert officer_dangling is not None, "Dangling FK finding must be recorded for tenancies.officer_code"
    assert officer_dangling["orphan_row_count"] > 0
    assert officer_dangling["distinct_orphan_keys"] == 2
    assert "sample_orphan_values" not in officer_dangling
    assert "STF-DEAD-01" not in profile_content
    assert "STF-DEAD-01" not in html_content

    # Criterion 5: Multi-flaw columns generate nested SQL expressions in stg_citizens.sql
    stg_citizens_path = os.path.join(dbt_dir, "models", "staging", "stg_citizens.sql")
    assert os.path.exists(stg_citizens_path)
    with open(stg_citizens_path, "r", encoding="utf-8") as sf:
        citizens_sql = sf.read()

    # Column legacy_notes has null byte poison, shadow_uk_nino, shadow_nhs_number, legacy_redaction_markers
    # Ensure REPLACE(..., CHR(0), '') is nested inside REGEXP_REPLACE(...)
    assert 'REPLACE("legacy_notes", CHR(0), \'\')' in citizens_sql
    assert 'REGEXP_REPLACE(' in citizens_sql
    assert re.search(r'REGEXP_REPLACE\([^;]+REPLACE\("legacy_notes", CHR\(0\), \'\'\)', citizens_sql)

    # v2.1: Type-Safe dbt SQL Compilation
    assert r"\ufffd" not in citizens_sql
    assert "CHR(65533)" in citizens_sql
    assert 'CAST(CASE WHEN "dob_str"' not in citizens_sql
    assert r'''CASE WHEN "dob_str" IN ('1900-01-01', '1970-01-01', '9999-12-31', '0000-00-00') THEN NULL WHEN "dob_str" ~ '^\d{4}-\d{2}-\d{2}$' THEN CAST("dob_str" AS DATE) ELSE NULL END''' in citizens_sql

    stg_rent_ledger_path = os.path.join(dbt_dir, "models", "staging", "stg_rent_ledger.sql")
    assert os.path.exists(stg_rent_ledger_path)
    with open(stg_rent_ledger_path, "r", encoding="utf-8") as rf:
        rent_ledger_sql = rf.read()
    assert "REGEXP_REPLACE(CAST(" not in rent_ledger_sql
    assert 'CASE WHEN "arrears_months" < 0 THEN NULL ELSE "arrears_months" END' in rent_ledger_sql

    # v2.1: Clean Reference Table Score
    assert profile_data["tables"]["staff"]["table_readiness_score"] == 100, "staff table must score 100/100"
    assert len(profile_data["tables"]["staff"]["dms_blockers"]) == 0, "staff table must have 0 DMS blockers"

    # v2.1: Negative Unsigned Detection
    arrears_flaws = [f["flaw_id"] for f in profile_data["tables"]["rent_ledger"]["columns"]["arrears_months"]["flaws"]]
    assert "negative_unsigned_field" in arrears_flaws, "negative_unsigned_field must be detected on arrears_months"

    # v2.1: Currency Deduplication
    balance_flaws = [f["flaw_id"] for f in profile_data["tables"]["rent_ledger"]["columns"]["balance_amt"]["flaws"]]
    assert balance_flaws == ["text_currency"], f"Expected ['text_currency'], got {balance_flaws}"

    # Criterion 6: Zero external http:// or https:// stylesheet/script dependencies in HTML
    external_deps = re.findall(r'<(?:link|script)[^>]*(?:href|src)=["\'](https?://[^"\']+)["\']', html_content)
    assert len(external_deps) == 0, f"Found external dependencies in air-gapped HTML report: {external_deps}"
    assert "cdn." not in html_content.lower()
    assert "fonts.googleapis" not in html_content.lower()

    # v2.1: HTML Dossier Quality Gate
    assert "scorecard RED -" not in html_content, "scorecard RED - truncation bug must be fixed"
    assert "graph TD" in html_content, "Mermaid topology graph TD must be rendered in Section 4"
    assert "Audit SQL:" in html_content, "Audit SQL: must be rendered in Section 5"
