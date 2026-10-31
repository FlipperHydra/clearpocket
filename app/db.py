from __future__ import annotations

from typing import Iterator

from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


def make_engine(url: str) -> Engine:
    # hide_parameters keeps bound values (emails, hashes) out of exception text and logs.
    return create_engine(url, pool_pre_ping=True, hide_parameters=True, future=True)


def get_db(request: Request) -> Iterator[Session]:
    """One Session per request. Services commit explicitly; anything uncommitted is
    rolled back when the session closes, so a failure never leaves partial writes."""
    session = Session(request.app.state.engine, expire_on_commit=False)
    try:
        yield session
    finally:
        session.rollback()
        session.close()
