#!/usr/bin/env python3
"""Rescore ALL outbound traces with the (fixed) scorer, surgically replacing
`trace["score_report"]` (ScoreReport subset) and
`trace["metadata"]["outbound_report"]` (full OutboundScoreReport dump).

WHY: the step_compliance Chinese-tokenization fix (commit 177d880) changed
scores. The locked blind_v1 predictions were built on the OLD scorer and are
stale. This regenerates the on-disk trace scores so build_blind_validation.py
re-selects + re-locks against the current scorer.

Surgical: only `score_report` and `metadata.outbound_report` are rewritten,
mirroring orchestrator_outbound.py:804-826. Every other field (conversation,
run_metadata, db_state, diagnosis, ledger_events, …) is untouched.

Deterministic:
  - use_llm_judge=False  → no LLM soft layer (same condition as the prior lock).
  - generated_at is PRESERVED from the original trace (no timestamp churn).
  - instruction_steps are read from the ORIGINAL scenario file — the trace's
    embedded scenario has none, so scoring in-place would silently degrade
    step_compliance (the very bug we just fixed).

Default is DRY-RUN (prints old→new, writes nothing). Pass --write to persist.

Usage:
  python scripts/rescore_all_traces.py            # dry-run
  python scripts/rescore_all_traces.py --write     # persist
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from models import Conversation, ScoreReport  # noqa: E402
from models_outbound import OutboundScenario  # noqa: E402
from scorer_outbound import score_outbound_conversation  # noqa: E402

TRACES = ROOT / "traces"


def build_id2file() -> dict[str, Path]:
    """Map scenario id -> original scenario file (which carries instruction_steps)."""
    id2file: dict[str, Path] = {}
    for sd in (ROOT / "scenarios" / "outbound", ROOT / "scenarios" / "outbound" / "generated"):
        if not sd.exists():
            continue
        for p in sorted(sd.glob("*.json")):
            try:
                j = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(j, dict) and j.get("id"):
                id2file.setdefault(j["id"], p)
    return id2file


def buckets(vals: list[float]) -> dict[str, int]:
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="persist changes (default: dry-run)")
    args = ap.parse_args()

    id2file = build_id2file()
    skipped: list[tuple[str, str]] = []
    results: list[tuple[str, float, float]] = []  # (name, old100, new100)

    print(f"{'trace':28s} {'scenario':26s} {'old':>6s} {'new':>6s} {'Δ':>7s}")
    print("-" * 80)

    for p in sorted(TRACES.glob("outbound_*.json")):
        try:
            trace = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            skipped.append((p.name, f"bad json: {str(e)[:40]}"))
            continue
        # meta_eval "mock" traces are synthetic (empty turns, model_backend points at a
        # source file). build_blind excludes them from the blind set; rescore skips them
        # too so we don't pointlessly rewrite synthetic files and keep both in lockstep.
        if ((trace.get("run_metadata") or {}).get("agent_type")) == "mock":
            skipped.append((p.name, "mock (meta_eval synthetic)"))
            continue
        sid = (trace.get("scenario") or {}).get("id") or (trace.get("conversation") or {}).get(
            "scenario_id"
        )
        sf = id2file.get(sid)
        if not sf:
            skipped.append((p.name, f"no scenario file for id={sid}"))
            continue

        scen = OutboundScenario(**json.loads(sf.read_text(encoding="utf-8")))
        conv = Conversation(**trace["conversation"])
        db_state = (trace.get("metadata") or {}).get("db_state") or {}

        old100 = round((((trace.get("score_report") or {}).get("overall_score")) or 0.0) * 100, 1)
        report = score_outbound_conversation(scen, conv, db_state, use_llm_judge=False)
        new100 = round((report.overall_score or 0.0) * 100, 1)

        # Surgical replacement — mirrors orchestrator_outbound.py:804-826.
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
        dumped = score_compat.model_dump(mode="json")
        # Preserve the original timestamp — this is a re-score of an existing run, not a
        # new run. Falls back to run_metadata.timestamp (also fixed) so the freeze stays
        # deterministic even in the edge case where generated_at is missing; never lets
        # model_dump inject a fresh datetime.now().
        old_gen = (trace.get("score_report") or {}).get("generated_at") or (
            trace.get("run_metadata") or {}
        ).get("timestamp")
        if old_gen:
            dumped["generated_at"] = old_gen
        trace["score_report"] = dumped
        trace.setdefault("metadata", {})["outbound_report"] = report.model_dump(mode="json")

        results.append((p.name, old100, new100))
        print(
            f"{p.name:28s} {str(sid)[:25]:26s} {old100:6.1f} {new100:6.1f} {new100 - old100:+7.1f}"
        )

        if args.write:
            p.write_text(
                json.dumps(trace, ensure_ascii=False, indent=2, default=str) + "\n",
                encoding="utf-8",
            )

    print("-" * 80)
    if results:
        olds = [r[1] for r in results]
        news = [r[2] for r in results]
        print(
            f"OLD  n={len(olds)} min={min(olds):.1f} max={max(olds):.1f} "
            f"mean={sum(olds) / len(olds):.1f} buckets={buckets(olds)}"
        )
        print(
            f"NEW  n={len(news)} min={min(news):.1f} max={max(news):.1f} "
            f"mean={sum(news) / len(news):.1f} buckets={buckets(news)}"
        )
        changed = sum(1 for _, o, n in results if abs(o - n) >= 0.05)
        print(f"changed: {changed}/{len(results)}  (|Δ|>=0.05)")
    if skipped:
        print(f"\nskipped {len(skipped)}:")
        for name, why in skipped:
            print(f"  {name}: {why}")
    print(f"\nmode: {'WRITE (persisted)' if args.write else 'DRY-RUN (no files written)'}")


if __name__ == "__main__":
    main()
