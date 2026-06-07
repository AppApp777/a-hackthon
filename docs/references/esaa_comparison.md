# ESAA vs 我们的 EventLedger — 对比分析

> 参考论文：ESAA: Event Sourcing for Autonomous Agents (arxiv 2602.23193, 2026-02)
> 对比对象：`models.py:EventLedger`（L204-289）

## ESAA 核心方法

把 Event Sourcing 模式应用于 LLM Agent 项目管理。Agent 发出结构化 JSON 意图 → Orchestrator 验证 → 追加到不可变日志 → 纯函数投影读模型。

### ESAA 关键设计

| 组件 | 做法 |
|---|---|
| 事件流 | `activity.jsonl`，追加写入，15 种事件类型 |
| Agent 隔离 | Agent 只能发 `agent.result` / `issue.report`，不能直接写文件 |
| 验证 | JSON Schema + 边界契约 + SHA-256 投影哈希 |
| 回放 | `esaa verify` 从事件零重建完整状态，与存储哈希比对 |
| 契约 | `AGENT_CONTRACT.yaml` 定义每种任务类型的允许操作和硬禁止 |
| 读模型 | `roadmap.json`，纯函数从事件流投影 |

### 哈希验证伪代码（论文 Appendix D）

```python
def esaa_verify(events, roadmap_json):
    projected = project_events(events)       # 纯函数重建状态
    computed = sha256(canonical_json(projected))  # RFC 8785 规范化
    stored = roadmap_json["run"]["projection_hash_sha256"]
    return "ok" if computed == stored else "mismatch"
```

注意：这是**投影状态哈希**，不是事件链式哈希。验证的是"从事件日志确定性投影出的状态是否与存储的读模型一致"。

## 异同对比

### 共同点

| 维度 | ESAA | 我们的 EventLedger |
|---|---|---|
| 不可变追加 | ✅ JSONL append-only | ✅ `_frozen` + `frozen=True` |
| 事件序列号 | ✅ `event_seq` | ✅ `seq` 自增 |
| 来源区分 | ✅ `actor: "^agent-.*"` | ✅ `source_token` 区分 harness/agent |
| 深拷贝防篡改 | ✅ JSON 序列化天然拷贝 | ✅ `copy.deepcopy` 显式拷贝 |
| 投影视图 | ✅ `roadmap.json` 纯函数投影 | ✅ `successful_tool_names()` 等方法 |

### 我们的优势

| 维度 | 我们做了什么 | ESAA 没有 |
|---|---|---|
| **伪造检测** | `TOOL_FABRICATED` 事件类型 + `has_fabricated` | 无直接对应 |
| **回滚追踪** | `TOOL_ROLLBACK` + `rollback_ids` 明确标记被撤销操作 | 用新任务覆盖，不标记回滚 |
| **评分集成** | `successful_tool_names()` / `successful_tool_events_ordered()` 直接服务评分管线 | 面向项目管理，不面向评分 |
| **冻结机制** | `freeze()` 评分开始后禁止追加 | 无——已完成任务不可变但日志持续追加 |
| **实体绑定** | `scenario_order_id` 过滤跨订单事件 | 无——面向任务 ID 不面向业务实体 |

### ESAA 的优势（Phase 3.1 要借鉴的）

| 维度 | ESAA 做法 | 我们的差距 | 借鉴方案 |
|---|---|---|---|
| **哈希验证** | SHA-256 投影哈希 + `esaa verify` | ❌ 无哈希，无法证明未被篡改 | Phase 3.1：加 `verify_chain()` |
| **持久化** | JSONL 文件，进程退出不丢失 | 内存对象，进程结束即丢 | 已有 trace 文件序列化（部分覆盖） |
| **回放能力** | 从事件零重建完整状态 | ❌ 无回放机制 | Phase 3.1：加 `replay()` 方法 |
| **边界契约** | JSON Schema + AGENT_CONTRACT.yaml | 隐式（harness 层面拦截） | 可学：显式契约文件更易审计 |
| **意图-变更分离** | Agent 只发意图，Orchestrator 执行 | Agent 直接调用工具 | 设计差异（评测场景需观察 Agent 行为） |

## Phase 3.1 借鉴实施方案

基于 ESAA 的投影哈希方案，改造为**事件链式哈希**（更强于 ESAA 的投影哈希）：

```python
# 方案：每条事件包含前一条的哈希
class EventLedger:
    def append(self, event_type, turn, **kwargs):
        prev_hash = self._compute_last_hash()
        event = ToolEvent(seq=self._seq, prev_hash=prev_hash, ...)
        self._events.append(event)

    def verify_chain(self) -> bool:
        """验证整条哈希链完整性。任何中间事件被篡改都会被检测到。"""
        for i, event in enumerate(self._events):
            if i == 0:
                expected_prev = "genesis"
            else:
                expected_prev = hash(canonical(self._events[i-1]))
            if event.prev_hash != expected_prev:
                return False
        return True
```

这比 ESAA 更强：ESAA 只验证最终投影状态，我们验证每一条事件的完整性。

## 路演引用方式

### PITCH.md 中加入的段落

> "ESAA (2026) 提出将 Event Sourcing 应用于 Agent 生命周期管理——Agent 发出结构化意图，不可变日志记录每一步，SHA-256 哈希保证可审计。我们的 EventLedger 是这一范式在评测场景的独立实现，并在此基础上增加了伪造检测（`TOOL_FABRICATED`）、回滚追踪（`TOOL_ROLLBACK`）和评分管线集成。"

### 评委追问时的话术

> "Event Sourcing 在金融和审计领域已经是标准做法。ESAA 论文证明了它同样适用于 LLM Agent 管理。我们在评测场景中独立得出了相同结论——不可变事件账本是建立 Agent 可信度的基础。区别在于：ESAA 面向项目管理，我们面向评测。我们增加了伪造检测和回滚追踪——这在评测场景中至关重要，因为 Agent 会撒谎。"

## 文件映射

| ESAA 概念 | 我们的对应 | 位置 |
|---|---|---|
| `activity.jsonl` | `EventLedger._events` | models.py:208 |
| `event_seq` | `ToolEvent.seq` | models.py:193 |
| `actor` | `ToolEvent.source` | models.py:201 |
| `output.rejected` | `TOOL_BLOCKED` + `TOOL_VALIDATION_FAILED` | models.py:184-186 |
| `esaa verify` | Phase 3.1 将实现 `verify_chain()` | — |
| `AGENT_CONTRACT.yaml` | `CONTRACTS.md`（文本约束） | 项目根 |
| `roadmap.json` 投影 | `successful_tool_names()` 等 | models.py:247 |
