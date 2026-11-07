# Asset-image prompt research: multi-panel composition, i2i reference side effects, anatomy safeguards, and negative tails

> Purpose: first-party evidence for [#2058](https://github.com/ArcReel/ArcReel/issues/2058), “asset-image prompt template optimization”, covering three internal-text families in `lib/prompts/prompt_builders.py`: `_CHARACTER_LAYOUT` / `_SCENE_LAYOUT` / `_PROP_LAYOUT` / `_PRODUCT_LAYOUT`, `_*_GUARD`, and `_NEGATIVE_TAIL_*`.
> Scope: five question blocks: A official guidance on multi-panel / multi-view / sheet composition; B side effects of multi-panel collages as i2i references; C effectiveness of positive anatomy safeguards and textual negative tails; D reference-image content as conditioning input (framing / angle / pose / background / image count); E aspect ratio. D and E were targeted additions on 2026-08-24 to answer what a single image should depict and at what ratio.
> Source discipline: only official documentation / model cards / vendor cookbooks / API references, arXiv, and peer-reviewed papers. Secondary blogs, forums, and aggregated tutorials do not support conclusions; mention them only as community lore without official endorsement, labeled accordingly. Every conclusion has a source URL and publication/update date; missing dates are labeled “date not stated”. Missing official statements are explicitly labeled, without speculative substitutes.
> Retrieval method: several vendor sites are client-rendered or return 403 directly (`docs.midjourney.com`, `www.volcengine.com/docs`, `docs.byteplus.com`, `platform.minimax.io`). Their bodies were retrieved through a rendering proxy; quotations and URLs refer to official original pages, without secondary retellings.
> Research date: 2026-08-24
> Relation to existing research: incremental to `arcreel-prompt-best-practices-research.md` (2026-07-13), without repeating Q1/Q2/Q4. The first half of C cites and refines Q3; A/B/D/E cover new ground. See section 7.

---

## 1. Block A: official guidance on multi-panel / multi-view / sheet composition

### 1.1 Conclusion matrix

| Vendor | Official recommendation for multiple panels/views in one image | Official consistency approach | Official panel-count/consistency statements | Character-sheet use case |
| --- | --- | --- | --- | --- |
| Google Gemini (Nano Banana family) | **Partial, narrative panels only**: prompting-guide section 6, Sequential art (comic panel / storyboard), gives “Make a 3 panel comic in a [style]”. **Same-subject views explicitly take the opposite approach**: section 7, Character consistency: 360 view, requires iterative generation by angle, not one-image layout | **Iterative generation + multiple references**: 360 views use “iteratively prompting for different angles”, feeding generated images back as references. Gemini 3 Pro allows 6 object + 5 character + 3 style images; 3.1 Flash 10 object + 4 character; 3.1 Flash Lite 14 object, no character channel; total maximum 14 | Explicit limit: “The model might not create the exact number of images you ask for”; 2.5 Flash Image recommends at most 3 input images, 3 Pro Image at most 14 | No “character sheet” wording found; equivalent official use case is 360 view, with separate images per angle |
| OpenAI gpt-image | **Recommended**, narrative panels only: cookbook Story-to-Comic Strip example uses “4 equal-sized panels” in one image | Describe each panel within one image + refer to multiple inputs by index | “one per panel”; no maximum or consistency guarantee | Not found (no character sheet / multi-view / turnaround wording) |
| Black Forest Labs FLUX.2 | **Explicit opposite**: official multi-panel section requires separate generation per panel | “Generate each panel separately while keeping character descriptions consistent”; repeat character descriptions in each panel prompt | “Repeat these details in every panel prompt” | Not found (multiple references support character consistency across variations) |
| ByteDance Seedream 4.0/4.5 (Volcengine Ark / BytePlus ModelArk) | **Explicit opposite**: multi-view/set requirements belong to multiple-image output | “Multi-image output”: trigger sequences with “a series”, “a set”, or an explicit count; official examples name storyboarding, comic creation, IP product design, emoji pack | Technical report: “image sequences that remain both character-consistent and stylistically aligned” | No single-image character-sheet statement; set-based design uses multiple outputs |
| Alibaba Wan / Qwen-Image (DashScope) | Not found | Official guide only gives subject+scene+style+camera language+mood+detail modifiers and a five-dimensional vocabulary | Not found | No official statement found |
| MiniMax image-01 | Not found | `subject_reference` (`type: "character"`) reference channel, see i2i endpoint | Not found | No official statement found |
| Midjourney | No multi-panel/sheet composition statement found | Omni Reference (`--oref`/`--ow`, one image only), Character Reference (`--cref`/`--cw`, replaced by Omni Reference since V7) | Not found | No official statement found; Character Reference best practices instead require a **single character** |
| Dreamina | Not found | Not found | Not found | No official statement found |

**Cross-vendor fact**: among all vendors whose documentation bodies were obtained, **none officially recommends arranging multiple views of one subject in one image**. Official consistency approaches use multiple references (Google, Midjourney, MiniMax, BFL) or grouped multi-image generation (Seedream). Three official single-image multi-panel examples (OpenAI comic strip, BFL comic strip, Google 3 panel comic) all concern **narrative panels**, never same-subject views; BFL generates panels separately. Google provides stronger contrary evidence: its dedicated 360 view section generates one angle at a time and reuses prior images as references, rather than arranging views in one image.

### 1.2 Individual evidence

**Google Gemini: consistency is a multiple-reference problem; requested counts are not guaranteed.**
Reference quotas (official per-model table): “Up to 14 reference images: You can now mix up to 14 reference images to produce the final image.” Gemini 3.1 Flash Lite Image accepts 14 object images, with character/style channels N/A; Gemini 3.1 Flash Image 10 object + 4 character; Gemini 3 Pro Image 6 object + 5 character + 3 style (character-column wording: “Up to N images of characters to maintain character consistency”).

Prompting-guide section 7 is titled “Character consistency: 360 view”: “You can generate 360-degree views of a character by iteratively prompting for different angles. For best results, include previously generated images in subsequent prompts to maintain consistency. For complex poses, include a reference image of the selected pose.” Template: “A studio portrait of [person] against [background], [looking forward/in profile looking right/etc.]”. Thus **one angle and one output at a time**; official example: “A studio portrait of this man against white, in profile looking right”.

Section 6, “Sequential art (comic panel / storyboard)”, is the only official single-image multi-panel template: “Make a 3 panel comic in a [style]. Put the character in a [type of scene].” Official note: “For accuracy with text and storytelling ability, these prompts work best with Gemini 3 Pro and Gemini 3.1 Flash Image.” Narrative panels, not same-subject views. Full-page search for `character sheet / turnaround / multiple views` returned no matches.
Source: https://ai.google.dev/gemini-api/docs/image-generation （Last updated 2026-08-10 UTC）

Official limitations: “The model might not create the exact number of images you ask for.” “For best results using Gemini 2.5 Flash Image, include a maximum of three images in an input. For best results using Gemini 3 Pro Image, include a maximum of 14 images in an input.”
Source: https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/capabilities/gemini-image-generation-limitations （Last updated 2026-08-21 UTC）

None of the seven official best practices addresses multi-panel composition; character drift is addressed by “restart a new conversation with a detailed description”.
Source: https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/capabilities/gemini-image-generation-best-practices （Last updated 2026-08-21 UTC）；https://developers.googleblog.com/en/how-to-prompt-gemini-2-5-flash-image-generation-for-the-best-results/ （2025-08-28）

**OpenAI: the only vendor with a complete official single-image multi-panel example, for narrative panels.**
Capability: “Complex structured visuals, including infographics, diagrams, and multi-panel compositions”.
Section 4.7: “For story-to-comic generation, define the narrative as a sequence of clear visual beats, one per panel. Keep descriptions concrete and action-focused so the model can translate the story into readable, well-paced panels.” Example opens “Create a short vertical comic-style reel with 4 equal-sized panels.”, then describes Panel 1–4.
Full-page searches for `multi-view / character sheet / turnaround / three-view / contact sheet / collage` found no matches.
Source: https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide （2026-04-21）

**Black Forest Labs: official multi-panel guidance generates each panel separately.**
“Comic Strips and Sequential Art — Create consistent comic panels with character continuity. The key is to define your character in detail and maintain that description across panels.” “Generate each panel separately while keeping character descriptions consistent”, ending the example with “Character Consistency: Notice how Diffusion Man's description stays detailed and consistent across panels... Repeat these details in every panel prompt.”
Multiple references are a separate channel: “[pro] API has a 9MP total limit for input+output. At 1MP output you can use up to 8 reference images, at 2MP output up to 7, and so on.” Official applications: fashion shoots / interior design / product composites / character consistency (Maintain identity across variations).
Source: https://docs.bfl.ai/guides/prompting_guide_flux2 （date not stated）

**Seedream: set/multi-view requirements explicitly use multi-image output, not single-image panels.**
Official “Multi-image output” guide: “Seedream 4.5 and 4.0 support generating image sequences with consistent character continuity and unified style, making it suitable for storyboarding, comic creation, and set-based design scenarios that require a cohesive visual identity, such as IP product design or emoji pack creation.” “When generating multiple images, you can trigger series generation with phrases like "a series", "a set", or by specifying the number of images.”
“Reference-based generation” requires two explicit components: “Reference Target: Clearly describe the elements to be extracted and retained from the reference image” and “Generated Scene Description: Provide detailed information about the desired generated content, including scene, layout, and other specifics.”
Source: https://docs.byteplus.com/en/docs/ModelArk/1829186 （Last updated 2026-07-06；Chinese page from the same source: https://www.volcengine.com/docs/82379/1829186?lang=zh ）

Technical-report corroboration: “Leveraging strong capabilities in global planning and in-context consistency, Seedream 4.0 supports the generation of image sequences that remain both character-consistent and stylistically aligned.”
Source: https://arxiv.org/html/2509.20427v2 （arXiv:2509.20427，2025-09）

**Alibaba DashScope: no relevant official prompt guidance.**
The `text-to-image-prompt` page gives basic/advanced formulas and five vocabulary dimensions (framing, angle, lens type, style, lighting), without multi-panel, three-view, character-sheet, or collage discussion.
Source: https://help.aliyun.com/zh/model-studio/text-to-image-prompt （page has an update-time field but its retrieved date was empty; date not stated）

**MiniMax image-01: no multi-panel guidance found; reference channel exists.**
t2i parameters: `model / prompt / aspect_ratio / width / height / response_format / seed / n / prompt_optimizer`, no negative channel or multi-panel instructions. The official i2i example includes `subject_reference: [{ "type": "character", "image_file": ... }]`.
Source: https://platform.minimax.io/docs/api-reference/image-generation-t2i 、https://platform.minimax.io/docs/api-reference/image-generation-i2i （date not stated）

**Midjourney: no multi-panel/sheet statements found; reference mechanisms are documented (details in B).**
Source: https://docs.midjourney.com/hc/en-us/articles/32040250122381-Image-Prompts 、https://docs.midjourney.com/hc/en-us/articles/36285124473997-Omni-Reference 、https://docs.midjourney.com/hc/en-us/articles/32162917505293-Character-Reference （all dates not stated）

**Dreamina: no official prompt-guide-level content found.**
`jimeng.jianying.com/features/resource/*` contains generic official educational/marketing how-to pages, with broad advice such as avoiding ambiguous words, without multi-panel composition, reference selection, negation, or anatomy guidance. On ModelArk, only Dreamina Seedance video tutorials were found (https://docs.byteplus.com/en/docs/modelark/2291680 ), without a separate image prompt guide.
Source: https://jimeng.jianying.com/features/resource/text-to-image-prompts （date not stated）

---

## 2. Block B: side effects of multi-panel collages as i2i references

### 2.1 Official evidence: composition inheritance is an explicitly documented reference behavior

**Midjourney explicitly names composition among three image-prompt influences.**
Image Prompts opens: “Want to influence the content, composition, and colors of your Midjourney creations? Include an image as part of your prompt!” `--iw` controls strength (V7 default 1, range 0–3). The same page gives a collage-related fact: multiple images without text are blended.
Source: https://docs.midjourney.com/hc/en-us/articles/32040250122381-Image-Prompts （date not stated）

**Midjourney character-reference best practice is a single character.**
Character Reference, first Best Practices item: “Use Midjourney Images: For best results, start with an image of a single character created by Midjourney.” Second: “Limit Character References: While you can use more than one image of the same character, it's often not necessary.”
Omni Reference describes multi-subject images as a qualified possibility, not a recommendation: “Multiple Characters: While you can only use one image as an Omni Reference, you can try using an image that contains multiple characters/people and describe them in your prompt.” Subjects require individual textual identification; no outcome is guaranteed.
Source: https://docs.midjourney.com/hc/en-us/articles/32162917505293-Character-Reference 、https://docs.midjourney.com/hc/en-us/articles/36285124473997-Omni-Reference （all dates not stated）

**Midjourney acknowledges references and style instructions competing for influence (style drift).**
Omni Reference: “Reinforce the Style: If you want your image in a different style than your reference, mention your desired style at both the start and end of your prompt... With a lower weight you will need to reinforce the physical characteristics you want to preserve using your prompt text.” Also: “If you're using high stylize or `--exp` values you may want to also use a higher Omni Reference Weight, as these will all compete for influence.” Excessive-weight warning: “it's best to keep your weight below 400, otherwise your results may be unpredictable.”
Source: same as above (date not stated)

**Google’s only official reference count/content restriction concerns a single subject.**
Veo Subject image: “You provide up to three images of a single person, character, or product. Veo preserves the subject's appearance in the output video.” Style image is limited to one. This is the closest official single-subject reference requirement found, but applies to video; no equivalent image-generation statement was found.
Source: https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/use-reference-images-to-guide-video-generation （Last updated 2026-08-21 UTC）

**Seedream requires explicit identification of what to extract from references.**
“Reference Target: Clearly describe the elements to be extracted and retained from the reference image”: correct selection is not assumed; prompts identify it. For complex content difficult to describe accurately, it suggests “visual indicators such as arrows, bounding boxes, or doodles can be used to specify the editing target and its location.”
Source: https://docs.byteplus.com/en/docs/ModelArk/1829186 （Last updated 2026-07-06）

**No direct official statement found**: no vendor directly discusses consequences of using multi-panel/collage references. These are related facts about composition inheritance, single-subject references, and explicit extraction targets, not official judgments on collage references themselves.

### 2.2 Papers: direct mechanistic evidence for composition inheritance and diluted subject anchoring

**Reference injection copies overall reference content rather than only the subject (copy-paste effect).**
Conceptrol (arXiv:2503.06568, 2025-03-09): “zero-shot adapters inject the reference image as a condition in the attention block with Direct Adding or MM-Attention. To that end, **the reference image is not attached to any explicit text concept**. This often results in a **copy-paste effect** or poor compositional generation.” Figure 1’s specific failure is “duplicating the book”: an object occurring twice in the reference is also duplicated in output.
Its conditioning-strength explanation shows why tuning does not bypass this: “a low image conditioning strength (IP Scale) in IP-Adapter fails to preserve the concept effectively while increasing the scale causes deviations from the text prompt and leads to a copy-paste effect.”
Source: https://arxiv.org/abs/2503.06568 、https://arxiv.org/html/2503.06568v1 （2025-03-09 v1）

**Diluted subject anchoring has direct attention-layer evidence.**
Of three empirical observations, the first directly describes this issue: “In the absence of textual concept constraints, **the attention map for reference images derived from adapters does not focus on the target subject requiring customization**”; the second gives the consequence: “Adapters do well at transferring appearance of reference images within regions of high attention scores”. Appearance transfers where attention lands. Multiple salient regions dispersing attention follows directly from this mechanism.
Source: same as above

**The vendor’s own technical report acknowledges that what to preserve is underdetermined.**
Seedream 4.0 technical report: “reference-based generation presents a more challenging trade-off between preservation and creativity. This difficulty arises from **the inherently ambiguous definition of what should be preserved**. In some cases, the target is a person's ID or IP, a particular artistic style, or even an abstract concept.”
Source: https://arxiv.org/html/2509.20427v2 （2025-09）

**Evidence boundary, which must be stated**: Conceptrol tests open-source adapter architectures (IP-Adapter / OminiControl on Stable Diffusion, SDXL, FLUX). ArcReel mostly calls closed commercial APIs whose reference injection is unpublished. The same mechanism applying to those APIs is **inference**, not a paper conclusion. The only aligned official signal is Midjourney explicitly saying image prompts influence composition.

---

## 3. Block C: anatomy safeguards and textual negative prompts

### 3.1 Positive anatomy safeguards (“symmetric facial features, five intact fingers, balanced limb proportions”)

**Conclusion: no vendor documentation body obtained in this research recommends declaring facial symmetry, finger count, or limb proportions in positive prompts.**

Per-vendor checks:
- Google: none of seven image best practices concerns anatomy; documented limitations are language support, image count, text rendering, and safety blocking, without anatomy breakdown. (https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/capabilities/gemini-image-generation-best-practices , Last updated 2026-08-21 UTC)
- OpenAI: **does** offer human-body guidance, but as **visible compositional descriptions**, unlike quality assertions: “For people in scenes, describe scale, body framing, gaze, and object interactions. Examples: "full body visible, feet included," "child-sized relative to the table," "looking down at the open book, not at the camera," or "hands naturally gripping the handlebars." These details help with body proportion, action geometry, and gaze alignment.” (https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide , 2026-04-21)
- BFL FLUX.2, Seedream prompt guide, DashScope text-to-image guide, MiniMax, Midjourney, Dreamina: no anatomy-safeguard advice in the full text.
- Alibaba DashScope alone explicitly names extra fingers, but in the **negative channel**: `negative_prompt` describes unwanted content such as blur or extra fingers, only to assist quality optimization. (https://help.aliyun.com/zh/model-studio/text-to-image , date not stated)

**Evidence strength**: positive anatomy safeguards are community lore without official endorsement within this scope. They share origins with early Stable Diffusion positive/negative quality-word practices, but **no first-party source found here establishes effectiveness or ineffectiveness on contemporary models**. Lack of endorsement is not falsification. The closest contrary official signals were already recorded: Vidu calls `cinematic dramatic beautiful high quality` “model noise”; Seedream gives keyword piling as a negative example.

### 3.2 Textual negative exclusions (“avoid watermarks, extra text, Logo” at the prompt end)

This requires two nonequivalent subquestions; existing Q3 answers only the first.

**Subquestion 1: official attitudes to body-text exclusions split three ways, including within vendors.**
Existing Q3’s three-way conclusion holds, with these additions/updates:

| Vendor | Official guidance | Recency verified here |
| --- | --- | --- |
| Google images | Explicitly opposes, requiring positive rewriting: “Describe what you want, not what you don't: Instead of saying "no cars", describe the scene positively by saying, "an empty, deserted street with no signs of traffic."” | Verified current, Last updated 2026-08-21 UTC |
| Black Forest Labs FLUX.2 | **New** (not covered previously): “**No negative prompts: FLUX.2 does not support negative prompts. Focus on describing what you want, not what you don't want.**” No negative channel, explicit positive rewriting | https://docs.bfl.ai/guides/prompting_guide_flux2 , date not stated |
| OpenAI images | Explicitly encourages body exclusions: “State exclusions and invariants explicitly (e.g., "no watermark," "no extra text," "no logos/trademarks," "preserve identity/geometry/layout/brand elements")”; repeated examples “No extra text, no watermarks, no unrelated logos” and “Constraints: - Original artwork only - No trademarks - No watermarks - No logos” | Verified current, 2026-04-21 |
| Alibaba DashScope | Split by model: qwen-image supports `negative_prompt`; wan2.7-image-pro and wan2.7-image do not support `negative_prompt`, and instruct describing unwanted elements in the positive prompt (“do not show xxx”). **Models without a negative channel explicitly put negation into positive prompts** | https://help.aliyun.com/zh/model-studio/text-to-image , date not stated |
| Seedream images | **Correction to previous classification**: the earlier report classified Volcengine as encouraging body exclusions based on Seedance **video** 2.0’s constraint-words section. The Seedream 4.0–4.5 **image** guide has no exclusion/constraint section, only preservation instructions to explicitly keep other elements unchanged. The video conclusion cannot transfer directly to images | https://docs.byteplus.com/en/docs/ModelArk/1829186 , Last updated 2026-07-06 |
| MiniMax image-01 | No negative channel; official docs take no position | Date not stated |
| Midjourney | No official body-negation guidance found on Image Prompts / Omni Reference / Character Reference | Date not stated |
| Dreamina | No official statement found | — |

**Subquestion 2: does textual negation backfire on contemporary models (mention reinforces)? Peer-review-level evidence is unfavorable to textual negation.**

- **T2I compliance with body negation is very low; failures still depict the prohibited entity.** “Relations, Negations, and Numbers: Looking for Logic in Generative Text-to-Image Models” (arXiv:2411.17066, 2024-11-26), testing DALL·E 3: “**Unmodified prompts specifically prohibiting an entity X invariably led to images showing X.**” Unmodified negative prompts achieved **12.3%** accuracy; even LLM rewrites reached only **40.7%** overall. Rewrite analysis matters: 71% were “Addition” (the original entity is still mentioned, with others added), which “seems to fail more often than it succeeds. While additional objects are indeed added in the image, the original object often remains”.
  Source: https://arxiv.org/abs/2411.17066 、https://arxiv.org/html/2411.17066v1 （2024-11-26，v1）
- **Underlying text encoders are nearly insensitive to negation.** “Vision-Language Models Do Not Understand Negation” (arXiv:2501.09425, v1 2025-01-16, v2 2025-05-13, **CVPR 2025**): “Our evaluation reveals that modern VLMs struggle significantly with negation, **often performing at chance level**.” It studies the CLIP family, the text-conditioning encoders in most diffusion models.
  Source: https://arxiv.org/abs/2501.09425
- **Distinct mechanism: the negative_prompt channel has independent, effective behavior and cannot validate textual negation.** “Understanding the Impact of Negative Prompts: When and How Do They Take Effect?” (arXiv:2406.02965, 2024-06-05) describes Delayed Effect (after positive content is rendered) and Deletion Through Neutralization (cancellation with positive conditioning in latent space), relying on CFG’s separate negative-conditioning branch. **This differs from negation in positive prompt prose**. ArcReel’s `lib/prompts/prompt_builders.py` docstring already abandons the negative_prompt parameter channel for textual tails; the paper **does not support** that implementation and instead supports the path it abandons.
  Source: https://arxiv.org/abs/2406.02965 （2024-06-05）

**Correcting “mention reinforces”**: papers show low compliance, with negated entities still appearing, not the stronger claim that negation makes them more likely than omission. No first-party comparison found here tests negative wording versus no mention and reports worse outcomes for the former. “Mention reinforces” would exceed the evidence.

### 3.3 Three evidence categories (block C)

- **Supported by first-party evidence**: Google / BFL favor positive rewriting over negation (explicit official guidance); OpenAI favors body exclusions (explicit, directly conflicting); Alibaba instructs “do not show xxx” when no negative channel exists (explicit); T2I compliance with textual negation is very low (papers, CVPR/arXiv).
- **Community lore without official endorsement**: positive anatomy safeguards (facial symmetry, five intact fingers, balanced proportions); positive quality-word piling.
- **Explicitly contradicted by official docs**: negative prompts on FLUX.2 (unsupported); "no X" negation on Google images (rewrite positively); `negative_prompt` on wan2.7-image / wan2.7-image-pro (unsupported; write in positive prose instead).

---

## 4. Block D: what conditioning-reference images should depict

The question concerns reference images as downstream i2i / reference-to-video conditioning, not their attractiveness to people: framing, angle, pose, background, and count. Sources: official reference specifications/content advice, plus subject-driven / personalized-generation reference ablations.

### 4.1 Conclusion matrix

| Question | Official vendor guidance | Paper evidence | Supported conclusion |
| --- | --- | --- | --- |
| D1 Framing (full body / half body / bust) | **Not found** across 7 vendors checked page by page (Google, OpenAI, BFL, Seedream, DashScope, MiniMax, Midjourney); Dreamina retains first-round absence | Two adjacent findings: latent diffusion reconstructs relatively small faces inaccurately (DiffBody); PhotoMaker training crops require face >10% of image | No direct support for a framing choice. Both suggest **face pixel share matters**, without directly testing reference framing |
| D2 Angle (front / 45° / three-quarter) | **Not found**; no “45° conveys depth” match in 7 official sources, none for Dreamina | No reference-angle ablation; IDAdapter’s mechanism instead explains absorption of individual face angles | Not found. Existing template’s 45° is **community lore without official endorsement** |
| D3 Pose (A-Pose / T-Pose / natural) | **No image vendor recommendation found**; adjacent Google guidance supplies a **separate pose reference** for complex poses | No T2I A-Pose ablation (canonical A-pose in 3D reconstruction is a different chain from i2i identity anchoring) | Not found. Official pose control uses a **separate pose reference**, not inherited identity-reference pose |
| D4 Background (plain vs. environment) | **Not found** across 7 vendors | **Consistent empirical evidence**: Subject-Diffusion segments before encoding and loses all metrics without location control; PhotoMaker noises non-person regions to remove background influence; PuLID treats background/light/composition/style as ID-unrelated and preserves original-model behavior | Papers consistently treat reference background as contamination removed before encoding; same mechanism as B7 copy-paste |
| D5 Count (one vs. multiple angles) | Only **quotas**, not recommendations: Google 3 Pro up to 5 character references; BFL pro up to 8 at 1MP output; Seedream 4.0–4.5 up to 14; MiniMax **one subject_reference per request** | **Quantitative ablations, strongest in this block**: DreamBooth 3–5 samples, 1→5 supplement ablation best near 4; PhotoMaker strongest gain 1→2 with text-control tradeoff; SynCD 3 vs. 1; IDAdapter N is **training hyperparameter, not inference reference count** (4.2) | Multiple same-subject angles outperform one; **all empirical evidence uses separate input images, never panels inside one image**. Counts differ: DreamBooth fine-tuning samples, PhotoMaker/SynCD inference references, IDAdapter training mixtures |

### 4.2 Individual evidence

**D1 Framing: no direct evidence; two adjacent findings concern face pixel share.**

DiffBody explains a **generation-side** mechanism: “the VAE used in the LDM cannot reconstruct relatively small faces accurately from low-dimensional latent maps”. Face-specific refinement improves the lower-is-better ID metric from 0.403 to 0.175 (Table 3). Small latent face share in full-body outputs loses identity, **on the output side, not the reference side**; this cannot directly prove references should avoid full-body framing.
Source: https://arxiv.org/abs/2401.02804 （2024-01-05）

PhotoMaker’s ID-training crop rule is an **author-imposed constraint**: “we crop the image with a larger square box based on the detected face area while ensuring that the facial region can occupy more than 10% of the image after cropping”. Authors thus consider low face share harmful to ID-feature learning. Applying this to inference references is **inference**; that experiment was not done.
Source: https://arxiv.org/abs/2312.04461 、https://arxiv.org/html/2312.04461v1 （2023-12-07）

Vendor checks retrieved and searched Gemini image generation, OpenAI guide/API reference, BFL FLUX.2, Seedream guide/API, MiniMax image guide, Midjourney, and DashScope text-to-image: 7 vendors (no new Dreamina page obtained; first-round finding retained). Searches for “full body / close-up / portrait / bust / framing / 景别 / 全身 / 半身” found **no reference-framing requirement**. Adjacent Midjourney single-character and Google Veo “a single person, character, or product” requirements constrain **subject count**, not framing.

**D2 Angle: no source for “45° conveys depth”.**

Searching the 7 retrieved official sources for “45 / three-quarter / 四分之三 / 侧视 / angle of view” found **no reference/prompt guide claiming a 45° angle better conveys depth** or similar (Dreamina retains first-round absence). Within this scope it is **community lore without official endorsement**.

No paper comparison of front versus 45° references and downstream identity fidelity was found either. Closest is IDAdapter’s multi-image mixing explanation: “This enriched feature is derived from multiple images under the same identity, so their common characteristics (i.e., the identity information) will be greatly enhanced, while others (such as **the face angle and expression of any specific image**) will be somewhat weakened.” This shows **individual reference angles are absorbed**, not which angle is preferable.
Source: https://arxiv.org/abs/2403.13535 （2024-03-20）

Adjacent, directionally relevant evidence lacked **citable first-party data** here: face-recognition accuracy declines at large yaw (pose-invariant face-recognition literature). This aligns with more frontal references, but concerns recognition models rather than diffusion conditioning; no directly quotable data was verified, so **it does not support this report’s conclusions**.

**D3 Pose: official control uses a separate pose reference.**

Google’s Character consistency: 360 view directly states: “For complex poses, include a reference image of the selected pose.” Thus a **separate dedicated pose reference** carries pose, rather than inheriting it from an identity reference.
Source: https://ai.google.dev/gemini-api/docs/image-generation （Last updated 2026-08-10 UTC）

A-Pose / T-Pose searches: no matches across 7 vendors, none for Dreamina. Main subject-driven papers (DreamBooth / Textual Inversion / IP-Adapter / InstantID / PuLID / PhotoMaker / Subject-Diffusion / IDAdapter) offered **no neutral-reference-pose ablation**. Canonical A-pose appears in 3D reconstruction (StdGEN family), but “single image → canonicalization → 3D reconstruction” differs from “reference → i2i conditioning”; no citable verbatim text obtained, so excluded from conclusions.

A related contrary fact: InstantID explicitly uses “five facial keypoints (two for the eyes, one for the nose, and two for the mouth)” as **weak spatial conditioning**, specifically to avoid transferring reference pose, allowing users to “generate customized images with various poses or styles”. In that architecture, reference pose does not determine downstream pose.
Source: https://arxiv.org/abs/2401.07519 （v1 2024-01-15，v2 2024-02-02）

**D4 Background: strongest directional finding here, reference backgrounds contaminate.**

Subject-Diffusion segments before encoding: “we feed the **segmented** subject image into the CLIP image encoder to obtain 256-length patch feature tokens”, using SAM (“these detection boxes are used as input prompt to SAM to obtain their respective object masks”). Ablation: “if we remove the location control (object masks), our model will apparently degenerate over all evaluation metrics”.
Source: https://arxiv.org/abs/2307.11410 （2023-07-21）

PhotoMaker fills non-person regions with noise, explicitly “we filled the image areas other than the body part of a specific ID with random noises **to eliminate the influence of other IDs and the background**”; segmentation uses Mask2Former person-class panoptic segmentation.
Source: https://arxiv.org/abs/2312.04461 （2023-12-07）

PuLID explicitly separates what references should transfer: “an ideal ID insertion should alter only ID-related aspects, such as face, hairstyle, and skin color, while image elements not directly associated with the specific identity, such as **background, lighting, composition, and style**, should be consistent with the behavior of the original model”. It acknowledges “Methods with higher ID fidelity tend to induce more severe style degradation”, quantified through CLIP-I (PuLID 0.812 vs InstantID 0.680).
Source: https://arxiv.org/abs/2404.16022 （v1 2024-04-24，v2 2024-10-31）

SynCD supplies evaluation evidence: metrics “can struggle with capturing overall quality and **favor methods that copy-paste the target object on a new background**”. Copy-paste is thus a known failure rewarded by metrics.
Source: https://arxiv.org/abs/2502.01720 、https://arxiv.org/html/2502.01720v2 （v2 2025-10-13）

These four findings and B7 (copy-paste, attention not focusing on the target) indicate **all salient non-subject reference regions participate in conditioning**. B8 still applies: open-source evidence, inferred applicability to closed commercial APIs. **None of 7 official vendors checked states what reference backgrounds should be.**

**D5 Reference count: quantitative ablations, with important distinctions.**

- **DreamBooth**: “Given only a few (typically **3-5**) casually captured images of a specific subject”; capture requirements: “We do not impose any restrictions on input image capture settings and the subject image can have varying contexts”. Supplement ablates count, training “5 models per subject with input images ranging from 1 to 5”, best near 4, noting “This number can vary depending on the subject”. A relevant limitation is “context and subject appearance to become entangled”: **input context becomes entangled with subject appearance**.
  Source: https://arxiv.org/abs/2208.12242 （v1 2022-08-25，v2 2023-03-15）
- **Textual Inversion**: “Using only **3-5** images of a user-provided concept”; no count ablation found in the body.
  Source: https://arxiv.org/abs/2208.01618 （2022-08-02）
- **PhotoMaker**: “using more images to form a stacked ID embedding can improve the metrics related to ID fidelity”; gains are “particularly noticeable” **from 1 to 2 images** (Figure 7, DINO about 47.0 → 49.5), then diminish. Cost: “there may exist a trade-off between text controllability and ID fidelity” (CLIP-T declines with image count).
  Source: https://arxiv.org/abs/2312.04461 （2023-12-07）
- **IDAdapter** provides a count ablation (Table 2), but **N must be defined first**: “Changing the number of images N used in **training** process”; “At the testing and inference stage, we use only **one** image and simply duplicate it N times to serve as the input for the network”. **N counts same-identity images mixed during training, not user-provided inference references**. It cannot directly answer downstream asset-reference count; it addresses multi-image mixing’s effect on pose/expression locking:

  | N (training) | ID-Sim | Expr-Div | Pose-Div (pitch) | Pose-Div (yaw) |
  | --- | --- | --- | --- | --- |
  | 1 | 0.602 | 37% | 5.02 | 12.90 |
  | 2 | 0.601 | 58% | 6.97 | 15.39 |
  | 3 | 0.604 | 61% | 7.03 | 15.44 |
  | 4 | 0.603 | 65% | 7.90 | 16.47 |
  | 5 | 0.601 | 64% | 7.88 | 16.42 |

  **ID similarity barely changes with N (0.601–0.604); expression/pose diversity changes** (Expr-Div 37% → 65%, yaw 12.90 → 16.47). Authors choose N=4 for balance. As D2 explains, common identity strengthens while individual angles/expressions weaken. **The mechanism direction can transfer as inference**; **“therefore use 4 inference references” cannot**: inference in this paper uses one image.
  Source: https://arxiv.org/abs/2403.13535 （2024-03-20）
- **SynCD**: 1 vs 3 references in the same model improves MDINOv2-I from 0.777 to 0.822. It identifies datasets with the same object in **different poses, backgrounds, lighting** as the major bottleneck: “the lack of a dataset comprising multiple images of the same object in diverse poses, backgrounds, and lighting conditions has been a major bottleneck”.
  Source: https://arxiv.org/abs/2502.01720 （v2 2025-10-13）
- **InstantID**, a single-image approach: “Given only one reference ID image, InstantID aims to generate customized images with various poses or styles from a single reference ID image while ensuring high fidelity”; for multiple images: “For multiple reference images, we take the average mean of ID embeddings as image prompt”.
  Source: https://arxiv.org/abs/2401.07519 （2024-01-15）

**Most relevant distinction**: every multiple-image experiment uses **separate image inputs** (training samples / references / stacked embeddings); **none packs multiple views into one reference image**. D5’s multi-angle benefit does not mean multi-angle panels inside one asset are beneficial. Instead it aligns with A’s multi-reference/multi-output approaches and B’s layout inheritance/subject dilution.

---

## 5. Block E: aspect ratio

### 5.1 Conclusion matrix

| Vendor | **Reference-image** aspect requirements | Official behavior when reference ratio ≠ output ratio | Output ratios |
| --- | --- | --- | --- |
| Google Gemini | No input-ratio/resolution requirement found | **Explicit**: “By default, the model matches the output image size to that of your input image, or otherwise generates 1:1 squares.” **No statement found when explicit `aspect_ratio` differs from input**, including crop/pad/stretch | `1:1`, `3:2`, `2:3`, `3:4`, `4:3`, `4:5`, `5:4`, `9:16`, `16:9`, `21:9` |
| OpenAI gpt-image | No input-ratio requirement; only combined file size ≤50MB found | Not found | gpt-image-1: `1024x1024` / `1024x1536` / `1536x1024`; gpt-image-2 arbitrary resolution, but “Long edge to short edge ratio must not exceed 3:1” |
| BFL FLUX.2 | **Pixel budget**, not ratio: “[pro] API has a 9MP total limit for input+output. At 1MP output you can use up to 8 reference images, at 2MP output up to 7” | Not found (`match_input_image` appeared only in third-party resale API docs, **not verified on docs.bfl.ai**, excluded) | “1:1 (Square), 16:9 (Widescreen), 9:16 (Portrait), 4:3 (Classic), 21:9 (Ultrawide)”; “Minimum 64×64, maximum 4MP”, “Output dimensions must be multiples of 16” |
| ByteDance Seedream 4.0–5.0 | **Explicit, the only numeric reference-ratio range found**: `[1/16, 16]`, both dimensions >14px, pixels `[196, 6000×6000]`, ≤30MB per image. Reference maximum 14 for 4.0/4.5/5.0 Lite, 10 for 5.0 Pro | No ordinary-generation adaptation statement found; only layer decomposition has `auto`, “generates output based on the input image dimensions and aspect ratio” | Mutually exclusive forms: resolution tier (1K/1.5K/2K/4K by version) + natural-language ratio in prompt; or explicit width×height pixels, ratio `[1/16, 16]` |
| Alibaba DashScope | No reference-ratio requirement found | Not found | wan2.7-image-pro: 1K/2K/4K, custom 768×768–4096×4096, ratio 1:8–8:1; wan2.7-image: 1K/2K, 768×768–2048×2048; qwen-image-3.0: 512×512–2048×2048, ratio 1:8–8:1. Official note: 4K/custom 4096×4096 only for text-to-image or multi-image generation without input images |
| MiniMax image-01 | No ratio requirement found; **explicit “Only a single reference image is supported per request.”** | Not found | `aspect_ratio`: 1:1, 16:9, 4:3, 3:2, 2:3, 3:4, 9:16, 21:9 |
| Midjourney | Not found | Not found (Aspect Ratio does not discuss reference/image-prompt interaction) | `--ar #:#`, default 1:1, no decimals; “Aspect ratio isn't the same as image dimensions” |

### 5.2 Individual evidence and assessment

**E1: only Seedream specifies a numeric reference-ratio range; others constrain file size/pixel budget or say nothing.**
Seedream reference specifications (JPEG/PNG/WebP/BMP/TIFF/GIF/HEIC/HEIF, ratio `[1/16, 16]`, dimensions >14px, ≤30MB, pixels `[196, 36,000,000]`) appear in ModelArk’s image-generation API reference.
Source: https://docs.byteplus.com/en/docs/ModelArk/1541523 （date not stated）；prompt guide from the same source: https://docs.byteplus.com/en/docs/ModelArk/1829186 （Last updated 2026-07-06）
BFL constrains pixel budget rather than ratio.
Source: https://docs.bfl.ai/guides/prompting_guide_flux2 （date not stated）
OpenAI input guidance found here only limits combined file size to 50MB; gpt-image-2 output has the hard limit “Long edge to short edge ratio must not exceed 3:1”.
Source: https://developers.openai.com/api/docs/guides/image-generation （date not stated）
MiniMax accepts one subject_reference per request.
Source: https://platform.minimax.io/docs/guides/image-generation （date not stated）

**E2: the only official statement found here, with an important direction.**
Google, Aspect ratios and image size: “By default, the model matches the output image **size** to that of your input image, or otherwise generates 1:1 squares. You can control the aspect ratio and the size of the output image using the `aspect_ratio` and `image_size` fields under `response_format` when type is set to "image".” Thus **references supplied without an explicit ratio determine output size/ratio**; a 16:9 character asset defaults downstream unspecified-ratio generation to 16:9.
Source: https://ai.google.dev/gemini-api/docs/image-generation （Last updated 2026-08-10 UTC）

**No other vendor states whether mismatched ratios cause cropping, padding, or stretching.** Page-by-page checks covered 7: Gemini’s explicit-ratio branch, OpenAI guide/API, BFL FLUX.2 text-to-image/editing/guide, Seedream guide/API, MiniMax guide, Midjourney Aspect Ratio, DashScope text-to-image. **The hypothesis that official cropping makes reference ratio determine which content reaches conditioning is neither officially confirmed nor disproved within this scope.**

An **implementation example limited to open-source architecture**: IP-Adapter preprocesses by “we resize the shortest side of the image to 512 and then center crop the image with 512×512 resolution”. Non-1:1 references are **center-cropped**, excluding edges from conditioning. This demonstrates the mechanism can exist, but **application to closed commercial APIs is inference**, not established.
Source: https://arxiv.org/abs/2308.06721 （2023-08-13）

**E3: no first-party evidence found that reference ratio itself affects generation quality or identity anchoring.** No official statement or ratio-as-independent-variable paper ablation found.

**E4: no official/paper statement found on large side margins around a single standing subject in landscape 16:9.** Only three adjacent facts, each with its boundary:
1. E2 Google defaults: a 16:9 asset defaults unspecified-ratio output to 16:9 (official, Google only).
2. IP-Adapter square center crops exclude landscape side regions (paper/open source, cannot transfer to closed APIs). Harmless for centered subjects, but **horizontal multi-panel layouts lose side panels outright**.
3. D4: salient non-subject reference regions participate in conditioning. **No evidence found** that blank space is itself salient; no inference made.

No first-party source, official statement or paper evidence found here for “large blank margins induce background filling”.

---

## 6. Evidence-strength classification

| # | Conclusion | Strength |
| --- | --- | --- |
| A1 | No vendor officially recommends multiple views of one subject in one image | Not found across 8 vendors; Midjourney/Dreamina/DashScope/MiniMax docs lack the topic. All three official single-image multi-panel examples (OpenAI, BFL, Google) are narrative |
| A2 | Google consistency uses iterative angles + multiple references (3 Pro: 6 object + 5 character + 3 style; 3.1 Flash: 10 object + 4 character; 3.1 Flash Lite: 14 object, no character channel) | Explicit official guidance |
| A3 | Google warns exact requested image counts may not be generated | Explicit official guidance |
| A4 | OpenAI supports/examples single-image 4-panel narratives | Explicit official guidance |
| A5 | BFL requires **separate generation per comic panel**, repeating character details for consistency | Explicit official guidance |
| A6 | Seedream routes sets/sequences to multiple-image output ("a series"/"a set"/explicit count), naming storyboarding, comics, IP design | Explicit official guidance |
| A7 | Seedream sequences maintain character/style consistency | Paper support (vendor technical report arXiv:2509.20427) |
| A8 | Google’s dedicated 360 view section generates angles separately and reuses prior outputs as references, not one-image layout | Explicit official guidance |
| A9 | Google has a single-image multi-panel template (“Make a 3 panel comic”), narrative only | Explicit official guidance |
| B1 | Image prompts influence content, composition, colors | Explicit official guidance (Midjourney) |
| B2 | Character-reference best practice is one character per image | Explicit official guidance (Midjourney) |
| B3 | Identify multiple reference subjects individually in prompts; no result guarantee | Explicit official guidance (Midjourney: "you can try") |
| B4 | References and style instructions compete; excessive weights are unpredictable | Explicit official guidance (Midjourney) |
| B5 | Video references require “a single person, character, or product” | Explicit official guidance (Google Veo; no equivalent image statement) |
| B6 | Prompts must identify reference elements to extract/preserve | Explicit official guidance (Seedream) |
| B7 | Untied reference/text concepts cause copy-paste; attention misses target subjects | Paper support (arXiv:2503.06568, open-source adapters) |
| B8 | Same mechanism applies to closed commercial image APIs | **Inference** (no direct official/paper evidence; only B1 is an aligned official signal) |
| B9 | Official docs directly discuss collage-reference consequences | Not found for any vendor |
| C1 | Positive anatomy safeguards (facial symmetry/five fingers/proportions) have official endorsement | Not found across 8 vendors; Midjourney limited to three checked pages. Community lore without endorsement |
| C2 | OpenAI encourages "no watermark / no extra text / no logos" in body text | Explicit official guidance |
| C3 | Google images require positive rewriting of negation | Explicit official guidance |
| C4 | FLUX.2 lacks negative prompts and requires positive descriptions | Explicit official guidance |
| C5 | For wan2.7-image models lacking `negative_prompt`, Alibaba instructs “do not show xxx” in positive prompts | Explicit official guidance |
| C6 | Seedream **image** guide has no exclusion/constraint section (previous Volcengine negation conclusion comes from Seedance video) | Official finding: no such content on the page |
| C7 | Unmodified negative-body prompts in DALL·E 3 “invariably led to images showing X”, 12.3% accuracy | Paper support (arXiv:2411.17066) |
| C8 | CLIP-family text encoders understand negation near chance | Paper support (arXiv:2501.09425, CVPR 2025) |
| C9 | negative_prompt channel has an independent explainable mechanism (Delayed Effect / Deletion Through Neutralization) | Paper support (arXiv:2406.02965); not applicable to textual tails |
| C10 | “Mention reinforces”: negative wording makes unwanted content more likely | Not found; papers support low compliance, not reverse reinforcement |
| C11 | Official body-negation positions from Midjourney / MiniMax / Dreamina | Not found |
| D1 | Official reference framing (full/half body/bust) guidance | Not found across 7 page-by-page checks; Dreamina retains first-round absence |
| D2 | Small latent face share harms identity reconstruction, generation-side not reference-side | Paper support (arXiv:2401.02804, DiffBody) |
| D3 | PhotoMaker ID training crops require face >10% | Paper support (arXiv:2312.04461); inference-reference transfer is **inference** |
| D4 | Official/paper support for 45° conveying depth | Not found: no matches across 7 vendors, no paper control experiment; community lore without endorsement |
| D5 | Neutral A-Pose / T-Pose references improve downstream reposing | Not found; no vendor or mainline T2I ablation of this variable |
| D6 | Official pose control uses a **separate pose reference**, not inherited identity-reference pose | Explicit official guidance (Google: “For complex poses, include a reference image of the selected pose.”) |
| D7 | Weak spatial conditioning avoids transferring reference pose (five-keypoint design) | Paper support (arXiv:2401.07519, InstantID) |
| D8 | Mainstream subject-driven methods **remove reference backgrounds** before encoding (segmentation/masks/noise); removing control worsens all metrics | Paper support (arXiv:2307.11410 Subject-Diffusion, arXiv:2312.04461 PhotoMaker, arXiv:2404.16022 PuLID) |
| D9 | Official reference-background requirements (plain vs. environment) | Not found across 7 checked vendors; Dreamina retains first-round finding |
| D10 | Common 3–5 images; 1→5 ablation best near 4 | Paper support (arXiv:2208.12242 DreamBooth + supplement; arXiv:2208.01618 also 3–5) |
| D11 | More references improve ID fidelity, strongest 1→2, trading text control | Paper support (arXiv:2312.04461, DINO about 47.0→49.5, CLIP-T declines) |
| D12 | Same-ID mixing **unlocks individual pose/expression**, not ID similarity (N=1→4: ID-Sim 0.602→0.603, Expr-Div 37%→65%, yaw 12.90→16.47); **N is a training hyperparameter; inference duplicates one image N times**. Applying it to inference reference count is **inference** | Paper support (arXiv:2403.13535, IDAdapter); transferred claims are inference |
| D13 | 1 → 3 references improve object identity (MDINOv2-I 0.777→0.822) | Paper support (arXiv:2502.01720, SynCD) |
| D14 | Every multiple-image benefit uses **separate image inputs**, never multiple views packed into one image | Official guidance + paper input forms, aligned with A |
| D15 | Official counts are quotas, not recommendations (Google 5 character references, BFL 8, Seedream 14, MiniMax 1/request) | Explicit official guidance |
| E1 | Seedream reference ratio `[1/16, 16]`, dimensions >14px, ≤30MB, pixels `[196, 36,000,000]` | Explicit official guidance; only numeric reference-ratio range found |
| E2 | Google references without explicit ratio **default output size/ratio to the reference** (“matches the output image size to that of your input image”) | Explicit official guidance |
| E3 | Behavior when reference ratio differs from explicit output ratio (crop/pad/stretch/ignore) | Not found across all 7 vendors, including Google explicit-ratio branch |
| E4 | Open-source adapters resize short edge + **center-square crop** non-1:1 references, excluding edges | Paper support (arXiv:2308.06721, IP-Adapter); closed-API transfer is **inference** |
| E5 | Reference ratio itself affects quality or identity anchoring | Not found; no official/paper evidence with ratio as independent variable |
| E6 | Single standing subject in 16:9 with large margins induces background filling or other side effects | Not found; no first-party source |
| E7 | Output options/limits: Google 10 ratios; OpenAI gpt-image-2 ≤3:1; BFL 5 ratios, dimensions multiples of 16, ≤4MP; Seedream `[1/16,16]`; DashScope 1:8–8:1; MiniMax 8 ratios; Midjourney `--ar` no decimals | Explicit official guidance |

---

## 7. Relation to `arcreel-prompt-best-practices-research.md`

**Additions absent from that report**
1. **All of A**: the four previous questions (image prompts, i2v prompts, negation, prompt language) do not concern output **layout/composition form**. Multi-panel/multi-view/sheet guidance is new here.
2. **All of B**: the previous report discusses supplied first frames only for i2v (subjects/scenes/styles come from the frame, prompts describe motion), not i2i composition inheritance or subject dilution. This report adds official reference-selection guidance and adapter-mechanism papers.
3. **Vendor coverage**: the previous 8 are ArcReel-integrated vendors, **excluding Midjourney and Black Forest Labs**. Both are added; BFL supplies the clearest negative-prompt statement here (FLUX.2 does not support them).
4. **Positive anatomy safeguards**: absent previously; per-vendor checking here finds no official endorsement.

**Corrections/refinements**
1. **Split Volcengine negation by image/video**. Previous Q3 classified it as C, encouraging body prohibition/exclusion constraints, based on Seedance 2.0 video’s constraint section. Full Seedream 4.0–4.5 **image** guide (Last updated 2026-07-06) has **no exclusion/constraint section**. The conclusion holds for video, with **no evidence** for images. ArcReel assets use images; preserve this boundary when citing.
2. **Tighten Q3’s shared implicit limit**. Previously: negation is not universal and vendors admit constraints are not 100% controllable. Added papers quantify this limitation (unmodified negative-prompt accuracy 12.3%).
3. **Key Q3 distinction**: negative_prompt **parameter channels** and prompt-body **textual negation** are separate mechanisms. Previous classification organizes vendor support/recommendations, without establishing transferable effectiveness. They cannot justify each other: arXiv:2406.02965 covers only the parameter channel; current `lib/prompts/prompt_builders.py` uses textual tails, not supported by that paper.
4. **Recency check**: the prior Google best-practices URL (`cloud.google.com/gemini-enterprise-agent-platform/...`) now 301-redirects to `docs.cloud.google.com/...`, retaining content, Last updated 2026-08-21 UTC; “Describe what you want, not what you don't” is unchanged. OpenAI cookbook updated 2026-04-21; “State exclusions and invariants explicitly” unchanged. Neither Q3 statement needs revision.

---

## 8. Direct implications for #2058’s four layout types

This section states what evidence indicates, without deciding retention, deletion, or redesign.

**Multiple panels in one image**
- A1/A5/A6: multiple views of one subject in one image have **no official guide endorsement** within this scope. OpenAI/BFL/Google’s official multi-panel examples all concern **narrative panels**, and BFL generates each separately. Seedream explicitly routes set design to multiple outputs instead of panels in one image.
- A2/A3/A8: Google’s same-character angles use iterative generation and reference feedback; generated counts are explicitly not guaranteed. Hard counts such as four panels or three views cannot be promised on official evidence.
- A9: Google’s single-image panel template exists but is limited to comic/storyboard narratives; same-subject asset views are a different requirement, routed to A8’s iterative approach on the same page.
- Relation to the issue’s criterion, whether every panel has a downstream consumer: this research **does not cover** ArcReel’s internal consumption chain and supplies no external evidence for that criterion. Even when a panel has a downstream consumer, arranging it in the same image is not an officially recommended implementation within this research scope; official guidance recommends multiple images/references. These are independent, and this report does not determine whether the criterion holds.

**Multi-panel images entering i2i reference chains**
- B1 (Midjourney image prompts affect composition) and B7 (paper copy-paste + unfocused subject attention) indicate overall layout and all salient regions participate in conditioning. #1803’s observed downstream panel-layout inheritance agrees with both.
- B2/B3/B5/B6: official reference expectations are **single subject, explicitly identified in the prompt**. Multi-panel images satisfy neither: multiple salient regions, with existing templates not identifying a particular reference panel.
- B8’s boundary must accompany the claim: papers study open-source adapters; closed-API applicability is inference.

**Positive anatomy safeguards in `_*_GUARD`**
- C1: facial symmetry / five intact fingers / balanced proportions have no endorsement across 8 official vendors. Compare OpenAI **visible composition** ("full body visible, feet included", "hands naturally gripping the handlebars") and Alibaba’s extra fingers in the negative channel.
- Distinguish `_SCENE_GUARD`’s absence of visible people and `_PRODUCT_GUARD`’s fidelity statement: these are **positively rewritten exclusions**, not anatomy safeguards. Their form matches Google/BFL “Describe what you want, not what you don't”; C3/C4 support that form and oppose "no X".

**`_NEGATIVE_TAIL_*` textual tails**
- Evidence splits across vendors, not time: OpenAI (C2) encourages; Google (C3) / BFL (C4) require positive rewriting; Alibaba (C5) uses positive-body negation without a parameter channel. **Effectiveness of “avoid watermarks, extra text, Logo” depends on the target model**. ArcReel’s backend-independent text concatenation sends the same tail to models with opposite guidance.
- C7/C8: even models encouraging exclusions do not reliably obey textual negation. C10 bounds the conclusion: no evidence shows negative wording is worse than omission.
- C9: the docstring claim that image backends mostly silently drop negative_prompt partly matches official facts (FLUX.2, MiniMax image-01, wan2.7-image lack it) and partly does not (qwen-image formally supports `negative_prompt`; Kling’s similarly named video field is not an image counterexample). Abandoning the parameter channel loses the independent reduction mechanism in arXiv:2406.02965.

**Separate constants for four negative tails, even when content matches**
- No external evidence found for or against this rule. It is an internal constant-organization convention, independent of vendors. Adjacent evidence: official guidance splits by **target model** (C2 vs C3/C4/C5), not **image type**; current constants split by image type.

---

## 9. Evidence supporting form and ratio decisions

This section asks only: **does this evidence support directly choosing framing / angle / pose / background / aspect ratio?** It does not decide the form itself.

**Support differs by dimension:**

| Dimension | Supports direct selection? | Basis |
| --- | --- | --- |
| Background (plain vs. environment) | **Yes, clear direction** | D8 uniquely combines independent papers with aligned ablations: mainstream subject-driven methods remove backgrounds before encoding; removing control worsens all metrics. PuLID calls backgrounds ID-unrelated and preserves original-model behavior; aligns with B7 copy-paste. B8 remains: open-source evidence, closed-API applicability inferred. Direction **does not require A/B testing**; tests establish magnitude |
| Reference count / single image | **Yes, only at the level “separate images > one composite”** | D10–D14 have quantitative ablations from four papers (IDAdapter N is training-only, not inference count). They support separate inputs over one, **not how many views to arrange inside an asset**; no experiment tests that. They strengthen the settled multi-panel → single-image premise. Generating multiple single images afterward is supported but is a new form question |
| Framing (full / half body / bust) | **No, requires testing** | D1 has no direct evidence. D2/D3 support only the weaker fact that face pixel share matters. They cannot rank framing because resolution and subject placement also change share |
| Angle (front / 45°) | **No, requires testing** | D4 has no source; template 45° is community lore. Papers supply no directional evidence |
| Pose (A-Pose / natural) | **No, requires testing** | D5 has no evidence. D6/D7 only show official/mainstream approaches do not rely on identity-reference pose inheritance (separate pose references / weak spatial conditioning). This **weakens the premise that reference pose matters**, without ranking A-Pose and natural pose |
| Aspect ratio | **No direct selection; incorporate one official constraint first** | E3/E5/E6 not found. E2 is the sole hard official fact: Google outputs follow reference ratio unless explicitly specified, a **downstream behavior constraint**. Asset ratio spills into unspecified downstream calls. Choosing a ratio requires testing; its downstream-default role is already established |

**Overall**: evidence supports **two points**: remove unrelated reference backgrounds (D8); multi-angle benefits belong to separate images, not one panel grid (D14). It also gives **one required design constraint**: asset ratio becomes the default for downstream unspecified-ratio outputs (E2). **Framing, angle, pose, and the specific ratio have no first-party basis for direct selection here; choose them through A/B testing.**

The split is explicable: framing, angle, and A-Pose are **art-asset pipeline conventions** (three-view character sheets, turnarounds), not variables commonly ablated in diffusion literature, which studies count, background processing, and encoding granularity. Conventions may be sound, but lack first-party endorsement under this source discipline; **retaining them acknowledges unverified defaults**.

**For A/B testing, the sole external input here is what to hold constant**: background removal and single-subject/single-image reference form, varying only framing / angle / pose / ratio, because the first two have evidence and the latter four do not. Evaluate **downstream identity fidelity and downstream pose/composition freedom relative to references**, rather than asset attractiveness, matching block D and IDAdapter’s ID-Sim/Pose-Div metric pairing.
