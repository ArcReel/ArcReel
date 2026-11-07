---
status: accepted
---

# Resolve image capability (t2i/i2i) only at execution; worker routing uses an approximation

Whether an item uses text-to-image or image-to-image depends on **reference presence**. References include character/scene/prop sheets and the **previous storyboard** for continuity, reflecting project state when drawing starts. During batch regeneration, N-1 often does not exist when N enqueues. Capability is execution-derived and **structurally unknowable** to enqueue/claim. Resolve it only at execution; enqueue no longer freezes backend identity snapshots. Enqueue still derives advisory `provider_id` from the representative slot (`_derive_execution_model_for_enqueue`, NULL on failure for fallback) for claim SQL/pool routing only. Execution resolves precisely, updates mismatches, and requeues in place without provider calls.

Image edits (`image_edit`, see `docs/adr/0050`) alone necessarily use i2i with current-image-only references. Their capability is known at enqueue, so enqueue/claim resolve i2i precisely for routing and reject unavailable slots before task creation.

## Consequences

- Rate-limit routing needs providers before capability is available. Except `image_edit`'s exact i2i resolution, use representative `capability="t2i"`, stored at enqueue and read by claim SQL.
- Only distinct t2i/i2i providers with actual i2i requests initially occupy the wrong pool. Execution updates/requeues once, then occupies the correct provider slot, an accepted cost for exact executing-provider limits.
- This extra requeue is unavoidable while providers may differ and capability is execution-time. **Do not add needs_i2i at enqueue/claim**: that duplicates reference collection in scheduling, and batch answers there are already wrong.
