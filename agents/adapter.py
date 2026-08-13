"""Channel response adapter — chat long vs voice short."""

from __future__ import annotations

import os
import re

PROMPT_VERSION = "adapter-v1"

MAX_SPEAK_CHARS = int(os.environ.get("MAX_SPEAK_CHARS", "600"))

VOICE_GREETING = (
    "Yes, I can hear you clearly. I'm Alex's QA assistant, built on his "
    "engineering knowledge base. Ask me about test automation, Playwright, "
    "RAG evaluation, or CI quality gates."
)
VOICE_IDENTITY = (
    "I'm a voice assistant running on Alex Pavlovsky's QA and AI-testing "
    "knowledge base. I can talk about Playwright, test automation, LLM "
    "evaluation, MCP servers, and cloud testing. What would you like to know?"
)
VOICE_THANKS = "You're welcome. Ask me anything else about QA or AI testing."

CHAT_GREETING = (
    "Hi! I'm the AI assistant on Alex Pavsky's tech hub — QA, AI testing, "
    "automation, and related topics. How can I help?"
)
CHAT_THANKS = "You're welcome. Anything else I can help with?"


def smalltalk_reply(message: str, channel: str, reason: str) -> str:
    lower = " ".join((message or "").lower().split())
    if channel == "voice":
        if "thank" in reason or lower in {"thanks", "thank you", "thx"}:
            return VOICE_THANKS
        if "identity" in reason or any(
            p in lower for p in ("who are you", "what are you", "what can you do")
        ):
            return VOICE_IDENTITY
        return VOICE_GREETING
    if "thank" in reason or lower in {"thanks", "thank you", "thx"}:
        return CHAT_THANKS
    return CHAT_GREETING


def trim_for_speech(text: str, max_chars=None) -> str:
    limit = max_chars if max_chars is not None else MAX_SPEAK_CHARS
    text = (text or "").strip()
    # Strip markdown-ish noise for TTS.
    text = re.sub(r"[#*_`]+", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\n{2,}", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for sep in (". ", "! ", "? "):
        idx = cut.rfind(sep)
        if idx > 120:
            return cut[: idx + 1]
    return cut


def adapt(reply: str, channel: str) -> tuple[str, str]:
    """Return (reply_for_ui, spoken_trim)."""
    reply = (reply or "").strip()
    if channel == "voice":
        spoken = trim_for_speech(reply)
        return reply, spoken
    return reply, ""
