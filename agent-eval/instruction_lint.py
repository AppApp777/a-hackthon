"""Instruction Semantic Lint — detect defects in the task instruction ITSELF.

Unlike scenario_linter.py (which checks compiled scenario JSON structure),
this module checks whether the raw Markdown instruction is internally
consistent, feasible, unambiguous, and branch-complete.

Four dimensions:
  - conflict: two constraints contradict each other
  - infeasible: a constraint is impossible to satisfy
  - ambiguous: a statement can be interpreted multiple ways
  - missing_branch: a conditional has unhandled cases

Each finding carries a source_quote verified against the original text.
The compliance score is computed deterministically (not by LLM).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal

from llm import chat_text


@dataclass
class InstructionDefect:
    dimension: Literal["conflict", "infeasible", "ambiguous", "missing_branch"]
    severity: Literal["critical", "major", "minor"]
    description: str
    quote_a: str = ""
    quote_b: str = ""
    suggestion: str = ""
    verified: bool = False


_SEVERITY_WEIGHT = {"critical": 1.0, "major": 0.6, "minor": 0.3}
_DIMENSION_FACTOR = {
    "conflict": 1.0,
    "infeasible": 1.0,
    "ambiguous": 0.6,
    "missing_branch": 0.6,
}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text.lower())


def _is_substring_fuzzy(quote: str, source: str, threshold: float = 0.82) -> bool:
    """Check if quote approximately appears in source text."""
    if not quote or not source:
        return False
    nq = _normalize(quote)
    ns = _normalize(source)
    if not nq:
        return False
    if nq in ns:
        return True
    win = len(nq)
    if win > len(ns):
        return False
    best = 0.0
    for i in range(len(ns) - win + 1):
        window = ns[i : i + win]
        matches = sum(a == b for a, b in zip(nq, window, strict=False))
        ratio = matches / win
        if ratio > best:
            best = ratio
    return best >= threshold


def _verify_quotes(defect: InstructionDefect, raw: str) -> InstructionDefect:
    """Verify source quotes against original instruction. Drop unverifiable."""
    a_ok = _is_substring_fuzzy(defect.quote_a, raw) if defect.quote_a else True
    b_ok = _is_substring_fuzzy(defect.quote_b, raw) if defect.quote_b else True
    defect.verified = a_ok and b_ok
    if not a_ok:
        defect.quote_a = ""
    if not b_ok:
        defect.quote_b = ""
    return defect


def compute_compliance_score(defects: list[InstructionDefect]) -> float:
    """Deterministic compliance score: start at 100, deduct per defect."""
    score = 100.0
    for d in defects:
        if not d.verified:
            continue
        penalty = _SEVERITY_WEIGHT[d.severity] * _DIMENSION_FACTOR[d.dimension] * 12
        score -= penalty
    return max(0.0, round(score, 1))


_PROMPT_PASS1 = """你是一个指令质量审核员。给你一份外呼任务指令，请找出其中的**内部矛盾**和**不可行约束**。

## 检测维度

1. **conflict（矛盾）**：指令中两条规则/约束互相矛盾，Agent 无法同时满足两者。
   例："每轮回复不超过30字" vs "必须在开场白中完整介绍所有产品功能"

2. **infeasible（不可行）**：某条约束在实际场景中根本无法做到。
   例："必须在用户说第一句话之前确认身份"（用户还没说话怎么确认）

## 输出格式

严格输出 JSON 数组，每个元素：
```json
{
  "dimension": "conflict" 或 "infeasible",
  "severity": "critical" / "major" / "minor",
  "description": "一句话描述问题",
  "quote_a": "指令原文中的相关片段A（必须是原文，不是你的改写）",
  "quote_b": "指令原文中的相关片段B（矛盾的另一方，infeasible 可留空）",
  "suggestion": "修改建议"
}
```

如果没有发现任何问题，输出空数组 `[]`。
宁缺毋滥——不确定的不要报。每条 quote 必须是指令中的原文。

## 指令原文

"""

_PROMPT_PASS2 = """你是一个指令质量审核员。给你一份外呼任务指令，请找出其中的**歧义表述**和**分支缺失**。

## 检测维度

1. **ambiguous（歧义）**：某条指令可以有多种理解，不同 Agent 会做出不同行为。
   例："适当安抚用户"——什么算"适当"？没有标准。

2. **missing_branch（分支缺失）**：条件判断没有覆盖所有可能情况。
   例："若用户同意→步骤3，若用户拒绝→步骤5"——用户说"我考虑一下"怎么办？

## 输出格式

严格输出 JSON 数组，每个元素：
```json
{
  "dimension": "ambiguous" 或 "missing_branch",
  "severity": "critical" / "major" / "minor",
  "description": "一句话描述问题",
  "quote_a": "指令原文中的相关片段（必须是原文）",
  "quote_b": "",
  "suggestion": "修改建议"
}
```

如果没有发现任何问题，输出空数组 `[]`。
宁缺毋滥——不确定的不要报。

## 指令原文

"""


def _parse_llm_response(text: str) -> list[dict]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return []


def _deduplicate(defects: list[InstructionDefect]) -> list[InstructionDefect]:
    seen: set[str] = set()
    result: list[InstructionDefect] = []
    for d in defects:
        key = f"{d.dimension}:{(d.quote_a or '')[:40]}"
        if key in seen:
            continue
        seen.add(key)
        result.append(d)
    return result


def lint_instruction(
    raw_instruction: str,
    model: str = "deepseek-chat",
) -> tuple[list[InstructionDefect], float]:
    """Run semantic lint on a raw Markdown instruction.

    Returns (defects, compliance_score).
    Uses 2 LLM calls: Pass 1 (conflict+infeasible), Pass 2 (ambiguous+missing_branch).
    """
    all_defects: list[InstructionDefect] = []

    for prompt_template in [_PROMPT_PASS1, _PROMPT_PASS2]:
        prompt = prompt_template + raw_instruction
        try:
            response = chat_text(
                prompt=prompt,
                model=model,
                temperature=0.0,
            )
        except Exception:
            continue

        raw_items = _parse_llm_response(response)
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            dim = item.get("dimension", "")
            if dim not in ("conflict", "infeasible", "ambiguous", "missing_branch"):
                continue
            sev = item.get("severity", "major")
            if sev not in ("critical", "major", "minor"):
                sev = "major"

            defect = InstructionDefect(
                dimension=dim,
                severity=sev,
                description=item.get("description", ""),
                quote_a=item.get("quote_a", ""),
                quote_b=item.get("quote_b", ""),
                suggestion=item.get("suggestion", ""),
            )
            defect = _verify_quotes(defect, raw_instruction)
            if defect.verified:
                all_defects.append(defect)

    all_defects = _deduplicate(all_defects)
    score = compute_compliance_score(all_defects)
    return all_defects, score


def format_lint_report(defects: list[InstructionDefect], score: float) -> str:
    """Format lint results as a human-readable report."""
    lines = ["# 指令语义校验报告", "", f"可遵循度评分: {score}/100", ""]

    if not defects:
        lines.append("未发现语义缺陷。")
        return "\n".join(lines)

    by_dim: dict[str, list[InstructionDefect]] = {}
    for d in defects:
        by_dim.setdefault(d.dimension, []).append(d)

    dim_labels = {
        "conflict": "矛盾",
        "infeasible": "不可行",
        "ambiguous": "歧义",
        "missing_branch": "分支缺失",
    }
    for dim, label in dim_labels.items():
        items = by_dim.get(dim, [])
        if not items:
            continue
        lines.append(f"## {label}（{len(items)} 条）")
        for i, d in enumerate(items, 1):
            lines.append("")
            lines.append(f"### {i}. [{d.severity.upper()}] {d.description}")
            if d.quote_a:
                lines.append(f'- 原文: "{d.quote_a}"')
            if d.quote_b:
                lines.append(f'- 矛盾方: "{d.quote_b}"')
            if d.suggestion:
                lines.append(f"- 建议: {d.suggestion}")
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python instruction_lint.py <instruction.md>")
        sys.exit(1)

    with open(sys.argv[1], encoding="utf-8") as f:
        raw = f.read()

    defects, score = lint_instruction(raw)
    print(format_lint_report(defects, score))
