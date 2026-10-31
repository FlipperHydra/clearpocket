from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import AuthContext, require_user, require_user_csrf
from ..errors import ApiError
from ..money import parse_month
from ..schemas import AssignmentIn, CategoryIn, CategoryPatch, GroupIn, GroupPatch, MoveIn
from ..services import budget as svc
from ..services import sample
from ..services.ledger import load_ledger, month_json, summary_json

router = APIRouter(prefix="/api", tags=["budget"])


def _month(value: str):
    try:
        return parse_month(value)
    except ValueError as exc:
        raise ApiError(422, "validation_error", str(exc), [{"field": "month", "message": str(exc)}])


@router.get("/months/{month}")
def get_month(month: str, ctx: AuthContext = Depends(require_user), db: Session = Depends(get_db)):
    """Full month view: summary + groups + categories (carry-in, assigned, activity, available)."""
    m = _month(month)
    return month_json(load_ledger(db, ctx.budget_id), m)


@router.get("/months/{month}/summary")
def get_summary(month: str, ctx: AuthContext = Depends(require_user), db: Session = Depends(get_db)):
    m = _month(month)
    return summary_json(m, load_ledger(db, ctx.budget_id).timeline(m)[m])


@router.put("/months/{month}/assignments/{category_id}")
def put_assignment(month: str, category_id: uuid.UUID, body: AssignmentIn, ctx: AuthContext = Depends(require_user_csrf), db: Session = Depends(get_db)):
    return svc.set_assignment(db, ctx.budget_id, _month(month), str(category_id), body.assigned_cents, body.expected_version)


@router.post("/months/{month}/moves")
def post_move(month: str, body: MoveIn, ctx: AuthContext = Depends(require_user_csrf), db: Session = Depends(get_db)):
    return svc.move_allocation(db, ctx.budget_id, _month(month), str(body.from_category_id), str(body.to_category_id), body.amount_cents)


@router.get("/category-groups")
def list_groups(ctx: AuthContext = Depends(require_user), db: Session = Depends(get_db)):
    return {"groups": svc.list_groups(db, ctx.budget_id)}


@router.post("/category-groups", status_code=201)
def create_group(body: GroupIn, ctx: AuthContext = Depends(require_user_csrf), db: Session = Depends(get_db)):
    return svc.create_group(db, ctx.budget_id, body.name, body.sort_order)


@router.patch("/category-groups/{group_id}")
def patch_group(group_id: uuid.UUID, body: GroupPatch, ctx: AuthContext = Depends(require_user_csrf), db: Session = Depends(get_db)):
    return svc.update_group(db, ctx.budget_id, str(group_id), body.model_dump(exclude_unset=True))


@router.delete("/category-groups/{group_id}", status_code=204)
def delete_group(group_id: uuid.UUID, ctx: AuthContext = Depends(require_user_csrf), db: Session = Depends(get_db)):
    svc.delete_group(db, ctx.budget_id, str(group_id))
    return Response(status_code=204)


@router.post("/categories", status_code=201)
def create_category(body: CategoryIn, ctx: AuthContext = Depends(require_user_csrf), db: Session = Depends(get_db)):
    return svc.create_category(db, ctx.budget_id, str(body.group_id), body.name, body.target_text, body.sort_order)


@router.patch("/categories/{category_id}")
def patch_category(category_id: uuid.UUID, body: CategoryPatch, ctx: AuthContext = Depends(require_user_csrf), db: Session = Depends(get_db)):
    return svc.update_category(db, ctx.budget_id, str(category_id), body.model_dump(exclude_unset=True))


@router.delete("/categories/{category_id}", status_code=204)
def delete_category(category_id: uuid.UUID, ctx: AuthContext = Depends(require_user_csrf), db: Session = Depends(get_db)):
    svc.delete_category(db, ctx.budget_id, str(category_id))
    return Response(status_code=204)


@router.post("/sample-data", status_code=201)
def load_sample(ctx: AuthContext = Depends(require_user_csrf), db: Session = Depends(get_db)):
    """Explicit opt-in: creates the demo budget (month 2026-09) for the signed-in user only."""
    sample.load_sample_data(db, ctx.budget_id)
    return {"loaded": True, "month": "2026-09"}
