"""v9→v10 script-plan / prompt-authoring persisted filename migration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lib.artifacts.artifact_manifest import (
    MANIFEST_SCHEMA_VERSION,
    ArtifactKey,
    ProjectArtifactManifestAdapter,
    decode_artifact_key_parts,
    encode_artifact_key_parts,
)
from lib.infra.content_digest import HASH_ALGORITHM
from lib.project.project_migration_report import load_migration_report
from lib.project.project_migrations.runner import migrate_project_dir, migrate_project_with_verdict
from lib.project.project_migrations.v9_to_v10_script_plan_naming import migrate_v9_to_v10
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.script import script_review

#: Three plan variants: content_mode / generation_mode / v9 draft filename / migrated filename.
_VARIANTS = [
    ("drama", "storyboard", "step1_normalized_script.json", "script_plan_normalized_script.json"),
    ("narration", "storyboard", "step1_segments.json", "script_plan_segments.json"),
    ("narration", "reference_video", "step1_reference_units.json", "script_plan_reference_units.json"),
]


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _legacy_artifact_key(kind: str, episode: int) -> str:
    """Construct v9 keys through real ``lib.artifacts.artifact_manifest`` encoding.

    Retired kinds are absent from ``ArtifactKind``, preventing ``ArtifactKey`` construction; use the module's public encoding
    entry instead. This is precisely the guard: neither migration nor tests may invent a second encoding.
    """

    return encode_artifact_key_parts(kind, (episode,))


def _decode_kind(encoded_key: str) -> str:
    parts = decode_artifact_key_parts(encoded_key)
    assert parts is not None
    return parts.kind


def _project(
    tmp_path: Path,
    *,
    content_mode: str,
    generation_mode: str,
    episode_extra: dict[str, Any] | None = None,
) -> Path:
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    _write_json(
        project_dir / "project.json",
        {
            "schema_version": 9,
            "content_mode": content_mode,
            "generation_mode": generation_mode,
            "style": "写实",
            "aspect_ratio": "9:16",
            "characters": {},
            "scenes": {},
            "props": {},
            "products": {},
            "episodes": [
                {
                    "episode": 1,
                    "title": "第 1 集",
                    "script_file": "scripts/episode_1.json",
                    **(episode_extra or {}),
                }
            ],
        },
    )
    return project_dir


@pytest.mark.parametrize(("content_mode", "generation_mode", "old_name", "new_name"), _VARIANTS)
def test_every_script_plan_variant_keeps_its_confirmation_across_the_rename(
    tmp_path: Path,
    content_mode: str,
    generation_mode: str,
    old_name: str,
    new_name: str,
) -> None:
    """Rename files/fields for all three plan variants and translate confirmation records; episodes remain confirmed."""

    draft = {"title": "第 1 集", "scenes": [{"scene_id": "S1"}]}
    project_dir = _project(tmp_path, content_mode=content_mode, generation_mode=generation_mode)
    draft_path = project_dir / "drafts" / "episode_1" / old_name
    _write_json(draft_path, draft)
    fingerprint = script_review.content_fingerprint(draft_path)
    project = _read_json(project_dir / "project.json")
    project["episodes"][0]["step1_review"] = {"fingerprint": fingerprint, "confirmed_at": "2026-01-01T00:00:00Z"}
    _write_json(project_dir / "project.json", project)
    _write_json(project_dir / "scripts" / "episode_1.json", {"episode": 1, "metadata": {"step1_revision": fingerprint}})

    assert migrate_project_dir(project_dir) is True

    migrated = _read_json(project_dir / "project.json")
    assert migrated["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION
    assert not draft_path.exists()
    assert _read_json(project_dir / "drafts" / "episode_1" / new_name) == draft
    episode = migrated["episodes"][0]
    assert "step1_review" not in episode
    assert episode["script_plan_review"] == {"fingerprint": fingerprint, "confirmed_at": "2026-01-01T00:00:00Z"}
    # Chain-end v15 script metadata omits whole-episode fingerprints.
    assert _read_json(project_dir / "scripts" / "episode_1.json")["metadata"] == {}
    # Confirmation fingerprints describe content, unchanged by renaming; episodes stay confirmed without blocking prompt writing again.
    assert script_review.review_status(project_dir, migrated, 1) == "confirmed"


def test_invalid_drafts_and_legacy_markdown_aliases_are_renamed_too(tmp_path: Path) -> None:
    """Rename repair-pending drafts and legacy ``.md`` aliases together, preserving every body character."""

    project_dir = _project(tmp_path, content_mode="narration", generation_mode="reference_video")
    drafts_dir = project_dir / "drafts" / "episode_1"
    _write_json(drafts_dir / "step1_reference_units.json", {"units": []})
    _write_json(drafts_dir / "step1_reference_units.invalid.json", {"units": [{"unit_id": "E1U1"}]})
    _write_json(drafts_dir / "step2_reference_script.invalid.json", {"video_units": []})
    (drafts_dir / "step1_reference_units.md").write_text("旧版自由文本", encoding="utf-8")

    assert migrate_project_dir(project_dir) is True

    assert _read_json(drafts_dir / "script_plan_reference_units.invalid.json") == {"units": [{"unit_id": "E1U1"}]}
    assert _read_json(drafts_dir / "prompt_authoring_reference_script.invalid.json") == {"video_units": []}
    assert (drafts_dir / "script_plan_reference_units.md").read_text(encoding="utf-8") == "旧版自由文本"
    assert not any(path.name.startswith(("step1_", "step2_")) for path in drafts_dir.glob("*.json"))
    assert not (drafts_dir / "step1_reference_units.md").exists()


def test_artifact_manifest_key_and_path_follow_the_rename(tmp_path: Path) -> None:
    """Rewrite plan artifact kinds and draft paths together in manifests, preserving fingerprints."""

    project_dir = _project(tmp_path, content_mode="narration", generation_mode="storyboard")
    _write_json(project_dir / "drafts" / "episode_1" / "step1_segments.json", {"segments": []})
    digest = f"{HASH_ALGORITHM}:" + "0" * 64
    _write_json(
        project_dir / ".arcreel_artifacts.json",
        {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "hash_algorithm": HASH_ALGORITHM,
            "entries": {
                _legacy_artifact_key("episode-step1", 1): {
                    "artifact_path": "drafts/episode_1/step1_segments.json",
                    "basis_digest": digest,
                },
                ArtifactKey.episode_script(1).encode(): {
                    "artifact_path": "scripts/episode_1.json",
                    "basis_digest": digest,
                },
            },
        },
    )

    migrate_v9_to_v10(project_dir)

    entries = _read_json(project_dir / ".arcreel_artifacts.json")["entries"]
    by_kind = {_decode_kind(key): entry for key, entry in entries.items()}
    assert set(by_kind) == {"episode-script-plan", "episode-script"}
    assert by_kind["episode-script-plan"]["artifact_path"] == "drafts/episode_1/script_plan_segments.json"
    assert by_kind["episode-script-plan"]["basis_digest"] == digest
    assert by_kind["episode-script"]["artifact_path"] == "scripts/episode_1.json"
    # Rewritten keys must exactly match current plan-artifact registration keys,
    # and whole manifests must remain readable; leftover old kinds make parsing reject the entire manifest.
    snapshot = ProjectArtifactManifestAdapter(project_dir).snapshot_entries()
    assert set(snapshot) == {ArtifactKey.episode_script_plan(1), ArtifactKey.episode_script(1)}
    assert snapshot[ArtifactKey.episode_script_plan(1)].artifact_path == "drafts/episode_1/script_plan_segments.json"


def test_a_project_holding_both_names_keeps_the_new_one_and_sets_the_old_one_aside(tmp_path: Path) -> None:
    """If both names exist, prefer new names, move old files to preserved copies, and report that choice."""

    project_dir = _project(tmp_path, content_mode="narration", generation_mode="storyboard")
    drafts_dir = project_dir / "drafts" / "episode_1"
    _write_json(drafts_dir / "step1_segments.json", {"segments": [{"novel_text": "旧"}]})
    _write_json(drafts_dir / "script_plan_segments.json", {"segments": [{"novel_text": "新"}]})
    old_bytes = (drafts_dir / "step1_segments.json").read_bytes()

    assert migrate_project_with_verdict(project_dir) is None

    assert _read_json(drafts_dir / "script_plan_segments.json") == {"segments": [{"novel_text": "新"}]}
    assert not (drafts_dir / "step1_segments.json").exists()
    [kept] = drafts_dir.glob("step1_segments.json.kept.v*")
    assert kept.read_bytes() == old_bytes
    report = load_migration_report(project_dir)
    assert report is not None
    assert [(item.code, item.episode, item.file, item.backup) for item in report.adjusted] == [
        (
            "draft_name_conflict_resolved",
            1,
            "drafts/episode_1/script_plan_segments.json",
            kept.relative_to(project_dir).as_posix(),
        )
    ]


def test_migrating_twice_is_a_no_op(tmp_path: Path) -> None:
    """Rerunning migration on already-renamed projects makes no changes."""

    project_dir = _project(tmp_path, content_mode="narration", generation_mode="storyboard")
    _write_json(project_dir / "drafts" / "episode_1" / "step1_segments.json", {"segments": []})
    migrate_project_dir(project_dir)
    after_first = _read_json(project_dir / "project.json")

    migrate_v9_to_v10(project_dir)

    assert _read_json(project_dir / "project.json") == after_first
    assert (project_dir / "drafts" / "episode_1" / "script_plan_segments.json").is_file()
