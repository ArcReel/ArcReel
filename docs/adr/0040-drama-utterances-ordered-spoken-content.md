---
status: accepted
---

# drama 口播内容统一为有序 utterances，取代 dialogue/voiceover 双字段

drama 的口播内容（角色台词 + 画外音）统一为一条**场景级、有序的判别式联合列表** `scene.utterances: list[Utterance]`，每条 `Utterance{kind: "dialogue"|"voiceover", speaker: str|None, text: str}`：插入顺序即时序（表达台词与画外音在一个场景里的先后），`kind` 决定下游路由（dialogue 进视频 YAML 交供应商生成口型音轨、voiceover 不作为视频提示词、留给字幕与日后 TTS；判别标准为是否归属具体说话角色，voiceover 仅承载无说话人的旁白解说），口播内容单一真相落在 utterances（`dialogue` 不嵌在 `video_prompt` 内）。这条统一序列是下游三处消费的共同地基——成片字幕从它派生、场景说话量对时长的约束按它估算、step1 结构化产出以它为目标；若口播分散在多个无序字段，字幕无源、跨类型先后丢失、对话量与时长脱钩、novel 叙述内容只能塞进画面而失真。配套：novel 源画外音克制放开（prompt 给语境创作判断、不给规则或类别白名单、不定性为兜底；screenplay 逐字提取）；场景级 `source_text`（逐字原文摘录，类比说书 `novel_text`，但纯作追溯锚、不被朗读）支撑人工审阅、单场景重生成与失真定位；成片字幕从 utterances 派生 `subtitle_spans`（按语速估时长、顺次摆放、允许留白、不撑满场景）落地剪映；场景说话量对时长取**单向约束**（台词只把时长顶上去、永不压下来）：保存期估算说话时长超 duration 仅 warn 不阻塞，生成期 step1 时长指引另引导模型为每场选不低于口播估算时长的档位，duration 仍由画面驱动、不机械改写。

## Considered Options

- **保留 0036 双字段、仅加跨类型排序（序号 / 顺序引用）**：改动小、视频 YAML 不动，但口播内容仍两处分散、LLM 要维护两套结构一致性，字幕 / 时长 / 结构化各自还得拼合两源；排序是 bolt-on 而非单一真相。
- **用 speaker 有无隐式判别（speaker=None 即画外音）**：省一个字段，但「null 当语义载体」隐晦、易与空串混淆，LLM 结构化输出缺显式 enum 不稳。取显式 `kind`，与 `ReferenceResource.type` / `AdUnitReference.type` 既有判别式风格一致。
- **novel 维持画外音恒空**：保 drama「演而非说」的纯度，但单个 drama 项目里夹着演不出的叙述段落只能塞画面而失真；改为克制放开、由 LLM 语境判断更贴实际。

## Consequences

- `lib/script_models.py`：`DramaScene` 的口播内容为 `utterances`（`Utterance{kind, speaker, text}`，`kind ⇄ speaker` 校验：dialogue⇒speaker 非空、voiceover⇒speaker 为 None），并有场景级 `source_text`。泛指群演 speaker（`老人甲`）照填原文称呼、不进 characters_in_scene、无机械校验。`VideoPrompt.dialogue` 是否保留按 narration / ad 是否消费该字段定：均不消费则共享模型不再带 dialogue、有消费则 drama 用无-dialogue 变体。
- 存量数据走「读时迁移」（`model_validator(before)`，仿 `LEGACY_DROPPED_FIELDS`）：老脚本 `video_prompt.dialogue` + `voiceover` 合成 `utterances` 并剥离旧字段（`extra="forbid"` + 「不更坏」守卫才不报错）；旧数据无交错信息、混合场景本就罕见，合并顺序按确定性 best-effort（dialogue 段在前、voiceover 段在后），不假装还原。
- `lib/data_validator.py`：校验 utterances 结构与 `kind ⇄ speaker`、`source_text`；新增「估算说话时长 > duration × 容差」的 **warning**（沿用 ad 总时长漂移那条「只 warn 不阻塞」）。语速常量单一真相源、可调（可随 `target_language`），与字幕共用、不写死。
- 说话量对时长为**方向对称的单向约束**、两处消费同一语速真相源（`lib/speech_rate.py`，按 `source_language` 取）：保存期取**单向上界 warning**（上条，`lib/data_validator.py` 估算超容差仅 warn 不阻塞、不改写）；生成期取**单向下界软指引**（`lib/prompt_builders_script.py` 的 drama step1 时长约束 prompt 引导模型为每场选**不低于**该场 utterances 估算口播时长的档位；口播超最长档时取最长档，仍由保存期 warning 兜底暴露）。二者只把时长「顶上去」、永不「压下来」：画面 / 留白可把 duration 撑过下界，无机械逻辑改写模型已选的 duration，「画面驱动 + 留白合法」立场不变。
- `server/services/jianying_draft_service.py`：drama 注册为字幕模式，从 utterances 派生 `subtitle_spans`（复用既有 span 渲染与单字幕兜底）；`_SUBTITLE_TEXT_FIELDS` 的「单字段」模型对 drama 不适用，改用 utterance→spans builder（类比 `_collect_ad_reference_unit_clips`）。
- step1 / step2 流水线如何产出 utterances（结构化 step1、透传 step2、web 审核 gate）见 ADR 0041。
