"""Orchestrator: runs the full evaluation loop with preflight validation."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from baseline_agent import BaselineAgent
from models import (
    Conversation,
    EvalTrace,
    Message,
    Role,
    RunMetadata,
    RunValidity,
    Scenario,
    ScoreReport,
    StateSnapshot,
    TaskOutcome,
)
from scorer import score_conversation
from tools import ToolSimulator
from user_sim import SimulatorOutput, UserSimulator
from validator import PreflightResult, validate_scenario


class Orchestrator:
    def __init__(
        self,
        scenario: Scenario,
        use_llm_judge: bool = True,
        trace_dir: str = "traces",
        agent_type: str = "baseline",
    ):
        self.scenario = scenario
        self.use_llm_judge = use_llm_judge
        self.trace_dir = Path(trace_dir)
        self.trace_dir.mkdir(exist_ok=True)
        self.agent_type = agent_type

        self.tool_sim = ToolSimulator(scenario)
        self.user_sim = UserSimulator(scenario)
        self.agent = self._create_agent(agent_type)

        self.conversation = Conversation(scenario_id=scenario.id)
        self.current_turn = 0
        self.state_snapshots: list[StateSnapshot] = []
        self.preflight: PreflightResult | None = None

    def _create_agent(self, agent_type: str):
        if agent_type == "oracle":
            from agents import OracleAgent

            return OracleAgent(self.tool_sim)
        elif agent_type == "careless":
            from agents import CarelessAgent

            return CarelessAgent(self.tool_sim)
        else:
            return BaselineAgent(self.tool_sim)

    def run(self, verbose: bool = True) -> EvalTrace:
        """Run the full evaluation: preflight -> user sim <-> agent -> score."""
        # ── Preflight validation ──
        if verbose:
            print(f"\n{'=' * 60}")
            print(f"场景: {self.scenario.name}")
            print(f"难度: {self.scenario.difficulty.value}")
            print(f"Agent: {self.agent_type}")
            print(f"最大轮次: {self.scenario.max_turns}")
            print(f"{'=' * 60}")
            print("\n[预检] 验证场景可行性...")

        self.preflight = validate_scenario(self.scenario)

        if verbose:
            if self.preflight.valid:
                print(
                    f"[预检] ✓ 场景有效 — {len(self.preflight.feasible_restaurants)} 家可行餐厅, {len(self.preflight.feasible_slots)} 个可用时段"
                )
                print(f"[预检] 有效约束: {self.preflight.effective_constraints}")
            else:
                print("[预检] ✗ 场景无效!")
                for issue in self.preflight.issues:
                    print(f"  - {issue}")
                print("[预检] 继续运行以收集诊断数据，但分数将标记为 withheld\n")

        if verbose:
            print()

        # ── Take initial DB snapshot ──
        self._take_snapshot(0, "initial_state")

        # ── User's first message ──
        first_msg = self.user_sim.get_initial_message()
        self._add_message(Role.USER, first_msg)
        if verbose:
            print(f"[第0轮] 用户: {first_msg}\n")

        # ── Main conversation loop ──
        for turn in range(1, self.scenario.max_turns + 1):
            self.current_turn = turn
            self.tool_sim.set_turn(turn)

            # agent responds
            if verbose:
                print(f"[第{turn}轮] Agent 思考中...")
            agent_text, tool_calls = self.agent.respond(self.conversation)

            self._add_message(Role.AGENT, agent_text, tool_calls_raw=tool_calls)

            # snapshot after tool calls that modify state
            for tc in tool_calls:
                if tc.tool_name in (
                    "make_reservation",
                    "cancel_reservation",
                    "place_order",
                    "apply_coupon",
                ):
                    self._take_snapshot(turn, tc.tool_name)

            if verbose:
                if tool_calls:
                    for tc in tool_calls:
                        status = (
                            f"✓ {_truncate(str(tc.result), 100)}"
                            if not tc.error
                            else f"✗ {tc.error}"
                        )
                        fault = " [故障]" if tc.fault_injected else ""
                        print(
                            f"  → {tc.tool_name}({_truncate(str(tc.arguments), 60)}) {status}{fault}"
                        )
                print(f"[第{turn}轮] Agent: {_truncate(agent_text, 200)}\n")

            # check if conversation should end
            if self._should_terminate(agent_text):
                self.conversation.termination_reason = "natural_end"
                if verbose:
                    print("[对话自然结束]")
                break

            # user simulator responds (structured output)
            self.current_turn = turn + 1
            sim_output = self.user_sim.generate_response(self.conversation, turn + 1)

            if isinstance(sim_output, SimulatorOutput):
                user_text = sim_output.utterance
                if sim_output.should_end:
                    self._add_message(Role.USER, user_text)
                    self.conversation.termination_reason = "user_ended"
                    if verbose:
                        print(f"[第{turn}轮] 用户: {user_text}")
                        print("[用户结束对话]")
                    break
            else:
                user_text = str(sim_output)

            self._add_message(Role.USER, user_text)

            if verbose:
                print(f"[第{turn}轮] 用户: {user_text}\n")
        else:
            self.conversation.termination_reason = "max_turns_reached"
            if verbose:
                print(f"[达到最大轮次 {self.scenario.max_turns}]")

        self.conversation.ended_at = datetime.now()

        # ── Score ──
        if verbose:
            print(f"\n{'=' * 60}")
            print("评分中...")
        db_state = self.tool_sim.get_db_state()

        # build run validity
        run_validity = RunValidity(
            status="valid" if self.preflight.valid else "invalid_scenario",
            reason="" if self.preflight.valid else "; ".join(self.preflight.issues),
            feasible_path_exists=self.preflight.valid,
        )

        # build task outcome
        confirmed = [r for r in db_state.get("reservations", []) if r.get("status") == "confirmed"]
        if confirmed:
            task_status = "success"
        elif not self.preflight.valid:
            task_status = "impossible"
        else:
            task_status = "failed"
        task_outcome = TaskOutcome(
            status=task_status,
            confirmed_reservations=len(confirmed),
        )

        report = score_conversation(self.scenario, self.conversation, db_state, self.use_llm_judge)

        # inject validity and outcome
        report.run_validity = run_validity
        report.task_outcome = task_outcome
        report.state_snapshots = self.state_snapshots
        if not self.preflight.valid:
            report.official = False
            report.overall_score = None

        if verbose:
            self._print_report(report)

        # ── Build trace ──
        trace = EvalTrace(
            scenario=self.scenario,
            conversation=self.conversation,
            score_report=report,
            run_metadata=RunMetadata(agent_type=self.agent_type),
            metadata={"db_state": db_state, "preflight": self.preflight.model_dump()},
        )
        self._save_trace(trace)
        return trace

    def _add_message(self, role: Role, content: str, tool_calls_raw=None):
        msg = Message(
            turn=self.current_turn,
            role=role,
            content=content,
            tool_calls=tool_calls_raw or [],
        )
        self.conversation.messages.append(msg)

    def _take_snapshot(self, turn: int, after_tool: str):
        db = self.tool_sim.get_db_state()
        prev = self.state_snapshots[-1] if self.state_snapshots else None
        diff = ""
        if prev:
            new_res = len(db.get("reservations", [])) - len(prev.reservations)
            new_ord = len(db.get("orders", [])) - len(prev.orders)
            parts = []
            if new_res > 0:
                parts.append(f"+{new_res} 预订")
            if new_ord > 0:
                parts.append(f"+{new_ord} 订单")
            if not parts:
                diff = "无变化"
            else:
                diff = ", ".join(parts)
        else:
            diff = "初始状态"

        self.state_snapshots.append(
            StateSnapshot(
                turn=turn,
                after_tool_call=after_tool,
                reservations=db.get("reservations", []),
                orders=db.get("orders", []),
                coupons_used=[c["code"] for c in db.get("coupons", []) if c.get("used")],
                diff_description=diff,
            )
        )

    def _should_terminate(self, agent_text: str) -> bool:
        success_signals = [
            "预订成功",
            "已为您预订",
            "已确认预订",
            "预订已完成",
            "订好了",
            "帮你订好了",
        ]
        if any(s in agent_text for s in success_signals):
            db = self.tool_sim.get_db_state()
            confirmed = [r for r in db.get("reservations", []) if r.get("status") == "confirmed"]
            if len(confirmed) > 0:
                return True

        farewell_signals = [
            "拜拜",
            "再见",
            "祝你顺利",
            "有需要再找我",
            "随时找我",
            "团建愉快",
            "玩得开心",
        ]
        if any(s in agent_text for s in farewell_signals):
            user_msgs = [m for m in self.conversation.messages if m.role == Role.USER]
            if user_msgs:
                last_user = user_msgs[-1].content
                user_bye = ["拜拜", "再见", "行吧", "好的", "算了", "就这样"]
                if any(s in last_user for s in user_bye):
                    return True
        return False

    def _save_trace(self, trace: EvalTrace):
        path = self.trace_dir / f"{trace.id}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(trace.model_dump(mode="json"), f, ensure_ascii=False, indent=2, default=str)
        print(f"\n评估轨迹已保存: {path}")

    def _print_report(self, report: ScoreReport):
        print(f"{'=' * 60}")
        print(f"评分报告 — 场景: {report.scenario_id}")
        print(f"{'=' * 60}")

        # Run validity
        rv = report.run_validity
        if rv.status != "valid":
            print(f"⚠ 运行有效性: {rv.status}")
            print(f"  原因: {rv.reason}")
            print(f"  分数状态: {'官方' if report.official else '诊断性（非官方）'}")
            print()

        # Task outcome
        to = report.task_outcome
        outcome_icons = {"success": "✓", "failed": "✗", "impossible": "⊘", "not_scored": "—"}
        print(
            f"任务结果: {outcome_icons.get(to.status, '?')} {to.status} (确认预订: {to.confirmed_reservations})"
        )
        print()

        print(f"对话轮次: {report.conversation_length}")
        print(f"硬指标得分: {report.hard_score:.1%}")
        soft_str = f"{report.soft_score:.1%}" if report.soft_score is not None else "未评估"
        print(f"软指标得分: {soft_str}")
        overall_str = (
            f"{report.overall_score:.1%}" if report.overall_score is not None else "withheld"
        )
        print(f"综合得分:   {overall_str}")
        if not report.official:
            print("  ↳ 非官方分数（场景无效或评估不完整）")
        print()

        # State snapshots
        if report.state_snapshots:
            print("── 状态变化 ──")
            for ss in report.state_snapshots:
                if ss.diff_description != "初始状态" and ss.diff_description != "无变化":
                    print(f"  第{ss.turn}轮 ({ss.after_tool_call}): {ss.diff_description}")
            print()

        # Rubric
        rubric = report.rubric
        if rubric.dimensions:
            print("── Rubric 维度评分 ──")
            for d in rubric.dimensions:
                flag = " ⚠未充分测试" if d.undertested else ""
                print(f"  {d.dimension_id} {d.name}: {d.score}/5{flag}")
            print(f"  Rubric 总分: {rubric.rubric_total}/34 [{rubric.grade}]")
            print()

        print("── 规则检查项 ──")
        for c in report.checks:
            if c.check_type == "rule":
                icon = "✓" if c.passed else "✗"
                print(f"  {icon} {c.description}: {c.score:.1f} — {c.explanation}")

        if report.failure_summary:
            print()
            print("── 失败清单 ──")
            for f in report.failure_summary:
                print(f"  ✗ {f}")

        print()
        print("── 约束账本 ──")
        for entry in report.constraint_ledger:
            c = entry.constraint
            print(f"  [{entry.final_status}] {c.description} ({c.type})")
            for ev in entry.events:
                print(
                    f"    第{ev.turn}轮: {ev.event_type} — {_truncate(ev.evidence, 60) if ev.evidence else ''}"
                )

        print(f"\n{'=' * 60}\n")


def load_scenario(path: str) -> Scenario:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return Scenario(**data)


def _truncate(s: str, n: int) -> str:
    return s[:n] + "..." if len(s) > n else s
