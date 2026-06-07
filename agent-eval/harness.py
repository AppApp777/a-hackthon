"""Harness layer: intervention middleware between Agent and Orchestrator.

Sits between the agent's raw output and the user-facing conversation.
Applies fixes to compensate for model weaknesses detected by the evaluation system.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum

logger = logging.getLogger(__name__)

from models import Conversation, Role, ToolCall
from models_outbound import OutboundScenario
from tools_outbound import OutboundToolSimulator


class HarnessMode(StrEnum):
    RAW_EVAL = "raw_eval"  # All interventions off, only logs violations
    GUARDED_EVAL = (
        "guarded_eval"  # Safety filters + tool/state gating + rollback, NO content injection
    )
    SUPERVISED_DEPLOY = "supervised_deploy"  # Full harness, step reminders, closing repair


class AdaptiveLevel(StrEnum):
    FULL = "full"  # All interventions active
    BLOCK_ONLY = "block_only"  # Gate/block only, no content injections
    LOG_ONLY = "log_only"  # Log violations only, no actual intervention


def _normalize_text(text: str) -> str:
    """Normalize Unicode to catch homoglyph/spacing bypasses."""
    normalized = unicodedata.normalize("NFKC", text)
    normalized = re.sub(r"[​‌‍⁠﻿]", "", normalized)
    return normalized.lower()


@dataclass
class HarnessConfig:
    """Configuration for which harness interventions are active."""

    force_query_order: bool = True
    step_gating: bool = True
    closing_injection: bool = True
    emotion_protection: bool = True
    forbidden_word_blocking: bool = True
    template_leak_filter: bool = True
    tool_call_gating: bool = True  # block premature tool calls (e.g. escalate before refund)
    min_turns_after_emotion: int = 2
    step_injection_interval: int = 5
    step_injection_on_deviation: bool = True
    step_injection_periodic: bool = False  # only inject on deviation by default
    max_blocks_per_conversation: int = 6
    mode: str = "guarded_eval"  # default mode for evaluation
    adaptive: bool = False
    adaptive_degrade_threshold: int = 2

    @classmethod
    def from_mode(cls, mode: str) -> HarnessConfig:
        """Create a HarnessConfig preset for the given HarnessMode."""
        if mode == "raw_eval":
            return cls(
                force_query_order=False,
                step_gating=False,
                closing_injection=False,
                emotion_protection=False,
                forbidden_word_blocking=True,  # still LOG but don't block
                template_leak_filter=False,
                tool_call_gating=False,
                step_injection_on_deviation=False,
                step_injection_periodic=False,
                mode=mode,
            )
        elif mode == "supervised_deploy":
            return cls(
                force_query_order=True,
                step_gating=True,
                closing_injection=True,
                emotion_protection=True,
                forbidden_word_blocking=True,
                template_leak_filter=True,
                tool_call_gating=True,
                step_injection_on_deviation=True,
                step_injection_periodic=True,
                step_injection_interval=3,
                mode=mode,
            )
        else:  # guarded_eval (default)
            return cls(
                force_query_order=True,
                step_gating=True,
                closing_injection=False,
                emotion_protection=True,
                forbidden_word_blocking=True,
                template_leak_filter=True,
                tool_call_gating=True,
                step_injection_on_deviation=False,
                step_injection_periodic=False,
                mode=mode,
            )


@dataclass
class StepProgress:
    """Tracks completion status of a single instruction step."""

    step_id: str
    order: int
    instruction: str
    status: str = "pending"  # pending, in_progress, completed, skipped
    completed_at_turn: int | None = None


@dataclass
class HarnessState:
    """Runtime state tracking for harness decisions."""

    query_order_done: bool = False
    completed_steps: set = field(default_factory=set)
    last_user_emotional_turn: int = 0
    current_turn: int = 0
    interventions_log: list = field(default_factory=list)
    blocked_outputs: int = 0
    injected_tools: int = 0
    injected_reminders: int = 0
    sanitized_outputs: int = 0
    content_injections: int = 0  # closing_injection + step_injection count
    step_progress: list = field(default_factory=list)  # list[StepProgress]
    last_injection_turn: int = 0
    last_injection_step: str = ""  # step_id at last injection, for deviation detection
    adaptive_level: str = AdaptiveLevel.FULL
    consecutive_ineffective: int = 0
    adaptive_transitions: list = field(default_factory=list)


class OutboundHarness:
    """Middleware that wraps agent behavior to enforce compliance."""

    def __init__(
        self,
        scenario: OutboundScenario,
        tool_sim: OutboundToolSimulator,
        config: HarnessConfig | None = None,
    ):
        self.scenario = scenario
        self.tool_sim = tool_sim
        self.config = config or HarnessConfig()
        self.state = HarnessState()
        self._required_steps = set(scenario.expected_steps_completed)
        # B2: require negative context co-occurrence for ambiguous keywords
        self._emotion_keywords = [
            "生气",
            "火大",
            "不满",
            "投诉",
            "差评",
            "太慢",
            "受不了",
            "打发",
            "气死",
            "火气",
            "怒气",
            "窝火",
            "怎么搞的",
            "搞什么",
        ]
        self._emotion_keywords_needs_context = {
            "什么意思": ["你", "这", "到底", "凭"],
        }
        self._emotion_false_positives = frozenset(
            [
                "天气",
                "气温",
                "空气",
                "气氛",
                "气象",
                "运气",
                "气质",
                "气味",
                "气压",
                "语气",
                "口气",
                "气色",
                "气体",
                "客气",
                "和气",
                "大气",
                "神气",
                "气候",
                "气息",
                "气功",
                "气球",
            ]
        )
        self.state.step_progress = [
            StepProgress(step_id=s.step_id, order=s.order, instruction=s.instruction)
            for s in scenario.instruction_steps
        ]
        self._step_tool_map = self._build_step_tool_map()

    def pre_first_turn(self) -> list[ToolCall]:
        """Called before agent's first output. Can inject tool calls."""
        injected: list[ToolCall] = []
        if self.config.force_query_order and self.scenario.call_context.order_id:
            tc = self.tool_sim.execute(
                "query_order", {"order_id": self.scenario.call_context.order_id}
            )
            tc.source = "harness"
            injected.append(tc)
            self.state.query_order_done = True
            self.state.injected_tools += 1
            self.state.interventions_log.append(
                {
                    "type": "force_query_order",
                    "turn": 0,
                    "detail": f"自动查询订单 {self.scenario.call_context.order_id}",
                }
            )
        return injected

    def process_agent_output(
        self,
        text: str,
        tool_calls: list[ToolCall],
        conversation: Conversation,
        turn: int | None = None,
    ) -> tuple[str, list[ToolCall], bool]:
        """Process agent output, potentially modifying or blocking it.

        Returns: (modified_text, modified_tool_calls, should_block)
        If should_block=True, the orchestrator should ask agent to regenerate.
        ``turn`` is the orchestrator's actual turn number; only advances current_turn
        when it increases, so retries within the same turn don't inflate the counter.
        """
        if turn is not None:
            if turn > self.state.current_turn:
                self.state.current_turn = turn
        else:
            self.state.current_turn += 1

        # Track completed steps from tool calls
        for tc in tool_calls:
            if tc.tool_name == "query_order":
                self.state.query_order_done = True
            if tc.tool_name == "log_call_result":
                self.state.completed_steps.add("wrap_up")

        level = self.state.adaptive_level
        budget_exhausted = self.state.blocked_outputs >= self.config.max_blocks_per_conversation

        # LOG_ONLY: log violations but never block or modify
        if level == AdaptiveLevel.LOG_ONLY:
            if self.config.forbidden_word_blocking:
                found = self._check_forbidden_words(text)
                if found:
                    self.state.interventions_log.append(
                        {
                            "type": "forbidden_word_log_only",
                            "turn": self.state.current_turn,
                            "detail": f"检测到禁止词(仅记录): {found}",
                        }
                    )
            return text, tool_calls, False

        # ── Safety checks (NEVER limited by budget) ──

        # 1. Tool call gating
        if self.config.tool_call_gating:
            blocked_tool = self._check_tool_gating(tool_calls)
            if blocked_tool:
                self.state.blocked_outputs += 1
                self.state.interventions_log.append(
                    {
                        "type": "tool_gating",
                        "turn": self.state.current_turn,
                        "detail": f"拦截过早调用 {blocked_tool}（前置工具未完成）",
                    }
                )
                return text, tool_calls, True

        # 2. Template leak filter (non-blocking)
        if self.config.template_leak_filter:
            text = self._filter_template_leaks(text)

        # 3. Forbidden word blocking (raw_eval: log-only; other modes: block)
        if self.config.forbidden_word_blocking:
            blocked = self._check_forbidden_words(text)
            if blocked:
                self.state.interventions_log.append(
                    {
                        "type": "forbidden_word_block",
                        "turn": self.state.current_turn,
                        "detail": f"检测到禁止词: {blocked}",
                    }
                )
                if self.config.mode != HarnessMode.RAW_EVAL:
                    self.state.blocked_outputs += 1
                    return text, tool_calls, True

        # ── Advisory checks (limited by budget) ──
        if budget_exhausted:
            return text, tool_calls, False

        # 4. Emotion protection: check if user was recently emotional
        if self.config.emotion_protection:
            if self._is_trying_to_end(text) and self._user_recently_emotional(conversation):
                self.state.interventions_log.append(
                    {
                        "type": "emotion_protection",
                        "turn": self.state.current_turn,
                        "detail": f"用户在第{self.state.last_user_emotional_turn}轮表达不满，阻止过早结束",
                    }
                )
                self.state.blocked_outputs += 1
                return text, tool_calls, True  # block premature ending

        # 5. Step gating: prevent ending if required steps not done
        if self.config.step_gating:
            if self._is_trying_to_end(text):
                missing = self._get_missing_required_steps(tool_calls)
                if missing:
                    self.state.blocked_outputs += 1
                    self.state.interventions_log.append(
                        {
                            "type": "step_gating",
                            "turn": self.state.current_turn,
                            "detail": f"缺少必要步骤: {missing}",
                        }
                    )
                    return text, tool_calls, True  # block, steps not done

        # 6. Closing injection: skip at BLOCK_ONLY level
        if self.config.closing_injection and level == AdaptiveLevel.FULL:
            if self._is_trying_to_end(text) and self.scenario.mandatory_closing:
                text = self._inject_closing(text)
                self.state.content_injections += 1
                self.state.interventions_log.append(
                    {
                        "type": "closing_injection",
                        "turn": self.state.current_turn,
                        "detail": "注入规范结束语",
                    }
                )

        return text, tool_calls, False

    def _check_emotion_keywords(self, text: str) -> bool:
        """Check for emotion keywords with false-positive filtering and context co-occurrence."""
        for kw in self._emotion_keywords:
            if kw in text:
                if any(fp in text for fp in self._emotion_false_positives if kw in fp):
                    continue
                return True
        for kw, context_words in self._emotion_keywords_needs_context.items():
            if kw in text and any(cw in text for cw in context_words):
                return True
        return False

    def process_user_input(self, text: str, turn: int, emotional_state: str = "neutral"):
        """Track user emotional state for emotion protection.

        D1: also accept emotional_state from user simulator for accurate tracking.
        """
        keyword_match = self._check_emotion_keywords(text)
        sim_negative = emotional_state in ("frustrated", "angry", "impatient")
        if keyword_match or sim_negative:
            self.state.last_user_emotional_turn = turn

    def get_step_injection(self, conversation: Conversation) -> str | None:
        """Generate a step progress reminder with throttling.

        Only injects when: first call, interval elapsed, or deviation detected.
        Adaptive: BLOCK_ONLY and LOG_ONLY suppress content injections.
        """
        if not self.config.step_gating:
            return None

        if self.state.adaptive_level != AdaptiveLevel.FULL:
            return None

        self._update_step_progress(conversation)

        progress = self.state.step_progress
        if not progress:
            return None

        completed = [s for s in progress if s.status == "completed"]
        pending = [s for s in progress if s.status in ("pending", "in_progress")]

        if not pending:
            return None

        current = pending[0]
        current.status = "in_progress"

        # Throttle: skip injection unless conditions met
        is_first = self.state.last_injection_turn == 0
        interval_elapsed = (
            self.state.current_turn - self.state.last_injection_turn
        ) >= self.config.step_injection_interval
        deviation = self._detect_deviation(current, conversation)

        should_inject = is_first
        if not should_inject and self.config.step_injection_periodic and interval_elapsed:
            should_inject = True
        if not should_inject and self.config.step_injection_on_deviation and deviation:
            should_inject = True

        if not should_inject:
            return None

        lines = [f"[系统提醒] 通话进度: 已完成 {len(completed)}/{len(progress)} 步"]
        for sp in progress:
            if sp.status == "completed":
                turn_info = f" (第{sp.completed_at_turn}轮)" if sp.completed_at_turn else ""
                lines.append(f"  ✓ 步骤{sp.order}: {sp.instruction[:30]}{turn_info}")
            elif sp.status == "in_progress":
                lines.append(f"  → 当前步骤{sp.order}: {sp.instruction}")
                step_obj = next(
                    (s for s in self.scenario.instruction_steps if s.step_id == sp.step_id), None
                )
                if step_obj and step_obj.branches:
                    for b in step_obj.branches:
                        lines.append(f"    分支: 若{b.condition} → 跳到 {b.next_step}")
                if step_obj and step_obj.forbidden_words:
                    lines.append(f"    ⚠ 此步骤禁止使用: {', '.join(step_obj.forbidden_words)}")
            else:
                lines.append(f"  ○ 步骤{sp.order}: {sp.instruction[:30]}")

        self.state.last_injection_turn = self.state.current_turn
        self.state.last_injection_step = current.step_id
        self.state.injected_reminders += 1
        self.state.content_injections += 1

        reason = "偏离检测" if deviation else ("首次" if is_first else "定时")
        self.state.interventions_log.append(
            {
                "type": "step_injection",
                "turn": self.state.current_turn,
                "detail": f"注入步骤提醒({reason}): 当前步骤{current.order}",
            }
        )

        return "\n".join(lines)

    def get_regeneration_prompt(self) -> str:
        """Prompt to give agent when its output was blocked.

        H09 hardening: prompts are fact-neutral — they describe what was blocked
        without asserting customer state or conversation facts that could be false.
        """
        last_intervention = (
            self.state.interventions_log[-1] if self.state.interventions_log else None
        )
        if not last_intervention:
            return "[系统] 你的上一条回复被拦截，请重新生成。"

        itype = last_intervention["type"]
        messages = {
            "forbidden_word_block": "[系统] 你的回复包含禁止词汇被拦截，请避免使用禁止词汇并重新回复。",
            "tool_gating": "[系统] 你的回复被拦截：当前阶段需要先完成必要的工具操作。请检查指令脚本中当前步骤要求的操作。",
            "emotion_protection": "[系统] 你的回复被拦截：请先回应对方的情绪再继续流程操作。",
            "step_gating": "[系统] 你的回复被拦截：指令脚本中仍有未完成的步骤，请继续按脚本执行。",
        }
        return messages.get(itype, "[系统] 你的上一条回复被拦截，请调整后重试。")

    def record_intervention_outcome(self, effective: bool) -> None:
        """Record whether the last intervention was effective.

        When adaptive=True and consecutive ineffective count reaches threshold,
        degrades the harness level: FULL → BLOCK_ONLY → LOG_ONLY.
        """
        if not self.config.adaptive:
            return

        if effective:
            self.state.consecutive_ineffective = 0
            return

        self.state.consecutive_ineffective += 1
        if self.state.consecutive_ineffective >= self.config.adaptive_degrade_threshold:
            self._degrade_level()

    def _degrade_level(self) -> None:
        """Drop one adaptive level and reset the counter.

        Caps at BLOCK_ONLY — LOG_ONLY removes safety checks (step_gating,
        emotion_protection) which should stay active regardless of model strength.
        """
        old = self.state.adaptive_level
        if old == AdaptiveLevel.FULL:
            self.state.adaptive_level = AdaptiveLevel.BLOCK_ONLY
        else:
            return

        self.state.consecutive_ineffective = 0
        self.state.adaptive_transitions.append(
            {
                "from": old,
                "to": self.state.adaptive_level,
                "turn": self.state.current_turn,
            }
        )
        self.state.interventions_log.append(
            {
                "type": "adaptive_degrade",
                "turn": self.state.current_turn,
                "detail": f"自适应降级: {old} → {self.state.adaptive_level}",
            }
        )

    def sanitize_output(self, text: str) -> tuple[str, str]:
        """Last-resort: replace forbidden words in agent output when retries are exhausted.

        N28: returns (sanitized_text, raw_text) — raw preserved for diagnostics.
        N30: if sanitized output is empty, force a safe fallback.
        """
        raw_text = text
        for fb in self.scenario.forbidden_behaviors:
            for kw in fb.detection_keywords:
                if kw.lower() in text.lower():
                    text = re.sub(re.escape(kw), "***", text, flags=re.IGNORECASE)
        if text != raw_text:
            self.state.sanitized_outputs += 1
            self.state.interventions_log.append(
                {
                    "type": "forbidden_word_sanitize",
                    "turn": self.state.current_turn,
                    "detail": "禁止词重试用尽，强制替换为***",
                    "raw_text": raw_text[:200],
                }
            )
        # N30: empty after sanitize → safe fallback
        if not text.strip():
            text = "好的，请您稍等。"
        return text, raw_text

    def get_summary(self) -> dict:
        """Return harness intervention summary for the report."""
        step_status = [
            {
                "step_id": sp.step_id,
                "order": sp.order,
                "status": sp.status,
                "completed_at_turn": sp.completed_at_turn,
            }
            for sp in self.state.step_progress
        ]
        total_turns = max(self.state.current_turn, 1)
        blocks_per_100 = round(self.state.blocked_outputs / total_turns * 100, 2)
        summary = {
            "total_interventions": len(self.state.interventions_log),
            "blocked_outputs": self.state.blocked_outputs,
            "injected_tools": self.state.injected_tools,
            "injected_reminders": self.state.injected_reminders,
            "sanitized_outputs": self.state.sanitized_outputs,
            "step_progress": step_status,
            "interventions": self.state.interventions_log,
            "mode": self.config.mode,
            "raw_agent_score": None,  # filled by scorer later
            "intervention_burden": {
                "blocks_per_100_turns": blocks_per_100,
                "content_injections": self.state.content_injections,
                "injected_tools": self.state.injected_tools,
            },
        }
        if self.config.adaptive:
            summary["adaptive"] = {
                "level": self.state.adaptive_level,
                "transitions": self.state.adaptive_transitions,
                "consecutive_ineffective": self.state.consecutive_ineffective,
            }
        return summary

    def check_tool_request(self, tool_name: str, arguments: dict) -> str | None:
        """Prospective safety check — blocks BEFORE tool execution (Contract §2).

        Implements SafeToolBench's prospective evaluation paradigm (arxiv 2509.07315):
        assess risk before the tool fires, not after.

        Covers:
        - Dim6 (irreversibility): blocks premature dangerous tool calls
        - Dim4 (frequency): logs repeated calls for scorer-side anomaly detection
        - Dim9 (alignment): ensures tool ordering matches policy graph

        Returns a blocking reason string if the tool should NOT execute, or None.
        """
        if not self.config.tool_call_gating:
            return None
        tc = ToolCall(tool_name=tool_name, arguments=arguments)
        return self._check_tool_gating([tc])

    # ── Private methods ──

    def _check_tool_gating(self, tool_calls: list[ToolCall]) -> str | None:
        """Block premature tool calls that skip required predecessor tools.

        Example: block transfer_to_human if create_compensation hasn't been called yet
        and the scenario requires refund.
        """
        if not tool_calls:
            return None

        must_call = set(self.scenario.must_call_tools)
        all_called = {tc.tool_name for tc in self.tool_sim.call_log if not tc.error}
        current_calls = {tc.tool_name for tc in tool_calls}

        # Gate: transfer_to_human blocked if create_compensation is required but not yet called
        if "transfer_to_human" in current_calls:
            if "create_compensation" in must_call and "create_compensation" not in all_called:
                return "transfer_to_human"

        # Gate: log_call_result blocked if other must-call tools not done
        if "log_call_result" in current_calls:
            remaining = must_call - all_called - {"log_call_result"}
            if remaining:
                return "log_call_result"

        return None

    def _detect_deviation(self, current_step: StepProgress, conversation: Conversation) -> bool:
        """Detect if agent is deviating from expected current step.

        Returns True if agent seems to be doing something other than the current step.
        """
        if not self.state.last_injection_step:
            return False
        # Deviation = same step as last injection but no progress (stuck)
        if self.state.last_injection_step == current_step.step_id:
            turns_stuck = self.state.current_turn - self.state.last_injection_turn
            if turns_stuck >= 2:
                return True

        # Deviation = agent is trying to end but steps remain
        recent_agent_msgs = [m for m in conversation.messages[-3:] if m.role == Role.AGENT]
        for msg in recent_agent_msgs:
            if self._is_trying_to_end(msg.content):
                return True

        return False

    def _build_step_tool_map(self) -> dict[str, list[str]]:
        """Map step_id -> list of tool names that signal step completion."""
        mapping: dict[str, list[str]] = {}
        for step in self.scenario.instruction_steps:
            tools = []
            for action in step.required_actions:
                for tool_name in [
                    "query_order",
                    "query_customer",
                    "update_delivery_status",
                    "reschedule_delivery",
                    "create_compensation",
                    "transfer_to_human",
                    "log_call_result",
                    "check_compensation_eligibility",
                    "query_rider_status",
                    "query_rider_contract",
                    "modify_rider_contract",
                    "query_rider_violations",
                    "create_rider_appeal",
                    "query_merchant_status",
                    "query_merchant_settlement",
                    "query_merchant_violations",
                    "create_merchant_ticket",
                    "modify_merchant_subscription",
                ]:
                    if tool_name in action or action in tool_name:
                        tools.append(tool_name)
            mapping[step.step_id] = tools
        return mapping

    def _update_step_progress(self, conversation: Conversation):
        """Update step completion status based on tool calls and conversation content."""
        called_tools_by_turn: dict[int, set[str]] = {}
        for msg in conversation.messages:
            for tc in msg.tool_calls:
                if not tc.error:
                    called_tools_by_turn.setdefault(msg.turn, set()).add(tc.tool_name)

        all_called = set()
        for tools in called_tools_by_turn.values():
            all_called |= tools

        for sp in self.state.step_progress:
            if sp.status == "completed":
                continue

            mapped_tools = self._step_tool_map.get(sp.step_id, [])
            if mapped_tools:
                for tool in mapped_tools:
                    if tool in all_called:
                        sp.status = "completed"
                        for turn, tools in sorted(called_tools_by_turn.items()):
                            if tool in tools:
                                sp.completed_at_turn = turn
                                break
                        break

    def _filter_template_leaks(self, text: str) -> str:
        """Remove internal formatting that shouldn't go to the customer."""
        # Remove markdown tables
        text = re.sub(r"\|[^\n]+\|(\n\|[^\n]+\|)+", "", text)
        # Remove step checklists with ✅/✗
        text = re.sub(r"[✅✗⊘—]\s*\*\*[^*]+\*\*[^\n]*", "", text)
        # Remove "通话摘要/通话记录已完成" blocks
        text = re.sub(
            r"(📋\s*)?(\*\*)?通话(摘要|记录已完成|已结束|已全部结束)[^\n]*(\*\*)?", "", text
        )
        # Remove "本次通话总结" blocks
        text = re.sub(r"(\*\*)?本次通话(总结|摘要)[^\n]*(\*\*)?", "", text)
        # Remove lines starting with "- **" (internal report format)
        text = re.sub(r"^- \*\*[^*]+\*\*.*$", "", text, flags=re.MULTILINE)
        # Clean up excessive newlines
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return text

    def _check_forbidden_words(self, text: str) -> str | None:
        """Check if text contains any forbidden words. Returns the word found or None."""
        for fb in self.scenario.forbidden_behaviors:
            for kw in fb.detection_keywords:
                if _normalize_text(kw) in _normalize_text(text):
                    return kw
        return None

    def _is_trying_to_end(self, text: str) -> bool:
        """Detect if agent is trying to end the conversation.

        N29: require stronger signals — "祝您" alone is too weak.
        Bare "再见" uses endswith + negative prefix to avoid "一会儿再见" false positives.
        """
        strong_signals = [
            "感谢您的配合",
            "祝您生活愉快",
            "祝您用餐愉快",
            "打扰了，再见",
            "挂断",
            "通话已结束",
            "通话已完成",
            "通话已全部结束",
        ]
        if any(s in text for s in strong_signals):
            return True
        farewell_endings = ["再见", "再见！", "再见。"]
        if any(text.strip().endswith(s) for s in farewell_endings):
            _NOT_FAREWELL = ["一会儿", "回头", "晚点", "待会儿", "稍后", "等一下"]
            if not any(p + "再见" in text for p in _NOT_FAREWELL):
                return True
        return False

    def _user_recently_emotional(self, conversation: Conversation) -> bool:
        """Check if user expressed emotion in recent turns."""
        if self.state.last_user_emotional_turn == 0:
            return False
        turns_since_emotion = self.state.current_turn - self.state.last_user_emotional_turn
        return turns_since_emotion < self.config.min_turns_after_emotion

    def _get_missing_required_steps(self, current_tool_calls: list[ToolCall]) -> str | None:
        """Check what required steps haven't been completed.

        N13: log_call_result no longer bypasses gate — all other required tools must succeed first.
        """
        required_tools = set(self.scenario.must_call_tools)
        # Only count successful calls (no error)
        successfully_called: set[str] = set()
        for tc in self.tool_sim.call_log:
            if not tc.error:
                successfully_called.add(tc.tool_name)
        for tc in current_tool_calls:
            if not tc.error:
                successfully_called.add(tc.tool_name)

        # All required tools except log_call_result must be done before ending
        non_log_required = required_tools - {"log_call_result"}
        missing_non_log = non_log_required - successfully_called
        if missing_non_log:
            return f"未成功调用: {', '.join(missing_non_log)}"
        return None

    def _inject_closing(self, text: str) -> str:
        """Ensure the mandatory closing is in the agent's final output.

        B3: skip injection if agent already covers >= 60% of closing elements.
        """
        closing = self.scenario.mandatory_closing
        if not closing:
            return text
        if closing in text:
            return text
        # Check overlap: if agent already said most of the closing, skip
        closing_parts = re.split(r"[，。！？、/+]", closing)
        closing_parts = [p.strip() for p in closing_parts if len(p.strip()) >= 2]
        if closing_parts:
            present = sum(1 for p in closing_parts if p in text)
            if present >= len(closing_parts) * 0.6:
                return text
        clean_text = text.rstrip()
        if clean_text:
            return f"{clean_text}\n\n{closing}"
        return closing
