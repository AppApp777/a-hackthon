# 评委离线审查指南

> 本指南为 30 分钟审查设计。按分钟级时间线阅读，每个阶段有明确产出。
> 如果只有 5 分钟，直接跳到 **Phase 1**（黄金案例 + 一键验证）。

## 30 秒概要

本系统解决"用 LLM 给 LLM 打分"的可信度问题。核心洞察：将业务规则编译为可执行策略图（policy graph），从对话 trace 中提取工具调用、数据库变更等**可信事件**，用确定性规则验证——而非让另一个模型"读对话猜好不好"。

一句话：**LLM 评委给 95.8 分的对话，我们给 0 分——因为工具调用是伪造的。**

---

## 分钟级审查时间线

### Phase 1 — 核心价值（0-6 分钟）

#### 0-2 min：README + 本指南

您正在读的文档。要点：

- 系统用**确定性规则**（88% 权重）替代 LLM 打分
- 纯 LLM 评委均分 88.8% vs 完整系统 37.2%（差 51.6pp）
- 这个差距不是"准确率"，而是"LLM 对执行层失败全盲"的动机指标

#### 2-4 min：一键验证

```bash
git clone https://github.com/AppApp777/a-hackthon.git && cd a-hackthon
pip install -r agent-eval/requirements.txt
python reproduce_claims.py
```

预期输出：**17/17 PASS**，含 LLM-only mean ≈88.8%、full-system mean ≈37.2%。无需 API key。

> **跑不起来？** 见下方"失败恢复路径"。

#### 4-6 min：黄金案例 ⭐

```bash
make judge-demo
```

**记忆锚点 1**：同一段对话，系统给 87 分 vs 0 分，差别只在工具是否真执行。

| | 正常执行 | 伪造执行 |
|---|---|---|
| 对话内容 | 完全相同 |完全相同 |
| 工具账本 | `query_order` ✓ `create_compensation` ✓ | 无记录 |
| DB 终态 | 订单已退款 | 未变更 |
| 系统评分 | **87** | **0** |
| LLM 评分 | 95.8 | 95.8 |

详细逐步分析：[`docs/judge_walkthrough/README.md`](docs/judge_walkthrough/README.md)

---

### Phase 2 — 证据验证（6-13 分钟）

#### 6-8 min：护城河有效性审计

[`docs/effectiveness/CORE_MOAT_EFFECTIVENESS.md`](docs/effectiveness/CORE_MOAT_EFFECTIVENESS.md)

**记忆锚点 2**：15 条有客观执行违规的 trace，LLM 全部给 88+ 分（识别率 0%）。系统区分度是 LLM 的 27 倍。

另见 [随机抽样审计](docs/effectiveness/RANDOM_SAMPLE_AUDIT.md)（20 条随机 trace，seed=42，无人工筛选，结论一致）。

三类 LLM 全盲的检测能力：
1. **工具伪造** — 对话说"已退款"但账本无记录 → 0 分
2. **DB 状态不一致** — 口头承诺但数据库无变更 → 19 分
3. **步骤完整性** — 有工具调用但 9/10 步骤未完成 → 23 分

#### 8-11 min：CLAIMS.md Tier 1

[`CLAIMS.md`](CLAIMS.md)  — 护城河声明（Tier 1）

每条声明结构：声明 → 为什么重要 → 代码位置 → 测试命令 → 预期输出。

快速验证护城河测试集：

```bash
PYTHONPATH=agent-eval python -m pytest tests/judge_moat -q
```

#### 11-13 min：契约 + 对抗测试

```bash
PYTHONPATH=agent-eval python -m pytest tests/contracts/ -q    # 不变量契约
PYTHONPATH=agent-eval python -m pytest tests/adversarial/ -q  # 反作弊
```

**记忆锚点 3**：1174 项测试全绿（单元 703 / 契约 200 / 对抗 186 / 新增 85）。

---

### Phase 3 — 架构理解（13-21 分钟）

#### 13-17 min：核心证据循环

```
场景 DSL → 策略图编译 → trace 路径对齐（DP）→ 事件账本验证 → DB 终态检查 → 确定性评分
                                                                            ↓
                                                               安全否决（伪造 → 0 分）
```

核心代码入口：

| 文件 | 职责 |
|---|---|
| `agent-eval/policy_graph.py` | 将场景 DSL 编译为有向图 |
| `agent-eval/scorer_outbound.py` | 评分引擎：确定性规则（88%）+ LLM 评委（12%） |
| `agent-eval/trace_verifier.py` | DP 算法对齐 trace 与策略图路径 |
| `agent-eval/harness.py` | 安全护栏：工具执行前拦截违规 |
| `agent-eval/evidence_verifier.py` | 验证 LLM 评委引用的证据是否真实存在 |
| `agent-eval/causal_diagnosis.py` | 失败根因归因 + 修复建议 |

#### 17-19 min：源码快速检查

建议 grep 以下关键词确认非空壳：

```bash
grep -n "safety_veto\|fabrication\|zero_gate" agent-eval/scorer_outbound.py | head -20
grep -n "hash_chain\|append_only" agent-eval/models.py | head -10
```

#### 19-21 min：消融实验

[`agent-eval/calibration/ablation_report.json`](agent-eval/calibration/ablation_report.json)

配对诊断表（移除单个组件的影响）：

| 移除组件 | 均分变化 | 解读 |
|---|---|---|
| 步骤合规 | +8.1pp | 步骤检测贡献最大 |
| 分支准确 | +1.6pp | 路径选择检测 |
| 安全否决 | +1.0pp | 均值小但**集中**——救的是伪造案例 |
| LLM 评委 | -0.3pp | 移除 LLM 几乎不影响——证明确定性规则主导 |

> 注意：消融差距是"评分严格度差异"，不是准确率。详见 [`CLAIMS.md`](CLAIMS.md) Tier 2。

---

### Phase 4 — 完整性确认（21-30 分钟）

#### 21-23 min：Demo

**方式 A**（推荐）：[在线 Demo](http://101.42.14.246/a-hackthon/)（提交后公开）

**方式 B**：本地打开提交包中的 `docs/demo/index.html`，零依赖、零安装。

**方式 C**：启动仪表盘 `cd agent-eval && python dashboard.py`（需 Python 3.10+）→ http://localhost:8765

5 条代表性评测轨迹，涵盖 2 个业务域、3 个模型、3 个分数段。

#### 23-25 min：商业价值

- **美团外呼场景直接适用**：34 个场景覆盖售后/配送/通知/合规
- **成本追踪内置**：12 模型定价，按用途分组计费
- **14 模型实测**（核心 6 深测 + 横评 8 抽测）：核心 Claude Sonnet 4 / Haiku 4.5 / MiMo-V2.5-Pro / LongCat-2.0 / MiniMax-M2.7 / Claude CLI；横评 GPT-5.5 / GLM-5.1 / GLM-5 / Qwen3.7-Max / DeepSeek-V4-Pro / DeepSeek-V3.2 / Kimi / Sonnet 4.6

#### 25-27 min：已知局限 + 校准状态

[`LIMITATIONS.md`](LIMITATIONS.md)

诚实声明：
- 0-100 分是确定性任务合规诊断分，**不是**人类偏好分（详见 [`docs/SCORE_SEMANTICS.md`](docs/SCORE_SEMANTICS.md)）
- 22 条单人标注 MAE 29.4——校准未通过，已转为错误分析附录
- 场景覆盖集中在外呼客服领域

#### 27-30 min：评分维度对照

| 评审维度 | 对应证据 |
|---|---|
| **创新性** | 策略图 + 事件账本 + 三层评分（全场独有），消融 51.6pp |
| **完整性** | 1174 测试 + Docker + Demo + CLAIMS 三级证据 |
| **应用效果** | 黄金案例 + 护城河审计（15 违规 + 5 反例） |
| **商业价值** | 外呼场景 + 成本追踪 + 多模型支持 |

---

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

---

## 失败恢复路径

如果验证命令跑不起来，按以下顺序降级：

| 问题 | 恢复方案 |
|---|---|
| `reproduce_claims.py` 报错 | 检查 Python 版本（需 3.10+）→ `pip install -r agent-eval/requirements.txt` |
| `make` 不可用 | 直接运行：`PYTHONPATH=agent-eval python scripts/judge_demo.py` |
| 依赖安装失败 | [在线 Demo](http://101.42.14.246/a-hackthon/)（零安装） |
| 网络不可用 | 阅读 [`docs/judge_walkthrough/README.md`](docs/judge_walkthrough/README.md)（静态 walkthrough） |

## 证据链

所有数据声明均有对应的证据文件和复现命令，见 [`CLAIMS.md`](CLAIMS.md)。
