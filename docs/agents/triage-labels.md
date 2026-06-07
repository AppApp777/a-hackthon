# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

## 首次使用前必须建好这些 label

GitHub repo 默认没有这些 label，第一次跑 triage skill 前要建：

```bash
gh label create needs-triage --color "fbca04" --description "Maintainer needs to evaluate"
gh label create needs-info --color "d93f0b" --description "Waiting on reporter"
gh label create ready-for-agent --color "0e8a16" --description "Fully specified, AFK agent ready"
gh label create ready-for-human --color "1d76db" --description "Requires human implementation"
gh label create wontfix --color "ffffff" --description "Will not be actioned"
```

如果未来想换 label 名（如 `bug:triage` 替代 `needs-triage`），改右列即可，skill 会用右列字符串。
