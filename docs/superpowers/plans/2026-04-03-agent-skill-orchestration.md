# Agent Skill Orchestration Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 11 issues in agent_runtime_profile/ — accuracy errors, architectural defects, information redundancy, and path inconsistencies.

**Architecture:** Create a new `generate-assets` agent to replace the vague "general-purpose subagent", rewrite the dispatch logic for manga-workflow phases 5-8 with parallel support, unify script invocation paths to the settings.json allow rule format, and eliminate information duplication between CLAUDE.md and the Persona Prompt.

**Tech Stack:** Markdown / YAML frontmatter / Python string literal (session_manager.py)

**Spec:** `docs/superpowers/specs/2026-04-03-agent-skill-orchestration-design.md`

---

## File Map

| Action | File | Responsibility |
|------|------|------|
| **Create** | `agent_runtime_profile/.claude/agents/generate-assets.md` | Unified asset generation subagent definition |
| **Modify** | `agent_runtime_profile/.claude/settings.json` | Remove ghost skill permission lines |
| **Modify** | `agent_runtime_profile/CLAUDE.md` | Fix agent names, remove duplicate content pattern table, update architecture diagram and workflow |
| **Modify** | `agent_runtime_profile/.claude/skills/manga-workflow/SKILL.md` | Rewrite phases 5-8 dispatch, unify paths, fix reference citations |
| **Modify** | `agent_runtime_profile/.claude/agents/analyze-characters-clues.md` | Fix script invocation style, unify paths |
| **Modify** | `agent_runtime_profile/.claude/agents/create-episode-script.md` | Unify script paths |
| **Modify** | `agent_runtime_profile/.claude/agents/normalize-drama-script.md` | Unify script paths |
| **Modify** | `agent_runtime_profile/.claude/skills/generate-storyboard/SKILL.md` | Document alias relationship, fix reference paths |
| **Modify** | `agent_runtime_profile/.claude/skills/generate-characters/SKILL.md` | Fix reference paths |
| **Modify** | `agent_runtime_profile/.claude/skills/generate-clues/SKILL.md` | Fix reference paths |
| **Modify** | `agent_runtime_profile/.claude/skills/generate-video/SKILL.md` | Fix reference paths |
| **Modify** | `server/agent_runtime/session_manager.py` | Streamline Persona Prompt |

---

### Task 1: Clean Up Ghost Permissions in settings.json

**Files:**
- Modify: `agent_runtime_profile/.claude/settings.json:29`

**Fixes:** #2

- [ ] **Step 1: Delete the edit-script-items line**

Delete line 29 in `agent_runtime_profile/.claude/settings.json`:

```diff
       "Bash(python .claude/skills/compose-video/scripts/compose_video.py:*)",
-      "Bash(python .claude/skills/edit-script-items/scripts/edit_script_items.py:*)",
       "Bash(ffmpeg:*)",
```

- [ ] **Step 2: Verify JSON format is valid**

Re-read the file with the Read tool to confirm the JSON format is complete and the allow array does not contain `edit-script-items`.

- [ ] **Step 3: Commit**

```
git add agent_runtime_profile/.claude/settings.json
git commit -m "fix: remove non-existent edit-script-items permission rule from settings.json"
```

---

### Task 2: Create generate-assets Agent Definition

**Files:**
- Create: `agent_runtime_profile/.claude/agents/generate-assets.md`

**Fixes:** #4, #7

- [ ] **Step 1: Create the agent definition file**

Write `agent_runtime_profile/.claude/agents/generate-assets.md`:

```markdown
---
name: generate-assets
description: "Unified asset generation subagent. Receives a task list (asset type, script commands, verification method), executes generation scripts in sequence, and returns a structured summary. Used for character design, clue design, storyboard, and video generation."
---

You are a focused asset generation executor. Your sole responsibility is to execute scripts according to the task list provided by the primary agent and report results.

## Task Definition

**Input**: The primary agent will provide the following in the dispatch prompt:
- Project name and project path
- Task type (characters / clues / storyboard / video)
- Script commands (one or more, formatted to match settings.json allow rules)
- Verification method

**Output**: Return structured status and summary upon completion

## Workflow

### Step 1: Read Project Status

Use the Read tool to read the project's `project.json`, recording:
- Project name, content mode, visual style
- Existing character/clue/script status (for verification use)

### Step 2: Execute Script Commands

Execute each command provided by the primary agent in sequence:
- Use the Bash tool to run each command
- If a command fails, **record the error message and continue executing subsequent commands**
- Do not skip, do not independently decide to skip any commands
- Do not execute additional commands not listed by the primary agent

### Step 3: Verify Results

Check generation results according to the verification method specified by the primary agent (usually re-reading project.json or the script JSON to check field updates).

### Step 4: Return Structured Status

Return one of the following statuses:

- **DONE**: All commands executed successfully, verification passed
- **DONE_WITH_CONCERNS**: All completed but with anomalies (e.g., generation results may have quality issues)
- **PARTIAL**: Partially successful, partially failed
- **BLOCKED**: Unable to execute (prerequisites not met, e.g., missing project.json or dependency files)

Summary format:

```
## Asset Generation Complete

**Status**: {DONE / DONE_WITH_CONCERNS / PARTIAL / BLOCKED}
**Task Type**: {characters / clues / storyboard / video}

| Item | Status | Notes |
|------|------|------|
| {Item 1} | ✅ Success | |
| {Item 2} | ❌ Failed | {error reason} |

{If DONE_WITH_CONCERNS, list concerns}
{If BLOCKED, explain the blocking reason and suggestions}
```

## Notes

- Do not perform additional operations not requested by the primary agent
- Do not wait for user confirmation; return immediately upon completion
- A single command failure does not block the overall flow; report collectively after all commands complete
```

- [ ] **Step 2: Verify the file can be discovered**

Use the Glob tool to confirm `agent_runtime_profile/.claude/agents/generate-assets.md` exists in the agents directory.

- [ ] **Step 3: Commit**

```
git add agent_runtime_profile/.claude/agents/generate-assets.md
git commit -m "feat: create generate-assets unified asset generation subagent definition"
```

---

### Task 3: Fix CLAUDE.md

**Files:**
- Modify: `agent_runtime_profile/CLAUDE.md:40-51, 77-87, 103-118, 120-136`

**Fixes:** #1, #8

- [ ] **Step 1: Replace the content mode comparison table with a reference**

Replace the complete content mode section in `CLAUDE.md` lines 38-66 (the `## Content Mode` heading and two sub-sections) with a brief reference:

```diff
-## Content Mode
-
-The system supports two content modes, switched via the `content_mode` field in `project.json`:
-
-| Dimension | Narration + Visuals Mode (default) | Drama Animation Mode |
-|------|----------------------|-------------|
-| content_mode | `narration` | `drama` |
-| Content form | Strictly preserves original novel text, no adaptation | Novel adapted into a script |
-| Data structure | `segments` array | `scenes` array |
-| Default duration | 4s/segment | 8s/scene |
-| Dialogue source | Post-production dubbing (original novel text) | Actor dialogue |
-| Video Prompt | Only includes character dialogue (if any), no narration | Includes dialogue, narration, sound effects |
-| Aspect ratio | 9:16 vertical (storyboard + video) | 16:9 horizontal |
-| Agent used | `novel-to-narration-script` | `novel-to-storyboard-script` |
-
-### Narration + Visuals Mode (default)
-
-(section omitted)
-
-### Drama Animation Mode
-
-(section omitted)
+## Content Mode
+
+The system supports two content modes (Narration + Visuals / Drama Animation), switched via the `content_mode` field in `project.json`.
+
+> For detailed specifications (aspect ratio, duration, data structure, preprocessing agent, etc.) see `.claude/references/content-modes.md`.
```

- [ ] **Step 2: Update the architecture diagram**

Replace the `general-purpose subagent` line in the architecture diagram with `generate-assets`:

```diff
   ├─ dispatch → create-episode-script        JSON script generation (preloads generate-script skill)
-  └─ dispatch → general-purpose subagent     Asset generation (calls scripts)
+  └─ dispatch → generate-assets              Asset generation (character/clue/storyboard/video)
```

- [ ] **Step 3: Update the available Skills table**

Remove the compose-video row from the available Skills table:

```diff
 | generate-video | `/generate-video` | Generate video |
-| compose-video | `/compose-video` | Post-processing |
```

- [ ] **Step 4: Update the workflow overview**

Rewrite the workflow overview section to reflect the new phase numbers and parallel dispatch:

```diff
 `/manga-workflow` orchestration skill advances automatically through the following phases (waits for user confirmation after each phase):

 1. **Project setup**: Create project, upload novel, generate project overview
 2. **Global character/clue design** → dispatch `analyze-characters-clues` subagent
 3. **Episode planning** → primary agent directly executes peek+split (manage-project toolset)
 4. **Per-episode preprocessing** → dispatch `split-narration-segments` (narration) or `normalize-drama-script` (drama)
 5. **JSON script generation** → dispatch `create-episode-script` subagent
-6. **Character design** → dispatch asset generation subagent (calls generate_character.py)
-7. **Clue design** → dispatch asset generation subagent (calls generate_clue.py)
-8. **Storyboard generation** → dispatch asset generation subagent (calls generate_storyboard.py)
-9. **Video generation** → dispatch asset generation subagent (calls generate_video.py)
-10. **Final composition** → dispatch asset generation subagent (calls compose_video.py)
+6. **Character design + Clue design** (parallelizable) → dispatch `generate-assets` subagent
+7. **Storyboard generation** → dispatch `generate-assets` subagent
+8. **Video generation** → dispatch `generate-assets` subagent

-The workflow supports **flexible entry**: status detection automatically locates the first incomplete phase, supports resumption after interruption.
+The workflow supports **flexible entry**: status detection automatically locates the first incomplete phase, supports resumption after interruption.
+After video generation is complete, users can export as a CapCut draft from the web interface.
```

- [ ] **Step 5: Verify**

Read the modified CLAUDE.md with the Read tool and confirm:
- No remaining `novel-to-narration-script` / `novel-to-storyboard-script`
- No remaining `general-purpose subagent`
- No remaining `compose-video` / `Final composition`
- Architecture diagram contains `generate-assets`

- [ ] **Step 6: Commit**

```
git add agent_runtime_profile/CLAUDE.md
git commit -m "fix: correct CLAUDE.md agent names, remove duplicate content mode table, update workflow"
```

---

### Task 4: Rewrite manga-workflow/SKILL.md

**Files:**
- Modify: `agent_runtime_profile/.claude/skills/manga-workflow/SKILL.md:39-175`

**Fixes:** #4, #7, #9, #11

- [ ] **Step 1: Fix reference paths**

Line 17:

```diff
-> Content mode specifications (aspect ratio, duration, etc.) see `references/content-modes.md`.
+> Content mode specifications (aspect ratio, duration, etc.) see `.claude/references/content-modes.md`.
```

- [ ] **Step 2: Update the status detection checklist**

Update the status detection section (lines 39-53) to the 8-phase version:

```diff
 1. characters/clues empty? → **Phase 1**
 2. Target episode source/episode_{N}.txt does not exist? → **Phase 2**
 3. Target episode drafts/ intermediate file does not exist? → **Phase 3**
    - narration: `drafts/episode_{N}/step1_segments.md`
    - drama: `drafts/episode_{N}/step1_normalized_script.md`
 4. scripts/episode_{N}.json does not exist? → **Phase 4**
-5. Any character missing character_sheet? → **Phase 5**
-6. Any importance=major clue missing clue_sheet? → **Phase 6**
-7. Any scene missing storyboard? → **Phase 7**
-8. Any scene missing video? → **Phase 8**
-9. All complete → **Phase 9**
+5. Any character missing character_sheet? → **Phase 5** (can run in parallel with Phase 6)
+6. Any importance=major clue missing clue_sheet? → **Phase 6** (can run in parallel with Phase 5)
+7. Any scene missing storyboard? → **Phase 7**
+8. Any scene missing video? → **Phase 8**
+9. All complete → Workflow ends, guide user to export CapCut draft from the web interface
```

- [ ] **Step 3: Fix Phase 2 script paths**

Change the Phase 2 script invocation from `cd` mode to relative path:

```diff
-3. Call `peek_split_point.py` to show context around the split point:
-   ```bash
-   cd projects/{project_name} && python ../../.claude/skills/manage-project/scripts/peek_split_point.py --source {source_file} --target {target_word_count}
-   ```
+3. Call `peek_split_point.py` to show context around the split point:
+   ```bash
+   python .claude/skills/manage-project/scripts/peek_split_point.py --source {source_file} --target {target_word_count}
+   ```
```

Similarly fix the two invocations of `split_episode.py` (dry-run and actual execution):

```diff
-   cd projects/{project_name} && python ../../.claude/skills/manage-project/scripts/split_episode.py --source {source_file} --episode {N} --target {target_word_count} --anchor "{anchor_text}" --dry-run
+   python .claude/skills/manage-project/scripts/split_episode.py --source {source_file} --episode {N} --target {target_word_count} --anchor "{anchor_text}" --dry-run
```

```diff
-6. After confirming correct, execute for real (remove `--dry-run`)
+6. After confirming correct, execute for real (remove `--dry-run`)
```

- [ ] **Step 4: Rewrite Phases 5-9 as Phases 5-8**

Replace the original phases 5-9 (approximately lines 134-155) with the following:

```markdown
## Phase 5+6: Character Design + Clue Design (Parallelizable)

The two tasks are independent of each other; **dispatch two `generate-assets` subagents simultaneously** (if both are needed).

### Subagent A — Character Design

**Trigger**: Any character is missing character_sheet

```
dispatch `generate-assets` subagent:
  Task type: characters
  Project name: {project_name}
  Project path: projects/{project_name}/
  Items to generate: {list of missing character names}
  Script commands:
    python .claude/skills/generate-characters/scripts/generate_character.py --all
  Verification: Re-read project.json, check the character_sheet field of corresponding characters
```

### Subagent B — Clue Design

**Trigger**: Any importance=major clue is missing clue_sheet

```
dispatch `generate-assets` subagent:
  Task type: clues
  Project name: {project_name}
  Project path: projects/{project_name}/
  Items to generate: {list of missing clue names}
  Script commands:
    python .claude/skills/generate-clues/scripts/generate_clue.py --all
  Verification: Re-read project.json, check the clue_sheet field of corresponding clues
```

If only one needs to be executed, dispatch only the corresponding one.
After both subagents return, merge summaries to display to the user, then proceed to inter-phase confirmation.

---

## Phase 7: Storyboard Generation

**Trigger**: Any scene is missing a storyboard

**dispatch `generate-assets` subagent**:

```
dispatch `generate-assets` subagent:
  Task type: storyboard
  Project name: {project_name}
  Project path: projects/{project_name}/
  Script commands:
    python .claude/skills/generate-storyboard/scripts/generate_storyboard.py episode_{N}.json
  Verification: Re-read scripts/episode_{N}.json, check the storyboard_image field of each scene
```

---

## Phase 8: Video Generation

**Trigger**: Any scene is missing a video

**dispatch `generate-assets` subagent**:

```
dispatch `generate-assets` subagent:
  Task type: video
  Project name: {project_name}
  Project path: projects/{project_name}/
  Script commands:
    python .claude/skills/generate-video/scripts/generate_video.py episode_{N}.json --episode {N}
  Verification: Re-read scripts/episode_{N}.json, check the video_clip field of each scene
```
```

- [ ] **Step 5: Update the flexible entry section**

Delete or update content related to Phase 9.

- [ ] **Step 6: Verify**

Read the modified SKILL.md with the Read tool and confirm:
- No remaining script invocations with `cd projects/` prefix
- No remaining `general-purpose subagent`
- No remaining `compose-video` / Phase 9 / Phase 10
- All reference paths are `.claude/references/content-modes.md`
- Phases 5+6 are marked as parallelizable

- [ ] **Step 7: Commit**

```
git add agent_runtime_profile/.claude/skills/manga-workflow/SKILL.md
git commit -m "feat: rewrite manga-workflow phases 5-8, introduce generate-assets dispatch and parallel support"
```

---

### Task 5: Fix Agent Definitions (3 Files)

**Files:**
- Modify: `agent_runtime_profile/.claude/agents/analyze-characters-clues.md:60`
- Modify: `agent_runtime_profile/.claude/agents/create-episode-script.md:42`
- Modify: `agent_runtime_profile/.claude/agents/normalize-drama-script.md:38`

**Fixes:** #3, #9

- [ ] **Step 1: Fix analyze-characters-clues.md script invocation**

`add_characters_clues.py` has no project name positional argument; it auto-detects from cwd. Fix the invocation example around line 60:

```diff
-```bash
-python .claude/skills/manage-project/scripts/add_characters_clues.py {project_name} \
-  --characters '{
+```bash
+python .claude/skills/manage-project/scripts/add_characters_clues.py \
+  --characters '{
```

Ensure the `{project_name}` positional argument is removed; the remaining `--characters` and `--clues` flags stay unchanged.

- [ ] **Step 2: Fix create-episode-script.md script path**

Change the script invocation around line 42 from `cd` mode to relative path:

```diff
-```bash
-cd projects/{project_name} && python ../../.claude/skills/generate-script/scripts/generate_script.py --episode {N}
-```
+```bash
+python .claude/skills/generate-script/scripts/generate_script.py --episode {N}
+```
```

- [ ] **Step 3: Fix normalize-drama-script.md script path**

Change the script invocation around line 38 from `cd` mode to relative path:

```diff
-```bash
-cd projects/{project_name} && python ../../.claude/skills/generate-script/scripts/normalize_drama_script.py --episode {N} --source source/episode_{N}.txt
-```
+```bash
+python .claude/skills/generate-script/scripts/normalize_drama_script.py --episode {N} --source source/episode_{N}.txt
+```
```

- [ ] **Step 4: Verify**

Use the Grep tool to search for `cd projects/` in the `agent_runtime_profile/.claude/agents/` directory to confirm no remaining instances.

- [ ] **Step 5: Commit**

```
git add agent_runtime_profile/.claude/agents/analyze-characters-clues.md
git add agent_runtime_profile/.claude/agents/create-episode-script.md
git add agent_runtime_profile/.claude/agents/normalize-drama-script.md
git commit -m "fix: unify script invocation paths in agent definitions, fix add_characters_clues.py arguments"
```

---

### Task 6: Fix Skill SKILL.md Files (4 Files)

**Files:**
- Modify: `agent_runtime_profile/.claude/skills/generate-storyboard/SKILL.md`
- Modify: `agent_runtime_profile/.claude/skills/generate-characters/SKILL.md`
- Modify: `agent_runtime_profile/.claude/skills/generate-clues/SKILL.md`
- Modify: `agent_runtime_profile/.claude/skills/generate-video/SKILL.md`

**Fixes:** #10, #11

- [ ] **Step 1: generate-storyboard — Add alias documentation and fix reference paths**

After the `--segment-ids` example in the command-line usage section, add documentation:

```diff
 cd projects/{project_name} && python ../../.claude/skills/generate-storyboard/scripts/generate_storyboard.py script.json --segment-ids E1S01 E1S02
 # or
 cd projects/{project_name} && python ../../.claude/skills/generate-storyboard/scripts/generate_storyboard.py script.json --scene-ids E1S01 E1S02
```

Replace with unified use of `--scene-ids` and add documentation:

```markdown
# Regenerate for multiple scenes
python .claude/skills/generate-storyboard/scripts/generate_storyboard.py script.json --scene-ids E1S01 E1S02
```

> `--scene-ids` and `--segment-ids` are synonymous aliases (the latter is the conventional name in narration mode); they have the same effect. The following uniformly uses `--scene-ids`.

Also change all script invocation paths from `cd projects/...` mode to `python .claude/skills/...` relative paths.

Fix reference path citations (if there are `references/content-modes.md` references, change to `.claude/references/content-modes.md`).

- [ ] **Step 2: generate-characters — Fix reference paths**

Change `references/content-modes.md` references in SKILL.md to `.claude/references/content-modes.md`.

Also change script invocation paths from `cd projects/...` mode to `python .claude/skills/...` relative paths.

- [ ] **Step 3: generate-clues — Fix reference paths**

Same as Step 2, fix reference paths and script invocation paths.

- [ ] **Step 4: generate-video — Fix reference paths**

Same as Step 2, fix reference paths and script invocation paths.

- [ ] **Step 5: Verify**

Use the Grep tool to search in the `agent_runtime_profile/.claude/skills/` directory:
- `references/content-modes.md` (without `.claude/` prefix) → should have no matches
- `cd projects/` → should have no matches
- `../../.claude/skills/` → should have no matches

- [ ] **Step 6: Commit**

```
git add agent_runtime_profile/.claude/skills/generate-storyboard/SKILL.md
git add agent_runtime_profile/.claude/skills/generate-characters/SKILL.md
git add agent_runtime_profile/.claude/skills/generate-clues/SKILL.md
git add agent_runtime_profile/.claude/skills/generate-video/SKILL.md
git commit -m "fix: unify reference paths and script invocation paths in skill SKILL.md files"
```

---

### Task 7: Streamline session_manager.py Persona Prompt

**Files:**
- Modify: `server/agent_runtime/session_manager.py:316-335`

**Fixes:** #6

- [ ] **Step 1: Streamline _PERSONA_PROMPT**

Replace lines 316-335 with a streamlined version (removing content duplicated with CLAUDE.md):

```diff
     _PERSONA_PROMPT = """\
 ## Identity

 You are the ArcReel Agent, a professional AI video content creation assistant. Your responsibility is to convert novels into publishable short video content.

 ## Behavioral Guidelines

-- Responses to users must be in Chinese
 - Proactively guide users through the video creation workflow, rather than passively answering questions
 - When faced with uncertain creative decisions, present options to the user and offer recommendations, rather than deciding independently
 - For multi-step tasks, use TodoWrite to track progress and report to users
-- You are the user's video production partner — professional, friendly, and efficient
-
-## Orchestration Mode
-
-You are the orchestration hub, completing each phase task by dispatching focused subagents:
-
-- Context-heavy tasks such as novel analysis and script generation are completed by dispatching subagents; subagents read required files themselves — do not directly call the Read tool to read them
-- Each subagent completes one focused task and returns a summary; you are responsible for displaying results and obtaining user confirmation
-- Use the decision tree in the /manga-workflow skill to determine which subagent to dispatch next"""
+- You are the user's video production partner — professional, friendly, and efficient"""
```

- [ ] **Step 2: Verify**

Read the modified `session_manager.py:316-340` with the Read tool and confirm:
- `_PERSONA_PROMPT` no longer contains "in Chinese"
- `_PERSONA_PROMPT` no longer contains the "Orchestration Mode" section
- `_PERSONA_PROMPT` no longer contains "manga-workflow"
- The closing `"""` of the string is correctly placed

- [ ] **Step 3: Commit**

```
git add server/agent_runtime/session_manager.py
git commit -m "refactor: streamline Persona Prompt, remove language and orchestration rules duplicated in CLAUDE.md"
```

---

## Plan Self-Review

**Spec coverage:**
- #1 Agent name errors → Task 3 Step 1 (remove table with incorrect names) + Step 2 (update architecture diagram) ✅
- #2 Ghost skill permissions → Task 1 ✅
- #3 add_characters_clues.py invocation inconsistency → Task 5 Step 1 ✅
- #4 Phases 5-8 without agent definitions → Task 2 (create) + Task 4 (rewrite workflow) ✅
- #6 Persona Prompt overlap → Task 7 ✅
- #7 Parallel dispatch → Task 4 Step 4 ✅
- #8 Content mode table duplicated three times → Task 3 Step 1 ✅
- #9 Path inconsistencies → Task 4 Step 3 + Task 5 Steps 1-3 + Task 6 Steps 1-4 ✅
- #10 --segment-ids/--scene-ids ambiguity → Task 6 Step 1 ✅
- #11 Reference paths → Task 4 Step 1 + Task 6 Steps 1-4 ✅

**Placeholder scan:** No TBD/TODO. All steps contain specific diffs or operation instructions.

**Type consistency:** The document uniformly uses `generate-assets` (not `generate_assets` or other variants).
