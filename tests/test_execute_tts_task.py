"""execute_tts_task 执行链单测：文本来源三分支 / 写回 narration_audio /
_get_or_create_audio_backend 缓存与自定义供应商路径 /
compute_affected_fingerprints tts 分支 / 任务注册表。"""

from __future__ import annotations

import asyncio
import copy
import json
import math
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from lib.artifact_activation import activate_artifact_target_state
from lib.artifact_manifest import (
    ArtifactKey,
    ArtifactManifest,
    ArtifactStatus,
    ProjectArtifactManifestAdapter,
)
from lib.config.resolver import ConfigResolver, ProviderModel
from lib.generation_queue import CompensableGenerationResult
from lib.narration_delivery import TtsSynthesisSettings, build_narration_audio_basis, register_narration_audio
from lib.script_editor import resolve_items
from lib.speech_composition import admit_script_unit
from lib.version_manager import PaidVersionCommit
from server.services import generation_context, generation_tasks
from server.services.generation_context import AudioLaneResult, GenerationContext

pytestmark = pytest.mark.unit


def _audio_ctx(
    generator,
    *,
    voice="Cherry",
    speed=None,
    configured_model="qwen3-tts-flash",
    backend_model="qwen3-tts-flash",
):
    """把 audio lane 解析产物拼成假 GenerationContext，替换 resolve_generation_context 单点。"""
    ctx = GenerationContext(
        generator=generator,
        audio_lane=AudioLaneResult(
            provider_model=ProviderModel("dashscope", configured_model),
            backend_name="dashscope",
            backend_model=backend_model,
            narration_voice=voice,
            narration_speed=speed,
            voices=(),
        ),
    )

    async def _resolve(*args, **kwargs):
        assert kwargs.get("audio") is not None
        assert kwargs.get("image") is None
        assert kwargs.get("video") is None
        return ctx

    return _resolve


class _FakePM:
    def __init__(self, project_path: Path):
        self.project_path = project_path
        self.project: dict[str, Any] = {"name": "demo", "content_mode": "narration"}
        self.script = {
            "episode": 1,
            "content_mode": "narration",
            "segments": [
                {
                    "segment_id": "E1S01",
                    "novel_text": "却说天下大势，分久必合，合久必分。",
                    "video_prompt": {},
                },
                {"segment_id": "E1S02", "novel_text": "   ", "video_prompt": {}},
            ],
        }
        self.updated_assets = []
        self.rebind_on_next_lock: str | None = None
        self.episode_lock_active = False

    def load_project(self, project_name):
        return self.project

    def get_project_path(self, project_name):
        return self.project_path

    def load_script(self, project_name, script_file):
        return self.script

    def update_scene_asset(self, **kwargs):
        self.updated_assets.append(kwargs)

    @contextmanager
    def locked_episode_script(self, project_name, resolve_script_file, *, validate=True, on_commit=None):
        del project_name, validate
        if self.rebind_on_next_lock is not None:
            self.project["episodes"][0]["script_file"] = self.rebind_on_next_lock
            self.rebind_on_next_lock = None
        resolve_script_file(self.project)
        before = copy.deepcopy(self.script)
        try:
            self.episode_lock_active = True
            yield self.script
            if on_commit is not None:
                on_commit(self.project_path / "scripts" / "episode_1.json")
        except BaseException:
            self.script = before
            raise
        finally:
            self.episode_lock_active = False

    @staticmethod
    def update_scene_status(item):
        assets = item.setdefault("generated_assets", {})
        assets["status"] = "completed" if assets.get("video_clip") else "pending"


class _FakeAudioGenerator:
    def __init__(self, project_path: Path):
        self.project_path = project_path
        self.audio_calls = []
        self.versions = self
        self.previous_formal: bytes | None = None
        self.rejected_versions: list[int] = []
        self.version_records: list[dict[str, Any]] = []
        self.current_version = 0

    async def generate_audio_async(self, **kwargs):
        if before_submit := kwargs.get("before_submit"):
            await before_submit()
        self.audio_calls.append(kwargs)
        output = self.project_path / "audio" / f"segment_{kwargs['resource_id']}.wav"
        output.parent.mkdir(parents=True, exist_ok=True)
        staged = output.with_name(f".{output.stem}.staged{output.suffix}")
        staged.write_bytes(b"RIFF-current-audio")
        if before_commit := kwargs.get("before_commit"):
            await before_commit(staged)
        if commit_staged := kwargs.get("commit_staged"):
            committed = await asyncio.to_thread(commit_staged, staged, output)
            version = committed.version if isinstance(committed, PaidVersionCommit) else committed
        else:
            staged.replace(output)
            version = 3
        return output, version

    def commit_staged_version(self, *, staged_file, current_file, on_commit=None, **kwargs):
        del kwargs
        previous = current_file.read_bytes() if current_file.is_file() else None
        self.previous_formal = previous
        staged_file.replace(current_file)
        try:
            if on_commit is not None:
                on_commit()
        except BaseException:
            if previous is None:
                current_file.unlink(missing_ok=True)
            else:
                current_file.write_bytes(previous)
            raise
        return 3

    def commit_staged_paid_version(
        self,
        *,
        resource_type,
        resource_id,
        prompt,
        staged_file,
        current_file,
        select_current,
        on_select=None,
        **metadata,
    ):
        assert resource_type == "audio"
        previous = current_file.read_bytes() if current_file.is_file() else None
        self.previous_formal = previous
        version = max((record["version"] for record in self.version_records), default=2) + 1
        version_rel = f"versions/audio/{resource_id}_v{version}.wav"
        version_file = self.project_path / version_rel
        version_file.parent.mkdir(parents=True, exist_ok=True)
        version_file.write_bytes(staged_file.read_bytes())
        record = {
            "version": version,
            "file": version_rel,
            "prompt": prompt,
            "created_at": "2026-06-01T00:00:00Z",
            **metadata,
        }
        self.version_records.append(record)

        should_select = select_current() if callable(select_current) else select_current
        if not should_select:
            staged_file.unlink(missing_ok=True)
            return PaidVersionCommit(version=version, selected=False)

        staged_file.replace(current_file)
        try:
            if on_select is not None:
                on_select()
        except BaseException:
            if previous is None:
                current_file.unlink(missing_ok=True)
            else:
                current_file.write_bytes(previous)
            raise
        self.current_version = version
        return PaidVersionCommit(version=version, selected=True)

    def reject_current_version(self, resource_type, resource_id, *, rejected_version, current_file, **kwargs):
        del resource_type, resource_id
        self.rejected_versions.append(rejected_version)
        if self.previous_formal is None:
            current_file.unlink(missing_ok=True)
        else:
            current_file.write_bytes(self.previous_formal)
        if on_reject := kwargs.get("on_reject"):
            on_reject()
        return True

    def restore_version(self, resource_type, resource_id, version, current_file):
        del resource_type, resource_id, version, current_file
        return {"restored_version": 3}

    def get_versions(self, resource_type, resource_id):
        del resource_type, resource_id
        if self.version_records:
            return {
                "current_version": self.current_version,
                "versions": [
                    {**record, "is_current": record["version"] == self.current_version}
                    for record in self.version_records
                ],
            }
        return {"current_version": 3, "versions": [{"version": 3, "created_at": "2026-06-01T00:00:00Z"}]}


@pytest.fixture
def tts_env(monkeypatch, tmp_path):
    pm = _FakePM(tmp_path / "projects" / "demo")
    pm.project_path.mkdir(parents=True)
    pm.project.update(
        {
            "schema_version": 7,
            "generation_mode": "storyboard",
            "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
        }
    )
    (pm.project_path / "project.json").write_text(
        json.dumps(pm.project, ensure_ascii=False),
        encoding="utf-8",
    )
    (pm.project_path / "scripts").mkdir()
    (pm.project_path / "scripts" / "episode_1.json").write_text(
        json.dumps(pm.script, ensure_ascii=False),
        encoding="utf-8",
    )
    gen = _FakeAudioGenerator(pm.project_path)
    monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: pm)
    monkeypatch.setattr(generation_tasks, "resolve_generation_context", _audio_ctx(gen))

    async def _duration(_path: Path) -> float:
        return 5.25

    monkeypatch.setattr(generation_tasks, "probe_existing_audio_duration_seconds", _duration)
    monkeypatch.setattr(
        generation_tasks,
        "active_narrated_video_resource_ids",
        AsyncMock(return_value=frozenset()),
    )
    return pm, gen


class TestExecuteTtsTask:
    async def test_active_manifest_without_script_claim_blocks_tts_before_provider(self, tts_env):
        pm, gen = tts_env
        assert activate_artifact_target_state(pm.project_path, bump_schema=True) is True
        pm.project["schema_version"] = 8
        assert ProjectArtifactManifestAdapter(pm.project_path).get_entry(ArtifactKey.episode_script(1)) is None

        with pytest.raises(ValueError, match="episode script is not registered"):
            await generation_tasks.execute_tts_task(
                "demo",
                "E1S01",
                {"script_file": "episode_1.json"},
            )

        assert gen.audio_calls == []

    async def test_activation_without_script_claim_blocks_tts_before_provider(self, tts_env, monkeypatch):
        pm, gen = tts_env
        original_generate = gen.generate_audio_async
        provider_submissions: list[str] = []

        async def _activate_before_submit(**kwargs):
            before_submit = kwargs["before_submit"]

            async def _activate_then_admit() -> None:
                assert activate_artifact_target_state(pm.project_path, bump_schema=True) is True
                assert ProjectArtifactManifestAdapter(pm.project_path).get_entry(ArtifactKey.episode_script(1)) is None
                await before_submit()
                provider_submissions.append("submitted")

            return await original_generate(**{**kwargs, "before_submit": _activate_then_admit})

        monkeypatch.setattr(gen, "generate_audio_async", _activate_before_submit)

        with pytest.raises(ValueError, match="no longer registered"):
            await generation_tasks.execute_tts_task(
                "demo",
                "E1S01",
                {"script_file": "episode_1.json"},
            )

        assert provider_submissions == []
        assert gen.audio_calls == []

    async def test_legacy_script_bytes_replaced_before_provider_are_rejected(self, tts_env, monkeypatch):
        pm, gen = tts_env
        original_generate = gen.generate_audio_async
        provider_submissions: list[str] = []

        async def _replace_script_before_submit(**kwargs):
            before_submit = kwargs["before_submit"]

            async def _replace_then_admit() -> None:
                replacement = copy.deepcopy(pm.script)
                replacement["segments"][0]["novel_text"] = "并发保存后的另一版旁白。"
                (pm.project_path / "scripts" / "episode_1.json").write_text(
                    json.dumps(replacement, ensure_ascii=False),
                    encoding="utf-8",
                )
                await before_submit()
                provider_submissions.append("submitted")

            return await original_generate(**{**kwargs, "before_submit": _replace_then_admit})

        monkeypatch.setattr(gen, "generate_audio_async", _replace_script_before_submit)

        with pytest.raises(ValueError, match="changed since it was selected"):
            await generation_tasks.execute_tts_task(
                "demo",
                "E1S01",
                {"script_file": "episode_1.json"},
            )

        assert provider_submissions == []
        assert gen.audio_calls == []

    async def test_active_use_tts_video_blocks_regeneration_before_provider_call(self, tts_env, monkeypatch):
        from lib.api_errors import ConflictError

        _pm, gen = tts_env
        monkeypatch.setattr(
            generation_tasks,
            "active_narrated_video_resource_ids",
            AsyncMock(return_value=frozenset({"E1S01"})),
        )

        with pytest.raises(ConflictError) as exc_info:
            await generation_tasks.execute_tts_task(
                "demo",
                "E1S01",
                {"script_file": "episode_1.json"},
            )

        assert exc_info.value.key == "tts_conflicts_with_active_narrated_video"
        assert gen.audio_calls == []

    async def test_explicit_payload_text(self, tts_env):
        pm, gen = tts_env
        result = await generation_tasks.execute_tts_task("demo", "E1S01", {"text": "你好世界"})
        assert result == {
            "version": 3,
            "file_path": "versions/audio/E1S01_v3.wav",
            "created_at": "2026-06-01T00:00:00Z",
            "resource_type": "audio",
            "resource_id": "E1S01",
            "duration_seconds": 5.25,
            "tts_basis_digest": None,
            "selected_current": False,
        }
        call = gen.audio_calls[0]
        assert call["text"] == "你好世界"
        assert call["voice"] == "Cherry"
        assert call["resource_id"] == "E1S01"
        assert not (pm.project_path / "audio" / "segment_E1S01.wav").exists()
        assert gen.version_records[-1]["tts_basis_digest"] is None
        # 无 script_file → 只保存付费历史，不写回 narration_audio 或抢占 current
        assert pm.updated_assets == []

    async def test_text_from_script_segment_and_writeback(self, tts_env):
        pm, gen = tts_env
        result = await generation_tasks.execute_tts_task("demo", "E1S01", {"script_file": "episode_1.json"})
        assert gen.audio_calls[0]["text"] == "却说天下大势，分久必合，合久必分。"
        assert pm.script["segments"][0]["generated_assets"]["narration_audio"] == "audio/segment_E1S01.wav"
        assert gen.version_records[-1]["artifact_episode"] == 1
        assert gen.version_records[-1]["artifact_audio_basis"]["digest"] == result["tts_basis_digest"]
        assert gen.version_records[-1]["tts_actual_duration_seconds"] == 5.25
        assert gen.version_records[-1]["execution_script_file"] == "episode_1.json"

        settings = TtsSynthesisSettings(
            provider_id="dashscope",
            model_id="qwen3-tts-flash",
            voice="Cherry",
            speed=None,
        )
        items, _id_field, kind = resolve_items(pm.script)
        basis = build_narration_audio_basis(admit_script_unit(kind, items[0]).preparation, settings)
        comparison = ArtifactManifest(ProjectArtifactManifestAdapter(pm.project_path)).compare(
            ArtifactKey.episode_audio(1, "E1S01"),
            artifact_path="audio/segment_E1S01.wav",
            basis=basis,
        )
        assert comparison.status is ArtifactStatus.CURRENT

    async def test_script_task_uses_latest_canonical_speech_instead_of_payload_snapshot(self, tts_env):
        _pm, gen = tts_env

        await generation_tasks.execute_tts_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json", "prompt": "stale queued text"},
        )

        assert gen.audio_calls[0]["text"] == "却说天下大势，分久必合，合久必分。"

    async def test_script_rebind_before_commit_preserves_old_formal_audio(self, tts_env):
        pm, gen = tts_env
        formal = pm.project_path / "audio" / "segment_E1S01.wav"
        formal.parent.mkdir(parents=True, exist_ok=True)
        formal.write_bytes(b"paid-old-audio")
        pm.project["episodes"] = [
            {"episode": 1, "script_file": "scripts/episode_1.json"},
        ]

        pm.rebind_on_next_lock = "scripts/episode_1_rebound.json"

        result = await generation_tasks.execute_tts_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json"},
            task_id="tts-task",
        )

        assert formal.read_bytes() == b"paid-old-audio"
        assert result["selected_current"] is False
        assert (pm.project_path / result["file_path"]).read_bytes() == b"RIFF-current-audio"
        assert "generated_assets" not in pm.script["segments"][0]

    async def test_project_transaction_failure_still_archives_paid_audio(
        self,
        tts_env,
        monkeypatch: pytest.MonkeyPatch,
    ):
        pm, gen = tts_env
        formal = pm.project_path / "audio" / "segment_E1S01.wav"
        formal.parent.mkdir(parents=True, exist_ok=True)
        formal.write_bytes(b"paid-old-audio")
        pm.project["episodes"] = [{"episode": 1, "script_file": "scripts/episode_1.json"}]

        @contextmanager
        def _failed_transaction(*_args, **_kwargs):
            raise OSError("project snapshot unavailable")
            yield

        monkeypatch.setattr(pm, "locked_episode_script", _failed_transaction)

        with pytest.raises(OSError, match="project snapshot unavailable"):
            await generation_tasks.execute_tts_task(
                "demo",
                "E1S01",
                {"script_file": "episode_1.json"},
                task_id="tts-task",
            )

        assert formal.read_bytes() == b"paid-old-audio"
        assert len(gen.version_records) == 1
        assert (pm.project_path / gen.version_records[0]["file"]).read_bytes() == b"RIFF-current-audio"
        assert gen.current_version == 0

    async def test_narration_change_before_commit_keeps_paid_history_without_taking_current(
        self,
        tts_env,
        monkeypatch: pytest.MonkeyPatch,
    ):
        pm, gen = tts_env
        formal = pm.project_path / "audio" / "segment_E1S01.wav"
        formal.parent.mkdir(parents=True, exist_ok=True)
        formal.write_bytes(b"paid-old-audio")
        pm.script["segments"][0]["generated_assets"] = {
            "narration_audio": "audio/segment_E1S01.wav",
            "status": "pending",
        }
        items, _id_field, kind = resolve_items(pm.script)
        settings = TtsSynthesisSettings("dashscope", "qwen3-tts-flash", "Cherry", None)
        register_narration_audio(
            project_path=pm.project_path,
            episode=1,
            preparation=admit_script_unit(kind, items[0]).preparation,
            settings=settings,
        )
        adapter = ProjectArtifactManifestAdapter(pm.project_path)
        prior_entry = adapter.get_entry(ArtifactKey.episode_audio(1, "E1S01"))
        original_generate = gen.generate_audio_async

        async def _edit_before_commit(**kwargs):
            original_commit = kwargs["commit_staged"]

            def _commit_after_edit(staged_path: Path, output_path: Path) -> int:
                pm.script["segments"][0]["novel_text"] = "合成期间并发改写的旁白。"
                return original_commit(staged_path, output_path)

            return await original_generate(**{**kwargs, "commit_staged": _commit_after_edit})

        monkeypatch.setattr(gen, "generate_audio_async", _edit_before_commit)

        result = await generation_tasks.execute_tts_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json"},
        )

        assert formal.read_bytes() == b"paid-old-audio"
        assert result["selected_current"] is False
        assert result["file_path"].startswith("versions/audio/")
        assert (pm.project_path / result["file_path"]).read_bytes() == b"RIFF-current-audio"
        assert pm.script["segments"][0]["novel_text"] == "合成期间并发改写的旁白。"
        assert pm.script["segments"][0]["generated_assets"] == {
            "narration_audio": "audio/segment_E1S01.wav",
            "status": "pending",
        }
        assert adapter.get_entry(ArtifactKey.episode_audio(1, "E1S01")) == prior_entry

    async def test_tts_settings_change_before_commit_keeps_paid_history_without_taking_current(
        self,
        tts_env,
        monkeypatch: pytest.MonkeyPatch,
    ):
        pm, gen = tts_env
        formal = pm.project_path / "audio" / "segment_E1S01.wav"
        formal.parent.mkdir(parents=True, exist_ok=True)
        formal.write_bytes(b"paid-old-audio")
        pm.script["segments"][0]["generated_assets"] = {
            "narration_audio": "audio/segment_E1S01.wav",
            "status": "pending",
        }
        items, _id_field, kind = resolve_items(pm.script)
        initial_settings = TtsSynthesisSettings("dashscope", "qwen3-tts-flash", "Cherry", None)
        register_narration_audio(
            project_path=pm.project_path,
            episode=1,
            preparation=admit_script_unit(kind, items[0]).preparation,
            settings=initial_settings,
        )
        adapter = ProjectArtifactManifestAdapter(pm.project_path)
        prior_entry = adapter.get_entry(ArtifactKey.episode_audio(1, "E1S01"))
        initial_resolver = _audio_ctx(gen, voice="Cherry")
        changed_resolver = _audio_ctx(gen, voice="Ada")
        calls = 0

        async def _settings_change(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                assert pm.episode_lock_active
                assert kwargs["project_path"] == pm.project_path
            resolver = initial_resolver if calls == 1 else changed_resolver
            return await resolver(*args, **kwargs)

        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _settings_change)

        result = await generation_tasks.execute_tts_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json"},
        )

        assert calls == 2
        assert result["selected_current"] is False
        assert formal.read_bytes() == b"paid-old-audio"
        assert (pm.project_path / result["file_path"]).read_bytes() == b"RIFF-current-audio"
        assert gen.version_records[-1]["tts_voice"] == "Cherry"
        assert adapter.get_entry(ArtifactKey.episode_audio(1, "E1S01")) == prior_entry

    async def test_reference_video_unit_uses_its_own_narrator_text_and_manifest_key(self, tts_env):
        pm, gen = tts_env
        pm.project.update({"content_mode": "ad", "generation_mode": "reference_video"})
        pm.script = {
            "episode": 1,
            "content_mode": "ad",
            "shots": [
                {
                    "shot_id": "E1S01",
                    "voiceover_text": "不属于参考视频单元的旧广告旁白。",
                }
            ],
            "video_units": [
                {
                    "unit_id": "E1U2",
                    "shots": [{"text": "镜头缓缓推进。\n{只属于第二单元的旁白。}"}],
                }
            ],
        }

        result = await generation_tasks.execute_tts_task("demo", "E1U2", {"script_file": "episode_1.json"})

        assert gen.audio_calls[0]["text"] == "只属于第二单元的旁白。"
        assert result["file_path"] == "audio/segment_E1U2.wav"
        assert result["duration_seconds"] == 5.25
        assert isinstance(result["tts_basis_digest"], str)
        assert pm.script["video_units"][0]["generated_assets"]["narration_audio"] == "audio/segment_E1U2.wav"

    async def test_reference_video_cancel_uses_video_units_when_ad_script_also_has_shots(self, tts_env):
        pm, gen = tts_env
        pm.project.update({"content_mode": "ad", "generation_mode": "reference_video"})
        prior_assets = {"narration_audio": "audio/prior-selection.wav", "status": "old-status"}
        pm.script = {
            "episode": 1,
            "content_mode": "ad",
            "shots": [
                {
                    "shot_id": "E1S01",
                    "voiceover_text": "不属于参考视频单元的旧广告旁白。",
                    "generated_assets": {"status": "decoy"},
                }
            ],
            "video_units": [
                {
                    "unit_id": "E1U2",
                    "shots": [{"text": "镜头缓缓推进。\n{参考视频单元旁白。}"}],
                    "generated_assets": copy.deepcopy(prior_assets),
                }
            ],
        }
        formal = pm.project_path / "audio" / "segment_E1U2.wav"
        formal.parent.mkdir(parents=True, exist_ok=True)
        formal.write_bytes(b"paid-old-audio")

        result = await generation_tasks.execute_tts_task(
            "demo",
            "E1U2",
            {"script_file": "episode_1.json"},
            task_id="tts-reference-task",
        )

        assert isinstance(result, CompensableGenerationResult)
        pm.script["video_units"][0]["generated_assets"]["video_clip"] = "reference_videos/E1U2.mp4"
        result.compensate_cancelled()

        assert formal.read_bytes() == b"paid-old-audio"
        assert gen.rejected_versions == [3]
        assert pm.script["video_units"][0]["generated_assets"] == {
            "narration_audio": "audio/prior-selection.wav",
            "video_clip": "reference_videos/E1U2.mp4",
            "status": "completed",
        }
        assert pm.script["shots"][0]["generated_assets"] == {"status": "decoy"}

    async def test_manifest_basis_tracks_actual_backend_model_identity(self, tts_env, monkeypatch):
        pm, gen = tts_env
        monkeypatch.setattr(
            generation_tasks,
            "resolve_generation_context",
            _audio_ctx(
                gen,
                configured_model="configured-tts-model",
                backend_model="backend-fallback-model",
            ),
        )

        await generation_tasks.execute_tts_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json"},
        )

        assert gen.audio_calls[0]["tts_model_id"] == "backend-fallback-model"
        settings = TtsSynthesisSettings(
            provider_id="dashscope",
            model_id="backend-fallback-model",
            voice="Cherry",
            speed=None,
        )
        items, _id_field, kind = resolve_items(pm.script)
        basis = build_narration_audio_basis(admit_script_unit(kind, items[0]).preparation, settings)
        comparison = ArtifactManifest(ProjectArtifactManifestAdapter(pm.project_path)).compare(
            ArtifactKey.episode_audio(1, "E1S01"),
            artifact_path="audio/segment_E1S01.wav",
            basis=basis,
        )
        assert comparison.status is ArtifactStatus.CURRENT

    @pytest.mark.parametrize("measured_duration", [None, 0.0, math.nan, math.inf])
    async def test_unmeasurable_staged_audio_keeps_old_formal_audio_script_and_basis(
        self,
        tts_env,
        monkeypatch,
        measured_duration: float | None,
    ):
        pm, gen = tts_env
        formal = pm.project_path / "audio" / "segment_E1S01.wav"
        formal.parent.mkdir(parents=True, exist_ok=True)
        formal.write_bytes(b"paid-old-audio")
        pm.script["segments"][0].setdefault("generated_assets", {})["narration_audio"] = "audio/segment_E1S01.wav"
        items, _id_field, kind = resolve_items(pm.script)
        settings = TtsSynthesisSettings("dashscope", "qwen3-tts-flash", "Cherry", None)
        old_basis = register_narration_audio(
            project_path=pm.project_path,
            episode=1,
            preparation=admit_script_unit(kind, items[0]).preparation,
            settings=settings,
        )
        pm.script["segments"][0]["novel_text"] = "这次后端写出了无法测量的音频。"

        async def _unmeasurable(_path: Path) -> float | None:
            return measured_duration

        monkeypatch.setattr(generation_tasks, "probe_existing_audio_duration_seconds", _unmeasurable)

        with pytest.raises(RuntimeError, match="duration is unavailable"):
            await generation_tasks.execute_tts_task(
                "demo",
                "E1S01",
                {"script_file": "episode_1.json"},
            )

        assert formal.read_bytes() == b"paid-old-audio"
        assert pm.script["segments"][0]["generated_assets"]["narration_audio"] == "audio/segment_E1S01.wav"
        assert len(gen.version_records) == 1
        assert (pm.project_path / gen.version_records[0]["file"]).read_bytes() == b"RIFF-current-audio"
        assert gen.current_version == 0
        comparison = ArtifactManifest(ProjectArtifactManifestAdapter(pm.project_path)).compare(
            ArtifactKey.episode_audio(1, "E1S01"),
            artifact_path="audio/segment_E1S01.wav",
            basis=old_basis,
        )
        assert comparison.status is ArtifactStatus.CURRENT

    @pytest.mark.parametrize("failure", [RuntimeError("manifest failed"), asyncio.CancelledError()])
    async def test_finalize_failure_or_cancellation_keeps_old_formal_audio_script_and_basis(
        self,
        tts_env,
        monkeypatch,
        failure: BaseException,
    ):
        pm, _gen = tts_env
        formal = pm.project_path / "audio" / "segment_E1S01.wav"
        formal.parent.mkdir(parents=True, exist_ok=True)
        formal.write_bytes(b"paid-old-audio")
        pm.script["segments"][0].setdefault("generated_assets", {})["narration_audio"] = "audio/segment_E1S01.wav"
        items, _id_field, kind = resolve_items(pm.script)
        old_preparation = admit_script_unit(kind, items[0]).preparation
        settings = TtsSynthesisSettings("dashscope", "qwen3-tts-flash", "Cherry", None)
        old_basis = register_narration_audio(
            project_path=pm.project_path,
            episode=1,
            preparation=old_preparation,
            settings=settings,
        )
        pm.script["segments"][0]["novel_text"] = "这是本次重生的新旁白。"

        def _fail_registration(**_kwargs):
            raise failure

        monkeypatch.setattr(generation_tasks, "register_narration_audio_transactionally", _fail_registration)

        with pytest.raises(type(failure)):
            await generation_tasks.execute_tts_task(
                "demo",
                "E1S01",
                {"script_file": "episode_1.json"},
            )

        assert formal.read_bytes() == b"paid-old-audio"
        assert pm.script["segments"][0]["generated_assets"]["narration_audio"] == "audio/segment_E1S01.wav"
        comparison = ArtifactManifest(ProjectArtifactManifestAdapter(pm.project_path)).compare(
            ArtifactKey.episode_audio(1, "E1S01"),
            artifact_path="audio/segment_E1S01.wav",
            basis=old_basis,
        )
        assert comparison.status is ArtifactStatus.CURRENT

    async def test_cancel_after_formal_commit_can_compensate_audio_script_and_basis(self, tts_env):
        pm, gen = tts_env
        formal = pm.project_path / "audio" / "segment_E1S01.wav"
        formal.parent.mkdir(parents=True, exist_ok=True)
        formal.write_bytes(b"paid-old-audio")
        prior_assets = {"narration_audio": "audio/prior-selection.wav", "status": "old-status"}
        pm.script["segments"][0]["generated_assets"] = copy.deepcopy(prior_assets)
        items, _id_field, kind = resolve_items(pm.script)
        settings = TtsSynthesisSettings("dashscope", "qwen3-tts-flash", "Cherry", None)
        old_basis = register_narration_audio(
            project_path=pm.project_path,
            episode=1,
            preparation=admit_script_unit(kind, items[0]).preparation,
            settings=settings,
        )
        pm.script["segments"][0]["novel_text"] = "取消前刚合成的新旁白。"

        result = await generation_tasks.execute_tts_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json"},
            task_id="tts-task",
        )

        assert isinstance(result, CompensableGenerationResult)
        assert formal.read_bytes() == b"RIFF-current-audio"
        pm.script["segments"][0]["generated_assets"]["video_clip"] = "videos/concurrent.mp4"
        result.compensate_cancelled()

        assert formal.read_bytes() == b"paid-old-audio"
        assert gen.rejected_versions == [3]
        assert pm.script["segments"][0]["generated_assets"] == {
            "narration_audio": "audio/prior-selection.wav",
            "video_clip": "videos/concurrent.mp4",
            "status": "completed",
        }
        comparison = ArtifactManifest(ProjectArtifactManifestAdapter(pm.project_path)).compare(
            ArtifactKey.episode_audio(1, "E1S01"),
            artifact_path="audio/segment_E1S01.wav",
            basis=old_basis,
        )
        assert comparison.status is ArtifactStatus.CURRENT

    async def test_cancel_after_first_tts_commit_removes_the_new_script_binding(self, tts_env):
        pm, gen = tts_env
        pm.script["segments"][0]["generated_assets"] = {"status": "pending"}

        result = await generation_tasks.execute_tts_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json"},
            task_id="first-tts-task",
        )

        assert isinstance(result, CompensableGenerationResult)
        result.compensate_cancelled()

        assert not (pm.project_path / "audio" / "segment_E1S01.wav").exists()
        assert "narration_audio" not in pm.script["segments"][0]["generated_assets"]

    async def test_cancel_after_episode_rebind_still_rejects_selected_tts(self, tts_env):
        pm, gen = tts_env

        result = await generation_tasks.execute_tts_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json"},
            task_id="rebound-tts-task",
        )
        pm.project["episodes"] = [{"episode": 1, "script_file": "scripts/rebound_episode_1.json"}]

        assert isinstance(result, CompensableGenerationResult)
        result.compensate_cancelled()

        assert gen.rejected_versions == [3]
        assert not (pm.project_path / "audio" / "segment_E1S01.wav").exists()
        assert ProjectArtifactManifestAdapter(pm.project_path).get_entry(ArtifactKey.episode_audio(1, "E1S01")) is None

    async def test_cancel_after_unit_removal_still_rejects_selected_tts(self, tts_env):
        pm, gen = tts_env

        result = await generation_tasks.execute_tts_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json"},
            task_id="removed-unit-tts-task",
        )
        pm.script["segments"].clear()

        assert isinstance(result, CompensableGenerationResult)
        result.compensate_cancelled()

        assert gen.rejected_versions == [3]
        assert not (pm.project_path / "audio" / "segment_E1S01.wav").exists()
        assert ProjectArtifactManifestAdapter(pm.project_path).get_entry(ArtifactKey.episode_audio(1, "E1S01")) is None
        assert pm.script["segments"] == []

    async def test_narration_speed_passed_to_generator(self, tts_env, monkeypatch):
        pm, gen = tts_env
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _audio_ctx(gen, speed=1.5))
        await generation_tasks.execute_tts_task("demo", "E1S01", {"text": "你好"})
        assert gen.audio_calls[0]["speed"] == 1.5

    async def test_unset_narration_speed_passes_none(self, tts_env):
        pm, gen = tts_env
        await generation_tasks.execute_tts_task("demo", "E1S01", {"text": "你好"})
        assert gen.audio_calls[0]["speed"] is None

    async def test_no_text_no_script_file_raises(self, tts_env):
        with pytest.raises(ValueError, match="payload.text 或 payload.script_file"):
            await generation_tasks.execute_tts_task("demo", "E1S01", {})

    async def test_segment_not_found_raises(self, tts_env):
        with pytest.raises(ValueError, match="segment not found"):
            await generation_tasks.execute_tts_task("demo", "NOPE", {"script_file": "episode_1.json"})

    async def test_blank_novel_text_raises(self, tts_env):
        with pytest.raises(ValueError, match="无可合成的旁白文本"):
            await generation_tasks.execute_tts_task("demo", "E1S02", {"script_file": "episode_1.json"})

    def test_tts_registered_as_a_skeleton_driven_task(self):
        assert generation_tasks._TASK_EXECUTORS["tts"] is generation_tasks.execute_tts_task
        assert generation_tasks._SKELETON_DRIVEN_TASK_ACTIONS["tts"] == "tts_ready"


class TestGetOrCreateAudioBackend:
    """audio backend 构造统一委托 assemble_backend；缓存留在调用方编排层。"""

    async def test_custom_provider_routes_through_assemble(self, monkeypatch):
        sentinel = object()
        calls = []

        async def _fake_assemble(*, provider_id, media_type, model_id, resolver, rate_limiter=None):
            calls.append((provider_id, media_type, model_id))
            return sentinel

        monkeypatch.setattr(generation_context, "assemble_backend", _fake_assemble)
        monkeypatch.setattr(generation_context, "_backend_cache", generation_context._BackendCache())

        resolver = cast(ConfigResolver, None)
        b1 = await generation_context._get_or_create_audio_backend("custom-3", {"model": "tts-1"}, resolver)
        b2 = await generation_context._get_or_create_audio_backend("custom-3", {"model": "tts-1"}, resolver)

        assert b1 is sentinel and b2 is sentinel
        assert calls == [("custom-3", "audio", "tts-1")], "第二次调用须命中缓存，不再重建 backend"

    async def test_builtin_created_and_cached(self, monkeypatch):
        created = []
        sentinel = object()

        async def _fake_assemble(*, provider_id, media_type, model_id, resolver, rate_limiter=None):
            created.append((provider_id, media_type, model_id))
            return sentinel

        monkeypatch.setattr(generation_context, "assemble_backend", _fake_assemble)
        monkeypatch.setattr(generation_context, "_backend_cache", generation_context._BackendCache())

        resolver = cast(ConfigResolver, None)
        b1 = await generation_context._get_or_create_audio_backend(
            "dashscope", {}, resolver, default_audio_model="qwen3-tts-flash"
        )
        b2 = await generation_context._get_or_create_audio_backend(
            "dashscope", {}, resolver, default_audio_model="qwen3-tts-flash"
        )
        assert b1 is sentinel and b2 is sentinel
        assert created == [("dashscope", "audio", "qwen3-tts-flash")], "第二次调用须命中缓存，不再重建 backend"

    async def test_payload_model_overrides_default(self, monkeypatch):
        calls = []

        async def _fake_assemble(*, provider_id, media_type, model_id, resolver, rate_limiter=None):
            calls.append(model_id)
            return object()

        monkeypatch.setattr(generation_context, "assemble_backend", _fake_assemble)
        monkeypatch.setattr(generation_context, "_backend_cache", generation_context._BackendCache())

        await generation_context._get_or_create_audio_backend(
            "dashscope",
            {"model": "explicit-model"},
            cast(ConfigResolver, None),
            default_audio_model="fallback-model",
        )
        assert calls == ["explicit-model"]


class TestComputeAffectedFingerprintsTts:
    def test_tts_includes_audio_path(self, monkeypatch, tmp_path):
        project_path = tmp_path / "projects" / "demo"
        (project_path / "audio").mkdir(parents=True)
        (project_path / "audio" / "segment_E1S01.wav").write_bytes(b"RIFF")
        pm = _FakePM(project_path)
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: pm)

        fp = generation_tasks.compute_affected_fingerprints("demo", "tts", "E1S01")
        assert "audio/segment_E1S01.wav" in fp
