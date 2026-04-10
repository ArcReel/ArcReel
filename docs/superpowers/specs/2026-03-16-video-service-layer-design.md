# Video Generation Service Layer Design Document

## Background

The current ArcReel video generation logic is tightly coupled to Google Gemini (Veo). `GeminiClient` (1400+ lines) encapsulates both image and video generation, and `MediaGenerator` depends directly on it. As the need to integrate providers like Seedance grows, a generic video generation service abstraction layer must be extracted to make providers pluggable.

**Related Issues**: #98 (Extract generic video/image generation service layer), #99 (video layer), #42 (Seedance integration)

## Scope

**In scope**:
- Video generation service layer abstraction (`VideoBackend` interface)
- Extract video logic from `GeminiClient` into `GeminiVideoBackend`
- Seedance 1.5 pro integration (`SeedanceVideoBackend`)
- `MediaGenerator` adapted to multiple Backends
- `CostCalculator` / `UsageTracker` multi-provider support
- Project-level + global default provider configuration

**Out of scope**:
- Image generation service layer (#101 independent iteration)
- Seedance draft preview mode (two-step workflow requiring frontend cooperation)
- end_image tail-frame control (currently no "end frame" concept)
- reference_images reference images (only supported by Seedance lite, lower quality)
- video_to_extend video extension (independent workflow, no interaction currently; `VideoGenerationResult` does not carry opaque handles like `video_ref`, to be designed when enabled later)
- return_last_frame tail-frame chaining (conflicts with the core storyboard-driven video flow)
- Provider management page frontend UI (#102)

## Architecture Design

### Call Chain Changes

```
Before:
  execute_video_task → MediaGenerator → GeminiClient

After:
  execute_video_task → MediaGenerator → VideoBackend.generate()
                                           ├─ GeminiVideoBackend (genai SDK + shared infrastructure)
                                           └─ SeedanceVideoBackend (Ark SDK)
```

### File Structure

```
lib/
  video_backends/
    __init__.py              # Export public API
    base.py                  # Protocol + dataclasses + VideoCapability enum
    gemini.py                # GeminiVideoBackend — video logic extracted from GeminiClient
    seedance.py              # SeedanceVideoBackend — Volcano Ark SDK
    registry.py              # Provider registration + factory functions
```

## Core Interface

### VideoCapability Enum

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
    duration_seconds: int = 5              # uses int uniformly; each Backend normalizes to its own valid values
    resolution: str = "1080p"
    start_image: Path | None = None
    generate_audio: bool = True

    # Veo-specific
    negative_prompt: str | None = None

    # Seedance-specific
    service_tier: str = "default"          # "default" | "flex"
    seed: int | None = None
```

> **duration_seconds normalization rule**: The interface uniformly uses `int` (seconds). Veo only supports discrete values `4/6/8`, normalized internally by `GeminiVideoBackend` using the existing `normalize_veo_duration_seconds()`; Seedance 1.5 pro supports the continuous range `4-12` and passes it through directly.

### VideoGenerationResult

```python
@dataclass
class VideoGenerationResult:
    video_path: Path
    provider: str                          # "gemini" | "seedance"
    model: str                             # specific model ID
    duration_seconds: int

    # optional
    video_uri: str | None = None           # remote URI (Veo GCS / Seedance CDN)
    seed: int | None = None                # actual seed used
    usage_tokens: int | None = None        # Seedance token usage
    task_id: str | None = None             # provider task ID
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

## Backend Implementation

### GeminiVideoBackend

**Strategy**: Extract video logic from `GeminiClient`, not a thin wrapper.

- Uses the `google-genai` SDK directly
- Reuses `GeminiClient` shared infrastructure: `RateLimiter`, `with_retry_async` decorator, client initialization logic (aistudio/vertex dual backends)
- `GeminiClient` retains image generation + shared utilities; video methods are marked deprecated and delegate internally to `GeminiVideoBackend`

Capability set:
- `TEXT_TO_VIDEO`、`IMAGE_TO_VIDEO`、`VIDEO_EXTEND`
- `GENERATE_AUDIO` (Vertex backend)
- `NEGATIVE_PROMPT`

Initialization parameters:
- `backend_type: str` — "aistudio" | "vertex"
- `api_key: str | None` — AI Studio mode
- `rate_limiter: RateLimiter` — shared rate limiter
- `video_model: str` — model ID (default `veo-3.1-generate-001`)

### SeedanceVideoBackend

- Uses `volcengine-python-sdk[ark]` (`volcenginesdkarkruntime.Ark`)
- Async polling mode: `tasks.create()` → poll `tasks.get()` → download MP4
- Model: `doubao-seedance-1-5-pro-251215`

Capability set:
- `TEXT_TO_VIDEO`、`IMAGE_TO_VIDEO`
- `GENERATE_AUDIO`
- `SEED_CONTROL`、`FLEX_TIER`

Initialization parameters:
- `api_key: str` — Volcano Ark API key
- `model: str` — model ID (default `doubao-seedance-1-5-pro-251215`)
- `file_service_base_url: str` — project file service public URL (for image uploads)

**Polling Strategy**:
- `service_tier="default"` (online): poll interval 10s, timeout 600s
- `service_tier="flex"` (offline): poll interval 60s, timeout 172800s (48h)
- Task status `failed` / `expired` mapped to exceptions, handled uniformly by `GenerationWorker` as task failed

**Local image upload**: Seedance API requires images to be passed via URL. `SeedanceVideoBackend` constructs an upload request using `file_service_base_url`, uploading local storyboard images to the project file service to obtain a public URL. The upload logic is encapsulated inside the Backend and is transparent to callers.

**Seedance fixed parameters**: `watermark=False` (no watermark in production), `ratio` mapped directly from `aspect_ratio` (same format, e.g., `"16:9"`).

> **Deployment requirement**: When using the Seedance provider, the project deployment environment must be publicly accessible (Seedance API needs to fetch images via URL). Document the `FILE_SERVICE_BASE_URL` configuration in README and `.env.example`.

### Registry

```python
_BACKEND_FACTORIES: dict[str, Callable[..., VideoBackend]] = {}

def register_backend(name: str, factory: Callable[..., VideoBackend]):
    _BACKEND_FACTORIES[name] = factory

def create_backend(name: str, **kwargs) -> VideoBackend:
    """Create a Backend instance by name and config. Raises ValueError if required API key is missing."""
    if name not in _BACKEND_FACTORIES:
        raise ValueError(f"Unknown video backend: {name}")
    return _BACKEND_FACTORIES[name](**kwargs)

def get_available_backends() -> list[str]:
    """Return a list of registered providers with available API keys."""
    ...
```

Automatically registers `gemini` and `seedance` on startup. Missing API keys do not cause startup failure; they only cause errors when the provider is actually selected.

## Configuration Design

### Global Configuration (SystemConfigManager)

Global configuration is managed via the existing `SystemConfigManager` (`.system_config.json`), with the frontend operating through MediaConfigTab. The following new configuration fields are added:

| Config Field | Description | Environment Variable |
|--------|------|-------------|
| `video_provider` | Global default video provider (`gemini` \| `seedance`) | `DEFAULT_VIDEO_PROVIDER` |
| `ark_api_key` | Volcano Ark API key | `ARK_API_KEY` |
| `file_service_base_url` | Project file service public address (used for Seedance image uploads) | `FILE_SERVICE_BASE_URL` |

These configuration fields follow the existing mechanism: applied to `os.environ` immediately upon saving, no restart needed. MediaConfigTab needs to extend its UI to support video provider selection and Seedance API key input.

> Note: The MediaConfigTab UI changes fall under the provider management page (#102) scope. For now, only backend config read/write is supported; the frontend can temporarily configure via direct editing of `.system_config.json`.

### Project-Level Override (project.json)

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

Priority: `project.json` > global environment variable default.

When switching providers:
- Generic settings (`video_settings`) are reused as-is
- Provider-specific settings are retained under their namespace and restored when switching back
- Already generated video files are unaffected

### Parameter Sources

| Parameter | Source | Description |
|------|------|------|
| `prompt` | Per request | Different for each storyboard segment |
| `duration_seconds` | Per request | Can be specified per storyboard segment |
| `seed` | Per request (optional) | Passed manually when iterating |
| `resolution` | Project video_settings | Consistent across the project |
| `aspect_ratio` | Project video_settings | Consistent across the project |
| `generate_audio` | Project video_settings | Consistent across the project |
| `service_tier` | Project video_provider_settings.seedance | Seedance project-level |
| `negative_prompt` | Project video_provider_settings.gemini | Gemini project-level |

## Parameter Flow Chain

```
1. API layer (generate.py)
   POST /generate/video/{segment_id}
   Body: { prompt, duration, seed? }

2. Enqueue (GenerationQueue)
   snapshot provider + settings in payload_json (determined at enqueue time, unaffected by subsequent config changes)

3. Worker execution (execute_video_task)
   Construct VideoGenerationRequest from payload_json

4. MediaGenerator
   Version management + UsageTracker wrapping

5. VideoBackend.generate(request)
   Each Backend reads its required fields from the request
```

## MediaGenerator Adaptation

The existing `MediaGenerator` `self.video_backend` attribute (`media_generator.py:62`) stores a string `"aistudio"` | `"vertex"`. Rename it to `self._gemini_backend_type` and add `self._video_backend: VideoBackend` to store the Backend instance.

```python
class MediaGenerator:
    def __init__(self, ..., video_backend: VideoBackend | None = None):
        self._video_backend = video_backend
        # backward compatibility: auto-create GeminiVideoBackend if video_backend not provided
        if self._video_backend is None:
            self._video_backend = GeminiVideoBackend(...)

    async def generate_video_async(self, ...):
        # version management (VersionManager) and usage tracking (UsageTracker) remain at this layer
        # core call becomes:
        request = VideoGenerationRequest(...)
        result = await self._video_backend.generate(request)
```

**Backend instantiation responsibility**: `get_media_generator()` (`server/services/generation_tasks.py`) is responsible for reading project config, selecting the provider, creating a Backend instance through the Registry, and injecting it into `MediaGenerator`.

**Cross-cutting concerns stay above Backend**: version management and usage tracking are handled at the MediaGenerator layer; the Backend is only responsible for "calling the API and getting the result".

## CostCalculator + UsageTracker Extensions

### CostCalculator

Bills per provider using separate strategies, returns an `(amount: float, currency: str)` tuple:

- **Gemini**: table lookup by resolution × duration × audio (USD) — existing logic unchanged
- **Seedance**: obtains actual token usage from `usage.completion_tokens` in the API response, billed at unit price

### Seedance Cost Calculation Logic

**Calculation Formula**:

```
Cost (CNY) = usage_tokens / 1_000_000 × unit price (CNY/million tokens)
```

`usage_tokens` comes from the `usage.completion_tokens` field in the API response (only successfully generated videos are billed).

**Unit Price Table** (CNY/million tokens):

| Model | Online with audio | Online without audio | Offline with audio | Offline without audio |
|------|---------|---------|---------|---------|
| seedance-1.5-pro | 16.00 | 8.00 | 8.00 | 4.00 |

**Billing Dimension Mapping**:
- Online/Offline → `service_tier` (`"default"` = online, `"flex"` = offline)
- With audio/Without audio → `generate_audio` (`True` = with audio, `False` = without audio)

**Example**: 1080p 16:9 with audio 5-second video, online inference
- API returns `usage.completion_tokens = 246840` (≈ `1920 × 1080 × 24 × 5 / 1024`)
- Cost = `246840 / 1_000_000 × 16.00` = **3.95 CNY**

**Implementation note**: `CostCalculator` adds a `_seedance_video_cost(model, usage_tokens, service_tier, generate_audio)` method that looks up the unit price by `service_tier` and `generate_audio`, then multiplies by token usage.

Different currencies are tracked separately; no currency conversion is performed.

### UsageTracker (api_calls table)

Database migration plan:
1. Rename the existing `cost_usd` column to `cost_amount`
2. Add `currency` column (`String`, default `"USD"`, backfill existing data with `"USD"`)
3. Add `provider` column (`String`, default `"gemini"`, backfill existing data with `"gemini"`)
4. Add `usage_tokens` column (`Integer`, nullable)

Executed via Alembic migration script to ensure no existing data is lost.

`UsageRepository.get_stats()` updated to aggregate costs grouped by `currency`.

## Provider Capability Comparison

| Capability | Gemini Veo | Seedance 1.5 |
|------|-----------|--------------|
| Text to video | Y | Y |
| Image to video (first frame) | Y | Y |
| Audio generation | Y (Vertex) | Y |
| Negative prompt | Y | N |
| Video extension | Y | N |
| Seed control | N | Y |
| Offline inference (half price) | N | Y |

## Supported Parameters

**Generic parameters**: prompt, aspect_ratio, duration_seconds, resolution, start_image, generate_audio

**Seedance-specific**: service_tier, seed

**Veo-specific**: negative_prompt
