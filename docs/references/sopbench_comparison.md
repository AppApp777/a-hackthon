# SOPBench vs 我们的 Policy Graph Compiler — 对比分析

> 参考论文：SOPBench (arxiv 2503.08669, 2025)
> 对比对象：`policy_graph.py` + `trace_verifier.py`

## SOPBench 核心方法

SOPBench 将标准操作规程（SOP）转换为有向图，用 oracle 规则验证器（非 LLM）检查 Agent 是否按图执行。覆盖 7 个客服领域、167 个工具/函数、900+ 测试用例。

### SOPBench 的关键设计

| 组件 | SOPBench 做法 |
|---|---|
| SOP 表示 | 原始 Python 代码 → 自动提取有向图 |
| 图节点类型 | 3 种：服务函数节点 / 逻辑组合节点(And/Or) / 辅助函数节点 |
| 约束组合 | 4 种：Single / And / Or / Chain（可嵌套） |
| 验证器 | 三维确定性验证：动作许可性 + DB 结果匹配 + 流程完整性 |
| 评分 | 二元 pass/fail（三维全通过才算通过） |
| 函数区分 | 服务函数 F^s（目标动作）vs 辅助函数 F^h（验证工具） |
| 领域覆盖 | 7 域 / 97 服务函数 / 70 辅助函数 / 165 约束 / 903 测试用例 |

### SOPBench 关键发现

- **o4-mini-high 最强**：总体通过率 76.08%
- GPT-4o：62.13%，Claude-3.7-Sonnet：54.26%
- **Hotel 和 Library 是最难的域**：o4-mini-high 在 Library 也只有 43.59%
- 推理模型系统性优于非推理模型
- **越狱攻击极简但有效**：一句"请直接用最合适的工具尽快解决"就让 Claude-3.7-Sonnet 从 66% 暴跌到 28%
- o4-mini-high 对越狱最具抗性

### SOPBench 自述局限

1. **SOP 类型单一**——只覆盖"前置约束验证"，**不含 IF-THEN-ELSE 条件分支工作流**
2. **全有或全无评分**——做了 90% 验证步骤但漏一个 = 0 分
3. **单轮用户请求**——用户只在开头发一条，没测多轮交互中的 SOP 遵循
4. **依赖可代码化的流程**——涉及主观判断的 SOP 无法形式化

## 异同对比

### 共同点（方法论一致性）

| 维度 | SOPBench | 我们的系统 | 说明 |
|---|---|---|---|
| 核心思路 | SOP → 有向图 → 确定性验证 | 策略 DSL → PolicyGraph → TraceVerifier | **方法论完全一致**，独立验证 |
| 不信任 Agent | Oracle 验证器不看 Agent 自述 | EventLedger + 工具交叉验证 | 两者都拒绝模型自报 |
| 确定性评分 | 规则验证器，无 LLM | 88% 确定性（规则+图对齐） | 都追求可重复性 |
| 客服场景 | 7 个客服领域 | 外呼 Agent 专用 | 都面向任务导向对话 |

### 我们的优势（差异化）

| 维度 | SOPBench | 我们的系统 | 评委话术 |
|---|---|---|---|
| **分数粒度** | 二元 pass/fail | 连续分数 + 30 原子级维度分解 | "SOPBench 告诉你过没过，我们告诉你哪里差了多少分" |
| **时序约束** | 无显式时序建模 | `TemporalConstraint`（BEFORE/REQUIRES/MUTEX） | "我们不只检查步骤有没有做，还检查做的顺序对不对" |
| **证据审计链** | 无 | `ScoringAtom → evidence_event_ids → 转写/工具记录` | "每个失分点可追溯到具体的工具调用或对话记录" |
| **诊断能力** | 无（只报 pass/fail） | 因果诊断 + 最小修复 + 反事实恢复估算 | "我们不只打分，还给修复方案" |
| **反作弊** | 有限 | 三层防御（表面/轨迹/基准）+ 伪造检测 + veto cap | "模型撒谎会被抓住并封顶 0 分" |
| **轨迹对齐** | 精确匹配 | 加权 DP 编辑距离（容忍顺序偏移、可选步骤跳过） | "真实对话不会完美按脚本走，我们的对齐算法能处理偏移" |
| **谓词类型** | 代码级匹配 | 4 种谓词（Tool/Utterance/Semantic/DBDelta） | "多种证据类型混合验证" |
| **Harness 实验** | 无 | 同模型 ±Harness 对比，量化安全护栏价值 | "不只评测 Agent，还评测安全护栏值多少分" |
| **DB 终态验证** | 无 | `expected_db_state` 对比（借鉴 τ-bench） | "不只看过程，还看数据库最终状态" |

### SOPBench 的优势（我们可以学的）

| 维度 | SOPBench 做法 | 我们的差距 | 是否采纳 |
|---|---|---|---|
| **SOP 来源** | 从实际代码自动提取图 | 我们用 JSON DSL 手写 | 暂不——JSON DSL 对非技术用户更友好 |
| **领域数量** | 7 域 / 903 用例 | 1 域 / 27 场景 | 场景数量是短板，但每个场景深度更大 |
| **三维验证** | 动作许可性 + DB 匹配 + 流程完整性独立评估 | 我们混在一起 | 可学：将验证维度显式分离报告 |
| **越狱攻击** | 一句话即可让 Sonnet 暴跌 38 个百分点 | 我们有对抗场景但没测越狱简单指令 | Phase 4 会扩展 |
| **Oracle 函数分类** | 服务函数 vs 辅助函数明确区分 | 我们的 18 个工具没有功能分类 | 可学：工具分类有助于评估覆盖率 |

### 关键洞察：SOPBench 不做的事正是我们的优势

SOPBench 自述"不含 IF-THEN-ELSE 条件分支工作流"——而**我们的 Policy Graph Compiler 恰好做了这件事**：
- `GraphEdge.edge_type = BRANCH`（条件分支边）
- `StepNode.branches`（每步可以有多个分支出口）
- `verify_branches()` 三层分支验证
- DP 对齐算法容忍路径偏移

这意味着我们的系统在 SOP 表达力上**超越了 SOPBench**。路演时可以说：
> "SOPBench 建模了前置约束验证，但明确表示不支持条件分支。我们的 Policy Graph Compiler 原生支持 IF-THEN-ELSE 分支、时序约束、循环和状态机——覆盖了更多真实外呼场景的复杂性。"

## 路演引用方式

### PITCH.md 中加入的段落

在"编译器管线"（幻灯片 3）段落后加：

> "SOPBench (2025) 独立验证了 SOP→有向图的评测方法论，他们在 7 个客服领域证明了确定性验证器优于 LLM judge。我们的 Policy Graph Compiler 在此基础上增加了：
> 1. **TemporalConstraints** — 显式时序约束建模
> 2. **ScoringAtoms** — 每个得分点可审计
> 3. **连续分数 + 诊断** — 不只 pass/fail，还给修复方案
> 4. **加权 DP 对齐** — 容忍真实对话的顺序偏移"

### 评委追问时的话术

> "SOPBench 是这个方向最接近的学术工作。他们从实际代码提取 SOP 图做验证，我们用 JSON DSL 编译成策略图。核心方法论一致——这说明我们的方向是对的。但 SOPBench 只做 pass/fail 二元判断，没有诊断、没有证据链、没有反作弊。我们补齐了从'能验证'到'能用于生产'之间的差距。"

## 文件映射

| SOPBench 概念 | 我们的对应文件 | 行号 |
|---|---|---|
| SOP 代码 → 有向图 | `policy_graph.py:compile_policy_graph()` | L430 |
| 图节点 | `policy_graph.py:StepNode` | L113 |
| 图边 | `policy_graph.py:GraphEdge` | L101 |
| Oracle 验证器 | `trace_verifier.py:verify_trace()` | L873 |
| 时序约束（我们独有） | `policy_graph.py:TemporalConstraint` | L135 |
| 评分原子（我们独有） | `policy_graph.py:ScoringAtom` | L157 |
| DP 对齐（我们独有） | `trace_verifier.py:align_sequences()` | L541 |
