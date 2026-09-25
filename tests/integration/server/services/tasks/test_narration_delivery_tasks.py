"""参考视频复用与请求投影共享事实，真实媒体和清单验证免费复用判定。"""

import json

from lib.artifacts.artifact_manifest import (
    ArtifactKey,
    ArtifactManifest,
    ProjectArtifactManifestAdapter,
    compose_video_artifact_basis,
)
from lib.artifacts.version_manager import VersionManager
from lib.artifacts.video_artifact_facts import VideoArtifactCurrencyFacts
from lib.artifacts.visual_artifact_provenance import build_reference_video_artifact_visual_basis
from lib.config.resolver import ConfigResolver
from lib.generation.video_request_facts import VideoRequestFacts
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.script.reference_video.request_projection import (
    ReferenceRequestOptions,
    configured_reference_request_facts,
    project_reference_unit_request,
)
from lib.speech.narration_delivery import USE_TTS, TtsSynthesisSettings, build_narration_audio_basis
from lib.speech.speech_artifact_provenance import build_video_duration_basis, build_video_speech_basis
from lib.speech.speech_composition import admit_script_unit
from server.services.tasks.narration_delivery_tasks import (
    ResolvedTtsSettingsResolver,
    prepare_current_reference_video_request_options,
    reference_video_visual_basis_digest,
)
from tests.factories import make_test_video, wav_bytes


async def test_reference_reuse_and_projection_keep_the_same_resolution_snapshot(db_factory, tmp_path):
    pair = "openai/sora-2"
    project = {
        "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
        "generation_mode": "reference_video",
        "video_provider_i2v": pair,
        "video_generate_audio": True,
        "model_settings": {pair: {"resolution": "720p"}},
        "episodes": [{"episode": 1, "script_file": "episode_1.json"}],
    }
    unit = {
        "unit_id": "E1U1",
        "text": "镜头1：海面\n{旁白正文。}",
        "duration_seconds": 8,
        "generated_assets": {},
    }
    script = {"episode": 1, "video_units": [unit]}
    (tmp_path / "project.json").write_text(json.dumps(project), encoding="utf-8")
    lookup = configured_reference_request_facts(project, ConfigResolver(db_factory))
    facts = await lookup("i2v")
    assert isinstance(facts, VideoRequestFacts)
    preparation = admit_script_unit("video_units", unit).preparation
    settings = TtsSynthesisSettings("openai", "tts-1", "alloy", None)
    audio = tmp_path / "audio/segment_E1U1.wav"
    audio.parent.mkdir()
    audio.write_bytes(wav_bytes(6.2))
    manifest = ArtifactManifest(ProjectArtifactManifestAdapter(tmp_path))
    manifest.register(
        ArtifactKey.episode_audio(1, "E1U1"),
        artifact_path="audio/segment_E1U1.wav",
        basis=build_narration_audio_basis(preparation, settings),
    )
    current = tmp_path / "reference_videos/E1U1.mp4"
    make_test_video(current, duration_sec=8, fps=1)
    visual = build_reference_video_artifact_visual_basis(unit=unit, request_assets=[], style=None, aspect_ratio="9:16")
    speech = build_video_speech_basis(preparation)
    duration = build_video_duration_basis(8)
    currency = VideoArtifactCurrencyFacts(
        episode=1,
        request_duration_seconds=8,
        visual_basis=visual,
        speech_basis=speech,
        duration_basis=duration,
        video_basis=compose_video_artifact_basis(visual=visual, speech=speech, duration=duration),
        voice_style_speakers=(),
        duration_tiers=(4, 8, 12),
        reference_image_limit=0,
        parent_version=0,
    )
    VersionManager(tmp_path).add_version(
        "reference_videos",
        "E1U1",
        "海面",
        source_file=current,
        duration_seconds=8,
        artifact_video_currency=currency.to_dict(),
        visual_basis_digest=reference_video_visual_basis_digest(
            project=project, project_path=tmp_path, unit=unit, request_assets=[], request_facts=facts
        ),
    )
    manifest.register_descriptor(
        ArtifactKey.episode_video(1, "E1U1"),
        artifact_path="reference_videos/E1U1.mp4",
        basis=currency.video_descriptor,
    )
    project["model_settings"][pair]["resolution"] = "1080p"

    options = await prepare_current_reference_video_request_options(
        project=project,
        script=script,
        script_file="episode_1.json",
        unit=unit,
        project_path=tmp_path,
        options=ReferenceRequestOptions(narration_delivery=USE_TTS),
        project_name="demo",
        request_facts_lookup=lookup,
        tts_settings_resolver=ResolvedTtsSettingsResolver(settings),
    )
    projection = await project_reference_unit_request(
        project=project,
        script=script,
        unit=unit,
        project_path=tmp_path,
        options=options,
        request_facts_lookup=lookup,
        current_options_materialized=True,
    )

    assert options.current_reusable_visual_duration_seconds == 8
    assert not projection.blocking_problems
    assert projection.cost is not None
    assert projection.cost.resolution == "720p"
    assert projection.request_facts == facts
