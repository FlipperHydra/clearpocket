"""Financial write operations. Every function: lock budget -> validate -> write -> commit.
Nothing is committed until the final db.commit(), so any exception rolls everything back."""
from __future__ import annotations

from datetime import date

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..calculations import min_ready_to_assign
from ..errors import ApiError, not_found
from .ledger import lock_budget, load_ledger, month_json


def ensure_month(db: Session, budget_id: str, month: date):
    return db.execute(
        text(
            """INSERT INTO budget_months (budget_id, month) VALUES (:b, :m)
               ON CONFLICT (budget_id, month) DO UPDATE SET month = EXCLUDED.month
               RETURNING id"""
        ),
        {"b": budget_id, "m": month},
    ).scalar_one()


def _add_assigned(db: Session, budget_id: str, month_id, category_id: str, delta: int) -> None:
    """Relative (delta) upsert, so concurrent-safe even without the lock."""
    db.execute(
        text(
            """INSERT INTO allocations (category_id, budget_month_id, budget_id, assigned_cents)
               VALUES (:c, :mid, :b, :d)
               ON CONFLICT (category_id, budget_month_id) DO UPDATE
               SET assigned_cents = allocations.assigned_cents + EXCLUDED.assigned_cents,
                   version = allocations.version + 1, updated_at = now()"""
        ),
        {"c": category_id, "mid": month_id, "b": budget_id, "d": delta},
    )


def _require_categories(db: Session, budget_id: str, ids: list[str]) -> None:
    found = db.execute(
        text("SELECT count(*) FROM categories WHERE budget_id = :b AND CAST(id AS text) = ANY(CAST(:ids AS text[]))"),
        {"b": budget_id, "ids": [str(i) for i in set(ids)]},
    ).scalar_one()
    if found != len(set(ids)):
        raise not_found("Category")


def set_assignment(db: Session, budget_id: str, month: date, category_id: str, assigned_cents: int, expected_version: int) -> dict:
    """Set the absolute assigned amount for (category, month).

    * expected_version must equal the stored version (0 = no row yet) else 409 version_conflict.
    * An INCREASE is rejected (422 over_allocation) if it would leave Ready to Assign negative in
      this month or any later month with data. Decreases are always allowed.
    """
    lock_budget(db, budget_id)
    _require_categories(db, budget_id, [category_id])
    ledger = load_ledger(db, budget_id)
    key = (category_id, month)
    current = ledger.assigned.get(key, 0)
    version = ledger.versions.get(key, 0)
    if expected_version != version:
        db.rollback()
        raise ApiError(
            409, "version_conflict", "This assignment changed since you loaded it. Reload and try again.",
            {"current_version": version, "current_assigned_cents": current},
        )
    delta = assigned_cents - current
    if delta == 0:
        db.rollback()
        return month_json(ledger, month)
    if delta > 0:
        horizon = ledger.last_data_month(month)
        before = min_ready_to_assign(ledger.timeline(horizon), month)
        ledger.assigned[key] = assigned_cents
        worst = min_ready_to_assign(ledger.timeline(horizon), month)
        if worst < 0:
            db.rollback()
            raise ApiError(
                422, "over_allocation", "That assignment is more than the money available to assign.",
                {"ready_to_assign_cents": before, "requested_increase_cents": delta},
            )
    mid = ensure_month(db, budget_id, month)
    _add_assigned(db, budget_id, mid, category_id, delta)
    db.commit()
    return month_json(load_ledger(db, budget_id), month)


def move_allocation(db: Session, budget_id: str, month: date, from_id: str, to_id: str, amount: int) -> dict:
    """Atomically move `amount` of assigned money between categories in `month`.
    Source needs available >= amount (incl. carry-in), else 422 insufficient_funds."""
    lock_budget(db, budget_id)
    _require_categories(db, budget_id, [from_id, to_id])
    ledger = load_ledger(db, budget_id)
    available = ledger.timeline(month)[month].categories[from_id].available
    if amount > available:
        db.rollback()
        raise ApiError(
            422, "insufficient_funds", "The source category does not have enough available money.",
            {"available_cents": available, "requested_cents": amount},
        )
    mid = ensure_month(db, budget_id, month)
    _add_assigned(db, budget_id, mid, from_id, -amount)
    _add_assigned(db, budget_id, mid, to_id, amount)
    db.commit()
    return month_json(load_ledger(db, budget_id), month)


# ---- groups & categories -------------------------------------------------------------

def _next_sort(db: Session, sql: str, params: dict) -> int:
    return db.execute(text(sql), params).scalar_one()


def list_groups(db: Session, budget_id: str) -> list[dict]:
    ledger = load_ledger(db, budget_id)
    return [
        {
            "id": str(g.id), "name": g.name, "sort_order": g.sort_order,
            "categories": [
                {"id": str(c.id), "name": c.name, "target_text": c.target_text, "sort_order": c.sort_order}
                for c in ledger.categories if c.group_id == g.id
            ],
        }
        for g in ledger.groups
    ]


def create_group(db: Session, budget_id: str, name: str, sort_order) -> dict:
    lock_budget(db, budget_id)
    if sort_order is None:
        sort_order = _next_sort(db, "SELECT COALESCE(max(sort_order), 0) + 1 FROM category_groups WHERE budget_id = :b", {"b": budget_id})
    try:
        row = db.execute(
            text("INSERT INTO category_groups (budget_id, name, sort_order) VALUES (:b, :n, :s) RETURNING id, name, sort_order"),
            {"b": budget_id, "n": name, "s": sort_order},
        ).one()
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "duplicate_name", "A group with that name already exists.")
    return {"id": str(row.id), "name": row.name, "sort_order": row.sort_order, "categories": []}


def update_group(db: Session, budget_id: str, group_id: str, fields: dict) -> dict:
    lock_budget(db, budget_id)
    row = db.execute(text("SELECT id, name, sort_order FROM category_groups WHERE id = :g AND budget_id = :b"), {"g": group_id, "b": budget_id}).first()
    if row is None:
        raise not_found("Group")
    name = fields.get("name") or row.name
    sort_order = fields["sort_order"] if fields.get("sort_order") is not None else row.sort_order
    try:
        db.execute(text("UPDATE category_groups SET name = :n, sort_order = :s WHERE id = :g AND budget_id = :b"), {"n": name, "s": sort_order, "g": group_id, "b": budget_id})
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "duplicate_name", "A group with that name already exists.")
    return {"id": group_id, "name": name, "sort_order": sort_order}


def delete_group(db: Session, budget_id: str, group_id: str) -> None:
    lock_budget(db, budget_id)
    if not db.execute(text("SELECT 1 FROM category_groups WHERE id = :g AND budget_id = :b"), {"g": group_id, "b": budget_id}).first():
        raise not_found("Group")
    if db.execute(text("SELECT 1 FROM categories WHERE group_id = :g AND budget_id = :b LIMIT 1"), {"g": group_id, "b": budget_id}).first():
        db.rollback()
        raise ApiError(409, "group_not_empty", "Move or delete the categories in this group first.")
    db.execute(text("DELETE FROM category_groups WHERE id = :g AND budget_id = :b"), {"g": group_id, "b": budget_id})
    db.commit()


def _require_group(db: Session, budget_id: str, group_id: str) -> None:
    if not db.execute(text("SELECT 1 FROM category_groups WHERE id = :g AND budget_id = :b"), {"g": group_id, "b": budget_id}).first():
        raise not_found("Group")


def create_category(db: Session, budget_id: str, group_id: str, name: str, target_text, sort_order) -> dict:
    lock_budget(db, budget_id)
    _require_group(db, budget_id, group_id)
    if sort_order is None:
        sort_order = _next_sort(db, "SELECT COALESCE(max(sort_order), 0) + 1 FROM categories WHERE group_id = :g", {"g": group_id})
    try:
        row = db.execute(
            text(
                """INSERT INTO categories (budget_id, group_id, name, target_text, sort_order)
                   VALUES (:b, :g, :n, :t, :s) RETURNING id, group_id, name, target_text, sort_order"""
            ),
            {"b": budget_id, "g": group_id, "n": name, "t": target_text, "s": sort_order},
        ).one()
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "duplicate_name", "A category with that name already exists.")
    return {"id": str(row.id), "group_id": str(row.group_id), "name": row.name, "target_text": row.target_text, "sort_order": row.sort_order}


def update_category(db: Session, budget_id: str, category_id: str, fields: dict) -> dict:
    lock_budget(db, budget_id)
    row = db.execute(
        text("SELECT id, group_id, name, target_text, sort_order FROM categories WHERE id = :c AND budget_id = :b"),
        {"c": category_id, "b": budget_id},
    ).first()
    if row is None:
        raise not_found("Category")
    group_id = str(fields["group_id"]) if fields.get("group_id") is not None else str(row.group_id)
    if group_id != str(row.group_id):
        _require_group(db, budget_id, group_id)
    name = fields.get("name") or row.name
    target = fields["target_text"] if "target_text" in fields else row.target_text
    sort_order = fields["sort_order"] if fields.get("sort_order") is not None else row.sort_order
    try:
        db.execute(
            text("UPDATE categories SET group_id = :g, name = :n, target_text = :t, sort_order = :s WHERE id = :c AND budget_id = :b"),
            {"g": group_id, "n": name, "t": target, "s": sort_order, "c": category_id, "b": budget_id},
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "duplicate_name", "A category with that name already exists.")
    return {"id": category_id, "group_id": group_id, "name": name, "target_text": target, "sort_order": sort_order}


def delete_category(db: Session, budget_id: str, category_id: str) -> None:
    """Only deletable when it has no transactions and no non-zero assignments."""
    lock_budget(db, budget_id)
    _require_categories(db, budget_id, [category_id])
    in_use = db.execute(
        text(
            """SELECT EXISTS (SELECT 1 FROM transactions WHERE category_id = :c AND budget_id = :b)
                   OR EXISTS (SELECT 1 FROM allocations WHERE category_id = :c AND budget_id = :b AND assigned_cents <> 0)"""
        ),
        {"c": category_id, "b": budget_id},
    ).scalar_one()
    if in_use:
        db.rollback()
        raise ApiError(409, "category_in_use", "This category has transactions or assigned money and cannot be deleted.")
    db.execute(text("DELETE FROM categories WHERE id = :c AND budget_id = :b"), {"c": category_id, "b": budget_id})
    db.commit()
