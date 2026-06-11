# 生成模式参考

ArcReel 把"做什么内容"和"怎么生成视频"拆成两条独立维度。`content_mode` 严格表达**内容类型**（narration / drama），`generation_mode` 表达**视频来源 / 生成路径**（storyboard / grid / reference_video）。组合上可枚举如下；参考生视频路径下内容类型仅作画面比例 / 默认时长等次级决策。

## 模式矩阵

| generation_mode | content_mode | 数据主结构 | 预处理 subagent | step1 中间文件 | 脚本 schema | 视觉参考来源 |
|---|---|---|---|---|---|---|
| `storyboard` | `narration` | `segments[]` | split-narration-segments | `step1_segments.md` | NarrationEpisodeScript | 每片段一张分镜图作起始帧 |
| `storyboard` | `drama` | `scenes[]` | normalize-drama-script | `step1_normalized_script.md` | DramaEpisodeScript | 每场景一张分镜图作起始帧 |
| `grid` | `narration` | `segments[]` + 宫格分组 | split-narration-segments | `step1_segments.md` | NarrationEpisodeScript | 宫格图切块 |
| `grid` | `drama` | `scenes[]` + 宫格分组 | normalize-drama-script | `step1_normalized_script.md` | DramaEpisodeScript | 宫格图切块 |
| `reference_video` | `narration` / `drama` | `video_units[]` | split-reference-video-units | `step1_reference_units.md` | ReferenceVideoScript | 角色 / 场景 / 道具 sheet 图直接作为 `reference_images` |

> `effective_mode(project, episode) = episode.generation_mode or project.generation_mode or "storyboard"`。缺省回退到图生视频（storyboard）。

> step1 中间文件统一位于 `drafts/episode_{N}/`。状态检测与剧本生成**只认当前组合对应的那一个文件**：目录中出现其他模式的 `step1_*` 文件属历史残留，既不作为预处理已完成的依据，也不能当作剧本生成的代替输入。

## 阶段映射

```
Step 3 预处理（按 effective_mode(project, episode) 分派；中间文件统一位于 drafts/episode_{N}/）
  effective_mode = reference_video        → dispatch split-reference-video-units → step1_reference_units.md
  effective_mode ∈ {storyboard, grid}：
    content_mode = narration               → dispatch split-narration-segments   → step1_segments.md
    content_mode = drama                   → dispatch normalize-drama-script     → step1_normalized_script.md

Step 4 JSON 剧本
  → dispatch create-episode-script（内部按 generation_mode 选 schema）
  Step 3 中间文件被修改 / 重拆后必须重新执行本步——剧本 JSON 不会自动跟随中间文件更新

Step 5 资产（characters / scenes / props 三类）
  三种模式共用 `generate-assets` skill（--characters/--scenes/--props）

Step 6 分镜图
  storyboard         → dispatch generate-assets (storyboard)
  grid               → dispatch generate-assets (grid)
  reference_video    → 跳过

Step 7 视频
  storyboard / grid  → dispatch generate-assets (video)
  reference_video    → dispatch generate-assets (video)
                       mcp__arcreel__generate_video_episode 检测 video_units 后路由到 task_type="reference_video"

Step 8 旁白配音（仅 narration 内容模式）
  storyboard / grid  → dispatch generate-assets (narration_audio)
                       mcp__arcreel__generate_narration_audio 按段以 novel_text 合成
  reference_video    → 跳过（无 segments）
```

## 视频规格

- **分辨率**：图片 1K，视频 1080p
- **单片段时长**（storyboard / grid）：取值必须在模型 `supported_durations` 内；项目 `default_duration` 非 null 时作默认值（项目创建时按 content_mode 写入 project.json），为 null 时由预处理按内容节奏自行取值
- **单 unit 时长**（reference_video）：所有 shot 总和 ≤ `max_duration` 且**目标贴近该值**，单 shot 取值必须在模型 `supported_durations` 列表中；放不下时重拆 unit，不违约时长。具体数值由 subagent 在执行时通过 `mcp__arcreel__get_video_capabilities` 工具查得，**不在本文档固化**
- **拼接**：全部模式用 ffmpeg concat；Veo extend 仅用于**单片段延长**，不串联不同镜头
- **BGM**：视频 prompt 末尾统一追加"禁止出现：BGM、文字字幕、水印"

## Prompt 语言

- 图片/视频生成 prompt 使用**中文**
- 采用叙事式描述，不使用关键词罗列
- reference_video 模式额外规则：用 `@[角色]/@[场景]/@[道具]` 引用资产；**禁止**描写外貌、服装、场景细节（由参考图提供）

## 目录差异

> 下面的目录树仅说明项目结构，session cwd 已在 `projects/{name}/`，**调用工具时使用相对 cwd 的路径**（如 `videos/`、`reference_videos/`），不可带 `projects/{name}/` 前缀。

```text
projects/{name}/          # ← session cwd 已在此
├── storyboards/          # storyboard / grid 模式（分镜图）
├── grids/                # grid 模式（宫格图）
├── reference_videos/     # reference_video 模式视频输出
├── videos/               # storyboard / grid 模式视频输出
└── audio/                # 旁白音频（仅 narration 内容模式，首次生成时创建）
```

> 参考 `docs/google-genai-docs/nano-banana.md` 第 365 行起的 Prompting guide and strategies。
