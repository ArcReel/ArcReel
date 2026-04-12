# Agent Skill Orchestration Optimization Design

## Background

A comprehensive review of `agent_runtime_profile/` found 11 issues requiring fixes, involving accuracy errors, architectural defects, information redundancy, and path inconsistencies. This design follows Option B (introduce Asset Generation Agent + information deduplication).

## Issue List

| # | Severity | Issue | Location |
|---|----------|-------|----------|
| 1 | P0 | Agent name reference error: `novel-to-narration-script` / `novel-to-storyboard-script` should be `split-narration-segments` / `normalize-drama-script` | `CLAUDE.md:51` |
| 2 | P0 | Ghost skill permission: `edit-script-items` has allow rules in settings.json but the skill does not exist | `settings.json:29` |
| 3 | P0 | `add_characters_clues.py` call inconsistency: agent definition has an extra non-existent `{project_name}` positional argument | `analyze-characters-clues.md:60` |
| 4 | P1 | Stages 5-8 say "dispatch general-purpose subagent" but there is no corresponding agent definition | `manga-workflow/SKILL.md:134` |
| 6 | P1 | Persona Prompt overlaps with CLAUDE.md information (language specification, orchestration mode defined in two places) | `session_manager.py:316` vs `CLAUDE.md` |
| 7 | P1 | Stage 5 (character design) and Stage 6 (clue design) are mutually independent and can be dispatched in parallel but currently run serially | `manga-workflow/SKILL.md` |
| 8 | P2 | Content mode table is duplicated in three places: CLAUDE.md, references/content-modes.md, session_manager.py | Multiple locations |
| 9 | P2 | Script call path assumptions are inconsistent: some use `cd projects/{name} && python ../../.claude/skills/...`, others use `python .claude/skills/...` | Multiple SKILL.md files |
| 10 | P2 | `--segment-ids` and `--scene-ids` both appear in generate-storyboard SKILL.md without explaining they are aliases | `generate-storyboard/SKILL.md:23-24` |
| 11 | P2 | Reference path is wrong: written as `references/content-modes.md` but actually located at `.claude/references/content-modes.md` | Multiple SKILL.md files |

## Design

### 1. Create `agents/generate-assets.md`

Create a unified asset generation subagent to replace the vague "general-purpose subagent."

**Design philosophy**: controller constructs precise task lists; subagent focuses on execution and returns structured status (following the subagent-driven-development pattern).

**Agent definition structure**:

```yaml
name: generate-assets
description: "Unified asset generation subagent. Receives a task list (containing asset type, script commands, verification method), executes generation scripts sequentially, and returns a structured summary. Used for character design, clue design, storyboard images, and video generation."
```

**Workflow:**
1. Read `{project_path}/project.json` to understand project status
2. Execute each script command provided by the main agent in sequence
3. On single item failure, record error and continue with subsequent items without blocking the whole
4. Check results using the verification method specified by the main agent
5. Return structured status

**Return Status Protocol:**
- **DONE**: all succeeded
- **DONE_WITH_CONCERNS**: all completed but with anomalies
- **PARTIAL**: some succeeded, some failed
- **BLOCKED**: unable to execute (preconditions not met)

**Return summary format:**

```markdown
## Asset Generation Complete

**Status**: DONE / PARTIAL / BLOCKED
**Task Type**: {type}

| Item | Status | Notes |
|------|--------|-------|
| {item1} | ✅ Success | |
| {item2} | ❌ Failed | {error reason} |
```

**Core constraints:**
- Do not perform additional operations not requested by the main agent
- Do not wait for user confirmation; return immediately upon completion
- Task types limited to: `characters` / `clues` / `storyboard` / `video`

### 2. Rewrite manga-workflow Stages 5-8

#### Workflow Stage Overview (Corrected)

1. Project setup
2. Global character/clue design → dispatch `analyze-characters-clues`
3. Episode planning → main agent executes directly
4. Single episode preprocessing → dispatch `split-narration-segments` (narration) or `normalize-drama-script` (drama)
5. JSON script generation → dispatch `create-episode-script`
6. Character design + Clue design → **parallel** dispatch two `generate-assets` (when mutually independent)
7. Storyboard image generation → dispatch `generate-assets`
8. Video generation → dispatch `generate-assets`

> Original Stage 9 (compositing) has been removed. After video generation, users export to CapCut draft from the web interface.

#### Stage 6: Character Design + Clue Design (Parallelizable)

The two tasks are mutually independent; **dispatch two `generate-assets` subagents simultaneously**.

**Subagent A — Character Design** (triggered when: characters are missing character_sheet):

```
dispatch `generate-assets` subagent:
  Task type: characters
  Project name: {project_name}
  Project path: projects/{project_name}/
  Items to generate: {list of missing character names}
  Script commands:
    python .claude/skills/generate-characters/scripts/generate_character.py --all
  Verification method: re-read project.json, check character_sheet field for corresponding characters
```

**Subagent B — Clue Design** (triggered when: importance=major clues are missing clue_sheet):

```
dispatch `generate-assets` subagent:
  Task type: clues
  Project name: {project_name}
  Project path: projects/{project_name}/
  Items to generate: {list of missing clue names}
  Script commands:
    python .claude/skills/generate-clues/scripts/generate_clue.py --all
  Verification method: re-read project.json, check clue_sheet field for corresponding clues
```

If only one needs to be executed, dispatch only the corresponding one.
After both subagents return, combine summaries to display to the user, then proceed to inter-stage confirmation.

#### Stage 7: Storyboard Image Generation

```
dispatch `generate-assets` subagent:
  Task type: storyboard
  Project name: {project_name}
  Project path: projects/{project_name}/
  Script commands:
    python .claude/skills/generate-storyboard/scripts/generate_storyboard.py episode_{N}.json
  Verification method: re-read scripts/episode_{N}.json, check storyboard_image field for each scene
```

#### Stage 8: Video Generation

```
dispatch `generate-assets` subagent:
  Task type: video
  Project name: {project_name}
  Project path: projects/{project_name}/
  Script commands:
    python .claude/skills/generate-video/scripts/generate_video.py episode_{N}.json --episode {N}
  Verification method: re-read scripts/episode_{N}.json, check video_clip field for each scene
```

#### Status Detection Update

Corrected status detection checklist (stages renumbered):

1. characters/clues are empty? → **Stage 1** (character/clue extraction)
2. Target episode `source/episode_{N}.txt` does not exist? → **Stage 2** (episode planning)
3. Target episode drafts/ intermediate files do not exist? → **Stage 3** (preprocessing)
4. `scripts/episode_{N}.json` does not exist? → **Stage 4** (JSON script)
5. Characters missing character_sheet? → **Stage 5** (character design) — can run in parallel with Stage 6
6. importance=major clues missing clue_sheet? → **Stage 6** (clue design) — can run in parallel with Stage 5
7. Scenes missing storyboard images? → **Stage 7** (storyboard)
8. Scenes missing videos? → **Stage 8** (video)
9. All complete → workflow ends, guide user to export CapCut draft from the web interface

### 3. Information Deduplication

#### CLAUDE.md Changes

**Delete** the content mode comparison table at lines 40-51 (including wrong agent names), replace with a one-line reference:

```markdown
> Content mode detailed specifications can be found in `.claude/references/content-modes.md`.
```

**Correct** the architecture diagram (approximately lines 77-87):
- Delete the `general-purpose subagent` line
- Add the `generate-assets` line
- Delete old incorrect agent name references

Corrected:

```
Main Agent (orchestration layer — extremely lightweight)
  │  Only holds: project status summary + user conversation history
  │  Responsibilities: status detection, process decisions, user confirmation, dispatch subagent
  │
  ├─ dispatch → analyze-characters-clues     Global character/clue extraction
  ├─ dispatch → split-narration-segments     Narration mode segment splitting
  ├─ dispatch → normalize-drama-script       Drama mode script normalization
  ├─ dispatch → create-episode-script        JSON script generation (pre-loads generate-script skill)
  └─ dispatch → generate-assets              Asset generation (characters/clues/storyboard/video)
```

**Correct** the available Skills table: delete the compose-video row.

**Correct** the workflow overview:
- Delete original Stage 9 (compositing) and Stage 10
- Mark Stages 5+6 as "parallelizable"
- Add note at the end: after video generation, export CapCut draft from the web interface

#### Persona Prompt (session_manager.py) Streamlining

Remove content duplicated from CLAUDE.md, keeping only what is unique to the Persona Prompt:

```python
_PERSONA_PROMPT = """\
## Identity

You are the ArcReel Agent, a professional AI video content creation assistant. Your responsibility is to transform novels into publishable short video content.

## Behavioral Guidelines

- Proactively guide users through the video creation workflow, rather than just passively answering questions
- When facing uncertain creative decisions, present options to the user with recommendations, rather than deciding unilaterally
- For multi-step tasks, use TodoWrite to track progress and report to the user
- You are the user's video production partner: professional, friendly, and efficient"""
```

Removed content:
- "Must respond to users in Chinese" (already covered in CLAUDE.md key principles)
- The entire "Orchestration Mode" section (already covered in CLAUDE.md architecture section)
- "Use the decision tree in /manga-workflow skill" (already in CLAUDE.md)

### 4. Path and Naming Consistency

#### Unified Script Call Paths

**Principle**: all script calls must use relative path format allowed by settings.json allow rules:

```bash
python .claude/skills/{skill}/scripts/{script}.py {args}
```

Do not use the `cd projects/{name} && python ../../.claude/skills/...` pattern (does not match allow rules).

**Files to modify:**

| File | Modification |
|------|-------------|
| `manga-workflow/SKILL.md` | Remove `cd` prefix from Stage 2 peek/split commands |
| `analyze-characters-clues.md` | Remove incorrect `{project_name}` positional argument, use `python .claude/skills/manage-project/scripts/add_characters_clues.py --characters ...` |
| `normalize-drama-script.md` | Remove `cd` prefix from script calls |
| `create-episode-script.md` | Remove `cd` prefix from script calls |
| `generate-assets.md` (new) | dispatch prompt templates use relative paths directly |

#### Fix `--segment-ids` / `--scene-ids` Ambiguity

Add clarification in `generate-storyboard/SKILL.md`:

```markdown
> `--scene-ids` and `--segment-ids` are synonymous aliases (the latter is the conventional name in narration mode); they have the same effect. The following uniformly uses `--scene-ids`.
```

Unify all examples to use `--scene-ids`.

#### Fix Reference Paths

Change `references/content-modes.md` references in each SKILL.md to the full relative path `.claude/references/content-modes.md`.

Affected files:
- `manga-workflow/SKILL.md:17`
- `generate-characters/SKILL.md` (references the prompt language section)
- `generate-clues/SKILL.md`
- `generate-storyboard/SKILL.md`
- `generate-video/SKILL.md`

### 5. Clean Up settings.json

Delete the ghost skill line at `settings.json:29`:

```diff
- "Bash(python .claude/skills/edit-script-items/scripts/edit_script_items.py:*)",
```

## Change File Summary

| Operation | File | Corresponding Issue |
|-----------|------|---------------------|
| **Create** | `.claude/agents/generate-assets.md` | #4, #7 |
| **Modify** | `CLAUDE.md` | #1, #8 |
| **Modify** | `.claude/skills/manga-workflow/SKILL.md` | #4, #7, #8, #9, #11 |
| **Modify** | `.claude/agents/analyze-characters-clues.md` | #3, #9 |
| **Modify** | `.claude/agents/create-episode-script.md` | #9 |
| **Modify** | `.claude/agents/normalize-drama-script.md` | #9 |
| **Modify** | `.claude/skills/generate-storyboard/SKILL.md` | #10, #11 |
| **Modify** | `.claude/skills/generate-characters/SKILL.md` | #11 |
| **Modify** | `.claude/skills/generate-clues/SKILL.md` | #11 |
| **Modify** | `.claude/skills/generate-video/SKILL.md` | #11 |
| **Modify** | `.claude/settings.json` | #2 |
| **Modify** | `server/agent_runtime/session_manager.py` | #6 |

## Out of Scope

- Error recovery protocol (#5) — explicitly excluded by user
- Eval coverage extension (#12-15) — future iteration
- Deleting the compose-video skill itself — retained as an independent skill, only removed from the workflow
