# 各媒体类型的主流调用协议与中转站路径变体

- **调研日期**：2026-10-08。外部事实均为当日抓取的一手来源，协议和模型上下线变动频繁，落地前须按链接复核。
- **关联**：#3080（本票），地图 #3079「模型元信息与协议覆盖」。
- **用途**：为「协议补齐」与「路径覆盖」两个设计决策提供输入。本文只陈述事实和建议优先级，不定 backend 类设计、schema 或实施计划。
- **既有调研**：视频协议的四大流派、官方视频平台规格、状态机与 URL 过期见 [`arcreel-video-api-protocol-research.md`](arcreel-video-api-protocol-research.md)；阿里、可灵、MiniMax 的模型与定价见 [`arcreel-vendor-integration-research.md`](arcreel-vendor-integration-research.md)；TTS 选型见 [`arcreel-tts-narration-research.md`](arcreel-tts-narration-research.md)。本文不重复这些结论，只引用并补齐 2026-10 的变化。
- **标注**：未能在一手来源确认的条目标为「未查明」；由源码推断、未实测的条目标为「推断」。

## 1. 结论

1. **文本是缺口最大的媒体类型。**现有只有 `openai-chat` 和 `gemini-generate`。Anthropic Messages 已成为国内主流厂商、OpenRouter、new-api、LiteLLM 和 Vercel 的共同出口，OpenAI Responses 已是部分 OpenAI 模型的唯一入口，也是 xAI 的推荐接口。建议 P0 补 `anthropic-messages`，P1 补 `openai-responses`。
2. **图片与视频以「路径覆盖 + 声明式定义」为主，少补内置协议。**新出现的图片、视频协议（OpenRouter `/images` 与 `/videos`、xAI 视频、智谱、千帆、硅基流动视频）都是「JSON in/out + 提交/轮询」，落在 ADR 0067 的流派内。唯一值得新增的内置图片协议是「Chat Completions 出图」（`modalities` 含 `image`，P1）。
3. **音频只能靠内置协议补齐**，因为地图已定声明式不扩展到 audio。现有 `openai-tts` 覆盖 OpenAI、硅基流动、智谱 GLM-TTS、new-api、one-api 和 OpenRouter。建议 P1 补 `dashscope-tts`（backend 已存在，只差端点注册）与 `gemini-tts`；MiniMax `t2a_v2`、xAI `/v1/tts` 为 P2。
4. **路径覆盖必须按「端点」而不是按「供应商」配置。**同一家供应商的不同协议常挂在不同前缀下：OpenRouter 的 OpenAI 协议在 `/api/v1`、Anthropic 协议在 `/api`；智谱 Chat 在 `/api/paas/v4`、Responses 在 `/api/v1`；千帆 Chat 在 `/v2`、视频在根路径。单个 `base_url` 表达不了这些组合。
5. **SDK 决定了路径覆盖能做到哪一层。**openai-python、anthropic-sdk-python、google-genai 都保留 `base_url` 自带的路径前缀，所以「加前缀」和「改版本段」能通过 `base_url` 解决；「改路径名」需要绕过 SDK 资源方法（低层 `post`、自定义 httpx transport 或私有方法覆写）。xai-sdk 走 gRPC，只能换 host，无法经过任何 HTTP 中转站。
6. **两项上游变化需要先处理**：OpenAI Videos API 与 Sora 2 已于 2026-09-24 关停，但 `/v1/videos` 路径形态仍被 Gemini OpenAI 兼容层、new-api、LiteLLM 沿用，`openai-video` 端点应保留；Gemini API 的 Imagen 已关停，`infer_endpoint` 把 `imagen-*` 推到 `gemini-image`（走 `generateContent`）的规则已无意义。

## 2. 现状基线

### 2.1 内置调用端点与实际请求路径

下表从 `lib/custom_provider/endpoints.py`、`lib/custom_provider/builtin_endpoints/*.json` 与各 backend 整理，「路径如何确定」一列是路径覆盖设计的直接输入。

| 媒体 | 端点键 | 实现 | 实际请求 | 路径如何确定 |
|---|---|---|---|---|
| 文本 | `openai-chat` | openai-python `chat.completions.create` | `{base}/chat/completions` | `ensure_openai_base_url`：末尾不是 `/vN` 就补 `/v1` |
| 文本 | `gemini-generate` | google-genai `generate_content` | `{base}/v1beta/models/{m}:generateContent` | `ensure_google_base_url`：剥掉末尾 `/vN*`，SDK 再拼 `v1beta` |
| 图片 | `openai-images` / `-generations` / `-edits` | openai-python `images.generate` / `images.edit` | `{base}/images/generations`、`{base}/images/edits` | 同 `openai-chat` |
| 图片 | `gemini-image` | google-genai `generate_content` | 同 `gemini-generate` | 同 `gemini-generate` |
| 图片 | `dashscope-image` | httpx | `{host}/api/v1/services/aigc/multimodal-generation/generation` | `_dashscope_host` 剥掉 `/compatible-mode/v1` 或 `/api/v1` 后固定拼 `/api/v1` |
| 图片 | `minimax-image` | httpx | `{base}/image_generation` | `base_url` 原样 |
| 图片 | `kling-image` | httpx | `{base}/v1/images/generations` | `_ensure_url_path_suffix(base, "/v1")` |
| 视频 | `openai-video` | openai-python `videos.*` | `{base}/videos`、`/videos/{id}`、`/videos/{id}/content` | 同 `openai-chat` |
| 视频 | `ark-seedance` | Ark SDK | `{base}/contents/generations/tasks` | `_ensure_url_path_suffix(base, "/api/v3")` |
| 视频 | `vidu-video` | httpx | `{base}/img2video` 等 | `_ensure_url_path_suffix(base, "/ent/v2")` |
| 视频 | `dashscope-async-video` | httpx | `{host}/api/v1/services/aigc/video-generation/video-synthesis` | 同 `dashscope-image` |
| 视频 | `kling-video` | httpx | `{base}/v1/videos/{text2video,image2video,multi-image2video}` | 同 `kling-image` |
| 视频 | `newapi-video`、`v2-video-generations`、`minimax-*` | 随版声明式定义 | 定义里写完整 URL 模板，如 `{{ base_url }}/v1/video/generations` | 模板，路径完全可控 |
| 音频 | `openai-tts` | openai-python `audio.speech` | `{base}/audio/speech` | 同 `openai-chat` |

内置供应商另有三条不进 `ENDPOINT_REGISTRY` 的通道：

- Grok 文本、图片、视频经 `xai_sdk`（`lib/backends/grok_shared.py`），是 gRPC。
- 内置 DashScope 供应商的 Qwen-TTS 走 `lib/backends/audio_backends/dashscope.py`，自定义供应商挂不上它。
- 内嵌 Agent 走 Anthropic Messages（Claude CLI 按 `base_url + /v1/messages` 拼接，见 `lib/config/url_utils.py` 的 `normalize_anthropic_base_url`）。`lib/agent/agent_provider_catalog.py` 已收录智谱、DeepSeek、MiniMax、Kimi、火山方舟等 Anthropic 兼容地址。这条通道只服务 Agent，不参与媒体生成（ADR 0017），但模型发现已支持 `discovery_format = "anthropic"`（`lib/custom_provider/discovery.py`）。

### 2.2 现有 `base_url` 归一化能吸收的变体

- **「加前缀」大多已可用。**new-api 把可灵、豆包、阿里原生路径挂在 `/kling`、`/doubao`、`/ali` 下（见第 7 节），用户把 `base_url` 填成 `https://relay.example.com/kling` 时，`_ensure_url_path_suffix` 与 `_dashscope_host` 都会正确拼出 `/kling/v1/...`、`/doubao/api/v3/...`、`/ali/api/v1/...`（推断，按代码逻辑）。
- **「非 `/v1` 版本段」可用于 OpenAI 系。**`ensure_openai_base_url` 只要求末尾是 `/vN`，所以 `/api/v3`、`/api/paas/v4`、`/v2`、`/compatible-mode/v1` 都原样保留。
- **吸收不了的变体：**
  - 末尾不是 `/vN` 的 OpenAI 兼容地址会被强补 `/v1`，例如 DeepSeek 官方文档给的是根路径 `https://api.deepseek.com`（是否接受 `/v1` 后缀：未查明），OpenRouter 的图片接口是 `/api/v1/images` 而不是 `/images/generations`。
  - Gemini 系一律被改成 `v1beta`，中转站只开 `/v1/models/*` 时无法表达。
  - 轮询、取件路径与提交路径前缀不同的情况（第 8.4 节）。

## 3. 文本协议

### 3.1 官方协议

| 协议 | 方法与路径 | 鉴权 | 同步或异步 | 来源 |
|---|---|---|---|---|
| OpenAI Chat Completions | `POST /v1/chat/completions` | `Authorization: Bearer` | 同步，`stream` 时 SSE | [OpenAI](https://developers.openai.com/api/docs/deprecations)（无弃用条目） |
| OpenAI Responses | `POST /v1/responses`；`GET /v1/responses/{id}`；`POST /v1/responses/{id}/cancel` | Bearer | 同步、SSE，或 `background: true` 后轮询 | [background](https://developers.openai.com/api/docs/guides/background) |
| Anthropic Messages | `POST /v1/messages` | `x-api-key`（或 `Authorization: Bearer`）+ 必填 `anthropic-version` | 同步、SSE；批量走 `/v1/messages/batches` | [API overview](https://platform.claude.com/docs/en/api/overview) |
| Gemini `generateContent` | `POST /v1beta/models/{m}:generateContent`；流式 `:streamGenerateContent?alt=sse` | `x-goog-api-key` 或 `?key=` | 同步、SSE | [generate-content](https://ai.google.dev/api/generate-content) |
| Gemini Interactions | `POST /v1beta/interactions` | 同上 | 同步、流式 | [interactions](https://ai.google.dev/gemini-api/docs/interactions) |
| xAI Responses / Chat | `POST /v1/responses`；`POST /v1/chat/completions` | Bearer | 同步、SSE；Chat 另有延迟结果 `GET /v1/chat/deferred-completion/{id}` | [xAI responses](https://docs.x.ai/developers/rest-api-reference/inference/responses.md)、[chat](https://docs.x.ai/developers/rest-api-reference/inference/chat-completions.md) |
| xAI gRPC | `/{package.Service}/{Method}`，如 `xai_api.Chat` | metadata 中的 key | 一元或流式 RPC | [gRPC ref](https://docs.x.ai/developers/grpc-api-reference.md) |

需要注意的状态变化：

- **OpenAI 部分模型只能走 Responses。**`o3-pro`、`gpt-5.5-pro` 仅支持 Responses，`gpt-5.3-codex` 不支持 Chat Completions；GPT-5.6 的 `reasoning.mode: "pro"` 文档只给出 Responses 用法（Chat 是否支持：未查明）。旗舰 `gpt-6-astra` 两者都支持。来源：[o3-pro](https://developers.openai.com/api/docs/models/o3-pro)、[gpt-5.5-pro](https://developers.openai.com/api/docs/models/gpt-5.5-pro)、[gpt-5.3-codex](https://developers.openai.com/api/docs/models/gpt-5.3-codex)、[reasoning](https://developers.openai.com/api/docs/guides/reasoning)、[gpt-6-astra](https://developers.openai.com/api/docs/models/gpt-6-astra)。
- **xAI 把 Chat Completions 标为 legacy**，新功能先上 Responses；Anthropic 兼容的 `/v1/messages` 已「fully deprecated」。来源：[legacy guide](https://docs.x.ai/developers/model-capabilities/legacy/chat-completions.md)、[legacy](https://docs.x.ai/developers/rest-api-reference/inference/legacy.md)。
- **Gemini 推荐新项目改用 Interactions API**（2026-06 起 GA），`generateContent`「remains fully supported」，但「all new models, multimodal capabilities, tools … will launch on the Interactions API」。来源：[interactions](https://ai.google.dev/gemini-api/docs/interactions)。
- **Anthropic 官方的 OpenAI SDK 兼容层**（`base_url = https://api.anthropic.com/v1/`）只覆盖 Chat Completions，官方说明它「primarily intended to test and compare」、不适合生产；`strict` 与 `response_format` 被忽略，`n` 只能为 1，不返回 thinking 内容。也就是说，用 `openai-chat` 直连 Anthropic 官方做结构化输出是不可靠的。来源：[openai-sdk](https://platform.claude.com/docs/en/api/openai-sdk)。

### 3.2 国内厂商的文本出口

国内主流厂商同时提供 OpenAI 兼容和 Anthropic 兼容两个出口，差别在路径前缀。

| 厂商 | OpenAI 兼容 `base_url` | Anthropic 兼容 `base_url`（SDK 再拼 `/v1/messages`） | Responses |
|---|---|---|---|
| 阿里百炼 | `https://dashscope.aliyuncs.com/compatible-mode/v1`；工作空间域名 `https://{WorkspaceId}.{region}.maas.aliyuncs.com/compatible-mode/v1` | `https://{WorkspaceId}.{region}.maas.aliyuncs.com/apps/anthropic` | `{compat base}/responses` |
| 火山方舟 | `https://ark.cn-beijing.volces.com/api/v3` | `https://ark.cn-beijing.volces.com/api/compatible`；Coding Plan 为 `/api/coding` | `/api/v3/responses` |
| 智谱 | `https://open.bigmodel.cn/api/paas/v4` | `https://open.bigmodel.cn/api/anthropic` | `https://open.bigmodel.cn/api/v1/responses`（与 Chat 不同前缀） |
| Moonshot Kimi | `https://api.moonshot.cn/v1` | `https://api.moonshot.cn/anthropic` | `/v1/responses` |
| DeepSeek | `https://api.deepseek.com` | `https://api.deepseek.com/anthropic` | 文档未提及 |
| MiniMax | `https://api.minimax.cn/v1`（国际 `https://api.minimax.io/v1`） | `https://api.minimax.cn/anthropic` | `/v1/responses` |
| 百度千帆 | `https://qianfan.baidubce.com/v2` | 未查明 | `/v2/responses` |
| 腾讯 TokenHub（混元后继） | `https://tokenhub.tencentcloudmaas.com/v1` | 与 OpenAI 同 host、同 `/v1`，路径 `/v1/messages` | `/v1/responses`，按模型 |
| 硅基流动 | `https://api.siliconflow.cn/v1` | 与 OpenAI 同 `/v1`，路径 `/v1/messages`，SDK `base_url` 填裸 host | 文档未列出 |

来源：[百炼 OpenAI 兼容](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)、[百炼 Anthropic](https://help.aliyun.com/zh/model-studio/anthropic-api-messages)、[百炼 Responses](https://help.aliyun.com/zh/model-studio/compatibility-with-openai-responses-api)、[方舟 Base URL](https://www.volcengine.com/docs/82379/1298459)、[方舟 Messages](https://www.volcengine.com/docs/ark/messages-api?lang=zh)、[智谱 API](https://docs.bigmodel.cn/cn/api/introduction)、[智谱 Claude 兼容](https://docs.bigmodel.cn/cn/guide/develop/claude/introduction)、[智谱 Responses](https://docs.bigmodel.cn/cn/guide/develop/responses/introduction.md)、[Kimi Messages](https://platform.kimi.com/docs/api/messages)、[Kimi Responses](https://platform.kimi.com/docs/api/responses)、[DeepSeek Anthropic](https://api-docs.deepseek.com/guides/anthropic_api)、[MiniMax OpenAI](https://platform.minimax.cn/docs/api-reference/text-openai-api)、[MiniMax Anthropic](https://platform.minimax.cn/docs/api-reference/text-anthropic-api)、[MiniMax Responses](https://platform.minimax.cn/docs/api-reference/responses-create.md)、[千帆 Chat](https://cloud.baidu.com/doc/qianfan-api/s/3m7of64lb)、[千帆 Responses](https://cloud.baidu.com/doc/qianfan-api/s/vmhejnuy8)、[混元 Anthropic](https://cloud.tencent.com/document/product/1729/127293)、[TokenHub](https://intl.cloud.tencent.com/document/product/1300/80632)、[硅基流动 Messages](https://docs.siliconflow.cn/cn/docs/api/messages-post)。

补充事实：

- Anthropic 路径的鉴权头不统一：方舟、混元、TokenHub 只认 `x-api-key`；百炼两种都认；Kimi、MiniMax、硅基流动的文档示例用 Bearer（是否也认 `x-api-key`：未查明）。ArcReel 的 Anthropic 模型发现在 401 时回退 Bearer 重试，正是为方舟这类网关写的（`lib/custom_provider/discovery.py`）。
- 百炼的 Anthropic 出口不提供 `/v1/models`，模型名要手填；API Key 绑定地域，跨地域调用返回 401。
- MiniMax 国内文档的 OpenAPI 服务器已是 `api.minimax.cn`；ArcReel 代码与 Agent 预设仍用 `api.minimaxi.com`（`lib/backends/minimax_shared.py`、`lib/agent/agent_provider_catalog.py`），旧域名是否继续可用：未查明。

### 3.3 文本缺口

| 缺口 | 使用面 | 实现成本 | 备注 |
|---|---|---|---|
| `anthropic-messages`（媒体生成侧的文本） | 高：Anthropic 官方的唯一生产协议；国内 7 家以上厂商、OpenRouter、new-api、LiteLLM、Vercel 都提供 | 中：请求与响应形态不同于 Chat；结构化输出要走 tool use 或 `output_format`；需新依赖 anthropic-sdk-python 或直接 httpx | Agent 侧已有 URL 归一化、预设目录和模型发现，可复用 |
| `openai-responses` | 中高：OpenAI Responses 独占模型；xAI 推荐接口；国内 6 家提供 | 中：openai-python `responses.create` 同一套 `base_url` 机制；结构化输出改用 `text.format` | 中转站支持面比 Chat 窄（one-api 不支持，new-api 支持并能与 Chat 互转） |
| Gemini Interactions | 低（当前）：`generateContent` 仍完全支持 | 中：新的请求响应形态（`steps[]`） | 观察项。新模型若只在 Interactions 上线才需跟进 |
| xAI gRPC 之外的文本 | 已覆盖：自定义供应商可用 `openai-chat` 指向 `https://api.x.ai/v1` | — | 内置 Grok 文本仍走 gRPC，中转站不可用（第 9 节） |

## 4. 图片协议

### 4.1 官方协议

| 协议 | 方法与路径 | 响应 | 同步或异步 | 来源 |
|---|---|---|---|---|
| OpenAI Images | `POST /v1/images/generations`；`POST /v1/images/edits`（multipart） | `data[0].b64_json` | 同步，可流式返回中间图 | [image guide](https://developers.openai.com/api/docs/guides/image-generation) |
| OpenAI Responses `image_generation` 工具 | `POST /v1/responses`，主模型为文本模型 | `image_generation_call.result`（base64） | 同步、SSE | 同上 |
| Gemini 原生出图 | `generateContent` + `generationConfig.imageConfig`；或 Interactions 的 `response_format: {type: "image"}` | 内联 base64 | 同步 | [image-generation](https://ai.google.dev/gemini-api/docs/image-generation) |
| Imagen | `POST /v1beta/models/{m}:predict` | — | — | **已在 Gemini API 关停**，见 [imagen](https://ai.google.dev/gemini-api/docs/imagen) |
| xAI Images | `POST /v1/images/generations`、`/v1/images/edits`；可选 `deferred: true` 后轮询 `GET /v1/images/{request_id}` | `url` 或 `b64_json` | 同步或延迟 | [xAI images](https://docs.x.ai/developers/rest-api-reference/inference/images.md) |
| 火山方舟 Seedream | `POST /api/v3/images/generations` | OpenAI 风格 | 同步 | [Ark SDK images.py](https://github.com/volcengine/volcengine-python-sdk/blob/master/volcenginesdkarkruntime/resources/images/images.py) |
| 智谱 | `POST /api/paas/v4/images/generations`；GLM-Image 另有异步 `POST /api/paas/v4/async/images/generations` | OpenAI 风格 | 同步或异步 | [智谱图像](https://docs.bigmodel.cn/api-reference/模型-api/图像生成.md) |
| 千帆 | `POST https://qianfan.baidubce.com/v2/images/generations` | OpenAI 风格 | 同步 | [千帆图像](https://cloud.baidu.com/doc/qianfan-api/s/8m7u6un8a) |
| 硅基流动 | `POST /v1/images/generations` | OpenAI 风格 | 同步 | [硅基流动图像](https://docs.siliconflow.cn/cn/docs/api/images-generations-post) |
| 百炼、MiniMax、可灵 | 原生，已有内置端点 | — | — | 见既有调研 |

状态变化：

- OpenAI 图片模型换代：`dall-e-2/3` 已于 2026-05-12 关停；`gpt-image-1` 于 2026-10-23 关停；`gpt-image-1-mini`、`gpt-image-1.5`、`chatgpt-image-latest` 于 2026-12-01 关停；替代为 `gpt-image-2.5-sunburst` 或 `gpt-image-2.5-flare`。路径不变。来源：[deprecations](https://developers.openai.com/api/docs/deprecations)。
- Gemini 当前出图模型为 `gemini-3.1-flash-image`、`gemini-3-pro-image` 等，`gemini-2.5-flash-image` 标为 legacy。图片指南只给 Interactions 的 REST 示例；`generateContent` 的 `imageConfig` 仍在参考文档中，其响应形态在现行文档里未再示例（未查明）。来源：[image-generation](https://ai.google.dev/gemini-api/docs/image-generation)、[models](https://ai.google.dev/gemini-api/docs/models)。

### 4.2 聚合平台的图片形态

- **Chat Completions 出图**：OpenRouter 在 Chat Completions 上接受 `modalities: ["text", "image"]`，图片在 `choices[].message.images` 中返回（[openapi.json](https://openrouter.ai/openapi.json)）；Vertex AI 的 OpenAI 兼容端点示例也出现了 `modalities: ["image", "text"]`（[Vertex OpenAI](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/start/openai)，支持范围未查明）。中转站用这种形态承载 Nano Banana 类模型很常见（普及度为观察结论，未量化）。
- **OpenRouter 专用图片接口**：`POST /api/v1/images`，不是 `/images/generations`；请求体为 `model`、`prompt`、`aspect_ratio`、`resolution`、`input_references`，响应 `data[].b64_json`。
- **Gemini OpenAI 兼容层**：`/v1beta/openai/images/generations`，只认 `prompt`、`model`、`n`、`size`、`response_format`，其余参数静默忽略，比例要放进 `extra_body`（[openai compat](https://ai.google.dev/gemini-api/docs/openai)）。
- **new-api**：`/v1/images/generations` 与 `/v1/images/edits` 由宿主协议承载，豆包和阿里插件另有 `/doubao/api/v3/images/generations` 等原生路由（第 7 节）。

### 4.3 图片缺口

| 缺口 | 使用面 | 实现成本 | 建议 |
|---|---|---|---|
| Chat Completions 出图（`modalities` 含 `image`） | 中：OpenRouter、Vertex OpenAI 兼容、承载 Gemini 出图的中转站 | 中低：复用 openai-python `chat.completions`；参考图走 `image_url` 消息段；从 `message.images` 取 data URI | P1，新增内置端点 |
| OpenRouter `/images` | 中 | 低：落在声明式图片定义能力内（JSON 进、base64 出） | 声明式，可作随版定义 |
| xAI、智谱、千帆、硅基流动、方舟 Seedream | 中 | 零或低：多数是 OpenAI 图片形态，`openai-images-generations` 加正确的 `base_url` 即可；差异字段走声明式 | 无需新内置端点 |
| Responses `image_generation` 工具 | 低：中转站支持面窄 | 中 | P3 |
| Imagen `:predict` | 无：已关停 | — | 不做；删去 `infer_endpoint` 的 `imagen` 规则或改为提示 |

## 5. 视频协议

四大流派与官方平台规格沿用既有调研第 2–5 节。2026-10 的增量如下。

### 5.1 官方协议的变化

- **OpenAI Videos API 与 Sora 2 已于 2026-09-24 关停**，`sora-2`、`sora-2-pro` 及各日期快照同日下线，官方未给替代（「No one-to-one replacement API is available」）。来源：[deprecations](https://developers.openai.com/api/docs/deprecations)、[video guide](https://developers.openai.com/api/docs/guides/video-generation)。既有调研第 8.1 节预告的风险已成为事实。内置 `openai` 供应商在 `lib/config/registry.py` 中仍登记 `sora-2` 与 `sora-2-pro`。
- **`/v1/videos` 路径形态仍在被沿用**，所以 `openai-video` 端点应保留：
  - Gemini OpenAI 兼容层提供 `POST /v1beta/openai/videos` 与 `GET /v1beta/openai/videos/{id}`，承载 Veo，文档自称「Sora-compatible」，但状态值是 `processing` / `completed` / `failed`（[openai compat](https://ai.google.dev/gemini-api/docs/openai)）。`openai-video` 不复用 SDK 的 `videos.poll`，能容忍非标状态（`lib/backends/video_backends/openai.py`）。
  - new-api 把所有视频插件同时暴露在 `/v1/videos`（宿主协议）上；LiteLLM 也有 `/v1/videos` 并转发到 OpenAI、Azure、Gemini、Vertex、Runway（[LiteLLM videos](https://docs.litellm.ai/docs/videos)）。
- **Veo**：当前模型为 `veo-3.1-generate-preview`、`veo-3.1-fast-generate-preview`、`veo-3.1-lite-generate-preview`，`veo-3.0-*` 已弃用；另有视频页推荐的 `gemini-omni-1.1-flash`。原生协议仍是 `POST /v1beta/models/{m}:predictLongRunning` → 轮询 `GET /v1beta/{operation name}`，生成结果保留 2 天。Vertex 上轮询改为 `POST …:fetchPredictOperation`。来源：[veo](https://ai.google.dev/gemini-api/docs/veo)、[video](https://ai.google.dev/gemini-api/docs/video)、[fetchPredictOperation](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/reference/rest/v1/projects.locations.publishers.models/fetchPredictOperation)。
- **xAI**：`POST /v1/videos/generations`（另有 `/edits`、`/extensions`）返回 `request_id`，轮询 `GET /v1/videos/{request_id}`，状态 `pending` / `done` / `failed` / `expired`。注意它不是 v2 通用 generations 的 `?generation_id=` 形态。来源：[xAI videos](https://docs.x.ai/developers/rest-api-reference/inference/videos.md)。
- **MiniMax v2**：`POST /v2/video_generation` + `GET /v2/query/video_generation/{task_id}`，已由随版声明式 `minimax-h3` 覆盖。

### 5.2 新出现的提交/轮询协议

| 平台 | 提交 | 轮询 | 备注 |
|---|---|---|---|
| OpenRouter | `POST /api/v1/videos`，返回 202 与 `polling_url` | `GET /api/v1/videos/{jobId}`；取件 `/content` | 路径同 OpenAI，请求体与状态值不同（`aspect_ratio`、`frame_images`、`pending`、`unsigned_urls[]`），来源 [openapi.json](https://openrouter.ai/openapi.json) |
| 智谱 | `POST /api/paas/v4/videos/generations` | `GET /api/paas/v4/async-result/{id}` | [智谱视频](https://docs.bigmodel.cn/api-reference/模型-api/视频生成异步.md) |
| 千帆 | `POST https://qianfan.baidubce.com/video/generations`（无 `/v2`） | `GET /video/generations?task_id=` | [千帆视频](https://cloud.baidu.com/doc/qianfan-api/s/Amg0lgcx4) |
| 硅基流动 | `POST /v1/video/submit` | `POST /v1/video/status` | 轮询是 POST，[submit](https://docs.siliconflow.cn/cn/docs/api/video-submit-post)、[status](https://docs.siliconflow.cn/cn/docs/api/video-status-post) |
| new-api 通用任务 | `POST /v1/tasks/:key` | `GET /v1/tasks/:key`、`/artifacts` | v1.0.0-rc.41 新增，[task-router.go](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.41/router/task-router.go) |
| Azure OpenAI 预览 REST | `POST /openai/v1/video/generations/jobs?api-version=preview` | `/jobs/{id}`；取件 `/video/generations/{gen_id}/content/video` | [Azure video](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/video-generation-quickstart) |

### 5.3 视频缺口

视频没有必须新增的内置协议。上表全部是「JSON in/out + 提交/轮询」，在 ADR 0067 的流派之内，由声明式定义承接；OpenRouter、xAI 这类普及度较高的可做成随版声明式定义。需要确认的是声明式定义能否表达「轮询用 POST」（硅基流动）和「任务 id 本身含 `/`」（Veo 的 operation name），这两点本文未核对声明式校验器。

自定义供应商目前不能用 Veo 原生 `predictLongRunning`（没有 `gemini-video` 端点）。实际影响有限：Gemini OpenAI 兼容层与 new-api、LiteLLM 都以 `/v1/videos` 形态提供 Veo，`openai-video` 可以接；只有走 LiteLLM `/gemini/v1beta/...` 透传或官方直连时才需要原生协议。建议 P2。

## 6. 音频协议

TTS 选型与时长错位见既有 TTS 调研，本节只补协议面。

| 协议 | 方法与路径 | 鉴权 | 同步或异步 | 来源 |
|---|---|---|---|---|
| OpenAI Speech | `POST /v1/audio/speech`，JSON 进、音频字节出 | Bearer | 同步，可分块流式 | [TTS guide](https://developers.openai.com/api/docs/guides/text-to-speech) |
| Gemini TTS | `generateContent`（`speechConfig`、`Modality.AUDIO`）或 Interactions 的 `response_format: {type: "audio"}`；默认 24 kHz 单声道 | `x-goog-api-key` | 同步；Live API 为 WebSocket | [speech-generation](https://ai.google.dev/gemini-api/docs/speech-generation) |
| xAI TTS | `POST /v1/tts`，`{text, voice_id, language, output_format}`；流式 `wss://api.x.ai/v1/tts` | Bearer | 同步或流式 | [xAI voice](https://docs.x.ai/developers/rest-api-reference/inference/voice.md) |
| 百炼 Qwen-TTS | `POST /api/v1/services/aigc/multimodal-generation/generation`；`X-DashScope-SSE: enable` 时流式 | Bearer | 同步 | [Qwen-TTS](https://help.aliyun.com/zh/model-studio/qwen-tts-api) |
| 豆包语音 | `POST https://openspeech.bytedance.com/api/v3/tts/unidirectional` | `X-Api-Key` + `X-Api-Resource-Id`（新控制台） | 同步或 SSE | [豆包 TTS](https://www.volcengine.com/docs/6561/1598757) |
| MiniMax | 同步 `POST /v1/t2a_v2`；异步 `POST /v1/t2a_async_v2` + `GET /v1/query/t2a_async_query_v2` | Bearer | 同步或异步 | [t2a](https://platform.minimax.cn/docs/api-reference/speech-t2a-http.md) |
| 智谱 GLM-TTS | `POST /api/paas/v4/audio/speech` | Bearer | 同步 | [智谱 TTS](https://docs.bigmodel.cn/api-reference/模型-api/文本转语音.md) |
| 硅基流动 | `POST /v1/audio/speech` | Bearer | 同步 | [硅基流动 speech](https://docs.siliconflow.cn/cn/docs/api/audio-speech-post) |
| 千帆 | 不在千帆 host：`POST https://tsn.baidu.com/text2audio` 等 | 百度语音鉴权 | 同步 | [千帆 TTS](https://cloud.baidu.com/doc/qianfan-api/s/5m7stbv04) |

状态变化与观察：

- OpenAI `tts-1`、`tts-1-hd` 与带日期的 `gpt-4o-mini-tts` 快照将于 2027-01-06 关停，弃用页给出的替代是 `gpt-realtime-2.1-mini`（[deprecations](https://developers.openai.com/api/docs/deprecations)）。TTS 指南仍只列出 `gpt-4o-mini-tts`、`tts-1`、`tts-1-hd` 三个 `/v1/audio/speech` 模型；替代模型能否用于 `/v1/audio/speech`、不带日期的 `gpt-4o-mini-tts` 别名是否随快照下线：未查明。若替代只能走 Realtime，OpenAI 官方的 `/v1/audio/speech` 会失去可用模型，但该路径仍是硅基流动、智谱、new-api、one-api、OpenRouter 的共同形态。
- 中转站普遍只转发 `/v1/audio/speech`：new-api、one-api 都有这条路由，OpenRouter 也提供 `/audio/speech`（第 7 节）。Gemini TTS 能经 new-api 的 `/v1beta/models/*` 透传，这是 `gemini-tts` 在自定义供应商侧的主要价值。

### 6.1 音频缺口

| 缺口 | 使用面 | 实现成本 | 建议 |
|---|---|---|---|
| `dashscope-tts`（自定义供应商挂接百炼 Qwen-TTS） | 中：百炼用户与转发百炼原生路径的中转站（new-api `/ali` 前缀） | 低：`lib/backends/audio_backends/dashscope.py` 已存在，只差 `EndpointSpec` 注册与推断规则 | P1 |
| `gemini-tts` | 中：Gemini 官方与透传 `/v1beta` 的中转站 | 中：同步、PCM 需封装成 WAV；可复用 google-genai | P1 |
| `minimax-tts`（`t2a_v2` 同步） | 中低 | 中 | P2 |
| `xai-tts`（`/v1/tts`） | 低 | 低中 | P2 |
| 豆包语音 | 中（中文质量好） | 高：多字段鉴权，与 ADR 0008 的单字段凭证冲突 | P3，只能做内置供应商 |

## 7. 聚合平台与中转站实际暴露的协议

### 7.1 普及度指标

| 项目 | GitHub stars（2026-10-08） | 状态 |
|---|---|---|
| new-api（QuantumNous/new-api） | 49,398 | 活跃，v1.0.0-rc.41 于 2026-09-30 发布 |
| LiteLLM（BerriAI/litellm） | 60,340 | 活跃 |
| one-api（songquanpeng/one-api） | 37,089 | 最后发布 v0.6.10（2025-02-02），实际停更 |
| one-hub / Veloera / VoAPI / done-hub | 2,897 / 1,633 / 1,089 / 809 | 分支，路由集未查明 |

stars 只反映关注度，不等于部署量。

### 7.2 new-api（v1.0.0-rc.41）

- **静态路由**（[relay-router.go](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.41/router/relay-router.go)）：`/v1/chat/completions`、`/v1/completions`、`/v1/messages`、`/v1/embeddings`、`/v1/audio/{speech,transcriptions,translations}`、`/v1/rerank`、`/v1/moderations`；Gemini 的 `/v1/models/*` 与 `/v1beta/models/*` 互为别名；Responses 与 Realtime 的 WebSocket；Midjourney `/mj/...` 与带模式前缀的 `/:mode/mj/...`。
- **宿主协议路由**（[routing.go](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.41/pkg/jsplugin/routing.go)）：`/v1/responses`、`/v1/videos`（含 `/content`）、`/v1/images/generations`、`/v1/images/edits`。
- **统一视频路由**（[video-router.go](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.41/router/video-router.go)）：`/v1/video/generations` 与 `/v1/video/generations/:task_id`，状态统一为 `queued|in_progress|completed|failed`（[文档](https://docs.newapi.pro/en/docs/api/ai-model/videos/getvideogeneration)）。
- **厂商原生路由**：rc.41 把视频、图片等改为 JS 任务插件，原生路径前加厂商前缀，见第 8.1 节。
- **协议互转**：Claude Messages、Gemini、OpenAI Chat、Responses 两两互转（`relaykit/relayconvert/internal/`），所以挂在 new-api 后面的任一文本模型都能用这几种协议调用。
- **入站鉴权归一**：`/v1/messages*` 接受 `x-api-key`，`/v1beta/models*` 接受 `x-goog-api-key` 或 `?key=`，统一换成 Bearer（[auth.go](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.41/middleware/auth.go)）。
- **「高级自定义」渠道**是 ArcReel 路径覆盖的现成参照：每条路由配置 `incoming_path` → `upstream_path`（相对 base 或完整 URL，支持 `{model}` 模板）、协议转换器、模型过滤与鉴权模板 `{header|query|none, name, value: "Bearer {api_key}"}`（[channel_settings.go](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.41/relaykit/dto/channel_settings.go)、[advancedcustom/adaptor.go](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.41/relay/channel/advancedcustom/adaptor.go)）。

### 7.3 one-api

全部路由在 `/v1` 下：`/chat/completions`、`/completions`、`/embeddings`、`/images/generations`（无 `/images/edits`）、`/audio/{speech,transcriptions,translations}`、`/moderations`，外加原样透传 `/v1/oneapi/proxy/:channelid/*target`。没有 `/v1/messages`、`/v1beta`、`/v1/responses` 和任何视频或任务路由（[relay.go](https://github.com/songquanpeng/one-api/blob/8df4a2670b98266bd287c698243fff327d9748cf/router/relay.go)）。这意味着部署 one-api 的中转站只能接 `openai-chat`、`openai-images-generations` 和 `openai-tts`。

### 7.4 OpenRouter

服务器 `https://openrouter.ai/api/v1`，只认 Bearer（[openapi.json](https://openrouter.ai/openapi.json)）：

- 文本：`/chat/completions`、`/responses`、`/messages`、`/embeddings`、`/rerank`。
- 图片：`POST /images`（专用接口）与 Chat Completions 的 `modalities` 出图。
- 视频：`POST /videos` + `GET /videos/{jobId}`。
- 音频：`/audio/speech`、`/audio/transcriptions`。
- Claude Code 接入时 `ANTHROPIC_BASE_URL=https://openrouter.ai/api`，即 Anthropic 协议的 base 比 OpenAI 协议少一段 `/v1`（[Claude Code 指南](https://openrouter.ai/docs/guides/guides/claude-code-integration)）。

### 7.5 其他网关

- **LiteLLM**：同一路由注册四种形态 `/v1/chat/completions`、`/chat/completions`、`/engines/{model}/chat/completions`、`/openai/deployments/{model}/chat/completions`（[proxy_server.py](https://github.com/BerriAI/litellm/blob/v1.104.1/litellm/proxy/proxy_server.py)）；`/v1/messages` 跨供应商统一；透传路径 `/anthropic/v1/messages`、`/gemini/v1beta/models/{m}:generateContent`（[anthropic 透传](https://docs.litellm.ai/docs/pass_through/anthropic_completion)、[gemini 透传](https://docs.litellm.ai/docs/pass_through/google_ai_studio)）。
- **Cloudflare AI Gateway**：`https://gateway.ai.cloudflare.com/v1/{account_id}/{gateway_id}/{provider}/` 后接原生路径，例如 `.../anthropic/v1/messages`、`.../google-ai-studio/v1/models/{m}:generateContent`；统一端点 `/compat/chat/completions` 已不推荐用于单模型（[anthropic](https://developers.cloudflare.com/ai-gateway/usage/providers/anthropic/)、[google-ai-studio](https://developers.cloudflare.com/ai-gateway/usage/providers/google-ai-studio/)、[chat-completion](https://developers.cloudflare.com/ai-gateway/usage/chat-completion/)）。
- **Azure OpenAI**：经典路径 `/openai/deployments/{deployment}/chat/completions?api-version=...`，v1 GA 路径 `/openai/v1/`（无 `api-version`，部署名放 `model`）（[reference](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/reference)、[api-version-lifecycle](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/api-version-lifecycle)）。
- **Vercel AI Gateway**：OpenAI 兼容 base `https://ai-gateway.vercel.sh/v1`；Anthropic 兼容 base 为不带 `/v1` 的根地址（[openai-compat](https://vercel.com/docs/ai-gateway/openai-compat)、[anthropic](https://vercel.com/docs/ai-gateway/sdks-and-apis/anthropic-messages-api)）。
- **硅基流动**：OpenAI 形态覆盖四种媒体（`/v1/chat/completions`、`/v1/images/generations`、`/v1/video/submit`、`/v1/audio/speech`），另有 `/v1/messages`。

### 7.6 各协议的中转覆盖面

| 协议 | new-api | one-api | OpenRouter | LiteLLM | 国内厂商直连 |
|---|---|---|---|---|---|
| OpenAI Chat | 有 | 有 | 有 | 有 | 全部 |
| OpenAI Responses | 有 | 无 | 有 | 未查明 | 百炼、方舟、智谱、Kimi、MiniMax、千帆、TokenHub |
| Anthropic Messages | 有 | 无 | 有 | 有 | 百炼、方舟、智谱、Kimi、DeepSeek、MiniMax、混元、TokenHub、硅基流动 |
| Gemini `generateContent` | 有（`/v1` 与 `/v1beta`） | 无 | 无 | 透传 | — |
| OpenAI Images | 有 | 仅 generations | 用 `/images` 替代 | 未查明 | 方舟、智谱、千帆、硅基流动 |
| OpenAI `/v1/videos` 形态 | 有 | 无 | 同路径不同 schema | 有 | — |
| OpenAI `/v1/audio/speech` | 有 | 有 | 有 | 未查明 | 智谱、硅基流动 |

## 8. 中转站对官方路径的改动目录

以下示例是路径覆盖机制的输入，按改动类型归类。

### 8.1 加前缀

| 官方路径 | 中转后 | 来源 |
|---|---|---|
| 可灵 `/v1/videos/text2video` | new-api `/kling/v1/videos/text2video` | [kling plugin.js](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.41/plugins/tasks/kling/plugin.js) |
| 方舟 `/api/v3/contents/generations/tasks` | new-api `/doubao/api/v3/contents/generations/tasks` | [doubao plugin.js](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.41/plugins/tasks/doubao/plugin.js) |
| 百炼 `/api/v1/services/aigc/video-generation/video-synthesis` | new-api `/ali/api/v1/...`，轮询 `/ali/api/v1/tasks/:task_id` | [alibaba plugin.js](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.41/plugins/tasks/alibaba/plugin.js) |
| Anthropic `/v1/messages` | LiteLLM `/anthropic/v1/messages`；Cloudflare `/v1/{acct}/{gw}/anthropic/v1/messages`；Moonshot、DeepSeek、MiniMax `/anthropic/v1/messages`；智谱 `/api/anthropic/v1/messages`；百炼 `/apps/anthropic/v1/messages` | 见第 3.2、7.5 节 |
| Gemini `/v1beta/models/{m}:generateContent` | LiteLLM `/gemini/v1beta/...`；Cloudflare `/v1/{acct}/{gw}/google-ai-studio/v1/models/...`；Gemini 官方 OpenAI 兼容层 `/v1beta/openai/...` | 见第 7.5 节 |
| Midjourney `/mj/submit/imagine` | new-api `/:mode/mj/submit/imagine`（前缀在厂商路径之前） | relay-router.go |

### 8.2 改版本段

- OpenAI 兼容出口的版本段各不相同：百炼 `/compatible-mode/v1`、方舟 `/api/v3`、智谱 `/api/paas/v4`、千帆 `/v2`、DeepSeek 无版本段。
- Gemini：new-api 把 `/v1/models/*` 与 `/v1beta/models/*` 视为同一协议；其 Veo 插件提交版本可配（默认 `v1beta`），轮询却写死 `/v1beta/`。
- Azure 经典 `/openai/deployments/{d}/...?api-version=` 与 v1 `/openai/v1/...` 并存。
- 同一厂商、同一 host 下不同协议的版本前缀不同：智谱 Chat `/api/paas/v4` 与 Responses `/api/v1`；千帆 Chat `/v2` 与视频（无版本段）；OpenRouter、Vercel 的 Anthropic base 比 OpenAI base 少 `/v1`；MiniMax 视频 `/v1/video_generation` 与 `/v2/video_generation` 按模型区分。

### 8.3 改路径名

- OpenRouter：图片 `POST /api/v1/images`，不是 `/images/generations`。
- LiteLLM、Azure：模型名进入路径，`/engines/{model}/...`、`/openai/deployments/{model}/chat/completions`。
- Cloudflare：统一端点 `/compat/chat/completions`。
- new-api：统一视频 `/v1/video/generations` 与 OpenAI 形态 `/v1/videos` 并存。
- 硅基流动视频：`/v1/video/submit` 与 `/v1/video/status`。

### 8.4 异步任务路径换成自家格式

| 官方 | 中转后 | 说明 |
|---|---|---|
| Veo `GET /v1beta/{operations/...}` | new-api `GET /v1/video/generations/{task_id}` 或 `/v1/videos/{id}` | new-api 把 operation name 做 base64url 编码成 task id，响应体也换成自家或 OpenAI 形态 |
| 可灵、Vidu 原生轮询 | new-api `/v1/videos/{id}` 返回 OpenAI 视频形态 | 路径与响应同时改写 |
| OpenAI `GET /v1/videos/{id}`（`queued`） | OpenRouter `GET /api/v1/videos/{jobId}`（`pending`，另给 `polling_url`） | 同路径不同 schema |
| — | AIMLAPI `GET /v2/video/generations?generation_id=` | 任务 id 在 query 中（[AIMLAPI](https://docs.aimlapi.com/api-references/video-models/kling-ai/v2.1-master-text-to-video)） |
| — | fal `GET queue.fal.run/{model}/requests/{id}/status` | [fal queue](https://fal.ai/docs/model-apis/model-endpoints/queue) |
| — | 硅基流动 `POST /v1/video/status` | 轮询用 POST |

既有调研已指出视频轮询是差异最大的部分；中转站通常连响应 schema 一起改写，所以「只改路径」不足以接上「提交是 A 协议、轮询是 B 协议」的组合，这类情况仍要靠声明式定义整体描述。

### 8.5 Query 参数与鉴权头差异

- Query：Azure 经典必须带 `api-version`；即梦与火山视觉把动作放进 query（`/?Action=CVSync2AsyncSubmitTask&Version=2022-08-31`，经 new-api 为 `/jimeng/?Action=...`）；Gemini 支持 `?key=`；MiniMax 取件 `?file_id=`。
- 鉴权头：
  - 中转站替换上游鉴权：可灵 JWT（AK/SK 签名）与即梦 SigV4 在 new-api 后都变成 `Bearer sk-...`（[kling plugin.js](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.41/plugins/tasks/kling/plugin.js)、[jimeng plugin.js](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.41/plugins/tasks/jimeng/plugin.js)）。ArcReel 的可灵端点已用 `auth_mode="bearer"` 适配这一点。
  - 仍在使用的原生鉴权头：Vidu `Authorization: Token <key>`、fal `Authorization: Key <key>`、Azure `api-key`、Gemini `x-goog-api-key`、Anthropic `x-api-key` + `anthropic-version`。
  - 叠加鉴权：Cloudflare 在供应商 key 之外加 `cf-aig-authorization`；启用 BYOK 后必须省略供应商 key，否则请求失败。

## 9. SDK 能否改路径

| SDK | `base_url` 自带路径 | 资源路径 | 单条请求改路径 | 绕过方式 |
|---|---|---|---|---|
| openai-python | 保留。`_prepare_url` 把相对路径拼在 `base_url.raw_path` 之后，`https://relay/x/y` + `/chat/completions` → `/x/y/chat/completions` | 写死在资源方法里，如 `"/videos"`、`"/images/edits"` | 无。单次请求只能改 `extra_headers`、`extra_query`、`extra_body`、`timeout` 等 | ① 低层 `client.post(path, cast_to=..., body=...)`，`path` 可为绝对 URL（`_prepare_url` 原样返回）；② `with_options(base_url=...)` 按请求换 base；③ 自定义 `http_client` 包装 transport 改写 URL；④ 子类覆写 `_build_request` / `_prepare_url`（私有 API，`AzureOpenAI` 即用此法注入 `/deployments/{model}`） |
| anthropic-sdk-python | 保留，逻辑同 openai-python | 资源路径自带 `/v1`，如 `"/v1/messages"`，所以 `base_url` 不能带 `/v1`，否则得到 `/v1/v1/messages`（推断） | 无 | 同 openai-python |
| google-genai | 保留。URL = `base_url` + `{api_version}/` + 资源路径 | 资源路径由 SDK 生成，如 `models/{m}:generateContent` | 部分：各 config 都有 `http_options`，可按请求换 `base_url`、`api_version`、`headers`、`extra_body`，但不能换方法路径 | ① `api_version` 可设任意值，设为空串时不带版本段（推断）；② `base_url_resource_scope = COLLECTION` 时 URL = `base_url` + 资源路径；③ Vertex 模式下 `base_url` 非 `*.googleapis.com` 且不给项目、地域时，请求 URL 就是 `base_url` 本身；④ 自定义 `httpx_async_client`；⑤ 私有 `client._api_client.request(method, path, ...)` |
| xai-sdk | 不适用。gRPC，只能设 `api_host`（host:port）与 `channel_options` | `/{package.Service}/{Method}` 固定 | 无 | 只能经 HTTP CONNECT 代理（`grpc_proxy`）；无法经过改路径的 HTTP 中转站 |
| Ark SDK（volcengine） | `BASE_URL` 常量为 `https://ark.cn-beijing.volces.com/api/v3`，可传入替换 | 资源路径写死 | 未核对 | 未核对 |
| httpx 直连（百炼、可灵、Vidu、MiniMax、声明式） | 完全由 ArcReel 代码决定 | 由代码或定义决定 | 可以 | — |

来源：[openai _base_client.py](https://github.com/openai/openai-python/blob/main/src/openai/_base_client.py)、[openai _types.py](https://github.com/openai/openai-python/blob/main/src/openai/_types.py)、[openai azure.py](https://github.com/openai/openai-python/blob/main/src/openai/lib/azure.py)、[anthropic _base_client.py](https://github.com/anthropics/anthropic-sdk-python/blob/main/src/anthropic/_base_client.py)、[anthropic messages.py](https://github.com/anthropics/anthropic-sdk-python/blob/main/src/anthropic/resources/messages/messages.py)、[google-genai _api_client.py](https://github.com/googleapis/python-genai/blob/main/google/genai/_api_client.py)、[google-genai types.py](https://github.com/googleapis/python-genai/blob/main/google/genai/types.py)、[xai-sdk client.py](https://github.com/xai-org/xai-sdk-python/blob/main/src/xai_sdk/client.py)、[grpc environment_variables](https://github.com/grpc/grpc/blob/master/doc/environment_variables.md)、[Ark SDK _constants.py](https://github.com/volcengine/volcengine-python-sdk/blob/master/volcenginesdkarkruntime/_constants.py)。本仓库锁定的版本为 openai 2.54.0、google-genai 2.25.0、xai-sdk 1.20.0，`_prepare_url` 与 `HttpOptions` 字段已在本地安装包中核对一致。

对路径覆盖设计的含义：

- 「加前缀」「改版本段」两类改动，对 SDK 实现的端点只要把覆盖值合成进传给 SDK 的 `base_url`（Gemini 另加 `api_version`）即可，不需要动 SDK。
- 「改路径名」对 SDK 实现的端点需要在 transport 层改写 URL，或改用低层调用。transport 包装依赖的是 httpx 的公共接口，openai-python 与 google-genai 都接受自定义 httpx 客户端，比覆写私有方法稳定。openai-python 主干已改用 `httpx2`（[pyproject](https://github.com/openai/openai-python/blob/main/pyproject.toml)），升级时这条路要随之核对。
- 一个端点可能有多条请求（提交、轮询、取件，或 generations 与 edits），覆盖粒度需要到「端点内的每条请求」，参照 new-api 高级自定义渠道的 `incoming_path → upstream_path` 列表。
- 内置 Grok 走 gRPC，路径覆盖对它无意义；中转站场景只能让用户改用 REST 协议端点（`openai-chat`、`openai-responses`、OpenAI 图片形态）。

## 10. 缺口与优先级

评分口径：使用面看「多少官方厂商与中转站提供这个协议、ArcReel 用户有多大概率需要它」；实现成本看「能否复用现有 SDK 与 backend、请求响应形态差多远」。

| 优先级 | 媒体 | 新增内置协议 | 使用面 | 实现成本 | 理由 |
|---|---|---|---|---|---|
| P0 | 文本 | `anthropic-messages` | 高 | 中 | Anthropic 官方唯一生产协议；国内主流厂商与主要中转站都提供；Agent 侧已有 URL 归一化、预设目录与模型发现可复用 |
| P1 | 文本 | `openai-responses` | 中高 | 中 | OpenAI Responses 独占模型、xAI 推荐接口；one-api 不支持，new-api 能互转 |
| P1 | 音频 | `dashscope-tts` | 中 | 低 | backend 已有，只差端点注册 |
| P1 | 音频 | `gemini-tts` | 中 | 中 | 中转站能透传 `/v1beta`；`/v1/audio/speech` 之外最常见的 TTS 形态 |
| P1 | 图片 | Chat Completions 出图 | 中 | 中低 | OpenRouter、Vertex OpenAI 兼容与承载 Gemini 出图的中转站 |
| P2 | 视频 | `gemini-video`（`predictLongRunning`） | 低中 | 中 | `/v1/videos` 形态已能接大部分 Veo 中转 |
| P2 | 音频 | `minimax-tts`、`xai-tts` | 低中 | 中、低中 | 按用户反馈 |
| P3 | 文本 | Gemini Interactions | 低（当前） | 中 | 观察新模型是否只在 Interactions 上线 |
| P3 | 图片 | Responses `image_generation` | 低 | 中 | 中转站支持面窄 |
| P3 | 音频 | 豆包语音 | 中 | 高 | 多字段鉴权只能做内置供应商 |

不新增内置协议、交给声明式定义或路径覆盖的：OpenRouter `/images` 与 `/videos`、xAI 视频、智谱、千帆、硅基流动视频、AIMLAPI、fal。其中 OpenRouter 与 xAI 视频可考虑做成随版声明式定义。

需要随本图一并处理的存量问题：

- `infer_endpoint` 的 `imagen` → `gemini-image` 规则：Imagen 已在 Gemini API 关停，且 `gemini-image` 走 `generateContent` 本就调不了 `:predict`。
- 内置 `openai` 供应商的 `sora-2` 与 `sora-2-pro` 已随上游关停。
- `openai-video` 依赖 openai-python 的 `videos` 资源；Videos API 关停后 SDK 是否保留该资源：未查明。若移除，需改用低层调用。

## 11. 风险与未查明项

- **未查明**：
  - OpenAI `gpt-realtime-2.1-mini` 能否用于 `/v1/audio/speech`；不带日期的 `gpt-4o-mini-tts` 别名是否随快照关停。
  - GPT-5.6 `reasoning.mode: "pro"` 是否支持 Chat Completions。
  - DeepSeek 是否接受 `https://api.deepseek.com/v1`（影响 `ensure_openai_base_url` 自动补 `/v1` 的行为）。
  - MiniMax 旧域名 `api.minimaxi.com` 是否继续可用。
  - 千帆是否有 Anthropic 出口；Kimi、MiniMax、硅基流动的 Anthropic 出口是否接受 `x-api-key`。
  - Gemini `generateContent` 出图与 TTS 的现行响应形态（指南已只给 Interactions 示例）。
  - LiteLLM 的 Responses、图片、TTS 路由；one-api 分支的路由集。
  - 声明式定义能否表达「轮询用 POST」和「任务 id 含 `/`」。
- **推断项**：google-genai `api_version=""` 去掉版本段、anthropic SDK `base_url` 带 `/v1` 会得到 `/v1/v1/messages`、new-api 前缀经现有 `base_url` 归一化可用，均来自读源码，未实测。
- **时效风险**：OpenAI、Gemini 本季度连续发生协议级变化（Videos API 关停、Interactions GA、Imagen 关停、TTS 模型替换到 Realtime）。出厂快照与官方同步需要能表达「协议级」变化，不只是价格和模型名。
- **普及度口径**：中转站普及度只用 GitHub stars 与「国内厂商直连覆盖」两个代理指标，没有部署量数据。

## 12. 来源索引

一手来源均在正文对应位置给出链接。主要入口：

- OpenAI：[deprecations](https://developers.openai.com/api/docs/deprecations)、[image-generation](https://developers.openai.com/api/docs/guides/image-generation)、[video-generation](https://developers.openai.com/api/docs/guides/video-generation)、[text-to-speech](https://developers.openai.com/api/docs/guides/text-to-speech)、[openai-python](https://github.com/openai/openai-python)
- Anthropic：[API overview](https://platform.claude.com/docs/en/api/overview)、[OpenAI SDK 兼容](https://platform.claude.com/docs/en/api/openai-sdk)、[anthropic-sdk-python](https://github.com/anthropics/anthropic-sdk-python)
- Google：[interactions](https://ai.google.dev/gemini-api/docs/interactions)、[generate-content](https://ai.google.dev/api/generate-content)、[openai compat](https://ai.google.dev/gemini-api/docs/openai)、[veo](https://ai.google.dev/gemini-api/docs/veo)、[speech-generation](https://ai.google.dev/gemini-api/docs/speech-generation)、[imagen](https://ai.google.dev/gemini-api/docs/imagen)、[python-genai](https://github.com/googleapis/python-genai)
- xAI：[REST inference](https://docs.x.ai/developers/rest-api-reference/inference.md)、[xai-sdk-python](https://github.com/xai-org/xai-sdk-python)
- 中转与网关：[new-api v1.0.0-rc.41](https://github.com/QuantumNous/new-api/tree/v1.0.0-rc.41)、[one-api](https://github.com/songquanpeng/one-api/tree/8df4a2670b98266bd287c698243fff327d9748cf)、[OpenRouter openapi.json](https://openrouter.ai/openapi.json)、[LiteLLM v1.104.1](https://github.com/BerriAI/litellm/tree/v1.104.1)、[Cloudflare AI Gateway](https://developers.cloudflare.com/ai-gateway/)、[Azure OpenAI reference](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/reference)、[Vercel AI Gateway](https://vercel.com/docs/ai-gateway/openai-compat)
