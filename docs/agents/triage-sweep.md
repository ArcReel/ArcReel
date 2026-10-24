# 无人值守 triage

每日 triage 巡检按本文运行 `.agents/skills/triage/SKILL.md`。skill 中「给出建议后等待维护者指示」的交互步骤，在巡检中由本文的分流规则代替。维护者手动调用 `/triage` 时按 skill 原样交互。

巡检发出的每条评论，首行写 skill 规定的 AI 标注，第二行写 `<!-- triage-sweep -->`。同一对象的结论没有变化时，沿用已有评论。巡检改变对象状态时，用新的状态标签替换原有状态标签，每个对象只保留一个状态标签。

## 1. 选取对象

本次巡检的对象是：

- 没有状态标签的 issue 和外部 PR。外部 PR 指作者的 `author_association` 不是 `OWNER`、`MEMBER` 或 `COLLABORATOR`，且不是 bot 的 PR。
- 带 `needs-info` 的 issue 和外部 PR。
- 维护者已把 `needs-triage` 换成其他状态标签、但还没有对应巡检评论的 issue 和外部 PR。
- 已发出修改意见的 `ready-for-agent` 外部 PR，以及巡检接手中的 PR：只按第 5 步「接手」核对期限或推进接手。
- 带 `merge-ready` 的外部 PR：只按「可直接合并」核对是否出现新提交或必需检查失败。

`needs-triage` 是维护者的待决队列：巡检把对象转交进去，由维护者换标签做出决定。

同时带有多个状态标签的对象只列入巡检摘要，由维护者处理冲突。

完成标准：对象清单已列出，每个对象标明属于哪一类。

## 2. 外部 PR 的前置检查

外部 PR 的请求明显命中 `.out-of-scope/` 条目时，评论引用该条目，打 `wontfix` 并关闭，不再做以下检查。

其余外部 PR 在审查前依次检查以下两项。未通过的 PR 停在这一步，打 `needs-info`；`needs-info` 超过 14 天仍未通过的，不论期间有无回复，评论说明补齐后可以重开，然后关闭：

1. **CLA**：`license/cla` 状态为 success。PR 的最新提交上没有这个状态时，先访问 `https://cla-assistant.io/check/ArcReel/ArcReel?pullRequest=<PR 号>` 触发重新检查，再读状态；仍然没有状态的，不按未签署处理，列入巡检摘要。未通过时发一条签署提醒；同一 PR 只提醒一次。
2. **先讨论方向**：新功能、新供应商、新端点，或增删合计超过 200 行的改动，需要有关联 issue。没有时，请作者先开 issue 讨论方向；只因增删行数超限时，也可以把 PR 拆小。

曾因前置检查打上 `needs-info` 的 PR 通过两项检查后，摘掉 `needs-info`，继续第 3 步。

## 3. 检查

按 skill「Triage a specific issue or PR」第 1 步与第 3 步收集上下文：读完整正文与评论，查是否已有实现，对照 `.out-of-scope/`，复现 bug 或验证 PR 的 diff。复现不了时，在记录中写明原因。外部 PR 另用 `code-review` skill 审查 diff：固定点取 PR 的 base 分支，spec 取关联 issue，没有关联 issue 时以 PR 正文作为 spec。

## 4. 分流

第 1 步中维护者已换上状态标签的对象，直接按第 5 步执行。其余对象按下表取第一条匹配的结果。

| 情况 | 处理 |
| --- | --- |
| 请求的行为已经实现 | 评论指出实现位置，打 `wontfix` 并关闭 |
| `needs-info` 超过 14 天，提问者或作者没有回复、提交或修改正文 | 评论说明补充信息后可以重开，然后关闭 |
| 信息不足，或按提供的步骤复现不了 | 按 skill 的 needs-info 模板发 Triage Notes，打 `needs-info` |
| `needs-info` 期间提问者或作者有了新回复、提交或正文修改 | 摘掉 `needs-info`，回到第 3 步重新检查 |
| 外部 PR 满足「可直接合并」的全部条件 | 打 `ready-for-human` 与 `merge-ready`，评论写明「建议合并」及依据 |
| 其他 | 转交维护者 |

所有检查过的对象都打类别标签 `bug` 或 `enhancement`。外部 PR 的 `code-review` 意见作为 PR 评论发给作者。

### 可直接合并

外部 PR 同时满足以下条件：

- 只改 Markdown 文档，或是增删合计不超过 50 行的 bug 修复。
- `license/cla`、`ci-required`、`codeql-required` 均为 success。
- `code-review` 与验证均未发现需要修改的问题。
- 改动不涉及敏感路径：定价与计费（`lib/config/`、`lib/billing/`）、模型目录与预设（`lib/catalog/`、`packages/arcreel-catalog-core/`）、界面文案（`lib/i18n/`、`frontend/src/i18n/`）、提示词（`lib/prompts/`、`agent_runtime_profile/`）、数据库与项目迁移（`alembic/`、`lib/project/project_migrations/`）、CI（`.github/`）、依赖清单与锁文件。
- PR 不是草稿。
- 主要目的不是推广商业服务（见 `CONTRIBUTING.md`）。

`merge-ready` 表示巡检已审查并验证通过，合并由维护者执行。PR 出现新提交或必需检查失败时，摘掉 `merge-ready`，回到第 3 步。

### 转交维护者

打 `needs-triage`，评论写明：

- 建议的类别与状态（`ready-for-agent`、`ready-for-human`、`needs-info` 或 `wontfix`）及理由。
- 复现或验证结果。
- 相近的 `.out-of-scope/` 条目（有则引用）。
- 外部 PR 另写：方向是否可以接受、质量问题清单、是否值得接手及理由。值得接手指解决的问题有实际价值，且补改工作量明显小于重写。

## 5. 执行维护者的决定

维护者换上的状态标签决定巡检的动作：

| 标签 | issue | 外部 PR |
| --- | --- | --- |
| `ready-for-agent` | 按 skill 的 AGENT-BRIEF.md 发 agent brief | 方向可以且值得接手：把修改意见发给作者，说明 7 天内没有回应将由维护方接手 |
| `ready-for-human` | 按 skill 写明不能委派的原因 | 无需动作 |
| `needs-info` | 按第 4 步的 needs-info 规则处理 | 方向可以、不接手：把修改意见发给作者，之后按第 4 步的 needs-info 规则处理 |
| `wontfix` | 附上理由后关闭 | 附上理由后关闭 |

关闭理由取自维护者在该对象上的评论；没有评论时，沿用巡检此前建议中的理由，命中 `.out-of-scope/` 条目时引用该条目。新出现的拒绝理由列入巡检摘要，由维护者决定是否补录到 `.out-of-scope/`。

**接手**：`ready-for-agent` 的外部 PR，从修改意见发出或作者最后一次回复（取较晚者）起 7 天内没有新提交时，由巡检派 subagent 接手。作者允许维护者编辑时，在原 PR 分支上追加提交；否则从最新 `main` 另开 PR，提交中保留 `Co-authored-by` 署名，在原 PR 评论链接新 PR 后关闭原 PR。补改完成后运行 `pr-ai-review-loop`，收敛后把 PR 改为 `ready-for-human`，再按「可直接合并」的条件判断是否打 `merge-ready`，敏感路径条件对接手的 PR 不适用。作者在期限内推送新提交时，由作者继续修改，巡检回到第 3 步。

## 6. 巡检摘要

完成标准：第 1 步清单中的每个对象，都已处于本文某一条规则规定的状态。

结束时用中文输出摘要：自主处理的对象与动作、状态标签冲突的对象、新转交到 `needs-triage` 的对象、执行的维护者决定、接手中的外部 PR 进度，以及建议补录到 `.out-of-scope/` 的拒绝理由。
