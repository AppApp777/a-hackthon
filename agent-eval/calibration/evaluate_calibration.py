"""Evaluate calibration: compare human labels against system scores.

Usage:
    python calibration/evaluate_calibration.py [--input calibration/gold_items.jsonl]

Expects gold_items.jsonl with human_label filled in:
  - For dimension items: human_label = 0-5 integer score
  - For binary items: human_label = true/false

Reports:
  - Cohen's kappa (weighted for dimensions, unweighted for binary)
  - Exact match rate
  - MAE (mean absolute error) for dimensions
  - Per-dimension and per-binary breakdown
  - Overall agreement summary
"""

import argparse
import json
from collections import defaultdict


def cohens_kappa_binary(labels_a: list, labels_b: list) -> float:
    """Unweighted Cohen's kappa for binary labels."""
    n = len(labels_a)
    if n == 0:
        return 0.0
    agree = sum(1 for a, b in zip(labels_a, labels_b, strict=False) if a == b)
    p_o = agree / n

    pos_a = sum(1 for x in labels_a if x)
    pos_b = sum(1 for x in labels_b if x)
    p_yes = (pos_a / n) * (pos_b / n)
    p_no = ((n - pos_a) / n) * ((n - pos_b) / n)
    p_e = p_yes + p_no

    if p_e == 1.0:
        return 1.0
    return (p_o - p_e) / (1 - p_e)


def weighted_kappa(scores_a: list[int], scores_b: list[int], max_val: int = 5) -> float:
    """Linear-weighted Cohen's kappa for ordinal scores."""
    n = len(scores_a)
    if n == 0:
        return 0.0

    matrix = [[0] * (max_val + 1) for _ in range(max_val + 1)]
    for a, b in zip(scores_a, scores_b, strict=False):
        matrix[a][b] += 1

    w = [[abs(i - j) / max_val for j in range(max_val + 1)] for i in range(max_val + 1)]

    row_sums = [sum(matrix[i]) for i in range(max_val + 1)]
    col_sums = [sum(matrix[i][j] for i in range(max_val + 1)) for j in range(max_val + 1)]

    p_o = sum(w[i][j] * matrix[i][j] for i in range(max_val + 1) for j in range(max_val + 1)) / n
    p_e = sum(
        w[i][j] * row_sums[i] * col_sums[j] for i in range(max_val + 1) for j in range(max_val + 1)
    ) / (n * n)

    if p_e == 1.0:
        return 1.0
    return 1 - (p_o / p_e)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="calibration/gold_items.jsonl")
    args = parser.parse_args()

    items = []
    with open(args.input, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))

    labeled = [i for i in items if i.get("human_label") is not None]
    unlabeled = len(items) - len(labeled)

    if not labeled:
        print("没有标注数据。请先在 gold_items.jsonl 中填写 human_label 字段。")
        print(f"共 {len(items)} 项待标注。")
        return

    print("=== 校准评估报告 ===")
    print(f"总项数: {len(items)}  已标注: {len(labeled)}  未标注: {unlabeled}")
    print()

    dims = [i for i in labeled if i["item_type"] == "dimension"]
    bins = [i for i in labeled if i["item_type"] == "binary"]

    if dims:
        sys_scores = [int(i["system_score"]) for i in dims]
        hum_scores = [int(i["human_label"]) for i in dims]

        exact = sum(1 for a, b in zip(sys_scores, hum_scores, strict=False) if a == b)
        within1 = sum(1 for a, b in zip(sys_scores, hum_scores, strict=False) if abs(a - b) <= 1)
        mae = sum(abs(a - b) for a, b in zip(sys_scores, hum_scores, strict=False)) / len(dims)
        kappa = weighted_kappa(sys_scores, hum_scores)

        print(f"--- 维度评分 ({len(dims)} 项) ---")
        print(f"加权 Cohen's κ:  {kappa:.3f}")
        print(f"完全一致率:      {exact}/{len(dims)} ({100 * exact / len(dims):.1f}%)")
        print(f"±1 一致率:       {within1}/{len(dims)} ({100 * within1 / len(dims):.1f}%)")
        print(f"MAE:             {mae:.2f}")

        if len(sys_scores) >= 3:
            mean_s = sum(sys_scores) / len(sys_scores)
            mean_h = sum(hum_scores) / len(hum_scores)
            cov = sum(
                (s - mean_s) * (h - mean_h) for s, h in zip(sys_scores, hum_scores, strict=False)
            )
            var_s = sum((s - mean_s) ** 2 for s in sys_scores)
            var_h = sum((h - mean_h) ** 2 for h in hum_scores)
            denom = (var_s * var_h) ** 0.5
            pearson_r = cov / denom if denom > 0 else 0.0
            print(f"Pearson r:       {pearson_r:.3f}")

        print()

        by_dim = defaultdict(list)
        for i in dims:
            by_dim[i["item_id"]].append(i)
        for dim_id in sorted(by_dim):
            group = by_dim[dim_id]
            s = [int(i["system_score"]) for i in group]
            h = [int(i["human_label"]) for i in group]
            ex = sum(1 for a, b in zip(s, h, strict=False) if a == b)
            m = sum(abs(a - b) for a, b in zip(s, h, strict=False)) / len(group)
            name = group[0].get("item_name", dim_id)
            print(f"  {dim_id} ({name}): 一致 {ex}/{len(group)}, MAE={m:.2f}")
        print()

    if bins:
        sys_labels = [bool(i["system_score"]) for i in bins]
        hum_labels = [bool(i["human_label"]) for i in bins]

        exact = sum(1 for a, b in zip(sys_labels, hum_labels, strict=False) if a == b)
        kappa = cohens_kappa_binary(sys_labels, hum_labels)

        tp = sum(1 for a, b in zip(sys_labels, hum_labels, strict=False) if a and b)
        fp = sum(1 for a, b in zip(sys_labels, hum_labels, strict=False) if a and not b)
        fn = sum(1 for a, b in zip(sys_labels, hum_labels, strict=False) if not a and b)

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

        print(f"--- 二元安全项 ({len(bins)} 项) ---")
        print(f"Cohen's κ:       {kappa:.3f}")
        print(f"一致率:          {exact}/{len(bins)} ({100 * exact / len(bins):.1f}%)")
        print(f"Precision:       {precision:.3f}")
        print(f"Recall:          {recall:.3f}")
        print(f"F1:              {f1:.3f}")
        print()

        by_item = defaultdict(list)
        for i in bins:
            by_item[i["item_id"]].append(i)
        for item_id in sorted(by_item):
            group = by_item[item_id]
            s = [bool(i["system_score"]) for i in group]
            h = [bool(i["human_label"]) for i in group]
            ex = sum(1 for a, b in zip(s, h, strict=False) if a == b)
            name = group[0].get("item_name", item_id)
            print(f"  {item_id} ({name}): 一致 {ex}/{len(group)}")

    print()
    print("=== 评级参考 ===")
    print("κ > 0.80: 近乎完美一致 | 0.60-0.80: 显著一致 | 0.40-0.60: 中等一致 | < 0.40: 弱一致")


if __name__ == "__main__":
    main()
