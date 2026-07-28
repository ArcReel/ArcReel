# ConfigResolver 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 引入统一的 ConfigResolver 替代层层透传的配置参数，修复 video_generate_audio 默认值不一致导致关闭音频无效的 bug。

**Architecture:** 新增 `lib/config/resolver.py` 作为 ConfigService 上层封装，提供类型化、带优先级解析的配置读取。MediaGenerator 改为持有 ConfigResolver 引用，在 generate_video 时按需读取配置，而非构造时接收参数。同时移除 generation_tasks.py 中的 `_BulkConfig` / `_load_all_config()`。

**Tech Stack:** Python 3.12, SQLAlchemy async, pytest, asyncio

**Spec:** `docs/superpowers/specs/2026-03-25-config-resolver-design.md`

---

### Task 1: 创建 ConfigResolver 类及单元测试

**Files:**
- Create: `lib/config/resolver.py`
- Modify: `lib/config/__init__.py`
- Create: `tests/test_config_resolver.py`

- [ ] **Step 1: 编写 ConfigResolver 失败测试**

```python
# tests/test_config_resolver.py
import pytest
from unittest.mock import AsyncMock, patch

from lib.config.resolver import ConfigResolver


class _FakeConfigService:
    """最小化的 ConfigService fake，只实现 resolver 需要的方法。"""

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
    """验证 video_generate_audio 的默认值、全局配置、项目级覆盖优先级。"""

    async def test_default_is_false_when_db_empty(self, tmp_path):
        """DB 无值时应返回 False（不是 True）。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        result = await resolver._resolve_video_generate_audio(fake_svc, project_name=None)
        assert result is False

    async def test_global_true(self, tmp_path):
        """DB 中值为 "true" 时返回 True。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"video_generate_audio": "true"})
        result = await resolver._resolve_video_generate_audio(fake_svc, project_name=None)
        assert result is True

    async def test_global_false(self, tmp_path):
        """DB 中值为 "false" 时返回 False。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"video_generate_audio": "false"})
        result = await resolver._resolve_video_generate_audio(fake_svc, project_name=None)
        assert result is False

    async def test_bool_parsing_variants(self, tmp_path):
        """验证各种布尔字符串的解析。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        for val, expected in [("TRUE", True), ("1", True), ("yes", True), ("0", False), ("no", False), ("", False)]:
            fake_svc = _FakeConfigService(settings={"video_generate_audio": val} if val else {})
            result = await resolver._resolve_video_generate_audio(fake_svc, project_name=None)
            assert result is expected, f"Failed for {val!r}: got {result}"

    async def test_project_override_true_over_global_false(self, tmp_path):
        """项目级覆盖 True 优先于全局 False。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"video_generate_audio": "false"})
        with patch("lib.config.resolver.get_project_manager") as mock_pm:
            mock_pm.return_value.load_project.return_value = {"video_generate_audio": True}
            result = await resolver._resolve_video_generate_audio(fake_svc, project_name="demo")
        assert result is True

    async def test_project_override_false_over_global_true(self, tmp_path):
        """项目级覆盖 False 优先于全局 True。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"video_generate_audio": "true"})
        with patch("lib.config.resolver.get_project_manager") as mock_pm:
            mock_pm.return_value.load_project.return_value = {"video_generate_audio": False}
            result = await resolver._resolve_video_generate_audio(fake_svc, project_name="demo")
        assert result is False

    async def test_project_none_skips_override(self, tmp_path):
        """project_name=None 时不读取项目配置。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"video_generate_audio": "true"})
        result = await resolver._resolve_video_generate_audio(fake_svc, project_name=None)
        assert result is True

    async def test_project_override_string_value(self, tmp_path):
        """项目级覆盖值为字符串时也能正确解析。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"video_generate_audio": "true"})
        with patch("lib.config.resolver.get_project_manager") as mock_pm:
            mock_pm.return_value.load_project.return_value = {"video_generate_audio": "false"}
            result = await resolver._resolve_video_generate_audio(fake_svc, project_name="demo")
        assert result is False


class TestDefaultBackends:
    """验证后端配置方法委托给 ConfigService。"""

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
    """验证供应商配置方法委托给 ConfigService。"""

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

- [ ] **Step 2: 运行测试，确认全部失败**

Run: `uv run python -m pytest tests/test_config_resolver.py -v`
Expected: ImportError 或 AttributeError（ConfigResolver 还不存在）

- [ ] **Step 3: 实现 ConfigResolver**

```python
# lib/config/resolver.py
"""统一运行时配置解析器。

将散落在多个文件中的配置读取和默认值定义集中到一处。
每次调用从 DB 读取，不缓存（本地 SQLite 开销可忽略）。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

from lib.config.service import ConfigService

logger = logging.getLogger(__name__)

# 布尔字符串解析的 truthy 值集合
_TRUTHY = frozenset({"true", "1", "yes"})


def _parse_bool(raw: str) -> bool:
    """将配置字符串解析为布尔值。"""
    return raw.strip().lower() in _TRUTHY


class ConfigResolver:
    """运行时配置解析器。

    作为 ConfigService 的上层薄封装，提供：
    - 唯一的默认值定义点
    - 类型化输出（bool / tuple / dict）
    - 内置优先级解析（全局配置 → 项目级覆盖）
    """

    # ── 唯一的默认值定义点 ──
    _DEFAULT_VIDEO_GENERATE_AUDIO = False

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    # ── 公开 API：每次调用打开新 session ──

    async def video_generate_audio(self, project_name: str | None = None) -> bool:
        """解析 video_generate_audio。

        优先级：项目级覆盖 > 全局配置 > 默认值(False)。
        """
        async with self._session_factory() as session:
            svc = ConfigService(session)
            return await self._resolve_video_generate_audio(svc, project_name)

    async def default_video_backend(self) -> tuple[str, str]:
        """返回 (provider_id, model_id)。"""
        async with self._session_factory() as session:
            svc = ConfigService(session)
            return await self._resolve_default_video_backend(svc)

    async def default_image_backend(self) -> tuple[str, str]:
        """返回 (provider_id, model_id)。"""
        async with self._session_factory() as session:
            svc = ConfigService(session)
            return await self._resolve_default_image_backend(svc)

    async def provider_config(self, provider_id: str) -> dict[str, str]:
        """获取单个供应商配置。"""
        async with self._session_factory() as session:
            svc = ConfigService(session)
            return await self._resolve_provider_config(svc, provider_id)

    async def all_provider_configs(self) -> dict[str, dict[str, str]]:
        """批量获取所有供应商配置。"""
        async with self._session_factory() as session:
            svc = ConfigService(session)
            return await self._resolve_all_provider_configs(svc)

    # ── 内部解析方法（可独立测试，接收已创建的 svc） ──

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

- [ ] **Step 4: 更新 `lib/config/__init__.py` 导出**

```python
# lib/config/__init__.py
"""Configuration management package."""

from lib.config.resolver import ConfigResolver

__all__ = ["ConfigResolver"]
```

- [ ] **Step 5: 运行测试，确认全部通过**

Run: `uv run python -m pytest tests/test_config_resolver.py -v`
Expected: 全部 PASS

- [ ] **Step 6: 提交**

```bash
git add lib/config/resolver.py lib/config/__init__.py tests/test_config_resolver.py
git commit -m "feat: add ConfigResolver with unified defaults and priority resolution"
```

---

### Task 2: 改造 MediaGenerator 使用 ConfigResolver

**Files:**
- Modify: `lib/media_generator.py:43-97` (构造函数)
- Modify: `lib/media_generator.py:136-143` (删除 `_resolve_video_generate_audio`)
- Modify: `lib/media_generator.py:406-418` (同步 `generate_video`)
- Modify: `lib/media_generator.py:554-566` (异步 `generate_video_async`)
- Modify: `tests/test_media_generator_module.py:63-83` (`_build_generator` helper)

- [ ] **Step 1: 更新 `_build_generator` 测试 helper，注入 FakeConfigResolver**

在 `tests/test_media_generator_module.py` 中添加 fake resolver 并更新 `_build_generator`：

```python
# 在文件顶部 import 之后添加
class _FakeConfigResolver:
    """Fake ConfigResolver，返回可控的配置值。"""
    def __init__(self, video_generate_audio: bool = False):
        self._video_generate_audio = video_generate_audio

    async def video_generate_audio(self, project_name=None):
        return self._video_generate_audio
```

在 `_build_generator` 函数中：
- 移除 `gen._video_generate_audio = None`
- 添加 `gen._config = _FakeConfigResolver()`

- [ ] **Step 2: 运行现有测试确认仍通过**

Run: `uv run python -m pytest tests/test_media_generator_module.py -v`
Expected: PASS（因为 `_build_generator` 用 `object.__new__` 手动设置属性，改属性名后需要对应修改）

- [ ] **Step 3: 改造 MediaGenerator 构造函数**

在 `lib/media_generator.py` 中：

1. 添加 import：
```python
from lib.config.resolver import ConfigResolver
```

2. 构造函数签名：将 `video_generate_audio: Optional[bool] = None` 替换为 `config_resolver: Optional[ConfigResolver] = None`

3. 构造函数体：将 `self._video_generate_audio = video_generate_audio` 替换为 `self._config = config_resolver`

4. 删除 `_resolve_video_generate_audio()` 方法（第 136-143 行）

- [ ] **Step 4: 改造同步 `generate_video()` 中的 audio 解析逻辑**

> **注意**：此处通过 `_sync()` 调用 async 的 ConfigResolver，复用了 `usage_tracker.start_call()` 等已有的跨线程 async 调用模式。

在 `lib/media_generator.py` 第 406-418 行，将：

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

替换为：

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

- [ ] **Step 5: 改造异步 `generate_video_async()` 中的 audio 解析逻辑**

在 `lib/media_generator.py` 第 554-566 行，同样的模式：

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

- [ ] **Step 6: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_media_generator_module.py -v`
Expected: 全部 PASS

- [ ] **Step 7: 提交**

```bash
git add lib/media_generator.py tests/test_media_generator_module.py
git commit -m "refactor: replace video_generate_audio param with ConfigResolver in MediaGenerator"
```

---

### Task 3: 改造 generation_tasks.py 移除 _BulkConfig

**Files:**
- Modify: `server/services/generation_tasks.py:68-248` (移除 `_BulkConfig`/`_load_all_config()`，改造辅助函数)
- Modify: `tests/test_generation_tasks_service.py` (适配新接口)

- [ ] **Step 1: 运行现有测试确认基线**

Run: `uv run python -m pytest tests/test_generation_tasks_service.py -v`
Expected: 全部 PASS

- [ ] **Step 2: 改造 `_get_or_create_video_backend` 为 async，接收 ConfigResolver**

将 `server/services/generation_tasks.py` 第 110-160 行从：

```python
def _get_or_create_video_backend(
    provider_name: str,
    provider_settings: dict,
    bulk: _BulkConfig,
    *,
    default_video_model: Optional[str] = None,
):
```

改为：

```python
async def _get_or_create_video_backend(
    provider_name: str,
    provider_settings: dict,
    resolver: "ConfigResolver",
    *,
    default_video_model: Optional[str] = None,
):
```

内部将 `bulk.get_provider_config(config_provider_id)` 替换为 `await resolver.provider_config(config_provider_id)`。同样替换 seedance 和 grok 的配置获取。

- [ ] **Step 3: 改造 `_resolve_image_backend` 为 async，接收 ConfigResolver**

将第 163-176 行从：

```python
def _resolve_image_backend(
    bulk: _BulkConfig, payload: dict | None,
) -> tuple[str, str, str]:
    image_provider_id, image_model = bulk.default_image_backend
```

改为：

```python
async def _resolve_image_backend(
    resolver: "ConfigResolver", payload: dict | None,
) -> tuple[str, str, str]:
    image_provider_id, image_model = await resolver.default_image_backend()
```

其余逻辑不变。

- [ ] **Step 4: 改造 `_resolve_video_backend` 为 async，接收 ConfigResolver**

将第 179-211 行从：

```python
def _resolve_video_backend(
    project_name: str, bulk: _BulkConfig, payload: dict | None,
) -> tuple[Any | None, str, str]:
    default_video_provider_id, video_model = bulk.default_video_backend
```

改为：

```python
async def _resolve_video_backend(
    project_name: str, resolver: "ConfigResolver", payload: dict | None,
) -> tuple[Any | None, str, str]:
    default_video_provider_id, video_model = await resolver.default_video_backend()
```

内部的 `_get_or_create_video_backend(provider_name, provider_settings, bulk, ...)` 调用改为 `await _get_or_create_video_backend(provider_name, provider_settings, resolver, ...)`。

- [ ] **Step 5: 改造 `get_media_generator` 使用 ConfigResolver**

将第 214-248 行改为：

```python
async def get_media_generator(project_name: str, payload: dict | None = None, *, user_id: str = DEFAULT_USER_ID) -> MediaGenerator:
    """创建 MediaGenerator。仅当 payload 包含视频配置时才初始化视频后端。"""
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

- [ ] **Step 6: 改造 `execute_video_task` 中的 `_load_all_config` 调用**

在 `execute_video_task()` 第 574-577 行，将：

```python
bulk = await _load_all_config()
default_provider_id, _ = bulk.default_video_backend
```

替换为：

```python
from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
resolver = ConfigResolver(async_session_factory)
default_provider_id, _ = await resolver.default_video_backend()
```

- [ ] **Step 7: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_generation_tasks_service.py -v`
Expected: 全部 PASS（测试用 monkeypatch 替换了 `get_media_generator`，不依赖内部实现细节）

- [ ] **Step 8: 提交**

```bash
git add server/services/generation_tasks.py tests/test_generation_tasks_service.py
git commit -m "refactor: replace _BulkConfig with ConfigResolver in generation_tasks"
```

---

### Task 4: 改造 generate.py 路由并删除 _BulkConfig

> **重要**：必须先替换 `generate.py` 中对 `_load_all_config` 的引用，然后再删除 `_BulkConfig` / `_load_all_config()`，否则会导致中间状态代码 broken。

**Files:**
- Modify: `server/routers/generate.py:213-216`
- Modify: `server/services/generation_tasks.py:68-108` (删除 `_BulkConfig` / `_load_all_config()`)

- [ ] **Step 1: 替换 `generate.py` 中的 `_load_all_config()` 调用**

在 `server/routers/generate.py` 第 213-216 行的 `else` 分支中，将：

```python
else:
    from server.services.generation_tasks import _load_all_config
    bulk = await _load_all_config()
    video_provider, video_model = bulk.default_video_backend
```

替换为：

```python
else:
    from lib.config.resolver import ConfigResolver
    from lib.db import async_session_factory
    resolver = ConfigResolver(async_session_factory)
    video_provider, video_model = await resolver.default_video_backend()
```

- [ ] **Step 2: 删除 `_BulkConfig` 和 `_load_all_config()`**

移除 `server/services/generation_tasks.py` 第 68-108 行的 `_BulkConfig` 数据类和 `_load_all_config()` 函数。

- [ ] **Step 3: 运行全量测试确认无回归**

Run: `uv run python -m pytest -v`
Expected: 全部 PASS

- [ ] **Step 4: 提交**

```bash
git add server/routers/generate.py server/services/generation_tasks.py
git commit -m "refactor: use ConfigResolver in generate.py route, remove _BulkConfig"
```

---

### Task 5: 补充集成测试

**Files:**
- Modify: `tests/test_media_generator_module.py`

- [ ] **Step 1: 添加 audio 配置集成测试**

在 `tests/test_media_generator_module.py` 的 `TestMediaGenerator` 类中添加：

```python
@pytest.mark.asyncio
async def test_video_generate_audio_from_config_resolver(self, tmp_path):
    """验证 generate_video_async 通过 ConfigResolver 获取 audio 设置。"""
    gen = _build_generator(tmp_path)
    gen._config = _FakeConfigResolver(video_generate_audio=False)

    await gen.generate_video_async(
        prompt="p", resource_type="videos", resource_id="E1S03",
    )
    # aistudio 后端强制 audio=True，即使 config 返回 False
    assert gen.usage_tracker.started[-1]["generate_audio"] is True

@pytest.mark.asyncio
async def test_video_generate_audio_vertex_respects_config(self, tmp_path):
    """验证 vertex 后端尊重 ConfigResolver 返回的 False。"""
    gen = _build_generator(tmp_path)
    gen._gemini_video_backend_type = "vertex"
    gen._config = _FakeConfigResolver(video_generate_audio=False)

    await gen.generate_video_async(
        prompt="p", resource_type="videos", resource_id="E1S04",
    )
    assert gen.usage_tracker.started[-1]["generate_audio"] is False
```

- [ ] **Step 2: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_media_generator_module.py -v`
Expected: 全部 PASS

- [ ] **Step 3: 提交**

```bash
git add tests/test_media_generator_module.py
git commit -m "test: add ConfigResolver integration tests for MediaGenerator"
```

---

### Task 6: 全量回归测试与清理

**Files:**
- 全部已修改文件

- [ ] **Step 1: 运行全量测试套件**

Run: `uv run python -m pytest -v`
Expected: 全部 PASS

- [ ] **Step 2: 验证 `_load_all_config` 无残留引用**

Run: `grep -r "_load_all_config\|_BulkConfig" lib/ server/ tests/ --include="*.py"`
Expected: 无匹配（已全部移除）

- [ ] **Step 3: 验证 `_resolve_video_generate_audio` 无残留引用**

Run: `grep -r "_resolve_video_generate_audio\|_video_generate_audio" lib/ server/ tests/ --include="*.py"`
Expected: 无匹配（已全部移除）

- [ ] **Step 4: 提交（如有清理）**

```bash
git add -A
git commit -m "chore: remove stale references to _BulkConfig and _video_generate_audio"
```

> **行为变更说明**：ConfigResolver 不像旧 `_load_all_config()` 那样在 DB 异常时静默回退到 `True`。DB 异常现在会抛出，这是设计规格中的有意决策——避免配置读取失败时静默启用音频生成。
