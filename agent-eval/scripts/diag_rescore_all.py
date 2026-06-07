#!/usr/bin/env python3
"""临时诊断：用当前(已修)打分器对 blind_v1 的 27 条留出集做完整重打分。
和原锁定预测同条件(use_llm_judge=False，无软分)，纯比 step 检测修复的影响。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from models import Conversation  # noqa: E402
from models_outbound import OutboundScenario  # noqa: E402
from scorer_outbound import score_outbound_conversation  # noqa: E402

MANIFEST = ROOT / "calibration" / "blind_v1" / "validation_manifest.jsonl"

# id -> scenario file
id2file: dict[str, Path] = {}
for sd in (ROOT / "scenarios" / "outbound", ROOT / "scenarios" / "outbound" / "generated"):
    for p in sd.glob("*.json"):
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(j, dict) and j.get("id"):
            id2file.setdefault(j["id"], p)

rows = [
    json.loads(line) for line in MANIFEST.read_text(encoding="utf-8").splitlines() if line.strip()
]

print(f"{'blind_id':9s} {'model':28s} {'scenario':26s} {'old':>6s} {'new':>6s}  {'Δ':>6s}")
print("-" * 90)
news = []
for r in rows:
    sid = r["scenario_id"]
    sf = id2file.get(sid)
    tpath = ROOT / r["path"]
    if not sf or not tpath.exists():
        print(f"{r['blind_id']:9s} MISSING scenario={sid} file={sf}")
        continue
    trace = json.loads(tpath.read_text(encoding="utf-8"))
    scen = OutboundScenario(**json.loads(sf.read_text(encoding="utf-8")))
    conv = Conversation(**trace["conversation"])
    db_state = (trace.get("metadata") or {}).get("db_state") or {}
    rep = score_outbound_conversation(scen, conv, db_state, use_llm_judge=False)
    new100 = round((rep.overall_score or 0.0) * 100, 1)
    old100 = r["system_score"]
    news.append((r["blind_id"], r["model"], sid, old100, new100))
    print(
        f"{r['blind_id']:9s} {r['model'][:27]:28s} {sid[:25]:26s} {old100:6.1f} {new100:6.1f}  {new100 - old100:+6.1f}"
    )

print("-" * 90)
if news:
    olds = [x[3] for x in news]
    nws = [x[4] for x in news]

    def buckets(vals):
        b = {"severe<40": 0, "weak40-60": 0, "border60-80": 0, "pass80+": 0}
        for v in vals:
            if v < 40:
                b["severe<40"] += 1
            elif v < 60:
                b["weak40-60"] += 1
            elif v < 80:
                b["border60-80"] += 1
            else:
                b["pass80+"] += 1
        return b

    print(
        f"OLD  min={min(olds):.1f} max={max(olds):.1f} mean={sum(olds) / len(olds):.1f}  buckets={buckets(olds)}"
    )
    print(
        f"NEW  min={min(nws):.1f} max={max(nws):.1f} mean={sum(nws) / len(nws):.1f}  buckets={buckets(nws)}"
    )
