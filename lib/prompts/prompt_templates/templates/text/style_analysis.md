---
id: text/style_analysis
category: text
title: "Style-reference image analysis"
description: "Extract a visual-style description from the user’s uploaded style-reference image and write it to the project style description."
stage: style_analysis
invoked_by:
  kind: user_action
  name: style_analysis
applies_to: {}
slots: {}
protected: false
---
Analyze the visual style of this image. Describe the lighting, color palette, medium (e.g., oil painting, digital art, photography), texture, and overall mood. Do NOT describe the subject matter (e.g., people, objects) or specific content. Focus ONLY on the artistic style. Provide a concise comma-separated list of descriptors suitable for an image generation prompt.
