# Reviewer 速查

循环决策依赖各家 bot 的状态信号,每家表达「已审 / 有 actionable 评论 / 已通过」的方式不同。本文件按 reviewer 聚合每家的全部规则(身份、触发、已审判定、actionable、通过信号);SKILL.md 只保留与 reviewer 无关的循环骨架。

## 通用约定

- **本轮新评论**:`created_at > last_push_at`。不要用 `commit_id == head` 判断——CodeRabbit 重审新 HEAD 时会改写旧 inline 的 `commit_id`,`created_at` 才是逐评论稳定的
- **Acknowledgment 例外**:`inline_comments_by_user.*` 中 `is_ack == true` 的条目是 reviewer 对上一次修复或 inline 回复的确认,一律**不算** actionable;review state 为 `APPROVED` 也不算
- **fix-up 顺延**:某家对上一已审 HEAD 已通过,且其后的 push 全为 fix-up(见 SKILL.md「fix-up 跳过」)时,沿用该通过结论参与目标判定,不触发重审——CodeRabbit 自动跟审每次 push,最终 HEAD 始终至少有它过目。**「上一已审 HEAD」指该家最近一次实际审过的 commit,不是最近一次通过的 commit**——若该家最近审的 HEAD 未通过(如 A 通过 → B 有 actionable → C fix-up,最近审的是 B),前提不满足,不得顺延;「其后」从该已审 HEAD 的下一个 commit 起算,到当前 HEAD 为止须全为 fix-up。该家还有未解决评论时同样不得跳过,必须正常触发重审
- **触发去重**:同一 HEAD 上每种触发命令只发一次。在 `own_trigger_comments` 中取该命令最大 `createdAt`,晚于 `last_push_at` 即视为本轮已触发,跳过(`@coderabbitai resume` 例外:以 CodeRabbit 节的 `updated_at` 口径为准)。poll.sh 按**前缀**匹配:评论以命令开头即被收录(`/gemini review 补充说明` 算;`考虑过 /gemini review` 这类中段提及不算)。发触发命令时仍应只写命令本身,且命令必须在评论最开头——前导只容空格/制表符(不容换行),空首行之后或第二行起的命令不会被识别
- **纯指标类 bot 不纳入循环**:`codecov[bot]` 等纯指标类 bot 没有意见可实施,也没有等待或重审的概念

## 总表

| Reviewer | GraphQL `author.login` | REST `user.login` | 自动 review 时机 | 触发命令 |
|---|---|---|---|---|
| CodeRabbit | `coderabbitai` | `coderabbitai[bot]` | PR opened 及后续每次 push | `@coderabbitai resume` / `review` / `full review` |
| Gemini Code Assist | `gemini-code-assist` | `gemini-code-assist[bot]` | **仅 PR opened**(5 分钟内出结果) | `/gemini review` |
| OpenAI Codex(未接入,不参审) | `chatgpt-codex-connector` | `chatgpt-codex-connector[bot]` | 不适用(未接入) | **不触发** |
| GitHub Code Quality | —(只发 inline) | `github-code-quality[bot]` | 每次 push 后的 CodeQL 分析 | **不可触发** |
| GitHub Advanced Security | —(只发 inline) | `github-advanced-security[bot]` | 同上 | **不可触发** |

本仓库未接入 Codex GitHub App:循环不发送 `@codex review`、不轮询或等待其响应、达标判定与 fix-up 跳过规则均不涉及 Codex。`## OpenAI Codex(未接入,不参审)` 节保留判定细则作日后接入时的参考。

## CodeRabbit

**状态表达**:反复改写首条评论(walkthrough),`updated_at` 被推后,body 开头带 `<!-- ... summarize by coderabbit.ai -->` HTML 注释。通过时 body 首行为 `No actionable comments were generated in the recent review. 🎉`。其余 reply 为独立会话评论。

**触发**:`coderabbit.walkthrough.is_paused == true`,且 `updated_at` 之后未发送过 `@coderabbitai resume`(从 `own_trigger_comments` 筛,最新一条 `createdAt` 早于 walkthrough 的 `updated_at`;为空视为未发送)→ 发送 `@coderabbitai resume`。其余场景 CodeRabbit 自动跟新 push,无需手动触发。

**已审当前 HEAD**:`coderabbit.walkthrough.updated_at > last_push_at`。

**actionable**:`walkthrough.is_ok == true` 或 `actionable_count == "0"` 时无 actionable;否则查看 `inline_comments_by_user["coderabbitai[bot]"]` 中本轮新条目,body 开头含 `_⚠️ Potential issue_`、`_🟠 Major_`、`_🛠️ Refactor suggestion_`、`_💡 Verification agent_` 等标签均算 actionable;nit 级不算。

**通过**:前置条件——已审当前 HEAD **且** `is_in_progress == false` **且** `is_paused == false`(paused 时 `is_ok` 等字段可能是上一轮残留,需先经触发规则 resume 后再判)。前置之上满足任一:

- `walkthrough.is_ok == true`
- `actionable_count == "0"`
- 本轮 inline 均为 `is_ack == true`
- 本轮 inline 均为 nit 级(body 含 `_🧹 Nitpick_` / `_🔵 Trivial_` / `_💤 Low value_`,不含上述 actionable 标签)

## Gemini Code Assist

**状态表达**:每次 review 发一条新 summary 评论(body 以 `## Code Review` 开头,涵盖整个 PR,**其中可能包含 inline 没有的 actionable 建议**——不能只看 inline);severity 标签在 inline 评论 body 开头,形如 `![high](https://www.gstatic.com/codereviewagent/high-priority.svg)` 的 markdown 图片。

**opened 与 synchronize 行为差异**:PR opened 时 GitHub App 自动 review;向已存在的 PR push 新 commit(synchronize)**不会**自动重审,需手动 `/gemini review`。cold-start 窗口内不要手动触发——重复触发既耗 quota,也容易引入第一次未提及的边缘建议。

**触发**(按 `pr_created_at` 与 `gemini.reviews` 判别,均受触发去重约束):

- `gemini.reviews` 完全为空,`pr_created_at` 距今**不足 5 分钟** → cold-start 窗口内,等待,不触发
- `gemini.reviews` 完全为空,`pr_created_at` 距今**已超 5 分钟** → cold-start fallback:自动 review 未在窗口内出现(可能失败或被跳过),发送 `/gemini review`。**此行不受 fix-up 跳过限制**——此时整个 PR 还没经过任何 Gemini review,不补发则 Gemini 永远不会审本 PR。5 分钟是含 webhook / 轮询延迟容差的宽松阈值,不必精确:若 Gemini 实际只是慢(窗口边缘误发),代价仅是一次额外触发,且受触发去重约束
- `gemini.reviews` 非空但最新一条 `submittedAt < last_push_at` → synchronize 场景,发送 `/gemini review`(受 fix-up 跳过限制)

**已审当前 HEAD**:`gemini.reviews[*].submittedAt > last_push_at` 至少一条。

**actionable**(两条路径,任一命中即算):

- **inline 路径**:本轮新 inline 中 `severity_alt` 为 `high` / `medium` / `critical`;`low` / `nit` / `style` 不算
- **summary 路径**:本轮最新一条 `gemini.reviews` 的 body 非空且不含明确通过标记(`LGTM`、`No issues found`、`Approved`、仅有 `## Code Review` 标题而无后续内容)

**通过**:前置条件——已审当前 HEAD(避免误用上一轮的通过标记)。前置之上需**同时**满足:

1. 本轮无新 inline,或本轮新 inline 全部为 `low/nit/style` 或全部 `is_ack`
2. summary 最新一条 body 含明确通过标记(非空不等于通过)

## OpenAI Codex(未接入,不参审)

本仓库未接入 Codex GitHub App:循环不发送 `@codex review`、不轮询或等待其响应、达标判定不含 Codex,fix-up 跳过规则也不再涉及它。以下判定规则保留作参考,日后接入 App 时按本节重新纳入参审名单。

**三种 ack 模式**(任一命中即算"对当前 HEAD 无 actionable"):

1. **inline review with body**:`codex.reviews` 最新一条,body 开头 `### 💡 Codex Review`,含 `**Reviewed commit:** <SHA>`,短 SHA 前 7-10 位与当前 HEAD 匹配
2. **PR-level +1 reaction**:`codex.reactions` 里有 `content == "+1"` **且** `created_at > last_push_at`(必须是本轮 push 之后留的 👍,旧的不算)
3. **empty-body review**:`codex.reviews` 最新一条 `submittedAt > last_push_at` **且** `state == "COMMENTED"` **且** `body == ""`,且本轮无新 inline

**已审当前 HEAD**:满足三种 ack 模式任一。

**actionable**:本轮新 inline 中 `severity_alt` 为 `Pn Badge` 形式;P0/P1 通常算 actionable,P2/P3 视情况。

**通过**:满足三种 ack 模式之一,且本轮无 ack 以外的 inline。

## GitHub code scanning bots(Code Quality + Advanced Security)

同一次 CodeQL 分析的两个投递面:`github-code-quality[bot]` 发质量告警(unused import、empty except 等,附修复建议),`github-advanced-security[bot]` 发安全告警(链接到 `/security/code-scanning/<n>` 的 alert)。与 CodeRabbit / Gemini 两家参审 AI reviewer 的本质差异:

- **不可触发**,随 push 后的 CodeQL 分析自动产出,可能比 CodeRabbit 慢几分钟
- **不读 inline 回复**,修复 push 后 alert 自动关闭——修了就不用回
- **对未修复告警不重复提醒**:同一 alert 只在引入时评论一次,后续 push 不重贴。因此"无遗留告警"**不能**用"本轮无新评论"判定,漏修一条会静默通过
- quality 告警通常**不会**让 check 变红,光看 CI 红绿会漏

**actionable**:两家所有本轮新 inline 一律算 actionable(量少且都是 CodeQL 高置信度规则),与 CodeRabbit / Gemini 的评论合并转交 `receiving-code-review`。pushback(误报、不该提交的产物等)仍由 `receiving-code-review` 判断,但落点是 PR 评论说明或 dismiss alert,**不是**回 inline。

**退出门槛**(代替"通过",在准备宣布循环结束时核对):

1. **分析完成且成功**:`codeql_checks` 非空,且各 check 对当前 HEAD 全部 `status == "completed"`、`conclusion` 无 failure / cancelled / timed_out(同名 check 取最新一条;check suite 重跑会产生同名多条)。两个陷阱:空数组对"全部 completed"恒真,但为空只说明分析未注册(继续等待)或仓库未接入(见下),不是通过;conclusion 失败时 alerts 数据停留在上次成功分析,直接核对门槛 2 会漏报新告警——归入故障类暂停。分析超过 15 分钟未完成同样归入故障类暂停
2. **security 无遗留**:`security_alerts.open_introduced` 为空(poll.sh 已做 base 分支差集,排除存量告警)。`available == false` 时降级:把 `unavailable_hint` 贴给用户,说明无法核对 alerts API(权限或 merge ref 原因),请人工确认后再退出
3. **quality 无遗留**:PR 上 `github-code-quality[bot]` 的**全量** inline 评论(不限本轮)逐条核对——对应代码已修改,或已有 pushback 记录(PR 评论说明)。quality 没有可查的告警列表 API(实测 404),全量评论 + 代码现状就是完整事实;不要依赖"本次循环收集过哪些"的对话记忆,压缩后无法重建。常规 PR 该量级是个位数,逐条核对是终核的一次性成本;若全量达数十条,逐条核对不再现实,向用户说明数量并商定抽查口径,不要硬撑也不要静默放行

**仓库未接入 code scanning 的判定**:`codeql_checks` 全程为空 + `security_alerts.available == false`(两端 alerts API 均不可用)+ PR 上从无两家 bot 评论 → 疑似未接入。**不得据此自动跳过**——GitHub 对无权限的资源同样返回 404,权限不足(如 token 缺 `security_events` scope)会伪装成与未接入相同的三信号,静默跳过等于放行未核对的安全告警。先读 `unavailable_hint` 判别:含 403 / permission / "must be enabled"(Advanced Security 未开)字样 → 权限或配置问题,按故障类暂停处理,不可跳过;含 404 + "not enabled" / "no analysis found" → 未接入佐证。无论判别结果如何,跳过门槛前都必须向用户确认一次,确认后在退出汇报中注明"code scanning 未接入(经用户确认),该门槛未核对"

## REST vs GraphQL 命名陷阱

`poll.sh` 的 JSON 输出已统一 key 命名——`inline_comments_by_user` 用 REST 的带 `[bot]` 名(inline 数据本就来自 REST),其它顶层字段用 GraphQL 的不带 `[bot]` 名。**不过**直接写 SKILL.md 之外的 jq 时:

| 数据源 | 字段路径 | 带不带 `[bot]` |
|---|---|---|
| `gh pr view --json reviews,comments,...`(GraphQL) | `.author.login` | **不带**——比如 `coderabbitai` |
| `gh api repos/.../pulls/.../comments`(REST inline) | `.user.login` | **带**——比如 `coderabbitai[bot]` |
| `gh api repos/.../issues/.../reactions`(REST) | `.user.login` | **带**——比如 `chatgpt-codex-connector[bot]` |

两边的字符串不通用。GitHub code scanning 两家 bot 只出现在 REST inline 数据中(不发 GraphQL 可见的 review/comment)。

## bot 改名时

REST 名规则:GraphQL 名 + `[bot]` 后缀。同步修改本文件和 `scripts/poll.sh` 的 select 语句。
