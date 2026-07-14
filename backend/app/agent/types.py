from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]

    def to_storage_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass(frozen=True)
class ProviderMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass(frozen=True)
class ModelUsage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class ModelResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: ModelUsage = field(default_factory=ModelUsage)
