---
name: generate-script
description: "Prompt authoring: call the project-configured text model to fill image_prompt/video_prompt for pending formal-script shots/units (reference video rewrites unit bodies); generate a full ad script when none exists. Called by create-episode-script, using the formal script itself and project.json."
user-invocable: false
---

# generate-script

Call the project-configured text-generation model (Gemini / Ark / OpenAI / custom provider, determined by project.json),
to complete the visual layer for pending formal-script entries. Script `image_prompt` / `video_prompt`
are the seeds of later image/video generation; **prompt quality largely determines visual quality**, making this skill
one of the most valuable optimization points in the ArcReel pipeline.

## Prerequisites

1. Project `project.json` exists, with style / overview / characters / scenes / props
2. **Formal script `scripts/episode_N.json` exists** (N is the target episode’s internal ID from plan `target.episode`, not playback position) for drama / narration / reference_video: content confirmation converts the whole plan into the formal script, all entries pending. Two equivalent confirmation paths: the user confirms in Web, or explicitly agrees in conversation and the main Agent calls `mcp__arcreel__confirm_script_review({"episode_id": N})`. This tool reads only formal content, not script planning: missing planning or an unconfirmed rerun does not prevent authoring.
   - **ad (ad / short film)**: without a formal script, generate it completely from `project.json` `brief` + `products` (including selling_points) + `target_duration` (the backend builds approved eight-section allocations, routing empty `products` to general shorts); at least one of `brief` and `products` is required. Output becomes formal without confirmation; the model lists new characters/scenes/props in `new_assets` with handling decisions, registering pending assets with the formal script and automatically merging same-type, same-name existing assets. The response lists new registrations. Existing formal scripts fill pending entries like other paths; full redo is below.
3. **Retain contract-failed output as a repairable draft, without discarding and drawing again**: reference-video prompt-authoring violations leave formal content untouched, saving output plus per-violation reports to `*.invalid.json`. Read with `open_draft` for content/revision, repair full `content` using `violations[]`, submit through `patch_draft`, then `promote_draft` with the same `episode_id` and `doc_type: reference_prompt_authoring`. Repeat open → patch → promote while violations remain, without a round limit.

## Usage

Call MCP tools; the session binds the project name, so no project argument is needed:

```text
mcp__arcreel__generate_episode_script({"episode_id": N})
mcp__arcreel__generate_episode_script({"episode_id": N, "instructions": "<附加指令原文，可选，无则省略>"})
mcp__arcreel__generate_episode_script({"episode_id": N, "dry_run": true})   # Preview prompt only
```

The tool fixes output at `{project}/scripts/episode_{N}.json`, without custom paths;
rename/archive through Web.

### Fill prompts (`author_prompts`)

Pending formal-script entries (all content-confirmed entries and manually added shots/units) lack visual layers.
When plan `next_action.type` is `author_prompts`, `requested_ids` identifies them.
Omit `entry_ids` to author all pending entries; successful writeback clears markers:

```text
mcp__arcreel__generate_episode_script({"episode_id": N})
```

- **Default gap filling**: assess image and video prompts separately as complete values, preserving existing ones and filling only the missing one; reference video expands bodies by pending
  markers. Completed entries, including handwritten ones, remain unchanged.
- **`entry_ids` defines the selection range only**: named entries still fill gaps; complete visual layers are skipped and listed in the response.
- **`rewrite: true` explicitly rewrites** the whole visual layer within that selection; use only when users explicitly request those overwrites. Content fields
  (dialogue, narration body, source), notes, and generated artifacts stay unchanged. Before overwriting, the tool returns
  `prompt_overwrite_required`, a server-generated loss list, and acceptance token `params.prompt_overwrite.revision`:
  relay the original list, obtain agreement, then retry with that revision as `overwrite_revision`.
- With no entries to author, the tool calls no model and changes no script, explaining this in the response. Whole-episode redo reruns planning and reconfirms.
- Before reference-video model calls, reassess pending formal-script bodies/dialogue volume. Failures use
  `prompt_authoring_dialogue_overload` (speech cannot fit) or `prompt_authoring_unit_invalid`, with `action` `fix_input`;
  `params.position` is this unit’s episode position, called “video unit N” in UI. Use that name when relaying the reason and ask users to edit
  body/duration in the timeline, rather than retrying unchanged.

### Full ad / short-film redo (`regenerate`)

For explicit full-ad regeneration, pass `regenerate: true` without deleting formal content first:

```text
mcp__arcreel__generate_episode_script({"episode_id": N, "regenerate": true, "instructions": "<可选>"})
```

- Existing formal content first returns `script_overwrite_required`, a loss list (removed entries and associated storyboard/video media),
  and token `params.script_overwrite.revision`: relay the original list and obtain agreement before retrying with
  `regenerate: true` + `overwrite_revision`.
- Previously registered assets remain after redo, even if unreferenced by the new script.
- Full-generation violations (unregistered names absent from `new_assets`, unresolvable decisions, invalid structure) fail the task,
  code `ad_script_rejected`, leaving formal script, assets, and drafts unchanged. Relay the cause, add a targeted instruction, and generate again.

**Important: use the MCP tools above for scripts. This skill provides no Python/Shell scripts; never call `python .../scripts/*.py` through Bash.**

## Generation flow

The MCP tool uses `ScriptGenerator` for these steps:

1. **Load project.json**: content_mode, characters, scenes, props, overview, style
2. **Load formal script**: select selected entries/fields (all pending or `entry_ids`) for gap filling/explicit rewrite; full ad generation when absent or `regenerate`
3. **Build prompt**: `lib.prompts.prompt_builders_script`, `lib.prompts.prompt_builders_reference`, or `lib.prompts.prompt_builders_ad`, using those entries’ content fields
4. **Call TextBackend**: `TextGenerator` selects the configured model, with Pydantic `response_schema` enforcing JSON structure
5. **Pydantic validation/writeback**: LLM produces only visuals, written to formal content by entry ID; content fields never enter output, preventing Structured Outputs drift:
   - narration → `NarrationVisualEpisodeScript` (`segment_id` + image_prompt + video_prompt; no dialogue in video_prompt)
   - drama (storyboard, including grid_storyboard) → `DramaVisualScript` (`scene_id` + image_prompt + video_prompt)
   - ad storyboard → `AdVisualScript` (`shot_id` + image_prompt + video_prompt); full ad → `AdEpisodeScript` plus `new_assets` (storyboard), or `AdReferenceFlatScript` (reference_video); decisions rewrite references and register new assets in the same write as formal content
   - reference_video → `ReferencePromptAuthoringFlatScript`: one rewritten body per pending unit in order, preserving dialogue verbatim
6. **Add metadata**: `episode` (internal ID), `content_mode`, `novel` (project title + episode title, falling back to “第 {播出位置} 集”), timestamps. SkipJsonSchema hides these from LLM, with backend injection from `project.json` preventing hallucination affecting consumers such as Jianying drafts.
   - **No script skeleton writes top-level `generation_mode`**. Mode is a creation-locked project fact (`project.json` `generation_mode`), reflected by skeleton kind; consumers always dispatch from `project.json`, never look for it in scripts.

## Output format

Generated JSON is saved to `scripts/episode_N.json`:

- `title`: episode title authored by LLM
- `episode` / `content_mode` / `novel` (title, chapter): backend `_add_metadata`, independent of LLM output
- Narration: `segments[]` (novel_text, duration_seconds, segment_break, characters/scenes/props transferred from planning at confirmation, then edited in formal content; image_prompt/video_prompt supplied by prompt_authoring)
- Drama: `scenes[]` (image_prompt, video_prompt, duration_seconds, plus confirmed utterances/source_text/characters_in_scene, etc.)
- Ad / short film: `shots[]` (section, voiceover_text, products_in_shot, image_prompt, video_prompt, duration_seconds, etc.); deviation above `target_duration` threshold only logs, without blocking saves
- Reference video: `video_units[]` (each with `text`, `duration_seconds`, etc.)
- `metadata`: created_at, updated_at, generator

Entry count and total duration are not persisted: script reads derive them and project summaries compute them on read; a stored copy would drift from the body.

## `--dry-run` output

Print the full text-model prompt without API calls or file writes, for quality/length inspection.

> See `.claude/references/generation-modes.md` for narration/drama paths, planning subagents, and schemas across storyboard/reference_video; ad paths are in `CLAUDE.ad.md`.
