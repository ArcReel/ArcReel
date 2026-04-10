# Provider Management Page Design

> Issue: [#102](https://github.com/ArcReel/ArcReel/issues/102)(sub-task of [#98](https://github.com/ArcReel/ArcReel/issues/98))
> Date: 2026-03-18

## Overview

As multiple providers (Gemini AI Studio, Gemini Vertex AI, Seedance, Grok) are integrated, the following are needed:

1. Migrate system configuration storage from JSON files to the database
2. Refactor the global settings page to a sidebar layout, adding provider management and usage statistics
3. Layer APIs by responsibility, adding `/api/v1/providers` routes
4. Fix project settings page routing, supporting project-level model overrides

## 1. Data Model

### 1.1 Provider Registry (Static, Code-Maintained)

Each provider's metadata is defined in code, not stored in the database:

```python
PROVIDER_REGISTRY = {
    "gemini-aistudio": ProviderMeta(
        display_name="Gemini AI Studio",
        media_types=["video", "image"],
        required_keys=["api_key"],
        optional_keys=["base_url", "image_rpm", "video_rpm", "request_gap", "image_max_workers", "video_max_workers"],
        secret_keys=["api_key"],
    ),
    "gemini-vertex": ProviderMeta(
        display_name="Gemini Vertex AI",
        media_types=["video", "image"],
        required_keys=["credentials_path"],
        optional_keys=["gcs_bucket", "image_rpm", "video_rpm", "request_gap", "image_max_workers", "video_max_workers"],
        secret_keys=[],
    ),
    "seedance": ProviderMeta(
        display_name="Seedance",
        media_types=["video"],
        required_keys=["api_key"],
        optional_keys=["file_service_base_url", "video_rpm", "request_gap", "video_max_workers"],
        secret_keys=["api_key"],
    ),
    "grok": ProviderMeta(
        display_name="Grok",
        media_types=["video"],
        required_keys=["api_key"],
        optional_keys=["video_rpm", "request_gap", "video_max_workers"],
        secret_keys=["api_key"],
    ),
}
```

Each `ProviderMeta` also contains a `capabilities` field, statically defining the list of capabilities supported by that provider (e.g., `text_to_video`, `image_to_video`, `generate_audio`, etc.). These values correspond directly to the `VideoBackend.capabilities` / `ImageBackend` capability enums, but are maintained statically in the registry without needing to instantiate a backend.

```python
# capabilities examples (included in ProviderMeta)
# gemini-aistudio: [text_to_video, image_to_video, text_to_image, negative_prompt, video_extend]
# gemini-vertex:   [text_to_video, image_to_video, text_to_image, generate_audio, negative_prompt, video_extend]
# seedance:        [text_to_video, image_to_video, generate_audio, seed_control, flex_tier]
# grok:            [text_to_video, image_to_video]
```

### 1.2 Database Tables

**`provider_config` — Provider Configuration**

| Column | Type | Description |
|---|---|---|
| id | INTEGER PK | Auto-increment primary key |
| provider | VARCHAR(32) NOT NULL | Provider identifier (gemini-aistudio, gemini-vertex, seedance, grok) |
| key | VARCHAR(64) NOT NULL | Config key (api_key, base_url, credentials_path, gcs_bucket, file_service_base_url) |
| value | TEXT NOT NULL | Config value |
| is_secret | BOOLEAN NOT NULL DEFAULT false | Whether the field is sensitive; controls GET response masking |
| updated_at | DATETIME NOT NULL | Last updated timestamp |

Unique constraint: `UNIQUE(provider, key)`

**`system_setting` — Global System Settings**

| Column | Type | Description |
|---|---|---|
| id | INTEGER PK | Auto-increment primary key |
| key | VARCHAR(64) UNIQUE NOT NULL | Setting key |
| value | TEXT NOT NULL | Setting value |
| updated_at | DATETIME NOT NULL | Last updated timestamp |

Keys stored in system_setting include:
- `default_video_backend` — Format `{provider_id}/{model_id}`, e.g., `gemini-vertex/veo-3.1-fast-generate-001`
- `default_image_backend` — Same format, e.g., `gemini-aistudio/gemini-3.1-flash-image-preview`
- `video_generate_audio` — `true` / `false`
- `anthropic_api_key` — Agent API Key
- `anthropic_base_url` — Agent proxy address
- Other settings previously managed by AdvancedConfigTab

### 1.3 Module Structure

```
lib/config/
├── models.py          # ORM: ProviderConfig, SystemSetting
├── repository.py      # Async CRUD: ProviderConfigRepository, SystemSettingRepository
├── service.py         # ConfigService business logic
├── registry.py        # PROVIDER_REGISTRY static metadata
└── migration.py       # JSON → DB one-time migration
```

### 1.4 ConfigService Interface

```python
class ConfigService:
    # Provider configuration
    async def get_provider_config(self, provider: str) -> dict[str, str]
    async def set_provider_config(self, provider: str, key: str, value: str) -> None
    async def delete_provider_config(self, provider: str, key: str) -> None
    async def get_all_providers_status(self) -> list[ProviderStatus]

    # Global settings
    async def get_setting(self, key: str, default: str = "") -> str
    async def set_setting(self, key: str, value: str) -> None

    # Convenience methods
    async def get_default_video_backend(self) -> tuple[str, str]  # (provider_id, model_id)
    async def get_default_image_backend(self) -> tuple[str, str]
```

```python
@dataclass
class ProviderStatus:
    name: str                              # "gemini-aistudio"
    display_name: str                      # "Gemini AI Studio"
    status: Literal["ready", "unconfigured", "error"]
    media_types: list[str]                 # ["video", "image"]
    capabilities: list[str]                # ["text_to_video", ...]
    required_keys: list[str]               # ["api_key"]
    configured_keys: list[str]             # List of configured keys
    missing_keys: list[str]                # Missing required keys
```

## 2. JSON → DB Migration

### 2.1 Trigger Condition

On application startup, check if `projects/.system_config.json` exists.

### 2.2 Migration Mapping

| Original JSON Field | Target Table | provider / key |
|---|---|---|
| `gemini_api_key` | provider_config | gemini-aistudio / api_key |
| `gemini_base_url` | provider_config | gemini-aistudio / base_url |
| Vertex credentials file path | provider_config | gemini-vertex / credentials_path |
| `vertex_gcs_bucket` | provider_config | gemini-vertex / gcs_bucket |
| `ark_api_key` | provider_config | seedance / api_key |
| `file_service_base_url` | provider_config | seedance / file_service_base_url |
| `xai_api_key` | provider_config | grok / api_key |
| `image_backend` ("aistudio"/"vertex") | system_setting | default_image_backend → converted to `gemini-{value}/{current image_model}` |
| `video_backend` ("aistudio"/"vertex") | system_setting | default_video_backend → converted to `gemini-{value}/{current video_model}` |
| `video_model` | Used in default_video_backend combination | — |
| `image_model` | Used in default_image_backend combination | — |
| `video_generate_audio` | system_setting | video_generate_audio |
| `anthropic_api_key` | system_setting | anthropic_api_key |
| `anthropic_base_url` | system_setting | anthropic_base_url |
| `anthropic_model` | system_setting | anthropic_model |
| `anthropic_default_haiku_model` | system_setting | anthropic_default_haiku_model |
| `anthropic_default_opus_model` | system_setting | anthropic_default_opus_model |
| `anthropic_default_sonnet_model` | system_setting | anthropic_default_sonnet_model |
| `claude_code_subagent_model` | system_setting | claude_code_subagent_model |
| `gemini_image_rpm` | provider_config | gemini-aistudio / image_rpm and gemini-vertex / image_rpm (Gemini-specific; not written for other providers) |
| `gemini_video_rpm` | provider_config | gemini-aistudio / video_rpm and gemini-vertex / video_rpm (Gemini-specific; not written for other providers) |
| `gemini_request_gap` | provider_config | gemini-aistudio / request_gap and gemini-vertex / request_gap (Gemini-specific; not written for other providers) |
| `image_max_workers` | provider_config | Written to image_max_workers for all configured providers that support image (migrating the old global value) |
| `video_max_workers` | provider_config | Written to video_max_workers for all configured providers that support video (migrating the old global value) |
| Other unlisted override keys | system_setting | Written directly using the original key name |

### 2.3 Migration Completion

After successful migration, rename `.system_config.json` to `.system_config.json.bak` to prevent re-migration.

## 3. API Design

### 3.1 `/api/v1/providers` — Provider Management

**GET /api/v1/providers**

Returns all providers and their status.

```json
{
  "providers": [
    {
      "id": "gemini-aistudio",
      "display_name": "Gemini AI Studio",
      "status": "ready",
      "media_types": ["video", "image"],
      "capabilities": ["text_to_video", "image_to_video", "text_to_image", "negative_prompt", "video_extend"],
      "configured_keys": ["api_key"],
      "missing_keys": []
    }
  ]
}
```

**GET /api/v1/providers/{id}/config**

Returns configuration field details for a single provider.

```json
{
  "id": "gemini-aistudio",
  "display_name": "Gemini AI Studio",
  "status": "ready",
  "fields": [
    {
      "key": "api_key",
      "label": "API Key",
      "type": "secret",
      "required": true,
      "value_masked": "AIza…••••",
      "is_set": true
    },
    {
      "key": "base_url",
      "label": "Base URL",
      "type": "url",
      "required": false,
      "value": "",
      "is_set": false,
      "placeholder": "Default official address"
    }
  ]
}
```

**PATCH /api/v1/providers/{id}/config**

Updates provider configuration. A `null` value clears that field.

```json
{ "api_key": "AIza-new-key", "base_url": null }
```

**POST /api/v1/providers/{id}/test**

Connection test, returns list of available models. Testing strategy differs per provider:
- **gemini-aistudio / gemini-vertex**: Calls the list models API to verify credentials and connectivity
- **seedance / grok**: If the API does not support list models, sends a lightweight validation request (e.g., fetching account info or sending a minimal parameter request); returns success/failure, with `available_models` being the models registered for that provider in the registry

```json
{
  "success": true,
  "available_models": ["veo-3.1-generate-001", "veo-3.1-fast-generate-001"],
  "message": "Connection successful, found 2 available models"
}
```

**POST /api/v1/providers/gemini-vertex/credentials**

Vertex AI credentials file upload (special endpoint); retains existing upload logic.

### 3.2 `/api/v1/system/config` — Global Settings

Slimmed down to manage only non-provider configuration.

**GET /api/v1/system/config**

```json
{
  "settings": {
    "default_video_backend": "gemini-vertex/veo-3.1-fast-generate-001",
    "default_image_backend": "gemini-aistudio/gemini-3.1-flash-image-preview",
    "video_generate_audio": false,
    "anthropic_api_key": { "is_set": true, "masked": "sk-…••••" },
    "anthropic_base_url": "https://xxx.com"
  },
  "options": {
    "video_backends": [
      "gemini-aistudio/veo-3.1-generate-001",
      "gemini-aistudio/veo-3.1-fast-generate-001",
      "gemini-vertex/veo-3.1-generate-001",
      "gemini-vertex/veo-3.1-fast-generate-001",
      "seedance/doubao-seedance-1-5-pro-251215",
      "grok/grok-imagine-video"
    ],
    "image_backends": [
      "gemini-aistudio/gemini-3.1-flash-image-preview",
      "gemini-vertex/gemini-3.1-flash-image-preview"
    ]
  }
}
```

`options` only lists models from providers with status=ready.

**PATCH /api/v1/system/config**

```json
{ "default_video_backend": "seedance/doubao-seedance-1-5-pro-251215" }
```

### 3.3 `/api/v1/usage/stats` — Usage Statistics

Extends the existing usage API with filtering and grouping support.

**GET /api/v1/usage/stats?provider=gemini-vertex&start=2026-03-01&end=2026-03-18&group_by=provider**

```json
{
  "stats": [
    {
      "provider": "gemini-vertex",
      "call_type": "video",
      "total_calls": 42,
      "success_calls": 38,
      "total_cost_usd": 12.50,
      "total_duration_seconds": 380
    }
  ],
  "period": { "start": "2026-03-01", "end": "2026-03-18" }
}
```

### 3.4 API Responsibility Summary

| Route | Responsibility | Corresponding Frontend Section |
|---|---|---|
| `/api/v1/providers` | Provider CRUD, connection test | Providers |
| `/api/v1/system/config` | Global default settings | Agent + Image/Video |
| `/api/v1/usage/stats` | Usage statistics query | Usage Statistics |

## 4. Frontend Design

### 4.1 Global Settings Page — Sidebar Layout

`SystemConfigPage` changes from a Tab layout to a sidebar navigation layout:

```
┌──────────┬──────────────────────────────────┐
│  Settings │                                  │
│          │   (Right content area)             │
│ 🤖 Agent  │                                  │
│ 🔌 Providers│                                │
│ 🎬 Image/Video│                              │
│ 📊 Usage Stats│                              │
│          │                                  │
└──────────┴──────────────────────────────────┘
```

- Sidebar icons use `lucide-react`
- Route parameter controls the active section: `/settings?section=providers`

### 4.2 Providers Section — List + Detail Layout

```
┌──────────┬──────────────┬───────────────────┐
│  Settings │ Provider List │ Provider Details     │
│          │              │                   │
│ 🤖 Agent  │ Gemini AS  🟢│ Gemini AI Studio    │
│ 🔌 Providers│ Gemini VX  🔴│ Status: Ready      │
│ 🎬 Image/ │ Seedance   🟢│                    │
│    Video  │ Grok       🔴│ API Key [*****]    │
│ 📊 Usage  │              │ Base URL [     ]   │
│          │              │ [Test Connection]  │
└──────────┴──────────────┴───────────────────┘
```

- Provider logos use `@lobehub/icons`
- Status indicators: 🟢 ready / 🔴 unconfigured / 🟡 error
- Sensitive fields displayed masked, with show/hide toggle support
- Connection test button inline at the bottom of the detail panel
- Advanced configuration section (collapsed): concurrency (image_max_workers, video_max_workers), rate limiting (rpm, request_gap), dynamically displayed based on the provider's supported media_types

### 4.3 Image/Video Section — Grouped Dropdown Selection

Two selectors: default video model, default image model.

Dropdown list grouped by provider (only providers with status=ready):

```
── Gemini AI Studio ──
   veo-3.1-generate-001
   veo-3.1-fast-generate-001
── Gemini Vertex AI ──
   veo-3.1-generate-001
   veo-3.1-fast-generate-001
── Seedance ──
   doubao-seedance-1-5-pro-251215
```

Additional options:
- `video_generate_audio` toggle (labeled "Only supported by some providers")

### 4.4 Usage Statistics Section

- Display usage data grouped by provider
- Filters: time range, provider, call type (video/image)
- Display fields: call count, success rate, cost, duration

### 4.5 Agent Section

Retains existing `AgentConfigTab` content (Anthropic API Key, Base URL), adapted to the new API response structure (read from system_setting).

### 4.6 Common Components

**`ProviderModelSelect`** — Grouped Dropdown Selection Component

- Accepts `options: string[]` (in `provider_id/model_id` format) and `providerDisplayNames: Record<string, string>`
- Splits on `/`, with provider as the group heading and model as the option
- Reused across the global settings page and project settings page

## 5. Project Settings Page

### 5.1 Routing and Interaction

- Route: `/projects/:name/settings`
- Interaction: full-screen overlay; close via back button in the top-left, returning to the project workspace
- Fixes the current routing blank page issue

### 5.2 Content

Supports overriding the global default model selection:

- **Video Model** — Grouped dropdown with an extra "Follow Global Default" option at the top (shows current global value as a hint)
- **Image Model** — Same as above
- **Generate Audio** — Three states: Follow Global / Enable / Disable

Selecting `null` means following the global default.

### 5.3 Data Storage

Project-level overrides are stored in `project.json`:

```json
{
  "video_backend": "seedance/doubao-seedance-1-5-pro-251215",
  "image_backend": null
}
```

`null` or absent field = follow global default.

## 6. Caller Migration

### 6.1 Backend

| Module | Change |
|---|---|
| `lib/system_config.py` (SystemConfigManager) | Deprecated, replaced by `lib/config/service.py` (ConfigService) |
| `server/routers/system_config.py` | Slimmed down; reads/writes go through ConfigService |
| `server/routers/` new `providers.py` | Provider CRUD + connection test |
| `server/services/generation_tasks.py` | `os.environ.get()` → `config_service.get_provider_config()` |
| `lib/media_generator.py` | Accepts provider_id/model parameters; no longer reads env itself |
| `lib/video_backends/*.py` | Constructor parameters unchanged; upper layer fetches from ConfigService and passes in |
| `server/routers/assistant.py` | `os.environ.get("ANTHROPIC_*")` → `config_service.get_setting()` |
| `server/routers/generate.py` | Config reads at enqueue time go through ConfigService |
| `server/auth.py` | Auth-related config goes through ConfigService |
| `server/agent_runtime/session_manager.py` | Agent-related config goes through ConfigService |
| `lib/generation_worker.py` | **Architecture refactor**: Changed from global 2-channel (N workers each for image/video) to per-provider pool scheduling, with independent concurrency and rate limiting per provider. Tasks carry provider_id at enqueue time; Worker routes to the corresponding pool based on provider |
| `lib/usage_tracker.py` / `server/routers/usage.py` | Extend filter parameters |

### 6.2 Frontend

| Component | Change |
|---|---|
| `SystemConfigPage.tsx` | Tab → Sidebar layout |
| `MediaConfigTab.tsx` | Deprecated; split into `ProviderSection.tsx` + `MediaModelSection.tsx` |
| `AgentConfigTab.tsx` | Retained; adapted to new API |
| `AdvancedConfigTab.tsx` | Deprecated; concurrency/rate limiting config moved into provider details |
| `ApiKeysTab.tsx` | Deprecated; merged into provider configuration |
| `config-status-store.ts` | Changed to use `/api/v1/providers` to determine configuration status |
| New `UsageStatsSection.tsx` | Usage statistics section |
| New `ProviderModelSelect.tsx` | Grouped dropdown component |
| Project settings page | Fix routing to full-screen overlay + model override UI |

## 7. Out of Scope

- `ImageBackend` abstraction layer extraction (#101)
- Seedance 2.0 integration (#42)
- Deployment-related configuration in `.env` (e.g., `DATABASE_URL`) continues to be read from environment variables
