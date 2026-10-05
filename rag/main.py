"""
rag/main.py — RAG + Eval API for alexpavsky.com
FastAPI service on port 8001.

Endpoints:
  POST /api/rag/upload       — ingest PDF, embed chunks → pgvector + Qdrant
  POST /api/rag/query        — semantic search + LLM answer + Ragas metrics
  POST /api/eval/run         — run a single LLM eval (exact_match|contains|llm_judge)
  GET  /api/eval/results     — list recent eval_runs
  GET  /api/rag/metrics      — last 50 rag_queries with metrics
  GET  /api/health           — liveness + dependency check
"""
import io
import json
import logging
import os
import re
import textwrap
import threading
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

try:
    from .safety import safety_response
except ImportError:  # Docker starts uvicorn from inside /app.
    from safety import safety_response

try:
    from .redaction import redact_local_paths
except ImportError:  # Docker starts uvicorn from inside /app.
    from redaction import redact_local_paths

try:
    import PyPDF2
except ImportError:
    PyPDF2 = None  # type: ignore

import tiktoken

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DATABASE_URL = os.environ["DATABASE_URL"]
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
HF_TOKEN = os.environ.get("HF_TOKEN", "")

# Embeddings: local sentence-transformers (no API key needed)
# Generation: OpenRouter (already configured with key rotation)
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_DIM = 384
LLM_MODEL = "meta-llama/llama-3.3-70b-instruct:free"  # via OpenRouter free tier
QDRANT_COLLECTION = "documents"
CHUNK_TOKENS = 500
OVERLAP_TOKENS = 50
RAG_TOP_K = int(os.environ.get("RAG_TOP_K", "10"))
DEFAULT_CORS_ORIGINS = (
    "https://alexpavsky.com",
    "https://www.alexpavsky.com",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("rag-api")

# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------
# Local sentence-transformers model (loaded once at startup). The model is
# pre-seeded into a persistent cache by deployment; never contact Hugging Face
# on the request path, where an expired token used to break every RAG query.
_st_model = None
_st_model_lock = threading.Lock()


def get_st_model():
    global _st_model
    if _st_model is not None:
        return _st_model
    with _st_model_lock:  # endpoints run in a threadpool; load the model once
        if _st_model is not None:
            return _st_model
        from sentence_transformers import SentenceTransformer
        log.info("Loading sentence-transformers model (local cache first)...")
        try:
            _st_model = SentenceTransformer("all-MiniLM-L6-v2", local_files_only=True)
        except Exception:
            # Fresh container without the cached model: the embedding model is
            # public, so download anonymously — an expired HF_TOKEN (used only by
            # the optional generation provider) must not break retrieval startup.
            _st_model = SentenceTransformer("all-MiniLM-L6-v2", token=False)
        log.info("Model loaded.")
    return _st_model

qdrant_client = QdrantClient(url=QDRANT_URL)

# Ensure Qdrant collection exists
try:
    qdrant_client.get_collection(QDRANT_COLLECTION)
    log.info("Qdrant collection '%s' already exists", QDRANT_COLLECTION)
except Exception:
    qdrant_client.create_collection(
        collection_name=QDRANT_COLLECTION,
        vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
    )
    log.info("Created Qdrant collection '%s'", QDRANT_COLLECTION)


def get_pg():
    """Return a psycopg2 connection (caller must close)."""
    return psycopg2.connect(DATABASE_URL)


def cors_origins() -> list[str]:
    raw = os.environ.get("RAG_CORS_ORIGINS", "")
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
    return origins or list(DEFAULT_CORS_ORIGINS)


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def embed(texts: list[str]) -> list[list[float]]:
    """Batch embed texts — local sentence-transformers (no API key needed)."""
    if not texts:
        return []
    # Local sentence-transformers — free, no rate limits
    model = get_st_model()
    vecs = model.encode(texts, normalize_embeddings=True)
    return [v.tolist() for v in vecs]


def embed_one(text: str) -> list[float]:
    return embed([text])[0]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = sum(x * x for x in a) ** 0.5
    mag_b = sum(x * x for x in b) ** 0.5
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def chunk_text(text: str) -> list[str]:
    """Split text into chunks of ~CHUNK_TOKENS with OVERLAP_TOKENS overlap."""
    enc = tiktoken.get_encoding("cl100k_base")
    tokens = enc.encode(text)
    chunks: list[str] = []
    start = 0
    while start < len(tokens):
        end = min(start + CHUNK_TOKENS, len(tokens))
        chunk_tokens = tokens[start:end]
        chunks.append(enc.decode(chunk_tokens))
        if end == len(tokens):
            break
        start += CHUNK_TOKENS - OVERLAP_TOKENS
    return chunks


def extract_pdf_text(data: bytes) -> str:
    if PyPDF2 is None:
        raise HTTPException(500, "PyPDF2 not installed")
    reader = PyPDF2.PdfReader(io.BytesIO(data))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages)


def extract_docx_text(data: bytes) -> str:
    """Extract plain text from a .docx file using built-in zipfile + ElementTree."""
    import zipfile
    import xml.etree.ElementTree as ET
    buf = io.BytesIO(data)
    try:
        with zipfile.ZipFile(buf) as z:
            if "word/document.xml" not in z.namelist():
                raise HTTPException(400, "Invalid .docx file")
            xml_bytes = z.read("word/document.xml")
        root = ET.fromstring(xml_bytes)
        ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        paragraphs = []
        for para in root.iter(f"{{{ns}}}p"):
            texts = [t.text for t in para.iter(f"{{{ns}}}t") if t.text]
            if texts:
                paragraphs.append("".join(texts))
        return "\n".join(paragraphs)
    except zipfile.BadZipFile:
        raise HTTPException(400, "Cannot read .docx — bad zip")


def extract_text(data: bytes, filename: str) -> str:
    """Route to correct extractor by file extension."""
    name = filename.lower()
    if name.endswith(".pdf"):
        return extract_pdf_text(data)
    if name.endswith(".docx"):
        return extract_docx_text(data)
    if name.endswith(".txt") or name.endswith(".md"):
        return data.decode("utf-8", errors="replace")
    raise HTTPException(400, f"Unsupported file type: {filename}")


# Every enabled provider is on the account's free/evaluation plan. OpenRouter is
# stricter still: only the free router or model ids ending in :free may execute.
_FREE_PROVIDER_CONFIG = {
    "groq": ("GROQ_API_KEY", "https://api.groq.com/openai/v1"),
    "nvidia": ("NVIDIA_API_KEY", "https://integrate.api.nvidia.com/v1"),
    "openrouter": ("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1"),
    "mistral": ("MISTRAL_API_KEY", "https://api.mistral.ai/v1"),
    "cohere": ("COHERE_API_KEY", "https://api.cohere.ai/compatibility/v1"),
    "huggingface": ("HF_INFERENCE_TOKEN", "https://router.huggingface.co/v1"),
    "local": ("", os.environ.get("BOSGAME_OLLAMA_URL", "http://172.18.0.1:11434").rstrip("/") + "/v1"),
}


def _cloudflare_base_url() -> str:
    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    if not account_id:
        return ""
    return f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1"


def _provider_config(provider: str):
    if provider == "cloudflare":
        base_url = _cloudflare_base_url()
        return ("CLOUDFLARE_API_TOKEN", base_url) if base_url else None
    return _FREE_PROVIDER_CONFIG.get(provider)


# Known-good routes make cold starts fast. The curator registry below adds every
# other recently verified free model and is mounted read-only into the container.
_STATIC_LLM_PROVIDERS = [
    ("groq", "GROQ_API_KEY", "https://api.groq.com/openai/v1", "openai/gpt-oss-120b", "free"),
    ("nvidia", "NVIDIA_API_KEY", "https://integrate.api.nvidia.com/v1", "meta/llama-3.2-11b-vision-instruct", "free"),
    ("cloudflare", "CLOUDFLARE_API_TOKEN", _cloudflare_base_url(), "@cf/ibm-granite/granite-4.0-h-micro", "free"),
    ("cohere", "COHERE_API_KEY", "https://api.cohere.ai/compatibility/v1", "command-r7b-12-2024", "free"),
    ("huggingface", "HF_INFERENCE_TOKEN", "https://router.huggingface.co/v1", "meta-llama/Llama-3.1-8B-Instruct", "free"),
    ("mistral", "MISTRAL_API_KEY", "https://api.mistral.ai/v1", "codestral-2508", "free"),
    ("openrouter", "OPENROUTER_API_KEY", "https://openrouter.ai/api/v1", "openrouter/free", "free"),
    ("local", "", os.environ.get("BOSGAME_OLLAMA_URL", "http://172.18.0.1:11434").rstrip("/") + "/v1", "gpt-oss:120b", "free"),
]


def _load_curated_llm_providers():
    configured = os.environ.get("LLM_REGISTRY_FILE", "/curator-data/models_registry.json")
    paths = (configured, "/app/models_registry.json")
    models = None
    for path in dict.fromkeys(paths):
        try:
            with open(path, encoding="utf-8") as handle:
                models = json.load(handle).get("models", [])
            break
        except (OSError, ValueError, AttributeError):
            continue
    if models is None:
        return []

    rows = []
    for model in models:
        provider = str(model.get("provider", ""))
        model_id = str(model.get("id", ""))
        config = _provider_config(provider)
        if not config or not model_id or not model.get("free"):
            continue
        if provider == "openrouter" and not model_id.endswith(":free"):
            continue
        key_env, base_url = config
        if base_url:
            rows.append((provider, key_env, base_url, model_id, "free"))
    return rows


def _build_llm_provider_pool():
    rows = []
    seen = set()
    for row in _STATIC_LLM_PROVIDERS + _load_curated_llm_providers():
        provider, _key_env, base_url, model, _tier = row
        key = (provider, model)
        if base_url and key not in seen:
            rows.append(row)
            seen.add(key)
    # Local inference is deliberately the final reserve after every cloud pool.
    rows.sort(key=lambda row: row[0] == "local")
    return rows


_LLM_PROVIDERS = _build_llm_provider_pool()
log.info(
    "llm_free_pool models=%d providers=%s",
    len(_LLM_PROVIDERS),
    ",".join(sorted({row[0] for row in _LLM_PROVIDERS})),
)

# Cooldown keyed by rate-limit bucket id (not raw key alone).
_PROVIDER_COOLDOWN = {}  # bucket_id -> unix ts until usable
_MODEL_COOLDOWN = {}  # (provider, model) -> unix ts until usable
_COOLDOWN_SECONDS = float(os.environ.get("LLM_COOLDOWN_SECONDS", "60"))
_ATTEMPT_TIMEOUT = float(os.environ.get("LLM_ATTEMPT_TIMEOUT", "6"))
_LOCAL_ATTEMPT_TIMEOUT = float(os.environ.get("LOCAL_LLM_TIMEOUT", "75"))


def _normalize_tier(tier) -> str:
    """Accept legacy bool free flags and new tier strings."""
    if tier is True:
        return "free"
    if tier is False:
        return "blocked"
    t = str(tier or "blocked").strip().lower()
    if t in ("free", "credits", "blocked", "paid"):
        return "credits" if t == "paid" else t
    if str(tier).endswith(":free"):
        return "free"
    return "blocked"


def _bucket_id(key_env: str, tier: str) -> str:
    """OpenRouter free vs credits are separate rate-limit pools on one key."""
    tier = _normalize_tier(tier)
    if key_env == "OPENROUTER_API_KEY":
        return f"{key_env}:{tier}"
    return key_env


def _is_allowed(provider: str, model: str, tier) -> bool:
    tier = _normalize_tier(tier)
    if provider == "openrouter":
        return model == "openrouter/free" or str(model).endswith(":free")
    return tier == "free" and provider in {*_FREE_PROVIDER_CONFIG, "cloudflare"}


def _cool_bucket(bucket_id: str, seconds: float = None) -> None:
    until = time.time() + (seconds if seconds is not None else _COOLDOWN_SECONDS)
    prev = _PROVIDER_COOLDOWN.get(bucket_id, 0.0)
    if until > prev:
        _PROVIDER_COOLDOWN[bucket_id] = until
        log.info("llm_bucket_cooldown bucket=%s for=%.0fs", bucket_id, until - time.time())


def _cool_model(provider: str, model: str, seconds: float = None) -> None:
    until = time.time() + (seconds if seconds is not None else _COOLDOWN_SECONDS)
    key = (provider, model)
    if until > _MODEL_COOLDOWN.get(key, 0.0):
        _MODEL_COOLDOWN[key] = until


def _provider_api_key(key_env: str) -> str:
    value = os.environ.get(key_env, "").strip()
    if value or key_env != "HF_INFERENCE_TOKEN":
        return value
    try:
        with open("/app/.hf-inference-token", encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def _response_content(response: httpx.Response) -> str:
    try:
        content = response.json()["choices"][0]["message"].get("content", "")
    except (ValueError, KeyError, IndexError, TypeError):
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text", "")).strip()
            for item in content
            if isinstance(item, dict) and item.get("text")
        ).strip()
    return ""


def call_llm(prompt: str, system: str = "", max_tokens: int = 1024, timeout: float = 15.0) -> str:
    """Call the curated free-only pool with bounded failover latency."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    last_error = None
    deadline = time.monotonic() + max(1.0, float(timeout))
    for provider, key_env, base_url, model, tier in _LLM_PROVIDERS:
        tier_n = _normalize_tier(tier)
        if not _is_allowed(provider, model, tier_n):
            continue
        now = time.time()
        bid = _bucket_id(key_env, tier_n)
        if _PROVIDER_COOLDOWN.get(bid, 0.0) > now:
            continue
        if _MODEL_COOLDOWN.get((provider, model), 0.0) > now:
            continue
        key = _provider_api_key(key_env)
        if not key and provider != "local":
            continue
        remaining = deadline - time.monotonic()
        if remaining <= 0.25 and provider != "local":
            last_error = "cloud request deadline reached"
            continue
        try:
            request_max_tokens = int(max_tokens)
            if "gpt-oss" in model:
                request_max_tokens = max(request_max_tokens, 256)
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "AlexPavsky-Free-Orchestrator/1.0",
                "HTTP-Referer": "https://alexpavsky.com",
                "X-Title": "AlexPavsky Voice/RAG",
            }
            if key:
                headers["Authorization"] = f"Bearer {key}"
            r = httpx.post(
                f"{base_url}/chat/completions",
                json={"model": model, "messages": messages, "max_tokens": request_max_tokens},
                headers=headers,
                timeout=_LOCAL_ATTEMPT_TIMEOUT if provider == "local" else min(_ATTEMPT_TIMEOUT, remaining),
            )
            content = _response_content(r) if r.status_code == 200 else ""
            if content:
                log.info("llm_ok provider=%s model=%s tier=%s", provider, model, tier_n)
                return content

            last_error = f"{provider}/{model} HTTP {r.status_code}" if r.status_code != 200 else f"{provider}/{model} empty response"
            if r.status_code in (401, 402, 403, 429):
                retry_after = r.headers.get("retry-after", "")
                try:
                    cooldown = min(max(float(retry_after), 60.0), 3600.0)
                except (TypeError, ValueError):
                    cooldown = 3600.0 if r.status_code in (401, 402, 403) else 120.0
                _cool_bucket(bid, cooldown)
            elif r.status_code in (404, 410):
                _cool_model(provider, model, 24 * 3600)
            elif r.status_code >= 500:
                _cool_model(provider, model, 90)
            else:
                _cool_model(provider, model, 120)
            log.warning("%s, trying next free model", last_error)
        except Exception as exc:
            _cool_model(provider, model, 60)
            last_error = f"{provider}/{model} {type(exc).__name__}"
            log.warning("%s, trying next free model", last_error)

    return f"LLM unavailable — all providers failed. Last error: {last_error or 'no keys set'}"


# ---------------------------------------------------------------------------
# Metrics helpers (Ragas-inspired, lightweight)
# ---------------------------------------------------------------------------

def compute_faithfulness(answer: str, context_chunks: list[str]) -> float:
    """
    Ask LLM to judge how many claims in the answer are supported by context.
    Returns 0-1 float. Falls back to 0.5 on error.
    """
    if not OPENROUTER_API_KEY:
        return 0.5
    context = "\n---\n".join(context_chunks[:3])
    prompt = textwrap.dedent(f"""
        Context:
        {context}

        Answer:
        {answer}

        Score how faithful the answer is to the context above.
        Return ONLY a decimal number between 0.0 and 1.0. No explanation.
    """).strip()
    try:
        raw = call_llm(prompt)
        return max(0.0, min(1.0, float(raw.strip())))
    except Exception:
        return 0.5


def compute_answer_relevancy(query: str, answer: str) -> float:
    """Cosine similarity between query embedding and answer embedding."""
    try:
        vecs = embed([query, answer])
        return float(cosine_similarity(vecs[0], vecs[1]))
    except Exception:
        return 0.5


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="alexpavsky RAG API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# POST /api/rag/upload
# ---------------------------------------------------------------------------
@app.post("/api/rag/upload")
async def upload_document(
    file: UploadFile = File(...),
    user_id: Optional[str] = Form(None),
):
    """
    Ingest a PDF: extract text → chunk → embed → store in pgvector + Qdrant.
    Returns: {document_id, filename, chunk_count}
    """
    allowed = (".pdf", ".docx", ".txt", ".md")
    if not any(file.filename.lower().endswith(ext) for ext in allowed):
        raise HTTPException(400, f"Supported formats: {', '.join(allowed)}")

    raw = await file.read()
    text = extract_text(raw, file.filename or "document")
    if not text.strip():
        raise HTTPException(422, "Could not extract text from file")

    chunks = chunk_text(text)
    if not chunks:
        raise HTTPException(422, "No chunks generated from document")

    log.info("Uploading '%s': %d chunks", file.filename, len(chunks))

    # Embed all chunks
    embeddings = embed(chunks)

    document_id = str(uuid.uuid4())
    uid = user_id or None

    conn = get_pg()
    try:
        with conn:
            with conn.cursor() as cur:
                # Insert document record
                cur.execute(
                    """
                    INSERT INTO documents (id, user_id, filename, content, chunk_count)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (document_id, uid, file.filename, text[:10000], len(chunks)),
                )

                # Insert chunks into PostgreSQL (pgvector)
                chunk_rows = [
                    (
                        str(uuid.uuid4()),
                        document_id,
                        i,
                        chunks[i],
                        embeddings[i],
                    )
                    for i in range(len(chunks))
                ]
                psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO document_chunks (id, document_id, chunk_index, content, embedding)
                    VALUES %s
                    """,
                    chunk_rows,
                    template="(%s, %s, %s, %s, %s::vector)",
                )
    finally:
        conn.close()

    # Upsert into Qdrant
    points = [
        PointStruct(
            id=str(uuid.uuid4()),
            vector=embeddings[i],
            payload={
                "document_id": document_id,
                "chunk_index": i,
                "content": chunks[i],
                "filename": file.filename,
            },
        )
        for i in range(len(chunks))
    ]
    qdrant_client.upsert(collection_name=QDRANT_COLLECTION, points=points)

    log.info("Stored document %s (%d chunks)", document_id, len(chunks))
    return {"document_id": document_id, "filename": file.filename, "chunk_count": len(chunks)}


# ---------------------------------------------------------------------------
# POST /api/rag/query
# ---------------------------------------------------------------------------
class QueryRequest(BaseModel):
    query: str
    document_id: Optional[str] = None
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    # Opt-in tuning for low-latency callers (e.g. the voice agent). Both default
    # to the historic behavior, so the website chat is unaffected.
    #   skip_metrics: don't run the two extra Ragas LLM calls (faithfulness +
    #     answer_relevancy) — removes ~2/3 of the per-query LLM latency.
    #   system: override the answer system prompt (e.g. a brief spoken-voice persona).
    skip_metrics: bool = False
    system: Optional[str] = None
    # Low-latency callers (voice) may cap how many context chunks reach the LLM.
    max_context_chunks: Optional[int] = None
    # Cap generation length (voice wants short spoken answers).
    max_tokens: Optional[int] = None
    # Agent runtime: return retrieved context + sources without generating, so
    # the chat-side RAG Agent writes the answer from the shared free-model pool.
    retrieve_only: bool = False


# Knowledge-base notes quote local machine paths (/Users/<name>/Projects/...).
# Strip the machine part before text reaches an LLM, the public site or the
# voice agent: keep the repo-relative path (the repos are public), drop the
# user name and home layout.
_LOCAL_PATH_PREFIX_RE = re.compile(
    r"(?:/private)?/(?:Users|home)/[^/\s`'\"]+/(?:Projects|LocalApps|Desktop|Documents|Downloads|llm-wiki)/"
)
_LOCAL_HOME_RE = re.compile(r"(?:/private)?/(?:Users|home)/[^/\s`'\"]+")


def _scrub_local_paths(text):
    if not isinstance(text, str) or not text:
        return text
    return _LOCAL_HOME_RE.sub("~", _LOCAL_PATH_PREFIX_RE.sub("", text))


# Endpoints below are plain `def` on purpose: they call blocking code (psycopg2,
# Qdrant, sentence-transformers, LLM HTTP). As `async def` they ran on the single
# event loop, so one slow generation froze every request — including the
# millisecond retrieve_only calls the chat RAG Agent and the voice agent depend on.
# FastAPI runs `def` endpoints in a threadpool. Upload stays async (awaits the file).
@app.post("/api/rag/query")
def rag_query(req: QueryRequest):
    """
    Semantic search over stored chunks, then generate an answer via LLM.
    Returns answer, sources, Ragas metrics, and both pgvector / Qdrant results.
    """
    if not req.query.strip():
        raise HTTPException(400, "query must not be empty")

    blocked_answer = safety_response(req.query)
    if blocked_answer:
        return {
            "answer": blocked_answer,
            "sources": [],
            "contexts": [],
            "metrics": {"faithfulness": 0.0, "answer_relevancy": 1.0},
            "pgvector_results": [],
            "qdrant_results": [],
            "safety_blocked": True,
        }

    query_vec = embed_one(req.query)

    # --- pgvector search ---
    conn = get_pg()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            filter_clause = ""
            if req.document_id:
                filter_clause = "WHERE dc.document_id = %s"

            cur.execute(
                f"""
                SELECT dc.content,
                       dc.chunk_index,
                       dc.document_id,
                       d.filename,
                       1 - (dc.embedding <=> %s::vector) AS score
                FROM document_chunks dc
                JOIN documents d ON d.id = dc.document_id
                {filter_clause}
                ORDER BY dc.embedding <=> %s::vector
                LIMIT %s
                """,
                [query_vec] + ([req.document_id] if req.document_id else []) + [query_vec, RAG_TOP_K],
            )
            pg_results = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

    # --- Qdrant search ---
    qdrant_filter = None
    if req.document_id:
        from qdrant_client.models import FieldCondition, Filter, MatchValue
        qdrant_filter = Filter(
            must=[FieldCondition(key="document_id", match=MatchValue(value=req.document_id))]
        )

    q_hits = qdrant_client.search(
        collection_name=QDRANT_COLLECTION,
        query_vector=query_vec,
        limit=RAG_TOP_K,
        query_filter=qdrant_filter,
    )
    qdrant_results = [
        {
            "content": h.payload.get("content", ""),
            "score": h.score,
            "chunk_index": h.payload.get("chunk_index"),
            "filename": h.payload.get("filename"),
        }
        for h in q_hits
    ]

    result_limit = max(1, min(req.max_context_chunks or RAG_TOP_K, RAG_TOP_K))
    pg_results = pg_results[:result_limit]
    qdrant_results = qdrant_results[:result_limit]

    # Build context from pgvector results (primary), fall back to qdrant
    context_chunks = [r["content"] for r in pg_results] or [r["content"] for r in qdrant_results]
    context_chunks = [_scrub_local_paths(c) for c in context_chunks]

    if not context_chunks:
        raise HTTPException(404, "No relevant documents found. Upload documents first.")

    # Low-latency callers (voice) can cap the context chunks fed to the LLM:
    # fewer chunks keep the prompt under the fast 8B model's payload limit
    # (avoids its 413 fallthrough) and speed generation. Default keeps all.
    if req.max_context_chunks and req.max_context_chunks > 0:
        context_chunks = context_chunks[: req.max_context_chunks]

    source_rows = pg_results or qdrant_results
    sources = [
        {
            "content": _scrub_local_paths(r["content"])[:300],
            "score": float(r["score"]),
            "filename": r.get("filename"),
        }
        for r in source_rows
    ]

    if req.retrieve_only:
        return {
            "answer": "",
            "sources": sources,
            "contexts": context_chunks,
            "metrics": {},
            "retrieve_only": True,
        }

    context_text = "\n\n---\n\n".join(context_chunks)

    # --- LLM answer ---
    system_prompt = (req.system or "").strip() or (
        "You are a helpful assistant. Answer the user's question using ONLY the provided context. "
        "If the context does not contain enough information, say so clearly."
    )
    user_prompt = f"Context:\n{context_text}\n\nQuestion: {req.query}"
    # Voice / low-latency: short answers + tighter provider timeout.
    gen_max_tokens = int(req.max_tokens) if req.max_tokens and req.max_tokens > 0 else 1024
    gen_timeout = 12.0 if req.skip_metrics else 20.0
    if req.skip_metrics and not req.max_tokens:
        gen_max_tokens = 180  # ~2 short spoken sentences
    answer = call_llm(
        user_prompt,
        system=system_prompt,
        max_tokens=gen_max_tokens,
        timeout=gen_timeout,
    )

    # --- Metrics ---
    # Low-latency callers (voice) skip the two extra Ragas LLM calls; the columns
    # are nullable, so we simply persist NULL and return null metrics.
    if req.skip_metrics:
        faithfulness = None
        answer_relevancy = None
    else:
        faithfulness = compute_faithfulness(answer, context_chunks)
        answer_relevancy = compute_answer_relevancy(req.query, answer)

    # --- Persist to DB ---
    conn = get_pg()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO rag_queries
                        (id, user_id, session_id, query, answer, model,
                         faithfulness, answer_relevancy)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(uuid.uuid4()),
                        req.user_id,
                        req.session_id,
                        req.query,
                        answer,
                        LLM_MODEL,
                        faithfulness,
                        answer_relevancy,
                    ),
                )
    finally:
        conn.close()

    return {
        "answer": _scrub_local_paths(answer),
        "sources": sources,
        # Full retrieved chunk text — what the LLM actually saw. Consumers that
        # need to verify groundedness (Ragas faithfulness, manual audit) should
        # read this instead of `sources[].content`, which is a 300-char preview.
        "contexts": context_chunks,
        "metrics": {
            "faithfulness": round(faithfulness, 4) if faithfulness is not None else None,
            "answer_relevancy": round(answer_relevancy, 4) if answer_relevancy is not None else None,
        },
        "pgvector_results": [{"content": r["content"][:200], "score": float(r["score"])} for r in pg_results],
        "qdrant_results": [{"content": r["content"][:200], "score": round(r["score"], 4)} for r in qdrant_results],
    }


# ---------------------------------------------------------------------------
# POST /api/eval/run
# ---------------------------------------------------------------------------
class EvalRequest(BaseModel):
    prompt: str
    expected: Optional[str] = None
    model: str = LLM_MODEL
    metric: str = "llm_judge"   # exact_match | contains | llm_judge
    test_name: Optional[str] = None
    user_id: Optional[str] = None


@app.post("/api/eval/run")
def eval_run(req: EvalRequest):
    """
    Run a single LLM evaluation.
    Metrics: exact_match, contains, llm_judge (LLM scores 0-1).
    """
    if req.metric not in ("exact_match", "contains", "llm_judge"):
        raise HTTPException(400, "metric must be one of: exact_match, contains, llm_judge")

    actual = call_llm(req.prompt)

    score: float
    passed: bool

    if req.metric == "exact_match":
        score = 1.0 if actual.strip() == (req.expected or "").strip() else 0.0
        passed = score == 1.0

    elif req.metric == "contains":
        needle = (req.expected or "").strip().lower()
        score = 1.0 if needle and needle in actual.lower() else 0.0
        passed = score == 1.0

    else:  # llm_judge
        judge_prompt = textwrap.dedent(f"""
            You are an evaluator. Score the following LLM response.

            Prompt: {req.prompt}
            Expected (ideal answer): {req.expected or '(not specified)'}
            Actual response: {actual}

            Rate the actual response quality from 0.0 (completely wrong) to 1.0 (perfect).
            Return ONLY a decimal number. No explanation.
        """).strip()
        try:
            raw_score = call_llm(judge_prompt)
            score = max(0.0, min(1.0, float(raw_score.strip())))
        except Exception:
            score = 0.5
        passed = score >= 0.7

    conn = get_pg()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO eval_runs
                        (id, user_id, test_name, model, prompt, expected, actual, score, metric, passed)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(uuid.uuid4()),
                        req.user_id,
                        req.test_name,
                        req.model,
                        req.prompt,
                        req.expected,
                        actual,
                        score,
                        req.metric,
                        passed,
                    ),
                )
    finally:
        conn.close()

    return {
        "actual": actual,
        "score": round(score, 4),
        "passed": passed,
        "metric": req.metric,
        "model": req.model,
    }


# ---------------------------------------------------------------------------
# GET /api/eval/results
# ---------------------------------------------------------------------------
@app.get("/api/eval/results")
def eval_results(limit: int = 20, offset: int = 0):
    limit = min(limit, 100)
    conn = get_pg()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, test_name, model, prompt, expected, actual, score, metric, passed, created_at
                FROM eval_runs
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                (limit, offset),
            )
            rows = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT COUNT(*) FROM eval_runs")
            total = cur.fetchone()["count"]
    finally:
        conn.close()

    for r in rows:
        if isinstance(r.get("created_at"), datetime):
            r["created_at"] = r["created_at"].isoformat()

    return {"results": rows, "total": total, "limit": limit, "offset": offset}


# ---------------------------------------------------------------------------
# GET /api/rag/metrics
# ---------------------------------------------------------------------------
@app.get("/api/rag/metrics")
def rag_metrics():
    conn = get_pg()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, session_id, query, answer, model,
                       faithfulness, answer_relevancy, context_precision, created_at
                FROM rag_queries
                ORDER BY created_at DESC
                LIMIT 50
                """
            )
            rows = [dict(r) for r in cur.fetchall()]
            cur.execute(
                """
                SELECT AVG(faithfulness)     AS avg_faithfulness,
                       AVG(answer_relevancy) AS avg_answer_relevancy,
                       COUNT(*)              AS total_queries
                FROM rag_queries
                """
            )
            agg = dict(cur.fetchone())
    finally:
        conn.close()

    for r in rows:
        if isinstance(r.get("created_at"), datetime):
            r["created_at"] = r["created_at"].isoformat()

        for field in ("query", "answer"):
            if isinstance(r.get(field), str):
                r[field] = redact_local_paths(r[field])

    return {
        "queries": rows,
        "aggregate": {
            "avg_faithfulness": round(float(agg["avg_faithfulness"] or 0), 4),
            "avg_answer_relevancy": round(float(agg["avg_answer_relevancy"] or 0), 4),
            "total_queries": agg["total_queries"],
        },
    }


# ---------------------------------------------------------------------------
# GET /api/health
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health():
    pg_status = "connected"
    qdrant_status = "connected"
    ts = datetime.now(tz=timezone.utc).isoformat()

    conn = None
    try:
        conn = get_pg()
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
    except Exception as exc:
        log.warning("Postgres health check failed: %s", exc)
        pg_status = "error"
    finally:
        if conn is not None:
            conn.close()

    try:
        qdrant_client.get_collections()
    except Exception as exc:
        log.warning("Qdrant health check failed: %s", exc)
        qdrant_status = "error"

    overall = "ok" if pg_status == "connected" and qdrant_status == "connected" else "degraded"
    return {
        "status": overall,
        "postgres": pg_status,
        "qdrant": qdrant_status,
        "timestamp": ts,
    }
