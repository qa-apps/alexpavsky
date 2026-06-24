"""
llm.py — Multi-provider LLM orchestration layer (showcase extract)
==================================================================

Standalone, self-contained extract of the LLM orchestration behind the AI chat
on alexpavsky.com. NOT wired into the running server — a clean reference for the
design.

It serves answers from a pool of ~30 free-tier models across 7 providers (Groq,
OpenRouter, Gemini, Hugging Face, Cerebras, SambaNova, Mistral). Free tiers fail
constantly and differently (rate limits, daily quotas, credit resets, vanished
models), so a single dependable `generate()` is built on four ideas:

  1. INTENT ROUTING   — classify the request and pick the cheapest tier that
                        fits; don't send "hi" to a 405B model.
  2. HEALTH TRACKING  — on failure, read the HTTP response and cool down the
                        right scope (model / provider / free pool) for the right
                        duration (90s blip → until next UTC midnight for a quota).
  3. FALLBACK CHAINS  — retry a *different provider* first, so one outage doesn't
                        cascade through every model behind it.
  4. GRACEFUL DEGRADE — if the whole pool is down, answer trivial messages
                        locally instead of erroring.

Reads provider keys from the same env vars as production (GROQ_API_KEY, …); with
no keys it still imports and routes, only network calls return "missing key".
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

log = logging.getLogger("llm")


# ════════════════════════════════════════════════════════════════════════════
# 1. PROVIDER CONFIG
# ════════════════════════════════════════════════════════════════════════════
# Every provider speaks the OpenAI chat-completions wire format *except* Gemini,
# which has its own request/response shape (handled separately in `_call_gemini`).

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models"
OPENROUTER_REFERER = "https://alexpavsky.com"
OPENROUTER_TITLE = "AlexPavsky AI Chat"

PROVIDER_API_URL = {
    "groq": GROQ_API_URL,
    "openrouter": OPENROUTER_API_URL,
    "huggingface": "https://router.huggingface.co/v1/chat/completions",
    "cerebras": "https://api.cerebras.ai/v1/chat/completions",
    "sambanova": "https://api.sambanova.ai/v1/chat/completions",
    "mistral": "https://api.mistral.ai/v1/chat/completions",
}

# Each provider's API key lives in an env var. Some have legacy aliases so a
# typo'd deployment secret still resolves instead of silently disabling a whole
# provider.
PROVIDER_KEY_ENV = {
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "huggingface": "HF_TOKEN",
    "cerebras": "CEREBRAS_API_KEY",
    "sambanova": "SAMBANOVA_API_KEY",
    "mistral": "MISTRAL_API_KEY",
}
PROVIDER_KEY_ALIASES = {
    "huggingface": ("HUGGINGFACE_API_KEY",),
    "cerebras": ("CEREBRES_API_KEY",),
    "sambanova": ("SAMBA_API_KEY",),
}


def _provider_env_names(provider):
    primary = PROVIDER_KEY_ENV.get(provider) or f"{str(provider).upper().replace('-', '_')}_API_KEY"
    extras = PROVIDER_KEY_ALIASES.get(provider, ())
    return (primary,) + tuple(name for name in extras if name != primary)


def _provider_api_key(provider):
    for env_name in _provider_env_names(provider):
        value = (os.environ.get(env_name) or "").strip()
        if value:
            return value
    return ""


def _provider_available(provider):
    """A provider is usable only if we actually hold a key for it."""
    return bool(_provider_api_key(provider))


# ════════════════════════════════════════════════════════════════════════════
# 2. MODEL REGISTRY
# ════════════════════════════════════════════════════════════════════════════
# Each model is tagged with a tier (S/M/H = small/medium/heavy) and capability
# flags (vision / coding / search / reasoning). The router selects against these
# tags. In production this list is *also* refreshed at runtime from the
# OpenRouter and Hugging Face catalog APIs so newly-released free models get
# picked up automatically; here we keep the curated static seed for clarity.

_STATIC_MODELS = [
    # Google Gemini — the only vision-capable free models in the pool
    {"id": "gemini-2.0-flash", "label": "Gemini 2.0 Flash", "provider": "gemini", "vision": True, "tier": "S"},
    {"id": "gemini-2.5-flash", "label": "Gemini 2.5 Flash", "provider": "gemini", "vision": True, "tier": "M"},
    {"id": "gemini-2.5-pro", "label": "Gemini 2.5 Pro", "provider": "gemini", "vision": True, "tier": "H"},
    # OpenRouter free tier — broad selection, day-quota limited
    {"id": "google/gemma-3-27b-it:free", "label": "Gemma 3 27B", "provider": "openrouter", "tier": "S"},
    {"id": "meta-llama/llama-3.3-70b-instruct:free", "label": "Llama 3.3 70B", "provider": "openrouter", "tier": "M"},
    {"id": "deepseek/deepseek-r1-0528:free", "label": "DeepSeek R1", "provider": "openrouter", "tier": "H", "reasoning": True},
    {"id": "qwen/qwen3-coder:free", "label": "Qwen 3 Coder 480B", "provider": "openrouter", "tier": "H", "coding": True},
    {"id": "nousresearch/hermes-3-llama-3.1-405b:free", "label": "Hermes 3 405B", "provider": "openrouter", "tier": "H"},
    # Groq — fastest inference, includes web-search "compound" models
    {"id": "llama-3.1-8b-instant", "label": "Llama 3.1 8B Instant", "provider": "groq", "tier": "S"},
    {"id": "llama-3.3-70b-versatile", "label": "Llama 3.3 70B", "provider": "groq", "tier": "M"},
    {"id": "qwen/qwen3-32b", "label": "Qwen 3 32B", "provider": "groq", "tier": "H", "coding": True},
    {"id": "openai/gpt-oss-120b", "label": "GPT-OSS 120B", "provider": "groq", "tier": "H"},
    {"id": "groq/compound", "label": "Compound AI", "provider": "groq", "tier": "H", "search": True},
    {"id": "groq/compound-mini", "label": "Compound Mini", "provider": "groq", "tier": "M", "search": True},
    # Cerebras / SambaNova / Mistral — extra capacity, different rate-limit clocks
    {"id": "llama3.1-8b", "label": "Cerebras Llama 3.1 8B", "provider": "cerebras", "tier": "S"},
    {"id": "llama-3.3-70b", "label": "Cerebras Llama 3.3 70B", "provider": "cerebras", "tier": "H"},
    {"id": "DeepSeek-V3.1", "label": "SambaNova DeepSeek V3.1", "provider": "sambanova", "tier": "M"},
    {"id": "mistral-small-latest", "label": "Mistral Small", "provider": "mistral", "tier": "S"},
    {"id": "codestral-latest", "label": "Codestral", "provider": "mistral", "tier": "H", "coding": True},
    {"id": "mistral-large-latest", "label": "Mistral Large", "provider": "mistral", "tier": "H"},
]

# Derived views over the registry, rebuilt whenever the pool changes. Routing
# reads these tier/capability buckets instead of scanning the full list.
_model_lock = threading.Lock()
CHAT_MODELS = []
MODEL_BY_ID = {}
VISION_MODEL_IDS = set()
TIER_S, TIER_M, TIER_H = [], [], []
CODING_MODELS, SEARCH_MODELS = [], []


def _rebuild_model_views(models):
    """Recompute the tier/capability buckets from a flat model list."""
    global CHAT_MODELS, MODEL_BY_ID, VISION_MODEL_IDS
    global TIER_S, TIER_M, TIER_H, CODING_MODELS, SEARCH_MODELS
    with _model_lock:
        models.sort(key=lambda m: m.get("created", 0), reverse=True)  # newest first
        CHAT_MODELS = models
        MODEL_BY_ID = {m["id"]: m for m in models}
        VISION_MODEL_IDS = {m["id"] for m in models if m.get("vision")}
        TIER_S = [m for m in models if m["tier"] == "S"]
        TIER_M = [m for m in models if m["tier"] == "M"]
        TIER_H = [m for m in models if m["tier"] == "H"]
        CODING_MODELS = [m for m in models if m.get("coding")]
        SEARCH_MODELS = [m for m in models if m.get("search")]


_rebuild_model_views(list(_STATIC_MODELS))


# ════════════════════════════════════════════════════════════════════════════
# 3. INTENT CLASSIFICATION (cheap, regex-based — runs before any network call)
# ════════════════════════════════════════════════════════════════════════════
# Bilingual (EN/RU) because the site serves both audiences. These never touch a
# model — they just decide *which* model the request deserves.

_SIMPLE = re.compile(
    r"^(?:hi|hello|hey|привет|здравствуй|thanks|спасибо|ok|okay|ок|"
    r"yes|no|да|нет|bye|пока|how\s+are\s+you|как\s+дела)$",
    re.IGNORECASE,
)
_CODE = re.compile(
    r"(?:code|код|script|function|функци|debug|python|javascript|typescript|"
    r"html|css|sql|react|fix\s+(?:the|this|my)\s+(?:bug|error|code)|"
    r"почини|implement|реализуй|```|def\s+\w+|class\s+\w+|import\s+\w+)",
    re.IGNORECASE,
)
_SEARCH = re.compile(
    r"(?:latest|newest|current|today|2024|2025|2026|последн|новост|сегодня|"
    r"актуальн|search\s+for|who\s+won|what\s+happened|price\s+of|stock|"
    r"weather|погода|курс)",
    re.IGNORECASE,
)
_COMPLEX = re.compile(
    r"(?:анализ|проанализируй|сравни|compare|analyze|explain\s+in\s+detail|"
    r"step[\s-]by[\s-]step|пошагов|таблиц|table|algorithm|алгоритм|"
    r"architect|архитектур|research|исследован|multi[\s-]?step|углубл)",
    re.IGNORECASE,
)


# ════════════════════════════════════════════════════════════════════════════
# 4. HEALTH TRACKING — provider-aware failure classification
# ════════════════════════════════════════════════════════════════════════════
# The heart of the resilience story. When a call fails we don't blindly retry —
# we read the status code + headers + body and decide:
#   • WHAT scope to disable: a single model, a whole provider, or just the
#     "free" sub-pool of a provider (OpenRouter free models share one daily cap).
#   • FOR HOW LONG: a 429 blip → minutes; a daily quota → until next midnight in
#     the provider's billing timezone; an auth failure → an hour.
# Cooldowns live in a shared dict guarded by a lock (the server is threaded).

MODEL_HEALTH = {}
MODEL_HEALTH_LOCK = threading.Lock()


def _parse_ratelimit_seconds(headers):
    """Read a cooldown hint from rate-limit headers (Retry-After in seconds,
    or a `t=<seconds>` field in a RateLimit header). None if absent."""
    lower = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    for key in ("retry-after", "x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
        try:
            return max(0, int(float(lower[key])))
        except (KeyError, ValueError):
            continue
    match = re.search(r"(?:^|[;,])\s*t=(\d+)", lower.get("ratelimit", ""))
    return int(match.group(1)) if match else None


def _seconds_until_next_utc_day():
    now = datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).date()
    return int((datetime.combine(tomorrow, datetime.min.time(), timezone.utc) - now).total_seconds())


def _seconds_until_next_pacific_day():
    tz = ZoneInfo("America/Los_Angeles") if ZoneInfo else timezone.utc
    now = datetime.now(tz)
    tomorrow = (now + timedelta(days=1)).date()
    return int((datetime.combine(tomorrow, datetime.min.time(), tz) - now).total_seconds())


def _seconds_until_next_utc_month():
    now = datetime.now(timezone.utc)
    year = now.year + (1 if now.month == 12 else 0)
    month = 1 if now.month == 12 else now.month + 1
    return int((datetime(year, month, 1, tzinfo=timezone.utc) - now).total_seconds())


def _health_key(scope, provider, model_id=None):
    return f"{scope}:{provider}:{model_id}" if model_id else f"{scope}:{provider}"


def _model_health_keys(model):
    """The cooldown keys that can disable a given model: its own, its provider's,
    and — for OpenRouter free models — the shared free-pool key."""
    provider = model.get("provider", "groq")
    keys = [_health_key("provider", provider), _health_key("model", provider, model.get("id"))]
    if provider == "openrouter" and str(model.get("id", "")).endswith(":free"):
        keys.append(_health_key("pool", "openrouter", "free"))
    return keys


def _model_available(model):
    """True if the provider key exists and no active cooldown covers this model.
    Expired cooldowns are cleaned up lazily on read."""
    if not model or not _provider_available(model.get("provider", "groq")):
        return False
    now = time.time()
    with MODEL_HEALTH_LOCK:
        for key in _model_health_keys(model):
            item = MODEL_HEALTH.get(key)
            if item and item.get("disabled_until", 0) > now:
                return False
            if item and item.get("disabled_until", 0) <= now:
                MODEL_HEALTH.pop(key, None)
    return True


def _cooldown_target(model, err):
    """Map a failure to (scope, key, cooldown_seconds, reason).

    This is where provider quirks are encoded:
      • OpenRouter free models share a per-DAY cap → disable the whole free pool
        until next UTC midnight, not just the one model.
      • Gemini daily limits reset on Pacific time, not UTC.
      • Hugging Face runs on a MONTHLY credit budget.
      • Auth errors (401/403) usually mean a dead key → disable the provider.
      • 404 / 'no endpoints' means the model vanished → bench it for a day.
    """
    provider = model.get("provider", "groq")
    model_id = model.get("id")
    headers = err.get("headers", {}) if isinstance(err, dict) else {}
    body = (err.get("body", "") if isinstance(err, dict) else str(err or "")).lower()
    status = err.get("status") if isinstance(err, dict) else None
    code = err.get("code", "") if isinstance(err, dict) else str(err or "")

    retry_seconds = _parse_ratelimit_seconds(headers)
    if status == 429:
        if provider == "openrouter" and any(s in body for s in ("free-models-per-day", "requests per day", "daily", "per-day")):
            return "pool", _health_key("pool", "openrouter", "free"), _seconds_until_next_utc_day(), "openrouter_free_daily_limit"
        if provider == "gemini" and any(s in body for s in ("per day", "requests per day", "daily")):
            return "provider", _health_key("provider", provider), _seconds_until_next_pacific_day(), "gemini_daily_limit"
        if retry_seconds is not None:
            return "model", _health_key("model", provider, model_id), min(max(retry_seconds, 1), 3600), "rate_limit"
        return "model", _health_key("model", provider, model_id), 120, "rate_limit"

    if provider == "huggingface" and ("monthly credit" in body or "credits exhausted" in body):
        return "provider", _health_key("provider", provider), _seconds_until_next_utc_month(), "hf_monthly_credits"

    if status == 402 or any(s in body for s in ("insufficient credit", "negative credit", "billing")):
        return "provider", _health_key("provider", provider), 3600, "credits_or_billing"

    if status in (401, 403):
        if provider == "groq" or any(s in body for s in ("error code: 1010", "invalid api key", "unauthorized")):
            return "provider", _health_key("provider", provider), 3600, "provider_access_denied"
        return "model", _health_key("model", provider, model_id), 3600, "model_access_denied"

    if status == 400 and provider == "huggingface":
        return "model", _health_key("model", provider, model_id), 24 * 3600, "model_unavailable"

    if status == 404 or "no endpoints" in body or "model not found" in body:
        return "model", _health_key("model", provider, model_id), 24 * 3600, "model_unavailable"

    if status and status >= 500:
        return "model", _health_key("model", provider, model_id), 180, "provider_error"

    if code in ("timeout", "unknown") or str(code).startswith("html_response"):
        return "model", _health_key("model", provider, model_id), 90, str(code or "model_error")

    return "model", _health_key("model", provider, model_id), 60, str(code or "model_error")


def _record_model_failure(model, err):
    """Apply the computed cooldown to the shared health map."""
    if not model or not err:
        return
    scope, key, seconds, reason = _cooldown_target(model, err)
    with MODEL_HEALTH_LOCK:
        MODEL_HEALTH[key] = {
            "scope": scope,
            "provider": model.get("provider", "groq"),
            "model_id": model.get("id"),
            "model_label": model.get("label"),
            "reason": reason,
            "disabled_until": time.time() + max(1, seconds),
            "cooldown_seconds": int(seconds),
        }
    log.warning("model cooldown: %s scope=%s reason=%s seconds=%s",
                model.get("label"), scope, reason, int(seconds))


# ════════════════════════════════════════════════════════════════════════════
# 5. ROUTING — pick the right model for the request
# ════════════════════════════════════════════════════════════════════════════

def _pick(candidates):
    """Weighted random choice among *available* candidates, biased toward the
    newest models (registry is newest-first, so a linear decay favors index 0).
    Randomness also spreads load across equivalent models so no single free
    model burns its quota first."""
    available = [m for m in candidates if _model_available(m)]
    if not available:
        return None
    n = len(available)
    weights = [n - i for i in range(n)]  # newest gets weight n, oldest gets 1
    return random.choices(available, weights=weights, k=1)[0]


def _route(message, attachments):
    """Return (model, tier, reason). Order matters: capability needs (vision,
    code, files) win over length heuristics. Falls back down the tiers so a
    request is never dropped just because the ideal tier is on cooldown."""
    has_images = any(a.get("kind") == "image" for a in attachments)
    has_files = any(a.get("kind") in ("text", "file", "doc") for a in attachments)
    msg_len = len(message)

    if has_images:
        # Vision is a hard capability gate — only Gemini models qualify.
        vision_pool = [MODEL_BY_ID.get(i) for i in ("gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash") if MODEL_BY_ID.get(i)]
        best = _pick(vision_pool)
        if best:
            return best, "H", "vision"

    if _CODE.search(message):
        best = _pick(CODING_MODELS) or _pick(TIER_H)
        if best:
            return best, "H", "coding"

    if has_files:
        best = _pick(TIER_H) or _pick(TIER_M)
        if best:
            return best, "H", "file_analysis"

    if msg_len < 80 and _SIMPLE.match(message.strip()):
        best = _pick(TIER_S) or _pick(TIER_M)
        if best:
            return best, "S", "simple"

    if msg_len > 500 or _COMPLEX.search(message):
        best = _pick(TIER_H) or _pick(TIER_M)
        if best:
            return best, "H", "complex"

    if msg_len > 30 and _SEARCH.search(message):
        best = _pick(SEARCH_MODELS) or _pick(TIER_H)
        if best:
            return best, "H", "search"

    best = _pick(TIER_M) or _pick(TIER_H) or _pick(TIER_S)
    if best:
        return best, "M", "general"
    # Last resort: nothing is healthy — hand back any model so the caller can
    # try and surface a real error rather than crashing on an empty pool.
    return next((m for m in CHAT_MODELS if _model_available(m)), CHAT_MODELS[0]), "M", "fallback"


def _get_fallback_chain(current, tier):
    """Ordered list of models to try after `current` fails. Cross-provider
    candidates come FIRST — if Groq is rate-limiting us, retrying another Groq
    model just fails again, so we jump to OpenRouter/Gemini/etc. before
    exhausting same-provider options."""
    tried = {current["id"]}
    chain = []
    pool = TIER_S + TIER_M + TIER_H if tier == "S" else TIER_M + TIER_H + TIER_S
    for m in pool:  # pass 1: different provider only
        if m["id"] not in tried and m.get("provider") != current.get("provider") and _model_available(m):
            chain.append(m)
            tried.add(m["id"])
    for m in pool:  # pass 2: anything else still healthy
        if m["id"] not in tried and _model_available(m):
            chain.append(m)
            tried.add(m["id"])
    return chain


def _max_tokens(tier):
    return {"S": 512, "M": 1024}.get(tier, 2048)


# ════════════════════════════════════════════════════════════════════════════
# 6. ATTACHMENT HANDLING — normalize multimodal input into a provider payload
# ════════════════════════════════════════════════════════════════════════════
# Plumbing: clamp/whitelist client uploads, then assemble the user turn as a
# plain string (text-only) or an OpenAI-style parts list when an image goes to a
# vision model. An image routed to a text-only model degrades to a note, not a
# failed call.

MAX_ATTACHMENTS = 4
MAX_TEXT_CHARS = 12000


def _clean(val, limit):
    return val.strip()[:limit] if isinstance(val, str) else ""


def _sanitize_attachments(items):
    """Defensive: clamp count, whitelist kinds, truncate oversized text."""
    out = []
    for item in (items if isinstance(items, list) else [])[:MAX_ATTACHMENTS]:
        if not isinstance(item, dict):
            continue
        kind, name = _clean(item.get("kind"), 20), _clean(item.get("name"), 200)
        if kind == "image" and str(item.get("data_url", "")).startswith("data:image/"):
            out.append({"kind": "image", "name": name, "data_url": item["data_url"]})
        elif kind == "text":
            out.append({"kind": "text", "name": name, "text": _clean(item.get("text"), MAX_TEXT_CHARS)})
        else:
            out.append({"kind": "file", "name": name})
    return out


def _build_content(message, attachments, model_id):
    """Assemble the user turn → (content, warning). Plain string for text-only
    turns; an OpenAI parts list when a vision model gets an image. Non-vision
    models get a text note about the image instead of a failure."""
    parts, warning, notes = [], "", []
    if message.strip():
        parts.append({"type": "text", "text": message.strip()})
    for a in attachments:
        kind, name = a.get("kind"), a.get("name", "unnamed")
        if kind == "image" and model_id in VISION_MODEL_IDS:
            parts.append({"type": "image_url", "image_url": {"url": a.get("data_url")}})
        elif kind == "image":
            warning = "Image attached but model is text-only."
            notes.append(f"image: {name}")
        elif kind == "text" and a.get("text"):
            parts.append({"type": "text", "text": f"[Attached: {name}]\n{a['text']}"})
        else:
            notes.append(f"file: {name}")
    if notes:
        parts.append({"type": "text", "text": "Attached:\n- " + "\n- ".join(notes)})
    if not parts:
        parts.append({"type": "text", "text": "Please help with my attached files."})
    if len(parts) == 1 and parts[0]["type"] == "text":
        return parts[0]["text"], warning
    return parts, warning


# ════════════════════════════════════════════════════════════════════════════
# 7. THE CALL LAYER — one function, every provider, structured errors
# ════════════════════════════════════════════════════════════════════════════
# `_call_model` returns (reply, err). On failure `err` is a structured dict
# (status / headers / body / code) — precisely the shape `_cooldown_target`
# needs to make a good benching decision. Never raises; the orchestrator above
# stays a clean loop.

def _content_to_gemini_parts(user_content):
    """Gemini uses {text} / {inline_data} parts instead of OpenAI's
    {type:text} / {type:image_url}, so multimodal content is re-shaped here."""
    parts = []
    if isinstance(user_content, str):
        return [{"text": user_content}]
    for item in user_content if isinstance(user_content, list) else []:
        if item.get("type") == "text":
            parts.append({"text": item["text"]})
        elif item.get("type") == "image_url":
            data_url = item.get("image_url", {}).get("url", "")
            if data_url.startswith("data:") and "," in data_url:
                header, b64 = data_url.split(",", 1)
                mime = header.split(":")[1].split(";")[0] if ":" in header else "image/png"
                parts.append({"inline_data": {"mime_type": mime, "data": b64}})
    return parts


def _call_gemini(model_id, system_prompt, user_content, api_key, history=None):
    url = f"{GEMINI_API_URL}/{model_id}:generateContent?key={api_key}"
    contents = []
    for h in (history or []):
        role = "model" if h.get("role") == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": h.get("content", "")}]})
    contents.append({"role": "user", "parts": _content_to_gemini_parts(user_content)})
    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": contents,
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 2048},
    }
    req = Request(url, data=json.dumps(payload).encode(),
                  headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())
    candidates = data.get("candidates", [])
    if not candidates:
        return ""
    return "\n".join(p.get("text", "") for p in candidates[0].get("content", {}).get("parts", []) if p.get("text")).strip()


def _extract_reply(data):
    """OpenAI-format responses put content either as a string or a parts list."""
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(i.get("text", "") for i in content if isinstance(i, dict) and i.get("text"))
    return ""


def _call_model(model, system_prompt, user_content, max_tok=1024, history=None):
    """Dispatch one completion. Returns (reply, None) on success or
    (None, err_dict) on any failure — including 'empty response' and HTML error
    pages some providers return with a 200."""
    provider = model.get("provider", "groq")
    api_key = _provider_api_key(provider)
    if not api_key:
        return None, f"missing_{provider}_key"
    try:
        if provider == "gemini":
            reply = _call_gemini(model["id"], system_prompt, user_content, api_key, history)
            if not reply or not reply.strip():
                return None, {"code": "empty_response", "provider": provider, "model": model.get("id")}
            return reply, None

        messages = [{"role": "system", "content": system_prompt}]
        for h in (history or []):
            messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
        messages.append({"role": "user", "content": user_content})
        payload = {"model": model["id"], "messages": messages, "temperature": 0.7, "max_tokens": max_tok}

        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        if provider == "openrouter":
            headers["HTTP-Referer"] = OPENROUTER_REFERER
            headers["X-Title"] = OPENROUTER_TITLE
        base_url = PROVIDER_API_URL.get(provider, GROQ_API_URL)

        req = Request(base_url, data=json.dumps(payload).encode(), headers=headers, method="POST")
        with urlopen(req, timeout=25) as resp:
            raw = resp.read().decode()
            if raw.lstrip().startswith("<"):  # provider returned an HTML error page
                return None, {"code": "html_response", "provider": provider, "model": model.get("id"), "body": raw[:300]}
            reply = _extract_reply(json.loads(raw))
            if not reply or not reply.strip():
                return None, {"code": "empty_response", "provider": provider, "model": model.get("id"), "body": raw[:300]}
            return reply, None
    except HTTPError as e:
        body = ""
        try:
            body = e.read().decode()[:1000]
        except Exception:
            pass
        return None, {
            "code": f"http_{e.code}", "status": e.code,
            "headers": dict(e.headers.items()) if e.headers else {},
            "body": body, "provider": provider, "model": model.get("id"),
        }
    except (URLError, TimeoutError) as e:
        return None, {"code": "timeout", "provider": provider, "model": model.get("id"), "body": str(e)[:300]}
    except Exception as e:
        return None, {"code": "unknown", "provider": provider, "model": model.get("id"), "body": str(e)[:300]}


# ════════════════════════════════════════════════════════════════════════════
# 8. DEGRADED MODE — answer trivial turns with no model at all
# ════════════════════════════════════════════════════════════════════════════

def _local_fallback_reply(message):
    """When the entire pool is down, still greet/thank in the user's language so
    a dead backend doesn't show as a hard error for a one-word message."""
    text = (message or "").strip().lower()
    is_ru = bool(re.search(r"[а-яё]", text, re.IGNORECASE))
    if re.fullmatch(r"(hi|hello|hey|привет|здравствуй|добрый\s+день)", text, re.IGNORECASE):
        return "Привет! Я на связи. Чем помочь?" if is_ru else "Hi! I'm online. How can I help?"
    if re.fullmatch(r"(thanks|thank you|спасибо)", text, re.IGNORECASE):
        return "Пожалуйста." if is_ru else "You're welcome."
    return ""


# ════════════════════════════════════════════════════════════════════════════
# 9. PUBLIC ENTRY POINT — route → call → fallback chain → degrade
# ════════════════════════════════════════════════════════════════════════════

DEFAULT_SYSTEM_PROMPT = (
    "You are the AI assistant on alexpavsky.com. Be concise, accurate, and "
    "helpful. Answer in the user's language."
)


def generate(message, attachments=None, history=None, system_prompt=DEFAULT_SYSTEM_PROMPT):
    """Turn a user message (+ optional attachments/history) into an answer,
    surviving the unreliability of a free-tier model pool.

    Returns a dict:
        {"reply": str, "model": str, "tier": str, "reason": str,
         "warning": str|None, "degraded": bool}
    or {"error": str, "reply": <user-facing message>} if everything is down.

    This mirrors the request path in chat_server.py with the HTTP/session/DB
    plumbing stripped away, so the orchestration is visible in one place.
    """
    message = _clean(message, MAX_TEXT_CHARS)
    attachments = _sanitize_attachments(attachments or [])
    history = (history if isinstance(history, list) else [])[-20:]

    # 1. Route to the best-fit healthy model.
    model, tier, reason = _route(message, attachments)
    max_tok = _max_tokens(tier)
    user_content, warning = _build_content(message, attachments, model["id"])
    log.info("route: %s tier=%s reason=%s", model["label"], tier, reason)

    # 2. Primary attempt.
    reply, err = _call_model(model, system_prompt, user_content, max_tok, history)
    if not reply or not reply.strip():
        _record_model_failure(model, err)

        # 3. Walk the cross-provider fallback chain.
        chain = _get_fallback_chain(model, tier)
        if reason == "vision":  # an image turn can only go to another vision model
            chain = [m for m in chain if m.get("id") in VISION_MODEL_IDS]
        for fallback in chain:
            if not _model_available(fallback):
                continue
            log.info("fallback: %s -> %s (%s)", model["label"], fallback["label"], fallback["provider"])
            fb_content, _ = _build_content(message, attachments, fallback["id"])
            reply, err = _call_model(fallback, system_prompt, fb_content, max_tok, history)
            if reply and reply.strip():
                model = fallback
                break
            _record_model_failure(fallback, err)
            time.sleep(0.5)

    # 4. Whole pool down → degrade locally or surface a clean error.
    if not reply or not reply.strip():
        local = _local_fallback_reply(message)
        if local:
            return {"reply": local, "model": "local_fallback", "tier": tier,
                    "reason": reason, "warning": warning, "degraded": True}
        return {"error": (err.get("code") if isinstance(err, dict) else err) or "no_response",
                "reply": "Sorry, all AI models are temporarily unavailable. Please try again in a moment."}

    return {"reply": reply.strip(), "model": model["label"], "tier": tier,
            "reason": reason, "warning": warning or None, "degraded": False}


if __name__ == "__main__":
    # Tiny manual smoke harness. With provider keys in the environment this
    # makes a real call; without them it shows the routing decision and a clean
    # "missing key" path. Either way it exercises route → call → fallback.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for probe in ("hi", "Write a Python function to reverse a linked list",
                  "What's the latest news on LLM evaluation?"):
        decided, decided_tier, decided_reason = _route(probe, [])
        print(f"\n> {probe!r}\n  routed to: {decided['label']} (tier={decided_tier}, reason={decided_reason})")
        print("  result:", json.dumps(generate(probe), ensure_ascii=False)[:300])
