# MediaGenerator 中间层设计

**日期**: 2026-01-29
**状态**: 已确认

---

## 需求概述

为图片和视频生成引入自动版本管理机制，调用方无感。通过创建 `MediaGenerator` 中间层，封装 `GeminiClient` + `VersionManager`。

---

## 核心定位

`MediaGenerator` 是一个中间层，封装 `GeminiClient` + `VersionManager`，提供"调用方无感"的版本管理。

**核心原则：**
- 调用方只需传入 `project_path` 和 `resource_id`
- 版本管理自动完成（备份、记录、跟踪）
- 不改变现有 `GeminiClient` 的职责

**覆盖的 4 种资源类型：**

| 资源类型 | 当前调用位置 | resource_id 格式 |
|---------|-------------|-----------------|
| `storyboards` | generate_storyboard.py, webui | `E1S01` (segment/scene ID) |
| `videos` | generate_video.py, webui | `E1S01` (segment/scene ID) |
| `characters` | generate_character.py, webui | `姜月茴` (角色名) |
| `clues` | generate_clue.py, webui | `玉佩` (线索名) |

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
    resource_type: str,  # 'storyboards' | 'characters' | 'clues'
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
| `clues` | `{project}/clues/{resource_id}.png` |

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
| `.claude/skills/generate-storyboard/scripts/generate_storyboard.py` | 替换 GeminiClient → MediaGenerator |
| `.claude/skills/generate-video/scripts/generate_video.py` | 替换 GeminiClient → MediaGenerator |
| `.claude/skills/generate-characters/scripts/generate_character.py` | 替换 GeminiClient → MediaGenerator |
| `.claude/skills/generate-clues/scripts/generate_clue.py` | 替换 GeminiClient → MediaGenerator |
| `webui/server/routers/generate.py` | 简化，移除手动版本管理代码 |

### 不修改的文件

- `lib/gemini_client.py` - 保持不变
- `lib/version_manager.py` - 保持不变

### 实现优先级

| 阶段 | 内容 |
|-----|------|
| Phase 1 | 创建 `lib/media_generator.py`，实现 4 个核心方法 |
| Phase 2 | 迁移 4 个 skill 脚本 |
| Phase 3 | 简化 webui router |

---

## 注意事项

1. **线程安全**：`VersionManager` 已实现线程安全锁，`MediaGenerator` 可直接复用
2. **异步支持**：需要同时提供同步和异步版本的方法
3. **向后兼容**：`GeminiClient` 保持不变，现有直接调用不受影响
4. **元数据传递**：通过 `**version_metadata` 支持传递额外信息（如 `aspect_ratio`、`duration_seconds`）
