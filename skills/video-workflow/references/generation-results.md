# Generation results

Generation tools return structured per-ID results. Read the structure rather than summarizing human-facing prose.

Selection has three meanings:

- Nonempty IDs explicitly select tasks and may force regeneration.
- Omitted IDs fill only missing artifacts.
- Empty IDs are invalid; omit the field.

Requested IDs partition into `succeeded`, `failed`, and `blocked`; `skipped` records reuse outside that partition. Report each ID using structured problem code/action, without automatic retries inferred from prose.

State reports must distinguish four independent dimensions:

- Workflow-step state;
- Task/queue state;
- Provider-submission checkpoint;
- Artifact state (`current`, `stale`, `missing`, or `blocked`).

Task success does not guarantee current-input fidelity; missing artifacts do not mean paid-task failure; submitted tasks may already be charged; never treat blocked as missing and regenerate.

For provider moderation rejection (`provider_input_image_rejected`, `provider_input_image_rejected_at`), relay the reason and explain provider-library upload is an option for the user. Only explicit requests to upload a particular image authorize `upload_to_provider_asset_library` with `user_requested=true`; rejection itself authorizes neither upload nor automatic regeneration. Upload immediately returns an ingestion task; query `get_provider_asset_links`, then regenerate after `active` only according to user wishes. Relay `provider_asset_link_processing` / `provider_asset_link_failed` without automatically unlinking or reuploading.
