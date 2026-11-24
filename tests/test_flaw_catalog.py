from flaw_catalog import (
    REMEDIATION_CATALOG,
    detect_column_flaws,
    detect_cp1252_encoding_ghosts,
    detect_duplicate_key_values,
    detect_impossible_or_sentinel_dates,
    detect_legacy_redaction_markers,
    detect_mixed_date_locales,
    detect_negative_where_unsigned_expected,
    detect_non_numeric_in_numeric_col,
    detect_overloaded_freetext,
    detect_postgres_null_byte_poison,
    detect_shadow_nhs_number,
    detect_shadow_uk_nino,
    detect_text_currency,
    detect_whitespace_padded_keys,
    is_pack_enabled,
    is_rule_enabled,
    to_pg_regex,
)


def test_detect_text_currency():
    values = ["$10.50", "€ 5", "100.00", "USD 50", None]
    match = detect_text_currency(values)
    assert match is not None
    assert match.flaw_id == "text_currency"
    assert match.confidence > 0.0
    assert "$10.50" not in match.evidence
    assert "USD 50" not in match.evidence


def test_detect_text_currency_no_match():
    values = ["10.50", "5", "100.00", "50", None]
    match = detect_text_currency(values)
    assert match is None


def test_detect_impossible_dates():
    # 'dob' matches the column_name_keywords for dates
    values = ["1990-02-30", "2020-13-01", "99/99/9999", "1990-01-01"]
    match = detect_impossible_or_sentinel_dates(values, "customer_dob")
    assert match is not None
    assert match.flaw_id == "impossible_or_sentinel_date"
    assert match.confidence > 0.0
    assert "1990-02-30" not in match.evidence
    assert "99/99/9999" not in match.evidence


def test_detect_impossible_dates_clean():
    values = ["1990-01-01", "2020-12-31"]
    match = detect_impossible_or_sentinel_dates(values, "customer_dob")
    assert match is None


def test_detect_non_numeric_in_numeric_col():
    values = ["100", "50.5", "abc", "N/A"]
    match = detect_non_numeric_in_numeric_col(values, "account_balance")
    assert match is not None
    assert match.flaw_id == "non_numeric_in_numeric_col"
    assert "abc" not in match.evidence


def test_detect_negative_unsigned():
    values = ["100", "50.5", "-10", "0"]
    match = detect_negative_where_unsigned_expected(values, "item_quantity")
    assert match is not None
    assert match.flaw_id == "negative_unsigned_field"
    assert "-10" not in match.evidence


def test_detect_negative_unsigned_clean():
    values = ["100", "50.5", "10", "0"]
    match = detect_negative_where_unsigned_expected(values, "item_quantity")
    assert match is None


def test_shadow_pii_detectors_zero_pii_evidence():
    nino_match = detect_shadow_uk_nino(["Claimant NINO QQ123456C on file", "clean"], "legacy_notes")
    assert nino_match is not None
    assert nino_match.flaw_id == "shadow_uk_nino"
    assert "1/2" in nino_match.evidence
    assert "QQ123456C" not in nino_match.evidence
    assert "QQ12****56C" not in nino_match.evidence

    nhs_match = detect_shadow_nhs_number(["Patient NHS 943 476 5919 verified", "clean"], "legacy_notes")
    assert nhs_match is not None
    assert nhs_match.flaw_id == "shadow_nhs_number"
    assert "1/2" in nhs_match.evidence
    assert "943 476 5919" not in nhs_match.evidence
    assert "9434765919" not in nhs_match.evidence
    assert "943****919" not in nhs_match.evidence

    redaction_match = detect_legacy_redaction_markers(["RECORD DECEASED - REDACTED", "ok"], "legacy_notes")
    assert redaction_match is not None
    assert redaction_match.flaw_id == "legacy_redaction_markers"
    assert "RECORD DECEASED" not in redaction_match.evidence


def test_dms_and_hygiene_detectors_zero_pii_evidence():
    nul_match = detect_postgres_null_byte_poison(["Mary O'Neill\x00secret", "normal"])
    assert nul_match is not None
    assert "Mary" not in nul_match.evidence
    assert "O'Neill" not in nul_match.evidence

    mojibake_match = detect_cp1252_encoding_ghosts(["Mary Oâ€™Neill", "John Smith"])
    assert mojibake_match is not None
    assert "Mary" not in mojibake_match.evidence
    assert "Oâ€™Neill" not in mojibake_match.evidence

    mixed_dates = detect_mixed_date_locales(["15/03/1982", "03/25/1982"], "dob_str")
    assert mixed_dates is not None
    assert "15/03/1982" not in mixed_dates.evidence
    assert "03/25/1982" not in mixed_dates.evidence

    packed = detect_overloaded_freetext(["KEY:VAL secret_token:ABC123", "plain"], "case_notes")
    assert packed is not None
    assert "secret_token" not in packed.evidence
    assert "ABC123" not in packed.evidence

    dupes = detect_duplicate_key_values(["KEY-SECRET-1", "KEY-SECRET-1", "KEY-SECRET-2"], is_pk_col=True)
    assert dupes is not None
    assert "KEY-SECRET-1" not in dupes.evidence

    padded = detect_whitespace_padded_keys(["  KEY-SECRET-1  ", "KEY-SECRET-2"], is_key_col=True)
    assert padded is not None
    assert "KEY-SECRET-1" not in padded.evidence


def test_to_pg_regex_and_remediation_catalog_no_pcre_word_boundary():
    assert to_pg_regex(r"\bACCT-\d{5}\b") == r"\yACCT-\d{5}\y"
    for flaw_id, entry in REMEDIATION_CATALOG.items():
        assert r"\b" not in entry["dbt_sql_expr"], f"PCRE \\b found in REMEDIATION_CATALOG[{flaw_id}].dbt_sql_expr"
        assert r"\b" not in entry["diagnostic_sql"], f"PCRE \\b found in REMEDIATION_CATALOG[{flaw_id}].diagnostic_sql"


def test_pack_toggles_and_custom_rules_in_detect_column_flaws():
    rules = {
        "packs": {
            "uk_shadow_pii": False,
            "aws_dms_cdc": True,
            "schema_integrity": True,
            "data_hygiene": True,
        },
        "custom_rules": [
            {
                "rule_id": "custom_acct_token",
                "category": "custom_rules",
                "severity": "high",
                "pattern": r"\bACCT-\d{5}\b",
                "description": "Internal account reference detected.",
                "remediation_hours": 1.5,
            }
        ],
    }
    assert is_pack_enabled("uk_shadow_pii", rules) is False
    assert is_rule_enabled("shadow_uk_nino", rules) is False
    assert is_rule_enabled("shadow_nhs_number", rules) is False
    assert is_rule_enabled("legacy_redaction_markers", rules) is False

    flaws = detect_column_flaws(
        values=["NINO QQ123456C and NHS 943 476 5919 with ACCT-99999"],
        col_name="case_notes",
        declared_type="TEXT",
        is_pk=False,
        rules_dict=rules,
        table_name="tickets",
    )
    flaw_ids = [f["flaw_id"] for f in flaws]
    assert "shadow_uk_nino" not in flaw_ids
    assert "shadow_nhs_number" not in flaw_ids
    assert "custom_acct_token" in flaw_ids

    custom_flaw = next(f for f in flaws if f["flaw_id"] == "custom_acct_token")
    assert "ACCT-99999" not in custom_flaw["evidence"]
    assert "1/1" in custom_flaw["evidence"]
    assert r"\yACCT-\d{5}\y" in custom_flaw["diagnostic_sql"]
    assert r"\b" not in custom_flaw["diagnostic_sql"]

