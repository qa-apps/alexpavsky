"""Optional Langfuse tracing helpers for alexpavsky.com.

The site must keep serving chat even when the tracing SDK is absent, disabled,
or temporarily unable to export. This module hides those failures behind small
no-op helpers so endpoint code can stay readable.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
from contextlib import contextmanager
from typing import Any, Generator

log = logging.getLogger("chat.observability")

_CLIENT: Any = None
_INIT_ATTEMPTED = False
_INIT_LOCK = threading.Lock()


class NoopObservation:
    id = ""
    trace_id = ""

    def update(self, **_kwargs: Any) -> None:
        return None

    def end(self) -> None:
        return None


def _truthy(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _client() -> Any:
    global _CLIENT, _INIT_ATTEMPTED
    with _INIT_LOCK:
        if _INIT_ATTEMPTED:
            return _CLIENT
        _INIT_ATTEMPTED = True

        if _truthy("LANGFUSE_DISABLED"):
            return None
        if not os.environ.get("LANGFUSE_PUBLIC_KEY") or not os.environ.get("LANGFUSE_SECRET_KEY"):
            return None
        if os.environ.get("LANGFUSE_BASE_URL") and not os.environ.get("LANGFUSE_HOST"):
            os.environ["LANGFUSE_HOST"] = os.environ["LANGFUSE_BASE_URL"]

        try:
            from langfuse import get_client  # type: ignore

            _CLIENT = get_client()
        except Exception as exc:  # noqa: BLE001
            log.warning("langfuse_init_failed: %s", exc)
            _CLIENT = None
        return _CLIENT


def enabled() -> bool:
    return _client() is not None


def safe_text(value: Any, limit: int = 800) -> str:
    text = str(value or "")
    text = re.sub(r"\b(?:sk|pk)-[A-Za-z0-9_-]{12,}\b", "[redacted-key]", text)
    text = re.sub(r"Bearer\s+[A-Za-z0-9._-]{12,}", "Bearer [redacted]", text, flags=re.IGNORECASE)
    return text[:limit]


@contextmanager
def attributes(
    *,
    user_id: str = "",
    session_id: str = "",
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Generator[None, None, None]:
    client = _client()
    if client is None:
        yield
        return
    try:
        client.update_current_trace(
            user_id=user_id or None,
            session_id=session_id or None,
            tags=tags or None,
            metadata=metadata or None,
        )
        yield
    except Exception as exc:  # noqa: BLE001
        log.debug("langfuse_attributes_failed: %s", exc)
        yield


@contextmanager
def observation(
    name: str,
    *,
    as_type: str = "span",
    input: Any = None,  # noqa: A002 - Langfuse API name
    output: Any = None,
    metadata: dict[str, Any] | None = None,
    model: str | None = None,
    trace_context: dict[str, str] | None = None,
) -> Generator[Any, None, None]:
    client = _client()
    if client is None:
        yield NoopObservation()
        return

    params: dict[str, Any] = {"name": name, "as_type": as_type}
    if input is not None:
        params["input"] = input
    if output is not None:
        params["output"] = output
    if metadata:
        params["metadata"] = metadata
    if model:
        params["model"] = model
    if trace_context:
        params["trace_context"] = trace_context

    ctx = None
    obs: Any = NoopObservation()
    try:
        ctx = client.start_as_current_observation(**params)
        obs = ctx.__enter__()
    except Exception as exc:  # noqa: BLE001
        log.debug("langfuse_observation_start_failed name=%s: %s", name, exc)
        yield NoopObservation()
        return

    exc_info = (None, None, None)
    try:
        yield obs
    except BaseException:
        exc_info = sys.exc_info()
        try:
            obs.update(metadata={"error": f"{exc_info[0].__name__}: {exc_info[1]}"})
        except Exception:
            pass
        raise
    finally:
        try:
            ctx.__exit__(*exc_info)
        except Exception as exc:  # noqa: BLE001
            log.debug("langfuse_observation_end_failed name=%s: %s", name, exc)


def update(obs: Any, **kwargs: Any) -> None:
    try:
        if obs is not None and hasattr(obs, "update"):
            obs.update(**kwargs)
    except Exception as exc:  # noqa: BLE001
        log.debug("langfuse_update_failed: %s", exc)


def current_trace_id(obs: Any | None = None) -> str:
    if obs is not None:
        trace_id = getattr(obs, "trace_id", "")
        if trace_id:
            return str(trace_id)
    client = _client()
    if client is None:
        return ""
    try:
        return str(client.get_current_trace_id() or "")
    except Exception:
        return ""


def trace_url(trace_id: str) -> str:
    traces_url = os.environ.get("LANGFUSE_TRACES_URL", "").strip()
    if traces_url and trace_id:
        return f"{traces_url.rstrip('/')}/{trace_id}"
    return traces_url or os.environ.get("LANGFUSE_BASE_URL", "").strip() or os.environ.get("LANGFUSE_HOST", "").strip()


def flush() -> None:
    client = _client()
    if client is None:
        return
    try:
        client.flush()
    except Exception as exc:  # noqa: BLE001
        log.debug("langfuse_flush_failed: %s", exc)
