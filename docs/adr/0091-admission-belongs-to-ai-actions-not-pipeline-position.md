---
status: accepted
---

# Admission belongs to AI actions, not pipeline positions: workflow state describes content, availability, and suggested next steps

Workflow status formerly followed a fixed prerequisite chain: source exists, inventory current, episodes planned, script plan confirmed, formal script, pending authoring, sheets, storyboard images, videos. The first unmet condition became the sole current/next step. Agents executed only matching actions and Web guidance showed only that step. Source, inventory, and confirmation protected specific AI actions but became project/episode prerequisites. Non-source creation survived only through exceptions: over ten `mode != "ad"` checks, with no path for source-free creation or handwritten Start from blank scripts. Most action entries did not enforce that order anyway, so state and real admission already diverged.

Decision: **admission belongs to AI actions, not content or pipeline position.**

- Content (episodes/scripts/video units/assets) observes minimal document constraints such as unique IDs and correct skeletons. Empty is a legal intermediate state; content editing has no pipeline prerequisites.
- Each action owns an input-based predicate: whole source for episode planning, episode source for script planning, pending formal entries for authoring, registered assets with sheets for generation (see `docs/adr/0073`). Workflow state and action entries share it: disabled reasons, Agent rejections, and next-action suggestions use the same conclusion, following `docs/adr/0056`.
- State describes three things: current content/artifacts, every action's availability/reasons, and one suggested next action with alternatives at forks (AI script planning versus Start from blank). Only whole-project unusability blocks: migration verdicts or unreadable project data/manifests, never pipeline position.
- Workflow-plan order is presentation only; steps describe their own content, not waiting for a previous step.
- Agents execute explicitly requested actions when admitted, otherwise explain why. For Continue, follow suggestions and ask users at forks.

## Considered Options

- **Keep one current step but relax prerequisites**: smallest change, but cannot express forks/manual paths and keeps accumulating mode exceptions.
- **Action lists without suggestions**: removes Agent Continue routing and Web primary-entry emphasis.
- **Separate predicates aligned through tests**: recreates the state-versus-entry mismatch this decision removes.

## Consequences

- Two admission layers: status computes structural admission without external I/O for button availability/reasons; quote/video-fact submission admission stays at preview/submit. Whole-batch admission remains (`docs/adr/0061`). State aggregates item actions by episode, while individual buttons use the same predicates locally.
- Scattered ad exceptions disappear; unavailable action kinds are explicitly not applicable, not skipped in a chain.
- Empty formal scripts are legal. Nonempty checks remain only in AI output acceptance; state suggests adding storyboard items rather than blocking.
- Project summaries have no current stage because multi-episode projects have no uniform one; show episode progress and actionable reminders.
- Stale usable artifacts never become suggested next actions; describe their status locally with update entries (`docs/adr/0062`).
