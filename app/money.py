"""Money = integer cents (USD). Months are the first day of a calendar month."""
from __future__ import annotations

import re
from datetime import date

MAX_CENTS = 10**11  # $1,000,000,000.00
MIN_DATE = date(2000, 1, 1)
MAX_DATE = date(2100, 12, 31)
_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")


def parse_month(value: str) -> date:
    m = _MONTH_RE.match(value or "")
    if not m:
        raise ValueError("Month must look like YYYY-MM.")
    try:
        d = date(int(m.group(1)), int(m.group(2)), 1)
    except ValueError as exc:
        raise ValueError("Month must look like YYYY-MM.") from exc
    if not (MIN_DATE <= d <= MAX_DATE):
        raise ValueError("Month is out of the supported range (2000-01 to 2100-12).")
    return d


def parse_iso_date(value: str) -> date:
    try:
        d = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Date must look like YYYY-MM-DD.") from exc
    if len(value) != 10:
        raise ValueError("Date must look like YYYY-MM-DD.")
    if not (MIN_DATE <= d <= MAX_DATE):
        raise ValueError("Date is out of the supported range (2000-01-01 to 2100-12-31).")
    return d


def month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def first_of_month(d: date) -> date:
    return d.replace(day=1)


def next_month(d: date) -> date:
    return date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)


def month_range(start: date, end: date):
    """Yield first-of-month dates from start to end inclusive."""
    cur = start
    while cur <= end:
        yield cur
        cur = next_month(cur)
