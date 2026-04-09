---
name: manga-workflow
description: End-to-end workflow orchestrator for converting novels into short videos. Must be used when the user mentions making videos, creating a project, continuing a project, or checking progress. Trigger scenarios include but are not limited to: "help me turn my novel into a video", "start a new project", "continue", "next step", "check project progress", "start from scratch", "split episodes", "run the whole workflow automatically", etc. Even if the user only says a brief "continue" or "next step", as long as the current context involves a video project, it should trigger. Do not use for generating individual assets (e.g., redrawing a single storyboard image or regenerating a single character design — those have dedicated skills).
---

# Video Workflow Orchestration

You (the main agent) are the orchestration hub. You **do not** directly process novel text or generate scripts; instead, you:
1. Detect project status → 2. Decide the next phase → 3. Dispatch the appropriate subagent → 4. Display results → 5. Get user confirmation → 6. Loop

**Core constraints**:
- Novel source text is **never loaded into the main agent context**; subagents read it independently
- Each dispatch only passes **file paths and key parameters**, not large chunks of content
- Each subagent completes one focused task and returns; the main agent handles inter-phase coordination

> Content mode specifications (aspect ratio, duration, etc.) are detailed in `.claude/references/content-modes.md`.

---

## Phase 0: Project Setup

### New Project

1. Ask for the project name
2. Create `projects/{name}/` and subdirectories (source/, scripts/, characters/, clues/, storyboards/, videos/, drafts/, output/)
3. Create the initial `project.json` file
4. **Ask for content mode**: `narration` (default) or `drama`
5. Ask the user to place the novel text in `source/`
6. **Automatically generate project overview after upload** (synopsis, genre, theme, world_setting)

### Existing Project

1. List projects in `projects/`
2. Display project status summary
3. Continue from the last incomplete phase

---

## Status Detection

After entering the workflow, use Read to read `project.json` and Glob to check the file system. Check in order; the first missing item determines the current phase:

1. characters/clues are empty? → **Phase 1**
2. Target episode's `source/episode_{N}.txt` does not exist? → **Phase 2**
3. Target episode's drafts/ intermediate file does not exist? → **Phase 3**
   - narration: `drafts/episode_{N}/step1_segments.md`
   - drama: `drafts/episode_{N}/step1_normalized_script.md`
4. `scripts/episode_{N}.json` does not exist? → **Phase 4**
5. Any character is missing character_sheet? → **Phase 5** (can run parallel with Phase 6)
6. Any importance=major clue is missing clue_sheet? → **Phase 6** (can run parallel with Phase 5)
7. Any scene is missing storyboard image? → **Phase 7**
8. Any scene is missing video? → **Phase 8**
9. All complete → workflow ends; guide user to export CapCut draft in the web interface

**Determining target episode**: if the user has not specified, find the latest incomplete episode, or ask the user.

---

## Inter-Phase Confirmation Protocol

**After each subagent returns**, the main agent performs:

1. **Display summary**: show the subagent's returned summary to the user
2. **Get confirmation**: use AskUserQuestion to provide options:
   - **Continue to next phase** (recommended)
   - **Redo this phase** (re-dispatch with additional modification requests)
   - **Skip this phase**
3. **Act based on user choice**

---

## Phase 1: Global Character/Clue Design

**Trigger**: characters or clues are empty in project.json

**Dispatch `analyze-characters-clues` subagent**:

```
Project name: {project_name}
Project path: projects/{project_name}/
Analysis scope: {entire novel / user-specified range}
Existing characters: {list of existing character names, or "none"}
Existing clues: {list of existing clue names, or "none"}

Please analyze the novel source text, extract character and clue information, write to project.json, and return a summary.
```

---

## Phase 2: Episode Planning

**Trigger**: target episode's `source/episode_{N}.txt` does not exist

Only split the current episode being produced. **The main agent executes directly** (does not dispatch a subagent):

1. Determine source file: use `source/_remaining.txt` if it exists, otherwise use the original novel file
2. Ask the user for the target word count (e.g., 1000 characters/episode)
3. Call `peek_split_point.py` to show context around the split point:
   ```bash
   python .claude/skills/manage-project/scripts/peek_split_point.py --source {source-file} --target {target-word-count}
   ```
4. Analyze nearby_breakpoints, suggest a natural break point
5. After user confirms, first do a dry run to verify:
   ```bash
   python .claude/skills/manage-project/scripts/split_episode.py --source {source-file} --episode {N} --target {target-word-count} --anchor "{anchor-text}" --dry-run
   ```
6. After confirming no issues, actually execute (remove `--dry-run`)

---

## Phase 3: Single Episode Preprocessing

**Trigger**: target episode's drafts/ intermediate file does not exist

Choose the subagent based on content_mode:

- **narration** → dispatch `split-narration-segments`
- **drama** → dispatch `normalize-drama-script`

The dispatch prompt includes: project name, project path, episode number, novel file path for the episode, character/clue name list.

---

## Phase 4: JSON Script Generation

**Trigger**: `scripts/episode_{N}.json` does not exist

**Dispatch `create-episode-script` subagent**: pass the project name, project path, and episode number.

---

## Phases 5+6: Character Design + Clue Design (Can Run in Parallel)

The two tasks are independent; **simultaneously dispatch two `generate-assets` subagents** (if both are needed).

### Subagent A — Character Design

**Trigger**: any character is missing character_sheet

```
dispatch `generate-assets` subagent:
  Task type: characters
  Project name: {project_name}
  Project path: projects/{project_name}/
  Items to generate: {list of missing character names}
  Script command:
    python .claude/skills/generate-characters/scripts/generate_character.py --all
  Verification method: re-read project.json and check the character_sheet field for the corresponding characters
```

### Subagent B — Clue Design

**Trigger**: any importance=major clue is missing clue_sheet

```
dispatch `generate-assets` subagent:
  Task type: clues
  Project name: {project_name}
  Project path: projects/{project_name}/
  Items to generate: {list of missing clue names}
  Script command:
    python .claude/skills/generate-clues/scripts/generate_clue.py --all
  Verification method: re-read project.json and check the clue_sheet field for the corresponding clues
```

If only one needs to run, only dispatch the corresponding one.
After both subagents return, merge the summaries and display to the user, then enter inter-phase confirmation.

---

## Phase 7: Storyboard Generation

**Trigger**: any scene is missing a storyboard image

**Dispatch `generate-assets` subagent**:

```
dispatch `generate-assets` subagent:
  Task type: storyboard
  Project name: {project_name}
  Project path: projects/{project_name}/
  Script command:
    python .claude/skills/generate-storyboard/scripts/generate_storyboard.py episode_{N}.json
  Verification method: re-read scripts/episode_{N}.json and check the storyboard_image field for each scene
```

---

## Phase 8: Video Generation

**Trigger**: any scene is missing a video

**Dispatch `generate-assets` subagent**:

```
dispatch `generate-assets` subagent:
  Task type: video
  Project name: {project_name}
  Project path: projects/{project_name}/
  Script command:
    python .claude/skills/generate-video/scripts/generate_video.py episode_{N}.json --episode {N}
  Verification method: re-read scripts/episode_{N}.json and check the video_clip field for each scene
```

---

## Flexible Entry Points

The workflow **does not force starting from the beginning**. Based on status detection results, automatically start from the correct phase:

- "Analyze novel characters" → only execute Phase 1
- "Create Episode 2 script" → start from Phase 2 (if characters already exist)
- "Continue" → status detection finds the first missing item
- Specify a specific phase (e.g., "generate storyboards") → jump directly to that phase

---

## Data Layering

- Complete character/clue definitions are **stored only in project.json**; scripts only reference names
- Stat fields (scenes_count, status, progress) are **computed on read**, not stored
- Episode metadata is **synced on write** when scripts are saved
