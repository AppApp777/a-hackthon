# 竞品数据资产差距追踪（vs WANGLEVY9/EvalSystem v3.0）

> 创建：2026-06-04 · 来源：6-agent workflow 逐类对比（竞品 `E:\Temp\EvalSystem`，全部带 file:line 证据）
> 目标：补齐"竞品有、我方没有"的数据资产。用户决定：**全部要补，先做必要的。**
> 状态图例：`[ ]` 待做 · `[~]` 进行中 · `[x]` 完成
>
> **🔄 状态复核（2026-06-06，据代码核实）**：6-05 冲刺已落地多项，旧勾选过时，本次按 src 真实状态更新——
> ✅ 已完成：A1 知识幻觉检测、A4 复算元数据、B1 跨会话主聚合报告、B3 HTML+MD 离线导出。
> ⚠️ 部分完成：A3 失败模式聚合（落盘了 top_failure_modes，缺富字段）、B5 复算字段（seed 未透传）、B6 人工校准试点（22 条盲标 + 容差桶，缺 per-case 预期vs实际）、C7。
> ❌ 仍是真缺口：A2（仅 2/34 场景有结构化知识点）、A5+B7（对比报告短板诊断并排，无 comparison 生成器）、B2（画像×维度热力图，dashboard 只有 model×scenario）、B4（逐轮流程节点 inferred_flow_node）、B8（维度级置信度/std）。

## 一句话定调

竞品强在"**把结果包装成给人看的成品**"（知识幻觉检测、跨会话聚合报告、PDF/HTML 导出、画像热力图）；
我方强在"**底层证据扎实可追溯**"（哈希链账本、真工具执行、消融实验、7 个真实模型横评）。
竞品领先的几乎全是**呈现层**，真正算"硬功能缺口"的只有知识幻觉检测 1 项。

---

## A. 高优先级（High）—— 真正要补的

- [x] **A1. 知识幻觉检测**（唯一真功能缺口，新维度）✅ 已落地 2026-06-06
  - 竞品：每会话产出 `KnowledgeAccuracyEvaluation{total_knowledge_queries, correct/incorrect, fabricated_info[], details[{query,expected,actual,correct,turn_id}], quotes}`（`src/models.py:313-320`；report `knowledge_accuracy_eval` 实测 3 条 expected vs actual）
  - 我方：只有 `ContextRetentionChecker` 关键词记忆保持（`scorer_modules/checkers.py:691-732`），无"Agent 业务知识对不对/有没有瞎编"维度
  - 工作量：2-4 天。新增知识准确度 judge，把 `scenario.knowledge_points` 与 Agent 各轮回答逐条比对
  - **已做**：`scorer_outbound.py:577 _check_knowledge_accuracy`（规则层关键词比对 → correct/contradicted/not_mentioned，+1/0/-1，LLM 兜底），输出 `knowledge_accuracy_score`（:1743），测试 `tests/test_knowledge_accuracy.py`。⚠️ 评分器到位但数据覆盖薄——见 A2
- [ ] **A2. 结构化知识库**（A1 的数据前提）⚠️ 真缺口：A1 评分器已就绪，但仍只有 2/34 outbound 场景有 `knowledge_points`，A1 大面积无数据可评
  - 竞品：每场景 5 条 FAQ `{question/answer/keywords[]/category}`（`course_platform_upgrade.json:304-335`）
  - 我方：34 个 outbound 仅 2 个有 `knowledge_points`，扁平字符串无结构；`course_livestream_upgrade.json:11-17` 第 5 条粘错成飞毛腿条目
  - 工作量：1-2 天。给缺的 32 场景补结构化问答库 + 修错条目
- [~] **A3. 跨会话失败模式排行（落盘+富信息）** 部分完成：`report_generator.py:166 _sec6_failure_modes` 已跨 trace 聚合 `top_failure_modes`(most_common 10)+severity 并落盘 .md/.json；**仍缺**竞品的富字段 affected_personas/impact_score/typical_quote/suggestion
  - 竞品：`weakness_profile.top_failure_modes` 落盘，每条 `name/category/occurrences(27次)/affected_personas/impact_score(162)/typical_quote/suggestion`（`src/weakness_analyzer.py:73-157`）
  - 我方：`dashboard.py:112-124` 仅请求时临时数 Top-5，无 affected_personas/impact/quote、不落盘；单 trace 级有 `diagnosis`
  - 工作量：半天到 1 天。原始数据每 trace 都有，缺跨 trace 聚合器 + 落盘
- [x] **A4. 评委模型版本 judge_model_id 没记进结果** ⭐ 已完成 2026-06-04
  - 竞品：`run_metadata.judge_model_id/target_model_id/simulator_model_id`（`report_*.json:53271-53273`）
  - 我方原状：`RunMetadata` 只有 `model_backend`(被测)，无 judge/simulator 模型
  - **已做**：`RunMetadata` 加 judge_model_id/judge_model_secondary_id/simulator_model_id，`orchestrator._build_run_metadata()` 记录真实模型；judge OFF 时诚实记 None。测试 `test_run_metadata.py` 9 项
- [ ] **A5. 模型对比报告缺"短板诊断并排"** ❌ 真缺口：仓内已无独立 comparison 生成器，对比仍是纯分数矩阵（与 B7 同源）
  - 竞品：对比两对象时并排 `weakest_dimensions/weakest_personas/top_failure_modes+suggestion/risk_summary`（`comparison_*.json:120-258`）
  - 我方：`generate_comparison.py` 纯分数矩阵，无诊断并排
  - 工作量：约 1 天（与 A3 同源）

---

## B. 中优先级（Medium）

- [x] **B1. 跨会话主聚合报告文件** ✅ 已落地：`report_generator.py build_report(traces)` 跨全部 trace 产单份 10 节聚合报告（.json + .md，含执行摘要/模型对比/失败模式/安全/harness 影响/逐场景/建议）
- [ ] **B2. 画像×维度热力图矩阵**——竞品 `persona × dim` 交叉均分（`report_generator.py:447-461`）；我方只有 `model×scenario`（`dashboard.py:85-105`）。补半天 ❌ 仍缺（dashboard 未加 persona×dim）
- [x] **B3. PDF / 静态 HTML 报告导出** ✅ 已落地（HTML+MD）：`generate_report.py` 产自包含 HTML（评委双击即看，内嵌 trace 数据，不依赖 FastAPI）；`report_generator.py render_markdown` 产 .md；`templates/eval_report.html`(Jinja2) 含 SVG 雷达图。**仅 PDF 一种格式未做**（weasyprint），价值低
- [ ] **B4. 逐轮流程节点标注 turn→step**——竞品 `DialogueTurn.inferred_flow_node` 每轮显示"📍步骤X"；我方只有反向 `StepComplianceEntry.turn`。补 1-2 天 ❌ 仍缺
- [~] **B5. 复算 seed / run_id / evaluator_version**——竞品有 LLM 采样 seed=42、run_id、evaluator_version=3.0.0；我方 `RunMetadata` 全缺。
  - **已做**（2026-06-04，随 A4）：run_id ✓、evaluator_version ✓、started_at/finished_at/duration_seconds ✓
  - **待做**：seed 字段已加但默认 None（尚未透传给 LLM API；claude_cli 后端不支持 seed，OpenAI-compat 后端需在 `llm.py` 各 `_call_*` 透传）
- [~] **B6. per-case 0-100 人工总分 + 容差带 + 预期vs实际对比** 部分完成：2026-06-06 人工校准试点已产 per-case 0-100 人工总分（22 条盲标，`reports/human_calibration_pilot.{md,json}`）+ bucket 容差准确率 + bootstrap CI；**仍缺**每条的 expected_issues vs actual_issues 结构化并排
- [ ] **B7. 对比报告逐维度并排（带置信度/评测方法）+ 画像表现对比 + 分支覆盖对比**——竞品 `comparison_*.json:9-316`；我方对比矩阵仅 5 个聚合值。补半天-1天 ❌ 仍缺（与 A5 同源）
- [ ] **B8. 维度级置信度 + 多次采样 std + "部分完成"四态 + 结构化引文** ❌ 仍缺（scorer 无 score_std/confidence；下注：我方主打确定性层，价值有限）——竞品 `score_samples/score_std/confidence`、checkpoint `partially` 给中间分、`EvidenceQuote{turn_id,role,text,note}`；我方多为单次确定性判定 + 文字说明。补各 1-2 天
  - ⚠️ 诚实备注：我方客观层确定性规则跑两次一样，std 主要对 LLM 软分有意义，价值有限

---

## C. 低优先级（Low）—— 补不补无所谓

- [ ] **C1. 逐轮字数 char_count**——纯展示，`len(content)` 即可。半天
- [ ] **C2. RMSE 总分指标**——加一行公式，几分钟（依赖 B6 先有人工总分）
- [ ] **C3. evaluation_criteria 写进每个 checkpoint**——判分口径从代码搬进数据。半天文案
- [ ] **C4. 单句话术约束打包成结构化对象**（max_chars/forbidden_words/required_tone/applies_to）——聚合散落字段。2-3 小时
- [ ] **C5. 约束 severity 三级 + check_method 标注**——补枚举字段 + violation_examples。半天
- [ ] **C6. Markdown 报告导出**——最易做。半天
- [~] **C7. self_consistency_n / concurrency 运行配置落盘**——`self_consistency_n` 字段已加（默认 1，随 A4）；待做：接 `--repeat` 实际写入 + concurrency 字段
- [ ] **C8. prompt 变体 A-B 对比轴**（同模型改 prompt 前后）——代码已支持，跑两遍喂对比器即可，无需改代码
- [ ] **C9. 评估器/per-case 置信度**——LLM judge 可附带。半天，价值有限（我方主打确定性层）
- [ ] **C10. 指令复杂度量化分 complexity_score(0-100)**——竞品 `instruction_compiler.py:37-96`；我方场景只有自由文本 difficulty。补半天

---

## D. 我方反而有、竞品没有的（路演反击弹药 · 不用补，要会讲）

1. **不可篡改工具账本 + SHA256 哈希链**（`models.py:191-315`）——分数只认账本不认 Agent 自报；竞品平铺存对话，无防伪/回滚/真工具执行 ⭐ 最硬差异点
2. **真工具执行 + 数据库终态校验**（`models.py:330-336`）——验"Agent 是否真改了系统"；竞品纯对话 + LLM 打分
3. **消融实验 10 配置 × 111 trace**（`calibration/ablation_report.json`）——"只用 LLM 打分虚高到 88.8，完整系统压回 37.2"（+51.6pp）；竞品**完全没有**
4. **配对重复实验**（`calibration/paired_experiment_report.json`）——量化评测自身稳定性（某场景 std 16.6）；竞品全单次跑，std 全 0
5. **7 个真实异构模型横评**（claude-sonnet/haiku、MiniMax、LongCat、mimo、kimi）；竞品全程 1 个 deepseek 同时当被测/客户/裁判，多模型还停在 README 计划
6. **策略图 DP 路径对齐 + 时序因果验证**（`models_outbound.py:442-465`）；竞品只有模糊分支覆盖字符串匹配
7. **因果失败链 + 反事实修复增益**（`causal_diagnosis.py:42-191`）；竞品只有修复建议文本
8. **得分原子审计链 + 不变量校验**（`scorer_modules/types.py`）——每个得分点反向指向账本事件；竞品证据是文字引用
9. **一键复现脚本** `reproduce_claims.py`（无 API/无联网）；竞品只在文档文字说"设相同 seed 可复现"
10. **模拟器质量自检 + 32 条逐维度长篇标注 + 分层校准**（88% 确定性层、可复现率 1.0）；竞品仅 8 case 短列表 + 手编对话

---

## E. 总判断

> 竞品真正领先核心只有两项：① **知识幻觉检测**（我方真缺的评分维度）；② **跨会话聚合成品**（失败排行 + 主报告 + PDF/HTML 离线分发）。
> 其余十几条全是表面差异——底层数据我方已具备，只差聚合/落盘/换皮工序。
> 反过来我方在"**证据可信度**"和"**评测科学性**"两大块对竞品形成代差碾压，这恰是评委判断"评测系统是否可信/黑箱"的核心。

**执行顺序**：A4（复算元数据，含 B5/C7，半天）→ A1+A2（知识幻觉，3-5天）→ A3+A5（失败聚合，1-2天）→ B1+B2+B3（聚合报告+热力图+导出）→ 其余按路演需要。

**🔄 进度复核（2026-06-06）**：A1 ✅ / A3 部分 / A4 ✅ / B1 ✅ / B3 ✅(HTML+MD) / B5 部分 / B6 部分。**剩余真缺口按 ROI 排序**：① A2 给 32 个场景补结构化知识点（否则 A1 评分器大面积空跑，这是当前最该补的）→ ② B2 画像×维度热力图（半天，视觉补强）→ ③ A5+B7 对比报告短板诊断并排（路演讲多模型时才需要）→ ④ B4/B8 低 ROI，路演有余力再说。
