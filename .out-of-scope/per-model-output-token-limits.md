# Per-Model Output Token Limits

ArcReel does not maintain a separate per-model output-token-limit table or runtime-clamp requested `max_tokens` to catalog maximum output facts.

## Why this is out of scope

Example request (PR #405): add a `TextGenerator` per-model `max_output_tokens` table (e.g. `doubao-seed-1-8-251228 → 8192`), use `min(requested, model_limit)` before generation, and silently split oversized requests.

ADR 0044 (`docs/adr/0044-text-output-token-nonbinding-ceiling.md`) chose otherwise: `DEFAULT_MAX_OUTPUT_TOKENS = 64000` in `lib/backends/text_backends/base.py` is one **nonbinding** safety valve. It still passes unchanged as request `max_tokens`; nonbinding means it is not a lowered functional task budget, only runaway protection. Backends detect structured-output truncation through `check_truncation()`, raising `TextOutputTruncatedError` so users explicitly choose more capable models; free text stays log-only. The ADR explicitly rejects:

- **Per-model limit tables**: external data for all built-in/custom providers requires ongoing maintenance, creating a stale-prone second truth source. #1847 (2026-08) records Ark’s `doubao-seed-1-8-251228` limit changing from about 8192 to 64K, while PR #405 hardcodes `8192`, cutting legitimate output 8-fold immediately.
- **Adaptive degradation on truncation**: silent batch shrinking conflicts with explicit failure and user control.

Entry `max_output_tokens` is an optional capability fact, currently used only to size episode-planning batches (ADR `docs/adr/0044` final revision), intentionally not constraining request `max_tokens`.

Reopen condition from #1847’s closing comment: **real, reproducible** incompatibility in a currently selectable model; file a specific provider/model issue rather than introduce a general table.

## Prior requests

- #1847 — Spec「按模型输出 token 上限」 (per-model output-token limits), closed as YAGNI on 2026-08-13
- PR #405 — feat(ai): add token limit awareness to text generator
