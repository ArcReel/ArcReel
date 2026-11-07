---
name: adapt-custom-endpoint
description: Use when the user asks to connect a video or image provider using JSON submission followed by polling, or to write, validate, test, or save a custom endpoint definition in ArcReel.
---

# Adapt a custom endpoint

Use the bundled `scripts/custom_endpoint.py` to call the ArcReel HTTP API; the shared validator and API are the only entry points for validating and saving definitions.

## Workflow

1. Read the provider-documentation URL supplied by the user; if inaccessible, ask them to paste submission, task-query, and response examples.
   Confirm that the protocol uses JSON requests/responses with submission followed by polling. Signed authentication, multipart requests, or input-dependent routing
   cannot be expressed by the initial definition format; explain these gaps directly.
2. When writing or correcting definitions, read the [definition format](references/definition-format.md); create definition JSON,
   test-parameter JSON, and real response samples supplied by the provider in the working directory. Prefer credentials referenced through an existing ArcReel custom provider’s
   `provider_id`; never write API keys into project files, command arguments, or replies.
3. Run this skill directory’s `scripts/custom_endpoint.py` with `validate <definition.json>` to validate.
   Correct until `errors` is empty. If `schema_version.level` is not `direct` or `warnings` remain, fix them or explain the reason and impact of accepting them; use `hints` to prepare the endpoint addresses and models needed for testing.
4. Run `check-response` separately for the provider’s submission and polling responses; also check the retrieval response if the definition contains `result`.
5. Run `preview-request` and check URL, method, redacted headers, body, and material summaries. When the definition requires inputs,
   supply the corresponding files using `--start-image` / `--end-image` / `--reference-images` / `--reference-audio-files`.
6. **Connection tests make real provider requests and may incur charges. Ask the user before calling.** Only after explicit agreement run
   `trial-run ... --confirm-cost` (reusing preview material arguments), then query `trial-status <run-id>` until terminal,
   and relay requests, responses, extracted values, and errors. Continue on `succeeded`; stop on other terminal states unless the user explicitly requests saving an unverified definition.
7. Validate again. Use `save` to create when no same-lineage endpoint exists; if duplicated, default to saving a copy and tell the user.
   **Overwriting an existing endpoint requires asking the user**; only after explicit agreement use
   `save ... --endpoint-id <id> --confirm-overwrite`. Completion requires a successful save returning `ce-<id>`.

## Connection

Run the script from the current workspace root. It reads `.arcreel/settings.json` created by `setup-arcreel-skills`, derives
the same instance’s `/api/v1` address from `mcp_url`, and authenticates with `api_key`; run the setup skill first if configuration is absent or invalid,
without falling back to localhost.

With `ARCREEL_EMBEDDED_AGENT=1`, the script prefers `ARCREEL_API_BASE` and `ARCREEL_API_TOKEN`; this JWT
expires after 15 minutes without renewal; on API 401, tell the user to reopen the session for a new token.
Local deployments with `AUTH_ENABLED=false` may leave the token empty.
