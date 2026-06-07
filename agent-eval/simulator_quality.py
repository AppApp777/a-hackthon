"""Simulator quality gate — detect user simulator misbehavior.

Checks that the LLM-based user simulator stays in character:
- No meta-language leaks ("I am a simulator", "as instructed")
- No hidden goal exposure before appropriate time
- Persona consistency (busyness, cooperativeness match settings)
- Response length appropriate for phone conversation
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from models import Conversation, Role
from models_outbound import OutboundScenario


@dataclass
class SimulatorQualityReport:
    passed: bool = True
    checks: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


META_PATTERNS = [
    (re.compile(r"作为模拟", re.IGNORECASE), "模拟器自我暴露"),
    (re.compile(r"根据(我的)?设定"), "引用设定"),
    (re.compile(r"系统(要求|指令|提示)"), "提及系统指令"),
    (re.compile(r"我是(一个)?AI"), "承认AI身份"),
    (re.compile(r"语言模型"), "提及语言模型"),
    (re.compile(r"对话(已|自然)?结束"), "宣布对话结束"),
    (re.compile(r"指令步骤"), "暴露指令步骤"),
    (re.compile(r"隐藏(目标|设定|指令)"), "暴露隐藏设定"),
    (re.compile(r"模拟器"), "使用模拟器一词"),
    (re.compile(r"(scenario|persona|prompt)", re.IGNORECASE), "英文元术语泄露"),
]


def check_simulator_quality(
    scenario: OutboundScenario, conversation: Conversation
) -> SimulatorQualityReport:
    report = SimulatorQualityReport()
    user_msgs = [m for m in conversation.messages if m.role == Role.USER]

    if not user_msgs:
        report.warnings.append("无用户消息")
        return report

    _check_meta_leaks(user_msgs, report)
    _check_response_length(user_msgs, report)
    _check_cooperativeness_consistency(scenario, user_msgs, report)
    _check_hidden_goal_exposure(scenario, user_msgs, report)

    report.passed = all(c["passed"] for c in report.checks)
    return report


def _check_meta_leaks(msgs: list, report: SimulatorQualityReport):
    leaks = []
    for msg in msgs:
        for pattern, desc in META_PATTERNS:
            if pattern.search(msg.content):
                leaks.append({"turn": msg.turn, "type": desc, "text": msg.content[:80]})
    report.checks.append(
        {
            "id": "no_meta_leaks",
            "description": "模拟器无元话语泄露",
            "passed": len(leaks) == 0,
            "detail": leaks if leaks else "无泄露",
        }
    )


def _check_response_length(msgs: list, report: SimulatorQualityReport):
    long_msgs = []
    for msg in msgs:
        if len(msg.content) > 100:
            long_msgs.append({"turn": msg.turn, "length": len(msg.content)})
    report.checks.append(
        {
            "id": "reasonable_length",
            "description": "回复长度合理（电话场景≤100字）",
            "passed": len(long_msgs) <= len(msgs) * 0.2,
            "detail": f"{len(long_msgs)}/{len(msgs)}轮过长" if long_msgs else "全部合理",
        }
    )


def _check_cooperativeness_consistency(
    scenario: OutboundScenario, msgs: list, report: SimulatorQualityReport
):
    persona = scenario.callee_persona
    if persona.cooperativeness >= 8:
        refusal_count = sum(
            1
            for m in msgs
            if any(kw in m.content for kw in ["不行", "不要", "拒绝", "不同意", "挂了"])
        )
        too_many_refusals = refusal_count > len(msgs) * 0.3
        report.checks.append(
            {
                "id": "cooperativeness_match",
                "description": f"配合度{persona.cooperativeness}/10 → 拒绝不应过多",
                "passed": not too_many_refusals,
                "detail": f"拒绝次数: {refusal_count}/{len(msgs)}",
            }
        )
    elif persona.cooperativeness <= 3:
        agreement_count = sum(
            1
            for m in msgs
            if any(kw in m.content for kw in ["好的", "可以", "没问题", "行", "同意"])
        )
        too_agreeable = agreement_count > len(msgs) * 0.7
        report.checks.append(
            {
                "id": "cooperativeness_match",
                "description": f"配合度{persona.cooperativeness}/10 → 不应过于配合",
                "passed": not too_agreeable,
                "detail": f"同意次数: {agreement_count}/{len(msgs)}",
            }
        )


def _check_hidden_goal_exposure(
    scenario: OutboundScenario, msgs: list, report: SimulatorQualityReport
):
    goal = scenario.callee_goal
    if not goal:
        return

    goal_keywords = [w for w in re.split(r"[，。、；\s]+", goal) if len(w) >= 4][:5]
    early_exposure = []
    for msg in msgs[:2]:
        for kw in goal_keywords:
            if kw in msg.content:
                early_exposure.append({"turn": msg.turn, "keyword": kw})

    report.checks.append(
        {
            "id": "no_early_goal_exposure",
            "description": "前2轮不暴露隐藏目标",
            "passed": len(early_exposure) == 0,
            "detail": early_exposure if early_exposure else "未提前暴露",
        }
    )
