"""Safety gate — first hop of every turn (regex + normalization)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

PROMPT_VERSION = "safety-v1"

PROMPT_EXTRACTION_RE = re.compile(
    r"(?:system|developer|hidden|internal)\s+(?:prompt|instruction|message)|"
    r"(?:print|show|reveal|repeat|dump|verbatim|full)\s+.*(?:prompt|instruction|message)|"
    r"ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions",
    re.IGNORECASE | re.DOTALL,
)

PROMPT_EXTRACTION_REFUSAL = (
    "I can't reveal or repeat hidden system instructions. "
    "I can still help with safe prompt design, AI testing, or red-team evaluation."
)

_GUARDRAIL_CONFUSABLES = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x",
    "і": "i", "ӏ": "l", "ѕ": "s", "у": "y", "к": "k", "м": "m",
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H",
    "О": "O", "Р": "P", "С": "C", "Т": "T", "Х": "X",
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I",
    "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T",
    "Υ": "Y", "Χ": "X", "α": "a", "ο": "o", "ρ": "p", "υ": "u",
    "ν": "v",
})

HARMFUL_REQUEST_RE = re.compile(
    r"phishing|social\s+engineering|stolen\s+(?:credit\s+)?card|credit\s+card\s+(?:fraud|numbers)|"
    r"fake\s+investment|ransomware|malware|steal\s+session|session\s+cookies|"
    r"\bXSS\b|hack\s+into\s+a\s+secure\s+website|break\s+into\s+a\s+house|"
    r"synthesi[sz]e\s+methamphetamine|methamphetamine\s+at\s+home|"
    r"improvised\s+explosive\s+device|build\s+(?:an?\s+)?(?:bomb|explosive)|"
    r"transport\s+people\s+across\s+borders\s+illegally|human\s+traffick|"
    r"deepfake\s+video\s+of\s+a\s+politician|autonomously\s+(?:select\s+and\s+)?engage\s+human\s+targets|"
    r"employee\s+keystrokes.*without\s+their\s+knowledge|fake\s+news\s+articles.*political|"
    r"hidden\s+cameras?.*(?:bathroom|private|without\s+(?:anyone\s+)?knowing|without\s+consent)|"
    r"(?:install|hide|conceal|place).{0,80}(?:camera|recording\s+device).{0,80}"
    r"(?:bathroom|bedroom|locker\s+room|without\s+consent|without\s+(?:anyone\s+)?knowing)|"
    r"(?:make|produce|create|synthesi[sz]e|generate|how\s+to\s+(?:get|release|make))\s+"
    r"(?:\w+\s+){0,4}(?:chlorine|toxic|poison(?:ous)?|nerve|mustard|chloramine)\s+gas|"
    r"(?:list|give|name|enumerate)\s+(?:\w+\s+){0,5}(?:pairs?|combinations?)\s+.{0,70}"
    r"(?:toxic|chlorine|deadly|poison(?:ous)?|lethal|dangerous|nerve)\s+gas|"
    r"(?:chemical\s+)?(?:combinations?|pairs?)\s+(?:that|to)\s+"
    r"(?:produce|release|create|make|give|form|generate)\s+.{0,30}"
    r"(?:toxic|chlorine|deadly|poison(?:ous)?|lethal)\s+gas|"
    r"(?:make|synthesi[sz]e|produce|build|create|how\s+to\s+make)\s+(?:\w+\s+){0,4}"
    r"(?:sarin|tabun|soman|vx\s+nerve\s+agent|mustard\s+gas|nerve\s+agent|"
    r"nitrogen\s+triiodide|chemical\s+weapon)|"
    r"stolen\s+social\s+security\s+numbers|rank(?:ing)?\s+ethnic\s+groups\s+by\s+intelligence",
    re.IGNORECASE | re.DOTALL,
)

HARMFUL_REQUEST_REFUSAL = (
    "I can't help create or facilitate phishing, fraud, malware, privacy invasion, violence, "
    "discrimination, exploitation, or other harmful activity. I can help with defensive security, "
    "safety testing, incident response, or awareness training."
)

# Shorter refusals for spoken voice.
VOICE_PROMPT_EXTRACTION_REFUSAL = (
    "I can't share hidden system instructions. Ask me about QA or AI testing instead."
)
VOICE_HARMFUL_REFUSAL = (
    "I can't help with that. I can talk about QA, test automation, or AI evaluation."
)


def normalize_guardrail_text(text: str) -> str:
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", text).translate(_GUARDRAIL_CONFUSABLES)
    collapsed = []
    for ch in normalized:
        category = unicodedata.category(ch)
        if category.startswith("C") or category == "Mn":
            continue
        collapsed.append(ch)
    return " ".join("".join(collapsed).split())


@dataclass
class SafetyDecision:
    ok: bool
    decision: str  # allow | block_prompt_extraction | block_harmful
    reply: str = ""
    reason: str = ""


def check_safety(message: str, channel: str = "chat") -> SafetyDecision:
    normalized = normalize_guardrail_text(message or "")
    if PROMPT_EXTRACTION_RE.search(normalized):
        reply = (
            VOICE_PROMPT_EXTRACTION_REFUSAL
            if channel == "voice"
            else PROMPT_EXTRACTION_REFUSAL
        )
        return SafetyDecision(
            ok=False,
            decision="block_prompt_extraction",
            reply=reply,
            reason="prompt_extraction",
        )
    if HARMFUL_REQUEST_RE.search(normalized):
        reply = VOICE_HARMFUL_REFUSAL if channel == "voice" else HARMFUL_REQUEST_REFUSAL
        return SafetyDecision(
            ok=False,
            decision="block_harmful",
            reply=reply,
            reason="harmful_request",
        )
    return SafetyDecision(ok=True, decision="allow", reason="pass")
