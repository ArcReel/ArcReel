---
name: generate-assets
description: >-
  Unified asset generation accepting `--type=character|scene|prop|product`, or scanning all pending resources (missing sheets) and dispatching by type when omitted. Use for character/scene/prop/product image requests, new asset references, or missing *_sheet images.
---

# Generate asset sheets

Create character, scene, prop, and product sheets for consistent visuals throughout the video.
Project settings select the image provider, without locking a specific backend.

> Prompt-writing principles: “Prompt language” in `.claude/references/generation-modes.md`.

## Shared conventions

- All asset `description` values use **narrative paragraphs**, not keyword lists.
- Users maintain only `description` in project.json; the complete image-backend prompt
  (layout / defect-prevention wording / negatives) is assembled server-side by `lib/prompts/prompt_builders.py`,
  sharing one source of truth between WebUI and skills.
- Pending means Artifact Manifest state `missing`, including registered but unreadable files; reuse `stale` artifacts, not counting them as pending.
- Derivatives (`本体/衍生`) share a batch with their base; if the base is pending, submit derivatives only after base success. Base failure yields `generation_dependency_failed`, without derivative submission.

---

## Character (character)

### description writing guide

Use a connected paragraph describing appearance, clothing, demeanor, age, build, facial features, and clothing details.

**Example**:

> “A woman in her early twenties, slender, with an oval face and clear almond eyes; her slightly furrowed willow-shaped brows convey a hint of melancholy. She wears a pale-blue embroidered silk dress and matching waist ribbon, dignified yet lively.”

### Output layout

Landscape 16:9 three-view sheet on pure white: front / exact side (90°) / rear, horizontally arranged.
Face, hair, clothing, and accessories must match exactly across all three panels.

> Users focus on appearance/clothing in description; the builder injects layout.

---

## Scene (scene)

### description writing guide

Use a connected paragraph describing form, lighting, and atmosphere, emphasizing distinctive features recognizable across scenes.

**Example**:

> “A century-old scholar tree at the village entrance, its trunk requiring three people with joined arms to encircle it, bark cracked with age. A clear charred lightning scar winds down from the top. Dense foliage casts dappled summer shadows.”

### Output layout

Landscape 16:9 single panoramic environment establishing shot.

---

## Prop (prop)

### description writing guide

Use a connected paragraph describing form, texture, and detail, emphasizing distinctive recognizable features.

**Example**:

> “An inherited emerald-green jade pendant about thumb-sized, smooth and translucent. Fine lotus patterns cover its surface, petals unfolding in layers. A red silk cord is tied in a traditional Chinese knot.”

### Output layout

Landscape 16:9 single prop sheet with a clean light-gray background.

---

## Product (product)

### description writing guide

Use a connected paragraph describing product appearance, brand text, colors, materials, proportions, and structure.

### Output layout

Landscape 16:9 single product sheet, clean light-gray background and even studio lighting.

---

## Tool calls

Enqueue through MCP:

| Operation | Tool |
|------|------|
| List all / one type pending | `mcp__arcreel__list_pending_assets({"type": "character"})` (type optional) |
| List episode-referenced pending | `mcp__arcreel__list_pending_assets({"episode_id": 3})` |
| Generate episode-referenced pending, including products/derivatives | `mcp__arcreel__generate_assets({"episode_id": 3})` |
| Generate all pending (one pass per four types) | `mcp__arcreel__generate_assets({})` |
| Generate one type’s pending | `mcp__arcreel__generate_assets({"type": "character"})` |
| Generate selected assets | `mcp__arcreel__generate_assets({"type": "prop", "names": ["玉佩", "密信"]})` |
| Generate one asset | `mcp__arcreel__generate_assets({"type": "scene", "names": ["村口老槐树"]})` |
| Generate one product | `mcp__arcreel__generate_assets({"type": "product", "names": ["保温杯"]})` |
| Generate derivative | `mcp__arcreel__generate_assets({"type": "character", "names": ["张三/战损"]})` |

Do not combine `episode_id` with `type` / `names`; the server computes episode references using the same list as the Web episode layer.

Results return `requested / succeeded / failed / blocked / skipped` per ID, such as `character/张三` (derivative `character/张三/战损`);
stale but reusable old images are `skipped`, never automatically regenerated;
follow each `problem.code` / `problem.action`, without parsing prose.
See `.claude/references/generation-results.md` for structure.

## Workflow

1. **Load metadata**: identify asset sheets with Manifest state `missing`
2. **Enqueue**: submit only names; server execution renders stored description (`lib.prompts.prompt_builders` injects layout / defect prevention / negatives). Declared unreadable originals reject generation with `asset_original_missing`; ask the user to reupload or clear original fields
3. **Review checkpoint**: dispatch reviewers per “Review checkpoint: image review”, giving reports/recommendations to users for approval, regeneration, or editing
4. **Update project.json**: `character_sheet` / `scene_sheet` / `prop_sheet` / `product_sheet` paths

## Review checkpoint: image review

After generation, read-only `review-images` inspects sheets before user confirmation. Review triggers no generation/editing charges; reviewer-model calls count normally toward Agent session usage.

1. **Dispatch**: main Agent only (subagents cannot dispatch subagents; generation subagents return results first). Review only this round’s successful assets, one type per group, at most 20. For multiple groups, issue one `review-images` call per group in the same message for parallel work. Supply type (`character` / `scene` / `prop` / `product` / `character_derivative`), asset names (base/derivative), and user concerns.
2. **User review**: summarize usable items, regenerate/edit recommendations with causes, and uncertainty. For items requiring attention, use AskUserQuestion multi-select (`multiSelect: true`), giving name, cause, and proposed handling per option. Never regenerate/edit before confirmation.
3. **Execute** selected handling according to “Editing vs regeneration” below. Optionally dispatch `review-images` again for new-version acceptance.

For user questions about particular sheets, inspect directly with `mcp__arcreel__inspect_images`; no subagent required.

## Review checkpoint: editing vs regeneration

Choose by user intent; the wrong path replaces satisfactory content or loses unexpected changes:

- **Local edits only** (hair color, clutter, lighting), with satisfactory composition/design →
  `mcp__arcreel__edit_images({"resource_type": "character", "edits": [{"id": "张三", "instruction": "把头发改成红色"}]})`
  refines the base and supports same-type asset batches
- **New composition/design**, or intended description changes affecting later
  regeneration → use `generate_assets` to redraw from updated description
- Editing does not update `description` / prompt; later `generate_assets` redraws the original description,
  with edits recoverable only through version history

## Quality checks

- **Character**: identical face, hair, clothes, accessories across front / side / rear
- **Scene**: strong composition/distinctive features, suitable lighting/atmosphere
- **Prop**: clear subject, faithful details, visible special textures
- **Product**: faithful brand, text, colors, materials, proportions, structure, without hands/models/other people from the reference
