"""Small web search tool used before writing a story."""

from urllib.parse import urlsplit

from ddgs import DDGS
from ddgs.exceptions import DDGSException
from pydantic import ValidationError

from .models import Source


def search_monument(name: str, city: str) -> list[Source]:
    """Find matching heritage references, trying DuckDuckGo before Bing."""
    query = f"{name} {city} history heritage official tourism"
    for engine in ("duckduckgo", "bing"):
        try:
            results = DDGS(timeout=8).text(query, backend=engine, max_results=5)
        except DDGSException:
            continue
        sources = []
        for result in results or []:
            url = result.get("href", "")
            try:
                if urlsplit(url).scheme != "https":
                    continue
                source = Source(title=str(result.get("title", ""))[:200],
                                url=url, snippet=str(result.get("body", ""))[:600])
            except (ValidationError, ValueError):
                continue
            text = (source.title + " " + source.snippet).casefold()
            if name.casefold() in text and city.casefold() in text:
                sources.append(source)
        if sources:
            return sources[:3]
    return []
