"""v11→v12：补做 v9→v10 漏掉的脚本规划产物 key 改名。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lib.artifact_manifest import (
    MANIFEST_SCHEMA_VERSION,
    ArtifactKey,
    ArtifactManifestError,
    ProjectArtifactManifestAdapter,
    encode_artifact_key_parts,
)
from lib.content_digest import HASH_ALGORITHM
from lib.project_migration_failure import load_migration_failure
from lib.project_migrations.runner import migrate_project_dir, migrate_project_with_verdict
from lib.project_migrations.v11_to_v12_script_plan_artifact_key_repair import migrate_v11_to_v12
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION

_DIGEST = f"{HASH_ALGORITHM}:" + "0" * 64
_MANIFEST_FILENAME = ".arcreel_artifacts.json"

#: v9 时期脚本规划产物的 key：该 kind 已从 ``ArtifactKind`` 删除，只能走公开编码出口构造。
_LEGACY_SCRIPT_PLAN_KEY = encode_artifact_key_parts("episode-step1", (1,))


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _snapshot(project_dir: Path) -> dict[str, bytes]:
    """项目目录里每个文件的相对路径与字节内容：用来断言「一个字节都没动」。"""

    return {
        str(path.relative_to(project_dir)): path.read_bytes()
        for path in sorted(project_dir.rglob("*"))
        if path.is_file()
    }


def _project(tmp_path: Path, *, manifest_entries: dict[str, Any] | None = None) -> Path:
    """造一个走过「空操作版 v9→v10」的项目：草稿与清单路径已是新名，key 仍是旧的。"""

    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    _write_json(
        project_dir / "project.json",
        {
            "schema_version": 11,
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "character_voice_binding": "prompt",
            "episodes": [{"episode": 1, "title": "第 1 集", "script_file": "scripts/episode_1.json"}],
        },
    )
    _write_json(project_dir / "drafts" / "episode_1" / "script_plan_segments.json", {"segments": []})
    if manifest_entries is not None:
        _write_json(
            project_dir / _MANIFEST_FILENAME,
            {
                "schema_version": MANIFEST_SCHEMA_VERSION,
                "hash_algorithm": HASH_ALGORITHM,
                "entries": manifest_entries,
            },
        )
    return project_dir


def _legacy_entries() -> dict[str, Any]:
    return {
        _LEGACY_SCRIPT_PLAN_KEY: {
            "artifact_path": "drafts/episode_1/script_plan_segments.json",
            "basis_digest": _DIGEST,
        },
        ArtifactKey.episode_script(1).encode(): {
            "artifact_path": "scripts/episode_1.json",
            "basis_digest": _DIGEST,
        },
    }


def test_a_residual_legacy_key_blocks_the_whole_manifest_rather_than_one_artifact(tmp_path: Path) -> None:
    """本迁移存在的依据：残留旧 key 不是「该产物按缺失处理」，而是整份清单读不出来。

    读侧对不认识的 kind 直接拒收整份清单，同项目其余产物一并变成不可读，没有自愈路径——
    因而必须补一次修复，不能靠下一次生成自然重登记。
    """

    project_dir = _project(tmp_path, manifest_entries=_legacy_entries())

    with pytest.raises(ArtifactManifestError):
        ProjectArtifactManifestAdapter(project_dir).snapshot_entries()


def test_repairs_the_key_left_behind_by_the_broken_rename(tmp_path: Path) -> None:
    """旧 key 就地改名为当前登记所用的 key，路径与指纹一字不动，清单恢复可读。"""

    project_dir = _project(tmp_path, manifest_entries=_legacy_entries())

    assert migrate_project_dir(project_dir) is True

    assert _read_json(project_dir / "project.json")["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION
    snapshot = ProjectArtifactManifestAdapter(project_dir).snapshot_entries()
    assert set(snapshot) == {ArtifactKey.episode_script_plan(1), ArtifactKey.episode_script(1)}
    plan_entry = snapshot[ArtifactKey.episode_script_plan(1)]
    assert plan_entry.artifact_path == "drafts/episode_1/script_plan_segments.json"
    assert plan_entry.basis_digest == _DIGEST
    assert snapshot[ArtifactKey.episode_script(1)].artifact_path == "scripts/episode_1.json"


def test_a_manifest_already_on_the_new_key_is_left_untouched(tmp_path: Path) -> None:
    """已在新 key 下的项目只升版本：清单一个字节不改，也不留备份。"""

    entries = {
        ArtifactKey.episode_script_plan(1).encode(): {
            "artifact_path": "drafts/episode_1/script_plan_segments.json",
            "basis_digest": _DIGEST,
        }
    }
    project_dir = _project(tmp_path, manifest_entries=entries)
    manifest_file = project_dir / _MANIFEST_FILENAME
    before = manifest_file.read_bytes()

    assert migrate_project_dir(project_dir) is True

    assert manifest_file.read_bytes() == before
    assert not list(project_dir.glob(f"{_MANIFEST_FILENAME}.bak.*"))


def test_a_project_without_a_manifest_only_bumps_its_version(tmp_path: Path) -> None:
    project_dir = _project(tmp_path)

    assert migrate_project_dir(project_dir) is True

    assert _read_json(project_dir / "project.json")["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION
    assert not (project_dir / _MANIFEST_FILENAME).exists()


def test_rerunning_the_repair_is_a_no_op(tmp_path: Path) -> None:
    """升到当前版本后重跑：清单与 project.json 都不再变化。"""

    project_dir = _project(tmp_path, manifest_entries=_legacy_entries())
    migrate_project_dir(project_dir)
    after_first = _snapshot(project_dir)

    migrate_v11_to_v12(project_dir)

    assert _snapshot(project_dir) == after_first


def test_a_corrupt_manifest_is_refused_before_anything_is_written(tmp_path: Path) -> None:
    """清单形状损坏时落「需要修复」裁决，且项目目录一个字节都没被动过。

    只读预检拒绝的项目连版本化备份都不该留下：本步自持 project.json 的备份，落盘只发生在
    预检全部通过之后，裁决文件是唯一的新增。
    """

    project_dir = _project(tmp_path)
    _write_json(
        project_dir / _MANIFEST_FILENAME,
        {"schema_version": MANIFEST_SCHEMA_VERSION, "hash_algorithm": HASH_ALGORITHM, "entries": []},
    )
    before = _snapshot(project_dir)

    record = migrate_project_with_verdict(project_dir)

    assert record is not None
    assert load_migration_failure(project_dir) is not None
    assert _read_json(project_dir / "project.json")["schema_version"] == 11
    assert {path: payload for path, payload in _snapshot(project_dir).items() if path != ".migration_failure.json"} == (
        before
    )
