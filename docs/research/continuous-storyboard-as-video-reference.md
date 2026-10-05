# 「连续分镜图作为参考生视频」需求调研与取舍分析

> 用途：用户请求「把连续分镜图（多张有先后顺序的分镜图，或一张宫格分镜板）作为参考喂给视频模型生成视频」。本文核对业界现状与 ArcReel 现有能力，给出是否值得做、以什么形态做的建议。调研日期 2026-10-05。
>
> 与既有调研的关系：`docs/research/storyboard-to-video-industry-survey.md`（2026-08-03）结论是「没有主流 API 原生接受整张多格分镜板」，并据此支撑了 ADR 0055「宫格是分镜路线内的装配选项」。本文补充其后两个月的变化，不推翻那份结论在 API 契约层面的判断。
>
> 来源局限：本次调研的出站代理拦截了 OpenAI、Google、BytePlus 的官方文档页，下列事实大多来自搜索摘要与第三方聚合文档（fal、wavespeed、novita、CometAPI 等），标 **[未核实]** 的条目仅有单一或二手来源。落到代码前须按官方文档复核具体上限。

## 一、核心结论

1. **API 契约层面没有质变。** 仍没有任何厂商官方文档把「一张多格分镜板」定义为输入。唯一可能的例外是 Seedance 2.5 的「分镜宫格控制」，只有二手报道 **[未核实]**。
2. **社区做法已成主流。** Seedance 2.0 于 2026-02 上线后，中文短剧圈普遍这样用：生图模型先出 9／12／25 宫格分镜板，把整张板作为一张参考图喂给 Seedance 全能参考，提示词按格序复述动作。这样一次生成就能得到带切镜的多镜头视频。LibTV、即梦都已把这个流程产品化。这是**提示词驱动的技巧**，不是 API 能力，效果靠经验，没有基准测试。
3. **原生「有序关键帧」API 确实存在，但都不是 ArcReel 的主力供应商。** 有 Vidu Q2 Turbo 智能多帧（≤9 帧）、PixVerse multi-transition（2～7 帧）、Luma Ray3.2 多关键帧（≤16 帧，**[未核实]**）、LTX-2。可灵 3.0 的 `multi_prompt` 是按镜头分配提示词与时长，不是按镜头分配图片。
4. **建议：值得做，但只做窄版，并且先实测再立项。** 形态是在**参考生视频路线**内，给视频单元增加一张可选的「分镜板」参考图。首批只对 Seedance 2.x 开放。不新增路线，不改分镜路线。理由见第四节。

## 二、业界现状（2026-10）

### 2.1 三类输入要分开看

| 类别 | 语义 | 代表 |
|---|---|---|
| 时序关键帧 | 有序图片，每张钉在某个时间点 | 首尾帧（各家都有）、Vidu 智能多帧、PixVerse multi-transition、Luma 多关键帧 |
| 身份／风格参考 | 无序图片，告诉模型「东西长什么样」 | 参考生视频（r2v）：Seedance 全能参考、可灵 Omni、Vidu reference2video、Veo `referenceImages` |
| 原生多镜头 | 一次生成内部含多个切镜 | Seedance 1.0 Pro／2.0（≤15s）／2.5（≤30s）、可灵 3.0（≤6 镜 ≤15s）、Wan 2.6（≤15s）、Vidu Q3 |

「宫格板喂 Seedance」是把第三类能力和第二类输入凑在一起：板以**参考图**身份进入请求，模型从格子位置推断时序。

### 2.2 各供应商要点（只列与本需求相关的变化）

- **Seedance 2.0**：全能参考支持 ≤9 图、≤3 视频、≤3 音频，提示词里用 `@图片N` 指代，时长 4～15s。**首帧角色与参考图不能混用**，所以分镜板不能同时充当首帧。来源：[DataCamp](https://www.datacamp.com/tutorial/seedance-2-0-api-guide)、[BytePlus](https://docs.byteplus.com/en/docs/modelark/seedance-2-0)。
- **Seedance 2.5**（2026-07-31 发布）：单条最长 30s，可含多镜头，参考素材上限为图 30、视频 10、音频 10，另有用于构图与运镜的白模参考。ModelArk API「即将上线」。有二手来源称它带「分镜宫格控制模式」，LibTV 转述官方示例中用了九宫格作为镜头结构与节奏参考，均为 **[未核实]**。来源：[Seed 博客](https://seed.bytedance.com/en/blog/one-take-creation-flexible-referencing-introducing-seedance-2-5)。
- **可灵 3.0**（2026-02-05）：首尾帧，加 `multi_prompt[]` 多镜头（1～6 镜，逐镜提示词与时长，即「智能分镜／自定义分镜」）。3.0 本体不收参考图，参考图走 O1 / 3.0 Omni。没有宫格输入。来源：[Kling API](https://kling.ai/document-api/api/video/3-0-omni/image-to-video)、[Krea](https://www.krea.ai/blog/kling-3-0-api-access-guide-pricing-code-examples-for-multi-shot-ai-video)。
- **Vidu Q2 Turbo 智能多帧**：1 张起始图加 2～9 张关键帧，每帧配一段提示词，段长 2～7s，总时长为各段之和。这是**唯一由国内主力厂商提供的真正有序关键帧 API**。来源：[Novita 文档](https://novita.ai/docs/api-reference/model-apis-vidu-q2-turbo-multiframe)。
- **Veo 3.1**：首帧（可加尾帧）和 1～3 张 `referenceImages` 二选一；带参考图时只能生成 8s。没有多镜头，也没有多关键帧。来源：[Google 开发者博客](https://developers.googleblog.com/introducing-veo-3-1-and-new-creative-capabilities-in-the-gemini-api/)。
- **Sora 2**：App 已于 2026-04-26 下线，API 已于 2026-09-24 下线；storyboard 编辑器从来不在 API 里。来源：[OpenAI Help](https://help.openai.com/en/articles/20001152-what-to-know-about-the-sora-discontinuation)。ArcReel 的 `openai.py` 视频后端应另行评估是否下架（不在本文范围）。
- **PixVerse multi-transition**：2～7 张有序关键帧，可逐段设时长与提示词，总时长 1～30s。来源：[PixVerse 文档](https://docs.platform.pixverse.ai/multi-transition-1740835m0)。ArcReel 未接入 PixVerse。

### 2.3 社区「宫格板 → 视频」的已知失败模式

来源：[ai-flow 模板](https://www.ai-flow.net/templates/storyboard-to-cinematic-video-with-seedance-2.0)、[Devtalk 讨论](https://forum.devtalk.com/t/gpt-image-2-seedance-2-0-pipeline-whats-your-experience-with-the-storyboard-grid-approach/242365)、[mer.vin](https://mer.vin/2026/06/gpt-image-2-seedance-2-0-4k-cinematic-ad-workflow-with-a-5x3-storyboard-grid/)，均为经验帖。

- **模型直接把宫格动起来**，输出分屏或拼贴视频，格线漏进画面。缓解办法：提示词逐格复述内容并写明「动画化每格的内容而非宫格本身」，再加上「无格线、无叠层」一类的负向词。
- **读格顺序错乱或相邻格被合并**，格数越多越明显。
- **单格分辨率低导致身份漂移**。缓解办法：板之外另带角色、场景资产图，格数控制在 9 格左右。
- 板只能作为参考图，**不能同时作为首帧**（Seedance 的互斥规则）。

### 2.4 国内产品形态

- **即梦**：Seedance 2.0／2.5 全能参考、首尾帧、智能多帧，是「九宫格 → 视频」玩法的主要阵地。
- **LibTV（哩布）**：画布内一键生成 9／25 宫格连续分镜、4 宫格剧情推进图，再批量调 Seedance 2.0 出视频。这是该流程**最完整的产品化形态**。来源：[网易](https://www.163.com/dy/article/KPGE0ACG0556KT58.html)。
- **可灵**：智能分镜、自定义分镜（`multi_prompt`）、多图主体参考。
- **Vidu**：智能多帧、多图参考。
- **剪映 Hub**（2026-09-20）：无限画布加多轨编辑，底层调即梦生成。来源：[科技日报](https://www.stdaily.com/web/gdxw/2026-09/20/content_584720.html)。

需求信号：知乎、CSDN、腾讯新闻上大量「九宫格／25 宫格分镜 → 成片」教程和提示词包。ArcReel 的目标用户（中文短剧、解说）正是这批人。

## 三、ArcReel 现状对照

| 方面 | 现状 | 出处 |
|---|---|---|
| 路线 | `storyboard`（i2v，单张分镜图作首帧，加可选尾帧）与 `reference_video`（r2v，资产图集合），创建即锁定 | ADR 0055；`lib/config/resolver.py:234-270` |
| 宫格 | 分镜路线内的装配选项：N 格联合图 → 切格 → 每格作对应分镜的首帧 → 逐镜 i2v。联合图本身从不进视频请求 | `lib/script/grid/`；`server/services/grid/grid_split.py:150-210` |
| 宫格帧链 | 第 0 格是首镜开场，第 i 格是「镜 i-1 → 镜 i」的过渡帧。这种「连续分镜」语义已经存在 | `lib/script/grid/models.py:67-114` |
| 参考生视频输入 | 只来自单元正文的 `@[名称]` 资产提及，按首次提及顺序装配，超限按前 N 张截断。单元没有图片字段 | ADR 0064；`lib/script/reference_video/request_projection.py:391-496` |
| 参考图上限 | Seedance 2.0 为 9、2.5 为 30；Veo 为 3；Vidu 为 7；可灵 Omni 为 4；Wan3.0 为 10；HappyHorse r2v 为 9 | `lib/backends/video_backends/*.py` |
| 分镜路线视频请求 | 只发 `start_image` 和 `end_image`，从不发 `reference_images` | `server/services/tasks/generation_tasks.py:997-1330` |
| 多镜头／有序关键帧 | `VideoGenerationRequest` 没有多关键帧或逐镜提示词字段；Vidu 后端没有接智能多帧 | `packages/arcreel-market-core/.../video_backend_contract.py:280-337` |
| 已排除方向 | 不做视频真实尾帧接龙；参考生视频不引入首帧／尾帧槽位 | `.out-of-scope/video-unit-frame-chaining.md`；#2182（parked） |

**结论：** ArcReel 已经具备「生成连续宫格板」的全部上游能力（宫格提示词构建、联合图生成、资产图参考装配），缺的是**把这张板作为参考图送进 r2v 请求**这一步，以及配套的单元级模型。

## 四、方案比较

用户的需求可以对应到三种做法。

| 方案 | 做什么 | 收益 | 代价与风险 |
|---|---|---|---|
| **A. 参考路线内的「单元分镜板」（推荐）** | 参考生视频单元可选挂一张分镜板（由单元正文和资产图生成 N 格连续分镜，用户审阅后锁定），作为一张参考图与资产图一起发送，提示词模版写明格序与「不要渲染宫格」 | 直接对应社区主流做法与 LibTV 形态；复用宫格提示词与生图链路；一个单元就是一段带切镜的视频，正好匹配 Seedance 2.x 的多镜头能力 | 需要修订 ADR 0055 与 ADR 0064 的判据（输入不再只有资产图集合）；新产物需要时效依据（板依赖单元正文与资产图，视频依赖板）；质量只能靠实测，失败模式见 2.3；多占一个参考图槽位（Seedance 2.0 只有 9 张） |
| B. 分镜路线内多镜合并成一次生成 | 把连续 N 个分镜的分镜图作为 `reference_images` 送进一次请求，生成一段多镜头视频 | 复用已有的逐张分镜图与审阅点 | 与分镜路线「一个分镜一段视频、首帧锚定」的模型根本冲突：剪辑时间线、逐条重生、时效、尾帧全部要重做；Seedance 不允许首帧和参考图混用，等于把分镜路线变成参考路线。不建议 |
| C. 接入原生有序关键帧 API | 新增 `keyframes[]` 契约，接 Vidu 智能多帧、可灵 `multi_prompt`、PixVerse 等 | 时序语义有 API 保证，不靠提示词技巧 | 各家契约差异大（逐帧段长、逐段提示词、是否要起始帧），ArcReel 主力后端（Seedance、Veo）都不支持；需要新的视频契约字段和能力位。需求量未知，可作为后续独立议题 |

### 推荐方案 A 的边界

- **不新增路线。** 板是参考生视频单元的可选附件，路线判据扩写为「资产参考图集合，外加可选的单元分镜板」，需要在 ADR 0055 中补一节修订。
- **能力门控。** 首批只对 Seedance 2.0／2.5 开放，以模型白名单或新能力位声明（例如 `storyboard_board_reference`），其余模型不显示入口。不要因为某模型的 `max_reference_images > 0` 就默认开放，因为效果是模型相关的经验行为。
- **槽位优先级。** 板占一个参考图槽位。超限截断时，板应当优先于靠后的资产图保留，还是反过来，要由实测决定。
- **时效。** 板的生成依据是单元正文加引用的资产图；视频的依据要包含板。遵循 ADR 0062 的同源依据要求，复用「生成输入」module（#2680）的装配方式。
- **复用宫格。** 板的提示词复用 `lib/script/grid/prompt_builder.py` 的帧链语义（开场 + 过渡），格数按单元时长取 4 或 9。
- **不做的事。** 不把板当首帧；不切格；不在分镜路线做多镜合并（方案 B）。

## 五、建议的推进顺序

1. **先实测（参照 #2182 的 parked 做法）**：手动生成若干张九宫格板，挑 3～5 个典型参考生视频单元（单人对话、多人动作、场景切换），在 Seedance 2.0 和 2.5 上做「仅资产图」与「资产图加分镜板」的对照。观察四点：切镜是否按格序出现，是否出现宫格或格线，角色一致性是否下降，参考图槽位被占后的影响。一个临时脚本就够，不改产品代码。
2. **实测结果明显更好时**：按 issue-tracker 约定写 Spec，并修订 ADR 0055 与 ADR 0064，拆为四部分：分镜板产物与时效、板生成入口（WebUI 与 Agent 工具）、r2v 请求装配与提示词模版、画布审阅界面。
3. **实测不佳时**：把结论写入 `.out-of-scope/`，并回复请求用户：目前可以先用宫格分镜路线配合尾帧（#1289）来获得连续性。
4. **另开议题跟踪**：Seedance 2.5 ModelArk API 上线后，复核「分镜宫格控制」是否成为官方参数；若成为官方参数，方案 A 的门控可以改为读官方能力，失败模式也会减少。Vidu 智能多帧、可灵 `multi_prompt` 视需求量再评估方案 C。

## 六、方法与局限

- 官方文档页（platform.openai.com、ai.google.dev、docs.byteplus.com 等）直接抓取被代理拦截，供应商事实来自搜索摘要与第三方聚合文档。标注为 [未核实] 的条目有：Seedance 2.5 的宫格控制模式、Luma 16 关键帧、Seedance 1.0 Lite 参考图上限、可灵 Omni 视频端点的图片上限。
- 「宫格板 → Seedance」的效果证据全部来自经验帖与教程，没有找到系统评测，所以第五节第 1 步的实测是立项前提，不是可选项。
- ArcReel 侧的事实来自对当前 `main` 代码与 ADR 的阅读，行号以本次调研时为准。
