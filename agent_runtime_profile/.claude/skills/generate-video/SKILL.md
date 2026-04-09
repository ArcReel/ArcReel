---
name: generate-video
description: Generate video clips for script scenes. Use when the user says "generate video", "turn storyboards into video", wants to regenerate a video for a specific scene, or when video generation was interrupted and needs to resume. Supports full episode batch, single scene, and resume-from-checkpoint modes.
---

# Generate Video

Use the Veo 3.1 API to create videos for each scene/segment, using storyboard images as the starting frame.

> Aspect ratio, duration, and other specifications are determined by the project configuration and video model capability; the script handles this automatically.

## Command-Line Usage

```bash
# Standard mode: generate all pending scenes for the entire episode (recommended)
python .claude/skills/generate-video/scripts/generate_video.py episode_{N}.json --episode {N}

# Resume from checkpoint: continue from the last interruption
python .claude/skills/generate-video/scripts/generate_video.py episode_{N}.json --episode {N} --resume

# Single scene: for testing or regeneration
python .claude/skills/generate-video/scripts/generate_video.py episode_{N}.json --scene E1S1

# Batch selection: specify multiple scenes
python .claude/skills/generate-video/scripts/generate_video.py episode_{N}.json --scenes E1S01,E1S05,E1S10

# All pending
python .claude/skills/generate-video/scripts/generate_video.py episode_{N}.json --all
```

> All tasks are submitted to the generation queue at once; the Worker auto-schedules based on per-provider concurrency configuration.

## Workflow

1. **Load project and script** — confirm all scenes have `storyboard_image`
2. **Generate video** — the script auto-builds the prompt, calls the API, and saves checkpoints
3. **Review checkpoint** — display results; the user can regenerate unsatisfactory scenes
4. **Update script** — automatically update `video_clip` path and scene status

## Prompt Building

The prompt is automatically built internally by the script, using different strategies based on content_mode. The script reads the following fields from the script JSON:

**image_prompt** (for storyboard image reference): scene, composition (shot_type, lighting, ambiance)

**video_prompt** (for video generation): action, camera_motion, ambiance_audio, dialogue, narration (drama only)

- Narration mode: `novel_text` does not participate in video generation (post-production manual voiceover); `dialogue` includes only character dialogue from the original text
- Drama animation mode: includes complete dialogue, narration, and sound effects
- Negative prompt automatically excludes BGM

## Pre-Generation Checklist

- [ ] All scenes have approved storyboard images
- [ ] Dialogue text length is appropriate
- [ ] Action descriptions are clear and simple
