# Image Backend 通用图片生成服务层设计

> 关联 Issue: #101, #162
> 日期: 2026-03-26

## 概述

提取通用 `ImageBackend` 抽象接口，使图片供应商可插拔接入。镜像现有 `VideoBackend` 模式，接入四个供应商：Gemini AI Studio、Gemini Vertex AI、Ark（火山方舟 Seedream）、Grok（xAI Aurora）。同时将现有 `seedance` provider 重命名为 `ark`，统一 Seedance 视频 + Seedream 图片。

## 背景

当前图片生成直接耦合 `GeminiClient`，无法接入其他供应商。视频侧已有完整的 `VideoBackend` Protocol + Registry + 3 个实现（Gemini/Seedance/Grok）。本次为图片侧复制这一模式，并借机统一 Ark 供应商命名。

## 设计

### 1. 核心抽象层 (`lib/image_backends/`)

#### 目录结构

```
lib/image_backends/
├── __init__.py          # auto-register all backends, 导出公共 API
├── base.py              # ImageBackend Protocol + Request/Result + Capability enum
├── registry.py          # factory registry (create_backend / register_backend)
├── gemini.py            # GeminiImageBackend (AI Studio + Vertex AI)
├── ark.py               # ArkImageBackend (Seedream)
└── grok.py              # GrokImageBackend (Aurora)
```

#### 数据模型 (`base.py`)

```python
class ImageCapability(str, Enum):  # 继承 str 以支持字符串比较，与 VideoCapability 一致
    TEXT_TO_IMAGE = "text_to_image"
    IMAGE_TO_IMAGE = "image_to_image"

@dataclass
class ReferenceImage:
    path: str              # 本地文件路径
    label: str = ""        # 可选标签（如 "角色参考"）

@dataclass
class ImageGenerationRequest:
    prompt: str
    output_path: Path
    reference_images: list[ReferenceImage] = field(default_factory=list)
    aspect_ratio: str = "9:16"
    image_size: str = "1K"       # "1K", "2K"；各 Backend 忽略不支持的字段
    project_name: str | None = None
    seed: int | None = None

@dataclass
class ImageGenerationResult:
    image_path: Path
    provider: str            # "gemini-aistudio", "gemini-vertex", "ark", "grok"
    model: str
    image_uri: str | None = None   # 远端 URL（如有）
    seed: int | None = None
    usage_tokens: int | None = None
```

#### Protocol

```python
class ImageBackend(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def capabilities(self) -> set[ImageCapability]: ...

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult: ...
```

#### Registry (`registry.py`)

与 `video_backends/registry.py` 完全对称：

- `register_backend(name, factory)` — 注册工厂函数
- `create_backend(name, **kwargs)` — 创建实例
- `get_registered_backends()` — 列出已注册后端

### 2. 四个具体实现

#### 2.1 GeminiImageBackend (`gemini.py`)

- **Provider ID**: `gemini-aistudio` / `gemini-vertex`（通过 `backend_type` 参数区分）
- **SDK**: `google-genai`
- **默认模型**: `gemini-3.1-flash-image-preview`
- **能力**: `TEXT_TO_IMAGE`, `IMAGE_TO_IMAGE`
- **API**: `client.aio.models.generate_content(model, contents, config)`
- **参考图处理**: 从 `gemini_client.py` 迁移 `_build_contents_with_labeled_refs()` 逻辑，将 `ReferenceImage` 列表转为 contents 中的 `[label, PIL.Image, ...]` 序列
- **构造参数**: `backend_type`, `api_key`, `rate_limiter`, `image_model`, `base_url`(AI Studio), `credentials_path`/`gcs_bucket`(Vertex)
- **Vertex 凭证**: 从 `GeminiClient` 迁移 Vertex 模式的凭证初始化逻辑（`service_account.Credentials.from_service_account_file()`），通过 `credentials_path` 参数传入

#### 2.2 ArkImageBackend (`ark.py`)

- **Provider ID**: `ark`
- **SDK**: `volcenginesdkarkruntime.Ark` → `client.images.generate()`
- **默认模型**: `doubao-seedream-5-0-lite-260128`
- **能力**: `TEXT_TO_IMAGE`, `IMAGE_TO_IMAGE`
- **可选模型**: `doubao-seedream-5-0-lite-260128`, `doubao-seedream-4-5-251128`, `doubao-seedream-4-0-250828`
- **API 调用**: 同步 SDK 通过 `asyncio.to_thread()` 包装
- **参考图处理**: 将 `ReferenceImage` 路径读取为 base64，通过 `image` 参数传入（支持多图）
- **构造参数**: `api_key`, `model`

#### 2.3 GrokImageBackend (`grok.py`)

- **Provider ID**: `grok`
- **SDK**: `xai_sdk.AsyncClient` → `client.image.sample()`
- **默认模型**: `grok-imagine-image`
- **可选模型**: `grok-imagine-image-pro`
- **能力**: `TEXT_TO_IMAGE`, `IMAGE_TO_IMAGE`
- **生成**: `client.image.sample(prompt, model, aspect_ratio, resolution)`
- **编辑（I2I）**: `client.image.sample(prompt, model, image_url="data:image/png;base64,...")`，SDK 的 `sample()` 方法在传入 `image_url` 时自动走编辑路径
- **参考图处理**: 读取第一张 `ReferenceImage` 为 base64 data URI 传入 `image_url`；多张参考图场景需确认 SDK 是否支持 `images` 数组参数，不支持则取第一张
- **构造参数**: `api_key`, `model`

#### 2.4 Reference Images 处理策略

各后端统一接收 `list[ReferenceImage]`，内部自行转换：

| 后端 | 转换方式 |
|------|---------|
| Gemini | `PIL.Image` + label 注入 contents 列表 |
| Ark | base64 字符串列表传入 `image` 参数 |
| Grok | 第一张转 base64 data URI 传入 `image_url`，多张通过 `images` 数组 |

不支持 `IMAGE_TO_IMAGE` 时（不会发生，因为四个后端都支持），忽略 reference_images 并 log warning。

### 3. 集成层变更

#### 3.1 GenerationWorker (`lib/generation_worker.py`)

- 现有 `_extract_provider()` 已支持 image 任务的 provider 解析，**无需修改**
- `_normalize_provider_id()` 新增 `"seedance": "ark"` 映射，确保历史队列中的任务正确路由
- 优先级链：payload 显式指定 > project.json `image_backend` > 全局 `default_image_backend` > 硬编码默认值

#### 3.2 generation_tasks.py (`server/services/generation_tasks.py`)

- **删除** `_resolve_image_backend()`（原返回 Gemini-only 三元组）
- **新增** `_get_or_create_image_backend(provider_name, provider_settings, resolver, default_image_model)` 工厂函数，返回 `ImageBackend` 实例
- 对称 `_get_or_create_video_backend()`，带实例缓存
- 通过 `image_backends.create_backend(provider_id, **config)` 创建实例
- `_PROVIDER_ID_TO_BACKEND` 映射更新：`"seedance"` → `"ark"`
- `_DEFAULT_VIDEO_RESOLUTION` 映射更新：`PROVIDER_SEEDANCE` → `PROVIDER_ARK`
- `get_media_generator()` 中：不再传 `image_backend_type` / `gemini_api_key` / `gemini_base_url` / `gemini_image_model` 给图片路径，改为注入 `image_backend` 实例（Gemini config 仅保留给文本生成所需）

#### 3.3 MediaGenerator (`lib/media_generator.py`)

构造函数新增 `image_backend` 参数：

```python
def __init__(self, ..., image_backend=None, ...):
```

`generate_image()` / `generate_image_async()` **移除 GeminiClient fallback**，统一走 `ImageBackend`：

```python
if self._image_backend is None:
    raise RuntimeError("image_backend not configured")
request = ImageGenerationRequest(...)
result = await self._image_backend.generate(request)
```

脚本直调 MediaGenerator 的场景由调用方负责创建 backend 实例（通过 `image_backends.create_backend()` 即可）。

#### 3.4 ConfigResolver / ConfigService

已有 `default_image_backend()` 返回 `(provider_id, model_id)`，**无需修改**。

### 4. Provider 重命名：`seedance` → `ark`

#### 4.1 DB Migration

新增 Alembic 迁移：

```sql
UPDATE provider_config SET provider = 'ark' WHERE provider = 'seedance';
UPDATE system_setting SET value = REPLACE(value, 'seedance/', 'ark/')
    WHERE key IN ('default_video_backend', 'default_image_backend');
```

#### 4.2 代码变更

| 文件 | 变更 |
|------|------|
| `lib/video_backends/seedance.py` | 重命名为 `lib/video_backends/ark.py`，类名 `SeedanceVideoBackend` → `ArkVideoBackend` |
| `lib/video_backends/base.py` | `PROVIDER_SEEDANCE` → `PROVIDER_ARK` |
| `lib/video_backends/__init__.py` | 更新 import 和注册 |
| `lib/config/registry.py` | key `"seedance"` → `"ark"`，description 更新，`media_types` 加入 `"image"` |
| `server/routers/system_config.py` | `_PROVIDER_MODELS` key 改为 `"ark"`，加入 image 模型列表 |
| `lib/cost_calculator.py` | `calculate_seedance_video_cost` → `calculate_ark_video_cost`；常量 `SEEDANCE_VIDEO_COST` / `DEFAULT_SEEDANCE_MODEL` 重命名为 `ARK_VIDEO_COST` / `DEFAULT_ARK_MODEL` |
| `lib/db/repositories/usage_repo.py` | 更新 provider 匹配逻辑 |
| `server/services/generation_tasks.py` | `_PROVIDER_ID_TO_BACKEND`: `"seedance"` → `"ark"`；`_DEFAULT_VIDEO_RESOLUTION`: key 更新 |
| `lib/generation_worker.py` | `_normalize_provider_id()` 新增 `"seedance": "ark"` 向后兼容映射 |
| 全局 | 搜索替换 `PROVIDER_SEEDANCE` → `PROVIDER_ARK`、`"seedance"` → `"ark"` |

#### 4.x project.json 向后兼容

已有 `project.json` 中可能包含 `"video_provider": "seedance"` 或 `"image_backend": "seedance/..."`。通过 `_normalize_provider_id()` 的 `"seedance" → "ark"` 映射实现运行时兼容，不需要迁移文件。

#### 4.3 Grok Provider 扩展

`lib/config/registry.py` 中 `"grok"` 的 `media_types` 更新为 `["video", "image"]`，`optional_keys` 加入 `image_rpm`, `image_max_workers`。

#### 4.4 `_PROVIDER_MODELS` 更新

```python
_PROVIDER_MODELS = {
    "gemini-aistudio": {
        "video": ["veo-3.1-generate-preview", "veo-3.1-fast-generate-preview"],
        "image": ["gemini-3.1-flash-image-preview"],
    },
    "gemini-vertex": {
        "video": ["veo-3.1-generate-001", "veo-3.1-fast-generate-001"],
        "image": ["gemini-3.1-flash-image-preview"],
    },
    "ark": {
        "video": ["doubao-seedance-1-5-pro-251215"],
        "image": ["doubao-seedream-5-0-260128", "doubao-seedream-5-0-lite-260128",
                   "doubao-seedream-4-5-251128", "doubao-seedream-4-0-250828"],
    },
    "grok": {
        "video": ["grok-imagine-video"],
        "image": ["grok-imagine-image", "grok-imagine-image-pro"],
    },
}
```

### 5. 计费扩展

#### 5.1 CostCalculator 新增方法

```python
def calculate_ark_image_cost(self, model: str | None = None, n: int = 1) -> tuple[float, str]:
    """Ark 图片按张计费，返回 (cost, 'CNY')"""
    # doubao-seedream-5-0: 0.22, 4-5: 0.25, 4-0: 0.20, 5-0-lite: 0.22

def calculate_grok_image_cost(self, model: str | None = None, n: int = 1) -> float:
    """Grok 图片按张计费，返回 USD"""
    # grok-imagine-image: $0.02, grok-imagine-image-pro: $0.07
```

**返回类型说明**: 保持与现有模式一致（Ark 返回 `tuple[float, str]` 含 currency，Grok/Gemini 返回 `float` 默认 USD）。UsageRepository 根据 provider 类型决定 currency：Ark 系列设 `currency = "CNY"`，其余默认 `"USD"`。

#### 5.2 UsageRepository 成本路由扩展

```python
if status == "success":
    if row.call_type == "image":
        if effective_provider == PROVIDER_ARK:
            cost_amount, currency = cost_calculator.calculate_ark_image_cost(...)
        elif effective_provider == PROVIDER_GROK:
            cost_amount = cost_calculator.calculate_grok_image_cost(...)
        else:  # gemini
            cost_amount = cost_calculator.calculate_image_cost(...)
    elif row.call_type == "video":
        ...  # 现有逻辑，seedance → ark 重命名
```

#### 5.3 UsageTracker

`start_call()` 已支持 `provider` 参数，**接口不变**。MediaGenerator 中传入正确的 provider 名称即可。

### 6. 死代码清理

#### 6.1 GeminiClient 精简

从 `lib/gemini_client.py` 中删除：

- `generate_image()` / `generate_image_async()` / `generate_image_with_chat()` — 被 `GeminiImageBackend` 取代
- `generate_video()` — 已被 `GeminiVideoBackend` 取代
- `_build_contents_with_labeled_refs()` — 迁移到 `GeminiImageBackend`
- `_prepare_image_config()` / `_process_image_response()` — 迁移到 `GeminiImageBackend`
- `_normalize_reference_image()` / `_extract_name_from_path()` / `_load_image_detached()` — 迁移到 `GeminiImageBackend`
- `IMAGE_MODEL` / `VIDEO_MODEL` 属性 — 不再需要

保留：
- `VERTEX_SCOPES` 常量
- `RateLimiter` 类 + `get_shared_rate_limiter()` / `refresh_shared_rate_limiter()`
- `with_retry()` / `with_retry_async()` 装饰器
- `GeminiClient` 类精简为纯文本生成客户端（保留 `client` 属性 + 构造函数）

#### 6.2 类型迁移

- `ReferenceImageInput` / `ReferenceImageValue` 类型别名从 `gemini_client.py` 迁移到 `image_backends/base.py`
- 更新所有 import 引用

#### 6.3 MediaGenerator GeminiClient 依赖移除

移除 `MediaGenerator` 中对 `GeminiClient` 的图片/视频生成依赖（与 3.3 节一致）。`image_backend` 为必需注入，脚本直调场景由调用方通过 `image_backends.create_backend()` 创建实例。`MediaGenerator` 不再直接 import `GeminiClient`。

### 7. 错误处理

- **网络/API 错误**: 直接抛出，Worker 记录 `status=failed` + `error_message`
- **审核拒绝**: Grok `respect_moderation=False`、Ark 特定错误码 → 统一抛出描述性异常
- **能力不匹配**: 传了 `reference_images` 但后端不支持 `IMAGE_TO_IMAGE` → 忽略参考图退回 T2I，log warning（不中断，四个后端均支持 I2I，此分支为防御性代码）
- **重试**: SDK 层通过 `@with_retry_async` 处理瞬态 API 错误（429/503，backoff 2-32s）；持久性失败直接标记 `failed` 终态，由用户决定是否重试

### 8. 测试策略

#### 单元测试 (`tests/test_image_backends/`)

- 每个 backend 一个测试文件，mock SDK 调用
- 验证 `ImageGenerationRequest` → SDK 参数转换
- 验证 reference_images 的格式转换（base64、PIL、data URI）
- 验证 capabilities 声明与行为一致

#### 集成测试

- `test_generation_tasks.py` — 验证 `_get_or_create_image_backend()` 工厂逻辑
- `test_media_generator.py` — 验证注入 image_backend 后的 `generate_image()` 流程
- `test_cost_calculator.py` — 新增 ark/grok 图片计费用例

#### Fakes

- `tests/fakes.py` 新增 `FakeImageBackend`，实现 `ImageBackend` Protocol

#### DB Migration 测试

- 空表场景正常迁移
- 已有 `seedance` 配置正确更新为 `ark`

## 不在范围内

- 前端 UI 变更（`MediaModelSection` 已支持 image backend 选择，数据驱动）
- 项目级 image_backend 配置 UI（已有 `project.json` 字段支持）
- Batch generation（生成组图）— 后续按需扩展 `ImageCapability`
- `generate_image_with_chat()` 多轮对话能力 — Gemini 特有，不纳入通用 Protocol
