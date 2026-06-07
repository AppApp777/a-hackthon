# Domain Docs

How the engineering skills should consume A-hackthon's domain documentation when exploring the codebase.

## Layout: single-context

```
/
├── CONTEXT.md             ← 项目领域语言词汇表（待 /grill-with-docs 滴灌生成）
├── CONTRACTS.md           ← 评测系统不变量（评 scorer/harness 前必读）
├── CLAUDE.md              ← 项目硬规则
├── BUGS.md                ← bug 历史档
├── CHANGELOG.md           ← 迭代日志
├── docs/
│   ├── adr/               ← 架构决策记录
│   └── ITERATIONS.md      ← 回顾日志
└── agent-eval/            ← 评测系统子项目
```

## Before exploring, read these

- **`CONTEXT.md`** at the repo root — domain glossary (may not exist yet; `/grill-with-docs` creates it lazily)
- **`CONTRACTS.md`** at the repo root — evaluation system invariants
- **`docs/adr/`** — read ADRs that touch the area you're about to work in

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront. The producer skill (`/grill-with-docs`) creates them lazily when terms or decisions actually get resolved.

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `CONTEXT.md`. Don't drift to synonyms the glossary explicitly avoids.

For A-hackthon specifically, key terms include:
- **scorer / harness / orchestrator / user_sim** — evaluation system components
- **可信事件 (trusted event)** — only data the harness/tools emit; never Agent self-reports
- **设计卡** — pre-implementation design card (problem → solution → data sources → failure modes → tests → files)

If the concept you need isn't in the glossary yet, that's a signal — either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/grill-with-docs`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0007 (xxx) — but worth reopening because…_

For A-hackthon, also flag conflicts with **CONTRACTS.md** invariants — those are evaluation-system contracts and breaking them silently is a CRITICAL bug.
