#!/usr/bin/env python3
"""Convert after-fix traces to the format expected by the blind annotation HTML page."""

import json
from pathlib import Path

TRACES_DIR = (
    Path(__file__).resolve().parent.parent / "data" / "calibration" / "blind_pilot" / "traces_after"
)
OUT_FILE = TRACES_DIR.parent / "traces_after.json"

out = []
for tf in sorted(TRACES_DIR.glob("*.json")):
    d = json.loads(tf.read_text(encoding="utf-8"))
    scenario = d.get("scenario", {})
    msgs_raw = d.get("conversation", {}).get("messages", [])

    msgs = []
    for m in msgs_raw:
        msg = {
            "role": m.get("role", ""),
            "turn": m.get("turn", 0),
            "content": m.get("content", ""),
            "tools": [],
        }
        for tc in m.get("tool_calls", []):
            result_str = json.dumps(tc.get("result", ""), ensure_ascii=False)
            if len(result_str) > 500:
                result_str = result_str[:500] + "..."
            tool = {
                "name": tc.get("tool_name", ""),
                "args": json.dumps(tc.get("arguments", {}), ensure_ascii=False),
                "result": result_str if tc.get("result") is not None else None,
                "error": tc.get("error"),
            }
            msg["tools"].append(tool)
        msgs.append(msg)

    trace = {
        "scenario": scenario.get("id", ""),
        "desc": scenario.get("description", ""),
        "purpose": scenario.get("initial_message", ""),
        "instruction": "",
        "msgs": msgs,
    }
    out.append(trace)

OUT_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"Wrote {len(out)} traces to {OUT_FILE}")
for i, t in enumerate(out):
    print(f"  {i + 1}. {t['scenario']} ({len(t['msgs'])} msgs)")
