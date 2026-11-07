---
id: text/draft_repair
category: text
title: "AI repair of a draft requiring repair"
description: "Revise a draft using its violation report: edit only affected entries for entry-level violations, or the whole document for episode-level violations. Shared by script-plan drafts and reference-video prompt-authoring drafts."
stage: script_plan
invoked_by:
  kind: user_action
  name: draft_repair
applies_to: {}
slots:
  document: "Draft document type and entry-array structure description"
  root: "Key of the entry array in the draft body (scenes / segments / units)"
  draft_json: "Full draft-body JSON"
  targets: "Entries to revise in entry mode, a list of objects with all keys {index, item_json, messages}; null in whole-episode mode"
  messages: "All violation messages in whole-episode mode; null in entry mode"
  source_text: "This episode’s source body; null without source text for reference"
  instructions: "Additional-instruction body; null if absent"
protected: false
---
You are a short-video script copy editor. The draft below failed validation. Revise it according to the violation report so that it passes validation.

# Draft description

{{ document }}

Full draft (for context only):

```json
{{ draft_json }}
```
{% if source_text %}

<source_text>
{{ source_text }}
</source_text>
{% endif %}

# Revision requirements

The violation messages were originally written for an Agent that can call tools. Tool calls mentioned in them (such as `patch_draft`) do not apply here; only revise the JSON to address the content problems identified by the messages.

{% if targets %}
Violations affect only the entries below. Revise only these entries, eliminating each violation; preserve the original values of unaffected fields and keep entry IDs unchanged.

{{ partial("text/draft_repair/lists/targets") }}

Output only one JSON object: `{"{{ root }}": [...]}`. In the order above, provide each revised entry’s complete object; do not output other entries.
{% else %}
Violations affect the episode as a whole, requiring revision of the entire draft:

{{ partial("text/draft_repair/lists/messages") }}

Keep entries without violations as unchanged as possible. Output only the complete revised draft JSON object, with the same top-level fields as the original draft.
{% endif %}

Keep the draft’s original language and writing style. Output nothing except JSON.
{% if instructions %}

{{ partial("shared/additional_instructions") }}
{% endif %}
