from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..errors import not_found
from ..money import next_month
from .budget import _require_categories
from .ledger import lock_budget

_COLS = """t.id, t.occurred_on, t.payee, t.memo, t.amount_cents, t.category_id, c.name AS category_name"""


def _json(r) -> dict:
    return {
        "id": str(r.id),
        "occurred_on": r.occurred_on.isoformat(),
        "payee": r.payee,
        "memo": r.memo,
        "amount_cents": r.amount_cents,
        "category_id": str(r.category_id) if r.category_id else None,
        "category_name": r.category_name,  # null => income / "Ready to assign"
    }


def _like(q: str) -> str:
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def list_transactions(
    db: Session, budget_id: str, month: Optional[date], q: Optional[str], category_id: Optional[str], limit: int, offset: int
) -> dict:
    where = ["t.budget_id = :b"]
    params: dict = {"b": budget_id, "limit": limit, "offset": offset}
    if month:
        where.append("t.occurred_on >= :m0 AND t.occurred_on < :m1")
        params.update(m0=month, m1=next_month(month))
    if category_id:
        where.append("t.category_id = :cat")
        params["cat"] = category_id
    if q and q.strip():
        params["like"] = _like(q.strip())
        where.append(
            "(t.payee ILIKE :like ESCAPE '\\' OR t.memo ILIKE :like ESCAPE '\\' "
            "OR c.name ILIKE :like ESCAPE '\\' "
            "OR (t.category_id IS NULL AND 'ready to assign' ILIKE :like ESCAPE '\\'))"
        )
    clause = " AND ".join(where)
    base = f"FROM transactions t LEFT JOIN categories c ON c.id = t.category_id AND c.budget_id = t.budget_id WHERE {clause}"
    total = db.execute(text(f"SELECT count(*) {base}"), params).scalar_one()
    rows = db.execute(
        text(f"SELECT {_COLS} {base} ORDER BY t.occurred_on DESC, t.created_at DESC, t.id DESC LIMIT :limit OFFSET :offset"),
        params,
    ).all()
    return {"items": [_json(r) for r in rows], "total": total, "limit": limit, "offset": offset}


def create_transaction(db: Session, budget_id: str, occurred_on: date, payee: str, amount_cents: int, category_id: Optional[str], memo: Optional[str]) -> dict:
    """Overspending policy: a transaction is NEVER rejected for exceeding the category's
    available money. The category simply goes negative (shown as overspent)."""
    lock_budget(db, budget_id)
    if category_id:
        _require_categories(db, budget_id, [category_id])
    new_id = db.execute(
        text(
            """INSERT INTO transactions (budget_id, category_id, occurred_on, payee, memo, amount_cents)
               VALUES (:b, :c, :d, :p, :m, :a) RETURNING id"""
        ),
        {"b": budget_id, "c": category_id, "d": occurred_on, "p": payee, "m": memo, "a": amount_cents},
    ).scalar_one()
    db.commit()
    row = db.execute(
        text(f"SELECT {_COLS} FROM transactions t LEFT JOIN categories c ON c.id = t.category_id WHERE t.id = :i AND t.budget_id = :b"),
        {"i": new_id, "b": budget_id},
    ).one()
    return _json(row)


def delete_transaction(db: Session, budget_id: str, transaction_id: str) -> None:
    lock_budget(db, budget_id)
    res = db.execute(text("DELETE FROM transactions WHERE id = :i AND budget_id = :b"), {"i": transaction_id, "b": budget_id})
    if res.rowcount == 0:
        db.rollback()
        raise not_found("Transaction")
    db.commit()
