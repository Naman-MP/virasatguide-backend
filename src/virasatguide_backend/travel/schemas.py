"""Fixed request and response formats shared by the API, LLM, and PDF."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from virasatguide_ai_agent.graph import clean_story_text


CHAT_HISTORY_LIMIT = 3


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)

    @field_validator("*", mode="before")
    @classmethod
    def clean_strings(cls, value):
        return clean_story_text(value) if isinstance(value, str) else value


class SessionInput(Model):
    monument_id: int = Field(gt=0)


class Evidence(Model):
    id: str
    title: str
    text: str
    kind: Literal["monument_reference", "generated_story", "web_snippet"]
    url: str | None = None


class QuestionInput(Model):
    question: str = Field(min_length=1, max_length=1200)
    request_id: UUID | None = None


class AnswerDraft(Model):
    answer: str = Field(min_length=1, max_length=6000)
    evidence_ids: list[str] = Field(max_length=5)
    supported: bool


class Answer(Model):
    request_id: UUID
    question: str
    answer: str
    supported: bool
    evidence: list[Evidence]
    created_at: datetime


class TourSession(Model):
    session_id: UUID
    monument_id: int
    monument_name: str
    story: str
    created_at: datetime
    history: list[Answer]


class Location(Model):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class PlanInput(Model):
    destination: str = Field(min_length=1, max_length=120)
    city: str | None = Field(default=None, min_length=1, max_length=120)
    area: str | None = Field(default=None, min_length=1, max_length=120)
    location: Location | None = None
    days: int = Field(default=1, ge=1, le=7)
    travelers: int = Field(default=1, ge=1, le=20)
    budget: int | None = Field(default=None, ge=0, le=100000000)
    currency: str = Field(default="INR", pattern=r"^[A-Z]{3}$")
    interests: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_interests(self):
        if any(not item.strip() or len(item) > 80 for item in self.interests):
            raise ValueError("Each interest must contain 1-80 characters")
        return self


class Stop(Model):
    place_name: str = Field(min_length=1, max_length=160)
    area: str = Field(min_length=1, max_length=160)
    time_of_day: Literal["morning", "afternoon", "evening"]
    description: str = Field(min_length=1, max_length=1000)
    duration_minutes: int = Field(ge=15, le=480)
    estimated_cost: int = Field(ge=0, le=100000000)
    evidence_ids: list[str] = Field(min_length=1, max_length=3)


class DayDraft(Model):
    day: int = Field(ge=1, le=7)
    title: str = Field(min_length=1, max_length=140)
    stops: list[Stop] = Field(min_length=1, max_length=5)
    transport_cost: int = Field(ge=0, le=100000000)
    accommodation_cost: int = Field(ge=0, le=100000000)


class PlanDraft(Model):
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=1600)
    days: list[DayDraft] = Field(min_length=1, max_length=7)
    tips: list[str] = Field(max_length=8)


class Day(DayDraft):
    estimated_total: int


class TripPlan(Model):
    plan_id: UUID
    created_at: datetime
    request: PlanInput
    title: str
    summary: str
    days: list[Day]
    estimated_total: int
    budget_status: Literal["not_set", "within_budget", "over_budget"]
    sources: list[Evidence]
    tips: list[str]
    warnings: list[str]
    proximity_status: Literal["destination_search", "area_search", "coordinates_unverified"]


def plain_text(value: str) -> str:
    return clean_story_text(value)
