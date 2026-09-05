"""V2 entrypoint: score committed outcomes, retain telemetry separately."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import selectinload

from app.agent.prompts import PROMPT_VERSION, SYSTEM_PROMPT
from app.agent.provider import ModelProvider
from app.commerce.seed import BASE_TIME
from app.evaluations.harness import ScenarioHarness
from app.evaluations.scenarios import DATASET_VERSION, Scenario, dataset_json, load_scenarios
from app.evaluations.simulator import SIMULATOR_VERSION
from app.models import EvaluationCaseResult, EvaluationRun
from app.observability.service import estimate_cost


@dataclass(frozen=True)
class EvaluationSettings:
    provider_name: str
    model_name: str
    input_cost_per_million: Decimal = Decimal("0")
    output_cost_per_million: Decimal = Decimal("0")
    dataset_name: str = "commerce-agent-scenarios"
    dataset_version: str = DATASET_VERSION
    simulator_mode: Literal["auto", "template", "llm"] = "auto"


def source_hash() -> str:
    digest = hashlib.sha256()
    root = Path(__file__).resolve().parents[1]
    for path in sorted(root.rglob("*.py")):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


class EvaluationService:
    def __init__(
        self, session: AsyncSession, provider: ModelProvider, settings: EvaluationSettings
    ) -> None:
        self.session = session
        self.provider = provider
        self.settings = settings

    async def run(self, cases: list[Scenario] | None = None) -> EvaluationRun:
        dataset = load_scenarios() if cases is None else cases
        if not dataset or len({c.case_id for c in dataset}) != len(dataset):
            raise ValueError("empty_or_duplicate_scenario_ids")
        source = self.session.bind
        if not isinstance(source, AsyncEngine):
            raise ValueError("evaluation_requires_async_engine")
        simulator_mode = self.settings.simulator_mode
        if simulator_mode == "auto":
            simulator_mode = "template" if self.settings.provider_name == "mock" else "llm"
        from app.core.config import get_settings

        configuration = get_settings()
        manifest = {
            "schema_version": "2.0",
            "dataset_version": DATASET_VERSION,
            "dataset_sha256": hashlib.sha256(dataset_json(dataset).encode()).hexdigest(),
            "application_sha256": source_hash(),
            "prompt_version": PROMPT_VERSION,
            "prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
            "provider": self.settings.provider_name,
            "model": self.settings.model_name,
            "model_mode": "non-thinking"
            if self.settings.provider_name == "deepseek"
            else "default",
            "simulator_mode": simulator_mode,
            "simulator_version": SIMULATOR_VERSION,
            "simulator_model": self.settings.model_name if simulator_mode == "llm" else None,
            "database": source.dialect.name,
            "isolation": "fresh-schema-or-file-per-case",
            "seed_base_time": BASE_TIME.isoformat(),
            "seed_embedding": configuration.embedding_provider,
            "query_embedding": configuration.embedding_provider,
            "query_embedding_model": configuration.embedding_model,
            "limits": {
                "case_timeout_seconds": 240,
                "turn_timeout_seconds": 60,
                "max_model_loops": 6,
                "max_tool_calls": 8,
            },
            "input_cost_per_million": str(self.settings.input_cost_per_million),
            "output_cost_per_million": str(self.settings.output_cost_per_million),
            "judge": None,
            "aggregation": "all_required_checks_per_case",
            "case_ids": [c.case_id for c in dataset],
        }
        run = EvaluationRun(
            status="running",
            dataset_name=self.settings.dataset_name,
            dataset_version=self.settings.dataset_version,
            provider=self.settings.provider_name,
            model_name=self.settings.model_name,
            prompt_version=PROMPT_VERSION,
            total_cases=len(dataset),
            passed_cases=0,
            metrics_json={"manifest": manifest},
            started_at=datetime.now(UTC),
        )
        self.session.add(run)
        await self.session.commit()
        run_id = run.id
        # Plain data survives rollback/commit; never aggregate expired ORM instances.
        summaries: list[dict[str, Any]] = []
        for index, case in enumerate(dataset, 1):
            attempt = await ScenarioHarness(
                source,
                self.provider,
                provider_name=self.settings.provider_name,
                model_name=self.settings.model_name,
                simulator_provider=self.provider if simulator_mode == "llm" else None,
            ).run(case)
            telemetry = attempt.telemetry
            cost = estimate_cost(
                telemetry["agent_input_tokens"] + telemetry["simulator_input_tokens"],
                telemetry["agent_output_tokens"] + telemetry["simulator_output_tokens"],
                input_cost_per_million=self.settings.input_cost_per_million,
                output_cost_per_million=self.settings.output_cost_per_million,
            )
            telemetry["estimated_cost_usd"] = str(cost)
            result = EvaluationCaseResult(
                run_id=run_id,
                case_index=index,
                case_id=case.case_id,
                category=case.category,
                input_text=case.steps[0].text,
                passed=attempt.verdict.passed,
                latency_ms=attempt.latency_ms,
                estimated_cost_usd=cost,
                actual_tools_json=[c["name"] for c in attempt.calls],
                checks_json=attempt.verdict.checks,
                failures_json=[k for k, passed in attempt.verdict.checks.items() if not passed],
                evidence_json={
                    "contract": case.model_dump(mode="json"),
                    "checks": attempt.verdict.evidence,
                    "trajectory": attempt.trajectory,
                    "user_simulator": attempt.simulator_events,
                    "before": attempt.before,
                    "after": attempt.after,
                    "checkpoints": [
                        {
                            "label": point["label"],
                            "actor": point["actor"],
                            "changes": {
                                table: {
                                    "before": point["before"][table],
                                    "after": point["after"][table],
                                }
                                for table in point["before"]
                                if point["before"][table] != point["after"][table]
                            },
                        }
                        for point in attempt.checkpoints
                    ],
                    "telemetry": telemetry,
                    "error": attempt.error,
                },
                response_preview=next(
                    (
                        t.get("assistant", "")
                        for t in reversed(attempt.trajectory)
                        if t.get("actor") == "agent"
                    ),
                    "",
                )[:1000],
            )
            self.session.add(result)
            await self.session.commit()
            summaries.append(
                {
                    "passed": attempt.verdict.passed,
                    "error": attempt.error,
                    "checks": attempt.verdict.checks,
                    "category": case.category,
                    "telemetry": telemetry,
                }
            )
        loaded_run = await self.session.get(EvaluationRun, run_id, populate_existing=True)
        assert loaded_run is not None
        run = loaded_run
        run.passed_cases = sum(r["passed"] for r in summaries)
        errors = sum(r["error"] is not None for r in summaries)
        run.status = "failed" if errors else "succeeded"
        run.error_message = f"{errors} scenario(s) did not complete" if errors else None
        run.completed_at = datetime.now(UTC)
        run.metrics_json = {"manifest": manifest, **calculate_metrics(summaries)}
        await self.session.commit()
        statement = (
            select(EvaluationRun)
            .where(EvaluationRun.id == run_id)
            .options(selectinload(EvaluationRun.cases).selectinload(EvaluationCaseResult.trace))
        )
        loaded = await self.session.scalar(statement)
        assert loaded is not None
        return loaded


def calculate_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(results)
    checks = sorted({k for r in results for k in r["checks"]})
    rates = {}
    for check in checks:
        applicable = [r for r in results if check in r["checks"]]
        rates[check] = {
            "passed": sum(r["checks"][check] for r in applicable),
            "applicable": len(applicable),
        }
    categories = {}
    for category in sorted({r["category"] for r in results}):
        group = [r for r in results if r["category"] == category]
        categories[category] = {"passed": sum(r["passed"] for r in group), "total": len(group)}
    latencies = sorted(r["telemetry"]["wall_time_ms"] for r in results)
    telemetry = {
        k: sum(r["telemetry"].get(k, 0) for r in results)
        for k in (
            "unreported_agent_usage_calls",
            "agent_input_tokens",
            "agent_output_tokens",
            "simulator_input_tokens",
            "simulator_output_tokens",
            "model_calls",
            "tool_calls",
        )
    }
    telemetry["p95_latency_ms"] = latencies[math.ceil(count * 0.95) - 1] if count else 0
    telemetry["total_estimated_cost_usd"] = str(
        sum((Decimal(r["telemetry"]["estimated_cost_usd"]) for r in results), Decimal(0))
    )
    return {
        "pass_rate": sum(r["passed"] for r in results) / count if count else None,
        "quality_gate_passed": all(r["passed"] for r in results) if count else None,
        "execution_success_rate": sum(r["error"] is None for r in results) / count
        if count
        else None,
        "checks": rates,
        "categories": categories,
        "telemetry": telemetry,
    }
