---
name: compose-video
description: 视频后期处理与合成。当用户说"加背景音乐"、"合并视频"、"加片头片尾"、想为成片添加 BGM、或需要将多集视频拼接时使用。
---

# 合成视频

使用 ffmpeg 进行视频后期处理和多片段合成。

## 使用场景

### 1. 添加背景音乐

```bash
cd projects/{project_name} && python ../../.claude/skills/compose-video/scripts/compose_video.py --episode {N} --music background_music.mp3 --music-volume 0.3
```

### 2. 合并多集视频

```bash
cd projects/{project_name} && python ../../.claude/skills/compose-video/scripts/compose_video.py --merge-episodes 1 2 3 --output final_movie.mp4
```

### 3. 添加片头片尾

```bash
cd projects/{project_name} && python ../../.claude/skills/compose-video/scripts/compose_video.py --episode {N} --intro intro.mp4 --outro outro.mp4
```

### 4. 后备拼接

正常流程中视频由 Veo 3.1 逐场景独立生成，最终需要拼接成完整剧集。当标准的转场拼接（xfade 滤镜）因编码参数不一致而失败时，后备模式使用 ffmpeg concat demuxer 做无转场的快速拼接，确保至少能输出完整视频：

```bash
cd projects/{project_name} && python ../../.claude/skills/compose-video/scripts/compose_video.py --episode {N} --fallback-mode
```

## 工作流程

1. **加载项目和剧本** — 检查视频文件是否存在
2. **选择处理模式** — 添加 BGM / 合并多集 / 添加片头片尾 / 后备拼接
3. **执行处理** — 使用 ffmpeg 处理，保持原始视频不变，输出到 `output/`

## 转场类型（后备模式）

根据剧本中的 `transition_to_next` 字段：

| 类型 | ffmpeg 滤镜 |
|------|-------------|
| cut | 直接拼接 |
| fade | `xfade=transition=fade:duration=0.5` |
| dissolve | `xfade=transition=dissolve:duration=0.5` |
| wipe | `xfade=transition=wipeleft:duration=0.5` |

## 处理前检查

- [ ] 场景视频存在且可播放
- [ ] 视频分辨率一致（由 content_mode 决定画面比例）
- [ ] 背景音乐 / 片头片尾文件存在（如需要）
- [ ] ffmpeg 已安装并在 PATH 中
