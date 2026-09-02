---
name: pr-ai-review-loop
description: 无人值守驱动 PR 的 review → 处置 → push → 再 review 循环,直到全部 AI reviewer 通过或轮次预算耗尽。仅当用户明确要求运行或继续该循环,或本地会话刚完成 PR push 后要求继续收敛时调用;作为 GitHub reviewer 审查代码、仅阅读 PR 或处理单条 review 意见时不调用。
---

# AI Review 自动循环

无人值守:触发命令、push、回复 inline、修复 CI、等待时长均自行决定。**委派方**指发起本循环的用户或 team-lead,汇报、询问与预算覆盖均面向委派方。

进入前:当前分支已有非 draft 的 PR(draft 状态下 CodeRabbit 不审;无 PR 时由委派方决定是否创建,不代为创建),并通读 [references/reviewers.md](references/reviewers.md)——每轮的已审 / actionable / 通过判定全部依赖其中的 per-reviewer 规则。以下命令的 `<repo-root>` 均为目标 PR checkout 的根目录。

## 目标状态

循环唯一的正常出口。宣布通过前逐项核对:

1. **每家参审 AI reviewer 通过**(CodeRabbit / Gemini / Codex)。自动跟随 push 的 reviewer 须审过当前 HEAD;手动重审型 reviewer 可按 fix-up 顺延沿用上一已审 HEAD 的通过结论(reviewers.md「通用约定」)
2. **CodeQL 退出门槛**:分析完成且成功、security 无本 PR 引入的 open 告警(reviewers.md「GitHub Advanced Security」)
3. **每条 actionable 均已实施或有在案 pushback**。终核时对三家各执行一次 `query.sh unacked <bot[bot]>`,再执行 `query.sh history` 取本循环尚未核对的 review / 顶层评论 id,用 `query.sh details <id>...` 批量读取全文核对

## 每轮流程

三步:拉取状态 → 找出缺口 → 处置一批。

### 1. 拉取状态

```bash
bash scripts/poll.sh --repo-root <repo-root> <PR_NUMBER>
bash scripts/query.sh --repo-root <repo-root> <PR_NUMBER> <子命令>
```

poll.sh 的 stdout 是最小索引:本轮新评论带索引行(id / 判定 flags / 预览),旧评论折叠为计数,正文只在 snapshot 中;字段语义见 poll.sh header。索引与上轮无差异时折叠为单行 `no_change`,决策沿用上下文中的索引,上下文丢失时用 `query.sh index` 重新打印。正文按需用 query.sh 查询,子命令见其 header;查询异常一律以非零退出并输出 `QUERY_ERROR`,因此空结果即确无数据。

### 2. 找出缺口

按「目标状态」逐项核对,对每个缺口执行对应动作,同一轮可并行处理多家:

| 缺口 | 动作 |
|---|---|
| `checks_failing` 非空 | 立即诊断根因,修复随下一批处置一起 push;本轮无待处置批次(reviewer 均已通过或仅在等待)时单独 push 修复。根因在 main 且修复已合入时,在批次 push 前 rebase 到最新 main,随批次 `git push --force-with-lease`;rebase 后仍失败即无法修复,按 faults.md 暂停 |
| 某家参审 reviewer 未审当前 HEAD | 按 reviewers.md 该家「触发」规则决定等待或发送触发命令;发送后归入末行等待 |
| 至少一家有本轮新 actionable | 进入步骤 3 |
| `security_alerts.open_introduced` 与已认定误报在案清单(reviewers.md「已知误报」)的差集非空,且无对应新评论 | 上轮未修复完整(bot 不重复提醒):把差集中的 alert 数据带入步骤 3 按数据修复。前提是 CodeQL 分析完成且成功,否则归入下行等待 |
| CodeQL 分析未完成(`codeql_checks.all_ok == false` 且 `failing` 为空) | 等待;不阻塞其它缺口,但阻塞终核 |
| 以上缺口均消失 | 执行目标状态**终核**;全部通过则正常退出,按 [references/retrospective.md](references/retrospective.md) 产出复盘随汇报交出 |
| 未全部达成且无可执行动作(含刚发送触发命令、reviewer 响应中) | `bash scripts/wait.sh --repo-root <repo-root> <PR_NUMBER>`(命令上限 1800 秒),返回后回到步骤 1;`WAIT_TIMEOUT` 后仍无动作,按 faults.md 的 30 分钟无响应条目处理 |

**fix-up 顺延**:仅在决定是否重新触发手动重审型 reviewer 前,对最近的 push 批次执行 `bash scripts/classify_commits.sh --repo-root <repo-root> <PR_NUMBER> [SINCE_SHA]`(SINCE_SHA 取上一批次末 commit 的 `oid`,首批次以 `base_oid` 为界),按 reviewers.md「通用约定」判定。脚本报 `WARNING: SINCE_SHA ... is not on PR` 时按其 header 的处置:不顺延,重新触发审查。

### 3. 处置一批

把本轮所有 reviewer 的新 actionable **合并为一批**:按索引选出条目,用 `query.sh details <id>...` 一次取全文;Gemini 最新 summary `has_pass_marker == false` 时再取 `gemini-latest-body`——部分建议只出现在 summary 中。code scanning 的评论并入同一批,处置落点见 reviewers.md。账本轮数(索引 `rounds`)达到评估点后,处置前先读 [references/convergence.md](references/convergence.md),评估、汇报与处置阶梯均以其为准。

运行 `/receiving-code-review` 取得评估与回复的纪律。**修复形状**与其逐条实施的要求冲突时以本节为准——逐条实施会产生分散的小补丁;修复取「回到合理形态」的最小改动:

- **YAGNI**:对防御性意见(新增检查、兜底、try-except、默认值、空值分支),先确认它要防的失败路径是否真实存在,即能否指出一个具体的调用方或输入触发它。能指出则实施,并在 commit 说明中一句话记录该路径;不能指出则回复评论说明理由,代码保持原样。`receiving-code-review` 的 `YAGNI Check` 检查的是静态未被调用的代码,这里检查的是运行时不可达的分支
- **Duplicated Code**:本批中有两条以上意见指向同一处逻辑或同一根因时,在已有抽象内合并为一处改动

`gh` 读命令遇 API 瞬断会输出截断内容且退出码仍为 0——写回远端(如 `pr edit --body`)前先确认读到的是完整原文。

本批处置完成后记账,然后回到步骤 1:

```bash
bash scripts/round.sh --repo-root <repo-root> <PR_NUMBER> mark --implemented <n> --pushback <n> --note "<一句话归类>"
```

## 轮次与收敛

**一轮** = 一批新反馈 → 一次处置 push 或全部 pushback 回复发出。修复 CI 随批次 push,不单独计轮;rebase、触发命令均不计为轮。账本落盘在 poll.sh snapshot 旁,`round.sh show` 可随时重读,上下文压缩或更换执行者后轮数仍可恢复。

预算默认**评估点 3、硬停 6**,委派方指令可给出其它数值;账本只累计,不清零。轮数到达硬停值,记账后回到步骤 1 执行最后一轮:该轮有 push 的等待 reviewer 复审,纯 pushback 的直接终核;目标状态全部通过则正常退出,出现新 actionable 则停止,本批留待委派方裁决,按 convergence.md「硬停汇报」汇报。

## 暂停询问委派方

无人值守的例外只有三类:

- **reviewer 之间冲突**:同一议题 A 家主张 X、B 家反对 X,交委派方裁决
- **业务取舍**:修复方案在前向兼容、性能、用户体验上有显著差异,可能影响业务意图
- **故障**:reviewer 无响应、bot 报错、配额、CodeQL 失败或不可用、`gh` 权限、语义模糊的评论——条件与处置见 [references/faults.md](references/faults.md)
