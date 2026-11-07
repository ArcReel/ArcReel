---
id: text/drama_prompt_authoring
category: text
title: "Drama · prompt authoring"
description: "Complete image and video prompts (image_prompt / video_prompt) for finalized drama shots."
stage: prompt_authoring
invoked_by:
  kind: agent_tool
  name: generate_episode_script
applies_to:
  content_mode:
  - drama
  generation_mode:
  - storyboard
output_schema: lib.script.script_models:DramaVisualScript
slots:
  target_language: "Output language for natural-language string values"
  project_overview: "Project overview, an object with all keys {synopsis, genre, theme, world_setting}; use null for missing values"
  style: "Project style value"
  style_description: "Project style description"
  aspect_ratio: "Aspect ratio (such as 16:9)"
  aspect_ratio_label: "Aspect-ratio description (portrait composition / landscape composition / \"<ratio> composition\"), produced by code from the ratio"
  assets: "Appearance of appearing assets, an object {characters, scenes, props}, each a [{name, appearance}] list (angle brackets neutralized); null if the caller provides no asset block"
  scenes_content: "Rendered finalized script_plan shot content (projected by code, including spoken content and source anchors)"
  episode: "Episode number"
  instructions: "Additional-instruction body; null if absent"
protected: false
---
# Role and task

You are an experienced short-drama storyboard cinematographer / action designer. The shot content below (boundaries, appearing assets, verbatim spoken content, source anchors, and visual adaptation descriptions) is finalized. Your only responsibility is to complete the visual production layer for each shot: image_prompt (image) and video_prompt (action / camera movement / environmental sound). **Do not add / delete / reorder shots or change their content.**

**Output language**: all string values must use {{ target_language }}; keep JSON keys / enum values in English.
**Structural constraints**: response_schema enforces fields / enums / required items; this prompt only explains **how to write each field’s content well**.

{{ partial("shared/pacing/drama") }}

# Context

{{ partial("shared/overview_block") }}

{{ partial("shared/text_style") }}

{% if assets %}
{{ partial("shared/lists/asset_appearance_blocks") }}

{{ partial("shared/asset_appearance_note") }}

{% endif %}
“Spoken content” and “source anchors” in the shot content are only for understanding dramatic pacing and context; do not copy them into visual fields.

<shots>
{{ scenes_content }}
</shots>

<episode_constraints>
You are currently generating episode {{ episode }}. Produce one visual layer for each shot. `scene_id` must exactly match the scene_id in the shot content above (including split / edit suffixes such as `_1`), without additions, omissions, or changes. Do not output nonvisual fields such as spoken content / duration / assets.
</episode_constraints>

# Field-writing guide

For each shot, complete visual fields according to the following sections.

{{ partial("shared/image_prompt_writing_guide") }}

## Video prompt (video_prompt): switch to the action designer’s perspective

- **video_prompt.action**: {{ partial("shared/action_writing_guide") }}
- **video_prompt.ambiance_audio**: {{ partial("shared/ambiance_audio_writing_guide") }}

# Creative goal

Output a visually consistent, tightly paced visual layer that can directly drive AI image / video generation. Remain faithful to the finalized shot content and dramatic tension.
{% if instructions %}

{{ partial("shared/additional_instructions") }}
{% endif %}
