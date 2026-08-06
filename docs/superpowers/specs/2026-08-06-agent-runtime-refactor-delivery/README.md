# ChatGPT Pro Agent Runtime 重构交付包

本目录原样归档 ChatGPT Pro 基于提交 `6883b9fd4cfc546c68c3f84c5c4f0c81edab677e` 产出的目标 profile、代码修改契约与 eval，用作后续实施参考。不要把其中的 `agent_runtime_profile/` 直接覆盖到运行目录：目标 profile 依赖尚待实现的服务端工作流接口。

实施时以同级的 `../2026-08-06-agent-runtime-refactor-design.md`、根 `CONTEXT.md` 与 ADR 0057–0061 为最终决策。它们明确修订了原始契约，包括：移除产品 sheet 审核、迁移时直接认定旧产物为 current、局部内容 provenance、stale 继续可用且不自动重生，以及任务结果与产物时效分离。
