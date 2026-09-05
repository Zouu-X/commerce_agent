"""Small, author-reviewed task contracts. Hidden expectations never enter Agent prompts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DATASET_VERSION = "commerce-scenarios-v2"
DATASET_PATH = Path(__file__).parent / "data" / "scenarios_v2.jsonl"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolExpectation(Contract):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=lambda: {"ok": True})
    session: str | None = None


class Step(Contract):
    kind: Literal["user", "parallel_users", "review"] = "user"
    session: str = "customer"
    text: str = ""
    # Equivalent natural-language realizations of one immutable user speech act.
    expressions: list[str] = Field(default_factory=list)
    when_asked: Literal["order", "amount", "confirmation"] | None = None
    optional: bool = False
    decision: Literal["approve", "reject"] = "approve"
    repeat: int = Field(default=1, ge=1, le=3)
    parallel: int = Field(default=1, ge=1, le=2)

    @model_validator(mode="after")
    def valid_step(self) -> Step:
        if self.kind != "review" and not self.text:
            raise ValueError("user_step_requires_text")
        if self.when_asked and self.kind != "user":
            raise ValueError("clarification_requires_user_step")
        if self.optional and not self.when_asked:
            raise ValueError("optional_step_requires_guard")
        if self.expressions and not self.when_asked:
            raise ValueError("expressions_are_for_guarded_clarifications")
        return self


class Scenario(Contract):
    case_id: str
    category: str
    description: str
    tenant_key: Literal["aurora", "harbor"] = "aurora"
    customer_index: int = Field(default=0, ge=0, le=5)
    steps: list[Step] = Field(min_length=1, max_length=8)
    tools: list[ToolExpectation] = Field(default_factory=list)
    any_tools: list[list[ToolExpectation]] = Field(default_factory=list)
    allowed_errors: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    # Exact cardinality + subset row matching; all unspecified tables must stay unchanged.
    final_rows: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    # Order changes are patches to the seed snapshot; all other orders must stay unchanged.
    order_updates: dict[str, dict[str, Any]] = Field(default_factory=dict)
    required_sources: list[str] = Field(default_factory=list)
    forbidden_sources: list[str] = Field(default_factory=list)

    @property
    def requires_postgres(self) -> bool:
        return any(s.kind == "parallel_users" or s.parallel > 1 for s in self.steps)

    @model_validator(mode="after")
    def validate_contract(self) -> Scenario:
        allowed = {"pending_actions", "refund_transactions", "coupon_grants"}
        if set(self.final_rows) - allowed:
            raise ValueError("unsupported_mutable_table")
        if any(set(patch) - {"status", "payment_status"} for patch in self.order_updates.values()):
            raise ValueError("unsupported_order_update")
        if any(not group for group in self.any_tools):
            raise ValueError("empty_tool_alternative_group")
        if not self.tools and not self.any_tools and not self.required_sources:
            raise ValueError("scenario_needs_observable_tool_or_source_expectation")
        for i, step in enumerate(self.steps):
            if step.when_asked and not any(
                p.kind == "user" and p.session == step.session for p in self.steps[:i]
            ):
                raise ValueError("clarification_needs_previous_turn_in_same_session")
        return self


def load_scenarios(path: Path = DATASET_PATH) -> list[Scenario]:
    cases = [
        Scenario.model_validate_json(line) for line in path.read_text().splitlines() if line.strip()
    ]
    if not cases or len({c.case_id for c in cases}) != len(cases):
        raise ValueError("empty_or_duplicate_scenario_ids")
    return cases


def dataset_json(cases: list[Scenario]) -> str:
    return json.dumps(
        [c.model_dump(mode="json") for c in cases],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
