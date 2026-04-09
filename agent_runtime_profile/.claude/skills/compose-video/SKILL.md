---
name: compose-video
description: Video post-processing and composition. Use when the user says "add background music", "merge videos", "add intro/outro", wants to add BGM to the finished video, or needs to concatenate multiple episodes.
---

# Compose Video

Use ffmpeg for video post-processing and multi-segment composition.

## Use Cases

### 1. Add Background Music

```bash
python .claude/skills/compose-video/scripts/compose_video.py --episode {N} --music background_music.mp3 --music-volume 0.3
```

### 2. Merge Multiple Episodes

```bash
python .claude/skills/compose-video/scripts/compose_video.py --merge-episodes 1 2 3 --output final_movie.mp4
```

### 3. Add Intro/Outro

```bash
python .claude/skills/compose-video/scripts/compose_video.py --episode {N} --intro intro.mp4 --outro outro.mp4
```

### 4. Fallback Concatenation

In normal flow, videos are generated independently per scene by Veo 3.1 and need to be concatenated into a complete episode. When the standard transition concatenation (xfade filter) fails due to inconsistent encoding parameters, fallback mode uses the ffmpeg concat demuxer for seamless quick concatenation, ensuring at least a complete video can be output:

```bash
python .claude/skills/compose-video/scripts/compose_video.py --episode {N} --fallback-mode
```

## Workflow

1. **Load project and script** — check whether video files exist
2. **Select processing mode** — add BGM / merge episodes / add intro/outro / fallback concatenation
3. **Execute processing** — process with ffmpeg; keep original videos unchanged; output to `output/`

## Transition Types (Fallback Mode)

Based on the `transition_to_next` field in the script:

| Type | ffmpeg Filter |
|------|-------------|
| cut | Direct concatenation |
| fade | `xfade=transition=fade:duration=0.5` |
| dissolve | `xfade=transition=dissolve:duration=0.5` |
| wipe | `xfade=transition=wipeleft:duration=0.5` |

## Pre-Processing Checklist

- [ ] Scene videos exist and are playable
- [ ] Video resolutions are consistent (aspect ratio determined by content_mode)
- [ ] Background music / intro/outro files exist (if needed)
- [ ] ffmpeg is installed and in PATH
