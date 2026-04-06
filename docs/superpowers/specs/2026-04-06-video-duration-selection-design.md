# 视频时长与横竖屏可配置化设计

## 背景

当前视频时长硬编码为 `[4, 6, 8]` 秒三个选项，横竖屏与 `content_mode` 强绑定（说书=竖屏，剧集=横屏）。随着多供应商视频模型接入，不同模型支持的时长各不相同，需要将这两项配置从硬编码改为动态可配置。

## 设计目标

1. 视频时长由视频模型的能力决定，精确到模型级别
2. 横竖屏（aspect_ratio）与 content_mode 完全解耦，项目创建时独立选择
3. 用户可设置项目默认时长偏好，也可选择"自动"让 AI 根据内容决定
4. 分镜级别仍可在模型支持范围内逐个选择时长
5. 向后兼容已有项目数据

## 方案：扩展 ModelInfo + 运行时解析

在现有 `ModelInfo` 和 `CustomProviderModel` 上扩展 `supported_durations` 字段，复用现有 Registry/ConfigService 体系。

---

## 1. 模型级别时长能力声明

### 1.1 预置供应商 — ModelInfo 扩展

`lib/config/registry.py` 中 `ModelInfo` 新增字段：

```python
@dataclass(frozen=True)
class ModelInfo:
    display_name: str
    media_type: str
    capabilities: list[str]
    default: bool = False
    supported_durations: list[int] = field(default_factory=list)  # 新增
```

各供应商视频模型示例：

| 供应商 | 模型 | supported_durations |
|--------|------|---------------------|
| Gemini (AI Studio/Vertex) | veo-3.1-generate-preview | [4, 6, 8] |
| Gemini (AI Studio/Vertex) | veo-3.1-lite-generate-preview | [4, 6, 8] |
| Ark | doubao-seedance-1-5-pro-251215 | 按实际填写 |
| Ark | doubao-seedance-2-0-260128 | 按实际填写 |
| Grok | grok-imagine-video | 按实际填写 |
| OpenAI | sora-2 | 按实际填写 |

非视频模型保持空列表 `[]`。

### 1.2 自定义供应商 — CustomProviderModel 扩展

`lib/db/models/custom_provider.py` 中 `CustomProviderModel` 新增列：

```python
supported_durations: Mapped[str | None] = mapped_column(Text, nullable=True)
# JSON 序列化的 list[int]，如 "[4, 8, 12]"
# null 表示使用按 api_format 的保守预设
```

需一个 Alembic 迁移。

### 1.3 保守预设

仅在自定义供应商且模型未声明 `supported_durations` 时回退：

```python
DEFAULT_DURATIONS_BY_FORMAT = {
    "openai": [4, 8, 12],
    "google": [4, 6, 8],
}
```

---

## 2. Aspect Ratio 与 Content Mode 解耦

### 2.1 项目创建

`CreateProjectRequest` 新增参数：

```python
class CreateProjectRequest(BaseModel):
    name: str | None = None
    title: str | None = None
    style: str | None = ""
    content_mode: str | None = "narration"
    aspect_ratio: str = "9:16"             # 新增，独立于 content_mode
```

`project_manager.create_project_metadata()` 新增 `aspect_ratio` 参数，写入 `project.json` 顶层：

```json
{
  "content_mode": "narration",
  "aspect_ratio": "9:16",
  ...
}
```

### 2.2 项目修改

移除 `aspect_ratio` 不可修改的限制。用户修改时前端弹出提示：已生成的分镜图/视频仍为原比例，建议重新生成。

`content_mode` 仍然创建后不可修改。

### 2.3 get_aspect_ratio() 简化

```python
def get_aspect_ratio(project: dict, resource_type: str) -> str:
    if resource_type == "characters":
        return "3:4"
    if resource_type == "clues":
        return "16:9"
    # 优先读顶层字段；缺失时按 content_mode 推导（向后兼容）
    if "aspect_ratio" in project:
        return project["aspect_ratio"]
    return "9:16" if project.get("content_mode", "narration") == "narration" else "16:9"
```

### 2.4 已有项目兼容

`project.json` 缺少 `aspect_ratio` 字段时，按原逻辑从 `content_mode` 推导（narration→`"9:16"`, drama→`"16:9"`），不做强制迁移。新项目必有此字段。

---

## 3. 项目级默认时长

### 3.1 project.json 新增字段

```json
{
  "aspect_ratio": "9:16",
  "default_duration": 4
}
```

- `default_duration: int` — 用户选择的偏好时长
- `default_duration: null`（或缺失） — "自动"，由 AI 根据内容决定

### 3.2 对剧本生成 Prompt 的影响

- 有默认值：Prompt 注入 `"时长：从 [4, 6, 8] 秒中选择，默认使用 4 秒"`
- 自动模式：Prompt 注入 `"时长：从 [4, 6, 8] 秒中选择，根据内容节奏自行决定"`

### 3.3 已有项目兼容

缺失 `default_duration` 视为 `null`（自动）。

---

## 4. DurationSeconds 类型重构

### 4.1 后端

移除 `lib/script_models.py` 中的 `DurationSeconds` 自定义类型，改为：

```python
# NarrationSegment
duration_seconds: int = Field(ge=1, le=60, description="片段时长（秒）")

# DramaScene
duration_seconds: int = Field(ge=1, le=60, description="场景时长（秒）")
```

不再在 Pydantic 层硬编码有效值，严格校验移到业务层（根据当前视频模型的 `supported_durations`）。

### 4.2 前端

```typescript
// 移除
export type DurationSeconds = 4 | 6 | 8;

// 改为
// duration_seconds 直接用 number 类型
```

---

## 5. Prompt 构建器动态化

### 5.1 函数签名变更

`lib/prompt_builders_script.py`：

```python
def build_narration_prompt(
    ...,
    supported_durations: list[int],
    default_duration: int | None,
    aspect_ratio: str,
) -> str:

def build_drama_prompt(
    ...,
    supported_durations: list[int],
    default_duration: int | None,
    aspect_ratio: str,
) -> str:
```

### 5.2 动态文本替换

**时长部分：**
- 移除硬编码 `"时长：4、6 或 8 秒"`
- 替换为根据参数动态生成的描述

**横竖屏部分：**
- `build_storyboard_suffix()` 改为接收 `aspect_ratio` 参数，根据值输出对应构图描述（`"竖屏构图。"` / `"横屏构图。"`）
- `build_drama_prompt` 中移除硬编码的 `"16:9 横屏构图"`，改为动态注入

### 5.3 调用方适配

`lib/script_generator.py`：从 `project.json` 读取 `supported_durations`（通过视频模型解析）、`default_duration`、`aspect_ratio` 后传入 Prompt 构建器。

---

## 6. Agent 脚本与视频生成适配

### 6.1 generate_video.py

`agent_runtime_profile/.claude/skills/generate-video/scripts/generate_video.py`：

- `validate_duration()` 移除硬编码 `[4, 6, 8]`，改为接收 `supported_durations` 参数
- `default_duration` 从项目配置读取，不再按 `content_mode` 硬编码 4/8
- SKILL.md 同步更新时长相关描述

### 6.2 服务层

`server/services/generation_tasks.py`：

- `execute_video_task()` 中 `duration_seconds` 回退逻辑：`payload > project.default_duration > supported_durations[0]`
- `get_aspect_ratio()` 简化为直接读 `project["aspect_ratio"]`

`server/routers/generate.py`：

- `GenerateVideoRequest.duration_seconds` 默认值从 `4` 改为 `None`，由服务层解析

---

## 7. 前端改动

### 7.1 项目创建表单

- 新增横竖屏选择器（竖屏 9:16 / 横屏 16:9），独立于 content_mode
- 新增默认时长选择器：选项从当前视频模型的 `supported_durations` 获取，额外提供"自动"选项

### 7.2 项目设置页面

- 允许修改 `aspect_ratio` 和 `default_duration`
- 修改 `aspect_ratio` 时弹出提示：已生成的分镜图/视频仍为原比例，建议重新生成
- 切换视频模型时，`default_duration` 选项联动更新；若当前值不在新模型支持列表中，重置为 `null`（自动）

### 7.3 SegmentCard 时长选择器

- `DURATION_OPTIONS` 从硬编码 `[4, 6, 8]` 改为从项目当前视频模型的 `supported_durations` 动态获取
- 数据来源：可在项目数据中通过 `StatusCalculator` 注入，或前端从 providers API 自行解析

### 7.4 TypeScript 类型

- `DurationSeconds = 4 | 6 | 8` 改为 `number`
- `ProjectData` 新增 `default_duration?: number | null`
- 顶层 `aspect_ratio: string`

---

## 8. 数据迁移与向后兼容

| 场景 | 处理方式 |
|------|---------|
| 已有项目无 `aspect_ratio` | 读取时按 `content_mode` 推导（narration→9:16, drama→16:9） |
| 已有项目无 `default_duration` | 视为 `null`（自动模式） |
| 已有剧本中 4/6/8 值 | 仍合法，无需迁移 |
| CustomProviderModel 新列 | Alembic 迁移，nullable，null 回退到按 api_format 预设 |
| API 响应 | 只新增字段，不删/改已有字段 |

---

## 涉及文件清单

### 后端
- `lib/config/registry.py` — ModelInfo 扩展 + 各供应商时长声明
- `lib/db/models/custom_provider.py` — CustomProviderModel 新增列
- `lib/script_models.py` — 移除 DurationSeconds 类型
- `lib/prompt_builders.py` — build_storyboard_suffix 参数化
- `lib/prompt_builders_script.py` — Prompt 动态注入时长和横竖屏
- `lib/script_generator.py` — 读取项目配置传入 Prompt 构建器
- `lib/project_manager.py` — create_project_metadata 新增 aspect_ratio
- `server/routers/projects.py` — CreateProjectRequest 新增字段、移除修改限制
- `server/routers/generate.py` — 时长默认值改为 None
- `server/services/generation_tasks.py` — get_aspect_ratio 简化、时长回退逻辑
- `agent_runtime_profile/.claude/skills/generate-video/` — 脚本 + SKILL.md

### 前端
- `frontend/src/types/script.ts` — DurationSeconds 类型
- `frontend/src/types/project.ts` — ProjectData 新增字段
- `frontend/src/components/canvas/timeline/SegmentCard.tsx` — 动态时长选项
- `frontend/src/components/canvas/timeline/TimelineCanvas.tsx` — 移除 content_mode 推导
- `frontend/src/api.ts` — 移除修改限制
- 项目创建/设置相关组件 — 新增选择器

### 数据库
- 1 个 Alembic 迁移（CustomProviderModel.supported_durations）
