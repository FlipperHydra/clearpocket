"""Loads the raw numbers for a budget and builds API payloads from calculations.py."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..calculations import MonthResult, compute_timeline
from ..money import month_key


@dataclass
class Ledger:
    groups: list
    categories: list
    assigned: dict  # (category_id:str, month) -> cents
    versions: dict  # (category_id:str, month) -> int
    activity: dict  # (category_id:str, month) -> cents
    income: dict  # month -> cents

    def last_data_month(self, default: date) -> date:
        months = [m for (_, m) in self.assigned] + [m for (_, m) in self.activity] + list(self.income)
        return max(months + [default])

    def timeline(self, through: date):
        return compute_timeline([str(c.id) for c in self.categories], self.assigned, self.activity, self.income, through)


def lock_budget(db: Session, budget_id: str) -> None:
    """Serialises every financial write for one budget (row lock held until commit/rollback).
    Reads of the ledger happen AFTER this lock, so check-then-write is race free."""
    db.execute(text("SELECT id FROM budgets WHERE id = :b FOR UPDATE"), {"b": budget_id})


def load_ledger(db: Session, budget_id: str) -> Ledger:
    p = {"b": budget_id}
    groups = db.execute(
        text("SELECT id, name, sort_order FROM category_groups WHERE budget_id = :b ORDER BY sort_order, lower(name), id"), p
    ).all()
    cats = db.execute(
        text(
            """SELECT c.id, c.group_id, c.name, c.target_text, c.sort_order
               FROM categories c JOIN category_groups g ON g.id = c.group_id
               WHERE c.budget_id = :b ORDER BY g.sort_order, c.sort_order, lower(c.name), c.id"""
        ),
        p,
    ).all()
    assigned, versions = {}, {}
    for r in db.execute(
        text(
            """SELECT a.category_id, m.month, a.assigned_cents, a.version
               FROM allocations a JOIN budget_months m ON m.id = a.budget_month_id
               WHERE a.budget_id = :b"""
        ),
        p,
    ):
        assigned[(str(r.category_id), r.month)] = r.assigned_cents
        versions[(str(r.category_id), r.month)] = r.version
    activity, income = {}, {}
    for r in db.execute(
        text(
            """SELECT category_id, date_trunc('month', occurred_on)::date AS month, sum(amount_cents)::bigint AS total
               FROM transactions WHERE budget_id = :b GROUP BY 1, 2"""
        ),
        p,
    ):
        if r.category_id is None:
            income[r.month] = r.total
        else:
            activity[(str(r.category_id), r.month)] = r.total
    return Ledger(groups, cats, assigned, versions, activity, income)


def summary_json(month: date, r: MonthResult) -> dict:
    return {
        "month": month_key(month),
        "income_cents": r.income,
        "assigned_cents": r.assigned,
        "activity_cents": r.activity,
        "available_cents": r.available,
        "carry_in_cents": r.carry_in,
        "overspent_cents": r.overspent,
        "prior_overspent_absorbed_cents": r.prior_overspent_absorbed,
        "cumulative_income_cents": r.cumulative_income,
        "cumulative_assigned_cents": r.cumulative_assigned,
        "ready_to_assign_cents": r.ready_to_assign,
        "is_over_allocated": r.ready_to_assign < 0,
    }


def month_json(ledger: Ledger, month: date) -> dict:
    r = ledger.timeline(month)[month]
    groups_out = []
    for g in ledger.groups:
        rows = []
        for c in (c for c in ledger.categories if c.group_id == g.id):
            cm = r.categories[str(c.id)]
            rows.append(
                {
                    "id": str(c.id),
                    "group_id": str(c.group_id),
                    "name": c.name,
                    "target_text": c.target_text,
                    "sort_order": c.sort_order,
                    "carry_in_cents": cm.carry_in,
                    "assigned_cents": cm.assigned,
                    "activity_cents": cm.activity,
                    "available_cents": cm.available,
                    "assignment_version": ledger.versions.get((str(c.id), month), 0),
                }
            )
        groups_out.append(
            {
                "id": str(g.id),
                "name": g.name,
                "sort_order": g.sort_order,
                "assigned_cents": sum(x["assigned_cents"] for x in rows),
                "activity_cents": sum(x["activity_cents"] for x in rows),
                "available_cents": sum(x["available_cents"] for x in rows),
                "categories": rows,
            }
        )
    return {"month": month_key(month), "summary": summary_json(month, r), "groups": groups_out}
