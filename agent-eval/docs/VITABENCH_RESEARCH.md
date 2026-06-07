# VitaBench 完整调研报告

> 2026-05-20 调研，来源：论文全文、GitHub 代码、HuggingFace 数据集、ICLR 审稿、美团技术博客

---

## 一、项目基本信息

| 项目 | 详情 |
|---|---|
| 全名 | VitaBench: Benchmarking LLM Agents with Versatile Interactive Tasks in Real-world Applications |
| 团队 | 美团 LongCat 团队（何伟†、孙月晴*、郝宏岩*、郝雪媛* 等） |
| 通讯 | whe23@m.fudan.edu.cn, guqi03@meituan.com |
| 论文 | ICLR 2026 Accept (Poster)，arXiv:2509.26490 |
| 代码 | https://github.com/meituan-longcat/vitabench (MIT) |
| 数据 | https://huggingface.co/datasets/meituan-longcat/VitaBench (46.4MB) |
| 官网 | https://vitabench.github.io |
| 排行榜 | https://vitabench.github.io/#Leaderboard |
| ICLR 审稿 | https://openreview.net/forum?id=rtcX9qOBaz |
| 美团博客 | https://tech.meituan.com/2025/11/02/vitabench-agent.html |
| 场景 | 外卖点餐 / 餐厅就餐 / 旅游出行 |
| 规模 | 66 工具、512 依赖边、400 任务（100 跨场景 + 300 单场景） |
| 技术栈 | Python 3.10+, Pydantic, litellm, loguru, PyYAML |

---

## 二、理论框架：POMDP + 三维复杂度

### 2.1 POMDP 形式化

每个任务建模为部分可观测马尔可夫决策过程：

```
(U, S, A, O, T, r)_e
```

- U = 指令空间
- S = S_db ⊗ S_user（数据库状态 × 用户状态的张量积）
- A = 动作空间（工具调用 + 对话）
- O = O_db ⊗ O_user（观测空间）
- T = 状态转移（T_db 确定性 Python 函数 + T_user 随机性 LLM）
- r: S×A→R = 奖励函数

轨迹：τ = (s₀, a₁, s₁, a₂, s₂, …, aₜ, sₜ) ~ πθ(τ|e, u)

### 2.2 三维复杂度框架

**总复杂度**：C_task = ⟨C_reason, C_tool, C_interact⟩

**推理复杂度 C_reason**：
- H(O) = 观测空间熵（Agent 需处理的信息量）
- η = 1 − |O|/|S| = 部分可观测度（越高越难）
- 单任务涉及 5-20 个服务商，100+ 候选产品

**工具复杂度 C_tool**：
- 工具集建模为有向图 G = (V, E)
- |V| = 工具数，ρ = |E| / [|V|(|V|−1)] = 边密度
- |V_task| / |V| = 任务覆盖率

**交互复杂度 C_interact**：
- 用户画像（人口属性、偏好、消费历史）
- 行为属性（情绪表达、交互模式）
- 动态状态演化

### 2.3 复杂度与难度相关性验证

| 领域 | 工具数 | 边密度 | 成功率 |
|---|---|---|---|
| 到店消费 | 24 | 12.3% | 42.1% |
| 外卖配送 | 20 | 13.2% | 中等 |
| 在线旅游 | 38 | 22.0% | 20.7% |
| 跨场景 | 66 | 11.2% | 16.2% |

---

## 三、评测方法论

### 3.1 评分方式：全有或全无

```
score = 𝟙[∑ⱼ sⱼ = k]  （全部 k 个 rubric 满足 = 1，否则 = 0）
```

### 3.2 三个指标（基于 4 次独立运行）

- **Avg@4**：4 次平均成功率
- **Pass@4**：至少 1 次成功的概率
- **Pass^4**：4 次全部成功的概率（稳定性）

统计论证：4 次运行 vs 单次，MSE 降 77.5%；增到 8 次仅边际改善。

### 3.3 滑动窗口评估器

1. 轨迹切成重叠窗口（每窗 10 条消息，重叠 2 条）
2. 每窗口调 LLM judge（默认 Claude-3.7-Sonnet）
3. 持久状态向量 s ∈ {0,1}^k 跨窗口追踪
4. rubric 可从 true→false（Agent 撤销了之前的正确行为）
5. 最终：全部满足 = 1.0，否则 = 0.0

**消融实验**：

| 配置 | Cohen's κ |
|---|---|
| 滑动窗口 + rubric（完整版） | **0.828** |
| 去掉滑动窗口 | 0.604 |
| 去掉 rubric | 0.018 |
| 两者都去掉 | 0.067 |

### 3.4 评估 prompt 硬规则

1. 查询结果 ≠ 推荐（工具返回搜索结果不代表 Agent 推荐了）
2. **用户妥协不影响评判**（用户说"少点也行"仍算不满足）
3. 文本用功能等价原则（地址/备注意思对即可）
4. Agent 不能编造工具结果（没调工具说"已查到" = 不满足）
5. 订单类必须确认实际下单成功
6. 严格匹配数量和时间

### 3.5 提前终止

如果仿真因 TOO_MANY_ERRORS / MAX_STEPS / INVALID_AGENT_MESSAGE 终止，直接 reward=0.0，不进 LLM judge。

---

## 四、用户模拟器

### 4.1 底层模型

GPT-4.1-2025-04-14

### 4.2 Prompt 五段式结构

1. **角色设定**：人物画像 + 任务指令
2. **对话风格**：强制单行回复，"背景描述 + 需求表达"模式
3. **信息披露**：渐进式揭示（不一次暴露所有需求，分轮次给）
4. **信息处理**：缺信息说"我不知道"，不假设不扩展不替换不泛化
5. **终止规则**：直到 Agent 完成所有任务才结束

### 4.3 关键行为规则

- 每次只生成一行（模拟真实短消息）
- 信息逐步透露
- 坚持需求（Agent 试图说服时不妥协）
- 重复提问 ≥3 次 → 表现不耐烦
- 终止信号：`###STOP###`、`###TRANSFER###`、`###OUT_OF_SCOPE###`

### 4.4 验证结果

- 信息保真度：9.48/10（2 名标注员评估 100 段对话）
- 人格一致性：9.34/10（5 种性格，100 段对话）

### 4.5 人格对性能影响

| 人格 | Avg@4 | Pass@4 | Pass^4 |
|---|---|---|---|
| 合作型 | 22.8% | 50.0% | 5.0% |
| 急躁型 | 21.5% | 48.0% | 4.0% |
| 随机（论文设定） | 21.3% | 49.0% | 4.0% |
| 依赖型 | 20.6% | 45.0% | 3.0% |
| 心散型 | 19.3% | 47.0% | 0.0% |
| 焦虑型 | 18.5% | 41.0% | 2.0% |

---

## 五、66 个工具完整列表

### 5.1 工具分布

| 领域 | 总数 | 写工具 | 读工具 | 通用工具 |
|---|---|---|---|---|
| 外卖配送 | 20 | 4 | 10 | 6 |
| 到店消费 | 24 | 9 | 10 | 5 |
| 在线旅游 | 38 | 14 | 19 | 5 |
| **合计** | **66** | **27** | **33** | **6** |

### 5.2 依赖图

| 领域 | 工具数 | 依赖边数 | 边密度 |
|---|---|---|---|
| 到店消费 | 24 | 68 | 12.3% |
| 外卖配送 | 20 | 50 | 13.2% |
| 在线旅游 | 38 | 309 | 22.0% |
| 跨场景 | 66 | 512 | 11.2% |

核心设计：**领域规则编码进工具依赖图**，Agent 通过探索图结构自主发现规则（不需要读策略文档）。

### 5.3 完整工具名

**通用工具（8 个）**：
longitude_latitude_to_distance, weather, address_to_longitude_latitude, get_date_holiday_info, get_holiday_date, get_user_historical_behaviors, get_user_all_orders, get_nearby

**配送域（12 个）**：
delivery_distance_to_time, get_delivery_store_info, get_delivery_product_info, delivery_store_search_recommand, delivery_product_search_recommand, create_delivery_order, pay_delivery_order, get_delivery_order_status, cancel_delivery_order, modify_delivery_order, search_delivery_orders, get_delivery_order_detail

**到店域（16 个）**：
instore_shop_search_recommend, instore_product_search_recommend, create_instore_product_order, pay_instore_order, instore_cancel_order, instore_book, pay_instore_book, instore_cancel_book, instore_reservation, instore_modify_reservation, instore_cancel_reservation, get_instore_orders, get_instore_reservations, get_instore_books, search_instore_book, search_instore_reservation

**旅行域（30 个）**：
get_ota_hotel_info, get_ota_attraction_info, get_ota_flight_info, get_ota_train_info, hotel_search_recommand, attractions_search_recommend, flight_search_recommend, train_ticket_search, create_hotel_order, create_attraction_order, create_flight_order, create_train_order, pay_hotel_order, pay_attraction_order, pay_flight_order, pay_train_order, search_hotel_order, search_attraction_order, search_flight_order, search_train_order, get_hotel_order_detail, get_attraction_order_detail, get_flight_order_detail, get_train_order_detail, modify_train_order, modify_flight_order, cancel_hotel_order, cancel_attraction_order, cancel_flight_order, cancel_train_order

---

## 六、代码架构

### 6.1 目录结构

```
vitabench/
├── data/vita/domains/
│   ├── cross_domain/tasks.json       # 跨域 100 任务 (~10MB)
│   ├── cross_domain/tasks_en.json    # 英文版 (~7MB)
│   ├── delivery/tasks[_en].json      # 外卖 (~2.5MB)
│   ├── instore/tasks[_en].json       # 到店 (~5.3MB)
│   └── ota/tasks[_en].json           # 旅游 (~8.7MB)
└── src/vita/
    ├── config.py                     # 默认值
    ├── registry.py                   # 组件注册中心
    ├── cli.py                        # CLI 入口
    ├── run.py                        # 仿真运行主逻辑
    ├── agent/
    │   ├── base.py                   # BaseAgent 抽象基类
    │   └── llm_agent.py              # LLMAgent + LLMSoloAgent
    ├── user/
    │   ├── base.py                   # BaseUser
    │   └── user_simulator.py         # UserSimulator + DummyUser
    ├── orchestrator/orchestrator.py   # 消息路由引擎
    ├── environment/
    │   ├── db.py                     # 数据库抽象
    │   ├── environment.py            # Environment + 跨域合并
    │   ├── tool.py                   # Tool 类（函数→OpenAI schema）
    │   └── toolkit.py                # @is_tool 装饰器
    ├── evaluator/
    │   ├── evaluator.py              # 路由入口
    │   └── evaluator_traj.py         # TrajectoryEvaluator（核心）
    ├── metrics/agent_metrics.py      # pass^k, pass@k, average@k
    ├── prompts/                      # 所有 YAML prompt 模板
    │   ├── agent_system_prompt.yaml
    │   ├── user_system_prompt.yaml
    │   ├── sliding_window_eval_template.yaml
    │   └── ...
    └── domains/
        ├── delivery/tools.py         # 15 个工具
        ├── instore/tools.py
        └── ota/tools.py              # 最大 ~45KB
```

### 6.2 数据流

```
CLI (vita run)
  → Environment（DB + Tools）
  → Agent（LLMAgent）+ UserSimulator
  → Orchestrator.run()（循环：Agent ↔ Environment ↔ User）
  → evaluate_simulation()
    → TrajectoryEvaluator.calculate_reward()（滑动窗口）
  → compute_metrics()（Avg@4, Pass@4, Pass^4）
```

### 6.3 关键默认值

| 参数 | 默认值 |
|---|---|
| Agent 模型 | gpt-4.1 |
| 用户模拟器 | gpt-4.1 |
| 评估器 | claude-3.7-sonnet |
| 最大步数 | 300 |
| 最大工具错误 | 10 |
| 温度 | 0.0 |
| 评估模式 | trajectory（滑动窗口+rubric） |

### 6.4 终止条件

- `###STOP###` 标记（Agent 或 User 发出）
- max_steps = 300
- max_errors = 10（工具调用错误）
- Agent 生成无效消息（重试 3 次失败）

### 6.5 关键发现：无测试代码

仓库中**没有 tests/ 目录**。没有单元测试、集成测试或对抗测试。

---

## 七、排行榜数据（2026-01-22 更新）

### 7.1 Thinking 模型 TOP 10

| 排名 | 模型 | 跨场景 Avg@4 | Pass@4 | Pass^4 |
|---|---|---|---|---|
| 1 | Gemini-3-Flash (high) | **32.5** | 63.0 | 7.0 |
| 2 | Gemini-3-Pro (high) | 31.5 | 59.0 | **10.0** |
| 3 | LongCat-Flash-Thinking | 29.3 | 60.0 | 8.0 |
| 4 | Claude-4.5-Opus | 28.5 | 52.0 | 8.0 |
| 5 | o3 (high) | 26.3 | 51.0 | 6.0 |
| 6 | GPT-5.2 (xhigh) | 24.3 | 55.0 | 2.0 |
| 7 | DeepSeek-V3.2 | 24.0 | 53.0 | 4.0 |
| 8 | Claude-4.5-Sonnet | 23.5 | 49.0 | 4.0 |
| 9 | o4-mini (high) | 19.5 | 49.0 | 1.0 |
| 10 | Doubao-Seed-1.8-Thinking | 18.8 | 43.0 | 4.0 |

### 7.2 Non-Thinking 模型 TOP 10

| 排名 | 模型 | 跨场景 Avg@4 | Pass@4 | Pass^4 |
|---|---|---|---|---|
| 1 | Gemini-3-Pro (low) | **30.0** | 57.0 | **10.0** |
| 2 | Claude-4.5-Opus | 23.3 | 54.0 | 4.0 |
| 3 | LongCat-Flash-Chat | 22.8 | 49.0 | 5.0 |
| 4 | DeepSeek-V3.2 | 18.5 | 41.0 | 2.0 |
| 5 | Claude-4.5-Sonnet | 17.0 | 38.0 | 1.0 |
| 6 | GLM-4.7 | 15.5 | 38.0 | 1.0 |
| 7 | Qwen3-Max | 14.3 | 32.0 | 1.0 |
| 8 | Doubao-Seed-1.8 | 13.8 | 34.0 | 3.0 |
| 9 | Qwen3-235B-A22B | 12.3 | 34.0 | 1.0 |
| 10 | Kimi-K2-0905 | 11.5 | 31.0 | 0.0 |

### 7.3 关键趋势

- 最佳模型跨场景仅 32.5%，70% 的任务失败
- Pass^4 最高 10%——即使最强模型也极不稳定
- Thinking 模式普遍提升 3-8 个百分点
- LongCat（美团自家）thinking 第三，non-thinking 第三
- 配送最容易，旅游最难

---

## 八、ICLR 2026 审稿详情

### 8.1 总体

- 4 位审稿人：3×6（接收）+ 1×4（拒稿）
- 最终决定：**Accept (Poster)**

### 8.2 审稿人 jGAh（6 分）

**优点**：清晰的三轴框架、真实数据、κ=0.828 有验证
**缺点**：计算成本不透明、只测了一种 Agent 架构

### 8.3 审稿人 Bj6N（4 分，最严厉）

**优点**：正确识别了更具挑战性的基准需求
**核心批评**：
1. **LLM 评 LLM 循环性**——Agent 是 LLM，用户模拟器是 LLM，评估器也是 LLM。可能测的是模型行为风格对齐而非客观任务成功
2. **用户模拟器不够真实**——真实用户会忘记约束、提供矛盾信息、中途改主意。基准的"不耐烦"是脚本式反应
3. **文化特异性**——以中国情境为主，对西方模型是否公平

### 8.4 审稿人 boYB（6 分）

**优点**：写作清晰、聚焦真实场景、可靠性分析全面
**缺点**：已有很多类似基准，新洞察有多少？永久标记机制存疑
**作者回复后**：信心从 3 提到 4

### 8.5 审稿人 zsYD（6 分）

**优点**：填补真实场景空白、复杂度形式化合理
**缺点**：只测基础模型、不清楚如何支持可学习 Agent

### 8.6 Area Chair 元审稿

- 整体偏正面（borderline positive）
- **关键评语："滑动窗口对于 graph/specification 类任务来说是一个较弱的替代方案"**
- 建议未来工作：评估 Agent 如何从交互中学习

### 8.7 作者补充实验

**跨模型偏差检查**：

| 用户模拟器 | Agent | Avg@4 |
|---|---|---|
| GPT-4.1 | GPT-4.1 | 13.8 |
| GPT-4.1 | Claude-4-Sonnet | 21.3 |
| Claude-4-Sonnet | GPT-4.1 | 13.8 |
| Claude-4-Sonnet | Claude-4-Sonnet | 19.5 |

结论：换模拟器后 GPT-4.1 不变，Claude 略降，隐式合作极小。

**成本**：GPT-4.1 单条轨迹 $0.16，约 3.5 分钟。

---

## 九、错误分析

### 9.1 错误分布（Claude-4.1-Opus，76 个失败 rubric）

| 错误类型 | 占比 |
|---|---|
| 推理错误（时空/常识/复杂约束） | 61.8% |
| 工具使用错误（选错/传参/恢复失败） | 21.1% |
| 用户模拟器自身错误 | 9.2% |
| 交互管理错误（丢偏好/不澄清） | 7.9% |

### 9.2 两个关键能力缺陷

1. **自我意识差**：Agent 经常在拥有合适工具的情况下放弃任务
2. **错误恢复有限**：工具失败时重复失败尝试而非调整策略

---

## 十、与我们系统的对比

| 维度 | VitaBench | 我们的系统 | 谁更好 |
|---|---|---|---|
| 评分方式 | 0/1（全有全无） | 0-100 连续 + 分项 | **我们**（工程指导价值） |
| 评估器 | LLM judge（κ=0.828） | 88% 确定性 + 12% LLM | **各有优劣** |
| 评估器可靠性 | κ=0.828 | ICC=0.625 | **VitaBench** |
| 诊断能力 | 错误分类统计 | CausalDiagnosis + 反事实修复 | **我们** |
| 反作弊 | 几乎无 | 三层防御 + 61 对抗测试 | **我们** |
| 测试覆盖 | 无测试代码 | 162 项测试 | **我们** |
| 工具规模 | 66 工具 / 512 边 | 8 工具 | **VitaBench** |
| 任务规模 | 400 任务 | 12 场景 | **VitaBench** |
| 模型覆盖 | 28 模型 | 3 模型 | **VitaBench** |
| 理论框架 | POMDP 三维复杂度 | PolicyGraph（工程化） | **VitaBench** |
| 用户模拟器 | 五段式 prompt + 验证 | 相对简单 | **VitaBench** |
| 业务贴合度 | 通用生活服务 | 外呼策略合规 | **我们**（对命题 2） |
| 学术地位 | ICLR 2026 | 无 | **VitaBench** |

---

## 十一、可借鉴的设计

### 高优先级（P0，直接影响路演）

1. **滑动窗口 + 原子化 rubric**——解决长对话评估 + 提升可靠性
2. **"用户妥协不影响评判"规则**——防止评分虚高
3. **对标 VitaBench 的路演叙事**——"解决了 ICLR 审稿人批评的三个问题"

### 中优先级（P1，显著提升深度）

4. **用户模拟器五段式 prompt 重构**
5. **场景扩展到 20-30 个**
6. **4 次运行协议 + 统计报告**

### 低优先级（P2，锦上添花）

7. PolicyGraph 形式化（图论定义）
8. 工具依赖图可视化
9. 排行榜页面

---

## 十二、图片资源 URL

| 序号 | URL | 描述 |
|---|---|---|
| 1 | https://p1.meituan.net/meituantechblog/388316cb7bd04c7e480828b7a44a36f9247626.jpg | VitaBench 基准展示 |
| 2 | https://p1.meituan.net/meituantechblog/28572d1e477b4ab2e62284fa9e1e007d757095.png | 概览图表 |
| 3 | https://p0.meituan.net/meituantechblog/0b61bf474466b1dcf8daac418d75e625558018.png | 三大维度框架 |
| 4 | https://p0.meituan.net/meituantechblog/0fc07354c9b8ef7b4bc8c6710daae51d46514.png | POMDP 建模示意图 |
| 5 | https://p0.meituan.net/meituantechblog/3fe03f09599c7cbb182795cb1e068ceb591064.png | 基准构建流程 |
| 6 | https://p0.meituan.net/meituantechblog/e3f6511753c4b2ac9efbd7632878ec59241228.png | 任务统计表 |
| 7 | https://p0.meituan.net/meituantechblog/21c7e753f051418156c17a7070a1b0e9361073.png | 评估器示意 |
| 8 | https://p0.meituan.net/meituantechblog/5f2a61f3711c8bb08fa316b58940c9181220681.png | 主实验结果表 |
| 9 | https://p0.meituan.net/meituantechblog/d82d4ef9b59966b22e04f57ef62d1fde279781.png | 思考型模型对比 |
| 10 | https://p0.meituan.net/meituantechblog/886d6333edf92fd640776c4b23e798fb298676.png | 消融实验数据 |
| 11 | https://p0.meituan.net/meituantechblog/afa23145db991e5b96d766b9b9e7c45d293425.png | 复杂性分析图 |
| 12 | https://p0.meituan.net/meituantechblog/0fd53930e8129500f3cba44a56be7f93113214.png | 交互复杂性影响 |
| 13 | https://p0.meituan.net/meituantechblog/338160924b4992bb8ed2d9dbbfdd7a26198394.png | 评估器对比 |
| 14 | https://p0.meituan.net/meituantechblog/08317fe2aa3eba5fed7878018671082a194506.png | 错误分类统计 |
