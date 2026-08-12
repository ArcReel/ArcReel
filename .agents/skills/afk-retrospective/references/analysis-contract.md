# Analysis JSON

写一份 UTF-8 JSON。文本使用中文；信息未知时省略可选字段，必填结论无法验证时明确说明。`sources` 引用 ledger 事件 ID（如 `EV-0007`）或 handoff 文件名（如 `handoff-123.md`），不复制原文。

```json
{
  "version": 1,
  "batch": {
    "title": "批次标题",
    "summary": "批次结论"
  },
  "issues": [
    {"number": 123, "pr": 456, "title": "issue 标题", "state": "merged"}
  ],
  "followups": [
    {
      "id": "FU-01",
      "priority": "P1",
      "title": "事项标题",
      "summary": "问题与预期改变",
      "evaluations": {
        "architecture": {"verdict": "支持", "summary": "..."},
        "product_user": {"verdict": "支持", "summary": "..."},
        "knowledge": {"verdict": "无关", "summary": "..."}
      },
      "conclusion": "team-lead 汇总结论",
      "sources": ["handoff-123.md", "EV-0007"]
    }
  ],
  "knowledge": {
    "CONTEXT": [
      {
        "id": "CTX-01",
        "verdict": "应更新",
        "title": "候选标题",
        "summary": "按判据得出的解释",
        "suggestion": "可选的建议文本",
        "sources": ["handoff-123.md"]
      }
    ],
    "ADR": [],
    "INST": []
  },
  "pending": [
    {
      "id": "DEC-01",
      "kind": "搁置",
      "title": "需要用户决定的事项",
      "context": "为什么必须由用户决定",
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

- `issues[].state`：`merged` / `shelved` / `not_started` / `done`
- `followups[].priority`：`P0` / `P1` / `P2` / `无需处理`
- 报告 ID：`FU-01` / `DEC-01` / `CTX-01` / `ADR-01` / `INST-01`，全局唯一
- 待裁决项至少提供两个互斥选项，选项 ID 使用所属决策 ID 加大写后缀
- `knowledge` 固定包含 `CONTEXT`、`ADR`、`INST`；空数组表示已检查且没有候选
