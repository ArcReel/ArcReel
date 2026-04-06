# 视频时长与横竖屏可配置化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将视频时长和横竖屏从硬编码改为动态可配置，时长由视频模型能力决定，横竖屏与 content_mode 完全解耦。

**Architecture:** 在现有 `ModelInfo` 上扩展 `supported_durations` 和 `duration_resolution_constraints` 字段，`CustomProviderModel` ORM 新增 `supported_durations` 列。项目级新增 `aspect_ratio`（顶层字符串）和 `default_duration` 字段。Prompt 构建器和 Agent 脚本动态注入时长/横竖屏参数。

**Tech Stack:** Python (Pydantic, SQLAlchemy, FastAPI, Alembic), TypeScript (React, Tailwind)

**Spec:** `docs/superpowers/specs/2026-04-06-video-duration-selection-design.md`

---

## File Map

| 文件 | 操作 | 职责 |
|------|------|------|
| `lib/config/registry.py` | Modify | ModelInfo 扩展 + 各供应商视频模型时长声明 |
| `lib/db/models/custom_provider.py` | Modify | CustomProviderModel 新增 supported_durations 列 |
| `alembic/versions/xxxx_add_supported_durations.py` | Create | DB 迁移 |
| `server/routers/providers.py` | Modify | ModelInfoResponse 新增字段 |
| `server/routers/custom_providers.py` | Modify | ModelResponse/ModelInput 新增字段 |
| `lib/script_models.py` | Modify | 移除 DurationSeconds 类型，改为 int + Field 校验 |
| `lib/project_manager.py` | Modify | create_project_metadata 新增 aspect_ratio |
| `server/routers/projects.py` | Modify | CreateProjectRequest 新增 aspect_ratio，UpdateProjectRequest 放开 aspect_ratio |
| `server/services/generation_tasks.py` | Modify | get_aspect_ratio 简化，时长回退逻辑 |
| `server/routers/generate.py` | Modify | GenerateVideoRequest.duration_seconds 默认值改 None |
| `lib/prompt_builders.py` | Modify | build_storyboard_suffix 接收 aspect_ratio 参数 |
| `lib/prompt_builders_script.py` | Modify | 动态注入时长和横竖屏 |
| `lib/script_generator.py` | Modify | 传入时长/横竖屏参数到 Prompt 构建器 |
| `agent_runtime_profile/.claude/skills/generate-video/scripts/generate_video.py` | Modify | validate_duration 动态化 |
| `agent_runtime_profile/.claude/skills/generate-video/SKILL.md` | Modify | 同步时长描述 |
| `agent_runtime_profile/.claude/references/content-modes.md` | Modify | 同步时长/横竖屏描述 |
| `agent_runtime_profile/CLAUDE.md` | Modify | 同步视频规格描述 |
| `frontend/src/types/script.ts` | Modify | DurationSeconds 类型改 number |
| `frontend/src/types/project.ts` | Modify | ProjectData 新增字段 |
| `frontend/src/api.ts` | Modify | createProject 新增 aspectRatio，updateProject 放开 aspect_ratio |
| `frontend/src/components/pages/CreateProjectModal.tsx` | Modify | 新增横竖屏选择器 |
| `frontend/src/components/canvas/timeline/SegmentCard.tsx` | Modify | 动态时长选项 |
| `frontend/src/components/canvas/timeline/TimelineCanvas.tsx` | Modify | 直接读 project.aspect_ratio |
| `tests/test_script_models.py` | Modify | 更新时长验证测试 |
| `tests/test_prompt_builders.py` | Modify | 更新 storyboard_suffix 测试 |
| `tests/test_prompt_builders_script.py` | Modify | 更新 Prompt 构建测试 |
| `tests/test_generation_tasks_service.py` | Modify | 更新 get_aspect_ratio 测试 |

---

### Task 1: ModelInfo 扩展 + 供应商时长声明

**Files:**
- Modify: `lib/config/registry.py`
- Test: `tests/test_config_registry.py` (create)

- [ ] **Step 1: 写 ModelInfo 扩展的测试**

```python
# tests/test_config_registry.py
from lib.config.registry import PROVIDER_REGISTRY, ModelInfo


class TestModelInfoDurations:
    def test_video_models_have_supported_durations(self):
        """所有预置视频模型必须声明 supported_durations。"""
        for provider_id, meta in PROVIDER_REGISTRY.items():
            for model_id, model_info in meta.models.items():
                if model_info.media_type == "video":
                    assert len(model_info.supported_durations) > 0, (
                        f"{provider_id}/{model_id} 是视频模型但未声明 supported_durations"
                    )

    def test_non_video_models_have_empty_durations(self):
        """非视频模型的 supported_durations 应为空列表。"""
        for provider_id, meta in PROVIDER_REGISTRY.items():
            for model_id, model_info in meta.models.items():
                if model_info.media_type != "video":
                    assert model_info.supported_durations == [], (
                        f"{provider_id}/{model_id} 不是视频模型但有 supported_durations"
                    )

    def test_aistudio_veo_has_resolution_constraints(self):
        """AI Studio Veo 模型在 1080p 下只支持 8s。"""
        meta = PROVIDER_REGISTRY["gemini-aistudio"]
        for model_id, model_info in meta.models.items():
            if model_info.media_type == "video":
                assert "1080p" in model_info.duration_resolution_constraints
                assert model_info.duration_resolution_constraints["1080p"] == [8]

    def test_vertex_veo_has_no_resolution_constraints(self):
        """Vertex Veo 模型无分辨率约束。"""
        meta = PROVIDER_REGISTRY["gemini-vertex"]
        for model_id, model_info in meta.models.items():
            if model_info.media_type == "video":
                assert model_info.duration_resolution_constraints == {}

    def test_model_info_default_values(self):
        """ModelInfo 新字段的默认值。"""
        mi = ModelInfo(display_name="test", media_type="text", capabilities=[])
        assert mi.supported_durations == []
        assert mi.duration_resolution_constraints == {}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_config_registry.py -v`
Expected: FAIL（ModelInfo 没有 supported_durations 字段）

- [ ] **Step 3: 实现 ModelInfo 扩展和供应商时长声明**

在 `lib/config/registry.py` 中：

1. `ModelInfo` dataclass 新增两个字段：

```python
@dataclass(frozen=True)
class ModelInfo:
    display_name: str
    media_type: str
    capabilities: list[str]
    default: bool = False
    supported_durations: list[int] = field(default_factory=list)
    duration_resolution_constraints: dict[str, list[int]] = field(default_factory=dict)
```

2. 为所有视频模型添加 `supported_durations` 和 `duration_resolution_constraints`：

AI Studio 视频模型（3 个）:
```python
supported_durations=[4, 6, 8],
duration_resolution_constraints={"1080p": [8]},
```

Vertex 视频模型（2 个）:
```python
supported_durations=[4, 6, 8],
```

Ark 视频模型:
- seedance-1-5-pro: `supported_durations=list(range(4, 13))`（4-12）
- seedance-2-0: `supported_durations=list(range(4, 16))`（4-15）
- seedance-2-0-fast: `supported_durations=list(range(4, 16))`（4-15）

Grok 视频模型:
```python
supported_durations=list(range(1, 16)),  # 1-15
```

OpenAI 视频模型（sora-2, sora-2-pro）:
```python
supported_durations=[4, 8, 12],
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_config_registry.py -v`
Expected: ALL PASS

- [ ] **Step 5: 提交**

```bash
git add lib/config/registry.py tests/test_config_registry.py
git commit -m "feat: ModelInfo 扩展 supported_durations 和 duration_resolution_constraints"
```

---

### Task 2: CustomProviderModel ORM + Alembic 迁移

**Files:**
- Modify: `lib/db/models/custom_provider.py`
- Create: `alembic/versions/xxxx_add_supported_durations.py`
- Modify: `server/routers/custom_providers.py`

- [ ] **Step 1: CustomProviderModel 新增列**

在 `lib/db/models/custom_provider.py` 的 `CustomProviderModel` 类末尾（`currency` 之后）新增：

```python
supported_durations: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list[int]
```

- [ ] **Step 2: 生成 Alembic 迁移**

Run: `uv run alembic revision --autogenerate -m "add supported_durations to custom_provider_model"`

- [ ] **Step 3: 检查生成的迁移文件**

确认 upgrade 包含 `op.add_column("custom_provider_model", sa.Column("supported_durations", sa.Text(), nullable=True))`。

- [ ] **Step 4: 运行迁移**

Run: `uv run alembic upgrade head`

- [ ] **Step 5: 更新 custom_providers API 的 ModelInput/ModelResponse**

在 `server/routers/custom_providers.py` 中：

`ModelInput` 类新增：
```python
supported_durations: list[int] | None = None
```

`ModelResponse` 类新增：
```python
supported_durations: list[int] | None = None
```

`_model_to_response` 函数新增字段映射：
```python
def _model_to_response(m) -> ModelResponse:
    durations = None
    if m.supported_durations:
        import json
        durations = json.loads(m.supported_durations)
    return ModelResponse(
        ...,  # 现有字段保持不变
        supported_durations=durations,
    )
```

在保存自定义模型的地方，将 `list[int]` 序列化为 JSON 字符串存入 DB。搜索 `CustomProviderModel(` 构造和批量替换逻辑，新增：
```python
supported_durations=json.dumps(model_input.supported_durations) if model_input.supported_durations else None,
```

- [ ] **Step 6: 更新 providers API 的 ModelInfoResponse**

在 `server/routers/providers.py` 中，`ModelInfoResponse` 新增：
```python
class ModelInfoResponse(BaseModel):
    display_name: str
    media_type: str
    capabilities: list[str]
    default: bool
    supported_durations: list[int] = []
    duration_resolution_constraints: dict[str, list[int]] = {}
```

由于 `ConfigService.get_all_providers_status` 中用 `asdict(mi)` 转字典，新字段会自动包含在 API 响应中。

- [ ] **Step 7: 提交**

```bash
git add lib/db/models/custom_provider.py alembic/versions/ server/routers/custom_providers.py server/routers/providers.py
git commit -m "feat: CustomProviderModel 新增 supported_durations 列 + API 响应扩展"
```

---

### Task 3: DurationSeconds 类型重构

**Files:**
- Modify: `lib/script_models.py`
- Modify: `tests/test_script_models.py`

- [ ] **Step 1: 更新测试**

在 `tests/test_script_models.py` 中：

1. 移除 `test_invalid_duration_raises_validation_error` 测试（不再做 Pydantic 层硬编码校验）
2. 新增范围校验测试：

```python
def test_duration_accepts_any_positive_int_within_range(self):
    """duration_seconds 接受 1-60 范围内任意整数。"""
    segment = NarrationSegment(
        segment_id="E1S01",
        episode=1,
        duration_seconds=10,  # 之前会被 DurationSeconds 拒绝
        novel_text="原文",
        characters_in_segment=["姜月茴"],
        image_prompt=ImagePrompt(
            scene="场景",
            composition=Composition(shot_type="Medium Shot", lighting="暖光", ambiance="薄雾"),
        ),
        video_prompt=VideoPrompt(action="转身", camera_motion="Static", ambiance_audio="风声"),
    )
    assert segment.duration_seconds == 10

def test_duration_rejects_out_of_range(self):
    """duration_seconds 拒绝范围外的值。"""
    with pytest.raises(ValidationError):
        NarrationSegment(
            segment_id="E1S01",
            episode=1,
            duration_seconds=0,
            novel_text="原文",
            characters_in_segment=["姜月茴"],
            image_prompt=ImagePrompt(
                scene="场景",
                composition=Composition(shot_type="Medium Shot", lighting="暖光", ambiance="薄雾"),
            ),
            video_prompt=VideoPrompt(action="转身", camera_motion="Static", ambiance_audio="风声"),
        )
    with pytest.raises(ValidationError):
        NarrationSegment(
            segment_id="E1S01",
            episode=1,
            duration_seconds=61,
            novel_text="原文",
            characters_in_segment=["姜月茴"],
            image_prompt=ImagePrompt(
                scene="场景",
                composition=Composition(shot_type="Medium Shot", lighting="暖光", ambiance="薄雾"),
            ),
            video_prompt=VideoPrompt(action="转身", camera_motion="Static", ambiance_audio="风声"),
        )

def test_drama_scene_default_duration_is_8(self):
    """DramaScene 的默认 duration_seconds 仍为 8。"""
    scene = DramaScene(
        scene_id="E1S01",
        characters_in_scene=["姜月茴"],
        image_prompt=ImagePrompt(
            scene="场景",
            composition=Composition(shot_type="Medium Shot", lighting="暖光", ambiance="薄雾"),
        ),
        video_prompt=VideoPrompt(action="前进", camera_motion="Static", ambiance_audio="雨声"),
    )
    assert scene.duration_seconds == 8
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_script_models.py -v`
Expected: `test_duration_accepts_any_positive_int_within_range` FAIL（DurationSeconds 拒绝 10）

- [ ] **Step 3: 重构 script_models.py**

在 `lib/script_models.py` 中：

1. 删除整个 `DurationSeconds` 类（第 16-38 行）
2. 移除不再需要的 import：`GetCoreSchemaHandler`, `GetJsonSchemaHandler`, `JsonSchemaValue`, `core_schema`
3. 修改 `NarrationSegment.duration_seconds`:
```python
duration_seconds: int = Field(ge=1, le=60, description="片段时长（秒）")
```
4. 修改 `DramaScene.duration_seconds`:
```python
duration_seconds: int = Field(default=8, ge=1, le=60, description="场景时长（秒）")
```
5. 修改 `DramaScene.image_prompt` 的 Field description（移除硬编码的"16:9 横屏"）：
```python
image_prompt: ImagePrompt = Field(description="分镜图生成提示词")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_script_models.py -v`
Expected: ALL PASS

- [ ] **Step 5: 运行全量测试确认无回归**

Run: `uv run python -m pytest -x`
Expected: 无新增失败（可能有 test_prompt_builders_script.py 的 `16:9` 断言失败，后续 Task 修复）

- [ ] **Step 6: 提交**

```bash
git add lib/script_models.py tests/test_script_models.py
git commit -m "refactor: 移除 DurationSeconds 硬编码类型，改为 int + Field 范围校验"
```

---

### Task 4: Aspect Ratio 解耦 — 后端

**Files:**
- Modify: `lib/project_manager.py:964-1000`
- Modify: `server/routers/projects.py:58-63, 418-429`
- Modify: `server/services/generation_tasks.py:299-311`
- Modify: `server/routers/generate.py:49`
- Test: `tests/test_generation_tasks_service.py`

- [ ] **Step 1: 写 get_aspect_ratio 新逻辑的测试**

在 `tests/test_generation_tasks_service.py` 中新增测试类（文件末尾）：

```python
class TestGetAspectRatio:
    def test_reads_top_level_aspect_ratio(self):
        project = {"aspect_ratio": "16:9", "content_mode": "narration"}
        assert generation_tasks.get_aspect_ratio(project, "videos") == "16:9"
        assert generation_tasks.get_aspect_ratio(project, "storyboards") == "16:9"

    def test_fallback_to_content_mode_narration(self):
        project = {"content_mode": "narration"}
        assert generation_tasks.get_aspect_ratio(project, "videos") == "9:16"

    def test_fallback_to_content_mode_drama(self):
        project = {"content_mode": "drama"}
        assert generation_tasks.get_aspect_ratio(project, "videos") == "16:9"

    def test_characters_always_3_4(self):
        project = {"aspect_ratio": "16:9"}
        assert generation_tasks.get_aspect_ratio(project, "characters") == "3:4"

    def test_clues_always_16_9(self):
        project = {"aspect_ratio": "9:16"}
        assert generation_tasks.get_aspect_ratio(project, "clues") == "16:9"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_generation_tasks_service.py::TestGetAspectRatio -v`
Expected: `test_reads_top_level_aspect_ratio` FAIL（当前逻辑把 aspect_ratio 当 dict 处理）

- [ ] **Step 3: 实现 get_aspect_ratio 新逻辑**

在 `server/services/generation_tasks.py` 中替换 `get_aspect_ratio`：

```python
def get_aspect_ratio(project: dict, resource_type: str) -> str:
    if resource_type == "characters":
        return "3:4"
    if resource_type == "clues":
        return "16:9"
    # 优先读顶层字段；缺失时按 content_mode 推导（向后兼容）
    if "aspect_ratio" in project and isinstance(project["aspect_ratio"], str):
        return project["aspect_ratio"]
    return "9:16" if project.get("content_mode", "narration") == "narration" else "16:9"
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_generation_tasks_service.py::TestGetAspectRatio -v`
Expected: ALL PASS

- [ ] **Step 5: 更新项目创建 API**

在 `server/routers/projects.py`：

1. `CreateProjectRequest` 新增字段：
```python
class CreateProjectRequest(BaseModel):
    name: str | None = None
    title: str | None = None
    style: str | None = ""
    content_mode: str | None = "narration"
    aspect_ratio: str | None = "9:16"
    default_duration: int | None = None
```

2. `create_project` 端点传入新参数：
```python
project = manager.create_project_metadata(
    project_name,
    title or manual_name,
    req.style,
    req.content_mode,
    aspect_ratio=req.aspect_ratio,
    default_duration=req.default_duration,
)
```

3. `UpdateProjectRequest` 新增字段：
```python
class UpdateProjectRequest(BaseModel):
    ...
    aspect_ratio: str | None = None
    default_duration: int | None = None  # 新增
```

4. `update_project` 端点：将 `aspect_ratio` 限制改为只禁止 `content_mode`：
```python
if req.content_mode is not None:
    raise HTTPException(
        status_code=400,
        detail="项目创建后不支持修改 content_mode",
    )
```

在后续的字段更新逻辑中增加对 `aspect_ratio` 和 `default_duration` 的处理：
```python
if "aspect_ratio" in req.model_fields_set and req.aspect_ratio is not None:
    project["aspect_ratio"] = req.aspect_ratio
if "default_duration" in req.model_fields_set:
    if req.default_duration is None:
        project.pop("default_duration", None)
    else:
        project["default_duration"] = req.default_duration
```

- [ ] **Step 6: 更新 project_manager.create_project_metadata**

在 `lib/project_manager.py` 中：

```python
def create_project_metadata(
    self,
    project_name: str,
    title: str | None = None,
    style: str | None = None,
    content_mode: str = "narration",
    aspect_ratio: str = "9:16",
    default_duration: int | None = None,
) -> dict:
    ...
    project = {
        "title": project_title or project_name,
        "content_mode": content_mode,
        "aspect_ratio": aspect_ratio,
        "style": style or "",
        "episodes": [],
        "characters": {},
        "clues": {},
        "metadata": {
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        },
    }
    if default_duration is not None:
        project["default_duration"] = default_duration
    ...
```

- [ ] **Step 7: 更新 GenerateVideoRequest 默认值**

在 `server/routers/generate.py` 中：
```python
class GenerateVideoRequest(BaseModel):
    prompt: str | dict
    script_file: str
    duration_seconds: int | None = None  # 改为 None，由服务层解析
    seed: int | None = None
```

在 `server/services/generation_tasks.py` 的 `execute_video_task` 中更新时长回退逻辑：
```python
duration_seconds = payload.get("duration_seconds") or project.get("default_duration") or 4
```

- [ ] **Step 8: 运行全量测试**

Run: `uv run python -m pytest -x`
Expected: PASS

- [ ] **Step 9: 提交**

```bash
git add lib/project_manager.py server/routers/projects.py server/routers/generate.py server/services/generation_tasks.py tests/test_generation_tasks_service.py
git commit -m "feat: aspect_ratio 与 content_mode 解耦，项目级 default_duration 支持"
```

---

### Task 5: Prompt 构建器动态化

**Files:**
- Modify: `lib/prompt_builders.py:134-149`
- Modify: `lib/prompt_builders_script.py`
- Modify: `lib/script_generator.py:84-103, 145-162`
- Modify: `tests/test_prompt_builders.py`
- Modify: `tests/test_prompt_builders_script.py`

- [ ] **Step 1: 更新 Prompt 构建器测试**

在 `tests/test_prompt_builders.py` 中替换 `test_build_storyboard_suffix`：

```python
def test_build_storyboard_suffix_by_aspect_ratio(self):
    assert build_storyboard_suffix(aspect_ratio="9:16") == "竖屏构图。"
    assert build_storyboard_suffix(aspect_ratio="16:9") == "横屏构图。"
    # 向后兼容：不传 aspect_ratio 时默认竖屏
    assert build_storyboard_suffix() == "竖屏构图。"
```

在 `tests/test_prompt_builders_script.py` 中，删除旧测试 `test_build_narration_prompt_contains_constraints_and_inputs` 和 `test_build_drama_prompt_mentions_16_9_and_scene_fields`，替换为以下新测试：

```python
def test_build_narration_prompt_contains_dynamic_durations(self):
    prompt = build_narration_prompt(
        project_overview={"synopsis": "故事", "genre": "悬疑", "theme": "真相", "world_setting": "古代"},
        style="古风",
        style_description="cinematic",
        characters={"姜月茴": {}},
        clues={"玉佩": {}},
        segments_md="E1S01 | 文本",
        supported_durations=[4, 6, 8],
        default_duration=4,
        aspect_ratio="9:16",
    )
    assert "4, 6, 8" in prompt
    assert "默认使用 4 秒" in prompt

def test_build_narration_prompt_auto_duration(self):
    prompt = build_narration_prompt(
        project_overview={"synopsis": "故事", "genre": "悬疑", "theme": "真相", "world_setting": "古代"},
        style="古风",
        style_description="cinematic",
        characters={"姜月茴": {}},
        clues={"玉佩": {}},
        segments_md="E1S01 | 文本",
        supported_durations=[5, 10],
        default_duration=None,
        aspect_ratio="9:16",
    )
    assert "5, 10" in prompt
    assert "根据内容节奏自行决定" in prompt

def test_build_drama_prompt_uses_dynamic_aspect_ratio(self):
    prompt = build_drama_prompt(
        project_overview={"synopsis": "动作", "genre": "动作", "theme": "成长", "world_setting": "近未来"},
        style="赛博",
        style_description="high contrast",
        characters={"林": {}},
        clues={"芯片": {}},
        scenes_md="E1S01 | 追逐",
        supported_durations=[4, 8, 12],
        default_duration=8,
        aspect_ratio="9:16",
    )
    # 传入竖屏时不应出现 "16:9 横屏构图"
    assert "16:9 横屏构图" not in prompt
    assert "竖屏构图" in prompt

def test_build_drama_prompt_landscape(self):
    prompt = build_drama_prompt(
        project_overview={"synopsis": "动作", "genre": "动作", "theme": "成长", "world_setting": "近未来"},
        style="赛博",
        style_description="high contrast",
        characters={"林": {}},
        clues={"芯片": {}},
        scenes_md="E1S01 | 追逐",
        supported_durations=[4, 6, 8],
        default_duration=8,
        aspect_ratio="16:9",
    )
    assert "横屏构图" in prompt
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_prompt_builders.py tests/test_prompt_builders_script.py -v`
Expected: FAIL（函数签名不匹配）

- [ ] **Step 3: 实现 build_storyboard_suffix 参数化**

在 `lib/prompt_builders.py` 中替换 `build_storyboard_suffix`：

```python
def build_storyboard_suffix(content_mode: str = "narration", *, aspect_ratio: str | None = None) -> str:
    """
    获取分镜图 Prompt 后缀

    优先使用 aspect_ratio 参数；若未传，按 content_mode 推导（向后兼容）。
    """
    if aspect_ratio is None:
        ratio = "9:16" if content_mode == "narration" else "16:9"
    else:
        ratio = aspect_ratio
    if ratio == "9:16":
        return "竖屏构图。"
    elif ratio == "16:9":
        return "横屏构图。"
    return ""
```

- [ ] **Step 4: 实现 Prompt 构建器动态化**

在 `lib/prompt_builders_script.py` 中：

1. 新增辅助函数：
```python
def _format_duration_constraint(supported_durations: list[int], default_duration: int | None) -> str:
    """根据参数生成时长约束描述。"""
    durations_str = ", ".join(str(d) for d in supported_durations)
    if default_duration is not None:
        return f"时长：从 [{durations_str}] 秒中选择，默认使用 {default_duration} 秒"
    return f"时长：从 [{durations_str}] 秒中选择，根据内容节奏自行决定"


def _format_aspect_ratio_desc(aspect_ratio: str) -> str:
    """根据宽高比返回构图描述。"""
    if aspect_ratio == "9:16":
        return "竖屏构图"
    elif aspect_ratio == "16:9":
        return "横屏构图"
    return f"{aspect_ratio} 构图"
```

2. `build_narration_prompt` 签名新增参数：
```python
def build_narration_prompt(
    project_overview: dict,
    style: str,
    style_description: str,
    characters: dict,
    clues: dict,
    segments_md: str,
    supported_durations: list[int] | None = None,
    default_duration: int | None = None,
    aspect_ratio: str = "9:16",
) -> str:
```

在 prompt 模板中替换硬编码：
- `"时长：4、6 或 8 秒"` → `{_format_duration_constraint(supported_durations or [4, 6, 8], default_duration)}`
- `"确保在指定时长（4/6/8秒）内可完成"` → `"确保在指定时长内可完成"`
- `"使用片段表中的时长（4、6 或 8）"` → `"使用片段表中的时长"`

3. `build_drama_prompt` 同理：
```python
def build_drama_prompt(
    project_overview: dict,
    style: str,
    style_description: str,
    characters: dict,
    clues: dict,
    scenes_md: str,
    supported_durations: list[int] | None = None,
    default_duration: int | None = None,
    aspect_ratio: str = "16:9",
) -> str:
```

在 prompt 模板中替换：
- `"时长：4、6 或 8 秒（默认 8 秒）"` → `{_format_duration_constraint(supported_durations or [4, 6, 8], default_duration)}`
- `"16:9 横屏构图"` → `{_format_aspect_ratio_desc(aspect_ratio)}`
- `"确保在指定时长（4/6/8秒）内可完成"` → `"确保在指定时长内可完成"`
- `"使用场景表中的时长（4、6 或 8），默认为 8"` → `"使用场景表中的时长"`
- `"适合 16:9 横屏动画呈现"` → `f"适合{_format_aspect_ratio_desc(aspect_ratio)}动画呈现"`

- [ ] **Step 5: 更新 script_generator.py 调用方**

在 `lib/script_generator.py` 中，`generate` 和 `build_prompt` 方法里两处调用 `build_narration_prompt` / `build_drama_prompt` 的地方，都新增三个参数：

```python
supported_durations=self.project_json.get("_supported_durations"),
default_duration=self.project_json.get("default_duration"),
aspect_ratio=self._resolve_aspect_ratio(),
```

新增辅助方法：
```python
def _resolve_aspect_ratio(self) -> str:
    """解析项目的 aspect_ratio，向后兼容。"""
    if "aspect_ratio" in self.project_json and isinstance(self.project_json["aspect_ratio"], str):
        return self.project_json["aspect_ratio"]
    return "9:16" if self.content_mode == "narration" else "16:9"
```

注意：`_supported_durations` 是一个由调用方注入的临时字段（由 generation_tasks 服务在调用脚本生成前设置），或者 ScriptGenerator 在 `__init__` 中从全局配置中解析。这里先用项目字段，如果不存在则传 `None`（Prompt 构建器会 fallback 到 `[4, 6, 8]`）。

- [ ] **Step 6: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_prompt_builders.py tests/test_prompt_builders_script.py -v`
Expected: ALL PASS

- [ ] **Step 7: 运行全量测试**

Run: `uv run python -m pytest -x`
Expected: PASS

- [ ] **Step 8: 提交**

```bash
git add lib/prompt_builders.py lib/prompt_builders_script.py lib/script_generator.py tests/test_prompt_builders.py tests/test_prompt_builders_script.py
git commit -m "feat: Prompt 构建器动态注入时长和横竖屏参数"
```

---

### Task 6: Agent 脚本适配

**Files:**
- Modify: `agent_runtime_profile/.claude/skills/generate-video/scripts/generate_video.py:101-121, 234, 477`
- Modify: `agent_runtime_profile/.claude/skills/generate-video/SKILL.md`
- Modify: `agent_runtime_profile/.claude/references/content-modes.md`
- Modify: `agent_runtime_profile/CLAUDE.md`

- [ ] **Step 1: 重构 validate_duration**

在 `agent_runtime_profile/.claude/skills/generate-video/scripts/generate_video.py` 中替换 `validate_duration`：

```python
DEFAULT_DURATIONS_FALLBACK = [4, 8]


def get_supported_durations(project: dict) -> list[int]:
    """从项目配置获取当前视频模型支持的时长列表。"""
    durations = project.get("_supported_durations")
    if durations and isinstance(durations, list):
        return durations
    return DEFAULT_DURATIONS_FALLBACK


def validate_duration(duration: int, supported_durations: list[int] | None = None) -> str:
    """
    验证并返回有效的时长参数。

    Args:
        duration: 输入的时长（秒）
        supported_durations: 当前视频模型支持的时长列表

    Returns:
        有效的时长字符串
    """
    valid = supported_durations or DEFAULT_DURATIONS_FALLBACK
    if duration in valid:
        return str(duration)
    # 向上取整到最近的有效值
    for d in sorted(valid):
        if d >= duration:
            return str(d)
    return str(max(valid))
```

- [ ] **Step 2: 更新 _build_video_specs 和 generate_scene_video 中的默认时长**

在 `_build_video_specs` 函数（约第 234 行）：
```python
# 替换
default_duration = 4 if content_mode == "narration" else 8
# 为
default_duration = project.get("default_duration") or (4 if content_mode == "narration" else 8)
```

在 `generate_scene_video` 函数（约第 477 行）：
```python
# 替换
default_duration = 4 if content_mode == "narration" else 8
duration = item.get("duration_seconds", default_duration)
duration_str = validate_duration(duration)
# 为
default_duration = project.get("default_duration") or (4 if content_mode == "narration" else 8)
duration = item.get("duration_seconds", default_duration)
supported = get_supported_durations(project)
duration_str = validate_duration(duration, supported)
```

需要确保 `project` 变量在这些函数中可用——检查函数签名是否已包含 project 参数，如果没有则需要传入。

- [ ] **Step 3: 更新 SKILL.md**

在 `agent_runtime_profile/.claude/skills/generate-video/SKILL.md` 中，将关于 Veo 特定时长的描述改为通用描述：

```markdown
> 画面比例、时长等规格由项目配置和视频模型能力决定，脚本自动处理。
```

- [ ] **Step 4: 更新 content-modes.md**

在 `agent_runtime_profile/.claude/references/content-modes.md` 中更新表格：

```markdown
| 维度 | 说书+画面（narration，默认） | 剧集动画（drama） |
|------|---------------------------|-----------------|
| 数据结构 | `segments` 数组 | `scenes` 数组 |
| 画面比例 | 项目配置（默认 9:16 竖屏） | 项目配置（默认 16:9 横屏） |
| 默认时长 | 项目配置（默认 4 秒/片段） | 项目配置（默认 8 秒/场景） |
| 时长可选 | 由视频模型能力决定 | 由视频模型能力决定 |
```

- [ ] **Step 5: 更新 agent_runtime_profile/CLAUDE.md**

在视频规格部分，将硬编码描述改为动态描述：

```markdown
### 视频规格
- **视频比例**：由项目 `aspect_ratio` 配置决定，无需在 prompt 中指定
  - 说书+画面模式默认：**9:16 竖屏**
  - 剧集动画模式默认：16:9 横屏
- **单片段/场景时长**：由视频模型能力和项目 `default_duration` 配置决定
  - 说书+画面模式默认：**4 秒**
  - 剧集动画模式默认：8 秒
```

- [ ] **Step 6: 提交**

```bash
git add agent_runtime_profile/
git commit -m "feat: Agent 脚本适配动态时长验证和项目配置"
```

---

### Task 7: 前端类型和 API 层

**Files:**
- Modify: `frontend/src/types/script.ts:36`
- Modify: `frontend/src/types/project.ts:32-37, 78-101`
- Modify: `frontend/src/api.ts:262-298`

- [ ] **Step 1: 更新 TypeScript 类型**

在 `frontend/src/types/script.ts` 中：
```typescript
// 替换第 36 行
export type DurationSeconds = 4 | 6 | 8;
// 为
export type DurationSeconds = number;
```

在 `frontend/src/types/project.ts` 中：

1. 保留 `AspectRatio` 接口不变（向后兼容旧项目的 dict 形式）
2. `ProjectData` 接口新增字段，修改 `aspect_ratio` 类型为联合类型：
```typescript
export interface ProjectData {
  ...
  aspect_ratio?: string | AspectRatio;  // 新项目为 string，旧项目可能为 dict
  default_duration?: number | null;     // 新增
  ...
}
```

- [ ] **Step 2: 更新 API 层**

在 `frontend/src/api.ts` 中：

1. `createProject` 新增 `aspectRatio` 参数：
```typescript
static async createProject(
  title: string,
  style: string = "",
  contentMode: string = "narration",
  aspectRatio: string = "9:16",
): Promise<{ success: boolean; name: string; project: ProjectData }> {
  return this.request("/projects", {
    method: "POST",
    body: JSON.stringify({
      title,
      style,
      content_mode: contentMode,
      aspect_ratio: aspectRatio,
    }),
  });
}
```

2. `updateProject` 修改限制——只禁止 `content_mode`，允许 `aspect_ratio`：
```typescript
static async updateProject(
  name: string,
  updates: Partial<ProjectData>
): Promise<{ success: boolean; project: ProjectData }> {
  if ("content_mode" in updates) {
    throw new Error("项目创建后不支持修改 content_mode");
  }
  return this.request(`/projects/${encodeURIComponent(name)}`, {
    method: "PATCH",
    body: JSON.stringify(updates),
  });
}
```

- [ ] **Step 3: 运行前端类型检查**

Run: `cd frontend && pnpm check`
Expected: 可能有类型错误需修复（`aspect_ratio` 类型变化影响引用处）

- [ ] **Step 4: 修复类型错误（如有）**

根据 `pnpm check` 报错逐一修复。主要是 `aspect_ratio` 原来引用 `AspectRatio` dict 形式的地方需要适配联合类型。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/types/ frontend/src/api.ts
git commit -m "feat: 前端类型和 API 层适配动态时长和 aspect_ratio"
```

---

### Task 8: 前端 UI — 项目创建横竖屏选择

**Files:**
- Modify: `frontend/src/components/pages/CreateProjectModal.tsx`

- [ ] **Step 1: 新增横竖屏选择器**

在 `frontend/src/components/pages/CreateProjectModal.tsx` 中：

1. 新增 state：
```typescript
const [aspectRatio, setAspectRatio] = useState<"9:16" | "16:9">("9:16");
```

2. 在 content_mode 选择器之后、style 选择器之前，新增 Aspect Ratio 选择器：
```tsx
{/* Aspect Ratio */}
<div>
  <label className="block text-sm font-medium text-gray-400 mb-1">
    画面比例
  </label>
  <div className="flex gap-3">
    <label className={`flex-1 cursor-pointer rounded-lg border px-3 py-2 text-center text-sm transition-colors ${
      aspectRatio === "9:16"
        ? "border-indigo-500 bg-indigo-500/10 text-indigo-300"
        : "border-gray-700 bg-gray-800 text-gray-400 hover:border-gray-600"
    }`}>
      <input
        type="radio"
        name="aspectRatio"
        value="9:16"
        checked={aspectRatio === "9:16"}
        onChange={() => setAspectRatio("9:16")}
        className="sr-only"
      />
      竖屏 9:16
    </label>
    <label className={`flex-1 cursor-pointer rounded-lg border px-3 py-2 text-center text-sm transition-colors ${
      aspectRatio === "16:9"
        ? "border-indigo-500 bg-indigo-500/10 text-indigo-300"
        : "border-gray-700 bg-gray-800 text-gray-400 hover:border-gray-600"
    }`}>
      <input
        type="radio"
        name="aspectRatio"
        value="16:9"
        checked={aspectRatio === "16:9"}
        onChange={() => setAspectRatio("16:9")}
        className="sr-only"
      />
      横屏 16:9
    </label>
  </div>
</div>
```

3. `handleSubmit` 传入 `aspectRatio`：
```typescript
const response = await API.createProject(title.trim(), style, contentMode, aspectRatio);
```

- [ ] **Step 2: 运行前端构建确认无错误**

Run: `cd frontend && pnpm build`
Expected: BUILD SUCCESS

- [ ] **Step 3: 提交**

```bash
git add frontend/src/components/pages/CreateProjectModal.tsx
git commit -m "feat: 项目创建表单新增横竖屏选择器"
```

---

### Task 9: 前端 UI — SegmentCard 动态时长选项

**Files:**
- Modify: `frontend/src/components/canvas/timeline/SegmentCard.tsx:164`
- Modify: `frontend/src/components/canvas/timeline/TimelineCanvas.tsx:67-70`

- [ ] **Step 1: SegmentCard 动态时长选项**

在 `frontend/src/components/canvas/timeline/SegmentCard.tsx` 中：

1. 移除硬编码常量：
```typescript
// 删除: const DURATION_OPTIONS = [4, 6, 8];
```

2. `DurationSelector` 组件新增 `durationOptions` prop：
```typescript
function DurationSelector({
  seconds,
  segmentId,
  onUpdatePrompt,
  durationOptions = [4, 6, 8],
}: {
  seconds: number;
  segmentId: string;
  onUpdatePrompt?: (segmentId: string, field: string, value: unknown) => void;
  durationOptions?: number[];
}) {
```

3. 渲染中 `DURATION_OPTIONS.map` 改为 `durationOptions.map`

4. `SegmentCard` 组件新增 `durationOptions` prop 并传递给 `DurationSelector`

- [ ] **Step 2: TimelineCanvas 直接读 project.aspect_ratio**

在 `frontend/src/components/canvas/timeline/TimelineCanvas.tsx` 中替换 aspect_ratio 解析逻辑：

```typescript
// 替换第 67-70 行
const aspectRatio =
  typeof projectData?.aspect_ratio === "string"
    ? projectData.aspect_ratio
    : projectData?.aspect_ratio?.storyboard ??
      (contentMode === "narration" ? "9:16" : "16:9");
```

- [ ] **Step 3: 运行前端构建**

Run: `cd frontend && pnpm build`
Expected: BUILD SUCCESS

- [ ] **Step 4: 提交**

```bash
git add frontend/src/components/canvas/timeline/SegmentCard.tsx frontend/src/components/canvas/timeline/TimelineCanvas.tsx
git commit -m "feat: SegmentCard 动态时长选项 + TimelineCanvas 读取 project.aspect_ratio"
```

---

### Task 10: 全量测试 + lint

**Files:** (no new files)

- [ ] **Step 1: 运行后端全量测试**

Run: `uv run python -m pytest -v`
Expected: ALL PASS

- [ ] **Step 2: 运行 lint + format**

Run: `uv run ruff check . && uv run ruff format .`
Expected: 无错误

- [ ] **Step 3: 运行前端 check**

Run: `cd frontend && pnpm check`
Expected: PASS

- [ ] **Step 4: 修复发现的问题（如有）**

根据测试/lint 报错修复。

- [ ] **Step 5: 最终提交（如有修复）**

```bash
git add -u
git commit -m "fix: 全量测试和 lint 修复"
```

---

## Scope Notes

本计划覆盖核心基础设施和主要 UI 路径。以下属于后续可独立完成的工作：

- **项目设置页面**：修改 `aspect_ratio` 和 `default_duration` 的 UI（后端已支持 PATCH）、修改 aspect_ratio 时的警告提示
- **视频模型切换联动**：切换视频模型时自动重置 `default_duration`（需要前端 store 监听逻辑）
- **SegmentCard 时长选项数据源**：当前 `durationOptions` prop 已就绪，需要从 providers API 或项目 store 中解析当前视频模型的 `supported_durations` 并传入
