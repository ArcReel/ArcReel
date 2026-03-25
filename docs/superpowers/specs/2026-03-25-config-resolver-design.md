# ConfigResolver：统一运行时配置解析

> 日期：2026-03-25
> 状态：设计已确认

## 问题

`video_generate_audio` 配置项在从 DB 到 Vertex API 的传递链路中经过 6 个文件、4 层传递，且存在 **默认值不一致** 的 bug：

| 位置 | 默认值 |
|------|--------|
| `server/routers/system_config.py` GET | `False` |
| `server/services/generation_tasks.py` `_load_all_config()` | `True`（字符串 `"true"`） |
| `server/services/generation_tasks.py` 异常回退 | `True` |
| `lib/media_generator.py` `_resolve_video_generate_audio()` | `True` |
| `lib/gemini_client.py` 参数签名 | `True` |
| `lib/system_config.py`（已废弃路径） | `True` |

用户在系统全局配置中关闭音频生成后，由于传递链路中某环节回退到 `True` 默认值，实际仍然生成了音频。

更深层的问题是架构性的：配置值通过参数层层透传（DB → `_BulkConfig` → `get_media_generator()` → `MediaGenerator.__init__()` → `generate_video()`），每一层都有自己的默认值，链条脆弱且难以维护。

## 方案

引入 `ConfigResolver` 作为 `ConfigService` 的上层薄封装，提供：

1. **唯一的默认值定义点** — 消除散落在各文件中的重复默认值（复用 ConfigService 已有常量）
2. **类型化输出** — 调用者拿到 `bool`/`tuple[str, str]`/`dict`，不再处理原始字符串
3. **内置优先级解析** — 全局配置 → 项目级覆盖
4. **用时读取** — 每次调用从 DB 读取，不缓存（本地 SQLite 开销可忽略）

## 设计

### 新增：`lib/config/resolver.py`

```python
from sqlalchemy.ext.asyncio import async_sessionmaker
from lib.config.service import ConfigService, _DEFAULT_VIDEO_BACKEND, _DEFAULT_IMAGE_BACKEND
from lib.project_manager import get_project_manager

class ConfigResolver:
    """运行时配置解析器。每次调用从 DB 读取，不缓存。"""

    # 唯一的默认值定义点。后端默认值复用 ConfigService 常量。
    _DEFAULT_VIDEO_GENERATE_AUDIO = False

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def video_generate_audio(self, project_name: str | None = None) -> bool:
        """解析 video_generate_audio。

        优先级：项目级覆盖 > 全局配置 > 默认值(False)。
        项目级覆盖从 project.json 读取（通过 ProjectManager）。
        """
        # 1. 从 DB 读全局配置
        async with self._session_factory() as session:
            svc = ConfigService(session)
            raw = await svc.get_setting("video_generate_audio", "")

        if raw:
            value = raw.lower() in ("true", "1", "yes")
        else:
            value = self._DEFAULT_VIDEO_GENERATE_AUDIO

        # 2. 如有 project_name，读项目级覆盖
        if project_name:
            project = get_project_manager().load_project(project_name)
            override = project.get("video_generate_audio")
            if override is not None:
                value = bool(override) if not isinstance(override, str) else override.lower() in ("true", "1", "yes")

        return value

    async def default_video_backend(self) -> tuple[str, str]:
        """返回 (provider_id, model_id)。复用 ConfigService 的解析逻辑和默认值。"""
        async with self._session_factory() as session:
            svc = ConfigService(session)
            return await svc.get_default_video_backend()

    async def default_image_backend(self) -> tuple[str, str]:
        """返回 (provider_id, model_id)。复用 ConfigService 的解析逻辑和默认值。"""
        async with self._session_factory() as session:
            svc = ConfigService(session)
            return await svc.get_default_image_backend()

    async def provider_config(self, provider_id: str) -> dict[str, str]:
        """获取单个供应商配置。"""
        async with self._session_factory() as session:
            svc = ConfigService(session)
            return await svc.get_provider_config(provider_id)

    async def all_provider_configs(self) -> dict[str, dict[str, str]]:
        """批量获取所有供应商配置。"""
        async with self._session_factory() as session:
            svc = ConfigService(session)
            return await svc.get_all_provider_configs()
```

### 改造：`lib/media_generator.py`

**移除：**
- 构造函数中的 `video_generate_audio` 参数
- `self._video_generate_audio` 字段
- `_resolve_video_generate_audio()` 方法

**新增：**
- 构造函数接收 `config_resolver: ConfigResolver`
- `generate_video()` / `generate_video_async()` 中调用 `self._config.video_generate_audio(project_name)` 获取配置值

**同步 `generate_video()` 路径**：通过现有 `_sync()` helper 调用 async 的 ConfigResolver 方法，与其他 async 调用方式一致。

**后端特定逻辑保留在 MediaGenerator 中**：ConfigResolver 返回的是"用户意图"（想不想要音频），而后端能力限制（如 aistudio 不支持关闭音频）由 MediaGenerator 在调用处处理：

```python
# ConfigResolver 返回用户配置
configured_generate_audio = await self._config.video_generate_audio(self.project_name)

# 后端能力限制仍在 MediaGenerator 处理
if self._gemini_video_backend_type == "vertex":
    effective_generate_audio = configured_generate_audio
else:
    effective_generate_audio = True  # aistudio 不支持关闭音频
```

**`version_metadata` 调用级覆盖保留**：这是第四优先级，仍在 MediaGenerator 内处理，不进 ConfigResolver。完整优先级链：

```
version_metadata > 项目级覆盖 > 全局配置 > 默认值(False)
    ↑ MediaGenerator      ↑ ConfigResolver 内部处理
```

变更前后对比：

```python
# 之前
class MediaGenerator:
    def __init__(self, ..., video_generate_audio=None):
        self._video_generate_audio = video_generate_audio

    def _resolve_video_generate_audio(self) -> bool:
        if self._video_generate_audio is not None:
            return self._video_generate_audio
        return True  # 散落的默认值

    async def generate_video_async(self, ...):
        configured = self._resolve_video_generate_audio()
        ...

# 之后
class MediaGenerator:
    def __init__(self, ..., config_resolver: ConfigResolver):
        self._config = config_resolver

    async def generate_video_async(self, ...):
        configured = await self._config.video_generate_audio(self.project_name)
        # 后端能力限制
        if not self._video_backend and self._gemini_video_backend_type != "vertex":
            effective = True
        else:
            effective = version_metadata.get("generate_audio", configured)
        ...
```

### 改造：`server/services/generation_tasks.py`

**移除：**
- `_BulkConfig` 数据类
- `_load_all_config()` 函数
- `get_media_generator()` 中的 `video_generate_audio` 参数解析和项目级覆盖逻辑

**改造：**
- `_resolve_video_backend()` / `_resolve_image_backend()` 改为接收 `ConfigResolver`
- `_get_or_create_video_backend()` 改为使用 `ConfigResolver`
- `get_media_generator()` 创建 `ConfigResolver` 实例并传给 `MediaGenerator`

简化后的 `get_media_generator()`：

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

### 改造：`server/routers/generate.py`

移除对 `_load_all_config()` 的跨模块导入，替换为：

```python
from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory

resolver = ConfigResolver(async_session_factory)
video_provider, video_model = await resolver.default_video_backend()
```

### 不变的部分

- **`lib/gemini_client.py`** — 继续接收 `generate_audio: bool` 参数，它是通用客户端，不依赖业务配置层
- **`lib/generation_worker.py`** — 已有独立的 ConfigService 调用路径，不受影响
- **`server/routers/system_config.py`** — GET/PATCH 端点直接用 ConfigService 读写原始值，不受影响
- **`server/agent_runtime/session_manager.py`** — 独立使用 ConfigService，不受影响
- **`server/routers/projects.py`** — 项目级 `video_generate_audio` 的写入端不变，仍写入 project.json

### 废弃清理

- **`lib/system_config.py`** — 其中 `video_generate_audio` 相关的环境变量映射逻辑（`GEMINI_VIDEO_GENERATE_AUDIO`）已被 DB 路径取代。ConfigResolver 上线后，该文件中的 audio 相关代码应标记为 dead code 并在后续清理。

## 影响范围

| 文件 | 变更类型 |
|------|---------|
| `lib/config/resolver.py` | **新增** |
| `lib/config/__init__.py` | 导出 ConfigResolver |
| `lib/media_generator.py` | 移除 audio 参数/方法，新增 config_resolver |
| `server/services/generation_tasks.py` | 移除 `_BulkConfig`/`_load_all_config()`，使用 ConfigResolver |
| `server/routers/generate.py` | 移除 `_load_all_config()` 导入，使用 ConfigResolver |
| 测试文件 | 更新 MediaGenerator 构造方式 |

## 测试策略

1. **ConfigResolver 单元测试**
   - 默认值：DB 无值时返回 `False`
   - 全局配置读取：DB 有值时正确解析布尔字符串（`"true"`, `"false"`, `"TRUE"`, `"0"`, `"1"`, `"yes"`）
   - 项目级覆盖优先级：项目值非 None 时覆盖全局值
   - `project_name=None` 时跳过项目级覆盖
   - DB 异常时的行为（应抛出异常而非静默回退到 True）
2. **MediaGenerator 集成测试**
   - 验证 `generate_video` 通过 ConfigResolver 获取正确的 audio 设置
   - 验证 aistudio 后端仍强制 `audio=True`
   - 验证 `version_metadata` 调用级覆盖正常工作
3. **回归测试** — 现有测试适配新的构造方式后应全部通过
