---
id: text/reference_video_script_plan
category: text
title: "Reference video · unit splitting"
description: "Split the source into a reference-video unit table, each unit with duration, source anchor, and reference-syntax body."
stage: script_plan
invoked_by:
  kind: agent_tool
  name: generate_script_plan
applies_to:
  content_mode: [drama, narration]
  generation_mode: [reference_video]
  source_kind: [novel, screenplay]
output_schema: lib.script.script_models:ReferenceScriptPlanFlatDraft
slots:
  source_kind: "Source kind; only drama uses screenplay"
  target_language: "Output language"
  project_overview: "Project overview, an object with all keys {synopsis, genre, theme, world_setting}; use null for missing values"
  assets: "Registered assets, an object {characters, scenes, props}, each a [{name, aliases, appearance}] list (angle brackets neutralized; derivatives written as “base/derivative”)"
  character_names: "Candidate referenceable character names (including base/derivative)"
  scene_names: "Candidate referenceable scene names"
  prop_names: "Candidate referenceable prop names"
  novel_text: "Verbatim source text"
  episode_outline: "This episode’s outline, an object with all keys {title, story_beats, hook, next_episode_teaser}; null without valid content"
  next_episode_outline: "Next episode’s outline, same shape as above; null without valid content"
  episode: "Current episode number"
  durations: "Sorted, deduplicated valid duration tiers in seconds, formatted as text"
  duration_tiers: "Two reference-image/duration tier sets, an object {with_references, without_references, default_with_references_only, default_without_references_only}; tiers formatted as text, with the last two fields indicating that default seconds belong to only one set; null when sets are identical or the model declares none"
  default_duration: "Preferred default seconds; null if not set"
  speech_rate: "Speaking rate (reading units / second), formatted as text"
  speech_unit: "Reading-unit noun (characters / words)"
  max_duration: "Maximum duration per generation call (seconds)"
  max_reference_images: "Reference-image limit per video unit; null if unlimited"
  episode_target_duration: "Target episode seconds; null if not set"
  instructions: "Additional-instruction body; null if missing"
protected: false
---
# Role and task

{{ variant("text/reference_video_script_plan/task", source_kind) }}
Each video_unit corresponds to **one video-generation call**; its body is a continuous visual description fully covered by that generation.
This stage determines the **structure and content contract**: unit boundaries, duration (the billing unit), dialogue placement, and core asset identification. The user reviews and confirms this contract unit by unit.
The subsequent prompt_authoring stage generates visual staging (shot size / composition / expanded camera movement) based on your divisions; do not write it at this stage.

**Output language**: all string values must use {{ target_language }}; keep JSON keys in English.
Exceptions (preserve verbatim, do not translate): asset names in `@[名称]` must exactly match registered names in the candidate list below or designations in `new_assets`; copy `source_text` verbatim from the source.
**Structural constraints**: response_schema enforces fields / enums / required items; this prompt only explains **how to write each field’s content well**.

# Context

{{ partial("shared/overview_block") }}

{{ partial("shared/lists/asset_registry_blocks") }}

{{ variant("text/reference_video_script_plan/source_heading", source_kind) }}

<{{ source_kind }}>
{{ novel_text }}
</{{ source_kind }}>

{% if episode_outline %}
<episode_outline>
{% if episode_outline.title %}
Title: {{ episode_outline.title }}
{% endif %}
{{ partial("shared/lists/episode_outline_lines", outline=episode_outline) }}
</episode_outline>

{% endif %}
{% if next_episode_outline %}
<next_episode_outline>
{% if next_episode_outline.title %}
Title: {{ next_episode_outline.title }}
{% endif %}
{{ partial("shared/lists/episode_outline_lines", outline=next_episode_outline) }}
</next_episode_outline>

{% endif %}
# Splitting rules

You are currently generating episode {{ episode }}. Cover all source plot content, producing units in narrative order.

- **Unit boundaries**: {{ variant("text/reference_video_script_plan/unit_rule", source_kind) }}
- **source_text**: the source passage underlying this unit, **copied verbatim** (you may truncate its beginning or ending, but must not remove words from the middle, rewrite, translate, or summarize).
  This is the traceability anchor linking generated content back to the source; nonverbatim copying is mechanically rejected.
- **Duration decision order** (top to bottom: higher priorities are hard boundaries; optimize lower priorities within them):
  1. Hard constraint: `duration_seconds` is the unit’s duration (one duration per generation call), and must be one of the supported tiers ({{ durations }}).
     If the required narrative duration does not fit, split the unit into multiple units in narrative order; **never violate the duration contract**.
{% if duration_tiers %}
     This model also has two sets of tiers depending on whether reference images are present. Choose according to whether the unit’s **visual description contains `@` asset references** (speaker positions in dialogue tokens `@[角色]{台词}` do not count: they generate no reference image and only drive voice declarations): use ({{ duration_tiers.with_references }}) with `@` references and ({{ duration_tiers.without_references }}) without them. The server determines the tier using the reference images **currently available** for that unit: when referenced assets have no generated reference images yet, the unit uses the tiers without references. Choose one approach: either select a value from the tier set matching the unit’s reference state, or adjust the references by incorporating secondary assets into descriptive text without `@`, thereby using the no-reference tiers.
{% endif %}
  2. Dialogue lower bound: first estimate the seconds needed to speak all dialogue and off-screen speech in the unit (approximately {{ speech_rate }} {{ speech_unit }}/second),
     then select a tier **not below** that duration. This is a one-way lower bound: never squeeze dialogue into a tier too short to finish speaking. Units without dialogue have no such lower bound.
     If dialogue exceeds the longest tier ({{ max_duration }} seconds), split the unit; do not force all the dialogue into one unit.
  3. Default preference: {% if default_duration %}default to {{ default_duration }} seconds per unit{% if duration_tiers and duration_tiers.default_with_references_only %} (this default exists only in the tiers for units with `@` references; units in the other state follow the hard constraint above){% endif %}{% if duration_tiers and duration_tiers.default_without_references_only %} (this default exists only in the tiers for units without `@` references; units in the other state follow the hard constraint above){% endif %}; longer narrative needs may use longer tiers (content needs may override preferences, but not hard constraints){% else %}choose a tier according to narrative needs, without enforcing a default{% endif %}.
  4. Packing efficiency: organize body content within rules 1-3; {% if episode_target_duration %}pack toward this episode’s target duration: {{ partial("shared/episode_target_duration_rule") }}. Individual units may be shorter or longer within that target, need not approach the per-call limit of {{ max_duration }} seconds, and{% else %}bring unit durations close to {{ max_duration }} seconds;{% endif %} do not default to the shortest / conservative value.
{% if max_reference_images %}
- **Reference limit**: a unit’s **visual description** may `@` reference at most {{ max_reference_images }} distinct asset names (speaker positions in dialogue tokens `@[角色]{台词}` do not count: they generate no reference image). If exceeded, incorporate secondary characters into background descriptions without `@` references; do not compress the main assets.
{% endif %}

# Body-writing syntax

{{ partial("shared/writing_syntax") }}

# Body-writing guide for this stage

- Visual descriptions focus on **visible action** in the present moment: who does what, object interactions, and environmental dynamics. Verbs describe physically observable actions
  (reach / turn / push a door / cast toward), avoiding mental verbs such as “sink into / remember / realize / decide”.
- Copy asset names exactly from the candidates below; use original source designations for assets absent from the candidates and list them in `new_assets` (see below). The same applies to speakers in dialogue tokens:
  - character: {{ partial("shared/asset_name_candidates", names=character_names) }}
  - scene: {{ partial("shared/asset_name_candidates", names=scene_names) }}
  - prop: {{ partial("shared/asset_name_candidates", names=prop_names) }}
{{ variant("text/reference_video_script_plan/dialogue_rule", source_kind) }}
- Each unit’s body must `@` reference the scene asset for its location. When a new unit changes location, change to the scene for the new location;
  consecutive units at the same location repeat that scene reference in every unit (only describe a location in text when no candidate scene matches it).
- Do not write shot size / composition / camera movement at this stage (prompt_authoring supplies them); clearly describe the narrative content and action process.

{{ partial("shared/new_assets_rule") }}
{% if instructions %}

{{ partial("shared/additional_instructions") }}
{% endif %}
