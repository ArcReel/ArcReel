---
name: manage-project
description: Project management toolset. Use cases: (1) episode splitting — detect split points and execute splitting, (2) batch add characters/clues to project.json. Provides a progressive peek (preview) + split (execute) splitting workflow and batch character/clue writing.
user-invocable: false
---

# Project Management Toolset

Provides command-line tools for project file management, primarily for episode splitting and batch character/clue writing.

## Tool Overview

| Script | Function | Caller |
|------|------|--------|
| `peek_split_point.py` | Detect context and natural break points near the target word count | Main agent (Phase 2) |
| `split_episode.py` | Execute episode splitting, generate episode_N.txt + _remaining.txt | Main agent (Phase 2) |
| `add_characters_clues.py` | Batch add characters/clues to project.json | Subagent |

## Episode Splitting Workflow

Episode splitting uses a progressive **peek → user confirmation → split** flow, executed directly by the main agent in manga-workflow Phase 2.

### Step 1: Detect Split Point

```bash
python .claude/skills/manage-project/scripts/peek_split_point.py --source {source-file} --target {target-word-count}
```

**Parameters**:
- `--source`: source file path (`source/novel.txt` or `source/_remaining.txt`)
- `--target`: target effective word count
- `--context`: context window size (default 200 characters)

**Output** (JSON):
- `total_chars`: total effective character count
- `target_offset`: original text offset corresponding to target word count
- `context_before` / `context_after`: context before and after the split point
- `nearby_breakpoints`: list of nearby natural break points (sorted by distance, up to 10)

### Step 2: Execute Split

```bash
# Dry run (preview only)
python .claude/skills/manage-project/scripts/split_episode.py --source {source-file} --episode {N} --target {target-word-count} --anchor "{anchor-text}" --dry-run

# Actual execution
python .claude/skills/manage-project/scripts/split_episode.py --source {source-file} --episode {N} --target {target-word-count} --anchor "{anchor-text}"
```

**Parameters**:
- `--source`: source file path
- `--episode`: episode number
- `--target`: target effective word count (consistent with peek)
- `--anchor`: anchor text at the split point (10-20 characters)
- `--context`: search window size (default 500 characters)
- `--dry-run`: preview only, do not write files

**Positioning mechanism**: target word count calculates approximate offset → search for anchor within ±window range → use the closest match

**Output files**:
- `source/episode_{N}.txt`: first half
- `source/_remaining.txt`: second half (source file for the next episode)

## Batch Character/Clue Writing

Execute from within the project directory; automatically detects the project name:

⚠️ Must be a single line; JSON must use compact format; do not use `\` for line breaks:

```bash
python .claude/skills/manage-project/scripts/add_characters_clues.py --characters '{"CharacterName": {"description": "...", "voice_style": "..."}}' --clues '{"ClueName": {"type": "prop", "description": "...", "importance": "major"}}'
```

## Character Count Rules

- Count all characters in non-empty lines (including punctuation)
- Empty lines (lines containing only whitespace) are not counted
