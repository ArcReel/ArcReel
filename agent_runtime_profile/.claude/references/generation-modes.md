# 生成模式参考

ArcReel 把"做什么内容"和"怎么生成视频"拆成两条独立维度。`content_mode` 严格表达**内容类型**（narration / drama），`generation_mode` 表达**视频来源 / 生成路径**（storyboard / reference_video）。二者均由 `project.json` 顶层字段唯一决定，项目创建后不可更改，不存在集级覆盖。组合上可枚举如下；参考生视频路径下内容类型仅作画面比例 / 默认时长等次级决策。

宫格不是独立生成模式：`grid_storyboard` 是仅在 `generation_mode="storyboard"` 下生效的独立布尔开关（由用户在设置页开关，智能体无对应写入权限），决定分镜图步骤走单图还是宫格图，不影响其余步骤的分派。

## 模式矩阵

| generation_mode | content_mode | 数据主结构 | 内容整理子任务 | step1 中间文件 | 脚本 schema | 视觉参考来源 |
|---|---|---|---|---|---|---|
| `storyboard` | `narration` | `segments[]` | split-narration-segments | `step1_segments.json` | NarrationEpisodeScript | 每片段一张分镜图作起始帧（`grid_storyboard=true` 时为宫格图切块） |
| `storyboard` | `drama` | `scenes[]` | normalize-drama-script | `step1_normalized_script.json` | DramaNormalizedScript（step1）→ DramaVisualScript（step2）→ DramaEpisodeScript（合并） | 每场景一张分镜图作起始帧（`grid_storyboard=true` 时为宫格图切块） |
| `reference_video` | `narration` / `drama` | `video_units[]` | split-reference-video-units | `step1_reference_units.json` | ReferenceVideoScript | 角色 / 场景 / 道具 sheet 图直接作为 `reference_images` |

> drama 走两段式（见 ADR 0041）：step1（normalize-drama-script）产出**结构化内容** `step1_normalized_script.json`（场景边界 / 出场资产 / 逐字口播 utterances / 原文锚 source_text / 视觉改编描述）；step2（create-episode-script）LLM 只出视觉层 `DramaVisualScript`（scene_id + image_prompt + video_prompt），后端按 scene_id 合并回 step1 内容得 `DramaEpisodeScript`、透传非视觉字段。
>
> step1 中间文件统一位于 `drafts/episode_{N}/`。状态检测与剧本生成**只认当前项目 generation_mode 对应的那一个文件**：目录中出现其他模式的 `step1_*` 文件属历史残留，既不作为内容整理已完成的依据，也不能当作剧本生成的代替输入。drama 旧项目残留的 `step1_normalized_script.md`（结构化前自由文本稿）不算有效 step1，须重跑 normalize 产出 `.json`。

## 步骤适用性由计划表达

**本文档不再复述一张按创作类型或生成模式展开的步骤表。** 哪些步骤适用、顺序如何、当前停在哪一步、
内容整理该 dispatch 哪个子任务，一律读 `mcp__arcreel__get_workflow_plan` 的 `steps[]` 与
`next_action`（`prepare_step1` 的 `next_action.args.preprocessor` 就是权威的内容整理子任务名）。
读法见 [workflow-plan.md](workflow-plan.md)。

上表只解释**数据结构与 schema 的差异**：同一步骤在不同组合下操作的是哪种主结构、哪个中间文件、
哪份 schema、视觉参考从哪来。两条生成模式的差别落在这里，不落在「跳过哪一步」上。

几条不由计划表达、需要在这里说清的事实：

- `reference_video` **只跳过分镜图**这一步。它不跳过 audio：旁白交付选择在两种生成模式下都要逐次做，
  只是参考生视频没有按段批量 TTS 的入口（无 `segments[]`）。
- 视频入队按项目 `generation_mode` 定生成模式，剧本骨架只作校验；失配（如 storyboard 项目里残留
  `video_units[]` 旧剧本）直接拒绝入队，正解是按项目当前生成模式重跑内容整理与剧本生成，而非指望旧剧本被执行。
- 内容整理中间文件被修改 / 重拆后必须重新生成剧本 JSON——剧本不会自动跟随中间文件更新。

## 视频规格

- **分辨率**：图片 1K，视频 1080p
- **单片段时长**（storyboard，含 grid_storyboard）：取值必须在模型 `supported_durations` 内；项目 `default_duration` 非 null 时作默认值（项目创建时按 content_mode 写入 project.json），为 null 时由内容整理按内容节奏自行取值
- **单 unit 时长**（reference_video）：unit 是一次生成调用的单元，一个 unit 一个时长——取值必须在该 unit **引用状态对应**的生效档位内（`get_video_capabilities` 返回的 `reference_unit_durations.with_references` / `.without_references`；部分型号对带参考图的生成另有时长限制）；内容装不下所选档位时重拆 unit，不违约时长。具体数值由子任务在执行时通过 `mcp__arcreel__get_video_capabilities` 工具查得，**不在本文档固化**
- **拼接**：全部模式用 ffmpeg concat；Veo extend 仅用于**单片段延长**，不串联不同镜头
- **BGM**：生成端已在视频 prompt 末尾自动追加"禁止出现：BGM、文字字幕、水印"，无需手动追加，prompt 里也不要描述 BGM / 配乐

## Prompt 语言

- 图片/视频生成 prompt 使用**中文**
- 采用叙事式描述，不使用关键词罗列
- reference_video 模式额外规则：用 `@[角色]/@[场景]/@[道具]` 引用资产；**禁止**描写外貌、服装、场景细节（由参考图提供）

## 目录差异

> 下面的目录树仅说明项目结构，session cwd 已在 `projects/{name}/`，**调用工具时使用相对 cwd 的路径**（如 `videos/`、`reference_videos/`），不可带 `projects/{name}/` 前缀。

```text
projects/{name}/          # ← session cwd 已在此
├── storyboards/          # storyboard 模式分镜图（grid_storyboard=true 时存宫格切割出的起始分镜图）
├── grids/                # storyboard 模式且 grid_storyboard=true（宫格大图）
├── reference_videos/     # reference_video 模式视频输出
├── videos/               # storyboard 模式视频输出
└── audio/                # 旁白音频（仅旁白/解说，首次生成时创建）
```

> 参考 [Gemini 图像生成官方指南](https://ai.google.dev/gemini-api/docs/image-generation)的 prompting strategies。
