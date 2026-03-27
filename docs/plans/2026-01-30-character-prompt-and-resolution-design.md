# 角色设计 Prompt 优化与分辨率升级设计

> 优化角色设计图的 prompt 模板，升级图片/视频分辨率，调整角色设计图比例

---

## 1. 需求概述

| 需求 | 说明 |
|------|------|
| 角色描述结构化 | 优化 prompt 模板格式，保持 description 单一字段 |
| 角色设计图比例 | 从 16:9 改为 **3:4**（单人全身像） |
| 线索设计图比例 | 保持 **16:9** 不变 |
| 图片分辨率 | 所有图片默认使用 **2K**（API 参数 `image_size="2K"`） |
| 视频分辨率 | 从 720p 改为 **1080p** |
| WebUI 同步 | 调整角色卡片展示框适配 3:4 比例 |

---

## 2. Prompt 模板优化

### 2.1 角色设计图 Prompt

**修改文件**: `.claude/skills/generate-characters/scripts/generate_character.py`

**之前**:
```python
def build_character_prompt(name: str, description: str, style: str = "") -> str:
    style_prefix = f"，{style}" if style else ""

    prompt = f"""一张专业的角色设计参考图{style_prefix}。

角色「{name}」的三视图设计稿。{description}

三个等比例全身像水平排列在纯净浅灰背景上：左侧正面、中间四分之三侧面、右侧纯侧面轮廓。柔和均匀的摄影棚照明，无强烈阴影。"""

    return prompt
```

**之后**:
```python
def build_character_prompt(name: str, description: str, style: str = "") -> str:
    style_part = f"，{style}" if style else ""

    prompt = f"""角色设计参考图{style_part}。

「{name}」的全身立绘。

{description}

构图要求：单人全身像，站立姿态自然，面向镜头。
背景：纯净浅灰色，无任何装饰元素。
光线：柔和均匀的摄影棚照明，无强烈阴影。
画质：高清，细节清晰，色彩准确。"""

    return prompt
```

**改进点**:
- 移除三视图要求（改为单人全身像适配 3:4）
- 结构化呈现构图/背景/光线要求
- 更清晰的层次便于模型理解

### 2.2 线索设计图 Prompt

**修改文件**: `.claude/skills/generate-clues/scripts/generate_clue.py`

保持现有 prompt 结构不变，仅确保使用 2K 分辨率。

---

## 3. API 参数修改

### 3.1 GeminiClient 图片生成

**修改文件**: `lib/gemini_client.py`

在 `_prepare_image_config` 方法中添加 `image_size` 参数支持：

```python
def _prepare_image_config(self, aspect_ratio: str, image_size: str = "2K"):
    """构建图片生成配置"""
    return self.types.GenerateContentConfig(
        response_modalities=['IMAGE'],
        image_config=self.types.ImageConfig(
            aspect_ratio=aspect_ratio,
            image_size=image_size
        )
    )
```

更新所有调用 `_prepare_image_config` 的方法签名：
- `generate_image()` 添加 `image_size: str = "2K"` 参数
- `generate_image_async()` 添加 `image_size: str = "2K"` 参数

### 3.2 MediaGenerator 默认值

**修改文件**: `lib/media_generator.py`

```python
def generate_image(
    self,
    prompt: str,
    resource_type: str,
    resource_id: str,
    reference_images: Optional[List[Union[str, Path, Image.Image]]] = None,
    aspect_ratio: str = "9:16",
    image_size: str = "2K",  # 新增，默认 2K
    **version_metadata
) -> Tuple[Path, int]:
```

视频生成默认分辨率改为 1080p：

```python
def generate_video(
    self,
    prompt: str,
    resource_type: str,
    resource_id: str,
    start_image: Optional[Union[str, Path, Image.Image]] = None,
    aspect_ratio: str = "9:16",
    duration_seconds: str = "8",
    resolution: str = "1080p",  # 从 720p 改为 1080p
    ...
) -> Tuple[Path, int, any, Optional[str]]:
```

### 3.3 角色设计图比例

**修改文件**: `.claude/skills/generate-characters/scripts/generate_character.py`

```python
output_path, version = generator.generate_image(
    prompt=prompt,
    resource_type="characters",
    resource_id=character_name,
    aspect_ratio="3:4"  # 从 16:9 改为 3:4
)
```

### 3.4 WebUI 生成路由

**修改文件**: `webui/server/routers/generate.py`

更新 `get_aspect_ratio` 函数：

```python
def get_aspect_ratio(project: dict, resource_type: str) -> str:
    content_mode = project.get("content_mode", "narration")

    # 检查自定义比例
    custom_ratios = project.get("aspect_ratio", {})
    if resource_type in custom_ratios:
        return custom_ratios[resource_type]

    # 默认比例
    if resource_type == "characters":
        return "3:4"  # 角色设计图改为 3:4
    elif resource_type == "clues":
        return "16:9"  # 线索保持 16:9
    elif content_mode == "narration":
        return "9:16"  # 说书模式竖屏
    else:
        return "16:9"  # 剧集模式横屏
```

角色生成 API 添加 `image_size` 参数：

```python
_, new_version = await generator.generate_image_async(
    prompt=full_prompt,
    resource_type="characters",
    resource_id=char_name,
    aspect_ratio=aspect_ratio,
    image_size="2K"  # 新增
)
```

---

## 4. WebUI 调整

### 4.1 CSS 添加 3:4 比例类

**修改文件**: `webui/css/styles.css`

```css
/* 3:4 竖版比例（角色设计图） */
.aspect-portrait-3-4 {
    aspect-ratio: 3 / 4;
}
```

### 4.2 角色卡片渲染

**修改文件**: `webui/js/project.js`

在渲染角色卡片时，图片容器使用新的比例类：

```javascript
// 角色卡片图片容器
<div class="aspect-portrait-3-4 bg-gray-700 rounded-lg overflow-hidden">
    <img src="${imageSrc}" class="w-full h-full object-cover" />
</div>
```

### 4.3 角色编辑模态框预览

**修改文件**: `webui/project.html`

角色图片预览区域调整：

```html
<!-- 之前 -->
<div id="char-image-preview" class="hidden mb-4">
    <img src="" alt="预览" class="max-h-48 mx-auto rounded">
</div>

<!-- 之后 -->
<div id="char-image-preview" class="hidden mb-4">
    <img src="" alt="预览" class="max-h-64 mx-auto rounded aspect-portrait-3-4 object-cover">
</div>
```

---

## 5. 文档更新

**修改文件**: `CLAUDE.md`

更新相关说明：

```markdown
### 视频规格
- **图片分辨率**：2K（通过 API 参数设置）
- **视频分辨率**：1080p

### 设计图规格
- **角色设计图**：3:4 竖版，2K 分辨率
- **线索设计图**：16:9 横版，2K 分辨率
```

---

## 6. 修改文件清单

| 文件 | 修改内容 |
|------|----------|
| `lib/gemini_client.py` | 添加 `image_size` 参数支持 |
| `lib/media_generator.py` | 默认 `image_size="2K"`，视频 `resolution="1080p"` |
| `.claude/skills/generate-characters/scripts/generate_character.py` | 优化 prompt，比例改为 3:4 |
| `webui/server/routers/generate.py` | 更新默认比例和分辨率 |
| `webui/css/styles.css` | 添加 `.aspect-portrait-3-4` 类 |
| `webui/js/project.js` | 角色卡片使用新比例 |
| `webui/project.html` | 角色预览区域调整 |
| `CLAUDE.md` | 更新文档说明 |

---

## 7. 向后兼容性

- `project.json` 中的 `aspect_ratio` 自定义配置优先级最高，可覆盖默认值
- 现有项目的角色/线索设计图不受影响，仅新生成的图片使用新规格
- `description` 字段保持不变，无需迁移数据

---

*设计完成时间: 2026-01-30*
