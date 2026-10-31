from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import Settings
from ..errors import ApiError
from ..security import hash_password, hash_token, needs_rehash, new_session_token, verify_password
from . import rate_limit

log = logging.getLogger("clearpocket.auth")


def create_session(db: Session, settings: Settings, user_id) -> str:
    token = new_session_token()
    db.execute(
        text(
            """INSERT INTO sessions (user_id, token_hash, expires_at, absolute_expires_at)
               VALUES (:u, :h, now() + make_interval(hours => CAST(:idle AS integer)),
                       now() + make_interval(days => CAST(:abs AS integer)))"""
        ),
        {"u": user_id, "h": hash_token(token), "idle": settings.session_idle_hours, "abs": settings.session_absolute_days},
    )
    return token


def register(db: Session, settings: Settings, ip: str, email: str, password: str, display_name):
    rate_limit.check_register(db, settings, ip)
    rate_limit.record(db, settings, "register-ip", ip)
    db.commit()  # the attempt counts even if the rest fails
    pw_hash = hash_password(password)
    try:
        user = db.execute(
            text(
                "INSERT INTO users (email, display_name, password_hash) VALUES (:e, :d, :p) "
                "RETURNING id, email, display_name"
            ),
            {"e": email, "d": display_name, "p": pw_hash},
        ).one()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "email_taken", "An account with this email already exists.")
    db.execute(text("INSERT INTO budgets (user_id) VALUES (:u)"), {"u": user.id})
    token = create_session(db, settings, user.id)
    db.commit()
    log.info("user registered user_id=%s", user.id)
    return user, token


def login(db: Session, settings: Settings, ip: str, email: str, password: str):
    rate_limit.check_login(db, settings, ip, email)
    rate_limit.record(db, settings, "login-ip", ip)
    row = db.execute(
        text("SELECT id, email, display_name, password_hash FROM users WHERE email = :e"), {"e": email}
    ).first()
    ok = verify_password(row.password_hash if row else None, password)
    if not ok:
        rate_limit.record(db, settings, "login-acct", email)
        db.commit()
        log.info("login failed")
        raise ApiError(401, "invalid_credentials", "Incorrect email or password.")
    if needs_rehash(row.password_hash):
        db.execute(text("UPDATE users SET password_hash = :p WHERE id = :u"), {"p": hash_password(password), "u": row.id})
    rate_limit.clear(db, settings, "login-acct", email)
    token = create_session(db, settings, row.id)  # always a fresh token (no session fixation)
    db.commit()
    log.info("login ok user_id=%s", row.id)
    return row, token


def logout(db: Session, session_id: str) -> None:
    db.execute(text("DELETE FROM sessions WHERE id = :s"), {"s": session_id})
    db.commit()
