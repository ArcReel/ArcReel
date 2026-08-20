# Analysis JSON

写一份 UTF-8 JSON。文本使用中文；信息未知时省略可选字段，必填结论无法验证时明确说明。`sources` 引用 ledger 事件 ID（如 `EV-0007`）或 handoff 文件名（如 `handoff-123.md`），不复制原文。

```json
{
  "version": 2,
  "batch": {
    "title": "批次标题",
    "top_recommendation": {"id": "FU-01", "reason": "为什么应先处理它"}
  },
  "issues": [
    {"number": 123, "pr": 456, "title": "issue 标题", "state": "merged"}
  ],
  "followups": [
    {
      "id": "FU-01",
      "recommendation": "强烈建议",
      "title": "事项标题",
      "body_markdown": "问题、评估事实、分歧与 team-lead 结论。可使用 Markdown 表格与 fenced Mermaid。",
      "sources": ["handoff-123.md", "EV-0007"]
    }
  ],
  "knowledge_reviewed": ["CONTEXT", "ADR", "INST"],
  "knowledge": [
    {
      "id": "ADR-01",
      "target": "ADR",
      "action": "supersede",
      "recommendation": "强烈建议",
      "target_ref": "docs/adr/0018-video-duration-supported-durations-single-source.md",
      "title": "候选标题",
      "body_markdown": "按判据得出的解释与建议内容。",
      "sources": ["handoff-123.md"]
    }
  ],
  "pending": [
    {
      "id": "DEC-01",
      "kind": "搁置",
      "title": "需要用户决定的事项",
      "body_markdown": "为什么必须由用户决定，以及各方案的共同背景。",
      "positions": [
        {"id": "DEC-01-A", "label": "方案 A", "stance": "选择内容", "reason": "理由"},
        {"id": "DEC-01-B", "label": "方案 B", "stance": "另一选择", "reason": "理由"}
      ],
      "current_state": "当前保留状态",
      "sources": ["EV-0009"]
    }
  ]
}
```

`body_markdown` 接受 Markdown 与 fenced Mermaid（```` ```mermaid ````）；原始 HTML 不属于该 interface。renderer 直接使用所选 Mermaid 版本的标准渲染行为。

- `issues[].state`：`merged` / `shelved` / `not_started` / `done`
- `followups[].recommendation`：`强烈建议` / `值得探索` / `推测性` / `无需处理`；判据见 [report-content-contract.md](report-content-contract.md)
- `batch.top_recommendation`：存在可行动的工程或知识候选时必填；`id` 指向对应报告 ID
- `knowledge_reviewed`：固定且不重复地包含 `CONTEXT`、`ADR`、`INST`，表示三个载体均已检查
- `knowledge[].target`：`CONTEXT` / `ADR` / `INST`
- `knowledge[].action`：`create` / `revise` / `retire` / `supersede` / `none`；允许矩阵与语义见 [report-content-contract.md](report-content-contract.md)
- `knowledge[].recommendation`：与工程候选使用同一套推荐强度；`none` 只与 `无需处理` 配对
- `knowledge[].target_ref`：`revise` / `retire` / `supersede` 时必填，`create` / `none` 时省略
- 报告 ID：`FU-01` / `DEC-01` / `CTX-01` / `ADR-01` / `INST-01`，全局唯一
- 待裁决项至少提供两个互斥选项，选项 ID 使用所属决策 ID 加大写后缀
- `knowledge` 为空表示三个载体均已检查且没有候选
