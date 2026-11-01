---
name: model-routing
description: 委派任务时选模型。为 subagent 或 teammate 选 Category、模型与 effort，或为方案与实现选跨 Harness reviewer 时使用。
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
| `visual-engineering` | 视觉设计、UI/UX、前端、样式、动画和设计系统 |
| `artistry` | 创造性、非常规的问题解决 |
| `writing` | 文档、技术写作及其他文字内容 |

`deep-*` 与 `ultrabrain` 路由到 GPT。GPT 倾向过度遵循，目标模糊时容易过度设计、过度防御、过度测试，所以这三档以目标明确为前提；目标或边界尚需判断的复杂任务选 `unspecified-high`。

## Review

方案与实现的审查单独走 Review 路由：reviewer 取实现者所在 Harness 的对侧，映射见 [models.md](models.md) 的 Review 表。
