---
name: afk-retrospective
description: 为刚完成的 AFK 批次生成中文 HTML 复盘报告。
disable-model-invocation: true
---

# AFK 复盘

读取当前会话刚结束的 AFK 批次，生成只读报告供用户裁决。只在无法从会话确定 `batch-id` 时询问用户。

## 1. 提取候选

通读 `.afk/<batch-id>.jsonl` 与 `.afk/<batch-id>/handoff-*.md`，提取 follow-up、CONTEXT、ADR、agent instructions 和待用户裁决项。`fault`、`merge` 与普通 `decision` 只进入执行历程；pushback 只作来源，除非 handoff 已将其提升为候选。

运行 `git fetch origin`，在最新 `origin/main` 验证工程候选。淘汰已修复、不可达或前提错误的项；按“同一触发路径 + 同一预期改变”去重。为保留项分配临时 `CAND-` ID，确保每条原始候选都有保留项或淘汰理由。

## 2. 独立评估

有候选时，读取 [evaluator-prompts.md](references/evaluator-prompts.md)，同时委派三个干净上下文的只读 evaluator；没有候选时跳过。team-lead 确认每个临时 ID 收齐三个结果并复查冲突事实；无法消解的冲突写入对应候选的未知项。每个临时 ID 收齐三个结果后，本步骤完成。

## 3. 生成报告

读取 [report-content-contract.md](references/report-content-contract.md) 决定推荐强度、知识动作并编写正文，再按 [analysis-contract.md](references/analysis-contract.md) 在操作系统临时目录写 `analysis.json`。待裁决项同时写结构化互斥选项。确认每个报告 ID、来源和保留候选都已纳入后运行：

```bash
uv run python .agents/skills/afk-retrospective/scripts/render_report.py \
  --repo-root <repo-root> \
  --batch-id <batch-id> \
  --analysis <analysis-json>
```

renderer 负责校验批次边界、可渲染字段与来源引用，嵌入 ledger/handoff 快照并生成 HTML。失败时报告错误并停止；成功条件是报告覆盖所有保留候选、每篇正文满足内容契约、所有来源可到达对应 ledger 事件或 handoff、每个待裁决项至少有两个互斥选项，且存在可行动候选时已给出 Top recommendation。达成后删除临时 JSON、打开报告并提供绝对路径。

只问用户要处理哪些报告 ID；待裁决项接受 `DEC-01 = DEC-01-A` 形式的回答。用户裁决前保持仓库内容不变。
