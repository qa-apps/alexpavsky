"""Observability adapter — Langfuse (chat) + LangWatch (voice) + local fallback.

Not an LLM agent. Every turn gets a trace_id; spans/generations nest under it.
SDKs are optional: missing keys or packages degrade to structured logging.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Generator, Optional

log = logging.getLogger("agents.obs")

PROMPT_VERSION = "obs-v1"


def _env_truthy(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class SpanRecord:
    name: str
    start: float
    end: float = 0.0
    input: Any = None
    output: Any = None
    metadata: dict = field(default_factory=dict)
    level: str = "DEFAULT"
    kind: str = "span"  # span | generation
    model: Optional[str] = None


class TraceSession:
    """In-process trace handle used by the runtime."""

    def __init__(
        self,
        channel: str,
        session_id: str = "",
        user_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ):
        self.channel = channel
        self.session_id = session_id or ""
        self.user_id = user_id
        self.metadata = dict(metadata or {})
        self.trace_id = uuid.uuid4().hex
        self.started = time.time()
        self.spans: list[SpanRecord] = []
        self.scores: list[dict] = []
        self._backend = _select_backend(channel)
        self._remote = None
        try:
            self._remote = self._backend.start_trace(self)
        except Exception as exc:  # noqa: BLE001
            log.warning("obs_start_failed backend=%s err=%s", type(self._backend).__name__, exc)
            self._remote = None

    @contextmanager
    def span(
        self,
        name: str,
        input: Any = None,
        metadata: Optional[dict] = None,
        kind: str = "span",
        model: Optional[str] = None,
    ) -> Generator[SpanRecord, None, None]:
        rec = SpanRecord(
            name=name,
            start=time.time(),
            input=input,
            metadata=dict(metadata or {}),
            kind=kind,
            model=model,
        )
        remote_span = None
        if self._remote is not None:
            try:
                remote_span = self._backend.start_span(self._remote, rec)
            except Exception as exc:  # noqa: BLE001
                log.debug("obs_span_start_failed name=%s err=%s", name, exc)
        try:
            yield rec
        except Exception as exc:
            rec.metadata["error"] = f"{type(exc).__name__}: {exc}"
            rec.level = "ERROR"
            raise
        finally:
            rec.end = time.time()
            self.spans.append(rec)
            if self._remote is not None and remote_span is not None:
                try:
                    self._backend.end_span(remote_span, rec)
                except Exception as exc:  # noqa: BLE001
                    log.debug("obs_span_end_failed name=%s err=%s", name, exc)

    def score(self, name: str, value: float, comment: str = "") -> None:
        self.scores.append({"name": name, "value": value, "comment": comment})
        if self._remote is not None:
            try:
                self._backend.score(self._remote, name, value, comment)
            except Exception as exc:  # noqa: BLE001
                log.debug("obs_score_failed name=%s err=%s", name, exc)

    def end(self, output: Any = None, metadata: Optional[dict] = None) -> None:
        meta = dict(self.metadata)
        if metadata:
            meta.update(metadata)
        duration_ms = int((time.time() - self.started) * 1000)
        meta["duration_ms"] = duration_ms
        meta["span_count"] = len(self.spans)
        if self._remote is not None:
            try:
                self._backend.end_trace(self._remote, output=output, metadata=meta, scores=self.scores)
            except Exception as exc:  # noqa: BLE001
                log.warning("obs_end_failed err=%s", exc)
        if _env_truthy("AGENT_OBS_LOG", "1"):
            span_names = [s.name for s in self.spans]
            log.info(
                "agent_trace id=%s channel=%s session=%s spans=%s duration_ms=%s meta=%s",
                self.trace_id[:12],
                self.channel,
                (self.session_id or "")[:8],
                span_names,
                duration_ms,
                {k: meta.get(k) for k in ("agents_used", "route_intent", "safety_decision", "model") if k in meta},
            )


# ── Backends ─────────────────────────────────────────────────────────────────


class _NoopBackend:
    def start_trace(self, session: TraceSession):
        return {"trace_id": session.trace_id}

    def start_span(self, remote, rec: SpanRecord):
        return rec.name

    def end_span(self, remote_span, rec: SpanRecord):
        return None

    def score(self, remote, name: str, value: float, comment: str = ""):
        return None

    def end_trace(self, remote, output=None, metadata=None, scores=None):
        return None


class _LangfuseBackend:
    """Chat observability via Langfuse (optional dependency)."""

    def __init__(self):
        from langfuse import Langfuse  # type: ignore

        self._client = Langfuse(
            public_key=os.environ.get("LANGFUSE_PUBLIC_KEY"),
            secret_key=os.environ.get("LANGFUSE_SECRET_KEY"),
            host=os.environ.get("LANGFUSE_HOST") or os.environ.get("LANGFUSE_BASE_URL") or None,
        )

    def start_trace(self, session: TraceSession):
        kwargs = {
            "name": "chat_turn" if session.channel == "chat" else f"{session.channel}_turn",
            "id": session.trace_id,
            "session_id": session.session_id or None,
            "user_id": session.user_id,
            "metadata": {"channel": session.channel, **session.metadata},
            "input": session.metadata.get("input"),
        }
        # langfuse v2 style
        if hasattr(self._client, "trace"):
            return self._client.trace(**{k: v for k, v in kwargs.items() if v is not None})
        # langfuse v3 start_as_current_span style — fall back to create_trace_id only
        return self._client.start_span(name=kwargs["name"], input=kwargs.get("input"), metadata=kwargs.get("metadata"))

    def start_span(self, remote, rec: SpanRecord):
        if rec.kind == "generation" and hasattr(remote, "generation"):
            return remote.generation(
                name=rec.name,
                model=rec.model,
                input=rec.input,
                metadata=rec.metadata,
            )
        if hasattr(remote, "span"):
            return remote.span(name=rec.name, input=rec.input, metadata=rec.metadata)
        return None

    def end_span(self, remote_span, rec: SpanRecord):
        if remote_span is None:
            return
        end_kwargs = {"output": rec.output, "metadata": rec.metadata}
        if hasattr(remote_span, "end"):
            remote_span.end(**{k: v for k, v in end_kwargs.items() if v is not None})
        elif hasattr(remote_span, "update"):
            remote_span.update(**{k: v for k, v in end_kwargs.items() if v is not None})

    def score(self, remote, name: str, value: float, comment: str = ""):
        if hasattr(remote, "score"):
            remote.score(name=name, value=value, comment=comment or None)

    def end_trace(self, remote, output=None, metadata=None, scores=None):
        if hasattr(remote, "update"):
            remote.update(output=output, metadata=metadata)
        if hasattr(self._client, "flush"):
            self._client.flush()


class _LangWatchBackend:
    """Voice observability via LangWatch (optional dependency)."""

    def __init__(self):
        import langwatch  # type: ignore

        api_key = os.environ.get("LANGWATCH_API_KEY") or os.environ.get("LANGWATCH_KEY")
        if api_key and hasattr(langwatch, "setup"):
            langwatch.setup(api_key=api_key)
        self._lw = langwatch

    def start_trace(self, session: TraceSession):
        # Prefer context-manager API when available; otherwise store a dict.
        if hasattr(self._lw, "trace"):
            # Some versions: langwatch.trace(name=..., ...) as context manager.
            # We keep a manual handle: create via get_current_trace if inside span.
            handle = {
                "name": "voice_turn",
                "trace_id": session.trace_id,
                "session_id": session.session_id,
                "metadata": {"channel": session.channel, **session.metadata},
                "spans": [],
            }
            try:
                # Non-context capture if API supports it
                if hasattr(self._lw, "get_current_trace"):
                    handle["current"] = self._lw.get_current_trace()
            except Exception:
                pass
            return handle
        return {"trace_id": session.trace_id, "spans": []}

    def start_span(self, remote, rec: SpanRecord):
        remote.setdefault("spans", []).append({"name": rec.name, "start": rec.start})
        return rec.name

    def end_span(self, remote_span, rec: SpanRecord):
        for s in reversed(remote.get("spans") or []):
            if s.get("name") == rec.name and "end" not in s:
                s["end"] = rec.end
                s["output"] = rec.output
                s["metadata"] = rec.metadata
                s["latency_ms"] = int((rec.end - rec.start) * 1000)
                break

    def score(self, remote, name: str, value: float, comment: str = ""):
        remote.setdefault("scores", []).append({"name": name, "value": value, "comment": comment})

    def end_trace(self, remote, output=None, metadata=None, scores=None):
        # Emit a structured log that LangWatch can also ingest via OpenTelemetry later.
        # When the SDK is present, try capture if available.
        try:
            if hasattr(self._lw, "log") and callable(self._lw.log):
                self._lw.log({
                    "type": "voice_turn",
                    "trace_id": remote.get("trace_id"),
                    "output": output,
                    "metadata": metadata,
                    "spans": remote.get("spans"),
                    "scores": scores or remote.get("scores"),
                })
        except Exception as exc:  # noqa: BLE001
            log.debug("langwatch_log_failed err=%s", exc)
        log.info(
            "langwatch_voice_trace id=%s spans=%s meta=%s",
            (remote.get("trace_id") or "")[:12],
            [s.get("name") for s in (remote.get("spans") or [])],
            {k: (metadata or {}).get(k) for k in ("agents_used", "route_intent", "stt_ms", "tts_ms", "turn_total_ms")},
        )


class _LangWatchLogOnly:
    """Structured voice traces without the SDK (always-on local visibility)."""

    def start_trace(self, session: TraceSession):
        return {
            "name": "voice_turn",
            "trace_id": session.trace_id,
            "session_id": session.session_id,
            "metadata": {"channel": session.channel, **session.metadata},
            "spans": [],
        }

    def start_span(self, remote, rec: SpanRecord):
        remote.setdefault("spans", []).append({"name": rec.name, "start": rec.start})
        return rec.name

    def end_span(self, remote_span, rec: SpanRecord):
        for s in reversed(remote.get("spans") or []):
            if s.get("name") == rec.name and "end" not in s:
                s["end"] = rec.end
                s["output"] = rec.output
                s["metadata"] = rec.metadata
                s["latency_ms"] = int((rec.end - rec.start) * 1000)
                break

    def score(self, remote, name: str, value: float, comment: str = ""):
        remote.setdefault("scores", []).append({"name": name, "value": value, "comment": comment})

    def end_trace(self, remote, output=None, metadata=None, scores=None):
        log.info(
            "langwatch_voice_trace id=%s spans=%s meta=%s output_chars=%s",
            (remote.get("trace_id") or "")[:12],
            [
                f"{s.get('name')}:{s.get('latency_ms', int(((s.get('end') or 0) - (s.get('start') or 0)) * 1000))}ms"
                for s in (remote.get("spans") or [])
            ],
            {
                k: (metadata or {}).get(k)
                for k in ("agents_used", "route_intent", "stt_ms", "tts_ms", "turn_total_ms", "model")
                if metadata and k in metadata
            },
            len(str(output or "")),
        )


def _select_backend(channel: str):
    if channel == "chat" and os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"):
        if not _env_truthy("AGENT_OBS_DISABLE_LANGFUSE"):
            try:
                return _LangfuseBackend()
            except Exception as exc:  # noqa: BLE001
                log.warning("langfuse_init_failed err=%s — using noop", exc)
    if channel == "voice" and not _env_truthy("AGENT_OBS_DISABLE_LANGWATCH"):
        if os.environ.get("LANGWATCH_API_KEY") or os.environ.get("LANGWATCH_KEY"):
            try:
                return _LangWatchBackend()
            except Exception as exc:  # noqa: BLE001
                log.warning("langwatch_init_failed err=%s — using log backend", exc)
        # Always emit structured voice traces locally so LangWatch wiring is debuggable
        # even before the cloud key is set.
        return _LangWatchLogOnly()
    return _NoopBackend()


def start_trace(
    channel: str,
    session_id: str = "",
    user_id: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> TraceSession:
    return TraceSession(channel=channel, session_id=session_id, user_id=user_id, metadata=metadata)
