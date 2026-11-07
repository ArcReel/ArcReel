---
status: accepted
---

# Provider-model path overrides replace complete paths per endpoint request

Relays may keep protocols but add prefixes, versions, or renamed paths, often differently per protocol. Previously only base_url `/vN` changed while SDK paths stayed fixed. Store overrides on custom-provider models, separately replacing each submission/poll/result path. Few attached models make this layer most flexible.

## Rules

- Only custom providers, for Python/declarative endpoints, not ComfyUI.
- Endpoints name requests and default paths; overrides key by request name. Slots are only values already available to that request, such as API model name/job ID.
- Overrides are relative to raw configured base_url, bypassing endpoint normalization such as appended `/v1`; unoverridden requests stay unchanged.
- Replace paths only. Use declarative definitions for request/response schema changes.
- Override queries replace, not merge, originals. Include required parameters such as Gemini `alt=media` explicitly.
- Resume replays submission-time base_url for polling/retrieval and matching. Stored URLs are normalized; reconstruct raw bases from current raw→normalized deltas. Prefix edits work only with unchanged deltas, such as `/old`→`/new` both gaining `/v1`.
- Raw bases are not persisted; changed normalization deltas make reconstruction ambiguous. `/old/v1`→`/new` can lose the original version because submission equals normalized `/old`. DashScope preserves ordinary prefixes, strips known suffixes, and appends `/api/v1`; ordinary edits still require stable deltas, not every raw path is lost.
- Python endpoints rewrite named requests at HTTP transport, never synthetic base URLs.
- Overrides are endpoint-protocol facts frozen in execution checkpoints. Changed overrides on resume count as drift under `docs/adr/0007`.
- Discovery/connectivity retain provider base_url/discovery format without overrides.
- New attachments have no inherited sibling overrides; reattachments discard old ones. Overrides beat later definition edits; ignore names no longer defined.

## Considered Options

- **Provider × endpoint records**: avoids repetition but differing model paths force extra providers.
- **Copy endpoint definitions**: Python has none; global copies proliferate for every relay.
- **Prefix-only or separate base URLs**: cannot rename/separately change asynchronous requests.
- **Copy sibling values on create/reattach**: disagreements provide no authoritative choice and inconsistent entry rules.
- **Synthesize when possible, transport otherwise**: two mechanisms require per-request decisions.

## Consequences

- Models under one relay repeat identical overrides individually.
- Declarative URLs not starting `{{ base_url }}`, such as response download URLs, have no replaceable paths and reject overrides.
