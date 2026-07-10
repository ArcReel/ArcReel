# Reviewer 速查

本文件按 reviewer 聚合决策规则(身份、触发、已审、actionable、通过),全部以 poll.sh 索引字段表达;字段语义与解析细节以 poll.sh header 为单一真相源,本文不复述。

## 通用约定

- **本轮新评论**:索引中 `is_new == true` 的条目(inline 走 `inline_new_by_user`,评论走 `comments_new`)。口径与陷阱见 poll.sh PITFALL 2
- **Acknowledgment 例外**:`is_ack == true` 的条目是 reviewer 对上一次修复或 inline 回复的确认,一律**不算** actionable;review state 为 `APPROVED` 也不算
- **flag 以正文为准**:索引 flags(`is_ack` / `cr_markers` / `has_pass_marker` / `severity_alt`)是脚本解析结果,预览观感与 flag 冲突时用 `query.sh details` 取全文核实,以正文为准
- **fix-up 顺延**:某家对上一已审 HEAD 已通过,且其后的 push 全为 fix-up 形状(nit、format、typo、单字段调整、小 bug 修复)时,沿用该通过结论参与目标判定,不触发重审——CodeRabbit 自动跟审每次 push,最终 HEAD 始终至少有它过目。**「上一已审 HEAD」指该家最近一次实际审过的 commit,不是最近一次通过的 commit**——若该家最近审的 HEAD 未通过(如 A 通过 → B 有 actionable → C fix-up,最近审的是 B),前提不满足,不得顺延;「其后」从该已审 HEAD 的下一个 commit 起算,到当前 HEAD 为止须全为 fix-up。该家还有未解决评论时(`query.sh unacked <bot[bot]>` 非空,bot 名须带 `[bot]` 后缀)同样不得跳过,必须正常触发重审
- **触发去重**:同一 HEAD 上每种触发命令只发一次。在 `own_trigger_comments` 中按 `command` 字段取该命令最大 `createdAt`,晚于 `last_push_at` 即视为本轮已触发,跳过(`@coderabbitai resume` 例外:以 CodeRabbit 节的 `updated_at` 口径为准)。发触发命令时只写命令本身,且命令必须在评论最开头(匹配细则见 poll.sh PITFALL 4)
- **纯指标类 bot 不纳入循环**:`codecov[bot]` 等纯指标类 bot 没有意见可实施,也没有等待或重审的概念

## 总表

| Reviewer | GraphQL `author.login` | REST `user.login` | 自动 review 时机 | 触发命令 |
|---|---|---|---|---|
| CodeRabbit | `coderabbitai` | `coderabbitai[bot]` | PR opened 及后续每次 push | `@coderabbitai resume` / `review` / `full review` |
| Gemini Code Assist | `gemini-code-assist` | `gemini-code-assist[bot]` | **仅 PR opened**(5 分钟内出结果) | `/gemini review` |
| GitHub Code Quality | —(只发 inline) | `github-code-quality[bot]` | 每次 push 后的 CodeQL 分析 | **不可触发** |
| GitHub Advanced Security | —(只发 inline) | `github-advanced-security[bot]` | 同上 | **不可触发** |

## CodeRabbit

**触发**:`coderabbit.walkthrough.is_paused == true`,且 `updated_at` 之后未发送过 `@coderabbitai resume`(从 `own_trigger_comments` 筛,最新一条 `createdAt` 早于 walkthrough 的 `updated_at`;为空视为未发送)→ 发送 `@coderabbitai resume`。其余场景 CodeRabbit 自动跟新 push,无需手动触发。

**已审当前 HEAD**:`walkthrough.reviewed_current_head == true`。

**actionable**:`walkthrough.is_ok == true` 或 `actionable_count == "0"` 时无 actionable;否则看 `inline_new_by_user["coderabbitai[bot]"]` 各行的 `cr_markers`:含 `potential_issue` / `major` / `refactor` / `verification` 任一即 actionable;仅含 nit 级 token(`nitpick` / `trivial` / `low_value` / `minor`)不算。

**通过**:前置条件——`reviewed_current_head == true` **且** `is_in_progress == false` **且** `is_paused == false`(paused 时 `is_ok` 等字段可能是上一轮残留,需先经触发规则 resume 后再判)。前置之上满足任一:

- `walkthrough.is_ok == true`
- `actionable_count == "0"`
- 本轮 inline 均为 `is_ack == true`
- 本轮 inline 均为 nit 级(`cr_markers` 仅含 nit 级 token,无 actionable token)

## Gemini Code Assist

**触发**(按 `pr_created_at` 与 `gemini.reviews` 判别,均受触发去重约束):

- `gemini.reviews` 完全为空,`pr_created_at` 距今**不足 5 分钟** → cold-start 窗口内,等待——此时抢跑触发既耗 quota,也容易引入第一次未提及的边缘建议
- `gemini.reviews` 完全为空,`pr_created_at` 距今**已超 5 分钟** → cold-start fallback:自动 review 未在窗口内出现(可能失败或被跳过),发送 `/gemini review`。**此行不受 fix-up 顺延限制**——否则 Gemini 永远不会审本 PR。阈值宽松不必精确——误发代价只是一次受去重约束的额外触发
- `gemini.reviews` 非空但无 `is_new == true` 条目 → 发送 `/gemini review`(受 fix-up 顺延限制)

**已审当前 HEAD**:`gemini.reviews` 至少一条 `is_new == true`。

**actionable**(两条路径,任一命中即算):

- **inline 路径**:`inline_new_by_user["gemini-code-assist[bot]"]` 中 `severity_alt` 为 `high` / `medium` / `critical`;`low` / `nit` / `style` 不算
- **summary 路径**:最新一条 `gemini.reviews` 的 `has_pass_marker == false`(通过标记词表见 poll.sh)

**通过**:前置条件——已审当前 HEAD(避免误用上一轮的通过标记)。前置之上需**同时**满足:

1. 本轮无新 inline,或本轮新 inline 全部为 `low/nit/style` 或全部 `is_ack`
2. 最新一条 `gemini.reviews` 的 `has_pass_marker == true`

## GitHub code scanning bots(Code Quality + Advanced Security)

同一次 CodeQL 分析的两个投递面:`github-code-quality[bot]` 发质量告警(unused import、empty except 等,附修复建议),`github-advanced-security[bot]` 发安全告警(链接到 `/security/code-scanning/<n>` 的 alert)。与 CodeRabbit / Gemini 两家参审 AI reviewer 的本质差异:

- **不可触发**,随 push 后的 CodeQL 分析自动产出,可能比 CodeRabbit 慢几分钟
- **不读 inline 回复**,修复 push 后 alert 自动关闭——修了就不用回
- **对未修复告警不重复提醒**:同一 alert 只在引入时评论一次,后续 push 不重贴。因此"无遗留告警"**不能**用"本轮无新评论"判定,漏修一条会静默通过
- quality 告警通常**不会**让 check 变红,光看 CI 红绿会漏

**actionable**:两家所有本轮新 inline 一律算 actionable,与 CodeRabbit / Gemini 的评论合并转交 `receiving-code-review`。pushback(误报、不该提交的产物等)仍由 `receiving-code-review` 判断,但落点是 PR 评论说明或 dismiss alert,**不是**回 inline。

**退出门槛**(代替"通过",在准备宣布循环结束时核对):

1. **分析完成且成功**:`codeql_checks.all_ok == true`(要求 total > 0 且无 pending、无 failing;失败态集合定义见 poll.sh header `checks_failing` 条,同名重跑已由 poll.sh 归一为每名最新一条)。`total == 0` 只说明分析未注册(继续等待)或仓库未接入(见下),不是通过;`failing` 非空时 alerts 数据停留在上次成功分析,直接核对门槛 2 会漏报新告警——归入故障类暂停。分析超过 15 分钟未完成同样归入故障类暂停
2. **security 无遗留**:`security_alerts.open_introduced` 为空(poll.sh 已做 base 分支差集,排除存量告警)。`available == false` 时降级:把 `unavailable_hint` 贴给用户,说明无法核对 alerts API(权限或 merge ref 原因),请人工确认后再退出
3. **quality 无遗留**:终核时跑 `query.sh quality-all` 取 `github-code-quality[bot]` 的**全量** inline 评论(不限本轮)逐条核对——对应代码已修改,或已有 pushback 记录(PR 评论说明)。quality 没有可查的告警列表 API(实测 404),全量评论 + 代码现状就是完整事实,以本次查询结果为准而非对话记忆(压缩后无法重建)。常规 PR 该量级是个位数;若全量达数十条,向用户说明数量并商定抽查口径

**仓库未接入 code scanning 的判定**:`codeql_checks.total` 全程为 0 + `security_alerts.available == false`(两端 alerts API 均不可用)+ PR 上从无两家 bot 评论 → 疑似未接入。跳过该门槛前必须先向用户确认一次——GitHub 对无权限的资源同样返回 404,权限不足(如 token 缺 `security_events` scope)会伪装成与未接入相同的三信号,静默跳过等于放行未核对的安全告警。判别辅助:读 `unavailable_hint`,含 403 / permission / "must be enabled"(Advanced Security 未开)字样 → 权限或配置问题,按故障类暂停处理;含 404 + "not enabled" / "no analysis found" → 未接入佐证。经用户确认跳过后,在退出汇报中注明"code scanning 未接入(经用户确认),该门槛未核对"

## REST vs GraphQL 命名陷阱

`poll.sh` 输出已统一 key 命名——`inline_*_by_user` 用 REST 的带 `[bot]` 名,其它顶层字段用 GraphQL 的不带 `[bot]` 名(差异由来见 poll.sh PITFALL 3)。确需对快照现场写 jq 时,先用已知非空的查询验证字段路径——空结果与路径打错不可区分。绕过 query.sh 直接打 GitHub API 时:GraphQL(`gh pr view --json ...`)的 `.author.login` 不带 `[bot]`,REST inline(`gh api .../pulls/.../comments`)的 `.user.login` 带 `[bot]`,两边字符串不通用;code scanning 两家 bot 只出现在 REST inline 数据中(不发 GraphQL 可见的 review/comment)。
