---
name: create-episode-script
description: "Single-episode JSON script generation subagent. Use cases: (1) intermediate files in drafts/episode_N/ already exist and the final JSON script needs to be generated, (2) user requests generating a JSON script for a specific episode, (3) manga-workflow orchestration enters the JSON script generation phase. Receives project name and episode number, calls generate_script.py to generate JSON, validates the output, and returns a generation result summary."
skills:
  - generate-script
---

Your task is to call the generate-script skill to generate the final JSON-format script.

## Task Definition

**Input**: the main agent provides in the prompt:
- Project name (e.g., `my_project`)
- Episode number (e.g., `1`)

**Output**: after generating `scripts/episode_{N}.json`, return a generation result summary

## Core Principles

1. **Call the script directly**: follow the generate-script skill instructions to call generate_script.py
2. **Validate output**: confirm the JSON file is generated and correctly formatted
3. **Return upon completion**: complete all work independently then return; do not wait for user confirmation

## Workflow

### Step 1: Confirm Prerequisites

Use the Read tool to read `projects/{project-name}/project.json`, confirming:
- The content_mode field (narration or drama)
- characters and clues already have data

Use the Glob tool to confirm the intermediate file exists:
- narration mode: `projects/{project-name}/drafts/episode_{N}/step1_segments.md`
- drama mode: `projects/{project-name}/drafts/episode_{N}/step1_normalized_script.md`

If the intermediate file does not exist, report an error and state which preprocessing subagent needs to be run first.

### Step 2: Call generate_script.py to Generate JSON Script

Run in the project directory:
```bash
python .claude/skills/generate-script/scripts/generate_script.py --episode {N}
```

Wait for execution to complete. If it fails, check the error message and try to fix or report the issue.

### Step 3: Validate Generated Output

Use the Read tool to read the generated `projects/{project-name}/scripts/episode_{N}.json`,
confirming:
- File exists and is valid JSON
- Contains episode and content_mode fields
- narration mode: segments array is not empty
- drama mode: scenes array is not empty

### Step 4: Return Summary

```
## JSON Script Generation Complete

**Project**: {project-name}  **Episode N**

| Metric | Value |
|--------|------|
| Content mode | narration/drama |
| Total segments/scenes | XX |
| Total duration | X min X sec |
| Generation model | gemini-3-flash-preview |

**File saved**: `scripts/episode_{N}.json`

✅ Data validation passed

Next step: the main agent can dispatch the asset generation subagent (character design sheets, storyboards, etc.).
```

If generation fails:
```
## JSON Script Generation Failed

**Error**: {error description}

**Recommendations**:
- {fix suggestions based on the error type}
```
