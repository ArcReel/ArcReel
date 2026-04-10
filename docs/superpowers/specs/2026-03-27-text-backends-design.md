# 通用文本生成服务层设计

> Issue: #168 — 提取通用文本生成服务层，补齐多供应商文本生成能力

## Background

当前文本生成任务（剧本生成、概述生成、风格分析）通过 `lib/text_client.py` 硬编码创建 `GeminiClient`，仅支持 Gemini（AI Studio / Vertex AI）。图片和视频生成已在 #165 中完成 Backend Protocol + Registry 的供应商抽象，文本生成需对齐该架构。

### 现有调用点

| 调用点 | 文件 | 用途 | 特殊需求 |
|--------|------|------|----------|
| ScriptGenerator.generate() | `lib/script_generator.py` | 剧本生成 | 结构化输出（JSON Schema） |
| ProjectManager.generate_overview() | `lib/project_manager.py` | 概述生成 | 结构化输出 |
| upload_style_image | `server/routers/files.py` | 风格图分析 | Vision（图片输入） |
| normalize_drama_script.py | `agent_runtime_profile/.../` | CLI 剧本规范化 | 同步调用 |

### 设计决策记录

| 决策 | 选择 | 理由 |
|------|------|------|
| Vision（图片分析）归属 | 纳入 TextBackend 作为 VISION 能力 | 三家供应商均支持多模态，统一接口 |
| 结构化输出策略 | Backend 内部透明处理 | 三家供应商均原生支持 structured output |
| 任务队列 | 不走队列，保持直接 await | 文本生成延迟低，无需排队 |
| GeminiClient 处置 | 直接删除，不做弃用保留 | 所有职责已迁移到各 Backend |
| 能力声明粒度 | 模型级别，跨 text/image/video | 同一供应商不同模型能力不同 |
| 文本模型选择 | 按任务类型分别配置 | 不同任务对模型能力/成本需求不同 |
| 供应商推断 | 自动推断已配置供应商 | 降低配置门槛，零配置即可用 |

## 架构总览

```
ScriptGenerator / ProjectManager / files.py
  └→ create_default_text_backend(task_type, project_name?)
       └→ ConfigResolver.text_backend_for_task()
            ├─ 项目级任务配置
            ├─ 全局任务配置
            ├─ 全局默认
            └─ 自动推断（首个 ready 供应商）

lib/text_backends/
  ├─ base.py          # TextBackend Protocol + 数据类
  ├─ registry.py      # register_backend / create_backend
  ├─ gemini.py        # GeminiTextBackend
  ├─ ark.py           # ArkTextBackend
  ├─ grok.py          # GrokTextBackend
  └─ __init__.py      # 公共 API + 自动注册
```

## 第 1 部分：TextBackend Protocol + 数据类

### TextCapability 枚举

```python
class TextCapability(str, Enum):
    TEXT_GENERATION = "text_generation"
    STRUCTURED_OUTPUT = "structured_output"
    VISION = "vision"
```

### TextTaskType 枚举

```python
class TextTaskType(str, Enum):
    SCRIPT = "script"           # 剧本生成
    OVERVIEW = "overview"       # 概述/摘要生成
    STYLE_ANALYSIS = "style"    # 风格图分析
```

### ImageInput 数据类

```python
@dataclass
class ImageInput:
    path: Path | None = None    # 本地图片路径
    url: str | None = None      # 远程图片 URL
```

### TextGenerationRequest 数据类

```python
@dataclass
class TextGenerationRequest:
    prompt: str
    response_schema: dict | None = None       # JSON Schema，用于结构化输出
    images: list[ImageInput] | None = None    # 图片输入，用于 vision
    system_prompt: str | None = None          # 系统 prompt
```

### TextGenerationResult 数据类

```python
@dataclass
class TextGenerationResult:
    text: str
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
```

### TextBackend Protocol

```python
class TextBackend(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def model(self) -> str: ...
    @property
    def capabilities(self) -> Set[TextCapability]: ...
    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult: ...
```

## 第 2 部分：Backend 实现

### GeminiTextBackend (`lib/text_backends/gemini.py`)

- 从 `GeminiClient` 提取文本生成 + 风格分析逻辑
- 使用 `google.genai` SDK：`client.aio.models.generate_content()`
- 支持 AI Studio（api_key）和 Vertex AI（service account），通过构造参数区分
- 结构化输出：使用原生 `response_json_schema` 参数
- Vision：使用 genai 的图片 Part
- 保留 `@with_retry_async` 装饰器

构造参数：
```python
def __init__(
    self,
    *,
    api_key: str | None = None,
    model: str | None = None,
    backend: str = "aistudio",     # "aistudio" | "vertex"
    base_url: str | None = None,
    gcs_bucket: str | None = None,
)
```

### ArkTextBackend (`lib/text_backends/ark.py`)

- 使用 `volcenginesdkarkruntime.Ark` SDK
- 结构化输出：`client.beta.chat.completions.parse()` + Pydantic model
- Vision：`client.responses.create()` + `input_image` type
- JSON Schema → Pydantic model 转换：使用 `pydantic.create_model()` 动态构建，或将 JSON Schema dict 传入 `response_format={"type": "json_schema", "json_schema": schema}` （若 SDK 支持原始 schema）
- 同步 SDK 通过 `asyncio.to_thread()` 包装为异步

构造参数：
```python
def __init__(self, *, api_key: str | None = None, model: str | None = None)
```

### GrokTextBackend (`lib/text_backends/grok.py`)

- 使用 `xai_sdk.Client`
- 结构化输出：`chat.parse(PydanticModel)`
- Vision：`image(image_url=...)` 或 `image(path=...)` helper
- xai_sdk 有原生异步支持

构造参数：
```python
def __init__(self, *, api_key: str | None = None, model: str | None = None)
```

### 共性

- 三个 Backend 构造参数统一风格：`api_key`, `model`（可选，默认从 registry 读 default）
- `generate()` 内部根据 request 有无 `images` / `response_schema` 选择 SDK 调用方式
- 返回 `TextGenerationResult`，尽量填充 `input_tokens` / `output_tokens`

### 清理

`lib/gemini_client.py` 包含 `GeminiClient` 类和被 image/video backends 广泛引用的共享工具。拆分如下：

- **新建 `lib/gemini_shared.py`**：迁入 `RateLimiter`、`get_shared_rate_limiter()`、`VERTEX_SCOPES`、`RETRYABLE_ERRORS`、`with_retry` / `with_retry_async` 装饰器
- **删除 `lib/gemini_client.py`**：`GeminiClient` 类迁入 `GeminiTextBackend`，共享工具迁入 `gemini_shared.py`
- **删除 `lib/text_client.py`**：由 registry + factory 替代
- **更新导入**：`lib/image_backends/gemini.py`、`lib/video_backends/gemini.py`、`server/services/generation_tasks.py`、`server/routers/providers.py`、`lib/media_generator.py` 改为从 `lib/gemini_shared.py` 导入
- **风格分析 prompt**：从 `GeminiClient.analyze_style_image()` 中提取为 `lib/text_backends/prompts.py` 中的 `STYLE_ANALYSIS_PROMPT` 常量

## 第 3 部分：Registry + Config 集成

### Registry (`lib/text_backends/registry.py`)

复刻 `image_backends/registry.py`：

```python
_BACKEND_FACTORIES: dict[str, Callable[..., TextBackend]] = {}

def register_backend(name: str, factory: Callable[..., TextBackend]) -> None
def create_backend(name: str, **kwargs) -> TextBackend
def get_registered_backends() -> list[str]
```

`__init__.py` 自动注册三个 Backend。

### ProviderMeta 重构 (`lib/config/registry.py`)

移除 `ProviderMeta` 顶层的 `media_types` 和 `capabilities` 字段，新增 `models` 字段：

```python
@dataclass(frozen=True)
class ModelInfo:
    display_name: str
    media_type: str                # "text" | "image" | "video"
    capabilities: list[str]        # 对应 media_type 的能力枚举值
    default: bool = False          # 是否为该 media_type 的默认模型

@dataclass(frozen=True)
class ProviderMeta:
    display_name: str
    description: str
    required_keys: list[str]
    optional_keys: list[str] = field(default_factory=list)
    secret_keys: list[str] = field(default_factory=list)
    models: dict[str, ModelInfo] = field(default_factory=dict)

    @property
    def media_types(self) -> list[str]:
        return sorted(set(m.media_type for m in self.models.values()))

    @property
    def capabilities(self) -> list[str]:
        return sorted(set(c for m in self.models.values() for c in m.capabilities))
```

四个供应商全部补全 text 模型声明：

| 供应商 | 文本默认模型 | 能力 |
|--------|-------------|------|
| gemini-aistudio | gemini-3-flash-preview | text_generation, structured_output, vision |
| gemini-vertex | gemini-3-flash-preview | text_generation, structured_output, vision |
| ark | doubao-seed-2-0-lite-260215 | text_generation, structured_output, vision |
| grok | grok-4-1-fast-reasoning | text_generation, structured_output, vision |

### ConfigService (`lib/config/service.py`)

新增：
```python
_DEFAULT_TEXT_BACKEND = "gemini-aistudio/gemini-3-flash-preview"

async def get_default_text_backend(self) -> tuple[str, str]:
    raw = await self._setting_repo.get("default_text_backend", _DEFAULT_TEXT_BACKEND)
    return self._parse_backend(raw, _DEFAULT_TEXT_BACKEND)
```

### ConfigResolver (`lib/config/resolver.py`)

新增核心方法：

```python
async def text_backend_for_task(
    self, task_type: TextTaskType, project_name: str | None = None,
) -> tuple[str, str]:
    """按优先级解析文本 backend。

    优先级：项目级任务配置 → 全局任务配置 → 全局默认 → 自动推断
    """
```

新增自动推断方法（适用于 text/image/video 三种 media_type）：

```python
async def _auto_resolve_backend(self, media_type: str) -> tuple[str, str]:
    """遍历 PROVIDER_REGISTRY（按注册顺序），找到第一个满足以下条件的供应商：
    1. required_keys 全部已配置（ready 状态）
    2. models 中有对应 media_type 的模型
    返回 (provider_id, default_model_id)。
    遍历顺序：gemini-aistudio → gemini-vertex → ark → grok（即 PROVIDER_REGISTRY 的声明顺序）。
    """
```

### 后端 API 变更

- `GET /providers` 返回的 `ProviderStatus` 中 `media_types` 和 `capabilities` 从 `models` 推导
- `GET /providers` 返回的每个供应商新增 `models` 字段：`dict[str, {display_name, media_type, capabilities, default}]`，供前端模型选择器按 media_type 分组渲染
- `GET /api/v1/system-config` 返回值包含 `default_text_backend` 及各任务类型配置
- `PATCH /api/v1/system-config` 支持写入上述字段

## 第 4 部分：调用方重构

### 共用工厂函数

新增 `lib/text_backends/factory.py`：

```python
PROVIDER_ID_TO_BACKEND = {
    "gemini-aistudio": "gemini",
    "gemini-vertex": "gemini",
    "ark": "ark",
    "grok": "grok",
}

async def create_text_backend_for_task(
    task_type: TextTaskType,
    project_name: str | None = None,
) -> TextBackend:
    """从 DB 配置创建文本 backend。"""
    resolver = ConfigResolver(async_session_factory)
    provider_id, model_id = await resolver.text_backend_for_task(task_type, project_name)
    provider_config = await resolver.provider_config(provider_id)
    backend_name = PROVIDER_ID_TO_BACKEND[provider_id]
    return create_backend(backend_name, api_key=provider_config.get("api_key"), model=model_id)
```

### ScriptGenerator (`lib/script_generator.py`)

- 构造参数：`client: GeminiClient` → `backend: TextBackend`
- 移除 `MODEL` 类常量，模型由 backend 实例决定
- `generate()` 内部：`await self.backend.generate(TextGenerationRequest(prompt=..., response_schema=...))`
- 工厂方法 `create()`：调用 `create_text_backend_for_task(TextTaskType.SCRIPT, project_name)`

### ProjectManager.generate_overview() (`lib/project_manager.py`)

- 改用 `create_text_backend_for_task(TextTaskType.OVERVIEW)`
- 其余逻辑不变

### upload_style_image (`server/routers/files.py`)

- 改用 `create_text_backend_for_task(TextTaskType.STYLE_ANALYSIS)`
- `client.analyze_style_image(path)` → `backend.generate(TextGenerationRequest(prompt=STYLE_PROMPT, images=[ImageInput(path=path)]))`
- 风格分析 prompt 从 GeminiClient 内部提取为常量

### CLI 脚本 (`normalize_drama_script.py`)

- `create_text_client_sync()` → `asyncio.run(create_text_backend_for_task(TextTaskType.SCRIPT))`
- `client.generate_text(prompt)` → `asyncio.run(backend.generate(TextGenerationRequest(prompt=...)))`

## 第 5 部分：前端设置页变更

### Tab 重命名

现有的图片/视频相关 tab 改名为 **"模型选择"**。

### MediaModelSection 重构 (`settings/MediaModelSection.tsx`)

扩展为三组：

```
模型选择
├─ 图片模型: [供应商/模型 dropdown]
├─ 视频模型: [供应商/模型 dropdown]
└─ 文本模型
     ├─ 剧本生成: [dropdown, placeholder="自动"]
     ├─ 概述生成: [dropdown, placeholder="自动"]
     └─ 风格分析: [dropdown, placeholder="自动"]
```

- dropdown 选项从 `GET /providers` 返回的 `models` 按 `media_type` 过滤生成
- 文本任务类型留空表示走自动推断
- 每个模型选项旁展示能力标签

### 项目配置页 (`ProjectSettingsPage.tsx`)

项目级覆盖，结构与全局一致，未设置的项继承全局配置。

### 配置状态提示 (`config-status-store.ts`)

- 改为从 providers 响应检查**是否存在至少一个 ready 状态的供应商**支持该 media_type
- 三种 media_type 各自独立判断
- 只要任一供应商 ready，该类型的配置提示消除

### 前端类型 (`types/system.ts`)

```typescript
interface SystemSettings {
  default_video_backend: string;
  default_image_backend: string;
  default_text_backend: string;
  text_backend_script?: string;
  text_backend_overview?: string;
  text_backend_style?: string;
}
```

## 第 6 部分：成本计算 + 测试

### 成本计算 (`lib/cost_calculator.py`)

新增文本成本计算，按 token 计费：

```python
GEMINI_TEXT_COST = {
    "gemini-3-flash-preview": {"input": 0.10, "output": 0.40},
}

ARK_TEXT_COST = {
    "doubao-seed-2-0-lite-260215": {"input": 0.30, "output": 0.60},
}

GROK_TEXT_COST = {
    "grok-4-1-fast-reasoning": {"input": 2.00, "output": 10.00},
}

def calculate_text_cost(
    self, input_tokens: int, output_tokens: int,
    provider: str, model: str,
) -> tuple[float, str]:
    """返回 (amount, currency)"""
```

### 测试计划

**单元测试（新增）**：

| 测试文件 | 覆盖范围 |
|----------|----------|
| `tests/test_text_backends/test_base.py` | Request/Result 构建、TextCapability 枚举 |
| `tests/test_text_backends/test_registry.py` | register/create/get_registered 工厂逻辑 |
| `tests/test_text_backends/test_gemini.py` | 纯文本、结构化输出、vision 三条路径 |
| `tests/test_text_backends/test_ark.py` | .parse() 结构化输出、vision |
| `tests/test_text_backends/test_grok.py` | .parse() 结构化输出、vision |
| `tests/test_config_registry.py` | ProviderMeta 重构后 models/media_types 推导 |
| `tests/test_cost_calculator.py` | 新增文本成本计算用例 |

**集成测试（更新）**：

| 测试文件 | 变更 |
|----------|------|
| `tests/test_script_generator.py` | mock 从 GeminiClient 改为 TextBackend |
| `tests/test_project_manager_more.py` | 更新 generate_overview 测试 |
| `tests/test_files_router.py` | 更新 upload_style_image 测试 |
| `tests/test_text_client.py` | 删除（随 text_client.py 移除） |

**向后兼容验证**：

- 仅配置 Gemini 的用户，未设置 `default_text_backend`，自动推断正常工作
- 现有图片/视频功能不受 ProviderMeta 重构影响

## Out of Scope

- 文本生成流式输出（streaming）
- 文本任务进入 GenerationQueue 任务队列
- 统一 Backend 基础协议（BaseBackend 抽象）
