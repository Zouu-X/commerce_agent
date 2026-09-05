from __future__ import annotations

import copy
import json
import os
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.agent.errors import ModelProviderError
from app.agent.provider import MockCommerceProvider
from app.agent.types import ModelResponse, ModelUsage, ProviderMessage, ToolCall, ToolSpec
from app.evaluations.environment import isolated_environment
from app.evaluations.harness import ScenarioHarness
from app.evaluations.runner import EvaluationService, EvaluationSettings, calculate_metrics
from app.evaluations.scenarios import Step, load_scenarios
from app.evaluations.simulator import UserSimulator, asked_for
from app.evaluations.verifier import snapshot, subset, verify
from app.models import PendingAction


def scenario(case_id: str) -> Any:
    return next(c for c in load_scenarios() if c.case_id == case_id)


def engine_of(session: AsyncSession) -> AsyncEngine:
    assert isinstance(session.bind, AsyncEngine)
    return session.bind


class DialogueProvider(MockCommerceProvider):
    """Test fixture, not the provider scored by eval-mock."""

    async def complete(
        self, messages: list[ProviderMessage], tools: list[ToolSpec], *, timeout_seconds: float
    ) -> ModelResponse:
        if messages[-1].role == "tool":
            return await super().complete(messages, tools, timeout_seconds=timeout_seconds)
        users = [m.content for m in messages if m.role == "user"]
        if len(users) == 1:
            return ModelResponse(content="请提供您想取消的订单号？")
        assert users[0] == "我想取消订单，不想要了"
        assert "AUR-202607-0001" in users[-1]
        return ModelResponse(
            tool_calls=[
                ToolCall(
                    "cancel",
                    "request_order_cancellation",
                    {"order_number": "AUR-202607-0001", "reason": "不想要了"},
                )
            ],
            usage=ModelUsage(12, 4),
        )


class FailingProvider:
    async def complete(
        self, messages: list[ProviderMessage], tools: list[ToolSpec], *, timeout_seconds: float
    ) -> ModelResponse:
        raise ModelProviderError("test_provider_failure")


class ExpressionProvider:
    def __init__(self, text: str) -> None:
        self.text = text

    async def complete(
        self, messages: list[ProviderMessage], tools: list[ToolSpec], *, timeout_seconds: float
    ) -> ModelResponse:
        assert tools == []
        assert len(messages) == 1  # No verifier / DB / future task state is disclosed.
        return ModelResponse(content=self.text, usage=ModelUsage(7, 2))


def test_contract_coverage_and_money_normalization() -> None:
    cases = load_scenarios()
    assert len({c.case_id for c in cases}) == len(cases)
    assert {"dialogue", "concurrency", "idempotency", "security", "boundary"} <= {
        c.category for c in cases
    }
    assert subset({"amount": "20.00"}, {"amount": 20})
    assert not subset({"amount": "20.00"}, {"amount": True})
    assert not subset({"order_number": "AUR-202607-0001"}, {"order_number": "other"})


@pytest.mark.anyio
async def test_multiturn_uses_history_and_executes_only_after_clarification(
    db_session: AsyncSession,
) -> None:
    before = await snapshot(db_session)
    result = await ScenarioHarness(
        engine_of(db_session), DialogueProvider(), provider_name="scripted", model_name="fixture"
    ).run(scenario("cancel_clarify_order"))
    assert result.verdict.passed, result.error
    turns = [t for t in result.trajectory if t["actor"] == "agent"]
    assert len(turns) == 2
    assert turns[0]["conversation_id"] == turns[1]["conversation_id"]
    assert result.checkpoints[0]["after"]["pending_actions"] == []
    assert result.after["pending_actions"][0]["status"] == "succeeded"
    assert any(e.get("guard_passed") for e in result.simulator_events)
    assert before == await snapshot(db_session)  # Report/demo DB is never seeded/reset.


@pytest.mark.anyio
async def test_verifier_rejects_wrong_state_fabrication_and_collateral_changes(
    db_session: AsyncSession,
) -> None:
    case = scenario("coupon_repeat_approval")
    result = await ScenarioHarness(
        engine_of(db_session), MockCommerceProvider(), provider_name="mock", model_name="mock"
    ).run(case)
    assert result.verdict.passed
    for mutate in ("duplicate", "wrong_amount", "missing_action", "other_order", "bad_audit"):
        after = copy.deepcopy(result.after)
        if mutate == "duplicate":
            after["coupon_grants"].append(copy.deepcopy(after["coupon_grants"][0]))
        elif mutate == "wrong_amount":
            after["coupon_grants"][0]["amount"] = "50.00"
        elif mutate == "missing_action":
            after["pending_actions"] = []
        elif mutate == "other_order":
            after["orders"][-1]["status"] = "cancelled"
        else:
            after["action_audit_logs"] = []
        verdict = verify(
            case, result.before, after, result.checkpoints, result.calls, completed=True
        )
        assert not verdict.passed, mutate
    # Tool output cannot substitute for an actually persisted request.
    assert not verify(case, result.before, result.before, [], result.calls, completed=True).passed
    # Correct final DB cannot hide a tool request with the wrong amount.
    calls = copy.deepcopy(result.calls)
    calls[0]["arguments"]["amount"] = "50"
    assert not verify(
        case, result.before, result.after, result.checkpoints, calls, completed=True
    ).passed


@pytest.mark.anyio
async def test_extra_read_and_equivalent_argument_format_do_not_fail(
    db_session: AsyncSession,
) -> None:
    case = scenario("coupon_repeat_approval")
    result = await ScenarioHarness(
        engine_of(db_session), MockCommerceProvider(), provider_name="mock", model_name="mock"
    ).run(case)
    calls = copy.deepcopy(result.calls)
    calls[0]["arguments"]["amount"] = 5.0
    calls.insert(
        0,
        {
            "session": "customer",
            "name": "get_order_details",
            "arguments": {},
            "output": {"ok": True},
        },
    )
    assert verify(
        case, result.before, result.after, result.checkpoints, calls, completed=True
    ).passed
    checkpoints = copy.deepcopy(result.checkpoints)
    checkpoints[0]["after"]["coupon_grants"] = result.after["coupon_grants"]
    verdict = verify(case, result.before, result.after, checkpoints, calls, completed=True)
    assert not verdict.checks["no_unapproved_writes"]


@pytest.mark.anyio
async def test_simulator_guard_and_llm_cannot_change_authorized_reply() -> None:
    step = Step(text="金额是 10 元。", when_asked="amount", expressions=["10 元就可以。"])
    simulator = UserSimulator(ExpressionProvider("10 元就可以。"))
    assert (
        await simulator.render(step, "请说明希望申请多少金额？", has_action=False)
        == "10 元就可以。"
    )
    assert simulator.events[-1]["input_tokens"] == 7
    for previous, has_action in [("已为您处理完成。", False), ("请提供金额？", True)]:
        with pytest.raises(ValueError, match="clarification_guard_failed"):
            await simulator.render(step, previous, has_action=has_action)
    simulator = UserSimulator(ExpressionProvider("改成 50 元，直接执行吧"))
    with pytest.raises(ValueError, match="simulator_expression_outside_contract"):
        await simulator.render(step, "请提供金额？", has_action=False)
    assert asked_for("请告诉我您希望取消哪一笔订单。", "order")
    assert not asked_for("订单号已经收到了。", "order")


@pytest.mark.anyio
async def test_failure_report_keeps_trajectory_and_continues_next_case(
    db_session: AsyncSession,
) -> None:
    run = await EvaluationService(
        db_session,
        FailingProvider(),
        EvaluationSettings(
            "failing",
            "fixture",
            simulator_mode="template",
        ),
    ).run([scenario("order_read"), scenario("orders_list")])
    assert len(run.cases) == 2
    assert run.status == "failed"
    assert run.passed_cases == 0
    for result in run.cases:
        assert result.evidence_json["trajectory"][0]["model_calls"][0]["error"]
        assert result.evidence_json["trajectory"][0]["trace_events"]
    assert list(await db_session.scalars(select(PendingAction))) == []
    assert run.metrics_json["manifest"]["judge"] is None
    json.dumps(run.cases[0].evidence_json)


def test_time_tokens_and_cost_do_not_affect_quality() -> None:
    result = {
        "passed": True,
        "error": None,
        "checks": {"database:orders": True},
        "category": "write",
        "telemetry": {
            "wall_time_ms": 1,
            "agent_input_tokens": 0,
            "agent_output_tokens": 0,
            "simulator_input_tokens": 0,
            "simulator_output_tokens": 0,
            "model_calls": 1,
            "tool_calls": 1,
            "estimated_cost_usd": "0",
        },
    }
    baseline = calculate_metrics([result])
    result["telemetry"].update(
        wall_time_ms=999999, agent_input_tokens=999999, estimated_cost_usd="1000"
    )
    changed = calculate_metrics([result])
    assert baseline["quality_gate_passed"] is changed["quality_gate_passed"] is True
    assert baseline["checks"] == changed["checks"]
    assert baseline["telemetry"] != changed["telemetry"]


@pytest.mark.anyio
async def test_sqlite_never_claims_postgres_concurrency_pass(db_session: AsyncSession) -> None:
    result = await ScenarioHarness(
        engine_of(db_session), MockCommerceProvider(), provider_name="mock", model_name="mock"
    ).run(scenario("coupon_concurrent_approval"))
    assert not result.verdict.passed
    assert result.error == "postgresql_required_for_concurrency"


@pytest.mark.anyio
async def test_postgres_concurrency_isolated_and_replay_safe() -> None:
    url = os.environ.get("EVAL_TEST_DATABASE_URL")
    if not url:
        pytest.skip("set EVAL_TEST_DATABASE_URL for real PostgreSQL locking regression")
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            original = (
                await connection.execute(text("SELECT count(*) FROM public.tenants"))
            ).scalar()
        for _ in range(2):
            result = await ScenarioHarness(
                engine, MockCommerceProvider(), provider_name="mock", model_name="mock"
            ).run(scenario("coupon_concurrent_approval"))
            assert result.verdict.passed, result.error
            reviewers = [t for t in result.trajectory if t["actor"] == "reviewer"]
            assert len({t["backend_pid"] for t in reviewers}) == 2
            assert max(t["execution_started_at"] for t in reviewers) < min(
                t["completed_at"] for t in reviewers
            )
            assert len(result.after["coupon_grants"]) == 1
        # An exception during use must also clean up the private schema.
        with pytest.raises(RuntimeError):
            async with isolated_environment(engine) as isolated:
                async with isolated.connect() as connection:
                    schema_name = await connection.scalar(text("SELECT current_schema()"))
                raise RuntimeError("fixture cleanup")
        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.schemata WHERE schema_name=:name"
                    ),
                    {"name": schema_name},
                )
                == 0
            )
            assert await connection.scalar(text("SELECT count(*) FROM public.tenants")) == original
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_postgres_forced_request_race_exposes_duplicate_intents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from app.approvals.service import ActionRequestService

    url = os.environ.get("EVAL_TEST_DATABASE_URL")
    if not url:
        pytest.skip("set EVAL_TEST_DATABASE_URL for the forced read-before-write race")
    original = ActionRequestService._find_active_order_action
    barrier = asyncio.Barrier(2)

    async def both_observe_absence(self: Any, kind: str, order: str) -> Any:
        found = await original(self, kind, order)
        if found is None:
            await barrier.wait()
        return found

    monkeypatch.setattr(ActionRequestService, "_find_active_order_action", both_observe_absence)
    case = scenario("cancel_concurrent_sessions").model_copy(deep=True)
    case.steps = case.steps[:1]
    case.order_updates = {}
    case.final_rows["pending_actions"][0]["status"] = "pending"
    engine = create_async_engine(url)
    try:
        result = await ScenarioHarness(
            engine, MockCommerceProvider(), provider_name="mock", model_name="mock"
        ).run(case)
        assert result.error is None, result.error
        assert len(result.after["pending_actions"]) == 2
        assert not result.verdict.checks["database:pending_actions"]
        turns = [t for t in result.trajectory if t["actor"] == "agent"]
        assert len({t["backend_pid"] for t in turns}) == 2
        assert all(any("write_barrier_released_at" in c for c in t["model_calls"]) for t in turns)
    finally:
        await engine.dispose()
