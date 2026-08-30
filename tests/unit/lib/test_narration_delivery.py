"""Narration delivery and TTS currency domain contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.artifact_manifest import (
    ArtifactComparison,
    ArtifactKey,
    ArtifactManifest,
    ArtifactStatus,
    InMemoryArtifactManifestAdapter,
    ProjectArtifactManifestAdapter,
)
from lib.narration_delivery import (
    POST_PRODUCTION,
    USE_TTS,
    NarratedVideoDurationPreparation,
    NarrationAudioEvidence,
    NarrationDeliveryRequestOptions,
    NarrationTtsStatus,
    TtsSynthesisSettings,
    build_narration_audio_basis,
    build_narration_audio_basis_from_canonical_text,
    canonical_narration_text,
    prepare_current_narration_delivery,
    prepare_narrated_video_duration,
    prepare_narrated_video_output,
    prepare_narration_delivery,
    register_narration_audio_transactionally,
    resolve_tts_synthesis_settings,
)
from lib.speech_composition import (
    SpeechFieldLocation,
    SpeechMode,
    SpeechOwner,
    SpeechPreparation,
    SpeechUtterance,
)


def _narrator_preparation(unit_id: str = "E1U1", *texts: str) -> SpeechPreparation:
    values = texts or ("旁白正文",)
    return SpeechPreparation(
        unit_id=unit_id,
        mode=SpeechMode.NARRATOR_VOICEOVER,
        utterances=tuple(
            SpeechUtterance(
                owner=SpeechOwner.NARRATOR,
                text=text,
                speaker=None,
                location=SpeechFieldLocation(("utterances", index, "text")),
            )
            for index, text in enumerate(values)
        ),
    )


def _settings(**overrides: object) -> TtsSynthesisSettings:
    values: dict[str, object] = {
        "provider_id": "dashscope",
        "model_id": "qwen3-tts-flash",
        "voice": "Cherry",
        "speed": None,
    }
    values.update(overrides)
    return TtsSynthesisSettings(**values)


def _comparison(status: ArtifactStatus, path: str = "audio/segment_E1U1.wav") -> ArtifactComparison:
    return ArtifactComparison(status=status, artifact_path=path)


@pytest.mark.parametrize("invalid", [True, 0, -1, 8.5, "8"])
def test_request_confirmation_is_an_exact_positive_integer(invalid: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        NarrationDeliveryRequestOptions(confirmed_request_duration_seconds=invalid)


def test_canonical_text_is_nfc_line_normalized_and_ordered() -> None:
    preparation = _narrator_preparation("E1U1", "  Cafe\u0301\r\n第一句  ", "\n第二句\r")

    assert canonical_narration_text(preparation) == "Café\n第一句\n第二句"


def test_tts_basis_changes_for_each_paid_synthesis_input() -> None:
    preparation = _narrator_preparation("E1U1", "正文")
    base = build_narration_audio_basis(preparation, _settings())

    assert base != build_narration_audio_basis(_narrator_preparation("E1U1", "正文改"), _settings())
    assert base != build_narration_audio_basis(preparation, _settings(voice="Ethan"))
    assert base != build_narration_audio_basis(preparation, _settings(model_id="cosyvoice-v3.5-flash"))
    assert base != build_narration_audio_basis(preparation, _settings(speed=1.2))


def test_tts_basis_raw_facts_builder_matches_preparation_builder() -> None:
    preparation = _narrator_preparation("E1U1", "正文")
    settings = _settings()

    assert build_narration_audio_basis(preparation, settings) == build_narration_audio_basis_from_canonical_text(
        "正文",
        settings,
    )


def test_registration_failure_restores_the_previous_current_basis(tmp_path: Path, monkeypatch) -> None:
    artifact = tmp_path / "audio" / "segment_E1U1.wav"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"formal-audio")
    old_preparation = _narrator_preparation("E1U1", "旧旁白")
    new_preparation = _narrator_preparation("E1U1", "新旁白")
    old_basis = register_narration_audio_transactionally(
        project_path=tmp_path,
        episode=1,
        preparation=old_preparation,
        settings=_settings(),
    )
    original_put = ProjectArtifactManifestAdapter.put_entry
    calls = 0

    def _write_then_fail(self, key, entry):
        nonlocal calls
        calls += 1
        changed = original_put(self, key, entry)
        if calls == 1:
            raise RuntimeError("manifest finalize failed")
        return changed

    monkeypatch.setattr(ProjectArtifactManifestAdapter, "put_entry", _write_then_fail)

    with pytest.raises(RuntimeError, match="manifest finalize failed"):
        register_narration_audio_transactionally(
            project_path=tmp_path,
            episode=1,
            preparation=new_preparation,
            settings=_settings(),
        )

    comparison = ArtifactManifest(ProjectArtifactManifestAdapter(tmp_path)).compare(
        ArtifactKey.episode_audio(1, "E1U1"),
        artifact_path="audio/segment_E1U1.wav",
        basis=old_basis,
    )
    assert comparison.status is ArtifactStatus.CURRENT


def test_post_production_bypasses_missing_tts_configuration_and_artifact() -> None:
    prepared = prepare_narration_delivery(
        delivery=POST_PRODUCTION,
        preparation=_narrator_preparation(),
        artifact_path="audio/segment_E1U1.wav",
        settings=None,
        evidence=None,
    )

    assert prepared.allowed is True
    assert prepared.duration_floor is None
    assert prepared.actual_duration_seconds is None
    assert prepared.problems == ()
    assert prepared.tts_status is NarrationTtsStatus.NOT_CONFIGURED


def test_use_tts_requires_current_basis_and_measurable_actual_duration() -> None:
    preparation = _narrator_preparation()
    settings = _settings()
    current = prepare_narration_delivery(
        delivery=USE_TTS,
        preparation=preparation,
        artifact_path="audio/segment_E1U1.wav",
        settings=settings,
        evidence=NarrationAudioEvidence(
            comparison=_comparison(ArtifactStatus.CURRENT),
            present=True,
            duration_seconds=6.25,
        ),
    )

    assert current.allowed is True
    assert current.tts_status is NarrationTtsStatus.CURRENT
    assert current.duration_floor == 6.25
    assert current.to_payload()["actual_duration_seconds"] == 6.25

    unmeasurable = prepare_narration_delivery(
        delivery=USE_TTS,
        preparation=preparation,
        artifact_path="audio/segment_E1U1.wav",
        settings=settings,
        evidence=NarrationAudioEvidence(
            comparison=_comparison(ArtifactStatus.CURRENT),
            present=True,
            duration_seconds=None,
        ),
    )
    assert unmeasurable.allowed is False
    assert unmeasurable.tts_status is NarrationTtsStatus.UNMEASURABLE
    assert [problem.code for problem in unmeasurable.problems] == ["tts_duration_unavailable"]


@pytest.mark.parametrize(
    ("status", "present", "expected_status", "expected_code"),
    [
        (ArtifactStatus.MISSING, False, NarrationTtsStatus.MISSING, "tts_missing"),
        # A formal legacy/failed-regeneration file without a matching manifest entry is playable,
        # but never fresh enough for a paid video request.
        (ArtifactStatus.MISSING, True, NarrationTtsStatus.STALE, "tts_stale"),
        (ArtifactStatus.STALE, True, NarrationTtsStatus.STALE, "tts_stale"),
    ],
)
def test_use_tts_rejects_missing_or_stale_audio(
    status: ArtifactStatus,
    present: bool,
    expected_status: NarrationTtsStatus,
    expected_code: str,
) -> None:
    prepared = prepare_narration_delivery(
        delivery=USE_TTS,
        preparation=_narrator_preparation(),
        artifact_path="audio/segment_E1U1.wav",
        settings=_settings(),
        evidence=NarrationAudioEvidence(
            comparison=_comparison(status),
            present=present,
            duration_seconds=4.0 if present else None,
        ),
    )

    assert prepared.allowed is False
    assert prepared.tts_status is expected_status
    assert [problem.code for problem in prepared.problems] == [expected_code]
    assert prepared.problems[0].to_payload(unit_id="E1U1")["action"] in {
        "generate_tts",
        "regenerate_tts",
    }


def test_use_tts_is_not_applicable_to_character_speech() -> None:
    preparation = SpeechPreparation(
        unit_id="E1S1",
        mode=SpeechMode.CHARACTER_SPEECH,
        utterances=(
            SpeechUtterance(
                owner=SpeechOwner.CHARACTER,
                text="人物台词",
                speaker="阿离",
                location=SpeechFieldLocation(("utterances", 0, "text")),
            ),
        ),
    )

    prepared = prepare_narration_delivery(
        delivery=USE_TTS,
        preparation=preparation,
        artifact_path="audio/segment_E1S1.wav",
        settings=_settings(),
        evidence=None,
    )

    assert prepared.tts_status is NarrationTtsStatus.NOT_APPLICABLE
    assert prepared.allowed is False
    assert [problem.code for problem in prepared.problems] == ["tts_not_applicable"]


def test_in_progress_tts_does_not_override_a_current_configuration_blocker() -> None:
    prepared = prepare_narration_delivery(
        delivery=USE_TTS,
        preparation=_narrator_preparation(),
        artifact_path="audio/segment_E1U1.wav",
        settings=None,
        evidence=None,
        tts_in_progress=True,
    )

    assert prepared.tts_status is NarrationTtsStatus.NOT_CONFIGURED
    assert [problem.code for problem in prepared.problems] == ["tts_not_configured"]


def test_same_episode_reference_units_have_independent_manifest_currency_and_duration() -> None:
    paths = {
        "E1U1": "audio/segment_E1U1.wav",
        "E1U2": "audio/segment_E1U2.wav",
    }
    adapter = InMemoryArtifactManifestAdapter(artifacts=set(paths.values()))
    manifest = ArtifactManifest(adapter)
    settings = _settings()
    p1 = _narrator_preparation("E1U1", "第一单元")
    p2 = _narrator_preparation("E1U2", "第二单元")
    manifest.register(
        ArtifactKey.episode_audio(1, "E1U1"),
        artifact_path=paths["E1U1"],
        basis=build_narration_audio_basis(p1, settings),
    )

    e1 = NarrationAudioEvidence(
        comparison=manifest.compare(
            ArtifactKey.episode_audio(1, "E1U1"),
            artifact_path=paths["E1U1"],
            basis=build_narration_audio_basis(p1, settings),
        ),
        present=True,
        duration_seconds=4.2,
    )
    e2 = NarrationAudioEvidence(
        comparison=manifest.compare(
            ArtifactKey.episode_audio(1, "E1U2"),
            artifact_path=paths["E1U2"],
            basis=build_narration_audio_basis(p2, settings),
        ),
        present=True,
        duration_seconds=9.7,
    )

    first = prepare_narration_delivery(
        delivery=USE_TTS,
        preparation=p1,
        artifact_path=paths["E1U1"],
        settings=settings,
        evidence=e1,
    )
    second = prepare_narration_delivery(
        delivery=USE_TTS,
        preparation=p2,
        artifact_path=paths["E1U2"],
        settings=settings,
        evidence=e2,
    )

    assert first.allowed is True
    assert first.duration_floor == 4.2
    assert second.allowed is False
    assert second.duration_floor is None
    assert second.tts_status is NarrationTtsStatus.STALE


class _ConfiguredIdentityOnlyResolver:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def resolve_audio_backend(self, project: dict, payload: object) -> object:
        from lib.config.resolver import ProviderModel

        self.calls.append("model")
        return ProviderModel("dashscope", "qwen3-tts-flash")

    async def resolve_narration_voice(self, project: dict) -> str:
        self.calls.append("voice")
        return "Cherry"

    async def resolve_narration_speed(self, project: dict) -> float | None:
        self.calls.append("speed")
        return 1.1


class _FakeSettingsResolver:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def resolve_tts_synthesis_settings(self, project: dict) -> TtsSynthesisSettings:
        self.calls.append("settings")
        return _settings(speed=1.1)


async def test_tts_settings_resolution_rejects_a_configured_identity_only_resolver() -> None:
    resolver = _ConfiguredIdentityOnlyResolver()

    with pytest.raises(AttributeError):
        await resolve_tts_synthesis_settings({}, resolver)

    assert resolver.calls == []


async def test_current_state_adapter_registers_and_reads_exact_unit_basis(tmp_path: Path) -> None:
    project_path = tmp_path / "demo"
    audio = project_path / "audio" / "segment_E1U1.wav"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"RIFF-current")
    preparation = _narrator_preparation("E1U1", "正文")
    settings = _settings(speed=1.1)

    registered = register_narration_audio_transactionally(
        project_path=project_path,
        episode=1,
        preparation=preparation,
        settings=settings,
    )

    async def _probe(path: Path) -> float:
        assert path == audio
        return 7.4

    resolver = _FakeSettingsResolver()
    prepared = await prepare_current_narration_delivery(
        project={"name": "demo"},
        episode=1,
        preparation=preparation,
        project_path=project_path,
        delivery=USE_TTS,
        resolver=resolver,
        duration_probe=_probe,
    )

    assert registered.digest == prepared.basis_digest
    assert prepared.allowed is True
    assert prepared.duration_floor == 7.4
    assert resolver.calls == ["settings"]


async def test_current_state_adapter_treats_concurrently_deleted_current_audio_as_unmeasurable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lib.narration_delivery as narration_delivery

    project_path = tmp_path / "demo"
    audio = project_path / "audio" / "segment_E1U1.wav"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"RIFF-current")
    preparation = _narrator_preparation("E1U1", "正文")
    register_narration_audio_transactionally(
        project_path=project_path,
        episode=1,
        preparation=preparation,
        settings=_settings(speed=1.1),
    )

    def _deleted(*_args: object, **_kwargs: object) -> Path:
        raise FileNotFoundError(audio)

    monkeypatch.setattr(narration_delivery, "safe_join", _deleted)
    prepared = await prepare_current_narration_delivery(
        project={"name": "demo"},
        episode=1,
        preparation=preparation,
        project_path=project_path,
        delivery=USE_TTS,
        resolver=_FakeSettingsResolver(),
    )

    assert prepared.tts_status is NarrationTtsStatus.UNMEASURABLE
    assert [problem.code for problem in prepared.problems] == ["tts_duration_unavailable"]


async def test_current_state_adapter_post_production_does_not_touch_tts_config(tmp_path: Path) -> None:
    class _ExplodingResolver:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"post-production touched resolver.{name}")

    prepared = await prepare_current_narration_delivery(
        project={"name": "demo"},
        episode=1,
        preparation=_narrator_preparation(),
        project_path=tmp_path,
        delivery=POST_PRODUCTION,
        resolver=_ExplodingResolver(),
    )

    assert prepared.allowed is True
    assert prepared.tts_status is NarrationTtsStatus.NOT_CONFIGURED


async def test_current_state_adapter_blocks_an_in_progress_tts_regeneration_without_reading_old_audio(
    tmp_path: Path,
) -> None:
    prepared = await prepare_current_narration_delivery(
        project={"name": "demo"},
        episode=1,
        preparation=_narrator_preparation(),
        project_path=tmp_path,
        delivery=USE_TTS,
        resolver=_FakeSettingsResolver(),
        tts_in_progress=True,
    )

    assert prepared.allowed is False
    assert prepared.tts_status is NarrationTtsStatus.GENERATING
    assert prepared.duration_floor is None
    assert [problem.code for problem in prepared.problems] == ["tts_generating"]


async def test_current_tts_is_blocked_while_an_explicit_regeneration_runs(tmp_path: Path) -> None:
    project_path = tmp_path / "demo"
    audio = project_path / "audio" / "segment_E1U1.wav"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"RIFF-current")
    preparation = _narrator_preparation("E1U1", "正文")
    register_narration_audio_transactionally(
        project_path=project_path,
        episode=1,
        preparation=preparation,
        settings=_settings(speed=1.1),
    )

    async def _probe(path: Path) -> float:
        assert path == audio
        return 7.4

    prepared = await prepare_current_narration_delivery(
        project={"name": "demo"},
        episode=1,
        preparation=preparation,
        project_path=project_path,
        delivery=USE_TTS,
        resolver=_FakeSettingsResolver(),
        duration_probe=_probe,
        tts_in_progress=True,
    )

    assert prepared.allowed is False
    assert prepared.tts_status is NarrationTtsStatus.GENERATING
    assert prepared.duration_floor is None
    assert [problem.code for problem in prepared.problems] == ["tts_generating"]


def test_narrated_video_keeps_the_visual_tier_when_tts_fits_and_leaves_a_tail() -> None:
    narration = prepare_narration_delivery(
        delivery=USE_TTS,
        preparation=_narrator_preparation(),
        artifact_path="audio/segment_E1U1.wav",
        settings=_settings(),
        evidence=NarrationAudioEvidence(
            comparison=_comparison(ArtifactStatus.CURRENT),
            present=True,
            duration_seconds=6.2,
        ),
    )

    result = prepare_narrated_video_duration(
        narration=narration,
        planned_duration_seconds=8,
        supported_durations=(4, 8, 12),
        confirmed_request_duration_seconds=None,
    )

    assert isinstance(result, NarratedVideoDurationPreparation)
    assert result.allowed is True
    assert result.duration_input == 8
    assert result.request_duration_seconds == 8
    assert result.problems == ()


def test_narrated_video_requires_the_exact_higher_tier_confirmation() -> None:
    narration = prepare_narration_delivery(
        delivery=USE_TTS,
        preparation=_narrator_preparation(),
        artifact_path="audio/segment_E1U1.wav",
        settings=_settings(),
        evidence=NarrationAudioEvidence(
            comparison=_comparison(ArtifactStatus.CURRENT),
            present=True,
            duration_seconds=6.2,
        ),
    )

    missing = prepare_narrated_video_duration(
        narration=narration,
        planned_duration_seconds=4,
        supported_durations=(4, 8, 12),
        confirmed_request_duration_seconds=None,
    )
    wrong = prepare_narrated_video_duration(
        narration=narration,
        planned_duration_seconds=4,
        supported_durations=(4, 8, 12),
        confirmed_request_duration_seconds=12,
    )
    accepted = prepare_narrated_video_duration(
        narration=narration,
        planned_duration_seconds=4,
        supported_durations=(4, 8, 12),
        confirmed_request_duration_seconds=8,
    )

    assert [problem.code for problem in missing.problems] == ["reference_duration_confirmation_required"]
    assert [problem.code for problem in wrong.problems] == ["reference_duration_confirmation_required"]
    assert accepted.allowed is True
    assert accepted.request_duration_seconds == 8


def test_narrated_video_confirmation_uses_the_selected_visual_tier_when_available() -> None:
    narration = prepare_narration_delivery(
        delivery=USE_TTS,
        preparation=_narrator_preparation(),
        artifact_path="audio/segment_E1U1.wav",
        settings=_settings(),
        evidence=NarrationAudioEvidence(
            comparison=_comparison(ArtifactStatus.CURRENT),
            present=True,
            duration_seconds=8.0,
        ),
    )

    reusable = prepare_narrated_video_duration(
        narration=narration,
        planned_duration_seconds=4,
        supported_durations=(4, 8, 12),
        confirmed_request_duration_seconds=None,
        current_visual_duration_seconds=8,
    )
    replacement = prepare_narrated_video_duration(
        narration=narration,
        planned_duration_seconds=8,
        supported_durations=(4, 8, 12),
        confirmed_request_duration_seconds=None,
        current_visual_duration_seconds=4,
    )

    assert reusable.allowed is True
    assert reusable.request_duration_seconds == 8
    assert reusable.current_visual_duration_seconds == 8
    assert [problem.code for problem in replacement.problems] == ["reference_duration_confirmation_required"]
    assert replacement.problems[0].parameters()["current_visual_duration"] == 4


def test_narrated_video_above_maximum_requires_replanning_without_truncation() -> None:
    narration = prepare_narration_delivery(
        delivery=USE_TTS,
        preparation=_narrator_preparation(),
        artifact_path="audio/segment_E1U1.wav",
        settings=_settings(),
        evidence=NarrationAudioEvidence(
            comparison=_comparison(ArtifactStatus.CURRENT),
            present=True,
            duration_seconds=13.0,
        ),
    )

    result = prepare_narrated_video_duration(
        narration=narration,
        planned_duration_seconds=8,
        supported_durations=(4, 8, 12),
        confirmed_request_duration_seconds=12,
    )

    assert result.allowed is False
    assert result.request_duration_seconds is None
    assert [problem.code for problem in result.problems] == ["needs_replan"]
    assert result.problems[0].action == "replan_unit"


@pytest.mark.parametrize(
    ("actual_duration", "expected_code"),
    [
        (None, "video_duration_unavailable"),
        (6.1, "video_shorter_than_tts"),
    ],
)
def test_generated_video_must_have_measurable_media_long_enough_for_current_tts(
    actual_duration: float | None,
    expected_code: str,
) -> None:
    narration = prepare_narration_delivery(
        delivery=USE_TTS,
        preparation=_narrator_preparation(),
        artifact_path="audio/segment_E1U1.wav",
        settings=_settings(),
        evidence=NarrationAudioEvidence(
            comparison=_comparison(ArtifactStatus.CURRENT),
            present=True,
            duration_seconds=6.2,
        ),
    )
    request = prepare_narrated_video_duration(
        narration=narration,
        planned_duration_seconds=8,
        supported_durations=(4, 8, 12),
        confirmed_request_duration_seconds=None,
    )

    result = prepare_narrated_video_output(request, actual_duration_seconds=actual_duration)

    assert result.allowed is False
    assert result.problems[-1].code == expected_code
    assert result.problems[-1].action == "regenerate_video"


def test_generated_video_equal_to_tts_duration_is_accepted_without_speedup() -> None:
    narration = prepare_narration_delivery(
        delivery=USE_TTS,
        preparation=_narrator_preparation(),
        artifact_path="audio/segment_E1U1.wav",
        settings=_settings(),
        evidence=NarrationAudioEvidence(
            comparison=_comparison(ArtifactStatus.CURRENT),
            present=True,
            duration_seconds=6.2,
        ),
    )
    request = prepare_narrated_video_duration(
        narration=narration,
        planned_duration_seconds=8,
        supported_durations=(4, 8, 12),
        confirmed_request_duration_seconds=None,
    )

    assert prepare_narrated_video_output(request, actual_duration_seconds=6.2) is request
