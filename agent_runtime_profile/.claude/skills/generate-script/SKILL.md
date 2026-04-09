---
name: generate-script
description: Generate JSON scripts using the Gemini API. Called by the create-episode-script subagent. Reads step1 intermediate files and project.json, calls Gemini to generate JSON scripts conforming to the Pydantic model.
user-invocable: false
---

# generate-script

Generate JSON scripts using the Gemini API. This skill is called by the `create-episode-script` subagent and is not directly user-facing.

## Prerequisites

1. `project.json` exists in the project directory (contains style, overview, characters, clues)
2. Step 1 preprocessing is complete:
   - narration: `drafts/episode_N/step1_segments.md`
   - drama: `drafts/episode_N/step1_normalized_script.md`

## Usage

```bash
# Generate script for a specific episode
python .claude/skills/generate-script/scripts/generate_script.py --episode {N}

# Custom output path
python .claude/skills/generate-script/scripts/generate_script.py --episode {N} --output scripts/ep1.json

# Preview prompt (does not actually call the API)
python .claude/skills/generate-script/scripts/generate_script.py --episode {N} --dry-run
```

## Generation Flow

The script internally completes the following steps via `ScriptGenerator`:

1. **Load project.json** — read content_mode, characters, clues, overview, style
2. **Load Step 1 intermediate file** — select `step1_segments.md` (narration) or `step1_normalized_script.md` (drama) based on content_mode
3. **Build Prompt** — combine the project overview, style, characters, clues, and intermediate file content into a complete prompt
4. **Call Gemini API** — use the `gemini-3-flash-preview` model, pass the Pydantic schema as `response_schema` to constrain the output format
5. **Pydantic validation** — validate the returned JSON with `NarrationEpisodeScript` (narration) or `DramaEpisodeScript` (drama)
6. **Add metadata** — write episode, content_mode, statistics (segment/scene count, total duration), and timestamp

## Output Format

The generated JSON file is saved to `scripts/episode_N.json`, with the core structure:

- `episode`, `content_mode`, `novel` (title, chapter, source_file)
- narration mode: `segments` array (each segment includes visual, novel_text, duration_seconds, etc.)
- drama mode: `scenes` array (each scene includes visual, dialogue, action, duration_seconds, etc.)
- `metadata`: total_segments/total_scenes, created_at, generator
- `duration_seconds`: total episode duration (seconds)

## `--dry-run` Output

Prints the complete prompt text that would be sent to Gemini; does not call the API or write files. Used to check prompt quality and length.

> Specifications for both supported modes are detailed in `.claude/references/content-modes.md`.
