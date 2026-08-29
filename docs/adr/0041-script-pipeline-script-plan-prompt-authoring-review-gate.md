---
status: accepted
---

# 剧本两段式切成 script_plan 内容 / prompt_authoring 视觉，加 script_plan→prompt_authoring 阻塞式审核 gate

drama / narration 剧本走两段式：script_plan（normalize）把源文整理为 markdown 场景 / 片段表，口播内容（台词 / 画外音 / `novel_text`）嵌在自由文本「场景描述」列，prompt_authoring（generate-script）再从该自由文本重新解析出结构化字段。逐字 / 原文内容因此穿过「结构→自由文本→结构」两道 LLM 关口：screenplay 面临保真丢失、novel 出现创作漂移、narration `novel_text` 实测偶有扩写（>10% 仅 warn）。且 script_plan 完成、prompt_authoring 未跑之间，web 端对中间态完全无感知，用户无法审核或修改。决定把两段式职责切干净——**script_plan = 内容**（场景 / 片段边界、characters/scenes/props、drama 的 `utterances` + `source_text` / narration 的结构化 `novel_text`），**prompt_authoring = 视觉**（image_prompt / video_prompt），prompt_authoring 对 script_plan 已定的口播 / 原文内容**透传、不再重识别**；自由文本列今后只承载视觉改编内容（丢失可容忍）。并在 script_plan→prompt_authoring 之间引入**阻塞式 web 审核 gate**：script_plan 产出的结构化中间态在 web 可见、可手动 / agent 编辑，用户显式确认后才跑昂贵的 prompt_authoring 视觉生成。drama、narration 与 reference_video 共用此机制（narration 不引入 `utterances`，其口播内容仍是 `novel_text`；reference_video 生成路径的 script_plan 为 video_unit 拆分——unit → shots 叙事文本 + 时长 + references 列表，同为结构化 JSON 中间态，prompt_authoring 以其为唯一基底生成 ReferenceVideoScript）。三者的 script_plan 中间态在 web 均按结构渲染、可编辑，确认后才放行 prompt_authoring。

## Considered Options

- **仅对 screenplay 让 script_plan 出结构、novel 维持 prompt_authoring 创作**：改动小，但 script_plan 输出按源不对称、prompt_authoring 仍保留按 source_kind 重识别分支，且 novel / narration 的漂移不修。取全量版让 prompt_authoring 对两源统一、把双重转写从根消除。
- **流水线不动，靠场景级 `source_text` 锚事后检测失真**：检测 ≠ 预防，对「逐字保真」目标预防优先；且不解决 web 无感知。
- **gate 旁观式（中间态可见但 prompt_authoring 自动往下走）**：用户仍可能错过审核窗口；既然目标是「能审核及修改」，阻塞到确认才让错误内容在进入昂贵视觉生成前被拦下。

## Consequences

- script_plan 工具契约变更（`normalize_drama_script` 等从「只出 markdown」变为「出结构」）；供人工审阅的中间产物仍在，但由结构渲染、而非自由文本原稿。
- script_plan 产出一律由服务端工具（normalize / split）经文本管道生成并落盘（模型来源按 `docs/adr/0051` 的档位解析），subagent 仅编排调用，不在自身上下文里生成内容。
- reference_video 的 prompt_authoring prompt（`lib/prompt_builders_reference.py`）以结构化 unit 数据为基底组装，不解析自由文本；web 端 script_plan 预览与编辑按结构渲染。
- prompt_authoring（generate-script）prompt / 流程以 script_plan 确认的结构化数据为唯一基底——完整保留 script_plan 已定的场景 / 片段边界与 `characters_in_scene` / `scenes` / `props`、`utterances` / `source_text` / `novel_text` 等非视觉字段，仅生成 / 覆盖视觉层（`image_prompt` / `video_prompt`）；移除按 source_kind 重新提取口播的分支。
- prompt_authoring 对已存在的剧本按**条目**增量：脚本规划的每个条目取一次内容指纹（`lib/script_plan_entries.py`，与产物清单同一套规范 JSON + sha256 构造器，登记与比对共用），剧本的每个条目持久化它消费的那个值（`script_plan_entry_revision`，对 LLM 隐藏、不进任何 PATCH 白名单）。重跑 prompt_authoring 只把内容失配与新增的条目送进 prompt 与 LLM 输出，未变条目连同 `image_prompt` / `video_prompt` / `note` / `end_frame_image` / `transition_to_next` / `generated_assets` / `needs_replan` 原样沿用，条目顺序与集合跟随脚本规划（删掉的条目移出剧本，产物文件不删、由产物清单按 `docs/adr/0062` 自然判 stale / missing）。改一条 `source_text` 因此只重写那一条，用户精修过的提示词与已付费产物不被整集重写覆盖。`generate_episode_script` 的 `scope` / `entry_ids` 可显式要求整集重写或点名条目；范围之外的判定不变——内容确认门禁对增量路径同样阻塞，脚本规划的整集指纹 `script_plan_revision` 保留作兼容与快速判断，存量剧本（条目无指纹）按它回退判定、不因升级被误报失效。
- prompt_authoring 透传以工程手段保真、不靠 prompt 自觉：prompt_authoring 的 LLM 输出 schema 只含 `scene_id`（对齐锚）+ 视觉字段（`image_prompt` / `video_prompt`），后端按 `scene_id`（非列表顺序）把视觉层合并回 script_plan 已确认结构、并校验 `scene_id` 唯一与全覆盖；`utterances` / `source_text` 等非视觉字段不进 LLM 输出——从工程上杜绝非视觉字段经 Structured Outputs 漂移，而非靠 prompt 自觉。reference_video 路径同一原则、另一种落法：其 prompt_authoring 输出是与 script_plan 等长同序的书写层正文列表，没有 id 可漂移，对齐靠「unit 数一致 + 逐位配对」，台词记号则以逐字 diff 拦改写（书写层正文的语法真相源是 `lib/reference_video/writing_syntax.py`）。
- 新增 script_plan→prompt_authoring 之间的 web 审核状态与确认动作（service / router + 前端）；prompt_authoring 由用户确认触发。
- drama 的 `utterances` / `source_text` 数据模型见 ADR 0040；novel 画外音克制放开同见 0040（其内容在 script_plan 产出、prompt_authoring 透传）。
- 两段的名字是**脚本规划（`script_plan`）**与**提示词编写（`prompt_authoring`）**（术语见 `CONTEXT.md`）；落盘文件名、`project.json` 分集字段、剧本 metadata 字段与产物 kind 一律用这两个标识，存量项目由 v9→v10 schema 迁移改名。唯一的保留项是产物指纹的两个 token——`lib.artifact_provenance` 的 `SCRIPT_PLAN_BASIS_KIND`（值 `structured-content/step1`）与 `SCRIPT_PLAN_BASIS_INPUT_KEY`（值 `step1_content`）：它们参与 `basis_digest` 计算，而清单只落 digest、不落输入，改值等于把全部存量脚本规划与剧本产物判成 stale。值是持久化格式，不随术语改名。
