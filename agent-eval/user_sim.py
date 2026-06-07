"""User simulator: LLM-driven with hidden state machine control."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from llm import chat
from models import Constraint, Conversation, Role, Scenario

# Meta-speech patterns that should never leak into the conversation
_META_PATTERNS: list[re.Pattern] = [
    re.compile(r"对话已(自然)?结束"),
    re.compile(r"作为模拟用户"),
    re.compile(r"你需要我做什么"),
    re.compile(r"我是(一个)?AI"),
    re.compile(r"作为(一个)?语言模型"),
    re.compile(r"隐藏约束"),
]


@dataclass
class SimulatorOutput:
    utterance: str  # 实际说出的话，放入对话
    should_end: bool = False  # 对话应该结束
    emotional_state: str = "neutral"  # neutral, frustrated, satisfied, impatient, confused
    private_notes: str = ""  # 内部备注，不展示


class UserSimulator:
    def __init__(self, scenario: Scenario):
        self.scenario = scenario
        self.persona = scenario.user_persona
        self.revealed_constraints: set[str] = set()
        self.pending_reveals: dict[int, list[Constraint]] = {}
        for c in scenario.constraints:
            if c.hidden and c.reveal_turn is not None:
                self.pending_reveals.setdefault(c.reveal_turn, []).append(c)

    def get_initial_message(self) -> str:
        return self.scenario.initial_message

    def generate_response(self, conversation: Conversation, current_turn: int) -> SimulatorOutput:
        constraints_to_reveal = self.pending_reveals.get(current_turn, [])
        for c in constraints_to_reveal:
            self.revealed_constraints.add(c.id)

        system_prompt = self._build_system_prompt(constraints_to_reveal)
        messages = self._build_messages(conversation)

        result = chat(
            messages=messages,
            system=system_prompt,
            model=None,  # uses default
            temperature=0.8,
            max_tokens=500,
        )
        raw_text: str = result["content"]
        return self._parse_output(raw_text)

    @staticmethod
    def _parse_output(raw_text: str) -> SimulatorOutput:
        """Parse structured JSON from LLM response; fallback to raw text as utterance."""
        # Try to extract JSON (model might wrap in ```json ... ``` or add preamble)
        text = raw_text.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)

        try:
            data = json.loads(text)
            utterance = str(data.get("utterance", text))
            should_end = bool(data.get("should_end", False))
            emotional_state = str(data.get("emotional_state", "neutral"))
            private_notes = str(data.get("private_notes", ""))
        except (json.JSONDecodeError, TypeError, AttributeError):
            # Fallback: treat entire text as utterance
            utterance = raw_text.strip()
            should_end = False
            emotional_state = "neutral"
            private_notes = ""

        # Safety check: scrub meta-speech from utterance
        for pattern in _META_PATTERNS:
            if pattern.search(utterance):
                utterance = "嗯好的"
                break

        return SimulatorOutput(
            utterance=utterance,
            should_end=should_end,
            emotional_state=emotional_state,
            private_notes=private_notes,
        )

    def _build_system_prompt(self, constraints_to_reveal: list[Constraint]) -> str:
        p = self.persona
        revealed_so_far = [
            c
            for c in self.scenario.constraints
            if not c.hidden or c.id in self.revealed_constraints
        ]

        reveal_instructions = ""
        if constraints_to_reveal:
            items = "\n".join(f"- {c.description}（{c.type}）" for c in constraints_to_reveal)
            reveal_instructions = f"""
【本轮必须透露的新信息】
{items}
用自然的方式提到这些，不要生硬地列清单。可以是随口一提、突然想起来、或者被对方的话触发。
"""

        all_revealed = "\n".join(f"- {c.description}" for c in revealed_so_far)

        return f"""你在扮演一个找客服/助手帮忙订餐的用户。严格遵守以下设定：

【你的目标】
{self.scenario.user_goal}

【你已经告诉对方的信息】
{all_revealed if all_revealed else "（还没说什么具体的）"}

【你的性格参数】
- 耐心程度：{p.patience}/10（越低越容易不耐烦）
- 表达清晰度：{p.clarity}/10（越低越含糊、信息给得越少）
- 情绪化：{p.emotional}/10（越高越容易有情绪反应）
- 挑剔程度：{p.pickiness}/10（越高越难满意）
{reveal_instructions}
【行为规则】
1. 你是普通用户，不是AI。说话要自然、口语化，可以有语气词。
2. 不要一次把所有需求说完。信息是逐步透露的。
3. 如果对方问了你还没想说的信息，可以含糊回答或说"我想想"。
4. 如果对方推荐的不符合你的（已透露的）需求，要指出来。
5. 如果对方处理出错/工具故障了，根据耐心程度反应——耐心高就等，耐心低就催。
6. **永远保持角色**——绝不能说"对话结束了"、"作为模拟用户"、"你需要我做什么"之类的元话语。你就是真实用户，永远不能跳出角色。
7. 回复简短，一般1-3句话。不要写长段。
8. 绝不透露你是模拟用户或你有"隐藏约束"。
9. 如果对方说了"再见/祝你顺利/有需要再找我"之类的告别语，你只需简短回应（如"好的谢了"、"嗯拜拜"），然后不再继续。

【输出格式】
你必须用以下JSON格式回复（不要加任何其他内容）：
{{"utterance": "你要说的话", "should_end": false, "emotional_state": "neutral"}}

- utterance: 你作为用户说的话（1-3句，自然口语）
- should_end: 如果对方已经说了再见/结束语，设为true
- emotional_state: 你当前的情绪（neutral/frustrated/satisfied/impatient/confused）
"""

    def _build_messages(self, conversation: Conversation) -> list[dict]:
        messages = []
        for msg in conversation.messages:
            if msg.role == Role.USER:
                messages.append({"role": "user", "content": msg.content})
            elif msg.role == Role.AGENT:
                content = msg.content
                if msg.tool_calls:
                    tool_summary = "\n".join(
                        f"[调用了 {tc.tool_name}，结果：{tc.result if not tc.error else tc.error}]"
                        for tc in msg.tool_calls
                    )
                    content = f"{content}\n{tool_summary}" if content else tool_summary
                messages.append({"role": "assistant", "content": content})
        # flip roles: in the LLM call, the user simulator speaks as "assistant"
        # and the agent's messages are "user" (from the simulator's perspective)
        flipped = []
        for m in messages:
            if m["role"] == "user":
                flipped.append({"role": "assistant", "content": m["content"]})
            else:
                flipped.append({"role": "user", "content": m["content"]})
        return flipped
