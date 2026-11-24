# Reviewer 协议

本文处理各家 reviewer 的触发、覆盖、发现读取与反馈。脚本输出的标志字段是定位线索，不是代码裁决；定性与收敛以 [SKILL.md](../SKILL.md) 为准。审查完成后仍可能有发现，通过标记也不能覆盖尚未处置的发现。

## 身份与触发

| Reviewer | 快照 login | REST login | 自动审查 | 手动触发 |
|---|---|---|---|---|
| CodeRabbit | `coderabbitai` | `coderabbitai[bot]` | PR opened、后续 push | `@coderabbitai resume` / `@coderabbitai review` |
| Codex | `chatgpt-codex-connector` | `chatgpt-codex-connector[bot]` | PR opened；后续 push 不保证 | `@codex review`，每个 HEAD 至多一次 |
| CodeQL | — | `github-advanced-security[bot]` | 每次 push 后分析 | 无 |

同一 HEAD 的同种触发命令只发送一次，且命令须单独置于评论开头；发送前用 `own_trigger_comments` 与 `last_push_at` 核对。各家的具体触发时机见下文各节。纯指标类 bot（如 Codecov）不参与本循环。

每次 push 后按各家的完成信号核对当前 HEAD，追加 commit 与改写历史后的 force push 同样处理。仅回复、不改 HEAD 时，已确认的覆盖仍有效。

## 读取

从 `is_new` 定位新正文，经 `query.sh ... details <id>...` 批量读取。严重度用于排序，定性按 SKILL.md「工程判断」；致谢与已确认的撤回不构成阻塞。标志字段与预览或正文冲突时读全文。

`query.sh ... history` / `unacked` 用于查漏，不是「未 ack 就必须修改」的队列。快照不含非 bot 的 inline 回复；核实在案处置时，用 `gh api 'repos/<owner>/<repo>/pulls/<pr>/comments?per_page=100&page=<N>'` 从第 1 页逐页读到不满 100 条，按 `in_reply_to_id` 关联。不用 `--paginate`：它跟随的下一页链接是数字 ID 路径，只放行 REST 的会话会拒绝。顶层处置说明从 `repos/<owner>/<repo>/issues/<pr>/comments` 读取。

## 处置写回

每条发现的处置都要在案。缺陷以修复在案，bot 会按新 HEAD 自行确认，无需回复；其余在原 inline 线程回复定性与依据，没有 inline id 的发现在 PR 顶层回复并注明来源 id。各家的官方反馈渠道见下文各节「反馈」。

## CodeRabbit

**触发。** `walkthrough.is_paused == true` 且其 `updated_at` 后尚未发送 resume 时，发送 `@coderabbitai resume`。`walkthrough.is_review_skipped == true`（draft、bot 作者等跳过审查的通知）且其 `updated_at` 后尚未发送 review 时，发送 `@coderabbitai review`。其他时候等待自动审查。暂停、限流和跳过后的静默都不是完成。

**覆盖完成。** `reviewed_current_head == true`，且 `is_in_progress == false`、`is_paused == false`。限流与跳过审查时的 walkthrough 更新时间不算审查，脚本已通过 `is_rate_limited`、`is_review_skipped` 排除。

**发现入口。** 读取新 inline 和新 review body；`has_outside_diff` 提示正文含 diff 外发现，这类发现没有 inline id。`is_ok`、`actionable_count` 是 bot 的摘要，不能替代这些正文。增量审查返回 `Already reviewed` 时，两者可能残留上一轮值，以本轮正文和实际处置为准。所有实质发现已处置即可收敛，不要求 `actionable_count` 归零。

**反馈。** CodeRabbit 从线程对话学习，learnings 作用于此后整个仓库的审查。回复留在 inline 线程内，不 `@coderabbitai`，以免为单个 PR 的判断生成全仓 learning。同一类噪声反复出现、且 `docs/standards/` 尚未覆盖时，按 [lessons.md](../../../../docs/agents/lessons.md) 提为 `lesson-candidate`，由规范收录；CodeRabbit 与 Codex 都从规范读取审查规则。

## Codex

**触发。** 每个 HEAD 同一条规则，PR 的首个 HEAD 也一样：`last_push_at` 起满 5 分钟，`codex.started_current_head == false`，且 `own_trigger_comments` 中没有晚于 `last_push_at` 的 `@codex review`，就发送一次。Codex 在 PR 创建时自动审查，force push 之后大多不会自行续审，所以不能等它。窗口在等待期间到期时，`wait.sh` 以 `codex_silent` 返回。`codex.failed_current_head == true` 表示 Codex 报告该 HEAD 的审查失败，它不会自行重试：尚未对该 HEAD 触发过就立即发送，不等 5 分钟；触发过仍失败按 [faults.md](faults.md) 处理。触发后迟迟没有结果同样按 faults.md 处理；该家已在本 PR 停用时不再触发。

**覆盖完成。** 当前 HEAD 上出现以下任一完成信号：带 `### 💡 Codex Review` 的 review；空 body 的 `COMMENTED` review 且本轮无新 inline；`has_pass_marker` 且 `reviewed_current_head == true` 的顶层通过评论；或可确认属于本 HEAD 的 `+1` reaction（新信号由 `is_new` 标示）。新 push 后的 `eyes` 表示正在重审，上一 HEAD 的 `+1` 不能沿用。`last_push_at` 是 commit 时间，早于实际 push：只有 reaction、没有带 `Reviewed commit` 的回执时，确认它晚于 push 再采信。

**发现入口。** 读取非 ack inline，以及 `has_body_finding == true` 的 review body。P0/P1 优先处理。

**反馈。** 每条 inline 发现末尾写有 `Useful? React with 👍 / 👎.`，reaction 是 Codex 收集有用性的渠道。定性后给该 inline 加 reaction：缺陷与存量（发现成立）用 `+1`，YAGNI、nitpick 与误报用 `-1`；reaction 不替代回复。review body 里的发现没有 reaction 入口，只在 PR 顶层回复。

```bash
gh api -X POST repos/<owner>/<repo>/pulls/comments/<id>/reactions -f content=-1
```

## CodeQL 安全退出门槛

CodeQL 不读 inline 回复，修复后由分析更新告警；尚未修复的告警也不一定再次评论。每次核对 `security_alerts.open_introduced`，而不只检查本轮新评论。

退出须同时满足：

- `codeql_checks.all_ok == true`：当前 HEAD 分析完成且成功。`total == 0`、pending 或失败都不是通过。
- `security_alerts.available == true`，且 `open_introduced` 为空，或仅剩经核实并在案的误报。该列表已排除 base 存量，check-run 标题中的 `N new alerts` 不能替代它。

新告警按 SKILL.md「证据」核实。核实为误报的，按 alert number 在 PR 顶层评论写明污点链终点与结论，即纳入在案清单；证据不足以定论的交委派方裁决。dismiss 只由用户执行，退出汇报列出待 dismiss 的告警。

**已有授权的误报家族。** `py/path-injection` 的污点链止于 `ProjectManager.get_project_path()` 返回路径本身，且内部 `safe_join` 对 `project_name` 的保护仍成立时，可直接核实并在案。返回值之后又拼接未校验的 `filename` 等污点，不属于此例外。记录 alert number 与实际污点终点；已有记录的前提未变时沿用结论。

在案清单从 PR 顶层评论按 alert number 核对；`query.sh ... history` 不含这些人工顶层说明。权限不足也可能返回 404，不能据此判定仓库未接入。若分析全程为 0、alerts 不可用且从无该 bot 评论，仅说明疑似未接入，须委派方确认后才能跳过，并在汇报中标明未核对；其他分析或权限故障见 [faults.md](faults.md)。

## 现场查询

优先使用 `query.sh`。确需直接查询快照时，先用已知非空查询验证字段路径。`gh` 偶尔会截断输出但仍返回 0；在写回 PR 正文等远端内容前，务必确认读到的是完整原文。
