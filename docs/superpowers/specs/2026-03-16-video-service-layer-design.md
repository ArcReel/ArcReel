# 视频生成服务层设计文档

## 背景

当前 ArcReel 的视频生成逻辑与 Google Gemini (Veo) 深度耦合。`GeminiClient`（1400+ 行）同时封装图片和视频生成，`MediaGenerator` 直接依赖它。随着 Seedance 等供应商的接入需求，需要提取通用的视频生成服务抽象层，实现供应商可插拔。

**关联 Issue**: #98（提取通用视频/图片生成服务层）、#99（视频层）、#42（Seedance 接入）

## 范围

**本次做**：
- 视频生成服务层抽象（`VideoBackend` 接口）
- 从 `GeminiClient` 提取视频逻辑为 `GeminiVideoBackend`
- Seedance 1.5 pro 接入（`SeedanceVideoBackend`）
- `MediaGenerator` 适配多 Backend
- `CostCalculator` / `UsageTracker` 多供应商支持
- 项目级 + 全局默认的供应商配置

**本次不做**：
- 图片生成服务层（#101 独立迭代）
- Seedance draft 样片模式（两步工作流，需前端配合）
- end_image 尾帧控制（当前无"结束帧"概念）
- reference_images 参考图（仅 Seedance lite 支持，质量较弱）
- video_to_extend 视频延长（独立工作流，当前无交互。`VideoGenerationResult` 不携带 `video_ref` 等 opaque handle，待后续启用时再设计）
- return_last_frame 尾帧接龙（与分镜驱动视频的核心流程冲突）
- 供应商管理页前端 UI（#102）

## 架构设计

### 调用链变化

```
之前:
  execute_video_task → MediaGenerator → GeminiClient

之后:
  execute_video_task → MediaGenerator → VideoBackend.generate()
                                           ├─ GeminiVideoBackend (genai SDK + 共享基础设施)
                                           └─ SeedanceVideoBackend (Ark SDK)
```

### 文件结构

```
lib/
  video_backends/
    __init__.py              # 导出公共 API
    base.py                  # Protocol + 数据类 + VideoCapability 枚举
    gemini.py                # GeminiVideoBackend — 从 GeminiClient 提取的视频逻辑
    seedance.py              # SeedanceVideoBackend — 火山方舟 Ark SDK
    registry.py              # 供应商注册 + 工厂函数
```

## 核心接口

### VideoCapability 枚举

```python
class VideoCapability(str, Enum):
    TEXT_TO_VIDEO = "text_to_video"
    IMAGE_TO_VIDEO = "image_to_video"
    GENERATE_AUDIO = "generate_audio"
    NEGATIVE_PROMPT = "negative_prompt"
    VIDEO_EXTEND = "video_extend"
    SEED_CONTROL = "seed_control"
    FLEX_TIER = "flex_tier"
```

### VideoGenerationRequest

```python
@dataclass
class VideoGenerationRequest:
    prompt: str
    output_path: Path
    aspect_ratio: str = "9:16"
    duration_seconds: int = 5              # 统一使用 int，各 Backend 负责标准化为自己的合法值
    resolution: str = "1080p"
    start_image: Path | None = None
    generate_audio: bool = True

    # Veo 特有
    negative_prompt: str | None = None

    # Seedance 特有
    service_tier: str = "default"          # "default" | "flex"
    seed: int | None = None
```

> **duration_seconds 标准化规则**：接口统一使用 `int`（秒数）。Veo 仅支持离散值 `4/6/8`，由 `GeminiVideoBackend` 内部调用现有 `normalize_veo_duration_seconds()` 标准化；Seedance 1.5 pro 支持 `4-12` 连续范围，直接透传。

### VideoGenerationResult

```python
@dataclass
class VideoGenerationResult:
    video_path: Path
    provider: str                          # "gemini" | "seedance"
    model: str                             # 具体模型 ID
    duration_seconds: int

    # 可选
    video_uri: str | None = None           # 远程 URI（Veo GCS / Seedance CDN）
    seed: int | None = None                # 实际使用的种子
    usage_tokens: int | None = None        # Seedance token 用量
    task_id: str | None = None             # 供应商任务 ID
```

### VideoBackend Protocol

```python
class VideoBackend(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def capabilities(self) -> set[VideoCapability]: ...

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult: ...
```

## Backend 实现

### GeminiVideoBackend

**策略**：从 `GeminiClient` 提取视频逻辑，而非薄包装。

- 直接使用 `google-genai` SDK
- 复用 `GeminiClient` 的共享基础设施：`RateLimiter`、`with_retry_async` 装饰器、客户端初始化逻辑（aistudio/vertex 双后端）
- `GeminiClient` 保留图片生成 + 共享工具，视频方法标记 deprecated 并内部转调 `GeminiVideoBackend`

能力集：
- `TEXT_TO_VIDEO`、`IMAGE_TO_VIDEO`、`VIDEO_EXTEND`
- `GENERATE_AUDIO`（Vertex 后端）
- `NEGATIVE_PROMPT`

初始化参数：
- `backend_type: str` — "aistudio" | "vertex"
- `api_key: str | None` — AI Studio 模式
- `rate_limiter: RateLimiter` — 共享限流器
- `video_model: str` — 模型 ID（默认 `veo-3.1-generate-001`）

### SeedanceVideoBackend

- 使用 `volcengine-python-sdk[ark]`（`volcenginesdkarkruntime.Ark`）
- 异步轮询模式：`tasks.create()` → 轮询 `tasks.get()` → 下载 MP4
- 模型：`doubao-seedance-1-5-pro-251215`

能力集：
- `TEXT_TO_VIDEO`、`IMAGE_TO_VIDEO`
- `GENERATE_AUDIO`
- `SEED_CONTROL`、`FLEX_TIER`

初始化参数：
- `api_key: str` — 火山方舟 API key
- `model: str` — 模型 ID（默认 `doubao-seedance-1-5-pro-251215`）
- `file_service_base_url: str` — 项目文件服务公网 URL（用于图片上传）

**轮询策略**：
- `service_tier="default"`（在线）：轮询间隔 10s，超时 600s
- `service_tier="flex"`（离线）：轮询间隔 60s，超时 172800s（48h）
- 任务状态 `failed` / `expired` 映射为异常，由 `GenerationWorker` 统一处理为 task failed

**本地图片上传**：Seedance API 要求图片通过 URL 传入。`SeedanceVideoBackend` 通过 `file_service_base_url` 构造上传请求，将本地分镜图上传到项目文件服务获取公网 URL。上传逻辑封装在 Backend 内部，对调用方透明。

**Seedance 固定参数**：`watermark=False`（生产环境不加水印）、`ratio` 从 `aspect_ratio` 直接映射（两者格式一致，如 `"16:9"`）。

> **部署要求**：使用 Seedance 供应商时，项目部署环境必须可公网访问（Seedance API 需通过 URL 拉取图片）。需在 README 和 `.env.example` 中说明 `FILE_SERVICE_BASE_URL` 配置。

### Registry

```python
_BACKEND_FACTORIES: dict[str, Callable[..., VideoBackend]] = {}

def register_backend(name: str, factory: Callable[..., VideoBackend]):
    _BACKEND_FACTORIES[name] = factory

def create_backend(name: str, **kwargs) -> VideoBackend:
    """根据名称和配置创建 Backend 实例。缺少必要 API key 时抛出 ValueError。"""
    if name not in _BACKEND_FACTORIES:
        raise ValueError(f"Unknown video backend: {name}")
    return _BACKEND_FACTORIES[name](**kwargs)

def get_available_backends() -> list[str]:
    """返回已注册且 API key 可用的供应商列表。"""
    ...
```

启动时自动注册 `gemini` 和 `seedance`。缺少 API key 不会导致启动失败，仅在实际选用该供应商时报错。

## 配置设计

### 全局配置（SystemConfigManager）

全局配置通过现有的 `SystemConfigManager`（`.system_config.json`）管理，前端通过 MediaConfigTab 操作。新增以下配置项：

| 配置项 | 说明 | 对应环境变量 |
|--------|------|-------------|
| `video_provider` | 全局默认视频供应商（`gemini` \| `seedance`） | `DEFAULT_VIDEO_PROVIDER` |
| `ark_api_key` | 火山方舟 API key | `ARK_API_KEY` |
| `file_service_base_url` | 项目文件服务公网地址（Seedance 图片上传用） | `FILE_SERVICE_BASE_URL` |

这些配置项遵循现有机制：保存后立即应用到 `os.environ`，无需重启。MediaConfigTab 需扩展 UI 以支持视频供应商选择和 Seedance API key 输入。

> 注：MediaConfigTab 的 UI 改动属于供应商管理页（#102）范畴，本次后端先支持配置读写，前端可暂通过直接编辑 `.system_config.json` 配置。

### 项目级覆盖（project.json）

```json
{
  "video_provider": "seedance",
  "video_settings": {
    "resolution": "1080p",
    "aspect_ratio": "9:16",
    "generate_audio": true
  },
  "video_provider_settings": {
    "seedance": {
      "service_tier": "default"
    },
    "gemini": {
      "negative_prompt": "music, BGM, background music, subtitles, low quality"
    }
  }
}
```

优先级：`project.json` > 环境变量全局默认。

切换供应商时：
- 通用设置（`video_settings`）直接沿用
- 供应商特有设置按命名空间保留，切回时恢复
- 已生成的视频文件不受影响

### 参数来源

| 参数 | 来源 | 说明 |
|------|------|------|
| `prompt` | 单次请求 | 每个分镜不同 |
| `duration_seconds` | 单次请求 | 可按分镜指定 |
| `seed` | 单次请求（可选） | 迭代时手动传入 |
| `resolution` | 项目 video_settings | 全项目一致 |
| `aspect_ratio` | 项目 video_settings | 全项目一致 |
| `generate_audio` | 项目 video_settings | 全项目一致 |
| `service_tier` | 项目 video_provider_settings.seedance | Seedance 项目级 |
| `negative_prompt` | 项目 video_provider_settings.gemini | Gemini 项目级 |

## 参数流通链路

```
1. API 层 (generate.py)
   POST /generate/video/{segment_id}
   Body: { prompt, duration, seed? }

2. 入队 (GenerationQueue)
   payload_json 中快照 provider + settings（入队时确定，不受后续配置变更影响）

3. Worker 执行 (execute_video_task)
   从 payload_json 构造 VideoGenerationRequest

4. MediaGenerator
   版本管理 + UsageTracker 包装

5. VideoBackend.generate(request)
   各 Backend 从 request 取自己需要的字段
```

## MediaGenerator 适配

现有 `MediaGenerator` 的 `self.video_backend` 属性（`media_generator.py:62`）存储的是字符串 `"aistudio"` | `"vertex"`。适配时将其重命名为 `self._gemini_backend_type`，新增 `self._video_backend: VideoBackend` 存储 Backend 实例。

```python
class MediaGenerator:
    def __init__(self, ..., video_backend: VideoBackend | None = None):
        self._video_backend = video_backend
        # 向后兼容：未提供 video_backend 时，自动创建 GeminiVideoBackend
        if self._video_backend is None:
            self._video_backend = GeminiVideoBackend(...)

    async def generate_video_async(self, ...):
        # 版本管理（VersionManager）和用量追踪（UsageTracker）保持在此层
        # 核心调用变为:
        request = VideoGenerationRequest(...)
        result = await self._video_backend.generate(request)
```

**Backend 实例化职责**：`get_media_generator()`（`server/services/generation_tasks.py`）负责读取项目配置、选择供应商、通过 Registry 创建 Backend 实例并注入 `MediaGenerator`。

**横切关注点不下沉到 Backend**：版本管理、用量追踪在 MediaGenerator 层处理，Backend 只负责「调 API、拿结果」。

## CostCalculator + UsageTracker 扩展

### CostCalculator

按供应商分策略计费，返回 `(amount: float, currency: str)` 元组：

- **Gemini**：按 resolution × duration × audio 查表（USD）— 现有逻辑不变
- **Seedance**：从 API 响应的 `usage.completion_tokens` 获取实际 token 用量，按单价计算

### Seedance 费用计算逻辑

**计算公式**：

```
费用(元) = usage_tokens / 1_000_000 × 单价(元/百万token)
```

其中 `usage_tokens` 来自 API 响应的 `usage.completion_tokens` 字段（仅成功生成才计费）。

**单价表**（元/百万 token）：

| 模型 | 在线有声 | 在线无声 | 离线有声 | 离线无声 |
|------|---------|---------|---------|---------|
| seedance-1.5-pro | 16.00 | 8.00 | 8.00 | 4.00 |

**计费维度映射**：
- 在线/离线 → `service_tier`（`"default"` = 在线，`"flex"` = 离线）
- 有声/无声 → `generate_audio`（`True` = 有声，`False` = 无声）

**示例**：1080p 16:9 有声 5 秒视频，在线推理
- API 返回 `usage.completion_tokens = 246840`（≈ `1920 × 1080 × 24 × 5 / 1024`）
- 费用 = `246840 / 1_000_000 × 16.00` = **3.95 元**

**实现要点**：`CostCalculator` 新增 `_seedance_video_cost(model, usage_tokens, service_tier, generate_audio)` 方法，根据 `service_tier` 和 `generate_audio` 查表取单价，乘以 token 用量。

不同币种分别统计，不做汇率转换。

### UsageTracker（api_calls 表）

数据库迁移方案：
1. 将现有 `cost_usd` 列重命名为 `cost_amount`
2. 新增 `currency` 列（`String`，默认 `"USD"`，既有数据回填 `"USD"`）
3. 新增 `provider` 列（`String`，默认 `"gemini"`，既有数据回填 `"gemini"`）
4. 新增 `usage_tokens` 列（`Integer`，可空）

通过 Alembic 迁移脚本执行，确保现有数据不丢失。

`UsageRepository.get_stats()` 修改为按 `currency` 分组汇总费用。

## 供应商能力对比

| 能力 | Gemini Veo | Seedance 1.5 |
|------|-----------|--------------|
| 文生视频 | Y | Y |
| 图生视频（首帧） | Y | Y |
| 音频生成 | Y (Vertex) | Y |
| 反向提示词 | Y | N |
| 视频延长 | Y | N |
| 种子控制 | N | Y |
| 离线推理（半价） | N | Y |

## 支持的参数

**通用参数**：prompt, aspect_ratio, duration_seconds, resolution, start_image, generate_audio

**Seedance 特有**：service_tier, seed

**Veo 特有**：negative_prompt
