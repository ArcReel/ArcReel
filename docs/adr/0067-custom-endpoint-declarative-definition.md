---
status: accepted
---

# Custom invocation endpoints use declarative definitions for the JSON-in/out, submit/poll paradigm

New video/image protocols previously required source edits: `lib/custom_provider/endpoints.py` hardcoded all built-in endpoints, and new protocols needed backend classes, endpoint keys, inference rules, and i18n. Long-tail aggregators/intermediaries share one paradigm (JSON requests/responses, submission then polling), differing mainly in field names/extraction paths; provider-specific Python is unsustainable. Images have similar providers: OpenAI-style paths/fields but asynchronous submit/poll semantics, unsupported by synchronous OpenAI-compatible image endpoints.

**Use declarative definitions for custom invocation endpoints**: JSON containers (`kind: declarative`, file-level `schema_version`), JSON-structured request templates with `{{ }}` placeholders (whole-string single placeholders preserve native types), a few fixed constructs (`$each` field expansion, object-level `$when` guards, optional secondary `result` retrieval), priority arrays of a restricted RFC 9535 JSONPath subset for response extraction, and pure-dictionary status/enum mappings. Definitions declare media (`media_type: image | video`, default video); introduce no general template engine such as Jinja2. The goal is not every protocol but **one shape for the JSON-in/out, submit/poll paradigm**: cover long-tail aggregators/intermediaries (8 of 10 sampled providers fully expressible), and absorb wholly expressible HTTP-style built-ins into shipped definitions (initial newapi / v2_video_generations / minimax, removing their Python paths in the same PR). Inexpressible protocols (signed authentication such as JWT / SigV4, material-based routing, cross-list pairing, SDK integrations) remain Python built-in endpoints without format expansion.

**Explicitly rejected**: ① **Abandoning declarative endpoints under universal coverage criteria**: measured against all protocols, coverage is only roughly 50–60%; within the paradigm, acceptance reproduced 8 HTTP-style built-ins through 22 definitions. Endpoint tests' three modes (request preview / response validation / connection testing) and validators shared with saving address debugging disadvantages. ② **Runtime Python plugins for custom protocols**: loading, execution, and trust are separate concerns (#872), with container `kind` reserving a slot; declarative definitions absorb wholly expressible protocols without excluding plugins. ③ **Expanding formats for inexpressible constructs** (signed authentication, material-based branches, multipart, etc.): under "unused optional constructs remain invisible," every extension requires real external long-tail demand, rather than format completeness.

## Image definitions

Media type is orthogonal to `kind`: `kind` distinguishes definition shapes (protocol conventions versus workflows). Images/videos share declarative format/runtime; ComfyUI definitions also declare media types (see `docs/adr/0081`). Image/video definitions differ only as follows:

- **Explicit capabilities**: image definitions declare t2i / i2i and reference limits, possibly both in one definition; i2i requires reference inputs. Reuse `docs/adr/0015` gating without downgrade. Do not derive capabilities from inputs as ComfyUI does (`docs/adr/0082`): video declarations are already explicit, keeping one `kind` consistent, while inference such as required reference inputs determining t2i/i2i is too hidden for definition authors.
- **Media-specific template-variable/input sets**: images may use only prompts, models, ratios, resolutions, dimensions, seeds, and references. Duration, audio, or first/last-frame inputs fail on save instead of rendering empty. Editing instructions use prompts without separate variables.
- **URL or base64 artifacts**: extraction can specify URL paths, base64 paths, or both, preferring URLs when both exist. Base64 remains a JSON response field within JSON-in/out. Videos extract only URLs until a real case warrants revisiting.
- **Retrieve one image per call**, with reference costs using model-row per-image prices, matching ComfyUI images (see `docs/adr/0081`).
- **No resumption**: only videos have resume protocols. On restart, in-flight image tasks are restart-lost like ComfyUI images. They usually finish within tens of seconds, not justifying a new resume protocol.

Both media types inline materials only as data URIs / base64, without public URL hosting; all three endpoint-test modes support both.

## Two storage deviations

- **Store complete definitions as JSON**: new `custom_endpoint` table uses JSON `definition` as sole truth. This does not conflict with ADR 0042's typed-column preference for stable fields: definitions evolve with `schema_version`, with unstable fields, precisely its genuinely dynamic JSON case. Typed columns are read-only mirrors needed without database-side JSON parsing (`kind` / `schema_version` / `media_type` / `display_name` / timestamps), derived on write rather than second truth.
- **Reject deletion while model rows reference endpoints**: deleting endpoints referenced by `custom_provider_model` returns 409 plus reference lists. This first rejection precedent coexists with existing deletion/dangling-reference-cleanup paths, without replacing them. Model rows hold manually configured pricing/overrides; cascades discard user work, while dangling references defer failure to generation. Service-layer reference counts ensure integrity without FK constraints: 409 needs reference lists, while constraints only raise `IntegrityError`, and deletion already must query references.

## Consequences

- Built-in invocation endpoints now have two implementations: Python backends or shipped declarative definitions (`lib/custom_provider/builtin_endpoints/*.json`, registered at import, retaining built-in keys without database storage). Switching implementations under the same key lets declarative runtimes take over in-flight tasks by key without version locks.
- New HTTP-style submit/poll protocols default to "write a definition" (UI/import, zero code), using Python only after confirming inexpressibility. Catalogs distinguish implementations through `kind: python | declarative`.
- Subsequent PRs expanding formats for inexpressible constructs or absorbing SDK-style backends must first revise this paradigm boundary.
- This ADR governs integration choices in chapter 7 of `docs/research/arcreel-video-api-protocol-research.md` (which carries a revision note).
