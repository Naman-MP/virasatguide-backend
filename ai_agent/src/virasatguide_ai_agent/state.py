"""Values passed between the two LangGraph steps."""

from typing import TypedDict

from .models import MonumentContext, Source


class StoryState(TypedDict, total=False):
    """Intermediate graph state shared by the research and narration nodes."""
    monument: MonumentContext
    sources: list[Source]
    research_status: str
    story: str
