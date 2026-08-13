"""Supervisor-specialist runtime: Safety → Supervisor → specialist → Adapter."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

from . import adapter, observability, rag_agent, safety, supervisor
from .types import GeneralGenerateFn, ImageGenerateFn, TurnRequest, TurnResult

log = logging.getLogger("agents.runtime")

RUNTIME_VERSION = "agent-runtime-v1"


@dataclass
class AgentDeps:
    """Host-injected capabilities (model calls stay in chat_server)."""

    general_generate: Optional[GeneralGenerateFn] = None
    image_generate: Optional[ImageGenerateFn] = None


def run_turn(req: TurnRequest, deps: Optional[AgentDeps] = None) -> TurnResult:
    deps = deps or AgentDeps()
    channel = (req.channel or "chat").strip().lower()
    if channel not in {"chat", "voice"}:
        channel = "chat"
    message = (req.message or "").strip()
    t0 = time.time()
    agents_used: list[str] = []
    prompt_versions = {
        "runtime": RUNTIME_VERSION,
        "safety": safety.PROMPT_VERSION,
        "supervisor": supervisor.PROMPT_VERSION,
        "rag": rag_agent.PROMPT_VERSION,
        "adapter": adapter.PROMPT_VERSION,
        "obs": observability.PROMPT_VERSION,
    }

    trace = observability.start_trace(
        channel=channel,
        session_id=req.session_id,
        user_id=req.user_id,
        metadata={
            "input": message[:500],
            "has_attachments": bool(req.attachments),
            "channel": channel,
        },
    )

    def _finish(
        reply: str,
        *,
        route_intent: str = "",
        route_reason: str = "",
        sources: Optional[list] = None,
        safety_meta: Optional[dict] = None,
        model: Optional[str] = None,
        degraded: bool = False,
        warning: Optional[str] = None,
        images: Optional[list] = None,
        image_error: Optional[str] = None,
        error: Optional[str] = None,
        timings: Optional[dict] = None,
    ) -> TurnResult:
        with trace.span("response_adapter", input={"channel": channel, "chars": len(reply or "")}) as span:
            ui_reply, spoken = adapter.adapt(reply, channel)
            span.output = {"reply_chars": len(ui_reply), "spoken_chars": len(spoken)}
        agents_used_final = list(dict.fromkeys(agents_used + ["response_adapter"]))
        latency_ms = int((time.time() - t0) * 1000)
        safety_out = safety_meta or {"ok": True, "decision": "allow"}
        result = TurnResult(
            reply=ui_reply,
            spoken=spoken,
            agents_used=agents_used_final,
            route_intent=route_intent,
            route_reason=route_reason,
            sources=sources or [],
            safety=safety_out,
            model=model,
            trace_id=trace.trace_id,
            latency_ms=latency_ms,
            degraded=degraded,
            warning=warning,
            images=images,
            image_error=image_error,
            error=error,
            timings=timings or {},
            prompt_versions=prompt_versions,
        )
        # Online score hooks (simple, always-on, free).
        try:
            trace.score("safety_ok", 1.0 if safety_out.get("ok") else 0.0)
            if sources:
                best = max((float(s.get("score") or 0) for s in sources), default=0.0)
                trace.score("source_coverage", min(1.0, best / 0.4) if best else 0.0)
            if channel == "voice" and spoken:
                # Prefer short spoken answers.
                ratio = min(1.0, 400 / max(len(spoken), 1))
                trace.score("voice_brevity", ratio)
        except Exception:  # noqa: BLE001
            pass
        trace.end(
            output={"reply": (ui_reply or "")[:500], "spoken": (spoken or "")[:300]},
            metadata={
                "agents_used": agents_used_final,
                "route_intent": route_intent,
                "route_reason": route_reason,
                "safety_decision": safety_out.get("decision"),
                "model": model,
                "source_count": len(sources or []),
                "latency_ms": latency_ms,
                "prompt_versions": prompt_versions,
                "channel": channel,
            },
        )
        return result

    # ── 1. Safety ────────────────────────────────────────────────────────────
    with trace.span("safety_gate", input={"chars": len(message)}) as span:
        decision = safety.check_safety(message, channel=channel)
        span.output = {"decision": decision.decision, "ok": decision.ok}
        span.metadata["reason"] = decision.reason
    agents_used.append("safety")
    if not decision.ok:
        return _finish(
            decision.reply,
            route_intent="unsafe",
            route_reason=decision.reason,
            safety_meta={"ok": False, "decision": decision.decision, "reason": decision.reason},
        )

    if not message and not req.attachments:
        return _finish(
            "Please send a message.",
            route_intent="empty",
            route_reason="empty_message",
            error="empty_message",
        )

    # ── 2. Supervisor ────────────────────────────────────────────────────────
    with trace.span("supervisor_route", input={"channel": channel}) as span:
        route = supervisor.route(
            message,
            channel=channel,
            has_attachments=bool(req.attachments),
        )
        span.output = {"intent": route.intent, "reason": route.reason, "confidence": route.confidence}
    agents_used.append("supervisor")

    # ── 3. Specialists ───────────────────────────────────────────────────────
    if route.intent == "smalltalk":
        agents_used.append("smalltalk")
        with trace.span("smalltalk", metadata={"reason": route.reason}) as span:
            reply = adapter.smalltalk_reply(message, channel, route.reason)
            span.output = {"reply": reply[:200]}
        return _finish(reply, route_intent=route.intent, route_reason=route.reason)

    if route.intent == "image":
        agents_used.append("image")
        if not deps.image_generate:
            return _finish(
                "Image generation is not available in this channel.",
                route_intent=route.intent,
                route_reason=route.reason,
                error="image_unavailable",
            )
        with trace.span("image_generate") as span:
            images, image_err, reply = deps.image_generate(message, req.attachments)
            span.output = {"ok": bool(images), "error": image_err}
        return _finish(
            reply,
            route_intent=route.intent,
            route_reason=route.reason,
            images=images,
            image_error=image_err,
            model="gemini-image" if images else None,
        )

    if route.intent == "rag":
        agents_used.append("rag")
        t_rag = time.time()
        with trace.span("rag_retrieve", input={"query": message[:300]}) as span:
            rag = rag_agent.query_rag(message, channel=channel, session_id=req.session_id)
            span.output = {
                "source_count": len(rag.sources),
                "best_score": rag.best_score,
                "out_of_domain": rag.out_of_domain,
                "error": rag.error,
            }
        # The RAG API already generates the answer; record as generation for Langfuse.
        with trace.span(
            "rag_generate",
            kind="generation",
            model="rag-llm",
            input={"query": message[:300]},
            metadata={"skip_metrics": channel == "voice"},
        ) as span:
            span.output = {"answer": (rag.answer or "")[:500], "error": rag.error}
        rag_ms = int((time.time() - t_rag) * 1000)
        if rag.error and not rag.answer:
            # Fall back to general when RAG infra fails (chat only).
            if channel == "chat" and deps.general_generate:
                log.info("rag_failed_fallback_general err=%s", rag.error)
                agents_used.append("general")
                return _general(req, deps, trace, agents_used, route, _finish, degraded=True, warning=f"rag_{rag.error}")
            return _finish(
                "The knowledge base is temporarily unavailable. Please try again in a moment.",
                route_intent=route.intent,
                route_reason=route.reason,
                error=rag.error,
                timings={"rag_ms": rag_ms},
            )
        return _finish(
            rag.answer,
            route_intent=route.intent,
            route_reason=route.reason,
            sources=rag.sources,
            model="rag",
            timings={"rag_ms": rag_ms},
        )

    # general
    agents_used.append("general")
    return _general(req, deps, trace, agents_used, route, _finish)


def _general(req, deps, trace, agents_used, route, _finish, degraded=False, warning=None):
    if not deps.general_generate:
        # Voice without general: stay honest.
        if req.channel == "voice":
            return _finish(
                "I can answer from Alex's QA knowledge base. Try a testing or AI-evaluation question.",
                route_intent=route.intent,
                route_reason=route.reason,
                error="general_unavailable",
            )
        return _finish(
            "Sorry, the chat model layer is not configured.",
            route_intent=route.intent,
            route_reason=route.reason,
            error="general_unavailable",
        )
    with trace.span(
        "general_generate",
        kind="generation",
        input={"message": (req.message or "")[:300]},
    ) as span:
        reply, err, model_label, gen_warning = deps.general_generate(
            req.message, req.attachments, req.history
        )
        span.model = model_label
        span.output = {"reply": (reply or "")[:500], "error": err}
        span.metadata["model"] = model_label
    if not reply:
        return _finish(
            "Sorry, all AI models are temporarily unavailable. Please try again in a moment.",
            route_intent=route.intent,
            route_reason=route.reason,
            model=model_label,
            degraded=True,
            error=err or "no_response",
            warning=warning or gen_warning,
        )
    return _finish(
        reply,
        route_intent=route.intent,
        route_reason=route.reason,
        model=model_label,
        degraded=degraded,
        warning=warning or gen_warning,
    )
