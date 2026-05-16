# 生成模式参考

ArcReel 把"做什么内容"和"怎么生成视频"拆成两条独立维度（issue #542）。`content_mode` 严格表达**内容类型**（narration / drama），`generation_mode` 表达**视频来源 / 生成路径**（storyboard / grid / reference_video）。组合上可枚举如下；参考生视频路径下内容类型仅作画面比例 / 默认时长等次级决策。

## 模式矩阵

| generation_mode | content_mode | 数据主结构 | 预处理 subagent | 脚本 schema | 视觉参考来源 |
|---|---|---|---|---|---|
| `storyboard` | `narration` | `segments[]` | split-narration-segments | NarrationEpisodeScript | 每片段一张分镜图作起始帧 |
| `storyboard` | `drama` | `scenes[]` | normalize-drama-script | DramaEpisodeScript | 每场景一张分镜图作起始帧 |
| `grid` | `narration` | `segments[]` + 宫格分组 | split-narration-segments | NarrationEpisodeScript | 宫格图切块 |
| `grid` | `drama` | `scenes[]` + 宫格分组 | normalize-drama-script | DramaEpisodeScript | 宫格图切块 |
| `reference_video` | `narration` / `drama` | `video_units[]` | split-reference-video-units | ReferenceVideoScript | 角色 / 场景 / 道具 sheet 图直接作为 `reference_images` |

> `effective_mode(project, episode) = episode.generation_mode or project.generation_mode or "storyboard"`。缺省回退到图生视频（storyboard）。

## 阶段映射

```
Step 3 预处理（按 effective_mode(project, episode) 分派）
  generation_mode = reference_video       → dispatch split-reference-video-units
  generation_mode ∈ {storyboard, grid}：
    content_mode = narration               → dispatch split-narration-segments
    content_mode = drama                   → dispatch normalize-drama-script

Step 4 JSON 剧本
  → dispatch create-episode-script（内部按 generation_mode 选 schema）

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
```

## 视频规格

- **分辨率**：图片 1K，视频 1080p
- **单片段时长**（storyboard / grid）：项目 `default_duration`（项目创建时按 content_mode 写入 project.json）
- **单 unit 时长**（reference_video）：所有 shot 总和；**目标贴近当前视频模型的 `max_duration`**，单 shot 取值必须在模型 `supported_durations` 列表中。具体数值由 subagent 在执行时通过 `mcp__arcreel__get_video_capabilities` 工具查得，**不在本文档固化**
- **拼接**：全部模式用 ffmpeg concat；Veo extend 仅用于**单片段延长**，不串联不同镜头
- **BGM**：视频 prompt 末尾统一追加"禁止出现：BGM、文字字幕、水印"

## Prompt 语言

- 图片/视频生成 prompt 使用**中文**
- 采用叙事式描述，不使用关键词罗列
- reference_video 模式额外规则：用 `@角色/@场景/@道具` 引用资产；**禁止**描写外貌、服装、场景细节（由参考图提供）

## 目录差异

```
projects/{name}/
├── storyboards/          # storyboard / grid 模式（分镜图）
├── grids/                # grid 模式（宫格图）
├── reference_videos/     # reference_video 模式视频输出
└── videos/               # storyboard / grid 模式视频输出
```

> 参考 `docs/google-genai-docs/nano-banana.md` 第 365 行起的 Prompting guide and strategies。
