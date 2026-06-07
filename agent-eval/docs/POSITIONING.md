# 路演定位 — VitaBench 对标叙事

> Day 1 产出 1/3。2026-05-20 起草，按 Oracle 18 天计划 K 项要求。
> 关联：[VITABENCH_RESEARCH.md](VITABENCH_RESEARCH.md) · [SCENARIO_MATRIX.md](SCENARIO_MATRIX.md) · [DEMO_PLAYBOOK.md](DEMO_PLAYBOOK.md)

---

## 1. 核心姿态

**致敬 + 吸收 + 垂直增强**——不是要"打败 VitaBench"，而是站在美团 LongCat 团队 ICLR 2026 Poster 的肩膀上，把通用 Agent 评测经验**垂直化到外呼业务**。

任何"VitaBench 不够好 / 审稿人也批评"的措辞**禁止使用**——这是在美团评委面前攻击自家公司成果，自杀行为。

---

## 2. 一句话定位（路演反复出现）

**正式版（slide 标题用）**

> VitaBench 证明了真实生活服务 Agent 评测必须覆盖推理、工具、交互三种复杂度；我们的工作把这个思想落到外呼业务里，并进一步把可形式化的业务要求编译成 PolicyGraph，实现可验证、可诊断、可审计的外呼 Agent 评测。

**工程化版（被追问时用）**

> VitaBench 告诉你 Agent 能不能完成复杂任务；我们的系统告诉外呼团队：Agent 为什么失败，下一步该修什么。

**对评委的两问对照**

| VitaBench 回答的问题 | 我们的系统回答的问题 |
|---|---|
| 通用 LLM Agent 能不能处理真实生活服务任务？ | 外呼 Agent 能不能安全执行一条具体业务策略？如果失败，根因在哪？怎么修？ |

---

## 3. 三维复杂度框架对应表（slide 3 用）

VitaBench 用 POMDP + 推理/工具/交互三维复杂度组织 benchmark。我们直接**接受这个框架**，投射到外呼场景：

| VitaBench 复杂度 | 外呼版投射 | 我们的处理方式 |
|---|---|---|
| 推理复杂度 C_reason | 业务分支、资格判断、话术策略 | PolicyGraph 编译成可执行决策树 |
| 工具复杂度 C_tool | CRM / 配送 / 补偿 / 转接工具依赖 | 工具依赖图 + EventLedger 审计 |
| 交互复杂度 C_interact | **被动接听**、拒绝、改口、情绪、隐私顾虑 | 五段式被叫方模拟器（外呼专属） |

**补一句关键差异**：

> 外呼比 VitaBench 的"主动求服务"用户更难——被叫方可能忙、警惕、不信任、随时挂断。所以我们在 VitaBench 用户模拟器的基础上加了"被动接听者"语义。

---

## 4. 四个声称优势的诚实重述（Oracle 审判后）

Oracle 的 Q2 严格审查指出：原来的 4 个优势全部"方向上真实，但口径要改"。**slide 上禁止用旧措辞**。

| 原声明（禁止使用） | 新口径（路演必用） | 一句话补充 |
|---|---|---|
| "88% 确定性验证" | "缩小 LLM judge 主观面" | 88% 是评分权重，不是真实错误覆盖率 |
| "0-100 优于 0/1" | "binary 准入 + continuous 诊断" | 不主张 58 和 62 有本质差异，主张 62 和 82 修复优先级不同 |
| "CausalDiagnosis 独特" | "可复核的 repair hypothesis" | 关键词是 hypothesis，不是 absolute truth |
| "反作弊是优势" | "trace integrity / auditability" | 防的不是模型作弊，是幻觉伪造和日志缺失 |

**slide 6 / 评委 Q&A 必须用新口径**。

---

## 5. 吸收 vs 增强对照（slide 8 用）

> **slide 标题禁止用"Comparison / Beat / 超越"——用"Inspired by VitaBench" 或 "Complementary to VitaBench"**

| VitaBench insight | 我们吸收 | 外呼增强 |
|---|---|---|
| Tool graph（66 工具 / 512 边）| PolicyGraph | 可执行业务策略验证 |
| User simulator（GPT-4.1 五段式） | 五段式被叫方模拟器 | 被动接听 / 挂断 / 隐私警惕 / 情绪波动 |
| Sliding-window rubric（κ=0.828） | soft-only sliding window MVP | hard/soft 分层，soft 不污染 hard |
| Multi-run（Avg@4/Pass@4/Pass^4） | subset stability report（3 场景 × 4 runs） | violation rate 而非纯成功率 |
| Atomic rubric | 我们已有 D1-D6 共 30 子标准 | + EventLedger evidence_id 绑定 |

---

## 6. 主叙事弧线（10 分钟路演骨架）

```text
警觉   → "听起来好的电话也可能违反业务策略"
顿悟   → "他们把业务指令编译成了可执行的策略图"
信任   → "每个得分都有 EventLedger 证据"
释然   → "失败时给修复方案，不只给分数"
确信   → "这是评测基础设施，不是演示玩具"
```

---

## 7. demo 关键瞬间（slide 5 + slide 6）

Oracle Q4 说这是杀手级 demo case：

```text
通话片段：
  Agent：那您不面试也可以，我先给您登记？
  User：行吧，随便。

普通评测器：
  ✓ 用户同意了 → PASS

我们的系统：
  ✗ FAIL — 用户妥协不改变原始业务红线
  根因：缺少线下面试告知（CanonicalIntentLedger 标记 mutable=false）
  证据：msg_8（agent 提议跳过）+ msg_12（用户疲劳式同意）
  最小修复：在面试告知后再询问意愿
  反事实恢复：+24 分
```

这个 demo 的"心跳冲击"在于：**它揭穿了"用户同意 = 任务完成"的天然假设**。评委会立刻明白为什么外呼评测需要业务红线。

---

## 8. 业务域定位（与美团真实示例对齐）

**重要修正**：Oracle 在不知道脱敏数据时建议"骑手招聘"业务域。读完美团 Excel（`命题二：外呼任务对话模型指令示例.xlsx`）后确认：

- **Task 1 = 站长外呼飞毛腿骑手通知**（不是招聘，是已在岗骑手的合同/绩效通知）
- **Task 2 = 客服外呼商家课程升级**（产品政策传达）

所以我们的三大业务域改为：

| 域 | 美团示例 | 业务特征 | 现有场景数 → 目标 |
|---|---|---|---|
| **D1 客服外呼用户/顾客** | （无直接示例，但是赛题主线） | 售后/退款/延迟/满意度 | 10 → 10 |
| **D2 站长外呼骑手** | Task 1 飞毛腿 | 配送任务/合同/绩效/激励 | 1 → 7 |
| **D3 客服外呼商家** | Task 2 课程直播 | 产品升级/政策变更/反馈收集 | 1 → 7 |

总计 24 场景。详见 [SCENARIO_MATRIX.md](SCENARIO_MATRIX.md)。

---

## 9. Slide 1 — 一句话 positioning slide 内容

> 这是 Day 1 必须产出的"一页 positioning slide"。HTML 占位版见 `docs/positioning_slide.html`。

```text
╔══════════════════════════════════════════════════════════════════╗
║                                                                  ║
║   Inspired by VitaBench:                                         ║
║   From General Life-Service Benchmark                            ║
║                to Outbound Policy Verification                   ║
║                                                                  ║
║   ──────────────────────────────────────────                     ║
║                                                                  ║
║   VitaBench 问：通用 Agent 能不能解决复杂任务？                  ║
║   我们问：外呼 Agent 是否执行了正确的业务策略路径？              ║
║              如果没有，根因在哪，下一步修什么？                  ║
║                                                                  ║
║   ──────────────────────────────────────────                     ║
║                                                                  ║
║   Tool Graph  ←  PolicyGraph (可执行策略验证)                    ║
║   User Sim    ←  五段式被叫方模拟器 (外呼专属)                   ║
║   Sliding Win ←  soft-only 分层评估 (不污染 hard)                ║
║   Multi-Run   ←  subset 稳定性 + violation rate                  ║
║                                                                  ║
║   ──────────────────────────────────────────                     ║
║                                                                  ║
║                + EventLedger 审计完整性                          ║
║                + CanonicalIntentLedger 用户妥协不改判定          ║
║                + CausalDiagnosis 可复核 repair hypothesis        ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝
```

---

## 10. 路演禁止 / 必用清单

### 禁止说

- "VitaBench 不够真实"
- "VitaBench 没有反作弊"
- "VitaBench 0/1 没工程价值"
- "审稿人也批评了他们"
- "我们超越 VitaBench"
- "我们 88% 的错误都能被规则覆盖"（错——88% 是权重不是覆盖率）

### 必用

- "我们站在 LongCat 团队的工作上"
- "VitaBench 解决通用 Agent 复杂任务评测；我们解决外呼业务策略可验证可诊断"
- "可复核的 repair hypothesis"（不是"绝对因果真相"）
- "binary 准入 + continuous 诊断"
- "trace integrity / auditability"
- "我们诚实地把 ICC 0.625 视为软判断的中等可靠性"（不要藏数据）

---

## 11. 评委 Q&A 防御清单（与 PITCH.md 联动）

| 评委问题 | 不要这样答 | 标准答案 |
|---|---|---|
| "你们和 VitaBench 啥关系？" | "我们超越了 VitaBench" | "我们吸收了它的可靠性设计，垂直化到外呼" |
| "ICC 0.625 是不是太低？" | "不低" / 回避 | "诚实地说是中等，所以我们用 PolicyGraph 做主判定，LLM judge 只补软维度" |
| "VitaBench 是用 LLM 评 LLM，你们呢？" | 攻击 VitaBench | "我们 PolicyGraph + EventLedger 占主，LLM judge 只用于不可形式化的软维度" |
| "你们 12 个场景太少了" | "现在已经扩到 24 了"（吹) | "12 不是产品，是种子；产品是 DSL + 编译器；24 场景按 3 业务域 × 4 难度矩阵设计" |
| "为啥不做 28 个模型对比？" | "我们做不动" | "18 天黑客松不追规模；我们选 2-3 个代表模型证明评测有区分力即可" |

---

## 12. 验收标准（Day 1 完成判定）

- [x] 一句话定位写定，**slide 标题、开场 hook、收尾口号都用同一句**
- [x] 三维复杂度对照表写定，**slide 3 可直接复用**
- [x] 四个优势新口径写定，**slide 6 / Q&A 必用新口径**
- [x] 吸收 vs 增强对照表写定，**slide 8 可直接复用**
- [x] HTML 占位 slide 落盘（见 `docs/positioning_slide.html`）
- [ ] 用户审阅 + 拍板"业务域修正方向"（**等审阅**）

---

## 13. 下一步引用

- 场景矩阵详细设计 → [SCENARIO_MATRIX.md](SCENARIO_MATRIX.md)
- demo 主线 + 演示画面 → [DEMO_PLAYBOOK.md](DEMO_PLAYBOOK.md)
- 10 分钟完整路演脚本 → [../PITCH.md](../PITCH.md)（需要根据本文档更新 slide 3 和 slide 8）
