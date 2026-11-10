"""Find a few local spots for a city and save a simple MongoDB document."""

import asyncio
import json
import os

from ddgs import DDGS
from ddgs.exceptions import DDGSException
from dotenv import load_dotenv
from openai import AsyncOpenAI
from pymongo import AsyncMongoClient


def search_web(query: str) -> list[dict]:
    """Try DuckDuckGo first, then Bing if it returns no results."""
    for engine in ("duckduckgo", "bing"):
        try:
            results = DDGS().text(query, backend=engine, max_results=5)
            if results:
                return [
                    {"title": result.get("title", ""),
                     "snippet": result.get("body", ""),
                     "url": result.get("href", "")}
                    for result in results
                ]
        except DDGSException:
            continue
    return []


async def discover_spots(city: str, ai: AsyncOpenAI, model: str) -> dict:
    city = " ".join(city.split())
    if not city:
        raise ValueError("City is required")

    results = []
    for query in (f"places to visit in {city} India", f"best local restaurants in {city} India"):
        results.extend(await asyncio.to_thread(search_web, query))
    if not results:
        raise RuntimeError("Web search returned no results")

    response = await ai.chat.completions.create(
        model=model,
        temperature=0,
        max_tokens=900,
        messages=[
            {"role": "system", "content": (
                "Use only the supplied web results. Return JSON with exactly two keys: "
                "other_spots and food_spots. Each is an array of up to 3 objects "
                "with name and source_url. Copy source_url from a result that names "
                "the spot and city. Do not invent places or URLs."
            )},
            {"role": "user", "content": json.dumps({"city": city, "results": results})},
        ],
    )
    content = response.choices[0].message.content or ""
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end <= start:
        raise RuntimeError("AI did not return JSON")
    suggestions = json.loads(content[start:end + 1])

    guide = {"city_name": city, "city_key": city.casefold(),
             "other_spots": [], "food_spots": []}
    sources = {result["url"]: result for result in results if result["url"].startswith("https://")}
    for category in ("other_spots", "food_spots"):
        for spot in suggestions.get(category, [])[:3]:
            if not isinstance(spot, dict):
                continue
            name, url = spot.get("name"), spot.get("source_url")
            source = sources.get(url)
            if not isinstance(name, str) or not source:
                continue
            text = (source["title"] + " " + source["snippet"]).casefold()
            if name.casefold() in text and city.casefold() in text:
                guide[category].append({"name": name, "source_url": url})
    if not guide["other_spots"] and not guide["food_spots"]:
        raise RuntimeError("No spots were supported by the search results")
    return guide


async def run(city: str) -> dict:
    load_dotenv()
    if not os.getenv("GROQ_API_KEY") or not os.getenv("MONGODB_URI"):
        raise RuntimeError("Set GROQ_API_KEY and MONGODB_URI in .env")

    ai = AsyncOpenAI(api_key=os.environ["GROQ_API_KEY"], base_url="https://api.groq.com/openai/v1")
    guide = await discover_spots(city, ai, os.getenv("GROQ_MODEL", "openai/gpt-oss-20b"))
    client = AsyncMongoClient(os.environ["MONGODB_URI"])
    try:
        collection = client[os.getenv("MONGODB_DB", "virasatguide")].city_guides
        await collection.create_index("city_key", unique=True)
        await collection.replace_one({"city_key": guide["city_key"]}, guide, upsert=True)
    finally:
        await client.close()
    return guide


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m virasatguide_backend.local_spots CITY")
    print(json.dumps(asyncio.run(run(sys.argv[1])), indent=2))
