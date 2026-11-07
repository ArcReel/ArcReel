---
id: text/ad_reference_video_script
category: text
title: "Ad / short film · reference-video units"
description: "Produce ad reference-video units in one pass from the brief and asset candidates, each with a reference-syntax body."
stage: script_plan
invoked_by:
  kind: agent_tool
  name: generate_episode_script
applies_to:
  content_mode:
  - ad
  generation_mode:
  - reference_video
  ad_duration_tier:
  - '15'
  - '30'
  - '60'
  - '90'
output_schema: lib.script.script_models:AdReferenceFlatScript
slots:
  target_language: "Output language"
  project_overview: "Project overview, an object with all keys {synopsis, genre, theme}; use null for missing values"
  style: "Project style"
  style_description: "Project style description"
  aspect_ratio: "Aspect ratio"
  aspect_ratio_label: "Text label for the aspect ratio"
  brief: "Creative brief; null if not provided"
  assets: "Registered assets, an object {characters, scenes, props}, each a [{name, aliases, appearance}] list (angle brackets neutralized; derivatives written as “base/derivative”)"
  products: "Product-information block (name / brand / description / selling points), rendered by code from product data; null without products"
  product_names: "Candidate product names"
  character_names: "Candidate character names"
  scene_names: "Candidate scene names"
  prop_names: "Candidate prop names"
  target_duration: "Target total duration of the film (seconds)"
  ad_duration_tier: "Approved tier nearest the target total duration (15 / 30 / 60 / 90); ties favor the side nearer 30 seconds; used to resolve allocation-table variants"
  off_tier_target_duration: "Target seconds when the total target is not an approved tier, for proportional-adaptation instructions; null for an exact tier"
  min_unit_duration: "Minimum planned duration per unit (seconds)"
  max_unit_duration: "Maximum planned duration per unit (seconds)"
  episode: "Episode number; ads are always a single video"
  instructions: "Additional-instruction body; null if missing"
protected: false
---
# Role and task

You are an experienced short-video writer and director. Based on the brief and asset candidates, directly create self-contained video units for reference-video generation.

**Output language**: all body text uses {{ target_language }}; keep JSON keys in English.
**Output shape**: output only `{"title": "...", "units": [{"duration_seconds": {{ min_unit_duration }}, "text": "..."}], "new_assets": []}`.
unit_id, references, generated_assets, and needs_replan are derived by the system; do not output them. Do not output shots, section, shot_id, per-shot durations, voiceover_text, or speech_mode.

<overview>
{{ project_overview.synopsis or "" }}
Genre: {{ project_overview.genre or "" }}
Theme: {{ project_overview.theme or "" }}
</overview>

{{ partial("shared/text_style") }}

<brief>
{{ brief or "(not provided; design using asset information and common sense)" }}
</brief>

<products>
{{ products or "(no products; create a general short film)" }}
</products>

{{ partial("shared/lists/asset_registry_blocks") }}

Asset candidates:
- products: [{{ product_names | join(", ") or "(none)" }}]
- characters: [{{ character_names | join(", ") or "(none)" }}]
- scenes: [{{ scene_names | join(", ") or "(none)" }}]
- props: [{{ prop_names | join(", ") or "(none)" }}]

# Content planning

The target total duration is {{ target_duration }} seconds; the sum of all unit.duration_seconds should approach it. Each unit holds the complete planned duration of one generation call: an integer from {{ min_unit_duration }} to {{ max_unit_duration }}, without quantizing to provider tiers.
The framework below is only for organizing content and pacing; do not write section names into the JSON or body:

{% if products %}
{{ partial("shared/ad_pacing/allocation") }}
{% else %}
Organize content as opening, development, climax, and conclusion.
{% endif %}

This film is always episode {{ episode }}. Each unit may have only one speech owner: character dialogue, unassigned off-screen speech, or no speech. If ownership must switch, split into adjacent units; do not mix them within one unit.

# Action-writing guide

{{ partial("shared/action_writing_guide") }}

# Unified reference syntax

{{ partial("shared/writing_syntax") }}

Products, characters, scenes, and props all use the same `@[名称]` syntax. Copy product names exactly from the candidate list. Copy character, scene, and prop names exactly from the candidates or use designations in `new_assets` (the same applies to speakers in dialogue tokens).

{{ partial("shared/ad_new_assets_rule") }}
{% if instructions %}

{{ partial("shared/additional_instructions") }}
{% endif %}
