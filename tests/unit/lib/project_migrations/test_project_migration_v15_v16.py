"""v15→v16：补记风格描述只改依据口径；版本守卫、幂等，与「描述为空则不规划」的短路。"""

import json
from pathlib import Path

import pytest

from lib.artifact_manifest import (
    ArtifactBasis,
    ArtifactKey,
    ArtifactManifestEntry,
    compose_video_artifact_basis,
)
from lib.project_migrations.v15_to_v16_grid_video_style_descriptions import (
    TARGET_SCHEMA_VERSION,
    has_style_description,
    legacy_projection_bytes,
    migrate_v15_to_v16,
    restamp_reference_video_provenance,
)
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.speech_artifact_provenance import build_video_duration_basis
from lib.video_artifact_facts import VideoArtifactCurrencyFacts
from lib.visual_artifact_provenance import build_reference_video_artifact_visual_basis


def _write(tmp_path: Path, data: dict) -> Path:
    d = tmp_path / "demo"
    d.mkdir()
    (d / "project.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return d


def _load(d: Path) -> dict:
    return json.loads((d / "project.json").read_text(encoding="utf-8"))


class TestHasStyleDescription:
    @pytest.mark.parametrize("value", [None, "", "   ", 7, ["淡彩"]])
    def test_absent_or_blank_descriptions_are_not_a_signal(self, value):
        """描述为空或缺失时两套口径逐字一致，本步没有可改写的东西。"""
        project = {} if value is None else {"style_description": value}
        assert has_style_description(project) is False

    def test_non_blank_description_is_a_signal(self):
        assert has_style_description({"style_description": "淡彩"}) is True


class TestLegacyProjectionBytes:
    def test_removes_the_description_and_keeps_everything_else(self):
        projected = json.loads(
            legacy_projection_bytes({"schema_version": 15, "style_description": "淡彩", "title": "T"})
        )
        assert projected == {"schema_version": 15, "title": "T"}

    def test_missing_description_is_not_added(self):
        assert json.loads(legacy_projection_bytes({"schema_version": 15})) == {"schema_version": 15}

    def test_does_not_mutate_the_input(self):
        project = {"schema_version": 15, "style_description": "淡彩"}
        legacy_projection_bytes(project)
        assert project == {"schema_version": 15, "style_description": "淡彩"}


class TestMigrateV15ToV16File:
    def test_bumps_the_schema_version_without_touching_the_project(self, tmp_path: Path):
        d = _write(tmp_path, {"schema_version": 15, "title": "T", "style_description": "淡彩"})
        migrate_v15_to_v16(d)
        assert _load(d) == {"schema_version": 16, "title": "T", "style_description": "淡彩"}

    def test_blank_description_needs_no_manifest(self, tmp_path: Path):
        """描述为空的项目不做目标态规划，因而没有产物清单也能走完这一步。"""
        d = _write(tmp_path, {"schema_version": 15, "style_description": "  "})
        assert migrate_v15_to_v16(d) is None
        assert _load(d) == {"schema_version": 16, "style_description": "  "}

    def test_missing_description_needs_no_manifest(self, tmp_path: Path):
        d = _write(tmp_path, {"schema_version": 15, "title": "T"})
        assert migrate_v15_to_v16(d) is None
        assert _load(d) == {"schema_version": 16, "title": "T"}

    def test_skips_already_current_project(self, tmp_path: Path):
        d = _write(tmp_path, {"schema_version": 16, "style_description": "淡彩"})
        assert migrate_v15_to_v16(d) is None
        assert _load(d)["schema_version"] == 16

    def test_missing_project_file_is_noop(self, tmp_path: Path):
        d = tmp_path / "empty"
        d.mkdir()
        assert migrate_v15_to_v16(d) is None
        assert not (d / "project.json").exists()

    def test_non_object_project_file_rejected(self, tmp_path: Path):
        d = tmp_path / "demo"
        d.mkdir()
        (d / "project.json").write_text("[]", encoding="utf-8")
        with pytest.raises(ValueError, match=r"project\.json 必须是对象"):
            migrate_v15_to_v16(d)

    def test_target_schema_version_is_the_current_one(self):
        assert TARGET_SCHEMA_VERSION == CURRENT_PROJECT_SCHEMA_VERSION


_UNIT_PATH = "reference_videos/E1U01.mp4"


def _facts(*, description: str = "", unit_id: str = "E1U01") -> VideoArtifactCurrencyFacts:
    visual = build_reference_video_artifact_visual_basis(
        unit={"unit_id": unit_id, "text": "环顾四周"},
        request_assets=(),
        style="写实",
        aspect_ratio="9:16",
        style_description=description,
    )
    speech = ArtifactBasis.build("artifact-speech/video", kind_version=1, inputs={"mode": "silent"})
    duration = build_video_duration_basis(8)
    return VideoArtifactCurrencyFacts(
        episode=1,
        request_duration_seconds=8,
        visual_basis=visual,
        speech_basis=speech,
        duration_basis=duration,
        video_basis=compose_video_artifact_basis(visual=visual, speech=speech, duration=duration),
        voice_style_speakers=(),
        duration_tiers=(4, 8),
        reference_image_limit=None,
        parent_version=0,
    )


def _write_versions(project_dir: Path, buckets: dict) -> Path:
    versions_path = project_dir / "versions" / "versions.json"
    versions_path.parent.mkdir(parents=True, exist_ok=True)
    versions_path.write_text(json.dumps(buckets, ensure_ascii=False), encoding="utf-8")
    return versions_path


def _reference_bucket(facts: VideoArtifactCurrencyFacts, *, version: int = 1) -> dict:
    record = {"version": version, "artifact_video_currency": facts.to_dict()}
    return {"reference_videos": {"E1U01": {"current_version": version, "versions": [record]}}}


def _before(*, artifact_path: str = _UNIT_PATH, digest: str) -> dict:
    return {
        ArtifactKey.episode_video(1, "E1U01"): ArtifactManifestEntry(artifact_path=artifact_path, basis_digest=digest)
    }


class TestRestampReferenceVideoProvenance:
    """冻结依据正是旧口径投影时按新口径重新盖章；其余形态一律原样留下。"""

    def test_old_style_record_is_restamped_onto_the_description(self, tmp_path: Path):
        project_dir = tmp_path / "demo"
        project_dir.mkdir()
        versions_path = _write_versions(project_dir, _reference_bucket(_facts()))
        before = _before(digest=_facts().video_descriptor.digest)

        restamped = restamp_reference_video_provenance(project_dir, before, description="淡彩")

        assert restamped == frozenset({_UNIT_PATH})
        record = json.loads(versions_path.read_text(encoding="utf-8"))["reference_videos"]["E1U01"]["versions"][0]
        assert record["provenance_backfilled_at"]
        amended = VideoArtifactCurrencyFacts.from_dict(record["artifact_video_currency"])
        assert amended == _facts(description="淡彩")
        assert amended.video_descriptor.digest != before[next(iter(before))].basis_digest

    def test_record_already_carrying_the_description_is_left_alone(self, tmp_path: Path):
        project_dir = tmp_path / "demo"
        project_dir.mkdir()
        versions_path = _write_versions(project_dir, _reference_bucket(_facts(description="淡彩")))
        content = versions_path.read_bytes()

        restamped = restamp_reference_video_provenance(
            project_dir,
            _before(digest=_facts().video_descriptor.digest),
            description="淡彩",
        )

        assert restamped == frozenset()
        assert versions_path.read_bytes() == content

    def test_record_stale_before_the_upgrade_is_left_alone(self, tmp_path: Path):
        """改写前就过期的登记不能被趁机洗成时新。"""

        project_dir = tmp_path / "demo"
        project_dir.mkdir()
        versions_path = _write_versions(project_dir, _reference_bucket(_facts()))
        content = versions_path.read_bytes()

        restamped = restamp_reference_video_provenance(
            project_dir,
            _before(digest="sha256-v1:" + "0" * 64),
            description="淡彩",
        )

        assert restamped == frozenset()
        assert versions_path.read_bytes() == content

    def test_videos_bucket_is_out_of_scope(self, tmp_path: Path):
        project_dir = tmp_path / "demo"
        project_dir.mkdir()
        versions_path = _write_versions(project_dir, _reference_bucket(_facts()))
        data = json.loads(versions_path.read_text(encoding="utf-8"))
        data["videos"] = data["reference_videos"]
        del data["reference_videos"]
        versions_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        content = versions_path.read_bytes()

        assert (
            restamp_reference_video_provenance(
                project_dir,
                _before(digest=_facts().video_descriptor.digest),
                description="淡彩",
            )
            == frozenset()
        )
        assert versions_path.read_bytes() == content

    @pytest.mark.parametrize(
        "bucket",
        [
            pytest.param({}, id="no-bucket"),
            pytest.param({"reference_videos": {"E1U01": {"versions": []}}}, id="no-selected-version"),
            pytest.param(
                {"reference_videos": {"E1U01": {"current_version": 1, "versions": [{"version": 1}]}}},
                id="record-without-facts",
            ),
        ],
    )
    def test_unreadable_shapes_are_left_alone(self, tmp_path: Path, bucket: dict):
        project_dir = tmp_path / "demo"
        project_dir.mkdir()
        versions_path = _write_versions(project_dir, bucket)
        content = versions_path.read_bytes()

        assert (
            restamp_reference_video_provenance(
                project_dir,
                _before(digest=_facts().video_descriptor.digest),
                description="淡彩",
            )
            == frozenset()
        )
        assert versions_path.read_bytes() == content

    def test_missing_versions_file_is_a_noop(self, tmp_path: Path):
        project_dir = tmp_path / "demo"
        project_dir.mkdir()
        assert (
            restamp_reference_video_provenance(
                project_dir,
                _before(digest=_facts().video_descriptor.digest),
                description="淡彩",
            )
            == frozenset()
        )

    @pytest.mark.parametrize("description", ["", "   "])
    def test_blank_description_records_no_key(self, tmp_path: Path, description: str):
        """描述为空时构造侧不落键，重盖章也不该落。"""

        project_dir = tmp_path / "demo"
        project_dir.mkdir()
        versions_path = _write_versions(project_dir, _reference_bucket(_facts()))
        content = versions_path.read_bytes()

        assert (
            restamp_reference_video_provenance(
                project_dir,
                _before(digest=_facts().video_descriptor.digest),
                description=description,
            )
            == frozenset()
        )
        assert versions_path.read_bytes() == content
