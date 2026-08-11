# Evaluator contracts

只在候选非空时读取本文件。三个 evaluator 并行运行，输入同一份带 `CAND-` working ID 的候选清单与仓库路径，不传递任何其他 evaluator 的判断。每个 evaluator 原样携带输入 ID；每个 ID 恰好返回一次，无法验证或不适用也返回明确 verdict。

## Architecture

读取 `$codebase-design`，逐条验证候选涉及的代码与测试。用 ETC、DRY、module、interface、depth、seam、adapter、leverage、locality 判断：问题是否真实、改变应集中在哪里、删除候选改动会让复杂度回到多少调用方。返回每项的 `verdict` 与简短依据；不设计具体 interface，不改文件。

完成标准：每个输入 ID 都有结论，且结论引用真实代码路径或明确写出无法验证或不适用。

## Product and user

逐条追踪真实用户路径，必要时读取 Spec、issue 与 PR。判断谁会遇到、何时触发、影响范围与损失、收益、实施成本和回归风险。返回每项的 `verdict` 与简短依据；不以技术整洁本身冒充用户收益，不改文件。

完成标准：每个输入 ID 都说明可达性与用户结果，或明确标注无法验证或不适用；未知事实不用数字制造精确感。

## Knowledge maintenance

读取 `$domain-modeling` 与 `$writing-for-agents`，只应用其判据，不落地修改：

- CONTEXT 只收领域专属术语的当前形状，不含实现细节
- ADR 必须同时满足难逆转、缺少上下文会意外、真实取舍
- agent instructions 必须能预防同类返工且现有指令未覆盖；指出应修的是 pointer、信息层级、完成标准、重复、sediment 或 no-op

同时判断工程候选是否其实只需知识维护，或是否已被现有文档覆盖。返回每项的 `verdict` 与简短依据。

完成标准：每个输入 ID 都有知识维护结论；CONTEXT/ADR/agent-instruction 候选通过对应门槛逐条核验，空类照实返回。
