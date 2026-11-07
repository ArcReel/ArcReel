---
status: accepted
---

# Evaluate video request facts once; reads and execution differ only in identity source

Effective models, submitted resolution, narrowed durations/reasons, endpoint-fixed duration, and audio-track shape were independently computed by pricing, preflight, planning, UI, Agent, and execution. Confirmation seconds differed from submitted seconds; pricing resolution differed from billed resolution. Consolidate video request facts, evaluated once per project × route × generation bucket × identity source. Consumers read results only. Reads use current-config effective identity; execution uses the constructed backend (`docs/adr/0049` item 3), with identical remaining evaluation.

Three resulting rules:

- **Unset resolution is omitted on both video routes**: reference video loses fallback resolution; duration constraints/pricing use actual submitted resolution (`docs/adr/0019`, `docs/adr/0056`).
- **Unavailable i2v means unknown**: no-reference units in reference projects use i2v only. Failure reports unknown with reasons, never r2v tiers.
- **No empty execution allowance**: failed/empty duration resolution blocks with codes, except legal endpoint-fixed empty sets (`docs/adr/0082`), instead of paid provider failures after capability-query fallback.

## Rejected alternatives

- **Soft r2v tier fallback when i2v is unavailable**: admits plans execution necessarily blocks.
- **Provider fallback resolution on reference routes**: its cause disappeared; it overrides ComfyUI literal dimensions (`docs/adr/0082`), fails specific-tier endpoints, and splits quotes/billing.
- **Independent assembly sharing only low-level rules**: separate defaults diverge silently on missing fields.
- **UI mirrors of reference presence**: declared references are not available references and disagree with server buckets.

## Consequences

- Omitting unset reference resolution changes execution fingerprints and stops same-tier free reuse, without changing artifact currency (`docs/adr/0062`).
- Supersede video fallback resolution/empty-duration allowance in `docs/adr/0049`; `docs/adr/0019` has no reference-route exception.
- Consumer tests use `make_video_request_facts` in `tests/factories.py` and direct `VideoRequestFactsFailure`, not fake resolvers/handbuilt capability dicts. Test capability evaluation with real `ConfigResolver` and test DB.
