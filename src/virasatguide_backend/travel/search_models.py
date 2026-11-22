"""Stable transport/hotel search contracts; offers can only come from providers."""

import re
from datetime import date, datetime, timezone
from typing import Annotated, Literal
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID, uuid4

import pycountry
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime, field_validator, model_validator

Mode = Literal["car", "train", "flight", "bus"]
SearchStatus = Literal["complete", "partial", "unavailable"]


def iso_date(value):
    if value is None:
        return None
    if not isinstance(value, (str, date)) or isinstance(value, datetime):
        raise ValueError("Use YYYY-MM-DD dates")
    if isinstance(value, str) and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Use YYYY-MM-DD dates")
    return value


def future_dates(start: date, end: date | None):
    if start < datetime.now(timezone.utc).date():
        raise ValueError("Travel dates must be today (UTC) or later")
    if end is not None and end < start:
        raise ValueError("The end date cannot precede the start date")


def safe_booking_url(value):
    if value is None:
        return None
    if any(ord(char) <= 32 or ord(char) == 127 for char in str(value)):
        raise ValueError("A booking URL cannot contain spaces or control characters")
    parsed = urlsplit(str(value))
    if parsed.port is not None and parsed.port < 1:
        raise ValueError("Invalid booking URL port")
    secret_fields = {"key", "api_key", "apikey", "token", "access_token", "secret", "password", "client_secret", "authorization"}
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
        or parsed.fragment or any(k.casefold() in secret_fields for k, _ in parse_qsl(parsed.query))):
        raise ValueError("A booking URL must be a public HTTPS provider page without credentials")
    return str(value)


class SearchModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False, revalidate_instances="always")


class Place(SearchModel):
    label: str = Field(min_length=1, max_length=200)


class HotelPreferences(SearchModel):
    star_ratings: list[Annotated[int, Field(strict=True, ge=1, le=5)]] = Field(default_factory=list, max_length=5)
    rooms: int = Field(default=1, ge=1, le=20, strict=True)

    @field_validator("star_ratings")
    @classmethod
    def valid_stars(cls, values):
        if len(set(values)) != len(values) or any(v not in {1, 2, 3, 4, 5} for v in values):
            raise ValueError("star_ratings must contain distinct numbers from 1 to 5")
        return values


class TransportSearchInput(SearchModel):
    origin: Place
    destination: Place
    stops: list[Place] = Field(default_factory=list, max_length=6)
    departure_date: date
    return_date: date | None = None
    travelers: int = Field(default=1, ge=1, le=20, strict=True)
    modes: list[Mode] = Field(default_factory=lambda: ["car", "train", "flight", "bus"], min_length=1, max_length=4)
    currency: str = Field(default="INR", pattern=r"^[A-Z]{3}$")

    _dates = field_validator("departure_date", "return_date", mode="before")(iso_date)

    @field_validator("currency")
    @classmethod
    def valid_currency(cls, value):
        if pycountry.currencies.get(alpha_3=value) is None:
            raise ValueError("Use an ISO 4217 currency code")
        return value

    @model_validator(mode="after")
    def valid_journey(self):
        future_dates(self.departure_date, self.return_date)
        labels = [p.label.casefold() for p in [self.origin, *self.stops, self.destination]]
        if any(a == b for a, b in zip(labels, labels[1:])):
            raise ValueError("Consecutive journey locations must differ")
        if len(set(self.modes)) != len(self.modes):
            raise ValueError("Transport modes must be distinct")
        return self


class HotelSearchInput(HotelPreferences):
    destination: str = Field(min_length=1, max_length=200)
    check_in: date
    check_out: date
    travelers: int = Field(default=1, ge=1, le=20, strict=True)
    currency: str = Field(default="INR", pattern=r"^[A-Z]{3}$")
    _dates = field_validator("check_in", "check_out", mode="before")(iso_date)
    _currency = field_validator("currency")(TransportSearchInput.valid_currency)

    @model_validator(mode="after")
    def valid_stay(self):
        future_dates(self.check_in, self.check_out)
        if self.check_out <= self.check_in or (self.check_out - self.check_in).days > 30:
            raise ValueError("Stay length must be 1-30 nights")
        if self.rooms > self.travelers:
            raise ValueError("Rooms cannot exceed traveler count")
        return self


class Price(SearchModel):
    @field_validator("amount", mode="before")
    @classmethod
    def numeric_amount(cls, value):
        if isinstance(value, bool):
            raise ValueError("A price must be a number, not a boolean")
        return value

    amount: float = Field(ge=0, le=1_000_000_000_000)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    type: Literal["live", "estimate"]


class TransportOption(SearchModel):
    id: UUID = Field(default_factory=uuid4)
    mode: Mode
    provider: str = Field(min_length=1, max_length=120)
    service_name: str = Field(min_length=1, max_length=200)
    departure_at: AwareDatetime | None = None
    arrival_at: AwareDatetime | None = None
    duration_minutes: int | None = Field(default=None, gt=0)
    price: Price | None = None
    booking_url: str | None = None
    retrieved_at: AwareDatetime
    _url = field_validator("booking_url")(safe_booking_url)

    @model_validator(mode="after")
    def valid_times(self):
        if self.departure_at and self.arrival_at and self.arrival_at <= self.departure_at:
            raise ValueError("Arrival must follow departure")
        return self


class HotelOption(SearchModel):
    id: UUID = Field(default_factory=uuid4)
    hotel_name: str = Field(min_length=1, max_length=200)
    location: str = Field(min_length=1, max_length=200)
    star_rating: int | None = Field(default=None, ge=1, le=5)
    provider: str = Field(min_length=1, max_length=120)
    nightly_price: Price | None = None
    total_price: Price | None = None
    booking_url: str | None = None
    retrieved_at: AwareDatetime
    _url = field_validator("booking_url")(safe_booking_url)


class TransportSearchResult(SearchModel):
    search_id: UUID = Field(default_factory=uuid4)
    status: SearchStatus
    searched_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    currency: str
    results: list[TransportOption] = Field(default_factory=list)
    unavailable_modes: list[Mode] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class HotelSearchResult(SearchModel):
    search_id: UUID = Field(default_factory=uuid4)
    status: SearchStatus
    searched_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    results: list[HotelOption] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PlanningOffers(SearchModel):
    transport: TransportOption | None = None
    hotel: HotelOption | None = None
    hotel_rooms: int | None = Field(default=None, ge=1, le=20)
    hotel_check_in: date | None = None
    hotel_check_out: date | None = None


class CostSummary(SearchModel):
    currency: str
    itinerary_estimate: int
    confirmed_total: float
    selected_estimate_total: float
    trip_total: float
    costs_complete: bool
