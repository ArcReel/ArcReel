# 聚合平台与中转站视频协议形态抽样

> 用途：为 [#2119](https://github.com/ArcReel/ArcReel/issues/2119)「自定义调用端点（声明式协议定义）」提供覆盖率证据，回答 [#2121](https://github.com/ArcReel/ArcReel/issues/2121)：拟定的声明式表达能力能否覆盖聚合平台 / 中转站 / ComfyUI 类工作流平台的视频 API；表达不了的构造是什么、出现多少次。直接输入 [#2123](https://github.com/ArcReel/ArcReel/issues/2123)（定义格式与模板语言细则）、[#2125](https://github.com/ArcReel/ArcReel/issues/2125)（提交、轮询与终态语义）、[#2126](https://github.com/ArcReel/ArcReel/issues/2126)（产物与用量提取）。
> 范围：10 家平台的**协议形态**（提交请求体、素材编码、轮询、状态串、产物路径、错误路径、鉴权），不做能力矩阵、定价与优先级评估。`arcreel-video-api-protocol-research.md`（2026-05-27）已覆盖的官方平台协议（Kling / DashScope / MiniMax / Runway / Luma / PixVerse / Jimeng）与四大流派归纳不重复；该报告对 NewAPI / Kie.ai / PiAPI / fal.ai 只记录到参数对齐表级别，本文补齐其**结构**层面的细节。
> 来源纪律：只采信官方文档站、官方 OpenAPI / Apifox 页面与开源仓库源码；每家列出来源 URL。文档未明示或抓取失败者标注「未确认」，不以推测填补。多家文档站无「最后更新」日期，以调研日期为准。
> 调研日期：2026-08-26

---

## 一、核对基准：拟定的声明式表达能力

按 #2119 已定决策，候选表达能力固定为以下六项（下文以 **E1–E6** 引用）：

| 编号 | 能力 | 语义约定 |
| --- | --- | --- |
| E1 | JSON 结构模板 | 请求体以 JSON 字面结构书写，键名、嵌套层级、常量值直接落在模板里 |
| E2 | Mustache 风格 `{{ }}` 占位符 | 字符串内插；**整串单占位符保留原生类型**（`"{{ duration }}"` 渲染成数字 `5`，`"{{ image }}"` 渲染成 `inputs` 声明的编码结果）；占位符不带过滤器 / 表达式 / 条件 |
| E3 | 唯一的 `$each` 铺字段构造 | 对列表型输入（参考图数组等）展开为数组元素或重复键；除此之外不提供条件、分支、循环 |
| E4 | JSONPath（RFC 9535）优先级数组提取 | 响应取值写成路径数组，按顺序取第一个命中的值；用于 task id / 状态 / 产物 URL / 错误信息 |
| E5 | 纯字典状态 / 枚举映射 | 供应商状态串 → 五档（queued / running / succeeded / failed / expired）的静态字典；枚举参数（分辨率、时长等）同样只做字典映射；未登记串沿用 `lib/video_backends/base.py::normalize_provider_status` 的兜底（按 running 继续轮询） |
| E6 | 鉴权仅「header 名 + 值模板 / query 参数」 | 不含签名、时间戳、JWT、token 交换 |

隐含约束（由 #2119 决策推出，核对时按此执行）：

- 轮询 URL 由模板拼出（`{{ base_url }}` + 路径 + `{{ task_id }}`），task id 取自提交响应。提交响应里返回的动态 URL（fal 的 `status_url`、Segmind 的 `poll_url`、WaveSpeed / Replicate 的 `urls.get`）在本次抽样中**全部可由模板等价构造**，不构成缺口。
- 素材编码首期只有 base64 data URI 与裸 base64（独立 `inputs` 声明）。**仅接受公网 URL** 的供应商需要 #2119 fog 中的「素材公网 URL 托管」能力，核对时记为「部分（依赖 URL 托管）」而非「不可」——这是产品能力缺口，不是模板语言缺口。
- multipart 请求体不在首期（#2119 明确列为未定）。
- 响应必须是 JSON；二进制响应体、JSON-in-string（需二次解析）不在 E4 范围。
- 只做提交 + 轮询；webhook 回调不在首期，各家 webhook 形状只作记录。

核对结论三档：**可** / **部分**（依赖 fog 能力，或需在 #2123 / #2125 写明的约定）/ **不可**（现有六项表达不了，且无等价绕行）。

---

## 二、抽样清单

| # | 平台 | 类型 | 抽样端点 | 与既有报告的关系 |
| --- | --- | --- | --- | --- |
| 1 | new-api（QuantumNous） | 开源中转站内核 | `/v1/video/generations` | 既有报告流派 B；本文补三种响应形状与 `metadata` 合并机制 |
| 2 | fal.ai | 海外聚合 | queue API | 既有报告仅提 webhook；本文补全 |
| 3 | Replicate | 海外聚合 | predictions API | 既有报告列为 P3，未记录形状 |
| 4 | kie.ai | 海外中转 | `/api/v1/jobs/createTask` + 旧版 Veo / Runway | 既有报告流派 D；本文补 `resultJson` 与旧版整数状态 |
| 5 | RunningHub | ComfyUI 工作流云 | `/task/openapi/*` + `/openapi/v2/*` | 新增 |
| 6 | 302.ai | 国内中转 | `/302/v2/video/*` 统一接口 + 代理端点 | 新增 |
| 7 | PiAPI | 海外中转 | `/api/v1/task` 统一 schema | 既有报告流派 D；本文补 envelope 与错误结构 |
| 8 | WaveSpeedAI | 海外聚合 | `/api/v3/{model}` + predictions | 新增 |
| 9 | Segmind | 海外聚合（serverless + PixelFlow） | `/v1/{slug}` 同步、`/v2/{slug}` 异步 | 新增 |
| 10 | Novita AI | 海外聚合 | `/v3/async/{model}` | 新增 |

---

## 三、逐家协议形态

每家按「提交 / 素材 / 提交响应 / 轮询 / 状态 / 产物 / 错误 / 鉴权 / 结构特征 / 核对」记录。JSONPath 一律写绝对路径。

### 3.1 new-api

来源：`github.com/QuantumNous/new-api` main 分支 `router/video-router.go`、`dto/video.go`、`dto/task.go`、`model/task.go`、`relay/relay_task.go`、`relay/channel/task/kling/adaptor.go`、`relay/channel/task/taskcommon/helpers.go`、`relaykit/dto/openai_video.go`、`docs/openapi/relay.json`；文档 `github.com/QuantumNous/new-api-docs` `docs/api/generate-video.md`、`query-video.md`、`openai-video.md`；`docs.newapi.pro/en/docs/api/ai-model/videos/getvideogeneration`。

- **提交**：`POST /v1/video/generations`，`application/json`，模型在 body `model`。`dto.VideoRequest` 字段（json tag 逐字）：`model, prompt, image, duration(float64), width, height, fps, seed, n, response_format, user, metadata(map[string]any)`。文档示例含 `size: "1920x1080"` 与 `metadata: {seed, negative_prompt, image_tail}`。
- **`metadata` 机制**（`taskcommon.UnmarshalMetadata`）：先 `delete(metadata, "model")`，再把 `metadata` 序列化后反序列化进各 adaptor 的上游 payload struct——**只有与上游 struct json tag 同名的 key 才生效**，不是透明透传。Kling adaptor 可被覆盖的 key：`prompt, image, image_tail, negative_prompt, mode, duration(string), aspect_ratio, model_name, model, cfg_scale, static_mask, dynamic_masks, camera_control, callback_url, external_task_id`。
- **其他视频路由**（`video-router.go`）：`POST /v1/videos`（Sora 兼容，JSON 或 multipart）、`GET /v1/videos/:task_id`、`POST /v1/videos/:video_id/remix`、`GET /v1/videos/:task_id/content`（流式回传视频字节）、`/kling/v1/videos/{text2video|image2video}`（中间件改写为 `/v1/video/generations`，原 body 整体进 `metadata`）、`/jimeng/`（按 query `Action`）。
- **素材**：文档 `image` 字段「图片输入（URL/Base64）」；Kling adaptor 对 `req.Image` 原样转发。无独立上传 API。Sora 路径本地文件需 multipart。
- **提交响应**：Kling 渠道返回 `relaykit/dto.OpenAIVideo`：`id, task_id, object:"video", model, status:"queued", progress(int), created_at, ..., error{message, code}, metadata`；`id` 与 `task_id` 均为 `PublicTaskID`。task id：`$.task_id`（备选 `$.id`）。源码返回 200，文档写 201。
- **轮询**：`GET /v1/video/generations/{task_id}`。无同步等待；new-api 自身无 webhook。
- **轮询响应三种形状**（按渠道类型与请求路径切换，源码 `videoFetchByIDRespBodyBuilder`）：
  - (a) Gemini / VertexAI 渠道：`{"code":"success","data":{"task_id","status","url","format","metadata":null,"error":null}}`，`status` 由 `mapTaskStatusToSimple` 折成 `succeeded | failed | queued | processing`。
  - (b) 其他渠道 + `/v1/video/generations/...` 路径：`{"code":"success","data":<TaskDto>}`，`TaskDto` 含 `task_id, status, fail_reason, result_url, progress(string, 如 "50%"), data(raw)`；`status` 是内部枚举 `NOT_START | SUBMITTED | QUEUED | IN_PROGRESS | FAILURE | SUCCESS | UNKNOWN`。
  - (c) `/v1/videos/...` 路径：`OpenAIVideo`，`status ∈ unknown | queued | in_progress | completed | failed`；Kling 视频 URL 在 `$.metadata.url`，失败在 `$.error.message`。
  - 文档（`query-video.md`）示例是扁平 `{"task_id","status":"succeeded","url","format":"mp4","metadata":{...},"error":null}`；`docs.newapi.pro` 新站写 `queued/in_progress/completed/failed` + `error:{code:int, message}`；`dto.VideoTaskResponse` 在 `relay_task.go` 中零引用。**文档与源码分歧，需实测。**
- **状态集合**：三套并存——`queued | processing | succeeded | failed`（a）、`NOT_START | SUBMITTED | QUEUED | IN_PROGRESS | FAILURE | SUCCESS | UNKNOWN`（b）、`unknown | queued | in_progress | completed | failed`（c）。
- **产物**：`$.data.url`（a）/ `$.data.result_url`（b）/ `$.metadata.url`（c）/ `$.url`（文档）。`url` 可能是 new-api 自建代理 URL（`taskcommon.BuildProxyURL`），需带鉴权 `GET /v1/videos/{id}/content` 取字节。
- **错误**：HTTP 级 `dto.TaskError` `{"code":string,"message":string,"data":any}`（429 时 `message` 改写为中文）；Sora 路径 `{"error":{"message","type"}}`。任务内失败：`$.data.fail_reason`（b）/ `$.error.message`（c）。
- **鉴权**：`Authorization: Bearer sk-...`。
- **结构特征**：同一任务三种响应形状；三套状态枚举；泛型 `code/data` 外壳；`metadata` 是同名字段合并而非透传；下载 URL 需带鉴权。
- **核对**：**可**。E1/E2 覆盖提交；E4 优先级数组 `["$.data.url", "$.data.result_url", "$.metadata.url", "$.url"]` 与 `["$.data.status", "$.status"]` 覆盖三种形状；E5 字典把三套状态串全部登记（`_PROVIDER_STATUS_SYNONYMS` 已含大部分，需补 `not_start / submitted / failure / success / unknown`）。前提：下载步骤沿用提交时的鉴权 header（#2126 需写明）。Sora 路径 multipart 由内置 `openai-video` 覆盖，不在声明式范围。

### 3.2 fal.ai queue API

来源：`fal.ai/docs/model-endpoints/queue`、`fal.ai/docs/model-apis/model-endpoints/queue`、`.../synchronous-requests`、`fal.ai/docs/model-endpoints/webhooks`、`fal.ai/docs/documentation/model-apis/authentication`、`fal.ai/docs/documentation/model-apis/errors`、模型页 `fal.ai/models/fal-ai/kling-video/v2.1/standard/image-to-video/api`、`fal.ai/models/fal-ai/minimax/hailuo-02/standard/image-to-video/api`；REST 上传细节取自 `github.com/fal-ai/fal-js` `libs/client/src/storage.ts`（`fal.ai/docs/model-apis/file-uploads` 404）。文档站正从 `docs.fal.ai` 迁往 `fal.ai/docs`（308）。

- **提交**：`POST https://queue.fal.run/{app_id}`，**模型在 URL 且含多级斜杠**（`fal-ai/kling-video/v2.1/standard/image-to-video`）。body 就是模型 input，无外壳：`{"prompt","image_url","duration":"5","negative_prompt","cfg_scale":0.5}`。可选 query `?fal_webhook=`。同步：`POST https://fal.run/{app_id}`。
- **素材**：模型页逐字「Pass a Base64 data URI as a file input」（注：「for large files ... can impact the request performance」，无数值上限）；公网 URL；或 `fal.storage.upload()`。REST 上传两步：`POST https://rest.fal.ai/storage/upload/initiate?storage_type=fal-cdn-v3` → `{upload_url, file_url}` → `PUT upload_url`；≥90 MB 走 `initiate-multipart`。
- **提交响应**：`{"request_id","response_url","status_url","cancel_url","queue_position":0}`。task id：`$.request_id`。三条 URL 均可由 `{{ base_url }}/{{ model }}/requests/{{ task_id }}[/status]` 构造。
- **轮询**：`GET .../requests/{request_id}/status?logs=1` → 状态；**结果另在** `GET .../requests/{request_id}`。取消 `PUT .../cancel`。SSE `/status/stream`。
- **状态集合**：`IN_QUEUE`（含 `queue_position`）、`IN_PROGRESS`（含 `logs[]`）、`COMPLETED`（含 `metrics.inference_time`）。**失败不改状态**：文档逐字 `error` / `error_type` 「present only if the request failed」，状态仍为 `COMPLETED`。终态只有 `COMPLETED`。失败时 `GET response_url` 的 HTTP 码 / 体：未确认（`fal-js` 对非 2xx 抛错：422 读 `body.detail`，其余读 `body.message`）。
- **产物**：两模型均 `$.video.url`（`video` 为 File 对象 `{url, content_type?, file_name?, file_size?}`）。Kling v2.1：`duration` 枚举 `"5" | "10"` 字符串；Hailuo-02：`duration` `"6" | "10"`、`resolution` `"512P" | "768P"`、`end_image_url`。媒体 URL「subject to your media expiration settings」。
- **错误**：模型级 422 `{"detail":[{"loc":[...],"msg","type","url"}]}`；请求级扁平 `{"detail":"...","error_type":"request_timeout"}` + 头 `X-Fal-Error-Type`。`detail` 既可能是数组也可能是字符串。
- **鉴权**：`Authorization: Key $FAL_KEY`。
- **结构特征**：模型 id 嵌 URL 且含 `/`；status 与 result 分两个 GET；失败与成功同为 `COMPLETED`；`detail` 类型不定；枚举为字符串。
- **核对**：**部分**。E1/E2 覆盖提交（data URI 直接可用，是本次抽样中对 base64 最友好的一家）；E6 `Authorization: Key {{ api_key }}` 可。缺口：① status 端点与 result 端点分离，需要「终态后再发一个取件请求」；若 `GET .../requests/{id}` 在进行中会返回可判定的非终态体，可只轮询 result 端点绕过——**未确认**；② 失败信号不在状态串，需「错误路径命中即失败」规则；③ `["$.detail[0].msg", "$.detail"]` 需只接受字符串命中。

### 3.3 Replicate predictions API

来源：`replicate.com/docs/reference/http`、`api.replicate.com/openapi.json`、`replicate.com/docs/topics/predictions/{lifecycle,input-files,output-files,data-retention,create-a-prediction}`、`replicate.com/docs/topics/webhooks/receive-webhook`、`replicate.com/docs/reference/error-codes`、changelog `2024-05-01-improved-validation-for-api-prediction-payloads`、`replicate.com/{owner}/{name}/llms.txt`；源码 `replicate/replicate-python` `replicate/exceptions.py`、`replicate/replicate-javascript` README。

- **提交**：`POST https://api.replicate.com/v1/predictions`（`version` 在 body：`{owner}/{name}` | `{owner}/{name}:{version_id}` | `{version_id}`）或 `POST /v1/models/{owner}/{name}/predictions`（模型在路径，body 无 `version`）。body：`{"version","input":{"prompt","start_image":"https://..."},"webhook","webhook_events_filter":["start","output","logs","completed"]}`。头 `Prefer: wait` / `wait=n`（1–60 s）；超时「returned in a "starting" state」。成功码 201（含终态）/ 202（未完成）。
- **素材**：openapi 逐字「Files should be passed as HTTP URLs or data URLs. Use an HTTP URL when: you have a large file > 256kb ... Use a data URL when: you have a small file <= 256kb」；`input-files` 页另写「only recommended if the file is less than 1MB」（两处口径不一）。Files API `POST /v1/files` multipart（≤100 MiB，24 h 过期），响应 `urls.get` 填进 `input`。
- **提交 / 轮询响应同形**：`{"id","model","version","input","logs","output","error":null,"status","created_at","started_at","completed_at","source","data_removed":false,"metrics":{"predict_time"},"urls":{"web","get","cancel"}}`。task id：`$.id`。
- **轮询**：`GET /v1/predictions/{id}`（= `$.urls.get`）；取消 `POST /v1/predictions/{id}/cancel`。
- **状态集合**（openapi enum）：`starting, processing, succeeded, failed, canceled, aborted`；终态 `succeeded / failed / canceled / aborted`。
- **产物**：抽样的 `minimax/video-01`、`google/veo-3`、`kwaivgi/kling-v2.1`、`bytedance/seedance-1-pro`、`wan-video/wan-2.2-i2v-fast` 的 Output schema 均为 `string(format: uri)`，即 `$.output` 直接是 URL。openapi 定义 `output` 为「any JSON-serializable value, depending on the model」——数组 / 对象形状逐模型而异。输出文件默认「automatically removed after an hour」。
- **错误**：HTTP 级 RFC 7807（`type, title, status, detail, instance`），422 另带 `invalid_fields[{type, field, description}]`；429 `{"detail":"Request was throttled..."}`。任务内：`$.error`（string | null）+ `status: "failed"`；平台错误码 `E####`。
- **鉴权**：openapi 逐字「prefixed by "Bearer"」；同一 openapi 的 files 示例用 `Authorization: Token ...`，两种前缀并存，Bearer 为规范写法。
- **结构特征**：嵌套 `input{}`；模型选择两种互斥方式；`output` 类型不固定；data URI 256 KB 建议上限；`Prefer: wait` 可能返回非终态；六态而非五态。
- **核对**：**可**（约束：素材 ≤256 KB 走 data URI；更大需 URL 托管或 Files API multipart）。E4 `["$.output[0]", "$.output"]` 覆盖数组 / 字符串两种 `output`（RFC 9535 对字符串值做 `[0]` 无命中，自然落到第二条）；E5 把 `starting → queued`、`canceled / aborted → failed`；201 / 202 均按 2xx 成功。

### 3.4 kie.ai

来源：`docs.kie.ai/market/common/get-task-detail`、`docs.kie.ai/market/kling/text-to-video`、`docs.kie.ai/market/grok-imagine/image-to-video`、`docs.kie.ai/veo3-api/{generate-veo-3-video,get-veo-3-video-details,generate-veo-3-video-callbacks}`、`docs.kie.ai/runway-api/{generate-ai-video,get-ai-video-details}`、`docs.kie.ai/file-upload-api/{quickstart,upload-file-base-64,upload-file-url}`、`docs.kie.ai/`。（`/market/common/create-task` 404，以模型页为准。）

- **提交（统一）**：`POST https://api.kie.ai/api/v1/jobs/createTask`，JSON，模型在 body `model`，参数全部嵌在 `input{}`：`{"model":"kling-2.6/text-to-video","callBackUrl","input":{"prompt","sound":false,"aspect_ratio":"1:1","duration":"5"}}`；图生视频 `input.image_urls:["https://..."]`（「Up to 7 images」）。
- **素材**：生成接口**仅公网 URL**。独立上传 API：`POST .../api/file-base64-upload`（JSON，`base64Data` 接受「plain Base64 string or a Data URL format」）、`file-url-upload`、`file-stream-upload`（multipart），返回 `data.downloadUrl`。base URL 文档不一（`kieai.redpandaai.co` vs `api.kie.ai`）、保留期 24 h / 3 天两说——未确认。产物 URL 保留 14 天。
- **提交响应**：`{"code":200,"msg":"success","data":{"taskId":"task_kling-2.6_1765182425861"}}`。task id：`$.data.taskId`。
- **轮询**：`GET /api/v1/jobs/recordInfo?taskId=`。响应 `data{taskId, model, state, param(string), resultJson(string), failCode, failMsg, costTime, completeTime, progress, creditsConsumed}`。webhook `callBackUrl`，回调体与 `data` 同构。
- **状态集合**：`waiting / queuing / generating / success / fail`；终态 `success / fail`。
- **产物**：`$.data.resultJson` 是 **JSON 字符串**（类型 string），二次解析后 `resultUrls[0]`；变体 `{"resultUrls":[],"firstFrameUrl":[],"lastFrameUrl":[]}`、`{"resultObject":{...}}`。`param` 同样 JSON-in-string。
- **错误**：外层 `{"code":<int>,"msg"}`，业务码 `200/400/401/402/404/422/429/455/500/501/505`（HTTP 状态是否恒 200 未确认）；任务内 `$.data.failCode`（string）、`$.data.failMsg`。
- **鉴权**：`Authorization: Bearer`。
- **旧版 Veo**：`POST /api/v1/veo/generate` 扁平 body（`prompt, imageUrls[], model, aspect_ratio, ...`）；`GET /api/v1/veo/record-info?taskId=` → `data{paramJson(string), response{resultUrls[], originUrls[], fullResultUrls[], resolution}, successFlag, errorCode, errorMessage, fallbackFlag}`；**状态是整数 `successFlag`**：`0` Generating / `1` Success / `2` Failed / `3` Generation Failed；视频 `$.data.response.resultUrls[0]`（真数组）。generate 页回调示例把 `resultUrls` 写成字符串化数组、callbacks 页写数组——两种都要容忍。业务码 400 = 「1080P processing; check in 1-2 minutes」（非错误）。
- **旧版 Runway**：`POST /api/v1/runway/generate`（`callBackUrl` 必填）；`GET /api/v1/runway/record-detail?taskId=` → `data.state ∈ wait / queueing / generating / success / fail`（拼写与统一接口不同）；视频 `$.data.videoInfo.videoUrl`；`expireFlag` 0/1；回调体另一套键名（`data.video_url / task_id / image_url`）。
- **结构特征**：嵌套 `input{}`；`resultJson` / `param` JSON-in-string；HTTP 200 + 整数业务码；三套状态表示（字符串 `state` / 整数 `successFlag` / `wait | queueing` 变体）。
- **核对**：统一接口 **不可**——产物在 JSON 字符串内，E4 取不到；素材仅 URL（依赖托管）。旧版 Veo / Runway **部分**：E4 取 `$.data.response.resultUrls[0]` / `$.data.videoInfo.videoUrl` 可；E5 字典对整数 `successFlag` 需约定「非字符串值按字符串化后查表」（`{"0":"running","1":"succeeded","2":"failed","3":"failed"}`）；旧版 Runway `callBackUrl` 必填但首期无 webhook，需允许填一个占位 URL——未确认服务端是否校验可达性。

### 3.5 RunningHub

来源（均在 `www.runninghub.cn/runninghub-api-doc-cn/`）：`doc-8287334`（开始 / v2 说明）、`api-425749012` / `api-425749013`（发起 ComfyUI 任务 简易 / 高级）、`api-425749010`（发起 AI 应用任务）、`api-425749003`（查询状态，弃用）、`api-425749004`（查询结果，弃用）、`api-425767306`（查询结果 V2）、`api-425749008`（上传，弃用）、`api-425749007`（文件上传 新）、`api-425749009`（取消）、`api-425749005` / `api-425749006`（webhook）、`doc-8287342`（工作流接入示例）、`doc-8287338`（错误码；英文 `runninghub.ai/runninghub-api-doc-en/doc-8287467`）、`api-448183131` / `api-459865180`（模型 API v2 示例）。两代并存：旧 `/task/openapi/*`（工作流与 AI 应用创建仍是唯一入口）与新 `/openapi/v2/*`（模型 API + 统一查询 + 上传）。

- **提交（工作流）**：`POST https://www.runninghub.cn/task/openapi/create`，JSON：`{"apiKey","workflowId":"1904136902449209346","nodeInfoList":[{"nodeId":"6","fieldName":"text","fieldValue":"1 girl in classroom"}],"webhookUrl","instanceType":"plus","addMetadata":true,"usePersonalQueue":false,"workflow":"<完整 workflow JSON 字符串>","accessPassword","retainSeconds":60}`。**`apiKey` 在 body**。AI 应用：`POST /task/openapi/ai-app/run`，`webappId` 为 int64。
- **提交（模型 API v2）**：`POST /openapi/v2/{endpoint}`（如 `/openapi/v2/vidu/text-to-video-q3-turbo`），扁平 body `{"prompt","aspectRatio":"16:9","resolution":"720p","duration":"5","audio":true}`，模型在路径；图片 `imageUrls:["https://..."]`。工作流 / AI 应用的 v2 提交路径未在文档找到——未确认。
- **素材**：旧 `POST /task/openapi/upload`，**multipart**（`apiKey, file, fileType:"input"`），响应 `data.fileName:"api/9d77b8...png"`——「返回的 fileName 字段是文件在服务器上的相对路径，请勿随意拼接为外链」；用法是把该相对路径填进 `nodeInfoList` 的 `LoadImage` 节点 `fieldValue`。单文件 30 MB。新 `POST /openapi/v2/media/upload/binary`，multipart 仅 `file`，Bearer；响应 `data{download_url, fileName, type, size}`，「上传后的获得的链接具有一天有效期」「此接口不支持输入url」。**base64 未提**。
- **提交响应（旧）**：`{"code":0,"msg":"success","data":{"taskId":"1910246754753896450","taskStatus":"QUEUED","clientId","netWssUrl":null,"promptTips":"{\"result\": true, \"error\": null}"}}`；task id `$.data.taskId`；`taskStatus ∈ CREATE, SUCCESS, FAILED, RUNNING, QUEUED`；`promptTips` JSON-in-string。**提交响应（v2）**无 envelope：`{"taskId","status":"RUNNING","errorCode":"","errorMessage":""}`。
- **轮询（旧）**：`POST /task/openapi/status` body `{"apiKey","taskId"}` → `data` 为裸字符串 `QUEUED | RUNNING | FAILED | SUCCESS`（弃用）。`POST /task/openapi/outputs` body `{"apiKey","taskId"}`，**状态在外层整数 `code`**：`0` 成功（`data:[{"fileUrl","fileType":"png","taskCostTime","nodeId","consumeCoins"}]`）、`804` 运行中、`813` 排队、`805` 失败（`data.failedReason{node_name, exception_message, traceback}`）。示例脚本每 5 s 轮询、10 分钟超时。
- **轮询（v2）**：`POST /openapi/v2/query` body `{"taskId"}`（仅 Bearer）→ `{"taskId","status":"SUCCESS","errorCode":"","errorMessage":"","results":[{"url":"https://...mp4","outputType":"mp4"}],"failedReason":{...},"usage":{...}}`；`status ∈ QUEUED, RUNNING, SUCCESS, FAILED`；处理中 `results` 为 `null`。
- **webhook**：`webhookUrl`；回调体 `{"event":"TASK_END","taskId","eventData":"<JSON 字符串，结构同 outputs 响应>"}`。
- **产物**：旧 `$.data[0].fileUrl`（多输出节点按 `nodeId` / `fileType` 筛：`$.data[?@.fileType=='mp4'].fileUrl`）；v2 `$.results[0].url`。
- **错误**：HTTP 200 + `{"code":<int>,"msg":"<IDENT>"}`：`301 PARAMS_INVALID`、`380 WORKFLOW_NOT_EXISTS`、`412 TOKEN_INVALID`、`415/421` 实例 / 队列上限、`416/812` 余额不足、`423/807 TASK_NOT_FOUND`、`433 VALIDATE_PROMPT_FAILED`、`435`（需 `instanceType: plus`）、`801–813`、`901 WEBAPP_NOT_EXISTS`、`1000+` 系统 / 模型错误。v2 失败示例 `{"code":401,"message":"ApiKey verification failed..."}`（envelope 键是 `message` 不是 `msg`）。
- **鉴权**：旧接口 `apiKey` 在 body，同时 header 表列 `Authorization: Bearer` 与 `Host: www.runninghub.cn`（均标 Required）；v2 仅 Bearer。英文站 base URL `www.runninghub.ai`。
- **结构特征**：凭证入 body；`Host` 头；`nodeInfoList` 对象数组（值类型须与节点原字段一致）；状态用整数 `code`；`status` 接口 `data` 为裸字符串；JSON-in-string（`promptTips`、`eventData`）；`outputs` 成功时 `data` 是数组、失败时是对象；multipart 上传返回相对路径而非 URL；`webappId` int64（JS 精度）；两代 envelope 键名不同（`msg` vs `message`）。
- **核对**：
  - 工作流 / AI 应用 **图生视频：不可**——素材必须经 multipart 上传换相对路径，没有 base64 / URL 通道；URL 托管也救不了（节点吃的是服务器相对路径）。
  - 工作流 / AI 应用 **文生视频：部分**——`nodeInfoList` 可作为 E1 字面数组写死（每个自定义调用端点本就对应一条工作流），`fieldValue: "{{ prompt }}"` 用 E2；`apiKey` 入 body 可用 header Bearer 替代（文档标两者均 Required，实测是否缺一不可未确认）；轮询是 **POST + JSON body**，需轮询模板支持方法与 body；状态取 `$.code` 整数按字符串查表 `{"0":"succeeded","804":"running","813":"queued","805":"failed"}`；`Host` 作静态 header。
  - 模型 API v2 **部分**——形态最规整（路径含模型、扁平 body、v2 query 无 envelope）；素材仅 URL（依赖托管）；轮询仍是 POST + body。

### 3.6 302.ai

来源（`doc.302.ai` 及 Apifox 镜像 `s.apifox.cn/apidoc/docs-site/4012774`）：统一接口 V2 创建 `/336214131e0`、查询 `/336229957e0`、webhook 示例 `/336230197e0`、模型列表 `/370490119e0`；V1 创建 `/268354205e0`、查询 `/268356563e0`；Kling 代理 `/305339559e0`、`/305527618e0`；MiniMax 代理 `/api-310678678`、`/211531465e0`、`/211531587e0`；`/302/submit` 系列 `/263524906e0`、`/305583456e0`、`/313890156e0`；HTTP 状态码 `/3704965m0`；错误体示例 `/351856639e0`；迁移指南 `/3704971m0`。Base URL：`api.302.ai`（海外）、`api.302ai.com` / `api.302ai.cn`（国内）。

- **提交（统一 V2）**：`POST /302/v2/video/create`，JSON 或 multipart，模型在 body `model`（「支持 - 或 _ 分隔，大小写不敏感」）：`{"model","prompt","image","end_image","negative_prompt","duration":10,"resolution":"1080p","aspect_ratio":"16:9"}`。任务类型自动判定（有 `image / end_image` → I2V，有 `video` → V2V）。webhook 文档标为 **query 参数**。
- **素材**（原句）：`image` 「Base64 图片(需要带上信息头; eg: data:{content_type};base64,{base64_str})」或公网 URL 或文件流；`video` 「仅支持使用 URL 链接形式传参」。
- **提交响应**：`{"task_id":"301504317665393","status":"pending","created_at"}`。task id `$.task_id`。
- **轮询**：`GET /302/v2/video/fetch/{task_id}`，无 envelope：`{"video_url","status":"completed","task_id","model","raw_response":"...","attempts","execution_time","upstream_task_id",...}`。
- **状态集合**：`pending / processing / completed / failed`；终态 `completed / failed`。
- **产物**：`$.video_url`。`raw_response` 标 string（可能是 JSON 字符串，未确认）。
- **错误**：失败时任务内错误字段 schema 未定义——未确认。HTTP 级（401 示例）：`{"error":{"err_code":10001,"message":"Invalid API Key","message_cn","message_jp","type":"unauthorized"}}`；是否全端点统一此形状未确认。
- **webhook 回调体**与 fetch 形状不同：`{"model","result":{"video_url","raw_response":{...},...},"status","request_id","webhook"}`。
- **鉴权**：`Authorization: Bearer sk-...`（个别旧代理用 `mj-api-secret` 头）。
- **V1（旧）**：`POST /302/video/create` → `{"task_id","data":{"requestId"}}`；`GET /302/video/fetch/{task_id}` → `{"status":"success","url","data":{"video":{"url"},"status":"COMPLETED","request_id"}}`；顶层 `status ∈ success, fail, queue, processing`，`data.status` 是上游值（双层状态）。
- **代理型端点**：Kling 官方格式 `/klingai/v1/videos/text2video`（`data.task_status ∈ submitted, processing, succeed, failed`，`data.task_result.videos[0].url`）；MiniMax `/minimaxi/v1/video_generation` 三步取件（`file_id` → `files/retrieve`）；`/302/submit/*` fal 风格（`request_id` + `IN_QUEUE / COMPLETED`，`video.url`）；不循任何模式的 `/klingai/m2v_omni_3_video`（`data.task.id` + 整数 `status 10/50/99`）、`/chanjing/open/v1/video`（整数 `10/30/4X/5X`）、`/runway/submit`（`THROTTLED`）。
- **结构特征**：V2 无 envelope；V1 双层 status；同一平台六套状态词表；webhook 走 query、回调形状 ≠ fetch；模型有的在 body 有的在路径；三个 base URL 域名。
- **核对**：统一 V2 **可**——是本次抽样中最贴合 E1–E6 的形态（JSON、data URI、字符串状态、扁平产物）。代理型端点各自归内置 endpoint（`kling-video`、`minimax-video`）或按 3.2 fal 风格处理；整数状态按字符串化查表。

### 3.7 PiAPI

来源：`piapi.ai/docs/unified-api-schema`（「the old API schema is not deprecated」）、`piapi.ai/docs/kling-api/{create-task,get-task}`、`piapi.ai/docs/hailuo-api/generate-video`、`piapi.ai/docs/wan-api/wan26-image-to-video`、`piapi.ai/docs/veo3-api/get-task`、`piapi.ai/docs/unified-webhook`、`piapi.ai/docs/tools/file-upload`。Legacy 页（`/docs/legacy/kling-api/*`）抓取 404——未确认。

- **提交**：`POST https://api.piapi.ai/api/v1/task`，JSON，模型由 body `model` + `task_type` 选择：`{"model":"kling","task_type":"video_generation","input":{"prompt","negative_prompt","cfg_scale":0.5,"duration":5,"aspect_ratio":"16:9","version":"2.6","mode":"std","image_url"},"config":{"service_mode":"public","webhook_config":{"endpoint","secret"}}}`。
- **素材**：Kling `image_url` 「No larger than 10MB」、Hailuo `image_url` 「Image URL must be jpg/png...」——**仅 URL**；Wan 2.6 字段名 `image`，「Image url or base64 string」——URL 或裸 base64。独立上传 `POST https://upload.theapi.app/api/ephemeral_resource`（JSON `{file_name, file_data:<base64>}`，≤10 MB）→ `data.url`。
- **提交响应**：`{"code":200,"data":{"task_id","model","task_type","status":"pending","config","input","output":null,"meta":{...,"usage":{"type":"point","frozen","consume"}},"detail":null,"logs":null,"error":{"code":0,"raw_message":"","message":"","detail":null}},"message":"success"}`。task id `$.data.task_id`。
- **轮询**：`GET /api/v1/task/{task_id}`，响应同形。webhook：`config.webhook_config`，回调体 `{"timestamp","data":{...}}`，secret 在头 `x-webhook-secret`。
- **状态集合**：OpenAPI enum 首字母大写 `Completed | Processing | Pending | Failed | Staged`，所有示例与 webhook 文档均小写 `pending / processing / completed / failed / staged`（`staged` 仅 Midjourney）。终态 `completed / failed`。
- **产物**（随模型变化）：Kling 标准 `$.data.output.video_url`，同时 `$.data.output.works[0].video.resource` / `.resource_without_watermark`，以及**整数** `$.data.output.status: 99` 与 `works[].status: 99`；Kling turbo / Kling 3 omni / Hailuo / Veo3 用 `$.data.output.video`；Wan 2.6 完成态字段名文档无示例——未确认。
- **错误**：任务内 `$.data.status == "failed"` + `$.data.error{code:int, raw_message, message, detail}`（成功时 `code: 0`）。HTTP 级 body 不统一：`{"code":400,"message"}` / `{"error":"Unauthorized..."}` / `{"description":"Unauthorized..."}`；`/api/v1/task` 自身 4xx 精确 body 未确认。
- **鉴权**：`x-api-key: <key>`。
- **结构特征**：三层嵌套 `input{}` + `config{}`；顶层 `code` 与 `data.error.code` 两套整数码；Kling 输出同时有整数 `status` 与字符串 status；产物字段随 `model / task_type` 变化；状态大小写文档自相矛盾。
- **核对**：**部分**——模板语言够用（E1 嵌套、E4 `["$.data.output.video_url","$.data.output.video","$.data.output.works[0].video.resource_without_watermark","$.data.output.works[0].video.resource"]`、E5 大小写不敏感由 `normalize_provider_status` 已保证）；缺口只在素材：Kling / Hailuo 仅 URL（依赖托管），Wan 可裸 base64。

### 3.8 WaveSpeedAI

来源：`wavespeed.ai/docs/rest-api`、`/docs/submit-task`、`/docs/get-result`、`/docs/sync-mode`、`/docs/base64-output`、`/docs/upload-files`、`/docs/upload-files-api`、`/docs/how-to-use-webhooks`、`/docs/verify-webhooks`、`/docs/error-codes`；模型页 `wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.1-i2v-480p`、`kwaivgi/kling-v3.0-std/image-to-video`。（`kwaivgi/kling-v2.1-i2v-standard` 页 404——未确认。）

- **提交**：`POST https://api.wavespeed.ai/api/v3/{model_id}`，模型 id 内嵌路径含 `/`（`wavespeed-ai/wan-2.1/i2v-480p`）。JSON：`{"prompt","image":"https://...","size":"832*480","num_inference_steps":30,"duration":5,"guidance_scale":5,"flow_shift":3,"seed":-1,"enable_sync_mode":false,"enable_base64_output":false}`。webhook 通过 **query** `?webhook=`。
- **素材**：视频模型 `image` 「URL of the source image.」/「upload or public URL」；仅个别非视频模型写「URL or base64 data URI」；视频模型是否接受 data URI **未确认**。上传两步：`POST /api/v3/media/uploads`（JSON `{filename, size, content_type}` → `download_url` + `upload.{url, method:"PUT", headers}`）再 PUT 字节；legacy 单步 `POST /api/v3/media/upload/binary` multipart `file`。≤200 MiB，保留 7 天。
- **提交响应**：`{"code":200,"message":"success","data":{"id":"pred_abc123","model","status":"created","urls":{"get":"https://api.wavespeed.ai/api/v3/predictions/pred_abc123/result"},"created_at"}}`。task id `$.data.id`。
- **轮询**：`GET /api/v3/predictions/{id}/result`。同步：`enable_sync_mode: true`（约 120 s 内直接返回终态）；**超时返回 HTTP 200 但 `data.status: "processing"` + `data.code: 5004`**。webhook 回调 `{id, model, status, outputs, error}`，HMAC-SHA256 验签（`webhook-id / webhook-timestamp / webhook-signature: v3,<hex>`）。
- **状态集合**：`created / processing`（非终态）；`completed / failed / cancelled / timeout`（终态）。
- **产物**：`$.data.outputs[0]`（字符串数组；`enable_base64_output: true` 时为无 MIME 前缀的裸 base64）。跨模型一致。另 `$.data.has_nsfw_contents[]`、`$.data.timings.inference`。
- **错误**：任务内 `$.data.status == "failed"` + `$.data.error`（字符串）+ `$.data.code`（整数业务码：`1200` 内容审核、`1400` 缺参、`1401` 参数无效、`1402` 媒体 URL 不可访问、`1403` 执行失败、`1405/1406/1407`、`5000/5003/5004`）。HTTP 级 4xx 精确 body 未确认。
- **鉴权**：`Authorization: Bearer`。
- **结构特征**：模型 id 嵌路径；外层 `code` 与内层 `data.code` 同名不同义；sync 超时 200 非终态；`outputs` 字符串数组；webhook 走 query。
- **核对**：**部分**——模板语言够用（E4 `$.data.outputs[0]`、`$.data.error`；E5 `timeout / cancelled → failed`）；缺口只在素材：data URI 未确认，按仅 URL 处理（依赖托管）。

### 3.9 Segmind

来源：`docs.segmind.com/docs/serverless-api`、`/docs/serverless-api/{async-inference,segmind-storage,webhooks,video-editing}`、`/docs/sdks/python`、`/docs/pixelflow/api-reference`；模型页 `segmind.com/models/kling-image2video/api`、`segmind.com/models/kling-3-standard-image2video/api`；PixelFlow 页 `segmind.com/pixelflows/ai-shorts-generator/api`；`docs.segmind.com/readme/pricing-and-billing`（`x-remaining-credits`）。

- **提交（V1 同步）**：`POST https://api.segmind.com/v1/{slug}`，JSON 或 multipart；视频模型页逐字「Response Type: Video (binary, not JSON)」；「V1 endpoints: For requests completing within ~60 seconds」。
- **提交（V2 异步）**：`POST https://api.segmind.com/v2/{slug}`，同 body：`{"prompt","start_image_url":"https://...","duration":"5","cfg_scale":0.5,"aspect_ratio":"16:9"}`。PixelFlow：`POST /workflows/v2/{slug}`（旧式 `/workflows/{id}-v3`）。
- **素材**：Kling 页 `image` / `start_image_url` 类型 `string (uri)`，「Max 50MB」；视频模型接受裸 base64 的语句官方页未见——**未确认**。独立上传 `POST https://workflows-api.segmind.com/upload-asset`（JSON `{"data_urls":["data:image/jpeg;base64,..."]}`，「Files must be provided as base64-encoded data URLs.」）→ `file_urls[]`。
- **提交响应（V2）**：`{"request_id","status":"QUEUED","poll_url":"https://api.segmind.com/v1/requests/{id}","status_url":"https://api.segmind.com/v2/requests/{id}/status","response_url":"https://api.segmind.com/v2/requests/{id}"}`。task id `$.request_id`。`poll_url` 指向 legacy v1。
- **轮询**（两步）：`GET /v2/requests/{id}/status` → `{"status":"COMPLETED","request_id","metrics":{cost, inference_time, queue_time, total_time, remaining_credits}}`；再 `GET /v2/requests/{id}` 取结果。结果保留 1 小时。webhook 非 per-request：控制台 / SDK 全局注册（`NODE_RUN / GRAPH_RUN`），payload 与签名未确认。
- **状态集合**：`QUEUED / PROCESSING`；`COMPLETED / FAILED`（终态）。
- **产物**：V2 视频 `{"status":"COMPLETED","video":{"url","content_type":"video/mp4","file_name","file_size"},"output":"https://..."}` → `$.output` 或 `$.video.url`；V1 同步 body 即 mp4 字节；PixelFlow `$.output` 是 **JSON 字符串**（`"[{\"keyname\":\"image\",\"value\":{...}}]"`）。
- **错误**：V2 任务失败 **HTTP 422** + `{"status":"FAILED","error":"Prompt is Mandatory and must be string","metrics"}`；PixelFlow 失败 `{"status":"FAILED","error_message":{...}}`（对象）。HTTP 级 400/401/403/404/406/422/429/500，仅 200 计费；响应头 `x-remaining-credits`。
- **鉴权**：`x-api-key`。
- **结构特征**：同一 slug 两种语义（V1 二进制 / V2 JSON）；三个动态 URL；status 与 result 分端点；PixelFlow JSON-in-string；失败以非 2xx 返回；webhook 非 per-request。
- **核对**：V1 **不可**（非 JSON 响应）。V2 **部分**：E4 `["$.output","$.video.url"]` 可；缺口 ① status / result 分端点（`GET /v2/requests/{id}` 在进行中返回什么未确认，若可判定则可单端点轮询）；② 失败以 HTTP 422 返回，`poll_with_retry` 需把该 4xx 视为终态而非传输错误（#2125）；③ 素材仅 URL（依赖托管）。PixelFlow **不可**（`output` JSON-in-string；`error_message` 为对象）。

### 3.10 Novita AI

来源（`novita.ai` 直抓 404，以 Context7 `/websites/novita_ai` 官方镜像 + 搜索摘要为准）：`novita.ai/docs/api-reference/{model-apis-wan-i2v,model-apis-wan-t2v,model-apis-kling-v3.0-std-t2v,model-apis-task-result,model-apis-webhook,model-apis-seedream-4-0,model-apis-exa-search}`、`novita.ai/docs/guides/error`。

- **提交**：`POST https://api.novita.ai/v3/async/{model}`（`wan-i2v`、`wan-2.2-i2v`、`kling-v1.6-i2v`、`kling-v3.0-std-t2v`、`txt2video`、`img2video`），模型在路径。JSON：`{"prompt","image_url":"https://...","negative_prompt","width":720,"height":1280,"steps":25,"seed":-1,"guidance_scale":5.0,"loras":[{"path","scale":1}],"extra":{"webhook":{"url","test_mode":{"enabled":false,"return_task_status":"TASK_STATUS_SUCCEED"}}}}`。
- **素材**：`wan-i2v` / `kling-v1.6-i2v` `image_url` 「URL of the input image.」——仅 URL；旧 `/v3/async/img2video` 的 `image_file`（base64）原页未抓到——未确认；seedream-4.0（图像）明确「either accessible URLs or Base64 encoded strings」，视频模型无此声明。无独立上传 API 文档。
- **提交响应**：`{"task_id":"9490dde7-..."}`（扁平）。task id `$.task_id`。
- **轮询**：`GET /v3/async/task-result?task_id=`（**query**）。响应：`{"task":{"task_id","task_type":"WAN_IMG_TO_VIDEO","status":"TASK_STATUS_SUCCEED","reason":"","eta":0,"progress_percent":100},"images":[],"videos":[{"video_url","video_url_ttl":"3600","video_type":"mp4"}],"audios":[],"extra":{...}}`。webhook `extra.webhook.url`，回调 `{"event_type":"ASYNC_TASK_RESULT","payload":{...同形...}}`；签名未确认。
- **状态集合**：官方页仅出现 `TASK_STATUS_SUCCEED`、`TASK_STATUS_FAILED`（终态）；`TASK_STATUS_QUEUED / TASK_STATUS_PROCESSING` 仅 JS SDK 有 `TaskStatus.QUEUED`——未确认。
- **产物**：`$.videos[0].video_url`，跨视频模型一致；`video_url_ttl` 字符串 `"3600"`（`image_url_ttl` 在另一示例为整数，类型不稳定）。
- **错误**：HTTP 级 `{"code":400,"reason":"INVALID_REQUEST_BODY","message","metadata":{"trace_id"}}`；`403 INVALID_API_KEY`、`404 PATH_NOT_FOUND`、`429 RATE_LIMIT_EXCEEDED`、`500 TASK_FAILED`、`503 SERVICE_UNAVAILABLE`。任务内 `$.task.status == "TASK_STATUS_FAILED"`，原因 `$.task.reason`。
- **鉴权**：`Authorization: Bearer`。
- **结构特征**：提交扁平、轮询嵌套 `task{}` + 三个并列结果数组；task_id 走 query；前缀式大写状态串；中间态未证实。
- **核对**：**部分**——模板语言够用（E5 登记 `task_status_succeed / task_status_failed / task_status_queued / task_status_processing`；未登记串按 running 兜底恰好覆盖「中间态未证实」）；缺口只在素材仅 URL（依赖托管）。

---

## 四、横向对照

| 维度 | new-api | fal | Replicate | kie.ai | RunningHub | 302.ai V2 | PiAPI | WaveSpeed | Segmind V2 | Novita |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 模型选择 | body `model` | URL 路径（含 `/`） | body `version` 或路径 | body `model` | body `workflowId` / 路径（v2） | body `model` | body `model`+`task_type` | URL 路径（含 `/`） | URL 路径 | URL 路径 |
| 参数嵌套 | 扁平 + `metadata{}` | 扁平 | `input{}` | `input{}` | `nodeInfoList[]` / 扁平（v2） | 扁平 | `input{}`+`config{}` | 扁平 | 扁平 | 扁平 + `extra{}` |
| 图片素材 | URL / base64 | URL / data URI / 上传 | URL / data URI ≤256 KB / Files | 仅 URL（另有上传 API） | multipart → 相对路径 / URL（v2） | URL / data URI / 文件流 | 仅 URL（Wan 裸 base64） | URL（data URI 未确认） | 仅 URL（上传 API 收 data URL） | 仅 URL |
| task id | `$.task_id` | `$.request_id` | `$.id` | `$.data.taskId` | `$.data.taskId` / `$.taskId` | `$.task_id` | `$.data.task_id` | `$.data.id` | `$.request_id` | `$.task_id` |
| 轮询 | GET 路径 | GET 路径 ×2 | GET 路径 | GET query | **POST body** | GET 路径 | GET 路径 | GET 路径 | GET 路径 ×2 | GET query |
| 状态载体 | 字符串（3 套） | 字符串 | 字符串 | 字符串 / 整数（旧 Veo） | 整数 `code` / 字符串（v2） | 字符串 | 字符串（大小写不定） | 字符串 | 字符串 | 字符串 |
| 终态 | `completed / succeeded / SUCCESS`, `failed / FAILURE` | `COMPLETED`（失败靠 `error`） | `succeeded / failed / canceled / aborted` | `success / fail` | `SUCCESS / FAILED`（或 `0 / 805`） | `completed / failed` | `completed / failed` | `completed / failed / cancelled / timeout` | `COMPLETED / FAILED`（422） | `TASK_STATUS_SUCCEED / _FAILED` |
| 视频 URL | `$.data.url` 等 4 路径 | `$.video.url` | `$.output` | **JSON 字符串内** | `$.data[0].fileUrl` / `$.results[0].url` | `$.video_url` | `$.data.output.video_url` / `.video` / `.works[]` | `$.data.outputs[0]` | `$.output` / `$.video.url` | `$.videos[0].video_url` |
| 任务内错误 | `$.data.fail_reason` / `$.error.message` | `$.error` / `$.detail` | `$.error` | `$.data.failMsg` | `$.data.failedReason.exception_message` / `$.errorMessage` | 未确认 | `$.data.error.message` | `$.data.error` | `$.error` | `$.task.reason` |
| 鉴权 | `Authorization: Bearer` | `Authorization: Key` | `Authorization: Bearer` | `Authorization: Bearer` | body `apiKey` + Bearer + `Host` | `Authorization: Bearer` | `x-api-key` | `Authorization: Bearer` | `x-api-key` | `Authorization: Bearer` |
| 产物 TTL | 代理 URL 24 h cache | 按账户设置 | 1 h | 14 天 | 上传件 1 天 | 未确认 | 未确认 | 7 天（上传件） | 1 h | `video_url_ttl` 3600 s |

---

## 五、覆盖核对表

按「E1–E6 + 一节的隐含约束」逐家判定。「阻塞项」只列造成非「可」的原因。

| 平台 / 端点 | E1 结构 | E2 占位 | E3 `$each` | E4 JSONPath | E5 映射 | E6 鉴权 | 结论 | 阻塞项 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| new-api `/v1/video/generations` | 可 | 可 | 可 | 可（4 路径优先级） | 可（3 套串） | 可 | **可** | — |
| fal.ai queue | 可 | 可（URL 含 `{{ model }}`） | 可（`image_urls[]` 类字段） | 可 | 可 | 可 | **部分** | status / result 分端点；失败不改状态串；`detail` 类型不定 |
| Replicate predictions | 可 | 可 | 可 | 可（`output` 串 / 数组） | 可 | 可 | **可** | 素材 >256 KB 需 URL 托管 |
| kie.ai `jobs/createTask` | 可 | 可 | 可（`image_urls[]`） | **不可**（`resultJson` JSON-in-string） | 可 | 可 | **不可** | JSON-in-string；素材仅 URL |
| kie.ai 旧版 Veo / Runway | 可 | 可 | 可 | 可 | 可（整数 `successFlag` 需字符串化） | 可 | **部分** | 素材仅 URL；`callBackUrl` 必填（Runway） |
| RunningHub 工作流 T2V | 可（`nodeInfoList` 字面） | 可 | 不需要 | 可（`$.code` 整数、`$.data[?@.fileType=='mp4'].fileUrl`） | 可（整数码字符串化） | 部分（body `apiKey`，有 Bearer 替代） | **部分** | 轮询 POST + body；凭证入体 |
| RunningHub 工作流 I2V | 可 | 可 | 可 | 可 | 可 | 部分 | **不可** | multipart 上传必经，产出相对路径 |
| RunningHub 模型 API v2 | 可 | 可（URL 含 `{{ model }}`） | 可（`imageUrls[]`） | 可 | 可 | 可 | **部分** | 素材仅 URL；轮询 POST + body |
| 302.ai 统一 V2 | 可 | 可 | 不需要（单图字段） | 可 | 可 | 可 | **可** | 失败错误字段未确认 |
| PiAPI `/api/v1/task` | 可 | 可 | 可（`elements[]` 类字段） | 可（按模型 4 路径优先级） | 可 | 可 | **部分** | 素材仅 URL（Wan 除外） |
| WaveSpeed `/api/v3/{model}` | 可 | 可（URL 含 `{{ model }}`） | 可 | 可 | 可 | 可 | **部分** | 素材 data URI 未确认 |
| Segmind V1 同步 | 可 | 可 | 可 | **不可**（二进制体） | — | 可 | **不可** | 非 JSON 响应 |
| Segmind V2 异步 | 可 | 可 | 可 | 可 | 可 | 可 | **部分** | status / result 分端点（未确认能否单端点）；失败 HTTP 422；素材仅 URL |
| Segmind PixelFlow | 可 | 可 | 可 | **不可**（`output` JSON-in-string） | 可 | 可 | **不可** | JSON-in-string |
| Novita `/v3/async/{model}` | 可 | 可（URL 含 `{{ model }}`） | 可 | 可 | 可 | 可 | **部分** | 素材仅 URL |

**按平台计**（每家取其主推异步端点）：可 3（new-api、Replicate、302.ai）；部分 5（fal、PiAPI、WaveSpeed、Segmind V2、Novita）；不可 2（kie.ai 统一接口、RunningHub 工作流 I2V）。

**按缺口归因**：5 家「部分」中 4 家（PiAPI、WaveSpeed、Novita、Segmind V2 的素材项）**唯一或主要阻塞是素材仅接受公网 URL**——模板语言本身已覆盖其全部请求 / 响应结构。因此：

- 只看模板语言（E1–E6），10 家中 **8 家可表达**主推异步端点的提交、轮询、状态、产物、错误全链路；表达不了的只有 kie.ai 统一接口（JSON-in-string）与 RunningHub 工作流 I2V（multipart 必经）。
- 叠加首期「仅 base64 素材」约束后，可直接落地做图生视频的只剩 new-api、fal、Replicate（≤256 KB）、302.ai、PiAPI（仅 Wan）5 家。**「素材公网 URL 托管」是覆盖率的最大单一杠杆**，把可用面从 5 家抬到 8 家。

---

## 六、表达不了的构造及出现频次

频次按平台计（同一平台多个端点只记一次），区分「主推端点必经」与「有绕行 / 仅旧版 / 可选」。

### 6.1 模板语言层面（E1–E6 缺口）

| # | 构造 | 必经（平台） | 可选 / 旧版 / 有绕行（平台） | 频次 | 备注 |
| --- | --- | --- | --- | --- | --- |
| U1 | **产物 / 关键字段为 JSON-in-string，需二次解析** | kie.ai 统一（`data.resultJson`）、Segmind PixelFlow（`output`） | RunningHub（`promptTips`、webhook `eventData`，非关键路径）、302.ai（`raw_response`，非关键路径）、kie.ai（`param`、旧 Veo 回调 `resultUrls` 字符串化数组） | 必经 2 / 涉及 5 | E4 在 RFC 9535 内无法解析字符串值。若给 JSONPath 提取结果加一个「结果再按 JSON 解析后继续取路径」的固定后缀（非表达式），可收编这 2 家；这是抽样中**唯一**能靠模板语言小改换来整家覆盖的构造 |
| U2 | **素材必须经独立上传 API，再以返回值引用**（multipart 或 JSON） | RunningHub 工作流（multipart，返回服务器相对路径，URL 托管无法替代） | fal（PUT 二进制）、Replicate Files（multipart，>256 KB）、WaveSpeed（PUT 二进制 / multipart）、kie.ai（JSON base64 → URL）、PiAPI（JSON base64 → URL）、Segmind（JSON data URL 数组 → URL） | 必经 1 / 涉及 7 | 7 家里 3 家（kie.ai、PiAPI、Segmind）的上传 API 是**纯 JSON + base64**，若格式支持一个「前置请求」步骤，它们的「素材仅 URL」缺口无需公网托管即可解决；multipart / PUT 类 4 家仍不可 |
| U3 | **终态后需第二个请求取产物**（status 端点与 result 端点分离） | fal（`/status` → `/`）、Segmind V2（`/status` → `/`） | RunningHub 旧版（`status` → `outputs`，但 `outputs` 单端点即可判态）、new-api 代理 URL（`/content`，本质是带鉴权下载，不算取件）、302.ai MiniMax 代理三步（归内置 `minimax-video`） | 必经 2 / 涉及 5 | 两家的 result 端点在进行中返回什么均未确认；若返回可判态 JSON 则可退化为单端点轮询。#2119 已把「两步流程」排除在首期外 |
| U4 | **失败信号不在状态串** | fal（`COMPLETED` + `error` 字段） | — | 1 | 需「错误路径命中即 failed」规则；纯字典映射无法表达「字段存在」条件 |
| U5 | **失败以非 2xx HTTP 码从轮询端点返回** | Segmind V2（HTTP 422 + `status: FAILED`） | — | 1 | `poll_with_retry` 现按 HTTP 码分类可重试 / 不可重试；需在 #2125 声明「非 2xx 但体可解析且状态命中终态 → 按终态处理」 |
| U6 | **轮询请求为 POST + JSON body** | RunningHub（旧版与 v2 均是） | — | 1 | 取决于轮询模板是否允许声明方法与 body；#2123 若只允许 GET + URL 模板则不可 |
| U7 | **凭证进入请求体** | — | RunningHub 旧版（body `apiKey`；文档同时要求 Bearer，实测是否缺一不可未确认） | 0 / 涉及 1 | E6 只放 header / query。若 body 模板允许引用 `{{ api_key }}` 即可表达，但与「分享文件不含凭证」的边界需明确 |
| U8 | **非 JSON 响应体** | Segmind V1（mp4 字节） | — | 1 | 有 V2 异步替代，不建议支持 |
| U9 | **multipart 提交请求体** | — | new-api Sora 路径、302.ai V2、Segmind（均有 JSON 替代） | 0 / 涉及 3 | 与 #2119 「multipart 未定」一致：本次抽样无一家必经 |

### 6.2 产品能力层面（非模板语言缺口）

| 构造 | 平台 | 频次 |
| --- | --- | --- |
| 视频模型的图片素材**仅接受公网 URL**（无 base64 通道） | kie.ai、PiAPI（Kling / Hailuo）、Segmind、Novita、RunningHub v2、WaveSpeed（未确认，按仅 URL 计） | 6 / 10 |
| 素材 base64 有大小上限（超过需 URL） | Replicate（256 KB 建议值） | 1 |
| 必填 webhook URL 而首期不做 webhook | kie.ai 旧版 Runway（`callBackUrl` 必填） | 1 |

### 6.3 可表达但须在 #2123 / #2125 / #2126 写成约定的构造

| 约定 | 平台 | 频次 |
| --- | --- | --- |
| URL 路径 / query 模板可含 `{{ model }}`（**不对 `/` 做 percent-encoding**）与 `{{ task_id }}` | fal、WaveSpeed、Segmind、Novita、RunningHub v2、Replicate（路径式）、kie.ai / Novita（query 式） | 7 |
| 状态值为整数或以 envelope 业务码承载：字典键为字符串，非字符串原值先字符串化再查表 | RunningHub 旧版（`code` 0/804/813/805）、kie.ai 旧 Veo（`successFlag`）、PiAPI Kling（`output.status: 99`，冗余）、302.ai 代理（`10/50/99`） | 4 |
| 提交失败以 HTTP 200 + 业务码表达：约定「task id 路径无命中 ⇒ 提交失败，错误信息按错误路径取」 | kie.ai、RunningHub、PiAPI、WaveSpeed、new-api（`code:"success"` 外壳） | 5 |
| E4 优先级数组只接受**字符串**命中（跳过数组 / 对象 / null） | Replicate（`output` 串 / 数组）、fal（`detail` 串 / 数组）、Segmind PixelFlow（`error_message` 对象）、RunningHub（`outputs` 成功数组 / 失败对象） | 4 |
| 数字型输入需渲染为字符串枚举（`"5"`）：走 E5 枚举字典而非 E2 | fal Kling（`"5" \| "10"`）、kie.ai（`"5"`）、Segmind（`"5"`）、RunningHub v2（`"5"`） | 4 |
| 静态 header（`Host`、`Prefer: wait`、`X-*` 版本头）与静态 query（`?logs=1`） | RunningHub、Replicate、fal | 3 |
| 提交成功码 201 / 202 均按成功 | Replicate、new-api（源码 200 / 文档 201） | 2 |
| 状态比较大小写不敏感（`normalize_provider_status` 已保证） | PiAPI（`Completed` vs `completed`） | 1 |
| 五档中的 `expired`：抽样 10 家**无一家**输出过期类状态；`timeout`（WaveSpeed）、`aborted / canceled`（Replicate）、`cancelled`（WaveSpeed）均应映射为 `failed` | — | 0 |
| `_PROVIDER_STATUS_SYNONYMS` 需补登记的串 | `not_start / failure / unknown / create`（new-api、RunningHub）、`in_queue`（fal）、`starting / aborted`（Replicate）、`waiting / queuing / wait`（kie.ai）、`staged`（PiAPI）、`timeout`（WaveSpeed）、`task_status_*`（Novita） | — |

---

## 七、对下游议题的输入

- **#2123（格式细则）**：① URL 模板须支持路径 / query 内占位符且不转义 `/`（7 家）；② 轮询模板需允许声明方法与 body（RunningHub 一家，可决定不支持并明示）；③ 建议评估给 E4 加「命中值按 JSON 再解析」的固定后缀——成本是 JSONPath 之外的一个布尔开关，收益是 kie.ai 统一接口与 Segmind PixelFlow 两家从不可变可（U1）；④ 优先级数组只接受字符串命中需写进 schema 说明；⑤ 数字→字符串枚举走 E5，不给 E2 加转换。
- **#2125（提交、轮询与终态）**：① 「错误路径命中即 failed」规则（fal，U4）；② 轮询端点非 2xx 但体含终态的处理（Segmind，U5）；③ 「task id 无命中 ⇒ 提交失败」约定（5 家）；④ 整数状态字符串化查表（4 家）；⑤ `expired` 不需要从声明式映射产生（0 家），可只允许映射到四档。
- **#2126（产物与用量）**：① 产物下载沿用提交鉴权 header（new-api 代理 URL）；② 产物 TTL 最短 1 h（Replicate、Segmind、Novita `3600`），转存时限与既有报告 §5.2 一致；③ 用量字段路径抽样：Segmind `$.metrics.cost` / `remaining_credits`、PiAPI `$.data.meta.usage.consume`、RunningHub `$.data[0].consumeCoins` / `$.usage`、kie.ai `$.data.creditsConsumed`、fal `$.metrics.inference_time`、Replicate `$.metrics.predict_time`——无统一形状，按 E4 单路径提取即可。
- **#2119 fog「素材公网 URL 托管」**：本次抽样给出量化依据——6 / 10 家的视频模型仅收 URL，是覆盖率最大单一杠杆；其次是「前置上传请求」（3 家纯 JSON 上传 API 可借此免托管），两者二选一即可把可落地面从 5 家抬到 8 家。
- **起步模板选型**（#2130）：「通用提交 + 轮询」以 302.ai V2 / WaveSpeed 形态为范本（扁平 JSON、字符串状态、扁平产物）；「ComfyUI 工作流」以 RunningHub 工作流 T2V 为范本，但需在模板注释里明示 I2V 不可用（U2）。

---

## 修订注

- 与 `arcreel-video-api-protocol-research.md` 的关系：该报告 §7.1 把「声明式 YAML 覆盖率」估为 50–60%，依据是官方平台的 JWT / SigV4 / multipart / 两步流程。本文限定在聚合平台 / 中转站抽样，得到的是**模板语言层面 8 / 10**、**叠加首期素材约束后 5 / 10** 两个数字；两组结论对象不同，不构成矛盾，也不修订该报告——#2119 已决定用新 ADR 表述选型现状。
