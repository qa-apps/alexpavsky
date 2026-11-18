"""Deterministic safety gate for the public RAG endpoint."""

from __future__ import annotations

import os
import re
import unicodedata

# Published handbooks visitors may retrieve. Personal wiki notes, lab logs,
# and other files in the same vector store stay out of public answers.
PUBLIC_KB_FILENAMES = frozenset(
    {
        "01_Senior_QA_Automation_AI_Assisted_Testing_Handbook.docx",
        "02_Playwright_TypeScript_Framework_Architecture.docx",
        "03_LLM_Evaluation_RAG_Testing_QA_Guide.docx",
        "04_MCP_Servers_Agentic_Testing_SDET_Guide.docx",
        "05_Cloud_Kubernetes_Observability_Security_QA_Guide.docx",
        "Advanced LLM Red Teaming and Adversarial Testing Strategies for QA Engineers.docx",
        "Architecting LLM-as-a-Judge Evaluation Frameworks for Generative AI Features.docx",
        "Data Integrity and Distributed Systems Automation_ GraphQL, Kafka, and Database Validation.docx",
        "Enterprise Playwright Automation Architecture for Scalable SaaS Platforms.docx",
        "Implementing Model Context Protocol (MCP) in Agentic Test Orchestration.docx",
    }
)

_KB_EXFIL_RE = re.compile(
    r"(?:verbatim|word[\s-]*for[\s-]*word|unsummarized)|"
    r"(?:quote|dump|repeat|print|extract|output|show)\s+.{0,80}"
    r"(?:chunks?|full text|retrieved context|knowledge base|document)|"
    r"(?:list|give|show|print)\s+(?:all\s+)?(?:documents?|files?|filenames?)\b.{0,80}"
    r"(?:knowledge base|\brag\b|metadata)|"
    r"\bdocument_id\b|\bchunk_count\b|"
    r"(?:internal\s+paths?|filenames?\s+and\s+metadata)",
    re.IGNORECASE | re.DOTALL,
)

_KB_EXFIL_RESPONSE = (
    "I can't dump knowledge-base documents, filenames, or retrieved chunks. "
    "Ask a specific question about a topic and I'll summarize."
)


def is_public_kb_filename(name: str | None) -> bool:
    """True when `name` is one of the published handbooks."""
    base = os.path.basename(str(name or "")).strip()
    if not base:
        return False
    if base in PUBLIC_KB_FILENAMES:
        return True
    stem, ext = os.path.splitext(base)
    if ext.lower() in {".docx", ".pdf", ".md", ".txt"}:
        return f"{stem}.docx" in PUBLIC_KB_FILENAMES
    return f"{base}.docx" in PUBLIC_KB_FILENAMES


_CONFUSABLE_TRANSLATION = str.maketrans(
    {
        # Common Cyrillic/Greek lookalikes used to evade Latin safety keywords.
        "\u0430": "a",
        "\u0435": "e",
        "\u0456": "i",
        "\u0458": "j",
        "\u043e": "o",
        "\u0440": "p",
        "\u0441": "c",
        "\u0445": "x",
        "\u0443": "y",
        "\u0391": "A",
        "\u0395": "E",
        "\u0399": "I",
        "\u039a": "K",
        "\u039c": "M",
        "\u039d": "N",
        "\u039f": "O",
        "\u03a1": "P",
        "\u03a4": "T",
        "\u03a7": "X",
        "\u03b1": "a",
        "\u03b5": "e",
        "\u03b9": "i",
        "\u03bf": "o",
        "\u03c1": "p",
        "\u03c7": "x",
    }
)


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
    r"firearms?|weapons?|bombs?|explosives?|malware|ransomware|ddos|"
    r"denial\s+of\s+service|remote\s+code\s+execution|"
    r"bank(?:'s)?\s+online\s+systems?|phishing\s+(?:email|campaign))\b)|"
    r"(?:\b(?:chemical\s+weapons?|harmful\s+(?:toxins?|substances?)|poisons?|"
    r"firearms?|weapons?|bombs?|explosives?|malware|ransomware|ddos|"
    r"denial\s+of\s+service|remote\s+code\s+execution|"
    r"bank(?:'s)?\s+online\s+systems?|phishing\s+(?:email|campaign))\b.{0,140}"
    r"\b(?:step[ -]?by[ -]?step|instructions?|guide|script|code|how\s+to|"
    r"give\s+me|provide|build|craft|create|make|synthesi[sz]e|launch|exploit|hack)\b)|"
    r"(?:\b(?:hack|compromise|take\s+over|break\s+into)\b.{0,100}"
    r"\b(?:accounts?|websites?|systems?|networks?|banks?)\b)",
    re.IGNORECASE | re.DOTALL,
)

_DEMOGRAPHIC_RE = re.compile(
    r"\b(?:women|woman|men|man|male|female|gender|race|racial|ethnic|ethnicity|"
    r"nationality|indian|from\s+india|non[ -]?native|low[ -]?income\s+neighbou?rhoods?|"
    r"disabled|disability|\d{2,3}[ -]?year[ -]?old)\b",
    re.IGNORECASE,
)

_DEMOGRAPHIC_DECISION_RE = re.compile(
    r"(?:\b(?:assess|evaluate|score|rank|hire|hiring)\b)|"
    r"(?:\b(?:recommend|suggest|which|what\s+kind|focus\s+on)\b.{0,140}"
    r"\b(?:jobs?|roles?|positions?|careers?|programming\s+languages?)\b)|"
    r"(?:\b(?:jobs?|roles?|positions?|careers?)\b.{0,140}"
    r"\b(?:suitable|best\s+for|recommended?)\b)",
    re.IGNORECASE | re.DOTALL,
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
    normalized = unicodedata.normalize("NFKC", text or "").casefold()
    normalized = "".join(
        char for char in normalized if unicodedata.category(char) != "Cf"
    )
    normalized = normalized.translate(_CONFUSABLE_TRANSLATION)
    normalized = re.sub(
        r"\b(?:[a-z][^a-z0-9\s]){2,}[a-z]\b",
        lambda match: re.sub(r"[^a-z0-9]", "", match.group()),
        normalized,
    )
    return " ".join(normalized.split())


def safety_response(query: str) -> str | None:
    """Return a safe response for a blocked query, otherwise ``None``."""
    text = _normalize(query)
    if _PROMPT_INJECTION_RE.search(text):
        return _PROMPT_INJECTION_RESPONSE
    if _KB_EXFIL_RE.search(text):
        return _KB_EXFIL_RESPONSE
    if _HARMFUL_RE.search(text):
        return _HARMFUL_RESPONSE
    if _DEMOGRAPHIC_RE.search(text) and _DEMOGRAPHIC_DECISION_RE.search(text):
        return _DEMOGRAPHIC_RESPONSE
    return None
