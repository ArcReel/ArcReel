## ADDED Requirements

### Requirement: Each Subagent Template Must Define a Clear Input/Output Contract

Each subagent definition file (`.claude/agents/*.md`) SHALL clearly declare its input parameters, output format, and internally invoked skills/scripts in both the description and system prompt.

#### Scenario: analyze-characters-clues Subagent Contract
- **WHEN** this subagent is dispatched
- **THEN** it receives the project name and source directory path as input, reads the novel text itself, analyzes and extracts the character table and clue table, calls the `add_characters_clues.py` script via Bash to write to project.json, and returns a summary list of characters/clues

#### Scenario: split-narration-segments Subagent Contract
- **WHEN** this subagent is dispatched
- **THEN** it receives the project name, episode number, and the novel text range for that episode as input, splits segments by reading pace (approximately 4 seconds per segment), marks segment_break, saves `drafts/episode_{N}/step1_segments.md`, and returns a summary of segment count and total duration

#### Scenario: normalize-drama-script Subagent Contract
- **WHEN** this subagent is dispatched
- **THEN** it receives the project name and episode number as input; on first generation it calls the `normalize_drama_script.py` script via Bash (using the gemini-3.1-pro-preview model) to generate a normalized script and shot budget, saving `drafts/episode_{N}/step1_normalized_script.md` and `step2_shot_budget.md`, and returns a scene count and shot distribution summary; on subsequent modifications the subagent directly edits the existing Markdown files

#### Scenario: create-episode-script Subagent Contract
- **WHEN** this subagent is dispatched
- **THEN** it receives the project name and episode number as input, preloads the generate-script skill, calls the generate_script.py script to generate JSON, validates the output, and returns a generation result summary

### Requirement: Each Subagent Must Be Designed for Single-Task Focus

Each subagent SHALL complete only one focused task and return; it MUST NOT contain multi-step workflows requiring user confirmation internally.

#### Scenario: Subagent Must Not Use AskUserQuestion for Inter-Step Confirmation
- **WHEN** the subagent executes its focused task
- **THEN** the subagent independently completes all work before returning results, without using AskUserQuestion to wait for user confirmation at intermediate steps

#### Scenario: Subagent May Request Clarification for Ambiguities
- **WHEN** the subagent encounters a critical ambiguity during execution that it cannot independently resolve (e.g., unclear character names in the novel)
- **THEN** the subagent may use AskUserQuestion to request clarification once, but MUST NOT use it for multi-step flow control

### Requirement: Preprocessing Subagents Must Be Independently Defined Per Content Mode

Narration mode and drama animation mode SHALL use their respective independent subagent definitions, rather than sharing a single subagent with parameter-based switching.

#### Scenario: Narration Mode Uses split-narration-segments
- **WHEN** the project's content_mode is "narration"
- **THEN** the orchestration skill guides the main agent to dispatch the `split-narration-segments` subagent to perform segment splitting (by reading pace, marking segment_break, marking dialogue segments), outputting step1_segments.md

#### Scenario: Drama Mode Uses normalize-drama-script
- **WHEN** the project's content_mode is "drama"
- **THEN** the orchestration skill guides the main agent to dispatch the `normalize-drama-script` subagent to perform script normalization (structured scenes, time, location, characters) + shot budgeting (estimated shot count, marking segment_break), outputting step1_normalized_script.md and step2_shot_budget.md

### Requirement: create-episode-script Subagent Must Preload the generate-script Skill

The `create-episode-script` subagent's frontmatter SHALL preload the `generate-script` skill via the `skills` field.

#### Scenario: Skill Content Is Injected When Subagent Starts
- **WHEN** the subagent is dispatched
- **THEN** the complete content of the generate-script skill is already in the subagent context, and the subagent can call the generate_script.py script per the skill's instructions

#### Scenario: Subagent Validates Generation Results
- **WHEN** the generate_script.py script finishes executing
- **THEN** the subagent verifies that scripts/episode_{N}.json exists and passes data validation; if there are errors it attempts to correct and regenerate

### Requirement: Remove Old Multi-Step Subagent Definitions

`novel-to-narration-script.md` and `novel-to-storyboard-script.md` SHALL be deleted and replaced with new focused subagent templates.

#### Scenario: Old Agent Files Are Removed
- **WHEN** the refactoring is complete
- **THEN** the `agent_runtime_profile/.claude/agents/` directory no longer contains `novel-to-narration-script.md` or `novel-to-storyboard-script.md`

#### Scenario: New Agent Files Are in Place
- **WHEN** the refactoring is complete
- **THEN** the `agent_runtime_profile/.claude/agents/` directory contains the four focused subagent definitions: `analyze-characters-clues.md`, `split-narration-segments.md`, `normalize-drama-script.md`, and `create-episode-script.md`

### Requirement: Must Provide a CLI Script for Writing Characters/Clues

SHALL provide an `add_characters_clues.py` script that wraps `ProjectManager.add_characters_batch()` + `add_clues_batch()` + `validate_project()` for subagents to call via the Bash tool.

#### Scenario: Batch Add Characters and Clues
- **WHEN** the subagent calls `add_characters_clues.py` via Bash with character/clue data in JSON format
- **THEN** the script writes characters/clues to project.json, calls validate_project to validate, and prints a success/failure summary

#### Scenario: Existing Characters Are Automatically Skipped
- **WHEN** a character name in the input already exists in project.json
- **THEN** the script skips that character (without overwriting existing data) and marks it as "already exists, skipped" in the output

#### Scenario: Script Is Allowed in settings.json
- **WHEN** the refactoring is complete
- **THEN** `settings.json`'s `permissions.allow` contains the Bash execution permission for this script

### Requirement: Must Provide a Gemini Generation Script for Drama Mode Normalized Scripts

SHALL provide a `normalize_drama_script.py` script that uses the `gemini-3.1-pro-preview` model to convert novel source text into a Markdown-format normalized script and shot budget.

#### Scenario: First-Time Generation of a Normalized Script
- **WHEN** the `normalize-drama-script` subagent calls this script
- **THEN** the script reads the source/ novel text, calls gemini-3.1-pro-preview to generate a structured normalized script, and outputs `drafts/episode_{N}/step1_normalized_script.md` and `step2_shot_budget.md`

#### Scenario: Output Format Is Compatible With script_generator
- **WHEN** the normalized script generation is complete
- **THEN** the output `step1_normalized_script.md` format is consistent with the input format expected by the existing `ScriptGenerator.build_drama_prompt()`, ensuring `generate_script.py` can consume it seamlessly

#### Scenario: Script Is Allowed in settings.json
- **WHEN** the refactoring is complete
- **THEN** `settings.json`'s `permissions.allow` contains the Bash execution permission for this script
