# Custom Provider Design Document

> Date: 2026-03-31
> Branch: feature/custom-provider
> Related: docs/proposal-openai-and-custom-provider.md Part 2, Issue #189

---

## Overview

Support users adding custom providers, connecting any compatible service via API format + Base URL + API Key + model list. Two API formats are supported: OpenAI-compatible and Google-compatible (API Key authentication mode only).

Also addresses 3 of the OpenAI preset provider improvements from Issue #189 (excluding rate limiting).

## Scope

### Custom Providers (main body)

- CRUD for custom providers
- Dynamic model list management (auto-discovery + manual add)
- User-defined pricing
- Lightweight backend wrapper classes (reusing existing OpenAI/Gemini backends)
- Integration with ConfigResolver, CostCalculator, and usage statistics
- Complete frontend UI

### #189 Remaining Improvements

- Instructor fallback structured output degradation
- quality parameter pass-through chain
- Video resolution parameter mapping

### Excluded

- RPM / request_gap rate limiting
- Vertex AI authentication mode for Google-compatible format

---

## Architecture: Parallel Tracks

Preset providers retain the existing `PROVIDER_REGISTRY` unchanged. Custom providers have independent API endpoints, Service, and frontend area. The two converge at the following points:

1. **Backend selection** — ConfigResolver queries both preset and custom providers when resolving the default backend
2. **Model selection dropdown** — `/api/v1/system-config/options` merges available models from both
3. **Cost recording** — ApiCall table records via `provider` field (`custom-{id}`) uniformly
4. **Usage statistics** — backend API returns `display_name`; preset providers source from `PROVIDER_REGISTRY`, custom providers join the `custom_provider` table

---

## 1. Data Model

### New Table `custom_provider`

| Field | Type | Description |
|-------|------|-------------|
| `id` | int PK | Auto-increment primary key; internal identifier is `custom-{id}` |
| `display_name` | str | User-visible name, e.g., "My NewAPI" |
| `api_format` | str | `"openai"` or `"google"` |
| `base_url` | str | API base URL |
| `api_key` | str | Sensitive field; stored in plaintext in DB, masked in API responses (reusing existing `mask_secret()`) |
| `created_at` / `updated_at` | datetime | Timestamps |

Design choice: `api_key` and `base_url` are stored directly in the provider table, not reusing the `provider_credential` table. Custom providers follow a simple "one provider = one relay address + one key" model; multi-credential switching is not needed.

### New Table `custom_provider_model`

| Field | Type | Description |
|-------|------|-------------|
| `id` | int PK | Auto-increment primary key |
| `provider_id` | int FK → custom_provider.id | Parent provider |
| `model_id` | str | Model identifier, e.g., `deepseek-v3` |
| `display_name` | str | Display name |
| `media_type` | str | `"text"` / `"image"` / `"video"` |
| `is_default` | bool | Default model for this media_type under this provider |
| `is_enabled` | bool | Whether enabled (user-selected) |
| `price_unit` | str NULL | Billing unit: `"token"` / `"image"` / `"second"` |
| `price_input` | float NULL | Input price (text: per million tokens, image: per image, video: per second) |
| `price_output` | float NULL | Output price (text only; NULL for others) |
| `currency` | str NULL | `"USD"` / `"CNY"` |
| `created_at` / `updated_at` | datetime | Timestamps |

Unique constraint: `(provider_id, model_id)`.

`is_default` constraint: each `(provider_id, media_type)` combination can have at most one `is_default=True`, enforced at the application layer.

Pricing is optional: all NULL means no billing (local scenarios like Ollama).

---

## 2. Backend Layer

### Lightweight Wrapper Classes

Three wrapper classes (`CustomTextBackend`, `CustomImageBackend`, `CustomVideoBackend`), approximately 30 lines each. Each holds an internal delegate (an existing OpenAI/Gemini Backend instance), overriding `name` and `model` attributes:

```python
# lib/custom_provider/backends.py
class CustomTextBackend:
    def __init__(self, *, provider_id: str, delegate: TextBackend, model: str):
        self._provider_id = provider_id   # "custom-3"
        self._delegate = delegate
        self._model = model

    @property
    def name(self) -> str:
        return self._provider_id

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[TextCapability]:
        return self._delegate.capabilities

    async def generate(self, request):
        return await self._delegate.generate(request)
```

### Build Process

Custom backends are not registered in `_BACKEND_FACTORIES`; instead they are built on demand:

```python
# lib/custom_provider/factory.py
async def create_custom_backend(provider_id: str, model_id: str, media_type: str):
    # 1. Query custom_provider + custom_provider_model from DB
    # 2. Select internal delegate based on api_format:
    #    - "openai" → OpenAITextBackend / OpenAIImageBackend / OpenAIVideoBackend
    #    - "google" → GeminiTextBackend / GeminiImageBackend / GeminiVideoBackend
    # 3. Initialize delegate with base_url + api_key + model_id
    # 4. Wrap as CustomXxxBackend(provider_id=..., delegate=..., model=...)
```

### ConfigResolver Integration

`ConfigResolver._auto_resolve_backend()` is extended to: first check preset providers, then check custom providers for models that are enabled and `is_default=True`.

Explicitly set default backends (e.g., `"custom-3:deepseek-v3"`) go directly to the custom factory path.

---

## 3. Cost Calculation

### CostCalculator Extension

```python
def calculate_cost(self, provider, call_type, *, model, ...):
    if provider.startswith("custom-"):
        return self._calculate_custom_cost(provider, call_type, model=model, ...)
    # ... existing preset provider logic unchanged
```

`_calculate_custom_cost()` queries price fields from `custom_provider_model`:

| Media Type | Calculation | Required Parameters |
|-----------|-------------|---------------------|
| text | `input_tokens * price_input / 1M + output_tokens * price_output / 1M` | input_tokens, output_tokens |
| image | `count * price_input` | image count (default 1) |
| video | `duration_seconds * price_input` | duration in seconds |

When price fields are NULL, returns `(0.0, currency)` without blocking usage.

### UsageTracker Pass-through

ApiCall records store `custom-{id}` in `provider` and the actual model ID in `model`. Usage statistics API joins the `custom_provider` table to get `display_name` on return.

---

## 4. Service Layer

### CustomProviderService (`lib/custom_provider/service.py`)

**CRUD operations:**

- `create_provider(display_name, api_format, base_url, api_key, models: list)` → create provider + model list in one operation
- `update_provider(provider_id, ...)` → update configuration
- `delete_provider(provider_id)` → cascade delete associated models
- `list_providers()` → return all custom providers and their status
- `get_provider(provider_id)` → single provider details (including model list)

**Model management:**

- `add_model(provider_id, model_id, display_name, media_type, ...)` → manual add
- `update_model(model_id, ...)` → update price/media type/enabled status/default flag
- `remove_model(model_id)` → delete model
- `set_default_model(model_id)` → set as default for the media type

**Stateless operations (do not depend on an already-saved provider):**

- `discover_models(api_format, base_url, api_key)` → automatic model discovery
- `test_connection(api_format, base_url, api_key)` → connection test

### Model Auto-Discovery Logic

```
discover_models(api_format, base_url, api_key):
  1. Call by format:
     - OpenAI: GET {base_url}/models → returns list of model IDs
     - Google: genai.Client(api_key=...).models.list() → returns model list
  2. Media type inference (OpenAI format):
     - Model ID contains image/dall → "image"
     - Model ID contains video/sora/kling/wan/seedance/cog/mochi → "video"
     - Everything else → "text"
  3. Google format: infer from model supported_generation_methods
     (contains generateContent → text, contains generateImages → image, contains predictVideo → video);
     if unavailable, fall back to keyword inference
  4. Mark the first model of each media type as is_default
  5. Return inferred results (without writing to DB); frontend displays for user confirmation before saving
```

---

## 5. API Route Layer

Route prefix `/api/v1/custom-providers/`.

**Provider CRUD:**

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | List all custom providers (including model list and status) |
| POST | `/` | Create provider + model list (in one operation) |
| GET | `/{id}` | Single provider details |
| PATCH | `/{id}` | Update provider configuration |
| DELETE | `/{id}` | Delete provider (cascade delete models) |

**Model Management:**

| Method | Endpoint | Description |
|--------|----------|-------------|
| PUT | `/{id}/models` | Batch replace entire model list (delete old list, write new list) |
| POST | `/{id}/models` | Add single model |
| PATCH | `/{id}/models/{model_id}` | Update single model |
| DELETE | `/{id}/models/{model_id}` | Delete single model |

**Stateless operations:**

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/discover` | Model discovery |
| POST | `/test` | Connection test |

**Convergence point:** `/api/v1/system-config/options` is extended to append enabled models from custom providers to the corresponding media type option list, in the format `"custom-{id}:{model_id}"`.

---

## 6. Frontend

### Page Structure

Add a "Custom Providers" section at the bottom of `ProviderSection`; clicking an entry displays `CustomProviderDetail`.

```
Settings → Providers
  ┌─ Preset Providers ──────────────────┐
  │  Google AI Studio    ● Configured   │
  │  ...                                │
  ├─ Custom Providers ──────────────────┤
  │  My NewAPI           ● Connected    │
  │  Local Ollama        ● Connected    │
  │  [+ Add Custom Provider]            │
  └─────────────────────────────────────┘
```

### Create/Edit Form

1. Basic info: name, API format (dropdown), Base URL, API Key
2. [Fetch Model List] → calls `/discover`
3. Model list: check to enable, correct media type, mark default, fill in pricing
4. [Test Connection] → calls `/test`
5. [Save] → submit in one operation

### Integration Points

- **Model selector**: `system-config/options` already includes custom models; `ProviderModelSelect` needs no special handling
- **ProviderIcon**: already has a fallback (show first letter); custom providers automatically use it
- **Usage statistics**: backend returns `display_name`; frontend changes to use `display_name ?? provider`

### New Files

- `frontend/src/components/pages/settings/CustomProviderSection.tsx`
- `frontend/src/components/pages/settings/CustomProviderDetail.tsx`
- `frontend/src/components/pages/settings/CustomProviderForm.tsx`
- `frontend/src/types/custom-provider.ts`

**Note**: Call the `/frontend-design` skill before implementing the frontend.

---

## 7. #189 Remaining Improvements

### 7.1 Instructor Fallback Structured Output Degradation

File: `lib/text_backends/openai.py`

Following the Gemini backend pattern: catch exceptions on native `response_format` failure and fall back to Instructor library parsing. This improvement also benefits custom providers (OpenAI-compatible relays may not support `response_format`).

### 7.2 quality Parameter Pass-through Chain

Files: `lib/image_backends/base.py`, `lib/image_backends/openai.py`, `lib/usage_tracker.py`

`ImageGenerationResult` adds optional field `quality: str | None`; `OpenAIImageBackend` fills in the actual value; `UsageTracker` passes it through to `CostCalculator`.

### 7.3 Video Resolution Parameter Mapping

File: `lib/video_backends/openai.py`

Map to the exact VideoSize based on the `(resolution, aspect_ratio)` combination, rather than relying solely on aspect_ratio.

---

## New/Modified File Checklist

### New Files

| File | Description |
|------|-------------|
| `lib/custom_provider/__init__.py` | Module entry point |
| `lib/custom_provider/service.py` | CustomProviderService |
| `lib/custom_provider/factory.py` | Custom backend construction |
| `lib/custom_provider/backends.py` | Wrapper classes (Custom{Text,Image,Video}Backend) |
| `lib/custom_provider/discovery.py` | Model auto-discovery logic |
| `lib/db/models/custom_provider.py` | ORM models |
| `lib/db/repositories/custom_provider_repo.py` | Data repository |
| `alembic/versions/xxx_add_custom_provider.py` | Database migration |
| `server/routers/custom_providers.py` | API routes |
| `frontend/src/types/custom-provider.ts` | TypeScript types |
| `frontend/src/components/pages/settings/CustomProviderSection.tsx` | List UI |
| `frontend/src/components/pages/settings/CustomProviderDetail.tsx` | Detail panel |
| `frontend/src/components/pages/settings/CustomProviderForm.tsx` | Create/edit form |
| `tests/test_custom_provider_service.py` | Service unit tests |
| `tests/test_custom_provider_api.py` | API integration tests |

### Modified Files

| File | Modification |
|------|-------------|
| `lib/config/resolver.py` | Extend `_auto_resolve_backend()` to query custom providers |
| `lib/cost_calculator.py` | Add `_calculate_custom_cost()` branch |
| `lib/usage_tracker.py` | Pass through quality parameter |
| `lib/text_backends/openai.py` | Instructor fallback |
| `lib/image_backends/openai.py` | quality pass-through |
| `lib/image_backends/base.py` | Add quality field to `ImageGenerationResult` |
| `lib/video_backends/openai.py` | Resolution mapping |
| `lib/db/repositories/usage_repo.py` | Usage statistics join display_name |
| `server/routers/system_config.py` | options merge custom models |
| `server/routers/usage.py` | Return display_name |
| `server/app.py` | Register new routes |
| `frontend/src/components/pages/settings/ProviderSection.tsx` | Integrate custom provider section |
| `frontend/src/components/pages/settings/UsageStatsSection.tsx` | Display display_name |
