---
name: generate-clues
description: Generate clue design reference sheets (props/environments). Use when the user says "generate clue images", "draw prop designs", wants to create reference sheets for important items or scenes, or when major clues are missing clue_sheet. Ensures visual consistency across scenes.
---

# Generate Clue Design Sheets

Use the Gemini 3 Pro Image API to create clue design sheets, ensuring visual consistency of important items and environments throughout the entire video.

> For prompt writing principles, see the "Prompt Language" section of `.claude/references/content-modes.md`.

## Clue Types

- **Props (prop)**: key items such as tokens, weapons, letters, jewelry
- **Environments (location)**: iconic buildings, specific trees, important locations

## Clue Description Writing Guide

When writing the `description`, use a **narrative style** rather than listing keywords.

**Prop example**:
> "A translucent jade pendant in emerald green, roughly the size of a thumb, with a warm and lustrous texture. The surface is carved with an intricate lotus pattern, the petals unfurling layer by layer. The pendant is strung on a red silk cord tied with a traditional Chinese knot."

**Environment example**:
> "A hundred-year-old scholar tree at the village entrance, its trunk so thick that three people cannot embrace it, its bark cracked and weathered. The main trunk bears a prominent lightning scar that winds from top to bottom. The tree crown is dense, casting dappled shadows in summer."

**Key points**: use flowing paragraphs to describe form, texture, and details, highlighting distinctive features that can be recognized across scenes.

## Command-Line Usage

```bash
# Generate all pending clues
python .claude/skills/generate-clues/scripts/generate_clue.py --all

# Generate a specified single clue
python .claude/skills/generate-clues/scripts/generate_clue.py --clue "jade pendant"

# Generate specified multiple clues
python .claude/skills/generate-clues/scripts/generate_clue.py --clues "jade pendant" "old scholar tree" "secret letter"

# List clues pending generation
python .claude/skills/generate-clues/scripts/generate_clue.py --list
```

## Workflow

1. **Load project metadata** — find clues with `importance='major'` and missing `clue_sheet` from project.json
2. **Generate clue designs** — select the corresponding template based on type (prop/location), call the script to generate
3. **Review checkpoint** — display each design sheet; the user can approve or request regeneration
4. **Update project.json** — update the `clue_sheet` path

## Prompt Templates

### Prop Type (prop)
```
A professional prop design reference sheet, {project style}.

Multi-angle showcase of the prop "[name]". [detailed description - narrative paragraph]

Three views arranged horizontally on a clean light gray background: full front view on the left, 45-degree side view in the middle to show depth, key detail close-up on the right. Soft and even studio lighting, high-definition quality, accurate colors.
```

### Environment Type (location)
```
A professional scene design reference sheet, {project style}.

Visual reference for the iconic scene "[name]". [detailed description - narrative paragraph]

The main view occupies three-quarters of the area to show the overall appearance and atmosphere of the environment; a detail close-up is shown in the lower right corner. Soft natural lighting.
```

## Quality Check

- Props: three angles clear and consistent, details match the description, special textures clearly visible
- Environments: overall composition and iconic features prominent, appropriate lighting atmosphere, detail image clear
