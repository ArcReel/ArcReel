# 「分镜板生视频」业界实现形态调研

> 用途：为 ArcReel 的一个数据建模决策提供依据——宫格（grid_4/6/9）应建模为「分镜路线内部的装配优化选项」，还是「独立的生成路线」。本文只陈述一手事实，不替业主拍板。调研日期：2026-08-03，所有「访问日期」均为当日。产品与模型更新很快，标注为「未查到一手来源」的结论应视为可能已过时。

## 一、核心结论（先给答案）

**没有查到任何主流视频生成模型的公开 API，原生接受"一整张多格分镜板图"作为条件输入、由模型一次性内部拆解成多镜头并输出一条连续多镜头视频。** 逐一核实 Sora、Vidu、可灵、即梦/Seedance、海螺、Pika、Runway、Google Veo/Flow 后，业界现存的"分镜板生视频"能力全部可以归入以下三类之一，且往往是同一产品身上叠了两三层：

1. **参考生视频（reference-to-video / r2v）**：喂 1～9 张左右的图片作为角色/风格/主体一致性锚点，模型输出**一条连续视频**，图片不对应具体镜头或时间段。这是 Vidu、可灵、Seedance、海螺 API 层的共同底层能力，术语边界清晰、跨厂商一致。
2. **首尾帧（first/last frame）**：恰好 2 张图定义起止状态，模型插值生成中间运动，仍是**一条连续视频**。Kling、Veo 3.1、海螺、即梦都有此能力，是 r2v 的一个更窄的特例（图片数固定为 2 且有明确的时间语义）。
3. **UI 层时间轴/分镜编排（storyboard editor）**：Sora 的 storyboard 编辑器、Google Flow 的 Scenebuilder、即梦的"故事创作"模式、Runway 的 Multi-Shot Video Recipe，都是**产品或编排层**功能——用户按镜头/卡片组织内容，系统仍是逐镜头调用底层生成能力（文生图/图生视频/图生图），再做拼接与转场，而不是把一张多格图喂给模型一次出片。

**唯一贴近 ArcReel 宫格思路（强图片模型出多格大图 → 切割 → 逐格图生视频）的实践**，是第三方博客记录的 gpt-image-2 + Seedance 2.0 工作流：先让 gpt-image-2 生成一张 3×3 九宫格分镜图（一张图内九个画格），再逐格喂给 Seedance 做图生视频——但这是**社区/教程层面的手工组合工作流，不是任何厂商官方产品化的单一 API 或按钮**（见五.6）。换句话说，ArcReel 现有的宫格实现，本身已经站在了行业已知实践的前沿，而不是落后于某个业界已有的"原生分镜板生视频"能力。

## 二、逐产品 / 逐模型 findings

### 1. OpenAI Sora（Sora 2 / storyboard）

**产品 UI 层**：Storyboard 是 Sora 2 网页版给 ChatGPT Pro 用户的编辑器，可在 composer 中点击 "storyboard" 进入。用户可以"从零逐帧搭建视频——就像 Sora 1 的分镜一样，或者只描述一个场景、选择时长，让 Sora 生成一份可编辑的详细分镜"。分镜编辑器把多条 prompt 卡片（描述场景/角色/动作的 caption card）按时间顺序组织成"a single multi-shot clip"（单条多镜头短片）；此外也支持直接用关键帧图片代替文字描述，"Sora 生成一条连接这些静止图片的视频"。另有独立的 **Stitching（拼接）** 功能，用于把多条**已经分别生成好的**片段连接成一条最长 60 秒的视频——这是与 storyboard 编辑器并列的另一个功能，说明 OpenAI 自己也区分"单次生成的多镜头分镜"与"多个独立生成结果的拼接"这两种不同机制。
来源：OpenAI Help Center《Creating videos with Sora》https://help.openai.com/en/articles/12460853-creating-videos-with-sora （直接抓取被 403 拦截，以上内容为搜索引擎收录的官方页面摘要，未能逐句核对全文，访问日期 2026-08-03）。

**模型 API 层**：Sora 目前没有公开的第三方开发者 API 文档描述 storyboard 的底层输入契约（Sora 2 的 API access 主要面向视频生成本身，storyboard 是 ChatGPT/Sora 产品内的编辑体验）；未查到官方文档说明 storyboard 编辑器提交给底层模型时具体是"一次调用多镜头联合生成"还是"逐卡片调用后模型内部拼接"。这一点**未查到一手来源确认**，只能确认产品层面呈现为"一次生成得到一条多镜头视频"。

### 2. Vidu（生数科技 / Shengshu）

**产品 UI 层**：Vidu 官方产品把该能力命名为 **Reference to Video（参考生视频）**，是与文生视频、图生视频并列的独立生成模式。

**模型 API 层**：官方 API 文档明确：viduq3-mix / viduq3-turbo / viduq3 / viduq2 / viduq1 / vidu2.0 等模型的 `reference2video` 接口接受 **1～7 张图片**（viduq2-pro 若同时上传视频则降为 1～4 张）；文档原文："The model will use the provided images as references to generate a video with consistent subjects"——图片是**主体/风格一致性参考**，不对应具体镜头或时间段，文档中**没有出现"storyboard"或"分镜"的表述**。输出是一条连续视频（时长上限随模型不同，viduq3 系列支持到 16 秒 1080p）。
来源：Vidu 官方 API 文档 https://platform.vidu.com/docs/reference-to-video ，访问日期 2026-08-03。

### 3. 可灵 Kling（快手）

**产品 UI 层**：可灵开放平台把图生视频细分为"基于首帧""基于首尾帧""参考生视频""视频编辑"等并列模式；中文产品页对"参考生视频"的营销表述接近"多图参考"，但**未查到官方将其称为"分镜板生视频"**。

**模型 API 层**（经阿里云百炼文档镜像可灵官方 API 参数核实，阿里云为可灵的转售接入商，文档描述的是可灵原生接口契约）：
- 文生视频：无需图片，输出 3～15 秒单条连续视频。
- 图生视频（首帧）：1 张图，作为"首帧图片"，输出单条连续视频。
- 图生视频（首尾帧）：2 张图，分别是起始帧与结束帧，模型在两帧间插值，输出单条连续视频。
- 参考生视频（多图参考）：最多 7 张参考图（与多图主体 element_list 数组长度之和不超过 7；若同时提供 feature video 则降为 4 张），文档原文强调这些图是"参考图片"用于主体/风格一致性，**不按镜头/时间段一一对应**，输出仍是单条连续视频（3～15 秒，或与 feature video 组合时 3～10 秒）。
来源：阿里云百炼《可灵视频生成 API 参考》https://help.aliyun.com/zh/model-studio/kling-video-generation-api-reference/ ，访问日期 2026-08-03。可灵官方开发者站点 https://klingai.com/document-api/apiReference/ 与 https://kling.ai/document-api/apiReference/model/multiImageToVideo 多次直接抓取被 403/446 拦截，未能逐句核对官方原文，以上参数以阿里云镜像文档为准，标注为**半一手来源**（阿里云描述的是可灵官方接口的透传参数，非阿里云自研，但终究不是可灵官网原文）。

### 4. 即梦 Dreamina / Seedance（字节跳动）

**产品 UI 层**：即梦官网（jimeng.jianying.com）提供"故事创作"模式：用户输入故事文本，即梦先自动生成**分镜脚本**（拆解成多个分镜），确认后**逐个分镜分别生成对应画面**，最后"自动将分镜图片合成视频，包括转场效果和基础动画"——即：文本 → 分镜脚本 → 逐格生图 → 逐格（或逐段）转视频 → 拼接+转场，是**产品编排层**的多步流水线，不是模型一次性吃一张多格图出片。即梦另有独立的"首尾帧"输入方式（首帧图+尾帧图控制单条视频的起止状态），是与故事创作模式并列的能力。
来源：搜索引擎聚合的第三方教程与百科对即梦官网功能页面的转述（CSDN、知乎教程、baike 等），**未找到即梦官方帮助中心对"故事创作"工作流的逐句原文**，以上流程描述属于二手转述，请注意标注：这是引自二手报道，未直接核实即梦官方文档原文。访问日期 2026-08-03。

**模型 API 层（Seedance，火山引擎/Volcengine Ark 官方 API）**：官方于 2026-04-14 上线 Seedance 2.0 系列 API，支持文本/图片/音频/视频四种模态输入，图生视频支持首帧或首尾帧控制，多模态参考支持 0～9 张参考图 + 0～3 段参考视频 + 0～3 段参考音频联合生成。这与 Vidu/可灵的"参考生视频"是同一范式：多图作为一致性参考，输出单条视频，不是分镜格到镜头的映射。**Seedance 2.5**（据 2026-06-23 火山引擎 FORCE 大会公开信息，尚在推送上线阶段）宣称原生输出 30 秒连续视频、支持最多 50 个全模态参考素材联合生成——本质仍是"参考素材数量与单条视频时长的提升"，**没有查到官方文档说明这是"多格分镜板图"或"逐镜头时间轴条件输入"的新契约**，此结论基于第三方科技媒体报道（SegmentFault、品玩、新浪财经），属于二手来源，火山引擎官方 Ark 文档的具体参数页多次抓取失败（404/重定向到导航页），未能核实一手参数表，**标注为未查到一手来源**。
来源：《火山引擎：Seedance 2.0API 服务全面开放》https://www.stdaily.com/web/gdxw/2026-04/14/content_502009.html （二手媒体报道，转引火山引擎官方发布内容）；火山引擎 Ark 文档入口 https://docs.volcengine.com/docs/82379/1951250 （仅抓到导航页，未能读取参数正文）。访问日期 2026-08-03。

### 5. 海螺 Hailuo（MiniMax）

**产品 UI 层**：未查到"分镜/storyboard"作为官方独立产品功能的一手表述；搜索结果显示海螺的产品定位是"文字/图片直接生成视频，跳过传统的脚本-分镜-拍摄流程"，即产品自我定位反而是**绕开**分镜环节，而非提供分镜编排工具。

**模型 API 层**：官方 API 文档（video-generation-v2-create）明确输入内容用角色（role）区分：`first_frame` / `last_frame`（各至多 1 张）与 `reference_image`（至多 9 张）/ `reference_video` / `reference_audio`，且**首尾帧模式与多模态参考模式互斥**，文档原文："图生视频与多模态参考生视频互斥"。输出是"一个连续视频片段"，无分镜/多格图/多镜头时间轴的输入概念。
来源：MiniMax 开放平台文档中心《创建视频生成任务》https://platform.minimaxi.com/docs/api-reference/video-generation-v2-create ，访问日期 2026-08-03。

### 6. Pika

**产品 UI 层**：Pika 的 **Scene Ingredients（场景食材）/ Pikascenes** 功能允许上传多张图（角色/道具/服装/背景）并融合成一个镜头；营销文案中出现"把分镜转化为详细 prompt"（translate storyboards into detailed prompts）这类表述，但这只是"用 storyboard 这个词做营销类比"，功能本质是**单镜头**的多参考图合成，不产出多镜头视频，也没有多格图输入。
来源：Pika 官方相关页面聚合信息（pikartai.com 等三方镜像页面较多，**未定位到 pika.art 官方一手文档原文**，以上内容基于搜索引擎摘要，标注为可能不完全一手）。访问日期 2026-08-03。

**模型 API 层**：Pika API（fal.ai 等平台转发）的 pikascenes 接口参数为图片 URL 列表 + prompt + 画幅/时长等，同样是多图参考单镜头生成，无分镜格概念。

### 7. Runway

**产品 UI 层 / API 层（合一）**：Runway Dev 官方文档提供一个显式命名为 **Multi-Shot Video** 的 "Recipe"（配方/编排层封装接口，而非底层基础模型本身）。它有两种模式：
- **auto 模式**：提交一段故事 prompt，由 Runway 内部把故事拆解为若干镜头。
- **custom 模式**：用户提供 3～5 个镜头的镜头列表，每镜头含 `prompt`（3～512 字符）与 `duration`（秒），Runway"打磨并组装"这些镜头，保持用户指定的顺序。
接口返回一个 task id，轮询 `GET /v1/tasks/{id}` 获取最终输出视频 URL——**对外只暴露一次 API 调用**，但官方文档并未说明内部是否对每个镜头分别调用 Gen-4/4.5 基础模型再拼接，还是有真正意义上的单次多镜头联合生成模型；"Recipe"这个命名本身暗示这是搭建在基础模型之上的编排层封装，但**这一点未查到官方明确声明，标注为未确认**。
来源：Runway Dev 官方文档《Multi-Shot Video》https://docs.dev.runwayml.com/recipes/multi-shot-video/ ，访问日期 2026-08-03。

### 8. Google Veo / Flow

**模型 API 层（Gemini API 官方文档）**：Veo 3.1 支持：`image`（首帧，单张）、`lastFrame`（尾帧，单张，仅 Veo 3.1 支持插值）、`referenceImages`（至多 3 张，用于风格/内容引导，`VideoGenerationReferenceImage` 类型）。文档明确"Videos per request: 1"——**每次请求输出一条连续视频**，文档中没有"storyboard"或"多镜头单次生成"的表述或参数。
来源：Google AI for Developers《Generate videos with Veo 3.1 in Gemini API》https://ai.google.dev/gemini-api/docs/veo ，访问日期 2026-08-03。首尾帧插值功能另见 Google Cloud 文档《Generate videos using first and last video frames》https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/generate-videos-from-first-and-last-frames ，访问日期 2026-08-03。

**产品 UI 层（Flow）**：Flow 是 Google 面向影视创作者的产品层工具，内置 **Scenebuilder**，官方博客称之为"your in-Flow storyboard"——用户在其中把**单独生成的镜头**组装成完整叙事，可对已有镜头做编辑/延展（保持角色一致性、动作连续），但底层每个镜头仍是分别调用 Veo 生成，Scenebuilder 是**产品编排/时间轴 UI**，不是模型一次性吃多格分镜图出片的能力。Flow 的"Ingredients to Video"支持每次 prompt 最多 3 张参考图，与 Veo API 的 `referenceImages` 对应。
来源：Google 官方博客《Introducing Flow: Google's AI filmmaking tool designed for Veo》https://blog.google/innovation-and-ai/products/google-flow-veo-ai-filmmaking-tool/ ，访问日期 2026-08-03。

### 9. 依托 gpt-image-1/2 的上层产品（第三方工作流，非官方产品功能）

OpenAI 官方没有把"分镜板"作为 gpt-image-1/2 图像 API 的产品化功能来宣传；gpt-image-2 本质仍是通用图像生成/编辑模型。但多篇第三方教程（Venice.ai、Alici、CrePal、Rewarx 等）记录了一个手工工作流：**用 gpt-image-2 一次性生成一张 3×3 九宫格图（单张图内含 9 个画格，各代表一个镜头），再把这张图切割，逐格喂给 Seedance 2.0 做图生视频**，用于制作预告片/广告分镜。这与 ArcReel 现有的 grid_4/6/9 实现在结构上高度一致：强生图模型出多格联合大图（保画风/角色一致）→ 切割 → 逐格 i2v。**但这只是社区总结的组合工作流，不是任何厂商官方发布的单一 API 或按钮功能**，不存在一个厂商声称"这是我们的分镜板生视频原生能力"。
来源：Venice.ai 博客《How to Create AI Trailers with GPT Image 2 + Seedance 2.0》https://venice.ai/blog/how-to-create-ai-trailers-gpt-image-2-seedance ；Rewarx《Generate Storyboard Frames with GPT Image 2》https://www.rewarx.com/blogs/generate-storyboard-frames-gpt-image-2 。均为第三方教程/二手内容，非厂商一手文档，访问日期 2026-08-03。

## 三、术语辨析

| 术语 | 业界通行含义 | 一手依据 |
|---|---|---|
| **reference-to-video / 参考生视频 (r2v)** | 输入若干张（通常 1～9 张）角色/场景/道具的静态参考图，作为**主体与风格一致性锚点**，模型据文字 prompt 生成**一条连续视频**；图片不对应具体镜头或时间段，是与文生视频、图生视频并列的第三种生成模式。跨厂商术语与实现高度一致。 | Vidu 官方 API（`reference2video`，1～7 张）；可灵"参考生视频"（≤7 张）；Seedance 多模态参考（0～9 张）；MiniMax `reference_image`（≤9 张，与首尾帧互斥）——均为一手文档确认 |
| **first/last frame / 首尾帧** | r2v 的一个特例：恰好 2 张图，分别语义化为"起始状态"与"结束状态"，模型在两帧间插值生成运动过程，输出仍是一条连续视频。 | 可灵图生视频（首尾帧）；Google Veo 3.1 `image`+`lastFrame`；MiniMax `first_frame`/`last_frame`；即梦首尾帧输入（一手/半一手来源见上文各节） |
| **storyboard-to-video / 分镜板生视频** | **没有统一、跨厂商一致的技术含义**，是一个跨越"产品营销词"与"UI 编排层功能"的模糊地带，实际所指随产品不同分裂为：①Sora：把多条文字/关键帧"卡片"按时间顺序编排、由模型输出一条多镜头连续片段（产品自称"single multi-shot clip"，但底层是否单次联合生成未经一手确认）；②即梦"故事创作"：文本拆解为分镜脚本 → 逐格生图 → 逐格转视频 → 拼接转场（明确是编排层多次调用+拼接）；③Runway Multi-Shot Video：镜头列表 → Runway 编排层组装成一条视频（是否内部多次调用底层模型未经官方确认）；④Google Flow Scenebuilder：把已生成的独立镜头在时间轴上组装（编排 UI，非单次模型输入）；⑤Pika/gpt-image 类：仅在营销文案里类比"storyboard"这个词，功能实际是单镜头多图参考合成，或需要手工分步完成的社区工作流。**没有任何一手文档描述"模型 API 原生接受一整张多格分镜板图作为单一输入、内部自动拆分镜头并输出连续多镜头视频"这种契约。** |
| **中文「分镜板生视频」** | 在中文语境（即梦"故事创作"、各类教程）里，这个说法几乎总是指"AI 辅助生成分镜脚本/分镜图，再逐格转视频，最后拼接"的**多步流水线体验**，即上表④的编排层含义，而不是"一张分镜图喂给模型一次出片"。 | 即梦官方产品页描述（经二手转述，见上文第 4 节，未直接核实即梦官方一手原文，标注为参考） |

## 四、对 ArcReel 建模决策的含义（供参考，不替业主拍板）

以下是本次调研得到的证据倾向，供决策参考：

1. **没有找到任何一手证据支持"整板分镜图直接喂给视频模型、一次性输出多镜头连续片段"这种原生能力存在于任何主流产品或公开 API。** 所有名为"storyboard"的功能，底层要么是逐镜头分别调用生成能力再拼接（Sora storyboard 编辑器的关键帧模式、即梦故事创作、Runway Multi-Shot Recipe、Flow Scenebuilder），要么是营销词汇借用（Pika）。这意味着如果 ArcReel 想要在数据模型里区分出"业界已有的原生分镜路线"，目前没有一手事实基础可以支撑——因为这个"原生形态"本身尚不存在于已核实的公开产品/API 中。

2. **ArcReel 现有的宫格实现（强生图模型出多格大图 → 切割 → 逐场景 i2v）在结构上，恰好对应业界唯一被记录下来、且最接近"分镜板生视频"字面含义的实践**——即 gpt-image-2 + Seedance 2.0 的第三方组合工作流（见二.9）。但这终究是"用通用强图片模型的多格生图能力，去优化分镜图的批量一致性生成"，随后仍然要老老实实走逐场景图生视频——本质仍是 ArcReel 已有的"分镜路线"（图生视频，分镜图驱动），只是分镜图的生产方式做了批处理优化。没有证据表明这构成一种独立于"分镜路线"的、拥有不同模型输入契约的生成路线。

3. **业界确实存在与 ArcReel 现有"分镜路线"（i2v）和"参考生视频路线"（r2v，`lib/reference_video/`）平行的、有清晰独立模型输入契约的第三种范式**——即"首尾帧"（exactly 2 张图，明确的起止时间语义）。这个范式目前在 ArcReel 的建模里似乎还没有被显式区分出来（如果 ArcReel 当前的图生视频就是用首帧驱动，那么"首尾帧"可能是 i2v 路线内部一个可选的关键帧输入子模式，而非独立路线）——这不在本次调研范围内，仅作为附带观察提出。

4. **证据倾向**：把宫格建模为"分镜路线内部的装配优化选项"，比建模为独立的"生成路线"更贴合本次查到的业界事实——因为区分"生成路线"的核心标准应该是"模型侧接受的输入契约是否不同"（i2v 的输入是单张分镜图；r2v 的输入是资产参考图集合），而宫格并不改变最终喂给视频模型的输入契约（依然是切割后的单张首尾帧图走 i2v），它改变的只是"分镜图从哪里、以什么方式被生产出来"这一上游装配环节。但这终究是数据建模取舍，业主可能有其他产品/工程角度的考量，本文不代为决策。

## 五、方法与局限性说明

- 多个厂商的官方开发者站点（klingai.com、kling.ai、docs.volcengine.com 部分深层参数页、help.openai.com 帮助中心文章）直接抓取被 403/446 拦截或重定向到导航页，未能逐句核对原文；这些情况已在对应小节逐一标注，改用可信度较低的替代来源（搜索引擎摘要、阿里云转售文档镜像、第三方媒体报道），并明确注明"半一手"或"二手，未查到一手来源"。
- Seedance 2.5、Sora storyboard 底层是否单次联合生成 vs 编排层多次调用+拼接，这两点是本次调研中最大的不确定项，均标注为未经一手确认。
- 调研聚焦于文字层面的官方文档表述，未做逐产品的实测（如实际调用 API 观察返回结果），因此"文档没写"不完全等同于"能力不存在"，请复核者知悉此局限。
