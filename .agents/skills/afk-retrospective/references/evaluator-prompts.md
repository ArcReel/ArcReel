# Evaluator contracts

只在候选非空时读取本文件。三个 evaluator 并行、只读运行，输入同一份带 `CAND-` working ID 的候选清单与仓库路径，不传递其他 evaluator 的判断。

共同输出契约：对每个输入 ID 恰好返回一次 `id`、`verdict` 与简短 `summary`；无法验证或不适用时原样返回 ID 并明确说明。

## Architecture

运行 `/codebase-design`，结合 ETC、DRY 逐条审阅候选涉及的代码与测试。判断候选是否成立、责任应落在哪里，以及预期收益是否值得跟进；`summary` 引用真实代码路径。

## Product and user

逐条追踪真实用户路径，必要时读取 Spec、issue 与 PR。判断谁会遇到、何时触发、影响范围与损失、收益、实施成本和回归风险；`summary` 说明可达性与用户结果。技术整洁本身不算用户收益，未知事实不用数字制造精确感。

## Knowledge maintenance

运行 `/domain-modeling` 评估 CONTEXT 与 ADR 候选，运行 `/writing-for-agents` 评估 agent instructions 候选。同时判断工程候选是否只需知识维护，或已被现有文档覆盖；`summary` 写明相应判断。
