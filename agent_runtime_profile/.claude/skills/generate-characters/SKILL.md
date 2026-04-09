---
name: generate-characters
description: Generate character design reference sheets (three-view). Use when the user says "generate character images", "draw character designs", wants to create reference sheets for new characters, or when characters are missing character_sheet. Ensures consistent character appearance throughout the video.
---

# Generate Character Design Sheets

Use the Gemini 3 Pro Image API to create character design sheets, ensuring visual consistency throughout the entire video.

> For prompt writing principles, see the "Prompt Language" section of `.claude/references/content-modes.md`.

## Character Description Writing Guide

When writing the character `description`, use a **narrative style** rather than listing keywords.

**Recommended**:
> "A woman in her early twenties, slender, with bright almond-shaped eyes on an oval face, and gently furrowed willow-leaf eyebrows that carry a touch of melancholy. She wears a pale blue embroidered silk skirt with a matching sash at the waist, appearing elegant yet lively."

**Key points**: use flowing paragraphs to describe appearance, clothing, and temperament, including age, build, facial features, and clothing details.

## Command-Line Usage

```bash
# Generate all pending characters
python .claude/skills/generate-characters/scripts/generate_character.py --all

# Generate a specified single character
python .claude/skills/generate-characters/scripts/generate_character.py --character "{character-name}"

# Generate specified multiple characters
python .claude/skills/generate-characters/scripts/generate_character.py --characters "{character1}" "{character2}" "{character3}"

# List characters pending generation
python .claude/skills/generate-characters/scripts/generate_character.py --list
```

## Workflow

1. **Load project data** — find characters missing `character_sheet` from project.json
2. **Generate character designs** — build the prompt from the description, call the script to generate
3. **Review checkpoint** — display each design sheet; the user can approve or request regeneration
4. **Update project.json** — update the `character_sheet` path

## Prompt Template

```
A professional character design reference sheet, {project style}.

Three-view design sheet for the character "[character name]". [character description - narrative paragraph]

Three full-body images of equal proportions arranged horizontally on a clean light gray background: front view on the left, three-quarter view in the middle, pure side profile on the right. Soft and even studio lighting, no harsh shadows.
```

> The art style is determined by the project's `style` field; do not use fixed "manga/anime" descriptions.
