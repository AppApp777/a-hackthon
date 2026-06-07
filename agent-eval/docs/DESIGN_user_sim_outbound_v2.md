# 设计卡 — 五段式外呼用户模拟器 v2

> Day 2-3 任务（Oracle 18 天计划项 B）。
> 关联 [POSITIONING.md](POSITIONING.md) §3、[VITABENCH_RESEARCH.md](VITABENCH_RESEARCH.md) §4、Oracle 原文 `tool-results/bxzed2mnj.txt` 第 109-197 行。

---

## 问题

现有 [`user_sim_outbound.py`](../user_sim_outbound.py) 用单段散乱 prompt + 数值参数驱动：

- 拼接的 system prompt 没有 VitaBench 五段式结构 → 不可控、不可解释
- 没有"被动接听"语义 → 用户模拟得像主动求助者
- 没有"渐进披露"概念 → 信息一次暴露
- 没有显式 persona 类型 → 数值组合不直观
- 缺"用户疲劳式同意"机制 → CanonicalIntentLedger 的杀手级 demo 演不出

**路演风险**：评委一听用户模拟器开口就"喂，我吃到异物了我要全额退款"——一看就是 AI 配合者，不像真实被叫人。

---

## 方案

### 1. 引入 `PersonaArchetype` 枚举（5 种）

| Archetype | 中文 | 行为特征 |
|---|---|---|
| `COOPERATIVE` | 合作型 | 配合、回答清楚、信任 Agent |
| `BUSY` | 忙碌型 | 一直催"快说"，>5 轮没到重点就挂 |
| `WARY` | 警惕型 | 怀疑诈骗、拒绝提供敏感信息、要求自证身份 |
| `IMPATIENT` | 急躁型 | 打断、要求快、3 次重复同问题就发火 |
| `HESITANT` | 犹豫型 | 反复改口、"我再想想"、容易被诱导妥协 |

每个 archetype 对应五段式 prompt 模板的差异化片段。

### 2. 五段式 prompt 结构

完全照 Oracle 给的外呼版（`bxzed2mnj.txt` 第 120-180 行）：

```text
1. ROLE       — 你是谁、当前状态、信任程度、业务意愿、信息掌握
2. STYLE      — 电话口语风格：短句、打断、催促
3. DISCLOSURE — 渐进披露 + 合规触发：只有 Agent 合法清楚询问才透露
4. PROCESSING — 不假设、不扩展、可遗忘、可疲劳式同意
5. TERMINATION— 5 类外呼专属终止：忙挂、不信任挂、重复挂、隐私违规挂、合规结束
```

### 3. 兼容性

- 保留 `CalleePersona` 数值字段（patience / cooperativeness / busy_level / trust_level / 等）—— 现有 12 个场景 JSON 不动
- 新加 `CalleePersona.archetype: PersonaArchetype | None`
  - 缺省 None 时，**从数值参数自动推断**（保留旧场景的行为）
  - 显式设置时，覆盖数值参数（新场景用 archetype 简化定义）
- 推断规则（v1，可调）：

  | 条件 | Archetype |
  |---|---|
  | `cooperativeness >= 7` 且 `trust_level >= 7` | COOPERATIVE |
  | `busy_level >= 7` | BUSY |
  | `trust_level <= 4` | WARY |
  | `patience <= 4` 且 `emotional >= 7` | IMPATIENT |
  | 其它 | HESITANT |

### 4. 渐进披露机制

新增 `DisclosurePolicy`：

```python
@dataclass
class DisclosurePolicy:
    # turn -> 允许披露的事实 id 集合
    reveal_after_turn: dict[int, list[str]] = field(default_factory=dict)
    # 永不主动披露的事实（敏感信息）
    never_disclose: list[str] = field(default_factory=list)
    # 触发条件：Agent 必须先满足才能解锁披露
    gated_by_agent_action: dict[str, list[str]] = field(default_factory=dict)
```

简化版：先实现 `never_disclose`（身份证号、银行卡号默认进去）和 `gated_by_agent_action`（如"披露面试时间需 Agent 先解释用途"）。

### 5. 疲劳式同意

新增 `CalleeOutput.compliance_pressure_level: int = 0`（0-3）：

- 0 = 主动同意
- 1 = 轻度妥协（"嗯好"）
- 2 = 疲劳式同意（"行吧，随便"） — CanonicalIntentLedger 触发
- 3 = 被迫沉默放弃

这个字段供 scorer / CanonicalIntentLedger 区分"真同意"和"诱导妥协"。HESITANT archetype 在 Agent 第 3 轮重复施压后自动升到 2。

### 6. 不变量（CONTRACTS.md 候选）

新加 3 条不变量：

1. **回复长度不变量**：所有 archetype 默认 ≤ 30 字（电话场景），违反则降级到 30 字截断
2. **疲劳式同意不变量**：`compliance_pressure_level >= 2` 时，CanonicalIntentLedger 必标 `induced_compromise = true`
3. **元话语不变量**：保留现有 `_META_PATTERNS` 过滤，违反则替换为"嗯好的"

---

## 数据来源

| 来源 | 用途 |
|---|---|
| `CalleePersona`（现场景 JSON） | 数值参数 + 可选 archetype |
| `OutboundScenario.callee_context` | Role 段背景 |
| `OutboundScenario.callee_goal` | 隐藏目标（不直接说出来） |
| `OutboundScenario.callee_role` | 身份（骑手 / 商家负责人 / 顾客） |
| `Conversation.messages` | 对话历史（驱动渐进披露） |
| `current_turn` | turn 计数（披露门控） |

---

## 失败模式

| 失败模式 | 检测方式 | 修法 |
|---|---|---|
| LLM 不返回 JSON | `_parse_output` fallback 到纯文本（现有） | 加 reasoning trace |
| Agent 没有合法解锁就要敏感信息 → 模拟器还是给了 | 渐进披露门控测试 | DisclosurePolicy 强制阻止 |
| HESITANT 疲劳式同意时压力等级未升 | 测试断言 `compliance_pressure_level >= 2` | 在 prompt 里显式触发 + 测试覆盖 |
| 旧场景 JSON 没有 archetype 字段，向后兼容崩 | 现有 12 场景全部跑通 | 推断逻辑默认值 |
| 五段式 prompt 过长导致 LLM 性能下降 | token 数测试 | 每段 ≤ 300 字 |

---

## 要加的测试

`tests/unit/test_user_sim_outbound_v2.py`：

1. **test_archetype_inference** — 5 种数值组合 → 推断出正确 archetype
2. **test_persona_archetype_overrides_numeric** — 显式 archetype 覆盖数值推断
3. **test_disclosure_policy_blocks_sensitive** — 警惕型不主动透露身份证号
4. **test_hesitant_yields_under_pressure** — HESITANT archetype 在 Agent 第 3 次施压后 `compliance_pressure_level >= 2`
5. **test_busy_terminates_after_5_turns** — BUSY archetype 5 轮没到重点会挂
6. **test_response_length_under_30_chars** — 默认回复 ≤ 30 字
7. **test_backward_compat_existing_12_scenarios** — 现有 12 场景 JSON 跑通不报错

`tests/integration/test_user_sim_v2_real_chat.py`（可选，依赖 LLM）：

8. **test_five_part_prompt_renders** — system prompt 包含 5 段标题
9. **test_archetype_changes_response_style** — 同场景跑 COOPERATIVE vs WARY，回复风格能拉开

---

## 要改的文件

| 文件 | 改动 |
|---|---|
| `models_outbound.py` | `CalleePersona` 加 `archetype: PersonaArchetype \| None` 字段 |
| `user_sim_outbound.py` | 重构 `_build_system_prompt` → 五段式 builder；新增 `PersonaArchetype` enum；新增 `_infer_archetype`、`_disclosure_section` 等方法；`CalleeOutput` 加 `compliance_pressure_level` |
| `tests/unit/test_user_sim_outbound_v2.py` | 新增（7 个失败测试） |
| `tests/integration/test_user_sim_v2_real_chat.py` | 新增（2 个集成测试，可选） |
| `CONTRACTS.md` | 加 3 条不变量 |
| `CHANGELOG.md` | 加一条 Day 2-3 完成记录 |
| `scripts/compare_user_sim_v1_v2.py` | 新增对比脚本（3 场景 × v1/v2） |

**不动**：`scenarios/outbound/*.json`（向后兼容），`harness.py`, `scorer*.py`, `diagnosis.py`。

---

## 完成判定

- [ ] 7 个单元测试全绿
- [ ] 现有 226 项测试全绿（向后兼容）
- [ ] simplify subagent + adversarial-review subagent 跑过 0 CRITICAL
- [ ] 对比脚本输出 3 场景 × v1/v2 的对话对比，能在 dashboard 上展示
- [ ] CHANGELOG / progress / HANDOFF 更新
