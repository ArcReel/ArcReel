# 声明式定义格式复刻 8 家 HTTP 式内置 video backend：覆盖验收

**日期**：2026-08-26
**议题**：#2142（Part of #2119）；格式约定以 #2123 评论区最新修订为准
**分支**：`research/declarative-builtin-coverage`（基于 `prototype/declarative-endpoint-format` @ `24c264475`）
**范围**：只回答「格式对内置协议的表达力」，产出字段清单与可整体表达的名单；不下「是否收编内置」的结论

---

## 0. 结论速览

判定口径：一份声明式定义对应一个模型行（与 `lib/custom_provider` 的端点粒度一致），对该模型行会遇到的全部请求形状（文生 / 首帧 / 首尾帧 / 参考图 / 参考音频）与全部轮询结局，渲染出的请求与内置 backend 实际发出的请求逐字节一致、解析结果一致，才算「全链路可表达」。差异只落在格式缺一个构造的记「需加字段」；差异源于格式设计边界（签名鉴权）的记「不可表达」。

| 家 | 判定 | 需加字段（编号见 §3） | 不可表达 / 备注 |
|---|---|---|---|
| **agnes** | 需加字段 | F1b 空 `$each` 删键、F2 按素材切换 body 形状（keyframes）、F3 派生 width/height、F4 二次取件（`/agnesapi?video_id=`）、F8 URL 形态校验 | 鉴权、轮询、状态、错误、`seconds` 用量均可表达 |
| **ark**（按 1.x / 2.0 / 2.5 三族拆） | 需加字段 | F1a 空占位符级联删除条目、F5 `service_tier` 变量（仅 1.x） | HTTP 路径按 SDK 源码确认；首尾帧都在场的请求已逐字节一致 |
| **dashscope** | happyhorse 全系与 wan2.7 t2v / i2v / happyhorse r2v **全链路可表达**（3 份定义，6 个 case 全部一致，含 `X-DashScope-Async` 头、HTTP 200 业务码提交失败、`UNKNOWN` 过期）；wan2.7-r2v、wan3.0 需加字段 | wan3.0：F1a、F2（`ratio` 仅无首帧时下发）；wan2.7-r2v：F7 | F7 音频按 targets 配对到参考项，建议按不可表达处理 |
| **kling** | JWT 模式**不可表达**；bearer 模式按子路径各自可表达，需加字段 | F2 按素材选子路径（text2video / image2video / multi-image2video）、F5 `mode` 由 service_tier 派生、F6 轮询 HTTP 200 + `code≠0` 判失败 | JWT：HS256 签 `{iss, exp, nbf}`、距过期 <60s 重签，格式 auth 节只有 header / query |
| **minimax** | v1（Hailuo / S2V-01）需加字段；H3（v2）需加字段 | v1：F4 二次取件（`file_id` → `/files/retrieve`）、F6 轮询 `base_resp.status_code≠0` 判失败；H3：F1a | S2V-01 的 `subject_reference` 形状可表达 |
| **newapi** | 需加字段（仅 1 项） | F3 派生 width/height | 扁平 / `{code,data}` 包装两种回包、`metadata` 用量、expired 折 failed 均一致 |
| **v2_video_generations** | 需加字段（仅 1 项，属语义调整） | F1b `$each` 展开为空时删除 `image_urls` 键 | 参考图在场的请求已逐字节一致；6 条视频路径、6 条 task_id 路径、5 条状态路径全部可写进优先级数组 |
| **vidu** | 四个端点各自**全链路可表达**（5 个 case 全部一致）；跨端点路由需加字段 | F2 按素材选端点（/text2video、/img2video、/start-end2video、/reference2video） | `Authorization: Token` 可表达；duration 就近校正与 resolution 白名单回落归模型行 |

**可整体表达的名单**见 §4；**需补字段清单**见 §3。

---

## 1. 方法与产物

### 1.1 做法

1. 逐家读内置 backend 的请求构造、轮询、状态判定、产物 / 用量提取（`lib/video_backends/{agnes,ark,dashscope,kling,minimax,newapi,v2_video_generations,vidu}.py` 与各自的 `lib/*_shared.py`），以及 `tests/unit/lib/video_backends/` 与 `tests/integration/lib/video_backends/` 下各家夹具里记录的请求 / 响应形状。
2. 在原型目录 `lib/custom_provider/prototype_declarative_endpoint/templates/builtin/` 为每家写声明式定义（22 份，见 §1.2）。
3. 写 `builtin_probe.py`：用 respx 在 transport 层拦截真实 httpx 流量，驱动 8 家内置 backend 对同一组参数与素材走一遍 submit → poll → 下载，记录**实际序列化后**的请求（URL / 鉴权头 / JSON body）、喂给它的响应夹具、最终结果或异常，落 `builtin_actual.json`（57 个 case）。Ark 走 volcenginesdkarkruntime SDK，记录 `tasks.create` 的 kwargs 与 `tasks.get` 调用，URL 按 SDK 源码 `resources/content_generation/tasks.py`（`/contents/generations/tasks`、`/contents/generations/tasks/{id}`）拼出。
4. 写 `compare_builtin.mjs`：对每个 case 用 `declarative_endpoint.js` 的 `validateDefinition` → `buildContext` → `renderRequest` 渲染 submit，与内置请求逐字段 diff；再对同一组响应夹具跑 `reduce`（SUBMIT → POLL…），把声明式的状态映射 / video_url / error / usage 与内置的下载 URL 或异常比对。结果写 `builtin_compare.json` 并打印报告。

跑法（仓库根目录）：

```bash
uv run python lib/custom_provider/prototype_declarative_endpoint/validate.py       # 22 份内置定义 + 3 份起步模板全部过 schema
uv run python lib/custom_provider/prototype_declarative_endpoint/builtin_probe.py  # → builtin_actual.json
node lib/custom_provider/prototype_declarative_endpoint/compare_builtin.mjs        # → builtin_compare.json，末行 PASS 19 / GAP 37 / N/A 1
node lib/custom_provider/prototype_declarative_endpoint/smoke.mjs                  # 原有冒烟仍全绿
```

本票对原型模块的改动（原型是 throwaway，按 #2142 允许为验收补最小改动）：

- `validate.py`：模板 glob 扩到 `templates/builtin/*.json`。
- 新增 `builtin_probe.py`、`compare_builtin.mjs`、`builtin_actual.json`、`builtin_compare.json`、`templates/builtin/`。
- `schema.json` / `declarative_endpoint.js` / 起步模板未改。

夹具来源说明：各家单测里的响应体是仓库对官方文档形状的记录（如 `tests/unit/lib/video_backends/test_agnes_video_backend.py:62-74` 的完成态、`test_dashscope_video_backend.py:51-59` 的 `output.task_status` 形状、`tests/integration/lib/video_backends/test_kling_video_backend.py:52-60` 的 `code/data` 信封），本票把它们原样搬进 probe 作为轮询夹具；素材字节用 PNG 魔数与 RIFF/WAVE 头，让两侧的 data URI MIME 一致。

### 1.2 定义清单

| 文件 | 复刻对象 |
|---|---|
| `agnes.json` / `agnes-keyframes.json` | agnes.py 的文生 / 首帧 / 参考图形状；首尾帧 keyframes 形状单独一份 |
| `ark-seedance-1x.json` / `ark-seedance-2-0.json` / `ark-seedance-2-5.json` | ark.py 按 `video_capabilities_for_model` 的三个族群；1.x 带 `service_tier`，2.x 带参考音频条目 |
| `dashscope-t2v.json` / `dashscope-i2v.json` / `dashscope-happyhorse-r2v.json` / `dashscope-wan27-r2v.json` / `dashscope-wan3.json` | dashscope.py 的 `_MODEL_PROFILES` 五种请求形态 |
| `kling-text2video.json` / `kling-image2video.json` / `kling-multi-image2video.json` | kling.py bearer 模式的三个子路径 |
| `minimax-hailuo-v1.json` / `minimax-s2v-01.json` / `minimax-h3-v2.json` | minimax.py 的 v1 两种请求体与 v2 多模态 |
| `newapi-builtin.json` | newapi.py（与起步模板 `newapi-video.json` 不同：内置发 width / height / n，不发 size / metadata） |
| `v2-video-generations.json` | v2_video_generations.py |
| `vidu-text2video.json` / `vidu-img2video.json` / `vidu-start-end2video.json` / `vidu-reference2video.json` | vidu.py 的四个端点 |

---

## 2. 逐家对照

每家列：定义能表达的部分、渲染请求与内置请求的差异、提取结果与内置解析的差异、结论。行号指向 `lib/video_backends/*.py`。

### 2.1 agnes（9 case，0 PASS）

可表达：`Authorization: Bearer`；`POST {base}/videos`、`GET {base}/videos/{task_id}`；裸 base64 素材（`encoding: base64`）；`num_frames` 经 `enum_maps.duration` 把 1–18 秒映射到 8n+1（与 `_duration_to_num_frames` 逐值核对，agnes.py:151-156）；`frame_rate: 24`、`seed` 空删；状态 `queued / in_progress / completed / failed / error / cancelled` 全在内置同义词表；错误 `$.error.message` → `$.error.code` → `$.error`；用量 `$.seconds`（accept scalar）。

请求差异：
- `height` / `width` 缺失（所有 case）。内置由 `aspect_size(aspect_ratio, resolution_to_short_edge(resolution), round_to=8, max_long_edge=1920)` 算出（agnes.py:159-164），格式无派生尺寸 → **F3**。
- 无参考图时多出 `extra_body: {"image": []}`（t2v / i2v case）。`$each` 展开为空仍留空数组，内置不写 `extra_body`（agnes.py:352-367）→ **F1b**。
- 首尾帧形状（`extra_body.image=[首,尾]` + `mode=keyframes`，顶层不放 `image`）与首帧形状互斥，一份定义只能写一种；`agnes-keyframes.json` 单独复刻后 body 一致（除 width/height）→ **F2**。

解析差异：
- 完成态只带 `video_id` 时，内置向网关根（去掉 `/v1`）的 `GET /agnesapi?video_id=` 二次查询（agnes.py:452-487）；声明式在 `video_url` 无命中处判失败 → **F4**（同 #2121 ③）。
- 旧网关把成片 URL 回填在 `remixed_from_video_id`：内置只在值为 http(s) URL 形态时采用（agnes.py:116-148）；格式的 `accept: string` 无法区分 URL 与 remix 来源 ID，本定义未列该路径，legacy case 判失败 → **F8**。
- `failed` / `cancelled` / `completed` 无 URL 三个失败 case 结局一致。

### 2.2 ark（3 族 9 case，2 PASS）

可表达：`POST {base}/contents/generations/tasks`、`GET .../tasks/{id}`（SDK 路径）；`content[]` 的 text / 首帧 / 尾帧 / `$each` 参考图 / `$each` 参考音频条目，role 字面量一致；`ratio`、`duration`、`generate_audio`（原生布尔）、`watermark: false`、`resolution`（空删）、`seed`（空删）；状态 `queued / running / succeeded / failed / expired / cancelled`；`$.content.video_url`；`$.error.message`；用量 `$.usage.completion_tokens` 与 `$.seed`（accept scalar）。首尾帧都在场的 `1x-i2v-last`、`2-0-i2v-last` 两个 case 请求逐字节一致。

请求差异：
- 首帧或尾帧缺席时，定义里对应条目变成 `{"type":"image_url","image_url":{},"role":"first_frame"}` 这样的空壳条目（t2v / r2v / 单首帧 case）。原型 `renderNode` 对象分支只删值为空的键、不删所在对象（declarative_endpoint.js:369-388），内置则不 append 该条目（ark.py:300-329）→ **F1a**。
- 1.x 的 `service_tier` 内置取 `request.service_tier`（ark.py:384-385），格式 `BASE_VARS` 无该变量（declarative_endpoint.js:469），本定义写死 `default`，flex case 差异 → **F5**。
- 2.5 首帧在场时 `ratio=adaptive` 由 `capabilities.first_frame_ratio_adaptive_only` 交编排层改写，probe 已模拟为入参，请求一致。

解析差异：
- 结局一致（succeeded / failed / expired）。内置把 `expired` 与 `failed` 同判失败（ark.py:433-437），resume 路径另按 `_is_ark_not_found` 转 `ResumeExpiredError`（ark.py:469-481）；声明式折进 failed，属 #2123 已拍板的口径，非本票缺口。
- 内置下载对 `400 video_not_ready` 单独重试（ark.py:400-416），是运行时策略，不在格式内。

### 2.3 dashscope（9 case，6 PASS）

可表达：`POST {base}/services/aigc/video-generation/video-synthesis` 带 `X-DashScope-Async: enable`（`submit.headers`，轮询不带，与 `dashscope_headers(async_mode=True)` 一致）；`input.prompt` / `input.media[]`（首帧、`$each` 参考图、`$each` 独立参考音频）；`parameters.resolution` 经 `enum_maps` 大写、`duration`、`watermark: false`、`seed` 空删、wan3 的 `audio` 开关；`GET {base}/tasks/{id}`；状态 `$.output.task_status` 的 `PENDING / RUNNING / SUCCEEDED / FAILED / CANCELED / UNKNOWN`（`unknown → failed` 显式登记）；`$.output.video_url`；错误 `$.output.message` → `$.output.code` → `$.message` → `$.code`；用量 `$.usage.duration`。提交阶段 HTTP 200 + `{code, message}` 无 `output.task_id` → 声明式「task_id 无命中 ⇒ 提交失败」并取到 `Invalid API-key provided.`，与 `extract_task_id` 一致。

happyhorse-1.1 t2v / i2v / r2v、wan2.7 t2v 的 6 个 case（含 FAILED、UNKNOWN、提交业务码）请求与结局全部一致。

请求差异：
- wan2.7-r2v：参考音频按 `reference_audio_targets` 逐段挂到 `media[i].reference_voice`（dashscope.py:654-705），需要「按下标把音频列表配到参考图列表」，`$each` 只能顺序展开单个列表；本定义不下发音频，`media[2].reference_voice` 缺失 → **F7**。
- wan3.0：`ratio` 只在无首帧时下发（dashscope.py:529-534）；格式无条件字段，本定义恒下发，带首帧 case 多出 `parameters.ratio` → **F2**（键级条件）。
- wan3.0：无首尾帧时 `media` 里留下 `{"type":"first_frame"}` / `{"type":"last_frame"}` 空壳条目 → **F1a**。

解析差异：无。`usage.duration` 内置做 half-up 取整与 24h 上限（`dashscope_shared.extract_billing_duration`），声明式只取原值，属 #2126 用量字段议题。

### 2.4 kling（6 case，4 PASS + 1 N/A）

JWT 模式：`KlingJWTManager` 用 `access_key` 作 `iss`、`secret_key` HS256 签 `{iss, exp: now+1800, nbf: now-5}`，每次取用距过期 <60s 重签（`lib/kling_shared.py:40-97`）。probe 记录到 `Authorization: Bearer <HS256 JWT>`，header `{"alg":"HS256","typ":"JWT"}`，payload 键 `iss, exp, nbf`。格式 auth 节只有 header / query 两种写法且 `{{ api_key }}` 是唯一凭证变量 → **不可表达**（与 #2142 预期一致）。

bearer 模式可表达：`POST {base}/videos/{子路径}`、`GET {base}/videos/{子路径}/{task_id}`；`model_name`、`prompt`、`duration` 经 `enum_maps` 转字符串（`"5"`）、`aspect_ratio`、裸 base64 的 `image` / `image_tail`、`image_list=[{image}]`（`$each`）、`sound` 经 `enum_maps.generate_audio` 转 `on / off`；`$.data.task_id`；状态 `$.data.task_status` 的 `submitted / processing / succeed / failed`；`$.data.task_result.videos[0].url`；错误 `$.data.task_status_msg` → `$.message`。text2video / multi-image2video / failed 三个 case 一致。

请求差异：
- 子路径由素材在场决定（kling.py:350-403）：一份定义只能写一个子路径 → **F2**。
- `mode` 由 `resolution=4k` / `service_tier=pro` 派生（kling.py:299-309），本定义写死 `std`，pro case 差异 → **F5**。
- `sound` 只对有音频能力的 model 下发、multi-image2video 不下发：本定义按 kling-v3 族写，能力不同的 model 另写定义即可，不算缺口。

解析差异：
- 轮询响应 HTTP 200 + 顶层 `code≠0`（无 `data.task_status`）：内置 `kling_task_failure_reason` 立即判失败（`lib/kling_shared.py:209-221`）；声明式 status 无命中 → `running`，会一路轮到运行时超时 → **F6**。

### 2.5 minimax（9 case，3 PASS）

v1（Hailuo / S2V-01）可表达：`POST {base}/video_generation`、`GET {base}/query/video_generation?task_id=`；`model / prompt / duration / resolution`（`enum_maps` 大写）/ `first_frame_image`；S2V-01 的 `subject_reference=[{type: character, image: [参考图]}]`（`$each` 展开，上限交 `max_reference_images: 1`）；`$.task_id`；状态 `Preparing / Queueing / Processing / Success / Fail`；错误 `$.base_resp.status_msg`。三个成功 case 的请求逐字节一致。

v1 解析差异：
- 成功态只给 `file_id`，内置再 `GET /v1/files/retrieve?file_id=` 取 `file.download_url`（minimax.py:464-477、501-506）；声明式在 `video_url` 无命中处判失败（3 个 case）→ **F4**。
- 轮询响应 HTTP 200 + `base_resp.status_code≠0` 且无 `status`：内置 `_base_resp_error` 立即判失败（`lib/minimax_shared.py:186-197`）；声明式落 `running` → **F6**。
- `status=Fail` 的失败 case 一致（`status_msg` 取到）。

v2（H3）可表达：base 为 `{host}/v2`；`content[]` 的 text / 首帧 / 尾帧 / `$each` 参考图 / `$each` 参考音频；`resolution`（`768p → 768P`、`2k → 2K`）、`duration`、`ratio`；`GET {base}/query/video_generation/{task_id}`；状态 `$.task.status` 的 `queued / running / succeeded / failed / cancelled`；`$.task.content.url`；错误 `$.task.error` → `$.task.status_msg` → `$.base_resp.status_msg`。首尾帧都在场的 case 一致；失败 case 结局一致。

v2 请求差异：首尾帧缺席时留下空壳条目（t2v / r2v case）→ **F1a**。

### 2.6 newapi（5 case，0 PASS）

可表达：`POST {base}/video/generations`、`GET {base}/video/generations/{task_id}`；`model / prompt / duration / n: 1 / seed / image`（data URI）；状态 `$.status` → `$.data.status`；视频 `$.url` → `$.result_url` → `$.data.url` → `$.data.result_url`（与 `_VIDEO_URL_PATHS` 同序，newapi.py:60-66）；错误 `$.error.message` → … → `$.data.error`；用量 `$.metadata.duration` / `$.data.metadata.duration` 与 `seed`（accept scalar，wrapped case 取到 `8 / 4242`，flat case 取到 `5 / 0`）；`expired → failed`（内置 generate 路径同样抛错，resume 路径分流 `ResumeExpiredError`，newapi.py:208-215）。

请求差异：`width` / `height` 缺失（全部 case）。内置 `aspect_size(aspect_ratio, resolution_to_short_edge(resolution), round_to=8)`（newapi.py:77-82）→ **F3**。其余字段一致。

解析差异：无。

### 2.7 v2_video_generations（5 case，1 PASS）

可表达：`POST {root}/v2/video/generations`、`GET {root}/v2/video/generations?generation_id=`（query 写在 url 模板）；`model / prompt / duration / aspect_ratio / resolution / seed / image_url / last_image_url / image_urls`；task_id 六条路径（`accept: scalar` 覆盖整数 `id: 123`，与 `first_str_by_paths` 的 int 容忍一致）；状态五条路径；视频六条路径（含 `$.data.task_result.videos[0].url`）；错误路径含 `error.name`；`expired → failed` 与内置 `normalize_status` 同口径（v2_video_generations.py:96-103）。参考图在场的 r2v case 逐字节一致。

请求差异：无参考图时多出 `image_urls: []`（4 个 case）。内置只在列表非空时写键（v2_video_generations.py:153-163）→ **F1b**。

解析差异：无。

### 2.8 vidu（5 case，5 PASS）

可表达：`Authorization: Token {{ api_key }}`；`POST {base}/{端点}`、`GET {base}/tasks/{task_id}/creations`；`model / prompt / duration / resolution / seed / audio / aspect_ratio`（仅 text2video、reference2video 定义带）/ `images`（`[首]`、`[首, 尾]`、`$each` 参考图）；`$.task_id`；状态 `$.state` 的 `created / queueing / processing / success / failed`；`$.creations[0].url`；错误 `$.err_code`（accept scalar）；用量 `$.credits`。四个端点定义各自与内置请求逐字节一致，失败 case 一致。

请求差异：端点由素材在场决定（vidu.py:352-367），一份定义只能写一个端点 → **F2**。

差异但归模型行 / 运行时：`_coerce_duration` 把不在合法集合的时长就近校正（vidu.py:399-418），`_coerce_resolution` 按白名单回落 720p（vidu.py:421-436）——声明式路线下模型行的 `supported_durations` / `resolutions` 在付费前 fail-loud，是更严的口径而非缺口；`assert_vidu_body_size` 的 18MB 上限是运行时检查；提交响应里的 `credits` 作为轮询缺失时的兜底（vidu.py:240-264）在格式里没有「提交阶段取用量」的位置，属 #2126 用量议题。

---

## 3. 格式需补的字段清单

按影响面排序。「形态建议」只是让清单可落地，具体写法留给 Spec。

| 编号 | 缺口 | 涉及 | 形态建议 |
|---|---|---|---|
| **F1a** | 对象内占位符为空时删除**整个所在对象**（数组元素 / 键），而非只删值键 | ark（三族）、minimax H3、dashscope wan3：`content[] / media[]` 的首帧 / 尾帧条目 | 元素级守卫 `{"$when": "inputs.first_frame", ...}`；或定语义：对象内所有动态键都被空删时整体删除 |
| **F1b** | `$each` 展开为零项时删除所在数组 / 键 | agnes `extra_body.image`、v2 `image_urls` | 定语义：数组只含 `$each` 且展开为空 ⇒ DROP 所在键（纯语义调整，不加字段） |
| **F2** | 按素材在场分支：URL 子路径、body 形状、单个键 | kling 三子路径、vidu 四端点、agnes keyframes 形状、dashscope wan3 `ratio` 仅无首帧时下发 | `submit.variants[]`：`{"when": {"start_image": true, "end_image": false, "reference_images": false}, "url": ..., "body": ...}` 首个匹配生效；键级 `$when` 可复用 F1a 的守卫 |
| **F3** | 派生尺寸变量 `width` / `height` | agnes、newapi | 内置变量 `{{ width }}` / `{{ height }}` 由运行时按 `aspect_size(aspect_ratio, resolution_to_short_edge(resolution), round_to=8)` 算出；agnes 还需 `max_long_edge=1920`，可作 `derived.size` 的可选参数 |
| **F4** | 第三段取件请求（poll 成功后按中间 id 再 GET 一次取 URL） | agnes（`video_id` → `{host}/agnesapi?video_id=`，且 host 需去掉 `/v1`）、minimax v1（`file_id` → `/files/retrieve?file_id=`） | `poll.extract.asset_id` + `fetch: {method, url, headers?, extract: {video_url, error?}}`；agnes 的 host 级 base 另需 `{{ base_host }}` 或允许在 url 模板里写绝对路径 |
| **F5** | `service_tier` 变量 | ark 1.x（`service_tier` 字段）、kling（`mode` 由 tier / 4k 派生） | `BASE_VARS` 加 `service_tier`，允许 `enum_maps.service_tier`；kling 的 4k 优先规则需 `resolution` 与 `service_tier` 联合映射，或在模型行把 4k 档表达为独立 model |
| **F6** | 轮询阶段 HTTP 200 + 业务码判失败 | kling（顶层 `code≠0`）、minimax（`base_resp.status_code≠0`） | `poll.extract.failure`：优先级数组任一可接受命中 ⇒ failed，错误按 `error` 取；或 `status_map` 支持 `"*": "failed"` 兜底数字状态。#2121 已记提交阶段同构（5 家），轮询阶段是新增 |
| **F7** | 参考音频按 `reference_audio_targets` 配到指定参考项 | dashscope wan2.7-r2v `reference_voice` | 需要 `$each` 内按下标查另一列表；仅 1 家 1 型号，建议列为不可表达而非加字段 |
| **F8** | 提取值的 URL 形态校验 | agnes `remixed_from_video_id`（旧网关兼容） | `accept: url`；只服务旧网关，可弃 |
| **F9** | 音频 data URI 的 MIME 口径按供应商改写 | mp3：ark / minimax 写 `audio/mp3`（ark.py:54、minimax.py:113），dashscope 写 `audio/mpeg`（dashscope.py:84） | `inputs.<name>.mime_overrides: {".mp3": "audio/mp3"}`。probe 用 wav 未触发，出处为代码常量 |

不需要补字段、归运行时策略或模型行的差异（对照时已排除）：`resolution` 为空时的默认值（dashscope `720P`、minimax `768P`、vidu `720p`，归模型行默认）；vidu 时长就近校正与分辨率白名单回落（模型行 fail-loud）；`expired` 折进 failed 丢失 resume 分流（#2123 拍板）；ark 下载 `400 video_not_ready` 重试、submit 长超时、按档位的轮询间隔（运行时）；agnes `seconds` / dashscope `usage.duration` 的取整与上限、newapi `metadata.duration` 的 `int(float())`（#2126 用量议题）；kling 把子路径与有声标志编进 job_id、dashscope 持久化提交域名（续跑机制，在格式之外）；kling JWT 每次调用前续签（不可表达的一部分）。

---

## 4. 哪些家可整体表达

「整体」指该家现有模型行在当前格式下就能写出与内置逐字节一致的定义，不等待任何 F 项：

- **dashscope**：happyhorse-1.0 / 1.1 的 t2v、i2v、r2v，wan2.7 的 t2v、i2v（`dashscope-t2v.json`、`dashscope-i2v.json`、`dashscope-happyhorse-r2v.json` 三份定义，6/6 case 一致）。
- **vidu**：四个端点各自（4 份定义，5/5 case 一致）；一个模型行只走一条路径时可直接用。
- **kling bearer**：text2video、multi-image2video 子路径各自（2/2 case 一致）；image2video 差 F5（`mode`）。

补一项即整体：**v2_video_generations**（F1b）、**newapi**（F3）、**minimax H3**（F1a）、**ark 三族**（F1a；1.x 另需 F5 才覆盖 flex）。

补两项以上或含不可表达：**agnes**（F1b / F2 / F3 / F4 / F8）、**minimax v1**（F4 / F6）、**dashscope wan3.0**（F1a / F2）、**dashscope wan2.7-r2v**（F7，建议不表达）、**kling JWT 模式**（不可表达）；跨路由的 kling / vidu / agnes 统一定义都压在 F2 上。

按 F 项的杠杆看：F1a + F1b 解开 ark 三族、minimax H3、v2、agnes 与 dashscope wan3 的请求形状差异；F2 解开 kling / vidu / agnes 的路由与 wan3 的 `ratio`；F3 / F4 各只影响两家；F5 / F6 各影响两家但都是单字段。

---

## 5. 来源

- 格式约定：#2123 评论区（原型说明、评审修正、结论、Codex 评审修正、能力全显式声明修订）；`lib/custom_provider/prototype_declarative_endpoint/schema.json`、`declarative_endpoint.js`（`renderNode` 346-389、`BASE_VARS` 469、`mapStatus` 456-466、reducer 587-663）。
- 外部抽样对照：#2121 评论（`docs/research/video-protocol-survey-aggregators.md`）——本票在内置协议上复现了其 ①（JSON-in-string，内置无）、③（二次取件：agnes、minimax v1）、④（失败不体现在状态串：kling、minimax 轮询业务码）三类构造中的后两类，并新增了空值级联删除（F1）、按素材分支（F2）、派生尺寸（F3）、`service_tier`（F5）四类外部抽样未暴露的缺口。
- 内置实现：`lib/video_backends/agnes.py`、`ark.py`、`dashscope.py`、`kling.py`、`minimax.py`、`newapi.py`、`v2_video_generations.py`、`vidu.py`；`lib/agnes_shared.py`、`lib/dashscope_shared.py`、`lib/kling_shared.py`、`lib/kling_backend_base.py`、`lib/minimax_shared.py`、`lib/vidu_shared.py`、`lib/data_uri.py`。
- Ark HTTP 路径与响应字段：`.venv/lib/python3.12/site-packages/volcenginesdkarkruntime/resources/content_generation/tasks.py`、`types/content_generation/content_generation_task.py`（`status` 取值 running / failed / queued / succeeded / cancelled，`content.video_url`，`usage.completion_tokens`，`error.{code,message}`）。
- 夹具：`tests/unit/lib/video_backends/test_{agnes,newapi,dashscope,minimax,v2_video_generations,vidu}_video_backend.py`、`test_video_backend_ark.py`、`tests/integration/lib/video_backends/test_kling_video_backend.py`、`tests/unit/lib/test_kling_shared.py`。
- 官方文档索引：`docs/api-docs/providers/{agnes,ark,dashscope,kling,minimax,vidu}.md`。
