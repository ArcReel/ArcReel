---
status: accepted
---

# Image editing branches the image: use only the current image as reference, use instructions as the prompt, and do not write back image_prompt

When making local adjustments to a generated asset design image / storyboard image, redrawing the entire image from the original prompt also overturns satisfactory parts and drifts further with each edit; uploading a replacement instead requires users to have external image-editing capabilities. Add a separate image-editing task type, `image_edit`: call i2i with the resource's **current image as the sole reference image** and the user's **incremental editing instructions as the sole prompt**, producing a new image that largely preserves the original and replaces current, while the old image automatically enters version history. Mark the new version with `source: "image_edit"`, and use the editing instructions as its prompt field. Editing branches the **image**, rather than the prompt: the original image_prompt is not written back; regenerating after an edit still redraws from the original prompt, and the edited result can only be recovered from version history.

- **Only the current image**: do not include automatic reference-image collection from the generation path (character/scene/prop sheets or the previous storyboard). Extra references dilute anchoring to the base image, repeating the problem of "a redraw that differs greatly from the original." The task payload is naturally extensible; allowing additional reference images in the future would still preserve the semantics that "the current image is the only base-image anchor."
- **Read current at execution time for the base image**: follow all generation tasks' rule of "read the latest state at execution time," without taking a snapshot at enqueue time.
- **Always i2i, known at enqueue time**: this is the sole exception to determining capability at execution time (`docs/adr/0001`). Resolve the i2i slot before enqueueing to fail fast (reject if unavailable, without creating a task), while retaining capability gating at the execution layer as a fallback.
- **No loss on failure**: if the backend call fails, neither the current image nor version files are touched, and no resource fields are written back.

## Explicitly rejected

- **Mask / inpaint selection interactions**: only one provider in the current matrix natively supports masks, yielding narrow benefits at high cost; instruction-based editing has no selection semantics.
- **Writing editing instructions back to / merging them into image_prompt**: editing instructions are incremental changes relative to the current image and become invalid without it; merging would contaminate the complete generation prompt that can independently redraw the image.
- **Previewing and confirming editing results**: directly replace the image with version history as the fallback, matching the mental model of regeneration.
- **Server-side mutual exclusion for tasks on the same resource**: preserve symmetry with existing generation semantics; preventing duplicates is the frontend's occupied-state responsibility.

## Consequences

- "Regenerating after editing returns to the original prompt and loses the edit" is inherent to editing semantics. Do not introduce prompt synchronization; recover edited results through version rollback.
- Editing is limited to character / scene / prop / product design images and storyboard images. It does not apply to grid images (intermediate artifacts whose editing requires re-slicing and backfilling), global asset-library images (not attached to a project, while tasks/versions/accounting/SSE are all project-scoped mechanisms), or character reference images (user-uploaded material that should be re-uploaded if unsatisfactory).
- `image_edit` is a separate task_type (media_type=image, using the image concurrency lane), clearly distinguishable from generation tasks in usage statistics, failure diagnosis, and task lists. Its completion event has the same shape as a generation-completion event for the same resource, so the frontend refresh mechanism need not distinguish them.
