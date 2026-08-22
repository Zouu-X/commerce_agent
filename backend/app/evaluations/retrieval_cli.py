from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from app.db.session import SessionFactory
from app.evaluations.retrieval_dataset import load_retrieval_gold_set
from app.evaluations.retrieval_runner import RetrievalEvaluationService


async def async_main() -> None:
    parser = argparse.ArgumentParser(description="Run the standalone RAG retrieval evaluation")
    parser.add_argument("--output-dir", default="eval-results")
    parser.add_argument("--split", choices=("all", "dev", "holdout"), default="all")
    args = parser.parse_args()
    split = None if args.split == "all" else cast(Literal["dev", "holdout"], args.split)
    cases = load_retrieval_gold_set(split=split)
    async with SessionFactory() as session:
        run = await RetrievalEvaluationService(session).run(cases)

    payload = run.to_payload()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report_path = output_dir / f"retrieval-evaluation-{timestamp}.json"
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    report_path.write_text(serialized + "\n", encoding="utf-8")
    (output_dir / "retrieval-latest.json").write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    print(f"report={report_path}")


if __name__ == "__main__":
    asyncio.run(async_main())
