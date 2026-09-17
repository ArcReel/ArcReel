# OpenMontage 与 dramaclaw-gateway 的 ComfyUI 接入实现调研

**调研日期**：2026-09-17
**议题**：#2518（地图 #2516）
**用途**：为 ArcReel 自建原生 ComfyUI 通道的 Spec 提供一手参考实现素材

**信息来源与可信度声明**：

- 全部结论直接读两家仓库当前 `HEAD` 的源码得出，未经二手文章转述。引用格式为 `仓库标签 路径:行号`。
- 仓库标签：`OM` = [calesthio/OpenMontage](https://github.com/calesthio/OpenMontage)；`DG` = [dramaclaw/dramaclaw-gateway](https://github.com/dramaclaw/dramaclaw-gateway)；`DC` = [dramaclaw/dramaclaw](https://github.com/dramaclaw/dramaclaw)（主仓，只做渠道配置校验）。
- Comfy Cloud 托管 API 一节来自 Comfy 官方文档仓 `comfy-org/docs`（`development/cloud/api-reference.mdx`、`development/cloud/overview.mdx`、`snippets/cloud/complete-example.mdx`），用于对照自建 API 的差异，不在本图范围内。
- HBAI-Ltd/Toonflow-app 已在上一轮核实无 ComfyUI 集成，本次未再查。

---

## 0. 两家的定位差异（先行结论）

| 维度 | OpenMontage（Python 工具） | dramaclaw-gateway（Go 网关渠道） |
|---|---|---|
| 形态 | Agent 工具，进程内同步执行 | new-api 系 `TaskAdaptor`，异步任务 + 外部轮询 |
| workflow 来源 | 自带 4 套模板 + 用户自定义 | 全部由渠道配置提供，请求内不允许带 |
| 注入方式 | 硬编码节点 ID 的 `patch_workflow` | 自动推断 + 显式 `node_mappings` 覆盖 |
| 自定义 workflow | **完全不注入**，只要 `output_node` | 一律注入，推断不出就报配置错误 |
| 等待策略 | WebSocket 优先，失败降级轮询 | 纯 `/history` 轮询（由框架驱动） |
| 取消 | 不支持 | 支持，但仅限 `pending` 队列内 |
| 缺模型检测 | `/object_info` 预检 + 结构化下载指引 | 无，只在提交时吃 `node_errors` |
| 媒体类型 | 图 / 视频 / 音乐三个工具 | 仅视频一个渠道 |
| 动态改图 | 无 | 有（参考素材按数量重建子图） |

两家恰好互补：OpenMontage 的强项在**等待与恢复语义**和**缺模型的用户指引**，dramaclaw-gateway 的强项在**绑定推断**、**多 workflow 选路**和**参考素材的动态改图**。

---

## 1. dramaclaw-gateway：绑定与选路

### 1.1 `ComfyUINodeMappings` 完整字段清单

显式绑定 schema 全部是字符串，一个槽位拆成「节点 ID + input 名」两个字段（`DG relaykit/dto/channel_settings.go:134-155`）：

| 槽位 | 节点 ID 字段 | input 名字段 | 注入时的 input 名默认值 |
|---|---|---|---|
| 正向提示词 | `prompt_node_id` | `prompt_input` | `text`（`DG relay/channel/task/comfyui/adaptor.go:929`） |
| 负向提示词 | `negative_prompt_node_id` | `negative_prompt_input` | `text`（`adaptor.go:933`） |
| 首帧 / 图输入 | `image_node_id` | `image_input` | `image`（`adaptor.go:968`） |
| 尾帧 | `last_frame_node_id` | `last_frame_input` | `image`（`adaptor.go:980`） |
| 宽 | `width_node_id` | `width_input` | `width`（`adaptor.go:995`） |
| 高 | `height_node_id` | `height_input` | `height`（`adaptor.go:1000`） |
| 时长 | `duration_node_id` | `duration_input` | `duration`（`adaptor.go:1006`） |
| 帧数 | `frames_node_id` | `frames_input` | `frames`（`adaptor.go:1012`） |
| 帧率 | `fps_node_id` | `fps_input` | `fps`（`adaptor.go:1018`） |
| 种子 | `seed_node_id` | `seed_input` | `seed`（`adaptor.go:1024`） |

外层 `ComfyUISettings`（`DG relaykit/dto/channel_settings.go:98-106`）：

| 字段 | 类型 | 作用 |
|---|---|---|
| `workflow` | `any` | 兜底 workflow，无路由无模型映射时使用 |
| `workflow_by_model` | `map[string]any` | 按模型名取 workflow |
| `workflow_routes` | `[]ComfyUIWorkflowRoute` | 条件选路表，优先级最高 |
| `node_mappings` | `ComfyUINodeMappings` | 渠道级显式绑定 |
| `model_mappings` | `map[string]ComfyUINodeMappings` | 按模型名的绑定覆盖 |
| `client_id` | `string` | 提交 `/prompt` 时的 `client_id`，缺省 `relayclaw-comfyui`（`adaptor.go:136-139`） |
| `output_base_url` | `string` | 产物 URL 前缀（结构体已声明） |

路由项 `ComfyUIWorkflowRoute`（`channel_settings.go:108-115`）：`id`、`name`、`priority`、`match`、`workflow`、`node_mappings`。
匹配条件 `ComfyUIWorkflowMatch`（`channel_settings.go:117-127`）：`models`、`modes`、`resolutions`、`ratios`、`min_duration`、`max_duration`、`reference_images`、`reference_videos`、`reference_audios`，后三者是 `{min, max}` 指针区间（`channel_settings.go:129-132`）。

**workflow 选取优先级**（`adaptor.go:486-528`）：`workflow_routes` 命中项 > `workflow_by_model[model]` > `workflow`。三者全空报 `comfyui_configuration_error`。请求 metadata 里带 `workflow` 直接拒绝（`adaptor.go:487-489`），workflow 只能来自渠道配置。

**绑定合并顺序**（`adaptor.go:132`、`adaptor.go:799-807`）：`inferNodeMappings(workflow)` → `settings.node_mappings` → `settings.model_mappings[model]` → 命中路由的 `node_mappings`，后者覆盖前者。合并规则是「非空白字符串才覆盖」（`adaptor.go:1500-1517`），所以显式绑定填空串等于不覆盖，无法用显式配置把推断结果**清空**——这是个真实的表达力缺口。

### 1.2 `inferNodeMappings` 完整规则表

推断入口 `adaptor.go:809-834`，通用查找器 `findWorkflowInput` 在 `adaptor.go:903-920`。

**查找语义**：节点 ID 先按字符串升序排序（`adaptor.go:811-815`，注意是字典序，`"10"` 排在 `"2"` 前面）。然后**外层循环别名、内层循环节点**——即先用第一个别名扫完所有节点，再用第二个别名扫。命中条件是节点的 `inputs` 字典里**存在该键**（不看值的类型，连线值 `[nodeID, slot]` 也算命中）。`accept` 回调可额外过滤节点。

| 槽位 | 别名（按优先序） | `class_type` 过滤 | `_meta.title` 参与 | 多候选取舍 |
|---|---|---|---|---|
| 正向提示词 | `prompt` → `text` | 无 | 否 | 先按别名序，同别名下取节点 ID 字典序最小者 |
| 图输入 | `image` | 小写后包含 `loadimage`（`adaptor.go:817-819`） | 否 | 节点 ID 字典序最小的 LoadImage 类节点 |
| 宽 | `width` | 无 | 否 | 字典序最小 |
| 高 | `height` | 无 | 否 | 字典序最小 |
| 时长 | `duration` → `seconds`；都没有再走 Primitive 回退（`adaptor.go:822-825`） | 无（回退分支有） | 回退分支有 | 见下 |
| 帧数 | `frames` → `num_frames` → `length` | 无 | 否 | 字典序最小；命中 `length` 时可能被撤销（见下） |
| 帧率 | `fps` → `frame_rate` | 无 | 否 | 字典序最小 |
| 种子 | `seed` → `noise_seed` | 无 | 否 | 字典序最小 |
| 负向提示词 | **不推断** | — | — | 只能显式配置（`adaptor.go:932`） |
| 尾帧 | **不推断** | — | — | 只能显式配置，否则给了尾帧就报错（`adaptor.go:972-974`） |

**时长的 Primitive 回退**（`adaptor.go:836-864`），两趟：

1. 第一趟：`class_type` 忽略大小写等于 `PrimitiveFloat`，且 `_meta.title` 小写后**恰好等于** `float (duration)` 或 `duration`，且 `inputs` 含 `value` 键 → 取 `(nodeID, "value")`。这是全仓唯一一处用 `_meta.title` 做绑定推断的地方。
2. 第二趟：找 `class_type` 小写后包含 `minimaxh3` 或 `minimax_h3` 的节点（`adaptor.go:866-869`），取其 `inputs["length"]` 的连线源节点，然后沿 `values.a` → `a` → `duration` → `seconds` 四个 input 名递归上溯（`adaptor.go:875-901`），直到撞见带 `value` 的 `PrimitiveFloat` 节点。递归带 `visited` 集合防环。

两趟都按节点 ID 字典序遍历，先命中者胜。

**帧数的撤销规则**（`adaptor.go:827-830`）：若时长已推断出来、帧数命中的别名恰好是 `length`、且 workflow 用了 MiniMax H3 节点，则把帧数绑定清空。原因是 H3 的 `length` 语义是秒而非帧，同时注入时长和帧数会互相打架。

**推断失败的后果**：提示词非空但没推断出节点 → `comfyui_configuration_error`，提示「显式配置 `prompt_node_id`」（`adaptor.go:923-928`）；图输入同理（`adaptor.go:960-962`）。其余槽位（宽高时长帧率种子）推断不出就**静默跳过**，`setWorkflowInput` 遇到空节点 ID 直接返回 nil（`adaptor.go:1210-1215`）。

### 1.3 `workflow_routes` 的「打分」算法

严格说不是打分，是**全条件与过滤 + 优先级排序 + 并列即报错**（`adaptor.go:536-571`）：

1. 先把请求归一成 `comfyUIRouteRequest{model, mode, resolution, ratio, duration, images, videos, audios}`（`adaptor.go:662-687`）。
2. 逐条 `normalizeWorkflowRoute` 补默认（见下），再用 `workflowRouteMatches` 过滤（`adaptor.go:758-763`）：八个条件全部 AND。字符串类条件 `stringMatches`（`adaptor.go:773-783`）——**空列表视为通配**，非空则忽略大小写与首尾空白逐个比对。时长 `durationMatches`（`adaptor.go:765-771`）——**请求时长为 0 视为通配**，否则落在 `[min_duration, max_duration]` 内，边界值 0 表示该侧不限。计数 `countMatches`（`adaptor.go:785-787`）——`min`/`max` 为 nil 即该侧不限。
3. `sort.SliceStable` 按 `priority` **降序**（`adaptor.go:557`）。稳定排序保留配置顺序作为同优先级的次序。
4. **前两名 priority 相同直接报 `comfyui_configuration_error`**，错误 data 带上两个 route id（`adaptor.go:558-563`）。这是刻意不猜——配置歧义当成配置错误，而不是取第一条。
5. 命中项 `workflow` 为空 → 配置错误（`adaptor.go:564-569`）。
6. 一条都没命中 → `invalid_request` / HTTP 400，data 带 model 与 mode（`adaptor.go:551-556`）。

**请求模式归类**（`adaptor.go:662-687`）：
- 参考图数 = `req.Image` + `req.AdditionalReferenceImages()` + `metadata.reference_images` 去空后计数；有尾帧再 +1。
- 尾帧与参考视频 / 音频混用直接报错（`adaptor.go:669-671`）。
- 模式判定顺序：`duration_auto` 且比例为 `auto` 且有参考视频 → `video_edit`；否则有视频或音频 → `reference_to_video`；否则有图或尾帧 → `image_to_video`；否则 `text_to_video`。
- `normalizeWorkflowMode`（`adaptor.go:743-756`）另给出别名表：`t2v` / `i2v` / `image_reference` / `r2v` / `all_reference` 归一到四个标准值。

**路由缺省补全**（`adaptor.go:573-604`）：路由没写 `modes` 时，从 workflow 自身**反推模式**（`inferMiniMaxH3WorkflowMode`，`adaptor.go:612-641`）——有 `MiniMaxH3ReferenceToVideo` 类节点 → `reference_to_video`；有 H3 图生视频节点且有 `LoadImage` → `image_to_video`；只有 H3 图生视频节点 → `text_to_video`；都没有 → 报错「无法从 workflow 推断生成模式」。反推成功后再补默认时长区间 `[4, 15]` 和各模式的参考素材上限（t2v 全 0，i2v 图 ≤1，r2v 图 ≤9 / 视频 ≤3 / 音频 ≤3）。

这套缺省补全**强绑定 MiniMax H3**，对通用 workflow 不成立：一份 Wan 或 Flux 的 workflow 若不显式写 `modes`，会直接在选路阶段报错。

`req.Content` 里的多模态条目会先合并进 metadata（`adaptor.go:689-713`）：`image_url` 按 `role` 分流到参考图 / 尾帧 / 首帧，`video_url`、`audio_url` 追加到对应列表，全部按字符串去重（`adaptor.go:730-741`）。

### 1.4 `reference_media.go`：参考素材的动态改图

这是两家里唯一**修改 workflow 拓扑**（增删节点、重接连线）而非只改 input 值的地方。

**触发条件**（`DG relay/channel/task/comfyui/reference_media.go:118-136`）：workflow 里存在 `class_type` 忽略大小写等于 `MiniMaxH3ReferenceToVideo` 的节点。节点 ID 按字典序取第一个命中者作为目标节点。不命中则整个参考素材流程跳过，返回 `false`。

**上限与校验**（`reference_media.go:14-19`、`138-164`）：图 9、视频 3、音频 3；超限报 `invalid_request` / 400，错误 data 带 `field`、`count`、`limit`。另有一条业务约束：**只给音频不给图或视频直接拒绝**（`reference_media.go:157-162`）。

**改图步骤**（`reference_media.go:55-116`）：

1. 取目标节点 `inputs`，没有就建空字典（`reference_media.go:272-283`）。
2. 清掉旧的参考 input：键名前缀命中 `ref_images.ref_image_`、`ref_videos.ref_video_`、`ref_video_audios.ref_video_audio_`、`ref_audios.ref_audio_` 四者之一的全部删除，并记下它们连向的源节点（`reference_media.go:21-26`、`166-180`）。
3. 对每个旧源节点做**孤儿链回收**（`reference_media.go:202-215`）：若无人再引用且 `class_type` 属于 `LoadImage` / `LoadVideo` / `GetVideoComponents` / `LoadAudio` 四类加载器（`reference_media.go:263-270`），就删除，并递归回收它自己的上游依赖。
4. 再做一轮**全图不动点清理**（`reference_media.go:182-200`）：反复扫描，把所有无人引用的加载器类节点删掉，直到一轮下来没有变化。
5. 分配新节点 ID（`reference_media.go:301-324`）：取全图能解析成整数的最大 ID + 1 起步，逐个自增并跳过已占用的 ID。
6. 逐个上传素材并建节点：
   - 图 → 新建 `LoadImage{image: name}`，接 `ref_images.ref_image_{i} = [loadID, 0]`（`reference_media.go:81-89`）。
   - 视频 → 新建 `LoadVideo{file: name}` 加 `GetVideoComponents{video: [loadID, 0]}`，**一条视频接两个槽位**：`ref_videos.ref_video_{i} = [componentsID, 0]`（画面）与 `ref_video_audios.ref_video_audio_{i} = [componentsID, 1]`（伴音）（`reference_media.go:91-104`）。
   - 音频 → 新建 `LoadAudio{audio: name}`，接 `ref_audios.ref_audio_{i} = [loadID, 0]`（`reference_media.go:106-114`）。
   - 下标从 0 开始。上传失败的错误会带上「第几个参考图 / 视频 / 音频」（`reference_media.go:84`、`94`、`109`）。

**与首帧的互斥**（`adaptor.go:937-959`）：参考型 workflow 不接受顶层 `image` 作首帧，给了就报错；反过来，非参考型 workflow 若模式是 `image_to_video` 且没有顶层图，而参考图恰好只有一张，会把这张**提升为首帧**并清空参考图列表（`adaptor.go:943-949`）；非参考型 workflow 收到任何参考素材则报「workflow 没有兼容的参考素材输入」（`adaptor.go:954-959`）。

---

## 2. OpenMontage：客户端封装与注入原语

### 2.1 端点封装

`OM tools/_comfyui/client.py` 封装全部端点：`/system_stats` 健康检查（`client.py:98-104`）、`/object_info/{NodeClass}` 模型枚举（`client.py:110-148`）与节点存在性探测（`client.py:163-172`）、`/prompt` 提交（`client.py:178-197`）、`/history/{id}` 轮询（`client.py:224-235`）、`/view` 下载（`client.py:371-391`）、`/upload/image` 上传（`client.py:393-405`）、`/ws?clientId=` 事件流（`client.py:246-341`）。

服务地址支持**按能力分流**：`COMFYUI_{IMAGE|VIDEO|MUSIC}_SERVER_URL` 优先于共享的 `COMFYUI_SERVER_URL`，再兜底 `http://localhost:8188`（`client.py:45-70`）。这样图和视频可以打到不同 GPU 机器。

### 2.2 唯一的注入原语

`patch_workflow(workflow, patches)`（`client.py:487-502`）：深拷贝后按 `{node_id: {input_name: value}}` 逐个赋值；节点 ID 不存在就抛 `ComfyUIError` 并列出全部可用节点 ID。没有任何名字推断、没有类型检查、不改拓扑。

节点 ID 全部硬编码在调用侧：

| workflow | 硬编码的节点 ID → 注入项 | 出处 |
|---|---|---|
| `flux2-txt2img.json` | `4.text` 提示词、`5.guidance`、`6.{width,height,batch_size}`、`7.noise_seed`、`10.{steps,width,height}`、`13.filename_prefix`；输出节点 `13` | `OM tools/graphics/comfyui_image.py:207-216` |
| `wan22-t2v-4step.json` | `2.text`、`11.{width,height,batch_size}`、`12.noise_seed`、`16.filename_prefix`；输出节点 `16` | `OM tools/video/comfyui_video.py:603-613`、`41` |
| `wan22-i2v-4step.json` | `93.text`、`97.image`、`98.{width,height,length}`、`86.noise_seed`、`108.filename_prefix`；输出节点 `108` | `OM tools/video/comfyui_video.py:643-654`、`42` |

值得注意：t2v 把帧数写进 `11.batch_size`，i2v 写进 `98.length`——同一件事在两份 workflow 里落在不同的 input 名上，这正说明纯硬编码在换 workflow 时的脆弱处。

Partner Node 分支（`comfyui_video.py:744-798`）则完全不读模板，直接**代码里现搭两节点图**：节点 `1` 是 `GeminiVideoOmni` / `ByteDance2TextToVideoNode` / `MinimaxHailuo03TextToVideoNode` 之一，节点 `2` 是 `SaveVideo`，输出节点固定 `2`。用前会先 `has_node` 探测服务端是否暴露该节点类（`comfyui_video.py:502-506`）。

### 2.3 自定义 workflow：只要 `output_node`，一律不注入

`comfyui_image.py:203-205` 与 `comfyui_video.py:497-499` 分支里，自定义 workflow 只做两件事：反序列化（`workflow_json` 字符串或 `workflow_path` 文件，`comfyui_image.py:252-256`）、读 `output_node`。提示词、宽高、种子、参考图**一概不注入**——用户必须把这些值预先烤进导出的 workflow 里。缺 `output_node` 直接拒绝执行，理由写在错误文案里：不知道该从哪个节点下载产物（`comfyui_image.py:162-169`、`comfyui_video.py:446-453`）。

自定义 workflow 的溯源信息单独记账（`comfyui_image.py:284-297`）：`source: user_supplied`、workflow 的 SHA-256（`OM tools/_comfyui/metadata.py:229-232`）、`model_stack_source` 标为 `caller_supplied` 或 `unknown_custom_workflow`。这是个不错的做法——不注入不等于不留痕。

---

## 3. 等待、完成判定与恢复

### 3.1 OpenMontage：WebSocket 优先 + 历史为准

**历史读取**（`client.py:224-235`）：`GET /history/{id}`，取 `json()[prompt_id]`；**键不存在返回 `None`**，表示还在排队或执行中——这是空态的唯一含义。`status.status_str == "error"` 抛 `ComfyUIError`，消息体是 `status.messages` 整个列表。注意只认 `"error"`，不认 `"failed"`。

**纯轮询**（`client.py:199-222`）：`deadline = now + timeout`，每 `interval` 秒探一次，超时抛错。错误文案明确写出「任务很可能仍在服务端运行，**没有被取消**」，并给出两条恢复路径：直接轮询 `/history/{prompt_id}`，或带着这个 `prompt_id` 重新调用。

**WebSocket 等待**（`client.py:246-341`）是这份代码里最讲究的一段，处理了三处竞态：

1. 连接前先探一次历史——事件不会重放，任务可能在 `submit()` 和 `wait_ws()` 之间就结束了（`client.py:273-277`）。
2. 连接建立后**再探一次**——关掉「第一次探测」与「连接建立」之间的残余窗口，此后的事件都会排在已开的 socket 上（`client.py:289-294`）。
3. `recv()` 超时（socket 超时设成 `interval`）时**不是直接 continue，而是再探一次历史**（`client.py:298-302`），避免事件丢了就永远等下去。

事件处理：二进制帧（预览图）跳过（`client.py:303-304`）；JSON 解析失败跳过；`data.prompt_id` 不是本任务的跳过——同一连接可能混着别的任务（`client.py:310-311`）；`progress` 转给回调；`execution_error` 抛错；`executing` 且 `data.node is None` 视为**完成信号**（`client.py:316-320`）。循环退出时若未标记完成，再探一次历史，还没有才报超时（`client.py:324-333`）。标记完成后取历史，取不到也报错（`client.py:335-340`）。

**降级**（`client.py:343-369`）：`wait_ws` 抛的传输层异常（缺 `websocket-client` 包、连不上、断线、坏帧）才降级到 `poll`，真正的 `ComfyUIError`（执行错误 / 超时）原样上抛。关键细节：降级后的 `poll` 拿的是**剩余预算** `timeout - elapsed`，不是全新的一份，避免中途断线让最坏等待翻倍（`client.py:360-369`）。

**按 `prompt_id` 续等**（`client.py:411-442`）：`generate(resume_prompt_id=...)` 跳过提交直接等。续等**强制走轮询而不是 WebSocket**，理由写在注释里：原任务是用上一个实例的 `client_id` 提交的，事件不保证送到当前 socket，只有历史是权威的（`client.py:434-438`）。上层把 `prompt_id` 塞进失败结果的 `data` 里，并在错误文案里直接教用户怎么续等（`comfyui_video.py:528-540`）。

### 3.2 dramaclaw-gateway：纯轮询，状态收敛在包装层

网关把上游 `/history` 的原始响应**包装成自有状态机**再交给任务框架（`adaptor.go:1229-1262`）：

| 情况 | 判定 | 结果状态 |
|---|---|---|
| `/history` HTTP ≥ 400 | `adaptor.go:210-225` | `failed`，reason 含状态码与响应体 |
| 响应体反序列化失败 | `adaptor.go:1236-1240` | `failed`，reason 为解析错误 |
| 找不到本任务的条目（空态） | `adaptor.go:1242-1245` | `running`（保持等待） |
| 条目标记失败 | `historyFailed`，`adaptor.go:1287-1305` | `failed` |
| 无产物 URL 但 `status.completed == true` | `adaptor.go:1252-1256` | `failed`，reason「任务完成但没有输出媒体」 |
| 无产物 URL 且未完成 | `adaptor.go:1257` | `running` |
| 有产物 URL | `adaptor.go:1259-1261` | `succeeded` |

**历史条目的两种形状**（`adaptor.go:1273-1285`）：既接受 `{prompt_id: {...}}` 的标准包裹形状，也接受**根对象本身就是条目**（判据是根上有 `outputs` 键）。这是对上游代理或不同版本 ComfyUI 的一层容错。

**失败判定**（`adaptor.go:1287-1305`）分两级：`status.status_str` 小写后等于 `error` 或 `failed` → 失败，reason 取 `status.message`，缺省「ComfyUI execution failed」；否则退而扫 `status.messages` 数组，任一条目的字符串化结果**包含子串** `execution_error` → 失败，reason 为该条目原文。子串匹配比 OpenMontage 的字段判定更宽松，也更容易误伤。

**完成判定**（`adaptor.go:1307-1314`）：只认 `status.completed` 为布尔 `true`。这个信号只用来区分「无产物是因为还没跑完」还是「跑完了但真没产物」。

**对外进度**（`adaptor.go:429-452`）：成功 100%、失败 100%、其余**固定 30%**。没有任何真实进度——纯轮询拿不到节点级进度。

**超时与续等**：渠道侧**没有**超时与续等逻辑。`FetchTask` 是无状态的单次查询（`adaptor.go:182-236`），轮询节奏、重试与超时全部由 new-api 的任务框架驱动。`prompt_id` 作为 `upstream task id` 持久化，天然具备按 ID 续查的能力，但恢复语义没有像 OpenMontage 那样显式表达给用户。

### 3.3 取消

OpenMontage **完全不支持取消**，也不碰 `/queue`。超时只是客户端放弃等待，任务继续在服务端跑——这一点在错误文案里反复强调。

dramaclaw-gateway 实现了 `TaskCanceller`（`adaptor.go:238-312`），流程是「查队列 → 判位置 → 删 → 复查」：

1. `GET /queue`，HTTP ≥ 400 报错（`adaptor.go:248-264`）。
2. 定位任务（`adaptor.go:372-411`）：先在 `queue_pending` / `pending` 里找，命中返回 `pending`；再在 `queue_running` / `running` 里找，命中返回 `running`；都没有返回空串。查找是**对任意嵌套结构的递归扫描**——数组递归、字典里遇到 `prompt_id` 或 `id` 键且值等于目标就命中、裸字符串相等也算命中。这是对 ComfyUI 队列结构不稳定的一种防御性写法。
3. `running` → 返回 `ErrTaskCancellationUnsupported`（**执行中不可取消**）；既不在 pending 也不在 running → `ErrTaskNotCancellable`（`adaptor.go:269-274`）。
4. `pending` → `POST /queue` 带 `{"delete": [taskID]}`（`adaptor.go:276-293`）。
5. 复查队列：仍在 pending 报错；变成 running 返回不支持；都不在则查 `/history`，若已出现在历史里说明已经跑完，返回 `ErrTaskNotCancellable`（`adaptor.go:294-311`）。历史查询把 404 当作「不存在」而非错误（`adaptor.go:350-352`）。

---

## 4. 缺模型与提交失败的报错结构

### 4.1 OpenMontage：预检 + 结构化下载指引

**模型枚举**（`client.py:110-148`）：对 `CheckpointLoaderSimple`、`UNETLoader`、`VAELoader`、`CLIPLoader`、`LoraLoaderModelOnly` 五个节点类各发一次 `GET /object_info/{class}`，从 `input.required.{field}[0]` 取候选列表，分别归入 `checkpoints` / `diffusion_models` / `vae` / `clip` / `loras` 五组。任何一类失败就把该组置空，不中断整体。

**比对**（`client.py:150-161`）：把五组拍平成一个集合，按文件名精确匹配，返回 `(found, missing)`。

**结构化报错**（`OM tools/_comfyui/metadata.py:245-273`）：

```json
{
  "provider": "comfyui",
  "workflow": "wan22-i2v-4step.json",
  "operation": "image_to_video",
  "missing_models": [
    {"name": "...", "role": "diffusion_model_high_noise", "quantization": "FP8",
     "destination_hint": "ComfyUI/models/diffusion_models/", "download_url": "https://..."}
  ],
  "setup_offer": { "...": "..." }
}
```

每个缺失项从 `BUNDLED_MODEL_STACKS`（`metadata.py:32-226`）里查出角色、量化方式、落盘目录和 HuggingFace 下载地址；查不到就补默认值，`destination_hint` 退化成「ComfyUI/models/ 里对应节点的那一层」（`metadata.py:253-265`）。`setup_offer`（`metadata.py:10-29`）另外描述了「这是本地服务」「修复复杂度：已经跑起来的话一分钟设个环境变量」「健康检查是 `GET /system_stats`」「配上之后解锁什么」。

工具层把它挂在失败结果的 `data` 上，错误文案里指路 `data.missing_models`（`comfyui_image.py:177-192`、`comfyui_video.py:463-489`）。工具状态也据此从 `AVAILABLE` 降到 `DEGRADED` 而非 `UNAVAILABLE`（`comfyui_image.py:140-146`）——服务活着只是模型不全。

### 4.2 dramaclaw-gateway：无预检，只有提交时兜底

网关**不做任何模型或节点预检**。唯一的上游校验发生在提交返回时（`adaptor.go:163-171`）：`prompt_id` 为空、或 `node_errors` 非空、或 `error` 非空，一律包成 `comfyui_submit_failed` / HTTP 502，把原始响应体整个塞进消息里。`node_errors` 的内部结构不解析，用户拿到的是一坨 JSON 文本。

本地校验则有一套自定义错误类型（`DG relay/channel/task/comfyui/task_error.go:5-34`），两种码：`invalid_request` / 400（用户请求有问题，如路由不匹配、参考素材超限）与 `comfyui_configuration_error` / 500（渠道配置有问题，如推断不出绑定、路由优先级并列）。两者都标 `TaskErrorLocal() = true`，并可携带结构化 `data`（路由 ID、优先级、字段名、count / limit）。这个「用户错 vs 配置错」的分档比单一错误码有用得多。

---

## 5. 产物类型判定与扩展名

### 5.1 OpenMontage

**输出键优先级**（`client.py:449-454`）：`images` → `gifs` → `audio` → `video`，取第一个非空列表（Python `or` 短路，空列表视为假）。注释说明：ComfyUI 把图片和视频帧存在 `images` 下，老式 GIF 在 `gifs` 下，原生 `SaveAudio` 的输出在 `audio` 下。全空则抛错，并把该条目下**实际有哪些节点**列出来帮用户改 `output_node`（`client.py:455-459`）。

只看 `output_node` 指定的那一个节点，不扫全图。

**扩展名**（`client.py:462-467`）：直接取服务端返回的 `item["filename"]` 的后缀，不看 content-type、不猜。单个产物直接写到 `dest`（**保留调用方给的扩展名**）；多个产物则改名成 `{stem}_{000}` 并**换成服务端的后缀**。这个不对称是真实的：单产物时若 workflow 实际产出 webm 而 `output_path` 写的是 mp4，文件名会撒谎。

**下载**（`client.py:371-391`、`468-473`）：`GET /view?filename=&subfolder=&type=`，`subfolder` 缺省空串，`type` 缺省 `output`，超时 120 秒。

### 5.2 dramaclaw-gateway

**输出键优先级**（`adaptor.go:1316-1349`）：`videos` → `gifs` → `images`，**外层循环键、内层循环节点**——即先在所有节点里找 `videos`，找不到再找 `gifs`，最后找 `images`。节点 ID 按字典序排序后遍历（`adaptor.go:1321-1325`）。这是视频渠道的合理偏置：同一份 workflow 若既存了预览图又存了视频，取视频。

注意与 OpenMontage 的两处差异：dramaclaw **扫全部输出节点**而非指定节点，且**不识别 `audio` 键**——渠道只做视频。

**产物 URL**（`adaptor.go:1351-1368`）：`filename` 为空或字面 `<nil>` 就跳过该条；`subfolder` 同理时不带该参数；`type` 缺省 `output`；拼成 `{baseURL}/view?{query}`。返回的是**指向 ComfyUI 的直链**，由上层决定要不要转成公网代理 URL（`adaptor.go:466-468`）。

**输入侧的扩展名规则**（与产物无关，但同属类型判定）：

| 环节 | 规则 | 出处 |
|---|---|---|
| 允许的扩展名 | 图 `.jpg` `.jpeg` `.png` `.webp`；视频 `.mp4` `.mov` `.webm`；音频 `.wav` `.mp3` | `adaptor.go:1421-1433` |
| content-type → 扩展名 | `image/jpeg`→`.jpg`、`image/png`→`.png`、`image/webp`→`.webp`、`video/mp4`→`.mp4`、`video/quicktime`→`.mov`、`video/webm`→`.webm`、`audio/wav` 与 `audio/x-wav`→`.wav`、`audio/mpeg`→`.mp3`，其余落缺省 | `adaptor.go:1399-1419` |
| data URL 头 → 扩展名 | 同上表但**漏了 `video/mp4`**，靠缺省兜到 `.mp4`，结果相同 | `adaptor.go:1435-1454` |
| 缺省扩展名 | 视频 `.mp4`、音频 `.mp3`、其余 `.png` | `adaptor.go:1456-1465` |
| URL 取名 | 取路径 basename；解析失败或退化成 `.` `/` 空串时用 `relayclaw-input.png`；后缀不在白名单内就按 content-type 换掉 | `adaptor.go:1370-1389` |

---

## 6. 素材上传：文件名回填、重名与清理

### 6.1 OpenMontage

`upload_image(local_path, name)`（`client.py:393-405`）：`POST /upload/image`，表单字段名 `image`，content-type **硬编码 `image/png`**，返回 `resp.json()["name"]`——即**以服务端回传的名字为准**。`subfolder` 字段完全忽略。

调用侧（`comfyui_video.py:640-641`）传的名字是 `om_{output_path.stem}.png`，**不加随机后缀、不传 `overwrite`**。ComfyUI 默认行为是给重名文件加序号另存，而代码正好用了回传的名字去填 `97.image`，所以重名是安全的——但这是「无意中对」：任何改成直接用本地名字的重构都会静默写错节点。

远程参考图先下载到 `{output_path}.ref.png` 落盘再上传（`comfyui_video.py:626-632`）。**没有任何清理**：临时 `.ref.png` 留在本地，上传到 ComfyUI `input/` 目录的文件也永久留着。

### 6.2 dramaclaw-gateway

`uploadComfyUIInput`（`adaptor.go:1106-1163`）：

- **文件名强制唯一化**（`adaptor.go:1391-1397`）：不管原名叫什么，一律重命名为 `relayclaw-{UUID}{ext}`。扩展名若不在该媒体类型的白名单内就换成缺省值。
- 表单字段名固定 `image`（**视频和音频也走这个字段**，`adaptor.go:1114`），另带 `type=input`、`overwrite=true`（`adaptor.go:1121-1122`）。因为名字已经唯一，`overwrite=true` 实际是冗余保险。
- **回填取服务端回传值**：`name` 为空报错；`subfolder` 非空时回填 `"{subfolder}/{name}"`，否则只回 `name`（`adaptor.go:1152-1162`）。比 OpenMontage 多处理了子目录。
- **同样没有清理**。UUID 命名意味着 ComfyUI 的 `input/` 目录会随请求量无限膨胀，需要外部运维手段回收。

**输入来源限制**（`adaptor.go:1169-1208`）：只接受 `data:` URL 和 `http(s)://` URL，本地路径直接拒绝。data URL 的 MIME 主类型必须与目标媒体类型一致。HTTP 下载时 content-type 允许为空或 `application/octet-stream`，否则必须以 `{mediaType}/` 开头。大小上限是 `MaxFileDownloadMB`（`adaptor.go:1492-1494`），**下载与 base64 解码前后各校验一次**，读取用 `io.LimitReader(maxBytes+1)` 防止内存打爆（`adaptor.go:1195-1204`）；data URL 则先按 base64 长度估算解码后大小再真解（`adaptor.go:1467-1490`）。

---

## 7. 各自的局限

### 7.1 OpenMontage

- **自定义 workflow 零注入**：提示词、尺寸、种子、参考图都得预先烤进 JSON。工具的 `prompt` 参数对自定义 workflow 形同虚设，只进结果元数据（`comfyui_image.py:236`）。
- **节点 ID 硬编码**：换一份 workflow 就得改代码。同一个「帧数」在 t2v 落 `11.batch_size`、在 i2v 落 `98.length`，无统一抽象。
- **无取消**：只能放弃等待，队列上的任务照跑不误。
- **无负向提示词、无尾帧**：内置 workflow 的注入表里都没有。
- **产物只看单个 `output_node`**：多输出节点的 workflow 得靠用户自己挑。
- **上传 content-type 写死 `image/png`**，且只有内置 i2v 路径用得上参考图。
- **单产物时保留调用方扩展名**，可能与实际格式不符。
- **临时文件与服务端 `input/` 均不清理**。

### 7.2 dramaclaw-gateway

- **一渠道一模型名**：主仓校验 `parse_comfyui_channel_workflows` 明确要求——每条路由的 `match.models` 最多一个模型名，所有路由加上 `model_name` 去重后**必须恰好一个**，否则报「a ComfyUI channel supports one model name」（`DC src/novelvideo/model_gateway_settings.py:410-433`）。走 `workflow_by_model` 形态时模型名即字典键（`model_gateway_settings.py:436-458`）。同一台 ComfyUI 要挂多个模型就得建多个渠道。
- **只有视频**：`ModelList` 只有 `comfyui-video`（`adaptor.go:33-35`），`GetCapabilities` 只返回 `["video"]`（`adaptor.go:421-423`），产物判定不认 `audio` 键。图像通道不存在。
- **多处硬绑 MiniMax H3**：路由模式反推（`adaptor.go:612-641`）、时长 Primitive 回退第二趟（`adaptor.go:852-862`）、帧数撤销（`adaptor.go:827-830`）、尺寸对齐与预设档位（`adaptor.go:1045-1097`）、参考素材改图的目标节点类（`reference_media.go:15`）。换个模型族，这些启发式要么失效要么误伤。
- **路由没写 `modes` 且 workflow 不是 H3 → 直接报错**，通用 workflow 必须显式声明模式。
- **显式绑定无法清空推断结果**：合并只认非空值（`adaptor.go:1510`）。
- **进度固定 30%**，无节点级反馈。
- **`/history` 一次 HTTP 5xx 就判定任务永久失败**（`adaptor.go:210-225`），没有瞬时故障的重试宽限。
- **取消只覆盖 `pending`**，执行中无能为力。
- **`node_errors` 不解析**，用户拿到原始 JSON 文本。
- **无 `/object_info` 预检**，缺模型只能等提交失败。
- **上传文件永不回收**。

### 7.3 对「一 workflow 一模型行」的启示

两家从相反方向印证了同一个结论：**workflow 与模型的一一绑定是简化设计的正确起点**。

dramaclaw 是**在 workflow 多样性上放开、在模型名上收紧**：允许一个渠道挂多条路由、多份 workflow，但强制它们共用一个模型名。代价是全部差异必须塞进 `match` 的八个维度，配置复杂度转嫁给用户，并列优先级只能报错不能仲裁。

OpenMontage 是**两边都收紧**：一份 workflow 一个能力，节点 ID 硬编码，自定义 workflow 干脆放弃注入。代价是扩展性为零。

ArcReel 的「一份 workflow = 一个模型行」介于两者之间，且比 dramaclaw 的形态更干净：模型行本就是用户可见的选择单位，把 workflow 绑上去之后，dramaclaw 用 `workflow_routes` 解决的「同一模型名下按素材 / 比例 / 时长选不同 workflow」问题，在 ArcReel 这里退化成「用户自己选哪一行模型」——把机器的打分猜测换成人的显式选择。这与地图 #2516 里「首期一模型行一 workflow，选路等真实用户出现」的倾向一致，本调研没有发现推翻它的证据：dramaclaw 的选路机制本身也在并列优先级时拒绝仲裁，说明作者同样不信任自动选路的歧义处理。

---

## 8. Comfy Cloud 托管 API 与自建 API 的差异

地图 #2516 要求顺带记录。据 Comfy 官方文档仓：

| 维度 | 自建 ComfyUI | Comfy Cloud |
|---|---|---|
| 基地址 | 自行配置，本地典型 `http://127.0.0.1:8188` | `https://cloud.comfy.org` |
| 路径前缀 | `/prompt`、`/history/{id}`、`/view` | `/api/prompt`、`/api/view`，均带 `/api` 前缀 |
| 鉴权 | 默认无 | `X-API-Key` 请求头，全部请求都要 |
| 状态查询 | `GET /history/{prompt_id}`，靠 `status` 字段自行判定 | `GET /api/job/{prompt_id}/status`，返回显式状态枚举 `success` / `error` / `non_retryable_error` / `lost` / `cancelled` |
| 取产物 | 历史条目里的 `outputs` | `GET /api/jobs/{prompt_id}` 的 `outputs` 字段 |
| 下载 | `/view` 直接返回文件字节 | `/api/view` 返回 **302 重定向**到对象存储签名 URL；必须手动读 `Location` 而不跟随重定向，否则客户端会把 API Key 带到存储域名 |

两边的 workflow JSON 是同一套 API Format，`prompt_id` 语义一致。差异集中在**路径前缀、鉴权、状态枚举、下载要走一次重定向**四点。托管形态不在 #2516 范围内，此处仅存档。

---

## 9. 对 ArcReel 的直接启示

### 9.1 可直接借用

1. **绑定表按「节点 ID + input 名」成对建模**（`DG channel_settings.go:134-155`）。槽位是产品概念，节点 ID 与 input 名是 ComfyUI 概念，成对存储让同一槽位能落在任意节点的任意 input 上，不需要为每份 workflow 写代码。ArcReel 的绑定表可以直接照搬这个形状。
2. **推断 + 显式覆盖的两层结构，保存后以显式为准**（`DG adaptor.go:799-807`）。与地图里已定的倾向完全一致，且 dramaclaw 的实现验证了它可行。
3. **别名有序表 + 固定的多候选取舍**（`DG adaptor.go:903-920`）。先按别名优先级、再按节点 ID 稳定排序，结果可复现。ArcReel 至少要保证**同一份 workflow 每次导入推断出同样的绑定**，这个双层确定性排序是最省事的做法。
4. **推断不出关键槽位就报「请显式配置 X」而不是静默跳过**（`DG adaptor.go:923-928`）。提示词与首帧属于关键槽位，宽高种子可以静默跳过。
5. **「用户请求错」与「渠道配置错」分成两个错误码 + 结构化 data**（`DG task_error.go:18-33`）。ArcReel 的绑定表本质是配置，配置错和调用错的处理路径完全不同（一个要引导用户改设置，一个要引导改这次请求）。
6. **历史条目的两种形状都接受**（`DG adaptor.go:1273-1285`）。成本极低的容错。
7. **产物按输出键优先级扫全部输出节点**（`DG adaptor.go:1316-1349`），而不是要求用户指定 `output_node`。这和地图里「产物按输出文件类型识别而非节点类型」的倾向一致，且省掉一个用户必填项。ArcReel 是图 + 视频双通道，优先级表要按模型行的媒体类型分别定（视频行 `videos` → `gifs` → `images`，图像行 `images` → `gifs`）。
8. **上传文件名强制 UUID 唯一化 + 以服务端回传值回填**（`DG adaptor.go:1152-1162`、`1391-1397`）。重名问题一次性消掉，且正确处理了 `subfolder`。OpenMontage 那种「靠回传值碰巧对」的写法不要学。
9. **输入大小上限在下载与解码前后各校验一次，读取用限长 Reader**（`DG adaptor.go:1195-1204`、`1467-1490`）。
10. **超时不等于取消，且把恢复路径写进错误文案**（`OM client.py:213-222`）。ArcReel 首期纯轮询，同样会遇到「等不动了但服务端还在跑」，`prompt_id` 必须持久化且用户可见。
11. **缺模型的结构化报错载荷**（`OM metadata.py:245-273`）：`missing_models[]` 带 `role` / `destination_hint` / `download_url`，外加一个描述「怎么修、修好解锁什么」的 setup 段。地图把缺模型检测列在「尚未明确」里，这份载荷形状可以直接作为将来那张票的起点。
12. **workflow 内容的 SHA-256 溯源**（`OM metadata.py:229-232`）。用户导入的 workflow 会被改，产物要能追回当时提交的是哪一版。

### 9.2 需改造

1. **参考素材的动态改图**（`DG reference_media.go:55-116`）。「按实际素材数量增删加载器节点并重接连线」这个思路对多参考图场景是必需的，但 dramaclaw 的实现死绑 `MiniMaxH3ReferenceToVideo` 和 `ref_images.ref_image_{i}` 这套命名。ArcReel 要借的是**机制**——旧连线清理、孤儿链回收、节点 ID 分配（`reference_media.go:301-324` 的「最大整数 ID + 1」可直接用）——而目标节点与 input 命名必须来自绑定表而非硬编码类名。这件事复杂度不低，建议明确划到二期，首期先支持固定数量的参考图槽位。
2. **`_meta.title` 参与推断**（`DG adaptor.go:836-851`）。地图已把 `_meta.title` 列进推断约定，但 dramaclaw 只在一处用它、且是**全等匹配**两个写死的标题。ArcReel 要把它提到和 `class_type` / input 名并列的第一等信号，并支持包含匹配与中文标题。
3. **时长语义**（`DG adaptor.go:822-830`）。dramaclaw 的 `frames`/`length`/`duration` 三方互斥处理暴露了真问题：`length` 在不同节点里可能是秒也可能是帧。ArcReel 不能靠「是不是 H3」来判，得在绑定表里让**时长和帧数各占一个槽位**，由用户确认哪个是哪个，并把 `frames × fps` 的换算显式化。地图已把这条列为待定，本调研支持「不猜、让用户确认」。
4. **取消流程**（`DG adaptor.go:238-312`）。三段式「查队列 → 删 → 复查」可以借，但 ArcReel 需要明确「执行中不可取消」这个限制怎么对用户表达——`ErrTaskCancellationUnsupported` 和 `ErrTaskNotCancellable` 的区分在产品上要落成两句不同的话。
5. **`node_errors` 的呈现**（`DG adaptor.go:168-171`）。dramaclaw 把原始 JSON 整个抛给用户是偷懒。ComfyUI 的 `node_errors` 结构是 `{node_id: {class_type, errors: [{type, message, details}]}}`，值得解析成「哪个节点的哪个输入有什么问题」，这也是缺模型 / 缺自定义节点的主要暴露面。
6. **`/history` 的瞬时故障**（`DG adaptor.go:210-225`）。一次 5xx 就判死不可接受。ArcReel 的轮询要区分「上游明确说失败」与「这次没查到」，后者应重试若干次再放弃。

### 9.3 不借用

1. **WebSocket 等待**（`OM client.py:246-341`）。代码质量很高、三处竞态处理值得记下，但地图已定「首期只轮询 `/history`，不接 WebSocket」。真要接时再回来读这段——尤其是「连接前后各探一次历史」和「降级只给剩余预算」两点。
2. **`workflow_routes` 打分选路**（`DG adaptor.go:536-571`）。地图已定首期一模型行一 workflow，选路无用武之地。而且 dramaclaw 自己在优先级并列时也选择报错而非仲裁，说明这套机制的歧义面不小。
3. **硬编码节点 ID 的注入表**（`OM comfyui_video.py:603-654`）。ArcReel 不自带 workflow，用户导入什么就是什么，这条路从一开始就不通。
4. **自定义 workflow 完全不注入**（`OM comfyui_image.py:203-205`）。这正是 ArcReel 要解决的问题本身——用户导入自己的 workflow 之后，提示词等槽位必须能注入，否则通道没有意义。
5. **一渠道一模型名的强制校验**（`DC model_gateway_settings.py:432-433`）。ArcReel 的模型行本就是一对一的，不需要再叠一层渠道级的唯一性约束。
6. **按能力分流的服务地址环境变量**（`OM client.py:45-70`）。ArcReel 的供应商本身就是配置实体，一台 ComfyUI 一个供应商，多台就建多个，不需要环境变量层。
7. **MiniMax H3 的尺寸预设与 32 对齐**（`DG adaptor.go:1045-1097`）。模型族专属知识，ArcReel 不该内置任何模型族的尺寸表。
8. **固定 30% 进度**（`DG adaptor.go:448-449`）。宁可不报进度，也不报假进度。

---

## 10. 未能核实的问题

- dramaclaw-gateway 的 `ComfyUISettings.OutputBaseURL` 字段（`channel_settings.go:105`）在 comfyui 渠道代码里没有读取点，产物 URL 一律用 `info.ChannelBaseUrl`。该字段是预留还是由其它层消费，未在本次阅读范围内查清。
- 两家的测试文件（`DG relay/channel/task/comfyui/adaptor_test.go`、`OM tests/contracts/test_comfyui_tools.py`）未逐条阅读，可能包含本文未覆盖的边界约定。
- ComfyUI 上游 `/queue` 与 `/history` 的响应结构未对照官方源码核实，本文对这两处的描述来自两家的解析代码所体现的预期形状。
