---
status: accepted
---

# Preserve structured and plain-text prompts with one final rendering exit

Storyboard-image and storyboard-video visual prompts may be stored as structured JSON or plain text. Structured forms support field-based projection and enrichment; plain text preserves creators' complete expression without requiring it to be parsed back into structure. Both are formal prompt content, not preview caches or temporary structured-form renders, and neither is promised lossless interchangeability.

Regardless of storage, final provider text passes through one renderer per media type: `render_storyboard_image_prompt` in `lib/prompts/prompt_builders.py` for images, and `render_storyboard_video_prompt` in `lib/prompts/prompt_utils.py` for video. Readable plain text still needs the same project style, exclusions, reference-type declarations (including product fidelity), and speech declarations as structured prompts. Separate preview/admission/execution assembly would produce different requests and duplicate injections on rerendering. One exit guarantees preview/execution agreement and idempotence, not one storage format.

Both forms enter artifact basis as formal content under `docs/adr/0062` (`lib/artifacts/visual_artifact_provenance.py`). Body changes make artifacts stale; switching between structured and text forms also changes formal content. Basis includes content/form but excludes renderer-derived wrappers and renderer versions. Otherwise wording-only injection updates would retroactively stale existing visual artifacts. Exact-request reuse may separately record sent text without changing currency semantics.

## Rejected alternatives

- **Send plain text without rendering**: misses derived constraints and gives forms different generation boundaries.
- **Parse plain text into structured JSON**: cannot guarantee losslessness and replaces authored expression with guessed fields.
- **Use final sent text for artifact currency**: renderer wording changes would stale artifacts whose formal content is unchanged, contradicting the intent/formal-content boundary in `docs/adr/0062`.

## Consequences

- New preview/admission/execution entries reuse the corresponding renderer, never independent plain-text assembly.
- Add derived declarations only in shared renderers, ensuring repeated calls on rendered plain text do not reinject.
- Prompt templates (`docs/adr/0077`) supply wrappers and stay outside artifact basis. Bundled or later system-level wording edits do not stale existing artifacts.
- Currency still compares formal prompt content. Exact provider-request identity uses separate execution-sensitive digests (`lib/artifacts/video_visual_provenance.py`) including sent text/request settings, not the manifest's visual-content basis.
