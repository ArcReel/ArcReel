---
status: accepted
---

# 剧本源（source_kind=screenplay）：提取优先复用全链路，逐字仅锚可听内容

drama 模式预期源文件是小说，由三段 LLM（plan_episodes 语义切分 / step1 改编为场景表 / step2 转写为 JSON）从散文**创作**出剧本。但有用户直接上传成品剧本——自带分集、场次、台词、画外音、人物，现有「改编式」链路会把它**二次改写**：plan_episodes 重切作者的分集、step1 改编台词、画外音整段丢失。决定：新增项目级 `source_kind`（`novel`/`screenplay`）作为与 content_mode/generation_mode 正交的第三轴；`screenplay` 下整条链路从「创作」翻为「提取优先」，且逐字保真只锚「可听见的内容」。

## 决定

- **`source_kind` 第三轴**：project.json 顶层字段，`novel`（默认，现状）/ `screenplay`，**创建时确定、之后不可变**（与 content_mode 同性质）。表达「源文件性质」，与「内容类型」（content_mode）、「视频来源」（generation_mode）正交。
- **提取优先铺在现有每个阶段，不另造剧本解析器**：analyze-assets / plan_episodes / normalize-drama-script 各自切到「提取优先」——LLM 先用作者已写的（任意形态、不写死标记正则），缺了才生成。贴合现有「按集惰性消费」架构（脚本可达 10 万字），复用现成 subagent/skill，鲁棒性由 LLM 在每阶段就地判断「有则用、无则生成」，各阶段独立降级（有人物表无分集标记 → 人物逐字提取 + 分集语义规划）。
- **分集永不机械切**：screenplay 下 plan_episodes 仍是语义规划——剧本自带分集（任意形态）则照用边界/标题/钩子/大纲，没有则按完整剧情弧选切分点。切分点至关重要，机械按长度切会切碎剧情弧。
- **逐字保真只锚「可听见的内容」**：严格不改写/不丢/不润色的仅限**角色台词文字** + **画外音文字**两类。排版/标签、运镜与舞台提示、视觉描述、泛指群演由 LLM 裁量转写或剥离——硬逐字会把舞台提示、群演、排版符号强行灌进结构化字段。
- **台词复用 `video_prompt.dialogue`，画外音新增一等字段 `DramaScene.voiceover: list[str]`**：台词本就该进 video_prompt（也是视频/字幕输入），描述从「仅当原文有引号对话时填写」翻为「逐字照搬」；画外音无 speaker、塞进 dialogue 语义错位，且它是日后接 TTS 的锚，与说书 `novel_text` 同位，故独立成字段。
- **泛指 speaker 不进资产**：`老人甲`/`年轻人乙` 这类路人不注册为 character 资产、不污染 characters_in_scene；`dialogue.speaker ∈ characters_in_scene` 校验随之放松。
- **人工审阅复用现有闸**：提取出的骨架（分集 + 人物）经 `/manga-workflow` 既有的每阶段确认 + plan/replan 批级审阅兜住 LLM 在野格式上的误判，零新增机制。
- **保真先锁文本层**：本期只保证台词/画外音逐字进 JSON、不丢不改写；「画外音/台词配音」是 drama 从 0 到 1 的 TTS 大功能，单列后续议题，不捆进本次。

## 为何不写专用剧本解析器、为何台词不另立逐字字段

一把大解析器要把 10 万字塞进单次调用并把全部工作前置到上传时，与「按集惰性消费」架构对着干，且对千奇百怪的格式写死结构假设会脆；「提取优先铺在现有阶段」让每阶段在自己的切片上用 LLM 语义识别，缺哪补哪自动成立。台词不另立逐字字段是因为 `video_prompt.dialogue` 本就是台词的归宿（视频生成 + 字幕导出的输入），再加一份逐字字段会制造双源；画外音例外——它无 speaker 且是 TTS 锚，沿用说书 `novel_text` 的定位独立成字段，而非塞进 dialogue。

## Consequences

- 数据校验器（`lib/data_validator.py`）：放松 speaker ∈ characters_in_scene、`DramaScene` 新增 `voiceover` 字段须纳入 `extra="forbid"` + 「不更坏」守卫。
- prompt builders（`lib/prompt_builders_script.py`）：plan_episodes / normalize / drama 三处加 screenplay 分支（创作→提取）。
- 创建向导 + 前端：暴露 source_kind 选择（参考产品「上传剧本 / AI 生剧本」），创建即定。
- 「画外音/台词配音」（drama-TTS）为独立后续议题，不在本决策范围。
