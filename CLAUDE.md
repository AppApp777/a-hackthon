# A-hackthon — 黑客松全流程开发工作区

> 美团 2026 黑客松，赛道二：复杂指令下的多轮对话评测系统。
> 本目录是项目根，所有子项目（agent-eval 等）继承此处的开发规则。

## 核心原则

> **skill/插件引导行为，hook/测试/CI 强制执行行为。**
> CLAUDE.md 是建议，PreToolUse hook 是铁门。

## 共享审查规则（引用）

> **遵循 `d:\VIBE CODING\.shared-rules\review-rules-full.md` 中的全部规则（完整版）。**
> 包括：双车道开发循环、四种审查 subagent、对抗审查清单、测试策略、迭代日志格式。

## 本项目专属开发协议（在共享规则之上追加）

对 evaluator、scorer、harness、工具执行、评分逻辑的任何改动：

1. **先读 CONTRACTS.md**
2. **不信模型自报**：打分只看工具/harness 可信事件，不看 Agent 自述
3. **失败不算成功**：失败/超时/异常/被拦截的操作都不算成功
4. **每个得分可追溯**：必须指向具体的可信事件
5. **完成标准追加**：契约测试通过 + 重大改动更新 ITERATIONS.md

**对抗审查追加触发文件**：`scorer*.py`、`harness.py`、`diagnosis.py`、`validator.py`、`orchestrator*.py`、`user_sim*.py`

## 运行命令

```bash
# 快速门禁（每次 commit 前自动跑）
bash scripts/quality_gate.sh

# 完整测试
pytest --cov=. --cov-branch

# 对抗测试
pytest tests/adversarial/ -v

# Promptfoo 红队（里程碑前手动）
npx promptfoo eval -c promptfoo-redteam.yaml
```

## 借鉴计划（进行中）

> **每个新会话开头先读 `BORROWING_PLAN.md`**，从第一个未完成的任务开始执行。
> 计划覆盖 15 个成熟项目的技术借鉴，分 5 个 Phase，预估 10-13 个会话完成。

## 关键文件

- `CONTRACTS.md` — 评测系统不变量（改 scorer/harness 前必读）
- `CHANGELOG.md` — 迭代日志（路演素材）
- `CLAIMS.md` — 声明→证据→复现命令
- `LIMITATIONS.md` — 已知局限
- `docs/adr/` — 架构决策记录（ADR-001~003）
- `docs/ITERATIONS.md` — 回顾日志（目标→改动→问题→教训）
- `scripts/quality_gate.sh` — 确定性门禁脚本
- `.claude/hooks/block_bad_commit.py` — Claude commit 拦截器
- `docs/internal/progress.md` — 项目状态
- `docs/internal/BUGS.md` — bug 历史档案
- `docs/internal/BORROWING_PLAN.md` — 成熟项目借鉴计划（25/25 完成）

## Agent skills

为 mattpocock/skills 的工程类 skill（`to-issues` / `triage` / `to-prd` / `diagnose` / `tdd` / `improve-codebase-architecture` / `zoom-out` 等）提供本仓的配置。

### Issue tracker

GitHub Issues at `AppApp777/a-hackthon`（使用 `gh` CLI）。详见 `docs/agents/issue-tracker.md`。

### Triage labels

5 个角色全部使用默认英文标签字符串（`needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`）。**首次使用前需在 GitHub 上建好这些 label**，建法见 `docs/agents/triage-labels.md`。

### Domain docs

单一领域（single-context）。词汇表 `CONTEXT.md`（待生成）+ 不变量档 `CONTRACTS.md` + 决策档 `docs/adr/`。详见 `docs/agents/domain.md`。
