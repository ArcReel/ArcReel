---
status: accepted
---

# ComfyUI endpoints: workflows are a second custom-invocation-endpoint `kind`

Users' native ComfyUI installations (local machines, intranets, or their own cloud hosts) must connect to ArcReel as image/video provider channels. Neither existing integration shape fits: built-in provider model lists are release-time static dictionaries that cannot hold runtime-imported workflows; declarative endpoints are bounded to "JSON in/out + submission/polling," while ComfyUI sends complete node graphs, uploads materials through multipart, and returns binary files, precisely the constructs `docs/adr/0067` explicitly excludes from format expansion.

**One ComfyUI workflow with node bindings is one custom invocation endpoint (a ComfyUI endpoint, `kind: comfyui`); one ComfyUI service is one ordinary custom provider (`discovery_format: comfyui`), whose model rows attach to ComfyUI endpoints.** Embed workflows unchanged in API format inside endpoint definitions, using the `kind` slot reserved by `docs/adr/0067` and storing the whole definition in `custom_endpoint.definition`; mirror columns remain derived from it. Reuse endpoint CRUD, import/export, referenced-entry deletion rejection, endpoint-test frameworks, and market payloads. Runtime branches on `kind`: `comfyui` produces Python-implemented image / video backends without the declarative runtime. Endpoint definitions determine media type; one ComfyUI provider can attach both image and video endpoints.

**Explicitly rejected**: ① **A new built-in provider**: runtime user-imported workflows cannot fit built-in model lists. ② **Expanding declarative formats** for node graphs, multipart, and binary artifacts: breaks 0067's paradigm boundary and yields no generality. ③ **Another entity family** (workflow table + workflow model rows): duplicates existing endpoint CRUD, import/export, deletion rejection, and market payloads, whereas `kind` exists for a second implementation shape. ④ **One workflow = one model row**: rows do not hold definitions and cannot support import/export/market entry, losing "attach one workflow to multiple machines."

## Credential-header templates belong in endpoint definitions, without provider columns

ComfyUI itself has no authentication; user-added reverse-proxy authentication varies (headers or query). Store credential-header templates in endpoint `auth`, sharing declarative-endpoint schema. Permit `{{api_key}}` only there, and skip rendering the whole section when `api_key` is empty. Add no provider columns, preserving `docs/adr/0008`'s single-field credential model unchanged. Accept two edge-case costs: deployments differ, so one workflow used on differently authenticated machines requires duplicate definitions; provider-level connectivity checks cannot read endpoint credential templates and use the same probe as the openai protocol, requesting `/system_stats` with `api_key` as Bearer (no credentials when `api_key` is empty). Proxies requiring custom headers may therefore appear unreachable in the probe; use endpoint connection tests as authoritative in that case.

## First bidirectional binding between endpoint and provider protocols

Previously, any custom invocation endpoint could attach to any custom provider. ComfyUI endpoints cannot: validate both directions server-side. `kind: comfyui` endpoints attach only to `discovery_format: comfyui` providers, whose model rows likewise accept only ComfyUI endpoints; selectors filter by protocol. Apply this constraint only to comfyui, preserving unrestricted attachment for other protocols. ComfyUI runtime paths (`/upload/image`, `/prompt`, `/history`, `/view`) make sense only for ComfyUI services; declarative endpoints likewise make no sense there, and wrong attachments fail only during generation.

## No provider bills; reference costs retain unit-price fields and execution duration is recorded only

ComfyUI uses users' own GPUs without provider bills. Invent no GPU pricing: reference costs reuse model-row unit-price fields and existing formulas (video by clip duration, images by count), treating missing prices as zero. Record execution durations normally for enterprise cost allocation, without using them in pricing. Accounting stays unchanged.

## Cancellation and timeout stop remote work inside backends, without protocol expansion

When ArcReel abandons generation (user cancellation or polling timeout, for both generation tasks and connection tests), notify ComfyUI best-effort to cancel the task and free the GPU. This is the repository's only provider-notified cancellation: abandoned commercial API tasks merely stop polling while providers keep generating/charging, making notification pointless, whereas leaving ComfyUI running wastes users' GPU capacity. Do not expand invocation-channel protocols: remote stopping happens inside ComfyUI backends' generation flows, retaining existing local failure paths after abandonment. Remote-stop failures only log; local state machines (`docs/adr/0006`) are unaffected. Slow-GPU users may lose nearly finished results at timeout limits, mitigated through configurable polling timeouts.

## Consequences

- Endpoint-definition schemas split asymmetrically by `kind`: declarative definitions have request templates and `capabilities`, while ComfyUI definitions have `workflow` and `bindings` (see `docs/adr/0082`). Shared validators dispatch by `kind`.
- Add `comfyui` to `discovery_format`: connectivity probes call `/system_stats` and return `comfyui_version`; discovery returns structured "not applicable," not an empty list. These providers default to concurrency 1.
- Retire "custom invocation endpoints are always video"; always read media type from endpoint definitions.
- ComfyUI endpoint tests offer only request preview and connection testing, without response validation: artifact extraction is fixed code, with no user-configurable extraction paths to validate.
- Initially poll only `/history`, without WebSocket support, ComfyUI queue positions, or fake progress.
- ComfyUI definitions naturally serve as market-entry payloads without changing market-entry types. Initial market display/installation of ComfyUI endpoints is outside this ADR. Hosted ComfyUI cloud services (such as Comfy Cloud and RunningHub) are also out of scope; they use declarative endpoints.
