---
id: text/drama_script_plan
category: text
title: "Drama script planning"
description: "Split this episode’s source into structured drama shot content: boundaries, appearing assets, visual descriptions, verbatim spoken content, and source anchors."
stage: script_plan
invoked_by:
  kind: agent_tool
  name: generate_script_plan
applies_to:
  content_mode:
  - drama
  generation_mode:
  - storyboard
  source_kind:
  - novel
  - screenplay
output_schema: lib.script.script_models:DramaNormalizedScript
slots:
  source_kind: "Source kind"
  target_language: "Output language, taken from the project source language"
  project_overview: "Project overview, an object with all keys {synopsis, genre, theme, world_setting}; use null for missing values"
  style: "Project visual style"
  assets: "Registered assets, an object {characters, scenes, props}, each a [{name, aliases, appearance}] list (angle brackets neutralized; derivatives written as “base/derivative”)"
  character_names: "Referenceable character names; derivatives written as “base/derivative”"
  scene_names: "Referenceable scene names"
  prop_names: "Referenceable prop names"
  novel_text: "This episode’s source"
  episode: "Episode number"
  durations: "Duration tiers in seconds supported by the video model"
  max_duration: "Longest tier in seconds"
  default_duration: "Default seconds per shot; empty if not set"
  speech_rate: "Speaking rate (reading units per second)"
  speech_unit: "Reading-unit noun (characters or words)"
  episode_target_duration: "Target episode duration in seconds; empty if not set"
  episode_outline: "This episode’s outline (title, story_beats, hook, next_episode_teaser); empty if unplanned"
  next_episode_outline: "Next episode’s outline, same shape as this episode’s; empty if unplanned"
  instructions: "Additional instructions; empty if not provided"
protected: false
---
{{ variant("text/drama_script_plan/task", source_kind) }}

**Output language**: natural-language string values must use {{ target_language }}; keep JSON keys / enum values in English. {{ variant("text/drama_script_plan/language_rule", source_kind) }}
**Structural constraints**: response_schema enforces fields / enums / required items; this prompt only explains **how to write each field’s content well**.

{{ partial("shared/pacing/drama") }}

## Project information

<overview>
{{ project_overview.synopsis or "" }}

Genre: {{ project_overview.genre or "" }}
Core theme: {{ project_overview.theme or "" }}
World setting: {{ project_overview.world_setting or "" }}
</overview>

<style>
{{ style }}
</style>

{{ partial("shared/lists/asset_registry_blocks") }}

## Source text

<{{ source_kind }}>
{{ novel_text }}
</{{ source_kind }}>

{% if episode_outline %}
<episode_outline>
This episode’s outline (designed during episode planning; the script must cover every story beat):
{% if episode_outline.title %}
This episode’s title: {{ episode_outline.title }}
{% endif %}
{{ partial("shared/lists/episode_outline_lines", outline=episode_outline) }}
</episode_outline>

{% if episode_outline.hook or episode_outline.next_episode_teaser %}
The final scene (the last shot or shots) must actually depict the dramatic content of the episode-end hook in visuals and dialogue, leaving the suspense on screen. If a next-episode teaser exists, introduce it naturally through the closing visuals or dialogue; do not awkwardly insert narration labeled “next-episode preview”.

{% endif %}
{% endif %}
{% if next_episode_outline %}
<next_episode_outline>
Next episode’s outline (only for designing the connection at this episode’s end; do not bring next episode’s plot into this episode):
{% if next_episode_outline.title %}
Next episode’s title: {{ next_episode_outline.title }}
{% endif %}
{{ partial("shared/lists/episode_outline_lines", outline=next_episode_outline) }}
</next_episode_outline>

{% endif %}
# Field-writing guide

Split the source into ordered shots, producing structured content for each. You are currently generating episode {{ episode }}.

## Basic fields

- **scene_id**: format `E{{ episode }}S{two-digit sequence}` (such as E{{ episode }}S01), increasing in shot order; do not use another episode-number prefix.
- **duration_seconds**:
  - Tiers: {% if default_duration %}choose from supported durations ({{ durations }}) according to visual content: default {{ default_duration }} seconds; fights / large scenes / emotional buildup may use longer tiers up to {{ max_duration }} seconds. Do not default to the shortest tier{% else %}match visual complexity to an appropriate supported duration ({{ durations }}), up to {{ max_duration }} seconds, without enforcing a default{% endif %}.
  - Spoken-content lower bound: first estimate the seconds needed to speak this shot’s utterances (dialogue + off-screen speech), at approximately {{ speech_rate }} {{ speech_unit }}/second, then choose the closest tier **not below** that duration. This is a one-way lower bound; visuals / emotional pauses may justify a longer tier. Shots without utterances have no such lower bound. If the speech estimate exceeds the longest tier of {{ max_duration }} seconds, use the longest tier; do not cut dialogue.
{% if episode_target_duration %}
  - Episode target: {{ partial("shared/episode_target_duration_rule") }}.
{% endif %}
- **segment_break**: {{ variant("text/drama_script_plan/break_rule", source_kind) }}
- **characters_in_scene** / **scenes** / **props**: list assets actually appearing in this shot from these candidates.
  - Candidate characters: [{{ character_names | join(", ") or "(none yet)" }}]
  - Candidate scenes: [{{ scene_names | join(", ") or "(none yet)" }}]
  - Candidate props: [{{ prop_names | join(", ") or "(none yet)" }}]
  - For assets absent from the candidates, use the original source designation and list them in `new_assets` (see below); generic extras (such as “old man A” or “several villagers”) do not enter characters_in_scene.
- **scene_description**: {{ variant("text/drama_script_plan/scene_rule", source_kind) }}

## Verbatim content (the content source of truth; preserve unchanged once finalized)

- **source_text**: quote the corresponding source passage verbatim, staying as close to the source as possible. Prefer omission to invention; leave it empty if uncertain.
- **utterances**: {{ variant("text/drama_script_plan/utterances_rule", source_kind) }}

Each shot should be one independent visual image achievable within its specified duration; avoid multiple actions or visual cuts within one shot.

{{ partial("shared/new_assets_rule") }}
{% if instructions %}

{{ partial("shared/additional_instructions") }}
{% endif %}
