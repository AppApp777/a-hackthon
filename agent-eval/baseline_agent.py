"""Baseline agent-under-test: LLM agent with tool use via strict JSON ACTION protocol."""

from __future__ import annotations

import json
import os
import re

from llm import chat
from models import Conversation, Role, ToolCall
from tools import ToolSimulator

AGENT_MODEL = os.getenv("AGENT_MODEL", "gpt-4o")


def _build_tool_docs(tool_defs: list[dict]) -> str:
    """Format tool definitions as readable documentation for the system prompt."""
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


SYSTEM_TEMPLATE = """你是一个餐饮预订助手，帮用户订餐厅、选菜、预订座位。

## 你的工具

你可以调用以下工具来完成任务。**你必须使用这些工具**，不要编造信息。

{tool_docs}

## 调用工具的格式

当你需要调用工具时，在回复末尾输出一行 JSON（独占一行，以 ACTION: 开头）：

ACTION: {{"tool": "search_restaurants", "args": {{"max_price_per_person": 70, "min_capacity": 10}}}}

规则：
- ACTION 行必须是有效 JSON
- args 中的字段名和类型必须严格匹配工具定义
- 一次只能调用一个工具
- 如果不需要调用工具，正常回复即可，不要输出 ACTION 行

## 重要规则

1. **必须用工具查询**：不要凭空编造餐厅名称、价格、地址等信息。所有数据必须来自工具调用结果。
2. **必须使用工具返回的精确 ID**：搜索结果中的 restaurant_id（如 "r4"、"r1"）是系统内部标识符，后续调用（check_availability、make_reservation 等）必须原样使用这些 ID，不要自行编造或改写。
3. 记住用户说过的所有要求（预算、人数、过敏、时间等），不要遗忘。
4. 推荐前确认符合用户的预算和需求。
5. 执行关键操作（预订、下单）前先跟用户确认。
6. 如果工具调用失败，告知用户并尝试替代方案。
7. 回复简洁有条理。
8. 一次只调用你需要的工具，获取结果后再决定下一步。
"""

ACTION_LINE_PATTERN = re.compile(r"^ACTION:\s*(.+)$", re.MULTILINE)

# Type mapping for JSON schema validation
_JSON_TYPE_MAP = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
}


class BaselineAgent:
    def __init__(self, tool_sim: ToolSimulator):
        self.tool_sim = tool_sim
        self.model = AGENT_MODEL
        self._tool_defs = tool_sim.get_tool_definitions()
        self._tool_schemas: dict[str, dict] = {}
        for t in self._tool_defs:
            schema = t.get("input_schema") or t.get("parameters", {})
            self._tool_schemas[t["name"]] = schema
        self.system_prompt = SYSTEM_TEMPLATE.format(tool_docs=_build_tool_docs(self._tool_defs))

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
                temperature=0.5,
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
                # Allow extra args (lenient on unknown fields)
                continue

            expected_type_str = properties[arg_name].get("type")
            if not expected_type_str:
                continue

            expected_types = _JSON_TYPE_MAP.get(expected_type_str)
            if expected_types is None:
                continue

            # Allow int where number is expected
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
