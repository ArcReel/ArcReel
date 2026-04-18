# PR3 · M3 后端（路由 + executor + queue）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 PR2 落地的 `ReferenceVideoScript` / `shot_parser` / `effective_mode` 接到可执行的服务端：新增 `/api/v1/projects/{project_name}/reference-videos/...` 路由族、`execute_reference_video_task` executor，接入 GenerationQueue/Worker dispatch，处理参考图压缩、`@→[图N]` 渲染、Veo/Sora 特判、归档和费用。纯后端工作，不碰前端/Agent。

**Architecture:** 新路由 `server/routers/reference_videos.py` 对 `scripts/episode_{N}.json` 的 `video_units[]` 做 CRUD + 重排 + 生成入队。Executor `server/services/reference_video_tasks.py::execute_reference_video_task` 读 unit → 用 `lib.reference_video.resolve_references` 把 `@` 名字按三 bucket 分派 → `lib.image_utils.compress_image_bytes` 把 sheet 图缩到 ≤2048px 写入 `tempfile.NamedTemporaryFile` → `lib.reference_video.render_prompt_for_backend` 把 `@X` 替成 `[图N]` → 复用 `MediaGenerator.generate_video_async(resource_type="reference_videos", ...)` → `lib.thumbnail.extract_video_thumbnail` 写首帧 → 回写 `unit.generated_assets`。`RequestPayloadTooLargeError` 触发二次压缩（1024px, q=70）；Veo/Sora 按供应商能力矩阵裁剪 `references` + `duration`，超限以 `warnings[]` 回前端。`GenerationQueue.task_type="reference_video"`、`media_type="video"`，走与 `execute_video_task` 相同的并发通道。

**Tech Stack:** Python 3.11+ / FastAPI / SQLAlchemy async ORM / Pydantic v2 / pytest

## 参考设计

- Roadmap：`docs/superpowers/plans/2026-04-17-reference-to-video-roadmap.md`
- Spec：`docs/superpowers/specs/2026-04-15-reference-to-video-mode-design.md` §5、§8.2、§8.3
- PR2 plan（前置）：`docs/superpowers/plans/2026-04-17-reference-to-video-pr2-data-model.md`
- 现有 executor 参照：`server/services/generation_tasks.py:701-834`（`execute_video_task`）
- 现有路由参照：`server/routers/grids.py`
- 压缩工具：`lib/image_utils.py:51-82`（`compress_image_bytes`）
- VideoGenerationRequest：`lib/video_backends/base.py:129-168`
- MediaGenerator.generate_video_async：`lib/media_generator.py:326-440`（接受 `reference_images` + `generate_audio` via version_metadata）

## 文件结构

### 新增

| 文件 | 职责 |
|---|---|
| `server/routers/reference_videos.py` | 6 个端点：`list` / `add` / `patch` / `delete` / `reorder` / `generate` |
| `server/services/reference_video_tasks.py` | `execute_reference_video_task` + 辅助（参考图解析、压缩、prompt 渲染） |
| `lib/reference_video/errors.py` | 新异常类：`MissingReferenceError` / `RequestPayloadTooLargeError` / `ProviderUnsupportedFeatureError` |
| `tests/server/test_reference_videos_router.py` | 路由端到端测试（FastAPI TestClient） |
| `tests/server/test_reference_video_tasks.py` | executor 单元测试（mock backend） |
| `tests/lib/test_image_compression_batch.py` | 批量压缩 9 张的内存 / 输出尺寸测试 |
| `tests/lib/test_cost_calculator_reference_video.py` | `estimate_reference_video_cost` 单元测试 |
| `tests/server/__init__.py` | 目录占位 |

### 改造

| 文件 | 改造点 |
|---|---|
| `lib/cost_calculator.py` | 新增 `estimate_reference_video_cost(units, provider, model, …)` |
| `lib/generation_worker.py` | `_TASK_EXECUTORS` 映射（实际在 generation_tasks.py）注册 `"reference_video"` |
| `server/services/generation_tasks.py` | `_TASK_EXECUTORS` 加 `"reference_video"`；`_TASK_CHANGE_SPECS` 加条目；`_compute_affected_fingerprints` 支持 `task_type="reference_video"` |
| `server/services/project_archive.py` | `_VERSION_HISTORY_DIRS` / `_RESOURCE_EXTENSIONS` 加 `reference_videos`；`_repair_project_tree` 遍历 `video_units` |
| `server/app.py` | 挂载 `reference_videos.router` |
| `lib/i18n/zh/errors.py` / `lib/i18n/en/errors.py` | 新增 6 个 `ref_*` key |

---

## Task 1：新增 i18n 错误 key（zh + en 对齐）

**Files:**
- Modify: `lib/i18n/zh/errors.py`
- Modify: `lib/i18n/en/errors.py`
- Test: `tests/test_i18n_consistency.py`（现有，运行即可）

- [ ] **Step 1：确认现状**

```bash
uv run pytest tests/test_i18n_consistency.py -v
```

Expected：全绿。

- [ ] **Step 2：加 zh 错误 key**

编辑 `lib/i18n/zh/errors.py`，在 `MESSAGES` 字典末尾（`# Versions` 段之后）追加：

```python
    # Reference Video
    "ref_missing_asset": "参考图引用的{type}「{name}」不在项目资产库中，请先生成",
    "ref_duration_exceeded": "参考视频单元时长 {duration}s 超出 {model} 上限 {max_duration}s，已裁剪",
    "ref_too_many_images": "参考图数量 {count} 超出 {model} 上限 {max_count}，已取前 {max_count} 张",
    "ref_payload_too_large": "参考图请求体超出供应商限制，已二次压缩重试",
    "ref_sora_single_ref": "Sora 参考模式暂不支持多图，已降级为单图",
    "ref_shot_parse_fallback": "未识别到 Shot N (Xs): 标记，按单镜头处理",
```

- [ ] **Step 3：加 en 错误 key（参数名完全一致）**

编辑 `lib/i18n/en/errors.py`，在对应位置追加：

```python
    # Reference Video
    "ref_missing_asset": "Reference to {type} '{name}' is not in the project asset library, please generate it first",
    "ref_duration_exceeded": "Reference video unit duration {duration}s exceeds {model} limit of {max_duration}s, clamped",
    "ref_too_many_images": "Reference image count {count} exceeds {model} limit of {max_count}, kept the first {max_count}",
    "ref_payload_too_large": "Reference image payload exceeded provider limits, retried with extra compression",
    "ref_sora_single_ref": "Sora reference mode does not currently support multiple images, downgraded to single image",
    "ref_shot_parse_fallback": "No Shot N (Xs) header detected, treated as a single shot",
```

- [ ] **Step 4：回归 i18n 一致性**

```bash
uv run pytest tests/test_i18n_consistency.py -v
```

Expected：全绿（`test_errors_module_keys_match` 确认 zh/en key 对齐；`test_format_placeholders_consistent` 确认占位符一致）。

- [ ] **Step 5：Commit**

```bash
git add lib/i18n/zh/errors.py lib/i18n/en/errors.py
git commit -m "feat(i18n): add reference video error messages (zh+en)"
```

---

## Task 2：新增异常类 `lib/reference_video/errors.py`

**Files:**
- Create: `lib/reference_video/errors.py`
- Modify: `lib/reference_video/__init__.py`
- Test: `tests/lib/test_reference_video_errors.py`

- [ ] **Step 1：写失败测试**

创建 `tests/lib/test_reference_video_errors.py`：

```python
import pytest

from lib.reference_video.errors import (
    MissingReferenceError,
    ProviderUnsupportedFeatureError,
    RequestPayloadTooLargeError,
)


def test_missing_reference_error_carries_details():
    err = MissingReferenceError(missing=[("character", "张三"), ("scene", "酒馆")])
    assert err.missing == [("character", "张三"), ("scene", "酒馆")]
    assert "张三" in str(err)


def test_missing_reference_error_empty():
    with pytest.raises(ValueError):
        MissingReferenceError(missing=[])


def test_payload_too_large_error_default_message():
    err = RequestPayloadTooLargeError()
    assert "payload" in str(err).lower()


def test_provider_unsupported_feature_error_carries_feature():
    err = ProviderUnsupportedFeatureError(provider="sora", feature="multi_reference")
    assert err.provider == "sora"
    assert err.feature == "multi_reference"
```

- [ ] **Step 2：运行测试确认失败**

```bash
uv run pytest tests/lib/test_reference_video_errors.py -v
```

Expected：FAIL（模块不存在）。

- [ ] **Step 3：实现异常类**

创建 `lib/reference_video/errors.py`：

```python
"""参考生视频模式专用异常。"""

from __future__ import annotations


class MissingReferenceError(Exception):
    """@ 提及解析到不存在或无 sheet 的资源。"""

    def __init__(self, *, missing: list[tuple[str, str]]):
        if not missing:
            raise ValueError("missing must be non-empty")
        self.missing = missing
        names = ", ".join(f"{t}:{n}" for t, n in missing)
        super().__init__(f"Missing references: {names}")


class RequestPayloadTooLargeError(Exception):
    """视频生成请求体超出供应商限制（gRPC/HTTP body size）。"""

    def __init__(self, message: str = "Request payload too large"):
        super().__init__(message)


class ProviderUnsupportedFeatureError(Exception):
    """供应商不支持某项能力（如 Sora 多参考图）。"""

    def __init__(self, *, provider: str, feature: str):
        self.provider = provider
        self.feature = feature
        super().__init__(f"Provider {provider} does not support {feature}")
```

- [ ] **Step 4：re-export 到包**

编辑 `lib/reference_video/__init__.py`，把现有 re-export 扩展为：

```python
from lib.reference_video.errors import (
    MissingReferenceError,
    ProviderUnsupportedFeatureError,
    RequestPayloadTooLargeError,
)
from lib.reference_video.shot_parser import (
    compute_duration_from_shots,
    parse_prompt,
    render_prompt_for_backend,
    resolve_references,
)

__all__ = [
    "MissingReferenceError",
    "ProviderUnsupportedFeatureError",
    "RequestPayloadTooLargeError",
    "compute_duration_from_shots",
    "parse_prompt",
    "render_prompt_for_backend",
    "resolve_references",
]
```

- [ ] **Step 5：运行测试确认通过**

```bash
uv run pytest tests/lib/test_reference_video_errors.py -v
```

Expected：4 PASS。

- [ ] **Step 6：Commit**

```bash
git add lib/reference_video/errors.py lib/reference_video/__init__.py tests/lib/test_reference_video_errors.py
git commit -m "feat(reference-video): add domain exceptions for missing refs and oversized payloads"
```

---

## Task 3：批量参考图压缩帮助 + 测试

**Files:**
- Test: `tests/lib/test_image_compression_batch.py`

`compress_image_bytes` 已在 `lib/image_utils.py:51-82` 存在；本任务只补测试以保证批量场景行为稳定（9 张 sheet 批量压缩、长边下限、二次压缩后尺寸）。不需要新增函数。

- [ ] **Step 1：写测试**

创建 `tests/lib/test_image_compression_batch.py`：

```python
from __future__ import annotations

import io

import pytest
from PIL import Image

from lib.image_utils import compress_image_bytes


def _make_big_png(width: int = 4096, height: int = 3072) -> bytes:
    img = Image.new("RGB", (width, height), color=(240, 80, 40))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_compress_single_image_under_long_edge_2048():
    raw = _make_big_png()
    out = compress_image_bytes(raw, max_long_edge=2048, quality=85)
    with Image.open(io.BytesIO(out)) as im:
        assert max(im.size) <= 2048


def test_compress_batch_nine_images_memory_ok():
    """批量压缩 9 张 4K 图，检查每张输出尺寸与体积都符合预期。"""
    raw = _make_big_png()
    outputs = [compress_image_bytes(raw, max_long_edge=2048, quality=85) for _ in range(9)]
    assert len(outputs) == 9
    for out in outputs:
        # 压缩后体积显著小于原 PNG
        assert len(out) < len(raw)
        with Image.open(io.BytesIO(out)) as im:
            assert max(im.size) <= 2048


def test_compress_fallback_long_edge_1024_smaller_bytes():
    raw = _make_big_png()
    first = compress_image_bytes(raw, max_long_edge=2048, quality=85)
    second = compress_image_bytes(raw, max_long_edge=1024, quality=70)
    assert len(second) < len(first)
    with Image.open(io.BytesIO(second)) as im:
        assert max(im.size) <= 1024


def test_compress_rejects_invalid_bytes():
    with pytest.raises(ValueError):
        compress_image_bytes(b"not an image", max_long_edge=1024)
```

- [ ] **Step 2：运行测试确认通过**

```bash
uv run pytest tests/lib/test_image_compression_batch.py -v
```

Expected：4 PASS。

- [ ] **Step 3：Commit**

```bash
git add tests/lib/test_image_compression_batch.py
git commit -m "test(image-utils): cover batch compression of 9 reference images"
```

---

## Task 4：`CostCalculator.estimate_reference_video_cost`

**Files:**
- Modify: `lib/cost_calculator.py`
- Test: `tests/lib/test_cost_calculator_reference_video.py`

`calculate_cost` 已按 provider 分派到 Ark/Grok/OpenAI/Gemini 视频费率。本任务在 `CostCalculator` 类内新增一个便捷入口，把"一集 N 个 unit × 每 unit duration"聚合成单一 `(amount, currency)`。

- [ ] **Step 1：写失败测试**

创建 `tests/lib/test_cost_calculator_reference_video.py`：

```python
from __future__ import annotations

import pytest

from lib.cost_calculator import CostCalculator
from lib.providers import PROVIDER_ARK, PROVIDER_GROK, PROVIDER_OPENAI


@pytest.fixture
def calc() -> CostCalculator:
    return CostCalculator()


def test_estimate_grok_reference_video_per_second(calc: CostCalculator):
    # Grok: 2 units, 各 8s, 费率 0.050 USD/s → 0.8 USD
    amount, currency = calc.estimate_reference_video_cost(
        unit_durations_seconds=[8, 8],
        provider=PROVIDER_GROK,
        model="grok-imagine-video",
    )
    assert currency == "USD"
    assert amount == pytest.approx(0.8, abs=1e-6)


def test_estimate_openai_reference_video_with_resolution(calc: CostCalculator):
    # sora-2-pro@1080p = 0.70 USD/s; 1 unit × 12s → 8.4
    amount, currency = calc.estimate_reference_video_cost(
        unit_durations_seconds=[12],
        provider=PROVIDER_OPENAI,
        model="sora-2-pro",
        resolution="1080p",
    )
    assert currency == "USD"
    assert amount == pytest.approx(8.4, abs=1e-6)


def test_estimate_ark_reference_video_requires_token_estimate(calc: CostCalculator):
    # Ark 走 token 计费；duration→token 估算使用 60 tokens/s 的常量近似
    amount, currency = calc.estimate_reference_video_cost(
        unit_durations_seconds=[5, 10],
        provider=PROVIDER_ARK,
        model="doubao-seedance-2-0-260128",
        generate_audio=True,
    )
    assert currency == "CNY"
    assert amount > 0


def test_estimate_empty_units_returns_zero(calc: CostCalculator):
    amount, currency = calc.estimate_reference_video_cost(
        unit_durations_seconds=[],
        provider=PROVIDER_GROK,
        model="grok-imagine-video",
    )
    assert amount == 0.0
    assert currency == "USD"
```

- [ ] **Step 2：运行测试确认失败**

```bash
uv run pytest tests/lib/test_cost_calculator_reference_video.py -v
```

Expected：FAIL（方法不存在）。

- [ ] **Step 3：加聚合方法**

编辑 `lib/cost_calculator.py`，在 `_calculate_custom_cost` 静态方法之前（类体内，单例实例之前）插入：

```python
    # Ark 生成视频的 token/s 近似常量（用于参考模式成本估算，实际 token 由生成回调覆盖）
    _ARK_TOKENS_PER_SECOND_ESTIMATE = 60_000

    def estimate_reference_video_cost(
        self,
        *,
        unit_durations_seconds: list[int],
        provider: str,
        model: str | None = None,
        resolution: str | None = None,
        generate_audio: bool = True,
        service_tier: str = "default",
    ) -> tuple[float, str]:
        """聚合参考模式一集的视频费用：sum over units of (duration × 单价)。

        - Grok/OpenAI/Gemini：按 duration_seconds 累加后一次性计费
        - Ark：token-based 计费，按 duration × _ARK_TOKENS_PER_SECOND_ESTIMATE 近似
        """
        if not unit_durations_seconds:
            if provider == PROVIDER_ARK:
                return 0.0, "CNY"
            return 0.0, "USD"

        total_duration = sum(max(0, int(d)) for d in unit_durations_seconds)
        if provider == PROVIDER_ARK:
            usage_tokens = total_duration * self._ARK_TOKENS_PER_SECOND_ESTIMATE
            return self.calculate_ark_video_cost(
                usage_tokens=usage_tokens,
                service_tier=service_tier,
                generate_audio=generate_audio,
                model=model,
            )
        if provider == PROVIDER_GROK:
            return self.calculate_grok_video_cost(
                duration_seconds=total_duration,
                model=model,
            )
        if provider == PROVIDER_OPENAI:
            return self.calculate_openai_video_cost(
                duration_seconds=total_duration,
                model=model,
                resolution=resolution,
            )
        # Gemini/Veo 默认
        return (
            self.calculate_video_cost(
                duration_seconds=total_duration,
                resolution=resolution or "1080p",
                generate_audio=generate_audio,
                model=model,
            ),
            "USD",
        )
```

- [ ] **Step 4：运行测试确认通过**

```bash
uv run pytest tests/lib/test_cost_calculator_reference_video.py -v
```

Expected：4 PASS。

- [ ] **Step 5：Commit**

```bash
git add lib/cost_calculator.py tests/lib/test_cost_calculator_reference_video.py
git commit -m "feat(cost): add estimate_reference_video_cost per-unit aggregation"
```

---

## Task 5：Executor 骨架 — 加载 + reference 解析

**Files:**
- Create: `server/services/reference_video_tasks.py`
- Create: `tests/server/__init__.py`
- Test: `tests/server/test_reference_video_tasks.py`

本 Task 先完成 executor 的**加载 + 校验 + references 解析**这 2 步，不调真实 backend，返回部分结构供下一个 Task 扩展。

- [ ] **Step 1：创建 server/__init__.py 占位（若缺）**

```bash
ls tests/server/__init__.py 2>/dev/null || mkdir -p tests/server
```

然后创建 `tests/server/__init__.py`（空文件）。

- [ ] **Step 2：写失败测试**

创建 `tests/server/test_reference_video_tasks.py`：

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.reference_video.errors import MissingReferenceError
from server.services.reference_video_tasks import (
    _load_unit_context,
    _resolve_unit_references,
)


def _write_project(tmp_path: Path) -> Path:
    project = {
        "title": "T",
        "content_mode": "reference_video",
        "generation_mode": "reference_video",
        "style": "s",
        "characters": {"张三": {"description": "x", "character_sheet": "characters/张三.png"}},
        "scenes": {"酒馆": {"description": "x", "scene_sheet": "scenes/酒馆.png"}},
        "props": {},
        "episodes": [{"episode": 1, "title": "E1", "script_file": "scripts/episode_1.json"}],
    }
    script = {
        "episode": 1,
        "title": "E1",
        "content_mode": "reference_video",
        "summary": "x",
        "novel": {"title": "t", "chapter": "c"},
        "duration_seconds": 8,
        "video_units": [
            {
                "unit_id": "E1U1",
                "shots": [{"duration": 3, "text": "Shot 1 (3s): @张三 推门"}],
                "references": [
                    {"type": "character", "name": "张三"},
                    {"type": "scene", "name": "酒馆"},
                ],
                "duration_seconds": 3,
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
    proj_dir = tmp_path / "demo"
    proj_dir.mkdir()
    (proj_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    (proj_dir / "scripts").mkdir()
    (proj_dir / "scripts" / "episode_1.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    (proj_dir / "characters").mkdir()
    (proj_dir / "characters" / "张三.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (proj_dir / "scenes").mkdir()
    (proj_dir / "scenes" / "酒馆.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return proj_dir


def test_load_unit_context_returns_project_and_unit(tmp_path: Path):
    proj_dir = _write_project(tmp_path)
    project, script, unit = _load_unit_context(
        project_path=proj_dir,
        script_file="scripts/episode_1.json",
        unit_id="E1U1",
    )
    assert project["title"] == "T"
    assert script["episode"] == 1
    assert unit["unit_id"] == "E1U1"


def test_load_unit_context_unknown_unit_raises(tmp_path: Path):
    proj_dir = _write_project(tmp_path)
    with pytest.raises(ValueError, match="unit not found"):
        _load_unit_context(
            project_path=proj_dir,
            script_file="scripts/episode_1.json",
            unit_id="E9U9",
        )


def test_resolve_unit_references_maps_sheets(tmp_path: Path):
    proj_dir = _write_project(tmp_path)
    project, _, unit = _load_unit_context(
        project_path=proj_dir,
        script_file="scripts/episode_1.json",
        unit_id="E1U1",
    )
    resolved = _resolve_unit_references(project, proj_dir, unit["references"])
    assert [p.name for p in resolved] == ["张三.png", "酒馆.png"]


def test_resolve_unit_references_missing_sheet_raises(tmp_path: Path):
    proj_dir = _write_project(tmp_path)
    project, _, unit = _load_unit_context(
        project_path=proj_dir,
        script_file="scripts/episode_1.json",
        unit_id="E1U1",
    )
    # 删掉 character sheet，模拟未生成的情况
    (proj_dir / "characters" / "张三.png").unlink()
    with pytest.raises(MissingReferenceError) as excinfo:
        _resolve_unit_references(project, proj_dir, unit["references"])
    assert ("character", "张三") in excinfo.value.missing


def test_resolve_unit_references_unknown_name_raises(tmp_path: Path):
    proj_dir = _write_project(tmp_path)
    project, _, _ = _load_unit_context(
        project_path=proj_dir,
        script_file="scripts/episode_1.json",
        unit_id="E1U1",
    )
    bad_refs = [{"type": "prop", "name": "不存在的道具"}]
    with pytest.raises(MissingReferenceError) as excinfo:
        _resolve_unit_references(project, proj_dir, bad_refs)
    assert ("prop", "不存在的道具") in excinfo.value.missing
```

- [ ] **Step 3：运行测试确认失败**

```bash
uv run pytest tests/server/test_reference_video_tasks.py -v
```

Expected：FAIL（`server.services.reference_video_tasks` 不存在）。

- [ ] **Step 4：写 executor 骨架**

创建 `server/services/reference_video_tasks.py`：

```python
"""参考生视频 executor。

Spec: docs/superpowers/specs/2026-04-15-reference-to-video-mode-design.md §5.2
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from lib.asset_types import BUCKET_KEY, SHEET_KEY
from lib.db.base import DEFAULT_USER_ID
from lib.reference_video.errors import MissingReferenceError

logger = logging.getLogger(__name__)


def _load_unit_context(
    *,
    project_path: Path,
    script_file: str,
    unit_id: str,
) -> tuple[dict, dict, dict]:
    """读取 project.json + 指定 episode 剧本 + 目标 unit。"""
    project = json.loads((project_path / "project.json").read_text(encoding="utf-8"))
    script_rel = script_file.removeprefix("scripts/")
    script = json.loads(
        (project_path / "scripts" / script_rel).read_text(encoding="utf-8")
    )
    units = script.get("video_units") or []
    unit = next((u for u in units if u.get("unit_id") == unit_id), None)
    if unit is None:
        raise ValueError(f"unit not found: {unit_id}")
    return project, script, unit


def _resolve_unit_references(
    project: dict,
    project_path: Path,
    references: list[dict],
) -> list[Path]:
    """把 unit.references 转成绝对路径列表（按 references 顺序）。

    Raises:
        MissingReferenceError: 任一 reference 在 project.json 对应 bucket 缺失或 sheet 不存在。
    """
    missing: list[tuple[str, str]] = []
    resolved: list[Path] = []
    for ref in references:
        rtype = ref.get("type")
        rname = ref.get("name")
        if rtype not in BUCKET_KEY:
            missing.append((str(rtype), str(rname)))
            continue
        bucket = project.get(BUCKET_KEY[rtype]) or {}
        item = bucket.get(rname)
        sheet_rel = item.get(SHEET_KEY[rtype]) if isinstance(item, dict) else None
        if not sheet_rel:
            missing.append((rtype, rname))
            continue
        path = project_path / sheet_rel
        if not path.exists():
            missing.append((rtype, rname))
            continue
        resolved.append(path)

    if missing:
        raise MissingReferenceError(missing=missing)
    return resolved


async def execute_reference_video_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
) -> dict[str, Any]:
    """占位：下一个 Task 会补齐压缩 + 渲染 + backend 调用 + 更新元数据。"""
    raise NotImplementedError("execute_reference_video_task: filled in next task")
```

- [ ] **Step 5：运行测试确认通过**

```bash
uv run pytest tests/server/test_reference_video_tasks.py -v
```

Expected：5 PASS（`_load_unit_context` + `_resolve_unit_references` 相关用例）。

- [ ] **Step 6：Commit**

```bash
git add server/services/reference_video_tasks.py tests/server/__init__.py tests/server/test_reference_video_tasks.py
git commit -m "feat(reference-video): scaffold executor with unit loader and reference resolver"
```

---

## Task 6：压缩 + 渲染 + 供应商特判

**Files:**
- Modify: `server/services/reference_video_tasks.py`
- Test: `tests/server/test_reference_video_tasks.py`

- [ ] **Step 1：追加失败测试**

在 `tests/server/test_reference_video_tasks.py` 末尾追加：

```python
from lib.reference_video.errors import RequestPayloadTooLargeError
from server.services.reference_video_tasks import (
    _compress_references_to_tempfiles,
    _render_unit_prompt,
    _apply_provider_constraints,
)


def _make_png_bytes() -> bytes:
    import io

    from PIL import Image

    img = Image.new("RGB", (3000, 2000), color=(200, 100, 50))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_compress_references_returns_temp_paths(tmp_path: Path):
    src = tmp_path / "big.png"
    src.write_bytes(_make_png_bytes())
    temps = _compress_references_to_tempfiles([src, src])
    try:
        assert len(temps) == 2
        for p in temps:
            assert p.exists()
            assert p.stat().st_size > 0
    finally:
        for p in temps:
            p.unlink(missing_ok=True)


def test_compress_references_empty_input(tmp_path: Path):
    assert _compress_references_to_tempfiles([]) == []


def test_render_unit_prompt_replaces_mentions_in_order():
    unit = {
        "shots": [
            {"duration": 3, "text": "Shot 1 (3s): @张三 推门"},
            {"duration": 5, "text": "Shot 2 (5s): 对面的 @张三 抬眼，背景是 @酒馆"},
        ],
        "references": [
            {"type": "character", "name": "张三"},
            {"type": "scene", "name": "酒馆"},
        ],
    }
    rendered = _render_unit_prompt(unit)
    assert "[图1]" in rendered
    assert "[图2]" in rendered
    assert "@张三" not in rendered
    # Shot header 保留
    assert "Shot 1 (3s):" in rendered
    assert "Shot 2 (5s):" in rendered


def test_apply_provider_constraints_veo_clamps_duration_and_refs():
    refs = [Path(f"/tmp/ref{i}.png") for i in range(5)]
    new_refs, new_duration, warnings = _apply_provider_constraints(
        provider="gemini",
        model="veo-3.1-generate-preview",
        references=refs,
        duration_seconds=12,
    )
    assert len(new_refs) == 3
    assert new_duration == 8
    assert any("ref_duration_exceeded" in w["key"] for w in warnings)
    assert any("ref_too_many_images" in w["key"] for w in warnings)


def test_apply_provider_constraints_sora_single_ref():
    refs = [Path(f"/tmp/ref{i}.png") for i in range(3)]
    new_refs, _, warnings = _apply_provider_constraints(
        provider="openai",
        model="sora-2",
        references=refs,
        duration_seconds=8,
    )
    assert len(new_refs) == 1
    assert any("ref_sora_single_ref" in w["key"] for w in warnings)


def test_apply_provider_constraints_ark_keeps_nine():
    refs = [Path(f"/tmp/ref{i}.png") for i in range(9)]
    new_refs, new_duration, warnings = _apply_provider_constraints(
        provider="ark",
        model="doubao-seedance-2-0-260128",
        references=refs,
        duration_seconds=12,
    )
    assert len(new_refs) == 9
    assert new_duration == 12
    assert warnings == []
```

- [ ] **Step 2：运行测试确认失败**

```bash
uv run pytest tests/server/test_reference_video_tasks.py -v
```

Expected：5 新增 FAIL（函数不存在）。

- [ ] **Step 3：实现三个辅助**

在 `server/services/reference_video_tasks.py` 追加（放在 `_resolve_unit_references` 之后、`execute_reference_video_task` 之前）：

```python
import tempfile

from lib.image_utils import compress_image_bytes
from lib.reference_video import render_prompt_for_backend
from lib.script_models import ReferenceResource

# 供应商能力上限（与 Spec §附录B + PROVIDER_REGISTRY 对齐）
_PROVIDER_LIMITS: dict[tuple[str, str | None], dict[str, int]] = {
    # (provider, model_prefix) → limits；None 代表同 provider 所有模型共享
    ("gemini", "veo"): {"max_refs": 3, "max_duration": 8},
    ("openai", "sora"): {"max_refs": 1, "max_duration": 12},
    ("grok", None): {"max_refs": 7, "max_duration": 15},
    ("ark", None): {"max_refs": 9, "max_duration": 15},
}


def _lookup_provider_limits(provider: str, model: str | None) -> dict[str, int]:
    """查找供应商 / 模型对应的参考图 + duration 上限。找不到返回空 dict（不裁剪）。"""
    provider = (provider or "").lower()
    model = (model or "").lower()
    for (p, prefix), limits in _PROVIDER_LIMITS.items():
        if p != provider:
            continue
        if prefix is None or model.startswith(prefix):
            return limits
    return {}


def _compress_references_to_tempfiles(
    source_paths: list[Path],
    *,
    long_edge: int = 2048,
    quality: int = 85,
) -> list[Path]:
    """把每张 sheet 压到 JPEG bytes 并写入 NamedTemporaryFile，返回 Path 列表。

    调用方须在 finally 里对每个返回 Path 调用 .unlink(missing_ok=True)。
    """
    temp_paths: list[Path] = []
    for src in source_paths:
        raw = src.read_bytes()
        compressed = compress_image_bytes(raw, max_long_edge=long_edge, quality=quality)
        tmp = tempfile.NamedTemporaryFile(
            prefix="refvid-",
            suffix=".jpg",
            delete=False,
        )
        try:
            tmp.write(compressed)
        finally:
            tmp.close()
        temp_paths.append(Path(tmp.name))
    return temp_paths


def _render_unit_prompt(unit: dict) -> str:
    """拼接 unit.shots[*].text 为单一 prompt，再用 shot_parser 把 @X 替成 [图N]。"""
    shots = unit.get("shots") or []
    raw = "\n".join(str(s.get("text", "")) for s in shots)
    references = [
        ReferenceResource(type=r["type"], name=r["name"])
        for r in (unit.get("references") or [])
    ]
    return render_prompt_for_backend(raw, references)


def _apply_provider_constraints(
    *,
    provider: str,
    model: str | None,
    references: list[Path],
    duration_seconds: int,
) -> tuple[list[Path], int, list[dict]]:
    """按供应商上限裁剪 references / duration；回传 warnings（i18n key + 参数）。"""
    warnings: list[dict] = []
    limits = _lookup_provider_limits(provider, model)

    new_duration = duration_seconds
    max_duration = limits.get("max_duration")
    if max_duration is not None and duration_seconds > max_duration:
        new_duration = max_duration
        warnings.append(
            {
                "key": "ref_duration_exceeded",
                "params": {
                    "duration": duration_seconds,
                    "model": model or provider,
                    "max_duration": max_duration,
                },
            }
        )

    new_refs = list(references)
    max_refs = limits.get("max_refs")
    if max_refs is not None and len(references) > max_refs:
        new_refs = references[:max_refs]
        # Sora 单图走专门的 warning key，其他走通用
        if provider.lower() == "openai" and (model or "").lower().startswith("sora") and max_refs == 1:
            warnings.append({"key": "ref_sora_single_ref", "params": {}})
        else:
            warnings.append(
                {
                    "key": "ref_too_many_images",
                    "params": {
                        "count": len(references),
                        "model": model or provider,
                        "max_count": max_refs,
                    },
                }
            )

    return new_refs, new_duration, warnings
```

- [ ] **Step 4：运行测试确认通过**

```bash
uv run pytest tests/server/test_reference_video_tasks.py -v
```

Expected：10 PASS。

- [ ] **Step 5：Commit**

```bash
git add server/services/reference_video_tasks.py tests/server/test_reference_video_tasks.py
git commit -m "feat(reference-video): add compression, prompt rendering, and provider constraint helpers"
```

---

## Task 7：Executor 主体 `execute_reference_video_task`

**Files:**
- Modify: `server/services/reference_video_tasks.py`
- Test: `tests/server/test_reference_video_tasks.py`

- [ ] **Step 1：追加主流程测试（mock MediaGenerator）**

在 `tests/server/test_reference_video_tasks.py` 末尾追加：

```python
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_execute_reference_video_task_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    proj_dir = _write_project(tmp_path)

    # Patch project_manager helpers
    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir

    def fake_load_script(_project_name, _filename):
        return json.loads((proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8"))

    fake_pm.load_script.side_effect = fake_load_script
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    # Mock generator.generate_video_async: 创建伪视频文件
    async def _fake_generate_video_async(**kwargs):
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00\x00\x00 ftypmp42")
        # (output_path, version, video_ref, video_uri)
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-17T10:00:00"}]}
    fake_video_backend = MagicMock()
    fake_video_backend.name = "ark"
    fake_video_backend.model = "doubao-seedance-2-0-260128"
    fake_generator._video_backend = fake_video_backend

    async def _fake_get_media_generator(*_args, **_kwargs):
        return fake_generator

    monkeypatch.setattr(rvt, "get_media_generator", _fake_get_media_generator)

    # Patch thumbnail extractor → success
    async def _fake_extract(*_a, **_k):
        return True

    monkeypatch.setattr(rvt, "extract_video_thumbnail", _fake_extract)

    result = await rvt.execute_reference_video_task(
        "demo",
        "E1U1",
        {"script_file": "scripts/episode_1.json"},
        user_id="u1",
    )
    assert result["resource_type"] == "reference_videos"
    assert result["resource_id"] == "E1U1"
    assert result["file_path"].endswith("E1U1.mp4")


@pytest.mark.asyncio
async def test_execute_reference_video_task_missing_reference_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    proj_dir = _write_project(tmp_path)
    (proj_dir / "characters" / "张三.png").unlink()

    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(
        (proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")
    )
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    with pytest.raises(MissingReferenceError):
        await rvt.execute_reference_video_task(
            "demo",
            "E1U1",
            {"script_file": "scripts/episode_1.json"},
            user_id="u1",
        )


@pytest.mark.asyncio
async def test_execute_reference_video_task_payload_too_large_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    proj_dir = _write_project(tmp_path)

    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(
        (proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")
    )
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    call_count = {"n": 0}

    async def _fake_generate_video_async(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RequestPayloadTooLargeError()
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-17T10:00:00"}]}
    fake_video_backend = MagicMock()
    fake_video_backend.name = "grok"
    fake_video_backend.model = "grok-imagine-video"
    fake_generator._video_backend = fake_video_backend

    async def _fake_get_media_generator(*_a, **_k):
        return fake_generator

    monkeypatch.setattr(rvt, "get_media_generator", _fake_get_media_generator)

    async def _fake_extract(*_a, **_k):
        return True

    monkeypatch.setattr(rvt, "extract_video_thumbnail", _fake_extract)

    result = await rvt.execute_reference_video_task(
        "demo",
        "E1U1",
        {"script_file": "scripts/episode_1.json"},
        user_id="u1",
    )
    assert call_count["n"] == 2
    assert result["resource_id"] == "E1U1"
```

- [ ] **Step 2：运行测试确认失败**

```bash
uv run pytest tests/server/test_reference_video_tasks.py -v
```

Expected：3 新增 FAIL（executor 仍抛 NotImplementedError）。

- [ ] **Step 3：实现主流程**

编辑 `server/services/reference_video_tasks.py`，把 `execute_reference_video_task` 的 `raise NotImplementedError` 替换为完整实现。先在文件顶部 import 区追加：

```python
import asyncio
import contextlib

from lib.reference_video.errors import RequestPayloadTooLargeError
from lib.thumbnail import extract_video_thumbnail
from server.services.generation_tasks import get_media_generator, get_project_manager
```

然后替换 `execute_reference_video_task`：

```python
async def execute_reference_video_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
) -> dict[str, Any]:
    """处理一个 reference_video unit 的生成。

    resource_id 即 unit_id（E{集}U{序号}）。
    """
    script_file = payload.get("script_file")
    if not script_file:
        raise ValueError("script_file is required for reference_video task")

    # 1. 加载上下文（阻塞 IO，线程池）
    def _load():
        pm = get_project_manager()
        project = pm.load_project(project_name)
        project_path = pm.get_project_path(project_name)
        script = pm.load_script(project_name, script_file)
        units = script.get("video_units") or []
        unit = next((u for u in units if u.get("unit_id") == resource_id), None)
        if unit is None:
            raise ValueError(f"unit not found: {resource_id}")
        return project, project_path, unit

    project, project_path, unit = await asyncio.to_thread(_load)

    # 2. 解析 references（缺图直接失败）
    source_refs = _resolve_unit_references(project, project_path, unit.get("references") or [])

    # 3. 构造 generator（拿到 video_backend 名字后才能做 provider 特判）
    generator = await get_media_generator(project_name, payload=payload, user_id=user_id)
    backend = getattr(generator, "_video_backend", None)
    provider_name = getattr(backend, "name", "") if backend else ""
    model_name = getattr(backend, "model", "") if backend else ""

    # 4. Provider 特判：裁 refs + duration
    base_duration = int(unit.get("duration_seconds") or 8)
    constrained_refs, effective_duration, warnings = _apply_provider_constraints(
        provider=provider_name,
        model=model_name,
        references=source_refs,
        duration_seconds=base_duration,
    )

    # 5. 渲染 prompt（@→[图N]）
    rendered_prompt = _render_unit_prompt(unit)

    # 6. 压缩到临时文件（2048px/q=85）→ 首次调用
    tmp_refs: list[Path] = await asyncio.to_thread(
        _compress_references_to_tempfiles, constrained_refs
    )
    output_path: Path | None = None
    version = 0
    video_uri: str | None = None
    try:
        try:
            output_path, version, _, video_uri = await generator.generate_video_async(
                prompt=rendered_prompt,
                resource_type="reference_videos",
                resource_id=resource_id,
                reference_images=tmp_refs,
                aspect_ratio=project.get("aspect_ratio", "9:16"),
                duration_seconds=effective_duration,
            )
        except RequestPayloadTooLargeError:
            # 二次压缩重试（1024px/q=70）
            for p in tmp_refs:
                p.unlink(missing_ok=True)
            tmp_refs = await asyncio.to_thread(
                _compress_references_to_tempfiles,
                constrained_refs,
                long_edge=1024,
                quality=70,
            )
            warnings.append({"key": "ref_payload_too_large", "params": {}})
            output_path, version, _, video_uri = await generator.generate_video_async(
                prompt=rendered_prompt,
                resource_type="reference_videos",
                resource_id=resource_id,
                reference_images=tmp_refs,
                aspect_ratio=project.get("aspect_ratio", "9:16"),
                duration_seconds=effective_duration,
            )
    finally:
        for p in tmp_refs:
            with contextlib.suppress(Exception):
                p.unlink(missing_ok=True)

    # 7. 首帧缩略图
    assert output_path is not None
    thumb_dir = project_path / "reference_videos" / "thumbnails"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    thumb_path = thumb_dir / f"{resource_id}.jpg"
    if await extract_video_thumbnail(output_path, thumb_path):
        thumb_rel = f"reference_videos/thumbnails/{resource_id}.jpg"
    else:
        thumb_path.unlink(missing_ok=True)
        thumb_rel = None

    # 8. 更新 unit.generated_assets（简单读改写 episode script）
    def _update_unit_assets():
        pm = get_project_manager()
        script = pm.load_script(project_name, script_file)
        for u in script.get("video_units") or []:
            if u.get("unit_id") == resource_id:
                ga = u.setdefault("generated_assets", {})
                ga["video_clip"] = f"reference_videos/{resource_id}.mp4"
                if video_uri:
                    ga["video_uri"] = video_uri
                if thumb_rel:
                    ga["video_thumbnail"] = thumb_rel
                ga["status"] = "completed"
                break
        pm.save_script(project_name, script, script_file)
        return script

    await asyncio.to_thread(_update_unit_assets)

    created_at = await asyncio.to_thread(
        lambda: generator.versions.get_versions("reference_videos", resource_id)["versions"][-1]["created_at"]
    )

    return {
        "version": version,
        "file_path": f"reference_videos/{resource_id}.mp4",
        "created_at": created_at,
        "resource_type": "reference_videos",
        "resource_id": resource_id,
        "video_uri": video_uri,
        "warnings": warnings,
    }
```

- [ ] **Step 4：运行测试确认通过**

```bash
uv run pytest tests/server/test_reference_video_tasks.py -v
```

Expected：13 PASS。

- [ ] **Step 5：Commit**

```bash
git add server/services/reference_video_tasks.py tests/server/test_reference_video_tasks.py
git commit -m "feat(reference-video): implement execute_reference_video_task with payload-too-large retry"
```

---

## Task 8：Worker dispatch 注册 + 项目事件

**Files:**
- Modify: `server/services/generation_tasks.py`
- Test: `tests/test_generation_tasks_dispatch.py`（新增，或按现有文件追加）

- [ ] **Step 1：写失败测试**

创建 `tests/test_generation_tasks_dispatch.py`：

```python
from __future__ import annotations

import pytest

from server.services.generation_tasks import _TASK_CHANGE_SPECS, _TASK_EXECUTORS


def test_task_executors_registered_for_reference_video():
    assert "reference_video" in _TASK_EXECUTORS


def test_task_change_specs_registered_for_reference_video():
    spec = _TASK_CHANGE_SPECS.get("reference_video")
    assert spec is not None
    entity_type, action, _label_tpl, include_script_episode = spec
    assert entity_type == "reference_video_unit"
    assert action == "reference_video_ready"
    assert include_script_episode is True


@pytest.mark.asyncio
async def test_execute_generation_task_rejects_unknown_type():
    from server.services.generation_tasks import execute_generation_task

    with pytest.raises(ValueError, match="unsupported task_type"):
        await execute_generation_task(
            {
                "task_type": "unknown_xyz",
                "project_name": "demo",
                "resource_id": "x",
                "payload": {},
            }
        )
```

- [ ] **Step 2：运行测试确认失败**

```bash
uv run pytest tests/test_generation_tasks_dispatch.py -v
```

Expected：2 FAIL（`reference_video` 未注册）+ 1 PASS（现有 unknown_type 校验）。

- [ ] **Step 3：注册 executor 与事件 spec**

编辑 `server/services/generation_tasks.py`：

1. 在文件顶部其他 import 附近加：

```python
from server.services.reference_video_tasks import execute_reference_video_task
```

2. 找到 `_TASK_CHANGE_SPECS = {...}`（约 line 577），在末尾 `"grid": ...` 之后加一行：

```python
    "reference_video": ("reference_video_unit", "reference_video_ready", "参考视频「{}」", True),
```

3. 找到 `_TASK_EXECUTORS = {...}`（约 line 1212），在末尾 `"grid": execute_grid_task,` 之后加：

```python
    "reference_video": execute_reference_video_task,
```

4. 在 `_compute_affected_fingerprints` 中，对 `task_type == "reference_video"` 加分支（约 line 526 之后追加）：

```python
    elif task_type == "reference_video":
        paths.append(
            (
                f"reference_videos/{resource_id}.mp4",
                project_path / "reference_videos" / f"{resource_id}.mp4",
            )
        )
        paths.append(
            (
                f"reference_videos/thumbnails/{resource_id}.jpg",
                project_path / "reference_videos" / "thumbnails" / f"{resource_id}.jpg",
            )
        )
```

- [ ] **Step 4：运行测试确认通过**

```bash
uv run pytest tests/test_generation_tasks_dispatch.py -v
```

Expected：3 PASS。

- [ ] **Step 5：Commit**

```bash
git add server/services/generation_tasks.py tests/test_generation_tasks_dispatch.py
git commit -m "feat(worker): register reference_video task_type dispatch + change event spec"
```

---

## Task 9：路由骨架（GET list + POST add）

**Files:**
- Create: `server/routers/reference_videos.py`
- Test: `tests/server/test_reference_videos_router.py`

- [ ] **Step 1：写失败测试（GET list / POST add）**

创建 `tests/server/test_reference_videos_router.py`：

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib import PROJECT_ROOT


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # 重定向 projects_root 到 tmp_path
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    proj_dir = projects_root / "demo"
    proj_dir.mkdir()
    (proj_dir / "scripts").mkdir()
    (proj_dir / "project.json").write_text(
        json.dumps(
            {
                "title": "T",
                "content_mode": "reference_video",
                "generation_mode": "reference_video",
                "style": "s",
                "characters": {"张三": {"description": "x"}},
                "scenes": {"酒馆": {"description": "x"}},
                "props": {},
                "episodes": [{"episode": 1, "title": "E1", "script_file": "scripts/episode_1.json"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (proj_dir / "scripts" / "episode_1.json").write_text(
        json.dumps(
            {
                "episode": 1,
                "title": "E1",
                "content_mode": "reference_video",
                "summary": "x",
                "novel": {"title": "t", "chapter": "c"},
                "duration_seconds": 0,
                "video_units": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # Patch project_manager 的根目录
    from lib.project_manager import ProjectManager
    from server.routers import reference_videos as router_mod

    custom_pm = ProjectManager(projects_root)
    monkeypatch.setattr(router_mod, "pm", custom_pm)
    monkeypatch.setattr(router_mod, "get_project_manager", lambda: custom_pm)

    # Bypass auth
    from server.auth import CurrentUser, User

    async def _fake_user() -> User:
        return User(id="u1", username="test", role="admin")

    app = FastAPI()
    app.include_router(router_mod.router, prefix="/api/v1")
    app.dependency_overrides[CurrentUser] = _fake_user
    return TestClient(app)


def test_list_units_empty(client: TestClient):
    resp = client.get("/api/v1/projects/demo/reference-videos/episodes/1/units")
    assert resp.status_code == 200
    assert resp.json() == {"units": []}


def test_list_units_404_for_unknown_project(client: TestClient):
    resp = client.get("/api/v1/projects/missing/reference-videos/episodes/1/units")
    assert resp.status_code == 404


def test_add_unit_creates_minimal_entry(client: TestClient):
    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={"prompt": "Shot 1 (3s): @张三 推门", "references": [{"type": "character", "name": "张三"}]},
    )
    assert resp.status_code == 201, resp.text
    payload = resp.json()
    assert payload["unit"]["unit_id"].startswith("E1U")
    assert payload["unit"]["duration_seconds"] == 3
    assert payload["unit"]["references"] == [{"type": "character", "name": "张三"}]


def test_add_unit_rejects_unknown_asset_reference(client: TestClient):
    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={"prompt": "Shot 1 (2s): @未知角色 出现", "references": [{"type": "character", "name": "未知角色"}]},
    )
    assert resp.status_code == 400
    assert "未知角色" in resp.text
```

- [ ] **Step 2：运行测试确认失败**

```bash
uv run pytest tests/server/test_reference_videos_router.py -v
```

Expected：FAIL（路由不存在）。

- [ ] **Step 3：创建路由骨架**

创建 `server/routers/reference_videos.py`：

```python
"""参考生视频 CRUD + 生成路由。

Spec: docs/superpowers/specs/2026-04-15-reference-to-video-mode-design.md §5.1
Mount prefix: /api/v1/projects/{project_name}/reference-videos
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field

from lib import PROJECT_ROOT
from lib.asset_types import BUCKET_KEY
from lib.project_manager import ProjectManager
from lib.reference_video import parse_prompt
from server.auth import CurrentUser

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/projects/{project_name}/reference-videos",
    tags=["reference-videos"],
)

pm = ProjectManager(PROJECT_ROOT / "projects")


def get_project_manager() -> ProjectManager:
    return pm


# ============ 请求模型 ============


class ReferenceDto(BaseModel):
    type: str = Field(pattern=r"^(character|scene|prop)$")
    name: str


class AddUnitRequest(BaseModel):
    prompt: str
    references: list[ReferenceDto] = Field(default_factory=list)
    duration_seconds: int | None = None
    transition_to_next: str = Field(default="cut", pattern=r"^(cut|fade|dissolve)$")
    note: str | None = None


# ============ 辅助 ============


def _load_episode_script(project_name: str, episode: int) -> tuple[dict, dict, str]:
    """加载 project.json + 指定集的剧本。返回 (project, script, script_file)。"""
    project = get_project_manager().load_project(project_name)
    episodes = project.get("episodes") or []
    meta = next((e for e in episodes if e.get("episode") == episode), None)
    if meta is None or not meta.get("script_file"):
        raise HTTPException(status_code=404, detail=f"episode {episode} not found")
    script_file = meta["script_file"]
    try:
        script = get_project_manager().load_script(project_name, script_file)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if script.get("content_mode") != "reference_video":
        raise HTTPException(
            status_code=409,
            detail="episode script is not in reference_video mode",
        )
    return project, script, script_file


def _validate_references_exist(project: dict, refs: list[dict]) -> None:
    """确保 references 都在 project.json 对应 bucket 中。"""
    missing: list[str] = []
    for r in refs:
        bucket = project.get(BUCKET_KEY.get(r["type"], "")) or {}
        if r["name"] not in bucket:
            missing.append(f"{r['type']}:{r['name']}")
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"references not registered: {', '.join(missing)}",
        )


def _next_unit_id(script: dict, episode: int) -> str:
    existing = {str(u.get("unit_id", "")) for u in (script.get("video_units") or [])}
    idx = 1
    while f"E{episode}U{idx}" in existing:
        idx += 1
    return f"E{episode}U{idx}"


def _build_unit_dict(
    *,
    unit_id: str,
    prompt: str,
    references: list[dict],
    duration_override: int | None,
    transition: str,
    note: str | None,
) -> dict:
    shots, _names, override = parse_prompt(prompt)
    if override and duration_override is not None:
        shots[0].duration = max(1, int(duration_override))
    duration_total = sum(s.duration for s in shots)
    return {
        "unit_id": unit_id,
        "shots": [s.model_dump() for s in shots],
        "references": references,
        "duration_seconds": duration_total,
        "duration_override": override,
        "transition_to_next": transition,
        "note": note,
        "generated_assets": {
            "storyboard_image": None,
            "storyboard_last_image": None,
            "grid_id": None,
            "grid_cell_index": None,
            "video_clip": None,
            "video_uri": None,
            "status": "pending",
        },
    }


# ============ 端点：列出 + 新建 ============


@router.get("/episodes/{episode}/units")
async def list_units(project_name: str, episode: int, _user: CurrentUser) -> dict[str, Any]:
    try:
        _project, script, _sf = _load_episode_script(project_name, episode)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"units": script.get("video_units") or []}


@router.post("/episodes/{episode}/units", status_code=status.HTTP_201_CREATED)
async def add_unit(
    project_name: str,
    episode: int,
    req: AddUnitRequest,
    _user: CurrentUser,
) -> dict[str, Any]:
    try:
        project, script, script_file = _load_episode_script(project_name, episode)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    refs = [r.model_dump() for r in req.references]
    _validate_references_exist(project, refs)

    unit = _build_unit_dict(
        unit_id=_next_unit_id(script, episode),
        prompt=req.prompt,
        references=refs,
        duration_override=req.duration_seconds,
        transition=req.transition_to_next,
        note=req.note,
    )
    script.setdefault("video_units", []).append(unit)
    get_project_manager().save_script(project_name, script, script_file)
    return {"unit": unit}
```

- [ ] **Step 4：运行测试确认通过**

```bash
uv run pytest tests/server/test_reference_videos_router.py -v
```

Expected：4 PASS。

- [ ] **Step 5：Commit**

```bash
git add server/routers/reference_videos.py tests/server/test_reference_videos_router.py
git commit -m "feat(reference-video-router): add list_units and add_unit endpoints"
```

---

## Task 10：PATCH + DELETE 端点

**Files:**
- Modify: `server/routers/reference_videos.py`
- Test: `tests/server/test_reference_videos_router.py`

- [ ] **Step 1：追加失败测试**

在 `tests/server/test_reference_videos_router.py` 末尾追加：

```python
def _seed_unit(client: TestClient) -> str:
    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={"prompt": "Shot 1 (3s): @张三 推门", "references": [{"type": "character", "name": "张三"}]},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["unit"]["unit_id"]


def test_patch_unit_prompt_recomputes_duration(client: TestClient):
    uid = _seed_unit(client)
    resp = client.patch(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}",
        json={"prompt": "Shot 1 (4s): @张三 推门\nShot 2 (6s): @酒馆 全景"},
    )
    assert resp.status_code == 200, resp.text
    unit = resp.json()["unit"]
    assert unit["duration_seconds"] == 10
    # 注意：prompt 新增的 @酒馆 应由 caller 先 PATCH references 再 PATCH prompt；本端点仅按旧 references 映射
    assert len(unit["references"]) == 1


def test_patch_unit_references_only(client: TestClient):
    uid = _seed_unit(client)
    resp = client.patch(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}",
        json={"references": [
            {"type": "character", "name": "张三"},
            {"type": "scene", "name": "酒馆"},
        ]},
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["unit"]["references"]) == 2


def test_patch_unit_rejects_unknown_reference(client: TestClient):
    uid = _seed_unit(client)
    resp = client.patch(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}",
        json={"references": [{"type": "prop", "name": "不存在"}]},
    )
    assert resp.status_code == 400


def test_patch_unknown_unit_404(client: TestClient):
    resp = client.patch(
        "/api/v1/projects/demo/reference-videos/episodes/1/units/E9U9",
        json={"note": "hi"},
    )
    assert resp.status_code == 404


def test_delete_unit_removes_entry(client: TestClient):
    uid = _seed_unit(client)
    resp = client.delete(f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}")
    assert resp.status_code == 204
    resp = client.get("/api/v1/projects/demo/reference-videos/episodes/1/units")
    assert resp.json()["units"] == []


def test_delete_unknown_unit_404(client: TestClient):
    resp = client.delete("/api/v1/projects/demo/reference-videos/episodes/1/units/E9U9")
    assert resp.status_code == 404
```

- [ ] **Step 2：运行测试确认失败**

```bash
uv run pytest tests/server/test_reference_videos_router.py -v
```

Expected：6 新增 FAIL。

- [ ] **Step 3：实现 PATCH + DELETE**

在 `server/routers/reference_videos.py` 末尾追加：

```python
class PatchUnitRequest(BaseModel):
    prompt: str | None = None
    references: list[ReferenceDto] | None = None
    duration_seconds: int | None = None
    transition_to_next: str | None = Field(default=None, pattern=r"^(cut|fade|dissolve)$")
    note: str | None = None


def _find_unit(script: dict, unit_id: str) -> dict:
    for u in script.get("video_units") or []:
        if u.get("unit_id") == unit_id:
            return u
    raise HTTPException(status_code=404, detail=f"unit {unit_id} not found")


@router.patch("/episodes/{episode}/units/{unit_id}")
async def patch_unit(
    project_name: str,
    episode: int,
    unit_id: str,
    req: PatchUnitRequest,
    _user: CurrentUser,
) -> dict[str, Any]:
    project, script, script_file = _load_episode_script(project_name, episode)
    unit = _find_unit(script, unit_id)

    if req.references is not None:
        refs = [r.model_dump() for r in req.references]
        _validate_references_exist(project, refs)
        unit["references"] = refs

    if req.prompt is not None:
        shots, _mentions, override = parse_prompt(req.prompt)
        if override and req.duration_seconds is not None:
            shots[0].duration = max(1, int(req.duration_seconds))
        unit["shots"] = [s.model_dump() for s in shots]
        unit["duration_seconds"] = sum(s.duration for s in shots)
        unit["duration_override"] = override
    elif req.duration_seconds is not None and unit.get("duration_override"):
        unit["duration_seconds"] = max(1, int(req.duration_seconds))
        if unit.get("shots"):
            unit["shots"][0]["duration"] = unit["duration_seconds"]

    if req.transition_to_next is not None:
        unit["transition_to_next"] = req.transition_to_next
    if req.note is not None:
        unit["note"] = req.note

    get_project_manager().save_script(project_name, script, script_file)
    return {"unit": unit}


@router.delete("/episodes/{episode}/units/{unit_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_unit(
    project_name: str,
    episode: int,
    unit_id: str,
    _user: CurrentUser,
) -> Response:
    _project, script, script_file = _load_episode_script(project_name, episode)
    units = script.get("video_units") or []
    new_units = [u for u in units if u.get("unit_id") != unit_id]
    if len(new_units) == len(units):
        raise HTTPException(status_code=404, detail=f"unit {unit_id} not found")
    script["video_units"] = new_units
    get_project_manager().save_script(project_name, script, script_file)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
```

- [ ] **Step 4：运行测试确认通过**

```bash
uv run pytest tests/server/test_reference_videos_router.py -v
```

Expected：10 PASS。

- [ ] **Step 5：Commit**

```bash
git add server/routers/reference_videos.py tests/server/test_reference_videos_router.py
git commit -m "feat(reference-video-router): add patch_unit and delete_unit endpoints"
```

---

## Task 11：reorder + generate 端点

**Files:**
- Modify: `server/routers/reference_videos.py`
- Test: `tests/server/test_reference_videos_router.py`

- [ ] **Step 1：追加失败测试**

在 `tests/server/test_reference_videos_router.py` 末尾追加：

```python
def test_reorder_units_applies_new_order(client: TestClient):
    uid1 = _seed_unit(client)
    uid2 = _seed_unit(client)
    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units/reorder",
        json={"unit_ids": [uid2, uid1]},
    )
    assert resp.status_code == 200, resp.text
    units = client.get("/api/v1/projects/demo/reference-videos/episodes/1/units").json()["units"]
    assert [u["unit_id"] for u in units] == [uid2, uid1]


def test_reorder_units_rejects_length_mismatch(client: TestClient):
    uid = _seed_unit(client)
    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units/reorder",
        json={"unit_ids": [uid, "E1U999"]},
    )
    assert resp.status_code == 400


def test_reorder_units_rejects_duplicates(client: TestClient):
    uid = _seed_unit(client)
    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units/reorder",
        json={"unit_ids": [uid, uid]},
    )
    assert resp.status_code == 400


def test_generate_unit_enqueues_task(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    uid = _seed_unit(client)

    enqueued: list[dict] = []

    class _FakeQueue:
        async def enqueue_task(self, **kwargs):
            enqueued.append(kwargs)
            return {"task_id": "task-xyz", "deduped": False}

    from server.routers import reference_videos as router_mod

    monkeypatch.setattr(router_mod, "get_generation_queue", lambda: _FakeQueue())

    resp = client.post(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}/generate"
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["task_id"] == "task-xyz"
    assert enqueued[0]["task_type"] == "reference_video"
    assert enqueued[0]["media_type"] == "video"
    assert enqueued[0]["resource_id"] == uid


def test_generate_unit_missing_returns_404(client: TestClient):
    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units/E9U9/generate"
    )
    assert resp.status_code == 404
```

- [ ] **Step 2：运行测试确认失败**

```bash
uv run pytest tests/server/test_reference_videos_router.py -v
```

Expected：5 新增 FAIL。

- [ ] **Step 3：实现 reorder + generate**

在 `server/routers/reference_videos.py` 顶部 import 追加：

```python
from lib.generation_queue import get_generation_queue
```

在文件末尾追加：

```python
class ReorderRequest(BaseModel):
    unit_ids: list[str]


@router.post("/episodes/{episode}/units/reorder")
async def reorder_units(
    project_name: str,
    episode: int,
    req: ReorderRequest,
    _user: CurrentUser,
) -> dict[str, Any]:
    _project, script, script_file = _load_episode_script(project_name, episode)
    units = script.get("video_units") or []
    existing_ids = [u.get("unit_id") for u in units]

    if len(req.unit_ids) != len(existing_ids):
        raise HTTPException(status_code=400, detail="unit_ids length mismatch")
    if len(set(req.unit_ids)) != len(req.unit_ids):
        raise HTTPException(status_code=400, detail="duplicate unit_ids")
    if set(req.unit_ids) != set(existing_ids):
        raise HTTPException(status_code=400, detail="unit_ids do not match existing units")

    by_id = {u["unit_id"]: u for u in units}
    script["video_units"] = [by_id[uid] for uid in req.unit_ids]
    get_project_manager().save_script(project_name, script, script_file)
    return {"units": script["video_units"]}


@router.post(
    "/episodes/{episode}/units/{unit_id}/generate",
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_unit(
    project_name: str,
    episode: int,
    unit_id: str,
    _user: CurrentUser,
) -> dict[str, Any]:
    _project, script, script_file = _load_episode_script(project_name, episode)
    _unit = _find_unit(script, unit_id)  # raises 404 if missing

    queue = get_generation_queue()
    result = await queue.enqueue_task(
        project_name=project_name,
        task_type="reference_video",
        media_type="video",
        resource_id=unit_id,
        payload={"script_file": script_file},
        script_file=script_file,
        source="webui",
        user_id=_user.id,
    )
    return {"task_id": result["task_id"], "deduped": result.get("deduped", False)}
```

- [ ] **Step 4：运行测试确认通过**

```bash
uv run pytest tests/server/test_reference_videos_router.py -v
```

Expected：15 PASS。

- [ ] **Step 5：Commit**

```bash
git add server/routers/reference_videos.py tests/server/test_reference_videos_router.py
git commit -m "feat(reference-video-router): add reorder and generate endpoints"
```

---

## Task 12：挂载路由 + 归档扩展

**Files:**
- Modify: `server/app.py`
- Modify: `server/services/project_archive.py`
- Test: `tests/test_project_archive_service.py`（现有）

- [ ] **Step 1：在 app.py 挂载路由**

编辑 `server/app.py`：

1. 在 `from server.routers import (...)` 块末尾加 `reference_videos,`。
2. 在路由注册段末尾（`app.include_router(assets.router, ...)` 之后）追加：

```python
app.include_router(reference_videos.router, prefix="/api/v1", tags=["参考生视频"])
```

- [ ] **Step 2：扩展 archive 资源表**

编辑 `server/services/project_archive.py`：

1. 更新 `_VERSION_HISTORY_DIRS`：

```python
    _VERSION_HISTORY_DIRS = frozenset(
        {
            "storyboards",
            "videos",
            "characters",
            "scenes",
            "props",
            "reference_videos",
        }
    )
```

2. 更新 `_RESOURCE_EXTENSIONS`：

```python
    _RESOURCE_EXTENSIONS = {
        "storyboards": ".png",
        "videos": ".mp4",
        "characters": ".png",
        "scenes": ".png",
        "props": ".png",
        "reference_videos": ".mp4",
    }
```

3. 在 `_canonical_resource_path`（文件约 line 1077）里把 `reference_videos` 与 `videos` / `storyboards` 一样走 `scene_` 前缀以外的分支（参考模式用 unit_id 作文件名，无前缀）：

当前代码：
```python
if resource_type in {"storyboards", "videos"}:
    return f"{resource_type}/scene_{resource_id}{extension}"
return f"{resource_type}/{resource_id}{extension}"
```

无需改动——`reference_videos` 会走第二分支，与设计一致（`reference_videos/E1U1.mp4`）。

- [ ] **Step 3：启动应用自检**

```bash
uv run python -c "from server.app import app; [print(r.path) for r in app.routes if 'reference-videos' in getattr(r, 'path', '')]"
```

Expected：列出 6 条路径：
```
/api/v1/projects/{project_name}/reference-videos/episodes/{episode}/units (GET, POST)
/api/v1/projects/{project_name}/reference-videos/episodes/{episode}/units/{unit_id} (PATCH, DELETE)
/api/v1/projects/{project_name}/reference-videos/episodes/{episode}/units/reorder (POST)
/api/v1/projects/{project_name}/reference-videos/episodes/{episode}/units/{unit_id}/generate (POST)
```

- [ ] **Step 4：回归 archive 测试**

```bash
uv run pytest tests/test_project_archive_service.py -v
```

Expected：全绿（仅加 key，未改行为；新 `reference_videos` key 对已有 fixture 无影响）。

- [ ] **Step 5：Commit**

```bash
git add server/app.py server/services/project_archive.py
git commit -m "feat(app): mount reference_videos router and extend archive with reference_videos resource"
```

---

## Task 13：端到端集成测试 + 回归

**Files:**
- Create: `tests/server/test_reference_video_e2e_backend.py`

- [ ] **Step 1：写端到端测试**

创建 `tests/server/test_reference_video_e2e_backend.py`：

```python
"""参考视频后端端到端：路由 → queue → executor（mock backend）。

本测试把路由 `POST .../generate` → GenerationQueue enqueue → 手动 claim →
`execute_generation_task` dispatch 到 `execute_reference_video_task` 串起来。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def seeded_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Path]:
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    proj_dir = projects_root / "demo"
    proj_dir.mkdir()
    (proj_dir / "scripts").mkdir()
    (proj_dir / "characters").mkdir()
    (proj_dir / "scenes").mkdir()
    (proj_dir / "characters" / "张三.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (proj_dir / "scenes" / "酒馆.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    (proj_dir / "project.json").write_text(
        json.dumps(
            {
                "title": "T",
                "content_mode": "reference_video",
                "generation_mode": "reference_video",
                "style": "s",
                "characters": {"张三": {"description": "x", "character_sheet": "characters/张三.png"}},
                "scenes": {"酒馆": {"description": "x", "scene_sheet": "scenes/酒馆.png"}},
                "props": {},
                "episodes": [{"episode": 1, "title": "E1", "script_file": "scripts/episode_1.json"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (proj_dir / "scripts" / "episode_1.json").write_text(
        json.dumps(
            {
                "episode": 1,
                "title": "E1",
                "content_mode": "reference_video",
                "summary": "x",
                "novel": {"title": "t", "chapter": "c"},
                "duration_seconds": 0,
                "video_units": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    from lib.project_manager import ProjectManager
    from server.routers import reference_videos as router_mod

    custom_pm = ProjectManager(projects_root)
    monkeypatch.setattr(router_mod, "pm", custom_pm)
    monkeypatch.setattr(router_mod, "get_project_manager", lambda: custom_pm)

    from server.services import generation_tasks as gt_mod
    from server.services import reference_video_tasks as rvt_mod

    monkeypatch.setattr(gt_mod, "pm", custom_pm)
    monkeypatch.setattr(gt_mod, "get_project_manager", lambda: custom_pm)
    monkeypatch.setattr(rvt_mod, "get_project_manager", lambda: custom_pm)

    # Bypass auth
    from server.auth import CurrentUser, User

    async def _fake_user() -> User:
        return User(id="u1", username="test", role="admin")

    app = FastAPI()
    app.include_router(router_mod.router, prefix="/api/v1")
    app.dependency_overrides[CurrentUser] = _fake_user
    return TestClient(app), proj_dir


@pytest.mark.asyncio
async def test_end_to_end_generate_unit_to_executor(
    seeded_client: tuple[TestClient, Path], monkeypatch: pytest.MonkeyPatch
):
    client, proj_dir = seeded_client

    # 1) 建 unit
    resp = client.post(
        "/api/v1/projects/demo/reference-videos/episodes/1/units",
        json={
            "prompt": "Shot 1 (3s): @张三 推门进 @酒馆",
            "references": [
                {"type": "character", "name": "张三"},
                {"type": "scene", "name": "酒馆"},
            ],
        },
    )
    uid = resp.json()["unit"]["unit_id"]

    # 2) Patch GenerationQueue.enqueue_task 直接返回 task dict（跳过 DB）
    captured_payload: dict = {}

    async def _fake_enqueue(**kwargs):
        captured_payload.update(kwargs)
        return {"task_id": "t1", "deduped": False}

    from server.routers import reference_videos as router_mod

    fake_queue = MagicMock()
    fake_queue.enqueue_task = AsyncMock(side_effect=_fake_enqueue)
    monkeypatch.setattr(router_mod, "get_generation_queue", lambda: fake_queue)

    resp = client.post(
        f"/api/v1/projects/demo/reference-videos/episodes/1/units/{uid}/generate"
    )
    assert resp.status_code == 202
    assert captured_payload["task_type"] == "reference_video"
    assert captured_payload["resource_id"] == uid

    # 3) Mock get_media_generator → 直接执行 executor
    from server.services import reference_video_tasks as rvt_mod

    async def _fake_generate_video_async(**kwargs):
        out = proj_dir / "reference_videos" / f"{uid}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00\x00\x00 ftypmp42")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-17T12:00:00"}]}
    fake_video_backend = MagicMock()
    fake_video_backend.name = "ark"
    fake_video_backend.model = "doubao-seedance-2-0-260128"
    fake_generator._video_backend = fake_video_backend

    async def _fake_get_media_generator(*_a, **_k):
        return fake_generator

    monkeypatch.setattr(rvt_mod, "get_media_generator", _fake_get_media_generator)

    async def _fake_extract(*_a, **_k):
        return True

    monkeypatch.setattr(rvt_mod, "extract_video_thumbnail", _fake_extract)

    # 4) Dispatch 到 execute_generation_task
    from server.services.generation_tasks import execute_generation_task

    result = await execute_generation_task(
        {
            "task_type": "reference_video",
            "project_name": "demo",
            "resource_id": uid,
            "payload": {"script_file": "scripts/episode_1.json"},
            "user_id": "u1",
        }
    )
    assert result["resource_id"] == uid
    assert result["file_path"].endswith(f"{uid}.mp4")

    # 5) 校验 unit.generated_assets 已更新
    script = json.loads((proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8"))
    u = next(u for u in script["video_units"] if u["unit_id"] == uid)
    assert u["generated_assets"]["status"] == "completed"
    assert u["generated_assets"]["video_clip"] == f"reference_videos/{uid}.mp4"
```

- [ ] **Step 2：运行端到端测试**

```bash
uv run pytest tests/server/test_reference_video_e2e_backend.py -v
```

Expected：1 PASS。

- [ ] **Step 3：回归所有新增 + 相关测试**

```bash
uv run pytest tests/server/test_reference_videos_router.py tests/server/test_reference_video_tasks.py tests/server/test_reference_video_e2e_backend.py tests/lib/test_image_compression_batch.py tests/lib/test_cost_calculator_reference_video.py tests/lib/test_reference_video_errors.py tests/test_generation_tasks_dispatch.py tests/test_i18n_consistency.py -v
```

Expected：全绿（≥30 测试）。

- [ ] **Step 4：覆盖率检查**

```bash
uv run pytest tests/server/test_reference_videos_router.py tests/server/test_reference_video_tasks.py tests/server/test_reference_video_e2e_backend.py --cov=server.services.reference_video_tasks --cov=server.routers.reference_videos --cov=lib.reference_video --cov-report=term-missing
```

Expected：`server/services/reference_video_tasks.py` 覆盖率 ≥ 90%；`server/routers/reference_videos.py` ≥ 90%。

- [ ] **Step 5：Commit**

```bash
git add tests/server/test_reference_video_e2e_backend.py
git commit -m "test(reference-video): add router → queue → executor end-to-end test"
```

---

## Task 14：PR 收尾 — lint + 回归 + PR

- [ ] **Step 1：lint + format**

```bash
uv run ruff check lib/reference_video/ lib/cost_calculator.py lib/i18n/ server/routers/reference_videos.py server/services/reference_video_tasks.py server/services/generation_tasks.py server/services/project_archive.py server/app.py tests/server/ tests/test_generation_tasks_dispatch.py tests/lib/test_image_compression_batch.py tests/lib/test_cost_calculator_reference_video.py tests/lib/test_reference_video_errors.py
uv run ruff format lib/reference_video/ lib/cost_calculator.py lib/i18n/ server/routers/reference_videos.py server/services/reference_video_tasks.py server/services/generation_tasks.py server/services/project_archive.py server/app.py tests/server/ tests/test_generation_tasks_dispatch.py tests/lib/test_image_compression_batch.py tests/lib/test_cost_calculator_reference_video.py tests/lib/test_reference_video_errors.py
```

Expected：干净。

- [ ] **Step 2：全量回归**

```bash
uv run pytest tests/ -x --ignore=tests/integration
```

Expected：全绿。如果 `test_project_archive_service.py` 因 `_VERSION_HISTORY_DIRS` 扩展而挂（通常不会，因为新条目只影响归档行为，不影响现有归档断言），按报错最小化修复。

- [ ] **Step 3：确认路由表**

```bash
uv run python -c "
from server.app import app
for r in app.routes:
    p = getattr(r, 'path', '')
    if 'reference-videos' in p:
        methods = getattr(r, 'methods', set())
        print(f'{sorted(methods)} {p}')
"
```

Expected：6 行，对应 list/add/patch/delete/reorder/generate。

- [ ] **Step 4：更新 roadmap**

编辑 `docs/superpowers/plans/2026-04-17-reference-to-video-roadmap.md` 的 "里程碑追踪" 段，保留 `- [ ] PR3 合并（后端可通过 curl 调 /reference-videos/...）`（留给合并时勾）。

- [ ] **Step 5：开 PR**

```bash
gh pr create --title "feat(backend): reference-to-video mode API + executor" --body "$(cat <<'EOF'
## Summary
- 新增 `/api/v1/projects/{project_name}/reference-videos` 路由族：list/add/patch/delete/reorder/generate 6 端点
- 新增 `execute_reference_video_task` executor：加载 unit → 解析 references → 压缩到 tempfile → 渲染 `@→[图N]` → Veo/Sora 特判 → 调 MediaGenerator → 抽首帧 → 回写 unit.generated_assets
- GenerationQueue 注册 `task_type="reference_video"`，与 storyboard/grid/video 共享视频并发通道
- 参考图压缩失败（`RequestPayloadTooLargeError`）触发二次压缩（1024px/q=70）重试
- 新增 6 个 `ref_*` i18n 错误 key（zh+en 对齐）
- `CostCalculator.estimate_reference_video_cost` 按 unit × duration × 单价聚合
- `ProjectArchiveService` 归档新增 `reference_videos/` 目录

## Spec 覆盖
- §5.1 路由族 6 端点
- §5.2 executor 10 步流水线
- §5.3 队列 / Worker dispatch
- §5.4 版本 / 费用 / 归档
- §8.2 错误矩阵（MissingReferenceError / RequestPayloadTooLargeError / ProviderUnsupportedFeatureError）
- §8.3 i18n key 集中新增

## 依赖 & 影响
- 前置：PR2（数据模型 + parser）已合并
- 旧项目零影响：路由仅作用于 `content_mode=reference_video` 的剧本；老的 storyboard/grid executor 未改签名
- 不 bump `schema_version`（沿用 v1）

## Test plan
- [x] `uv run pytest tests/server/test_reference_videos_router.py tests/server/test_reference_video_tasks.py tests/server/test_reference_video_e2e_backend.py -v` 全绿（15+13+1 测试）
- [x] `uv run pytest tests/lib/test_image_compression_batch.py tests/lib/test_cost_calculator_reference_video.py tests/lib/test_reference_video_errors.py tests/test_generation_tasks_dispatch.py -v` 全绿
- [x] `uv run pytest tests/test_i18n_consistency.py -v` 绿（6 个新 ref_* key zh/en 对齐）
- [x] 覆盖率：`server/services/reference_video_tasks.py` + `server/routers/reference_videos.py` ≥ 90%
- [x] 路由挂载自检：6 条 `/reference-videos/...` 路径可列出
- [x] 全量回归 `uv run pytest tests/ -x --ignore=tests/integration` 绿

## Out of scope
- 前端画布 / 模式选择器 → PR4
- 前端编辑器 / MentionPicker → PR5
- Agent 工作流 → PR6
- 真实 SDK 联调 + 发版 → PR7
EOF
)"
```

---

## Self-Review

**1. Spec 覆盖：**

| Spec 章节 | 对应 Task |
|---|---|
| §5.1 路由族（6 端点） | Task 9（list/add）+ Task 10（patch/delete）+ Task 11（reorder/generate） |
| §5.2 executor 10 步 | Task 5（load+resolve）+ Task 6（compress+render+constraints）+ Task 7（主流程含 retry + thumbnail + 元数据回写） |
| §5.3 queue/worker dispatch | Task 8 |
| §5.4 版本 / 费用 / 归档 | Task 4（cost）+ Task 12（archive）。VersionManager 复用既有 `resource_type` 分派，无需改动 |
| §8.2 错误矩阵 | Task 2（异常类）+ Task 7（PayloadTooLarge retry）+ Task 6（provider constraints → warnings） |
| §8.3 i18n key | Task 1 |

**2. Placeholder 扫描：**

- 无 "TBD" / "implement later"；每个 step 要么有完整代码块要么是明确的 lint/commit/test 命令。
- Task 12 Step 2 说明"无需改动 `_canonical_resource_path`"是事实澄清，不是 placeholder。

**3. Type 一致性：**

- `ReferenceResource` Pydantic 模型来自 PR2，字段签名（`type: Literal["character", "scene", "prop"]` + `name: str`）贯穿 Task 5-11。
- `execute_reference_video_task` 的返回字段 `{version, file_path, created_at, resource_type="reference_videos", resource_id, video_uri, warnings}` 在 Task 7 定义，Task 8 的 `_TASK_CHANGE_SPECS` 和 Task 13 的 E2E 测试都按此结构读取。
- `_apply_provider_constraints` 的 `warnings: list[dict]` 始终为 `{"key": str, "params": dict}`，由 Task 6 定义，Task 7 在 `RequestPayloadTooLargeError` 分支里 append 相同结构。
- `reference_video` task_type 字符串在 Task 8 / 11 / 13 中一致。
- `"reference_videos"` resource_type 字符串（复数）在 executor 返回、MediaGenerator 调用、archive 配置、目录路径中一致。

## 验收清单

- [ ] 14 个 task 全部 commit
- [ ] `uv run pytest tests/server/test_reference_videos_router.py tests/server/test_reference_video_tasks.py tests/server/test_reference_video_e2e_backend.py -v` 全绿（≥29 测试）
- [ ] `uv run pytest tests/lib/test_image_compression_batch.py tests/lib/test_cost_calculator_reference_video.py tests/lib/test_reference_video_errors.py tests/test_generation_tasks_dispatch.py tests/test_i18n_consistency.py -v` 全绿
- [ ] 全量回归 `uv run pytest tests/ -x --ignore=tests/integration` 绿
- [ ] 覆盖率 `server/services/reference_video_tasks.py` + `server/routers/reference_videos.py` ≥ 90%
- [ ] 6 条 `/reference-videos/...` 路径在 `app.routes` 中可列出
- [ ] `lib/i18n/{zh,en}/errors.py` 新增的 6 个 `ref_*` key zh/en 对齐
- [ ] PR 已开
