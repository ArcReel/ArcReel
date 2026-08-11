---
status: accepted
---

# 广告/短片为第三内容类型：骨架按生成路线派生

> 当前架构已用自包含 `video_units[]` 统一三种内容模式的参考路线；骨架与路线的现行契约见
> [ADR 0045](0045-script-skeleton-registry-dual-resolvers.md) 与
> [ADR 0055](0055-generation-route-locked-at-creation.md)。本 ADR 初版的
> `shots + reference_units` 派生分组只解释历史数据，不再是运行时契约。

广告/短片模式（带货短视频为主场景）产出单个视频而非多集系列，需要进入类型系统。决定：`ad` 作为 content_mode 第三值落地；生成路线仍是独立维度，按项目级 `generation_mode` 决定剧本使用分镜骨架还是参考视频骨架。

## 决定

- **`ad` 为 content_mode 第三值**：复用全部按 content_mode 分派的机制（profile 变体 `CLAUDE.ad.md`、SCRIPT_SHAPES、创建后不可变约束、StatusCalculator 分派）。
- **骨架按生成路线派生**：storyboard 路径使用平铺 `shots[]`（`shot_id`，E1S{n}），每镜头携带 `section` 与一等口播文案 `voiceover_text`；reference_video 路径使用自包含 `video_units[]`，每个 unit 自身承载正文、编排时长、引用与产物归属。运行时不读取或写回旧 `reference_units`。
- **ad 仅开放 storyboard 与 reference_video**：grid 不开放——宫格单格分辨率与产品高保真目标冲突，其画风一致性价值在 ad 由产品/风格参考承载。
- **恒单集承载**：ad 项目 episodes 恒为 `[{episode: 1, …}]`，剧本即 `scripts/episode_1.json`；按集机械（状态/归档/版本/费用/导出）零结构改动，前端对 ad 隐藏集语义。未来「一产品多变体」以每集=一个变体扩展。
- **镜头时长约束按 generation_mode 动态注入**：storyboard 路径按 supported_durations 硬枚举（模型能力约束）；reference 路径的 unit 持有 1–300 秒编排时长，生成预检再投影到供应商申请档位。

## 历史决定与替换原因

初版曾让两条广告路线都以 `shots[]` 为内容真相，并把 reference_video 分组持久化为只引用 `shot_id` 的 `reference_units` 索引。该形态要求运行时水合两层结构，也让参考路线与 narration/drama 的自包含 unit 契约分裂。现行设计改为所有内容模式的参考路线统一使用 `video_units[]`；旧 `shots + reference_units` 仅由项目 schema 迁移读取一次，迁移完成后业务代码只消费新骨架。

## Consequences

- VALID_CONTENT_MODES、SCRIPT_SHAPES、profile manifest、数据校验器、创建向导随第三值扩展；ad 专属字段（`target_duration`、`brief`、`products` bucket）见提案与 ADR 0034。
- 分集账本重设计需把 ad 视为恒单条账本/豁免拆分规划，不得对 content_mode 做二值假设。
- 存量广告参考剧本由启动迁移保留可证明的 unit 身份、边界与历史产物；迁移完成后不保留旧结构双读或重新派生入口。
