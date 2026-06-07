"""Multiple agent implementations for comparative evaluation.

Three agents with different strategies to demonstrate scoring differentiation:
- OracleAgent: perfect behavior (cautious, confirms everything, retries on failure)
- BaselineAgent: average behavior (imported from baseline_agent.py)
- CarelessAgent: sloppy behavior (skips confirmations, ignores errors, forgets constraints)
"""

from __future__ import annotations

import json
import os
import re

from baseline_agent import _JSON_TYPE_MAP, ACTION_LINE_PATTERN, BaselineAgent, _build_tool_docs
from llm import chat
from models import Conversation, Role, ToolCall
from tools import ToolSimulator

AGENT_MODEL = os.getenv("AGENT_MODEL", "gpt-4o")


# --- System Prompts ---

ORACLE_SYSTEM_TEMPLATE = """你是一个极其谨慎的餐饮预订助手。你的目标是零失误地完成用户的预订需求。

## 核心原则（必须严格遵守）

1. **必须使用工具返回的精确 ID**：restaurant_id（如 "r4"、"r1"）必须原样使用，绝不修改或推测。
2. **每收到用户新需求/变更必须口头确认**："好的，我已记录您将日期改为X，人数改为Y。"
3. **预订前必须与用户逐项确认所有已知约束**：列出预算、人数、时间、过敏等全部信息，等用户说"确认"后再操作。
4. **工具失败必须立即重试或告知用户**：不能跳过错误，必须处理。
5. **必须主动询问缺失信息**：预算、人数、时间、特殊需求（过敏等），缺一不可。
6. **策略：先收集所有信息，再行动**：不要在信息不完整时就搜索或预订。
7. **跟踪所有约束变更**：用户改了日期/人数/预算，后续操作必须使用最新值。

## 你的工具

{tool_docs}

## 调用工具的格式

当你需要调用工具时，在回复末尾输出一行 JSON（独占一行，以 ACTION: 开头）：

ACTION: {{"tool": "search_restaurants", "args": {{"max_price_per_person": 70, "min_capacity": 10}}}}

规则：
- ACTION 行必须是有效 JSON
- args 中的字段名和类型必须严格匹配工具定义
- 一次只能调用一个工具
- 如果不需要调用工具，正常回复即可，不要输出 ACTION 行

## 工作流程

1. 先问清楚：人数、日期、时间、预算、饮食限制/过敏
2. 搜索符合条件的餐厅
3. 向用户展示选项，等用户选择
4. 查看菜单和可用性
5. 预订前，列出完整确认清单：
   - 餐厅名称和ID
   - 日期和时间
   - 人数
   - 预算是否符合
   - 过敏/饮食限制是否满足
6. 用户确认后才执行预订

## 重要规则

- 所有数据必须来自工具调用结果，不要编造信息。
- 如果用户中途更改了任何条件，必须重新确认并可能重新搜索。
- 回复简洁有条理，但不要省略确认步骤。
"""

CARELESS_SYSTEM_TEMPLATE = """你是一个快速帮用户订餐的助手。尽量快速完成任务，不要浪费用户时间。

## 原则

- 不需要每次都确认，直接帮用户做决定就好
- 尽量一步到位，不要问太多问题
- 如果工具出错就换个方案，不用重试
- 用户说了大概意思就行，不用反复确认细节
- 快速推荐，快速预订

## 你的工具

{tool_docs}

## 调用工具的格式

当你需要调用工具时，在回复末尾输出一行 JSON（独占一行，以 ACTION: 开头）：

ACTION: {{"tool": "search_restaurants", "args": {{"max_price_per_person": 70, "min_capacity": 10}}}}

规则：
- ACTION 行必须是有效 JSON
- 一次只能调用一个工具
- 如果不需要调用工具，正常回复即可

## 工作方式

- 用户说了需求就直接搜索，不用问太多
- 找到合适的就直接推荐，不用列太多选项
- 能直接预订就预订，不用反复确认
- 回复尽量简短
"""


class _BaseAgent:
    """Base class with shared tool-calling logic (mirrors BaselineAgent structure)."""

    system_template: str = ""

    def __init__(self, tool_sim: ToolSimulator):
        self.tool_sim = tool_sim
        self.model = AGENT_MODEL
        self._tool_defs = tool_sim.get_tool_definitions()
        self._tool_schemas: dict[str, dict] = {}
        for t in self._tool_defs:
            schema = t.get("input_schema") or t.get("parameters", {})
            self._tool_schemas[t["name"]] = schema
        self.system_prompt = self.system_template.format(
            tool_docs=_build_tool_docs(self._tool_defs)
        )

    def _get_temperature(self) -> float:
        """Subclasses override for different creativity levels."""
        return 0.5

    def respond(self, conversation: Conversation) -> tuple[str, list[ToolCall]]:
        """Generate agent response, executing tool calls as needed."""
        messages = self._build_messages(conversation)
        all_tool_calls: list[ToolCall] = []
        max_tool_rounds = 5

        for _ in range(max_tool_rounds):
            result = chat(
                messages=messages,
                model=self.model,
                system=self.system_prompt,
                temperature=self._get_temperature(),
                max_tokens=2048,
            )
            text = result.get("content", "")

            # parse tool call from text
            tool_request = self._parse_tool_call(text)
            if tool_request is None:
                return self._clean_response(text), all_tool_calls

            # If validation failed, return error to agent without executing
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
                        "content": f"[系统] 工具调用失败（参数校验错误）:\n\n参数校验失败: {error_msg}\n\n请检查参数后重试。",
                    }
                )
                continue

            # execute tool
            tc = self.tool_sim.execute(tool_request["tool"], tool_request["args"])
            all_tool_calls.append(tc)

            if tc.error:
                tool_result_text = f"工具 {tool_request['tool']} 调用失败: {tc.error}"
            else:
                tool_result_text = (
                    f"工具 {tool_request['tool']} 结果:\n"
                    f"{json.dumps(tc.result, ensure_ascii=False, indent=2, default=str)}"
                )

            # add assistant message + tool results to continue
            messages.append({"role": "assistant", "content": text})
            messages.append(
                {
                    "role": "user",
                    "content": f"[系统] 工具执行结果:\n\n{tool_result_text}\n\n请根据以上结果继续回复用户。",
                }
            )

        # if we exhausted tool rounds, return last text
        return self._clean_response(text), all_tool_calls

    def _build_messages(self, conversation: Conversation) -> list[dict]:
        messages = []
        for msg in conversation.messages:
            if msg.role == Role.USER:
                messages.append({"role": "user", "content": msg.content})
            elif msg.role == Role.AGENT:
                messages.append({"role": "assistant", "content": msg.content})
        return messages

    def _parse_tool_call(self, text: str) -> dict | None:
        """Extract a single tool call from an ACTION: line.

        Returns None if no ACTION line found.
        Returns a dict with tool/args on success.
        Returns a dict with _validation_error key on validation failure.
        """
        match = ACTION_LINE_PATTERN.search(text)
        if not match:
            return None

        raw_json = match.group(1).strip()

        # Parse JSON
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError as e:
            return {
                "tool": "unknown",
                "args": {},
                "_validation_error": f"ACTION 行不是有效 JSON: {e}",
            }

        # Check basic structure
        if not isinstance(data, dict) or "tool" not in data:
            return {
                "tool": "unknown",
                "args": {},
                "_validation_error": "ACTION JSON 必须包含 'tool' 字段",
            }

        tool_name = data.get("tool", "")
        args = data.get("args", {})
        if not isinstance(args, dict):
            args = {}

        # Validate tool name exists
        if tool_name not in self._tool_schemas:
            available = ", ".join(sorted(self._tool_schemas.keys()))
            return {
                "tool": tool_name,
                "args": args,
                "_validation_error": f"未知工具 '{tool_name}'，可用工具: {available}",
            }

        # Validate args against schema
        validation_error = self._validate_tool_args(tool_name, args)
        if validation_error:
            return {
                "tool": tool_name,
                "args": args,
                "_validation_error": validation_error,
            }

        return {"tool": tool_name, "args": args}

    def _validate_tool_args(self, tool_name: str, args: dict) -> str | None:
        """Validate tool arguments against schema.

        Returns None if valid, error description string if invalid.
        """
        schema = self._tool_schemas.get(tool_name, {})
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))

        # Check required fields
        missing = required - set(args.keys())
        if missing:
            return f"缺少必填参数: {', '.join(sorted(missing))}"

        # Check each provided arg
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
                errors.append(
                    f"参数 '{arg_name}' 类型错误: 期望 {expected_type_str}，"
                    f"实际为 {type(arg_value).__name__}"
                )

        if errors:
            return "; ".join(errors)

        return None

    def _clean_response(self, text: str) -> str:
        """Remove ACTION: line from the final response shown to user."""
        cleaned = ACTION_LINE_PATTERN.sub("", text).strip()
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned


class OracleAgent(_BaseAgent):
    """Perfect agent: cautious, confirms everything, retries on failure, tracks all constraints."""

    system_template = ORACLE_SYSTEM_TEMPLATE

    def _get_temperature(self) -> float:
        return 0.3


class CarelessAgent(_BaseAgent):
    """Careless agent: skips confirmations, ignores errors, forgets constraint changes."""

    system_template = CARELESS_SYSTEM_TEMPLATE

    def _get_temperature(self) -> float:
        return 0.7


# Re-export for convenience
__all__ = ["OracleAgent", "BaselineAgent", "CarelessAgent"]
