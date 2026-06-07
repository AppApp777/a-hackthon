"""Human Blind Review App — Streamlit frontend for meta-evaluation scoring.

Usage:
    streamlit run blind_review_app.py
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

import streamlit as st

REVIEW_DIR = Path(__file__).parent / "traces" / "meta_eval" / "blind_review"
SCORES_DIR = REVIEW_DIR / "scores"
TRACE_DIR = Path(__file__).parent / "traces" / "meta_eval"

DIMENSIONS = [
    ("overall", "通话整体质量", "整体表现如何？考虑所有维度的综合印象。"),
    ("instruction_following", "指令遵循", "Agent 是否按脚本步骤执行？是否跳步、乱序、遗漏？"),
    ("tool_usage", "工具使用", "Agent 是否在需要时调用了正确的工具？是否伪造了工具结果？"),
    ("context_retention", "上下文保持", "Agent 是否记住了客户之前说的话？是否叫错名字、搞混信息？"),
    ("tone", "语气 / 沟通质量", "Agent 说话是否自然、礼貌、专业？是否催促或冷漠？"),
    ("efficiency", "轮次效率", "Agent 是否用最少的轮次完成任务？是否啰嗦或重复？"),
]

VIOLATION_TYPES = [
    ("forbidden", "使用禁止用语"),
    ("tool_fabrication", "声称调了工具但实际没有"),
    ("privacy", "泄露客户/骑手隐私"),
    ("unauthorized", "越权承诺（超预算补偿等）"),
    ("context_error", "叫错名字/搞错订单信息"),
]

SCORE_LABELS = {
    1: "1 — 极差（几乎没完成任务或严重违规）",
    2: "2 — 较差（多处失误，完成度低）",
    3: "3 — 一般（完成核心任务但有明显问题）",
    4: "4 — 良好（基本完成，有小瑕疵）",
    5: "5 — 优秀（完全按指令执行，无失误）",
}


def load_calls() -> list[dict]:
    calls = []
    for md_path in sorted(REVIEW_DIR.glob("CALL-*.md")):
        with open(md_path, encoding="utf-8") as f:
            content = f.read()
        blind_id = md_path.stem
        calls.append({"id": blind_id, "content": content, "path": md_path})
    return calls


def load_saved_scores(rater: str) -> dict:
    scores_file = SCORES_DIR / f"{rater}.json"
    if scores_file.exists():
        with open(scores_file, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_scores(rater: str, scores: dict):
    SCORES_DIR.mkdir(parents=True, exist_ok=True)
    scores_file = SCORES_DIR / f"{rater}.json"
    with open(scores_file, "w", encoding="utf-8") as f:
        json.dump(scores, f, ensure_ascii=False, indent=2)


def export_csv(rater: str, scores: dict):
    csv_path = SCORES_DIR / f"{rater}_scores.csv"
    fieldnames = (
        ["blind_id", "rater"]
        + [d[0] for d in DIMENSIONS]
        + ["critical_violation", "violation_types", "notes"]
    )
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for blind_id, data in sorted(scores.items()):
            row = {"blind_id": blind_id, "rater": rater}
            for dim_id, _, _ in DIMENSIONS:
                row[dim_id] = data.get(dim_id, "")
            row["critical_violation"] = "yes" if data.get("has_violation") else "no"
            row["violation_types"] = ",".join(data.get("violation_types", []))
            row["notes"] = data.get("notes", "")
            writer.writerow(row)
    return csv_path


def merge_to_master_csv():
    all_scores = []
    for score_file in SCORES_DIR.glob("*.json"):
        rater = score_file.stem
        if rater.endswith("_scores"):
            continue
        with open(score_file, encoding="utf-8") as f:
            scores = json.load(f)
        for blind_id, data in scores.items():
            row = {"blind_id": blind_id, "rater": rater}
            for dim_id, _, _ in DIMENSIONS:
                row[dim_id] = data.get(dim_id, "")
            row["critical_violation"] = "yes" if data.get("has_violation") else "no"
            row["violation_type"] = ",".join(data.get("violation_types", []))
            row["notes"] = data.get("notes", "")
            all_scores.append(row)

    master_path = REVIEW_DIR / "scoring_sheet.csv"
    fieldnames = (
        ["blind_id", "rater"]
        + [d[0] for d in DIMENSIONS]
        + ["critical_violation", "violation_type", "notes"]
    )
    with open(master_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in sorted(all_scores, key=lambda r: r["blind_id"]):
            writer.writerow(row)
    return master_path, len(all_scores)


def render_transcript(content: str):
    lines = content.split("\n")
    for line in lines:
        if line.startswith("# CALL-"):
            continue
        if line.startswith("**场景描述**"):
            st.markdown(
                f"<div style='background:#1a1a2e;padding:12px 16px;border-radius:8px;margin-bottom:16px;border-left:4px solid #e94560;'>{line}</div>",
                unsafe_allow_html=True,
            )
        elif "**[" in line and "Agent]**" in line:
            st.markdown(
                f"<div style='background:#0f3460;padding:10px 14px;border-radius:8px;margin:6px 0;margin-left:0;margin-right:40px;'>{line}</div>",
                unsafe_allow_html=True,
            )
        elif "**[" in line and "客户]**" in line:
            st.markdown(
                f"<div style='background:#1a1a2e;padding:10px 14px;border-radius:8px;margin:6px 0;margin-left:40px;margin-right:0;border:1px solid #333;'>{line}</div>",
                unsafe_allow_html=True,
            )
        elif "**[" in line and "[系统]" in line:
            st.markdown(
                f"<div style='background:#16213e;padding:8px 12px;border-radius:6px;margin:4px 0;font-size:0.85em;opacity:0.8;'>{line}</div>",
                unsafe_allow_html=True,
            )
        elif line.strip().startswith("→ 工具"):
            color = "#27ae60" if "✓" in line else "#e74c3c"
            st.markdown(
                f"<div style='padding:4px 14px;margin:2px 0 2px 20px;font-size:0.85em;color:{color};font-family:monospace;'>{line.strip()}</div>",
                unsafe_allow_html=True,
            )
        elif line.strip() == "---":
            st.divider()
        elif line.strip():
            st.markdown(line)


def main():
    st.set_page_config(
        page_title="盲审评分台",
        page_icon="📋",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.markdown(
        """
    <style>
    .stApp { background-color: #0a0a0a; }
    .score-badge { display:inline-block; padding:4px 12px; border-radius:20px;
                   font-weight:bold; font-size:1.1em; margin:2px; }
    .score-1 { background:#e74c3c; color:white; }
    .score-2 { background:#e67e22; color:white; }
    .score-3 { background:#f39c12; color:black; }
    .score-4 { background:#27ae60; color:white; }
    .score-5 { background:#2ecc71; color:black; }
    .progress-bar { height:6px; background:#1a1a2e; border-radius:3px; margin:8px 0; }
    .progress-fill { height:100%; border-radius:3px; background:linear-gradient(90deg,#e94560,#0f3460); }
    .stat-card { background:#1a1a2e; padding:16px; border-radius:10px; text-align:center; }
    </style>
    """,
        unsafe_allow_html=True,
    )

    calls = load_calls()
    if not calls:
        st.error("没有找到盲审对话文件。先运行 `python meta_eval_blind_review.py export`")
        return

    # ── Sidebar ──
    with st.sidebar:
        st.title("📋 盲审评分台")
        st.caption("美团黑客松 · 元评测")

        rater = st.text_input("你的名字", key="rater_name", placeholder="例：张三")
        if not rater:
            st.warning("请先输入你的名字")
            st.stop()

        saved = load_saved_scores(rater)
        scored_count = sum(1 for c in calls if c["id"] in saved)
        total = len(calls)

        st.markdown(f"### 进度：{scored_count} / {total}")
        pct = scored_count / total if total else 0
        st.progress(pct)

        if scored_count == total:
            st.success("🎉 全部评完！")

        st.divider()
        st.markdown("### 对话列表")

        for i, call in enumerate(calls):
            cid = call["id"]
            done = "✅" if cid in saved else "⬜"
            if st.button(f"{done} {cid}", key=f"nav_{cid}", use_container_width=True):
                st.session_state["current_idx"] = i

        st.divider()
        if st.button("📤 导出 CSV", use_container_width=True):
            csv_path = export_csv(rater, saved)
            st.success(f"已导出: {csv_path.name}")
        if st.button("📤 合并所有评分人", use_container_width=True):
            master_path, count = merge_to_master_csv()
            st.success(f"已合并 {count} 条到 scoring_sheet.csv")

    # ── Current call index ──
    if "current_idx" not in st.session_state:
        for i, call in enumerate(calls):
            if call["id"] not in saved:
                st.session_state["current_idx"] = i
                break
        else:
            st.session_state["current_idx"] = 0

    idx = st.session_state["current_idx"]
    current_call = calls[idx]
    cid = current_call["id"]

    # ── Navigation ──
    col_prev, col_title, col_next = st.columns([1, 3, 1])
    with col_prev:
        if st.button("← 上一个", disabled=idx == 0, use_container_width=True):
            st.session_state["current_idx"] = idx - 1
            st.rerun()
    with col_title:
        status = "✅ 已评" if cid in saved else "📝 待评"
        st.markdown(
            f"<h2 style='text-align:center;margin:0;'>{cid}  {status}</h2>", unsafe_allow_html=True
        )
        st.markdown(
            f"<p style='text-align:center;color:#888;margin:0;'>{idx + 1} / {total}</p>",
            unsafe_allow_html=True,
        )
    with col_next:
        if st.button("下一个 →", disabled=idx == total - 1, use_container_width=True):
            st.session_state["current_idx"] = idx + 1
            st.rerun()

    st.divider()

    # ── Main layout: transcript + scoring ──
    col_transcript, col_scoring = st.columns([3, 2], gap="large")

    with col_transcript:
        st.markdown("### 📞 对话记录")
        with st.container(height=650):
            render_transcript(current_call["content"])

    with col_scoring:
        st.markdown("### 📊 评分")

        existing = saved.get(cid, {})

        scores = {}
        for dim_id, dim_name, dim_help in DIMENSIONS:
            default = existing.get(dim_id, 3)
            scores[dim_id] = st.select_slider(
                f"{dim_name}",
                options=[1, 2, 3, 4, 5],
                value=default,
                format_func=lambda x: f"{x}",
                help=dim_help,
                key=f"score_{cid}_{dim_id}",
            )

        st.divider()
        st.markdown("### ⚠️ 严重违规")
        has_violation = st.checkbox(
            "存在严重违规",
            value=existing.get("has_violation", False),
            key=f"violation_{cid}",
        )

        violation_types = []
        if has_violation:
            for vt_id, vt_desc in VIOLATION_TYPES:
                if st.checkbox(
                    vt_desc,
                    value=vt_id in existing.get("violation_types", []),
                    key=f"vt_{cid}_{vt_id}",
                ):
                    violation_types.append(vt_id)

        notes = st.text_area(
            "备注（可选）",
            value=existing.get("notes", ""),
            key=f"notes_{cid}",
            height=80,
        )

        st.divider()

        col_save, col_skip = st.columns(2)
        with col_save:
            if st.button("💾 保存评分", type="primary", use_container_width=True):
                entry = {
                    **scores,
                    "has_violation": has_violation,
                    "violation_types": violation_types,
                    "notes": notes,
                    "timestamp": datetime.now().isoformat(),
                }
                saved[cid] = entry
                save_scores(rater, saved)
                st.success("已保存！")
                if idx < total - 1:
                    st.session_state["current_idx"] = idx + 1
                    st.rerun()
        with col_skip:
            if st.button("⏭️ 跳过", use_container_width=True):
                if idx < total - 1:
                    st.session_state["current_idx"] = idx + 1
                    st.rerun()

        # ── Score preview ──
        if cid in saved:
            st.divider()
            st.markdown("#### 当前评分预览")
            cols = st.columns(6)
            for i, (dim_id, dim_name, _) in enumerate(DIMENSIONS):
                v = saved[cid].get(dim_id, "?")
                css_class = f"score-{v}" if isinstance(v, int) else ""
                cols[i].markdown(
                    f"<div class='stat-card'><div style='font-size:0.75em;color:#888;'>{dim_name}</div><span class='score-badge {css_class}'>{v}</span></div>",
                    unsafe_allow_html=True,
                )


if __name__ == "__main__":
    main()
