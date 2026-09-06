# Reviewer 协议

本文处理触发、覆盖与 finding 的读取。脚本 flags 是定位线索，不是代码裁决；是否实施及何时收敛以 [SKILL.md](../SKILL.md) 为准。审查完成可以仍有建议，pass marker 也不能覆盖尚未处置的发现。

## 身份与触发

| Reviewer | GraphQL login | REST login | 自动审查 | 手动触发 |
|---|---|---|---|---|
| CodeRabbit | `coderabbitai` | `coderabbitai[bot]` | PR opened、后续 push | `@coderabbitai resume` / `@coderabbitai review` |
| Gemini | `gemini-code-assist` | `gemini-code-assist[bot]` | PR opened | `/gemini review` |
| Codex | `chatgpt-codex-connector` | `chatgpt-codex-connector[bot]` | PR opened、修复 push 后续审 | `@codex review`，仅首次未启动时兜底 |
| CodeQL | — | `github-advanced-security[bot]` | 每次 push 后分析 | 无 |

同一 HEAD 的同种触发只发一次，命令单独置于评论开头；用 `own_trigger_comments` 与 `last_push_at` 核对。CodeRabbit resume 按该家下述 `updated_at` 判断。纯指标 bot（如 Codecov）不参与此循环。

## 覆盖与沿用

CodeRabbit、Codex 自动跟随 push，核对其当前 HEAD 的完成信号。仅回复、不改 HEAD 时，已确认的覆盖仍有效。

Gemini 手动重审消耗有限配额，默认沿用最近一次已完成且发现已处置的审查。沿用前检查从**最近已审 commit**到当前 HEAD 的全部变更：处置 reviewer 发现的修复、格式、拼写和局部小改动都可沿用；引入新行为、新接口、触及未审过的文件或改变安全边界时才重审。

`classify_commits.sh` 可帮助定位提交，不能单凭提交名或改动大小决定沿用。需查看提交范围时按其 header 使用；出现 `SINCE_SHA ... is not on PR`，或无法确认覆盖连续性时，重新审查。沿用依据（已审 SHA、当前 SHA）写进 `round.sh mark` 的 `--note`，便于接力核对。

## 读取与处置记录

从 `is_new` 定位新正文，经 `query.sh ... details <id>...` 批量读取。严重度用于排序，实际内容决定处置；致谢、已确认的撤回与纯风格建议不构成阻塞。flags 与预览或正文冲突时读全文。

`query.sh ... history` / `unacked` 用于查漏，不是“未 ack 就必须修改”的队列。快照不含非 bot 的 inline 回复；核实在案处置时，用 `gh api --paginate repos/<owner>/<repo>/pulls/<pr>/comments` 按 `in_reply_to_id` 关联。顶层处置说明从 `repos/<owner>/<repo>/issues/<pr>/comments` 读取。

## CodeRabbit

**触发。** `walkthrough.is_paused == true` 且其 `updated_at` 后尚未发送 resume 时，发送 `@coderabbitai resume`。其他时候等待自动审查。暂停和限流后的静默都不是完成。

**覆盖完成。** `reviewed_current_head == true`，且 `is_in_progress == false`、`is_paused == false`。限流时的 walkthrough 更新时间不算审查，脚本已通过 `is_rate_limited` 排除。

**发现入口。** 读取新 inline 和新 review body；`has_outside_diff` 提示正文含 diff 外发现，无 inline id 时在 PR 顶层回复。`is_ok`、`actionable_count` 是 bot 的摘要，不能替代这些正文。增量审查返回 `Already reviewed` 时，两者可能残留上一轮值，以本轮正文和实际处置为准。所有实质发现已处置即可收敛，不要求 `actionable_count` 归零。

## Gemini

**触发。** PR 创建不足 5 分钟且从无 review 时等待；超过 5 分钟仍无 review，才发送 `/gemini review`。已有审查但不覆盖当前 HEAD、且不满足沿用条件时，再触发。发送后等待，避免同一 HEAD 重复请求。

**覆盖完成。** 有 `reviewed_current_head == true` 的已提交 review，或满足上述沿用条件。

**发现入口。** 同时检查 inline 和最新 review summary。`has_pass_marker == false` 时用 `query.sh ... gemini-latest-body` 读取，区分真实 finding 与没有采用固定通过措辞。inline 全部处置不代表 summary 已处置；反之，缺少 pass marker 也不等于存在待修复问题。

## Codex

**触发。** PR 创建不足 5 分钟时等待。超过 5 分钟，仍无 review、inline、reaction 或 `has_started`，才发送一次 `@codex review`。出现启动信号或已经参审后，后续 push 等待自动续审；超时按 [faults.md](faults.md) 处理。

**覆盖完成。** 当前 HEAD 上出现以下任一完成信号：带 `### 💡 Codex Review` 的 review；空 body 的 `COMMENTED` review 且本轮无新 inline；`has_pass_marker` 且 `reviewed_current_head == true` 的顶层通过评论；或可确认属于本 HEAD 的 `+1` reaction（新信号由 `is_new` 标示）。新 push 后的 `eyes` 表示正在重审，上一 HEAD 的 `+1` 不能沿用。

**发现入口。** 读取非 ack inline，以及 `has_body_finding == true` 的 review body。P0/P1 优先；其余发现同样按内容判断，不因标签自动实施或自动驳回。

## CodeQL 安全退出门槛

CodeQL 不读 inline 回复，修复后由分析更新告警；尚未修复的告警也不一定再次评论。每次核对 `security_alerts.open_introduced`，而不只检查本轮新评论。

退出须同时满足：

- `codeql_checks.all_ok == true`：当前 HEAD 分析完成且成功。`total == 0`、pending 或失败都不是通过。
- `security_alerts.available == true`，且 `open_introduced` 为空，或仅剩经核实并在案的误报。该列表已排除 base 存量，check-run 标题中的 “N new alerts” 不能替代它。

新告警按主文件的证据标准核实。核实为误报的，按 alert number 在 PR 顶层评论写明污点链终点与结论，即纳入在案清单；证据不足以定论的交委派方裁决。dismiss 由用户执行，退出汇报列出待 dismiss 项；循环不代为消警。

**已有授权的误报家族。** `py/path-injection` 的污点链止于 `ProjectManager.get_project_path()` 返回路径本身，且内部 `safe_join` 对 `project_name` 的保护仍成立时，可直接核实并在案。返回值之后又拼接未校验的 `filename` 等污点，不属于此例外。记录 alert number 与实际污点终点；已有记录的前提未变时沿用结论。

在案清单从 PR 顶层评论按 alert number 核对；`query.sh ... history` 不含这些人工顶层说明。权限不足也可能返回 404，不能据此判定仓库未接入。若分析全程为 0、alerts 不可用且从无该 bot 评论，仅说明疑似未接入，须委派方确认后才能跳过，并在汇报中标明未核对；其他分析或权限故障见 [faults.md](faults.md)。

## 现场查询

优先使用 query.sh。确需直接查快照时，先用已知非空查询验证字段路径。`gh` 读取偶有截断却返回 0 的情况，写回 PR 正文等远端内容前，确认读到的是完整原文。
