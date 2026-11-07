---
id: text/ad_reference_video_prompt_authoring
category: text
title: "Ad / short film · reference-video unit authoring"
description: "Write reference-syntax bodies for pending units in an ad reference-video script."
stage: prompt_authoring
invoked_by:
  kind: agent_tool
  name: generate_episode_script
applies_to:
  content_mode:
  - ad
  generation_mode:
  - reference_video
output_schema: lib.script.script_models:ReferencePromptAuthoringFlatScript
slots:
  target_language: "Output language"
  project_overview: "Project overview, an object with all keys {synopsis, genre, theme}; use null for missing values"
  style: "Project style"
  style_description: "Project style description"
  aspect_ratio: "Aspect ratio"
  aspect_ratio_label: "Text label for the aspect ratio"
  brief: "Creative brief; null if not provided"
  products: "Product-information block (name / brand / description / selling points), rendered by code from product data; null without products"
  product_names: "Candidate product names"
  character_names: "Candidate character names"
  scene_names: "Candidate scene names"
  prop_names: "Candidate prop names"
  units_content: "Rendered text for all video units (projected by code: sequence number + pending marker + duration + body)"
  pending_count: "Pending unit count"
  episode: "Episode number; ads are always a single video"
  instructions: "Additional-instruction body; null if missing"
protected: false
---
# Role and task

You are an experienced short-video writer and director. Below are existing reference-video units for an advertisement / short film. Write bodies for units marked “【待编写】”; the other units are finalized and provide context only.

**Output language**: all body text uses {{ target_language }}; keep JSON keys in English.
**Output shape**: output only `{"title": "...", "units": [{"text": "..."}]}`, with exactly {{ pending_count }} `units`, corresponding one-to-one to pending units in their order in the film.
The system retains or derives unit duration, unit_id, references, generated_assets, and needs_replan; do not output them. `title` is not written back to the script; simply supply a short title.

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

Asset candidates:
- products: [{{ product_names | join(", ") or "(none)" }}]
- characters: [{{ character_names | join(", ") or "(none)" }}]
- scenes: [{{ scene_names | join(", ") or "(none)" }}]
- props: [{{ prop_names | join(", ") or "(none)" }}]

<units>
{{ units_content }}
</units>

# Writing requirements

- If a pending unit already has body text, complete its visual description based on that text: preserve dialogue and off-screen speech tokens verbatim, without adding, deleting, or changing words.
- If a pending unit’s body is empty, write it using the brief, product information, and surrounding units. Match content quantity to its duration and connect naturally to adjacent units.
- Each unit may have only one speech owner: character dialogue, unassigned off-screen speech, or no speech.

# Action-writing guide

{{ partial("shared/action_writing_guide") }}

# Unified reference syntax

{{ partial("shared/writing_syntax") }}

Products, characters, scenes, and props all use the same `@[名称]` syntax. Names must be copied exactly from the candidate list; do not invent assets. This film is always episode {{ episode }}.
{% if instructions %}

{{ partial("shared/additional_instructions") }}
{% endif %}
