#!/usr/bin/env python3
"""judge_demo.py - offline, no-API proof of the core moat.

The moat in one sentence:
    We score what the EXECUTABLE TRACE PROVES, not what the transcript CLAIMS.

A senior reviewer's reflex toward any demo is "this is rigged / cherry-picked."
So this is built as a *metamorphic proof*, not a pair of hand-picked examples:

  CASE A (GOOD)     a real trace whose agent actually executed the tools; the
                    SQLite world-state was written and the event ledger records
                    the executions  ->  full credit.

  CASE B (MUTATED)  the SAME trace as A, with the VISIBLE DIALOGUE held
                    byte-for-byte identical, but the hidden execution evidence
                    removed (tool calls marked unexecuted + world-state
                    reverted). The same production scorer now applies the
                    non-compensatory fabrication veto  ->  0.

  CASE C (REAL BAD) a genuine real-world trace where the agent claims a refund
                    it never executed  ->  0. Shows MUTATED is not a contrived
                    shape; this failure occurs in the wild.

CASE B is the proof: the visible conversation is a CONTROL (identical words,
identical politeness). Only the hidden tool-ledger / DB evidence differs. An
LLM-only judge cannot tell A and B apart; our engine gives 87 vs 0.

This script contains NO scoring logic. It loads traces, calls the REAL modules
(scorer_outbound.score_outbound_conversation, models.EventLedger), prints the
evidence the scorer used, and asserts the invariants - exiting non-zero if any
assertion fails. Everything is offline (use_llm_judge=False); no API, no network.

Run:
    python scripts/judge_demo.py
    python scripts/judge_demo.py --good <trace.json> --bad <trace.json>
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import scorer_outbound  # noqa: E402
from models import Conversation, EventLedger, ToolEventType  # noqa: E402
from models_outbound import OutboundScenario  # noqa: E402
from scorer_outbound import score_outbound_conversation  # noqa: E402

TRACES = ROOT / "traces"
DEFAULT_GOOD = "outbound_c5a77fde.json"  # agent really executed the tools; DB written
DEFAULT_BAD = "outbound_092b13fe.json"  # agent claims refund done; ledger/DB say otherwise


def _scenario_index() -> dict[str, Path]:
    idx: dict[str, Path] = {}
    for sd in (ROOT / "scenarios" / "outbound", ROOT / "scenarios" / "outbound" / "generated"):
        if not sd.exists():
            continue
        for p in sd.glob("*.json"):
            try:
                j = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(j, dict) and j.get("id"):
                idx.setdefault(j["id"], p)
    return idx


def _load(path_name: str) -> dict:
    return json.loads((TRACES / path_name).read_text(encoding="utf-8"))


def _conv(trace: dict) -> Conversation:
    return Conversation(**trace["conversation"])


def _scenario(trace: dict, idx: dict[str, Path]) -> OutboundScenario:
    sid = (trace.get("scenario") or {}).get("id") or (trace.get("conversation") or {}).get(
        "scenario_id"
    )
    return OutboundScenario(**json.loads(idx[sid].read_text(encoding="utf-8")))


def reconstruct_ledger(conv: Conversation) -> EventLedger:
    """Rebuild the canonical EventLedger from a frozen trace.

    Trust boundary: an agent-emitted ToolCall counts as EXECUTED only if it
    carries no failure marker. A '[FABRICATED]' marker (the agent named a tool
    whose call never reached the simulator) becomes a TOOL_FABRICATED event,
    which is exactly what the non-compensatory veto keys on. Mirrors the live
    orchestrator (orchestrator_outbound.py:630-645)."""
    led = EventLedger()
    for msg in conv.messages:
        for tc in msg.tool_calls:
            if getattr(tc, "source", None) == "harness":
                continue
            err = tc.error or ""
            if "[FABRICATED]" in err:
                et = ToolEventType.TOOL_FABRICATED
            elif "[BLOCKED]" in err:
                et = ToolEventType.TOOL_BLOCKED
            else:
                et = ToolEventType.TOOL_EXECUTED
            led.append(
                et,
                turn=msg.turn,
                tool_name=tc.tool_name,
                tool_call_id=tc.id,
                arguments=tc.arguments or {},
                error=err,
            )
    led.freeze()
    return led


def visible_dialogue_sha256(conv: Conversation) -> str:
    """Hash ONLY the user-visible dialogue (role + spoken content), excluding
    tool calls / ledger / DB. The control: if two cases share this hash, any
    score difference comes purely from hidden execution evidence."""
    h = hashlib.sha256()
    for m in conv.messages:
        h.update((str(m.role) + "\x1f" + (m.content or "") + "\x1e").encode("utf-8"))
    return h.hexdigest()


def _count_rows(db_state: dict, table: str) -> int:
    v = (db_state or {}).get(table)
    return len(v) if isinstance(v, list) else 0


def mutate_remove_evidence(good_trace: dict) -> dict:
    """Metamorphic mutation: keep the visible dialogue byte-identical, but remove
    the hidden execution evidence - mark every agent tool call as unexecuted and
    revert the world-state. Models an agent that SAYS it did everything while
    nothing actually happened. The scorer must collapse this to a fabrication veto."""
    bad = copy.deepcopy(good_trace)
    for msg in bad.get("conversation", {}).get("messages", []):
        for tc in msg.get("tool_calls", []):
            if tc.get("source") == "harness":
                continue
            tc["error"] = "[FABRICATED] (metamorphic: execution evidence removed)"
    db = (bad.get("metadata") or {}).get("db_state") or {}
    for k, v in list(db.items()):
        if isinstance(v, list):
            db[k] = []
    return bad


def score_case(trace: dict, idx: dict[str, Path]):
    conv = _conv(trace)
    scen = _scenario(trace, idx)
    db_state = (trace.get("metadata") or {}).get("db_state") or {}
    led = reconstruct_ledger(conv)
    rep = score_outbound_conversation(scen, conv, db_state, use_llm_judge=False, ledger=led)
    return rep, led, conv, db_state


def _pre_veto(rep) -> float:
    """Score BEFORE the safety veto (evidence layer). Shown so the reviewer sees
    the veto is non-compensatory: a high pre-veto score is still capped to 0."""
    for attr in ("evidence_score", "objective_score", "hard_score"):
        v = getattr(rep, attr, None)
        if v is not None:
            return round(v * 100, 1) if v <= 1.0 else round(v, 1)
    return -1.0


def main() -> int:
    ap = argparse.ArgumentParser(description="Offline proof of the agent-eval moat.")
    ap.add_argument("--good", default=DEFAULT_GOOD, help="genuine trace (tools executed)")
    ap.add_argument("--bad", default=DEFAULT_BAD, help="real fabricating trace")
    args = ap.parse_args()

    idx = _scenario_index()

    print("=" * 70)
    print("JUDGE DEMO - the moat, no API key, fully offline")
    print("=" * 70)
    print("mode = NO_API   llm_judge = DISABLED   network = DISABLED")
    print("[code path] demo only loads + calls real modules; it has NO scoring logic")
    print(
        f"  scorer   = scorer_outbound.score_outbound_conversation ({Path(scorer_outbound.__file__).name})"
    )
    print("  veto cap = scorer_modules/computation.py:compute_veto_cap")
    print("  ledger   = models.EventLedger (append-only, hash-chained)")
    print()

    good = _load(args.good)
    rep_g, led_g, conv_g, db_g = score_case(good, idx)
    sid = (good.get("scenario") or {}).get("id")
    vis_g = visible_dialogue_sha256(conv_g)

    bad_mut = mutate_remove_evidence(good)
    rep_m, led_m, conv_m, db_m = score_case(bad_mut, idx)
    vis_m = visible_dialogue_sha256(conv_m)

    badr = _load(args.bad)
    rep_b, led_b, conv_b, db_b = score_case(badr, idx)

    print(f"Scenario: {sid}  (after-sales missing-item complaint, refund budget Y50)")
    print()
    print("--- CASE A: GOOD - agent actually executed the tools -----------------")
    print(f"  ledger tool events   : {len(led_g.events)}  (TOOL_EXECUTED)")
    print(f"  SQLite compensations : {_count_rows(db_g, 'compensations')} row(s)")
    print(f"  fabrication veto     : {'YES' if led_g.has_fabricated else 'no'}")
    print(f"  pre-veto score       : {_pre_veto(rep_g)}")
    print(f"  >>> FINAL SCORE      : {rep_g.overall_score_100} / 100   gate={rep_g.gate_type}")
    print()
    print("--- CASE B: MUTATED - SAME dialogue, hidden evidence removed ---------")
    print(
        f"  visible-dialogue hash: {'MATCH A' if vis_m == vis_g else 'DIFFERENT (!)'}  ({vis_m[:12]})"
    )
    print(f"  ledger tool events   : {len(led_m.events)}  (now TOOL_FABRICATED)")
    print(f"  SQLite compensations : {_count_rows(db_m, 'compensations')} row(s)  (reverted)")
    print(f"  fabrication veto     : {'YES' if led_m.has_fabricated else 'no'}")
    print(f"  pre-veto score       : {_pre_veto(rep_m)}   <- NOT zero")
    print(f"  >>> FINAL SCORE      : {rep_m.overall_score_100} / 100   gate={rep_m.gate_type}")
    print()
    print("--- CASE C: REAL - a genuine trace claiming a refund it never made ---")
    print(f"  ledger tool events   : {len(led_b.events)}")
    print(f"  SQLite compensations : {_count_rows(db_b, 'compensations')} row(s)")
    print(f"  fabrication veto     : {'YES' if led_b.has_fabricated else 'no'}")
    print(f"  >>> FINAL SCORE      : {rep_b.overall_score_100} / 100   gate={rep_b.gate_type}")
    print()
    print("--- Why this is the moat ---------------------------------------------")
    print("  CASE A and CASE B have the SAME visible conversation (hash matches).")
    print("  An LLM-only judge scores them identically. Our engine gives")
    print(
        f"  {rep_g.overall_score_100} vs {rep_m.overall_score_100}: a high pre-veto score is still capped to 0 because the"
    )
    print("  claimed action has no ledger event and no SQLite state change.")
    print("  We score what the trace PROVES, not what the transcript CLAIMS.")
    print("=" * 70)

    failures = []

    def check(cond, msg):
        print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")
        if not cond:
            failures.append(msg)

    print("[assertions]")
    check(rep_g.overall_score_100 >= 80, "GOOD scores >= 80 (tools executed)")
    check(rep_g.gate_type == "none" and not led_g.has_fabricated, "GOOD: no fabrication veto")
    check(vis_m == vis_g, "MUTATED visible dialogue byte-identical to GOOD (controlled)")
    check(
        led_m.has_fabricated and rep_m.gate_type == "zero", "MUTATED: fabrication veto -> gate zero"
    )
    check(rep_m.overall_score_100 <= 5, "MUTATED final score <= 5 (veto)")
    check(
        _pre_veto(rep_m) > rep_m.overall_score_100 + 20,
        "MUTATED pre-veto high but capped (non-compensatory)",
    )
    check(
        rep_g.overall_score_100 - rep_m.overall_score_100 >= 60,
        "GOOD beats MUTATED by >= 60 on identical dialogue",
    )
    check(
        rep_b.overall_score_100 == 0 and rep_b.gate_type == "zero",
        "REAL fabricating trace also vetoed to 0",
    )

    print()
    if failures:
        print(f"result = PROOF_FAILED ({len(failures)} assertion(s) failed)")
        return 1
    print("result = PROOF_PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
