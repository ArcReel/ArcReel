# 单集费用估算 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Web UI 中展示每集的预估费用和实际费用，支持项目概览→分镜板→分镜卡片三级展示。

**Architecture:** 后端新增 `segment_id` 字段贯穿 API 调用追踪链路，新建 `cost_estimation` 服务读取剧本 + 模型配置计算预估、查询 ApiCall 累计实际费用。前端新增 cost store，三个组件层级各自从 store 读取对应粒度数据。

**Tech Stack:** Python/FastAPI/SQLAlchemy (后端), React 19/TypeScript/Zustand/Tailwind CSS 4 (前端)

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `lib/db/models/api_call.py` | 新增 `segment_id` 列 |
| Create | Alembic migration | 数据库添加 `segment_id` 字段 + 索引 |
| Modify | `lib/db/repositories/usage_repo.py` | `start_call()` 接受 `segment_id`；新增 `get_actual_costs_by_segment()` |
| Modify | `lib/usage_tracker.py` | `start_call()` 透传 `segment_id` |
| Modify | `lib/media_generator.py` | `generate_image_async`/`generate_video_async` 传 `resource_id` 作为 `segment_id` |
| Create | `server/services/cost_estimation.py` | 费用估算服务（预估 + 实际 + 汇总） |
| Create | `server/routers/cost_estimation.py` | API 路由 |
| Modify | `server/app.py` | 注册新路由 |
| Create | `frontend/src/types/cost.ts` | 费用估算类型定义 |
| Modify | `frontend/src/types/index.ts` | 导出 cost types |
| Modify | `frontend/src/api.ts` | 新增 `getCostEstimate()` |
| Create | `frontend/src/stores/cost-store.ts` | 费用估算 Zustand store |
| Modify | `frontend/src/components/canvas/OverviewCanvas.tsx` | 项目费用汇总 + 剧集费用列 |
| Modify | `frontend/src/components/canvas/timeline/TimelineCanvas.tsx` | 单集费用栏 |
| Modify | `frontend/src/components/canvas/timeline/SegmentCard.tsx` | 分镜卡片费用内联 |
| Create | `tests/test_cost_estimation_service.py` | 服务层测试 |
| Create | `tests/test_cost_estimation_router.py` | 路由层测试 |

---

### Task 1: ApiCall 模型新增 segment_id 字段

**Files:**
- Modify: `lib/db/models/api_call.py:38` (在 `output_path` 后添加)
- Modify: `lib/db/repositories/usage_repo.py:18-43` (`_row_to_dict` 加 `segment_id`)

- [ ] **Step 1: 修改 ApiCall 模型**

在 `lib/db/models/api_call.py` 的 `output_path` 字段后添加 `segment_id`：

```python
    output_path: Mapped[str | None] = mapped_column(Text)
    segment_id: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

- [ ] **Step 2: 更新 _row_to_dict**

在 `lib/db/repositories/usage_repo.py` 的 `_row_to_dict` 函数中，在 `"output_path"` 行后添加：

```python
        "segment_id": row.segment_id,
```

- [ ] **Step 3: 生成 Alembic migration**

Run: `cd /Users/pollochen/MyProjects/ArcReel/.worktrees/feature/episode-cost-estimation && uv run alembic revision --autogenerate -m "add segment_id to api_calls"`
Expected: 生成新的 migration 文件

- [ ] **Step 4: 应用 migration**

Run: `uv run alembic upgrade head`
Expected: OK

- [ ] **Step 5: Commit**

```bash
git add lib/db/models/api_call.py lib/db/repositories/usage_repo.py alembic/versions/
git commit -m "feat: ApiCall 新增 segment_id 字段"
```

---

### Task 2: UsageRepository 和 UsageTracker 支持 segment_id

**Files:**
- Modify: `lib/db/repositories/usage_repo.py:47-81`
- Modify: `lib/usage_tracker.py:24-51`
- Test: `tests/test_usage_tracker.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_usage_tracker.py` 添加：

```python
    async def test_start_call_with_segment_id(self, tracker):
        call_id = await tracker.start_call(
            project_name="demo",
            call_type="image",
            model="gemini-3.1-flash-image-preview",
            resolution="1K",
            segment_id="E1S001",
        )
        await tracker.finish_call(call_id, status="success", output_path="a.png")

        result = await tracker.get_calls(project_name="demo")
        item = result["items"][0]
        assert item["segment_id"] == "E1S001"

    async def test_start_call_without_segment_id(self, tracker):
        call_id = await tracker.start_call(
            project_name="demo",
            call_type="image",
            model="gemini-3.1-flash-image-preview",
            resolution="1K",
        )
        await tracker.finish_call(call_id, status="success", output_path="a.png")

        result = await tracker.get_calls(project_name="demo")
        item = result["items"][0]
        assert item["segment_id"] is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_usage_tracker.py::TestUsageTracker::test_start_call_with_segment_id -v`
Expected: FAIL — `start_call()` 不接受 `segment_id` 参数

- [ ] **Step 3: 修改 UsageRepository.start_call**

在 `lib/db/repositories/usage_repo.py:47-81`，`start_call` 方法签名添加 `segment_id` 参数，并传给 `ApiCall` 构造：

```python
    async def start_call(
        self,
        *,
        project_name: str,
        call_type: CallType,
        model: str,
        prompt: str | None = None,
        resolution: str | None = None,
        duration_seconds: int | None = None,
        aspect_ratio: str | None = None,
        generate_audio: bool = True,
        provider: str = PROVIDER_GEMINI,
        user_id: str = DEFAULT_USER_ID,
        segment_id: str | None = None,
    ) -> int:
        now = utc_now()
        prompt_truncated = prompt[:500] if prompt else None

        row = ApiCall(
            project_name=project_name,
            call_type=call_type,
            model=model,
            prompt=prompt_truncated,
            resolution=resolution,
            duration_seconds=duration_seconds,
            aspect_ratio=aspect_ratio,
            generate_audio=generate_audio,
            status="pending",
            started_at=now,
            provider=provider,
            user_id=user_id,
            segment_id=segment_id,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row.id
```

- [ ] **Step 4: 修改 UsageTracker.start_call**

在 `lib/usage_tracker.py:24-51`，添加 `segment_id` 参数并透传：

```python
    async def start_call(
        self,
        project_name: str,
        call_type: CallType,
        model: str,
        prompt: str | None = None,
        resolution: str | None = None,
        duration_seconds: int | None = None,
        aspect_ratio: str | None = None,
        generate_audio: bool = True,
        provider: str = PROVIDER_GEMINI,
        user_id: str = DEFAULT_USER_ID,
        segment_id: str | None = None,
    ) -> int:
```

在调用 `repo.start_call()` 时传入 `segment_id=segment_id`。

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_usage_tracker.py -v`
Expected: 全部 PASS（含新增的两个测试）

- [ ] **Step 6: Commit**

```bash
git add lib/db/repositories/usage_repo.py lib/usage_tracker.py tests/test_usage_tracker.py
git commit -m "feat: UsageTracker/Repository 支持 segment_id 参数"
```

---

### Task 3: MediaGenerator 传递 segment_id

**Files:**
- Modify: `lib/media_generator.py:204-214` (generate_image_async 中的 start_call)
- Modify: `lib/media_generator.py:374-385` (generate_video_async 中的 start_call)

- [ ] **Step 1: 修改 generate_image_async 中的 start_call 调用**

在 `lib/media_generator.py:204-214`，`start_call()` 调用中添加 `segment_id=resource_id`：

```python
        call_id = await self.usage_tracker.start_call(
            project_name=self.project_name,
            call_type="image",
            model=self._image_backend.model,
            prompt=prompt,
            resolution=image_size,
            aspect_ratio=aspect_ratio,
            provider=self._image_backend.name,
            user_id=self._user_id,
            segment_id=resource_id,
        )
```

- [ ] **Step 2: 修改 generate_video_async 中的 start_call 调用**

在 `lib/media_generator.py:374-385`，同样添加 `segment_id=resource_id`：

```python
        call_id = await self.usage_tracker.start_call(
            project_name=self.project_name,
            call_type="video",
            model=model_name,
            prompt=prompt,
            resolution=resolution,
            duration_seconds=duration_int,
            aspect_ratio=aspect_ratio,
            generate_audio=effective_generate_audio,
            provider=provider_name,
            user_id=self._user_id,
            segment_id=resource_id,
        )
```

- [ ] **Step 3: 运行现有测试确保无回归**

Run: `uv run python -m pytest tests/test_usage_tracker.py -v`
Expected: 全部 PASS

- [ ] **Step 4: Commit**

```bash
git add lib/media_generator.py
git commit -m "feat: MediaGenerator 将 resource_id 作为 segment_id 传入 UsageTracker"
```

---

### Task 4: UsageRepository 新增按 segment 汇总查询

**Files:**
- Modify: `lib/db/repositories/usage_repo.py`
- Test: `tests/test_usage_tracker.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_usage_tracker.py` 添加新测试类：

```python
class TestActualCostsBySegment:
    async def test_aggregates_costs_by_segment_and_type(self, tracker):
        # E1S001: image 两次成功（累计）
        c1 = await tracker.start_call("proj", "image", "gemini-3.1-flash-image-preview", resolution="1K", segment_id="E1S001")
        await tracker.finish_call(c1, status="success", output_path="a.png")
        c2 = await tracker.start_call("proj", "image", "gemini-3.1-flash-image-preview", resolution="1K", segment_id="E1S001")
        await tracker.finish_call(c2, status="success", output_path="b.png")

        # E1S001: video 一次成功
        c3 = await tracker.start_call("proj", "video", "veo-3.1-generate-001", resolution="1080p", duration_seconds=6, segment_id="E1S001")
        await tracker.finish_call(c3, status="success", output_path="v.mp4")

        # E1S002: image 一次成功
        c4 = await tracker.start_call("proj", "image", "gemini-3.1-flash-image-preview", resolution="1K", segment_id="E1S002")
        await tracker.finish_call(c4, status="success", output_path="c.png")

        # 失败的不计入
        c5 = await tracker.start_call("proj", "image", "gemini-3.1-flash-image-preview", resolution="1K", segment_id="E1S001")
        await tracker.finish_call(c5, status="failed", error_message="err")

        result = await tracker.get_actual_costs_by_segment("proj")

        assert "E1S001" in result
        assert result["E1S001"]["image"]["USD"] == pytest.approx(0.067 * 2)
        assert result["E1S001"]["video"]["USD"] == pytest.approx(1.2)
        assert "E1S002" in result
        assert result["E1S002"]["image"]["USD"] == pytest.approx(0.067)

    async def test_project_level_costs(self, tracker):
        # 角色生成（无 segment_id）
        c1 = await tracker.start_call("proj", "image", "gemini-3.1-flash-image-preview", resolution="1K")
        await tracker.finish_call(c1, status="success", output_path="char.png")

        result = await tracker.get_actual_costs_by_segment("proj")
        assert result.get("__project__", {}).get("image", {}).get("USD") == pytest.approx(0.067)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_usage_tracker.py::TestActualCostsBySegment -v`
Expected: FAIL — `get_actual_costs_by_segment` 不存在

- [ ] **Step 3: 在 UsageRepository 新增 get_actual_costs_by_segment**

在 `lib/db/repositories/usage_repo.py` 添加方法：

```python
    async def get_actual_costs_by_segment(
        self,
        project_name: str,
    ) -> dict[str, dict[str, dict[str, float]]]:
        """按 segment_id + call_type + currency 汇总实际费用。

        Returns:
            {segment_id: {call_type: {currency: total_amount}}}
            segment_id 为 None 的记录归入 "__project__" 键。
        """
        stmt = (
            select(
                ApiCall.segment_id,
                ApiCall.call_type,
                ApiCall.currency,
                func.sum(ApiCall.cost_amount).label("total"),
            )
            .where(
                ApiCall.project_name == project_name,
                ApiCall.status == "success",
                ApiCall.cost_amount > 0,
            )
            .group_by(ApiCall.segment_id, ApiCall.call_type, ApiCall.currency)
        )
        rows = (await self.session.execute(stmt)).all()

        result: dict[str, dict[str, dict[str, float]]] = {}
        for seg_id, call_type, currency, total in rows:
            key = seg_id if seg_id is not None else "__project__"
            result.setdefault(key, {}).setdefault(call_type, {})[currency] = round(total, 6)
        return result
```

- [ ] **Step 4: 在 UsageTracker 添加透传方法**

在 `lib/usage_tracker.py` 添加：

```python
    async def get_actual_costs_by_segment(self, project_name: str) -> dict:
        async with self._session_factory() as session:
            repo = UsageRepository(session)
            return await repo.get_actual_costs_by_segment(project_name)
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_usage_tracker.py -v`
Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add lib/db/repositories/usage_repo.py lib/usage_tracker.py tests/test_usage_tracker.py
git commit -m "feat: 新增按 segment 汇总实际费用查询"
```

---

### Task 5: 费用估算服务

**Files:**
- Create: `server/services/cost_estimation.py`
- Test: `tests/test_cost_estimation_service.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_cost_estimation_service.py`：

```python
"""Tests for CostEstimationService."""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.config.resolver import ConfigResolver
from lib.db.base import Base
from lib.usage_tracker import UsageTracker
from server.services.cost_estimation import CostEstimationService


@pytest.fixture
async def db_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


def _make_script(episode: int, segment_ids: list[str], durations: list[int]) -> dict:
    """Helper to create a narration episode script dict."""
    return {
        "episode": episode,
        "title": f"Episode {episode}",
        "content_mode": "narration",
        "duration_seconds": sum(durations),
        "summary": "test",
        "novel": {"title": "t", "chapter": "c"},
        "segments": [
            {
                "segment_id": sid,
                "episode": episode,
                "duration_seconds": dur,
                "segment_break": False,
                "novel_text": "text",
                "characters_in_segment": [],
                "clues_in_segment": [],
                "image_prompt": {"scene": "s", "composition": {"shot_type": "medium", "lighting": "l", "ambiance": "a"}},
                "video_prompt": {"action": "a", "camera_motion": "Static", "ambiance_audio": "aa"},
                "transition_to_next": "cut",
                "generated_assets": {"storyboard_image": None, "video_clip": None, "status": "pending"},
            }
            for sid, dur in zip(segment_ids, durations)
        ],
    }


class TestCostEstimationService:
    async def test_estimate_single_episode(self, db_factory):
        resolver = ConfigResolver(db_factory)
        tracker = UsageTracker(session_factory=db_factory)
        service = CostEstimationService(resolver, tracker)

        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, ["E1S001", "E1S002"], [6, 8])}

        result = await service.compute(project_data, scripts, project_name="test")

        assert len(result["episodes"]) == 1
        ep = result["episodes"][0]
        assert len(ep["segments"]) == 2
        # Each segment should have estimate with image and video
        for seg in ep["segments"]:
            assert "image" in seg["estimate"]
            assert "video" in seg["estimate"]
            # Each cost is {currency: amount}
            for cost in seg["estimate"].values():
                assert isinstance(cost, dict)
                assert all(isinstance(v, (int, float)) for v in cost.values())

    async def test_actual_costs_included(self, db_factory):
        resolver = ConfigResolver(db_factory)
        tracker = UsageTracker(session_factory=db_factory)
        service = CostEstimationService(resolver, tracker)

        # Record actual cost
        cid = await tracker.start_call("proj", "image", "gemini-3.1-flash-image-preview", resolution="1K", segment_id="E1S001")
        await tracker.finish_call(cid, status="success", output_path="a.png")

        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, ["E1S001"], [6])}

        result = await service.compute(project_data, scripts, project_name="proj")

        seg = result["episodes"][0]["segments"][0]
        assert seg["actual"]["image"]["USD"] == pytest.approx(0.067)

    async def test_empty_episodes(self, db_factory):
        resolver = ConfigResolver(db_factory)
        tracker = UsageTracker(session_factory=db_factory)
        service = CostEstimationService(resolver, tracker)

        result = await service.compute({"title": "T", "content_mode": "narration", "episodes": []}, {}, project_name="p")

        assert result["episodes"] == []
        assert result["project_totals"]["estimate"] == {}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_cost_estimation_service.py -v`
Expected: FAIL — `cost_estimation` 模块不存在

- [ ] **Step 3: 实现 CostEstimationService**

创建 `server/services/cost_estimation.py`：

```python
"""费用估算服务 — 计算预估 + 汇总实际费用。"""

from __future__ import annotations

import logging
from typing import Any

from lib.config.resolver import ConfigResolver
from lib.cost_calculator import cost_calculator
from lib.usage_tracker import UsageTracker

logger = logging.getLogger(__name__)

# CostBreakdown = {currency: amount}
CostBreakdown = dict[str, float]


def _add_cost(target: CostBreakdown, amount: float, currency: str) -> None:
    """将费用累加到 CostBreakdown。"""
    if amount <= 0:
        return
    target[currency] = round(target.get(currency, 0) + amount, 6)


def _merge_breakdowns(a: CostBreakdown, b: CostBreakdown) -> CostBreakdown:
    """合并两个 CostBreakdown。"""
    merged = dict(a)
    for cur, amt in b.items():
        merged[cur] = round(merged.get(cur, 0) + amt, 6)
    return merged


class CostEstimationService:
    """费用估算服务。"""

    def __init__(self, resolver: ConfigResolver, tracker: UsageTracker) -> None:
        self._resolver = resolver
        self._tracker = tracker

    async def compute(
        self,
        project_data: dict[str, Any],
        scripts: dict[str, dict[str, Any]],
        *,
        project_name: str,
    ) -> dict[str, Any]:
        """计算整个项目的预估 + 实际费用。

        Args:
            project_data: project.json 内容
            scripts: {script_filename: script_dict} 映射
            project_name: 项目名（用于查询实际费用）
        """
        content_mode = project_data.get("content_mode", "narration")
        episodes_meta = project_data.get("episodes", [])

        # 解析当前模型配置
        try:
            image_provider, image_model = await self._resolver.default_image_backend()
        except (ValueError, Exception):
            image_provider, image_model = "unknown", "unknown"

        try:
            video_provider, video_model = await self._resolver.default_video_backend()
        except (ValueError, Exception):
            video_provider, video_model = "unknown", "unknown"

        generate_audio = await self._resolver.video_generate_audio(project_name)

        # 获取视频分辨率默认值
        from lib.providers import PROVIDER_GEMINI
        from server.services.generation_tasks import _DEFAULT_VIDEO_RESOLUTION

        video_resolution = _DEFAULT_VIDEO_RESOLUTION.get(video_provider, "1080p")

        # 获取实际费用
        actual_by_segment = await self._tracker.get_actual_costs_by_segment(project_name)

        # 计算每集
        episodes_result = []
        proj_est: dict[str, CostBreakdown] = {}
        proj_act: dict[str, CostBreakdown] = {}

        for ep_meta in episodes_meta:
            script_file = ep_meta.get("script_file", "")
            script = scripts.get(script_file)
            if not script:
                continue

            segments_key = "segments" if content_mode == "narration" else "scenes"
            id_key = "segment_id" if content_mode == "narration" else "scene_id"
            raw_segments = script.get(segments_key, [])

            segments_result = []
            ep_est: dict[str, CostBreakdown] = {}
            ep_act: dict[str, CostBreakdown] = {}

            for seg in raw_segments:
                seg_id = seg.get(id_key, "")
                duration = seg.get("duration_seconds", 8)

                # 预估
                est_image: CostBreakdown = {}
                est_video: CostBreakdown = {}

                try:
                    img_amount, img_currency = cost_calculator.calculate_cost(
                        provider=image_provider,
                        call_type="image",
                        model=image_model,
                        resolution="1K",
                    )
                    _add_cost(est_image, img_amount, img_currency)
                except Exception:
                    logger.debug("无法计算 image 预估 for %s", seg_id, exc_info=True)

                try:
                    vid_amount, vid_currency = cost_calculator.calculate_cost(
                        provider=video_provider,
                        call_type="video",
                        model=video_model,
                        resolution=video_resolution,
                        duration_seconds=duration,
                        generate_audio=generate_audio,
                    )
                    _add_cost(est_video, vid_amount, vid_currency)
                except Exception:
                    logger.debug("无法计算 video 预估 for %s", seg_id, exc_info=True)

                # 实际
                seg_actual = actual_by_segment.get(seg_id, {})
                act_image: CostBreakdown = seg_actual.get("image", {})
                act_video: CostBreakdown = seg_actual.get("video", {})

                segments_result.append({
                    "segment_id": seg_id,
                    "duration_seconds": duration,
                    "estimate": {"image": est_image, "video": est_video},
                    "actual": {"image": act_image, "video": act_video},
                })

                # 累加到集合计
                for cost_type in ("image", "video"):
                    ep_est[cost_type] = _merge_breakdowns(
                        ep_est.get(cost_type, {}),
                        {"image": est_image, "video": est_video}[cost_type],
                    )
                    ep_act[cost_type] = _merge_breakdowns(
                        ep_act.get(cost_type, {}),
                        {"image": act_image, "video": act_video}[cost_type],
                    )

            episodes_result.append({
                "episode": ep_meta.get("episode"),
                "title": ep_meta.get("title", ""),
                "segments": segments_result,
                "totals": {"estimate": ep_est, "actual": ep_act},
            })

            # 累加到项目总计
            for cost_type in ("image", "video"):
                proj_est[cost_type] = _merge_breakdowns(
                    proj_est.get(cost_type, {}),
                    ep_est.get(cost_type, {}),
                )
                proj_act[cost_type] = _merge_breakdowns(
                    proj_act.get(cost_type, {}),
                    ep_act.get(cost_type, {}),
                )

        # 项目级实际费用（角色/线索 — segment_id 为 null）
        project_level = actual_by_segment.get("__project__", {})
        for cost_type in ("character", "clue"):
            if cost_type in project_level:
                proj_act[cost_type] = project_level[cost_type]
            # image 类型中包含了角色/线索的图片费用（call_type 都是 image）
        # 角色/线索的 call_type 都是 "image"，但 segment_id 为 null
        # 它们已在 __project__.image 中。重命名到 character/clue 需要额外区分
        # 目前 call_type 不区分 character/clue，都是 "image"
        # 所以 project_level 的 image 就是角色+线索的图片费用
        if "image" in project_level:
            proj_act["character_and_clue"] = project_level["image"]

        return {
            "project_name": project_name,
            "models": {
                "image": {"provider": image_provider, "model": image_model},
                "video": {"provider": video_provider, "model": video_model},
            },
            "episodes": episodes_result,
            "project_totals": {"estimate": proj_est, "actual": proj_act},
        }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_cost_estimation_service.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add server/services/cost_estimation.py tests/test_cost_estimation_service.py
git commit -m "feat: 新增费用估算服务 CostEstimationService"
```

---

### Task 6: API 路由

**Files:**
- Create: `server/routers/cost_estimation.py`
- Modify: `server/app.py:183`
- Test: `tests/test_cost_estimation_router.py`

- [ ] **Step 1: 写路由端点**

创建 `server/routers/cost_estimation.py`：

```python
"""费用估算 API 路由。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from lib.config.resolver import ConfigResolver
from lib.db.engine import get_session_factory
from lib.project_manager import ProjectManager
from lib.usage_tracker import UsageTracker
from lib import PROJECT_ROOT
from server.auth import CurrentUser
from server.services.cost_estimation import CostEstimationService

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/projects/{project_name}/cost-estimate")
async def get_cost_estimate(project_name: str, _user: CurrentUser):
    """获取项目费用估算（预估 + 实际）。"""
    pm = ProjectManager(PROJECT_ROOT / "projects")
    if not pm.project_exists(project_name):
        raise HTTPException(status_code=404, detail=f"项目 '{project_name}' 不存在")

    try:
        project_data = pm.load_project(project_name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目 '{project_name}' 不存在")

    # 加载所有剧本
    scripts: dict[str, dict] = {}
    for ep in project_data.get("episodes", []):
        script_file = ep.get("script_file", "")
        if script_file:
            try:
                scripts[script_file] = pm.load_script(project_name, script_file)
            except FileNotFoundError:
                pass

    factory = get_session_factory()
    resolver = ConfigResolver(factory)
    tracker = UsageTracker(session_factory=factory)
    service = CostEstimationService(resolver, tracker)

    try:
        return await service.compute(project_data, scripts, project_name=project_name)
    except Exception as e:
        logger.exception("费用估算失败")
        raise HTTPException(status_code=500, detail=str(e))
```

- [ ] **Step 2: 注册路由**

在 `server/app.py:183`（`custom_providers` 行后）添加：

```python
app.include_router(cost_estimation.router, prefix="/api/v1", tags=["费用估算"])
```

在文件顶部 import 区域添加：

```python
from server.routers import cost_estimation
```

- [ ] **Step 3: 写路由测试**

创建 `tests/test_cost_estimation_router.py`：

```python
"""Tests for cost estimation API router."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from server.app import app
    with TestClient(app) as c:
        yield c


class TestCostEstimationRouter:
    @patch("server.routers.cost_estimation.ProjectManager")
    @patch("server.routers.cost_estimation.CostEstimationService")
    @patch("server.routers.cost_estimation.get_session_factory")
    def test_project_not_found(self, mock_factory, mock_svc_cls, mock_pm_cls, client):
        mock_pm = MagicMock()
        mock_pm.project_exists.return_value = False
        mock_pm_cls.return_value = mock_pm

        resp = client.get("/api/v1/projects/nonexistent/cost-estimate", headers={"Authorization": "Bearer test"})
        assert resp.status_code in (401, 404)  # 401 if auth blocks first
```

- [ ] **Step 4: 运行测试**

Run: `uv run python -m pytest tests/test_cost_estimation_router.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/routers/cost_estimation.py server/app.py tests/test_cost_estimation_router.py
git commit -m "feat: 新增费用估算 API 路由 /projects/{name}/cost-estimate"
```

---

### Task 7: 前端类型定义和 API 方法

**Files:**
- Create: `frontend/src/types/cost.ts`
- Modify: `frontend/src/types/index.ts`
- Modify: `frontend/src/api.ts`

- [ ] **Step 1: 创建费用类型定义**

创建 `frontend/src/types/cost.ts`：

```typescript
/** 费用明细：货币 → 金额 映射 */
export type CostBreakdown = Record<string, number>;

/** 按类型拆分的费用 */
export interface CostByType {
  image?: CostBreakdown;
  video?: CostBreakdown;
  character_and_clue?: CostBreakdown;
}

/** 单个 segment 的费用 */
export interface SegmentCost {
  segment_id: string;
  duration_seconds: number;
  estimate: { image: CostBreakdown; video: CostBreakdown };
  actual: { image: CostBreakdown; video: CostBreakdown };
}

/** 单集费用 */
export interface EpisodeCost {
  episode: number;
  title: string;
  segments: SegmentCost[];
  totals: { estimate: CostByType; actual: CostByType };
}

/** 模型信息 */
export interface ModelInfo {
  provider: string;
  model: string;
}

/** 费用估算 API 响应 */
export interface CostEstimateResponse {
  project_name: string;
  models: { image: ModelInfo; video: ModelInfo };
  episodes: EpisodeCost[];
  project_totals: { estimate: CostByType; actual: CostByType };
}
```

- [ ] **Step 2: 导出类型**

在 `frontend/src/types/index.ts` 末尾添加：

```typescript
export * from "./cost";
```

- [ ] **Step 3: 新增 API 方法**

在 `frontend/src/api.ts` 中添加 `getCostEstimate` 方法（放在类的静态方法区域中）：

```typescript
  static async getCostEstimate(projectName: string): Promise<CostEstimateResponse> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/cost-estimate`);
  }
```

在文件顶部的 import 中添加 `CostEstimateResponse` 类型导入（如果是 from `@/types` 导入）。

- [ ] **Step 4: 构建确认无 TS 错误**

Run: `cd frontend && pnpm build`
Expected: 构建成功

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types/cost.ts frontend/src/types/index.ts frontend/src/api.ts
git commit -m "feat: 前端费用估算类型定义和 API 方法"
```

---

### Task 8: 费用估算 Store

**Files:**
- Create: `frontend/src/stores/cost-store.ts`

- [ ] **Step 1: 创建 cost store**

创建 `frontend/src/stores/cost-store.ts`：

```typescript
import { create } from "zustand";
import { API } from "@/api";
import type { CostEstimateResponse, SegmentCost, EpisodeCost, CostByType } from "@/types";

interface CostState {
  costData: CostEstimateResponse | null;
  loading: boolean;
  error: string | null;

  fetchCost: (projectName: string) => Promise<void>;
  clear: () => void;

  // Selectors
  getEpisodeCost: (episode: number) => EpisodeCost | undefined;
  getSegmentCost: (segmentId: string) => SegmentCost | undefined;
  getProjectTotals: () => { estimate: CostByType; actual: CostByType } | undefined;
}

export const useCostStore = create<CostState>((set, get) => ({
  costData: null,
  loading: false,
  error: null,

  fetchCost: async (projectName: string) => {
    set({ loading: true, error: null });
    try {
      const data = await API.getCostEstimate(projectName);
      set({ costData: data, loading: false });
    } catch (err) {
      set({ error: (err as Error).message, loading: false });
    }
  },

  clear: () => set({ costData: null, loading: false, error: null }),

  getEpisodeCost: (episode: number) => {
    return get().costData?.episodes.find((e) => e.episode === episode);
  },

  getSegmentCost: (segmentId: string) => {
    const data = get().costData;
    if (!data) return undefined;
    for (const ep of data.episodes) {
      const seg = ep.segments.find((s) => s.segment_id === segmentId);
      if (seg) return seg;
    }
    return undefined;
  },

  getProjectTotals: () => {
    return get().costData
      ? {
          estimate: get().costData!.project_totals.estimate,
          actual: get().costData!.project_totals.actual,
        }
      : undefined;
  },
}));
```

- [ ] **Step 2: 构建确认**

Run: `cd frontend && pnpm build`
Expected: 构建成功

- [ ] **Step 3: Commit**

```bash
git add frontend/src/stores/cost-store.ts
git commit -m "feat: 新增费用估算 Zustand store"
```

---

### Task 9: OverviewCanvas — 项目费用汇总 + 剧集费用列

**Files:**
- Modify: `frontend/src/components/canvas/OverviewCanvas.tsx`

- [ ] **Step 1: 添加 cost store 导入和数据加载**

在 `OverviewCanvas.tsx` 顶部添加导入：

```typescript
import { useCostStore } from "@/stores/cost-store";
import type { CostBreakdown, CostByType } from "@/types";
```

在组件内部添加 cost store 使用和 debounced fetch：

```typescript
  const costData = useCostStore((s) => s.costData);
  const fetchCost = useCostStore((s) => s.fetchCost);

  useEffect(() => {
    if (!projectName) return;
    const timer = setTimeout(() => void fetchCost(projectName), 500);
    return () => clearTimeout(timer);
  }, [projectName, projectData?.episodes, fetchCost]);
```

- [ ] **Step 2: 添加费用格式化 helper**

在组件文件顶部或组件内添加：

```typescript
function formatCost(breakdown: CostBreakdown | undefined): string {
  if (!breakdown || Object.keys(breakdown).length === 0) return "—";
  const SYMBOLS: Record<string, string> = { USD: "$", CNY: "¥" };
  return Object.entries(breakdown)
    .map(([cur, amt]) => `${SYMBOLS[cur] ?? cur}${amt.toFixed(2)}`)
    .join(" + ");
}

function totalBreakdown(byType: CostByType): CostBreakdown {
  const result: CostBreakdown = {};
  for (const costs of Object.values(byType)) {
    if (!costs) continue;
    for (const [cur, amt] of Object.entries(costs)) {
      result[cur] = (result[cur] ?? 0) + amt;
    }
  }
  // Round
  for (const cur of Object.keys(result)) {
    result[cur] = Math.round(result[cur] * 100) / 100;
  }
  return result;
}
```

- [ ] **Step 3: 添加项目总费用汇总栏**

在 `OverviewCanvas.tsx` 的剧集列表 `<div className="space-y-2">` (line 354) 前面，添加项目总费用 section：

```typescript
            {costData && (
              <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
                <p className="mb-3 text-sm font-semibold text-gray-300">项目总费用</p>
                <div className="flex flex-wrap items-start justify-between gap-6">
                  <div>
                    <p className="mb-1 text-[11px] text-gray-600">预估</p>
                    <p className="text-sm text-gray-400">
                      <span className="text-gray-500">分镜 </span>
                      <span className="text-gray-200">{formatCost(costData.project_totals.estimate.image)}</span>
                      <span className="ml-3 text-gray-500">视频 </span>
                      <span className="text-gray-200">{formatCost(costData.project_totals.estimate.video)}</span>
                      <span className="ml-3 text-gray-500">总计 </span>
                      <span className="font-semibold text-amber-400">{formatCost(totalBreakdown(costData.project_totals.estimate))}</span>
                    </p>
                  </div>
                  <div className="h-8 w-px bg-gray-800" />
                  <div>
                    <p className="mb-1 text-[11px] text-gray-600">实际</p>
                    <p className="text-sm text-gray-400">
                      <span className="text-gray-500">分镜 </span>
                      <span className="text-gray-200">{formatCost(costData.project_totals.actual.image)}</span>
                      <span className="ml-3 text-gray-500">视频 </span>
                      <span className="text-gray-200">{formatCost(costData.project_totals.actual.video)}</span>
                      {costData.project_totals.actual.character_and_clue && (
                        <>
                          <span className="ml-3 text-gray-500">角色/线索 </span>
                          <span className="text-gray-200">{formatCost(costData.project_totals.actual.character_and_clue)}</span>
                        </>
                      )}
                      <span className="ml-3 text-gray-500">总计 </span>
                      <span className="font-semibold text-emerald-400">{formatCost(totalBreakdown(costData.project_totals.actual))}</span>
                    </p>
                  </div>
                </div>
              </div>
            )}
```

- [ ] **Step 4: 修改剧集列表行，添加费用列**

修改 `OverviewCanvas.tsx:361-374`，给每个剧集行添加费用信息：

```typescript
                (projectData.episodes ?? []).map((ep) => {
                  const epCost = costData?.episodes.find((e) => e.episode === ep.episode);
                  return (
                    <div
                      key={ep.episode}
                      className="flex items-center gap-3 rounded-lg border border-gray-800 bg-gray-900 px-4 py-2.5"
                    >
                      <span className="font-mono text-xs text-gray-400">
                        E{ep.episode}
                      </span>
                      <span className="text-sm text-gray-200">{ep.title}</span>
                      <span className="text-xs text-gray-500">
                        {ep.scenes_count ?? "?"} 片段 · {ep.status ?? "draft"}
                      </span>
                      {epCost && (
                        <span className="ml-auto flex gap-4 text-xs text-gray-400">
                          <span>
                            <span className="text-gray-500">预估 </span>
                            <span className="text-gray-500">分镜 </span><span className="text-gray-300">{formatCost(epCost.totals.estimate.image)}</span>
                            <span className="ml-2 text-gray-500">视频 </span><span className="text-gray-300">{formatCost(epCost.totals.estimate.video)}</span>
                            <span className="ml-2 text-gray-500">总计 </span><span className="font-medium text-amber-400">{formatCost(totalBreakdown(epCost.totals.estimate))}</span>
                          </span>
                          <span className="text-gray-700">|</span>
                          <span>
                            <span className="text-gray-500">实际 </span>
                            <span className="text-gray-500">分镜 </span><span className="text-gray-300">{formatCost(epCost.totals.actual.image)}</span>
                            <span className="ml-2 text-gray-500">视频 </span><span className="text-gray-300">{formatCost(epCost.totals.actual.video)}</span>
                            <span className="ml-2 text-gray-500">总计 </span><span className="font-medium text-emerald-400">{formatCost(totalBreakdown(epCost.totals.actual))}</span>
                          </span>
                        </span>
                      )}
                    </div>
                  );
                })
```

- [ ] **Step 5: 构建确认**

Run: `cd frontend && pnpm build`
Expected: 构建成功

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/canvas/OverviewCanvas.tsx
git commit -m "feat: OverviewCanvas 展示项目费用汇总和剧集费用列"
```

---

### Task 10: TimelineCanvas — 单集费用栏

**Files:**
- Modify: `frontend/src/components/canvas/timeline/TimelineCanvas.tsx`

- [ ] **Step 1: 添加 cost store 导入**

在 `TimelineCanvas.tsx` 顶部添加：

```typescript
import { useCostStore } from "@/stores/cost-store";
import type { CostBreakdown, CostByType } from "@/types";
```

在组件内部添加（`formatCost` 和 `totalBreakdown` 同 Task 9 或提取到共享 utils — 此处直接内联）：

```typescript
function formatCost(breakdown: CostBreakdown | undefined): string {
  if (!breakdown || Object.keys(breakdown).length === 0) return "—";
  const SYMBOLS: Record<string, string> = { USD: "$", CNY: "¥" };
  return Object.entries(breakdown)
    .map(([cur, amt]) => `${SYMBOLS[cur] ?? cur}${amt.toFixed(2)}`)
    .join(" + ");
}

function totalBreakdown(byType: CostByType): CostBreakdown {
  const result: CostBreakdown = {};
  for (const costs of Object.values(byType)) {
    if (!costs) continue;
    for (const [cur, amt] of Object.entries(costs)) {
      result[cur] = (result[cur] ?? 0) + amt;
    }
  }
  for (const cur of Object.keys(result)) {
    result[cur] = Math.round(result[cur] * 100) / 100;
  }
  return result;
}
```

- [ ] **Step 2: 添加费用栏到 episode header**

在组件函数内部获取 cost 数据：

```typescript
  const episodeCost = useCostStore((s) =>
    episodeScript ? s.getEpisodeCost(episodeScript.episode) : undefined,
  );
```

在 `TimelineCanvas.tsx:133-140` 的 episode header `<div className="mb-4">` 内，`</p>` 后添加费用栏：

```typescript
          {episodeCost && (
            <div className="mt-2 flex items-center gap-4 rounded-lg bg-gray-900 border border-gray-800 px-3 py-2 text-xs">
              <span className="text-gray-600">预估</span>
              <span className="text-gray-500">分镜 <span className="text-gray-300">{formatCost(episodeCost.totals.estimate.image)}</span></span>
              <span className="text-gray-500">视频 <span className="text-gray-300">{formatCost(episodeCost.totals.estimate.video)}</span></span>
              <span className="text-gray-500">总计 <span className="font-medium text-amber-400">{formatCost(totalBreakdown(episodeCost.totals.estimate))}</span></span>
              <span className="text-gray-700">|</span>
              <span className="text-gray-600">实际</span>
              <span className="text-gray-500">分镜 <span className="text-gray-300">{formatCost(episodeCost.totals.actual.image)}</span></span>
              <span className="text-gray-500">视频 <span className="text-gray-300">{formatCost(episodeCost.totals.actual.video)}</span></span>
              <span className="text-gray-500">总计 <span className="font-medium text-emerald-400">{formatCost(totalBreakdown(episodeCost.totals.actual))}</span></span>
            </div>
          )}
```

- [ ] **Step 3: 构建确认**

Run: `cd frontend && pnpm build`
Expected: 构建成功

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/canvas/timeline/TimelineCanvas.tsx
git commit -m "feat: TimelineCanvas 展示单集费用栏"
```

---

### Task 11: SegmentCard — 分镜卡片费用内联

**Files:**
- Modify: `frontend/src/components/canvas/timeline/SegmentCard.tsx`

- [ ] **Step 1: 添加 cost store 导入**

在 `SegmentCard.tsx` 顶部添加：

```typescript
import { useCostStore } from "@/stores/cost-store";
import type { CostBreakdown } from "@/types";
```

在 helpers 区域添加 `formatCost`：

```typescript
function formatCost(breakdown: CostBreakdown | undefined): string {
  if (!breakdown || Object.keys(breakdown).length === 0) return "—";
  const SYMBOLS: Record<string, string> = { USD: "$", CNY: "¥" };
  return Object.entries(breakdown)
    .map(([cur, amt]) => `${SYMBOLS[cur] ?? cur}${amt.toFixed(2)}`)
    .join(" + ");
}
```

- [ ] **Step 2: 在 SegmentCard 组件内获取 cost 数据**

在 `SegmentCard` 函数（line 676）内部添加：

```typescript
  const segCost = useCostStore((s) => s.getSegmentCost(segmentId));
```

- [ ] **Step 3: 在 header 中内联费用显示**

修改 `SegmentCard.tsx:688-699`，在 `<DurationSelector>` 后面添加费用信息：

```typescript
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-gray-800">
          {/* Left: ID badge + duration + cost */}
          <div className="flex items-center gap-2">
            <span className="font-mono text-xs bg-gray-800 rounded px-1.5 py-0.5 text-gray-300">
              {segmentId}
            </span>
            <DurationSelector
              seconds={segment.duration_seconds}
              segmentId={segmentId}
              onUpdatePrompt={onUpdatePrompt}
            />
            {segCost && (
              <>
                <span className="text-gray-700">|</span>
                <span className="text-[11px] text-gray-600">预估</span>
                <span className="text-[11px] text-gray-500">分镜 <span className="text-gray-400">{formatCost(segCost.estimate.image)}</span></span>
                <span className="text-[11px] text-gray-500">视频 <span className="text-gray-400">{formatCost(segCost.estimate.video)}</span></span>
                <span className="text-gray-700">|</span>
                <span className="text-[11px] text-gray-600">实际</span>
                <span className="text-[11px] text-gray-500">分镜 <span className="text-gray-400">{formatCost(segCost.actual.image)}</span></span>
                <span className="text-[11px] text-gray-500">视频 <span className="text-gray-400">{formatCost(segCost.actual.video)}</span></span>
              </>
            )}
          </div>

          {/* Right: AvatarStack + ClueStack */}
```

- [ ] **Step 4: 构建确认**

Run: `cd frontend && pnpm build`
Expected: 构建成功

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/canvas/timeline/SegmentCard.tsx
git commit -m "feat: SegmentCard header 内联费用显示"
```

---

### Task 12: 费用数据实时刷新

**Files:**
- Modify: `frontend/src/components/canvas/OverviewCanvas.tsx`
- Modify: `frontend/src/components/canvas/timeline/TimelineCanvas.tsx`

- [ ] **Step 1: OverviewCanvas 中监听剧本变更**

确保 `OverviewCanvas` 中的 `useEffect` 已经在 Task 9 中实现了 debounced fetch（依赖 `projectData?.episodes`）。此步验证即可。

- [ ] **Step 2: TimelineCanvas / StudioCanvasRouter 中触发 cost 刷新**

在 `StudioCanvasRouter.tsx` 或使用 cost store 的组件中，确保当 `currentScripts` 变化时也触发 cost 刷新。

在 `TimelineCanvas.tsx` 组件内添加：

```typescript
  const fetchCost = useCostStore((s) => s.fetchCost);

  useEffect(() => {
    if (!projectName) return;
    const timer = setTimeout(() => void fetchCost(projectName), 500);
    return () => clearTimeout(timer);
  }, [projectName, episodeScript, fetchCost]);
```

- [ ] **Step 3: 构建确认**

Run: `cd frontend && pnpm build`
Expected: 构建成功

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/canvas/timeline/TimelineCanvas.tsx
git commit -m "feat: 剧本变更时 debounced 刷新费用数据"
```

---

### Task 13: 提取共享 formatCost 工具函数

**Files:**
- Create: `frontend/src/utils/cost-format.ts`
- Modify: `frontend/src/components/canvas/OverviewCanvas.tsx`
- Modify: `frontend/src/components/canvas/timeline/TimelineCanvas.tsx`
- Modify: `frontend/src/components/canvas/timeline/SegmentCard.tsx`

- [ ] **Step 1: 提取 formatCost 和 totalBreakdown 到共享模块**

创建 `frontend/src/utils/cost-format.ts`：

```typescript
import type { CostBreakdown, CostByType } from "@/types";

const SYMBOLS: Record<string, string> = { USD: "$", CNY: "¥" };

export function formatCost(breakdown: CostBreakdown | undefined): string {
  if (!breakdown || Object.keys(breakdown).length === 0) return "—";
  return Object.entries(breakdown)
    .map(([cur, amt]) => `${SYMBOLS[cur] ?? cur}${amt.toFixed(2)}`)
    .join(" + ");
}

export function totalBreakdown(byType: CostByType): CostBreakdown {
  const result: CostBreakdown = {};
  for (const costs of Object.values(byType)) {
    if (!costs) continue;
    for (const [cur, amt] of Object.entries(costs)) {
      result[cur] = (result[cur] ?? 0) + amt;
    }
  }
  for (const cur of Object.keys(result)) {
    result[cur] = Math.round(result[cur] * 100) / 100;
  }
  return result;
}
```

- [ ] **Step 2: 替换三个组件中的内联定义**

将 `OverviewCanvas.tsx`、`TimelineCanvas.tsx`、`SegmentCard.tsx` 中的内联 `formatCost`/`totalBreakdown` 函数替换为从 `@/utils/cost-format` 导入。

- [ ] **Step 3: 构建确认**

Run: `cd frontend && pnpm build`
Expected: 构建成功

- [ ] **Step 4: Commit**

```bash
git add frontend/src/utils/cost-format.ts frontend/src/components/canvas/OverviewCanvas.tsx frontend/src/components/canvas/timeline/TimelineCanvas.tsx frontend/src/components/canvas/timeline/SegmentCard.tsx
git commit -m "refactor: 提取 formatCost/totalBreakdown 到共享工具模块"
```

---

### Task 14: 全量测试和 lint

**Files:** 无新文件

- [ ] **Step 1: 运行全部后端测试**

Run: `uv run python -m pytest -v`
Expected: 全部 PASS

- [ ] **Step 2: 运行 lint + format**

Run: `uv run ruff check . && uv run ruff format .`
Expected: 无错误

- [ ] **Step 3: 运行前端 check**

Run: `cd frontend && pnpm check`
Expected: typecheck + test 全部通过

- [ ] **Step 4: 修复任何问题后 commit**

```bash
git add -A
git commit -m "chore: fix lint and test issues"
```
