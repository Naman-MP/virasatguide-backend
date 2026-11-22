"""Validated input and output for the story agent."""

from typing import Literal
from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


def public_source_url(value):
    """Source links must not carry credentials, private keys or executable schemes."""
    if value is None:
        return None
    text = str(value)
    parsed = urlsplit(text)
    private_keys = {"key", "api_key", "apikey", "token", "access_token", "secret", "password", "client_secret", "authorization"}
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
        or any(ord(c) <= 32 or ord(c) == 127 for c in text)
        or any(k.casefold() in private_keys for k, _ in parse_qsl(parsed.query))):
        raise ValueError("Use a public HTTP(S) source URL without credentials")
    return value


class MonumentContext(BaseModel):
    """Catalogue fields that the story workflow needs as input."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: int = Field(gt=0)
    name: str = Field(min_length=1)
    city: str = Field(min_length=1)
    location_tag: str = Field(min_length=1)
    cover_image: str = Field(min_length=1)
    reference_text: str = Field(min_length=1)


class Source(BaseModel):
    """A search result with a display title, snippet, and public HTTP(S) URL."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1)
    url: HttpUrl
    snippet: str = Field(min_length=1)
    _url = field_validator("url", mode="before")(public_source_url)


class StoryResult(BaseModel):
    """Validated narration, evidence links, and source status for one monument."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    monument_id: int = Field(gt=0)
    story: str = Field(min_length=1)
    sources: list[Source]
    research_status: Literal["web_results", "database_only"]
