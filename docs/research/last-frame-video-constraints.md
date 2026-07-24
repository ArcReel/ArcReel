# 尾帧（first-last frame）模式后端约束盘点

Issue: [#1273](https://github.com/ArcReel/ArcReel/issues/1273)（Part of [#1271](https://github.com/ArcReel/ArcReel/issues/1271)）

盘点已实现尾帧（`end_image`/`last_frame`）的六家视频后端在尾帧模式下的四项约束：

1. 尾帧与参考图（`reference_images`）能否共存
2. 尾帧模式下的时长/分辨率/比例限制是否与普通图生视频不同
3. 尾帧模式计价是否不同（费用预估是否需要感知）
4. 首尾帧两图的比例/尺寸一致性要求

## 结论摘要

| 后端 | 尾帧×参考图共存 | 时长/分辨率/比例 | 计价是否不同 | 两图一致性要求 |
|---|---|---|---|---|
| gemini/veo | 未确权（协议允许同传，官方文档未验证组合行为） | 相同；仅"带参考图"强制 `durationSeconds=8`，与尾帧无关 | 否（按时长×分辨率×audio 定价，无尾帧维度） | 未确权（文档未提及） |
| ark/seedance2 | **互斥**（实测 400：`first/last frame content cannot be mixed with reference media content`） | 相同（按模型档位统一，未按模式区分） | 否（按 token 估算，与角色标签无关） | 未确权（文档未提及） |
| vidu | **互斥**（代码级：见参考图即切 `/reference2video`，首尾帧被静默丢弃） | 相同（`/start-end2video` 与 `/img2video` 的 duration/resolution 规则逐模型对齐） | 否（`credits` 由 model+duration+resolution 决定，文档示例未见尾帧加价） | **有**：官方要求首尾图分辨率比在 0.8～1.25 之间，且单图比例需 <1:4 或 >4:1（代码未做校验，仅转发） |
| kling | **互斥**（架构级：参考图走独立 `multi-image2video` 端点，与 `image2video`/`image_tail` 互不相通；官方另文档 `image_tail` 与 `dynamic_masks`/`static_mask`/`camera_control` 互斥） | 未确权（官方文档未公开尾帧专属限制，本仓库按 `image2video` 统一处理） | 否（按 (model, mode) 分档定价，与尾帧/参考图无关） | 未确权（官方文档未提及） |
| agnes | **互斥**（自方 fail-loud：两者同给直接抛 `VideoCapabilityError`，非官方文档确认） | 相同（`num_frames`/分辨率算法不区分 keyframes 与单图模式，第三方文档摘要印证） | 否（按秒计价，无模式维度） | 未确权（官方文档与仓内代码均未见校验或说明） |
| v2 通用协议 | **声明可共存**（`reference_images_with_start_frame=True`，请求体同时带 `last_image_url` + `image_urls`），但未针对具体中转网关做真实集成验证 | 未确权（协议为多供应商共同子集，具体限制随实际路由的上游而定） | 否（本仓库不建模，直接透传请求；无内建定价逻辑） | 未确权 |

**系统级结论**：本仓库费用预估（`lib/pricing/` + `lib/cost_calculator.py`）的定价维度是 `(provider, model, call_type, duration, resolution, audio)`，**不含"是否尾帧模式"这一维度**——六家后端均未发现官方文档或代码证据表明尾帧模式单独计价，因此现有费用预估无需为尾帧模式新增感知逻辑；若后续某供应商更新定价把尾帧/插值列为独立计费项，需重新核实。

---

## 编排层现状（跨后端共性）

`VideoGenerationRequest`（`lib/video_backends/base.py:409-432`）同时携带 `end_image` 与 `reference_images` 两个字段，互不排斥；`MediaGenerator.generate_video_async`（`lib/media_generator.py:640-678`）把调用方传入的 `end_image`、`reference_images` **无条件透传**给 backend 的 `generate()`——是否互斥、如何互斥完全由各 backend 自行处理，编排层不做前置校验。

ArcReel 自身生产路径目前**不会同时**下发两者：分镜驱动的图生视频路径按 `reference_images_with_start_frame` 门控产品参考图二次注入（`server/services/generation_tasks.py:358-389`），该注释显式指出 `end_image`（首尾帧）路径与该门控无关、该槽位恢复使用时需另行复核；参考生视频路径（`server/services/reference_video_tasks.py`）本身不设置 `end_image`。因此"尾帧+参考图"组合当前只在 backend 防御代码里成立，尚无生产触发路径——本盘点面向未来接线该组合时的护栏设计。

`VideoCapabilities`（`lib/video_backends/base.py:378-393`）的字段语义：
- `reference_images`：后端接受 `reference_images` 字段，但多家后端把它实现为独立的"参考生视频"模式，与首帧互斥或竞争
- `reference_images_with_start_frame`：真正表示"参考图可叠加在带首帧请求上且首帧语义保持"——是否可与尾帧叠加需额外核实，字段名未覆盖尾帧维度

---

## 逐家盘点

### 1. gemini / veo（Veo 3.1）

**能力声明**：`VideoCapabilities(last_frame=True, reference_images=True, max_reference_images=3)`（`lib/video_backends/gemini.py:113`），`reference_images_with_start_frame` 维持默认 `False`。

**尾帧×参考图共存**：**未确权**。Veo 官方文档把 "Using first and last frames"（插值/尾帧）与 "Using reference images"（风格/内容参考）列为两个独立小节（`docs/google-genai-docs/veo.md:172-268`），未给出组合示例，也未明确声明互斥或允许共存。本仓库代码注释显式标注这一点：

> reference_images_with_start_frame 维持 False：Veo 文档（docs/google-genai-docs/veo.md）把 Image-to-video 与 Reference images 列为并列模式且未确权可组合，并要求带参考图时 durationSeconds 必须为 8——与短镜头时长冲突会让请求批量被拒；确权后再开启。
> —— `lib/video_backends/gemini.py:110-113`

`_create_task`（`lib/video_backends/gemini.py:171-186`）本身**不做拦截**：`end_image`（→ `last_frame`）与 `reference_images` 均可分别设值同传给 `GenerateVideosConfig`，行为取决于 Google 服务端实际处理逻辑，未经本仓库实测验证。

**时长/分辨率/比例**：官方参数表（`docs/google-genai-docs/veo.md:383-389`）：
- `lastFrame`："Must be used in combination with the `image` parameter"，取值范围（`durationSeconds`: `"4"`, `"6"`, `"8"`）与普通图生视频**相同**，未单独限制
- 唯一的时长强制规则挂在 `referenceImages`、`video`（extension）、`1080p`/`4k` 分辨率上："Must be '8' when using extension, reference images or with 1080p and 4k resolutions"——不含 `lastFrame`
- 结论：尾帧模式本身不改变时长/分辨率/比例约束；若尾帧请求同时带参考图，则继承参考图的 `duration=8` 强制规则（与是否尾帧无关，是参考图规则的连带效果）

**计价**：`_veo_video_pricing`（`lib/config/registry.py:136, 382-411`）按 `(resolution, generate_audio)` 定价（`PerSecondMatrix`），无尾帧/参考图维度；`CostCalculator.calculate_cost`（`lib/cost_calculator.py`）不读取 `end_image`/`reference_images` 字段。**计价不感知尾帧模式**。

**两图一致性**：**未确权**。官方文档仅对 `image`（首帧）注明输入图片限制（"Any image resolution and aspect ratio up to 20MB file size"，`docs/google-genai-docs/veo.md:692`），未对 `image`/`lastFrame` 两图之间的比例或尺寸一致性作任何说明。

---

### 2. ark / seedance2.0

**能力声明**：仅 `doubao-seedance-2-0` / `2.0` 系列声明 `VideoCapabilities(last_frame=True, reference_images=True, max_reference_images=9)`（`lib/video_backends/ark.py:90-95`）；其余型号返回空 `VideoCapabilities()`（不支持参考图组合）。能力表也显示 1.0 pro fast / 1.0 lite t2v 不支持首尾帧（`docs/ark-docs/seedance2.0.md:14`）。

**尾帧×参考图共存**：**互斥，已实测确认**。代码注释直接引用上游错误：

> API 拒绝首帧/尾帧与参考素材混合请求（InvalidParameter: first/last frame content cannot be mixed with reference media content，实测）——参考图是与首尾帧互斥的参考生视频模式，故不声明首帧叠加参考能力；若上游后续放开混合可重新开启。
> —— `lib/video_backends/ark.py:91-93`

`_create_task`（`lib/video_backends/ark.py:120-193`）按 `role` 字段（`first_frame`/`last_frame`/`reference_image`）把三类图片平铺进同一个 `content` 数组——本仓库代码层面**不拦截**同传两者，依赖 Ark 服务端用 400 拒绝，这与其余后端在客户端就 fail-loud 的做法不同。

**时长/分辨率/比例**：官方能力表（`docs/ark-docs/seedance2.0.md:9-26`）按型号给出统一的"输出分辨率""输出时长"，不区分文生/图生首帧/图生首尾帧模式；官方示例中"图生视频-基于首帧"（3.2 节）与"图生视频-基于首尾帧"（3.3 节）均使用 `ratio="adaptive"`，未见针对首尾帧模式的专属限制段落。**结论：尾帧模式与普通图生视频共用同一套时长/分辨率/比例限制。**

**计价**：`_ark_video_pricing`（`lib/config/registry.py:184`）为 `PerTokenVideo`，`CostCalculator._ARK_TOKENS_PER_SECOND_ESTIMATE` 按时长近似换算 token（`lib/cost_calculator.py:22-23`），与 `role` 标签无关。官方费用参考文档（`docs/ark-docs/火山方舟费用参考.md`）未提及首尾帧加价。**计价不感知尾帧模式**。

**两图一致性**：**未确权**。官方文档仅有"自动图片裁剪规则"（`docs/ark-docs/seedance2.0.md:365-372`），描述单张输入图与目标 `ratio` 不一致时的居中裁剪逻辑，未提及首帧图与尾帧图之间是否要求比例/分辨率一致。

---

### 3. vidu

**能力声明**：`VideoCapabilities(first_frame=True, last_frame=True, reference_images=True, max_reference_images=7)`（`lib/video_backends/vidu.py:191-199`），`reference_images_with_start_frame` 维持默认 `False`。

**尾帧×参考图共存**：**互斥，代码级确认**（非官方文档明文声明，而是端点路由结构决定）。`_select_endpoint`（`lib/video_backends/vidu.py:322-337`）：

```python
if refs:
    return "/reference2video"
if has_start and has_end:
    return "/start-end2video"
```

参考图存在时优先路由到 `/reference2video`，`start_image`/`end_image` 即使非空也**不会**被写入该端点的请求体（`_build_request` 的 `/reference2video` 分支只读 `reference_images`）——首尾帧被静默丢弃，而非报错。代码注释明确：

> reference_images_with_start_frame 维持 False：_select_endpoint 见参考图即切 /reference2video，start_image 不进请求体（首帧被丢弃），且多数型号不在该端点白名单内会直接 RuntimeError——参考图与首帧在 Vidu 上是互斥模式，不可叠加。
> —— `lib/video_backends/vidu.py:196-198`

**时长/分辨率/比例**：`_DURATION_RULES`（`lib/video_backends/vidu.py:62-95`）逐 `(model, endpoint)` 列出合法时长——对比同一模型的 `/img2video` 与 `/start-end2video`，取值范围**完全相同**（如 `viduq3-pro`: 两端点均为 `range(1, 17)`；`vidu2.0`: 两端点均为 `[4, 8]`）。`_RESOLUTION_WHITELIST`（`lib/video_backends/vidu.py:134-147`）按 model 而非按端点键控，同样不区分。`_ENDPOINTS_WITH_ASPECT_RATIO`（`lib/video_backends/vidu.py:131`）显示 `/img2video` 与 `/start-end2video` 均**不接受** `aspect_ratio` 字段（由输入图决定），二者待遇一致。**结论：尾帧模式的时长/分辨率/比例限制与普通图生视频相同。**

官方文档进一步印证（`docs/vidu-docs/首尾帧生视频.md:20,22`）：时长/分辨率默认值按 model 而非模式给出，与 `docs/vidu-docs/图生视频.md` 对应模型条目一致。

**计价**：`credits` 由响应体按 model+duration+resolution 决定（`docs/vidu-docs/首尾帧生视频.md:64`），三份文档（图生视频/首尾帧/参考生视频）的示例响应均为 `"credits": 12`（同一 5s/1080p 组合），未见首尾帧专属加价说明。本仓库未对 Vidu 建立静态定价表（按响应体 `credits` 实时记账），**无需在费用预估侧为尾帧模式做特殊处理**。

**两图一致性**：**有明确要求**（官方文档，代码未做校验）：

> 注1: 首尾帧两张输入图的分辨率需相近，首帧图的分辨率/尾帧图的分辨率要在0.8～1.25之间。且图片比例需要小于1:4或者4:1
> —— `docs/vidu-docs/首尾帧生视频.md:17`

`lib/video_backends/vidu.py` 的 `_build_request`（`/start-end2video` 分支，第 309-314 行）仅做文件存在性校验，未对两图分辨率比、单图比例做前置校验或告警——不合规请求会被直接转发，失败风险留给上游 400。

---

### 4. kling（可灵）

**能力声明**：按型号从 `_KLING_VIDEO_CAPS` 查表（`lib/video_backends/kling.py:67-131`）：`kling-v2-5-turbo`/`kling-v3`/`kling-v2-6` 声明 `last_frame=True, reference_images=False`；`kling-v3-omni`/`kling-video-o1` 同时声明 `last_frame=True, reference_images=True`（`max_reference_images=4`，保守值待核实）。所有型号均未设置 `reference_images_with_start_frame`（默认 `False`）。

**尾帧×参考图共存**：**互斥，架构级确认**。`_build_payload`（`lib/video_backends/kling.py:262-300`）按优先级路由：

```python
if reference_images:
    ...  # 走 multi-image2video 端点，image_list 载荷，函数在此 return
...
if start_image:
    payload["image"] = ...
    if end_image:
        payload["image_tail"] = ...  # 仅 image2video 分支可达
```

`reference_images` 存在时直接进入 `multi-image2video` 子路径并 `return`，`start_image`/`end_image` 完全不被读取——对同时声明 `last_frame`/`reference_images` 能力的 `kling-v3-omni`/`kling-video-o1`，若调用方同时传两者，尾帧会被**静默丢弃**（非报错）。这是本仓库客户端层面的路由选择，而非官方 API 显式拒绝；但外部检索证实官方文档确有相邻互斥声明：`image_tail`、`dynamic_masks`/`static_mask`、`camera_control` 三者互斥（一次只能用一个），来源 [kling.ai Image to Video API Documentation](https://kling.ai/document-api/apiReference/model/imageToVideo)（页面为 JS 渲染 SPA，WebFetch 未能取得完整正文，此结论转引自搜索引擎摘录的官方文档片段，未逐字核对原文，建议后续人工复核）；`multi-image2video`（参考图/多图主体）本身是与 `image2video`（承载首尾帧）平行的独立端点，二者天然不可在同一请求中混用。

**时长/分辨率/比例**：**未确权**。多次 WebFetch 官方 `kling.ai`/`www.klingai.com` 文档页均因 JS 渲染或非常规状态码（HTTP 446）未能取得正文；第三方镜像（Segmind）文档仅列出 `duration`（5/10s 二选一）与 `image_tail`（可选 URL），未说明尾帧模式下时长/分辨率/比例是否有专属限制。本仓库代码（`kling.py:262-276`）对 `image2video` 子路径统一处理 `duration`/`aspect_ratio`/`mode`（质量档），不因是否携带 `image_tail` 分支切换取值范围，说明**至少在本仓库集成层面尾帧模式与普通图生视频共用同一套请求字段和取值**；官方是否有更细的服务端校验未经证实。

**计价**：`_kling_video_pricing`（`lib/config/registry.py:296-297, 1173-1238`）为 `PerSecondTiered`，按 `(model, mode)` 键控（`mode` 指 `std`/`pro`/`4k` 质量档，非首尾帧/参考图维度）；`_resolve_mode`（`lib/video_backends/kling.py:236-244`）同样只看 `resolution`/`service_tier`。**计价不感知尾帧模式**。

**两图一致性**：**未确权**。官方文档与本仓库代码均未提及首尾帧图片之间的比例/尺寸一致性要求。

---

### 5. agnes

Agnes 是第三方网关 apihub.agnes-ai.com 提供的 OpenAI 风格视频端点（非一线大厂官方 API），本仓库代码多处注释标注"待 console / 实测核对，不硬编当既成事实"。

**能力声明**：`VideoCapabilities(first_frame=True, last_frame=True, reference_images=True, max_reference_images=4, reference_images_with_start_frame=False)`（`lib/video_backends/agnes.py:226-234`）。

**尾帧×参考图共存**：**互斥，自方防御性拒绝**（非官方文档确认，而是本仓库主动选择的保守策略）。`_build_payload`（`lib/video_backends/agnes.py:259-309`）显式 fail-loud：

```python
if reference_images and (start_image is not None or end_image is not None):
    raise VideoCapabilityError("video_reference_images_with_frames_unsupported", model=self._model)
```

> 参考图与首/尾帧走互斥的单通道（reference_images_with_start_frame=False）。两者同时给出时 fail-loud，而非静默走参考图分支丢掉用户的首/尾帧。
> —— `lib/video_backends/agnes.py:282-285`

官方文档（[Agnes Video V2.0](https://agnes-ai.com/en/docs/agnes-video-v20)）未明确说明 `keyframes` 模式与多图参考是否可组合（WebFetch 摘要确认："Whether keyframes and reference images can combine in a single request" 属于文档未覆盖项）——本仓库选择在不确定时直接拒绝，而非放行未经验证的组合。

另需注意：只传 `end_image` 不传 `start_image` 同样 fail-loud（`agnes.py:289-290`，`video_end_image_requires_start_image`）——Agnes 无独立尾帧通道，尾帧只在 `keyframes`（首+尾）模式下生效。

**时长/分辨率/比例**：官方文档确认（WebFetch 摘要）`num_frames <= 441`、`8n+1` 帧数规则、`480p/720p/1080p` 三档分辨率标准化，均为跨模式统一规则，未见 `keyframes` 模式专属限制；本仓库代码（`_duration_to_num_frames`/`_resolve_size`，`agnes.py:99-112`）同样不区分 `keyframes` 与单图/纯文本模式。**结论：尾帧模式与普通图生视频共用同一套时长/分辨率限制。**

**计价**：`_agnes_video_pricing`（`lib/config/registry.py:325-`）为 `PerSecondMatrix`，按秒计费，无模式维度；官方文档摘要显示当前视频按秒计费且促销价 `$0/秒`，未提及模式加价。**计价不感知尾帧模式。**

**两图一致性**：**未确权**。官方文档与仓内代码均未提及首尾帧图片比例/尺寸一致性要求或校验。

---

### 6. v2 通用协议（`/v2/video/generations`，流派 C）

该后端不对应单一供应商，而是"单端点 + model 字段切换"的中转协议事实标准（aimlapi / xAI / getimg.ai / APIMart / CometAPI 等），本仓库只编码一份跨供应商公共子集契约，不背 per-model schema（`lib/video_backends/v2_video_generations.py:1-16`）。

**能力声明**：`VideoCapabilities(first_frame=True, last_frame=True, reference_images=True, max_reference_images=4, reference_images_with_start_frame=True)`（`lib/video_backends/v2_video_generations.py:295-303`）——六家中唯一显式声明"可与首帧叠加"的后端。

**尾帧×参考图共存**：**协议层声明可共存，但未经真实网关验证**。`build_request_body`（`lib/video_backends/v2_video_generations.py:164-209`）会在同一请求体中并列写入 `image_url`（首帧）、`last_image_url`（尾帧）、`image_urls`（参考数组），三者互不冲突、可同时出现：

> 协议 body 中 image_url（首帧）与 image_urls（参考数组）为共存字段，build_request_body 同请求组装两者，首帧语义保持。
> —— `lib/video_backends/v2_video_generations.py:300-302`

但模块文档同时声明这是**尽力而为的通用契约**，非任何单一供应商的实测确认：

> 已知风险：部分中转站要求公网 URL而非 base64，真实接受形态留手动集成测试。
> —— `lib/video_backends/v2_video_generations.py:169-170`

即：本仓库*声明*三者可共存是"协议设计意图"，不是"某家网关实测通过"——具体路由到的上游供应商是否真的接受三者同传、是否会静默忽略某个字段或直接拒绝，随实际网关而定，**未确权**。

**时长/分辨率/比例**：**未确权**。generic 协议只透传 `duration`/`aspect_ratio`/`resolution` 标量字段（`build_request_body:174-182`），不做任何取值校验或按模式分支，实际约束完全取决于路由到的上游供应商，本仓库无法给出统一结论。

**计价**：本仓库未对该后端建立静态定价表（`PROVIDER_V2_VIDEO` 未出现在 `lib/config/registry.py` 的内置模型定价中），费用预估依赖用户为自定义供应商单独配置的价格（`custom_price_input`/`custom_price_output`，`lib/cost_calculator.py`），与是否尾帧模式无关。**计价不感知尾帧模式（因为压根没有内建计价逻辑）。**

**两图一致性**：**未确权**。协议层不做任何图片尺寸/比例校验，且实际约束随上游供应商而定。

---

## 信源清单

- 仓内代码：`lib/video_backends/{base,gemini,ark,vidu,kling,agnes,v2_video_generations}.py`
- 仓内文档：`docs/google-genai-docs/veo.md`、`docs/ark-docs/seedance2.0.md`、`docs/ark-docs/火山方舟费用参考.md`、`docs/vidu-docs/{首尾帧生视频,图生视频,参考生视频,文生视频}.md`
- 仓内编排层：`lib/media_generator.py`、`server/services/generation_tasks.py`、`server/services/reference_video_tasks.py`、`lib/config/registry.py`、`lib/cost_calculator.py`
- 官方文档（外部）：
  - [Kling AI — Image to Video API Documentation](https://kling.ai/document-api/apiReference/model/imageToVideo)（JS 渲染 SPA，WebFetch 无法取正文，结论转引自搜索引擎摘录）
  - [Agnes AI — Agnes Video V2.0 Docs](https://agnes-ai.com/en/docs/agnes-video-v20)
