from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import AuthContext, require_user, require_user_csrf
from ..errors import ApiError
from ..money import parse_month
from ..schemas import TransactionIn
from ..services import transactions as svc

router = APIRouter(prefix="/api/transactions", tags=["transactions"])


@router.get("")
def list_transactions(
    month: Optional[str] = Query(None, description="YYYY-MM"),
    q: Optional[str] = Query(None, max_length=100),
    category_id: Optional[uuid.UUID] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=100000),
    ctx: AuthContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    m = None
    if month:
        try:
            m = parse_month(month)
        except ValueError as exc:
            raise ApiError(422, "validation_error", str(exc), [{"field": "month", "message": str(exc)}])
    return svc.list_transactions(db, ctx.budget_id, m, q, str(category_id) if category_id else None, limit, offset)


@router.post("", status_code=201)
def create_transaction(body: TransactionIn, ctx: AuthContext = Depends(require_user_csrf), db: Session = Depends(get_db)):
    return svc.create_transaction(
        db, ctx.budget_id, body.date, body.payee, body.amount_cents,
        str(body.category_id) if body.category_id else None, body.memo,
    )


@router.delete("/{transaction_id}", status_code=204)
def delete_transaction(transaction_id: uuid.UUID, ctx: AuthContext = Depends(require_user_csrf), db: Session = Depends(get_db)):
    svc.delete_transaction(db, ctx.budget_id, str(transaction_id))
    return Response(status_code=204)
