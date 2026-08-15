from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from app.api.dependencies import get_model_provider
from app.core.config import get_settings
from app.db.session import SessionFactory
from app.evaluations.runner import EvaluationService, EvaluationSettings
from app.models import EvaluationRun


def report_payload(run: EvaluationRun) -> dict[str, Any]:
    return {
        "schema_version": "1.1",
        "run": {
            "id": str(run.id),
            "status": run.status,
            "dataset": {"name": run.dataset_name, "version": run.dataset_version},
            "provider": run.provider,
            "model": run.model_name,
            "prompt_version": run.prompt_version,
            "started_at": run.started_at.isoformat(),
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "total_cases": run.total_cases,
            "passed_cases": run.passed_cases,
        },
        "metrics": run.metrics_json,
        "cases": [
            {
                "case_index": result.case_index,
                "case_id": result.case_id,
                "category": result.category,
                "passed": result.passed,
                "trace_id": str(result.trace_id) if result.trace_id else None,
                "latency_ms": result.latency_ms,
                "estimated_cost_usd": str(result.estimated_cost_usd),
                "actual_tools": result.actual_tools_json,
                "checks": result.checks_json,
                "failures": result.failures_json,
                "evidence": result.evidence_json,
            }
            for result in run.cases
        ],
    }


async def async_main() -> None:
    parser = argparse.ArgumentParser(description="Run the Commerce Agent offline evaluation")
    parser.add_argument("--output-dir", default="eval-results")
    args = parser.parse_args()
    settings = get_settings()
    provider = await get_model_provider()
    async with SessionFactory() as session:
        run = await EvaluationService(
            session,
            provider,
            EvaluationSettings(
                provider_name=settings.model_provider,
                model_name=settings.model_name,
                input_cost_per_million=settings.model_input_cost_per_million,
                output_cost_per_million=settings.model_output_cost_per_million,
            ),
        ).run()
    payload = report_payload(run)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    report_path = output_dir / f"evaluation-{run.id}.json"
    report_path.write_text(serialized + "\n", encoding="utf-8")
    (output_dir / "latest.json").write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    print(f"report={report_path}")


if __name__ == "__main__":
    asyncio.run(async_main())
