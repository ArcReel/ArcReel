---
name: manage-project
description: Project management tools. Use to add/update characters/scenes/props in project.json (patch_project upserts by table+name), cascade-rename assets (rename_asset), merge duplicate registrations of the same identity (merge_asset), write top-level settings, edit project overview, and query video model capabilities (get_video_capabilities). Episode planning is outside this skill; use server tools mcp__arcreel__plan_episodes / reset_episode_planning.
user-invocable: false
---

# Project management tools

Provides batch character/scene/prop writes in project.json, project-level settings and overview editing, and video model capability queries.

## Tool overview

| Tool | Function | Caller |
|------|------|--------|
| `mcp__arcreel__patch_project` (SDK tool) | Add/update project.json characters/scenes/props (upsert by table+name), top-level settings, or project overview (overview branch) | Subagent / main Agent |
| `mcp__arcreel__rename_asset` (SDK tool) | Cascade-rename an asset: update asset-table keys, name references in all episode scripts and script_plan drafts (reference arrays / speaker / `@[名称]` mentions), associated files, and version history together | Subagent / main Agent |
| `mcp__arcreel__merge_asset` (SDK tool) | Merge two same-table assets registered for one identity: redirect references to the survivor, retain the merged name as an alias, or merge as a derivative of the surviving character | Main Agent |
| `mcp__arcreel__get_video_capabilities` (SDK tool) | Query video model capabilities (model granularity, resolved by the project's unique generation_mode, consistent project-wide, no episode required) | **Subagent** (query independently while executing tasks) |

> Server tools `mcp__arcreel__plan_episodes` / `mcp__arcreel__reset_episode_planning` handle episode planning (splitting/adjusting). Adjust existing plans through “reset + replan”; see video-workflow stage 2.

## Write characters/scenes/props

Write through `mcp__arcreel__patch_project` (the session binds the project name; no parameter needed). Call separately for each table;
each entry upserts by name: add absent names, merge changed fields for existing names. **Revising existing asset descriptions requires explicit user
intent** (avoid silently overwriting manually edited fields). Script planning discovers this episode's new assets and registers them at content confirmation; no advance registration here is needed.
`aliases` is a string list used only for identity recognition in script planning; aliases must not appear in references.

```text
mcp__arcreel__patch_project({"table": "characters", "entries": {"角色名": {"description": "...", "voice_style": "..."}}})
mcp__arcreel__patch_project({"table": "characters", "entries": {"角色名": {"derivatives": {"战斗装": {"description": "换上黑色重甲，其余外观保持不变"}}}}})
mcp__arcreel__patch_project({"table": "scenes", "entries": {"场景名": {"description": "..."}}})
mcp__arcreel__patch_project({"table": "props", "entries": {"道具名": {"description": "..."}}})
mcp__arcreel__patch_project({"settings": {"episode_target_units": 1000}})
mcp__arcreel__patch_project({"settings": {"episode_target_duration": 90}})
mcp__arcreel__patch_project({"settings": {"source_language": "en"}})
mcp__arcreel__patch_project({"settings": {"narration_voice": "Ethan", "narration_speed": 1.2}})
mcp__arcreel__patch_project({"overview": {"genre": "悬疑", "theme": "复仇与救赎"}})
```

A character entry's `derivatives` table registers another appearance of that character beyond the base (persisting across scenes and representable through image generation),
referenced in scripts as `@[角色/衍生]`. It merges by derivative name: update descriptions for existing names, add new names, and preserve unmentioned derivatives unchanged (including their asset images).
Write only `description` for derivatives: changes relative to the base, worded as image-edit instructions; the generation pipeline writes asset images back.
Rename a derivative through the derivative subresource rename endpoint (the character card's “Derivatives” overlay in Web); writing a new name with `patch_project` only creates another entry.

**Choose exactly one of three call forms**: `{"table", "entries"}` for asset upsert, `{"settings"}` for top-level fields,
or `{"overview"}` for overview editing; multiple forms or none are rejected. `settings` field allowlist:

- `episode_target_units`: set with `int >= 1` / clear with `null`. Target episode size (reading units interpreted by `source_language`); episode planning uses it to determine split size. If unset, the tool converts `episode_target_duration` to an equivalent size using speech rate; no need to ask the user for a word/character count just to control size
- `episode_target_duration`: set integer seconds `10`–`600` / clear with `null`. Target final episode duration; script planning uses it to determine the number of shots/video units. Without `episode_target_units`, episode planning also converts it to source-text size (explicit `episode_target_units` takes precedence). A soft target (content needs can override it; excess only warns, never blocks), writable only for non-ad/short-film projects; ad writes are rejected (whole-episode size already uses the `target_duration` budget)
- `source_language`: set `"zh" / "en" / "vi"` / clear with `null`. Priority: **explicit user configuration > automatic inference**. Write when the user explicitly specifies a language (not limited to skipped/failed overview generation); without explicit user confirmation, do not guess and write it. The normal path persists it automatically through overview generation. If explicit configuration conflicts with inference / actual source language, warn the user (WARN) and continue using explicit configuration without blocking
- `brief`: set a string / clear with `null`. Short creative-intent text, writable only for ad/short-film projects (`content_mode=ad`); other project types reject writes
- `narration_voice`: set a nonempty string (voice ID per provider documentation) / clear with `null`. Narration voice in the project's TTS snapshot; affects this project only. TTS projects require a voice and reject clearing it
- `narration_speed`: set a positive finite number (such as `1.2`) / clear with `null`. Speech rate in the project's TTS snapshot; affects this project only. Clearing means no speech-rate parameter is sent to the provider
- `character_voice_binding`: set `"prompt" / "reference_audio"` / clear with `null` to default (`prompt`). Determines character-voice constraints: `prompt` places character `voice_style` in prompts as a soft constraint; only `reference_audio` attaches configured character reference audio to video-model requests for native voice consistency. Native consistency requires both this setting to be `reference_audio` and the character to have reference audio

`generation_mode` and `grid_storyboard` are not allowlisted; `patch_project` rejects them: `generation_mode` cannot change after project creation. If the user requests a generation-method change (storyboard ↔ reference_video), clearly explain it is immutable and has no workaround. Users toggle `grid_storyboard` in Web settings; direct grid-assembly change requests there and explain the switch affects only future generation. Existing storyboard images do not automatically become stale; explicitly regenerate corresponding storyboards to use the new assembly method.

`overview` allowlists `synopsis` / `genre` / `theme` / `world_setting`, with **merge semantics** (change only supplied fields;
create an overview if absent). **Revising the overview requires explicit user intent** (avoid silently overwriting manually edited fields).

Tool results distinguish **N added / N field-merged** and explicitly list ignored fields (``reference_image`` /
``character_sheet`` and other system-managed fields, deprecated ``type`` / ``importance``). Invalid structure (such as missing
description) is not persisted and returns `is_error: true`.
**Never** modify `project.json` directly with Write/Edit/Bash: use patch_project for fields, rename_asset for asset names,
and merge_asset to combine assets. `patch_project` upserts by name; using it to rename only creates a new name while leaving the old entry and updates no references.

## Merge duplicate asset registrations

When one person, location, or object has two same-table registrations (such as “Old Wang” and “Wang Jianguo”), use `mcp__arcreel__merge_asset` to merge
`source` into `target`. Merging is irreversible; proceed in this order:

1. Confirm with the user which asset survives as `target` and the merge method: merge into the base for the same appearance (default); if `source` is another appearance of `target`
   (such as a black-clad figure who is the protagonist in disguise), pass `as_derivative: true`, supported only for characters.
2. Call with `dry_run: true` and relay per-episode reference rewrite counts, storyboard/video counts that will become stale, and discarded `source` content
   (description, asset image and version history, voice settings, originals, reference audio). If `source` descriptions contain information needed by `target`,
   also ask whether to first add it to `target` using `patch_project`.
3. After user consent, call again without `dry_run` to execute.

## Query video model capabilities

Query through the MCP tool (the session binds the project name; no parameter needed):

```text
mcp__arcreel__get_video_capabilities({})
```

The project uniquely determines generation mode, with no episode-level override; capability queries are consistent project-wide and neither accept nor require `episode_id`.

**Returns**: JSON text containing `provider_id` / `model` / `supported_durations[]` / `max_duration` / `max_reference_images` / `source` / `default_duration` / `episode_target_duration` / `content_mode` / `generation_mode`; narration / drama reference-to-video projects also include `reference_unit_durations` (effective `with_references` / `without_references` tiers, selected by whether a unit has `@` references; `units`, keyed by `unit_id`, gives each existing formal unit's server-determined available-reference bucket `hydrated_capability`, effective `allowed_durations`, factual failure `problem`, and `problems` / `unavailable_references` when declared references differ from available images; use these as authoritative for existing units' buckets and tiers). **Ad projects do not return this field**: ad machine field `unit` is a lightweight index derived from `shots[]`; shot durations are not governed by tier enums (see `video-workflow/SKILL.ad.md`). Do not wait for this field or rearrange ad shot durations to fit tiers.

**Use**: script-planning subagents for all generation_mode values (storyboard / reference_video) query independently during execution to determine individual shot/unit durations. **Decision priority** (highest first): hard constraints (storyboard shot duration must come from `supported_durations`; narration / drama reference_video unit duration must come from the `reference_unit_durations` tier matching that unit's reference state; ad shot duration follows free-integer rules in `SKILL.ad.md`) > `episode_target_duration` / `default_duration` preferences (the former is a soft target for this episode's summed unit durations and determines how many units to split into; the latter, when non-null, is the default per unit) > content needs (reference_video chooses a tier matching that unit's actual content length; narration / drama long sentences or complex visuals can use longer values). Resplit units when content does not fit; do not violate duration constraints.

**Errors**: if the project is absent or model capabilities cannot be resolved, returns `is_error: true` with the reason in text.
