# 图像与视频提示词的供应商指南取样

2026-07-13 为 [#1092](https://github.com/ArcReel/ArcReel/issues/1092) 调研了八家供应商的一手指南，重点考察图像描述、图生视频的首帧用法、排除项写法以及语言选择。ArcReel 实际执行的规范见[图片提示词写作指南](../../lib/prompts/prompt_templates/templates/partials/shared/image_prompt_writing_guide.md)与[视频动作写作指南](../../lib/prompts/prompt_templates/templates/partials/shared/action_writing_guide.md)。

## 结论与边界

- 图像提示词通常从主体、环境、构图、光线等可见元素组织。说明要保留哪些已有细节，能比重复整段描述更精确地限定编辑。不同模型对描述长度的建议存在差异。
- 图生视频已有首帧提供主体、环境和风格，视频文字的重点是动作、时间进程和镜头运动。需要保持不动的元素可以明确说明，避免让提示词与输入图冲突。
- 排除项的支持按模型而异。Google 图像指南强调正向改写，Veo 为排除项提供独立参数并建议填实体名词，Seedance 2.0 视频指南及 GPT Image 指南则允许正文直接写约束。后续[资产图取样](arcreel-asset-sheet-prompt-research.md)没有在 Seedream 图像线上找到与 Seedance 视频线相同的明文。
- 调研未发现足以普遍比较中文与英文效果的一手资料。支持某种提示词书写语言、支持某种输出对白语言是两个不同问题。

## 按供应商复核

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

本次取样未将无法定位原始页面的 Vidu Q3 字数建议作为证据。产品能力与输入参数可能随版本演进，复核当前兼容性时请从[官方索引](../api-docs/)入手。原始归纳与逐项证据边界见[调研快照](https://github.com/ArcReel/ArcReel/blob/fed952b5f25fb48a9b26e70a5878705c9ecbf988/docs/research/arcreel-prompt-best-practices-research.md)。
