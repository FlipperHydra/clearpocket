"""Opt-in sample data (the September 2026 demo budget), created only for the caller's budget."""
from __future__ import annotations

from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..errors import ApiError
from .budget import _add_assigned, ensure_month
from .ledger import lock_budget

MONTH = date(2026, 9, 1)

GROUPS = [
    ("Home & bills", [("Rent", "Due Sep 1", 145000), ("Utilities", "Monthly target $140", 14000), ("Internet", "Due Sep 18", 6500)]),
    ("Everyday life", [("Groceries", "Monthly target $500", 50000), ("Dining out", "Monthly target $180", 18000),
                       ("Fuel & transit", "Monthly target $160", 16000), ("Fun money", "Monthly target $125", 12500)]),
    ("Future plans", [("Emergency fund", "$6,000 goal", 50000), ("Spring trip", "$1,800 by March", 30000),
                      ("Car repairs", "Monthly target $150", 15000), ("Gifts", "$600 by December", 11500)]),
]

TRANSACTIONS = [
    ("2026-09-22", "Shell Station", "Fuel & transit", -4218), ("2026-09-21", "Green Basket Market", "Groceries", -8426),
    ("2026-09-20", "Book Nook", "Fun money", -2800), ("2026-09-18", "Fiberway Internet", "Internet", -6500),
    ("2026-09-16", "Pasta House", "Dining out", -5480), ("2026-09-14", "Green Basket Market", "Groceries", -12274),
    ("2026-09-12", "City Electric", "Utilities", -11842), ("2026-09-10", "Corner Caf\u00e9", "Dining out", -1838),
    ("2026-09-08", "Gift Gallery", "Gifts", -3850), ("2026-09-06", "Green Basket Market", "Groceries", -17767),
    ("2026-09-03", "Landlord", "Rent", -145000), ("2026-09-01", "September income", None, 368500),
]


def _insert_transactions(db: Session, budget_id: str, cat_ids: dict) -> None:
    for d, payee, cat, cents in TRANSACTIONS:
        db.execute(
            text("INSERT INTO transactions (budget_id, category_id, occurred_on, payee, amount_cents) VALUES (:b, :c, :d, :p, :a)"),
            {"b": budget_id, "c": cat_ids.get(cat) if cat else None, "d": date.fromisoformat(d), "p": payee, "a": cents},
        )


def load_sample_data(db: Session, budget_id: str) -> None:
    """All-or-nothing. Refuses (409) unless the budget is completely empty."""
    lock_budget(db, budget_id)
    nonempty = db.execute(
        text("SELECT EXISTS (SELECT 1 FROM category_groups WHERE budget_id = :b) OR EXISTS (SELECT 1 FROM transactions WHERE budget_id = :b)"),
        {"b": budget_id},
    ).scalar_one()
    if nonempty:
        db.rollback()
        raise ApiError(409, "budget_not_empty", "Sample data can only be loaded into an empty budget.")
    mid = ensure_month(db, budget_id, MONTH)
    cat_ids: dict[str, str] = {}
    for gi, (gname, cats) in enumerate(GROUPS, start=1):
        gid = db.execute(
            text("INSERT INTO category_groups (budget_id, name, sort_order) VALUES (:b, :n, :s) RETURNING id"),
            {"b": budget_id, "n": gname, "s": gi},
        ).scalar_one()
        for ci, (cname, target, assigned) in enumerate(cats, start=1):
            cid = db.execute(
                text("INSERT INTO categories (budget_id, group_id, name, target_text, sort_order) VALUES (:b, :g, :n, :t, :s) RETURNING id"),
                {"b": budget_id, "g": gid, "n": cname, "t": target, "s": ci},
            ).scalar_one()
            cat_ids[cname] = str(cid)
            _add_assigned(db, budget_id, mid, str(cid), assigned)
    _insert_transactions(db, budget_id, cat_ids)
    db.commit()
