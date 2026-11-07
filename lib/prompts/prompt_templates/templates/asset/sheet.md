---
id: asset/sheet
category: asset
title: "Asset sheet"
description: "Complete prompts for character, character-derivative, scene, prop, and product asset sheets."
stage: asset_sheet
invoked_by:
  kind: generation_task
  name: asset
applies_to:
  asset_type:
  - character
  - character_derivative
  - scene
  - prop
  - product
slots:
  asset_type: "Asset type"
  name: "Asset name; empty for derivatives"
  description: "Appearance description or changes relative to the base"
  style: "Project visual style"
  style_description: "Project style description"
protected: false
---
{{ variant("asset/sheet/style", asset_type) }}

{{ variant("asset/sheet/title", asset_type) }}

{{ description }}

{{ variant("asset/sheet/layout", asset_type) }}

{{ variant("asset/sheet/guard", asset_type) }}

Avoid:{{ variant("asset/sheet/exclusions", asset_type) }} {{ partial("shared/image_avoid") }}