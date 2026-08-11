---
name: afk-retrospective
description: 为刚完成的 AFK 批次生成可视化复盘报告。
disable-model-invocation: true
---

# AFK Retrospective

以当前会话刚结束的 AFK 批次账本与 handoff 为输入，生成一份只读 HTML 复盘报告。

## 1. 锁定输入

从当前会话确定刚完成的 `batch-id`。只有无法唯一确定时才询问用户。

确认以下条件全部成立：

- `.afk/<batch-id>.jsonl` 存在，末条事件为 `closed`
- `.afk/<batch-id>/` 存在，且包含本批 handoff
- 本次复盘紧接批次结束；不兼容历史账本或缺失 handoff 的旧批次

任一条件不满足就列出缺失条件并停止。

## 2. 提取并验证候选

通读当前 ledger segment 与全部 handoff，提取：follow-up、CONTEXT、ADR、agent instructions、gap、shelve，以及清尾分拣中转呈的工程事项。`fault`、`merge` 与普通 `decision` 只进入执行历程；pushback 只作证据，除非 handoff 明确把它提升为 follow-up。

先运行 `git fetch origin`，再在最新 `origin/main` 验证每条工程候选的触发路径。已修复、不可达或前提错误的项淘汰；对仍成立的项按“同一触发路径 + 同一预期改变”语义去重并保留全部来源。产品背景不足时再查 Spec、issue 或 PR。去重后为每项分配 `CAND-001` 形式的 working ID。

完成标准：每条原始候选都已映射到一个带 working ID 的保留项或一条淘汰理由，没有遗漏，也没有把同一事项重复计权。

## 3. 独立评估

有候选时，按 [evaluator-prompts.md](references/evaluator-prompts.md) 同时委派三个干净上下文的只读 evaluator；候选为空时跳过委派。team-lead 按 working ID 汇总事实、解释分歧并给出简短结论，不套数值公式。

完成标准：三个 evaluator 的输出均满足其契约，team-lead 已汇总结论。

## 4. 生成报告数据

按 [analysis-contract.md](references/analysis-contract.md) 在操作系统临时目录写一份 `analysis.json`。agent 只写结构化 JSON，HTML 由 renderer 生成。

完成标准：renderer 接受 `analysis.json`，且每个保留 working ID 都映射到报告项。

## 5. 渲染并交付

运行：

```bash
uv run python .agents/skills/afk-retrospective/scripts/render_report.py \
  --repo-root <repo-root> \
  --batch-id <batch-id> \
  --analysis <analysis-json>
```

成功后删除临时 `analysis.json`，打开报告并向用户提供绝对路径。最后只问用户要处理哪些报告 ID；待裁决项同时接受 `DEC-01 = DEC-01-A` 形式的选项 ID。用户裁决前保持仓库内容不变。

完成标准：HTML 已生成并打开，ledger segment 与全部 handoff 原文已嵌入，用户可以按报告 ID 与选项 ID 无歧义地回答下一步。
