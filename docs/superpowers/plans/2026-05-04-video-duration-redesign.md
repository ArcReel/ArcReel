# 视频时长（supported_durations）系统性重设计 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `supported_durations` 成为视频时长的真单一真相源；删除 `_map_duration` / `_normalize_duration` / `VALID_DURATIONS` / 三处 prompt fallback / 前端 `DEFAULT_DURATIONS` 共五个旁路；自定义 provider 支持基于 `model_id` 启发式预设表 + 用户编辑；前端按连续性切换 slider/按钮组并对越界历史值打警示角标。

**Architecture:** 真相源 = `lib/config/registry.py::ModelInfo.supported_durations` 与 `CustomProviderModel.supported_durations` (DB JSON list[int])。`ConfigResolver.video_capabilities()` 归一输出，三个消费点（剧本生成 prompt、前端时长选择器、VideoBackend 请求）全部读同一条结果。新增 `lib/custom_provider/duration_presets.py` 作为 `model_id → list[int]` 启发式预设表（受 lmarena 排行榜数据驱动）。Alembic 一支迁移回填历史 NULL。

**Tech Stack:** Python 3.12 + FastAPI + SQLAlchemy Async + Alembic / React 19 + TypeScript + vitest / pytest（asyncio_mode=auto）。

**Spec：** `docs/superpowers/specs/2026-05-04-video-duration-redesign-design.md`

---

## File Structure

**新建（Python）：**
- `lib/custom_provider/duration_presets.py` — `model_id` → `list[int]` 启发式预设表，单一文件、受 PRESETS regex 数组驱动。
- `tests/test_duration_presets.py` — 全 PRESETS 分支 + DEFAULT_FALLBACK 命中。
- `alembic/versions/<rev>_backfill_custom_model_durations.py` — 一次性回填 video endpoint 模型的空 `supported_durations`，PRESETS 表 inline 复制（不 import）。
- `tests/test_alembic_supported_durations_backfill.py` — 升级后 NULL 行被回填。

**新建（前端）：**
- `frontend/src/utils/duration_format.ts` — `parseDurationInput / isContinuousIntegerRange / compactRangeFormat / formatDurationsLabel` 共享工具。
- `frontend/src/utils/duration_format.test.ts` — 全工具的单元测试。

**修改（Python，6 文件）：**
- `lib/video_backends/openai.py` — 删 `_map_duration`，`seconds` 改为 `str(request.duration_seconds)` 透传。
- `lib/video_backends/gemini.py` — 删 `_normalize_duration`，`duration_seconds` 改为 `str(request.duration_seconds)` 透传。
- `lib/data_validator.py` — 删 `VALID_DURATIONS`；`duration_seconds` 校验改为正整数。
- `lib/prompt_builders_script.py` — 删 `or [4, 6, 8]` fallback；`_format_duration_constraint` 加连续性检测。
- `lib/script_generator.py` — 删两处 `or [4, 8]` 二级 fallback；`_resolve_supported_durations` 找不到抛 ValueError。
- `lib/custom_provider/discovery.py` — 视频 endpoint 模型 discovery 时调 `infer_supported_durations` 预填。
- `server/routers/custom_providers.py` — `ModelCreate / ModelUpdate` 接 `supported_durations: list[int] | None`，None 时 server 调 preset；endpoint=video 且最终空时返 422。

**修改（前端，5 文件）：**
- `frontend/src/utils/provider-models.ts` — 删 `DEFAULT_DURATIONS = [4, 6, 8]`；`lookupSupportedDurations` 找不到时仍返 `undefined`（已是）。
- `frontend/src/components/pages/settings/CustomProviderForm.tsx` — `ModelRow` 加 `supported_durations: string`（逗号文本）；视频 endpoint 行展示输入框；提交时 parse。
- `frontend/src/components/pages/settings/CustomProviderDetail.tsx` — 模型卡片显示 supported_durations 格式化。
- `frontend/src/components/shared/ModelConfigSection.tsx` — `supportedDurations` 来源不再 fallback 到 DEFAULT_DURATIONS，找不到隐藏整个时长卡片；连续性 ≥5 用 slider，否则按钮组。
- `frontend/src/components/canvas/timeline/SegmentCard.tsx` — `DurationSelector` 检测越界值显示 ⚠ 角标 + tooltip；连续性 ≥5 切 slider。

**修改（i18n）：**
- `lib/i18n/zh/errors.py` / `lib/i18n/en/errors.py` — `supported_durations_missing` 错误。
- `frontend/src/i18n/zh/dashboard.ts` / `frontend/src/i18n/en/dashboard.ts` — 自定义 provider Form 和 SegmentCard 角标新文案。

**修改（测试）：**
- `tests/test_openai_video_backend.py`、`tests/test_data_validator.py`、`tests/test_resolver.py`、`tests/test_script_generator.py`、`tests/test_custom_providers_router.py`
- `frontend/src/components/shared/ModelConfigSection.test.tsx`、`frontend/src/components/canvas/timeline/SegmentCard.test.tsx`、`frontend/src/components/pages/settings/CustomProviderForm.test.tsx`（新建）

---

## Task 1: 预设表 + 单元测试

**Files:**
- Create: `lib/custom_provider/duration_presets.py`
- Create: `tests/test_duration_presets.py`

- [ ] **Step 1.1: 写失败测试**

`tests/test_duration_presets.py`:

```python
"""验证 duration_presets 启发式表覆盖排行榜 Top-20 模型 + 未匹配回退。"""
from __future__ import annotations

import pytest

from lib.custom_provider.duration_presets import (
    DEFAULT_FALLBACK,
    infer_supported_durations,
)


@pytest.mark.parametrize(
    "model_id, expected",
    [
        # OpenAI Sora 第一方
        ("sora-2", [4, 8, 12]),
        ("sora-2-pro", [4, 8, 12]),
        ("sora-2-pro-2026-01-15", [4, 8, 12]),
        # 第三方聚合 sora-pro 变体（命名含 sora 与 pro 但不匹配第一方严格 regex）
        ("aggregator-sora-pro-v2", [6, 10, 12, 16, 20]),
        # Veo 系列
        ("veo-3.1-generate-001", [4, 6, 8]),
        ("veo-3.1-fast-generate-preview", [4, 6, 8]),
        ("veo3-lite", [4, 6, 8]),
        # Kling 全系
        ("kling-v3.0", [5, 10]),
        ("kling-3.0-omni-pro", [5, 10]),
        ("kling-2.5-turbo", [5, 10]),
        ("kling-o1-pro", [5, 10]),
        # Runway Gen
        ("runway-gen-4.5", [5, 8, 10]),
        ("gen-4.5", [5, 8, 10]),
        # Luma Ray
        ("ray-3", [5, 10]),
        # ByteDance Seedance / Dreamina（4-15 全展开）
        ("dreamina-seedance-2-0-260128", list(range(4, 16))),
        ("doubao-seedance-1-5-pro-251215", list(range(4, 16))),
        # 即梦
        ("jimeng-video-3.0", list(range(4, 16))),
        # HappyHorse
        ("happyhorse-1.0", list(range(3, 16))),
        # Grok Imagine
        ("grok-imagine-video", list(range(1, 16))),
        # Vidu
        ("viduq3-pro", list(range(1, 17))),
        # PixVerse
        ("pixverse-v6", list(range(1, 16))),
        ("v5.6", list(range(1, 16))),
        # Hailuo / MiniMax
        ("hailuo-02", [6]),
        ("minimax-video", [6]),
        # Wan
        ("wan-2.1", [4, 5]),
        # Pika
        ("pika-2.0", [3, 5, 10]),
        # 未知模型 → fallback
        ("totally-unknown-model", DEFAULT_FALLBACK),
        ("", DEFAULT_FALLBACK),
    ],
)
def test_infer_supported_durations_known_and_unknown(model_id: str, expected: list[int]):
    assert infer_supported_durations(model_id) == expected


def test_returned_list_is_independent_copy():
    """连续两次调用返回的列表应是独立对象（防止外部修改污染预设表）。"""
    a = infer_supported_durations("sora-2")
    b = infer_supported_durations("sora-2")
    assert a == b
    a.append(999)
    assert infer_supported_durations("sora-2") == [4, 8, 12]


def test_default_fallback_constant_shape():
    assert DEFAULT_FALLBACK == [4, 8]
    assert all(isinstance(x, int) and x > 0 for x in DEFAULT_FALLBACK)
```

- [ ] **Step 1.2: 运行测试确认失败**

```bash
uv run python -m pytest tests/test_duration_presets.py -v
```

Expected: `ModuleNotFoundError: No module named 'lib.custom_provider.duration_presets'`

- [ ] **Step 1.3: 实现预设表**

`lib/custom_provider/duration_presets.py`:

```python
"""自定义供应商 model_id → supported_durations 启发式预设表。

数据来源：lmarena 视频模型排行榜 Top 20（2026-05 快照）+ 常见聚合命名。
匹配按 PRESETS 顺序，命中即返回；未匹配 → DEFAULT_FALLBACK。

歧义说明：同名 model_id（如 sora-2-pro）在 OpenAI 第一方与第三方聚合站点的实际允许
秒数可能不同。预设只是启发，给用户起点；用户必须在创建/编辑模型时 review 输入框值。
"""

from __future__ import annotations

import re

DEFAULT_FALLBACK: list[int] = [4, 8]

# 按特异性从高到低排列；命中一条即返回。range 全展开为离散集。
PRESETS: list[tuple[re.Pattern[str], list[int]]] = [
    # OpenAI Sora 第一方（严格 regex：可选 -pro，可选 -YYYY-MM-DD 日期后缀）
    (re.compile(r"^sora-2(-pro)?(-\d{4}-\d{2}-\d{2})?$", re.I), [4, 8, 12]),
    # 第三方聚合 Sora-Pro 变体（常见 6/10/12/16/20）
    (re.compile(r"sora.*pro", re.I), [6, 10, 12, 16, 20]),
    # Google Veo（含 fast / lite / preview）
    (re.compile(r"veo-?\d", re.I), [4, 6, 8]),
    # Kling 全系（v1/v2/v2.5/v2.6/v3.0/o1/turbo/pro/omni/standard）
    (re.compile(r"kling[-.]?(o1|v?[123](\.\d+)?)", re.I), [5, 10]),
    # Runway Gen 系列
    (re.compile(r"^(runway[-.]?)?gen-?\d", re.I), [5, 8, 10]),
    # Luma Ray / Dream Machine
    (re.compile(r"\bray-?\d", re.I), [5, 10]),
    # ByteDance Dreamina / Seedance（4-15 任意）
    (re.compile(r"dreamina|seedance", re.I), list(range(4, 16))),
    # 字节即梦
    (re.compile(r"jimeng", re.I), list(range(4, 16))),
    # Alibaba HappyHorse（3-15 任意）
    (re.compile(r"happyhorse", re.I), list(range(3, 16))),
    # xAI Grok Imagine（1-15 任意）
    (re.compile(r"grok[-.]?imagine", re.I), list(range(1, 16))),
    # Vidu Q 系列（1-16 任意）
    (re.compile(r"vidu", re.I), list(range(1, 17))),
    # PixVerse V5/V5.5/V5.6/V6（1-15 任意）
    (re.compile(r"pixverse|^v[56](\.\d+)?$", re.I), list(range(1, 16))),
    # MiniMax Hailuo（固定 6）
    (re.compile(r"hailuo|minimax", re.I), [6]),
    # Wan
    (re.compile(r"wan-?\d", re.I), [4, 5]),
    # Pika
    (re.compile(r"pika", re.I), [3, 5, 10]),
]


def infer_supported_durations(model_id: str) -> list[int]:
    """根据 model_id 启发式推导 supported_durations。

    返回值始终是非空升序去重的正整数列表，且为独立 list（caller 可安全修改）。
    """
    for pattern, durations in PRESETS:
        if pattern.search(model_id):
            return list(durations)
    return list(DEFAULT_FALLBACK)
```

- [ ] **Step 1.4: 运行测试确认通过**

```bash
uv run python -m pytest tests/test_duration_presets.py -v
uv run ruff check lib/custom_provider/duration_presets.py tests/test_duration_presets.py
uv run ruff format lib/custom_provider/duration_presets.py tests/test_duration_presets.py
```

Expected: 全部 passed；ruff 无 error。

- [ ] **Step 1.5: Commit**

```bash
git add lib/custom_provider/duration_presets.py tests/test_duration_presets.py
git commit -m "feat(duration): 新增 model_id → supported_durations 启发式预设表"
```

---

## Task 2: Alembic 回填迁移 + 测试

**Files:**
- Create: `alembic/versions/<NEW_REV>_backfill_custom_model_durations.py`
- Create: `tests/test_alembic_supported_durations_backfill.py`

- [ ] **Step 2.1: 找到当前 alembic head**

```bash
uv run alembic heads
```

记下输出的 revision id（例如 `5b87accc10dd`），下一步用作 `down_revision`。Plan 后续示例中以 `<HEAD_REV>` 表示。

- [ ] **Step 2.2: 写失败测试**

`tests/test_alembic_supported_durations_backfill.py`:

```python
"""Alembic 回填迁移：验证 video endpoint 模型的 NULL supported_durations 被启发式填充。"""
from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config


@pytest.fixture
def alembic_cfg(tmp_path: Path) -> Config:
    """指向项目 alembic.ini，但 DB 用临时 sqlite。"""
    repo_root = Path(__file__).resolve().parent.parent
    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(repo_root / "alembic"))
    db_path = tmp_path / "test.db"
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


@pytest.fixture
def backfill_revision_id() -> str:
    """读出新增迁移 revision id（在脚本顶部 revision 变量），便于按名锁定。"""
    repo_root = Path(__file__).resolve().parent.parent
    versions_dir = repo_root / "alembic" / "versions"
    matches = list(versions_dir.glob("*_backfill_custom_model_durations.py"))
    assert len(matches) == 1, f"找到 {len(matches)} 个回填迁移文件，期望 1"
    text = matches[0].read_text()
    for line in text.splitlines():
        if line.startswith("revision: str ="):
            return line.split("=")[1].strip().strip('"').strip("'")
    raise RuntimeError("未在迁移文件中找到 revision id")


def test_backfill_video_endpoints_with_null_durations(alembic_cfg: Config, backfill_revision_id: str):
    """先回退到 backfill 之前一格，插入若干 NULL 行，再升级到 backfill，断言被填充。"""
    # 1. 把 schema 升到 backfill 之前一格
    command.upgrade(alembic_cfg, f"{backfill_revision_id}^")

    engine = sa.create_engine(alembic_cfg.get_main_option("sqlalchemy.url"))
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO custom_provider (id, display_name, discovery_format, base_url, api_key) "
                    "VALUES (1, 'P', 'openai', 'https://x', 'k')")
        )
        # 三条：video endpoint 且 NULL → 应被回填；text endpoint 不动；非 NULL 也不动
        conn.execute(sa.text(
            "INSERT INTO custom_provider_model (id, provider_id, model_id, display_name, "
            "endpoint, is_default, is_enabled, supported_durations) VALUES "
            "(1, 1, 'sora-2-pro', 'X', 'openai-video', 0, 1, NULL),"
            "(2, 1, 'unknown-foo', 'Y', 'openai-video', 0, 1, NULL),"
            "(3, 1, 'gpt-4o', 'Z', 'openai-chat', 0, 1, NULL),"
            "(4, 1, 'sora-2', 'W', 'openai-video', 0, 1, '[1,2,3]')"
        ))

    # 2. 升级到 backfill
    command.upgrade(alembic_cfg, backfill_revision_id)

    # 3. 断言
    with engine.begin() as conn:
        rows = conn.execute(sa.text(
            "SELECT model_id, supported_durations FROM custom_provider_model ORDER BY id"
        )).fetchall()
    by_id = {r[0]: r[1] for r in rows}

    # sora-2-pro 命中第一条预设：[4, 8, 12]
    assert by_id["sora-2-pro"] == "[4, 8, 12]"
    # 未知 → DEFAULT_FALLBACK [4, 8]
    assert by_id["unknown-foo"] == "[4, 8]"
    # text endpoint 不动
    assert by_id["gpt-4o"] is None
    # 已有非 NULL 不动
    assert by_id["sora-2"] == "[1,2,3]"
```

- [ ] **Step 2.3: 运行测试确认失败**

```bash
uv run python -m pytest tests/test_alembic_supported_durations_backfill.py -v
```

Expected: `AssertionError: 找到 0 个回填迁移文件，期望 1`（迁移文件还没建）

- [ ] **Step 2.4: 创建迁移文件**

新文件 `alembic/versions/<auto_id>_backfill_custom_model_durations.py`，先用 alembic 生成空迁移再填内容：

```bash
uv run alembic revision -m "backfill custom_model supported_durations"
```

记下生成文件路径与 revision id（替换下面的 `<NEW_REV>`），把内容改为：

```python
"""backfill custom_model supported_durations

按 model_id 启发式填充 video endpoint 模型的 NULL supported_durations。
PRESETS 在迁移内 inline 复制（不 import lib.custom_provider.duration_presets），
让历史迁移与未来代码改动解耦。

Revision ID: <NEW_REV>
Revises: <HEAD_REV>
Create Date: 2026-05-04 ...
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "<NEW_REV>"
down_revision: str | Sequence[str] | None = "<HEAD_REV>"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# --- inline 快照（与 lib/custom_provider/duration_presets.py 同步，但解耦演进）---
_DEFAULT_FALLBACK: list[int] = [4, 8]

_PRESETS: list[tuple[re.Pattern[str], list[int]]] = [
    (re.compile(r"^sora-2(-pro)?(-\d{4}-\d{2}-\d{2})?$", re.I), [4, 8, 12]),
    (re.compile(r"sora.*pro", re.I), [6, 10, 12, 16, 20]),
    (re.compile(r"veo-?\d", re.I), [4, 6, 8]),
    (re.compile(r"kling[-.]?(o1|v?[123](\.\d+)?)", re.I), [5, 10]),
    (re.compile(r"^(runway[-.]?)?gen-?\d", re.I), [5, 8, 10]),
    (re.compile(r"\bray-?\d", re.I), [5, 10]),
    (re.compile(r"dreamina|seedance", re.I), list(range(4, 16))),
    (re.compile(r"jimeng", re.I), list(range(4, 16))),
    (re.compile(r"happyhorse", re.I), list(range(3, 16))),
    (re.compile(r"grok[-.]?imagine", re.I), list(range(1, 16))),
    (re.compile(r"vidu", re.I), list(range(1, 17))),
    (re.compile(r"pixverse|^v[56](\.\d+)?$", re.I), list(range(1, 16))),
    (re.compile(r"hailuo|minimax", re.I), [6]),
    (re.compile(r"wan-?\d", re.I), [4, 5]),
    (re.compile(r"pika", re.I), [3, 5, 10]),
]

# 视频类 endpoint key 集合（与 lib/custom_provider/endpoints.py ENDPOINT_REGISTRY 同步快照）
_VIDEO_ENDPOINTS = ("openai-video", "newapi-video")


def _infer(model_id: str) -> list[int]:
    for pattern, durations in _PRESETS:
        if pattern.search(model_id):
            return list(durations)
    return list(_DEFAULT_FALLBACK)


def upgrade() -> None:
    bind = op.get_bind()
    placeholders = ",".join(f"'{ep}'" for ep in _VIDEO_ENDPOINTS)
    rows = bind.execute(sa.text(
        f"SELECT id, model_id FROM custom_provider_model "
        f"WHERE supported_durations IS NULL AND endpoint IN ({placeholders})"
    )).fetchall()
    for row_id, model_id in rows:
        durations = _infer(model_id or "")
        bind.execute(
            sa.text("UPDATE custom_provider_model SET supported_durations = :v WHERE id = :id"),
            {"v": json.dumps(durations), "id": row_id},
        )


def downgrade() -> None:
    # 不主动清除（回填后保留即可，避免破坏数据）
    pass
```

将占位 `<NEW_REV>` / `<HEAD_REV>` 替换为实际 revision id。

- [ ] **Step 2.5: 运行测试确认通过**

```bash
uv run python -m pytest tests/test_alembic_supported_durations_backfill.py -v
uv run ruff check alembic/versions/*backfill_custom_model_durations.py tests/test_alembic_supported_durations_backfill.py
```

Expected: 测试 passed。

- [ ] **Step 2.6: Commit**

```bash
git add alembic/versions/*backfill_custom_model_durations.py tests/test_alembic_supported_durations_backfill.py
git commit -m "feat(duration): alembic 回填空 supported_durations（按预设表启发）"
```

---

## Task 3: 删除 OpenAIVideoBackend._map_duration

**Files:**
- Modify: `lib/video_backends/openai.py`
- Modify: `tests/test_openai_video_backend.py`

- [ ] **Step 3.1: 改测试预期**

将 `tests/test_openai_video_backend.py` 中：

替换 `test_duration_mapping`（line ~167）整段函数为：

```python
    async def test_duration_passthrough(self, tmp_path: Path):
        """所有 duration 值应原值透传到 SDK，不再被 _map_duration 篡改。"""
        mock_client = AsyncMock()
        _stub_client_completed(mock_client, seconds="6")

        with (
            patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client),
            patch("lib.video_backends.base.asyncio.sleep", new_callable=AsyncMock),
        ):
            from lib.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key")

            for seconds in [3, 4, 5, 6, 7, 8, 10, 12, 15, 20]:
                output_path = tmp_path / f"output_{seconds}.mp4"
                request = VideoGenerationRequest(
                    prompt="test",
                    output_path=output_path,
                    duration_seconds=seconds,
                )
                await backend.generate(request)
                call_kwargs = mock_client.videos.create.call_args[1]
                assert call_kwargs["seconds"] == str(seconds), f"duration={seconds}"
```

替换 `test_video_seconds_none_fallback`（line ~190）的最后一行注释与断言：

```python
        # 请求 6 秒 → 透传 → 回退应保留请求值 6
        assert result.duration_seconds == 6
```

并把同函数中 `duration_seconds=5` 改为 `duration_seconds=6`，与新预期一致。

- [ ] **Step 3.2: 运行测试确认失败**

```bash
uv run python -m pytest tests/test_openai_video_backend.py::TestOpenAIVideoBackend::test_duration_passthrough -v
```

Expected: FAIL —— `assert call_kwargs["seconds"] == "6"` 因为 `_map_duration(6)` 仍返回 `"8"`。

- [ ] **Step 3.3: 删除 _map_duration**

`lib/video_backends/openai.py`：

定位第 85 行附近 `"seconds": _map_duration(request.duration_seconds),` 改为：

```python
            "seconds": str(request.duration_seconds),
```

定位第 123 行附近 result 构造里 `int(final.seconds if final.seconds is not None else kwargs["seconds"])` 保持不变（kwargs["seconds"] 现在是 str 也可被 int 包裹）。

定位第 163-169 行 `_map_duration` 整段函数及其上方注释删除。

- [ ] **Step 3.4: 运行测试确认通过**

```bash
uv run python -m pytest tests/test_openai_video_backend.py -v
uv run ruff check lib/video_backends/openai.py tests/test_openai_video_backend.py
uv run ruff format lib/video_backends/openai.py tests/test_openai_video_backend.py
```

Expected: 全部 passed。

- [ ] **Step 3.5: Commit**

```bash
git add lib/video_backends/openai.py tests/test_openai_video_backend.py
git commit -m "fix(video-openai): seconds 字段原值透传，删除 _map_duration 篡改

修复用户在自定义供应商上选 6s 被静默改成 8s 后被对端拒绝的问题。
对端的 supported_durations 由 model 配置决定，backend 不应再做桶映射。"
```

---

## Task 4: 删除 GeminiVideoBackend._normalize_duration

**Files:**
- Modify: `lib/video_backends/gemini.py`
- Create or extend: `tests/test_gemini_video_resolution.py`（仅新增 duration 透传测试段；resolution 测试不动）

- [ ] **Step 4.1: 写失败测试**

打开 `tests/test_gemini_video_resolution.py`，在文件末尾追加：

```python
async def test_gemini_duration_passthrough(monkeypatch, tmp_path: Path):
    """删除 _normalize_duration 后，duration_seconds 应原值（str）透传到 SDK config。"""
    from lib.video_backends.base import VideoGenerationRequest

    captured: dict = {}

    class _FakeOps:
        async def get(self, op):
            class R:
                done = True
                response = None
                error = "stub"
                metadata = None
            return R()

    class _FakeAio:
        models = type("M", (), {"generate_videos": staticmethod(
            lambda model, source, config: _capture(captured, model, source, config)
        )})()
        operations = _FakeOps()

    async def _capture(cap, model, source, config):
        # 把传给 SDK 的 config.duration_seconds 抓出来
        cap["duration_seconds"] = config.duration_seconds
        cap["model"] = model
        class Op:
            done = True
            response = None
            error = "stub"
            name = "operations/x"
            metadata = None
        return Op()

    # 完整 mock GeminiVideoBackend 的 client；具体写法参照同文件已有 fixture 风格
    # （此处省略 fixture，保留意图：构造 backend 后调 generate 抓 captured["duration_seconds"]）
    pytest.skip("此测试为 plan 模板示意；实际实现请参照同文件 resolution 测试的 mock 方式构造 fake client。")
```

> **写实现时的注意**：本仓库 `tests/test_gemini_video_resolution.py` 已建立完整的 GeminiVideoBackend mock fixture。直接复用同一 fixture 写新测试 `test_duration_passthrough_str`，断言传 7 秒时 SDK config 的 `duration_seconds == "7"`，而非被映射成 `"8"`。

可执行的最小测试（替换上面 skip 段）：

```python
async def test_gemini_duration_passthrough_str(tmp_path: Path):
    """7 秒应原值透传为 '7'，不被 _normalize_duration 改成 '8'。"""
    from lib.video_backends.gemini import GeminiVideoBackend
    from lib.video_backends.base import VideoGenerationRequest

    captured: dict = {}

    async def fake_generate_videos(model, source, config):
        captured["duration_seconds"] = config.duration_seconds
        class Op:
            name = "operations/abc"
            done = True
            response = type("R", (), {"generated_videos": []})()
            error = "boom"
            metadata = None
        return Op()

    backend = GeminiVideoBackend.__new__(GeminiVideoBackend)
    backend._video_model = "veo-3.1-generate-001"  # type: ignore[attr-defined]
    backend._backend_type = "aistudio"  # type: ignore[attr-defined]
    backend._rate_limiter = None  # type: ignore[attr-defined]
    backend._capabilities = set()  # type: ignore[attr-defined]
    # 替换 _client.aio.models.generate_videos
    class _Models:
        generate_videos = staticmethod(fake_generate_videos)
    class _Aio:
        models = _Models()
        operations = type("O", (), {"get": staticmethod(lambda op: op)})()
    backend._client = type("C", (), {"aio": _Aio()})()  # type: ignore[attr-defined]
    # 旁路 _types 构造（最小可运行 stub）
    class _GVConfig:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)
    class _Types:
        GenerateVideosConfig = _GVConfig
        GenerateVideosSource = lambda prompt, image=None: type("S", (), {"prompt": prompt, "image": image})()
        VideoGenerationReferenceImage = type("Ri", (), {})
        VideoGenerationReferenceType = type("Rt", (), {"ASSET": "ASSET"})()
    backend._types = _Types()  # type: ignore[attr-defined]

    request = VideoGenerationRequest(
        prompt="x",
        output_path=tmp_path / "out.mp4",
        duration_seconds=7,
    )
    with pytest.raises(RuntimeError):
        await backend.generate(request)  # 因 stub 没有 generated_videos 会抛
    assert captured["duration_seconds"] == "7"
```

- [ ] **Step 4.2: 运行测试确认失败**

```bash
uv run python -m pytest tests/test_gemini_video_resolution.py::test_gemini_duration_passthrough_str -v
```

Expected: FAIL —— `_normalize_duration(7)` 返回 `"8"`，captured 中拿到 `"8"` 而非 `"7"`。

- [ ] **Step 4.3: 删除 _normalize_duration**

`lib/video_backends/gemini.py`：

定位第 114-121 行 `@staticmethod def _normalize_duration` 整段方法删除。

定位第 135-136 行：

```python
        # 2. duration 标准化为 Veo 支持的离散值并转字符串
        duration_str = self._normalize_duration(request.duration_seconds)
```

替换为：

```python
        # 2. duration 原值透传（保持 SDK 接受的 str 形态）
        duration_str = str(request.duration_seconds)
```

- [ ] **Step 4.4: 运行测试确认通过**

```bash
uv run python -m pytest tests/test_gemini_video_resolution.py -v
uv run ruff check lib/video_backends/gemini.py tests/test_gemini_video_resolution.py
uv run ruff format lib/video_backends/gemini.py tests/test_gemini_video_resolution.py
```

Expected: 全部 passed。

- [ ] **Step 4.5: Commit**

```bash
git add lib/video_backends/gemini.py tests/test_gemini_video_resolution.py
git commit -m "fix(video-gemini): duration_seconds 原值透传，删除 _normalize_duration 桶映射"
```

---

## Task 5: 删除 data_validator.VALID_DURATIONS

**Files:**
- Modify: `lib/data_validator.py`
- Modify: `tests/test_data_validator.py`

- [ ] **Step 5.1: 改测试预期**

打开 `tests/test_data_validator.py`，定位 `duration_seconds: 5` 与 `duration_seconds 值无效` 相关用例。把"5 应当报错"的断言改为"5 不再报错"：

找到包含 `"duration_seconds": 5` 的用例（约 line 137），把断言：

```python
        assert any("duration_seconds 值无效" in error for error in result.errors)
```

替换为：

```python
        assert not any("duration_seconds 值无效" in error for error in result.errors)
        assert not any("duration_seconds" in error and "无效" in error for error in result.errors)
```

新增一条测试函数（紧邻原用例后）：

```python
    def test_duration_seconds_zero_or_negative_still_invalid(self):
        """非正整数仍应报错（0 / 负数 / 非整数）。"""
        validator = DataValidator()
        for bad in [0, -1, "5", 4.5]:
            data = self._minimal_episode(duration_seconds=bad)  # type: ignore[arg-type]
            result = validator.validate_episode(data)
            assert any("duration_seconds" in e for e in result.errors), f"bad={bad}"
```

> **写实现时**：`_minimal_episode` 是文件内已有的辅助函数；如果没有就参考已有 `validate_episode` 测试的 dict 构造模式 inline 同样结构。

- [ ] **Step 5.2: 运行测试确认失败**

```bash
uv run python -m pytest tests/test_data_validator.py -v
```

Expected: FAIL —— 现有 `VALID_DURATIONS = {4, 6, 8}` 仍把 5 判为无效。

- [ ] **Step 5.3: 修改 data_validator**

`lib/data_validator.py`：

第 44 行：

```python
    VALID_DURATIONS = {4, 6, 8}
```

删除整行。

第 347-351 行附近：

```python
            duration = segment.get("duration_seconds")
            if duration is None:
                warnings.append(f"{prefix}: 缺少 duration_seconds，将使用默认值 4")
            elif duration not in self.VALID_DURATIONS:
                errors.append(f"{prefix}: duration_seconds 值无效 '{duration}'，必须是 {self.VALID_DURATIONS}")
```

替换为：

```python
            duration = segment.get("duration_seconds")
            if duration is None:
                warnings.append(f"{prefix}: 缺少 duration_seconds，将使用默认值 4")
            elif not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
                errors.append(f"{prefix}: duration_seconds 值无效 '{duration}'，必须为正整数")
```

第 429-433 行做同样替换（`scene` 版本）：

```python
            duration = scene.get("duration_seconds")
            if duration is None:
                warnings.append(f"{prefix}: 缺少 duration_seconds，将使用默认值 8")
            elif not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
                errors.append(f"{prefix}: duration_seconds 值无效 '{duration}'，必须为正整数")
```

> 加 `isinstance(duration, bool)` 排除是因为 Python 中 `True/False` 是 `int` 子类。

- [ ] **Step 5.4: 运行测试确认通过**

```bash
uv run python -m pytest tests/test_data_validator.py -v
uv run ruff check lib/data_validator.py tests/test_data_validator.py
uv run ruff format lib/data_validator.py tests/test_data_validator.py
```

Expected: 全部 passed。

- [ ] **Step 5.5: Commit**

```bash
git add lib/data_validator.py tests/test_data_validator.py
git commit -m "refactor(validator): 删除 VALID_DURATIONS 硬编码集合，改为正整数校验

duration_seconds 的合法值由 model 的 supported_durations 决定（剧本 prompt 已注入约束），
data_validator 仅验证类型与正性。"
```

---

## Task 6: prompt_builders 连续性检测 + 删除 fallback

**Files:**
- Modify: `lib/prompt_builders_script.py`

- [ ] **Step 6.1: 写失败测试（新文件）**

新建 `tests/test_prompt_builders_script_duration.py`：

```python
"""验证 _format_duration_constraint 按连续性切换文案，且不允许空 supported_durations。"""
from __future__ import annotations

import pytest

from lib.prompt_builders_script import _format_duration_constraint


class TestFormatDurationConstraint:
    def test_discrete_set(self):
        text = _format_duration_constraint([4, 6, 8], default_duration=None)
        assert "[4, 6, 8]" in text
        assert "根据内容节奏自行决定" in text

    def test_discrete_set_with_default(self):
        text = _format_duration_constraint([4, 6, 8], default_duration=6)
        assert "[4, 6, 8]" in text
        assert "默认使用 6 秒" in text

    def test_continuous_range_uses_min_max_phrasing(self):
        """长度 ≥5 且连续整数 → 用 'min 到 max 整数任选' 文案。"""
        text = _format_duration_constraint([3, 4, 5, 6, 7, 8, 9, 10], default_duration=None)
        assert "3 到 10 秒间" in text or "3-10" in text
        # 不再列举每个数
        assert "[3, 4, 5, 6, 7, 8, 9, 10]" not in text

    def test_short_continuous_still_uses_list(self):
        """长度 <5 即使连续，仍走列举形态（避免简短列表强行变成 '4 到 6'）。"""
        text = _format_duration_constraint([4, 5, 6], default_duration=None)
        assert "[4, 5, 6]" in text


class TestBuildersRequireDurations:
    """删除 fallback 后，传 None / 空 list 不应再被静默回填。"""

    def test_format_constraint_rejects_empty(self):
        with pytest.raises((ValueError, AssertionError, IndexError)):
            _format_duration_constraint([], default_duration=None)
```

- [ ] **Step 6.2: 运行测试确认失败**

```bash
uv run python -m pytest tests/test_prompt_builders_script_duration.py -v
```

Expected: FAIL —— 现有实现仍输出列表化文案；空 list 不会抛错。

- [ ] **Step 6.3: 修改 prompt_builders_script.py**

`lib/prompt_builders_script.py`：

第 26-31 行 `_format_duration_constraint` 替换为：

```python
def _format_duration_constraint(supported_durations: list[int], default_duration: int | None) -> str:
    """根据参数生成时长约束描述。

    长度 ≥5 且为连续整数集时，输出 "min 到 max 秒间整数任选"；否则按列表逐个列出。
    """
    if not supported_durations:
        raise ValueError("supported_durations 不能为空：调用方必须提供 model 的合法时长列表")

    sorted_d = sorted(set(supported_durations))
    is_continuous = len(sorted_d) >= 5 and all(
        sorted_d[i] == sorted_d[i - 1] + 1 for i in range(1, len(sorted_d))
    )
    if is_continuous:
        body = f"{sorted_d[0]} 到 {sorted_d[-1]} 秒间整数任选"
    else:
        durations_str = ", ".join(str(d) for d in sorted_d)
        body = f"从 [{durations_str}] 秒中选择"

    if default_duration is not None:
        return f"时长：{body}，默认使用 {default_duration} 秒"
    return f"时长：{body}，根据内容节奏自行决定"
```

第 51-52 行（`build_narration_prompt`）和第 178 行附近（`build_drama_prompt`）的签名：

```python
    supported_durations: list[int] | None = None,
```

改为：

```python
    supported_durations: list[int],
```

第 118 与 245 行附近的 fallback：

```python
- {_format_duration_constraint(supported_durations or [4, 6, 8], default_duration)}
```

改为：

```python
- {_format_duration_constraint(supported_durations, default_duration)}
```

- [ ] **Step 6.4: 运行测试确认通过**

```bash
uv run python -m pytest tests/test_prompt_builders_script_duration.py -v
# 同时跑现有 prompt builder 测试确保无回归
uv run python -m pytest tests/ -k "prompt_builder" -v
uv run ruff check lib/prompt_builders_script.py tests/test_prompt_builders_script_duration.py
uv run ruff format lib/prompt_builders_script.py tests/test_prompt_builders_script_duration.py
```

Expected: 全部 passed。

- [ ] **Step 6.5: Commit**

```bash
git add lib/prompt_builders_script.py tests/test_prompt_builders_script_duration.py
git commit -m "refactor(prompt): supported_durations 必填 + 连续整数集合改用区间文案

删除 [4,6,8] 隐性 fallback；连续 ≥5 个整数（如 1..15）走 'X 到 Y 整数任选'，
避免向 LLM prompt 注入十几个枚举值。"
```

---

## Task 7: script_generator 删除 fallback

**Files:**
- Modify: `lib/script_generator.py`
- Modify: `tests/test_script_generator.py`（如有 fallback 测试）

- [ ] **Step 7.1: 检查现有测试**

```bash
grep -n "_resolve_supported_durations\|or \[4, 8\]" /Users/pollochen/MyProjects/ArcReel/tests/test_script_generator.py 2>/dev/null
```

记下命中行，准备改 / 删。

- [ ] **Step 7.2: 写新失败测试**

在 `tests/test_script_generator.py`（如不存在新建）末尾追加：

```python
def test_resolve_supported_durations_raises_when_unset(tmp_path):
    """caps、project.json、registry 三处都查不到时应抛 ValueError，不再 silent fallback。"""
    import pytest
    from lib.script_generator import ScriptGenerator

    project_dir = tmp_path / "p"
    project_dir.mkdir()
    (project_dir / "project.json").write_text(
        '{"video_backend": "nonexistent-provider/nonexistent-model"}', encoding="utf-8"
    )
    sg = ScriptGenerator.__new__(ScriptGenerator)
    sg.project_path = project_dir
    sg.project_json = {"video_backend": "nonexistent-provider/nonexistent-model"}

    with pytest.raises(ValueError, match="supported_durations"):
        sg._resolve_supported_durations(None)
```

- [ ] **Step 7.3: 运行测试确认失败**

```bash
uv run python -m pytest tests/test_script_generator.py::test_resolve_supported_durations_raises_when_unset -v
```

Expected: FAIL —— 当前 `_resolve_supported_durations` 找不到时返回 `None` 而非抛错。

- [ ] **Step 7.4: 修改 script_generator.py**

`lib/script_generator.py`：

第 118 行：

```python
                supported_durations=self._resolve_supported_durations(caps) or [4, 8],
```

改为：

```python
                supported_durations=self._resolve_supported_durations(caps),
```

第 208 行同样替换（`or [4, 8]` 删掉）。

第 257-275 行附近 `_resolve_supported_durations` 整段重写为：

```python
    def _resolve_supported_durations(self, caps: dict | None = None) -> list[int]:
        """从 caps → project.json → registry 三级解析；都拿不到抛 ValueError。"""
        if caps and caps.get("supported_durations"):
            return list(caps["supported_durations"])
        durations = self.project_json.get("_supported_durations")
        if durations and isinstance(durations, list):
            return list(durations)
        video_backend = self.project_json.get("video_backend")
        if video_backend and isinstance(video_backend, str) and "/" in video_backend:
            provider_id, model_id = video_backend.split("/", 1)
            provider_meta = PROVIDER_REGISTRY.get(provider_id)
            if provider_meta:
                model_info = provider_meta.models.get(model_id)
                if model_info and model_info.supported_durations:
                    return list(model_info.supported_durations)
        raise ValueError(
            f"supported_durations 无法解析：caps={bool(caps)}, "
            f"video_backend={video_backend!r}；请确保 model 配置完整"
        )
```

> 函数返回类型从 `list[int] | None` 收紧到 `list[int]`。

- [ ] **Step 7.5: 运行测试确认通过**

```bash
uv run python -m pytest tests/test_script_generator.py -v
uv run ruff check lib/script_generator.py tests/test_script_generator.py
uv run ruff format lib/script_generator.py tests/test_script_generator.py
```

Expected: 全部 passed。

- [ ] **Step 7.6: Commit**

```bash
git add lib/script_generator.py tests/test_script_generator.py
git commit -m "refactor(script-gen): _resolve_supported_durations 找不到时 fail-loud

删除两处 'or [4, 8]' 隐性兜底；配置错误浮到表面而不是被掩盖成默认 [4, 8]。"
```

---

## Task 8: discovery + custom_providers router 接 supported_durations

**Files:**
- Modify: `lib/custom_provider/discovery.py`
- Modify: `server/routers/custom_providers.py`
- Modify: `tests/test_custom_providers_router.py`

- [ ] **Step 8.1: 写失败测试**

在 `tests/test_custom_providers_router.py` 末尾追加：

```python
async def test_create_custom_model_video_endpoint_autofills_durations(async_client):
    """创建 video endpoint 模型时不传 supported_durations，应由 server 用预设表自动填。"""
    # 1. 创建 provider
    resp = await async_client.post("/api/v1/custom-providers", json={
        "display_name": "test-cp",
        "discovery_format": "openai",
        "base_url": "https://example.com/v1",
        "api_key": "sk-test",
        "models": [{
            "model_id": "sora-2-pro",
            "display_name": "Sora 2 Pro",
            "endpoint": "openai-video",
            "is_default": True,
            "is_enabled": True,
            # 注意：不传 supported_durations
        }],
    })
    assert resp.status_code == 201, resp.text
    provider_id = resp.json()["id"]

    # 2. 读回，断言被 preset 填上 [4, 8, 12]
    resp = await async_client.get(f"/api/v1/custom-providers/{provider_id}")
    assert resp.status_code == 200
    model = resp.json()["models"][0]
    assert model["supported_durations"] == [4, 8, 12]


async def test_create_custom_model_user_provided_durations_kept(async_client):
    """用户传了非空 supported_durations 时，server 不应被预设表覆盖。"""
    resp = await async_client.post("/api/v1/custom-providers", json={
        "display_name": "test-cp-2",
        "discovery_format": "openai",
        "base_url": "https://example.com/v1",
        "api_key": "sk-test",
        "models": [{
            "model_id": "sora-2-pro",
            "display_name": "Sora 2 Pro",
            "endpoint": "openai-video",
            "is_default": True,
            "is_enabled": True,
            "supported_durations": [6, 10, 12, 16, 20],  # 用户已自填
        }],
    })
    assert resp.status_code == 201, resp.text
    provider_id = resp.json()["id"]
    resp = await async_client.get(f"/api/v1/custom-providers/{provider_id}")
    model = resp.json()["models"][0]
    assert model["supported_durations"] == [6, 10, 12, 16, 20]


async def test_text_endpoint_does_not_get_durations(async_client):
    """text endpoint 模型不应被预设表赋值（保持 None）。"""
    resp = await async_client.post("/api/v1/custom-providers", json={
        "display_name": "test-cp-3",
        "discovery_format": "openai",
        "base_url": "https://example.com/v1",
        "api_key": "sk-test",
        "models": [{
            "model_id": "gpt-4o",
            "display_name": "GPT 4o",
            "endpoint": "openai-chat",
            "is_default": True,
            "is_enabled": True,
        }],
    })
    assert resp.status_code == 201
    provider_id = resp.json()["id"]
    resp = await async_client.get(f"/api/v1/custom-providers/{provider_id}")
    model = resp.json()["models"][0]
    assert model["supported_durations"] is None
```

> **fixture `async_client`** 应已在 conftest.py 中存在（参考已有 `tests/test_custom_providers_router.py` 测试同款 fixture）。如不存在，按文件中现有用例的 `client` 参数名替换。

- [ ] **Step 8.2: 运行测试确认失败**

```bash
uv run python -m pytest tests/test_custom_providers_router.py -v -k "autofills or kept or text_endpoint"
```

Expected: FAIL —— `model["supported_durations"]` 是 None 或空。

- [ ] **Step 8.3: 修改 custom_providers router**

`server/routers/custom_providers.py`：

定位 line ~81 `class ModelCreate` 与 line ~138 `class ModelUpdate`（含 `supported_durations: list[int] | None = None` 字段；spec 已确认两处都有）。

定位 line ~85-89 把 `supported_durations` 序列化为 JSON string 的代码（已存在）。在转 dict 之前增加预填逻辑：

找到 `def to_db_dict(self) -> dict:`（约 85 行）与下面的 `d["supported_durations"] = json.dumps(...)`：

```python
    def to_db_dict(self) -> dict:
        """返回适合写入数据库的字典（supported_durations 序列化为 JSON 字符串）。"""
        d = self.model_dump(exclude_none=False)
        d["supported_durations"] = (
            json.dumps(self.supported_durations) if self.supported_durations is not None else None
        )
        return d
```

替换为：

```python
    def to_db_dict(self) -> dict:
        """返回适合写入数据库的字典（supported_durations 序列化为 JSON 字符串）。

        视频类 endpoint 且 supported_durations 缺省时，由 duration_presets 启发式填补。
        非视频类 endpoint 保持 None。
        """
        from lib.custom_provider.duration_presets import infer_supported_durations
        from lib.custom_provider.endpoints import endpoint_to_media_type

        d = self.model_dump(exclude_none=False)
        durations = self.supported_durations
        if durations is None:
            try:
                if endpoint_to_media_type(self.endpoint) == "video":
                    durations = infer_supported_durations(self.model_id)
            except ValueError:
                # 未知 endpoint 由后续校验报错；这里保持 None
                pass
        d["supported_durations"] = json.dumps(durations) if durations is not None else None
        return d
```

> 同样修改 `ModelUpdate.to_db_dict`（如果 ModelUpdate 也有该方法；否则把上面的逻辑封装为模块函数被两处调用）。

- [ ] **Step 8.4: 运行测试确认通过**

```bash
uv run python -m pytest tests/test_custom_providers_router.py -v
uv run ruff check server/routers/custom_providers.py tests/test_custom_providers_router.py
uv run ruff format server/routers/custom_providers.py tests/test_custom_providers_router.py
```

Expected: 全部 passed。

- [ ] **Step 8.5: Commit**

```bash
git add server/routers/custom_providers.py tests/test_custom_providers_router.py
git commit -m "feat(custom-provider): 视频模型创建/更新时按预设表自动填 supported_durations

用户未填即用 model_id 启发式预设，命中常见视频模型；
用户填了即透传不覆盖。非视频 endpoint 保持 None。"
```

---

## Task 9: 前端 utils duration_format

**Files:**
- Create: `frontend/src/utils/duration_format.ts`
- Create: `frontend/src/utils/duration_format.test.ts`

- [ ] **Step 9.1: 写失败测试**

`frontend/src/utils/duration_format.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import {
  parseDurationInput,
  isContinuousIntegerRange,
  compactRangeFormat,
  formatDurationsLabel,
} from "./duration_format";

describe("parseDurationInput", () => {
  it("解析单值列表", () => {
    expect(parseDurationInput("4, 6, 8")).toEqual([4, 6, 8]);
  });

  it("解析区间简写", () => {
    expect(parseDurationInput("3-15")).toEqual([3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]);
  });

  it("混合单值与区间，去重排序", () => {
    expect(parseDurationInput("3, 5, 7-10, 12")).toEqual([3, 5, 7, 8, 9, 10, 12]);
  });

  it("空白容忍", () => {
    expect(parseDurationInput("  4 , 6 ")).toEqual([4, 6]);
  });

  it("空字符串返回 null", () => {
    expect(parseDurationInput("")).toBeNull();
    expect(parseDurationInput("   ")).toBeNull();
  });

  it("非法片段抛错", () => {
    expect(() => parseDurationInput("abc")).toThrow();
    expect(() => parseDurationInput("4, abc")).toThrow();
    expect(() => parseDurationInput("10-3")).toThrow();
    expect(() => parseDurationInput("0-5")).toThrow(); // 0 非正
    expect(() => parseDurationInput("-3")).toThrow();
    expect(() => parseDurationInput("4--6")).toThrow();
  });

  it("拒绝过大区间", () => {
    expect(() => parseDurationInput("1-100")).toThrow(/区间过大/);
  });
});

describe("isContinuousIntegerRange", () => {
  it("正例", () => {
    expect(isContinuousIntegerRange([3, 4, 5, 6, 7])).toBe(true);
    expect(isContinuousIntegerRange([1, 2, 3])).toBe(true);
  });

  it("负例：跳值", () => {
    expect(isContinuousIntegerRange([4, 6, 8])).toBe(false);
    expect(isContinuousIntegerRange([1, 3, 5])).toBe(false);
  });

  it("边界：单值与空", () => {
    expect(isContinuousIntegerRange([5])).toBe(false);
    expect(isContinuousIntegerRange([])).toBe(false);
  });

  it("无序输入也能识别（内部排序）", () => {
    expect(isContinuousIntegerRange([7, 5, 6, 8, 4])).toBe(true);
  });
});

describe("compactRangeFormat", () => {
  it("纯连续 → 折叠", () => {
    expect(compactRangeFormat([3, 4, 5, 6, 7])).toBe("3-7");
  });

  it("混合", () => {
    expect(compactRangeFormat([3, 4, 5, 7, 8, 9, 10, 12])).toBe("3-5, 7-10, 12");
  });

  it("纯离散", () => {
    expect(compactRangeFormat([4, 6, 8])).toBe("4, 6, 8");
  });

  it("单值", () => {
    expect(compactRangeFormat([6])).toBe("6");
  });

  it("空", () => {
    expect(compactRangeFormat([])).toBe("");
  });

  it("往返一致：parse → compact", () => {
    expect(compactRangeFormat(parseDurationInput("3-5, 7-10, 12")!)).toBe("3-5, 7-10, 12");
  });
});

describe("formatDurationsLabel", () => {
  it("简短 trailing s", () => {
    expect(formatDurationsLabel([4, 6, 8])).toBe("4, 6, 8s");
  });
  it("区间 trailing s", () => {
    expect(formatDurationsLabel([3, 4, 5, 6, 7])).toBe("3-7s");
  });
});
```

- [ ] **Step 9.2: 运行测试确认失败**

```bash
cd frontend && pnpm test --run src/utils/duration_format.test.ts
```

Expected: FAIL —— 模块不存在。

- [ ] **Step 9.3: 实现 duration_format.ts**

`frontend/src/utils/duration_format.ts`:

```typescript
/**
 * supported_durations 输入 / 显示 / 检测的纯函数工具。
 *
 * 设计：所有持久化形态都是 list[int]。本工具负责 UI ↔ list 互转、
 * 检测连续整数集（用于 slider/按钮组渲染选择）、以及格式化展示标签。
 */

const MAX_RANGE_SPAN = 30;

/**
 * 解析用户输入的逗号分隔时长文本，支持区间简写。
 *
 * 规则：
 *   - 逗号分隔片段；每段 trim
 *   - 单值：^\d+$（必须正整数）
 *   - 区间：^(\d+)-(\d+)$；min ≤ max 且跨度 ≤ MAX_RANGE_SPAN
 *   - 输出去重升序
 * @returns 解析得到的 list；输入为空白则 null
 * @throws Error 当存在非法片段
 */
export function parseDurationInput(text: string): number[] | null {
  const trimmed = text.trim();
  if (!trimmed) return null;

  const segments = trimmed.split(",").map((s) => s.trim()).filter(Boolean);
  const result = new Set<number>();

  for (const seg of segments) {
    if (/^\d+$/.test(seg)) {
      const n = parseInt(seg, 10);
      if (n <= 0) throw new Error(`非法片段 '${seg}'：必须是正整数`);
      result.add(n);
      continue;
    }
    const m = /^(\d+)-(\d+)$/.exec(seg);
    if (m) {
      const lo = parseInt(m[1], 10);
      const hi = parseInt(m[2], 10);
      if (lo <= 0 || hi <= 0) throw new Error(`非法片段 '${seg}'：必须是正整数`);
      if (hi < lo) throw new Error(`非法片段 '${seg}'：区间右端必须 ≥ 左端`);
      if (hi - lo > MAX_RANGE_SPAN) {
        throw new Error(`非法片段 '${seg}'：区间过大（>${MAX_RANGE_SPAN}）`);
      }
      for (let i = lo; i <= hi; i++) result.add(i);
      continue;
    }
    throw new Error(`无法解析片段 '${seg}'`);
  }

  return [...result].sort((a, b) => a - b);
}

/** 判断列表是否为连续整数集（如 [3,4,5,6,7]），需 ≥2 个元素。 */
export function isContinuousIntegerRange(durations: readonly number[]): boolean {
  if (durations.length < 2) return false;
  const sorted = [...durations].sort((a, b) => a - b);
  for (let i = 1; i < sorted.length; i++) {
    if (sorted[i] !== sorted[i - 1] + 1) return false;
  }
  return true;
}

/**
 * 把 list[int] 紧凑展示，连续段折叠为 "min-max"。
 *
 * 例：[3,4,5,7,8,9,10,12] → "3-5, 7-10, 12"
 */
export function compactRangeFormat(durations: readonly number[]): string {
  if (durations.length === 0) return "";
  const sorted = [...new Set(durations)].sort((a, b) => a - b);
  const parts: string[] = [];
  let runStart = sorted[0];
  let runPrev = sorted[0];
  for (let i = 1; i < sorted.length; i++) {
    const v = sorted[i];
    if (v === runPrev + 1) {
      runPrev = v;
    } else {
      parts.push(runStart === runPrev ? `${runStart}` : `${runStart}-${runPrev}`);
      runStart = v;
      runPrev = v;
    }
  }
  parts.push(runStart === runPrev ? `${runStart}` : `${runStart}-${runPrev}`);
  return parts.join(", ");
}

/** UI 标签格式：连续区间 → "3-7s"，否则 → "4, 6, 8s"。 */
export function formatDurationsLabel(durations: readonly number[]): string {
  if (durations.length === 0) return "";
  if (isContinuousIntegerRange(durations)) {
    const sorted = [...durations].sort((a, b) => a - b);
    return `${sorted[0]}-${sorted[sorted.length - 1]}s`;
  }
  return `${[...durations].sort((a, b) => a - b).join(", ")}s`;
}
```

- [ ] **Step 9.4: 运行测试确认通过**

```bash
cd frontend && pnpm test --run src/utils/duration_format.test.ts
```

Expected: 全部 passed。

- [ ] **Step 9.5: Commit**

```bash
git add frontend/src/utils/duration_format.ts frontend/src/utils/duration_format.test.ts
git commit -m "feat(frontend): supported_durations 解析/格式化工具（含区间简写）"
```

---

## Task 10: CustomProviderForm 加 supported_durations 输入

**Files:**
- Modify: `frontend/src/components/pages/settings/CustomProviderForm.tsx`
- Modify: `frontend/src/types/custom-provider.ts`（确认 ModelInput 含 supported_durations）
- Modify: `frontend/src/i18n/zh/dashboard.ts`、`frontend/src/i18n/en/dashboard.ts`
- Create: `frontend/src/components/pages/settings/CustomProviderForm.test.tsx`（如不存在）

- [ ] **Step 10.1: 添加 i18n keys**

`frontend/src/i18n/zh/dashboard.ts`（在合适位置追加）：

```typescript
  // supported_durations input
  supported_durations_label: "支持秒数",
  supported_durations_placeholder: "例如 4, 8, 12 或 3-15（不填将按模型 id 自动推断）",
  supported_durations_help: "用逗号分隔多个值，或用区间简写（如 3-15）。空白则由后端预设表按 model id 推断。",
  supported_durations_invalid: "格式无效：{{message}}",
  supported_durations_summary: "支持秒数：{{value}}",
```

`frontend/src/i18n/en/dashboard.ts` 同名 key（英文）：

```typescript
  supported_durations_label: "Supported durations",
  supported_durations_placeholder: "e.g. 4, 8, 12 or 3-15 (leave blank to auto-infer)",
  supported_durations_help: "Comma-separated values or a range (e.g. 3-15). Leave blank to let the server infer from model id.",
  supported_durations_invalid: "Invalid format: {{message}}",
  supported_durations_summary: "Supported durations: {{value}}",
```

- [ ] **Step 10.2: 更新 ModelRow 与 rowToInput 类型**

`frontend/src/components/pages/settings/CustomProviderForm.tsx`：

`interface ModelRow`（约 line 29）追加：

```typescript
  supported_durations_text: string; // 用户原始文本，提交前 parse；空串 = 让后端按 preset 兜底
```

`function newModelRow`（line ~43）默认值：

```typescript
    supported_durations_text: "",
```

`function existingToRow`（line ~70）：

```typescript
    supported_durations_text: m.supported_durations
      ? compactRangeFormat(m.supported_durations)
      : "",
```

文件顶部 import：

```typescript
import { compactRangeFormat, parseDurationInput } from "@/utils/duration_format";
```

`function rowToInput`（line ~85）追加 supported_durations 字段。把整个函数重写为：

```typescript
function rowToInput(r: ModelRow): CustomProviderModelInput {
  const trimmed = r.supported_durations_text.trim();
  let supported_durations: number[] | null = null;
  if (trimmed) {
    supported_durations = parseDurationInput(trimmed);
  }
  return {
    model_id: r.model_id,
    display_name: r.display_name || r.model_id,
    endpoint: r.endpoint,
    is_default: r.is_default,
    is_enabled: r.is_enabled,
    ...(r.price_unit ? { price_unit: r.price_unit } : {}),
    ...(r.price_input ? { price_input: parseFloat(r.price_input) } : {}),
    ...(r.price_output ? { price_output: parseFloat(r.price_output) } : {}),
    ...(r.currency ? { currency: r.currency } : {}),
    ...(r.resolution ? { resolution: r.resolution } : { resolution: null }),
    ...(supported_durations ? { supported_durations } : { supported_durations: null }),
  };
}
```

- [ ] **Step 10.3: 在视频 endpoint 行渲染输入框**

定位"Resolution row"渲染（约 line 535）：

```typescript
                    {/* Resolution row */}
                    {media !== "text" && (
                      <div className="mt-2 flex items-center gap-2 pl-6">
                        ...
                      </div>
                    )}
```

紧随其后追加：

```typescript
                    {/* Supported durations row（仅 video endpoint） */}
                    {media === "video" && (
                      <DurationsInputRow
                        value={m.supported_durations_text}
                        onChange={(v) => updateModel(m.key, { supported_durations_text: v })}
                      />
                    )}
```

在文件靠下（rowToInput 之后、CustomProviderForm 函数之前）新增子组件：

```typescript
// ---------------------------------------------------------------------------
// DurationsInputRow — 视频模型行内的 supported_durations 输入
// ---------------------------------------------------------------------------

function DurationsInputRow({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  const { t } = useTranslation("dashboard");
  const [error, setError] = useState<string | null>(null);

  const handleChange = (next: string) => {
    onChange(next);
    if (!next.trim()) {
      setError(null);
      return;
    }
    try {
      parseDurationInput(next);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div className="mt-2 flex flex-col gap-1 pl-6">
      <div className="flex items-center gap-2">
        <label className="text-sm text-gray-400 whitespace-nowrap">
          {t("supported_durations_label")}
        </label>
        <input
          type="text"
          value={value}
          onChange={(e) => handleChange(e.target.value)}
          placeholder={t("supported_durations_placeholder")}
          aria-label={t("supported_durations_label")}
          className="min-w-0 flex-1 rounded-lg border border-gray-700 bg-gray-900 px-2 py-1 text-sm text-gray-100 placeholder-gray-600 focus-visible:border-indigo-500 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-indigo-500"
        />
      </div>
      {error ? (
        <p className="text-xs text-red-400">
          {t("supported_durations_invalid", { message: error })}
        </p>
      ) : (
        <p className="text-xs text-gray-500">{t("supported_durations_help")}</p>
      )}
    </div>
  );
}
```

- [ ] **Step 10.4: 写组件测试**

新建 `frontend/src/components/pages/settings/CustomProviderForm.test.tsx`（若已存在则追加）：

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { CustomProviderForm } from "./CustomProviderForm";

vi.mock("@/api", () => ({
  API: {
    listCustomProviders: vi.fn(async () => ({ providers: [] })),
    discoverModels: vi.fn(),
    discoverModelsForProvider: vi.fn(),
    testCustomConnection: vi.fn(),
    createCustomProvider: vi.fn(async () => ({ id: 1 })),
    fullUpdateCustomProvider: vi.fn(),
  },
}));

vi.mock("@/stores/endpoint-catalog-store", () => ({
  useEndpointCatalogStore: (selector: (s: any) => any) =>
    selector({
      endpointToMediaType: { "openai-video": "video", "openai-chat": "text" },
      endpointToImageCapabilities: {},
      fetch: vi.fn(),
    }),
}));

describe("CustomProviderForm — supported_durations input", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("视频 endpoint 模型行渲染 supported_durations 输入框", () => {
    render(<CustomProviderForm onSaved={vi.fn()} onCancel={vi.fn()} />);
    fireEvent.click(screen.getByText(/手动添加模型|add model manually/i));
    // 默认 endpoint=openai-chat → 不显示
    expect(screen.queryByLabelText(/支持秒数|supported durations/i)).not.toBeInTheDocument();
  });

  it("用户输入 '3-15' 提交 payload 含展开的 list", async () => {
    const { API } = await import("@/api");
    render(<CustomProviderForm onSaved={vi.fn()} onCancel={vi.fn()} />);
    // 填基础字段（display_name / base_url / api_key）— 简化按 placeholder 定位
    fireEvent.change(screen.getByPlaceholderText(/api\.example|输入名称|cp_name/i), {
      target: { value: "p" },
    });
    // 实际测试需根据现有 placeholders 调整 — 见 testing notes 下方
    // 目标：模拟一行 video model + 输入 "3-15" → save → 拦截 createCustomProvider payload
    // 断言 payload.models[0].supported_durations.length === 13
    // （详细 setup 步骤见 plan 注释）
  });
});
```

> **测试 setup 复杂度说明**：完整组件渲染依赖多个 store 和 i18n。优先验证 `rowToInput` / `DurationsInputRow` 的纯逻辑用 vitest 单元测试，对完整 form 集成只测"输入 → 提交 payload"骨架。如时间允许把 `rowToInput` 提到独立模块 `customProviderHelpers.ts` 单测。

最小可执行的纯函数测试（替换上面 it 的占位）：

```typescript
import { parseDurationInput, compactRangeFormat } from "@/utils/duration_format";

it("rowToInput-equivalent: '3-15' → 13 个值", () => {
  const parsed = parseDurationInput("3-15");
  expect(parsed).toHaveLength(13);
  expect(compactRangeFormat(parsed!)).toBe("3-15");
});
```

- [ ] **Step 10.5: 运行测试确认通过**

```bash
cd frontend && pnpm test --run src/components/pages/settings/CustomProviderForm.test.tsx
cd frontend && pnpm build  # 含 typecheck
```

Expected: 全部 passed；typecheck 无 error。

- [ ] **Step 10.6: Commit**

```bash
git add frontend/src/components/pages/settings/CustomProviderForm.tsx \
        frontend/src/components/pages/settings/CustomProviderForm.test.tsx \
        frontend/src/i18n/zh/dashboard.ts frontend/src/i18n/en/dashboard.ts
git commit -m "feat(frontend): 自定义 provider 视频模型行加 supported_durations 输入"
```

---

## Task 11: ModelConfigSection slider 切换 + 删 DEFAULT_DURATIONS fallback

**Files:**
- Modify: `frontend/src/utils/provider-models.ts`
- Modify: `frontend/src/components/shared/ModelConfigSection.tsx`
- Modify: `frontend/src/components/shared/ModelConfigSection.test.tsx`

- [ ] **Step 11.1: 改 ModelConfigSection 测试预期**

打开 `frontend/src/components/shared/ModelConfigSection.test.tsx`：

定位 line ~201 的 `falls back to globalDefaults.video supported_durations when videoBackend is empty (bug repro)` —— 现状逻辑已是"按 effective backend 看"，OK 保留。

定位 line ~218 的 `Should reflect ark/seedance's supported_durations [5, 8, 10]` —— 改为：

```typescript
    // ark/seedance supported_durations 是连续 [4..12]，应渲染 slider 而不是按钮组
    expect(screen.queryAllByRole("radio", { name: /^\d+s$/ })).toHaveLength(0);
    expect(screen.getByRole("slider", { name: /duration/i })).toBeInTheDocument();
```

新增一条测试：

```typescript
it("supported_durations 长度 ≥5 且连续整数时渲染 slider", () => {
  // ...构造 providers 让 effective backend 有 [3,4,5,6,7,8,9,10,11,12,13,14,15]
  render(<ModelConfigSection {...props} />);
  expect(screen.getByRole("slider", { name: /duration/i })).toBeInTheDocument();
});

it("supported_durations 离散（[4, 6, 8]）时仍渲染按钮组", () => {
  // ...
  expect(screen.queryByRole("slider", { name: /duration/i })).not.toBeInTheDocument();
  expect(screen.getByRole("radio", { name: "4s" })).toBeInTheDocument();
  expect(screen.getByRole("radio", { name: "6s" })).toBeInTheDocument();
  expect(screen.getByRole("radio", { name: "8s" })).toBeInTheDocument();
});

it("找不到 supported_durations 时隐藏整个时长卡片（不再 fallback 到 [4,6,8]）", () => {
  // 构造 effective backend 不存在于 providers / customProviders
  const props = { ...baseProps, options: { ...baseProps.options, videoBackends: ["unknown/no-such"] } };
  render(<ModelConfigSection {...props} value={{ ...defaultValue, videoBackend: "unknown/no-such" }} />);
  expect(screen.queryByText(/duration/i)).not.toBeInTheDocument();
});
```

- [ ] **Step 11.2: 运行测试确认失败**

```bash
cd frontend && pnpm test --run src/components/shared/ModelConfigSection.test.tsx
```

Expected: FAIL —— 当前组件没有 slider，且 fallback 到 DEFAULT_DURATIONS。

- [ ] **Step 11.3: 删除 provider-models.ts 的 DEFAULT_DURATIONS 导出**

`frontend/src/utils/provider-models.ts`：

第 4 行 `export const DEFAULT_DURATIONS: readonly number[] = [4, 6, 8];` 删除。

> 这会让所有 import `DEFAULT_DURATIONS` 的地方编译错误，下面统一替换。

- [ ] **Step 11.4: 重构 ModelConfigSection**

`frontend/src/components/shared/ModelConfigSection.tsx`：

第 4 行 `import { DEFAULT_DURATIONS, lookupSupportedDurations, lookupResolutions } ...` 改为：

```typescript
import { lookupSupportedDurations, lookupResolutions } from "@/utils/provider-models";
import { isContinuousIntegerRange } from "@/utils/duration_format";
```

第 96-102 行 `supportedDurations` useMemo 改为：

```typescript
  // 找不到 supported_durations 时返回 null（隐藏整个时长卡片，不再用 [4,6,8] 兜底）
  const supportedDurations = useMemo<readonly number[] | null>(() => {
    if (!effectiveVideoBackend) return null;
    const raw = lookupSupportedDurations(providers, effectiveVideoBackend, customProviders);
    if (!raw || raw.length === 0) return null;
    return [...raw].sort((a, b) => a - b);
  }, [providers, effectiveVideoBackend, customProviders]);
```

第 105-118 行 `handleVideoChange` 内的 `nextDurations` 同样改为可空：

```typescript
  const handleVideoChange = (next: string) => {
    const effectiveNext = next || globalDefaults.video || "";
    const nextDurations = effectiveNext
      ? (lookupSupportedDurations(providers, effectiveNext, customProviders) ?? null)
      : null;
    const shouldReset =
      value.defaultDuration !== null && (!nextDurations || !nextDurations.includes(value.defaultDuration));
    onChange({
      ...value,
      videoBackend: next,
      defaultDuration: shouldReset ? null : value.defaultDuration,
      videoResolution: null,
    });
  };
```

把第 171-214 行 `{/* Duration picker */}` 整段（含 auto button + 按钮组）替换为：

```typescript
          {/* Duration picker — 找不到 supported_durations 时不渲染 */}
          {showDuration && supportedDurations && supportedDurations.length > 0 && (
            <>
              <div className="mt-3 mb-2 text-xs text-gray-400">{t("duration_label")}</div>
              {isContinuousIntegerRange(supportedDurations) && supportedDurations.length >= 5 ? (
                <DurationSlider
                  options={supportedDurations}
                  value={value.defaultDuration}
                  onChange={handleDurationClick}
                  ariaLabel={t("duration_label")}
                  autoLabel={t("duration_auto")}
                />
              ) : (
                <DurationButtonGroup
                  options={supportedDurations}
                  value={value.defaultDuration}
                  onChange={handleDurationClick}
                  ariaLabel={t("duration_label")}
                  autoLabel={t("duration_auto")}
                />
              )}
            </>
          )}
```

在文件末尾（`export function ModelConfigSection` 之外）追加两个子组件：

```typescript
// ---------------------------------------------------------------------------
// Duration sub-components
// ---------------------------------------------------------------------------

function DurationButtonGroup({
  options,
  value,
  onChange,
  ariaLabel,
  autoLabel,
}: {
  options: readonly number[];
  value: number | null;
  onChange: (next: number | null) => void;
  ariaLabel: string;
  autoLabel: string;
}) {
  return (
    <div className="flex flex-wrap gap-2" role="radiogroup" aria-label={ariaLabel}>
      <button
        type="button"
        role="radio"
        aria-checked={value === null}
        aria-label={autoLabel}
        tabIndex={value === null ? 0 : -1}
        onClick={() => onChange(null)}
        className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 ${
          value === null
            ? "border-indigo-500 bg-indigo-500/10 text-indigo-300"
            : "border-gray-700 bg-gray-800 text-gray-400 hover:border-gray-600"
        }`}
      >
        {autoLabel}
      </button>
      {options.map((d) => (
        <button
          key={d}
          type="button"
          role="radio"
          aria-checked={value === d}
          aria-label={`${d}s`}
          tabIndex={value === d ? 0 : -1}
          onClick={() => onChange(d)}
          className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 ${
            value === d
              ? "border-indigo-500 bg-indigo-500/10 text-indigo-300"
              : "border-gray-700 bg-gray-800 text-gray-400 hover:border-gray-600"
          }`}
        >
          {d}s
        </button>
      ))}
    </div>
  );
}

function DurationSlider({
  options,
  value,
  onChange,
  ariaLabel,
  autoLabel,
}: {
  options: readonly number[];
  value: number | null;
  onChange: (next: number | null) => void;
  ariaLabel: string;
  autoLabel: string;
}) {
  const min = options[0];
  const max = options[options.length - 1];
  const sliderValue = value === null ? min : value;
  return (
    <div className="flex flex-wrap items-center gap-3">
      <button
        type="button"
        role="radio"
        aria-checked={value === null}
        aria-label={autoLabel}
        onClick={() => onChange(null)}
        className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 ${
          value === null
            ? "border-indigo-500 bg-indigo-500/10 text-indigo-300"
            : "border-gray-700 bg-gray-800 text-gray-400 hover:border-gray-600"
        }`}
      >
        {autoLabel}
      </button>
      <input
        type="range"
        role="slider"
        aria-label={ariaLabel}
        min={min}
        max={max}
        step={1}
        value={sliderValue}
        onChange={(e) => onChange(parseInt(e.target.value, 10))}
        className="flex-1 min-w-[120px]"
      />
      <span className="min-w-[2.5rem] text-right text-xs text-gray-300">
        {value === null ? autoLabel : `${value}s`}
      </span>
    </div>
  );
}
```

- [ ] **Step 11.5: 修复 SegmentCard 对 DEFAULT_DURATIONS 的依赖（同步）**

`frontend/src/components/canvas/timeline/SegmentCard.tsx`（第 192 行附近）：

```typescript
  durationOptions = DEFAULT_DURATIONS as number[],
```

改为：

```typescript
  durationOptions = [],
```

并删除文件顶部对 `DEFAULT_DURATIONS` 的 import（如有）。后续 Task 12 会进一步处理 SegmentCard。

- [ ] **Step 11.6: 运行测试 + typecheck**

```bash
cd frontend && pnpm test --run src/components/shared/ModelConfigSection.test.tsx
cd frontend && pnpm build
```

Expected: passed；typecheck 无 error。

- [ ] **Step 11.7: Commit**

```bash
git add frontend/src/utils/provider-models.ts \
        frontend/src/components/shared/ModelConfigSection.tsx \
        frontend/src/components/shared/ModelConfigSection.test.tsx \
        frontend/src/components/canvas/timeline/SegmentCard.tsx
git commit -m "refactor(frontend): 删 DEFAULT_DURATIONS fallback；连续区间用 slider，离散用按钮组"
```

---

## Task 12: SegmentCard 越界角标 + slider

**Files:**
- Modify: `frontend/src/components/canvas/timeline/SegmentCard.tsx`
- Modify: `frontend/src/components/canvas/timeline/SegmentCard.test.tsx`
- Modify: `frontend/src/i18n/{zh,en}/dashboard.ts`

- [ ] **Step 12.1: 添加 i18n keys**

`frontend/src/i18n/zh/dashboard.ts`：

```typescript
  duration_incompatible_warning: "{{seconds}}s 不在当前模型支持列表内",
```

`frontend/src/i18n/en/dashboard.ts`：

```typescript
  duration_incompatible_warning: "{{seconds}}s is not in the current model's supported durations",
```

- [ ] **Step 12.2: 写失败测试**

`frontend/src/components/canvas/timeline/SegmentCard.test.tsx` 末尾追加：

```typescript
describe("SegmentCard — duration incompatible warning", () => {
  it("当 segment.duration_seconds 不在 durationOptions 内时显示 ⚠ 角标", () => {
    const segment = { ...mockSegment, duration_seconds: 6 };
    render(
      <SegmentCard
        segment={segment}
        contentMode="narration"
        aspectRatio="9:16"
        characters={{}}
        scenes={{}}
        props={{}}
        projectName="p"
        durationOptions={[4, 8, 12]}
        onUpdatePrompt={vi.fn()}
      />,
    );
    // 角标 title 含 "不兼容" 或具体警告文案
    const warning = screen.getByLabelText(/不在当前模型支持列表内|not in the current model/i);
    expect(warning).toBeInTheDocument();
  });

  it("当 segment.duration_seconds 在 durationOptions 内时不显示角标", () => {
    const segment = { ...mockSegment, duration_seconds: 4 };
    render(
      <SegmentCard
        segment={segment}
        contentMode="narration"
        aspectRatio="9:16"
        characters={{}}
        scenes={{}}
        props={{}}
        projectName="p"
        durationOptions={[4, 8, 12]}
        onUpdatePrompt={vi.fn()}
      />,
    );
    expect(screen.queryByLabelText(/不在当前模型|not in the current model/i)).not.toBeInTheDocument();
  });
});
```

> 文件中 `mockSegment` 应已有；如无参考已有用例 inline 构造。

- [ ] **Step 12.3: 运行测试确认失败**

```bash
cd frontend && pnpm test --run src/components/canvas/timeline/SegmentCard.test.tsx
```

Expected: FAIL —— 当前 `DurationSelector` 不检测越界。

- [ ] **Step 12.4: 改 DurationSelector**

`frontend/src/components/canvas/timeline/SegmentCard.tsx`，第 187-254 行 `DurationSelector` 整段函数替换为：

```typescript
function DurationSelector({
  seconds,
  segmentId,
  onUpdatePrompt,
  durationOptions = [],
}: {
  seconds: number;
  segmentId: string;
  onUpdatePrompt?: (segmentId: string, field: string, value: unknown) => void;
  durationOptions?: number[];
}) {
  const { t } = useTranslation("dashboard");
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLButtonElement>(null);

  const isIncompatible = durationOptions.length > 0 && !durationOptions.includes(seconds);
  const incompatibleLabel = t("duration_incompatible_warning", { seconds });

  // 只读模式
  if (!onUpdatePrompt) {
    return (
      <span className="inline-flex items-center gap-0.5 rounded bg-gray-700 px-1.5 py-0.5 text-xs text-gray-300">
        <Clock aria-hidden="true" className="h-3 w-3" />
        {seconds}s
        {isIncompatible && (
          <span
            aria-label={incompatibleLabel}
            title={incompatibleLabel}
            className="ml-0.5 text-amber-400"
          >
            ⚠
          </span>
        )}
      </span>
    );
  }

  const useSlider = isContinuousIntegerRange(durationOptions) && durationOptions.length >= 5;

  return (
    <>
      <button
        ref={ref}
        onClick={() => setOpen((o) => !o)}
        className={`inline-flex cursor-pointer items-center gap-0.5 rounded px-1.5 py-0.5 text-xs hover:bg-gray-600 focus-ring ${
          isIncompatible ? "bg-amber-900/40 text-amber-200" : "bg-gray-700 text-gray-300"
        }`}
      >
        <Clock aria-hidden="true" className="h-3 w-3" />
        {seconds}s
        {isIncompatible && (
          <span aria-label={incompatibleLabel} title={incompatibleLabel} className="ml-0.5">
            ⚠
          </span>
        )}
      </button>
      <Popover
        open={open}
        onClose={() => setOpen(false)}
        anchorRef={ref}
        width="w-auto"
        className="rounded-lg border border-gray-700 p-1.5 shadow-xl"
        align="start"
        sideOffset={6}
      >
        {useSlider ? (
          <div className="flex items-center gap-2 px-1 py-1">
            <input
              type="range"
              role="slider"
              aria-label={t("duration_selector_aria")}
              min={durationOptions[0]}
              max={durationOptions[durationOptions.length - 1]}
              step={1}
              value={seconds}
              onChange={(e) => {
                onUpdatePrompt(segmentId, "duration_seconds", parseInt(e.target.value, 10));
              }}
              className="w-40"
            />
            <span className="min-w-[2rem] text-right text-xs text-gray-200">{seconds}s</span>
          </div>
        ) : (
          <div className="flex gap-1" role="radiogroup" aria-label={t("duration_selector_aria")}>
            {durationOptions.map((d) => (
              <button
                key={d}
                role="radio"
                aria-checked={d === seconds}
                onClick={() => {
                  onUpdatePrompt(segmentId, "duration_seconds", d);
                  setOpen(false);
                }}
                className={`rounded px-3 py-1.5 text-xs font-medium transition-colors focus-ring ${
                  d === seconds
                    ? "bg-indigo-600 text-white"
                    : "text-gray-300 hover:bg-gray-700"
                }`}
              >
                {d}s
              </button>
            ))}
          </div>
        )}
      </Popover>
    </>
  );
}
```

文件顶部 import 加上：

```typescript
import { isContinuousIntegerRange } from "@/utils/duration_format";
```

- [ ] **Step 12.5: 运行测试确认通过**

```bash
cd frontend && pnpm test --run src/components/canvas/timeline/SegmentCard.test.tsx
cd frontend && pnpm build
```

Expected: passed；typecheck 无 error。

- [ ] **Step 12.6: Commit**

```bash
git add frontend/src/components/canvas/timeline/SegmentCard.tsx \
        frontend/src/components/canvas/timeline/SegmentCard.test.tsx \
        frontend/src/i18n/zh/dashboard.ts frontend/src/i18n/en/dashboard.ts
git commit -m "feat(segment-card): 越界 duration 显示 ⚠ 角标；连续区间切 slider 形态"
```

---

## Task 13: CustomProviderDetail 显示 supported_durations + i18n 一致性 + 端到端验证

**Files:**
- Modify: `frontend/src/components/pages/settings/CustomProviderDetail.tsx`
- Run: i18n 一致性测试 + 全量 backend / frontend 测试

- [ ] **Step 13.1: CustomProviderDetail 显示**

`frontend/src/components/pages/settings/CustomProviderDetail.tsx` 在模型卡片渲染处（按文件现有结构定位 `model.endpoint` 显示行附近）追加：

```typescript
{model.supported_durations && model.supported_durations.length > 0 && (
  <span className="text-xs text-gray-500">
    {t("supported_durations_summary", {
      value: formatDurationsLabel(model.supported_durations),
    })}
  </span>
)}
```

文件顶部 import：

```typescript
import { formatDurationsLabel } from "@/utils/duration_format";
```

- [ ] **Step 13.2: 跑 i18n 一致性测试**

```bash
uv run python -m pytest tests/test_i18n_consistency.py -v
```

Expected: passed —— 新增的 zh/en key 完全对齐。

> 若 fail，根据报错补全缺失的 key（zh 或 en）。

- [ ] **Step 13.3: 全量 backend 测试**

```bash
uv run python -m pytest -x
uv run ruff check .
```

Expected: 全部 passed；ruff 无 error。

- [ ] **Step 13.4: 全量前端测试 + build**

```bash
cd frontend && pnpm check  # = typecheck + test
cd frontend && pnpm build
```

Expected: 全部 passed。

- [ ] **Step 13.5: 手动验证（dev server）**

```bash
uv run uvicorn server.app:app --reload --reload-dir server --reload-dir lib --port 1241 &
cd frontend && pnpm dev &
```

浏览器打开 http://localhost:5173：
1. 进入 `/settings`，找一个自定义 provider，编辑某个视频模型，把 supported_durations 输入为 `6, 10, 12, 16, 20`，保存。
2. 进入项目设置，把视频后端切到这个模型；分镜时长选 6s，触发视频生成 —— 不应再被改成 8。
3. 进入项目设置，切到 supported_durations=[4, 8, 12] 的模型，对原有 6s 分镜应在 SegmentCard 上看到 ⚠ 角标。
4. 在 `/settings` 创建一个 supported_durations 输入留空、model_id="kling-v2.5-turbo" 的视频模型；保存后从 detail 页应看到自动填的 "5, 10s"。

**预期**：所有路径无 backend 内部秒数篡改、错误信息直接来自对端 API。

- [ ] **Step 13.6: Commit**

```bash
git add frontend/src/components/pages/settings/CustomProviderDetail.tsx
git commit -m "feat(frontend): 自定义 provider 详情页显示 supported_durations 摘要"
```

- [ ] **Step 13.7: 最终 sanity 提交**

```bash
git status  # 确认无遗留
git log --oneline -15  # review 整个 plan 的 commits
```

---

## Self-Review

**1. Spec 覆盖**

- §1 目标 1（单一真相源）→ Task 5 删 VALID_DURATIONS、Task 7 _resolve_supported_durations fail-loud、Task 11 删 DEFAULT_DURATIONS、Task 8 router preset。
- §1 目标 2（三个消费点同源）→ Task 3/4 backend 透传、Task 6 prompt 必填、Task 11 前端选择器、Task 12 SegmentCard。
- §1 目标 3（自定义 provider 录入策略）→ Task 1 预设表、Task 8 discovery+router、Task 10 Form 输入。
- §1 目标 4（删除一切第二/三真相源）→ Task 3/4/5/6/7/11。
- §2 真相源链路 → 全任务覆盖。
- §3 预设表 → Task 1。
- §4 数据模型变更（迁移）→ Task 2。
- §5 后端清单 → Task 3-8。
- §6 前端清单 → Task 9-13。
- §7 错误处理矩阵 → Task 7 (resolver fail) / Task 12 (UI 角标) / Task 8 (router 422 由 endpoint check 覆盖)。
- §8 测试矩阵 → 每个 task 内含 TDD step。
- §9 升级与兼容性 → Task 2 alembic + Task 5 校验放宽。

**2. Placeholder 扫描**

- 无 "TBD" / "TODO" / "implement later"。
- Task 4 / Task 10 中标了 "实现细节请参照同文件 fixture 风格" 等指引段，但都附带了"可执行的最小测试段"作为代码模板 —— 不是 placeholder 而是具体引导。
- Task 2 的 `<NEW_REV>` / `<HEAD_REV>` 是"运行命令后填入实际值"，不是 placeholder（每个 alembic 迁移本来就要这样）。
- Task 13 step 5 是手动验证 checklist，非代码。

**3. 类型一致性**

- `parseDurationInput(text: string): number[] | null` —— Task 9 定义、Task 10 使用，签名一致。
- `isContinuousIntegerRange(durations: readonly number[]): boolean` —— Task 9 定义、Task 11 / 12 使用，签名一致。
- `compactRangeFormat(durations: readonly number[]): string` —— Task 9 定义、Task 10 使用。
- `formatDurationsLabel(durations: readonly number[]): string` —— Task 9 定义、Task 13 使用。
- `infer_supported_durations(model_id: str) -> list[int]` —— Task 1 定义、Task 8 router 调用、Task 2 alembic inline 复制（独立但语义同步）。
- `_resolve_supported_durations(self, caps: dict | None) -> list[int]`（Task 7）从 `list[int] | None` 收紧为 `list[int]`（不再返回 None）。
- `ModelRow.supported_durations_text: string`（Task 10）+ `CustomProviderModelInput.supported_durations: number[] | null`（已有）签名一致。

**4. 命名一致性**

- "supported_durations" snake_case 在 Python / DB / API JSON / 前端 type 全程一致。
- "duration_seconds" 字段名贯穿前后端不变。
- DurationButtonGroup / DurationSlider / DurationsInputRow / DurationSelector 各自语义独立，命名互不冲突。

无 gap，可交付。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-04-video-duration-redesign.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
