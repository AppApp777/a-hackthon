import json
from pathlib import Path

TRACES_DIR = Path(__file__).parent.parent / "traces"
OUTPUT = Path(__file__).parent.parent / "data" / "calibration" / "blind_pilot" / "traces_blind.json"

SELECTED = [
    "fec0f023-d30b-46c0-b9a9-4ab5606cb5bf",
    "96bb49e1-3bae-40ff-bc55-f5fab0b2827c",
    "outbound_528f852f",
    "outbound_c50d015b",
    "outbound_c5a77fde",
    "outbound_b721247d",
    "outbound_22c0a09e",
    "outbound_e6dcbff3",
    "outbound_f1f52154",
    "outbound_89c7257c",
    "outbound_706fbaea",
    "outbound_64322b6d",
    "outbound_5af36c0d",
    "outbound_a6e6d2ee",
    "outbound_130887c4",
    "outbound_5df2d028",
    "0ee5fef1-2a40-4d1d-a95c-612840d5ba5f",
    "outbound_c15731d6",
    "outbound_65365325",
    "outbound_1041b5a5",
    "outbound_349b29da",
    "outbound_85ba029e",
    "outbound_152feba3",
    "outbound_2c0f63fd",
]

data = []
for tid in SELECTED:
    p = TRACES_DIR / f"{tid}.json"
    if not p.exists():
        print(f"MISSING: {tid}")
        continue
    t = json.loads(p.read_text("utf-8"))
    msgs = t.get("conversation", {}).get("messages", [])
    scenario = t.get("scenario", {})

    sid = t.get("scenario_id", "")
    if not sid and isinstance(scenario, dict):
        sid = scenario.get("id", "") or scenario.get("name", "")
    if not sid:
        sid = tid[:20]

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
            "id": tid[:20],
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

OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"Exported {len(data)} traces")
for i, d in enumerate(data):
    print(f"  {i + 1:2d}. {d['scenario'][:35]:35s} msgs={len(d['msgs']):2d}")
