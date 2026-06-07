import json
from pathlib import Path

TRACES_DIR = Path(__file__).parent.parent / "data" / "calibration" / "blind_pilot" / "traces_v2"
OUTPUT = Path(__file__).parent.parent / "data" / "calibration" / "blind_pilot" / "traces_blind.json"
SCORES_OUT = (
    Path(__file__).parent.parent
    / "data"
    / "calibration"
    / "blind_pilot"
    / "_system_scores_DO_NOT_OPEN.json"
)

files = sorted(TRACES_DIR.glob("*.json"))
print(f"Found {len(files)} traces in {TRACES_DIR}")

data = []
system_scores = {}
for idx, p in enumerate(files, 1):
    t = json.loads(p.read_text("utf-8"))
    msgs = t.get("conversation", {}).get("messages", [])
    scenario = t.get("scenario", {})

    sid = t.get("scenario_id", "")
    if not sid and isinstance(scenario, dict):
        sid = scenario.get("id", "") or scenario.get("name", "")
    if not sid:
        sid = p.stem

    desc = ""
    purpose = ""
    instruction_text = ""
    if isinstance(scenario, dict):
        desc = scenario.get("description", "")[:300]
        purpose = scenario.get("call_purpose", "")[:200]
        steps = scenario.get("instruction_steps", [])
        if steps:
            lines = []
            for i, s in enumerate(steps[:15]):
                order = s.get("order", i + 1)
                instr = s.get("instruction", "")
                lines.append(f"{order}. {instr}")
            instruction_text = "\n".join(lines)

    data.append(
        {
            "id": p.stem[:20],
            "scenario": sid,
            "desc": desc,
            "purpose": purpose,
            "instruction": instruction_text[:800],
            "msgs": [
                {
                    "turn": m.get("turn", i + 1),
                    "role": m.get("role", "?"),
                    "content": (m.get("content", "") or "")[:2000],
                    "tools": [
                        {
                            "name": tc.get("tool_name", "?"),
                            "args": str(tc.get("arguments", {}))[:250],
                            "result": str(tc.get("result", ""))[:400],
                            "error": tc.get("error", ""),
                        }
                        for tc in m.get("tool_calls", [])
                    ],
                }
                for i, m in enumerate(msgs)
            ],
        }
    )

    score_report = t.get("score_report", {})
    system_scores[f"{idx:02d}"] = {
        "trace_id": p.stem,
        "scenario_id": sid,
        "model": t.get("run_metadata", {}).get("model_backend", "unknown"),
        "system_score_100": score_report.get("overall_score_100"),
        "system_veto_cap": score_report.get("veto_cap"),
        "system_gate_type": score_report.get("gate_type", "none"),
    }

OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
SCORES_OUT.write_text(json.dumps(system_scores, ensure_ascii=False, indent=2), encoding="utf-8")

print(f"\nExported {len(data)} traces to {OUTPUT.name}")
for i, d in enumerate(data):
    sc = system_scores[f"{i + 1:02d}"]
    print(
        f"  {i + 1:2d}. {d['scenario'][:35]:35s} msgs={len(d['msgs']):2d}  score={sc['system_score_100']}"
    )
