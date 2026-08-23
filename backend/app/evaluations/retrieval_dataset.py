from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RetrievalGoldCase(BaseModel):
    """A human-reviewed query-to-document relevance judgment."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    split: Literal["dev", "holdout"]
    query: str = Field(min_length=1, max_length=500)
    tenant_key: Literal["aurora", "harbor"] = "aurora"
    document_type: Literal["policy", "product_guide", "security_guide"] | None = None
    as_of: datetime | None = None
    relevance: dict[str, int] = Field(default_factory=dict)
    expected_intents: list[str] = Field(default_factory=list)
    negative_sources: list[str] = Field(default_factory=list)
    forbidden_sources: list[str] = Field(default_factory=list)
    must_contain: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    annotation_reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_judgments(self) -> RetrievalGoldCase:
        if any(grade < 1 or grade > 3 for grade in self.relevance.values()):
            raise ValueError("relevance grades must be between 1 and 3")
        judged_relevant = set(self.relevance)
        judged_negative = set(self.negative_sources)
        judged_forbidden = set(self.forbidden_sources)
        if judged_relevant & judged_negative:
            raise ValueError("relevant and negative sources must not overlap")
        if judged_relevant & judged_forbidden:
            raise ValueError("relevant and forbidden sources must not overlap")
        if not self.relevance and "no_answer" not in self.tags:
            raise ValueError("cases without relevant sources require the no_answer tag")
        if self.relevance and "no_answer" in self.tags:
            raise ValueError("no_answer cases cannot contain relevant sources")
        if "multi_intent" in self.tags and len(self.expected_intents) < 2:
            raise ValueError("multi_intent cases require at least two expected intents")
        if "multi_intent" not in self.tags and self.expected_intents:
            raise ValueError("expected intents are reserved for multi_intent cases")
        if len(self.expected_intents) != len(set(self.expected_intents)):
            raise ValueError("expected intents must be unique")
        return self


def load_retrieval_gold_set(
    *,
    split: Literal["dev", "holdout"] | None = None,
    path: Path | None = None,
) -> list[RetrievalGoldCase]:
    dataset_path = path or Path(__file__).parent / "data" / "retrieval_gold_v1.jsonl"
    cases = [
        RetrievalGoldCase.model_validate(json.loads(line))
        for line in dataset_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    case_ids = [case.case_id for case in cases]
    if len(cases) < 80 or len(cases) > 120:
        raise ValueError("retrieval_gold_set_must_have_80_to_120_cases")
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("duplicate_retrieval_gold_case_id")
    if {case.split for case in cases} != {"dev", "holdout"}:
        raise ValueError("retrieval_gold_set_requires_dev_and_holdout_splits")
    if split is not None:
        return [case for case in cases if case.split == split]
    return cases
