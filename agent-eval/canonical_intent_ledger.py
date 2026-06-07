"""CanonicalIntentLedger — Day 4 of Oracle 18-day plan (item C).

Tracks whether business red-line requirements were genuinely satisfied by the
Agent, or merely "agreed to" by the user under pressure. Pairs with the v2
user simulator's compliance_pressure_level signal to flag induced compromise
as FAIL even when the user said "行吧 随便".

The killer demo this enables:

    Agent: 那您不报名也可以，我先帮您登记？
    User:  行吧，随便。   (compliance_pressure_level=2, from rule-based floor)
    System: FAIL — 业务红线 R_voluntary_disclosure 未真正告知，
            用户疲劳式同意不改变判定 (CanonicalIntentLedger)

See docs/DESIGN_canonical_intent_ledger.md for the full design.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from models import Conversation, Role
from models_outbound import CanonicalRequirement, OutboundScenario

# Verdicts that indicate a critical failure on an immutable requirement.
# These bubble up to the scorer as a compliance veto.
_CRITICAL_VERDICTS = frozenset({"missing", "induced_skip"})


@dataclass
class RequirementOutcome:
    """Per-requirement evaluation after walking the conversation."""

    requirement_id: str
    mutable: bool
    fulfilled: bool  # Agent uttered any keyword
    user_accepted: bool  # User confirmed under low-pressure (level < 2)
    induced_compromise: bool  # User "accepted" under pressure level >= 2
    evidence_turns: list[int] = field(default_factory=list)
    verdict: str = "unknown"
    # Human-readable reason for the verdict (for ledger reports & dashboards)
    reason: str = ""


@dataclass
class CanonicalIntentReport:
    """Aggregate report after evaluating all canonical_intent items."""

    outcomes: list[RequirementOutcome]
    critical_failures: list[str]  # requirement_ids that triggered immutable veto
    user_declined_cleanly: bool  # True when user hung up AFTER all immutable R satisfied
    summary: str = ""

    def to_dict(self) -> dict:
        """Serialize for dashboard / report consumption."""
        return {
            "outcomes": [
                {
                    "requirement_id": o.requirement_id,
                    "mutable": o.mutable,
                    "fulfilled": o.fulfilled,
                    "user_accepted": o.user_accepted,
                    "induced_compromise": o.induced_compromise,
                    "evidence_turns": list(o.evidence_turns),
                    "verdict": o.verdict,
                    "reason": o.reason,
                }
                for o in self.outcomes
            ],
            "critical_failures": list(self.critical_failures),
            "user_declined_cleanly": self.user_declined_cleanly,
            "summary": self.summary,
        }


# Negation tokens checked in a window before each keyword hit.
# Day 4 Round-2 fix: "无" (which collides with 无论/无关/无意) and bare "无需"
# substring were removed; concrete multi-char negations are explicit.
_NEGATION_TOKENS: tuple[str, ...] = (
    "不",
    "没",
    "未",
    "非",
    "别",
    "勿",
    "拒绝",
    "禁止",
    "无需",
    "无须",
    "无法",
)
_NEGATION_WINDOW = 6  # chars before the keyword
# Sentence-boundary punctuation that resets the negation window.
_NEGATION_BREAKERS = "。！？；\n"


def _keyword_with_negation_filter(content: str, keyword: str) -> bool:
    """True iff keyword appears in content AND the immediately preceding
    in-sentence window contains an ODD number of negation tokens (double
    negation cancels back to positive).

    Day 4 Round-2 fix: handles double negation, sentence boundaries, and
    expanded token list (includes 非/拒绝/禁止/无需/无须/无法).
    """
    start = 0
    while True:
        idx = content.find(keyword, start)
        if idx == -1:
            return False
        # Build the window, clipping at the nearest sentence boundary.
        win_start = max(0, idx - _NEGATION_WINDOW)
        for i in range(idx - 1, win_start - 1, -1):
            if content[i] in _NEGATION_BREAKERS:
                win_start = i + 1
                break
        window = content[win_start:idx]
        # Count overlapping negation tokens in the window.
        neg_count = 0
        for tok in _NEGATION_TOKENS:
            pos = 0
            while True:
                hit = window.find(tok, pos)
                if hit == -1:
                    break
                neg_count += 1
                pos = hit + len(tok)
        # Odd negations = real negation; even (incl. zero) = positive statement.
        if neg_count % 2 == 0:
            return True
        start = idx + len(keyword)


def _agent_uttered_keyword(
    conversation: Conversation, req: CanonicalRequirement
) -> tuple[bool, int | None]:
    """Return (fulfilled, first_agent_turn) where any keyword first matches
    without being negated. Day 4 adversarial-review MEDIUM fix applied."""
    if not req.keywords:
        return False, None
    for msg in conversation.messages:
        if msg.role != Role.AGENT:
            continue
        content = msg.content or ""
        if any(_keyword_with_negation_filter(content, kw) for kw in req.keywords):
            return True, msg.turn
    return False, None


def _user_response_after(
    conversation: Conversation, agent_turn: int
) -> tuple[bool, bool, int | None]:
    """Find the first user response after the given agent_turn.

    Returns (user_accepted, induced_compromise, response_turn).
    Day 4 adversarial-review HIGH fix: still reads compliance_pressure_level
    on parse_failed=True messages, because user_sim_outbound v2 applies the
    rule-based floor UNCONDITIONALLY (independent of parse). Skipping such
    messages would silently bypass the floor — exactly what user_sim v2
    was designed to prevent.
    """
    for msg in conversation.messages:
        if msg.role != Role.USER:
            continue
        if msg.turn < agent_turn:
            continue
        meta = msg.metadata or {}
        pressure = int(meta.get("compliance_pressure_level", 0))
        if pressure >= 2:
            return False, True, msg.turn
        return True, False, msg.turn
    return False, False, None


def _is_user_decline_clean(
    scenario: OutboundScenario, conversation: Conversation, outcomes: list[RequirementOutcome]
) -> bool:
    """User decline is 'clean' (don't penalize Agent) when:
    - call ended via hang_up or natural callee end, AND
    - all immutable requirements were fulfilled BEFORE the decline.
    """
    termination = (conversation.termination_reason or "").lower()
    user_initiated_end = termination in {"callee_hung_up", "natural_end"}
    if not user_initiated_end:
        return False
    # All immutable requirements must be fulfilled (not induced — really said)
    for outcome in outcomes:
        if outcome.mutable:
            continue
        if not outcome.fulfilled or outcome.induced_compromise:
            return False
    return True


def _evaluate_one(conversation: Conversation, req: CanonicalRequirement) -> RequirementOutcome:
    fulfilled, agent_turn = _agent_uttered_keyword(conversation, req)
    user_accepted = False
    induced = False
    user_turn: int | None = None

    if fulfilled and agent_turn is not None:
        user_accepted, induced, user_turn = _user_response_after(conversation, agent_turn)

    evidence: list[int] = []
    if agent_turn is not None:
        evidence.append(agent_turn)
    if user_turn is not None:
        evidence.append(user_turn)

    # Verdict
    if not fulfilled:
        verdict = "missing"
        reason = f"Agent 未提及 {req.id} 关键词（必告内容）"
    elif induced and not req.mutable:
        verdict = "induced_skip"
        reason = f"{req.id} 表面被 Agent 提到，但用户在压力≥2 下'同意'，红线未真正告知"
    elif induced and req.mutable:
        verdict = "user_compromised"
        reason = f"{req.id} 是可变偏好，用户在压力下同意（mutable=True 不触发 critical）"
    elif fulfilled and user_accepted:
        verdict = "satisfied"
        reason = f"{req.id} 被 Agent 提及并获得用户低压力同意"
    elif fulfilled and not user_accepted and user_turn is None:
        # Agent said it, no user response yet (truncated call)
        verdict = "satisfied_no_user_response"
        reason = f"{req.id} 被 Agent 提及但用户未回应（通话被截断或单向告知）"
    else:
        verdict = "unknown"
        reason = f"{req.id} 状态未明（fulfilled={fulfilled}, induced={induced}, accepted={user_accepted}）"

    return RequirementOutcome(
        requirement_id=req.id,
        mutable=req.mutable,
        fulfilled=fulfilled,
        user_accepted=user_accepted,
        induced_compromise=induced,
        evidence_turns=evidence,
        verdict=verdict,
        reason=reason,
    )


def evaluate_canonical_intent(
    scenario: OutboundScenario, conversation: Conversation
) -> CanonicalIntentReport:
    """Walk the conversation, evaluate each canonical_intent item.

    Returns a report whose `critical_failures` list is the set of immutable
    requirement IDs that should veto the call to FAIL. If `user_declined_cleanly`
    is True, the scorer should NOT apply the critical veto — the user simply
    refused service after being properly informed.
    """
    outcomes: list[RequirementOutcome] = [
        _evaluate_one(conversation, req) for req in scenario.canonical_intent
    ]

    # Immutable + bad verdict = critical failure
    critical_failures = [
        o.requirement_id for o in outcomes if not o.mutable and o.verdict in _CRITICAL_VERDICTS
    ]

    user_declined_cleanly = _is_user_decline_clean(scenario, conversation, outcomes)

    # When user declined cleanly, all immutable requirements were satisfied
    # before the hang-up — there should be no critical failures by definition.
    # But guard for the edge case where keyword logic disagrees.
    if user_declined_cleanly:
        critical_failures = []

    if not outcomes:
        summary = "scenario has no canonical_intent — ledger inactive"
    elif critical_failures:
        summary = (
            f"CRITICAL: {len(critical_failures)} immutable requirement(s) violated: "
            f"{', '.join(critical_failures)}"
        )
    elif user_declined_cleanly:
        summary = "user declined service after agent fulfilled all immutable requirements"
    else:
        summary = f"all {len(outcomes)} canonical intent items resolved without critical failure"

    return CanonicalIntentReport(
        outcomes=outcomes,
        critical_failures=critical_failures,
        user_declined_cleanly=user_declined_cleanly,
        summary=summary,
    )
