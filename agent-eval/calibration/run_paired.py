"""Paired experiment — direct execution, no subprocess wrapper."""

import json
import math
import sys
import traceback
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

SCENARIOS = [
    "delivery_confirm_basic.json",
    "simple_satisfaction_survey.json",
    "rider_feimaotui_notify.json",
    "after_sales_complaint.json",
    "refund_over_budget.json",
]

REPEATS = 3
MODEL = "LongCat-2.0-Preview"
LOG = Path("calibration/paired_log.jsonl")
REPORT = Path("calibration/paired_experiment_report.json")


def run_one(scenario_path: str, model: str):
    from run_outbound import run_single

    trace = run_single(scenario_path, model, no_llm_judge=True, trace_dir="traces", verbose=False)
    return trace


def main():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    results = defaultdict(list)
    total = len(SCENARIOS) * REPEATS
    done = 0

    with open(LOG, "a", encoding="utf-8") as logf:
        for scenario_file in SCENARIOS:
            scenario_path = f"scenarios/outbound/{scenario_file}"
            scenario_name = scenario_file.replace(".json", "")

            for rep in range(REPEATS):
                done += 1
                msg = f"[{done}/{total}] {scenario_name} rep={rep + 1}"
                print(msg, flush=True)
                try:
                    trace = run_one(scenario_path, MODEL)
                    if trace and isinstance(trace, dict):
                        sr = trace.get("score_report", {})
                        score = sr.get("overall_score", 0) * 100
                        entry = {
                            "scenario": scenario_name,
                            "repeat": rep + 1,
                            "score": round(score, 1),
                            "trace_id": trace.get("id", ""),
                        }
                    else:
                        score = 0
                        entry = {
                            "scenario": scenario_name,
                            "repeat": rep + 1,
                            "score": 0,
                            "error": "no trace",
                        }
                except Exception as e:
                    score = 0
                    entry = {
                        "scenario": scenario_name,
                        "repeat": rep + 1,
                        "score": 0,
                        "error": str(e)[:200],
                    }
                    traceback.print_exc()

                results[scenario_name].append(score)
                logf.write(json.dumps(entry, ensure_ascii=False) + "\n")
                logf.flush()
                print(f"  → {score:.1f}%", flush=True)

    def mean(v):
        return sum(v) / len(v) if v else 0

    def sd(v):
        if len(v) < 2:
            return 0
        m = mean(v)
        return math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))

    print("\n" + "=" * 60)
    all_scores = []
    summary = []
    for sf in SCENARIOS:
        sn = sf.replace(".json", "")
        scores = results[sn]
        if not scores:
            continue
        all_scores.extend(scores)
        m, s = mean(scores), sd(scores)
        print(f"{sn:<35} {m:>6.1f}% ±{s:.1f}")
        summary.append(
            {
                "scenario": sn,
                "mean": round(m, 1),
                "stdev": round(s, 1),
                "scores": [round(x, 1) for x in scores],
            }
        )

    report = {
        "model": MODEL,
        "repeats": REPEATS,
        "scenarios": len(SCENARIOS),
        "overall_mean": round(mean(all_scores), 1),
        "overall_stdev": round(sd(all_scores), 1),
        "summary": summary,
    }
    with open(REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n报告: {REPORT}")


if __name__ == "__main__":
    main()
