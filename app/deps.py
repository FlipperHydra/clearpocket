from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from .config import Settings
from .db import get_db
from .errors import ApiError
from .security import csrf_token_for, hash_token, tokens_equal

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass(frozen=True)
class AuthContext:
    user_id: str
    session_id: str
    session_token: str
    budget_id: str


def check_origin(request: Request) -> None:
    """Reject cross-site browser requests. Browsers always send Origin on unsafe methods."""
    settings: Settings = request.app.state.settings
    origin = request.headers.get("origin")
    if origin is not None and origin.rstrip("/") not in settings.allowed_origins:
        raise ApiError(403, "bad_origin", "Request origin is not allowed.")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise ApiError(403, "bad_origin", "Cross-site requests are not allowed.")


def origin_guard(request: Request) -> None:
    """Dependency for unauthenticated state-changing endpoints (register/login)."""
    if request.method in UNSAFE:
        check_origin(request)


def _load_session(request: Request, db: Session) -> Optional[AuthContext]:
    settings: Settings = request.app.state.settings
    token = request.cookies.get(settings.session_cookie_name)
    if not token or len(token) > 200:
        return None
    row = db.execute(
        text(
            """
            SELECT s.id AS sid, s.user_id, b.id AS budget_id,
                   s.last_seen_at < now() - interval '1 minute' AS stale
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            JOIN budgets b ON b.user_id = u.id
            WHERE s.token_hash = :h AND s.expires_at > now() AND s.absolute_expires_at > now()
            """
        ),
        {"h": hash_token(token)},
    ).first()
    if row is None:
        return None
    if row.stale:  # sliding idle timeout, capped by the absolute lifetime
        db.execute(
            text(
                """UPDATE sessions SET last_seen_at = now(),
                   expires_at = LEAST(now() + make_interval(hours => CAST(:hrs AS integer)), absolute_expires_at)
                   WHERE id = :sid"""
            ),
            {"sid": row.sid, "hrs": settings.session_idle_hours},
        )
        db.commit()
    return AuthContext(str(row.user_id), str(row.sid), token, str(row.budget_id))


def optional_user(request: Request, db: Session = Depends(get_db)) -> Optional[AuthContext]:
    return _load_session(request, db)


def require_user(request: Request, db: Session = Depends(get_db)) -> AuthContext:
    """Identity comes ONLY from the server-side session behind the HttpOnly cookie."""
    ctx = _load_session(request, db)
    if ctx is None:
        raise ApiError(401, "unauthenticated", "Authentication required.")
    return ctx


def require_user_csrf(request: Request, ctx: AuthContext = Depends(require_user)) -> AuthContext:
    """For state-changing private endpoints: session + Origin check + CSRF header."""
    settings: Settings = request.app.state.settings
    check_origin(request)
    supplied = request.headers.get("x-csrf-token", "")
    expected = csrf_token_for(ctx.session_token, settings.secret_key)
    if not supplied or not tokens_equal(supplied, expected):
        raise ApiError(403, "csrf_failed", "Missing or invalid CSRF token.")
    return ctx
