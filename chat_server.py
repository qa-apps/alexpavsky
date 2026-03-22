import hashlib
import json
import logging
import os
import re
import sqlite3
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("chat")

DB_PATH = Path(__file__).resolve().parent / "chat.db"
_db_lock = __import__("threading").Lock()


def _init_db():
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS chat_logs (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        ip_hash TEXT NOT NULL,
        role TEXT NOT NULL,
        message TEXT NOT NULL,
        model TEXT,
        created_at REAL NOT NULL
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_logs(session_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_chat_time ON chat_logs(created_at)")
    c.execute("""CREATE TABLE IF NOT EXISTS subscribers (
        id TEXT PRIMARY KEY,
        email TEXT UNIQUE NOT NULL,
        ip_hash TEXT,
        created_at REAL NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        created_at REAL NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        created_at REAL NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS forum_posts (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        user_name TEXT NOT NULL,
        text TEXT NOT NULL,
        created_at REAL NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )""")
    conn.commit()
    conn.close()


def _log_message(session_id, ip_hash, role, message, model=None):
    try:
        with _db_lock:
            conn = sqlite3.connect(str(DB_PATH))
            conn.execute(
                "INSERT INTO chat_logs (id, session_id, ip_hash, role, message, model, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, session_id, ip_hash, role, message[:50000], model, time.time()),
            )
            conn.commit()
            conn.close()
    except Exception as e:
        log.warning("db log error: %s", e)


def _hash_ip(ip):
    return hashlib.sha256((ip or "unknown").encode()).hexdigest()[:16]


def _hash_pw(password):
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def _db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _get_user_by_token(token):
    if not token:
        return None
    conn = _db()
    row = conn.execute(
        "SELECT u.* FROM users u JOIN sessions s ON u.id = s.user_id WHERE s.token = ?",
        (token,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


_init_db()


def _load_dotenv():
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_dotenv()

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models"
OPENROUTER_REFERER = "https://alexpavsky.com"
OPENROUTER_TITLE = "AlexPavsky AI Chat"

PROVIDER_KEY_ENV = {
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

CHAT_MODELS = [
    {"id": "gemini-2.0-flash", "label": "Gemini 2.0 Flash", "provider": "gemini", "free": True, "vision": True, "tier": "S"},
    {"id": "gemini-2.5-flash", "label": "Gemini 2.5 Flash", "provider": "gemini", "free": True, "vision": True, "tier": "M"},
    {"id": "gemini-3-flash-preview", "label": "Gemini 3 Flash", "provider": "gemini", "free": True, "vision": True, "tier": "M"},
    {"id": "gemini-2.5-pro", "label": "Gemini 2.5 Pro", "provider": "gemini", "free": True, "vision": True, "tier": "H"},
    {"id": "gemini-3-pro-preview", "label": "Gemini 3 Pro", "provider": "gemini", "free": True, "vision": True, "tier": "H"},
    {"id": "google/gemma-3-27b-it:free", "label": "Gemma 3 27B", "provider": "openrouter", "free": True, "tier": "S"},
    {"id": "mistralai/mistral-small-3.1-24b-instruct:free", "label": "Mistral Small 24B", "provider": "openrouter", "free": True, "tier": "S"},
    {"id": "stepfun/step-3.5-flash:free", "label": "Step 3.5 Flash", "provider": "openrouter", "free": True, "tier": "S"},
    {"id": "nvidia/nemotron-nano-9b-v2:free", "label": "Nemotron Nano 9B", "provider": "openrouter", "free": True, "tier": "S"},
    {"id": "meta-llama/llama-3.3-70b-instruct:free", "label": "Llama 3.3 70B", "provider": "openrouter", "free": True, "tier": "M"},
    {"id": "z-ai/glm-4.5-air:free", "label": "GLM 4.5 Air", "provider": "openrouter", "free": True, "tier": "M"},
    {"id": "arcee-ai/trinity-large-preview:free", "label": "Arcee Trinity Large", "provider": "openrouter", "free": True, "tier": "M"},
    {"id": "deepseek/deepseek-r1-0528:free", "label": "DeepSeek R1", "provider": "openrouter", "free": True, "tier": "H", "reasoning": True},
    {"id": "qwen/qwen3-coder:free", "label": "Qwen 3 Coder 480B", "provider": "openrouter", "free": True, "tier": "H", "coding": True},
    {"id": "nousresearch/hermes-3-llama-3.1-405b:free", "label": "Hermes 3 405B", "provider": "openrouter", "free": True, "tier": "H"},
    {"id": "llama-3.1-8b-instant", "label": "Llama 3.1 8B Instant", "provider": "groq", "tier": "S"},
    {"id": "llama-3.3-70b-versatile", "label": "Llama 3.3 70B", "provider": "groq", "tier": "M"},
    {"id": "meta-llama/llama-4-scout-17b-16e-instruct", "label": "Llama 4 Scout 17B", "provider": "groq", "tier": "M"},
    {"id": "meta-llama/llama-4-maverick-17b-128e-instruct", "label": "Llama 4 Maverick 17B", "provider": "groq", "tier": "M"},
    {"id": "openai/gpt-oss-20b", "label": "GPT-OSS 20B", "provider": "groq", "tier": "M"},
    {"id": "qwen/qwen3-32b", "label": "Qwen 3 32B", "provider": "groq", "tier": "H", "coding": True},
    {"id": "moonshotai/kimi-k2-instruct", "label": "Kimi K2 Instruct", "provider": "groq", "tier": "H"},
    {"id": "openai/gpt-oss-120b", "label": "GPT-OSS 120B", "provider": "groq", "tier": "H"},
    {"id": "groq/compound", "label": "Compound AI", "provider": "groq", "search": True, "tier": "H"},
    {"id": "groq/compound-mini", "label": "Compound Mini", "provider": "groq", "search": True, "tier": "M"},
]

MODEL_BY_ID = {m["id"]: m for m in CHAT_MODELS}
VISION_MODEL_IDS = {m["id"] for m in CHAT_MODELS if m.get("vision")}
TIER_S = [m for m in CHAT_MODELS if m["tier"] == "S"]
TIER_M = [m for m in CHAT_MODELS if m["tier"] == "M"]
TIER_H = [m for m in CHAT_MODELS if m["tier"] == "H"]
CODING_MODELS = [m for m in CHAT_MODELS if m.get("coding")]
SEARCH_MODELS = [m for m in CHAT_MODELS if m.get("search")]

MAX_ATTACHMENTS = 4
MAX_TEXT_CHARS = 12000
MAX_IMAGE_URL_CHARS = 36_000_000
MAX_BODY_BYTES = 40 * 1024 * 1024

SYSTEM_PROMPT = (
    "You are a helpful AI assistant on Alex Pavsky's personal tech hub. "
    "You can answer questions on any topic — QA, AI testing, coding, science, history, math, languages, or casual chat. "
    "Be concise, friendly, and accurate. Format code in fenced blocks with language tags. "
    "If files are attached, reason from provided text/metadata. "
    "Answer in the user's language when possible."
)

_COMPLEX = re.compile(
    r"(?:анализ|проанализируй|сравни|compare|analyze|explain\s+in\s+detail|"
    r"step[\s-]by[\s-]step|пошагов|таблиц|table|"
    r"algorithm|алгоритм|architect|архитектур|"
    r"write\s+(?:a\s+)?(?:full|complete|detailed)|"
    r"напиши\s+(?:полн|подробн|детальн)|"
    r"research|исследован|multi[\s-]?step|углубл)",
    re.IGNORECASE,
)

_CODE = re.compile(
    r"(?:code|код|script|скрипт|function|функци|debug|дебаг|"
    r"python|javascript|typescript|html|css|sql|react|"
    r"fix\s+(?:the|this|my)\s+(?:bug|error|code)|"
    r"почини|исправь\s+(?:код|ошибк)|implement|реализуй|"
    r"```|def\s+\w+|class\s+\w+|import\s+\w+)",
    re.IGNORECASE,
)

_SEARCH = re.compile(
    r"(?:latest|newest|current|today|2024|2025|2026|"
    r"последн|новост|сегодня|актуальн|"
    r"search\s+for|who\s+won|what\s+happened|"
    r"price\s+of|stock|weather|погода|курс)",
    re.IGNORECASE,
)

_SIMPLE = re.compile(
    r"^(?:hi|hello|hey|привет|здравствуй|добрый\s+день|"
    r"thanks|спасибо|ok|okay|ок|хорошо|"
    r"yes|no|да|нет|bye|пока|"
    r"how\s+are\s+you|как\s+дела|"
    r"what\s+(?:is|are)\s+\w+\??|who\s+(?:is|are)\s+\w+\??)$",
    re.IGNORECASE,
)

EXECUTOR = ThreadPoolExecutor(max_workers=4)


def _provider_available(provider):
    return bool(os.environ.get(PROVIDER_KEY_ENV.get(provider, "GROQ_API_KEY")))


def _pick(candidates):
    for m in candidates:
        if _provider_available(m.get("provider", "groq")):
            return m
    return None


def _route(message, attachments):
    has_images = any(a.get("kind") == "image" for a in attachments)
    has_files = any(a.get("kind") in ("text", "file") for a in attachments)
    msg_len = len(message)

    if has_images:
        vision_pool = [MODEL_BY_ID.get(i) for i in ("gemini-2.5-pro", "gemini-3-pro-preview", "gemini-2.5-flash", "gemini-3-flash-preview", "gemini-2.0-flash") if MODEL_BY_ID.get(i)]
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
    return CHAT_MODELS[0], "M", "fallback"


def _max_tokens(tier):
    return {"S": 512, "M": 1024}.get(tier, 2048)


def _clean(val, limit):
    return val.strip()[:limit] if isinstance(val, str) else ""


def _sanitize_attachments(items):
    if not isinstance(items, list):
        return []
    out = []
    for item in items[:MAX_ATTACHMENTS]:
        if not isinstance(item, dict):
            continue
        base = {"name": _clean(item.get("name"), 200), "type": _clean(item.get("type"), 100), "size": item.get("size", 0) if isinstance(item.get("size"), int) else 0}
        kind = _clean(item.get("kind"), 20)
        if kind == "image":
            url = item.get("data_url")
            if isinstance(url, str) and url.startswith("data:image/") and len(url) <= MAX_IMAGE_URL_CHARS:
                out.append({**base, "kind": "image", "data_url": url})
        elif kind == "text":
            text = item.get("text") if isinstance(item.get("text"), str) else ""
            trunc = bool(item.get("truncated"))
            if len(text) > MAX_TEXT_CHARS:
                text, trunc = text[:MAX_TEXT_CHARS], True
            out.append({**base, "kind": "text", "text": text, "truncated": trunc})
        else:
            out.append({**base, "kind": "file"})
    return out


def _build_content(message, attachments, model_id):
    parts, warning = [], ""
    if message.strip():
        parts.append({"type": "text", "text": message.strip()})
    text_blocks, file_notes = [], []
    for a in attachments:
        kind, name = a.get("kind"), a.get("name", "unnamed")
        if kind == "image":
            if model_id in VISION_MODEL_IDS:
                parts.append({"type": "image_url", "image_url": {"url": a.get("data_url")}})
            else:
                warning = "Image attached but model is text-only."
                file_notes.append(f"image: {name}")
        elif kind == "text":
            text = a.get("text", "")
            if text:
                chunk = f"[Attached: {name}]\n{text}"
                if a.get("truncated"):
                    chunk += "\n[truncated]"
                text_blocks.append(chunk)
            else:
                file_notes.append(f"file: {name}")
        else:
            file_notes.append(f"file: {name} ({a.get('type', '?')})")
    if text_blocks:
        parts.append({"type": "text", "text": "\n\n".join(text_blocks)})
    if file_notes:
        parts.append({"type": "text", "text": "Attached:\n- " + "\n- ".join(file_notes)})
    if not parts:
        parts.append({"type": "text", "text": "Please help with my attached files."})
    if len(parts) == 1 and parts[0].get("type") == "text":
        return parts[0]["text"], warning
    return parts, warning


def _content_to_gemini_parts(user_content):
    parts = []
    if isinstance(user_content, str):
        parts.append({"text": user_content})
    elif isinstance(user_content, list):
        for item in user_content:
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
    req = Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())
    candidates = data.get("candidates", [])
    if not candidates:
        return ""
    return "\n".join(p.get("text", "") for p in candidates[0].get("content", {}).get("parts", []) if p.get("text")).strip()


def _extract_reply(data):
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(i.get("text", "") for i in content if isinstance(i, dict) and i.get("text"))
    return ""


def _call_model(model, system_prompt, user_content, max_tok=1024, history=None):
    provider = model.get("provider", "groq")
    api_key = os.environ.get(PROVIDER_KEY_ENV.get(provider, "GROQ_API_KEY"))
    if not api_key:
        return None, f"missing_{provider}_key"
    try:
        if provider == "gemini":
            return _call_gemini(model["id"], system_prompt, user_content, api_key, history), None
        messages = [{"role": "system", "content": system_prompt}]
        for h in (history or []):
            messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
        messages.append({"role": "user", "content": user_content})
        payload = {
            "model": model["id"],
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": max_tok,
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        if provider == "openrouter":
            headers["HTTP-Referer"] = OPENROUTER_REFERER
            headers["X-Title"] = OPENROUTER_TITLE
        req = Request(GROQ_API_URL if provider == "groq" else OPENROUTER_API_URL, data=json.dumps(payload).encode(), headers=headers, method="POST")
        with urlopen(req, timeout=25) as resp:
            return _extract_reply(json.loads(resp.read().decode())), None
    except HTTPError as e:
        body = ""
        try:
            body = e.read().decode()[:200]
        except Exception:
            pass
        return None, f"http_{e.code}:{body}"
    except (URLError, TimeoutError):
        return None, "timeout"
    except Exception:
        return None, "unknown"


def _get_fallback_chain(current, tier):
    tried = {current["id"]}
    chain = []
    pool = TIER_S + TIER_M + TIER_H if tier == "S" else TIER_M + TIER_H + TIER_S
    for m in pool:
        if m["id"] not in tried and m.get("provider") != current.get("provider"):
            chain.append(m)
            tried.add(m["id"])
    for m in pool:
        if m["id"] not in tried:
            chain.append(m)
            tried.add(m["id"])
    return chain


class Handler(SimpleHTTPRequestHandler):
    def _cors(self):
        origin = self.headers.get("Origin", "*")
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Credentials", "true")

    def _get_session(self):
        cookie_header = self.headers.get("Cookie", "")
        for part in cookie_header.split(";"):
            part = part.strip()
            if part.startswith("ap_chat_sid="):
                return part.split("=", 1)[1].strip()
        return None

    def _json(self, status, payload, new_session=None):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._cors()
        if new_session:
            self.send_header("Set-Cookie", f"ap_chat_sid={new_session}; Path=/; Max-Age=86400; SameSite=Lax; HttpOnly")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _client_ip(self):
        forwarded = self.headers.get("X-Forwarded-For", "")
        return forwarded.split(",")[0].strip() if forwarded else self.client_address[0]

    def _get_token(self):
        auth = self.headers.get("Authorization", "")
        return auth.replace("Bearer ", "") if auth.startswith("Bearer ") else ""

    def _read_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode()) if length > 0 else {}

    def do_GET(self):
        if self.path == "/api/auth/me":
            user = _get_user_by_token(self._get_token())
            if not user:
                self._json(401, {"error": "not_authenticated"})
                return
            self._json(200, {"id": user["id"], "name": user["name"], "email": user["email"]})
            return
        if self.path == "/api/forum/posts":
            conn = _db()
            rows = conn.execute("SELECT id, user_id, user_name, text, created_at FROM forum_posts ORDER BY created_at DESC LIMIT 50").fetchall()
            conn.close()
            posts = [{"id": r["id"], "user_name": r["user_name"], "text": r["text"], "created_at": r["created_at"]} for r in rows]
            self._json(200, {"posts": posts})
            return
        super().do_GET()

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        if self.path == "/api/subscribe":
            return self._handle_subscribe()
        if self.path == "/api/auth/register":
            return self._handle_register()
        if self.path == "/api/auth/login":
            return self._handle_login()
        if self.path == "/api/auth/logout":
            return self._handle_logout()
        if self.path == "/api/forum/posts":
            return self._handle_forum_post()
        if self.path != "/api/chat":
            self._json(404, {"error": "not_found"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length > MAX_BODY_BYTES:
            self._json(413, {"error": "payload_too_large"})
            return

        try:
            body = json.loads(self.rfile.read(length).decode()) if length > 0 else {}
        except Exception:
            self._json(400, {"error": "invalid_json"})
            return

        session_id = self._get_session()
        new_session = None
        if not session_id:
            session_id = uuid.uuid4().hex
            new_session = session_id
        ip_hash = _hash_ip(self._client_ip())

        message = _clean(body.get("message"), 12000)
        attachments = _sanitize_attachments(body.get("attachments", []))
        history = body.get("history", [])
        if not isinstance(history, list):
            history = []
        history = history[-20:]

        if not message and not attachments:
            self._json(400, {"error": "empty_message"})
            return

        _log_message(session_id, ip_hash, "user", message or "[attachment]")

        model, tier, reason = _route(message, attachments)
        max_tok = _max_tokens(tier)
        user_content, warning = _build_content(message, attachments, model["id"])

        log.info("route: %s tier=%s reason=%s sid=%s", model["label"], tier, reason, session_id[:8])

        reply, err = _call_model(model, SYSTEM_PROMPT, user_content, max_tok, history)

        if not reply or not reply.strip():
            chain = _get_fallback_chain(model, tier)
            for fallback in chain[:6]:
                log.info("fallback: %s -> %s (%s)", model["label"], fallback["label"], fallback["provider"])
                reply, err = _call_model(fallback, SYSTEM_PROMPT, user_content, max_tok, history)
                if reply and reply.strip():
                    model = fallback
                    break
                time.sleep(0.5)

        if not reply or not reply.strip():
            self._json(502, {"error": err or "no_response", "reply": "Sorry, all AI models are temporarily unavailable. Please try again in a moment."}, new_session)
            return

        _log_message(session_id, ip_hash, "assistant", reply.strip(), model["label"])

        result = {"reply": reply.strip()}
        if warning:
            result["warning"] = warning
        self._json(200, result, new_session)


    def _handle_subscribe(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length > 4096:
            self._json(413, {"error": "payload_too_large"})
            return
        try:
            body = json.loads(self.rfile.read(length).decode()) if length > 0 else {}
        except Exception:
            self._json(400, {"error": "invalid_json"})
            return
        email = (body.get("email") or "").strip().lower()
        if not email or not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
            self._json(400, {"error": "Invalid email address."})
            return
        ip_hash = _hash_ip(self._client_ip())
        try:
            with _db_lock:
                conn = sqlite3.connect(str(DB_PATH))
                conn.execute(
                    "INSERT OR IGNORE INTO subscribers (id, email, ip_hash, created_at) VALUES (?, ?, ?, ?)",
                    (uuid.uuid4().hex, email, ip_hash, time.time()),
                )
                conn.commit()
                conn.close()
            log.info("subscriber: %s ip=%s", email[:3] + '***', ip_hash[:8])
            self._json(200, {"message": "You're subscribed! We'll send you the latest AI & QA news."})
        except Exception as e:
            log.warning("subscribe error: %s", e)
            self._json(500, {"error": "Server error. Please try again."})


    def _handle_register(self):
        try:
            body = self._read_body()
        except Exception:
            self._json(400, {"error": "invalid_json"})
            return
        name = (body.get("name") or "").strip()
        email = (body.get("email") or "").strip().lower()
        password = body.get("password") or ""
        if not name or not email or len(password) < 6:
            self._json(400, {"error": "Name, email, and password (min 6 chars) required."})
            return
        conn = _db()
        if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            conn.close()
            self._json(409, {"error": "Email already registered."})
            return
        uid = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO users (id, name, email, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
            (uid, name, email, _hash_pw(password), time.time())
        )
        token = uuid.uuid4().hex
        conn.execute("INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)", (token, uid, time.time()))
        conn.commit()
        conn.close()
        log.info("register: %s", email[:3] + "***")
        self._json(200, {"token": token, "user": {"id": uid, "name": name, "email": email}})

    def _handle_login(self):
        try:
            body = self._read_body()
        except Exception:
            self._json(400, {"error": "invalid_json"})
            return
        email = (body.get("email") or "").strip().lower()
        password = body.get("password") or ""
        conn = _db()
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if not row or row["password_hash"] != _hash_pw(password):
            conn.close()
            self._json(401, {"error": "Invalid email or password."})
            return
        user = dict(row)
        token = uuid.uuid4().hex
        conn.execute("INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)", (token, user["id"], time.time()))
        conn.commit()
        conn.close()
        log.info("login: %s", email[:3] + "***")
        self._json(200, {"token": token, "user": {"id": user["id"], "name": user["name"], "email": user["email"]}})

    def _handle_logout(self):
        token = self._get_token()
        if token:
            conn = _db()
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            conn.commit()
            conn.close()
        self._json(200, {"ok": True})

    def _handle_forum_post(self):
        user = _get_user_by_token(self._get_token())
        if not user:
            self._json(401, {"error": "Login required to post."})
            return
        try:
            body = self._read_body()
        except Exception:
            self._json(400, {"error": "invalid_json"})
            return
        text = (body.get("text") or "").strip()
        if not text or len(text) > 2000:
            self._json(400, {"error": "Message required (max 2000 chars)."})
            return
        pid = uuid.uuid4().hex
        now = time.time()
        conn = _db()
        conn.execute(
            "INSERT INTO forum_posts (id, user_id, user_name, text, created_at) VALUES (?, ?, ?, ?, ?)",
            (pid, user["id"], user["name"], text, now)
        )
        conn.commit()
        conn.close()
        self._json(200, {"post": {"id": pid, "user_name": user["name"], "text": text, "created_at": now}})


def main():
    port = int(os.environ.get("CHAT_PORT", "8000"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    log.info("Chat server on port %d with %d models", port, len(CHAT_MODELS))
    for p, k in PROVIDER_KEY_ENV.items():
        log.info("  %s: %s", p, "OK" if os.environ.get(k) else "MISSING")
    server.serve_forever()


if __name__ == "__main__":
    main()
