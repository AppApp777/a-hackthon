"""Paired experiment: same scenarios, multiple repeats, statistical summary.

Usage:
    python calibration/paired_experiment.py --model LongCat-2.0-Preview --repeats 3 [--scenarios 10]
"""

import argparse
import json
import math
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

SCENARIO_DIR = Path("scenarios/outbound")

SCENARIO_PRIORITY = [
    "delivery_confirm_basic.json",
    "simple_satisfaction_survey.json",
    "rider_feimaotui_notify.json",
    "course_livestream_upgrade.json",
    "after_sales_complaint.json",
    "refund_over_budget.json",
    "compliance_conflict.json",
    "delay_notify_difficult.json",
    "multi_issue_combo.json",
    "stress_test_extreme.json",
]


def run_one(scenario_path: str, model: str) -> dict | None:
    cmd = [
        sys.executable,
        "run_outbound.py",
        scenario_path,
        "--model",
        model,
        "--no-llm-judge",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, encoding="utf-8")
        for line in result.stdout.split("\n"):
            if "轨迹ID:" in line:
                trace_id = line.split("轨迹ID:")[1].strip()
                trace_path = Path("traces") / f"outbound_{trace_id}.json"
                if trace_path.exists():
                    with open(trace_path, encoding="utf-8") as f:
                        trace = json.load(f)
                    sr = trace.get("score_report", {})
                    ob = trace.get("metadata", {}).get("outbound_report", {})
                    return {
                        "trace_id": trace_id,
                        "overall": sr.get("overall_score", 0),
                        "hard_score": sr.get("hard_score", 0),
                        "soft_score": sr.get("soft_score"),
                        "step_compliance": ob.get("step_compliance_score"),
                        "branch_accuracy": ob.get("branch_accuracy_score"),
                        "temporal_order": ob.get("temporal_order_score"),
                        "path_alignment": ob.get("alignment_score"),
                    }
    except subprocess.TimeoutExpired:
        print(f"  TIMEOUT: {scenario_path}")
    except Exception as e:
        print(f"  ERROR: {e}")
    return None


def mean(vals: list[float]) -> float:
    return sum(vals) / len(vals) if vals else 0


def stdev(vals: list[float]) -> float:
    if len(vals) < 2:
        return 0
    m = mean(vals)
    return math.sqrt(sum((x - m) ** 2 for x in vals) / (len(vals) - 1))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--scenarios", type=int, default=10)
    parser.add_argument("--output", default="calibration/paired_experiment_report.json")
    args = parser.parse_args()

    scenarios = SCENARIO_PRIORITY[: args.scenarios]
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    results = defaultdict(list)
    all_runs = []
    total = len(scenarios) * args.repeats
    done = 0

    for scenario_file in scenarios:
        scenario_path = str(SCENARIO_DIR / scenario_file)
        scenario_name = scenario_file.replace(".json", "")

        for rep in range(args.repeats):
            done += 1
            print(f"[{done}/{total}] {scenario_name} (repeat {rep + 1}/{args.repeats})")
            result = run_one(scenario_path, args.model)
            if result:
                score = result["overall"] * 100
                results[scenario_name].append(score)
                all_runs.append(
                    {
                        "scenario": scenario_name,
                        "repeat": rep + 1,
                        "score": round(score, 1),
                        **{
                            k: round(v, 3) if v is not None else None
                            for k, v in result.items()
                            if k != "trace_id"
                        },
                        "trace_id": result["trace_id"],
                    }
                )
                print(f"  → {score:.1f}%")
            else:
                print("  → FAILED")

    print("\n" + "=" * 70)
    print(f"配对实验结果 — {args.model} × {len(scenarios)} 场景 × {args.repeats} 次")
    print("=" * 70)
    print(f"{'场景':<35} {'均值':>8} {'标准差':>8} {'最低':>8} {'最高':>8} {'N':>4}")
    print("-" * 70)

    summary = []
    all_scores = []
    for scenario_name in [s.replace(".json", "") for s in scenarios]:
        scores = results[scenario_name]
        if not scores:
            continue
        all_scores.extend(scores)
        m = mean(scores)
        s = stdev(scores)
        lo = min(scores)
        hi = max(scores)
        print(f"{scenario_name:<35} {m:>7.1f}% {s:>7.1f}% {lo:>7.1f}% {hi:>7.1f}% {len(scores):>4}")
        summary.append(
            {
                "scenario": scenario_name,
                "mean": round(m, 1),
                "stdev": round(s, 1),
                "min": round(lo, 1),
                "max": round(hi, 1),
                "n": len(scores),
            }
        )

    print("-" * 70)
    if all_scores:
        print(
            f"{'总计':<35} {mean(all_scores):>7.1f}% {stdev(all_scores):>7.1f}% {min(all_scores):>7.1f}% {max(all_scores):>7.1f}% {len(all_scores):>4}"
        )

    report = {
        "model": args.model,
        "repeats": args.repeats,
        "scenario_count": len(scenarios),
        "total_runs": len(all_runs),
        "overall_mean": round(mean(all_scores), 1) if all_scores else 0,
        "overall_stdev": round(stdev(all_scores), 1) if all_scores else 0,
        "summary": summary,
        "runs": all_runs,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\n报告已保存: {output_path}")


if __name__ == "__main__":
    main()
