"""Validated traveler fields and the public account response."""

import re
from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .credentials import public_login_fields

Gender = Literal["male", "female", "non_binary", "other", "prefer_not_to_say"]


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    full_name: str = Field(min_length=1, max_length=120)
    date_of_birth: date | None = None
    gender: Gender | None = "prefer_not_to_say"

    @field_validator("date_of_birth", mode="before")
    @classmethod
    def check_birth_date(cls, value):
        if value is None:
            return None
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("Use YYYY-MM-DD for date_of_birth")
        born = date.fromisoformat(value)
        if not date(1900, 1, 1) <= born <= datetime.now(timezone.utc).date():
            raise ValueError("date_of_birth must be between 1900-01-01 and today (UTC)")
        return born

    @field_validator("gender", mode="before")
    @classmethod
    def allow_undisclosed_gender(cls, value):
        return "prefer_not_to_say" if value is None else value


class PublicProfile(BaseModel):
    id: str = Field(serialization_alias="_id")
    username: str
    login_username: str | None = None
    credentials_set: bool = False
    full_name: str | None
    date_of_birth: date | None
    gender: Gender
    onboarding_completed: bool
    name: str | None = None
    email: str | None = None
    google_sub: str | None = None


def public_profile(user: dict) -> PublicProfile:
    return PublicProfile(
        id=str(user["_id"]), username=user["username"], **public_login_fields(user),
        full_name=user.get("full_name") or user.get("name"),
        date_of_birth=user.get("date_of_birth"),
        gender=user.get("gender") or "prefer_not_to_say",
        onboarding_completed=bool(user.get("onboarding_completed", False)),
        name=user.get("name"), email=user.get("email"), google_sub=user.get("google_sub"),
    )
