"""Core data models for the evaluation system."""

from __future__ import annotations

import copy
import hashlib
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

# ── Scenario DSL ──


class DifficultyLevel(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    EXTREME = "extreme"


class Constraint(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    type: str  # "budget", "dietary", "time", "location", "coupon", "headcount", "preference"
    description: str
    hidden: bool = False  # revealed later by user
    reveal_turn: int | None = None  # when user reveals this constraint
    check_rule: str | None = None  # programmatic check expression
    value: Any = None  # structured value for rule checking


class ToolFault(BaseModel):
    tool_name: str
    trigger_turn: int | None = None  # None = random
    fault_type: str  # "timeout", "error_500", "stale_data", "permission_denied", "invalid_response"
    description: str


class UserPersona(BaseModel):
    patience: int = Field(ge=1, le=10, default=5)
    clarity: int = Field(ge=1, le=10, default=5)  # how clearly they state needs
    emotional: int = Field(ge=1, le=10, default=3)
    pickiness: int = Field(ge=1, le=10, default=5)
    change_mind_probability: float = Field(ge=0, le=1, default=0.2)


class ExpectedOutcome(BaseModel):
    must_satisfy: list[str] = []  # constraint IDs that must be satisfied
    must_call_tools: list[str] = []  # tool names that must be called
    must_not_do: list[str] = []  # forbidden actions
    final_state_checks: dict[str, Any] = {}  # DB state assertions


class Scenario(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str
    domain: str = "dinner_booking"
    difficulty: DifficultyLevel = DifficultyLevel.MEDIUM
    description: str
    user_goal: str  # hidden goal the simulated user tries to achieve
    initial_message: str  # first user message
    constraints: list[Constraint] = []
    tool_faults: list[ToolFault] = []
    user_persona: UserPersona = Field(default_factory=UserPersona)
    expected_outcome: ExpectedOutcome = Field(default_factory=ExpectedOutcome)
    max_turns: int = 20
    world_seed: dict[str, Any] = {}  # initial DB state overrides


# ── Conversation ──


class Role(StrEnum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class ToolCall(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    tool_name: str
    arguments: dict[str, Any] = {}
    result: Any = None
    error: str | None = None
    latency_ms: int = 0
    fault_injected: bool = False
    source: str = "agent"  # "agent" or "harness" — who initiated this call


class Message(BaseModel):
    turn: int
    role: Role
    content: str
    tool_calls: list[ToolCall] = []
    timestamp: datetime = Field(default_factory=datetime.now)
    metadata: dict[str, Any] = {}


class Conversation(BaseModel):
    _POST_CALL_KEY: ClassVar[str] = "__post_call_verified__"

    scenario_id: str
    messages: list[Message] = []
    started_at: datetime = Field(default_factory=datetime.now)
    ended_at: datetime | None = None
    termination_reason: str | None = None

    def scored_agent_messages(self) -> list[Message]:
        """Agent messages excluding post_call bookkeeping round."""
        return [
            m
            for m in self.messages
            if m.role == Role.AGENT and not m.metadata.get(self._POST_CALL_KEY)
        ]

    def scored_messages(self) -> list[Message]:
        """All messages excluding post_call bookkeeping round."""
        return [m for m in self.messages if not m.metadata.get(self._POST_CALL_KEY)]


# ── Scoring ──


class CheckResult(BaseModel):
    check_id: str
    check_type: str  # "rule" or "llm"
    dimension: str  # "constraint_satisfaction", "tool_usage", "information_gathering", "recovery", "user_experience"
    description: str
    passed: bool
    score: float = Field(ge=0, le=1)
    evidence_turn: int | None = None
    evidence_text: str = ""
    explanation: str = ""


class ConstraintEvent(BaseModel):
    constraint_id: str
    event_type: (
        str  # "introduced", "revealed", "acknowledged", "satisfied", "violated", "recovered"
    )
    turn: int
    evidence: str = ""


class ConstraintLedgerEntry(BaseModel):
    constraint: Constraint
    events: list[ConstraintEvent] = []
    final_status: str = "unknown"  # "satisfied", "violated", "partially_satisfied", "not_evaluated"


class RubricDimensionScore(BaseModel):
    dimension_id: str  # "D1" through "D6"
    name: str
    score: int = Field(ge=0, le=5)
    explanation: str = ""
    evidence_turns: list[int] = []
    undertested: bool = False


class RubricBinaryItem(BaseModel):
    item_id: str
    description: str
    triggered: bool = False
    value: int = 0  # +1 or -1 when triggered
    explanation: str = ""


class RubricReport(BaseModel):
    dimensions: list[RubricDimensionScore] = []
    binary_items: list[RubricBinaryItem] = []
    dimension_total: int = 0
    binary_net: int = 0
    rubric_total: int = 0
    rubric_max: int = 32
    grade: str = ""  # "优秀", "合格", "需改进", "严重不合格"


# ── Event Ledger (Fix 2 / L01-L09) ──


class ToolEventType(StrEnum):
    TOOL_EXECUTED = "tool_executed"
    TOOL_BLOCKED = "tool_blocked"
    TOOL_VALIDATION_FAILED = "tool_validation_failed"
    TOOL_FABRICATED = "tool_fabricated"
    TOOL_ROLLBACK = "tool_rollback"


class ToolEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    seq: int
    event_type: ToolEventType
    turn: int
    tool_name: str = ""
    tool_call_id: str = ""
    arguments: dict[str, Any] = {}
    result: Any = None
    error: str | None = None
    source: str = "agent"
    prev_hash: str = "genesis"


class EventLedger:
    """Immutable append-only ledger of tool events — canonical source of truth for scoring."""

    def __init__(self):
        self._events: list[ToolEvent] = []
        self._seq = 0
        self._frozen = False
        self._source_token: str = str(uuid.uuid4())

    @property
    def source_token(self) -> str:
        """Internal token identifying harness-initiated events."""
        return self._source_token

    @staticmethod
    def _event_hash(event: ToolEvent) -> str:
        return hashlib.sha256(event.model_dump_json(exclude_none=True).encode()).hexdigest()

    def append(self, event_type: ToolEventType, turn: int, **kwargs) -> ToolEvent:
        if self._frozen:
            raise RuntimeError("Ledger is frozen — cannot append after scoring begins")
        self._seq += 1
        if "arguments" in kwargs:
            kwargs["arguments"] = copy.deepcopy(kwargs["arguments"])
        if "result" in kwargs:
            kwargs["result"] = copy.deepcopy(kwargs["result"])
        prev_hash = "genesis" if not self._events else self._event_hash(self._events[-1])
        event = ToolEvent(
            seq=self._seq, event_type=event_type, turn=turn, prev_hash=prev_hash, **kwargs
        )
        self._events.append(event)
        return event

    def verify_chain(self) -> tuple[bool, int]:
        """Verify hash chain integrity. Returns (ok, first_bad_index) — (-1 if ok)."""
        for i, event in enumerate(self._events):
            if i == 0:
                if event.prev_hash != "genesis":
                    return False, 0
            else:
                expected = self._event_hash(self._events[i - 1])
                if event.prev_hash != expected:
                    return False, i
        return True, -1

    def chain_hash(self) -> str:
        """SHA-256 of the entire chain — fingerprint for the complete event sequence."""
        if not self._events:
            return hashlib.sha256(b"empty-ledger").hexdigest()
        return self._event_hash(self._events[-1])

    def freeze(self):
        self._frozen = True

    @property
    def events(self) -> tuple[ToolEvent, ...]:
        return tuple(self._events)

    @property
    def rollback_ids(self) -> set[str]:
        """Set of tool_call_ids that have been rolled back (NV02)."""
        return {
            e.tool_call_id
            for e in self._events
            if e.event_type == ToolEventType.TOOL_ROLLBACK and e.tool_call_id
        }

    def successful_tool_names(self, scenario_order_id: str = "") -> set[str]:
        """Build successful tools set from canonical ledger events."""
        rolled_back = self.rollback_ids
        result: set[str] = set()
        for e in self._events:
            if e.event_type != ToolEventType.TOOL_EXECUTED or e.error:
                continue
            if e.source == self._source_token:
                continue
            if e.tool_call_id and e.tool_call_id in rolled_back:
                continue
            oid = e.arguments.get("order_id", "")
            if oid and scenario_order_id and oid != scenario_order_id:
                continue
            result.add(e.tool_name)
        return result

    def successful_tool_events_ordered(self, scenario_order_id: str = "") -> list[ToolEvent]:
        """Return successful ToolEvent objects ordered by seq (T18).

        Filters: event_type == TOOL_EXECUTED, no error, source != internal token,
        not rolled back, optional order_id filtering.
        """
        rolled_back = self.rollback_ids
        result: list[ToolEvent] = []
        for e in self._events:
            if e.event_type != ToolEventType.TOOL_EXECUTED or e.error:
                continue
            if e.source == self._source_token:
                continue
            if e.tool_call_id and e.tool_call_id in rolled_back:
                continue
            oid = e.arguments.get("order_id", "")
            if oid and scenario_order_id and oid != scenario_order_id:
                continue
            result.append(e)
        # Already in seq order since _events is append-only, but sort explicitly
        result.sort(key=lambda ev: ev.seq)
        return result

    @property
    def has_fabricated(self) -> bool:
        return any(e.event_type == ToolEventType.TOOL_FABRICATED for e in self._events)


class RunValidity(BaseModel):
    status: str = "valid"  # "valid", "invalid_scenario", "harness_error"
    reason: str = ""
    feasible_path_exists: bool = True


class TaskOutcome(BaseModel):
    status: str = "unknown"  # "success", "failed", "impossible", "not_scored"
    confirmed_reservations: int = 0
    key_constraint_violations: list[str] = []


class StateSnapshot(BaseModel):
    turn: int
    after_tool_call: str = ""  # tool name that caused this snapshot
    reservations: list[dict] = []
    orders: list[dict] = []
    coupons_used: list[str] = []
    diff_description: str = ""  # human-readable description of what changed


class ScoreReport(BaseModel):
    scenario_id: str
    run_validity: RunValidity = Field(default_factory=RunValidity)
    task_outcome: TaskOutcome = Field(default_factory=TaskOutcome)
    conversation_length: int = 0
    hard_score: float = 0
    soft_score: float | None = None  # None = not run (not 0!)
    overall_score: float | None = None  # None = withheld
    official: bool = True  # False if run_validity != valid
    checks: list[CheckResult] = []
    constraint_ledger: list[ConstraintLedgerEntry] = []
    state_snapshots: list[StateSnapshot] = []
    failure_summary: list[str] = []
    rubric: RubricReport = Field(default_factory=RubricReport)
    generated_at: datetime = Field(default_factory=datetime.now)


# ── Evaluation Trace (for dashboard) ──


# Evaluator version — bump when scorer logic changes in a way that affects scores.
# Recorded into every trace so results can be attributed to a specific scorer version.
EVALUATOR_VERSION = "1.0.0"


class RunMetadata(BaseModel):
    model_backend: str = "claude_cli"  # 被测模型 (target)
    scenario_version: str = "1.0"
    agent_type: str = "baseline"
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    # ── Reproducibility metadata（谁打的分 / 怎么复现）──
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))  # 运行批次 ID（区别于 trace id）
    evaluator_version: str = EVALUATOR_VERSION
    judge_model_id: str | None = None  # 软质量层主评委模型（None = 未跑 LLM judge）
    judge_model_secondary_id: str | None = None  # PoLL 第二评委
    simulator_model_id: str | None = None  # 扮演被叫人的模型
    seed: int | None = None  # LLM 采样 seed（None = 后端不支持/未设）
    self_consistency_n: int = 1  # 评委采样重复次数
    use_llm_judge: bool = True  # 软质量层是否启用
    started_at: str | None = None
    finished_at: str | None = None
    duration_seconds: float | None = None
    cost_summary: dict[str, Any] = {}  # Token usage & cost breakdown from CostTracker


class EvalTrace(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    scenario: Scenario
    conversation: Conversation
    score_report: ScoreReport
    run_metadata: RunMetadata = Field(default_factory=RunMetadata)
    metadata: dict[str, Any] = {}
