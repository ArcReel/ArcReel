---
name: compose-video
description: 使用 ffmpeg 进行视频后期处理。使用场景：(1) 用户运行 /compose-video 命令，(2) 需要添加背景音乐、片头片尾，(3) 需要合并多个 episode 的视频。主要用于后期处理，视频连贯性由 generate-video 的连续模式保证。
---

# 合成视频

使用 ffmpeg 进行视频后期处理和多片段合成。

## 定位说明

**重要**：视频的连贯性现在由 `/generate-video --continuous` 模式保证。本 skill 主要用于：

1. **添加背景音乐** - 为连续视频添加 BGM
2. **多集合成** - 将多个 episode 的视频合并
3. **添加片头片尾** - 插入 intro/outro
4. **后备方案** - 当连续生成失败时，拼接独立场景视频

## 使用场景

### 1. 添加背景音乐

```bash
python .claude/skills/compose-video/scripts/compose_video.py \
    my_project script.json \
    --input output/episode_01.mp4 \
    --music background_music.mp3 \
    --music-volume 0.3
```

### 2. 合并多集视频

```bash
python .claude/skills/compose-video/scripts/compose_video.py \
    my_project script.json \
    --merge-episodes 1 2 3 \
    --output final_movie.mp4
```

### 3. 添加片头片尾

```bash
python .claude/skills/compose-video/scripts/compose_video.py \
    my_project script.json \
    --intro intro.mp4 \
    --outro outro.mp4 \
    --output final_with_intro.mp4
```

### 4. 后备拼接（不推荐）

当连续生成不可用时，拼接独立场景视频：

```bash
python .claude/skills/compose-video/scripts/compose_video.py \
    my_project script.json \
    --fallback-mode \
    --output chapter_01_final.mp4
```

## 工作流程

1. **加载项目和剧本**
   - 如未指定项目名称，询问用户
   - 从 `projects/{项目名}/scripts/` 加载剧本
   - 检查 `output/` 目录中的连续视频

2. **选择处理模式**
   - 添加 BGM
   - 合并多集
   - 添加片头片尾
   - 后备拼接

3. **执行处理**
   - 使用 ffmpeg 进行相应处理
   - 保持原始视频不变
   - 输出到 `projects/{项目名}/output/`

## 转场类型（后备模式）

根据剧本中的 `transition_to_next` 字段：

| 类型 | ffmpeg 滤镜 |
|------|-------------|
| cut | 直接拼接 |
| fade | `xfade=transition=fade:duration=0.5` |
| dissolve | `xfade=transition=dissolve:duration=0.5` |
| wipe | `xfade=transition=wipeleft:duration=0.5` |

## 环境要求

- ffmpeg 已安装并在 PATH 中
- 连续视频或场景视频已生成
- 视频分辨率一致（9:16 竖屏）

## 输出选项

1. **添加 BGM** - 混合原音频和背景音乐
2. **合并视频** - 多个视频拼接为一个
3. **添加片头片尾** - 在视频首尾插入素材

## 处理前检查

- [ ] 连续视频或场景视频存在且可播放
- [ ] 视频分辨率一致（9:16 竖屏）
- [ ] 背景音乐文件存在（如需要）
- [ ] 片头片尾文件存在（如需要）

## 推荐工作流

1. 使用 `/generate-video --continuous --episode N` 生成连贯视频
2. 使用 `/compose-video` 添加 BGM（可选）
3. 使用 `/compose-video` 添加片头片尾（可选）
