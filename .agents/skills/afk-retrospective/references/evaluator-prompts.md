# Evaluator 契约

每个 evaluator 接收仓库路径和同一份带 `CAND-` ID 的候选清单，以只读方式核验一个评估轴。每个输入 ID 恰好返回一个对象：

```json
{
  "id": "CAND-01",
  "finding": "supported | contradicted | unverified | not_applicable",
  "evidence": ["可复查的文件、issue、PR 或 ledger 引用"],
  "assessment": "已确认事实与候选主张的对应关系",
  "unknowns": ["证据仍缺失的事实"]
}
```

- `supported`：证据支持候选主张；
- `contradicted`：证据否定候选主张；
- `unverified`：证据不足，缺失项写入 `unknowns`；
- `not_applicable`：该评估轴不适用于候选。

## Architecture

运行 `/codebase-design`，结合 ETC、DRY 审阅候选涉及的代码与测试。核验触发路径、当前责任归属、预期改变、涉及文件与测试、改动范围和工程风险；引用真实代码路径。

## Product and user

从入口追踪到用户可见结果；候选引用 Spec、issue 或 PR 时读取对应来源。核验受影响者、触发条件、当前与预期结果、实施成本和回归风险。

## Knowledge maintenance

运行 `/domain-modeling` 核验 CONTEXT 与 ADR，运行 `/writing-for-agents` 核验 agent instructions。检查现有知识是否已覆盖候选，以及知识动作能否替代工程改动。

输出 ID 集合与输入候选 ID 集合相同。每个 `supported` 或 `contradicted` 结果至少包含一项可定位的 `evidence`。
