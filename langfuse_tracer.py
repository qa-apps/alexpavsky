"""Minimal Langfuse tracing for the /api/chat pipeline (stdlib only).

Each chat turn becomes one Langfuse trace ("chat_turn") whose observations
mirror the pipeline: supervisor_route -> safety_gate -> general (LLM
generation with token usage, one per model tried) -> response_adapter.

Enabled only when LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set
(LANGFUSE_HOST defaults to https://us.cloud.langfuse.com). Events are sent by
a background thread; tracing never raises into, or slows down, the request.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import queue
import threading
import time
import uuid
from datetime import datetime, timezone
from urllib.request import Request, urlopen

log = logging.getLogger("chat")

MAX_TEXT = 4000
_BATCH_MAX = 50
_QUEUE: "queue.Queue[dict]" = queue.Queue(maxsize=5000)
_WORKER: threading.Thread | None = None
_WORKER_LOCK = threading.Lock()


def _config() -> tuple[str, str, str] | None:
    public = (os.environ.get("LANGFUSE_PUBLIC_KEY") or "").strip()
    secret = (os.environ.get("LANGFUSE_SECRET_KEY") or "").strip()
    if not public or not secret or os.environ.get("LANGFUSE_ENABLED", "1") == "0":
        return None
    host = (os.environ.get("LANGFUSE_HOST") or os.environ.get("LANGFUSE_BASE_URL")
            or "https://us.cloud.langfuse.com").rstrip("/")
    return host, public, secret


def enabled() -> bool:
    return _config() is not None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _clip(value):
    if isinstance(value, str) and len(value) > MAX_TEXT:
        return value[:MAX_TEXT] + " …[truncated]"
    return value


def _post(batch: list[dict]) -> None:
    cfg = _config()
    if not cfg or not batch:
        return
    host, public, secret = cfg
    token = base64.b64encode(f"{public}:{secret}".encode()).decode()
    req = Request(
        f"{host}/api/public/ingestion",
        data=json.dumps({"batch": batch}, default=str).encode(),
        headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode() or "{}")
        errors = body.get("errors") or []
        if errors:
            log.warning("langfuse: %d/%d events rejected: %s", len(errors), len(batch), str(errors[0])[:300])
    except Exception as exc:  # network or auth problems must never affect chat
        log.warning("langfuse: ingestion failed: %s", str(exc)[:300])


def _worker() -> None:
    while True:
        batch = [_QUEUE.get()]
        deadline = time.time() + 1.0
        while len(batch) < _BATCH_MAX and time.time() < deadline:
            try:
                batch.append(_QUEUE.get(timeout=max(0.05, deadline - time.time())))
            except queue.Empty:
                break
        _post(batch)


def _enqueue(event_type: str, body: dict) -> None:
    global _WORKER
    if _WORKER is None:
        with _WORKER_LOCK:
            if _WORKER is None:
                _WORKER = threading.Thread(target=_worker, name="langfuse-ingest", daemon=True)
                _WORKER.start()
    try:
        _QUEUE.put_nowait({"id": uuid.uuid4().hex, "timestamp": _now(), "type": event_type, "body": body})
    except queue.Full:
        pass


def _usage(raw: dict | None) -> dict | None:
    if not isinstance(raw, dict):
        return None
    inp = raw.get("prompt_tokens", raw.get("input_tokens", raw.get("input")))
    out = raw.get("completion_tokens", raw.get("output_tokens", raw.get("output")))
    total = raw.get("total_tokens", raw.get("total"))
    try:
        inp = int(inp or 0)
        out = int(out or 0)
        total = int(total or (inp + out))
    except (TypeError, ValueError):
        return None
    if not (inp or out or total):
        return None
    return {"input": inp, "output": out, "total": total}


class ChatTrace:
    """One chat turn. All methods are safe no-ops when tracing is disabled."""

    def __init__(self, *, session_id: str, user_input: str, metadata: dict | None = None):
        self.active = enabled()
        self.trace_id = uuid.uuid4().hex
        self.session_id = str(session_id or "")[:200]
        self.started = time.time()
        if not self.active:
            return
        try:
            _enqueue("trace-create", {
                "id": self.trace_id,
                "timestamp": _iso(self.started),
                "name": "chat_turn",
                "sessionId": self.session_id or None,
                "input": _clip(user_input),
                "metadata": metadata or {},
                "tags": ["alexpavsky-chat"],
                "release": os.environ.get("LANGFUSE_RELEASE") or None,
                "environment": os.environ.get("LANGFUSE_TRACING_ENVIRONMENT") or "production",
            })
        except Exception:
            self.active = False

    def span(self, name: str, start: float, *, input=None, output=None, metadata=None, level: str | None = None) -> None:
        if not self.active:
            return
        try:
            body = {
                "id": uuid.uuid4().hex,
                "traceId": self.trace_id,
                "name": name,
                "startTime": _iso(start),
                "endTime": _now(),
                "input": _clip(input),
                "output": _clip(output),
                "metadata": metadata or {},
            }
            if level:
                body["level"] = level
            _enqueue("span-create", body)
        except Exception:
            pass

    def generation(self, name: str, start: float, *, model: str, provider: str, input=None, output=None,
                   usage: dict | None = None, error=None) -> None:
        if not self.active:
            return
        try:
            body = {
                "id": uuid.uuid4().hex,
                "traceId": self.trace_id,
                "name": name,
                "startTime": _iso(start),
                "endTime": _now(),
                "model": model,
                "input": _clip(input),
                "output": _clip(output),
                "metadata": {"provider": provider},
            }
            u = _usage(usage)
            if u:
                body["usage"] = {**u, "unit": "TOKENS"}
                body["usageDetails"] = u
            if error:
                body["level"] = "ERROR"
                body["statusMessage"] = _clip(json.dumps(error, default=str) if not isinstance(error, str) else error)
            _enqueue("generation-create", body)
        except Exception:
            pass

    def finish(self, output, **metadata) -> None:
        if not self.active:
            return
        try:
            _enqueue("trace-create", {
                "id": self.trace_id,
                "timestamp": _iso(self.started),
                "output": _clip(output),
                "metadata": {**metadata, "latency_ms": int((time.time() - self.started) * 1000)},
            })
        except Exception:
            pass
        self.active = False


def self_test() -> int:
    """Send one synthetic trace and wait for the result (used by setup scripts)."""
    cfg = _config()
    if not cfg:
        print("langfuse self-test: keys not configured")
        return 1
    host, public, secret = cfg
    trace_id = uuid.uuid4().hex
    batch = [
        {"id": uuid.uuid4().hex, "timestamp": _now(), "type": "trace-create",
         "body": {"id": trace_id, "name": "setup_self_test", "sessionId": "setup-self-test",
                  "input": "ping", "output": "pong", "tags": ["setup"]}},
        {"id": uuid.uuid4().hex, "timestamp": _now(), "type": "generation-create",
         "body": {"id": uuid.uuid4().hex, "traceId": trace_id, "name": "self_test_generation",
                  "startTime": _now(), "endTime": _now(), "model": "self-test",
                  "usage": {"input": 1, "output": 1, "total": 2, "unit": "TOKENS"},
                  "usageDetails": {"input": 1, "output": 1, "total": 2}}},
    ]
    token = base64.b64encode(f"{public}:{secret}".encode()).decode()
    req = Request(f"{host}/api/public/ingestion", data=json.dumps({"batch": batch}).encode(),
                  headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode() or "{}")
            status = resp.status
    except Exception as exc:
        print(f"langfuse self-test: FAILED ({str(exc)[:200]}) host={host}")
        return 1
    errors = body.get("errors") or []
    if errors:
        print(f"langfuse self-test: HTTP {status} but rejected: {str(errors)[:300]}")
        return 1
    print(f"langfuse self-test: OK (HTTP {status}, {len(body.get('successes') or [])} events accepted) host={host}")
    return 0


if __name__ == "__main__":
    raise SystemExit(self_test())
