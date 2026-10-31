"""Request models. extra='forbid' means a client-supplied user_id/budget_id is rejected."""
from __future__ import annotations

import re
import uuid
from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, StrictInt, field_validator, model_validator

from .money import MAX_CENTS, parse_iso_date

_CTRL = re.compile(r"[\x00-\x1f\x7f]")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _clean_text(v: str, label: str, max_len: int, min_len: int = 1) -> str:
    if _CTRL.search(v):
        raise ValueError(f"{label} contains control characters.")
    if not (min_len <= len(v) <= max_len):
        raise ValueError(f"{label} must be {min_len}-{max_len} characters.")
    return v


class RegisterIn(Strict):
    email: EmailStr
    password: str
    display_name: Optional[str] = None

    @field_validator("email")
    @classmethod
    def _email(cls, v):
        return str(v).lower()

    @field_validator("password")
    @classmethod
    def _pw(cls, v: str) -> str:
        if not (12 <= len(v) <= 128):
            raise ValueError("Password must be 12-128 characters.")
        return v

    @field_validator("display_name")
    @classmethod
    def _dn(cls, v):
        return None if v is None or v == "" else _clean_text(v, "Display name", 80)


class LoginIn(Strict):
    email: EmailStr
    password: str

    @field_validator("email")
    @classmethod
    def _email(cls, v):
        return str(v).lower()

    @field_validator("password")
    @classmethod
    def _pw(cls, v: str) -> str:
        if not (1 <= len(v) <= 128):
            raise ValueError("Password must be 1-128 characters.")
        return v


def _cents(v: int, allow_negative: bool = False) -> int:
    if abs(v) > MAX_CENTS:
        raise ValueError("Amount is too large.")
    if not allow_negative and v < 0:
        raise ValueError("Amount must not be negative.")
    return v


class GroupIn(Strict):
    name: str
    sort_order: Optional[StrictInt] = None

    @field_validator("name")
    @classmethod
    def _n(cls, v):
        return _clean_text(v, "Name", 60)


class GroupPatch(Strict):
    name: Optional[str] = None
    sort_order: Optional[StrictInt] = None

    @field_validator("name")
    @classmethod
    def _n(cls, v):
        return None if v is None else _clean_text(v, "Name", 60)


class CategoryIn(Strict):
    group_id: uuid.UUID
    name: str
    target_text: Optional[str] = None
    sort_order: Optional[StrictInt] = None

    @field_validator("name")
    @classmethod
    def _n(cls, v):
        return _clean_text(v, "Name", 60)

    @field_validator("target_text")
    @classmethod
    def _t(cls, v):
        return None if v in (None, "") else _clean_text(v, "Target", 80)


class CategoryPatch(Strict):
    group_id: Optional[uuid.UUID] = None
    name: Optional[str] = None
    target_text: Optional[str] = None  # send null/"" to clear
    sort_order: Optional[StrictInt] = None

    @field_validator("name")
    @classmethod
    def _n(cls, v):
        return None if v is None else _clean_text(v, "Name", 60)

    @field_validator("target_text")
    @classmethod
    def _t(cls, v):
        return None if v in (None, "") else _clean_text(v, "Target", 80)


class AssignmentIn(Strict):
    assigned_cents: StrictInt
    expected_version: StrictInt  # assignment_version from the month payload (0 = never set)

    @field_validator("assigned_cents")
    @classmethod
    def _a(cls, v):
        return _cents(v)

    @field_validator("expected_version")
    @classmethod
    def _v(cls, v):
        if v < 0:
            raise ValueError("expected_version must be >= 0.")
        return v


class MoveIn(Strict):
    from_category_id: uuid.UUID
    to_category_id: uuid.UUID
    amount_cents: StrictInt

    @field_validator("amount_cents")
    @classmethod
    def _a(cls, v):
        if v <= 0:
            raise ValueError("Amount must be greater than zero.")
        return _cents(v)

    @model_validator(mode="after")
    def _distinct(self):
        if self.from_category_id == self.to_category_id:
            raise ValueError("Choose two different categories.")
        return self


class TransactionIn(Strict):
    occurred_on: str
    payee: str
    amount_cents: StrictInt  # negative = outflow, positive = inflow
    category_id: Optional[uuid.UUID] = None  # null = income (Ready to Assign)
    memo: Optional[str] = None

    @field_validator("occurred_on")
    @classmethod
    def _d(cls, v):
        parse_iso_date(v)
        return v

    @field_validator("payee")
    @classmethod
    def _p(cls, v):
        return _clean_text(v, "Payee", 120)

    @field_validator("memo")
    @classmethod
    def _m(cls, v):
        return None if v in (None, "") else _clean_text(v, "Memo", 500)

    @field_validator("amount_cents")
    @classmethod
    def _a(cls, v):
        if v == 0:
            raise ValueError("Amount must not be zero.")
        return _cents(v, allow_negative=True)

    @model_validator(mode="after")
    def _income_positive(self):
        if self.category_id is None and self.amount_cents <= 0:
            raise ValueError("Income (no category) must be a positive amount.")
        return self

    @property
    def date(self) -> date:
        return parse_iso_date(self.occurred_on)
