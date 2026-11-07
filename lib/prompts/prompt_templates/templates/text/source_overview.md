---
id: text/source_overview
category: text
title: "Source overview"
description: "Derive a project overview (synopsis, genre, theme, world setting, and source language) from project source text for prompts in all later generation stages."
stage: source_overview
invoked_by:
  kind: user_action
  name: source_overview
applies_to:
  source_kind:
  - novel
  - screenplay
output_schema: lib.project.project_manager:ProjectOverview
slots:
  source_kind: "Source kind"
  target_language: "Output language, taken from the project source language, defaulting to Chinese"
  source_content: "Concatenated body of all source text in the project source directory"
protected: false
---
{{ variant("text/source_overview/task", source_kind) }}

**Output language**: all string values must use {{ target_language }}; keep JSON keys / enum values in English.

{{ source_content }}
