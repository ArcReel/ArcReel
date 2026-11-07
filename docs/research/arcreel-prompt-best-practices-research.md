# Official image/video prompting best-practice research

> Purpose: inform ArcReel novel→short-video i2v script-writing guidance for LLM `image_prompt` / `video_prompt` output.
> Issue:<https://github.com/ArcReel/ArcReel/issues/1092>; design-alignment decisions in comments.
> Scope:eight integrated providers; **official** docs/blogs/model cards/Help Centers only. Missing official guidance stays missing, never replaced by tutorials/model recollection.
> Research date:2026-07-13.
> Every finding has official URLs; unstated points are labeled not found/not explicit. Clearly unverified third-party mirrors appear only as side notes, never conclusions.

---

## 1. Cross-provider findings by four research questions

### Q1 General image-prompt guidance

**Consensus 1: layered elements are widespread and similar**, usually subject→environment→lighting→shot/angle/camera/lens→style/mood.
- Google template `景别 + 主体 + 环境 + 光线 + 机位 + 镜头`, <https://ai.google.dev/gemini-api/docs/image-generation>.
- Wan advanced `主体 + 场景 + 风格 + 镜头语言 + 氛围词 + 细节修饰`, plus shot/angle/lens/style/light dictionaries, <https://help.aliyun.com/zh/model-studio/text-to-image-prompt>.
- Seedream `主体 + 行为 + 环境`, optional style/color/light/composition, <https://www.volcengine.com/docs/82379/1829186?lang=zh>.
- MiniMax image-01 `主体+服装 → 景别/视角 → 环境 → 摄影风格/年代 → 介质/质感 → 写实度`, <https://platform.minimax.io/docs/api-reference/image-generation-t2i>.
- GPT-image fixed `场景/背景 → 主体 → 关键细节 → 约束`, specify purpose, <https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide>.
- Kling MVL natural language+images, scene-direction writing, facial-reference `面部特征+发型+服装+动作+背景+光线+氛围`, <https://kling.ai/quickstart/klingai-image-3-model-user-guide>.

**Consensus 2: concrete/visual descriptions beat vague quality-word piles.** Providers repeatedly replace abstractions with visible details.
- Google: `Be specific: More details give you more control`; "fantasy armor" → "ornate elven plate armor, etched with silver leaf patterns…" (<https://cloud.google.com/gemini-enterprise-agent-platform/models/capabilities/gemini-image-generation-best-practices>).
- Alibaba `提示词描述越完整、精确和丰富，生成的图像品质越高`, <https://help.aliyun.com/zh/model-studio/text-to-image-prompt>.
- Kling: replace "magic" with "swirling blue energy particles with an ethereal glow" (<https://kling.ai/blog/kling-ai-prompt-guide>).
- Vidu references: `cinematic dramatic beautiful high quality` is model noise; use actionable constraints, <https://www.vidu.com/blog/image-prompt-generator-ai-video>.

**Consensus 3: narrative natural sentences over keywords/tags.**
- Seedream contrasts a richly clothed girl holding a parasol on a tree-lined avenue in Monet oil-painting style with fragmented girl/umbrella/tree-lined-street/delicate-oil-brushstroke keywords, <https://www.volcengine.com/docs/82379/1829186?lang=zh>.
- Google/xAI/MiniMax examples are full sentences; sources above and <https://docs.x.ai/developers/model-capabilities/images/generation>.
- OpenAI is neutral: minimal/narrative/JSON/instructions/tags all work with clear intent/constraints, <https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide>.

**Consensus 4: almost no quantitative length guidance.** Specificity is qualitative.
- None found for Google/Seedream/MiniMax/Kling/OpenAI, excerpts below.
- OpenAI says `Long prompts can work well`, GPT-image-2 structured descriptions; qwen-image-2.0-pro examples use hundreds of detailed structured characters, <https://help.aliyun.com/zh/model-studio/text-to-image>.
- Vidu Q3's alleged50–150-character guide exists only on CSDN/gitcode mirrors without official originals; **excluded**, divergence5.

> **ArcReel implications, facts not rewrites**: layered/concrete/narrative descriptions and avoiding empty quality words have broad support. Excess brevity, such as missing scene lighting/mood, conflicts with that. Subject/environment/light/camera/mood completeness has cross-provider evidence; lengths mostly favor specificity where needed, not hard numbers.

---

### Q2 i2v guidance with supplied first frames

**Consensus 1, strongest: first frames already supply subjects/scenes/styles; prioritize motion/change/camera rather than repeating static visuals, adding environmental dynamics/style transitions where necessary.** Directly relevant to ArcReel i2v.
- Google: `Prompt for motion only. Your source image already provides the subject, scene, and style. Focus your prompt on the motion…`; explicitly avoid redescribing characters/background/light because redundancy confuses the model. Refer to characters generically as "the subject/the woman/he/she" (<https://cloud.google.com/vertex-ai/generative-ai/docs/video/best-practice>).
- Wan `图像已经确定了主体、场景与风格，因此提示词主要描述动态过程及运镜需求`, formula `运动 + 运镜`, <https://help.aliyun.com/zh/model-studio/text-to-video-prompt>.
- MiniMax `首帧主体 + 运动/变化`; `prompt describes how the scene evolves from this static image into motion`, <https://platform.minimax.io/docs/guides/video-prompt>,<https://platform.minimax.io/docs/guides/video-generation>.
- Vidu `subject motion + camera movement + scene change + style`, not restatement, <https://www.vidu.com/ai-image-to-video>.
- Sora `The model uses the image as an anchor for the first frame, while your text prompt defines what happens next`, <https://developers.openai.com/cookbook/examples/sora/sora2_prompting_guide>.
- Kling example: camera pulls out, woman smiles; image supplies scene, <https://www.klingai.com/document-api/api/video/3-0-omni/image-to-video>.
- Seedance Character-motion prompts combine subject + detailed action changes from the first frame to the last + camera movement, <https://www.volcengine.com/docs/82379/1951250?lang=zh>.
- xAI weak signal `Give it a starting image, describe the motion…`, motion/camera examples, <https://x.ai/news/grok-imagine-video-1-5>.

**Consensus 2: explicit camera motion, preferably one kind**, with official vocabularies.
- MiniMax: the i2v API provides 15 `[command]` bracketed camera instructions ([Push in]/[Pull out]/[Pan left]…/[Static shot], etc.) for Hailuo 2.3 / 2.3-Fast; at most 3 commands per [] are recommended (<https://platform.minimax.io/docs/api-reference/video-generation-i2v>).
- Kling natural push-in/pan/tilt/tracking plus six-axis `camera_control`, horizontal/vertical/pan/tilt/roll/zoom,[-10,10], <https://kling.ai/blog/kling-ai-prompt-guide>,<https://www.klingai.com/document-api/api/video/3-0-omni/image-to-video>.
- Alibaba push/left-shift or fixed-camera; wan2.7 replaced `shot_type` with natural language, <https://help.aliyun.com/zh/model-studio/text-to-video-prompt>.
- Seedance 2.0 `一个镜头里尽量只指定 1 种运镜方式，不要同时要求推拉摇移`, <https://www.volcengine.com/docs/82379/2222480?lang=zh>.
- Vidu directional/subtle/gentle/minimal motion, avoid simultaneous instructions in one sentence, <https://www.vidu.com/blog/image-to-video-ai>.

**Consensus 3: match action volume to duration, one focused achievable short-shot action. Slow/gentle/continuous motion is provider-specific, not universal. Duration comes from APIs, not prose.**
- Seedance 2.0 `优先选用缓慢、轻柔、连贯的细微动作，尽量规避狂奔、大跳、剧烈翻滚等高爆发、大动态动作`; exact0–3s timing unstable, prefer ordered shot1/2/3 events, <https://www.volcengine.com/docs/82379/2222480?lang=zh>.
- Google `For short videos, dedicate each prompt to a single, focused moment`, not A→B→C chains, <https://cloud.google.com/vertex-ai/generative-ai/docs/video/best-practice>.
- OpenAI: actions should be simple, single, and achievable, with beats measured in seconds ("takes four steps… in the final second"). Duration/resolution are container parameters that `will not change based on prose like 'make it longer'`; a 4-second shot accommodates about 1–2 sentences of dialogue (<https://developers.openai.com/cookbook/examples/sora/sora2_prompting_guide>).
- Vidu most stable 4–6s;8s+ complex images drift; minimal face-heavy motion, <https://www.vidu.com/blog/image-to-video-ai>.
- MiniMax camera moves around 5–6s, <https://platform.minimax.io/docs/guides/video-prompt>.

**Consensus 4: most sites clearly separate t2v/i2v.** Google/Alibaba/MiniMax/Seedance/Vidu/Kling have identifiable i2v guidance; xAI only one blog sentence.

> **ArcReel implications**: actions/camera within shot duration match focused achievable movement and explicit camera guidance. Slow/continuous is provider-specific; environmental sound requires explicit support, neither universal. Assess nine-second/28-character sparse actions against motion+camera+(environment/mood dynamics), without repeating static scenes or piling simultaneous violent actions.

---

### Q3 Official handling of negation

Largest divergence, three approaches:

**A: no separate negative channels; positive descriptions replace prohibitions.**
- Google images (Gemini/Nano Banana, exactly ArcReel’s first-frame use): literal guidance `Describe what you want, not what you don't`; the example rewrites "no cars" as "an empty, deserted street with no signs of traffic" (<https://cloud.google.com/gemini-enterprise-agent-platform/models/capabilities/gemini-image-generation-best-practices>).
- Vidu: all generation interfaces lack `negative_prompt`. Official guidance favors positive, concrete, restrained descriptions + explicit statements of what to preserve or keep still (e.g. "background remains static") (<https://platform.vidu.com/docs/image-to-video>, <https://www.vidu.com/blog/image-prompt-generator-ai-video>).
- MiniMax inspected image/i2v/s2v schemas have no negatives, positive accumulation, <https://platform.minimax.io/docs/api-reference/video-generation-i2v>.
- Sora no negative argument; unspecified details invented, so describe positively, <https://developers.openai.com/cookbook/examples/sora/sora2_prompting_guide>,<https://developers.openai.com/api/reference/resources/videos/methods/create>.

**B: separate negative channels, but descriptive exclusions rather than no/don't, often supplementing positives.**
- Google video (Veo): separate `negativePrompt`, but official guidance asks for excluded entity nouns ("wall, frame"), not "no walls", explicitly stating `Not recommended: using instructive language or words such as "no" or "don't"` (<https://cloud.google.com/vertex-ai/generative-ai/docs/video/video-gen-prompt-guide>).
- Kling `negative_prompt`≤2500 characters, additionally recommends negative sentences inside positive prompts, <https://www.klingai.com/document-api/api/video/3-0-omni/image-to-video>.
- Alibaba **splits by model**: qwen-image/wan2.7 video **support** `negative_prompt`≤500, auxiliary quality optimization; wan2.7-image-pro/image **do not**, instructing exclusions in positive prose, <https://help.aliyun.com/zh/model-studio/text-to-image>,<https://help.aliyun.com/zh/model-studio/text-to-video-api-reference>.

**C: explicitly encourage exclusion/invariant sentences in prompt bodies.**
- Seedance 2.0: a dedicated constraint-words section gives examples “keep subtitles absent / avoid generating any text or subtitles / do not generate Logo / do not generate watermarks”, even “throughout the video, prohibit … identical duplicates or twin effects”. It acknowledges “cannot avoid them 100%, only reduce the probability” (<https://www.volcengine.com/docs/82379/2222480?lang=zh>).
- OpenAI images (GPT-image): officially **encourages** explicit exclusions/invariants, `State exclusions and invariants explicitly (e.g., 'no watermark,' 'no extra text,' 'no logos')`; for edits, "change only X + keep everything else the same" (<https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide>).

**Shared limit: negatives never guarantee control.** Seedance says so; Google/Vidu favor positive rewrites or descriptive exclusions. xAI gives neither channels nor guidance.

> **Evidence for three frozen ArcReel decisions, not rewrites**:
> - Past/future-event prohibitions face split evidence: positive image guidance versus Seedance/OpenAI image exclusions. No provider exposes narrative chronology/flashbacks as controllable; single stills cannot render chronology, independent of negatives.
> - Word-family blacklists have no official precedent. Describe desired visuals or excluded entity nouns instead; no guidance supports enumerated lexical blocking.
> - Retaining negatives depends on models: Gemini/Seedream favor positive rewriting; Kling/qwen-image/wan2.7 video offer separate channels, still preferably descriptive.

---

### Q4 Prompt language: Chinese versus English

**Almost no official source says one language works better.** Multilingual support is common; authoring-language recommendations are absent.
- Alibaba supports Chinese/English with paired examples, HappyHorse any input language, <https://help.aliyun.com/zh/model-studio/text-to-video-api-reference>,<https://help.aliyun.com/zh/model-studio/happyhorse-image-to-video-api-reference>; Seedance Chinese/English, <https://www.volcengine.com/docs/82379/2168087?lang=zh>; MiniMax/Kling/Vidu bilingual docs/examples without superiority claims.
- Only old Imagen explicitly `Imagen models support only English natively`, translating others, selectable zh/zh-CN/zh-TW. **Deprecated line, migration deadline passed**: move to gemini-2.5-flash-image before 2026-06-30. No Gemini 3/Veo superiority claims, <https://cloud.google.com/vertex-ai/generative-ai/docs/image/set-text-prompt-language>.
- OpenAI general Help, not image/Sora-specific: `optimized for English, but trained on multilingual data`; target languages possible without comparative conclusions, <https://help.openai.com/en/articles/6742369>.
- Distinguish **generated speech language**: Kling Chinese/English/Japanese/Korean/Spanish, <https://kling.ai/blog/kling-ai-prompt-guide>; ViduQ3 English/Japanese/Chinese, <https://www.vidu.com/vidu-q3>; Seedance 2.0 consistent dialogue language, avoid mixed Chinese/English, <https://www.volcengine.com/docs/82379/2222480?lang=zh>. These are output, not prompt-language advice.

> **ArcReel implications**: no citable official better-language conclusion. Chinese vendors provide extensive Chinese docs/examples; overseas examples mostly English without mandates. Prompt language remains an independent product decision without hard official evidence.

---

## 2. Provider excerpts: official sources and findings

### 1. Google: Gemini 3 Pro/3.1 Flash Image, Veo 3.1

Official sources:
- Image prompting:<https://ai.google.dev/gemini-api/docs/image-generation>
- Image best practices:<https://cloud.google.com/gemini-enterprise-agent-platform/models/capabilities/gemini-image-generation-best-practices>
- Legacy Imagen language:<https://cloud.google.com/vertex-ai/generative-ai/docs/image/set-text-prompt-language>
- Veo prompts:<https://cloud.google.com/vertex-ai/generative-ai/docs/video/video-gen-prompt-guide>
- Veo best practices, separate i2v:<https://cloud.google.com/vertex-ai/generative-ai/docs/video/best-practice>
- Veo 3.1 blog:<https://cloud.google.com/blog/products/ai-machine-learning/ultimate-prompting-guide-for-veo-3-1>

Key points: images combine layered elements, concrete details, and narrative sentences, "more details = more control", without numerical length guidance. Official i2v guidance has its own section: describe motion only, do not restate the image, use generic character references; motion is classified as Camera/Subject/Environmental. Negatives: **images have no negative channel, use positives only**; **video has `negativePrompt` but requires excluded entity nouns, avoiding no/don't**. Language: only legacy Imagen explicitly documents native English + translation; newer lines make no superiority claim.
Not found: image length numbers, Gemini 3/Veo language superiority, quantified duration/action mappings.

### 2. Volcengine Ark/Doubao: Seedream 4.0/4.5/5.0, Seedance 1.5 Pro/2.0

Official sources:
- Seedream images:<https://www.volcengine.com/docs/82379/1829186?lang=zh>
- Seedance 1.5 Pro:<https://www.volcengine.com/docs/82379/2168087?lang=zh>
- Seedance 2.0:<https://www.volcengine.com/docs/82379/2222480?lang=zh>
- Seedance 2.0 first/last/role tutorial:<https://www.volcengine.com/docs/82379/2291680?lang=zh>
- Seedream→Seedance i2v:<https://www.volcengine.com/docs/82379/1951250?lang=zh>

Image `主体+行为+环境`, optional aesthetics; sentences beat tags, with explicit examples; newer models need less wording, concise precision over ornament. i2v `主体 + 首帧到尾帧动作变化 + 运镜`, brief camera descriptions.2.0 favors gentle continuous microactions over explosive movement, one camera movement per shot, shot1/2/3 instead of seconds. **Body constraints**, no subtitles/logos/twins, not100% reliable. Chinese/English supported, no superiority; consistent dialogue language.
Not found: explicit image-language superiority claims; a separately named "Seedance 2.0 Pro" guide (official names are 2.0/2.0 Fast/2.0 Mini).
Note: JS-rendered documentation read through browsers.

### 3. xAI: Grok Imagine Image/Video

Official sources checked, no dedicated guide:
- Imagine:<https://docs.x.ai/developers/model-capabilities/imagine>
- Images:<https://docs.x.ai/developers/model-capabilities/images/generation>
- i2v:<https://docs.x.ai/developers/model-capabilities/video/image-to-video>
- Video:<https://docs.x.ai/developers/model-capabilities/video/generation>
- Video 1.5 blog:<https://x.ai/news/grok-imagine-video-1-5>

**No dedicated prompt-writing guide**, docs are APIs. Only `Give it a starting image, describe the motion…`, narrative English motion/camera/mood examples, and duration1–15s/ratio/resolution constraints. **No negative channel/guidance; no language advice**, English examples only. Exclude third-party formulas/negative recipes.

### 4. OpenAI: GPT-image-2, Sora 2/2 Pro

Official sources:
- Image prompting:<https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide>
- Image APIs:<https://developers.openai.com/api/docs/guides/image-generation>
- Sora 2 prompting:<https://developers.openai.com/cookbook/examples/sora/sora2_prompting_guide>
- Video generation:<https://developers.openai.com/api/docs/guides/video-generation>
- Create video:<https://developers.openai.com/api/reference/resources/videos/methods/create>
- General multilingual Help:<https://help.openai.com/en/articles/6742369>

Key points: images follow `场景→主体→细节→约束` + stated purpose; minimal/narrative/JSON/tag forms all work. Long prompts are viable, but production favors clear templates + incremental iteration. i2v uses the image as first-frame anchor; prompts define what happens next (`input_reference` supplies it). Keep actions simple and give second-based beats; **short prompts = more creative freedom, long prompts = stronger control**. Duration is a container parameter prose cannot change, up to 20s. Negatives: **official image guidance encourages explicit no-watermark/no-logo exclusions**; Sora uses positive descriptions with no negative parameter. Language: only generic Help Center English-optimization + multilingual guidance, no GPT-image/Sora-specific Chinese-vs-English conclusion.

### 5. Vidu: Q1/Q2 images, Q3 turbo/pro/2.0 videos

Official sources:
- i2v:<https://platform.vidu.com/docs/image-to-video>
- Reference-to-image:<https://platform.vidu.com/docs/reference-to-image>
- Motion blog:<https://www.vidu.com/ai-image-to-video>
- Image workflow:<https://www.vidu.com/blog/image-prompt-generator-ai-video>
- i2v practice:<https://www.vidu.com/blog/image-to-video-ai>
- Q3:<https://www.vidu.com/vidu-q3>

Key points: Vidu has **no centralized official Prompt Guide**; API model cards and official blogs carry its guidance. Images used as video references should specify face/clothing/light direction, avoiding meaningless quality-word piles ("model noise"). i2v formula `subject motion + camera movement + scene change + style`: describe movement rather than restating the image, keep prompts simple and focused, avoid simultaneous actions; 4–6s is most stable, face-heavy images use minimal motion. Negatives: **no negative channel**, use positives + explicitly stationary elements. Both Chinese and English sites offer first-class support without superiority claims (Q3 multilingual means audio output). Parameter caveat: `movement_amplitude` is ineffective on Q2/Q3; control motion amplitude with prompt text.
Unverified note: alleged Chinese Q3 official formulas/50–150 characters/end-position sounds only CSDN/gitcode, no original, excluded.

### 6. Alibaba DashScope/Bailian: Qwen-image 2.0, wan2.7, HappyHorse 1.0

Official sources:
- Image prompts:<https://help.aliyun.com/zh/model-studio/text-to-image-prompt>
- t2v/i2v prompts:<https://help.aliyun.com/zh/model-studio/text-to-video-prompt>
- Qwen/Wan images:<https://help.aliyun.com/zh/model-studio/text-to-image>
- Wan2.7 image API:<https://help.aliyun.com/zh/model-studio/wan-image-generation-and-editing-api-reference>
- Wan2.7 t2v:<https://help.aliyun.com/zh/model-studio/text-to-video-api-reference>
- Wan2.7 i2v:<https://help.aliyun.com/zh/model-studio/image-to-video-general-api-reference>
- HappyHorse i2v:<https://help.aliyun.com/zh/model-studio/happyhorse-image-to-video-api-reference>

Basic `主体+场景+风格`, advanced `+镜头语言+氛围+细节`, five-dimensional dictionaries, completeness/precision/richness; qwen-image-2.0-pro long structured examples. i2v `运动+运镜`, images supply static elements, fixed-camera if unchanged; wan2.7 removes shot_type for natural language, `duration`2–15s, first-frame ratio. **Negatives vary**: qwen-image/wan2.7 video `negative_prompt` auxiliary; wan2.7-image-pro/image unsupported, body prohibitions; HappyHorse none. Chinese/English paired examples/any HappyHorse language, **no official Chinese-optimized/better claim**.

### 7. MiniMax: image-01, Hailuo 2.3/Fast, S2V-01

Official sources:
- Images:<https://platform.minimax.io/docs/guides/image-generation>
- t2i:<https://platform.minimax.io/docs/api-reference/image-generation-t2i>
- Video:<https://platform.minimax.io/docs/guides/video-generation>
- i2v/15 camera commands:<https://platform.minimax.io/docs/api-reference/video-generation-i2v>
- S2V:<https://platform.minimax.io/docs/api-reference/video-generation-s2v>
- Hailuo tutorial:<https://platform.minimax.io/docs/guides/video-prompt>

Images layer subject/shot/environment/style/medium/realism; image-01≤1500 characters, image `prompt_optimizer` false. i2v basic `首帧主体+运动/变化`, precise `+运镜+美学氛围`; **15 `[command]` bracket instructions**, Hailuo 2.3,≤3 per bracket;5–6s moves; i2v≤2000 characters, video `prompt_optimizer` true. **No negative channel, positive layering**; only prohibition is sensitive-content 1026. Bilingual, no superiority claims.

### 8. Kling: ImageO1/v3 Omni Image; Videov2.5T/v2.6/v3/v3 Omni/O1

Official sources:
- Video prompts:<https://kling.ai/blog/kling-ai-prompt-guide>
- Image 3.0 handbook:<https://kling.ai/quickstart/klingai-image-3-model-user-guide>
- Image 3.0 Omni:<https://kling.ai/quickstart/klingai-image-3-omni-user-guide>
- 3.0/Omni i2v:<https://www.klingai.com/document-api/api/video/3-0-omni/image-to-video>
- Quickstarts:<https://kling.ai/quickstart>
- Adjectives:<https://kling.ai/blog/best-kling-ai-adjectives-video-prompts>

Key points: official guidance favors clear scene-direction writing rather than secret formulas. Images use MVL (natural language + images), with task-specific formulas: edits “keep everything else unchanged, change X to Y”; face references include face/hair/clothing/action/background/light/mood. Make subjects concrete and visualizable. Six video elements: `主体+动作+场景+镜头+光影氛围(+对白)`. Camera control has two tracks: natural-language vocabulary + six-axis `camera_control`. Set pacing/duration per shot through `multi_prompt` (shot durations sum to the total, ≤6 shots). i2v: first-frame `image` (optional last frame), with prompts adding action/camera motion; official example “camera pulls out, girl smiles”. Negatives: **`negative_prompt` (≤2500), but official guidance recommends supplementing through negative sentences inside positive prompts**. Chinese/English examples coexist without superiority claims; dialogue supports Chinese/English/Japanese/Korean/Spanish, 5 languages (output language).
Note: no per-model writing differences; alleged subject+motion+background i2v formula absent from official pages, excluded.

---

## 3. Divergences and exceptions

1. **Three negative strategies, largest split**:
   - Positive only/no channel: Google images/Vidu/MiniMax/Sora.
   - Descriptive channels: Veo `negativePrompt`,Kling `negative_prompt`,Alibaba qwen-image/wan2.7 video, not wan2.7-image.
   - Body exclusions: Seedance constraints/GPT-image `no watermark/no logos`.
   - No guidance: xAI.
   → **Even providers split internally**: Google image/video, Alibaba models. ArcReel cannot apply one negative strategy universally.

2. **Detail direction differs**: most favor detail/control; **Sora uniquely treats shorter creative freedom versus longer control/lower creativity as two-way tradeoff**, <https://developers.openai.com/cookbook/examples/sora/sora2_prompting_guide>.

3. **Sentence preference differs**: Seedream strongest positive/negative examples; OpenAI neutral toward tags/JSON.

4. **i2v coverage varies**: Alibaba/Seedance/MiniMax/Google/Kling/Vidu usable; **xAI one sentence only**. OpenAI's dedicated first-frame section is one paragraph; motion/camera/duration otherwise shared with t2v.

5. **Alleged ViduQ3 official guide uncertain**: Chinese50–150-character/sound-at-end/phased-i2v templates attributed to Vidu API exist only mirrors, **no platform.vidu.cn/.com/vidu.com/blog original located**. Excluded; verify originals before citing numerical advice.

6. **No language-superiority exception** among eight. Only deprecated Imagen documents mechanisms; popular Chinese-optimization claims lack official statements, leaving ArcReel independent choice.

7. **Parameter traps**:
   - Vidu `movement_amplitude` ineffective on Q2/Q3; textual motion, <https://platform.vidu.com/docs/image-to-video>.
   - wan2.7 removed `shot_type`; natural cameras, <https://help.aliyun.com/zh/model-studio/text-to-video-prompt>.
   - MiniMax `prompt_optimizer` image false/video true, opposite rewrite defaults, <https://platform.minimax.io/docs/api-reference/video-generation-i2v>.
   - Alibaba `prompt_extend` true, but wan2.7-image replaces it with `thinking_mode`, <https://help.aliyun.com/zh/model-studio/text-to-image>.

---

## 4. Methods and constraints

- Only official docs/blogs/cards/Help; exclude tutorials/community/mirrors, except clearly labeled unverified Vidu side notes outside conclusions.
- Seedance/MiniMax Chinese/some Alibaba/Vidu pages are JS-rendered; `web_fetch` returns navigation, so browser-rendered bodies read. Prefer fuller English MiniMax docs, consistent with Chinese.
- Missing official statements remain labeled missing/not explicit; never fill provider claims from model memory.
- No ArcReel guidance rewrites here: deliver evidence/common findings; aligned main-repository sessions plan changes separately.
