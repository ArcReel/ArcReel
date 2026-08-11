# Evaluator 契约

三个 evaluator 并行、只读运行，接收同一份带 `CAND-` ID 的候选清单与仓库路径。每个输入 ID 恰好返回一次 `verdict` 和简短 `summary`；无法验证或不适用时保留原 ID 并说明原因。

## Architecture

运行 `/codebase-design`，结合 ETC、DRY 审阅候选涉及的代码与测试。判断触发路径、责任归属和收益是否值得跟进；引用真实代码路径。

## Product and user

追踪真实用户路径，必要时读取 Spec、issue 与 PR。说明受影响者、触发时机、用户结果、实施成本和回归风险；未知事实不制造精确数字。

## Knowledge maintenance

运行 `/domain-modeling` 判断 CONTEXT 与 ADR，运行 `/writing-for-agents` 判断 agent instructions；同时判断工程候选是否只需知识维护或已被现有文档覆盖。
