---
status: accepted
---

# 剧本源（source_kind=screenplay）：提取优先复用全链路，逐字仅锚可听内容

drama 模式预期源文件是小说，由三段 LLM（plan_episodes 语义切分 / step1 改编为场景表 / step2 转写为 JSON）从散文**创作**出剧本；但有用户直接上传自带分集、场次、台词、画外音、人物的成品剧本，现有「改编式」链路会把它**二次改写**——plan_episodes 重切作者的分集、step1 改编台词、画外音整段丢失。决定新增项目级 `source_kind`（`novel` 默认 / `screenplay`）作为与 content_mode、generation_mode 都正交的第三轴「源文件性质」，**创建时确定、之后不可变**（与 content_mode 同性质）；`screenplay` 下整条 drama 链路从「创作」翻为「提取优先」，且逐字保真只锚「可听见的内容」。

提取优先**不另造剧本解析器**，而是铺在 analyze-assets / plan_episodes / normalize-drama-script 现有每个阶段——LLM 先用作者已写的（任意形态、不写死标记正则），缺了才生成，贴合现有「按集惰性消费」架构（脚本可达 10 万字）、复用现成 subagent/skill，各阶段独立降级（有人物表无分集标记 → 人物逐字提取 + 分集语义规划）。**分集永不机械切**：plan_episodes 仍是语义规划，剧本自带分集（任意形态）则照用其边界/标题/钩子/大纲，没有则按完整剧情弧选切分点——切分点至关重要，机械按长度切会切碎剧情弧。**逐字保真只锚「可听见的内容」**：严格不改写/不丢/不润色的仅限角色台词文字与画外音文字两类，排版/标签、运镜与舞台提示、视觉描述、泛指群演由 LLM 裁量转写或剥离（硬逐字会把舞台提示、群演、排版符号强行灌进结构化字段）。

台词复用 `video_prompt.dialogue`（它本就是视频/字幕输入，描述从「仅当原文有引号对话时填写」翻为「逐字照搬」）；画外音无 speaker、塞进 dialogue 语义错位，故新增一等字段 `DramaScene.voiceover: list[str]`——角色上类比说书 `novel_text`（同为日后接 TTS 的逐字锚），但取 `list[str]` 而非 `str`，因一个场景可含多段有序画外音插入（基数为多），而 `NarrationSegment` 本身即一个旁白单位（基数为一）。泛指 speaker（`老人甲`/`年轻人乙`）不注册为 character 资产、不进 characters_in_scene，其对话照常进 `video_prompt.dialogue` 只是 speaker 不登记——泛指 vs 命名角色由 LLM 在 prompt 层判定（`dialogue.speaker ∈ characters_in_scene` 本就只是 `prompt_builders_script.py` 的 prompt 指令、无机械校验，screenplay 下放松该指令即可）。提取出的骨架（分集 + 人物）经 `/manga-workflow` 既有的每阶段确认 + plan/replan 批级审阅兜住 LLM 在野格式上的误判，零新增机制。本期只锁文本层——台词/画外音逐字进 JSON、不丢不改写；「画外音/台词配音」（drama-TTS）及画外音与台词的**幕内时序交错**（先后顺序）属配音/合成阶段，单列后续议题，本期不建模二者顺序。

## Considered Options

- **一把专用大解析器**：要把 10 万字塞进单次调用、把全部工作前置到上传时，与「按集惰性消费」架构对着干，且对千奇百怪的格式写死结构假设易脆；「提取优先铺在现有阶段」让每阶段在自己的切片上用 LLM 语义识别，缺哪补哪自动成立。
- **台词另立逐字字段**：`video_prompt.dialogue` 本就是台词的归宿（视频生成 + 字幕导出的输入），再加一份逐字字段会制造双源。画外音例外——它无 speaker 且是 TTS 锚，沿用说书 `novel_text` 的定位独立成字段，而非塞进 dialogue。

## Consequences

- 数据校验器（`lib/data_validator.py`）：新增 `source_kind` 顶层字段的枚举校验（仅 `novel`/`screenplay`，拦截 `screen_play` 等非法值）；`DramaScene` 新增 `voiceover` 字段须纳入 `extra="forbid"` + 「不更坏」守卫；speaker 无机械校验可放松——该约束只是 prompt 指令（见下条）。
- prompt builders（`lib/prompt_builders_script.py`）：plan_episodes / normalize / drama 三处加 screenplay 分支（创作→提取）；drama 分支放松「speaker 必须出现在 characters_in_scene」指令以容纳泛指 speaker。
- 创建向导 + 前端：暴露 source_kind 选择（参考产品「上传剧本 / AI 生剧本」），创建即定；前端 `ProjectData` 类型同步新增 `source_kind`。
- 「画外音/台词配音」（drama-TTS）为独立后续议题，不在本决策范围。
