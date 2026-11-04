# 无人值守 triage

每日 triage 巡检按本文运行 `.agents/skills/triage/SKILL.md`。巡检把每个对象推进到下一个状态，只把需要判断的事转交维护者；skill 中「给出建议后等待维护者指示」的交互步骤，由本文的规则代替。维护者手动调用 `/triage` 时按 skill 原样交互。

## 约定

- **评论**：首行写 skill 规定的 AI 标注，第二行写 `<!-- triage-sweep -->`。同一对象的结论没有变化时，沿用已有评论。
- **状态标签**：每个对象只保留一个状态标签；改变状态时，用新标签替换旧标签。同时带有多个状态标签的对象只列入巡检摘要，由维护者处理冲突。所有检查过的对象都打类别标签 `bug` 或 `enhancement`。
- **外部 PR**：作者的 `author_association` 不是 `OWNER`、`MEMBER` 或 `COLLABORATOR`，且不是 bot 的 PR。
- **方向**：修复可复现的 bug 或修正文档的外部 PR，方向无需判断；其余的，例如新功能、新供应商、新端点、重构和行为变更，方向待维护者判断。
- **转交**：打 `needs-triage`，评论写明建议的状态（`ready-for-agent`、`ready-for-human`、`needs-info` 或 `wontfix`）及理由、复现或验证结果、相近的 `.out-of-scope/` 条目。外部 PR 另写方向是否可以接受，以及是否值得接手：解决的问题有实际价值，且补改工作量明显小于重写。`needs-triage` 是维护者的待决队列，维护者换上其他状态标签即做出决定。

## 1. 选取对象

每个对象按表中第一条匹配的行处理。

| 对象 | 处理 |
| --- | --- |
| 维护者已把 `needs-triage` 换成其他状态标签、但还没有对应巡检评论的对象 | 第 5 步 |
| 没有状态标签的 issue | 第 2 步 |
| 没有状态标签的外部 PR | 第 3 步 |
| 带 `needs-info` 的 issue 和外部 PR | 第 4 步 |
| 已发出修改意见的 `ready-for-agent` 外部 PR，以及巡检接手中的 PR | 第 5 步「接手」 |
| 带 `merge-ready` 的外部 PR | 第 3 步「建议合并」的失效规则 |

完成标准：对象清单已列出，每个对象标明对应的处理。

## 2. issue

按 skill「Triage a specific issue or PR」第 1 步与第 3 步收集上下文：读完整正文与评论，查是否已有实现，对照 `.out-of-scope/`，bug 按报告的步骤复现。复现不了时，在记录中写明原因。然后取第一条匹配的结果：

1. 请求的行为已经实现：评论指出实现位置，打 `wontfix` 并关闭。
2. 信息不足，或按提供的步骤复现不了：按 skill 的 needs-info 模板发 Triage Notes，打 `needs-info`。
3. 其他：转交。

## 3. 外部 PR

依次执行，在得出结果的一步停下：

1. **关闭检查**：请求明显命中 `.out-of-scope/` 条目，或关联 issue 带 `ready-for-agent`（理由见 `CONTRIBUTING.md`）时，评论引用对应条目或理由，打 `wontfix` 并关闭。
2. **CLA**：`license/cla` 状态为 success 才继续。最新提交上没有这个状态时，先访问 `https://cla-assistant.io/check/ArcReel/ArcReel?pullRequest=<PR 号>` 触发重新检查，再读状态；仍然没有状态的，不按未签署处理，继续下一步并列入巡检摘要。未签署时发一条签署提醒，同一 PR 只提醒一次，打 `needs-info`。
3. **检查**：按 skill 第 1 步与第 3 步收集上下文并验证 diff，再用 `code-review` skill 审查 diff：固定点取 PR 的 base 分支，spec 取关联 issue，没有关联 issue 时以 PR 正文作为 spec。请求的行为已经实现的，按第 2 步第 1 条关闭；信息不足以验证的，按第 2 步第 2 条打 `needs-info`。
4. **分流**：
   - 方向待判断：转交。评论不列 `code-review` 的具体修改意见，这些意见在维护者做出决定后由第 5 步发给作者。
   - 方向无需判断、满足下面「建议合并」的全部条件：打 `ready-for-human` 与 `merge-ready`，评论写明「建议合并」及依据。
   - 方向无需判断的其余 PR：`code-review` 意见作为 PR 评论发给作者，然后转交。

### 建议合并

条件：

- 方向无需判断。
- `license/cla`、`ci-required`、`codeql-required` 均为 success。
- `code-review` 与验证均未发现需要修改的问题。
- 改动不涉及敏感路径：定价与计费（`lib/config/`、`lib/billing/`）、模型目录与预设（`lib/catalog/`、`packages/arcreel-catalog-core/`）、界面文案（`lib/i18n/`、`frontend/src/i18n/`）、提示词（`lib/prompts/`、`agent_runtime_profile/`）、数据库与项目迁移（`alembic/`、`lib/project/project_migrations/`）、CI（`.github/`）、依赖清单与锁文件。
- PR 不是草稿。
- 主要目的不是推广商业服务（见 `CONTRIBUTING.md`）。

`merge-ready` 表示巡检已审查并验证通过，合并由维护者执行。PR 出现新提交或必需检查失败时，摘掉 `merge-ready`，从第 3 步第 3 条重新检查。

## 4. 等待作者

带 `needs-info` 的对象按等待的事项处理：

- **等待签署 CLA**：已签署的，摘掉 `needs-info`，从第 3 步第 3 条继续。打上 `needs-info` 超过 14 天仍未签署的，不论期间有无回复，评论说明签署后可以重开，然后关闭。
- **等待补充信息或修改**：提问者或作者在最后一条巡检评论后有了新回复、提交或正文修改的，摘掉 `needs-info`，issue 回到第 2 步，外部 PR 从第 3 步第 3 条重新检查。最后一条巡检评论后超过 14 天没有这类活动的，评论说明补充后可以重开，然后关闭。
- **等待的事项已不是本文的要求**：摘掉 `needs-info`，issue 回到第 2 步，外部 PR 从第 3 步第 3 条重新检查。

## 5. 执行维护者的决定

| 标签 | issue | 外部 PR |
| --- | --- | --- |
| `ready-for-agent` | 按 skill 的 AGENT-BRIEF.md 发 agent brief | 方向可以且值得接手：把修改意见发给作者，说明 7 天内没有回应将由维护方接手 |
| `ready-for-human` | 按 skill 写明不能委派的原因 | 无需动作 |
| `needs-info` | 发 Triage Notes，按第 4 步处理 | 方向可以、不接手：把修改意见发给作者，按第 4 步处理 |
| `wontfix` | 附上理由后关闭 | 附上理由后关闭 |

关闭理由取自维护者在该对象上的评论；没有评论时，沿用巡检此前建议中的理由，命中 `.out-of-scope/` 条目时引用该条目。新出现的拒绝理由列入巡检摘要，由维护者决定是否补录到 `.out-of-scope/`。

### 接手

`ready-for-agent` 的外部 PR，从修改意见发出或作者最后一次回复（取较晚者）起 7 天内没有新提交时，由巡检派 subagent 接手。作者在期限内推送新提交时，由作者继续修改，从第 3 步第 3 条重新检查。

接手时，作者允许维护者编辑的，在原 PR 分支上追加提交；否则从最新 `main` 另开 PR，提交中保留 `Co-authored-by` 署名，在原 PR 评论链接新 PR 后关闭原 PR。补改完成后运行 `pr-ai-review-loop`，收敛后把 PR 改为 `ready-for-human`，再按「建议合并」的条件判断是否打 `merge-ready`，敏感路径条件对接手的 PR 不适用。

## 6. 巡检摘要

完成标准：第 1 步清单中的每个对象，都已处于本文某一条规则规定的状态。

结束时用中文输出摘要：自主处理的对象与动作、状态标签冲突的对象、新转交到 `needs-triage` 的对象、执行的维护者决定、接手中的外部 PR 进度、CLA 状态缺失的 PR，以及建议补录到 `.out-of-scope/` 的拒绝理由。
