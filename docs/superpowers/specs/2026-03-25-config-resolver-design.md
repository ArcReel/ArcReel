# ConfigResolver: Unified Runtime Configuration Resolution

> Date: 2026-03-25
> Status: Design confirmed

## Problem

The `video_generate_audio` configuration travels through 6 files and 4 layers from DB to the Vertex API, and there are **inconsistent default values** bugs:

| Location | Default Value |
|----------|--------------|
| `server/routers/system_config.py` GET | `False` |
| `server/services/generation_tasks.py` `_load_all_config()` | `True` (string `"true"`) |
| `server/services/generation_tasks.py` exception fallback | `True` |
| `lib/media_generator.py` `_resolve_video_generate_audio()` | `True` |
| `lib/gemini_client.py` parameter signature | `True` |
| `lib/system_config.py` (deprecated path) | `True` |

After the user disables audio generation in the global system configuration, audio is still generated because some link in the chain falls back to the `True` default value.

The deeper problem is architectural: configuration values are passed through parameter layers (DB → `_BulkConfig` → `get_media_generator()` → `MediaGenerator.__init__()` → `generate_video()`), with each layer having its own default values, making the chain fragile and hard to maintain.

## Solution

Introduce `ConfigResolver` as a thin wrapper above `ConfigService`, providing:

1. **Single point for default value definitions** — eliminates duplicate defaults scattered across files (reuses ConfigService's existing constants)
2. **Typed outputs** — callers receive `bool`/`tuple[str, str]`/`dict`, no longer handling raw strings
3. **Built-in priority resolution** — global config → project-level override
4. **Read on use** — each call reads from DB, no caching (local SQLite overhead is negligible)

## Design

### New: `lib/config/resolver.py`

```python
from sqlalchemy.ext.asyncio import async_sessionmaker
from lib.config.service import ConfigService, _DEFAULT_VIDEO_BACKEND, _DEFAULT_IMAGE_BACKEND
from lib.project_manager import get_project_manager

class ConfigResolver:
    """Runtime configuration resolver. Reads from DB on each call, no caching."""

    # Single point for default value definitions. Backend defaults reuse ConfigService constants.
    _DEFAULT_VIDEO_GENERATE_AUDIO = False

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def video_generate_audio(self, project_name: str | None = None) -> bool:
        """Resolve video_generate_audio.

        Priority: project-level override > global config > default (False).
        Project-level override is read from project.json (via ProjectManager).
        """
        # 1. Read global config from DB
        async with self._session_factory() as session:
            svc = ConfigService(session)
            raw = await svc.get_setting("video_generate_audio", "")

        if raw:
            value = raw.lower() in ("true", "1", "yes")
        else:
            value = self._DEFAULT_VIDEO_GENERATE_AUDIO

        # 2. If project_name provided, read project-level override
        if project_name:
            project = get_project_manager().load_project(project_name)
            override = project.get("video_generate_audio")
            if override is not None:
                value = bool(override) if not isinstance(override, str) else override.lower() in ("true", "1", "yes")

        return value

    async def default_video_backend(self) -> tuple[str, str]:
        """Returns (provider_id, model_id). Reuses ConfigService resolution logic and defaults."""
        async with self._session_factory() as session:
            svc = ConfigService(session)
            return await svc.get_default_video_backend()

    async def default_image_backend(self) -> tuple[str, str]:
        """Returns (provider_id, model_id). Reuses ConfigService resolution logic and defaults."""
        async with self._session_factory() as session:
            svc = ConfigService(session)
            return await svc.get_default_image_backend()

    async def provider_config(self, provider_id: str) -> dict[str, str]:
        """Get configuration for a single provider."""
        async with self._session_factory() as session:
            svc = ConfigService(session)
            return await svc.get_provider_config(provider_id)

    async def all_provider_configs(self) -> dict[str, dict[str, str]]:
        """Batch-get all provider configurations."""
        async with self._session_factory() as session:
            svc = ConfigService(session)
            return await svc.get_all_provider_configs()
```

### Refactoring: `lib/media_generator.py`

**Remove:**
- `video_generate_audio` parameter in constructor
- `self._video_generate_audio` field
- `_resolve_video_generate_audio()` method

**Add:**
- Constructor accepts `config_resolver: ConfigResolver`
- `generate_video()` / `generate_video_async()` calls `self._config.video_generate_audio(project_name)` to get config value

**Sync `generate_video()` path**: calls async ConfigResolver method via existing `_sync()` helper, consistent with other async call patterns.

**Backend capability limits are handled by the backend itself**: ConfigResolver returns the "user intent", MediaGenerator passes it faithfully to the backend. The backend decides actual behavior based on its own capabilities and writes back the actual value via `VideoGenerationResult.generate_audio`. MediaGenerator uses the backend-written actual value in `finish_call` to ensure usage tracking matches actual API behavior.

Responsibility separation:
- **ConfigResolver**: returns user configuration (project-level override > global config > default)
- **MediaGenerator**: faithfully passes config values to backend, records usage with backend-written actual value
- **VideoBackend**: decides actual `generate_audio` behavior based on its own capabilities and writes back to result

```python
# ConfigResolver returns user configuration
configured_generate_audio = await self._config.video_generate_audio(self.project_name)

# MediaGenerator faithfully passes to backend
request = VideoGenerationRequest(..., generate_audio=configured_generate_audio)
result = await self._video_backend.generate(request)

# Backend writes back actual value for usage tracking
await self.usage_tracker.finish_call(..., generate_audio=result.generate_audio)
```

**GeminiClient path** (non-VideoBackend) still handles aistudio forced `True` logic inside MediaGenerator, because GeminiClient doesn't follow the VideoBackend protocol.

**`version_metadata` call-level override**: only supported in the VideoBackend path, implemented via `version_metadata.get("generate_audio", configured)`. GeminiClient path doesn't support this override (same as before refactoring). Full priority chain:

```
VideoBackend path: version_metadata > project-level override > global config > default (False)
GeminiClient path:                    project-level override > global config > default (False)
                                       ^ ConfigResolver handles internally
```

### Refactoring: `server/services/generation_tasks.py`

**Remove:**
- `_BulkConfig` dataclass
- `_load_all_config()` function
- `video_generate_audio` parameter parsing and project-level override logic in `get_media_generator()`

**Refactor:**
- `_resolve_video_backend()` / `_resolve_image_backend()` changed to accept `ConfigResolver`, signature changed to `async` (because they need `await resolver.default_video_backend()` etc.)
- `_get_or_create_video_backend()` changed to `async`, accepts `ConfigResolver` (needs `await resolver.provider_config()` to replace original `bulk.get_provider_config()`)
- `get_media_generator()` creates `ConfigResolver` instance and passes it to `MediaGenerator`

Simplified `get_media_generator()`:

```python
async def get_media_generator(project_name, ..., user_id=None):
    resolver = ConfigResolver(async_session_factory)

    image_backend_type, image_model, gemini_config_id = await _resolve_image_backend(resolver, ...)
    video_backend, video_backend_type, video_model = await _resolve_video_backend(resolver, ...)
    gemini_config = await resolver.provider_config(gemini_config_id)

    return MediaGenerator(
        project_path,
        config_resolver=resolver,
        video_backend=video_backend,
        image_backend_type=image_backend_type,
        video_backend_type=video_backend_type,
        gemini_api_key=gemini_config.get("api_key"),
        gemini_base_url=gemini_config.get("base_url"),
        gemini_image_model=image_model,
        gemini_video_model=video_model,
        user_id=user_id,
    )
```

### Refactoring: `server/routers/generate.py`

In `generate_video` route lines 213-216, `_load_all_config()` is only used in the `else` branch (when project has no `video_backend` config) to get the global default backend. Replace with:

```python
# Before
else:
    from server.services.generation_tasks import _load_all_config
    bulk = await _load_all_config()
    video_provider, video_model = bulk.default_video_backend

# After
else:
    from lib.config.resolver import ConfigResolver
    from lib.db import async_session_factory
    resolver = ConfigResolver(async_session_factory)
    video_provider, video_model = await resolver.default_video_backend()
```

The conditional branch structure is unchanged, only replacing the data source in the else branch.

### Parts That Remain Unchanged

- **`lib/gemini_client.py`** — continues to accept `generate_audio: bool` parameter; it's a general-purpose client and doesn't depend on the business configuration layer
- **`lib/generation_worker.py`** — has an independent ConfigService call path, not affected
- **`server/routers/system_config.py`** — GET/PATCH endpoints directly use ConfigService to read/write raw values, not affected
- **`server/agent_runtime/session_manager.py`** — independently uses ConfigService, not affected
- **`server/routers/projects.py`** — project-level `video_generate_audio` write endpoint unchanged, still writes to project.json

### Deprecation Cleanup

- **`lib/system_config.py`** — environment variable mapping logic related to `video_generate_audio` (GEMINI_VIDEO_GENERATE_AUDIO) has been superseded by the DB path. After ConfigResolver goes live, audio-related code in this file should be marked as dead code and cleaned up later.

## Impact Scope

| File | Change Type |
|------|------------|
| `lib/config/resolver.py` | **New** |
| `lib/config/__init__.py` | Export ConfigResolver |
| `lib/media_generator.py` | Remove audio parameter/method, add config_resolver; `finish_call` passes backend-written actual value |
| `server/services/generation_tasks.py` | Remove `_BulkConfig`/`_load_all_config()`, use ConfigResolver |
| `server/routers/generate.py` | Remove `_load_all_config()` import, use ConfigResolver |
| `lib/video_backends/base.py` | `VideoGenerationResult` adds `generate_audio` field |
| `lib/video_backends/gemini.py` | `generate()` writes back actual `generate_audio` value |
| `lib/video_backends/seedance.py` | `generate()` writes back actual `generate_audio` value |
| `lib/video_backends/grok.py` | `generate()` writes back actual `generate_audio` value |
| `lib/usage_tracker.py` | `finish_call` adds optional `generate_audio` parameter |
| `lib/db/repositories/usage_repo.py` | `finish_call` supports overriding `generate_audio` with backend actual value |
| Test files | Update MediaGenerator construction method |

## Testing Strategy

1. **ConfigResolver unit tests**
   - Default value: returns `False` when DB has no value
   - Global config reading: correctly parses boolean strings when DB has value (`"true"`, `"false"`, `"TRUE"`, `"0"`, `"1"`, `"yes"`)
   - Project-level override priority: project value overrides global when non-None
   - `project_name=None` skips project-level override
   - Behavior on DB exception (should throw exception, not silently fall back to True)
2. **MediaGenerator integration tests**
   - Verify `generate_video` gets correct audio setting via ConfigResolver
   - Verify aistudio backend still forces `audio=True`
   - Verify `version_metadata` call-level override works correctly
3. **Regression tests** — all existing tests should pass after adapting to new construction method
