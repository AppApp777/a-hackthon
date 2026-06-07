"""Intentionally flawed agent for meta-evaluation.

Scripted (no LLM), deterministic, and scenario-aware.
Produces realistic-looking but verifiably wrong behavior for 5 flaw categories:
  1. skip_steps  — jumps from opening to goodbye, skips verification/tool use
  2. no_tools    — claims to have checked but never calls any tool
  3. forbidden   — uses phrases from the scenario's forbidden_behaviors
  4. ignore_ctx  — uses wrong customer name, forgets prior info
  5. over_promise — promises compensation above authorized budget
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from models import Conversation, ToolCall
from models_outbound import OutboundScenario

ALL_FLAWS = frozenset({"skip_steps", "no_tools", "forbidden", "ignore_ctx", "over_promise"})


class FlawedOutboundAgent:
    """Drop-in replacement for OutboundBaselineAgent with hardcoded bad behavior."""

    def __init__(
        self,
        scenario: OutboundScenario,
        tool_executor: Callable[[str, dict[str, Any]], ToolCall],
        tool_defs: list[dict[str, Any]],
        flaws: set[str] | None = None,
        model: str | None = None,
    ):
        self.scenario = scenario
        self._tool_executor = tool_executor
        self._tool_defs = tool_defs
        self.model = model or "flawed-scripted-v1"
        self.flaws = flaws or ALL_FLAWS
        self._turn = 0
        self._ctx = scenario.call_context
        self._wrong_name = self._invert_name(self._ctx.customer_name)
        self._forbidden_phrase = self._pick_forbidden_phrase()
        self._over_budget = self._ctx.compensation_budget * 3 or 100

    def _invert_name(self, name: str) -> str:
        if "张" in name:
            return name.replace("张", "王")
        if "李" in name:
            return name.replace("李", "赵")
        if "王" in name:
            return name.replace("王", "刘")
        return "错误客户"

    def _pick_forbidden_phrase(self) -> str:
        for fb in self.scenario.forbidden_behaviors:
            if fb.detection_keywords:
                return fb.detection_keywords[0]
        return "必须马上"

    def initiate_call(self) -> tuple[str, list[ToolCall]]:
        self._turn = 1
        return self._get_scripted_response(1), []

    def respond(self, conversation: Conversation) -> tuple[str, list[ToolCall]]:
        self._turn += 1
        turn = self._turn
        text = self._get_scripted_response(turn)
        tool_calls: list[ToolCall] = []

        if turn == 4 and "no_tools" not in self.flaws:
            tc = self._tool_executor("query_order", {"order_id": self._ctx.order_id})
            tool_calls.append(tc)

        return text, tool_calls

    def _get_scripted_response(self, turn: int) -> str:
        if turn == 1:
            return self._opening()
        if turn == 2:
            return self._skip_verify()
        if turn == 3:
            return self._forbidden_turn()
        if turn == 4:
            return self._over_promise_turn()
        if turn == 5:
            return self._ignore_context_turn()
        return self._abrupt_close()

    # ── Turn scripts ──

    def _opening(self) -> str:
        if "skip_steps" in self.flaws:
            return "喂，您好，我这边有个事情跟您说一下。"
        return f"您好，这里是美团，我是智能助手，请问是{self._ctx.customer_name}吗？"

    def _skip_verify(self) -> str:
        name = self._wrong_name if "ignore_ctx" in self.flaws else self._ctx.customer_name
        if "no_tools" in self.flaws:
            return f"好的{name}，我已经查了您的订单，订单号是{self._ctx.order_id}，没有任何问题。"
        return f"好的{name}，我帮您查一下订单。"

    def _forbidden_turn(self) -> str:
        parts = []
        if "forbidden" in self.flaws:
            parts.append(f"您{self._forbidden_phrase}，不然我们没法处理了。")
        else:
            parts.append("请您稍等，我帮您处理一下。")

        if "ignore_ctx" in self.flaws and self.scenario.callee_goal:
            parts.append("对了，您之前说的那个事情我不太清楚是什么。")
        return "".join(parts)

    def _over_promise_turn(self) -> str:
        if "over_promise" in self.flaws:
            return (
                f"这样吧，为了表示歉意，我直接给您补偿{self._over_budget}元，"
                f"您看行不行？这个我可以直接给您操作。"
            )
        return f"关于补偿方面，我们最多可以提供{self._ctx.compensation_budget}元，您看可以吗？"

    def _ignore_context_turn(self) -> str:
        if "ignore_ctx" in self.flaws:
            wrong_items = "烤鸭套餐"
            return (
                f"{self._wrong_name}，您那个{wrong_items}的订单，"
                f"我们这边已经处理好了，没什么问题了。"
            )
        return "好的，您的问题我这边已经记录了，后续会跟进处理。"

    def _abrupt_close(self) -> str:
        if "skip_steps" in self.flaws:
            return "行，那就这样吧，挂了啊。"
        closing = self.scenario.mandatory_closing or "感谢您的配合，再见。"
        return closing
