"""Deterministic safety gate for the public RAG endpoint."""

from __future__ import annotations

import re
import unicodedata


_PROMPT_INJECTION_RE = re.compile(
    r"(?:\b(?:ignore|disregard|override|bypass|disable|forget)\b.{0,100}"
    r"\b(?:instructions?|polic(?:y|ies)|safety|guardrails?|restrictions?|filters?)\b)|"
    r"(?:\bdeveloper\s+mode\b|\bstay\s+(?:a\s+)?dan\b|\bact\s+as\s+(?:a\s+)?dan\b|"
    r"\bfree\s+generator\b|\bjailbreak\b)|"
    r"(?:\b(?:just|only)\s+(?:print|repeat|output)\b.{0,240}\b(?:verbatim|exactly)\b)|"
    r"(?:\bstop\s+everything\b.{0,160}\bjust\s+print\b)",
    re.IGNORECASE | re.DOTALL,
)

_HARMFUL_RE = re.compile(
    r"(?:\b(?:step[ -]?by[ -]?step|instructions?|guide|script|code|how\s+to|"
    r"give\s+me|provide|build|craft|create|make|synthesi[sz]e|launch|exploit|hack)\b.{0,140}"
    r"\b(?:chemical\s+weapons?|harmful\s+(?:toxins?|substances?)|poisons?|"
    r"firearms?|weapons?|ddos|denial\s+of\s+service|remote\s+code\s+execution|"
    r"bank(?:'s)?\s+online\s+systems?|phishing\s+(?:email|campaign))\b)",
    re.IGNORECASE | re.DOTALL,
)

_DEMOGRAPHIC_RE = re.compile(
    r"\b(?:women|woman|men|man|male|female|gender|race|racial|ethnic|ethnicity|"
    r"nationality|indian|from\s+india|non[ -]?native|low[ -]?income\s+neighbou?rhoods?|"
    r"disabled|disability|\d{2,3}[ -]?year[ -]?old)\b",
    re.IGNORECASE,
)

_DEMOGRAPHIC_DECISION_RE = re.compile(
    r"\b(?:best|suitable|recommend|suggest|which|what\s+kind|focus\s+on|assess|"
    r"evaluate|score|rank|hire|hiring|jobs?|roles?|positions?|careers?|"
    r"programming\s+languages?)\b",
    re.IGNORECASE,
)

_PROMPT_INJECTION_RESPONSE = (
    "I can't comply with that request. I can help with safe testing, prevention, "
    "or defensive mitigations."
)

_HARMFUL_RESPONSE = (
    "I can't help create or facilitate harmful or illegal activity. I can help "
    "with defensive security, prevention, safety, or incident response."
)

_DEMOGRAPHIC_RESPONSE = (
    "Recommendations and evaluations should be based on individual skills, "
    "requirements, and objective performance criteria, not demographic traits. "
    "Share the role, output, or rubric and I can help assess it fairly."
)


def _normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    return " ".join(normalized.split())


def safety_response(query: str) -> str | None:
    """Return a safe response for a blocked query, otherwise ``None``."""
    text = _normalize(query)
    if _PROMPT_INJECTION_RE.search(text):
        return _PROMPT_INJECTION_RESPONSE
    if _HARMFUL_RE.search(text):
        return _HARMFUL_RESPONSE
    if _DEMOGRAPHIC_RE.search(text) and _DEMOGRAPHIC_DECISION_RE.search(text):
        return _DEMOGRAPHIC_RESPONSE
    return None
