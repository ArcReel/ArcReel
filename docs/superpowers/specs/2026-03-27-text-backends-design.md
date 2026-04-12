# Generic Text Generation Service Layer Design

> Issue: #168 — Extract generic text generation service layer, filling multi-provider text generation capabilities

## Background

Current text generation tasks (script generation, overview generation, style analysis) create a `GeminiClient` through hardcoded calls in `lib/text_client.py`, supporting only Gemini (AI Studio / Vertex AI). Image and video generation already completed the Backend Protocol + Registry provider abstraction in #165; text generation needs to align with that architecture.

### Existing Call Points

| Call Point | File | Purpose | Special Requirements |
|-----------|------|---------|---------------------|
| ScriptGenerator.generate() | `lib/script_generator.py` | Script generation | Structured output (JSON Schema) |
| ProjectManager.generate_overview() | `lib/project_manager.py` | Overview generation | Structured output |
| upload_style_image | `server/routers/files.py` | Style image analysis | Vision (image input) |
| normalize_drama_script.py | `agent_runtime_profile/.../` | CLI script normalization | Synchronous calls |

### Design Decision Record

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Vision (image analysis) placement | Include in TextBackend as VISION capability | All three providers support multimodal; unified interface |
| Structured output strategy | Backend handles transparently internally | All three providers natively support structured output |
| Task queue | No queue; keep direct await | Text generation latency is low, no queuing needed |
| GeminiClient disposition | Delete directly, no deprecation retention | All responsibilities migrated to respective backends |
| Capability declaration granularity | Model-level, across text/image/video | Different models from the same provider have different capabilities |
| Text model selection | Configure separately by task type | Different tasks have different model capability/cost requirements |
| Provider inference | Automatically infer configured providers | Reduces configuration overhead, usable with zero configuration |

## Architecture Overview

```
ScriptGenerator / ProjectManager / files.py
  └→ create_default_text_backend(task_type, project_name?)
       └→ ConfigResolver.text_backend_for_task()
            ├─ project-level task configuration
            ├─ global task configuration
            ├─ global default
            └─ auto-inference (first ready provider)

lib/text_backends/
  ├─ base.py          # TextBackend Protocol + data classes
  ├─ registry.py      # register_backend / create_backend
  ├─ gemini.py        # GeminiTextBackend
  ├─ ark.py           # ArkTextBackend
  ├─ grok.py          # GrokTextBackend
  └─ __init__.py      # public API + auto-registration
```

## Part 1: TextBackend Protocol + Data Classes

### TextCapability Enum

```python
class TextCapability(str, Enum):
    TEXT_GENERATION = "text_generation"
    STRUCTURED_OUTPUT = "structured_output"
    VISION = "vision"
```

### TextTaskType Enum

```python
class TextTaskType(str, Enum):
    SCRIPT = "script"           # script generation
    OVERVIEW = "overview"       # overview/summary generation
    STYLE_ANALYSIS = "style"    # style image analysis
```

### ImageInput Data Class

```python
@dataclass
class ImageInput:
    path: Path | None = None    # local image path
    url: str | None = None      # remote image URL
```

### TextGenerationRequest Data Class

```python
@dataclass
class TextGenerationRequest:
    prompt: str
    response_schema: dict | None = None       # JSON Schema for structured output
    images: list[ImageInput] | None = None    # image inputs for vision
    system_prompt: str | None = None          # system prompt
```

### TextGenerationResult Data Class

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

## Part 2: Backend Implementations

### GeminiTextBackend (`lib/text_backends/gemini.py`)

- Extract text generation + style analysis logic from `GeminiClient`
- Uses `google.genai` SDK: `client.aio.models.generate_content()`
- Supports AI Studio (api_key) and Vertex AI (service account), distinguished via constructor parameters
- Structured output: uses native `response_json_schema` parameter
- Vision: uses genai's image Part
- Retains `@with_retry_async` decorator

Constructor parameters:
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

- Uses `volcenginesdkarkruntime.Ark` SDK
- Structured output: `client.beta.chat.completions.parse()` + Pydantic model
- Vision: `client.responses.create()` + `input_image` type
- JSON Schema → Pydantic model conversion: uses `pydantic.create_model()` for dynamic construction, or passes JSON Schema dict to `response_format={"type": "json_schema", "json_schema": schema}` if the SDK supports raw schema
- Synchronous SDK wrapped with `asyncio.to_thread()` for async

Constructor parameters:
```python
def __init__(self, *, api_key: str | None = None, model: str | None = None)
```

### GrokTextBackend (`lib/text_backends/grok.py`)

- Uses `xai_sdk.Client`
- Structured output: `chat.parse(PydanticModel)`
- Vision: `image(image_url=...)` or `image(path=...)` helper
- xai_sdk has native async support

Constructor parameters:
```python
def __init__(self, *, api_key: str | None = None, model: str | None = None)
```

### Common Traits

- All three Backend constructors follow a unified style: `api_key`, `model` (optional, defaults to registry default)
- `generate()` internally selects SDK call path based on whether `images` / `response_schema` is present in the request
- Returns `TextGenerationResult`, populating `input_tokens` / `output_tokens` where possible

### Cleanup

`lib/gemini_client.py` contains the `GeminiClient` class and shared utilities widely referenced by image/video backends. Split as follows:

- **New `lib/gemini_shared.py`**: migrate `RateLimiter`, `get_shared_rate_limiter()`, `VERTEX_SCOPES`, `RETRYABLE_ERRORS`, `with_retry` / `with_retry_async` decorators
- **Delete `lib/gemini_client.py`**: `GeminiClient` class migrates to `GeminiTextBackend`, shared utilities migrate to `gemini_shared.py`
- **Delete `lib/text_client.py`**: replaced by registry + factory
- **Update imports**: `lib/image_backends/gemini.py`, `lib/video_backends/gemini.py`, `server/services/generation_tasks.py`, `server/routers/providers.py`, `lib/media_generator.py` updated to import from `lib/gemini_shared.py`
- **Style analysis prompt**: extracted from `GeminiClient.analyze_style_image()` as `STYLE_ANALYSIS_PROMPT` constant in `lib/text_backends/prompts.py`

## Part 3: Registry + Config Integration

### Registry (`lib/text_backends/registry.py`)

Mirrors `image_backends/registry.py`:

```python
_BACKEND_FACTORIES: dict[str, Callable[..., TextBackend]] = {}

def register_backend(name: str, factory: Callable[..., TextBackend]) -> None
def create_backend(name: str, **kwargs) -> TextBackend
def get_registered_backends() -> list[str]
```

`__init__.py` auto-registers all three backends.

### ProviderMeta Refactoring (`lib/config/registry.py`)

Remove `media_types` and `capabilities` from the `ProviderMeta` top level, add a `models` field:

```python
@dataclass(frozen=True)
class ModelInfo:
    display_name: str
    media_type: str                # "text" | "image" | "video"
    capabilities: list[str]        # capability enum values for this media_type
    default: bool = False          # whether this is the default model for the media_type

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

All four providers get complete text model declarations:

| Provider | Default Text Model | Capabilities |
|----------|-------------------|--------------|
| gemini-aistudio | gemini-3-flash-preview | text_generation, structured_output, vision |
| gemini-vertex | gemini-3-flash-preview | text_generation, structured_output, vision |
| ark | doubao-seed-2-0-lite-260215 | text_generation, structured_output, vision |
| grok | grok-4-1-fast-reasoning | text_generation, structured_output, vision |

### ConfigService (`lib/config/service.py`)

Add:
```python
_DEFAULT_TEXT_BACKEND = "gemini-aistudio/gemini-3-flash-preview"

async def get_default_text_backend(self) -> tuple[str, str]:
    raw = await self._setting_repo.get("default_text_backend", _DEFAULT_TEXT_BACKEND)
    return self._parse_backend(raw, _DEFAULT_TEXT_BACKEND)
```

### ConfigResolver (`lib/config/resolver.py`)

Add core method:

```python
async def text_backend_for_task(
    self, task_type: TextTaskType, project_name: str | None = None,
) -> tuple[str, str]:
    """Resolve text backend by priority.

    Priority: project-level task config → global task config → global default → auto-inference
    """
```

Add auto-inference method (applicable for text/image/video media types):

```python
async def _auto_resolve_backend(self, media_type: str) -> tuple[str, str]:
    """Iterate PROVIDER_REGISTRY (in registration order), find the first provider that:
    1. Has all required_keys configured (ready status)
    2. Has models for the corresponding media_type
    Returns (provider_id, default_model_id).
    Iteration order: gemini-aistudio → gemini-vertex → ark → grok (PROVIDER_REGISTRY declaration order).
    """
```

### Backend API Changes

- `GET /providers` returns `ProviderStatus` where `media_types` and `capabilities` are derived from `models`
- `GET /providers` returns a new `models` field per provider: `dict[str, {display_name, media_type, capabilities, default}]`, for frontend model selectors to render grouped by media_type
- `GET /api/v1/system-config` return value includes `default_text_backend` and per-task-type configuration
- `PATCH /api/v1/system-config` supports writing the above fields

## Part 4: Caller Refactoring

### Shared Factory Function

New `lib/text_backends/factory.py`:

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
    """Create a text backend from DB configuration."""
    resolver = ConfigResolver(async_session_factory)
    provider_id, model_id = await resolver.text_backend_for_task(task_type, project_name)
    provider_config = await resolver.provider_config(provider_id)
    backend_name = PROVIDER_ID_TO_BACKEND[provider_id]
    return create_backend(backend_name, api_key=provider_config.get("api_key"), model=model_id)
```

### ScriptGenerator (`lib/script_generator.py`)

- Constructor parameter: `client: GeminiClient` → `backend: TextBackend`
- Remove `MODEL` class constant; model is determined by the backend instance
- In `generate()`: `await self.backend.generate(TextGenerationRequest(prompt=..., response_schema=...))`
- Factory method `create()`: calls `create_text_backend_for_task(TextTaskType.SCRIPT, project_name)`

### ProjectManager.generate_overview() (`lib/project_manager.py`)

- Switch to `create_text_backend_for_task(TextTaskType.OVERVIEW)`
- Rest of the logic unchanged

### upload_style_image (`server/routers/files.py`)

- Switch to `create_text_backend_for_task(TextTaskType.STYLE_ANALYSIS)`
- `client.analyze_style_image(path)` → `backend.generate(TextGenerationRequest(prompt=STYLE_PROMPT, images=[ImageInput(path=path)]))`
- Style analysis prompt extracted from GeminiClient internals as a constant

### CLI Script (`normalize_drama_script.py`)

- `create_text_client_sync()` → `asyncio.run(create_text_backend_for_task(TextTaskType.SCRIPT))`
- `client.generate_text(prompt)` → `asyncio.run(backend.generate(TextGenerationRequest(prompt=...)))`

## Part 5: Frontend Settings Page Changes

### Tab Rename

The existing image/video-related tab is renamed to **"Model Selection"**.

### MediaModelSection Refactoring (`settings/MediaModelSection.tsx`)

Extended to three groups:

```
Model Selection
├─ Image Model: [provider/model dropdown]
├─ Video Model: [provider/model dropdown]
└─ Text Model
     ├─ Script Generation: [dropdown, placeholder="Auto"]
     ├─ Overview Generation: [dropdown, placeholder="Auto"]
     └─ Style Analysis: [dropdown, placeholder="Auto"]
```

- Dropdown options generated by filtering `models` returned from `GET /providers` by `media_type`
- Leaving text task types empty means using auto-inference
- Each model option shows capability tags

### Project Settings Page (`ProjectSettingsPage.tsx`)

Project-level overrides, structure identical to global settings; unset items inherit global configuration.

### Configuration Status Prompt (`config-status-store.ts`)

- Changed to check from providers response **whether at least one provider in ready status** supports the media_type
- Three media types each have independent judgment
- As long as any provider is ready, the configuration prompt for that type is dismissed

### Frontend Types (`types/system.ts`)

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

## Part 6: Cost Calculation + Testing

### Cost Calculation (`lib/cost_calculator.py`)

Add text cost calculation, billed per token:

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
    """Returns (amount, currency)"""
```

### Testing Plan

**Unit Tests (new):**

| Test File | Coverage |
|-----------|----------|
| `tests/test_text_backends/test_base.py` | Request/Result construction, TextCapability enum |
| `tests/test_text_backends/test_registry.py` | register/create/get_registered factory logic |
| `tests/test_text_backends/test_gemini.py` | Plain text, structured output, vision — three code paths |
| `tests/test_text_backends/test_ark.py` | .parse() structured output, vision |
| `tests/test_text_backends/test_grok.py` | .parse() structured output, vision |
| `tests/test_config_registry.py` | ProviderMeta refactoring — models/media_types derivation |
| `tests/test_cost_calculator.py` | New text cost calculation test cases |

**Integration Tests (updated):**

| Test File | Change |
|-----------|--------|
| `tests/test_script_generator.py` | Mock changed from GeminiClient to TextBackend |
| `tests/test_project_manager_more.py` | Update generate_overview tests |
| `tests/test_files_router.py` | Update upload_style_image tests |
| `tests/test_text_client.py` | Delete (removed along with text_client.py) |

**Backward Compatibility Verification:**

- Users with only Gemini configured and no `default_text_backend` set — auto-inference works correctly
- Existing image/video functionality not affected by ProviderMeta refactoring

## Out of Scope

- Streaming text generation output
- Text tasks entering the GenerationQueue task queue
- Unified Backend base protocol (BaseBackend abstraction)
