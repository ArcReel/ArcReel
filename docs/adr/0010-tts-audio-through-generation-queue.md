---
status: accepted
---

> For TTS scope and generation cardinality, see `docs/adr/0089`: generate only in TTS-delivery projects, once per voiceover-bearing unit, during editing. This ADR's audio-generation-queue and synchronous-backend decisions remain valid.

# TTS (audio media type) uses GenerationQueue/Worker like image/video; backends stay synchronous, unlike inline text

ArcReel media generation fans out by `media_type`: image/video use **GenerationQueue + GenerationWorker** (slots per provider×media_type, progress/cancellation/resumption/orphan handling), while text is **synchronous inline calling** (`TextGenerator` = TextBackend + UsageTracker, without queueing/tasks; workers use only `for media_type in ("image","video")`). Introducing narration dubbing (TTS) requires deciding whether audio follows text (synchronous inline) or image/video (queued).

The initial preference was synchronous inline, "like text calls, without concurrency control," because TTS backends **are synchronous one-shot POSTs** (like `text_backends`, no submit/poll, returning in seconds), seemingly closer to text. But **both Web and Agent require batch generation**, and the key fact is **narration generation cardinality (one per segment, N per episode, batchable/regeneratable) matches image/video, not text's "once per episode."** Inline batches require serial frontend orchestration, browser-tab-bound progress, no task panel, and no cancellation/resumption/cross-device continuity, splitting experience from image/video. Queues already serve those media precisely for "long batch tasks + progress + cancellation + resumption."

**Audio uses GenerationQueue/Worker like image/video**: one segment enqueues one task, batches enqueue N, reusing `/api/v1/tasks`. Retain **intentional asymmetry**: audio **backends remain synchronous one-shot** (like `text_backends`, not video submit→poll→resume); workers call them directly after claiming and mark terminal states on quick return. Thus **queueing follows generation cardinality (per-segment batches), not whether backends are asynchronous**. This ADR records that distinction for future readers wondering "why queue audio when its backend is synchronous like text?"

## Considered Options

- **Synchronous inline (like text) + serial frontend batches**: least code, unchanged workers. Rejected: browser-tab-bound progress, no task panel, no cancellation/resumption/cross-device continuity, unlike image/video; cardinality also matches media, invalidating the text analogy.
- **Queueing (like image/video)**: accepted.

## Consequences

- **A third worker lane**: add `audio_max` / `has_audio_room` to `ProviderPool`, extend claim-loop `for media_type in ("image","video")` with `"audio"`, and update lane-related `_resolve_dispatch_provider` / `_any_pool_has_room` / `_pool_full_providers` / `_load_pools_from_db` / `_build_default_pools` (mechanical changes). TTS is cheap/fast; allow a more generous `AUDIO_MAX_WORKERS` default so lanes do not bottleneck it.
- **Agent tool `generate_narration_audio(segment_ids?)`** (registered name; enqueue implementation in `server/media_tools/narration_audio.py`, modeled after storyboard tools) queues instead of synchronously waiting. Omitted arguments mean all missing segments; lists specify batch scope; one segment uses a one-element list (schema accepts arrays only, rejecting bare strings during argument validation).
- **Web enqueueing with existing task panels**: individual and batch operations enqueue; frontend polls `/api/v1/tasks` for progress and reuses cancellation/resumption UI.
- **Synchronous audio backends without resume/`provider_job_id`**: worker claim → synchronous backend (seconds) → terminal state. `docs/adr/0007` orphan handling reduces to "mark failed, do not resume" for audio; synchronous, cheap regeneration needs no video submit/poll/resume.
- **Synchronous is a choice for short segments and synchronous APIs; asynchronous remains an extension point**: v1 uses synchronous because (a) selected APIs return bytes synchronously (DashScope Qwen-TTS sync HTTP, OpenAI-compatible `/v1/audio/speech` returns immediately), (b) bounded short segment narration (`duration_seconds ≤ 60`) finishes in seconds. **Industry long-text TTS APIs are asynchronous** (MiniMax T2A async, Doubao asynchronous long text ≤100,000 characters, Google long-audio LRO, Azure batch). Asynchronous-only providers or "whole-episode long-text synthesis" would require video-style submit/poll lifecycles, so `AudioBackend` Protocol **reserves** asynchronous possibilities (allowing backend-specific forms like text/video); v1 builds only synchronous.
- **No Task migration**: `task.task_type` / `media_type` are free String columns; audio rows persist directly.
- **Unchanged versioning**: audio uses VersionManager like image/video (see "Narration dubbing" and "Media type (media_type / call_type)" in `GLOSSARY.md`).
- **Asymmetry with text is contractual**: text generates once per episode inline without queueing; audio generates N segments per episode queued. Subsequent PRs moving audio back inline (or text into queues) must first deprecate this ADR and explain changed cardinality.
