---
name: lessons-sweep
description: 定期巡检合并记录，更新教训库，按阈值晋升或退出规范，产出一个自动合并的巡检 PR。
disable-model-invocation: true
---

# 教训巡检

你是 `docs/LESSONS.md` 的唯一写入者：核验并计数上次巡检以来的教训候选，按阈值晋升，清理过期条目与失效规则，产出一个巡检 PR。记录与晋升规则以 [lessons.md](../../../docs/agents/lessons.md) 为准，开工前通读；本文只写步骤。是否找用户、范围外的缺陷是否开 issue，按 [escalation.md](../../../docs/agents/escalation.md) 判断。巡检通常无人值守运行，需要用户的事项开 `needs-triage` issue，其余部分照常推进。

## 1. 开工检查

1. fetch `origin`，用 `gh pr list --state open --limit 1000 --json number,headRefName` 列出全部 open PR（默认只返回 30 条），找出 head 分支以 `lessons-sweep/` 开头的 PR。
2. 存在这样的 PR 时接手它，本次巡检只处理它；新窗口留给下一次巡检，这样窗口首尾相接，同一时间只有一份教训库改动。接手路径上的 `gh pr` 命令都显式传入它的 PR 号，改动在其 head 分支的工作树中进行。先读 PR 上的阻塞评论，并用 `gh pr view <PR 号> --json isDraft,autoMergeRequest,mergeStateStatus,statusCheckRollup` 查看状态：
   - 已开启 auto-merge、没有冲突、required checks 没有失败的：确认后结束。
   - 阻塞原因与上一次接手时相同：对应的 `needs-triage` issue 仍 open 时补一条评论后结束，没有就开一个；issue 已关闭的，按其中的裁决处理后转下一项。
   - 其余：rebase 到最新 `origin/main`，按第 6 步第 2 项跑闸门，用 `--force-with-lease` push，更新 PR 正文；PR 为 draft 时用 `gh pr ready` 转为 ready，再执行第 6 步第 4 项。
3. 读 `docs/LESSONS.md` 顶部的 `<!-- reviewed-through: <SHA> <日期> -->`。窗口是 `<SHA>..origin/main`，记下此刻 `origin/main` 的 SHA 作为窗口终点，从窗口终点创建 `lessons-sweep/<YYYY-MM-DD>` 分支与工作树。后续改动都在这里进行。

## 2. 收集候选

窗口内每个合并 PR 都要过一遍。普通 PR squash 合并，AFK stage PR rebase 合并，所以按提交反查 PR：对 `git log <SHA>..<窗口终点> --first-parent` 的每个提交调用 `gh api repos/{owner}/{repo}/commits/<sha>/pulls`，去重得到 PR 列表。候选评论在 PR 的 issue comments，审查意见在 reviews 与 review comments；squash 合并的 PR，修复提交只在该 PR 的 commits 列表里。

- PR 上首行为 `<!-- lesson-candidate -->` 的评论。
- 被采纳并修复的审查意见，以及 PR 内的 fix 提交。
- 窗口内 main 上的 revert 提交与 `fix` 类型提交。
- 窗口内 main 上失败的 CI 运行。

后三类没有现成的候选文字：符合 lessons.md「收尾自查」判据的，按条目格式整理成候选。

## 3. 核验与计数

逐条核验候选：现象、根因与没拦住的原因由你对照代码、diff 与 CI 日志查证，评论里的结论只作线索。不成立的候选，以及不符合 lessons.md「收尾自查」判据的候选，一律丢弃。

成立的候选按「模式」比对现有条目：同类就追加一条出现记录，否则新建条目，按 lessons.md「分组」归组。计数单位见 lessons.md。

## 4. 清理与退出

1. 按 lessons.md「计数与晋升」的过期规则删除条目。
2. 按 lessons.md「规范退出」的统计窗口，统计审查评论中 `docs/standards/` 各规则标题被引用的次数。
3. 按 lessons.md「规范退出」处理已做成检查的、架构上已不可能再犯的，以及零引用的规则。裁定保留的零引用规则列进 PR 正文，后续巡检据此确定重新裁决的起点。

## 5. 晋升

达到 lessons.md 晋升阈值的条目逐一按 lessons.md「计数与晋升」裁决去向。注明了 issue 号的条目：issue 仍 open 的跳过；已关闭的按其中的裁决处理，采纳就按去向执行，否决就删除条目。按改动规模选择执行方式：

- **文字规则**：写进路径对应的 `docs/standards/` 文件，体例与相邻规则一致；条目移到「已晋升」区。
- **小而自洽的检查**（`scripts/audit_conventions.py` 加一项、ESLint `no-restricted-syntax`、一个测试）：在本 PR 实现，确认它在当前代码上通过、对该教训的反例报错；条目移到「已晋升」区。
- **其余去向**（skill、导航指针、架构文档，以及需要大面积改动的检查）：按 escalation.md「范围外的真缺陷」的三步开 `ready-for-agent` issue，条目标注「已晋升，见 #<issue>」并移到「已晋升」区。
- **符合 escalation.md 找用户判据的**：先查重，再开 `needs-triage` issue，写明选项与推荐；条目留在原组，注明 issue 号。

## 6. 产出 PR

第 3–5 步没有产生任何改动时，PR 只推进 reviewed-through 标记，下一次巡检就不必重扫同一窗口。依次执行：

1. 把 reviewed-through 标记更新为窗口终点的 SHA 与日期，提交本次全部改动。
2. 运行 `uv run python scripts/gate.py --changed` 直到通过。
3. 创建 PR，标题为 `chore(lessons): <YYYY-MM-DD> 教训巡检`。正文列出：新增教训、计数变化、清理掉的教训、晋升及去向、退出的规则、agent 自行裁决的事项（各附一行理由）、窗口内 agent 新开的 issue 数（带 `ready-for-agent` 或 `needs-triage` 标签，两类分开计数）。
4. PR 只推进标记时，直接用 `gh pr merge --auto --squash` 开启 auto-merge。其余情况使用 Skill 工具调用 `pr-ai-review-loop`，由你担任委派方：需要用户的事项开 `needs-triage` issue，从本 PR 撤下相关改动（条目留在教训库，注明 issue 号），其余改动照常收敛；follow-up 按 escalation.md 分流。收敛后用 `gh pr merge --auto --squash` 开启 auto-merge。

## 失败与停止

遇到以下情况时停止，报告停在哪一步、原因与现场。按情况分别收尾：

- reviewed-through 标记缺失、无法解析，或 SHA 不是 `origin/main` 的祖先；`gh` 认证或 API 访问失败；候选收集不完整；其他在第 6 步第 1 项提交前无法完成的步骤：丢弃分支与工作树，不 push、不建 PR。标记保持不动，下一次巡检从同一窗口重跑。
- 闸门持续失败，或 `pr-ai-review-loop` 硬停时仍有未决事项：push 分支，PR 尚未创建的创建 draft PR。不开启 auto-merge，在 PR 上评论阻塞原因，下一次巡检在第 1 步接手。

标记只随巡检 PR 合并而推进，未完成的窗口留给后续巡检。
