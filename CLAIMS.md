# 声明 → 代码 → 测试 → 命令（证据账本）

> 每条头条声明都映射到可读的代码、可跑的测试、和带预期输出的命令。
> 强声明要让人觉得「站得住」，弱声明要让人觉得「诚实」。
> 一键核对可复现数字：`python reproduce_claims.py`（无需 API key）。
>
> 📌 所有评分数字为 **2026-06-07 冻结快照**（对应当时 scorer 版本）。`reproduce_claims.py` 校验快照内部一致性；scorer 后续迭代后重跑评测可能得到不同数值。

## Tier 1 — 护城河（完全离线可复现，无需 API key）

| 声明 | 为什么重要 | 代码 | 测试 / 命令 | 预期 |
|---|---|---|---|---|
| **声称的工具动作若无账本事件、无 DB 变更 → 封顶 0（非补偿性否决）** | 拦住"我查了订单/我退了款"这类纯文本评分会放过的伪造 | `orchestrator_outbound.py:630-645`（检测）· `scorer_modules/computation.py:103-151`（封顶）· `scorer_outbound.py:1469-1498`（应用） | `PYTHONPATH=agent-eval pytest tests/adversarial/test_adversarial.py::TestScoreCaps -q` | 通过；`overall_score_100 == 0`，`gate_type == "zero"` |
| **声称会被账本 + SQLite 状态 + 因果时序交叉验证**（退款需要 approved 行 + `create_compensation` 事件 + 正确顺序） | 光"调了工具"不够，世界状态必须真的改变 | `scorer_modules/checkers.py:207-296` `_cross_validate_outcome` | `PYTHONPATH=agent-eval pytest tests/contracts/test_contracts.py::TestSourceOfTruth -q` | 通过 |
| **同场景、同可见对话，87 vs 0**，差别只在工具是否真执行 | 整个护城河浓缩成一个可跑的产物 | `scripts/judge_demo.py` → 真实 `score_outbound_conversation`；`tests/judge_moat/test_moat.py` | `make judge-demo` | `PROOF_PASSED`；GOOD 87/none，MUTATED 0/zero，对话哈希相同 |
| **纯 LLM 评委虚高：88.8% vs 完整系统 37.2%（差 51.6pp），111 条冻结轨迹** —— 这是**动机**指标，不是准确率 | 证明纯文本评分会系统性放过轨迹层失败 | `calibration/ablation_report.json` | `python reproduce_claims.py` | 17/17，含"≈51.6pp" |
| **事件账本仅追加 + 哈希链（可检测篡改）** —— 支撑性完整性，不是护城河 | 运行后轨迹被篡改可被检测 | `models.py:206-256` `EventLedger` | `PYTHONPATH=agent-eval pytest tests/contracts/test_hash_chain.py -q` | 通过（17 项） |

护城河是前三行。哈希链是完整性卫生，**不是**反作弊——反作弊靠账本 + SQLite 语义验证，不靠哈希。

## Tier 2 — 支撑实验（从冻结产物可复现，无需 API key）

### 消融实验 —— 这是「动机」指标，不是「准确率」指标

它说明纯 LLM 评委对我们的轨迹太宽松（放过了违反可执行约束的轨迹）。完整系统均分更低意味着
**更严、更有区分度**，但**不**单凭这一点就等于"更准"——"更准"的论证在 Tier 1（打分器抓到纯文本
评分漏掉的具体失败）和 Tier 3（校准状态）。

| 声明 | 证据 | 命令 |
|---|---|---|
| 纯 LLM 均分 88.8% / 完整系统 37.2% / 差 51.6pp | `agent-eval/calibration/ablation_report.json`（`soft_judge_only.mean`、`full_system.mean`） | `python reproduce_claims.py` |
| 111 条轨迹 | `ablation_report.json` → `trace_count` | 同上 |
| 各组件增量（步骤合规 +8.1pp、分支 +1.6pp、安全否决 +1.0pp） | `ablation_report.json` → `no_*.delta_vs_full` | 同上 |

> 关于安全否决 +1.0pp 的**均值**增量：否决的作用是**集中的**，不摊在均值上。移除它几乎不动平均分，
> 但会把伪造轨迹的**最低分**从 0 抬上来——它救的是特定作弊案例，这正是它的职责。要按 case 看
> （见 `make judge-demo`），不要当均值看。

### 配对实验（需 API key —— 提供冻结快照）

| 声明 | 证据 |
|---|---|
| 10 场景 × 3 次重复；中位数标准差 7.1%；easy ~52% > medium ~36% > hard ~30% | `agent-eval/calibration/paired_experiment_report.json` |

### 测试 / 场景 / 代码规模

| 声明 | 命令 |
|---|---|
| 策展护城河测试集（先跑这个） | `PYTHONPATH=agent-eval python -m pytest tests/judge_moat -q` |
| 完整测试套件 | `PYTHONPATH=agent-eval python -m pytest tests/ agent-eval/tests/ --tb=short` |
| 34 个外呼场景 | `ls agent-eval/scenarios/outbound/*.json | wc -l` |
| 18 个模拟工具 | `python -c "from tools_outbound import _TOOL_REQUIRED_PARAMS; print(len(_TOOL_REQUIRED_PARAMS))"` |
| scorer ≤ 1800 行 | `python reproduce_claims.py`（代码规模检查项） |

## Tier 3 — 校准状态（诚实：这**不是**准确率声明）

我们把两件事分开，今天只声称第一件：

1. **可执行打分器正确性** —— 打分器正确执行策略图、账本、DB 状态、分支、时序、伪造否决这些契约。
   由 Tier 1 + 契约/对抗测试套件论证。
2. **人工校准** —— 0–100 分是否在规模上与独立人工判断一致。**尚未建立**。一个 29 条盲验证集的
   预测在任何标签出现**之前**就哈希锁定，留作未来比对。我们**不报告 MAE / κ 作为准确率头条**，
   因为我们还没有支撑它的标签。

| 项 | 状态 |
|---|---|
| 盲验证留出集 | 29 条，预测已锁定（轨迹哈希 + 打分器 commit，带锁保护）—— `agent-eval/calibration/blind_v1/` |
| 标签前冻结卫生 | 冻结期间独立审查发现两个校准工具 bug（veto 标志恒 False、失败归因恒空）；在标签出现**之前**修复 + 加回归护栏 —— 见 `agent-eval/calibration/README.md` |
| 22 条单人标注试点（MAE 29.4，Spearman −0.06） | **已被取代，不作准确率声明。** 这是一次*负向*试点：单人标注分布塌缩使相关性无意义，但它暴露了一个真实的 veto 规则缺口，我们随后修复了。细节保留在 `agent-eval/reports/human_calibration_pilot.md` 作附录，不作头条。 |
| GPT-5.x 交叉核对 | 仅作诊断，**不是**金标。LLM-as-judge 不被当作人工标签。 |

诚实的一句话：**打分器是一个可验证的确定性策略检查器；它的绝对 0–100 分尚未对大规模人工标签做统计
校准，我们也不假装它做了。**
