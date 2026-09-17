# ComfyUI 原生 HTTP / WebSocket API 一手事实调研

**调研截止日期**：2026-09-17
**议题**：ArcReel/ArcReel#2517（地图 #2516）
**用途**：作为 ComfyUI 运行时接入设计的输入素材，不包含 ArcReel 侧的目录结构、类设计与实施计划
**作者**：协助调研（Claude）

## 0. 调研范围与来源基线

本报告只采信以下一手来源，每条结论都标注来源位置：

| 来源 | 说明 |
|---|---|
| `comfyanonymous/ComfyUI` 源码 | 以 `master` 分支为准，快照 commit `d39cdfdb03932f14f390dda27891b3e546efb746`，对应版本 `comfyui_version.py:__version__ = "0.36.0"`（release v0.36.0，2026-09-15） |
| `comfy-org/docs` 官方文档仓库 | 即 docs.comfy.org 的源文件，经 context7 library `/comfy-org/docs` 读取 |
| GitHub Releases / commit 历史 | `repos/comfyanonymous/ComfyUI` 的 tag 与 commit，用于版本差异定位 |
| `Kosinkadink/ComfyUI-VideoHelperSuite` 源码 | VHS 节点输出形状的唯一权威来源（第三方插件，非 ComfyUI 本体） |
| ComfyUI 仓库的 `openapi.yaml` | 官方 OpenAPI 3.0.3 规格（`title: ComfyUI API`，5489 行），PR #13397 于 v0.20.1（2026-04-27）引入 |

本报告中的文件行号均指上述 `master` 快照。次级来源（博客、教程、逆向整理的 API 文档）一律不采信。

### 0.1 `openapi.yaml` 与自建实现存在系统性偏差

`openapi.yaml` 是官方一手规格，但**它描述的是偏 Cloud 的服务面，不能当作自建 ComfyUI 的准确契约**。实测偏差：

- 规格里 `PromptRequest` 的属性只有 `extra_data` / `front` / `number` / `partial_execution_targets` / `prompt` / `workflow_id` / `workflow_version_id`，**没有 `prompt_id`**（`openapi.yaml:1045-1075`），而 `server.py:1094-1107` 明确支持客户端自带 `prompt_id`。
- 规格根本没有记录 `GET /api/history/{prompt_id}`（只有 `POST /api/history`、`GET /api/history_v2`、`GET /api/history_v2/{prompt_id}`），而自建实例上 `GET /history/{prompt_id}` 是可用的核心端点。
- 规格说 `/api/interrupt` "Cancels the first active job for **the authenticated user**" 且 "**Takes no body** and cannot target a specific job"（`openapi.yaml:3114-3117`），而 `server.py:1165-1180` 实际会读 body 里的 `prompt_id` 做定向中断，且自建实例没有"authenticated user"这个概念。
- 规格里出现 `workflow_id`（"UUID identifying the **cloud** workflow entity"）、`cloud_version` 等自建不存在的字段。

**结论：涉及自建行为时，一律以 `server.py` / `execution.py` 为准，`openapi.yaml` 只用来读取上传限制、参数枚举这类实现里不直接体现的约束，以及官方的废弃意向。** 下文凡引用规格处都会标明。

不在调研范围：ArcReel 代码层面的适配层设计、数据库 schema、前端改动、实施时间线。

---

## 1. 端点全景

`server.py` 在 `PromptServer.__init__` 里用 `aiohttp` 的 `RouteTableDef` 注册路由，`add_routes()`（`server.py:1229-1243`）再把每条非静态路由复制一份加上 `/api` 前缀：

```python
# server.py:1231-1241
# Prefix every route with /api for easier matching for delegation.
# Currently both the old endpoints without prefix and new endpoints with
# prefix are supported.
api_routes = web.RouteTableDef()
for route in self.routes:
    if isinstance(route, web.RouteDef):
        api_routes.route(route.method, "/api" + route.path)(route.handler, **route.kwargs)
```

**结论：`/prompt` 与 `/api/prompt` 是同一个 handler，响应完全一致。** 两套都长期支持，注释里明说 "both ... are supported"。下文除非特别说明，端点一律写不带前缀的形式。

本次调研关心的路由与源码位置：

| 端点 | 方法 | 源码位置 |
|---|---|---|
| `/ws` | GET (WebSocket) | `server.py:269` |
| `/prompt` | POST | `server.py:1075` |
| `/prompt` | GET（队列剩余量） | `server.py:750` |
| `/history` | GET / POST | `server.py:1048` / `server.py:1206` |
| `/history/{prompt_id}` | GET | `server.py:1062` |
| `/queue` | GET / POST | `server.py:1067` / `server.py:1149` |
| `/interrupt` | POST | `server.py:1163` |
| `/free` | POST | `server.py:1195` |
| `/view` | GET | `server.py:516` |
| `/upload/image` | POST | `server.py:464` |
| `/upload/mask` | POST | `server.py:470` |
| `/system_stats` | GET | `server.py:689` |
| `/features` | GET | `server.py:742` |
| `/object_info`、`/object_info/{node_class}` | GET | `server.py:803` / `server.py:816` |
| `/models`、`/models/{folder}`、`/embeddings` | GET | `server.py:342` / `348` / `337` |
| `/api/jobs`、`/api/jobs/{job_id}` | GET | `server.py:824` / `server.py:922` |
| `/api/jobs/{job_id}/cancel`、`/api/jobs/cancel` | POST | `server.py:974` / `server.py:992` |

注意 `/api/jobs` 系列在源码里**就是**以 `/api/` 硬编码注册的，因此实际可用路径是 `/api/jobs` 与 `/api/api/jobs`（后者由前缀复制产生，无实用价值）。

---

## 2. `POST /prompt`

### 2.1 请求形状

handler 从 `json_data` 里读取的字段（`server.py:1075-1145`）：

| 字段 | 必填 | 说明 |
|---|---|---|
| `prompt` | 是 | API 格式的工作流图，`{"<node_id>": {"class_type": ..., "inputs": {...}, "_meta": {...}}}`。缺失时返回 400 `no_prompt` |
| `client_id` | 否 | 写进 `extra_data["client_id"]`；**决定执行期 WebSocket 事件是否定向推送给你**（见 §6） |
| `prompt_id` | 否 | 客户端自带的 job id，必须是规范小写连字符 UUID；缺省或显式 `null` 时由服务端 `str(uuid.uuid4())` 生成 |
| `number` | 否 | 队列优先级数值，越小越靠前 |
| `front` | 否 | 为真时取 `-self.number`，即插队到最前 |
| `extra_data` | 否 | 透传的附加数据，`extra_pnginfo.workflow` 会被写进产物元数据 |
| `partial_execution_targets` | 否 | 只执行指定输出节点子集 |

请求示例：

```json
{
  "client_id": "9f1c7b0c4a1c4e5f9a2b3c4d5e6f7a8b",
  "prompt_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "prompt": {
    "3": {"class_type": "KSampler", "inputs": {"seed": 42, "steps": 20, "model": ["4", 0]}},
    "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "ArcReel", "images": ["8", 0]}}
  },
  "extra_data": {"extra_pnginfo": {"workflow": {"id": "wf-uuid"}}}
}
```

### 2.2 成功响应（HTTP 200）

```python
# server.py:1136-1137
response = {"prompt_id": prompt_id, "number": number, "node_errors": valid[3]}
return web.json_response(response)
```

```json
{
  "prompt_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "number": 12,
  "node_errors": {}
}
```

**关键事实：成功响应里也可能带非空 `node_errors`。** `validate_prompt` 只要**至少有一个**输出节点校验通过就返回 `(True, None, good_outputs, node_errors)`（`execution.py:1282`），此时失败的那些输出节点仍会出现在 `node_errors` 里，HTTP 码还是 200，提交照常入队。判"提交成功"只能看 HTTP 码，判"整图健康"要额外看 `node_errors` 是否为空。

### 2.3 客户端自带 `prompt_id` 的校验

```python
# server.py:1096-1107
try:
    prompt_id = validate_job_id(client_prompt_id)
except ValueError:
    error = {
        "type": "invalid_prompt_id",
        "message": "prompt_id must be a valid UUID",
        "details": "prompt_id must be a UUID string in canonical lowercase hyphenated form; omit it to let the server generate one",
        "extra_info": {}
    }
    return web.json_response({"error": error, "node_errors": {}}, status=400)
```

`validate_job_id`（`comfy_execution/jobs.py:34-47`）要求 `str(uuid.UUID(value)) == value`，即**必须是规范小写连字符形式**。大写、去连字符、带花括号都会被 400 拒绝。注释说明了原因：id 在 history 键、WebSocket 事件、`/interrupt` 匹配里都是逐字比较的，静默改写会让后续所有精确匹配失效。

**未核实**：服务端不去重相同 `prompt_id`。源码里没有看到对已存在 id 的拒绝逻辑，重复提交同一个 id 会产生两条队列项，后完成的那条会覆盖 `history[prompt_id]`（`execution.py:1335`）。这一点没有官方文档背书，属于从源码推出的行为。

### 2.4 校验失败（HTTP 400）与 `node_errors` 结构

失败响应（`server.py:1139-1140`）：

```python
return web.json_response({"error": valid[1], "node_errors": valid[3]}, status=400)
```

顶层 `error` 是单个字典，`node_errors` 是 `{node_id: {errors, dependent_outputs, class_type}}`（`execution.py:1229-1237`）：

```python
node_errors[node_id] = {
    "errors": reasons,
    "dependent_outputs": [],
    "class_type": class_type
}
```

每条 `reasons` 元素形如 `{"type", "message", "details", "extra_info"}`。

#### 缺节点（自定义节点没装）

`execution.py:1145-1161`，**提前返回，`node_errors` 为空字典**：

```json
{
  "error": {
    "type": "missing_node_type",
    "message": "Node 'VHS_VideoCombine' not found. The custom node may not be installed.",
    "details": "Node ID '#14'",
    "extra_info": {"node_id": "14", "class_type": "VHS_VideoCombine", "node_title": "VHS_VideoCombine"}
  },
  "node_errors": {}
}
```

同一个 `missing_node_type` 也用于节点缺 `class_type` 字段的情况（`execution.py:1131-1144`），此时 `extra_info.class_type` 为 `null`。

#### 缺模型（combo 值不在候选列表里）

模型文件缺失在 ComfyUI 里表现为 combo 值不在枚举中，错误类型是 `value_not_in_list`（`execution.py:1069-1079`）。当候选超过 20 项时会裁掉 `input_config` 并只报长度：

```json
{
  "error": {
    "type": "prompt_outputs_failed_validation",
    "message": "Prompt outputs failed validation",
    "details": "Value not in list: ckpt_name: 'sd_xl_base_1.0.safetensors' not in (list of length 37)",
    "extra_info": {}
  },
  "node_errors": {
    "4": {
      "errors": [{
        "type": "value_not_in_list",
        "message": "Value not in list",
        "details": "ckpt_name: 'sd_xl_base_1.0.safetensors' not in (list of length 37)",
        "extra_info": {"input_name": "ckpt_name", "input_config": null, "received_value": "sd_xl_base_1.0.safetensors"}
      }],
      "dependent_outputs": ["9"],
      "class_type": "CheckpointLoaderSimple"
    }
  }
}
```

#### 类型错误

两个不同的类型：

`invalid_input_type`（`execution.py:1005-1015`）是标量转换失败，比如给 `INT` 传了 `"abc"`：

```json
{
  "type": "invalid_input_type",
  "message": "Failed to convert an input value to a INT value",
  "details": "steps, abc, invalid literal for int() with base 10: 'abc'",
  "extra_info": {"input_name": "steps", "input_config": [...], "received_value": "abc", "exception_message": "..."}
}
```

`return_type_mismatch`（`execution.py:939-950`）是连线两端类型不匹配：

```json
{
  "type": "return_type_mismatch",
  "message": "Return type mismatch between linked nodes",
  "details": "model, received_type(CLIP) mismatch input_type(MODEL)",
  "extra_info": {"input_name": "model", "input_config": [...], "received_type": "CLIP", "linked_node": ["4", 1]}
}
```

#### 其余错误类型清单

`execution.py` 中出现的全部 `type` 取值：`dependency_cycle`(860)、`required_input_missing`(905)、`bad_linked_input`(920)、`return_type_mismatch`(940)、`exception_during_inner_validation`(967)、`invalid_input_type`(1006)、`value_smaller_than_min`(1022)、`value_bigger_than_max`(1035)、`value_not_in_list`(1070)、`custom_validation_failed`(1101)、`exception_during_validation`(1202)、`missing_node_type`(1135/1152)、`prompt_no_outputs`(1169)、`prompt_outputs_failed_validation`(1274)。加上 `server.py:1100` 的 `invalid_prompt_id` 与 `server.py:1143` 的 `no_prompt`。

**HTTP 码只有 200 和 400 两种**，没有 422/409/500 的分支。

---

## 3. `GET /history/{prompt_id}` 四态

handler 只是把 `PromptQueue.get_history(prompt_id=...)` 的结果原样 JSON 化（`server.py:1062-1065`）。真正的形状在 `execution.py:1399-1425`。

### 3.1 跑完前：空对象 `{}`

```python
# execution.py:1420-1425
elif prompt_id in self.history:
    p = self.history[prompt_id]
    ...
    return {prompt_id: p}
else:
    return {}
```

`self.history[prompt_id]` 只在 `task_done` 里写入（`execution.py:1321-1341`），而 `task_done` 由 `prompt_worker` 在 `e.execute(...)` **返回之后**才调用（`main.py:363-372`）。

**结论（对运行时最有约束力的一条）：排队中和执行中的 prompt，`GET /history/{prompt_id}` 一律返回 `{}`，HTTP 200。** 没有中间态、没有 `status: "running"`、没有裸 entry。`{}` 有三种含义完全无法区分：还在排队、正在跑、id 根本不存在。另外 `MAXIMUM_HISTORY_SIZE = 10000`（`execution.py:1284`），超出后最旧的条目被 `self.history.pop(next(iter(self.history)))` 丢弃（`execution.py:1325-1326`），所以跑完很久的 id 也会重新变回 `{}`。

判断是否还在跑必须另找来源：`GET /queue`、`GET /api/jobs/{job_id}`，或 WebSocket。

### 3.2 成功

返回值永远是 **以 prompt_id 为键的单键字典**，不是裸 entry：

```json
{
  "3fa85f64-5717-4562-b3fc-2c963f66afa6": {
    "prompt": [12, "3fa85f64-5717-4562-b3fc-2c963f66afa6", {"3": {"class_type": "KSampler", "inputs": {}}}, {"client_id": "...", "create_time": 1789000000000}, ["9"]],
    "outputs": {
      "9": {"images": [{"filename": "ArcReel_00001_.png", "subfolder": "", "type": "output"}]}
    },
    "status": {
      "status_str": "success",
      "completed": true,
      "messages": [
        ["execution_start", {"prompt_id": "3fa85f64-...", "timestamp": 1789000000000}],
        ["execution_cached", {"nodes": ["4"], "prompt_id": "3fa85f64-...", "timestamp": 1789000000100}],
        ["execution_success", {"prompt_id": "3fa85f64-...", "timestamp": 1789000012000}]
      ]
    },
    "meta": {
      "9": {"node_id": "9", "display_node": "9", "parent_node": null, "real_node_id": "9"}
    }
  }
}
```

entry 的构造在 `execution.py:1335-1340`：先写 `{"prompt", "outputs", "status"}`，再 `update(history_result)`，而 `history_result = {"outputs": ui_outputs, "meta": meta_outputs}`（`execution.py:831-834`）。所以 `meta` 键是 `update` 带进来的，`outputs` 被覆盖成真实输出。

`prompt` 字段是个 **5 元组数组**（敏感字段已被 `remove_sensitive = lambda prompt: prompt[:5] + prompt[6:]` 剥掉，`main.py:366`）：`[number, prompt_id, prompt, extra_data, outputs_to_execute]`。被剥掉的是 `SENSITIVE_EXTRA_DATA_KEYS = ("auth_token_comfy_org", "api_key_comfy_org")`（`execution.py:161`）。

`status` 三个字段来自 `ExecutionStatus` NamedTuple（`execution.py:1316-1319`），由 `prompt_worker` 填充（`main.py:367-372`）：

```python
status=execution.PromptQueue.ExecutionStatus(
    status_str='success' if e.success else 'error',
    completed=e.success,
    messages=e.status_messages)
```

`messages` 是 `[event_name, data]` 的二元数组列表，`data` 里一定带 `timestamp`（毫秒），因为 `add_message` 统一注入（`execution.py:677-682`）。

### 3.3 失败

```json
{
  "3fa85f64-...": {
    "prompt": [...],
    "outputs": {},
    "status": {
      "status_str": "error",
      "completed": false,
      "messages": [
        ["execution_start", {"prompt_id": "3fa85f64-...", "timestamp": 1789000000000}],
        ["execution_error", {
          "prompt_id": "3fa85f64-...",
          "node_id": "3",
          "node_type": "KSampler",
          "executed": ["4", "5"],
          "exception_message": "CUDA out of memory. Tried to allocate 2.00 GiB",
          "exception_type": "torch.cuda.OutOfMemoryError",
          "traceback": ["  File \"...\", line 123, in ...\n"],
          "current_inputs": {"model": ["<MODEL>"], "steps": [20]},
          "current_outputs": ["4", "5"],
          "timestamp": 1789000003000
        }]
      ]
    },
    "meta": {}
  }
}
```

字段来自 `handle_execution_error` 的 else 分支（`execution.py:703-713`）。

### 3.4 中断（interrupt / cancel）

**中断也走 `status_str: "error"`，`completed: false`**，与真失败在 `status` 三字段上完全同形。唯一区别是 `messages` 里的事件名是 `execution_interrupted` 而不是 `execution_error`，且 payload 少了 exception 相关字段（`execution.py:692-700`）：

```json
{
  "3fa85f64-...": {
    "prompt": [...],
    "outputs": {},
    "status": {
      "status_str": "error",
      "completed": false,
      "messages": [
        ["execution_start", {"prompt_id": "3fa85f64-...", "timestamp": 1789000000000}],
        ["execution_interrupted", {
          "prompt_id": "3fa85f64-...",
          "node_id": "3",
          "node_type": "KSampler",
          "executed": ["4", "5"],
          "timestamp": 1789000005000
        }]
      ]
    },
    "meta": {}
  }
}
```

ComfyUI 自己也是这样区分的，`comfy_execution/jobs.py:222-243` 是官方的判定实现：

```python
for entry in messages:
    event_name, event_data = entry[0], entry[1]
    if event_name == 'execution_start':
        execution_start_time = event_data.get('timestamp')
    elif event_name in ('execution_success', 'execution_error', 'execution_interrupted'):
        execution_end_time = event_data.get('timestamp')
        if event_name == 'execution_error':
            execution_error = event_data
        elif event_name == 'execution_interrupted':
            was_interrupted = True

if status_str == 'success':
    status = JobStatus.COMPLETED
elif status_str == 'error':
    status = JobStatus.CANCELLED if was_interrupted else JobStatus.FAILED
else:
    status = JobStatus.COMPLETED
```

**四态判定表**（权威依据就是上面这段）：

| 情形 | `GET /history/{id}` | `status_str` | `completed` | `messages` 末尾事件 |
|---|---|---|---|---|
| 排队中 / 执行中 / 不存在 | `{}` | — | — | — |
| 成功 | 单键字典 | `"success"` | `true` | `execution_success` |
| 失败 | 单键字典 | `"error"` | `false` | `execution_error` |
| 中断 | 单键字典 | `"error"` | `false` | `execution_interrupted` |

注意 `status` 字段本身可能是 `null`：`task_done` 的 `status` 参数是 `Optional`，为 `None` 时写入 `'status': None`（`execution.py:1330-1338`）。`prompt_worker` 总是传值，但自定义前端或插件调用路径不保证。读取时要按 `status_info.get('status_str') if status_info else None` 防空，官方自己就是这么写的（`jobs.py:214-215`）。

### 3.5 `GET /history`（列表）

`server.py:1048-1060`，支持 `max_items` 与 `offset` 查询参数，返回 `{prompt_id: entry, ...}` 的多键字典。`offset` 默认 `-1`，此时若给了 `max_items` 会被换算成 `len(history) - max_items`，即取最后 N 条（`execution.py:1403-1405`）。

`POST /history` 支持 `{"clear": true}` 清空与 `{"delete": ["<id>", ...]}` 删除指定条目，都返回裸 200（`server.py:1206-1218`）。

---

## 4. `outputs` 的产物字段

### 4.1 底层数据结构

所有核心保存节点最终都产出 `SavedResult`，它是个 dict 子类，**只有三个键**（`comfy_api/latest/_ui.py:27-30`）：

```python
class SavedResult(dict):
    def __init__(self, filename: str, subfolder: str, type: FolderType):
        super().__init__(filename=filename, subfolder=subfolder, type=type.value)
```

`type` 取值来自 `FolderType`，即 `"output"` / `"input"` / `"temp"`。**核心节点的产物项里没有 `format`、没有 `fullpath`、没有 `frame_rate`、没有 URL。**

### 4.2 各节点的 `outputs` 键

| 节点 | `outputs[node_id]` 形状 | 源码 |
|---|---|---|
| `SaveImage` | `{"images": [{filename, subfolder, type}]}` | `nodes.py:1712-1719` |
| `PreviewImage`（V3） | `{"images": [...], "animated": [false]}` | `_ui.py:404-408` |
| `SaveVideo` | `{"images": [...], "animated": [true]}` | `nodes_video.py:203` → `_ui.py:436-437` |
| `SaveWEBM` | `{"images": [...], "animated": [true]}` | `nodes_video.py:74` → 同上 |
| `SaveAudio` / `SaveAudioMP3` / `SaveAudioOpus` / `SaveAudioAdvanced` | `{"audio": [{filename, subfolder, type}]}` | `nodes_audio.py:184/214/245/291` → `_ui.py:64-65` |
| `PreviewAudio` | `{"audio": [...]}`（`type` 为 `"temp"`） | `_ui.py:428-429` |
| `VHS_VideoCombine`（第三方） | `{"gifs": [{filename, subfolder, type, format, frame_rate, workflow, fullpath}]}` | `videohelpersuite/nodes.py:622-634` |

### 4.3 最容易踩的一条：核心视频落在 `images` 下

```python
# comfy_api/latest/_ui.py:432-437
class PreviewVideo(_UIOutput):
    def __init__(self, values: list[SavedResult | dict], **kwargs):
        self.values = values

    def as_dict(self):
        return {"images": self.values, "animated": (True,)}
```

`SaveVideo` 返回 `io.NodeOutput(video, ui=ui.PreviewVideo([ui.SavedResult(file, subfolder, io.FolderType.output)]))`（`nodes_video.py:203`）。

**核心 `SaveVideo` 的 mp4/webm/mkv 产物出现在 `outputs[node_id]["images"]` 里，键名是 `images` 而不是 `videos`。** 唯一的区分信号是同级的 `"animated": [true]`（注意是**单元素数组**，源码里是元组 `(True,)`，序列化成 `[true]`）。文件类型只能从 `filename` 的扩展名判断，扩展名由 `Types.VideoContainer.get_extension(format_name)` 决定（`nodes_video.py:200`），`format` 为 `auto` 时按 codec 推导：av1 → webm，否则 mp4（`nodes_video.py:172-173`）。

**`outputs` 里没有 `videos` 这个键。** 在 `master` 快照的核心代码里搜不到任何产出 `{"videos": ...}` 的路径。`comfy_execution/jobs.py:53` 的 `PREVIEWABLE_MEDIA_TYPES = frozenset({'images', 'video', 'audio', '3d', 'text'})` 里的 `'video'` 是 jobs API 用于预览分类的媒体类型标签，不是 `outputs` 的键名。

### 4.4 VHS 与核心的差异

VHS 的 preview dict 字段明显更多（`videohelpersuite/nodes.py:622-630`）：

```python
preview = {
    "filename": file,
    "subfolder": subfolder,
    "type": "output" if save_output else "temp",
    "format": format,            # 形如 "video/h264-mp4" 或 "image/gif"
    "frame_rate": frame_rate,
    "workflow": first_image_file,
    "fullpath": output_files[-1],
}
return {"ui": {"gifs": [preview]}, "result": ((save_output, output_files),)}
```

差异清单：

| 维度 | 核心 `SaveVideo` | `VHS_VideoCombine` |
|---|---|---|
| `outputs` 键 | `images` | `gifs` |
| `animated` 标记 | 有，`[true]` | 无 |
| `format` | 无 | 有，MIME 风格字符串 |
| `frame_rate` | 无 | 有 |
| `fullpath` | 无 | 有，**服务器本地绝对路径**，跨机不可用 |
| `workflow` | 无 | 有，指向同批次首帧 png 的文件名 |
| `type` | 固定 `output` | 随 `save_output` 在 `output` / `temp` 间切换 |
| 单帧特例 | 无 | `num_frames == 1` 且 format 含 png 时，`format` 改写为 `image/png`，`filename` 把 `%03d` 替换成 `001`（`nodes.py:631-633`） |

**接入含义：拼 `/view` URL 只能用 `filename` / `subfolder` / `type` 三个字段，这三个是两边都有的唯一交集。`fullpath` 必须忽略。**

### 4.5 `meta` 与子图

`history[id]["meta"][node_id]` 形如（`execution.py:566-571`）：

```json
{"node_id": "9", "display_node": "9", "parent_node": null, "real_node_id": "9"}
```

子图展开时 `outputs` 的键是内部节点 id，`meta.display_node` 才是用户在画布上看到的那个节点。按用户视角的节点 id 取产物时要经 `meta` 映射。

---

## 5. `/view`、`/queue`、`/interrupt`、`/upload` 与连通检查

### 5.1 `GET /view`

参数（`server.py:516-560`）：

| 参数 | 说明 |
|---|---|
| `filename` | 必填。以 `/` 开头或含 `..` 一律 400。最终会被 `os.path.basename()` 只取文件名 |
| `type` | `output`（默认）/ `input` / `temp`，映射到 `folder_paths.get_directory_by_type` |
| `subfolder` | 可选。会做 `commonpath` 校验，越界返回 403 |
| `channel` | `rgba`（默认）/ `rgb` / `a`，只对图像有意义 |
| `preview` | 形如 `webp;90` 或 `jpeg;85`，服务端转码缩略图。非 `webp`/`jpeg` 一律回落 `webp` |
| `res` | 仅见于 `openapi.yaml:4755+`，取值 64–1024，返回 JPEG 缩略图 |

产物 URL 的拼法就是把 history 里那三个字段原样塞进 query：

```
GET /view?filename=ArcReel_00001_.mp4&subfolder=video&type=output
```

`filename` 还支持 `blake3:<hash>` 形式（`server.py:524-536`），由 asset manager 解析到磁盘路径，这是前端 combo widget 用的，不是产物下载的常规路径。

命中时返回 `web.FileResponse`，`Content-Type` 由 `mimetypes.guess_type` 猜（`server.py:618-622`），并带 `X-Content-Type-Options: nosniff`。HTML/JS/CSS/SVG/XML 这类可执行内容会被强制 `Content-Disposition: attachment` 并改成 `application/octet-stream`（`server.py:632-646`）。未命中返回 404，**没有 JSON body**。

**`/view` 是流式返回文件字节，不是重定向、不返回 JSON、不返回签名 URL。** 自建实例下产物必须由 ArcReel 侧主动 GET 取回。

### 5.2 `GET /queue`

```python
# server.py:1067-1073
queue_info['queue_running'] = _remove_sensitive_from_queue(current_queue[0])
queue_info['queue_pending'] = _remove_sensitive_from_queue(current_queue[1])
```

```json
{
  "queue_running": [[12, "3fa85f64-...", {"3": {}}, {"client_id": "...", "create_time": 1789000000000}, ["9"]]],
  "queue_pending": [[13, "aa11bb22-...", {}, {}, ["9"]]]
}
```

每项是 5 元组数组，第 2 个元素（下标 1）是 `prompt_id`。**这是判断"是否还在跑"的唯一原生 HTTP 手段**（另有 `/api/jobs`）。`_remove_sensitive_from_queue` 就是 `item[:5]`（`server.py:69-71`）。

`GET /prompt` 只返回 `{"exec_info": {"queue_remaining": N}}`（`server.py:750-752` → `get_queue_info`（`server.py:1286-1291`）），`queue_remaining = len(queue) + len(currently_running)`。

### 5.3 取消语义

**三条路径，语义不同，容易搞错。**

**`POST /interrupt`**（`server.py:1163-1193`）：body 可选 `{"prompt_id": "..."}`。带 id 时先在 `currently_running` 里找，找到才调 `nodes.interrupt_processing()`；**找不到就什么都不做并返回 200**（日志里写 "skipping interrupt"）。不带 id 是全局中断，打断当前正在跑的任何 prompt。**无论哪种情况都返回裸 `web.Response(status=200)`，没有 body，无法从响应判断是否真的中断了。** 而且 `/interrupt` 对**排队中**的 prompt 完全无效，它只看 `currently_running`。

**`POST /queue` + `delete`**（`server.py:1149-1161`）：

```json
{"delete": ["aa11bb22-cc33-dd44-ee55-ff6677889900"]}
```

只从 `self.queue` 里摘掉排队项（`delete_queue_item`），**对正在跑的 prompt 无效**。同样返回裸 200 无 body。`{"clear": true}` 清空全部排队项（不影响正在跑的）。

**`POST /api/jobs/{job_id}/cancel`**（`server.py:974-990`，v0.25 起，PR #14493）：这是唯一状态无关且有返回值的取消接口。内部 `_cancel_job_by_id` 先分类再分派（`server.py:950-972`）：running 走 `interrupt_if_running`（在队列 mutex 内原子判断+中断），pending 走 `delete_queue_item`，terminal/unknown 是 no-op。

```json
{"cancelled": true}
```

`cancelled` 为 `false` 表示这次调用是 no-op（已结束或 id 未知），**不是错误，仍是 200**。批量版 `POST /api/jobs/cancel` 收 `{"job_ids": [...]}`，只要有一个真的被取消就返回 `{"cancelled": true}`（`server.py:992-1046`）。

`interrupt_if_running` 的原子性注释（`execution.py:1350-1360`）值得引用：全局 interrupt flag 在每个 prompt 开始时被重置（`execute_async` 里的 `nodes.interrupt_processing(False)`，`execution.py:733`），所以一个在消费 flag 前就结束的任务不会把中断泄漏给下一个任务。裸 `/interrupt` 没有这层保护。

**接入含义：取消必须用 `/api/jobs/{id}/cancel`（需要 ComfyUI ≥ v0.26.0，见 §9）。要兼容老版本就得自己先查 `/queue` 分类，再分别调 `/interrupt` 或 `/queue delete`，并接受这中间存在竞态。**

### 5.3.1 官方已标注的废弃意向

`openapi.yaml` 把下列端点标为 `deprecated: true`，理由都是"被 `/api/jobs` 取代"：

| 端点 | 规格位置 | 规格原文要点 |
|---|---|---|
| `POST /api/interrupt` | `openapi.yaml:3106-3117` | "Prefer the jobs-namespace cancel endpoints" |
| `POST /api/queue` | `openapi.yaml:3692+` | 同上 |
| `POST /api/history` | `openapi.yaml:2960-2966` | "Superseded by the job-management endpoints under `/api/jobs`. Planned for removal no earlier than a future major release; sunset timeline TBD." |
| `GET /api/history_v2`、`GET /api/history_v2/{prompt_id}` | `openapi.yaml:3001+` / `3046+` | "Superseded by `GET /api/jobs` / `GET /api/jobs/{job_id}`" |
| `GET /api/job/{job_id}/status` | `openapi.yaml:3139+` | "Superseded by `GET /api/jobs/{job_id}` (plural path)" |

两点重要限定：

1. **`GET /history/{prompt_id}` 本身没有被标废弃**，它甚至没被规格收录（见 §0.1）。被标废弃的是 `POST /api/history` 和 `history_v2` 系列。
2. 官方另有明确表态，新的 v2 API **不废弃**这些老路由：`comfy-org/docs` 的 `development/api-development/sdks-design.mdx` 原文说 Comfy API v2 "operates alongside existing endpoints without deprecating legacy routes like `/prompt`, `/history`, or `/ws`"。

**所以这些标记是方向性信号而非迁移最后通牒。** ArcReel 可以继续用 `/prompt` + `/history` + `/queue`，但取消路径应当优先 `/api/jobs/{id}/cancel`，且不宜在新代码里依赖 `POST /api/history` 与 `history_v2`。

### 5.4 `POST /upload/image`

multipart/form-data 字段（`server.py:397-460`）：

| 字段 | 说明 |
|---|---|
| `image` | 文件本体，必填。缺失或无文件名返回 400 |
| `type` | `input`（默认）/ `output` / `temp`，决定落盘目录 |
| `subfolder` | 子目录，做 `commonpath` 越界校验 |
| `overwrite` | 字符串 `"true"` 或 `"1"` 才生效，其余值视为不覆盖 |

官方规格声明的上传限制（`openapi.yaml:4118-4120`，`/upload/mask` 同样，`4191-4193`）：

```
- Maximum file size: 50 MB
- Maximum width/height per edge: 16384 px
- Maximum total pixel count: 64 megapixels (67108864 pixels)
```

自建实例上真正的硬限制是 aiohttp 的 `client_max_size=max_upload_size`（`server.py:249`），由 `--max-upload-size` 控制，并通过 `GET /features` 的 `max_upload_size` 字段暴露给客户端。**上面那三条像素级限制在自建 `image_upload` 的代码路径里看不到对应实现，应视为 Cloud 侧约束或规格前瞻，不能假定自建会拒绝超限文件。**

不覆盖时的行为很特别：先比对 SHA 哈希，**内容相同则直接复用已有文件并返回原名**（`compare_image_hash`，`server.py:383-395`）；内容不同才改名为 `原名 (1).ext`、`原名 (2).ext` 递增。

响应（`server.py:440-459`）：

```json
{
  "name": "ref_frame (1).png",
  "subfolder": "arcreel",
  "type": "input",
  "asset": {
    "id": "...",
    "name": "ref_frame (1).png",
    "asset_hash": "blake3:...",
    "size": 123456,
    "mime_type": "image/png",
    "tags": ["..."]
  }
}
```

**响应里是 `name` 不是 `filename`，而 `/view` 要的参数叫 `filename`。** 上传后要拼回读取 URL 必须做这个字段改名。`asset` 子对象只在 asset manager 启用时出现（`view is not None`），不能假定存在。

**视频与音频素材也走 `/upload/image`。** handler 里没有任何图像格式校验，`image_save_function` 为 `None` 时就是裸 `f.write(image.file.read())`（`server.py:435-438`）。ComfyUI 自己也是这么用的：`LoadAudio` 的候选列表来自 `folder_paths.filter_files_content_types(os.listdir(input_dir), ["audio", "video"])`（`nodes_audio.py:364`），即从 input 目录里按内容类型筛，而唯一往 input 目录写文件的公开接口就是 `/upload/image`。**没有 `/upload/video` 或 `/upload/audio` 端点。**

`POST /upload/mask` 是另一套语义：它把上传的 alpha 通道合并进 `original_ref` 指定的已有图片，不适合当通用上传用（`server.py:470-514`）。

### 5.5 连通检查与模型枚举

`GET /system_stats`（`server.py:689-740`）返回：

```json
{
  "system": {
    "os": "linux",
    "ram_total": 67430000000,
    "ram_free": 41000000000,
    "comfyui_version": "0.36.0",
    "required_frontend_version": "...",
    "python_version": "3.12.x ...",
    "pytorch_version": "2.x",
    "embedded_python": false,
    "deploy_environment": "...",
    "argv": ["main.py", "--listen"]
  },
  "devices": [
    {"name": "cuda:0 NVIDIA ...", "type": "cuda", "index": 0, "vram_total": 25400000000, "vram_free": 24100000000, "torch_vram_total": 0, "torch_vram_free": 0}
  ]
}
```

**这是最合适的连通检查端点**：无副作用、响应小、且 `system.comfyui_version` 直接给出服务端版本，可以用来做能力探测（例如判断 `/api/jobs` 是否可用）。`devices` 是数组且主设备排在第一位（`server.py:698-704` 的注释明确保证 "so existing clients that read devices[0] keep working"）。注意 `argv` 会原样暴露服务端启动参数。

`GET /object_info`（`server.py:803-814`）遍历全部 `NODE_CLASS_MAPPINGS` 返回每个节点的 `input` / `input_order` / 输出类型等。**它可以用来枚举模型，但代价很大**：模型列表是以 combo 候选值的形式嵌在 `CheckpointLoaderSimple.input.required.ckpt_name` 这类字段里的，整个响应在装了大量自定义节点的实例上可达数 MB，且 `ensure_scan_started()` 会触发 asset 扫描。

**更合适的模型枚举端点是 `GET /models`（列出模型目录类别）与 `GET /models/{folder}`（列出该类别下的文件名），`server.py:342-354`。** 只想要单个节点的规格时用 `GET /object_info/{node_class}`（`server.py:816-822`），响应仍是 `{node_class: {...}}` 单键字典，节点不存在时返回空对象 `{}` 而不是 404。

---

## 6. WebSocket `/ws` 与事件流

连接：`ws://host:8188/ws?clientId=<client_id>`（`server.py:269-276`）。`clientId` 省略时服务端生成 `uuid.uuid4().hex`，并在第一条 `status` 消息的 `sid` 字段里告诉你。**传同一个 `clientId` 重连会把旧 socket 直接从 `self.sockets` 里踢掉**（`server.py:273-274`）。

文本帧统一是 `{"type": "<event>", "data": {...}}`。已确认的事件（`execution.py` + `main.py` + `comfy_execution/progress.py`）：

| 事件 | 触发点 | 关键字段 |
|---|---|---|
| `status` | 连接建立、队列变动 | `{"status": {"exec_info": {"queue_remaining": N}}, "sid": "..."}` |
| `execution_start` | `execution.py:742` | `prompt_id`, `timestamp` |
| `execution_cached` | `execution.py:769-772` | `nodes`, `prompt_id`, `timestamp` |
| `executing` | `execution.py` 逐节点 / `main.py:374` | `{"node": "<id>", "prompt_id": "..."}`；**`node` 为 `null` 表示整个 prompt 跑完** |
| `progress_state` | `progress.py:184-185` | `{"prompt_id": "...", "nodes": {node_id: {...}}}` |
| `progress` | 节点实现进度钩子时 | `node`, `prompt_id`, `value`, `max`（官方 comms_messages 页列出） |
| `notification` | 面向用户的状态文案 | `value` 为文本，如 "Executing workflow..."（仅 Cloud 文档列出） |
| `executed` | `execution.py:576` | `{"node", "display_node", "output", "prompt_id"}`，`output` 就是该节点的 `outputs` 片段 |
| `execution_success` | `execution.py:824` | `prompt_id`, `timestamp` |
| `execution_error` | `execution.py:712` | 见 §3.3 |
| `execution_interrupted` | `execution.py:699` | 见 §3.4 |
| `feature_flags` | `server.py:308-312` | 握手响应 |

**广播规则决定了 `client_id` 是必需的**（`execution.py:681-682`）：

```python
if self.server.client_id is not None or broadcast:
    self.server.send_sync(event, data, self.server.client_id)
```

`broadcast=True` 的只有 `execution_interrupted`（`execution.py:699`）。`execution_error`、`execution_start`、`execution_success` 全是 `broadcast=False`（`execution.py:712`/`742`/`824`），`executed` 也显式要求 `server.client_id is not None`（`execution.py:575`）。

**结论：提交 `POST /prompt` 时不带 `client_id`，或带的 `client_id` 与 WebSocket 的 `clientId` 不一致，你就收不到 `executed` / `execution_error` / `execution_success`。** 只有中断事件会广播给所有连接。这是接入时最隐蔽的一个坑。

### 6.1 二进制帧格式

类型码在 `protocol.py`：`PREVIEW_IMAGE = 1`、`UNENCODED_PREVIEW_IMAGE = 2`、`TEXT = 3`、`PREVIEW_IMAGE_WITH_METADATA = 4`。采样中间预览图走这条通道（`server.py:1336`/`1373`），与产物无关，接入时可以直接丢弃。

帧布局（官方文档中唯一给出完整格式的是 Cloud 那页，https://docs.comfy.org/development/cloud/api-reference ，形状与自建一致；整数均为大端）：

| 类型 | 布局 |
|---|---|
| `PREVIEW_IMAGE` (1) | `[0..4)` type=`0x00000001`；`[4..8)` image_type（**1=JPEG, 2=PNG**）；`[8..)` 图片字节 |
| `TEXT` (3) | `[0..4)` type=`0x00000003`；`[4..8)` node_id 长度 N；`[8..8+N)` node_id (UTF-8)；`[8+N..)` 文本 (UTF-8) |
| `PREVIEW_IMAGE_WITH_METADATA` (4) | `[0..4)` type=`0x00000004`；`[4..8)` metadata 长度 N；`[8..8+N)` metadata JSON；`[8+N..)` 图片字节 |

类型 4 的 metadata JSON 形如：

```json
{"node_id": "3", "display_node_id": "3", "real_node_id": "3", "prompt_id": "abc-123", "parent_node_id": null}
```

官方原生文档页（https://docs.comfy.org/development/comfyui-server/comms_messages ）**没有**记录二进制格式，也没有记录 `progress_state`、`notification` 与 feature_flags；`api-examples` 页里"see the Server Messages page for the binary format"的交叉引用指向一个并不存在的小节。这是官方原生文档的一处缺口。

### 6.2 feature_flags 握手

握手发生在连接建立之后（`server.py:296-315`）：服务端先发 `status`（含 `sid`），随后**只有当客户端的第一条文本消息是 `{"type": "feature_flags", "data": {...}}` 时**，服务端才存下客户端能力并回发同名消息带服务端能力。只认第一条消息（`first_message` 标志），错过就没有第二次机会。

服务端能力也可以用 `GET /features` 直接拿到（`server.py:742-748`），不必走 WebSocket。`comfy_api/feature_flags.py` 的 `_CORE_FEATURE_FLAGS` 含 `supports_preview_metadata`、`supports_model_type_tags`、`max_upload_size`（字节）、`node_replacements`、`assets` 等；CLI 可设的只有 `show_signin_button`、`enable_telemetry`、`partner_run_gate_enabled` 三个，且不能覆盖 core 标志。

**ArcReel 用不到这个握手**：它只影响服务端是否发送带 metadata 的预览帧等增强行为，不影响 `/prompt`、`/history`、`executed` 这些核心路径。需要探测能力时用 `GET /features` 更简单。

官方文档的推荐用法（`comfy-org/docs` 仓库 `development/comfyui-server/api-examples.mdx`，即 https://docs.comfy.org/development/comfyui-server/api-examples ）是 **WebSocket 等 `executing` 且 `node is None` 且 `prompt_id` 匹配，然后再去 `GET /history/{prompt_id}` 取产物**，原文注释就是 `# Execution done`。注意这个官方示例**不处理失败与中断**：`execution_error` 到来时不会有 `node: null` 的 `executing`，示例会永远阻塞。生产代码必须同时监听 `execution_error` / `execution_interrupted`。

---

## 7. 鉴权现状

### 7.1 ComfyUI 本体没有任何内建鉴权

`comfy/cli_args.py` 的全部网络相关参数只有：

```python
# comfy/cli_args.py:63-67
parser.add_argument("--listen", ...)          # 监听地址，默认 127.0.0.1
parser.add_argument("--port", type=int, default=8188, ...)
parser.add_argument("--tls-keyfile", ...)     # TLS
parser.add_argument("--tls-certfile", ...)    # TLS
parser.add_argument("--enable-cors-header", type=str, default=None, metavar="ORIGIN", nargs="?", const="*", ...)
```

**没有 `--api-key`、没有 `--auth-token`、没有 `--password`、没有任何鉴权中间件。** `server.py:233-246` 组装的中间件链只有 `cache_control`、`deprecation_warning`、`compress_body`、CORS、origin-only、block-external，全是 CORS/来源类，不是身份类。

`--multi-user`（`cli_args.py:218`）只是 per-user 存储隔离，读的是 `comfy-user` 请求头（`app/user_manager.py:59-62`），**没有任何验证**，任何客户端都能自称任意用户。它不是鉴权。

**结论：凡是暴露到内网/公网的 ComfyUI，鉴权必须由前置层提供。ArcReel 侧必须把"带什么凭据"做成可配置的请求头，而不是假定某种固定方案。**

### 7.2 CORS 与来源控制

`--enable-cors-header [ORIGIN]` 不给值时默认 `*`（`cli_args.py:67` 的 `const="*"`）。开启后的响应头（`server.py:124-127`）：

```
Access-Control-Allow-Origin: <origin>
Access-Control-Allow-Methods: POST, GET, DELETE, PUT, OPTIONS, PATCH
Access-Control-Allow-Headers: Content-Type, Authorization
Access-Control-Allow-Credentials: true
```

**`Allow-Headers` 只放行 `Content-Type` 和 `Authorization` 两个头。** 如果前置层要求 `X-API-Key` 之类的自定义头，浏览器侧的跨域预检会被挡；服务端到服务端调用不受影响。ArcReel 的后端是 server-to-server，不受这条限制，但值得记录。

不开 CORS 时默认挂 `create_origin_only_middleware`（`server.py:240`），只做 Origin/Host 同源校验，同样不是身份验证。

### 7.3 反向代理 / 登录插件下客户端要带什么

这一层没有标准，以下是按部署形态归纳的、客户端需要携带的凭据（无官方文档背书，属于部署惯例，实现时必须做成配置项）：

| 前置方案 | 客户端需带 | 备注 |
|---|---|---|
| Nginx / Caddy HTTP Basic | `Authorization: Basic base64(user:pass)` | WebSocket 握手也要带同一个头 |
| Nginx `auth_request` / API 网关 | 自定义头，常见 `X-API-Key` 或 `Authorization: Bearer <token>` | 头名完全由部署方决定 |
| Cloudflare Access / Zero Trust | `CF-Access-Client-Id` + `CF-Access-Client-Secret` | Service Token 形式 |
| Tailscale / WireGuard / VPN | 无需头，靠网络层 | 最省事，ArcReel 只要能路由到即可 |
| 社区登录插件（如 ComfyUI-Login） | 通常是 `Authorization: Bearer <token>`，token 由插件自定义端点签发 | 这类插件不在 ComfyUI 本体，升级易失效 |

**共同约束：凭据必须同时用于 HTTP 请求与 `/ws` 握手。** aiohttp 的 WebSocket 握手是标准 HTTP Upgrade，可以带自定义头；浏览器的 `WebSocket` 构造函数不能，只能靠 cookie 或 query 参数。ArcReel 后端发起连接，不受浏览器限制。

### 7.4 API Nodes 的 `COMFY_API_KEY` 是另一回事

ComfyUI 内置的 Partner / API Nodes 调用 Comfy Org 的托管模型服务，凭据通过 `auth_token_comfy_org` / `api_key_comfy_org` 这两个 hidden input 注入（`execution.py:161` 的 `SENSITIVE_EXTRA_DATA_KEYS`），并在入队时从 `extra_data` 里剥离另存（`server.py:1128-1131`），不会出现在 `/queue` 与 `/history` 的响应里。`--comfy-api-base` 用于改写这些节点的目标地址。

**这套凭据是"ComfyUI 作为客户端去调用 Comfy Org"的凭据，与"ArcReel 调用 ComfyUI"完全无关。** 混淆这两者是接入时的常见错误。

---

## 8. Comfy Cloud、Comfy API v2 与自建 API 的差异

来源：`comfy-org/docs` 仓库的 `development/cloud/api-reference.mdx`、`development/cloud/overview.mdx`、`development/cloud/openapi.mdx`、`development/comfy-api/overview.mdx`、`api-reference/v2/overview.mdx`、`development/api-development/sdks.mdx`、`development/api-development/sdks-design.mdx`、`snippets/cloud/*.mdx`。

### 8.1 官方现在有三套 API，别搞混

`development/comfy-api/overview` 原文：

> The public API for running ComfyUI headlessly has evolved over time. For new integrations, use **Comfy API v2**. It is supported on open-source ComfyUI (during the beta, via the API Proxy), Comfy Cloud, and Comfy API deployments... Older versions include the **v1 Cloud API**, which is deprecated.

| | 自建原生 server API | v1 Cloud API | Comfy API v2 |
|---|---|---|---|
| 形态 | `/prompt`、`/history`、`/view`、`/ws`（含 `/api/` 别名） | `cloud.comfy.org/api/*`，**刻意与原生同名同形状** | `/api/v2/jobs`、`/api/v2/assets`，全新形状 |
| 状态 | 现役 | **已标 Deprecated** | Beta（0.1.x） |
| 鉴权 | 无内建 | `X-API-Key` | `Authorization: Bearer <api-key>` |

v1 Cloud API 页顶部 Warning 原文：

> **Deprecated:** The v1 Cloud API is deprecated in favor of Comfy API v2. ... Some endpoints are maintained for compatibility with local ComfyUI but may have different semantics (e.g., ignored fields).

Comfy API v2 的三个接入面（`api-reference/v2/overview`）：

| Surface | URL | Authentication |
|---|---|---|
| Comfy Cloud | `https://cloud.comfy.org` | `Authorization: Bearer <api-key>` |
| Comfy API deployment | `https://{deployment}.run.comfy.app` | `Authorization: Bearer <api-key>` |
| Self-hosted, via comfy-api-proxy | `http://127.0.0.1:8189` | None by default, optional static bearer token |

**关键限定：v2 不废弃自建的老路由。** `development/api-development/sdks-design.mdx` 原文说 v2 "operates alongside existing endpoints **without deprecating legacy routes** like `/prompt`, `/history`, or `/ws`"。自建场景下 v2 还得靠一个独立进程 `comfy-api-proxy` 提供（`pip install comfy-api-proxy`，代理 8188、自身监听 8189），官方自己称它是 "a stopgap: once v2 stabilizes it moves into ComfyUI core"。

### 8.2 自建 vs Comfy Cloud（v1 形状）逐项对照

| 维度 | 自建原生 ComfyUI | Comfy Cloud |
|---|---|---|
| Base URL | `http://<host>:8188` | `https://cloud.comfy.org` |
| 鉴权 | 无内建，靠前置层 | `X-API-Key` 头（v2 是 Bearer） |
| 提交 | `POST /prompt`，可带 `client_id` / `prompt_id` / `number` / `front` | `POST /api/prompt`，文档示例只有 `{"prompt": ...}` |
| 提交响应 | `{prompt_id, number, node_errors}` | `{prompt_id}` |
| 查状态 | 无专用端点；`/history/{id}` 跑完前返回 `{}`，要靠 `/queue` 或 WS | `GET /api/job/{prompt_id}/status` → `{"status": "..."}` |
| 状态取值 | `status_str` 只有 `success` / `error` | 终态 `success` / `error` / `non_retryable_error` / `lost` / `cancelled`；在途 `submitted` / `queued_waiting` / `preparing` / `executing` / `cancel_requested` |
| 中断与失败的区分 | 都是 `status_str: "error"`，靠 `messages` 里的事件名区分 | 直接是两个不同的 status 值 |
| 取产物 | `GET /history/{id}` 的 `outputs` | `GET /api/jobs/{prompt_id}` 的 `job.outputs` |
| **产物里的视频键** | 只有 `images`（`SaveVideo` 也走 `images`） | 官方下载示例遍历 `["images", "video", "audio"]`，**Cloud 侧存在 `video` 键** |
| 下载 | `GET /view?...` 直接流式返回文件字节 | `GET /api/view?...` 返回 **302 重定向到对象存储签名 URL** |
| 实时推送 | WebSocket `/ws?clientId=...` | WebSocket `wss://cloud.comfy.org/ws?clientId={uuid}&token={api_key}`，**token 走 query 不是 header** |
| webhook | 无 | 无。v2 明确把 `webhook_url` 列为 "Reserved for post-MVP and rejected if present today" |
| 上传的 `subfolder` | 真实子目录 | "accepted for API compatibility but **ignored** in cloud storage. All files are stored in a flat, content-addressed namespace." |
| 取消 | `/api/jobs/{id}/cancel` 或 `/interrupt` + `/queue delete` | `POST /api/queue {"delete": [id]}` 按 ID 取消 |
| 错误码 | 只有 200 / 400 | 另有 401（key 无效/缺失）、**429（订阅未激活）**、402（额度不足） |
| 执行错误类型 | Python 异常类型字符串 | `ValidationError` / `ModelDownloadError` / `ImageDownloadError` / `OOMError` / `InsufficientFundsError` / `InactiveSubscriptionError` |
| Partner Nodes 凭据 | `extra_data.api_key_comfy_org` | 同样要在 body 里再带一次 |
| 自定义节点 | 任意安装 | 只能用预装的（`get_started/cloud.mdx`） |
| 模型 | 任意本地模型 | 预装模型 + 从 Civitai 导入 LoRA |

**Cloud 的 WebSocket 事件与自建同形**（`status` / `notification` / `execution_start` / `executing` / `progress` / `progress_state` / `executed` / `execution_cached` / `execution_success` / `execution_error` / `execution_interrupted`），这也是本报告 §6.1 二进制帧格式的来源。Cloud 文档注明 "The `clientId` parameter is currently ignored—all connections for a user receive the same messages."，与自建按 `client_id` 定向推送的语义相反。

### 8.3 签名 URL 与 API key 泄漏

官方 Python 示例的注释（`snippets/cloud/complete-example.mdx`）：

```python
# Read the redirect instead of following it. requests keeps custom
# headers across hosts, which would send the key to storage.
view_res = requests.get(..., headers={"X-API-Key": API_KEY}, allow_redirects=False)
```

对应的 curl 版原文是 "Step 1: read the 302 target. Do not use `-L` here: following the redirect would resend your API key to the storage host."

**跟随重定向会把 API key 泄漏给对象存储主机，必须手动取 `Location` 再发无头请求。** v2 的 `GET /api/v2/assets/{id}/content` 同理：自建直接返回字节，Cloud 与 serverless 返回 302 到新签名 URL。

### 8.4 三个容易混淆的服务面

- `https://cloud.comfy.org` — Comfy Cloud，跑工作流的托管服务。
- `https://api.comfy.org` — Comfy Org 平台 API：节点 registry、API/Partner Nodes 的上游与计费、Comfy Router（`POST /v2/models/{provider}/{model}` 直接调模型）。**与跑工作流不是一回事**，尽管 API key 是同一把（platform.comfy.org 生成）。它也是 ComfyUI `--comfy-api-base` 的默认值。
- `http://127.0.0.1:8189` — 自建场景下 `comfy-api-proxy` 暴露的 v2 API。

**接入含义：Cloud 与自建的差异大到无法用同一个客户端实现覆盖。提交体、状态查询、产物键名、下载方式、WebSocket 鉴权五处全都不同。若两者都要支持，必须在适配层分叉，而不是靠 base URL 切换。若未来要统一，正确的方向是 Comfy API v2 而不是 v1 Cloud 形状，但 v2 目前是 Beta 且自建需额外部署 proxy。**

---

## 9. 近一年（2025-09 至 2026-09）的版本变动

当前 master 快照对应 v0.36.0（2026-09-15）。以下变动经 commit 历史与跨 tag 源码比对确认。

| 变动 | 首次出现 | 依据 |
|---|---|---|
| `/api/` 前缀 | **远早于一年前**，2023-02-21 commit `a52aa9f4` "Moved api out to server" 起就在 | `server.py` 提交历史。近一年无变化，新老路径都支持 |
| `POST /interrupt` 接受 `prompt_id` | commit `464ba1d6`（2025-09-02，PR #9607） | 之前只有全局中断 |
| 统一 jobs API `/api/jobs` | commit `1ca89b81`（2025-12-18，PR #11054），**首个发布是 v0.6.0（2025-12-24）**，release body 原文 "Unified jobs API with /api/jobs endpoints for workflow monitoring" | `comfy_execution/jobs.py` 的首个提交 + release notes |
| `openapi.yaml` 官方规格引入 | PR #13397，**v0.20.1（2026-04-27）**，"Add OpenAPI 3.1 specification for ComfyUI API"（文件里实际写的是 `openapi: 3.0.3`） | release notes |
| `/api/jobs` 增加文本预览支持 | v0.16.0（2026-03-05，PR #12169） | release notes |
| assets 哈希改为选择性开启 `--enable-asset-hashing`（默认关） | v0.27.0（2026-06-30，PR #14663） | release notes |
| 通用 `--feature-flag` 与 `--list-feature-flags` | v0.21.0（2026-05-11，PR #13685） | release notes |
| `/history` 与 `/queue` 的 `prompt` 字段加 `create_time` | commit `2fde9597`（2025-11-13，PR #10741） | 老版本的 `extra_data` 里没有 `create_time` |
| 从 queue API 移除 Comfy API key | commit `8cf2ba4b`（2025-10-28，PR #10502） | 即现在的 `SENSITIVE_EXTRA_DATA_KEYS` 剥离机制 |
| `/jobs` 增加 `cancelled` 过滤 | commit `04c49a29`（2026-01-09，PR #11680） | |
| Asset 支持（upload 响应多出 `asset` 子对象、`/view?filename=blake3:...`） | commit `1dc3da63`（2026-01-09，PR #11315），后续多次演进至 2026-09 | `server.py` 提交历史中 `feat(assets)` 系列 |
| CORS 响应加 `PATCH` 方法 | commit `3f512f56`（2025-12-03，PR #11066） | |
| `POST /prompt` 支持客户端自带 `prompt_id` | commit `e5b7140d`（2026-06-10，PR #13998），**首个含该能力的发布是 v0.25.0（2026-06-16）** | 逐 tag 比对 `client_prompt_id` 出现：v0.24.0 无，v0.25.0 有 |
| jobs 命名空间取消端点 `POST /api/jobs/{id}/cancel`、`POST /api/jobs/cancel` | commit `4e716f7c`（2026-06-19，PR #14493），**首个发布是 v0.26.0（2026-06-23）** | |
| `/system_stats` 暴露 `deploy_environment` | commit `b664349a`（2026-06-13，PR #14402） | |
| `Comfy-Usage-Source` 请求头透传 | commit `bc5f8eca`（2026-06-12，PR #14404） | 写进 `extra_data["comfy_usage_source"]`（`server.py:1124-1127`） |
| `executed` WS 消息带 asset id | commit `ce200c08`（2026-06-11，PR #13862） | |
| 音频节点合并为 `SaveAudioAdvanced` | commit `ab0d8a92`（2026-06-05，PR #13871） | 老的 `SaveAudio`/`SaveAudioMP3`/`SaveAudioOpus` 仍在 |
| `workflow_id` 进所有执行期 WS 消息 | 加入后**又被回滚**：`4f601898`（2026-05-14，PR #13684）→ `616cab4f`（2026-05-14，PR #13901） | **不要依赖 WS 消息里的 `workflow_id`** |

### 视频输出字段的变动

**结论：核心视频产物的 `outputs` 键名近一年没有变过，一直是 `images` + `animated`。**

- `SaveWEBM` 于 2025-02-19 加入，`SaveVideo` 与 VIDEO 内置类型于 2025-04-29（PR #7844）加入，两者都早于调研窗口。
- 逐 tag 比对 `nodes_video.py` 中 `PreviewVideo` 的出现次数：v0.20.1 到 v0.33.1 一直是 2 处，v0.36.0 升到 7 处。新增的 5 处是**输入侧与中间态视频的预览**（`preview_input_video` 走 `FolderType.input`、`save_video_preview` 走 `FolderType.temp`，`nodes_video.py:386-414`，被 `VideoSlice`、裁剪等节点使用），产出的键名仍是 `images`，只是 `type` 变成 `input` / `temp`。
- **这意味着新版本里 `outputs[...]["images"]` 中的项，`type` 不再必然是 `output`。** 拼 `/view` URL 时必须逐项读 `type`，不能硬编码 `type=output`。

`/api/` 前缀在近一年完全稳定，无废弃计划。倒是 2025-10-16 加了 "deprecated API alert"（commit `4054b4bf`，PR #10366）与 2025-11-21 的 `--disable-api-nodes` CSP 变更（`532938b1`，PR #10829），但都不影响本报告涉及的端点。

---

## 10. 对 ArcReel 接入的直接结论

以下每条都对运行时设计有约束力，按影响面排序。

1. **跑完前 `/history/{id}` 返回 `{}`，且与"id 不存在"无法区分。** 轮询 `/history` 判完成是可行的，但必须用 `/queue`（或 `/api/jobs/{id}`）同时确认任务仍在系统里，否则一个被丢弃的 id 会让轮询永远转下去。`MAXIMUM_HISTORY_SIZE = 10000` 意味着高吞吐实例上早期任务的 history 会被静默淘汰，轮询必须有超时。

2. **失败与中断在 `status` 三字段上完全同形，都是 `status_str: "error"` + `completed: false`。** 唯一判据是 `status.messages` 里末尾事件是 `execution_error` 还是 `execution_interrupted`。ComfyUI 官方在 `comfy_execution/jobs.py:222-243` 就是这么判的，照抄这段逻辑，不要自己发明。同时 `status` 本身可能为 `null`，读取要防空。

3. **产物 URL 只能用 `filename` / `subfolder` / `type` 三字段拼 `GET /view`。** 这是核心节点与 VHS 唯一的字段交集。`type` 必须逐项读取，不能硬编码 `output`（新版本的中间态视频预览是 `temp`/`input`）。VHS 的 `fullpath` 是服务器本地绝对路径，跨机无意义，必须忽略。`/view` 返回文件字节流，不是 JSON 也不是签名 URL，产物必须由 ArcReel 主动拉取并落到自己的存储。

4. **核心 `SaveVideo` 的视频落在 `outputs[node]["images"]` 下，不是 `videos`。** 产物解析必须同时扫 `images`（核心图片与视频）、`gifs`（VHS 视频）、`audio`（核心音频）三个键，并靠 `filename` 扩展名而非键名判断媒体类型。`animated: [true]` 只是辅助信号，不能当作视频的充分条件（多帧 `PreviewImage` 也会是 `true`）。`outputs` 里不存在 `videos` 键。

5. **提交时必须带 `client_id`，且与 WebSocket 的 `clientId` 严格一致，否则收不到 `executed` / `execution_error` / `execution_success`。** 只有 `execution_interrupted` 是广播的。若走纯轮询不订阅 WS，可以不带 `client_id`，但那就彻底失去了进度与实时失败信号。

6. **自带 `prompt_id` 可用（需 ≥ v0.25.0），但必须是规范小写连字符 UUID，否则 400。** 这能让 ArcReel 用自己的任务 id 直接当 ComfyUI 的 job id，省掉一层映射。要用就必须做版本门限，并在低版本上回落到读响应里的 `prompt_id`。服务端不去重相同 id，重复提交会产生两条队列项且互相覆盖 history。

7. **取消要分版本。** ≥ v0.26.0 用 `POST /api/jobs/{id}/cancel`，它状态无关、原子、幂等，且返回 `{"cancelled": bool}`。低版本只能自己先查 `/queue` 分类，running 调 `POST /interrupt {"prompt_id": ...}`，pending 调 `POST /queue {"delete": [...]}`，两者都返回裸 200 无 body，无法确认是否生效，且存在"查完到发命令之间任务状态变了"的竞态。

8. **`POST /prompt` 返回 200 不代表整图无误。** 只要有一个输出节点校验通过就入队，其余失败节点出现在 200 响应的 `node_errors` 里。要做严格校验就必须在 200 时也检查 `node_errors` 是否为空。

9. **上传统一走 `POST /upload/image`，视频和音频也是。** 没有 `/upload/video` 或 `/upload/audio`。响应字段叫 `name`，而 `/view` 的参数叫 `filename`，中间要改名。不传 `overwrite=true` 时服务端会做哈希去重或自动改名为 `原名 (1).ext`，**必须以响应里的 `name` 为准**，不能假定就是你上传的文件名。

10. **鉴权必须做成可配置的自定义请求头，且同时作用于 HTTP 与 `/ws` 握手。** ComfyUI 本体零鉴权，`--multi-user` 的 `comfy-user` 头不做任何验证，不是鉴权。前置层用什么头完全由部署方决定，不能写死任何一种。ComfyUI 的 `COMFY_API_KEY` / `--comfy-api-base` 是 ComfyUI 去调 Comfy Org 的凭据，与 ArcReel 调 ComfyUI 无关，不要混淆。

11. **连通检查用 `GET /system_stats`**，无副作用且 `system.comfyui_version` 直接给版本号，正好用来驱动上面第 6、7 条的能力门限。**模型枚举用 `GET /models/{folder}` 而不是 `GET /object_info`**，后者在装了大量自定义节点的实例上响应可达数 MB 且会触发 asset 扫描。

12. **Comfy Cloud 与自建不能共用一套客户端。** 差异有五处：提交体、状态查询（Cloud 有专用 `/api/job/{id}/status`，五个终态枚举）、**产物键名（Cloud 有 `video` 键，自建没有）**、下载（Cloud 的 `/api/view` 返回 302 到签名 URL，跟随重定向会泄漏 API key）、WebSocket 鉴权（Cloud 的 token 走 query 且 `clientId` 被忽略，所有连接收到同一用户的全部消息）。必须在适配层分叉。另外注意 **v1 Cloud API 已被官方标为 deprecated**，新接入的推荐方向是 Comfy API v2（Bearer 鉴权、`/api/v2/jobs`），但 v2 目前是 Beta，自建要用还得额外跑 `comfy-api-proxy` 进程。

13. **子图场景下 `outputs` 的键是内部节点 id。** 要按用户画布上看到的节点取产物，必须经 `history[id]["meta"][node_id]["display_node"]` 映射。

14. **不要依赖 WebSocket 消息里的 `workflow_id`。** 该字段 2026-05-14 加入当天就被回滚了。

15. **官方规格 `openapi.yaml` 不能当自建契约用。** 它偏 Cloud 形状：没收录 `GET /api/history/{prompt_id}`、`PromptRequest` 里没有 `prompt_id`、把 `/api/interrupt` 描述成"取消当前认证用户的第一个活跃任务且不接受 body"（自建实现明明读 body 里的 `prompt_id`）。涉及自建行为一律以 `server.py` / `execution.py` 为准。规格的价值在于两点：上传限制等实现里不直接体现的约束，以及官方的废弃意向。

16. **废弃标记是方向性信号，不是最后通牒。** `POST /api/interrupt`、`POST /api/queue`、`POST /api/history`、`history_v2` 系列、`GET /api/job/{id}/status` 都被标 `deprecated: true`，理由是被 `/api/jobs` 取代；但 `GET /history/{prompt_id}` 本身没有被标废弃，且官方明确说 v2 "operates alongside existing endpoints without deprecating legacy routes like `/prompt`, `/history`, or `/ws`"。ArcReel 继续用 `/prompt` + `/history` + `/queue` 是安全的，只是取消路径应优先 `/api/jobs/{id}/cancel`，新代码不要依赖 `POST /api/history` 与 `history_v2`。

17. **上传限制以 `GET /features` 的 `max_upload_size` 为准。** 自建的真实硬限制是 aiohttp 的 `client_max_size`（由 `--max-upload-size` 控制，`server.py:248-249`），并通过 feature flags 暴露。规格里那三条像素级限制（50 MB / 16384 px / 64 MP）在自建代码路径里没有对应实现，不要假定服务端会替你挡住超限文件。

18. **`asset` 字段与 blake3 能力都是选择性开启的。** upload 响应里的 `asset` 子对象只在 `--enable-assets` 时出现（默认关闭），而 asset 哈希自 v0.27.0 起还要额外的 `--enable-asset-hashing`（也默认关闭）。解析上传响应必须按可选字段处理，不能依赖 `asset.asset_hash` 存在。

---

## 11. 未能核实的问题

- **Comfy Cloud 的 `POST /api/prompt` 是否接受 `client_id` / `prompt_id` / `number` / `front`。** 文档示例的请求体只有 `{"prompt": ...}`，未列出其他字段，也未说明不支持。
- **`openapi.yaml` 里那三条上传像素限制（50 MB / 16384 px / 64 MP）在自建实例上是否真的生效。** 自建 `image_upload` 的代码路径里看不到对应校验，只有 aiohttp 的 `client_max_size`。倾向于是 Cloud 侧约束，但没有明确的一手表述把两者分开。
- **官方原生文档缺口**：`comms_messages` 页没有记录二进制帧格式、`progress_state`、`notification` 与 feature_flags 握手；`api-examples` 页指向的 "Server Messages page 的 binary format 小节"并不存在。本报告 §6.1 的帧布局取自 Cloud API Reference 页，与自建 `protocol.py` 的类型码一致，但**自建侧的逐字节布局没有官方文档背书**。
- **docs.comfy.org 上没有原生 API 的逐端点 request / response JSON 示例页。** 本报告 §2、§3 的 JSON 示例是依据 `server.py` / `execution.py` 的实际构造代码推出的，不是抄自官方示例。`GET /history/{prompt_id}`、`GET /object_info`、`GET /queue`、`GET /system_stats` 的官方响应示例均未找到一手来源。
- **Comfy Cloud 产物签名 URL 的有效期。** 文档只说是重定向到存储，未给 TTL。
- **`POST /prompt` 重复使用同一个 `prompt_id` 的官方语义。** §2.3 的结论是从源码推出的（无去重逻辑、后完成者覆盖 history），没有文档或测试背书。
- **社区登录插件的具体头名。** §7.3 的表格是按部署惯例归纳的，除 Cloudflare Access 外没有 ComfyUI 官方来源，实现时必须做成配置项而不是内置预设。
