---
status: accepted
---

# 广告/短片为第三内容类型：剧本骨架唯一、生成路径正交

广告/短片模式（带货短视频为主场景）产出单个视频而非多集系列，需要进入类型系统。此前 reference_video 作为 generation_mode 取值却整体更换剧本骨架（video_units 取代 segments/scenes，content_mode 不参与结构选择），两个维度并不真正正交；若广告模式也以「换骨架」方式落地，口播文案、字幕导出、费用预估、状态计算将在两种结构里各活一份。决定：`ad` 作为 content_mode 第三值落地，且 ad 的剧本骨架唯一、不随生成路径更换。

## 决定

- **`ad` 为 content_mode 第三值**：复用全部按 content_mode 分派的机制（profile 变体 `CLAUDE.ad.md`、SCRIPT_SHAPES、创建后不可变约束、StatusCalculator 分派）。
- **剧本骨架唯一**：ad 剧本为平铺 `shots[]`（`shot_id`，E1S{n}），每镜头携带 `section` 标签（带货框架 hook→…→cta 为镜头属性而非嵌套结构）与一等口播文案 `voiceover_text`。两条生成路径消费同一份剧本：storyboard 路径逐镜头出图出视频；reference_video 路径把镜头**派生分组**为 video_unit（轻量索引仅引用 shot_id 与参考集，不复制内容），ad 镜头与 R2V Shot 一一对应。generation_mode 在 ad 下成为真正正交的「视频来源」维度。
- **ad 仅开放 storyboard 与 reference_video**：grid 不开放——宫格单格分辨率与产品高保真目标冲突，其画风一致性价值在 ad 由产品/风格参考承载。
- **恒单集承载**：ad 项目 episodes 恒为 `[{episode: 1, …}]`，剧本即 `scripts/episode_1.json`；按集机械（状态/归档/版本/费用/导出）零结构改动，前端对 ad 隐藏集语义。未来「一产品多变体」以每集=一个变体扩展。
- **镜头时长约束按 generation_mode 动态注入**：storyboard 路径按 supported_durations 硬枚举（模型能力约束）；reference 路径 1–15s 自由整数（短切节奏赖此成立）。骨架统一，值约束随路径。

## 为何不沿用「换骨架」语义、也不先升格 reference_video

口播文案必须跨路径单源（字幕导出与后续 TTS 的输入），双骨架使其在两种结构中重复存在并迫使下游全面双分支。先把 reference_video 升格为顶层类型再落 ad，会让顶层枚举混入「内容语义」（narration/drama/ad）与「生成骨架」（reference_video）两种性质——这正是 generation_mode 此前被批评的维度混淆上移一层，且让 ad 上线被一次大重构阻塞。ad 的「不吞骨架」形态反过来为存量问题提供了改造范本：narration/drama 下的 reference_video 可参照此形态回归纯 generation_mode，升格方案需据此重评。

## Consequences

- VALID_CONTENT_MODES、SCRIPT_SHAPES、profile manifest、数据校验器、创建向导随第三值扩展；ad 专属字段（`target_duration`、`brief`、`products` bucket）见提案与 ADR 0034。
- 分集账本重设计需把 ad 视为恒单条账本/豁免拆分规划，不得对 content_mode 做二值假设。
- 派生 video_unit 索引持久于剧本 JSON，shots 为内容唯一真相；重生成单个 unit 时分组可复现。
