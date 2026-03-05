---
name: generate-video
description: 使用 Veo 3.1 API 为每个场景独立生成视频片段，以分镜图作为起始帧，然后使用 ffmpeg 拼接。使用场景：(1) 用户运行 /generate-video 命令，(2) 剧本中有场景没有 video_clip 路径，(3) 用户想将分镜图转换为视频。
---

# 生成视频

使用 Veo 3.1 API 为每个场景/片段创建视频，以分镜图作为起始帧。

## 内容模式支持

系统支持两种内容模式，视频规格根据模式自动调整：

| 维度 | 说书+画面模式（默认） | 剧集动画模式 |
|------|----------------------|-------------|
| 画面比例 | **9:16 竖屏** | 16:9 横屏 |
| 默认时长 | **4 秒** | 8 秒 |
| 数据结构 | `segments` 数组 | `scenes` 数组 |
| Prompt 构建 | `build_segment_prompt()` | `build_scene_prompt()` |
| 视频 Prompt | 仅角色对话（如有），无旁白 | 对话、旁白、音效 |

> 画面比例通过 API 参数设置，不包含在 prompt 中。
> 时长仅支持 4s / 6s / 8s 选项。

## 生成模式

> 并发说明：默认并发来自环境变量 `VIDEO_MAX_WORKERS`（默认 2），也可用 `--max-workers` 覆盖。

### 1. 标准模式（推荐）

每个场景独立生成视频，然后使用 ffmpeg 拼接：

```bash
python .claude/skills/generate-video/scripts/generate_video.py \
    my_project script.json --episode 1
```

**特点：**
- 每个场景都使用分镜图作为起始帧，保证人物一致性
- 场景之间使用 ffmpeg 拼接
- 支持断点续传
- 可单独重新生成不满意的场景

### 2. 断点续传

如果生成中断，可以从上次检查点继续：

```bash
python .claude/skills/generate-video/scripts/generate_video.py \
    my_project script.json --episode 1 --resume
```

### 3. 单场景模式

生成单个场景的视频（用于测试或重新生成）：

```bash
python .claude/skills/generate-video/scripts/generate_video.py \
    my_project script.json --scene E1S1
```

### 4. 批量独立模式

独立生成所有待处理场景：

```bash
python .claude/skills/generate-video/scripts/generate_video.py \
    my_project script.json --all
```

### 5. 批量自选模式

生成指定的多个场景视频：

```bash
python .claude/skills/generate-video/scripts/generate_video.py \
    my_project script.json --scenes E1S01,E1S05,E1S10
```

结合断点续传：

```bash
python .claude/skills/generate-video/scripts/generate_video.py \
    my_project script.json --scenes E1S01,E1S05,E1S10 --resume
```

**特点：**
- 按逗号分隔指定多个场景 ID
- 支持断点续传

## Veo 3.1 技术参考

| 功能 | 说明 |
|------|------|
| 图生视频 | 使用分镜图作为起始帧 |
| 单片段/场景时长 | 说书模式默认 4 秒，剧集动画模式默认 8 秒 |
| 时长选项 | 仅支持 4s / 6s / 8s |
| 分辨率 | 1080p（默认） |
| 宽高比 | 说书模式 9:16 竖屏，剧集动画模式 16:9 横屏 |

### 关于 extend 功能

Veo 3.1 的 extend 功能**仅适用于延长单个片段/场景**（例如让一个动作继续进行），不适合用于串联不同镜头。

如果某个片段/场景需要更长时长，可以使用 extend：
- 每次扩展固定 +7 秒
- 最多扩展至 148 秒
- 扩展时无法更换起始帧

> ⚠️ **重要分辨率限制**：
> - **延长功能仅支持 720p 分辨率的视频**
> - 1080p 或更高分辨率的视频无法使用 extend 延长
> - 如需延长，必须先以 720p 分辨率生成初始视频

> ⚠️ **不推荐用于多镜头串联**：不同场景之间应使用 ffmpeg 拼接，而非 extend。

## 工作流程

1. **加载项目和剧本**
   - 如未指定项目名称，询问用户
   - 从 `projects/{项目名}/scripts/` 加载剧本
   - 确认所有场景都有 `storyboard_image`

2. **生成视频**
   - 遍历指定 episode 的所有场景
   - 每个场景使用分镜图作为起始帧独立生成视频
   - 保存 checkpoint 支持断点续传

3. **更新剧本**
   - 更新 `video_clip` 路径
   - 更新场景状态为 `completed`

## 视频 Prompt 生成

Prompt 由 `generate_video.py` 中的函数自动构建，根据内容模式选择不同的构建器：

- **说书模式**: `build_segment_prompt()` - 不包含 `novel_text`（旁白），仅包含角色对话（如有）
- **剧集动画模式**: `build_scene_prompt()` - 包含对话、旁白、音效

### Prompt 结构

根据 Veo 官方指导和最新数据结构，prompt 分为两部分：

#### image_prompt（用于分镜图生成）

```json
{
  "scene": "[场景环境描述：包含地点、氛围、关键视觉元素的叙述性描述]",
  "composition": {
    "shot_type": "[镜头类型：Long Shot / Medium Shot / Close-up / Extreme Close-up 等]",
    "lighting": "[光线描述：光源方向、色温、阴影特征]",
    "ambiance": "[氛围描述：色调、情绪、环境效果]"
  }
}
```

#### video_prompt（用于视频生成）

```json
{
  "action": "[动作描述：主体在场景时长内的具体动作、姿态、表情变化]",
  "camera_motion": "[摄像机运动：Static / Pan Left / Pan Right / Tilt Up / Tilt Down / Zoom In / Zoom Out / Tracking Shot]",
  "ambiance_audio": "[环境音效：仅描述 diegetic sound（画内音），不包含背景音乐]",
  "dialogue": [
    {"speaker": "[角色名]", "line": "[对话内容]"}
  ],
  "narration": "[可选：旁白或内心独白，仅剧集动画模式使用]"
}
```

### 剧集动画模式示例

剧本场景数据：
```json
{
  "scene_id": "E1S01",
  "image_prompt": {
    "scene": "[场景环境描述文本]",
    "composition": {
      "shot_type": "[镜头类型]",
      "lighting": "[光线描述]",
      "ambiance": "[氛围描述]"
    }
  },
  "video_prompt": {
    "action": "[动作描述文本]",
    "camera_motion": "[摄像机运动类型]",
    "ambiance_audio": "[环境音效描述]",
    "dialogue": [
      {"speaker": "[角色名]", "line": "[对话内容]"}
    ],
    "narration": "[旁白文本，可选]"
  }
}
```

### 说书模式示例

```json
{
  "segment_id": "E1S01",
  "novel_text": "[小说原文，用于后期人工配音]",
  "image_prompt": {
    "scene": "[场景环境描述文本]",
    "composition": {
      "shot_type": "[镜头类型]",
      "lighting": "[光线描述]",
      "ambiance": "[氛围描述]"
    }
  },
  "video_prompt": {
    "action": "[动作描述文本]",
    "camera_motion": "[摄像机运动类型]",
    "ambiance_audio": "[环境音效描述]",
    "dialogue": [
      {"speaker": "[角色名]", "line": "[仅当 novel_text 包含角色对话时填写]"}
    ]
  }
}
```

> **说书模式关键点**：`novel_text` 不参与视频生成，由后期人工配音。`dialogue` 仅包含原文中的角色对话。

### 对话格式

Veo 3 推荐的对话格式：`Speaker（manner）说道："text"`

| emotion | 转换为 manner |
|---------|--------------|
| happy | 开心地 |
| sad | 悲伤地 |
| angry | 愤怒地 |
| surprised | 惊讶地 |
| scared | 恐惧地 |
| neutral | 平静地 |
| determined | 坚定地 |
| cold | 冷淡地 |
| proud | 得意地 |
| anxious | 焦虑地 |

### 音效描述

音效自然融入场景，不使用标签：
- 1 个音效：`背景中传来{效果}。`
- 2 个音效：`可以听到{效果1}和{效果2}。`
- 多个音效：`环境音：{效果1}、{效果2}，以及{效果3}。`

### 镜头运动描述

镜头运动转换为自然语言：
- `dolly in` → `镜头缓缓推进。`
- `pan left` → `镜头向左平移。`
- `track` → `镜头跟随移动。`
- `handheld` → `手持镜头轻微晃动。`

### Negative Prompt

通过 API 参数自动排除不需要的元素：
```
background music, BGM, soundtrack, musical accompaniment
```

> 无需在视频 prompt 中声明"不要背景音乐"，这由 `negative_prompt` 参数处理。

## 音频规范

视频音频包含以下内容：
- **人物对白**：通过 `dialogue` 字段自动生成
- **环境音**：风声、雨声、城市噪音、自然声等
- **场景声效**：脚步声、门声、物体碰撞声等

> BGM 通过 `negative_prompt` 自动排除。如需添加背景音乐，请使用 `/compose-video` 后期处理。

## API 使用

### 视频生成

`generate_video()` 方法支持视频生成功能：

```python
from lib.gemini_client import GeminiClient

client = GeminiClient()

# 获取内容模式对应的画面比例
# 说书模式: 9:16, 剧集动画模式: 16:9
video_aspect_ratio = get_aspect_ratio(project_data, 'video')

# 获取默认时长
# 说书模式: 4s, 剧集动画模式: 8s
default_duration = 4 if content_mode == 'narration' else 8

# 生成视频（返回三元组）
path, video_ref, video_uri = client.generate_video(
    prompt=video_prompt,
    start_image="projects/{项目名}/storyboards/scene_E1S1.png",
    aspect_ratio=video_aspect_ratio,
    duration_seconds=str(default_duration),
    output_path="projects/{项目名}/videos/scene_E1S1.mp4"
)
```

### 视频延长（仅当需要超过 8 秒时）

> ⚠️ **重要**：延长功能仅支持 720p 分辨率的视频。如需使用延长功能，必须先以 720p 生成初始视频。

使用 `generate_video()` 方法，通过 `video` 参数延长视频：

```python
# 先生成初始视频（如需延长，必须使用 720p）
path, video_ref, video_uri = client.generate_video(
    prompt=video_prompt,
    start_image="projects/{项目名}/storyboards/scene_E1S1.png",
    aspect_ratio="16:9",
    duration_seconds="8",
    resolution="720p",  # ⚠️ 延长功能要求 720p
    output_path="projects/{项目名}/videos/scene_E1S1.mp4"
)

# 延长同一个场景（+7秒）
# ⚠️ 注意：仅 720p 视频可以延长，1080p 无法延长
path2, video_ref2, video_uri2 = client.generate_video(
    prompt="继续当前动作...",  # 延续同一场景的 prompt
    video=video_ref,  # 传入上一次返回的 video_ref
    output_path="projects/{项目名}/videos/scene_E1S1_extended.mp4"
)

# 也可以使用本地视频文件延长
path3, video_ref3, video_uri3 = client.generate_video(
    prompt="继续当前动作...",
    video="projects/{项目名}/videos/scene_E1S1.mp4",  # 本地文件路径
    output_path="projects/{项目名}/videos/scene_E1S1_extended2.mp4"
)

# 或使用保存的 URI 恢复并延长（跨会话）
path4, video_ref4, video_uri4 = client.generate_video(
    prompt="继续当前动作...",
    video=video_uri,  # 使用之前保存的 URI 字符串
    output_path="projects/{项目名}/videos/scene_E1S1_extended3.mp4"
)
```

### video 参数支持的类型

| 类型 | 示例 | 说明 |
|------|------|------|
| Video 对象 | `video_ref` | 来自上一次调用的返回值，直接使用 |
| GCS URI | `gs://bucket/video.mp4` | 包装为 Video 对象 |
| API URI | `https://...` | 包装为 Video 对象 |
| 本地文件 | `./output.mp4` | 读取文件内容 |

## 生成前检查

- [ ] 所有场景都有已批准的分镜图
- [ ] 对话文本长度适当
- [ ] 动作描述清晰简单
