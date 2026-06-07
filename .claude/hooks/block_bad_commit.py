#!/usr/bin/env python3
"""Claude Code PreToolUse hook: enforce full development SOP before commit.

Gates (all must pass for critical-path commits):
 1. quality_gate.sh (ruff lint + format + pytest)
 2. CONTRACTS.md read (marker: .pipeline/contracts_read)
 3. Tests written/updated (test files staged when critical files staged)
 4. Subagent review run (.pipeline/review_report.json — must exist, no CRITICAL)
 5. Subagent adversarial review run (.pipeline/adversarial_report.json — must exist, no CRITICAL, 5 questions answered)
 6. Real e2e eval run (marker: .pipeline/e2e_tested)
 7. BUGS.md updated (staged when bug-fix .py files staged)
 8. CHANGELOG.md updated (staged when ≥3 code files staged)

Review reports are JSON files written by subagent reviews, not touch markers.
Non-critical commits (no scorer/harness/orchestrator) only need gate 1.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

COMMIT_PATTERN = re.compile(r"(^|[;&|])\s*git\s+(commit|push)\b")

CRITICAL_PATTERNS = ["scorer", "harness", "orchestrator", "user_sim", "diagnosis", "validator"]

PIPELINE_DIR = Path(".pipeline")

# Old-style touch markers (gates that don't need subagent reports)
MARKERS = {
    "contracts_read": PIPELINE_DIR / "contracts_read",
    "e2e_tested": PIPELINE_DIR / "e2e_tested",
}

# Subagent review report files (gates that require structured JSON reports)
REVIEW_REPORTS = {
    "review": PIPELINE_DIR / "review_report.json",
    "adversarial": PIPELINE_DIR / "adversarial_report.json",
}

# Max age for review reports (seconds) — stale reports don't count
REPORT_MAX_AGE_SECONDS = 3600  # 1 hour

GIT_BASH = Path(r"C:\Program Files\Git\bin\bash.exe")


def _staged_files() -> list[str]:
    try:
        r = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True,
            check=False,
        )
        return [
            f.strip() for f in r.stdout.decode("utf-8", errors="replace").split("\n") if f.strip()
        ]
    except Exception:
        return []


def _is_critical(staged: list[str]) -> bool:
    for f in staged:
        name = Path(f).name.lower()
        if name.endswith(".py") and any(p in name for p in CRITICAL_PATTERNS):
            return True
    return False


def _critical_files(staged: list[str]) -> list[str]:
    return [
        f
        for f in staged
        if Path(f).name.lower().endswith(".py")
        and any(p in Path(f).name.lower() for p in CRITICAL_PATTERNS)
    ]


def _code_py_files(staged: list[str]) -> list[str]:
    return [f for f in staged if f.endswith(".py") and "test" not in f.lower()]


def _test_files(staged: list[str]) -> list[str]:
    return [f for f in staged if f.endswith(".py") and "test" in f.lower()]


def _run_quality_gate() -> tuple[bool, str]:
    gate_sh = Path("scripts/quality_gate.sh")
    gate_ps = Path("scripts/quality_gate.ps1")
    bash = str(GIT_BASH) if GIT_BASH.exists() else "bash"
    if gate_sh.exists():
        cmd = [bash, str(gate_sh)]
    elif gate_ps.exists():
        cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(gate_ps)]
    else:
        return False, "scripts/quality_gate.{sh,ps1} not found"

    r = subprocess.run(cmd, capture_output=True, check=False)
    if r.returncode != 0:
        out = r.stdout.decode("utf-8", errors="replace") if r.stdout else ""
        err = r.stderr.decode("utf-8", errors="replace") if r.stderr else ""
        return False, (out + err)[-3000:]
    return True, ""


def _check_review_report(
    report_path: Path, require_adversarial_questions: bool = False
) -> tuple[bool, str]:
    """Validate a subagent review report JSON file.

    Checks:
    1. File exists
    2. File is not stale (< REPORT_MAX_AGE_SECONDS old)
    3. Valid JSON with required fields
    4. No CRITICAL findings
    5. (adversarial only) 5 adversarial questions answered
    """
    if not report_path.exists():
        return False, f"报告文件不存在: {report_path}"

    age = time.time() - report_path.stat().st_mtime
    if age > REPORT_MAX_AGE_SECONDS:
        mins = int(age / 60)
        return False, f"报告已过期（{mins}分钟前生成，上限{REPORT_MAX_AGE_SECONDS // 60}分钟）"

    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return False, f"报告 JSON 解析失败: {e}"

    if not isinstance(data, dict):
        return False, "报告格式错误：顶层必须是 dict"

    if "findings" not in data:
        return False, "报告缺少 findings 字段"

    findings = data["findings"]
    if not isinstance(findings, list):
        return False, "findings 必须是数组"

    critical_count = sum(
        1 for f in findings if isinstance(f, dict) and f.get("severity") == "CRITICAL"
    )
    if critical_count > 0:
        critical_items = [
            f.get("description", "?")
            for f in findings
            if isinstance(f, dict) and f.get("severity") == "CRITICAL"
        ]
        return False, f"有 {critical_count} 个 CRITICAL 问题未修复:\n" + "\n".join(
            f"  - {c}" for c in critical_items[:5]
        )

    if require_adversarial_questions:
        questions = data.get("adversarial_questions", {})
        required = {"cheat", "order", "trust", "determinism", "defaults"}
        answered = set(questions.keys()) if isinstance(questions, dict) else set()
        missing = required - answered
        if missing:
            return False, f"对抗审查 5 问未全部回答，缺: {', '.join(missing)}"

    return True, ""


def main() -> int:
    payload = json.load(sys.stdin)
    tool_input = payload.get("tool_input") or {}
    command = tool_input.get("command") or ""

    if not COMMIT_PATTERN.search(command):
        return 0

    staged = _staged_files()
    critical = _is_critical(staged)
    failures: list[str] = []

    # ── Gate 1: quality gate (always) ──
    ok, detail = _run_quality_gate()
    if not ok:
        print(f"❌ Gate 1/8 FAILED: 质量门禁\n{detail}", file=sys.stderr)
        return 2

    if not critical:
        code_files = _code_py_files(staged)
        if len(code_files) >= 3 and not any(f.endswith("CHANGELOG.md") for f in staged):
            print(
                f"❌ CHANGELOG.md 未更新（{len(code_files)} 个代码文件改动，≥3 时必须更新）",
                file=sys.stderr,
            )
            return 2
        return 0

    # ── Critical path: full SOP ──
    crit_files = _critical_files(staged)
    print(f"🔒 关键文件变更检测到: {', '.join(Path(f).name for f in crit_files)}", file=sys.stderr)
    print("   启动完整 SOP 检查（8 项门禁）...\n", file=sys.stderr)

    # ── Gate 2: CONTRACTS.md read ──
    if not MARKERS["contracts_read"].exists():
        failures.append(
            "Gate 2: 未读 CONTRACTS.md\n"
            "   → 改 scorer/harness/orchestrator 前必须先读 CONTRACTS.md\n"
            "   → 读完后执行: mkdir -p .pipeline && touch .pipeline/contracts_read"
        )

    # ── Gate 3: test files staged ──
    tests = _test_files(staged)
    if not tests:
        failures.append(
            "Gate 3: 未提交测试文件\n"
            "   → 关键改动必须先写测试（先写失败测试再实现）\n"
            "   → 确保 tests/ 目录有新增或修改的测试文件被 staged"
        )

    # ── Gate 4: subagent review report (replaces old /simplify + /review markers) ──
    ok, detail = _check_review_report(REVIEW_REPORTS["review"])
    if not ok:
        failures.append(
            f"Gate 4: Subagent 代码审查未通过\n"
            f"   → {detail}\n"
            "   → 必须用 Agent(subagent_type='feature-dev:code-reviewer', model='opus') 执行审查\n"
            "   → 审查结果写入 .pipeline/review_report.json（格式见下）"
        )

    # ── Gate 5: subagent adversarial review report ──
    ok, detail = _check_review_report(
        REVIEW_REPORTS["adversarial"], require_adversarial_questions=True
    )
    if not ok:
        failures.append(
            f"Gate 5: Subagent 对抗审查未通过\n"
            f"   → {detail}\n"
            "   → 必须用 Agent(subagent_type='feature-dev:code-reviewer', model='opus') 执行对抗审查\n"
            "   → 审查结果写入 .pipeline/adversarial_report.json（需包含 adversarial_questions）"
        )

    # ── Gate 6: e2e eval ──
    if not MARKERS["e2e_tested"].exists():
        failures.append(
            "Gate 6: 未执行真实端到端评测\n"
            "   → 不能只靠 pytest，必须跑一次真实评测验证输出\n"
            "   → 完成后执行: mkdir -p .pipeline && touch .pipeline/e2e_tested"
        )

    # ── Gate 7: BUGS.md updated ──
    bugs_staged = any("BUGS.md" in f for f in staged)
    if not bugs_staged:
        failures.append(
            "Gate 7: BUGS.md 未更新\n"
            "   → 关键文件改动（尤其是 bug 修复）必须同步更新 BUGS.md\n"
            "   → git add BUGS.md"
        )

    # ── Gate 8: CHANGELOG.md updated ──
    code_files = _code_py_files(staged)
    if len(code_files) >= 3 and not any(f.endswith("CHANGELOG.md") for f in staged):
        failures.append(
            f"Gate 8: CHANGELOG.md 未更新（{len(code_files)} 个代码文件改动）\n"
            "   → ≥3 个代码文件改动时必须更新 CHANGELOG.md\n"
            "   → git add CHANGELOG.md"
        )

    if failures:
        print(f"❌ 关键路径 SOP 检查未通过（{len(failures)}/{8} 项失败）:\n", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}\n", file=sys.stderr)

        # Print report format hint
        print(
            "\n📋 审查报告 JSON 格式（写入 .pipeline/xxx_report.json）:\n"
            '  {"timestamp": "...", "agent_model": "opus", '
            '"files_reviewed": [...], "findings": '
            '[{"severity": "HIGH", "file": "...", "line": 0, "description": "..."}], '
            '"adversarial_questions": {"cheat": "...", "order": "...", '
            '"trust": "...", "determinism": "...", "defaults": "..."}}\n',
            file=sys.stderr,
        )

        print(
            "完整 SOP: 读契约 → 写测试 → 实现 → lint+test → subagent 审查 → subagent 对抗审查 → 真实评测 → 更新文档",
            file=sys.stderr,
        )
        return 2

    # All gates passed — clean markers and reports
    for marker in MARKERS.values():
        marker.unlink(missing_ok=True)
    for report in REVIEW_REPORTS.values():
        report.unlink(missing_ok=True)

    print("✅ 完整 SOP 8 项门禁全部通过（含 subagent 独立审查验证）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
