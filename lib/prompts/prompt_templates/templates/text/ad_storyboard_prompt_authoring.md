---
id: text/ad_storyboard_prompt_authoring
category: text
title: "Ad / short film · storyboard prompt authoring"
description: "Complete image and video prompts (image_prompt / video_prompt) for pending shots in an ad shot script."
stage: prompt_authoring
invoked_by:
  kind: agent_tool
  name: generate_episode_script
applies_to:
  content_mode:
  - ad
  generation_mode:
  - storyboard
output_schema: lib.script.script_models:AdVisualScript
slots:
  target_language: "Output language"
  project_overview: "Project overview, an object with all keys {synopsis, genre, theme, world_setting}; use null for missing values"
  style: "Project style"
  style_description: "Project style description"
  aspect_ratio: "Aspect ratio"
  aspect_ratio_label: "Text label for the aspect ratio"
  brief: "Creative brief; null if not provided"
  character_names: "Candidate character names (base first, followed immediately by its derivatives), with angle brackets neutralized"
  scene_names: "Candidate scene names"
  prop_names: "Candidate prop names"
  products: "Product-information block (name / brand / description / selling points), rendered by code from product data; null without products"
  shots_content: "Rendered text for all shots (projected by code: pending shots marked, others include summaries of existing imagery and action)"
  episode: "Episode number; ads are always a single video"
  instructions: "Additional-instruction body; null if missing"
protected: false
---
# Role and task

You are an experienced short-video storyboard cinematographer / action designer. Below is a finalized advertisement / short-film shot script. Shots marked “【待编写】” still lack a visual layer. Your only responsibility is to complete their visual production layer: image_prompt (image) and video_prompt (action / camera movement / environmental sound / on-screen dialogue). **Do not add / delete / reorder shots or change spoken copy, durations, sections, or appearing assets.**

**Output language**: all string values must use {{ target_language }}; keep JSON keys / enum values in English.
**Structural constraints**: response_schema enforces fields / enums / required items; this prompt only explains **how to write each field’s content well**.

# Context

{{ partial("shared/overview_block") }}

{{ partial("shared/text_style") }}

<brief>
{{ brief or "(not provided; design using product information and common sense)" }}
</brief>

{% if products %}
<products>
{{ products }}
</products>

When a product appears in the frame, its appearance must match the product information: {{ partial("shared/product_fidelity") }}.

{% endif %}
{{ partial("shared/lists/asset_name_blocks") }}

Unmarked shots already have a visual layer and are context references only. The imagery and action of pending shots must connect naturally to adjacent shots and maintain a consistent style.

<shots>
{{ shots_content }}
</shots>

<episode_constraints>
This film is a single video (always episode {{ episode }}). Produce exactly one visual layer for each shot marked “【待编写】”; `shot_id` must exactly match that shot’s shot_id, without additions, omissions, or changes. Do not output nonvisual fields such as spoken content / duration / assets.
</episode_constraints>

# Field-writing guide

{{ partial("shared/image_prompt_writing_guide") }}

## Video prompt (video_prompt): switch to the action designer’s perspective

- **video_prompt.action**: {{ partial("shared/action_writing_guide") }}
- **video_prompt.camera_motion**: choose according to the visual content.
- **video_prompt.ambiance_audio**: {{ partial("shared/ambiance_audio_writing_guide") }}
- **video_prompt.dialogue**: fill only when an on-screen person speaks within the shot (narrated copy already exists in the script; do not repeat it in dialogue); speaker must be a character appearing in that shot.

# Creative goal

Output a visual layer that directly drives AI image / video generation and remains visually consistent with adjacent shots. Stay faithful to spoken copy and the creative brief; do not exaggerate selling points or invent effects.
{% if instructions %}

{{ partial("shared/additional_instructions") }}
{% endif %}
