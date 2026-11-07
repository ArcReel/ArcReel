"""Full-chain restart/resume regression for custom-endpoint (``ce-<id>``) video tasks.

Exercise DB ce- rows → orphan scan → execute_resume_video_task → resolve_generation_context → real DeclarativeVideoBackend → HTTP. Persist actual custom_endpoint / custom_provider / custom_provider_model and running-task rows; keep all business functions real, stubbing outbound HTTP only with respx. Assert HTTP counts (no duplicate submission charges), terminal task state and paid artifact bytes."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from lib.artifacts.artifact_manifest import ArtifactBasis, compose_video_artifact_basis
from lib.artifacts.video_artifact_facts import VideoArtifactCurrencyFacts
from lib.catalog.errors import ModelDiscontinuedError
from lib.catalog.query import factory_snapshot
from lib.config.service import ConfigService
from lib.custom_provider import make_endpoint_key, make_provider_id
from lib.custom_provider.path_overrides import endpoint_guard
from lib.db.models.task import Task
from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.generation.generation_queue import GenerationQueue
from lib.generation.generation_worker import GenerationWorker
from lib.project.project_manager import ProjectManager
from lib.script.reference_video.execution_checkpoint import (
    StagedProviderMedia,
    StoryboardSubmissionCheckpoint,
)
from server.services.tasks import generation_context
from server.services.tasks.generation_context import VideoLaneRequest, resolve_generation_context
from server.services.tasks.generation_tasks import execute_generation_task
from server.services.tasks.resume_executor import execute_resume_video_task
from tests.catalog_support import install_catalog, set_catalog_today, with_entry_changes
from tests.factories import custom_endpoint_definition
from tests.fakes import bind_safe_session_factory, bind_session_factory, use_video_thumbnail_extractor
from tests.http_capture import capture_http


@pytest.fixture(autouse=True)
def clean_backend_cache():
    """Clear per-key locks as well as cache entries.

    pytest-asyncio uses independent loops per test. Reusing cache keys after prior contention leaves ``asyncio.Lock`` bound to a closed loop; later contention raises ``RuntimeError``. Clearing locks does not change production behavior under test."""
    generation_context.reset_backend_cache_for_tests()
    yield
    generation_context.reset_backend_cache_for_tests()


@pytest.fixture
async def chain_project(session_factory, tmp_path: Path, monkeypatch) -> Path:
    """Shared full-chain environment: global session factories point to dialect-sensitive test DB and a real temporary ProjectManager.

    Address environment dependencies without stubbing business flow:
    - Redirect both resume-path factories: ``lib.db.async_session_factory`` imported inside ``resolve_generation_context``, and worker ``safe_session_factory``. Its ``safe_session_factory`` singleton opens sessions through ``lib.db.engine.async_session_factory``, which is redirected too.
    - Isolate admission file locks under temporary ``app_data_dir()``.
    - Replace ``get_project_manager`` in each consuming module (resolver / generation_context / resume_executor / finalize helpers / worker cleanup) with the real temporary manager; top-level bound imports would ignore patches only at the definition site.
    - Replace ffmpeg thumbnail extraction with no-op to keep tests self-contained."""
    bind_session_factory(monkeypatch, session_factory)
    bind_safe_session_factory(monkeypatch, session_factory)
    monkeypatch.setattr("lib.db.engine.async_session_factory", session_factory)
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path / "appdata"))

    pm = ProjectManager(tmp_path)
    for target in (
        "lib.project.project_manager.get_project_manager",
        "lib.config.resolver.get_project_manager",
        "lib.generation.generation_worker.get_project_manager",
        "lib.generation.video_resume.get_project_manager",
        "server.services.tasks.generation_context.get_project_manager",
        "server.services.tasks.resume_executor.get_project_manager",
        "server.services.tasks.generation_tasks.get_project_manager",
        "server.services.tasks.reference_video_tasks.get_project_manager",
    ):
        monkeypatch.setattr(target, lambda: pm)
    project_dir = tmp_path / "projects" / "demo"
    (project_dir / "scripts").mkdir(parents=True)
    (project_dir / "project.json").write_text(
        json.dumps(
            {
                "content_mode": "narration",
                "default_duration": 8,
                "aspect_ratio": "9:16",
                "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
            }
        ),
        encoding="utf-8",
    )
    (project_dir / "scripts" / "episode_1.json").write_text(
        json.dumps(
            {
                "episode": 1,
                "content_mode": "narration",
                "segments": [{"segment_id": "E1S01", "novel_text": "n"}],
            }
        ),
        encoding="utf-8",
    )

    async def _no_thumbnail(*_args: Any, **_kwargs: Any) -> bool:
        return False

    use_video_thumbnail_extractor(monkeypatch, _no_thumbnail)
    return project_dir


async def _seed_custom_video_rows(
    session_factory,
    *,
    endpoint_count: int = 1,
    model_endpoint_index: int = 0,
    path_overrides: dict[str, str] | None = None,
    model_id: str = "video-x",
) -> dict:
    """Persist custom-provider trio: endpoint definition, provider and video-model row bound to an endpoint key.

    ``endpoint_count`` > 1 creates extra endpoint rows; ``model_endpoint_index`` selects model binding to exercise model-endpoint/checkpoint mismatch. ``path_overrides`` is the model's current path override; ``model_id`` matching a catalog entry binds by name."""
    async with session_factory() as session:
        endpoint_repo = CustomEndpointRepository(session)
        endpoint_keys: list[str] = []
        for index in range(endpoint_count):
            endpoint = await endpoint_repo.create(
                definition=custom_endpoint_definition(),
                kind="declarative",
                schema_version="1.0.0",
                media_type="video",
                display_name=f"示例端点 {index}",
            )
            endpoint_keys.append(make_endpoint_key(endpoint.id))
        provider = await CustomProviderRepository(session).create_provider(
            display_name="中转站",
            discovery_format="openai",
            base_url="https://relay.test",
            api_key="sk-secret-key-1234",
            models=[
                {
                    "model_id": model_id,
                    "display_name": model_id,
                    "endpoint": endpoint_keys[model_endpoint_index],
                    "is_enabled": True,
                    "is_default": True,
                    "path_overrides": path_overrides,
                }
            ],
        )
        await session.commit()
        return {"provider_id": make_provider_id(provider.id), "endpoint_keys": endpoint_keys}


def _storyboard_checkpoint_json(
    task_id: str, *, provider_id: str, endpoint_guard: str | None, model_id: str = "video-x"
) -> str:
    """Real ``StoryboardSubmissionCheckpoint.create(...).to_json()`` with ``provider_id`` / ``model_id`` identity."""
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
        provider_model_id=model_id,
        backend_model_id=model_id,
        endpoint_guard=endpoint_guard,
        prompt="frozen",
        duration_seconds=8,
        aspect_ratio="9:16",
        resolution="720p",
        generate_audio=True,
        service_tier="default",
        seed=None,
        visual_basis_digest="b" * 64,
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
        media=(
            StagedProviderMedia(
                index=0,
                role="start_image",
                logical_type="storyboard",
                logical_name="E1S01",
                kind="first_frame",
                source_locator="storyboards/scene_E1S01.png",
                staged_locator=f".arcreel/tasks/{task_id}/provider_media/000-start_image.png",
                sha256="c" * 64,
                size_bytes=1,
            ),
        ),
        reference_audio_targets=None,
    ).to_json()


async def _seed_running_video_task(
    session_factory,
    *,
    task_id: str,
    provider_id: str,
    checkpoint_json: str,
    submitted_base_url: str | None = None,
) -> None:
    """Seed a submitted running video task with job_id + checkpoint for orphan-scan claiming."""
    now = datetime.now(UTC)
    async with session_factory() as session:
        session.add(
            Task(
                task_id=task_id,
                project_name="demo",
                task_type="video",
                media_type="video",
                resource_id="E1S01",
                script_file="scripts/episode_1.json",
                status="running",
                provider_id=provider_id,
                provider_job_id="job-old",
                submitted_base_url=submitted_base_url,
                execution_checkpoint_json=checkpoint_json,
                queued_at=now,
                started_at=now,
                updated_at=now,
            )
        )
        await session.commit()


async def _load_task_row(session_factory, task_id: str) -> Task:
    async with session_factory() as session:
        row = await session.get(Task, task_id)
        assert row is not None
        return row


async def _run_orphan_recovery(session_factory) -> None:
    """Use real worker + queue for one startup orphan scan and await background dispatcher completion."""
    worker = GenerationWorker(
        queue=GenerationQueue(session_factory=session_factory),
        executor=execute_generation_task,
        resume_executor=execute_resume_video_task,
    )
    await worker._recovery.handle_orphans()
    dispatcher = worker._recovery.orphan_dispatcher_task
    assert dispatcher is not None
    await asyncio.wait_for(dispatcher, timeout=10)


async def test_ce_orphan_resumes_end_to_end_without_resubmitting(chain_project: Path, session_factory):
    """Happy path: resolve ce- rows into real backends and resume only poll/download without resubmission.

    Task ``submitted_base_url`` is historical and differs from current provider domain. Resume must replay it; changing base_url after submission must not strand paid jobs."""
    rows = await _seed_custom_video_rows(session_factory)
    await _seed_running_video_task(
        session_factory,
        task_id="T-ce-resume",
        provider_id=rows["provider_id"],
        checkpoint_json=_storyboard_checkpoint_json(
            "T-ce-resume",
            provider_id=rows["provider_id"],
            endpoint_guard=rows["endpoint_keys"][0],
        ),
        submitted_base_url="https://old-relay.test",
    )

    with capture_http() as router:
        submit = router.post("https://relay.test/v1/video/create")
        poll_current = router.get("https://relay.test/v1/video/fetch/job-old")
        poll = router.get("https://old-relay.test/v1/video/fetch/job-old").mock(
            return_value=httpx.Response(
                200,
                json={"status": "completed", "video_url": "https://old-relay.test/files/job-old.mp4"},
            )
        )
        download = router.get("https://old-relay.test/files/job-old.mp4").mock(
            return_value=httpx.Response(200, content=b"resumed")
        )

        await _run_orphan_recovery(session_factory)

    # Resume never resubmits charges: zero submit requests, only poll original jobs and retrieve artifacts.
    # Poll historical submission domains rather than providers' current domains.
    assert submit.call_count == 0
    assert poll_current.call_count == 0
    assert poll.call_count == 1
    assert download.call_count == 1

    row = await _load_task_row(session_factory, "T-ce-resume")
    assert row.status == "succeeded"
    result = json.loads(row.result_json or "{}")
    assert result["resource_id"] == "E1S01"
    # Paid bytes enter version history; if current project differs from frozen submission, retain history without selection while task still succeeds.
    paid_files = sorted((chain_project / "versions" / "videos").glob("E1S01_v*.mp4"))
    assert [path.read_bytes() for path in paid_files] == [b"resumed"]


async def test_ce_orphan_fails_before_any_http_when_endpoint_rebound(chain_project: Path, session_factory):
    """After model endpoint rebinding, the endpoint guard fails resume before any HTTP request."""
    rows = await _seed_custom_video_rows(session_factory, endpoint_count=2, model_endpoint_index=1)
    await _seed_running_video_task(
        session_factory,
        task_id="T-ce-rebound",
        provider_id=rows["provider_id"],
        checkpoint_json=_storyboard_checkpoint_json(
            "T-ce-rebound",
            provider_id=rows["provider_id"],
            endpoint_guard=rows["endpoint_keys"][0],
        ),
    )

    with capture_http() as router:
        submit = router.post("https://relay.test/v1/video/create")
        poll = router.get("https://relay.test/v1/video/fetch/job-old")

        await _run_orphan_recovery(session_factory)

    # Exact-comparison failure precedes backend calls: neither poll nor submit may send traffic.
    assert submit.call_count == 0
    assert poll.call_count == 0

    row = await _load_task_row(session_factory, "T-ce-rebound")
    assert row.status == "failed"
    assert row.error_message is not None
    assert "resume_endpoint_changed" in row.error_message
    # No artifacts persisted.
    assert not (chain_project / "versions" / "videos").exists()


async def test_ce_orphan_fails_without_resubmitting_when_path_overrides_changed(chain_project: Path, session_factory):
    """Path overrides freeze with protocol: editing request addresses after submission fails resume like endpoint rebinding, without resubmission."""
    rows = await _seed_custom_video_rows(session_factory, path_overrides={"poll": "/v2/fetch/{{ task_id }}"})
    endpoint_key = rows["endpoint_keys"][0]
    await _seed_running_video_task(
        session_factory,
        task_id="T-ce-paths",
        provider_id=rows["provider_id"],
        checkpoint_json=_storyboard_checkpoint_json(
            "T-ce-paths",
            provider_id=rows["provider_id"],
            endpoint_guard=endpoint_guard(endpoint_key, {"poll": "/v1/video/fetch/{{ task_id }}"}),
        ),
    )

    with capture_http() as router:
        submit = router.post("https://relay.test/v1/video/create")
        poll_submitted = router.get("https://relay.test/v1/video/fetch/job-old")
        poll_current = router.get("https://relay.test/v2/fetch/job-old")

        await _run_orphan_recovery(session_factory)

    assert submit.call_count == 0
    assert poll_submitted.call_count == 0
    assert poll_current.call_count == 0
    row = await _load_task_row(session_factory, "T-ce-paths")
    assert row.status == "failed"
    assert row.error_message is not None
    assert "resume_endpoint_changed" in row.error_message


async def test_ce_orphan_resumes_through_unchanged_path_overrides(chain_project: Path, session_factory):
    """Unchanged overrides pass protocol comparison and retrieve through overridden polling URLs."""
    overrides = {"poll": "/v2/fetch/{{ task_id }}"}
    rows = await _seed_custom_video_rows(session_factory, path_overrides=overrides)
    await _seed_running_video_task(
        session_factory,
        task_id="T-ce-paths-kept",
        provider_id=rows["provider_id"],
        checkpoint_json=_storyboard_checkpoint_json(
            "T-ce-paths-kept",
            provider_id=rows["provider_id"],
            endpoint_guard=endpoint_guard(rows["endpoint_keys"][0], overrides),
        ),
    )

    with capture_http() as router:
        submit = router.post("https://relay.test/v1/video/create")
        poll = router.get("https://relay.test/v2/fetch/job-old").mock(
            return_value=httpx.Response(
                200, json={"status": "completed", "video_url": "https://relay.test/files/job-old.mp4"}
            )
        )
        router.get("https://relay.test/files/job-old.mp4").mock(return_value=httpx.Response(200, content=b"resumed"))

        await _run_orphan_recovery(session_factory)

    assert submit.call_count == 0
    assert poll.call_count == 1
    assert (await _load_task_row(session_factory, "T-ce-paths-kept")).status == "succeeded"


#: Model resumed after shutdown: built-in agnes includes it; same-name custom models match the same catalog entry.
RETIRED_MODEL = "agnes-video-v2.0"
SHUTDOWN = date(2026, 11, 24)


@pytest.fixture
def retired_model(monkeypatch) -> None:
    """``RETIRED_MODEL`` shuts down at ``SHUTDOWN`` and today is that day: tasks were submitted earlier and resume after restart."""
    install_catalog(
        monkeypatch,
        with_entry_changes(
            factory_snapshot(),
            "agnes",
            RETIRED_MODEL,
            discontinued=False,
            shutdown_date=SHUTDOWN,
            successor="agnes/agnes-video-2.5",
        ),
    )
    set_catalog_today(monkeypatch, SHUTDOWN)


async def _configure_agnes(session_factory) -> None:
    async with session_factory() as session:
        await ConfigService(session).set_provider_config("agnes", "api_key", "sk-agnes")
        await session.commit()


async def _assert_new_submission_rejected(provider_id: str) -> None:
    """New submissions after model shutdown still fail discontinued: current-config resolution (queue execution removed enqueue identity) fails, as do calls locking the same payload identity without declaring resume."""
    identity = f"{provider_id}/{RETIRED_MODEL}"
    with pytest.raises(ModelDiscontinuedError) as excinfo:
        await resolve_generation_context(
            "demo",
            None,
            project={"video_provider_i2v": identity},
            video=VideoLaneRequest(generation_type="i2v", route="storyboard"),
        )
    assert excinfo.value.code == "model_discontinued"
    assert excinfo.value.params["shutdown_date"] == SHUTDOWN.isoformat()
    with pytest.raises(ModelDiscontinuedError):
        await resolve_generation_context(
            "demo", {"video_provider_i2v": identity}, project={}, video=VideoLaneRequest(generation_type="i2v")
        )


async def test_builtin_job_submitted_before_shutdown_still_resumes_after_it(
    chain_project: Path, session_factory, retired_model
):
    """Built-in providers resume pre-shutdown jobs with provider task IDs after shutdown/restart, polling/retrieving without resubmission; new submissions still fail discontinued."""
    await _configure_agnes(session_factory)
    await _seed_running_video_task(
        session_factory,
        task_id="T-agnes-retired",
        provider_id="agnes",
        checkpoint_json=_storyboard_checkpoint_json(
            "T-agnes-retired", provider_id="agnes", endpoint_guard=None, model_id=RETIRED_MODEL
        ),
    )

    with capture_http() as router:
        submit = router.post("https://apihub.agnes-ai.com/v1/videos")
        poll = router.get("https://apihub.agnes-ai.com/v1/videos/job-old").mock(
            return_value=httpx.Response(200, json={"status": "completed", "url": "https://cdn.agnes.test/job-old.mp4"})
        )
        download = router.get("https://cdn.agnes.test/job-old.mp4").mock(
            return_value=httpx.Response(200, content=b"resumed")
        )

        await _run_orphan_recovery(session_factory)

    assert submit.call_count == 0
    assert (poll.call_count, download.call_count) == (1, 1)
    row = await _load_task_row(session_factory, "T-agnes-retired")
    assert row.status == "succeeded", row.error_message
    paid_files = sorted((chain_project / "versions" / "videos").glob("E1S01_v*.mp4"))
    assert [path.read_bytes() for path in paid_files] == [b"resumed"]

    # Resume-created backends may be cached; new submissions still cannot bypass shutdown checks through cache.
    await _assert_new_submission_rejected("agnes")


async def test_custom_job_submitted_before_shutdown_still_resumes_after_it(
    chain_project: Path, session_factory, retired_model
):
    """Custom providers resume jobs submitted before their matched entry shutdown, polling/retrieving without resubmission; new submissions still fail discontinued."""
    rows = await _seed_custom_video_rows(session_factory, model_id=RETIRED_MODEL)
    await _seed_running_video_task(
        session_factory,
        task_id="T-ce-retired",
        provider_id=rows["provider_id"],
        checkpoint_json=_storyboard_checkpoint_json(
            "T-ce-retired",
            provider_id=rows["provider_id"],
            endpoint_guard=rows["endpoint_keys"][0],
            model_id=RETIRED_MODEL,
        ),
    )

    with capture_http() as router:
        submit = router.post("https://relay.test/v1/video/create")
        poll = router.get("https://relay.test/v1/video/fetch/job-old").mock(
            return_value=httpx.Response(
                200, json={"status": "completed", "video_url": "https://relay.test/files/job-old.mp4"}
            )
        )
        download = router.get("https://relay.test/files/job-old.mp4").mock(
            return_value=httpx.Response(200, content=b"resumed")
        )

        await _run_orphan_recovery(session_factory)

    assert submit.call_count == 0
    assert (poll.call_count, download.call_count) == (1, 1)
    row = await _load_task_row(session_factory, "T-ce-retired")
    assert row.status == "succeeded", row.error_message

    await _assert_new_submission_rejected(rows["provider_id"])


#: Pre-split Ark Seedance endpoint name frozen by tasks submitted before upgrade.
LEGACY_SEEDANCE_ENDPOINT = "ark-seedance"
SEEDANCE_MODEL = "doubao-seedance-2-0-260128"


async def _seed_relay_model(session_factory, *, endpoint: str, model_id: str) -> str:
    """Persist a custom provider using built-in ``endpoint`` to invoke ``model_id`` and return its provider ID."""
    async with session_factory() as session:
        provider = await CustomProviderRepository(session).create_provider(
            display_name="方舟中转",
            discovery_format="openai",
            base_url="https://ark-relay.test",
            api_key="sk-secret-key-1234",
            models=[
                {
                    "model_id": model_id,
                    "display_name": model_id,
                    "endpoint": endpoint,
                    "is_enabled": True,
                    "is_default": True,
                }
            ],
        )
        await session.commit()
        return make_provider_id(provider.id)


async def test_seedance_job_submitted_before_endpoint_split_resumes(chain_project: Path, session_factory):
    """Pre-upgrade ``ark-seedance`` tasks resume through protocol-equivalent backfilled ``ark-seedance-2`` and retrieve without resubmission."""
    provider_id = await _seed_relay_model(session_factory, endpoint="ark-seedance-2", model_id=SEEDANCE_MODEL)
    await _seed_running_video_task(
        session_factory,
        task_id="T-seedance-legacy",
        provider_id=provider_id,
        checkpoint_json=_storyboard_checkpoint_json(
            "T-seedance-legacy",
            provider_id=provider_id,
            endpoint_guard=LEGACY_SEEDANCE_ENDPOINT,
            model_id=SEEDANCE_MODEL,
        ),
    )

    with capture_http() as router:
        submit = router.post("https://ark-relay.test/api/v3/contents/generations/tasks")
        poll = router.get("https://ark-relay.test/api/v3/contents/generations/tasks/job-old").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "job-old",
                    "model": SEEDANCE_MODEL,
                    "status": "succeeded",
                    "content": {"video_url": "https://cdn.ark.test/job-old.mp4"},
                    "usage": {"completion_tokens": 10, "total_tokens": 10},
                    "created_at": 1,
                    "updated_at": 2,
                },
            )
        )
        download = router.get("https://cdn.ark.test/job-old.mp4").mock(
            return_value=httpx.Response(200, content=b"resumed")
        )

        await _run_orphan_recovery(session_factory)

    assert submit.call_count == 0
    assert (poll.call_count, download.call_count) == (1, 1)
    row = await _load_task_row(session_factory, "T-seedance-legacy")
    assert row.status == "succeeded", row.error_message
    paid_files = sorted((chain_project / "versions" / "videos").glob("E1S01_v*.mp4"))
    assert [path.read_bytes() for path in paid_files] == [b"resumed"]


async def test_seedance_job_rejected_after_switching_protocol(chain_project: Path, session_factory):
    """Pre-upgrade ``ark-seedance`` tasks fail before any request if their model changes to another protocol while in flight."""
    provider_id = await _seed_relay_model(session_factory, endpoint="openai-video", model_id=SEEDANCE_MODEL)
    await _seed_running_video_task(
        session_factory,
        task_id="T-seedance-switched",
        provider_id=provider_id,
        checkpoint_json=_storyboard_checkpoint_json(
            "T-seedance-switched",
            provider_id=provider_id,
            endpoint_guard=LEGACY_SEEDANCE_ENDPOINT,
            model_id=SEEDANCE_MODEL,
        ),
    )

    with capture_http() as router:
        ark = router.route(host="ark-relay.test")

        await _run_orphan_recovery(session_factory)

    assert ark.call_count == 0
    row = await _load_task_row(session_factory, "T-seedance-switched")
    assert row.status == "failed"
    assert row.error_message is not None
    assert "resume_endpoint_changed" in row.error_message


@pytest.mark.parametrize(
    "endpoint",
    ["kling-video-pro-silent", "kling-video-pro-audio", "kling-video-audio", "kling-video-silent"],
)
async def test_kling_job_submitted_before_split_resumes(chain_project: Path, session_factory, endpoint: str):
    """Old Kling tasks retrieve through protocol-equivalent successors with zero resubmissions."""
    model = "kling-v2-6"
    provider_id = await _seed_relay_model(session_factory, endpoint=endpoint, model_id=model)
    await _seed_running_video_task(
        session_factory,
        task_id="T-kling-legacy",
        provider_id=provider_id,
        checkpoint_json=_storyboard_checkpoint_json(
            "T-kling-legacy", provider_id=provider_id, endpoint_guard="kling-video", model_id=model
        ),
    )
    with capture_http() as router:
        submit = router.post("https://ark-relay.test/v1/videos/text2video")
        poll = router.get("https://ark-relay.test/v1/videos/text2video/job-old").mock(
            return_value=httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "task_status": "succeed",
                        "task_result": {"videos": [{"url": "https://cdn.kling.test/old.mp4"}]},
                    },
                },
            )
        )
        download = router.get("https://cdn.kling.test/old.mp4").mock(
            return_value=httpx.Response(200, content=b"resumed-kling")
        )
        await _run_orphan_recovery(session_factory)

    assert submit.call_count == 0
    assert (poll.call_count, download.call_count) == (1, 1)
    row = await _load_task_row(session_factory, "T-kling-legacy")
    assert row.status == "succeeded", row.error_message
    paid_files = sorted((chain_project / "versions" / "videos").glob("E1S01_v*.mp4"))
    assert [path.read_bytes() for path in paid_files] == [b"resumed-kling"]


async def test_old_kling_job_rejects_a_different_protocol(chain_project: Path, session_factory):
    provider_id = await _seed_relay_model(session_factory, endpoint="openai-video", model_id="kling-v2-6")
    await _seed_running_video_task(
        session_factory,
        task_id="T-kling-switched",
        provider_id=provider_id,
        checkpoint_json=_storyboard_checkpoint_json(
            "T-kling-switched", provider_id=provider_id, endpoint_guard="kling-video", model_id="kling-v2-6"
        ),
    )
    with capture_http() as router:
        outbound = router.route(host="ark-relay.test")
        await _run_orphan_recovery(session_factory)
    assert outbound.call_count == 0
    row = await _load_task_row(session_factory, "T-kling-switched")
    assert row.status == "failed"
    assert "resume_endpoint_changed" in (row.error_message or "")


@pytest.mark.parametrize("endpoint", ["dashscope-wan3-video", "openai-video"])
async def test_dashscope_legacy_job_resumes_only_on_successor(chain_project: Path, session_factory, endpoint: str):
    provider_id = await _seed_relay_model(session_factory, endpoint=endpoint, model_id="wan3.0-video")
    await _seed_running_video_task(
        session_factory,
        task_id="T-dashscope-legacy",
        provider_id=provider_id,
        checkpoint_json=_storyboard_checkpoint_json(
            "T-dashscope-legacy",
            provider_id=provider_id,
            endpoint_guard="dashscope-async-video",
            model_id="wan3.0-video",
        ),
    )
    with capture_http() as router:
        submit = router.post("https://ark-relay.test/api/v1/services/aigc/video-generation/video-synthesis")
        poll = router.get("https://ark-relay.test/api/v1/tasks/job-old").respond(
            200,
            json={
                "output": {"task_status": "SUCCEEDED", "video_url": "https://cdn.test/legacy.mp4"},
                "usage": {"duration": 5},
            },
        )
        download = router.get("https://cdn.test/legacy.mp4").respond(200, content=b"resumed")
        await _run_orphan_recovery(session_factory)
    row = await _load_task_row(session_factory, "T-dashscope-legacy")
    assert submit.call_count == 0
    if endpoint == "dashscope-wan3-video":
        assert row.status == "succeeded", row.error_message
        assert (poll.call_count, download.call_count) == (1, 1)
        paid_files = sorted((chain_project / "versions" / "videos").glob("E1S01_v*.mp4"))
        assert [path.read_bytes() for path in paid_files] == [b"resumed"]
    else:
        assert row.status == "failed"
        assert row.error_message
        assert "resume_endpoint_changed" in row.error_message
        assert len(router.calls) == 0
