---
name: pr-ai-review-loop
description: 无人值守驱动 PR 的 review → 修复 → push → 再 review 循环,直到全部 AI reviewer 通过或触发收敛退出。仅当用户明确要求运行或继续该编排循环,或本地会话刚完成 PR push 后要求继续收敛时调用;作为 GitHub reviewer 审查代码、仅阅读 PR 或处理单条 review 意见时不调用。
---

# AI Review 自动循环

本 skill 监控 reviewer 状态、必要时触发 review、收集评论转交 `receiving-code-review`。进入循环前:确认当前分支已有非 draft 的 PR(draft 时 CodeRabbit 默认不审;无 PR 时交用户决定是否创建,不代为提交),并通读 [references/reviewers.md](references/reviewers.md)——每轮判定(已审 / actionable / 通过)全部依赖其中的 per-reviewer 规则。

## 目标状态

循环的唯一正常出口。宣布通过前逐项核对:

1. **本 PR 参审的每家 AI reviewer**(CodeRabbit / Gemini / Codex)通过。CodeRabbit 与 Codex 审过当前 HEAD;Gemini 由 cold-start fallback 保证参审,可按 fix-up 顺延沿用上一已审 HEAD 的通过结论(口径见 reviewers.md「通用约定」)
2. **CodeQL 退出门槛**:分析完成且成功、security 无 PR 引入的 open 告警、quality 全量评论逐条已处置——三条细则与"仓库未接入"的跳过口径见 reviewers.md「GitHub code scanning bots」节
3. 循环期间的所有 actionable 评论均已实施修复或记录 pushback。终核时对三家 AI reviewer 各跑一次 `query.sh unacked <bot[bot]>` 核对历史 inline;再跑 `query.sh history`,从三家的 review / 顶层评论中取本循环尚未核对的 id,用 `query.sh details <id>...` 批量读全文。两处发现的每条 actionable 均须已修复或有在案 pushback

## 运行模式:无人值守

自动执行整个循环,无需每轮征求授权:触发命令、push 修复、回应 inline、修复 CI、下一轮 poll 的延迟均自行决定。只有两类场景暂停询问用户——故障类见「故障处理」节,调度类如下:

- **根本性分歧无定论**——同一主题反复重提的判定与动作见「收敛兜底」#3
- **reviewer 之间冲突**:同一议题,A 家主张 X、B 家反对 X。暂停并交用户裁决,不自行选边
- **业务取舍**:修复方案在前向兼容、性能、用户体验上存在显著差异,可能影响业务意图。暂停并确认

## 每轮 poll 流程

每轮三步:拉数据 → 对照目标找缺口 → 动作。

### 步骤 1:拉取当前状态

```bash
bash .agents/skills/pr-ai-review-loop/scripts/poll.sh <PR_NUMBER>
```

stdout 是最小索引:本轮新评论带索引行(id / 判定 flags / 120 字符预览),旧评论折叠为 per-bot 计数,正文一律不内联;字段语义见 poll.sh header。索引与上一轮无差异时,stdout 折叠为单行 `no_change`(`unchanged_since` 即上次全量打印时刻)——决策沿用上下文中已有的索引,上下文已丢失(如压缩后)时用 `index` 子命令重印。完整快照(含全文)落盘在 `snapshot_file`,正文详情按需查询:

```bash
bash .agents/skills/pr-ai-review-loop/scripts/query.sh <PR_NUMBER> <子命令>
```

子命令:`details <id>...`(按 id 批量取全文)/ `gemini-latest-body` / `quality-all`(终核)/ `history`(主题重复及终核枚举 review / 顶层评论)/ `unacked <bot[bot]>`(终核或 fix-up 顺延时核对历史 inline;bot 名带 `[bot]` 后缀,如 `chatgpt-codex-connector[bot]`)/ `index`(重印上轮全量索引)。查询异常一律以 `QUERY_ERROR` 响亮失败——空结果因此可以放心当作确无数据。

### 步骤 2:对照目标找缺口

按「目标状态」逐项核对,对每个缺口执行对应动作(同一轮可并行处理多家):

| 缺口 | 动作 |
|---|---|
| `checks_failing` 非空(CI 红) | 就地修复并 push——CI 红会阻塞 reviewer 触发;修不动(重试仍红 / 根因在 main)才暂停询问 |
| 某家参审 reviewer 未审当前 HEAD | 按 reviewers.md 该家「触发」规则决定等待或发触发命令 |
| 至少一家有本轮新 actionable 评论(判定见 reviewers.md) | 进入步骤 3 |
| `security_alerts.open_introduced` 非空但无对应新评论 | 上一轮没修干净(bot 不重复提醒)——把 alert 数据(rule / path / url)直接带入步骤 3,按数据修而非按评论修。前提:CodeQL 分析完成且成功(门槛 1 口径)——分析未完成时差集基于过期数据,归入下行等待 |
| CodeQL 分析未完成(`codeql_checks.all_ok == false` 且 `failing` 为空) | 等待(不阻塞其它缺口的处理,但阻塞终核——分析完成前不得宣布"缺口均消失") |
| 以上缺口均消失 | 做目标状态**终核**(含 CodeQL 门槛与 `unacked` 兜底逐条);全过则按「收敛兜底」#4 正常退出;发现遗留则按对应缺口处理 |
| 未全部达成且无可执行动作(reviewer 响应中) | 按「轮询节奏」表等待下一轮 |

**fix-up 顺延**:仅在决定是否重触发 Gemini 前,对最近的 push 批次跑 `classify_commits.sh`(SINCE_SHA 取上一批次末 commit 的 `oid`;批次边界从索引 `commits_since_pr_created` 的间隔看,首批次以 `base_oid` 为界),按 reviewers.md「通用约定」判定是否沿用 Gemini 结论。

执行完触发动作后,按「轮询节奏」表选择延迟,调用 `ScheduleWakeup`。

### 步骤 3:收集评论并转交 receiving-code-review

按索引挑出本轮新 actionable 条目(判定见 reviewers.md),用 `query.sh details <id>...` 一次批量取全文;Gemini 最新 summary 的 `has_pass_marker == false` 时再取 `gemini-latest-body` 整段——某些建议仅出现在 summary 中,inline 部分为空。将所有 reviewer 的本轮新评论**合并为一次调用**,通过 Skill 工具调用 `receiving-code-review`——分家调用意味着多次 push,而每次 push 都会让全部 reviewer 重审一轮。

GitHub code scanning 两家(quality / security)的评论一并转交,处置口径(全部 actionable、修复与 pushback 落点)见 reviewers.md「GitHub code scanning bots」节。

`receiving-code-review` 调用完成后回到步骤 1。

## 轮询节奏

每轮 poll 与决策完成后,调用 `ScheduleWakeup` 安排下一次唤醒,唤醒 prompt 写明 skill 名与 PR 号;唤醒后按已加载的本文继续步骤 1,无需重新调用 Skill 工具。延迟取值:

| 场景 | 延迟 | 备注 |
|---|---|---|
| 新 HEAD 后首次 poll | 180s | reviewer cold-start |
| 发送 `/gemini review` 或首次 cold-start fallback `@codex review` 之后 | 120s | |
| 常规等待(reviewer 响应中) | 60s | |
| 仅剩 CodeQL 分析未完成 | 120s | 等 check 完成做终核 |
| 超过 25 分钟无响应 | 暂停并询问用户,不再 ScheduleWakeup | 见「故障处理」 |

## 收敛兜底

下列任一条件触发退出:

1. `round_estimate` ≥ 8 → 暂停询问"已 8 轮,merge / 继续 / 放弃?"
2. 连续 2 轮 push 全为 nit / format 形状(跑 `classify_commits.sh` 看最近两批;口径故意比 fix-up 顺延的五类窄,typo / 单字段调整 / 小 bug 修复不算)→ 暂停询问"边际收益已降低,是否结束?"
3. 同一主题(reviewer + 关键词,例如 "Pydantic `extra=ignore` vs `forbid`")被同一家 reviewer 在 ≥ 3 个 HEAD 上反复提出,且无 ADR / memory 兜底 → 暂停询问是否升级 ADR。新评论似曾相识时跑 `query.sh <PR> history` 通读评论历史,按语义归并主题,数同一主题出现在几个 HEAD 上
4. 目标状态全部达成 → 正常退出,按 [references/retrospective.md](references/retrospective.md) 产出复盘随汇报交出(何种出口产复盘以该文件开篇为准)

## 故障处理

条件与处置一一对应,除能力证伪外均暂停询问用户(无人值守的例外面):

**能力证伪自裁决**(唯一不暂停的故障):reviewer 的回复或官方通知确证其无法参审——App 未接入、要求创建/连接账号、服务已停止——该家本 PR 按不参审处理、不再触发,记入退出汇报;沉默或一般报错不算证伪,仍按下列条目暂停询问。

- **某家 reviewer(含 CodeQL 分析)超过 25 分钟未响应**:bot 可能服务异常或配额已满,暂停说明现状。Gemini fix-up 顺延导致的"未审"不算无响应——那是设计内跳过
- **bot 报错**(如 "Internal error"、"Token limit exceeded"):贴出错误内容,按 reviewers.md 该家的触发约束询问是否重跑
- **`quota_alerts` 非空**:bot 留下了 quota / rate limit 报错,贴出 `body_head`,询问停用该家继续其他家,还是等 quota 恢复后再 push
- **`codeql_checks.failing` 非空**(失败态集合见 poll.sh header `checks_failing` 条):分析失败,alerts 数据停留在上次成功分析,不能做终核;询问是否重跑失败的 workflow
- **`security_alerts.available == false`**:贴出 `unavailable_hint`,按 reviewers.md「仓库未接入」段判别权限问题与未接入——两种情形都需用户确认,不得自动跳过 security 门槛
- **`gh` 401/403**:请用户运行 `gh auth refresh -s repo`
- **review 评论语义模糊**,`receiving-code-review` 无法判定是否 pushback:贴出原文请用户定夺
