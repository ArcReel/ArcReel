---
id: storyboard/grid
category: storyboard
title: "Composite storyboard grid"
description: "Prompt for drawing a group of shots into one composite grid using a first/last-frame chain, then splitting the generated panels back into individual shots."
stage: grid
invoked_by:
  kind: generation_task
  name: grid
applies_to: {}
slots:
  reference_images: "Reference-image type declarations, numbered “图N” by the order sent with the request; empty without reference images"
  rows: "Number of grid rows"
  cols: "Number of grid columns"
  cell_count: "Total panel count"
  grid_aspect_ratio: "Aspect ratio of the whole composite image"
  panel_aspect_ratio: "Individual panel aspect ratio, calculated from the whole-image ratio and row/column counts"
  last_chain_cell: "Index of the final frame-chain panel; 0 when there is only an opening panel, omitting transition-frame lines"
  opening: "Opening shot in panel 0 (scene_id, description)"
  transitions: "Transition panel list (index, row, col, from_id, to_id, action, description)"
  placeholders: "Empty placeholder panel list (index, row, col)"
  style: "Project visual style"
  style_description: "Project style description"
protected: false
---
{% if reference_images %}
Reference_Images: {{ reference_images }}

{% endif %}
You are a professional storyboard artist. Generate one composite image containing exactly {{ cell_count }} equally sized panels in a strict {{ rows }}×{{ cols }} grid.

[Layout requirements]
- Exactly {{ rows }} rows and {{ cols }} columns, {{ cell_count }} panels in total, read left to right and top to bottom
- Overall image aspect ratio: {{ grid_aspect_ratio }}
- Each panel’s aspect ratio: {{ panel_aspect_ratio }}; all panels are exactly the same size
- No borders, gaps, or whitespace between panels; pack them tightly
- Do not merge, omit, or misalign panels
- Keep character appearance, lighting, and color style consistent across all panels

[Frame-chain pacing]
This grid uses a first/last-frame chain:
- Panel 0 is the first scene’s opening image
{% if last_chain_cell %}
- Panels 1~{{ last_chain_cell }} are transition frames between adjacent scenes (the previous scene’s ending = the next scene’s beginning)
{% endif %}
- Adjacent panels should show natural visual transitions and continuous action

[Panel content]
Panel 0 (row1 col1) - {{ opening.scene_id }} opening:
  {{ opening.description }}
{{ partial("storyboard/grid/lists/cells") }}

{{ partial("shared/media_style") }}

{{ partial("storyboard/grid/avoid") }}
