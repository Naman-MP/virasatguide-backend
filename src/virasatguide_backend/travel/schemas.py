"""Fixed request and response formats shared by the API, LLM, and PDF."""

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from virasatguide_ai_agent.graph import clean_story_text
from virasatguide_ai_agent.models import public_source_url
from .search_models import Place, Mode, HotelPreferences, PlanningOffers, CostSummary, TransportSearchInput, iso_date


CHAT_HISTORY_LIMIT = 3


class Model(BaseModel):
    """Share strict request validation and plain-text cleanup across travel models."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)

    @field_validator("*", mode="before")
    @classmethod
    def clean_strings(cls, value):
        """Normalize escaped model text in string fields before validation."""
        return clean_story_text(value) if isinstance(value, str) else value


class SessionInput(Model):
    """Accept the positive monument ID used to start a tour."""
    monument_id: int = Field(gt=0)


class Evidence(Model):
    """Represent a named reference or excerpt that an answer or stop can cite."""
    id: str
    title: str
    text: str
    kind: Literal["monument_reference", "generated_story", "web_snippet", "provider_offer"]
    url: str | None = None
    _url = field_validator("url")(public_source_url)


class QuestionInput(Model):
    """Validate a follow-up question and its optional idempotency identifier."""
    question: str = Field(min_length=1, max_length=1200)
    request_id: UUID | None = None


class AnswerDraft(Model):
    """Structured model output before the backend verifies citations."""
    answer: str = Field(min_length=1, max_length=6000)
    evidence_ids: list[str] = Field(max_length=5)
    supported: bool


class Answer(Model):
    """Saved question and answer with support status, accepted evidence, and timestamp."""
    request_id: UUID
    question: str
    answer: str
    supported: bool
    evidence: list[Evidence]
    created_at: datetime


class TourSession(Model):
    """Public view of a private tour and its recent question history."""
    session_id: UUID
    monument_id: int
    monument_name: str
    story: str
    created_at: datetime
    history: list[Answer]


class Location(Model):
    """Validate optional latitude and longitude supplied for a trip."""
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class PlanInput(Model):
    """Capture the traveler's trip preferences, route, budget, and selected offers."""
    destination: str = Field(min_length=1, max_length=120)
    city: str | None = Field(default=None, min_length=1, max_length=120)
    area: str | None = Field(default=None, min_length=1, max_length=120)
    location: Location | None = None
    days: int = Field(default=1, ge=1, le=7, strict=True)
    travelers: int = Field(default=1, ge=1, le=20, strict=True)
    budget: int | None = Field(default=None, ge=0, le=100000000, strict=True)
    currency: str = Field(default="INR", pattern=r"^[A-Z]{3}$")
    interests: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(default_factory=list, max_length=8)

    origin: Place | None = None
    stops: list[Place] = Field(default_factory=list, max_length=6)
    departure_date: date | None = None
    return_date: date | None = None
    travel_mode: Mode | None = None
    hotel_preferences: HotelPreferences | None = None
    selected_transport_id: UUID | None = None
    selected_hotel_id: UUID | None = None

    _dates = field_validator("departure_date", "return_date", mode="before")(iso_date)

    @model_validator(mode="after")
    def valid_travel_details(self):
        """Check date order, offer prerequisites, room counts, currency, and consecutive stops."""
        if self.return_date and not self.departure_date:
            raise ValueError("return_date requires departure_date")
        if self.return_date and self.return_date < self.departure_date:
            raise ValueError("return_date cannot precede departure_date")
        if self.selected_transport_id and (not self.origin or not self.departure_date):
            raise ValueError("Selected transport requires origin and departure_date")
        if self.selected_hotel_id and not self.departure_date:
            raise ValueError("Selected accommodation requires departure_date")
        if self.hotel_preferences and self.hotel_preferences.rooms > self.travelers:
            raise ValueError("Rooms cannot exceed traveler count")
        TransportSearchInput.valid_currency(self.currency)
        route = ([self.origin.label] if self.origin else []) + [p.label for p in self.stops] + [self.destination]
        if any(a.casefold() == b.casefold() for a, b in zip(route, route[1:])):
            raise ValueError("Consecutive route locations must differ")
        return self


class Stop(Model):
    """One scheduled visit with duration, estimated cost, and evidence references."""
    place_name: str = Field(min_length=1, max_length=160)
    area: str = Field(min_length=1, max_length=160)
    time_of_day: Literal["morning", "afternoon", "evening"]
    description: str = Field(min_length=1, max_length=1000)
    duration_minutes: int = Field(ge=15, le=480)
    estimated_cost: int = Field(ge=0, le=100000000)
    evidence_ids: list[str] = Field(min_length=1, max_length=3)


class DayDraft(Model):
    """Model-produced visits and local costs for one itinerary day."""
    day: int = Field(ge=1, le=7)
    title: str = Field(min_length=1, max_length=140)
    stops: list[Stop] = Field(min_length=1, max_length=5)
    transport_cost: int = Field(ge=0, le=100000000)
    accommodation_cost: int = Field(ge=0, le=100000000)


class PlanDraft(Model):
    """Model-produced itinerary text and day cards before server validation."""
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=1600)
    days: list[DayDraft] = Field(min_length=1, max_length=7)
    tips: list[str] = Field(max_length=8)


class Day(DayDraft):
    """Validated itinerary day with its server-calculated total."""
    estimated_total: int


class TripPlan(Model):
    """Saved itinerary with ownership-independent display data, costs, and sources."""
    plan_id: UUID
    created_at: datetime
    request: PlanInput
    title: str
    summary: str
    days: list[Day]
    estimated_total: int | float
    budget_status: Literal["not_set", "within_budget", "over_budget"]
    sources: list[Evidence]
    tips: list[str]
    warnings: list[str]
    proximity_status: Literal["destination_search", "area_search", "coordinates_unverified"]
    selected_offers: PlanningOffers | None = None
    cost_summary: CostSummary | None = None
