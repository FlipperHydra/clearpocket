from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import Settings
from ..db import get_db
from ..deps import AuthContext, optional_user, origin_guard, require_user_csrf
from ..schemas import LoginIn, RegisterIn
from ..security import csrf_token_for
from ..services import auth as auth_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _set_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        settings.session_cookie_name, token,
        max_age=settings.session_absolute_days * 86400,
        httponly=True, secure=settings.cookie_secure, samesite=settings.cookie_samesite, path="/",
    )


def _user_json(row) -> dict:
    return {"id": str(row.id), "email": row.email, "display_name": row.display_name}


@router.post("/register", status_code=201, dependencies=[Depends(origin_guard)])
def register(payload: RegisterIn, request: Request, response: Response, db: Session = Depends(get_db)):
    settings: Settings = request.app.state.settings
    user, token = auth_service.register(db, settings, _ip(request), payload.email, payload.password, payload.display_name)
    _set_cookie(response, settings, token)
    return {"user": _user_json(user), "csrf_token": csrf_token_for(token, settings.secret_key)}


@router.post("/login", dependencies=[Depends(origin_guard)])
def login(payload: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    settings: Settings = request.app.state.settings
    user, token = auth_service.login(db, settings, _ip(request), payload.email, payload.password)
    _set_cookie(response, settings, token)
    return {"user": _user_json(user), "csrf_token": csrf_token_for(token, settings.secret_key)}


@router.post("/logout")
def logout(request: Request, response: Response, ctx: AuthContext = Depends(require_user_csrf), db: Session = Depends(get_db)):
    settings: Settings = request.app.state.settings
    auth_service.logout(db, ctx.session_id)
    response.delete_cookie(settings.session_cookie_name, path="/", secure=settings.cookie_secure, httponly=True, samesite=settings.cookie_samesite)
    return {"ok": True}


@router.get("/session")
def session(request: Request, ctx: Optional[AuthContext] = Depends(optional_user), db: Session = Depends(get_db)):
    """Always 200. Frontend bootstrap: tells it whether to show login or the app."""
    settings: Settings = request.app.state.settings
    if ctx is None:
        return {"authenticated": False, "user": None, "csrf_token": None}
    user = db.execute(text("SELECT id, email, display_name FROM users WHERE id = :u"), {"u": ctx.user_id}).one()
    return {"authenticated": True, "user": _user_json(user), "csrf_token": csrf_token_for(ctx.session_token, settings.secret_key)}
