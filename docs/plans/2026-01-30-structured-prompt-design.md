# 结构化 Prompt 设计

**日期**: 2026-01-30
**状态**: 待确认

---

## 概述

基于 StoryCraft 项目的 Prompt 工程实践，对我们项目的 `image_prompt` 和 `video_prompt` 进行结构化改造，并引入固定风格选项系统。

### 改进目标

1. **结构化 Prompt 模板** - 将自由文本改为结构化字段
2. **YAML 格式传递** - 转换为 YAML 格式传给 Gemini/Veo API
3. **固定风格选项** - 将自由填写的风格改为预设选项
4. **统一 negative_prompt** - 标准化禁止生成的元素

---

## 一、结构化 Prompt 模板

### 1.1 imagePrompt 结构

```json
{
  "image_prompt": {
    "scene": "A dimly lit underground laboratory with flickering monitors and scattered blueprints",
    "composition": {
      "shot_type": "Medium Shot",
      "lighting": "cold blue light from monitors, harsh shadows",
      "ambiance": "tense, mysterious atmosphere with steam rising from equipment"
    }
  }
}
```

#### 字段说明

| 字段 | 类型 | 说明 |
|-----|------|------|
| `scene` | string | 场景描述，包含环境、物品、氛围 |
| `composition.shot_type` | enum | 镜头类型，从预设选项中选择 |
| `composition.lighting` | string | 光线描述（光源、色温、阴影） |
| `composition.ambiance` | string | 氛围描述（色调、情绪、环境效果） |

> **注意**：
> - **Style（风格）** 由项目级 `project.json` 的 `style` 字段统一决定，不在每个 segment 中重复
> - **角色和线索** 通过现有的 `characters_in_segment` / `clues_in_segment` 字段引用，不在 imagePrompt 中重复

### 1.2 videoPrompt 结构

```json
{
  "video_prompt": {
    "action": "The scientist slowly turns around, eyes widening as alarms begin to flash",
    "camera_motion": "Dolly In",
    "ambiance_audio": "electrical humming, distant alarm beeping, footsteps on metal floor",
    "dialogue": [
      {
        "speaker": "Dr. Chen",
        "line": "It's happening... exactly as I predicted."
      }
    ]
  }
}
```

#### 字段说明

| 字段 | 类型 | 说明 |
|-----|------|------|
| `action` | string | 动作描述，明确说明主体在做什么 |
| `camera_motion` | enum | 摄像机运动，从预设选项中选择 |
| `ambiance_audio` | string | 环境音效描述（仅 diegetic sound，不含音乐） |
| `dialogue` | array | 对话列表，每条包含 speaker 和 line |

---

## 二、预设选项定义

### 2.1 Style（视觉风格）

| 选项 | 说明 |
|-----|------|
| `Photographic` | 写实摄影风格 |
| `Anime` | 日式动漫风格 |
| `3D Animation` | 3D 动画风格 |

### 2.2 shot_type（镜头类型）

| 选项 | 中文 | 说明 |
|-----|------|------|
| `Extreme Close-up` | 大特写 | 面部局部或物体细节 |
| `Close-up` | 特写 | 面部或重要物体 |
| `Medium Close-up` | 中近景 | 头部到胸部 |
| `Medium Shot` | 中景 | 头部到腰部 |
| `Medium Long Shot` | 中远景 | 头部到膝盖 |
| `Long Shot` | 远景 | 全身可见 |
| `Extreme Long Shot` | 大远景 | 角色在环境中很小 |
| `Over-the-shoulder` | 过肩镜头 | 从一个角色肩后看另一个角色 |
| `Point-of-view` | 主观镜头 | 从角色视角看 |

### 2.3 camera_motion（摄像机运动）

| 选项 | 中文 | 说明 |
|-----|------|------|
| `Static` | 静止 | 摄像机固定不动 |
| `Pan Left` | 左摇 | 摄像机水平向左转动 |
| `Pan Right` | 右摇 | 摄像机水平向右转动 |
| `Tilt Up` | 上摇 | 摄像机垂直向上转动 |
| `Tilt Down` | 下摇 | 摄像机垂直向下转动 |
| `Zoom In` | 推进 | 镜头拉近 |
| `Zoom Out` | 拉远 | 镜头拉远 |
| `Tracking Shot` | 跟踪 | 摄像机跟随主体移动 |

---

## 三、YAML 格式转换

### 3.1 转换工具函数

在调用 Gemini/Veo API 时，将结构化 Prompt 转换为 YAML 格式字符串。

#### imagePrompt 转换示例

**输入 JSON**：
```json
{
  "scene": "A dimly lit underground laboratory with flickering monitors",
  "composition": {
    "shot_type": "Medium Shot",
    "lighting": "cold blue light from monitors, harsh shadows",
    "ambiance": "tense, mysterious atmosphere"
  }
}
```

**输出 YAML**（Style 从项目配置注入）：
```yaml
Style: Anime
Scene: A dimly lit underground laboratory with flickering monitors
Composition:
  shot_type: Medium Shot
  lighting: cold blue light from monitors, harsh shadows
  ambiance: tense, mysterious atmosphere
```

#### videoPrompt 转换示例

**输入 JSON**：
```json
{
  "action": "The scientist slowly turns around, eyes widening",
  "camera_motion": "Dolly In",
  "ambiance_audio": "electrical humming, distant alarm beeping",
  "dialogue": [
    {
      "speaker": "Dr. Chen",
      "line": "It's happening..."
    }
  ]
}
```

**输出 YAML**：
```yaml
Action: The scientist slowly turns around, eyes widening
Camera_Motion: Dolly In
Ambiance_Audio: electrical humming, distant alarm beeping
Dialogue:
  - Speaker: Dr. Chen
    Line: It's happening...
```

### 3.2 Python 实现

```python
import yaml

def image_prompt_to_yaml(image_prompt: dict, project_style: str) -> str:
    """
    将 imagePrompt 结构转换为 YAML 格式字符串

    Args:
        image_prompt: segment 中的 image_prompt 对象
        project_style: 项目级风格设置（从 project.json 读取）
    """
    ordered = {
        "Style": project_style,
        "Scene": image_prompt["scene"],
        "Composition": {
            "shot_type": image_prompt["composition"]["shot_type"],
            "lighting": image_prompt["composition"]["lighting"],
            "ambiance": image_prompt["composition"]["ambiance"],
        },
    }
    return yaml.dump(ordered, allow_unicode=True, default_flow_style=False)


def video_prompt_to_yaml(video_prompt: dict) -> str:
    """将 videoPrompt 结构转换为 YAML 格式字符串"""
    dialogue = [
        {"Speaker": d["speaker"], "Line": d["line"]}
        for d in video_prompt.get("dialogue", [])
    ]

    ordered = {
        "Action": video_prompt["action"],
        "Camera_Motion": video_prompt["camera_motion"],
        "Ambiance_Audio": video_prompt["ambiance_audio"],
        "Dialogue": dialogue,
    }
    return yaml.dump(ordered, allow_unicode=True, default_flow_style=False)
```

---

## 四、negative_prompt 标准化

在调用 Veo API 时，统一使用以下 negative_prompt：

```python
negative_prompt = "music, BGM, background music, subtitles, low quality"
```

### 更新位置

`lib/gemini_client.py` 中的 `generate_video()` 方法：

```python
def generate_video(
    self,
    prompt: str,
    # ... 其他参数
    negative_prompt: str = "music, BGM, background music, subtitles, low quality",
    # ...
) -> tuple:
```

---

## 五、数据结构变更

### 5.1 剧本 JSON 结构（说书模式）

**变更前**：
```json
{
  "segment_id": "E1S01",
  "novel_text": "原文内容...",
  "image_prompt": "中景镜头，实验室内...",
  "video_prompt": "镜头缓慢推进...",
  "characters_in_segment": ["Dr. Chen"],
  "clues_in_segment": ["Blueprint"],
  "duration_seconds": 4
}
```

**变更后**：
```json
{
  "segment_id": "E1S01",
  "novel_text": "原文内容...",
  "image_prompt": {
    "scene": "A high-tech laboratory with holographic displays and scattered research papers",
    "composition": {
      "shot_type": "Medium Shot",
      "lighting": "cool fluorescent lights with blue accent from holograms",
      "ambiance": "clinical, futuristic atmosphere with soft mechanical hum"
    }
  },
  "video_prompt": {
    "action": "Dr. Chen examines a holographic display, then turns sharply as an alert flashes",
    "camera_motion": "Static",
    "ambiance_audio": "soft mechanical whirring, sudden alert beep, fabric rustling",
    "dialogue": []
  },
  "characters_in_segment": ["Dr. Chen"],
  "clues_in_segment": ["Blueprint"],
  "duration_seconds": 4
}
```

### 5.2 project.json 风格字段

**变更前**：
```json
{
  "style": "古装宫廷风格，精致唯美画面"
}
```

**变更后**：
```json
{
  "style": "Anime"
}
```

仅允许预设选项：`Photographic` | `Anime` | `3D Animation`

---

## 六、Agent 生成指令更新

需要更新 `novel-to-narration-script` 和 `novel-to-storyboard-script` Agent 的 System Prompt，指导其输出结构化格式。

### 6.1 imagePrompt 生成指令

```
For each segment, generate an image_prompt object with the following structure:

{
  "scene": "[Describe the environment, objects, and atmosphere in 1-2 sentences]",
  "composition": {
    "shot_type": "[Choose from: Extreme Close-up, Close-up, Medium Close-up, Medium Shot, Medium Long Shot, Long Shot, Extreme Long Shot, Over-the-shoulder, Point-of-view]",
    "lighting": "[Describe light sources, color temperature, and shadow characteristics]",
    "ambiance": "[Describe color tones, mood, and environmental effects like fog, dust, etc.]"
  }
}

Note:
- Style is defined at project level (project.json), not per segment
- Characters and clues are referenced via characters_in_segment and clues_in_segment fields
```

### 6.2 videoPrompt 生成指令

```
For each segment, generate a video_prompt object with the following structure:

{
  "action": "[Describe what the subject(s) are doing within the clip duration. Be specific about movements, gestures, and expressions]",
  "camera_motion": "[Choose from: Static, Pan Left, Pan Right, Tilt Up, Tilt Down, Zoom In, Zoom Out, Tracking Shot]",
  "ambiance_audio": "[Describe diegetic sounds only - environmental sounds, footsteps, object sounds. Do NOT mention music or narration]",
  "dialogue": [
    {
      "speaker": "[Character name from characters_in_segment]",
      "line": "[The spoken dialogue]"
    }
  ]
}
```

---

## 七、实现计划

| 阶段 | 内容 | 涉及文件 |
|------|------|---------|
| Phase 1 | 新增 YAML 转换工具函数 | `lib/prompt_utils.py` (新增) |
| Phase 2 | 更新 negative_prompt 默认值 | `lib/gemini_client.py` |
| Phase 3 | 更新 Agent System Prompt | `.claude/commands/novel-to-narration-script.md`, `.claude/commands/novel-to-storyboard-script.md` |
| Phase 4 | 更新分镜生成脚本以使用 YAML | `generate_storyboard.py`, `generate_video.py` |
| Phase 5 | 更新 WebUI 风格选择器 | `webui/` 相关文件 |

---

## 八、迁移策略

**直接重构**：所有项目统一使用新的结构化格式，不保留旧格式兼容。

### 迁移步骤

1. 更新 Agent System Prompt，输出结构化格式
2. 更新分镜/视频生成脚本，解析结构化 Prompt 并转换为 YAML
3. 现有项目剧本需重新生成或手动迁移

---

## 九、参考

- [StoryCraft 调研报告](/docs/storycraft-investigation.md)
- [Veo Prompt Guide:423](/docs/veo.md)
- [StoryCraft prompt-utils.ts](/docs/storycraft/lib/utils/prompt-utils.ts)
