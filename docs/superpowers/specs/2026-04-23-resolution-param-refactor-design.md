# 视频/图片分辨率参数重构设计

- Issue: #359
- 分支: `feat/resolution-param-refactor`
- 日期: 2026-04-23

## 背景

当前各供应商后端的分辨率参数处理不一致：

- Gemini image: `aspect_ratio` + `image_size` 两个参数透传 SDK
- OpenAI image: 内部 `_SIZE_MAP` 把 `aspect_ratio` 翻成 `size`，`image_size` 翻成 `quality`
- Ark image: 完全不传分辨率，走 SDK 默认
- Grok image: `aspect_ratio` + 自定义 `_map_image_size_to_resolution` 映射
- 视频后端: 统一 `aspect_ratio + resolution`，但 OpenAI/Sora 内部再映射成 `size`；部分模型 SDK 不接受某些参数时会报错或被忽略

注册表 `ModelInfo` 未声明分辨率能力，默认值散落在 `server/services/generation_tasks.py` 的 `DEFAULT_VIDEO_RESOLUTION` dict 与各 backend 的常量中；调用方无法在运行时按模型粒度覆盖。

## 目标

1. `ModelInfo` 记录每个模型支持的分辨率列表（仅用于前端下拉选项来源，不做强校验）
2. 自定义供应商的每个模型支持配置默认分辨率（combobox：下拉 + 手填）
3. 项目设置支持按 `(provider, model)` 粒度配置分辨率
4. 媒体生成时按 `project.override → custom_model.default → 不传` 解析；对 SDK 非必传参数，未配置时不传
5. 各后端对不支持的参数进行兼容处理（宽容透传 + warning）

## 非目标

- **不改动画面比例 `aspect_ratio`**：保留现有 `get_aspect_ratio()` 规则（资产类型 + 项目画面比例）
- **不增加系统级全局默认分辨率**：自定义供应商的模型默认值已经覆盖了"系统层"的语义
- 不做一次性数据迁移脚本（legacy 字段在保存时自动迁移）

## 分辨率标准 token

统一的 token 集合，用于预置 backend 的翻译表 key 与前端下拉选项：

- **图片**: `["512px", "1K", "2K", "4K"]`
- **视频**: `["480p", "720p", "1080p", "4K"]`

各 backend 维护自身的标准 token → 原生 SDK 值翻译表（见设计段 3）。

---

## 1. 数据模型

### 1.1 `lib/config/registry.py` — ModelInfo 新增字段

```python
@dataclass(frozen=True)
class ModelInfo:
    display_name: str
    media_type: str
    capabilities: list[str]
    default: bool = False
    supported_durations: list[int] = field(default_factory=list)
    duration_resolution_constraints: dict[str, list[int]] = field(default_factory=dict)
    # 新增：前端下拉选项集合；text 模型留空
    resolutions: list[str] = field(default_factory=list)
```

预置模型的 `resolutions` 按各模型官方文档实际支持填写。Ark image（目前不传分辨率）留 `[]`，UI 不展示下拉控件。

### 1.2 `lib/db/models/custom_provider.py` — CustomProviderModel 新增列

```python
class CustomProviderModel(Base):
    # 现有列 ...
    resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)
```

配合 Alembic 迁移：新增 nullable 列，无数据回填。

### 1.3 `project.json` — 新增统一结构

```jsonc
{
  "model_settings": {
    "gemini-aistudio/veo-3.1-lite-generate-preview": { "resolution": "1080p" },
    "ark/doubao-seedream-5-0-lite-260128": { "resolution": "2K" }
  }
}
```

- Key = `"<provider_id>/<model_id>"`（复合 key，避免跨供应商同名模型冲突）
- 旧字段 `project.video_model_settings[model_id].resolution` 保留读兼容；**任何一次对 `model_settings` 的 PATCH 写入**会把命中的 legacy 条目迁移到新字段并从 `video_model_settings` 删除（由 `ProjectManager` 负责）

---

## 2. 分辨率解析链

### 2.1 解析顺序

```
project.model_settings["<provider>/<model>"].resolution
    → project.video_model_settings[model].resolution  (legacy read)
    → custom_model.resolution  (仅自定义供应商)
    → None  (不传给 SDK)
```

### 2.2 解析函数

新建 `server/services/resolution_resolver.py`（作为独立模块，便于单元测试）：

```python
def resolve_resolution(
    project: dict,
    provider_id: str,
    model_id: str,
    custom_default: str | None = None,
) -> str | None:
    """按 project > custom_default > None 顺序解析分辨率。

    返回 None 表示未配置，调用时不传该参数。
    """
    key = f"{provider_id}/{model_id}"
    project_override = (
        project.get("model_settings", {}).get(key, {}).get("resolution")
    )
    if project_override:
        return project_override

    legacy = (
        project.get("video_model_settings", {}).get(model_id, {}).get("resolution")
    )
    if legacy:
        return legacy

    if custom_default:
        return custom_default

    return None
```

### 2.3 调用点改造

- `generation_tasks.py` 顶部的 `DEFAULT_VIDEO_RESOLUTION` dict **删除**
- `generate_storyboard_task` / `generate_character_task` / `generate_scene_or_prop_task`（及 grid 相关）里 `image_size="1K"` / `"2K"` 的硬编码替换为 `resolve_resolution(...)` 返回值
- `generate_video_task` 现有的 `model_settings.get("resolution") or DEFAULT_VIDEO_RESOLUTION.get(...)` 替换为 `resolve_resolution(...)`
- `server/services/reference_video_tasks.py:239-243`（已引用 `project.video_model_settings`）同步改为 `resolve_resolution()`
- 自定义供应商的 `custom_default` 通过 `CustomProviderRepository.get_model(...)` 查到的 `resolution` 字段注入

### 2.4 Request 归一化

`MediaGenerator.generate_image_async` / `generate_video_async` 的 `image_size` / `resolution` 参数类型改为 `str | None`，默认 `None`。

---

## 3. 后端 "不传" 语义

### 3.1 Request dataclass

`lib/image_backends/base.py`:
```python
@dataclass
class ImageGenerationRequest:
    prompt: str
    output_path: Path
    reference_images: list[ReferenceImage] = field(default_factory=list)
    aspect_ratio: str = "9:16"       # 不在本次重构范围
    image_size: str | None = None    # None = 不传
    project_name: str | None = None
    seed: int | None = None
```

`lib/video_backends/base.py`:
```python
@dataclass
class VideoGenerationRequest:
    # ...
    resolution: str | None = None    # None = 不传
```

### 3.2 各后端改造点

| Backend | 改造 | 备注 |
|---|---|---|
| Gemini image | `image_size` 非 None 时构造 `ImageConfig(image_size=...)`；`aspect_ratio` 保持传入 | SDK 两字段都可 Optional |
| Gemini video | `resolution` 非 None 才加入 kwargs | |
| Ark image | 当前已不传，**保持不变** | `ModelInfo.resolutions=[]`；UI 不展示下拉 |
| Ark video | `resolution` 非 None 才加入 kwargs；若 SDK 必传则降级为强制选择（见 §5.4） | 实施阶段验证 |
| Grok image | 删除 `_map_image_size_to_resolution`，标准 token 直接透传；None 则不传 | 标准 token 已接近 Grok 原生值 |
| Grok video | `resolution` 非 None 才加入 kwargs；验证 #387 回归 | |
| OpenAI image | `_SIZE_MAP` 的 key 改为标准 token × aspect 复合键（标准 token 如 `"1K"` + `"9:16"` → `"1024x1792"`）决定 `size`；`quality` 仍由 `image_size` 推导；`image_size=None` 时 `size` 和 `quality` 都不传 | 移除旧的按 aspect 单键查表 |
| OpenAI video | `_SIZE_MAP` 已是 `(resolution, aspect_ratio)` 复合 key；None 时不传 `size` | 走 SDK 默认 |
| 自定义 provider | `lib/custom_provider/factory.py` 的 OpenAI-compat / Google-compat wrapper：`image_size` / `resolution` 字符串直接透传，不查翻译表 | |

### 3.3 翻译表兜底（仅预置供应商）

翻译表查不到对应 token → warning 后直接透传字符串（宽容），由 SDK 自行校验并报错。

---

## 4. 前端 UI

### 4.1 `ResolutionPicker` 组件

新建 `frontend/src/components/shared/ResolutionPicker.tsx`：

```tsx
interface Props {
  mode: "select" | "combobox";
  options: string[];
  value: string | null;             // null = 未配置
  onChange: (v: string | null) => void;
  placeholder?: string;             // 如 "默认（不传）"
  disabled?: boolean;
}
```

- `mode="select"`：标准下拉，首项为 placeholder（映射 null）
- `mode="combobox"`：下拉 + 手填（用 `<input list=...>` 或 @floating-ui 自造）；空字符串归一化为 null
- `options=[]` 时组件不渲染（Ark image 场景）

### 4.2 接入点

**1. 自定义供应商模型管理页**（`frontend/src/components/pages/settings/` 下相关组件）
- 每个模型行新增 "默认分辨率" 字段
- `mode="combobox"`，`options` 按 `media_type` 给标准 token 全量
- 写入 `CustomProviderModel.resolution`

**2. 创建项目向导 Step2**（`frontend/src/components/pages/create-project/WizardStep2Models.tsx`）
- 选定 image/video backend 后，下方展开"分辨率"行
- 预置模型 `mode="select"`，`options` 取 `ProviderInfo.models[model].resolutions`
- 自定义模型 `mode="combobox"`，`options` = 标准 token 全量；预填值 = 该模型的 `resolution` 默认
- 值写入提交时的 `CreateProjectRequest.model_settings`

**3. 项目设置页**（`frontend/src/components/pages/ProjectSettingsPage.tsx`）
- image/video backend 选择后展开分辨率控件（同 wizard 的模式切换规则）
- 读写 `project.model_settings["<provider>/<model>"].resolution`

### 4.3 数据流

- `GET /api/v1/providers` 返回的 `ModelInfoResponse` 新增 `resolutions: string[]`
- `GET /api/v1/custom-providers/{id}/models` 返回项新增 `resolution: string | null`
- `POST /api/v1/projects` 和 `PATCH /api/v1/projects/{name}` 接受 `model_settings: Record<string, { resolution?: string | null }>`

### 4.4 i18n

`frontend/src/i18n/{zh,en}/dashboard.ts` 新增 key：

- `resolution_label` — "分辨率" / "Resolution"
- `resolution_default_placeholder` — "默认（不传）" / "Default (unset)"
- `resolution_field_help` — 辅助说明

---

## 5. 测试、迁移与风险

### 5.1 Alembic 迁移

新增 `CustomProviderModel.resolution` 列（nullable，无回填）。迁移脚本路径：`alembic/versions/`。

### 5.2 project.json legacy 兼容

- 读路径：`resolve_resolution()` 额外读 `video_model_settings[model].resolution`
- 写路径：`ProjectManager` 在 PATCH `model_settings` 时，若命中 legacy 条目，把 resolution 迁入新字段并从 `video_model_settings` 删除（保留其他可能的字段）

### 5.3 测试矩阵

| 层 | 测试文件 | 覆盖点 |
|---|---|---|
| registry | `tests/lib/config/test_registry.py`（扩展） | 所有预置 image/video 模型的 `resolutions` 非空；text 模型为 `[]` |
| resolver | `tests/server/services/test_resolve_resolution.py`（新建） | project > custom_default > None 顺序；legacy 字段读取与迁移；复合 key 冲突 |
| 各 backend | `tests/lib/{image,video}_backends/test_*.py` | 传 None 时 SDK 调用参数不含 size/resolution；传字符串时忠实透传；Grok 去除 `_map_image_size_to_resolution` 后的行为 |
| custom provider | `tests/lib/custom_provider/test_factory.py` | OpenAI-compat / Google-compat wrapper 透传自定义分辨率字符串 |
| API | `tests/server/routers/test_projects.py` | PATCH `model_settings` 写入；legacy 字段 auto-migrate |
| API | `tests/server/routers/test_custom_providers.py` | 模型 CRUD 的 `resolution` 字段持久化 |
| Frontend | `ResolutionPicker.test.tsx`（新建） | select / combobox 两种模式；null 渲染为 placeholder；空 options 不渲染 |
| Frontend | `WizardStep2Models.test.tsx`、`ProjectSettingsPage.test.tsx`（扩展） | 选定 backend 后出现分辨率控件；保存写入 model_settings |
| i18n | `tests/lib/i18n/test_i18n_consistency.py`（自动） | zh/en key 对称 |

### 5.4 回归风险点

- **`DEFAULT_VIDEO_RESOLUTION` 移除**：`generation_tasks.py` 所有老调用路径必须切到 `resolve_resolution()`，不能有遗漏
- **OpenAI image `_SIZE_MAP` 重写**（aspect-only → `(size, aspect)` 复合 key）：需覆盖所有现有 case，不能丢失降级路径
- **Grok video 默认不传** 可能触发 #387 类型的 xai_sdk 报错：实施阶段按模型验证
- **SDK 必传性验证**：以下若验证为 SDK 必传，该模型的分辨率 UI 强制选择（disable null），但不在 registry 里写 default——保持"不选=不传"语义一致：
  - Gemini image: `ImageConfig(image_size=None)` 是否接受
  - Ark video: 不传 `resolution` 是否接受
  - Grok image / video: 不传 `resolution` 是否接受

---

## 验收标准回溯

- [x] 注册表（`ModelInfo`）记录每个模型支持的分辨率/图片尺寸列表 — §1.1
- [x] 自定义供应商模型支持配置默认分辨率 — §1.2 §4.2.1
- [x] 项目设置支持分辨率配置 — §1.3 §4.2.3
- [x] 媒体生成时按 `project → custom_default → 不传` 顺序解析；若 SDK 非必传且模型未声明支持，则不传 — §2
- [x] 各后端针对不支持参数的兼容验证 — §3 §5.4

## 不在本次范围

- 模型选择的"项目 > 系统 > 注册表默认"解析顺序（已存在，不改）
- 系统级全局分辨率默认（由"自定义供应商模型默认 + 项目覆盖"组合代替）
- 画面比例 `aspect_ratio` 的可配置化（保留现有规则）
