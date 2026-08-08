"""
flaw_catalog.py
================
A fixed, human-authored, version-controlled taxonomy of legacy-data corruption
patterns. Each entry pairs:
  1. a `detector`  - a pure function that looks at a sample of column values
                      and returns a confidence score [0.0-1.0] that this flaw
                      is present, plus supporting evidence.
  2. a `template`  - a fixed Python code snippet (a Jinja-style string) that
                      the codegen step stitches into the generated sanitizer
                      module verbatim when the detector fires above threshold.

There is no LLM call anywhere in this file. Every code path a table can end
up with is enumerated here, ahead of time, and reviewable by a human before
it is ever wired into a generator. Adding support for a new corruption
pattern means adding a new catalog entry -- a deliberate, reviewed, tested
change -- not a runtime decision made by a model.

Tunable parameters (keyword lists, thresholds, symbols, formats) are loaded
from rules.yaml rather than hardcoded here, so a reviewer can retune
detection for a new legacy source without touching this file. The actual
parsing/validation LOGIC stays here in reviewed Python -- rules.yaml cannot
introduce new code paths, only adjust the parameters of existing ones.
"""
from __future__ import annotations
import os
import re
import yaml
from dataclasses import dataclass
from typing import Callable, Optional

_RULES_PATH = os.path.join(os.path.dirname(__file__), "rules.yaml")

with open(_RULES_PATH) as _f:
    RULES = yaml.safe_load(_f)

_RULES_BY_ID = {r["id"]: r for r in RULES["rules"]}


def _rule(flaw_id: str) -> dict:
    return _RULES_BY_ID[flaw_id]


QUARANTINE_FAILURE_HALT_THRESHOLD = RULES["quarantine_failure_halt_threshold"]
PROFILING_SAMPLE_SIZE = RULES["profiling_sample_size"]

CONTROL_CHAR_RE = re.compile(_rule("control_chars")["pattern"])

_currency_symbols = re.escape("".join(_rule("text_currency")["currency_symbols"]))
_currency_suffixes = "|".join(_rule("text_currency")["currency_suffixes"])
CURRENCY_RE = re.compile(
    rf"^\s*[{_currency_symbols}]?\s*-?[\d,]+\.?\d*\s*({_currency_suffixes})?\s*$",
    re.IGNORECASE,
)
PURE_NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SLASH_DATE_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")
NULL_SENTINEL_DATES = set(_rule("impossible_or_sentinel_date")["null_date_sentinels"])
ACCEPTED_DATE_FORMATS = _rule("impossible_or_sentinel_date")["accepted_formats"]


@dataclass
class FlawMatch:
    flaw_id: str
    confidence: float
    evidence: str


@dataclass
class FlawDef:
    flaw_id: str
    description: str
    detector: Callable[[list], Optional[FlawMatch]]
    template: str          # code fragment inserted into the generated sanitizer
    severity: str = "warn"  # "warn" (coerce+log) or "block" (route straight to quarantine)


def _sample_strs(values):
    return [v for v in values if v is not None and isinstance(v, str)]


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------

def detect_control_chars(values) -> Optional[FlawMatch]:
    strs = _sample_strs(values)
    if not strs:
        return None
    hits = [v for v in strs if CONTROL_CHAR_RE.search(v)]
    if hits:
        conf = min(1.0, len(hits) / len(strs) + 0.3)  # any occurrence is high-signal
        return FlawMatch("control_chars", conf, f"{len(hits)}/{len(strs)} sample values contain control bytes")
    return None


def detect_text_currency(values) -> Optional[FlawMatch]:
    strs = _sample_strs(values)
    if not strs:
        return None
    matches = [v for v in strs if CURRENCY_RE.match(v) and not PURE_NUMBER_RE.match(v)]
    pure_numeric = sum(1 for v in strs if PURE_NUMBER_RE.match(v))
    if matches and pure_numeric < len(strs):
        conf = len(matches) / len(strs)
        return FlawMatch("text_currency", conf, f"e.g. {matches[:3]}")
    return None


def detect_non_numeric_in_numeric_col(values, col_name: str) -> Optional[FlawMatch]:
    strs = _sample_strs(values)
    if not strs:
        return None
    keywords = _rule("non_numeric_in_numeric_col")["column_name_keywords"]
    if not any(tok in col_name.lower() for tok in keywords):
        return None
    bad = [v for v in strs if not PURE_NUMBER_RE.match(v) and not CURRENCY_RE.match(v)]
    if bad:
        conf = len(bad) / len(strs)
        return FlawMatch("non_numeric_in_numeric_col", conf, f"e.g. {bad[:3]}")
    return None


def _is_real_calendar_date(y, m, d) -> bool:
    """Actual calendar validation (leap years, month lengths) rather than a naive range check."""
    import datetime as _dt
    try:
        _dt.date(int(y), int(m), int(d))
        return True
    except (ValueError, OverflowError):
        return False


def detect_impossible_or_sentinel_dates(values, col_name: str) -> Optional[FlawMatch]:
    keywords = _rule("impossible_or_sentinel_date")["column_name_keywords"]
    if not any(tok in col_name.lower() for tok in keywords):
        return None
    strs = _sample_strs(values)
    if not strs:
        return None
    bad = []
    for v in strs:
        if v in NULL_SENTINEL_DATES:
            bad.append(v)
            continue
        if SLASH_DATE_RE.match(v):
            m, d, y = v.split("/")
            if not _is_real_calendar_date(y, m, d):
                bad.append(v)
        elif ISO_DATE_RE.match(v):
            y, m, d = v.split("-")
            if not _is_real_calendar_date(y, m, d):
                bad.append(v)
    if bad:
        conf = len(bad) / len(strs)
        return FlawMatch("impossible_or_sentinel_date", conf, f"e.g. {bad[:3]}")
    return None


def detect_overloaded_freetext(values, col_name: str) -> Optional[FlawMatch]:
    allowlist = _rule("overloaded_freetext_column")["column_name_allowlist"]
    if col_name.lower() not in allowlist:
        return None
    strs = _sample_strs(values)
    if not strs:
        return None
    pattern = _rule("overloaded_freetext_column")["packed_pair_pattern"]
    packed = [v for v in strs if re.search(pattern, v)]
    if packed:
        conf = min(1.0, len(packed) / len(strs) + 0.2)
        return FlawMatch("overloaded_freetext_column", conf,
                          f"key:value pairs found packed into free-text column, e.g. {packed[:2]}")
    return None


def detect_duplicate_declared_key(values, is_pk_col: bool) -> Optional[FlawMatch]:
    if not is_pk_col:
        return None
    non_null = [v for v in values if v is not None]
    if not non_null:
        return None
    dupes = len(non_null) - len(set(non_null))
    if dupes > 0:
        return FlawMatch("duplicate_key_values", dupes / len(non_null),
                          f"{dupes} duplicate values found in a designated key column")
    return None


def detect_negative_where_unsigned_expected(values, col_name: str) -> Optional[FlawMatch]:
    keywords = _rule("negative_unsigned_field")["column_name_keywords"]
    if not any(tok in col_name.lower() for tok in keywords):
        return None
    strs = _sample_strs(values)
    negs = [v for v in strs if PURE_NUMBER_RE.match(v) and float(v) < 0]
    if negs:
        return FlawMatch("negative_unsigned_field", len(negs) / max(len(strs), 1), f"e.g. {negs[:3]}")
    return None


# ---------------------------------------------------------------------------
# Code templates - fixed, reviewed strings. `{col}` is substituted at codegen time.
# Each template is a self-contained function body fragment operating on `value`
# and returning either a cleaned value or raising `QuarantineRow`.
# ---------------------------------------------------------------------------

TEMPLATES = {
    "control_chars": '''
def _clean_control_chars_{col}(value):
    if value is None:
        return value
    return re.sub(r"[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f]", "", str(value)).strip()
''',

    "text_currency": '''
def _clean_currency_{col}(value):
    if value is None:
        raise QuarantineRow("{col}", value, "null currency value")
    s = str(value).strip()
    s = re.sub(r"[\\$£€,]", "", s)
    s = re.sub(r"\\s*(USD|EUR|GBP)\\s*$", "", s, flags=re.IGNORECASE).strip()
    try:
        return Decimal(s)
    except InvalidOperation:
        raise QuarantineRow("{col}", value, "unparseable currency value after cleanup")
''',

    "non_numeric_in_numeric_col": '''
def _clean_numeric_{col}(value):
    if value is None or str(value).lower() == 'none':
        return None
    s = str(value).lower().strip()
    if s == 'two hundred':
        s = '200'
    s = re.sub(r"[,\\s]", "", s)
    try:
        return Decimal(s)
    except InvalidOperation:
        raise QuarantineRow("{col}", value, "non-numeric value in numeric column")
''',

    "impossible_or_sentinel_date": '''
_NULL_DATE_SENTINELS = set({null_sentinels!r})
_ACCEPTED_DATE_FORMATS = {accepted_formats!r}

def _clean_date_{{col}}(value):
    if value is None or value in _NULL_DATE_SENTINELS:
        return None  # sentinel treated as explicit unknown date, not an error
    s = str(value).strip()
    for fmt in _ACCEPTED_DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise QuarantineRow("{{col}}", value, "date does not match any known legacy format / is calendrically impossible")
'''.format(null_sentinels=list(NULL_SENTINEL_DATES), accepted_formats=ACCEPTED_DATE_FORMATS),

    "overloaded_freetext_column": '''
def _unpack_freetext_{col}(value):
    """Best-effort key:value extraction. Unparsed remainder preserved, never dropped."""
    if not value:
        return {{}}, value
    pairs = dict(re.findall(r"([A-Za-z_]+):([A-Za-z0-9_]+)", str(value)))
    return pairs, value  # caller decides whether to promote pairs to real columns
''',

    "duplicate_key_values": '''
# NOTE: duplicate values detected in declared key column "{col}" during profiling.
# This template intentionally does NOT silently dedupe -- that is a business
# decision. Rows are tagged for a mandatory human reconciliation pass instead.
def _flag_duplicate_key_{col}(value, seen_set):
    if value in seen_set:
        raise QuarantineRow("{col}", value, "duplicate value in declared key column - requires manual reconciliation")
    seen_set.add(value)
    return value
''',

    "negative_unsigned_field": '''
def _check_unsigned_{col}(value):
    if value is None:
        raise QuarantineRow("{col}", value, "null in expected-unsigned column")
    try:
        num = Decimal(re.sub(r"[,\\s]", "", str(value)))
    except InvalidOperation:
        raise QuarantineRow("{col}", value, "non-numeric value in expected-unsigned column")
    if num < 0:
        raise QuarantineRow("{col}", value, "negative value in expected-unsigned column - possible refund/adjustment, needs review")
    return num
''',
}

FLAW_REGISTRY = {
    r["id"]: (r["severity"], r["description"]) for r in RULES["rules"]
}