"""Tests for execute_reference_video_task."""

from __future__ import annotations

import asyncio
import json
import threading
from contextlib import asynccontextmanager, contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from lib.reference_video.request_projection import resolve_reference_assets
from tests.fakes import FakeConfigResolver
from tests.integration.server.services.reference_video_tasks_support import (
    _TINY_PNG,
    _register_asset_sheet,
    _write_project,
)


def _wire_locked_script(fake_pm: MagicMock) -> None:
    """让 fake_pm.locked_script 产出磁盘上的真实剧本 dict。

    finalize 写回 unit 资产时会在剧本中查找 unit 并在缺失时抛 KeyError，
    裸 MagicMock 的 script.get("video_units") 不是 list 会直接炸。
    """
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
    resolution_or_fallback: str = "1080p",
    resolution: str | None = None,
    max_refs: int | None = None,
    max_duration: int | None = None,
    supported_durations: tuple[int, ...] = (3,),
    voice_consistency: str = "soft",
    max_reference_audio_count: int = 0,
    reference_audio_per_image: bool = False,
    requested_generate_audio: bool = True,
    generate_audio: bool = False,
    text_to_video: bool = True,
    seen_lane_requests: list[dict[str, Any]] | None = None,
) -> None:
    """把 fake generator + video lane 值包成 GenerationContext，替换 resolve_generation_context 单点。

    执行器不触碰 MediaGenerator 私有属性、不手工重建 provider 身份——所有
    provider/backend 身份、能力上限、resolution 均由 GenerationContext 的 video lane 提供。
    能力上限与 resolution 的解析逻辑本身在 tests/server/test_generation_context.py 覆盖，此处
    只需喂入 lane 值验证执行器的下游 clamp / 守卫 / 透传行为。

    ``registry_provider_id`` 缺省与 ``backend_name`` 相同（多数供应商如此）；族别名供应商
    （如 ark-agent-plan 族复用 Ark backend）两者不同，需显式区分以覆盖 registry 查表路径。
    """
    from lib.config.resolver import ProviderModel
    from lib.version_manager import PaidVersionCommit
    from server.services.generation_context import AudioLaneResult, GenerationContext, VideoLaneResult

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

    lane = VideoLaneResult(
        provider_model=ProviderModel(provider_id=registry_provider_id or backend_name, model_id=backend_model),
        backend_name=backend_name,
        backend_model=backend_model,
        resolution=resolution,
        resolution_or_fallback=resolution_or_fallback,
        supported_durations=supported_durations,
        max_duration=max_duration,
        max_reference_images=max_refs,
        voice_consistency=voice_consistency,  # type: ignore[arg-type]
        max_reference_audio_count=max_reference_audio_count,
        reference_audio_per_image=reference_audio_per_image,
        requested_generate_audio=requested_generate_audio,
        generate_audio=generate_audio,
        text_to_video=text_to_video,
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
        audio_lane = None
        if kwargs.get("audio") is not None:
            audio_lane = AudioLaneResult(
                provider_model=ProviderModel("dashscope", "configured-tts"),
                backend_name="dashscope",
                backend_model="actual-tts",
                narration_voice="Cherry",
                narration_speed=1.1,
                voices=(),
            )
        return GenerationContext(generator=fake_generator, video_lane=lane, audio_lane=audio_lane)

    monkeypatch.setattr(rvt, "resolve_generation_context", _fake_resolve)


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
    _wire_locked_script(fake_pm)
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
    _wire_context(monkeypatch, rvt, fake_generator, backend_name="ark", backend_model="doubao-seedance-2-0-260128")

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
async def test_execute_reference_video_task_rejects_changed_claim_provider_before_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """执行期 provider 与已占用槽不同时，在任何生成调用前交回 worker 重认领。"""

    from lib.generation_queue import DispatchProviderChanged
    from server.services import reference_video_tasks as rvt

    proj_dir = _write_project(tmp_path)
    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda _project_name, _filename: json.loads(
        (proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")
    )
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

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
    from server.services import reference_video_tasks as rvt

    proj_dir = _write_project(tmp_path)
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
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

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
@pytest.mark.parametrize(("strip_mentions", "expected_capability"), [(False, "r2v"), (True, "i2v")])
async def test_execute_reference_video_task_bucket_follows_resolved_references(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, strip_mentions: bool, expected_capability: str
):
    """执行侧按解析后的实际参考图分流定桶：有参考图 → r2v，无参考图的视频单元 → i2v。

    降级让 r2v 桶配置为拒空参考模型（DashScope R2V / MiniMax S2V）的项目也能生成
    无参考图的视频单元——若恒声明 r2v，这类视频单元执行期必以 video_reference_images_required 失败。
    """
    proj_dir = _write_project(tmp_path)
    if strip_mentions:
        script_path = proj_dir / "scripts" / "episode_1.json"
        script = json.loads(script_path.read_text(encoding="utf-8"))
        script["video_units"][0]["text"] = "空镜头，推门而入"
        script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir

    def fake_load_script(_project_name, _filename):
        return json.loads((proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8"))

    fake_pm.load_script.side_effect = fake_load_script
    _wire_locked_script(fake_pm)
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    async def _fake_generate_video_async(**kwargs):
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00\x00\x00 ftypmp42")
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

    monkeypatch.setattr(rvt, "resolve_generation_context", _capture)

    async def _fake_extract(*_a, **_k):
        return True

    monkeypatch.setattr(rvt, "extract_video_thumbnail", _fake_extract)

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

    assert captured["video"].capability == expected_capability
    assert "video_provider_i2v" not in captured["payload"]
    assert "video_provider_r2v" not in captured["payload"]


@pytest.mark.asyncio
async def test_execute_reference_video_task_rechecks_text_to_video_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    proj_dir = _write_project(tmp_path)
    script_path = proj_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["video_units"][0]["text"] = "空镜头，推门而入"
    script["video_units"][0]["duration_seconds"] = 6
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    from lib.reference_video.request_projection import ReferenceProjectionBlockedError
    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda _name, _filename: json.loads(script_path.read_text(encoding="utf-8"))
    _wire_locked_script(fake_pm)
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

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
    """A 类 tracer bullet：请求 reference_audio_files 的顺序与第一段 @音频N 指认严格一致。"""
    proj_dir = _write_project(tmp_path)

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
    _register_asset_sheet(proj_dir, "character", "李四", "characters/李四.png")
    refs_audio = proj_dir / "characters" / "refs_audio"
    refs_audio.mkdir(parents=True)
    (refs_audio / "张三.wav").write_bytes(b"RIFF")
    (refs_audio / "李四.mp3").write_bytes(b"ID3")

    script_path = proj_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["video_units"][0]["text"] = "@[张三] 推门而入。\n@[李四]：{你终于来了。}\n@[张三]：{今晚的酒，我请。}"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = project
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda _n, _f: json.loads(script_path.read_text(encoding="utf-8"))
    _wire_locked_script(fake_pm)
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    captured: dict = {}

    async def _fake_generate_video_async(**kwargs):
        captured.update(kwargs)
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00\x00\x00 ftypmp42")
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

    monkeypatch.setattr(rvt, "extract_video_thumbnail", _fake_extract)

    await rvt.execute_reference_video_task("demo", "E1U1", {"script_file": "scripts/episode_1.json"}, user_id="u1")

    prompt = captured["prompt"]
    # speaker 首现顺序 = 音频编号 = 请求字段顺序（李四先开口，尽管张三先入画）
    assert "<李四>的台词音色参考 @音频1，声音特征：清亮少女音。" in prompt
    assert "<张三>的台词音色参考 @音频2，声音特征：低沉沙哑的男声。" in prompt
    assert [p.name for p in captured["reference_audio_files"]] == ["李四.mp3", "张三.wav"]
    # speaker 位不产生参考图：李四没有 @图片N 绑定
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
    """无声视频：即便角色配好了音色档案、模型也是 A 类，请求里也不带任何参考音频负载；
    台词文本照常下发（供应商可用作口型参考），并随结果回一条无声知会。"""
    proj_dir = _write_project(tmp_path)

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

    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = project
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda _n, _f: json.loads(script_path.read_text(encoding="utf-8"))
    _wire_locked_script(fake_pm)
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    captured: dict = {}

    async def _fake_generate_video_async(**kwargs):
        captured.update(kwargs)
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00\x00\x00 ftypmp42")
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

    monkeypatch.setattr(rvt, "extract_video_thumbnail", _fake_extract)

    result = await rvt.execute_reference_video_task(
        "demo", "E1U1", {"script_file": "scripts/episode_1.json"}, user_id="u1"
    )

    assert captured["reference_audio_files"] is None
    assert captured["reference_audio_targets"] is None
    prompt = captured["prompt"]
    assert "@音频" not in prompt
    # 台词与参考图绑定照常，只有音色参考行消失
    assert "<张三>说 {今晚的酒，我请。}" in prompt
    assert "<张三>@图片1。" in prompt
    assert {"key": "ref_warn_silent_episode", "params": {}} in result["warnings"]


@pytest.mark.asyncio
async def test_execute_reference_video_task_rechecks_audio_switch_for_latest_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """任务等待期间切到恒有声音轨模型后，worker 按当前模型阻止无声请求。"""

    proj_dir = _write_project(tmp_path)
    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda _n, _f: json.loads(
        (proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")
    )
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

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
    """backend 要求音频逐段挂参考图（如 wan2.7-r2v）时，reference_audio_targets 须按名字
    对齐到正确的参考图下标，纯画外角色（李四）降级不绑定——不能假设两个列表天然同序。"""
    proj_dir = _write_project(tmp_path)

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
    _register_asset_sheet(proj_dir, "character", "李四", "characters/李四.png")
    refs_audio = proj_dir / "characters" / "refs_audio"
    refs_audio.mkdir(parents=True)
    (refs_audio / "张三.wav").write_bytes(b"RIFF")
    (refs_audio / "李四.mp3").write_bytes(b"ID3")

    script_path = proj_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    # 场景先于张三被提及：图片顺序是酒馆（下标0）、张三（下标1）——
    # 音频顺序（李四先开口→张三）与图片顺序不同序，位置对齐会把音频错挂到酒馆图上。
    script["video_units"][0]["text"] = (
        "@[酒馆] 内景。@[张三] 推门而入。\n@[李四]：{你终于来了。}\n@[张三]：{今晚的酒，我请。}"
    )
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = project
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda _n, _f: json.loads(script_path.read_text(encoding="utf-8"))
    _wire_locked_script(fake_pm)
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    captured: dict = {}

    async def _fake_generate_video_async(**kwargs):
        captured.update(kwargs)
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00\x00\x00 ftypmp42")
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

    monkeypatch.setattr(rvt, "extract_video_thumbnail", _fake_extract)

    await rvt.execute_reference_video_task("demo", "E1U1", {"script_file": "scripts/episode_1.json"}, user_id="u1")

    # 李四没有参考图（纯画外），降级不绑定：只剩张三一段音频
    assert [p.name for p in captured["reference_audio_files"]] == ["张三.wav"]
    # 引用顺序即正文首次提及顺序：酒馆在下标 0、张三在下标 1，音频按名字挂到张三那张图上。
    assert captured["reference_audio_targets"] == [1]


@pytest.mark.asyncio
async def test_execute_reference_video_task_omits_audio_field_for_soft_tier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """B 类：无音频字段，但第一段仍带 voice_style 声音特征声明。"""
    proj_dir = _write_project(tmp_path)
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

    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = project
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda _n, _f: json.loads(script_path.read_text(encoding="utf-8"))
    _wire_locked_script(fake_pm)
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    captured: dict = {}

    async def _fake_generate_video_async(**kwargs):
        captured.update(kwargs)
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00\x00\x00 ftypmp42")
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

    monkeypatch.setattr(rvt, "extract_video_thumbnail", _fake_extract)

    await rvt.execute_reference_video_task("demo", "E1U1", {"script_file": "scripts/episode_1.json"}, user_id="u1")

    assert captured["reference_audio_files"] is None
    assert "<张三>的声音特征：低沉沙哑的男声。" in captured["prompt"]
    assert "@音频" not in captured["prompt"]


@pytest.mark.asyncio
async def test_execute_reference_video_task_surfaces_render_warnings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """渲染期降级 warning 与既有 result.warnings 通道贯通（超上限截断降级）。"""
    proj_dir = _write_project(tmp_path)
    project = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    project["characters"]["张三"]["reference_audio"] = "characters/refs_audio/张三.wav"
    project["characters"]["李四"] = {
        "description": "x",
        "character_sheet": "characters/李四.png",
        "reference_audio": "characters/refs_audio/李四.mp3",
    }
    (proj_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    _register_asset_sheet(proj_dir, "character", "李四", "characters/李四.png")
    refs_audio = proj_dir / "characters" / "refs_audio"
    refs_audio.mkdir(parents=True)
    (refs_audio / "张三.wav").write_bytes(b"RIFF")
    (refs_audio / "李四.mp3").write_bytes(b"ID3")

    script_path = proj_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["video_units"][0]["text"] = "@[张三] 推门。\n@[张三]：{我回来了。}\n@[李四]：{你好。}"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = project
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda _n, _f: json.loads(script_path.read_text(encoding="utf-8"))
    _wire_locked_script(fake_pm)
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    async def _fake_generate_video_async(**kwargs):
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00\x00\x00 ftypmp42")
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

    monkeypatch.setattr(rvt, "extract_video_thumbnail", _fake_extract)

    result = await rvt.execute_reference_video_task(
        "demo", "E1U1", {"script_file": "scripts/episode_1.json"}, user_id="u1"
    )
    assert {"key": "ref_warn_reference_audio_overflow", "params": {"limit": 1, "name": "李四"}} in result["warnings"]


@pytest.mark.asyncio
async def test_execute_reference_video_task_clears_stale_video_uri_and_thumbnail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """重跑时新结果不含 video_uri 且缩略图提取失败 → 旧 video_uri / video_thumbnail 必须被清空，
    不能保留指向过期 URI / 已删除文件的旧值。"""
    proj_dir = _write_project(tmp_path)

    # 预置上一次成功生成留下的旧产物
    script_path = proj_dir / "scripts" / "episode_1.json"
    script_data = json.loads(script_path.read_text(encoding="utf-8"))
    ga = script_data["video_units"][0]["generated_assets"]
    ga["video_uri"] = "https://old/expired.mp4"
    ga["video_thumbnail"] = "reference_videos/thumbnails/E1U1.jpg"
    script_path.write_text(json.dumps(script_data, ensure_ascii=False), encoding="utf-8")

    from server.services import reference_video_tasks as rvt

    # locked_script 用真实 contextmanager 回写到 live_script，供断言读取
    live_script = json.loads(script_path.read_text(encoding="utf-8"))

    @contextmanager
    def _locked_script(_name, _file, *, validate=True):
        yield live_script

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(script_path.read_text(encoding="utf-8"))
    fake_pm.locked_script.side_effect = _locked_script
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    # 新后端不返回 video_uri（第 4 个元素为 None）
    async def _fake_generate_video_async(**kwargs):
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00\x00\x00 ftypmp42")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-17T10:00:00"}]}
    _wire_context(monkeypatch, rvt, fake_generator, backend_name="ark", backend_model="doubao-seedance-2-0-260128")

    # 缩略图提取失败 → thumb_rel=None
    async def _fake_extract(*_a, **_k):
        return False

    monkeypatch.setattr(rvt, "extract_video_thumbnail", _fake_extract)

    await rvt.execute_reference_video_task(
        "demo",
        "E1U1",
        {"script_file": "scripts/episode_1.json"},
        user_id="u1",
    )

    ga_after = live_script["video_units"][0]["generated_assets"]
    assert "video_uri" not in ga_after
    assert "video_thumbnail" not in ga_after
    # 正常产物仍正确写入
    assert ga_after["video_clip"] == "reference_videos/E1U1.mp4"
    assert ga_after["status"] == "completed"


@pytest.mark.asyncio
async def test_execute_reference_video_task_grok_uses_provider_default_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Regression: Grok 视频生成必须用 720p（xai_sdk 的 VideoResolutionMap 只接受 480p/720p；
    参考生视频执行器若回退到 MediaGenerator 默认 1080p，会在 SDK 抛 `Invalid video resolution 1080p`）。
    执行器必须把 video lane 的 `resolution_or_fallback` 原样传给 generate_video_async——
    档位的解析/兜底逻辑（provider fallback、model_settings 优先级）在
    tests/server/test_generation_context.py 覆盖。
    """
    proj_dir = _write_project(tmp_path)

    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(
        (proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")
    )
    _wire_locked_script(fake_pm)
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    captured: dict = {}

    async def _fake_generate_video_async(**kwargs):
        captured.update(kwargs)
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00\x00\x00 ftypmp42")
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
        resolution_or_fallback="720p",
    )

    async def _fake_extract(*_a, **_k):
        return True

    monkeypatch.setattr(rvt, "extract_video_thumbnail", _fake_extract)

    await rvt.execute_reference_video_task(
        "demo",
        "E1U1",
        {"script_file": "scripts/episode_1.json"},
        user_id="u1",
    )

    assert captured.get("resolution") == "720p", (
        f"Grok 执行器必须显式传 720p，否则 MediaGenerator 默认 1080p 会被 xai_sdk 拒绝。"
        f"实际收到: {captured.get('resolution')!r}"
    )


@pytest.mark.asyncio
async def test_execute_reference_video_task_narrows_durations_by_registry_provider_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """条件档位收窄按规范 registry provider_id 查表，不按 backend 报告的族名。

    族别名供应商（如 ark-agent-plan 族复用 Ark backend）的 backend_name 不是 registry key：
    拿它查 ModelInfo 会静默落空，收窄整个失效——3 秒剧本会取到 4 秒，而 Veo 3.1 带参考图
    只接受 8 秒，执行期必然被 backend 拒绝。
    """
    proj_dir = _write_project(tmp_path)

    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(
        (proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")
    )
    _wire_locked_script(fake_pm)
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    captured: dict = {}

    async def _fake_generate_video_async(**kwargs):
        captured.update(kwargs)
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00\x00\x00 ftypmp42")
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
        resolution_or_fallback="720p",
        supported_durations=(4, 6, 8),
    )

    async def _fake_extract(*_a, **_k):
        return True

    monkeypatch.setattr(rvt, "extract_video_thumbnail", _fake_extract)

    await rvt.execute_reference_video_task(
        "demo",
        "E1U1",
        {"script_file": "scripts/episode_1.json"},
        user_id="u1",
    )

    # 3 秒剧本 + 带参考图：按 registry 声明收窄到 [8]。落空则取全集首个能装下的 4 秒。
    assert captured.get("duration_seconds") == 8


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
    _wire_locked_script(fake_pm)
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)
    _wire_context(
        monkeypatch,
        rvt,
        MagicMock(),
        backend_name="ark",
        backend_model="doubao-seedance-2-0-260128",
        supported_durations=(3,),
    )

    from lib.reference_video.request_projection import ReferenceProjectionBlockedError

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

    from lib.artifact_manifest import ArtifactKey, ProjectArtifactManifestAdapter
    from lib.reference_video.request_projection import ReferenceProjectionBlockedError
    from server.services import reference_video_tasks as rvt

    proj_dir = _write_project(tmp_path)
    ProjectArtifactManifestAdapter(proj_dir).delete_entry(ArtifactKey.asset_sheet("character", "张三"))

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(
        (proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")
    )
    _wire_locked_script(fake_pm)
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

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
    from server.services import reference_video_tasks as rvt

    proj_dir = _write_project(tmp_path, register_script=False)

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(
        (proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")
    )
    _wire_locked_script(fake_pm)
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

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

    from lib.artifact_manifest import ArtifactKey, ProjectArtifactManifestAdapter
    from server.services import reference_video_tasks as rvt

    proj_dir = _write_project(tmp_path)

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(
        (proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")
    )
    _wire_locked_script(fake_pm)
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    provider_submissions: list[str] = []

    async def _fake_generate_video_async(**kwargs):
        ProjectArtifactManifestAdapter(proj_dir).delete_entry(ArtifactKey.asset_sheet("character", "张三"))
        await kwargs["before_submit"](71)
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
    """产物清单是读取已生成产物的唯一口径：项目没迁到 v8 就在付费前阻断。"""

    from lib.project_migration_failure import ProjectMigrationError
    from server.services import reference_video_tasks as rvt

    proj_dir = _write_project(tmp_path)
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
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

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

    with pytest.raises(ProjectMigrationError, match="did not reach v9"):
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
    from server.services import reference_video_tasks as rvt

    proj_dir = _write_project(tmp_path)
    project_path = proj_dir / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = project
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(
        (proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")
    )
    _wire_locked_script(fake_pm)
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    provider_submissions: list[str] = []

    async def _fake_generate_video_async(**kwargs):
        (proj_dir / "characters" / "张三.png").write_bytes(b"replacement-sheet")
        await kwargs["before_submit"](73)
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
    """产物身份只认 project.json 的 episodes 账本：没绑定的剧本在付费前被拒。"""

    from server.services import reference_video_tasks as rvt

    proj_dir = _write_project(tmp_path)
    canonical_script = proj_dir / "scripts" / "episode_1.json"
    script = json.loads(canonical_script.read_text(encoding="utf-8"))
    unbound_script = proj_dir / "scripts" / "unbound.json"
    unbound_script.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(unbound_script.read_text(encoding="utf-8"))
    _wire_locked_script(fake_pm)
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    async def _fake_generate_video_async(**kwargs):
        await kwargs["before_submit"](72)
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
    """执行器必须走真实 MediaGenerator._get_output_path。

    只 mock 最外层的 VideoBackend.generate ——resource_type 未注册到
    lib.resource_paths 时，这条测试会立刻爆 ValueError。
    """
    from lib.media_generator import MediaGenerator
    from lib.version_manager import VersionManager
    from lib.video_backends.base import VideoCapabilities, VideoGenerationResult
    from server.services import reference_video_tasks as rvt

    proj_dir = _write_project(tmp_path)

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(
        (proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")
    )
    _wire_locked_script(fake_pm)
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    # 只 mock 最外层：VideoBackend（唯一的真外部依赖）+ Ledger/ConfigResolver
    # （这俩摸 DB，测试无 DB）。VersionManager 用真实实现 —— 这样 VersionManager
    # 自己的白名单（RESOURCE_TYPES / EXTENSIONS）也被这条路径守住，
    # 任何一处三张注册表漏登记都会在此爆 ValueError。
    captured_requests: list = []

    class _FakeVideoBackend:
        name = "ark"
        model = "doubao-seedance-2-0-260128"
        capabilities: set = set()

        @property
        def video_capabilities(self):
            return VideoCapabilities(max_reference_images=9)

        async def generate(self, request):
            captured_requests.append(request)
            request.output_path.parent.mkdir(parents=True, exist_ok=True)
            request.output_path.write_bytes(b"\x00\x00\x00 ftypmp42")
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

                def success(self, _result):
                    pass

            yield _Call()

    # object.__new__ 绕过 MediaGenerator.__init__（避开 __init__ 里的 Ledger 对 DB 的初始化）
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

    monkeypatch.setattr(rvt, "extract_video_thumbnail", _fake_extract)

    result = await rvt.execute_reference_video_task(
        "demo",
        "E1U1",
        {"script_file": "scripts/episode_1.json"},
        user_id="u1",
    )

    # Backend 被真实调用一次，且 output_path 走 resource_relative_path("reference_videos", ...) 模板
    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert req.output_path == (proj_dir / "reference_videos" / "E1U1.mp4")
    # 真实文件落盘
    assert (proj_dir / "reference_videos" / "E1U1.mp4").exists()
    assert result["file_path"] == "reference_videos/E1U1.mp4"
    assert result["video_uri"] == "uri-x"
    # 真实 VersionManager 闭环：版本文件落入 versions/reference_videos/
    version_dir = proj_dir / "versions" / "reference_videos"
    assert version_dir.exists()
    assert any(p.suffix == ".mp4" for p in version_dir.iterdir())


@pytest.mark.asyncio
async def test_execute_reference_video_task_passes_source_refs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """执行器把**源 sheet 路径**直接交给 generate_video_async（单次调用），压缩下沉
    咽喉层——不预压缩到临时文件，不在 R2V 层做二次压缩重试。
    """
    proj_dir = _write_project(tmp_path)

    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(
        (proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")
    )
    _wire_locked_script(fake_pm)
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    captured: dict = {}
    call_count = {"n": 0}

    async def _fake_generate_video_async(**kwargs):
        call_count["n"] += 1
        captured["reference_images"] = kwargs.get("reference_images")
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-17T10:00:00"}]}
    _wire_context(monkeypatch, rvt, fake_generator, backend_name="grok", backend_model="grok-imagine-video")

    async def _fake_extract(*_a, **_k):
        return True

    monkeypatch.setattr(rvt, "extract_video_thumbnail", _fake_extract)

    result = await rvt.execute_reference_video_task(
        "demo",
        "E1U1",
        {"script_file": "scripts/episode_1.json"},
        user_id="u1",
    )
    # 单次调用：R2V 层不做二次压缩重试
    assert call_count["n"] == 1
    assert result["resource_id"] == "E1U1"
    # 传给咽喉层的恰是源 sheet 路径（项目目录内真实文件），而非临时压缩副本——
    # 压缩已下沉到 MediaGenerator 咽喉层
    refs = [Path(p).resolve() for p in captured["reference_images"]]
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
    proj_dir = _write_project(tmp_path)

    # 改造 unit 让它有 2 张 refs + 15s duration，便于验证 clamp
    script_path = proj_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["video_units"][0]["duration_seconds"] = 15
    # characters 已有 张三 sheet；scenes 已有 酒馆 sheet —— refs 已是 2 张
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(script_path.read_text(encoding="utf-8"))
    _wire_locked_script(fake_pm)
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    captured: dict = {}

    async def _fake_generate_video_async(**kwargs):
        captured.update(kwargs)
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-17T10:00:00"}]}
    # lane 喂入假 caps —— 模拟 "supported_durations=[2,4,6]", max_reference_images=1 的 custom model
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="custom-openai",
        backend_model="my-custom-video",
        max_refs=1,
        max_duration=6,
        supported_durations=(2, 4, 6),
    )

    async def _fake_extract(*_a, **_k):
        return True

    monkeypatch.setattr(rvt, "extract_video_thumbnail", _fake_extract)

    from lib.reference_video.request_projection import ReferenceProjectionBlockedError

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
    """prompt 里的 `@图片N` 索引必须与 backend 收到的 reference_images 对齐：references 裁剪后
    须按 `constrained_refs` 长度重新 slice，用整条 `unit.references` 渲染会让 `@图片N` 越界
    （例如 5 张裁到 1 张，prompt 里仍出现 `@图片5`）。
    """
    proj_dir = _write_project(tmp_path)

    # 新增一个道具 sheet，让 unit 拥有 3 张 refs（1 character + 1 scene + 1 prop）。
    (proj_dir / "props").mkdir()
    (proj_dir / "props" / "瓶子.png").write_bytes(_TINY_PNG)
    project_path = proj_dir / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project["props"] = {"瓶子": {"description": "x", "prop_sheet": "props/瓶子.png"}}
    project_path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    _register_asset_sheet(proj_dir, "prop", "瓶子", "props/瓶子.png")

    script_path = proj_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    # 时长取 sora supported_durations 成员（4），避免触发执行层 duration 能力守卫；本测试聚焦 refs 裁剪。
    script["video_units"][0]["text"] = "Shot 1 (4s): @张三 在 @酒馆 拿起 @瓶子"
    script["video_units"][0]["duration_seconds"] = 4
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads(project_path.read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(script_path.read_text(encoding="utf-8"))
    _wire_locked_script(fake_pm)
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    captured: dict = {}

    async def _fake_generate_video_async(**kwargs):
        captured.update(kwargs)
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-17T10:00:00"}]}
    # Sora 上限 1 张（provider_id=openai, model=sora-2）
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="openai",
        backend_model="sora-2",
        max_refs=1,
        max_duration=12,
        supported_durations=(4, 8, 12),
    )

    async def _fake_extract(*_a, **_k):
        return True

    monkeypatch.setattr(rvt, "extract_video_thumbnail", _fake_extract)

    await rvt.execute_reference_video_task(
        "demo",
        "E1U1",
        {"script_file": "scripts/episode_1.json"},
        user_id="u1",
    )

    # 3 张裁到 1 张，第一段只能绑出 @图片1，不能出现 @图片2/@图片3
    assert len(captured["reference_images"]) == 1
    prompt = captured["prompt"]
    assert "@图片1" in prompt
    assert "@图片2" not in prompt
    assert "@图片3" not in prompt
    # 被裁掉的 @酒馆 / @瓶子 仍是画面主体（<X> 与图号解耦），只是没有绑定行、没随请求发图
    assert "<酒馆>" in prompt and "<瓶子>" in prompt
    assert "<酒馆>@图片" not in prompt and "<瓶子>@图片" not in prompt
    from lib.reference_video.request_projection import (
        ProviderProjectionCandidate,
        clamp_reference_assets,
    )
    from server.services.narration_delivery_tasks import reference_video_visual_basis_digest

    expected_candidate = ProviderProjectionCandidate(
        capability="r2v",
        provider_id="openai",
        model_id="sora-2",
        supported_durations=(4, 8, 12),
        max_reference_images=1,
        resolution="1080p",
        generate_audio=False,
        requested_generate_audio=True,
        has_audio_track=True,
        audio_switch_controllable=False,
    )
    expected_digest = reference_video_visual_basis_digest(
        project=project,
        project_path=proj_dir,
        unit=script["video_units"][0],
        request_assets=clamp_reference_assets(
            resolve_reference_assets(project, proj_dir, script["video_units"][0]),
            1,
        ),
        candidate=expected_candidate,
    )
    assert captured["visual_basis_digest"] == expected_digest


@pytest.mark.asyncio
async def test_execute_reference_video_task_prompt_matches_deduped_refs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """unit.references 携带同一角色的 NFC/NFD 两条记录时，prompt 的 `@图片N` 索引必须与去重后的
    reference_images 对齐：图片列表与渲染用的逻辑引用列表须按同一去重规则计数，否则 `@图片2`
    会错误绑到与 `@图片1` 相同的资产、后面一条真正不同的参考丢失编号。"""
    import unicodedata

    proj_dir = _write_project(tmp_path)
    name_nfc = unicodedata.normalize("NFC", "Hiếu")
    name_nfd = unicodedata.normalize("NFD", "Hiếu")
    assert name_nfc != name_nfd

    project_path = proj_dir / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project["characters"][name_nfc] = {"description": "x", "character_sheet": "characters/hieu.png"}
    project_path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    _register_asset_sheet(proj_dir, "character", name_nfc, "characters/hieu.png")

    script_path = proj_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["video_units"][0]["text"] = f"Shot 1 (4s): @[{name_nfc}] 与 @[{name_nfd}] 在 @酒馆"
    script["video_units"][0]["duration_seconds"] = 4
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads(project_path.read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(script_path.read_text(encoding="utf-8"))
    _wire_locked_script(fake_pm)
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    captured: dict = {}

    async def _fake_generate_video_async(**kwargs):
        captured.update(kwargs)
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-17T10:00:00"}]}
    # max_refs=None：不做 provider 裁剪，去重本身是本测试唯一验证的效应。
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="grok",
        backend_model="grok-imagine-video",
        max_refs=None,
        max_duration=12,
        supported_durations=(4, 8, 12),
    )

    async def _fake_extract(*_a, **_k):
        return True

    monkeypatch.setattr(rvt, "extract_video_thumbnail", _fake_extract)

    await rvt.execute_reference_video_task(
        "demo",
        "E1U1",
        {"script_file": "scripts/episode_1.json"},
        user_id="u1",
    )

    # 3 条 references（含一对 NFC/NFD 重复）去重后只剩 2 张图，图号必须与之对齐
    assert len(captured["reference_images"]) == 2
    prompt = captured["prompt"]
    assert "@图片1" in prompt
    assert "@图片2" in prompt
    assert "@图片3" not in prompt


@pytest.mark.asyncio
async def test_execute_reference_video_task_reprojects_fresh_tts_duration_and_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Worker only trusts current TTS media and rejects an accepted tier after it changes."""
    proj_dir = _write_project(tmp_path)
    script_path = proj_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    # 5 不是 [4,8,12] 成员 → 按 8 秒申请
    script["video_units"][0]["text"] = "镜头1：海面\n{旁白正文。}"
    script["video_units"][0]["duration_seconds"] = 5
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    from lib.reference_video.execution_checkpoint import ReferenceSubmissionCheckpoint
    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(script_path.read_text(encoding="utf-8"))
    _wire_locked_script(fake_pm)
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    captured: dict = {}

    async def _fake_generate_video_async(**kwargs):
        captured.update(kwargs)
        await kwargs["before_submit"](44)
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00")
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
        max_duration=12,
        supported_durations=(4, 8, 12),
        seen_lane_requests=seen_lane_requests,
    )

    from lib.artifact_manifest import ArtifactComparison, ArtifactStatus
    from lib.narration_delivery import NarrationAudioEvidence, TtsSynthesisSettings, prepare_narration_delivery
    from lib.reference_video.request_projection import ReferenceProjectionBlockedError
    from lib.speech_composition import admit_script_unit

    actual_duration = 6.2

    async def _materialize(**kwargs):
        options = kwargs["options"]
        tts_settings = await kwargs["tts_settings_resolver"].resolve_tts_synthesis_settings(kwargs["project"])
        assert tts_settings == TtsSynthesisSettings("dashscope", "actual-tts", "Cherry", 1.1)
        assert options.to_payload() == {
            "narration_delivery": "use_tts",
            "confirmed_request_duration_seconds": 8,
        }
        preparation = admit_script_unit("video_units", kwargs["unit"]).preparation
        delivery = prepare_narration_delivery(
            delivery="use_tts",
            preparation=preparation,
            artifact_path="audio/segment_E1U1.wav",
            settings=TtsSynthesisSettings("fake-audio", "tts-model", "voice", None),
            evidence=NarrationAudioEvidence(
                comparison=ArtifactComparison(
                    status=ArtifactStatus.CURRENT,
                    artifact_path="audio/segment_E1U1.wav",
                ),
                present=True,
                duration_seconds=actual_duration,
            ),
        )
        return replace(
            options,
            current_tts_duration_seconds=delivery.duration_floor,
            narration_preparation=delivery,
        )

    monkeypatch.setattr(rvt, "prepare_current_reference_video_request_options", _materialize)
    monkeypatch.setattr(rvt, "tts_task_in_progress", AsyncMock(return_value=False))
    monkeypatch.setattr(
        "server.services.narration_delivery_tasks.probe_existing_media_duration_seconds",
        AsyncMock(return_value=8.0),
    )
    fake_queue = MagicMock()
    fake_queue.persist_execution_checkpoint = AsyncMock()
    monkeypatch.setattr(rvt, "get_generation_queue", lambda: fake_queue)

    result = await rvt.execute_reference_video_task(
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
        task_id="task-current-tts",
    )
    assert captured["duration_seconds"] == 8
    checkpoint = ReferenceSubmissionCheckpoint.from_json(fake_queue.persist_execution_checkpoint.await_args.args[1])
    assert checkpoint.duration_seconds == 8
    assert checkpoint.narration.delivery == "use_tts"
    assert checkpoint.narration.tts_status == "current"
    assert checkpoint.narration.artifact_path == "audio/segment_E1U1.wav"
    assert checkpoint.narration.basis_digest
    assert checkpoint.narration.actual_duration_seconds == 6.2
    assert all(media.source_locator != "audio/segment_E1U1.wav" for media in checkpoint.media)
    assert callable(captured["before_formal_commit"])
    assert len(seen_lane_requests) == 1
    assert seen_lane_requests[0]["video"] is not None
    assert seen_lane_requests[0]["audio"] is not None
    warnings = result["warnings"]
    assert [w["key"] for w in warnings] == ["ref_duration_rounded_up"]
    assert warnings[0]["params"] == {"total": 6.2, "duration": 8, "model": "sora-2"}

    actual_duration = 9.5
    with pytest.raises(ReferenceProjectionBlockedError) as exc_info:
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
        )
    assert exc_info.value.code == "reference_duration_confirmation_required"
    assert fake_generator.generate_video_async.await_count == 1
    assert len(seen_lane_requests) == 2


@pytest.mark.asyncio
async def test_execute_reference_video_task_reuses_same_tier_visual_without_provider_or_state_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from lib.artifact_manifest import (
        ArtifactComparison,
        ArtifactKey,
        ArtifactManifest,
        ArtifactStatus,
        ProjectArtifactManifestAdapter,
        compose_video_artifact_basis,
    )
    from lib.narration_delivery import NarrationAudioEvidence, TtsSynthesisSettings, prepare_narration_delivery
    from lib.reference_video.request_projection import (
        ProviderProjectionCandidate,
        reference_audio_model_facts,
    )
    from lib.speech_artifact_provenance import build_video_duration_basis, build_video_speech_basis
    from lib.speech_composition import admit_script_unit
    from lib.version_manager import VersionManager
    from lib.video_artifact_facts import VideoArtifactCurrencyFacts
    from lib.visual_artifact_provenance import build_reference_video_artifact_visual_basis
    from server.services import reference_video_tasks as rvt
    from server.services.narration_delivery_tasks import reference_video_visual_basis_digest

    proj_dir = _write_project(tmp_path)
    script_path = proj_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    unit = script["video_units"][0]
    unit["text"] = "镜头1：海面\n{旁白正文。}"
    unit["duration_seconds"] = 5
    unit["generated_assets"].update(
        {
            "video_clip": "reference_videos/E1U1.mp4",
            "video_uri": "provider://existing",
            "status": "completed",
        }
    )
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    script_before = script_path.read_bytes()

    current = proj_dir / "reference_videos" / "E1U1.mp4"
    current.parent.mkdir(parents=True)
    current.write_bytes(b"existing-paid-video")
    versions = VersionManager(proj_dir)
    project = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    has_audio_track, audio_switch_controllable = reference_audio_model_facts(
        "openai", "sora-2", voice_consistency="soft", capability="i2v"
    )
    # 正文没有 @ 提及 → 无参考图，执行侧按 i2v 桶分流。
    candidate = ProviderProjectionCandidate(
        capability="i2v",
        provider_id="openai",
        model_id="sora-2",
        supported_durations=(4, 8, 12),
        max_reference_images=9,
        resolution="1080p",
        generate_audio=False,
        requested_generate_audio=True,
        has_audio_track=has_audio_track,
        audio_switch_controllable=audio_switch_controllable,
        voice_consistency="soft",
    )
    request_assets = resolve_reference_assets(project, proj_dir, unit)
    visual_basis_digest = reference_video_visual_basis_digest(
        project=project,
        project_path=proj_dir,
        unit=unit,
        request_assets=request_assets,
        candidate=candidate,
    )
    artifact_visual_basis = build_reference_video_artifact_visual_basis(
        unit=unit,
        request_assets=request_assets,
        style=project.get("style"),
        aspect_ratio="9:16",
    )
    artifact_speech_basis = build_video_speech_basis(admit_script_unit("video_units", unit).preparation)
    artifact_duration_basis = build_video_duration_basis(8)
    artifact_currency = VideoArtifactCurrencyFacts(
        episode=1,
        request_duration_seconds=8,
        visual_basis=artifact_visual_basis,
        speech_basis=artifact_speech_basis,
        duration_basis=artifact_duration_basis,
        video_basis=compose_video_artifact_basis(
            visual=artifact_visual_basis,
            speech=artifact_speech_basis,
            duration=artifact_duration_basis,
        ),
        voice_style_speakers=(),
        duration_tiers=(4, 8, 12),
        reference_image_limit=9,
        parent_version=0,
    )
    selected_version = versions.add_version(
        "reference_videos",
        "E1U1",
        "old visual",
        source_file=current,
        duration_seconds=8,
        visual_basis_digest=visual_basis_digest,
        execution_checkpoint_schema_version=3,
        execution_script_file="scripts/episode_1.json",
        execution_duration_seconds=8,
        execution_request_digest="d" * 64,
        execution_provider_media=[],
        artifact_video_currency=artifact_currency.to_dict(),
    )
    ArtifactManifest(ProjectArtifactManifestAdapter(proj_dir)).register_descriptor(
        ArtifactKey.episode_video(1, "E1U1"),
        artifact_path="reference_videos/E1U1.mp4",
        basis=artifact_currency.video_descriptor,
    )
    versions_before = (proj_dir / "versions" / "versions.json").read_bytes()

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(script_path.read_text(encoding="utf-8"))
    _wire_locked_script(fake_pm)
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    fake_generator = MagicMock()
    fake_generator.versions = versions
    fake_generator.generate_video_async = AsyncMock()
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="openai",
        backend_model="sora-2",
        max_refs=9,
        max_duration=12,
        supported_durations=(4, 8, 12),
    )

    async def _materialize(**kwargs):
        options = kwargs["options"]
        preparation = admit_script_unit("video_units", kwargs["unit"]).preparation
        delivery = prepare_narration_delivery(
            delivery="use_tts",
            preparation=preparation,
            artifact_path="audio/segment_E1U1.wav",
            settings=TtsSynthesisSettings("fake-audio", "tts-model", "voice", None),
            evidence=NarrationAudioEvidence(
                comparison=ArtifactComparison(
                    status=ArtifactStatus.CURRENT,
                    artifact_path="audio/segment_E1U1.wav",
                ),
                present=True,
                duration_seconds=6.2,
            ),
        )
        return replace(
            options,
            current_tts_duration_seconds=delivery.duration_floor,
            narration_preparation=delivery,
        )

    monkeypatch.setattr(rvt, "prepare_current_reference_video_request_options", _materialize)
    monkeypatch.setattr(rvt, "tts_task_in_progress", AsyncMock(return_value=False))
    monkeypatch.setattr(
        "server.services.narration_delivery_tasks.probe_existing_media_duration_seconds",
        AsyncMock(return_value=8.0),
    )
    fake_queue = MagicMock()
    monkeypatch.setattr(rvt, "get_generation_queue", lambda: fake_queue)

    result = await rvt.execute_reference_video_task(
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
        task_id="task-reuse",
    )

    assert result["reused_existing"] is True
    assert result["version"] == selected_version
    assert result["request_duration_seconds"] == 8
    fake_generator.generate_video_async.assert_not_awaited()
    assert script_path.read_bytes() == script_before
    assert (proj_dir / "versions" / "versions.json").read_bytes() == versions_before
    assert current.read_bytes() == b"existing-paid-video"


async def test_execute_reference_video_task_persists_effective_duration_when_rounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """取档偏移时 checkpoint 冻结实际申请秒数，enqueue payload 保持无内容快照。"""
    proj_dir = _write_project(tmp_path)
    script_path = proj_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["video_units"][0]["text"] = "@张三 推门"
    script["video_units"][0]["duration_seconds"] = 5
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    from lib.reference_video.execution_checkpoint import ReferenceSubmissionCheckpoint
    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(script_path.read_text(encoding="utf-8"))
    _wire_locked_script(fake_pm)
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    async def _fake_generate_video_async(**kwargs):
        await kwargs["before_submit"](31)
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00")
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
        max_duration=12,
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
    """未偏移时 checkpoint 也冻结 unit 实际时长，resume 不读当前 project 默认值。"""
    proj_dir = _write_project(tmp_path)

    from lib.reference_video.execution_checkpoint import ReferenceSubmissionCheckpoint
    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(
        (proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")
    )
    _wire_locked_script(fake_pm)
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    async def _fake_generate_video_async(**kwargs):
        await kwargs["before_submit"](32)
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00")
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
        max_duration=12,
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
    """当前投影解析出的 registry/provider/backend 身份在 provider submit 前原子冻结。"""
    proj_dir = _write_project(tmp_path)

    from lib.reference_video.execution_checkpoint import ReferenceSubmissionCheckpoint
    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(
        (proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")
    )
    _wire_locked_script(fake_pm)
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    events: list[str] = []

    async def _fake_generate_video_async(**kwargs):
        await kwargs["before_submit"](33)
        events.append("provider_submit")
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    # 族别名场景：registry provider_id 与 backend_name 不同，写回的必须是 registry 身份
    # （provider_id 列的既有口径，claim 池过滤与 resume 锁定都按它查）。
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
    assert checkpoint.capability == "r2v"
    assert events == ["checkpoint", "provider_submit"]


@pytest.mark.asyncio
async def test_execute_reference_video_task_stages_actual_request_and_checkpoints_at_submit_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from lib.artifact_manifest import ArtifactKey, ProjectArtifactManifestAdapter
    from lib.reference_video.execution_checkpoint import ReferenceSubmissionCheckpoint
    from server.services import reference_video_tasks as rvt

    proj_dir = _write_project(tmp_path)
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
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    events: list[str] = []
    submitted: dict[str, Any] = {}
    real_visual_basis = rvt.reference_video_visual_basis_digest
    captured_basis_kwargs: dict[str, Any] = {}

    def _capture_live_visual_basis(**kwargs):
        captured_basis_kwargs.update(kwargs)
        digest = real_visual_basis(**kwargs)
        submitted["initial_live_visual_basis"] = digest
        return digest

    monkeypatch.setattr(rvt, "reference_video_visual_basis_digest", _capture_live_visual_basis)

    async def _edit_source_before_staging(project_path, task_id, inputs):
        """经 ``stage_media_for_task`` 注入：在现摘要与暂存之间改写源文件，其余照真实暂存跑。"""
        if inputs:
            inputs[0].path.write_bytes(b"edited-between-live-basis-and-staging")
        return await rvt.stage_provider_media_for_task(project_path, task_id, inputs, stage=rvt.stage_provider_media)

    async def _fake_generate_video_async(**kwargs):
        submitted.update(kwargs)
        assert kwargs["formal_output"] is True
        assert all(".arcreel/tasks/task-submit/provider_media/" in str(path) for path in kwargs["reference_images"])
        expected_staged_basis = real_visual_basis(**captured_basis_kwargs)
        assert kwargs["visual_basis_digest"] == expected_staged_basis
        assert kwargs["visual_basis_digest"] != submitted["initial_live_visual_basis"]
        submitted["checkpoint_metadata"] = await kwargs["before_submit"](73)
        events.append("provider_submit")
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"paid-video")
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
    monkeypatch.setattr(rvt, "extract_video_thumbnail", AsyncMock(return_value=False))

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
    checkpoint = ReferenceSubmissionCheckpoint.from_json(persisted["raw"])
    assert persisted["task_id"] == "task-submit"
    assert persisted["provider_id"] == "ark"
    assert checkpoint.api_call_id == 73
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
    assert metadata["execution_api_call_id"] == checkpoint.api_call_id
    assert metadata["execution_request_digest"] == checkpoint.request_digest
    assert metadata["execution_prompt_sha256"] == checkpoint.prompt_sha256
    assert metadata["execution_visual_basis_digest"] == checkpoint.visual_basis_digest
    assert checkpoint.artifact_currency is not None
    assert metadata["artifact_video_currency"] == checkpoint.artifact_currency.to_dict()
    assert ProjectArtifactManifestAdapter(proj_dir).get_entry(ArtifactKey.episode_video(1, "E1U1")) is None
    assert not (proj_dir / ".arcreel" / "tasks" / "task-submit" / "provider_media").exists()

    async def _cancel_after_staging(**_kwargs):
        assert (proj_dir / ".arcreel" / "tasks" / "task-cancel" / "provider_media").is_dir()
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
        assert (proj_dir / ".arcreel" / "tasks" / "task-checkpoint-failure" / "provider_media").is_dir()
        await kwargs["before_submit"](74)
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
