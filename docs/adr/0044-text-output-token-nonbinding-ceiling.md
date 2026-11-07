---
status: accepted
---

# Text-output token limits are nonbinding safety valves; structured truncation is an actionable hard error

> This ADR's "do not build a per-model output-limit capability table" and manual episode-planning adjustments (reduce `planning_window_chars` / `planning_max_episodes`) are superseded by "Register model output limits and size episode-planning batches accordingly (revision)" below. The rest remains valid.

Episode planning, script generation, and drama script_plan normalization each lowered `max_output_tokens` to 16000 / 32000 as a **functional budget** to "save money/limit time." Their output volumes, however, are naturally constrained by schemas and content; lowering limits merely cuts off normal output. Large drama whole-batch planning (up to approximately 20 episodes with episode outlines) consistently hit the limit and truncated; Instructor's Pydantic structured path threw `IncompleteOutputException`, surfacing as an undiagnosable error. Reposition `max_output_tokens` as a **nonbinding safety valve** (only preventing pathological model runaway), consolidating the three sites on the single shared constant 64000 in `lib/backends/text_backends/base.py`: high enough not to trigger for normal planning/script output, only pathological oversized batches or user-configured models with insufficient output capability. Make "truncation" a first-class error alongside this: **structured output** (requests with `response_schema`) truncated by the output limit throws typed `TextOutputTruncatedError` (explicitly identifying the current text model and suggesting one with higher output capability), also normalizing Instructor's `IncompleteOutputException` to it. **Free text** (without `response_schema`) retains `warn_if_truncated`'s log-only behavior. Throwing from `generate()` naturally short-circuits the planner's validation-retry loop: resending the same request certain to truncate again is pointless. Episode planning adds its own manual-adjustment guidance (reduce `planning_window_chars` / `planning_max_episodes`; see `docs/adr/0032`).

## Explicitly rejected

- **Passing `None` to use provider defaults**: providers' defaults when omitting `max_tokens` differ and are not guaranteed high (especially with intermediary proxies). It also reverses the logic: to make validating providers reject an unreachable limit, explicitly request a large number; `None` asks for nothing, silently receiving a possibly low default and still truncating without a model-switch signal. With Anthropic compatibility (`max_tokens` required), `None` can also crash directly. Explicit high values are better in all three cases.
- **Automatically shrinking windows/episode counts and retrying on truncation**: silent batch reduction is implicit degradation, contrary to "fail explicitly, let users decide"; smaller windows may also cut story arcs at worse boundaries. Return the choice to users instead (switch to a stronger model or reduce settings themselves).
- **Building a per-model output-limit capability table to set caps dynamically**: the closest fit, but supplying external data for every built-in and custom provider carries maintenance costs far beyond this decision. No such registry currently exists; an explicit high constant suffices.

## Consequences

- Consolidate output limits into one shared source of truth; planning / script / normalize reference the same constant, requiring only one edit to adjust it.
- Structured-output truncation is a hard typed error; free-text truncation remains a soft warning. Behaviors diverge, with impact limited to calls carrying `response_schema`.
- The planner's manual adjustments (`planning_window_chars` / `planning_max_episodes`) require settings writes to accept those values to actually work. They support the two self-service paths after truncation errors (switch models / reduce batch size).
- 64000 is a nonbinding safety valve, not a precise budget: users configuring models with lower output capabilities encounter truncation errors more often, an intentional "fail explicitly and switch models" behavior rather than a defect.

## Register model output limits and size episode-planning batches accordingly (revision)

The system determines episode-planning batch size, without creator adjustment (see `docs/adr/0032`), so it must size batches according to model output capability. Output capability belongs to and is registered by the model: model configuration carries an optional output limit, maintained with the registry for built-ins and entered by users for custom-provider models. Episode planning uses it to determine episodes per batch.
