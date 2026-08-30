# 模型选择

每个角色启动前按下列依据选择实际可用模型与 reasoning effort，并记录一句理由。模型别名不可用时，直接选择满足同一角色目标的模型，不维护 fallback 表。

- **Implementer**：按 issue 的跨模块程度、歧义与风险选择。
- **Local reviewer**：使用擅长独立推理与代码审查的强模型；必须是未参与该 issue 实现的干净上下文。
- **Review looper**：在 stage PR 和完整 diff 就绪后，按整体复杂度选择。
