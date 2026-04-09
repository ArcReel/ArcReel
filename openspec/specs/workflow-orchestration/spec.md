## ADDED Requirements

### Requirement: manga-workflow Orchestration Skill Must Have Project Status Detection Capability

After the manga-workflow skill is loaded, it SHALL automatically detect the current project's workflow status, determining the current stage based on project.json and the file system.

#### Scenario: New Project With No Characters or Clues
- **WHEN** project.json has empty characters and clues
- **THEN** the orchestration skill determines the current stage is "Global Character/Clue Design" and guides the main agent to dispatch the `analyze-characters-clues` subagent

#### Scenario: Characters Exist But No Drafts Intermediate Files
- **WHEN** project.json has non-empty characters, but the `drafts/episode_{N}/` directory does not exist or is empty
- **THEN** the orchestration skill determines the current stage is "Per-Episode Preprocessing" and guides the main agent to dispatch the appropriate mode's preprocessing subagent

#### Scenario: Drafts Exist But No Scripts
- **WHEN** `drafts/episode_{N}/` intermediate files exist, but `scripts/episode_{N}.json` does not exist
- **THEN** the orchestration skill determines the current stage is "JSON Script Generation" and guides the main agent to dispatch the `create-episode-script` subagent

#### Scenario: Scripts Exist But Missing Assets
- **WHEN** `scripts/episode_{N}.json` exists, but there are missing assets in characters/, storyboards/, or videos/
- **THEN** the orchestration skill determines the current stage is "Asset Generation" and guides the main agent to dispatch the appropriate asset generation subagent

### Requirement: Orchestration Skill Must Define Inter-Stage Dispatch and Confirmation Protocol

After each stage's subagent returns, the main agent SHALL present a result summary to the user and wait for confirmation before proceeding to the next stage.

#### Scenario: Subagent Returns Character/Clue Extraction Results
- **WHEN** the `analyze-characters-clues` subagent completes and returns
- **THEN** the main agent presents a summary of character/clue counts and name lists, uses AskUserQuestion to get user confirmation, and proceeds to the next stage after confirmation

#### Scenario: User Rejects Subagent Results
- **WHEN** the user is unsatisfied with a stage's results
- **THEN** the main agent may choose to re-dispatch the same subagent (with user feedback appended) or allow the user to manually edit and continue

#### Scenario: User Chooses to Skip a Stage
- **WHEN** the user explicitly states they want to skip the current stage
- **THEN** the main agent skips that stage and proceeds directly to the next stage

### Requirement: Orchestration Skill Must Support Flexible Entry Points

manga-workflow SHALL support execution starting from any stage, rather than forcing a start from the beginning.

#### Scenario: User Only Wants Character Design
- **WHEN** the user requests "analyze novel characters" but does not need to create a script
- **THEN** the main agent only dispatches the `analyze-characters-clues` subagent and does not automatically proceed to the next stage after completion

#### Scenario: User Already Has Characters and Wants to Create Script Directly
- **WHEN** project.json already has character/clue definitions and the user requests creation of a specific episode's script
- **THEN** the orchestration skill skips the character/clue extraction stage and proceeds directly to the per-episode preprocessing stage

#### Scenario: User Wants to Resume Previously Interrupted Work
- **WHEN** the user runs /manga-workflow and the project has partially completed work
- **THEN** the orchestration skill automatically locates the last interrupted stage through status detection and continues from that stage

### Requirement: Orchestration Skill Must Correctly Pass Context to Subagents

When the main agent dispatches a subagent, it SHALL pass only the minimum context required for that subagent's task (file paths and key parameters), not large blocks of raw content.

#### Scenario: Dispatch Character/Clue Extraction Subagent
- **WHEN** the main agent dispatches `analyze-characters-clues`
- **THEN** it passes the project name, source directory path, and existing character/clue name lists; the subagent reads the novel text itself

#### Scenario: Dispatch Per-Episode Preprocessing Subagent
- **WHEN** the main agent dispatches a preprocessing subagent
- **THEN** it passes the project name, episode number, content_mode, and character/clue name lists; the subagent reads the corresponding novel text itself

### Requirement: Asset Generation Stage Calls Skills via Subagent

Generation skills (generate-characters, generate-clues, generate-storyboard, generate-video) SHALL be invoked via subagent, not called directly by the main agent.

#### Scenario: Generate Character Design Images
- **WHEN** orchestration enters the character design stage
- **THEN** the main agent dispatches a subagent, which internally calls the generate_character.py script via the Bash tool and returns a generation result summary

#### Scenario: Batch Generate Storyboard Images
- **WHEN** orchestration enters the storyboard generation stage
- **THEN** the main agent dispatches a subagent, which internally calls the generate_storyboard.py script, processes all storyboard images pending generation, and returns a success/failure summary
