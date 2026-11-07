---
id: text/reference_video_prompt_authoring
category: text
title: "Reference video · prompt authoring"
description: "Expand confirmed reference-video unit bodies with shot size, composition, camera movement, and visual details."
stage: prompt_authoring
invoked_by:
  kind: agent_tool
  name: generate_episode_script
applies_to:
  content_mode: [drama, narration]
  generation_mode: [reference_video]
output_schema: lib.script.script_models:ReferencePromptAuthoringFlatScript
slots:
  target_language: "Output language"
  project_overview: "Project overview, an object with all keys {synopsis, genre, theme, world_setting}; use null for missing values"
  style: "Project style value"
  style_description: "Project style description"
  aspect_ratio: "Aspect ratio (such as 9:16)"
  assets: "Asset appearances, an object {characters, scenes, props}, each a [{name, appearance}] list (angle brackets neutralized)"
  character_names: "Candidate referenceable character names (including base/derivative)"
  scene_names: "Candidate referenceable scene names"
  prop_names: "Candidate referenceable prop names"
  units_content: "Rendered confirmed video units (projected by code: sequence number + duration + body)"
  episode: "Current episode number"
  max_refs: "Reference-image limit per video unit; null if unlimited"
  instructions: "Additional-instruction body; null if missing"
protected: false
---
# Role and task

You are an experienced short-video storyboard writer. Your task is **prompt authoring** for episode {{ episode }} using reference-video generation.
The script_plan_units table below is the content contract already confirmed by the user. Expand each unit’s body with shot size / composition / camera movement and visual details.

**Output language**: all string values must use {{ target_language }}; keep JSON keys in English.
**Structural constraints**: response_schema enforces fields / required items; this prompt only explains **how to write the body well**.

# Structure-preservation requirements (any violation rejects the whole output)

- The `units` array must have **the same length and order** as script_plan_units: do not merge, split, add, or delete units.
- Preserve every unit’s **dialogue and off-screen speech verbatim**: do not change words, add, remove, reorder, or change speakers.
  If dialogue does not fit the visuals you want, write visuals to fit the dialogue; **do not** change dialogue.
- Any new `@[名称]` in the body must be a registered name from the candidate list (you may reference assets not referenced by script_plan, but they must already be registered).
{% if max_refs %}
- A unit’s **visual description** may `@` reference at most {{ max_refs }} distinct asset names (the model limit); speaker positions in dialogue tokens `@[角色]{台词}` do not count, as they generate no reference image and only drive voice declarations. If exceeded, merge secondary characters into background descriptions without `@` references.
{% endif %}

# Context

{{ partial("shared/overview_block") }}

<style>
Style: {{ style }}
Description: {{ style_description }}
Aspect ratio: {{ aspect_ratio }}
</style>

{{ partial("shared/lists/asset_appearance_blocks") }}

<script_plan_units>
{{ units_content }}
</script_plan_units>

# Body-writing syntax

{{ partial("shared/writing_syntax") }}

# Prompt-authoring guide

The body directly drives video generation for this unit. Organize it in the order “shot size → composition → camera movement → visual content”, supplying ample visual information and favoring detail over brevity:

- Shot size: extreme wide / wide / medium / medium close-up / close-up, and angle (high / low / eye level).
- Composition: the subject’s position and foreground-background relationship (such as centered composition, diagonal composition, roads / colonnades as leading lines).
- Camera movement: camera position and movement (fixed / following / pushing in / pulling out / panning), including changes of focal subject.
- Visual content: this occupies most of the text. Include all visible movement during the unit: action sequences for each appearing subject (body / gestures / expression transitions),
  object interactions, background and environmental dynamics (crowds, weather, hems, moving light and shadow). You may describe motion texture (such as motion blur), ending with one sentence establishing the atmospheric tone.
  Match action quantity to unit duration: longer units have more action stages.
- Reference characters / scenes / props only with `@[名称]`. Candidates:
  - character: {{ partial("shared/asset_name_candidates", names=character_names) }}
  - scene: {{ partial("shared/asset_name_candidates", names=scene_names) }}
  - prop: {{ partial("shared/asset_name_candidates", names=prop_names) }}
  Reference images supply static appearance such as facial features, clothing, and furnishings; **do not** describe it in text. Be as specific as possible about actions, posture, interactions, and environmental dynamics.
  Verbs should describe physically observable actions (reach / turn / stroke / cast toward / tighten), avoiding mental verbs such as “sink into / remember / realize / decide”.
- Good: “Shot size: medium shot, slightly low angle. Composition: @[Character A] centered, with the window lattice and table of @[Scene A] in the foreground. Camera movement: fixed position, slowly pushing in.
  Visual content: @[Character A] slowly walks toward the window in @[Scene A], raises a hand, and pushes open the wooden window; the hem of the clothing lifts gently in the draft. @[Character A]{This wind is colder than last year.}
  Then the character lowers their head to gaze at @[Prop A] held in hand,
  fingertips slowly tightening, breathing slowing, gaze gradually rising from @[Prop A] toward the window; candlelight flickers in the draft, light and shadow moving slowly across the face, creating a restrained, oppressive atmosphere.”
- Bad (too short): “@[Character A] stands in @[Scene A].” No shot size / composition / camera movement, action progression, or environmental dynamics; the generated video will be nearly static.
- Bad (describing appearance): “Character A in clothing of a certain color stands before Scene A of a certain color.” Reference images should supply appearance / clothing / colors, and `@[名称]` references are missing.
- Preserve scene references per unit: each scene asset referenced in a unit’s script_plan body must still be `@` referenced in the expanded body.
  Do not omit it because the previous unit referenced the same scene. If script_plan has no scene reference but a candidate matches the location, add that reference.

Give this episode a short `title`. Output units in script_plan_units order.
{% if instructions %}

{{ partial("shared/additional_instructions") }}
{% endif %}
