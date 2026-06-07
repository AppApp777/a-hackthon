"""Anti-cheating self-inspection: scan evaluator source for hardcoded business terms.

Detects hardcoded product names, customer names, amounts, undocumented thresholds,
security risks (eval/exec), non-determinism (unseeded random, time-based scoring),
and leftover TODO/FIXME/HACK markers in evaluation-critical source files.

Usage:
    python self_check.py
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ── Files to scan ──────────────────────────────────────────────────────────────

TARGET_FILES = [
    "scorer_outbound.py",
    "scorer.py",
    "harness.py",
    "orchestrator_outbound.py",
    "user_sim_outbound.py",
    "diagnosis.py",
    "validator.py",
]

# ── Detection patterns ─────────────────────────────────────────────────────────

# Chinese business terms that should live in scenario JSON, not evaluator code.
# Presence in source suggests hardcoded bias toward specific scenarios.
BUSINESS_TERM_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("product_name", re.compile(r"(麻辣香锅|米饭|可乐|香锅|奶茶|咖啡|汉堡|披萨|寿司|炸鸡|烧烤)")),
    ("merchant_name", re.compile(r"(川味坊|肯德基|麦当劳|星巴克|瑞幸|海底捞|美团买菜)")),
    ("customer_name", re.compile(r"(张先生|李先生|王女士|赵先生|刘女士|张三|李四|王五)")),
    ("rider_name", re.compile(r"(李师傅|王师傅|张师傅|赵师傅)")),
    ("address", re.compile(r"(朝阳区|建国路|金地中心|海淀区|望京|国贸|三里屯)")),
    ("order_id", re.compile(r"MT20\d{8,}")),
    ("phone_number", re.compile(r"1[3-9]\d{9}")),
    ("fixed_amount_cny", re.compile(r"(?<!\d)(补偿|赔偿|退款|预算).{0,4}?\d+(?:元|块)")),
]

# Hardcoded numeric thresholds without doc comments — potential scoring bias.
# Matches bare float assignments like `threshold = 0.7` without a comment.
UNDOCUMENTED_THRESHOLD_RE = re.compile(
    r"^\s*(threshold|cutoff|weight|penalty|bonus|min_score|max_score|pass_score)"
    r"\s*[:=]\s*[\d.]+\s*$"
)

# TODO / FIXME / HACK / XXX markers — shortcuts that may hide scoring bugs.
SHORTCUT_MARKER_RE = re.compile(r"#\s*(TODO|FIXME|HACK|XXX)\b", re.IGNORECASE)

# Security: eval() / exec() calls.
EVAL_EXEC_RE = re.compile(r"\beval\s*\(|\bexec\s*\(")

# Non-determinism: `random.` without a preceding `seed` in the same scope,
# and `time.time()` / `datetime.now()` used near scoring logic.
RANDOM_CALL_RE = re.compile(r"\brandom\.(random|choice|randint|uniform|sample|shuffle)\b")
TIME_SCORING_RE = re.compile(r"\b(time\.time|datetime\.now)\s*\(")

# ── Data structures ────────────────────────────────────────────────────────────


@dataclass
class Finding:
    level: str  # ALERT | WARNING
    category: str
    line: int
    snippet: str


@dataclass
class FileReport:
    path: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def status(self) -> str:
        if any(f.level == "ALERT" for f in self.findings):
            return "ALERT"
        if any(f.level == "WARNING" for f in self.findings):
            return "WARNING"
        return "CLEAN"


# ── Scanning logic ─────────────────────────────────────────────────────────────


def _is_in_comment_or_docstring(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''")


def _check_random_has_seed(source: str) -> bool:
    """Return True if source imports random AND calls seed() somewhere."""
    return "random.seed" in source or "random.seed" in source.replace(" ", "")


def scan_file(filepath: Path) -> FileReport:
    report = FileReport(path=str(filepath))

    if not filepath.exists():
        report.findings.append(Finding("WARNING", "missing_file", 0, f"{filepath.name} not found"))
        return report

    source = filepath.read_text(encoding="utf-8", errors="replace")
    lines = source.splitlines()
    has_random_seed = _check_random_has_seed(source)

    for i, line in enumerate(lines, start=1):
        # Skip pure comment lines for business-term checks (doc comments are ok)
        is_comment = _is_in_comment_or_docstring(line)

        # 1. Business terms (only in non-comment code)
        if not is_comment:
            for tag, pattern in BUSINESS_TERM_PATTERNS:
                m = pattern.search(line)
                if m:
                    report.findings.append(
                        Finding(
                            "ALERT",
                            f"hardcoded_{tag}",
                            i,
                            f"'{m.group()}' in: {line.strip()[:100]}",
                        )
                    )

        # 2. Undocumented thresholds
        if UNDOCUMENTED_THRESHOLD_RE.match(line):
            report.findings.append(
                Finding("WARNING", "undocumented_threshold", i, line.strip()[:100])
            )

        # 3. Shortcut markers
        m = SHORTCUT_MARKER_RE.search(line)
        if m:
            report.findings.append(
                Finding("WARNING", "shortcut_marker", i, f"{m.group(1)}: {line.strip()[:100]}")
            )

        # 4. eval / exec
        if EVAL_EXEC_RE.search(line) and not is_comment:
            report.findings.append(Finding("ALERT", "eval_exec", i, line.strip()[:100]))

        # 5. Non-determinism
        if RANDOM_CALL_RE.search(line) and not has_random_seed and not is_comment:
            report.findings.append(Finding("WARNING", "unseeded_random", i, line.strip()[:100]))

        if TIME_SCORING_RE.search(line) and not is_comment:
            # Only flag if near scoring-related context
            context_window = "\n".join(lines[max(0, i - 5) : min(len(lines), i + 5)])
            if re.search(r"(score|grade|point|判|分|评)", context_window):
                report.findings.append(Finding("WARNING", "time_in_scoring", i, line.strip()[:100]))

    return report


# ── Output formatting ──────────────────────────────────────────────────────────

LEVEL_ICONS = {"ALERT": "!!", "WARNING": " ?", "CLEAN": "OK"}


def print_report(reports: list[FileReport]) -> int:
    """Print structured report. Returns count of ALERT-level findings."""
    alert_count = 0
    divider = "-" * 72

    print("\n" + "=" * 72)
    print("  SELF-CHECK: Anti-Cheating Source Inspection")
    print("=" * 72)

    for r in reports:
        icon = LEVEL_ICONS.get(r.status, "  ")
        print(f"\n[{icon}] {r.path}  — {r.status}")
        print(divider)

        if r.status == "CLEAN":
            print("    No issues found.")
            continue

        for f in r.findings:
            prefix = "ALERT" if f.level == "ALERT" else "WARN "
            loc = f"L{f.line}" if f.line > 0 else "---"
            print(f"    [{prefix}] {loc:>5}  [{f.category}]  {f.snippet}")
            if f.level == "ALERT":
                alert_count += 1

    # Summary
    total_findings = sum(len(r.findings) for r in reports)
    alert_files = [r.path for r in reports if r.status == "ALERT"]
    warn_files = [r.path for r in reports if r.status == "WARNING"]
    clean_files = [r.path for r in reports if r.status == "CLEAN"]

    print(f"\n{'=' * 72}")
    print(f"  SUMMARY: {len(reports)} files scanned, {total_findings} findings")
    print(f"    CLEAN  : {len(clean_files)}")
    print(f"    WARNING: {len(warn_files)}")
    print(f"    ALERT  : {len(alert_files)}")

    if alert_count:
        print(f"\n  ** {alert_count} ALERT(s): hardcoded business terms or security risks.")
        print("     These MUST be resolved before submission.")

    print("=" * 72 + "\n")
    return alert_count


# ── Main ───────────────────────────────────────────────────────────────────────


def main() -> int:
    base = Path(__file__).parent
    reports = [scan_file(base / f) for f in TARGET_FILES]
    alert_count = print_report(reports)
    return 1 if alert_count > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
