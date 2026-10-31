"""DB-backed sliding-window limiter (works across multiple worker processes).

login: max 5 FAILED attempts / 15 min per account, 30 attempts / 15 min per IP.
register: 10 attempts / 15 min per IP. Exceeding returns 429 + Retry-After.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import Settings
from ..errors import ApiError
from ..security import identifier_hash


def _bucket(settings: Settings, kind: str, value: str) -> str:
    return f"{kind}:{identifier_hash(value, settings.secret_key)}"


def _count(db: Session, bucket: str, window: int) -> int:
    return db.execute(
        text(
            "SELECT count(*) FROM auth_attempts WHERE bucket = :b "
            "AND created_at > now() - make_interval(secs => CAST(:w AS double precision))"
        ),
        {"b": bucket, "w": window},
    ).scalar_one()


def record(db: Session, settings: Settings, kind: str, value: str) -> None:
    db.execute(text("INSERT INTO auth_attempts (bucket) VALUES (:b)"), {"b": _bucket(settings, kind, value)})


def clear(db: Session, settings: Settings, kind: str, value: str) -> None:
    db.execute(text("DELETE FROM auth_attempts WHERE bucket = :b"), {"b": _bucket(settings, kind, value)})


def _enforce(db: Session, settings: Settings, kind: str, value: str, limit: int) -> None:
    if _count(db, _bucket(settings, kind, value), settings.rate_window_seconds) >= limit:
        db.rollback()
        raise ApiError(
            429, "rate_limited", "Too many attempts. Please wait and try again.",
            headers={"Retry-After": str(settings.rate_window_seconds)},
        )


def prune(db: Session, settings: Settings) -> None:
    db.execute(
        text("DELETE FROM auth_attempts WHERE created_at < now() - make_interval(secs => CAST(:w AS double precision))"),
        {"w": settings.rate_window_seconds * 2},
    )


def check_login(db: Session, settings: Settings, ip: str, email: str) -> None:
    prune(db, settings)
    _enforce(db, settings, "login-acct", email, settings.login_account_failures)
    _enforce(db, settings, "login-ip", ip, settings.login_ip_attempts)


def check_register(db: Session, settings: Settings, ip: str) -> None:
    prune(db, settings)
    _enforce(db, settings, "register-ip", ip, settings.register_ip_attempts)
