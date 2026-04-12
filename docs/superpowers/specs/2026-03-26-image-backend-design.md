# Image Backend Generic Image Generation Service Layer Design

> Related Issues: #101, #162
> Date: 2026-03-26

## Overview

Extract a generic `ImageBackend` abstract interface to make image providers pluggable. Mirrors the existing `VideoBackend` pattern, integrating four providers: Gemini AI Studio, Gemini Vertex AI, Ark (Volcano Engine Seedream), and Grok (xAI Aurora). Also renames the existing `seedance` provider to `ark`, unifying Seedance video + Seedream image.

## Background

Image generation is currently tightly coupled to `GeminiClient`, making it impossible to integrate other providers. The video side already has a complete `VideoBackend` Protocol + Registry + 3 implementations (Gemini/Seedance/Grok). This effort copies that pattern for the image side, and also unifies the Ark provider naming.

## Design

### 1. Core Abstraction Layer (`lib/image_backends/`)

#### Directory Structure

```
lib/image_backends/
├── __init__.py          # auto-register all backends, export public API
├── base.py              # ImageBackend Protocol + Request/Result + Capability enum
├── registry.py          # factory registry (create_backend / register_backend)
├── gemini.py            # GeminiImageBackend (AI Studio + Vertex AI)
├── ark.py               # ArkImageBackend (Seedream)
└── grok.py              # GrokImageBackend (Aurora)
```

#### Data Models (`base.py`)

```python
class ImageCapability(str, Enum):  # inherits str to support string comparison, consistent with VideoCapability
    TEXT_TO_IMAGE = "text_to_image"
    IMAGE_TO_IMAGE = "image_to_image"

@dataclass
class ReferenceImage:
    path: str              # local file path
    label: str = ""        # optional label (e.g., "character reference")

@dataclass
class ImageGenerationRequest:
    prompt: str
    output_path: Path
    reference_images: list[ReferenceImage] = field(default_factory=list)
    aspect_ratio: str = "9:16"
    image_size: str = "1K"       # "1K", "2K"; each Backend ignores unsupported fields
    project_name: str | None = None
    seed: int | None = None

@dataclass
class ImageGenerationResult:
    image_path: Path
    provider: str            # "gemini-aistudio", "gemini-vertex", "ark", "grok"
    model: str
    image_uri: str | None = None   # remote URL (if any)
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

Fully symmetrical with `video_backends/registry.py`:

- `register_backend(name, factory)` — register factory function
- `create_backend(name, **kwargs)` — create instance
- `get_registered_backends()` — list registered backends

### 2. Four Concrete Implementations

#### 2.1 GeminiImageBackend (`gemini.py`)

- **Provider ID**: `gemini-aistudio` / `gemini-vertex` (distinguished via `backend_type` parameter)
- **SDK**: `google-genai`
- **Default model**: `gemini-3.1-flash-image-preview`
- **Capabilities**: `TEXT_TO_IMAGE`, `IMAGE_TO_IMAGE`
- **API**: `client.aio.models.generate_content(model, contents, config)`
- **Reference image handling**: migrates `_build_contents_with_labeled_refs()` logic from `gemini_client.py`, converts `ReferenceImage` list to `[label, PIL.Image, ...]` sequence in contents
- **Constructor parameters**: `backend_type`, `api_key`, `rate_limiter`, `image_model`, `base_url` (AI Studio), `credentials_path`/`gcs_bucket` (Vertex)
- **Vertex credentials**: migrates Vertex mode credential initialization logic from `GeminiClient` (`service_account.Credentials.from_service_account_file()`), passed via `credentials_path` parameter

#### 2.2 ArkImageBackend (`ark.py`)

- **Provider ID**: `ark`
- **SDK**: `volcenginesdkarkruntime.Ark` → `client.images.generate()`
- **Default model**: `doubao-seedream-5-0-lite-260128`
- **Capabilities**: `TEXT_TO_IMAGE`, `IMAGE_TO_IMAGE`
- **Optional models**: `doubao-seedream-5-0-lite-260128`, `doubao-seedream-4-5-251128`, `doubao-seedream-4-0-250828`
- **API call**: synchronous SDK wrapped with `asyncio.to_thread()`
- **Reference image handling**: reads `ReferenceImage` paths as base64, passes via `image` parameter (supports multiple images)
- **Constructor parameters**: `api_key`, `model`

#### 2.3 GrokImageBackend (`grok.py`)

- **Provider ID**: `grok`
- **SDK**: `xai_sdk.AsyncClient` → `client.image.sample()`
- **Default model**: `grok-imagine-image`
- **Optional models**: `grok-imagine-image-pro`
- **Capabilities**: `TEXT_TO_IMAGE`, `IMAGE_TO_IMAGE`
- **Generation**: `client.image.sample(prompt, model, aspect_ratio, resolution)`
- **Editing (I2I)**: `client.image.sample(prompt, model, image_url="data:image/png;base64,...")`, SDK `sample()` method automatically takes the editing path when `image_url` is passed
- **Reference image handling**: reads the first `ReferenceImage` as base64 data URI passed to `image_url`; for multiple reference images, confirm whether SDK supports `images` array parameter, if not take only the first
- **Constructor parameters**: `api_key`, `model`

#### 2.4 Reference Images Handling Strategy

Each backend uniformly accepts `list[ReferenceImage]` and converts internally:

| Backend | Conversion Method |
|---------|-----------------|
| Gemini | `PIL.Image` + label injected into contents list |
| Ark | base64 string list passed to `image` parameter |
| Grok | First image converted to base64 data URI passed to `image_url`, multiple via `images` array |

If `IMAGE_TO_IMAGE` is not supported (won't happen as all four backends support I2I), ignore reference_images and fall back to T2I, log warning.

### 3. Integration Layer Changes

#### 3.1 GenerationWorker (`lib/generation_worker.py`)

- Existing `_extract_provider()` already supports provider resolution for image tasks, **no changes needed**
- `_normalize_provider_id()` adds `"seedance": "ark"` mapping, ensuring historical queue tasks route correctly
- Priority chain: payload explicit specification > project.json `image_backend` > global `default_image_backend` > hardcoded default

#### 3.2 generation_tasks.py (`server/services/generation_tasks.py`)

- **Delete** `_resolve_image_backend()` (originally returns Gemini-only triplet)
- **Add** `_get_or_create_image_backend(provider_name, provider_settings, resolver, default_image_model)` factory function, returns `ImageBackend` instance
- Symmetric with `_get_or_create_video_backend()`, with instance caching
- Create instances via `image_backends.create_backend(provider_id, **config)`
- `_PROVIDER_ID_TO_BACKEND` mapping updated: `"seedance"` → `"ark"`
- `_DEFAULT_VIDEO_RESOLUTION` mapping updated: `PROVIDER_SEEDANCE` → `PROVIDER_ARK`
- `get_media_generator()`: no longer passes `image_backend_type` / `gemini_api_key` / `gemini_base_url` / `gemini_image_model` for image path, instead injects `image_backend` instance (Gemini config retained only for text generation)

#### 3.3 MediaGenerator (`lib/media_generator.py`)

Constructor adds `image_backend` parameter:

```python
def __init__(self, ..., image_backend=None, ...):
```

`generate_image()` / `generate_image_async()` **removes GeminiClient fallback**, uniformly uses `ImageBackend`:

```python
if self._image_backend is None:
    raise RuntimeError("image_backend not configured")
request = ImageGenerationRequest(...)
result = await self._image_backend.generate(request)
```

Scripts calling MediaGenerator directly are responsible for creating backend instances (via `image_backends.create_backend()`).

#### 3.4 ConfigResolver / ConfigService

Existing `default_image_backend()` returns `(provider_id, model_id)`, **no changes needed**.

### 4. Provider Rename: `seedance` → `ark`

#### 4.1 DB Migration

New Alembic migration:

```sql
UPDATE provider_config SET provider = 'ark' WHERE provider = 'seedance';
UPDATE system_setting SET value = REPLACE(value, 'seedance/', 'ark/')
    WHERE key IN ('default_video_backend', 'default_image_backend');
```

#### 4.2 Code Changes

| File | Change |
|------|--------|
| `lib/video_backends/seedance.py` | Renamed to `lib/video_backends/ark.py`, class `SeedanceVideoBackend` → `ArkVideoBackend` |
| `lib/video_backends/base.py` | `PROVIDER_SEEDANCE` → `PROVIDER_ARK` |
| `lib/video_backends/__init__.py` | Update imports and registration |
| `lib/config/registry.py` | key `"seedance"` → `"ark"`, update description, add `"image"` to `media_types` |
| `server/routers/system_config.py` | `_PROVIDER_MODELS` key changed to `"ark"`, add image model list |
| `lib/cost_calculator.py` | `calculate_seedance_video_cost` → `calculate_ark_video_cost`; constants `SEEDANCE_VIDEO_COST` / `DEFAULT_SEEDANCE_MODEL` renamed to `ARK_VIDEO_COST` / `DEFAULT_ARK_MODEL` |
| `lib/db/repositories/usage_repo.py` | Update provider matching logic |
| `server/services/generation_tasks.py` | `_PROVIDER_ID_TO_BACKEND`: `"seedance"` → `"ark"`; `_DEFAULT_VIDEO_RESOLUTION`: update key |
| `lib/generation_worker.py` | `_normalize_provider_id()` adds `"seedance": "ark"` backward-compatible mapping |
| Global | Search and replace `PROVIDER_SEEDANCE` → `PROVIDER_ARK`, `"seedance"` → `"ark"` |

#### 4.x project.json Backward Compatibility

Existing `project.json` may contain `"video_provider": "seedance"` or `"image_backend": "seedance/..."`. Runtime compatibility is achieved via `_normalize_provider_id()`'s `"seedance" → "ark"` mapping, no file migration needed.

#### 4.3 Grok Provider Extension

`lib/config/registry.py` updates `"grok"`'s `media_types` to `["video", "image"]`, adds `image_rpm`, `image_max_workers` to `optional_keys`.

#### 4.4 `_PROVIDER_MODELS` Update

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

### 5. Cost Calculation Extension

#### 5.1 CostCalculator New Methods

```python
def calculate_ark_image_cost(self, model: str | None = None, n: int = 1) -> tuple[float, str]:
    """Ark image billing per image, returns (cost, 'CNY')"""
    # doubao-seedream-5-0: 0.22, 4-5: 0.25, 4-0: 0.20, 5-0-lite: 0.22

def calculate_grok_image_cost(self, model: str | None = None, n: int = 1) -> float:
    """Grok image billing per image, returns USD"""
    # grok-imagine-image: $0.02, grok-imagine-image-pro: $0.07
```

**Return type notes**: Consistent with existing patterns (Ark returns `tuple[float, str]` with currency, Grok/Gemini return `float` defaulting to USD). UsageRepository sets `currency = "CNY"` for Ark series based on provider type, others default to `"USD"`.

#### 5.2 UsageRepository Cost Routing Extension

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
        ...  # existing logic, seedance → ark rename
```

#### 5.3 UsageTracker

`start_call()` already supports `provider` parameter, **interface unchanged**. MediaGenerator passes the correct provider name.

### 6. Dead Code Cleanup

#### 6.1 GeminiClient Streamlining

Delete from `lib/gemini_client.py`:

- `generate_image()` / `generate_image_async()` / `generate_image_with_chat()` — replaced by `GeminiImageBackend`
- `generate_video()` — already replaced by `GeminiVideoBackend`
- `_build_contents_with_labeled_refs()` — migrated to `GeminiImageBackend`
- `_prepare_image_config()` / `_process_image_response()` — migrated to `GeminiImageBackend`
- `_normalize_reference_image()` / `_extract_name_from_path()` / `_load_image_detached()` — migrated to `GeminiImageBackend`
- `IMAGE_MODEL` / `VIDEO_MODEL` attributes — no longer needed

Retain:
- `VERTEX_SCOPES` constant
- `RateLimiter` class + `get_shared_rate_limiter()` / `refresh_shared_rate_limiter()`
- `with_retry()` / `with_retry_async()` decorators
- `GeminiClient` class streamlined to pure text generation client (retains `client` attribute + constructor)

#### 6.2 Type Migration

- `ReferenceImageInput` / `ReferenceImageValue` type aliases migrated from `gemini_client.py` to `image_backends/base.py`
- Update all import references

#### 6.3 MediaGenerator GeminiClient Dependency Removal

Remove `MediaGenerator`'s dependency on `GeminiClient` for image/video generation (consistent with section 3.3). `image_backend` is a required injection; scripts calling directly are responsible for creating instances via `image_backends.create_backend()`. `MediaGenerator` no longer directly imports `GeminiClient`.

### 7. Error Handling

- **Network/API errors**: raised directly, Worker records `status=failed` + `error_message`
- **Content moderation rejection**: Grok `respect_moderation=False`, Ark specific error codes → uniformly raise descriptive exceptions
- **Capability mismatch**: `reference_images` passed but backend doesn't support `IMAGE_TO_IMAGE` → ignore reference images, fall back to T2I, log warning (won't happen as all four backends support I2I, this branch is defensive code)
- **Retry**: SDK layer handles transient API errors via `@with_retry_async` (429/503, backoff 2-32s); persistent failures directly mark `failed` terminal state, user decides whether to retry

### 8. Testing Strategy

#### Unit Tests (`tests/test_image_backends/`)

- One test file per backend, mock SDK calls
- Verify `ImageGenerationRequest` → SDK parameter conversion
- Verify reference_images format conversion (base64, PIL, data URI)
- Verify capabilities declarations are consistent with behavior

#### Integration Tests

- `test_generation_tasks.py` — verify `_get_or_create_image_backend()` factory logic
- `test_media_generator.py` — verify `generate_image()` flow after injecting image_backend
- `test_cost_calculator.py` — add ark/grok image cost calculation test cases

#### Fakes

- `tests/fakes.py` adds `FakeImageBackend` implementing `ImageBackend` Protocol

#### DB Migration Tests

- Normal migration with empty table
- Existing `seedance` configuration correctly updated to `ark`

## Out of Scope

- Frontend UI changes (`MediaModelSection` already supports image backend selection, data-driven)
- Project-level image_backend configuration UI (existing `project.json` field already supported)
- Batch generation (generating multiple images) — extend `ImageCapability` as needed later
- `generate_image_with_chat()` multi-turn dialogue capability — Gemini-specific, not included in the generic Protocol
