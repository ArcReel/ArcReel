---
id: storyboard/image
category: storyboard
title: "Storyboard image"
description: "Complete storyboard-image prompt combining style, reference-image declarations, and shot imagery."
stage: storyboard_image
invoked_by:
  kind: generation_task
  name: storyboard
applies_to: {}
slots:
  style: "Project visual style"
  style_description: "Project style description"
  reference_images: "Serialized reference-image type declarations; empty without reference images"
  structured_body: "Serialized Scene and Composition; empty for the plain-text form"
  text_body: "Plain-text body; empty for the structured form"
protected: false
idempotent: true
---
{{ partial("shared/media_style") }}
{% if reference_images %}
{{ partial("storyboard/image/references") }}
{% endif %}
{% if text_body %}

{{ text_body }}

{% else %}
{{ structured_body }}
{% endif %}
{{ partial("storyboard/image/avoid") }}