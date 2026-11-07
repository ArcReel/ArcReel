---
paths:
  - "lib/backends/**"
  - "lib/custom_provider/**"
  - "lib/config/**"
  - "lib/billing/**"
  - "lib/agent/agent_provider_catalog.py"
  - "lib/prompts/prompt_builders*.py"
  - "agent_runtime_profile/**"
  - "docs/api-docs/**"
---

# Provider capabilities and contracts

## Capability data

### Each capability dimension has one source; never duplicate its values elsewhere

Capability sources differ by dimension (`docs/adr/0104`):

- Model facts belong to catalog entries: supported durations, resolutions, duration/resolution/reference-image constraints, text/image capability flags, image/video reference-image limits, and supported video inputs (text-to-video, first frame, end frame, reference audio), audio-segment counts, and total audio duration limits. Evaluate input capabilities against both catalog and backend declarations: intersect support flags and take the smaller numeric limit. Evaluate images only in `lib/backends/image_capabilities.py` and videos only in `lib/backends/video_capabilities.py`; backends do not restate these by model name. Missing `supported_durations` is an error, without implicit fallback (`docs/adr/0018`).
- Backend facts are declared in code by backends or endpoints, sharing the source used for request construction and execution decisions (`docs/adr/0054`, `docs/adr/0056`). They include implemented video-input paths, per-request reference-image/audio counts, prompt-length limits, and audio-track form.
- Channel restrictions use only user-owned capability overrides on provider models; do not modify entries.

Duplicates include constants repeating a tier, callers/settings UIs inventing defaults when source values are absent, backends repeating catalog duration or resolution checks, and templates in `agent_runtime_profile/` or `lib/prompts/` hardcoding duration/resolution tiers instead of receiving them from orchestration. Provider/catalog updates eventually make copies disagree: UI choices differ from actual requests, or the Agent plans durations the execution path cannot request.

Exceptions:

- Explicit ADR exceptions: Vidu endpoint duration correction in `docs/adr/0018`; conservative fallback for unknown models and duration-prefill presets for new entries in `docs/adr/0104` and `docs/adr/0105`.
- Transitional duplicates guarded by consistency tests, with the deletion issue identified in the PR.
- Empirical pacing guidance in prompts (such as an average 3–5 seconds per storyboard item) is not a capability tier.

## Resumption

### Resume submitted jobs with frozen submission context; distinguish new-call checks from resumption

Resumption retrieves already-submitted results without generating again, but shares resolution, assembly, and request chains with new calls. Decisions based on current state also affect resumes: model-retirement gates block paid jobs submitted before shutdown, and path overrides matching current base paths fail to recognize resumed requests. Both prevent retrieval of paid results.

When adding pre-call gates or current-configuration request rewriting to a shared chain, the resume entry point explicitly declares the call's nature. Under that declaration, evaluate the identity and context frozen in the payload at submission; new submissions omit it and retain normal checks. See `VideoLaneRequest.submitted_job` in `server/services/tasks/generation_context.py`, passed to `assemble_backend`, and submission-time base matching in `lib/custom_provider/path_overrides.py`. Add a resume case covering lifecycle or configuration changes after submission.
