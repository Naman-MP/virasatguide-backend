"""Validated input and output for the story agent."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class MonumentContext(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: int = Field(gt=0)
    name: str = Field(min_length=1)
    city: str = Field(min_length=1)
    location_tag: str = Field(min_length=1)
    cover_image: str = Field(min_length=1)
    reference_text: str = Field(min_length=1)


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1)
    url: HttpUrl
    snippet: str = Field(min_length=1)


class StoryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    monument_id: int = Field(gt=0)
    story: str = Field(min_length=1)
    sources: list[Source]
    research_status: Literal["web_results", "database_only"]
