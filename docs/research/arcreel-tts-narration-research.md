# ArcReel TTS narration-dubbing selection and integration research

> Research date: 2026-06-02. Prices/model names/interfaces were fetched that day. TTS prices change frequently; recheck live documentation before engineering decisions.
> Mark every uncertainty **UNVERIFIED**, never invent external-provider data.
> Delivery decisions: `docs/adr/0010-tts-audio-through-generation-queue.md` and "Media types and dubbing (TTS)" in `GLOSSARY.md`.

## 0. Scope and positioning

Select TTS for ArcReel (Chinese novels→short videos), **initially narration-mode dubbing**.

- **Primary criterion**: Chinese narration quality, including tone/pauses/emotion/continuity in long-form reading. Architecture stays language-independent; list coverage per provider and let users choose.
- **Both paths**: A cloud APIs, primary; B self-hosted/on-device open-source for privacy/cost comparison.
- **Constraints**: ① custom credentials fixed to `api_key` + `base_url` (`docs/adr/0008`), multi-field authentication built-in only; ② compatible `/v1/audio/speech` reuses OpenAI clients/intermediaries; ③ declarative `kind` pricing (`docs/adr/0009`); ④ synchronous/asynchronous determines `text_backends`/`video_backends` patterns; ⑤ credential reuse with existing Gemini/Ark/OpenAI/Grok/Vidu/DashScope minimizes integration.

Method: two adversarial deep-research rounds (109 + 9 agents), then targeted duration/provider-spec/watermark checks. Confidence/votes: 3-0 unanimous,2-1 majority,0-3/1-2 rejected.

## 1. Findings

### 1.1 Recommendations by tier

| Tier | Preferred | Why | Cost |
|---|---|---|---|
| **Lowest integration effort** | **DashScope Qwen-TTS** | Reuse existing single-field sk- credentials without forms; best Chinese in low-friction tier; synchronous HTTP fits | Native `/api/v1`, not OpenAI; new synchronous adapter; price UNVERIFIED |
| **Extensible/self-service** | **Custom OpenAI-compatible audio** | Single field/real compatibility; Fish, IndexTTS shims, intermediaries without per-provider code | Audio `EndpointSpec`, `CustomAudioBackend`, audio `infer_endpoint` |
| **Best Chinese** | **Doubao large-model synthesis** / ElevenLabs | Doubao 325 voices/native dialects/cloning2.0/emotional continuity; ElevenLabs multilingual v2/v3 reputation | Doubao appid+access_token+resource_id requires built-in; **Ark keys cannot be reused**, Seed Speech separate; neither OpenAI-compatible |
| **Self-hosted privacy** | **CosyVoice2** / **VoxCPM-0.5B** | Apache2.0 commercial; VoxCPM Chinese CER 0.93%, best open-source | GPU, no official OpenAI wrappers; memory/RTF UNVERIFIED |

### 1.2 Initial v1 decision

**DashScope Qwen-TTS preset (credential reuse/character pricing/native sync) + custom OpenAI-compatible audio (`openai-tts` → `/v1/audio/speech` + `CustomAudioBackend`).** Fish/IndexTTS shims/intermediaries use custom paths. See `docs/adr/0010`.

## 2. Cloud APIs

### 2.1 Main comparison

| Provider | Chinese | Compatible `/v1/audio/speech` | Sync/async | Authentication | Billing | Existing integration | Confidence |
|---|---|---|---|---|---|---|---|
| **OpenAI**, tts-1/tts-1-hd/gpt-4o-mini-tts | Strong English, weak Chinese | ✅ standard | Synchronous bytes + chunked streams | Single field | Characters: tts-1$15/tts-1-hd$30 per1M | Text/image yes, TTS no | 3-0; prices 2-1 |
| **ElevenLabs**, Multilingual v2/v3, Flash/Turbo | Excellent | ❌ proprietary | Synchronous `/stream` chunking, still one request | Single field | Flash/Turbo$0.05, Multilingual$0.10/1K characters | No | 3-0 |
| **DashScope**, Qwen-TTS/CosyVoice | Strong | ❌ TTS outside compatibility, chat only | Qwen sync HTTP; CosyVoice realtime WebSocket | Bearer sk- | Characters/10K; CosyVoice approximately¥2/10K **UNVERIFIED** | ✅ reusable | 3-0; priceUNVERIFIED |
| **Doubao Seed-TTS** | Strong/native dialects | ❌ openspeech.bytedance.com | HTTP/WSS/**async long text**, submit→query ≤100K characters,7-day audio | **Multiple fields**, appid/access_token/resource_id | Per character / character packs + yearly voices | ❌ Ark key separate | 3-0 |
| **MiniMax**, T2A async | Strong | ❌ `t2a_async_v2` | **Async**, task→poll→file_id,9h URL expiry | Single-field claim **rejected1-2**, never assume custom compatibility | Credit-subscription claim **rejected0-3** | No | Interface 3-0 |
| **Fish**, OpenAudio S1 | Good | ✅ native `/v1/audio/speech` | Synchronous streaming,TTFB <150ms | Bearer | **UTF-8 bytes**, $15/1M | No; best custom candidate | One targeted round |
| **Gemini** native TTS | Good/cmn | ❌ `generateContent` base64/**no streams** | Sync | x-goog-api-key | **Tokens** | ✅ reusable | Targeted round |
| **Google Cloud**, Chirp3/Neural2/WaveNet | Good/cmn-CN | ❌ `synthesize` REST | Sync/long-audio LRO | **Multiple**, service accounts | Characters,$4–$160/1M | Vertex credential family, API enablement needed | Targeted round |
| **Azure** | Good/zh-CN+dialects | ❌ | Realtime sync/async batches | **Multiple**, key+region | Characters,$16/1M standard | No | Targeted round |
| **Tencent** | Strong | ❌ signed API | Sync/streaming | **Multiple**, AppID/SecretId/SecretKey signatures | Character tiers | No | Targeted round |
| **iFlytek** | Top tier | ❌ WSS | WebSocket | **Multiple**, APPID/APIKey/APISecret HMAC | Characters/plans | No | Targeted round |

### 2.2 Key boundaries

- **OpenAI pricing split**: main pricing shifted toward realtime tokens, while dedicated character tts-1$15/tts-1-hd$30 per1M are **absent from main tables**; see `developers.openai.com/api/docs/models/tts-1`. Validators judged blanket token-pricing claims exaggerated,2-1.
- **DashScope reuses credentials, not compatibility**: Bearer reuse works, but compatibility exposes chat only. TTS uses `/api/v1/services/aigc/multimodal-generation/generation`, requiring native adapters rather than OpenAI factories/intermediaries.
- **Doubao credential trap**: Seed-TTS is separate from Ark; **existing Ark keys cannot be reused**, requiring built-in presets/multi-field forms.
- **MiniMax rejected claims**: single-field1-2,credits0-3 failed verification; **do not assert custom-provider eligibility**.
- **Pricing kinds**: **characters** (OpenAI/ElevenLabs/DashScope/GCloud/Azure/Tencent/iFlytek), **tokens** (Gemini), **UTF-8 bytes** (Fish). `docs/adr/0009` needs at least `per_character` now.

## 3. Self-hosted/on-device TTS: privacy and licensing traps

| Model | Chinese | License/commercial | Notes | Confidence |
|---|---|---|---|---|
| **CosyVoice2-0.5B** | Strong,CER 1.38% | ✅ Apache2.0 code/weights, commercial/royalty-free | FunAudioLLM/cloning | 3-0 |
| **VoxCPM-0.5B** | **Best open,CER 0.93%** | ✅ Apache2.0 commercial | Tokenizer-free,1.8M Chinese/English hours, ahead IndexTTS2/CosyVoice2 | 3-0, author-reported arXiv preprint, not peer-reviewed |
| **IndexTTS2** | Strong,CER 1.03%, second open | ⚠️ bilibili commercial authorization: v1 written; **v2 thresholds >100M MAU or >CNY1B annual revenue**, smaller businesses likely exempt | Strong emotions; paper duration control **not enabled in release**; **no official API/OpenAI endpoint**, needs shim; GPU/RTF UNVERIFIED | License 2-1 |
| **F5-TTS** | Good | ⚠️ base weights **CC-BY-NC, noncommercial**, Emilia-derived even after finetuning; codeMIT; commercial requires own-data training from scratch | Commercial trap | 3-0 |

**Choose CosyVoice2/VoxCPM** for privacy, Apache2.0 commercial. Verify IndexTTS/F5 licenses per version. Clean integration: self-host OpenAI-compatible `/v1/audio/speech` wrappers through custom audio.

## 4. Risks and unknowns

### 4.1 Duration mismatch: critical, deferred with solutions recorded

Actual TTS duration differs from `NarrationSegment.duration_seconds`, also driving video/subtitle timing. **Industry approach: invert contracts, audio-driven timelines, `duration_seconds` merely generation guidance.**

- Existing `server/services/presentation/jianying_draft_service.py` **already uses actual media**: video/subtitle `trange` uses `actual_duration_us = material.duration`; longest real material wins.
- Longer audio → **extend video** using `setpts`/`tpad` freeze frames, without quality loss, **never accelerate narration**.
- **Subtitles from real audio**: provider character timestamps first (ElevenLabs `with-timestamps`, Chinese support, no infrastructure); WhisperX fallback, whose Chinese community `jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn` accuracy is weaker.
- ⚠️ **Do not expect target-duration TTS**: no commercial Chinese API exposes target_duration; research-only AmazonICASSP22/VideoDubberAAAI23 note Chinese difficulties because token counts differ from duration. IndexTTS2 paper control is not enabled currently.
- ⚠️ Rate-based duration control is unreliable: ElevenLabs0.7–1.2/OpenAI0.25–4/Doubao 0.8–2/MiniMax0.5–2. **OpenAI `gpt-4o-mini-tts` historically ignored speed**, called a documentation bug. Use only ±10% secondary adjustments. `ffmpeg atempo` clean0.5–2x, but speech stretching beyond approximately 10–15% audibly degrades.

> Initial decision: **attach independent, unaligned Jianying audio tracks for manual refinement**; defer automatic retiming/subtitle rearrangement. Solutions above inform future work.

### 4.2 Other issues

- **Long text**: MiniMax inline50K/file1M characters,2-1; Doubao async ≤100K. Short per-segment narration needs no initial chunking.
- **Voice consistency**: fixed voices remain consistent; Doubao 2.0/CosyVoice cloning can lock further, deferred beyond preset v1.
- **Streaming/files**: versionable audio/Jianying need **whole files only**, streaming adds complexity; synchronous backends collect bytes once.
- **Watermarks/compliance**: selected providers **do not mandate unremovable marks on preset voices**, MiniMax `aigc_watermark` default false. User decision treats AI-label compliance as outside this open-source project's responsibility; report does not expand it.

## 5. Initial decision and implementation surface

Full decisions: `docs/adr/0010`, `GLOSSARY.md`.

- **Media**: fourth `media_type`=`audio`, capability `text_to_speech`, peer to image/video/text.
- **Scheduling**: GenerationQueue/Worker audio lane; **synchronous backend** like `text_backends`, seconds, no submit/poll/resume. `enqueue_tts(segment_ids?)`.
- **Backends**: `lib/backends/audio_backends/`, native DashScope sync + custom OpenAI audio.
- **Voices**: configurable string IDs, global default plus `project.json` `settings.narration_voice`, no catalog, optional rate.
- **Versions**: resource_paths/VersionManager, `audio/`, `segment_{id}.mp3`.
- **Data**: `GeneratedAssets.narration_audio: str | None`, project.json not DB; verbatim `NarrationSegment.novel_text`.
- **Billing**: DashScope `per_character`; custom database user prices.

## 6. Must verify before implementation: UNVERIFIED

**Never guess/hardcode external data**; verify primary documentation before coding:

1. Exact DashScope IDs (qwen-tts/qwen3-tts-flash/cosyvoice-v2?) and **pure sync HTTP** support; realtime CosyVoice WebSockets do not fit, favor Qwen.
2. Sync endpoints/schemas, character prices/currencies, voice IDs, formats/sample rates.
3. Compatible `/v1/audio/speech` model/voice/input/response_format/speed schema for audio `EndpointSpec`.
4. Whether custom `price_unit` supports audio characters.
5. Fish OpenAudioS1 cloud mandatory marks, UNVERIFIED.
6. Self-hosted GPU/RTF/official OpenAI wrappers: CosyVoice2/VoxCPM/IndexTTS2.

## 7. References (official primary sources, fetched 2026-06-02)

- OpenAI TTS guide: https://developers.openai.com/api/docs/guides/text-to-speech
- OpenAI tts-1 models/pricing: https://developers.openai.com/api/docs/models/tts-1
- ElevenLabs pricing: https://elevenlabs.io/pricing/api ; streaming: https://elevenlabs.io/docs/api-reference/text-to-speech/stream ; character timestamps: https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps
- Doubao large-model synthesis: https://www.volcengine.com/docs/6561/1257543 ; asynchronous long text: https://www.volcengine.com/docs/6561/1829010 ; voice cloning 2.0: https://www.volcengine.com/docs/6561/1305191
- DashScope OpenAI compatibility: https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope ; Qwen-TTS: https://www.alibabacloud.com/help/en/model-studio/qwen-tts ; pricing: https://help.aliyun.com/zh/model-studio/model-pricing
- MiniMax T2A async: https://platform.minimax.io/docs/guides/speech-t2a-async
- Fish Audio pricing/rate limits: https://docs.fish.audio/developer-guide/models-pricing/pricing-and-rate-limits
- Gemini speech generation: https://ai.google.dev/gemini-api/docs/speech-generation
- Google Cloud TTS pricing: https://cloud.google.com/text-to-speech/pricing
- Azure Speech pricing: https://azure.microsoft.com/en-us/pricing/details/cognitive-services/speech-services/
- Tencent TTS: https://cloud.tencent.com/document/product/1073/37995 ; iFlytek TTS: https://www.xfyun.cn/doc/tts/online_tts/API.html
- CosyVoice LICENSE: https://github.com/FunAudioLLM/CosyVoice/blob/main/LICENSE
- VoxCPM paper: https://arxiv.org/html/2509.24650v1 ; weights: https://huggingface.co/openbmb/VoxCPM-0.5B
- IndexTTS: https://github.com/index-tts/index-tts ; license: https://huggingface.co/spaces/IndexTeam/IndexTTS-2-Demo/blob/main/INDEX_MODEL_LICENSE_EN.txt ; license discussion: https://github.com/index-tts/index-tts/issues/228
- F5-TTS commercial restrictions: https://github.com/SWivid/F5-TTS/discussions/997
- duration alignment: WhisperX https://github.com/m-bain/whisperX ; automatic dubbing duration modeling https://arxiv.org/html/2211.16934v2 ; speech translation to automatic dubbing https://aclanthology.org/2020.iwslt-1.31/ ; ffmpeg atempo https://ayosec.github.io/ffmpeg-filters-docs/7.1/Filters/Audio/atempo.html
- self-hosted OpenAI shim pattern: https://github.com/matatonic/openedai-speech
