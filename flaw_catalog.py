"""
flaw_catalog.py
================
A deterministic, version-controlled taxonomy of legacy-data corruption patterns.
Pairs deterministic detectors across Enterprise Migration Pillars with:
  1. Pure Python detectors returning confidence scores, affected rates, and Zero-PII evidence.
  2. dbt SQL expressions to sanitize the column in dbt staging models.
  3. Read-only diagnostic SQL queries for DBAs to isolate affected rows.
  4. Plain-English remediation advice for executive dossiers.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import yaml

_RULES_CACHE: dict[tuple[str, float], dict] = {}

RULE_PACK_MAP: dict[str, str] = {
    # aws_dms_cdc
    "postgres_null_byte_poison": "aws_dms_cdc",
    "cp1252_encoding_ghosts": "aws_dms_cdc",
    "legacy_proprietary_datatype": "aws_dms_cdc",
    # schema_integrity
    "duplicate_key_values": "schema_integrity",
    "whitespace_padded_keys": "schema_integrity",
    # data_hygiene
    "control_chars": "data_hygiene",
    "text_currency": "data_hygiene",
    "non_numeric_in_numeric_col": "data_hygiene",
    "impossible_or_sentinel_date": "data_hygiene",
    "mixed_date_locales": "data_hygiene",
    "negative_unsigned_field": "data_hygiene",
    "overloaded_freetext_column": "data_hygiene",
    # uk_shadow_pii
    "shadow_uk_nino": "uk_shadow_pii",
    "shadow_nhs_number": "uk_shadow_pii",
    "legacy_redaction_markers": "uk_shadow_pii",
}

_RULE_LEAF_KEYS = frozenset({
    "enabled",
    "severity",
    "description",
    "pattern",
    "pg_pattern",
    "column_name_keywords",
    "proprietary_types",
    "ghost_sequences",
    "null_date_sentinels",
    "packed_pair_pattern",
    "pack",
    "remediation_hours",
})


def resolve_rules_path(rules_path: str | None = None) -> str | None:
    """Resolves rules.yaml via explicit path -> package dir -> sys.prefix share -> CWD."""
    if rules_path is not None:
        return rules_path
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules.yaml"),
        os.path.join(sys.prefix, "share", "crucible-audit", "rules.yaml"),
        os.path.join(os.getcwd(), "rules.yaml"),
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return None


def load_rules(rules_path: str | None = None) -> dict:
    """Loads rules from rules.yaml with (abs_path, mtime) caching. Avoids stale cache entries."""
    resolved = resolve_rules_path(rules_path)
    if not resolved or not os.path.exists(resolved):
        return {}
    abs_path = os.path.abspath(resolved)
    mtime = os.path.getmtime(abs_path)
    cache_key = (abs_path, mtime)
    if cache_key in _RULES_CACHE:
        return _RULES_CACHE[cache_key]
    with open(abs_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    _RULES_CACHE[cache_key] = data
    return data


def _get_flattened_rules(rules_dict: dict | None = None) -> dict[str, dict]:
    """Flattens category-nested or flat rules into {rule_id: rule_meta}."""
    rules = rules_dict if rules_dict is not None else load_rules()
    flat: dict[str, dict] = {}
    rule_blocks = rules.get("rules", {})
    if isinstance(rule_blocks, dict):
        for key, val in rule_blocks.items():
            if isinstance(val, dict):
                # Support direct flat rules.<rule_id> overrides as well as rules.<category>.<rule_id>
                if key in RULE_PACK_MAP or any(k in _RULE_LEAF_KEYS for k in val):
                    rule_info = dict(val)
                    rule_info["id"] = key
                    flat[key] = rule_info
                else:
                    for rule_id, rule_conf in val.items():
                        if isinstance(rule_conf, dict):
                            rule_info = dict(rule_conf)
                            rule_info["category"] = key
                            rule_info["id"] = rule_id
                            flat[rule_id] = rule_info
            elif isinstance(val, list):
                for r in val:
                    if isinstance(r, dict) and "id" in r:
                        rule_info = dict(r)
                        rule_info["category"] = key
                        flat[r["id"]] = rule_info
    elif isinstance(rule_blocks, list):
        for r in rule_blocks:
            if isinstance(r, dict) and "id" in r:
                flat[r["id"]] = dict(r)
    return flat


def _get_rule_config(flaw_id: str, rules_dict: dict | None = None) -> dict:
    flat = _get_flattened_rules(rules_dict)
    return flat.get(flaw_id, {})


def is_pack_enabled(pack_name: str, rules_dict: dict | None = None) -> bool:
    """Checks whether a rule pack is enabled in rules.yaml (defaults to True)."""
    rules = rules_dict if rules_dict is not None else load_rules()
    packs = rules.get("packs")
    if not isinstance(packs, dict):
        return True
    return bool(packs.get(pack_name, True))


def is_rule_enabled(rule_id: str, rules_dict: dict | None = None) -> bool:
    """Checks both pack-level toggle and per-rule enabled flag (defaults to True)."""
    rules = rules_dict if rules_dict is not None else load_rules()
    cfg = _get_rule_config(rule_id, rules)
    pack_name = cfg.get("pack") or RULE_PACK_MAP.get(rule_id)
    if pack_name and not is_pack_enabled(pack_name, rules):
        return False
    return cfg.get("enabled") is not False


def to_pg_regex(pattern: str) -> str:
    """Converts Python word boundaries (\\b) to PostgreSQL ARE word boundaries (\\y)."""
    return pattern.replace(r"\b", r"\y")


# Precompiled static regexes
CONTROL_CHAR_RE = re.compile(r"[\x01-\x08\x0b\x0c\x0e-\x1f]")
PURE_NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SLASH_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
MOJIBAKE_RE = re.compile(r"(â€™|â€“|â€œ|Ã©|Ã£|\ufffd)")
UK_NINO_RE = re.compile(
    r"(?i)\b(?:QQ|(?!BG|GB|NK|KN|TN|NT|ZZ)[A-CEGHJ-PR-TW-ZQ][A-CEGHJ-NPR-TW-ZQ])\s*\d{2}\s*\d{2}\s*\d{2}\s*[A-D]\b"
)
NHS_NUMBER_RE = re.compile(r"\b(\d{3})\s?(\d{3})\s?(\d{4})\b")
REDACTION_MARKERS_RE = re.compile(
    r"(?i)\b(REDACTED|GDPR_DELETED|DECEASED|DO NOT CONTACT|TEST TEST|XXXX+)\b"
)
CURRENCY_RE = re.compile(
    r"^\s*-?\s*[\$£€]\s*-?[\d,]+\.?\d*\s*(USD|EUR|GBP)?\s*$"
    r"|^\s*-?[\d,]+\.?\d*\s*(USD|EUR|GBP)\s*$",
    re.IGNORECASE,
)


@dataclass
class FlawMatch:
    flaw_id: str
    confidence: float
    evidence: str
    affected_rate: float = 0.0


def _sample_strs(values: list) -> list[str]:
    """Filters non-null string samples."""
    return [str(v) for v in values if v is not None and isinstance(v, str)]


def _sample_numeric(values: list) -> list[float]:
    """Inspects int, float, Decimal, and numeric strings alike."""
    nums: list[float] = []
    for v in values:
        if v is None:
            continue
        if isinstance(v, (int, float, Decimal)):
            nums.append(float(v))
        elif isinstance(v, str):
            clean_str = v.replace(",", "").strip()
            if PURE_NUMBER_RE.match(clean_str):
                try:
                    nums.append(float(clean_str))
                except ValueError:
                    pass
    return nums


def validate_nhs_modulus11(candidate: str) -> bool:
    """
    Validates a 10-digit UK NHS number using official Modulus 11 algorithm:
    Strip spaces/hyphens. Digits 1-9 weighted by 10,9,8,7,6,5,4,3,2.
    Check digit = 11 - (Total % 11). If 11 -> 0, if 10 -> invalid.
    Must equal 10th digit.
    """
    digits = re.sub(r"[\s-]", "", candidate)
    if len(digits) != 10 or not digits.isdigit():
        return False
    weights = [10, 9, 8, 7, 6, 5, 4, 3, 2]
    total = sum(int(digits[i]) * weights[i] for i in range(9))
    remainder = total % 11
    check_digit = 11 - remainder
    if check_digit == 11:
        check_digit = 0
    elif check_digit == 10:
        return False
    return check_digit == int(digits[9])


def _is_real_calendar_date(y: int, m: int, d: int) -> bool:
    import datetime as _dt
    try:
        _dt.date(int(y), int(m), int(d))
        return True
    except (ValueError, OverflowError):
        return False


# ---------------------------------------------------------------------------
# Pillar 1: DMS Replication Blockers
# ---------------------------------------------------------------------------

def detect_postgres_null_byte_poison(values: list, rules_dict=None) -> FlawMatch | None:
    strs = _sample_strs(values)
    if not strs:
        return None
    hits = [s for s in strs if "\x00" in s]
    if hits:
        rate = len(hits) / len(strs)
        return FlawMatch(
            flaw_id="postgres_null_byte_poison",
            confidence=1.0,
            evidence=(
                f"[postgres_null_byte_poison] {len(hits)}/{len(strs)} ({rate * 100:.1f}%) "
                f"sampled rows contain ASCII NUL (0x00) bytes"
            ),
            affected_rate=round(rate, 2),
        )
    return None


def detect_cp1252_encoding_ghosts(values: list, rules_dict=None) -> FlawMatch | None:
    strs = _sample_strs(values)
    if not strs:
        return None
    hits = [s for s in strs if MOJIBAKE_RE.search(s)]
    if hits:
        rate = len(hits) / len(strs)
        return FlawMatch(
            flaw_id="cp1252_encoding_ghosts",
            confidence=0.95,
            evidence=(
                f"[cp1252_encoding_ghosts] {len(hits)}/{len(strs)} ({rate * 100:.1f}%) "
                f"sampled rows contain UTF-8/CP1252 mojibake artifacts"
            ),
            affected_rate=round(rate, 2),
        )
    return None


def detect_legacy_proprietary_datatype(declared_type: str, rules_dict=None) -> FlawMatch | None:
    cfg = _get_rule_config("legacy_proprietary_datatype", rules_dict)
    types_list = cfg.get("proprietary_types", [
        "MONEY", "SMALLMONEY", "SQL_VARIANT", "IMAGE", "NTEXT", "LONG", "RAW", "BFILE"
    ])
    dt = str(declared_type).upper()
    for ptype in types_list:
        if ptype in dt:
            return FlawMatch(
                flaw_id="legacy_proprietary_datatype",
                confidence=1.0,
                evidence=(
                    f"[legacy_proprietary_datatype] Declared column type '{declared_type}' "
                    f"matches legacy engine proprietary type '{ptype}'"
                ),
                affected_rate=1.0,
            )
    return None


# ---------------------------------------------------------------------------
# Pillar 2: Referential & Key Integrity
# ---------------------------------------------------------------------------

def detect_duplicate_key_values(values: list, is_pk_col: bool, rules_dict=None) -> FlawMatch | None:
    if not is_pk_col:
        return None
    non_null = [v for v in values if v is not None]
    if not non_null:
        return None
    try:
        unique_count = len(set(non_null))
    except TypeError:
        unique_count = len({str(v) for v in non_null})
    dupes = len(non_null) - unique_count
    if dupes > 0:
        rate = dupes / len(non_null)
        return FlawMatch(
            flaw_id="duplicate_key_values",
            confidence=1.0,
            evidence=(
                f"[duplicate_key_values] {dupes}/{len(non_null)} ({rate * 100:.1f}%) "
                f"non-null sampled primary key values are duplicates"
            ),
            affected_rate=round(rate, 2),
        )
    return None


def detect_whitespace_padded_keys(values: list, is_key_col: bool, rules_dict=None) -> FlawMatch | None:
    if not is_key_col:
        return None
    strs = _sample_strs(values)
    if not strs:
        return None
    hits = [s for s in strs if s != s.strip()]
    if hits:
        rate = len(hits) / len(strs)
        return FlawMatch(
            flaw_id="whitespace_padded_keys",
            confidence=0.95,
            evidence=(
                f"[whitespace_padded_keys] {len(hits)}/{len(strs)} ({rate * 100:.1f}%) "
                f"sampled key values contain leading/trailing whitespace or CHAR padding"
            ),
            affected_rate=round(rate, 2),
        )
    return None


# ---------------------------------------------------------------------------
# Pillar 3: Semantic Data Rot
# ---------------------------------------------------------------------------

def detect_control_chars(values: list, rules_dict=None) -> FlawMatch | None:
    strs = _sample_strs(values)
    if not strs:
        return None
    # Exclude \x00 as that is postgres_null_byte_poison
    hits = [s for s in strs if CONTROL_CHAR_RE.search(s) and "\x00" not in s]
    if hits:
        rate = len(hits) / len(strs)
        conf = min(1.0, rate + 0.3)
        return FlawMatch(
            flaw_id="control_chars",
            confidence=round(conf, 2),
            evidence=(
                f"[control_chars] {len(hits)}/{len(strs)} ({rate * 100:.1f}%) "
                f"sampled rows contain invisible ASCII control bytes"
            ),
            affected_rate=round(rate, 2),
        )
    return None


def detect_text_currency(values: list, rules_dict=None) -> FlawMatch | None:
    strs = _sample_strs(values)
    if not strs:
        return None
    matches = [s for s in strs if CURRENCY_RE.match(s) and not PURE_NUMBER_RE.match(s.strip())]
    pure_num = sum(1 for s in strs if PURE_NUMBER_RE.match(s.strip()))
    if matches and pure_num < len(strs):
        rate = len(matches) / len(strs)
        return FlawMatch(
            flaw_id="text_currency",
            confidence=round(rate, 2),
            evidence=(
                f"[text_currency] {len(matches)}/{len(strs)} ({rate * 100:.1f}%) "
                f"sampled rows contain formatted currency strings"
            ),
            affected_rate=round(rate, 2),
        )
    return None


def detect_non_numeric_in_numeric_col(values: list, col_name: str, rules_dict=None) -> FlawMatch | None:
    cfg = _get_rule_config("non_numeric_in_numeric_col", rules_dict)
    keywords = cfg.get("column_name_keywords", [
        "balance", "amount", "total", "price", "qty", "quantity", "cost", "fee", "rate"
    ])
    if not any(tok in col_name.lower() for tok in keywords):
        return None
    strs = _sample_strs(values)
    if not strs:
        return None
    bad = [
        s for s in strs
        if not PURE_NUMBER_RE.match(s.strip())
        and not CURRENCY_RE.match(s.strip())
        and s.strip().lower() not in {"null", "none", "n/a", ""}
    ]
    if not bad:
        return None
    rate = len(bad) / len(strs)
    return FlawMatch(
        flaw_id="non_numeric_in_numeric_col",
        confidence=round(rate, 2),
        evidence=(
            f"[non_numeric_in_numeric_col] {len(bad)}/{len(strs)} ({rate * 100:.1f}%) "
            f"sampled rows in column '{col_name}' contain non-numeric values"
        ),
        affected_rate=round(rate, 2),
    )


def detect_impossible_or_sentinel_dates(values: list, col_name: str, rules_dict=None) -> FlawMatch | None:
    cfg = _get_rule_config("impossible_or_sentinel_date", rules_dict)
    keywords = cfg.get("column_name_keywords", [
        "date", "dob", "created", "updated", "time", "start", "end", "expiry"
    ])
    if not any(tok in col_name.lower() for tok in keywords):
        return None
    strs = _sample_strs(values)
    if not strs:
        return None
    sentinels = set(cfg.get("null_date_sentinels", ["1900-01-01", "1970-01-01", "9999-12-31", "0000-00-00"]))
    bad_count = 0
    for v in strs:
        v_clean = v.strip()
        if v_clean in sentinels:
            bad_count += 1
            continue
        m_slash = SLASH_DATE_RE.match(v_clean)
        if m_slash:
            p1, p2, y = int(m_slash.group(1)), int(m_slash.group(2)), int(m_slash.group(3))
            # Test if calendrically valid in either DD/MM or MM/DD
            valid = _is_real_calendar_date(y, p2, p1) or _is_real_calendar_date(y, p1, p2)
            if not valid:
                bad_count += 1
        elif ISO_DATE_RE.match(v_clean):
            y, m, d = v_clean.split("-")
            if not _is_real_calendar_date(int(y), int(m), int(d)):
                bad_count += 1
    if bad_count > 0:
        rate = bad_count / len(strs)
        return FlawMatch(
            flaw_id="impossible_or_sentinel_date",
            confidence=round(rate, 2),
            evidence=(
                f"[impossible_or_sentinel_date] {bad_count}/{len(strs)} ({rate * 100:.1f}%) "
                f"sampled rows in column '{col_name}' contain sentinel or impossible dates"
            ),
            affected_rate=round(rate, 2),
        )
    return None


def detect_mixed_date_locales(values: list, col_name: str, rules_dict=None) -> FlawMatch | None:
    strs = _sample_strs(values)
    if not strs:
        return None
    uk_count = 0
    us_count = 0
    for s in strs:
        m = SLASH_DATE_RE.match(s.strip())
        if m:
            t1, t2 = int(m.group(1)), int(m.group(2))
            if t1 > 12 and t2 <= 12:
                uk_count += 1
            elif t2 > 12 and t1 <= 12:
                us_count += 1
    if uk_count > 0 and us_count > 0:
        total_hits = uk_count + us_count
        rate = total_hits / len(strs)
        evidence = (
            f"[mixed_date_locales] Column '{col_name}' mixes date locales across "
            f"{total_hits}/{len(strs)} ({rate * 100:.1f}%) sampled rows "
            f"({uk_count} DD/MM/YYYY, {us_count} MM/DD/YYYY)"
        )
        return FlawMatch(
            flaw_id="mixed_date_locales",
            confidence=0.95,
            evidence=evidence,
            affected_rate=round(rate, 2),
        )
    return None


def detect_negative_where_unsigned_expected(values: list, col_name: str, rules_dict=None) -> FlawMatch | None:
    cfg = _get_rule_config("negative_unsigned_field", rules_dict)
    keywords = cfg.get("column_name_keywords", [
        "qty", "quantity", "count", "age", "tenure", "arrears", "months", "days", "weeks", "years"
    ])
    if not any(tok in col_name.lower() for tok in keywords):
        return None
    non_null = [v for v in values if v is not None and not isinstance(v, bool)]
    if not non_null:
        return None
    neg_count = 0
    for v in non_null:
        if isinstance(v, (int, float, Decimal)):
            if v < 0:
                neg_count += 1
        elif isinstance(v, str):
            s = v.strip()
            if PURE_NUMBER_RE.match(s) and float(s) < 0:
                neg_count += 1
    if neg_count > 0:
        rate = neg_count / len(non_null)
        conf = round(min(1.0, rate + 0.3), 2)
        return FlawMatch(
            flaw_id="negative_unsigned_field",
            confidence=conf,
            evidence=(
                f"[negative_unsigned_field] {neg_count}/{len(non_null)} ({rate * 100:.1f}%) "
                f"sampled values in unsigned column '{col_name}' are negative"
            ),
            affected_rate=round(rate, 2),
        )
    return None


def detect_overloaded_freetext(values: list, col_name: str, rules_dict=None) -> FlawMatch | None:
    cfg = _get_rule_config("overloaded_freetext_column", rules_dict)
    keywords = cfg.get("column_name_keywords", ["notes", "memo", "description", "details", "comments"])
    if not any(tok in col_name.lower() for tok in keywords):
        return None
    strs = _sample_strs(values)
    if not strs:
        return None
    pair_pattern = re.compile(cfg.get("packed_pair_pattern", r"[A-Za-z_]+:[A-Za-z0-9_]+"))
    packed_count = sum(1 for s in strs if pair_pattern.search(s))
    if packed_count > 0:
        rate = packed_count / len(strs)
        conf = min(1.0, rate + 0.2)
        return FlawMatch(
            flaw_id="overloaded_freetext_column",
            confidence=round(conf, 2),
            evidence=(
                f"[overloaded_freetext_column] {packed_count}/{len(strs)} ({rate * 100:.1f}%) "
                f"sampled rows in column '{col_name}' contain packed key:value pairs"
            ),
            affected_rate=round(rate, 2),
        )
    return None


# ---------------------------------------------------------------------------
# Pillar 4: Shadow PII Exposures (Only run on free-text / non-explicit columns)
# ---------------------------------------------------------------------------

def detect_shadow_uk_nino(values: list, col_name: str, rules_dict=None) -> FlawMatch | None:
    col_lower = col_name.lower()
    if any(explicit in col_lower for explicit in ["ni_number", "nino", "national_insurance", "email"]):
        return None
    strs = _sample_strs(values)
    if not strs:
        return None
    hit_count = sum(1 for s in strs if UK_NINO_RE.search(s))
    if hit_count > 0:
        rate = hit_count / len(strs)
        return FlawMatch(
            flaw_id="shadow_uk_nino",
            confidence=0.98,
            evidence=(
                f"[shadow_uk_nino] {hit_count}/{len(strs)} ({rate * 100:.1f}%) "
                f"sampled rows in column '{col_name}' match UK National Insurance Number pattern"
            ),
            affected_rate=round(rate, 2),
        )
    return None


def detect_shadow_nhs_number(values: list, col_name: str, rules_dict=None) -> FlawMatch | None:
    col_lower = col_name.lower()
    if any(explicit in col_lower for explicit in ["nhs_number", "nhs_no"]):
        return None
    strs = _sample_strs(values)
    if not strs:
        return None
    hit_count = 0
    for s in strs:
        m = NHS_NUMBER_RE.search(s)
        if m and validate_nhs_modulus11(m.group(0)):
            hit_count += 1
    if hit_count > 0:
        rate = hit_count / len(strs)
        return FlawMatch(
            flaw_id="shadow_nhs_number",
            confidence=0.99,
            evidence=(
                f"[shadow_nhs_number] {hit_count}/{len(strs)} ({rate * 100:.1f}%) "
                f"sampled rows in column '{col_name}' match Modulus-11 verified UK NHS Number pattern"
            ),
            affected_rate=round(rate, 2),
        )
    return None


def detect_legacy_redaction_markers(values: list, col_name: str, rules_dict=None) -> FlawMatch | None:
    strs = _sample_strs(values)
    if not strs:
        return None
    hit_count = sum(1 for s in strs if REDACTION_MARKERS_RE.search(s))
    if hit_count > 0:
        rate = hit_count / len(strs)
        return FlawMatch(
            flaw_id="legacy_redaction_markers",
            confidence=0.90,
            evidence=(
                f"[legacy_redaction_markers] {hit_count}/{len(strs)} ({rate * 100:.1f}%) "
                f"sampled rows in column '{col_name}' contain legacy redaction or deletion tombstones"
            ),
            affected_rate=round(rate, 2),
        )
    return None


# ---------------------------------------------------------------------------
# Remediation Artifacts & SQL Emitters
# ---------------------------------------------------------------------------

REMEDIATION_CATALOG = {
    "postgres_null_byte_poison": {
        "category": "dms_replication_blockers",
        "severity": "CRITICAL",
        "recommended_target_type": "TEXT",
        "dbt_sql_expr": "REPLACE(\"{col}\", CHR(0), '')",
        "diagnostic_sql": "SELECT \"{col}\" FROM \"{table}\" WHERE \"{col}\" LIKE '%' || CHR(0) || '%';",
        "remediation_advice": "Strip ASCII NUL (0x00) bytes using REPLACE to prevent PostgreSQL wire termination and AWS DMS CDC replication task crashes.",
    },
    "cp1252_encoding_ghosts": {
        "category": "dms_replication_blockers",
        "severity": "HIGH",
        "recommended_target_type": "TEXT",
        "dbt_sql_expr": "REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(\"{col}\", 'â€™', ''''), 'â€“', '-'), 'â€œ', '\"'), 'Ã©', 'é'), 'Ã£', 'ã'), CHR(65533), '')",
        "diagnostic_sql": "SELECT \"{col}\" FROM \"{table}\" WHERE \"{col}\" LIKE '%â%' OR \"{col}\" LIKE '%\ufffd%';",
        "remediation_advice": "Re-encode CP1252/Windows-1252 mojibake artifacts back to valid UTF-8 equivalents.",
    },
    "legacy_proprietary_datatype": {
        "category": "dms_replication_blockers",
        "severity": "HIGH",
        "recommended_target_type": "NUMERIC(18,2)",
        "dbt_sql_expr": "CAST(\"{col}\" AS NUMERIC(18,2))",
        "diagnostic_sql": "SELECT \"{col}\" FROM \"{table}\" WHERE \"{col}\" IS NOT NULL;",
        "remediation_advice": "Convert proprietary database datatypes (e.g., MONEY, SQL_VARIANT) to modern ANSI SQL types.",
    },
    "duplicate_key_values": {
        "category": "referential_and_key_integrity",
        "severity": "CRITICAL",
        "recommended_target_type": "BIGINT",
        "dbt_sql_expr": "\"{col}\"",
        "diagnostic_sql": "SELECT \"{col}\", COUNT(*) FROM \"{table}\" WHERE \"{col}\" IS NOT NULL GROUP BY \"{col}\" HAVING COUNT(*) > 1;",
        "remediation_advice": "Deduplicate declared primary key values or route collision rows to crucible_quarantine for manual reconciliation.",
    },
    "whitespace_padded_keys": {
        "category": "referential_and_key_integrity",
        "severity": "HIGH",
        "recommended_target_type": "VARCHAR(64)",
        "dbt_sql_expr": "TRIM(\"{col}\")",
        "diagnostic_sql": "SELECT \"{col}\" FROM \"{table}\" WHERE \"{col}\" != TRIM(\"{col}\");",
        "remediation_advice": "Strip leading/trailing whitespace padding with TRIM() to prevent silent foreign key join failures.",
    },
    "control_chars": {
        "category": "semantic_data_rot",
        "severity": "WARN",
        "recommended_target_type": "TEXT",
        "dbt_sql_expr": "REGEXP_REPLACE(\"{col}\", '[\\x01-\\x1f]', '', 'g')",
        "diagnostic_sql": "SELECT \"{col}\" FROM \"{table}\" WHERE \"{col}\" ~ '[\\x01-\\x1f]';",
        "remediation_advice": "Strip invisible ASCII control bytes from string content using REGEXP_REPLACE.",
    },
    "text_currency": {
        "category": "semantic_data_rot",
        "severity": "WARN",
        "recommended_target_type": "NUMERIC(18,2)",
        "dbt_sql_expr": "CAST(NULLIF(REGEXP_REPLACE(\"{col}\", '[^0-9.-]', '', 'g'), '') AS NUMERIC(18,2))",
        "diagnostic_sql": "SELECT \"{col}\" FROM \"{table}\" WHERE \"{col}\" ~ '[$£€]' OR \"{col}\" ~* '(USD|EUR|GBP)';",
        "remediation_advice": "Strip formatting currency symbols/suffixes and cast text values to NUMERIC(18,2).",
    },
    "non_numeric_in_numeric_col": {
        "category": "semantic_data_rot",
        "severity": "HIGH",
        "recommended_target_type": "NUMERIC(18,2)",
        "dbt_sql_expr": "CAST(NULLIF(REGEXP_REPLACE(\"{col}\", '[^0-9.-]', '', 'g'), '') AS NUMERIC(18,2))",
        "diagnostic_sql": "SELECT \"{col}\" FROM \"{table}\" WHERE \"{col}\" IS NOT NULL AND \"{col}\" !~ '^-?[0-9]+(\\.[0-9]+)?$';",
        "remediation_advice": "Sanitize non-numeric text strings and enforce standard NUMERIC(18,2) schema typing.",
    },
    "impossible_or_sentinel_date": {
        "category": "semantic_data_rot",
        "severity": "WARN",
        "recommended_target_type": "DATE",
        "dbt_sql_expr": "CASE WHEN \"{col}\" IN ('1900-01-01', '1970-01-01', '9999-12-31', '0000-00-00') THEN NULL ELSE CAST(\"{col}\" AS DATE) END",
        "diagnostic_sql": "SELECT \"{col}\" FROM \"{table}\" WHERE \"{col}\" IN ('1900-01-01', '1970-01-01', '9999-12-31', '0000-00-00');",
        "remediation_advice": "Map legacy sentinel dates (1900-01-01, 9999-12-31) to NULL and enforce ISO-8601 calendar validation.",
    },
    "mixed_date_locales": {
        "category": "semantic_data_rot",
        "severity": "CRITICAL",
        "recommended_target_type": "DATE",
        "dbt_sql_expr": "CASE WHEN \"{col}\" ~ '^\\d{4}-\\d{2}-\\d{2}$' THEN CAST(\"{col}\" AS DATE) ELSE NULL END",
        "diagnostic_sql": "SELECT \"{col}\" FROM \"{table}\" WHERE \"{col}\" IS NOT NULL;",
        "remediation_advice": "Cannot auto-parse ambiguous dates (e.g. 04/05/1990). Quarantine ambiguous rows and enforce ISO-8601.",
    },
    "negative_unsigned_field": {
        "category": "semantic_data_rot",
        "severity": "HIGH",
        "recommended_target_type": "INTEGER",
        "dbt_sql_expr": "CASE WHEN CAST(\"{col}\" AS NUMERIC) < 0 THEN NULL ELSE CAST(\"{col}\" AS NUMERIC) END",
        "diagnostic_sql": "SELECT \"{col}\" FROM \"{table}\" WHERE CAST(\"{col}\" AS NUMERIC) < 0;",
        "remediation_advice": "Flag or nullify negative values in columns where unsigned positive quantities are required.",
    },
    "overloaded_freetext_column": {
        "category": "semantic_data_rot",
        "severity": "WARN",
        "recommended_target_type": "TEXT",
        "dbt_sql_expr": "\"{col}\"",
        "diagnostic_sql": "SELECT \"{col}\" FROM \"{table}\" WHERE \"{col}\" ~ '[A-Za-z_]+:[A-Za-z0-9_]+';",
        "remediation_advice": "Unpack packed key:value pairs into structured downstream staging columns or JSON.",
    },
    "shadow_uk_nino": {
        "category": "shadow_pii_exposures",
        "severity": "CRITICAL",
        "recommended_target_type": "TEXT",
        "dbt_sql_expr": "REGEXP_REPLACE(\"{col}\", '(?i)\\y(?:QQ|(?!BG|GB|NK|KN|TN|NT|ZZ)[A-CEGHJ-PR-TW-ZQ][A-CEGHJ-NPR-TW-ZQ])\\s*\\d{2}\\s*\\d{2}\\s*\\d{2}\\s*[A-D]\\y', '[REDACTED_NINO]', 'g')",
        "diagnostic_sql": "SELECT \"{col}\" FROM \"{table}\" WHERE \"{col}\" ~* '\\y[A-Z]{2}\\s?[0-9]{2}\\s?[0-9]{2}\\s?[0-9]{2}\\s?[A-D]\\y';",
        "remediation_advice": "UK National Insurance Number detected in general text. Quarantine or tokenize under UK GDPR.",
    },
    "shadow_nhs_number": {
        "category": "shadow_pii_exposures",
        "severity": "CRITICAL",
        "recommended_target_type": "TEXT",
        "dbt_sql_expr": "REGEXP_REPLACE(\"{col}\", '\\y\\d{3}\\s?\\d{3}\\s?\\d{4}\\y', '[REDACTED_NHS]', 'g')",
        "diagnostic_sql": "SELECT \"{col}\" FROM \"{table}\" WHERE \"{col}\" ~ '\\y[0-9]{3}\\s?[0-9]{3}\\s?[0-9]{4}\\y';",
        "remediation_advice": "UK NHS Number detected in general text. Quarantine under UK Data Protection Act.",
    },
    "legacy_redaction_markers": {
        "category": "shadow_pii_exposures",
        "severity": "WARN",
        "recommended_target_type": "TEXT",
        "dbt_sql_expr": "REGEXP_REPLACE(\"{col}\", '(?i)\\y(REDACTED|GDPR_DELETED|DECEASED|DO NOT CONTACT|TEST TEST|XXXX+)\\y', '', 'g')",
        "diagnostic_sql": "SELECT \"{col}\" FROM \"{table}\" WHERE \"{col}\" ~* '\\y(REDACTED|GDPR_DELETED|DECEASED|DO NOT CONTACT|TEST TEST|XXXX+)\\y';",
        "remediation_advice": "Clean legacy manual redaction and GDPR tombstone markers.",
    },
}


def build_remediation_artifacts(
    flaw_id: str,
    table: str,
    col: str,
    declared_type: str = "VARCHAR",
    rules_dict: dict | None = None,
) -> dict:
    """Builds dbt SQL expressions, diagnostic queries, and remediation advice."""
    cfg = _get_rule_config(flaw_id, rules_dict) if rules_dict is not None else {}
    tmpl = REMEDIATION_CATALOG.get(flaw_id)
    if not tmpl:
        severity = str(cfg.get("severity", "WARN")).upper()
        return {
            "category": cfg.get("category", "semantic_data_rot"),
            "severity": severity,
            "recommended_target_type": declared_type,
            "dbt_sql_expr": f'"{col}"',
            "diagnostic_sql": f'SELECT "{col}" FROM "{table}" WHERE "{col}" IS NOT NULL;',
            "remediation_advice": cfg.get("description", "Review column values for data consistency."),
        }

    dbt_expr = to_pg_regex(tmpl["dbt_sql_expr"].replace("{col}", col).replace("{table}", table))
    diag_sql = to_pg_regex(tmpl["diagnostic_sql"].replace("{col}", col).replace("{table}", table))
    rec_type = tmpl["recommended_target_type"]
    severity = str(cfg.get("severity", tmpl["severity"])).upper()

    return {
        "category": tmpl["category"],
        "severity": severity,
        "recommended_target_type": rec_type,
        "dbt_sql_expr": dbt_expr,
        "diagnostic_sql": diag_sql,
        "remediation_advice": tmpl["remediation_advice"],
    }


def detect_column_flaws(
    values: list,
    col_name: str,
    declared_type: str = "VARCHAR",
    is_pk: bool = False,
    rules_dict: dict | None = None,
    table_name: str = "",
) -> list[dict[str, Any]]:
    """
    Runs all enabled built-in detectors and enabled custom_rules for a column.
    Respects packs.<pack> toggles, per-rule enabled flags, and severity overrides.
    Emits strictly Zero-PII count/rate/metadata-only evidence.
    """
    rules = rules_dict if rules_dict is not None else load_rules()

    builtin_detectors = [
        # aws_dms_cdc
        ("postgres_null_byte_poison", detect_postgres_null_byte_poison, (values, rules)),
        ("cp1252_encoding_ghosts", detect_cp1252_encoding_ghosts, (values, rules)),
        ("legacy_proprietary_datatype", detect_legacy_proprietary_datatype, (declared_type, rules)),
        # schema_integrity
        ("duplicate_key_values", detect_duplicate_key_values, (values, is_pk, rules)),
        ("whitespace_padded_keys", detect_whitespace_padded_keys, (values, is_pk, rules)),
        # data_hygiene
        ("control_chars", detect_control_chars, (values, rules)),
        ("text_currency", detect_text_currency, (values, rules)),
        ("non_numeric_in_numeric_col", detect_non_numeric_in_numeric_col, (values, col_name, rules)),
        ("impossible_or_sentinel_date", detect_impossible_or_sentinel_dates, (values, col_name, rules)),
        ("mixed_date_locales", detect_mixed_date_locales, (values, col_name, rules)),
        ("negative_unsigned_field", detect_negative_where_unsigned_expected, (values, col_name, rules)),
        ("overloaded_freetext_column", detect_overloaded_freetext, (values, col_name, rules)),
        # uk_shadow_pii
        ("shadow_uk_nino", detect_shadow_uk_nino, (values, col_name, rules)),
        ("shadow_nhs_number", detect_shadow_nhs_number, (values, col_name, rules)),
        ("legacy_redaction_markers", detect_legacy_redaction_markers, (values, col_name, rules)),
    ]

    flaws: list[dict[str, Any]] = []

    for rule_id, detector_fn, args in builtin_detectors:
        if not is_rule_enabled(rule_id, rules):
            continue
        match = detector_fn(*args)
        if match:
            artifacts = build_remediation_artifacts(
                match.flaw_id, table_name, col_name, declared_type, rules_dict=rules
            )
            flaws.append({
                "flaw_id": match.flaw_id,
                "category": artifacts["category"],
                "severity": artifacts["severity"],
                "confidence": round(match.confidence, 2),
                "affected_rate": round(match.affected_rate, 2),
                "evidence": match.evidence,
                "remediation_advice": artifacts["remediation_advice"],
                "dbt_sql_expr": artifacts["dbt_sql_expr"],
                "diagnostic_sql": artifacts["diagnostic_sql"],
            })

    # Custom regex rules from rules.yaml
    custom_rules = rules.get("custom_rules") or []
    if isinstance(custom_rules, list) and custom_rules:
        strs = _sample_strs(values)
        if strs:
            col_lower = col_name.lower()
            for rule in custom_rules:
                if not isinstance(rule, dict):
                    continue
                if rule.get("enabled") is False:
                    continue
                rule_id = rule.get("rule_id") or rule.get("id")
                pattern = rule.get("pattern")
                if not rule_id or not pattern:
                    continue
                pack_name = rule.get("pack")
                if pack_name and not is_pack_enabled(pack_name, rules):
                    continue
                keywords = rule.get("column_name_keywords")
                if keywords and not any(str(kw).lower() in col_lower for kw in keywords):
                    continue
                try:
                    compiled = re.compile(pattern)
                except re.error:
                    continue
                hit_count = sum(1 for s in strs if compiled.search(s))
                if hit_count > 0:
                    rate = hit_count / len(strs)
                    category = str(rule.get("category") or "custom_rules")
                    severity = str(rule.get("severity") or "medium").upper()
                    pg_pattern = str(rule.get("pg_pattern") or to_pg_regex(pattern))
                    pg_pattern = to_pg_regex(pg_pattern)
                    pg_pat_sql = pg_pattern.replace("'", "''")
                    description = str(rule.get("description") or f"Custom rule '{rule_id}' matched.")
                    remediation_hours = float(rule.get("remediation_hours", 1.0))
                    diag_sql = (
                        f'SELECT "{col_name}" FROM "{table_name}" '
                        f'WHERE "{col_name}" ~* \'{pg_pat_sql}\';'
                    )
                    flaws.append({
                        "flaw_id": str(rule_id),
                        "category": category,
                        "severity": severity,
                        "confidence": round(min(1.0, max(0.70, rate)), 2),
                        "affected_rate": round(rate, 2),
                        "evidence": (
                            f"[{rule_id}] {hit_count}/{len(strs)} ({rate * 100:.1f}%) "
                            f"sampled rows in column '{col_name}' matched custom rule '{rule_id}'"
                        ),
                        "remediation_advice": description,
                        "dbt_sql_expr": f'"{col_name}"',
                        "diagnostic_sql": diag_sql,
                        "pg_pattern": pg_pattern,
                        "remediation_hours": remediation_hours,
                        "is_custom_rule": True,
                    })

    return flaws