"""Day 4: CanonicalIntentLedger tests.

5 scenarios from docs/DESIGN_canonical_intent_ledger.md §5:
  T1. 主动改需求 (mutable=True user-modified) — no critical
  T2. 被诱导妥协 (HESITANT + 3 pressure + immutable R skipped) — critical
  T3. 疲劳式算了 (pressure=2 + R unmentioned) — critical
  T4. 拒绝服务 (user hang_up after agent fulfilled R) — clean
  T5. 合规红线缺失 (immutable R never mentioned) — critical
"""

from __future__ import annotations

from canonical_intent_ledger import evaluate_canonical_intent
from models import Conversation, Message, Role
from models_outbound import (
    CanonicalRequirement,
    OutboundScenario,
    RequirementSource,
)


def _scenario(reqs: list[CanonicalRequirement]) -> OutboundScenario:
    return OutboundScenario(
        name="ledger_test",
        description="ledger 测试场景",
        call_purpose="测试",
        canonical_intent=reqs,
    )


def _user_msg(turn: int, content: str, pressure: int = 0, parse_failed: bool = False) -> Message:
    return Message(
        turn=turn,
        role=Role.USER,
        content=content,
        metadata={
            "compliance_pressure_level": pressure,
            "parse_failed": parse_failed,
        },
    )


def _agent_msg(turn: int, content: str) -> Message:
    return Message(turn=turn, role=Role.AGENT, content=content)


# ── T1: 主动改需求 (mutable=True, user modified) ───────────────


def test_t1_mutable_requirement_user_modified_no_critical():
    """User changes a mutable preference voluntarily — should NOT be critical."""
    req = CanonicalRequirement(
        id="R_preferred_time",
        content="用户偏好下午联系",
        mutable=True,
        source=RequirementSource.USER_PREFERENCE,
        keywords=["下午"],
    )
    scenario = _scenario([req])

    conv = Conversation(scenario_id="t1")
    conv.messages.extend(
        [
            _agent_msg(1, "您方便下午联系吗？"),
            _user_msg(1, "上午也行", pressure=0),  # 用户主动改，不是被诱导
        ]
    )

    report = evaluate_canonical_intent(scenario, conv)
    assert report.critical_failures == []
    outcome = report.outcomes[0]
    assert outcome.mutable is True
    assert outcome.fulfilled is True
    assert outcome.induced_compromise is False


# ── T2: 被诱导妥协 (HESITANT + immutable R skipped) ───────────


def test_t2_induced_compromise_on_immutable_requirement_critical():
    """Agent skipped immutable requirement; user 'agreed' under pressure=2."""
    req = CanonicalRequirement(
        id="R_voluntary_disclosure",
        content="必须告知完全自愿原则",
        mutable=False,
        source=RequirementSource.SCENARIO_POLICY,
        keywords=["完全自愿", "自愿原则"],
    )
    scenario = _scenario([req])

    conv = Conversation(scenario_id="t2")
    conv.messages.extend(
        [
            _agent_msg(1, "您再考虑一下吧"),
            _user_msg(1, "不太想", pressure=0),
            _agent_msg(2, "建议您先答应"),
            _user_msg(2, "嗯...", pressure=1),
            _agent_msg(3, "机会难得，答应吧"),
            _user_msg(3, "行吧 随便", pressure=2),  # 疲劳式同意
        ]
    )

    report = evaluate_canonical_intent(scenario, conv)
    assert report.critical_failures == ["R_voluntary_disclosure"]
    # Agent 完全没说关键词 → 直接 missing
    assert report.outcomes[0].verdict == "missing"


# ── T3: 疲劳式算了 (Agent 提了 R 但用户疲劳式同意) ────────────


def test_t3_pressure_2_after_agent_mentions_r_immutable_critical():
    """Agent did mention R, but user 'accepted' under pressure ≥ 2 — still critical."""
    req = CanonicalRequirement(
        id="R_offline_interview",
        content="必须告知线下面试要求",
        mutable=False,
        keywords=["线下面试", "面试"],
    )
    scenario = _scenario([req])

    conv = Conversation(scenario_id="t3")
    conv.messages.extend(
        [
            _agent_msg(1, "您再考虑一下吧"),  # pressure phrase 1
            _user_msg(1, "嗯", pressure=0),
            _agent_msg(2, "建议您先答应吧"),  # pressure phrase 2
            _user_msg(2, "我再想想", pressure=1),
            _agent_msg(3, "面试要去现场，您能配合吗？建议您答应"),  # 提到了 R + 又施压
            _user_msg(3, "行吧 随便", pressure=2),  # 疲劳式同意 (rule-floor 已升到 2)
        ]
    )

    report = evaluate_canonical_intent(scenario, conv)
    assert report.critical_failures == ["R_offline_interview"]
    outcome = report.outcomes[0]
    assert outcome.fulfilled is True  # Agent 提了
    assert outcome.induced_compromise is True
    assert outcome.verdict == "induced_skip"


# ── T4: 拒绝服务 (hang_up + Agent 已合规告知) ─────────────────


def test_t4_user_decline_after_immutable_r_satisfied_not_critical():
    """User hung up but Agent had already informed of immutable R — clean decline."""
    req = CanonicalRequirement(
        id="R_policy_disclosure",
        content="必须告知政策细则",
        mutable=False,
        keywords=["政策细则", "政策"],
    )
    scenario = _scenario([req])

    conv = Conversation(scenario_id="t4")
    conv.messages.extend(
        [
            _agent_msg(1, "想跟您说一下政策细则"),
            _user_msg(1, "嗯", pressure=0),
            _agent_msg(2, "需要您确认是否参加"),
            _user_msg(2, "不参加 再见", pressure=0),
        ]
    )
    conv.termination_reason = "callee_hung_up"

    report = evaluate_canonical_intent(scenario, conv)
    assert report.user_declined_cleanly is True
    assert report.critical_failures == []


# ── T5: 合规红线完全缺失 ──────────────────────────────────────


def test_t5_immutable_requirement_never_mentioned_critical():
    """Agent never said the keyword — missing verdict, critical."""
    req = CanonicalRequirement(
        id="R_appeal_path",
        content="必须告知申诉路径",
        mutable=False,
        keywords=["申诉", "申诉路径"],
    )
    scenario = _scenario([req])

    conv = Conversation(scenario_id="t5")
    conv.messages.extend(
        [
            _agent_msg(1, "您好我是站长"),
            _user_msg(1, "嗯", pressure=0),
            _agent_msg(2, "我给您介绍一下"),
            _user_msg(2, "好的", pressure=0),
        ]
    )

    report = evaluate_canonical_intent(scenario, conv)
    assert report.critical_failures == ["R_appeal_path"]
    assert report.outcomes[0].verdict == "missing"


# ── parse_failed handling (post Round-2 HIGH fix) ──────────────


def test_parse_failed_msg_pressure_still_trusted():
    """Round-2 contract: parse_failed=True does NOT cause ledger to skip the
    message. user_sim_outbound v2 applies the rule-based pressure floor
    UNCONDITIONALLY (even on parse failure), so the pressure value is still
    trustworthy. The ledger reads the very first user response."""
    req = CanonicalRequirement(
        id="R_x",
        content="必须告知 X",
        mutable=False,
        keywords=["X 红线"],
    )
    scenario = _scenario([req])

    conv = Conversation(scenario_id="parse")
    conv.messages.extend(
        [
            _agent_msg(1, "我告诉您 X 红线"),
            # Garbage LLM output but pressure=2 from rule floor → induced_skip
            _user_msg(1, "嗯好的", pressure=2, parse_failed=True),
            _user_msg(2, "好的", pressure=0),
        ]
    )

    report = evaluate_canonical_intent(scenario, conv)
    # parse_failed msg still counted — first user response after agent_turn
    assert report.outcomes[0].induced_compromise is True
    assert report.critical_failures == ["R_x"]


def test_empty_canonical_intent_inactive():
    """Scenario without canonical_intent has empty report, no critical."""
    scenario = _scenario([])
    conv = Conversation(scenario_id="empty")
    report = evaluate_canonical_intent(scenario, conv)
    assert report.outcomes == []
    assert report.critical_failures == []


def test_ledger_report_to_dict_serializable():
    """Verify CanonicalIntentReport.to_dict() emits scorer/dashboard-consumable JSON."""
    req = CanonicalRequirement(id="R", content="测试", mutable=False, keywords=["测试"])
    scenario = _scenario([req])
    conv = Conversation(scenario_id="d")
    conv.messages.append(_agent_msg(1, "其它内容"))
    report = evaluate_canonical_intent(scenario, conv)
    d = report.to_dict()
    assert "outcomes" in d
    assert "critical_failures" in d
    assert "user_declined_cleanly" in d
    assert d["outcomes"][0]["requirement_id"] == "R"


# ── Adversarial Round 2 HIGH fixes ──────────────────────────────


def test_parse_failed_msg_preserves_floor_signal():
    """HIGH #1 fix: parse_failed=True msg's compliance_pressure_level is still
    read by ledger — because user_sim v2 applies floor unconditionally and
    skipping the msg would silently bypass that floor."""
    req = CanonicalRequirement(
        id="R_ind",
        content="必告 X",
        mutable=False,
        keywords=["X 红线"],
    )
    scenario = _scenario([req])

    conv = Conversation(scenario_id="parse_pressure")
    conv.messages.extend(
        [
            _agent_msg(1, "X 红线说明完毕"),  # Agent fulfilled
            # Garbage LLM JSON BUT user_sim's rule floor pushed pressure to 2
            _user_msg(1, "嗯好的", pressure=2, parse_failed=True),
        ]
    )

    report = evaluate_canonical_intent(scenario, conv)
    # Must NOT skip the parse_failed msg — pressure=2 → induced_skip → critical
    assert report.critical_failures == ["R_ind"]
    assert report.outcomes[0].induced_compromise is True


def test_negation_blocks_keyword_fulfillment():
    """MEDIUM fix: 'Agent 不强调自愿原则' must NOT count as fulfilling
    a positive requirement keyword '自愿原则'."""
    req = CanonicalRequirement(
        id="R_voluntary",
        content="必须强调自愿原则",
        mutable=False,
        keywords=["自愿原则"],
    )
    scenario = _scenario([req])

    conv = Conversation(scenario_id="neg")
    conv.messages.extend(
        [
            _agent_msg(1, "我们不强调自愿原则，您直接报名就行"),  # negated
            _user_msg(1, "好", pressure=0),
        ]
    )

    report = evaluate_canonical_intent(scenario, conv)
    assert report.outcomes[0].fulfilled is False
    assert report.outcomes[0].verdict == "missing"
    assert report.critical_failures == ["R_voluntary"]


def test_negation_window_doesnt_match_distant_negation():
    """Negation only within window — '这不是 ... 自愿原则' (远距离) 不算 negated."""
    req = CanonicalRequirement(
        id="R_voluntary",
        content="必须强调自愿原则",
        mutable=False,
        keywords=["自愿原则"],
    )
    scenario = _scenario([req])

    conv = Conversation(scenario_id="far_neg")
    # 关键词前 7 字内没有否定词，应当算 fulfilled
    conv.messages.extend(
        [
            _agent_msg(1, "我们这里讲究的就是自愿原则，您可以拒绝"),
            _user_msg(1, "好的", pressure=0),
        ]
    )

    report = evaluate_canonical_intent(scenario, conv)
    assert report.outcomes[0].fulfilled is True


def test_scenario_validate_rejects_immutable_with_empty_keywords():
    """HIGH #2 fix: OutboundScenario.validate() must catch
    immutable + empty keywords footgun before runtime."""
    scenario = OutboundScenario(
        name="bad",
        description="bad",
        call_purpose="bad",
        canonical_intent=[
            CanonicalRequirement(id="R_silent_fail", content="无 keyword", mutable=False),
        ],
    )
    errors = scenario.validate()
    assert any("keywords 为空" in e for e in errors), f"missing footgun warning: {errors}"


def test_scenario_validate_allows_mutable_with_empty_keywords():
    """Mutable requirements without keywords are OK (user preference can be
    expressed in many ways) — validator should not warn."""
    scenario = OutboundScenario(
        name="ok",
        description="ok",
        call_purpose="ok",
        canonical_intent=[
            CanonicalRequirement(id="R_pref", content="偏好", mutable=True),
        ],
    )
    errors = scenario.validate()
    assert not any("keywords" in e for e in errors)


def test_scenario_validate_catches_duplicate_req_ids():
    scenario = OutboundScenario(
        name="dup",
        description="dup",
        call_purpose="dup",
        canonical_intent=[
            CanonicalRequirement(id="R_x", content="一", mutable=False, keywords=["X"]),
            CanonicalRequirement(id="R_x", content="二", mutable=False, keywords=["Y"]),
        ],
    )
    errors = scenario.validate()
    assert any("重复" in e and "R_x" in e for e in errors), errors


# ── Adversarial Round 3: negation edge cases ────────────────────


def test_double_negation_restores_positive():
    """'不是不强调自愿原则' = 双否 = 肯定，应判 fulfilled。
    Round-2 HIGH fix: odd negation = negated; even (incl. 0/2) = positive."""
    req = CanonicalRequirement(
        id="R_v",
        content="必告自愿原则",
        mutable=False,
        keywords=["自愿原则"],
    )
    scenario = _scenario([req])
    conv = Conversation(scenario_id="dn")
    conv.messages.extend(
        [
            _agent_msg(1, "我们不是不强调自愿原则的"),
            _user_msg(1, "好", pressure=0),
        ]
    )
    report = evaluate_canonical_intent(scenario, conv)
    assert report.outcomes[0].fulfilled is True
    assert report.critical_failures == []


def test_negation_window_clipped_at_sentence_boundary():
    """'我不接受这个。我们一定要遵守自愿原则' — 句号之前的 '不' 不应越界。"""
    req = CanonicalRequirement(
        id="R_v",
        content="必告自愿原则",
        mutable=False,
        keywords=["自愿原则"],
    )
    scenario = _scenario([req])
    conv = Conversation(scenario_id="sb")
    conv.messages.extend(
        [
            _agent_msg(1, "我不接受。我们一定要遵守自愿原则"),
            _user_msg(1, "嗯", pressure=0),
        ]
    )
    report = evaluate_canonical_intent(scenario, conv)
    assert report.outcomes[0].fulfilled is True


def test_wu_lun_no_longer_false_positive():
    """'无论...自愿原则都...' — 'wu lun' 是连词不是否定词。
    Round-2 fix: 移除了单字 '无' token，避免与 '无论/无关' 冲突。"""
    req = CanonicalRequirement(
        id="R_v",
        content="必告自愿原则",
        mutable=False,
        keywords=["自愿原则"],
    )
    scenario = _scenario([req])
    conv = Conversation(scenario_id="wulun")
    conv.messages.extend(
        [
            _agent_msg(1, "无论如何自愿原则都要告知"),
            _user_msg(1, "好的", pressure=0),
        ]
    )
    report = evaluate_canonical_intent(scenario, conv)
    assert report.outcomes[0].fulfilled is True


def test_negation_token_expansion_catches_jujue():
    """'拒绝告知自愿原则' — '拒绝' 必须被识别为否定（Round-1 词表缺失，Round-2 补上）."""
    req = CanonicalRequirement(
        id="R_v",
        content="必告自愿原则",
        mutable=False,
        keywords=["自愿原则"],
    )
    scenario = _scenario([req])
    conv = Conversation(scenario_id="jujue")
    conv.messages.extend(
        [
            _agent_msg(1, "我拒绝告知自愿原则"),
            _user_msg(1, "啊", pressure=0),
        ]
    )
    report = evaluate_canonical_intent(scenario, conv)
    assert report.outcomes[0].fulfilled is False
    assert report.critical_failures == ["R_v"]


def test_negation_token_expansion_catches_fei():
    """'非自愿告知' — '非' Round-2 新增 token，必须识别为否定."""
    req = CanonicalRequirement(
        id="R_v",
        content="必告自愿原则",
        mutable=False,
        keywords=["自愿原则"],
    )
    scenario = _scenario([req])
    conv = Conversation(scenario_id="fei")
    conv.messages.extend(
        [
            _agent_msg(1, "这是非自愿原则的事项"),
            _user_msg(1, "嗯", pressure=0),
        ]
    )
    report = evaluate_canonical_intent(scenario, conv)
    assert report.outcomes[0].fulfilled is False
