"""Tests for execute_reference_video_task."""

from __future__ import annotations

import asyncio
import json
import threading
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock

import pytest

from lib.generation.video_request_facts import VideoRequestFactsFailure
from lib.i18n import SUPPORTED_LOCALES, _, render_message
from lib.script.reference_video.request_projection import resolve_reference_assets
from tests.factories import make_display_names, make_video_request_facts, wav_bytes
from tests.fakes import (
    FakeConfigResolver,
    use_generation_context,
    use_project_manager,
    use_video_thumbnail_extractor,
    write_backend_output,
)
from tests.integration.server.services.tasks.reference_video_tasks_support import (
    _TINY_PNG,
    register_asset_sheet,
    write_project,
)


def _wire_locked_script(fake_pm: MagicMock) -> None:
    """Make fake_pm.locked_script yield the actual on-disk script dict.

    Finalize locates the unit for asset writeback and raises KeyError if absent; a bare MagicMock script.get("video_units") is not a list and fails immediately."""
    proj_dir = fake_pm.get_project_path.return_value

    @contextmanager
    def _locked(_name, script_file, *, validate=True):
        yield json.loads((proj_dir / script_file).read_text(encoding="utf-8"))

    fake_pm.locked_script.side_effect = _locked


def _wire_context(
    monkeypatch: pytest.MonkeyPatch,
    rvt,
    fake_generator,
    *,
    backend_name: str,
    backend_model: str,
    registry_provider_id: str | None = None,
    resolution: str | None = None,
    max_refs: int | None = None,
    supported_durations: tuple[int, ...] = (3,),
    allowed_durations: tuple[int, ...] | None = None,
    duration_endpoint_fixed: bool = False,
    voice_consistency: str = "soft",
    max_reference_audio_count: int = 0,
    reference_audio_per_image: bool = False,
    requested_generate_audio: bool = True,
    generate_audio: bool = False,
    text_to_video: bool = True,
    has_audio_track: bool = True,
    audio_switch_controllable: bool = True,
    request_facts_failure: VideoRequestFactsFailure | None = None,
    seen_lane_requests: list[dict[str, Any]] | None = None,
) -> None:
    """Wrap fake generator and video-lane values into GenerationContext, replacing only resolve_generation_context.

    Executor never accesses MediaGenerator private attributes or rebuilds provider identity. The video lane supplies provider/backend identities; read capabilities and resolution only from its request facts. Construct facts directly for the declared bucket (``allowed_durations`` defaults to the full set); evaluation itself is covered by tests/integration/lib/generation/test_video_request_facts.py. Here test downstream clamping, guards and forwarding. If ``request_facts_failure`` is supplied, carry that failure in the lane.

    ``registry_provider_id`` defaults to ``backend_name``, as for most providers. Family aliases such as ark-agent-plan reusing Ark backend differ; distinguish them explicitly to cover registry lookup."""
    from lib.artifacts.version_manager import PaidVersionCommit
    from lib.config.resolver import ProviderModel
    from server.services.tasks.generation_context import GenerationContext, VideoLaneResult

    class _SelectedArtifactCommitter:
        def __init__(self, **_kwargs):
            self.outcome = PaidVersionCommit(version=1, selected=True)
            self.selection_error = None

        async def prepare_selection(self, *_args, **_kwargs):
            return None

        async def release_admission_guard(self):
            return None

        def __call__(self, *_args, **_kwargs):
            return self.outcome

    monkeypatch.setattr(rvt, "VideoArtifactCommitter", _SelectedArtifactCommitter)
    if isinstance(fake_generator.versions, MagicMock):
        fake_generator.versions.get_current_version.return_value = 0

    provider_id = registry_provider_id or backend_name

    def _lane(video_request) -> VideoLaneResult:
        request_facts = request_facts_failure or make_video_request_facts(
            route="reference_video",
            generation_type=video_request.generation_type,
            provider_id=provider_id,
            model_id=backend_model,
            resolution=resolution,
            supported_durations=supported_durations,
            allowed_durations=supported_durations if allowed_durations is None else allowed_durations,
            duration_endpoint_fixed=duration_endpoint_fixed,
            max_reference_images=max_refs,
            voice_consistency=voice_consistency,
            max_reference_audio_count=max_reference_audio_count,
            reference_audio_per_image=reference_audio_per_image,
            requested_generate_audio=requested_generate_audio,
            generate_audio=generate_audio,
            has_audio_track=has_audio_track,
            audio_switch_controllable=audio_switch_controllable,
            text_to_video=text_to_video,
        )
        return VideoLaneResult(
            provider_model=ProviderModel(provider_id=provider_id, model_id=backend_model),
            backend_name=backend_name,
            backend_model=backend_model,
            resolution=resolution,
            request_facts=request_facts,
            requested_generate_audio_fallback=requested_generate_audio,
        )

    async def _fake_resolve(*_args, **kwargs):
        if seen_lane_requests is not None:
            seen_lane_requests.append(
                {
                    "image": kwargs.get("image"),
                    "video": kwargs.get("video"),
                    "audio": kwargs.get("audio"),
                }
            )
        return GenerationContext(generator=fake_generator, video_lane=_lane(kwargs["video"]))

    use_generation_context(monkeypatch, _fake_resolve)


@pytest.mark.asyncio
async def test_execute_reference_video_task_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    proj_dir = write_project(tmp_path)

    # Patch project_manager helpers
    from server.services.tasks import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir

    def fake_load_script(_project_name, _filename):
        return json.loads((proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8"))

    fake_pm.load_script.side_effect = fake_load_script
    _wire_locked_script(fake_pm)
    use_project_manager(monkeypatch, fake_pm)

    # Mock generator.generate_video_async: create a fake video file.
    async def _fake_generate_video_async(**kwargs):
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        await write_backend_output(out, b"\x00\x00\x00 ftypmp42")
        # (output_path, version, video_ref, video_uri)
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-17T10:00:00"}]}
    _wire_context(monkeypatch, rvt, fake_generator, backend_name="ark", backend_model="doubao-seedance-2-0-260128")

    # Patch thumbnail extractor → success
    async def _fake_extract(*_a, **_k):
        return True

    use_video_thumbnail_extractor(monkeypatch, _fake_extract)

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
async def test_execute_reference_video_task_passes_planned_duration_when_endpoint_fixed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Endpoint-fixed-duration models remain admitted at execution: do not reject absent tier declarations; pass planned seconds unchanged.

    Use ``task_id`` for formal submission so artifact-currency facts' tier set is checked too."""

    proj_dir = write_project(tmp_path)

    from lib.script.reference_video.execution_checkpoint import ReferenceSubmissionCheckpoint
    from server.services.tasks import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda _project_name, _filename: json.loads(
        (proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")
    )
    _wire_locked_script(fake_pm)
    use_project_manager(monkeypatch, fake_pm)

    async def _fake_generate_video_async(**kwargs):
        await kwargs["before_submit"]()
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        await write_backend_output(out, b"\x00\x00\x00 ftypmp42")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-17T10:00:00"}]}
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="ark",
        backend_model="doubao-seedance-2-0-260128",
        supported_durations=(),
        duration_endpoint_fixed=True,
    )

    async def _fake_extract(*_a, **_k):
        return True

    use_video_thumbnail_extractor(monkeypatch, _fake_extract)
    persisted: dict[str, Any] = {}
    fake_queue = MagicMock()
    fake_queue.persist_execution_checkpoint = AsyncMock(
        side_effect=lambda task_id, raw, provider_id: persisted.update(raw=raw)
    )
    monkeypatch.setattr(rvt, "get_generation_queue", lambda: fake_queue)

    result = await rvt.execute_reference_video_task(
        "demo",
        "E1U1",
        {"script_file": "scripts/episode_1.json"},
        user_id="u1",
        task_id="task-endpoint-fixed",
    )

    assert result["resource_id"] == "E1U1"
    assert fake_generator.generate_video_async.await_args.kwargs["duration_seconds"] == 3
    # Currency facts require nonempty tiers containing the paid tier; endpoint-fixed duration has only the seconds passed through that time.
    checkpoint = ReferenceSubmissionCheckpoint.from_json(persisted["raw"])
    assert checkpoint.duration_seconds == 3
    assert checkpoint.artifact_duration_tiers == (3,)


@pytest.mark.asyncio
async def test_execute_reference_video_task_rejects_changed_claim_provider_before_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """If execution provider differs from the occupied slot, return to worker for reclaim before any generation call."""

    from lib.generation.generation_queue import DispatchProviderChanged
    from server.services.tasks import reference_video_tasks as rvt

    proj_dir = write_project(tmp_path)
    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda _project_name, _filename: json.loads(
        (proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")
    )
    use_project_manager(monkeypatch, fake_pm)

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock()
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="minimax",
        backend_model="S2V-01",
    )

    with pytest.raises(DispatchProviderChanged) as exc_info:
        await rvt.execute_reference_video_task(
            "demo",
            "E1U1",
            {"script_file": "scripts/episode_1.json"},
            user_id="u1",
            claimed_provider_id="ark",
        )

    assert exc_info.value.claimed_provider_id == "ark"
    assert exc_info.value.actual_provider_id == "minimax"
    fake_generator.generate_video_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_reference_video_task_blocks_a_dirty_text_before_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from server.services.tasks import reference_video_tasks as rvt

    proj_dir = write_project(tmp_path)
    script_path = proj_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["video_units"][0]["text"] = 42
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda _project_name, _filename: json.loads(
        script_path.read_text(encoding="utf-8")
    )
    use_project_manager(monkeypatch, fake_pm)

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock()
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="ark",
        backend_model="doubao-seedance-2-0-260128",
    )

    with pytest.raises(ValueError, match="needs replanning"):
        await rvt.execute_reference_video_task(
            "demo",
            "E1U1",
            {"script_file": "scripts/episode_1.json"},
            user_id="u1",
        )

    fake_generator.generate_video_async.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(("strip_mentions", "expected_generation_type"), [(False, "r2v"), (True, "i2v")])
async def test_execute_reference_video_task_bucket_follows_resolved_references(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, strip_mentions: bool, expected_generation_type: str
):
    """Bucket execution by actual resolved references: images → r2v, imageless units → i2v.

    This downgrade lets imageless units generate even when configured r2v buckets require images (DashScope R2V / MiniMax S2V). Always declaring r2v would fail these units with video_reference_images_required."""
    proj_dir = write_project(tmp_path)
    if strip_mentions:
        script_path = proj_dir / "scripts" / "episode_1.json"
        script = json.loads(script_path.read_text(encoding="utf-8"))
        script["video_units"][0]["text"] = "空镜头，推门而入"
        script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    from server.services.tasks import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir

    def fake_load_script(_project_name, _filename):
        return json.loads((proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8"))

    fake_pm.load_script.side_effect = fake_load_script
    _wire_locked_script(fake_pm)
    use_project_manager(monkeypatch, fake_pm)

    async def _fake_generate_video_async(**kwargs):
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        await write_backend_output(out, b"\x00\x00\x00 ftypmp42")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-17T10:00:00"}]}
    _wire_context(monkeypatch, rvt, fake_generator, backend_name="ark", backend_model="doubao-seedance-2-0-260128")

    captured: dict = {}
    inner = rvt.resolve_generation_context

    async def _capture(*args, **kwargs):
        captured["video"] = kwargs.get("video")
        captured["payload"] = args[1]
        return await inner(*args, **kwargs)

    use_generation_context(monkeypatch, _capture)

    async def _fake_extract(*_a, **_k):
        return True

    use_video_thumbnail_extractor(monkeypatch, _fake_extract)

    await rvt.execute_reference_video_task(
        "demo",
        "E1U1",
        {
            "script_file": "scripts/episode_1.json",
            "video_provider_i2v": "legacy/i2v-model",
            "video_provider_r2v": "legacy/r2v-model",
        },
        user_id="u1",
    )

    assert captured["video"].generation_type == expected_generation_type
    assert "video_provider_i2v" not in captured["payload"]
    assert "video_provider_r2v" not in captured["payload"]


@pytest.mark.asyncio
async def test_execute_reference_video_task_rechecks_text_to_video_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    proj_dir = write_project(tmp_path)
    script_path = proj_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["video_units"][0]["text"] = "空镜头，推门而入"
    script["video_units"][0]["duration_seconds"] = 6
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    from lib.script.reference_video.request_projection import ReferenceProjectionBlockedError
    from server.services.tasks import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda _name, _filename: json.loads(script_path.read_text(encoding="utf-8"))
    _wire_locked_script(fake_pm)
    use_project_manager(monkeypatch, fake_pm)

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock()
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="minimax",
        backend_model="MiniMax-Hailuo-2.3-Fast",
        supported_durations=(6,),
        text_to_video=False,
    )

    with pytest.raises(ReferenceProjectionBlockedError) as exc:
        await rvt.execute_reference_video_task(
            "demo",
            "E1U1",
            {"script_file": "scripts/episode_1.json"},
            user_id="u1",
        )

    assert exc.value.code == "video_capability_missing_t2v"
    fake_generator.generate_video_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_reference_video_task_sends_reference_audio_in_prompt_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Class A tracer bullet: request reference_audio_files order exactly matches first-section @音频N declarations."""
    proj_dir = write_project(tmp_path)

    project = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    project["characters"]["张三"]["voice_style"] = "低沉沙哑的男声"
    project["characters"]["张三"]["reference_audio"] = "characters/refs_audio/张三.wav"
    project["characters"]["李四"] = {
        "description": "x",
        "character_sheet": "characters/李四.png",
        "voice_style": "清亮少女音",
        "reference_audio": "characters/refs_audio/李四.mp3",
    }
    (proj_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    register_asset_sheet(proj_dir, "character", "李四", "characters/李四.png")
    refs_audio = proj_dir / "characters" / "refs_audio"
    refs_audio.mkdir(parents=True)
    (refs_audio / "张三.wav").write_bytes(b"RIFF")
    (refs_audio / "李四.mp3").write_bytes(b"ID3")

    script_path = proj_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["video_units"][0]["text"] = "@[张三] 推门而入。\n@[李四]：{你终于来了。}\n@[张三]：{今晚的酒，我请。}"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    from server.services.tasks import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = project
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda _n, _f: json.loads(script_path.read_text(encoding="utf-8"))
    _wire_locked_script(fake_pm)
    use_project_manager(monkeypatch, fake_pm)

    captured: dict = {}

    async def _fake_generate_video_async(**kwargs):
        captured.update(kwargs)
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        await write_backend_output(out, b"\x00\x00\x00 ftypmp42")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-17T10:00:00"}]}
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="ark",
        backend_model="doubao-seedance-2-0-260128",
        voice_consistency="native",
        max_reference_audio_count=3,
    )

    async def _fake_extract(*_a, **_k):
        return True

    use_video_thumbnail_extractor(monkeypatch, _fake_extract)

    await rvt.execute_reference_video_task("demo", "E1U1", {"script_file": "scripts/episode_1.json"}, user_id="u1")

    prompt = captured["prompt"]
    # Speaker first-occurrence order = audio numbering = request-field order (李四 speaks first although 张三 appears first).
    assert (
        "<李四>: Dialogue voice reference @音频1; use only timbre, speech rate, and emotion; the body text is authoritative for dialogue; Voice characteristics: 清亮少女音."
        in prompt
    )
    assert (
        "<张三>: Dialogue voice reference @音频2; use only timbre, speech rate, and emotion; the body text is authoritative for dialogue; Voice characteristics: 低沉沙哑的男声."
        in prompt
    )
    assert [p.name for p in captured["reference_audio_files"]] == ["李四.mp3", "张三.wav"]
    # Speaker slots derive no reference image: 李四 has no @图片N binding.
    assert "<张三>@图片1。" in prompt
    assert "<李四>@图片" not in prompt

    frozen_speech_paths: dict[str, Path] = {}
    submitted_audio_paths: list[Path] = []
    real_freeze_speech = rvt.freeze_video_speech_facts

    def _capture_frozen_speech(*args, **kwargs):
        frozen_speech_paths.update(kwargs.get("reference_audio_paths") or {})
        return real_freeze_speech(*args, **kwargs)

    async def _stop_after_currency_freeze(**kwargs):
        submitted_audio_paths.extend(kwargs["reference_audio_files"] or [])
        raise RuntimeError("stop after frozen speech evidence")

    monkeypatch.setattr(rvt, "freeze_video_speech_facts", _capture_frozen_speech)
    fake_generator.generate_video_async = AsyncMock(side_effect=_stop_after_currency_freeze)
    with pytest.raises(RuntimeError, match="stop after frozen speech evidence"):
        await rvt.execute_reference_video_task(
            "demo",
            "E1U1",
            {"script_file": "episode_1.json"},
            user_id="u1",
            task_id="task-audio-evidence",
        )

    assert list(frozen_speech_paths.values()) == submitted_audio_paths
    assert all(
        ".arcreel/tasks/task-audio-evidence/provider_media/" in path.as_posix() for path in submitted_audio_paths
    )


@pytest.mark.asyncio
async def test_execute_reference_video_task_omits_reference_audio_when_episode_is_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Silent video sends no reference-audio payload even with character voice profiles and a class A model. Dialogue still goes to providers for lip-sync reference; return a silence notice with results."""
    proj_dir = write_project(tmp_path)

    project = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    project["characters"]["张三"]["voice_style"] = "低沉沙哑的男声"
    project["characters"]["张三"]["reference_audio"] = "characters/refs_audio/张三.wav"
    (proj_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    refs_audio = proj_dir / "characters" / "refs_audio"
    refs_audio.mkdir(parents=True)
    (refs_audio / "张三.wav").write_bytes(b"RIFF")

    script_path = proj_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["video_units"][0]["text"] = "@[张三] 推门而入。\n@[张三]：{今晚的酒，我请。}"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    from server.services.tasks import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = project
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda _n, _f: json.loads(script_path.read_text(encoding="utf-8"))
    _wire_locked_script(fake_pm)
    use_project_manager(monkeypatch, fake_pm)

    captured: dict = {}

    async def _fake_generate_video_async(**kwargs):
        captured.update(kwargs)
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        await write_backend_output(out, b"\x00\x00\x00 ftypmp42")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-17T10:00:00"}]}
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="ark",
        backend_model="doubao-seedance-2-0-260128",
        voice_consistency="native",
        max_reference_audio_count=3,
        requested_generate_audio=False,
    )

    async def _fake_extract(*_a, **_k):
        return True

    use_video_thumbnail_extractor(monkeypatch, _fake_extract)

    result = await rvt.execute_reference_video_task(
        "demo", "E1U1", {"script_file": "scripts/episode_1.json"}, user_id="u1"
    )

    assert captured["reference_audio_files"] is None
    assert captured["reference_audio_targets"] is None
    prompt = captured["prompt"]
    assert "@音频" not in prompt
    # Dialogue and image bindings remain; only voice-reference lines disappear.
    assert "<张三> says {今晚的酒，我请。}" in prompt
    assert "<张三>@图片1。" in prompt
    assert {"key": "ref_warn_silent_episode", "params": {}} in result["warnings"]


@pytest.mark.asyncio
async def test_execute_reference_video_task_blocks_on_request_facts_failure_with_its_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Unresolvable lane video-request facts block execution before provider submission with original problem code and parameter keys."""

    from lib.script.reference_video.request_projection import ReferenceProjectionBlockedError

    proj_dir = write_project(tmp_path)
    from server.services.tasks import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda _n, _f: json.loads(
        (proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")
    )
    use_project_manager(monkeypatch, fake_pm)

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock()
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="gemini-aistudio",
        backend_model="veo-3.1-generate-preview",
        request_facts_failure=VideoRequestFactsFailure(
            "reference_supported_durations_incompatible",
            (
                ("provider", "gemini-aistudio"),
                ("model", "veo-3.1-generate-preview"),
                ("resolution", "1080p"),
                ("capability", "r2v"),
            ),
        ),
    )

    with pytest.raises(ReferenceProjectionBlockedError) as exc_info:
        await rvt.execute_reference_video_task(
            "demo",
            "E1U1",
            {"script_file": "scripts/episode_1.json"},
            user_id="u1",
        )

    assert exc_info.value.code == "reference_supported_durations_incompatible"
    assert exc_info.value.params == {
        "capability": "r2v",
        "provider": "gemini-aistudio",
        "model": "veo-3.1-generate-preview",
        "resolution": "1080p",
    }
    fake_generator.generate_video_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_reference_video_task_rechecks_audio_switch_for_latest_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Switching to an always-audible model while queued makes worker reject silent requests using current capabilities."""

    proj_dir = write_project(tmp_path)
    from server.services.tasks import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda _n, _f: json.loads(
        (proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")
    )
    use_project_manager(monkeypatch, fake_pm)

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock()
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="dashscope",
        backend_model="wan2.7-r2v",
        supported_durations=(3, 4, 5),
        voice_consistency="native",
        requested_generate_audio=False,
        generate_audio=True,
        has_audio_track=True,
        audio_switch_controllable=False,
    )
    fake_queue = MagicMock()
    fake_queue.persist_execution_checkpoint = AsyncMock()
    monkeypatch.setattr(rvt, "get_generation_queue", lambda: fake_queue)

    with pytest.raises(ValueError, match="video_audio_switch_not_supported"):
        await rvt.execute_reference_video_task(
            "demo",
            "E1U1",
            {"script_file": "scripts/episode_1.json"},
            user_id="u1",
            task_id="task-1",
        )
    fake_generator.generate_video_async.assert_not_awaited()
    fake_queue.persist_execution_checkpoint.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_reference_video_task_aligns_reference_audio_targets_for_per_image_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """For backends binding audio clips to images (such as wan2.7-r2v), align reference_audio_targets by name to correct image indices. Purely offscreen 李四 degrades without binding; never assume equal list order."""
    proj_dir = write_project(tmp_path)

    project = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    project["characters"]["张三"]["voice_style"] = "低沉沙哑的男声"
    project["characters"]["张三"]["reference_audio"] = "characters/refs_audio/张三.wav"
    project["characters"]["李四"] = {
        "description": "x",
        "character_sheet": "characters/李四.png",
        "voice_style": "清亮少女音",
        "reference_audio": "characters/refs_audio/李四.mp3",
    }
    (proj_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    register_asset_sheet(proj_dir, "character", "李四", "characters/李四.png")
    refs_audio = proj_dir / "characters" / "refs_audio"
    refs_audio.mkdir(parents=True)
    (refs_audio / "张三.wav").write_bytes(b"RIFF")
    (refs_audio / "李四.mp3").write_bytes(b"ID3")

    script_path = proj_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    # Scene precedes 张三 in body mentions: image order is 酒馆 at index 0, then 张三 at index 1;
    # audio order 李四→张三 differs, so positional alignment would attach audio to the tavern image.
    script["video_units"][0]["text"] = (
        "@[酒馆] 内景。@[张三] 推门而入。\n@[李四]：{你终于来了。}\n@[张三]：{今晚的酒，我请。}"
    )
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    from server.services.tasks import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = project
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda _n, _f: json.loads(script_path.read_text(encoding="utf-8"))
    _wire_locked_script(fake_pm)
    use_project_manager(monkeypatch, fake_pm)

    captured: dict = {}

    async def _fake_generate_video_async(**kwargs):
        captured.update(kwargs)
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        await write_backend_output(out, b"\x00\x00\x00 ftypmp42")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-17T10:00:00"}]}
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="dashscope",
        backend_model="wan2.7-r2v",
        voice_consistency="native",
        max_reference_audio_count=5,
        reference_audio_per_image=True,
    )

    async def _fake_extract(*_a, **_k):
        return True

    use_video_thumbnail_extractor(monkeypatch, _fake_extract)

    await rvt.execute_reference_video_task("demo", "E1U1", {"script_file": "scripts/episode_1.json"}, user_id="u1")

    # 李四 is purely offscreen with no image; degrade without binding, leaving only 张三 audio.
    assert [p.name for p in captured["reference_audio_files"]] == ["张三.wav"]
    # Body first mentions determine reference order: 酒馆 index 0, 张三 index 1; bind audio by name to 张三's image.
    assert captured["reference_audio_targets"] == [1]


@pytest.mark.asyncio
async def test_execute_reference_video_task_omits_audio_field_for_soft_tier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Class B has no audio fields but retains first-section voice_style declarations."""
    proj_dir = write_project(tmp_path)
    project = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    project["characters"]["张三"]["voice_style"] = "低沉沙哑的男声"
    project["characters"]["张三"]["reference_audio"] = "characters/refs_audio/张三.wav"
    (proj_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    refs_audio = proj_dir / "characters" / "refs_audio"
    refs_audio.mkdir(parents=True)
    (refs_audio / "张三.wav").write_bytes(b"RIFF")

    script_path = proj_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["video_units"][0]["text"] = "@[张三] 推门。\n@[张三]：{我回来了。}"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    from server.services.tasks import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = project
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda _n, _f: json.loads(script_path.read_text(encoding="utf-8"))
    _wire_locked_script(fake_pm)
    use_project_manager(monkeypatch, fake_pm)

    captured: dict = {}

    async def _fake_generate_video_async(**kwargs):
        captured.update(kwargs)
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        await write_backend_output(out, b"\x00\x00\x00 ftypmp42")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-17T10:00:00"}]}
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="ark",
        backend_model="doubao-seedance-1-5",
        voice_consistency="soft",
    )

    async def _fake_extract(*_a, **_k):
        return True

    use_video_thumbnail_extractor(monkeypatch, _fake_extract)

    await rvt.execute_reference_video_task("demo", "E1U1", {"script_file": "scripts/episode_1.json"}, user_id="u1")

    assert captured["reference_audio_files"] is None
    assert "<张三>: Voice characteristics: 低沉沙哑的男声." in captured["prompt"]
    assert "@音频" not in captured["prompt"]


@pytest.mark.asyncio
async def test_execute_reference_video_task_surfaces_backend_warnings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Backend execution notices share result.warnings with rendering notices."""
    proj_dir = write_project(tmp_path)
    project = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    script_path = proj_dir / "scripts" / "episode_1.json"

    from server.services.tasks import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = project
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda _n, _f: json.loads(script_path.read_text(encoding="utf-8"))
    _wire_locked_script(fake_pm)
    use_project_manager(monkeypatch, fake_pm)

    warning = {"key": "comfyui_multiple_outputs", "params": {"count": 2, "filename": "final.mp4"}}

    async def _fake_generate_video_async(**kwargs):
        kwargs["warnings"].append(warning)
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        await write_backend_output(out, b"\x00\x00\x00 ftypmp42")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-17T10:00:00"}]}
    _wire_context(monkeypatch, rvt, fake_generator, backend_name="ark", backend_model="doubao-seedance-2-0-260128")

    async def _fake_extract(*_a, **_k):
        return True

    use_video_thumbnail_extractor(monkeypatch, _fake_extract)

    result = await rvt.execute_reference_video_task(
        "demo", "E1U1", {"script_file": "scripts/episode_1.json"}, user_id="u1"
    )

    assert warning in result["warnings"]


@pytest.mark.asyncio
async def test_execute_reference_video_task_surfaces_render_warnings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Render-time downgrade warnings use existing result.warnings (over-limit truncation)."""
    proj_dir = write_project(tmp_path)
    project = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    project["characters"]["张三"]["reference_audio"] = "characters/refs_audio/张三.wav"
    project["characters"]["李四"] = {
        "description": "x",
        "character_sheet": "characters/李四.png",
        "reference_audio": "characters/refs_audio/李四.mp3",
    }
    (proj_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    register_asset_sheet(proj_dir, "character", "李四", "characters/李四.png")
    refs_audio = proj_dir / "characters" / "refs_audio"
    refs_audio.mkdir(parents=True)
    (refs_audio / "张三.wav").write_bytes(b"RIFF")
    (refs_audio / "李四.mp3").write_bytes(b"ID3")

    script_path = proj_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["video_units"][0]["text"] = "@[张三] 推门。\n@[张三]：{我回来了。}\n@[李四]：{你好。}"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    from server.services.tasks import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = project
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda _n, _f: json.loads(script_path.read_text(encoding="utf-8"))
    _wire_locked_script(fake_pm)
    use_project_manager(monkeypatch, fake_pm)

    async def _fake_generate_video_async(**kwargs):
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        await write_backend_output(out, b"\x00\x00\x00 ftypmp42")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-17T10:00:00"}]}
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="ark",
        backend_model="doubao-seedance-2-0-260128",
        voice_consistency="native",
        max_reference_audio_count=1,
    )

    async def _fake_extract(*_a, **_k):
        return True

    use_video_thumbnail_extractor(monkeypatch, _fake_extract)

    result = await rvt.execute_reference_video_task(
        "demo", "E1U1", {"script_file": "scripts/episode_1.json"}, user_id="u1"
    )
    assert {"key": "ref_warn_reference_audio_overflow", "params": {"limit": 1, "name": "李四"}} in result["warnings"]


@pytest.mark.asyncio
async def test_execute_reference_video_task_reports_reference_image_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """One-image reference limits truncate to one and return a single-reference notice with provider and model."""
    proj_dir = write_project(tmp_path)
    project = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))

    from server.services.tasks import reference_video_tasks as rvt

    script_path = proj_dir / "scripts" / "episode_1.json"
    fake_pm = MagicMock()
    fake_pm.load_project.return_value = project
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda _n, _f: json.loads(script_path.read_text(encoding="utf-8"))
    _wire_locked_script(fake_pm)
    use_project_manager(monkeypatch, fake_pm)

    captured: dict[str, Any] = {}

    async def _fake_generate_video_async(**kwargs):
        captured["reference_images"] = kwargs.get("reference_images")
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        await write_backend_output(out, b"\x00\x00\x00 ftypmp42")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-17T10:00:00"}]}
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="custom-7",
        backend_model="shared-model",
        max_refs=1,
    )

    async def _fake_extract(*_a, **_k):
        return True

    use_video_thumbnail_extractor(monkeypatch, _fake_extract)

    result = await rvt.execute_reference_video_task(
        "demo", "E1U1", {"script_file": "scripts/episode_1.json"}, user_id="u1"
    )

    assert len(captured["reference_images"]) == 1
    assert {
        "key": "ref_single_ref_only",
        "params": {"provider": "custom-7", "model": "shared-model"},
    } in result["warnings"]
    # Warnings include provider identity so UI finds the correct name when another provider uses the same upstream model ID differently.
    (warning,) = [warning for warning in result["warnings"] if warning["key"] == "ref_single_ref_only"]
    models = {("custom-7", "shared-model"): "甲网关模型", ("custom-8", "shared-model"): "乙网关模型"}
    for locale in SUPPORTED_LOCALES:
        names = make_display_names(locale, providers={"custom-7": "我的网关"}, models=models)
        rendered = render_message(
            warning["key"],
            warning["params"],
            lambda key, locale=locale, **params: _(key, locale=locale, **params),
            names,
        )
        assert "custom-7" not in rendered
        assert "shared-model" not in rendered
        assert "甲网关模型" in rendered


@pytest.mark.asyncio
async def test_execute_reference_video_task_clears_stale_video_uri_and_thumbnail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """If regeneration returns no video_uri and thumbnail extraction fails, clear old video_uri / video_thumbnail; never retain stale URIs or deleted-file pointers."""
    proj_dir = write_project(tmp_path)

    # Prepare old artifacts from the previous successful generation.
    script_path = proj_dir / "scripts" / "episode_1.json"
    script_data = json.loads(script_path.read_text(encoding="utf-8"))
    ga = script_data["video_units"][0]["generated_assets"]
    ga["video_uri"] = "https://old/expired.mp4"
    ga["video_thumbnail"] = "reference_videos/thumbnails/E1U1.jpg"
    script_path.write_text(json.dumps(script_data, ensure_ascii=False), encoding="utf-8")

    from server.services.tasks import reference_video_tasks as rvt

    # Real locked_script contextmanager writes back live_script for assertions.
    live_script = json.loads(script_path.read_text(encoding="utf-8"))

    @contextmanager
    def _locked_script(_name, _file, *, validate=True):
        yield live_script

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(script_path.read_text(encoding="utf-8"))
    fake_pm.locked_script.side_effect = _locked_script
    use_project_manager(monkeypatch, fake_pm)

    # New backend returns no video_uri (fourth element None).
    async def _fake_generate_video_async(**kwargs):
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        await write_backend_output(out, b"\x00\x00\x00 ftypmp42")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-17T10:00:00"}]}
    _wire_context(monkeypatch, rvt, fake_generator, backend_name="ark", backend_model="doubao-seedance-2-0-260128")

    # Thumbnail extraction fails, yielding thumb_rel=None.
    async def _fake_extract(*_a, **_k):
        return False

    use_video_thumbnail_extractor(monkeypatch, _fake_extract)

    await rvt.execute_reference_video_task(
        "demo",
        "E1U1",
        {"script_file": "scripts/episode_1.json"},
        user_id="u1",
    )

    ga_after = live_script["video_units"][0]["generated_assets"]
    assert "video_uri" not in ga_after
    assert "video_thumbnail" not in ga_after
    # Normal artifacts still write correctly.
    assert ga_after["video_clip"] == "reference_videos/E1U1.mp4"
    assert ga_after["status"] == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("resolution", ["720p", None])
async def test_execute_reference_video_task_sends_the_request_facts_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resolution: str | None,
):
    """Forward request resolution from video-request facts unchanged; omit when unset without supplying fallback tiers."""
    proj_dir = write_project(tmp_path)

    from server.services.tasks import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(
        (proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")
    )
    _wire_locked_script(fake_pm)
    use_project_manager(monkeypatch, fake_pm)

    captured: dict = {}

    async def _fake_generate_video_async(**kwargs):
        captured.update(kwargs)
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        await write_backend_output(out, b"\x00\x00\x00 ftypmp42")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-21T22:00:00"}]}
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="grok",
        backend_model="grok-imagine-video",
        resolution=resolution,
    )

    async def _fake_extract(*_a, **_k):
        return True

    use_video_thumbnail_extractor(monkeypatch, _fake_extract)

    await rvt.execute_reference_video_task(
        "demo",
        "E1U1",
        {"script_file": "scripts/episode_1.json"},
        user_id="u1",
    )

    assert "resolution" in captured
    assert captured["resolution"] == resolution


@pytest.mark.asyncio
async def test_execute_reference_video_task_picks_the_tier_from_narrowed_request_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Select only narrowed request-fact tiers: Veo 3.1 with references allows eight seconds only, so three-second scripts never select unrestricted four-second tiers."""
    proj_dir = write_project(tmp_path)

    from server.services.tasks import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(
        (proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")
    )
    _wire_locked_script(fake_pm)
    use_project_manager(monkeypatch, fake_pm)

    captured: dict = {}

    async def _fake_generate_video_async(**kwargs):
        captured.update(kwargs)
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        await write_backend_output(out, b"\x00\x00\x00 ftypmp42")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-21T22:00:00"}]}
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="ark-agent-plan",
        registry_provider_id="gemini-aistudio",
        backend_model="veo-3.1-generate-preview",
        supported_durations=(4, 6, 8),
        allowed_durations=(8,),
    )

    async def _fake_extract(*_a, **_k):
        return True

    use_video_thumbnail_extractor(monkeypatch, _fake_extract)

    await rvt.execute_reference_video_task(
        "demo",
        "E1U1",
        {"script_file": "scripts/episode_1.json"},
        user_id="u1",
    )

    assert captured.get("duration_seconds") == 8


@pytest.mark.asyncio
async def test_execute_reference_video_task_missing_reference_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    proj_dir = write_project(tmp_path)
    (proj_dir / "characters" / "张三.png").unlink()

    from server.services.tasks import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(
        (proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")
    )
    _wire_locked_script(fake_pm)
    use_project_manager(monkeypatch, fake_pm)
    _wire_context(
        monkeypatch,
        rvt,
        MagicMock(),
        backend_name="ark",
        backend_model="doubao-seedance-2-0-260128",
        supported_durations=(3,),
    )

    from lib.script.reference_video.request_projection import ReferenceProjectionBlockedError

    with pytest.raises(ReferenceProjectionBlockedError) as exc_info:
        await rvt.execute_reference_video_task(
            "demo",
            "E1U1",
            {"script_file": "scripts/episode_1.json"},
            user_id="u1",
        )
    assert exc_info.value.code == "reference_asset_missing"
    assert exc_info.value.params["missing"] == (("character", "张三"),)
    assert exc_info.value.params["missing_text"] == "character: 张三"


@pytest.mark.asyncio
async def test_execute_reference_video_task_rejects_unclaimed_formal_sheet_before_submission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """An active Manifest, rather than a surviving pointer/file, owns formal-sheet admission."""

    from lib.artifacts.artifact_manifest import ArtifactKey, ProjectArtifactManifestAdapter
    from lib.script.reference_video.request_projection import ReferenceProjectionBlockedError
    from server.services.tasks import reference_video_tasks as rvt

    proj_dir = write_project(tmp_path)
    ProjectArtifactManifestAdapter(proj_dir).delete_entry(ArtifactKey.asset_sheet("character", "张三"))

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(
        (proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")
    )
    _wire_locked_script(fake_pm)
    use_project_manager(monkeypatch, fake_pm)

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock()
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="ark",
        backend_model="doubao-seedance-2-0-260128",
        supported_durations=(3,),
    )

    with pytest.raises(ReferenceProjectionBlockedError) as exc_info:
        await rvt.execute_reference_video_task(
            "demo",
            "E1U1",
            {"script_file": "scripts/episode_1.json"},
            user_id="u1",
        )

    assert exc_info.value.code == "reference_asset_missing"
    assert exc_info.value.params["missing"] == (("character", "张三"),)
    fake_generator.generate_video_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_reference_video_task_rejects_unclaimed_bound_script_before_submission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from server.services.tasks import reference_video_tasks as rvt

    proj_dir = write_project(tmp_path, register_script=False)

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(
        (proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")
    )
    _wire_locked_script(fake_pm)
    use_project_manager(monkeypatch, fake_pm)

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock()
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="ark",
        backend_model="doubao-seedance-2-0-260128",
        supported_durations=(3,),
    )

    with pytest.raises(ValueError, match="episode script is not registered"):
        await rvt.execute_reference_video_task(
            "demo",
            "E1U1",
            {"script_file": "scripts/episode_1.json"},
            user_id="u1",
        )

    fake_generator.generate_video_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_reference_video_task_rechecks_formal_sheet_claim_before_provider_submission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Losing a selected sheet claim while preparing a request must not spend provider quota."""

    from lib.artifacts.artifact_manifest import ArtifactKey, ProjectArtifactManifestAdapter
    from server.services.tasks import reference_video_tasks as rvt

    proj_dir = write_project(tmp_path)

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(
        (proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")
    )
    _wire_locked_script(fake_pm)
    use_project_manager(monkeypatch, fake_pm)

    provider_submissions: list[str] = []

    async def _fake_generate_video_async(**kwargs):
        # Simulate concurrent claim revocation in worker threads without blocking the event loop.
        await asyncio.to_thread(
            lambda: ProjectArtifactManifestAdapter(proj_dir).delete_entry(ArtifactKey.asset_sheet("character", "张三"))
        )
        await kwargs["before_submit"]()
        provider_submissions.append("submitted")
        raise AssertionError("provider submission must remain unreachable")

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="ark",
        backend_model="doubao-seedance-2-0-260128",
        supported_durations=(3,),
    )
    fake_queue = MagicMock()
    fake_queue.persist_execution_checkpoint = AsyncMock()
    monkeypatch.setattr(rvt, "get_generation_queue", lambda: fake_queue)

    with pytest.raises(ValueError, match="no longer registered"):
        await rvt.execute_reference_video_task(
            "demo",
            "E1U1",
            {"script_file": "scripts/episode_1.json"},
            user_id="u1",
            task_id="task-reference-claim-race",
        )

    assert provider_submissions == []
    fake_queue.persist_execution_checkpoint.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_reference_video_task_blocks_an_unmigrated_project_before_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Manifests alone authorize reading generated artifacts: block below-current-schema projects before paid calls."""

    from lib.project.project_migration_failure import ProjectMigrationError
    from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
    from server.services.tasks import reference_video_tasks as rvt

    proj_dir = write_project(tmp_path)
    project_path = proj_dir / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project["schema_version"] = 7
    project_path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = project
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(
        (proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")
    )
    _wire_locked_script(fake_pm)
    use_project_manager(monkeypatch, fake_pm)

    provider_submissions: list[str] = []

    async def _fake_generate_video_async(**kwargs):
        provider_submissions.append("submitted")
        raise AssertionError("provider submission must remain unreachable")

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="ark",
        backend_model="doubao-seedance-2-0-260128",
        supported_durations=(3,),
    )
    fake_queue = MagicMock()
    fake_queue.persist_execution_checkpoint = AsyncMock()
    monkeypatch.setattr(rvt, "get_generation_queue", lambda: fake_queue)

    with pytest.raises(ProjectMigrationError, match=f"did not reach v{CURRENT_PROJECT_SCHEMA_VERSION}"):
        await rvt.execute_reference_video_task(
            "demo",
            "E1U1",
            {"script_file": "scripts/episode_1.json"},
            user_id="u1",
            task_id="task-reference-unmigrated-project",
        )

    assert provider_submissions == []
    fake_queue.persist_execution_checkpoint.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_reference_video_task_rejects_a_replaced_sheet_before_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from server.services.tasks import reference_video_tasks as rvt

    proj_dir = write_project(tmp_path)
    project_path = proj_dir / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = project
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(
        (proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")
    )
    _wire_locked_script(fake_pm)
    use_project_manager(monkeypatch, fake_pm)

    provider_submissions: list[str] = []

    async def _fake_generate_video_async(**kwargs):
        # Simulate concurrent sheet replacement through threaded disk writes, not on the event loop.
        await asyncio.to_thread((proj_dir / "characters" / "张三.png").write_bytes, b"replacement-sheet")
        await kwargs["before_submit"]()
        provider_submissions.append("submitted")
        raise AssertionError("provider submission must remain unreachable")

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="ark",
        backend_model="doubao-seedance-2-0-260128",
        supported_durations=(3,),
    )
    fake_queue = MagicMock()
    fake_queue.persist_execution_checkpoint = AsyncMock()
    monkeypatch.setattr(rvt, "get_generation_queue", lambda: fake_queue)

    with pytest.raises(ValueError, match="changed since it was selected"):
        await rvt.execute_reference_video_task(
            "demo",
            "E1U1",
            {"script_file": "scripts/episode_1.json"},
            user_id="u1",
            task_id="task-reference-sheet-bytes-race",
        )

    assert provider_submissions == []
    fake_queue.persist_execution_checkpoint.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_reference_video_task_refuses_a_script_outside_the_episode_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Only project.json episodes ledger identifies artifacts: reject unbound scripts before paid calls."""

    from server.services.tasks import reference_video_tasks as rvt

    proj_dir = write_project(tmp_path)
    canonical_script = proj_dir / "scripts" / "episode_1.json"
    script = json.loads(canonical_script.read_text(encoding="utf-8"))
    unbound_script = proj_dir / "scripts" / "unbound.json"
    unbound_script.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(unbound_script.read_text(encoding="utf-8"))
    _wire_locked_script(fake_pm)
    use_project_manager(monkeypatch, fake_pm)

    async def _fake_generate_video_async(**kwargs):
        await kwargs["before_submit"]()
        raise RuntimeError("stop after checkpoint")

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="ark",
        backend_model="doubao-seedance-2-0-260128",
        supported_durations=(3,),
    )
    fake_queue = MagicMock()
    fake_queue.persist_execution_checkpoint = AsyncMock()
    monkeypatch.setattr(rvt, "get_generation_queue", lambda: fake_queue)

    with pytest.raises(ValueError, match="is not bound to episode"):
        await rvt.execute_reference_video_task(
            "demo",
            "E1U1",
            {"script_file": "scripts/unbound.json"},
            user_id="u1",
            task_id="task-unbound-script",
        )

    fake_generator.generate_video_async.assert_not_awaited()
    fake_queue.persist_execution_checkpoint.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_reference_video_task_uses_real_media_generator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Executor must use real MediaGenerator._get_output_path.

    Mock only outer VideoBackend.generate; unregistered resource_type in lib.project.resource_paths immediately raises ValueError here."""
    from arcreel_market_core.video_backend_contract import VideoCapabilities, VideoGenerationResult
    from lib.artifacts.version_manager import VersionManager
    from lib.generation.media_generator import MediaGenerator
    from server.services.tasks import reference_video_tasks as rvt

    proj_dir = write_project(tmp_path)

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(
        (proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")
    )
    _wire_locked_script(fake_pm)
    use_project_manager(monkeypatch, fake_pm)

    # Mock only outer VideoBackend (sole truly external dependency) plus Ledger/ConfigResolver
    # (both touch DB, absent here). Use real VersionManager so this path protects its
    # RESOURCE_TYPES / EXTENSIONS allowlists too;
    # missing registration in any of the three registries raises ValueError here.
    captured_requests: list = []

    class _FakeVideoBackend:
        name = "ark"
        model = "doubao-seedance-2-0-260128"
        capabilities: ClassVar[set] = set()

        @property
        def video_capabilities(self):
            return VideoCapabilities(max_reference_images=9)

        async def generate(self, request):
            captured_requests.append(request)
            await write_backend_output(request.output_path, b"\x00\x00\x00 ftypmp42")
            return VideoGenerationResult(
                video_path=request.output_path,
                provider=self.name,
                model=self.model,
                duration_seconds=request.duration_seconds,
                video_uri="uri-x",
                usage_tokens=0,
                generate_audio=False,
            )

    class _FakeLedger:
        @asynccontextmanager
        async def record(self, **_kwargs):
            class _Call:
                call_id = 1

                def success(self, _result, *, usage=None):
                    pass

            yield _Call()

    # object.__new__ bypasses MediaGenerator.__init__ to avoid its Ledger DB initialization.
    real_gen = object.__new__(MediaGenerator)
    real_gen.project_path = proj_dir
    real_gen.project_name = "demo"
    real_gen._rate_limiter = None
    real_gen._image_backend = None
    real_gen._video_backend = _FakeVideoBackend()
    real_gen._user_id = "u1"
    real_gen._config = FakeConfigResolver(requested_generate_audio=False)
    real_gen._image_provider_id = None
    real_gen._video_provider_id = None
    real_gen.versions = VersionManager(proj_dir)
    real_gen.ledger = _FakeLedger()

    _wire_context(monkeypatch, rvt, real_gen, backend_name="ark", backend_model="doubao-seedance-2-0-260128")

    async def _fake_extract(*_a, **_k):
        return True

    use_video_thumbnail_extractor(monkeypatch, _fake_extract)

    result = await rvt.execute_reference_video_task(
        "demo",
        "E1U1",
        {"script_file": "scripts/episode_1.json"},
        user_id="u1",
    )

    # Call backend once with output_path from resource_relative_path("reference_videos", ...) template.
    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert req.output_path == (proj_dir / "reference_videos" / "E1U1.mp4")
    # Persist a real file.
    assert (proj_dir / "reference_videos" / "E1U1.mp4").exists()
    assert result["file_path"] == "reference_videos/E1U1.mp4"
    assert result["video_uri"] == "uri-x"
    # Real VersionManager roundtrip places history under versions/reference_videos/.
    version_dir = proj_dir / "versions" / "reference_videos"
    assert version_dir.exists()
    assert any(p.suffix == ".mp4" for p in version_dir.iterdir())


@pytest.mark.asyncio
async def test_execute_reference_video_task_passes_source_refs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Pass **original sheet paths** directly to generate_video_async once. Compression belongs in the shared generation boundary: no temporary precompression or second R2V compression retry."""
    proj_dir = write_project(tmp_path)

    from server.services.tasks import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(
        (proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")
    )
    _wire_locked_script(fake_pm)
    use_project_manager(monkeypatch, fake_pm)

    captured: dict = {}
    call_count = {"n": 0}

    async def _fake_generate_video_async(**kwargs):
        call_count["n"] += 1
        captured["reference_images"] = kwargs.get("reference_images")
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        await write_backend_output(out, b"\x00")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-17T10:00:00"}]}
    _wire_context(monkeypatch, rvt, fake_generator, backend_name="grok", backend_model="grok-imagine-video")

    async def _fake_extract(*_a, **_k):
        return True

    use_video_thumbnail_extractor(monkeypatch, _fake_extract)

    result = await rvt.execute_reference_video_task(
        "demo",
        "E1U1",
        {"script_file": "scripts/episode_1.json"},
        user_id="u1",
    )
    # Single call: R2V performs no second compression retry.
    assert call_count["n"] == 1
    assert result["resource_id"] == "E1U1"
    # Pass original sheet paths (real project files) to the shared generation boundary, never temporary compressed copies;
    # compression belongs inside MediaGenerator's shared boundary.
    refs = [Path(p).resolve() for p in captured["reference_images"]]  # noqa: ASYNC240 -- Test-local small-file I/O/assertions, outside production event loops
    assert refs == [
        (proj_dir / "characters" / "张三.png").resolve(),
        (proj_dir / "scenes" / "酒馆.png").resolve(),
    ]


@pytest.mark.asyncio
async def test_execute_reference_video_task_rejects_duration_above_lane_maximum(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Executor never truncates a unit whose duration exceeds the current model tier."""
    proj_dir = write_project(tmp_path)

    # Give the unit two references and 15s duration to verify clamping.
    script_path = proj_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["video_units"][0]["duration_seconds"] = 15
    # Existing characters 张三 sheet plus scenes 酒馆 sheet already give two references.
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    from server.services.tasks import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(script_path.read_text(encoding="utf-8"))
    _wire_locked_script(fake_pm)
    use_project_manager(monkeypatch, fake_pm)

    captured: dict = {}

    async def _fake_generate_video_async(**kwargs):
        captured.update(kwargs)
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        await write_backend_output(out, b"\x00")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-17T10:00:00"}]}
    # Inject fake lane capabilities: custom model supported_durations=[2,4,6], max_reference_images=1.
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="custom-openai",
        backend_model="my-custom-video",
        max_refs=1,
        supported_durations=(2, 4, 6),
    )

    async def _fake_extract(*_a, **_k):
        return True

    use_video_thumbnail_extractor(monkeypatch, _fake_extract)

    from lib.script.reference_video.request_projection import ReferenceProjectionBlockedError

    with pytest.raises(ReferenceProjectionBlockedError) as exc_info:
        await rvt.execute_reference_video_task(
            "demo",
            "E1U1",
            {"script_file": "scripts/episode_1.json"},
            user_id="u1",
        )

    assert exc_info.value.code == "needs_replan"
    assert captured == {}


@pytest.mark.asyncio
async def test_execute_reference_video_task_prompt_matches_clipped_refs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Prompt `@图片N` indices match backend reference_images. After clipping references, slice again to `constrained_refs` length; rendering full `unit.references` would create out-of-range `@图片N` indices (five images clipped to one but prompt still contains `@图片5`)."""
    proj_dir = write_project(tmp_path)

    # Add a prop sheet for three references: character + scene + prop.
    (proj_dir / "props").mkdir()
    (proj_dir / "props" / "瓶子.png").write_bytes(_TINY_PNG)
    project_path = proj_dir / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project["props"] = {"瓶子": {"description": "x", "prop_sheet": "props/瓶子.png"}}
    project_path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    register_asset_sheet(proj_dir, "prop", "瓶子", "props/瓶子.png")

    script_path = proj_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    # Use Sora supported_durations member four to avoid execution duration guards; this test covers reference clipping.
    script["video_units"][0]["text"] = "Shot 1 (4s): @张三 在 @酒馆 拿起 @瓶子"
    script["video_units"][0]["duration_seconds"] = 4
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    from server.services.tasks import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads(project_path.read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(script_path.read_text(encoding="utf-8"))
    _wire_locked_script(fake_pm)
    use_project_manager(monkeypatch, fake_pm)

    captured: dict = {}

    async def _fake_generate_video_async(**kwargs):
        captured.update(kwargs)
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        await write_backend_output(out, b"\x00")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-17T10:00:00"}]}
    # Sora limit is one image (provider_id=openai, model=sora-2).
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="openai",
        backend_model="sora-2",
        max_refs=1,
        supported_durations=(4, 8, 12),
    )

    async def _fake_extract(*_a, **_k):
        return True

    use_video_thumbnail_extractor(monkeypatch, _fake_extract)

    await rvt.execute_reference_video_task(
        "demo",
        "E1U1",
        {"script_file": "scripts/episode_1.json"},
        user_id="u1",
    )

    # Clip three images to one: first section binds only @图片1, never @图片2/@图片3.
    assert len(captured["reference_images"]) == 1
    prompt = captured["prompt"]
    assert "@图片1" in prompt
    assert "@图片2" not in prompt
    assert "@图片3" not in prompt
    # Clipped @酒馆 / @瓶子 remain visual subjects (<X> is independent of image numbering), without binding lines or outgoing images.
    assert "<酒馆>" in prompt
    assert "<瓶子>" in prompt
    assert "<酒馆>@图片" not in prompt
    assert "<瓶子>@图片" not in prompt
    from lib.script.reference_video.request_projection import clamp_reference_assets
    from server.services.tasks.reference_video_tasks import reference_video_visual_basis_digest

    expected_facts = make_video_request_facts(
        route="reference_video",
        generation_type="r2v",
        provider_id="openai",
        model_id="sora-2",
        resolution=None,
        supported_durations=(4, 8, 12),
        allowed_durations=(4, 8, 12),
        max_reference_images=1,
        generate_audio=False,
        requested_generate_audio=True,
        has_audio_track=True,
        audio_switch_controllable=True,
    )
    expected_digest = reference_video_visual_basis_digest(
        project=project,
        project_path=proj_dir,
        unit=script["video_units"][0],
        request_assets=clamp_reference_assets(
            resolve_reference_assets(project, proj_dir, script["video_units"][0]),
            1,
        ),
        request_facts=expected_facts,
    )
    assert captured["visual_basis_digest"] == expected_digest


@pytest.mark.asyncio
async def test_execute_reference_video_task_prompt_matches_deduped_refs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """If unit.references has NFC/NFD duplicates for one character, prompt `@图片N` numbering matches deduplicated reference_images. Use the same deduplication count for images and logical render references; otherwise `@图片2` rebinds `@图片1`'s asset and a genuinely different later image loses its number."""
    import unicodedata

    proj_dir = write_project(tmp_path)
    name_nfc = unicodedata.normalize("NFC", "Hiếu")
    name_nfd = unicodedata.normalize("NFD", "Hiếu")
    assert name_nfc != name_nfd

    project_path = proj_dir / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project["characters"][name_nfc] = {"description": "x", "character_sheet": "characters/hieu.png"}
    project_path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    register_asset_sheet(proj_dir, "character", name_nfc, "characters/hieu.png")

    script_path = proj_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["video_units"][0]["text"] = f"Shot 1 (4s): @[{name_nfc}] 与 @[{name_nfd}] 在 @酒馆"
    script["video_units"][0]["duration_seconds"] = 4
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    from server.services.tasks import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads(project_path.read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(script_path.read_text(encoding="utf-8"))
    _wire_locked_script(fake_pm)
    use_project_manager(monkeypatch, fake_pm)

    captured: dict = {}

    async def _fake_generate_video_async(**kwargs):
        captured.update(kwargs)
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        await write_backend_output(out, b"\x00")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-17T10:00:00"}]}
    # max_refs=None disables provider clipping; deduplication is the sole effect under test.
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="grok",
        backend_model="grok-imagine-video",
        max_refs=None,
        supported_durations=(4, 8, 12),
    )

    async def _fake_extract(*_a, **_k):
        return True

    use_video_thumbnail_extractor(monkeypatch, _fake_extract)

    await rvt.execute_reference_video_task(
        "demo",
        "E1U1",
        {"script_file": "scripts/episode_1.json"},
        user_id="u1",
    )

    # Three references including an NFC/NFD duplicate yield two images; numbering must match.
    assert len(captured["reference_images"]) == 2
    prompt = captured["prompt"]
    assert "@图片1" in prompt
    assert "@图片2" in prompt
    assert "@图片3" not in prompt


@pytest.mark.asyncio
async def test_execute_reference_video_task_requests_planned_tier_regardless_of_current_tts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Even with longer narration in use_tts projects, worker selects tiers from planned script duration with video lane only.

    Ignore legacy queued ``narration_delivery`` options; checkpoints no longer record narration-delivery facts."""
    proj_dir = write_project(tmp_path)
    project_path = proj_dir / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project.update(narration_delivery="use_tts", audio_backend="dashscope/actual-tts", narration_voice="Cherry")
    project_path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    script_path = proj_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    # Five is outside [4,8,12], so request eight seconds; 9.5s narration does not raise the tier.
    script["video_units"][0]["text"] = "镜头1：海面\n{旁白正文。}"
    script["video_units"][0]["duration_seconds"] = 5
    script["video_units"][0].setdefault("generated_assets", {})["narration_audio"] = "audio/segment_E1U1.wav"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    tts_audio = proj_dir / "audio" / "segment_E1U1.wav"
    tts_audio.parent.mkdir()
    tts_audio.write_bytes(wav_bytes(9.5))

    from lib.script.reference_video.execution_checkpoint import ReferenceSubmissionCheckpoint
    from server.services.tasks import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = project
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(script_path.read_text(encoding="utf-8"))
    _wire_locked_script(fake_pm)
    use_project_manager(monkeypatch, fake_pm)

    captured: dict = {}

    async def _fake_generate_video_async(**kwargs):
        captured.update(kwargs)
        await kwargs["before_submit"]()
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        await write_backend_output(out, b"\x00")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    seen_lane_requests: list[dict[str, Any]] = []
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="openai",
        backend_model="sora-2",
        max_refs=9,
        supported_durations=(4, 8, 12),
        seen_lane_requests=seen_lane_requests,
    )
    fake_queue = MagicMock()
    fake_queue.persist_execution_checkpoint = AsyncMock()
    monkeypatch.setattr(rvt, "get_generation_queue", lambda: fake_queue)

    await rvt.execute_reference_video_task(
        "demo",
        "E1U1",
        {
            "script_file": "scripts/episode_1.json",
            "reference_request_options": {
                "narration_delivery": "use_tts",
                "confirmed_request_duration_seconds": 8,
            },
        },
        user_id="u1",
        task_id="task-planned-tier",
    )

    assert captured["duration_seconds"] == 8
    raw_checkpoint = fake_queue.persist_execution_checkpoint.await_args.args[1]
    checkpoint = ReferenceSubmissionCheckpoint.from_json(raw_checkpoint)
    assert checkpoint.duration_seconds == 8
    assert "narration" not in json.loads(raw_checkpoint)
    assert all(media.source_locator != "audio/segment_E1U1.wav" for media in checkpoint.media)
    assert [request["audio"] for request in seen_lane_requests] == [None]


async def test_execute_reference_video_task_persists_effective_duration_when_rounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """When tier selection shifts duration, checkpoints freeze actual requested seconds while enqueue payloads remain free of content snapshots."""
    proj_dir = write_project(tmp_path)
    script_path = proj_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["video_units"][0]["text"] = "@张三 推门"
    script["video_units"][0]["duration_seconds"] = 5
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    from lib.script.reference_video.execution_checkpoint import ReferenceSubmissionCheckpoint
    from server.services.tasks import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(script_path.read_text(encoding="utf-8"))
    _wire_locked_script(fake_pm)
    use_project_manager(monkeypatch, fake_pm)

    async def _fake_generate_video_async(**kwargs):
        await kwargs["before_submit"]()
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        await write_backend_output(out, b"\x00")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="openai",
        backend_model="sora-2",
        max_refs=9,
        supported_durations=(4, 8, 12),
    )

    fake_queue = MagicMock()
    fake_queue.persist_execution_checkpoint = AsyncMock()
    monkeypatch.setattr(rvt, "get_generation_queue", lambda: fake_queue)

    payload = {"script_file": "scripts/episode_1.json"}
    await rvt.execute_reference_video_task(
        "demo",
        "E1U1",
        payload,
        user_id="u1",
        task_id="task-1",
    )

    fake_queue.persist_execution_checkpoint.assert_awaited_once()
    checkpoint = ReferenceSubmissionCheckpoint.from_json(fake_queue.persist_execution_checkpoint.await_args.args[1])
    assert checkpoint.duration_seconds == 8
    assert payload == {"script_file": "scripts/episode_1.json"}


async def test_execute_reference_video_task_persists_duration_when_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Even without tier shifts, checkpoints freeze actual unit duration; resume never reads current project defaults."""
    proj_dir = write_project(tmp_path)

    from lib.script.reference_video.execution_checkpoint import ReferenceSubmissionCheckpoint
    from server.services.tasks import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(
        (proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")
    )
    _wire_locked_script(fake_pm)
    use_project_manager(monkeypatch, fake_pm)

    async def _fake_generate_video_async(**kwargs):
        await kwargs["before_submit"]()
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        await write_backend_output(out, b"\x00")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="openai",
        backend_model="sora-2",
        max_refs=9,
        supported_durations=(3, 8, 12),
    )

    fake_queue = MagicMock()
    fake_queue.persist_execution_checkpoint = AsyncMock()
    monkeypatch.setattr(rvt, "get_generation_queue", lambda: fake_queue)

    await rvt.execute_reference_video_task(
        "demo",
        "E1U1",
        {"script_file": "scripts/episode_1.json"},
        user_id="u1",
        task_id="task-1",
    )

    checkpoint = ReferenceSubmissionCheckpoint.from_json(fake_queue.persist_execution_checkpoint.await_args.args[1])
    assert checkpoint.duration_seconds == 3


async def test_execute_reference_video_task_persists_execution_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Atomically freeze current-projection registry/provider/backend identities before provider submission."""
    proj_dir = write_project(tmp_path)

    from lib.script.reference_video.execution_checkpoint import ReferenceSubmissionCheckpoint
    from server.services.tasks import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(
        (proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")
    )
    _wire_locked_script(fake_pm)
    use_project_manager(monkeypatch, fake_pm)

    events: list[str] = []

    async def _fake_generate_video_async(**kwargs):
        await kwargs["before_submit"]()
        events.append("provider_submit")
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        await write_backend_output(out, b"\x00")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    # Family alias: registry provider_id differs from backend_name; persist registry identity
    # to match existing provider_id-column rules used by claim-pool filtering and resume locks.
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="ark",
        backend_model="doubao-seedance-1-5-pro-251215",
        registry_provider_id="ark-agent-plan",
        supported_durations=(3, 8, 12),
    )

    fake_queue = MagicMock()

    async def _persist_checkpoint(*_args, **_kwargs):
        events.append("checkpoint")

    fake_queue.persist_execution_checkpoint = AsyncMock(side_effect=_persist_checkpoint)
    monkeypatch.setattr(rvt, "get_generation_queue", lambda: fake_queue)

    await rvt.execute_reference_video_task(
        "demo",
        "E1U1",
        {"script_file": "scripts/episode_1.json"},
        user_id="u1",
        task_id="task-1",
    )

    fake_queue.persist_execution_checkpoint.assert_awaited_once()
    task_id, raw, provider_id = fake_queue.persist_execution_checkpoint.await_args.args
    checkpoint = ReferenceSubmissionCheckpoint.from_json(raw)
    assert task_id == "task-1"
    assert provider_id == "ark-agent-plan"
    assert checkpoint.provider_id == "ark-agent-plan"
    assert checkpoint.provider_model_id == "doubao-seedance-1-5-pro-251215"
    assert checkpoint.backend_model_id == "doubao-seedance-1-5-pro-251215"
    assert checkpoint.generation_type == "r2v"
    assert events == ["checkpoint", "provider_submit"]


@pytest.mark.asyncio
async def test_execute_reference_video_task_stages_actual_request_and_checkpoints_at_submit_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from lib.artifacts.artifact_manifest import ArtifactKey, ProjectArtifactManifestAdapter
    from lib.script.reference_video.execution_checkpoint import ReferenceSubmissionCheckpoint
    from server.services.tasks import reference_video_tasks as rvt

    proj_dir = write_project(tmp_path)
    tts_audio = proj_dir / "audio" / "segment_E1U1.wav"
    tts_audio.parent.mkdir()
    tts_audio.write_bytes(b"narration-is-not-a-provider-input")

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir

    def _load_script(_project_name: str, filename: str):
        assert filename == "scripts/episode_1.json"
        return json.loads((proj_dir / filename).read_text(encoding="utf-8"))

    fake_pm.load_script.side_effect = _load_script
    _wire_locked_script(fake_pm)
    use_project_manager(monkeypatch, fake_pm)

    events: list[str] = []
    submitted: dict[str, Any] = {}
    submitted_visual_bases: dict[str, str] = {}

    async def _edit_source_before_staging(project_path, task_id, inputs):
        """Inject through ``stage_media_for_task``: rewrite source between current digest and staging, leaving all other staging real."""
        if inputs:
            await asyncio.to_thread(inputs[0].path.write_bytes, b"edited-between-live-basis-and-staging")
        return await rvt.stage_provider_media_for_task(project_path, task_id, inputs, stage=rvt.stage_provider_media)

    async def _fake_generate_video_async(**kwargs):
        submitted.update(kwargs)
        assert kwargs["formal_output"] is True
        staged_task_ids = {
            Path(path).relative_to(proj_dir / ".arcreel" / "tasks").parts[0] for path in kwargs["reference_images"]
        }
        assert len(staged_task_ids) == 1
        assert all(Path(path).parent.name == "provider_media" for path in kwargs["reference_images"])
        submitted_visual_bases[staged_task_ids.pop()] = kwargs["visual_basis_digest"]
        submitted["checkpoint_metadata"] = await kwargs["before_submit"]()
        events.append("provider_submit")
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        await write_backend_output(out, b"paid-video")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-08-13T00:00:00Z"}]}
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="ark",
        backend_model="doubao-seedance-2-0-260128",
        supported_durations=(3, 8, 12),
    )

    persisted: dict[str, Any] = {}

    async def _persist_checkpoint(task_id: str, raw: str, provider_id: str) -> None:
        events.append("checkpoint")
        persisted.update(task_id=task_id, raw=raw, provider_id=provider_id)

    fake_queue = MagicMock()
    fake_queue.persist_execution_checkpoint = AsyncMock(side_effect=_persist_checkpoint)
    monkeypatch.setattr(rvt, "get_generation_queue", lambda: fake_queue)
    use_video_thumbnail_extractor(monkeypatch, AsyncMock(return_value=False))

    # Baseline: submitted visual-basis digest before source changes, based on the bytes underlying the old current digest.
    await rvt.execute_reference_video_task(
        "demo", "E1U1", {}, script_file="scripts/episode_1.json", user_id="u1", task_id="task-baseline"
    )
    events.clear()

    result = await rvt.execute_reference_video_task(
        "demo",
        "E1U1",
        {
            "script_file": "scripts/wrong.json",
            "prompt": "stale enqueue prompt",
            "duration_seconds": 999,
            "video_provider_r2v": "stale/model",
        },
        script_file="scripts/episode_1.json",
        user_id="u1",
        task_id="task-submit",
        stage_media_for_task=_edit_source_before_staging,
    )

    assert events == ["checkpoint", "provider_submit"]
    assert result["resource_id"] == "E1U1"
    # If source changes between current digest and staging, bind the submitted digest to staged changed bytes, not the old current digest.
    assert submitted_visual_bases["task-submit"] != submitted_visual_bases["task-baseline"]
    checkpoint = ReferenceSubmissionCheckpoint.from_json(persisted["raw"])
    assert persisted["task_id"] == "task-submit"
    assert persisted["provider_id"] == "ark"
    assert checkpoint.script_file == "scripts/episode_1.json"
    assert checkpoint.provider_id == "ark"
    assert checkpoint.provider_model_id == "doubao-seedance-2-0-260128"
    assert checkpoint.backend_model_id == "doubao-seedance-2-0-260128"
    assert checkpoint.duration_seconds == 3
    assert checkpoint.prompt == submitted["prompt"]
    assert [media.role for media in checkpoint.media] == ["reference_image", "reference_image"]
    assert all(media.source_locator != "audio/segment_E1U1.wav" for media in checkpoint.media)
    assert checkpoint.artifact_visual_basis is not None
    assert checkpoint.artifact_visual_basis.kind == "artifact-visual/video-reference"
    metadata = submitted["checkpoint_metadata"]
    assert metadata["execution_request_digest"] == checkpoint.request_digest
    assert metadata["execution_prompt_sha256"] == checkpoint.prompt_sha256
    assert metadata["execution_visual_basis_digest"] == checkpoint.visual_basis_digest
    assert checkpoint.artifact_currency is not None
    assert metadata["artifact_video_currency"] == checkpoint.artifact_currency.to_dict()
    assert ProjectArtifactManifestAdapter(proj_dir).get_entry(ArtifactKey.episode_video(1, "E1U1")) is None
    assert not (proj_dir / ".arcreel" / "tasks" / "task-submit" / "provider_media").exists()

    async def _cancel_after_staging(**_kwargs):
        assert await asyncio.to_thread((proj_dir / ".arcreel" / "tasks" / "task-cancel" / "provider_media").is_dir)
        raise asyncio.CancelledError

    fake_generator.generate_video_async = AsyncMock(side_effect=_cancel_after_staging)
    with pytest.raises(asyncio.CancelledError):
        await rvt.execute_reference_video_task(
            "demo",
            "E1U1",
            {},
            script_file="scripts/episode_1.json",
            user_id="u1",
            task_id="task-cancel",
        )
    assert not (proj_dir / ".arcreel" / "tasks" / "task-cancel" / "provider_media").exists()

    real_stage_provider_media = rvt.stage_provider_media
    staging_started = threading.Event()
    release_staging = threading.Event()
    staging_finished = threading.Event()

    def _delayed_stage(*args, **kwargs):
        try:
            staged = real_stage_provider_media(*args, **kwargs)
            staging_started.set()
            release_staging.wait(timeout=5)
            return staged
        finally:
            staging_finished.set()

    monkeypatch.setattr(rvt, "stage_provider_media", _delayed_stage)
    staging_task = asyncio.create_task(
        rvt.execute_reference_video_task(
            "demo",
            "E1U1",
            {},
            script_file="scripts/episode_1.json",
            user_id="u1",
            task_id="task-cancel-during-staging",
        )
    )
    assert await asyncio.to_thread(staging_started.wait, 5)
    staging_task.cancel()
    release_staging.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(staging_task, timeout=5)
    assert await asyncio.to_thread(staging_finished.wait, 5)
    assert not (proj_dir / ".arcreel" / "tasks" / "task-cancel-during-staging" / "provider_media").exists()
    monkeypatch.setattr(rvt, "stage_provider_media", real_stage_provider_media)

    def _reject_checkpoint(**_kwargs):
        raise RuntimeError("checkpoint construction failed")

    async def _invoke_rejected_checkpoint(**kwargs):
        assert await asyncio.to_thread(
            (proj_dir / ".arcreel" / "tasks" / "task-checkpoint-failure" / "provider_media").is_dir
        )
        await kwargs["before_submit"]()
        raise AssertionError("provider submit must not run after checkpoint construction fails")

    fake_generator.generate_video_async = AsyncMock(side_effect=_invoke_rejected_checkpoint)
    monkeypatch.setattr(rvt.ReferenceSubmissionCheckpoint, "create", _reject_checkpoint)
    with pytest.raises(RuntimeError, match="checkpoint construction failed"):
        await rvt.execute_reference_video_task(
            "demo",
            "E1U1",
            {},
            script_file="scripts/episode_1.json",
            user_id="u1",
            task_id="task-checkpoint-failure",
        )
    assert not (proj_dir / ".arcreel" / "tasks" / "task-checkpoint-failure" / "provider_media").exists()


def _provider_asset_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    proj_dir = write_project(tmp_path)
    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda _name, filename: json.loads((proj_dir / filename).read_text("utf-8"))
    _wire_locked_script(fake_pm)
    use_project_manager(monkeypatch, fake_pm)
    use_video_thumbnail_extractor(monkeypatch, AsyncMock(return_value=False))
    return proj_dir


def _capturing_queue(monkeypatch: pytest.MonkeyPatch, rvt) -> dict[str, str]:
    persisted: dict[str, str] = {}

    async def _persist(task_id: str, raw: str, _provider_id: str) -> None:
        persisted[task_id] = raw

    queue = MagicMock()
    queue.persist_execution_checkpoint = AsyncMock(side_effect=_persist)
    monkeypatch.setattr(rvt, "get_generation_queue", lambda: queue)
    return persisted


@pytest.mark.asyncio
async def test_selected_provider_assets_reach_request_and_checkpoint_without_changing_the_artifact_basis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Send usable associations as asset references in actual reference-image order; record checkpoint/version metadata. Artifact-currency bases match image-content transport."""
    from lib.asset_library.generation_inputs import ReferenceAssetChoice
    from lib.script.reference_video.execution_checkpoint import ReferenceSubmissionCheckpoint
    from server.services.tasks import reference_video_tasks as rvt

    proj_dir = _provider_asset_project(tmp_path, monkeypatch)
    persisted = _capturing_queue(monkeypatch, rvt)
    submitted: dict[str, dict[str, Any]] = {}
    seen_sources: list[list[Any]] = []
    use_assets = False

    async def _choose(sources):
        seen_sources.append(list(sources))
        return tuple(
            ReferenceAssetChoice(asset_uri="asset://asset-zhang-1", content_version_id="cv-zhang-1", link_id=7)
            if use_assets and source.name == "张三"
            else None
            for source in sources
        )

    async def _fake_generate_video_async(**kwargs):
        submitted[kwargs["task_id"]] = {**kwargs, "metadata": await kwargs["before_submit"]()}
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        await write_backend_output(out, b"paid-video")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.choose_reference_image_assets = AsyncMock(side_effect=_choose)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-11-03T00:00:00Z"}]}
    _wire_context(monkeypatch, rvt, fake_generator, backend_name="ark", backend_model="doubao-seedance-2-0-260128")

    await rvt.execute_reference_video_task(
        "demo", "E1U1", {}, script_file="scripts/episode_1.json", user_id="u1", task_id="task-plain"
    )
    use_assets = True
    await rvt.execute_reference_video_task(
        "demo", "E1U1", {}, script_file="scripts/episode_1.json", user_id="u1", task_id="task-asset"
    )

    # Choose by actual outgoing bytes: source path and staged digest in prompt image order.
    assert [(s.locator, s.name) for s in seen_sources[-1]] == [
        ("characters/张三.png", "张三"),
        ("scenes/酒馆.png", "酒馆"),
    ]
    plain = ReferenceSubmissionCheckpoint.from_json(persisted["task-plain"])
    asset = ReferenceSubmissionCheckpoint.from_json(persisted["task-asset"])
    assert [s.sha256 for s in seen_sources[-1]] == [m.sha256 for m in asset.media if m.role == "reference_image"]

    assert submitted["task-plain"]["reference_image_asset_uris"] is None
    assert submitted["task-asset"]["reference_image_asset_uris"] == ["asset://asset-zhang-1", None]
    assert len(submitted["task-asset"]["reference_images"]) == 2
    assert [ref.to_dict() for ref in asset.provider_asset_references] == [
        {"media_index": 0, "content_version_id": "cv-zhang-1", "asset_uri": "asset://asset-zhang-1"}
    ]
    assert submitted["task-asset"]["metadata"]["execution_provider_asset_references"] == [
        {"media_index": 0, "content_version_id": "cv-zhang-1", "asset_uri": "asset://asset-zhang-1"}
    ]
    assert plain.provider_asset_references == ()
    assert asset.request_digest != plain.request_digest
    # Transport form is outside artifact bases: identical content sent as asset references retains reuse and currency behavior.
    assert asset.artifact_currency is not None
    assert plain.artifact_currency is not None
    assert asset.artifact_currency.video_basis == plain.artifact_currency.video_basis
    assert asset.visual_basis_digest == plain.visual_basis_digest


@pytest.mark.asyncio
@pytest.mark.parametrize("unusable_from_call", [1, 2], ids=["at-projection", "after-staging"])
async def test_unusable_provider_asset_link_fails_before_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unusable_from_call: int
):
    """If applicable links are still ingesting, neither submit nor retain staging; persist failure naming the image and advising wait or unlink.

    Projection first checks current files using the same codes as task acceptance/batch preview. Recheck actual outgoing bytes after staging; if links became unavailable between checks, fail with the same code and guidance."""
    from lib.asset_library.generation_inputs import ProviderAssetLinkUnavailableError
    from lib.db.models.asset_library import ProviderAssetLink
    from lib.generation.task_failure import render_failure
    from lib.generation.task_failure_encoding import encode_task_failure_message
    from lib.script.reference_video.request_projection import ReferenceProjectionBlockedError
    from server.services.tasks import reference_video_tasks as rvt

    proj_dir = _provider_asset_project(tmp_path, monkeypatch)
    persisted = _capturing_queue(monkeypatch, rvt)
    pending = ProviderAssetLink(id=7, state="processing", selected=True)
    expected_error = ReferenceProjectionBlockedError if unusable_from_call == 1 else ProviderAssetLinkUnavailableError
    calls = 0

    async def _choose(sources):
        nonlocal calls
        calls += 1
        if calls >= unusable_from_call:
            raise ProviderAssetLinkUnavailableError(pending, index=1, name="张三")
        return (None,) * len(sources)

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock()
    fake_generator.choose_reference_image_assets = AsyncMock(side_effect=_choose)
    _wire_context(monkeypatch, rvt, fake_generator, backend_name="ark", backend_model="doubao-seedance-2-0-260128")

    with pytest.raises(expected_error) as excinfo:
        await rvt.execute_reference_video_task(
            "demo", "E1U1", {}, script_file="scripts/episode_1.json", user_id="u1", task_id="task-wait"
        )

    assert calls == unusable_from_call
    assert excinfo.value.code == "provider_asset_link_processing"
    assert fake_generator.generate_video_async.await_count == 0
    assert persisted == {}
    assert not (proj_dir / ".arcreel" / "tasks" / "task-wait" / "provider_media").exists()
    rendered = render_failure(encode_task_failure_message(excinfo.value), _, make_display_names())
    assert rendered is not None
    assert "图片1（张三）" in rendered
    assert "解除关联" in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reference_index", "sent_as_asset", "expected"),
    [
        (1, False, "供应商审核未通过图片2（酒馆）"),
        (0, True, "以素材库素材发出的图片1（张三）"),
        (None, False, "供应商审核未通过这次请求的输入图片"),
    ],
    ids=["plain-image", "asset", "unmapped"],
)
async def test_input_image_rejection_names_the_reference_image_and_points_to_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reference_index, sent_as_asset, expected
):
    """If ordinary images are rejected, name that asset image and suggest library upload. Do not suggest upload for rejected asset-reference transport; do not name uncertain images."""
    from lib.backends.provider_assets import ProviderInputImageRejectedError
    from lib.generation.task_failure import render_failure
    from lib.generation.task_failure_encoding import encode_task_failure_message
    from server.services.tasks import reference_video_tasks as rvt

    _provider_asset_project(tmp_path, monkeypatch)
    _capturing_queue(monkeypatch, rvt)

    async def _rejected(**kwargs):
        await kwargs["before_submit"]()
        raise ProviderInputImageRejectedError(
            provider_code="InputImageSensitiveContentDetected.PrivacyInformation",
            provider_reason="input image may contain a real person",
            reference_index=reference_index,
            sent_as_asset=sent_as_asset,
        )

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_rejected)
    fake_generator.versions.get_versions.return_value = {"versions": []}
    _wire_context(monkeypatch, rvt, fake_generator, backend_name="ark", backend_model="doubao-seedance-2-0-260128")

    with pytest.raises(ProviderInputImageRejectedError) as excinfo:
        await rvt.execute_reference_video_task(
            "demo", "E1U1", {}, script_file="scripts/episode_1.json", user_id="u1", task_id="task-rejected"
        )

    rendered = render_failure(encode_task_failure_message(excinfo.value), _, make_display_names())
    assert rendered is not None
    assert expected in rendered
    assert "InputImageSensitiveContentDetected.PrivacyInformation" in rendered
    assert ("上传到供应商素材库" in rendered) is (not sent_as_asset)
