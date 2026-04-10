# GrokVideoBackend Integration Design

> Issue: [#100](https://github.com/ArcReel/ArcReel/issues/100) — grok-imagine-video integration
> Parent task: [#98](https://github.com/ArcReel/ArcReel/issues/98) — multi-provider video generation
> Prerequisite: [#99](https://github.com/ArcReel/ArcReel/issues/99) — extract generic video generation service layer (completed)

## Scope

Backend-only integration. Frontend config page changes are deferred to #102 (provider management page).

## 1. GrokVideoBackend Implementation

Create `lib/video_backends/grok.py`.

### SDK Selection

Uses the official `xai_sdk` Python SDK. Calls the `grok-imagine-video` model via `xai_sdk.AsyncClient`.

### Model Constants

```python
DEFAULT_MODEL = "grok-imagine-video"
```

### Initialization

```python
def __init__(self, *, api_key: str | None = None, model: str | None = None):
```

- `api_key`: passed from constructor parameters (sourced from the `XAI_API_KEY` environment variable set by the WebUI config page)
- Creates `xai_sdk.AsyncClient(api_key=api_key)`

### Capability Set

```python
{VideoCapability.TEXT_TO_VIDEO, VideoCapability.IMAGE_TO_VIDEO}
```

Not supported: `GENERATE_AUDIO`, `NEGATIVE_PROMPT`, `VIDEO_EXTEND`, `SEED_CONTROL`, `FLEX_TIER`.

### generate() Flow

1. Build parameters: prompt, aspect_ratio, duration (integer 1-15 seconds, passed directly)
2. If `start_image` provided: read the local file, base64-encode as `data:image/{ext};base64,{data}`
3. Call `client.video.generate(...)` (exact parameter names follow the actual `xai_sdk` API; refer to `docs/grok-docs/video-generation.md` when implementing)
4. SDK handles polling automatically; the result contains a temporary video URL
5. Async-download the video to `output_path` using `httpx.AsyncClient`
6. Return `VideoGenerationResult(video_path=output_path, provider="grok", model=model, duration_seconds=...)`

### Resolution

Read from `VideoGenerationRequest.resolution` (injected by the caller from `video_model_settings`). Only `480p` / `720p` are supported.

## 2. Registration and Factory

### base.py

Add constant:

```python
PROVIDER_GROK = "grok"
```

### __init__.py

Add registration:

```python
from lib.video_backends.grok import GrokVideoBackend
register_backend(PROVIDER_GROK, GrokVideoBackend)
```

Consistent with Gemini/Seedance: automatically registered on module load.

### generation_tasks.py

`_get_or_create_video_backend()` adds a `grok` branch:

```python
elif provider_name == PROVIDER_GROK:
    kwargs = {
        "api_key": os.environ.get("XAI_API_KEY"),
        "model": provider_settings.get("model"),
    }
```

Reuses the `(provider_name, model)` caching strategy.

## 3. Billing and Usage Tracking

### CostCalculator

Add Grok billing dict and instance method (consistent with the existing `calculate_video_cost` / `calculate_seedance_video_cost` pattern):

```python
GROK_VIDEO_COST = {
    "grok-imagine-video": 0.050,  # USD/second, no resolution distinction (source: docs/grok-docs/models.md)
}

def calculate_grok_video_cost(self, duration_seconds: int, model: str) -> float:
    per_second = GROK_VIDEO_COST.get(model, 0.050)
    return duration_seconds * per_second
```

Currency: USD (consistent with Gemini).

> **Note**: $0.050/second is a reference value; verify against the official xAI pricing page when implementing.

### UsageRepository

`finish_call()` adds a `PROVIDER_GROK` branch:

- Routes to `calculate_grok_video_cost()` based on `row.provider`
- Calculates cost using `duration_seconds` (extracted from `VideoGenerationResult`) × unit price
- Does not depend on `usage_tokens` (Grok billed per second)

## 4. Configuration Management

### SystemConfigManager

**`_ENV_KEYS`** adds:

```python
"XAI_API_KEY"
```

**`_apply_to_env()`** adds the `xai_api_key` → `XAI_API_KEY` mapping (consistent with the `ark_api_key` → `ARK_API_KEY` pattern), ensuring that WebUI config writes are correctly applied to environment variables.

`DEFAULT_VIDEO_PROVIDER` valid values extended to `gemini | seedance | grok`.

### Resolution: Model-Level Sub-Configuration

Resolution is read from `video_model_settings.{model}.resolution`, not at the global or provider level.

Configuration structure example (`.system_config.json`):

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

**Resolution injection point**: In `execute_video_task()` in `server/services/generation_tasks.py`, before constructing `VideoGenerationRequest`, look up the resolution for the currently selected model from `video_model_settings` and set it on `request.resolution`. Default values per model:

| Model | Default Resolution |
|------|-----------|
| veo-3.1-* | 1080p |
| seedance-1.5-* | 720p |
| grok-imagine-video | 720p |

## 5. Testing Strategy

### Unit Tests

`tests/test_grok_video_backend.py`：

- mock `xai_sdk.AsyncClient`, verify `generate()` correctly builds parameters and returns `VideoGenerationResult`
- text-to-video path: verify prompt, aspect_ratio, duration are correctly passed
- image-to-video path: verify local image is base64-encoded and passed
- Unsupported capabilities (e.g., `generate_audio`) are correctly ignored

### Billing Tests

`tests/test_cost_calculator.py`：

- Add Grok billing test cases: verify `calculate_grok_video_cost()` bills per second

### Not Added

- Integration tests (require a real API key)
- Frontend tests (this change does not involve frontend modifications)

## Affected File Checklist

| File | Action |
|------|------|
| `lib/video_backends/grok.py` | Add |
| `lib/video_backends/base.py` | Modify (add `PROVIDER_GROK`) |
| `lib/video_backends/__init__.py` | Modify (register Grok) |
| `lib/cost_calculator.py` | Modify (add Grok billing) |
| `lib/db/repositories/usage_repo.py` | Modify (`finish_call()` add Grok branch) |
| `lib/system_config.py` | Modify (`_ENV_KEYS` + `_apply_to_env`) |
| `server/services/generation_tasks.py` | Modify (factory + resolution injection) |
| `pyproject.toml` | Modify (add `xai_sdk` dependency) |
| `tests/test_grok_video_backend.py` | Add |
| `tests/test_cost_calculator.py` | Modify (add test cases) |
