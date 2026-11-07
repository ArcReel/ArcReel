# ArcReel

An AI video creation platform that turns novels, screenplays, or creative ideas into short videos. This domain glossary (ubiquitous language) defines concepts only, without implementation details. Chinese product labels are retained alongside English terms for correspondence with the existing Chinese interface and its copy rules.

## Language

### Providers and models

**provider (供应商)**:
An external service that supplies ArcReel with text, image, video, or speech generation capabilities.
_Avoid_: vendor, channel.


**backend (调用通道)**:
A callable implementation of a provider and model pair using a specific protocol. Provider resolution yields a ProviderModel identity; the backend constructed from it actually makes the provider call.
_Avoid_: client, adapter; the Chinese term 后端, already used for the frontend/backend architectural distinction.


**model (模型)**:
A specific generation service published by a model maker and offered through a provider. One provider can offer models for different purposes and capabilities, and the same model can be called through multiple providers.
_Avoid_: Treating providers and models as the same conceptual layer.


**model maker (模型厂商)**:
The party that trains and publishes a model, such as Google or ByteDance. It differs from the provider: Gemini is offered through AI Studio and Vertex, but its model maker is Google in both cases.
_Avoid_: Treating a model maker as a provider; using “official channel” to mean the model maker.


**model catalog (模型目录)**:
All model entries known to ArcReel, providing shared model metadata and prices for built-in providers, custom providers, and the Agent.
_Avoid_: Registry, price table, or a model list belonging to one provider.


**catalog entry (模型条目)**:
One entry in the model catalog describing facts about a model itself: media type, capabilities, limits, and official prices. It is independent of the provider; providers share the same entry, identified by model maker/official model name. The two entries of an amphibious model are further distinguished by media type.
_Avoid_: Registering the same model separately for each provider; storing a provider's price difference on the entry.


**provider model (供应商模型)**:
A model offered by a provider, recording which endpoint and API model name it uses. Built-in and custom providers share this concept. Models under built-in providers may be officially listed or user-added.
_Avoid_: The former term 模型行 (“model row”); separate prices on provider models; a synchronization switch on a provider model.


**model alias (模型别名)**:
An alternative call name for a model, such as a dated snapshot name or a common relay-service name. It only matches model names to catalog entries. Variants with different prices, capabilities, or retirement dates are separate entries, not aliases.
_Avoid_: Unqualified “alias”; confusing it with an asset alias; registering differently priced dated snapshots as aliases.


**entry binding (条目绑定)**:
A catalog entry manually assigned by the user to a custom provider's provider model or an Agent credential's tier.
_Avoid_: Association, entry reference; treating an on-demand name lookup as a saved binding.


**officially listed (官方收录)**:
A built-in provider's provider model that appears in the provider's official-data list, as opposed to a user-added provider model.
_Avoid_: Preset model, built-in model.


**capability override (能力覆盖)**:
A whole-dimension replacement of model capabilities on a provider model, expressing differences between this channel and the catalog entry or backend declaration (see `docs/adr/0104`).
_Avoid_: Changing a catalog entry for channel restrictions; overriding capabilities on an officially listed provider model.


**factory snapshot (出厂快照)**:
Model-catalog data bundled with a release, taken from the catalog data repository at release time.
_Avoid_: Built-in price table, default data.


**official data (官方数据)**:
Model-catalog data from the catalog data repository, comprising the factory snapshot and official sync, as opposed to user modifications.
_Avoid_: Preset data, upstream data.


**official sync (官方同步)**:
An instance's automatic retrieval of model-catalog data newer than its factory snapshot from the catalog data repository.
_Avoid_: Automatic update, online catalog; treating it as a feature of the official service.


**catalog data repository (目录数据仓库)**:
The public repository publishing model-catalog data, used by both factory snapshots and official sync.
_Avoid_: Market source, synchronization server.


**minimum app version (最低应用版本)**:
The lowest ArcReel version able to call a provider model, recorded on officially listed provider models. Catalog builds derive it from the attached endpoint, or it can be written explicitly (see `docs/adr/0103`).
_Avoid_: Schema version, compatibility version.


**third-party metadata source (第三方数据源)**:
Model metadata and pricing data maintained by external projects such as basellm and models.dev, used as inputs to catalog-data-repository builds.
_Avoid_: Sync source, upstream catalog.


**credential (凭证)**:
Authentication information ArcReel needs to access a provider. Media-provider settings call it 密钥 in Chinese and Key in English, as in “2 keys configured” and “Add key.”
_Avoid_: Model, provider, connection.


**built-in provider (内置供应商)**:
A provider bundled with ArcReel and ready to use after the user supplies credentials and selects a model; it represents an official channel.
_Avoid_: preset, official; using a built-in provider for a relay service.


**custom provider (自定义供应商)**:
A provider created by the user at runtime, with each provider model attached to an endpoint. Its credentials contain only api_key and base_url; api_key may be empty.
_Avoid_: Built-in provider.


**endpoint (调用端点)**:
A call mechanism attachable to a provider model: either a protocol convention (URL templates, authentication, and field semantics) or a ComfyUI workflow with node bindings.
_Avoid_: protocol, format, port, interface format, HTTP endpoint: it is an entire call convention, not one URL.


**builtin endpoint (内置调用端点)**:
An endpoint bundled in ArcReel's registry, implemented as a Python backend or a bundled declarative definition. It is read-only and cannot be edited or deleted. A declarative built-in endpoint can be copied with “Copy as mine” to create a custom endpoint.
_Avoid_: System preset endpoint, preset endpoint, preset.


**custom endpoint (自定义调用端点)**:
An endpoint created or imported by the user at runtime, holding an endpoint definition. It exists globally and can be attached to multiple custom providers' provider models. Like a built-in endpoint it is an endpoint; only its origin differs.
_Avoid_: Protocol template, custom protocol; treating it as a child of one custom provider.


**declarative endpoint (声明式端点)**:
An endpoint whose definition is a protocol convention, with request templates, response extraction, and status mappings written in the definition and executed by the generic runtime.
_Avoid_: JSON endpoint, template endpoint.


**ComfyUI endpoint (ComfyUI 端点)**:
A custom endpoint defined by a ComfyUI workflow and its node bindings. It can attach only to provider models of ComfyUI-protocol providers. Exactly one workflow is one endpoint.
_Avoid_: Workflow endpoint; treating it as a provider (a ComfyUI service is a provider, a workflow is an endpoint); treating one workflow as a provider model.


**endpoint definition (端点定义)**:
JSON describing how to call an endpoint. Its `kind` distinguishes declarative protocol definitions and ComfyUI workflow definitions. It is the endpoint's sole source of truth and the import, export, and market-installation payload. Definitions are considered the same by author and name in `meta`.
_Avoid_: Configuration, protocol template, lineage / provenance: say “definitions with the same author and name.”


**path override (路径覆盖)**:
A replacement of the complete URL path for one request of a custom provider model's endpoint, leaving the rest of the request unchanged (see `docs/adr/0101`).
_Avoid_: Custom path, URL override; storing it on a provider or endpoint.


**node binding (节点绑定)**:
A correspondence on a ComfyUI endpoint between an ArcReel named semantic input (prompt, first frame, last frame, reference images, dimensions, frame count, frame rate, seed, output) and workflow node inputs, or node outputs for generated output. Import inference produces only candidates; they become bindings after the user saves. With no binding for a semantic input, ArcReel does not drive it and the workflow literal applies. Prompt and output bindings are mandatory. Each semantic input has three states: bound, user-confirmed unsupported by this workflow, or never inferred. The latter two both have no binding, but only the last is inferred again on import.
_Avoid_: Slot, mapping, bare “binding”; treating inference candidates as bindings; calling “never inferred” unsupported.


**example template (示例模板)**:
A definition example bundled in frontend source that cannot be attached, used only to prefill the “New custom endpoint” form.
_Avoid_: Starter template, starter skeleton, template file.


**endpoint test (端点测试)**:
Validation of a custom endpoint definition before or after saving, with preview request, check response, and trial run modes. ComfyUI endpoints have only preview request and trial run.
_Avoid_: Test drive, runner, dry run.


**preview request (预览请求)**:
An endpoint-test mode that renders the request for inspection without sending it to the provider.


**check response (验证响应)**:
An endpoint-test mode that uses a real provider response to validate extraction paths and status mappings without sending a provider request.


**trial run (测试连接)**:
A real generation submission using a custom endpoint definition or provider model, polled to a terminal state. It incurs charges or consumes compute and is the only check that proves successful model invocation.
_Avoid_: Calling a provider-level connectivity probe a trial run.


**connectivity check (连通性检查)**:
A free provider-level probe that checks credential reachability, such as listing models; it does not prove that any model can generate successfully.
_Avoid_: 测试连接 (“trial run”), the former name.


**canonical provider id (规范供应商 id)**:
The canonical spelling of a provider identity and the only accepted write form throughout the system.


**legacy provider alias (旧版供应商别名)**:
A compatibility name formerly used for a provider in historical data that is not its canonical provider id.
_Avoid_: Using a legacy provider alias as a writable provider identity.


**api_model_name (API 模型名)**:
The model name actually sent to a provider API by a provider model. It may differ from the catalog entry's official name, for example `anthropic/claude-sonnet-4.5` on a relay service (see `docs/adr/0038`).
_Avoid_: Using the catalog entry identity directly as the model name sent to the provider.


**amphibious model (两栖模型)**:
A model whose provider API model name supports both image and video media_type values, `registered` as two catalog entries in ArcReel (see `docs/adr/0038`).
_Avoid_: Treating alias keys as real model names.


**discovery_format (模型发现协议)**:
A custom-provider declaration selecting the API used for model discovery and connectivity checks, without determining any model's invocation protocol. With `comfyui`, model discovery does not apply; only connectivity is checked.
_Avoid_: api_format; treating it as a model-invocation protocol switch.


**active credential (生效凭证)**:
The credential currently selected when multiple credentials are configured under one provider. The user switches it manually in the UI; it applies globally, with at most one active credential per provider.
_Avoid_: default credential, enabled credential (which implies several may be enabled); interpreting switching as automatic rotation or load balancing.


**agent_credential (Agent 凭证)**:
An Anthropic-compatible gateway credential (base_url + api_key + routing model) for the Claude Agent SDK, stored in a separate Agent credential table and not interchangeable with custom-provider credentials (see `docs/adr/0017`). The UI calls these “Agent providers” (“Add provider,” “Delete provider”), one per gateway.
_Avoid_: Treating it as a custom provider.


### Market

**market (市场)**:
The area where users browse and install content from one or more market sources. Browsing and installation depend only on local ArcReel and market-source repositories, with no central service or account. The sole initial market-entry type is endpoint.
_Avoid_: Template library, shop / store, registry, plugin market, community.


**market source (市场源)**:
A GitHub repository (or equivalent direct-link address) listing its market entries in an index file, registered in ArcReel for browsing and installation. The official market source is bundled; users add other third-party market sources themselves.
_Avoid_: Unqualified source, repository, origin; registry, feed, mirror.


**market entry (市场条目)**:
Installable content in a market-source index, identified by a source-local unique slug, displayed by `meta.name`, with an endpoint definition as payload. The same slug across sources does not imply the same definition.
_Avoid_: Unqualified entry, plugin, package, template.


**install (安装)**:
Materializing a market entry's endpoint definition as a local custom endpoint and recording its source and entry, which the endpoint follows thereafter. Importing a local file creates no such binding and does not count as installed.
_Avoid_: Interchanging import and install; download, subscribe.


**official service (官方服务)**:
An ArcReel-operated layer above the market that records installation counts and ratings and receives sharing submissions. It is not a market source, does not participate in browsing or installation, and can be disabled by the user.
_Avoid_: Cloud service, backend, registry.


**instance id (实例标识)**:
A locally generated random identifier of an ArcReel instance, sent with requests to the official service for server-side deduplication. The user may reset it.
_Avoid_: Device ID, user ID.


**submission (分享提交)**:
The action and record of submitting a custom endpoint from the client through the official service to the official market source. Acceptance is determined by the PR in the market-source repository.
_Avoid_: 投稿 (reserved for manual PR contributions), publish, upload.


### Tasks and cancellation

**task (生成任务)**:
A work unit ArcReel queues and tracks to complete one media generation.
_Avoid_: Job, provider call.


**api_call (供应商调用)**:
One API request ArcReel makes to an external model provider, the smallest cost and usage accounting unit. The embedded Agent is an exception: requests to the same model in one turn count together as one provider call (see `docs/adr/0102`). A call may serve a generation task (a task can have zero to many calls) or occur independently (script generation, Agent sessions, endpoint trial runs).
_Avoid_: Provider job, generation task; assuming calls and tasks are one-to-one.


**provider_job (供应商任务)**:
A remote asynchronous operation created by the provider for a submit-and-poll generation. Its provider-written status is distinct from ArcReel's local task status even when names match.
_Avoid_: Confusing it with local task status; generation task.


**batch_admission (整批准入判定)**:
A single all-or-nothing decision over every target before “Generate all / Batch generate” creates any task. Outcomes are admitted, awaiting confirmation (such as aggregated cross-tier costs requiring the user), or blocked (zero tasks created). It decides whether tasks can be created, not whether execution will succeed.
_Avoid_: Calling whole-batch rejection “batch failure.”


**execution_checkpoint (执行检查点)**:
The identity and actual input facts of a single paid video request, frozen before its first submission to the provider. It proves how that submission occurred, not a queue snapshot or artifact marker.
_Avoid_: request snapshot, resume payload, current marker.


**orphan task (孤儿任务)**:
A generation task recorded as running with no executor actually running, caused in practice by service restart (see `docs/adr/0007`).


**resume (续跑)**:
The self-recovery path that resumes polling and downloading an orphan video task holding a provider job id after service restart, without resubmission. An expired or missing provider job fails explicitly; it does not fall back to resubmission.
_Avoid_: Retry, resubmit, restart recovery (the cause, not the path's name).


**cancel (取消)**:
A user withdrawing a generation task that has not started, available only for `queued`. Once execution begins the task cannot be cancelled: submitted provider calls finish and their results become artifacts, so incurred costs are not wasted.
_Avoid_: abort, stop; interpreting cancellation as interrupting running generation.


**cancelled_by (取消来源)**:
A cancellation-origin marker: `user` means triggered from the UI, `cascade` means downstream dependencies were cancelled together, and `interrupted` means a process-level interruption (event-loop teardown, shutdown timeout, etc.), not a user action. Internal timeout reclamation is not cancellation.


### Resolution

**resolve (供应商解析)**:
Selecting a task's ProviderModel with descending precedence: this request > project > global default. This selects identity and does not construct the backend.
_Avoid_: Using resolution for this process: resolution means image/video resolution only.


**ProviderModel (供应商模型组合)**:
The result of provider resolution: a `(provider_id, model_id)` pair, a value object identifying one provider model, not a backend.
_Avoid_: ResolvedBackend, BackendSelection.


**text task tier (文本任务档位)**:
A coarse classification of text-generation call sites as simple or complex. Users configure a text backend per tier, with a default model as fallback for unset tiers (see `docs/adr/0051`).
_Avoid_: Dedicated model settings per call site; treating the Agent conversation model as a tier.


**capabilities (模型能力)**:
The generation inputs, outputs, and controls a model supports, such as accepting reference images, generating sound with video, or accepting an end frame.
_Avoid_: Confusing capabilities with the user's model choice.


**model_selection (模型选择)**:
Settings specifying models for different generation purposes, falling back to the default model when not individually specified.
_Avoid_: Generation model.


**generation_type (任务类型)**:
A generation-request classification by input form, orthogonal to media type: media type describes the output, while generation type describes inputs. Images use t2i (text-to-image) / i2i (image-to-image); videos use i2v (first-frame-driven image-to-video) / r2v (reference-slot-driven video). Image t2i/i2i can be determined only at execution (see `docs/adr/0001`).
_Avoid_: t2v; confusing it with model capabilities.


**GenerationTypeBucket (任务类型桶)**:
An optional model-setting slot specialized by generation type (image t2i / i2i, video i2v / r2v), refining the default model. Unconfigured buckets fall back to the same layer's default model (see `docs/adr/0054`).
_Avoid_: Treating buckets as mandatory configuration; showing “generation type bucket” in user-facing copy.


**effective model (执行模型)**:
The final model selected by layered resolution given a call site's generation type and current settings. The default-layer model is only one resolution input; the effective model actually executes.
_Avoid_: Treating it as a configurable setting; querying capabilities using the default-layer model.


**video request facts (视频请求事实)**:
Execution facts for one video request: effective model, submitted resolution, narrowed supported durations and reasons, whether duration is endpoint-fixed, and audio-track form. Evaluated once per request; pre-submission and execution differ only in identity source (see `docs/adr/0086`).
_Avoid_: Recomputing individual facts in pricing, preflight, or UI paths.


**image_edit (图片编辑)**:
Changing an existing image according to the creator's editing instructions while preserving major content not requested to change. Editing produces a new version and does not rewrite the image prompt used for the original generation.
_Avoid_: Regenerate, inpaint; saving editing instructions as the image prompt.


### Dimensions and aspect ratios

**aspect_ratio (比例)**:
The output width-to-height ratio (such as `9:16`, `16:9`, or `1:1`), configured per project.
_Avoid_: Mixing aspect ratio into resolution or size fields.


**resolution (分辨率)**:
A clarity tier determining pixel scale, not aspect ratio. For custom values containing a ratio, only the short side determines scale; aspect_ratio still determines the ratio (see `docs/adr/0011`).
_Avoid_: Using resolution to mean provider resolution; describing unset as a specific tier.


**size (尺寸)**:
The final width × height in pixels sent to a backend, derived from aspect ratio and resolution within that backend's pixel constraints.
_Avoid_: Using size as a synonym for aspect ratio or clarity.


**`supported_durations` (可选时长)**:
The discrete set of durations in seconds allowed by a video model (see `docs/adr/0018`). An empty set is legal only when duration is endpoint-fixed (see `docs/adr/0082`).
_Avoid_: Global duration allowlist; treating an endpoint-fixed empty set as missing configuration.


**endpoint-fixed dimension (维度由端点固定)**:
A dimension, size or duration, that ArcReel does not drive and only the endpoint determines. Currently only ComfyUI endpoints enter this state (see `docs/adr/0082`).
_Avoid_: Calling it unsupported or missing configuration.


**duration_resolution_constraints / reference_image_durations (时长联动约束)**:
Two per-model declarations narrowing supported_durations by resolution or reference-image context. The narrowed set and its reasons are video request facts.
_Avoid_: Global duration-constraint table; recomputing narrowing in the UI.


**default_duration (偏好时长)**:
An integer project-level preferred duration. Null or missing means the semantic “auto” tier: the AI selects a duration within supported_durations according to content pacing.
_Avoid_: Reading null as unconfigured and supplying a default without authorization.


### Material storage and provider asset links

**media material (素材)**:
Image, video, or audio content used in creation, with an identity independent of storage location and provider, and potentially multiple content versions. A resource's version history within a project is one media material. A sheet is one use of material; project assets such as characters and scenes are creative objects using material.
_Avoid_: Equating material identity with a local path, object-storage address, or provider asset ID.


**content version (内容版本)**:
Specific content of a media material, corresponding to one version-history record, with a project-unique stable identity, verifiable content digest, and durable locator. Selection, restoration, and resource renaming do not change its identity. Two records with identical bytes, including copies in different projects, are two content versions (see `docs/adr/0109`).
_Avoid_: Using a content hash, asset entry, resource ID plus version number, or file path as content-version identity.


**material storage (素材存储)**:
Where and how material content is saved, including local files and object storage. A content version's location in storage is its durable locator: a local version snapshot within the project, or the object-storage connection, bucket, and key recorded at write time. A resource's formal file is a working copy of the selected version; a temporary signed URL is an access credential. Neither is a durable locator. Formal commit determines new image-version storage from the global “New image storage location” setting, defaulting to local; existing versions are not reinterpreted when settings or connections change (see `docs/adr/0116`). Images without version records, such as end frames and character originals, are excluded and always local.
_Avoid_: Treating provider asset links as a storage type mutually exclusive with local files or object storage; treating formal paths or signed URLs as durable locators.


**formal object (正式对象)**:
An object holding a remote content version in the user's object storage, named by content digest and shared by byte-identical versions within one connection. References belong to their holders: project version histories and project trash, deleted projects, cross-project consumers, upload jobs still within the provider fetch retention period, and ongoing formal commits. Retain it while any reference exists and collect it centrally after all references disappear, including after formal-commit settlement releases its claim. Unreferenced objects pending deletion do not block connection changes or deletion; afterwards they are detached from the current connection and no longer automatically deleted (see `docs/adr/0116`, `docs/adr/0118`).
_Avoid_: Confusing it with a relay copy; deleting by object-prefix scans; letting individual consumers decide when zero-reference objects are deleted.


**work file (工作文件)**:
A local copy of a remote content version at the original snapshot and formal-file locations. It is only a cache: fetch from object storage and verify its digest on demand before reading; remove when idle and unused without affecting the formal object. An absent work file does not mean missing material. Presence and digests come from the version record; fetch only when bytes are actually read.
_Avoid_: Treating a work file as a durable locator; readers bypassing the work-file entry point to download objects themselves.


**provider asset (供应商素材)**:
Image, video, or audio material stored in a provider asset library, identified by the provider and usable subject to its permissions and review conditions.
_Avoid_: Confusing it with ArcReel project assets.


**provider asset link (供应商素材关联)**:
A link between one material content version and a corresponding provider asset, constrained by provider account, project, and usage conditions. Creation freezes the asset-library account's cloud-account identity. Replacing the account's Access Key with one from another cloud account does not move the link to the new identity (see `docs/adr/0115`). One version can link to multiple provider assets; different images or versions have independent links. Upload creates links; selecting an existing library image first saves its actual content as a new content version, then links that version (see `docs/adr/0112`). Generation selects links using the backend's actual inference credential and binding. If an applicable link is unavailable, generation fails instead of sending image content (see `docs/adr/0113`). Archives carry available links for included versions and preserve their frozen scope on import; binding another cloud identity cannot select them, so image content is sent normally (see `docs/adr/0119`).
_Avoid_: Registration, material storage.


**object storage connection (对象存储连接)**:
The region, Endpoint, bucket, and credentials for the user's own object storage. Initially only TOS is supported, independently of media-provider credentials. Configuring a connection alone changes no image's storage location.
_Avoid_: Treating it as a provider; equating connection configuration with enabling remote storage.


**asset library account (素材库账号)**:
An Access Key for a provider asset library's control plane, together with its site, region, and project scope. Control-plane credentials and inference API Keys are not interchangeable. Control-plane requests go only to official hosts for that site (see `docs/adr/0110`).
_Avoid_: Using inference API Keys for the control plane; deriving its host from an inference credential's base_url.


**asset library binding (素材库绑定)**:
The user's explicit confirmation that an inference credential may use an asset-library account's resource scope. One inference credential binds to at most one account. Changes to credential content or endpoint, or account site, region, project, or Access Key ID return it to pending confirmation; treat it as unbound until confirmed. Binding does not replace provider authorization.
_Avoid_: Inferring bindings from provider names or domains; retaining a binding merely because the credential record ID did not change.


**asset upload job (入库任务)**:
Uploading a content version into a specific asset-library call scope (cloud identity, site, region, and provider project). It exists and is queryable from the user's upload action, before a provider asset ID is obtained. Once provider processing completes it becomes the version's provider asset link, usable for generation only when available. Within one scope, one version has at most one currently selected link or upload job; only user upload or unlinking changes selection (see `docs/adr/0111`).
_Avoid_: Registration; equating a returned provider asset ID with usability; recreating after an uncertain submission outcome.


**relay copy (中转副本)**:
A temporary object written to the user's object storage so a provider can fetch an image, retained until the upload job has an outcome or its download URL expires, then cleaned up separately from formal material objects.
_Avoid_: Treating it as material storage or a durable locator; equating signature expiry with object deletion.


### Reference images and compression

**reference_image (原图)**:
A source image uploaded by the user to express a subject's actual appearance or creative intent. Character, scene, and prop originals are only inputs to sheet generation, not direct references for storyboard or video generation; product originals are the exception (see `docs/adr/0034`, `docs/adr/0073`).
_Avoid_: Sheet; calling all uploads reference images; substituting originals for missing sheets.


**reference image (参考图)**:
An image used as a conditioning input in a particular generation to guide identity, style, or composition. Originals, sheets, and other generated results can all serve as reference images in a specific generation.
_Avoid_: Treating reference image as a permanent image type.


**declared reference / available reference image (声明引用 / 可用参考图)**:
Assets referenced as visual guidance in a reference-video unit's body are declared references. The subset whose reference images can actually be sent with the request now are available reference images.
_Avoid_: Assuming a video unit has reference images just because its names are registered.


**reference upload copy (参考上传副本)**:
The bytes used when a reference image is encoded into a provider request. It is a temporary copy deleted after use, not the source asset file or generated output.
_Avoid_: Reading “compress reference image” as compressing source files or outputs.


**reference image compression (参考图压缩)**:
Proportional resizing and re-encoding of only a reference upload copy, with no effect on source assets or outputs (see `docs/adr/0012`).
_Avoid_: Confusing it with compression when saving uploads.


**reference image clamping (参考图裁剪)**:
When assembled reference images exceed the selected model's limit, retain the first N in assembly order and discard the tail; numbering identifies only sent images. Artifact basis still records the complete assembled set (the limit is a provider property; see `docs/adr/0062`). Clamping adds an over-limit `warnings` entry to storyboard/grid task results, as on the reference-video route, and the storyboard final-prompt preview gives the same warning. Users and the Agent can see that some reference images were not sent.
_Avoid_: Treating clamping as type-priority selection; treating it as a silent log-only behavior.


### Billing

**cost (生成费用)**:
The cost of model generation estimated from catalog price tables and multipliers, a reference metric rather than an actual bill. Estimated cost appears before submission; the call's cost is recorded on completion. Costs not attributable to specific content appear as unattributed costs.
_Avoid_: Cost snapshot, cost attribution; calling it an actual bill in user-facing copy.


**reference cost (参考费用)**:
The user-facing cost measure estimated from catalog price tables and multipliers, not a provider invoice amount.
_Avoid_: Actual cost, bill, production cost.


**price table (价目表)**:
Official prices on a catalog entry, in one currency, listing unit prices for meters according to pricing dimensions.
_Avoid_: Pricing shape, rate table.


**meter (计量项)**:
A usage category counted for pricing, such as uncached input tokens, cache-read tokens, output images, output seconds, characters, or requests.
_Avoid_: Pricing unit, billing type.


**pricing dimension (选择维度)**:
A call attribute selecting a meter's unit-price tier, such as resolution, quality, audio, service tier, or context tier.
_Avoid_: Tier, specification.


**context tier (上下文档)**:
The price tier selected by total input tokens of a single request; its token price applies to the entire request.
_Avoid_: Graduated pricing, long-context surcharge.


**price multiplier (倍率)**:
A coefficient expressing channel price differences, applied to the whole reference cost. Provider calls have provider and provider-model multipliers; Agent calls have only an Agent-credential multiplier.
_Avoid_: Discount, group multiplier, completion multiplier.


**unpriced (未定价)**:
The cost state of a provider call with no applicable unit price, distinct from zero cost.
_Avoid_: Free, zero currency units.


**usage record (使用记录)**:
The user-facing projection of provider calls, with generation-call and Agent-session views. The first is a timeline with one row per call; the second groups Agent calls by session, one row per session, expandable to chronological per-model details. It also supports filters, summaries, and attention signals. It is a projection of call facts, not a separately stored entity. Views differ in presentation, not accounting granularity.
_Avoid_: Ledger, bookkeeping, cost details, call records.


**purpose (来源)**:
Why a provider call was initiated: script generation, episode planning, project overview, style analysis, Agent session, endpoint trial run, or generation task.
_Avoid_: origin, call scenario, task type.


**primary currency (主币种)**:
The currency with the largest total reference cost in a usage-record period. Charts and breakdowns plot only this currency; others are listed separately without conversion.
_Avoid_: Default currency, base currency; converting multiple currencies to one amount.


**attention (需要关注)**:
Automatically identified generation-call anomalies in usage records: a provider or model failure rate significantly above the period's overall rate, or repeated failures for one target. Agent calls are excluded; nothing appears without anomalies.
_Avoid_: Alert, alarm, anomaly list.


### Media types and text-to-speech (TTS)

**media_type / call_type (媒体类型)**:
The system-wide media dimension `image` / `video` / `text` / `audio`, used to branch provider resolution, backend families, usage, and billing. `audio` is text-to-speech and, like image/video, is scheduled through the generation queue (see `docs/adr/0010`).
_Avoid_: modality, media kind; using tts / voice / speech to mean audio.


**narration_audio (旁白配音)**:
Independent speech material generated from narration or commentary text, compositable with visuals in post-production.
_Avoid_: Generate narration, video sound, TTS audio.


**narration delivery (旁白交付方式)**:
The project's chosen route for off-screen speech: ArcReel generates narration audio using project TTS settings, or the creator supplies voiceover after delivery. It does not affect video generation.
_Avoid_: Narration mode, voiceover settings; treating it as a per-generation-request parameter.


**`generate_audio` (生成有声视频)**:
Sound generated by a video model together with and synchronized to the visuals.
_Avoid_: Generate audio, narration audio, video audio.


**voice (音色)**:
A provider's preset speaker, determining whose voice synthesized speech sounds like.
_Avoid_: Using voice for the audio media type itself; confusing presets with voice cloning.


**speech rate (编排语速)**:
The absolute rate used to estimate spoken duration (characters/second for Chinese; words/second for English, Vietnamese, and Russian), defaulted by language and overridable per project. It arranges planned durations and validates speech volume during script writing, independently of who speaks.
_Avoid_: Unqualified “speed”; treating it as a TTS speed parameter.


**narration speed (配音语速)**:
A TTS project's speed multiplier for narration audio, relative to the selected voice's own rate. It is part of project TTS settings and affects only narration audio, not dialogue.
_Avoid_: Unqualified “speed”; configuring per-video-unit narration speed; converting it to planning speech rate.


**voice_sample (音色试听)**:
A short audio sample for auditioning and confirming a voice, not material for the final cut.
_Avoid_: Narration audio, reference audio.


**ambiance_audio (环境音描述)**:
The script's textual environmental-sound prompt for the video model, not an audio file.
_Avoid_: Narration audio, generate video with sound.


**voice_consistency (声音一致性档位)**:
A three-level indication of cross-clip character-voice consistency: `native` (character reference audio sent directly in reference-video mode), `soft` (audio track with voice guided only by text), or `none` (silent). Derived from model audio-track support × project generation mode × character voice binding.
_Avoid_: Using generate_audio alone to indicate whether an audio track exists.


**character_voice_binding (角色声音绑定方式)**:
A project setting stating how character voices are constrained: default `prompt` includes voice descriptions as soft constraints; only `reference_audio` attaches configured character reference audio to video-model requests.
_Avoid_: Voice mode, audio mode; using it for narration-audio voice settings.


### Projects and assets

**project (项目)**:
A complete video creation in ArcReel, with its own source files, scripts, assets, generation mode, and final-cut content.
_Avoid_: Work, engineering project; using series to mean the entire project.


**data root (数据根)**:
A deployment's filesystem directory for runtime data, of which projects are only one category.
_Avoid_: Project root, projects directory when meaning data root; treating any directory under it as a project.


**episode (集)**:
A continuous content unit within a project. Ad/short-film projects also have one episode, although the UI may hide episode selection.
_Avoid_: Using series for both an episode and the entire series.


**sheet (资产图)**:
An image establishing a standard visual appearance for a character, scene, prop, or product for reuse in later creation within the project. When used in a generation it is also that generation's reference image. Assets without an available sheet are “pending generation.”
_Avoid_: Design image, setting image, reference-design image.


**character (角色)**:
An asset appearing with a specific identity in project content and reusable across video units. A character has one base look and may have derivatives.
_Avoid_: Person, person asset.


**base look (本体)**:
The persistent appearance defined by the character entry's own description and sheet, referenced by bare `@[角色]` in scripts.
_Avoid_: Default form; treating the base look as a derivative.


**derivative (衍生)**:
Another persistent appearance of a character outside its base look (costume change, transformation, disguise, age change, etc.), sharing name, voice, and identity. It holds only appearance changes relative to the base and one sheet. The sheet is generated by editing the base sheet or replaced by the author's finished upload (see `docs/adr/0072`). Written as `@[角色/衍生]` in scripts.
_Avoid_: Variant (already used for Agent profile variants), look, skin; registering a derivative as a separate character; uploading an original for it (the upload is the finished sheet, not an editing input).


**scene (场景)**:
An environment asset reusable across video units, describing a story location and its spatial appearance.
_Avoid_: Using scene to mean a script video unit or story passage.


**project asset (项目资产)**:
The current project's characters, scenes, props, and products. They belong only to that project and do not automatically synchronize with the global asset library.
_Avoid_: Resource library, setting collection.


**global asset library (全局资产库)**:
A collection for saving and reusing characters, scenes, and props across projects. Character derivatives travel with the base as a child table (see `docs/adr/0072`). Applying an asset creates an independent project asset; later changes do not propagate either way. Library images share material storage with project versions: new images follow global local/object-storage settings. Available provider asset links of the current image version are copied with the image, with independent copies held by the library and each project (see `docs/adr/0120`).
_Avoid_: Project assets; assuming library and project assets synchronize automatically.


**product (商品)**:
A product asset that must be accurately presented and promoted in an ad/short film, with the user's uploaded product originals as the visual-fidelity basis.
_Avoid_: 产品 (“product” when referring to the application), product asset under that alternate Chinese name, product character.


**asset rename (资产重命名)**:
An atomic cascading transaction changing the name of a name-identified asset and updating every reference to that name together (see `docs/adr/0057`).
_Avoid_: Implementing rename as upsert-new-name plus delete-old-name.


**asset merge (资产合并)**:
An atomic cascading transaction merging two same-type character, scene, or prop registrations of one identity into one. References switch together to the retained asset, and the merged name becomes an asset alias (see `docs/adr/0057`, `docs/adr/0092`). A character may also merge as a derivative of a retained character: its name becomes the derivative name rather than a base alias, visual references point to `保留角色/衍生`, and speakers point to the retained character.
_Avoid_: Expressing merge as renaming to an existing name; calling upsert field combination a merge.


**asset alias (资产别名)**:
Other names registered on characters, scenes, or props: names in the source, former names, and merged names except those merged into derivatives. They only help AI recognize one asset when planning or generating scripts; they are not its name (see `docs/adr/0092`).
_Avoid_: Nickname, name table; using aliases in references; unqualified alias; confusing with model aliases.


**style_template (风格模版)**:
A preset whole visual-style prompt (live-action or animation). Selection saves its expanded text as a snapshot in the project style (see `docs/adr/0023`).
_Avoid_: Treating style as a short label; combining it with a style-reference image (they are mutually exclusive).


**clue (线索)**:
ArcReel's former collective term for scenes and props, now split into separate scene and prop asset types.
_Avoid_: Using clue/线索 for scenes or props in new code or documentation.


**project event (项目事件)**:
A server-pushed record telling the browser which project entity changed and how, including entity type, action, id, label, and location. The browser uses it to refresh the project and decide on notifications or navigation (see `docs/adr/0085`).
_Avoid_: Notification (only one frontend presentation of an event); change hint (only triggers rescanning, without describing what happened).


**project state projection (项目状态投影)**:
Rules normalizing current project state (metadata and all scripts) into comparable snapshots, determining which state changes count as project events.
_Avoid_: Shadow schema, hand-picked-field snapshot.


### Scripts and storyboards

**content_mode (创作类型)**:
How project content is organized and expressed, independently of generation mode.
_Avoid_: Confusing content organization with video-generation methods.


**narration (旁白/解说)**:
A content mode organized by continuous narration or commentary, with visuals accompanying the telling.
_Avoid_: Narration mode, storytelling mode.


**drama (剧情演绎)**:
A content mode organized by character action, dialogue, and dramatic scenes, supporting visual styles such as live-action or animation.
_Avoid_: Series animation, drama mode.


**ad (广告/短片)**:
A content mode organized around one final cut and its communication goal, for product promotion, branding, or standalone creative shorts. It has no source text or script plan; AI generates the complete formal script from the creative brief and products, or it is handwritten.
_Avoid_: Ad mode, short-film mode; restricting it to sales videos.


**brief (创作灵感)**:
The creator's free-form description of the desired ad/short film, such as audience, tone, key selling points, and call to action.
_Avoid_: 简报, 需求, 创作简报 (alternate Chinese terms).


**skeleton (骨架)**:
The structural kind of script-entry array, with four values: `segments` (narration), `scenes` (drama), `shots` (ad/short film), and `video_units` (reference video), derived from content mode and generation mode (see `docs/adr/0045`).
_Avoid_: Using skeleton as a synonym for content_mode; binary fallback for unknown modes.


**generation_mode (生成模式)**:
The video-generation method selected at project creation, determining whether storyboard images or references such as sheets drive generation. All episodes share it, and it cannot change after creation.
_Avoid_: Generation route; confusing it with content mode.


**storyboard (分镜图生视频)**:
A generation mode that first generates an image for each storyboard item, then uses it to drive that item's video generation.
_Avoid_: Image-to-video mode, storyboard mode as abbreviated Chinese 分镜模式.


**reference_video (参考生视频)**:
A generation mode that drives videos directly with character, scene, prop, or product reference images, without first generating an image for each storyboard item.
_Avoid_: Reference video as a media object, reference mode, direct-reference output.


**grid (多宫格分镜)**:
A production method generating several storyboard items together as one composite image, then splitting its cells into individual storyboard images to improve style and subject consistency.
_Avoid_: Storyboard board; treating it as a generation mode alongside other modes.


**grid composite (联合图)**:
The whole multicell image generated for a grid group, covering its arranged storyboard items. It is a reviewable artifact that can be replaced by upload or rolled back, not a direct visual input for any storyboard item.
_Avoid_: Grid picture, collage; treating it as a storyboard image.


**grid split (切分落格)**:
Splitting a grid composite into cells and writing the storyboard images for all covered items, explicitly confirmed after reviewing the composite, identically on Web and Agent. A ready but unsplit grid is “unsplit”; overwritten storyboard images remain in version history for rollback.
_Avoid_: Split image, automatic split, pending split; treating splitting as part of generation.


**segment / scene / shot (分镜)**:
One independently editable and generatable script entry when storyboard images drive video: the video unit of storyboard generation. Narration, drama, and ads/short films all organize these scripts into storyboard items. Their count measures content scale for storyboard projects.
_Avoid_: Confusing an item with its image; counting reference-video units as storyboard items.


**storyboard_image (分镜图)**:
An image establishing a storyboard item's composition and starting visuals, also used as visual input for its video generation.
_Avoid_: Storyboard item, sheet, grid storyboard.


**segment_break (章节切分点)**:
A marker on a script storyboard item starting a new chapter, like a video-timeline chapter. It bounds the storyboard-image reference chain and grid groups, and is a generation input rather than an editing decision.
_Avoid_: Scene-based names such as scene switch point (scene is an environment asset); confusing it with source chapters or episode planning.


**video unit (视频单元)**:
Content corresponding to one video generation, the smallest unit of generation, billing, speech ownership, and editing references. In storyboard mode it is a storyboard item; in reference-video mode it is an entry containing free text and planned duration (`video_units` refers only to the latter). Reference-video projects measure scale by video-unit count.
_Avoid_: Scene, unqualified unit; calling reference-video units storyboard items.


**writing_syntax (引用语法)**:
Notation written directly in content by the creator. Reference-video unit bodies support `@[名称]` for registered product/character/scene/prop assets (`@[角色/衍生]` for a derivative), `@[名称]{台词}` for an asset with character speech (a derivative speaker binds only its character's voice), and bare `{台词}` for off-screen narration without an asset reference. A storyboard's `image_prompt.scene` supports only `@[名称]` to identify assets already declared in that item's reference fields; rendering substitutes the reference-image number 图N and does not derive references.
_Avoid_: Confusing notation parsing with asset registration.


**script (脚本)**:
Structured creative content ArcReel organizes from a novel, screenplay, or creative idea for subsequent video-unit generation.
_Avoid_: Calling ArcReel's structured output a screenplay.


**novel (小说)**:
A narrative-text source file for ArcReel to adapt into a script.
_Avoid_: Original work, script.


**screenplay (剧本)**:
A user-uploaded text source that already organizes drama and dialogue. ArcReel should prioritize preserving its audible content and supplement it with video-production information.
_Avoid_: Script.


**source_kind (源文件类型)**:
The type of a source text in drama: novel or screenplay, determining adaptation versus prioritizing the author's existing story and dialogue. It belongs to the source file, not the project: each whole-source file and each episode with its own source has one (see `docs/adr/0036`).
_Avoid_: Confusing it with content mode or generation mode; treating it as project-level.


**utterance (发声条目)**:
The unified unit of spoken content in a video unit. Each entry is either character speech (with a speaker, including dialogue, inner monologue, and a character's off-screen commentary) or off-screen narration (without a speaker), ordered by insertion.
_Avoid_: Treating dialogue and narration as independent unordered fields; counting character off-screen commentary as speakerless narration.


**source_text (对应原文)**:
The novel or screenplay text underlying a video unit, for creators to check its source. It is not read aloud or generated as separate content.
_Avoid_: Treating corresponding source text as narration text.


**end_frame_image (尾帧)**:
The target final image the user specifies for one storyboard item's video generation. It is persistent user intent on the item, not generated output.
_Avoid_: Confusing it with grid-output fields.


**script_plan (脚本规划)**:
The production step organizing an episode's source into script content for creator inspection, and its output: content editable at the review gate and converted to a formal script on confirmation. It also includes AI decisions about assets first appearing in the episode, registered with the formal script on confirmation (see `docs/adr/0092`).
_Avoid_: step1, content organization, preprocessing, content layer, plan file, intermediate state.


**prompt_authoring (提示词编写)**:
The production step filling the visual layer of pending-authoring units in a formal script. Its input is the formal script itself, not the script plan.
_Avoid_: step2, visual expansion, generate-script, incremental authoring.


**prompt form (提示词形态)**:
The representation of visual prompts in the final script: structured JSON or plain text. Both are formal prompt content, and the form itself distinguishes content (see `docs/adr/0070`).
_Avoid_: Treating plain text as a preview cache; assuming the forms interchange losslessly.


**prompt_template (提示词模版)**:
The definition of the complete prompt sent to a model at one generation stage, with data slots in its body and bundled with releases. Only rendering with project data produces the actual sent text (see `docs/adr/0077`).
_Avoid_: Generation scenario, the alternate Chinese prompt 模板; confusing it with style templates or prompt content.


**generation stage (生成环节)**:
A fixed pipeline position initiating one text, image, or video model call, distinguished by production step, content mode, and generation mode, combining identical cases. It corresponds to exactly one prompt template.
_Avoid_: Generation entry point (an admission gate; see `docs/adr/0073`), scenario, call site, builder.


**template category (模版类别)**:
Prompt templates grouped by output media: text generation, sheets, storyboard images, and videos. Slotless visual-style prompts have their own style category.
_Avoid_: Using content mode or generation mode as a category.


**partial (模版片段)**:
Wording referenced by prompt templates, either shared across templates or a named variant split along axes such as source kind or asset type, selected by axis value. Fixed wording used by one template is not a partial.
_Avoid_: Rule constant, prompt_rules; switching axis values with conditionals inside templates.


**negative prompt (负向提示词)**:
Media-specific shared exclusions, one partial each for images and video, rendered as an `Avoid:` line in the prompt body. Only ComfyUI endpoints extract it into a negative-prompt node binding; for other backends it is not a provider parameter. Stages may append their own exclusions but cannot invent separate shared wording.
_Avoid_: Reverse word list, negative_prompt parameter, separate per-stage prohibition lists.


**template body (模版正文)**:
A prompt template's text after removing metadata, containing slots and partial references, displayed and edited in system settings. It is neither the project's source narrative nor the rendered provider prompt.
_Avoid_: Source text (the narrative input), source code; calling it a preview.


**review gate (内容确认)**:
The production step where organized script content awaits creator confirmation. Confirmation converts the script plan to a formal script, after which the plan is read-only.
_Avoid_: Audit gate, access gate, pending.


**pending authoring (待编写)**:
The state of a formal-script video unit whose visual layer has not been authored. Set on review-gate conversion or manual creation without visuals; cleared by prompt authoring or manually completing the visual layer.
_Avoid_: Pending generation, missing prompt, pending.


**invalid draft (待修复草稿)**:
A generated draft failing content constraints that must be corrected before further use. Existing formal content is unaffected. While the draft exists, review confirmation and generation entry points reject further progress. Creators can edit it directly; every save revalidates and adopts it as formal content once violations reach zero. They can also request AI repair, regenerate the entire draft, or discard it.
_Avoid_: Isolated draft, violating artifact, quarantine.


**draft repair (AI 修复)**:
AI correction of the entries violating constraints in an invalid draft, preserving the others. Only episode-level violations permit changing the entire draft. Revalidation adopts it when violations reach zero; otherwise it remains invalid.
_Avoid_: Regenerate, reroll, automatic repair.


**editable draft (可编辑草稿)**:
A draft retrieved from formal content for Agent editing and revalidation, potentially with no violations. Its presence blocks the same operations as an invalid draft. Creators do not edit it directly; they ask the Agent to complete it or discard it.
_Avoid_: Isolated draft, invalid draft.


**soft violation (软违约)**:
A degraded-result conclusion that does not block saving or determine invalid-draft status but must accompany the output, such as missing character reference audio or no scene reference in a unit. Presented to the Agent and creator as a degradation notice.
_Avoid_: Confusing it with a violation; counting it among invalid-draft violation entries.


**needs_replan (需要重新规划)**:
A formal-script video unit state indicating its content cannot generate directly (for example mixed character speech and off-screen narration, missing body, or duration). It must return to planning for rewriting.
_Avoid_: Pending authoring, invalid draft, invalidated.


**episode ledger (分集账本)**:
The ledger of each episode's identity and source origin, the sole truth for episode planning. Whole-source episodes record a source range; own-source and source-free episodes consume no whole source. A split episode's file is derived from the ledger; an own-source episode's file is its source (see `docs/adr/0031`).
_Avoid_: Bypassing the ledger; inferring episode count or planning state from physical files; treating entries without source ranges as abnormal.


**episode order (播出顺序)**:
The single sequence of all episodes in the project, defined by ledger entry order. Creators identify episodes by title and position (see `docs/adr/0096`).
_Avoid_: Episode id, sorting by id, separate numbering sequences by source origin.


**whole source (整本源文)**:
The narrative source from which episode planning splits episodes, consisting of one or more files concatenated in creator-defined order. File order is part of the source, not determined by names; file boundaries are not episode boundaries (see `docs/adr/0097`).
_Avoid_: Sorting by filename to determine order; treating a file as a container for one episode or a batch of episodes.


**episode source (集原文)**:
The source used to plan an episode's script, with three origins: split from the whole source by episode planning, own source uploaded or entered for the episode, or no source (bypassing script planning; except ads/short films, scripts must then be handwritten) (see `docs/adr/0031`).
_Avoid_: Presplit exception, manual presplit as a special case; episode file when referring to content.


**unsplit source (未切分的原文)**:
Whole-source portions belonging to no episode: not-yet-planned text after the last split episode, and unassigned spans before the first or between split episodes after deletion, file insertion, or source edits.
_Avoid_: Gap, skipped source, remaining source.


**ledger_status (集规划状态)**:
An episode's three-state lifecycle in the ledger: `planned` (planned with no downstream artifacts), `consumed` (with downstream artifacts), or `stale` (planning or artifacts misaligned with source after range edits/deletion, manual changes to an artifact-bearing episode's boundaries, or replacement by replanning).
_Avoid_: Confusing it with artifact-currency stale or workflow status: it describes the planning/artifact relationship, not production progress.


**instructions (附加指令)**:
Extra creator requirements for a single planning or generation operation, effective only for that operation. Repeated requirements become preferences in Agent memory.
_Avoid_: Unqualified instructions, user opinions, additional notes, standing preferences, creative requirements, prompt.


**plan (分集规划)**:
Splitting episodes from the whole source in two ways. Server-side AI planning reads source in sections and plans all episodes with complete story arcs in that section (title, hook, source range); manual splitting lets creators mark boundaries directly. Both produce whole-source episodes under the same continuity and replanning rules (see `docs/adr/0032`).
_Avoid_: Having the main Agent read source and select cuts itself; treating manual splits as a separate source origin outside episode planning.


**replan (重新规划)**:
AI planning again from a chosen episode to replace existing whole-source episodes. AI first produces a candidate without changing the ledger. Only creator adoption, after reviewing candidate episodes and the change summary, replaces them atomically; discarding changes nothing. Whole-source edits do not require replanning. Manual boundary changes, split/merge, and unsplitting update the ledger directly without candidates (see `docs/adr/0032`).
_Avoid_: Resetting before planning; exposing reset as the creator action; calling manual splitting replanning.


**reset_episode_planning (重置分集规划)**:
A low-level operation returning whole-source episodes in the ledger to unplanned status, either completely or partially retaining entries before a specified episode. Own-source and source-free episodes are unaffected (see `docs/adr/0032`). The creator-facing adjustment is replanning.
_Avoid_: Treating reset as deletion.


### Artifacts and workflow status

**artifact (产物)**:
A formal project file produced by generation or rendering (sheet, script, storyboard image, video, narration audio, final cut, Jianying draft, etc.), together with its relationship to generation inputs.
_Avoid_: Confusing it with product; replacing artifact existence with file existence.


**artifact_manifest (产物清单)**:
The sole source of truth recording formal artifacts' identities, locations, and generation-basis digests. Video/audio entries also record the formal file's content digest, byte count, and nanosecond mtime at registration: write-time facts, not currency conclusions. Ask the manifest whether artifacts exist and match current content (see `docs/adr/0062`).
_Avoid_: Scanning directories or reading script path strings to determine artifact existence.


**artifact basis (生成依据)**:
A canonical description of inputs determining artifact content: prompt, style, canvas ratio, and the complete reference-image set in assembly order (logical identity and digest per image). Provider, model, resolution, and seed are excluded request parameters. Registration basis is recorded at commit; expected basis is recomputed from current project content. Equality means current (see `docs/adr/0062`). User-supplied artifacts (uploaded sheets, derivative sheets, storyboard images, videos) and sheets applied from the asset library are finished content. While that version remains selected and bytes unchanged, their basis depends only on bytes, not prompts, descriptions, style, or the base sheet. Selecting a generated version or externally replacing the file returns to generation-input-based judgment.
_Avoid_: Mixing basis/provenance with canonical Chinese terms; using the clamped sent subset as basis; including provider request parameters; treating uploads/library-applied artifacts as generated from current content.


**generation input (生成输入)**:
The content inputs assembled from current project state for one stage and target: semantic prompt, style, canvas ratio, and the complete reference-image set in assembly order. Artifact basis is computed from these (see `docs/adr/0062`).
_Avoid_: Including clamping, prompt rendering, or provider parameters; assembling references independently for execution and currency.


**input gap (生成缺口)**:
A missing part of generation input, such as an empty description or a referenced asset without a sheet (see `docs/adr/0098`).
_Avoid_: Saying generation input does not exist; confusing it with missing artifact currency.


**artifact currency (产物时效)**:
A manifest entry's four-state judgment against current content: `current` (matches), `stale` (older but usable), `missing` (expected but absent), or `blocked` (unreadable or basis unavailable). Stale artifacts remain viewable and usable for final cuts and export (see `docs/adr/0062`).
_Avoid_: Calling stale expired; confusing it with ledger_status stale.


**backfill (补录)**:
Registering existing formal project artifacts in the manifest during project migration or archive import (see `docs/adr/0062`, `docs/adr/0098`).
_Avoid_: Confusing it with cost-attribution backfill; claiming.


**project migration (项目迁移)**:
Upgrading a project directory from an old schema to the current one, step by step, on application startup or archive import. Each version has one idempotent step, and failures are isolated per project (see `docs/adr/0022`).
_Avoid_: Confusing it with Alembic database migrations; treating it as a reconciliation entry point.


**migration report (迁移报告)**:
A durable project-local record after project migration or archive import, listing changed and lost content (see `docs/adr/0098`).
_Avoid_: Confusing it with a migration verdict; logging only.


**migration verdict (迁移失败判定)**:
A persistent decision left on a project after an incomplete schema migration, carrying the unchanged failure reason and structured details. While present, the project needs repair: generation entry points reject it, but it remains openable.
_Avoid_: Treating it as a one-time notification; blocking read-only viewing.


**workflow status (制作状态)**:
The authoritative manifest-based judgment for a project and target episode: current content and artifacts, whether each AI operation can run and why not, and the suggested next action. Blockers indicate only whole-project unusability (see `docs/adr/0091`). This is the sole state definition.
_Avoid_: A parallel file-existence stage/progress calculation; using pipeline position as an operation prerequisite.


**workflow plan (制作计划)**:
A step view over workflow status, each step stating content status, available operations, artifacts, and issues. Order is presentation only, not prerequisites. The server generates it authoritatively for both Web and Agent.
_Avoid_: The Agent inferring next steps from files.


**project summary (项目摘要)**:
A cross-episode projection of workflow status for project lists and episode cards: episode progress, available assets and per-episode artifacts, and script status. `verified` compares artifact currency individually for one project's detail/cards. `registered` uses only registrations and file presence, without reading artifact content or producing stale from comparison, for project lists. Replanning-shell stale markers are identical in both. Both use the manifest, not directory scans.
_Avoid_: Replacing target-episode workflow status; interpreting registered as file-existence statistics; assigning one uniform stage to a multi-episode project.


### Files and trash

**file view (文件视图)**:
An interface presenting project files by disk directory structure, labeling each file's role (structure file, referenced formal file, historical version, unreferenced file, derived file).
_Avoid_: File manager, resource explorer; using presence in the file view to determine artifact existence.


**unreferenced file (未引用文件)**:
A project-directory file referenced by no project data, manifest, or version history, such as old media after structural removal or miscellaneous Agent files.
_Avoid_: Orphan file, unregistered file (registration specifically means manifest registration), junk file.


**project trash (项目回收站)**:
Storage for deletion records within a project, outside project content.
_Avoid_: Unqualified trash; treating version history or overwritten versions as project trash.


**deletion record (删除记录)**:
One project-trash record for a deletion, containing deleted content and its prior project position and relationships.
_Avoid_: Trash entry; deleted file when referring to the whole record.


**restore (恢复)**:
Adding back a deletion record or a deleted project (rules in `docs/adr/0107`).
_Avoid_: Rollback (reserved for version history), undo.


**deleted project (已删除项目)**:
A project deleted as a whole but still restorable.
_Avoid_: Archived project; calling project deletion moving it to project trash.


### Editing and final cuts

**edit timeline (剪辑时间线)**:
A named set of editing decisions for one episode: a primary track of clips, transitions, narration, subtitles, and BGM. It is the sole truth for rendered final cuts and Jianying drafts. One episode can have multiple independently edited timelines. It does not update automatically with script changes. It is formal content, not an artifact.
_Avoid_: Unqualified timeline (used for call and conversation timelines), editing plan; treating it as an artifact.


**clip (剪辑片段)**:
One slot in an edit timeline's primary track: visuals from one video unit between in/out points, always using that unit's current video version. Selecting a version changes current. A video unit may supply multiple clips or none for a timeline.
_Avoid_: Unqualified “片段” (already a template partial); confusing clips with video units.


**edit (剪辑)**:
An Agent or creator modifying an edit timeline, excluding rendering it to a file.
_Avoid_: Calling rendering, compositing, or export editing.


**contact sheet (联系表)**:
An image assembled from frames of one video version, labeled with video-unit ID and frame times, for Agent footage review. Returned with tool results, not saved and not an artifact. Sampling guarantees at least one frame per shot and denser frames near video signals.
_Avoid_: Thumbnail, grid (reserved for grid storyboards).


**video signal (视频信号)**:
Three local machine checks computed by bundled ffmpeg for one video version: black intervals, frozen intervals, and shot boundaries. The first two are suspected defects; shot boundaries are structural information. Signals are advisory and do not automatically trim or discard footage. Computed on first viewing, cached per video version, outside the generation pipeline.
_Avoid_: QA result, defect detection (shot boundaries are not defects).


**review-footage (审片子智能体)**:
A read-only subagent reviewing video-unit contact sheets for the main Agent and returning a textual footage report, used for initial full review and comparing old/new regenerated versions. It can only view footage and read project files; contact sheets remain only in its context.
_Avoid_: QA Agent; letting it edit timelines or select versions directly.


**review-images (审图子智能体)**:
A read-only subagent reviewing sheets or storyboard images against settings and prompts for the main Agent and returning a textual image report, used at review checkpoints after generation. It can only view images and read project files; images remain only in its context. The user decides regeneration or editing.
_Avoid_: QA Agent; letting it regenerate or edit images directly.


**checklist (勾选清单)**:
Decisions submitted together to the creator through AskUserQuestion multiselect after an editing round: which video units to regenerate (with preflight estimated costs and tiers) and which off-screen narration to rewrite. Selection is authorization; do not ask again during execution.
_Avoid_: Asking separately for each paid action; including version selection (which needs no confirmation).


**final cut (成片)**:
An episode video file rendered from an edit timeline, an artifact whose currency determines whether it matches the timeline.
_Avoid_: Composite video, final video; calling one video unit's video a final cut.


**JianYing draft (剪映草稿)**:
An export of an edit timeline for further refinement in Jianying. In Chinese writing and UI, always call it 剪映草稿, not bare 草稿.
_Avoid_: Bare draft (reserved for invalid/editable drafts in script workflow), editing draft.


**subtitle draft (字幕草稿)**:
Mechanically generated timed subtitles from dialogue and off-screen narration by video unit, an artifact without a claim of line-by-line audio alignment (splitting/timing rules in `docs/adr/0089`). It is the only source of subtitle text on an edit timeline; the timeline places subtitles without editing or deleting them. Text edits return to the script.
_Avoid_: Treating timeline subtitle text as independently editable; bare draft.


### Agent runtime

**ArcReel Agent**:
The conversational AI in ArcReel that understands creator requests, helps organize content, and invokes creation capabilities. The Chinese product name is ArcReel Agent; other Chinese explanations may use Agent or 智能体.
_Avoid_: Assistant, creation assistant, Copilot.


**external Agent integration (外部 Agent 接入)**:
Connecting a trusted Agent outside ArcReel, including installing public skills, configuring remote MCP and an API Key, and verifying connectivity.
_Avoid_: Calling it only skills installation; confusing it with ArcReel Agent configuration.


**Agent toolset (Agent 工具集)**:
The same creation tools supplied to ArcReel Agent and external Agents, with the same capabilities, results, and migration-failure constraints. They differ only in project selection (session-bound for ArcReel Agent, explicit per call externally) and generation submission (ArcReel Agent waits for completion; external Agents receive a receipt then query results).
_Avoid_: Tool directory, MCP tool list, tool registry, sdk tools.


**subagent (子智能体)**:
Focused work delegated by an Agent and executed by a subordinate Agent at runtime. The main conversation displays only its goal, status, and result.
_Avoid_: Subtask, sub-Agent.


**SessionActor**:
The per-session executor serializing all SDK calls for that Agent session (see `docs/adr/0028`).
_Avoid_: Confusing it with ManagedSession.


**autonomous turn (自主轮次)**:
A new Agent-session turn started after a turn ends without a user message, typically from CLI-injected notifications of background subagent completion. The session returns from idle to running.
_Avoid_: Treating result as proof the session will produce no more messages.


**queued message (排队消息)**:
A user-sent message not yet admitted to the Agent conversation. It becomes a user message on the conversation timeline only upon admission, positioned at that actual time.
_Avoid_: Treating queued messages as already in the conversation; ordering them by send time; calling admission 受理 (confused with AI-action admission).


**send now (立即发送)**:
The action making one queued message interrupt the current turn and be admitted before other queued messages. The interrupted turn is recorded as interrupted, just like user stop.
_Avoid_: Steering, queue jumping.


**agent_startup_failure (Agent 启动失败)**:
A system failure before the Agent establishes a usable runtime environment, before any conversation turn.
_Avoid_: Confusing it with Agent turn failure.


**agent_turn_failure (Agent 轮次失败)**:
A failed terminal state in which a turn does not complete after successful startup. It is a system-failure event, not an Agent reply.
_Avoid_: Showing SDK-synthesized errors as normal Agent replies; treating user-stopped or send-now-interrupted turns as failures.


**failure_observation (故障观测)**:
The context and raw failure facts ArcReel actually obtains during an Agent failure: diagnostic evidence, not a root-cause conclusion.
_Avoid_: Normalizing unrecognized facts into “unknown error.”


**agent memory (Agent 记忆)**:
Natural-language notes about creators and projects that the Agent writes autonomously, persists across sessions, and creators can inspect and edit, consisting of a memory index and on-demand memory files. Record only preferences, corrections, decisions, and external leads unavailable from project data or instructions and useful in future sessions. Split into project and user memory.
_Avoid_: Formal project data, SDK transcripts/session event logs, additional instructions, or production progress as memory; calling human-written instruction files such as `CLAUDE.<mode>.md` memory.


**memory index (记忆索引)**:
The directory of one Agent memory, one line per entry summarizing and pointing to a memory file, loaded when a session starts.
_Avoid_: Index file; putting memory details directly into the index.


**memory file (记忆文件)**:
One note referenced by the memory index, recording one matter and read only when relevant to the task.
_Avoid_: Theme file, topic file: theme is first read as a creative-work theme and also conflicts with interface themes in Chinese.


**project memory (项目记忆)**:
Agent memory traveling with a project and effective only in its sessions, carrying style preferences, confirmed practices, and creator corrections for that project.
_Avoid_: Cross-project reuse; writing project memory into formal project data; confusing it with project settings.


**user memory (用户记忆)**:
Agent memory belonging to a user and effective across all that user's projects, carrying identity background and long-term work preferences.
_Avoid_: Binding it to an instance or host directory; recording content true for only one project.


**project private area (项目私有区)**:
Runtime-managed storage within a project directory, outside formal project data, for content such as project memory that travels with the directory but not archives. It is excluded from archives and data validation and survives runtime-profile materialization and resets.
_Avoid_: Confusing it with profile materialization directories; exporting or validating its contents as formal data.


**SDK transcript**:
The session record the SDK writes using its own protocol, solely to reconstruct Agent context.
_Avoid_: Using it as the UI conversation timeline's data source.


**session event log (会话事件日志)**:
The sole read source for the conversation timeline: an ordered sequence per session, materialized from the SDK transcript.
_Avoid_: Treating it as a second truth source to reconcile against the transcript.


**message rewrite (消息改写)**:
Editing and rerunning a historical user message, equivalent to returning to before that message and sending the revised content anew. The original and all subsequent conversation are discarded.
_Avoid_: Confusing it with image editing.


**session branch (分支会话)**:
A new session carrying a message rewrite: the prefix before the rewrite point is its entire history. The original session remains as an invisible backup (see `docs/adr/0058`).
_Avoid_: Confusing it with native SDK `fork_session`.


**subagent timeline (子时间线)**:
The message sequence belonging to one subagent within a session, represented by one collapsible subagent card on the main timeline.
_Avoid_: Flattening subagent messages into the main timeline.


**agent_runtime_profile (Agent 运行 profile)**:
The Agent-specific runtime configuration tree, physically separate from developers' local editor configuration, materialized into projects at runtime.
_Avoid_: Using .claude as an imprecise collective name; calling it agent config.


**materialization (profile 物化)**:
Writing the Agent runtime profile into a project directory, selecting variants by the project's content mode.
_Avoid_: Generic sync / copy.


**agent_sandbox (Agent 沙箱)**:
The kernel-level isolation layer around Agent tool calls, constraining filesystem and network access of all subprocesses in the sandbox (see `docs/adr/0025`, `docs/adr/0026`).
_Avoid_: Using sandbox to mean the application path-guard hook.


**AgentAccessPolicy**:
The single rule source for what the Agent may access, projected separately for kernel sandbox and application guard (see `docs/adr/0046`).
_Avoid_: SandboxPolicy.


### Authentication and credentials

**session JWT (会话 JWT)**:
An administrator credential issued on interactive login, normally granting all ArcReel management capabilities. Leakage means full administrator compromise.
_Avoid_: Calling every bearer token an API Key.


**API Key**:
A broad-permission automation credential accessing most business and configuration capabilities but not API Key management. Leakage remains a high-impact security event. The UI calls them 访问令牌 / Access tokens, distinguishing the issuance direction from media-provider keys.
_Avoid_: Treating it as identical to session JWT; scoped token; UI labels API 令牌 or API 密钥.


**download token (下载 token)**:
A short-lived project-export credential valid only for the export endpoint, currently the only credential form of self-authenticated endpoints (see `docs/adr/0071`).
_Avoid_: Calling it one-time or low-privilege; placing session JWTs in URLs.


**browser-native request (浏览器原生请求)**:
A request issued by the browser itself without an `Authorization` header: only `<img>` / `<video>` src loading and native download navigation. It motivates anonymous static-media reading and download tokens.
_Avoid_: Browser-direct request; including SSE (an ordinary credentialed API request; see `docs/adr/0071`).


**self-authenticated endpoint (自带认证端点)**:
An endpoint validating a dedicated credential internally rather than relying on login, always motivated by browser-native requests. Like public endpoints it needs no login, but it rejects anonymous requests.
_Avoid_: Confusing it with public endpoints.


### Interface editing and layout

**edit unit (编辑单元)**:
The interface scope submitted by one explicit save, such as a settings view, asset card, storyboard detail, or memory file. Fields first become unsaved changes; the unit saves or discards them together. Before leaving or switching with unsaved changes, ask whether to keep editing, discard, or save. Upload, generate, rename, and delete act immediately, outside edit units. Structure and navigation interception are in “编辑单元与离开拦截” in `website/docs/dev/architecture.md`.
_Avoid_: Edit mode, immediate per-field saves.


**unsaved changes (未保存修改)**:
Local edits in an edit unit that have not been submitted. If the Agent or pushed updates change saved content meanwhile, retain local edits and let the creator adopt the new content or insist on saving (last writer wins).
_Avoid_: Bare draft (reserved for invalid/editable drafts), local draft.


**wide window (宽窗口)**:
Layout issues caused by a wide CSS viewport, such as logical widths 1920 or 2560, unrelated to pixel density.
_Avoid_: Big screen, high-resolution screen, high DPI.
