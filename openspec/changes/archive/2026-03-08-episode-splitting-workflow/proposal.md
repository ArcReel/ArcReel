## Why

The current system lacks a **mapping mechanism from novel to episodes**. After a user uploads a complete novel, the system passes the full text to Gemini to generate a script, but there is no way to specify "this episode should use only a particular portion of the novel." Specifically:

1. `normalize_drama_script.py` by default reads and concatenates **all files** under `source/`; the `--source` parameter can only specify the entire file, not a range within the file
2. The description for the `split-narration-segments` subagent mentions "the novel text range for this episode", but there is no actual mechanism for users to define or split that range
3. In the `manga-workflow` orchestration dispatch, it writes `Novel range for this episode: {chapter name/filename/start-end description}`, but this value cannot be obtained — the main agent does not know which part of the novel corresponds to which episode

Consequences:
- A 100,000-character novel is fully fed into Gemini, making generation quality unpredictable (the model decides which part to use on its own)
- Users cannot produce a specific episode on demand (e.g., only wanting to work on the first 1,000 characters of content)
- Consistent episode boundaries are missing when producing multiple episodes

## What Changes

A **progressive episode splitting** mechanism is added: a human-AI collaborative episode splitting workflow implemented through two scripts.

### Core Concept

```
User specifies target word count (e.g., 1000 characters per episode)
    ↓
peek script shows context around the split point (200 characters before and after)
    ↓
Agent reads the context and suggests natural break points (period, paragraph, or chapter boundary)
    ↓
User confirms or adjusts
    ↓
split --anchor "text before break point" --dry-run  to verify the split position
    ↓
Confirmed → split executes: episode_N.txt + _remaining.txt
    ↓
Loop to process the next episode
```

### New Scripts

**1. `peek_split_point.py`** — Split Point Detection

```bash
python peek_split_point.py --source source/novel.txt --target 1000 --context 200
```

- Input: source file path, target character count, context character count (default 200)
- Counting rules: includes punctuation, excludes blank lines (purely formatting whitespace lines)
- Output: context text before and after the split point + metadata (total character count, target position, actual character offset)

**2. `split_episode.py`** — Execute the Split

```bash
# Dry run first to verify the split position
python split_episode.py --source source/novel.txt --episode 1 --anchor "He turned and walked away." --dry-run

# Execute after confirming
python split_episode.py --source source/novel.txt --episode 1 --anchor "He turned and walked away."
```

- Input: source file path, episode number, anchor text (10-20 characters before the split point)
- `--dry-run`: only shows a split preview (last 50 characters of the front portion + first 50 characters of the back portion), without writing files
- Reports an error requiring a longer anchor text if the anchor matches multiple locations
- Output:
  - `source/episode_N.txt` — this episode's content
  - `source/_remaining.txt` — remaining content (overwritten each time)
- The original file is always preserved

### Workflow Integration

Episode splitting is embedded in **phase 2 (per-episode preprocessing) as a prerequisite check** within `manga-workflow`:

```
When phase 2 is triggered:
  Check if source/episode_{N}.txt exists
    ├─ Exists → proceed directly to preprocessing
    └─ Does not exist → trigger episode splitting workflow:
         1. Main agent asks user for target word count (or uses the last set value)
         2. Dispatch subagent to call peek_split_point.py
         3. Agent analyzes context and suggests split point
         4. User confirms
         5. Call split_episode.py to execute the split
         6. Continue into preprocessing (using episode_N.txt)
```

### On-Demand Per-Episode Splitting

Episode splitting **does not split the entire novel at once**, but rather splits only the episode currently being produced each time:

```
When producing episode 1:
  source/episode_1.txt does not exist
  → peek novel.txt → confirm → split → episode_1.txt + _remaining.txt
  → Continue with episode 1 preprocessing, script generation, asset generation...

(A few days later) When producing episode 2:
  source/episode_2.txt does not exist
  → peek _remaining.txt → confirm → split → episode_2.txt + _remaining.txt (updated)
  → Continue with episode 2...

Users can stop at any time; they do not need to plan all episodes at once.
```

### Adapting Existing Scripts

`normalize_drama_script.py` and the `split-narration-segments` subagent do not need major modifications — they only need to be given `--source source/episode_N.txt` at dispatch time, so they read the already-split single-episode file rather than the full novel.

## Capabilities

### New Capabilities
- `episode-splitting`: Progressive episode splitting — peek to detect split points + human-AI collaborative confirmation + physical splitting into per-episode files

### Modified Capabilities
- `workflow-orchestration` (from refactor-script-creation-workflow): manga-workflow phase 2 adds a prerequisite check; if the episode file is missing, the episode splitting workflow is triggered

## Impact

- **New files**:
  - `agent_runtime_profile/.claude/skills/manage-project/scripts/peek_split_point.py`
  - `agent_runtime_profile/.claude/skills/manage-project/scripts/split_episode.py`
- **Modified files**:
  - `agent_runtime_profile/.claude/skills/manga-workflow/SKILL.md` — phase 2 adds prerequisite check logic
  - `agent_runtime_profile/.claude/settings.json` — adds Bash execution permissions for the two new scripts
- **Not affected**:
  - `normalize_drama_script.py` — already supports the `--source` parameter; no modification needed
  - `split-narration-segments` subagent — just specify the file path at dispatch time
  - Backend services, frontend, and data model are all unaffected
