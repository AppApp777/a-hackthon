# 评审从这里开始 — 30 分钟代码审查路径

你时间有限，这个仓库文件很多。本页给你**最快证伪我们核心主张**的路径，不是路演。下面全部
**离线运行**（无需 API key、无需联网）。如果哪条命令没做到我们说的，那是我们的问题。

---

## 唯一主张

> **我们评的是「可执行轨迹证明发生了什么」，不是「话术声称做了什么」。**

外呼 Agent 可以用流畅礼貌的中文说"我已经帮您查了订单、退款已操作完成"。纯 LLM 评委会给它高分。
但如果事件账本里没有这次工具调用、SQLite 世界状态没有任何改变——退款就没真发生。我们的打分器
触发**非补偿性伪造否决（fabrication veto）**，把分数封顶到 0。

---

## 5 分钟 — 跑一遍证明

```bash
make judge-demo          # 或：cd agent-eval && python scripts/judge_demo.py
```

它用**同一个真实打分器**给**同一个售后场景**的三条轨迹打分：

```
CASE A: GOOD     — Agent 真的执行了工具（DB 已写入）        -> 87 / 100  gate=none
CASE B: MUTATED  — 与 A 可见对话完全相同，但删掉执行证据    -> 0 / 100   gate=zero
CASE C: REAL     — 一条真实轨迹，声称退款却从未执行          -> 0 / 100   gate=zero
```

关键在 **CASE B**：它是 demo 运行时从 CASE A **现场生成**的——只删掉隐藏的执行证据（账本事件 +
DB 行），同时把可见对话保持**逐字节相同**（demo 打印 `visible-dialogue hash: MATCH A`）。同样的话，
相反的分。而且这条被改的轨迹**否决前分数还有 70.8**——veto 照样封它到 0，因为伪造不能被流畅度补偿。
脚本在任一断言失败时返回非零退出码（成功打印 `result = PROOF_PASSED`）。

这就是 90 秒里的全部主张。想推翻它：`make judge-demo --good <你的轨迹>`。

---

## 怎么证伪每条主张（命令 → 代码 → 证据）

| 如果你怀疑… | 跑 | 然后读 |
|---|---|---|
| "打分器只是更严，没真抓到什么" | `make judge-demo` | `scripts/judge_demo.py`（它**没有打分逻辑**——只加载轨迹、调真实打分器） |
| "代码里其实没检测伪造" | `PYTHONPATH=agent-eval python -m pytest tests/judge_moat -q` | `orchestrator_outbound.py:630-645`（检测）→ `scorer_modules/computation.py:103-151`（封顶）→ `scorer_outbound.py:1469-1498`（应用） |
| "工具调了但 DB 没变也能过" | `pytest tests/contracts/test_contracts.py::TestSourceOfTruth -q` | `scorer_modules/checkers.py:207-296` `_cross_validate_outcome` |
| "账本可以被篡改" | `pytest tests/contracts/test_hash_chain.py -q` | `models.py:206-256` `EventLedger`（仅追加、哈希链） |
| "头条数字是编的" | `python reproduce_claims.py` | `reproduce_claims.py`（核对冻结产物，不调实时模型） |

---

## 15 分钟 — 按顺序读核心

可审查的核心是 **6 个概念**，不是 90 个文件：

1. `agent-eval/compile_instruction.py` — 业务指令 → 策略图原子（带 `source_quote` 溯源）
2. `agent-eval/policy_graph.py` — 分支、依赖、时序约束
3. `agent-eval/orchestrator_outbound.py:630-645` — 信任边界：声称的工具调用若不在模拟器账本里，标记为 `TOOL_FABRICATED`
4. `agent-eval/scorer_modules/checkers.py` — 把 Agent 的声称与账本 + SQLite 状态交叉验证
5. `agent-eval/scorer_modules/computation.py:103-151` — 非补偿性否决（`has_fabricated → cap 0`）
6. `agent-eval/diagnosis.py` — 从失败原子反推最早偏离点 + 候选修复

其余（dashboard、校准工具、成本追踪、哈希链、场景库）都是适配层、实验和支撑设施。

## 30 分钟

```bash
PYTHONPATH=agent-eval python -m pytest tests/judge_moat -q   # 策展的护城河测试集
python reproduce_claims.py                                   # 头条声明 vs 冻结产物
```

然后读 [CLAIMS.md](CLAIMS.md) — 每条声明都映射到代码 + 测试 + 命令 + 诚实的边界说明。

---

## 我们**不**声称什么（省得你自己找边界）

- 0–100 分**尚未**对大规模人工标签做统计校准。一个 29 条的盲验证集已锁定（预测在任何标签出现前
  就哈希锁死），留作未来的系统-人工比对；当前**不报告 MAE/κ 作为准确率结论**。详见
  [agent-eval/calibration/README.md](agent-eval/calibration/README.md)。
- 工具执行是**确定性的 SQLite 参考世界**，不是生产电话系统。打分器消费的是事件/状态**契约**；
  生产环境只需吐出同样的契约。
- 诊断给出最早失败原子 + 一个**候选**修复和粗略分数估计——不是经过证明的全局最小修复。
- 我们不评测语音/ASR/时延/语气；外呼被建模为文本对话 + 工具状态。

如果你只验证一件事，请验证 `make judge-demo`。仓库其余部分的存在，是为了让这一条不变量处处成立。
