# Issue tracker: GitHub

Issues and PRDs for A-hackthon live as GitHub issues at `AppApp777/a-hackthon`. Use the `gh` CLI for all operations.

## Conventions

- **Create an issue**: `gh issue create --title "..." --body "..."`. Use a heredoc for multi-line bodies.
- **Read an issue**: `gh issue view <number> --comments`, filtering comments by `jq` and also fetching labels.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` with appropriate `--label` and `--state` filters.
- **Comment on an issue**: `gh issue comment <number> --body "..."`
- **Apply / remove labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **Close**: `gh issue close <number> --comment "..."`

Infer the repo from `git remote -v` — `gh` does this automatically when run inside a clone.

## When a skill says "publish to the issue tracker"

Create a GitHub issue.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --comments`.

## 本项目的特殊约定

- **issue 标题用中文**没问题，正文也可以。但 label 必须用 `docs/agents/triage-labels.md` 里定义的英文标签。
- **不替代 BUGS.md**：bug 修复仍然先入 BUGS.md（项目级历史档），需要追踪进度或多人协作时才升级为 GitHub issue。
- **不替代 progress.md**：progress.md 是项目级长期状态滴灌，issue 是离散工作项。
- **黑客松提交后**：评审反馈直接以 issue 形式留档，方便后续迭代。
