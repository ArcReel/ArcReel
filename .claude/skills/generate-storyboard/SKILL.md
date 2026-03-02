---
name: generate-storyboard
description: 通过生成队列提交分镜图任务。两种模式均由 generation worker 产出分镜图。使用场景：(1) 用户运行 /generate-storyboard 命令，(2) 剧本中有场景没有分镜图，(3) 用户想在视频生成前预览场景。
---

# 生成分镜图

通过生成队列创建分镜图。

## 内容模式支持

系统支持两种内容模式，生成流程和画面比例根据模式自动调整：

| 模式 | 流程 | 画面比例 |
|------|------|----------|
| 说书+画面（默认） | **队列生成** | **9:16 竖屏** |
| 剧集动画 | **队列生成** | 16:9 横屏 |

> 画面比例通过 API 参数设置，不包含在 prompt 中。

## 说书模式流程（narration）

### 队列生成分镜图
- 由 generation worker 生成单独场景图（**9:16 竖屏**）
- 使用 character_sheet 和 clue_sheet 作为参考图保持人物一致性
- 自动将上一张分镜图加入参考图，提升相邻画面连续性
- 若当前片段 `segment_break=true`，则跳过上一张分镜图参考
- 保存为 `storyboards/scene_{segment_id}.png`
- 更新剧本中的 `storyboard_image` 字段
- 用于视频生成的起始帧

### 数据结构

```json
{
  "generated_assets": {
    "storyboard_image": "storyboards/scene_E1S01.png",
    "video_clip": null,
    "status": "storyboard_ready"
  }
}
```

## 剧集动画模式流程（drama）

### 队列生成分镜图
- 由 generation worker 生成单独场景图（**16:9 横屏**）
- 使用 character_sheet 和 clue_sheet 作为参考图保持人物一致性
- 自动将上一张分镜图加入参考图，提升相邻画面连续性
- 若当前场景 `segment_break=true`，则跳过上一张分镜图参考
- 保存为 `storyboards/scene_{scene_id}.png`
- 更新剧本中的 `storyboard_image` 字段
- 用于视频生成的起始帧

### 数据结构

```json
{
  "generated_assets": {
    "storyboard_image": "storyboards/scene_E1S01.png",
    "video_clip": null,
    "status": "storyboard_ready"
  }
}
```

## 命令行用法

```bash
# 提交所有缺失分镜图到生成队列（自动检测 content_mode）
python .claude/skills/generate-storyboard/scripts/generate_storyboard.py \
    my_project script.json

# 为指定片段/场景重新生成
python .claude/skills/generate-storyboard/scripts/generate_storyboard.py \
    my_project script.json --segment-ids E1S01 E1S02

# 为单个片段/场景重新生成（自动查找上一张分镜图）
python .claude/skills/generate-storyboard/scripts/generate_storyboard.py \
    my_project script.json --scene E1S05
```

> **注意**：脚本要求 generation worker 在线；worker 负责实际图像生成与速率控制。

## 工作流程

1. **加载项目和剧本**
   - 如未指定项目名称，询问用户
   - 从 `projects/{项目名}/scripts/` 加载剧本
   - 确认所有人物都有 `character_sheet` 图像

2. **生成分镜图**
   - 运行 `.claude/skills/generate-storyboard/scripts/generate_storyboard.py`
   - 脚本自动检测 content_mode 并按相邻关系串联依赖任务
   - generation worker 根据模式选择画面比例，并自动查找上一张分镜图（若存在）

3. **审核检查点**
   - 展示每张分镜图
   - 询问用户是否批准或重新生成

4. **更新剧本**
   - 更新 `storyboard_image` 路径
   - 更新场景状态

```
projects/{项目名}/storyboards/
├── scene_E1S01.png        # 单独场景图
├── scene_E1S02.png
└── ...
```

## 分镜图 Prompt 模板

```
场景 [scene_id/segment_id] 的分镜图：

- 画面描述：[visual.description]
- 镜头构图：[visual.shot_type]（wide shot / medium shot / close-up / extreme close-up）
- 镜头运动起点：[visual.camera_movement]
- 光线条件：[visual.lighting]
- 画面氛围：[visual.mood]
- 人物：[characters_in_scene/segment]
- 动作：[action]

风格要求：
- 电影分镜图风格，根据项目 style 设定
- 画面构图完整，焦点清晰

人物必须与提供的人物参考图完全一致。
```

> 画面比例（9:16 或 16:9）通过 API 参数设置，不写入 prompt。

### 字段说明

| 字段 | 来源 | 说明 |
|------|------|------|
| description | visual.description | 主体和环境描述 |
| shot_type | visual.shot_type | 镜头构图类型 |
| camera_movement | visual.camera_movement | 镜头运动方式（图片表现起始状态） |
| lighting | visual.lighting | 光线条件 |
| mood | visual.mood | 画面氛围和色调 |
| action | scene.action | 人物动作描述 |

## 人物一致性

**关键**：始终传入人物参考图以保持一致性；相邻片段默认还会引用上一张分镜图。画面比例根据内容模式自动选择。

```python
from lib.gemini_client import GeminiClient

client = GeminiClient()

# 生成分镜图（根据内容模式选择画面比例）
# 说书模式: 9:16, 剧集动画模式: 16:9
storyboard_aspect_ratio = get_aspect_ratio(project_data, 'storyboard')

image = client.generate_image(
    prompt=scene_prompt,
    reference_images=[
        f"projects/{项目名}/characters/{人物名}.png"           # 人物参考
        for 人物名 in scene_characters
    ],
    aspect_ratio=storyboard_aspect_ratio,
    output_path=f"projects/{项目名}/storyboards/scene_{scene_id}.png"
)
```

## 生成前检查

生成分镜前确认：
- [ ] 所有人物都有已批准的 character_sheet 图像
- [ ] 场景视觉描述完整
- [ ] 人物动作已指定

## 质量检查清单

### 分镜图审核
- [ ] 人物与参考图一致
- [ ] 画面质量适合作为视频起始帧
- [ ] 光线和氛围正确
- [ ] 场景准确传达预期动作
- [ ] 整体风格统一

## 错误处理

1. **单场景失败不影响批次**：记录失败场景，继续处理下一个
2. **失败汇总报告**：生成结束后列出所有失败的场景和原因
3. **增量生成**：检测已存在的场景图，跳过重复生成
4. **支持重试**：使用 `--segment-ids E1S01` 重新生成失败场景
