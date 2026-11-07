# Bundled declarative built-in endpoints

This directory contains **built-in endpoints** implemented as declarative definitions. Each `<key>.json` filename is its built-in key (for example, `newapi-video.json` -> `newapi-video`). `lib/custom_provider/builtin_definitions.py` loads them into `ENDPOINT_REGISTRY` at import time.

Constraints:

- Definitions must pass the shared `arcreel_market_core.endpoint_definition.validate_definition` validator, use `ArcReel` as `meta.author`, and have keys that do not start with `ce-`. Violations raise at import time and prevent startup.
- Definitions are not persisted to the database and users cannot edit or delete them. Replacing bundled files during an upgrade takes effect immediately. User copies created with “Copy as mine”, using `ce-<id>`, do not follow these updates.
- Keys share a namespace with Python built-in endpoints. Filenames must not duplicate existing registry keys.

## Verified runtime expressiveness

The declarative runtime already supports both behaviors below; adding a definition does not require runtime changes:

- **POST polling.** Submission, polling and secondary retrieval sections accept `GET`, `POST` or `PUT` for `method`. Polling requests may include a `body`, with `body` referencing `{{ task_id }}`. Upstreams using POST polling, such as SiliconFlow video, can be expressed directly.
- **Task IDs containing `/`.** URL-template placeholders are not percent-encoded. An ID containing `/` expands into multiple path segments: `{{ base_url }}/v1beta/{{ task_id }}` with the Veo operation name `models/veo/operations/abc` expands to `/v1beta/models/veo/operations/abc`. Upstreams requiring `/` to become `%2F` are unsupported; none have been identified (new-api encodes operation names into its own task IDs first).

The frontend “New” form's example templates are prefilled content, not built-in endpoints. They live in `frontend/src/data/example-templates/`.
