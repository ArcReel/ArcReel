# OpenAI Preset Provider Design Document

> Date: 2026-03-31 | Status: Confirmed | Branch: feature/openai-provider

## Overview

Add OpenAI as the fifth preset provider in ArcReel, supporting three media types: text (GPT-5.4), image (GPT Image 1.5), and video (Sora 2). Uses a "shared module + three independent backends" architecture, adding `openai_shared.py` following the existing `gemini_shared.py` pattern.

### Scope

- OpenAI preset provider (text + image + video)
- **Excludes** custom providers (next iteration)

### Key Decisions

| Decision Point | Conclusion | Rationale |
|---------------|------------|-----------|
| Architecture pattern | Shared `openai_shared.py` + three independent backends | Follows `gemini_shared.py` precedent; DRY and paves the way for custom providers |
| SDK | Unified use of `openai` SDK 2.30.0 | Already in dependencies; full API support for all three media types |
| Structured output | Native `response_format` first, Instructor fallback | Consistent with Gemini backend strategy |
| Image API | Images API (`generate` + `edit`) | Naturally aligned with the `ImageBackend` Protocol |
| Image I2I | `client.images.edit()` with reference images | Supports multiple reference image inputs |
| Video API | Native SDK `client.videos.create_and_poll()` | SDK 2.30.0 fully supports it with built-in polling |
| Video Seed | Not supported | SDK `VideoCreateParams` has no seed parameter |

---

## 1. Provider Registration and Constants

### `lib/providers.py`

```python
PROVIDER_OPENAI = "openai"
```

### `lib/config/registry.py`

```python
"openai": ProviderMeta(
    display_name="OpenAI",
    description="OpenAI official platform, supporting GPT-5.4 text, GPT Image image, and Sora video generation.",
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

**Design notes:**
- `optional_keys` includes `base_url`, paving the way for custom providers in the next iteration
- GPT-5.4 Mini is the default text model (high cost-performance ratio)
- Image supports `text_to_image` + `image_to_image` (T2I uses `images.generate()`, I2I uses `images.edit()`)
- Video supports `text_to_video` + `image_to_video` (Sora supports `input_reference`)

---

## 2. `openai_shared.py` Shared Module

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
    """Create AsyncOpenAI client with unified api_key and base_url handling."""
    kwargs: dict = {}
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url
    return AsyncOpenAI(**kwargs)
```

**Differences from `gemini_shared.py`:**
- No `RateLimiter` needed — OpenAI SDK has built-in retry and backoff
- No `with_retry_async` needed — SDK `max_retries` defaults to 2
- Only provides client factory + retryable error type exports
- Custom providers in the next iteration only need to pass a different `base_url` for reuse

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

**Key implementation details:**

1. **Message construction** — `_build_messages()` converts `request.prompt` / `system_prompt` / `images` to OpenAI messages format, images use `{"type": "image_url", "image_url": {"url": data_uri}}`
2. **Structured output** — `_build_response_format()` converts Pydantic model / JSON schema to `{"type": "json_schema", "json_schema": {...}}`, using the existing `resolve_schema()` utility
3. **Instructor fallback (future iteration)** — this iteration only implements native `response_format` structured output. The Instructor fallback path is a future optimization, to be added after confirming GPT-5.4 schema compatibility boundaries
4. **Usage fault tolerance** — `response.usage` may be None (compatible services); recorded as None without blocking

### Registration and Factory

- `text_backends/__init__.py`: `register_backend(PROVIDER_OPENAI, OpenAITextBackend)`
- `text_backends/factory.py`: `"openai": "openai"` mapping, passing `api_key` + `base_url` + `model`

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
    # base64 decode → write to output_path
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
    # base64 decode → write to output_path
```

**Size mapping** (`aspect_ratio` → OpenAI `size`):

| aspect_ratio | OpenAI size |
|--------------|-------------|
| `9:16` | `1024x1792` |
| `16:9` | `1792x1024` |
| `1:1` | `1024x1024` |

**Quality mapping** (`image_size` → OpenAI `quality`):

| image_size | quality |
|------------|---------|
| `512PX` | `low` |
| `1K` | `medium` |
| `2K` | `high` |
| `4K` | `high` |

### Registration

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
            raise RuntimeError(f"Sora video generation failed: {video.error}")

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

**Duration mapping** (`duration_seconds: int` → SDK `VideoSeconds`):

| duration_seconds | VideoSeconds |
|-----------------|--------------|
| ≤ 4 | `"4"` |
| 5-8 | `"8"` |
| ≥ 9 | `"12"` |

**Size mapping** (`aspect_ratio` → SDK `VideoSize`):

| aspect_ratio | VideoSize |
|--------------|-----------|
| `9:16` | `720x1280` |
| `16:9` | `1280x720` |

**Unsupported capabilities (not declared):**
- `GENERATE_AUDIO` — Sora does not independently control audio
- `NEGATIVE_PROMPT` — Sora does not support
- `SEED_CONTROL` — SDK VideoCreateParams has no seed parameter
- `FLEX_TIER` — Sora does not support

### Registration

- `video_backends/__init__.py`: `register_backend(PROVIDER_OPENAI, OpenAIVideoBackend)`

---

## 6. Cost Calculator Extension

### New Pricing Tables

```python
# OpenAI text rates (USD per million tokens)
OPENAI_TEXT_COST = {
    "gpt-5.4":      {"input": 2.50, "output": 15.00},
    "gpt-5.4-mini": {"input": 0.75, "output": 4.50},
    "gpt-5.4-nano": {"input": 0.20, "output": 1.25},
}

# OpenAI image rates (USD per image), by quality
OPENAI_IMAGE_COST = {
    "gpt-image-1.5":    {"low": 0.009, "medium": 0.034, "high": 0.133},
    "gpt-image-1-mini": {"low": 0.005, "medium": 0.011, "high": 0.036},
}

# OpenAI video rates (USD per second), by resolution
OPENAI_VIDEO_COST = {
    "sora-2":     {"720p": 0.10},
    "sora-2-pro": {"720p": 0.30, "1024p": 0.50, "1080p": 0.70},
}
```

### Unified Entry Point Extension

`calculate_cost()` adds `PROVIDER_OPENAI` branch:
- Text: `_TEXT_COST_TABLES` adds `"openai": ("OPENAI_TEXT_COST", "gpt-5.4-mini", "USD")`
- Image: add `calculate_openai_image_cost(model, quality)` method
- Video: add `calculate_openai_video_cost(duration_seconds, model, resolution)` method

`calculate_cost()` signature adds optional `quality` parameter, used only for OpenAI images.

> **`quality` upstream passing:** In this iteration, `UsageTracker` / `usage_repo` temporarily do not pass `quality`; OpenAI image costs will be calculated using the default value `"medium"`. Completing the `quality` pass-through chain from Backend → UsageTracker → CostCalculator is a future optimization.

---

## 7. Connection Test

### `server/routers/providers.py`

```python
def _test_openai(config: dict[str, str]) -> ConnectionTestResponse:
    """Verify OpenAI API Key via models.list(). Synchronous function called via asyncio.to_thread by the framework."""
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
        message="Connection successful",
    )
```

Registered in `_TEST_DISPATCH["openai"] = _test_openai`.

> **Note:** Using the synchronous `OpenAI` client rather than `AsyncOpenAI`, because the existing framework runs all connection test functions in a thread pool via `asyncio.to_thread(test_fn, config)` (consistent with `_test_grok`, `_test_ark`, etc.).

---

## 8. Frontend Changes

### No Changes Needed

The frontend is already data-driven; registering a new provider on the backend automatically displays it:
- Provider list page — dynamically rendered
- Configuration form — dynamically generated
- Credential management — already generalized
- Connection test button — already generalized
- Backend selection dropdown — dynamically fetched

### Changes Needed

- **Provider icon** — use lobe-icons' OpenAI icon, also update `PROVIDER_NAMES` mapping
- **`config-status-store.ts`** — confirmed to be completely dynamic; no changes needed

---

## 9. Testing Strategy

### Unit Tests

| File | Coverage |
|------|----------|
| `test_openai_text_backend.py` | Message construction, structured output, Instructor fallback, vision, usage fault tolerance |
| `test_openai_image_backend.py` | T2I/I2I path dispatch, b64 decode write, size mapping, quality mapping |
| `test_openai_video_backend.py` | T2V/I2V, duration/size mapping, failed status exception, download_content |
| `test_cost_calculator.py` (extended) | OpenAI three media type pricing calculation |

### Integration Point Tests

- Registry: verify `PROVIDER_REGISTRY["openai"]` exists and media_types covers text/image/video
- Factory: verify OpenAI configuration returns `OpenAITextBackend` when ready
- Connection test: mock `client.models.list()` to verify connection test path

### Excluded

- End-to-end API call tests (require real API Key)
- Frontend tests (almost no frontend changes)

---

## File Change Checklist

### New Files

| File | Description |
|------|-------------|
| `lib/openai_shared.py` | Shared client factory + retryable error types |
| `lib/text_backends/openai.py` | OpenAI text backend |
| `lib/image_backends/openai.py` | OpenAI image backend |
| `lib/video_backends/openai.py` | OpenAI video backend |
| `tests/test_openai_text_backend.py` | Text backend tests |
| `tests/test_openai_image_backend.py` | Image backend tests |
| `tests/test_openai_video_backend.py` | Video backend tests |

### Modified Files

| File | Change |
|------|--------|
| `pyproject.toml` | `openai>=2.30.0` |
| `lib/providers.py` | Add `PROVIDER_OPENAI` constant |
| `lib/config/registry.py` | Add OpenAI ProviderMeta |
| `lib/cost_calculator.py` | Add OpenAI pricing tables + calculation methods, add quality parameter to `calculate_cost()` |
| `lib/text_backends/__init__.py` | Register OpenAITextBackend |
| `lib/text_backends/factory.py` | Add `"openai": "openai"` mapping + parameter passing |
| `lib/image_backends/__init__.py` | Register OpenAIImageBackend |
| `lib/video_backends/__init__.py` | Register OpenAIVideoBackend |
| `server/routers/providers.py` | Add `_test_openai` connection test |
| `server/services/generation_tasks.py` | Add `PROVIDER_OPENAI` to mapping tables, `_DEFAULT_VIDEO_RESOLUTION`, factory branch |
| `tests/test_cost_calculator.py` | Extend OpenAI pricing test cases |
| Frontend: `ProviderIcon.tsx` | Add OpenAI lobe-icons icon + `PROVIDER_NAMES` mapping |
