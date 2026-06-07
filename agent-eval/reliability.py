"""pass^k reliability metric (τ-bench inspired).

pass^k = E[c^k / n^k] where:
  - n = total runs
  - c = number of successful runs (overall_score >= threshold)
  - k = repetition count

For a single scenario, pass^k = (c/n)^k = success_rate^k.
Higher pass^k → more deterministic agent behavior.
"""

from __future__ import annotations


def compute_pass_k(
    scores: list[float],
    threshold: float = 0.60,
    max_k: int | None = None,
) -> dict[int, float]:
    """Compute pass^k for k=1..max_k given a list of overall scores.

    Args:
        scores: list of overall_score values from repeated runs
        threshold: score >= this counts as "pass" (default 0.60)
        max_k: maximum k to compute (default: len(scores))

    Returns:
        dict mapping k -> pass^k value
    """
    n = len(scores)
    if n == 0:
        return {}
    c = sum(1 for s in scores if s >= threshold)
    p = c / n
    if max_k is None:
        max_k = n
    return {k: round(p**k, 4) for k in range(1, max_k + 1)}


def format_pass_k(pass_k: dict[int, float], scores: list[float], threshold: float = 0.60) -> str:
    """Format pass^k results for CLI output."""
    n = len(scores)
    c = sum(1 for s in scores if s >= threshold)
    lines = [
        "可复现性分析 (pass^k, τ-bench 方法)",
        f"  运行次数: {n}  |  通过次数: {c}  |  阈值: {threshold:.0%}",
        f"  分数分布: min={min(scores):.1%}  max={max(scores):.1%}  "
        f"mean={sum(scores) / n:.1%}  std={_std(scores):.1%}",
        "",
    ]
    for k, v in sorted(pass_k.items()):
        bar = "█" * int(v * 20) + "░" * (20 - int(v * 20))
        lines.append(f"  pass^{k}: {v:.1%}  {bar}")
    return "\n".join(lines)


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
    return variance**0.5
