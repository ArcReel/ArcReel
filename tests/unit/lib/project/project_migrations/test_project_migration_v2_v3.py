"""v2→v3: version stamping only; byte-identical episodes, version guards, idempotency, and remainder preservation."""

import json
from pathlib import Path

import pytest

from lib.project.data_validator import DataValidator
from lib.project.project_migrations import v2_to_v3_episode_ledger
from lib.project.project_migrations.runner import migrate_project_dir
from lib.project.project_migrations.v2_to_v3_episode_ledger import migrate_v2_to_v3

NOVEL = "第一集的正文内容。第二集还没拆出来的余文。"
EP1 = "第一集的正文内容。"


def _write(tmp_path: Path, data: dict) -> Path:
    d = tmp_path / "demo"
    d.mkdir()
    (d / "project.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    source = d / "source"
    source.mkdir()
    (source / "novel.txt").write_text(NOVEL, encoding="utf-8")
    (source / "episode_1.txt").write_text(EP1, encoding="utf-8")
    (source / "_remaining.txt").write_text(NOVEL[len(EP1) :], encoding="utf-8")
    return d


def _load(d: Path) -> dict:
    return json.loads((d / "project.json").read_text(encoding="utf-8"))


def test_bumps_schema_version_only(tmp_path: Path):
    """Write only schema_version: preserve episodes exactly, adding no ledger fields and deriving no planning_cursor."""
    episodes = [{"episode": 1, "title": "开端", "script_file": "scripts/episode_1.json"}]
    d = _write(tmp_path, {"schema_version": 2, "episodes": episodes})
    migrate_v2_to_v3(d)
    data = _load(d)
    assert data["schema_version"] == 3
    assert data["episodes"] == episodes
    assert "planning_cursor" not in data


def test_upgraded_project_passes_consumer_side_validation(tmp_path: Path):
    """Upgraded projects remain valid for consumers; ledger entries lacking position records are not corrupt.

    Stamping intentionally leaves entries without ``source_range`` / ``ledger_status``, normal after legacy upgrades.
    The validator guards all consumers (scripts / media / status / export); its acceptance establishes continued operation
    after upgrade. Planning separately rejects these entries; see ``test_episode_planner``.
    """
    d = _write(
        tmp_path,
        {
            "schema_version": 2,
            "title": "Demo",
            "content_mode": "narration",
            "style": "Anime",
            "characters": {"姜月茴": {"description": "女主"}},
            "scenes": {"古宅": {"description": "废弃古宅"}},
            "props": {"玉佩": {"description": "关键道具"}},
            "episodes": [{"episode": 1, "title": "开端", "script_file": "scripts/episode_1.json"}],
        },
    )
    # Validation requires current schema shapes (such as mandatory generation_mode); projects entering consumers
    # have completed the full migration chain, so likewise upgrade to current before validating here.
    migrate_project_dir(d)
    result = DataValidator(projects_dir=str(tmp_path)).validate_project_payload(_load(d))
    assert result.errors == []


def test_existing_ledger_fields_preserved_verbatim(tmp_path: Path):
    """Preserve existing ledger fields in v2 projects, whether manually or previously written; migration does not rewrite them."""
    episodes = [
        {
            "episode": 1,
            "title": "开端",
            "script_file": "scripts/episode_1.json",
            "source_range": {"source_file": "source/novel.txt", "start": 0, "end": len(EP1)},
            "ledger_status": "consumed",
        }
    ]
    d = _write(tmp_path, {"schema_version": 2, "episodes": episodes, "planning_cursor": None})
    migrate_v2_to_v3(d)
    data = _load(d)
    assert data["episodes"] == episodes
    assert data["planning_cursor"] is None


@pytest.fixture
def no_write_guard(monkeypatch: pytest.MonkeyPatch):
    """A functioning guard means no disk writes at all.

    Stamping writes only schema_version; if the guard fails and remigrates an existing v3 project, written content
    still matches the original. Unchanged field/content assertions cannot disprove the bug; observe actual writes.
    """
    calls: list[Path] = []

    def _spy(path: Path, data: dict) -> None:
        calls.append(path)

    monkeypatch.setattr(v2_to_v3_episode_ledger, "atomic_write_json", _spy)
    return calls


def test_version_guard_skips_already_v3(tmp_path: Path, no_write_guard: list[Path]):
    d = _write(
        tmp_path,
        {
            "schema_version": 3,
            "episodes": [{"episode": 1, "title": "开端", "script_file": "scripts/episode_1.json"}],
        },
    )
    migrate_v2_to_v3(d)
    assert no_write_guard == []


def test_string_schema_version_is_normalized(tmp_path: Path):
    """Historical project.json may store string versions; guards normalize with int instead of raising TypeError."""
    d = _write(tmp_path, {"schema_version": "2", "episodes": []})
    migrate_v2_to_v3(d)
    assert _load(d)["schema_version"] == 3


def test_string_schema_version_guard_skips_v3(tmp_path: Path, no_write_guard: list[Path]):
    d = _write(
        tmp_path,
        {
            "schema_version": "3",
            "episodes": [{"episode": 1, "title": "开端", "script_file": "scripts/episode_1.json"}],
        },
    )
    migrate_v2_to_v3(d)
    assert no_write_guard == []


def test_double_run_idempotent_at_file_level(tmp_path: Path):
    d = _write(tmp_path, {"schema_version": 2, "episodes": []})
    migrate_v2_to_v3(d)
    first = (d / "project.json").read_bytes()
    migrate_v2_to_v3(d)
    assert (d / "project.json").read_bytes() == first


def test_remaining_file_preserved(tmp_path: Path):
    """Preserve remainder files: migration touches only project.json, never files under source/."""
    d = _write(tmp_path, {"schema_version": 2, "episodes": []})
    migrate_v2_to_v3(d)
    assert (d / "source" / "_remaining.txt").read_text(encoding="utf-8") == NOVEL[len(EP1) :]


def test_missing_project_json_is_noop(tmp_path: Path):
    """Without project.json, migration neither raises nor creates any files."""
    empty = tmp_path / "empty"
    empty.mkdir()

    assert migrate_v2_to_v3(empty) is None
    assert list(empty.iterdir()) == []
