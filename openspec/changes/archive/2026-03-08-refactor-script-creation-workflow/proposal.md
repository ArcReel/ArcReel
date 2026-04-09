## Why

The current script creation system has three structural problems:

**1. Scope Mismatch — Global Tasks Are Locked Into Per-Episode Workflows**

Character/clue design is fundamentally a **whole-series-level** operation (analyzing the entire novel, establishing a cross-episode reusable character system), but it has been embedded in a per-episode subagent workflow (narration Step 2, drama Step 3). Consequences:
- Characters are registered during the first episode creation; subsequent episodes rely on "auto-skip if exists" — this is an implicit side effect rather than an explicit global design
- Users cannot first do a complete character/clue planning pass and then create scripts episode by episode
- If the user only wants to process a single chapter of the novel, they are still forced through the entire workflow

**2. Multi-Step Interaction Confirmations Stuffed Into the Subagent — Violates Subagent Usage Patterns**

The core value of a subagent is **protecting the main agent's context space**: offloading the processing of large amounts of raw material (the entire novel) and skill invocations to the subagent, so the main agent only receives refined results. But the current two subagents each contain 3-4 steps requiring user confirmation, leading to:
- Subagents occupy execution state for long periods; intermediate products (step1, step2...) pile up in the subagent context
- User confirmation is done via AskUserQuestion inside the subagent, but the review capabilities users need (viewing project files, comparing changes) are more natural in the main agent
- If the subagent context approaches window limits or encounters an error, the entire multi-step process must restart from the beginning

The correct pattern: **each subagent accepts one focused task, completes it independently (can call skills/scripts internally), and returns the result**. Confirmations between multiple steps are done by the main agent between subagent invocations.

**3. Missing Orchestration Layer — Unreasonable Responsibility Division Between Skills and Agents**

- `manga-workflow` should be the orchestration hub, but it is only a static Markdown document
- The two agents mix orchestration (step control), reasoning (text analysis), and execution (calling generate-script skill)
- There is no clear skill/agent boundary principle: what should use a subagent (requires reasoning + protects main context), and what should be called directly in the main agent

## What Changes

Following the subagent-driven-development design philosophy — **each subagent one focused task, subagents can call skills internally, main agent only does orchestration and user confirmation** — the entire skill/agent system is restructured.

### Architectural Layering Principles

```
Main Agent (Orchestration Layer — Very Lightweight)
  │  Holds only: project status summary + user conversation history
  │  Responsibilities: status detection, flow decisions, user confirmation, dispatch subagents
  │
  ├─ dispatch via Agent tool ──→  Subagent (Execution Layer — Focused Task)
  │                                 Holds: raw materials needed for the task (novel text, etc.)
  │                                 Responsibilities: reasoning/analysis + calling skills/scripts
  │                                 │
  │                                 ├─ preload skills (via frontmatter `skills` field)
  │                                 ├─ invoke Skill tool / Bash ──→  Script execution
  │                                 │    generate-script, generate-characters...
  │                                 │    Deterministic operations, call APIs / run ffmpeg
  │                                 │
  │                                 ├─ ⚠️ Cannot spawn child subagents (SDK constraint)
  │                                 │
  │                                 └─ Returns refined results to Main Agent
  │
  └─ Receives result summary, shows to user, obtains confirmation
```

**Key constraints** (from Claude Code official documentation):
- Subagents **cannot** spawn other subagents — only the main agent can dispatch subagents
- Skills can be **preloaded** into a subagent via the `skills` field (content directly injected into the subagent context)
- Skills can also run in subagents via the `context: fork` mechanism
- Skills are called by subagents, not the main agent — the large amounts of prompts/logs from skill execution remain in the subagent context; the main agent only sees a summary

### Core Changes

- **Split two large agents into multiple focused subagent templates** (`agents/` directory):
  - `analyze-characters-clues` — global character/clue extraction (analyzes the entire novel or a specified range), internally calls ProjectManager to write to project.json
  - `split-narration-segments` — narration mode segment splitting (per-episode), returns step1 intermediate file
  - `normalize-drama-script` — drama mode normalization + shot budget (per-episode), returns step1+step2 intermediate files
  - `create-episode-script` — unified JSON script generation (per-episode), internally calls the generate-script skill, returns generation results
  - Each template defines a clear **input/output contract**: what it receives, what it returns, what it calls internally
  - **BREAKING**: delete `novel-to-narration-script.md` and `novel-to-storyboard-script.md`

- **Upgrade manga-workflow to a true orchestration skill**:
  - Has status detection capability (reads project.json + checks file system)
  - Defines clear phase transitions and dispatch strategies
  - After each subagent returns, the main agent reviews the summary, shows it to the user, obtains confirmation, then dispatches the next subagent
  - Supports flexible entry points: can only do global character design, can only do a single-episode script, can continue from any phase
  - Asset generation phases (generate-characters/storyboard/video) also execute by dispatching subagents, not by the main agent calling skills directly

- **Establish skill/agent boundary principles**:
  - **Subagent (Task)** = tasks requiring large context or reasoning → protect main agent context
  - **Skill (called inside a subagent)** = deterministic script execution → API calls, file generation
  - **Main Agent direct invocation** = limited to lightweight operations (reading project status, simple file operations)

### New Workflow Sequence

```
Main Agent (Orchestration — Very Lightweight)    Subagents (Focused Execution)
───────────────────────                          ─────────────────────

[Phase 0] Detect Project Status
├─ Read project.json summary
├─ Determine what's missing
└─ Decide entry phase

[Phase 1: Global Character/Clue Design]
dispatch → ──────────────────────────── → analyze-characters-clues
  Pass in: novel text + existing chars     Analyze the entire novel
                                           Extract character table + clue table
                                           Call ProjectManager to write to project.json
                                           Return: character/clue summary
← ──────────────────────────────────── ←
Show summary, user confirms ✓

[Phase 2: Per-Episode Preprocessing]
dispatch → ──────────────────────────── → split-narration-segments
  Pass in: this episode's novel text        (or normalize-drama-script)
  Pass in: character/clue name list          Split/normalize + shot budget
                                            Save drafts/ intermediate files
                                            Return: segment/scene summary
← ──────────────────────────────────── ←
Show summary, user confirms ✓

[Phase 3: JSON Script Generation]
dispatch → ──────────────────────────── → create-episode-script
  Pass in: episode number + mode params    Internally calls generate-script skill
                                           Validates output
                                           Return: generation result summary
← ──────────────────────────────────── ←
Show result, user confirms ✓

[Phase 4+: Asset Generation]
dispatch → ──────────────────────────── → subagent calls /generate-characters
dispatch → ──────────────────────────── → subagent calls /generate-clues
dispatch → ──────────────────────────── → subagent calls /generate-storyboard
dispatch → ──────────────────────────── → subagent calls /generate-video
  Each subagent internally calls the corresponding skill
  Returns summary to main agent
```

### Key Design Decisions

1. **Context isolation**: Novel text only enters the subagent context; the main agent never loads the raw novel. Subagents return refined summaries (tables, statistics, status), protecting the main agent's context space.

2. **Decoupling global design from per-episode creation**: Character/clue extraction is an independent phase that can be executed alone. Users can first do a whole-book character planning pass, then create scripts episode by episode. Incremental mode is also supported — new characters discovered in new episodes can be appended.

3. **Skill invocations pushed down to subagents**: Generation skills (generate-characters, generate-storyboard, etc.) are called by subagents, not the main agent. The large amounts of prompt text and generation logs produced by skill execution remain in the subagent context; the main agent only receives a summary like "generated N character design images."

4. **Two modes share global steps**: Character/clue extraction and JSON generation use the same subagent template in both modes. Only the preprocessing step uses different templates due to mode differences.

## Capabilities

### New Capabilities
- `workflow-orchestration`: Orchestration skill mechanism — manga-workflow upgraded to a state-aware orchestration skill that defines phase transitions, subagent dispatch strategies, context passing protocols (only pass necessary information), interruption recovery, and flexible entry points
- `focused-subagent-tasks`: Focused subagent task template system — the original multi-step agents are split into independent task prompt templates, each defining input/output contracts, internally callable skill lists, and execution constraints
- `global-character-clue-extraction`: Global character/clue extraction — independent of per-episode workflows, supports both full-book analysis and incremental append modes, internally calls ProjectManager to complete data writing

### Modified Capabilities
(No existing specs need modification)

## Impact

- **Agent definitions**: 2 large agents deleted, 3-4 focused task prompt templates added (`agent_runtime_profile/.claude/agents/`)
- **Skill files**: `manga-workflow/SKILL.md` rewritten from a static document to an orchestration skill with status detection and dispatch logic
- **Main agent prompt**: `_PERSONA_PROMPT` in `session_manager.py` gains orchestration awareness and workflow phase understanding
- **agent_runtime_profile CLAUDE.md**: Workflow documentation and skill/agent boundary explanation updated
- **Existing generation skills not affected**: `generate-script`, `generate-characters`, `generate-clues`, `generate-storyboard`, `generate-video`, `compose-video` remain unchanged (only the caller changes from main agent to subagent)
- **Backend services not affected**: Subagents in `server/agent_runtime/` are still dispatched via the Task tool
- **Frontend not affected**
- **Data model unchanged**: `project.json` structure unchanged
