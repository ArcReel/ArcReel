# MediaGenerator 中间层设计

**日期**: 2026-01-29
**状态**: 已确认

---

## 需求概述

为图片和视频生成引入自动版本管理机制，调用方无感。通过创建 `MediaGenerator` 中间层，封装媒体生成后端 + `VersionManager`。

> 演进说明：初版直接封装 `GeminiClient`。现实现封装可插拔的 image/video backend（Registry + Factory，
> 多供应商），并组合 `VersionManager` + `UsageTracker`；下文以 GeminiClient 为例描述中间层职责。
> 资源类型 `clues` 已拆分为 `scenes` / `props`（另增 `grids` / `reference_videos`）。

---

## 核心定位

`MediaGenerator` 是一个中间层，封装媒体生成后端 + `VersionManager`，提供"调用方无感"的版本管理。

**核心原则：**
- 调用方只需传入 `project_path` 和 `resource_id`
- 版本管理自动完成（备份、记录、跟踪）
- 不改变底层媒体后端的职责

**覆盖的资源类型：**

| 资源类型 | 当前调用位置 | resource_id 格式 |
|---------|-------------|-----------------|
| `storyboards` | 分镜生成, 前端 | `E1S01` (segment/scene ID) |
| `videos` | 视频生成, 前端 | `E1S01` (segment/scene ID) |
| `characters` | 资产生成, 前端 | `姜月茴` (角色名) |
| `scenes` | 资产生成, 前端 | `庙宇` (场景名) |
| `props` | 资产生成, 前端 | `玉佩` (道具名) |

---

## API 接口设计

### 类初始化

```python
class MediaGenerator:
    def __init__(
        self,
        project_path: Path,
        rate_limiter: Optional[RateLimiter] = None
    ):
        self.project_path = Path(project_path)
        self.gemini = GeminiClient(rate_limiter=rate_limiter)
        self.versions = VersionManager(project_path)
```

### 核心方法

| 方法 | 对应 GeminiClient 方法 | 新增参数 |
|-----|----------------------|---------|
| `generate_image()` | `generate_image()` | `resource_type`, `resource_id` |
| `generate_image_async()` | `generate_image_async()` | `resource_type`, `resource_id` |
| `generate_video()` | `generate_video()` | `resource_type`, `resource_id` |
| `generate_video_async()` | `generate_video_async()` | `resource_type`, `resource_id` |

### 版本管理逻辑（内部自动执行）

```
1. 检查 output_path 是否存在
2. 若存在 → 调用 ensure_current_tracked() 确保旧文件被记录
3. 调用 GeminiClient 生成新文件
4. 调用 add_version() 记录新版本
5. 返回结果
```

---

## 方法签名

```python
def generate_image(
    self,
    prompt: str,
    resource_type: str,  # 'storyboards' | 'characters' | 'scenes' | 'props'
    resource_id: str,    # E1S01 | 姜月茴 | 玉佩
    # 以下参数透传给 GeminiClient
    reference_images: Optional[List] = None,
    aspect_ratio: str = "9:16",
    **version_metadata  # 额外元数据：aspect_ratio, duration_seconds 等
) -> Tuple[Path, int]:
    """
    Returns:
        (output_path, version_number)
    """
```

### 输出路径自动推断

| resource_type | 输出路径模式 |
|--------------|------------|
| `storyboards` | `{project}/storyboards/scene_{resource_id}.png` |
| `videos` | `{project}/videos/scene_{resource_id}.mp4` |
| `characters` | `{project}/characters/{resource_id}.png` |
| `scenes` | `{project}/scenes/{resource_id}.png` |
| `props` | `{project}/props/{resource_id}.png` |

### 返回值变化

- 原 `GeminiClient.generate_image()` 返回 `Image`
- 新 `MediaGenerator.generate_image()` 返回 `(Path, int)` —— 路径和版本号

---

## 调用方迁移示例

### 当前 skill 脚本调用方式（以 generate_character.py 为例）

```python
# 现在
client = GeminiClient()
client.generate_image(
    prompt=prompt,
    aspect_ratio="16:9",
    output_path=output_path
)
```

### 迁移后

```python
# 迁移后
from lib.media_generator import MediaGenerator

generator = MediaGenerator(project_dir)
output_path, version = generator.generate_image(
    prompt=prompt,
    resource_type="characters",
    resource_id=character_name,
    aspect_ratio="16:9"
)
```

### 变化点

1. 导入类从 `GeminiClient` → `MediaGenerator`
2. 初始化时传入 `project_path`（而非空参数）
3. 调用时新增 `resource_type` 和 `resource_id`
4. 移除 `output_path`（自动推断）
5. 返回值新增 `version` 版本号

### webui router 的变化

- 可以删除手动调用 `VersionManager` 的代码
- 直接使用 `MediaGenerator`，逻辑更简洁

---

## 文件结构与实现计划

### 新增文件

```
lib/media_generator.py    # MediaGenerator 类
```

### 需要修改的文件

| 文件 | 改动内容 |
|-----|---------|
| `generate-storyboard` skill 脚本 | 改用 MediaGenerator |
| `generate-video` skill 脚本 | 改用 MediaGenerator |
| `generate-assets` skill 脚本（角色/场景/道具） | 改用 MediaGenerator |
| `server/routers/generate.py` | 简化，移除手动版本管理代码 |

### 不修改的文件

- 底层媒体后端 - 保持各自职责
- `lib/version_manager.py` - 保持不变

### 实现优先级

| 阶段 | 内容 |
|-----|------|
| Phase 1 | 创建 `lib/media_generator.py`，实现核心方法 |
| Phase 2 | 迁移 skill 脚本 |
| Phase 3 | 简化生成 router |

---

## 注意事项

1. **线程安全**：`VersionManager` 已实现线程安全锁，`MediaGenerator` 可直接复用
2. **异步支持**：需要同时提供同步和异步版本的方法
3. **向后兼容**：底层媒体后端的直接调用职责不受 MediaGenerator 引入影响
4. **元数据传递**：通过 `**version_metadata` 支持传递额外信息（如 `aspect_ratio`、`duration_seconds`）
