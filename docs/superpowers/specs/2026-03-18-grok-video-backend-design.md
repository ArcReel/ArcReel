# GrokVideoBackend 接入设计

> Issue: [#100](https://github.com/ArcReel/ArcReel/issues/100) — grok-imagine-video 接入
> 父任务: [#98](https://github.com/ArcReel/ArcReel/issues/98) — 多供应商视频生成
> 前置依赖: [#99](https://github.com/ArcReel/ArcReel/issues/99) — 提取通用视频生成服务层（已完成）

## 范围

纯后端接入。前端配置页改动留给 #102（供应商管理页）。

## 1. GrokVideoBackend 实现

新建 `lib/video_backends/grok.py`。

### SDK 选择

使用 `xai_sdk` 官方 Python SDK。通过 `xai_sdk.AsyncClient` 调用 `grok-imagine-video` 模型。

### 模型常量

```python
DEFAULT_MODEL = "grok-imagine-video"
```

### 初始化

```python
def __init__(self, *, api_key: str | None = None, model: str | None = None):
```

- `api_key`：从构造参数传入（来源于 WebUI 配置页写入的 `XAI_API_KEY` 环境变量）
- 创建 `xai_sdk.AsyncClient(api_key=api_key)`

### 能力集

```python
{VideoCapability.TEXT_TO_VIDEO, VideoCapability.IMAGE_TO_VIDEO}
```

不支持：`GENERATE_AUDIO`、`NEGATIVE_PROMPT`、`VIDEO_EXTEND`、`SEED_CONTROL`、`FLEX_TIER`。

### generate() 流程

1. 构建参数：prompt、aspect_ratio、duration（1-15 秒连续值，直接传入）
2. 若有 `start_image`：读取本地文件，base64 编码为 `data:image/{ext};base64,{data}`
3. 调用 `client.video.generate(prompt=..., image_url=..., aspect_ratio=..., duration=...)`
4. SDK 自动处理轮询，返回结果包含临时视频 URL
5. 用 `httpx.AsyncClient` 异步下载视频到 `output_path`
6. 返回 `VideoGenerationResult(video_path=output_path, provider="grok", model=model, duration_seconds=...)`

### 分辨率

从 `VideoGenerationRequest.resolution` 读取（由调用方从 `video_model_settings` 注入）。仅支持 `480p` / `720p`。

## 2. 注册与工厂

### base.py

新增常量：

```python
PROVIDER_GROK = "grok"
```

### __init__.py

新增注册：

```python
from lib.video_backends.grok import GrokVideoBackend
register_backend(PROVIDER_GROK, GrokVideoBackend)
```

与 Gemini/Seedance 一致，模块加载时自动注册。

### generation_tasks.py

`_get_or_create_video_backend()` 新增 `grok` 分支：

```python
elif provider_name == PROVIDER_GROK:
    kwargs = {
        "api_key": os.environ.get("XAI_API_KEY"),
        "model": provider_settings.get("model"),
    }
```

沿用 `(provider_name, model)` 缓存策略。

## 3. 计费与用量追踪

### CostCalculator

新增 Grok 计费字典和方法：

```python
GROK_VIDEO_COST = {
    "grok-imagine-video": 0.050,  # USD/秒，不区分分辨率
}

def calculate_grok_video_cost(duration_seconds: int, model: str) -> float:
    per_second = GROK_VIDEO_COST.get(model, 0.050)
    return duration_seconds * per_second
```

货币：USD（与 Gemini 一致）。

### UsageRepository

`finish_call()` 新增 `PROVIDER_GROK` 分支：

- 使用 `duration_seconds`（从 `VideoGenerationResult` 提取）× 单价计算费用
- 不依赖 `usage_tokens`（Grok 按秒计费）

## 4. 配置管理

### SystemConfigManager

`_ENV_KEYS` 新增：

```python
"XAI_API_KEY"
```

`DEFAULT_VIDEO_PROVIDER` 合法值扩展为 `gemini | seedance | grok`。

### 分辨率：模型级子配置

分辨率从 `video_model_settings.{model}.resolution` 读取，而非全局或供应商级别。

配置结构示例（`.system_config.json`）：

```json
{
  "video_model_settings": {
    "veo-3.1-generate-001": {
      "resolution": "1080p"
    },
    "doubao-seedance-1-5-pro-251215": {
      "resolution": "720p"
    },
    "grok-imagine-video": {
      "resolution": "720p"
    }
  }
}
```

`generation_tasks.py` 构建 `VideoGenerationRequest` 时，根据当前选中模型名从 `video_model_settings` 取对应分辨率并注入 `request.resolution`。各模型默认值：

| 模型 | 默认分辨率 |
|------|-----------|
| veo-3.1-* | 1080p |
| seedance-1.5-* | 720p |
| grok-imagine-video | 720p |

## 5. 测试策略

### 单元测试

`tests/test_grok_video_backend.py`：

- mock `xai_sdk.AsyncClient`，验证 `generate()` 正确构建参数并返回 `VideoGenerationResult`
- text-to-video 路径：验证 prompt、aspect_ratio、duration 正确传递
- image-to-video 路径：验证本地图片被 base64 编码并传递
- 不支持的能力（如 `generate_audio`）被正确忽略

### 计费测试

`tests/test_cost_calculator.py`：

- 新增 Grok 计费用例：验证 `calculate_grok_video_cost()` 按秒计费

### 不新增

- 集成测试（需要真实 API Key）
- 前端测试（本次不涉及前端改动）

## 涉及文件清单

| 文件 | 操作 |
|------|------|
| `lib/video_backends/grok.py` | 新增 |
| `lib/video_backends/base.py` | 修改（新增 `PROVIDER_GROK`） |
| `lib/video_backends/__init__.py` | 修改（注册 Grok） |
| `lib/cost_calculator.py` | 修改（新增 Grok 计费） |
| `lib/system_config.py` | 修改（新增 `XAI_API_KEY`） |
| `server/services/generation_tasks.py` | 修改（工厂 + 分辨率注入） |
| `tests/test_grok_video_backend.py` | 新增 |
| `tests/test_cost_calculator.py` | 修改（新增用例） |
