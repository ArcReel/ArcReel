# Grok Provider Multi-Issue Fix Design

## Background

Grok 供应商在实际使用中暴露出四个问题，影响图片/视频生成质量和体验：

1. 用户上传的角色参考图/角色图过大，base64 编码后超过 gRPC 4MB 限制，导致生图/生视频直接报错
2. Grok 图片生成并发实际只有 1（默认应为 5），需排查根因
3. Grok 生成分镜图时参考图只有第一张有效，其他供应商均支持多张
4. Drama 模式下分镜图生成为竖屏比例，应为 16:9 横屏

## 问题 1：上传图片压缩

### 现状

- `image_utils.py` 的 `convert_image_bytes_to_png()` 仅做格式转换（→ PNG），无压缩/缩放
- `image_to_base64_data_uri()` 直接读原始文件字节 base64 编码，无大小检查
- 用户上传的高分辨率 PNG 可轻松超过 4MB（base64 后更大，约 1.33 倍）
- Grok 图片和视频后端均通过 base64 data URI 传递参考图/起始帧

### 方案

在 `image_utils.py` 新增压缩函数，上传入口在图片大于 2MB 时调用：

- **触发条件**：上传图片原始大小 > 2MB
- **格式**：转 JPEG（quality=85）
- **分辨率**：长边不超过 2048px，等比缩放
- **≤ 2MB 时**：直接保存原始内容，不做任何转换
- **适用范围**：所有用户上传的图片（角色参考图、角色图、线索图、风格参考图），AI 生成的图片暂不处理

### 改动文件

| 文件 | Modify |
|------|------|
| `lib/image_utils.py` | 新增 `compress_image_bytes()` 函数：JPEG 转换 + 分辨率限制 |
| `server/routers/files.py` | 上传入口改调 `compress_image_bytes()` 替换 `convert_image_bytes_to_png()` |

### Notes

- 仅大于 2MB 的图片触发压缩（JPEG + 缩放），后缀改为 `.jpg`
- ≤ 2MB 的图片直接保存原始内容，保留原格式后缀（`.png`/`.jpg`/`.webp`）
- `project.json` 中的引用路径会自然反映实际后缀
- 下游代码（`image_to_base64_data_uri`、`_collect_reference_images` 等）通过路径读取文件，不依赖特定后缀，无需改动
- 已存储的旧文件无需迁移，仍可正常使用
- 风格参考图（`style_reference`）同样适用此规则，因为 Grok 文本后端的 vision 调用也通过 `image_to_base64_data_uri()` 传图，同受 gRPC 4MB 限制

## 问题 2：Grok 图片并发异常

### 现状

- `generation_worker.py` 中 `_load_pools_from_db()` 默认 image_max=5, video_max=3
- 用户未手动修改配置，但观察到 Grok 图片任务串行执行
- 视频并发正常（3）

### 排查方向

1. **DB 配置残留**：检查 `provider_configs` 表中 Grok 的 `image_max_workers` 是否被设为 1
2. **Pool 加载逻辑**：`_load_pools_from_db()` 在解析 config 时是否有类型转换或默认值问题
3. **Fallback pool**：如果 DB 加载失败，`_build_default_pools()` 是否被使用且行为正确

### 方案

- 在 worker 启动和 `reload_limits()` 时，增加 INFO 级别日志，打印每个 provider 的实际 pool 配置（image_max, video_max）
- 如果确认是 DB 残留值（如 `"1"` 或空字符串），修复 `_load_pools_from_db()` 的默认值回退逻辑

### 改动文件

| 文件 | Modify |
|------|------|
| `lib/generation_worker.py` | `_load_pools_from_db()` 和 `reload_limits()` 增加 pool 配置日志 |

## 问题 3：Grok 参考图只用第一张

### 现状

- `grok.py` 图片后端仅取 `request.reference_images[0]`，通过 `image_url`（单数）传给 API
- Grok API 实际支持 `image_urls`（复数列表），可传多张参考图
- 其他供应商均已支持多张参考图：Gemini（无限制 + label）、Ark（列表）、OpenAI（最多 16 张）

### 方案

将 `grok.py` 的 I2I 逻辑从单张改为多张：

```python
# Before
if request.reference_images:
    ref_path = Path(request.reference_images[0].path)
    if ref_path.exists():
        generate_kwargs["image_url"] = image_to_base64_data_uri(ref_path)

# After
if request.reference_images:
    data_uris = []
    for ref in request.reference_images:
        ref_path = Path(ref.path)
        if ref_path.exists():
            data_uris.append(image_to_base64_data_uri(ref_path))
    if data_uris:
        generate_kwargs["image_urls"] = data_uris
```

### 改动文件

| 文件 | Modify |
|------|------|
| `lib/image_backends/grok.py` | `generate()` 方法：改用 `image_urls` 传所有参考图 |

## 问题 4：Drama 模式分镜比例不对

### 排查结论

**ArcReel 代码传参链路完全正确**，`aspect_ratio="16:9"` 一路传递到 xAI SDK。

**根因确认**：Grok API 在单图编辑模式（`image_url` 单数参数）下会忽略 `aspect_ratio` 参数，使用参考图的原始比例。而多图编辑模式（`image_urls` 列表参数）下 `aspect_ratio` 正常生效。

### 方案

**与问题 3 合并为同一修复**：始终使用 `image_urls`（列表），即使只有一张参考图也走多图编辑路径。这样 `aspect_ratio` 参数就能被正确识别。

额外增加 aspect_ratio 支持列表校验作为兜底。Grok 支持的比例远多于最初Expected: `1:1`, `16:9`/`9:16`, `4:3`/`3:4`, `3:2`/`2:3`, `2:1`/`1:2`, `19.5:9`/`9:19.5`, `20:9`/`9:20`, `auto`。不在列表中的比例 warning 后透传给 API（不做映射）。

### 改动文件

| 文件 | Modify |
|------|------|
| `lib/image_backends/grok.py` | 已在问题 3 中改用 `image_urls`；额外增加 aspect_ratio 校验 |

## 跨问题影响

- 问题 1（图片压缩）会减小参考图体积，间接缓解问题 3（多张参考图时总体积更大）的负担
- 问题 3（切换 `image_urls`）直接修复了问题 4：多图编辑模式下 `aspect_ratio` 参数正常生效
- 所有改动仅影响 Grok 供应商（压缩除外，但压缩是通用的上传逻辑）

## Testing Strategy

- 问题 1：单元测试 `compress_image_bytes()` 对大图/小图/各种格式的处理
- 问题 2：检查日志输出确认 pool 配置正确
- 问题 3：集成测试传多张参考图到 Grok API
- 问题 4：集成测试 drama 模式生成的图片尺寸是否为横屏
