"""User FSM controls disclosure; an optional LLM realizes only an authorized speech act.

A finite language avoids silently changing consent, intent, IDs or money. The model
may choose one of the author-reviewed paraphrases; it cannot invent a next action.
This is a user simulator, never a judge. Invalid output fails the harness explicitly.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from app.agent.provider import ModelProvider
from app.agent.types import ProviderMessage
from app.evaluations.scenarios import Step

SIMULATOR_VERSION = "guarded-finite-language-v1"
_SLOT_PATTERNS = {
    "order": r"订单号|订单编号|哪[个一笔张]+订单|哪[一笔张]+|订单.*(?:号码|编号)",
    "confirmation": r"确认.*(?:取消|退款|提交|申请)|是否.*(?:取消|退款|提交|申请)",
    "amount": r"金额|多少.*(?:元|钱)|几元|面额",
}
_QUESTION_PATTERN = r"[？?]|请.*(?:提供|告诉|说明|确认|问|选择)|需要.*(?:提供|确认)|麻烦|希望.*多少"


def asked_for(response: str, slot: str) -> bool:
    # This is an explicit Chinese slot-request contract, not a language-quality score.
    return bool(
        re.search(_SLOT_PATTERNS[slot], response) and re.search(_QUESTION_PATTERN, response)
    )


class UserSimulator:
    def __init__(self, provider: ModelProvider | None) -> None:
        self.provider = provider
        self.events: list[dict[str, Any]] = []

    async def render(self, step: Step, previous: str, *, has_action: bool) -> str | None:
        if step.when_asked:
            permitted = not has_action and asked_for(previous, step.when_asked)
            self.events.append(
                {
                    "state": "awaiting_clarification",
                    "slot": step.when_asked,
                    "guard_passed": permitted,
                    "session": step.session,
                }
            )
            if not permitted and step.optional:
                self.events.append({"state": "optional_reply_skipped", "slot": step.when_asked})
                return None
            if not permitted:
                raise ValueError("clarification_guard_failed")
        if self.provider is None or not step.expressions:
            text = step.text
            self.events.append({"state": "user_reply", "mode": "template", "text": text})
            return text
        choices = [step.text, *step.expressions]
        prompt = (
            "你只负责用户回复的自然语言表达。请从下列语义相同的句子中任选一句，"
            "逐字输出这句话，不加引号或说明，不修改金额、订单号或意图。\n" + "\n".join(choices)
        )
        started = perf_counter()
        started_at = datetime.now(UTC).isoformat()
        async with asyncio.timeout(30):
            response = await self.provider.complete(
                [ProviderMessage(role="system", content=prompt)],
                [],
                timeout_seconds=30,
            )
        text = response.content.strip()
        self.events.append(
            {
                "state": "user_reply",
                "started_at": started_at,
                "latency_ms": round((perf_counter() - started) * 1000),
                "mode": "llm",
                "text": text,
                "accepted": text in choices and not response.tool_calls,
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            }
        )
        if text not in choices or response.tool_calls:
            raise ValueError("simulator_expression_outside_contract")
        return text
