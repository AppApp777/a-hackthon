"""Compile raw Markdown task instruction into an OutboundScenario.

This is the "instruction compiler" that Oracle identified as the top innovation:
paste a Markdown instruction → get an executable evaluation plan.
"""

from __future__ import annotations

import re
import uuid

from models_outbound import (
    Branch,
    CalleePersona,
    ForbiddenBehavior,
    InstructionStep,
    OutboundScenario,
)
from parse_instruction import parse_instruction

_CALLEE_ROLE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"骑手|配送员|快递员"), "骑手"),
    (re.compile(r"商家|店铺|门店|商户|机构"), "商家负责人"),
    (re.compile(r"客户|用户|顾客|消费者"), "客户"),
    (re.compile(r"团长|团购"), "团长"),
    (re.compile(r"司机|车主"), "司机"),
]

_CALLEE_GOAL_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"通知|告知|提醒"), "了解通知内容，确认已知悉"),
    (re.compile(r"确认.*订单|核实"), "确认订单信息无误"),
    (re.compile(r"升级|推销|推荐|营销"), "听完介绍，根据自身情况决定是否接受"),
    (re.compile(r"投诉|不满|差评"), "表达不满，争取满意的解决方案"),
    (re.compile(r"退款|赔偿|补偿"), "争取合理的补偿或退款"),
    (re.compile(r"回访|满意度|评价"), "如实反馈使用体验"),
    (re.compile(r"催单|催促|加急"), "了解进度，催促尽快处理"),
]

_DIFFICULTY_PERSONA: dict[str, dict[str, int]] = {
    "easy": {
        "patience": 7,
        "cooperativeness": 8,
        "trust_level": 7,
        "busy_level": 2,
        "emotional": 2,
    },
    "medium": {
        "patience": 5,
        "cooperativeness": 5,
        "trust_level": 5,
        "busy_level": 4,
        "emotional": 4,
    },
    "hard": {
        "patience": 3,
        "cooperativeness": 3,
        "trust_level": 3,
        "busy_level": 7,
        "emotional": 6,
    },
}


def compile_instruction(
    raw_instruction: str,
    scenario_name: str = "",
    callee_role: str = "",
    callee_goal: str = "",
    difficulty: str = "medium",
) -> OutboundScenario:
    """Compile a raw Markdown instruction into a runnable OutboundScenario."""
    parsed = parse_instruction(raw_instruction)

    flow_steps = parsed["call_flow_steps"] or parsed["conversation_flow_steps"]
    instruction_steps = _build_steps(flow_steps)

    constraints = parsed["constraints"]
    forbidden_behaviors = _extract_forbidden(constraints)

    opening_line = parsed["opening_line"]
    task_text = parsed["task"]
    role_text = parsed.get("role", "")

    name = scenario_name or task_text[:40] or "编译场景"

    inferred_role = callee_role or _infer_callee_role(role_text, task_text)
    inferred_goal = callee_goal or _infer_callee_goal(task_text, raw_instruction)
    callee_context = _infer_callee_context(role_text, task_text, raw_instruction)
    persona = _build_persona(difficulty)

    expected_result = _infer_call_result(task_text, raw_instruction)

    return OutboundScenario(
        id=str(uuid.uuid4())[:8],
        name=name,
        domain="outbound_call",
        call_type="compiled",
        difficulty=difficulty,
        description=task_text,
        call_purpose=task_text,
        raw_instruction=raw_instruction,
        instruction_steps=instruction_steps,
        mandatory_opening=opening_line,
        forbidden_behaviors=forbidden_behaviors,
        knowledge_points=parsed["knowledge_points"],
        response_length_limit=parsed["response_length_limit"],
        callee_role=inferred_role,
        callee_goal=inferred_goal,
        callee_context=callee_context,
        callee_persona=persona,
        max_turns=max(15, len(instruction_steps) * 3),
        expected_steps_completed=[s.step_id for s in instruction_steps if not s.is_optional],
        expected_call_result=expected_result,
        must_call_tools=["log_call_result"],
    )


def _infer_callee_role(role_text: str, task_text: str) -> str:
    combined = role_text + " " + task_text
    for pattern, role in _CALLEE_ROLE_PATTERNS:
        if pattern.search(combined):
            return role
    return "接电话的用户"


def _infer_callee_goal(task_text: str, raw: str) -> str:
    combined = task_text + " " + raw[:500]
    for pattern, goal in _CALLEE_GOAL_PATTERNS:
        if pattern.search(combined):
            return goal
    return "配合对方完成通话"


def _infer_callee_context(role_text: str, task_text: str, raw: str) -> str:
    parts = []
    if role_text:
        parts.append(f"对方自称是{role_text}")
    if task_text:
        parts.append(f"来电目的似乎是：{task_text[:100]}")
    parts.append("你事先不知道对方要说什么，需要从对话中了解情况")
    return "\n".join(f"- {p}" for p in parts)


def _infer_call_result(task_text: str, raw: str) -> str:
    combined = (task_text + " " + raw[:500]).lower()
    if re.search(r"退款|赔偿|补偿", combined):
        return "refunded"
    if re.search(r"改期|改时间|重新安排", combined):
        return "rescheduled"
    if re.search(r"升级|转人工|转接", combined):
        return "escalated"
    return "confirmed"


def _build_persona(difficulty: str) -> CalleePersona:
    params = _DIFFICULTY_PERSONA.get(difficulty, _DIFFICULTY_PERSONA["medium"])
    return CalleePersona(**params)


def _build_steps(flow_steps: list[dict]) -> list[InstructionStep]:
    steps = []
    for i, fs in enumerate(flow_steps):
        branches = []
        for b in fs.get("branches", []):
            branches.append(
                Branch(
                    condition=b["condition"],
                    next_step=b.get("next_step", f"step_{i + 2}"),
                    description=b.get("action", ""),
                )
            )

        steps.append(
            InstructionStep(
                step_id=f"step_{i + 1}",
                order=i + 1,
                instruction=fs["instruction"],
                branches=branches,
                completion_condition="",
                source_quote=fs.get("source_quote", ""),
            )
        )
    return steps


def _extract_forbidden(constraints: list[str]) -> list[ForbiddenBehavior]:
    behaviors = []
    negative_keywords = ["不", "禁止", "避免", "不能", "不要", "不说", "不使用"]
    for i, c in enumerate(constraints):
        if any(kw in c for kw in negative_keywords):
            behaviors.append(
                ForbiddenBehavior(
                    id=f"fb_constraint_{i}",
                    description=c,
                    severity="major",
                    source_quote=c,
                )
            )
    return behaviors


def get_compiled_preview(scenario: OutboundScenario) -> dict:
    """Return a structured preview of the compiled evaluation plan for the UI."""
    return {
        "name": scenario.name,
        "role": scenario.raw_instruction.split("\n")[0] if scenario.raw_instruction else "",
        "task": scenario.call_purpose,
        "opening_line": scenario.mandatory_opening,
        "steps": [
            {
                "step_id": s.step_id,
                "order": s.order,
                "instruction": s.instruction,
                "branches": [
                    {"condition": b.condition, "next_step": b.next_step} for b in s.branches
                ],
                "is_optional": s.is_optional,
                "source_quote": s.source_quote,
            }
            for s in scenario.instruction_steps
        ],
        "forbidden_behaviors": [
            {"id": fb.id, "description": fb.description, "severity": fb.severity}
            for fb in scenario.forbidden_behaviors
        ],
        "knowledge_points": scenario.knowledge_points,
        "response_length_limit": scenario.response_length_limit,
        "callee_role": scenario.callee_role,
        "max_turns": scenario.max_turns,
        "expected_steps": scenario.expected_steps_completed,
        "must_call_tools": scenario.must_call_tools,
    }


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 2:
        print("Usage: python compile_instruction.py <instruction.md>")
        print("  Compiles Markdown instruction → OutboundScenario JSON")
        sys.exit(1)

    with open(sys.argv[1], encoding="utf-8") as f:
        raw = f.read()

    scenario = compile_instruction(raw)
    preview = get_compiled_preview(scenario)
    print(json.dumps(preview, ensure_ascii=False, indent=2))
