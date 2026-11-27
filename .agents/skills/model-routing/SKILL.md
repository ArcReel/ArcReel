---
name: model-routing
description: 委派执行或审查任务时使用，按任务性质或实现者 Harness 选择模型与 effort。
---

# 模型路由

委派前先确定路径，再到 [models.md](models.md) 查模型与 effort，按模型所属系列选择 Harness：GPT 用 Codex，Claude 用 Claude Code，Gemini 用 Antigravity CLI。首选不可用时按同行 fallback 链依次重试，并在汇报中注明降级情况。

## Category

日常执行从以下 6 档选择：

| Category | 适用任务 |
|---|---|
| `quick` | 单文件或机械修改、依赖与配置更新、简单修补，无需跨模块推理 |
| `standard` | 目标与验收标准明确的常规功能实现、中等改动，以及常规流程核验与验收（默认档） |
| `complex` | 跨多模块重构、状态机/并发/调度改造，或目标与边界尚需权衡的复杂实现 |
| `debug-fix` | 已有明确报错、失败用例或失败清单的根因排查与定点修复，以及边界清晰的并发、性能或算法攻坚 |
| `visual` | 前端 UI/UX、组件样式、布局、动画与交互实现，以及以实际渲染或视觉效果为主验收项的任务 |
| `writing` | README、用户指南、开发/运维文档、ADR、规范与 Release Note 等技术文档及文案 |

规划期方案权衡或执行期遇阻塞需升级裁决时，选用规划专用档：

| Category | 适用任务 |
|---|---|
| `architect` | 系统级架构设计与非常规方案破局；只产出方案与技术权衡，交由后续实现 |

## Review

方案与实现的审查单独走 Review 路由：reviewer 与实现者使用不同的 Harness，映射见 [models.md](models.md) 的 Review 表。改动来自多个 Harness 的实现者时，按改动量最大的实现者查表。首选 reviewer 不可用时改用同行 fallback。
