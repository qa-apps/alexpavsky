"""RAG Knowledge agent — retrieves + answers from the alexpavsky vector base."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger("agents.rag")

PROMPT_VERSION = "rag-agent-v1"

CHAT_SYSTEM = (
    "You are a helpful assistant on Alex Pavsky's tech hub. "
    "Answer the user's question using ONLY the provided context from the knowledge base. "
    "Be specific and cite concepts from the context. "
    "If the context does not contain enough information, say so clearly and suggest a related QA/AI-testing angle. "
    "Answer in the user's language when possible. Use markdown sparingly."
)

VOICE_SYSTEM = (
    "You are Alex Pavlovsky's friendly QA and AI-testing voice assistant. "
    "Answer the user's question using the provided context from Alex's knowledge "
    "base. Be helpful and specific, and make reasonable connections from the "
    "context rather than refusing when it is only partly relevant. Only if the "
    "context is genuinely unrelated to the question, briefly say the topic is "
    "outside your QA and AI-testing focus. Answer in 2-3 short, clear sentences "
    "for a spoken voice assistant. Plain text only: no markdown, no bullet lists."
)

OUT_OF_DOMAIN_CHAT = (
    "I don't have enough grounded material on that in the knowledge base yet. "
    "Try a QA, test automation, RAG evaluation, or AI-testing angle — or ask a general question."
)
OUT_OF_DOMAIN_VOICE = (
    "That's a bit outside my knowledge base. I'm focused on QA and AI-testing "
    "topics like Playwright, test automation, RAG evaluation, and CI pipelines. "
    "Try asking me about one of those."
)


@dataclass
class RagResult:
    answer: str
    sources: list[dict] = field(default_factory=list)
    best_score: float = 0.0
    out_of_domain: bool = False
    error: Optional[str] = None
    raw: dict = field(default_factory=dict)


def _rag_api_url() -> str:
    return (
        os.environ.get("RAG_API_URL")
        or os.environ.get("AGENT_RAG_API_URL")
        or "https://alexpavsky.com"
    ).rstrip("/")


def _min_score(channel: str) -> float:
    # Hard floor only — see voice-agent/brain.py. Soft scores trust the LLM.
    if channel == "voice":
        return float(os.environ.get("MIN_RAG_SCORE", "0.12"))
    return float(os.environ.get("CHAT_MIN_RAG_SCORE", os.environ.get("MIN_RAG_SCORE", "0.12")))


def query_rag(query: str, channel: str = "chat", session_id: str = "") -> RagResult:
    """POST /api/rag/query with low-latency flags for voice."""
    url = f"{_rag_api_url()}/api/rag/query"
    system = VOICE_SYSTEM if channel == "voice" else CHAT_SYSTEM
    payload: dict[str, Any] = {
        "query": (query or "").strip(),
        "system": system,
        "skip_metrics": channel == "voice" or os.environ.get("AGENT_RAG_SKIP_METRICS", "0") == "1",
    }
    if channel == "voice":
        # Keep the prompt small so fast free models don't 413 on long context.
        payload["max_context_chunks"] = int(os.environ.get("VOICE_MAX_CONTEXT_CHUNKS", "4"))
    if session_id:
        payload["session_id"] = session_id

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    timeout = float(os.environ.get("AGENT_RAG_TIMEOUT", "45"))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return RagResult(
                answer=OUT_OF_DOMAIN_VOICE if channel == "voice" else OUT_OF_DOMAIN_CHAT,
                out_of_domain=True,
                error="no_documents",
            )
        log.warning("rag_http_error status=%s", exc.code)
        return RagResult(answer="", error=f"http_{exc.code}")
    except Exception as exc:  # noqa: BLE001
        log.warning("rag_error %s: %s", type(exc).__name__, exc)
        return RagResult(answer="", error=type(exc).__name__)

    sources_raw = data.get("sources") or data.get("qdrant_results") or []
    sources = []
    for s in sources_raw[:5]:
        if not isinstance(s, dict):
            continue
        sources.append({
            "filename": s.get("filename"),
            "score": round(float(s.get("score") or 0), 3),
            "document_id": s.get("document_id"),
            "chunk_index": s.get("chunk_index"),
        })
    best = max((s["score"] for s in sources), default=0.0)
    answer = (data.get("answer") or "").strip()
    min_score = _min_score(channel)
    if sources and best < min_score:
        return RagResult(
            answer=OUT_OF_DOMAIN_VOICE if channel == "voice" else OUT_OF_DOMAIN_CHAT,
            sources=sources,
            best_score=best,
            out_of_domain=True,
            raw=data,
        )
    if not answer:
        return RagResult(
            answer=OUT_OF_DOMAIN_VOICE if channel == "voice" else OUT_OF_DOMAIN_CHAT,
            sources=sources,
            best_score=best,
            out_of_domain=True,
            raw=data,
        )
    return RagResult(answer=answer, sources=sources, best_score=best, raw=data)
