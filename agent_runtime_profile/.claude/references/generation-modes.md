# 生成模式参考

`project.json` 的 `content_mode` 决定创作类型，`generation_mode` 决定生成模式。项目创建后两项都不能改，单集也不能覆盖。

旁白/解说和剧情演绎项目采用分镜图生视频时，`grid_storyboard` 只决定分镜图用单图还是宫格图。用户可在设置页切换。

## 剧本结构

| generation_mode | content_mode | 数据主结构 | 脚本规划子智能体 | script_plan 中间文件 | 脚本 schema | 视觉参考来源 |
|---|---|---|---|---|---|---|
| `storyboard` | `narration` | `segments[]` | split-narration-segments | `script_plan_segments.json` | NarrationEpisodeScript | 每分镜一张分镜图作起始帧（`grid_storyboard=true` 时为宫格图切块） |
| `storyboard` | `drama` | `scenes[]` | normalize-drama-script | `script_plan_normalized_script.json` | DramaNormalizedScript（script_plan）→ DramaVisualScript（prompt_authoring）→ DramaEpisodeScript（合并） | 每个分镜一张分镜图作起始帧（`grid_storyboard=true` 时为宫格图切块） |
| `reference_video` | `narration` / `drama` | `video_units[]` | split-reference-video-units | `script_plan_reference_units.json` | ReferenceVideoScript | 角色 / 场景 / 道具 sheet 图直接作为 `reference_images` |
| `storyboard` | `ad` | `shots[]` | 无 | 无 | AdEpisodeScript | 每个分镜一张分镜图作起始帧 |
| `reference_video` | `ad` | `video_units[]` | 无 | 无 | ReferenceVideoScript | 正文引用的商品与其他资产图 |

> drama 走两段式：script_plan（normalize-drama-script）产出**结构化内容** `script_plan_normalized_script.json`（分镜边界 / 出场资产 / 逐字口播 utterances / 原文锚 source_text / 视觉改编描述）；prompt_authoring（create-episode-script）LLM 只出视觉层 `DramaVisualScript`（scene_id + image_prompt + video_prompt），后端按 scene_id 写回正式脚本 `DramaEpisodeScript`。内容确认时脚本规划整集转为正式脚本，之后内容修改在正式脚本上经 `patch_episode_script` 进行。
>
> script_plan 中间文件统一位于 `drafts/episode_{N}/`。状态检测与内容确认**只认当前项目 generation_mode 对应的那一个文件**：目录中出现其他模式的 `script_plan_*` 文件属历史残留，既不作为脚本规划已完成的依据，也不能当作内容确认的代替输入。drama 旧项目残留的 `script_plan_normalized_script.md`（结构化前自由文本稿）不算有效 script_plan，须重跑 normalize 产出 `.json`。

## 步骤适用性由计划表达

步骤是否适用、顺序、当前进度与脚本规划子智能体，一律读 `mcp__arcreel__get_workflow_plan` 的 `steps[]` 与
`next_action`（`prepare_script_plan` 的 `next_action.args.preprocessor` 就是权威的脚本规划子智能体名）。
读法见 [workflow-plan.md](workflow-plan.md)。

- 参考生视频跳过分镜图。两种生成模式都可用同一个工具为旁白条目补齐配音。
- 视频入队按项目 `generation_mode` 定生成模式，剧本骨架只作校验；失配（如 storyboard 项目里残留
  `video_units[]` 旧剧本）直接拒绝入队，正解是按项目当前生成模式重跑脚本规划与剧本生成，而非指望旧剧本被执行。
- 正式脚本不跟随脚本规划变化：内容修改在正式脚本上经 `patch_episode_script` 进行；只有整集重做才重跑脚本规划，重跑后须重新确认，确认会整份覆盖现有正式脚本（先经覆盖确认）。

## 视频规格

- **分辨率**：取生效的模型设置与能力。
- **单分镜时长**（含宫格）：从 `get_video_capabilities.duration_constraints.allowed` 选档，优先用可用的 `default_duration`。若 `duration_endpoint_fixed` 为 true，档位可以为空，由服务端决定规划时长。新分镜按无尾帧档位规划。挂尾帧后按 `end_frame_unit_durations.with_end_frame` 核对，越界时按用户意图改时长或移除尾帧。
- **单集目标时长**（非 ad 项目）：项目 `episode_target_duration` 非 null 时，脚本规划据它决定本集拆多少个分镜 / 视频单元；未显式设 `episode_target_units` 时，分集规划也按它经口播语速折算出每集塞多少原文。两处均为软目标：内容不足时宁可少拆，内容确实需要时可以超出，超出仅提示而不阻断。
- **单个视频单元时长**（旁白/解说、剧情演绎的参考生视频）：视频单元是一次生成调用的单元，一个视频单元一个时长——取值必须在该视频单元**引用状态对应**的生效档位内（`get_video_capabilities` 返回的 `reference_unit_durations.with_references` / `.without_references`；部分型号对带参考图的生成另有时长限制）；内容装不下所选档位时重拆视频单元，不违约时长。具体数值由子智能体在执行时通过 `mcp__arcreel__get_video_capabilities` 工具查得，**不在本文档固化**。已有正式视频单元实际落的桶与档位以同一返回里 `reference_unit_durations.units[unit_id]` 的服务端判定为准：它按此刻可用的参考图定桶，登记了资产却没有资产图的引用不算带图，并在 `problems` / `unavailable_references` 中明确指出。
- **广告/短片时长**：分镜图生视频按生效的分镜时长档位规划。参考生视频用剧本模型允许的正整数，生成预检再投影到供应商申请档位。

## 提示词

- 按会话的「语言规范」写提示词，连贯描述画面、动作和声音。视频提示词写「她穿着红色外套」这类画面描述。「把外套改成红色」这类指令可能被供应商当成编辑请求，延长指令也有同样的问题。
- 参考生视频用 `@[名称]` 指认角色、场景、道具或商品。外貌、服装和场景细节沿用参考图。
- 视频提示词无需重复生成端的避免项。配乐在剪辑阶段处理。

## 目录差异

> 下面的目录树仅说明项目结构，session cwd 已在 `projects/{name}/`，目录树里的相对路径用于定位内容。Read、Glob、Grep 等文件工具传从项目根拼出的绝对路径。

```text
projects/{name}/          # ← session cwd 已在此
├── storyboards/          # 分镜图生视频分镜图（grid_storyboard=true 时存宫格切割出的起始分镜图）
├── grids/                # 分镜图生视频且 grid_storyboard=true（宫格大图）
├── reference_videos/     # 参考生视频产物
├── videos/               # 分镜图生视频产物
└── audio/                # 旁白音频（仅旁白/解说，首次生成时创建）
```
