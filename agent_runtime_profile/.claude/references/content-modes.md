# Content Mode Reference

Switched via the `content_mode` field in `project.json`. Each skill's scripts automatically read and apply the corresponding spec — no need to specify aspect ratios in the prompt.

| Dimension | Narration + Visuals (narration, default) | Drama Animation (drama) |
|-----------|------------------------------------------|------------------------|
| Data structure | `segments` array | `scenes` array |
| Aspect ratio | Project config (default 9:16 portrait) | Project config (default 16:9 landscape) |
| Default duration | Project config (default 4 sec/segment) | Project config (default 8 sec/scene) |
| Duration options | Determined by video model capability | Determined by video model capability |
| Dialogue source | Post-production voiceover (novel text) | Actor dialogue |
| Video prompt | Character dialogue only (if any), no narration | Includes dialogue, narration, sound effects |
| Preprocessing agent | split-narration-segments | normalize-drama-script |

## Video Specifications

- **Resolution**: Images 1K, video 1080p
- **Generation**: Each segment/scene generated independently, storyboard image used as starting frame
- **Concatenation**: ffmpeg concatenates independent segments; Veo extend is not used to chain shots
- **BGM**: Automatically excluded via the `negative_prompt` API parameter; added in post-production with compose-video

## Veo 3.1 Extend Notes

- Only used to extend a **single** segment/scene (each call adds +7 seconds, max 148 seconds)
- **720p only** — 1080p cannot be extended
- Not suitable for chaining different shots

## Prompt Language

- Image/video generation prompts use **Chinese**
- Use narrative-style descriptions, not keyword lists

> Reference `docs/google-genai-docs/nano-banana.md` from line 365 for the Prompting guide and strategies.
