# Analysis JSON contract

renderer 接受 UTF-8 JSON，顶层 `version` 固定为 `1`。所有文本面向最终用户，使用中文；可选信息未知时省略，必填文本无法验证时明确写出，不编造。

## Shape

```json
{
  "version": 1,
  "batch": {
    "title": "批次标题",
    "summary": "一段批次结论",
    "headline_ids": ["FU-01", "DEC-01"],
    "counts": {"merged": 3, "shelved": 1, "not_started": 1, "cleanup_filed": 0}
  },
  "issues": [
    {
      "number": 123,
      "title": "issue 标题",
      "state": "merged",
      "pr": 456,
      "model": "可选",
      "blocked_by": null,
      "stages": [{"name": "实现", "status": "done", "rounds": null}]
    }
  ],
  "followups": [
    {
      "id": "FU-01",
      "candidate_ids": ["CAND-001"],
      "priority": "P1",
      "title": "事项标题",
      "summary": "问题与预期改变",
      "status": "已在 origin/main 验证",
      "origin": {"issues": [123], "prs": [456], "where": "handoff-123 审查循环"},
      "evaluations": {
        "architecture": {"verdict": "支持", "summary": "..."},
        "product_user": {"verdict": "支持", "summary": "..."},
        "knowledge": {"verdict": "无关", "summary": "..."}
      },
      "conclusion": "team-lead 汇总结论",
      "evidence": [{"kind": "handoff", "ref": "handoff-123.md", "text": "原文摘录"}]
    }
  ],
  "gates": [
    {
      "key": "CONTEXT",
      "label": "CONTEXT.md",
      "rule": "领域术语的当前形状发生变化",
      "empty_note": "无候选时的说明",
      "items": [
        {
          "id": "CTX-01",
          "candidate_ids": ["CAND-002"],
          "verdict": "应更新",
          "title": "候选标题",
          "summary": "判断依据",
          "suggestion": "可选的建议文本",
          "origin_issues": [123],
          "checks": [{"label": "判据", "passed": true, "reason": "原因"}],
          "evidence": []
        }
      ]
    }
  ],
  "pending": [
    {
      "id": "DEC-01",
      "candidate_ids": ["CAND-003"],
      "kind": "搁置",
      "title": "需要用户决定的事项",
      "context": "为什么必须由用户决定",
      "positions": [
        {"id": "DEC-01-A", "label": "方案 A", "stance": "选择内容", "reason": "理由"},
        {"id": "DEC-01-B", "label": "方案 B", "stance": "另一选择", "reason": "理由"}
      ],
      "current_state": "当前保留状态",
      "origin_issues": [123],
      "evidence": []
    }
  ],
  "pushbacks": [{"pr": 456, "reviewer": "Codex", "topic": "建议", "ruling": "驳回", "reason": "依据"}],
  "reply_defaults": ["FU-01"]
}
```

## Enumerations

- `issues[].state`: `merged` / `shelved` / `not_started` / `done`
- `stages[].status`: `done` / `halted` / `skipped`
- `followups[].priority`: `P0` / `P1` / `P2` / `无需处理`
- gate `key`: `CONTEXT` / `ADR` / `INST`，三类各出现一次
- gate verdict：CONTEXT 为 `应更新` / `无需更新`；ADR 为 `应记录` / `无需记录`；INST 为 `应更新` / `无需更新`
- `candidate_ids`：非空且单项内不重复，格式为 `CAND-001`；同一 working ID 可映射到多个最终报告项
- 报告项 ID 前缀：follow-up 用 `FU-`，待裁决项用 `DEC-`，gate item 按所属 gate 分别用 `CTX-` / `ADR-` / `INST-`
- `pending[].positions`：至少两个互斥选项；选项 ID 使用所属决策 ID 加大写后缀，例如 `DEC-01-A`

`evidence` 始终使用同一形状，只承担报告展示，不要求与快照做文本级强一致性校验。ID 在 follow-up、gate item、pending 三类中全局唯一；决策选项 ID 也全局唯一；`headline_ids` 只能引用这些报告 ID，`reply_defaults` 只能引用 follow-up 或 gate item。
`issues[].pr` 与 `pushbacks[].pr` 可省略或为 `null`；存在编号时必须是正整数。
