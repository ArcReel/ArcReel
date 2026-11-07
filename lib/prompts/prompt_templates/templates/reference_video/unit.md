---
id: reference_video/unit
category: video
title: "Reference video"
description: "Complete prompt for a reference-video unit, arranged in three parts: reference-material declarations, unit body, and style with negative constraints."
stage: reference_video
invoked_by:
  kind: generation_task
  name: reference_video
applies_to: {}
slots:
  declarations: "First part: subject bindings (<X>@图片N), appearance declarations for the same character, and voice declarations (@音频N); empty without reference material"
  body: "Second part: unit body, with mentions replaced by subject tokens, dialogue tokens reassembled, and narration tokens discarded"
  style: "Project visual style"
  style_description: "Project style description"
  co_present_characters: "Distinct characters sharing the frame, counting a base and its derivatives as one; empty if fewer than two"
  products: "Product names whose reference images are sent with the request, deduplicated in first-appearance order; empty without product reference images"
protected: false
---
{{ declarations }}

{{ body }}

{{ partial("shared/media_style") }}
{{ partial("reference_video/unit/avoid") }}
{% if products %}

High-fidelity product reproduction (highest priority, overriding the text / logo prohibitions above): products {{ partial("reference_video/unit/lists/products") }} in the frame must exactly match their product reference images: {{ partial("shared/product_fidelity") }}. Do not redesign or beautify the product itself. The text / logo prohibitions above mean no invented extra text or logos in the frame; text and logos already present in product reference images must remain unchanged. The project style applies only to visual elements other than the product.
{% endif %}
