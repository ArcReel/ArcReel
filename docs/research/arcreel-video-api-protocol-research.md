# ArcReel video API protocol adaptation research report

**Research cutoff date**: 2026-05-27
**Purpose**: input material for subsequent PRDs and design documents
**Author**: research assistance (Claude)
**Background**: extending ArcReel video providers and integrating custom-provider ecosystems

---

## 0. Research scope and positioning

This report compiles **research material**. It **does not include** a concrete directory structure, Adapter class design, or implementation plan. Those belong in subsequent PRDs and design documents, based on ArcReel’s current `lib/backends/video_backends/` + `lib/custom_provider/` architecture.

Questions covered:
1. De facto standards for video API endpoint formats at relay services
2. API specifications of major official video-generation platforms
3. Protocol capability matrix (text-to-video / image-to-video / first-and-last-frame / reference-to-video / video continuation / audio / lip sync / character consistency)
4. Feasibility assessment and priority recommendations for each protocol
5. Complete API specification details for the highest-priority protocols
6. Custom-provider integration approach (plugin vs. declarative)
7. Feasibility and design options for runtime plugins (supporting planned community protocol sharing)

Outside scope:
- ArcReel source-directory organization / class inheritance / file naming
- Database schema changes / Alembic migrations
- Frontend UI changes
- Concrete implementation timeline

---

## 1. ArcReel architecture research baseline (important: aligned with the current implementation)

> This section is a **prerequisite for understanding subsequent conclusions**. All protocol adaptation recommendations must fit this architecture.

### 1.1 Video backend abstraction

ArcReel already has a mature video-generation abstraction in `lib/backends/video_backends/`:

| Key element | Location | Description |
|---|---|---|
| `VideoBackend` Protocol | `lib/backends/video_backend_contract.py` | Duck-typed contract requiring `name` / `model` / `capabilities` / `generate()` |
| `VideoCapability` enum | `lib/backends/video_backend_contract.py` | Capability bitmap: `TEXT_TO_VIDEO` / `IMAGE_TO_VIDEO` / `GENERATE_AUDIO` / `NEGATIVE_PROMPT` / `VIDEO_EXTEND` / `SEED_CONTROL` / `FLEX_TIER` |
| `VideoGenerationRequest` / `VideoGenerationResult` | Same as above | Unified request/response dataclasses |
| `register_backend(name, factory)` | `lib/backends/video_backends/registry.py` | Registration mechanism |
| Existing backend implementations | `gemini.py` / `ark.py` / `grok.py` / `openai.py` / `newapi.py` / `vidu.py` | 6 providers |

**ArcReel glossary convention**: **backend** means the generation client object constructed from provider + model that actually calls the API; the glossary’s `_Avoid` discourages `adapter`. These objects perform the Adapter role in Ports & Adapters, but ArcReel consistently calls them backends to match provider-derivation semantics, complement frontend, and keep naming consistent across the three media backend families (matching SQLAlchemy / Django conventions for similar roles). See 9.1 for architecture alignment.

### 1.2 Custom-provider system

`lib/custom_provider/` already lets users connect arbitrary OpenAI/Google-compatible relay services:

| Key element | Role |
|---|---|
| `CustomProvider` ORM (DB table) | `discovery_format` ∈ {openai, google} + `base_url` + `api_key` |
| `CustomProviderModel` ORM (DB table) | Each model has an `endpoint` (ENDPOINT_REGISTRY key) |
| `ENDPOINT_REGISTRY` | Single source of truth for protocol assignment |
| `infer_endpoint()` | Heuristically infers the endpoint from model_id |
| `create_custom_backend()` factory | Dispatches to the corresponding VideoBackend by endpoint |
| Frontend UI | Settings-page CRUD and automatic `/v1/models` discovery |

**ENDPOINT_REGISTRY currently has 6 relevant entries** (excluding anthropic-messages and others outside this research):

| Endpoint Key | media_type | family | Status |
|---|---|---|---|
| `openai-chat` | text | openai | Implemented |
| `gemini-generate` | text | google | Implemented |
| `openai-images` | image | openai | Implemented |
| `gemini-image` | image | google | Implemented |
| `openai-video` | video | openai | Implemented (**OpenAI Sora `/v1/videos` compatible**) |
| `newapi-video` | video | newapi | Implemented (**NewAPI’s own `/v1/video/generations` protocol**) |

### 1.3 Built-in providers

`PROVIDER_REGISTRY` in `lib/config/registry.py` already includes 5 built-in providers:

- `gemini-aistudio` / `gemini-vertex`: full Veo 3.1 family
- `ark`: Volcengine Ark (Seedance 1.5 Pro / 2.0 / 2.0 Fast registered)
- `grok` — xAI Grok Imagine Video
- `openai` — Sora 2 / Sora 2 Pro
- `vidu`: full Vidu Q3 family (including reference-to-video)

### 1.4 Protocol binding at model level (architecture decision)

ArcReel explicitly confirmed in `2026-04-26-custom-provider-model-endpoint-design`:

> One relay service = one provider; different models under the same provider may use entirely different protocols;
> Protocol assignment belongs at model level (the `CustomProviderModel.endpoint` field);
> Provider-level `discovery_format` is only for model discovery, decoupled from the invocation protocol.

**All subsequent protocol adaptation designs must follow this architecture constraint.**

---

## 2. Relay-service protocol ecosystem (de facto standards)

Research into 15+ major relay services (the main NewAPI repository, AiHubMix, Qiniu Cloud, Wisdom Gate, AI/ML API, APIMart, EvoLink, CometAPI, Kie.ai, PiAPI, useapi.net, kazhang.ai, apiyi, burn.hair, closeai, etc.) identified **four coexisting protocol families**:

### 2.1 Family A: OpenAI Sora `/v1/videos`

**Endpoints**: `POST /v1/videos` + `GET /v1/videos/{video_id}` + `GET /v1/videos/{video_id}/content`
**Format**: multipart/form-data or JSON
**Parameters**: `model` + `prompt` + `seconds` (string) + `size` ("1280x720") + `input_reference`
**Authentication**: `Authorization: Bearer`
**Examples**: official OpenAI, AiHubMix, Qiniu’s sora route, Wisdom Gate, Azure OpenAI, ArcReel’s existing `openai-video` endpoint

**Key facts**:
- OpenAI officially announced that **Sora 2 / Sora 2 Pro retire on 2026-09-24** (developer notification issued 2026-03-24)
- The de facto path standard will remain; relay services will continue using it for other models
- Qiniu’s compatible route returns `task_result.videos[0].url` directly in its query response, whereas official OpenAI requires a second GET to `/content`

### 2.2 Family B: NewAPI’s own `/v1/video/generations`

**Endpoints**: `POST /v1/video/generations` (note the plural) + `GET /v1/video/generations/{task_id}`
**Format**: pure JSON
**Parameters**: `model` + `prompt` + `image` + `duration` (number) + `width`/`height` + `metadata` (object)
**Authentication**: `Authorization: Bearer`
**Examples**: all relay services deployed on NewAPI / OneAPI (dozens including DMXAPI, closeai, burn.hair); ArcReel’s existing `newapi-video` endpoint

**Key facts**:
- `metadata{}` is a vendor-specific passthrough black box (NewAPI documents only Kling’s `image_tail`/`negative_prompt`/`seed` and Jimeng’s `req_key`/`image_urls`/`aspect_ratio`; everything else depends entirely on relay middleware)
- Metadata passthrough completeness varies greatly: the same Kling `camera_control` works on DMXAPI but is lost on some self-hosted NewAPI deployments
- State machine: `queued / in_progress / completed / failed`

### 2.3 Family C: generic v2 generations (one endpoint for all models)

**Endpoints**: `POST /v2/video/generations` or `/v2/videos/generations` (minor provider differences)
**Format**: pure JSON
**Core feature**: **one endpoint + model-field switching**, supporting dozens or hundreds of models (Kling/Veo/Sora/Hailuo/Wan/Seedance under one URL)
**Authentication**: `Authorization: Bearer`
**Examples**:
- **AI/ML API** (aimlapi.com) `/v2/video/generations`: the most typical Family C example
- **getimg.ai** `/v2/videos/generations`
- **APIMart** `/v1/videos/generations`
- **EvoLink** `/v1/videos/generations`
- **Qiniu veo** route `/v1/videos/generations`
- **Official xAI** `/v1/videos/generations` (**the official API itself uses this format**)
- **CometAPI** `/volc/v3/contents/generations/tasks` (different path details, same approach)

**Key facts**:
- Effectively a **discriminated union by model**: one URL accepts 60+ model IDs, but each model’s optional field set is completely different
- Example under one endpoint: Kling accepts `cfg_scale` / `negative_prompt` / `image_url` / `last_image_url`; Veo 3.1 accepts `image_urls[]`; Seedance accepts `reference_images[]` / `reference_audios[]` / `reference_videos[]` / `generate_audio`; Sora accepts `resolution` / `image_url`
- Model naming is highly fragmented: AIMLAPI uses `kling-video/v1/standard/text-to-video`, APIMart uses `sora-2-vip`, and getimg.ai uses `happyhorse-1` (its own brand name)
- **Widely adopted by relay services**, a mainstream de facto standard alongside Family B
- **Not yet covered** by ArcReel’s current ENDPOINT_REGISTRY

### 2.4 Family D: verb-based create/submit style

**Endpoints**: `POST /api/v1/jobs/createTask`, `POST /api/v1/task`, `POST /v1/videos/create`, etc. (separate create + query)
**Format**: pure JSON, parameters nested under `input{}`
**Examples**:
- **Kie.ai** `/api/v1/jobs/createTask` + `/api/v1/jobs/recordInfo`
- **PiAPI** `/api/v1/task` + `/api/v1/task/{task_id}`
- **useapi.net** `/v1/{vendor}/videos/create`
- **Suchuang** `/api/sora2/submit`
- **laozhang.ai** `/veo/v1/api/video/submit`

**Key facts**:
- Family D is internally inconsistent; paths and parameter details vary greatly, preventing reuse through one backend
- PiAPI uses nonstandard status codes (outer `status: "completed"` string + inner `output.status: 99` integer for completion), requiring checks at both levels

### 2.5 Observed family distribution

By number of relay services covered (**not rigorous market share**):

- Family A (OpenAI Sora compatible): high coverage, essential for overseas/compliance-oriented customers
- Family B (NewAPI’s own): **de facto monopoly among domestic self-hosted relay services**, highest coverage
- Family C (generic v2 generations): **widely adopted**, used by overseas aggregators and official xAI; second-highest coverage
- Family D (verb-based): fragmented, mostly custom per service, limited coverage per implementation

---

## 3. Major official video-platform APIs

### 3.1 Platforms already integrated into ArcReel

| Platform | Current status | Protocol | Notes |
|---|---|---|---|
| Google Veo 3.1 | Implemented | Vertex AI + AI Studio backends | Text-to-video/image-to-video/extension/audio/negative prompts |
| Volcengine Ark Seedance 2.0 | Implemented | Ark SDK | 2.0 registered, but **multimodal references and video extension are not enabled** |
| xAI Grok Imagine | Implemented | xAI SDK | Text-to-video/image-to-video/reference-to-video |
| OpenAI Sora 2 | Implemented | openai SDK | Includes `create_and_poll` |
| Vidu Q3 | Implemented | httpx | Text-to-video/image-to-video/**reference-to-video** (used in reference-to-video mode) |

### 3.2 Official platforms researched but not yet integrated

#### Official Kling

- **Base URL**：`https://api.klingai.com`
- **Authentication**: JWT HS256, payload `{iss: ak, exp: now+1800, nbf: now-5}`, expires after 30 minutes
- **Endpoints**: multiple by intent: `/v1/videos/text2video`, `/image2video`, `/multi-image2video`, `/video-extend`, `/lip-sync`
- **Capabilities**: T2V / I2V / FLF (pro mode `image_tail`) / R2V (`elements[]` 1.6) / Extend / Audio (2.6 pro `enable_audio`) / LipSync
- **Pitfalls**:
  - Status string is `succeed`, not `succeeded`
  - Video URL is at `$.data.task_result.videos[0].url`
  - 500 indicates moderation rejection (check error, not message)
  - `kling-2.1-master` does not support `mode`; 2.x models do not support `cfg_scale`
- **Value for web-fiction workflows**: one of China’s strongest video models; essential direct connection for self-hosted studios

#### Alibaba DashScope Wan

- **Base URL (Beijing)**: `https://dashscope.aliyuncs.com/api/v1`
- **Base URL (Singapore)**: `https://dashscope-intl.aliyuncs.com/api/v1`
- **Authentication**: `Authorization: Bearer` + mandatory `X-DashScope-Async: enable` header
- **Endpoints**: `POST /services/aigc/video-generation/video-synthesis` (wan2.5+ multimodal) / `POST /services/aigc/image2video/video-synthesis/` (wan2.1 / wan2.2-s2v) + `GET /tasks/{task_id}`
- **Capabilities**: T2V / I2V / FLF (wan2.1-kf2v) / R2V (`ref_image_urls[]`) / Extend / Audio / digital-human LipSync (wan2.2-s2v)
- **Multiple models on one protocol**: wan2.7-t2v / wan2.6-i2v-flash / wan2.1-kf2v-plus / wan2.2-s2v
- **Pitfalls**:
  - Omitting `X-DashScope-Async: enable` causes an indefinite synchronous wait
  - wan2.7 uses `resolution`+`ratio`; wan2.6 uses `size` ("1280*720", asterisk)
  - All-uppercase state machine: `PENDING / RUNNING / SUCCEEDED / FAILED / CANCELED / SUSPENDED / UNKNOWN`
  - Temporary OSS URLs expire after **24h**; task_id also lasts 24h
  - `IPInfringementSuspect` / `DataInspectionFailed` are moderation errors
- **Value for web-fiction workflows**:
  - wan2.7 accepts extremely long Chinese prompts, enough for a complete storyboard script in one request (5 shots, 1500 characters works)
  - `shot_type: multi` provides native multi-shot storytelling

#### Official MiniMax Hailuo

- **Base URL (global)**: `https://api.minimax.io/v1`
- **Base URL (domestic)**: `https://api.minimaxi.com/v1`
- **Authentication**: `Authorization: Bearer`
- **Endpoints**: `POST /video_generation` + `GET /query/video_generation?task_id={id}` + **`GET /files/retrieve?file_id={id}`** (two-step URL retrieval)
- **Capabilities**: T2V / I2V / FLF / R2V (S2V-01 `subject_reference`) / Audio (Hailuo 2.3) / Director models support shot instructions embedded in prompts
- **Pitfalls**:
  - **Two-step URL retrieval**: query returns only `file_id`, not the video URL; a second File API call is needed
  - **Download URLs last only 9 hours** (32,400 seconds, wording from the official announcement), the shortest across platforms
  - Status strings begin uppercase: `Preparing / Queueing / Processing / Success / Fail`
  - Webhook registration must echo `challenge`; 3-second timeout
  - Domestic / global endpoints and API keys are separate
  - `base_resp.status_code != 0` indicates a business error (HTTP 200 may still contain a failure)
- **Value for web-fiction workflows**: leading price/performance, strong physical behavior

#### Runway Gen-3 / Gen-4 / Gen-4.5

- **Base URL**：`https://api.dev.runwayml.com/v1`
- **Authentication**: `Authorization: Bearer` + mandatory `X-Runway-Version: 2024-11-06` header
- **Endpoints**: `/image_to_video` / `/text_to_video` / `/video_to_video` + `/tasks/{id}`
- **Capabilities**: T2V (gen4.5) / I2V / R2V (gen4_image `@mention`) / Aleph video editing / Act-Two LipSync
- **Pitfalls**:
  - No webhook; polling required
  - Files use an ephemeral upload URI
  - Duration only `5 | 10`
- **Business value**: strong overseas advertising/e-commerce demand; Gen-4.5 ranks 1st in Arena

#### Luma Dream Machine (Ray-2)

- **Base URL**：`https://api.lumalabs.ai/dream-machine/v1`
- **Authentication**: `Authorization: Bearer`
- **Endpoints**: **single `POST /generations`** + `GET /generations/{id}`
- **Capabilities**: T2V / I2V / FLF / Extend / Loop (**two `keyframes.frame0/frame1` slots elegantly express all intents**)
- **Key design**: keyframes union `{type: "image"|"generation", url|id}`; a generation id in frame0 means Extend
- **Pitfall**: the keyframes union requires special modeling; the `image_url` field cannot be reused

#### Pika 2.2

- **Official API is not self-service**, available only to B2B partners
- **Only integration route**: fal.ai `queue.fal.run/fal-ai/pika/v2.2/{capability}`
- **Exclusive capabilities**: Pikaframes (2-5-keyframe interpolation), Pikascenes (multi-image fusion, `ingredients_mode: creative|precise`)

#### PixVerse

- **Base URL**：`https://app-api.pixverse.ai`
- **Authentication**: custom `API-KEY:` header + mandatory `Ai-trace-id` idempotency key
- **Endpoints**: `/openapi/v2/video/{text|img|transition|extend|fusion|lipsync}/generate`
- **Capabilities**: T2V / I2V / FLF (Transition) / R2V (Fusion) / Extend / Audio (V5.5+) / LipSync
- **No native NewAPI channel**; official direct connection required

#### ByteDance Jimeng (Volcengine CV)

- **Base URL**：`https://visual.volcengineapi.com`
- **Authentication**: **AWS V4-style signing** (Volcengine SigV4)
- **Endpoints**: `?Action=CVSync2AsyncSubmitTask` / `?Action=CVSync2AsyncGetResult`
- **Extremely complex**; relay recommended over direct integration

#### fal.ai

- **Base URL**：`queue.fal.run/{vendor}/{model}/{capability}`
- **Authentication**: fal.ai Key
- **Features**: native Webhook support (`?fal_webhook=URL`) + JWKS signature verification
- **Value**: the only feasible Pika 2.2 integration route; also supports models such as Luma / Veo / Kling

---

## 4. Capability matrix (cross-platform comparison)

### 4.1 Capability definitions

- **T2V**: text-to-video, prompt only
- **I2V first frame**: image-to-video (first-frame constraint)
- **FLF**: First-and-Last-Frame
- **R2V**: reference-to-video (multiple reference images maintain character/object consistency, **not a first frame**)
- **Extend**: video continuation/extension
- **Audio**: native joint audio/video generation
- **LipSync**: lip synchronization
- **Character**: character consistency (named-entity registration)

### 4.2 Relay-service families × capabilities

✅ Native ｜ 🟡 Partial support/model restrictions ｜ ❌ Unsupported

| Family / platform | T2V | I2V | FLF | R2V | Extend | Audio | LipSync | Character |
|---|---|---|---|---|---|---|---|---|
| **Family A** OpenAI Sora `/v1/videos` | ✅ | ✅ | ❌ | 🟡 characters API | ✅ extensions | ✅ | ❌ | ✅ |
| **Family B** NewAPI `/v1/video/generations` | ✅ | ✅ | 🟡 metadata.image_tail | 🟡 metadata passthrough | ❌ | 🟡 passthrough | ❌ | ❌ |
| **Family C** AIMLAPI `/v2/video/generations` | ✅ | ✅ | ✅ | ✅ | 🟡 vendor-specific | ✅ | ❌ | ✅ |
| **Family C** APIMart `/v1/videos/generations` | ✅ | ✅ | ❌ | 🟡 prompt-mention | 🟡 sora ref | ✅ Sora2 | ❌ | 🟡 prompt-mention |
| **Family D** Kie.ai `jobs/createTask` | ✅ | ✅ | 🟡 model-specific | ✅ character_id_list ≤5 | 🟡 veo extend | ✅ veo/sora2/wan2.7 | ❌ | ✅ |
| **Family D** PiAPI `task` | ✅ | ✅ | ✅ image_tail_url | ✅ elements | ✅ task_type=extend | ✅ Seedance 2.0 | ✅ separate task | ✅ |

### 4.3 Official platforms × capabilities

| Platform | T2V | I2V | FLF | R2V | Extend | Audio | LipSync | Character |
|---|---|---|---|---|---|---|---|---|
| OpenAI Sora 2 | ✅ | ✅ | ❌ | 🟡 characters | ✅ 6×20s=120s | ✅ | ❌ | ✅ |
| Google Veo 3.1 | ✅ | ✅ | ✅ | ✅ | 🟡 /extend | ✅ 48kHz | ❌ | 🟡 |
| Volcengine Seedance 2.0 | ✅ | ✅ first_frame | ✅ last_frame | ✅ 9 images + 3 videos + 3 audio files | ✅ | ✅ | ✅ phoneme 8 languages | ✅ omni-ref |
| Kling 2.6 / 3.0 | ✅ | ✅ | ✅ pro mode | ✅ elements ≤4 | ✅ extend | ✅ 2.6 pro | ✅ lip-sync task | ✅ multi-image |
| MiniMax Hailuo 02 / 2.3 | ✅ | ✅ | ✅ | ✅ subject_reference | ❌ | ✅ 2.3 | ❌ | ✅ S2V-01 |
| PixVerse V5.5 / V6 | ✅ | ✅ | ✅ Transition | ✅ Fusion | ✅ extend | ✅ V5.5+ | ✅ | ✅ ≤3-image fusion |
| Vidu Q3 | ✅ | ✅ | ✅ | ✅ ref_image_urls ≤7 | 🟡 | ✅ + bgm | ❌ | ✅ |
| Runway Gen-4.5 | ✅ | ✅ | ❌ | ✅ gen4_image @mention ≤3 | 🟡 expand | ❌ | ✅ Act-Two | ✅ |
| Pika 2.2 | ✅ | ✅ | ✅ Pikaframes | ✅ Pikascenes ≤5 | 🟡 | ❌ | ❌ | ✅ |
| Luma Ray-2 | ✅ | ✅ keyframes.frame0 | ✅ frame0+frame1 | ❌ | ✅ generation id | ❌ | ❌ | 🟡 chain |
| Alibaba Wan 2.7 | ✅ | ✅ | ✅ | ✅ Image1..5 placeholders | ✅ | ✅ | ✅ s2v digital human | ✅ |
| xAI Grok Imagine | ✅ | ✅ image:{url} | ❌ | ✅ reference_images ≤7 | ✅ extensions | ❌ | ❌ | 🟡 |

### 4.4 Key observations

- **7 R2V field naming forms**: `characters` / `reference_images` / `image_urls` / `reference_image_urls` / `elements` / Wan 2.7 prompt placeholders / `subject_reference`
- **4 FLF field names**: `last_frame_image` / `image_tail` / `last_image_url` / `keyframes.frame1`
- **Duration representation** differs across platforms: string `"5"` / integer `5` / frame count / enum `6|10` / Luma `"5s"` / Sora string enum `"4"|"8"|"12"|"16"|"20"`
- **Synchronized audio/video generation** has overlapping semantics: joint generation (Veo/Sora/Seedance/Vidu Q3) vs. explicit toggle (Kling 2.6 pro `enable_audio` / Vidu `generate_audio`) vs. automatic (Hailuo 2.3)

---

## 5. Parameter alignment (15 comparison dimensions)

| Dimension | OpenAI Sora 2 | xAI Grok | Kling | MiniMax | Veo 3.1 | Wan 2.7 | Runway | Luma | Vidu Q3 | Pika 2.2 | Seedance 2.0 | NewAPI Family B | aimlapi Family C | Kie.ai Family D | PiAPI Family D |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **prompt** | `prompt` | `prompt` | `prompt` | `prompt` | `prompt` | `prompt` | `promptText` | `prompt` | `prompt` | `prompt` | `content[].text` | `prompt` | `prompt` | `input.prompt` | `input.prompt` |
| **negative_prompt** | — | — | `negative_prompt` | — | `negative_prompt` | `negative_prompt` ≤500 | — | — | — | `negative_prompt` | — | `metadata.negative_prompt` | `negative_prompt` | `input.negative_prompt` | `input.negative_prompt` |
| **duration** | `seconds: "4\|8\|12\|16\|20"` | `duration: 1-15` | `duration: "5"\|"10"` | `duration: 6\|10` | `seconds: "8"` | `duration: 2-15` | `duration: 5\|10` | `duration: "5s"\|"9s"` | `duration: 1-16` | `duration: 5\|10` | `duration: 4\|5\|6\|8\|10\|12\|15` | `duration: 5` int | `duration` string | `input.n_frames`/`input.duration` | `input.duration: 5` |
| **resolution** | `size: "1280x720"` | `resolution: "720p"` | `aspect_ratio` | `resolution: "1080P"` | `aspectRatio` | `resolution: "720P"` + `ratio` | `ratio: "1280:720"` | `resolution: "540p..4k"` | `resolution: "540p"\|"720p"\|"1080p"` | `aspect_ratio` + `resolution` | `ratio: "16:9"` + `resolution` | `size: "1920x1080"` | model decides | `input.size: "standard"\|"hd"` | `input.aspect_ratio` |
| **seed** | — | **None** | (legacy) | — | — | `seed` | `seed` | — | `seed: -1` random | `seed` | `seed` | `metadata.seed` | `seed` | — | `input.seed` |
| **First frame** | `input_reference` | `image:{url}` | `image_url` | `first_frame_image` | `image_url` | `first_frame_url` | `promptImage` | `keyframes.frame0` | `image` | `image_url` | `first_frame_url` | `image` | `image_url` | `input.image_urls[0]` | `input.image_url` |
| **Last frame** | ❌ | ❌ | `image_tail` | `last_frame_image` | ❌ | `last_frame_url` | ❌ | `keyframes.frame1` | `last_image` | `images[1]` (Pikaframes) | `last_frame_url` | `metadata.image_tail` | `last_image_url` | (some models) | `input.image_tail_url` |
| **Reference arrays** | `characters:[{id}]` | `reference_images:[{url}]` ≤7 | `elements:[]` ≤4 | `subject_reference` single | `image_urls:[]` | `Image1..5` placeholders | `referenceImages` ≤3 | — | `reference_image_urls:[]` ≤7 | `images:[]` ≤5 | `reference_images:[]` ≤9 | `metadata.image_urls:[]` | `image_urls`/`reference_images` | `input.character_id_list` | `input.elements:[]` |
| **Audio toggle** | (automatic) | — | `enable_audio` (2.6 pro) | (2.3 automatic) | (automatic) | (automatic) | — | — | `generate_audio` + `bgm` | — | `generate_audio` | (passthrough) | (model-dependent) | `input.sound` (2.6) | `input.enable_audio` |
| **Callback** | — polling | — polling | polling | polling | LRO | DashScope async | polling | polling | polling | polling | `callback_url` | `webhook_url`（partial） | polling | `callBackUrl` | `config.webhook_config` |
| **Mode/quality** | model-dependent | — | `mode: "std"\|"pro"` | `prompt_optimizer` | model variant | — | model variant | model variant | `movement_amplitude` | `ingredients_mode` | model variant `-fast` | — | model ID includes mode | `input.size: "standard"\|"hd"` | `input.mode: "std"\|"pro"` |
| **CFG** | — | — | `cfg_scale: 0-1` (v1) | — | — | — | — | — | — | `cfg_scale` (legacy) | — | (passthrough) | `cfg_scale` | — | `input.cfg_scale` |
| **Special header** | — | — | JWT 30min | — | OAuth2 | `X-DashScope-Async` | `X-Runway-Version` | — | — | — | — | — | — | — | — |
| **task_id path** | `$.id` | `$.request_id` | `$.data.task_id` | `$.task_id` | LRO operation | `$.output.task_id` | `$.id` | `$.id` | `$.task_id` | `$.request_id` | `$.id` | `$.task_id` | `$.id` | `$.data.taskId` | `$.task_id` |
| **Video URL path** | Second GET /content | `$.video.url` | `$.data.task_result.videos[0].url` | Second call using file_id | `$.video.uri` | `$.output.video_url` | `$.output[0]` | `$.assets.video` | `$.creations[0].url` | `$.video.url` | `$.content.video_url` | `$.url` | `$.video.url` / `$.assets.video` | `$.data.response.resultUrls[0]` | `$.output.works[0].video.url` |

### 5.1 State-machine mapping differences

Terminal-state strings differ completely and require per-platform mapping:

| Platform | queued | running | succeeded | failed |
|---|---|---|---|---|
| OpenAI Sora | `queued` | `in_progress` | `completed` | `failed` |
| NewAPI Family B | `queued` | `in_progress` | `completed` | `failed` |
| Alibaba DashScope | `PENDING`（**uppercase**） | `RUNNING` | `SUCCEEDED` | `FAILED` |
| Volcengine Ark Seedance | `queued` | `running` | `succeeded` | `failed` |
| Kling | `submitted` | `processing` | **`succeed`（not succeeded）** | `failed` |
| MiniMax Hailuo | `Queueing` / `Preparing` | `Processing` | **`Success`（initial uppercase）** | `Fail` |
| PiAPI inner | — | — | `output.status: 99`（**integer**） | `output.status: <99` |
| Runway | `PENDING` | `RUNNING` | `SUCCEEDED` | `FAILED` |

### 5.2 Video URL expiration (temporary OSS links)

**All official platforms use temporary URLs; copy them to persistent storage immediately**:

| Platform | Expiration | Risk |
|---|---|---|
| **MiniMax Hailuo** | **9 hours** (32,400 seconds) | 🔴 Shortest across platforms |
| OpenAI Sora 2 | Documentation and observations differ (docs: 24h; some user reports: 1h) | 🟠 |
| Vertex AI Veo | Shortly after LRO operation completion | 🟠 |
| Volcengine Ark Seedance | 24 hours (temporary TOS URL) | 🟡 |
| Alibaba DashScope Wan | 24 hours | 🟡 |
| Runway | 24-48 hours | 🟡 |
| Luma Ray-2 | Longer | 🟢 |
| Kling | 30 days (inferred from lip-sync endpoint constraints) | 🟢 |
| Qiniu sora compatible | 7 days | 🟢 |

---

## 6. Protocol priority recommendations

> Scoring dimensions (1-5 each): relay-ecosystem coverage / model-capability coverage / documentation completeness / implementation complexity (simpler scores higher) / long-term stability / ArcReel web-fiction demand

### 6.1 Implemented protocols (retain)

| Protocol | ArcReel endpoint | Status | Notes |
|---|---|---|---|
| OpenAI Sora compatible | `openai-video` | ✅ Implemented | Note 2026-09-24 deprecation |
| NewAPI’s own | `newapi-video` | ✅ Implemented | Metadata passthrough needs an expanded vendor mapping table |
| Full Veo 3.1 family | gemini-* provider | ✅ Implemented | — |
| Seedance 1.5/2.0 | ark provider | ✅ Implemented | 2.0 multimodal references + Extend not enabled |
| Grok Imagine | grok provider | ✅ Implemented | — |
| Full Vidu Q3 family | vidu provider | ✅ Implemented | reference-to-video mode shipped |

### 6.2 P0: recommended for v1.0

| Protocol | Why P0 | Complexity |
|---|---|---|
| **Family C `/v2/video/generations`** | **Widely adopted by relay services** (AIMLAPI / official xAI / getimg.ai / APIMart / EvoLink / Qiniu veo / CometAPI, etc.); second major de facto standard alongside Family B | Medium (discriminated union by model; model-specific schema branches required) |
| **Official Kling protocol** | Preferred model for domestic novel-to-video users; self-hosted studios need stable direct connections; unusual JWT authentication | Medium-high (JWT HS256 + 30min token cache, multiple endpoints by intent) |
| **Alibaba DashScope (Wan)** | **Unique value** for web fiction: wan2.7 long Chinese prompts + native `shot_type: multi` multi-shot storytelling; one protocol covers wan2.7/2.6/2.1-kf2v/2.2-s2v | Medium (X-DashScope-Async + nested input/parameters + multi-model routing) |
| **Official MiniMax Hailuo** | Strong physical behavior and leading price/performance; two-step File API URL retrieval differs from other platforms; 9h URLs require the most urgent persistence | Medium-high (two-step File API + webhook challenge) |

### 6.3 P1: add in v1.x

| Protocol | Reason |
|---|---|
| **Family D Kie.ai** `/api/v1/jobs/createTask` | Representative overseas verb-based style, covering some niche relay services |
| **Official Runway Gen-3/4** | Advertising/e-commerce demand; `X-Runway-Version` header; no webhook, polling required |
| **Seedance 2.0 multimodal-reference extension** | Models registered but reference_images / video_extend not enabled; improves the existing ark provider capability bitmap |

### 6.4 P2: wait for user feedback

| Protocol | Reason |
|---|---|
| Official PixVerse | No native NewAPI channel; direct official connection required; custom `API-KEY` header |
| fal.ai queue API | Only Pika 2.2 integration route; value of a unified queue protocol |
| Luma Dream Machine | Unique keyframes union, but low demand for Chinese-language workflows |

### 6.5 P3: do not pursue proactively

| Protocol | Reason |
|---|---|
| Direct ByteDance Jimeng | Extremely complex Volcengine SigV4 signing; relay recommended (widely proxied by Families B/C) |
| Separate PiAPI adaptation | High overlap with Kie.ai; coverage through P1 is sufficient |
| Replicate predictions | Little overlap with ArcReel’s user base |
| Together AI | Same as above |

---

## 7. Custom-provider integration approach (conclusion)

> **Revision note (2026-08)**: this chapter’s approach has been superseded by `docs/adr/0067`: custom invocation endpoints use declarative JSON definitions, a unified form for “JSON in/out + submit/poll” protocols. This covers long-tail aggregators and incorporates HTTP-style built-in backends that can be expressed in full as shipped declarative definitions (first batch: newapi / v2_video_generations / minimax). Inexpressible protocols, including signing-based authentication and SDK integrations, remain Python built-in endpoints. The conclusions in 7.1–7.2, measured against “expressing every protocol”, are no longer decision criteria; the runtime-plugin direction in 7.5 remains unaffected (tracked by #872). The original chapter body is retained.

### 7.1 Approach comparison

Detailed evaluation finds **plugins (Python code extensions) preferable to declarative YAML**:

| Dimension | Python plugin | Declarative YAML | Winner |
|---|---|---|---|
| Protocol coverage | 100% (fallback) | 50-60% (cannot express JWT/SigV4/multipart/two-step flows) | plugin |
| Integration difficulty | Write backend class + tests | Write YAML + debug templates | Apparently tied; plugins are easier to debug in practice |
| ArcReel maintenance cost | Independent backends, existing system | New Jinja2 + JSONPath + state mapping + probes + documentation | plugin (major advantage) |
| Debugging | Standard Python traceback | YAML schema errors / template-rendering errors / field-path errors | plugin by a wide margin |
| AI-assisted code generation | Cursor/Claude Code generates a complete backend in 30 seconds | Must first understand a custom YAML schema | plugin (key reversal since 2024+) |
| Fit with ArcReel users | All developers, know Python | Suits nonprogrammers | plugin |

### 7.2 Key decision grounds

**10 video-API capability categories require Python** (not expressible declaratively):

1. Official Kling JWT signing (HS256, 30-minute expiration, token cache required)
2. Volcengine Ark SigV4-like signing (AKSK + canonical request + HMAC)
3. DashScope two-step flow + regional base URL switching (Beijing/Singapore/US West credentials not interchangeable)
4. OpenAI Sora multipart upload (local-file base64 case)
5. MiniMax two-step download (`file_id → /files/{file_id}` to obtain download_url)
6. Luma keyframes union (`type: image|generation`)
7. Vertex AI Veo (service-account JSON + OAuth2 token refresh + dual LRO polling)
8. Runway header-version validation
9. OpenAI Sora content-download variants (`?variant=video|thumbnail|spritesheet`)
10. PiAPI nonstandard status codes (outer string + inner `output.status: 99` integer)

**Declarative YAML covers exactly the scenarios** already provided by ArcReel V1 built-in backends (pure JSON in/out families such as NewAPI, OpenAI-compatible, and Kie.ai), removing users’ motivation for declarative integration.

### 7.3 Fit with ArcReel’s current implementation

ArcReel’s **custom-provider integration flow already exists and needs no redesign**:

1. User → Settings → Add custom provider
2. Enter `display_name` + `discovery_format` + `base_url` + `api_key`
3. Click [Fetch model list] → call `/api/v1/custom-providers/discover`
4. UI automatically infers endpoint
5. User enables models / manually adjusts endpoints / enters pricing
6. Save to `custom_provider` + `custom_provider_model` tables

**Engineering work for a new protocol (e.g. official Kling, DashScope)**:
1. Add a backend class in `lib/backends/video_backends/` (implementing `VideoBackend` Protocol)
2. Register a new endpoint key and build_backend closure in `ENDPOINT_REGISTRY`
3. Add heuristic rules to `infer_endpoint()`
4. Add `endpoint_xxx_display` in three languages to i18n files
5. Add mocked httpx unit tests

**Users need no code** or configuration-file changes; they only select the corresponding endpoint when adding a custom provider in the frontend UI.

### 7.4 Distinguishing two “new backend” scenarios (prerequisite)

Two user scenarios must be distinguished; their integration paths differ completely:

| Scenario | Description | Current support |
|---|---|---|
| **Scenario A: supported protocol, new relay service** | User finds a new NewAPI / Family C aggregator / OpenAI-compatible service | ✅ UI only, no code (see 7.3) |
| **Scenario B: unsupported protocol, new adaptation needed** | User wants official Kling (JWT) / DashScope (special header) / a brand-new private relay protocol | ⚠️ Currently requires source changes in `lib/backends/video_backends/` + `ENDPOINT_REGISTRY` |

**Scenario A represents most user demand** and already works smoothly. **Scenario B currently has a gap**: connecting a new protocol requires source changes, rebuilding, and a PR or local fork. Runtime plugins aim to remove this gap and support community protocol sharing.

---

## 7.5 Runtime-plugin research (supporting planned functionality)

> This section researches feasibility and design options for ArcReel’s planned runtime-plugin functionality. Goal: load third-party video-protocol backends without modifying ArcReel source or rebuilding images, supporting community sharing and lowering the barrier to custom protocols. It **does not make PRD decisions**; it only collects feasible paths, industry practice, fit with current architecture, and unresolved questions.

### 7.5.1 Current ArcReel registration mechanism (starting point for runtime plugins)

Facts confirmed by reviewing ArcReel source, on which runtime-plugin design must be based:

1. **Backend registration uses a process-local static dictionary**. Each of the three media backend families (`video_backends` / `image_backends` / `text_backends`) has a `registry.py` with the same pattern:

   ```python
   _BACKEND_FACTORIES: dict[str, Callable[..., VideoBackend]] = {}


   def register_backend(name: str, factory: Callable[..., VideoBackend]) -> None:
       _BACKEND_FACTORIES[name] = factory


   def create_backend(name: str, **kwargs) -> VideoBackend:
       if name not in _BACKEND_FACTORIES:
           raise ValueError(f"Unknown video backend: {name}")
       return _BACKEND_FACTORIES[name](**kwargs)
   ```

2. **Registration occurs at module import**. On load, `lib/backends/video_backends/__init__.py` explicitly calls `register_backend(PROVIDER_GROK, GrokVideoBackend)` and similar registrations for all built-in backends. `register_backend()` is already public API; **calling it again at runtime is entirely valid**, making it a natural plugin entry point.

3. **Protocol assignment lives in `ENDPOINT_REGISTRY` (static dictionary)**. `CustomProviderModel.endpoint` accepts only keys registered in `ENDPOINT_REGISTRY`. This is the largest current runtime-extension obstacle: **even after registering a backend at runtime, users cannot register a new endpoint key for models to use**.

4. **Backend constructor arguments are injected from DB configuration**. Factories such as `create_text_backend_for_task()` read `api_key` and other DB configuration through `ConfigResolver.provider_config()` and pass it explicitly to constructors, without environment-variable fallbacks (`2026-05-12-agent-sandbox-design` removed all env fallbacks). Runtime-plugin backends must likewise accept explicit configuration injection and must not read environment variables.

**Conclusion**: ArcReel’s `register_backend()` + duck-typed Protocol design is **naturally suited to runtime extension**. The central change is turning `ENDPOINT_REGISTRY` from a static dictionary into two layers, “built-in + dynamically injected plugins”, and adding discovery, loading, security, and lifecycle management.

### 7.5.2 Industry runtime-plugin mechanisms

| Approach | Discovery | Registration | ArcReel fit | Limitations |
|---|---|---|---|---|
| **Python entry_points** (PEP 621) | `importlib.metadata.entry_points(group=...)` | Automatically visible after `pip install arcreel-plugin-xxx` | Standard, no third-party dependencies, fits community pip-package sharing | Requires wheel packaging, not bare local files; Docker users must rebuild images or mount packages |
| **Directory scanning + importlib** | Scan a directory for `*_backend.py`; load dynamically through `importlib.util.spec_from_file_location` | Effective when files arrive, with automatic `__subclasses__()` capture | Self-hosted users load through mounted volumes without rebuilding; friendly to local development | Must handle duplicate loading, naming collisions, and error isolation |
| **pluggy** (pytest plugin system) | Hook specifications + setuptools entry_points | Hook multicast (1:N) | Mature and stable | ArcReel uses 1:1 routing (one model → one backend), so multicast is unnecessary; pluggy lacks lifecycle management (token caches/connection pools), making it overdesigned |
| **LiteLLM `custom_provider_map`** | Declare `{provider, custom_handler}` in configuration | Module path + instance variable | Direct comparison for user-registered custom providers | LiteLLM video endpoints currently do not support CustomLLM (even embeddings are still at issue stage); its video path cannot be reused directly |

**Industry consensus**: entry_points (published plugins) + directory scanning (local development/self-hosting) is a mainstream discovery combination in modern Python applications. ArcReel’s Docker deployment makes **directory scanning + mounted volumes** friendlier to self-hosted users; entry_points better suits mature plugins published to PyPI for one-step community installation.

**LiteLLM evolution (direct reference value)**: LiteLLM initially supported only manual runtime assignment to `litellm.custom_provider_map = [{"provider": ..., "custom_handler": ...}]`. Issue #7733 requested automatic third-party registration via entry_points (then possible only through a `.pth` hack). PR #15881 subsequently implemented CustomLLM subclass declarations in `pyproject.toml`’s `[project.entry-points.litellm]`, automatically discovered and registered by `importlib.metadata`. This “manual registration → entry_points discovery” progression closely matches ArcReel’s current manual `register_backend()` calls and offers a direct blueprint for staged implementation.

**Known LiteLLM defect to avoid**: issue #23352 reports that when a plugin’s model name (after prefix removal) collides with a known model of a built-in provider, requests are **silently routed to the built-in provider instead of the plugin handler**, without errors. For ArcReel, plugin-endpoint dispatch precedence and namespace isolation must be explicit in the design; **explicitly registered plugins should take precedence over heuristic inference** to avoid silent routing hijacks caused by naming collisions.

### 7.5.3 Runtime-plugin design questions (PRD decisions pending)

Questions derived from the current architecture that runtime plugins must answer, as PRD input:

**A. Discovery and loading**
- Plugin sources: PyPI packages (entry_points) / local directories (mounted volumes) / both?
- Loading time: once at process startup, or hot loading (adding plugins while running without restarting)?
- Loading-failure isolation: one plugin exception must not bring down the entire backend registry. Wrap loading in try-except and log degraded operation (following the current `register_backend` philosophy that missing keys do not prevent startup).

**B. Extensible ENDPOINT_REGISTRY (central change)**
- May plugins register new endpoint keys? If so, `ENDPOINT_REGISTRY` must change from a module-level static dictionary to support runtime injection.
- Namespace isolation for new keys: prefix plugin endpoints (e.g. `plugin:kling-official`) to avoid collisions with built-in keys?
- How should `infer_endpoint()` heuristics accommodate plugins: may plugins declare rules (model_id pattern → their endpoint)?

**C. Plugin contract (backend interface constraints)**
- Plugin backends must implement `VideoBackend` Protocol (`name` / `model` / `capabilities` / `generate()`); the existing Protocol is ready.
- Must plugins declare metadata: supported models, capability bitmap, required credential fields (`required_keys` / `secret_keys`, see `ProviderMeta`), and display names (i18n)?
- Configuration injection: how do plugin backends receive api_key / base_url entered in the UI? Reuse existing `CustomProvider` credential storage + `mask_secret()` masking.

**D. Security (central risk of executing third-party code at runtime)**
- Plugins are arbitrary Python code running inside the ArcReel process, with full filesystem/network access.
- Is sandboxing needed? ArcReel already has an agent sandbox (bubblewrap/seatbelt, see `2026-05-12-agent-sandbox-design`), but it isolates Agent SDK Bash subprocesses; **backend plugins run in the main process**, so that sandbox cannot be reused directly.
- **In-process Python sandboxing is infeasible (explicit research conclusion)**: RestrictedPython officially states it "is not a sandbox system or a secured environment". Industry consensus holds that CPython in-process sandboxing is almost impossible to secure because of Python’s dynamic features (`__import__` abuse, introspection escapes, deserialization attacks); the pysandbox author has declared the approach unworkable. Real isolation requires process/container boundaries (seccomp, namespaces, micro-VMs such as Firecracker, WASM compilation), conflicting with calling a plugin as a backend in the main process. **ArcReel plugin security should therefore rely on source trust, not code-level sandboxing.**
- Mitigations (source trust): source review (trust only signed PyPI packages / allowlisted GitHub repositories), human confirmation at installation, community ratings, an officially maintained reviewed-plugin list, and preinstallation static analysis (dependencies/dangerous calls). Specific tradeoffs await PRD decisions.
- Credential leakage: malicious plugins can read other providers’ api_key values. Assess restricting each plugin to credentials assigned to it.

**E. Lifecycle management**
- Connection pools / token caches: for stateful resources such as Kling JWTs expiring after 30 minutes and reused connection pools, what is the instance lifecycle (singleton vs. per-request construction)? Existing backends reuse instances through `(provider_name, model)` caching; plugins should follow it.
- Uninstallation / updates: when users disable or update a plugin, how are registered backends and endpoints cleaned up?

**F. Distribution and community**
- Community-sharing format: GitHub repositories / PyPI packages / official ArcReel plugin marketplace?
- Version compatibility: plugins declare compatible ArcReel version ranges (compatibility policy when Protocol interfaces change).
- Documentation and scaffolding: provide a tool such as `arcreel backend scaffold` + AI prompt templates so users can quickly generate Protocol-compliant plugin skeletons with Cursor/Claude Code (7.1 argues AI-assisted generation is a key plugin advantage over declarative approaches).

### 7.5.4 Fit with current architecture

| Change | Existing foundation | Effort |
|---|---|---|
| Runtime backend registration | `register_backend()` is public API; duck-typed Protocol ready | Small (add plugin-loader calls) |
| Extensible ENDPOINT_REGISTRY | Currently a static dictionary | **Medium** (two layers + namespace isolation) |
| Plugin-aware `infer_endpoint()` | Hardcoded heuristics | Medium (expose an interface for plugin-declared heuristics) |
| Credential storage/masking | `CustomProvider` + `mask_secret()` ready | Small (plugins reuse them) |
| i18n endpoint display names | Three-language `endpoint_xxx_display` mechanism ready | Small (plugins supply names, possibly with English fallback) |
| Security model | Agent sandbox excludes main-process backends; in-process Python sandboxing proven infeasible | **Large/unknown** (source trust + review, not code sandboxing; product/security decision) |
| Lifecycle/caching | `(provider, model)` instance cache ready | Small (plugins reuse it) |

**Overall**: `register_backend()` + Protocol makes runtime-plugin **functionality** a strong architectural fit with manageable effort. The real difficulties are **dynamic ENDPOINT_REGISTRY** (medium engineering work) and **security when executing third-party code in the main process** (product + security decisions requiring substantial PRD analysis). Since the research establishes code-level sandboxing as infeasible, security should focus on “source trust + review + community governance” rather than “technical isolation”.

### 7.5.5 Possible incremental rollout (PRD options, not settled conclusions)

Research identified these incremental paths, ordered by increasing effort:

1. **Minimum viable (self-hosted only)**: directory scanning + mounted volumes, letting self-hosted users load their own plugins in trusted environments. This does not solve security (users trust themselves). Least effort; validates the mechanism quickly.
2. **Community sharing (PyPI + review)**: entry_points + PyPI publication with an officially maintained reviewed-plugin list. Contributions undergo review before recommendation. Balances openness and security.
3. **Official plugin marketplace**: ArcReel maintains a registry + version-compatibility declarations + community ratings, with one-click UI installation. Best experience, but requires marketplace infrastructure and ongoing operations.

These paths are compatible and may form three evolutionary stages.

---

## 8. Key risks and pitfalls

### 8.1 OpenAI Sora 2 Deprecation

- Official announcement issued 2026-03-24: **Sora 2 / Sora 2 Pro and the Videos API shut down on 2026-09-24**
- Affected model IDs: `sora-2`, `sora-2-pro`, `sora-2-2025-10-06`, `sora-2-2025-12-08`, `sora-2-pro-2025-10-06`
- ArcReel must assess migration to sora-3 or alternatives before 2026-Q3
- The `/v1/videos` de facto path standard remains; relay services will keep using it

### 8.2 Uncontrolled NewAPI metadata passthrough completeness

- The same Kling `camera_control` works on DMXAPI but may be lost on some self-hosted NewAPI deployments
- ArcReel’s `newapi-video` backend must identify tested passthrough fields in channel configuration or documentation
- Mark some advanced capabilities (motion brush / camera control) in the UI as “may be unavailable on some relay services”

### 8.3 Shared temporary-video-URL pitfall

- All platforms use temporary URLs; MiniMax is most urgent at only **9 hours**
- ArcReel must **start copying to local or object storage within 10 seconds** after SUCCEEDED
- Do not rely on vendor URLs for frontend display

### 8.4 Status-string differences

- Kling `succeed` (not `succeeded`)
- MiniMax `Success` (initial uppercase)
- DashScope uppercase `SUCCEEDED`
- PiAPI inner `output.status: 99` (integer)
- Each backend must maintain its own state mapping; mappings cannot be reused

### 8.5 Fragmented model IDs

Family C `/v2/video/generations` names the same model completely differently across relay services:
- AIMLAPI：`kling-video/v1/standard/text-to-video`
- APIMart：`sora-2-vip`
- getimg.ai: `happyhorse-1` (own brand name)
- Official xAI: `grok-imagine-video`

ArcReel needs model-name alias mappings in channel configuration, or must allow manual user corrections to `infer_endpoint()` heuristics.

### 8.6 Family C v2 discovery limitations

The `/v1/models` list cannot reliably distinguish target endpoints `/v1/video/generations` vs. `/v2/video/generations`, because their model IDs are identical.

**Automatic heuristics can provide only a default** (`newapi-video` recommended as more common); users switch manually in the UI based on their relay service’s documentation. This is inherent in the NewAPI / OneAPI-derived ecosystem, not an ArcReel design defect.

### 8.7 Seedance 2.0 domestic/overseas model IDs are not interchangeable

- Domestic: `doubao-seedance-2-0-260128`
- Overseas: `dreamina-seedance-2-0-260128`
- Cross-region calls inevitably return 404
- ArcReel’s existing ark / ark-agent-plan dual-provider design is a reference

### 8.8 Moderation error codes

Moderation-rejection codes differ completely across platforms:
- OpenAI Sora：`error.code: "moderation_blocked"`
- Alibaba DashScope: `IPInfringementSuspect` / `DataInspectionFailed`
- Kling: HTTP 500 + `error: "...violate the community guidelines (CM_EXT.POther)"` (**use error, not message**)

Do not treat these as retryable errors; backends must recognize them specially.

---

## 9. Questions for subsequent PRDs / design documents

This report provides input; subsequent PRDs must settle the following questions, grouped as “architecture → protocol implementation → runtime plugins”.

### 9.1 Architecture alignment (before individual protocol decisions)

> These are **higher-level decisions than individual protocol integrations**: before adding many protocols, assess whether the current `lib/backends/video_backends/` + `lib/custom_provider/` architecture needs changes to align with mature industry implementations. **Important premise: do not let naming mislead the assessment.**

**Naming clarification (to prevent confusion)**:
- ArcReel’s `VideoBackend` Protocol + `XxxVideoBackend` implementations **already embody the industry-favored Ports & Adapters (hexagonal architecture) pattern**: the Protocol is a Port and each backend an Adapter. The architectural skeleton already follows good practice.
- The glossary (GLOSSARY.md) chooses `backend` over `adapter` because it matches provider derivation (one provider yields multiple backends), complements frontend, and keeps video/image/text naming consistent. **Mature projects including SQLAlchemy / Django likewise call similar roles backends rather than adapters**. Naming alone does not mean architectural misalignment.
- **Likely conclusion**: do not refactor just to adopt adapter naming. Naming is superficial; assess the capability gaps below.

**Actual architecture gaps for PRD assessment (independent of naming)**:

1. **Should a unified asynchronous-task abstraction become a first-class concept?** Research confirms that 9 official platforms + 4 relay families **all use asynchronous tasks** (submit → poll → state mapping → URL persistence), with differing state strings, URL expiration windows, two-step URL retrieval, etc.
   - **Real signal**: do backends duplicate polling loops / state mapping / URL persistence? If duplication is high, extract an `AsyncVideoTask`-like abstraction above `VideoBackend` (see fal.ai’s queue model). If shared polling infrastructure already exists, no change is needed.
   - This is substantive industry alignment, unrelated to naming.

2. **Review coupling when adding protocols**. Ideally, a new protocol (e.g. Kling) needs only a new backend class + one `ENDPOINT_REGISTRY` registration.
   - **Real signal**: if a new protocol also requires changes to `resolver` / `cost_calculator` / `media_generator` / multiple enums, coupling is excessive and needs reduction. The decoupling approach remains independent of naming.

3. **Make the static ENDPOINT_REGISTRY extensible**. This is a hard runtime-plugin blocker (7.5.1) and the main gap relative to extensible industry registration mechanisms (entry_points / pluggy). It clearly warrants alignment (details in 9.3).

**Driving principle**: real pain points (many files changed per protocol, duplicated async polling, plugins blocked by a static dictionary) should drive refactoring, rather than the observation that others say adapter. Without those substantive problems, retain the current architecture and extend it incrementally.

### 9.2 Protocol implementation (specific P0 questions)

1. **P0 integration order** (suggested: Family C v2-video-generations → official Kling → DashScope → MiniMax)
2. **Each new endpoint’s `ENDPOINT_REGISTRY` key** (e.g. `v2-video-generations` / `kling-official` / `dashscope-async` / `minimax-video`)
3. **`infer_endpoint()` heuristic extensions** (distinguishing new and existing endpoints)
4. **Video-URL persistence middleware** (short-lived URLs such as MiniMax 9h and Sora 1h)
5. **Encapsulation of MiniMax’s two-step File API inside the backend** (expose a unified video_url)
6. **Kling JWT cache policy** (asyncio.Lock + dict TTL)
7. **DashScope regional configuration** (base_url and api_key binding)
8. **NewAPI backend metadata-profile design** (per-vendor field mappings)
9. **Unified asynchronous-task state machine** (introduce at VideoBackend level or implement separately; coordinate with 9.1 item 1)
10. **Sora 2 deprecation response** (migration assessment window and fallback policy)

### 9.3 Runtime-plugin layer

1. **Dynamic runtime-plugin ENDPOINT_REGISTRY** (static dictionary → built-in + plugin layers; same concern as 9.1 item 3)
2. **Runtime-plugin security model** (code-level sandboxing proven infeasible; focus on source trust + review)
3. **Plugin discovery/loading selection** (entry_points / directory scanning / both)
4. **Plugin contract** (metadata, credential injection, i18n, heuristics)
5. **Incremental rollout stages** (minimum viable → community sharing → official marketplace)

---

## 10. References

### First-party API documentation

- OpenAI Sora 2: https://platform.openai.com/docs/guides/video-generation
- OpenAI Deprecations: https://developers.openai.com/api/docs/deprecations
- NewAPI documentation: https://doc.newapi.pro/api/generate-video/ and https://doc.newapi.pro/api/kling-jimeng/
- Kling: https://app.klingai.com/cn/dev/document-api
- Volcengine Ark Seedance: https://www.volcengine.com/docs/82379
- Alibaba DashScope: https://help.aliyun.com/zh/model-studio/text-to-video-api-reference
- MiniMax Hailuo: https://platform.minimax.io/docs/api-reference/video-generation-t2v
- AI/ML API: https://docs.aimlapi.com/api-references/video-models
- xAI Grok: https://docs.x.ai/developers/model-capabilities/video/generation
- Kie.ai: https://docs.kie.ai/market/
- PiAPI: https://piapi.ai/docs/
- Runway: https://docs.dev.runwayml.com/
- Luma: https://docs.lumalabs.ai/docs/video-generation
- PixVerse: https://docs.platform.pixverse.ai/

### Existing ArcReel design documents (background alignment)

- `docs/adr/`: architecture decisions on VideoBackend layering, custom providers and endpoints, duration’s source of truth, etc.
- `GLOSSARY.md`: glossary (backend rather than adapter)

### Relay-service documentation (de facto standard sources)

- AiHubMix: https://docs.aihubmix.com/en/api/Video-Gen
- APIMart: https://docs.apimart.ai/en/api-reference/videos/
- Qiniu Cloud AI inference: https://developer.qiniu.com/aitokenapi
- useapi.net Kling: https://useapi.net/docs/api-kling-v1/
- fal.ai: https://fal.ai/models/

---

**Report version**: v1 (final research version)
**Aligned architecture**: ArcReel `lib/backends/video_backends/` + `lib/custom_provider/` + `ENDPOINT_REGISTRY`
**Next step**: write PRDs and design documents for individual endpoint integrations based on this report
