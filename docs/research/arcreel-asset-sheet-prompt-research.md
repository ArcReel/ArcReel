# 资产图提示词调研：多格构图、i2i 参考图副作用、防崩与反向尾句

> 用途：为 [#2058](https://github.com/ArcReel/ArcReel/issues/2058)「资产图提示词模板优化」提供一手证据，覆盖 `lib/prompt_builders.py` 中 `_CHARACTER_LAYOUT` / `_SCENE_LAYOUT` / `_PROP_LAYOUT` / `_PRODUCT_LAYOUT`、`_*_GUARD`、`_NEGATIVE_TAIL_*` 三类内化文本。
> 范围：五个问题块——A 多格 / 多视图 / sheet 式构图的官方口径；B 多格拼贴图作为 i2i 参考图的副作用；C 正向防崩句与文本化反向尾句的实际有效性；D 作为条件输入的参考图其画面应当是什么样（景别 / 视角 / 姿态 / 背景 / 张数）；E 宽高比。D、E 两块为 2026-08-24 定向补充调研，回答「单图该画什么、用什么宽高比」。
> 来源纪律：只采信官方文档站 / 模型卡 / 供应商 cookbook / API reference，以及 arXiv 与同行评审论文。二手博客、论坛、聚合教程不进结论，仅在需要标注「社区口传无官方背书」时提及并如此标注。每条结论附来源 URL 与发布/更新日期；取不到日期写「日期未标注」。查不到官方说法一律写「未找到官方表述」，不以推测填补。
> 抓取方式说明：多家供应商文档站为前端渲染或对直连返回 403（`docs.midjourney.com`、`www.volcengine.com/docs`、`docs.byteplus.com`、`platform.minimax.io`）。这些页面的正文经渲染代理取得，引文与 URL 均为官方原页内容，不引用任何二手转述。
> 调研日期：2026-08-24
> 与既有报告的关系：本报告是 `arcreel-prompt-best-practices-research.md`（2026-07-13）的增量，不重述其 Q1/Q2/Q4；C 块前半引用并细化其 Q3，A/B/D/E 四块为该报告未覆盖的新地。详见第七节。

---

## 一、A 块：多格 / 多视图 / sheet 式构图的官方口径

### 1.1 结论矩阵

| 供应商 | 官方是否推荐「一张图排多个面板/视图」 | 官方给出的一致性路径 | 面板数量/一致性的官方说明 | character sheet 用例 |
| --- | --- | --- | --- | --- |
| Google Gemini（Nano Banana 系列） | **部分推荐（仅限叙事分格）**：官方 prompting guide 第 6 节 Sequential art (comic panel / storyboard) 给出「Make a 3 panel comic in a [style]」模板；**同主体多视图明确走相反路径**：第 7 节 Character consistency: 360 view 要求逐角度迭代生成，非单图排版 | **迭代生成 + 多参考图通道**：360 视角靠「iteratively prompting for different angles」并把已生成图回灌为参考；多参考图配额 Gemini 3 Pro 6 物体图 + 5 角色图 + 3 风格图，3.1 Flash 10 物体图 + 4 角色图，3.1 Flash Lite 14 物体图、无角色通道，合计上限 14 张 | 明确限制：「The model might not create the exact number of images you ask for」；2.5 Flash Image 建议输入图不超过 3 张，3 Pro Image 不超过 14 张 | 未找到「character sheet」字样；官方对等用例是 360 view，做法为逐角度分图 |
| OpenAI gpt-image | **推荐**（仅限叙事分格）：cookbook 设 Story-to-Comic Strip 小节，示例即「4 equal-sized panels」单图四格 | 单图内逐格描述 + 多图输入按 index 引用 | 「one per panel」，未给数量上限或一致性保证 | 未找到（全文无 character sheet / multi-view / turnaround 字样） |
| Black Forest Labs FLUX.2 | **明确相反**：官方多格章节要求逐格分别生成 | 「Generate each panel separately while keeping character descriptions consistent」；一致性靠在每格 prompt 里重复角色描述 | 「Repeat these details in every panel prompt」 | 未找到（多参考图用途列为 character consistency across variations） |
| 字节 Seedream 4.0/4.5（火山方舟 / BytePlus ModelArk） | **明确相反**：官方把多视图/成套需求归到「多图输出」 | 「Multi-image output」——用「a series」「a set」或指定张数触发成组生成，官方点名 storyboarding、comic creation、IP product design、emoji pack | 技术报告：「image sequences that remain both character-consistent and stylistically aligned」 | 未找到「单图 character sheet」表述；成套设计场景走多图输出 |
| 阿里通义万相 / Qwen-Image（DashScope） | 未找到 | 官方 prompt 指南只给「主体+场景+风格+镜头语言+氛围词+细节修饰」公式与五维词典 | 未找到 | 未找到官方表述 |
| MiniMax image-01 | 未找到 | `subject_reference`（`type: "character"`）参考图通道，见 i2i 端点 | 未找到 | 未找到官方表述 |
| Midjourney | 未找到多格/sheet 构图表述 | Omni Reference（`--oref`/`--ow`，仅一张）、Character Reference（`--cref`/`--cw`，V7 起由 Omni Reference 取代） | 未找到 | 未找到官方表述；Character Reference 官方最佳实践反向要求参考图为**单角色** |
| 即梦 Dreamina | 未找到 | 未找到 | 未找到 | 未找到官方表述 |

**跨供应商可归纳的一条事实**：在本次能取得正文的全部供应商中，**没有任何一家把「同一主体的多视图排在一张图里」写进官方指南**。官方给出的一致性路径分两类——多参考图通道（Google、Midjourney、MiniMax、BFL）或成组多图生成（Seedream）。出现的单图多格官方用例共三处（OpenAI comic strip、BFL comic strip、Google 3 panel comic）全部是**叙事分格**，无一是同主体多视图；且 BFL 的做法是逐格分别生成。更强的一条反向证据来自 Google：官方为「同一角色的多个角度」专设了 360 view 小节，给出的做法是逐角度分别出图、把已生成图回灌为参考，而不是在一张图里排版。

### 1.2 逐条证据

**Google Gemini：一致性被定位为多参考图问题，且官方明示数量不可保证。**
参考图配额（官方表格按模型分列）：「Up to 14 reference images: You can now mix up to 14 reference images to produce the final image.」Gemini 3.1 Flash Lite Image 最多 14 张物体图、角色与风格通道均为 N/A；Gemini 3.1 Flash Image 最多 10 张物体图 + 4 张角色图；Gemini 3 Pro Image 最多 6 张物体图 + 5 张角色图 + 3 张风格图（角色图一列的原文为「Up to N images of characters to maintain character consistency」）。

同页 prompting guide 第 7 节标题为「Character consistency: 360 view」，正文：「You can generate 360-degree views of a character by iteratively prompting for different angles. For best results, include previously generated images in subsequent prompts to maintain consistency. For complex poses, include a reference image of the selected pose.」模板为「A studio portrait of [person] against [background], [looking forward/in profile looking right/etc.]」——即**一次一个角度、单张输出**，官方示例 prompt 为「A studio portrait of this man against white, in profile looking right」。

同页第 6 节「Sequential art (comic panel / storyboard)」是官方唯一的单图多格模板：「Make a 3 panel comic in a [style]. Put the character in a [type of scene].」官方注明「For accuracy with text and storytelling ability, these prompts work best with Gemini 3 Pro and Gemini 3.1 Flash Image.」该用例是叙事分格，不是同主体多视图。全页检索 `character sheet / turnaround / multiple views` 无命中。
来源：https://ai.google.dev/gemini-api/docs/image-generation （Last updated 2026-08-10 UTC）

官方限制页：「The model might not create the exact number of images you ask for.」「For best results using Gemini 2.5 Flash Image, include a maximum of three images in an input. For best results using Gemini 3 Pro Image, include a maximum of 14 images in an input.」
来源：https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/capabilities/gemini-image-generation-limitations （Last updated 2026-08-21 UTC）

官方最佳实践页七条中无一条涉及多格构图；角色漂移的对策是「restart a new conversation with a detailed description」。
来源：https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/capabilities/gemini-image-generation-best-practices （Last updated 2026-08-21 UTC）；https://developers.googleblog.com/en/how-to-prompt-gemini-2-5-flash-image-generation-for-the-best-results/ （2025-08-28）

**OpenAI：唯一把单图多格写进官方指南并给出完整示例的一家，但用途是叙事分格。**
能力项：「Complex structured visuals, including infographics, diagrams, and multi-panel compositions」。
4.7 节：「For story-to-comic generation, define the narrative as a sequence of clear visual beats, one per panel. Keep descriptions concrete and action-focused so the model can translate the story into readable, well-paced panels.」示例 prompt 首句「Create a short vertical comic-style reel with 4 equal-sized panels.」并逐格写 Panel 1–4。
该页全文检索 `multi-view / character sheet / turnaround / three-view / contact sheet / collage` 全部无命中。
来源：https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide （2026-04-21）

**Black Forest Labs：官方多格章节的做法是逐格分别生成。**
「Comic Strips and Sequential Art — Create consistent comic panels with character continuity. The key is to define your character in detail and maintain that description across panels.」「Generate each panel separately while keeping character descriptions consistent」，并在示例末尾提示「Character Consistency: Notice how Diffusion Man's description stays detailed and consistent across panels... Repeat these details in every panel prompt.」
多参考图为独立通道：「[pro] API has a 9MP total limit for input+output. At 1MP output you can use up to 8 reference images, at 2MP output up to 7, and so on.」适用场景官方列为 fashion shoots / interior design / product composites / character consistency（Maintain identity across variations）。
来源：https://docs.bfl.ai/guides/prompting_guide_flux2 （日期未标注）

**Seedream：官方把「成套/多视图」需求明确归到多图输出，不是单图多格。**
官方 prompt guide「Multi-image output」小节：「Seedream 4.5 and 4.0 support generating image sequences with consistent character continuity and unified style, making it suitable for storyboarding, comic creation, and set-based design scenarios that require a cohesive visual identity, such as IP product design or emoji pack creation.」「When generating multiple images, you can trigger series generation with phrases like "a series", "a set", or by specifying the number of images.」
同页「Reference-based generation」要求同时写清两件事：「Reference Target: Clearly describe the elements to be extracted and retained from the reference image」与「Generated Scene Description: Provide detailed information about the desired generated content, including scene, layout, and other specifics.」
来源：https://docs.byteplus.com/en/docs/ModelArk/1829186 （Last updated 2026-07-06；中文同源页 https://www.volcengine.com/docs/82379/1829186?lang=zh ）

技术报告佐证：「Leveraging strong capabilities in global planning and in-context consistency, Seedream 4.0 supports the generation of image sequences that remain both character-consistent and stylistically aligned.」
来源：https://arxiv.org/html/2509.20427v2 （arXiv:2509.20427，2025-09）

**阿里 DashScope：官方 prompt 指南无相关表述。**
`text-to-image-prompt` 页给基础/进阶公式与「景别、视角、镜头类型、风格、光线」五维词典，未涉及多格、三视图、角色设定图、拼贴。
来源：https://help.aliyun.com/zh/model-studio/text-to-image-prompt （页面有「更新时间」栏位但抓取到的日期值为空，记为日期未标注）

**MiniMax image-01：未找到多格构图表述；参考图通道存在。**
t2i 端点参数表为 `model / prompt / aspect_ratio / width / height / response_format / seed / n / prompt_optimizer`，无 negative 通道、无多格构图说明。i2i 端点官方示例含 `subject_reference: [{ "type": "character", "image_file": ... }]`。
来源：https://platform.minimax.io/docs/api-reference/image-generation-t2i 、https://platform.minimax.io/docs/api-reference/image-generation-i2i （日期未标注）

**Midjourney：未找到多格/sheet 构图表述；参考图机制文档齐备（细节见 B 块）。**
来源：https://docs.midjourney.com/hc/en-us/articles/32040250122381-Image-Prompts 、https://docs.midjourney.com/hc/en-us/articles/36285124473997-Omni-Reference 、https://docs.midjourney.com/hc/en-us/articles/32162917505293-Character-Reference （均日期未标注）

**即梦 Dreamina：未找到官方 prompt 指南级内容。**
`jimeng.jianying.com/features/resource/*` 为官方站上的通用科普/营销型 how-to 页，仅有「避免在提示中使用模棱两可的词」这类泛泛建议，无多格构图、参考图选择、否定表述、人体结构相关表述。即梦在 ModelArk 侧的官方文档目前只见到 Dreamina Seedance 视频系列教程（https://docs.byteplus.com/en/docs/modelark/2291680 ），未见图像侧独立 prompt 指南。
来源：https://jimeng.jianying.com/features/resource/text-to-image-prompts （日期未标注）

---

## 二、B 块：多格拼贴图作为 i2i 参考图的副作用

### 2.1 官方侧：构图继承是被官方点名的参考图行为

**Midjourney 官方直接把「构图」列为 image prompt 会影响的三件事之一。**
Image Prompts 页首句：「Want to influence the content, composition, and colors of your Midjourney creations? Include an image as part of your prompt!」影响强度由 `--iw` 控制（V7 默认 1，范围 0–3）。同页另有一条与拼贴图直接相关的说明：多张图不带文字提示时会被 blend 融合。
来源：https://docs.midjourney.com/hc/en-us/articles/32040250122381-Image-Prompts （日期未标注）

**Midjourney 官方对角色参考图的最佳实践是「单角色」。**
Character Reference 页 Best Practices 第一条：「Use Midjourney Images: For best results, start with an image of a single character created by Midjourney.」第二条：「Limit Character References: While you can use more than one image of the same character, it's often not necessary.」
Omni Reference 页对多主体参考图的表述是有保留的可行性，而非推荐：「Multiple Characters: While you can only use one image as an Omni Reference, you can try using an image that contains multiple characters/people and describe them in your prompt.」——即多主体参考图必须靠文字逐个指认，官方未承诺效果。
来源：https://docs.midjourney.com/hc/en-us/articles/32162917505293-Character-Reference 、https://docs.midjourney.com/hc/en-us/articles/36285124473997-Omni-Reference （均日期未标注）

**Midjourney 官方承认参考图与风格指令互相争夺影响力（风格漂移）。**
Omni Reference 页：「Reinforce the Style: If you want your image in a different style than your reference, mention your desired style at both the start and end of your prompt... With a lower weight you will need to reinforce the physical characteristics you want to preserve using your prompt text.」以及「If you're using high stylize or `--exp` values you may want to also use a higher Omni Reference Weight, as these will all compete for influence.」权重过高的后果同样有官方警告：「it's best to keep your weight below 400, otherwise your results may be unpredictable.」
来源：同上（日期未标注）

**Google 官方对参考图的唯一数量/内容约束落在「单一主体」。**
Veo 侧 Subject image：「You provide up to three images of a single person, character, or product. Veo preserves the subject's appearance in the output video.」Style image 则限定单张。这是本次调研中最接近「参考图应单主体」的官方明文，但它属于视频参考图口径，图像侧未见同类明文。
来源：https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/use-reference-images-to-guide-video-generation （Last updated 2026-08-21 UTC）

**Seedream 官方要求对参考图显式指认「要提取什么」。**
「Reference Target: Clearly describe the elements to be extracted and retained from the reference image」——官方默认参考图内容不会自动被正确取舍，需要 prompt 指认。同页还专门为「参考图内容复杂、难以用文字准确描述」的情况给出补救手段：「visual indicators such as arrows, bounding boxes, or doodles can be used to specify the editing target and its location.」
来源：https://docs.byteplus.com/en/docs/ModelArk/1829186 （Last updated 2026-07-06）

**未找到官方表述的部分**：没有任何一家官方文档直接讨论「把多格/拼贴图作为参考图」这一具体情形的后果。上述条目都是从「参考图会带入构图」「参考图应单主体」「参考图需指认提取目标」三类明文推出的相关证据，本身不是对拼贴参考图的官方判断。

### 2.2 论文侧：构图继承与主体锚定稀释有机制层面的直接证据

**参考图注入会整体复制参考内容而非只取主体（copy-paste 效应）。**
Conceptrol（arXiv:2503.06568，2025-03-09）：「zero-shot adapters inject the reference image as a condition in the attention block with Direct Adding or MM-Attention. To that end, **the reference image is not attached to any explicit text concept**. This often results in a **copy-paste effect** or poor compositional generation.」图 1 说明中给出的具体失败形态即「duplicating the book」——参考图里出现两次的物件在输出里也被复制。
同文对条件强度的描述解释了为什么这不是调参能绕开的：「a low image conditioning strength (IP Scale) in IP-Adapter fails to preserve the concept effectively while increasing the scale causes deviations from the text prompt and leads to a copy-paste effect.」
来源：https://arxiv.org/abs/2503.06568 、https://arxiv.org/html/2503.06568v1 （2025-03-09 v1）

**主体锚定稀释有直接的注意力层证据。**
同文的三条实测观察，第一条正是本议题所指的现象：「In the absence of textual concept constraints, **the attention map for reference images derived from adapters does not focus on the target subject requiring customization**」；第二条给出结果侧后果：「Adapters do well at transferring appearance of reference images within regions of high attention scores」——即注意力落在哪里，外观就被搬到哪里。参考图内含多个高显著性区域时，注意力被分散是这套机制的直接推论。
来源：同上

**供应商自己的技术报告承认「该保留什么」本身是欠定的。**
Seedream 4.0 技术报告：「reference-based generation presents a more challenging trade-off between preservation and creativity. This difficulty arises from **the inherently ambiguous definition of what should be preserved**. In some cases, the target is a person's ID or IP, a particular artistic style, or even an abstract concept.」
来源：https://arxiv.org/html/2509.20427v2 （2025-09）

**证据边界（必须标注）**：Conceptrol 的实测对象是开源适配器架构（IP-Adapter / OminiControl，基座为 Stable Diffusion、SDXL、FLUX）。ArcReel 实际调用的多为闭源商用 API，其参考图注入实现未公开。「同一机制在闭源 API 上同样成立」属**推断**，不是论文结论。可与之对齐的官方信号只有 Midjourney 那条「image prompt 影响 composition」的明文。

---

## 三、C 块：防崩句与文本化反向提示词

### 3.1 正向防崩句（「五官对称、手指完整为五指、肢体比例协调」）

**结论：本次调研在全部可取得正文的官方文档中，未找到任何一家推荐在正向 prompt 中声明五官对称、手指数量或肢体比例。**

逐家核对结果：
- Google：图像最佳实践页七条建议中无解剖学条目；限制页列出的失败模式为语言支持、图像张数、文字渲染、安全拦截，不含解剖崩坏。（https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/capabilities/gemini-image-generation-best-practices ，Last updated 2026-08-21 UTC）
- OpenAI：**有**人体相关指导，但形态与防崩句相反——是**可见的构图性描述**而非质量声明：「For people in scenes, describe scale, body framing, gaze, and object interactions. Examples: "full body visible, feet included," "child-sized relative to the table," "looking down at the open book, not at the camera," or "hands naturally gripping the handlebars." These details help with body proportion, action geometry, and gaze alignment.」（https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide ，2026-04-21）
- BFL FLUX.2、Seedream prompt guide、DashScope 文生图 prompt 指南、MiniMax、Midjourney、即梦：全文无解剖学防崩类建议。
- 阿里 DashScope 是唯一在官方文档里点名「多余的手指」的一家，但它把该词放在**反向通道**：`negative_prompt`「描述不希望在画面中出现的内容，如"模糊"、"多余的手指"等。仅用于辅助优化质量。」（https://help.aliyun.com/zh/model-studio/text-to-image ，日期未标注）

**证据强度定性**：正向防崩句在本次调研范围内属「社区口传无官方背书」。它与早期 Stable Diffusion 生态的正/负向质量词习惯同源，但**本报告未找到任何一手来源证明它在当代模型上仍然有效或无效**——「无官方背书」不等于「已被证伪」，这两件事必须分开陈述。可引用的最接近的官方反向信号是既有报告已记录的两条：Vidu 把 `cinematic dramatic beautiful high quality` 称为「model noise」；Seedream 官方反例把关键词堆叠列为不推荐写法。

### 3.2 文本化反向排除项（「画面避免：水印、多余文字、Logo」写在 prompt 末尾）

这一问题必须拆成两个互不等价的子问题，既有报告的 Q3 只回答了前者。

**子问题一：各供应商官方对「在正文里写排除项」的态度——三分且同厂内部分裂。**
既有报告 Q3 的三分结论仍然成立，本次补充与更新如下：

| 供应商 | 官方口径 | 本次核实的时效 |
| --- | --- | --- |
| Google 图像 | 明确反对，要求正向改写：「Describe what you want, not what you don't: Instead of saying "no cars", describe the scene positively by saying, "an empty, deserted street with no signs of traffic."」 | 核实有效，Last updated 2026-08-21 UTC |
| Black Forest Labs FLUX.2 | **新增**（既有报告未覆盖 BFL）：「**No negative prompts: FLUX.2 does not support negative prompts. Focus on describing what you want, not what you don't want.**」即无 negative 通道且明确要求正向改写 | https://docs.bfl.ai/guides/prompting_guide_flux2 ，日期未标注 |
| OpenAI 图像 | 明确鼓励正文写排除项：「State exclusions and invariants explicitly (e.g., "no watermark," "no extra text," "no logos/trademarks," "preserve identity/geometry/layout/brand elements")」；示例 prompt 中反复出现「No extra text, no watermarks, no unrelated logos」「Constraints: - Original artwork only - No trademarks - No watermarks - No logos」 | 核实有效，2026-04-21 |
| 阿里 DashScope | 模型间分裂：qwen-image 系列支持 `negative_prompt`；「wan2.7-image-pro、wan2.7-image 不支持 `negative_prompt` 参数，对于不希望出现的元素，请在正向提示词中描述（不要出现xxx）。」——**官方对不支持 negative 通道的模型，明确指示把否定句写进正向 prompt** | https://help.aliyun.com/zh/model-studio/text-to-image ，日期未标注 |
| Seedream 图像 | **本次修订既有报告的一处归类**：既有报告把火山归入「鼓励正文写排除句」类，其依据是 Seedance **视频** 2.0 的「约束词」小节。Seedream 4.0–4.5 的**图像** prompt guide 全文无排除项/约束词小节，只有「若其他元素应保持不变，需显式说明」这类保持性指令。火山的「鼓励否定式」结论适用于视频线，不能直接外推到图像线 | https://docs.byteplus.com/en/docs/ModelArk/1829186 ，Last updated 2026-07-06 |
| MiniMax image-01 | 无 negative 通道，官方文档未表态 | 日期未标注 |
| Midjourney | 未找到关于 prompt 内否定表述的官方口径（本次未在 Image Prompts / Omni Reference / Character Reference 三页中见到） | 日期未标注 |
| 即梦 Dreamina | 未找到官方表述 | — |

**子问题二：文本否定在当代模型上是否产生反效果（提及即强化）——有同行评审级证据，方向不利于文本否定。**

- **T2I 模型对正文否定的服从率极低，且失败形态就是「照画不误」。**《Relations, Negations, and Numbers: Looking for Logic in Generative Text-to-Image Models》（arXiv:2411.17066，2024-11-26）在 DALL·E 3 上实测：「**Unmodified prompts specifically prohibiting an entity X invariably led to images showing X.**」未改写否定 prompt 的准确率为 **12.3%**；即便借助 LLM 改写，整体准确率也只到 **40.7%**。文中对改写策略的分解同样值得注意：71% 的改写属于「Addition」（原物体仍被提及、只是补充了别的物体），而这类改写「seems to fail more often than it succeeds. While additional objects are indeed added in the image, the original object often remains」。
  来源：https://arxiv.org/abs/2411.17066 、https://arxiv.org/html/2411.17066v1 （2024-11-26，v1）
- **底层文本编码器对否定近乎无感。**《Vision-Language Models Do Not Understand Negation》（arXiv:2501.09425，v1 2025-01-16，v2 2025-05-13，**CVPR 2025**）：「Our evaluation reveals that modern VLMs struggle significantly with negation, **often performing at chance level**.」该文针对 CLIP 族——即多数扩散模型的文本条件编码器。
  来源：https://arxiv.org/abs/2501.09425
- **必须与之区分的一条：负向通道（negative_prompt）有独立的、有效的作用机制，不能拿它为文本否定背书。**《Understanding the Impact of Negative Prompts: When and How Do They Take Effect?》（arXiv:2406.02965，2024-06-05）刻画的两种行为——Delayed Effect（负向作用发生在正向内容已被渲染之后）与 Deletion Through Neutralization（在潜空间与正向条件相互抵消）——依赖的是 CFG 的独立负向条件分支，**与把否定句写进正向 prompt 是两套机制**。ArcReel 当前在 `lib/prompt_builders.py` 模块 docstring 中已声明放弃 negative_prompt 参数通道、统一走文本尾句，因此这篇论文的结论**不支持**当前实现，反而是当前实现放弃的那条路径的有利证据。
  来源：https://arxiv.org/abs/2406.02965 （2024-06-05）

**「提及即强化」的措辞校正**：论文证据支持的是「否定句常常不被执行，被否定的实体仍然出现」（服从率低），而不是更强的「写了反而比不写更容易出现」（主动强化）。本次未找到任何一手来源做过「写否定 vs 不写」的对照实验并报告前者更差。把结论表述为「提及即强化」会超出证据。

### 3.3 三档归类速览（C 块）

- **有一手证据支持**：Google / BFL「正向改写优于否定」（官方明确）；OpenAI「正文写排除项」（官方明确，且与前者直接冲突）；阿里对无 negative 通道的模型指示写「不要出现xxx」（官方明确）；T2I 对文本否定服从率极低（论文支持，CVPR/arXiv）。
- **社区口传但官方从未背书**：正向解剖防崩句（五官对称、五指完整、肢体比例协调）；正向堆叠质量词。
- **已被官方文档明确否定**：在 FLUX.2 上使用 negative prompt（官方称不支持）；在 Google 图像线上使用 "no X" 式否定（官方要求改写为正向）；在 wan2.7-image / wan2.7-image-pro 上使用 `negative_prompt` 参数（官方称不支持，改写入正向）。

---

## 四、D 块：作为条件输入的参考图，画面本身应当是什么样

本块的问题不是「资产图给人看好不好看」，而是「资产图作为下游 i2i / 参考生视频的条件输入，画面本身该取什么景别、视角、姿态、背景、张数」。来源分两侧：供应商官方对参考图的规格与内容建议，以及 subject-driven / personalized generation 文献里关于参考图的消融。

### 4.1 结论矩阵

| 子问题 | 供应商官方口径 | 论文侧证据 | 可下的结论 |
| --- | --- | --- | --- |
| D1 景别（全身 / 半身 / 胸像） | **未找到**：本轮逐页检索的 7 家（Google、OpenAI、BFL、Seedream、DashScope、MiniMax、Midjourney）均无对参考图景别的表述；即梦沿用第一轮结论——官方文档未见相关内容 | 间接两条：latent 扩散下「相对较小的面部」重建不准（DiffBody）；PhotoMaker 构造训练集时强制「裁剪后面部占画面 >10%」 | 无直接证据支持任一景别；两条相邻证据同向指出**面部的像素占比是变量**，但都不是对「参考图景别」的直接实验 |
| D2 视角（正面 / 45° / 四分之三侧） | **未找到**；「45° 体现立体感」在本轮检索的 7 家官方文档中零命中，即梦无相关内容 | 未找到针对参考图视角的消融；IDAdapter 的机制解释反向说明单张图的 face angle 会被模型吸收 | 未找到。现有模板中的 45° 属**社区口传无官方背书** |
| D3 姿态（A-Pose / T-Pose / 自然姿态） | **未找到**任何图像供应商对参考图姿态的建议；Google 有一句相邻表述：复杂姿态**另给一张姿态参考图** | 未找到 T2I 侧的 A-Pose 消融（A-pose 作为规范表示出现在 3D 角色重建管线，与 i2i 身份锚定不是同一条链路） | 未找到。官方给出的姿态控制路径是**独立的姿态参考图**，不是从身份参考图继承姿态 |
| D4 背景（纯色 vs 带环境） | **未找到**：本轮 7 家均无对参考图背景的明文要求 | **有实证且方向一致**：Subject-Diffusion 先分割主体再编码、去掉位置控制则全指标退化；PhotoMaker 把人物以外区域填随机噪声以「消除背景影响」；PuLID 把背景/光照/构图/风格列为「ID 无关」并要求其保持原模型行为 | 论文侧一致指向「参考图背景是污染源，主流做法是在编码前去掉」。与 B7 的 copy-paste 效应同源 |
| D5 参考图数量（单张 vs 多张多角度） | 官方只给**配额**不给建议：Google 3 Pro 最多 5 张角色图、BFL pro 1MP 输出时最多 8 张、Seedream 4.0–4.5 最多 14 张、MiniMax **每请求仅支持单张** subject_reference | **有定量消融，是本块最硬的一块**：DreamBooth 3–5 张、补充材料 1→5 张消融约 4 张最佳；PhotoMaker 推理期 1→2 张增益最明显、但与文本可控性 trade-off；SynCD 3 张 vs 1 张；IDAdapter 的 N 消融**是训练期超参，不是推理期参考图张数**（见 4.2 的边界说明） | 多张同主体不同角度优于单张；**全部实证都建立在「多张分离的图」上，无一是「一张图里排多格」**。需注意各论文的「张数」并非同一个量：DreamBooth 是微调样本数，PhotoMaker/SynCD 是推理期参考图数，IDAdapter 是训练期混合张数 |

### 4.2 逐条证据

**D1 景别：无直接证据，两条相邻证据都指向面部像素占比。**

DiffBody 给出的是**生成侧**的机制解释：「the VAE used in the LDM cannot reconstruct relatively small faces accurately from low-dimensional latent maps」；其消融显示加入面部专门 refinement 后 ID 指标（越低越好）由 0.403 降到 0.175（Table 3）。这条说的是全身构图下面部在 latent 中占比过小导致身份丢失，**作用位置是输出侧，不是参考图侧**，不能直接当作「参考图不要用全身」的证据。
来源：https://arxiv.org/abs/2401.02804 （2024-01-05）

PhotoMaker 在构造 ID 训练集时的裁剪规则是一条**作者主动施加的约束**：「we crop the image with a larger square box based on the detected face area while ensuring that the facial region can occupy more than 10% of the image after cropping」。这说明作者认为面部占比过低会损害 ID 特征的学习。把它外推到推理期参考图属于**推断**，论文没做这个实验。
来源：https://arxiv.org/abs/2312.04461 、https://arxiv.org/html/2312.04461v1 （2023-12-07）

供应商侧：本轮逐页取得并检索了 Gemini 图像生成页、OpenAI 图像指南与 API 参考、BFL FLUX.2 文档、Seedream prompt guide 与 API reference、MiniMax 图像指南、Midjourney 文档、DashScope 文生图页共 7 家（即梦本轮未能取得新页，沿用第一轮结论），检索「full body / close-up / portrait / bust / framing / 景别 / 全身 / 半身」，**未找到任何一家对参考图应取什么景别的表述**。唯一相邻的官方要求是 B 块已记录的 Midjourney「角色参考图应为单角色」与 Google Veo「a single person, character, or product」，两条都约束**主体数量**而非景别。

**D2 视角：「45° 体现立体感」查无来源。**

对本轮逐页取得的 7 家官方文档检索「45 / three-quarter / 四分之三 / 侧视 / angle of view」，**未找到任何一家把「45° 视角更能体现立体感」或类似说法写进参考图/prompt 指南**（即梦沿用第一轮结论：官方文档未见相关内容）。该说法在本次调研范围内归类为**社区口传无官方背书**。

论文侧同样未找到「参考图取正面 vs 取 45°，下游身份保真孰优」的对照实验。能取到的最接近的一手表述是 IDAdapter 对多图混合的机制解释：「This enriched feature is derived from multiple images under the same identity, so their common characteristics (i.e., the identity information) will be greatly enhanced, while others (such as **the face angle and expression of any specific image**) will be somewhat weakened.」——它说明的是**单张参考图的视角会被模型一并吸收**，而不是哪个视角更好。
来源：https://arxiv.org/abs/2403.13535 （2024-03-20）

另有一条方向相关但本次**未取到可引一手数据**的相邻领域：人脸识别在大 yaw 角下精度下降（pose-invariant face recognition 文献）。这条与「参考图应偏正面」方向一致，但作用对象是识别模型而非扩散条件通道，且本次未核到可直接引用的原文数据，**不作为本报告结论**。

**D3 姿态：官方把姿态控制交给独立的姿态参考图。**

Google 官方在 Character consistency: 360 view 小节的原文包含一句直接相关的表述：「For complex poses, include a reference image of the selected pose.」——即姿态由**另一张专门的姿态参考图**承载，而不是期望从身份参考图里继承。
来源：https://ai.google.dev/gemini-api/docs/image-generation （Last updated 2026-08-10 UTC）

对 A-Pose / T-Pose 检索：本轮 7 家官方文档零命中，即梦无相关内容；subject-driven generation 主线论文（DreamBooth / Textual Inversion / IP-Adapter / InstantID / PuLID / PhotoMaker / Subject-Diffusion / IDAdapter）中，**未找到把「参考图取中性姿态」作为变量做消融的工作**。A-pose 作为规范中间表示确实出现在 3D 角色重建方向（StdGEN 系工作），但那条链路是「单图 → 规范化 → 3D 重建」，与本议题的「参考图 → i2i 条件」不是同一条链路，本次也未取到可引的 verbatim 原文，故不入结论。

一条反向的相关事实：InstantID 明确使用「five facial keypoints (two for the eyes, one for the nose, and two for the mouth)」作为**弱空间条件**，其设计目标正是让参考图的姿态**不被**传递到输出，用户可「generate customized images with various poses or styles」。这说明在该架构下，参考图姿态本身不是下游姿态的决定项。
来源：https://arxiv.org/abs/2401.07519 （v1 2024-01-15，v2 2024-02-02）

**D4 背景：本块最强的一条，方向明确——参考图背景是污染源。**

Subject-Diffusion 的做法是先分割再编码：「we feed the **segmented** subject image into the CLIP image encoder to obtain 256-length patch feature tokens」，分割由 SAM 完成（「these detection boxes are used as input prompt to SAM to obtain their respective object masks」）；消融显示「if we remove the location control (object masks), our model will apparently degenerate over all evaluation metrics」。
来源：https://arxiv.org/abs/2307.11410 （2023-07-21）

PhotoMaker 用随机噪声填充人物以外区域，理由写得很直白：「we filled the image areas other than the body part of a specific ID with random noises **to eliminate the influence of other IDs and the background**」，分割用 Mask2Former 的 person 类全景分割。
来源：https://arxiv.org/abs/2312.04461 （2023-12-07）

PuLID 把「什么应该被参考图带过来、什么不应该」划成了明确的两类：「an ideal ID insertion should alter only ID-related aspects, such as face, hairstyle, and skin color, while image elements not directly associated with the specific identity, such as **background, lighting, composition, and style**, should be consistent with the behavior of the original model」；论文同时承认现状是「Methods with higher ID fidelity tend to induce more severe style degradation」，并以 CLIP-I 量化（PuLID 0.812 vs InstantID 0.680）。
来源：https://arxiv.org/abs/2404.16022 （v1 2024-04-24，v2 2024-10-31）

SynCD 提供了一条评测侧的旁证：现有指标「can struggle with capturing overall quality and **favor methods that copy-paste the target object on a new background**」——即 copy-paste 是这一族方法的已知失败形态，且会被指标奖励。
来源：https://arxiv.org/abs/2502.01720 、https://arxiv.org/html/2502.01720v2 （v2 2025-10-13）

这四条与 B 块的 B7（copy-paste 效应、注意力图不聚焦目标主体）指向同一件事：**参考图中主体之外的所有显著区域都会参与条件化**。B8 的边界同样适用——这些证据都来自开源架构，闭源商用 API 上成立与否属推断。供应商官方对「参考图背景该是什么样」，本轮逐页核对的 7 家**均未找到明文**。

**D5 参考图数量：有定量消融，且消融结论的形状值得注意。**

- **DreamBooth**：「Given only a few (typically **3-5**) casually captured images of a specific subject」；对输入张数「We do not impose any restrictions on input image capture settings and the subject image can have varying contexts」；补充材料含张数消融——训练「5 models per subject with input images ranging from 1 to 5」，最优出现在 4 张附近，论文注明「This number can vary depending on the subject」。同时论文在 limitations 中点名了本议题相关的一个失败形态：「context and subject appearance to become entangled」，即**输入图的语境会与主体外观纠缠**。
  来源：https://arxiv.org/abs/2208.12242 （v1 2022-08-25，v2 2023-03-15）
- **Textual Inversion**：「Using only **3-5** images of a user-provided concept」；本次未在正文中找到张数消融。
  来源：https://arxiv.org/abs/2208.01618 （2022-08-02）
- **PhotoMaker**：「using more images to form a stacked ID embedding can improve the metrics related to ID fidelity」，增益「particularly noticeable」发生在**从 1 张增到 2 张**（Figure 7，DINO 约 47.0 → 49.5），随后边际递减；并明示代价：「there may exist a trade-off between text controllability and ID fidelity」（CLIP-T 随张数下降）。
  来源：https://arxiv.org/abs/2312.04461 （2023-12-07）
- **IDAdapter**：给出了一张张数消融表（Table 2），但**其 N 的语义必须先说清楚**：论文原文为「Changing the number of images N used in **training** process」，且「At the testing and inference stage, we use only **one** image and simply duplicate it N times to serve as the input for the network」。也就是说，**N 是训练期混合的同身份图像张数，不是推理期用户提供的参考图张数**——这条证据不能直接回答「资产图该给下游几张参考图」，它回答的是「多图混合这一机制对姿态/表情锁定的影响」——

  | N（训练期） | ID-Sim | Expr-Div | Pose-Div (pitch) | Pose-Div (yaw) |
  | --- | --- | --- | --- | --- |
  | 1 | 0.602 | 37% | 5.02 | 12.90 |
  | 2 | 0.601 | 58% | 6.97 | 15.39 |
  | 3 | 0.604 | 61% | 7.03 | 15.44 |
  | 4 | 0.603 | 65% | 7.90 | 16.47 |
  | 5 | 0.601 | 64% | 7.88 | 16.42 |

  **ID 相似度几乎不随 N 变化（0.601–0.604），变化的是表情与姿态的多样性**（Expr-Div 37% → 65%，yaw 12.90 → 16.47），作者取 N=4 为最佳平衡。机制解释即 D2 引用的那句：多图的共性（身份）被增强，任一张的角度与表情被削弱。**可外推的部分**是这个机制方向；**不可外推的部分**是「所以推理期该给 4 张」——该论文的推理期就是单图。
  来源：https://arxiv.org/abs/2403.13535 （2024-03-20）
- **SynCD**：同模型下 1 张 vs 3 张参考图，MDINOv2-I 由 0.777 升到 0.822；论文把「缺少同一物体在**不同姿态、背景、光照**下的多图数据集」直接称为该方向的主要瓶颈（「the lack of a dataset comprising multiple images of the same object in diverse poses, backgrounds, and lighting conditions has been a major bottleneck」）。
  来源：https://arxiv.org/abs/2502.01720 （v2 2025-10-13）
- **InstantID**：单图路线的代表——「Given only one reference ID image, InstantID aims to generate customized images with various poses or styles from a single reference ID image while ensuring high fidelity」；多图时的做法是「For multiple reference images, we take the average mean of ID embeddings as image prompt」。
  来源：https://arxiv.org/abs/2401.07519 （2024-01-15）

**这一组消融对本议题最要紧的一点**：全部实证的「多张」都是**多个独立的图像输入**（多张训练图 / 多张 reference image / stacked embedding），**没有任何一篇把多视图拼进一张图再作为单一参考输入**。因此 D5 的「多角度有益」不能被读成「把多角度排进一张资产图有益」——恰恰相反，它与 A 块（官方一致性路径是多参考图通道或多图输出）和 B 块（多格图作参考会继承版式、稀释主体）指向同一个方向。

---

## 五、E 块：宽高比

### 5.1 结论矩阵

| 供应商 | 对**参考图**宽高比的要求 | 参考图比例 ≠ 输出比例时的官方行为说明 | 输出比例可选项 |
| --- | --- | --- | --- |
| Google Gemini | 未找到（未见对输入图比例/分辨率的硬性要求） | **官方明确**：「By default, the model matches the output image size to that of your input image, or otherwise generates 1:1 squares.」；**显式指定 `aspect_ratio` 且与输入图不一致时的处理方式未找到表述**（无裁切/补边/拉伸的明文） | `1:1`、`3:2`、`2:3`、`3:4`、`4:3`、`4:5`、`5:4`、`9:16`、`16:9`、`21:9` |
| OpenAI gpt-image | 未找到对输入图比例的要求；仅取到体积口径「Combined file size 不超过 50MB」 | 未找到 | gpt-image-1：`1024x1024` / `1024x1536` / `1536x1024`；gpt-image-2 接受任意分辨率但「Long edge to short edge ratio must not exceed 3:1」 |
| BFL FLUX.2 | 约束是**像素预算**不是比例：「[pro] API has a 9MP total limit for input+output. At 1MP output you can use up to 8 reference images, at 2MP output up to 7」 | 未找到（`match_input_image` 这一取值仅见于第三方 API 转售文档，**未在 docs.bfl.ai 官方页核到**，不采信） | 「1:1 (Square), 16:9 (Widescreen), 9:16 (Portrait), 4:3 (Classic), 21:9 (Ultrawide)」；「Minimum 64×64, maximum 4MP」「Output dimensions must be multiples of 16」 |
| 字节 Seedream 4.0–5.0 | **官方明确且是本次唯一给出参考图比例数值区间的一家**：参考图宽高比 `[1/16, 16]`，宽高均须 > 14px，总像素 `[196, 6000×6000]`，单张 ≤ 30MB；参考图张数上限 4.0/4.5/5.0 Lite 为 14 张、5.0 Pro 为 10 张 | 常规生成模式未找到自适应说明；仅「图层分解」模式有 `auto`，「generates output based on the input image dimensions and aspect ratio」 | 两种互斥写法：给分辨率档位（1K/1.5K/2K/4K，随版本不同）+ 在 prompt 里用自然语言描述比例；或直接给宽×高像素，比例区间 `[1/16, 16]` |
| 阿里 DashScope | 未找到对参考图比例的要求 | 未找到 | wan2.7-image-pro：1K/2K/4K，自定义 768×768–4096×4096，比例 1:8–8:1；wan2.7-image：1K/2K，768×768–2048×2048；qwen-image-3.0 系列：512×512–2048×2048，比例 1:8–8:1。官方另注明「4K 分辨率与自定义 4096×4096 仅支持无输入图的文生图或多图生成场景」 |
| MiniMax image-01 | 未找到比例要求；**官方明确「Only a single reference image is supported per request.」** | 未找到 | `aspect_ratio` 支持 1:1、16:9、4:3、3:2、2:3、3:4、9:16、21:9 |
| Midjourney | 未找到 | 未找到（Aspect Ratio 页未提及与 image prompt / 参考图的交互） | `--ar #:#`，默认 1:1，不接受小数；官方另注明「Aspect ratio isn't the same as image dimensions」 |

### 5.2 逐条证据与判断

**E1 —— 官方对参考图宽高比：只有 Seedream 给了数值区间，其余各家要么只约束体积/像素预算，要么完全没写。**
Seedream 的参考图规格（格式 JPEG/PNG/WebP/BMP/TIFF/GIF/HEIC/HEIF，宽高比 `[1/16, 16]`，宽高 > 14px，≤30MB，总像素 `[196, 36,000,000]`）见 ModelArk 图像生成 API 参考。
来源：https://docs.byteplus.com/en/docs/ModelArk/1541523 （日期未标注）；prompt guide 同源页 https://docs.byteplus.com/en/docs/ModelArk/1829186 （Last updated 2026-07-06）
BFL 的约束形态是像素预算而非比例。
来源：https://docs.bfl.ai/guides/prompting_guide_flux2 （日期未标注）
OpenAI 的输入图口径本次只取到体积上限（合计不超过 50MB）；输出侧对 gpt-image-2 有「Long edge to short edge ratio must not exceed 3:1」的硬约束。
来源：https://developers.openai.com/api/docs/guides/image-generation （日期未标注）
MiniMax 每请求仅接受单张 subject_reference。
来源：https://platform.minimax.io/docs/guides/image-generation （日期未标注）

**E2 —— 这是本块唯一取到官方明文的一条，而且方向很关键。**
Google 官方（Aspect ratios and image size 小节原文）：「By default, the model matches the output image **size** to that of your input image, or otherwise generates 1:1 squares. You can control the aspect ratio and the size of the output image using the `aspect_ratio` and `image_size` fields under `response_format` when type is set to "image".」即**只要送了参考图且未显式指定比例，输出尺寸/比例就跟随参考图**。这意味着一张 16:9 的角色资产图会把下游未指定比例的生成默认推成 16:9。
来源：https://ai.google.dev/gemini-api/docs/image-generation （Last updated 2026-08-10 UTC）

**其余各家：未找到任何一家写明比例不一致时是裁切、补边还是拉伸。** 逐页核对 Gemini（显式指定比例的分支）、OpenAI 图像指南与 API 参考、BFL FLUX.2 文生图/图像编辑/prompting guide、Seedream prompt guide 与 API reference、MiniMax 图像指南、Midjourney Aspect Ratio 页、DashScope 文生图页共 7 家，均无该表述。**「若官方会裁切、参考图比例就直接决定哪部分内容进入条件」这个假设，在本次调研范围内既没有被官方证实，也没有被否证。**

一条**实现侧的旁证，仅适用于开源架构**：IP-Adapter 的预处理是「we resize the shortest side of the image to 512 and then center crop the image with 512×512 resolution」——即非 1:1 的参考图会被**中心裁切**，边缘内容不进入条件。这解释了「参考图比例可能影响哪部分内容被喂进去」这一机制在技术上是真实存在的，但**不能外推到闭源商用 API**，属推断。
来源：https://arxiv.org/abs/2308.06721 （2023-08-13）

**E3 —— 参考图比例本身是否影响生成质量或锚定效果：未找到任何一手证据。** 官方侧无表述，论文侧本次也未找到以「参考图宽高比」为自变量的消融。

**E4 —— 单主体立像用横版 16:9 承载（大量左右留白）的副作用：未找到任何官方或论文表述。** 能提供的只有三条相邻事实，且都需要标明各自的边界：
1. E2 的 Google 默认继承——16:9 的资产图会把下游未指定比例的输出默认拉成 16:9（官方明确，仅 Google 线）。
2. IP-Adapter 的中心方形裁切——横版参考图的左右两侧不进入条件（论文/开源实现，不可外推）。对居中单主体而言这反而无害，对**横向排布的多格版式则意味着两侧的格子被直接裁掉**。
3. D4 的一组证据——参考图中主体之外的显著区域会参与条件化。留白本身是否构成「显著区域」，本次**未找到任何证据**，不做推断。

「大留白诱导模型填充背景元素」这一说法，在本次调研范围内**未找到任何一手来源**，既无官方表述也无论文实证。

---

## 六、证据强度分级

| # | 结论 | 强度 |
| --- | --- | --- |
| A1 | 无任何供应商官方推荐「同一主体多视图排一张图」 | 未找到（跨 8 家逐家核对，其中 Midjourney/即梦/DashScope/MiniMax 为「文档中无此主题」；三处官方单图多格用例——OpenAI、BFL、Google——全为叙事分格） |
| A2 | Google 的一致性方案是逐角度迭代生成 + 多参考图（3 Pro：6 物体 + 5 角色 + 3 风格；3.1 Flash：10 物体 + 4 角色；3.1 Flash Lite：14 物体、无角色通道） | 官方明确 |
| A3 | Google 明示「模型可能不会生成你要求的确切图片数量」 | 官方明确 |
| A4 | OpenAI 官方支持并示范单图 4 格叙事分格 | 官方明确 |
| A5 | BFL 官方要求漫画分格**逐格分别生成**，一致性靠每格重复角色描述 | 官方明确 |
| A6 | Seedream 把「成套/系列」需求归到多图输出（"a series"/"a set"/指定张数），点名 storyboarding、comic、IP 设计 | 官方明确 |
| A7 | Seedream 成组生成能保持角色与风格一致 | 论文支持（供应商技术报告 arXiv:2509.20427） |
| A8 | Google 为「同一角色多角度」专设 360 view 小节，官方做法是逐角度分图、已生成图回灌为参考，而非单图排版 | 官方明确 |
| A9 | Google 官方存在单图多格模板（「Make a 3 panel comic」），用途限于叙事分格 | 官方明确 |
| B1 | image prompt 会影响输出的 content、composition、colors | 官方明确（Midjourney） |
| B2 | 角色参考图最佳实践是「单角色图」 | 官方明确（Midjourney） |
| B3 | 多主体参考图需在 prompt 中逐个指认，官方未承诺效果 | 官方明确（Midjourney，措辞为 "you can try"） |
| B4 | 参考图与风格指令互相争夺影响力，权重过高结果不可预测 | 官方明确（Midjourney） |
| B5 | 视频参考图官方限定「a single person, character, or product」 | 官方明确（Google Veo；图像侧无同类明文） |
| B6 | 参考图需在 prompt 中显式指认「要提取保留什么」 | 官方明确（Seedream） |
| B7 | 参考图注入未与文本概念绑定时产生 copy-paste 效应，且注意力图不聚焦目标主体 | 论文支持（arXiv:2503.06568，开源适配器架构） |
| B8 | 上述机制同样作用于闭源商用图像 API | **推断**（无官方或论文直接证据；仅 B1 提供方向一致的官方信号） |
| B9 | 官方文档直接讨论「多格拼贴图作参考图」的后果 | 未找到（无任何一家） |
| C1 | 正向解剖防崩句（五官对称/五指/比例）有官方背书 | 未找到（8 家全数无；Midjourney 以已核三页为限）；该做法归类为社区口传无背书 |
| C2 | OpenAI 官方鼓励正文写 "no watermark / no extra text / no logos" | 官方明确 |
| C3 | Google 图像官方要求把否定改写为正向描述 | 官方明确 |
| C4 | FLUX.2 不支持 negative prompt，官方要求正向描述 | 官方明确 |
| C5 | 阿里对不支持 `negative_prompt` 的 wan2.7-image 系列，官方指示在正向 prompt 写「不要出现xxx」 | 官方明确 |
| C6 | Seedream **图像** prompt guide 无排除项/约束词小节（既有报告的「火山鼓励否定式」结论来自 Seedance 视频线） | 官方明确（以「该页无此内容」为准） |
| C7 | 未改写的正文否定 prompt 在 DALL·E 3 上「invariably led to images showing X」，准确率 12.3% | 论文支持（arXiv:2411.17066） |
| C8 | CLIP 族文本编码器对否定的理解接近随机水平 | 论文支持（arXiv:2501.09425，CVPR 2025） |
| C9 | negative_prompt 参数通道有独立且可解释的作用机制（Delayed Effect / Deletion Through Neutralization） | 论文支持（arXiv:2406.02965）——注意该机制不适用于文本尾句写法 |
| C10 | 「提及即强化」（写了反而更容易出现） | 未找到（论文只支持「服从率低」，不支持「反向强化」） |
| C11 | Midjourney / MiniMax / 即梦对 prompt 内否定表述的官方口径 | 未找到 |
| D1 | 供应商官方对参考图应取何种景别（全身/半身/胸像）的表述 | 未找到（本轮逐页核对 7 家；即梦沿用第一轮「文档未见相关内容」） |
| D2 | latent 扩散下面部在画面中占比过小会损害身份重建（生成侧机制，非参考图侧） | 论文支持（arXiv:2401.02804，DiffBody） |
| D3 | PhotoMaker 构造 ID 训练集时强制「裁剪后面部占画面 >10%」 | 论文支持（arXiv:2312.04461）——外推到推理期参考图属**推断** |
| D4 | 「45° 视角体现立体感」有官方或论文依据 | 未找到（本轮 7 家官方零命中，论文侧无对照实验）；归类为社区口传无背书 |
| D5 | 参考图取 A-Pose / T-Pose 等中性姿态更利于下游重新摆姿 | 未找到（供应商与 T2I 主线论文均无该变量的消融） |
| D6 | 官方给出的姿态控制路径是**另给一张姿态参考图**，而非从身份参考图继承 | 官方明确（Google：「For complex poses, include a reference image of the selected pose.」） |
| D7 | 参考图姿态在弱空间条件架构下不被传递到输出（五关键点设计） | 论文支持（arXiv:2401.07519，InstantID） |
| D8 | 主流 subject-driven 方法在编码前**去除参考图背景**（分割/掩码/噪声填充），去掉该控制则全指标退化 | 论文支持（arXiv:2307.11410 Subject-Diffusion、arXiv:2312.04461 PhotoMaker、arXiv:2404.16022 PuLID） |
| D9 | 供应商官方对参考图背景（纯色 vs 带环境）的要求 | 未找到（本轮逐页核对 7 家；即梦沿用第一轮结论） |
| D10 | 参考图张数 3–5 张为主流设定，1→5 张消融最优约 4 张 | 论文支持（arXiv:2208.12242 DreamBooth 及其补充材料；arXiv:2208.01618 同为 3–5 张） |
| D11 | 增加参考图张数提升 ID 保真，增益在 1→2 张最显著，且与文本可控性存在 trade-off | 论文支持（arXiv:2312.04461，DINO 约 47.0→49.5，CLIP-T 下降） |
| D12 | 同身份多图混合的收益是**解除对单张图姿态/表情的锁定**而非提升 ID 相似度（N=1→4：ID-Sim 0.602→0.603，Expr-Div 37%→65%，yaw 12.90→16.47）；**该 N 是训练期超参，论文推理期只用单图复制 N 份**，外推到推理期参考图张数属**推断** | 论文支持（arXiv:2403.13535，IDAdapter），外推部分为推断 |
| D13 | 1 张 → 3 张参考图提升物体身份保真（MDINOv2-I 0.777→0.822） | 论文支持（arXiv:2502.01720，SynCD） |
| D14 | 上述「多张有益」的实证全部建立在**多个独立图像输入**上，无一是「多视图拼进一张图」 | 官方明确 + 论文支持（各论文的输入形态；与 A 块口径一致） |
| D15 | 供应商官方对参考图张数只给配额不给建议（Google 角色图 5 张、BFL 8 张、Seedream 14 张、MiniMax 每请求 1 张） | 官方明确 |
| E1 | Seedream 官方给出参考图宽高比区间 `[1/16, 16]`、宽高 >14px、≤30MB、总像素 `[196, 36,000,000]` | 官方明确（本次唯一给出参考图比例数值区间的一家） |
| E2 | Google：送入参考图且未显式指定比例时，**输出尺寸/比例默认跟随参考图**（原文「matches the output image size to that of your input image」） | 官方明确 |
| E3 | 参考图比例与显式指定的输出比例不一致时的具体行为（裁切/补边/拉伸/忽略） | 未找到（含 Google 显式指定分支在内，本轮 7 家全数无明文） |
| E4 | 非 1:1 参考图在开源适配器实现中被**短边缩放 + 中心方形裁切**，边缘内容不进入条件 | 论文支持（arXiv:2308.06721，IP-Adapter）；闭源 API 上成立与否属**推断** |
| E5 | 参考图宽高比本身影响生成质量或身份锚定效果 | 未找到（官方与论文均无以该项为自变量的证据） |
| E6 | 单主体立像用 16:9 承载（大留白）诱导模型填充背景元素等副作用 | 未找到（无任何一手来源） |
| E7 | 各家输出侧比例可选项与硬约束（Google 10 档；OpenAI gpt-image-2 长短边比 ≤3:1；BFL 5 档、尺寸须为 16 的倍数、≤4MP；Seedream `[1/16,16]`；DashScope 1:8–8:1；MiniMax 8 档；Midjourney `--ar` 不接受小数） | 官方明确 |

---

## 七、与 `arcreel-prompt-best-practices-research.md` 的关系

**补足的部分（该报告完全未覆盖）**
1. **A 块整体**：既有报告的四个调研问题（图像 prompt 写法、i2v 写法、否定式口径、prompt 语言）都不涉及输出画面的**版式/构图形态**。本报告的多格/多视图/sheet 构图口径为新增。
2. **B 块整体**：既有报告只在 i2v 语境下讨论首帧图（"首帧已给主体/场景/风格，prompt 只写运动"），未讨论 i2i 参考图的构图继承与主体稀释。本报告补入官方参考图选择口径与适配器机制论文证据。
3. **供应商覆盖**：既有报告的 8 家为 ArcReel 已接入的供应商，**不含 Midjourney 与 Black Forest Labs**。本报告补入这两家，其中 BFL 提供了本次最干脆的一条否定式官方口径（FLUX.2 不支持 negative prompt）。
4. **正向防崩句**：既有报告未涉及。本报告逐家核对后给出「未找到官方背书」的结论。

**修订/细化的部分**
1. **火山的否定式归类需要按图像/视频线拆分**。既有报告 Q3 把火山归入「类型 C：官方鼓励在正文里直接写禁止/排除约束句」，依据是 Seedance 2.0 视频 prompt 指南的「约束词」小节。本次核对 Seedream 4.0–4.5 **图像** prompt guide（Last updated 2026-07-06）全文，**未见排除项/约束词小节**。既有报告的结论对火山视频线成立，对火山图像线**无据**。ArcReel 的资产图走图像线，引用时须注意这一分界。
2. **既有报告 Q3 的「共同隐含底线」可以再收紧一档**。既有报告的表述是「否定式不是万能，且几家明确约束非 100% 可控」；本报告补入的论文证据把「不是万能」量化到了具体数量级（未改写否定 prompt 准确率 12.3%）。
3. **既有报告 Q3 未区分的一件关键事**：negative_prompt **参数通道** 与 prompt 正文里的**文本否定**是两套机制。既有报告的三分类是按「供应商是否提供 negative 通道 / 是否推荐正文写否定」组织的，未讨论两者的效力是否可以互相推断。本报告给出的答案是不能：arXiv:2406.02965 的机制解释只覆盖参数通道；`lib/prompt_builders.py` 现有实现走的是文本尾句，因此该论文不为其背书。
4. **时效核验**：既有报告引用的 Google 图像最佳实践页 URL（`cloud.google.com/gemini-enterprise-agent-platform/...`）现 301 重定向至 `docs.cloud.google.com/...`，内容仍在，Last updated 2026-08-21 UTC，「Describe what you want, not what you don't」原文未变。OpenAI cookbook 页更新至 2026-04-21，「State exclusions and invariants explicitly」原文未变。既有报告 Q3 中这两条不需要修订。

---

## 八、对 #2058 四类布局的直接启示

本节只陈述上述证据指向什么，不对保留/删除/改形做裁决。

**关于「一张图排多格」这一形态本身**
- 证据 A1/A5/A6 指向：把「同一主体的多视图」放进一张图，在本次调研范围内**没有任何官方指南背书**；三家有官方多格章节的供应商（OpenAI、BFL、Google）示范的都是**叙事分格**，且 BFL 的官方做法是逐格分别生成；Seedream 官方把「成套设计」需求明确导向多图输出而非单图多格。
- 证据 A2/A3/A8 指向：Google 线上，同一角色跨视角的官方路径是逐角度迭代生成加多参考图回灌；同时官方明示生成数量不可保证——「四格」「三视图」这类对格数的硬性要求，在官方口径下不是可承诺项。
- 证据 A9 指向：Google 官方确有单图多格模板，但其适用面被限定在叙事分格（comic / storyboard），与资产 sheet 的「同主体多视角」是两类不同需求，官方在同一页上把后者导向了 A8 的迭代做法。
- 与议题「每一格构图是否有下游消费者」的判据的关系：本调研**不涉及** ArcReel 内部消费链，无法为该判据提供外部证据。它能提供的是另一侧的事实——即便某一格有消费者，把它排进同一张图也不是官方推荐的实现方式；官方推荐的是多图/多参考图。这两条是独立的，判据成立与否不由本报告决定。

**关于多格图进入 i2i 参考链**
- 证据 B1（Midjourney 官方：image prompt 影响 composition）与 B7（论文：copy-paste 效应 + 注意力不聚焦目标主体）指向同一方向：参考图的整体版式与其中的全部显著区域都会参与条件化，#1803 观察到的「分格布局被下游继承」与这两条一致。
- 证据 B2/B3/B5/B6 指向：官方对参考图的期望形态是**单主体、且在 prompt 中被显式指认**。多格图两条都不满足——多个显著区域，且现有模板未对参考图内的哪一格作指认。
- 证据 B8 的边界必须一并转述：论文证据来自开源适配器架构，闭源 API 上成立与否属推断。

**关于 `_*_GUARD` 中的正向防崩句**
- 证据 C1 指向：五官对称 / 五指完整 / 肢体比例协调这类声明，在 8 家官方文档中均无背书。可对照的官方做法是 OpenAI 式的**可见构图性描述**（"full body visible, feet included"、"hands naturally gripping the handlebars"），以及阿里把「多余的手指」放进 negative 通道。
- 需要与之分开看待的是 `_SCENE_GUARD` 的「画面中没有人物出镜」与 `_PRODUCT_GUARD` 的保真声明：这两条不是解剖防崩，而是**正向改写形态的排除项**，恰好与 Google/BFL 官方「Describe what you want, not what you don't」的口径同形——证据 C3/C4 支持这种写法，反对的是 "no X" 形态。

**关于 `_NEGATIVE_TAIL_*` 文本尾句**
- 证据两极且分歧发生在供应商之间，不在时间上：OpenAI（C2）明确鼓励，Google（C3）与 BFL（C4）明确要求改写为正向，阿里（C5）在无参数通道时指示写入正向。**「画面避免：水印、多余文字、Logo」这一写法的有效性取决于目标模型**，ArcReel 的「无 backend 锁定、纯文本拼接」设计意味着同一串尾句会同时发往口径相反的模型。
- 证据 C7/C8 指向：即便在鼓励写排除项的模型上，文本否定的服从率也远非可靠。C10 同时限定了这条结论的上界——没有证据表明写了比不写更差。
- 证据 C9 指向：模块 docstring 中「image backends 大多 silent 丢弃 negative_prompt」的判断，与本次核实的官方事实部分吻合（FLUX.2、MiniMax image-01、wan2.7-image 确实无该参数）、部分不吻合（qwen-image 系列有正式支持的 `negative_prompt` 字段；可灵的同名字段属视频线，不构成图像线反例）。放弃参数通道这一决定的代价是放弃了 arXiv:2406.02965 所刻画的那套有独立机制的削减能力。

**关于「四类反向尾句各自定义、内容相同也不合并」这一纪律**
- 本调研未找到任何外部证据支持或反对该纪律——它是仓库内部的常量组织约定，与供应商口径无关。证据能提供的只有一条相邻事实：官方口径按**目标模型**分裂（C2 vs C3/C4/C5），而非按**图种**分裂；现有常量的切分维度是图种。

---

## 九、对形状与比例决策的支撑度判定

本节只回答一个问题：**上述证据是否足以支撑直接选定具体的景别 / 视角 / 姿态 / 背景 / 宽高比？** 不对形状本身作裁决。

**判定是分裂的，逐项如下。**

| 维度 | 证据能否支撑直接选定 | 依据 |
| --- | --- | --- |
| 背景（纯色 vs 带环境） | **能，方向明确** | D8 是本次唯一一组多篇独立论文同向、且带消融的证据：主流 subject-driven 方法在编码前一律去背景，去掉该控制则全指标退化；PuLID 把背景明确归入「ID 无关、应保持原模型行为」。与 B7 的 copy-paste 效应同源。唯一保留项是 B8 的边界——证据来自开源架构，闭源 API 属推断。这一项**不需要 A/B 实测即可定向**，实测只用于确认幅度 |
| 参考图张数 / 是否单图 | **能，但只能支撑「多张独立图 > 单张拼合图」这一层** | D10–D14 有四篇论文的定量消融（其中 IDAdapter 的 N 是训练期超参，不可直接读作推理期张数）。但它们支撑的命题是「多个独立图像输入优于一个」，**不支撑「资产图内部该排几个视图」**——后者在证据里根本不存在对应实验。对已定的「多格 → 单图」前提，这组证据是加强项；对「单图之后是否要出多张单图」，证据支持但属于新的形状问题 |
| 景别（全身 / 半身 / 胸像） | **不能，必须实测** | D1 零直接证据。两条相邻证据（D2/D3）只支撑一句弱得多的话：面部的像素占比是一个真实变量。它无法在「全身」「半身」「胸像」之间排序，因为占比同时受输出分辨率与主体在画面中的位置影响 |
| 视角（正面 / 45°） | **不能，必须实测** | D4 完全查无来源，现有模板中的 45° 属社区口传。论文侧连方向性证据都没有 |
| 姿态（A-Pose / 自然姿态） | **不能，必须实测** | D5 零证据。D6/D7 只说明一件事：官方与主流架构都不指望从身份参考图继承姿态（姿态另给参考图 / 用弱空间条件剥离）。这条**削弱了「参考图姿态很重要」这一前提本身**，但不能用来在 A-Pose 与自然姿态之间排序 |
| 宽高比 | **不能直接选定，但有一条官方约束必须先纳入** | E3/E5/E6 全部未找到。唯一的官方硬事实是 E2：在 Google 线上，送参考图且未显式指定比例时输出比例跟随参考图——这是一条**下游行为约束**，意味着资产图比例的选择会外溢到未指定比例的下游调用。选哪个比例仍需实测，但「资产图比例是下游默认值」这件事不需要实测就已成立 |

**总结论**：证据足以支撑**两件事**——参考图应去除无关背景（D8）、多角度的收益属于「多张独立图」而非「一张多格图」（D14）；并给出**一条必须纳入设计的官方约束**——资产图比例会成为下游未指定比例时的默认输出比例（E2）。**景别、视角、姿态、以及具体选哪个宽高比这四项，本次调研找不到任何一手证据可以支撑直接拍板，只能靠实测 A/B 决定。**

这一分裂本身是可解释的：景别、视角、A-Pose 这三项是**美术资产管线的行业惯例**（角色三视图、转身图），而不是扩散模型文献会去消融的变量；文献消融的变量是张数、背景处理、编码粒度。惯例未必错，但在本次来源纪律下，它拿不到一手背书，**继续沿用等于承认它是未经验证的默认值**。

**若要做 A/B，本报告能提供的唯一外部输入是「什么该被固定住」**：实测时应把背景（去背景）与参考图形态（单主体单图）固定为常量，只让景别 / 视角 / 姿态 / 比例作为变量——因为前两项已有证据、后四项没有。实测的判据也不应是「资产图本身好不好看」，而应是 D 块全部证据共用的那个判据：**下游生成对身份的保真度，以及下游姿态/构图相对参考图的自由度**（对应 IDAdapter 的 ID-Sim 与 Pose-Div 这一对指标形态）。
