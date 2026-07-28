# PR2 · M2 数据模型 + shot_parser 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `lib/` 落地 `ReferenceVideoScript` Pydantic 模型、`shot_parser` 双向解析、`generation_mode` 字段与 `effective_mode()` 辅助、`data_validator` 支持第三种 `content_mode`。纯数据层工作，不碰路由/前端。

**Architecture:** `lib/reference_video/shot_parser.py` 内两组纯函数——`parse_prompt(text) -> (shots, references)` 把用户书写的 prompt 解析为结构化 `Shot[]` 与 `ReferenceResource[]`；`render_prompt_for_backend(text, references)` 把 `@名称` 替换为 `[图N]`。`DataValidator` 对 `content_mode=reference_video` 脚本校验 `video_units`，并校验引用的 character/scene/prop 都在 project.json 对应 bucket 中注册。`effective_mode(project, episode)` 按 episode → project → 默认 `storyboard` 回退。

**Tech Stack:** Python 3.11+ / Pydantic v2 / pytest

## 参考设计

- Roadmap: `docs/superpowers/plans/2026-04-17-reference-to-video-roadmap.md`
- Spec: `docs/superpowers/specs/2026-04-15-reference-to-video-mode-design.md` §4、§4.1-§4.6、§11
- 现有模型：`lib/script_models.py:85-151`
- 现有 validator：`lib/data_validator.py:160-212`
- 资产类型映射：`lib/asset_types.py:10-25`

## 文件结构

### 新增

| 文件 | 职责 |
|---|---|
| `lib/reference_video/__init__.py` | 包 init，re-export `parse_prompt` / `render_prompt_for_backend` |
| `lib/reference_video/shot_parser.py` | prompt ↔ Shot[]/references 双向解析（纯函数） |
| `tests/lib/test_script_models_reference.py` | Pydantic 模型单测 |
| `tests/lib/test_shot_parser.py` | parser 单测 |
| `tests/lib/test_data_validator_reference.py` | 第三种 content_mode 校验 |
| `tests/lib/test_project_manager_effective_mode.py` | `effective_mode()` 回退 |

### 改造

| 文件 | 改造点 |
|---|---|
| `lib/script_models.py` | 新增 `Shot` / `ReferenceResource` / `ReferenceVideoUnit` / `ReferenceVideoScript` |
| `lib/data_validator.py` | `VALID_CONTENT_MODES` 加 `"reference_video"`；新增 `_validate_reference_video_script`；`ALLOWED_ROOT_ENTRIES` 加 `"reference_videos"` |
| `lib/project_manager.py` | 新增模块级 `effective_mode(project, episode)` 函数 |

---

## Task 1：`Shot` + `ReferenceResource` Pydantic 模型

**Files:**
- Modify: `lib/script_models.py`
- Test: `tests/lib/test_script_models_reference.py`

- [ ] **Step 1：写失败测试（Shot）**

创建 `tests/lib/test_script_models_reference.py`：

```python
import pytest
from pydantic import ValidationError

from lib.script_models import ReferenceResource, Shot


def test_shot_valid():
    s = Shot(duration=5, text="中远景，主角推门进酒馆")
    assert s.duration == 5
    assert "酒馆" in s.text


def test_shot_duration_range():
    with pytest.raises(ValidationError):
        Shot(duration=0, text="x")
    with pytest.raises(ValidationError):
        Shot(duration=16, text="x")


def test_reference_resource_valid_types():
    for t in ("character", "scene", "prop"):
        r = ReferenceResource(type=t, name="张三")
        assert r.type == t


def test_reference_resource_rejects_clue():
    with pytest.raises(ValidationError):
        ReferenceResource(type="clue", name="张三")
```

- [ ] **Step 2：运行测试确认失败**

```bash
uv run pytest tests/lib/test_script_models_reference.py -v
```

Expected: FAIL（`Shot` / `ReferenceResource` 未导出）。

- [ ] **Step 3：加模型**

编辑 `lib/script_models.py`，在文件末尾追加：

```python
# ============ 参考生视频模式（Reference Video） ============


class Shot(BaseModel):
    """参考视频单元内的一个镜头。"""

    duration: int = Field(ge=1, le=15, description="该镜头时长（秒）")
    text: str = Field(description="镜头描述，可包含 @角色/@场景/@道具 引用")


class ReferenceResource(BaseModel):
    """参考图引用——只存名称 + 类型，具体路径从 project.json 对应 bucket 读时解析。"""

    type: Literal["character", "scene", "prop"] = Field(description="引用的资源类型")
    name: str = Field(description="角色/场景/道具名称，必须在 project.json 对应 bucket 中已注册")
```

- [ ] **Step 4：运行测试确认通过**

```bash
uv run pytest tests/lib/test_script_models_reference.py -v
```

Expected: 4 PASS。

- [ ] **Step 5：Commit**

```bash
git add lib/script_models.py tests/lib/test_script_models_reference.py
git commit -m "feat(script-models): add Shot and ReferenceResource for reference video mode"
```

---

## Task 2：`ReferenceVideoUnit` + `ReferenceVideoScript`

**Files:**
- Modify: `lib/script_models.py`
- Test: `tests/lib/test_script_models_reference.py`

- [ ] **Step 1：追加失败测试**

追加到 `tests/lib/test_script_models_reference.py`：

```python
from lib.script_models import (
    NovelInfo,
    ReferenceVideoScript,
    ReferenceVideoUnit,
)


def _make_unit(**overrides):
    defaults = dict(
        unit_id="E1U1",
        shots=[Shot(duration=3, text="Shot 1"), Shot(duration=5, text="Shot 2")],
        references=[ReferenceResource(type="character", name="张三")],
        duration_seconds=8,
    )
    defaults.update(overrides)
    return ReferenceVideoUnit(**defaults)


def test_reference_video_unit_minimal():
    u = _make_unit()
    assert u.unit_id == "E1U1"
    assert len(u.shots) == 2
    assert u.duration_seconds == 8
    assert u.duration_override is False
    assert u.transition_to_next == "cut"


def test_reference_video_unit_requires_at_least_one_shot():
    with pytest.raises(ValidationError):
        _make_unit(shots=[])


def test_reference_video_unit_duration_override_flag():
    u = _make_unit(duration_override=True)
    assert u.duration_override is True


def test_reference_video_unit_transition_enum():
    with pytest.raises(ValidationError):
        _make_unit(transition_to_next="wipe")


def test_reference_video_script_valid():
    script = ReferenceVideoScript(
        episode=1,
        title="江湖夜话",
        content_mode="reference_video",
        duration_seconds=8,
        summary="主角闯江湖。",
        novel=NovelInfo(title="江湖行", chapter="第一回"),
        video_units=[_make_unit()],
    )
    assert script.content_mode == "reference_video"
    assert len(script.video_units) == 1


def test_reference_video_script_rejects_wrong_content_mode():
    with pytest.raises(ValidationError):
        ReferenceVideoScript(
            episode=1,
            title="x",
            content_mode="narration",
            summary="x",
            novel=NovelInfo(title="x", chapter="x"),
            video_units=[_make_unit()],
        )
```

- [ ] **Step 2：运行测试确认失败**

```bash
uv run pytest tests/lib/test_script_models_reference.py -v
```

Expected: 6 新增 FAIL。

- [ ] **Step 3：加模型**

编辑 `lib/script_models.py`，在 Task 1 追加的块之后继续追加：

```python
class ReferenceVideoUnit(BaseModel):
    """参考视频单元——一个视频文件的最小生成粒度。"""

    unit_id: str = Field(description="格式 E{集}U{序号}")
    shots: list[Shot] = Field(min_length=1, description="1-4 个 shot")
    references: list[ReferenceResource] = Field(
        default_factory=list,
        description="按顺序决定 [图N] 编号",
    )
    duration_seconds: int = Field(description="派生字段：所有 shot 时长之和")
    duration_override: bool = Field(default=False, description="true 时停止自动派生")
    transition_to_next: Literal["cut", "fade", "dissolve"] = Field(default="cut")
    note: str | None = Field(default=None, description="用户备注")
    generated_assets: GeneratedAssets = Field(default_factory=GeneratedAssets)


class ReferenceVideoScript(BaseModel):
    """参考生视频模式剧集脚本。"""

    episode: int = Field(description="剧集编号")
    title: str = Field(description="剧集标题")
    content_mode: Literal["reference_video"] = Field(default="reference_video")
    duration_seconds: int = Field(default=0, description="总时长（秒）")
    summary: str = Field(description="剧集摘要")
    novel: NovelInfo = Field(description="小说来源信息")
    video_units: list[ReferenceVideoUnit] = Field(description="视频单元列表")
```

- [ ] **Step 4：运行测试确认通过**

```bash
uv run pytest tests/lib/test_script_models_reference.py -v
```

Expected: 10 PASS。

- [ ] **Step 5：Commit**

```bash
git add lib/script_models.py tests/lib/test_script_models_reference.py
git commit -m "feat(script-models): add ReferenceVideoUnit and ReferenceVideoScript"
```

---

## Task 3：`shot_parser.parse_prompt` — 识别 `Shot N (Xs):` 段

**Files:**
- Create: `lib/reference_video/__init__.py`
- Create: `lib/reference_video/shot_parser.py`
- Test: `tests/lib/test_shot_parser.py`

- [ ] **Step 1：写失败测试**

创建 `tests/lib/test_shot_parser.py`：

```python
from lib.reference_video.shot_parser import parse_prompt


def test_parse_single_shot_no_header():
    shots, refs, override = parse_prompt("中景，主角走进房间。")
    assert len(shots) == 1
    assert shots[0].text == "中景，主角走进房间。"
    assert override is True  # 无 header → 单镜头，override 模式
    assert refs == []


def test_parse_multi_shot():
    text = (
        "Shot 1 (3s): 中远景，主角推门进酒馆。\n"
        "Shot 2 (5s): 近景，对面的张三抬眼。\n"
    )
    shots, refs, override = parse_prompt(text)
    assert len(shots) == 2
    assert shots[0].duration == 3
    assert shots[0].text == "中远景，主角推门进酒馆。"
    assert shots[1].duration == 5
    assert shots[1].text == "近景，对面的张三抬眼。"
    assert override is False  # 有 header → 派生模式


def test_parse_three_shots_mixed_whitespace():
    text = """Shot 1 (2s):  开场  
Shot 2 (4s):   中段
Shot 3 (3s): 收尾"""
    shots, _refs, _ = parse_prompt(text)
    durations = [s.duration for s in shots]
    assert durations == [2, 4, 3]


def test_parse_empty_returns_empty_text_as_single_shot():
    shots, refs, override = parse_prompt("")
    assert len(shots) == 1
    assert shots[0].text == ""
    assert override is True
```

- [ ] **Step 2：运行测试确认失败**

```bash
uv run pytest tests/lib/test_shot_parser.py -v
```

Expected: FAIL（`shot_parser` 模块不存在）。

- [ ] **Step 3：创建包与实现**

创建 `lib/reference_video/__init__.py`：

```python
from lib.reference_video.shot_parser import parse_prompt, render_prompt_for_backend

__all__ = ["parse_prompt", "render_prompt_for_backend"]
```

创建 `lib/reference_video/shot_parser.py`：

```python
"""参考视频 prompt 解析器：prompt ↔ Shot[]/references 双向转换。

Spec: docs/superpowers/specs/2026-04-15-reference-to-video-mode-design.md §4.3
"""

from __future__ import annotations

import re

from lib.script_models import ReferenceResource, Shot

_SHOT_HEADER_RE = re.compile(
    r"""^Shot\s+\d+\s*\(\s*(\d+)\s*s\s*\)\s*:\s*(.*)$""",
    re.IGNORECASE,
)

# @名称：Unicode 字母/数字/下划线；不吞 @ 之前的字符
_MENTION_RE = re.compile(r"@([\w\u4e00-\u9fff]+)")


def parse_prompt(text: str) -> tuple[list[Shot], list[str], bool]:
    """把用户书写的 prompt 文本拆为 (shots, mention_names, duration_override)。

    返回的第二项是 prompt 中出现的名字列表（保持首次出现的顺序、去重），
    由 caller 结合 project.json 分派成 ReferenceResource（本函数不区分 type）。

    - 有 `Shot N (Xs):` header → 按 header 切分；override=False
    - 无 header → 整段视为单镜头、duration 由 caller 指定；override=True
    """
    lines = text.splitlines()
    segments: list[tuple[int, str]] = []
    current_duration: int | None = None
    current_buf: list[str] = []

    for line in lines:
        m = _SHOT_HEADER_RE.match(line.strip())
        if m:
            if current_duration is not None:
                segments.append((current_duration, "\n".join(current_buf).strip()))
            current_duration = int(m.group(1))
            current_buf = [m.group(2)]
        else:
            current_buf.append(line)

    if current_duration is not None:
        segments.append((current_duration, "\n".join(current_buf).strip()))

    if not segments:
        # 无 header → 单镜头
        return [Shot(duration=1, text=text.strip())], _extract_mentions(text), True

    shots = [Shot(duration=d, text=t) for d, t in segments]
    mentions = _extract_mentions(text)
    return shots, mentions, False


def _extract_mentions(text: str) -> list[str]:
    seen: list[str] = []
    for m in _MENTION_RE.finditer(text):
        name = m.group(1)
        if name not in seen:
            seen.append(name)
    return seen


def render_prompt_for_backend(text: str, references: list[ReferenceResource]) -> str:
    """把 prompt 中的 @名称 替换为 [图N]，其中 N 是 references 列表中 1-based 序号。"""
    index_by_name: dict[str, int] = {}
    for i, ref in enumerate(references, start=1):
        index_by_name[ref.name] = i

    def _repl(m: re.Match[str]) -> str:
        name = m.group(1)
        idx = index_by_name.get(name)
        return f"[图{idx}]" if idx else m.group(0)  # 未注册 → 保留原样

    return _MENTION_RE.sub(_repl, text)
```

- [ ] **Step 4：运行测试确认通过**

```bash
uv run pytest tests/lib/test_shot_parser.py -v
```

Expected: 4 PASS。

- [ ] **Step 5：Commit**

```bash
git add lib/reference_video/ tests/lib/test_shot_parser.py
git commit -m "feat(reference-video): add shot_parser.parse_prompt for multi-shot prompts"
```

---

## Task 4：`@` 提及抽取 + `render_prompt_for_backend`

**Files:**
- Test: `tests/lib/test_shot_parser.py`

- [ ] **Step 1：追加失败测试**

追加到 `tests/lib/test_shot_parser.py`：

```python
from lib.reference_video.shot_parser import render_prompt_for_backend
from lib.script_models import ReferenceResource


def test_extract_mentions_ordered_unique():
    text = "Shot 1 (3s): @张三 看向 @酒馆\nShot 2 (5s): @张三 拔剑 @长剑"
    _shots, refs, _ = parse_prompt(text)
    assert refs == ["张三", "酒馆", "长剑"]


def test_extract_mentions_empty_prompt():
    _shots, refs, _ = parse_prompt("没有任何提及")
    assert refs == []


def test_render_prompt_replaces_mentions():
    text = "中景，@张三 走进 @酒馆 找 @长剑。"
    refs = [
        ReferenceResource(type="character", name="张三"),
        ReferenceResource(type="scene", name="酒馆"),
        ReferenceResource(type="prop", name="长剑"),
    ]
    rendered = render_prompt_for_backend(text, refs)
    assert rendered == "中景，[图1] 走进 [图2] 找 [图3]。"


def test_render_prompt_unknown_mention_kept():
    text = "@张三 和 @未知 对话"
    refs = [ReferenceResource(type="character", name="张三")]
    rendered = render_prompt_for_backend(text, refs)
    assert "[图1]" in rendered
    assert "@未知" in rendered  # 未注册保留


def test_render_prompt_multi_shot_text():
    text = "Shot 1 (3s): @张三 推门\nShot 2 (5s): @张三 坐下"
    refs = [ReferenceResource(type="character", name="张三")]
    rendered = render_prompt_for_backend(text, refs)
    assert rendered.count("[图1]") == 2
    assert "Shot 1 (3s):" in rendered  # header 保留
```

- [ ] **Step 2：运行测试确认通过**

```bash
uv run pytest tests/lib/test_shot_parser.py -v
```

Expected: 9 PASS（Task 3 已实现全部函数，测试仅追加覆盖场景）。

- [ ] **Step 3：Commit**

```bash
git add tests/lib/test_shot_parser.py
git commit -m "test(shot-parser): cover mention extraction and [图N] rendering"
```

---

## Task 5：`compute_duration_from_shots` 派生函数

**Files:**
- Modify: `lib/reference_video/shot_parser.py`
- Modify: `lib/reference_video/__init__.py`
- Test: `tests/lib/test_shot_parser.py`

- [ ] **Step 1：追加失败测试**

追加到 `tests/lib/test_shot_parser.py`：

```python
from lib.reference_video.shot_parser import compute_duration_from_shots
from lib.script_models import Shot


def test_compute_duration_sums_shots():
    shots = [Shot(duration=3, text="a"), Shot(duration=5, text="b"), Shot(duration=2, text="c")]
    assert compute_duration_from_shots(shots) == 10


def test_compute_duration_single_shot():
    assert compute_duration_from_shots([Shot(duration=7, text="x")]) == 7


def test_compute_duration_empty_list():
    assert compute_duration_from_shots([]) == 0
```

- [ ] **Step 2：运行测试确认失败**

```bash
uv run pytest tests/lib/test_shot_parser.py::test_compute_duration_sums_shots -v
```

Expected: FAIL。

- [ ] **Step 3：实现**

编辑 `lib/reference_video/shot_parser.py`，在文件末尾追加：

```python
def compute_duration_from_shots(shots: list[Shot]) -> int:
    """把 shots 时长求和，返回整数秒。"""
    return sum(s.duration for s in shots)
```

编辑 `lib/reference_video/__init__.py`：

```python
from lib.reference_video.shot_parser import (
    compute_duration_from_shots,
    parse_prompt,
    render_prompt_for_backend,
)

__all__ = ["compute_duration_from_shots", "parse_prompt", "render_prompt_for_backend"]
```

- [ ] **Step 4：运行测试确认通过**

```bash
uv run pytest tests/lib/test_shot_parser.py -v
```

Expected: 12 PASS。

- [ ] **Step 5：Commit**

```bash
git add lib/reference_video/ tests/lib/test_shot_parser.py
git commit -m "feat(shot-parser): add compute_duration_from_shots helper"
```

---

## Task 6：`DataValidator` 接受 `reference_video` content_mode

**Files:**
- Modify: `lib/data_validator.py`
- Test: `tests/lib/test_data_validator_reference.py`

先看一眼 `lib/data_validator.py` 里 `VALID_CONTENT_MODES` 和 `_validate_episode_payload` 的位置，然后加分支。

- [ ] **Step 1：定位现有 validator 分支**

```bash
uv run python -c "from lib.data_validator import DataValidator; print(DataValidator.VALID_CONTENT_MODES)"
```

Expected: `{'narration', 'drama'}`

```bash
grep -n "VALID_CONTENT_MODES\|_validate_episode_payload\|_validate_segments\|_validate_scenes" lib/data_validator.py | head
```

记下 `_validate_episode_payload` 行号（用于 Step 4 插入分支）。

- [ ] **Step 2：写失败测试**

创建 `tests/lib/test_data_validator_reference.py`：

```python
import json
from pathlib import Path

from lib.data_validator import DataValidator


def _write(dir: Path, path: str, data: dict) -> Path:
    full = dir / path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return full


def _valid_reference_script(episode: int = 1) -> dict:
    return {
        "episode": episode,
        "title": "E1",
        "content_mode": "reference_video",
        "summary": "x",
        "novel": {"title": "t", "chapter": "c"},
        "duration_seconds": 8,
        "video_units": [
            {
                "unit_id": f"E{episode}U1",
                "shots": [
                    {"duration": 3, "text": "Shot 1 (3s): @张三 推门"},
                    {"duration": 5, "text": "Shot 2 (5s): @酒馆 全景"},
                ],
                "references": [
                    {"type": "character", "name": "张三"},
                    {"type": "scene", "name": "酒馆"},
                ],
                "duration_seconds": 8,
                "duration_override": False,
                "transition_to_next": "cut",
                "note": None,
                "generated_assets": {
                    "storyboard_image": None,
                    "storyboard_last_image": None,
                    "grid_id": None,
                    "grid_cell_index": None,
                    "video_clip": None,
                    "video_uri": None,
                    "status": "pending",
                },
            },
        ],
    }


def test_validator_accepts_reference_video_content_mode(tmp_path: Path):
    project = {
        "title": "T",
        "content_mode": "reference_video",
        "style": "s",
        "episodes": [{"episode": 1, "title": "E1", "script_file": "scripts/episode_1.json"}],
        "characters": {"张三": {"description": "x"}},
        "scenes": {"酒馆": {"description": "x"}},
        "props": {},
    }
    _write(tmp_path, "project.json", project)
    _write(tmp_path, "scripts/episode_1.json", _valid_reference_script())

    v = DataValidator()
    result = v.validate_project_tree(tmp_path)
    assert result.valid, result.errors


def test_validator_rejects_unknown_mention(tmp_path: Path):
    project = {
        "title": "T",
        "content_mode": "reference_video",
        "style": "s",
        "episodes": [{"episode": 1, "title": "E1", "script_file": "scripts/episode_1.json"}],
        "characters": {},
        "scenes": {},
        "props": {},
    }
    _write(tmp_path, "project.json", project)
    _write(tmp_path, "scripts/episode_1.json", _valid_reference_script())  # 引用了未注册的 张三/酒馆

    v = DataValidator()
    result = v.validate_project_tree(tmp_path)
    assert not result.valid
    assert any("张三" in e for e in result.errors)


def test_validator_allows_reference_videos_dir(tmp_path: Path):
    project = {
        "title": "T",
        "content_mode": "reference_video",
        "style": "s",
        "episodes": [],
        "characters": {},
        "scenes": {},
        "props": {},
    }
    _write(tmp_path, "project.json", project)
    (tmp_path / "reference_videos").mkdir()
    (tmp_path / "reference_videos" / "E1U1.mp4").write_bytes(b"\x00")

    v = DataValidator()
    result = v.validate_project_tree(tmp_path)
    assert result.valid, result.errors
```

- [ ] **Step 3：运行测试确认失败**

```bash
uv run pytest tests/lib/test_data_validator_reference.py -v
```

Expected: FAIL（`reference_video` 不在 `VALID_CONTENT_MODES`）。

- [ ] **Step 4：扩展 validator**

编辑 `lib/data_validator.py`：

1. `VALID_CONTENT_MODES` 改为 `{"narration", "drama", "reference_video"}`
2. `ALLOWED_ROOT_ENTRIES` 追加 `"reference_videos"`
3. 新增 `_validate_reference_video_script` 方法
4. `_validate_episode_payload` 分派到对应校验器

具体：

```python
# Line ~41
VALID_CONTENT_MODES = {"narration", "drama", "reference_video"}

# Line ~46（ALLOWED_ROOT_ENTRIES 集合内追加）
    "reference_videos",

# 追加新方法（放在 _validate_scenes 等方法附近，保持分组）
def _validate_reference_video_script(
    self,
    script: dict[str, Any],
    project_asset_refs: dict[str, set[str]],  # {"character": {...}, "scene": {...}, "prop": {...}}
    errors: list[str],
    warnings: list[str],
) -> None:
    units = script.get("video_units")
    if not isinstance(units, list) or not units:
        errors.append("reference_video 脚本缺少 video_units 数组或为空")
        return

    for idx, unit in enumerate(units):
        prefix = f"video_units[{idx}]"
        if not isinstance(unit, dict):
            errors.append(f"{prefix}: 必须是对象")
            continue
        if not unit.get("unit_id"):
            errors.append(f"{prefix}: 缺少 unit_id")
        shots = unit.get("shots")
        if not isinstance(shots, list) or not shots:
            errors.append(f"{prefix}: shots 必须是非空数组")
        refs = unit.get("references") or []
        for ref in refs:
            if not isinstance(ref, dict):
                errors.append(f"{prefix}: reference 条目必须是对象")
                continue
            rtype = ref.get("type")
            rname = ref.get("name")
            if rtype not in {"character", "scene", "prop"}:
                errors.append(f"{prefix}: reference.type 无效: {rtype!r}")
                continue
            bucket = project_asset_refs.get(rtype, set())
            if rname not in bucket:
                errors.append(
                    f"{prefix}: 引用的{rtype} '{rname}' 不在 project.json 对应 bucket 中"
                )

# _validate_episode_payload 内，按 content_mode 分派：
# 原本只有 narration / drama 两支，加一支 reference_video
# 位置在现有的 "if content_mode == 'narration': ..." 后面
```

（实施时需要根据 main 现有 `_validate_episode_payload` 的实际结构插入；上面给出的是示意逻辑。）

- [ ] **Step 5：运行测试确认通过**

```bash
uv run pytest tests/lib/test_data_validator_reference.py -v
```

Expected: 3 PASS。

- [ ] **Step 6：回归跑现有 validator 测试**

```bash
uv run pytest tests/test_data_validator.py -v
```

Expected：main 上现有的全部 PASS（不应因扩展而破坏）。

- [ ] **Step 7：Commit**

```bash
git add lib/data_validator.py tests/lib/test_data_validator_reference.py
git commit -m "feat(validator): accept reference_video content_mode with video_units check"
```

---

## Task 7：`effective_mode(project, episode)` 模块函数

**Files:**
- Modify: `lib/project_manager.py`
- Test: `tests/lib/test_project_manager_effective_mode.py`

- [ ] **Step 1：写失败测试**

创建 `tests/lib/test_project_manager_effective_mode.py`：

```python
from lib.project_manager import effective_mode


def test_effective_mode_defaults_to_storyboard():
    assert effective_mode(project={}, episode={}) == "storyboard"


def test_effective_mode_reads_project_level():
    assert effective_mode(project={"generation_mode": "grid"}, episode={}) == "grid"


def test_effective_mode_episode_overrides_project():
    assert effective_mode(
        project={"generation_mode": "grid"},
        episode={"generation_mode": "reference_video"},
    ) == "reference_video"


def test_effective_mode_episode_none_falls_back():
    assert effective_mode(
        project={"generation_mode": "grid"},
        episode={"generation_mode": None},
    ) == "grid"


def test_effective_mode_empty_episode_string_falls_back():
    assert effective_mode(
        project={"generation_mode": "grid"},
        episode={"generation_mode": ""},
    ) == "grid"


def test_effective_mode_rejects_unknown_value_fallback():
    # 未知值应回退到 storyboard，不抛异常（兼容旧项目的脏数据）
    assert effective_mode(project={"generation_mode": "invalid"}, episode={}) == "storyboard"
```

- [ ] **Step 2：运行测试确认失败**

```bash
uv run pytest tests/lib/test_project_manager_effective_mode.py -v
```

Expected: FAIL。

- [ ] **Step 3：实现**

编辑 `lib/project_manager.py`，在文件顶部（import 之后、类定义之前）添加：

```python
_VALID_GENERATION_MODES = {"storyboard", "grid", "reference_video"}
_DEFAULT_GENERATION_MODE = "storyboard"


def effective_mode(*, project: dict, episode: dict) -> str:
    """按 episode → project → 默认 storyboard 回退解析 generation_mode。

    Spec §4.6。未知值一律回退到默认，兼容旧项目/脏数据。
    """
    ep_mode = episode.get("generation_mode")
    if ep_mode in _VALID_GENERATION_MODES:
        return ep_mode
    proj_mode = project.get("generation_mode")
    if proj_mode in _VALID_GENERATION_MODES:
        return proj_mode
    return _DEFAULT_GENERATION_MODE
```

- [ ] **Step 4：运行测试确认通过**

```bash
uv run pytest tests/lib/test_project_manager_effective_mode.py -v
```

Expected: 6 PASS。

- [ ] **Step 5：Commit**

```bash
git add lib/project_manager.py tests/lib/test_project_manager_effective_mode.py
git commit -m "feat(project-manager): add effective_mode() fallback helper"
```

---

## Task 8：让 parser 协助产出 `ReferenceResource[]`（依 project 分派 type）

**Files:**
- Modify: `lib/reference_video/shot_parser.py`
- Modify: `lib/reference_video/__init__.py`
- Test: `tests/lib/test_shot_parser.py`

`parse_prompt` 现在只返回名字列表；PR3 的后端会把这些名字按 project.json 的三 bucket 分派成 `ReferenceResource`。为方便 PR3 和 Agent 共享，此处加一个纯函数 `resolve_references(names, project)`。

- [ ] **Step 1：追加失败测试**

追加到 `tests/lib/test_shot_parser.py`：

```python
from lib.reference_video.shot_parser import resolve_references


def _proj(characters=None, scenes=None, props=None):
    return {
        "characters": characters or {},
        "scenes": scenes or {},
        "props": props or {},
    }


def test_resolve_references_character():
    proj = _proj(characters={"张三": {}})
    refs, missing = resolve_references(["张三"], proj)
    assert len(refs) == 1
    assert refs[0].type == "character"
    assert refs[0].name == "张三"
    assert missing == []


def test_resolve_references_scene_and_prop():
    proj = _proj(scenes={"酒馆": {}}, props={"长剑": {}})
    refs, missing = resolve_references(["酒馆", "长剑"], proj)
    types = {r.name: r.type for r in refs}
    assert types == {"酒馆": "scene", "长剑": "prop"}
    assert missing == []


def test_resolve_references_missing_reports_name():
    refs, missing = resolve_references(["张三", "未知"], _proj(characters={"张三": {}}))
    assert len(refs) == 1
    assert missing == ["未知"]


def test_resolve_references_preserves_order():
    proj = _proj(characters={"B": {}}, scenes={"A": {}}, props={"C": {}})
    refs, _ = resolve_references(["A", "B", "C"], proj)
    assert [r.name for r in refs] == ["A", "B", "C"]


def test_resolve_references_empty_input():
    refs, missing = resolve_references([], _proj())
    assert refs == []
    assert missing == []
```

- [ ] **Step 2：运行测试确认失败**

```bash
uv run pytest tests/lib/test_shot_parser.py -v
```

Expected: FAIL（`resolve_references` 未定义）。

- [ ] **Step 3：实现**

编辑 `lib/reference_video/shot_parser.py`，追加：

```python
from lib.asset_types import BUCKET_KEY


def resolve_references(
    names: list[str],
    project: dict,
) -> tuple[list[ReferenceResource], list[str]]:
    """按 project.json 三 bucket 把 mention 名字分派成 ReferenceResource。

    Returns:
        (refs, missing): refs 保持入参顺序；missing 是没在任何 bucket 找到的名字
    """
    buckets: dict[str, dict] = {
        "character": project.get(BUCKET_KEY["character"]) or {},
        "scene": project.get(BUCKET_KEY["scene"]) or {},
        "prop": project.get(BUCKET_KEY["prop"]) or {},
    }
    refs: list[ReferenceResource] = []
    missing: list[str] = []
    for name in names:
        resolved = False
        for rtype, bucket in buckets.items():
            if name in bucket:
                refs.append(ReferenceResource(type=rtype, name=name))
                resolved = True
                break
        if not resolved:
            missing.append(name)
    return refs, missing
```

编辑 `lib/reference_video/__init__.py`：

```python
from lib.reference_video.shot_parser import (
    compute_duration_from_shots,
    parse_prompt,
    render_prompt_for_backend,
    resolve_references,
)

__all__ = [
    "compute_duration_from_shots",
    "parse_prompt",
    "render_prompt_for_backend",
    "resolve_references",
]
```

- [ ] **Step 4：运行测试确认通过**

```bash
uv run pytest tests/lib/test_shot_parser.py -v
```

Expected: 17 PASS。

- [ ] **Step 5：Commit**

```bash
git add lib/reference_video/ tests/lib/test_shot_parser.py
git commit -m "feat(shot-parser): add resolve_references to dispatch names by project buckets"
```

---

## Task 9：PR 收尾 — lint + 覆盖率 + 自检

- [ ] **Step 1：lint + format**

```bash
uv run ruff check lib/reference_video/ lib/script_models.py lib/data_validator.py lib/project_manager.py tests/lib/test_script_models_reference.py tests/lib/test_shot_parser.py tests/lib/test_data_validator_reference.py tests/lib/test_project_manager_effective_mode.py
uv run ruff format lib/reference_video/ lib/script_models.py lib/data_validator.py lib/project_manager.py tests/lib/test_script_models_reference.py tests/lib/test_shot_parser.py tests/lib/test_data_validator_reference.py tests/lib/test_project_manager_effective_mode.py
```

Expected：干净。

- [ ] **Step 2：覆盖率检查**

```bash
uv run pytest tests/lib/test_shot_parser.py tests/lib/test_script_models_reference.py tests/lib/test_data_validator_reference.py tests/lib/test_project_manager_effective_mode.py --cov=lib.reference_video --cov=lib.script_models --cov-report=term-missing
```

Expected：`lib/reference_video/shot_parser.py` 覆盖率 ≥ 95%；`ReferenceVideoUnit` / `ReferenceVideoScript` 100%。

- [ ] **Step 3：回归跑全部后端测试**

```bash
uv run pytest tests/ -x --ignore=tests/integration
```

Expected：全绿。若 `test_data_validator.py` 等文件因 `VALID_CONTENT_MODES` 扩大失败，按报错修复（通常只需断言调整）。

- [ ] **Step 4：更新 roadmap**

编辑 `docs/superpowers/plans/2026-04-17-reference-to-video-roadmap.md`，在"里程碑追踪"勾选 `- [ ] PR2 合并（数据模型 + parser）`（留给合并时勾）。

- [ ] **Step 5：开 PR**

```bash
gh pr create --title "feat(script-models): reference-video data model + shot parser" --body "$(cat <<'EOF'
## Summary
- 新增 `ReferenceVideoScript` / `ReferenceVideoUnit` / `Shot` / `ReferenceResource` Pydantic 模型
- 新增 `lib/reference_video/shot_parser.py`：prompt ↔ Shot[]/references 双向解析 + `resolve_references` 按 project buckets 分派
- `DataValidator` 接受 `content_mode=reference_video`，允许 `reference_videos/` 顶层目录
- `effective_mode(project, episode)` 解析 `generation_mode` 回退链

## 依赖 & 影响
- 前置：无（与 PR1 SDK 验证并行可做）
- 旧项目零影响：`generation_mode` 缺失回退 storyboard；`schema_version` 不变（v1）
- 后续 PR3 后端 executor 会消费这套模型

## Test plan
- [x] `uv run pytest tests/lib/test_shot_parser.py tests/lib/test_script_models_reference.py tests/lib/test_data_validator_reference.py tests/lib/test_project_manager_effective_mode.py -v` 全绿
- [x] 覆盖率 ≥ 95%
- [x] 回归 `tests/test_data_validator.py` 未破坏

## Out of scope
- 路由 / queue / worker → PR3
- 前端 / Agent → PR4-7
EOF
)"
```

---

## Self-Review

1. **Spec 覆盖**：
   - §4.2 Pydantic 模型 ✅（Task 1-2）
   - §4.3 parser 规则 ✅（Task 3-5）
   - §4.5 资产映射（BUCKET_KEY） ✅（Task 8）
   - §4.6 effective_mode ✅（Task 7）
   - §11 schema_version 策略（不 bump） ✅（未改 schema_version）
2. **Placeholder scan**：
   - Task 6 Step 4 里的 "实施时需要根据 main 现有 `_validate_episode_payload` 的实际结构插入" 是真实运行时适配点，不是 placeholder；已给出插入位置定位命令
3. **Type 一致性**：
   - `Shot.duration` 始终 `int`（Task 1 起）
   - `ReferenceResource.type` 始终 `Literal["character", "scene", "prop"]`
   - `parse_prompt` 返回签名 `(shots, names, override)` 自 Task 3 稳定
   - `resolve_references` 返回 `(refs, missing)` 自 Task 8 稳定

## 验收清单

- [ ] 9 个 task 全部 commit
- [ ] `uv run pytest tests/lib/test_shot_parser.py tests/lib/test_script_models_reference.py tests/lib/test_data_validator_reference.py tests/lib/test_project_manager_effective_mode.py -v` 全绿（≥17 测试）
- [ ] 回归 `tests/` 全绿
- [ ] 覆盖率 ≥ 90%
- [ ] `schema_version` 未 bump
- [ ] PR 已开
