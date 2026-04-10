# OpenAI Preset Provider Design文档

> 日期：2026-03-31 | 状态：已确认 | 分支：feature/openai-provider

## Overview

在 ArcReel 中新增 OpenAI 为第五个预置供应商，支持文本（GPT-5.4）、图片（GPT Image 1.5）、视频（Sora 2）三种媒体类型。Uses"共享模块 + 三个独立 Backend"的架构，参照现有 `gemini_shared.py` 模式新增 `openai_shared.py`。

### Scope

- OpenAI 预置供应商（文本 + 图片 + 视频）
- **不包含**自定义供应商（下一个迭代）

### 关键决策

| 决策点 | 结论 | 理由 |
|--------|------|------|
| 架构模式 | 共享 `openai_shared.py` + 三个独立 Backend | 有 `gemini_shared.py` 先例，DRY 且为自定义供应商铺路 |
| SDK | 统一使用 `openai` SDK 2.30.0 | 已在依赖中，三种媒体类型 API 均完整支持 |
| 结构化输出 | 原生 `response_format` 优先，Instructor fallback | 与 Gemini 后端策略一致 |
| 图片 API | Images API（`generate` + `edit`） | 与 `ImageBackend` Protocol 天然对齐 |
| 图片 I2I | `client.images.edit()` 传入参考图 | 支持多张参考图输入 |
| 视频 API | SDK 原生 `client.videos.create_and_poll()` | SDK 2.30.0 完整支持，内置轮询 |
| 视频 Seed | 不支持 | SDK `VideoCreateParams` 无 seed 参数 |

---

## 1. 供应商注册与常量

### `lib/providers.py`

```python
PROVIDER_OPENAI = "openai"
```

### `lib/config/registry.py`

```python
"openai": ProviderMeta(
    display_name="OpenAI",
    description="OpenAI 官方平台，支持 GPT-5.4 文本、GPT Image 图片和 Sora 视频生成。",
    required_keys=["api_key"],
    optional_keys=["base_url", "image_rpm", "video_rpm", "request_gap",
                   "image_max_workers", "video_max_workers"],
    secret_keys=["api_key"],
    models={
        # --- text ---
        "gpt-5.4":      ModelInfo("GPT-5.4",      "text",  ["text_generation", "structured_output", "vision"]),
        "gpt-5.4-mini": ModelInfo("GPT-5.4 Mini", "text",  ["text_generation", "structured_output", "vision"], default=True),
        "gpt-5.4-nano": ModelInfo("GPT-5.4 Nano", "text",  ["text_generation", "structured_output", "vision"]),
        # --- image ---
        "gpt-image-1.5":    ModelInfo("GPT Image 1.5",    "image", ["text_to_image", "image_to_image"], default=True),
        "gpt-image-1-mini": ModelInfo("GPT Image 1 Mini", "image", ["text_to_image", "image_to_image"]),
        # --- video ---
        "sora-2":     ModelInfo("Sora 2",     "video", ["text_to_video", "image_to_video"], default=True),
        "sora-2-pro": ModelInfo("Sora 2 Pro", "video", ["text_to_video", "image_to_video"]),
    },
)
```

**设计要点：**
- `optional_keys` 包含 `base_url`，为下期自定义供应商铺路
- GPT-5.4 Mini 为默认文本模型（高性价比）
- 图片支持 `text_to_image` + `image_to_image`（T2I 走 `images.generate()`，I2I 走 `images.edit()`）
- 视频支持 `text_to_video` + `image_to_video`（Sora 支持 `input_reference`）

---

## 2. `openai_shared.py` 共享模块

```python
# lib/openai_shared.py

from openai import AsyncOpenAI

OPENAI_RETRYABLE_ERRORS: tuple[type[Exception], ...] = ()

try:
    from openai import (
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
        RateLimitError,
    )
    OPENAI_RETRYABLE_ERRORS = (
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
        RateLimitError,
    )
except ImportError:
    pass


def create_openai_client(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
) -> AsyncOpenAI:
    """创建 AsyncOpenAI 客户端，统一处理 api_key 和 base_url。"""
    kwargs: dict = {}
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url
    return AsyncOpenAI(**kwargs)
```

**与 `gemini_shared.py` 的区别：**
- 不需要 `RateLimiter` — OpenAI SDK 内置重试和退避
- 不需要 `with_retry_async` — SDK `max_retries` 默认 2
- 只做客户端工厂 + 可重试错误类型导出
- 下期自定义供应商只需传入不同 `base_url` 即可复用

---

## 3. OpenAI Text Backend

### `lib/text_backends/openai.py`

```python
class OpenAITextBackend:
    def __init__(self, *, api_key=None, model=None, base_url=None):
        self._client = create_openai_client(api_key=api_key, base_url=base_url)
        self._model = model or "gpt-5.4-mini"
        self._capabilities = {
            TextCapability.TEXT_GENERATION,
            TextCapability.STRUCTURED_OUTPUT,
            TextCapability.VISION,
        }

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        messages = self._build_messages(request)
        kwargs = {"model": self._model, "messages": messages}

        if request.response_schema:
            kwargs["response_format"] = self._build_response_format(request.response_schema)

        response = await self._client.chat.completions.create(**kwargs)
        return TextGenerationResult(
            text=response.choices[0].message.content or "",
            provider=PROVIDER_OPENAI,
            model=self._model,
            input_tokens=response.usage.prompt_tokens if response.usage else None,
            output_tokens=response.usage.completion_tokens if response.usage else None,
        )
```

**关键实现细节：**

1. **消息构建** — `_build_messages()` 将 `request.prompt` / `system_prompt` / `images` 转为 OpenAI messages 格式，图片用 `{"type": "image_url", "image_url": {"url": data_uri}}`
2. **结构化输出** — `_build_response_format()` 将 Pydantic model / JSON schema 转为 `{"type": "json_schema", "json_schema": {...}}`，配合现有 `resolve_schema()` 工具
3. **Instructor fallback（后续迭代）** — 本期仅实现原生 `response_format` 结构化输出。Instructor fallback 路径作为后续优化，待确认 GPT-5.4 系列的 schema 兼容性边界后再添加
4. **Usage 容错** — `response.usage` 可能为 None（兼容服务），记为 None 不阻塞

### 注册与工厂

- `text_backends/__init__.py`: `register_backend(PROVIDER_OPENAI, OpenAITextBackend)`
- `text_backends/factory.py`: `"openai": "openai"` 映射，传入 `api_key` + `base_url` + `model`

---

## 4. OpenAI Image Backend

### `lib/image_backends/openai.py`

```python
class OpenAIImageBackend:
    def __init__(self, *, api_key=None, model=None, base_url=None):
        self._client = create_openai_client(api_key=api_key, base_url=base_url)
        self._model = model or "gpt-image-1.5"
        self._capabilities = {
            ImageCapability.TEXT_TO_IMAGE,
            ImageCapability.IMAGE_TO_IMAGE,
        }

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        if request.reference_images:
            return await self._generate_edit(request)    # I2I
        return await self._generate_create(request)      # T2I
```

**T2I** — `client.images.generate()`:

```python
async def _generate_create(self, request):
    response = await self._client.images.generate(
        model=self._model,
        prompt=request.prompt,
        size=self._map_size(request.aspect_ratio),
        quality=self._map_quality(request.image_size),
        response_format="b64_json",
        n=1,
    )
    # base64 解码 → 写入 output_path
```

**I2I** — `client.images.edit()`:

```python
async def _generate_edit(self, request):
    image_files = [open(ref.path, "rb") for ref in request.reference_images]
    try:
        response = await self._client.images.edit(
            model=self._model,
            image=image_files,
            prompt=request.prompt,
            response_format="b64_json",
        )
    finally:
        for f in image_files:
            f.close()
    # base64 解码 → 写入 output_path
```

**尺寸映射**（`aspect_ratio` → OpenAI `size`）:

| aspect_ratio | OpenAI size |
|--------------|-------------|
| `9:16` | `1024x1792` |
| `16:9` | `1792x1024` |
| `1:1` | `1024x1024` |

**质量映射**（`image_size` → OpenAI `quality`）:

| image_size | quality |
|------------|---------|
| `512PX` | `low` |
| `1K` | `medium` |
| `2K` | `high` |
| `4K` | `high` |

### 注册

- `image_backends/__init__.py`: `register_backend(PROVIDER_OPENAI, OpenAIImageBackend)`

---

## 5. OpenAI Video Backend

### `lib/video_backends/openai.py`

```python
class OpenAIVideoBackend:
    def __init__(self, *, api_key=None, model=None, base_url=None):
        self._client = create_openai_client(api_key=api_key, base_url=base_url)
        self._model = model or "sora-2"
        self._capabilities = {
            VideoCapability.TEXT_TO_VIDEO,
            VideoCapability.IMAGE_TO_VIDEO,
        }

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        kwargs = {
            "prompt": request.prompt,
            "model": self._model,
            "seconds": self._map_duration(request.duration_seconds),
            "size": self._map_size(request.aspect_ratio),
        }

        if request.start_image and Path(request.start_image).exists():
            kwargs["input_reference"] = {
                "type": "image_url",
                "image_url": self._encode_start_image(request.start_image),
            }

        video = await self._client.videos.create_and_poll(**kwargs)

        if video.status == "failed":
            raise RuntimeError(f"Sora 视频生成失败: {video.error}")

        content = await self._client.videos.download_content(video.id)
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        request.output_path.write_bytes(content.content)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_OPENAI,
            model=self._model,
            duration_seconds=int(video.seconds),
            task_id=video.id,
        )
```

**时长映射**（`duration_seconds: int` → SDK `VideoSeconds`）:

| duration_seconds | VideoSeconds |
|-----------------|--------------|
| ≤ 4 | `"4"` |
| 5-8 | `"8"` |
| ≥ 9 | `"12"` |

**尺寸映射**（`aspect_ratio` → SDK `VideoSize`）:

| aspect_ratio | VideoSize |
|--------------|-----------|
| `9:16` | `720x1280` |
| `16:9` | `1280x720` |

**不支持的能力（不标记）：**
- `GENERATE_AUDIO` — Sora 不独立控制音频
- `NEGATIVE_PROMPT` — Sora 不支持
- `SEED_CONTROL` — SDK VideoCreateParams 无 seed 参数
- `FLEX_TIER` — Sora 不支持

### 注册

- `video_backends/__init__.py`: `register_backend(PROVIDER_OPENAI, OpenAIVideoBackend)`

---

## 6. Cost Calculator 扩展

### 新增定价表

```python
# OpenAI 文本费率（USD/百万 token）
OPENAI_TEXT_COST = {
    "gpt-5.4":      {"input": 2.50, "output": 15.00},
    "gpt-5.4-mini": {"input": 0.75, "output": 4.50},
    "gpt-5.4-nano": {"input": 0.20, "output": 1.25},
}

# OpenAI 图片费率（USD/张），按 quality 区分
OPENAI_IMAGE_COST = {
    "gpt-image-1.5":    {"low": 0.009, "medium": 0.034, "high": 0.133},
    "gpt-image-1-mini": {"low": 0.005, "medium": 0.011, "high": 0.036},
}

# OpenAI 视频费率（USD/秒），按分辨率区分
OPENAI_VIDEO_COST = {
    "sora-2":     {"720p": 0.10},
    "sora-2-pro": {"720p": 0.30, "1024p": 0.50, "1080p": 0.70},
}
```

### 统一入口扩展

`calculate_cost()` 新增 `PROVIDER_OPENAI` 分支：
- 文本：`_TEXT_COST_TABLES` 新增 `"openai": ("OPENAI_TEXT_COST", "gpt-5.4-mini", "USD")`
- 图片：新增 `calculate_openai_image_cost(model, quality)` 方法
- 视频：新增 `calculate_openai_video_cost(duration_seconds, model, resolution)` 方法

`calculate_cost()` 签名新增可选 `quality` 参数，仅 OpenAI 图片使用。

> **`quality` 上游传递：** 本期 `UsageTracker` / `usage_repo` 暂不传递 `quality`，OpenAI 图片费用将使用默认值 `"medium"` 计算。完善 `quality` 从 Backend → UsageTracker → CostCalculator 的传递链作为后续优化。

---

## 7. 连接测试

### `server/routers/providers.py`

```python
def _test_openai(config: dict[str, str]) -> ConnectionTestResponse:
    """通过 models.list() 验证 OpenAI API Key。同步函数，由框架通过 asyncio.to_thread 调用。"""
    from openai import OpenAI

    kwargs: dict = {"api_key": config["api_key"]}
    base_url = config.get("base_url")
    if base_url:
        kwargs["base_url"] = base_url
    client = OpenAI(**kwargs)
    models = client.models.list()
    available = sorted(m.id for m in models.data[:10])
    return ConnectionTestResponse(
        success=True,
        available_models=available,
        message="连接成功",
    )
```

注册到 `_TEST_DISPATCH["openai"] = _test_openai`。

> **Note: ** 使用同步 `OpenAI` 客户端而非 `AsyncOpenAI`，因为现有框架通过 `asyncio.to_thread(test_fn, config)` 在线程池中运行所有连接测试函数（与 `_test_grok`、`_test_ark` 等一致）。

---

## 8. 前端变更

### 不需要改的

前端已是数据驱动的，后端注册新供应商后自动展示：
- Provider 列表页 — 动态渲染
- 配置表单 — 动态生成
- Credential 管理 — 已通用化
- 连接测试按钮 — 已通用化
- 后端选择下拉框 — 动态获取

### 需要改的

- **供应商图标** — 使用 lobe-icons 的 OpenAI 图标，同时更新 `PROVIDER_NAMES` 映射
- **`config-status-store.ts`** — 已确认完全动态判断，无需修改

---

## 9. 测试策略

### 单元测试

| 文件 | 覆盖范围 |
|------|----------|
| `test_openai_text_backend.py` | 消息构建、structured output、Instructor fallback、vision、usage 容错 |
| `test_openai_image_backend.py` | T2I/I2I 路径分派、b64 解码写入、尺寸映射、质量映射 |
| `test_openai_video_backend.py` | T2V/I2V、时长/尺寸映射、failed 状态异常、download_content |
| `test_cost_calculator.py`（扩展） | OpenAI 三种媒体类型定价计算 |

### 集成点测试

- Registry: 验证 `PROVIDER_REGISTRY["openai"]` 存在且 media_types 覆盖 text/image/video
- Factory: 验证 OpenAI 配置就绪时返回 `OpenAITextBackend`
- Connection test: mock `client.models.list()` 验证连接测试路径

### 不包含

- 端到端 API 调用测试（需要真实 API Key）
- 前端测试（前端几乎无改动）

---

## File Change Checklist

### New Files

| File | Description |
|------|------|
| `lib/openai_shared.py` | 共享客户端工厂 + 可重试错误类型 |
| `lib/text_backends/openai.py` | OpenAI 文本后端 |
| `lib/image_backends/openai.py` | OpenAI 图片后端 |
| `lib/video_backends/openai.py` | OpenAI 视频后端 |
| `tests/test_openai_text_backend.py` | 文本后端测试 |
| `tests/test_openai_image_backend.py` | 图片后端测试 |
| `tests/test_openai_video_backend.py` | 视频后端测试 |

### Modified Files

| 文件 | 变更 |
|------|------|
| `pyproject.toml` | `openai>=2.30.0` |
| `lib/providers.py` | 新增 `PROVIDER_OPENAI` 常量 |
| `lib/config/registry.py` | 新增 OpenAI ProviderMeta |
| `lib/cost_calculator.py` | 新增 OpenAI 定价表 + 计算方法，`calculate_cost()` 新增 quality 参数 |
| `lib/text_backends/__init__.py` | 注册 OpenAITextBackend |
| `lib/text_backends/factory.py` | 新增 `"openai": "openai"` 映射 + 参数传递 |
| `lib/image_backends/__init__.py` | 注册 OpenAIImageBackend |
| `lib/video_backends/__init__.py` | 注册 OpenAIVideoBackend |
| `server/routers/providers.py` | 新增 `_test_openai` 连接测试 |
| `server/services/generation_tasks.py` | 新增 `PROVIDER_OPENAI` 到映射表、`_DEFAULT_VIDEO_RESOLUTION`、工厂分支 |
| `tests/test_cost_calculator.py` | 扩展 OpenAI 定价用例 |
| 前端：`ProviderIcon.tsx` | 添加 OpenAI lobe-icons 图标 + `PROVIDER_NAMES` 映射 |
