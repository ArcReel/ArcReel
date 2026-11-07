# ArcReel integration research: Alibaba Bailian, Kling, MiniMax

**Research date**:2026-05-29
**Purpose**: inputs to later PRDs/designs, assessing preset text/image/video integrations
**Currency**: mainland-China CNY; international differences identified
**Prices**: promotional fluctuations; selection references distinguish official/third-party evidence. Recheck official consoles before launch

**Source/confidence statement**:
- Bailian model IDs individually checked against [Official model catalog](https://help.aliyun.com/zh/model-studio/models), updated2026-05-21; official IDs authoritative.
- MiniMax release timelines checked against [Official model releases](https://platform.minimaxi.com/docs/release-notes/models), supporting flagship classifications.
- Kling models/prices checked against official [Video models](https://klingai.com/document-api/apiReference/model/videoModels)/[Image models](https://klingai.com/document-api/apiReference/model/imageModels)/[Official prices](https://klingai.com/dev/pricing); IDs/capability matrices/credit prices authoritative: video1 credit=¥1, image 1=¥0.025.
- Exact per-image `qwen-image-2.0`/`wan2.7-image` and login-gated tiers still need consoles; console-check labels mean no primary confirmation.

---

## 0. Scope

**Research compilation** of current latest quality models, capabilities/APIs/official prices for Chinese web-fiction→scripts→character/scene designs→storyboards→videos→composition.

**Outside scope**, PRD/design decisions: integration order/phases, cost breakers/fallbacks, backend classes, database schemas.

Include suitable latest quality models; superseded versions only where necessary, without expanding them.

---

## 1. Models recommended for assessment

| Vendor | Modality | Model | ID | Official price | Protocol |
|---|---|---|---|---|---|
| Bailian | text | Balanced Qwen-Plus | `qwen-plus` | 0–128K:¥0.8 input/¥2 output per 1Mtokens | OpenAI ✅ |
| Bailian | text | Latest vision-language Qwen3.6-Plus | `qwen3.6-plus` | ¥2/¥12, cache¥0.2 | OpenAI ✅ |
| Bailian | text | Stable flagship Qwen3-Max | `qwen3-max` | 0–32K:¥2.5/¥10 | OpenAI ✅ |
| Bailian | text | Agent flagship Qwen3.7-Max | `qwen3.7-max` | ¥12/¥36, 256K–1M; 50%off¥6/¥18 through 2026-06-22 | OpenAI ✅ |
| Bailian | image | Recommended text/comics/storyboards Qwen-Image-2.0-Pro | `qwen-image-2.0-pro` / accelerated `qwen-image-2.0` | Per image, console check | DashScope sync |
| Bailian | image | Wan 2.7 realistic portraits/sets | `wan2.7-image` / `-image-pro` | Per image, console, failures uncharged | DashScope sync/async |
| Bailian | video | HappyHorse 1.0 first-frame | `happyhorse-1.0-i2v` | 720P ¥0.9/s, 1080P ¥1.6/s | DashScope async |
| Bailian | video | HappyHorse 1.0 R2V | `happyhorse-1.0-r2v` | 720P ¥0.9/s, 1080P ¥1.6/s | DashScope async |
| Bailian | video | HappyHorse 1.0 T2V | `happyhorse-1.0-t2v` | 720P ¥0.9/s, 1080P ¥1.6/s | DashScope async |
| Bailian | video | Wan 2.7 first/last/continuation/audio | `wan2.7-i2v-2026-04-25` | Per second/resolution, console | DashScope async |
| Kling | image | Omni O1 consistency/IP/comics/series | `kling-image-o1`, 1–10 refs / `kling-v3-omni`, 4K+sets | ¥0.2/1K–2K; v3-omni4K ¥0.4 | JWT async |
| Kling | video | v3/v3-omni multishot/4K/subjects | `kling-v3` / `kling-v3-omni` | Silent list std ¥0.6/s, pro¥0.8/s, 4K ¥3/s | JWT async |
| Kling | video | v2-6, only human-voice control | `kling-v2-6` | Pro audible ¥1/s, silent¥0.8/s | JWT async |
| Kling | video | Value2.5Turbo | `kling-v2-5-turbo` | Std silent¥0.6/s, pro audible ¥1/s | JWT async |
| MiniMax | text | Flagship M2.7 | `MiniMax-M2.7` | ¥2.1/¥8.4, cache¥0.42 | OpenAI/Anthropic ✅ |
| MiniMax | image | image-01 consistency | `image-01` | ¥0.025, success only | REST, single URL |
| MiniMax | video | Quality Hailuo 2.3 T2V/I2V | `MiniMax-Hailuo-2.3` | 768P 6s ¥2/10s ¥4; 1080P 6s ¥3.5 | REST, two-step file_id |
| MiniMax | video | I2V-only Hailuo 2.3-Fast, half price | `MiniMax-Hailuo-2.3-Fast` | 768P 6s ¥1.35/10s ¥2.25; 1080P 6s ¥2.31 | Same |
| MiniMax | video | Consistency S2V-01 | `S2V-01` | Pack1.5 credits/video, approximately¥3 | Same,`subject_reference` |

**Protocols**: Alibaba/MiniMax text reuse OpenAI, Kling no text. Alibaba images mainly synchronous (Qwen2.0 sync-only, Wan both), videos async. Kling JWT; MiniMax own REST, images direct URLs/video file_id two-step.

---

## 2. R2V/character consistency

> Reference images preserve **characters/subjects in new scenes**, central to protagonists across web-fiction shots. Fields/counts/model support differ significantly.

### 2.1 Comparison

| Vendor | R2V ID | References | Maximum | Consistency | Audio | Notes |
|---|---|---|---|---|---|---|
| HappyHorse | `happyhorse-1.0-r2v` | DashScope input images | Multiple characters | Character/subject | ✅ | 720P/1080P, 3–15s, native audiovisual |
| Wan 2.7 | `wan2.7-r2v` | 图N/视频N references | Multiple characters/images+videos | Character/subject | ✅ voice/video subjects | 720P/1080P, 2–10s, **only video-reference subjects** |
| Wan 2.6 | `wan2.6-r2v` / `wan2.6-r2v-flash` | Same | Multiple | Character | ✅ | Fast flash |
| Kling v3/v3-omni | `kling-v3` / `kling-v3-omni` | Subject controls | Video characters+multiple images | Character/subject | Official ❌human voices | Strongest official subjects; v3-omni video refs3–10s |
| Kling O1 | `kling-video-o1` | Image subjects/video references | Multiple-image subjects | Character/video-driven | ❌ | Video editing/reference3–10s |
| Legacy Kling v1-6 | `kling-v1-6` | `image_list[]` | Legacy multiple images | Multiple subjects | Model-dependent | References/multimodal edits |
| MiniMax | `S2V-01` | `subject_reference[]` | One face image | Single face | — | Pioneering single-image drive; minor environment distortion |

### 2.2 Boundaries

- **Kling upgraded to subject control**, official: `kling-v3`/`kling-v3-omni` video characters+multi-image subjects strongest; `kling-video-o1` image subjects+videos. Older `kling-v1-6` references superseded by v3/o1.
- **Hailuo 2.3/Fast lack R2V**:2.3 T2V/I2V, Fast I2V only; use separate **S2V-01**, one face.
- **Alibaba most complete**: HappyHorse-1.0-r2v/Wan 2.7-r2v multicharacter/native audio; Wan 2.7-r2v uniquely Alibaba video-subject references, also supported by Kling v3-omni/o1.

### 2.3 Web-fiction fit

- Recurring protagonist: `S2V-01`, stable one-face drive, or `happyhorse-1.0-r2v`
- Multicharacter scenes: `wan2.7-r2v`/`happyhorse-1.0-r2v`, audio, or `kling-v3-omni` video+image subjects
- Video-driven motion/subjects: `wan2.7-r2v`,`kling-v3-omni`/`kling-video-o1`

---

## 3. Alibaba Bailian: DashScope/Model Studio

> **Regions**: Beijing `dashscope.aliyuncs.com`, Singapore `dashscope-intl.aliyuncs.com`, Virginia `dashscope-us.aliyuncs.com`; independent keys cannot cross regions.

### 3.1 Qwen text, including Qwen3.7-Max

#### Mainland CNY/million tokens; whole-request input volume selects whole-bill tiers

| ID | Position | Input | Output | Context | Notes |
|---|---|---|---|---|---|
| `qwen3.7-max` | **Latest Agent flagship**, GPT-5.5/Claude Opus 4.7 peer | ¥12 | ¥36 | 256K–1M | 50%off¥6/¥18 to 2026-06-22; cache¥1.2; AA 56.6, global 5/China 1; text available |
| `qwen3-max` | Stable,`qwen3-max-2025-09-23`/`-2026-01-23` | ¥2.5, 0–32K | ¥10 | 256K | 32–128K¥4/¥16; 128–256K¥7/¥28; native search Agent; thinking multiplies output |
| `qwen3.6-plus` | Latest visual Plus, 2026-04-02 snapshot, official favorite | ¥2 | ¥12 | — | Cache¥0.2; code/OCR/multimodal beyond 3.5 |
| `qwen3.6-flash` | Latest high-frequency/value Flash | Console | Console | — | One of three official favorites; prompt rewrites/tag extraction |
| `qwen-plus` | Stable balanced alias, may target qwen3.5-plus | ¥0.8, 0–128K | ¥2 | 1M | 128–256K¥2/—; low-cost long-context leader |
| `qwen-long` | Long-document value | ¥0.5 | ¥2 | 10M | Cheapest long-chapter comprehension/summaries |

> Official2026-05-21 **top three**: `qwen3.7-max`/`qwen3.6-plus`/`qwen3.6-flash`; stable `qwen-plus`/`qwen-long` remain available, not promoted.

**Roles**: critical plots/reasoning `qwen3.7-max`/`qwen3-max`; frequent prompts/cards/dubbing extraction `qwen-plus`/`qwen3.6-plus`; whole long novels `qwen-long`. Flagships overkill for simple tasks.

**OpenAI API ✅**:  `base_url=https://dashscope.aliyuncs.com/compatible-mode/v1`,`/chat/completions`, Bearer, streams/`tool_calls`/`response_format={"type":"json_object"}`. `enable_thinking`/`thinking_budget` via `extra_body`; Chinese complex schemas more reliable with forced `tool_choice` than `response_format`.

**Tiers**: total input determines whole-order price, not marginal excess only.

> Superseded `qwen-turbo`/Qwen2.x omitted; stable old interfaces still usable.

#### Bailian third-party proxy models

Official catalogs expose third-party text through **identical OpenAI format**: one `dashscope`+OpenAI backend, change model:

- `deepseek-v4-pro`/`deepseek-v4-flash`, DeepSeekV4
- `kimi-k2.6`, Moonshot
- `glm-5.1`, Zhipu
- `MiniMax-M2.7`, proxy `MiniMax/MiniMax-M2.7`, 20%implicit-cache discount
- `mimo-v2.5-pro`, Xiaomi

One endpoint avoids separate official integrations; proxy prices may differ, check Bailian consoles.

### 3.2 Parallel image lines: Qwen-Image and Wan 2.7

Two **complementary** product lines, both relevant:

#### ① Qwen-Image: Qwen team, text rendering/comic-storyboard SOTA

- **IDs**:
  - `qwen-image-2.0-pro`/`qwen-image-2.0-pro-2026-03-03`, **officially recommended**, unified generation/editing, strongest text/texture/semantic following, sync-only
  - `qwen-image-2.0`/`qwen-image-2.0-2026-03-03`, accelerated quality/performance balance, sync-only
  - `qwen-image-edit`, editing/local changes, 1–3 images+instruction
  - Superseded `qwen-image-max`/`qwen-image-plus`, not expanded
- **Released**2026-02-10, same day Seedream 5.0; Qwen image2.0, 7B versus original20B MMDiT.
- **Capabilities**:
  - **Complex-text SOTA**:1000 token instructions, approximately 800–1000Chinese characters; complex typography/multiple scripts including regular/slender-gold/small-regular, and glass/clothing/magazines. Official Lanting Preface demo renders324 characters with illustrations
  - **Multicell comics with stable cross-cell characters**, direct storyboarding fit
  - **Native2K**, 2048×2048, skin/vegetation/architecture detail; over ten styles including realistic/ink/hand-drawn/anime/oil
  - **Unified generation/editing**:text/image edits, inscriptions/backgrounds/multi-image composition/cross-dimensional fusion; object edits preserve surroundings
  - Both AI Arena text/image-edit leaderboards first; DPG-Bench88.32 versus FLUX.1(12B)83.84
- **API**:DashScope, separate generation/edit endpoints; 2.0sync-only. Editing `messages` one user object, 1–3 images+text; recommended384–3072px, ≤10MB/image; temporary OSS URLs.
- **Pricing**:success-only, failures consume neither charges nor free quota; exact consoles, official expected approximatelyone-third Midjourney.
- **Languages**:Simplified Chinese/English officially.

#### ② Wan 2.7 images: Wan team, realistic portraits/sets

- **IDs**:`wan2.7-image`/larger, complex-scene-stabler `wan2.7-image-pro`, released 2026-04-01.
- **Capabilities**:
  - Diverse faces rather than homogeneous AI faces, realism
  - **Up to 12 images per set**, consistent character/style/color, storyboard references
  - Long text≤4000 characters/3Ktokens, 12 languages, print-quality Chinese typography
  - Precise hex colors, up to 9 references/group portraits preserving characters
  - Natural-language online image/story edits
- **API**:DashScope sync `POST /api/v1/services/aigc/multimodal-generation/generation`, supported sincewan2.6, or async `X-DashScope-Async: enable`.
- **Prices**:successful images only, console exacts.

**Selection**:Qwen-Image-2.0-Pro for comics/storyboards/text posters, cross-cell consistency/text/native 2K; wan2.7-image for realistic/diverse portraits/sets. Both per-image DashScope.

### 3.3 Video: preferred HappyHorse 1.0 and Wan 2.7

> HappyHorse(快乐小马), official April 2026 Alibaba family: **Artificial Analysis blind silent T2V/I2V first, audible second behind Seedance 2.0**.15B single-stream Transformer, native audio/video; single H100 generates 5s 1080P in 38s, 2–3× peers. **Fully open/commercial**, friendly to ArcReel.

#### HappyHorse 1.0, official 720P ¥0.9/s, 1080P ¥1.6/s

| ID | Type | Features | Output |
|---|---|---|---|
| `happyhorse-1.0-t2v` | Text | Audio, seven lip-sync languages, multishot | 720P/1080P, 3–15s, 24fpsMP4 |
| `happyhorse-1.0-i2v` | First frame | Audio, 1080P | 720P/1080P, 3–15s, 24fpsMP4 |
| `happyhorse-1.0-r2v` | References | Audio, multicharacter | 720P/1080P, 3–15s, 24fpsMP4 |
| `happyhorse-1.0-video-edit` | Editing | Audio, style transfer | 720P/1080P, 3–15s, 24fpsMP4 |

Lip-sync Chinese/English/Japanese/Korean/German/French/Cantonese. Up to 15s multishot narratives, cinematic lighting/cameras/character continuity.

#### Official HappyHorse/Wan roles

| Scenario | Recommendation | Alternative |
|---|---|---|
| Audible T2V | `happyhorse-1.0-t2v` | `wan2.7-t2v-2026-04-25`, custom audio |
| First frame | `happyhorse-1.0-i2v` | — |
| First/last, continuation, long chains | `wan2.7-i2v-2026-04-25` | — |
| Character R2V | `happyhorse-1.0-r2v` | `wan2.7-r2v`, video subjects/custom voices |
| Editing | `happyhorse-1.0-video-edit` | `wan2.7-videoedit`, effects/camera reproduction |

#### Wan 2.7, 720P/1080P, 2–15s, 30fps

| ID | Type | Features |
|---|---|---|
| `wan2.7-t2v`/`wan2.7-t2v-2026-04-25` | Text | Synchronized audio, multishot |
| `wan2.7-i2v`/`wan2.7-i2v-2026-04-25` | Image | First frame, first/last frames, continuation, audio drive |
| `wan2.7-r2v` | Video references | Multicharacter, 图N/视频N syntax, only video-reference subjects, 2–10s |
| `wan2.7-videoedit` | Editing | Instructions/transfer/effects/camera reproduction, ≤10s |

> Wan 2.5 `wan2.5-t2v-preview`/`wan2.5-i2v-preview` uniquely accepts custom `audio_url`, 480P/720P/1080P, fixed 5/10s. Earlier 2.1/2.2 superseded by 2.6/2.7, omitted.

**Unified asynchronous DashScope video**:
- Submit `POST /api/v1/services/aigc/video-generation/video-synthesis` + `X-DashScope-Async: enable`→`output.task_id`
- Poll `GET /api/v1/tasks/{task_id}`→`PENDING → RUNNING → SUCCEEDED/FAILED`
- Retrieve `output.video_url`, public OSS 24h; save immediately

---

## 4. Kling/Kuaishou

> **No text**. API `https://api.klingai.com/v1`, **JWT HS256**, AK issuer/SK signature, 30 min expiry/automatic refresh. Bailian proxies images/videos asynchronously, Beijing-only, avoiding JWT if preferred.
>
> Models checked against official `klingai.com/document-api/apiReference/model/{video,image}Models`. **Exact inspiration-point tiers still need app.klingai.com login**; capability matrices omit per-tier values.

### 4.1 Images: Kolors and new Omni

Official lineage:`kling-v1`/`kling-v1-5`/`kling-v2`/`kling-v2-new`/`kling-v2-1`/`kling-v3`/`kling-v3-omni`/`kling-image-o1`.

#### Two strongest web-fiction candidates

- **`kling-image-o1`, strongly recommended**:MVL multimodal-visual-language, **1–10 references**, cross-image characters; official IP/comic-series/brand positioning. **Strongest current Kling recurring-character/storyboard images**, beyond Kolors. Custom1K/2K/intelligent ratios, text/image/subject control, multi-image subjects; training through 2025-12.
- **`kling-v3-omni`, images**:custom1K/2K/**4K**/intelligent ratios, text/image/**sets**/multi-image subjects; image component of unified3.0, valuable4K/batches.

#### Legacy, as needed, superseded by o1/v3

- `kling-v1`:general text/image guidance, 1K, eight ratios
- `kling-v1-5`:image **character features/appearance**, 1K
- `kling-v2`/`kling-v2-1`:multireference/style repaint; v2-1 fullest general/features/appearance/multiple/style
- Chinese text traditional Kolors strength, ChatGLM3 encoder; new o1/v3-omni improve further

**API**:JWT async,`POST /v1/images/generations`→`GET /v1/images/generations/{task_id}`, image_url24h
**Parameters**:`model_name`,`prompt`,`negative_prompt`,`image`,`image_fidelity`,`human_fidelity`,`n`,`aspect_ratio`; image arrays for references, o1≤10

**Official images**,`klingai.com/dev/pricing`, 1 credit=¥0.025:

| Model | Capability | Specification | Per image |
|---|---|---|---|
| `kling-image-o1` | Text/image/edit | All ratios | ¥0.2, 8 credits |
| `kling-v3-omni` | Text/image/edit | 1K/2K | ¥0.2 |
| `kling-v3-omni` | Same | 4K | ¥0.4, 16 credits |
| `kling-v3` | Text/image | 1K/2K | ¥0.2 |
| `kling-v2-1` | Text | — | ¥0.1, 4 credits |
| `kling-v2-1` | Image | — | ¥0.2 |
| `kling-v2` | Text | — | ¥0.1 |
| `kling-v2` | Multireference image | — | ¥0.4, 16 credits |
| `kling-v1-5` | Character features/appearance | — | ¥0.2 |
| `kling-v1` | Text/image | — | ¥0.025, 1 credit, cheapest |
| Utility | Outpainting | — | ¥0.2 |
| Utility | Intelligent subject completion | — | ¥0.5, 20 credits |

Recommended `kling-image-o1`/`kling-v3-omni`¥0.2at1K–2K cost more than image-01 ¥0.025, but stronger multireference continuity.

### 4.2 Videos

Official lineage:`kling-v1`/`kling-v1-5`/`kling-v1-6`/`kling-v2-master`/`kling-v2-1`/`kling-v2-1-master`/`kling-v2-5-turbo`/`kling-v2-6`/`kling-v3`/`kling-v3-omni`/`kling-video-o1`.

**Official video prices**,`klingai.com/dev/pricing`, 1 credit=¥1list, dimension-combined CNY/second:

| Specification | Silent | Audible | Video references, silent |
|---|---|---|---|
| Std×1s | ¥0.6 | ¥0.8 | ¥0.9 |
| Pro×1s | ¥0.8 | ¥1.0 | ¥1.2 |
| 4K×1s | ¥3.0 | ¥3.0 | — |

Mode×duration×video-reference×audio:pro audible 5s=¥1×5=¥5, std silent 5s=¥3. Credit packs:trial¥0.7/credit, first-purchase30%off; standard¥1; bulk¥0.9.

Community assessments place motion physics/camera stability/complex instructions globally top-tier. Artificial Analysis audibleT2V2026-05-28:Kling 3.0 Omni 1080p Pro Elo 1099, fourth behind Dreamina Seedance 2.0/HappyHorse-1.0/Veo3.1.

> Version-independent official platform features:photo avatars, script/audio lip sync, video sound effects for Kling or uploaded clips.

#### ① kling-v3 / kling-v3-omni:flagships, multishot/4K/subjects

- **Official**:std/pro/**4K**, 3–15s; T2V single/**multishot**; I2V first-frame single/multishot/**first+last continuous**/**video-character+multi-image subjects**. v3-omni adds video references3–10s, not4K; v3 action controls std/pro, not4K.
- **Correction**:official v3/v3-omni **human-voice control ❌**, contradicting third-party native-audiovisual claims. Follow official unsupported status; platform sound-effects generation can supplement. Reconfirm official docs before integration.
- **Subjects matter for fiction**:video-character+multi-image controls surpass legacy v1-6 references.

#### ② kling-v2-6:only explicit human-voice support

- **Official**:std/pro, 5/10s+other durations; T2V/I2V, std silent/pro audible; pro silent-only first/last; **human voices pro ✅**; action controls at other durations.
- Only official matrix version explicitly **voice-control ✅**, pro; evaluate first for spoken video.

#### ③ kling-v2-5-turbo:value production

- **Official**:std/pro, 5/10s, T2V/I2V all, first/last pro; 1080p 24fps pro.
- **Pricing**:std silent 5s ¥3, pro audible 5s ¥5, 1080p 24fps. Kuaishou IR 2025-09-24 announced approximately 30% lower 5s 1080P than 2.1.
- No advanced multi-image subjects/voice control; value volume.

#### ④ kling-video-o1:video references/subjects

- **Official**:std/pro, 3–10s, T2V/I2V only 5/10; first/last continuous+**multi-image-only subjects**+**video references/editing**; voice❌.
- Reference-driven or multi-image-consistent scenarios.

#### ⑤ Legacy kling-v1-6 / v1-5 / v1

- `kling-v1-6`:T2V/I2V all, pro first/last, **multireference video**, multimodal edits/continuation/two-image hug/kiss/heart effects, pro1080p
- `kling-v1-5`:mainly I2V, pro first/last/last-only/motion brushes, continuation
- `kling-v1`:T2V/I2V, cameras, first/last, brushes, continuation, two-image effects, 720p
- `kling-v2-master`/`kling-v2-1`/`kling-v2-1-master`:v2tiers; v2-1 pro first/last I2V

**Official capability lookup**:
- Multishot only `v3`/`v3-omni`
- Human voices only `kling-v2-6`pro
- Subjects:`v3`/`v3-omni` strongest video+images,`o1`images+video,`v1-6`multireference
- 4K only `v3`/`v3-omni`
- Video references/editing:`v3-omni`3–10s,`o1`

**Kling JWT integration**:

```python
import jwt, time


def kling_token(ak, sk):
    return jwt.encode(
        {"iss": ak, "exp": int(time.time()) + 1800, "nbf": int(time.time()) - 5},
        sk,
        algorithm="HS256",
        headers={"alg": "HS256", "typ": "JWT"},
    )
```

Wrap authentication, cache tokens within 30 min, refresh 60s before expiry.

---

## 5. MiniMax Hailuo

> Cheapest across modalities, most consistent protocols. Mainland `https://api.minimaxi.com/v1`, international `https://api.minimax.io/v1`, Bearer; OpenAI/Anthropic text SDKs.

### 5.1 MiniMax-M2.7 text

- **IDs**:`MiniMax-M2.7`flagship/`MiniMax-M2.7-highspeed`, twice speed/price; Bailian `MiniMax/MiniMax-M2.7`
- **Released**2026-03-18, latest replacingM2.5/abab7
- **Capabilities**:SWE-Pro 56.22%, official matchingGPT-5.3-Codex; 230BMoE/10Bactive, 200Kcontext, top Chinese literary writing; thinking/functions/Toolathon46.3%
- **API**:OpenAI `base_url=https://api.minimaxi.com/v1`, or Bailian20%implicit-cache discount; Anthropic compatible
- **Official mainland CNY/milliontokens**, platform.minimaxi.com pricing-paygo:

  | Model | Input | Output | Cache read | Cache write |
  |---|---|---|---|---|
  | M2.7 | ¥2.1 | ¥8.4 | ¥0.42, 20% | ¥2.625, 125% |
  | M2.7-highspeed | ¥4.2 | ¥16.8 | ¥0.42 | ¥2.625 |

- **International**:$0.30 input/$1.20 output perM, approximatelyone-eighth Claude Sonnet
- **Position**:Qwen counterpart; M2.7 nuanced characters/emotion/Chinese, Qwen-Plus structured JSON

> abab6.5/M2.5/abab7 remain callable but superseded, not expanded.

### 5.2 image-01

- **ID**:`image-01`
- **Capabilities**:unified text/image; single-face `subject_reference` across scenes;`aspect_ratio` or `width`/`height`, 512–2048, multiples8;`prompt_optimizer`;`n`1–9
- **API**:`POST https://api.minimaxi.com/v1/image_generation`, Bearer JSON, direct `url`24h/base64
- **Prices**:¥0.025success-only; internationalapproximately$0.0035–0.005
- **Style**:cinematic portraits/realistic-material SOTA, strongest three-vendor character continuity; pure anime behind Kolors/Qwen-Image

### 5.3 Hailuo 2.3 video

> 2.3=T2V/I2V; 2.3-Fast=I2V only; R2V separate S2V-01, single face.

#### ① MiniMax-Hailuo-2.3:quality T2V/I2V

- **ID**:`MiniMax-Hailuo-2.3`, released 2025-10-28, current flagship
- **Capabilities**:T2V/I2V, native 1080P, NCRphysics, 85%complex instructions, camera bracket `[左摇,上升]`, ≤3combined; **anime/illustration/gameCG strength**, fiction fit
- **Tiers**:6s 768P/1080P; 10s 768P, no 10s 1080P
- **Mainland per video**:768P 6s ¥2/10s ¥4; 1080P 6s ¥3.5
- **International**:768P$0.045/s, 1080PPro$0.08/s

#### ② MiniMax-Hailuo-2.3-Fast:I2V only, approximatelyhalf price

- **ID**:`MiniMax-Hailuo-2.3-Fast`
- **Capabilities**:I2V, no T2V, same quality/halfprice, rapid iteration
- **Prices**:768P 6s ¥1.35/10s ¥2.25; 1080P 6s ¥2.31

#### ③ S2V-01:character-reference specialist

- **ID**:`S2V-01`
- **Capabilities**:pioneering Subject-to-Video, single face sustains video characters; face/pose/expression/light independently promptable, recurring/multishot fit
- **Parameters**:`subject_reference=[{"type":"character","image":["url"]}]`
- **Limits**:one face, minor environmental distortion, slightly weaker adherence thanT2V/I2V
- **Price**:1.5packcredits/video, approximately¥3

> `MiniMax-Hailuo-02` retains 512P drafts, 6s ¥0.6; rest superseded by 2.3. T2V-01-Director legacy compatibility, not expanded.

**MiniMax two-step URL retrieval**:
1. `POST /v1/video_generation`→`task_id`
2. Poll `GET /v1/query/video_generation?task_id=xxx`→`status=Success`,`file_id`
3. `GET /v1/files/retrieve?file_id=xxx`→short-lived `download_url`; prefer `callback_url`, first answer challenge

---

## 6. Feasibility

### 6.1 Protocols

| Modality/vendor | Bailian | Kling | MiniMax |
|---|---|---|---|
| Text | ✅ OpenAI, reuse | ❌ none | ✅ OpenAI/Anthropic |
| Image | DashScope sync/async | JWT async | REST single URL |
| Video | Unified DashScope async | Multiple JWT async endpoints | REST two-step file_id |

### 6.2 Backend work

| Reuse | Models |
|---|---|
| Existing OpenAI text | `qwen3.7-max`,`qwen3.6-plus`,`qwen3.6-flash`,`qwen3-max`,`qwen-plus`,`MiniMax-M2.7`; Bailian `deepseek-v4-pro/flash`,`kimi-k2.6`,`glm-5.1`,`mimo-v2.5-pro`, same format |
| One new DashScope, sync/async paths | Images `qwen-image-2.0-pro`/`qwen-image-2.0`/`qwen-image-edit`sync,`wan2.7-image`both; videos `happyhorse-1.0-*`/`wan2.7-*`/`wan2.5-*`async; Kling proxies |
| New Kling JWT | Videos `kling-v3`/`kling-v3-omni`/`kling-v2-6`/`kling-v2-5-turbo`/`kling-video-o1`; images `kling-image-o1`/`kling-v3-omni`; unified `/v1/images`/`/v1/videos`, JWT refresh |
| New MiniMax, two-step URLs | `MiniMax-Hailuo-2.3/-Fast`,`S2V-01`; simpler one-step `image-01` |

### 6.3 Authentication

- Alibaba Bearer, regional keys noninterchangeable; async `X-DashScope-Async: enable`
- Kling JWT HS256, 30 min, cache/early refresh
- MiniMax Bearer, file_idtwo-step or callback_url/challenge

### 6.4 Web-fiction fit

| Dimension | Bailian | Kling | MiniMax |
|---|---|---|---|
| Chinese scripts/structure | ★★★★★ | — | ★★★★★ |
| Chinese cover/poster text | ★★★★★ Qwen-Image-2.0 | ★★★★ image-o1/v3-omni improvement | ★★★ |
| Comic/storyboard cross-cell characters | ★★★★★ Qwen-Image-2.0comics | ★★★★★ image-o1≤10 refs, comics/series | ★★★★ image-01 |
| Video R2V | ★★★★★ happyhorse-r2v/wan2.7-r2v, multiple/audio/video | ★★★★★ v3-omni video+images, o1video refs | ★★★★ S2V-01stable oneface only |
| Video physics | ★★★★★ HappyHorse first/second | ★★★★★ Omni physics | ★★★★ NCR native 1080P, anime |
| Multishot video | ★★★★★ wan2.7/happyhorse continuity | ★★★★★ official v3/v3-omni | ★★★ |
| Audiovisual | ★★★★★ HappyHorse 7 languages/wan2.5audio_url | ★★★ onlyv2-6 pro human voices, v3/v3-omni❌, platform effects | ★★★ external TTS |
| Value | ★★★★ HappyHorse 720P ¥0.9/s | ★★★ std ¥0.6/s, pro audible ¥1/s, 4K ¥3/s, image ¥0.2 | ★★★★★ Hailuo-2.3-Fast 768P 6s ¥1.35 |

---

## 7. Risks and uncertainties

1. **Prices fluctuate**:Qwen3.7-Max 50% off until 2026-06-22, all Qwen3.6 models 55% off, HappyHorse 20% off, Kling plans. Configure `PROVIDER_REGISTRY` prices, not hardcode.
2. **Official verified**:HappyHorse 1.0 all 720P ¥0.9/s, 1080P ¥1.6/s, launch; Qwen tiers/3.7-Max ¥12/¥36, official pricing/products; MiniMax M2.7 ¥2.1/¥8.4, image-01 ¥0.025, Hailuo 2.3 tiers; **all Kling video/image credit rates**, klingai.com/dev/pricing, video 1=¥1 across mode×duration×video×audio, image 1=¥0.025, image-o1/v3-omni ¥0.2.
3. **Console pending**:`qwen-image-2.0-pro/2.0`,`wan2.7-image`exact CNY/image,`qwen3.6-flash`tokens, not directly listed; Bailian DeepSeek/Kimi/GLM/MiMo pricing. Kling prices verified, no further check.
4. **IDs change**:monthly Qwen snapshots, use stable aliases/regress. Official2026-05-21favorites `qwen3.7-max`/`qwen3.6-plus`/`qwen3.6-flash`; images/videos `wan2.7-image-pro`/`qwen-image-2.0-pro`/`happyhorse-1.0-*`. MiniMax release page M2.7/Hailuo 2.3/image-01.
5. **Official capabilities**:strongest Kling `kling-v3`/`v3-omni`video+image subjects, not oldv1-6;`v3`/`v3-omni`human voices❌, only `kling-v2-6`pro. Hailuo 2.3T2V/I2V, FastI2V, no R2V; S2V-01singleface.
6. **Alibaba images complementary**, Qwen text/comics versus Wan portrait realism, assess per scene, not replacements.
7. **HappyHorse open/commercial**:base/distillation/upscaling/inference code all open; many same-name third-party sites, use official Bailian APIs.
8. **International**:MiniMax minimaxi.com/mainland versusminimax.io/global keys separate; Alibaba regional keys separate; Kling app.klingai.comChina versusklingai.comglobal.
9. **Leaderboards** change quickly; Chinese specialization is not universal benchmark leadership, assess Chinese/web-fiction outputs.
10. **Kling models primary-verified, prices still console-dependent**:v3/v3-omni/v2-6/v2-5-turbo/o1/image-o1 individually checked, official matrices govern. ①v3/v3-omni voices❌, only `kling-v2-6`pro, contradicting third parties; ②exact per-tier inspiration points require app.klingai.comlogin, not official docs.

---

## 8. Official references

- Bailian catalog/recommendations, updated2026-05-21:<https://help.aliyun.com/zh/model-studio/models>
- Bailian videos:<https://help.aliyun.com/zh/model-studio/video-generate-edit-model/>
- Bailian prices:<https://help.aliyun.com/zh/model-studio/model-pricing>
- Qwen image API:<https://help.aliyun.com/zh/model-studio/qwen-image-api>
- Qwen editing:<https://help.aliyun.com/zh/model-studio/qwen-image-edit-guide>
- Wan T2V:<https://help.aliyun.com/zh/model-studio/text-to-video-api-reference>
- Qwen product:3.7-Max/Qwen-Image/Wan 2.7:<https://www.aliyun.com/product/tongyi>
- Kling official video models:<https://klingai.com/document-api/apiReference/model/videoModels>
- Kling official image models:<https://klingai.com/document-api/apiReference/model/imageModels>
- Kling official prices:<https://klingai.com/dev/pricing>
- Kling development:<https://app.klingai.com/cn/dev/document-api>
- MiniMax pay-as-you-go:<https://platform.minimaxi.com/docs/guides/pricing-paygo>
- MiniMax releases:<https://platform.minimaxi.com/docs/release-notes/models>
- MiniMax I2V:<https://platform.minimaxi.com/docs/api-reference/video-generation-i2v>
- MiniMax S2V-01:<https://platform.minimax.io/docs/api-reference/video-generation-s2v>
- Hailuo 2.3 announcement:<https://www.minimax.io/news/minimax-hailuo-23>

---

**Architecture alignment**:ArcReel preset `PROVIDER_REGISTRY` + `lib/backends/{text,image,video}_backends/`
