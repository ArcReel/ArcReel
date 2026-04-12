# ConfigResolver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce a unified ConfigResolver to replace the cascading parameter passing, and fix the bug where inconsistent `video_generate_audio` default values cause disabling audio to have no effect.

**Architecture:** Add `lib/config/resolver.py` as a thin wrapper above ConfigService, providing typed, priority-aware configuration reads. MediaGenerator is refactored to hold a ConfigResolver reference, reading config on demand in generate_video instead of receiving parameters at construction time. Also removes `_BulkConfig` / `_load_all_config()` from generation_tasks.py.

**Tech Stack:** Python 3.12, SQLAlchemy async, pytest, asyncio

**Spec:** `docs/superpowers/specs/2026-03-25-config-resolver-design.md`

---

### Task 1: Create ConfigResolver class and unit tests

**Files:**
- Create: `lib/config/resolver.py`
- Modify: `lib/config/__init__.py`
- Create: `tests/test_config_resolver.py`

- [ ] **Step 1: Write failing tests for ConfigResolver**

```python
# tests/test_config_resolver.py
import pytest
from unittest.mock import AsyncMock, patch

from lib.config.resolver import ConfigResolver


class _FakeConfigService:
    """Minimal ConfigService fake, only implementing the methods required by the resolver."""

    def __init__(self, settings: dict[str, str] | None = None):
        self._settings = settings or {}

    async def get_setting(self, key: str, default: str = "") -> str:
        return self._settings.get(key, default)

    async def get_default_video_backend(self) -> tuple[str, str]:
        return ("gemini-aistudio", "veo-3.1-fast-generate-preview")

    async def get_default_image_backend(self) -> tuple[str, str]:
        return ("gemini-aistudio", "gemini-3.1-flash-image-preview")

    async def get_provider_config(self, provider: str) -> dict[str, str]:
        return {"api_key": f"key-{provider}"}

    async def get_all_provider_configs(self) -> dict[str, dict[str, str]]:
        return {"gemini-aistudio": {"api_key": "key-aistudio"}}


class TestVideoGenerateAudio:
    """Verify video_generate_audio default value, global config, and project-level override priority."""

    async def test_default_is_false_when_db_empty(self, tmp_path):
        """Should return False (not True) when DB has no value."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        result = await resolver._resolve_video_generate_audio(fake_svc, project_name=None)
        assert result is False

    async def test_global_true(self, tmp_path):
        """Returns True when DB value is "true"."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"video_generate_audio": "true"})
        result = await resolver._resolve_video_generate_audio(fake_svc, project_name=None)
        assert result is True

    async def test_global_false(self, tmp_path):
        """Returns False when DB value is "false"."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"video_generate_audio": "false"})
        result = await resolver._resolve_video_generate_audio(fake_svc, project_name=None)
        assert result is False

    async def test_bool_parsing_variants(self, tmp_path):
        """Verify parsing of various boolean string values."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        for val, expected in [("TRUE", True), ("1", True), ("yes", True), ("0", False), ("no", False), ("", False)]:
            fake_svc = _FakeConfigService(settings={"video_generate_audio": val} if val else {})
            result = await resolver._resolve_video_generate_audio(fake_svc, project_name=None)
            assert result is expected, f"Failed for {val!r}: got {result}"

    async def test_project_override_true_over_global_false(self, tmp_path):
        """Project-level override True takes priority over global False."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"video_generate_audio": "false"})
        with patch("lib.config.resolver.get_project_manager") as mock_pm:
            mock_pm.return_value.load_project.return_value = {"video_generate_audio": True}
            result = await resolver._resolve_video_generate_audio(fake_svc, project_name="demo")
        assert result is True

    async def test_project_override_false_over_global_true(self, tmp_path):
        """Project-level override False takes priority over global True."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"video_generate_audio": "true"})
        with patch("lib.config.resolver.get_project_manager") as mock_pm:
            mock_pm.return_value.load_project.return_value = {"video_generate_audio": False}
            result = await resolver._resolve_video_generate_audio(fake_svc, project_name="demo")
        assert result is False

    async def test_project_none_skips_override(self, tmp_path):
        """Does not read project config when project_name=None."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"video_generate_audio": "true"})
        result = await resolver._resolve_video_generate_audio(fake_svc, project_name=None)
        assert result is True

    async def test_project_override_string_value(self, tmp_path):
        """Correctly parses project-level override when value is a string."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"video_generate_audio": "true"})
        with patch("lib.config.resolver.get_project_manager") as mock_pm:
            mock_pm.return_value.load_project.return_value = {"video_generate_audio": "false"}
            result = await resolver._resolve_video_generate_audio(fake_svc, project_name="demo")
        assert result is False


class TestDefaultBackends:
    """Verify that backend config methods delegate to ConfigService."""

    async def test_default_video_backend(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService()
        result = await resolver._resolve_default_video_backend(fake_svc)
        assert result == ("gemini-aistudio", "veo-3.1-fast-generate-preview")

    async def test_default_image_backend(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService()
        result = await resolver._resolve_default_image_backend(fake_svc)
        assert result == ("gemini-aistudio", "gemini-3.1-flash-image-preview")


class TestProviderConfig:
    """Verify that provider config methods delegate to ConfigService."""

    async def test_provider_config(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService()
        result = await resolver._resolve_provider_config(fake_svc, "gemini-aistudio")
        assert result == {"api_key": "key-gemini-aistudio"}

    async def test_all_provider_configs(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService()
        result = await resolver._resolve_all_provider_configs(fake_svc)
        assert "gemini-aistudio" in result
```

- [ ] **Step 2: Run tests, confirm all fail**

Run: `uv run python -m pytest tests/test_config_resolver.py -v`
Expected: ImportError or AttributeError (ConfigResolver does not exist yet)

- [ ] **Step 3: Implement ConfigResolver**

```python
# lib/config/resolver.py
"""Unified runtime configuration resolver.

Centralizes configuration reads and default value definitions scattered across multiple files.
Each call reads from DB without caching (local SQLite overhead is negligible).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

from lib.config.service import ConfigService

logger = logging.getLogger(__name__)

# Set of truthy values for boolean string parsing
_TRUTHY = frozenset({"true", "1", "yes"})


def _parse_bool(raw: str) -> bool:
    """Parse a configuration string into a boolean value."""
    return raw.strip().lower() in _TRUTHY


class ConfigResolver:
    """Runtime configuration resolver.

    A thin wrapper above ConfigService providing:
    - Single point for default value definitions
    - Typed outputs (bool / tuple / dict)
    - Built-in priority resolution (global config → project-level override)
    """

    # ── Single point for default value definitions ──
    _DEFAULT_VIDEO_GENERATE_AUDIO = False

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    # ── Public API: opens a new session on each call ──

    async def video_generate_audio(self, project_name: str | None = None) -> bool:
        """Resolve video_generate_audio.

        Priority: project-level override > global config > default value (False).
        """
        async with self._session_factory() as session:
            svc = ConfigService(session)
            return await self._resolve_video_generate_audio(svc, project_name)

    async def default_video_backend(self) -> tuple[str, str]:
        """Returns (provider_id, model_id)."""
        async with self._session_factory() as session:
            svc = ConfigService(session)
            return await self._resolve_default_video_backend(svc)

    async def default_image_backend(self) -> tuple[str, str]:
        """Returns (provider_id, model_id)."""
        async with self._session_factory() as session:
            svc = ConfigService(session)
            return await self._resolve_default_image_backend(svc)

    async def provider_config(self, provider_id: str) -> dict[str, str]:
        """Get configuration for a single provider."""
        async with self._session_factory() as session:
            svc = ConfigService(session)
            return await self._resolve_provider_config(svc, provider_id)

    async def all_provider_configs(self) -> dict[str, dict[str, str]]:
        """Fetch all provider configurations in bulk."""
        async with self._session_factory() as session:
            svc = ConfigService(session)
            return await self._resolve_all_provider_configs(svc)

    # ── Internal resolution methods (independently testable, receive a pre-created svc) ──

    async def _resolve_video_generate_audio(
        self, svc: ConfigService, project_name: str | None,
    ) -> bool:
        raw = await svc.get_setting("video_generate_audio", "")
        value = _parse_bool(raw) if raw else self._DEFAULT_VIDEO_GENERATE_AUDIO

        if project_name:
            from lib.project_manager import get_project_manager
            project = get_project_manager().load_project(project_name)
            override = project.get("video_generate_audio")
            if override is not None:
                if isinstance(override, str):
                    value = _parse_bool(override)
                else:
                    value = bool(override)

        return value

    async def _resolve_default_video_backend(self, svc: ConfigService) -> tuple[str, str]:
        return await svc.get_default_video_backend()

    async def _resolve_default_image_backend(self, svc: ConfigService) -> tuple[str, str]:
        return await svc.get_default_image_backend()

    async def _resolve_provider_config(self, svc: ConfigService, provider_id: str) -> dict[str, str]:
        return await svc.get_provider_config(provider_id)

    async def _resolve_all_provider_configs(self, svc: ConfigService) -> dict[str, dict[str, str]]:
        return await svc.get_all_provider_configs()
```

- [ ] **Step 4: Update `lib/config/__init__.py` exports**

```python
# lib/config/__init__.py
"""Configuration management package."""

from lib.config.resolver import ConfigResolver

__all__ = ["ConfigResolver"]
```

- [ ] **Step 5: Run tests, confirm all pass**

Run: `uv run python -m pytest tests/test_config_resolver.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add lib/config/resolver.py lib/config/__init__.py tests/test_config_resolver.py
git commit -m "feat: add ConfigResolver with unified defaults and priority resolution"
```

---

### Task 2: Refactor MediaGenerator to use ConfigResolver

**Files:**
- Modify: `lib/media_generator.py:43-97` (constructor)
- Modify: `lib/media_generator.py:136-143` (remove `_resolve_video_generate_audio`)
- Modify: `lib/media_generator.py:406-418` (sync `generate_video`)
- Modify: `lib/media_generator.py:554-566` (async `generate_video_async`)
- Modify: `tests/test_media_generator_module.py:63-83` (`_build_generator` helper)

- [ ] **Step 1: Update `_build_generator` test helper to inject FakeConfigResolver**

Add fake resolver and update `_build_generator` in `tests/test_media_generator_module.py`:

```python
# Add after imports at the top of the file
class _FakeConfigResolver:
    """Fake ConfigResolver that returns controllable config values."""
    def __init__(self, video_generate_audio: bool = False):
        self._video_generate_audio = video_generate_audio

    async def video_generate_audio(self, project_name=None):
        return self._video_generate_audio
```

In the `_build_generator` function:
- Remove `gen._video_generate_audio = None`
- Add `gen._config = _FakeConfigResolver()`

- [ ] **Step 2: Run existing tests to confirm they still pass**

Run: `uv run python -m pytest tests/test_media_generator_module.py -v`
Expected: PASS (since `_build_generator` uses `object.__new__` to set attributes manually, the attribute name change must be reflected accordingly)

- [ ] **Step 3: Refactor MediaGenerator constructor**

In `lib/media_generator.py`:

1. Add import:
```python
from lib.config.resolver import ConfigResolver
```

2. Constructor signature: replace `video_generate_audio: Optional[bool] = None` with `config_resolver: Optional[ConfigResolver] = None`

3. Constructor body: replace `self._video_generate_audio = video_generate_audio` with `self._config = config_resolver`

4. Remove the `_resolve_video_generate_audio()` method (lines 136-143)

- [ ] **Step 4: Refactor audio resolution logic in sync `generate_video()`**

> **Note**: Here the async ConfigResolver is called via `_sync()`, reusing the existing cross-thread async call pattern used by `usage_tracker.start_call()` etc.

In `lib/media_generator.py` lines 406-418, replace:

```python
if self._video_backend:
    ...
    configured_generate_audio = self._resolve_video_generate_audio()
    effective_generate_audio = version_metadata.get("generate_audio", configured_generate_audio)
else:
    ...
    configured_generate_audio = self._resolve_video_generate_audio()
    effective_generate_audio = (
        configured_generate_audio if self._gemini_video_backend_type == "vertex" else True
    )
```

with:

```python
if self._video_backend:
    ...
    configured_generate_audio = self._sync(
        self._config.video_generate_audio(self.project_name)
    ) if self._config else False
    effective_generate_audio = version_metadata.get("generate_audio", configured_generate_audio)
else:
    ...
    configured_generate_audio = self._sync(
        self._config.video_generate_audio(self.project_name)
    ) if self._config else False
    effective_generate_audio = (
        configured_generate_audio if self._gemini_video_backend_type == "vertex" else True
    )
```

- [ ] **Step 5: Refactor audio resolution logic in async `generate_video_async()`**

In `lib/media_generator.py` lines 554-566, same pattern:

```python
if self._video_backend:
    ...
    configured_generate_audio = await self._config.video_generate_audio(self.project_name) if self._config else False
    effective_generate_audio = version_metadata.get("generate_audio", configured_generate_audio)
else:
    ...
    configured_generate_audio = await self._config.video_generate_audio(self.project_name) if self._config else False
    effective_generate_audio = (
        configured_generate_audio if self._gemini_video_backend_type == "vertex" else True
    )
```

- [ ] **Step 6: Run tests to confirm they pass**

Run: `uv run python -m pytest tests/test_media_generator_module.py -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add lib/media_generator.py tests/test_media_generator_module.py
git commit -m "refactor: replace video_generate_audio param with ConfigResolver in MediaGenerator"
```

---

### Task 3: Refactor generation_tasks.py to remove _BulkConfig

**Files:**
- Modify: `server/services/generation_tasks.py:68-248` (remove `_BulkConfig`/`_load_all_config()`, refactor helper functions)
- Modify: `tests/test_generation_tasks_service.py` (adapt to new interface)

- [ ] **Step 1: Run existing tests to confirm baseline**

Run: `uv run python -m pytest tests/test_generation_tasks_service.py -v`
Expected: all PASS

- [ ] **Step 2: Refactor `_get_or_create_video_backend` to async, accepting ConfigResolver**

In `server/services/generation_tasks.py` lines 110-160, change from:

```python
def _get_or_create_video_backend(
    provider_name: str,
    provider_settings: dict,
    bulk: _BulkConfig,
    *,
    default_video_model: Optional[str] = None,
):
```

to:

```python
async def _get_or_create_video_backend(
    provider_name: str,
    provider_settings: dict,
    resolver: "ConfigResolver",
    *,
    default_video_model: Optional[str] = None,
):
```

Internally replace `bulk.get_provider_config(config_provider_id)` with `await resolver.provider_config(config_provider_id)`. Do the same for seedance and grok config fetching.

- [ ] **Step 3: Refactor `_resolve_image_backend` to async, accepting ConfigResolver**

Change lines 163-176 from:

```python
def _resolve_image_backend(
    bulk: _BulkConfig, payload: dict | None,
) -> tuple[str, str, str]:
    image_provider_id, image_model = bulk.default_image_backend
```

to:

```python
async def _resolve_image_backend(
    resolver: "ConfigResolver", payload: dict | None,
) -> tuple[str, str, str]:
    image_provider_id, image_model = await resolver.default_image_backend()
```

The remaining logic stays the same.

- [ ] **Step 4: Refactor `_resolve_video_backend` to async, accepting ConfigResolver**

Change lines 179-211 from:

```python
def _resolve_video_backend(
    project_name: str, bulk: _BulkConfig, payload: dict | None,
) -> tuple[Any | None, str, str]:
    default_video_provider_id, video_model = bulk.default_video_backend
```

to:

```python
async def _resolve_video_backend(
    project_name: str, resolver: "ConfigResolver", payload: dict | None,
) -> tuple[Any | None, str, str]:
    default_video_provider_id, video_model = await resolver.default_video_backend()
```

Change the internal `_get_or_create_video_backend(provider_name, provider_settings, bulk, ...)` call to `await _get_or_create_video_backend(provider_name, provider_settings, resolver, ...)`.

- [ ] **Step 5: Refactor `get_media_generator` to use ConfigResolver**

Change lines 214-248 to:

```python
async def get_media_generator(project_name: str, payload: dict | None = None, *, user_id: str = DEFAULT_USER_ID) -> MediaGenerator:
    """Create a MediaGenerator. Video backend is only initialized when payload contains video config."""
    from lib.config.resolver import ConfigResolver
    from lib.db import async_session_factory

    project_path = get_project_manager().get_project_path(project_name)
    resolver = ConfigResolver(async_session_factory)

    image_backend_type, gemini_config_id, image_model = await _resolve_image_backend(resolver, payload)
    gemini_config = await resolver.provider_config(gemini_config_id)
    video_backend, video_backend_type, video_model = await _resolve_video_backend(project_name, resolver, payload)

    return MediaGenerator(
        project_path,
        rate_limiter=rate_limiter,
        video_backend=video_backend,
        config_resolver=resolver,
        image_backend_type=image_backend_type,
        video_backend_type=video_backend_type,
        gemini_api_key=gemini_config.get("api_key"),
        gemini_base_url=gemini_config.get("base_url"),
        gemini_image_model=image_model or None,
        gemini_video_model=video_model or None,
        user_id=user_id,
    )
```

- [ ] **Step 6: Refactor `_load_all_config` call in `execute_video_task`**

In `execute_video_task()` lines 574-577, replace:

```python
bulk = await _load_all_config()
default_provider_id, _ = bulk.default_video_backend
```

with:

```python
from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
resolver = ConfigResolver(async_session_factory)
default_provider_id, _ = await resolver.default_video_backend()
```

- [ ] **Step 7: Run tests to confirm they pass**

Run: `uv run python -m pytest tests/test_generation_tasks_service.py -v`
Expected: all PASS (tests use monkeypatch to replace `get_media_generator`, not depending on internal implementation details)

- [ ] **Step 8: Commit**

```bash
git add server/services/generation_tasks.py tests/test_generation_tasks_service.py
git commit -m "refactor: replace _BulkConfig with ConfigResolver in generation_tasks"
```

---

### Task 4: Refactor generate.py route and delete _BulkConfig

> **Important**: Must replace all `_load_all_config` references in `generate.py` before deleting `_BulkConfig` / `_load_all_config()`, otherwise the intermediate state will break the code.

**Files:**
- Modify: `server/routers/generate.py:213-216`
- Modify: `server/services/generation_tasks.py:68-108` (delete `_BulkConfig` / `_load_all_config()`)

- [ ] **Step 1: Replace `_load_all_config()` calls in `generate.py`**

In the `else` branch at `server/routers/generate.py` lines 213-216, replace:

```python
else:
    from server.services.generation_tasks import _load_all_config
    bulk = await _load_all_config()
    video_provider, video_model = bulk.default_video_backend
```

with:

```python
else:
    from lib.config.resolver import ConfigResolver
    from lib.db import async_session_factory
    resolver = ConfigResolver(async_session_factory)
    video_provider, video_model = await resolver.default_video_backend()
```

- [ ] **Step 2: Delete `_BulkConfig` and `_load_all_config()`**

Remove the `_BulkConfig` dataclass and `_load_all_config()` function from `server/services/generation_tasks.py` lines 68-108.

- [ ] **Step 3: Run full test suite to confirm no regressions**

Run: `uv run python -m pytest -v`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add server/routers/generate.py server/services/generation_tasks.py
git commit -m "refactor: use ConfigResolver in generate.py route, remove _BulkConfig"
```

---

### Task 5: Add integration tests

**Files:**
- Modify: `tests/test_media_generator_module.py`

- [ ] **Step 1: Add audio config integration tests**

Add to the `TestMediaGenerator` class in `tests/test_media_generator_module.py`:

```python
@pytest.mark.asyncio
async def test_video_generate_audio_from_config_resolver(self, tmp_path):
    """Verify that generate_video_async fetches audio setting via ConfigResolver."""
    gen = _build_generator(tmp_path)
    gen._config = _FakeConfigResolver(video_generate_audio=False)

    await gen.generate_video_async(
        prompt="p", resource_type="videos", resource_id="E1S03",
    )
    # aistudio backend forces audio=True even when config returns False
    assert gen.usage_tracker.started[-1]["generate_audio"] is True

@pytest.mark.asyncio
async def test_video_generate_audio_vertex_respects_config(self, tmp_path):
    """Verify that vertex backend respects False returned by ConfigResolver."""
    gen = _build_generator(tmp_path)
    gen._gemini_video_backend_type = "vertex"
    gen._config = _FakeConfigResolver(video_generate_audio=False)

    await gen.generate_video_async(
        prompt="p", resource_type="videos", resource_id="E1S04",
    )
    assert gen.usage_tracker.started[-1]["generate_audio"] is False
```

- [ ] **Step 2: Run tests to confirm they pass**

Run: `uv run python -m pytest tests/test_media_generator_module.py -v`
Expected: all PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_media_generator_module.py
git commit -m "test: add ConfigResolver integration tests for MediaGenerator"
```

---

### Task 6: Full regression tests and cleanup

**Files:**
- All modified files

- [ ] **Step 1: Run the full test suite**

Run: `uv run python -m pytest -v`
Expected: all PASS

- [ ] **Step 2: Verify no remaining references to `_load_all_config`**

Run: `grep -r "_load_all_config\|_BulkConfig" lib/ server/ tests/ --include="*.py"`
Expected: no matches (all removed)

- [ ] **Step 3: Verify no remaining references to `_resolve_video_generate_audio`**

Run: `grep -r "_resolve_video_generate_audio\|_video_generate_audio" lib/ server/ tests/ --include="*.py"`
Expected: no matches (all removed)

- [ ] **Step 4: Commit (if any cleanup needed)**

```bash
git add -A
git commit -m "chore: remove stale references to _BulkConfig and _video_generate_audio"
```

> **Behavior change note**: ConfigResolver does not silently fall back to `True` on DB exceptions like the old `_load_all_config()` did. DB exceptions now propagate — this is an intentional decision in the design spec — to avoid silently enabling audio generation when config reads fail.
