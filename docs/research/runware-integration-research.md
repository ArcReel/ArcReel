# Runware 图像生成接入调研报告

**调研日期**：2026-08-19
**用途**：评估 Runware 作为 ArcReel 图像供应商接入（目标模型：Nano Banana Lite、Image 02）
**信息来源**：Runware 官方文档（docs.runware.ai，实时抓取），协议细节以官方页面为准

---

## 0. 结论先行

Runware 图像生成走**原生 `imageInference` 协议**（`POST https://api.runware.ai/v1`，JSON 数组，Bearer 认证），**不是** OpenAI 兼容的 `/v1/images/generations`。因此：

- ❌ **不能**直接复用 ArcReel 现有的 OpenAI 兼容自定义供应商 endpoint（`openai-images` / `openai-images-generations`）。
- ✅ 需要**新增**一个图像 backend（`lib/image_backends/runware.py`）或自定义 endpoint（`lib/custom_provider/endpoints.py` 的 `runware-image`）。
- ✅ 同步返回图片 URL（`data[].imageURL`），无需异步轮询——与现有 `agnes` / `openai` 图像 backend 的同步 POST + 解析 URL 形态同构，改造量可控。

## 1. 协议细节（官方文档一手）

### 1.1 端点与认证

- REST：`POST https://api.runware.ai/v1`
- 认证：`Authorization: Bearer <API_KEY>`（或 payload 首元素放 `{"taskType":"authentication","apiKey":"..."}`）
- `Content-Type: application/json`
- 请求体是 **JSON 数组**，每个元素一个 task 对象（支持一次提交多任务）
- WebSocket：`wss://ws-api.runware.ai/v1`（官方推荐，REST 更简单）

### 1.2 图像生成（imageInference）

同步返回，无需轮询（文本 `deliveryMethod: async` 才轮询）。

请求：

```json
[
  {
    "taskType": "imageInference",
    "taskUUID": "39d7207a-87ef-4c93-8082-1431f9c1dc97",
    "model": "<AIR_ID>",
    "positivePrompt": "a cat",
    "width": 1024,
    "height": 1024,
    "numberResults": 1
  }
]
```

响应：

```json
{
  "data": [
    {
      "taskType": "imageInference",
      "taskUUID": "39d7207a-87ef-4c93-8082-1431f9c1dc97",
      "imageUUID": "b7db282d-2943-4f12-992f-77df3ad3ec71",
      "imageURL": "https://im.runware.ai/image/os/a14d18/ws/2/ii/b7db282d-2943-4f12-992f-77df3ad3ec71.jpg"
    }
  ]
}
```

错误时无 `data`，返回 `error` 字段。

### 1.3 模型标识（AIR ID）

格式 `provider:model@version/quality`。官方文档已出现的示例：

| AIR ID | 模态 | 上游 |
|---|---|---|
| `xai:grok-imagine@image-quality` | 图像 | xAI Grok Imagine |
| `google:gemini@3.1-pro` | 文本 | Google Gemini |
| `klingai:kling-video@3-4k` | 视频 | 可灵 |
| `minimax:m2.7@0` | 文本 | MiniMax |
| `civitai:102438@133677` | 图像 | CivitAI 社区模型 |

### 1.4 OpenAI 兼容范围

Runware 的 OpenAI 兼容**仅覆盖文本**（`/v1/chat/completions`），模型同样用 AIR ID（如 `minimax:m2.7@0`）。**图像/视频/音频不走 OpenAI 兼容**，必须用原生 `taskType` 协议。

## 2. 已确认项（2026-08-19 实测）

| # | 项 | 结果 |
|---|---|---|
| 1 | Nano Banana Lite AIR ID | `google:nano-banana@2-lite`（Gemini 3.1 Flash Lite Image，实测生成成功） |
| 2 | Image 02 归属与 AIR ID | `openai:gpt-image@2`（OpenAI GPT Image 2） |
| 3 | T2I / I2I 能力 | 两模型均 `text_to_image` + `image_to_image` + `op:edit` |
| 4 | 参考图上传 | `taskType: "mediaStorage"` + `operation: "upload"`（data URI）→ `mediaUUID`（实测成功） |

### 2.1 尺寸档位（关键差异，实测）

- **Nano Banana（`google:*`）**：只支持**固定比例档位**，任意尺寸报 `unsupportedDimensions`。14 档：
  `1:1→1024x1024`、`9:16→768x1376`、`16:9→1376x768`、`4:3→1200x896`、`3:4→896x1200`、
  `21:9→1584x672`、`4:1→2048x512`、`8:1→3072x384` 等（backend `_NANO_BANANA_DIMENSIONS`）。
- **GPT Image 2（`openai:*`）**：任意尺寸，但总像素 655360~8294400、宽高被 16 整除
  （backend 用 `aspect_size(round_to=16, max_total_pixels=8294400)`）。

## 3. 集成方案（已完成）

- ✅ `lib/providers.py::PROVIDER_RUNWARE`
- ✅ `lib/runware_shared.py`（RUNWARE_API_BASE / resolve_runware_api_key / runware_base_url / runware_headers）
- ✅ `lib/image_backends/runware.py::RunwareImageBackend`（T2I + I2I + 尺寸档位 + mediaStorage 上传）
- ✅ `lib/image_backends/__init__.py` 注册 backend
- ✅ `lib/config/registry.py::PROVIDER_REGISTRY["runware"]`（Nano Banana 2 Lite + GPT Image 2）
- ⏳ 定价：Runware 按 compute/fixed price 计费、无公开单价，`pricing=None` 按 Gemini 兜底估算，待补
- ⏳ 前端：`frontend` 的 provider 图标（ICON_LOADERS）待补 Runware 图标

## 4. 参考资料（官方来源）

- https://runware.ai/docs/platform/introduction
- https://runware.ai/docs/platform/authentication
- https://runware.ai/docs/platform/openai
- https://runware.ai/docs/platform/model-search
- https://runware.ai/docs/platform/pricing
