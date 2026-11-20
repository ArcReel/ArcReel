# 分流与无人值守巡检

手动使用 [triage](../../.agents/skills/triage/SKILL.md) 时按其交互步骤处理，同时使用下文的 ArcReel 范围规则。无人值守巡检按本文推进每个对象，需维护者决定的事项转交维护者。

## 核对当前范围

按请求涉及的概念查[产品定位](../../PRODUCT.md)、相关有效 ADR、[投稿政策](../../CONTRIBUTING.md)与 [`.out-of-scope/`](../../.out-of-scope/)。关闭前引用明确排除该请求的当前来源。既有实现的取舍或相近的拒绝决定只作判断线索，含义不明确时交维护者裁决。

已有权威来源记录排除及理由时，关闭评论引用该来源。维护者拒绝新功能且理由需要长期保留、也没有其他负责文档时，才按 [Out-of-scope 记录格式](../../.agents/skills/triage/OUT-OF-SCOPE.md#file-format)补录。

## 约定

- **评论**：首行写 skill 规定的 AI 标注，第二行写 `<!-- triage-sweep -->`。同一对象的结论没有变化时，沿用已有评论。
- **标签**：按[分流标签](triage-labels.md)保留一个状态标签和一个类别标签。改变状态时替换旧标签。状态冲突的对象只列入巡检摘要，由维护者裁决。
- **外部 PR**：作者的 `author_association` 不是 `OWNER`、`MEMBER` 或 `COLLABORATOR`，且非 bot 提交的 PR。
- **方向**：修复可复现的 bug 或修正文档的外部 PR，方向无需判断；其余的，例如新功能、新供应商、新端点、重构和行为变更，方向待维护者判断。
- **转交**：打 `needs-triage`，评论写明建议状态、理由、验证结果及相关范围决定。外部 PR 另说明方向与接手价值。接手应能解决实际问题，且工作量明显小于重写。维护者替换 `needs-triage` 即给出处置决定。

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

按 [triage](../../.agents/skills/triage/SKILL.md#triage-a-specific-issue-or-pr) 收集上下文并核实请求。无法复现时写明原因。然后取第一条匹配的结果：

1. 请求的行为已经实现：评论指出实现位置，打 `wontfix` 并关闭。
2. 信息不足，或按提供的步骤复现不了：按 skill 的 needs-info 模板发 Triage Notes，打 `needs-info`。
3. 其他情况：转交维护者。

## 3. 外部 PR

依次执行，在得出结果的一步停下：

1. **关闭检查**：请求明确命中当前有效的[范围或投稿限制](#核对当前范围)，或关联 issue 带 `ready-for-agent` 且满足[贡献指南](../../CONTRIBUTING.md)的关闭条件时，评论附依据，打 `wontfix` 并关闭。只有实现取舍或方向相近时，按本节「分流」转交。
2. **CLA**：`license/cla` 状态为 success 的继续下一步。最新提交上没有这个状态时，访问 `https://cla-assistant.io/check/ArcReel/ArcReel?pullRequest=<PR 号>` 触发重新检查，再读一次状态；仍然没有的，不按未签署处理，继续下一步，并把这个链接列入巡检摘要。未签署时发一条签署提醒，同一 PR 只提醒一次，打 `needs-info`。
3. **检查**：按 skill 第 1 步与第 3 步收集上下文并验证 diff，再用 `code-review` skill 审查 diff：固定点取 PR 的 base 分支，spec 取关联 issue，没有关联 issue 时以 PR 正文作为 spec。请求的行为已经实现的，按第 2 步第 1 条关闭；信息不足以验证的，按第 2 步第 2 条打 `needs-info`。
4. **分流**：
   - 方向待判断：转交维护者。评论中不列出 `code-review` 的具体修改意见，待维护者决定后再由第 5 步发给作者。
   - 方向无需判断、满足下面「建议合并」的全部条件：打 `ready-for-human` 与 `merge-ready`，评论写明「建议合并」及依据。
   - 方向无需判断、`code-review` 或验证发现需要修改的问题：修改意见作为 PR 评论发给作者，打 `needs-info`。
   - 方向无需判断的其余 PR：转交维护者，并在评论中写明尚未满足的「建议合并」条件。

### 建议合并

条件：

- 方向无需判断。
- `license/cla`、`ci-required`、`codeql-required` 均为 success。
- `code-review` 与验证均未发现需要修改的问题。
- 改动不涉及敏感路径：定价与计费（`lib/config/`、`lib/billing/`）、模型目录与预设（`lib/catalog/`、`packages/arcreel-catalog-core/`）、界面文案（`lib/i18n/`、`frontend/src/i18n/`）、提示词（`lib/prompts/`、`agent_runtime_profile/`）、数据库与项目迁移（`alembic/`、`lib/project/project_migrations/`）、CI（`.github/`）、依赖清单与锁文件。
- PR 不是草稿。
- 主要目的不是推广商业服务（见 `CONTRIBUTING.md`）。

`merge-ready` 表示巡检已完成审查并验证通过，最终合并由维护者执行。若 PR 出现新提交或必需检查失败，则摘掉 `merge-ready`，从第 3 步第 3 条重新检查。

## 4. 等待作者

带 `needs-info` 的对象按等待的事项处理：

- **等待签署 CLA**：已签署的，摘掉 `needs-info`，从第 3 步第 3 条继续。打上 `needs-info` 超过 14 天仍未签署的，不论期间有无回复，评论说明签署后可以重开，然后关闭。
- **等待补充信息或修改**：提问者或作者在最后一条巡检评论后有了新回复、提交或正文修改的，摘掉 `needs-info`，issue 回到第 2 步，外部 PR 从第 3 步第 3 条重新检查。最后一条巡检评论后超过 14 天没有这类活动的：值得接手的外部 PR 转交；其余的评论说明补充后可以重开，然后关闭。
- **等待的事项已不是本文的要求**：摘掉 `needs-info`，issue 回到第 2 步，外部 PR 从第 3 步第 3 条重新检查。

## 5. 执行维护者的决定

| 标签 | issue | 外部 PR |
| --- | --- | --- |
| `ready-for-agent` | 按 skill 的 AGENT-BRIEF.md 发 agent brief | 方向可以且值得接手：把修改意见发给作者，说明 7 天内没有回应将由维护方接手 |
| `ready-for-human` | 按 skill 写明不能委派的原因 | 无需动作 |
| `needs-info` | 发 Triage Notes，按第 4 步处理 | 方向可以、不接手：把修改意见发给作者，按第 4 步处理 |
| `wontfix` | 附上理由后关闭 | 附上理由后关闭 |
| `parked` | 按维护者的决定记录已搁置，退出巡检 | 按维护者的决定记录已搁置，退出巡检 |

发给作者的修改意见以最新提交上的审查结果为准。关闭理由优先取自维护者的评论，否则沿用已确认的巡检建议。涉及范围决定时引用现有来源，新的拒绝理由列入摘要，由维护者决定是否需要记录。

### 接手

`ready-for-agent` 的外部 PR，从修改意见发出或作者最后一次回复（取较晚者）起 7 天内没有新提交时，由巡检派 subagent 接手。作者在期限内推送新提交时，由作者继续修改，从第 3 步第 3 条重新检查。

接手时，作者允许维护者编辑的，在原 PR 分支上追加提交；否则从最新 `main` 另开 PR，提交中保留 `Co-authored-by` 署名，在原 PR 评论链接新 PR 后关闭原 PR。补改完成后运行 `pr-ai-review-loop`，收敛后把 PR 改为 `ready-for-human`，再按「建议合并」的条件判断是否打 `merge-ready`，敏感路径条件对接手的 PR 不适用。

## 6. 巡检摘要

完成标准：第 1 步清单中的每个对象，都已处于本文某一条规则规定的状态。

结束时用中文输出摘要：自主处理的对象与动作、状态标签冲突的对象、新转交到 `needs-triage` 的对象、执行的维护者决定、接手中的外部 PR 进度、CLA 状态缺失的 PR 及其重新检查链接，以及建议补录到 `.out-of-scope/` 的拒绝理由。
