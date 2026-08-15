from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    category: str
    tenant_key: str = "aurora"
    customer_index: int = Field(default=0, ge=0, le=5)
    input: str
    after_sale_index: int | None = Field(default=None, ge=0)
    after_sale_tenant_key: str | None = None
    expected_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    allowed_error_codes: list[str] = Field(default_factory=list)
    must_contain: list[str] = Field(default_factory=list)
    must_not_contain: list[str] = Field(default_factory=list)
    requires_citation: bool = False
    expected_citations: list[str] = Field(default_factory=list)
    requires_pending_action: bool = False
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def template_has_value(self) -> EvaluationCase:
        if "{after_sale_id}" in self.input and self.after_sale_index is None:
            raise ValueError("after_sale_index is required for after_sale_id template")
        if self.expected_citations and not self.requires_citation:
            raise ValueError("expected_citations requires requires_citation=true")
        return self


def load_dataset() -> list[EvaluationCase]:
    path = Path(__file__).parent / "data" / "milestone5.jsonl"
    cases = [
        EvaluationCase.model_validate(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    case_ids = [case.case_id for case in cases]
    if len(cases) < 50 or len(cases) > 100:
        raise ValueError("evaluation_dataset_must_have_50_to_100_cases")
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("duplicate_evaluation_case_id")
    return cases
