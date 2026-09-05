"""Execute scenarios through the real runtime/services in an isolated environment."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from time import perf_counter
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.agent.errors import ModelProviderError
from app.agent.memory import ConversationMemory
from app.agent.provider import ModelProvider
from app.agent.runtime import AgentLimits, AgentRuntime
from app.agent.types import ModelResponse, ProviderMessage, ToolSpec
from app.approvals.context import ApprovalContext
from app.approvals.service import ApprovalService
from app.commerce.context import CommerceContext
from app.commerce.seed import stable_id
from app.evaluations.environment import isolated_environment
from app.evaluations.scenarios import Scenario, Step
from app.evaluations.simulator import UserSimulator
from app.evaluations.verifier import Verdict, snapshot, verify
from app.models import AgentTrace, Conversation, Message, PendingAction
from app.observability.service import sanitize


def error_code(error: BaseException) -> str:
    if isinstance(error, BaseExceptionGroup):
        return ";".join(error_code(item) for item in error.exceptions)
    if isinstance(error, (ValueError, ModelProviderError)):
        return str(error)
    return type(error).__name__


class RecordingProvider:
    def __init__(
        self, provider: ModelProvider, write_barrier: asyncio.Barrier | None = None
    ) -> None:
        self.provider = provider
        self.write_barrier = write_barrier
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self, messages: list[ProviderMessage], tools: list[ToolSpec], *, timeout_seconds: float
    ) -> ModelResponse:
        call: dict[str, Any] = {
            "started_at": datetime.now(UTC).isoformat(),
            "messages": sanitize([asdict(m) for m in messages], max_string_chars=100_000),
        }
        self.calls.append(call)
        started = perf_counter()
        try:
            response = await self.provider.complete(
                messages, tools, timeout_seconds=timeout_seconds
            )
            call["response"] = sanitize(asdict(response), max_string_chars=100_000)
            if self.write_barrier and any(
                c.name.startswith("request_") for c in response.tool_calls
            ):
                # Synchronize at the write-tool boundary as well as the session start.
                async with asyncio.timeout(35):
                    await self.write_barrier.wait()
                call["write_barrier_released_at"] = datetime.now(UTC).isoformat()
                self.write_barrier = None
            return response
        except Exception as error:
            call["error"] = type(error).__name__
            raise
        finally:
            call["latency_ms"] = round((perf_counter() - started) * 1000)


@dataclass
class Attempt:
    trajectory: list[dict[str, Any]] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    checkpoints: list[dict[str, Any]] = field(default_factory=list)
    before: dict[str, Any] = field(default_factory=dict)
    after: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    latency_ms: int = 0
    verdict: Verdict = field(default_factory=Verdict)
    simulator_events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def telemetry(self) -> dict[str, Any]:
        agent_usage = [
            call.get("response", {}).get("usage", {})
            for t in self.trajectory
            for call in t.get("model_calls", [])
        ]
        unreported = sum(
            "response" not in call
            for turn in self.trajectory
            for call in turn.get("model_calls", [])
        )
        return {
            "unreported_agent_usage_calls": unreported,
            "wall_time_ms": self.latency_ms,
            "agent_input_tokens": sum(u.get("input_tokens", 0) for u in agent_usage),
            "agent_output_tokens": sum(u.get("output_tokens", 0) for u in agent_usage),
            "simulator_input_tokens": sum(e.get("input_tokens", 0) for e in self.simulator_events),
            "simulator_output_tokens": sum(
                e.get("output_tokens", 0) for e in self.simulator_events
            ),
            "model_calls": len(agent_usage),
            "tool_calls": len(self.calls),
        }


class ScenarioHarness:
    def __init__(
        self,
        source: AsyncEngine,
        provider: ModelProvider,
        *,
        provider_name: str,
        model_name: str,
        simulator_provider: ModelProvider | None = None,
    ) -> None:
        self.source = source
        self.provider = provider
        self.provider_name = provider_name
        self.model_name = model_name
        self.simulator = UserSimulator(simulator_provider)
        self.conversations: dict[str, UUID] = {}
        self.responses: dict[str, str] = {}
        self.result = Attempt()

    async def run(self, case: Scenario) -> Attempt:
        started = perf_counter()
        if case.requires_postgres and self.source.dialect.name != "postgresql":
            self.result.error = "postgresql_required_for_concurrency"
            self.result.verdict.check(
                "environment", False, expected="postgresql", actual=self.source.dialect.name
            )
            return self.result
        try:
            async with isolated_environment(self.source) as engine:
                self.factory = async_sessionmaker(engine, expire_on_commit=False)
                self.context = CommerceContext(
                    tenant_id=stable_id(f"tenant:{case.tenant_key}"),
                    store_id=stable_id(f"store:{case.tenant_key}"),
                    customer_id=stable_id(f"customer:{case.tenant_key}:{case.customer_index}"),
                )
                self.result.before = await self._snapshot()
                try:
                    async with asyncio.timeout(240):
                        for index, step in enumerate(case.steps):
                            await self._step(step, index)
                except Exception as error:
                    self.result.error = error_code(error)
                self.result.after = await self._snapshot()
                self.result.verdict = verify(
                    case,
                    self.result.before,
                    self.result.after,
                    self.result.checkpoints,
                    self.result.calls,
                    completed=self.result.error is None,
                )
        except Exception as error:
            self.result.error = type(error).__name__
            self.result.verdict.check(
                "environment",
                False,
                expected="isolated_environment_completed",
                actual=self.result.error,
            )
        self.result.simulator_events = self.simulator.events
        self.result.latency_ms = round((perf_counter() - started) * 1000)
        return self.result

    async def _snapshot(self) -> dict[str, Any]:
        async with self.factory() as session:
            return await snapshot(session)

    async def _conversation(self, name: str) -> UUID:
        if name not in self.conversations:
            async with self.factory() as session:
                conversation = await ConversationMemory(session).create(self.context)
                await session.commit()
                self.conversations[name] = conversation.id
        return self.conversations[name]

    async def _step(self, step: Step, index: int) -> None:
        before = await self._snapshot()
        label = f"step:{index}:{step.kind}"
        try:
            if step.kind == "user":
                user_text = await self.simulator.render(
                    step,
                    self.responses.get(step.session, ""),
                    has_action=bool(before["pending_actions"]),
                )
                if user_text is not None:
                    await self._turn(step.session, user_text)
            elif step.kind == "parallel_users":
                names = ["session_a", "session_b"]
                for name in names:
                    await self._conversation(name)
                barrier = asyncio.Barrier(2)
                write_barrier = asyncio.Barrier(2)
                # Separate transactions/connections; wait for BOTH before either runtime starts.
                async with asyncio.TaskGroup() as group:
                    for name in names:
                        group.create_task(self._turn(name, step.text, barrier, write_barrier))
            else:
                async with self.factory() as session:
                    actions = list(await session.scalars(select(PendingAction.id)))
                if not actions:
                    raise ValueError("review_requires_agent_created_action")
                for repeat in range(step.repeat):
                    barrier = asyncio.Barrier(step.parallel)
                    async with asyncio.TaskGroup() as group:
                        for worker in range(step.parallel):
                            group.create_task(self._review(actions, step, barrier, repeat, worker))
        finally:
            after = await self._snapshot()
            self.result.checkpoints.append(
                {
                    "label": label,
                    "actor": "reviewer" if step.kind == "review" else "agent",
                    "before": before,
                    "after": after,
                }
            )

    async def _turn(
        self,
        name: str,
        user_text: str,
        barrier: asyncio.Barrier | None = None,
        write_barrier: asyncio.Barrier | None = None,
    ) -> None:
        conversation_id = await self._conversation(name)
        recording = RecordingProvider(self.provider, write_barrier)
        entry: dict[str, Any] = {
            "actor": "agent",
            "session": name,
            "conversation_id": str(conversation_id),
            "user": user_text,
            "started_at": datetime.now(UTC).isoformat(),
            "model_calls": recording.calls,
        }
        self.result.trajectory.append(entry)
        async with self.factory() as session:
            conversation = await session.get(Conversation, conversation_id)
            assert conversation is not None
            if barrier:
                entry["backend_pid"] = await session.scalar(text("SELECT pg_backend_pid()"))
                await barrier.wait()
            entry["execution_started_at"] = datetime.now(UTC).isoformat()
            trace_id: UUID | None = None
            try:
                turn = await AgentRuntime(
                    session,
                    recording,
                    limits=AgentLimits(total_timeout_seconds=60),
                    model_provider=self.provider_name,
                    model_name=self.model_name,
                ).run(conversation, self.context, user_text)
                trace_id = turn.trace_id
                entry["assistant"] = turn.message.content
                self.responses[name] = turn.message.content
                await session.commit()
            except Exception as error:
                trace_id = getattr(error, "trace_id", None)
                entry["error"] = type(error).__name__
                await session.rollback()
                raise
            finally:
                entry["completed_at"] = datetime.now(UTC).isoformat()
                if trace_id:
                    trace = await session.scalar(
                        select(AgentTrace)
                        .options(selectinload(AgentTrace.events))
                        .where(AgentTrace.id == trace_id)
                    )
                    if trace:
                        entry["trace_id"] = str(trace_id)
                        entry["trace_events"] = [
                            {
                                "index": e.event_index,
                                "type": e.event_type,
                                "name": e.name,
                                "input": e.input_json,
                                "output": e.output_json,
                                "latency_ms": e.latency_ms,
                                "created_at": e.created_at.isoformat(),
                            }
                            for e in trace.events
                        ]
                        self.result.calls.extend(
                            [
                                {
                                    "session": name,
                                    "trace_id": str(trace_id),
                                    "name": e.name,
                                    "arguments": (e.input_json or {}).get("arguments", {}),
                                    "output": e.output_json or {},
                                }
                                for e in trace.events
                                if e.event_type == "tool"
                            ]
                        )
                messages = list(
                    await session.scalars(
                        select(Message)
                        .where(Message.conversation_id == conversation_id)
                        .order_by(Message.sequence)
                    )
                )
                entry["conversation"] = [
                    {"role": m.role, "content": m.content, "tool_calls": m.tool_calls_json}
                    for m in messages
                ]

    async def _review(
        self, actions: list[UUID], step: Step, barrier: asyncio.Barrier, repeat: int, worker: int
    ) -> None:
        entry: dict[str, Any] = {
            "actor": "reviewer",
            "decision": step.decision,
            "repeat": repeat,
            "worker": worker,
            "started_at": datetime.now(UTC).isoformat(),
        }
        self.result.trajectory.append(entry)
        async with self.factory() as session:
            if self.source.dialect.name == "postgresql":
                entry["backend_pid"] = await session.scalar(text("SELECT pg_backend_pid()"))
            await barrier.wait()
            entry["execution_started_at"] = datetime.now(UTC).isoformat()
            service = ApprovalService(
                session,
                ApprovalContext(
                    tenant_id=self.context.tenant_id,
                    store_id=self.context.store_id,
                    approver_id=f"evaluation-reviewer-{worker}",
                ),
            )
            try:
                entry["actions"] = []
                for action_id in actions:
                    action = (
                        await service.approve(action_id)
                        if step.decision == "approve"
                        else await service.reject(action_id, "评测：人工拒绝")
                    )
                    entry["actions"].append({"id": str(action.id), "status": action.status})
                await session.commit()
            except Exception as error:
                entry["error"] = type(error).__name__
                raise
            finally:
                entry["completed_at"] = datetime.now(UTC).isoformat()
