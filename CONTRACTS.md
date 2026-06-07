# 评测系统契约（Evaluation Contracts）

> 任何改动 scorer/harness/orchestrator 前必须先读本文件。
> 每条契约对应 `tests/contracts/` 里的可执行测试。

## 1. 来源可信契约（Source-of-Truth）

打分必须且只能从以下来源获取成功/失败判据：
- 可观测的 harness 事件
- 工具调用的实际返回结果
- 验证过的状态变更

**禁止**将模型自述（"我已经完成了"、"退款已发出"）作为成功证据。

## 2. 执行顺序契约（Execution-Order）

任何拦截、验证、权限检查、策略检查，必须在它要阻止的工具副作用**之前**执行。

如果策略判定 BLOCK，工具**不能执行**，**不能产生副作用**。

## 3. 结果严格契约（Outcome-Strictness）

只有明确成功的操作才算成功。以下状态**不算成功**：
- failed（失败）
- blocked（被拦截）
- timed_out（超时）
- exception（异常）
- missing（缺失）
- malformed（格式错误）

## 4. 可审计契约（Auditability）

每个得分增量必须可追溯到：
- operation_id — 操作标识
- requested_action — 请求的动作
- observed_event — 观测到的 harness 事件
- observed_status — 观测到的结果状态
- scorer_decision — 打分决策
- reason — 理由

## 5. 事件顺序契约（Event-Order）

评测管道的事件流必须严格遵循：
```
request → policy_check → execute_or_block → observe → score
```
不允许跳步或乱序。

## 6. 哈希链完整性契约（Hash-Chain Integrity）

EventLedger 中每条事件包含前一条事件的 SHA-256 哈希（首条为 `"genesis"`），形成不可变链。

- `verify_chain()` 在评分前自动调用，链断裂则拒绝评分
- 任何中间事件被篡改、删除、插入、交换，都必须被检测到
- `chain_hash()` 返回整条链的指纹，写入 trace 文件供离线校验
- 测试：`tests/contracts/test_hash_chain.py`（16 项）
