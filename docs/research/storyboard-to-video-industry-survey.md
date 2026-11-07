# Industry implementation survey of storyboard-to-video

> Purpose: evidence for ArcReel modeling, whether grid_4/6/9 are assembly optimizations inside storyboard routes or separate generation routes. State primary facts without deciding for the owner. Research/access date: 2026-08-03 throughout. Fast-changing products/models mean missing-primary-source findings may be outdated.

## 1. Core findings

**No mainstream public video API was found natively accepting one complete multicell storyboard image, internally splitting it into shots, and producing continuous multishot video in one generation.** Sora, Vidu, Kling, Dreamina/Seedance, Hailuo, Pika, Runway, and Veo/Flow capabilities fall into three categories, often layered within products:

1. **Reference-to-video / r2v**: approximately1–9 images anchor characters/styles/subjects; outputs are **continuous videos**, without image-to-shot/time mapping. Shared Vidu/Kling/Seedance/Hailuo API foundations with clear cross-vendor terminology.
2. **First/last frames**: exactly two images define start/end; interpolation yields **continuous videos**. Kling/Veo3.1/Hailuo/Dreamina support this narrower r2v case with two time-semantic images.
3. **UI timelines/storyboard editors**: Sora, Flow Scenebuilder, Dreamina story creation, Runway Multi-Shot Recipes are **product/orchestration**: organize shots/cards, invoke underlying text/image/video generation per shot, then concatenate/transitions, rather than feeding multicell boards once.

**The closest recorded practice to ArcReel grids** (strong image model→multicell image→slice→per-cell i2v) is third-party gpt-image-2 + Seedance2.0 tutorials: generate3×3 grids, feed cells individually. **Community/manual composition, not official single APIs/buttons** (see5.6). Thus existing ArcReel grids already align with the frontier of known practice, not lagging native storyboard-to-video.

## 2. Per-product/model findings

### 1. OpenAI Sora (Sora2/storyboard)

**Product UI**: Sora2 Web storyboards serve ChatGPT Pro via composer. Build videos frame-by-frame like Sora1, or describe scenes/select duration and generate editable detailed storyboards. Caption cards for scenes/characters/actions are arranged chronologically into "a single multi-shot clip"; keyframe images can replace text, generating connecting video. Separate **Stitching** combines **individually generated** clips up to60s, distinguishing generated multishot storyboards from concatenation.
Source: OpenAI Help Creating videos with Sora, <https://help.openai.com/en/articles/12460853-creating-videos-with-sora>. Direct fetch403; official search snippets only, not sentence-by-sentence full-page verification. Access2026-08-03.

**Model APIs**: no public developer documentation found for storyboard underlying inputs. Sora2 API access concerns video generation; storyboards are product editing. No official source establishes single joint multishot calls versus per-card calls/internal assembly. **Unconfirmed by primary sources**; only product-level one multishot result is confirmed.

### 2. Vidu (Shengshu)

**Product UI**: official **Reference to Video** is independent beside text/image-to-video.

**API**: viduq3-mix / viduq3-turbo / viduq3 / viduq2 / viduq1 / vidu2.0 `reference2video` accepts **1–7 images**, or1–4 for viduq2-pro with video. Documentation states: "The model will use the provided images as references to generate a video with consistent subjects". Images are **subject/style anchors**, not shots/time segments, with **no storyboard wording**. Continuous outputs, durations per model, the viduq3 series supports up to 16 seconds at 1080p.
Source: official <https://platform.vidu.com/docs/reference-to-video>, accessed2026-08-03.

### 3. Kling (Kuaishou)

**Product UI**: first-frame, first/last, references, editing are parallel modes. Reference marketing resembles multi-image guidance; **no official storyboard-to-video name found**.

**API**, checked through Alibaba Bailian mirrors of native parameters, as a Kling reseller:
- Text: no images, continuous3–15s.
- First-frame: one starting image, continuous output.
- First/last: two timed endpoint images, interpolated continuous output.
- References: maximum7 images including element_list counts,4 with feature video. Subject/style references **without shot/time correspondence**, continuous3–15s or3–10 with feature video.
Source: <https://help.aliyun.com/zh/model-studio/kling-video-generation-api-reference/>,2026-08-03. Official <https://klingai.com/document-api/apiReference/> / <https://kling.ai/document-api/apiReference/model/multiImageToVideo> repeatedly returned403/446, preventing original sentence verification. Label **partial-primary**: reseller native passthrough, not Alibaba-created contracts, but not original Kling text.

### 4. Dreamina / Seedance (ByteDance)

**Product UI**: jimeng.jianying.com story creation turns text into **storyboard scripts**, then confirmed **individual shot images**, finally videos with transitions/basic animation: text→script→per-cell images→per-cell/segment video→assembly. **Multistep product orchestration**, not one multicell model call. Separate first/last input controls continuous-clip endpoints alongside story creation.
Sources: search-aggregated CSDN/Zhihu/encyclopedia retellings, **no sentence-level official help source located**. Workflow is unverified secondary reporting, accessed2026-08-03.

**Seedance official Ark API**:2.0 released2026-04-14 with text/image/audio/video; image-to-video supports first-frame or first/last-frame control, multimodal references combine0–9 images/0–3 videos/0–3 audio. Like Vidu/Kling, references anchor one video without board-cell mapping. **Seedance2.5**, reported at FORCE2026-06-23 while rollout continued, claims30s native videos and50 multimodal references: larger reference counts/duration, **no official evidence of new multicell or per-shot-timeline contracts**. These claims rely on SegmentFault/PingWest/Sina reports; official deep parameter pages failed404/navigation redirects, **primary parameters unconfirmed**.
Sources: <https://www.stdaily.com/web/gdxw/2026-04/14/content_502009.html>, secondary media quoting official release; Ark <https://docs.volcengine.com/docs/82379/1951250> yielded navigation only. Access2026-08-03.

### 5. Hailuo (MiniMax)

**Product UI**: no primary independent storyboard feature found. Search snippets position text/image generation as bypassing script/storyboard/shooting, **avoiding storyboards** rather than arranging them.

**API**: video-generation-v2-create distinguishes roles `first_frame` / `last_frame`, maximum one each, versus `reference_image`, maximum9, plus `reference_video` / `reference_audio`. **Frame/image-to-video and multimodal-reference modes are mutually exclusive**. One continuous clip, without storyboard/cell/multishot-timeline input concepts.
Source: official <https://platform.minimaxi.com/docs/api-reference/video-generation-v2-create>,2026-08-03.

### 6. Pika

**Product UI**: **Scene Ingredients / Pikascenes** combines characters/props/clothes/background images into one shot. Marketing mentions translating storyboards to detailed prompts, an analogy: **single-shot** multireference synthesis, neither multishot output nor multicell input.
Sources: aggregated pages, including third-party pikartai.com mirrors; **no pika.art primary text located**. Search snippets may not be fully primary, accessed2026-08-03.

**API**: fal.ai-forwarded pikascenes accepts image URL lists/prompts/ratios/durations, likewise single-shot references without cells.

### 7. Runway

**Product/API**: official Dev **Multi-Shot Video Recipe** is orchestration wrapping, not the base model. Two modes:
- **Auto**: story prompt split into shots internally.
- **Custom**:3–5 shots, each `prompt`3–512characters and `duration` seconds, refined/assembled in supplied order.
Return task IDs; poll `GET /v1/tasks/{id}` for video URLs. **One externally exposed API call**, but docs do not establish separate Gen-4/4.5 calls/concatenation versus joint multishot models. Recipe naming suggests orchestration; **not explicitly confirmed**.
Source: official <https://docs.dev.runwayml.com/recipes/multi-shot-video/>,2026-08-03.

### 8. Google Veo / Flow

**Gemini API**: Veo3.1 `image` single first frame, `lastFrame` single last frame (interpolation3.1 only), `referenceImages` up to3 style/content references, `VideoGenerationReferenceImage`. The documentation explicitly states "Videos per request: 1": **one continuous video per request**, no storyboard/single-call-multishot wording/parameters.
Sources: official <https://ai.google.dev/gemini-api/docs/veo>; first/last Cloud <https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/generate-videos-from-first-and-last-frames>, both2026-08-03.

**Flow UI**: filmmaking product with **Scenebuilder**, officially called "your in-Flow storyboard". Assemble **separately generated shots** into narratives, edit/extend with character/action continuity; individual Veo generations remain separate. **Timeline/product orchestration**, not multicell inputs. Ingredients to Video supports three references per prompt, matching `referenceImages`.
Source: official Google blog <https://blog.google/innovation-and-ai/products/google-flow-veo-ai-filmmaking-tool/>,2026-08-03.

### 9. gpt-image-1/2-based products: unofficial third-party workflows

OpenAI does not advertise storyboards as packaged gpt-image1/2 functionality; gpt-image2 remains general image generation/editing. Venice/Alici/CrePal/Rewarx tutorials **generate3×3 boards once, slice nine shot cells, then feed Seedance2.0 per cell** for trailers/ads. This structurally matches grid_4/6/9: combined generation preserves style/characters, slicing precedes i2v. **Community composition, not official single APIs/buttons**, with no vendor-native-board capability claim.
Sources: Venice <https://venice.ai/blog/how-to-create-ai-trailers-gpt-image-2-seedance>; Rewarx <https://www.rewarx.com/blogs/generate-storyboard-frames-gpt-image-2>. Third-party tutorials, not vendor documentation, accessed2026-08-03.

## 3. Terminology

| Term | Industry meaning | Primary evidence |
|---|---|---|
| **reference-to-video / 参考生视频 (r2v)** | Usually1–9 character/scene/prop images **anchor subject/style consistency**; text prompts produce **continuous videos**, without shot/time mappings. A third mode beside text/image, consistent across vendors | Official Vidu `reference2video`1–7, Kling≤7, Seedance0–9, MiniMax `reference_image`≤9 exclusive of first/last, all confirmed by primary documentation |
| **first/last frame / 首尾帧** | Narrow r2v case: exactly two images, start/end semantics, interpolated motion, one continuous video | Kling; Veo3.1 `image`+`lastFrame`; MiniMax `first_frame`/`last_frame`; Dreamina, primary/partial-primary as above |
| **storyboard-to-video / 分镜板生视频** | **No unified cross-vendor technical meaning**, spanning marketing/UI orchestration: ①Sora chronological text/keyframe cards produce a "single multi-shot clip", underlying joint generation unconfirmed; ②Dreamina text→scripts→images→videos→transitions, orchestration; ③Runway shot lists assembled, internal calls unconfirmed; ④Flow pre-generated shots on timelines; ⑤Pika/gpt-image marketing analogy, single-shot references or manual community sequences. **No primary contract for one complete board input internally split into continuous multishot output.** |
| **Chinese “分镜板生视频”** | Almost always AI scripts/images→per-cell videos→concatenation, **multistep workflow**, orchestration meaning④above, not one-board-one-call | Dreamina product descriptions through secondary sources, section4, original primary text unverified; reference only |

## 4. Implications for ArcReel modeling: reference, not owner decisions

Evidence inclinations for decision support:

1. **No primary evidence establishes whole boards natively producing continuous multishot clips in mainstream products/public APIs.** Storyboard functions invoke shots/assemble (Sora keyframes, Dreamina, Runway Recipes, Flow) or borrow marketing terminology (Pika). A separate industry-native storyboard route therefore has no verified public factual foundation.

2. **Existing grids structurally match the closest recorded literal storyboard-to-video practice**: strong image models→multicell boards→slices→per-scene i2v, gpt-image2/Seedance third-party composition (2.9). This optimizes batch-consistent storyboard production; final per-scene generation remains the existing storyboard/i2v route. No evidence supports another input-contract-distinct route.

3. **A third clear input paradigm exists alongside i2v/r2v (`lib/script/reference_video/`)**: exactly-two-image first/last temporal endpoints. ArcReel may not distinguish it explicitly; if i2v is first-frame driven, it may be optional keyframe submode rather than independent route. Incidental observation outside this investigation.

4. **Evidence favors grids as internal assembly optimizations** because routes differ by model input contracts (single storyboard i2v versus asset-reference sets r2v). Grids still feed sliced single first/last-frame images into i2v, changing only upstream production/assembly. Modeling remains an owner tradeoff with possible other product/engineering concerns; this report does not decide.

## 5. Methods and limitations

- Official sites (klingai.com/kling.ai, deep docs.volcengine.com parameters, help.openai.com) returned403/446/navigation redirects, preventing sentence-level verification. Sections identify lower-confidence substitutes: search snippets/reseller mirrors/secondary media, labeled partial-primary or unconfirmed-secondary.
- Seedance2.5 and underlying Sora joint-versus-orchestrated generation are the largest uncertainties, both unconfirmed by primary sources.
- Research examines documented wording, without product/API tests. Undocumented does not completely imply nonexistent; reviewers should retain that limitation.
