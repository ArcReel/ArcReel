---
name: afk-retrospective
description: 为刚完成的 AFK 批次生成可视化复盘报告。
disable-model-invocation: true
---

# AFK Retrospective

把刚完成的 `$afk-team-workflow` 批次复盘成一份只读 HTML。账本与 handoff 是过程真相源；报告只给建议，等用户按报告 ID 明确裁决后再落地。

## 1. 锁定输入

从当前会话确定刚完成的 `batch-id`。只有无法唯一确定时才询问用户。

确认以下条件全部成立：

- `.afk/<batch-id>.jsonl` 存在，末条事件为 `closed`
- `.afk/<batch-id>/` 存在，且包含本批 handoff
- 本次复盘紧接批次结束；不兼容历史账本或缺失 handoff 的旧批次

任一条件不满足就 fail loud，说明缺少什么；不要猜测历史 segment，也不要从 transcript 重写缺失文件。

## 2. 提取并验证候选

通读当前 ledger segment 与全部 handoff，提取：follow-up、CONTEXT、ADR、agent instructions、gap、shelve，以及清尾分拣中转呈的工程事项。`fault`、`merge` 与普通 `decision` 只进入执行历程；pushback 只作证据，除非 handoff 明确把它提升为 follow-up。

先运行 `git fetch origin`，再在最新 `origin/main` 验证每条工程候选的触发路径。已修复、不可达或前提错误的项淘汰；对仍成立的项按“同一触发路径 + 同一预期改变”语义去重并保留全部来源。产品背景不足时再查 Spec、issue 或 PR。去重后为每项分配 `CAND-001` 形式的 working ID。

完成标准：每条原始候选都已映射到一个带 working ID 的保留项或一条淘汰理由，没有遗漏，也没有把同一事项重复计权。

## 3. 独立评估

有候选时，按 [evaluator-prompts.md](references/evaluator-prompts.md) 同时委派三个干净上下文的只读 evaluator；候选为空时跳过委派：

1. 架构：ETC、DRY 与 `$codebase-design`
2. 产品与用户：真实路径、影响范围、收益、成本与回归风险
3. 知识维护：用 `$domain-modeling` 判断 CONTEXT/ADR，用 `$writing-for-agents` 判断 agent instructions

三个 evaluator 不看彼此结论，并对每个输入 working ID 恰好返回一次；无法验证或不适用时也原样返回 ID 与 verdict。team-lead 按 ID 汇总事实、解释分歧并自行给出简短结论：

- follow-up：`P0` / `P1` / `P2` / `无需处理`
- CONTEXT：`应更新` / `无需更新`
- ADR：`应记录` / `无需记录`
- agent instructions：`应更新` / `无需更新`
- gap / shelve：`待裁决`，保留各方立场

不套数值公式。空候选是正常结果。

## 4. 生成报告数据

按 [analysis-contract.md](references/analysis-contract.md) 在操作系统临时目录写一份 `analysis.json`。agent 只写结构化 JSON，不直接创建、拼接或 patch HTML。

为每项分配报告内唯一 ID：`FU-`、`CTX-`、`ADR-`、`INST-`、`DEC-`，并保留其 `candidate_ids`。每个 `DEC-` 提供至少两个带稳定 ID 的互斥选项。把建议用户优先查看的 ID 放入 `batch.headline_ids`，把建议默认处理的 ID 放入 `reply_defaults`。

完成标准：renderer 对 `analysis.json` 的 schema 校验通过，所有 headline 与默认选择都能解析，每个保留 working ID 都出现在至少一项 `candidate_ids` 中。

## 5. 渲染并交付

运行：

```bash
uv run python .agents/skills/afk-retrospective/scripts/render_report.py \
  --repo-root <repo-root> \
  --batch-id <batch-id> \
  --analysis <analysis-json>
```

renderer 独占 HTML 模板、样式、转义与源文件快照，默认写入 `.afk/reports/<batch-id>/<timestamp>.html`。成功后删除临时 `analysis.json`；macOS 用 `open`、Linux 用 `xdg-open`、Windows 用 `start` 打开报告，并向用户提供绝对路径。

最后只问用户要处理哪些报告 ID；待裁决项同时接受 `DEC-01 = DEC-01-A` 形式的选项 ID。不要在本 skill 内修改 CONTEXT/ADR/agent instructions，也不要创建 issue。

完成标准：HTML 已生成并打开；ledger segment 与全部 handoff 原文已嵌入；每条保留候选恰好出现一次；用户可以按报告 ID 与选项 ID 无歧义地回答下一步。
