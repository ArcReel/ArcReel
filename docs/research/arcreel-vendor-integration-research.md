# ArcReel 三家厂商接入调研报告（阿里百炼 / 可灵 Kling / MiniMax）

**调研日期**：2026-05-29
**用途**：作为后续 PRD 与设计文档的输入素材，评估三家作为 ArcReel 预设供应商接入（文本 / 图片 / 视频）
**币种**：中国内地站人民币定价为准，国际站差异已标注
**定价说明**：各家价格随促销波动，本报告价格供选型参考，标注了官方来源与第三方来源的区别；上线前应以各官方控制台为准

**信息来源与可信度声明**：
- 阿里百炼：模型清单已对照[官方"模型大全"页](https://help.aliyun.com/zh/model-studio/models)（页面更新时间 2026-05-21）逐条核对，模型 ID 以官方页为准。
- MiniMax：模型清单已对照[官方"模型发布"页](https://platform.minimaxi.com/docs/release-notes/models)核对发布时间线，旗舰判断准确。
- 可灵 Kling：模型清单与定价均已对照可灵官方文档（[视频模型](https://klingai.com/document-api/apiReference/model/videoModels) / [图像模型](https://klingai.com/document-api/apiReference/model/imageModels)）与[官方定价页](https://klingai.com/dev/pricing)一手核对，模型 ID、能力矩阵、积分单价（视频 1 积分=¥1、图像 1 积分=¥0.025）以官方为准。
- 阿里"按张"的 `qwen-image-2.0` / `wan2.7-image` 精确单价、登录墙后的细分档位仍需控制台确认；标注"控制台核对"的为未一手确认项。

---

## 0. 调研范围与定位

本报告是**调研性质**的素材汇编，覆盖三家厂商**当前最新优质模型**的能力、API 协议、官方定价，聚焦 ArcReel 中文网文转视频工作流（剧本→角色/场景设计图→分镜图→视频→合成）的实际需求。

**不在范围内**（属 PRD / 设计文档阶段决策）：接入次序、阶段划分、成本熔断/降级规则、backend 类设计、数据库 schema。

模型筛选原则：只收录适合 ArcReel 接入的最新优质模型，过时或被取代的旧版本仅在必要处标注"已被取代"，不展开。

---

## 1. 结论先行：推荐评估的模型清单

| 厂商 | 模态 | 模型 | 模型 ID | 官方定价 | 协议 |
|---|---|---|---|---|---|
| 阿里百炼 | text | Qwen-Plus（量产均衡） | `qwen-plus` | 阶梯 0-128K：¥0.8 入 / ¥2 出（百万 token） | OpenAI 兼容 ✅ |
| 阿里百炼 | text | Qwen3.6-Plus（最新视觉语言 Plus） | `qwen3.6-plus` | ¥2 入 / ¥12 出；缓存命中 ¥0.2 | OpenAI 兼容 ✅ |
| 阿里百炼 | text | Qwen3-Max（稳定旗舰） | `qwen3-max` | 阶梯 0-32K：¥2.5 入 / ¥10 出 | OpenAI 兼容 ✅ |
| 阿里百炼 | text | Qwen3.7-Max（最新旗舰，智能体） | `qwen3.7-max` | ¥12 入 / ¥36 出（256K-1M）；限时 5 折至 ¥6/¥18（2026-06-22 止） | OpenAI 兼容 ✅ |
| 阿里百炼 | image | Qwen-Image-2.0-Pro（官方推荐，漫画/分镜/文字渲染） | `qwen-image-2.0-pro` / `qwen-image-2.0`（加速版） | 按张（控制台核对） | DashScope 同步 |
| 阿里百炼 | image | 万相 2.7 图像（人像真实感/组图） | `wan2.7-image` / `-image-pro` | 按张（控制台核对，失败不扣） | DashScope 同步/异步 |
| 阿里百炼 | video | HappyHorse 1.0 首帧生视频 | `happyhorse-1.0-i2v` | 720P ¥0.9/s、1080P ¥1.6/s | DashScope 异步 |
| 阿里百炼 | video | HappyHorse 1.0 参考生视频（R2V） | `happyhorse-1.0-r2v` | 720P ¥0.9/s、1080P ¥1.6/s | DashScope 异步 |
| 阿里百炼 | video | HappyHorse 1.0 文生视频 | `happyhorse-1.0-t2v` | 720P ¥0.9/s、1080P ¥1.6/s | DashScope 异步 |
| 阿里百炼 | video | 万相 2.7 图生视频（首尾帧/续写/音频） | `wan2.7-i2v-2026-04-25` | 按秒分辨率档（控制台核对） | DashScope 异步 |
| 可灵 Kling | image | Kling Omni Image O1（多参考角色一致，IP/漫画/连载） | `kling-image-o1`（1-10 图参考）/ `kling-v3-omni`（4K+组图） | ¥0.2/张（1K-2K）；v3-omni 4K ¥0.4 | JWT + 异步 |
| 可灵 Kling | video | 可灵 v3 / v3-omni（旗舰，多镜头+4K+主体控制） | `kling-v3` / `kling-v3-omni` | std ¥0.6/s、pro ¥0.8/s、4K ¥3/s（无声原价） | JWT + 异步 |
| 可灵 Kling | video | 可灵 v2-6（唯一支持视频内人声控制） | `kling-v2-6` | pro 有声 ¥1.0/s、无声 ¥0.8/s | JWT + 异步 |
| 可灵 Kling | video | 可灵 2.5 Turbo（性价比主力） | `kling-v2-5-turbo` | std 无声 ¥0.6/s、pro 有声 ¥1.0/s | JWT + 异步 |
| MiniMax | text | MiniMax-M2.7（旗舰） | `MiniMax-M2.7` | ¥2.1 入 / ¥8.4 出；缓存读 ¥0.42 | OpenAI/Anthropic 双兼容 ✅ |
| MiniMax | image | image-01（含角色一致性） | `image-01` | ¥0.025/张（成功才扣） | 自有 REST（单步取 URL） |
| MiniMax | video | Hailuo 2.3（T2V+I2V 高质量） | `MiniMax-Hailuo-2.3` | 768P 6s ¥2 / 10s ¥4；1080P 6s ¥3.5 | 自有 REST（两步 file_id） |
| MiniMax | video | Hailuo 2.3-Fast（仅 I2V，半价） | `MiniMax-Hailuo-2.3-Fast` | 768P 6s ¥1.35 / 10s ¥2.25；1080P 6s ¥2.31 | 同上 |
| MiniMax | video | S2V-01（角色一致性 R2V 专项） | `S2V-01` | 资源包 1.5 积分/视频（约 ¥3） | 同上，`subject_reference` |

**协议归属概览**：文本三家中阿里、MiniMax 都走 OpenAI 兼容（可复用现有 backend），可灵无文本模型；图片与视频阿里走 DashScope（图片以同步为主——Qwen-Image-2.0 仅同步、Wan-Image 同步/异步皆可；视频统一异步任务），可灵走 JWT，MiniMax 走自有 REST（图片单步取 URL、视频两步 file_id）。

---

## 2. R2V（参考生视频 / 角色一致性）能力对比

> R2V = 用一张或多张参考图，在新场景中保持**角色/主体一致性**生成视频，是网文转视频"主角串戏"的核心能力。各家字段名、参考图数量、模型支持差异大，单列对比。

### 2.1 横向对比

| 厂商 | R2V 模型 ID | 参考字段名 | 参考图上限 | 一致性对象 | 音频 | 备注 |
|---|---|---|---|---|---|---|
| 阿里 HappyHorse | `happyhorse-1.0-r2v` | DashScope input 参考图字段 | 多角色 | 角色 + 主体 | ✅ 有声 | 720P/1080P，3-15s，原生音画 |
| 阿里 Wan 2.7 | `wan2.7-r2v` | 图N/视频N 引用格式 | 多角色（图+视频混合引用） | 角色 + 主体 | ✅ 可传音色/视频主体 | 720P/1080P，2-10s，**唯一支持视频参考主体** |
| 阿里 Wan 2.6 | `wan2.6-r2v` / `wan2.6-r2v-flash` | 同上 | 多角色 | 角色 | ✅ | flash 版快速生成 |
| 可灵 v3 / v3-omni | `kling-v3` / `kling-v3-omni` | 主体控制参数 | 视频角色主体 + 多图主体 | 角色 + 主体 | 官方标 ❌ 人声 | 官方"主体控制"最强；v3-omni 另支持视频参考（3-10s）|
| 可灵 O1 | `kling-video-o1` | 多图主体 + 视频参考 | 多图主体 | 角色 + 视频驱动 | ❌ | 含视频参考（视频编辑），3-10s |
| 可灵 v1-6（旧） | `kling-v1-6` | `image_list[]` | 多图参考生视频（旧版） | 多主体融合 | 视模型 | 多图参考 + 多模态视频编辑 |
| MiniMax S2V-01 | `S2V-01` | `subject_reference[]` | 1 张（仅人脸） | 单角色人脸 | — | 业界首创单图驱动；环境可能轻度变形 |

### 2.2 关键边界

- **可灵 R2V 已升级到"主体控制"**（官方一手）：当前最强是 `kling-v3` / `kling-v3-omni` 的"主体控制（视频角色主体 + 多图主体）"，`kling-video-o1` 支持多图主体 + 视频参考；老 `kling-v1-6` 的多图参考生视频是早期能力，已被 v3/o1 超越。
- **MiniMax Hailuo 2.3 / 2.3-Fast 不支持 R2V**。Hailuo 2.3 只有 T2V + I2V，2.3-Fast 仅 I2V。MiniMax 的角色一致性 R2V 必须用独立的 **S2V-01** 模型，且仅支持单张人脸参考。
- **阿里 R2V 能力最全**：HappyHorse-1.0-r2v 与 Wan2.7-r2v 都支持**多角色**参考且带原生音频；Wan2.7-r2v 是阿里侧**唯一支持"视频参考主体"**的模型，可灵 v3-omni/o1 也支持视频参考。

### 2.3 网文场景 R2V 适配

- 主角单人串戏：MiniMax `S2V-01`（单图人脸驱动，一致性最稳）或 `happyhorse-1.0-r2v`
- 多角色同框对手戏：阿里 `wan2.7-r2v` / `happyhorse-1.0-r2v`（多角色 + 音频），或可灵 `kling-v3-omni`（视频角色主体 + 多图主体）
- 视频参考驱动（以参考视频带动作/主体）：阿里 `wan2.7-r2v`、可灵 `kling-v3-omni` / `kling-video-o1`

---

## 3. 阿里百炼（DashScope / Model Studio）

> **地域**：北京 `dashscope.aliyuncs.com` / 新加坡 `dashscope-intl.aliyuncs.com` / 弗吉尼亚 `dashscope-us.aliyuncs.com`，三地 API Key 独立不可跨域。

### 3.1 文本：Qwen 系列（含最新 Qwen3.7-Max）

#### 模型与官方定价（中国内地，元/百万 token，阶梯按单次请求输入总量计费整单）

| 模型 ID | 定位 | 输入 | 输出 | 上下文 | 说明 |
|---|---|---|---|---|---|
| `qwen3.7-max` | **最新旗舰**，面向智能体时代，对位 GPT-5.5 / Claude Opus 4.7 | ¥12 | ¥36 | 256K-1M | 限时 5 折至 ¥6/¥18（至 2026-06-22）；缓存命中输入 ¥1.2；AA 榜 56.6 分全球第五国产第一；纯文本能力开放 |
| `qwen3-max` | 稳定旗舰（快照 `qwen3-max-2025-09-23` / `-2026-01-23`） | ¥2.5（0-32K） | ¥10 | 256K | 32-128K ¥4/¥16；128-256K ¥7/¥28；原生 search agent；思考模式输出翻数倍 |
| `qwen3.6-plus` | 最新视觉语言 Plus（2026-04-02 快照，官方千问首推之一） | ¥2 | ¥12 | — | 缓存命中 ¥0.2；代码/OCR/多模态超 3.5 系列 |
| `qwen3.6-flash` | 最新 Flash（官方千问首推之一，高频低成本档） | 控制台核对 | 控制台核对 | — | 官方模型大全页千问首推三款之一；适合 ArcReel 最高频的 prompt 改写/标签提取 |
| `qwen-plus` | 量产均衡（稳定别名，可指向 qwen3.5-plus） | ¥0.8（0-128K） | ¥2 | 1M | 128-256K ¥2/—；超长上下文低成本一梯队 |
| `qwen-long` | 超长文档低成本 | ¥0.5 | ¥2 | 10M | 长章节理解/长文摘要最省 |

> 阿里官方"模型大全"页（2026-05-21）的**千问文本首推三款**为 `qwen3.7-max` / `qwen3.6-plus` / `qwen3.6-flash`；`qwen-plus` / `qwen-long` 是稳定别名档，仍可用但已非首推位。

**选型分工建议**：旗舰关键剧情/复杂推理用 `qwen3.7-max` 或 `qwen3-max`；高频分镜 prompt 改写、角色卡、配音脚本提取用 `qwen-plus` 或 `qwen3.6-plus`（性价比）；超长网文章节整本理解用 `qwen-long`。简单任务用 Qwen3.7-Max 属"大炮打蚊子"。

**API**：OpenAI 兼容 ✅ — `base_url=https://dashscope.aliyuncs.com/compatible-mode/v1`，端点 `/chat/completions`，Bearer Key。支持流式、`tool_calls`、`response_format={"type":"json_object"}`。非标参数 `enable_thinking` / `thinking_budget` 走 `extra_body` 透传。中文复杂 schema 结构化输出用 `tool_choice` 强制工具调用比 `response_format` 稳。

**阶梯计费规则**：单次请求输入 Token 总量决定整单档位单价（不是超出部分才涨价）。

> 已被取代的旧版（`qwen-turbo` / Qwen2.x 系列）不展开；存量稳定接口仍可用。

#### 百炼三方模型代理（一个接入点覆盖多家）

阿里官方模型大全页显示，百炼除千问外还以**与千问完全一致的 OpenAI 兼容格式**代理了多家第三方文本模型，ArcReel 接入百炼一个 `dashscope` provider + OpenAI backend 即可顺带拿到这些（model 字段切换）：

- `deepseek-v4-pro` / `deepseek-v4-flash`（DeepSeek V4）
- `kimi-k2.6`（月之暗面 Kimi）
- `glm-5.1`（智谱 GLM）
- `MiniMax-M2.7`（百炼代理路径 `MiniMax/MiniMax-M2.7`，享隐式缓存 20% 折扣）
- `mimo-v2.5-pro`（小米 MiMo）

对 ArcReel 的价值：若不想分别对接 MiniMax/DeepSeek 等官方站，可统一走百炼一个接入点；代价是这些三方模型在百炼的定价可能与其官方站略有差异，精确价以百炼控制台为准。

### 3.2 图像：Qwen-Image 与 万相 2.7 图像（两条平行产品线）

阿里有两套图像生成产品线，**定位互补**，网文场景都值得评估：

#### ① Qwen-Image 系列（通义千问团队，文字渲染 + 漫画分镜 SOTA）

- **模型 ID**：
  - `qwen-image-2.0-pro` / `qwen-image-2.0-pro-2026-03-03`（**官方推荐**，图像生成与编辑融合模型，文字渲染/真实质感/语义遵循最强，仅同步接口）
  - `qwen-image-2.0` / `qwen-image-2.0-2026-03-03`（加速版，效果与性能平衡，仅同步接口）
  - `qwen-image-edit`（图像编辑/局部修改专用，content 含 1-3 张图 + 一条编辑指令）
  - 前代 `qwen-image-max` / `qwen-image-plus`（已被 2.0 系列取代，不展开）
- **发布**：Qwen-Image-2.0 于 2026-02-10 发布（与字节 Seedream 5.0 同日），千问首个图像生成模型 2.0 迭代，7B 参数（较初版 20B MMDiT 大幅瘦身）
- **核心能力（网文契合度高）**：
  - **复杂文本渲染 SOTA**：支持最长 1000 token（约 800-1000 汉字）长指令，准确处理复杂排版、多字体（楷书/瘦金体/小楷等）、多介质文字（玻璃/衣物/杂志等）；官方演示成功生成《兰亭集序》324 字全文配图
  - **多格漫画生成 + 跨格角色一致性稳定** —— 直接对应分镜场景
  - **原生 2K 分辨率**（2048×2048）输出，细腻刻画人物皮肤/植被/建筑纹理；支持写实、水墨、手绘、动漫、油画等十余种风格
  - **生图编辑二合一**：统一架构在同一模型内完成文生图 + 图生图编辑（题词、换背景、合成多图、跨次元融合），物体级编辑不损周围细节
  - AI Arena 文生图 + 图像编辑双榜第一；DPG-Bench 88.32 分超 FLUX.1（12B）的 83.84 分
- **API**：DashScope，文生图与图像编辑分端点；2.0 系列仅同步接口；图像编辑 `messages` 仅含一个 user 对象（image 1-3 张 + 一条 text 指令），建议图像宽高 384-3072px、单张 ≤10MB，返回 OSS 临时 URL
- **定价**：按成功生成张数计费，失败不扣费、不耗免费额度；精确单价以百炼控制台"模型列表与价格"为准（官方称商用价预计约为 Midjourney 三分之一）
- **语言**：正式支持简体中文、英文

#### ② 万相 2.7 图像（万相团队，人像真实感 + 组图）

- **模型 ID**：`wan2.7-image`（标准）/ `wan2.7-image-pro`（大规模，复杂场景更稳）；发布 2026-04-01
- **核心能力**：
  - 人物多样性"千人千面"捏脸，告别同质 AI 脸（人像真实感强）
  - **组图生成单次最多 12 张**，保持同角色/同风格/同调色 → 分镜参考
  - 超长文字渲染（最多 4000 字符 / 3K tokens，12 种语言印刷级中文排版）
  - Hex 色值精确控制；多参考图最多 9 图合影保持角色一致
  - 支持自然语言在线修改画面与剧情
- **API**：DashScope 同步 HTTP `POST /api/v1/services/aigc/multimodal-generation/generation`（wan2.6 起支持同步）或异步 + `X-DashScope-Async: enable`
- **定价**：按成功张数计费，失败不扣；精确单价以控制台为准

**两线选型**：分镜/漫画/带文字海报优先 Qwen-Image-2.0-Pro（跨格角色一致 + 文字 SOTA + 原生 2K）；写实人像/角色多样性/单次组图优先 wan2.7-image。两者均按张计费、走 DashScope。

### 3.3 视频：HappyHorse 1.0 系列（主推）与 万相 Wan 2.7

> HappyHorse（快乐小马）是阿里 2026-04 正式官宣的视频大模型族，**Artificial Analysis Video Arena 盲测文生/图生"无音频"赛道双第一、有音频赛道居全球第二（紧随 Seedance 2.0）**。150 亿参数单流 Transformer，原生音视频同步生成，单 H100 生成 5s 1080P 仅 38s（同类 2-3 倍速度）。**全开源可商用**，对 ArcReel 开源项目尤其友好。

#### HappyHorse 1.0 全族（官方定价：720P ¥0.9/秒、1080P ¥1.6/秒）

| 模型 ID | 类型 | 特性 | 输出规格 |
|---|---|---|---|
| `happyhorse-1.0-t2v` | 文生视频 | 有声、7 语言唇形、多镜头 | 720P/1080P，3-15s，24fps MP4 |
| `happyhorse-1.0-i2v` | 首帧生视频 | 有声、1080P | 720P/1080P，3-15s，24fps MP4 |
| `happyhorse-1.0-r2v` | 参考生视频 | 有声、多角色一致性 | 720P/1080P，3-15s，24fps MP4 |
| `happyhorse-1.0-video-edit` | 视频编辑 | 有声、风格转换 | 720P/1080P，3-15s，24fps MP4 |

唇形语言：中、英、日、韩、德、法、粤 七种。最长 15 秒多镜头叙事，电影级光影/运镜/人物一致性。

#### 阿里官方推荐的 HappyHorse / Wan 分工

| 场景 | 官方推荐 | 备选 |
|---|---|---|
| 文生视频（有声） | `happyhorse-1.0-t2v` | `wan2.7-t2v-2026-04-25`（需自定义音频文件时） |
| 首帧生视频 | `happyhorse-1.0-i2v` | — |
| 首尾帧 / 视频续写 / 长视频串联 | `wan2.7-i2v-2026-04-25` | — |
| 参考生视频（角色一致） | `happyhorse-1.0-r2v` | `wan2.7-r2v`（需视频参考主体/自定义音色） |
| 视频编辑 | `happyhorse-1.0-video-edit` | `wan2.7-videoedit`（特效/运镜复刻） |

#### Wan 2.7 全族（720P/1080P，2-15s，30fps）

| 模型 ID | 类型 | 特性 |
|---|---|---|
| `wan2.7-t2v` / `wan2.7-t2v-2026-04-25` | 文生视频 | 音频同步、多镜头叙事 |
| `wan2.7-i2v` / `wan2.7-i2v-2026-04-25` | 图生视频 | 首帧、首尾帧、视频续写、音频驱动 |
| `wan2.7-r2v` | 视频引用 | 多角色、图N/视频N 引用格式（唯一支持视频参考主体），2-10s |
| `wan2.7-videoedit` | 视频编辑 | 指令编辑、视频迁移、特效/运镜复刻，最长 10s |

> 万相 2.5（`wan2.5-t2v-preview` / `wan2.5-i2v-preview`）是唯一可传自定义音频文件（`audio_url`）的版本，480P/720P/1080P，5s/10s 固定档。Wan 2.1/2.2 等更早版本已被 2.6/2.7 取代，不展开。

**Wan / HappyHorse 视频 API**（统一 DashScope 异步）：
- 提交：`POST /api/v1/services/aigc/video-generation/video-synthesis` + `X-DashScope-Async: enable` → `output.task_id`
- 轮询：`GET /api/v1/tasks/{task_id}` → `PENDING → RUNNING → SUCCEEDED/FAILED`
- 取结果：`output.video_url`（OSS 公网，24h 有效，需立即转存）

---

## 4. 可灵 Kling（快手）

> **无文本模型**。**API**：`https://api.klingai.com/v1`，**JWT HS256** 鉴权（AK 作 iss、SK 签名，30 分钟过期，需自动续签）。阿里云百炼也代理了可灵图像/视频（走 DashScope 异步，仅北京地域），如不想写 JWT 可走百炼代理路径。
>
> 本节模型清单已对照可灵官方文档 `klingai.com/document-api/apiReference/model/{video,image}Models` 一手核对。**精确灵感值定价仍需登录 app.klingai.com 控制台确认**（官方文档列能力矩阵，不列每档灵感值数值）。

### 4.1 图像（可图 Kolors 系 + 新一代 Omni 图像）

可灵官方图像模型谱系（一手核对）：`kling-v1` / `kling-v1-5` / `kling-v2` / `kling-v2-new` / `kling-v2-1` / `kling-v3` / `kling-v3-omni` / `kling-image-o1`。

#### 网文场景最值得评估的两款

- **`kling-image-o1`（Kling Omni Image O1，强烈推荐）**：可灵新一代多参考图像模型，基于 MVL（多模态视觉语言）框架，**支持 1-10 张参考图同时输入**、跨图角色一致性，官方定位即"IP 角色设计、漫画/连载、品牌物料"——**这是可灵当前对网文角色串戏/分镜最强的图像模型**，远超老 Kolors。支持自定义长宽比（1K/2K）+ 智能长宽比，文生图/图生图/主体控制（多图主体）。训练数据截至 2025-12。
- **`kling-v3-omni`（图像能力）**：自定义长宽比 1K/2K/**4K** + 智能长宽比，文生图/图生图/**组图生成**/主体控制（多图主体）。是 3.0 统一多模态架构的图像组件，4K + 组图对分镜批量产出有价值。

#### 旧版（按需，已被 o1/v3 取代）

- `kling-v1`：文生图/图生图通用垫图，1K，8 种长宽比
- `kling-v1-5`：图生图含**角色特征 + 人物长相**保持，1K
- `kling-v2` / `kling-v2-1`：含多图参考生图、风格转绘；v2-1 最全（通用垫图+角色特征+人物长相+多图参考+风格转绘）
- 中文写字是 Kolors 系传统强项（ChatGLM3 文本编码器）；新一代 o1/v3-omni 文字渲染进一步增强

**API**：JWT + 异步，`POST /v1/images/generations` → `GET /v1/images/generations/{task_id}`（image_url 24h）
**关键参数**：`model_name`、`prompt`、`negative_prompt`、`image`、`image_fidelity`、`human_fidelity`、`n`、`aspect_ratio`；多图参考用 image 数组（o1 最多 10 图）

**官方图像定价（一手，`klingai.com/dev/pricing`，图像积分 1 积分 = ¥0.025）**：

| 模型 | 能力 | 规格 | 单价/张 |
|---|---|---|---|
| `kling-image-o1` | 文生图/图生图/图片编辑 | 各长宽比 | ¥0.2（8 积分）|
| `kling-v3-omni` | 文生图/图生图/图片编辑 | 1K/2K | ¥0.2 |
| `kling-v3-omni` | 同上 | 4K | ¥0.4（16 积分）|
| `kling-v3` | 文生图/图生图 | 1K/2K | ¥0.2 |
| `kling-v2-1` | 文生图 | — | ¥0.1（4 积分）|
| `kling-v2-1` | 图生图 | — | ¥0.2 |
| `kling-v2` | 文生图 | — | ¥0.1 |
| `kling-v2` | 图生图-多图参考 | — | ¥0.4（16 积分）|
| `kling-v1-5` | 图生图-角色特征/人物长相 | — | ¥0.2 |
| `kling-v1` | 文生图/图生图 | — | ¥0.025（1 积分，最便宜）|
| 功能模型 | 扩图 | — | ¥0.2 |
| 功能模型 | 智能补全主体图 | — | ¥0.5（20 积分）|

即 `kling-image-o1` / `kling-v3-omni`（推荐）为 ¥0.2/张（1K-2K），与 MiniMax image-01 的 ¥0.025/张相比偏贵，但多图参考一致性更强。

### 4.2 视频

可灵官方视频模型谱系（一手核对）：`kling-v1` / `kling-v1-5` / `kling-v1-6` / `kling-v2-master` / `kling-v2-1` / `kling-v2-1-master` / `kling-v2-5-turbo` / `kling-v2-6` / `kling-v3` / `kling-v3-omni` / `kling-video-o1`。

**官方视频定价（一手，`klingai.com/dev/pricing`，1 积分 = ¥1 原价，按维度组合计费 ¥/秒）**：

| 规格 | 无声 | 有声 | 有参考视频（无声） |
|---|---|---|---|
| 标准 std × 1s | ¥0.6 | ¥0.8 | ¥0.9 |
| 高品质 pro × 1s | ¥0.8 | ¥1.0 | ¥1.2 |
| 4K × 1s | ¥3.0 | ¥3.0 | — |

即可灵视频按"模式 × 时长 × 是否参考视频 × 是否有声"四维组合计费，例如 pro 有声 5s = ¥1.0×5 = ¥5；std 无声 5s = ¥3。资源包阶梯：试用 0.7 元/积分（首购 7 折），标准 1 元/积分，大额 0.9 元/积分。

社区评测：可灵在动作物理感、运镜稳定性、复杂指令拆解为全球第一档。Artificial Analysis T2V (with-audio) Leaderboard（2026-05-28）Kling 3.0 Omni 1080p Pro Elo 1099 排名第四（前三 Dreamina Seedance 2.0、HappyHorse-1.0、Veo 3.1）。

> 与模型版本无关的平台能力（官方）：数字人（单张照片生成播报视频）、对口型（文案/音频驱动口型）、视频生音效（为可灵生成或用户上传视频加音效）。

#### ① kling-v3 / kling-v3-omni（旗舰，多镜头 + 4K + 主体控制）

- **能力（官方一手）**：std/pro/**4K**，时长 3-15s；文生视频含**单镜头 + 多镜头视频生成**；图生视频含单镜头（仅首帧）+ 多镜头 + **首尾帧（一镜到底）** + **主体控制（视频角色主体 + 多图主体）**；v3-omni 额外支持视频参考（仅 3-10s，4K 档不支持视频参考）；v3 还支持动作控制（std/pro，4K 不支持）
- **重要更正**：官方能力表中 v3 / v3-omni 的**"声音控制（人声控制）"标注为 ❌**。这与部分第三方"v3 原生音画同步"的说法**不一致**——以官方为准，v3/v3-omni 视频本身的人声控制能力按官方标注为不支持，音效可走平台级"视频生音效"能力补充。接入前务必在官方文档二次确认。
- **主体控制是网文关键**：v3/v3-omni 的"视频角色主体 + 多图主体"控制，是比老 v1-6 多图参考更强的角色一致性能力

#### ② kling-v2-6（唯一明确支持人声控制的视频版本）

- **能力（官方一手）**：std/pro，5s/10s + 其他时长；文生/图生视频（std 仅无声视频，pro 含声）；首尾帧（pro，仅无声）；**声音控制（人声控制）仅 pro 支持 ✅**；动作控制（其他时长档）
- 是官方视频模型里**唯一在能力表明确标注"声音控制 ✅"的版本**（pro 档），需要视频内人声时优先评估它

#### ③ kling-v2-5-turbo（性价比主力）

- **能力（官方一手）**：std/pro，5s/10s；文生视频 + 图生视频（全档）；首尾帧（仅 pro）；分辨率 pro 1080p、24fps
- **官方定价**：按上表维度组合，std 5s 无声 = ¥3、pro 5s 有声 = ¥5（pro 1080p 24fps）；快手 IR 2025-09-24 曾公告其每段 5s 1080P 较 2.1 降约 30%
- 无多图主体/声音控制等高级能力，纯性价比走量档

#### ④ kling-video-o1（视频参考 + 主体控制专项）

- **能力（官方一手）**：std/pro，3-10s（文生/图生仅 5s、10s）；图生视频含首尾帧（一镜到底）+ **主体控制（仅多图主体）** + **视频参考（含视频编辑）**；声音控制 ❌
- 适合需要"以参考视频驱动"或多图主体一致性的场景

#### ⑤ kling-v1-6 / v1-5 / v1（旧版按需）

- `kling-v1-6`：文生/图生视频全档，首尾帧（pro）、**多图参考生视频** + 多模态视频编辑 + 视频续写 + 双图特效（拥抱/亲吻/比心）；pro 1080p
- `kling-v1-5`：图生视频为主，首尾帧/仅尾帧/运动笔刷（pro）；含视频续写
- `kling-v1`：文生/图生视频，运镜控制、首尾帧、运动笔刷、视频续写、双图特效；720p
- `kling-v2-master` / `kling-v2-1` / `kling-v2-1-master`：v2 代各档，v2-1 图生视频含首尾帧（pro）

**关键能力分布速查**（官方一手）：
- 多镜头叙事：仅 `v3` / `v3-omni`
- 视频内人声控制：仅 `kling-v2-6`（pro）
- 主体控制（角色一致性）：`v3` / `v3-omni`（视频主体+多图主体最强）、`o1`（多图主体+视频参考）、`v1-6`（多图参考生视频）
- 4K：仅 `v3` / `v3-omni`
- 视频参考（含编辑）：`v3-omni`（3-10s）、`o1`

**可灵视频接入要点**（JWT）：

```python
import jwt, time
def kling_token(ak, sk):
    return jwt.encode(
        {"iss": ak, "exp": int(time.time())+1800, "nbf": int(time.time())-5},
        sk, algorithm="HS256", headers={"alg":"HS256","typ":"JWT"})
```

建议封装 JWT 鉴权类，30 分钟内复用 token、过期前 60 秒自动刷新。

---

## 5. MiniMax 海螺

> 全模态最便宜、协议最一致。所有 API 走 `https://api.minimaxi.com/v1`（国内）/ `https://api.minimax.io/v1`（国际），Bearer Key，文本同时兼容 OpenAI 和 Anthropic SDK。

### 5.1 文本：MiniMax-M2.7

- **模型 ID**：`MiniMax-M2.7`（旗舰）/ `MiniMax-M2.7-highspeed`（2 倍速，2 倍价）；百炼代理 `MiniMax/MiniMax-M2.7`
- **发布**：2026-03-18，当前最新旗舰（取代 M2.5 / abab 7）
- **能力**：SWE-Pro 56.22%（官方称 matching GPT-5.3-Codex）；MoE 230B 总参/10B 激活；200K 上下文；中文文学创作第一档；思考模式 + Function Calling + Toolathon 46.3%
- **API**：OpenAI 兼容 `base_url=https://api.minimaxi.com/v1`；或百炼代理享隐式缓存 20% 折扣；Anthropic SDK 同样兼容
- **官方定价**（国内站，元/百万 token，来源 platform.minimaxi.com pricing-paygo）：

  | 模型 | 输入 | 输出 | 缓存命中读 | 缓存写入 |
  |---|---|---|---|---|
  | M2.7 | ¥2.1 | ¥8.4 | ¥0.42（20%） | ¥2.625（125%） |
  | M2.7-highspeed | ¥4.2 | ¥16.8 | ¥0.42 | ¥2.625 |

- **国际站**：M2.7 输入 $0.30/M、输出 $1.20/M（约 Claude Sonnet 1/8 价）
- **定位**：与 Qwen 配对的第二文本模型——M2.7 做人设/情感细腻文本与中文创作，Qwen-Plus 做结构化 JSON

> abab 6.5 / M2.5 / abab 7 等旧版仍可调用但已被 M2.7 取代，不展开。

### 5.2 图像：image-01

- **模型 ID**：`image-01`
- **能力**：文生图 + 图生图统一；`subject_reference` 单张人脸参考驱动多场景（角色卡→多场景立绘）；`aspect_ratio` 或 `width`/`height`（512-2048，8 倍数）；`prompt_optimizer`；`n` 1-9
- **API**：`POST https://api.minimaxi.com/v1/image_generation`，Bearer + JSON，单步直接返回 `url`（24h）或 base64
- **官方定价**：¥0.025/张（成功才扣费）；国际站约 $0.0035-0.005/张
- **风格**：电影级人像 + 真实材质 SOTA，角色一致性三家中最稳；纯动漫略逊 Kolors / Qwen-Image

### 5.3 视频：Hailuo 2.3 系列

> 能力边界：Hailuo 2.3 = T2V + I2V；Hailuo 2.3-Fast = 仅 I2V；角色一致性 R2V 用独立的 S2V-01（仅单图人脸）。

#### ① MiniMax-Hailuo-2.3（T2V + I2V，高质量）

- **模型 ID**：`MiniMax-Hailuo-2.3`（2025-10-28 发布，当前旗舰视频模型）
- **能力**：T2V + I2V；1080P 原生输出；极致物理感（NCR 架构）；85% 复杂指令响应；运镜控制（prompt 内嵌 `[左摇,上升]` 等，≤3 组合）；**擅长动漫/插画/游戏 CG 风格化**（网文契合）
- **支持档**：6s（768P/1080P）、10s（768P，10s 不支持 1080P）
- **官方定价**（国内，元/视频）：768P 6s ¥2 / 10s ¥4；1080P 6s ¥3.5
- **国际站**：768P $0.045/s、1080P Pro $0.08/s

#### ② MiniMax-Hailuo-2.3-Fast（仅 I2V，约半价）

- **模型 ID**：`MiniMax-Hailuo-2.3-Fast`
- **能力**：仅图生视频（I2V），不支持 T2V；同生成质量、约半价；给 I2V 快速迭代用
- **官方定价**：768P 6s ¥1.35 / 10s ¥2.25；1080P 6s ¥2.31

#### ③ S2V-01（角色一致性 R2V 专项）

- **模型 ID**：`S2V-01`
- **能力**：MiniMax 首创 Subject-to-Video，单张人脸参考图驱动整段视频角色一致性（脸/姿态/表情/光照可 prompt 独立调整）；尤其适合多分镜/回归角色
- **参数**：`subject_reference=[{"type":"character","image":["url"]}]`
- **限制**：仅单张人脸；环境可能轻度变形；prompt 遵循度略低于 T2V/I2V
- **定价**：资源包扣 1.5 积分/视频（约 ¥3）

> Hailuo-02（`MiniMax-Hailuo-02`）保留 512P 草稿档（512P 6s ¥0.6），其余被 2.3 取代；T2V-01-Director 等旧版仅向后兼容，不展开。

**MiniMax 视频接入要点**（两步取 URL）：
1. `POST /v1/video_generation` → `task_id`
2. 轮询 `GET /v1/query/video_generation?task_id=xxx` → `status=Success` 返回 `file_id`
3. `GET /v1/files/retrieve?file_id=xxx` → `download_url`（短期有效，建议配 `callback_url`，先响应 challenge 校验）

---

## 6. 接入可行性评估

### 6.1 协议归属

| 模态 / 厂商 | 阿里百炼 | 可灵 Kling | MiniMax |
|---|---|---|---|
| 文本 | ✅ OpenAI 兼容，复用现有 backend | ❌ 无 | ✅ OpenAI/Anthropic 双兼容 |
| 图像 | DashScope 同步/异步 | JWT + 异步 | 自有 REST（单步取 URL） |
| 视频 | DashScope 异步（统一端点） | JWT + 异步（多端点） | 自有 REST（两步 file_id） |

### 6.2 ArcReel backend 工作量

| 复用度 | 模型 |
|---|---|
| 复用 OpenAI text backend | `qwen3.7-max`、`qwen3.6-plus`、`qwen3.6-flash`、`qwen3-max`、`qwen-plus`、`MiniMax-M2.7`；以及百炼代理的 `deepseek-v4-pro/flash`、`kimi-k2.6`、`glm-5.1`、`mimo-v2.5-pro`（均同一 OpenAI 兼容格式） |
| 新写 DashScope backend（一个覆盖全部，需同时支持同步与异步任务两种调用路径） | 图片 `qwen-image-2.0-pro` / `qwen-image-2.0` / `qwen-image-edit`（同步）、`wan2.7-image`（同步/异步）；视频 `happyhorse-1.0-*` / `wan2.7-*` / `wan2.5-*`（异步）；可灵百炼代理 |
| 新写 Kling JWT backend | 视频 `kling-v3` / `kling-v3-omni` / `kling-v2-6` / `kling-v2-5-turbo` / `kling-video-o1`；图像 `kling-image-o1` / `kling-v3-omni`（统一 `/v1/images` 与 `/v1/videos` 两类端点 + JWT 续签） |
| 新写 MiniMax backend（两步取 URL） | `MiniMax-Hailuo-2.3/-Fast`、`S2V-01`；`image-01` 单步更简单 |

### 6.3 鉴权特殊性

- 阿里：Bearer + 多地域 Key 不可混用，异步需 `X-DashScope-Async: enable` 头
- 可灵：JWT HS256，30 分钟过期，需 token 缓存 + 提前刷新
- MiniMax：Bearer 简单；视频 file_id 两步下载或配 callback_url（先响应 challenge）

### 6.4 网文场景契合度评分

| 维度 | 阿里百炼 | 可灵 | MiniMax |
|---|---|---|---|
| 中文剧本理解/结构化输出 | ★★★★★ | — | ★★★★★ |
| 中文字渲染（封面/海报） | ★★★★★ (Qwen-Image-2.0) | ★★★★ (image-o1/v3-omni 文字渲染增强) | ★★★ |
| 漫画/分镜跨格角色一致（图） | ★★★★★ (Qwen-Image-2.0 多格漫画) | ★★★★★ (image-o1 最多 10 图参考，专为漫画/连载) | ★★★★ (image-01) |
| 角色一致性（视频 R2V） | ★★★★★（happyhorse-r2v / wan2.7-r2v 多角色+音频+视频参考） | ★★★★★（v3-omni 视频主体+多图主体，o1 视频参考） | ★★★★（S2V-01 单图人脸，最稳但仅单人） |
| 视频画面物理感 | ★★★★★（HappyHorse 双榜第一/第二） | ★★★★★（Omni 物理引擎） | ★★★★（NCR 1080P 原生，动漫强） |
| 视频多镜头叙事 | ★★★★★（wan2.7 + happyhorse 多镜头主体一致） | ★★★★★（v3/v3-omni 官方多镜头视频生成） | ★★★ |
| 视频音画一体 | ★★★★★（HappyHorse 原生音画 7 语言唇形 / wan2.5 audio_url） | ★★★（官方仅 v2-6 pro 支持人声控制；v3/v3-omni 标 ❌，音效走平台级"视频生音效"） | ★★★（需外挂 TTS） |
| 价格性价比 | ★★★★（HappyHorse 720P ¥0.9/s） | ★★★（视频 std ¥0.6/s 起、pro 有声 ¥1.0/s、4K ¥3/s；图像 ¥0.2/张） | ★★★★★（Hailuo-2.3-Fast 768P 6s ¥1.35） |

---

## 7. 关键风险与不确定性

1. **价格波动**：三家均有限时折扣（Qwen3.7-Max 限时 5 折至 2026-06-22、Qwen3.6 全模型 4.5 折、HappyHorse 限时 8 折、可灵套餐活动）。ArcReel `PROVIDER_REGISTRY` 价格字段应配置化，不写死。
2. **官方价已核实项**：HappyHorse 1.0 全族 720P ¥0.9/s、1080P ¥1.6/s（阿里云上线公告）；Qwen 系列阶梯价与 Qwen3.7-Max ¥12/¥36（阿里云价格页/产品页）；MiniMax M2.7 ¥2.1/¥8.4、image-01 ¥0.025/张、Hailuo 2.3 各档（platform.minimaxi.com）；**可灵全系视频/图像积分单价**（官方定价页 klingai.com/dev/pricing：视频 1 积分=¥1 按"模式×时长×参考视频×有声"四维计费、图像 1 积分=¥0.025，image-o1/v3-omni ¥0.2/张）。
3. **仍需控制台核对项**：`qwen-image-2.0-pro/2.0` 与 `wan2.7-image` 精确每张 RMB 单价、`qwen3.6-flash` 精确 token 单价（阿里官方价格页未直接列出每张/每档数值）；百炼代理的三方模型（DeepSeek/Kimi/GLM/MiMo）在百炼侧定价。可灵定价已一手核实，无需再核对。
4. **模型 ID 更新**：Qwen 每月发快照，优先用稳定别名 + 定期回归；阿里官方千问文本首推 `qwen3.7-max`/`qwen3.6-plus`/`qwen3.6-flash`，图像视频首推 `wan2.7-image-pro`/`qwen-image-2.0-pro`/`happyhorse-1.0-*`（均据官方模型大全页 2026-05-21）；MiniMax 当前 M2.7 / Hailuo 2.3 / image-01（据官方发布页）。
5. **能力边界（官方一手）**：可灵角色一致性当前最强是 `kling-v3`/`v3-omni` 的"主体控制（视频角色主体+多图主体）"，非旧版 v1-6 多图参考；可灵 `v3`/`v3-omni` 视频"人声控制"官方标 ❌，仅 `kling-v2-6` pro 支持视频内人声。Hailuo 2.3 不支持 R2V（只 T2V+I2V），2.3-Fast 仅 I2V，MiniMax 的 R2V 走 S2V-01（仅单图人脸）。
6. **阿里两条图像产品线**：Qwen-Image-2.0（文字渲染/漫画分镜）与 Wan-Image（人像真实感）定位不同，需按场景分别评估，不是替代关系。
7. **HappyHorse 开源可商用**：基础模型/蒸馏/超分/推理代码全开源，对 ArcReel 友好；但网上有大量同名第三方站点，接入认准阿里云百炼官方 API。
8. **国际站差异**：MiniMax minimaxi.com（国内）vs minimax.io（国际）Key 不互通；阿里三地域 Key 独立；可灵 app.klingai.com（中国）vs klingai.com（全球）。
9. **榜单谨慎**：Artificial Analysis 更新快，国产模型中文专项优势不等于全球通用基准领先，选型以"中文+网文"实测为准。
10. **可灵已一手核对模型清单，但定价仍需控制台**：可灵视频/图像模型清单已对照官方文档逐条核对（v3/v3-omni/v2-6/v2-5-turbo/o1/image-o1 等），能力矩阵以官方为准。两点务必注意：① 官方能力表标注 **v3/v3-omni 视频"声音控制（人声控制）"为 ❌**，仅 `kling-v2-6` pro 支持视频内人声，这与部分第三方"v3 原生音画"说法冲突，以官方为准；② 各模型每档精确灵感值官方文档未列，需登录 app.klingai.com 控制台确认。

---

## 8. 参考资料（一手官方来源）

- 阿里云百炼模型大全（首推清单，2026-05-21 更新）：https://help.aliyun.com/zh/model-studio/models
- 阿里云百炼视频模型大全：https://help.aliyun.com/zh/model-studio/video-generate-edit-model/
- 阿里云百炼模型价格：https://help.aliyun.com/zh/model-studio/model-pricing
- 千问 Qwen-Image 文生图 API：https://help.aliyun.com/zh/model-studio/qwen-image-api
- 千问 Qwen-Image-Edit 图像编辑：https://help.aliyun.com/zh/model-studio/qwen-image-edit-guide
- 通义万相文生视频 API：https://help.aliyun.com/zh/model-studio/text-to-video-api-reference
- 千问大模型产品页（Qwen3.7-Max / Qwen-Image / Wan2.7）：https://www.aliyun.com/product/tongyi
- 可灵官方视频模型清单（一手）：https://klingai.com/document-api/apiReference/model/videoModels
- 可灵官方图像模型清单（一手）：https://klingai.com/document-api/apiReference/model/imageModels
- 可灵官方定价页（一手）：https://klingai.com/dev/pricing
- 可灵开放平台开发文档：https://app.klingai.com/cn/dev/document-api
- MiniMax 价格（pricing-paygo）：https://platform.minimaxi.com/docs/guides/pricing-paygo
- MiniMax 模型发布动态：https://platform.minimaxi.com/docs/release-notes/models
- MiniMax 图生视频 API：https://platform.minimaxi.com/docs/api-reference/video-generation-i2v
- MiniMax S2V-01：https://platform.minimax.io/docs/api-reference/video-generation-s2v
- MiniMax Hailuo 2.3 发布：https://www.minimax.io/news/minimax-hailuo-23

---

**对齐架构**：ArcReel `PROVIDER_REGISTRY` 预置供应商 + `lib/{text,image,video}_backends/`
