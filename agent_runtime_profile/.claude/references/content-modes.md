# 内容模式参考

通过 `project.json` 的 `content_mode` 字段切换。各 skill 的脚本会自动读取并应用对应规格，无需在 prompt 中指定画面比例。

| 维度 | 说书+画面（narration，默认） | 剧集动画（drama） |
|------|---------------------------|-----------------|
| 数据结构 | `segments` 数组 | `scenes` 数组 |
| 画面比例 | 9:16 竖屏 | 16:9 横屏 |
| 默认时长 | 4 秒/片段 | 8 秒/场景 |
| 时长可选 | 4s / 6s / 8s | 4s / 6s / 8s |
| 对白来源 | 后期人工配音（小说原文） | 演员对话 |
| 视频 Prompt | 仅角色对话（如有），无旁白 | 包含对话、旁白、音效 |
| 预处理 Agent | split-narration-segments | normalize-drama-script |

## 视频规格

- **分辨率**：图片 1K，视频 1080p
- **生成方式**：每个片段/场景独立生成，分镜图作为起始帧
- **拼接方式**：ffmpeg 拼接独立片段，不使用 Veo extend 串联镜头
- **BGM**：通过 `negative_prompt` API 参数自动排除，后期用 compose-video 添加

## Veo 3.1 extend 说明

- 仅用于延长**单个**片段/场景（每次 +7 秒，最多 148 秒）
- **仅支持 720p**，1080p 无法延长
- 不适合串联不同镜头

## Prompt 语言

- 图片/视频生成 prompt 使用**中文**
- 采用叙事式描述，不使用关键词罗列

> 参考 `docs/google-genai-docs/nano-banana.md` 第 365 行起的 Prompting guide and strategies。
