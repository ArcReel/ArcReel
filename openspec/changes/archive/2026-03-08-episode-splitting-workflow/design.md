## Context

### Current State

In ArcReel's script creation workflow, `normalize_drama_script.py` by default reads all files under `source/`, concatenates them, and passes the result to Gemini. This is fine for short novels, but when users upload a complete long novel, there is no way to specify "this episode should use only content from section X to section Y." Although the `manga-workflow` orchestration pre-allocates a "novel text range for this episode" parameter slot, there is no actual splitting mechanism.

### Dependencies

This change depends on the architecture already completed by `refactor-script-creation-workflow` — focused subagents + manga-workflow orchestration skill. The new episode splitting workflow is embedded as a prerequisite check in phase 2 of manga-workflow.

### Related Files

| File | Purpose |
|------|---------|
| `agent_runtime_profile/.claude/skills/manga-workflow/SKILL.md` | Orchestration skill, needs prerequisite check added |
| `agent_runtime_profile/.claude/skills/manage-project/scripts/` | Project management scripts directory, new scripts go here |
| `agent_runtime_profile/.claude/settings.json` | Permission configuration |
| `agent_runtime_profile/.claude/agents/normalize-drama-script.md` | Drama mode preprocessing subagent |
| `agent_runtime_profile/.claude/agents/split-narration-segments.md` | Narration mode preprocessing subagent |

## Goals / Non-Goals

**Goals:**

1. Provide the `peek_split_point.py` script to show context near the target character count for agent and user decision-making
2. Provide the `split_episode.py` script to physically split the novel into per-episode files + remaining file
3. Embed episode splitting into the phase 2 prerequisite check of manga-workflow
4. Keep existing scripts (`normalize_drama_script.py`, `generate_script.py`) unchanged

**Non-Goals:**

- Not implementing automatic episode splitting (AI fully automatically deciding episode boundaries) — manual confirmation step is retained
- Not implementing frontend UI interactions (drag-and-drop range marking, etc.) — completed via agent conversation
- Not modifying the project.json data structure (no storage of episode_plan mapping)
- Not supporting logical mapping approach (dynamic extraction based on anchor text) — physical splitting is used

## Decisions

### Decision 1: Split Positioning Method — Anchor Text Matching

**Choice**: `split_episode.py` uses **anchor text** (N characters before the split point) to locate the split position, rather than a numeric offset

**Workflow**:
```
peek outputs context → agent suggests break point → user confirms
    ↓
split --anchor "He turned and walked away." --dry-run    ← dry run first
    ↓
Output: "Found matching position, will split at character 1047.
      End of front portion: ...Moonlight fell on the cobblestone path. He turned and walked away.
      Start of back portion: Chapter Two: The Great Desert..."
    ↓
User confirms → split --anchor "He turned and walked away."  ← actual execution
```

**Parameter design**:
- `--anchor <text>`: a text fragment before the split point (recommended 10-20 characters); the script finds this text in the original and splits at its **end**
- `--dry-run`: only shows the split preview (last 50 characters of front portion + first 50 characters of back portion), without actually writing files
- If the anchor matches multiple locations in the original text, reports an error requesting a longer anchor text

**Alternatives**:
- Numeric offset `--split-at 1047` → the counting baseline for peek and split must be strictly consistent; users cannot verify whether the position is correct
- Line number → paragraph lengths in Chinese novels vary greatly; not precise enough

**Rationale**: Anchor text is human-readable and verifiable. The dry run lets users confirm the position is correct before actual splitting. Even if the file content changes slightly (e.g., a typo is corrected), as long as the anchor text still exists, the split position is correct.

### Decision 2: Physical Splitting vs. Logical Mapping

**Choice**: Physical splitting (generate `source/episode_N.txt` files)

**Alternatives**:
- Record `{start_marker, end_marker}` mapping in project.json, dynamically extracting at script runtime → requires modifying multiple downstream scripts; anchor matching is error-prone
- Have users manually split files and upload → poor user experience

**Rationale**: After physical splitting, downstream processes (`normalize_drama_script.py --source source/episode_N.txt`) require **zero modifications**. The file is the state — simple, reliable, and debuggable.

### Decision 2: Character Counting Rules

**Choice**: Include punctuation, exclude blank lines

- Counting scope: all characters in non-blank lines (including Chinese characters, punctuation, digits, and letters)
- Excluded: purely blank lines (`\n`, `\r\n`, or lines containing only spaces)
- Rationale: punctuation is part of the content (it affects reading duration); blank lines are only formatting

### Decision 3: Remaining Content Management

**Choice**: Overwriting `_remaining.txt`

- After each split, `_remaining.txt` is updated with the remaining content
- The original `novel.txt` (or user-uploaded original file) is always preserved
- To re-split, you can start over from the original

**Alternatives**:
- Only record the offset, dynamically extract from the original each time → introduces state management complexity
- Don't keep a remaining file; subtract already-split content from the original each time → complex calculation, error-prone

### Decision 4: Script Placement Location

**Choice**: `agent_runtime_profile/.claude/skills/manage-project/scripts/`

**Rationale**: Episode splitting is a project management operation, in the same directory as the existing `add_characters_clues.py`. It does not belong to the `generate-script` skill (which is for generating JSON scripts).

### Decision 5: Position of Episode Splitting in the Workflow

**Choice**: Prerequisite check in phase 2, not an independent phase

**Rationale**:
- Episode splitting is only triggered when needed (when `source/episode_{N}.txt` does not exist)
- If users prepare per-episode files themselves, it is completely skipped
- Does not increase the number of workflow phases, keeping it simple

**Trigger logic**:
```
Phase 2 starts (producing episode N) →
  Does source/episode_{N}.txt exist?
    ├─ Yes → use it directly for preprocessing
    └─ No → trigger single-episode splitting:
         Does _remaining.txt exist?
           ├─ Yes → peek _remaining.txt (continue from last remaining content)
           └─ No → peek the original novel file (first split)
         → agent suggests break point → user confirms
         → split --dry-run to verify → split executes
         → generate episode_{N}.txt + update _remaining.txt
         → continue to preprocessing
```

**On-demand nature**: Only the episode currently being produced is split each time; planning all episodes at once is not required. Users can produce one episode, then come back days later to produce the next.

## Risks / Trade-offs

### [Risk] Re-splitting Scenario

A user splits 3 episodes and then finds the split point for episode 1 is not ideal and wants to redo it.

→ **Mitigation**: The original is always preserved. Users can delete `episode_*.txt` and `_remaining.txt` and re-split from the beginning. They can also re-do just a single episode (by manually editing the episode file).

### [Trade-off] More Physical Files

Each episode has a file + `_remaining.txt`, so the source/ directory will have more files.

→ **Accepted**: The number of files is proportional to the number of episodes, which is manageable. File names are clear (`episode_N.txt`) and not confusing.
