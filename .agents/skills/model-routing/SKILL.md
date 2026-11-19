---
name: model-routing
description: 启动 subagent 或向其他 agent 分派任务前使用，用于选择执行任务的 Harness 和模型。
---

# 模型路由

委派前按任务性质选一个 Category，再到 [models.md](models.md) 查它对应的 Harness、模型与 effort。首选不可用时改用同行的 fallback，并在汇报中注明降级。

## Category

| Category | 适用任务 |
|---|---|
| `quick` | 琐碎任务、简单修改 |
| `unspecified-low` | 一般通用任务 |
| `unspecified-high` | 复杂通用任务，包括目标或边界尚需判断的任务 |
| `deep-low` | 目标与验收标准明确、需要深入自主执行的任务；关键决策可通过证据解决 |
| `deep-high` | 目标与验收标准明确的深度任务，但关键决策无法仅凭证据确定 |
| `ultrabrain` | 目标明确的极难逻辑推理与复杂算法问题 |
| `architect` | 系统级架构设计与技术权衡，只提供方案、不负责实现 |
| `visual-engineering` | 视觉设计、UI/UX、样式、动画和设计系统，以及以实际渲染或交互效果为主要验收项的前端任务 |
| `artistry` | 创造性、非常规的问题解决 |
| `writing` | 文档、技术写作及其他文字内容 |

`deep-low`、`deep-high` 和 `ultrabrain` 以目标与验收标准明确为前提。目标或边界尚需判断的复杂任务选 `unspecified-high`。

## Review

方案与实现的审查单独走 Review 路由：reviewer 与实现者使用不同的 Harness，映射见 [models.md](models.md) 的 Review 表。被审查的改动来自多个 Harness 的实现者时，按改动量最大的实现者查表。首选 reviewer 不可用时改用同行的 fallback。
