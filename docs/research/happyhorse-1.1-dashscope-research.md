# HappyHorse 1.1 video-generation research (Alibaba Cloud Bailian DashScope)

Research date: 2026-08-08. Sources limited to Alibaba Cloud primary channels: `help.aliyun.com` official documentation (highest authority), and official `developer.aliyun.com` community articles (pricing announcements/version comparisons, secondary authority). Unavailable facts are marked unconfirmed.

## 0. Is happyhorse an alias?

**No: a real, publicly released Alibaba-developed series, high confidence.**

- Official `help.aliyun.com` API pages directly document model IDs `happyhorse-1.1-t2v` / `happyhorse-1.1-i2v` / `happyhorse-1.1-r2v` / `happyhorse-1.0-*` (sources below).
- Community introductions describe HappyHorse (快乐小马), Alibaba's native multimodal video model from ATH, gray-launched 2026-04-27: 15B parameters, single-stream Transformer, joint audio/video, fixed "Happy Horse" watermark. Sources: <https://developer.aliyun.com/article/1731571>, <https://developer.aliyun.com/article/1731726>
- Repository `lib/config/registry.py` entries `happyhorse-1.0-{i2v,t2v,r2v}` (720P ¥0.9/s, 1080P ¥1.6/s, 3–15s, nine r2v references) match official documentation, so registry IDs already are official, without real-series mapping.
- The series postdates common model knowledge cutoffs; public research must use post-April-2026 sources.

## 1. Findings table

| Modality | Model ID | Resolution tiers | Duration | Capabilities | List price (CNY/second) |
|------|----------|-----------|------|--------|------------------|
| Text-to-video | `happyhorse-1.1-t2v` | 480P / 720P / 1080P default | Integer 3–15s, default 5 | No first frame; synchronized audio; nine ratios | 480P 0.45 / 720P 0.9 / 1080P 1.2 |
| Image-to-video, first frame | `happyhorse-1.1-i2v` | 480P / 720P / 1080P default | Integer 3–15s, default 5 | First frame ✓, last ✗; input-frame ratio, no ratio parameter; synchronized audio | Same |
| Reference-to-video | `happyhorse-1.1-r2v` | 480P / 720P / 1080P default | Integer 3–15s, default 5 | 1–9 references, no first frame; synchronized audio | Same |
| First/last frames | **No 1.1 variant** | — | — | Official recommendation `wan2.7-i2v-2026-04-25` | — |
| Video editing | `happyhorse-1.0-video-edit`, 1.0 only | 720P / 1080P default | Output 3–15s, input 3–60s | Video + 0–5 references + instructions; style/local replacement | Unconfirmed |

Promotion: all 1.1 variants had 40%-off pricing, 2026-06-22 through 2026-07-06 (720P 0.54 / 1080P 0.72 CNY/s), alongside 20%-off 1.0. That period ended; extensions/new promotions unconfirmed. New-user free quota: ten seconds of video.

## 2. Details and references

### 2.1 Text-to-video `happyhorse-1.1-t2v`

Source: <https://help.aliyun.com/zh/model-studio/happyhorse-text-to-video-api-reference>

- Page lists `happyhorse-1.1-t2v` and `happyhorse-1.0-t2v` together.
- `resolution`: 480P / 720P / 1080P, default 1080P. Combined 1.1/1.0 tables leave 480P applicability to both unconfirmed; 1.1 announcements advertised only 720P/1080P.
- `duration`: integers [3,15], default 5.
- `ratio`: 16:9 default, 9:16, 1:1, 4:3, 3:4, 4:5, 5:4, 9:21, 21:9.
- Prompts allow 5000 non-Chinese or 2500 Chinese characters, silently truncating excess like wan2.7.
- `watermark`: default `true`, bottom-right fixed "Happy Horse" text; `false` disables.
- Audio: **no switch in API tables**. Official overview describes text-generated audible video; 1.1 announcements describe native synchronized audio/video. Infer always-on audio without switches, matching 1.0. Sources: <https://help.aliyun.com/zh/model-studio/video-generate-edit-model/>, <https://developer.aliyun.com/article/1749474>
- Regions: North China 2 Beijing, Singapore, Japan Tokyo, Germany Frankfurt, US Virginia.

### 2.2 Image-to-video `happyhorse-1.1-i2v`

Source: <https://help.aliyun.com/zh/model-studio/happyhorse-image-to-video-api-reference>

- First frame ✓, last ✗ for both 1.1/1.0; documentation title explicitly says first-frame-based.
- Output ratio follows the first image automatically; ratio parameter unsupported.
- Input: JPEG/JPG/PNG/WEBP; both dimensions ≥300px; ratio 1:2.5–2.5:1; ≤20MB.
- Resolution/duration/prompt/watermark match t2v.

### 2.3 Reference-to-video `happyhorse-1.1-r2v`

Source: <https://help.aliyun.com/zh/model-studio/happyhorse-reference-to-video-api-reference>

- 1–9 reference images, identical 1.1/1.0 maxima.
- `input.media` entries `{type: "reference_image", url}`; prompts refer through `[Image N]`.
- 480P/720P/1080P, default 1080P; duration 3–15s.
- No first-frame inputs, unlike wan2.7-r2v's first-frame-plus-references shape.

### 2.4 Video editing `happyhorse-1.0-video-edit`

Source: <https://help.aliyun.com/zh/model-studio/happyhorse-video-edit-api-reference>

- **1.0 only**, no 1.1 variant. MP4/MOV input, H.264 recommended, 3–60s, long edge ≤4096px, short ≥360px, ≤100MB, >8fps; 0–5 references; output 3–15s, 720P / 1080P default. Pricing unconfirmed.

### 2.5 Pricing and free quotas

- **1.1 list: 480P ¥0.45/s, 720P ¥0.9/s, 1080P ¥1.2/s**, identical t2v/i2v/r2v per-output-second prices. 720P/1080P: <https://developer.aliyun.com/article/1750051> (“720P at 0.9 yuan/second and 1080P at 1.2 yuan/second”), consistent with promotion 0.54/0.72 at 40% off. Maintainers verified 480P in Bailian console tables; dynamically rendered help.aliyun model catalogs yielded no public-page prices.
- 40% off, 2026-06-22 through 2026-07-06: 720P ¥0.54/s, 1080P ¥0.72/s. Sources: <https://developer.aliyun.com/article/1743014>, <https://developer.aliyun.com/article/1742909>
- **1.0 list: 720P ¥0.9/s, 1080P ¥1.6/s**, matching current registry. Source: <https://developer.aliyun.com/article/1731470>
- Free ten-second HappyHorse video quota. Source: <https://developer.aliyun.com/article/1743014>

### 2.6 Differences from 1.0

Source: <https://developer.aliyun.com/article/1742909>, community feature analysis.

- 1080P decreases ¥1.6/s → ¥1.2/s; 720P remains ¥0.9/s.
- Better instruction adherence/reference fidelity (faithfully rendered text/borders), lip sync, relaxed safety thresholds, faster output (1080P 15s approximately twelve versus fourteen minutes).
- Both support 3–15s; API pages now list 480P, whose version applicability is unconfirmed because early 1.0 sources listed only 720P/1080P.
- **Deprecation unconfirmed**: at research time, all 1.0 variants remained sold in APIs/Token Plan docs and had concurrent 20%-off promotions; no official retirement/shutdown announcements found.

## 3. Token Plan coverage and invocation paths

Sources: <https://help.aliyun.com/zh/model-studio/token-plan-overview>, <https://help.aliyun.com/zh/model-studio/token-plan-multimodal-gen>, <https://help.aliyun.com/zh/model-studio/token-plan-personal-overview>, <https://help.aliyun.com/zh/model-studio/token-plan-faq>

### Covered models

- Personal documentation explicitly lists `happyhorse-1.1-i2v` / `happyhorse-1.1-t2v` / `happyhorse-1.1-r2v`, shared by personal/team plans.
- Multimodal docs default to `happyhorse-1.1-t2v` with examples for `happyhorse-1.0-t2v` / `happyhorse-1.1-r2v`, indicating 1.0 coverage too; consoles govern complete lists.
- Wan video is absent from official video examples; coverage **unconfirmed**. FAQs do cover image `wan2.7-image`.

### Invocation: native DashScope asynchronous paths, not compatible-mode

Official multimodal video calls:

```http
POST https://token-plan.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis
GET  https://token-plan.cn-beijing.maas.aliyuncs.com/api/v1/tasks/{task_id}
```

**Same native asynchronous two-step structure as standard DashScope**, including `X-DashScope-Async: enable`, with different domains/plan keys. Only text uses `https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1` (OpenAI, also Anthropic compatibility).

### Plan domains versus standard dashscope.aliyuncs.com

| Dimension | Standard DashScope | Token Plan |
|------|----------------|-----------|
| Domain | `dashscope.aliyuncs.com` | `token-plan.cn-beijing.maas.aliyuncs.com`, Beijing only |
| API keys | Ordinary Bailian | Dedicated `sk-sp-` keys; personal/team independent, noninterchangeable |
| Paths | Native `/api/v1/...` + `/compatible-mode/v1` | Both supported; video native asynchronous |
| Billing | Pay-as-you-go | Credits; **exhaustion blocks with 429 Allocated quota exceeded, never pay-as-you-go fallback** |
| Mixing | — | Plan keys on `dashscope.aliyuncs.com` or vice versa → `401 InvalidApiKey`; FAQ identifies wrong generic Base URLs as common causes, without silent usage billing |
| Quotas | — | Team monthly seats 25k/100k/250k Credits reset without rollover; personal seven-day rolling limits, no rollover within windows |

Video credit conversion per second/tier is **unconfirmed**, absent from docs; consult consoles.

## 4. Suggested ArcReel registry/backend additions

Against ADR 0018 (fail-loud: unregistered `supported_durations` reject without implicit fallback):

### `lib/config/registry.py` (DashScope video)

Add three entries shaped like 1.0:

- `happyhorse-1.1-t2v` / `happyhorse-1.1-i2v` / `happyhorse-1.1-r2v`
  - `supported_durations = list(range(3, 16))`, explicitly registering official integer 3–15s
  - `resolutions = ["480p", "720p", "1080p"]`
  - `pricing = _dashscope_video_pricing(..., {"480p": 0.45, "720p": 0.9, "1080p": 1.2})`, list prices with console-verified 480P, excluding promotions
  - Move `default=True` from `happyhorse-1.0-i2v` to `happyhorse-1.1-i2v` (cheaper 1080P, official recommended series), updating `lib/backends/video_backends/dashscope.py::DEFAULT_MODEL`
- Retain three 1.0 entries (still sold, no deprecation); omit `happyhorse-1.0-video-edit` lacking corresponding pipeline capabilities

### `lib/backends/video_backends/dashscope.py::_MODEL_PROFILES`

- `"happyhorse-1.1-t2v": VideoCapabilities(first_frame=False)`
- `"happyhorse-1.1-i2v": VideoCapabilities(first_frame=True)`
- `"happyhorse-1.1-r2v": VideoCapabilities(first_frame=False, max_reference_images=9)`
- Non-substring key invariant remains (`happyhorse-1.0-*` and `happyhorse-1.1-*`), requiring no `_profile_for_model` tolerance changes
- Consider series-wide `max_prompt_chars`: 5000 non-Chinese/2500 Chinese, silently truncated yet charged like wan2.7, currently the only gated family. One `max_prompt_chars` cannot express Chinese 2500 versus non-Chinese 5000; extend dimensions first or register conservative 2500
- Watermark remains default `true`, "Happy Horse"; continue explicit `watermark: false`, matching 1.0

### Related areas

- `lib/custom_provider/duration_presets.py` `happyhorse` regex (3–15) and `lib/custom_provider/endpoints.py` `"happyhorse"` dashscope-async-video routing already cover 1.1
- Do not use HappyHorse for last frames; no series support, official recommendation `wan2.7-i2v-2026-04-25`
- Token Plan video shares native schemas; theoretically reuse `DashScopeVideoBackend` with `base_url` `https://token-plan.cn-beijing.maas.aliyuncs.com/api/v1` + `sk-sp-`. Exhaustion is hard 429, not usage fallback; map to understandable quota errors and restrict Beijing
- Official HappyHorse source entries are in `docs/api-docs/providers/dashscope.md`; retain per-page links below without duplicating official bodies

## Sources

Official documentation, help.aliyun.com:

- Text-to-video: <https://help.aliyun.com/zh/model-studio/happyhorse-text-to-video-api-reference>
- First-frame image-to-video: <https://help.aliyun.com/zh/model-studio/happyhorse-image-to-video-api-reference>
- Reference-to-video: <https://help.aliyun.com/zh/model-studio/happyhorse-reference-to-video-api-reference>
- Video editing: <https://help.aliyun.com/zh/model-studio/happyhorse-video-edit-api-reference>
- Video overview: <https://help.aliyun.com/zh/model-studio/video-generate-edit-model/>
- Model catalog: <https://help.aliyun.com/zh/model-studio/models>, dynamic page without captured prices
- Token Plan overview/personal/multimodal/FAQ: <https://help.aliyun.com/zh/model-studio/token-plan-overview>, <https://help.aliyun.com/zh/model-studio/token-plan-personal-overview>, <https://help.aliyun.com/zh/model-studio/token-plan-multimodal-gen>, <https://help.aliyun.com/zh/model-studio/token-plan-faq>

Alibaba Cloud developer community, pricing/version comparisons:

- 1.0 prices: <https://developer.aliyun.com/article/1731470>
- Introduction: <https://developer.aliyun.com/article/1731571>, <https://developer.aliyun.com/article/1731726>
- 1.1 pricing, discounted 0.54/0.72: <https://developer.aliyun.com/article/1743014>
- 1.1 features/costs/promotion/1.0 comparison: <https://developer.aliyun.com/article/1742909>
- 1.1 synchronized audio/tiers: <https://developer.aliyun.com/article/1749474>
- Token Plan 1.1 list prices 0.9/1.2: <https://developer.aliyun.com/article/1750051>
