# Reviewer 速查

本文按 reviewer 聚合决策规则(身份、触发、已审、actionable、通过),全部以 poll.sh 索引字段表达;字段语义与解析细节以 poll.sh header 为单一真相源,本文不复述。

## 通用约定

- **本轮新评论**:索引中 `is_new == true` 的条目(inline 在 `inline_new_by_user`,评论在 `comments_new`)。口径见 poll.sh PITFALL 2 与 6
- **Acknowledgment 例外**:`is_ack == true` 的条目是 reviewer 对上一次修复或 inline 回复的确认,不算 actionable;review state 为 `APPROVED` 也不算
- **flag 以正文为准**:索引 flags(`is_ack` / `cr_markers` / `has_pass_marker` / `has_outside_diff` / `has_body_finding` / `severity_alt`)是脚本解析结果,预览与 flag 冲突时用 `query.sh details` 取全文核实
- **unacked 核对的假阳性**:终核时 unacked 非空则逐条核对,对应修复已落地或已回复在案 pushback 的不算遗漏。快照不含非 bot 的 inline 回复,核对回复线程用 `gh api --paginate repos/<owner>/<repo>/pulls/<pr>/comments` 按 `in_reply_to_id` 关联
- **fix-up 顺延**:适用于**手动重审型** reviewer——总表中自动 review 时机仅为 PR opened、重审须发送触发命令的 reviewer(当前仅 Gemini)。每次手动重审消耗配额,且倾向于产生首次未提及的边缘建议,因此该 reviewer 对上一已审 HEAD 已通过、其后的 push 全为 fix-up 形状(nit、format、typo、单字段调整、小 bug 修复)时,沿用该通过结论参与目标判定,不触发重审。自动跟随 push 的 reviewer(CodeRabbit、Codex)以实际审过当前 HEAD 为目标状态。**「上一已审 HEAD」指该 reviewer 最近一次实际审过的 commit,不是最近一次通过的 commit**——最近审的 HEAD 未通过,或 `query.sh unacked <bot[bot]>` 仍有未解决评论时,不得顺延
- **触发去重**:同一 HEAD 上每种触发命令只发送一次。在 `own_trigger_comments` 中按 `command` 字段取该命令最大 `createdAt`,晚于 `last_push_at` 即视为本轮已触发(`@coderabbitai resume` 例外:以 CodeRabbit 节的 `updated_at` 口径为准)。触发评论只写命令本身,且命令位于评论开头(匹配细则见 poll.sh PITFALL 4)
- **纯指标类 bot 不纳入循环**:`codecov[bot]` 等没有可实施的意见,也没有等待或重审的概念

## 总表

| Reviewer | GraphQL `author.login` | REST `user.login` | 自动 review 时机 | 触发命令 |
|---|---|---|---|---|
| CodeRabbit | `coderabbitai` | `coderabbitai[bot]` | PR opened 及后续每次 push | `@coderabbitai resume` / `review` / `full review` |
| Gemini Code Assist | `gemini-code-assist` | `gemini-code-assist[bot]` | **仅 PR opened**(5 分钟内出结果) | `/gemini review` |
| OpenAI Codex | `chatgpt-codex-connector` | `chatgpt-codex-connector[bot]` | PR opened;修复 push 后自动续审 | 仅首次 cold-start fallback 用 `@codex review` |
| GitHub Advanced Security | —(只发 inline) | `github-advanced-security[bot]` | 每次 push 后的 CodeQL 分析 | **不可触发** |

## CodeRabbit

**触发**:`coderabbit.walkthrough.is_paused == true`,且 `updated_at` 之后未发送过 `@coderabbitai resume`(从 `own_trigger_comments` 筛选,最新一条 `createdAt` 早于 walkthrough 的 `updated_at`;为空视为未发送)→ 发送 `@coderabbitai resume`。其余场景 CodeRabbit 自动跟随 push。暂停会在后续 push 后重现,触发判定逐轮执行;暂停期间的静默不是通过。

**已审当前 HEAD**:`walkthrough.reviewed_current_head == true`。限流时 walkthrough 被改写为限流横幅,不算审查——poll.sh 已按 `is_rate_limited` 排除,该场景下字段恒为 false。

**actionable**:本轮新 `coderabbit.reviews` 任一行 `has_outside_diff == true` 即 actionable——diff 之外代码的建议内嵌在 review body 中,没有 inline id,用该行 `id` 经 `query.sh details` 取正文,回复走 PR 顶层评论。无此形状时,`walkthrough.is_ok == true` 或 `actionable_count == "0"` 表示无 actionable;否则看 `inline_new_by_user["coderabbitai[bot]"]` 各行的 `cr_markers`:含 `potential_issue` / `major` / `refactor` / `verification` 任一即 actionable;仅含 nit 级 token(`nitpick` / `trivial` / `low_value` / `minor`)不算。**残留例外**:增量重审回复 `Already reviewed` 时 `is_ok` / `actionable_count` 是上一轮残留,跳过这两个字段,直接按本轮 review 的 `has_outside_diff` 与 inline 的 `cr_markers` 判定。

**通过**:前置条件——`reviewed_current_head == true` 且 `is_in_progress == false` 且 `is_paused == false`(paused 时 `is_ok` 等字段可能是上一轮残留,须先按触发规则 resume 再判定)。前置之上,本轮新 review 均为 `has_outside_diff == false`,且满足任一:

- `walkthrough.is_ok == true`
- `actionable_count == "0"`
- 本轮 inline 均为 `is_ack == true`
- 本轮 inline 均为 nit 级(`cr_markers` 仅含 nit 级 token)

增量重审回复 `Already reviewed` 时前两条不可用(上一轮残留),按本轮 inline 的后两条判定。

**配额**:本仓库使用 CodeRabbit 免费开源方案,限流以 `walkthrough.is_rate_limited == true` 或 `quota_alerts` 判读;受阻时等待自动恢复并手动 `@coderabbitai review` 重试一次;仍失败则本 PR 停用该家,记入退出汇报。全程不询问委派方,也不提议付费扩容。

## Gemini Code Assist

**触发**(按 `pr_created_at` 与 `gemini.reviews` + `reviews_history.total` 判别,均受触发去重约束):

- `gemini.reviews` 为空且 `reviews_history.total == 0`,`pr_created_at` 距今**不足 5 分钟** → cold-start 窗口内,等待——此时提前触发既消耗配额,也容易引入首次未提及的边缘建议
- `gemini.reviews` 为空且 `reviews_history.total == 0`,`pr_created_at` 距今**已超 5 分钟** → cold-start fallback:自动 review 未在窗口内出现,发送 `/gemini review`。**此行不受 fix-up 顺延限制**——否则 Gemini 不会审本 PR
- 已有 review(`gemini.reviews` 非空或 `reviews_history.total > 0`)但无 `reviewed_current_head == true` 条目 → 发送 `/gemini review`(受 fix-up 顺延限制)

**已审当前 HEAD**:`gemini.reviews` 至少一条 `reviewed_current_head == true`。

**actionable**(两条路径,任一命中即算):

- **inline 路径**:`inline_new_by_user["gemini-code-assist[bot]"]` 中 `severity_alt` 为 `high` / `medium` / `critical`;`low` / `nit` / `style` 不算
- **summary 路径**:最新一条 `reviewed_current_head == true` 的 review 的 `has_pass_marker == false`(判定规则见 poll.sh header;未命中即视为仍有 actionable)

**通过**:前置条件——已审当前 HEAD。前置之上须**同时**满足:

1. 本轮无新 inline,或本轮新 inline 全部为 `low/nit/style` 或全部 `is_ack`
2. 最新一条 `reviewed_current_head == true` 的 review 的 `has_pass_marker == true`

**pushback 例外**:存在 pushback 时 pass marker 不可达,按「本轮非 ack inline 均已处置」判定通过。

## OpenAI Codex

首次 PR 自动审查;修复 push 后自动续审,直到当前 HEAD 不再产生 finding。Codex 已参审或 `codex.has_started == true` 后,后续动作固定为等待自动续审。

**触发**(受通用触发去重约束):

- `codex.has_started == true`,或已有 review(`codex.reviews` 非空或 `reviews_history.total > 0`) / `+1` / inline → 等待
- 无上述信号,`pr_created_at` 距今不足 5 分钟 → cold-start 窗口内等待
- 无上述信号,已超过 5 分钟,且本轮尚无 `@codex review` → 发送一次 `@codex review`
- 已有 `@codex review` 但尚无结果 → 评论上的 Codex `👀` 会使 `codex.has_started == true`;未出现时同样等待,30 分钟后按故障处理
- Codex 已参审,新 push 后尚无已审当前 HEAD 信号 → 等待;距 `last_push_at` 超过 30 分钟仍无 review / 顶层通过评论 / `+1` / inline → 按故障处理

PR reaction 表示当前审查状态:新 push 启动审查时,Codex 会把上一轮 `+1` 换成 `eyes`;此时上一轮通过失效,当前 HEAD 进入审查中。

**已审当前 HEAD**:满足以下任一信号:

1. 带 `### 💡 Codex Review` 的 review,其 `reviewed_current_head == true`
2. `codex.reactions` 有 `content == "+1"` 且 `is_new == true`
3. 空 body `COMMENTED` review,其 `reviewed_current_head == true`,且本轮无新 inline
4. `codex.comments_new` 中顶层评论的 `has_pass_marker == true` 且 `reviewed_current_head == true`

**actionable**:本轮新 `codex.reviews` 任一行 `has_body_finding == true` 即进入处置,用该行 `id` 经 `query.sh details` 取正文;本轮非 ack inline 的 `P0 Badge` / `P1 Badge` 同样 actionable。两种投递面的 P2/P3 均按 `receiving-code-review` 的纪律核实;判定为非 actionable 并记录 pushback 后不再阻塞通过。

**通过**:满足已审信号之一,且本轮无未解决的 actionable inline 或 review-body finding。

## GitHub Advanced Security(code scanning)

`github-advanced-security[bot]` 发布 CodeQL 分析的安全告警(链接到 `/security/code-scanning/<n>` 的 alert)。与三家 AI reviewer 的差异:

- **不可触发**,随 push 后的 CodeQL 分析自动产出,可能比 CodeRabbit 慢几分钟
- **不读 inline 回复**,修复 push 后 alert 自动关闭,无需回复
- **对未修复告警不重复提醒**:同一 alert 只在引入时评论一次,后续 push 不再评论。因此「无遗留告警」不能以「本轮无新评论」判定,遗漏的告警不会再次出现

**actionable**:本轮全部新 inline 一律 actionable,与三家 AI reviewer 的评论并入同一批处理。pushback(误报、不应提交的产物等)同样按 `receiving-code-review` 的纪律判断,落点是 PR 评论说明或 dismiss alert,不回复 inline。

**已知误报**:`py/path-injection` 告警中,污点值止于 `ProjectManager.get_project_path()` 的返回路径本身(内部 `safe_join` 已处理 project_name)时属已知误报家族——核实告警链路的最终污点未越过该函数返回值后,处置为 PR 评论说明(写明该 alert 的 `number` 与核实到的污点终点)并记入已认定误报在案清单,不重新分析、不为其改代码。`get_project_path()` 之后又拼接了未经 `safe_join` 处理的其他污点片段(如 `get_source_path()` / `_get_asset_path()` 追加的 `filename`)不属该家族,按常规告警核实处置。dismiss 权限在用户,不得代执行——退出汇报列明待 dismiss 清单转呈用户事后执行,在案清单即该 alert 在循环内的终态。核对某条 `open_introduced` alert 是否已计入在案清单:`query.sh history` 只扫描三家 AI reviewer 的 review / comment,不含顶层 PR 说明;须用 `gh api --paginate repos/<owner>/<repo>/issues/<pr>/comments` 取出全部顶层评论,按 alert 的 `number` 匹配(同一 rule/path 可能对应多条 alert)是否已有本循环所用账号发布的「已知误报」说明。该核对是逐轮可重新执行的现场查询,不依赖对话记忆。

**退出门槛**(代替「通过」,宣布循环结束前核对):

1. **分析完成且成功**:`codeql_checks.all_ok == true`(要求 total > 0 且无 pending、无 failing;失败态集合见 poll.sh header `checks_failing` 条)。`total == 0` 只说明分析未注册(继续等待)或仓库未接入(见下),不是通过;`failing` 非空时 alerts 数据停留在上次成功分析,归入故障类暂停。分析超过 30 分钟未完成同样归入故障类暂停
2. **security 无遗留**:`security_alerts.open_introduced` 为空(poll.sh 已做 base 分支差集,排除存量告警);仅剩已认定误报在案清单内的 alert 同样视为达成,退出汇报列明待 dismiss 清单。CodeQL check-run 标题中的 "N new alerts" 按 merge-ref 全量统计,含 main 上的存量告警,不作判定依据。`available == false` 时把 `unavailable_hint` 交委派方,说明无法核对 alerts API(权限或 merge ref 原因),经人工确认后再退出

**仓库未接入 code scanning 的判定**:`codeql_checks.total` 全程为 0 + `security_alerts.available == false` + PR 上从无该 bot 评论 → 疑似未接入。跳过该门槛前须先向委派方确认一次——GitHub 对无权限的资源同样返回 404,权限不足(如 token 缺 `security_events` scope)会表现为相同的三个信号,跳过即等于未核对安全告警。判别辅助:`unavailable_hint` 含 403 / permission / "must be enabled"(Advanced Security 未开启)→ 权限或配置问题,按故障类暂停;含 404 + "not enabled" / "no analysis found" → 未接入佐证。经确认跳过后,在退出汇报中注明「code scanning 未接入(经委派方确认),该门槛未核对」。

## 现场查询

确需对快照直接写 jq 时,先用已知非空的查询验证字段路径——空结果与路径错误不可区分。绕过 query.sh 直接调用 GitHub API 时,登录名按总表两列取用(差异由来见 poll.sh PITFALL 3);Advanced Security bot 只出现在 REST inline 数据中。
