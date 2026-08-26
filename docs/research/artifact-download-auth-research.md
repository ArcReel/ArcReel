# 产物下载请求的鉴权附带策略调研

> 用途：回答 [#2140](https://github.com/ArcReel/ArcReel/issues/2140)——自定义调用端点（[#2119](https://github.com/ArcReel/ArcReel/issues/2119)）在任务完成后从产物 URL 下载视频时，要不要附带定义文件 `auth` 节的凭证（header / query）。为 [#2126](https://github.com/ArcReel/ArcReel/issues/2126) 的三个候选提供依据：(a) 下载 URL 与 `base_url` 同源时自动附带、异源裸请求；(b) 定义文件显式 `download.auth` 开关；(c) 一律裸请求。
> 来源纪律：只采信官方文档、官方 OpenAPI / Apifox 页、开源仓库源码与 RFC；每条结论附来源。文档未明示或抓取失败者标「未确认」，不以推测填补；由规范条文推导的结论标「推导」。
> 既有材料：`arcreel-video-api-protocol-research.md` §5.2 / §8.3（官方平台 URL 有效期）；`research/video-protocol-survey` 分支 `docs/research/video-protocol-survey-aggregators.md` 第三、四节（十家聚合平台的产物路径与鉴权）。本文不重复其内容，只补「URL 归属域与鉴权形态」这一维度。
> 调研日期：2026-08-26。

---

## 一、问题拆解

产物 URL 有三种归属形态，鉴权需求互斥：

| 形态 | 含义 | 下载时的鉴权需求 |
| --- | --- | --- |
| I 同域代理 / 内容端点 | URL 与 API `base_url` 同源，由 API 服务自己回源出字节 | 必须带 API 凭证 |
| II 对象存储 / CDN 签名 URL | query 内含签名与过期参数（`X-Amz-Signature`、`Expires=&Signature=`、`X-Tos-*`、`X-Goog-Signature`、`_jwt=` 等） | 凭证已在 URL 内；额外鉴权可能被拒 |
| III 无鉴权公网 URL | CDN 域名，无签名参数，凭 URL 即可访问 | 不需要 |

决策要点是：同一份声明式定义面对的三种形态能否被静态区分；把凭证带到 II / III 类域名的代价是什么。

---

## 二、ArcReel 现状

- 共用下载函数 `lib/video_backends/base.py` `download_video()`：`httpx.AsyncClient()` 裸请求，不带任何 header，超时 120 s。
- httpx 0.28.1（`uv.lock`）`AsyncClient` 与 `stream()` 默认 `follow_redirects=False`（`httpx/_client.py` 构造函数签名 `follow_redirects: bool = False`）；`Response.raise_for_status()` 仅对 `is_success`（2xx）放行，未跟随的 3xx 走 `error_types[3] = "Redirect response"` 分支并抛 `HTTPStatusError`（`httpx/_models.py`）。即 **现状下产物 URL 只要返回 302 就下载失败**，与鉴权无关。
- 内置 backend 对 I 类的处理：`openai.py` 经 SDK `videos.download_content()`（同域 `/videos/{id}/content` + Bearer）；`gemini.py` AI Studio 模式经 SDK `files.download()`（同域 + `x-goog-api-key`）。二者都不是走 `download_video()`。
- `newapi.py` 取到 `url / result_url` 后直接走裸 `download_video()`。而 new-api 在上游不返回 URL 的渠道（Sora / Gemini / Vertex）会生成需要鉴权的代理 URL（见 §3.1），此路径今天会得到 401。这是现状缺口，独立于 #2126 的选型。

---

## 三、核实点 1：抽样平台的产物 URL 形态

### 3.1 逐家归类

来源均为官方文档示例响应、官方 OpenAPI 页或官方 SDK / 开源仓库源码；示例 URL 只保留域名与参数名。

| 平台 | 归类 | 域名 / 端点 | 签名或鉴权 | 有效期 | 来源 |
| --- | --- | --- | --- | --- | --- |
| new-api | I（上游不给 URL 时）/ 透传上游 URL | `{ServerAddress}/v1/videos/{task_id}/content` | 路由挂 `middleware.TokenOrUserAuth()`，API 客户端须带 new-api API Key | — | `relay/channel/task/taskcommon/helpers.go` `BuildProxyURL`；`service/task_polling.go`；`router/video-router.go`（commit 2d8e50b） |
| fal.ai | III | `v3b.fal.media` / `v3.fal.media` | 无；FAQ 原文「Media URLs returned by fal … are publicly accessible — anyone with the URL can access the file until it expires」 | 默认 ≥ 7 天，可配 | `fal.ai/docs/documentation/model-apis/faq`、`.../media-expiration` |
| Replicate | III | `replicate.delivery` | 文档未展示签名参数，也未要求鉴权 | 1 小时 | `replicate.com/docs/topics/predictions/output-files` |
| kie.ai | III | `tempfile.aiquickdraw.com` | 无 | 14 天（market 通用页写 24 小时，口径不一） | `docs.kie.ai/veo3-api/get-veo-3-1080-p-video` |
| RunningHub | III | `rh-images.xiaoyaoyou.com` | 无 | 未见 | `runninghub.cn/runninghub-api-doc-cn/doc-8287342` |
| 302.ai | III | `file.302.ai` | 无 | 未见 | `302ai-en.apifox.cn/api-207705176` |
| PiAPI | III | `img.theapi.app/ephemeral/…` | 无 | 未给具体时长 | `piapi.ai/docs/veo3-api/get-task` |
| WaveSpeedAI | III | `cdn.wavespeed.ai/outputs/…` | 无 | 约 7 天 | `wavespeed.ai/docs/docs-common-api/predictions`、`.../data-retention-policy` |
| Segmind | 未确认 | 响应示例无 URL（同步端点直接返回 mp4 字节） | — | 结果保留 1 小时 | `segmind.com/models/*/api` |
| Novita AI | II | `faas-output-*.s3.ap-southeast-1.amazonaws.com` | S3 域名 + `video_url_ttl`；当前文档示例未展示 `X-Amz-*` 参数 | `video_url_ttl`（示例 3600 s） | `novita.ai/docs/api-reference/model-apis-task-result` |
| OpenAI Sora 2 | I | `api.openai.com/v1/videos/{id}/content` | Bearer | — | `openai-python` `src/openai/resources/videos.py` `download_content` |
| Google Gemini Veo | I | `generativelanguage.googleapis.com/v1beta/files/{id}:download?alt=media` | `x-goog-api-key`；文档下载命令 `curl -L -o … -H "x-goog-api-key: $GEMINI_API_KEY" "${video_uri}"` | 2 天 | `ai.google.dev/gemini-api/docs/veo` |
| Vertex AI Veo | I（`gs://` 需 GCP 凭证）/ base64 内联 | GCS bucket | GCP 凭证 | — | `docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/generate-videos-from-text` |
| 可灵 Kling | 未确认 | 文档只写「hotlink protection format」 | 未展示 | 30 天 | `kling.ai/document-api/apiReference/model/textToVideo` |
| MiniMax | III（按示例） | `cdn.hailuoai.com` | 无 | 未见 | `platform.minimax.io/docs/api-reference/video-generation-v2-query` |
| 阿里云 DashScope 万相 | II | `dashscope-result-sh.oss-accelerate.aliyuncs.com/….mp4?Expires=…` | OSS URL 签名 | 24 小时 | `help.aliyun.com/zh/model-studio/text-to-video-api-reference` |
| 火山 Ark Seedance | II | `ark-content-generation-*.tos-*.volces.com` | TOS 域名；示例参数打码 | 24 小时（2.5 限 100 次下载） | `volcengine.com/docs/82379/1521309` |
| Runway | II | `dnznrvs05pmza.cloudfront.net/output.mp4?_jwt=…` | `_jwt` | 24–48 小时 | `docs.dev.runwayml.com/assets/outputs/` |
| Luma | II | 示例为占位域名 | `X-Amz-Expires=3600&…`；原文「Presigned URLs expire after 1 hour」 | 1 小时 | `docs.agents.lumalabs.ai/guides/videos/generation` |
| Vidu | 未确认 | 示例为占位符 | — | 未见 | `docs.platform.vidu.com/7207545m0` |
| xAI Grok Imagine | III | `vidgen.x.ai` | 未见签名；原文「Videos are returned as temporary URLs」 | 未给具体时长 | `docs.x.ai/developers/model-capabilities/video/generation` |
| PixVerse | 未确认 | 示例 `"url": "string"` | — | 未见 | `docs.platform.pixverse.ai` |
| 即梦 Jimeng | 未确认 | 示例 `https://xxxx` | — | 1 小时 | `volcengine.com/docs/85621/1777001` |

**统计（23 家）**：I 类 4 家（new-api 代理、OpenAI、Gemini、Vertex）；II 类 5 家（Novita、DashScope、Ark、Runway、Luma）；III 类 9 家；未确认 5 家。

**new-api 的关键细节**：`service/task_polling.go` 的分支是——上游 URL 为 `data:` 前缀或为空时 `ResultURL = BuildProxyURL(task.TaskID)`，否则 `ResultURL = taskResult.Url` 原样透传（注释「Direct upstream URL (e.g. Kling, Ali, Doubao, etc.)」）。没有全局开关。因此 **同一个 new-api 实例、同一份声明式定义，按渠道不同会交替返回 I 类（须鉴权）与 II 类（DashScope OSS 签名 URL，鉴权反而出错）产物 URL**，静态开关无法表达。

### 3.2 签名 URL 遇到额外鉴权的已知行为

| 存储 | header `Authorization` 与 URL 签名并存 | 追加未签名 query 参数（如 `?api_key=`） | 来源 |
| --- | --- | --- | --- |
| AWS S3 | 官方把两者列为二选一的认证方式；「If you add a signed header that is also a signed query parameter, and they differ in value, you will receive an InvalidRequest error」。第三方 issue 中常见的 `Only one auth mechanism allowed…` 文案在官方 ErrorResponses 页未找到 | SigV4 canonical query string 含全部 query 参数，追加即签名不匹配（推导，403 `SignatureDoesNotMatch`） | `AmazonS3/latest/API/sig-v4-authenticating-requests.html`、`sigv4-query-string-auth.html`（Wayback 快照）；`IAM/latest/UserGuide/create-signed-request.html` |
| 阿里云 OSS | V1 明示「OSS不支持同时在URL和Header中包含签名」，同时出现返回 400；V4 页未单独明示 | V1 CanonicalizedResource 只含子资源清单参数，无关参数不参与签名（不破坏签名，但仍进访问日志）；V4 Canonical Query String 含全部 query，追加即破坏（推导） | `help.aliyun.com/zh/oss/developer-reference/ddd-signatures-to-urls`、`.../add-signatures-to-urls` |
| 火山 TOS | 未确认 | 「所有 Query 参数必须参与计算」，追加即破坏（推导） | `docs.volcengine.com/docs/6349/129226`、`/74839` |
| GCS | 未确认 | canonical query string 须含「each query string parameter that will be subsequently included in any signed request」，追加即破坏（推导） | `cloud.google.com/storage/docs/authentication/canonical-requests` |
| Cloudflare R2 | 「no additional authentication headers are required」 | 「Attempting to modify the resource, operation, or expiry will result in a 403/SignatureDoesNotMatch error」 | `developers.cloudflare.com/r2/api/s3/presigned-urls/` |

结论：向 II 类 URL 附带 header 鉴权在 OSS V1 上是确定的 400、在 S3 上是文档明示的冲突场景；附带 query 鉴权在 S3 / OSS V4 / TOS / GCS 上都会破坏签名。**只要抽样里存在 II 类产物（5 家已确认，含 new-api 透传的 DashScope / Ark），「一律附带」就不是可选项。**

---

## 四、核实点 2：把凭证附到非 API 域的泄漏面

### 4.1 访问日志

| 日志 | 记录 query string | 记录 `Authorization` 值 | 来源 |
| --- | --- | --- | --- |
| S3 server access log | 是。`Request-URI` 字段示例 `GET /…/puppy.jpg?x-foo=bar HTTP/1.1`；「Amazon S3 ignores query-string parameters that begin with x-, but includes those parameters in the access log record」 | 否，只记认证类型 `AuthHeader` / `QueryString` | `AmazonS3/latest/userguide/LogFormat.html` |
| CloudFront standard log | 是，独立字段 `cs-uri-query` | 否（可选记 `cs(Cookie)`） | `AmazonCloudFront/latest/DeveloperGuide/standard-logs-reference.html` |
| 阿里云 OSS 日志转存 / SLS | 是。`RequestURL`「包含query string的请求URL」；SLS `request_uri`「包括query-string」（另一页称不记录 URL 参数，两处表述冲突） | 否，只记 `access_id` 与 `sign_type` | `help.aliyun.com/zh/oss/user-guide/logging`、`help.aliyun.com/zh/sls/log-fields-13` |
| GCS usage log | `cs_uri` 字段，是否含 query 未明示 | 否 | `cloud.google.com/storage/docs/access-logs` |
| Nginx `combined` | 是，`$request` 为完整请求行 | 否 | `nginx.org/en/docs/http/ngx_http_log_module.html` |

结论：**query 形式的凭证会进入所有对象存储 / CDN / 反代的默认访问日志；header 形式的凭证不会。** 定义文件 `auth` 节支持 query 参数（#2119 已定），因此把 query 凭证带到异源 URL 的泄漏面显著大于 header。

### 4.2 跳转时的凭证处理

| 客户端 / 规范 | 跨源跳转对 `Authorization` 的处理 | 来源 |
| --- | --- | --- |
| httpx 0.28.1 | `_redirect_headers`：`if not _same_origin(url, request.url): if not _is_https_redirect(request.url, url): headers.pop("Authorization", None)`；`_same_origin` 比较 scheme / host / port；`Cookie` 一律重取 | `httpx/_client.py`（本地 `.venv` 与上游 `encode/httpx` 一致） |
| requests | `rebuild_auth`：`should_strip_auth` 判定 hostname / scheme / port 变化即 `del headers["Authorization"]` | `psf/requests` `src/requests/sessions.py` |
| curl | 「curl only sends its credentials to the initial host. If a redirect takes curl to a different host, it does not get the credentials passed on」；需 `--location-trusted` 才跨 host 发送 | `curl/docs/cmdline-opts/location.md`、`location-trusted.md` |
| Fetch 标准 | HTTP-redirect fetch：「If request's current URL's origin is not same origin with locationURL's origin, then for each headerName of CORS non-wildcard request-header name, delete headerName」；该名单即 `Authorization` | `fetch.spec.whatwg.org/#http-redirect-fetch` |
| RFC 9110 §15.4 | 自动跟随跳转时「Consider removing header fields … where there are security implications; this includes but is not limited to Authorization and Cookie」 | `rfc-editor.org/rfc/rfc9110.html#section-15.4` |
| follow-redirects（n8n 底层） | 「Drop confidential headers when redirecting … to a different domain that is not a superdomain」 | `follow-redirects/index.js` |

结论：**「同源保留、跨源剥离」是 HTTP 客户端与规范的一致默认。** 若 `download_video()` 开启 `follow_redirects=True`，同域代理 302 到 OSS 的场景由 httpx 自动剥离 header，无需 ArcReel 额外处理；query 形式的凭证附在原 URL 上，不会随 `Location` 传到新 URL（除非服务端回显），泄漏面在第一跳日志（§4.1）。

---

## 五、核实点 3：业界声明式 HTTP 适配器的做法

| 产品 | 凭证作用域 | 对「响应里的文件 URL」 | 跨源跳转 | 来源 |
| --- | --- | --- | --- | --- |
| n8n HTTP Request 节点 | 节点级（None / Predefined / Generic Credential）；凭据可设「Allowed HTTP Request Domains」（All / Specific Domains / None，默认 All），执行与每一跳跳转均校验 | 不自动下载；需另起一个 HTTP Request 节点（Response Format: File），鉴权独立配置 | 「Send Credentials on Cross-Origin Redirect」布尔选项，节点版本 4.4 起默认 `false`；实现 `stripCredentials = isCrossOrigin(originalUrl, nextUrl) && !policy.sendCredentialsOnCrossOriginRedirect` | `docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.httprequest/`、`docs.n8n.io/build/understand-workflows/create-and-edit-credentials/`；`packages/workflow/src/credential-domain-restrictions.ts`、`packages/@n8n/backend-network/src/http/axios/redirect.ts`、`nodes/HttpRequest/V3/Description.ts` |
| Dify 自定义工具 | 工具提供者级（None / API Key，Header 或 Query，Basic / Bearer / Custom），对该 schema 下所有端点统一附加 | 不自动下载；`validate_and_parse_response` 只区分 JSON / 文本，无解析 URL 再请求的代码 | `do_http_request` 固定 `follow_redirects=True`，跨源剥离交给 httpx | `api/core/tools/custom_tool/tool.py`；`web/i18n/en-US/tools.json` |
| Dify 工作流 HTTP Request 节点 | 节点级 `no-auth / api-key`（`basic / bearer / custom` + 自定义 header 名） | 二进制响应按 Content-Type 落成 File 输出；不解析 JSON 内 URL | `follow_redirects: True` | `langgenius/graphon` `src/graphon/nodes/http_request/{entities,executor,node}.py` |
| Postman | collection → folder → request 继承（「Inherit auth from parent」） | 响应内链接高亮，点击才生成新 GET 请求；「Retain headers when clicking on links」需显式开启 | 「Follow Authorization header」设置及默认值：现行文档页未抓到，未确认 | `learning.postman.com/docs/use/send-requests/authorization/specifying-authorization-details`、`.../settings/general-settings` |
| OpenAPI 3.1 | `security` 作用于 OpenAPI Object / Operation Object；Link Object 只描述操作间关系（「MUST point to an Operation Object」） | 对响应体内 `format: uri` 值无任何鉴权语义 | — | `spec.openapis.org/oas/v3.1.0.html` |

官方 SDK 对「响应 URL 下载」的处理（作为业界惯例佐证）：

- OpenAI `videos.download_content`、google-genai `files.download`：I 类，同域 + API 凭证，由 SDK 内部完成。
- Replicate Python `FileOutput.read()` 复用带 `Authorization: Bearer` 的 httpx client 去拉 `replicate.delivery`——是抽样中唯一「无差别附带」的实现；因目标是 III 类公开 URL 而无害，但与本文 §3.2 / §4.1 的风险面相悖，不宜作为范式。
- fal（`fal-js` / `fal_client`）不提供下载函数；MiniMax、DashScope 官方示例均 `requests.get(download_url)` 裸请求。

结论：**没有任何声明式适配器会把 API 凭证自动带到响应里出现的 URL；凡是「自动」的行为，其边界都是同源 / 同域**（n8n 的域名白名单与跨源剥离、httpx / curl / Fetch 的跳转规则）。「显式配置」在这些产品里的粒度是「再建一个请求节点」，而不是在原请求上加一个下载开关。

---

## 六、对 (a)/(b)/(c) 的评估

| 候选 | I 类（4 家） | II 类（5 家） | III 类（9 家） | new-api 混合场景 | 泄漏面 |
| --- | --- | --- | --- | --- | --- |
| (a) 同源自动附带、异源裸请求 | 通过（URL 与 `base_url` 同源） | 通过（异源不附带，签名 URL 原样） | 通过 | 通过（逐个 URL 判定） | 凭证只到 API 自己的域；同 httpx / curl / Fetch 默认 |
| (b) 定义文件 `download.auth` 开关 | 开启时通过 | 关闭时通过；开启时 OSS V1 400、S3 冲突、query 破坏签名 | 通过 | **失败**：同一定义在渠道间交替出现 I / II 类 URL，静态开关无法同时满足 | 开启时 query 凭证进入对象存储 / CDN 日志 |
| (c) 一律裸请求 | **失败**（401） | 通过 | 通过 | 部分失败（代理 URL 渠道） | 无 |

- (c) 排除：#2119 起步模板包含「NewAPI 视频」，而 new-api 在 Sora / Gemini / Vertex 渠道只返回须鉴权的代理 URL；内置 `openai.py` / `gemini.py` 也证明同域取件端点是官方平台的常规设计。
- (b) 排除：它假设产物 URL 形态在定义时可知，但 §3.1 的 new-api 分支表明形态由运行时的上游渠道决定；且开关打开时会把凭证送到签名 URL（§3.2）与第三方日志（§4.1）。开关只能在「同源判定」之上做为逃生口，本身不能替代同源判定。抽样中未发现任何「同源 URL 拒绝鉴权」的平台，首期没有需要逃生口的用例。
- (a) 推荐：覆盖三类形态与混合场景；边界与 httpx `_same_origin`、n8n 域名限制、Fetch 跳转规则一致，实现与心智模型都有现成先例。

### 推荐

采用 **(a)**，具体约定：

1. 同源判定按 scheme + host + port（等价 httpx `_same_origin`），不比较路径前缀。`base_url` 的路径部分（如 `/v1`）不参与判定。
2. 同源时按定义文件 `auth` 节原样附带：header 类加 header；query 类在 URL 尚无同名参数时追加，已有同名参数视为服务端已签入、不覆盖（与 #2123「`auth` 节是凭证唯一写入口」的校验错误规则区分：此处是运行时对服务端返回 URL 的容错，不是定义校验）。
3. `download_video()` 开启 `follow_redirects=True`：一是修复现状 3xx 直接失败的缺陷（§二），二是同域代理 302 到对象存储时由 httpx 自动剥离 header（§4.2），(a) 的同源判定只需对第一跳做。
4. 首期不设 `download.auth` 开关；若日后出现「同源 URL 拒绝鉴权」的平台，再以 `download.auth: never` 形式加逃生口，缺省仍为同源附带。
5. `newapi.py` 内置 backend 存在与本议题相同的缺口（§二），建议在实现 (a) 时让共用 `download_video()` 接收可选的鉴权附加器，内置 backend 与声明式端点同一路径收敛。

---

## 七、不确定项

1. S3 「Only one auth mechanism allowed…」错误文案在官方 ErrorResponses 页未找到，仅见第三方 issue；官方以「二选一」与「InvalidRequest」间接支持。
2. S3 / TOS / GCS 对预签名 URL 追加未签名参数必定失败，是由 canonical query string 规则推导，官方无逐字句；S3 对 `x-` 前缀参数的「ignore」是否也免于签名校验未确认。
3. OSS V4、TOS、GCS 的「URL 签名与 `Authorization` header 并存」行为文档均未明示。
4. OSS 日志是否记录 query string：`logging` 页与 `set-logging-request-headers-or-url-parameters` 页表述冲突。
5. Postman「Follow Authorization header」设置原文及默认值未在现行文档页抓到。
6. Kling、Vidu、PixVerse、即梦、Segmind 五家官方文档未展示真实产物 URL，归类未确认；Novita / Ark 的签名参数在当前示例中被打码或省略，归 II 类依据是对象存储域名与明示的 TTL。
7. Replicate `replicate.delivery`、xAI `vidgen.x.ai` 归 III 类是因为官方未展示签名参数也未要求鉴权，但没有 fal 那样「publicly accessible」的正面表述。

## 修订注

- 2026-08-26：初版。`video-protocol-survey-aggregators.md` §3.1 new-api「核对」一节写的前提「下载步骤沿用提交时的鉴权 header」应按本文收窄为「同源时沿用」；该分支文档的修订由其所属议题处理。
