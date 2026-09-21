import asyncio
import contextlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from lib.artifacts.artifact_manifest import ArtifactBasis, compose_video_artifact_basis
from lib.artifacts.video_artifact_facts import VideoArtifactCurrencyFacts
from lib.db.models.api_call import ApiCall
from lib.generation.generation_worker import (
    _ORPHAN_RESCAN_LEASE_LOST_MULT,
    DEFAULT_PROVIDER,
    CapacityTable,
    GenerationWorker,
    SlotTable,
    _execute_task,
    _extract_provider,
    _read_int_env,
)
from lib.script.script_editor import ScriptEditError
from tests.fakes import bind_safe_session_factory


async def stored_calls(session) -> list[ApiCall]:
    """按 started_at 倒序读回 api_calls 行。"""
    stmt = select(ApiCall).order_by(ApiCall.started_at.desc(), ApiCall.id.desc())
    return list((await session.execute(stmt)).scalars().all())


def _cap(limits: dict[str, dict[str, int]] | None = None, *, image: int = 5, video: int = 3) -> CapacityTable:
    """构造一张容量表：显式 per-provider 上限 + 懒默认（默认 image=5 / video=3）。"""
    return CapacityTable(_limits=limits or {}, _defaults={"image": image, "video": video})


def _phase_ids(slots: SlotTable, provider: str, media: str) -> tuple[set[str], set[str]]:
    """白盒辅助：返回某 (provider, media) bucket 的 (inflight_ids, pending_ids)。

    等价于旧测对 ``pool.video_inflight`` / ``pool.video_pending`` 的直接读取——
    SlotTable 用 ``_Occupant.pending`` 标志区分两相，对外不暴露按相计数。
    """
    bucket = slots._slots.get((provider, media), {})
    inflight = {tid for tid, occ in bucket.items() if not occ.pending}
    pending = {tid for tid, occ in bucket.items() if occ.pending}
    return inflight, pending


def _worker_reference_checkpoint(task_id: str, *, provider_id: str = "ark") -> str:
    from lib.script.reference_video.execution_checkpoint import NarrationExecutionFacts, ReferenceSubmissionCheckpoint

    visual = ArtifactBasis.build(
        "artifact-visual/video-reference",
        kind_version=1,
        inputs={
            "unit_id": "E1U1",
            "visual_lines": ["Run."],
            "style": "cinematic",
            "canvas": {"aspect_ratio": "9:16"},
            "request_references": [],
        },
    )
    speech = ArtifactBasis.build("artifact-speech/video", kind_version=1, inputs={"mode": "silent"})
    duration = ArtifactBasis.build(
        "artifact-speech/video-duration",
        kind_version=1,
        inputs={"request_duration_seconds": 8},
    )
    return ReferenceSubmissionCheckpoint.create(
        task_id=task_id,
        project_name="demo",
        script_file="scripts/episode_1.json",
        unit_id="E1U1",
        generation_type="r2v",
        provider_id=provider_id,
        provider_model_id="model-v1",
        backend_model_id="model-v1",
        endpoint_guard=None,
        prompt="frozen",
        duration_seconds=8,
        aspect_ratio="9:16",
        resolution="1080p",
        generate_audio=True,
        service_tier="default",
        seed=None,
        visual_basis_digest="a" * 64,
        artifact_currency=VideoArtifactCurrencyFacts(
            episode=1,
            request_duration_seconds=8,
            visual_basis=visual,
            speech_basis=speech,
            duration_basis=duration,
            video_basis=compose_video_artifact_basis(visual=visual, speech=speech, duration=duration),
            voice_style_speakers=(),
            duration_tiers=(8,),
            reference_image_limit=1,
            parent_version=0,
        ),
        narration=NarrationExecutionFacts(
            delivery="post_production",
            tts_status="not_applicable",
            artifact_path="",
            basis_digest=None,
            actual_duration_seconds=None,
        ),
        media=(),
        reference_audio_targets=None,
    ).to_json()


def _worker_storyboard_checkpoint(task_id: str, *, provider_id: str = "ark") -> str:
    from lib.script.reference_video.execution_checkpoint import (
        NarrationExecutionFacts,
        StagedProviderMedia,
        StoryboardSubmissionCheckpoint,
    )

    visual = ArtifactBasis.build(
        "artifact-visual/video-storyboard",
        kind_version=1,
        inputs={
            "resource_id": "E1S01",
            "visual_prompt": {"action": "Run.", "camera_motion": "Static"},
            "canvas": {"aspect_ratio": "9:16"},
            "frames": [{"role": "storyboard", "sha256": "a" * 64}],
        },
    )
    speech = ArtifactBasis.build("artifact-speech/video", kind_version=1, inputs={"mode": "silent"})
    duration = ArtifactBasis.build(
        "artifact-speech/video-duration",
        kind_version=1,
        inputs={"request_duration_seconds": 8},
    )
    return StoryboardSubmissionCheckpoint.create(
        task_id=task_id,
        project_name="demo",
        script_file="scripts/episode_1.json",
        unit_id="E1S01",
        generation_type="i2v",
        provider_id=provider_id,
        provider_model_id="model-v1",
        backend_model_id="model-v1",
        endpoint_guard=None,
        prompt="frozen",
        duration_seconds=8,
        aspect_ratio="9:16",
        resolution="1080p",
        generate_audio=True,
        service_tier="default",
        seed=None,
        visual_basis_digest="a" * 64,
        artifact_currency=VideoArtifactCurrencyFacts(
            episode=1,
            request_duration_seconds=8,
            visual_basis=visual,
            speech_basis=speech,
            duration_basis=duration,
            video_basis=compose_video_artifact_basis(visual=visual, speech=speech, duration=duration),
            voice_style_speakers=(),
            duration_tiers=(8,),
            reference_image_limit=None,
            parent_version=0,
        ),
        narration=NarrationExecutionFacts(
            delivery="post_production",
            tts_status="not_applicable",
            artifact_path="",
            basis_digest=None,
            actual_duration_seconds=None,
        ),
        media=(
            StagedProviderMedia(
                index=0,
                role="start_image",
                logical_type="storyboard",
                logical_name="E1S01",
                kind="storyboard",
                source_locator="storyboards/E1S01.png",
                staged_locator=f".arcreel/tasks/{task_id}/provider_media/000-start_image.png",
                sha256="b" * 64,
                size_bytes=5,
            ),
        ),
        reference_audio_targets=None,
    ).to_json()


def _storyboard_resume_task(task_id: str, *, provider_id: str = "ark", job_id: str = "job-1") -> dict[str, Any]:
    return {
        "task_id": task_id,
        "task_type": "video",
        "media_type": "video",
        "provider_id": provider_id,
        "provider_job_id": job_id,
        "execution_checkpoint_json": _worker_storyboard_checkpoint(task_id, provider_id=provider_id),
        "payload": {},
        "project_name": "demo",
        "resource_id": "E1S01",
        "script_file": "scripts/episode_1.json",
    }


def _storyboard_orphan(task_id: str, *, provider_id: str = "ark", job_id: str = "job-1") -> dict[str, Any]:
    return {**_storyboard_resume_task(task_id, provider_id=provider_id, job_id=job_id), "status": "running"}


class _FakeQueue:
    def __init__(self, *, failed_rows: int = 1):
        self.released = False
        self.succeeded = []
        self.failed = []
        self.interrupted: list[str] = []
        self.claim_after_interruption = asyncio.Event()
        self._lease_calls = 0
        self._failed_rows = failed_rows
        self._orphans: list[dict] = []
        self.persisted_providers: list[tuple[str, str]] = []

    @property
    def session_factory(self):
        """替身不自带库：worker 经队列取到的落库接线就是当前的全局 factory（``worker_db`` 换成内存库）。"""
        import lib.db

        return lib.db.safe_session_factory

    async def persist_execution_provider_id(self, task_id, provider_id):
        self.persisted_providers.append((task_id, provider_id))

    async def acquire_or_renew_worker_lease(self, name, owner_id, ttl_seconds):
        self._lease_calls += 1
        return True

    async def release_worker_lease(self, name, owner_id):
        self.released = True

    async def requeue_running_tasks(self):
        return 0

    async def list_orphan_tasks_on_start(self):
        return self._orphans

    async def claim_next_task(self, media_type, **_kwargs):
        if self.interrupted:
            self.claim_after_interruption.set()

    async def mark_task_succeeded(self, task_id, result):
        self.succeeded.append((task_id, result))
        return 1

    async def mark_task_failed(self, task_id, error):
        self.failed.append((task_id, error))
        return self._failed_rows

    async def mark_task_interrupted(self, task_id):
        self.interrupted.append(task_id)
        return 1


@pytest.fixture
async def worker_db(db_factory, monkeypatch):
    """把 worker 直接触达的全局 session factory 换成内存库。

    ``_requeue_single_task`` 与 ``CapacityTable.from_db`` 绕开注入的 queue 协作者、
    直接经 ``lib.db.safe_session_factory`` 落库；绑到内存库后这两条路径真的执行，
    断言因而能落在行状态上。
    """
    bind_safe_session_factory(monkeypatch, db_factory)
    return db_factory


async def _seed_running_task(factory, task_id: str, **overrides: Any) -> None:
    """种一行 running 状态的 task，供回队路径的真实 guarded UPDATE 命中。"""
    from lib.db.models.task import Task

    now = datetime.now(UTC)
    fields: dict[str, Any] = {
        "project_name": "demo",
        "task_type": "video",
        "media_type": "video",
        "resource_id": "E1S01",
        "status": "running",
        "queued_at": now,
        "started_at": now,
        "updated_at": now,
    }
    fields.update(overrides)
    async with factory() as session:
        session.add(Task(task_id=task_id, **fields))
        await session.commit()


async def _task_status(factory, task_id: str) -> str | None:
    from lib.db.models.task import Task

    async with factory() as session:
        row = await session.get(Task, task_id)
        return None if row is None else row.status


async def _task_error(factory, task_id: str) -> str | None:
    from lib.db.models.task import Task

    async with factory() as session:
        row = await session.get(Task, task_id)
        return None if row is None else row.error_message


def _db_queue(factory):
    """真实的 GenerationQueue，落库到测试库。"""
    from lib.generation.generation_queue import GenerationQueue

    return GenerationQueue(session_factory=factory)


async def _fixed_projection(_task) -> str:
    return "test"


def _active_task_named(slots: SlotTable, name: str) -> asyncio.Future[Any]:
    """按执行体的 task 名从占用台账取在跑的执行体。"""
    matches = [t for t in slots.all_active_tasks() if isinstance(t, asyncio.Task) and t.get_name() == name]
    assert len(matches) == 1, f"占用台账里没有唯一名为 {name} 的执行体"
    return matches[0]


@pytest.fixture
def staged_project(tmp_path, monkeypatch) -> Path:
    """把 worker 的项目定位指向 tmp 项目，让 staging 清理落在真实文件系统上。"""
    project_path = tmp_path / "demo"
    project_path.mkdir()

    class _PM:
        def get_project_path(self, _name: str) -> Path:
            return project_path

    monkeypatch.setattr("lib.project.project_manager.get_project_manager", lambda: _PM())
    return project_path


def _stage_task_dir(project_path: Path, task_id: str) -> Path:
    """建出一个任务的 provider media staging 目录，返回该任务的 staging 根。"""
    staged = project_path / ".arcreel" / "tasks" / task_id / "provider_media"
    staged.mkdir(parents=True)
    (staged / "000-start_image.png").write_bytes(b"x")
    return staged.parent


class TestReadIntEnv:
    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("ARCREEL_INT", raising=False)
        assert _read_int_env("ARCREEL_INT", 3, minimum=1) == 3

    def test_default_when_bad(self, monkeypatch):
        monkeypatch.setenv("ARCREEL_INT", "bad")
        assert _read_int_env("ARCREEL_INT", 3, minimum=1) == 3

    def test_minimum_enforced(self, monkeypatch):
        monkeypatch.setenv("ARCREEL_INT", "0")
        assert _read_int_env("ARCREEL_INT", 3, minimum=2) == 2


def _patch_pm(monkeypatch, project: dict | None):
    """让 worker 的 get_project_manager().load_project 返回给定 project dict。"""
    monkeypatch.setattr(
        "lib.config.resolver.get_project_manager",
        lambda: type("PM", (), {"load_project": lambda self, name: project or {}})(),
    )


@pytest.fixture
async def patch_empty_db(db_factory, monkeypatch):
    """把全局 async_session_factory 换成空内存库，隔离掉真实数据库。

    无 project_name 的 _extract_provider 会用 lib.db.async_session_factory 经 ConfigResolver
    解析全局默认供应商；不隔离时它读真实 dev 库，本机一旦配了其它 ready 供应商，回退断言就被污染。
    """
    monkeypatch.setattr("lib.db.async_session_factory", db_factory)
    return db_factory


class TestExtractProvider:
    """_extract_provider 是解析链的薄投影：按 task_type 派发，取 .provider_id。"""

    @pytest.mark.usefixtures("patch_empty_db")
    async def test_video_payload_identity_is_advisory_only(self):
        """分镜视频认领忽略 enqueue payload 的旧身份；无当前配置时只回退限流默认。"""
        task = {"payload": {"video_provider_i2v": "ark/doubao-seedance-2-0-260128"}, "task_type": "video"}
        assert await _extract_provider(task) == DEFAULT_PROVIDER

    async def test_image_payload_provider(self):
        """payload 携带历史 image_provider → 投影取到。"""
        task = {"payload": {"image_provider": "gemini-vertex"}, "task_type": "storyboard"}
        assert await _extract_provider(task) == "gemini-vertex"

    @pytest.mark.usefixtures("patch_empty_db")
    async def test_default_when_unresolvable(self):
        """无 project、无 payload、全局未配供应商 → 回退 DEFAULT_PROVIDER（仅供限流）。

        必须隔离全局 DB（patch_empty_db）——否则会读真实 dev 库，本机配了其它 ready 供应商时
        auto-resolve 会返回该供应商而非 DEFAULT_PROVIDER，断言被本机环境污染。
        """
        task = {"payload": {}}
        assert await _extract_provider(task) == DEFAULT_PROVIDER

    async def test_project_level_video_backend(self, monkeypatch):
        """项目级 video_backend 优先于全局默认。"""
        _patch_pm(monkeypatch, {"video_backend": "ark/doubao-seedance-1-5-pro-251215"})
        task = {"payload": {}, "project_name": "demo", "task_type": "video"}
        assert await _extract_provider(task) == "ark"

    async def test_project_level_image_t2i(self, monkeypatch):
        """image 投影按代表性 generation_type=t2i 取项目级 image_provider_t2i。"""
        _patch_pm(monkeypatch, {"image_provider_t2i": "gemini-vertex/imagen-3"})
        task = {"payload": {}, "project_name": "demo", "task_type": "storyboard"}
        assert await _extract_provider(task) == "gemini-vertex"

    async def test_reference_video_routes_to_video_lane(self, monkeypatch):
        """reference_video task_type 必须按 video lane 解析 video_backend，而非 image 槽。

        项目同时配置了不同 provider 的 video_backend（ark）与 image_provider_t2i
        （gemini-vertex）。reference_video 属于 video lane，认领期 provider 投影须取 ark；
        若误判为 image lane（按 task_type != "video" 去读 image 槽），会取到纯图片
        供应商，导致 worker 在 video 通道以 video_max==0 直接把任务标记
        「供应商不支持 video 生成」。"""
        _patch_pm(
            monkeypatch,
            {
                "video_backend": "ark/doubao-seedance-1-5-pro-251215",
                "image_provider_t2i": "gemini-vertex/imagen-3",
            },
        )
        task = {"payload": {}, "project_name": "demo", "task_type": "reference_video"}
        assert await _extract_provider(task) == "ark"

    async def test_reference_video_prefers_r2v_bucket_provider(self, monkeypatch):
        """视频单元级状态读不到时回退当前 r2v 配置，不采用 payload 中的 enqueue-time pin。"""
        _patch_pm(
            monkeypatch,
            {
                "video_backend": "ark/doubao-seedance-1-5-pro-251215",
                "video_provider_r2v": "minimax/S2V-01",
            },
        )
        task = {
            "payload": {"video_provider_r2v": "ark/doubao-seedance-1-5-pro-251215"},
            "project_name": "demo",
            "task_type": "reference_video",
        }
        assert await _extract_provider(task) == "minimax"

    @pytest.mark.parametrize(
        ("text", "expected_provider"),
        [("@[A] 走进房间", "minimax"), ("空镜头", "ark")],
    )
    async def test_reference_video_routes_by_hydrated_reference_bucket(
        self, tmp_path, monkeypatch, text, expected_provider
    ):
        """reference_video 投影按 unit 当前实际可用参考图分桶：有图 → r2v 桶 provider，
        无参考图退化镜头 → i2v 桶 provider，与执行层降级定桶同口径。"""
        project = {
            "video_provider_i2v": "ark/doubao-seedance-1-5-pro-251215",
            "video_provider_r2v": "minimax/S2V-01",
            "video_generate_audio": True,
            "characters": {"A": {"character_sheet": "characters/A.png"}},
        }
        (tmp_path / "characters").mkdir()
        (tmp_path / "characters" / "A.png").write_bytes(b"image")
        script = {"video_units": [{"unit_id": "E1U1", "text": text}]}
        pm_cls = type(
            "PM",
            (),
            {
                "load_project": lambda self, name: project,
                "load_script": lambda self, name, filename: script,
                "get_project_path": lambda self, name: tmp_path,
            },
        )
        monkeypatch.setattr("lib.config.resolver.get_project_manager", pm_cls)
        task = {
            "payload": {"script_file": "ep1.json"},
            "project_name": "demo",
            "task_type": "reference_video",
            "resource_id": "E1U1",
        }
        assert await _extract_provider(task) == expected_provider

    async def test_reference_video_claim_uses_frozen_task_script_locator(self, tmp_path, monkeypatch):
        project = {
            "video_provider_i2v": "ark/doubao-seedance-1-5-pro-251215",
            "video_provider_r2v": "minimax/S2V-01",
            "video_generate_audio": True,
            "characters": {"A": {"character_sheet": "characters/A.png"}},
        }
        (tmp_path / "characters").mkdir()
        (tmp_path / "characters" / "A.png").write_bytes(b"image")
        scripts = {
            "stale.json": {"video_units": [{"unit_id": "E1U1", "text": "空镜头"}]},
            "frozen.json": {"video_units": [{"unit_id": "E1U1", "text": "@[A] 走进房间"}]},
        }
        pm_cls = type(
            "PM",
            (),
            {
                "load_project": lambda self, name: project,
                "load_script": lambda self, name, filename: scripts[filename],
                "get_project_path": lambda self, name: tmp_path,
            },
        )
        monkeypatch.setattr("lib.config.resolver.get_project_manager", pm_cls)
        task = {
            "payload": {"script_file": "stale.json"},
            "script_file": "frozen.json",
            "project_name": "demo",
            "task_type": "reference_video",
            "resource_id": "E1U1",
        }

        assert await _extract_provider(task) == "minimax"

    async def test_reference_video_script_read_failure_falls_back_to_r2v(self, monkeypatch):
        """剧本读取失败时投影回退 r2v 代表桶，不冒泡阻断认领。"""

        def _load_script(self, name, filename):
            raise ScriptEditError("broken")

        project = {
            "video_provider_i2v": "ark/doubao-seedance-1-5-pro-251215",
            "video_provider_r2v": "minimax/S2V-01",
        }
        pm_cls = type("PM", (), {"load_project": lambda self, name: project, "load_script": _load_script})
        monkeypatch.setattr("lib.config.resolver.get_project_manager", pm_cls)
        task = {
            "payload": {"script_file": "ep1.json"},
            "project_name": "demo",
            "task_type": "reference_video",
            "resource_id": "E1U1",
        }
        assert await _extract_provider(task) == "minimax"

    async def test_queued_reference_video_reprojects_provider_after_project_edit(
        self, tmp_path, monkeypatch, patch_empty_db
    ):
        """队列行只保存 advisory provider；领取/执行前按项目最新 provider/model 重新投影。"""

        from lib.generation.generation_queue import GenerationQueue, reference_projection_for_queued_task

        holder = {"model": "ark/doubao-seedance-1-5-pro-251215"}
        script = {"video_units": [{"unit_id": "E1U1", "text": "空镜", "duration_seconds": 5}]}

        class _PM:
            def load_project(self, _name):
                return {
                    "generation_mode": "reference_video",
                    "video_provider_i2v": holder["model"],
                    "video_generate_audio": True,
                }

            def load_script(self, _name, _filename):
                return script

            def get_project_path(self, _name):
                return tmp_path

        monkeypatch.setattr("lib.config.resolver.get_project_manager", _PM)
        queue = GenerationQueue(session_factory=patch_empty_db)
        enqueued = await queue.enqueue_task(
            project_name="demo",
            task_type="reference_video",
            media_type="video",
            resource_id="E1U1",
            payload={"script_file": "ep1.json"},
            script_file="ep1.json",
        )
        task = await queue.get_task(enqueued["task_id"])
        assert task is not None
        assert task["provider_id"] == "ark"
        assert "video_provider_i2v" not in task["payload"]
        assert "video_provider_r2v" not in task["payload"]

        first = await reference_projection_for_queued_task(
            project=_PM().load_project(task["project_name"]),
            project_name=task["project_name"],
            payload=task["payload"],
            resource_id=task["resource_id"],
        )
        assert first is not None
        assert (first.provider_id, first.model_id) == ("ark", "doubao-seedance-1-5-pro-251215")
        assert await _extract_provider(task) == "ark"

        holder["model"] = "ark/doubao-seedance-2-0-260128"
        second = await reference_projection_for_queued_task(
            project=_PM().load_project(task["project_name"]),
            project_name=task["project_name"],
            payload=task["payload"],
            resource_id=task["resource_id"],
        )
        assert second is not None
        assert (second.provider_id, second.model_id) == ("ark", "doubao-seedance-2-0-260128")

        holder["model"] = "grok/grok-imagine-video"
        third = await reference_projection_for_queued_task(
            project=_PM().load_project(task["project_name"]),
            project_name=task["project_name"],
            payload=task["payload"],
            resource_id=task["resource_id"],
        )
        assert third is not None
        assert (third.provider_id, third.model_id) == ("grok", "grok-imagine-video")
        assert await _extract_provider(task) == "grok"

    async def test_current_project_provider_takes_precedence_over_stale_payload(self, monkeypatch):
        """分镜视频在认领时重投影当前项目配置，不复用 enqueue payload 的旧 provider。"""
        _patch_pm(monkeypatch, {"video_backend": "grok/grok-imagine-video"})
        task = {
            "payload": {"video_provider_i2v": "ark/doubao-seedance-2-0-260128"},
            "project_name": "demo",
            "task_type": "video",
        }
        assert await _extract_provider(task) == "grok"

    async def test_deleted_project_load_failure_falls_back_not_raises(self, monkeypatch):
        """指向已删除/不可读项目的残留任务：load_project 抛错也须回退 DEFAULT_PROVIDER，
        绝不冒泡阻断认领循环（否则一个坏任务会拖垮整个 worker）。"""

        def _raising_pm():
            def _load(self, name):
                raise FileNotFoundError(name)

            return type("PM", (), {"load_project": _load})()

        monkeypatch.setattr("lib.config.resolver.get_project_manager", _raising_pm)
        task = {"payload": {}, "project_name": "deleted-proj", "task_type": "video"}
        assert await _extract_provider(task) == DEFAULT_PROVIDER


class TestExtractProviderAlignsWithExecution:
    """M5 投影对齐：worker 取到的 provider_id 与执行层解析在同一 project/payload 下一致。"""

    async def test_image_alignment(self, monkeypatch):
        from lib.config.resolver import ConfigResolver
        from lib.db import async_session_factory

        project = {"image_provider_t2i": "openai/gen-1", "image_provider_i2i": "openai/edit-1"}
        _patch_pm(monkeypatch, project)
        task = {"payload": {}, "project_name": "demo", "task_type": "storyboard"}

        worker_provider = await _extract_provider(task)
        resolved = await ConfigResolver(async_session_factory).resolve_image_backend(project, {}, generation_type="t2i")
        assert worker_provider == resolved.provider_id == "openai"

    async def test_video_alignment(self, monkeypatch):
        from lib.config.resolver import ConfigResolver
        from lib.db import async_session_factory

        project = {"video_backend": "ark/doubao-seedance-1-5-pro-251215"}
        _patch_pm(monkeypatch, project)
        task = {"payload": {}, "project_name": "demo", "task_type": "video"}

        worker_provider = await _extract_provider(task)
        resolved = await ConfigResolver(async_session_factory).resolve_video_backend(project, {}, generation_type="i2v")
        assert worker_provider == resolved.provider_id == "ark"


class TestExecuteTaskPollTimeout:
    """派发时读一次全局轮询超时并写进任务字典下传；只有视频两条 lane 需要它。"""

    @pytest.fixture
    def patch_settings_db(self, db_factory, monkeypatch):
        @contextlib.asynccontextmanager
        async def _safe_session_factory():
            async with db_factory() as session:
                yield session

        bind_safe_session_factory(monkeypatch, _safe_session_factory)
        return db_factory

    @staticmethod
    def _capture_dispatch(monkeypatch) -> dict[str, Any]:
        captured: dict[str, Any] = {}

        async def _fake_execute(task: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
            captured.update(task)
            return {"ok": True}

        monkeypatch.setattr("server.services.tasks.generation_tasks.execute_generation_task", _fake_execute)
        return captured

    @pytest.mark.parametrize("task_type", ["video", "reference_video"])
    async def test_video_lanes_carry_configured_timeout(self, patch_settings_db, monkeypatch, task_type):
        from lib.config.service import ConfigService

        async with patch_settings_db() as session:
            await ConfigService(session).set_video_poll_timeout_seconds(7200)
            await session.commit()

        captured = self._capture_dispatch(monkeypatch)
        await _execute_task({"task_type": task_type})

        assert captured["video_poll_timeout_seconds"] == 7200

    @pytest.mark.usefixtures("patch_settings_db")
    async def test_defaults_when_setting_absent(self, monkeypatch):
        captured = self._capture_dispatch(monkeypatch)
        await _execute_task({"task_type": "video"})

        assert captured["video_poll_timeout_seconds"] == 3600

    @pytest.mark.usefixtures("patch_settings_db")
    async def test_non_video_lane_is_not_stamped(self, monkeypatch):
        captured = self._capture_dispatch(monkeypatch)
        await _execute_task({"task_type": "storyboard"})

        assert "video_poll_timeout_seconds" not in captured


class TestGenerationWorker:
    @pytest.mark.asyncio
    async def test_process_task_success_and_failure(self, monkeypatch):
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)

        async def _fake_execute(task):
            return {"ok": task["task_id"]}

        monkeypatch.setattr(
            "server.services.tasks.generation_tasks.execute_generation_task",
            _fake_execute,
        )
        await worker._process_task({"task_id": "t1"})
        assert queue.succeeded == [("t1", {"ok": "t1"})]

        async def _raise(_task):
            raise RuntimeError("boom")

        monkeypatch.setattr("server.services.tasks.generation_tasks.execute_generation_task", _raise)
        await worker._process_task({"task_id": "t2"})
        assert queue.failed
        assert queue.failed[0][0] == "t2"

    @pytest.mark.asyncio
    async def test_reused_video_result_reaches_the_normal_succeeded_terminal_state(self, monkeypatch):
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)
        reused = {
            "version": 2,
            "file_path": "videos/scene_E1S01.mp4",
            "resource_type": "videos",
            "resource_id": "E1S01",
            "reused_existing": True,
        }

        async def _fake_execute(_task, *, claimed_provider_id):
            assert claimed_provider_id
            return reused

        monkeypatch.setattr("server.services.tasks.generation_tasks.execute_generation_task", _fake_execute)

        await worker._process_task({"task_id": "task-reuse", "task_type": "video"})

        assert queue.succeeded == [("task-reuse", reused)]
        assert queue.failed == []

    @pytest.mark.asyncio
    async def test_process_reference_task_requeues_when_execution_provider_changes(self, monkeypatch, worker_db):
        """执行入口解析到别的 provider 时不占旧槽提交，而是刷新投影并回队重认领。"""

        from lib.generation.generation_queue import DispatchProviderChanged

        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)

        async def _changed(_task, *, claimed_provider_id):
            assert claimed_provider_id == "ark"
            raise DispatchProviderChanged(claimed_provider_id="ark", actual_provider_id="minimax")

        monkeypatch.setattr("server.services.tasks.generation_tasks.execute_generation_task", _changed)
        await _seed_running_task(worker_db, "ref-provider-changed", task_type="reference_video", resource_id="E1U1")

        await worker._process_task(
            {"task_id": "ref-provider-changed", "task_type": "reference_video"},
            claimed_provider_id="ark",
        )

        assert queue.persisted_providers == [("ref-provider-changed", "minimax")]
        assert await _task_status(worker_db, "ref-provider-changed") == "queued"
        assert queue.succeeded == []
        assert queue.failed == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failed_rows", [1, 0])
    async def test_process_reference_task_closes_terminal_state_when_provider_requeue_fails(
        self, monkeypatch, worker_db, failed_rows: int
    ):
        from lib.generation.generation_queue import DispatchProviderChanged

        queue = _FakeQueue(failed_rows=failed_rows)
        worker = GenerationWorker(queue=queue)

        async def _changed(_task, *, claimed_provider_id):
            raise DispatchProviderChanged(claimed_provider_id=claimed_provider_id, actual_provider_id="minimax")

        monkeypatch.setattr("server.services.tasks.generation_tasks.execute_generation_task", _changed)
        # 库里没有这行 running 记录，回队的 guarded UPDATE 命中 0 行——回队失败正是本用例的前提

        await worker._process_task(
            {"task_id": "ref-provider-changed", "task_type": "reference_video"},
            claimed_provider_id="ark",
        )

        assert queue.succeeded == []
        assert queue.failed == [
            (
                "ref-provider-changed",
                '[dispatch_provider_requeue_failed] {"actual_provider_id": "minimax", "claimed_provider_id": "ark"}',
            )
        ]
        # 终态写入命中 0 行时任务保持原状，不被改判为打断
        assert queue.interrupted == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("affected", "expected"), [(1, True), (0, False)])
    async def test_requeue_single_task_reports_guarded_update_result(self, monkeypatch, affected: int, expected: bool):
        from contextlib import asynccontextmanager

        class _Result:
            rowcount = affected

        class _Session:
            async def execute(self, _statement):
                return _Result()

            async def commit(self):
                return None

        @asynccontextmanager
        async def _session_factory():
            yield _Session()

        bind_safe_session_factory(monkeypatch, _session_factory)

        assert await GenerationWorker(queue=_FakeQueue())._requeue_single_task("candidate") is expected

    @pytest.mark.asyncio
    async def test_requeue_single_task_reports_database_failure(self, monkeypatch):
        def _session_factory() -> contextlib.AbstractAsyncContextManager[object]:
            raise RuntimeError("database unavailable")

        bind_safe_session_factory(monkeypatch, _session_factory)

        assert await GenerationWorker(queue=_FakeQueue())._requeue_single_task("running") is False

    @pytest.mark.asyncio
    async def test_process_task_script_edit_error_encodes_key_and_params(self, monkeypatch):
        """apply_unit_video_assets 经异步任务队列（非 upload_unit_video 同步路由）抛出时，
        error_message 落成可翻译的 [key] {params} 结构而非 str(exc) 的固定中文，任务状态
        接口按 Accept-Language 渲染时才不会给 en/vi 用户漏出中文；params 一并落库才能在
        渲染侧还原成完整的翻译占位符替换（如 resolve_items 的 kind/type_name）。"""
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)

        async def _raise_script_edit_error(_task):
            raise ScriptEditError(
                "segments 必须是列表，当前为 dict",
                key="script_edit_items_not_list",
                kind="segments",
                type_name="dict",
            )

        monkeypatch.setattr(
            "server.services.tasks.generation_tasks.execute_generation_task",
            _raise_script_edit_error,
        )
        await worker._process_task({"task_id": "t_script_edit"})
        assert queue.failed
        assert queue.failed[0] == (
            "t_script_edit",
            '[script_edit_items_not_list] {"kind": "segments", "type_name": "dict"}',
        )

    @pytest.mark.asyncio
    async def test_process_task_script_edit_error_unregistered_key_falls_back(self, monkeypatch):
        """两份登记（errors.py 的翻译 key、task_failure.FAILURE_CODE_KEYS 的 worker 编码表）
        靠约定同步，非运行时校验——新 raise 点忘了同步登记时，encode_failure 对未登记 key
        抛 KeyError 不能打断 mark_task_failed，否则任务会卡在 running 而非落终态。"""
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)

        async def _raise_unregistered(_task):
            raise ScriptEditError("尚未登记的错误", key="script_edit_not_yet_registered")

        monkeypatch.setattr(
            "server.services.tasks.generation_tasks.execute_generation_task",
            _raise_unregistered,
        )
        await worker._process_task({"task_id": "t_unregistered"})
        assert queue.failed
        assert queue.failed[0] == ("t_unregistered", "[script_edit_error]")

    @pytest.mark.asyncio
    async def test_process_task_script_edit_error_circular_params_falls_back(self, monkeypatch):
        """params 含循环引用时 json.dumps 抛 ValueError（而非 TypeError）——同一条降级路径
        也要接住这个分支，否则序列化失败照样打断 mark_task_failed，任务卡在 running。"""
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)

        async def _raise_circular_params(_task):
            circular: dict[str, Any] = {}
            circular["self"] = circular
            raise ScriptEditError(
                "generated_assets 必须是 dict",
                key="script_edit_generated_assets_invalid",
                circular=circular,
            )

        monkeypatch.setattr(
            "server.services.tasks.generation_tasks.execute_generation_task",
            _raise_circular_params,
        )
        await worker._process_task({"task_id": "t_circular"})
        assert queue.failed
        assert queue.failed[0] == ("t_circular", "[script_edit_error]")

    @pytest.mark.asyncio
    async def test_process_task_executor_cancelled_error_lands_cancelled(self, worker_db):
        """执行器抛 CancelledError（进程级打断）：任务不停在 running，落 cancelled 后重新抛出。"""
        await _seed_running_task(worker_db, "tc", task_type="storyboard", media_type="image")

        async def _cancelled(_task, *, claimed_provider_id):
            raise asyncio.CancelledError

        worker = GenerationWorker(
            queue=_db_queue(worker_db), provider_projection=_fixed_projection, executor=_cancelled
        )
        with pytest.raises(asyncio.CancelledError):
            await worker._process_task({"task_id": "tc", "media_type": "image"})

        assert await _task_status(worker_db, "tc") == "cancelled"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("outcome", ["succeeded", "failed"])
    async def test_process_task_zero_row_terminal_write_leaves_task_untouched(self, worker_db, outcome: str):
        """终态写入命中 0 行（任务已不在 running）：该写入不生效，任务不被改判为 cancelled。"""
        # 行处于 queued：终态写入的守卫只放行 running，而打断兜底会把 queued 翻成 cancelled
        await _seed_running_task(worker_db, "t0rows", status="queued", started_at=None)

        async def _execute(_task, *, claimed_provider_id):
            if outcome == "failed":
                raise RuntimeError("boom")
            return {"result": "ok"}

        worker = GenerationWorker(queue=_db_queue(worker_db), provider_projection=_fixed_projection, executor=_execute)
        await worker._process_task({"task_id": "t0rows", "media_type": "video"})

        assert await _task_status(worker_db, "t0rows") == "queued"

    @pytest.mark.asyncio
    async def test_stop_waits_for_running_task_to_finish_as_succeeded(self, concurrent_session_factory, monkeypatch):
        """服务关停不取消在跑任务：stop 等它跑完，任务落 succeeded；关停期间一直是 running。"""
        bind_safe_session_factory(monkeypatch, concurrent_session_factory)
        worker_db = concurrent_session_factory
        await _seed_running_task(worker_db, "shutdown-run", task_type="storyboard", media_type="image")
        started = asyncio.Event()
        release = asyncio.Event()

        async def _execute(_task, *, claimed_provider_id):
            started.set()
            await release.wait()
            return {"ok": True}

        worker = GenerationWorker(queue=_db_queue(worker_db), provider_projection=_fixed_projection, executor=_execute)
        worker.heartbeat_interval = 0.01
        worker.poll_interval = 0.01
        running = asyncio.create_task(
            worker._process_task({"task_id": "shutdown-run", "media_type": "image"}),
            name="generation-image-shutdown-run",
        )
        worker._slots.register("test", "image", "shutdown-run", running)

        await worker.start()
        await started.wait()
        assert await _task_status(worker_db, "shutdown-run") == "running"
        stop_started = asyncio.Event()

        async def _stop_worker() -> None:
            stop_started.set()
            await worker.stop()

        stopping = asyncio.create_task(_stop_worker())
        await stop_started.wait()

        assert not stopping.done(), "stop 必须等在跑任务跑完"
        assert not running.done(), "stop 不得打断在跑任务"

        release.set()
        await asyncio.wait_for(running, timeout=2.0)
        await asyncio.wait_for(stopping, timeout=2.0)

        assert not running.cancelled()
        assert await _task_status(worker_db, "shutdown-run") == "succeeded"
        assert worker._main_task is None

    @pytest.mark.asyncio
    async def test_drain_finished_tasks_absorbs_cancelled_error(self):
        """被打断的执行体被 drain：不抛、从台账移除，并由 drain 端兜底落终态。"""
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)

        async def _long():
            await asyncio.sleep(10)

        t = asyncio.create_task(_long())
        t.cancel()
        await asyncio.gather(t, return_exceptions=True)  # 驱动到 done(cancelled)，吞掉取消结果
        assert t.cancelled()
        worker._slots.register("test", "video", "tid", t)

        # 同步判定 .cancelled()：不 await，不抛 CancelledError，task 已被 drain 移除。
        await worker._drain_finished_tasks()
        assert worker._slots.occupied("test", "video") == 0
        # 子任务来不及自落终态时，drain 端兜底落终态。
        assert queue.interrupted == ["tid"]

    @pytest.mark.asyncio
    async def test_drain_finished_tasks_drains_success_and_failure(self):
        """非打断路径：成功 task 走 .result() 无异常，失败 task 走 except 分支，均不触发兜底。"""
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)

        async def _ok():
            return "done"

        async def _boom():
            raise RuntimeError("boom")

        ok_t = asyncio.create_task(_ok())
        boom_t = asyncio.create_task(_boom())
        await asyncio.gather(ok_t, boom_t, return_exceptions=True)
        worker._slots.register("test", "image", "ok", ok_t)
        worker._slots.register("test", "video", "boom", boom_t)

        await worker._drain_finished_tasks()  # 不抛
        assert worker._slots.occupied("test", "image") == 0
        assert worker._slots.occupied("test", "video") == 0
        assert queue.interrupted == []

    @pytest.mark.asyncio
    async def test_drain_fallback_terminal_write_failure_does_not_propagate(self):
        """drain 端兜底落终态自身抛错时只 warning，不冒泡（不挂掉主循环）。"""

        class _RaisingQueue(_FakeQueue):
            async def mark_task_interrupted(self, task_id):
                raise RuntimeError("db down")

        queue = _RaisingQueue()
        worker = GenerationWorker(queue=queue)

        async def _long():
            await asyncio.sleep(10)

        t = asyncio.create_task(_long())
        t.cancel()
        await asyncio.gather(t, return_exceptions=True)  # 驱动到 done(cancelled)，吞掉取消结果
        assert t.cancelled()
        worker._slots.register("test", "video", "tid", t)

        # 兜底抛错被 except 吞掉，drain 不抛、task 仍被移除
        await worker._drain_finished_tasks()
        assert worker._slots.occupied("test", "video") == 0

    @pytest.mark.asyncio
    async def test_drain_lands_cancelled_when_interruption_hits_before_process_task_try(self, monkeypatch):
        """打断落在 _process_task 进 try 之前（provider 投影 await）：drain 端兜底落终态。"""
        queue = _FakeQueue()
        in_extract = asyncio.Event()

        async def _blocking_projection(_task):
            in_extract.set()
            await asyncio.sleep(10)  # 停在入口解析，模拟打断落在 _process_task 的 try 之前
            return "test"

        worker = GenerationWorker(queue=queue, provider_projection=_blocking_projection)
        worker.heartbeat_interval = 0.01
        worker.poll_interval = 0.01

        async def _execute(_task):
            raise AssertionError("execute 不应被调用：打断在 provider 投影阶段就到")

        monkeypatch.setattr("server.services.tasks.generation_tasks.execute_generation_task", _execute)

        t = asyncio.create_task(
            worker._process_task({"task_id": "tid", "media_type": "video"}),
            name="generation-video-tid",
        )
        worker._slots.register("test", "video", "tid", t)

        await worker.start()
        await in_extract.wait()  # 确保停在 provider 投影（try 之前）
        t.cancel()
        await asyncio.wait_for(queue.claim_after_interruption.wait(), timeout=2.0)

        # _process_task 没机会落终态（打断在 try 之前）→ drain 端兜底
        assert queue.interrupted == ["tid"]
        # 主循环仍存活
        assert worker._main_task is not None
        assert not worker._main_task.done()

        await asyncio.wait_for(worker.stop(), timeout=2.0)

    @pytest.mark.asyncio
    async def test_run_loop_survives_inflight_task_interruption(self, monkeypatch):
        """单个执行体被打断：任务落终态，但 worker 主循环不退出，显式 stop 才退出。"""
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)
        worker.heartbeat_interval = 0.01
        worker.poll_interval = 0.01

        started = asyncio.Event()

        async def _block(_task):
            started.set()
            await asyncio.sleep(10)  # 模拟长时间运行的生成任务

        monkeypatch.setattr("server.services.tasks.generation_tasks.execute_generation_task", _block)

        t = asyncio.create_task(
            worker._process_task({"task_id": "tid", "media_type": "video"}),
            name="generation-video-tid",
        )
        worker._slots.register("test", "video", "tid", t)

        await worker.start()
        await started.wait()  # 确保 _process_task 已进入 execute（_extract_provider 已完成）

        t.cancel()
        await asyncio.wait_for(queue.claim_after_interruption.wait(), timeout=2.0)

        # _process_task 自落终态后 drain 端再兜底一次，守卫保证第二次无副作用
        assert set(queue.interrupted) == {"tid"}
        # 主循环吸收 CancelledError 后仍存活
        assert worker._main_task is not None
        assert not worker._main_task.done()

        await asyncio.wait_for(worker.stop(), timeout=2.0)
        assert worker._main_task is None
        assert queue.released

    @pytest.mark.asyncio
    async def test_run_loop_survives_consecutive_interruptions_and_keeps_claiming(self, monkeypatch):
        """连续打断多个 inflight 执行体后，主循环仍存活并能继续 claim 新任务。"""

        class _GatedQueue(_FakeQueue):
            def __init__(self):
                super().__init__()
                self.allow_new = False
                self.new_dispatched = False

            async def claim_next_task(self, media_type, **_kwargs):  # type: ignore[override]
                if self.allow_new and not self.new_dispatched and media_type == "image":
                    self.new_dispatched = True
                    return {
                        "task_id": "fresh-img",
                        "task_type": "gen_image",
                        "media_type": "image",
                        "payload": {"image_provider": "gemini-aistudio"},
                    }
                return None

        queue = _GatedQueue()
        worker = GenerationWorker(queue=queue, capacity=_cap({"gemini-aistudio": {"image": 5, "video": 3}}))
        worker.heartbeat_interval = 0.01
        worker.poll_interval = 0.01

        release = asyncio.Event()
        fresh_started = asyncio.Event()
        entered: set[str] = set()
        all_entered = asyncio.Event()

        async def _block(task):
            entered.add(task["task_id"])
            if task["task_id"] == "fresh-img":
                fresh_started.set()
            if set(vid_ids) <= entered:
                all_entered.set()
            await release.wait()
            return {"ok": True}

        monkeypatch.setattr("server.services.tasks.generation_tasks.execute_generation_task", _block)

        vid_ids = ["vid-0", "vid-1", "vid-2"]
        running: list[asyncio.Task] = []
        for tid in vid_ids:
            t = asyncio.create_task(
                worker._process_task({"task_id": tid, "media_type": "video"}),
                name=f"generation-video-{tid}",
            )
            worker._slots.register("gemini-aistudio", "video", tid, t)
            running.append(t)

        await worker.start()
        await all_entered.wait()  # 等三个任务都进入 execute

        for t in running:
            t.cancel()
        await asyncio.gather(*running, return_exceptions=True)

        # 主循环存活 + 三个任务都落终态
        assert worker._main_task is not None
        assert not worker._main_task.done()
        assert set(vid_ids) <= set(queue.interrupted)

        # 打断后仍能 claim 并 dispatch 新任务
        queue.allow_new = True
        await asyncio.wait_for(fresh_started.wait(), timeout=2.0)
        assert queue.new_dispatched is True
        assert "fresh-img" in worker._slots.active_task_ids()

        # 收尾：放行 fresh-img 后正常停机
        release.set()
        await asyncio.wait_for(worker.stop(), timeout=2.0)
        assert queue.released

    @pytest.mark.asyncio
    async def test_video_orphan_cleanup_removes_provider_media_and_task_output(self, tmp_path, monkeypatch):
        from lib.generation.media_generator import task_video_staging_path

        project_path = tmp_path / "demo"
        output_path = project_path / "videos" / "scene_E1S01.mp4"
        output_path.parent.mkdir(parents=True)
        staged_output = task_video_staging_path(output_path, "orphan-cleanup")
        staged_output.write_bytes(b"interrupted-download")
        provider_media = project_path / ".arcreel" / "tasks" / "orphan-cleanup" / "provider_media"
        provider_media.mkdir(parents=True)
        (provider_media / "000-start_image.png").write_bytes(b"staged-input")

        class _ProjectManager:
            def get_project_path(self, _project_name):
                return project_path

        monkeypatch.setattr("lib.project.project_manager.get_project_manager", lambda: _ProjectManager())
        worker = GenerationWorker(queue=_FakeQueue())
        await worker._cleanup_video_staging(
            {
                "task_id": "orphan-cleanup",
                "task_type": "video",
                "project_name": "demo",
                "resource_id": "E1S01",
            }
        )

        assert not staged_output.exists()
        assert not provider_media.exists()

    @pytest.mark.asyncio
    async def test_handle_orphan_running_no_job_id_marks_restart_lost(self, monkeypatch):
        """ADR 0007：running 但无 provider_job_id → [restart_lost]。"""
        queue = _FakeQueue()
        queue._orphans = [
            {
                "task_id": "orphan-lost",
                "status": "running",
                "provider_id": None,
                "provider_job_id": None,
                "media_type": "video",
                "task_type": "video",
                "payload": {},
                "project_name": "demo",
            }
        ]
        worker = GenerationWorker(queue=queue)
        await worker._handle_orphan_tasks_on_start()
        assert queue.failed
        assert queue.failed[0][0] == "orphan-lost"
        assert "[restart_lost_no_job_id]" in queue.failed[0][1]

    @pytest.mark.asyncio
    async def test_reference_orphans_apply_strict_four_state_checkpoint_job_matrix(self, staged_project):
        queue = _FakeQueue()
        queue._orphans = [
            {
                "task_id": "ref-neither",
                "status": "running",
                "task_type": "reference_video",
                "media_type": "video",
                "project_name": "demo",
                "resource_id": "E1U1",
                "script_file": "scripts/episode_1.json",
                "provider_job_id": None,
                "payload": {},
            },
            {
                "task_id": "ref-checkpoint-only",
                "status": "running",
                "task_type": "reference_video",
                "media_type": "video",
                "project_name": "demo",
                "resource_id": "E1U1",
                "script_file": "scripts/episode_1.json",
                "provider_job_id": None,
                "execution_checkpoint_json": _worker_reference_checkpoint("ref-checkpoint-only"),
                "payload": {},
            },
            {
                "task_id": "ref-job-only",
                "status": "running",
                "task_type": "reference_video",
                "media_type": "video",
                "project_name": "demo",
                "resource_id": "E1U1",
                "script_file": "scripts/episode_1.json",
                "provider_job_id": "job-1",
                "payload": {},
            },
            {
                "task_id": "ref-bad-checkpoint",
                "status": "running",
                "task_type": "reference_video",
                "media_type": "video",
                "project_name": "demo",
                "resource_id": "E1U1",
                "script_file": "scripts/episode_1.json",
                "provider_job_id": "job-2",
                "execution_checkpoint_json": "{broken",
                "payload": {},
            },
        ]
        worker = GenerationWorker(queue=queue)
        staged = {task["task_id"]: _stage_task_dir(staged_project, task["task_id"]) for task in queue._orphans}

        await worker._handle_orphan_tasks_on_start()

        failures = dict(queue.failed)
        assert "[restart_lost_no_job_id]" in failures["ref-neither"]
        assert "[restart_lost_checkpoint_no_job_id]" in failures["ref-checkpoint-only"]
        assert "[execution_identity_unrecoverable]" in failures["ref-job-only"]
        assert "[execution_identity_unrecoverable]" in failures["ref-bad-checkpoint"]
        assert [task_id for task_id, path in staged.items() if path.exists()] == []
        assert worker._orphan_dispatcher_task is None

    @pytest.mark.asyncio
    async def test_storyboard_orphans_apply_strict_four_state_checkpoint_job_matrix(self, staged_project):
        base = {
            "status": "running",
            "task_type": "video",
            "media_type": "video",
            "project_name": "demo",
            "resource_id": "E1S01",
            "script_file": "scripts/episode_1.json",
            "payload": {},
        }
        queue = _FakeQueue()
        queue._orphans = [
            {**base, "task_id": "story-neither", "provider_job_id": None},
            {
                **base,
                "task_id": "story-checkpoint-only",
                "provider_job_id": None,
                "execution_checkpoint_json": _worker_storyboard_checkpoint("story-checkpoint-only"),
            },
            {**base, "task_id": "story-job-only", "provider_job_id": "job-1"},
            {
                **base,
                "task_id": "story-bad-checkpoint",
                "provider_job_id": "job-2",
                "execution_checkpoint_json": "{broken",
            },
        ]
        worker = GenerationWorker(queue=queue)
        staged = {task["task_id"]: _stage_task_dir(staged_project, task["task_id"]) for task in queue._orphans}

        await worker._handle_orphan_tasks_on_start()

        failures = dict(queue.failed)
        assert "[restart_lost_no_job_id]" in failures["story-neither"]
        assert "[restart_lost_checkpoint_no_job_id]" in failures["story-checkpoint-only"]
        assert "[execution_identity_unrecoverable]" in failures["story-job-only"]
        assert "[execution_identity_unrecoverable]" in failures["story-bad-checkpoint"]
        assert [task_id for task_id, path in staged.items() if path.exists()] == []
        assert worker._orphan_dispatcher_task is None

    @pytest.mark.asyncio
    async def test_reference_orphan_with_checkpoint_and_job_routes_by_checkpoint_provider(
        self, monkeypatch, staged_project
    ):
        from lib.backends.providers import PROVIDER_GROK

        queue = _FakeQueue()
        task = {
            "task_id": "ref-ready",
            "status": "running",
            "task_type": "reference_video",
            "media_type": "video",
            "project_name": "demo",
            "resource_id": "E1U1",
            "script_file": "scripts/episode_1.json",
            "provider_id": PROVIDER_GROK,
            "provider_job_id": "job-ready",
            "execution_checkpoint_json": _worker_reference_checkpoint("ref-ready", provider_id="ark"),
            "payload": {"video_provider_r2v": "current/wrong"},
        }
        queue._orphans = [task]
        worker = GenerationWorker(queue=queue)
        resumed: list[dict[str, Any]] = []

        async def _capture_resume(resume_task, *, job_id):
            resumed.append(resume_task)
            return {"job_id": job_id}

        monkeypatch.setattr("server.services.tasks.resume_executor.execute_resume_video_task", _capture_resume)

        await worker._handle_orphan_tasks_on_start()
        dispatcher_task = worker._orphan_dispatcher_task
        assert dispatcher_task is not None
        await asyncio.wait_for(dispatcher_task, timeout=5)

        # 派发身份取 checkpoint 的 ark，而不是 task 投影列的 Grok 或 payload 里的当前配置
        assert [t["task_id"] for t in resumed] == ["ref-ready"]
        assert resumed[0]["provider_id"] == "ark"
        assert queue.failed == []

    @pytest.mark.asyncio
    async def test_start_stop_run_loop_releases_lease(self):
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)
        worker.heartbeat_interval = 0.01
        worker.poll_interval = 0.01

        await worker.start()
        await asyncio.sleep(0.05)
        await worker.stop()

        assert queue.released
        assert worker._main_task is None

    @pytest.mark.asyncio
    async def test_claim_tasks_dispatches_to_correct_pool(self, monkeypatch):
        """Tasks are dispatched to the correct provider slot."""

        class _ClaimableQueue(_FakeQueue):
            def __init__(self):
                super().__init__()
                self._tasks = [
                    {
                        "task_id": "img1",
                        "task_type": "gen_image",
                        "media_type": "image",
                        "payload": {"image_provider": "gemini-aistudio"},
                    },
                    {
                        "task_id": "vid1",
                        "task_type": "gen_video",
                        "media_type": "video",
                        "payload": {"video_provider_i2v": "ark/doubao-seedance-2-0-260128"},
                    },
                ]

            async def claim_next_task(self, media_type, **_kwargs):  # type: ignore[override]
                for i, t in enumerate(self._tasks):
                    if t["media_type"] == media_type:
                        return self._tasks.pop(i)
                return None

        queue = _ClaimableQueue()
        worker = GenerationWorker(
            queue=queue,
            capacity=_cap({"gemini-aistudio": {"image": 3, "video": 2}, "ark": {"image": 0, "video": 2}}),
        )

        async def _fake_execute(task):
            return {"ok": True}

        monkeypatch.setattr(
            "server.services.tasks.generation_tasks.execute_generation_task",
            _fake_execute,
        )

        claimed = await worker._claim_tasks()
        assert claimed
        assert worker._slots.occupied("gemini-aistudio", "image") == 1
        assert worker._slots.occupied("ark", "video") == 1
        assert worker._slots.active_task_ids() == {"img1", "vid1"}

        # Wait for tasks to complete
        await asyncio.gather(*worker._slots.all_active_tasks(), return_exceptions=True)

    @pytest.mark.asyncio
    async def test_claim_requeue_on_full_pool_refreshes_provider_projection(self, monkeypatch, worker_db):
        """池满回队前把重派生的 provider 刷回投影列。

        走到二次校验池满这条路，说明存量投影与现值分裂（NULL 兜底，或入队后剧本参考集 /
        供应商配置被改）；不刷新的话存量值躲过 pool_full 的 SQL 过滤，满池期间每个 cycle
        都重复 claim → requeue → break，同 lane 其他可跑任务被持续排头阻塞。
        """

        class _StaleProjectionQueue(_FakeQueue):
            def __init__(self):
                super().__init__()
                self._tasks = [
                    {
                        "task_id": "vid-ready",
                        "task_type": "video",
                        "media_type": "video",
                        "provider_id": "ark",
                        "payload": {"video_provider_i2v": "ark/model"},
                    },
                    {
                        "task_id": "vid-stale",
                        "task_type": "reference_video",
                        "media_type": "video",
                        "provider_id": "ark",  # 入队时的投影，与现值分裂
                        "payload": {},
                    },
                ]
                self.claim_kwargs: list[dict] = []

            async def claim_next_task(self, media_type, **kwargs):  # type: ignore[override]
                self.claim_kwargs.append(kwargs)
                if media_type == "video" and self._tasks:
                    return self._tasks.pop()
                return None

        queue = _StaleProjectionQueue()

        async def _current_provider(task):
            return "minimax" if task["task_id"] == "vid-stale" else "ark"

        worker = GenerationWorker(
            queue=queue,
            capacity=_cap({"minimax": {"video": 1}, "ark": {"video": 1}}),
            provider_projection=_current_provider,
        )

        async def _execute(_task, *, claimed_provider_id):
            assert claimed_provider_id == "ark"
            return {"ok": True}

        monkeypatch.setattr("server.services.tasks.generation_tasks.execute_generation_task", _execute)

        # 永不完成的占位 future 把 minimax 的 video 槽占满；不用 sleep，避免真实时间等待
        occupier = asyncio.get_running_loop().create_future()
        worker._slots.register("minimax", "video", "vid-running", occupier)
        await _seed_running_task(worker_db, "vid-stale", task_type="reference_video", resource_id="E1U1")
        try:
            await worker._claim_tasks()
            await asyncio.gather(*[t for t in worker._slots.all_active_tasks() if t is not occupier])
        finally:
            occupier.cancel()

        assert await _task_status(worker_db, "vid-stale") == "queued"
        assert queue.persisted_providers == [("vid-stale", "minimax")]
        assert queue.succeeded == [("vid-ready", {"ok": True})]
        assert any(kwargs.get("exclude_task_ids") == frozenset({"vid-stale"}) for kwargs in queue.claim_kwargs)

    # ------------------------------------------------------------------
    # _pool_full_providers
    # ------------------------------------------------------------------
    def test_pool_full_providers_sources_from_occupancy_with_cap_guard(self):
        """池满黑名单源 = 有占用的 provider；cap==0 守卫短路降级 lane 的在跑占用。

        新设计下黑名单源是 ``occupied_providers``，无占用的 provider 根本不会到达
        ``cap > 0`` 守卫——所以守卫唯一可达场景是「有占用 + cap==0」（运行中 reload
        把某 lane 降级为不支持）。这类 provider 必须 *不* 进黑名单：其在跑任务靠主循环
        drain，新任务走 ``_claim_tasks`` 的 cap==0 fail-fast，而非被 SQL 静默 drop。
        """
        loop = asyncio.new_event_loop()
        dummy = loop.create_future()
        dummy.set_result(None)

        worker = GenerationWorker(
            queue=_FakeQueue(),
            capacity=_cap(
                {
                    "full": {"image": 1, "video": 0},  # cap=1 占 1 → 满
                    "roomy": {"image": 2, "video": 0},  # cap=2 占 1 → 有空
                    "downgraded": {"image": 0, "video": 0},  # 占 1 但 cap=0（被 reload 降级）
                }
            ),
        )
        # 三个 provider 在 image lane 都有占用 → 都会进入 occupied_providers，都到达守卫
        worker._slots.register("full", "image", "t-full", dummy)
        worker._slots.register("roomy", "image", "t-roomy", dummy)
        worker._slots.register("downgraded", "image", "t-down", dummy)

        full_image = worker._pool_full_providers("image")
        assert "full" in full_image, "cap>0 且占满应进黑名单"
        assert "roomy" not in full_image, "cap>0 但有空不进黑名单"
        # 关键：守卫真正被执行——downgraded 有占用 → 到达 cap>0 守卫 → 短路排除。
        # 若直译旧测（无占用的 cap==0 provider），它根本不在 occupied_providers 里，
        # 守卫一行都不会跑，测试会"假通过"。
        assert "downgraded" not in full_image, "cap==0 守卫必须短路，不让降级 lane 进黑名单"

        # video lane 无任何占用 → 黑名单为空（无占用就不可能"满"）
        assert worker._pool_full_providers("video") == frozenset()
        loop.close()

    # ------------------------------------------------------------------
    # _handle_orphan_tasks_on_start：分流补全
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_handle_orphan_image_running_marks_restart_lost(self, worker_db):
        """image 孤儿无 resume 入口 → [restart_lost]，绝不主动 requeue（避免重复扣费）。"""
        queue = _FakeQueue()
        queue._orphans = [
            {
                "task_id": "img-orphan",
                "status": "running",
                "provider_id": "gemini-aistudio",
                "provider_job_id": "should-not-be-used",
                "media_type": "image",
                "task_type": "storyboard",
                "payload": {},
                "project_name": "demo",
            }
        ]
        worker = GenerationWorker(queue=queue)
        await _seed_running_task(worker_db, "img-orphan", task_type="storyboard", media_type="image")

        await worker._handle_orphan_tasks_on_start()

        assert await _task_status(worker_db, "img-orphan") == "running", "image 孤儿绝不能被回队重跑"
        assert queue.failed
        assert queue.failed[0][0] == "img-orphan"
        assert "[restart_lost_image]" in queue.failed[0][1]

    @pytest.mark.asyncio
    async def test_handle_orphan_non_resumable_video_marks_resume_unsupported(self, worker_db, staged_project):
        """Grok/Vidu video 孤儿 → [resume_unsupported]（backend 无 resume，绝不重跑）。"""
        from lib.backends.providers import PROVIDER_GROK

        queue = _FakeQueue()
        queue._orphans = [_storyboard_orphan("grok-orphan", provider_id=PROVIDER_GROK, job_id="some-job")]
        worker = GenerationWorker(queue=queue)
        await _seed_running_task(worker_db, "grok-orphan")

        await worker._handle_orphan_tasks_on_start()

        assert await _task_status(worker_db, "grok-orphan") == "running", "不可 resume 的视频孤儿绝不能被回队重跑"
        assert queue.failed
        assert queue.failed[0][0] == "grok-orphan"
        assert "[resume_unsupported_provider]" in queue.failed[0][1]
        assert PROVIDER_GROK in queue.failed[0][1]

    @pytest.mark.asyncio
    async def test_handle_orphan_running_routes_through_real_queue(self, worker_db, staged_project):
        """重启自愈只扫 running：image / 不可 resume 的视频孤儿落 failed，其余状态的行不被触碰。"""
        from lib.backends.providers import PROVIDER_GROK

        await _seed_running_task(worker_db, "img-run", task_type="storyboard", media_type="image", resource_id="E1S02")
        await _seed_running_task(
            worker_db,
            "grok-run",
            script_file="scripts/episode_1.json",
            provider_id=PROVIDER_GROK,
            provider_job_id="job",
            execution_checkpoint_json=_worker_storyboard_checkpoint("grok-run", provider_id=PROVIDER_GROK),
        )
        await _seed_running_task(
            worker_db, "img-queued", task_type="storyboard", media_type="image", resource_id="E1S03", status="queued"
        )

        worker = GenerationWorker(queue=_db_queue(worker_db))
        await worker._handle_orphan_tasks_on_start()

        assert await _task_status(worker_db, "img-run") == "failed"
        assert await _task_error(worker_db, "img-run") == "[restart_lost_image]"
        assert await _task_status(worker_db, "grok-run") == "failed"
        assert "[resume_unsupported_provider]" in (await _task_error(worker_db, "grok-run") or "")
        assert await _task_status(worker_db, "img-queued") == "queued"
        assert worker._orphan_dispatcher_task is None

    @pytest.mark.asyncio
    async def test_handle_orphan_discard_paths_zero_rows_leave_task_untouched(self, worker_db, staged_project):
        """丢弃路径的 mark_failed 命中 0 行（扫描后行已离开 running）：任务保持原状，不被改判为 cancelled。"""
        from lib.backends.providers import PROVIDER_GROK
        from lib.generation.generation_queue import GenerationQueue

        stale_snapshot = [
            {
                "task_id": "img-raced",
                "status": "running",
                "provider_id": "gemini-aistudio",
                "provider_job_id": None,
                "media_type": "image",
                "task_type": "storyboard",
                "payload": {},
                "project_name": "demo",
            },
            _storyboard_orphan("grok-raced", provider_id=PROVIDER_GROK, job_id="job"),
        ]

        class _StaleScanQueue(GenerationQueue):
            async def list_orphan_tasks_on_start(self):
                return stale_snapshot

        # 行在扫描之后回到 queued：终态写入的守卫只放行 running，而打断兜底会把 queued 翻成 cancelled
        await _seed_running_task(
            worker_db, "img-raced", task_type="storyboard", media_type="image", status="queued", started_at=None
        )
        await _seed_running_task(worker_db, "grok-raced", status="queued", started_at=None)

        worker = GenerationWorker(queue=_StaleScanQueue(session_factory=worker_db))
        await worker._handle_orphan_tasks_on_start()

        assert await _task_status(worker_db, "img-raced") == "queued"
        assert await _task_status(worker_db, "grok-raced") == "queued"

    @pytest.mark.asyncio
    async def test_handle_orphan_uses_checkpoint_provider_id(self, monkeypatch, worker_db, staged_project):
        """checkpoint provider 优先于 task 投影列和当前项目解析。

        如果 checkpoint provider 是 Grok（不支持 resume），即便 task advisory 列和当前项目
        已切换成 Ark（支持 resume），孤儿仍应被识别为 non_resumable → [resume_unsupported]，
        而不是去派发 _process_resume_task 拿旧 job_id 给新 backend 轮询。
        """
        from lib.backends.providers import PROVIDER_GROK

        queue = _FakeQueue()
        orphan = _storyboard_orphan("ghost-orphan", provider_id=PROVIDER_GROK, job_id="stale-job")
        orphan["provider_id"] = "ark"
        orphan["payload"] = {"video_provider_i2v": "ark/doubao-seedance-2-0-260128"}
        queue._orphans = [orphan]
        worker = GenerationWorker(queue=queue)
        await _seed_running_task(worker_db, "ghost-orphan")
        resumed: list[str] = []

        async def _capture_resume(resume_task, *, job_id):
            resumed.append(resume_task["task_id"])
            return {"job_id": job_id}

        monkeypatch.setattr("server.services.tasks.resume_executor.execute_resume_video_task", _capture_resume)

        await worker._handle_orphan_tasks_on_start()

        # 用 checkpoint 的 Grok → [resume_unsupported]；若误用 advisory/payload 的 ark → 会派发 resume
        assert await _task_status(worker_db, "ghost-orphan") == "running"
        assert resumed == []
        assert worker._orphan_dispatcher_task is None
        assert queue.failed
        assert queue.failed[0][0] == "ghost-orphan"
        assert "[resume_unsupported_provider]" in queue.failed[0][1]

    @pytest.mark.asyncio
    async def test_handle_orphan_resumable_dispatches_process_resume_task(self, monkeypatch, staged_project):
        """video resumable provider + 有 job_id → 后台 dispatcher 派发 _process_resume_task。

        Semaphore-based dispatcher 在 sub-task 内填 inflight、finally pop；本测验证
        dispatched 列表收到目标 task 即可（dispatcher 完成时 inflight 已被清理）。
        """
        queue = _FakeQueue()
        queue._orphans = [_storyboard_orphan("ark-orphan", job_id="ark-job-1")]
        worker = GenerationWorker(queue=queue)
        dispatched: list[dict] = []

        async def _capture_resume(task, *, job_id):
            dispatched.append(task)
            return {"job_id": job_id}

        monkeypatch.setattr("server.services.tasks.resume_executor.execute_resume_video_task", _capture_resume)
        await worker._handle_orphan_tasks_on_start()
        # 等后台 dispatcher（含 orphan-dispatcher + provider 桶 sub-task）完成
        for _ in range(50):
            await asyncio.sleep(0)
            if dispatched:
                break
        # 让 dispatcher 自身的 task 跑完（避免 unawaited task 警告）
        for t in list(asyncio.all_tasks()):
            name = t.get_name()
            if name in ("orphan-dispatcher",) or name.startswith(("orphan-dispatch-", "resume-video-")):
                with contextlib.suppress(Exception):
                    await t
        assert len(dispatched) == 1
        assert dispatched[0]["task_id"] == "ark-orphan"

    @pytest.mark.asyncio
    async def test_handle_orphan_fast_path_returns_immediately(self, monkeypatch, staged_project):
        """fast path 不阻塞——5 个可 resume orphan + video_max=2，
        `_handle_orphan_tasks_on_start` 应几乎立刻返回（< 100ms），
        实际 dispatch 由后台 dispatcher 处理。"""
        import time

        queue = _FakeQueue()
        queue._orphans = [_storyboard_orphan(f"orphan-{i}", job_id=f"job-{i}") for i in range(5)]
        worker = GenerationWorker(queue=queue, capacity=_cap({"ark": {"image": 0, "video": 2}}))

        # 让 resume 执行入口 block 住——验证 fast path 不等它完成
        async def _block_forever(_task, *, job_id):
            await asyncio.Event().wait()

        monkeypatch.setattr("server.services.tasks.resume_executor.execute_resume_video_task", _block_forever)

        start = time.monotonic()
        await worker._handle_orphan_tasks_on_start()
        elapsed = time.monotonic() - start
        assert elapsed < 0.1, f"fast path 阻塞了 {elapsed:.3f}s（应 < 100ms）"

        # 清理后台 dispatcher，避免 unawaited task 警告
        for t in list(asyncio.all_tasks()):
            if t.get_name() in ("orphan-dispatcher", "orphan-dispatch-ark"):
                t.cancel()
            if t.get_name().startswith("resume-video-"):
                t.cancel()
        await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_handle_orphan_dispatcher_respects_pool_capacity(self, monkeypatch, staged_project):
        """后台 dispatcher 受 video 容量约束分批 promote 进 INFLIGHT，
        任一时刻 inflight 占用 ≤ cap（pending 不消耗 sem，不计入此上限）。"""
        queue = _FakeQueue()
        queue._orphans = [_storyboard_orphan(f"orphan-{i}", job_id=f"job-{i}") for i in range(4)]
        worker = GenerationWorker(queue=queue, capacity=_cap({"ark": {"image": 0, "video": 2}}))

        # 用 controlled event 让 resume 任务可控完成；同时记录每次 dispatch 时的占用
        snapshots: list[int] = []
        entered: list[str] = []
        gates: dict[str, asyncio.Event] = {f"orphan-{i}": asyncio.Event() for i in range(4)}

        def _inflight() -> int:
            return len(_phase_ids(worker._slots, "ark", "video")[0])

        async def _gated(task, *, job_id):
            snapshots.append(_inflight())
            entered.append(task["task_id"])
            await gates[task["task_id"]].wait()
            return {"job_id": job_id}

        monkeypatch.setattr("server.services.tasks.resume_executor.execute_resume_video_task", _gated)

        await worker._handle_orphan_tasks_on_start()
        # 让 dispatcher 把前 2 个 promote 进 INFLIGHT（其余 2 个 PENDING 排队）
        for _ in range(20):
            await asyncio.sleep(0)
            if _inflight() >= 2:
                break
        assert _inflight() == 2, f"应只有 2 个 inflight，实际 {_inflight()}"

        # 释放一个已 inflight 的 task：其 _run_one finally 会 release 占用 + sem，
        # 腾出名额让 dispatcher 把第 3 个 promote 进来；主循环每 cycle 的 drain_finished
        # 在生产中清理，这里调一次模拟（对 dispatcher sub-task 是 no-op，占用由 finally 释放）。
        gates[entered[0]].set()
        await asyncio.sleep(0)
        worker._slots.drain_finished()
        for _ in range(20):
            await asyncio.sleep(0)
            if _inflight() >= 2:
                break
        # 任一时刻 inflight 都不超过容量 2
        assert _inflight() <= 2
        # 每次 promote 进 _process_resume_task 时的 inflight 快照都 ≤ 容量（核心回归点）
        assert snapshots, "未采集到 inflight 快照"
        assert all(s <= 2 for s in snapshots), f"inflight 快照越过容量上限: {snapshots}"

        # 收尾：释放所有 gate，等 dispatcher 结束
        for gate in gates.values():
            gate.set()
        for _ in range(50):
            await asyncio.sleep(0)
        # 清理可能的残余
        for t in list(asyncio.all_tasks()):
            name = t.get_name()
            if name.startswith(("orphan-", "resume-video-")) and not t.done():
                t.cancel()
        await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_handle_orphan_dispatcher_exits_on_stop_event(self, monkeypatch, staged_project):
        """`_stop_event` 触发时 dispatcher 干净退出，不再 dispatch 剩余 orphan。"""
        queue = _FakeQueue()
        queue._orphans = [_storyboard_orphan(f"orphan-{i}", job_id=f"job-{i}") for i in range(3)]
        worker = GenerationWorker(queue=queue, capacity=_cap({"ark": {"image": 0, "video": 1}}))

        dispatched_count = 0
        first_dispatched = asyncio.Event()
        block_gate = asyncio.Event()

        async def _maybe_block(_task, *, job_id):
            nonlocal dispatched_count
            dispatched_count += 1
            first_dispatched.set()
            await block_gate.wait()
            return {"job_id": job_id}

        monkeypatch.setattr("server.services.tasks.resume_executor.execute_resume_video_task", _maybe_block)

        await worker._handle_orphan_tasks_on_start()
        # 等第一个 orphan 进 inflight
        await asyncio.wait_for(first_dispatched.wait(), timeout=1.0)
        assert dispatched_count == 1
        # 触发停机
        worker._stop_event.set()
        block_gate.set()
        # 让 dispatcher 看到 stop_event 退出（不再 dispatch 剩余 2 个）
        for _ in range(50):
            await asyncio.sleep(0)
            if dispatched_count == 1 and not any(
                t.get_name() == "orphan-dispatcher" and not t.done() for t in asyncio.all_tasks()
            ):
                break
        assert dispatched_count == 1, f"stop_event 后不应再 dispatch，实际 dispatched={dispatched_count}"

    # ------------------------------------------------------------------
    # _process_resume_task：分流 + provider 锁定
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_process_resume_task_uses_storyboard_checkpoint_identity(self, monkeypatch):
        """分镜视频只有 checkpoint + job 齐备才续跑；enqueue payload 不再承担身份锁定。"""
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)
        captured: list[tuple[dict, str]] = []

        async def _fake_resume(task, *, job_id):
            captured.append((task, job_id))
            return {"ok": True}

        monkeypatch.setattr("server.services.tasks.resume_executor.execute_resume_video_task", _fake_resume)

        task = _storyboard_resume_task("resume-locked", provider_id="openai", job_id="openai-job")
        task["payload"] = {"video_provider_i2v": "gemini-aistudio/veo-3.1-fast-generate-preview"}
        await worker._process_resume_task(task)
        assert len(captured) == 1
        captured_task, captured_job_id = captured[0]
        # Resume executor receives the row unchanged and loads its immutable checkpoint itself.
        assert captured_task["payload"] == {"video_provider_i2v": "gemini-aistudio/veo-3.1-fast-generate-preview"}
        assert captured_job_id == "openai-job"
        assert queue.succeeded == [("resume-locked", {"ok": True})]

    @pytest.mark.asyncio
    async def test_process_resume_task_resume_expired(self, monkeypatch):
        """ResumeExpiredError → mark_failed [resume_expired]。"""
        from lib.backends.video_backend_contract import ResumeExpiredError

        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)

        async def _expire(_task, *, job_id):
            raise ResumeExpiredError(job_id=job_id, provider="ark")

        monkeypatch.setattr("server.services.tasks.resume_executor.execute_resume_video_task", _expire)
        task = _storyboard_resume_task("exp", job_id="x")
        await worker._process_resume_task(task)
        assert queue.failed
        assert queue.failed[0][0] == "exp"
        assert "[resume_expired_detail]" in queue.failed[0][1]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failed_rows", [1, 0])
    async def test_process_resume_task_settles_the_call_row_with_the_failure_text(
        self, monkeypatch, worker_db, failed_rows: int
    ):
        """派发侧终态失败：判死这次续跑的异常要随补账落到调用行，不能只翻任务。

        任务侧落的是任务失败码（``[resume_expired_detail]``），记录表读的是调用行的
        error_message / error_code——两张表各有自己的失败登记，调用行那份只能从这里落。
        任务终态写入命中 0 行时调用行照样按失败结算，不改判为 cancelled。
        """
        from lib.backends.video_backend_contract import ResumeExpiredError
        from lib.db.repositories.usage_repo import UsageRepository

        async with worker_db() as session:
            call_id = await UsageRepository(session).start_call(
                project_name="demo", call_type="video", model="m", task_id="exp-row"
            )

        queue = _FakeQueue(failed_rows=failed_rows)
        worker = GenerationWorker(queue=queue)

        async def _expire(_task, *, job_id):
            raise ResumeExpiredError(job_id=job_id, provider="ark")

        monkeypatch.setattr("server.services.tasks.resume_executor.execute_resume_video_task", _expire)
        await worker._process_resume_task(_storyboard_resume_task("exp-row", job_id="x"))
        assert queue.interrupted == []

        async with worker_db() as session:
            row = await session.get(ApiCall, call_id)
        assert (row.status, row.cost_amount) == ("failed", 0)
        assert row.error_message == "resume job x expired or not found on provider ark"
        # 过期异常不带 HTTP 状态也不带上游错误码，分类落空——读侧按原文显示
        assert (row.error_code, row.error_params) == (None, None)

    @pytest.mark.asyncio
    async def test_process_resume_task_endpoint_changed(self, monkeypatch):
        """ResumeEndpointChangedError → mark_failed [resume_endpoint_changed]，错误可归因。"""
        from lib.backends.video_backend_contract import ResumeEndpointChangedError

        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)

        async def _changed(_task, *, job_id):
            raise ResumeEndpointChangedError(
                job_id=job_id,
                provider="custom-7",
                submitted_endpoint="openai-video",
                current_endpoint="minimax-video",
            )

        monkeypatch.setattr("server.services.tasks.resume_executor.execute_resume_video_task", _changed)
        task = _storyboard_resume_task("ep", provider_id="custom-7", job_id="x")
        await worker._process_resume_task(task)
        assert queue.failed
        assert queue.failed[0][0] == "ep"
        assert "[resume_endpoint_changed_detail]" in queue.failed[0][1]
        # 两侧 endpoint 都进错误详情，用户能归因到「接口被换过」而非泛化失败
        assert "openai-video" in queue.failed[0][1]
        assert "minimax-video" in queue.failed[0][1]

    @pytest.mark.asyncio
    async def test_process_resume_task_resume_unsupported(self, monkeypatch):
        """NotImplementedError → mark_failed [resume_unsupported]。"""
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)

        async def _unsup(_task, *, job_id):
            raise NotImplementedError("no resume_video")

        monkeypatch.setattr("server.services.tasks.resume_executor.execute_resume_video_task", _unsup)
        task = _storyboard_resume_task("uns", provider_id="vidu", job_id="x")
        await worker._process_resume_task(task)
        assert queue.failed
        assert queue.failed[0][0] == "uns"
        assert "[resume_unsupported_detail]" in queue.failed[0][1]

    @pytest.mark.asyncio
    async def test_process_resume_task_generic_exception(self, monkeypatch):
        """通用 Exception → mark_failed（无前缀，与运行期 backend 失败同款）。"""
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)

        async def _boom(_task, *, job_id):
            raise RuntimeError("transient backend error")

        monkeypatch.setattr("server.services.tasks.resume_executor.execute_resume_video_task", _boom)
        task = _storyboard_resume_task("boom", job_id="x")
        await worker._process_resume_task(task)
        assert queue.failed
        assert queue.failed[0][0] == "boom"
        # 无 [resume_*] 前缀
        assert not queue.failed[0][1].startswith("[resume_")

    @pytest.mark.asyncio
    async def test_process_resume_task_script_edit_error_encodes_key(self, monkeypatch, staged_project):
        """resume_executor 复用 finalize_reference_video_unit 等 finalize helper，同样会抛
        ScriptEditError；resume 路径与常规 _process_task 走同一份 _encode_task_failure_message，
        不能因为是重启自愈这条独立调用链就退回 str(exc) 的固定中文。"""
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)

        async def _raise_script_edit_error(_task, *, job_id):
            raise ScriptEditError("generated_assets 必须是 dict", key="script_edit_generated_assets_invalid")

        monkeypatch.setattr("server.services.tasks.resume_executor.execute_resume_video_task", _raise_script_edit_error)
        staged = _stage_task_dir(staged_project, "resume_script_edit")
        task = {
            "task_id": "resume_script_edit",
            "task_type": "reference_video",
            "media_type": "video",
            "provider_id": "ark",
            "provider_job_id": "x",
            "execution_checkpoint_json": _worker_reference_checkpoint("resume_script_edit"),
            "payload": {},
            "project_name": "demo",
            "resource_id": "E1U1",
            "script_file": "scripts/episode_1.json",
        }
        await worker._process_resume_task(task)
        assert queue.failed
        assert queue.failed[0] == (
            "resume_script_edit",
            "[script_edit_generated_assets_invalid]",
        )
        assert not staged.exists(), "终态落定后必须清掉该任务的 provider media staging"

    @pytest.mark.asyncio
    async def test_process_resume_task_cancelled_error(self, monkeypatch, worker_db):
        """进程级打断（CancelledError）→ task / ApiCall 都结算 cancelled，再重新抛出。"""
        from lib.db.repositories.usage_repo import UsageRepository

        async with worker_db() as session:
            older_call_id = await UsageRepository(session).start_call(
                project_name="demo", call_type="video", model="old", task_id="rc"
            )
            call_id = await UsageRepository(session).start_call(
                project_name="demo", call_type="video", model="m", task_id="rc"
            )
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)

        async def _cancel(_task, *, job_id):
            raise asyncio.CancelledError

        monkeypatch.setattr("server.services.tasks.resume_executor.execute_resume_video_task", _cancel)
        task = _storyboard_resume_task("rc", job_id="x")
        with pytest.raises(asyncio.CancelledError):
            await worker._process_resume_task(task)
        assert queue.interrupted == ["rc"]
        async with worker_db() as session:
            stored = await stored_calls(session)
        assert [(row.id, row.status) for row in stored] == [
            (call_id, "cancelled"),
            (older_call_id, "pending"),
        ]
        assert stored[0].cost_amount == 0

    @pytest.mark.asyncio
    async def test_process_resume_task_no_job_id_fails_fast(self):
        """无 provider_job_id 的 task 被派发到 _process_resume_task 时直接 mark_failed。"""
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)
        task = {
            "task_id": "no-job",
            "task_type": "video",
            "media_type": "video",
            "provider_job_id": "",
            "payload": {},
            "project_name": "demo",
        }
        await worker._process_resume_task(task)
        assert queue.failed
        assert queue.failed[0][0] == "no-job"
        assert "[restart_lost_no_job_id]" in queue.failed[0][1]


class TestDispatcherFailFastAndPendingTracking:
    """dispatcher fail-fast + pending/inflight 分集合精确容量与 cancel 跟踪。"""

    @pytest.mark.asyncio
    async def test_dispatch_provider_bucket_fail_fast_when_video_max_zero(self, worker_db):
        """video 容量=0 → 直接 mark_failed[resume_unsupported]，不进 Semaphore(0) 死锁。

        容量 0 会先触发一次 reload 兜底：库里这个自定义供应商一个启用模型都没有，
        video lane 因而被真实投影成 0，fail-fast 才落地。
        """
        from lib.db.repositories.custom_provider_repo import CustomProviderRepository

        async with worker_db() as session:
            provider = await CustomProviderRepository(session).create_provider(
                display_name="no-models",
                discovery_format="openai",
                base_url="https://example.invalid",
                api_key="k",
            )
            await session.commit()
        provider_key = f"custom-{provider.id}"

        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue, capacity=_cap({provider_key: {"image": 0, "video": 0}}))

        tasks = [{"task_id": f"orphan-{i}", "provider_id": provider_key} for i in range(3)]
        await worker._dispatch_provider_bucket(provider_key, tasks)

        assert {tid for tid, _ in queue.failed} == {"orphan-0", "orphan-1", "orphan-2"}
        assert all("[resume_unsupported_capacity_zero]" in msg for _, msg in queue.failed)

    @pytest.mark.asyncio
    async def test_capacity_zero_settles_the_pending_call(self, worker_db):
        """派发前就判死时那条 pending 的 ApiCall 也要翻 failed（零费用）。

        续跑不开新记账括号，只翻任务不结算调用会在用量报表里留一条永不终态的行；
        「重试下载」尤其明显——它刚把这条调用重开成 pending。
        """
        from lib.db.repositories.custom_provider_repo import CustomProviderRepository
        from lib.db.repositories.usage_repo import UsageRepository

        async with worker_db() as session:
            provider = await CustomProviderRepository(session).create_provider(
                display_name="no-models",
                discovery_format="openai",
                base_url="https://example.invalid",
                api_key="k",
            )
            call_id = await UsageRepository(session).start_call(
                project_name="demo", call_type="video", model="m", task_id="retry-1"
            )
            await session.commit()
        provider_key = f"custom-{provider.id}"

        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue, capacity=_cap({provider_key: {"image": 0, "video": 0}}))

        await worker._dispatch_provider_bucket(
            provider_key,
            [{"task_id": "retry-1", "provider_id": provider_key, "payload": {}}],
        )

        async with worker_db() as session:
            row = await session.get(ApiCall, call_id)
        assert (row.status, row.cost_amount) == ("failed", 0)
        # 判死时没有异常对象可分类，只有一段原文：落 error_message，不落码
        assert row.error_message == f"resume unsupported: provider {provider_key} has no video capacity"
        assert (row.error_code, row.error_params) == (None, None)

    @pytest.mark.asyncio
    async def test_sub_task_registered_in_pending_before_sem_acquire(self, monkeypatch, staged_project):
        """sem=1 + 2 task：第 2 个 sub-task sem 排队期间应以 PENDING 登记在台账。"""
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue, capacity=_cap({"ark": {"image": 0, "video": 1}}))

        gate = asyncio.Event()

        async def _gated(_task, *, job_id):
            await gate.wait()
            return {"job_id": job_id}

        monkeypatch.setattr("server.services.tasks.resume_executor.execute_resume_video_task", _gated)

        tasks = [_storyboard_resume_task(f"orphan-{i}", job_id=f"job-{i}") for i in range(2)]
        dispatcher = asyncio.create_task(worker._dispatch_provider_bucket("ark", tasks))

        for _ in range(20):
            await asyncio.sleep(0)
            inflight, _pending = _phase_ids(worker._slots, "ark", "video")
            if len(inflight) == 1:
                break
        inflight, pending = _phase_ids(worker._slots, "ark", "video")
        assert len(inflight) == 1
        assert len(pending) == 1
        # pending 计入容量：占用=2 ≥ cap=1 → 无空位，主循环不会超额 claim
        assert worker._slots.has_room("ark", "video", 1) is False

        gate.set()
        await dispatcher

    @pytest.mark.asyncio
    async def test_sem_queued_interruption_lands_terminal(self, monkeypatch, staged_project):
        """sem 排队期被打断：_run_one 显式落终态，任务不停在 running。"""
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue, capacity=_cap({"ark": {"image": 0, "video": 1}}))

        gate = asyncio.Event()
        first_started = asyncio.Event()

        async def _gated(_task, *, job_id):
            first_started.set()
            await gate.wait()
            return {"job_id": job_id}

        monkeypatch.setattr("server.services.tasks.resume_executor.execute_resume_video_task", _gated)

        tasks = [_storyboard_resume_task(f"orphan-{i}", job_id=f"job-{i}") for i in range(2)]
        dispatcher = asyncio.create_task(worker._dispatch_provider_bucket("ark", tasks))

        # 等第 1 个 task 进入 _process_resume_task（占住 sem），第 2 个还在 sem 排队
        await asyncio.wait_for(first_started.wait(), timeout=1.0)
        _inflight, pending = _phase_ids(worker._slots, "ark", "video")
        assert "orphan-1" in pending

        # 打断 sem 排队中的 orphan-1
        _active_task_named(worker._slots, "resume-video-orphan-1").cancel()

        # 让 dispatcher 跑完
        gate.set()
        await dispatcher

        assert queue.interrupted == ["orphan-1"]
        assert [tid for tid, _ in queue.succeeded] == ["orphan-0"]

    @pytest.mark.asyncio
    async def test_acquired_pre_process_interruption_lands_terminal(self, staged_project):
        """acquire 后、_process_resume_task 入 try 之前被打断：_run_one 兜底落终态。

        漏窗就是 _process_resume_task 内 try 块之前那段 provider 投影 await：打断落在
        那里时内部不会落终态，必须由 _run_one 兜底。任务不带 checkpoint 才会走到投影。
        """
        queue = _FakeQueue()

        acquired_event = asyncio.Event()
        pre_try_gate = asyncio.Event()

        async def _blocked_projection(_task):
            acquired_event.set()
            await pre_try_gate.wait()
            return "ark"

        worker = GenerationWorker(
            queue=queue,
            capacity=_cap({"ark": {"image": 0, "video": 1}}),
            provider_projection=_blocked_projection,
        )

        tasks = [
            {
                "task_id": "orphan-pre-try",
                "provider_id": "ark",
                "media_type": "video",
                "provider_job_id": "job-pre-try",
                "project_name": "demo",
            }
        ]
        dispatcher = asyncio.create_task(worker._dispatch_provider_bucket("ark", tasks))

        # 等 _process_resume_task 进入空窗（await pre_try_gate.wait() 期间）
        await asyncio.wait_for(acquired_event.wait(), timeout=1.0)

        # 现在 task 在 acquired=True（已 promote 为 INFLIGHT）状态，但内部还没接管终态
        _active_task_named(worker._slots, "resume-video-orphan-pre-try").cancel()

        # gate 放开（CancelledError 已经在路上）
        pre_try_gate.set()
        await dispatcher

        # 必须落终态，不能停在 running
        assert queue.interrupted == ["orphan-pre-try"]

    @pytest.mark.asyncio
    async def test_dispatcher_handle_set_after_handle_orphan(self, monkeypatch, staged_project):
        """_handle_orphan_tasks_on_start 后 self._orphan_dispatcher_task 应被设置。"""
        queue = _FakeQueue()
        queue._orphans = [_storyboard_orphan("orphan-x", job_id="job-x")]
        worker = GenerationWorker(queue=queue, capacity=_cap({"ark": {"image": 0, "video": 1}}))

        block = asyncio.Event()

        async def _gated(_task, *, job_id):
            await block.wait()
            return {"job_id": job_id}

        monkeypatch.setattr("server.services.tasks.resume_executor.execute_resume_video_task", _gated)

        await worker._handle_orphan_tasks_on_start()
        assert worker._orphan_dispatcher_task is not None
        assert not worker._orphan_dispatcher_task.done()

        block.set()
        await worker._orphan_dispatcher_task


class TestOrphanScanSelfPreemption:
    """lease flap > 3×TTL 重夺时，本进程仍 inflight 的 task 不应被当孤儿处理。"""

    @pytest.mark.asyncio
    async def test_image_inflight_not_marked_restart_lost(self, monkeypatch):
        """本进程 image_inflight 含 task → 孤儿扫描应跳过，不标 [restart_lost]。"""
        queue = _FakeQueue()
        queue._orphans = [
            {
                "task_id": "img-active",
                "status": "running",
                "media_type": "image",
                "task_type": "storyboard",
                "payload": {},
                "project_name": "demo",
            }
        ]
        worker = GenerationWorker(queue=queue)
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        worker._slots.register("ark", "image", "img-active", fut)

        await worker._handle_orphan_tasks_on_start()

        # dispatcher 句柄不应被设置（本进程仍在跑，无 resumable 任务进 dispatcher）
        assert worker._orphan_dispatcher_task is None
        # 不应被标 [restart_lost] —— 本进程还在跑
        assert "img-active" not in {tid for tid, _ in queue.failed}
        # 清理
        fut.set_result(None)

    @pytest.mark.asyncio
    async def test_video_inflight_not_dispatched_to_resume(self):
        """本进程 video_inflight 含 task → 孤儿扫描应跳过，不启动重复 resume 流。"""
        queue = _FakeQueue()
        queue._orphans = [
            {
                "task_id": "vid-active",
                "status": "running",
                "media_type": "video",
                "task_type": "video",
                "provider_id": "ark",
                "provider_job_id": "ark-job-1",
                "payload": {},
                "project_name": "demo",
            }
        ]
        worker = GenerationWorker(queue=queue)
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        worker._slots.register("ark", "video", "vid-active", fut)

        await worker._handle_orphan_tasks_on_start()

        # dispatcher 句柄从未被创建 → 本进程 inflight 的 task 没有被重复 dispatch
        assert worker._orphan_dispatcher_task is None
        assert "vid-active" not in {tid for tid, _ in queue.failed}
        fut.set_result(None)

    @pytest.mark.asyncio
    async def test_video_pending_also_skipped(self):
        """本进程 video_pending（sem 排队中）含 task → 孤儿扫描应跳过。"""
        queue = _FakeQueue()
        queue._orphans = [
            {
                "task_id": "vid-pending",
                "status": "running",
                "media_type": "video",
                "task_type": "video",
                "provider_id": "ark",
                "provider_job_id": "ark-job-2",
                "payload": {},
                "project_name": "demo",
            }
        ]
        worker = GenerationWorker(queue=queue)
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        worker._slots.register("ark", "video", "vid-pending", fut, pending=True)

        await worker._handle_orphan_tasks_on_start()

        assert "vid-pending" not in {tid for tid, _ in queue.failed}
        # dispatcher 句柄不应被设置（无 resumable 任务进 dispatcher）
        assert worker._orphan_dispatcher_task is None
        fut.set_result(None)


class TestOrphanDispatcherNonBlockingOverride:
    """lease 重夺时旧 dispatcher 仍在跑：本轮直接覆盖句柄，不 await（liveness）也不 cancel
    （避免错误中断 in-flight resume）。"""

    @pytest.mark.asyncio
    async def test_old_dispatcher_not_awaited_on_re_scan(self, monkeypatch, staged_project):
        """旧 dispatcher 跑 5s 时，再次进 _handle_orphan_tasks_on_start 应秒级返回（不阻塞）。"""
        queue = _FakeQueue()
        queue._orphans = [_storyboard_orphan("orphan-x", job_id="job-x")]
        worker = GenerationWorker(queue=queue, capacity=_cap({"ark": {"image": 0, "video": 1}}))

        block = asyncio.Event()

        async def _gated(_task, *, job_id):
            await block.wait()
            return {"job_id": job_id}

        monkeypatch.setattr("server.services.tasks.resume_executor.execute_resume_video_task", _gated)

        # 第 1 次扫描：启动旧 dispatcher
        await worker._handle_orphan_tasks_on_start()
        old_dispatcher = worker._orphan_dispatcher_task
        assert old_dispatcher is not None
        assert not old_dispatcher.done()

        # 第 2 次扫描（模拟 lease flap 超阈值后重夺）：应直接覆盖句柄、不阻塞
        start = asyncio.get_event_loop().time()
        await worker._handle_orphan_tasks_on_start()
        elapsed = asyncio.get_event_loop().time() - start
        # 应在 1s 内完成；旧 dispatcher 仍未 done 但句柄已被新的覆盖
        assert elapsed < 1.0, f"重夺时不应阻塞：elapsed={elapsed:.3f}s"
        assert worker._orphan_dispatcher_task is not old_dispatcher
        # 旧 dispatcher 未被 cancel——in-flight resume 不应被错误中断
        assert not old_dispatcher.cancelled()
        assert not old_dispatcher.done()

        # 清理：放开 gate 让 dispatcher 完成
        block.set()
        new_dispatcher = worker._orphan_dispatcher_task
        assert new_dispatcher is not None
        await asyncio.gather(old_dispatcher, new_dispatcher, return_exceptions=True)


class TestOrphanOnceAndLeaseFlap:
    """orphan 一次性扫描 + lease flap 阈值。"""

    def _build_worker(self) -> tuple[GenerationWorker, _FakeQueue, list[int]]:
        """构造 worker；返回 (worker, queue, scan_count)。scan_count 记录扫描次数。"""
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)
        scan_count: list[int] = []

        async def _spy_scan():
            scan_count.append(1)

        # 替换 _handle_orphan_tasks_on_start 为 spy，便于精确断言扫描次数
        worker._handle_orphan_tasks_on_start = _spy_scan
        return worker, queue, scan_count

    @pytest.mark.asyncio
    async def test_orphan_scanned_once_on_first_lease_acquire(self):
        worker, _, scan_count = self._build_worker()
        worker._owns_lease = True
        worker._orphan_handled_once = False

        # 模拟主循环里那段守卫
        if worker._owns_lease and not worker._orphan_handled_once:
            await worker._handle_orphan_tasks_on_start()
            worker._orphan_handled_once = True

        assert len(scan_count) == 1
        assert worker._orphan_handled_once is True

    @pytest.mark.asyncio
    async def test_orphan_not_rescanned_in_steady_state(self):
        """稳定持 lease 多拍主循环：扫描仅 1 次。"""
        worker, _, scan_count = self._build_worker()
        worker._owns_lease = True

        for _ in range(5):
            if worker._owns_lease and not worker._orphan_handled_once:
                await worker._handle_orphan_tasks_on_start()
                worker._orphan_handled_once = True

        assert len(scan_count) == 1

    @pytest.mark.asyncio
    async def test_orphan_does_not_rescan_on_short_lease_flap(self):
        """lease flap < lease_ttl：不重扫。"""
        worker, _, scan_count = self._build_worker()
        worker.lease_ttl = 10.0

        # 首次获得 lease 扫一次
        worker._owns_lease = True
        await worker._handle_orphan_tasks_on_start()
        worker._orphan_handled_once = True

        # 模拟丢 lease 1 秒后又夺回（短 flap）
        import time as _time

        worker._lease_lost_monotonic = _time.monotonic() - 1.0
        # 应用 _run_loop 中的逻辑片段
        lost_duration = _time.monotonic() - worker._lease_lost_monotonic
        if lost_duration > worker.lease_ttl * _ORPHAN_RESCAN_LEASE_LOST_MULT:
            worker._orphan_handled_once = False
        worker._lease_lost_monotonic = None

        # flap 时长远小于 30s，仍是 handled_once
        assert worker._orphan_handled_once is True
        if worker._owns_lease and not worker._orphan_handled_once:
            await worker._handle_orphan_tasks_on_start()
        assert len(scan_count) == 1, "短 flap 不应重扫"

    @pytest.mark.asyncio
    async def test_orphan_does_not_rescan_below_3x_ttl(self):
        """lease_ttl < lost < 3×lease_ttl：仍不重扫（边界）。"""
        worker, _, scan_count = self._build_worker()
        worker.lease_ttl = 10.0

        worker._owns_lease = True
        await worker._handle_orphan_tasks_on_start()
        worker._orphan_handled_once = True

        import time as _time

        # lost = 15s（介于 ttl=10s 与 3×ttl=30s 之间）
        worker._lease_lost_monotonic = _time.monotonic() - 15.0
        lost_duration = _time.monotonic() - worker._lease_lost_monotonic
        if lost_duration > worker.lease_ttl * _ORPHAN_RESCAN_LEASE_LOST_MULT:
            worker._orphan_handled_once = False
        worker._lease_lost_monotonic = None

        assert worker._orphan_handled_once is True
        if worker._owns_lease and not worker._orphan_handled_once:
            await worker._handle_orphan_tasks_on_start()
        assert len(scan_count) == 1, "15s 仍 < 3×ttl=30s，不应重扫"

    @pytest.mark.asyncio
    async def test_orphan_rescans_after_real_lease_handoff(self):
        """lost > 3×lease_ttl：清零开关，下次重扫。"""
        worker, _, scan_count = self._build_worker()
        worker.lease_ttl = 10.0

        worker._owns_lease = True
        await worker._handle_orphan_tasks_on_start()
        worker._orphan_handled_once = True

        import time as _time

        # lost = 40s > 30s 阈值，认为另一进程曾持过 lease
        worker._lease_lost_monotonic = _time.monotonic() - 40.0
        lost_duration = _time.monotonic() - worker._lease_lost_monotonic
        if lost_duration > worker.lease_ttl * _ORPHAN_RESCAN_LEASE_LOST_MULT:
            worker._orphan_handled_once = False
        worker._lease_lost_monotonic = None

        assert worker._orphan_handled_once is False
        if worker._owns_lease and not worker._orphan_handled_once:
            await worker._handle_orphan_tasks_on_start()
            worker._orphan_handled_once = True
        assert len(scan_count) == 2, "lost 超过 3×ttl 应重扫一次"


class TestStartupInterruptedCallSettlement:
    """启动收口挂在孤儿处理旁：同一 lease 内只跑一次，且跑在孤儿处理之后。"""

    class _CyclingQueue(_FakeQueue):
        """认领若干轮后置位事件，让测试等到主循环真的转了几圈再断言。"""

        def __init__(self, cycles: int = 3):
            super().__init__()
            self._target_cycles = cycles
            self._cycles = 0
            self.cycled = asyncio.Event()

        async def claim_next_task(self, media_type, **_kwargs):
            if media_type == "image":
                self._cycles += 1
                if self._cycles >= self._target_cycles:
                    self.cycled.set()
            return

    async def _run_until_settled(self, worker, queue) -> None:
        worker.heartbeat_interval = 0.01
        worker.poll_interval = 0.01
        await worker.start()
        try:
            await asyncio.wait_for(queue.cycled.wait(), timeout=5)
        finally:
            await worker.stop()

    @pytest.mark.asyncio
    async def test_settles_once_and_after_orphan_handling(self):
        queue = self._CyclingQueue()
        queue._orphans = [
            {"task_id": "orphan-image", "status": "running", "task_type": "gen_image", "media_type": "image"}
        ]
        seen_failed_orphans: list[list[str]] = []

        async def _settle(*, taskless_started_before: datetime | None) -> int:
            seen_failed_orphans.append([task_id for task_id, _error in queue.failed])
            return 0

        worker = GenerationWorker(queue=queue, settle_interrupted_calls=_settle)
        await self._run_until_settled(worker, queue)

        assert seen_failed_orphans == [["orphan-image"]], "收口只跑一次，且孤儿已先被处理"

    @pytest.mark.asyncio
    async def test_rescan_after_lease_loss_keeps_taskless_calls(self):
        """首次收口只收 worker 构造之前发起的无任务行；之后的重扫只收绑定任务的行。"""
        queue = self._CyclingQueue()
        seen: list[datetime | None] = []

        async def _settle(*, taskless_started_before: datetime | None) -> int:
            seen.append(taskless_started_before)
            return 0

        before = datetime.now(UTC)
        worker = GenerationWorker(queue=queue, settle_interrupted_calls=_settle)
        after = datetime.now(UTC)
        await self._run_until_settled(worker, queue)
        assert len(seen) == 1
        assert seen[0] is not None
        assert before <= seen[0] <= after

        # lease 丢失超过阈值后的重扫：进程仍存活，无任务身份的行可能正在本进程里跑。
        worker._orphan_handled_once = False
        queue._cycles = 0
        queue.cycled.clear()
        await self._run_until_settled(worker, queue)
        assert seen[1:] == [None]

    @pytest.mark.asyncio
    async def test_default_wiring_flips_orphan_pending_call_row(self, worker_db):
        """不注入替身时走真实 Ledger，落在队列那处接线的库里：无任务身份的 pending 行翻成 failed[interrupted]。"""
        from lib.db.models.api_call import ApiCall
        from lib.db.repositories.usage_repo import UsageRepository

        async with worker_db() as session:
            call_id = await UsageRepository(session).start_call(
                project_name="demo", call_type="text", model="m", provider="anthropic"
            )

        queue = self._CyclingQueue()
        await self._run_until_settled(GenerationWorker(queue=queue), queue)

        async with worker_db() as session:
            row = await session.get(ApiCall, call_id)
        assert (row.status, row.error_code) == ("failed", "interrupted")
