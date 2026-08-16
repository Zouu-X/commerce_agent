from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.types import ModelResponse
from app.models import AgentTrace, TraceEvent

_COST_QUANTUM = Decimal("0.00000001")
_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "address",
        "api_key",
        "email",
        "password",
        "phone",
        "recipient",
        "token",
        "tracking_number",
    }
)
_EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_PHONE_PATTERN = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")


def sanitize(value: Any, *, max_string_chars: int = 1000) -> Any:
    """Return JSON-safe trace data without common credentials or customer PII."""
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower()
            sanitized[str(key)] = (
                "[REDACTED]"
                if normalized in _SENSITIVE_KEYS
                else sanitize(item, max_string_chars=max_string_chars)
            )
        return sanitized
    if isinstance(value, (list, tuple)):
        return [sanitize(item, max_string_chars=max_string_chars) for item in value]
    if isinstance(value, str):
        redacted = _EMAIL_PATTERN.sub("[REDACTED_EMAIL]", value)
        redacted = _PHONE_PATTERN.sub("[REDACTED_PHONE]", redacted)
        return redacted[:max_string_chars]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:max_string_chars]


def estimate_cost(
    input_tokens: int,
    output_tokens: int,
    *,
    input_cost_per_million: Decimal,
    output_cost_per_million: Decimal,
) -> Decimal:
    cost = (
        Decimal(input_tokens) * input_cost_per_million
        + Decimal(output_tokens) * output_cost_per_million
    ) / Decimal(1_000_000)
    return cost.quantize(_COST_QUANTUM, rounding=ROUND_HALF_UP)


class TraceRecorder:
    def __init__(
        self,
        trace: AgentTrace,
        *,
        input_cost_per_million: Decimal,
        output_cost_per_million: Decimal,
    ) -> None:
        self.trace = trace
        self._input_rate = input_cost_per_million
        self._output_rate = output_cost_per_million
        self._next_event_index = 1
        self._total_cost = Decimal("0")

    def add_event(
        self,
        event_type: str,
        name: str,
        status: str,
        *,
        input_data: dict[str, Any] | None = None,
        output_data: dict[str, Any] | None = None,
        latency_ms: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost: Decimal | None = None,
    ) -> TraceEvent:
        event = TraceEvent(
            event_index=self._next_event_index,
            event_type=event_type,
            name=name,
            status=status,
            input_json=sanitize(input_data) if input_data is not None else None,
            output_json=sanitize(output_data) if output_data is not None else None,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=cost,
            created_at=datetime.now(UTC),
        )
        self._next_event_index += 1
        self.trace.events.append(event)
        return event

    def request_received(self, user_content: str) -> None:
        self.add_event(
            "request",
            "user_message",
            "succeeded",
            input_data={"content_preview": user_content[:240], "content_chars": len(user_content)},
        )

    def model_completed(
        self,
        loop: int,
        response: ModelResponse,
        *,
        latency_ms: int,
        message_count: int,
        tool_count: int,
    ) -> None:
        cost = estimate_cost(
            response.usage.input_tokens,
            response.usage.output_tokens,
            input_cost_per_million=self._input_rate,
            output_cost_per_million=self._output_rate,
        )
        self._total_cost += cost
        self.trace.model_calls += 1
        self.trace.input_tokens += response.usage.input_tokens
        self.trace.output_tokens += response.usage.output_tokens
        self.add_event(
            "model",
            f"model_loop_{loop}",
            "succeeded",
            input_data={"message_count": message_count, "available_tools": tool_count},
            output_data={
                "content_preview": response.content[:500],
                "tool_calls": [
                    {"id": call.id, "name": call.name} for call in response.tool_calls
                ],
            },
            latency_ms=latency_ms,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cost=cost,
        )

    def tool_completed(
        self,
        *,
        call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        latency_ms: int,
    ) -> None:
        self.trace.tool_calls += 1
        ok = result.get("ok") is True
        self.add_event(
            "tool",
            tool_name,
            "succeeded" if ok else "failed",
            input_data={"call_id": call_id, "arguments": arguments},
            output_data=result,
            latency_ms=latency_ms,
        )

    def complete(self, response: str, *, total_latency_ms: int) -> None:
        sanitized_response = sanitize(response)
        self.trace.status = "succeeded"
        self.trace.total_latency_ms = total_latency_ms
        self.trace.estimated_cost_usd = self._total_cost
        self.trace.final_response_preview = str(sanitized_response)[:1000]
        self.trace.completed_at = datetime.now(UTC)
        self.add_event(
            "response",
            "assistant_message",
            "succeeded",
            output_data={"content_preview": response[:500]},
        )

    def fail(self, error_code: str, *, total_latency_ms: int) -> None:
        self.trace.status = "failed"
        self.trace.total_latency_ms = total_latency_ms
        self.trace.estimated_cost_usd = self._total_cost
        self.trace.error_code = error_code[:160]
        self.trace.completed_at = datetime.now(UTC)
        self.add_event(
            "error",
            "agent_failed",
            "failed",
            output_data={"error_code": error_code[:160]},
        )


async def persist_failed_trace(
    session: AsyncSession,
    trace: AgentTrace,
) -> UUID:
    """Roll back partial turn state, then persist only its immutable failure evidence.

    A failed tool/model turn must not commit pending business writes or partial messages.
    Copying the Trace values before rollback lets the failure timeline survive in a clean
    transaction without widening the transaction boundary of the Agent runtime.
    """
    trace_values = {
        "id": trace.id,
        "tenant_id": trace.tenant_id,
        "store_id": trace.store_id,
        "customer_id": trace.customer_id,
        "conversation_id": trace.conversation_id,
        "status": trace.status,
        "model_provider": trace.model_provider,
        "model_name": trace.model_name,
        "prompt_version": trace.prompt_version,
        "model_calls": trace.model_calls,
        "tool_calls": trace.tool_calls,
        "input_tokens": trace.input_tokens,
        "output_tokens": trace.output_tokens,
        "estimated_cost_usd": trace.estimated_cost_usd,
        "first_model_response_ms": trace.first_model_response_ms,
        "total_latency_ms": trace.total_latency_ms,
        "final_response_preview": trace.final_response_preview,
        "error_code": trace.error_code,
        "started_at": trace.started_at,
        "completed_at": trace.completed_at,
    }
    event_values = [
        {
            "id": event.id or uuid4(),
            "trace_id": trace.id,
            "event_index": event.event_index,
            "event_type": event.event_type,
            "name": event.name,
            "status": event.status,
            "input_json": event.input_json,
            "output_json": event.output_json,
            "latency_ms": event.latency_ms,
            "input_tokens": event.input_tokens,
            "output_tokens": event.output_tokens,
            "estimated_cost_usd": event.estimated_cost_usd,
            "created_at": event.created_at,
        }
        for event in trace.events
    ]

    await session.rollback()
    await session.execute(insert(AgentTrace), trace_values)
    if event_values:
        await session.execute(insert(TraceEvent), event_values)
    await session.commit()
    return trace.id
