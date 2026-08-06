---
name: compose-video
description: Drama 成片：当 drama storyboard 视频齐全，需要拼接、转场或加入 BGM 时使用。
---

# Drama 单集成片

只支持顶层 `scenes[]` 的 drama storyboard 剧本。narration、ad 与 reference-video 使用 Web 端剪映草稿导出。

脚本在项目 cwd 内运行：

```bash
python .claude/skills/compose-video/scripts/compose_video.py scripts/episode_1.json
python .claude/skills/compose-video/scripts/compose_video.py scripts/episode_1.json --music background_music.mp3
python .claude/skills/compose-video/scripts/compose_video.py scripts/episode_1.json --no-transitions
python .claude/skills/compose-video/scripts/compose_video.py scripts/episode_1.json --output episode_1_final.mp4
```

## 前置与完成

- 所有 scene 视频 current；
- `ffmpeg` 与 `ffprobe` 可用；
- BGM 与输出路径位于项目内；
- 命令退出码为 0，输出文件存在且可被 ffprobe 读取。

CLI 当前没有 `--music-volume`。用户要求精确音量时说明限制，使用 Web 端剪映调节；不发明不存在的参数。脚本内默认 BGM 音量为 0.3。
