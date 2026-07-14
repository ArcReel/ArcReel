# 图像/视频生成提示词官方最佳实践调研报告

> 用途：驱动 ArcReel（小说→短视频，i2v 图生视频流水线）剧本生成写作指导（LLM 产出 `image_prompt` / `video_prompt`）的迭代。
> 对应议题：https://github.com/ArcReel/ArcReel/issues/1092（设计对齐决议见该 issue 评论区）。
> 范围：8 家 ArcReel 实际接入的供应商，仅采信**官方**文档站/官方博客/模型卡/官方 Help Center。查不到官方指南的，如实记为"未找到官方指南"，不以第三方教程或模型记忆顶替。
> 调研日期：2026-07-13
> 硬约束落实：本报告每条结论均附官方来源 URL；凡官方未明示者一律标注"未找到官方指南/未见官方明文"。少数第三方镜像内容仅在明确标注"未验证"的前提下作旁注，不进入结论。

---

## 一、跨供应商共性结论（按 4 个调研问题组织）

### Q1 图像 prompt 总体编写建议

**共识 1：要素分层是普遍范式，且各家层次高度趋同。** 几乎所有有官方指南的厂商都把图像 prompt 拆成"主体 → 环境/场景 → 光线 → 镜头语言（景别/视角/机位/镜头）→ 风格/氛围"这一层次。
- Google：`景别 + 主体 + 环境 + 光线 + 机位 + 镜头` 模板（https://ai.google.dev/gemini-api/docs/image-generation ）。
- 阿里万相：进阶公式 = `主体 + 场景 + 风格 + 镜头语言 + 氛围词 + 细节修饰`，另给"景别/视角/镜头类型/风格/光线"五维词典（https://help.aliyun.com/zh/model-studio/text-to-image-prompt ）。
- 火山 Seedream：`主体 + 行为 + 环境`，风格/色彩/光影/构图为可选补充（https://www.volcengine.com/docs/82379/1829186?lang=zh ）。
- MiniMax image-01：官方示例分层为 `主体+服装 → 景别/视角 → 环境 → 摄影风格/年代 → 介质/质感 → 写实度`（https://platform.minimax.io/docs/api-reference/image-generation-t2i ）。
- OpenAI GPT-image：推荐固定顺序 `场景/背景 → 主体 → 关键细节 → 约束`，并写明用途（https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide ）。
- 可灵：图像走 MVL（自然语言+图像），以"场景导演式写作"为纲，面部参考公式含 `面部特征+发型+服装+动作+背景+光线+氛围`（https://kling.ai/quickstart/klingai-image-3-model-user-guide ）。

**共识 2：官方一致主张"具体化、可视化"，反对空泛与无意义堆砌质量词。** 多家用几乎相同的正反例句法：把抽象词换成可见的视觉细节。
- Google：`Be specific: More details give you more control`；"fantasy armor" → "ornate elven plate armor, etched with silver leaf patterns…"（https://cloud.google.com/gemini-enterprise-agent-platform/models/capabilities/gemini-image-generation-best-practices ）。
- 阿里：`提示词描述越完整、精确和丰富，生成的图像品质越高`（https://help.aliyun.com/zh/model-studio/text-to-image-prompt ）。
- 可灵：把 "magic" 换成 "swirling blue energy particles with an ethereal glow"（https://kling.ai/blog/kling-ai-prompt-guide ）。
- Vidu（作视频参考图时）：`cinematic dramatic beautiful high quality` 被官方称为 "model noise（模型噪声）"，应改为可执行视觉约束（https://www.vidu.com/blog/image-prompt-generator-ai-video ）。

**共识 3：叙述性自然语言句 > 关键词/标签堆叠。**
- 火山 Seedream 有最直白的正反例：推荐"一个穿着华丽服装的女孩，撑着遮阳伞走在林荫道上，莫奈油画风格"，避免"一个女孩，撑伞，林荫街道，油画般的细腻笔触"（https://www.volcengine.com/docs/82379/1829186?lang=zh ）。
- Google、xAI、MiniMax 的官方示例 prompt 全为完整叙述句（分别见上引 URL 与 https://docs.x.ai/developers/model-capabilities/images/generation ）。
- 例外/更中立：OpenAI 明确说"极简、叙述段落、JSON、指令式、标签式都能用，只要意图和约束清晰"，不强制某一种（https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide ）。

**共识 4：几乎没有厂商给出"字数/长度"的量化建议。** 详略度是定性口径（越具体越可控），而非数值。
- 无量化长度建议：Google、火山、MiniMax、可灵、OpenAI（均"未找到长度数值建议"，见各家逐家摘录）。
- 唯一的"长 prompt 取向"官方信号：OpenAI 明确"长 prompt 可行"（`Long prompts can work well`），并指出 GPT-image-2 擅长处理结构化长描述；阿里 qwen-image-2.0-pro 官方示例走"数百字超长结构化详述"路线（https://help.aliyun.com/zh/model-studio/text-to-image ）。
- 唯一见到的具体字数区间来自 Vidu 的"Q3 官方提示词指南（50–150 字）"——但**仅见于 CSDN/gitcode 镜像，未定位官方原始 URL，不作结论**（见分歧点第 5 条）。

> **对 ArcReel 的启示（仅陈述共性事实，不作改稿）**：官方普遍支持"要素分层 + 具体可视化 + 叙述句"，且普遍反对空泛质量词堆砌。"过于精简"（如 scene 缺光线/氛围层）与主流官方口径相悖；补齐主体/环境/光线/镜头语言/氛围层次有跨厂商官方依据。长度上官方几乎不设硬指标，倾向"该具体的地方具体"。

---

### Q2 i2v 图生视频 prompt 总体建议（首帧已给）

**共识 1（最强、最一致）：首帧已提供主体/场景/风格，prompt 应优先描述运动/变化与运镜，避免重复复述静态画面；必要时补充环境动态或风格过渡。** 这是本次调研跨厂商一致性最高的一条，且直接命中 ArcReel 的 i2v 路径。
- Google：`Prompt for motion only. Your source image already provides the subject, scene, and style. Focus your prompt on the motion…`；并明确"不要重新描述人物/背景/光线，冗余会让模型混乱"，人物用"the subject/the woman/he/she"等泛称（https://cloud.google.com/vertex-ai/generative-ai/docs/video/best-practice ）。
- 阿里万相：`图像已经确定了主体、场景与风格，因此提示词主要描述动态过程及运镜需求`，图生视频公式 = `运动 + 运镜`（https://help.aliyun.com/zh/model-studio/text-to-video-prompt ）。
- MiniMax：I2V 基础公式 = `首帧主体 + 运动/变化`；`prompt describes how the scene evolves from this static image into motion`（https://platform.minimax.io/docs/guides/video-prompt , https://platform.minimax.io/docs/guides/video-generation ）。
- Vidu：i2v 公式 = `subject motion + camera movement + scene change + style`，核心是"描述运动而非复述画面"（https://www.vidu.com/ai-image-to-video ）。
- OpenAI Sora：图片作首帧锚点，`The model uses the image as an anchor for the first frame, while your text prompt defines what happens next`（https://developers.openai.com/cookbook/examples/sora/sora2_prompting_guide ）。
- 可灵：i2v 官方示例 prompt 即"镜头拉远，女生微笑"（运镜+主体动作），场景由图给定（https://www.klingai.com/document-api/api/video/3-0-omni/image-to-video ）。
- 火山 Seedance：i2v 专篇给出"人物动作类 Prompt = 主体 + 主体从首帧到尾帧的详细动作变化描述 + 运镜"（https://www.volcengine.com/docs/82379/1951250?lang=zh ）。
- xAI（弱信号）：官方博客一句 `Give it a starting image, describe the motion…`，示例聚焦运动与运镜、不复述画面（https://x.ai/news/grok-imagine-video-1-5 ）。

**共识 2：运镜要显式且"一次一种为宜"，多家提供官方运镜词表/指令。**
- MiniMax：i2v API 有 15 种 `[command]` 方括号运镜指令（[Push in]/[Pull out]/[Pan left]…/[Static shot] 等），适用于 Hailuo 2.3 / 2.3-Fast；同一 [] 内建议最多 3 个（https://platform.minimax.io/docs/api-reference/video-generation-i2v ）。
- 可灵：自然语言运镜表（push-in/pan/tilt/tracking…）+ 结构化 `camera_control` 六轴协议（horizontal/vertical/pan/tilt/roll/zoom，[-10,10]）（https://kling.ai/blog/kling-ai-prompt-guide , https://www.klingai.com/document-api/api/video/3-0-omni/image-to-video ）。
- 阿里：用"镜头推进/镜头左移"控制，不要变化时写"固定镜头"；且 wan2.7 已取消 `shot_type` 参数改由自然语言控制（https://help.aliyun.com/zh/model-studio/text-to-video-prompt ）。
- 火山 Seedance 2.0：`一个镜头里尽量只指定 1 种运镜方式，不要同时要求推拉摇移`（https://www.volcengine.com/docs/82379/2222480?lang=zh ）。
- Vidu：加镜头运动更电影感，但"避免同一句里堆多个同时运动指令"，用方向性语言+强度限定词（subtle/gentle/minimal）（https://www.vidu.com/blog/image-to-video-ai ）。

**共识 3：时长/动作量匹配——官方普遍主张"短镜头聚焦单一、可完成的动作"，"动作宜低缓连续"是部分供应商（如火山 Seedance）的具体建议而非普遍口径；时长由 API 参数定、不能靠 prompt 文字改。**
- 火山 Seedance 2.0（最明确）：`优先选用缓慢、轻柔、连贯的细微动作，尽量规避狂奔、大跳、剧烈翻滚等高爆发、大动态动作`；且"模型对精确时间（如 0–3 秒）的支持不稳定"，建议用"镜头1/2/3"分镜按事件顺序描述而非秒级硬卡（https://www.volcengine.com/docs/82379/2222480?lang=zh ）。
- Google：`For short videos, dedicate each prompt to a single, focused moment`，不要一条串 A→B→C（https://cloud.google.com/vertex-ai/generative-ai/docs/video/best-practice ）。
- OpenAI：动作要"简单、单一、可完成"并给以秒计节拍（"takes four steps… in the final second"）；时长/分辨率是"容器"参数，`will not change based on prose like 'make it longer'`；4 秒镜头约容纳 1–2 句对白（https://developers.openai.com/cookbook/examples/sora/sora2_prompting_guide ）。
- Vidu：i2v 在 4–6 秒最稳，推向 8 秒+复杂源图易漂移；face-heavy 图用最小运动 prompt（https://www.vidu.com/blog/image-to-video-ai ）。
- MiniMax：官方教程建议把单次运镜控制在合理的 5–6 秒（https://platform.minimax.io/docs/guides/video-prompt ）。

**共识 4：t2v 与 i2v 指南在多数官方站点是清晰区分的。** Google、阿里、MiniMax、火山、Vidu、可灵均有可对应到 i2v 的官方内容（见逐家摘录标注）；仅 xAI 无成体系指南（只有一句博客措辞）。

> **对 ArcReel 的启示（仅陈述共性事实）**："video_prompt 描述该镜头时长内的动作/运镜"与官方"短镜头聚焦单一、可完成动作 + 显式运镜"的主流口径一致；"低缓连续"仅是部分供应商（如火山 Seedance）的具体建议，"环境音"仅在供应商明确支持时适用，均不宜归为全体主流口径。"9 秒镜头动作仅 28 字、缺环境动态层"的问题，可对照官方"运动+运镜+（环境/氛围动态）"分层来衡量——官方既反对复述静态画面，也反对堆多个同时剧烈动作。

---

### Q3 否定式表述的官方口径

这是各家分歧最大的一题，可归为三种官方处理方式：

**类型 A：无独立 negative 通道，官方明确"只写想要的、用正向描述替代否定"。**
- Google 图像（Gemini/Nano Banana，正是 ArcReel 首帧用途）：逐字建议 `Describe what you want, not what you don't`，示例把 "no cars" 改写为 "an empty, deserted street with no signs of traffic"（https://cloud.google.com/gemini-enterprise-agent-platform/models/capabilities/gemini-image-generation-best-practices ）。
- Vidu：全部生成接口无 `negative_prompt` 字段，官方走"正向、具体、克制 + 显式声明要保留/静止"（如 "background remains static"）（https://platform.vidu.com/docs/image-to-video , https://www.vidu.com/blog/image-prompt-generator-ai-video ）。
- MiniMax：所查 image/i2v/s2v schema 均无 negative 参数，教程通篇正向叠加描述（https://platform.minimax.io/docs/api-reference/video-generation-i2v ）。
- OpenAI Sora：无 negative 参数；核心口径"你不描述的细节 Sora 会自行编造"，即靠正向描述（https://developers.openai.com/cookbook/examples/sora/sora2_prompting_guide , https://developers.openai.com/api/reference/resources/videos/methods/create ）。

**类型 B：提供独立 negative 通道，但官方要求"通道内也用描述式、避免 no/don't"，且倾向把否定写进正向。**
- Google 视频（Veo）：有独立 `negativePrompt` 参数，但官方要求填要排除的实体名词（"wall, frame"）而非 "no walls"，并明确 `Not recommended: using instructive language or words such as "no" or "don't"`（https://cloud.google.com/vertex-ai/generative-ai/docs/video/video-gen-prompt-guide ）。
- 可灵：有 `negative_prompt` 字段（≤2500 字符），但官方同时建议"通过正向提示词中的负向句子补充负向提示信息"（https://www.klingai.com/document-api/api/video/3-0-omni/image-to-video ）。
- 阿里：**模型内部分裂**——qwen-image 系列与 wan2.7 视频**支持** `negative_prompt`（≤500 字符，官方定义"仅用于辅助优化生成质量"）；但 wan2.7-image-pro/image **不支持**，官方直接要求"在正向提示词中描述（不要出现 xxx）"（https://help.aliyun.com/zh/model-studio/text-to-image , https://help.aliyun.com/zh/model-studio/text-to-video-api-reference ）。

**类型 C：官方鼓励在正文里直接写"禁止/排除"约束句（否定式作为一等手段）。**
- 火山 Seedance 2.0：专设"约束词"小节，示例含"保持无字幕/避免生成任何文字或字幕/不要生成Logo/不要生成水印"，甚至"视频全程禁止出现…同款分身、双胞胎效果"；同时坦承"无法 100% 避免，只能降低概率"（https://www.volcengine.com/docs/82379/2222480?lang=zh ）。
- OpenAI 图像（GPT-image）：官方**鼓励**显式写排除项/不变量，`State exclusions and invariants explicitly (e.g., 'no watermark,' 'no extra text,' 'no logos')`，编辑用"change only X + keep everything else the same"（https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide ）。

**共同的隐含底线：否定式（无论哪种通道）都不是万能，且几家明确"约束非 100% 可控"（火山明说；Vidu/Google 图像侧以"改写为正向"规避，Google 视频侧使用描述式 negativePrompt）。** xAI 则对否定式完全无官方口径（接口无 negative 通道、也无相关指导文字）。

> **对 ArcReel 三个冻结决策的直接关联（仅提供事实依据，不给改稿）**：
> - 「不要混入过去/未来事件」类禁令句——官方证据两极：图像侧（Google/Vidu/MiniMax/Sora）主流是"改写为正向描述、少用否定词"；但火山与 OpenAI 图像又明确"可把排除项写进正文"。没有任何一家把"叙事时序/记忆闪回"作为可 prompt 控制项，这类问题本质是"单帧静态图无法渲染时序"，与 negative 通道无关。
> - 反例词族黑名单——官方无一家采用"词族枚举黑名单"机制；主流是"描述你想要的画面"（正向替代），或"写要排除的实体名词"（Veo 式）。词族枚举拦截在官方指南中无先例支撑。
> - 是否保留否定句——取决于目标模型：ArcReel 若首帧走 Gemini/Seedream 类，官方倾向正向改写；若走支持 negative_prompt 的模型（可灵/qwen-image/wan2.7 视频），可用独立通道但仍以"描述式"为佳。

---

### Q4 prompt 语言（中文 vs 英文）

**共识：几乎没有任何一家官方声明"某语言效果更佳"。** 各家普遍"支持中英文/多语言"，但对"该用哪种写 prompt"基本沉默。
- 声明"支持中英文/多语言"但不表态优劣：阿里（图像/视频 prompt "支持中英文"，并给中英双写对照；HappyHorse "支持任何语言输入"）（https://help.aliyun.com/zh/model-studio/text-to-video-api-reference , https://help.aliyun.com/zh/model-studio/happyhorse-image-to-video-api-reference ）；火山 Seedance "支持中英文"（https://www.volcengine.com/docs/82379/2168087?lang=zh ）；MiniMax 中英双站、prompt 无语言限制（未表态）；可灵中英文示例并用（未表态）；Vidu 中英双站、prompt 均一等支持（未表态）。
- 唯一有"语言机制"明文的是 Google 旧线 Imagen：`Imagen models support only English natively`，非英文靠翻译转英文，可显式设 zh/zh-CN/zh-TW 等；**但这是已弃用且迁移期限已过的旧线**（Google 官方标注为 deprecated，建议在 2026-06-30 前迁移至 gemini-2.5-flash-image），Gemini 3 新图像线与 Veo 均未见中英文优劣明文（https://cloud.google.com/vertex-ai/generative-ai/docs/image/set-text-prompt-language ）。
- OpenAI 通用口径（Help Center，非针对 GPT-image/Sora）：`optimized for English, but trained on multilingual data`，可用目标语言，但未给中英文优劣结论（https://help.openai.com/en/articles/6742369 ）。
- 与"prompt 语言"易混但需区分的是"输出语言/对白语言"：可灵对白支持中/英/日/韩/西 5 语（https://kling.ai/blog/kling-ai-prompt-guide ）；Vidu Q3 音频输出支持英/日/中（https://www.vidu.com/vidu-q3 ）；火山 Seedance 2.0 要求"台词语言统一、避免中英混用"（https://www.volcengine.com/docs/82379/2222480?lang=zh ）。这些都是**视频内语音语言**，不是 prompt 书写语言建议。

> **对 ArcReel 的启示**：官方层面没有"中文 vs 英文哪个更好"的可引用定论。国产模型（火山/阿里/可灵/MiniMax/Vidu）官方文档中文并重、示例中文充分；海外模型（Google/OpenAI/xAI）示例以英文为主但未强制。ArcReel 的 prompt 语言选择缺少官方硬依据，属可自行决策项。

---

## 二、逐家摘录（官方来源 + 关键结论）

### 1. Google（Gemini 3 Pro/3.1 Flash Image；Veo 3.1）

官方来源：
- 图像 prompting：https://ai.google.dev/gemini-api/docs/image-generation
- 图像 best practices：https://cloud.google.com/gemini-enterprise-agent-platform/models/capabilities/gemini-image-generation-best-practices
- 语言设置（Imagen 旧线）：https://cloud.google.com/vertex-ai/generative-ai/docs/image/set-text-prompt-language
- Veo prompt guide：https://cloud.google.com/vertex-ai/generative-ai/docs/video/video-gen-prompt-guide
- Veo best practices（含 i2v 独立章节）：https://cloud.google.com/vertex-ai/generative-ai/docs/video/best-practice
- Veo 3.1 blog：https://cloud.google.com/blog/products/ai-machine-learning/ultimate-prompting-guide-for-veo-3-1

要点：图像=要素分层+具体化+叙述句，"more details = more control"，无长度数值建议。i2v 官方有独立章节，明确"只描述动作、不复述画面、人物用泛称"，运镜分 Camera/Subject/Environmental 三类。否定式：**图像无 negative 通道、只写正向**；**视频有 `negativePrompt` 但要写排除实体名词、忌 no/don't**。语言：仅 Imagen 旧线有"原生仅英文+翻译"明文，新线无优劣声明。
查不到：图像字数/长度量化建议；Gemini 3 新图像线与 Veo 的中英文优劣明文；时长-动作量量化对照。

### 2. 火山方舟 / 豆包（Seedream 4.0/4.5/5.0；Seedance 1.5 Pro/2.0）

官方来源：
- Seedream 图像 prompt 指南：https://www.volcengine.com/docs/82379/1829186?lang=zh
- Seedance 1.5 Pro 提示词指南：https://www.volcengine.com/docs/82379/2168087?lang=zh
- Seedance 2.0 提示词指南：https://www.volcengine.com/docs/82379/2222480?lang=zh
- Seedance 2.0 教程（i2v 首帧/首尾帧/role 配置）：https://www.volcengine.com/docs/82379/2291680?lang=zh
- Seedream 助力 Seedance 生视频最佳实践（i2v 专篇）：https://www.volcengine.com/docs/82379/1951250?lang=zh

要点：图像=`主体+行为+环境`+可选美学；叙述句优于关键词（有正反例）；新模型可少描述、"简洁精确优于华丽堆叠"。i2v 专篇公式=`主体 + 首帧到尾帧动作变化 + 运镜`，运镜"简要描述即可"；2.0 强调"优先低缓连续小动作、规避高爆发大动态"、"一镜只 1 种运镜"、用"镜头1/2/3"替代秒级时间码。否定式=**正文写"约束词"**（保持无字幕/不要 Logo/禁止双胞胎），并承认非 100% 可控。语言：支持中英文，未表态优劣；台词要求语言统一。
查不到：图像语言优劣明文；独立命名的"Seedance 2.0 Pro"指南（官方为 2.0/2.0 Fast/2.0 Mini）。
备注：火山文档站为 JS 渲染，正文经浏览器渲染后读取。

### 3. xAI（Grok Imagine Image/Video）

官方来源（均已核对，确认无 prompt 指南）：
- Imagine 总览：https://docs.x.ai/developers/model-capabilities/imagine
- 图像生成：https://docs.x.ai/developers/model-capabilities/images/generation
- 图生视频 i2v：https://docs.x.ai/developers/model-capabilities/video/image-to-video
- 视频生成：https://docs.x.ai/developers/model-capabilities/video/generation
- 博客 Video 1.5：https://x.ai/news/grok-imagine-video-1-5

**结论：xAI 未发布任何专门 prompt 编写指南**，docs.x.ai 全为 API 参考。可用的官方信号仅：i2v 一句 `Give it a starting image, describe the motion…` + 官方英文叙述句示例（动作/运镜/氛围导向）；参数硬约束（duration 1–15s、宽高比、分辨率）。**否定式：接口无 negative 通道、官方无任何相关指导文字。语言：官方零建议（示例均英文）。** 网传结构公式/negative 用法均来自第三方镜像，未采信。

### 4. OpenAI（GPT-image-2；Sora 2 / 2 Pro）

官方来源：
- GPT Image prompting 指南：https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide
- 图像 API 指南：https://developers.openai.com/api/docs/guides/image-generation
- Sora 2 prompting 指南：https://developers.openai.com/cookbook/examples/sora/sora2_prompting_guide
- 视频生成指南：https://developers.openai.com/api/docs/guides/video-generation
- create video API：https://developers.openai.com/api/reference/resources/videos/methods/create
- 多语言使用（Help Center）：https://help.openai.com/en/articles/6742369

要点：图像=固定顺序`场景→主体→细节→约束`+写明用途；极简/叙述/JSON/标签式皆可；"长 prompt 可行"但生产环境优先清晰模板+小步迭代。i2v=图作首帧锚点、prompt 定义"之后发生什么"（`input_reference` 传图）；动作要简单+给秒级节拍；**短 prompt=更多创意自由、长 prompt=更强控制**；时长是"容器"参数不能靠 prose 改，最长 20s。否定式：**图像官方鼓励显式写 no watermark/no logos 等排除项**；Sora 靠正向描述、无 negative 参数。语言：仅 Help Center 通用"英文优化+多语言"，无 GPT-image/Sora 专门中英文结论。

### 5. 生数科技 Vidu（Q1/Q2 图像；Q3 turbo/pro、2.0 视频）

官方来源：
- 图生视频 API：https://platform.vidu.com/docs/image-to-video
- 参考生图 API：https://platform.vidu.com/docs/reference-to-image
- 官方 i2v 运动 prompt 博客：https://www.vidu.com/ai-image-to-video
- 图像 prompt 工作流博客：https://www.vidu.com/blog/image-prompt-generator-ai-video
- i2v 实践博客：https://www.vidu.com/blog/image-to-video-ai
- Q3 模型页：https://www.vidu.com/vidu-q3

要点：Vidu **无集中式官方 Prompt Guide**，指导散落在 API 模型卡+官方博客。图像（作视频参考图）=具体化面部/服装/光照方向，反对堆无意义质量词（"model noise"）。i2v 公式=`subject motion + camera movement + scene change + style`，描述运动而非画面、prompt 简单聚焦、避免多动作并发；4–6s 最稳、face-heavy 用最小运动。否定式=**无 negative 通道**，走正向+显式声明静止项。语言：中英双站均一等支持，无优劣声明（Q3 多语言指音频输出）。参数注意：`movement_amplitude` 对 Q2/Q3 无效，运动幅度需靠 prompt 文字控制。
未验证旁注：中文流传《Vidu Q3 官方提示词指南（结构公式+50–150 字+音效句尾）》仅见 CSDN/gitcode 镜像，未定位官方原始 URL，不作结论。

### 6. 阿里 DashScope / 百炼（Qwen-image 2.0；万相 wan2.7；HappyHorse 1.0）

官方来源：
- 文生图 prompt 指南：https://help.aliyun.com/zh/model-studio/text-to-image-prompt
- 文/图生视频 prompt 指南（含 i2v 公式）：https://help.aliyun.com/zh/model-studio/text-to-video-prompt
- qwen-image/万相文生图使用：https://help.aliyun.com/zh/model-studio/text-to-image
- 万相 2.7 图像 API：https://help.aliyun.com/zh/model-studio/wan-image-generation-and-editing-api-reference
- 万相 2.7 文生视频 API：https://help.aliyun.com/zh/model-studio/text-to-video-api-reference
- 万相 2.7 图生视频 API（i2v）：https://help.aliyun.com/zh/model-studio/image-to-video-general-api-reference
- HappyHorse i2v API：https://help.aliyun.com/zh/model-studio/happyhorse-image-to-video-api-reference

要点：图像基础`主体+场景+风格`/进阶`+镜头语言+氛围+细节`+五维词典；"越完整精确丰富品质越高"；qwen-image-2.0-pro 走超长结构化详述。i2v 公式=`运动+运镜`（"图像已确定主体/场景/风格"），不变化写"固定镜头"，wan2.7 取消 shot_type 改自然语言，`duration` 2–15s、宽高比跟随首帧。否定式=**分模型**：qwen-image/wan2.7 视频支持 `negative_prompt`（辅助优化）；wan2.7-image-pro/image 不支持、要求正文写"不要出现 xxx"；HappyHorse 无 negative。语言：图/视频 prompt "支持中英文"、给中英双写对照、HappyHorse "支持任何语言"，但**未见"中文优化/更佳"官方原话**。

### 7. MiniMax（image-01；Hailuo 2.3/Fast；S2V-01）

官方来源：
- 图像生成指南：https://platform.minimax.io/docs/guides/image-generation
- 图像 t2i API：https://platform.minimax.io/docs/api-reference/image-generation-t2i
- 视频生成指南：https://platform.minimax.io/docs/guides/video-generation
- 图生视频 i2v API（15 种运镜指令）：https://platform.minimax.io/docs/api-reference/video-generation-i2v
- S2V API：https://platform.minimax.io/docs/api-reference/video-generation-s2v
- 官方 Hailuo prompt 教程：https://platform.minimax.io/docs/guides/video-prompt

要点：图像=分层堆叠描述性要素（主体+景别+环境+风格+介质+写实度），image-01 上限 1500 字符、`prompt_optimizer` 图像端默认 false。i2v 基础`首帧主体+运动/变化`、精确`+运镜+美学氛围`；**官方 15 种 `[command]` 方括号运镜指令**适用 Hailuo 2.3，同 [] 内≤3 个；单次运镜控制 5–6s；i2v 上限 2000 字符、`prompt_optimizer` 视频端默认 true。否定式=**无 negative 通道、正向叠加描述**（唯一"禁止"是敏感内容 1026 拦截）。语言：中英双站，无优劣声明。

### 8. 快手可灵 Kling（Image O1、v3 Omni Image；Video v2.5T/v2.6/v3/v3 Omni/O1）

官方来源：
- 官方视频提示词指南：https://kling.ai/blog/kling-ai-prompt-guide
- Image 3.0 用户指南（Prompt Handbook）：https://kling.ai/quickstart/klingai-image-3-model-user-guide
- Image 3.0 Omni 指南：https://kling.ai/quickstart/klingai-image-3-omni-user-guide
- 图生视频 API（3.0 & Omni）：https://www.klingai.com/document-api/api/video/3-0-omni/image-to-video
- quickstart 汇总：https://kling.ai/quickstart
- 形容词表：https://kling.ai/blog/best-kling-ai-adjectives-video-prompts

要点：官方主张"清晰的场景导演式写作，而非秘密公式"。图像走 MVL（自然语言+图像），给分场景公式（编辑"保持其余不变，把X改为Y"、人脸参考含面部/发型/服装/动作/背景/光线/氛围）；主体要具体可视化。视频六要素`主体+动作+场景+镜头+光影氛围(+对白)`；运镜双轨（自然语言表 + `camera_control` 六轴协议）；节奏/时长用 `multi_prompt` 逐分镜设定（各分镜和=总时长，≤6 分镜）。i2v：首帧`image`（尾帧可选）+ prompt 补动作/运镜，官方示例"镜头拉远，女生微笑"。否定式=**有 `negative_prompt`（≤2500）但官方建议在正向里写负向句补充**。语言：中英文示例并用、无优劣声明；对白支持中/英/日/韩/西 5 语（属输出语言）。
备注：无逐型号差异化写作规范；网传"主体+运动+背景"i2v 字面公式官方页未见，未采信。

---

## 三、分歧点与例外（单独列出）

1. **否定式处理三分天下（最大分歧）**：
   - 只写正向、无 negative 通道：Google 图像、Vidu、MiniMax、OpenAI Sora。
   - 有独立 negative 通道但要求描述式：Google Veo（`negativePrompt`）、可灵（`negative_prompt`）、阿里（qwen-image/wan2.7 视频支持，wan2.7-image 不支持）。
   - 鼓励正文写排除句：火山 Seedance（"约束词"）、OpenAI 图像（`no watermark/no logos`）。
   - 完全无口径：xAI。
   → **同一厂商内也可能分裂**：Google（图像无/视频有）、阿里（模型间有/无）。ArcReel 的否定式策略无法"一刀切"，需按目标模型区分。

2. **详略度取向的方向性差异**：多数厂商"越详细越可控"（Google/阿里/火山/MiniMax）；但 **OpenAI Sora 明确"短 prompt=更多创意自由、长 prompt=更强控制但压创意"**，是唯一把"详略度"表述为双向权衡而非单调"越详越好"的官方口径（https://developers.openai.com/cookbook/examples/sora/sora2_prompting_guide ）。

3. **叙述句 vs 关键词的强度不同**：火山给出最强正反例（关键词堆叠为反例）；OpenAI 最中立（标签式/JSON 式也认可）。

4. **i2v 指南完备度差异极大**：阿里、火山、MiniMax、Google、可灵、Vidu 有可用的 i2v 官方内容；**xAI 仅一句博客措辞，无成体系指南**；OpenAI 的 i2v 专属段落也仅一段（image input 作首帧锚点），其余动作/运镜/时长为 t2v/i2v 通用未分列。

5. **Vidu"Q3 官方提示词指南"来源存疑（例外，需核实）**：中文侧署名"Vidu API 开放平台"的《结构公式+速查模板（50–150 字、音效句尾、i2v 分阶段结构）》仅在 CSDN/gitcode 镜像可见，**未能定位 platform.vidu.cn/.com 或 vidu.com/blog 官方原始 URL**。本报告未将其纳入结论；若 ArcReel 要引用其"50–150 字"等具体口径，建议先向 Vidu 官方核实原页。

6. **语言优劣普遍空白（例外是没有例外）**：8 家中无一家声明"中文/英文哪个更好"。唯一有语言机制明文的 Google Imagen 属已弃用旧线。这与国产模型"中文优化"的坊间印象不符——**官方文档层面并无此声明**，属 ArcReel 的自主决策项。

7. **参数陷阱（影响 prompt 策略的官方例外）**：
   - Vidu：`movement_amplitude` 对 Q2/Q3 无效 → 运动幅度只能靠 prompt 文字（https://platform.vidu.com/docs/image-to-video ）。
   - 阿里 wan2.7：取消 `shot_type` → 镜头控制改由自然语言（https://help.aliyun.com/zh/model-studio/text-to-video-prompt ）。
   - MiniMax：`prompt_optimizer` 图像端默认 false、视频端默认 true → 是否自动改写 prompt 的默认行为相反（https://platform.minimax.io/docs/api-reference/video-generation-i2v ）。
   - 阿里：`prompt_extend` 默认 true，但 wan2.7-image 不支持、改用 `thinking_mode`（https://help.aliyun.com/zh/model-studio/text-to-image ）。

---

## 四、方法与约束说明

- 本报告仅采信官方文档站/官方博客/模型卡/官方 Help Center；第三方教程、社区经验、镜像转载一律排除（唯一提及的 Vidu 镜像内容已明确标注"未验证、不作结论"）。
- 火山、MiniMax 中文站、部分阿里/Vidu 文档为 JS 前端渲染，`web_fetch` 仅得导航壳，正文均经浏览器渲染后读取；MiniMax 以内容更完整的英文官方站为准（与中文站描述一致）。
- 凡官方未明示的问题，均标注"未找到官方指南/未见官方明文"，未凭模型记忆补写供应商说法（硬约束）。
- 本次不含 ArcReel 指导语改稿方案——仅交调研事实与共性结论，改稿由主仓库会话在对齐后另行制定。
