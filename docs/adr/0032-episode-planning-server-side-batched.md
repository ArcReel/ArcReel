---
status: accepted
---

# Server-side text-model tools plan episodes in rolling batches; drama ledgers are episode outlines

The old main-agent flow mechanically ran multiple steps (peek reading units → convert to characters → choose anchors → dry-run → split). Semantic-free split points could not preserve hooks; reliability depended on agent workflow adherence, and per-episode rolling planning could not see next-episode content. Give split decisions to LLMs and flows to deterministic tools: a plan server MCP tool reads source windows (tens of thousands of characters), calls the project-configured text model, and plans all complete-arc episodes within the window (titles/hooks/ranges). Commit only through each batch's last high-confidence split, retaining tails for subsequent batches. Strong Pydantic schemas and mechanical anchor-existence/range-continuity validation trigger automatic tool-layer retries carrying previous schema/anchor/continuity errors for targeted fixes rather than identical resubmission. Main agents call once for summaries without performing splits. Drama ledgers become episode outlines (story beats, hooks, next-episode teasers + soft material ranges); narration ledgers stay thin (exact splits + hooks).

Window character counts and batch episode limits are not creator parameters; Web, Agent, and project settings cannot override them. The system derives batch counts from registered output limits of current text models (see `docs/adr/0044`). Windows must reveal cross-episode arcs; beyond that, size mainly affects call counts and episodes per batch, committing only complete arcs and retaining tails.

## Adjust existing planning: replanning and manual splitting

Episode planning has two methods: AI planning and manual splitting (creators directly mark whole-source boundaries). Both create episodes "split from whole source," sharing continuity/stale rules. Creator-facing adjustments have no reset; `reset_episode_planning` is a low-level service command.

- Initial/continuation planning overwrite nothing and commit directly. Continue from the end of the last split episode by source position, deriving starts from ledgers without separate cursors.
- AI can plan unsplit source before the last split episode (before the first or in gaps), ending at the gap's end. Otherwise follow continuation semantics: replace no episodes and commit directly.
- AI adjustments are replanning: produce candidates from a chosen episode without changing ledgers; only creator acceptance after candidate/change-summary review replaces them together. Discarding changes nothing. Source edits require no replanning; remap affected split episodes under `docs/adr/0097`.
- Stopped replanning leaves completed portions as candidates, available to continue/accept/discard. Acceptance replaces all split episodes from the start onward; subsequent continuation begins at candidate endings.
- Candidates are always new episodes with new IDs (see `docs/adr/0096`): differing splits give no basis for attaching old artifacts by position. Replaced old episodes with artifacts become sourceless, stale episodes retaining artifacts (see `docs/adr/0062`), left for creators to delete; artifact-free ones are removed directly.
- Manual splitting supplies creator-chosen results directly to ledgers without candidates. It changes existing episode boundaries and preserves identity: both sides retain IDs when boundaries move; splitting preserves the first part and gives the second a new ID; merging with the next preserves the first and handles the second as replaced old episodes. Changed-range episodes with artifacts become stale. Removing splits handles artifact-bearing episodes as replaced old ones.

### Broadcast order after split-episode changes

Split episodes retain source order; creators arrange others (see `docs/adr/0096`). Planning changes use the following placement rules, naming every moved episode in acceptance lists/change summaries:

- Initial/continuation new episodes immediately follow the last split episode, before trailing episodes from other sources; without split episodes, append to broadcast order. Gap-planned episodes enter between bounding split episodes by source position (before the first when the gap leads). Other-source episodes there use the replanning anchor rules below, treating new episodes as candidates.
- Manual splitting moves no other episodes: second split parts immediately follow first parts; episodes between merged episodes remain after the merged episode; boundary moves change no placements.
- Replanning proceeds with independent-source/sourceless episodes inside the range, from any start including episode 1. Place them with the story: anchor to the source end of their nearest preceding split episode in broadcast order (source start if none); on acceptance, place after the first candidate whose source end is not before the anchor, the episode reaching that story point. Anchors not after replanning starts place before all candidates; those after candidate ends (stopped replanning) place after the last candidate. Preserve relative order among episodes sharing placement. Compute anchors only on acceptance, without storage.
- If anchor split episodes lack source ranges (legacy splitting, requiring replanning from episode 1; see `docs/adr/0031`), anchors are invalid; place those episodes after all new split episodes in original relative order.
- Retired episodes (replaced through replanning, removed splits, deleted source files or entire source passages, with artifacts converting them to sourceless old split episodes) move to broadcast-order ends in original relative order.

## Considered Options

- Planning SubAgents (Claude reads windows and produces ledgers): multi-turn iteration adds no value here (hook quality depends on prompts/global views), prompt-based adherence is hard to unit-test, and tokens use agent credentials.
- Whole-book planning at once: front-loads million-character costs; users often test early episodes then change styles, invalidating much of the ledger.
- Improved per-episode splitting: lacks cross-episode pacing context, limits hook quality, and still depends on rolling state.
- Direct drama scripts in one step: conflicts with batches (dozens of episodes cannot be written at once); any split adjustment rewrites whole scripts.
- Separate planning cursors: gaps and file addition/deletion/reordering make cursors point to absent files/wrong positions and disagree with ledgers.
- Manual-only gaps or replanning from gaps: replanning replaces every subsequent split episode, including unchanged later files.
- Resetting and planning together: destructive; failed/worse planning cannot restore old ledgers.
- Reusing old episode numbers and reattaching stale old artifacts by position: differing splits make artifacts unrelated to new content.
- Rejecting replanning with other-source episodes in range: forces creators to delete/move intentionally arranged episodes before adjusting splits.
- Preserving other-source episodes' numeric broadcast positions: changed episode counts detach positions from story context.
- Keeping retired episodes in place and appending continuation episodes: retired episodes retell source already covered by new episodes, duplicating story inside main sequences.
- Fixed batches with truncation errors asking creators to switch models: the system chose oversized output, so creators should not bear repair, and many cannot switch models.
- Automatically shrinking after truncation: wastes full-output calls each run and hides the root model-capability cause.
- Agent-only window overrides: Web users must open Agent after truncation, creating unequal capabilities; project knobs also miss model capability as root cause.

## Consequences

- Delete `peek_split_point.py` / `split_episode.py` / `_text_utils.py`; rewrite manage-project SKILL.md splitting sections and CLAUDE.*.md phase 2 instructions as single-tool calls.
- V1 quality assurance = global window view + explicit hook fields + candidate review, without an LLM-judge loop.
- Planning quality depends on configured text-model capability; schemas/mechanical checks ensure only formal correctness.
- Ledger hooks/next-episode teasers enter script-generation inputs and persist as episode-level script JSON metadata (hooks appear in final scenes). Inputs include current/next-episode outlines, choosing the immediately next broadcast episode and supplying only its title if no outline exists. Heavier scene/shot-size/voiceover structural upgrades are separate, outside this decision.
