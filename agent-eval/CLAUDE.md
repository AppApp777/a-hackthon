# agent-eval — 项目规则

## 迭代日志（强制）

每次 commit 涉及 ≥3 个代码文件改动时，**必须同步更新 `CHANGELOG.md`**。

格式（倒序，最新在最上）：
```
## YYYY-MM-DD — 一句话标题

### 改了什么
- 具体改动列表（带文件名）

### 遇到的问题
- 具体问题（不是"出了 bug"，是"Harness 步骤注入频率过高导致 Haiku 模板泄露"）

### 解决方法
- 怎么修的 + 为什么这么修

### 路演叙事（可选但推荐）
> 一句话，假设你在给评委讲这次迭代的故事
```

**Why**：路演需要讲清楚"来时路"——踩了什么坑、怎么一步步迭代。CHANGELOG.md 是路演叙事的素材库。

## 关键文件

- `CHANGELOG.md` — 迭代日志（路演素材）
- `PITCH.md` — 路演要点 + 话术 + 数据
- `DEMO_GUIDE.md` — 离线演示指南
- `progress.md` — 项目状态（覆盖更新）
- `HANDOFF.md` — 会话交接快照
- `ORACLE_AUDIT.md` — Oracle 全面审计报告（30+ 问题 + 修复方案）
- `oracle_raw_reply_20260517.txt` — Oracle 原文
- `promptfoo-redteam.yaml` — Promptfoo 红队测试配置
- 审查机制 — 全部由独立 subagent（Opus，即最新版）执行，见下方"审查机制"章节

## 审查机制（强制：独立 subagent 执行，写代码的 agent 不审代码）

> **2026-05-19 教训**：主 agent 改了十几处 source_token 漏了一处，自己审查没发现，盲审 subagent 10 秒就抓到。

**改动涉及以下文件时，必须提交前用 subagent 跑对抗审查：**
`scorer*.py`、`harness.py`、`diagnosis.py`、`validator.py`、`orchestrator*.py`、`user_sim*.py`

### 审查 subagent 执行方式

所有审查用 `Agent(subagent_type="feature-dev:code-reviewer", model="opus")`，**无上下文启动**（不传当前会话推理过程），让 subagent 自己读代码判断。

| 审查类型 | 何时触发 | subagent prompt 要点 |
|---|---|---|
| **simplify** | 测试通过后 | 审代码复用/质量/效率，列出问题 |
| **review** | 改动提交前 | 审 bug/逻辑/规范 |
| **security-review** | 信任边界相关 | 审注入/伪造/绕过 |
| **adversarial-review** | scorer/harness/orchestrator 改动 | 假设模型在作弊，答以下 5 问 |

### 对抗审查 5 问（subagent 必须回答）

1. **能骗过吗？** — 被测 Agent 能否通过撒谎/跳步/伪造工具结果来获得高分？
2. **执行顺序对吗？** — 拦截/门控是在工具执行前还是执行后？状态回滚了吗？
3. **信任边界在哪？** — 哪些数据是 Agent 自报的？有没有交叉验证？
4. **跑两次一样吗？** — LLM judge 温度是 0 吗？有规则兜底吗？
5. **空值/默认值安全吗？** — 空对象是否默认给满分？judge 失败是否静默通过？

### 输出格式

```
[CRITICAL] file:line — 描述
[HIGH] file:line — 描述
[MEDIUM] file:line — 描述
[LOW] file:line — 描述
```

### 补充工具

```bash
# Promptfoo 红队测试（里程碑前手动）
npx promptfoo eval -c promptfoo-redteam.yaml
```

## 运行命令

```bash
# 评测（外呼）
python run_outbound.py scenarios/outbound/<场景>.json --model <模型> [--harness] [--no-llm-judge]

# 多模型对比
python run_outbound.py <场景>.json --compare sonnet,haiku

# 仪表盘
python dashboard.py  # → http://localhost:8765
```
