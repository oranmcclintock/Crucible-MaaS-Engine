import pytest
from flaw_catalog import (
    detect_text_currency,
    detect_impossible_or_sentinel_dates,
    detect_non_numeric_in_numeric_col,
    detect_negative_where_unsigned_expected,
    FlawMatch
)

def test_detect_text_currency():
    values = ["$10.50", "€ 5", "100.00", "USD 50", None]
    match = detect_text_currency(values)
    assert match is not None
    assert match.flaw_id == "text_currency"
    assert match.confidence > 0.0

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

def test_detect_impossible_dates_clean():
    values = ["1990-01-01", "2020-12-31"]
    match = detect_impossible_or_sentinel_dates(values, "customer_dob")
    assert match is None

def test_detect_non_numeric_in_numeric_col():
    values = ["100", "50.5", "abc", "N/A"]
    match = detect_non_numeric_in_numeric_col(values, "account_balance")
    assert match is not None
    assert match.flaw_id == "non_numeric_in_numeric_col"

def test_detect_negative_unsigned():
    values = ["100", "50.5", "-10", "0"]
    match = detect_negative_where_unsigned_expected(values, "item_quantity")
    assert match is not None
    assert match.flaw_id == "negative_unsigned_field"

def test_detect_negative_unsigned_clean():
    values = ["100", "50.5", "10", "0"]
    match = detect_negative_where_unsigned_expected(values, "item_quantity")
    assert match is None
