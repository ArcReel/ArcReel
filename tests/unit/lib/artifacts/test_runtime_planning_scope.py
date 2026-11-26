import json
import weakref
from pathlib import Path

import pytest

from lib.artifacts.artifact_activation import assert_artifact_target_state_plan_unchanged
from lib.artifacts.artifact_currency import ArtifactCurrencyResolver
from lib.artifacts.artifact_manifest import ArtifactKey
from lib.artifacts.artifact_planner import TargetStatePlanner
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION


def test_runtime_releases_each_episode_and_its_evidence(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    episodes = []
    for episode in range(1, 687):
        script = f"scripts/episode_{episode}.json"
        episodes.append({"episode": episode, "script_file": script})
        (tmp_path / script).write_text(json.dumps({"episode": episode, "segments": []}))
    project = {"schema_version": CURRENT_PROJECT_SCHEMA_VERSION, "content_mode": "narration", "episodes": episodes}
    resolver = ArtifactCurrencyResolver(tmp_path, project=project)

    previous = None
    for episode in range(1, 687):
        key = ArtifactKey.episode_script(episode)
        resolver.resolve_basis(key)
        planner = resolver._planner_for(key)
        assert planner.project is project
        assert planner.episodes[0].episode == episode
        assert not planner.dependencies
        assert previous is None or previous() is None
        previous = weakref.ref(planner)
        del planner
    resolver.release_episode()
    assert previous is not None
    assert previous() is None


def test_single_sheet_avoids_unrelated_asset_entries(tmp_path: Path) -> None:
    class DirectLookup(dict):
        sealed = False

        def _check(self):
            if self.sealed:
                raise AssertionError("single-sheet lookup enumerated the project asset bucket")

        def __iter__(self):
            self._check()
            return super().__iter__()

        def keys(self):
            self._check()
            return super().keys()

        def values(self):
            self._check()
            return super().values()

        def items(self):
            self._check()
            return super().items()

    characters = DirectLookup({str(i): {} for i in range(400)})
    characters["hero"] = {"description": "traveller", "character_sheet": "characters/hero.png"}
    characters["Cafe\u0301"] = {"description": "traveller", "character_sheet": "characters/Café.png"}
    project = {"schema_version": CURRENT_PROJECT_SCHEMA_VERSION, "characters": characters}
    resolver = ArtifactCurrencyResolver(tmp_path, project=project)
    characters.sealed = True
    assert resolver.resolve_basis(ArtifactKey.asset_sheet("character", "hero")) is not None
    assert resolver.resolve_basis(ArtifactKey.asset_sheet("character", "Café")) is not None
    assert resolver.resolve_basis(ArtifactKey.asset_sheet("character", "absent")) is None


@pytest.mark.parametrize(
    "appeared",
    ["characters/hero.png", "scripts/added.json", "grids/grid_123456789abc.json", "bgm/bgm-12345678.mp3"],
)
def test_preflight_refuses_new_input(tmp_path: Path, appeared: str) -> None:
    metadata = {
        "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
        "characters": {"hero": {"description": "hero", "character_sheet": "characters/hero.png"}},
        "bgm": {
            "bgm-12345678": {
                "name": "Theme",
                "file": "bgm/bgm-12345678.mp3",
                "duration_us": 10_000_000,
                "integrated_lufs": -16.0,
                "gain_db": 0.0,
                "content_digest": "sha256:" + "0" * 64,
                "uploaded_at": "2026-11-25T00:00:00Z",
            }
        },
    }
    (tmp_path / "project.json").write_text(json.dumps(metadata))
    plan = TargetStatePlanner(tmp_path).plan()
    path = tmp_path / appeared
    path.parent.mkdir()
    path.write_bytes(b"{}")
    with pytest.raises(RuntimeError, match=r"after .*preflight"):
        assert_artifact_target_state_plan_unchanged(tmp_path, plan)
