"""Shared fixtures and helpers for reference_video_tasks tests."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from lib.project_migrations.v8_to_v9_reference_unit_text import migrate_v8_to_v9
from lib.reference_video.request_projection import resolve_reference_assets


def _load_project_and_unit(proj_dir: Path, unit_id: str) -> tuple[dict, dict]:
    project = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    script = json.loads((proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8"))
    unit = next(u for u in script["video_units"] if u["unit_id"] == unit_id)
    return project, unit


_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x04\x00\x00\x00\x04"
    b"\x08\x02\x00\x00\x00&\x93\t)\x00\x00\x00\x13IDATx\x9cc<\x91b\xc4\x00"
    b"\x03Lp\x16^\x0e\x00E\xf6\x01f\xac\xf5\x15\xfa\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _write_project(tmp_path: Path, *, register_script: bool = True) -> Path:
    project = {
        "title": "T",
        "content_mode": "narration",
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
        "content_mode": "narration",
        "generation_mode": "reference_video",
        "summary": "x",
        "novel": {"title": "t", "chapter": "c"},
        "duration_seconds": 8,
        "video_units": [
            {
                "unit_id": "E1U1",
                "text": "@张三 推门，走进 @酒馆",
                "duration_seconds": 3,
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
    (proj_dir / "characters" / "张三.png").write_bytes(_TINY_PNG)
    (proj_dir / "scenes").mkdir()
    (proj_dir / "scenes" / "酒馆.png").write_bytes(_TINY_PNG)
    _activate_project_manifest(proj_dir, register_script=register_script)
    return proj_dir


def _register_asset_sheet(proj_dir: Path, asset_type: str, name: str, relative_path: str) -> None:
    """把新增资产补成生产形态：sheet 文件在盘上，且在产物清单里登记。

    调用前 project.json 必须已经写盘并带上该资产的 sheet 指针——清单登记的依据来自
    project.json 的当前指针，只改内存里的 project 副本不构成一个可登记的资产。
    """

    from lib.artifact_activation import register_current_artifact
    from lib.artifact_manifest import ArtifactKey

    path = proj_dir / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(_TINY_PNG)
    register_current_artifact(proj_dir, ArtifactKey.asset_sheet(asset_type, name))


def _activate_project_manifest(proj_dir: Path, *, register_script: bool = True) -> None:
    """Activate the fixture through the production v7 -> v8 boundary, then finish the chain."""

    from lib.artifact_activation import activate_artifact_target_state
    from lib.artifact_manifest import (
        ArtifactBasis,
        ArtifactKey,
        ArtifactManifest,
        ProjectArtifactManifestAdapter,
    )

    project_path = proj_dir / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project["schema_version"] = 7
    project_path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    assert activate_artifact_target_state(proj_dir, bump_schema=True) is True
    # 清单激活只落到 v8；产物读写要求当前 schema，故补齐剩余迁移。
    migrate_v8_to_v9(proj_dir)
    if register_script:
        ArtifactManifest(ProjectArtifactManifestAdapter(proj_dir)).register(
            ArtifactKey.episode_script(1),
            artifact_path="scripts/episode_1.json",
            basis=ArtifactBasis.build("test/episode-script", kind_version=1, inputs={}),
        )


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


def _resolved_names(project: dict, proj_dir: Path, text: str) -> list[str]:
    return [asset.path.name for asset in resolve_reference_assets(project, proj_dir, {"text": text})]
