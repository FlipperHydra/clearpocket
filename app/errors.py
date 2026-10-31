from __future__ import annotations

from typing import Any


class ApiError(Exception):
    """Safe, client-facing error. Never put secrets or raw SQL in message/details."""

    def __init__(self, status: int, code: str, message: str, details: Any = None, headers: dict | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers or {}


def not_found(what: str = "Resource") -> ApiError:
    return ApiError(404, "not_found", f"{what} not found.")


def validation(message: str, details: Any = None, code: str = "validation_error") -> ApiError:
    return ApiError(422, code, message, details)
