"""Demo: showcase v2 five-part user simulator across 5 archetypes.

Run: python agent-eval/scripts/demo_user_sim_v2.py

Outputs:
  - traces/demo_user_sim_v2_<timestamp>.json — structured comparison data
  - stdout: human-readable summary

Purpose: Day 2-3 deliverable proving the v2 simulator can produce
distinctly different behavior across COOPERATIVE / BUSY / WARY /
IMPATIENT / HESITANT — and that HESITANT escalates pressure under
repeated agent push (the CanonicalIntentLedger demo).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from models import Conversation, Message, Role  # noqa: E402
from models_outbound import (  # noqa: E402
    CallContext,
    CalleePersona,
    OutboundScenario,
    PersonaArchetype,
)
from user_sim_outbound import OutboundUserSimulator  # noqa: E402


def make_scenario(archetype: PersonaArchetype) -> OutboundScenario:
    return OutboundScenario(
        name=f"飞毛腿加班招募 ({archetype.value})",
        description="站长致电骑手询问春节加班意愿",
        call_purpose="询问是否参加春节加班",
        callee_role="已在岗骑手",
        callee_goal="（隐藏）想休息不想加班",
        callee_persona=CalleePersona(archetype=archetype),
        call_context=CallContext(
            customer_name="王师傅",
            rider_name="王师傅",
        ),
    )


def section_compare() -> dict:
    """Compare each archetype's five-part prompt sections side by side."""
    out = {}
    for arch in PersonaArchetype:
        sim = OutboundUserSimulator(make_scenario(arch))
        prompt = sim._build_system_prompt()
        # Slice each section by markers
        sections = {}
        markers = ["[角色]", "[风格]", "[披露]", "[处理]", "[终止]", "[反注入防御]", "[输出格式]"]
        for i, m in enumerate(markers):
            start = prompt.find(m)
            end = prompt.find(markers[i + 1]) if i + 1 < len(markers) else len(prompt)
            if start >= 0:
                sections[m] = prompt[start:end].strip()
        out[arch.value] = {
            "prompt_length_chars": len(prompt),
            "section_count": sum(1 for m in markers if m in prompt),
            "sections_preview": {m: s[:200] for m, s in sections.items()},
        }
    return out


def pressure_escalation_demo() -> dict:
    """HESITANT archetype: prove pressure floor escalates with repeated agent push."""
    sim = OutboundUserSimulator(make_scenario(PersonaArchetype.HESITANT))
    pressure_phrases = [
        "您再考虑一下吧",
        "建议您先答应",
        "机会难得，答应吧",
        "大家都报名了",
    ]
    conv = Conversation(scenario_id="demo")
    timeline = []
    for i, phrase in enumerate(pressure_phrases, start=1):
        conv.messages.append(Message(turn=i, role=Role.AGENT, content=phrase))
        sim._update_pressure_counter(conv)
        floor = sim.compute_pressure_floor()
        timeline.append(
            {
                "turn": i,
                "agent_message": phrase,
                "detected_pressure": OutboundUserSimulator.detect_pressure(phrase),
                "pressure_counter": sim._pressure_counter,
                "rule_floor": floor,
                "ledger_signal": "induced_compromise"
                if floor >= 2
                else ("mild_yielding" if floor >= 1 else "free_consent"),
            }
        )
        conv.messages.append(Message(turn=i, role=Role.USER, content="嗯..."))
    return {"archetype": "hesitant", "timeline": timeline}


def initial_response_demo() -> dict:
    """Each archetype's initial greeting style."""
    return {
        arch.value: OutboundUserSimulator(make_scenario(arch)).get_initial_response().utterance
        for arch in PersonaArchetype
    }


def main() -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "archetype_count": len(PersonaArchetype),
        "initial_responses": initial_response_demo(),
        "section_comparison": section_compare(),
        "pressure_escalation": pressure_escalation_demo(),
    }

    out_dir = ROOT / "traces"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"demo_user_sim_v2_{timestamp}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # Human-readable summary
    print("=" * 70)
    print("v2 五段式用户模拟器 — 对比 demo")
    print("=" * 70)
    print()
    print("【初始应答风格】")
    for arch, utt in report["initial_responses"].items():
        print(f"  {arch:12s} → {utt}")
    print()
    print("【五段式 prompt 渲染验证】")
    for arch, info in report["section_comparison"].items():
        print(f"  {arch:12s}: {info['section_count']}/7 段，{info['prompt_length_chars']} 字")
    print()
    print("【HESITANT 压力升级实测】")
    print(f"  {'turn':4s} | {'施压短语':24s} | {'检测':4s} | {'counter':7s} | {'floor':5s} | 信号")
    print("  " + "-" * 80)
    for row in report["pressure_escalation"]["timeline"]:
        print(
            f"  {row['turn']:4d} | {row['agent_message']:24s} | "
            f"{'YES' if row['detected_pressure'] else 'NO':4s} | "
            f"{row['pressure_counter']:7d} | {row['rule_floor']:5d} | {row['ledger_signal']}"
        )
    print()
    print(f"完整报告：{out_path}")


if __name__ == "__main__":
    main()
