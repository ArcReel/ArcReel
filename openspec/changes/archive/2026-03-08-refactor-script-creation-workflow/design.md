## Context

### Current State

ArcReel uses the Claude Agent SDK to run a main agent session, dispatching subagents via the Agent tool (formerly Task tool) to handle specific tasks. Currently there are two large subagents (`novel-to-narration-script`, `novel-to-storyboard-script`), each containing 3-4 steps and user confirmation points, plus a static orchestration document `manga-workflow`.

### Claude Code Subagent Mechanism Constraints

Based on [Claude Code official documentation](https://code.claude.com/docs/en/sub-agents.md):

1. **Subagents cannot spawn subagents**: Nested delegation is not feasible; multi-step workflows can only be done through the main agent chain-dispatching subagents
2. **Skill preloading**: The `skills` frontmatter field of a subagent can inject skill content into the subagent context; subagents do not inherit the main agent's skills
3. **Subagent independent context**: A subagent only receives its own system prompt + basic environment information, not the main agent's complete system prompt
4. **Background subagent**: Setting `background: true` lets the subagent run in the background, auto-denying unpre-authorized permissions
5. **Resume mechanism**: Subagents can be resumed by agent ID, retaining complete history
6. **Auto-compaction**: Subagents support auto-compaction, triggered at approximately 95% capacity

### Related Files

| File | Purpose |
|------|---------|
| `agent_runtime_profile/.claude/agents/*.md` | Subagent definitions |
| `agent_runtime_profile/.claude/skills/*/SKILL.md` | Skill definitions |
| `agent_runtime_profile/.claude/settings.json` | Permission and tool configuration |
| `agent_runtime_profile/CLAUDE.md` | Main agent runtime instructions |
| `server/agent_runtime/session_manager.py` | Main agent prompt injection |

## Goals / Non-Goals

**Goals:**

1. Split two multi-step subagents into multiple focused single-task subagents, each doing one thing and returning
2. Extract character/clue extraction from the per-episode workflow and make it a global operation
3. Upgrade manga-workflow from a static document to an orchestration skill with status detection and dispatch logic
4. Establish clear skill (script execution) vs. subagent (reasoning/analysis) boundaries
5. Push generation skill invocations down into subagents to protect main agent context space

**Non-Goals:**

- Modifying the implementations of generate-characters, generate-clues, generate-storyboard, generate-video, compose-video, and other generation skills
- Modifying backend service code (server/agent_runtime/ unchanged)
- Modifying frontend code
- Modifying the project.json data structure
- Implementing a new workflow engine or state machine framework (a skill prompt is sufficient)

## Decisions

### Decision 1: Subagent Splitting Strategy

**Choice**: Split two large agents into 3 focused subagents + leverage existing skills

**Alternatives**:
- A) Keep the two large agents, only modify internal flow → Does not solve the scope mismatch problem
- B) Completely eliminate subagents, execute everything via skills in the main agent → Novel text would pollute main agent context
- C) One subagent per original step (5-6 subagents) → Over-splitting; some steps are too lightweight to justify subagent overhead

**Rationale**: 3 subagents correspond to 3 phases that truly require reasoning (global character analysis, per-episode preprocessing, verification and correction during JSON generation). Generation operations (generate-characters, etc.) already have independent skills/scripts and can be called directly via subagents.

**New subagent list**:

| Subagent | Scope | Input | Output | Preloaded Skills |
|----------|-------|-------|--------|-----------------|
| `analyze-characters-clues` | Global (entire novel) | Novel text + existing chars/clues | Character table + clue table (written to project.json) | — |
| `split-narration-segments` | Per-episode (narration mode) | This episode's novel text + character/clue list | `drafts/episode_{N}/step1_segments.md` | — |
| `normalize-drama-script` | Per-episode (drama mode) | This episode's novel text + character/clue list | `drafts/episode_{N}/step1_normalized_script.md` + `step2_shot_budget.md` | — |
| `create-episode-script` | Per-episode | Episode number + content_mode | scripts/episode_N.json | `generate-script` |

**Creation method**: During implementation, Claude provides the description text for each agent; users create them interactively via the `/agents` command.

### Decision 2: Orchestration Skill Design Approach

**Choice**: Rewrite `manga-workflow` as an orchestration skill with status detection logic (pure prompt-driven, no code framework)

**Alternatives**:
- A) Write a Python state machine framework to orchestrate → Over-engineered, and doesn't match the Claude Agent SDK's prompt-based approach
- B) Split into multiple independent skills, users manually invoke in sequence → Lacks automated orchestration, poor user experience
- C) Hardcode orchestration logic in session_manager.py → Violates the separation principle between agent_runtime_profile and server

**Rationale**: An orchestration skill is essentially a set of decision rules — check status, decide next step, dispatch the right subagent. This is well-suited to be expressed as structured prompts, no code framework needed. After the main agent loads the manga-workflow skill, it acts according to the decision tree in the skill.

**manga-workflow orchestration skill structure**:
```
1. Status detection (read project.json + check drafts/scripts file system)
2. Phase decision tree:
   ├─ Missing characters/clues → dispatch analyze-characters-clues
   ├─ Missing drafts → dispatch preprocess-episode
   ├─ Missing scripts → dispatch create-episode-script
   ├─ Missing design images → dispatch subagent calling generate-characters/clues
   ├─ Missing storyboards → dispatch subagent calling generate-storyboard
   └─ Missing videos → dispatch subagent calling generate-video
3. After each dispatch returns: show summary → user confirms → proceed to next phase
```

### Decision 3: Skill Invocation Method — Preload vs. Runtime Invocation

**Choice**: Hybrid strategy

- `create-episode-script` subagent **preloads** the `generate-script` skill via the `skills` field (because the core task of this subagent is to call this skill)
- Asset generation phase (generate-characters/storyboard/video) is dispatched by the main agent to a general subagent, which at runtime **directly calls** the corresponding Python script via the **Bash tool** (because these skills are essentially script wrappers)

**Alternatives**:
- All skills preloaded → Some subagents would load skill content they don't need, wasting context
- All skills invoked at runtime → Some skills' instructions are critical to subagent behavior and need preloading

**Rationale**: Preloading is suitable for scenarios where "the subagent's behavior is completely defined by the skill"; runtime invocation is suitable for scenarios where "the subagent only needs to execute one script command."

### Decision 4: Trigger Timing for Global Character/Clue Extraction

**Choice**: An explicit first phase in the orchestration flow, also supporting independent invocation

**Design**:
- In `manga-workflow` orchestration, if characters or clues in project.json are empty, automatically enter the global extraction phase
- Users can also invoke it independently at any time (e.g., "analyze all novel characters" → main agent dispatches `analyze-characters-clues`)
- Supports incremental mode: if project.json already has characters, the subagent compares the novel against the existing list and only appends new characters
- Users can specify the analysis range (entire novel / a few chapters / the portion corresponding to a specific episode)

**Alternatives**:
- Only auto-trigger on first episode creation → Returns to the original implicit side-effect mode
- Re-analyze on each episode → Wasteful and may produce inconsistencies

### Decision 5: Should Asset Generation Phase Use Subagents

**Choice**: Asset generation skills (generate-characters/storyboard/video) are called via subagents

**Rationale**:
- These skills produce large amounts of output when executed (generation prompts, API call logs, progress information)
- Pushing down to subagents protects main agent context
- Subagents can handle generation failures, retries, and partial result aggregation logic, returning only the final summary
- Using subagents' `background: true` option, some generation tasks can run in the background

**Implementation**: Create a general `asset-generator` subagent template for the asset generation phase, specifying which skill script to call via parameters. Or just have the main agent dispatch a general-purpose subagent and specify the task in the prompt.

### Decision 6: Script-Based Character/Clue Writing

**Choice**: Create a new `add_characters_clues.py` script wrapping `ProjectManager.add_characters_batch()` + `add_clues_batch()` + `validate_project()`

**Current state**:
- `ProjectManager` already has `add_characters_batch()` and `add_clues_batch()` methods
- But there is no independent CLI script — the current two large agents call these methods via embedded Python code blocks
- Subagents need to call scripts via the Bash tool (not embedded code), so an executable script is required

**Design**:
```bash
# Usage
python .claude/skills/manage-project/scripts/add_characters_clues.py {project_name} \
  --characters '{"character_name": {"description": "...", "voice_style": "..."}}' \
  --clues '{"clue_name": {"type": "prop", "description": "...", "importance": "major"}}'
```

- Input: project name + character/clue data in JSON format (via command-line arguments or stdin)
- Output: write to project.json + call validate_project to validate + print success/failure summary
- **settings.json allowlist**: Need to add `Bash(python .claude/skills/manage-project/scripts/add_characters_clues.py *)` to `permissions.allow`

### Decision 7: Drama Mode Preprocessing in Two Steps — Gemini Generates Markdown + script_generator Generates JSON

**Choice**: The `normalize-drama-script` subagent calls a new script using `gemini-3.1-pro-preview` to generate a Markdown-format normalized script (step1), then the `create-episode-script` subagent uses the existing `script_generator` to convert the Markdown to JSON (step2)

**Two-step process**:

```
Step 1 (normalize-drama-script subagent)
  ├─ Call new script normalize_drama_script.py
  ├─ Script uses gemini-3.1-pro-preview model
  ├─ Input: source/ novel text
  ├─ Output: drafts/episode_{N}/step1_normalized_script.md
  │          drafts/episode_{N}/step2_shot_budget.md
  └─ Subsequent modifications: subagent (Claude) directly edits Markdown files

Step 2 (create-episode-script subagent — existing implementation)
  ├─ Call existing generate_script.py
  ├─ script_generator reads step1_normalized_script.md
  ├─ Uses gemini-3-flash-preview to generate JSON
  └─ Output: scripts/episode_{N}.json
```

**Current state**:
- `ScriptGenerator` already supports drama mode — `build_drama_prompt()` reads `step1_normalized_script.md` as input
- The normalized script (step1) is currently written manually by the agent (Claude) — inefficient and consumes a lot of context
- What's new is only the Gemini automation script for step1; step2 completely reuses the existing implementation

**New script design**:
- Location: `agent_runtime_profile/.claude/skills/generate-script/scripts/normalize_drama_script.py` (placed in the generate-script skill directory, as it's part of the script generation flow)
- Model: `gemini-3.1-pro-preview` (Pro model is better at long-text structured reformatting)
- Output format: Markdown (consistent with the existing `step1_normalized_script.md` format, ensuring the script_generator can consume it seamlessly)
- **settings.json allowlist**: Need to add `Bash(python .claude/skills/generate-script/scripts/normalize_drama_script.py *)`

**Alternatives**:
- Have Claude generate the entire normalized script → Slow, high context overhead, not friendly to long novels
- Use flash model → Pro model has higher quality for long-text understanding and structured reformatting

## Risks / Trade-offs

### [Risk] Subagent Context Transfer Overhead
When dispatching subagents, large amounts of content such as novel text need to be passed in the prompt, consuming subagent context space.

→ **Mitigation**: Subagents read files rather than receiving content via the prompt. Only file paths and key parameters are passed in the prompt; subagents read the required files themselves.

### [Risk] Orchestration Skill Complexity
The manga-workflow orchestration skill needs to detect many states and handle many entry points; pure prompts may become too long.

→ **Mitigation**: Use clear decision tree structure and Markdown formatting; if prompts become too long, split into multiple auxiliary skills.

### [Risk] Changed Interruption Recovery Capability
The original large subagent had a continuous context for natural recovery. After splitting, interruption recovery depends on the orchestration skill re-detecting the state.

→ **Mitigation**: After each phase completes, it is persisted to the file system (project.json, drafts/, scripts/). The orchestration skill recovers via file system state. This is actually more reliable than the original subagent internal recovery — it does not depend on subagent context survival.

### [Trade-off] More Subagents → More API Calls
Originally 1 subagent completed all the work; now 3-4 subagents execute in sequence, with overhead for each dispatch.

→ **Accepted**: The additional API call overhead is manageable; the gains are better architectural isolation, flexibility, and fault tolerance.

### [Trade-off] Main Agent Orchestration Burden
The main agent needs to understand workflow phases, dispatch the right subagent, and pass context.

→ **Mitigation**: The manga-workflow orchestration skill provides clear instructions; the main agent only needs to "act according to the skill's instructions."
