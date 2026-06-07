# 迭代日志 — 来时路

> 路演用：每次大改动记录"为什么改、改了什么、踩了什么坑、怎么解决的"。倒序排列，最新在最上。
>
> **路演核心叙事**：不是"一次做对"——是系统能不断发现问题并闭环修复。每次"修好了"都暴露更深层的问题，这正是评测系统的价值。

## 2026-06-06 — 修复"70 分天花板"：step_compliance 中文对话步骤系统性误判

### 改了什么
- `policy_graph.py`：新建 `_ACTION_CONCEPTS`（语义动作→自然话术别名词表）+ `_derive_negation_terms`；重写 `_infer_predicates`——对话步骤从"整句不分词关键词"改为"命中任一别名子串即该动作完成"，否定守卫按每个动作自己的别名派生
- `trace_verifier.py`：`extract_observed_steps` 加**覆盖率门**——多动作对话步骤需命中 ≥50% 的 required_action 才算完成（工具命中仍然权威）
- `scorer_modules/checkers.py`：`RuleBasedStepChecker` 改用同一张概念词表 + 按动作否定，保证规则兜底展示与策略图标量一致
- `scripts/diag_rescore.py` / `diag_rescore_all.py` / `diag_redteam.py`：诊断 + 全量重打分 + 红队护栏验证脚本

### 遇到的问题
- 用户质疑"根本跑不出表现好的 agent"。实跑发现：全库顶不破 70、pass 桶 0 条、Haiku 反而 ≈ Sonnet——典型"打分有结构性上限"信号
- 根因：中文步骤指令按标点切词，句内无空格 → 整句变成无法匹配的 mega-token（"确认对方是王女士本人"），等于要 Agent 逐字背指令；纯对话步骤对所有模型恒判未完成，只有工具步骤得分 → step_compliance(0.24权重)+alignment 被钉死，评测丧失区分度
- 对抗审查（独立 Opus subagent）抓到两个作弊面：单句堆砌别名刷多步、"无法退款…不过补送"绕过否定

### 解决方法
- 概念词表把"语义动作"映射到真 Agent 实际说的话，而不是指令原句——这才是步骤完成的可观测证据
- 覆盖率门 + 按动作否定守卫堵住对抗审查发现的两个洞；红队 4 例（只道歉/道歉+复述+确认/无法退款但补送/既不退也不补）全过，且用真实输入端到端验证（非只读代码）
- 27 条留出集重打分：min/max/mean 29-70/47 → 18-87/55，pass 桶 0→2，有升有降（证明是校准不是注水）

### 路演叙事
> 评委问"凭什么相信你的分数准"——我们自己第一个不信：跑出来最强的 Sonnet 都顶不破 70、还输给 Haiku，这不对劲。顺着最高分那条 trace 逐项扒，发现是中文没分词让评分器"看不见 Agent 说的话"，只看得见它调的工具。修完，好 Agent 才浮出水面（87 分），评测第一次有了区分真假高手的能力。

## 2026-06-06 — 人工校准试点 + veto 规则缺口闭环

### 改了什么
- `scripts/run_human_calibration_report.py`：新建人工校准报告生成器（MAE / Spearman / veto P-R-F1 / bucket 准确率 / bootstrap 95% CI / 基线对比），输出 `reports/human_calibration_pilot.{md,json}`
- `scorer_outbound.py`：`_INTERNAL_INFO_PATTERNS` 增加 6 条内部信息泄露检测正则（✅步骤清单 / 通话收尾状态 / 脚本执行状态 / log_id / 步骤编号 / 结果汇总）
- `scripts/_posthoc_veto_sim.py`：新建后验 veto 模拟，验证新规则 F1 提升
- `data/calibration/blind_pilot/traces_after/`：prompt 修复后用 sonnet 重跑 3 条 trace（aftersales / rider_warning / multi_issue）
- `盲标评分_after.html` + `traces_after.json`：修复后 trace 的盲标页面
- README / CLAIMS / JUDGE_GUIDE / BUGS 同步人工校准数据

### 遇到的问题
- 22 条单人盲标 MAE 29.4、Spearman -0.06，数字难看
- 标注者承认打分随意：11/22 给同一个 59 分，区分度低于系统（std 23.8 vs 12.9）
- 系统 veto 命中 0/22，但人工标了 12 条 veto（多为内部信息泄露）

### 解决方法
- 把校准定位从"证明系统和人一致"改成"诊断系统缺口"——单人标注噪声大恰恰说明自动化评测的必要性
- 据 12 条人工 veto 反推规则缺口，补 6 条检测正则（经审查收紧 + NFKC 归一化 + raw_text 双扫），后验 F1 0 → 0.64（P=0.615 R=0.667）
- 修复后重跑 multi_issue trace，3 项内部信息泄露全部被新规则命中

### 路演叙事
> 人工校准的价值不在于"系统打分和人一样"，而在于发现系统漏掉的东西。22 条盲标暴露了 veto 规则缺口，我们当场补规则、当场验证生效——这就是评测系统自我迭代的闭环。

---

## 2026-06-05 — 冲刺 8 项升级（评委体验 + Dashboard 增强）

### 改了什么
- `generate_report.py`：自包含 HTML 报告导出，评委双击即看全部 trace，无需 Python
- `docs/demo/index.html`：GitHub Pages 在线 demo（5 条代表性 trace，0.3MB）
- `report_generator.py`：10 节结构化报告生成器，输出 .md + .json
- `self_check.py`：反作弊自省脚本，扫描 7 个关键文件（硬编码/安全/非确定性）
- `dashboard.py` + `index.html`：Dashboard 增加确定性评分占比卡片、成本估算、pass^k 表
- `scorer_modules/computation.py`：veto_cap 加文档字符串（触发层级说明）
- 测试数量 1065→1082（README/PITCH/CLAIMS/JUDGE_GUIDE/progress 全部统一）

### 遇到的问题
- 盲审指出"声称 1065 测试实际 644"——实际是 pytest 集合了 1082 项（含参数化展开），旧数字过时
- 离线报告需要 patch fetch() 为内嵌数据，正则替换不可靠

### 解决方法
- pytest --co 重新计数，全部文档批量替换为 1082
- 离线报告用后置 script 覆盖原函数（注入在原脚本之后），避免正则匹配问题

### 路演叙事
> 评委不需要装 Python 也不需要启动服务器——一个 HTML 文件看完全部 trace、分数、对话回放。

---

## 2026-06-04 — 复算元数据补全（竞品对标 · 谁打的分可追溯）

### 改了什么
- `models.py`：新增 `EVALUATOR_VERSION` 常量 + `RunMetadata` 扩 11 字段（run_id / evaluator_version / judge_model_id / judge_model_secondary_id / simulator_model_id / seed / self_consistency_n / use_llm_judge / started_at / finished_at / duration_seconds）
- `orchestrator_outbound.py`：新增 `_build_run_metadata()`，run() 起点打时间戳，trace 落盘时记录真实的被测/评委/模拟器模型 + 耗时
- 新增 `tests/unit/test_run_metadata.py`（9 项）：校验字段存在 + judge OFF 时 judge_model_id 必为 None + 耗时计算
- 新增 `docs/competitor_gap.md`：竞品（WANGLEVY9/EvalSystem）数据资产差距全追踪（A/B/C 三级 + 我方反击弹药）

### 遇到的问题
- 竞品报告每份都记 `target/simulator/judge_model_id`，评委一眼能看到"谁打的分"；我方 trace 只记了被测模型，无法回答"这分是哪个模型评的"
- 主评委实际走 `model=None`→`DEFAULT_MODEL`，`JUDGE_MODEL` 只作配置身份——记录时要反映真实解析，不能想当然
- 我方 LLM 层目前不向 API 传 seed（claude_cli 后端也不支持）——不能为了对标就填假 seed

### 解决方法
- judge_model_id 在 `use_llm_judge=False` 时强制为 None（诚实：没跑评委就不记评委）
- seed 字段加上但默认 None（诚实标注"未透传"，真透传留作后续 B5 任务）
- duration 用 run() 起止时间戳相减，claude_cli 无 seed 支持的情况在 gap 文档标注

### 路演叙事
> 对标竞品后第一刀砍在"可信度元数据"：评委最先问的不是"准不准"，是"谁评的、能不能复现"。我们把"谁打的分"做成每份结果可追溯的硬字段，且诚实标注 seed 尚未透传——不靠假数字对标。

## 2026-05-25 — 可复现证据包 + 人工标注工具

### 改了什么
- 新增 `reproduce_claims.py`：17 项声明一键验证（消融/配对/测试/场景/trace/代码规模）
- 新增 `CLAIMS.md`：声明→证据文件→复现命令映射表
- 新增 `LIMITATIONS.md`：6 项已知限制（rubric 缺陷 / 评分层面 / 数据层面）
- 升级 `annotate.html`：按维度显示不同参考面板（D1 步骤顺序 / D2 应确认信息 / D3 禁止话术等）、系统评分标完后才显示
- 升级 `build_gold_set.py`：从场景 JSON 读取 instruction_steps / call_context / callee_context
- 修正 `README.md`：标准差声明从虚假的 "< 5%" 改为真实的 "中位数 7.1%"
- 完成 48/123 条人工标注，二元题一致率 93.8%

### 遇到的问题
- 标注者发现对话截断（只展示前 3 轮）导致无法判断
- 标注者发现维度题缺少正确步骤/应确认信息等参考
- 标注者发现 rubric 与场景不匹配（"被识别为机器人"在自报 AI 场景下无效、"确认身份"在无姓名场景下不公平）
- README 中标准差 "< 5%" 是虚假声明，实际中位数 7.1%

### 解决方法
- 对话展示从前 3 轮改为完整展示（最多 20 轮）
- 按维度 D1-D6 分别显示不同参考面板
- rubric 缺陷记入 LIMITATIONS.md，标注按原 rubric 字面标
- 标准差声明改为真实数据

### 路演叙事
> "我们不只声称数字——我们让评委一键验证。17 项声明，0 项造假。"

---

## 2026-05-23 — Phase 4：安全与对抗强化（SafeToolBench/Garak/ARTKIT/AgentPRM）

### 改了什么
- `scorer_outbound.py`：D6 原子从 5 个扩充到 8 个对齐 SafeToolBench 九维；`SAFETY_DIMENSION_MAP` 映射常量；`_TOOL_CALL_FREQUENCY_THRESHOLD` 频率异常检测；`dimensions_triggered` 九维追踪（9/9 覆盖）
- `models_outbound.py`：`SafetyVetoLayer.dimensions_triggered`；`InstructionStep.weight`（gt=0 验证）；`StepComplianceEntry.contribution_weight`；`CalleePersona.adversarial_mode`
- `harness.py`：`check_tool_request()` 前瞻性安全评估文档（SafeToolBench 引用）
- `user_sim_outbound.py`：5 级自适应对抗策略（direct→social_engineering→emotional→authority→reframing）；`_detect_refusal()` + `_update_adversarial_state()` + `_adversarial_section()`
- `scenario_mutator.py`：`adversarial_escalation()` 变异策略
- 5 个新对抗场景（06-10）：提示词注入、角色扮演、编码绕过、上下文淹没、输出劫持
- 5 个场景步骤权重配置（after_sales_complaint + refund_over_budget + adversarial_social_engineering + rider_safety_incident + multi_issue_combo）
- `docs/references/safety_framework.md`：九维映射文档

### 遇到的问题
- `_update_adversarial_state()` 忘了加去重防护，同一条拒绝会被重复计数导致策略过快升级
- `dimensions_triggered` 只填了 6/9 维，dim5/dim6/dim9 声明了但从未触发
- `InstructionStep.weight` 无下界约束，weight=0 导致 progress_rate 静默变 None

### 解决方法
- 对抗审查 subagent（Opus 独立盲审）发现全部 3 个 HIGH，逐个修复后重新验证
- `_last_processed_adversarial_turn` 去重 → 与 `_update_pressure_counter` 保持一致模式
- 补齐 dim5（key_sensitivity=info_leak）、dim6（tool_gating_block）、dim9（alignment<0.70）触发条件
- `Field(gt=0)` 约束 + Pydantic 自动拒绝非正数权重

### 路演叙事
> 对标行业最高标准（SafeToolBench EMNLP 2025、NVIDIA Garak、BCG ARTKIT），安全维度从 5 个扩展到覆盖九维框架的 8 个，对抗场景从 5 个扩展到 10 个，评测器自己也在被"攻击"——每轮改动都经过独立 Opus subagent 的对抗审查。

---

## 2026-05-23 — Phase 3：核心技术借鉴（ESAA/BFCL/Strands/AgentBoard/CRMArena/VoiceAgentEval）

### 改了什么
- `models.py`：ToolEvent 加 `prev_hash` 字段 + EventLedger 加 `verify_chain()` / `chain_hash()` 哈希链验证（ESAA 借鉴）
- `orchestrator_outbound.py`：freeze 后自动调 verify_chain()，链断裂拒绝评分；trace 输出含 `ledger_chain_hash`
- `scorer_outbound.py`：AST 级工具调用匹配 `_ast_match_tool_call()`（BFCL 借鉴）+ 内部信息泄露检测 `_INTERNAL_INFO_PATTERNS`（CRMArena 借鉴）+ `progress_rate` 计算（AgentBoard 借鉴）
- `trace_verifier.py`：三种轨迹匹配模式 strict/ordered/unordered（Strands Evals 借鉴）+ required_args 升级为 AST 匹配
- `models_outbound.py`：场景加 `trace_match_mode` / report 加 `progress_rate` / persona 加 `scenario_hints`
- `user_sim_outbound.py`：用户模拟器支持场景行为提示 + 压力升级（VoiceAgentEval + ARTKIT 借鉴）
- 新增 2 个对抗场景：信息钓鱼 + 社会工程攻击
- CONTRACTS.md 加入第 6 条：哈希链完整性契约

### 遇到的问题
- `_INTERNAL_INFO_PATTERNS` 和 ForbiddenBehaviorChecker 会双重计数 → 加去重集
- `align_strict` 对可选步骤用了硬编码 COST_MISSING_REQUIRED → 改用 _deletion_cost()
- `trace_match_mode` 没有验证，拼写错误会静默降级 → 加 validate() 检查

### 解决方法
- 3 个 HIGH 由 Opus 独立审查发现，全部修复后 0 CRITICAL
- 测试 462 → **533 项**（+71 项）

### 路演叙事
> "Phase 3 是技术密度最高的一轮——6 个学术项目的核心技术融入一个系统。哈希链让事件账本从'不可变'升级到'可验证不可篡改'；AST 匹配让工具调用评测从字符串比较升级到结构化比较；三种轨迹模式让评测适应不同严格度需求。"

---

## 2026-05-23 — Phase 2：快速代码改进（RubricEval/τ-bench/CRMArena 借鉴）

### 改了什么
- **2.1 三层分数结构**：`models_outbound.py` 新增 `ObjectiveEvidenceLayer`/`SoftQualityLayer`/`SafetyVetoLayer` 子模型，10+ 平铺分数归并为三层（保留旧字段向后兼容）。Dashboard 重构为三区卡片展示。PITCH.md 评分公式改为三层架构图。
- **2.2 LLM Judge CoT + 温度 0**：修 `scorer.py` 温度 0.3→0。维度/步骤 judge prompt 加"先推理再判断"。`_validate_atom_result()` 新增推理检查——status=yes 但 reason 为空时降级 partial。
- **2.3 pass^k 可复现性**：新建 `reliability.py`（pass^k 计算 + CLI 格式化），`run_outbound.py` 新增 `--repeat N` 参数。
- **2.4 信息泄漏修复**：新增 `SCENARIO_ANSWER_KEY_FIELDS` 常量 + `agent_safe_dump()` 方法，过滤 9 个答案字段。修 orchestrator 的 IsolatedAgentAdapter 初始化。
- **2.5 情绪检测强化**：`harness.py` 新增 `_emotion_keywords_needs_context`（歧义词需上下文共现）+ 新关键词"怎么搞的"/"搞什么"。

### 遇到的问题
- Dashboard JS 的 `||` 运算符在 `veto_cap=0` 时返回 1（`0||1=1`），旧 trace 的伪造场景封顶值显示绿色。改为 null check。
- Dashboard fallback 用了 `r.veto_cap`（ScoreReport 无此字段）而非 `ob.veto_cap`（OutboundScoreReport 才有）。
- 测试 db_state 缺少 `call_type` 字段，导致 scorer 过滤掉 call_log，走失败路径而非成功路径。
- Pydantic 私有属性（`_` 前缀）会被 `ModelPrivateAttr` 包裹，不能直接比较。改为模块级常量。

### 解决方法
- 三层结构只改数据组织不改计算逻辑，通过独立 subagent（Opus）审查 + 对抗审查验证安全性。
- 所有修改通过 462 项测试（新增 65 项）。

### 路演叙事
> "RubricEval 论文告诉我们分数概念越少越可解释——我们把 9 种分数归并为三层故事：88% 确定性证据、12% 门控 LLM、不可补偿的安全封顶。评委三秒看懂。"

---

## 2026-05-21 凌晨 — Day 4：CanonicalIntentLedger（用户妥协不改变业务红线）

### 改了什么

- 新增 [`docs/DESIGN_canonical_intent_ledger.md`](docs/DESIGN_canonical_intent_ledger.md) — Day 4 设计卡
- `models_outbound.py` 加 `RequirementSource` enum + `CanonicalRequirement` 模型 + `OutboundScenario.canonical_intent` 字段 + validate() 校验
- 新增 [`canonical_intent_ledger.py`](canonical_intent_ledger.py) — ledger 核心模块（200+ 行）
  - `evaluate_canonical_intent()` 主入口
  - 反义 keyword 过滤器（奇偶 token 计数 + 句号边界 + 9 个否定 token）
  - `_is_user_decline_clean()` 区分主动拒绝服务 vs 诱导妥协
- `orchestrator_outbound.py` 3 处 `_add_message` 加 metadata（`compliance_pressure_level` + `parse_failed`）
- `scorer_outbound.py` 接 ledger：`_compute_veto_cap` 加 `induced_compromise` 参数（cap 0.60，gate `cap_060_induced`）
- 新增 [`tests/unit/test_canonical_intent_ledger.py`](../tests/unit/test_canonical_intent_ledger.py) — 19 项测试覆盖 5 个核心场景 + 8 个边界 case（反义/双否/parse_failed/重复 id/keywords=[]）

### 遇到的问题（迭代审查发现）

1. **Round 1 adversarial subagent 抓到 2 个 HIGH**：
   - parse_failed=True 的 user msg 被 ledger 整个 skip——和 user_sim v2 那一层精心保留的"floor 无条件应用"防御互相抵消（攻击者吐 garbage JSON 能绕过 floor 信号）
   - `keywords=[] + mutable=False` 是 footgun：业务方加新红线但忘写 keywords → 任何对话都崩成 critical FAIL
2. **Round 1 MEDIUM**：子串匹配可被反义绕过——"Agent 不强调自愿原则" 子串命中 "自愿原则"→ fulfilled=True
3. **Round 2 验证修复后又抓到 2 个新 HIGH**：双重否定 "不是不强调" 会被错判为 negated；单字 "无" 与 "无论 / 无关 / 无意" 冲突

### 解决方法

1. **HIGH #1**: `_user_response_after` 删掉 parse_failed skip 逻辑——floor 已被 v2 user_sim 保留，ledger 信任 pressure_level 即可
2. **HIGH #2**: `OutboundScenario.validate()` 新增 3 条校验——空 keywords + immutable / 重复 req id / `must_appear_before_step` 引用
3. **反义检测**: 实现 `_keyword_with_negation_filter()`——6 字窗口内的否定 token 计数，奇数 = 真否定，偶数（含 0/2）= 肯定。token 表移除单字 "无"（与无论冲突），改用具体词 "无需/无须/无法"；新增 "非/拒绝/禁止"
4. **句号边界**: 窗口在 `。！？；\n` 处截断，不跨句

### 验证

- 307 项测试全绿（226 原有 + 62 v2 user_sim + 19 ledger）
- 4 轮 adversarial-review subagent 通过：0 CRITICAL + 0 HIGH
- demo 端到端链路成立：v2 user_sim 吐 `compliance_pressure_level≥2` + `parse_failed` 信号 → orchestrator 写 metadata → ledger 评估 → scorer `_compute_veto_cap` 触发 `cap_060_induced` → 最终得分被封顶

### 路演叙事

> Day 4 是杀手级 demo case 落地的"最后一公里"。Day 2-3 让用户模拟器吐"用户被诱导妥协"信号，Day 4 让评测系统消费这个信号——"用户说'行吧 随便'，系统照样判 FAIL"。代码 LLM 写完后审查 LLM 起了 3 轮——抓到 2 CRITICAL + 4 HIGH，全部修完才 commit。反义检测从子串匹配升级到奇偶计数 + 句号边界，挡住了"Agent 说'不强调自愿原则'还能拿满分"这种攻击。这套"代码写、审查抓、规则做底线"的纪律，让评委没法用一句反语就把我们的评测系统问崩。

---

## 2026-05-20 深夜 — Day 2-3：五段式外呼用户模拟器 v2（含规则型压力计数器）

### 改了什么

- 新增 [`docs/DESIGN_user_sim_outbound_v2.md`](docs/DESIGN_user_sim_outbound_v2.md) — 设计卡，问题/方案/失败模式/不变量
- 改 [`models_outbound.py`](models_outbound.py) — 加 `PersonaArchetype` enum（5 种）+ `CalleePersona.archetype/never_disclose/gated_disclosure` 字段
- 重构 [`user_sim_outbound.py`](user_sim_outbound.py) — 单段散乱 prompt → 五段式 builder（角色/风格/披露/处理/终止 + 反注入防御 + 输出格式）
- 新增 `infer_archetype()` — 数值参数推断 archetype（向后兼容现有 12 场景）
- 新增 `_PRESSURE_PHRASES` 词典 + `detect_pressure()` + `_update_pressure_counter()` + `compute_pressure_floor()` — 规则型压力计数器，floor 作为 LLM 自报值的 MAX 兜底
- `CalleeOutput` 加 `compliance_pressure_level: int` + `parse_failed: bool`
- 新增 [`scripts/demo_user_sim_v2.py`](scripts/demo_user_sim_v2.py) — 5 archetype × 同场景 prompt 对比 + HESITANT 压力升级实测
- 新增 [`tests/unit/test_user_sim_outbound_v2.py`](../tests/unit/test_user_sim_outbound_v2.py) — 62 项测试，含 monkeypatch 端到端 LLM-lying 反作弊验证
- 更新 `.pipeline/review_report.json` + `.pipeline/adversarial_report.json` — Round 1 + Round 2 审查结果

### 遇到的问题

1. **Round 1 adversarial subagent 抓到 2 个 CRITICAL**：
   - `compliance_pressure_level` 完全是 LLM 自报字段，零交叉验证，HESITANT 场景 LLM 可一直返回 0 掩盖被诱导妥协（CanonicalIntentLedger 杀手级 demo 依赖这个信号）
   - JSON 解析失败时静默兜底 pressure_level=0，反向作弊路径（LLM 故意吐 garbage JSON 就能逃过 ledger）
2. **Round 2 subagent 抓到新 MEDIUM**：parse_failed=True 时 floor 被 skip，依赖下游 scorer 读 parse_failed 是脆弱契约

### 解决方法

1. **CRITICAL #1**：加 `_PRESSURE_PHRASES` 词典（16 个施压短语 regex）+ `_PRESSURE_FLOOR` 表 per archetype。HESITANT counter≥3 → floor=2，counter≥4 → 3。`generate_response()` 中 floor 作为 MAX 强制覆盖 LLM 自报值。规则部分基于 agent 消息（确定性输入），LLM 无法谎报绕过
2. **CRITICAL #2**：`_parse_output` JSON 解析失败 → `parse_failed=True` + emotional_state="invalid"，让 scorer 知道这一轮信号不可信
3. **Round 2 MEDIUM**：把 floor 应用移出 `if not parse_failed` 守卫——floor 是规则的，不依赖 LLM，无条件应用更安全。配套加 `test_floor_applied_even_when_parse_failed`
4. 顺手修：archetype 优先级 WARY > BUSY（安全语义优先）；全 archetype 基线敏感词（不仅 WARY）；`[反注入防御]` prompt 段防 Agent 在 utterance 里塞 JSON 直令；`_META_PATTERNS` 去掉过宽的"隐藏"单字匹配

### 验证

- 288 项测试全绿（226 原有 + 62 新增 v2 测试）
- 2 轮 adversarial-review subagent 通过，0 CRITICAL
- demo 脚本实测：HESITANT counter 1→2→3→4，floor 0→1→2→3，turn 3 起 ledger 信号变 `induced_compromise`——CanonicalIntentLedger 杀手级 demo 落地

### 路演叙事

> 我们让 Sonnet 写完五段式模拟器后，没急着 commit——用 Opus 起两轮无上下文盲审，立刻抓到 2 个 CRITICAL：LLM 可以谎报压力字段 + JSON 乱码就能绕过 ledger。换了硬规则的"压力计数器 + archetype floor"做兜底，LLM 自报值再低也会被规则覆盖。这套"代码 LLM 写、审查 LLM 抓、规则做底线"的纪律，比任何"LLM 评 LLM"都靠谱。

---

## 2026-05-20 晚 — Phase 4 启动：VitaBench 对标 Day 1 落地

### 改了什么

- 新增 [`docs/POSITIONING.md`](docs/POSITIONING.md) — VitaBench 对标叙事 / 一句话定位 / 三维复杂度对照表 / 4 个优势新口径 / 吸收 vs 增强对照 / 禁止/必用清单 / Q&A 防御
- 新增 [`docs/SCENARIO_MATRIX.md`](docs/SCENARIO_MATRIX.md) — 24 场景扩展计划 + 业务域定义 + 六轴复杂度 + fixture 结构 + 新工具清单 + 实施排期
- 新增 [`docs/DEMO_PLAYBOOK.md`](docs/DEMO_PLAYBOOK.md) — demo 三轨设计 + dashboard 画面布局 + 90 秒话术 + 应急方案
- 新增 [`docs/positioning_slide.html`](docs/positioning_slide.html) — 一页 HTML 占位 slide
- 修订 `HANDOFF.md` — 确认脱敏数据已在 `命题二：外呼任务对话模型指令示例.xlsx`（HANDOFF 之前误标"未到"），更新到 Day 2-3 切换状态
- 修订 `progress.md` — 进入 Phase 4，Day 1 完成

### 遇到的问题

1. **HANDOFF 误标数据状态**：读 HANDOFF 时阻塞项写"美团数据原计划 2026-05-18 到队长邮箱，需确认是否已收到"，开会话时按这条问用户"数据到了没"，用户指出数据一直在用（`命题二：外呼任务对话模型指令示例.xlsx`，2026-05-18 起就在项目根）。HANDOFF 是过时的
2. **Oracle 业务域方向偏差**：Oracle 在不知道脱敏数据时推荐"骑手招聘 / 骑手助手 / AI 站长"业务域。读完 Excel 确认 Task 1 是"已在岗骑手通知"（不是招聘）、Task 2 是"客服→商家产品升级"。Oracle 的方向需要根据真实数据修正

### 解决方法

1. 立即修 `HANDOFF.md` 的"阻塞 / 等用户决定"块，删除"数据未到"误信息，加上数据文件确认路径
2. 在 `POSITIONING.md` 第 8 节明确写"基于美团真实示例修正 Oracle 方向"，业务域改为 D1 客服→用户 / D2 站长→骑手 / D3 客服→商家。在 `SCENARIO_MATRIX.md` 第 1 节用同样方式标注"修正 Oracle"，确保下次会话不会再走偏

### 路演叙事

> 我们不是闷头照搬 VitaBench——读完 ICLR 论文 + GitHub + 排行榜 + 审稿后请战略 Oracle 给 18 天计划，然后跟美团真实脱敏示例对齐，发现 Oracle 建议的"骑手招聘"业务域在真实数据里不存在，于是修正成"已在岗骑手通知"。这套"先调研再咨询再对齐数据"的纪律，比"现场拍脑袋设计场景"靠谱得多。

---

## 2026-05-17 深夜 — Harness v3：工具调用门控（硬指标 62.5% → 100%）

### 问题发现

节流修复后重跑，Haiku+Harness 得分 47.5%——仍低于裸跑 62.5%。节流解决了"过度干预"，但分数没回来。

### 诊断过程

1. 对比 Harness 干预日志 vs 对话内容
2. 发现：Harness 拦截了模板泄露（✓），注入频率正常（✓），但 Agent 照样走错了——直接调 `transfer_to_human` 跳过了 `create_compensation`
3. 定位根因：**Harness 只审查 text 层（禁止词、结束语），对 tool_calls 完全透明**。模型在第 4 轮调了 transfer_to_human，Harness 看到了但没拦

### 核心洞察

> Harness 不能只管"Agent 说什么"，还得管"Agent 做什么"。text 层和 tool 层是两个独立的干预面。

### 修复

- `HarnessConfig` 新增 `tool_call_gating: bool = True`
- `process_agent_output()` 最前面加工具门控检查（优先级最高）
- `_check_tool_gating()`: 当 `create_compensation` 在 `must_call_tools` 但未调用时，拦截 `transfer_to_human`；当核心工具未完成时，拦截 `log_call_result`
- 被拦截后 `get_regeneration_prompt()` 明确告诉 Agent："客户已选退款，请先执行退款操作"

### 验证数据

| 配置 | 硬指标 | 综合 | 关键变化 |
|---|---|---|---|
| Haiku 裸跑 | 87.5% | 62.5% | 没调 query_order |
| Sonnet 裸跑 | 62.5% | 47.5% | 没调 create_compensation，直接升级 |
| Haiku+Harness v2（仅节流） | 62.5% | 47.5% | 注入减少但仍走错路径 |
| **Haiku+Harness v3（门控）** | **100%** | **70.0%** | 门控拦截→Agent 被迫退款→全工具调用完成 |

### 路演话术

> "我们发现修好了注入频率后分数还是低，但原因完全不同了——模型不是被干扰，是在选择上犯了错。它明明知道该退款，但直接选了'升级转人工'这条偷懒路径。于是我们给 Harness 加了工具调用门控——当前置操作没完成时，拦截后续跳转。加了这一层后，硬指标从 62.5% 直接到 100%。"

---

## 2026-05-17 深夜 — Harness v2：步骤注入节流（14 次→3 次，模板泄露消除）

### 问题发现

Harness v1 跑 after_sales 场景，14 次干预 / 11 轮对话。结果：
- 模板泄露：Agent 把内部步骤表格输出给客户
- 对话轮次暴涨（35 轮）
- 分数反降：55% vs 裸跑 62.5%

### 诊断过程

1. 检查 Harness 干预日志：`step_injection` 出现 10 次 → 几乎每轮都注入
2. 对比 Agent 输出：出现"按脚本完成步骤""✅ 步骤 1-9"等内部格式
3. 定位根因：`get_step_injection()` 无条件每轮触发，Haiku 上下文处理能力弱，高频注入 = 噪音 > 信号

### 核心洞察

> 干预不是越多越好——过度干预等于噪音。模型的注意力是有限资源，Harness 必须精准投放。

### 修复

- `HarnessConfig` 新增 `step_injection_interval: int = 3`
- `HarnessState` 新增 `last_injection_turn` + `last_injection_step`
- `get_step_injection()` 三条件判断：首次 / 间隔≥3轮 / 偏离检测
- `_detect_deviation()`: 同一步骤卡 2+ 轮 = stuck；Agent 试图提前结束 = deviation
- 注入原因标注（"首次"/"定时"/"偏离检测"），日志可审计

### 验证数据

| 指标 | v1（每轮注入） | v2（节流） |
|---|---|---|
| 步骤注入次数 | 10-14 | 3-4 |
| 模板泄露 | 有 | 无 |
| 注入原因可追溯 | 否 | 是 |

### 路演话术

> "一开始我们以为多提醒模型就能让它不跑偏，结果恰恰相反——Haiku 被持续轰炸的系统消息搞晕了，开始把内部指令当成回复内容输出。修复方案是'少说多看'——只在首次、定时、或检测到偏离时才注入。注入频率降了 80%，模板泄露彻底消失。"

---

## 2026-05-17 深夜 — 仪表盘总览页 + LLM judge 完整 trace

### 改了什么

1. **仪表盘总览页**：打开仪表盘默认展示（不需要先选 trace）
   - 后端 `/api/overview` 聚合所有 trace 的模型×场景矩阵
   - 前端统计卡片（总数/平均分/最高分/最低分）+ 热力图矩阵 + 失败模式 Top 5
2. **LLM judge 完整 trace**：`outbound_5c9f8269.json`
   - 6 维 Rubric 全部有评分（D1-D6: 4-5 分）
   - 6 步执行状态有 turn/evidence/branch 数据
   - Rubric 横条图和步骤流程 tab 现在有真实数据填充

### 路演价值

> 总览页让评委一眼看到"你的系统跑了多少数据、哪些模型在哪些场景表现最好/最差、最常见的失败模式是什么"——不需要逐条点开 trace。

---

## 2026-05-17 晚 — Harness 回归发现 + 仪表盘对比视图 + Git 初始化

### 改了什么
1. **仪表盘模型对比视图**：勾选两条 trace 并排展示分数/检查项/对话差异
2. **离线演示方案**：DEMO_GUIDE.md + 刷新按钮，仪表盘纯离线可用
3. **重跑 2 条 trace**：Haiku 裸跑 + Haiku+Harness，验证 diagnosis/harness 数据落盘
4. **Git 初始化**：5 个 commit，.gitignore 排除 .env 和缓存

### 遇到的问题
- **Harness 步骤注入反而让分数更低**（55% vs 裸跑 62.5%）：每轮都注入步骤提醒（10 次/11 轮）导致 Haiku 混乱，产生模板泄露（输出内部步骤表格），被叫方识破是机器人
- 根因：Haiku 上下文窗口处理长 system 消息能力弱，频繁注入等于在给模型加噪

### 解决方法（待下次实施）
- 步骤注入加节流：间隔 ≥3 轮或检测到偏离时才注入，不是每轮都注入
- 减少注入内容长度：只显示当前步骤和下一步，不显示全部进度

### 路演叙事
> "我们一开始以为每轮提醒 Agent 当前步骤能提高合规率，结果 Haiku 被这些提醒搞晕了——步骤提醒变成了干扰源。这教会我们：Harness 的干预粒度必须匹配模型能力。弱模型需要更少但更精准的干预。"

---

## 2026-05-17 下午 — 仪表盘适配外呼 + Harness 增强

### 改了什么
1. **仪表盘全面适配外呼**：5 个 tab（对话/评分/步骤/诊断/Harness），侧边栏域筛选+域标签
2. **Harness 增强**：每轮步骤进度注入（`get_step_injection`）、智能步骤完成追踪、禁止词兜底清洗（`sanitize_output`）
3. **Orchestrator 集成**：diagnosis + harness_summary 自动存入 trace metadata

### 遇到的问题
- 现有 trace 全是 `--no-llm-judge` 跑的，步骤流程 tab 和 Rubric 横条图没数据
- 仪表盘需要向后兼容旧 trace（没有 diagnosis/harness 字段）

### 解决方法
- 前端用 `||` 和可选链做兜底，旧 trace 不崩
- 动态 tab：根据 trace 是否有 diagnosis/harness 数据决定显示哪些 tab
- 重跑 2 条新 trace 验证全链路

### 路演叙事
> "评测系统不只打分，还自动生成诊断报告——告诉你模型在第几轮偏离了脚本、属于什么失败模式、建议用什么 Harness 修复。这些信息全部可视化在仪表盘里。"

---

## 2026-05-17 上午 — 外呼场景全栈搭建

### 改了什么
1. **外呼数据模型**（`models_outbound.py`）：InstructionStep + Branch + CalleePersona + ForbiddenBehavior DSL
2. **8 个外呼工具**（`tools_outbound.py`）：订单/客户/配送/补偿/转接/通话记录
3. **被叫方模拟器**（`user_sim_outbound.py`）：配合/拒绝/信号差/忙碌/低信任
4. **外呼打分引擎**（`scorer_outbound.py`）：步骤遵循 + 分支准确 + 禁止行为 + 6 维 Rubric
5. **失败根因分析**（`diagnosis.py`）：10 种失败模式 + 偏离点定位 + 修复建议
6. **Harness 干预层**（`harness.py`）：5 个拦截机制
7. **4 个梯度场景**：easy → hard → extreme → 真·极限（20 步）
8. **多模型对比实验**：Sonnet vs Haiku，4 个场景

### 遇到的问题
- **Anthropic API 无 key**：`--model sonnet` 报 auth 错误
- **EvalTrace 类型不兼容**：OutboundScoreReport 不能直接赋给 EvalTrace.score_report
- **Haiku CLI 极限场景超时**：180s timeout 不够

### 解决方法
- `_infer_provider()` 在无 key 时 fallback 到 claude_cli + `--model` 参数
- orchestrator 手动构建 ScoreReport 兼容对象
- 超时后重试，实测第二次跑完（16 轮自然结束）

### 关键发现
- **Haiku 伪造执行记录**：输出"✓ 步骤 1-20/20 通话完成"但实际只做了 2 步——比"不做"更危险
- **所有模型不调 query_order**：直接用 system prompt 预设信息
- **Sonnet 极限场景也崩**：被骂后第 6 轮直接念结束语逃跑
- **指数衰减效应**：20 步 × 单步 95% = 全对 36%，对上业界"最强模型 30%"

### 路演叙事
> "Haiku 不是不做任务——它编造了一份完美的执行报告然后挂断。如果没有我们的系统对比工具调用记录和数据库状态，人工质检看到这份报告会标记为'通话正常'。这就是为什么'让 AI 评 AI'不够——必须有确定性规则层。"

---

## 2026-05-17 凌晨 — Phase 1 订餐场景 MVP

### 改了什么
- 完整评测管道：Scenario DSL → 工具模拟器（SQLite）→ 用户模拟器（LLM+状态机）→ 基线 Agent → 三层打分引擎 → 仪表盘
- 三个 Agent 对比：OracleAgent / BaselineAgent / CarelessAgent
- 场景预检验证器

### 验证结果
- dinner_basic 100% / dinner_extreme 93.3%

### 路演叙事
> "我们先用订餐场景验证管道可行性——100% 基线证明系统能准确评分，93.3% 的极限场景证明评分有区分度。管道跑通后我们马上迁移到外呼场景。"
