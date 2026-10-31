"""Pure budget math (no I/O).

* income(M)      = sum of transactions in M with no category (must be > 0).
* assigned(c,M)  = money assigned to category c in M (stored).
* activity(c,M)  = sum of transactions in M for category c (derived, never stored).
* carry_in(c,M)  = max(0, available(c, M-1)).
* available(c,M) = carry_in + assigned + activity.
* overspent(M)   = sum over c of max(0, -available(c,M)).
* ready_to_assign(M) = sum_{m<=M} income - sum_{m<=M} assigned - sum_{m<M} overspent.
Invariant: ready_to_assign(M) + sum_c available(c,M) == cumulative income + cumulative activity.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Mapping

from .money import month_range


@dataclass(frozen=True)
class CategoryMonth:
    carry_in: int
    assigned: int
    activity: int
    available: int


@dataclass(frozen=True)
class MonthResult:
    month: date
    categories: Mapping[str, CategoryMonth]
    income: int
    assigned: int
    activity: int
    available: int
    overspent: int
    prior_overspent_absorbed: int
    cumulative_income: int
    cumulative_assigned: int
    ready_to_assign: int
    carry_in: int = 0


def compute_timeline(
    category_ids: Iterable[str],
    assigned: Mapping[tuple[str, date], int],
    activity: Mapping[tuple[str, date], int],
    income: Mapping[date, int],
    through: date,
) -> dict[date, MonthResult]:
    cats = list(category_ids)
    data_months = [m for (_, m) in assigned] + [m for (_, m) in activity] + list(income)
    data_months = [m for m in data_months if m <= through]
    start = min(data_months + [through])

    result: dict[date, MonthResult] = {}
    carry = {c: 0 for c in cats}
    cum_income = cum_assigned = absorbed = 0

    for month in month_range(start, through):
        rows: dict[str, CategoryMonth] = {}
        tot_assigned = tot_activity = tot_available = tot_over = tot_carry = 0
        for c in cats:
            a = assigned.get((c, month), 0)
            t = activity.get((c, month), 0)
            avail = carry[c] + a + t
            rows[c] = CategoryMonth(carry[c], a, t, avail)
            tot_assigned += a
            tot_activity += t
            tot_available += avail
            tot_carry += carry[c]
            if avail < 0:
                tot_over += -avail
        inc = income.get(month, 0)
        cum_income += inc
        cum_assigned += tot_assigned
        result[month] = MonthResult(
            month=month, categories=rows, income=inc, assigned=tot_assigned, activity=tot_activity,
            available=tot_available, overspent=tot_over, prior_overspent_absorbed=absorbed,
            cumulative_income=cum_income, cumulative_assigned=cum_assigned,
            ready_to_assign=cum_income - cum_assigned - absorbed, carry_in=tot_carry,
        )
        absorbed += tot_over
        carry = {c: max(0, rows[c].available) for c in cats}
    return result


def min_ready_to_assign(timeline: Mapping[date, MonthResult], from_month: date) -> int:
    values = [r.ready_to_assign for m, r in timeline.items() if m >= from_month]
    return min(values) if values else 0
