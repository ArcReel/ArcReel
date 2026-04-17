# NewAPI 自定义供应商格式设计

**日期**：2026-04-15
**作者**：Pollo（协助：Claude Code）
**状态**：设计草案

## 背景

ArcReel 现有 `lib/custom_provider/` 支持两种 `api_format`：`openai` 和 `google`，分别复用 `OpenAI*Backend` / `Gemini*Backend` 作为 delegate。

用户希望新增 **NewAPI**（`https://docs.newapi.pro/`）作为第三种协议格式。NewAPI 是一个 AI 聚合网关，将 Sora、Kling、即梦、Wan、Veo 等多家视频模型通过**统一的 `/v1/video/generations` 端点**聚合，依靠 `model` 字段分发。这与 ArcReel "一个接口覆盖多模型" 的业务诉求天然契合，相比为每家模型写专用 backend 更有扩展性。

文本和图片部分，NewAPI 原生是 OpenAI 兼容（`/v1/chat/completions` 和 `/v1/images/generations`），无需新写 backend。

## 目标

1. 新增 `api_format = "newapi"`，与 `openai` / `google` 并列
2. 文本/图片复用 `OpenAITextBackend` / `OpenAIImageBackend`（OpenAI 兼容）
3. 视频新增 `NewAPIVideoBackend`，对接 NewAPI 统一视频协议
4. 自定义供应商 UI 协议下拉菜单增加 "NewAPI" 选项
5. 模型发现复用 OpenAI 兼容 `/v1/models` 查询

## 非目标

- 不做 NewAPI 预置（preset）条目，不填默认 base_url
- 不支持 Midjourney/可灵/即梦等厂商专用路径（都走统一端点）
- 不支持 `n > 1` 多视频并发生成（业务每镜头 1 个视频）
- 不改动现有 `openai` / `google` 格式的任何行为

## NewAPI 视频协议摘要

### 创建任务
- `POST /v1/video/generations`
- Request body（`application/json`）：
  - `model*` (string)：模型 ID，如 `kling-v1`、`sora-2`、`veo-3` 等
  - `prompt*` (string)：文本描述
  - `image?` (string)：URL 或 Base64 字符串（图生视频）
  - `duration?` (number)：秒数
  - `width?`, `height?` (integer)
  - `fps?` (integer), `seed?` (integer), `n?` (integer)
  - `response_format?`, `user?` (string)
  - `metadata?` (object)：扩展参数（`negative_prompt` / `style` / `quality_level` 等）
- Response：`{task_id, status}`

### 查询任务
- `GET /v1/video/generations/{task_id}`
- Response：
  - `task_id`, `status` (enum: `queued` / `in_progress` / `completed` / `failed`)
  - `url` (string)：完成时的视频下载链接
  - `format` (string)：如 `"mp4"`
  - `metadata` (object)：`{duration, fps, width, height, seed}`
  - `error` (object)：`{code, message}`（失败时）

## 架构设计

### 1. 数据模型

`lib/db/models/custom_provider.py`：
```python
api_format: Mapped[str]  # "openai" | "google" | "newapi"
```

数据库侧无 CHECK 约束（字段为 `String(32)`，迁移 `5105eceaf7cb` 已确认），**无需新建 Alembic 迁移**，仅更新注释。校验在应用层完成（factory / router）。

### 2. Factory 分支

`lib/custom_provider/factory.py`：
- `_VALID_API_FORMATS` 增加 `"newapi"`
- 新增 `_create_newapi_backend`：
  - `text` / `image` → 走 OpenAI delegate（base_url 经 `ensure_openai_base_url` 规范化）
  - `video` → 返回 `CustomVideoBackend(delegate=NewAPIVideoBackend(...))`

### 3. NewAPIVideoBackend（新建）

`lib/video_backends/newapi.py`：

```python
class NewAPIVideoBackend:
    name = "newapi"
    video_capabilities = VideoCapabilities(
        reference_images=False,  # schema 只支持单个 image 字段
        max_reference_images=1,
    )
    capabilities = {VideoCapability.TEXT_TO_VIDEO, VideoCapability.IMAGE_TO_VIDEO}

    def __init__(self, *, api_key, base_url, model): ...

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        # 1) 编码 start_image (Base64 data URI)
        # 2) POST /v1/video/generations → task_id
        # 3) 轮询 GET /v1/video/generations/{task_id} 直到 completed/failed
        # 4) httpx.get(url) 下载到 output_path
```

关键实现要点：

| 维度 | 做法 |
|---|---|
| HTTP 客户端 | `httpx.AsyncClient(timeout=60)`，`Authorization: Bearer <api_key>` |
| base_url 规范化 | 复用 `ensure_openai_base_url`（NewAPI 的 OpenAI 兼容路径同一前缀） |
| image 编码 | 读本地文件 → Base64 → `f"data:image/{mime};base64,{b64}"` 形式 |
| reference_images | 有值时记录 warning 并丢弃（capabilities 已声明不支持） |
| 尺寸映射 | 新增 `_SIZE_MAP: {(resolution, aspect_ratio): (width, height)}`，默认回退 `720x1280` |
| 轮询 | 初始 5 秒间隔，固定间隔轮询；最大超时 = `max(600, duration_seconds * 30)` |
| 状态机 | `queued`/`in_progress`→ 继续；`completed` → 取 `url`；`failed` → `raise RuntimeError(error.message)` |
| 重试 | `with_retry_async` 分三段装饰：`_create_task` / `_poll_once` / `_download_content` |
| 可重试错误 | 复用或新增 `NEWAPI_RETRYABLE_ERRORS`（httpx 网络异常 + 5xx） |
| 结果字段 | `VideoGenerationResult(video_path, provider="newapi", model, duration_seconds=metadata.duration, task_id)` |

### 4. 模型发现

`lib/custom_provider/discovery.py`：
- `discover_models(api_format="newapi", ...)` → 复用 `_discover_openai`（NewAPI 的 `/v1/models` 是 OpenAI 兼容）
- `_VIDEO_PATTERN` 已包含 `kling` / `wan` / `veo`，可追加 `minimax` / `hailuo` / `seedream` 等 NewAPI 常见 ID

### 5. 前端

`frontend/src/components/pages/settings/CustomProviderForm.tsx`：
```typescript
type ApiFormat = "openai" | "google" | "newapi";
API_FORMAT_OPTIONS = [
  { value: "openai", label: "OpenAI" },
  { value: "google", label: "Google" },
  { value: "newapi", label: "NewAPI" },
];
```
同步更新 `frontend/src/types/custom-provider.ts` 的类型联合。

### 6. i18n

- 后端：**实现时发现仓库实际使用 `unsupported_format` 参数化文案**（`不支持的 api_format: {api_format}`），`{api_format}` 会在运行时自动填入，新增 `newapi` 无需改动 `errors.py`；factory 抛出的 `ValueError(f"不支持的 api_format: {api_format!r}")` 是开发层错误，不走 i18n。原 spec 提到的 `invalid_api_format` key 在代码中不存在，此条作废
- 前端：`frontend/src/i18n/{zh,en}/dashboard.ts` — 若有 NewAPI 说明文案新增

## 错误处理

| 场景 | 处理 |
|---|---|
| 创建任务返回非 200 | 解析 `error.message`，抛 `RuntimeError` |
| 轮询超时 | 抛 `TimeoutError("视频生成超时，task_id=...")` |
| 任务 `status=failed` | 抛 `RuntimeError(error.message)` |
| 下载失败 | `DOWNLOAD_MAX_ATTEMPTS` 重试耗尽后抛 |
| 网络异常 | `with_retry_async` 指数退避 |

## 测试策略

1. **单元：`tests/test_newapi_video_backend.py`**
   - Mock `httpx.AsyncClient` 三段：create / poll(3 次) / download
   - 覆盖：纯文生、图生视频（Base64 编码）、失败状态、轮询超时
2. **Factory 单元扩展：`tests/test_custom_provider_factory.py`**
   - `newapi` + `text` → `CustomTextBackend(OpenAITextBackend)`
   - `newapi` + `image` → `CustomImageBackend(OpenAIImageBackend)`
   - `newapi` + `video` → `CustomVideoBackend(NewAPIVideoBackend)`
3. **Discovery 扩展：`tests/test_model_discovery.py`**
   - `newapi` 格式走 OpenAI 路径
4. **API 层：`tests/test_custom_providers_api.py`**
   - POST/PUT 接受 `api_format="newapi"`，校验通过

## 改动清单

| 文件 | 变更类型 |
|---|---|
| `lib/db/models/custom_provider.py` | 更新字段注释为 `openai/google/newapi` |
| `lib/custom_provider/factory.py` | 增加 `newapi` 分支 + `_create_newapi_backend` |
| `lib/custom_provider/discovery.py` | `discover_models("newapi", ...)` 复用 openai 路径；补充视频关键词 |
| `lib/video_backends/newapi.py` | **新建** NewAPIVideoBackend |
| `lib/video_backends/registry.py` | 注册（如有显式注册表） |
| ~~`lib/i18n/{zh,en}/errors.py`~~ | ~~更新 `invalid_api_format` 文案~~ （作废：key 不存在，实际使用参数化 `unsupported_format`） |
| `server/routers/custom_providers.py` | 校验允许 `newapi` |
| `frontend/src/types/custom-provider.ts` | 类型联合加 `newapi` |
| `frontend/src/components/pages/settings/CustomProviderForm.tsx` | 下拉选项加 NewAPI |
| `frontend/src/i18n/{zh,en}/dashboard.ts` | 相关文案 |
| `tests/test_newapi_video_backend.py` | **新建**，mock httpx |
| `tests/test_custom_provider_factory.py` | 扩展 newapi 用例 |
| `tests/test_model_discovery.py` | 扩展 newapi 用例 |
| `tests/test_custom_providers_api.py` | 校验 newapi 入参 |

## 风险与权衡

- **`/v1/models` 列表过长**：NewAPI 聚合多家厂商，模型数可能几十上百。UI 已支持批量编辑/禁用，用户可手动筛选。
- **轮询频率**：固定 5 秒可能在 kling 等长任务上浪费请求。考虑未来改为指数退避（5s → 15s → 30s 上限），但初版固定间隔足够。
- **`image` 字段 URL vs Base64**：本地图片只能走 Base64，可能导致请求体较大。NewAPI 若限制请求体大小需要用户提前上传到 OSS；当前不做 URL fallback。
- **API Key 轮换**：NewAPIVideoBackend 目前只接受单个 api_key；后续若与 `Credential` 多 Key 整合，需要拿到已选中的活跃 key 注入（与现有 OpenAIVideoBackend 对齐）。

## 非目标回顾

本设计不引入以下内容，需要时再开新 spec：

- NewAPI 预置条目（`lib/config/registry.py`）
- Midjourney / Kling 专用路径 backend
- 多视频并发（`n > 1`）
- 图片 backend 的 NewAPI 扩展字段（保持 DALL-E 兼容即可）
