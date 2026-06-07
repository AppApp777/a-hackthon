# agent-eval — 外呼 Agent 多轮对话评测引擎

> 把业务策略**编译成可执行的验证图**，把对话当**程序运行轨迹**，评分变成 **trace 是否满足图约束**。

## 30 秒了解

```
仅 LLM 评委:    ████████████████████████████████████████████░░ 88.8%  ← 纯文本评委看不到执行状态违规
完整系统:        ███████████████████░░░░░░░░░░░░░░░░░░░░░░░░░ 37.2%  ← 策略图+账本+DB 检出隐藏缺陷
                 ↑ 差 51.6pp（111 条 trace，评分严格度差异，非准确率）
```

一键复现（无需 API key）：
```bash
python reproduce_claims.py
# 预期输出：17/17 PASS，含 LLM-only mean ≈88.8%、full-system mean ≈37.2%
```

## 评委 2 分钟验证路径

```bash
git clone https://github.com/AppApp777/a-hackthon.git && cd a-hackthon
pip install -r agent-eval/requirements.txt

# 1. 验证核心声明（无需 API key，约 10 秒）
python reproduce_claims.py

# 2. 看黄金案例：同一段对话，87 分 vs 0 分
make judge-demo
```

无 API key 即可验证全部数据声明和黄金案例。需要 API key 的只有"重新生成新 trace"。

## 一个具体案例

**场景**：售后外呼 - 漏餐投诉（难度：hard）

```
Agent 对话："王女士您好，我已经帮您查询了订单，确认缺少一份宫保鸡丁。
            我现在为您申请退款 18 元……退款已经提交成功。"

纯文本 LLM 评委打分：95.8 分 ✓ "通话质量优秀，流程完整"

事件账本记录：
  query_order    → ❌ 未找到调用记录
  create_refund  → ❌ 未找到调用记录
  DB 终态        → ❌ 订单状态未变更

agent-eval 判定：0 分（伪造工具调用，安全否决层封顶）

诊断输出：
  偏离点：步骤 3（查询订单）—— Agent 声称已执行但账本无记录
  失败模式：fabrication（工具调用伪造）
  修复建议：确保 Agent 实际调用 query_order 工具而非仅在对话中声称
```

**这说明什么**：纯文本评委只能读对话，无法验证工具是否真的执行。这就是为什么 88% 的评分权重交给了确定性规则。

## 核心能力

- **88% 确定性评分**：基于策略图对齐 + 事件账本 + DB 终态验证，不依赖 LLM
- **33 个原子分解**：每一分都可追溯到具体事件证据
- **因果诊断**：不是"你 58 分"，是"第 5 步走错分支，修复=加资格核查，+18 分"
- **反作弊**：三层防御，Agent 自报"我查了订单"但账本没记录 → 伪造，封顶 0 分

## 护城河有效性审计

[→ 完整审计报告](../docs/effectiveness/CORE_MOAT_EFFECTIVENESS.md) | [JSON 摘要](reports/core_moat_audit_summary.json)

从 111 条冻结 trace 中挑选 20 条（15 违规 + 5 反例），证明系统的三类独有检测能力：

| 检测能力 | 代表案例 | LLM 评分 → 系统评分 |
|---|---|---|
| **工具伪造检测** — 对话声称"已执行"但账本无记录 | `18d2a1cf` 售后外呼 | 95.8 → **0** |
| **DB 状态与承诺对比** — 口头退款但数据库无变更 | `62d5b311` 售后外呼 | 88.0 → **19.4** |
| **步骤完整性检测** — 有工具调用但 9/10 步骤未完成 | `37863b4f` 延迟通知 | 88.0 → **23.2** |

15 条违规 trace 中 LLM 识别率 **0%**（全部给出 88+ 分），平均差距 **69.7 分**。反例组系统区分度是 LLM 的 **27 倍**（n=3，举例说明）。

[随机抽样审计](../docs/effectiveness/RANDOM_SAMPLE_AUDIT.md)：20 条随机 trace（seed=42，无人工筛选），11 条有执行违规，LLM 识别率同样 **0%**——结论与护城河审计一致，非挑选效应。全 111 条中 81 条（73%）存在执行违规，LLM 无一捕获。

## 在线演示

[→ 点击查看在线 Demo](http://101.42.14.246/a-hackthon/)

包含 5 条代表性评测轨迹，涵盖 2 个业务域、3 个模型、3 个分数段。零安装、零配置。

## 关键数据

| 指标 | 数据 |
|---|---|
| 测试覆盖 | **1174 项全绿**（单元 703 / 契约 200 / 对抗 186 / 新增模块 85） |
| 消融实验 | LLM 独评均分 88.8% vs 完整系统均分 37.2%，差 **51.6pp**（111 条 trace） |
| 配对实验 | 10 场景 × 3 次重复，中位数标准差 7.1% |
| 人工校准 | 22 条 trace 单人盲标，MAE 29.4（95% CI [22.4, 36.5]）；诊断出 veto 规则缺口并修复（F1 0 → 0.64） |
| 模型覆盖 | 14 个实测（核心 6 深测：Claude Sonnet 4 / Haiku 4.5 / MiMo-V2.5-Pro / LongCat-2.0 / MiniMax-M2.7 / Claude CLI；横评 8 抽测：GPT-5.5 / GLM-5.1 / GLM-5 / Qwen3.7-Max / DeepSeek-V4-Pro / DeepSeek-V3.2 / Kimi / Sonnet 4.6）+ 12 模型定价追踪 |
| 校准 | GPT-5.5-pro 32 条维度交叉验证（67% ±1 一致），二元判定 κ = 0.868 |
| 场景 | 34 个（easy×4 / medium×6 / hard×14 / extreme×10） |
| 评分维度 | **D1–D7**（含知识准确性 D7，规则 + LLM 双层验证） |
| 成本追踪 | 内置 12 模型定价，按用途分组（user_sim / judge / scorer / diagnosis） |
| 部署 | Docker 一键启动 / GitHub Pages 在线演示 |

## 模块成熟度

| 模块 | 状态 | 证据等级 |
|---|---|---|
| 策略图对齐 | 稳定 | 测试 + 复现 |
| 事件账本 | 稳定 | 哈希链测试 |
| DB 终态验证 | 稳定 | 契约测试 |
| 伪造否决 | 稳定 | 黄金案例 + 审计 |
| PoLL 软质量层 | Beta | 有限示例 |
| 因果失败链 | Beta | 定性示例 |
| 反事实修复估算 | 实验性 | 仅 demo |

分数语义：0-100 分是确定性任务合规诊断分，不是人类偏好分。详见 [`docs/SCORE_SEMANTICS.md`](../docs/SCORE_SEMANTICS.md)。

## 快速开始

```bash
# 安装
cd agent-eval
pip install -r requirements.txt

# 配置（在 .env 中设置 API Key）
cp .env.example .env

# 跑一个场景
python run_outbound.py scenarios/outbound/delivery_confirm_basic.json \
  --model LongCat-2.0-Preview --no-llm-judge

# 跑完整测试套件
cd .. && PYTHONPATH=agent-eval pytest tests/ --tb=short

# 启动可视化仪表盘
cd agent-eval && python dashboard.py  # → http://localhost:8765
```

## Docker 一键部署

```bash
# 方式一：从项目根目录
docker compose up --build
# → 打开 http://localhost:8765

# 方式二：仅评测模块
cd agent-eval && docker compose up --build
```

无需安装 Python 环境。启动后访问 http://localhost:8765 查看仪表盘。容器内评测轨迹通过 volume 挂载持久化到 `traces/` 目录。

运行评测（需要 API Key）：
```bash
docker compose exec agent-eval python run_outbound.py \
  scenarios/outbound/<场景>.json --model <模型>
```

## 与同类系统对比

| 能力维度 | 本系统 | 传统 LLM 评测 | 一般黑客松方案 |
|----------|--------|--------------|--------------|
| 评分确定性 | **88% 规则 + 12% LLM** | 0% 规则 + 100% LLM | 20-40% 规则 |
| 工具执行验证 | **18 工具 + SQLite 状态变更** | 仅文本 | 仅文本 |
| 篡改检测审计 | **SHA-256 哈希链（篡改可检测）** | 无 | 无 |
| 因果诊断 | **反事实修复估算** | 仅打分 | 仅打分 |
| 消融实验 | **51.6pp 差距证明** | 无 | 无 |
| 进程隔离 | **子进程沙箱** | 同进程 | 同进程 |
| 伪造检测 | **工具账本交叉验证** | 无 | 无 |
| 成本追踪 | **12 模型定价 + 按用途分组** | 无 | 无 |
| 指令质量校验 | **4 维度语义 lint** | 无 | 无 |

20 维度竞品对比（覆盖 τ-bench / SOPBench / VoiceAgentEval / IFEval 等同类系统）：核心差异点为工具账本验证、策略图分支对齐、DB 终态断言、伪造检测与安全否决。完整表格见 [`docs/competitive_analysis_20agents.md`](../docs/competitive_analysis_20agents.md)。

## 架构

```
┌─────────────────────────────────────────────────────────┐
│                    run_outbound.py (CLI)                  │
└──────────────┬──────────────────────────────────────────┘
               │
┌──────────────▼──────────────────────────────────────────┐
│              OrchestratorOutbound                        │
│  ┌──────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │  Agent   │←→│  UserSim     │  │   Harness         │  │
│  │(被测)    │  │  (模拟被叫)  │  │   (安全护栏)      │  │
│  └────┬─────┘  └──────────────┘  └────────┬──────────┘  │
│       │                                    │             │
│  ┌────▼────────────────────────────────────▼──────────┐  │
│  │              ToolSimulator (18 个工具)              │  │
│  │  SQLite 内存 DB · EventLedger · SHA-256 哈希链     │  │
│  └───────────────────────────────────────────────────┘  │
│                                                          │
│  ┌─────────────────────────────────────────────────────┐ │
│  │  CostTracker（全局回调，12 模型定价，按用途分组）    │ │
│  └─────────────────────────────────────────────────────┘ │
└──────────────┬──────────────────────────────────────────┘
               │
┌──────────────▼──────────────────────────────────────────┐
│              ScorerOutbound (三层评分 · D1-D7)            │
│  客观证据层 88% + 软质量层 12% + 安全否决层(不可补偿)    │
│  含 D7 知识准确性（规则 + LLM 双层验证）                 │
└──────────────┬──────────────────────────────────────────┘
               │
┌──────────────▼──────────────────────────────────────────┐
│              DiagnosisEngine                             │
│  偏离点定位 → 失败模式分类 → 最小修复建议               │
└─────────────────────────────────────────────────────────┘
```

## 方法论

### 三层评分架构

| 层 | 权重 | 数据来源 | 说明 |
|---|---|---|---|
| **客观证据层** | 88% | 策略图对齐 + 事件账本 + DB 终态 | 确定性规则，不依赖 LLM |
| **软质量层** | 12% | PoLL 双模型评委（Opus + Sonnet） | 被客观层门控：客观不及格则无效 |
| **安全否决层** | 封顶 | 伪造检测 + 违规分类 | 伪造→0 分，严重违规→≤40% |

**客观证据层细分**：
- **硬指标 30%**：工具调用成功率、开场白/结束语、禁止词 — 全确定性
- **步骤合规 24%**：步骤按序执行率 — 谓词匹配 + 保守回退
- **分支准确 14%**：条件分支走向 — 确定性
- **时序约束 12%**：工具必须在依赖操作后执行 — 确定性
- **路径对齐 8%**：核心策略节点覆盖比例 — 确定性

每个得分原子都指向 EventLedger 中的具体事件，33 个 rubric 子指标（D1-D7）确定性评分。

### 策略图编译

```
原始指令（自然语言 / JSON DSL）
    ↓ parse_instruction.py
结构化步骤 + 分支 + 约束 + source_quote 溯源
    ↓ compile_instruction.py
策略图 DAG（InstructionStep → Branch → ScoringAtom）
    ↓ policy_graph.py
可视化 Mermaid 状态图
```

编译时保留 `source_quote` 原文片段——每个评分原子都可以追溯到原始指令中的具体文字，防止 LLM 幻觉。

### 指令质量校验

`instruction_lint.py` 在评测前自动检测指令自身的 4 类缺陷：

| 维度 | 检测内容 | 示例 |
|---|---|---|
| **矛盾检测** | 两条规则互斥 | "必须道歉" vs "禁止道歉" |
| **不可行检测** | 单条规则无法执行 | "在 3 秒内完成 20 步操作" |
| **歧义检测** | 不同人理解不同 | "适当补偿"（多少算适当？） |
| **分支缺失** | 条件分支无处理器 | "如果用户拒绝" 没有后续步骤 |

每个发现附带 `source_quote` 原文引用和修复建议，合规分数确定性计算。

### 仪表盘可视化

- **SVG 雷达图**：D1-D7 七维度得分一目了然，纯 SVG 渲染零外部依赖
- **模型 × 场景热力图**：多模型横评的分数矩阵，色温映射得分高低
- **证据下钻**：从总分 → 维度 → ScoringAtom → 事件 ID → 工具记录，逐层追溯

## 实验数据

### 消融实验（评分严格度差异，非准确率）

111 条冻结 trace，逐层拆除组件观察系统评分均值变化。**注意**：更低的均分意味着更严格、更多违规被检出，**不**自动等于更准确——准确性需要人工标签验证（见下方校准状态）。

| 配置 | 评分均值 | 与完整系统差距 |
|------|---------|--------------|
| 仅 LLM 评委 | 88.8% | +51.6pp（纯文本评委看不到执行状态违规） |
| + 规则层 | 45.2% | +8.0pp |
| + 分支检查 | 38.8% | +1.6pp |
| + 安全否决 | 38.2% | +1.0pp |
| **完整系统** | **37.2%** | — |
| 去掉 LLM 评委 | 36.9% | -0.3pp（LLM 仅贡献微量加分） |

**结论**：纯文本 LLM 评委对隐藏的执行状态违规（工具未调用、DB 未变更、时序错乱）不敏感，因此给出系统性偏高分数。确定性规则层通过检查账本和 DB 终态，检出了这些隐藏违规。每个组件都有可测量的独立贡献。`python reproduce_claims.py` 可一键验证。

### 配对实验（评分可复现性）

LongCat-2.0-Preview × 10 场景 × 3 次重复：

| 场景 | 难度 | 均分 | 标准差 |
|------|------|------|--------|
| 配送确认 | easy | 55.7% | ±11.5 |
| 满意度回访 | easy | 48.2% | ±6.0 |
| 飞毛腿骑手 | medium | 35.7% | ±5.1 |
| 课程直播 | hard | 28.8% | ±9.7 |
| 售后外呼 | hard | 32.4% | ±3.7 |
| 合规冲突 | hard | 30.1% | ±7.1 |
| 极限压测 | extreme | 32.5% | ±7.4 |

**中位数标准差 7.1%**，评分可复现。难度梯度清晰：easy ~52% > medium ~36% > hard ~30%。

### 多模型横评（2026-05-22 实测，41 条 trace）

| 模型配置 | Trace 数 | 平均分 | 最低 | 最高 |
|---|---|---|---|---|
| Haiku + Harness | 3 | **57.5%** | 47.5% | 70.0% |
| Claude CLI | 8 | **56.5%** | 30.0% | 100.0% |
| MiniMax-M2.7 | 8 | **53.3%** | 13.0% | 84.5% |
| MiMo-V2.5-Pro | 5 | **49.2%** | 36.0% | 60.0% |
| Sonnet 4 | 6 | **48.3%** | 34.2% | 62.5% |
| Haiku 4.5（裸跑） | 8 | **47.7%** | 34.0% | 62.5% |

**关键发现**：Harness 让 Haiku 从 48% 提升到 58%（+10pp）；MiniMax 同一场景波动高达 70 分，证明评测系统能捕获模型不稳定性；难度梯度清晰（easy 55%+ > hard 40-50% > extreme 35-62%）。

### 校准

- **二元判定**：κ = 0.868（近乎完美一致性，61 条标注）
- **维度交叉验证**：GPT-5.5-pro 32 条独立评审，67% ±1 维度一致
- **违规召回率**：96%（100 个种入缺陷，96 个被捕获）
- **元评测区分力**：medium ≥ flawed 100%（11/11 场景排序正确）

### 成本追踪

`cost_tracker.py` 内置 12 个主流模型的定价（Anthropic / OpenAI / DeepSeek / MiniMax / Kimi 等），按用途分组统计（user_sim / judge / scorer / diagnosis），通过全局回调集成到编排器。单场景评测成本约 ¥0.8-1.5，34 场景全套约 ¥10-18（基于 MiniMax API 价格）。

## 学术对标

本系统融合了以下前沿研究的方法论：

| 项目 | 来源 | 我们借鉴了什么 | 我们的区别 |
|---|---|---|---|
| **τ-bench** | ICLR 2025 | DB 终态断言验证 | τ-bench 只做 pass/fail，我们加了策略图对齐 + 连续分数 + 因果诊断 |
| **SOPBench** | arxiv 2503.08669 | SOP→有向图 oracle 验证 | SOPBench 不支持条件分支（自述局限），我们原生支持 |
| **VoiceAgentEval** | Xbench 2025 | 外呼专属评测框架 | VoiceAgentEval 用 LLM 总体评分，我们 88% 确定性 |
| **ESAA** | arxiv 2602.23193 | Event Sourcing + SHA-256 投影哈希 | 我们在此基础上加了伪造检测 + EventLedger 哈希链 |
| **IFEval** | Google 2023 | 指令遵循评测基准 | 我们聚焦多轮对话 + 工具调用场景 |
| **RubricEval** | 2026 | 细粒度 rubric 评分框架 | 我们用 PoLL 双评委 + 客观层门控解决 LLM Judge 56% 准确率问题 |

更多参考文献（CheckList / MT-Bench / LLMBar / SafeToolBench / AgentPRM 等）见 [PITCH.md](PITCH.md) 附录。

## 项目结构

```
agent-eval/
├── run_outbound.py          # CLI 入口
├── orchestrator_outbound.py # 编排器
├── scorer_outbound.py       # 评分引擎（D1-D7，含知识准确性）
├── scorer_modules/          # 评分子模块（checkers / judges / computation）
├── harness.py               # 安全护栏（三模式重试回滚）
├── user_sim_outbound.py     # 用户模拟器（5 级自适应对抗）
├── policy_graph.py          # 策略图编译器
├── compile_instruction.py   # 指令编译（source_quote 溯源）
├── instruction_lint.py      # 指令语义校验（4 维度 lint）
├── trace_verifier.py        # 轨迹验证器（DP 对齐）
├── evidence_verifier.py     # 证据验证（canary + coercive 检测）
├── diagnosis.py             # 失败根因诊断 + 反事实修复估算
├── cost_tracker.py          # 成本追踪（12 模型定价 · 全局回调）
├── llm.py                   # 多模型 LLM 抽象层
├── dashboard.py             # FastAPI 可视化仪表盘
├── replay.py                # 确定性 replay（hash chain 验证）
├── scenario_linter.py       # 场景静态检查（8 项 lint）
├── Dockerfile               # 容器化部署
├── docker-compose.yml       # 一键启动
├── static/index.html        # 仪表盘前端（SVG 雷达图 + 热力图）
├── calibration/             # 校准实验工具
│   ├── oracle_batch_annotate.py  # Oracle 多模型交叉验证
│   ├── ablation_study.py         # 消融实验
│   ├── paired_experiment.py      # 配对实验
│   └── build_gold_set.py         # 金标数据集构建
├── scenarios/outbound/      # 34 个评测场景
└── traces/                  # 评测轨迹输出
```

## 关键文档

| 文档 | 内容 |
|---|---|
| [CONTRACTS.md](../CONTRACTS.md) | 6 条系统不变量（改评分前必读） |
| [CLAIMS.md](../CLAIMS.md) | 声明→证据→复现命令映射 |
| [LIMITATIONS.md](../LIMITATIONS.md) | 已知局限 |
| [CHANGELOG.md](../CHANGELOG.md) | 迭代日志 |
| [PITCH.md](PITCH.md) | 路演要点 + 话术 + 完整数据 |
| [docs/adr/](../docs/adr/) | 架构决策记录 |
| [docs/references/](../docs/references/) | 学术对标（τ-bench / SOPBench / ESAA / VoiceAgentEval） |

## 许可

内部项目，仅供美团黑客松评审使用。
