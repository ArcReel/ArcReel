# 内置供应商的模型列表 API 调研

> 日期：2026-10-08。对应 [#3083](https://github.com/ArcReel/ArcReel/issues/3083)（地图 [#3079](https://github.com/ArcReel/ArcReel/issues/3079)）。代码位置以 main `c9bf02cd3` 为准。
> 问题：ArcReel 的 11 个内置供应商中，哪些提供列出模型的 API，返回哪些元信息，能否让内置供应商像自定义供应商一样「获取模型」。
> 方法：只读官方 API 文档、官方 SDK 源码（仓库 `.venv` 中已安装的 google-genai 2.25.0、openai 2.54.0、xai_sdk 1.20.0、volcengine-python-sdk 5.0.45）和官方 GitHub 仓库。没有用任何凭证调用接口。少数路由存在性用不带凭证的 GET 探测过，已在对应处注明。标注「未验证」的条目需要一次带凭证的调用才能确认。

## 结论速览

- **能用现有凭证列模型的有 7 家**：gemini-aistudio、gemini-vertex、openai、grok、dashscope、minimax、agnes（agnes 的接口存在，但没有写进公开文档）。
- **不能列模型的有 4 家**：ark 和 ark-agent-plan 的列表接口都在管控面，要 AK/SK 签名，ArcReel 只存 API Key；kling 和 vidu 没有模型列表接口。
- **元信息够用的只有 2 家**：dashscope 的 `GET /api/v1/models` 带模态、能力、token 上限、分段价格和下线时间；grok 的 REST 接口带模态、token 单价和图片单价，但视频模型不带价格。gemini-aistudio 带 token 上限和 `supportedGenerationMethods`，其余几家基本只有模型 id。
- **结果因账号而异**：方舟要先开通模型；xAI 的 API Key 有模型 ACL，US 区域返回的列表不同；百炼按 workspace 授权；Agnes 的国内 key 与国际 key 不通用；OpenAI 按组织权限列出。
- **推荐**：内置供应商的「获取模型」只做 id 级发现，用来标出「本账号可用」和「目录外的新模型」，元信息仍以模型目录（出厂快照 + 同步）为准。dashscope 和 grok 的列表可以作为价格与能力同步的候选来源。ark、ark-agent-plan、kling、vidu 不提供获取模型，只依赖模型目录。详见[对 #3085 的建议](#对-3085-的建议)。

## 逐供应商对比

| 供应商 | 列表接口 | 鉴权 | 现有凭证可用 | 媒体类型或能力 | token 上限 | 价格 | 弃用状态 | 账号或地域差异 |
|---|---|---|---|---|---|---|---|---|
| gemini-aistudio | `GET /v1beta/models`（SDK `client.models.list()`） | API Key | 是 | `supportedGenerationMethods` | 输入、输出 | 无 | 无，只在网页公布 | 未查明 |
| gemini-vertex | `GET v1beta1/publishers/google/models`（SDK 同名方法） | 服务账号 OAuth | 是 | REST 有 `launchStage`，SDK 丢弃 | 无 | 无 | `versionState`（SDK 丢弃） | 未查明 |
| ark | 管控面 `ListFoundationModels`、`ListModelActivations` 等 | AK/SK | 否 | `TaskTypes`、`Domains` | 仅 `ListModelActivations` 分档有 | `ListModelActivations` 有 | `Retiring`、`IsDeprecated` | 需先开通模型 |
| ark-agent-plan | 管控面 `ListArkAgentPlanModel` | AK/SK | 否 | 无，只有 `ModelID` | 无 | 无 | 无 | 按套餐版本 |
| grok | `GET /v1/models`、`/v1/language-models`、`/v1/image-generation-models`、`/v1/video-generation-models` | Bearer | 是 | 按接口分类，带 `input_modalities` / `output_modalities` | `context_length`、`max_prompt_length` | 文本与图片有，视频无 | 无 | 按 key 的 ACL；US 区域不同 |
| openai | `GET /v1/models` | Bearer | 是 | 无 | 无 | 无 | `shutdown_date` | 按组织 |
| vidu | 无 | — | — | — | — | — | — | 国内站与国际站目录不同 |
| dashscope | `GET /api/v1/models` | Bearer | 是 | `capabilities`、`inference_metadata` | 有，含推理模式 | 有，含分段 | `inference_offline_info.offline_time` | 按地域 host；workspace 过滤未查明 |
| minimax | `GET /v1/models`、`GET /anthropic/v1/models` | Bearer / `X-Api-Key` | 是 | 无 | 无 | 无 | 无 | 未查明 |
| kling | 无 | — | — | — | — | — | — | 按账号的资源包 |
| agnes | `GET /v1/models`（未写进文档） | Bearer | 是 | 无 | 无 | 无 | 无 | 国内 key 与国际 key 不通用 |

ArcReel 现有的自定义供应商发现代码（`lib/custom_provider/discovery.py`）已经覆盖其中三种形态：`_discover_openai` 可直接用于 openai、minimax、agnes 和 grok 的 `/v1/models`（但 OpenAI SDK 只取 id，不读 grok 的价格字段），`_discover_google` 可直接用于 gemini-aistudio。dashscope、gemini-vertex 和 grok 的完整元信息需要新的发现实现。

## 1. Gemini API（gemini-aistudio）

**接口。**`GET https://generativelanguage.googleapis.com/v1beta/models`，支持 `pageSize`（默认 50，上限 1000）和 `pageToken` 分页（[Models API](https://ai.google.dev/api/models)，页面更新于 2026-09-23）。鉴权用 API Key，SDK 以 `x-goog-api-key` 头发送。ArcReel 的 `api_key` 凭证足够。

**返回字段。**Model 资源包含 `name`、`baseModelId`、`version`、`displayName`、`description`、`inputTokenLimit`、`outputTokenLimit`、`supportedGenerationMethods`、`thinking`、`temperature`、`maxTemperature`、`topP`、`topK`（同上页面及 `generativelanguage` discovery 文档）。

- 没有价格、上线时间、模态和弃用字段。弃用和关停日期只在 [Deprecations](https://ai.google.dev/gemini-api/docs/deprecations) 网页公布。
- `supportedGenerationMethods` 是方法名字符串，例如 `generateContent`。Imagen 和 Veo 用的 `models.predict` 与 `models.predictLongRunning` 是文档里的方法，但列表中某个模型具体返回哪些方法名，官方文档没有给出示例（未验证）。
- 原生图像模型（如 `gemini-3.1-flash-image`）和文本模型都走 `generateContent`，仅凭方法名无法区分文本与图像。

**SDK 映射。**google-genai 的 `google/genai/models.py::_Model_from_mldev` 把 `supportedGenerationMethods` 映射为 `supported_actions`，token 上限映射为 `input_token_limit` / `output_token_limit`，不映射 `baseModelId`。`config.query_base` 默认为 True，列出基础模型；为 False 时列出调优模型（`google/genai/_transformers.py::t_models_url`）。ArcReel 的 `_discover_google` 已经读取 `output_token_limit` 并预填到文本模型。

## 2. Vertex AI（gemini-vertex）

**SDK 行为。**ArcReel 用服务账号 JSON 加 `location="global"` 创建 `genai.Client(vertexai=True)`（`lib/backends/text_backends/gemini.py`）。此时 `client.models.list()` 请求 `https://aiplatform.googleapis.com/v1beta1/publishers/google/models`。路径以 `publishers/` 开头时，SDK 不拼接 `projects/{p}/locations/{l}/`（`google/genai/_api_client.py` 约 1370–1385 行）。鉴权 scope 为 `cloud-platform`，现有服务账号凭证可用。

**REST 接口。**[`publishers.models.list`](https://docs.cloud.google.com/vertex-ai/docs/reference/rest/v1beta1/publishers.models) 只存在于 v1beta1，v1 只有 `get`。PublisherModel 字段包括 `name`、`versionId`、`openSourceCategory`、`supportedActions`、`launchStage`（EXPERIMENTAL、PRIVATE_PREVIEW、PUBLIC_PREVIEW、GA）、`versionState`（STABLE、UNSTABLE）、`predictSchemata` 等。

- 这里的 `supportedActions` 是控制台操作入口（部署、打开 Notebook 等），不是生成方法。
- 没有显示名、token 上限、价格或关停日期。

**SDK 丢弃的字段。**`google/genai/models.py::_Model_from_vertex` 只映射 `name`、`displayName`、`description`、`versionId` 等，`launchStage`、`versionState`、`supportedActions` 都被丢弃。PublisherModel 本身没有 `displayName`，实际只能拿到资源名和版本。要拿到 `launchStage` 需要绕过 SDK 直接请求 REST。

## 3. 火山方舟（ark）

**数据面没有列表接口。**方舟 API 参考目录（[导航](https://www.volcengine.com/docs/82379/1520758)）没有 models 资源。官方 SDK `volcenginesdkarkruntime/_client.py::Ark` 挂载了 chat、embeddings、images、content_generation、responses、files、batch 等资源，没有 models（已核对 5.0.45 与 PyPI 最新 5.0.50）。仓库 `docs/api-docs/providers/ark.md` 中的「[模型列表](https://www.volcengine.com/docs/82379/1330310)」是给人看的网页，列出上下文窗口、最大输出、RPM/TPM 和「即将下线」标记，不是 API。

**管控面接口都要 AK/SK。**管控面 host 为 `https://ark.cn-beijing.volcengineapi.com/`，请求形如 `POST /?Action=<X>&Version=2024-01-01`，用 HMAC-SHA256 V4 签名，文档注明「本接口仅支持 Access Key 鉴权」（[Base URL 与鉴权](https://www.volcengine.com/docs/ark/base-url-and-authentication?lang=zh)）。ArcReel 的 ark 凭证只有 `api_key`，不能调用以下接口：

| Action | 主要字段 | 来源 |
|---|---|---|
| `ListFoundationModels` | `Name`、`DisplayName`、`PrimaryVersion`、`FoundationModelTag.Domains`（LLM、ComputerVision、MultiModal 等）、`FoundationModelTag.TaskTypes`（TextGeneration、TextToImage、ImageToVideo 等）。无 token 上限、价格和弃用字段 | [文档](https://www.volcengine.com/docs/ark/list-foundation-models-api?lang=zh) |
| `ListFoundationModelVersions` | `ModelVersion`、`Status`（含 `Retiring` 下线中）、`PublishTime` | [文档](https://www.volcengine.com/docs/ark/list-foundation-model-versions-api?lang=zh) |
| `ListModelActivations` | 本账号开通状态 `State`、`IsDeprecated`、`ChargeItems`（单价与单位）、`MultiChargeItems`（按 `MaxPromptTokens` 分档的价格）、免费额度 | [文档](https://www.volcengine.com/docs/ark/list-model-activations-api?lang=zh) |
| `ListEndpoints` | 推理接入点 `ep-xxx`、绑定的基础模型与版本、状态 | SDK `volcenginesdkark/api/ark_api.py::ArkApi.list_endpoints` |

`ListModelActivations` 是唯一能回答「本账号开通了哪些模型、单价多少」的接口，但已安装的 `volcenginesdkark` 没有封装它，需要手工签名。

**结果因账号而异。**用 API Key 按 Model ID 调用前，账号必须先开通对应模型。未开通时返回 `404 ModelNotOpen`（「当前账号 %s 暂未开通 %s 模型服务」），部分账号还可能返回 `ModelIDAccessDisabled`（[错误码](https://www.volcengine.com/docs/ark/error-codes?lang=zh)）。只有 AK/SK 鉴权的数据面调用才必须使用接入点 ID。

## 4. 火山方舟 Agent Plan（ark-agent-plan）

**管控面接口。**`ListArkAgentPlanModel`（请求体 `{Edition: personal|enterprise}`）和 `ListArkCodingPlanModel` 返回 `Result.Datas[{ModelID}]`，只有模型 id。两者同样只支持 AK/SK（[ListArkAgentPlanModel](https://www.volcengine.com/docs/ark/list-ark-agent-plan-model-api?lang=zh)，页面更新于 2026-09-22；[ListArkCodingPlanModel](https://www.volcengine.com/docs/ark/list-ark-coding-plan-model-api?lang=zh)）。

**数据面没有文档化的列表接口。**`/api/plan/v3` 下没有找到 models 接口。`lib/custom_provider/discovery.py::_discover_anthropic` 的注释记载方舟网关的 `/v1/models` 只认 `Authorization: Bearer`，这是实测行为，官方文档没有对应说明。

**人读目录。**[Agent Plan 支持模型](https://www.volcengine.com/docs/82379/2366394)按模型列出领域、上下文与最大输出、包含该模型的套餐档位和「即将下线」标记。Seedance 视频模型只在部分档位中提供。

## 5. xAI（grok）

**REST 接口**（Bearer，base `https://api.x.ai`，[Models API](https://docs.x.ai/developers/rest-api-reference/inference/models)）：

| 路径 | 主要字段 |
|---|---|
| `GET /v1/models` | `id`、`aliases`、`created`、`owned_by`、`context_length`、`capabilities.reasoning_effort`、文本 token 单价（含缓存与长上下文）、`long_context_threshold`、`image_price`、`pricing[]`（按质量和分辨率的图片单价） |
| `GET /v1/language-models` | 在上表基础上增加 `version`、`fingerprint`、`input_modalities`、`output_modalities`、`search_price` |
| `GET /v1/image-generation-models` | `max_prompt_length`、`image_price`、`pricing[]`、模态 |
| `GET /v1/video-generation-models` | `id`、`aliases`、`version`、模态。**没有价格字段** |

每个路径都有对应的 `/{model_id}` 单条查询。

**价格单位。**token 单价的单位是「USD cents per 100 million tokens」，例如 20000 表示每百万 token 2 美元。`pricing[].price_per_image` 的单位是「1/100,000,000ths of a USD cent」。`image_price` 文档写作「USD cents」，但示例值 `200000000` 只有按 1e-8 美分理解才合理，文档本身前后矛盾（未验证）。

**gRPC SDK。**ArcReel 的 grok 文本 backend 使用 `xai_sdk` gRPC 客户端（`lib/backends/grok_shared.py`）。`xai_sdk/sync/models.py` 提供 `list_language_models`、`list_image_generation_models`、`list_embedding_models`，没有视频模型的 RPC。`xai_sdk/proto/v6/models_pb2.pyi` 的 Modality 枚举只有 TEXT、IMAGE、EMBEDDING。

**账号与地域。**proto 注释写明列表是「available to your team (based on the API key)」。API Key 可配置模型 ACL，默认无权限（[Auth](https://docs.x.ai/developers/rest-api-reference/management/auth)）。US 区域端点 `https://us.api.x.ai/v1/models` 返回的列表不同，只有部分文本模型，且 token 价格更高（[Regions](https://docs.x.ai/developers/advanced-api-usage/regions)）。

## 6. OpenAI（openai）

**接口。**`GET https://api.openai.com/v1/models`，Bearer 鉴权，不分页（[List models](https://developers.openai.com/api/reference/resources/models/methods/list)；SDK `openai/pagination.py::SyncPage` 注明尚无分页）。

**返回字段。**`id`、`object`、`created`、`owned_by`、`shutdown_date`。`shutdown_date` 是 2026-08 新增字段，已安装的 openai 2.54.0 的 `openai/types/model.py::Model` 没有声明，但 SDK 的模型基类允许额外字段，仍可读出（`openai/_models.py`）。没有能力、模态、token 上限和价格字段。

**账号差异。**调用需要 `api.model.read` 权限，文档描述为「List models this organization has access to」（[RBAC](https://developers.openai.com/api/docs/guides/rbac)）。项目级模型允许列表是否过滤 `/v1/models` 未查明。列表是否包含 `gpt-image-*`、`sora-2` 和 TTS 模型，官方示例只用占位 id（未验证）。

## 7. Vidu（vidu）

**没有模型列表接口。**文档索引（[llms.txt](https://platform.vidu.cn/docs/llms.txt)）只有任务查询、任务列表、取消和积分接口。不带凭证请求 `GET https://api.vidu.cn/ent/v2/models` 返回 404。

**相关接口。**

- `GET /ent/v2/credits` 返回剩余积分、并发和资源包信息，不含模型信息（[账户余额](https://platform.vidu.cn/docs/api-reference/task-management/account-balance.md)）。字段表写 `remaining_credits`，示例 JSON 写 `remains`，文档前后不一致。
- `GET /ent/v2/tasks` 可按 `model_versions` 过滤，返回每个任务的 `model`，只能看到用过的模型（[任务列表](https://platform.vidu.cn/docs/api-reference/task-management/task-list.md)）。

**可机读的替代来源。**「[模型地图](https://platform.vidu.cn/docs/overview/model-map.md)」以 Markdown 表格按系列列出模型 id（如 `viduq3-pro`、`viduq3-turbo`、`viduq2`），行是清晰度、帧率、时长和各生成模式是否支持。国际站的[模型地图](https://platform.vidu.com/docs/overview/model-map.md)多列出 `vidu2.0`，两站目录不同。仓库 `docs/api-docs/providers/vidu.md` 引用的旧「功能清单」地址已显示「文档未找到」，新的[功能清单](https://platform.vidu.cn/docs/overview/function-list.md)是功能分类，不含模型 id。价格见 [pricing.md](https://platform.vidu.cn/docs/overview/pricing.md)，弃用只在[更新日志](https://platform.vidu.cn/docs/api-reference/updates.md)中以文字说明。

官方 GitHub 组织 shengshu-ai 的 `vidu-cli` 在 `src/validators.rs` 中有模型版本枚举，但它面向消费端服务，不是 `/ent/v2` 企业 API，不适用于 ArcReel。

## 8. 阿里百炼（dashscope）

**原生列表接口。**`GET /api/v1/models`，`Authorization: Bearer {API_KEY}`，ArcReel 的 `api_key` 凭证足够（[查询模型列表](https://help.aliyun.com/zh/model-studio/list-models)）。

**查询参数。**`name`、`model`、`language`、`page_no`、`page_size`（默认 20）、`providers`、`inference_providers`、`capabilities`（如 `TG`、`VU`、`IG`、`VG`、`TTS`）、`features`（如 `function-calling`、`structured-outputs`、`cache`、`batch`）、`context_window`、`service_site`、`supports`。

**返回字段**（`output.models[]`）：

| 字段 | 含义 |
|---|---|
| `model`、`name`、`description` | 模型 id、名称、描述 |
| `provider`、`inference_provider` | 模型作者、推理服务方 |
| `capabilities`、`features` | 能力代码与特性 |
| `inference_metadata.request_modality` / `response_modality` | 输入与输出模态，取值 Text、Image、Audio、Video |
| `model_info` | `context_window`、`max_input_tokens`、`max_output_tokens`、`max_reasoning_tokens` 等，`null` 表示无限制或不适用 |
| `prices[]` | `range_name`（如 `Default` 或 `32k<Input<=128k`）加 `prices[{type, price, price_unit, price_name}]` |
| `published_time`、`equivalent_snapshot` | 发布时间、对应快照模型 |
| `inference_offline_info.offline_time` | 预计下线时间 |

文档示例响应省略了 `capabilities` 和 `inference_offline_info`，但字段说明里有这两项。

**地域 host。**文档列出的 host 为北京 `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com`、新加坡 `https://dashscope-intl.aliyuncs.com`、香港 `https://cn-hongkong.dashscope.aliyuncs.com`，以及东京、法兰克福、弗吉尼亚的 `{WorkspaceId}` 形式 host。文档没有列出 ArcReel 默认的 `https://dashscope.aliyuncs.com`。官方 SDK `dashscope/models.py::Models.list` 用 `dashscope.base_http_api_url` 拼接 `models`，`dashscope/common/env.py` 对北京地域保留旧 host `https://dashscope.aliyuncs.com/api/v1`，说明旧 host 很可能也可用（未验证）。SDK 的 `Models.list` 只支持分页参数，过滤要直接请求 HTTP 接口。

**OpenAI 兼容模式。**`/compatible-mode/v1/models` 没有写进[兼容模式文档](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)。

**管控面。**ModelStudio OpenAPI 2026-02-10 版本有 `ListModels`（`GET /modelstudio/models`）、`ListModelPermissions`（按 workspace 的推理授权）和 `ListModelLimits`，需要 AK/SK（[API 元数据](https://api.aliyun.com/meta/v1/products/ModelStudio/versions/2026-02-10/api-docs.json)）。数据面 `/api/v1/models` 是否按 workspace 授权过滤，文档只描述为「平台上可用的模型」（未验证）。

## 9. MiniMax（minimax）

**接口。**

- `GET /v1/models`，Bearer，返回 `{object:"list", data:[{id, object, created, owned_by}]}`（[List models](https://platform.minimaxi.com/docs/api-reference/models/openai/list-models.md)）。另有 `GET /v1/models/{model_id}`。
- `GET /anthropic/v1/models`，`X-Api-Key` 鉴权，支持 `limit`、`after_id`、`before_id` 分页，返回 `id`、`created_at`、`display_name`、`type`（[Anthropic list models](https://platform.minimax.io/docs/api-reference/models/anthropic/list-models.md)）。

两者都没有模态、上限、价格字段。文档示例只有文本模型，海螺视频、`image-01`、语音模型是否出现在列表中未查明。没有其他机器可读目录，[llms.txt](https://platform.minimaxi.com/docs/llms.txt) 只是文档索引。

**附带发现。**国内站文档的 OpenAPI `servers` 与 OpenAI 兼容快速开始已改用 `https://api.minimax.cn`，ArcReel 默认的 `https://api.minimaxi.com/v1`（`lib/backends/minimax_shared.py::MINIMAX_BASE_URL`）是否继续有效，没有找到官方说明。

## 10. 可灵（kling）

**没有模型列表接口。**官方文档索引（[llms.txt](https://klingai.com/document-api/llms.txt)）的全部接口中没有列模型的接口。不带凭证请求 `GET https://api-singapore.klingai.com/v1/models` 返回 404，而已存在的路由在无凭证时返回鉴权错误，说明该路由不存在。

**账户接口。**以下接口都不返回可用模型：

- `GET /account/costs`（无 `/v1` 前缀）返回资源包名称、类型、总量、剩余量和状态（[Account usage](https://kling.ai/document-api/api/assets/account-usage.md)）。
- `POST /account/billing/balance` 和 `POST /account/billing/package` 返回逐任务扣费记录，含 `model_name`、`resolution`、`duration`、`list_price`，只能看到用过的模型。

这些接口文档只写了 `Bearer <API_KEY>`，是否接受 AK/SK 生成的 JWT 未查明。

**可机读的替代来源。**[视频能力图](https://kling.ai/document-api/guides/capability-map/video.md)与[图像能力图](https://kling.ai/document-api/guides/capability-map/image.md)提供 Markdown 导出，内容是模型表加「模式 × 模型」支持矩阵，标注更新于 2026-05-19。Markdown 中用的是显示名（如「Kling 3.0 Omni」），不是 `model_name`。同一矩阵在文档站前端 JS 包中以结构化对象存在，带模型 id，但文件名含构建哈希，不是稳定契约。价格见 [video.md](https://kling.ai/document-api/pricing/base/video.md) 和 [image.md](https://kling.ai/document-api/pricing/base/image.md)。

**命名变化。**2026-06-17 起，新 API 把模型放进路径（如 `POST /text-to-video/kling-3.0`），且只支持 API Key 鉴权。旧的 `/v1/videos/*` 接口用 `model_name`（如 `kling-v3`），官方称「会持续提供服务，暂无下线计划」（[API 更新](https://klingai.com/document-api/updates/api.md)）。同一模型在两套接口中的标识不同。

## 11. Agnes（agnes）

**`GET /v1/models` 存在，但没有写进文档。**公开文档（[llms.txt](https://agnestech.mintlify.app/llms.txt)）只列出 chat、responses、messages、images、videos 等接口。不带凭证请求 `GET https://apihub.agnes-ai.com/v1/models` 返回 401「Token not provided」，说明路由存在。Agnes 官方的 [agnes-harness](https://github.com/AgnesAI-Labs/agnes-harness/blob/main/packages/ai/src/adapters/pi/providers/agnes-ai.ts) 源码注释写明该接口只返回 id、不提供模态，且列表中混有图像和视频模型 id，测试夹具把响应建模为 `{ data: [{ id }] }`。

**地域。**同一文件注释写明国际网关 `apihub.agnes-ai.com` 拒绝国内 key。模型可用性还取决于「account and API key permissions」（[MODEL_CATALOG.md](https://github.com/AgnesAI-Labs/AgnesAI-Models/blob/main/MODEL_CATALOG.md)）。

**官方目录仓库已过时。**[AgnesAI-Models](https://github.com/AgnesAI-Labs/AgnesAI-Models) 只有 Markdown 表格，没有 JSON 或 YAML。`MODEL_CATALOG.md`（目录版本 `2026.07.30`）仍列出 2026-09-25 已下线的 `agnes-video-v2.0`（[下线说明](https://agnes-ai.com/en/docs/agnes-video-v20)），缺少 `agnes-2.5-pro`、`agnes-3.0-flash`、`agnes-image-2.5-flash`。仓库 `docs/api-docs/providers/agnes.md` 以该仓库为总入口，需要更新。价格以 [pricing.md](https://wiki.agnes-ai.com/en/docs/pricing.md) 为准。

## 对 #3085 的建议

以下建议供 #3085 决策参考，不是已定结论。

1. **「获取模型」只做 id 级发现。**11 家中只有 dashscope 和 grok 的列表带足够的能力与价格信息，且 grok 视频模型没有价格。内置供应商的 backend 还依赖 `ModelInfo` 中的时长、分辨率、能力声明（ADR 0013），这些都不在任何列表接口中。因此发现结果应和模型目录合并：目录内的模型标出「本账号可用 / 不可用」，目录外的新 id 单独列出，能否启用由 #3085 决定。
2. **按供应商声明是否支持发现。**可在 `ProviderMeta` 上声明发现方式：openai、minimax、agnes 复用 openai 形态；gemini-aistudio 复用 google 形态；gemini-vertex 需要服务账号路径；dashscope 和 grok 需要新实现才能读出元信息。ark、ark-agent-plan、kling、vidu 声明为不支持。
3. **dashscope 与 grok 可作为目录同步的候选来源。**两者的价格字段机器可读，但单位各不相同（dashscope 用 `price_unit`，xAI 用 1e-8 美分等），且 xAI 的 `image_price` 单位有歧义，进入同步前需要逐字段核对。
4. **弃用信息只能部分自动获取。**openai `shutdown_date` 和 dashscope `offline_time` 可直接用；ark 的弃用状态要 AK/SK；其余几家只在网页上以文字公布。

## 风险与未查明项

| 项 | 影响 | 确认方式 |
|---|---|---|
| Gemini 列表是否包含 Veo，`supportedGenerationMethods` 的实际取值 | 决定能否用方法名区分视频模型 | 一次带凭证的 `models.list` |
| OpenAI `/v1/models` 是否包含图像、视频、TTS 模型，项目允许列表是否过滤 | 决定 openai 发现结果是否完整 | 一次带凭证调用 |
| dashscope 旧 host `dashscope.aliyuncs.com/api/v1/models` 是否可用，结果是否按 workspace 过滤 | ArcReel 默认 host 不在文档列表中 | 一次带凭证调用 |
| MiniMax `/v1/models` 是否包含非文本模型；`api.minimaxi.com` 与 `api.minimax.cn` 的关系 | 决定发现结果是否完整；默认 base_url 是否需要更新 | 带凭证调用；跟踪官方公告 |
| Agnes `/v1/models` 的完整响应结构，是否按 key 过滤 | 接口未写进文档，可能变动 | 一次带凭证调用 |
| 方舟 `/api/plan`、`/api/coding` 下 `/v1/models` 的行为，是否消耗套餐额度 | 只有代码注释中的实测记录 | 带凭证调用并查看用量 |
| xAI `image_price` 的真实单位 | 同步价格时可能差 1e8 倍 | 向 xAI 确认，或带凭证调用后与定价页比对 |
| 方舟 Agent Plan 的使用限制 | [支持模型页](https://www.volcengine.com/docs/82379/2366394)写明「文本生成模型及向量化模型不可用于 API 调用，在非 AI 工具中使用 Agent Plan 权益对应的 Base URL 和 API Key 有可能被识别为滥用/违规」。ark-agent-plan 内置供应商的文本模型是否受此影响需要另行评估 | 阅读套餐条款，必要时咨询火山引擎 |
| 可灵新旧接口的模型标识不同 | 目录同步时同一模型可能有两个标识 | 跟踪可灵 API 更新 |
| 仓库文档索引过时 | `docs/api-docs/providers/vidu.md` 的功能清单链接失效；`agnes.md` 指向过时目录 | 另开票更新索引 |
