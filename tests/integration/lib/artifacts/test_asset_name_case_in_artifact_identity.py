"""Asset/derivative-name deduplication ignores case; artifact identity/basis retain original name casing, so deduplication rules never alter manifest keys or basis digests."""

from __future__ import annotations

import json
from pathlib import Path

from lib.artifacts.artifact_activation import plan_artifact_target_state
from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactManifestEntry
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION


def test_an_asset_sheet_key_and_basis_digest_use_the_name_as_written(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "demo"
    (project_dir / "characters").mkdir(parents=True)
    (project_dir / "characters" / "Alice.png").write_bytes(b"\x89PNG alice sheet")
    project = {
        "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
        "title": "demo",
        "content_mode": "narration",
        "generation_mode": "storyboard",
        "aspect_ratio": "9:16",
        "style": "Anime",
        "style_description": "cinematic",
        "episodes": [],
        "characters": {"Alice": {"description": "Heroine", "character_sheet": "characters/Alice.png"}},
    }
    (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

    plan = plan_artifact_target_state(project_dir)

    # Fixed values lock manifest keys/basis digests: original name casing participates; changing it would mismatch or expire existing registrations.
    assert dict(plan.entries) == {
        ArtifactKey.asset_sheet("character", "Alice"): ArtifactManifestEntry(
            artifact_path="characters/Alice.png",
            basis_digest="sha256-v1:4a197985b87d33ba11265bbdb63aeee598ce88f6c625e11deac442b8026567dd",
        )
    }
    assert ArtifactKey.asset_sheet("character", "Alice").encode() == (
        "artifact-key-v1:WyJhc3NldC1zaGVldCIsImNoYXJhY3RlciIsIkFsaWNlIl0"
    )


def test_a_derivative_sheet_key_and_basis_digest_use_the_name_as_written(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "demo"
    (project_dir / "characters" / "derivatives" / "Alice").mkdir(parents=True)
    (project_dir / "characters" / "Alice.png").write_bytes(b"\x89PNG alice sheet")
    (project_dir / "characters" / "derivatives" / "Alice" / "Coat.png").write_bytes(b"\x89PNG alice coat sheet")
    project = {
        "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
        "title": "demo",
        "content_mode": "narration",
        "generation_mode": "storyboard",
        "aspect_ratio": "9:16",
        "style": "Anime",
        "style_description": "cinematic",
        "episodes": [],
        "characters": {
            "Alice": {
                "description": "Heroine",
                "character_sheet": "characters/Alice.png",
                "derivatives": {
                    "Coat": {
                        "description": "Wears a red coat",
                        "character_sheet": "characters/derivatives/Alice/Coat.png",
                    }
                },
            }
        },
    }
    (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

    plan = plan_artifact_target_state(project_dir)

    # Fixed values lock derivative-image manifest keys/basis digests: both base and derivative names retain original casing.
    key = ArtifactKey.asset_sheet("character", "Alice/Coat")
    assert plan.entries[key] == ArtifactManifestEntry(
        artifact_path="characters/derivatives/Alice/Coat.png",
        basis_digest="sha256-v1:52ab07fc1289c526ea3c39cda4eb564817da5307d0a034468258bc4272e2f0ca",
    )
    assert key.encode() == "artifact-key-v1:WyJhc3NldC1zaGVldCIsImNoYXJhY3RlciIsIkFsaWNlL0NvYXQiXQ"
