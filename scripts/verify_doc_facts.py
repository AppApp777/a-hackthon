#!/usr/bin/env python3
"""
文档事实校验器 — 检查所有评委文档中的关键数字是否与 DOC_FACTS 一致。

用法：
    cd A-hackthon
    python scripts/verify_doc_facts.py

Opus 4.6 每次重写文档后跑一次，全 PASS 才采纳。
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent

DOCS_TO_CHECK = [
    ROOT / "README.md",
    ROOT / "JUDGE_GUIDE.md",
    ROOT / "CLAIMS.md",
    ROOT / "LIMITATIONS.md",
    ROOT / "agent-eval" / "README.md",
    ROOT / "agent-eval" / "TECH_REPORT.md",
    ROOT / "docs" / "demo" / "index.html",
]

PASS = "✅ PASS"
FAIL = "❌ FAIL"
results = []


def check(name: str, condition: bool, detail: str):
    status = PASS if condition else FAIL
    results.append((name, status, detail))
    print(f"  {status}  {name}: {detail}")


def read_file(p: Path) -> str:
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


def find_wrong_value(content: str, pattern: str, file_name: str) -> str | None:
    """在文件内容中搜索错误模式，返回匹配到的文本或 None。"""
    m = re.search(pattern, content)
    if m:
        return m.group()
    return None


def main():
    print("=" * 60)
    print("  文档事实校验器 — 关键数字一致性检查")
    print("=" * 60)

    docs = {}
    for path in DOCS_TO_CHECK:
        rel = str(path.relative_to(ROOT))
        if path.exists():
            docs[rel] = read_file(path)
        else:
            print(f"  [SKIP] {rel}")

    print(f"\n检查 {len(docs)} 个文档...\n")

    # ━━ 1. 测试数量：不能出现 1082/1163（旧值）━━
    for f, c in docs.items():
        if re.search(r"1082\s*项|1082\s*测试|\*\*1082\*\*|测试.*1082|1082.*test", c):
            check(f"测试数量 @ {f}", False, "包含旧值 1082，应为 1167")
        elif re.search(r"1163\s*项|1163\s*测试|\*\*1163\*\*|测试.*1163|1163.*test", c):
            check(f"测试数量 @ {f}", False, "包含旧值 1163，应为 1167")

    # ━━ 2. 配对实验标准差：不能是 7.1%（旧值），应为 7.4% ━━
    for f, c in docs.items():
        if re.search(
            r"标准差\s*7\.1|7\.1%.*标准差|中位.*7\.1|7\.1.*中位|stdev.*7\.1|7\.1.*stdev",
            c,
            re.IGNORECASE,
        ):
            check(f"配对标准差 @ {f}", False, "包含旧值 7.1%，应为 7.4%")

    # ━━ 3. 跨文档测试数量一致性 ━━
    files_old = [
        f
        for f, c in docs.items()
        if re.search(r"(?:1082|1163)\s*(?:项|测试)|\*\*(?:1082|1163)\*\*", c)
    ]
    if files_old:
        check("测试数量跨文档一致性", False, f"{files_old} 包含旧值，应为 1167")
    else:
        check("测试数量跨文档一致性", True, "无冲突")

    # ━━ 4. 消融数据不能偏 ━━
    for f, c in docs.items():
        # 完整系统均分
        if re.search(r"完整.*37\.[013-9]|37\.[013-9].*完整|full.*37\.[013-9]", c, re.IGNORECASE):
            check(f"消融完整均分 @ {f}", False, "应为 37.2%")
        # LLM 均分
        if re.search(r"LLM.*88\.[0-79]|88\.[0-79].*LLM|独评.*88\.[0-79]", c, re.IGNORECASE):
            check(f"消融LLM均分 @ {f}", False, "应为 88.8%")

    # ━━ 5. 评分权重：确定性 88% / LLM 12% ━━
    for f, c in docs.items():
        # 检查 "LLM" 附近出现错误的权重百分比
        lines = c.split("\n")
        for i, line in enumerate(lines):
            if "LLM" in line and ("评委" in line or "权重" in line or "辅" in line):
                if re.search(r"(?<!\d)(15|20|10)%", line):
                    # 排除明显不是 LLM 权重的行（如"步骤合规 35%"）
                    if "步骤" not in line and "硬指标" not in line and "软指标" not in line:
                        check(
                            f"LLM权重 @ {f}:{i + 1}",
                            False,
                            "LLM 评委权重应为 12%，该行含其他百分比",
                        )

    # ━━ 6. 模型数量：不能写 "7 个模型" ━━
    for f, c in docs.items():
        if re.search(r"7\s*个.*模型|模型.*7\s*个", c):
            check(f"模型数量 @ {f}", False, '不要写 "7 个模型"，应为 3 个')

    # ━━ 7. 场景数量上下文检查 ━━
    for f, c in docs.items():
        # 只在场景相关上下文中检查错误场景数
        if re.search(r"(?<!\d)(30|33|35)\s*个.*场景|场景.*(?<!\d)(30|33|35)\s*个", c):
            check(f"场景数 @ {f}", False, "场景数应为 34")

    # ━━ 汇总 ━━
    print("\n" + "=" * 60)
    failures = [r for r in results if FAIL in r[1]]
    if failures:
        print(f"  {len(failures)} 项失败 — 修正后重新运行")
    else:
        print("  全部通过")
    print("=" * 60)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
