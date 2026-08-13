"""Shared request/response types for the agent runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class TurnRequest:
    channel: str  # "chat" | "voice"
    message: str
    session_id: str = ""
    history: list = field(default_factory=list)
    attachments: list = field(default_factory=list)
    user_id: Optional[str] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class TurnResult:
    reply: str
    spoken: str = ""
    agents_used: list[str] = field(default_factory=list)
    route_intent: str = ""
    route_reason: str = ""
    sources: list[dict] = field(default_factory=list)
    safety: dict = field(default_factory=dict)
    model: Optional[str] = None
    trace_id: str = ""
    latency_ms: int = 0
    degraded: bool = False
    warning: Optional[str] = None
    images: Optional[list] = None
    image_error: Optional[str] = None
    error: Optional[str] = None
    timings: dict = field(default_factory=dict)
    prompt_versions: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)

    def to_public_dict(self) -> dict[str, Any]:
        """JSON-safe payload for API responses (chat/voice)."""
        out: dict[str, Any] = {
            "reply": self.reply,
            "agents_used": self.agents_used,
            "route_intent": self.route_intent,
            "route_reason": self.route_reason,
            "sources": self.sources,
            "safety": self.safety,
            "trace_id": self.trace_id,
            "latency_ms": self.latency_ms,
            "prompt_versions": self.prompt_versions,
        }
        if self.spoken:
            out["spoken"] = self.spoken
        if self.model:
            out["model"] = self.model
        if self.degraded:
            out["degraded"] = True
        if self.warning:
            out["warning"] = self.warning
        if self.images:
            out["images"] = self.images
        if self.image_error:
            out["image_error"] = self.image_error
        if self.error:
            out["error"] = self.error
        if self.timings:
            out["timings"] = self.timings
        return out


# Callbacks injected by the host (chat_server) so agents/ stays free of HTTP server code.
GenerateFn = Callable[..., tuple[Optional[str], Optional[str]]]
# (message, attachments, history) -> (reply, err, model_label, warning)
GeneralGenerateFn = Callable[
    [str, list, list],
    tuple[Optional[str], Optional[str], Optional[str], Optional[str]],
]
# (message, attachments) -> (images|None, err_code|None, reply_text)
ImageGenerateFn = Callable[
    [str, list],
    tuple[Optional[list], Optional[str], str],
]
