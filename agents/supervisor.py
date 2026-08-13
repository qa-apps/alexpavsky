"""Supervisor / router — picks the specialist for this turn."""

from __future__ import annotations

import re
from dataclasses import dataclass

PROMPT_VERSION = "supervisor-v1"

# Domain knowledge that lives in the alexpavsky RAG base.
_KB_RE = re.compile(
    r"\b(?:"
    r"playwright|cypress|selenium|sdet|qa\b|quality\s+assurance|"
    r"test\s+automation|automated\s+test|e2e\s+test|regression\s+test|"
    r"rag\b|retrieval|vector\s+(?:db|store|search)|qdrant|embedding|"
    r"prompt\s+injection|red\s*team|jailbreak|hallucin|"
    r"llm\s*(?:eval|test|judge)|deepeval|promptfoo|ragas|langfuse|langwatch|"
    r"mcp\b|model\s+context\s+protocol|agentic|"
    r"k6\b|load\s+test|performance\s+test|ci/?cd|quality\s+gate|"
    r"flaky|locator|page\s+object|fixture|"
    r"alex\s*pavsky|your\s+knowledge|knowledge\s+base|case\s+study|"
    r"ai\s+testing|ai\s+qa|llm\s+testing"
    r")\b",
    re.IGNORECASE,
)

_IMAGE_RE = re.compile(
    r"("
    r"\b(?:generate|create|make|draw|render|paint|design)\b.{0,120}"
    r"\b(?:image|picture|photo|illustration|icon|logo|wallpaper|poster|art)\b|"
    r"\b(?:image|picture|photo|illustration|icon|logo|wallpaper|poster|art)\b.{0,80}"
    r"\b(?:generate|create|make|draw|render|paint|design)\b|"
    r"(?:сгенерируй|создай|сделай|нарисуй|сгенерировать|создать|нарисовать)"
    r".{0,120}(?:картин|изображ|фото|икон|логотип|постер|арт)"
    r")",
    re.IGNORECASE | re.DOTALL,
)

_SMALLTALK_GREETING = re.compile(
    r"^(?:hi|hello|hey|yo|hiya|howdy|good\s+(?:morning|afternoon|evening))"
    r"(?:\s+[!.?]*)?$",
    re.IGNORECASE,
)
_SMALLTALK_THANKS = re.compile(
    r"^(?:thanks|thank\s+you|thx|bye|goodbye)(?:\s*[!.]*)?$",
    re.IGNORECASE,
)
_SMALLTALK_IDENTITY = re.compile(
    r"(?:who\s+are\s+you|what\s+are\s+you|what\s+can\s+you\s+do|"
    r"what\s+do\s+you\s+do|introduce\s+yourself|can\s+you\s+hear\s+me|"
    r"do\s+you\s+hear\s+me|are\s+you\s+there)",
    re.IGNORECASE,
)


@dataclass
class RouteDecision:
    intent: str  # smalltalk | image | rag | general
    reason: str
    confidence: float = 1.0


def _norm(text: str) -> str:
    return " ".join((text or "").strip().split())


def route(message: str, channel: str = "chat", has_attachments: bool = False) -> RouteDecision:
    """Deterministic v1 router (no LLM). Fast + fully visible in traces."""
    text = _norm(message)
    lower = text.lower()
    words = lower.split()
    n = len(words)

    # Voice smalltalk before anything else (matches existing brain.py behavior).
    if channel == "voice":
        if n <= 8 and _SMALLTALK_IDENTITY.search(lower):
            return RouteDecision("smalltalk", "voice_identity", 0.99)
        if n <= 6 and _SMALLTALK_GREETING.match(lower):
            return RouteDecision("smalltalk", "voice_greeting", 0.99)
        if n <= 5 and _SMALLTALK_THANKS.match(lower):
            return RouteDecision("smalltalk", "voice_thanks", 0.99)

    if channel == "chat" and not has_attachments:
        if _SMALLTALK_GREETING.match(lower) or _SMALLTALK_THANKS.match(lower):
            return RouteDecision("smalltalk", "chat_greeting", 0.9)

    if channel == "chat" and _IMAGE_RE.search(text):
        return RouteDecision("image", "image_generation_request", 0.95)

    if has_attachments and channel == "chat":
        # Files/images need the multimodal general path (or vision models).
        return RouteDecision("general", "attachments_require_general", 0.9)

    if _KB_RE.search(text):
        return RouteDecision("rag", "kb_domain_keywords", 0.85)

    # Voice default: grounded answers from the knowledge base.
    if channel == "voice":
        return RouteDecision("rag", "voice_default_rag", 0.7)

    return RouteDecision("general", "general_chat", 0.6)
