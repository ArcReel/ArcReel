# Generation-mode reference

ArcReel separates “what content” from “how video is generated”. `content_mode` strictly expresses **content type** (narration / drama), while `generation_mode` expresses **video source / generation path** (storyboard / reference_video). Both are determined solely by top-level `project.json` fields, immutable after creation, with no episode overrides. Combinations are below; for reference video, content type affects secondary decisions such as ratio/default duration.

Grids are not another generation mode: `grid_storyboard` is an independent boolean effective only for `generation_mode="storyboard"` (toggled by users in settings, without Agent write permission), selecting single-image/grid storyboard assembly without affecting other step dispatch.

## Mode matrix

| generation_mode | content_mode | Main structure | Planning subagent | Intermediate script_plan | Script schema | Visual-reference source |
|---|---|---|---|---|---|---|
| `storyboard` | `narration` | `segments[]` | split-narration-segments | `script_plan_segments.json` | NarrationEpisodeScript | One storyboard first frame per shot (split grid panels when `grid_storyboard=true`) |
| `storyboard` | `drama` | `scenes[]` | normalize-drama-script | `script_plan_normalized_script.json` | DramaNormalizedScript (script_plan) → DramaVisualScript (prompt_authoring) → DramaEpisodeScript (merged) | One storyboard first frame per shot (split grid panels when `grid_storyboard=true`) |
| `reference_video` | `narration` / `drama` | `video_units[]` | split-reference-video-units | `script_plan_reference_units.json` | ReferenceVideoScript | Character / scene / prop sheets directly as `reference_images` |

> Drama has two stages (ADR 0041): script_plan (normalize-drama-script) produces **structured content** `script_plan_normalized_script.json` (boundaries / assets / verbatim utterances / source_text anchors / visual adaptation descriptions); prompt_authoring (create-episode-script) outputs only `DramaVisualScript` (scene_id + image_prompt + video_prompt), written by scene_id into formal `DramaEpisodeScript`. Confirmation converts the whole plan to formal content; later edits use formal `patch_episode_script`.
>
> Intermediates live under `drafts/episode_{N}/`. State and confirmation recognize **only the file matching current project generation_mode**: other `script_plan_*` files are historical residue, neither completion evidence nor substitute confirmation input. Legacy drama `script_plan_normalized_script.md` is not valid planning; rerun normalization to produce `.json`.

## The plan expresses step applicability

**This document does not duplicate a content/generation-mode step table.** Applicable steps, order, current stage,
and script-planning subagent all come from `mcp__arcreel__get_workflow_plan` `steps[]` and
`next_action` (`prepare_script_plan` `next_action.args.preprocessor` is the authoritative subagent name).
See [workflow-plan.md](workflow-plan.md).

The matrix explains only **data structures and schemas**: which main structure, intermediate file,
schema, and references the same step uses in each combination. Mode differences belong here, not in a skipped-step table.

Additional facts not expressed by the plan:

- `reference_video` **skips only storyboard images**. TTS projects can generate narration under both modes,
  but reference video has no segment-batch TTS entry (no `segments[]`).
- Queueing dispatches by project `generation_mode`; script skeleton is validation only. Mismatches, such as residual
  `video_units[]` in storyboard projects, reject queueing. Rerun planning/generation for the current project mode instead of expecting old scripts to execute.
- Formal scripts do not follow planning changes automatically: use formal `patch_episode_script` for content; rerun planning only for whole-episode redo, then reconfirm, overwriting existing formal content after overwrite agreement.

## Video specifications

- **Resolution**: images 1K, video 1080p
- **Shot duration** (storyboard, including grid_storyboard): must belong to model `supported_durations`; non-null project `default_duration` is the default (written to project.json by content_mode at creation), while null lets planning choose by pacing
- **Episode target duration** (non-ad): non-null `episode_target_duration` guides shot/unit count; unless `episode_target_units` is explicit, planning also converts it to source volume via speaking rate. Both are soft: fewer units for insufficient content, overruns for necessary content, warnings without blocking
- **Reference-video unit duration**: one unit is one generation call and duration, chosen from effective tiers **matching its reference state** (`get_video_capabilities` `reference_unit_durations.with_references` / `.without_references`; some models restrict image-backed duration). Resplit content that cannot fit; never violate duration. Subagents query `mcp__arcreel__get_video_capabilities` at execution; **do not freeze values here**. Existing units follow server `reference_unit_durations.units[unit_id]`, based on currently available images; registered assets without sheets are not image-backed and appear in `problems` / `unavailable_references`
- **Editing/final cuts**: all modes join/render via `edit-video` timelines; Veo extend is only **single-clip extension**, not joining shots
- **BGM**: generation appends `Avoid: BGM、文字字幕、水印` to video prompts automatically; do not append manually or describe BGM / scores

## Prompt language

- Image/video prompts use **Chinese**
- Use narrative descriptions, not keyword lists
- Reference video additionally uses `@[角色]/@[场景]/@[道具]` to reference assets; **never** describe appearance, clothing, or scene details supplied by references

## Directory differences

> This tree only illustrates project layout; session cwd is already `projects/{name}/`. **Use cwd-relative paths in tool calls** (such as `videos/`, `reference_videos/`), never the `projects/{name}/` prefix.

```text
projects/{name}/          # ← session cwd is here
├── storyboards/          # Storyboard images (split grid first frames when grid_storyboard=true)
├── grids/                # Composite grids for storyboard + grid_storyboard=true
├── reference_videos/     # Reference-video artifacts
├── videos/               # Storyboard-video artifacts
└── audio/                # Narration audio (narration/commentary only, created on first generation)
```

> See prompting strategies in the [official Gemini image-generation guide](https://ai.google.dev/gemini-api/docs/image-generation).
