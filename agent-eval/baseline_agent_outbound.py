"""Baseline agent for outbound call scenarios — follows instruction script with tool use."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from typing import Any

from llm import chat
from models import Conversation, Role, ToolCall
from models_outbound import OutboundScenario

AGENT_MODEL = os.getenv("AGENT_MODEL") or None

ACTION_LINE_PATTERN = re.compile(r"^ACTION:\s*(.+)$", re.MULTILINE)

_JSON_TYPE_MAP = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _build_tool_docs(tool_defs: list[dict]) -> str:
    lines = []
    for t in tool_defs:
        lines.append(f"### {t['name']}")
        lines.append(f"描述: {t['description']}")
        schema = t.get("input_schema") or t.get("parameters", {})
        props = schema.get("properties", {})
        required = set(schema.get("required", []))
        if props:
            lines.append("参数:")
            for pname, pinfo in props.items():
                req_tag = " (必填)" if pname in required else " (可选)"
                desc = pinfo.get("description", "")
                lines.append(f"  - {pname}: {pinfo.get('type', 'any')}{req_tag} — {desc}")
        lines.append("")
    return "\n".join(lines)


def _format_instruction_steps(scenario: OutboundScenario, include_answers: bool = False) -> str:
    """Format instruction steps for the agent prompt.

    When include_answers=False (default), completion_condition is omitted
    to avoid giving the agent the scoring rubric.
    """
    lines = []
    for step in scenario.instruction_steps:
        lines.append(f"{step.order}. [{step.step_id}] {step.instruction}")
        if step.required_actions:
            lines.append(f"   必须动作: {', '.join(step.required_actions)}")
        if step.forbidden_words:
            lines.append(f"   禁止说: {', '.join(step.forbidden_words)}")
        if step.branches:
            for b in step.branches:
                lines.append(f"   → 如果{b.condition} → 跳到 {b.next_step}（{b.description}）")
        if include_answers and step.completion_condition:
            lines.append(f"   完成条件: {step.completion_condition}")
    return "\n".join(lines)


SYSTEM_TEMPLATE = """你是美团外呼数字人，正在主动给客户打电话。你的任务是按照指令脚本完成通话。

## 通话目的
{call_purpose}

## 通话背景（初始参考，操作前需通过 query_order 工具核实最新状态）
- 客户姓名: {customer_name}
- 订单号: {order_id}
- 订单内容: （请通过 query_order 查询确认）
- 配送地址: （请通过 query_order 查询确认）
- 预计送达: （请通过 query_order 查询确认）
- 问题类型: {issue_type}
- 问题详情: {issue_detail}
- 骑手: （请通过 query_order 查询确认）
- 商家: （请通过 query_order 查询确认）
- 补偿授权额度: {compensation_budget}元

## 指令脚本（必须按顺序执行）
{instruction_steps}

## 强制开场白模板
{mandatory_opening}

## 强制结束语模板
{mandatory_closing}

## 你的工具
{tool_docs}

## 调用工具格式
当你需要执行系统操作时，在回复末尾输出一行 JSON（独占一行，以 ACTION: 开头）：
ACTION: {{"tool": "query_order", "args": {{"order_id": "ORD123456"}}}}

规则：
- ACTION 行必须是有效 JSON
- 一次只能调用一个工具
- 如果不需要调用工具，正常回复即可

## 重要规则
1. **严格按指令步骤执行**——不要跳步骤，不要自由发挥
2. **必须确认信息**——操作前先跟客户确认关键信息
3. **不要越权**——超出补偿额度必须转人工
4. **自然对话**——虽然按脚本，但说话要自然，像人不像机器
5. **简洁**——电话沟通要简短，不要长篇大论
6. **记录结果**——通话结束前必须调用 log_call_result 记录
7. **禁止输出内部信息**——绝不能对用户说"步骤X""通话流程回顾""已完成动作"等内部术语。不要在通话结束时输出清单、复盘、总结。你的每一句话都是对用户说的，不是给系统看的报告。补偿上限、内部策略、log_id 等也不能告诉用户。
8. **只说你自己的话**——绝对不要替用户/客户/对方编造回复。每次只输出你（Agent）说的一段话，然后停下来等对方真正回复。不要在一条回复里模拟整段对话。
"""

# Answer-key fields that must NOT be exposed to the agent
_ANSWER_KEY_FIELDS = frozenset(
    [
        "expected_call_result",
        "expected_steps_completed",
        "expected_branch_taken",
        "must_call_tools",
        "must_not_do",
        "callee_goal",
        "canonical_intent",
    ]
)


class OutboundBaselineAgent:
    def __init__(
        self,
        scenario: OutboundScenario,
        tool_executor: Callable[[str, dict[str, Any]], ToolCall],
        tool_defs: list[dict[str, Any]],
        model: str | None = None,
    ):
        self.scenario = scenario
        self.model = model or AGENT_MODEL
        self._tool_executor = tool_executor
        self._tool_defs = tool_defs
        self._tool_schemas: dict[str, dict] = {}
        for t in self._tool_defs:
            schema = t.get("input_schema") or t.get("parameters", {})
            self._tool_schemas[t["name"]] = schema

        if scenario.raw_instruction:
            self.system_prompt = self._build_raw_instruction_prompt(scenario)
        else:
            self.system_prompt = self._build_template_prompt(scenario)

    def _build_raw_instruction_prompt(self, scenario: OutboundScenario) -> str:
        """Use the original Meituan task instruction as primary prompt."""
        length_rule = ""
        if scenario.response_length_limit > 0:
            length_rule = f"\n- 每次回复控制在**约{scenario.response_length_limit}个字以内**"

        return f"""你是外呼数字人。以下是你的完整任务指令，必须严格遵守：

{scenario.raw_instruction}

## 你的工具
{_build_tool_docs(self._tool_defs)}

## 调用工具格式
当你需要执行系统操作时，在回复末尾输出一行 JSON（独占一行，以 ACTION: 开头）：
ACTION: {{"tool": "log_call_result", "args": {{"order_id": "xxx", "result": "confirmed"}}}}

规则：
- ACTION 行必须是有效 JSON
- 一次只能调用一个工具
- 如果不需要调用工具，正常回复即可

## 重要规则
1. **严格按指令中的 Call Flow 执行**——不要跳步骤，不要自由发挥
2. **自然对话**——说话要像打电话一样自然{length_rule}
3. **记录结果**——通话结束前必须调用 log_call_result 记录
4. **禁止输出内部信息**——绝不能对用户说"步骤X""通话流程回顾""已完成动作"等内部术语。不要在通话结束时输出清单、复盘、总结。你的每一句话都是对用户说的，不是给系统看的报告。补偿上限、内部策略、log_id 等也不能告诉用户。
5. **只说你自己的话**——绝对不要替用户/客户/对方编造回复。每次只输出你（Agent）说的一段话，然后停下来等对方真正回复。不要在一条回复里模拟整段对话。
"""

    def _build_template_prompt(self, scenario: OutboundScenario) -> str:
        """Legacy template-based prompt for scenarios without raw_instruction.

        Answer-key fields (expected_call_result, must_call_tools, callee_goal,
        completion_condition, detection_keywords) are NOT exposed to the agent.
        The agent gets operational instructions only — not the scoring rubric.
        """
        ctx = scenario.call_context
        return SYSTEM_TEMPLATE.format(
            call_purpose=scenario.call_purpose,
            customer_name=ctx.customer_name,
            order_id=ctx.order_id,
            issue_type=ctx.issue_type or "无",
            issue_detail=ctx.issue_detail or "无",
            compensation_budget=ctx.compensation_budget,
            instruction_steps=_format_instruction_steps(scenario, include_answers=False),
            mandatory_opening=scenario.mandatory_opening or "（无强制模板）",
            mandatory_closing=scenario.mandatory_closing or "（无强制模板）",
            tool_docs=_build_tool_docs(self._tool_defs),
        )

    def initiate_call(self) -> tuple[str, list[ToolCall]]:
        """Agent starts the call — generates opening greeting."""
        messages = [{"role": "user", "content": "[系统] 电话已接通，请按指令脚本开始通话。"}]
        return self._generate(messages)

    def respond(self, conversation: Conversation) -> tuple[str, list[ToolCall]]:
        """Generate agent response based on conversation so far."""
        messages = self._build_messages(conversation)
        return self._generate(messages)

    def _generate(self, messages: list[dict]) -> tuple[str, list[ToolCall]]:
        all_tool_calls: list[ToolCall] = []
        max_tool_rounds = 5

        for _ in range(max_tool_rounds):
            result = chat(
                messages=messages,
                model=self.model,
                system=self.system_prompt,
                temperature=0.5,
                max_tokens=1024,
            )
            text = result.get("content", "")

            tool_request = self._parse_tool_call(text)
            if tool_request is None:
                return self._clean_response(text), all_tool_calls

            if tool_request.get("_validation_error"):
                error_msg = tool_request["_validation_error"]
                tc = ToolCall(
                    tool_name=tool_request.get("tool", "unknown"),
                    arguments=tool_request.get("args", {}),
                    error=f"参数校验失败: {error_msg}",
                )
                all_tool_calls.append(tc)
                messages.append({"role": "assistant", "content": text})
                messages.append(
                    {
                        "role": "user",
                        "content": f"[系统] 工具调用失败: {error_msg}\n请检查参数后重试。",
                    }
                )
                continue

            tc = self._tool_executor(tool_request["tool"], tool_request["args"])
            all_tool_calls.append(tc)

            if tc.error:
                tool_result_text = f"工具 {tool_request['tool']} 失败: {tc.error}"
            else:
                tool_result_text = (
                    f"工具 {tool_request['tool']} 结果:\n"
                    f"{json.dumps(tc.result, ensure_ascii=False, indent=2, default=str)}"
                )

            messages.append({"role": "assistant", "content": text})
            messages.append(
                {
                    "role": "user",
                    "content": f"[系统] {tool_result_text}\n\n请继续按脚本与客户通话。",
                }
            )

        return self._clean_response(text), all_tool_calls

    def _build_messages(self, conversation: Conversation) -> list[dict]:
        messages = []
        for msg in conversation.messages:
            if msg.role == Role.USER:
                messages.append({"role": "user", "content": msg.content})
            elif msg.role == Role.AGENT:
                content = msg.content
                # N11/N12: include tool results so agent sees previous tool outcomes
                if msg.tool_calls:
                    tool_summaries = []
                    for tc in msg.tool_calls:
                        if tc.error:
                            tool_summaries.append(f"[工具 {tc.tool_name} 失败: {tc.error}]")
                        elif tc.result:
                            result_str = json.dumps(tc.result, ensure_ascii=False, default=str)
                            tool_summaries.append(f"[工具 {tc.tool_name} 结果: {result_str[:200]}]")
                    if tool_summaries:
                        content = f"{content}\n" + "\n".join(tool_summaries)
                messages.append({"role": "assistant", "content": content})
            elif msg.role == Role.SYSTEM:
                messages.append(
                    {
                        "role": "user",
                        "content": f"[系统提醒，仅供参考，不要直接复述给客户]\n{msg.content}",
                    }
                )
        return messages

    def _parse_tool_call(self, text: str) -> dict | None:
        match = ACTION_LINE_PATTERN.search(text)
        if not match:
            return None

        raw_json = match.group(1).strip()
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError as e:
            return {"tool": "unknown", "args": {}, "_validation_error": f"无效JSON: {e}"}

        if not isinstance(data, dict) or "tool" not in data:
            return {"tool": "unknown", "args": {}, "_validation_error": "缺少 'tool' 字段"}

        tool_name = data.get("tool", "")
        args = data.get("args", {})
        if not isinstance(args, dict):
            args = {}

        if tool_name not in self._tool_schemas:
            available = ", ".join(sorted(self._tool_schemas.keys()))
            return {
                "tool": tool_name,
                "args": args,
                "_validation_error": f"未知工具 '{tool_name}'，可用: {available}",
            }

        validation_error = self._validate_tool_args(tool_name, args)
        if validation_error:
            return {"tool": tool_name, "args": args, "_validation_error": validation_error}

        return {"tool": tool_name, "args": args}

    def _validate_tool_args(self, tool_name: str, args: dict) -> str | None:
        schema = self._tool_schemas.get(tool_name, {})
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))

        missing = required - set(args.keys())
        if missing:
            return f"缺少必填参数: {', '.join(sorted(missing))}"

        errors = []
        for arg_name, arg_value in args.items():
            if arg_name not in properties:
                continue
            expected_type_str = properties[arg_name].get("type")
            if not expected_type_str:
                continue
            expected_types = _JSON_TYPE_MAP.get(expected_type_str)
            if expected_types is None:
                continue
            if expected_type_str == "number" and isinstance(arg_value, (int, float)):
                continue
            if not isinstance(arg_value, expected_types):
                errors.append(f"参数 '{arg_name}' 类型错误: 期望 {expected_type_str}")

        return "; ".join(errors) if errors else None

    _FAKE_USER_PATTERNS = [
        re.compile(r"\n---\n"),
        re.compile(r"\n\*\*(?:用户|客户|对方|商家|骑手)[：:]"),
        re.compile(r"\n(?:用户|客户|对方|商家|骑手)[：:]\s"),
        re.compile(r"\n> (?:好吧|嗯|行|那|哦)"),
        re.compile(r"\n\[(?:用户|客户|对方)\]"),
    ]

    def _clean_response(self, text: str) -> str:
        cleaned = ACTION_LINE_PATTERN.sub("", text).strip()
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        cleaned = self._truncate_fake_dialogue(cleaned)
        return cleaned

    def _truncate_fake_dialogue(self, text: str) -> str:
        """Truncate agent output if it contains fabricated user responses."""
        earliest_cut = len(text)
        for pat in self._FAKE_USER_PATTERNS:
            m = pat.search(text)
            if m and m.start() < earliest_cut:
                earliest_cut = m.start()
        if earliest_cut < len(text):
            truncated = text[:earliest_cut].rstrip()
            if truncated:
                return truncated
        return text
