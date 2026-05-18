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
import logging
import os
import textwrap
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
from openai import OpenAI
from pydantic import BaseModel
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

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
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
HF_TOKEN = os.environ.get("HF_TOKEN", "")

# Embeddings: local sentence-transformers (no API key needed)
# Generation: OpenRouter (already configured with key rotation)
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_DIM = 384
USE_OPENAI_EMBED = bool(OPENAI_API_KEY)  # fallback to OpenAI if key provided
LLM_MODEL = "meta-llama/llama-3.3-70b-instruct:free"  # via OpenRouter free tier
QDRANT_COLLECTION = "documents"
CHUNK_TOKENS = 500
OVERLAP_TOKENS = 50
RAG_TOP_K = int(os.environ.get("RAG_TOP_K", "10"))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("rag-api")

# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------
openai_client = OpenAI(api_key=OPENAI_API_KEY) if USE_OPENAI_EMBED else None

# Local sentence-transformers model (loaded once at startup)
_st_model = None
def get_st_model():
    global _st_model
    if _st_model is None:
        from sentence_transformers import SentenceTransformer
        log.info("Loading sentence-transformers model (first run, ~90MB)...")
        _st_model = SentenceTransformer("all-MiniLM-L6-v2")
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


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def embed(texts: list[str]) -> list[list[float]]:
    """Batch embed texts — local sentence-transformers (no API key needed)."""
    if not texts:
        return []
    if USE_OPENAI_EMBED and openai_client:
        resp = openai_client.embeddings.create(model="text-embedding-3-small", input=texts)
        return [item.embedding for item in resp.data]
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


# Provider pool for LLM generation — each provider tried in order on failure.
# All speak the OpenAI chat-completions wire format, so one POST helper works.
_LLM_PROVIDERS = [
    ("groq",       "GROQ_API_KEY",       "https://api.groq.com/openai/v1",       "llama-3.3-70b-versatile"),
    ("cerebras",   "CEREBRAS_API_KEY",   "https://api.cerebras.ai/v1",           "llama-3.3-70b"),
    ("sambanova",  "SAMBANOVA_API_KEY",  "https://api.sambanova.ai/v1",          "Meta-Llama-3.3-70B-Instruct"),
    ("mistral",    "MISTRAL_API_KEY",    "https://api.mistral.ai/v1",            "mistral-small-latest"),
    ("or-llama",   "OPENROUTER_API_KEY", "https://openrouter.ai/api/v1",         "meta-llama/llama-3.3-70b-instruct:free"),
    ("or-deepseek","OPENROUTER_API_KEY", "https://openrouter.ai/api/v1",         "deepseek/deepseek-r1-0528:free"),
    ("or-qwen",    "OPENROUTER_API_KEY", "https://openrouter.ai/api/v1",         "qwen/qwen3-coder:free"),
    ("hf-router",  "HF_TOKEN",           "https://router.huggingface.co/v1",     "meta-llama/Llama-3.3-70B-Instruct:cerebras"),
]


def call_llm(prompt: str, system: str = "") -> str:
    """Call LLM with rotation across all available providers.

    Tries each provider in _LLM_PROVIDERS order; switches to the next on any
    non-200 response or exception. Returns a fallback string only if every
    provider fails.
    """
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    last_error = None
    for name, key_env, base_url, model in _LLM_PROVIDERS:
        key = os.environ.get(key_env, "").strip()
        if not key:
            continue
        try:
            r = httpx.post(
                f"{base_url}/chat/completions",
                json={"model": model, "messages": messages, "max_tokens": 1024},
                headers={
                    "Authorization": f"Bearer {key}",
                    "HTTP-Referer": "https://alexpavsky.com",
                },
                timeout=45,
            )
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"].strip()
            last_error = f"{name}/{model} HTTP {r.status_code}"
            log.warning("%s, trying next provider", last_error)
        except Exception as e:
            last_error = f"{name}/{model} {type(e).__name__}"
            log.warning("%s, trying next provider", last_error)

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
    allow_origins=["*"],
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


@app.post("/api/rag/query")
async def rag_query(req: QueryRequest):
    """
    Semantic search over stored chunks, then generate an answer via LLM.
    Returns answer, sources, Ragas metrics, and both pgvector / Qdrant results.
    """
    if not req.query.strip():
        raise HTTPException(400, "query must not be empty")

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
        {"content": h.payload.get("content", ""), "score": h.score, "chunk_index": h.payload.get("chunk_index")}
        for h in q_hits
    ]

    # Build context from pgvector results (primary), fall back to qdrant
    context_chunks = [r["content"] for r in pg_results] or [r["content"] for r in qdrant_results]

    if not context_chunks:
        raise HTTPException(404, "No relevant documents found. Upload documents first.")

    context_text = "\n\n---\n\n".join(context_chunks)

    # --- LLM answer ---
    system_prompt = (
        "You are a helpful assistant. Answer the user's question using ONLY the provided context. "
        "If the context does not contain enough information, say so clearly."
    )
    user_prompt = f"Context:\n{context_text}\n\nQuestion: {req.query}"
    answer = call_llm(user_prompt, system=system_prompt)

    # --- Metrics ---
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

    sources = [{"content": r["content"][:300], "score": float(r["score"]), "filename": r.get("filename")} for r in pg_results]

    return {
        "answer": answer,
        "sources": sources,
        # Full retrieved chunk text — what the LLM actually saw. Consumers that
        # need to verify groundedness (Ragas faithfulness, manual audit) should
        # read this instead of `sources[].content`, which is a 300-char preview.
        "contexts": context_chunks,
        "metrics": {
            "faithfulness": round(faithfulness, 4),
            "answer_relevancy": round(answer_relevancy, 4),
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
async def eval_run(req: EvalRequest):
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
async def eval_results(limit: int = 20, offset: int = 0):
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
async def rag_metrics():
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
async def health():
    pg_status = "connected"
    qdrant_status = "connected"
    ts = datetime.now(tz=timezone.utc).isoformat()

    try:
        conn = get_pg()
        conn.cursor().execute("SELECT 1")
        conn.close()
    except Exception as exc:
        pg_status = f"error: {exc}"

    try:
        qdrant_client.get_collections()
    except Exception as exc:
        qdrant_status = f"error: {exc}"

    overall = "ok" if pg_status == "connected" and qdrant_status == "connected" else "degraded"
    return {
        "status": overall,
        "postgres": pg_status,
        "qdrant": qdrant_status,
        "timestamp": ts,
    }
