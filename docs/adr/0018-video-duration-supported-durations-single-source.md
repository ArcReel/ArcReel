---
status: accepted
---

# Per-model supported_durations owns video duration; forward unchanged and fail loudly on missing declarations

Backend bucket mappings silently changing 6 to 8 and implicit `or [4,6,8]` fallbacks caused selected 6s requests to become invalid 8s requests. Each video model declares a nonempty discrete `supported_durations` set shared by script prompts, frontend pickers, and request bodies. Remove backend bucket mappings/normalization and forward values unchanged, leaving out-of-range requests to provider 400 responses. Empty-set resolution raises `ValueError`, with all implicit fallbacks removed. Prefer clear failure directing users to configuration rather than silently changing user/LLM choices or hiding configuration defects.

## Consequences

- Do not introduce a continuous-range schema type; fully expand lists and let the frontend detect continuity.
- Prefill missing custom-provider values from model_id heuristic presets, conservatively defaulting on no match. Alembic backfills inline copied preset snapshots rather than importing modules, preserving historical determinism.
- One bounded exception: Vidu APIs list substantially different durations per endpoint, so retain `_coerce_duration` endpoint-level nearest correction with warnings, a different dimension from model-level truth.
- Fail-loud guards missing model declarations, not a dimension absent from the endpoint. With unbound ComfyUI `frames` or unreadable frame-rate sources, ArcReel does not drive duration (see `docs/adr/0082`). An empty set is legal with a separate flag distinguishing that state from missing declarations; every consumer accepts the flag without weakening this decision.
