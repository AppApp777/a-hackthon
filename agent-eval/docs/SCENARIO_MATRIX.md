# 场景矩阵设计 — 12 → 24 扩展计划

> Day 1 产出 2/3。2026-05-20 起草，关联 [POSITIONING.md](POSITIONING.md) · [VITABENCH_RESEARCH.md](VITABENCH_RESEARCH.md)。
> Oracle 18 天计划 D 项：3-5 天落地，目标 20-24 个高质量场景。

---

## 1. 业务域定义（基于美团真实示例，修正 Oracle 方向）

> Oracle 原建议"骑手招聘 + 骑手助手 + AI 站长"——读完脱敏数据后修正：美团 Task 1 是**已在岗骑手通知**（不是招聘）。

### D1 — 客服外呼用户 / 顾客（赛题主线）

- **业务范畴**：售后投诉、退款、延迟通知、满意度回访、合规处理
- **被叫方**：C 端用户
- **典型情境**：吃到异物、漏餐、餐冷、配送超时、投诉处理、回访
- **合规红线**：不能滥承诺、不能跳过身份核验、不能编造工具结果

### D2 — 站长外呼骑手（基于 Task 1 飞毛腿）

- **业务范畴**：合同生效通知、绩效警告、纪律告知、激励通知
- **被叫方**：B 端外卖骑手
- **典型情境**：飞毛腿合同生效、单数未达标警告、严重违规告知、节假日加班招募
- **合规红线**：不能强制配送、不能违反劳动法承诺、必须告知申诉路径

### D3 — 客服外呼商家（基于 Task 2 课程直播）

- **业务范畴**：产品升级、政策变更、违规警告、结算异议、服务回访
- **被叫方**：B 端机构 / 商户负责人
- **典型情境**：直播产品升级、平台政策传达、违规警告、结算争议
- **合规红线**：不能承诺折扣 / 优惠券、不能保证业绩、必须告知申诉机制

---

## 2. 复杂度六轴（每个场景沿这 6 轴设计）

吸收 Oracle 的轴定义：

| 轴 | 取值 | 演示价值 |
|---|---|---|
| C1 分支复杂度 | 0 分支 / 1-2 分支 / 3+ 嵌套分支 | 测路径对齐能力 |
| C2 工具复杂度 | 单工具 / 多工具依赖 / 工具失败注入 | 测工具调用 + 故障恢复 |
| C3 合规复杂度 | 无 / 单条禁止 / 多条禁止 + 红线冲突 | 测合规守则遵守 |
| C4 情绪复杂度 | 平静 / 急躁 or 抵触 / 暴躁 + 信号差 | 测情绪保护 + 不逃跑 |
| C5 时间复杂度 | 无序 / 必须前置确认 / 多步时序锁定 | 测 temporal constraint |
| C6 用户妥协 | 无 / 主动改需求 / 被诱导妥协（杀手级 demo） | 测 CanonicalIntentLedger |

每个新场景必须在文档里**显式标注六轴取值**，便于覆盖率分析。

---

## 3. 现有 12 场景的域 + 复杂度归类

| 场景 | 域 | 难度 | 步数 | C1 分支 | C2 工具 | C3 合规 | C4 情绪 | C5 时序 | C6 妥协 |
|---|---|---|---|---|---|---|---|---|---|
| delivery_confirm_basic | D1 | easy | 6 | 0 | 单 | 无 | 平静 | 无 | 无 |
| simple_satisfaction_survey | D1 | easy | 5 | 0 | 单 | 无 | 平静 | 无 | 无 |
| rider_feimaotui_notify | **D2** | medium | 6 | 0 | 单 | 单条 | 配合 | 无 | 无 |
| user_flip_flop | D1 | medium | 13 | 0 | 多 | 无 | 平静 | 前置 | **主动** |
| course_livestream_upgrade | **D3** | hard | 7 | 0 | 多 | 单条 | 配合 | 前置 | 无 |
| after_sales_complaint | D1 | hard | 9 | 0 | 多 | 单条 | 抵触 | 前置 | 无 |
| refund_over_budget | D1 | hard | 7 | 0 | 多 | 单条 | 平静 | 前置 | 无 |
| system_error_fallback | D1 | hard | 11 | 0 | 故障 | 无 | 平静 | 前置 | 无 |
| multi_issue_combo | D1 | hard | 16 | 0 | 多 | 单条 | 抵触 | 前置 | 无 |
| compliance_conflict | D1 | hard | 14 | 0 | 多 | 红线冲突 | 抵触 | 前置 | 无 |
| delay_notify_difficult | D1 | extreme | 10 | 0 | 多 | 单条 | 暴躁+信号差 | 前置 | 无 |
| stress_test_extreme | D1 | extreme | 20 | 0 | 多+故障 | 红线冲突 | 暴躁+信号差 | 多步锁定 | 主动 |

### 覆盖盲点（决定新增方向）

- **C1 分支复杂度** = 0 在所有 12 个场景！没有任何"显式分支"场景。这是 PolicyGraph 最该展示的能力，是路演大盲点
- **C6 用户妥协（被诱导）** = 0 个场景。CanonicalIntentLedger 的杀手级 demo 没有任何场景能跑
- **D2 站长→骑手**只有 1 个 medium
- **D3 客服→商家**只有 1 个 hard

---

## 4. 新增 12 场景设计（D2 × 6 + D3 × 6）

> 标 ★ = 必须显式分支（C1 ≥ 2），用于 PolicyGraph demo
> 标 ✗ = 必须包含被诱导妥协（C6 = 被诱导），用于 CanonicalIntentLedger demo

### D2 — 站长外呼骑手（+6）

| ID | 场景名 | 难度 | 步数 | 关键复杂度 | demo 标签 |
|---|---|---|---|---|---|
| D2.1 | `rider_contract_warning` | easy | 5 | 单数未达标警告，配合型 | — |
| D2.2 | `rider_cancel_feimaotui` | medium | 8 | 主动退出请求，**分支**：能退/不能退/犹豫 | ★ |
| D2.3 | `rider_safety_incident_callback` | hard | 11 | 安全事故回访，情绪激烈 + 合规告知（不能强迫继续配送） | — |
| D2.4 | `rider_compensation_dispute` | hard | 12 | 配送奖励争议，**分支**：核实正确/系统错误/规则误解 + 工具查询 | ★ |
| D2.5 | `rider_holiday_overtime_recruit` | medium | 8 | 节假日加班招募，**诱导妥协**：站长暗示"不报名影响排名"，骑手"那行吧"——FAIL | ★ ✗ |
| D2.6 | `rider_serious_violation_warning` | extreme | 15 | 严重违规警告（高拒单率/客诉率），**分支** + 抵触 + 申诉路径必告 + 暴躁挂断 | ★ |

### D3 — 客服外呼商家（+6）

| ID | 场景名 | 难度 | 步数 | 关键复杂度 | demo 标签 |
|---|---|---|---|---|---|
| D3.1 | `merchant_policy_announcement` | easy | 5 | 平台政策变更告知，配合型 | — |
| D3.2 | `merchant_feature_promotion` | medium | 7 | 新功能推广，**分支**：商家感兴趣/拒绝/已用/犹豫 | ★ |
| D3.3 | `merchant_violation_warning` | hard | 10 | 商户违规警告，**分支**：认错/申诉/否认 + 抵触型 | ★ |
| D3.4 | `merchant_settlement_dispute` | hard | 12 | 结算异议处理，**分支**：能解释/需工单/需升级 + 工具查询 + 情绪 | ★ |
| D3.5 | `merchant_quality_review` | medium | 7 | 服务质量回访，**诱导妥协**：客服"分数 4.0 也算满意吧？"商家"那行"——FAIL（原始要求是 ≥4.5 才算满意） | ✗ |
| D3.6 | `merchant_dropout_retention` | extreme | 14 | 商户流失挽留，**红线冲突**：不能承诺折扣 vs 想留住商家，**分支** + 多次拒绝 | ★ |

### 新场景的累计覆盖率

新增 12 个后总分布：

| 维度 | 12 场景前 | 24 场景后 |
|---|---|---|
| **D1 客服→用户** | 10 | 10 |
| **D2 站长→骑手** | 1 | 7 |
| **D3 客服→商家** | 1 | 7 |
| **C1 显式分支 ≥ 2** | 0 | **8** |
| **C6 被诱导妥协** | 0 | **2** |
| extreme 难度 | 2 | 4 |
| hard 难度 | 6 | 11 |
| medium 难度 | 2 | 5 |
| easy 难度 | 2 | 4 |

---

## 5. Fixture 结构（每个场景必带）

Oracle Q1 要求：每个 scenario 必须有 self-contained fixture，避免被质疑"只是一段 prompt"。

```jsonc
{
  "name": "rider_cancel_feimaotui",
  "domain": "D2_station_to_rider",
  "difficulty": "medium",
  "callee_profile": {
    "name": "王师傅",
    "role": "已在岗骑手",
    "persona": "犹豫型",
    "signal_quality": 8,
    "busy_level": 6,
    "trust_level": 7
  },
  "business_context": {
    "current_contract": "飞毛腿多日合同",
    "remaining_days": 3,
    "today_orders_done": 8,
    "today_orders_required": 15
  },
  "tool_database": {
    "rider_status": { "...": "..." },
    "contract_rules": { "single_day_X": 20, "multi_day_Y": 15, "deadline_hour_Z": 22 }
  },
  "allowed_tool_results": [ "query_rider_status", "modify_contract", "..." ],
  "forbidden_claims": [
    "强迫继续配送",
    "保证排名不下降",
    "承诺其他骑手不报名"
  ],
  "canonical_intent": {
    "R1": { "content": "必须告知前一日 22:00 前 App 取消规则", "mutable": false, "source": "scenario_policy" },
    "R2": { "content": "用户是否同意配送", "mutable": true, "source": "user_preference" }
  },
  "instruction_steps": [ "..." ],
  "branches": [
    { "condition": "用户能完成剩余任务", "next_step": "step_4_encourage" },
    { "condition": "用户确认无法完成", "next_step": "step_5_safe_offboard" },
    { "condition": "用户犹豫", "next_step": "step_6_clarify_consequence" }
  ]
}
```

**关键字段说明**：

| 字段 | 用途 |
|---|---|
| `canonical_intent` | CanonicalIntentLedger 输入。`mutable=false` 表示业务红线不可被用户妥协覆盖 |
| `branches` | PolicyGraph 显式分支（C1 复杂度） |
| `forbidden_claims` | 禁止行为检测（C3 合规） |
| `tool_database` | 工具模拟器的 fixture 数据，确保确定性回放 |

---

## 6. 生成流程（Oracle Q1 要求）

```text
LLM 起草场景 JSON
        ↓
PolicyGraph 编译器
        ↓
自动一致性检查
  - 是否有 unreachable node
  - 是否有 conflicting constraint
  - 是否有 missing tool precondition
  - 是否有 scoring atom
        ↓
种入缺陷回归测试
  - 故意差 Agent 跑一遍，必须 ≤ 10 分
  - good Agent 跑一遍，必须 ≥ 60 分
        ↓
人工快速验收（5 分钟看完一个场景）
```

**关键纪律**：LLM 只用于**起草**，不能直接入库。每个场景必须过编译器 + 回归测试 + 人工验收。

---

## 7. 实施排期（Oracle Day 5-8）

3-5 天产出 12 个新场景：

| 阶段 | 时间 | 任务 |
|---|---|---|
| Day 5 | 1 天 | 写场景模板生成器 `scripts/scenario_template.py`（输入：域 + 难度 + 复杂度六轴；输出：JSON 骨架） |
| Day 6 | 1 天 | 产 D2 × 6（基于 Task 1 飞毛腿真实数据做变体） |
| Day 7 | 1 天 | 产 D3 × 6（基于 Task 2 课程直播真实数据做变体） |
| Day 8 | 1 天 | 跑回归测试 + 修复编译错误 + 人工验收 + 6 个新工具到 tools_outbound.py |

---

## 8. 需要新增的工具（D2 / D3 域）

现有 8 个工具不够覆盖 D2 / D3。需补：

### D2 域新工具（4-6 个）

- `query_rider_status(rider_id)` — 查骑手当前状态、合同、绩效
- `modify_rider_contract(rider_id, action)` — 修改 / 取消 / 续签
- `query_rider_violation_history(rider_id)` — 违规记录
- `create_rider_appeal_ticket(rider_id, content)` — 创建申诉工单
- `query_station_overtime_quota(station_id)` — 节假日加班名额
- `register_rider_for_overtime(rider_id, date)` — 加班报名

### D3 域新工具（4-6 个）

- `query_merchant_status(merchant_id)` — 商家入驻状态、产品订阅
- `modify_merchant_subscription(merchant_id, product)` — 修改订阅
- `query_merchant_settlement(merchant_id, period)` — 结算明细
- `create_merchant_dispute_ticket(merchant_id, type)` — 异议工单
- `query_merchant_violation_history(merchant_id)` — 违规记录
- `create_merchant_appeal(merchant_id, content)` — 商家申诉

合计现有 8 + D2 新 5 + D3 新 5 = **18 个工具**，依然远小于 VitaBench 的 66 但密度更高（针对外呼业务）。

---

## 9. fixture 数据保密性

美团脱敏数据（`命题二：外呼任务对话模型指令示例.xlsx`）已含变量占位符（`${rider_name}` / `X 单 / Y 单 / Z 点 / W 天`）。

- 我们的 fixture **直接用占位符填具体值**（不引入外部敏感字段）
- 客户姓名 / 订单号 / 商家名 / 骑手姓名都用合成假名（如"王师傅"/"安心餐饮"/"订单 X12345"）
- 不提交真实数据到 git（脱敏 Excel 已在 git 里因为只是模板）

---

## 10. 验收标准（Day 8 完成判定）

- [ ] 24 个场景全部能跑通（good Agent ≥ 60 分，flawed Agent ≤ 10 分）
- [ ] 每个新场景显式标注六轴复杂度
- [ ] 每个新场景有完整 fixture（callee_profile + business_context + tool_database + canonical_intent）
- [ ] D2 / D3 域至少各有 1 个"显式分支 ≥ 2"场景
- [ ] D2 / D3 域至少各有 1 个"被诱导妥协"场景（demo 用）
- [ ] 6-10 个新工具加入 tools_outbound.py 并有测试
- [ ] CHANGELOG.md 更新本次扩展

---

## 11. 风险提示

- **场景质量 > 数量**：如果到 Day 8 只能稳定产出 18 个高质量场景，停在 18，不要冲 24
- **避免"假分支"**：分支必须由 PolicyGraph 编译器识别，不能只在 JSON 文本里写"如果...就..."
- **避免"假合规红线"**：每条 forbidden_claims 必须有触发测试，不能只是文字摆设
