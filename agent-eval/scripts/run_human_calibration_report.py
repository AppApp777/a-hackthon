#!/usr/bin/env python3
"""Human calibration pilot report — compares human blind annotations with system scores.

Reads:
  - data/calibration/blind_pilot/人工标注表_已填.csv  (22 human annotations)
  - data/calibration/blind_pilot/_system_scores_DO_NOT_OPEN.json  (trace mapping)
  - data/calibration/blind_pilot/traces_v2/*.json  (system score_reports)

Outputs:
  - reports/human_calibration_pilot.json
  - reports/human_calibration_pilot.md

Metrics:
  - MAE (mean absolute error, 0-100 scale)
  - Spearman rank correlation
  - Bucket accuracy (A/B/C/F grade agreement)
  - Veto precision / recall / F1
  - Bootstrap 95% CI for MAE and Spearman
  - Baseline comparison (naive keyword scorer)
"""

from __future__ import annotations

import csv
import json
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
DATA_DIR = PROJECT_DIR / "data" / "calibration" / "blind_pilot"
TRACES_DIR = DATA_DIR / "traces_v2"
REPORTS_DIR = PROJECT_DIR / "reports"

HUMAN_CSV = DATA_DIR / "人工标注表_已填.csv"
MAPPING_FILE = DATA_DIR / "_system_scores_DO_NOT_OPEN.json"


@dataclass
class PairedScore:
    idx: str
    trace_id: str
    scenario_id: str
    human_score: float
    human_grade: str
    human_veto: bool
    human_veto_reason: str
    system_score: float
    system_grade: str
    system_veto: bool
    naive_score: float
    naive_veto: bool
    human_dims: dict[str, float] = field(default_factory=dict)
    system_rubric_total: float = 0.0
    system_rubric_max: float = 0.0


def _score_to_grade(score: float) -> str:
    if score >= 80:
        return "A"
    if score >= 60:
        return "B"
    if score >= 40:
        return "C"
    return "F"


def _parse_bool(s: str) -> bool:
    return s.strip().lower() in ("是", "yes", "true", "1")


def _spearman(x: list[float], y: list[float]) -> float:
    n = len(x)
    if n < 3:
        return 0.0

    def _rank(arr):
        indexed = sorted(enumerate(arr), key=lambda t: t[1])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j < n - 1 and indexed[j + 1][1] == indexed[j][1]:
                j += 1
            avg_rank = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                ranks[indexed[k][0]] = avg_rank
            i = j + 1
        return ranks

    rx = _rank(x)
    ry = _rank(y)
    d2 = sum((a - b) ** 2 for a, b in zip(rx, ry, strict=False))
    return 1 - 6 * d2 / (n * (n * n - 1))


def _bootstrap_ci(
    pairs: list[PairedScore],
    metric_fn,
    n_bootstrap: int = 2000,
    ci: float = 0.95,
    seed: int = 42,
) -> dict:
    rng = random.Random(seed)
    n = len(pairs)
    samples = []
    for _ in range(n_bootstrap):
        boot = [pairs[rng.randint(0, n - 1)] for _ in range(n)]
        val = metric_fn(boot)
        if val is not None:
            samples.append(val)

    samples.sort()
    alpha = (1 - ci) / 2
    lo_idx = int(alpha * len(samples))
    hi_idx = int((1 - alpha) * len(samples))
    return {
        "point": metric_fn(pairs),
        "ci_lower": samples[lo_idx] if samples else None,
        "ci_upper": samples[min(hi_idx, len(samples) - 1)] if samples else None,
        "ci_level": ci,
        "n_bootstrap": n_bootstrap,
    }


def _mae(pairs: list[PairedScore]) -> float:
    if not pairs:
        return 0.0
    return sum(abs(p.human_score - p.system_score) for p in pairs) / len(pairs)


def _spearman_from_pairs(pairs: list[PairedScore]) -> float:
    return _spearman(
        [p.human_score for p in pairs],
        [p.system_score for p in pairs],
    )


def _veto_metrics(pairs: list[PairedScore]) -> dict:
    tp = sum(1 for p in pairs if p.human_veto and p.system_veto)
    fp = sum(1 for p in pairs if not p.human_veto and p.system_veto)
    fn = sum(1 for p in pairs if p.human_veto and not p.system_veto)
    tn = sum(1 for p in pairs if not p.human_veto and not p.system_veto)
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
    }


def _bucket_accuracy(pairs: list[PairedScore]) -> dict:
    correct = sum(1 for p in pairs if p.human_grade == p.system_grade)
    total = len(pairs)
    confusion: dict[str, dict[str, int]] = {}
    for p in pairs:
        confusion.setdefault(p.human_grade, {})
        confusion[p.human_grade][p.system_grade] = (
            confusion[p.human_grade].get(p.system_grade, 0) + 1
        )
    return {
        "accuracy": round(correct / total, 3) if total else 0,
        "correct": correct,
        "total": total,
        "confusion_matrix": confusion,
    }


def load_pairs() -> list[PairedScore]:
    with open(MAPPING_FILE, encoding="utf-8") as f:
        mapping = json.load(f)

    human_rows: dict[str, dict] = {}
    with open(HUMAN_CSV, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            idx = row["对话编号"].strip().zfill(2)
            human_rows[idx] = row

    sys.path.insert(0, str(PROJECT_DIR))
    from baseline_comparison import naive_score_trace

    pairs = []
    for idx in sorted(mapping.keys()):
        entry = mapping[idx]
        trace_id = entry["trace_id"]
        scenario_id = entry["scenario_id"]

        trace_file = TRACES_DIR / f"{trace_id}.json"
        if not trace_file.exists():
            print(f"WARNING: trace file not found: {trace_file}")
            continue

        with open(trace_file, encoding="utf-8") as f:
            trace = json.load(f)

        sr = trace.get("score_report", {})
        system_overall = sr.get("overall_score", 0) * 100
        system_veto_cap = sr.get("veto_cap", 1.0)
        gate_type = sr.get("gate_type", "none") or "none"
        system_veto = gate_type != "none" or (
            system_veto_cap is not None and system_veto_cap < 1.0 and system_veto_cap > 0
        )

        rubric = sr.get("rubric", {})
        rubric_total = rubric.get("rubric_total", 0)
        rubric_max = rubric.get("rubric_max", 0)

        trace_for_naive = json.loads(json.dumps(trace))
        for msg in trace_for_naive.get("conversation", {}).get("messages", []):
            if msg.get("role") == "agent":
                msg["role"] = "assistant"
        naive_score, naive_veto = naive_score_trace(trace_for_naive)

        human_row = human_rows.get(idx)
        if not human_row:
            print(f"WARNING: no human annotation for idx {idx}")
            continue

        human_score = float(human_row.get("总分", 0))
        human_grade = human_row.get("评级", "").strip()
        human_veto = _parse_bool(human_row.get("veto", ""))
        human_veto_reason = human_row.get("veto原因", "").strip()

        dim_cols = [
            "任务完成度",
            "工具正确性",
            "时序正确性",
            "约束合规",
            "知识准确性",
            "安全隐私",
            "软质量",
        ]
        human_dims = {}
        for col in dim_cols:
            val = human_row.get(col, "").strip()
            if val and val != "NA":
                try:
                    human_dims[col] = float(val)
                except ValueError:
                    pass

        pairs.append(
            PairedScore(
                idx=idx,
                trace_id=trace_id,
                scenario_id=scenario_id,
                human_score=human_score,
                human_grade=human_grade,
                human_veto=human_veto,
                human_veto_reason=human_veto_reason,
                system_score=round(system_overall, 1),
                system_grade=_score_to_grade(system_overall),
                system_veto=system_veto,
                naive_score=naive_score,
                naive_veto=naive_veto,
                human_dims=human_dims,
                system_rubric_total=rubric_total,
                system_rubric_max=rubric_max,
            )
        )

    return pairs


def build_report(pairs: list[PairedScore]) -> dict[str, Any]:
    mae_result = _bootstrap_ci(pairs, _mae)
    spearman_result = _bootstrap_ci(pairs, _spearman_from_pairs)
    veto = _veto_metrics(pairs)
    bucket = _bucket_accuracy(pairs)

    naive_mae = sum(abs(p.human_score - p.naive_score) for p in pairs) / len(pairs)
    full_mae = mae_result["point"]
    naive_spearman = _spearman(
        [p.human_score for p in pairs],
        [p.naive_score for p in pairs],
    )

    per_trace = []
    for p in pairs:
        per_trace.append(
            {
                "idx": p.idx,
                "trace_id": p.trace_id,
                "scenario_id": p.scenario_id,
                "human_score": p.human_score,
                "system_score": p.system_score,
                "naive_score": p.naive_score,
                "error": round(p.system_score - p.human_score, 1),
                "abs_error": round(abs(p.system_score - p.human_score), 1),
                "human_grade": p.human_grade,
                "system_grade": p.system_grade,
                "grade_match": p.human_grade == p.system_grade,
                "human_veto": p.human_veto,
                "system_veto": p.system_veto,
                "human_veto_reason": p.human_veto_reason,
            }
        )

    human_scores = [p.human_score for p in pairs]
    system_scores = [p.system_score for p in pairs]

    return {
        "summary": {
            "n_traces": len(pairs),
            "human_mean": round(sum(human_scores) / len(pairs), 1),
            "system_mean": round(sum(system_scores) / len(pairs), 1),
            "naive_mean": round(sum(p.naive_score for p in pairs) / len(pairs), 1),
            "human_std": round(
                (sum((s - sum(human_scores) / len(pairs)) ** 2 for s in human_scores) / len(pairs))
                ** 0.5,
                1,
            ),
            "system_std": round(
                (
                    sum((s - sum(system_scores) / len(pairs)) ** 2 for s in system_scores)
                    / len(pairs)
                )
                ** 0.5,
                1,
            ),
        },
        "mae": mae_result,
        "spearman": spearman_result,
        "veto": veto,
        "bucket_accuracy": bucket,
        "baseline_comparison": {
            "naive_mae": round(naive_mae, 1),
            "full_system_mae": round(full_mae, 1),
            "mae_improvement": round(naive_mae - full_mae, 1),
            "naive_spearman": round(naive_spearman, 3),
            "full_system_spearman": round(spearman_result["point"], 3),
        },
        "veto_gap_analysis": {
            "human_veto_count": sum(1 for p in pairs if p.human_veto),
            "system_veto_count": sum(1 for p in pairs if p.system_veto),
            "human_veto_reasons": [
                {"idx": p.idx, "reason": p.human_veto_reason, "scenario": p.scenario_id}
                for p in pairs
                if p.human_veto and p.human_veto_reason
            ],
        },
        "per_trace": per_trace,
    }


def render_markdown(report: dict) -> str:
    s = report["summary"]
    mae = report["mae"]
    sp = report["spearman"]
    veto = report["veto"]
    bucket = report["bucket_accuracy"]
    bl = report["baseline_comparison"]
    vgap = report["veto_gap_analysis"]

    lines = [
        "# Human Calibration Pilot Report",
        "",
        "**22 traces blind-annotated by a single human rater, compared against system scores.**",
        "",
        "## Summary Statistics",
        "",
        "| Metric | Human | System | Naive Baseline |",
        "|---|---|---|---|",
        f"| Mean Score | {s['human_mean']} | {s['system_mean']} | {s['naive_mean']} |",
        f"| Std Dev | {s['human_std']} | {s['system_std']} | — |",
        "",
        "## Core Metrics",
        "",
        "| Metric | Value | 95% CI |",
        "|---|---|---|",
        f"| MAE | **{mae['point']:.1f}** | [{mae['ci_lower']:.1f}, {mae['ci_upper']:.1f}] |",
        f"| Spearman ρ | **{sp['point']:.3f}** | [{sp['ci_lower']:.3f}, {sp['ci_upper']:.3f}] |",
        f"| Bucket Accuracy | **{bucket['accuracy']:.1%}** | ({bucket['correct']}/{bucket['total']}) |",
        "",
        "## Veto Gate Analysis",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Human Veto Count | {vgap['human_veto_count']}/22 ({vgap['human_veto_count'] / 22:.0%}) |",
        f"| System Veto Count | {vgap['system_veto_count']}/22 ({vgap['system_veto_count'] / 22:.0%}) |",
        f"| Precision | {veto['precision']:.3f} |",
        f"| Recall | {veto['recall']:.3f} |",
        f"| F1 | {veto['f1']:.3f} |",
        f"| TP/FP/FN/TN | {veto['tp']}/{veto['fp']}/{veto['fn']}/{veto['tn']} |",
        "",
    ]

    if vgap["human_veto_reasons"]:
        lines.append("### Human Veto Reasons (system missed)")
        lines.append("")
        lines.append("| # | Scenario | Reason |")
        lines.append("|---|---|---|")
        for r in vgap["human_veto_reasons"]:
            lines.append(f"| {r['idx']} | {r['scenario']} | {r['reason']} |")
        lines.append("")

    lines.extend(
        [
            "## Baseline Comparison",
            "",
            "| | Naive Keyword | Full System | Δ |",
            "|---|---|---|---|",
            f"| MAE vs Human | {bl['naive_mae']:.1f} | {bl['full_system_mae']:.1f} | {bl['mae_improvement']:+.1f} |",
            f"| Spearman ρ | {bl['naive_spearman']:.3f} | {bl['full_system_spearman']:.3f} | {bl['full_system_spearman'] - bl['naive_spearman']:+.3f} |",
            "",
            "## Per-Trace Detail",
            "",
            "| # | Scenario | Human | System | Naive | Error | Grade H→S | Veto H/S |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    for t in report["per_trace"]:
        grade_str = f"{t['human_grade']}→{t['system_grade']}" + (" ✓" if t["grade_match"] else " ✗")
        veto_str = f"{'Y' if t['human_veto'] else 'N'}/{'Y' if t['system_veto'] else 'N'}"
        lines.append(
            f"| {t['idx']} | {t['scenario_id'][:30]} | {t['human_score']:.0f} | {t['system_score']:.1f} | {t['naive_score']:.1f} | {t['error']:+.1f} | {grade_str} | {veto_str} |"
        )

    lines.extend(
        [
            "",
            "## Confusion Matrix (Grade Buckets)",
            "",
            "Human \\ System | A | B | C | F",
            "---|---|---|---|---",
        ]
    )

    cm = bucket["confusion_matrix"]
    for hg in ["A", "B", "C", "F"]:
        row = cm.get(hg, {})
        cells = [str(row.get(sg, 0)) for sg in ["A", "B", "C", "F"]]
        lines.append(f"{hg} | {' | '.join(cells)}")

    lines.extend(
        [
            "",
            "---",
            f"*Generated by run_human_calibration_report.py | {len(report['per_trace'])} traces | bootstrap n=2000*",
        ]
    )

    return "\n".join(lines)


def main():
    pairs = load_pairs()
    if not pairs:
        print("ERROR: No paired data found")
        return 1

    print(f"Loaded {len(pairs)} paired human-system annotations")

    report = build_report(pairs)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    json_path = REPORTS_DIR / "human_calibration_pilot.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"JSON report: {json_path}")

    md_path = REPORTS_DIR / "human_calibration_pilot.md"
    md_content = render_markdown(report)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    print(f"Markdown report: {md_path}")

    print("\n=== Key Results ===")
    print(
        f"MAE: {report['mae']['point']:.1f} (95% CI: [{report['mae']['ci_lower']:.1f}, {report['mae']['ci_upper']:.1f}])"
    )
    print(
        f"Spearman ρ: {report['spearman']['point']:.3f} (95% CI: [{report['spearman']['ci_lower']:.3f}, {report['spearman']['ci_upper']:.3f}])"
    )
    print(f"Bucket Accuracy: {report['bucket_accuracy']['accuracy']:.1%}")
    print(
        f"Veto F1: {report['veto']['f1']:.3f} (P={report['veto']['precision']:.3f}, R={report['veto']['recall']:.3f})"
    )
    print(f"Baseline MAE improvement: {report['baseline_comparison']['mae_improvement']:+.1f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
