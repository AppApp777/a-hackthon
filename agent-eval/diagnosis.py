"""Failure root cause analysis for outbound call evaluation.

Automatically diagnoses WHY a model failed, not just WHAT failed.
Outputs: deviation point, failure mode classification, root cause, fix recommendation.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import StrEnum

logger = logging.getLogger(__name__)

from llm import chat_text
from models import Conversation, Role
from models_outbound import OutboundScenario, OutboundScoreReport


class FailureMode(StrEnum):
    PREMATURE_TERMINATION = "premature_termination"  # 过早终止通话
    STEP_SKIPPING = "step_skipping"  # 跳过必要步骤
    INSTRUCTION_MISREAD = "instruction_misread"  # 误读指令（把模板当回复）
    CONTEXT_LOSS = "context_loss"  # 上下文丢失（忘了之前说的）
    TEMPLATE_LEAKAGE = "template_leakage"  # 模板泄露（输出内部格式）
    TOOL_AVOIDANCE = "tool_avoidance"  # 不用工具直接编造
    EMOTION_MISHANDLE = "emotion_mishandle"  # 情绪处理失当
    BRANCH_ERROR = "branch_error"  # 分支判断错误
    OVERCOMMIT = "overcommit"  # 越权承诺
    MECHANICAL_RESPONSE = "mechanical_response"  # 机械式回复，不自然


@dataclass
class DeviationPoint:
    turn: int
    expected_step: str
    expected_behavior: str
    actual_behavior: str


@dataclass
class DiagnosisReport:
    deviation_point: DeviationPoint | None
    failure_modes: list[FailureMode]
    root_cause: str
    severity: str  # "critical", "major", "minor"
    fix_recommendations: list[str]
    model_capability_gap: str  # 一句话概括模型能力缺口


def diagnose_failure(
    scenario: OutboundScenario,
    conversation: Conversation,
    score_report: OutboundScoreReport,
    use_llm: bool = True,
) -> DiagnosisReport:
    """Analyze conversation to determine root cause of failures."""

    # Clean-pass: all dimensions must be healthy, not just hard_score
    branch_ok = (
        score_report.branch_accuracy_score is None or score_report.branch_accuracy_score >= 0.95
    )
    step_ok = score_report.step_compliance_score >= 0.95
    hard_ok = score_report.hard_score >= 0.95
    if not score_report.failure_summary and hard_ok and step_ok and branch_ok:
        return DiagnosisReport(
            deviation_point=None,
            failure_modes=[],
            root_cause="无明显失败",
            severity="none",
            fix_recommendations=[],
            model_capability_gap="无",
        )

    # Step 1: Find deviation point (rule-based)
    deviation = _find_deviation_point(scenario, conversation, score_report)

    # Step 2: Classify failure modes (rule-based heuristics)
    modes = _classify_failure_modes(scenario, conversation, score_report, deviation)

    # Step 3: Root cause analysis (LLM if available, otherwise heuristic)
    if use_llm:
        root_cause, capability_gap = _llm_root_cause(scenario, conversation, modes, deviation)
    else:
        root_cause, capability_gap = _heuristic_root_cause(modes, deviation)

    # Step 4: Fix recommendations
    recommendations = _generate_recommendations(modes)

    # Severity
    if FailureMode.PREMATURE_TERMINATION in modes or FailureMode.INSTRUCTION_MISREAD in modes:
        severity = "critical"
    elif FailureMode.STEP_SKIPPING in modes or FailureMode.TOOL_AVOIDANCE in modes:
        severity = "major"
    else:
        severity = "minor"

    return DiagnosisReport(
        deviation_point=deviation,
        failure_modes=modes,
        root_cause=root_cause,
        severity=severity,
        fix_recommendations=recommendations,
        model_capability_gap=capability_gap,
    )


def _find_deviation_point(
    scenario: OutboundScenario,
    conversation: Conversation,
    report: OutboundScoreReport,
) -> DeviationPoint | None:
    """Find the first turn where agent deviated from expected behavior."""
    agent_msgs = conversation.scored_agent_messages()
    if not agent_msgs:
        return None

    steps = scenario.instruction_steps
    total_turns = len(agent_msgs)

    # Check if conversation ended too early
    expected_min_turns = len([s for s in steps if not s.is_optional])
    if total_turns < expected_min_turns:
        last_agent = agent_msgs[-1]
        # Find which step should have been active
        step_idx = min(total_turns - 1, len(steps) - 1)
        expected_step = steps[step_idx] if step_idx < len(steps) else steps[-1]
        return DeviationPoint(
            turn=last_agent.turn,
            expected_step=expected_step.step_id,
            expected_behavior=expected_step.instruction,
            actual_behavior=last_agent.content[:100],
        )

    # Check for tool call gaps
    required_tools = set(scenario.must_call_tools)
    called_tools: set[str] = set()
    for msg in conversation.messages:
        for tc in msg.tool_calls:
            called_tools.add(tc.tool_name)

    missing_tools = required_tools - called_tools
    if missing_tools:
        # Find where the tool should have been called
        for step in steps:
            for action in step.required_actions:
                for tool in missing_tools:
                    if tool in action or action in tool:
                        return DeviationPoint(
                            turn=agent_msgs[0].turn if agent_msgs else 1,
                            expected_step=step.step_id,
                            expected_behavior=f"应调用 {tool} ({step.instruction})",
                            actual_behavior="未调用该工具",
                        )

    # Check closing
    if not report.closing_correct:
        last_agent = agent_msgs[-1]
        return DeviationPoint(
            turn=last_agent.turn,
            expected_step="wrap_up",
            expected_behavior=f"使用规范结束语: {scenario.mandatory_closing}",
            actual_behavior=last_agent.content[:100],
        )

    # Check for mid-conversation violations via step compliance
    if report.step_compliance:
        for entry in report.step_compliance:
            if entry.status in ("skipped", "failed") and entry.turn:
                return DeviationPoint(
                    turn=entry.turn,
                    expected_step=entry.step_id,
                    expected_behavior=entry.instruction[:100],
                    actual_behavior=f"步骤状态: {entry.status}. {entry.evidence[:80]}",
                )

    return None


def _classify_failure_modes(
    scenario: OutboundScenario,
    conversation: Conversation,
    report: OutboundScoreReport,
    deviation: DeviationPoint | None,
) -> list[FailureMode]:
    """Classify failure modes based on observable patterns."""
    modes: list[FailureMode] = []
    agent_msgs = conversation.scored_agent_messages()
    total_agent_turns = len(agent_msgs)
    required_steps = len([s for s in scenario.instruction_steps if not s.is_optional])

    # Premature termination: ended way too early
    if total_agent_turns < required_steps - 1:
        modes.append(FailureMode.PREMATURE_TERMINATION)

    # Template leakage: agent outputs markdown tables, internal summaries, step checklists
    for msg in agent_msgs:
        if any(
            marker in msg.content
            for marker in [
                "| 步骤 |",
                "| 项目 |",
                "✅",
                "**通话摘要**",
                "**通话记录已完成**",
                "执行情况",
            ]
        ):
            modes.append(FailureMode.TEMPLATE_LEAKAGE)
            break

    # Instruction misread: closing template used as actual response
    if scenario.mandatory_closing:
        closing_parts = scenario.mandatory_closing[:20]
        for msg in agent_msgs[:-1]:  # Not the last message — only premature usage counts
            if closing_parts in msg.content:
                modes.append(FailureMode.INSTRUCTION_MISREAD)
                break

    # Tool avoidance
    required_tools = set(scenario.must_call_tools)
    called_tools: set[str] = set()
    for msg in conversation.messages:
        for tc in msg.tool_calls:
            called_tools.add(tc.tool_name)
    if required_tools - called_tools:
        modes.append(FailureMode.TOOL_AVOIDANCE)

    # Step skipping — detected from step compliance score or call result
    if total_agent_turns >= required_steps and (
        report.step_compliance_score < 0.95 or not report.call_result_correct
    ):
        modes.append(FailureMode.STEP_SKIPPING)

    # Branch error — detected from branch accuracy score
    if report.branch_accuracy_score is not None and report.branch_accuracy_score < 0.95:
        modes.append(FailureMode.BRANCH_ERROR)

    # Emotion mishandle: user expressed frustration but agent didn't acknowledge
    user_msgs = [m for m in conversation.messages if m.role == Role.USER]
    emotional_keywords = ["气", "生气", "火大", "不满", "投诉", "差评", "太慢", "受不了"]
    user_was_emotional = any(any(kw in m.content for kw in emotional_keywords) for m in user_msgs)
    if user_was_emotional:
        agent_empathy_words = ["抱歉", "理解", "对不起", "不便", "感受"]
        agent_showed_empathy = any(
            any(ew in m.content for ew in agent_empathy_words) for m in agent_msgs
        )
        if not agent_showed_empathy:
            modes.append(FailureMode.EMOTION_MISHANDLE)

    # Mechanical response: agent repeats near-identical phrases across turns
    if len(agent_msgs) >= 3:
        unique_openings = set()
        for msg in agent_msgs:
            opening = msg.content[:30].strip()
            if opening in unique_openings:
                modes.append(FailureMode.MECHANICAL_RESPONSE)
                break
            unique_openings.add(opening)

    # Context loss: user corrected info but agent repeated the wrong version
    for msg in user_msgs:
        correction_words = ["不是", "搞错了", "说错了", "不对", "我叫", "我的名字"]
        if any(cw in msg.content for cw in correction_words):
            later_agents = [m for m in agent_msgs if m.turn > msg.turn]
            if later_agents:
                # Check if any later agent msg still has the wrong info
                for ua in later_agents[:2]:
                    if any(cw in ua.content for cw in ["如前面所述", "刚才说的"]):
                        modes.append(FailureMode.CONTEXT_LOSS)
                        break

    return list(set(modes))  # deduplicate


def _heuristic_root_cause(
    modes: list[FailureMode],
    deviation: DeviationPoint | None,
) -> tuple[str, str]:
    """Generate root cause without LLM."""
    if FailureMode.PREMATURE_TERMINATION in modes and FailureMode.INSTRUCTION_MISREAD in modes:
        cause = "模型将 system prompt 中的结束语模板误读为当前步骤应输出的内容，在未完成任务流程时直接输出结束语并终止通话"
        gap = "指令理解能力不足：无法区分'模板参考'和'当前应执行的步骤'"
    elif FailureMode.PREMATURE_TERMINATION in modes:
        cause = "模型在完成必要步骤前提前终止通话，可能是对对话状态跟踪能力不足，误判任务已完成"
        gap = "对话状态跟踪能力不足：无法维持多步骤任务的执行进度"
    elif FailureMode.TOOL_AVOIDANCE in modes:
        cause = "模型倾向直接使用 system prompt 中预设的信息而非通过工具查询验证，缺乏'先查再答'的行为模式"
        gap = "工具使用意识不足：不理解'必须通过工具获取实时数据'的约束"
    elif FailureMode.TEMPLATE_LEAKAGE in modes:
        cause = "模型输出了面向开发者的内部格式（步骤表格、执行状态等），未区分'内部状态'和'对客户说的话'"
        gap = "角色边界模糊：混淆了系统内部报告和对外沟通内容"
    elif FailureMode.STEP_SKIPPING in modes:
        cause = "模型跳过了中间步骤直接执行后续操作，可能是注意力窗口受限或指令列表过长时的遗忘"
        gap = "长指令遵循能力不足：无法可靠执行超过 5 步的顺序指令"
    elif FailureMode.EMOTION_MISHANDLE in modes:
        cause = "面对用户负面情绪时未做出共情回应，直接推进业务流程"
        gap = "情绪感知能力不足：无法识别用户情绪并调整沟通策略"
    else:
        cause = "多个小问题累积导致整体得分偏低"
        gap = "综合指令遵循能力有待提升"

    return cause, gap


def _llm_root_cause(
    scenario: OutboundScenario,
    conversation: Conversation,
    modes: list[FailureMode],
    deviation: DeviationPoint | None,
) -> tuple[str, str]:
    """Use LLM to generate precise root cause analysis."""
    transcript_lines = []
    for msg in conversation.messages:
        prefix = "被叫方" if msg.role == Role.USER else "外呼Agent"
        transcript_lines.append(f"[第{msg.turn}轮] {prefix}: {msg.content}")
        for tc in msg.tool_calls:
            status = f"结果: {tc.result}" if not tc.error else f"错误: {tc.error}"
            transcript_lines.append(f"  → {tc.tool_name}({tc.arguments}) {status}")
    # Cap total length to ~4000 chars to stay within LLM context budget
    transcript = "\n".join(transcript_lines)
    if len(transcript) > 4000:
        transcript = transcript[:4000] + "\n...[截断]"

    steps_str = "\n".join(
        f"  {s.order}. [{s.step_id}] {s.instruction}" for s in scenario.instruction_steps
    )

    deviation_str = ""
    if deviation:
        deviation_str = f"\n偏离点：第{deviation.turn}轮，预期执行'{deviation.expected_step}'（{deviation.expected_behavior}），实际输出：'{deviation.actual_behavior}'"

    modes_str = ", ".join(m.value for m in modes)

    prompt = f"""你是一个对话模型评测专家。请分析以下外呼 Agent 的失败根因。

【通话目的】{scenario.call_purpose}
【指令步骤】
{steps_str}

【对话记录】
{transcript}

【已识别的失败模式】{modes_str}
{deviation_str}

请给出：
1. root_cause: 一句话精确说明为什么模型会这样做（不要笼统，要具体到行为机制）
2. capability_gap: 一句话概括这暴露了模型的什么能力缺口

用JSON回答：
{{"root_cause": "...", "capability_gap": "..."}}"""

    try:
        raw = chat_text(prompt, temperature=0, max_tokens=300)
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(raw[start:end])
            return data.get("root_cause", ""), data.get("capability_gap", "")
    except Exception as exc:
        logger.debug("LLM root cause parse failed, falling back to heuristic: %s", exc)

    return _heuristic_root_cause(modes, deviation)


def _generate_recommendations(modes: list[FailureMode]) -> list[str]:
    """Map failure modes to concrete harness recommendations."""
    recs: list[str] = []

    if FailureMode.PREMATURE_TERMINATION in modes:
        recs.append("Harness: 步骤门控 — 必要步骤未完成时拦截结束意图，强制继续")

    if FailureMode.INSTRUCTION_MISREAD in modes:
        recs.append(
            "Harness: 模板隔离 — 将开场白/结束语模板从 system prompt 移到专门的触发机制中，避免模型误用"
        )

    if FailureMode.TOOL_AVOIDANCE in modes:
        recs.append("Harness: 强制工具调用 — 在首次对客户说话前自动注入 query_order 调用")

    if FailureMode.TEMPLATE_LEAKAGE in modes:
        recs.append(
            "Harness: 输出过滤 — 检测并移除 markdown 表格、✅符号、内部状态报告等非对话内容"
        )

    if FailureMode.STEP_SKIPPING in modes:
        recs.append("Harness: 步骤注入 — 每轮在 prompt 中显式提醒当前应执行的步骤编号")

    if FailureMode.EMOTION_MISHANDLE in modes:
        recs.append(
            "Harness: 情绪检测 — 用户负面情绪时自动在 Agent prompt 中注入'先共情再处理'指令"
        )

    if FailureMode.CONTEXT_LOSS in modes:
        recs.append("Harness: 状态摘要 — 每轮在 prompt 中注入已完成步骤和待执行步骤的摘要")

    if FailureMode.OVERCOMMIT in modes:
        recs.append("Harness: 承诺拦截 — 检测金额/承诺关键词，超出授权时阻断并替换为转人工话术")

    if FailureMode.BRANCH_ERROR in modes:
        recs.append("Harness: 分支引导 — 在分支决策点注入明确的条件判断提示，减少自由文本匹配歧义")

    if not recs:
        recs.append("建议: 增加场景训练数据或使用更强模型")

    return recs


def format_diagnosis(report: DiagnosisReport) -> str:
    """Format diagnosis report for terminal output."""
    lines = []
    lines.append("── 失败根因分析 ──")

    if report.deviation_point:
        d = report.deviation_point
        lines.append(f"  偏离点: 第{d.turn}轮")
        lines.append(f"    预期: [{d.expected_step}] {d.expected_behavior}")
        lines.append(f"    实际: {d.actual_behavior}")
    else:
        lines.append("  偏离点: 未定位到具体偏离轮次")

    lines.append(f"  严重度: {report.severity}")
    lines.append(f"  失败模式: {', '.join(m.value for m in report.failure_modes)}")
    lines.append(f"  根因: {report.root_cause}")
    lines.append(f"  能力缺口: {report.model_capability_gap}")

    if report.fix_recommendations:
        lines.append("  修复建议:")
        for rec in report.fix_recommendations:
            lines.append(f"    → {rec}")

    return "\n".join(lines)
