---
name: generate-video
description: 为剧本场景生成视频片段。当用户说"生成视频"、"把分镜图变成视频"、想重新生成某个场景的视频、或视频生成中断需要续传时使用。支持整集批量、单场景、断点续传等模式。
---

# 生成视频

使用 Veo 3.1 API 为每个场景/片段创建视频，以分镜图作为起始帧。

> 画面比例、时长等规格由项目配置和视频模型能力决定，脚本自动处理。

## 命令行用法

```bash
# 标准模式：生成整集所有待处理场景（默认；集号从 script 顶层 episode 字段或文件名推导）
python .claude/skills/generate-video/scripts/generate_video.py episode_{N}.json

# 断点续传：从上次中断处继续
python .claude/skills/generate-video/scripts/generate_video.py episode_{N}.json --resume

# 单场景：测试或重新生成
python .claude/skills/generate-video/scripts/generate_video.py episode_{N}.json --scene E1S1

# 批量自选：指定多个场景
python .claude/skills/generate-video/scripts/generate_video.py episode_{N}.json --scenes E1S01,E1S05,E1S10

# 全部待处理
python .claude/skills/generate-video/scripts/generate_video.py episode_{N}.json --all
```

> 所有任务一次性提交到生成队列，由 Worker 按 per-provider 并发配置自动调度。

## 工作流程

1. **加载项目和剧本** — 确认所有场景都有 `storyboard_image`
2. **生成视频** — 脚本自动构建 Prompt、调用 API、保存 checkpoint
3. **审核检查点** — 展示结果，用户可重新生成不满意的场景
4. **更新剧本** — 自动更新 `video_clip` 路径和场景状态

## Prompt 构建

Prompt 由脚本内部自动构建，根据 content_mode 选择不同策略。脚本从剧本 JSON 读取以下字段：

**image_prompt**（用于分镜图参考）：scene、composition（shot_type、lighting、ambiance）

**video_prompt**（用于视频生成）：action、camera_motion、ambiance_audio、dialogue、narration（仅 drama）

- 说书模式：`novel_text` 不参与视频生成（后期人工配音），`dialogue` 仅包含原文中的角色对话
- 剧集动画模式：包含完整的对话、旁白、音效
- Negative prompt 自动排除 BGM

## 生成前检查

- [ ] 所有场景都有已批准的分镜图
- [ ] 对话文本长度适当
- [ ] 动作描述清晰简单
