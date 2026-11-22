"""Small lexical RAG over a snapshot of the selected monument's story and facts."""

import re

from fastapi import HTTPException

from virasatguide_ai_agent.graph import clean_story_text
from .schemas import CHAT_HISTORY_LIMIT, AnswerDraft, Evidence
from .llm import generate_json

STOP_WORDS = set("the a an is was were are of to in and it that this what who when where why how about me tell did does do its he she they for with".split())


def story_evidence(monument: dict, result: dict) -> list[Evidence]:
    """Collect the monument reference, stored narration, and search excerpts for retrieval."""
    documents = [
        ("reference", monument["name"] + " database reference", monument["reference_text"], "monument_reference", None),
        ("story", monument["name"] + " generated narration", result["story"], "generated_story", None),
    ]
    for i, source in enumerate(result.get("sources", []), 1):
        documents.append((f"web-{i}", source["title"], source["snippet"], "web_snippet", source["url"]))
    chunks = []
    for prefix, title, content, kind, url in documents:
        words = clean_story_text(content).split()
        for start in range(0, len(words), 100):
            chunks.append(Evidence(id=f"{prefix}-{start // 100 + 1}", title=title,
                                   text=" ".join(words[start:start + 125]), kind=kind, url=url))
    return chunks


def tokens(text: str) -> set[str]:
    """Normalize words for matching a question to saved evidence."""
    return set(re.findall(r"\w+", text.casefold())) - STOP_WORDS


def retrieve(question: str, evidence: list[Evidence], previous_question: str = "") -> list[Evidence]:
    """Rank saved passages by the keywords they share with the question."""
    query = tokens(question + " " + previous_question)
    # More shared words means a passage is more relevant. Ties keep source order.
    ranked = sorted(evidence, key=lambda item: len(query & tokens(item.text)), reverse=True)
    return ranked[:4]


async def answer_question(question: str, chunks: list[Evidence], history: list[dict]):
    """Answer from retrieved evidence and reject citations outside that evidence."""
    previous = history[-1]["question"] if history else ""
    selected = retrieve(question, chunks, previous)
    draft = await generate_json(
        AnswerDraft,
        "Answer the visitor's follow-up in at most 150 words using only the retrieved evidence. "
        "History helps resolve pronouns but is not evidence. Prefer the monument_reference over generated narration. "
        "Web snippets and generated stories may be incomplete. If the evidence cannot answer, say you do not have enough information, "
        "set supported=false and evidence_ids=[]. Otherwise set supported=true and cite the exact evidence IDs supporting the answer. "
        "Do not add outside facts, invent history or claim sources were independently verified.",
        {"question": question, "history": history[-CHAT_HISTORY_LIMIT:], "evidence": [e.model_dump() for e in selected]},
    )
    by_id = {e.id: e for e in selected}
    if any(key not in by_id for key in draft.evidence_ids) or (draft.supported and not draft.evidence_ids):
        raise HTTPException(502, "The answer contained invalid evidence references; please retry")
    if not draft.supported:
        return "I do not have enough information in this tour's sources to answer that question.", False, []
    cited_passages = []
    for evidence_id in draft.evidence_ids:
        passage = by_id[evidence_id]
        if passage not in cited_passages:
            cited_passages.append(passage)
    return draft.answer, True, cited_passages
