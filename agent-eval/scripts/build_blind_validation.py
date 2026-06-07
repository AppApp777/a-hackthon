#!/usr/bin/env python3
"""blind_v1 builder — prediction-locked, held-out blind validation set.

Implements Priority 1-2 of the calibration plan (see calibration/blind_v1/README.md):
  1. Exclude CONTAMINATED traces (the 22-item pilot + dev + demo) so the set is
     genuinely held-out from the scoring rules we already tuned.
  2. Deterministic STRATIFIED sampling of N held-out traces across the score range
     (severe / weak / borderline / pass) with tool-call, scenario and model diversity.
  3. Emit an internal manifest + an annotator-facing BLIND manifest (blind_id only).
  4. LOCK the full-system predictions + SHA-256 so nobody can claim we saw the labels
     first and then tuned the scorer.

The LLM-only baseline predictions are locked by a separate step (needs API):
see `scripts/lock_llm_only_baseline.py` (TODO) — same blind_id manifest.

Usage:
  python scripts/build_blind_validation.py --n 32 --seed blind-validation-v1 [--commit <git-hash>]

Stdlib only. Deterministic: same traces + same seed -> identical manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
TRACES_DIR = PROJECT_DIR / "traces"
PILOT_DIR = PROJECT_DIR / "data" / "calibration" / "blind_pilot"
OUT_DIR = PROJECT_DIR / "calibration" / "blind_v1"

# (name, score_lo_inclusive, score_hi_exclusive, target_count) on a 0-100 scale.
STRATA = [
    ("severe", 0.0, 40.0, 8),
    ("weak", 40.0, 60.0, 8),
    ("borderline", 60.0, 80.0, 8),
    ("pass", 80.0, 100.01, 8),
]
MAX_PER_SCENARIO = 3  # only ~14 scenarios exist; 2 caps total at 28 < 32
MAX_PER_MODEL = 6

# Models whose tool-call dialect our harness does not parse → exclude (their tool
# calls leak into the spoken channel and never execute, so scoring them is unfair).
EXCLUDE_MODEL_PREFIXES = ("MiniMax",)
# Raw tool-call markup an agent emits as text when its tool call was NOT parsed.
_TOOL_MARKUP = re.compile(
    r"<\s*(minimax:tool_call|invoke\s+name=|tool_call\b|function_call\b)", re.I
)


def _tool_parse_failed(trace: dict) -> bool:
    """True if any agent turn contains raw tool-call markup while its tool_calls is empty
    (the harness failed to parse the call → it leaked as speech and never executed)."""
    for m in ((trace.get("conversation") or {}).get("messages")) or []:
        if m.get("role") == "agent" and not m.get("tool_calls"):
            if _TOOL_MARKUP.search(m.get("content") or ""):
                return True
    return False


def _anchor_ids() -> set[str]:
    p = PROJECT_DIR / "calibration" / "blind_v1" / "anchors" / "anchor_map.jsonl"
    ids: set[str] = set()
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                ids.add(json.loads(line)["trace_id"])
    return ids


def _find(obj, key):
    """Recursively return the first value for `key` in a nested dict/list, else None."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = _find(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find(v, key)
            if r is not None:
                return r
    return None


def _contaminated_keys() -> set[str]:
    """Collect trace ids / filename stems that influenced scoring (the pilot) → must be excluded.

    The pilot's 22 traces drove the 6 internal-info-leak veto regexes, so they are
    DEVELOPMENT data, not validation data. Over-collecting harmless strings is safe
    (we only ever match against trace ids / stems)."""
    keys: set[str] = set()
    for mapping in (
        PILOT_DIR / "_system_scores_DO_NOT_OPEN.json",
        PILOT_DIR / "traces_blind.json",
    ):
        if not mapping.exists():
            continue
        try:
            data = json.loads(mapping.read_text(encoding="utf-8"))
        except Exception:
            continue

        def walk(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if isinstance(k, str) and len(k) >= 6:
                        keys.add(k)
                    if isinstance(v, str) and len(v) >= 6:
                        keys.add(v)
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)

        walk(data)
    return keys


def load_eligible() -> list[dict]:
    contaminated = _contaminated_keys()
    anchors = _anchor_ids()
    stats = {"minimax": 0, "parse_fail": 0, "anchor": 0, "mock": 0}
    rows: list[dict] = []
    for p in sorted(TRACES_DIR.glob("outbound_*.json")):
        try:
            t = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        sr = t.get("score_report") or {}
        raw = sr.get("overall_score")
        if raw is None:
            continue
        score100 = round(raw * 100, 1) if raw <= 1.0 else round(raw, 1)
        tid = str(t.get("id", ""))
        stem = p.stem
        if tid in contaminated or stem in contaminated:
            continue  # held-out integrity: drop anything that touched scoring development
        # meta_eval synthetic ("mock") traces leak into traces/ — they are NOT real
        # model conversations (empty turns, model_backend points at a source file).
        # They must never enter the human-labelled blind set.
        if _find(t.get("run_metadata") or {}, "agent_type") == "mock":
            stats["mock"] += 1
            continue
        model = _find(t.get("run_metadata") or {}, "model_backend") or "unknown"
        if any(model.startswith(pre) for pre in EXCLUDE_MODEL_PREFIXES):
            stats["minimax"] += 1
            continue
        if tid in anchors:
            stats["anchor"] += 1
            continue  # the 8 anchor traces are not part of the reported validation set
        if _tool_parse_failed(t):
            stats["parse_fail"] += 1
            continue  # agent's tool-call markup left unparsed by our harness → unfair to score

        scenario = t.get("scenario") or {}
        msgs = ((t.get("conversation") or {}).get("messages")) or []
        has_tool = any(m.get("tool_calls") for m in msgs)
        snaps = _find(sr, "state_snapshots") or []
        has_db = bool(_find(t, "call_logs")) or any(
            isinstance(s, dict) and s.get("after_tool_call") not in (None, "initial_state")
            for s in snaps
        )
        # Veto/gate live in the FULL outbound_report (metadata.outbound_report),
        # NOT the top-level score_report subset — that subset has no safety_layer,
        # so reading it would make every prediction veto=False (silent bug).
        rep = (t.get("metadata") or {}).get("outbound_report") or {}
        safety = rep.get("safety_layer") or _find(sr, "safety_layer") or {}
        gate = safety.get("gate_type") or rep.get("gate_type") or _find(sr, "gate_type")
        gate_str = str(gate) if gate else ""
        veto = bool(
            safety.get("safety_triggered")
            or safety.get("has_fabrication")
            # gate_type values: none / cap_040 / cap_060 / cap_070 / zero.
            # "cap_*" and "zero" are noncompensatory veto caps; "none" is not.
            or ("cap" in gate_str)
            or (gate_str == "zero")
        )
        # diagnosis lives in metadata.diagnosis (orchestrator_outbound.py:827), NOT in
        # the top-level score_report subset — _find(sr,...) and t.get("diagnosis") both
        # miss it, which left primary_failure null in every locked prediction.
        diag = (t.get("metadata") or {}).get("diagnosis") or _find(sr, "diagnosis") or {}
        fmodes = diag.get("failure_modes") if isinstance(diag, dict) else None
        primary = fmodes[0] if fmodes else None

        rows.append(
            {
                "trace_id": tid,
                "stem": stem,
                "scenario_id": scenario.get("id", ""),
                "scenario_name": scenario.get("name", ""),
                "model": model,
                "system_score": score100,
                "predicted_veto": veto,
                "gate_type": gate,
                "primary_failure": primary,
                "has_tool_calls": has_tool,
                "has_db_activity": has_db,
                "path": str(p.relative_to(PROJECT_DIR)).replace("\\", "/"),
            }
        )
    print(
        f"excluded: MiniMax={stats['minimax']} parse_fail={stats['parse_fail']} "
        f"anchors={stats['anchor']} mock={stats['mock']}"
    )
    return rows


def stratified_sample(rows: list[dict], n: int, seed: str) -> list[dict]:
    """Greedy seeded selection: fill each score stratum, preferring tool/db traces,
    while respecting per-scenario and per-model caps. Auditable, not clever."""
    rng = random.Random(int.from_bytes(hashlib.sha256(seed.encode()).digest()[:8], "big"))
    per_stratum = max(1, n // len(STRATA))
    picked: list[dict] = []
    scen_count: dict[str, int] = {}
    model_count: dict[str, int] = {}

    def eligible(r):
        return (
            scen_count.get(r["scenario_id"], 0) < MAX_PER_SCENARIO
            and model_count.get(r["model"], 0) < MAX_PER_MODEL
        )

    def take(r):
        picked.append(r)
        scen_count[r["scenario_id"]] = scen_count.get(r["scenario_id"], 0) + 1
        model_count[r["model"]] = model_count.get(r["model"], 0) + 1

    shortfalls = []
    # Build per-stratum pools, then fill the SCARCEST stratum first so the thin
    # high-score buckets claim scenario/model budget before the dense low buckets eat it.
    pools = []
    for name, lo, hi, _t in STRATA:
        pool = [r for r in rows if lo <= r["system_score"] < hi]
        rng.shuffle(pool)
        pool.sort(key=lambda r: 0 if (r["has_tool_calls"] or r["has_db_activity"]) else 1)
        pools.append((name, pool))
    pools.sort(key=lambda np: len(np[1]))
    for name, pool in pools:
        got = 0
        for r in pool:
            if got >= per_stratum:
                break
            if r not in picked and eligible(r):
                take(r)
                got += 1
        if got < per_stratum:
            shortfalls.append((name, got, per_stratum))

    # top up to n if some strata were short, relaxing strata but keeping caps
    if len(picked) < n:
        rest = [r for r in rows if r not in picked]
        rng.shuffle(rest)
        for r in rest:
            if len(picked) >= n:
                break
            if eligible(r):
                take(r)

    rng.shuffle(picked)  # randomize blind_id order so id != difficulty
    for i, r in enumerate(picked, 1):
        r["blind_id"] = f"BV1_{i:04d}"
        r["selection_bucket"] = next(
            (nm for nm, lo, hi, _ in STRATA if lo <= r["system_score"] < hi), "other"
        )
    return picked, shortfalls


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_outputs(picked: list[dict], commit: str, seed: str, force: bool = False) -> None:
    # The locked predictions are a commitment made BEFORE labels are seen. Silently
    # overwriting them would defeat the whole point of "locking". Require --force.
    locked = OUT_DIR / "predictions_full_system.locked.jsonl"
    if locked.exists() and not force:
        raise SystemExit(
            f"REFUSING to overwrite an existing lock:\n  {locked}\n"
            "Locked predictions are frozen before annotation. Overwriting them after\n"
            "seeing labels would invalidate the blind validation. Re-freeze only with\n"
            "--force, and record why in seed.txt / CHANGELOG."
        )
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # internal manifest (full reproducibility)
    manifest = OUT_DIR / "validation_manifest.jsonl"
    with manifest.open("w", encoding="utf-8") as f:
        for r in picked:
            f.write(
                json.dumps(
                    {
                        k: r[k]
                        for k in (
                            "blind_id",
                            "trace_id",
                            "scenario_id",
                            "model",
                            "system_score",
                            "predicted_veto",
                            "selection_bucket",
                            "has_tool_calls",
                            "has_db_activity",
                            "path",
                        )
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    # annotator-facing blind manifest — NO score / model / veto / bucket
    blind = OUT_DIR / "manifest_blind.jsonl"
    with blind.open("w", encoding="utf-8") as f:
        for r in picked:
            f.write(
                json.dumps(
                    {
                        "blind_id": r["blind_id"],
                        "scenario_name": r["scenario_name"],
                        "trace_path": r["path"],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    # locked full-system predictions
    preds = OUT_DIR / "predictions_full_system.locked.jsonl"
    with preds.open("w", encoding="utf-8") as f:
        for r in picked:
            f.write(
                json.dumps(
                    {
                        "blind_id": r["blind_id"],
                        "trace_id": r["trace_id"],
                        "system_score": r["system_score"],
                        "veto": r["predicted_veto"],
                        "gate_type": r["gate_type"],
                        "primary_failure": r["primary_failure"],
                        "scoring_commit": commit,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    # checksums
    sha = OUT_DIR / "blind_v1.sha256"
    with sha.open("w", encoding="utf-8") as f:
        for fp in (manifest, blind, preds):
            f.write(f"{_sha256(fp)}  {fp.name}\n")

    (OUT_DIR / "seed.txt").write_text(f"seed={seed}\nscoring_commit={commit}\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=32)
    ap.add_argument("--seed", default="blind-validation-v1")
    ap.add_argument("--commit", default="UNCOMMITTED", help="scoring freeze git hash")
    ap.add_argument("--force", action="store_true", help="re-freeze: overwrite an existing lock")
    args = ap.parse_args()

    if args.commit == "UNCOMMITTED":
        print(
            "WARNING: --commit not set → scoring_commit=UNCOMMITTED (predictions not "
            "attributable to a scorer version). Pass --commit <git-hash> for a real freeze."
        )

    rows = load_eligible()
    picked, shortfalls = stratified_sample(rows, args.n, args.seed)
    write_outputs(picked, args.commit, args.seed, force=args.force)

    # summary
    models: dict[str, int] = {}
    scen = set()
    tool = sum(1 for r in picked if r["has_tool_calls"] or r["has_db_activity"])
    buckets: dict[str, int] = {}
    for r in picked:
        models[r["model"]] = models.get(r["model"], 0) + 1
        scen.add(r["scenario_id"])
        buckets[r["selection_bucket"]] = buckets.get(r["selection_bucket"], 0) + 1
    print(f"eligible traces: {len(rows)}")
    print(f"selected: {len(picked)} -> {OUT_DIR}")
    print(f"strata: {buckets}")
    print(f"tool/db coverage: {tool}/{len(picked)}")
    print(f"scenarios covered: {len(scen)}")
    print(f"models: {models}")
    if shortfalls:
        print(f"WARNING shortfalls (stratum, got, target): {shortfalls}")


if __name__ == "__main__":
    main()
