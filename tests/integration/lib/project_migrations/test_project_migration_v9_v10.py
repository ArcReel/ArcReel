"""v9→v10 脚本规划 / 提示词编写落盘改名迁移。"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest

from lib import script_review
from lib.project_migration_failure import load_migration_failure
from lib.project_migrations.runner import migrate_project_dir, migrate_project_with_verdict
from lib.project_migrations.v9_to_v10_script_plan_naming import migrate_v9_to_v10
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION

#: 三种脚本规划变体：content_mode / generation_mode / v9 草稿文件名 / 迁移后文件名。
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


def _artifact_key(kind: str, episode: int) -> str:
    payload = json.dumps([kind, episode], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return "artifact-v1:" + base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_kind(encoded_key: str) -> str:
    token = encoded_key.removeprefix("artifact-v1:")
    raw = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
    return json.loads(raw.decode("utf-8"))[0]


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
    """三种脚本规划变体：文件与字段改名后确认记录随之平移，该集仍是已确认。"""

    draft = {"title": "第 1 集", "scenes": [{"scene_id": "S1"}]}
    project_dir = _project(tmp_path, content_mode=content_mode, generation_mode=generation_mode)
    draft_path = project_dir / "drafts" / "episode_1" / old_name
    _write_json(draft_path, draft)
    fingerprint = script_review.content_fingerprint(draft_path)
    project = _read_json(project_dir / "project.json")
    project["episodes"][0]["step1_review"] = {"fingerprint": fingerprint, "confirmed_at": "2026-01-01T00:00:00Z"}
    _write_json(project_dir / "project.json", project)
    _write_json(project_dir / "scripts" / "episode_1.json", {"metadata": {"step1_revision": fingerprint}})

    assert migrate_project_dir(project_dir) is True

    migrated = _read_json(project_dir / "project.json")
    assert migrated["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION
    assert not draft_path.exists()
    assert _read_json(project_dir / "drafts" / "episode_1" / new_name) == draft
    episode = migrated["episodes"][0]
    assert "step1_review" not in episode
    assert episode["script_plan_review"] == {"fingerprint": fingerprint, "confirmed_at": "2026-01-01T00:00:00Z"}
    assert _read_json(project_dir / "scripts" / "episode_1.json")["metadata"] == {"script_plan_revision": fingerprint}
    # 确认指纹是内容指纹，改名不动内容：迁移后这一集仍是已确认、不重新阻塞提示词编写。
    assert script_review.review_status(project_dir, migrated, 1) == "confirmed"


def test_invalid_drafts_and_legacy_markdown_aliases_are_renamed_too(tmp_path: Path) -> None:
    """待修复草稿与旧版 ``.md`` 别名同批改名，正文一字不动。"""

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
    """产物清单里脚本规划产物的 kind 与草稿路径同批改写，指纹原样保留。"""

    project_dir = _project(tmp_path, content_mode="narration", generation_mode="storyboard")
    _write_json(project_dir / "drafts" / "episode_1" / "step1_segments.json", {"segments": []})
    digest = "sha256-v1:" + "0" * 64
    _write_json(
        project_dir / ".arcreel_artifacts.json",
        {
            "schema_version": 1,
            "hash_algorithm": "sha256",
            "entries": {
                _artifact_key("episode-step1", 1): {
                    "artifact_path": "drafts/episode_1/step1_segments.json",
                    "basis_digest": digest,
                },
                _artifact_key("episode-script", 1): {
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


def test_a_project_already_holding_both_names_is_refused_without_touching_disk(tmp_path: Path) -> None:
    """新旧两个名字同时在场时拒绝改名：无从判定保留哪一份，项目目录一个字节都不动。"""

    project_dir = _project(tmp_path, content_mode="narration", generation_mode="storyboard")
    drafts_dir = project_dir / "drafts" / "episode_1"
    _write_json(drafts_dir / "step1_segments.json", {"segments": [{"novel_text": "旧"}]})
    _write_json(drafts_dir / "script_plan_segments.json", {"segments": [{"novel_text": "新"}]})
    snapshot = {
        str(path.relative_to(project_dir)): path.read_bytes()
        for path in sorted(project_dir.rglob("*"))
        if path.is_file()
    }

    record = migrate_project_with_verdict(project_dir)

    assert record is not None
    assert load_migration_failure(project_dir) is not None
    survivors = {
        str(path.relative_to(project_dir)): path.read_bytes()
        for path in sorted(project_dir.rglob("*"))
        if path.is_file() and not path.name.startswith(".migration")
    }
    assert {name: data for name, data in survivors.items() if name in snapshot} == snapshot
    assert _read_json(project_dir / "project.json")["schema_version"] == 9


def test_migrating_twice_is_a_no_op(tmp_path: Path) -> None:
    """已在新名下的项目重跑迁移不产生任何改动。"""

    project_dir = _project(tmp_path, content_mode="narration", generation_mode="storyboard")
    _write_json(project_dir / "drafts" / "episode_1" / "step1_segments.json", {"segments": []})
    migrate_project_dir(project_dir)
    after_first = _read_json(project_dir / "project.json")

    migrate_v9_to_v10(project_dir)

    assert _read_json(project_dir / "project.json") == after_first
    assert (project_dir / "drafts" / "episode_1" / "script_plan_segments.json").is_file()
