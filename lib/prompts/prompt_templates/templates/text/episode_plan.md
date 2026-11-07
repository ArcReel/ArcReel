---
id: text/episode_plan
category: text
title: "Episode planning"
description: "Split the source into episodes with a title, end hook, and end anchor for each; drama also includes story beats and a next-episode teaser."
stage: episode_plan
invoked_by:
  kind: agent_tool
  name: plan_episodes
applies_to:
  content_mode: [drama, narration]
  source_kind: [novel, screenplay]
output_schema: lib.episode.episode_planner:DramaPlanDraft
slots:
  content_mode: "Content-mode axis value (drama / narration), used to resolve named variant fragments"
  source_kind: "Source-kind axis value (novel / screenplay), used to resolve named variant fragments"
  synopsis: "Synopsis; null if absent"
  genre: "Genre; null if absent"
  unit_noun: "Reading-unit noun (characters / words), following the source language"
  target_volume: "Target volume per episode, an object with all keys {units, seconds, units_per_second}; seconds and units_per_second are populated only when converted from target episode duration, and the speaking rate is formatted as text; null if not set"
  max_episodes: "Maximum episodes to plan in this batch, derived from the text model’s output limit"
  context_entries: "Last few planned episodes, objects with all keys {episode, title, hook}, with null title when absent; empty list if none"
  instructions: "Additional-instruction body; null if absent"
  progress: "Overall progress, an object with all keys {planned_count, remaining_units, window_units}; supplied only with additional instructions, otherwise null"
  window_is_final: "Whether this window contains the full source’s ending (or the end of the unsplit source range being planned)"
  followed_by_episode: "An already planned episode immediately follows this window’s end (true when planning an unsplit source range and this window includes its ending)"
  failure: "Reasons the previous output failed validation; null on the first round"
  window: "Source-window body for this batch"
protected: false
---
{{ variant("text/episode_plan/intro", source_kind) }}

# Project information
- Content mode: {{ variant("text/episode_plan/content_mode_label", content_mode) }}
{% if synopsis %}
- Synopsis: {{ synopsis }}
{% endif %}
{% if genre %}
- Genre: {{ genre }}
{% endif %}
{% if target_volume %}
- Target volume per episode: approximately {{ target_volume.units }} {{ unit_noun }} ({% if target_volume.seconds %}roughly converted from a target episode duration of {{ target_volume.seconds }} seconds at a speaking rate of approximately {{ target_volume.units_per_second }} {{ unit_noun }}/second; {% endif %}variation is allowed to preserve plot completeness)
{% else %}
- Target volume per episode: not set; use your judgment for short-video pacing, prioritizing complete plot arcs
{% endif %}
{% if max_episodes %}
- Plan at most {{ max_episodes }} episodes in this batch
{% endif %}
{% if context_entries %}

# Previously planned context (fixed; do not change it, continue from it)
{{ partial("text/episode_plan/lists/context_entries") }}
{% endif %}
{% if instructions %}

{{ partial("shared/additional_instructions") }}
{% endif %}
{% if progress %}

# Overall progress
- {{ progress.planned_count }} episodes already planned
- Approximately {{ progress.remaining_units }} {{ unit_noun }} remain unplanned (including this window and subsequent source files)
- This window contains the first {{ progress.window_units }} {{ unit_noun }} of that remainder
- If the additional instructions include global constraints such as total episode count or chapter alignment, use this progress and remainder to determine this batch’s splitting pace and episode count.
{% endif %}

# Splitting rules
- For each episode provide title (an appealing short title), hook (an explanation of the episode-end hook: why the cut belongs here and what suspense it leaves),
  and end_anchor (a 10~30-character passage at the episode’s end, copied verbatim from the source below including punctuation, occurring exactly once in the entire source excerpt;
  episode content is all source text from after the previous episode’s ending through the end of this passage).
{{ variant("text/episode_plan/author_division_rule", source_kind) }}
{{ variant("text/episode_plan/outline_rule", content_mode) }}
- List episodes in order. end_anchor positions must strictly increase (continuous ranges, without overlap or gaps).
{% if window_is_final and followed_by_episode %}
- An already planned next episode immediately follows this source excerpt: plan through this excerpt’s end, using a passage at its end as the last episode’s end_anchor; leave no trailing text.
{% elif window_is_final %}
- This excerpt contains the end of the full source: plan through that ending, using a passage at the full source’s end as the last episode’s end_anchor; leave no trailing text.
{% else %}
- This excerpt is only one window of the full source: do not force incomplete plot arcs at the window’s end into episodes; leave them for the next planning batch.
{% endif %}
- If this excerpt contains no splitting point with a complete plot arc, return an empty episodes list; do not force a cut.
- Output only JSON conforming to the schema, with no other content.
{% if failure %}

# The previous output failed validation; correct the specific issues and output again
{{ partial("text/episode_plan/lists/failure") }}
{% endif %}

{{ variant("text/episode_plan/source_heading", source_kind) }}
---
{{ window }}
---
