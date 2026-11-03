---
name: pr-ai-review-loop
description: PR AI review 收敛。用户或 team-lead 要求启动或继续审查与修复循环时使用；不用于以 GitHub reviewer 身份产出审查意见，或只处理单条评论。
---

# PR AI Review 收敛

以可合并的代码质量为目标。完成意味着每条发现都有处置，而不是所有 bot 都停止提建议。

委派方指发起循环的用户或 team-lead。进入前确认已有非 draft PR，checkout 对应其最新 HEAD，并理解 PR 的验收边界；创建 PR、转为 ready 和合并由委派方或上层流程负责。

## 工程判断

发现是待证实的线索：你能从代码和契约还原出触发路径与错误结果，它才成立；未能证实的按误报或 YAGNI 结案。是否属于本 PR、怎样修复，同样由你判断。AI reviewer 对任何可想象的路径都会提建议，每采纳一条，下一轮就多一段新代码要审；判断的产出是处置，修改只是其中一种。

**定性。** 读完整批，先逐条定性再动代码：本 PR 的**缺陷**才修；**YAGNI**、**nitpick**、**存量**与**误报**以回复结案，存量按 [escalation.md](../../../docs/agents/escalation.md) 分流。缺陷须常规可达，判据见 [general.md](../../../docs/standards/general.md)「防御只针对常规可达的场景」。严重度标签是 reviewer 的估计，定性是你的结论。

**证据。** 从项目契约、真实调用路径和信任边界判断实际后果。触发路径存在只是起点，还要说明当前行为为什么错误。可执行的行为缺陷优先用修复前失败、修复后通过的测试验证；难以稳定复现时，用明确的代码因果链或接口契约支撑。安全与数据完整性风险优先核实，修法仍独立判断；仍需取舍时按 escalation.md 判断是否交委派方裁决。

**根因。** 修根因而非症状。reviewer 指的位置和附带的 patch 通常停在症状处；修改落在负责该保证的那一层，利用已有的不变量、抽象和错误处理。在症状处加防御是补丁的信号。以最终设计的清晰度衡量最小修复，而非改动行数。验证后通看整个 PR diff，确认各轮修改仍组成一份连贯的实现。

**收敛。** 后续审查重点是已接受修复及其回归；新发现仍先定性。已裁决的议题仅在出现新证据或相关前提变化时重开。多轮围绕同一处加补丁时，回到根因重新整理实现，而不是继续叠加局部防御。

## 推进循环

首次进入读 [reviewers.md](references/reviewers.md)，按各家的协议确认审查覆盖、读取发现、补齐必要复审。下列命令从本 skill 目录执行，`<repo-root>` 始终指向目标 PR checkout；参数和字段定义以脚本头注释为准。

```bash
bash scripts/poll.sh --repo-root <repo-root> <PR_NUMBER>
bash scripts/query.sh --repo-root <repo-root> <PR_NUMBER> details <id>...
```

索引用于定位，正文按需读取。`no_change` 只表示索引未变；上下文丢失时用 `query.sh ... index` 恢复。索引出现新发现时带 `triage_hint`，提示回到定性。

合并各家本轮发现、CI 失败和新安全告警，先完成整批定性，再修复、运行相关测试与闸门并集中 push。CI 根因已在 main 修复时，同步主线并重新验证。每条发现按 reviewers.md「处置写回」在案；同根因的多条发现可以引用同一处置结论。

每处置一批记一轮，包括全部以回复结案、没有 push 的批次：

```bash
bash scripts/round.sh --repo-root <repo-root> <PR_NUMBER> mark --implemented <n> --pushback <n> --note "根因与处置"
```

`--pushback` 计入以回复结案的发现。CI 修复、rebase 和触发命令本身不计轮。继续拉取状态；仅在缺少审查或检查结果且无可执行动作时，前台运行 `bash scripts/wait.sh --repo-root <repo-root> <PR_NUMBER>`。超时、配额、权限或 bot 异常按 [faults.md](references/faults.md) 处理。

## 完成条件

宣布收敛前，确认当前 HEAD 同时满足：

- **覆盖有效**：每家参审 reviewer 的审查已完成，覆盖当前变更；允许沿用的情形见 reviewers.md「身份与触发」。故障停用单独报告，不能记作通过。
- **发现已处置**：本循环的每条实质发现，包括历史 inline、review body 和 summary，都已定性；缺陷已修复，其余已在案回复，不存在未裁决的重要风险。在案即可，无需等待 bot 同意或撤回。
- **检查通过**：相关测试、闸门与当前 HEAD 的 required checks 通过，CodeQL 分析和新增安全告警满足 reviewers.md「CodeQL 安全退出门槛」。`checks_failing` 为空不代表所有检查已完成。

终核重新 poll；对 CodeRabbit 与 Codex 各查一次 `query.sh ... unacked <bot[bot]>`，并用 `history` / `details` 补齐尚未核对的正文。旧发现是否遗漏以实际修复和 PR 回复为准，不以 bot 的 ack 数量判断。终核期间 HEAD 改变时，按新 HEAD 重新核对。

## 预算与交接

默认评估点 **3**、硬停 **6**，委派方可覆盖。账本用 `round.sh ... show` 恢复，接力沿用累计轮数。到评估点简要报告进展：仍在消除真实缺陷，还是主要在往复、扩大范围。定性标准各轮一致，不因临近预算而放宽或收紧。

达到硬停后，只等待最后一批的必要复审并终核；满足完成条件即可退出，否则停止并交接未决事项。预算耗尽不等于质量通过。

按 escalation.md 需要找用户的事项及时交委派方裁决；reviewer 冲突与其余分歧先核验证据，再自行判断。退出时报告最终 HEAD、轮数、验证结果、关键修复、不修改的依据和未决事项。超出现有记录或范围的线索附依据并区分事实与猜测：委派方是 team-lead 时作为后续事项交给它；否则自行按 escalation.md「不找用户的事项怎么处理」分流。已有记录覆盖的不重复提出。退出前按 [lessons.md](../../../docs/agents/lessons.md)「收尾自查」自查，候选以 `lesson-candidate` 评论发在本 PR。
