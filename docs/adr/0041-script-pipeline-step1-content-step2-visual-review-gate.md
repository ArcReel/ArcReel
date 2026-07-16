---
status: accepted
---

# 剧本两段式切成 step1 内容 / step2 视觉，加 step1→step2 阻塞式审核 gate

drama / narration 剧本走两段式：step1（normalize）把源文整理为 markdown 场景 / 片段表，口播内容（台词 / 画外音 / `novel_text`）嵌在自由文本「场景描述」列，step2（generate-script）再从该自由文本重新解析出结构化字段。逐字 / 原文内容因此穿过「结构→自由文本→结构」两道 LLM 关口：screenplay 面临保真丢失、novel 出现创作漂移、narration `novel_text` 实测偶有扩写（>10% 仅 warn）。且 step1 完成、step2 未跑之间，web 端对中间态完全无感知，用户无法审核或修改。决定把两段式职责切干净——**step1 = 内容**（场景 / 片段边界、characters/scenes/props、drama 的 `utterances` + `source_text` / narration 的结构化 `novel_text`），**step2 = 视觉**（image_prompt / video_prompt），step2 对 step1 已定的口播 / 原文内容**透传、不再重识别**；自由文本列今后只承载视觉改编内容（丢失可容忍）。并在 step1→step2 之间引入**阻塞式 web 审核 gate**：step1 产出的结构化中间态在 web 可见、可手动 / agent 编辑，用户显式确认后才跑昂贵的 step2 视觉生成。drama 与 narration 共用此机制（narration 不引入 `utterances`，其口播内容仍是 `novel_text`）；reference_video 生成路径的 step1（video_unit 拆分：unit → shots 叙事文本 + 时长 + references 列表）同为结构化 JSON 中间态，step2 以其为唯一基底生成 ReferenceVideoScript，但尚未纳入该阻塞式 gate（自动放行）。

## Considered Options

- **仅对 screenplay 让 step1 出结构、novel 维持 step2 创作**：改动小，但 step1 输出按源不对称、step2 仍保留按 source_kind 重识别分支，且 novel / narration 的漂移不修。取全量版让 step2 对两源统一、把双重转写从根消除。
- **流水线不动，靠场景级 `source_text` 锚事后检测失真**：检测 ≠ 预防，对「逐字保真」目标预防优先；且不解决 web 无感知。
- **gate 旁观式（中间态可见但 step2 自动往下走）**：用户仍可能错过审核窗口；既然目标是「能审核及修改」，阻塞到确认才让错误内容在进入昂贵视觉生成前被拦下。

## Consequences

- step1 工具契约变更（`normalize_drama_script` 等从「只出 markdown」变为「出结构」）；供人工审阅的中间产物仍在，但由结构渲染、而非自由文本原稿。
- step1 产出一律由服务端工具（normalize / split）经文本管道生成并落盘（模型来源按 `docs/adr/0049` 的档位解析），subagent 仅编排调用，不在自身上下文里生成内容。
- reference_video 的 step2 prompt（`lib/prompt_builders_reference.py`）以结构化 unit 数据为基底组装，不解析自由文本；web 端 step1 预览与编辑按结构渲染。
- step2（generate-script）prompt / 流程以 step1 确认的结构化数据为唯一基底——完整保留 step1 已定的场景 / 片段边界与 `characters_in_scene` / `scenes` / `props`、`utterances` / `source_text` / `novel_text` 等非视觉字段，仅生成 / 覆盖视觉层（`image_prompt` / `video_prompt`）；移除按 source_kind 重新提取口播的分支。
- step2 透传以工程手段保真、不靠 prompt 自觉：step2 的 LLM 输出 schema 只含 `scene_id`（对齐锚）+ 视觉字段（`image_prompt` / `video_prompt`），后端按 `scene_id`（非列表顺序）把视觉层合并回 step1 已确认结构、并校验 `scene_id` 唯一与全覆盖；`utterances` / `source_text` 等非视觉字段不进 LLM 输出——从工程上杜绝非视觉字段经 Structured Outputs 漂移，而非靠 prompt 自觉。
- 新增 step1→step2 之间的 web 审核状态与确认动作（service / router + 前端）；step2 由用户确认触发。
- drama 的 `utterances` / `source_text` 数据模型见 ADR 0040；novel 画外音克制放开同见 0040（其内容在 step1 产出、step2 透传）。
