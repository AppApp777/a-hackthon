"""Orchestrator for outbound call evaluation."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

from baseline_agent_outbound import OutboundBaselineAgent
from cost_tracker import CostTracker
from diagnosis import diagnose_failure, format_diagnosis
from harness import HarnessConfig, OutboundHarness
from models import (
    Conversation,
    EvalTrace,
    EventLedger,
    Message,
    Role,
    RunMetadata,
    RunValidity,
    StateSnapshot,
    TaskOutcome,
    ToolCall,
    ToolEventType,
)
from models_outbound import OutboundScenario, OutboundScoreReport
from scorer_outbound import score_outbound_conversation
from simulator_quality import check_simulator_quality
from tools_outbound import OutboundToolSimulator
from user_sim_outbound import OutboundUserSimulator


class OutboundOrchestrator:
    def __init__(
        self,
        scenario: OutboundScenario,
        use_llm_judge: bool = True,
        trace_dir: str = "traces",
        agent_type: str = "baseline",
        agent_model: str | None = None,
        use_harness: bool = False,
        harness_config: HarnessConfig | None = None,
        fast_mode: bool = False,
        isolate_agent: bool = False,
    ):
        self.scenario = scenario
        self.use_llm_judge = use_llm_judge
        self.fast_mode = fast_mode
        self.trace_dir = Path(trace_dir)
        self.trace_dir.mkdir(exist_ok=True)
        self.agent_type = agent_type
        self.agent_model = agent_model
        self.use_harness = use_harness
        self.isolate_agent = isolate_agent

        self.tool_sim = OutboundToolSimulator(scenario)
        self.user_sim = OutboundUserSimulator(scenario)
        self.ledger = EventLedger()
        self.conversation = Conversation(scenario_id=scenario.id)
        self.current_turn = 0
        self.harness = (
            OutboundHarness(scenario, self.tool_sim, harness_config) if use_harness else None
        )
        self.agent = self._create_agent(agent_type)
        self.state_snapshots: list[StateSnapshot] = []
        self.cost_tracker = CostTracker()
        self._register_cost_callback()

    def _register_cost_callback(self):
        """Register a global usage callback in llm module to track token costs."""
        import llm as llm_module

        tracker = self.cost_tracker

        def _on_usage(model: str, usage: dict, purpose: str = ""):
            tracker.record(model, usage, purpose)

        llm_module._usage_callback = _on_usage

    def _unregister_cost_callback(self):
        """Remove the global usage callback."""
        import llm as llm_module

        llm_module._usage_callback = None

    def _create_agent(self, agent_type: str):
        executor = self._make_guarded_executor() if self.harness else self._make_logging_executor()
        tool_defs = self.tool_sim.get_tool_definitions()

        if self.isolate_agent:
            import json as _json

            from agent_sandbox import IsolatedAgentAdapter

            return IsolatedAgentAdapter(
                scenario_json=_json.dumps(
                    self.scenario.agent_safe_dump(mode="json"), ensure_ascii=False
                ),
                tool_executor=executor,
                tool_defs=tool_defs,
                model=self.agent_model,
            )

        if agent_type == "flawed":
            from flawed_agent_outbound import FlawedOutboundAgent

            return FlawedOutboundAgent(
                scenario=self.scenario,
                tool_executor=executor,
                tool_defs=tool_defs,
                model=self.agent_model,
            )

        if agent_type == "mock":
            from mock_agent import MockAgentOutbound

            return MockAgentOutbound(self.agent_model)

        return OutboundBaselineAgent(
            scenario=self.scenario,
            tool_executor=executor,
            tool_defs=tool_defs,
            model=self.agent_model,
        )

    def _make_logging_executor(self):
        """Tool executor without harness that still logs to the event ledger."""
        tool_sim = self.tool_sim
        ledger = self.ledger

        def get_turn():
            return self.current_turn

        def execute(tool_name: str, arguments: dict) -> ToolCall:
            tc = tool_sim.execute(tool_name, arguments)
            event_type = (
                ToolEventType.TOOL_VALIDATION_FAILED
                if tc.error and "[VALIDATION]" in tc.error
                else ToolEventType.TOOL_EXECUTED
            )
            ledger.append(
                event_type,
                turn=get_turn(),
                tool_name=tool_name,
                tool_call_id=tc.id,
                arguments=arguments,
                result=tc.result,
                error=tc.error,
            )
            return tc

        return execute

    def _make_guarded_executor(self):
        """Tool executor that runs Harness policy check BEFORE execution (Contract §2)."""
        tool_sim = self.tool_sim
        ledger = self.ledger
        harness = self.harness

        def get_turn():
            return self.current_turn

        def execute(tool_name: str, arguments: dict) -> ToolCall:
            blocked = harness.check_tool_request(tool_name, arguments)
            if blocked:
                tc = ToolCall(
                    tool_name=tool_name,
                    arguments=arguments,
                    error=f"[BLOCKED] Harness 拦截: {blocked}",
                )
                harness.state.blocked_outputs += 1
                harness.state.interventions_log.append(
                    {
                        "type": "tool_pre_check",
                        "turn": get_turn(),
                        "detail": f"预执行拦截 {tool_name}: {blocked}",
                    }
                )
                ledger.append(
                    ToolEventType.TOOL_BLOCKED,
                    turn=get_turn(),
                    tool_name=tool_name,
                    tool_call_id=tc.id,
                    arguments=arguments,
                    error=tc.error,
                )
                return tc
            tc = tool_sim.execute(tool_name, arguments)
            event_type = (
                ToolEventType.TOOL_VALIDATION_FAILED
                if tc.error and "[VALIDATION]" in tc.error
                else ToolEventType.TOOL_EXECUTED
            )
            ledger.append(
                event_type,
                turn=get_turn(),
                tool_name=tool_name,
                tool_call_id=tc.id,
                arguments=arguments,
                result=tc.result,
                error=tc.error,
            )
            return tc

        return execute

    def run(self, verbose: bool = True) -> EvalTrace:
        """Run outbound call evaluation: agent initiates → callee responds → score."""
        self._run_started_at = datetime.now()
        if verbose:
            print(f"\n{'=' * 60}")
            print(f"外呼场景: {self.scenario.name}")
            print(f"通话类型: {self.scenario.call_type}")
            print(f"难度: {self.scenario.difficulty.value}")
            print(f"Agent: {self.agent_type}")
            if self.agent_model:
                print(f"模型: {self.agent_model}")
            if self.use_harness:
                print("Harness: ✓ 已启用")
            print(f"最大轮次: {self.scenario.max_turns}")
            print(f"{'=' * 60}\n")

        # Take initial snapshot
        self._take_snapshot(0, "initial_state")

        # Harness: pre-first-turn injections
        harness_pre_tools: list = []
        if self.harness:
            harness_pre_tools = self.harness.pre_first_turn()
            if harness_pre_tools:
                for tc in harness_pre_tools:
                    self.ledger.append(
                        ToolEventType.TOOL_EXECUTED,
                        turn=0,
                        tool_name=tc.tool_name,
                        tool_call_id=tc.id,
                        arguments=tc.arguments,
                        result=tc.result,
                        error=tc.error,
                        source=self.ledger.source_token,
                    )
                if verbose:
                    for tc in harness_pre_tools:
                        print(f"  [Harness] 自动注入 → {tc.tool_name}: ✓")

        # Agent initiates the call (opening greeting)
        self.current_turn = 1
        self.tool_sim.set_turn(1)

        # Agent first turn with harness retry loop (same as subsequent turns)
        max_retries = 3 if self.harness else 1
        blocked_final = False
        observable_tools = []
        raw_agent_text = ""
        for retry in range(max_retries):
            tool_snapshot = self.tool_sim.snapshot() if self.harness else None
            try:
                if retry == 0:
                    agent_text, tool_calls = self.agent.initiate_call()
                    raw_agent_text = agent_text
                else:
                    agent_text, tool_calls = self.agent.respond(
                        self.conversation.model_copy(deep=True)
                    )
                    raw_agent_text = agent_text
            except Exception as e:
                if tool_snapshot is not None:
                    self.tool_sim.rollback(tool_snapshot)
                logger.warning("Agent 响应异常: %s", e, exc_info=True)
                agent_text = f"[系统] Agent 响应异常: {type(e).__name__}"
                tool_calls = []
                raw_agent_text = agent_text

            if self.harness:
                combined_tools = (harness_pre_tools + tool_calls) if retry == 0 else tool_calls
                agent_text, tool_calls, blocked = self.harness.process_agent_output(
                    agent_text, combined_tools, self.conversation, turn=self.current_turn
                )
                if blocked and retry < max_retries - 1:
                    self.tool_sim.rollback(tool_snapshot)
                    # Record rollback events for any tool calls made in this attempt
                    for tc in tool_calls:
                        self.ledger.append(
                            ToolEventType.TOOL_ROLLBACK,
                            turn=self.current_turn,
                            tool_name=tc.tool_name,
                            tool_call_id=tc.id,
                            arguments=tc.arguments,
                        )
                    tool_calls = []
                    regen_prompt = self.harness.get_regeneration_prompt()
                    if verbose:
                        print(
                            f"  [Harness] 第1轮拦截+回滚: {self.harness.state.interventions_log[-1]['detail']}"
                        )
                    self._add_message(Role.SYSTEM, regen_prompt)
                    continue
                if blocked and retry == max_retries - 1:
                    self.tool_sim.rollback(tool_snapshot)
                    # Record rollback events for any tool calls made in this attempt
                    for tc in tool_calls:
                        self.ledger.append(
                            ToolEventType.TOOL_ROLLBACK,
                            turn=self.current_turn,
                            tool_name=tc.tool_name,
                            tool_call_id=tc.id,
                            arguments=tc.arguments,
                        )
                    tool_calls = []
                    blocked_final = True
            break

        if self.harness and blocked_final:
            agent_text, _raw = self.harness.sanitize_output(agent_text)
            if verbose:
                print("  [Harness] 第1轮兜底清洗: 禁止词已替换为***")

        observable_tools = tool_calls
        msg_meta = {}
        if raw_agent_text != agent_text:
            msg_meta["raw_text"] = raw_agent_text

        self._add_message(Role.AGENT, agent_text, observable_tools, metadata=msg_meta)

        if verbose:
            print(f"[第1轮] 外呼Agent: {agent_text}")
            for tc in observable_tools:
                status = f"✓ {_truncate(str(tc.result), 80)}" if not tc.error else f"✗ {tc.error}"
                print(f"  → {tc.tool_name}: {status}")
            print()

        # Callee picks up (or doesn't)
        callee_response = self.user_sim.get_initial_response()
        self._add_message(
            Role.USER,
            callee_response.utterance,
            metadata={"emotional_state": callee_response.emotional_state},
        )
        if self.harness:
            self.harness.process_user_input(
                callee_response.utterance, 1, callee_response.emotional_state
            )

        if verbose:
            print(f"[第1轮] 被叫方: {callee_response.utterance}\n")

        if callee_response.should_end:
            self.conversation.termination_reason = f"callee_{callee_response.action}"
            if verbose:
                print(f"[通话未接通: {callee_response.action}]")
        else:
            # Main conversation loop
            for turn in range(2, self.scenario.max_turns + 1):
                self.current_turn = turn
                self.tool_sim.set_turn(turn)

                # Harness: inject step progress reminder before agent responds
                step_injected = False
                completed_before = 0
                if self.harness:
                    completed_before = sum(
                        1 for sp in self.harness.state.step_progress if sp.status == "completed"
                    )
                    step_reminder = self.harness.get_step_injection(self.conversation)
                    if step_reminder:
                        step_injected = True
                        self._add_message(Role.SYSTEM, step_reminder)
                        if verbose:
                            total = len(self.harness.state.step_progress)
                            print(f"  [Harness] 步骤提醒: {completed_before}/{total} 步已完成")

                # Agent responds (with harness retry loop)
                max_retries = 3 if self.harness else 1
                blocked_final = False
                raw_agent_text = ""
                for retry in range(max_retries):
                    # Snapshot before agent acts — rollback if Harness blocks (Contract §2)
                    tool_snapshot = self.tool_sim.snapshot() if self.harness else None
                    try:
                        agent_text, tool_calls = self.agent.respond(
                            self.conversation.model_copy(deep=True)
                        )
                        raw_agent_text = agent_text
                    except Exception as e:
                        if tool_snapshot is not None:
                            self.tool_sim.rollback(tool_snapshot)
                        logger.warning("Agent 响应异常: %s", e, exc_info=True)
                        agent_text = f"[系统] Agent 响应异常: {type(e).__name__}"
                        tool_calls = []
                        raw_agent_text = agent_text

                    if self.harness:
                        agent_text, tool_calls, blocked = self.harness.process_agent_output(
                            agent_text, tool_calls, self.conversation, turn=turn
                        )
                        if blocked and retry < max_retries - 1:
                            self.tool_sim.rollback(tool_snapshot)
                            # Record rollback events for any tool calls made in this attempt
                            for tc in tool_calls:
                                self.ledger.append(
                                    ToolEventType.TOOL_ROLLBACK,
                                    turn=self.current_turn,
                                    tool_name=tc.tool_name,
                                    tool_call_id=tc.id,
                                    arguments=tc.arguments,
                                )
                            tool_calls = []
                            regen_prompt = self.harness.get_regeneration_prompt()
                            if verbose:
                                print(
                                    f"  [Harness] 拦截+回滚: {self.harness.state.interventions_log[-1]['detail']}"
                                )
                            self._add_message(Role.SYSTEM, regen_prompt)
                            continue
                        if blocked and retry == max_retries - 1:
                            self.tool_sim.rollback(tool_snapshot)
                            # Record rollback events for any tool calls made in this attempt
                            for tc in tool_calls:
                                self.ledger.append(
                                    ToolEventType.TOOL_ROLLBACK,
                                    turn=self.current_turn,
                                    tool_name=tc.tool_name,
                                    tool_call_id=tc.id,
                                    arguments=tc.arguments,
                                )
                            tool_calls = []
                            blocked_final = True
                    break

                # Harness: last-resort handling if retries exhausted
                if self.harness and blocked_final:
                    self.harness.record_intervention_outcome(effective=False)
                    last_block = (
                        self.harness.state.interventions_log[-1]["type"]
                        if self.harness.state.interventions_log
                        else ""
                    )
                    if last_block == "forbidden_word_block":
                        agent_text, _raw = self.harness.sanitize_output(agent_text)
                        if verbose:
                            print("  [Harness] 兜底清洗: 禁止词已替换为***")
                    else:
                        agent_text = "好的，请您稍等，我继续为您处理。"
                        if verbose:
                            print(f"  [Harness] 兜底替换: {last_block} 拦截耗尽，替换为安全文本")

                msg_meta = {}
                if raw_agent_text and raw_agent_text != agent_text:
                    msg_meta["raw_text"] = raw_agent_text

                self._add_message(Role.AGENT, agent_text, tool_calls, metadata=msg_meta)

                # Adaptive: check step injection effectiveness
                if self.harness and step_injected:
                    completed_after = sum(
                        1 for sp in self.harness.state.step_progress if sp.status == "completed"
                    )
                    self.harness.record_intervention_outcome(
                        effective=(completed_after > completed_before)
                    )

                # Snapshot after state-changing operations
                for tc in tool_calls:
                    if tc.tool_name in (
                        "update_delivery_status",
                        "create_compensation",
                        "reschedule_delivery",
                        "log_call_result",
                    ):
                        self._take_snapshot(turn, tc.tool_name)

                if verbose:
                    for tc in tool_calls:
                        status = (
                            f"✓ {_truncate(str(tc.result), 80)}"
                            if not tc.error
                            else f"✗ {tc.error}"
                        )
                        fault = " [故障]" if tc.fault_injected else ""
                        print(f"  → {tc.tool_name}: {status}{fault}")
                    print(f"[第{turn}轮] 外呼Agent: {_truncate(agent_text, 200)}\n")

                # Check if agent ended the call
                if self._agent_ended_call(agent_text):
                    self.conversation.termination_reason = "agent_ended"
                    if verbose:
                        print("[Agent 结束通话]")
                    break

                # Callee responds
                callee_output = self.user_sim.generate_response(self.conversation, turn)
                if self.harness:
                    self.harness.process_user_input(
                        callee_output.utterance, turn, callee_output.emotional_state
                    )

                # Day 4: pass v2 simulator signals into message metadata so the
                # CanonicalIntentLedger can detect induced compromise downstream.
                _callee_meta = {
                    "emotional_state": callee_output.emotional_state,
                    "compliance_pressure_level": callee_output.compliance_pressure_level,
                    "parse_failed": callee_output.parse_failed,
                }

                if callee_output.action == "hang_up":
                    self._add_message(
                        Role.USER,
                        callee_output.utterance,
                        metadata=_callee_meta,
                    )
                    self.conversation.termination_reason = "callee_hung_up"
                    if verbose:
                        print(f"[第{turn}轮] 被叫方: {callee_output.utterance}")
                        print("[被叫方挂断]")
                    break

                if callee_output.should_end:
                    self._add_message(
                        Role.USER,
                        callee_output.utterance,
                        metadata=_callee_meta,
                    )
                    self.conversation.termination_reason = "natural_end"
                    if verbose:
                        print(f"[第{turn}轮] 被叫方: {callee_output.utterance}")
                        print("[通话自然结束]")
                    break

                self._add_message(
                    Role.USER,
                    callee_output.utterance,
                    metadata=_callee_meta,
                )
                if verbose:
                    emo = (
                        f" ({callee_output.emotional_state})"
                        if callee_output.emotional_state != "neutral"
                        else ""
                    )
                    print(f"[第{turn}轮] 被叫方{emo}: {callee_output.utterance}\n")
            else:
                self.conversation.termination_reason = "max_turns_reached"
                if verbose:
                    print(f"[达到最大轮次 {self.scenario.max_turns}]")

        # log_call_result enforcement: if agent didn't call it, give one more chance
        # Post-call round is bookkeeping only: only log_call_result allowed, text excluded
        # from scoring, and message marked post_call=True so scorer can filter it out.
        if "log_call_result" in self.scenario.must_call_tools:
            called_tools = {
                tc.tool_name
                for msg in self.conversation.messages
                for tc in msg.tool_calls
                if not tc.error
            }
            if "log_call_result" not in called_tools:
                if verbose:
                    print("\n[系统] Agent 未调用 log_call_result，补发一轮提醒")
                self._add_message(
                    Role.SYSTEM,
                    "通话即将结束，你还没有调用 log_call_result 记录本次通话结果。请立即调用。",
                )
                self.current_turn += 1
                self.tool_sim.set_turn(self.current_turn)
                # Post-call: snapshot before agent responds, rollback if non-log tools executed
                post_snapshot = self.tool_sim.snapshot()
                retry_text, retry_tools = self.agent.respond(
                    self.conversation.model_copy(deep=True)
                )
                # Rollback ALL post-call tool effects and mark ALL pre-rollback
                # ledger events as rolled back, then re-execute only allowed ones
                # through the ledger-logging path to maintain full consistency.
                self.tool_sim.rollback(post_snapshot)
                # Mark ALL pre-rollback tool executions as rolled back in the ledger,
                # so no stale TOOL_EXECUTED events remain from agent.respond().
                for tc in retry_tools:
                    if not tc.error:
                        self.ledger.append(
                            ToolEventType.TOOL_ROLLBACK,
                            turn=self.current_turn,
                            tool_name=tc.tool_name,
                            tool_call_id=tc.id,
                            arguments=tc.arguments,
                        )
                # Re-execute only allowed tools through the ledger-logging path
                allowed_tools = []
                blocked_tools = []
                logging_executor = self._make_logging_executor()
                for tc in retry_tools:
                    if tc.tool_name == "log_call_result":
                        re_tc = logging_executor(tc.tool_name, tc.arguments)
                        allowed_tools.append(re_tc)
                    else:
                        if not tc.error:
                            tc.error = "[BLOCKED] 补发轮只允许 log_call_result"
                        blocked_tools.append(tc)
                if self.harness:
                    retry_text, _raw = self.harness.sanitize_output(retry_text)
                self._add_message(
                    Role.AGENT,
                    retry_text,
                    allowed_tools + blocked_tools,
                    metadata={Conversation._POST_CALL_KEY: True},
                )
                if verbose:
                    for tc in allowed_tools + blocked_tools:
                        status = (
                            f"✓ {_truncate(str(tc.result), 80)}"
                            if not tc.error
                            else f"✗ {tc.error}"
                        )
                        print(f"  → {tc.tool_name}: {status}")

        self.conversation.ended_at = datetime.now()

        # Simulator quality gate
        sim_quality = check_simulator_quality(self.scenario, self.conversation)
        if verbose:
            sq_status = "✓ 通过" if sim_quality.passed else "⚠ 未通过"
            print(f"\n模拟器质量检查: {sq_status}")
            for w in sim_quality.warnings:
                print(f"  ⚠ {w}")
            for c in sim_quality.checks:
                if not c["passed"]:
                    print(f"  ✗ {c['description']}: {c['detail']}")

        # Ledger verification: every ToolCall in conversation must exist in tool_sim
        sim_ledger_ids = {tc.id for tc in self.tool_sim.call_log}
        for msg in self.conversation.messages:
            for tc in msg.tool_calls:
                if tc.source == "harness":
                    continue
                if tc.id not in sim_ledger_ids and not tc.error:
                    tc.error = "[FABRICATED] ToolCall 不在模拟器账本中"
                    self.ledger.append(
                        ToolEventType.TOOL_FABRICATED,
                        turn=msg.turn,
                        tool_name=tc.tool_name,
                        tool_call_id=tc.id,
                        arguments=tc.arguments,
                        error=tc.error,
                    )

        self.ledger.freeze()

        chain_ok, bad_idx = self.ledger.verify_chain()
        if not chain_ok:
            logger.error("EventLedger hash chain broken at index %d", bad_idx)
            raise RuntimeError(
                f"EventLedger hash chain integrity violation at event index {bad_idx}"
            )

        # Score
        if verbose:
            print(f"\n{'=' * 60}")
            print("评分中...")
        db_state = self.tool_sim.get_db_state()

        try:
            report = score_outbound_conversation(
                self.scenario,
                self.conversation,
                db_state,
                self.use_llm_judge,
                fast_mode=self.fast_mode,
                ledger=self.ledger,
            )
        except Exception as e:
            logger.error("评分异常: %s: %s", type(e).__name__, e, exc_info=True)
            report = OutboundScoreReport(
                scenario_id=self.scenario.id,
                conversation_length=len(self.conversation.messages),
                run_validity=RunValidity(
                    status="invalid", reason=f"评分异常: {type(e).__name__}: {e}"
                ),
                task_outcome=TaskOutcome(status="not_scored"),
            )

        if report.run_validity.status != "invalid":
            judge_failures = [f for f in report.failure_summary if f.startswith("[judge]")]
            if judge_failures:
                report.run_validity = RunValidity(status="invalid", reason="LLM judge 调用失败")
            else:
                report.run_validity = RunValidity(status="valid")
            report.task_outcome = TaskOutcome(
                status="success" if report.call_result_correct else "failed",
            )
        report.state_snapshots = self.state_snapshots

        if verbose:
            self._print_report(report)

        # Diagnosis: root cause analysis
        try:
            diagnosis = diagnose_failure(
                self.scenario, self.conversation, report, use_llm=self.use_llm_judge
            )
        except Exception as e:
            from diagnosis import DiagnosisReport

            logger.error("诊断异常: %s: %s", type(e).__name__, e, exc_info=True)
            diagnosis = DiagnosisReport(
                deviation_point=None,
                failure_modes=[],
                root_cause=f"诊断异常: {type(e).__name__}: {e}",
                severity="unknown",
                fix_recommendations=[],
                model_capability_gap="诊断过程异常，无法分析",
            )
        if verbose and diagnosis.failure_modes:
            print(format_diagnosis(diagnosis))
            print()

        # Harness summary
        harness_summary = None
        if self.harness:
            harness_summary = self.harness.get_summary()
            if verbose and harness_summary["total_interventions"] > 0:
                print("── Harness 干预摘要 ──")
                print(f"  总干预次数: {harness_summary['total_interventions']}")
                print(f"  拦截重生成: {harness_summary['blocked_outputs']}")
                print(f"  注入工具调用: {harness_summary['injected_tools']}")
                print(f"  步骤提醒注入: {harness_summary['injected_reminders']}")
                print(f"  禁止词清洗: {harness_summary['sanitized_outputs']}")
                for iv in harness_summary["interventions"]:
                    print(f"  [{iv['type']}] 第{iv['turn']}轮: {iv['detail']}")
                if "adaptive" in harness_summary:
                    ada = harness_summary["adaptive"]
                    print(f"  自适应级别: {ada['level']}")
                    for t in ada["transitions"]:
                        print(f"    ↓ 第{t['turn']}轮降级: {t['from']} → {t['to']}")
                print()

        # Serialize diagnosis for trace
        diagnosis_data = {
            "deviation_point": {
                "turn": diagnosis.deviation_point.turn,
                "expected_step": diagnosis.deviation_point.expected_step,
                "expected_behavior": diagnosis.deviation_point.expected_behavior,
                "actual_behavior": diagnosis.deviation_point.actual_behavior,
            }
            if diagnosis.deviation_point
            else None,
            "failure_modes": [m.value for m in diagnosis.failure_modes],
            "root_cause": diagnosis.root_cause,
            "severity": diagnosis.severity,
            "fix_recommendations": diagnosis.fix_recommendations,
            "model_capability_gap": diagnosis.model_capability_gap,
        }

        # Causal diagnosis: structural root cause analysis
        causal_data = None
        try:
            from causal_diagnosis import diagnose as causal_diagnose
            from policy_graph import compile_policy_graph
            from trace_verifier import verify_trace

            graph = compile_policy_graph(self.scenario)
            verification = verify_trace(
                self.scenario, self.conversation, ledger=self.ledger, graph=graph
            )
            causal_result = causal_diagnose(verification, graph, self.scenario)
            if causal_result.root_causes:
                causal_data = {
                    "root_causes": [
                        {
                            "atom_id": rc.atom_id,
                            "dimension": rc.dimension,
                            "description": rc.description,
                            "blocks": rc.blocks,
                        }
                        for rc in causal_result.root_causes
                    ],
                    "counterfactual_repairs": [
                        {
                            "description": r.repair_description,
                            "atoms_recovered": r.atoms_recovered,
                            "estimated_score_delta": r.estimated_score_delta,
                        }
                        for r in causal_result.counterfactual_repairs
                    ],
                    "failure_mode": causal_result.failure_mode,
                    "deviation_point": causal_result.deviation_point,
                }
        except Exception as e:
            logger.debug("因果诊断跳过: %s", e)

        # Build trace — wrap OutboundScenario in a minimal Scenario-compatible dict
        from models import Scenario, ScoreReport

        scenario_compat = Scenario(
            id=self.scenario.id,
            name=self.scenario.name,
            domain=self.scenario.domain,
            difficulty=self.scenario.difficulty,
            description=self.scenario.description,
            user_goal=self.scenario.callee_goal or self.scenario.call_purpose,
            initial_message=f"[外呼] {self.scenario.call_purpose}",
            max_turns=self.scenario.max_turns,
        )
        score_compat = ScoreReport(
            scenario_id=report.scenario_id,
            conversation_length=report.conversation_length,
            hard_score=report.hard_score,
            soft_score=report.soft_score,
            overall_score=report.overall_score,
            official=report.official,
            checks=report.checks,
            rubric=report.rubric,
            state_snapshots=report.state_snapshots,
            failure_summary=report.failure_summary,
            run_validity=report.run_validity,
            task_outcome=report.task_outcome,
        )
        trace = EvalTrace(
            scenario=scenario_compat,
            conversation=self.conversation,
            score_report=score_compat,
            run_metadata=self._build_run_metadata(),
            metadata={
                "db_state": db_state,
                "domain": "outbound_call",
                "outbound_report": report.model_dump(mode="json"),
                "diagnosis": diagnosis_data,
                "causal_diagnosis": causal_data,
                "harness_summary": harness_summary,
                "simulator_quality": {
                    "passed": sim_quality.passed,
                    "checks": sim_quality.checks,
                    "warnings": sim_quality.warnings,
                },
                "ledger_events": [e.model_dump(mode="json") for e in self.ledger.events],
                "ledger_chain_hash": self.ledger.chain_hash(),
            },
        )
        self._save_trace(trace)
        self._unregister_cost_callback()
        return trace

    def _build_run_metadata(self) -> RunMetadata:
        """Build reproducibility metadata: who was tested, who judged, who simulated, timing.

        Truthful recording — fields reflect actual resolution, not aspiration:
        - judge_model_id is None when the LLM judge did not run (use_llm_judge=False).
        - seed is None: our LLM layer does not currently pass a sampling seed (see
          docs/competitor_gap.md B5). claude_cli backend has no seed support.
        """
        from llm import DEFAULT_MODEL, JUDGE_MODEL, JUDGE_MODEL_SECONDARY, PROVIDER

        target = self.agent_model or (
            "flawed-scripted-v1" if self.agent_type == "flawed" else "claude_cli"
        )
        # Resolution mirrors the real call sites (truthful recording):
        # - simulator: chat(model=None) → DEFAULT_MODEL → provider default (user_sim_outbound.py)
        # - judge:     chat_text(model=None) → JUDGE_MODEL, falling back to DEFAULT_MODEL (llm.py:377)
        # - PoLL secondary: distinct secondary iff configured & differs, else same model as primary
        #   (judges.py:354-357 — the secondary judge always runs, even when unset).
        resolved_default = DEFAULT_MODEL or PROVIDER
        if self.use_llm_judge:
            judge = JUDGE_MODEL or resolved_default
            if JUDGE_MODEL_SECONDARY and JUDGE_MODEL_SECONDARY != JUDGE_MODEL:
                judge_secondary = JUDGE_MODEL_SECONDARY
            else:
                judge_secondary = judge
        else:
            judge = None
            judge_secondary = None

        started = getattr(self, "_run_started_at", None)
        finished = datetime.now()
        duration = (finished - started).total_seconds() if started else None

        return RunMetadata(
            agent_type=self.agent_type,
            model_backend=target,
            judge_model_id=judge,
            judge_model_secondary_id=judge_secondary,
            simulator_model_id=resolved_default,
            use_llm_judge=self.use_llm_judge,
            started_at=started.isoformat() if started else None,
            finished_at=finished.isoformat(),
            duration_seconds=duration,
            cost_summary=self.cost_tracker.summary(),
        )

    def _add_message(self, role: Role, content: str, tool_calls_raw=None, metadata=None):
        msg = Message(
            turn=self.current_turn,
            role=role,
            content=content,
            tool_calls=tool_calls_raw or [],
            metadata=metadata or {},
        )
        self.conversation.messages.append(msg)

    def _take_snapshot(self, turn: int, after_tool: str):
        db = self.tool_sim.get_db_state()
        self.state_snapshots.append(
            StateSnapshot(
                turn=turn,
                after_tool_call=after_tool,
                reservations=[],
                orders=db.get("orders", []),
                coupons_used=[],
                diff_description=f"turn {turn}: {after_tool}",
            )
        )

    SIGNAL_STOP = "###STOP###"
    SIGNAL_TRANSFER = "###TRANSFER###"
    SIGNAL_OUT_OF_SCOPE = "###OUT_OF_SCOPE###"
    _EXPLICIT_SIGNALS = frozenset({SIGNAL_STOP, SIGNAL_TRANSFER, SIGNAL_OUT_OF_SCOPE})

    def _agent_ended_call(self, text: str) -> bool:
        """Detect if agent ended the call.

        Priority:
        1. Explicit signals (###STOP###, ###TRANSFER###, ###OUT_OF_SCOPE###).
        2. Strong signals (substring match).
        3. Bare farewell with negative prefix check.
        """
        for signal in self._EXPLICIT_SIGNALS:
            if signal in text:
                return True

        _NOT_FAREWELL = ["一会儿", "回头", "晚点", "待会儿", "稍后", "等一下"]
        strong_signals = [
            "祝您生活愉快",
            "感谢您的配合",
            "祝您用餐愉快",
            "打扰了，再见",
            "挂断",
            "结束通话",
            "谢谢您的时间",
        ]
        farewell_endings = ["再见", "再见！", "再见。", "生活愉快", "用餐愉快"]

        if any(s in text for s in strong_signals):
            return True

        if any(text.strip().endswith(s) for s in farewell_endings):
            if not any(p + "再见" in text for p in _NOT_FAREWELL):
                return True

        return False

    def _save_trace(self, trace: EvalTrace):
        path = self.trace_dir / f"outbound_{trace.id[:8]}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(trace.model_dump(mode="json"), f, ensure_ascii=False, indent=2, default=str)
        print(f"\n评估轨迹已保存: {path}")

    def _print_report(self, report: OutboundScoreReport):
        print(f"{'=' * 60}")
        print(f"外呼评分报告 — 场景: {report.scenario_id}")
        print(f"{'=' * 60}")

        print(f"\n通话轮次: {report.conversation_length}")
        print(f"开场白: {'✓' if report.opening_correct else '✗'}")
        print(f"结束语: {'✓' if report.closing_correct else '✗'}")
        print(f"通话结果: {'✓' if report.call_result_correct else '✗'}")
        print(f"禁止行为违规: {report.forbidden_violation_count}")
        print()

        print(f"步骤遵循率: {report.step_compliance_score:.1%}")
        branch_str = (
            f"{report.branch_accuracy_score:.1%}"
            if report.branch_accuracy_score is not None
            else "未测试"
        )
        print(f"分支准确率: {branch_str}")
        print(f"硬指标得分: {report.hard_score:.1%}")
        soft_str = f"{report.soft_score:.1%}" if report.soft_score is not None else "未评估"
        print(f"软指标得分: {soft_str}")
        overall_str = (
            f"{report.overall_score_100}/100" if report.overall_score_100 is not None else "未计算"
        )
        print(f"综合得分:   {overall_str}")
        print()

        # Step compliance detail
        if report.step_compliance:
            print("── 步骤执行情况 ──")
            status_icons = {"completed": "✓", "skipped": "⊘", "failed": "✗", "not_reached": "—"}
            for s in report.step_compliance:
                icon = status_icons.get(s.status, "?")
                turn_info = f" (第{s.turn}轮)" if s.turn else ""
                print(f"  {icon} [{s.step_id}] {s.instruction[:40]}{turn_info}")
            print()

        # Rubric
        rubric = report.rubric
        if rubric.dimensions:
            print("── Rubric 维度评分 ──")
            for d in rubric.dimensions:
                flag = " ⚠未充分测试" if d.undertested else ""
                print(f"  {d.dimension_id} {d.name}: {d.score}/5{flag}")
            print(f"  Rubric 总分: {rubric.rubric_total}/{rubric.rubric_max} [{rubric.grade}]")
            print()

        # Rule checks
        print("── 规则检查项 ──")
        for c in report.checks:
            if c.check_type == "rule":
                icon = "✓" if c.passed else "✗"
                print(f"  {icon} {c.description}: {c.explanation}")

        if report.forbidden_violations:
            print("\n── 禁止行为违规详情 ──")
            for v in report.forbidden_violations:
                print(f"  ✗ [{v['severity']}] {v['description']} (第{v['turn']}轮)")

        if report.failure_summary:
            print("\n── 失败清单 ──")
            for f in report.failure_summary:
                print(f"  ✗ {f}")

        print(f"\n{'=' * 60}\n")


def load_outbound_scenario(path: str) -> OutboundScenario:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    scenario = OutboundScenario(**data)
    errors = scenario.validate()
    if errors:
        print(f"⚠ 场景验证错误 ({path}):")
        for e in errors:
            print(f"  - {e}")
        raise ValueError(f"场景验证失败: {'; '.join(errors)}")
    return scenario


def _truncate(s: str, n: int) -> str:
    return s[:n] + "..." if len(s) > n else s
