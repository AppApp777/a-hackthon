"""Meta-evaluation metrics calculator.

Computes 7 metrics from the meta_eval_summary.json produced by meta_eval_runner.py:
  1. Discrimination power  — good > medium > flawed ordering rate
  2. Violation recall       — % of seeded flaws caught
  3. Test-retest stability  — ICC between duplicate runs (placeholder until data)
  4. Human-machine agreement — Spearman ρ (after human blind review)
  5. Anti-cheat             — flawed agent never scores above medium
  6. Anti-bias              — score variance under irrelevant perturbation
  7. Weight transparency    — breakdown available and consistent

Usage:
    python meta_eval_metrics.py                          # from summary
    python meta_eval_metrics.py --human human_scores.json  # with human data
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

SUMMARY_PATH = Path(__file__).parent / "traces" / "meta_eval" / "meta_eval_summary.json"
REPORT_PATH = Path(__file__).parent / "traces" / "meta_eval" / "meta_eval_report.json"


def load_summary(path: Path = SUMMARY_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data["results"]


def _group_by_scenario(results: list[dict]) -> dict[str, dict[str, dict]]:
    """Returns {scenario_name: {condition: result_dict}}."""
    grouped: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in results:
        if "error" in r:
            continue
        name = r.get("scenario_name") or r.get("scenario_file", "?")
        grouped[name][r["condition"]] = r
    return dict(grouped)


# ── Metric 1: Discrimination ──


def compute_discrimination(results: list[dict]) -> dict:
    """Good > medium > flawed in how many scenarios?"""
    by_scenario = _group_by_scenario(results)
    total_full = 0
    correct_full = 0
    total_gm = 0
    correct_pair_gm = 0
    total_mf = 0
    correct_pair_mf = 0

    details = []
    for scenario, conds in by_scenario.items():
        g = conds.get("good", {}).get("overall_score")
        m = conds.get("medium", {}).get("overall_score")
        f = conds.get("flawed", {}).get("overall_score")

        detail = {"scenario": scenario}
        if g is not None:
            detail["good"] = round(g, 3)
        if m is not None:
            detail["medium"] = round(m, 3)
        if f is not None:
            detail["flawed"] = round(f, 3)

        if g is not None and m is not None:
            total_gm += 1
            if g >= m:
                correct_pair_gm += 1
        if m is not None and f is not None:
            total_mf += 1
            if m >= f:
                correct_pair_mf += 1
        if g is not None and m is not None and f is not None:
            total_full += 1
            if g >= m and m >= f:
                correct_full += 1
            detail["order_correct"] = g >= m >= f

        details.append(detail)

    return {
        "total_scenarios_full": total_full,
        "full_order_correct": correct_full,
        "full_order_rate": round(correct_full / total_full, 3) if total_full else None,
        "good_ge_medium": f"{correct_pair_gm}/{total_gm}" if total_gm else None,
        "medium_ge_flawed": f"{correct_pair_mf}/{total_mf}" if total_mf else None,
        "medium_ge_flawed_rate": round(correct_pair_mf / total_mf, 3) if total_mf else None,
        "details": details,
    }


# ── Metric 2: Violation Recall ──


def compute_violation_recall(results: list[dict]) -> dict:
    """For flawed agent: how many of its seeded flaws did the evaluator catch?"""
    SEEDED_FLAWS = {
        "opening": {
            "keywords": ["开场白", "opening", "speech_protocol"],
            "flag_field": "opening_correct",
            "flag_bad": False,
        },
        "forbidden": {
            "keywords": ["禁止行为", "forbidden", "forbidden_behavior"],
            "flag_field": "forbidden_violations",
            "flag_bad_gt": 0,
        },
        "tool": {
            "keywords": ["工具", "tool", "tool_usage", "未调用"],
            "flag_field": None,
        },
        "step": {
            "keywords": ["步骤", "step", "step_compliance"],
            "flag_field": "step_compliance",
            "flag_bad_lt": 0.5,
        },
        "closing": {
            "keywords": ["结束语", "closing", "speech_protocol.*结束"],
            "flag_field": "closing_correct",
            "flag_bad": False,
        },
    }

    flawed_runs = [r for r in results if r.get("condition") == "flawed" and "error" not in r]
    total_flawed = len(flawed_runs)
    all_caught = []
    all_missed = []

    for run in flawed_runs:
        failures = run.get("failures", [])
        failure_text = " ".join(str(f) for f in failures)

        caught = []
        missed = []
        for flaw_name, flaw_def in SEEDED_FLAWS.items():
            detected = False
            for kw in flaw_def["keywords"]:
                if kw in failure_text:
                    detected = True
                    break

            if not detected:
                ff = flaw_def.get("flag_field")
                if ff:
                    val = run.get(ff)
                    if "flag_bad" in flaw_def and val == flaw_def["flag_bad"]:
                        detected = True
                    if (
                        "flag_bad_gt" in flaw_def
                        and val is not None
                        and val > flaw_def["flag_bad_gt"]
                    ):
                        detected = True
                    if (
                        "flag_bad_lt" in flaw_def
                        and val is not None
                        and val < flaw_def["flag_bad_lt"]
                    ):
                        detected = True

            if detected:
                caught.append(flaw_name)
            else:
                missed.append(flaw_name)

        all_caught.append(caught)
        all_missed.append(missed)

    total_possible = total_flawed * len(SEEDED_FLAWS)
    total_caught = sum(len(c) for c in all_caught)

    return {
        "flawed_runs": total_flawed,
        "seeded_flaw_types": list(SEEDED_FLAWS.keys()),
        "total_possible": total_possible,
        "total_caught": total_caught,
        "recall_rate": round(total_caught / total_possible, 3) if total_possible else None,
        "per_run": [
            {"caught": c, "missed": m} for c, m in zip(all_caught, all_missed, strict=False)
        ],
    }


# ── Metric 3: Stability ──


def compute_stability(results: list[dict], condition_filter: str | None = "medium") -> dict:
    """ICC(3,1) from repeated runs of the same scenario+condition+scoring_mode.

    Args:
        condition_filter: only use this condition for stability (default: medium).
            None = use all conditions.
    """
    from collections import defaultdict

    groups: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for r in results:
        if "error" in r or r.get("overall_score") is None:
            continue
        cond = r["condition"]
        if condition_filter and cond != condition_filter:
            continue
        name = r.get("scenario_name") or r.get("scenario_file", "?")
        scoring_mode = "full" if r.get("soft_score") is not None else "hard_only"
        groups[(name, cond, scoring_mode)].append(r["overall_score"])

    repeated = {k: v for k, v in groups.items() if len(v) >= 3}
    if not repeated:
        return {
            "note": "无重复运行数据。需要同一场景+条件跑多次。",
            "icc": None,
            "mean_cv": None,
        }

    cvs = []
    per_group = []
    for (scenario, cond, smode), scores in sorted(repeated.items()):
        import statistics

        mean_s = statistics.mean(scores)
        std_s = statistics.stdev(scores) if len(scores) > 1 else 0.0
        cv = (std_s / mean_s * 100) if mean_s > 0 else 0.0
        cvs.append(cv)
        per_group.append(
            {
                "scenario": scenario,
                "condition": cond,
                "scoring_mode": smode,
                "runs": len(scores),
                "scores": [round(s, 3) for s in scores],
                "mean": round(mean_s, 3),
                "std": round(std_s, 3),
                "cv_pct": round(cv, 1),
            }
        )

    mean_cv = sum(cvs) / len(cvs) if cvs else None

    # ICC(3,1) two-way mixed, single measures, consistency
    # rows = scenarios (subjects), cols = runs (raters)
    icc = None
    run_counts = [len(v) for v in repeated.values()]
    if len(repeated) >= 2:
        import numpy as np

        k = min(run_counts)  # truncate to shortest group
        n = len(repeated)  # number of scenarios
        data = np.array([v[:k] for v in repeated.values()])  # (n_scenarios, n_runs)
        grand_mean = data.mean()
        scenario_means = data.mean(axis=1)  # mean across runs per scenario
        run_means = data.mean(axis=0)  # mean across scenarios per run
        SS_subjects = k * np.sum((scenario_means - grand_mean) ** 2)
        SS_raters = n * np.sum((run_means - grand_mean) ** 2)
        SS_total = np.sum((data - grand_mean) ** 2)
        SS_error = SS_total - SS_subjects - SS_raters
        MS_subjects = SS_subjects / (n - 1) if n > 1 else 0
        MS_error = SS_error / ((n - 1) * (k - 1)) if (n - 1) * (k - 1) > 0 else 0
        denom = MS_subjects + (k - 1) * MS_error
        if denom > 0:
            icc = round((MS_subjects - MS_error) / denom, 3)

    return {
        "icc": icc,
        "mean_cv_pct": round(mean_cv, 1) if mean_cv is not None else None,
        "repeated_groups": len(repeated),
        "per_group": per_group,
    }


# ── Metric 4: Human-machine agreement (placeholder) ──


def compute_human_agreement(results: list[dict], human_scores: dict | None = None) -> dict:
    """Compute Spearman ρ between evaluator and human scores."""
    if human_scores is None:
        return {
            "note": "等待人类盲审数据。格式: {trace_id: {overall: N, violations: [...]}}",
            "spearman_rho": None,
            "pairwise_agreement": None,
        }

    try:
        from scipy.stats import spearmanr
    except ImportError:
        return {"error": "需要 scipy: pip install scipy"}

    evaluator_scores = []
    human_score_list = []

    for r in results:
        if "error" in r:
            continue
        tid = r.get("trace_id")
        if tid in human_scores:
            hs = human_scores[tid]
            es = r.get("overall_score")
            if es is not None and hs.get("overall") is not None:
                evaluator_scores.append(es)
                human_score_list.append(hs["overall"] / 100.0)

    if len(evaluator_scores) < 3:
        return {"note": "样本不足（< 3）", "spearman_rho": None}

    rho, p_value = spearmanr(evaluator_scores, human_score_list)
    return {
        "sample_size": len(evaluator_scores),
        "spearman_rho": round(rho, 4),
        "p_value": round(p_value, 4),
    }


# ── Metric 5: Anti-cheat ──


def compute_anti_cheat(results: list[dict]) -> dict:
    """Flawed agent must never outscore medium agent on any scenario."""
    by_scenario = _group_by_scenario(results)
    violations = []

    for scenario, conds in by_scenario.items():
        f_score = conds.get("flawed", {}).get("overall_score")
        m_score = conds.get("medium", {}).get("overall_score")
        if f_score is not None and m_score is not None:
            if f_score > m_score:
                violations.append(
                    {
                        "scenario": scenario,
                        "flawed_score": round(f_score, 3),
                        "medium_score": round(m_score, 3),
                    }
                )

    flawed_runs = [r for r in results if r.get("condition") == "flawed" and "error" not in r]
    scored_flawed = [r for r in flawed_runs if r.get("overall_score") is not None]
    flawed_mean = (
        sum(r["overall_score"] for r in scored_flawed) / len(scored_flawed)
        if scored_flawed
        else None
    )

    return {
        "violations": violations,
        "violation_count": len(violations),
        "passed": len(violations) == 0,
        "flawed_mean_score": round(flawed_mean, 3) if flawed_mean is not None else None,
    }


# ── Metric 6: Anti-bias (placeholder) ──


def compute_anti_bias(results: list[dict]) -> dict:
    """Placeholder: requires perturbation runs (name swap, verbosity injection)."""
    return {
        "note": "需要扰动实验数据（改名字/加废话/换顺序）。对抗测试套件已有 61 项通过。",
        "position_flip_rate": None,
        "verbosity_delta": None,
        "identity_delta": None,
    }


# ── Metric 7: Weight transparency ──


def compute_weight_transparency(results: list[dict]) -> dict:
    """Check that score breakdown is available and hard score + soft score contribute to overall."""
    runs_with_breakdown = 0
    runs_checked = 0
    consistency_ok = 0

    for r in results:
        if "error" in r or r.get("overall_score") is None:
            continue
        runs_checked += 1
        has_hard = r.get("hard_score") is not None
        has_soft = r.get("soft_score") is not None
        if has_hard and has_soft:
            runs_with_breakdown += 1

        if has_hard and has_soft and r.get("overall_score") is not None:
            breakdown = r.get("score_breakdown", {})
            if "hard_score" in breakdown and "objective_score" in breakdown:
                consistency_ok += 1

    return {
        "runs_checked": runs_checked,
        "runs_with_breakdown": runs_with_breakdown,
        "breakdown_rate": round(runs_with_breakdown / runs_checked, 3) if runs_checked else None,
        "weight_consistency_rate": round(consistency_ok / runs_checked, 3)
        if runs_checked
        else None,
    }


# ── Dashboard ──


def compute_all(results: list[dict], human_scores: dict | None = None) -> dict:
    return {
        "discrimination": compute_discrimination(results),
        "violation_recall": compute_violation_recall(results),
        "stability": compute_stability(results),
        "human_agreement": compute_human_agreement(results, human_scores),
        "anti_cheat": compute_anti_cheat(results),
        "anti_bias": compute_anti_bias(results),
        "weight_transparency": compute_weight_transparency(results),
    }


def print_dashboard(metrics: dict):
    print(f"\n{'=' * 65}")
    print("元评测仪表盘")
    print(f"{'=' * 65}")

    d = metrics["discrimination"]
    print("\n1. 区分力")
    print(
        f"   完整排序 (good≥medium≥flawed): {d['full_order_correct']}/{d['total_scenarios_full']}"
        if d["total_scenarios_full"]
        else "   完整排序: 待 good 条件数据"
    )
    print(
        f"   good≥medium: {d.get('good_ge_medium', '待测')}  |  medium≥flawed: {d.get('medium_ge_flawed', '待测')}"
    )
    if d.get("medium_ge_flawed_rate") is not None:
        print(f"   medium≥flawed 通过率: {d['medium_ge_flawed_rate']}")

    v = metrics["violation_recall"]
    print("\n2. 违规召回")
    print(f"   召回率: {v['recall_rate']}  ({v['total_caught']}/{v['total_possible']})")

    s = metrics["stability"]
    print("\n3. 稳定性")
    icc_str = f"{s['icc']}" if s.get("icc") is not None else "待测"
    cv_str = f"{s['mean_cv_pct']}%" if s.get("mean_cv_pct") is not None else "待测"
    print(
        f"   ICC(3,1): {icc_str}  |  平均 CV: {cv_str}  |  重复组数: {s.get('repeated_groups', 0)}"
    )

    h = metrics["human_agreement"]
    print("\n4. 人机一致性")
    rho = h.get("spearman_rho")
    print(f"   Spearman ρ: {rho if rho is not None else '待填入'}")

    c = metrics["anti_cheat"]
    print("\n5. 反作弊")
    status = "✓ 通过" if c["passed"] else f"✗ {c['violation_count']} 个场景违规"
    print(f"   {status}  |  flawed 均分: {c['flawed_mean_score']}")

    b = metrics["anti_bias"]
    print("\n6. 反偏见")
    print(f"   位置翻转率: {b['position_flip_rate'] or '待测'}")

    w = metrics["weight_transparency"]
    print("\n7. 权重透明")
    print(f"   分项可用率: {w['breakdown_rate']}  |  权重一致率: {w['weight_consistency_rate']}")

    print(f"\n{'=' * 65}")


def main():
    parser = argparse.ArgumentParser(description="元评测指标计算")
    parser.add_argument("--summary", default=str(SUMMARY_PATH))
    parser.add_argument("--human", default=None, help="人类盲审数据 JSON")
    args = parser.parse_args()

    results = load_summary(Path(args.summary))
    human_scores = None
    if args.human:
        with open(args.human, encoding="utf-8") as f:
            human_scores = json.load(f)

    metrics = compute_all(results, human_scores)
    print_dashboard(metrics)

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n报告已保存: {REPORT_PATH}")


if __name__ == "__main__":
    main()
