---
name: pr-ai-review-loop
description: PR AI review 收敛。用户或 team-lead 要求启动或继续审查—修复循环时使用；供本地实现者或受委派的 review-looper 调用，不用于 GitHub reviewer 产出审查意见或仅处理单条评论。
---

# PR AI Review 收敛

以可合并的代码质量为目标。Reviewer 提供待验证的发现，你负责判断问题是否成立、是否应在本 PR 解决，以及怎样修复。完成意味着风险已有处置，而不是所有 bot 都停止提建议。

委派方指发起循环的用户或 team-lead。进入前确认已有非 draft PR，checkout 对应其最新 HEAD，并理解 PR 的验收边界；创建 PR、转为 ready 和合并由委派方或上层流程负责。

## 工程判断

**证据。** 从项目契约、真实调用路径和信任边界判断实际后果。可执行的行为缺陷优先用修复前失败、修复后通过的测试验证；难以稳定复现时，用明确的代码因果链或接口契约支撑判断。触发路径存在只是起点，还要说明当前行为为什么错误。安全与数据完整性风险优先核实，修法仍独立判断。

**必要性。** 优先解决本 PR 引入、加剧或阻碍其验收的缺陷。对存量问题和改进建议，判断是否值得扩大当前变更；不修改或列为非阻塞候选也是有效处置，理由写回 PR。重要风险尚无结论时交委派方裁决。

**根因修复。** 先理解整批反馈，再按根因选择改动。接受问题不等于接受 reviewer 的 patch；在负责该保证的边界修复，利用已有不变量、抽象和错误处理。以最终设计的清晰度衡量最小修复，而非改动行数。验证后通看整个 PR diff，确认各轮修改仍组成一份连贯的实现。

**收敛。** 后续审查重点是已接受修复及其回归；新发现仍按上述标准判断。已裁决的议题仅在出现新证据或相关前提变化时重开。多轮围绕同一处加补丁时，回到根因重新整理实现，而不是继续叠加局部防御。

## 推进循环

首次进入读 [reviewers.md](references/reviewers.md)，按各家的协议确认审查覆盖、读取发现、补齐必要复审。下列命令从本 skill 目录执行，`<repo-root>` 始终指向目标 PR checkout；参数和字段定义以脚本 header 为准。

```bash
bash scripts/poll.sh --repo-root <repo-root> <PR_NUMBER>
bash scripts/query.sh --repo-root <repo-root> <PR_NUMBER> details <id>...
```

索引用于定位，正文按需读取。`no_change` 只表示索引未变；上下文丢失时用 `query.sh ... index` 恢复。合并各家本轮发现、CI 失败和新安全告警，先完成整批判断，再修复、运行受影响质量门并集中 push。对不修改的意见回复依据；同根因的多条意见可以引用同一处置结论。CI 根因已在 main 修复时同步主线并重新验证。

每处置一批记一轮，包括全部以回复结案、没有 push 的批次：

```bash
bash scripts/round.sh --repo-root <repo-root> <PR_NUMBER> mark --implemented <n> --pushback <n> --note "根因与处置"
```

`--pushback` 计入已回复的不修改意见，包括非阻塞处置。CI 修复、rebase 和触发命令本身不计轮。继续拉取状态；仅在缺少审查或检查结果且无可执行动作时，前台运行 `bash scripts/wait.sh --repo-root <repo-root> <PR_NUMBER>`。超时、配额、权限或 bot 异常按 [faults.md](references/faults.md) 处理。

## 完成条件

宣布收敛前，确认当前 HEAD 同时满足：

- **覆盖有效**：每家参审 reviewer 的审查已完成，覆盖当前变更；允许沿用的情形见 reviewers.md。故障停用单独报告，不能记作通过。
- **发现已处置**：本循环所有实质发现，包括历史 inline、review body 和 summary，均已修复、基于证据不修改或明确按非阻塞处置；不存在未裁决的重要风险。处置记录足够，无需等待 bot 同意或撤回。
- **质量门通过**：受影响质量门与当前 HEAD 的 required checks 通过，CodeQL 分析和新增安全告警满足 reviewers.md 的安全退出门槛。`checks_failing` 为空不代表所有检查已完成。

终核重新 poll；对三家各查一次 `query.sh ... unacked <bot[bot]>`，并用 `history` / `details` 补齐尚未核对的正文。旧意见是否遗漏以实际修复和 PR 回复为准，不以 bot 的 ack 数量判断。终核期间 HEAD 改变时，按新 HEAD 重新核对。

## 预算与交接

默认评估点 **3**、硬停 **6**，委派方可覆盖。账本用 `round.sh ... show` 恢复，接力沿用累计轮数。到评估点简要报告进展：仍在消除真实缺陷，还是主要在往复、扩大范围；判断标准从第一轮起一致。

达到硬停后，只等待最后一批的必要复审并终核；满足完成条件即可退出，否则带着未决事项停止交接。预算耗尽不等于质量通过。

有证据仍无法消解的 reviewer 冲突、重大业务取舍或重要风险，及时交委派方裁决；普通技术分歧自行判断。退出时报告最终 HEAD、轮数、验证结果、关键修复、不修改的依据和未决事项。只有确有超出现有记录或范围的长期价值时，才提出 ADR / CONTEXT / agent instructions / follow-up 候选；通常无需新增候选。
