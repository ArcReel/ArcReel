# 声明式 API 适配格式的既有实践调研

**调研截止日期**：2026-08-26
**用途**：Wayfinder #2119「自定义调用端点（声明式协议定义、导入导出与试跑器）」的输入素材；对应 research 票 #2120
**作者**：协助调研（Claude）
**关联现状**：`lib/custom_provider/endpoints.py`（`EndpointSpec`）、`lib/video_backends/base.py`（`_PROVIDER_STATUS_SYNONYMS`、`first_str_by_paths`）、`docs/research/arcreel-video-api-protocol-research.md` 第 7 章

---

## 0. 调研范围与方法

问题：n8n HTTP Request 节点、Dify 自定义工具与 HTTP Request 节点、Postman、LiteLLM 自定义 provider / `config.yaml`、new-api 与 one-api 渠道配置，各自如何表达以下四件事：

1. 占位符语法与类型保留约定
2. 响应字段提取方式
3. 素材（图片 / 文件）编码声明
4. 导出文件的版本化与迁移

目的：ArcReel 的声明式模板语言贴近用户既有心智、不自造方言。

方法：只采信一手来源——各项目官方文档站与 GitHub 主分支源码。每条结论标注来源类型：

- 【文档】官方文档原文陈述
- 【源码】主分支源码可直接核对的事实
- 【推断】由上述材料推出的判断，未在来源中以原话出现

不在范围：各项目的商业信息、UI 交互细节、ArcReel 的实现方案。

---

## 1. 对照表

| 维度 | n8n HTTP Request | Dify 自定义工具 / HTTP 节点 | Postman | LiteLLM `config.yaml` | new-api / one-api 渠道 |
|---|---|---|---|---|---|
| 占位符语法 | `{{ $json.a.b }}`，内容是 JavaScript；导出 JSON 里以 `=` 前缀标记表达式 | `{{#node_id.var.key#}}`，`#` 包裹的点路径，不支持 `[0]` 下标 | `{{var}}`，纯文本替换；路径变量 `:id`；动态变量 `{{$guid}}` | `os.environ/VAR` 仅用于取环境变量；其余字段是 YAML 字面量 | 无占位符；`param_override` 是 JSON 字典或 `operations` 数组（`path`/`mode`/`value`） |
| 类型保留 | 表达式解析为原生类型（object/number），整段是表达式时保留；`jsonBody` 若为字符串再 `JSON.parse` | 先文本拼接再整体 `json.loads`：占位符不加引号则保留 number/object，加引号则成字符串；form-data 一律字符串 | 变量一律字符串存储；JSON body 内 `{{n}}` 不加引号则文本替换后成为 JSON 数字字面量 | YAML 原生类型透传到 `litellm_params` | `param_override.value` 是 JSON 原生类型，按 `path` 用 `sjson` 写入请求体 |
| 响应提取 | 下游用 `{{ $json.x }}`；分页选项内 `$response.body` / `$response.headers` / `$response.statusCode`；`$jmespath()`；`Include Response Headers and Status` 把响应拆成 body/headers/statusCode | HTTP 节点输出 `body`（字符串）/`status_code`/`headers`/`files`；无 JSONPath 节点，取字段要 Code 节点先 `json.loads`；自定义工具响应以 `json` + `text` 消息回流 | 全靠脚本：`pm.response.json()`、`pm.response.text()`、`pm.response.headers`，再 `pm.collectionVariables.set()` 串联 | 无声明式提取；provider 响应由 Python 代码归一为 OpenAI `ModelResponse`（自定义 provider 需实现 `CustomLLM`） | 无声明式提取；各上游由 Go adaptor 转换；任务类统一为 `TaskStatus` 枚举（`SUBMITTED`/`QUEUED`/`IN_PROGRESS`/`SUCCESS`/`FAILURE`）并映射到 `/v1/video/generations/{task_id}` 的 `status` |
| 素材编码 | 条目级 `binary` 对象：`data`（base64）+ `mimeType` + `fileName` + `fileExtension`；请求体 `n8n Binary File` / `multipart-form-data`；响应 `Response Format: File` 落到 `data` 字段 | `file` / `array[file]` 变量，`transfer_method` = `remote_url` / `local_file` / `tool_file`；请求体 `binary` 或 form-data `type: file`；响应按 Content-Disposition / Content-Type 判定为文件，上限 `HTTP_REQUEST_NODE_MAX_BINARY_SIZE`（默认 10MB） | form-data `type: "file"` 与 `binary` 模式只存**本机路径**（相对工作目录），文件不随 collection 导出；base64 需自己在脚本里编 | 沿用 OpenAI 约定：`image_url` 为 URL 或 `data:image/jpeg;base64,...`；图像生成 `response_format: url \| b64_json`；`model_info.supports_vision` 声明能力 | 沿用 OpenAI 约定；视频接口 `image` 字段文档写明「URL/Base64」；渠道配置无素材编码声明 |
| 版本化与迁移 | 每个节点带 `typeVersion`，HTTP Request 现为 `[1,2,3,4,4.1,…,4.5]`，`defaultVersion: 4.5`；旧 workflow 继续用保存时的版本，不自动升级；导出含 credential 名称与 ID | DSL YAML 顶层 `version`（当前 `0.7.0`）+ `kind: app`；导入按 semver 三档：更高版本或 major 更低 → `PENDING` 需确认，minor 更低 → `COMPLETED_WITH_WARNINGS`，其余直接完成；无自动迁移脚本；自定义工具 provider 与凭证不在 DSL 内 | `info.schema` URL 标版本（v1 / v2.0 / v2.1）；Postman v12 引入 3.0（多 YAML 文件，`*.request.yaml`），`postman collection migrate` 单向 2.1 → 3；Newman 只跑 2.1 | 无 schema 版本字段；顶层键 `model_list` / `litellm_settings` / `general_settings` / `router_settings` / `include`；`STORE_MODEL_IN_DB` 后模型定义迁入数据库，凭证以 `LITELLM_SALT_KEY` 加密 | 无版本字段；渠道行是 GORM 模型，`AutoMigrate` 隐式迁移；`param_override` 简单模式被文档称为「向前兼容」；无渠道导入 / 导出文件格式 |

---

## 2. 各家详述

### 2.1 n8n HTTP Request 节点

**占位符与类型**

- 【文档】表达式用 `{{ }}` 包裹，内容是 JavaScript；工作流 JSON 中含表达式的参数值以 `=` 开头。常用变量：`$json`（当前条目的 JSON，`$input.item.json` 的简写）、`$('Node name')`、`$input.item` / `$input.all()`、`$binary`、`$vars`、`$env`。
- 【文档】表达式解析为原生类型（对象、数字），而不是字符串，需要转换时显式用 `.toString()` / `.toNumber()`。
- 【源码】`HttpRequestV3.node.ts` 的 `specifyBody` 有 `keypair` 与 `json` 两种；`json` 模式下 `jsonBody` 若已是对象直接使用，若是字符串则 `JSON.parse()`。
- 【推断】因此「整个字段只有一个表达式 → 保留原生类型；表达式嵌在文本里 → 拼成字符串」是 n8n 的实际语义；`={{ { "a": 1 } }}` 直接得到对象，而 `="{{ $json.n }} items"` 得到字符串。

**响应提取**

- 【文档】`Response Format` 有 `Autodetect` / `JSON` / `Text` / `File`；`Include Response Headers and Status` 开启后「返回完整响应（headers 与状态码）以及 body」；`Never Error` 开启后「无论返回码为何都视作成功」。
- 【文档】分页选项内置变量：`$pageCount` 记录已取页数；`$response` 含 `$response.body`、`$response.headers`、`$response.statusCode`。
- 【文档】`$jmespath(obj, expression)` 用 JMESPath 表达式从对象或对象数组抽取数据。
- 【推断】节点自身不做「响应路径 → 输出字段」的声明式映射；提取发生在下游节点的表达式里。`$response` 对象模型（body / headers / statusCode 三分）是 n8n 对「响应」最接近声明式的抽象。

**素材编码**

- 【文档】条目结构为 `{ "json": {...}, "binary": { "<field>": { "data": "<base64>", "mimeType": "image/png", "fileExtension": "png", "fileName": "example.png" } } }`；`data` 必填，其余可选。
- 【文档】请求体 `Body Content Type` 提供 `Form-Data (multipart)`、`n8n Binary File`（把 n8n 中存的文件内容作为 body，需指定输入字段名）、`JSON`、`Raw`；【源码】对应 `bodyContentType` 取值 `json` / `form-urlencoded` / `multipart-form-data` / `binaryData` / `raw`。
- 【文档】`Response Format: File` 把响应放进 `Put Output in Field` 指定的字段；【源码】默认字段名 `data`。
- 【文档】文件转换靠 `Convert to File` / `Extract From File` / 读写磁盘节点。

**版本化与迁移**

- 【源码】`HttpRequest.node.ts` 的 `nodeVersions`：`1 → HttpRequestV1`、`2 → HttpRequestV2`、`3, 4, 4.1, 4.2, 4.3, 4.4, 4.5 → HttpRequestV3`，`defaultVersion: 4.5`；V3 内部用版本号分支处理默认行为差异（如重定向、跨域凭证）。
- 【文档】「如果用户用版本 1 构建并保存了工作流，即使你发布了节点的版本 2，n8n 仍在该工作流中继续使用版本 1。」
- 【文档】导出的工作流 JSON「包含凭证名称与 ID」，分享前需移除或匿名化。
- 【推断】n8n 的版本化粒度是**节点**而非文件：格式演进靠新增 `typeVersion` 并在实现内做分支，旧文件永不被改写；代价是实现里长期背着所有历史版本的分支。

来源：
- https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.httprequest/
- https://docs.n8n.io/build/work-with-data/transform-data/expression-reference.md
- https://docs.n8n.io/build/work-with-data/understand-n8ns-data-structure.md
- https://docs.n8n.io/build/work-with-data/handle-special-data-types/work-with-files-and-images.md
- https://docs.n8n.io/build/manage-workflows/export-and-import.md
- https://docs.n8n.io/connect/create-nodes/build-your-node/reference/versioning.md
- https://github.com/n8n-io/n8n/blob/master/packages/nodes-base/nodes/HttpRequest/HttpRequest.node.ts
- https://github.com/n8n-io/n8n/blob/master/packages/nodes-base/nodes/HttpRequest/V3/HttpRequestV3.node.ts

### 2.2 Dify 自定义工具与 HTTP Request 节点

调研时 Dify 主分支已把工作流节点实现抽到独立包 `graphon`（`src/graphon/nodes/http_request/`），下文源码引用以该包为准。

**占位符与类型**

- 【源码】变量引用正则 `\{\{#([a-zA-Z0-9_]{1,50}(?:\.[a-zA-Z_][a-zA-Z0-9_]{0,29}){1,10})#\}\}`；`convert_template` 按 `.` 切成 selector `[node_id, var, nested...]` 从 VariablePool 取值，取不到则当字面文本保留。系统变量如 `{{#sys.query#}}` 同规则。
- 【推断】正则不支持 `[0]` 下标；官方 HTTP 节点文档中出现的 `{{api_response.data.items[0].id}}` 与源码不符，以源码为准。
- 【源码】各段渲染为文本：Object → `json.dumps(value, ensure_ascii=False)`，Number/String → `str(value)`，File → `""`。
- 【源码】HTTP 节点 JSON body：`group = convert_template(...)`，`repaired = repair_json(group.text)`，`self.json = json.loads(repaired, strict=False)`——先纯字符串拼接，再整体解析。单测：`'{"number": {{#pre_node_id.number#}}}'` 且变量为 42 → `{"number": 42}`；`"{{#pre_node_id.object#}}"` → 解析为 dict。
- 【推断】类型是否保留取决于模板作者是否给占位符加引号；字符串变量内含引号或换行不做转义，只靠 `json_repair` 兜底。form-data / x-www-form-urlencoded / raw 一律字符串。
- 【源码】自定义工具（`api/core/tools/utils/parser.py`）：自动探测 OpenAPI 3 / Swagger 2 / OpenAI plugin；参数类型映射 integer/number → NUMBER、boolean → BOOLEAN、`format: binary` → FILE、array → ARRAY，嵌套 object 被展平为独立参数；LLM 填参后按 `cast_parameter_value` 转型，发请求时再按 schema 二次转型（`_convert_body_property_type`），header 强制 `str(value)`。

**响应提取**

- 【源码】HTTP 节点输出 `{"status_code", "body": response.text if not files else "", "headers", "files"}`——`body` 是**字符串**，且响应被判为文件时 `body` 为空串。
- 【文档】下游取字段：Code 节点（Python/JS，嵌套 ≤ 5 层）；List Operator 只支持标量数组与文件数组，不支持 `array[object]`；Variable Aggregator 只做分支汇聚；Parameter Extractor 用 LLM 抽取。
- 【推断】没有 JSONPath 节点。对 `body` 需 Code 节点 `json.loads` 后返回 object，再用 `{{#code.out.key.sub#}}` 深入取值。官方测试夹具 `http_request_with_json_tool_workflow.yml` 用内置工具 `json_parse` 解析 `{{#http_node.body#}}`，说明官方也走「先解析再引用」。
- 【源码】自定义工具响应：`Content-Type: application/json` 且能 `response.json()` → `create_json_message(dict)` + `create_text_message(text)`；工作流 Tool 节点固定输出 `text` / `files` / `json`；Agent 路径把 JSON `json.dumps` 后与 text 合并喂给 LLM。

**素材编码**

- 【源码】文件类型 `file` / `array[file]`；`FileTransferMethod` = `remote_url` / `local_file` / `tool_file` / `datasource_file`；`FileType` = image / document / audio / video / custom；【文档】上传上限 image 10MB、document 15MB、audio 50MB、video 100MB。
- 【源码】HTTP 节点发文件：`binary` body 取单个 file selector，`file_manager.download(file)` 后作为 raw content；form-data 中 `type: file` 项构造 `(key, (filename, bytes, mime_type or "application/octet-stream"))` 走 multipart。
- 【源码】自定义工具 `format: binary` 参数 → FILE；【文档】Agent 场景下 FILE/FILES 参数被跳过，LLM 不能直接填文件参数。
- 【源码】响应转文件（`Response.is_file`）：先看 Content-Disposition 是否 attachment / filename；`text/*`（csv 除外）非文件；`application/{json,xml,javascript,x-www-form-urlencoded,yaml,graphql}` 或前 1024 字节可 UTF-8 解码且含 `{ [ <` 者非文件；主类型属 application / image / audio / video 视为文件。大小闸门 `HTTP_REQUEST_NODE_MAX_BINARY_SIZE`（默认 10MB）、`HTTP_REQUEST_NODE_MAX_TEXT_SIZE`（默认 1MB）。

**版本化与迁移**

- 【源码】`CURRENT_APP_DSL_VERSION = "0.7.0"`（`api/constants/dsl_version.py`）；导出结构 `version` / `kind: app` / `app` / `workflow`（或 `model_config`）/ `dependencies`。导入读 `data.get("version", "0.1.0")`，`kind` 非 `app` 直接改写为 `app`。
- 【源码】`check_version_compatibility`（`api/services/dsl_version.py`）：`imported > current` → `PENDING`；`imported.major < current.major` → `PENDING`；`imported.minor < current.minor` → `COMPLETED_WITH_WARNINGS`；否则 `COMPLETED`；解析失败 `FAILED`。`PENDING` 时 YAML 暂存 Redis `app_import_info:{import_id}` 10 分钟，需 `confirm_import` 才建 app；返回体含 `imported_dsl_version` / `current_dsl_version` / `warnings`。
- 【推断】无自动迁移脚本，「迁移」只是放行 + 警告；【文档】官方文案称平台总是运行最新 DSL 版本，导入文件总是兼容。
- 【源码】自定义工具 provider 不在 DSL 中导出：api 类型 tool 节点只保留 `provider_id`（UUID）/ `provider_type: api` / `tool_name`，schema 与凭证存在 `ApiToolProvider` 表（`credentials_str` 加密），`api_tools_manage_service.py` 无导出方法；`include_secret=False` 时导出剥离 `credential_id`，Secret 环境变量值置空。
- 【推断】跨工作区导入后自定义工具需手工重建，且 `provider_id` 会变。

来源：
- https://github.com/langgenius/graphon/blob/main/src/graphon/nodes/http_request/executor.py
- https://github.com/langgenius/graphon/blob/main/src/graphon/nodes/http_request/entities.py
- https://github.com/langgenius/graphon/blob/main/src/graphon/nodes/http_request/node.py
- https://github.com/langgenius/graphon/blob/main/src/graphon/variables/template_resolution.py
- https://github.com/langgenius/graphon/blob/main/src/graphon/variables/segments.py
- https://github.com/langgenius/graphon/blob/main/src/graphon/file/enums.py
- https://github.com/langgenius/dify/blob/main/api/tests/unit_tests/core/workflow/nodes/http_request/test_http_request_executor.py
- https://github.com/langgenius/dify/blob/main/api/core/tools/utils/parser.py
- https://github.com/langgenius/dify/blob/main/api/core/tools/custom_tool/tool.py
- https://github.com/langgenius/dify/blob/main/api/core/plugin/entities/parameters.py
- https://github.com/langgenius/dify/blob/main/api/core/tools/tool_engine.py
- https://github.com/langgenius/dify/blob/main/api/configs/feature/__init__.py
- https://github.com/langgenius/dify/blob/main/api/constants/dsl_version.py
- https://github.com/langgenius/dify/blob/main/api/services/dsl_version.py
- https://github.com/langgenius/dify/blob/main/api/services/app_dsl_service.py
- https://github.com/langgenius/dify/blob/main/api/services/tools/api_tools_manage_service.py
- https://github.com/langgenius/dify/blob/main/api/tests/fixtures/workflow/http_request_with_json_tool_workflow.yml
- https://docs.dify.ai/en/cloud/use-dify/nodes/http-request
- https://docs.dify.ai/en/cloud/use-dify/nodes/list-operator
- https://docs.dify.ai/en/cloud/use-dify/nodes/code
- https://docs.dify.ai/en/cloud/use-dify/workspace/tools
- https://docs.dify.ai/en/cloud/use-dify/workspace/app-management
- https://docs.dify.ai/en/self-host/deploy/configuration/environments

### 2.3 Postman 变量、脚本与 Collection 格式

**占位符与类型**

- 【文档】用 `{{base_url}}` 在请求中引用变量；运行时「变量中存储的值会被填到引用处」。五个作用域由宽到窄：global、collection、environment、data、local；同名时最窄作用域胜出。
- 【文档】「Postman 将变量存储为字符串」；复杂类型「存之前 `JSON.stringify()`，取出时 `JSON.parse()`」。
- 【文档】动态变量 `{{$guid}}`、`{{$timestamp}}`、`{{$randomInt}}` 在请求运行时生成，用法与普通变量相同。
- 【文档】Collection 格式 v2.1 的 `variable` 项有 `type` 字段，枚举 `string | boolean | any | number`；URL 路径变量语法 `/path/:variableName/to/somewhere`。
- 【推断】替换是纯文本级：JSON body 里写 `"n": {{count}}`（不加引号）替换后是数字字面量，写 `"n": "{{count}}"` 则是字符串——与 Dify 的 JSON body 语义相同；官方 issue 追踪器（postmanlabs/postman-app-support #1468、#12686）里的报错场景都是这一文本替换语义的直接后果。

**响应提取**

- 【文档】`pm.response.json()`「获取响应 JSON 对象」，`pm.response.text()`「获取响应文本」，`pm.response.headers` 是响应头列表，`pm.response.code` 为数字类型状态码；这些只在 Post-response 脚本可用。
- 【文档】请求串联靠脚本把响应值写回变量（`pm.environment.set` / `pm.collectionVariables.set`），再由后续请求的 `{{var}}` 读取。
- 【推断】Postman 没有声明式的「响应路径 → 变量」映射；提取与断言全部是 JavaScript。

**素材编码**

- 【文档】form-data 字段「在键名旁的下拉里选 File，再选择要发送的文件」；binary 模式「选 binary，再选文件」。「Postman 把文件路径保存在请求里，路径相对于本地工作目录」；与团队成员分享或在 monitor 中使用时，文件需重新上传——文件本身不存于 collection。
- 【文档】v2.1 schema 中 body `file` 模式的 `src`「包含要上传的文件名，不是路径」，`src` 为 null 表示未选择文件。
- 【推断】Postman 的分享文件明确把「素材」排除在外，只留引用；base64 编码是用户在 pre-request 脚本里自己做的事，格式层没有编码声明。

**版本化与迁移**

- 【文档】schema.postman.com 列出 v1.0.0、v2.0.0、v2.1.0 三个 JSON 版本，均标为 Stable；`info.schema`「理想情况下应指向用于校验该 collection 的 Postman schema 链接」（如 `https://schema.getpostman.com/json/collection/v2.1.0/collection.json`）。
- 【文档】「当前版本的 Postman 使用 schema 3.0.0」：3.0.0「把一个 collection 定义为磁盘上多个 YAML 文件，请求存为 `*.request.yaml`，附属资源放在保留的 `.resources/` 目录」，目标是让人、Agent 与自动化工具像对待源码一样 diff / review。「在 Postman v12 中，Newman 能运行导出为 2.1.0 格式的 collection，但不能运行 3.0 的；要运行 3.0 collection 请改用 Postman CLI。」
- 【文档】`postman collection migrate`「把现有 v2.1 collection 迁移到新的 v3 格式」；文档未提供反向迁移。
- 【推断】Postman 的版本策略是「文件级 schema URL 标版本 + 官方单向迁移工具」；大版本（2.1 → 3.0）连载体（单 JSON → 多 YAML）都换，靠工具链而非运行时兼容层过渡。

来源：
- https://learning.postman.com/docs/sending-requests/variables/variables/
- https://learning.postman.com/docs/tests-and-scripts/write-scripts/variables-list/
- https://learning.postman.com/docs/tests-and-scripts/write-scripts/postman-sandbox-reference/pm-response/
- https://learning.postman.com/docs/sending-requests/create-requests/parameters/
- https://learning.postman.com/docs/use/use-collections/collections-schemas/
- https://learning.postman.com/docs/postman-cli/postman-cli-collections
- https://schema.postman.com/
- https://schema.postman.com/collection/json/v2.1.0/draft-07/collection.json
- https://github.com/postmanlabs/postman-app-support/issues/1468 、 https://github.com/postmanlabs/postman-app-support/issues/12686（官方 issue 追踪器，仅作文本替换语义的佐证）

### 2.4 LiteLLM 自定义 provider 与 `config.yaml`

**占位符与类型**

- 【文档】`model_list` 每项含 `model_name`（「对外暴露给客户端的名字」）与 `litellm_params`（「`litellm.completion()` 接受的所有参数」，含真正的 `model`、`api_base`、`api_key`）。
- 【文档】密钥引用语法 `os.environ/<YOUR-ENV-VAR>`，「等价于运行 `os.getenv("YOUR-ENV-VAR")`」，如 `api_key: "os.environ/AZURE_API_KEY_EU"`。这是 `config.yaml` 中唯一的「占位符」，且只解析环境变量。
- 【文档】OpenAI 兼容端点：「在模型名前加 `openai/`」并设 `api_base`，「不要在 base url 后追加 `/v1/embedding` 之类」；报 Not Found 时检查 `api_base` 是否带 `/v1` 后缀。
- 【文档】自定义 provider（Custom API Server）：写 Python 类继承 `CustomLLM`，在 `litellm_settings.custom_provider_map` 里登记 `{provider: my-custom-llm, custom_handler: custom_handler.my_custom_llm}`，模型名写作 `my-custom-llm/my-model`；`completion` 必须返回 `ModelResponse`，未实现的方法默认抛 `CustomLLMError`。
- 【推断】LiteLLM 的「自定义」是代码扩展点而非声明式格式；`config.yaml` 只负责路由与参数，YAML 原生类型（number / bool）原样透传到 `litellm_params`，不存在模板层的类型问题。

**响应提取**

- 【文档】自定义 provider 的响应归一由 Python 完成（返回 `ModelResponse`）；【推断】配置文件层没有任何响应路径提取语法。
- 【文档】`model_info` 承载能力与定价声明：`supports_vision: True` 让 `/model_group/info` 正确上报能力；`mode`、`input_cost_per_token` / `output_cost_per_token`、`max_tokens`、`base_model` 等；定价默认由 `model_prices_and_context_window.json` 自动映射，可用 `LITELLM_LOCAL_MODEL_COST_MAP` 换本地副本或逐模型覆盖。

**素材编码**

- 【文档】视觉输入沿用 OpenAI 约定：`"image_url": str` 或 `{"url": "url OR base64 encoded str"}`；`format` 参数可显式声明 MIME（如 `image/jpeg`）供 LiteLLM 无法推断时使用；`litellm.supports_vision(model=...)` 查询能力。
- 【文档】图像生成 `response_format` 取 `url` 或 `b64_json`，响应 `data[]` 含 `url` / `b64_json` / `revised_prompt`。
- 【推断】素材编码没有独立声明位，全部继承 OpenAI 请求 / 响应 schema；对各家 provider 的转换是代码。

**版本化与迁移**

- 【推断】`config.yaml` 无 schema 版本字段（文档全文未出现）；顶层键为 `model_list`、`litellm_settings`、`general_settings`、`router_settings`。
- 【文档】`include:` 可引入一个或多个其他 YAML 文件，启动时合并。
- 【文档】`STORE_MODEL_IN_DB="True"`（或 `general_settings.store_model_in_db: true`）后「模型定义存在数据库而非静态 `config.yaml`」，可在 Admin UI / API 增改且无需重启；库中的 provider 密钥用 `LITELLM_SALT_KEY`（回退 `LITELLM_MASTER_KEY`）加密，重启需同一密钥。
- 【推断】LiteLLM 的「迁移」是数据库层（Prisma）的事；配置文件本身不迁移，靠字段宽容与文档说明向前兼容。

来源：
- https://docs.litellm.ai/docs/proxy/configs
- https://docs.litellm.ai/docs/proxy/config_management
- https://docs.litellm.ai/docs/providers/openai_compatible
- https://docs.litellm.ai/docs/providers/custom_llm_server
- https://docs.litellm.ai/docs/completion/vision
- https://docs.litellm.ai/docs/image_generation
- https://docs.litellm.ai/docs/proxy/model_management
- https://docs.litellm.ai/docs/proxy/ui_store_model_db_setting

### 2.5 new-api 与 one-api 渠道配置

**占位符与类型**

- 【源码】one-api `model/channel.go`：`type`（整数枚举）、`key`（text）、`base_url`、`models`（逗号分隔字符串）、`model_mapping`（JSON 字符串）、`group`、`priority`、`weight`、`config`（JSON，`ChannelConfig` 含 `region` / `ak` / `sk` / `user_id` / `api_version` / `vertex_ai_project_id` 等）、`system_prompt`；`other` 已标 DEPRECATED。
- 【文档】one-api README：模型重定向「如无必要请不要设置，设置之后会导致请求体被重新构造而非直接透传」；支持批量创建渠道，密钥多 key 换行分隔。
- 【源码】new-api `model/channel.go` 在此之上新增 `status_code_mapping`、`tag`、`setting`（渠道额外设置）、`param_override`、`header_override`、`remark`、`channel_info`（多 key 模式：`is_multi_key` / `multi_key_size` / `multi_key_mode` 等）、`settings`（如 azure 版本）。
- 【文档】new-api 参数覆盖两种模式。简单覆盖模式「向前兼容性，直接指定要覆盖的字段和值，系统会将这些字段合并到原始请求中」，如 `{"temperature": 0.8, "max_tokens": 2000, "model": "gpt-4"}`；高级操作模式 `{"operations": [{"path": "temperature", "mode": "set", "value": 0.8, "conditions": [...], "logic": "AND"}]}`。「路径语法：`temperature`、`messages.0.content`、`messages.-1.content`、`metadata.user.name`」；「`keep_origin`: 为 true 时，如果目标路径已存在值则跳过设置」；条件匹配模式 `full` / `prefix` / `suffix` / `contains` / `gt` / `gte` / `lt` / `lte`。
- 【源码】`relay/common/override.go`：`ParamOperation{Path, Mode, Value interface{}, KeepOrigin, From, To, Conditions, Logic}`，`Mode` 注释列出 `delete, set, move, copy, prepend, append, trim_prefix, trim_suffix, ensure_prefix, ensure_suffix, trim_space, to_lower, to_upper, replace, regex_replace, return_error, prune_objects, set_header, delete_header, copy_header, move_header, pass_headers, sync_fields`；`ConditionOperation{Path, Mode, Value, Invert, PassMissingKey}`；实现基于 `gjson` / `sjson`，负索引经 `negativeIndexRegexp` 处理。
- 【推断】两家都没有占位符 / 表达式语法：全部是静态 JSON 字典。`param_override` 的 `value` 是 JSON 原生类型、按点路径写入请求体，因此天然保留 number / bool；它是「改写请求」的算子集，不是模板。

**响应提取**

- 【推断】渠道层没有响应路径提取；每种上游由 Go adaptor（`relay/channel/<provider>/adaptor.go`）做请求 / 响应转换。
- 【源码】new-api 任务类接口：`router/video-router.go` 注册 `POST /v1/video/generations` → `RelayTask`，`GET /v1/video/generations/:task_id` → `RelayTaskFetch`，另有 `POST /v1/videos` 与 `GET /v1/videos/:task_id`（OpenAI 风格）及 Kling / Jimeng 兼容路由。`model/task.go` 的 `TaskStatus` 枚举 `NOT_START` / `SUBMITTED` / `QUEUED` / `IN_PROGRESS` / `FAILURE` / `SUCCESS` / `UNKNOWN`，`ToVideoStatus()` 把 `QUEUED`+`SUBMITTED` → `dto.VideoStatusQueued`、`IN_PROGRESS` → `VideoStatusInProgress`、`SUCCESS` → `VideoStatusCompleted`、`FAILURE` → `VideoStatusFailed`，其余 → `VideoStatusUnknown`。`Task` 行保存 `task_id`（第三方 id）、`platform`、`action`、`status`、`progress`、`fail_reason`、`data`（原始 JSON）与 `private_data.upstream_task_id` / `result_url`。
- 【推断】这与 ArcReel `_PROVIDER_STATUS_SYNONYMS` 的做法同构：上游状态串 → 少数几个 canonical 档位，是**字典映射**而不是表达式。

**素材编码**

- 【文档】new-api 视频接口 `POST /v1/video/generations` 必填 `model`、`prompt`，可选 `image`（「URL/Base64」）、`duration`、`size`、`fps`、`seed`、`n`、`response_format`、`user`、`metadata`；成功响应 201 返回 `task_id`。
- 【推断】渠道配置里没有素材编码声明；素材格式完全由 API 协议（OpenAI 兼容体系）规定，网关只转发或在 adaptor 里做代码级转换。

**版本化与迁移**

- 【源码】两家都是 GORM 模型 + `AutoMigrate`（one-api `model/main.go` `migrateDB()` 逐表 `DB.AutoMigrate(&Channel{})`；new-api 同名函数额外做列类型迁移）；【文档】one-api README「无需手动建表，程序将自动建表」。
- 【推断】渠道配置没有 schema 版本字段，`config` / `setting` / `param_override` 都是自由 JSON 文本列；兼容性靠「新字段可空 + 旧字段标 DEPRECATED」。
- 【源码】new-api `controller/channel.go` 仅有 `DeleteChannelBatch` / `BatchUpdateChannelStatus` / `BatchSetChannelTag` 等批量操作，前端 `channel-actions.ts` 也没有导出 / 导入函数；【推断】两家都没有「渠道定义文件」这一分享载体，渠道复制在同一实例内完成。

来源：
- https://github.com/songquanpeng/one-api/blob/main/README.md
- https://github.com/songquanpeng/one-api/blob/main/model/channel.go
- https://github.com/songquanpeng/one-api/blob/main/model/main.go
- https://github.com/QuantumNous/new-api/blob/main/model/channel.go
- https://github.com/QuantumNous/new-api/blob/main/model/task.go
- https://github.com/QuantumNous/new-api/blob/main/model/main.go
- https://github.com/QuantumNous/new-api/blob/main/relay/common/override.go
- https://github.com/QuantumNous/new-api/blob/main/router/video-router.go
- https://github.com/QuantumNous/new-api/blob/main/controller/channel.go
- https://github.com/QuantumNous/new-api/blob/main/web/src/features/channels/lib/channel-actions.ts
- https://github.com/QuantumNous/new-api-docs-v1/blob/main/content/docs/zh/guide/console/channel-management.mdx
- https://github.com/QuantumNous/new-api-docs/blob/main/docs/api/generate-video.md

---

## 3. 横向观察

1. **占位符语法只有一种主流：双花括号 + 点路径。** n8n、Dify、Postman 三家都用 `{{ }}`，差异只在内部是 JS 表达式（n8n）、`#` 包裹的 selector（Dify）还是纯变量名（Postman）。LiteLLM 与 new-api 根本没有模板层。
2. **类型保留有两种既有语义，用户都见过。** (a) n8n：表达式求值得原生类型，整段是表达式则字段就是该类型；(b) Dify / Postman：文本替换后整体再 JSON 解析，作者靠「加不加引号」控制类型。两种语义在「整串单占位符 → 原生类型」这一点上结果一致；分歧只在混合文本与转义（Dify 不转义字符串内的引号，靠 `json_repair` 兜底）。
3. **没有一家提供声明式的响应路径提取。** n8n 把提取推到下游表达式（`$json.x`、`$jmespath`），Dify 需 Code 节点，Postman 全靠脚本，LiteLLM / new-api 全靠代码。n8n 的 `$response.body / headers / statusCode` 三分和 new-api 的 `TaskStatus` 归一化枚举，是仅有的两处「响应侧」声明式抽象。
4. **素材编码从不写在模板里，而是写在「值的类型」上。** n8n 的 `binary` 对象（`data` + `mimeType` + `fileName`）、Dify 的 `file` 变量（`transfer_method` + `mime_type`）、OpenAI 体系的 `image_url` 双轨（URL 或 data URI）。分享文件里一律不内嵌素材（Postman 只存路径、Dify 不导出凭证与工具）。
5. **版本化有三种粒度。** 节点级 `typeVersion` 冻结（n8n，旧文件永不改写、实现背历史分支）；文件级 semver + 导入三档策略（Dify，`PENDING` / `COMPLETED_WITH_WARNINGS` / `COMPLETED`，无迁移脚本）；文件级 schema URL + 官方单向迁移工具（Postman 2.1 → 3.0）。LiteLLM 与 new-api 无版本字段，靠字段宽容与数据库 `AutoMigrate`。
6. **凭证一律与定义分离。** n8n 导出只带凭证名与 ID，Dify 剥离 `credential_id`，LiteLLM 用 `os.environ/` 间接引用，new-api 密钥在渠道行里但没有导出格式。

---

## 4. 对 ArcReel 模板语言的借鉴建议

限三条，均针对 #2119 已定的方向（JSON 结构模板 + `{{ }}` 占位符 + 独立 `inputs` + JSONPath 优先级数组 + `schema_version`）：

1. **占位符沿用 `{{ path.to.value }}`，类型规则明写成 n8n 语义，不写成 Dify / Postman 的「加不加引号」语义。** 三家用户都认识双花括号，无需再发明 `{{#…#}}` 或 `=` 前缀；但「整串单占位符保留原生类型、混合文本串化」应作为**模板层规则**成文，让作者永远写 `"n": "{{ n }}"` 这一种形式即可得到数字——这样既避开 Dify 那类「字符串内引号不转义、靠 `json_repair` 兜底」的坑，也避开 Postman 那类「不加引号才是数字、JSON 编辑器报错」的坑。不引入 JS 表达式与方法调用，类型转换留给 `inputs` 声明。
2. **响应侧不追随任何一家的方言，因为没有方言可追随；对齐的对象是「响应对象模型」与「状态字典」。** 提取路径用 RFC 9535 标准 JSONPath（`$.data.task_id`）的优先级数组；把响应固定为 n8n 式的 `body` / `headers` / `status_code` 三分对象供路径引用（分页、错误信息经常在 headers 或状态码里）；状态归一化沿用 new-api `TaskStatus` 式的**纯字典**（上游字面量 → `queued` / `running` / `succeeded` / `failed` / `expired`），并把 ArcReel 既有 `_PROVIDER_STATUS_SYNONYMS` 作为默认表让作者只写增量。试跑器的「离线校验：粘贴真实响应验证取值路径」对标的是 Postman 用户「拿响应写断言」的心智，是这一空白地带里最容易被理解的形式。
3. **版本化取 Dify 的文件级 semver + 三档导入策略，素材取「值类型自带编码」而非模板内标注，且分享文件永不内嵌素材与凭证。** `schema_version` 用 semver，导入时「更高版本或 major 更低 → 需确认、minor 更低 → 警告放行、同版 → 直接」，不走 n8n 的节点级 `typeVersion`（ArcReel 的定义是单文件、单实现，没有理由背历史分支），也不预设 Postman 式的单向迁移工具。`inputs` 里的素材声明参照 n8n `binary` 三元组与 OpenAI `image_url` 双轨：每个素材输入声明 `encoding`（`data_uri` / `base64` / `url`，首期前两种）与可选 `mime_type`，模板中只引用 `{{ inputs.image }}`；分享文件与 Postman、Dify 一致，只带引用不带内容，凭证守 ADR 0008 的 `api_key + base_url` 单独存放、`base_url` 仅作提示随文件分发。
