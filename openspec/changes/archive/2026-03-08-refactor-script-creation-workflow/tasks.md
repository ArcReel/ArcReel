## 1. New Script Development

- [x] 1.1 Create the `add_characters_clues.py` script: wraps `ProjectManager.add_characters_batch()` + `add_clues_batch()` + `validate_project()`; supports receiving character/clue data in JSON format via command-line arguments or stdin
- [x] 1.2 Create the `normalize_drama_script.py` script: uses the `gemini-3.1-pro-preview` model to read source/ novel text and generate a Markdown-format normalized script (`step1_normalized_script.md`) and shot budget (`step2_shot_budget.md`); the output format must be compatible with `ScriptGenerator.build_drama_prompt()`
- [x] 1.3 Add Bash execution permissions for both new scripts in `settings.json`'s `permissions.allow`
- [x] 1.4 Verify `generate-script` compatibility with both content_mode types (narration reads step1_segments.md; drama reads step1_normalized_script.md); fix any issues found

## 2. Create Focused Subagents

- [x] 2.1 Provide the description for the `analyze-characters-clues` agent; user creates it via the `/agents` command (global character/clue extraction, analyzes the entire novel, calls add_characters_clues.py via Bash to write to project.json, returns structured summary)
- [x] 2.2 Provide the description for the `split-narration-segments` agent; user creates it via the `/agents` command (narration mode segment splitting, approximately 4 seconds per segment by reading pace, marks segment_break, saves drafts/ intermediate files, returns summary)
- [x] 2.3 Provide the description for the `normalize-drama-script` agent; user creates it via the `/agents` command (on first generation calls normalize_drama_script.py using Gemini 3.1 Pro to generate a normalized script; subsequent modifications by the agent directly editing Markdown; returns summary)
- [x] 2.4 Provide the description for the `create-episode-script` agent; user creates it via the `/agents` command (preloads generate-script skill, calls generate_script.py to generate JSON, validates output, returns summary)
- [x] 2.5 After the user completes creating all 4 agents, review the generated agent files to ensure frontmatter and system prompts meet requirements

## 3. Rewrite the Orchestration Skill

- [x] 3.1 Rewrite `manga-workflow/SKILL.md`: status detection logic (read project.json + check drafts/scripts/characters/storyboards/videos file system)
- [x] 3.2 Define the phase decision tree in manga-workflow: missing characters → dispatch analyze-characters-clues; missing drafts → dispatch split-narration-segments or normalize-drama-script (by content_mode); missing scripts → dispatch create-episode-script; missing assets → dispatch asset generation subagent
- [x] 3.3 Define inter-phase confirmation protocol in manga-workflow: after each subagent returns, show summary; use AskUserQuestion to get user confirmation; support redo/skip/continue
- [x] 3.4 Define context passing rules in manga-workflow: what parameters to pass when dispatching each subagent (project name, episode number, content_mode, file paths)

## 4. Asset Generation Subagent Adaptation

- [x] 4.1 Define the dispatch method for asset generation subagents: determine whether to create a dedicated agent template or use a general-purpose subagent + specific task prompt
- [x] 4.2 Add asset generation phase dispatch logic in manga-workflow (each phase of generate-characters, generate-clues, generate-storyboard, generate-video)

## 5. Old Agent Cleanup and Documentation Update

- [x] 5.1 Delete `agent_runtime_profile/.claude/agents/novel-to-narration-script.md`
- [x] 5.2 Delete `agent_runtime_profile/.claude/agents/novel-to-storyboard-script.md`
- [x] 5.3 Update `agent_runtime_profile/CLAUDE.md`: replace workflow description (change from description of two large agents to new orchestration skill + focused subagent architecture description)
- [x] 5.4 Update `agent_runtime_profile/CLAUDE.md`: add skill/agent boundary principle description
- [x] 5.5 Update `agent_runtime_profile/.claude/settings.json`: confirm that new subagents' tool permission configuration is correct

## 6. Main Agent Prompt Enhancement

- [x] 6.1 Update `_PERSONA_PROMPT` in `server/agent_runtime/session_manager.py`: add orchestration awareness — understand workflow phases, know when to dispatch which subagent
- [x] 6.2 Evaluate whether `_build_append_prompt()` needs to inject current workflow phase status (optional optimization)

## 7. Integration Testing and Validation

- [x] 7.1 End-to-end validation: new project from scratch through the complete manga-workflow flow (character extraction → preprocessing → JSON generation)
- [x] 7.2 Validate flexible entry: project with existing characters goes directly to the per-episode preprocessing phase
- [x] 7.3 Validate incremental mode: incremental append behavior for character/clue extraction when creating the second episode
- [x] 7.4 Validate narration mode uses split-narration-segments subagent, drama mode uses normalize-drama-script subagent (including Gemini script invocation)
- [x] 7.5 Validate that normalize_drama_script.py output format can be correctly consumed by generate_script.py
