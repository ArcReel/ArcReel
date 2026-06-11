---
name: pr-ai-review-loop
description: 无人值守驱动 CodeRabbit、Gemini Code Assist、OpenAI Codex 与 GitHub Code Quality / Advanced Security(CodeQL)的 review → 修复 → push → 再 review 循环,直到全部通过或触发收敛退出。主动调用:用户刚 push PR 或跑完 /commit-push-pr;提到 review / coderabbit / gemini / codex / codeql / code quality / code security / 审查 / AI review 等 bot 回复;CodeRabbit paused 需 resume;reviewer 有 actionable comments。即使用户只说"PR 怎么样了""review 回了吗"也应触发。
---

# AI Review 自动循环

PR push 之后,多家 reviewer bot 会产出评论。本 skill 调度 review → 修复 → push → 再 review 的循环:监控状态、必要时触发 review、收集评论转交 `receiving-code-review`,直到达成目标状态。首次进入循环时通读 [references/reviewers.md](references/reviewers.md)——每轮判定(已审 / actionable / 通过)全部依赖其中的 per-reviewer 规则。

## 目标状态

循环的唯一正常出口。宣布通过前逐项核对:

1. **本 PR 参审的每家 AI reviewer**(CodeRabbit / Gemini / Codex)对当前 HEAD 通过。CodeRabbit 始终参审,Gemini 由 cold-start fallback 保证参审,Codex 按其触发决策可不参审;fix-up 顺延时沿用上一已审 HEAD 的通过结论(口径见 reviewers.md「通用约定」)
2. **CodeQL 退出门槛**:分析完成且成功、security 无 PR 引入的 open 告警、quality 全量评论逐条已处置——三条细则与"仓库未接入"的跳过口径见 reviewers.md「GitHub code scanning bots」节
3. 循环期间的所有 actionable 评论均已实施修复或记录 pushback

门槛核对(alerts 差集、quality 逐条)成本高,设计上只在其余缺口全部消失后做一次**终核**,不必每轮执行。每轮决策 = 对照目标找缺口 → 执行最小动作集 → 安排下一次唤醒。达不成目标时由「收敛兜底」退出。

## 运行模式:无人值守

自动执行整个循环,无需每轮征求授权:触发命令、push 修复、回应 inline、修复 CI、下一轮 poll 的延迟均自行决定。只有两类场景暂停询问用户——故障类见「故障处理」节,调度类如下:

- **根本性分歧无定论**:同一主题(reviewer + 关键词,例如 "Pydantic `extra=ignore` vs `forbid`")被同一家 reviewer 在 ≥ 3 个 HEAD 上反复提出,且无 ADR / memory 兜底。暂停并请用户决定是否升级 ADR(与「收敛兜底」#3 同口径)
- **reviewer 之间冲突**:同一议题,A 家主张 X、B 家反对 X。暂停并交用户裁决,不自行选边
- **业务取舍**:修复方案在前向兼容、性能、用户体验上存在显著差异,可能影响业务意图。暂停并确认

## 无状态原则

本 skill 不在对话中维护状态账本。每轮判断所需的全部事实——谁审过当前 HEAD、本轮新评论、已进行的轮数、近几轮 commit 形状、主题是否重复——都从本轮 poll 输出与 git 数据现场推导。对话被压缩或会话中断后,重新跑一轮步骤 1 即可完全恢复循环。

现场推导口径(按需推导,不必每轮全做):

- **轮数**:读 poll 输出的 `round_estimate`(PR 创建后的 commits 按 >5 分钟间隔聚类;rebase 会刷新全部日期导致低估,仅作启发),每轮顺手对照收敛兜底阈值
- **commit 形状**:仅在发触发命令前推导——对最近的 push 批次跑 `classify_commits.sh`,SINCE_SHA 取上一批次末 commit 的 `oid`(批次边界从 `commits[*].committedDate` 的间隔看)
- **主题重复**:仅在新评论似曾相识或逼近兜底阈值时推导——通读全量评论历史(inline `body_head` + reviews body),按语义归并主题,看同一主题出现在几个不同 HEAD 上
- **本轮已触发**:`own_trigger_comments` 中该命令最大 `createdAt` 晚于 `last_push_at`(详见 reviewers.md「触发去重」)

## 前置条件

- 当前分支已有对应 PR(`gh pr view` 能读取到 PR 号)且非 draft(draft 时 CodeRabbit 默认不审)。若无 PR,建议先运行 `/commit-commands:commit-push-pr`
- `gh` 已登录且具备评论权限(`gh auth status` 通过);已安装 `jq`
- 仓库已接入 CodeRabbit;Gemini Code Assist / OpenAI Codex / GitHub code scanning 按仓库实际接入情况启用

## 每轮 poll 流程

每轮三步:拉数据 → 对照目标找缺口 → 动作。**不要**用单条长 sleep 阻塞会话,由 ScheduleWakeup 控制节奏。

### 步骤 1:拉取当前状态

```bash
bash .agents/skills/pr-ai-review-loop/scripts/poll.sh <PR_NUMBER>
```

JSON 解析后仅保留在对话上下文中,不落盘。

### 步骤 2:对照目标找缺口

按「目标状态」逐项核对,对每个缺口执行对应动作(同一轮可并行处理多家):

| 缺口 | 动作 |
|---|---|
| `checks_failing` 非空(CI 红) | 就地修复并 push——CI 红会阻塞 reviewer 触发;修不动(重试仍红 / 根因在 main)才暂停询问 |
| 某家参审 reviewer 未审当前 HEAD | 按 reviewers.md 该家「触发」规则决定等待或发触发命令 |
| 至少一家有本轮新 actionable 评论(判定见 reviewers.md) | 进入步骤 3 |
| `security_alerts.open_introduced` 非空但无对应新评论 | 上一轮没修干净(bot 不重复提醒)——把 alert 数据(rule / path / url)直接带入步骤 3,按数据修而非按评论修。前提:CodeQL 分析完成且成功(门槛 1 口径)——分析未完成时差集基于过期数据,归入下行等待 |
| CodeQL 分析未完成 | 等待(不阻塞其它缺口的处理,但阻塞终核——分析完成前不得宣布"缺口均消失") |
| 以上缺口均消失 | 做目标状态**终核**(含 CodeQL 门槛逐条);全过则退出循环并简短汇报,发现遗留则按对应缺口处理 |
| 未全部达成且无可执行动作(reviewer 响应中) | 按「轮询节奏」表等待下一轮 |

**fix-up 跳过**:发触发命令前先跑 `classify_commits.sh`;若本轮 push 全为 fix-up(nit、format、typo、单字段调整、小 bug 修复)**且该家对上一已审 HEAD 已通过**,跳过手动触发 Gemini 与 Codex,沿用其通过结论(顺延口径见 reviewers.md);该家还有未解决评论时不得跳过。本跳过仅作用于需手动触发的 Gemini / Codex——CodeRabbit 自动跟审每次 push,不在跳过范围,最终 HEAD 始终有它过目。例外:Gemini cold-start fallback 不受此限(该场景下整个 PR 还没经过任何 Gemini review)。

执行完触发动作后,按「轮询节奏」表选择延迟,调用 `ScheduleWakeup`。

### 步骤 3:收集评论并转交 receiving-code-review

将所有 reviewer 的本轮新评论**合并为一次调用**,通过 Skill 工具调用 `receiving-code-review`。不要每家单独调用:`receiving-code-review` 以一次修复 push 收尾,分家调用意味着多次 push,而每次 push 都会让全部 reviewer 重审一轮——轮数膨胀、quota 加倍。

- Gemini 的 `gemini.reviews[*].body`(summary)整段贴入上下文——某些建议仅出现在 summary 中,inline 部分为空;只贴 inline 会丢失内容。`receiving-code-review` 与本 skill 共享 context,只有把 summary body 摆在对话中它才能读到
- GitHub code scanning 两家(quality / security)的评论一并转交,全部视为 actionable;修复后**不回 inline**(bot 不读回复),pushback 落点为 PR 评论说明或 dismiss alert
- `body_head` 只有 400 字符,语义被截断时按评论 id 用 `gh api` 拉全文再转交

`receiving-code-review` 调用完成后回到步骤 1。

## 轮询节奏

每轮 poll 与决策完成后,调用 `ScheduleWakeup` 安排下一次唤醒。唤醒 prompt 写明 skill 名与 PR 号,可附上一轮动作摘要(例:"继续 pr-ai-review-loop:对 PR #N 重跑 poll.sh 走步骤 1;上轮已发 /gemini review 等响应")。重入语义:判定所需的全部事实由唤醒后的现场 poll 重建(无状态原则),摘要只是省一次推导,不作判定依据——即使唤醒发生在上下文压缩之后,重跑步骤 1 即可继续。延迟取值:

| 场景 | 延迟 | 备注 |
|---|---|---|
| 新 HEAD 后首次 poll | 180s | reviewer cold-start;CR 通常 60-90s 跟新 HEAD;Gemini 仅 PR opened 自动 review;CodeQL 分析需数分钟 |
| 发送 `/gemini review` 或 `@codex review` 之后 | 120s | Gemini 响应通常 90-120s,60s 容易错过 |
| 常规等待(reviewer 响应中) | 60s | 处于 prompt cache 5 分钟窗口内 |
| 仅剩 CodeQL 分析未完成 | 120s | 等 check 完成做终核 |
| 超过 15 分钟无响应 | 暂停并询问用户,不再 ScheduleWakeup | 见「故障处理」 |

## 收敛兜底

下列任一条件触发退出:

1. `round_estimate` ≥ 8 → 暂停询问"已 8 轮,merge / 继续 / 放弃?"
2. 连续 2 轮 push 全为 nit / format 形状(跑 `classify_commits.sh` 看最近两批)→ 暂停询问"边际收益已降低,是否结束?"
3. 同一主题在 ≥ 3 个 HEAD 上被反复提出(与「运行模式」调度类暂停联动)→ 暂停询问是否升级 ADR
4. 目标状态全部达成 → 正常退出

## 故障处理

条件与处置一一对应,均暂停询问用户(无人值守的例外面):

- **某家 reviewer(含 CodeQL 分析)超过 15 分钟未响应**:bot 可能服务异常或配额已满,暂停说明现状。fix-up 顺延导致的"未审"不算无响应——那是设计内跳过
- **bot 报错**(如 "Internal error"、"Token limit exceeded"):贴出错误内容,询问是否强制重跑(`@coderabbitai full review` 或 `/gemini review`)
- **`quota_alerts` 非空**:bot 留下了 quota / rate limit 报错,贴出 `body_head`,询问停用该家继续其他家,还是等 quota 恢复后再 push
- **`codeql_checks` 有 conclusion 为 failure / cancelled / timed_out**:分析失败,alerts 数据停留在上次成功分析,不能做终核;询问是否重跑失败的 workflow
- **`security_alerts.available == false`**:贴出 `unavailable_hint`,按 reviewers.md「仓库未接入」段判别权限问题与未接入——两种情形都需用户确认,不得自动跳过 security 门槛
- **`gh` 401/403**:请用户运行 `gh auth refresh -s repo`
- **脚本报 `POLL_ERROR:`**:重试一次(网络抖动常见),再失败贴出 stderr
- **review 评论语义模糊**,`receiving-code-review` 无法判定是否 pushback:贴出原文请用户定夺

## 与其他 skill 的分工

| 任务 | 对应 skill |
|---|---|
| 创建 PR | `commit-commands:commit-push-pr` |
| 回应 / 实施 / 反驳 review 评论 | `receiving-code-review` |
| 验证修复是否真的解决问题 | `verify` |
| **循环调度、CI 修复、终核与退出** | **本 skill** |

本 skill 不负责"回应评论"与"验证修复";CI 红的就地修复属于调度职责(不是 review 评论,不转交)。
