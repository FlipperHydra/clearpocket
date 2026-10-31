from datetime import date

import pytest

from app.money import MAX_CENTS, month_key, month_range, next_month, parse_iso_date, parse_month


def test_parse_month_ok():
    assert parse_month("2026-09") == date(2026, 9, 1)
    assert month_key(date(2026, 9, 1)) == "2026-09"


@pytest.mark.parametrize("bad", ["2026-13", "2026-9", "abcd", "", "2026-09-01", "1999-12", "2101-01", "2026-00"])
def test_parse_month_rejects(bad):
    with pytest.raises(ValueError):
        parse_month(bad)


@pytest.mark.parametrize("bad", ["2026-02-30", "2026-9-1", "20260901", "1999-12-31", "2101-01-01", "x", ""])
def test_parse_date_rejects(bad):
    with pytest.raises(ValueError):
        parse_iso_date(bad)


def test_month_helpers():
    assert next_month(date(2026, 12, 1)) == date(2027, 1, 1)
    assert list(month_range(date(2026, 11, 1), date(2027, 1, 1))) == [date(2026, 11, 1), date(2026, 12, 1), date(2027, 1, 1)]
    assert MAX_CENTS == 10**11
