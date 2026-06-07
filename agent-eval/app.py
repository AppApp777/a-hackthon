"""Streamlit frontend — one-stop evaluation system for outbound call instructions.

Oracle requirement: "评委不打开终端就能理解整个系统。"
Four panels: Input → Compiled Preview → Conversation Trace → Evaluation Report
Three modes: Run New | Browse History | Demo (offline)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

from compile_instruction import compile_instruction, get_compiled_preview
from orchestrator_outbound import OutboundOrchestrator, load_outbound_scenario

# ── Page Config ──
st.set_page_config(
    page_title="外呼对话评测系统",
    page_icon="📞",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ──
st.markdown(
    """
<style>
    /* Score card styling */
    .score-high { color: #0f5132; background: #d1e7dd; padding: 0.8rem; border-radius: 0.5rem; text-align: center; }
    .score-mid  { color: #664d03; background: #fff3cd; padding: 0.8rem; border-radius: 0.5rem; text-align: center; }
    .score-low  { color: #842029; background: #f8d7da; padding: 0.8rem; border-radius: 0.5rem; text-align: center; }
    .score-num  { font-size: 2.5rem; font-weight: 700; margin: 0; }
    .score-label { font-size: 0.85rem; opacity: 0.7; }

    /* Step status pills */
    .step-ok   { display: inline-block; padding: 2px 8px; border-radius: 10px; background: #d1e7dd; color: #0f5132; font-size: 0.8rem; }
    .step-fail { display: inline-block; padding: 2px 8px; border-radius: 10px; background: #f8d7da; color: #842029; font-size: 0.8rem; }
    .step-skip { display: inline-block; padding: 2px 8px; border-radius: 10px; background: #e2e3e5; color: #41464b; font-size: 0.8rem; }
    .step-na   { display: inline-block; padding: 2px 8px; border-radius: 10px; background: #cff4fc; color: #055160; font-size: 0.8rem; }

    /* Sidebar section titles */
    .sidebar-section { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; opacity: 0.6; margin-top: 1rem; }

    /* Header area */
    .main-title { margin-bottom: 0; }
    .main-subtitle { opacity: 0.6; font-size: 0.9rem; margin-top: -0.5rem; }

    /* Reduce padding */
    .block-container { padding-top: 2rem; }
</style>
""",
    unsafe_allow_html=True,
)

# ── Constants ──
SCENARIO_DIR = Path(__file__).parent / "scenarios" / "outbound"
TRACE_DIR = Path(__file__).parent / "traces"

MODEL_OPTIONS = {
    "MiniMax-M2.7": "MiniMax-M2.7",
    "MiniMax-M2.7-highspeed": "MiniMax-M2.7-highspeed",
    "Kimi K2.6": "kimi-for-coding",
    "Claude Opus 4.6": "claude-opus-4-6",
    "Claude Sonnet 4.6": "claude-sonnet-4-6",
    "Qwen-Max": "qwen-max",
    "DeepSeek": "deepseek-chat",
    "（默认 Claude CLI）": None,
}

SCENARIO_LABELS = {
    "rider_feimaotui_notify": "🏍️ 飞毛腿骑手合同通知",
    "course_livestream_upgrade": "🎓 课程直播产品升级",
    "delivery_confirm_basic": "📦 配送确认（基础）",
    "after_sales_complaint": "🛎️ 售后投诉处理",
    "delay_notify_difficult": "⏰ 延迟通知（困难）",
    "stress_test_extreme": "🔥 极端压力测试",
}


# ── Helper functions ──
def _load_trace_file(path: Path) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _list_traces() -> list[dict]:
    if not TRACE_DIR.exists():
        return []
    traces = []
    for p in sorted(TRACE_DIR.glob("outbound_*.json"), reverse=True):
        data = _load_trace_file(p)
        if not data:
            continue
        meta = data.get("metadata", {})
        report = meta.get("outbound_report", {})
        score = report.get("overall_score_100")
        scenario_name = data.get("scenario", {}).get("name", "未知场景")
        model = data.get("run_metadata", {}).get("model_backend", "unknown")
        created = data.get("created_at", "")[:16]
        traces.append(
            {
                "file": p.name,
                "path": p,
                "scenario": scenario_name,
                "model": model,
                "score": score,
                "created": created,
                "trace_id": data.get("id", "")[:8],
            }
        )
    return traces


def _score_css_class(score: int | float | None) -> str:
    if score is None:
        return "score-mid"
    if score >= 70:
        return "score-high"
    if score >= 50:
        return "score-mid"
    return "score-low"


def _render_score_card(score: int | float | None, label: str):
    css = _score_css_class(score)
    val = f"{score}" if score is not None else "—"
    st.markdown(
        f'<div class="{css}"><p class="score-num">{val}</p><p class="score-label">{label}</p></div>',
        unsafe_allow_html=True,
    )


def _render_report(report_data: dict, diagnosis: dict, sim_quality: dict, conversation_data=None):
    """Render the full evaluation report from trace data."""

    # ── Score Overview ──
    st.markdown("### 📊 评分总览")
    score_100 = report_data.get("overall_score_100")

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        _render_score_card(score_100, "综合得分")
    with c2:
        hard = report_data.get("hard_score")
        _render_score_card(round(hard * 100) if hard else None, "硬指标")
    with c3:
        soft = report_data.get("soft_score")
        _render_score_card(round(soft * 100) if soft else None, "软指标")
    with c4:
        step = report_data.get("step_compliance_score")
        _render_score_card(round(step * 100) if step else None, "步骤合规")
    with c5:
        branch = report_data.get("branch_accuracy_score")
        _render_score_card(round(branch * 100) if branch is not None else None, "分支准确")

    # Quick facts row
    st.markdown("")
    qc1, qc2, qc3, qc4 = st.columns(4)
    with qc1:
        ok = "✅" if report_data.get("opening_correct") else "❌"
        st.write(f"{ok} 开场白")
    with qc2:
        ok = "✅" if report_data.get("closing_correct") else "❌"
        st.write(f"{ok} 结束语")
    with qc3:
        ok = "✅" if report_data.get("call_result_correct") else "❌"
        st.write(f"{ok} 通话结果")
    with qc4:
        cnt = report_data.get("forbidden_violation_count", 0)
        ok = "✅" if cnt == 0 else "❌"
        st.write(f"{ok} 禁止行为 ({cnt})")

    # ── Tabs for details ──
    tab_steps, tab_rubric, tab_failures, tab_diag, tab_sim = st.tabs(
        ["📋 步骤执行", "📐 Rubric 维度", "⚠️ 失败清单", "🔍 诊断分析", "🤖 模拟器质量"]
    )

    with tab_steps:
        step_compliance = report_data.get("step_compliance", [])
        if step_compliance:
            for sc in step_compliance:
                status = sc.get("status", "unknown")
                icon_map = {
                    "completed": ("✅", "step-ok"),
                    "failed": ("❌", "step-fail"),
                    "skipped": ("⏭️", "step-skip"),
                    "not_reached": ("➖", "step-na"),
                    "not_applicable": ("⬜", "step-na"),
                }
                icon, css = icon_map.get(status, ("❓", "step-na"))
                turn_info = f" — 第{sc['turn']}轮" if sc.get("turn") else ""
                st.markdown(
                    f"{icon} **[{sc['step_id']}]** {sc['instruction'][:60]}"
                    f' <span class="{css}">{status}</span>{turn_info}',
                    unsafe_allow_html=True,
                )
        else:
            st.info("无步骤合规数据（可能 LLM judge 未启用）")

    with tab_rubric:
        rubric = report_data.get("rubric", {})
        dims = rubric.get("dimensions", [])
        if dims:
            for d in dims:
                score_val = d.get("score", 0)
                undertested = " ⚠️ 未充分测试" if d.get("undertested") else ""
                st.progress(
                    score_val / 5.0,
                    text=f"{d['dimension_id']} {d['name']}: {score_val}/5{undertested}",
                )
            grade = rubric.get("grade", "")
            total = rubric.get("rubric_total", 0)
            max_s = rubric.get("rubric_max", 32)
            st.markdown(f"**Rubric 总分**: {total}/{max_s} **[{grade}]**")
        else:
            st.info("无 Rubric 数据")

    with tab_failures:
        failures = report_data.get("failure_summary", [])
        violations = report_data.get("forbidden_violations", [])
        if failures:
            for f in failures:
                st.error(f)
        if violations:
            st.markdown("**禁止行为违规详情**")
            for v in violations:
                st.warning(
                    f"[{v.get('severity', '')}] 第{v.get('turn', '?')}轮: {v.get('description', '')}"
                )
        if not failures and not violations:
            st.success("无失败项")

    with tab_diag:
        if diagnosis and diagnosis.get("failure_modes"):
            if diagnosis.get("deviation_point"):
                dp = diagnosis["deviation_point"]
                st.markdown(f"**首次偏差**: 第{dp.get('turn', '?')}轮")
                st.markdown(f"> **期望**: {dp.get('expected_behavior', '')}")
                st.markdown(f"> **实际**: {dp.get('actual_behavior', '')}")
            st.markdown(f"**根因**: {diagnosis.get('root_cause', '')}")
            st.markdown(f"**严重程度**: {diagnosis.get('severity', '')}")
            recs = diagnosis.get("fix_recommendations", [])
            if recs:
                st.markdown("**修复建议**:")
                for rec in recs:
                    st.markdown(f"- {rec}")
        else:
            st.success("无显著偏差（通过诊断门槛）")

    with tab_sim:
        if sim_quality:
            if sim_quality.get("passed", True):
                st.success("模拟器质量检查通过")
            else:
                st.warning("模拟器质量检查未通过")
            for c in sim_quality.get("checks", []):
                icon = "✅" if c["passed"] else "❌"
                st.markdown(f"{icon} {c['description']}: {c['detail']}")
            for w in sim_quality.get("warnings", []):
                st.markdown(f"⚠️ {w}")
        else:
            st.info("无模拟器质量数据")


def _render_conversation(messages: list[dict]):
    """Render conversation messages from trace data."""
    for msg in messages:
        role = msg.get("role", "")
        turn = msg.get("turn", "?")
        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls", [])

        if role == "user":
            with st.chat_message("user"):
                st.markdown(f"**[第{turn}轮] 被叫方**: {content}")
        elif role == "agent":
            with st.chat_message("assistant"):
                st.markdown(f"**[第{turn}轮] 外呼Agent**: {content}")
                for tc in tool_calls:
                    if tc.get("error"):
                        st.error(f"🔧 {tc['tool_name']}: {tc['error']}")
                    else:
                        result_str = json.dumps(
                            tc.get("result", {}), ensure_ascii=False, default=str
                        )[:200]
                        st.success(f"🔧 {tc['tool_name']}: {result_str}")
        elif role == "system":
            st.info(f"[系统] {content[:150]}")


def _render_compiled_preview(preview: dict):
    """Render instruction compiled preview."""
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**基本信息**")
        st.markdown(f"- **任务**: {preview['task']}")
        st.markdown(f"- **开场白**: {preview['opening_line'] or '（无强制要求）'}")
        st.markdown(f"- **回复长度限制**: {preview['response_length_limit'] or '无'}字")
        st.markdown(f"- **被叫方角色**: {preview['callee_role']}")
        st.markdown(f"- **最大轮次**: {preview['max_turns']}")

    with col2:
        st.markdown("**知识点 / FAQ**")
        for kp in preview["knowledge_points"]:
            st.markdown(f"- {kp}")
        if not preview["knowledge_points"]:
            st.caption("（无）")

    st.markdown("**指令步骤**")
    for step in preview["steps"]:
        optional_tag = " *(可选)*" if step["is_optional"] else ""
        st.markdown(f"**{step['order']}. [{step['step_id']}]** {step['instruction']}{optional_tag}")
        for b in step["branches"]:
            st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;↳ 若 {b['condition']} → {b['next_step']}")

    if preview["forbidden_behaviors"]:
        st.markdown("**禁止行为**")
        for fb in preview["forbidden_behaviors"]:
            st.markdown(f"- 🚫 [{fb['severity']}] {fb['description']}")


# ══════════════════════════════════════════════════════════════
# ── Sidebar ──
# ══════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("## 📞 外呼对话评测")
    mode = st.radio(
        "操作模式",
        ["▶️ 运行新评测", "📁 查看历史轨迹", "🎬 离线演示"],
        index=0,
        label_visibility="collapsed",
    )

    if mode == "▶️ 运行新评测":
        st.markdown('<p class="sidebar-section">场景输入</p>', unsafe_allow_html=True)
        input_mode = st.radio(
            "输入方式",
            ["选择预置场景", "粘贴 Markdown 指令"],
            index=0,
            label_visibility="collapsed",
        )

        if input_mode == "粘贴 Markdown 指令":
            raw_instruction = st.text_area(
                "任务指令（Markdown）",
                height=200,
                placeholder="# Role\n你是...\n\n# Task\n...\n\n# Call Flow\n1. ...",
            )
            callee_role = st.text_input("被叫方角色", value="接电话的用户")
            callee_goal = st.text_input("被叫方目标（隐藏）", value="配合对方完成通话")
        else:
            scenario_files = sorted(SCENARIO_DIR.glob("*.json"))
            scenario_names = {f.stem: f for f in scenario_files}
            display_names = [SCENARIO_LABELS.get(k, k) for k in scenario_names]
            name_to_key = dict(zip(display_names, scenario_names.keys(), strict=False))
            selected_display = st.selectbox("选择场景", display_names)
            selected = name_to_key[selected_display]
            raw_instruction = ""
            callee_role = ""
            callee_goal = ""

        st.markdown('<p class="sidebar-section">被测模型</p>', unsafe_allow_html=True)
        model_display = st.selectbox("模型", list(MODEL_OPTIONS.keys()), index=0)
        selected_model = MODEL_OPTIONS[model_display]

        st.markdown('<p class="sidebar-section">评测参数</p>', unsafe_allow_html=True)
        fast_mode = st.checkbox("⚡ 快速评分（单次 LLM 调用）", value=True)
        use_llm_judge = st.checkbox("🧠 启用 LLM 评分", value=True)
        use_harness = st.checkbox("🛡️ 启用 Harness 围栏", value=False)

        run_button = st.button("🚀 运行评测", type="primary", use_container_width=True)

    elif mode == "📁 查看历史轨迹":
        traces = _list_traces()
        if traces:
            st.markdown(
                f'<p class="sidebar-section">{len(traces)} 条历史轨迹</p>', unsafe_allow_html=True
            )
            trace_labels = []
            for t in traces:
                score_str = f"{t['score']}分" if t["score"] is not None else "—"
                trace_labels.append(f"{t['trace_id']} | {score_str} | {t['model']}")
            selected_trace_idx = st.selectbox(
                "选择轨迹", range(len(trace_labels)), format_func=lambda i: trace_labels[i]
            )
        else:
            st.info("traces/ 目录为空，先运行一次评测")
            selected_trace_idx = None

    elif mode == "🎬 离线演示":
        traces = _list_traces()
        st.markdown('<p class="sidebar-section">演示模式</p>', unsafe_allow_html=True)
        st.caption("使用缓存轨迹演示，无需 API 调用")
        if traces:
            demo_labels = []
            for t in traces:
                score_str = f"{t['score']}分" if t["score"] is not None else "—"
                demo_labels.append(f"{t['scenario']} | {score_str}")
            selected_demo_idx = st.selectbox(
                "演示轨迹", range(len(demo_labels)), format_func=lambda i: demo_labels[i]
            )
        else:
            st.warning("无缓存轨迹，请先运行评测生成数据")
            selected_demo_idx = None


# ══════════════════════════════════════════════════════════════
# ── Main Content Area ──
# ══════════════════════════════════════════════════════════════

st.markdown(
    '<h1 class="main-title">外呼对话评测系统</h1>'
    '<p class="main-subtitle">复杂指令下的多轮对话评测 · 美团黑客松赛道二</p>',
    unsafe_allow_html=True,
)

# ── Mode: Run New Evaluation ──
if mode == "▶️ 运行新评测":
    if run_button:
        # Build scenario
        if input_mode == "粘贴 Markdown 指令" and raw_instruction.strip():
            with st.spinner("编译指令..."):
                scenario = compile_instruction(
                    raw_instruction, callee_role=callee_role, callee_goal=callee_goal
                )
        elif input_mode == "选择预置场景":
            scenario = load_outbound_scenario(str(scenario_names[selected]))
        else:
            st.error("请输入任务指令或选择预置场景")
            st.stop()

        # ── Compiled Preview (collapsible) ──
        with st.expander("📝 指令编译预览", expanded=True):
            preview = get_compiled_preview(scenario)
            _render_compiled_preview(preview)

        st.divider()

        # ── Run Evaluation ──
        model_label = model_display if selected_model else "Claude CLI"
        with st.spinner(f"🔄 正在用 {model_label} 运行评测..."):
            from harness import HarnessConfig

            orch = OutboundOrchestrator(
                scenario=scenario,
                use_llm_judge=use_llm_judge,
                use_harness=use_harness,
                fast_mode=fast_mode,
                agent_model=selected_model,
                harness_config=HarnessConfig() if use_harness else None,
            )
            trace = orch.run(verbose=False)

        report_data = trace.metadata.get("outbound_report", {})
        diagnosis = trace.metadata.get("diagnosis", {})
        sim_quality = trace.metadata.get("simulator_quality", {})
        conversation = trace.conversation

        # ── Conversation Trace ──
        with st.expander("💬 对话轨迹", expanded=False):
            for msg in conversation.messages:
                if msg.role.value == "user":
                    with st.chat_message("user"):
                        st.markdown(f"**[第{msg.turn}轮] 被叫方**: {msg.content}")
                elif msg.role.value == "agent":
                    with st.chat_message("assistant"):
                        st.markdown(f"**[第{msg.turn}轮] 外呼Agent**: {msg.content}")
                        for tc in msg.tool_calls:
                            if tc.error:
                                st.error(f"🔧 {tc.tool_name}: {tc.error}")
                            else:
                                st.success(
                                    f"🔧 {tc.tool_name}: {json.dumps(tc.result, ensure_ascii=False, default=str)[:200]}"
                                )
                elif msg.role.value == "system":
                    st.info(f"[系统] {msg.content[:150]}")

            if conversation.termination_reason:
                st.caption(f"通话结束: {conversation.termination_reason}")

        st.divider()

        # ── Evaluation Report ──
        _render_report(report_data, diagnosis, sim_quality)

        # ── Download ──
        st.divider()
        trace_json = json.dumps(
            trace.model_dump(mode="json"), ensure_ascii=False, indent=2, default=str
        )
        st.download_button(
            "📥 下载完整 Trace JSON",
            data=trace_json,
            file_name=f"trace_{trace.id[:8]}.json",
            mime="application/json",
        )

    else:
        # Landing page when no evaluation is running
        st.markdown("---")
        st.markdown("### 👋 使用指南")
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            st.markdown(
                "**1. 选择场景**\n\n"
                "在左侧选择预置场景或粘贴自定义 Markdown 指令。"
                "系统支持 6 个预置场景覆盖不同难度。"
            )
        with col_b:
            st.markdown(
                "**2. 选择模型**\n\n"
                "选择被测模型（MiniMax / Kimi / Claude 等）。"
                "模拟器和评委使用 Claude Opus。"
            )
        with col_c:
            st.markdown(
                "**3. 运行评测**\n\n"
                "点击「运行评测」，系统自动：编译指令 → 模拟对话 → 产出报告。"
                "快速模式 ≤10 秒出结果。"
            )

        st.markdown("---")
        # Show existing trace summary
        traces = _list_traces()
        if traces:
            st.markdown(f"### 📊 已有 {len(traces)} 条评测记录")
            cols = st.columns(min(len(traces), 4))
            for i, t in enumerate(traces[:4]):
                with cols[i]:
                    score_val = t["score"] if t["score"] is not None else "—"
                    css = _score_css_class(t["score"])
                    st.markdown(
                        f'<div class="{css}" style="margin-bottom:0.5rem">'
                        f'<p class="score-num" style="font-size:1.8rem">{score_val}</p>'
                        f'<p class="score-label">{t["scenario"][:12]}</p>'
                        f'<p class="score-label">{t["model"]}</p>'
                        f"</div>",
                        unsafe_allow_html=True,
                    )


# ── Mode: Browse History ──
elif mode == "📁 查看历史轨迹":
    if not traces or selected_trace_idx is None:
        st.info("暂无历史轨迹")
    else:
        selected_t = traces[selected_trace_idx]
        data = _load_trace_file(selected_t["path"])
        if not data:
            st.error("加载轨迹失败")
        else:
            st.markdown(f"### 轨迹 `{selected_t['trace_id']}` — {selected_t['scenario']}")
            st.caption(
                f"模型: {selected_t['model']} · 时间: {selected_t['created']} · "
                f"文件: {selected_t['file']}"
            )

            meta = data.get("metadata", {})
            report_data = meta.get("outbound_report", {})
            diagnosis = meta.get("diagnosis", {})
            sim_quality = meta.get("simulator_quality", {})
            conv = data.get("conversation", {})
            messages = conv.get("messages", [])

            # Compiled preview from scenario data
            scenario_data = data.get("scenario", {})
            with st.expander("📝 场景信息", expanded=False):
                st.write(f"**名称**: {scenario_data.get('name', '')}")
                st.write(f"**描述**: {scenario_data.get('description', '')}")
                st.write(f"**难度**: {scenario_data.get('difficulty', '')}")

            # Conversation
            with st.expander("💬 对话轨迹", expanded=False):
                _render_conversation(messages)
                term = conv.get("termination_reason")
                if term:
                    st.caption(f"通话结束: {term}")

            st.divider()

            # Report
            _render_report(report_data, diagnosis, sim_quality)

            # Download
            st.divider()
            st.download_button(
                "📥 下载 Trace JSON",
                data=json.dumps(data, ensure_ascii=False, indent=2, default=str),
                file_name=selected_t["file"],
                mime="application/json",
            )


# ── Mode: Offline Demo ──
elif mode == "🎬 离线演示":
    if not traces or selected_demo_idx is None:
        st.warning("无缓存轨迹可演示。请先在「运行新评测」模式下生成数据。")
    else:
        selected_t = traces[selected_demo_idx]
        data = _load_trace_file(selected_t["path"])
        if not data:
            st.error("加载演示轨迹失败")
        else:
            st.markdown("### 🎬 离线演示模式")
            st.caption("以下内容来自缓存轨迹，无需网络和 API 调用")

            meta = data.get("metadata", {})
            report_data = meta.get("outbound_report", {})
            diagnosis = meta.get("diagnosis", {})
            sim_quality = meta.get("simulator_quality", {})
            conv = data.get("conversation", {})
            messages = conv.get("messages", [])
            scenario_data = data.get("scenario", {})

            # Demo header
            st.markdown(f"**场景**: {selected_t['scenario']}")
            st.markdown(f"**被测模型**: {selected_t['model']}")

            # Step 1: Compiled Preview
            st.markdown("---")
            st.markdown("#### 第一步：指令编译预览")
            st.caption("系统把 Markdown 任务指令编译成可审计的评测计划")
            with st.expander("📝 编译结果", expanded=True):
                st.write(f"**场景名称**: {scenario_data.get('name', '')}")
                st.write(f"**描述**: {scenario_data.get('description', '')}")
                st.write(f"**最大轮次**: {scenario_data.get('max_turns', '')}")
                # Show steps from report
                steps = report_data.get("step_compliance", [])
                if steps:
                    st.markdown("**指令步骤**:")
                    for i, s in enumerate(steps, 1):
                        st.write(f"{i}. [{s['step_id']}] {s['instruction'][:60]}")

            # Step 2: Conversation
            st.markdown("---")
            st.markdown("#### 第二步：对话轨迹")
            st.caption("Agent 与模拟用户的多轮对话，含工具调用和 Harness 干预")
            with st.expander("💬 完整对话", expanded=True):
                _render_conversation(messages)
                term = conv.get("termination_reason")
                if term:
                    st.caption(f"通话结束: {term}")

            # Step 3: Evaluation Report
            st.markdown("---")
            st.markdown("#### 第三步：评估报告")
            st.caption("0-100 分综合评分 + 步骤级诊断 + 根因分析 + 修复建议")
            _render_report(report_data, diagnosis, sim_quality)

            # Step 4: Download
            st.divider()
            st.download_button(
                "📥 下载完整 Trace JSON",
                data=json.dumps(data, ensure_ascii=False, indent=2, default=str),
                file_name=selected_t["file"],
                mime="application/json",
            )
