from __future__ import annotations

import pytest

from lib.artifact_manifest import ArtifactBasis, ArtifactBasisDescriptor
from lib.artifact_provenance import build_episode_script_basis, build_step1_basis

pytestmark = pytest.mark.unit


def test_artifact_basis_has_deterministic_canonical_json() -> None:
    first = ArtifactBasis.build(
        "structured-content-test",
        kind_version=2,
        inputs={"z": "雪", "a": [1, True, None]},
    )
    second = ArtifactBasis.build(
        "structured-content-test",
        kind_version=2,
        inputs={"a": [1, True, None], "z": "雪"},
    )

    assert (
        first.normalized_bytes()
        == ('{"inputs":{"a":[1,true,null],"z":"雪"},"kind":"structured-content-test","kind_version":2}').encode()
    )
    assert second.normalized_bytes() == first.normalized_bytes()
    assert second.digest == first.digest


def test_artifact_basis_descriptor_round_trips_strict_source_fact() -> None:
    basis = ArtifactBasis.build("artifact-visual/video-storyboard", kind_version=3, inputs={"frame": "v1"})

    descriptor = ArtifactBasisDescriptor.from_basis(basis)

    assert descriptor.to_dict() == {
        "kind": "artifact-visual/video-storyboard",
        "kind_version": 3,
        "digest": basis.digest,
    }
    assert ArtifactBasisDescriptor.from_dict(descriptor.to_dict()) == descriptor


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"kind": "visual", "kind_version": 1, "digest": "sha256-v1:" + "a" * 64, "extra": True},
        {"kind": "", "kind_version": 1, "digest": "sha256-v1:" + "a" * 64},
        {"kind": "visual", "kind_version": True, "digest": "sha256-v1:" + "a" * 64},
        {"kind": "visual", "kind_version": 1, "digest": "a" * 64},
    ],
)
def test_artifact_basis_descriptor_rejects_noncanonical_source_fact(value: object) -> None:
    with pytest.raises(ValueError):
        ArtifactBasisDescriptor.from_dict(value)


def test_structured_content_basis_tracks_only_the_direct_formal_chain() -> None:
    first_project = {
        "creation_type": "drama",
        "generation_mode": "storyboard",
        "source_file_type": "screenplay",
        "source_language": "zh",
        "provider": "first-provider",
        "model": "first-model",
        "credentials": {"api_key": "first-secret"},
        "endpoint": "https://first.invalid",
        "resolution": "720p",
        "aspect_ratio": "16:9",
        "prompt_builder_version": 1,
        "voice": "first-voice",
        "speed": 1.0,
    }
    changed_execution_project = {
        **first_project,
        "provider": "second-provider",
        "model": "second-model",
        "credentials": {"api_key": "second-secret"},
        "endpoint": "https://second.invalid",
        "resolution": "4k",
        "aspect_ratio": "9:16",
        "prompt_builder_version": 99,
        "voice": "second-voice",
        "speed": 2.0,
    }

    step1 = build_step1_basis("第一场\n对白", project=first_project)
    same_step1 = build_step1_basis("第一场\n对白", project=changed_execution_project)
    changed_source = build_step1_basis("第一场\n另一句对白", project=first_project)
    script = build_episode_script_basis({"scenes": [{"scene_id": "E1S01"}]}, project=first_project)
    same_script = build_episode_script_basis(
        {"scenes": [{"scene_id": "E1S01"}]},
        project=changed_execution_project,
    )
    changed_step1 = build_episode_script_basis(
        {"scenes": [{"scene_id": "E1S01", "source_text": "changed"}]},
        project=first_project,
    )

    assert same_step1.digest == step1.digest
    assert changed_source.digest != step1.digest
    assert same_script.digest == script.digest
    assert changed_step1.digest != script.digest


def test_structured_basis_rejects_malformed_formal_inputs() -> None:
    with pytest.raises(ValueError, match="creation_type"):
        build_step1_basis(
            "source",
            project={"creation_type": [], "generation_mode": "storyboard"},
        )
    with pytest.raises(ValueError, match="non-finite"):
        build_episode_script_basis(
            {"duration": float("nan")},
            project={"creation_type": "narration", "generation_mode": "storyboard"},
        )


def test_step1_basis_treats_null_source_kind_as_default() -> None:
    project = {
        "creation_type": "narration",
        "generation_mode": "storyboard",
        "source_file_type": None,
    }

    defaulted = build_step1_basis("source", project=project)
    explicit = build_step1_basis("source", project={**project, "source_file_type": "novel"})

    assert defaulted.digest == explicit.digest


@pytest.mark.parametrize("source_language", [None, "", False, 0, [], {}])
def test_step1_basis_canonicalizes_default_source_language(source_language: object) -> None:
    project = {
        "creation_type": "narration",
        "generation_mode": "storyboard",
        "source_language": source_language,
    }

    defaulted = build_step1_basis("source", project=project)
    explicit = build_step1_basis("source", project={**project, "source_language": "中文"})

    assert defaulted.digest == explicit.digest
