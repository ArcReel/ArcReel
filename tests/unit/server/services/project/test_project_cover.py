"""Project-cover selector unit tests: fallback-chain priority and resilience."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lib.project.project_manager import ProjectManager
from server.services.project.project_cover import resolve_project_cover


def _mk_manager(scripts_by_file: dict[str, dict]) -> MagicMock:
    """Fake ProjectManager: load_script looks up filenames and raises FileNotFoundError on misses.

    get_project_path raises FileNotFoundError, so cover URLs lack version keys; test only selection order. Version-key cases use ``_real_manager`` for actual directory lookup."""
    mgr = MagicMock()
    mgr.get_project_path.side_effect = FileNotFoundError("proj")

    def _load_script(_project_name: str, filename: str) -> dict:
        if filename in scripts_by_file:
            return scripts_by_file[filename]
        raise FileNotFoundError(filename)

    mgr.load_script.side_effect = _load_script
    return mgr


# Three storyboard-image-to-video skeletons: (content_mode, entry-array key).
_STORYBOARD_SKELETONS = [
    pytest.param("narration", "segments", id="narration-segments"),
    pytest.param("drama", "scenes", id="drama-scenes"),
    pytest.param("ad", "shots", id="ad-shots"),
]


def test_returns_video_thumbnail_when_present_in_reference_mode():
    """Generated reference-mode video gives video_thumbnail highest priority."""
    project = {
        "episodes": [{"script_file": "scripts/episode_1.json"}],
        "scenes": {"S": {"scene_sheet": "scenes/s.png"}},
    }
    scripts = {
        "scripts/episode_1.json": {
            "video_units": [
                {"generated_assets": {"video_thumbnail": "reference_videos/thumbnails/E1U1.jpg"}},
            ]
        }
    }
    url = resolve_project_cover(_mk_manager(scripts), "proj", project)
    assert url == "/api/v1/files/proj/reference_videos/thumbnails/E1U1.jpg"


@pytest.mark.parametrize(("content_mode", "items_key"), _STORYBOARD_SKELETONS)
def test_returns_video_thumbnail_in_storyboard_mode(content_mode, items_key):
    """Storyboard-image-to-video entries in all three skeletons select video_thumbnail rather than project-level scene images."""
    project = {
        "episodes": [{"script_file": "scripts/episode_1.json"}],
        "scenes": {"S": {"scene_sheet": "scenes/s.png"}},
    }
    scripts = {
        "scripts/episode_1.json": {
            "content_mode": content_mode,
            items_key: [
                {"generated_assets": {"video_thumbnail": "thumbnails/scene_E1S1.jpg"}},
            ],
        }
    }
    url = resolve_project_cover(_mk_manager(scripts), "proj", project)
    assert url == "/api/v1/files/proj/thumbnails/scene_E1S1.jpg"


def test_video_thumbnail_beats_storyboard_image_across_all_episodes():
    """Any episode's video_thumbnail outranks the first episode's storyboard_image: the core two-pass contract, independent of episode order."""
    project = {
        "episodes": [
            {"script_file": "scripts/episode_1.json"},
            {"script_file": "scripts/episode_2.json"},
        ]
    }
    scripts = {
        "scripts/episode_1.json": {
            "segments": [{"generated_assets": {"storyboard_image": "storyboards/scene_E1S1_first.png"}}]
        },
        "scripts/episode_2.json": {
            "segments": [{"generated_assets": {"video_thumbnail": "thumbnails/scene_E2S1.jpg"}}]
        },
    }
    url = resolve_project_cover(_mk_manager(scripts), "proj", project)
    assert url == "/api/v1/files/proj/thumbnails/scene_E2S1.jpg"


@pytest.mark.parametrize(("content_mode", "items_key"), _STORYBOARD_SKELETONS)
def test_falls_back_to_storyboard_image_when_no_video_thumbnail(content_mode, items_key):
    """Without video frames in any skeleton, select entry storyboard_image, still ahead of project-level scene images."""
    project = {
        "episodes": [{"script_file": "scripts/episode_1.json"}],
        "scenes": {"S": {"scene_sheet": "scenes/s.png"}},
    }
    scripts = {
        "scripts/episode_1.json": {
            "content_mode": content_mode,
            items_key: [
                {"generated_assets": {"storyboard_image": "storyboards/scene_E1S1_first.png"}},
            ],
        }
    }
    url = resolve_project_cover(_mk_manager(scripts), "proj", project)
    assert url == "/api/v1/files/proj/storyboards/scene_E1S1_first.png"


def test_reference_mode_without_generated_assets_falls_back_to_scene_sheet():
    """Reference-to-video without generated video uses the first scene reference image as cover."""
    project = {
        "episodes": [{"script_file": "scripts/episode_1.json"}],
        "scenes": {"酒馆": {"scene_sheet": "scenes/酒馆.png"}},
        "characters": {"张三": {"character_sheet": "characters/张三.png"}},
    }
    scripts = {"scripts/episode_1.json": {"video_units": [{"generated_assets": {"status": "pending"}}]}}
    url = resolve_project_cover(_mk_manager(scripts), "proj", project)
    # scene precedes character.
    assert url == "/api/v1/files/proj/scenes/酒馆.png"


def test_tolerates_corrupt_generated_assets():
    """Treat malformed nondict generated_assets (such as strings) as absent; fall back to scene_sheet without raising."""
    project = {
        "episodes": [{"script_file": "scripts/episode_1.json"}],
        "scenes": {"酒馆": {"scene_sheet": "scenes/酒馆.png"}},
    }
    scripts = {"scripts/episode_1.json": {"video_units": [{"generated_assets": "corrupt"}]}}
    url = resolve_project_cover(_mk_manager(scripts), "proj", project)
    assert url == "/api/v1/files/proj/scenes/酒馆.png"


def test_tolerates_corrupt_item_arrays():
    """Skip malformed nonlist entry arrays, nondict entries and nonstring asset paths as missing, without exceptions or hiding valid same-script assets."""
    project = {
        "episodes": [{"script_file": "scripts/episode_1.json"}],
        "scenes": {"酒馆": {"scene_sheet": "scenes/酒馆.png"}},
    }
    scripts = {
        "scripts/episode_1.json": {
            "segments": {"E1S1": "corrupt"},
            "scenes": [
                "corrupt",
                None,
                {"generated_assets": {"video_thumbnail": 1}},
                {"generated_assets": {"video_thumbnail": "thumbnails/scene_E1S2.jpg"}},
            ],
            "shots": 0,
        }
    }
    url = resolve_project_cover(_mk_manager(scripts), "proj", project)
    assert url == "/api/v1/files/proj/thumbnails/scene_E1S2.jpg"


def test_falls_back_to_character_sheet_when_no_scenes():
    project = {
        "episodes": [],
        "characters": {"张三": {"character_sheet": "characters/张三.png"}},
    }
    url = resolve_project_cover(_mk_manager({}), "proj", project)
    assert url == "/api/v1/files/proj/characters/张三.png"


def test_returns_none_for_empty_project():
    url = resolve_project_cover(_mk_manager({}), "proj", {})
    assert url is None


def test_missing_script_file_does_not_break_fallback():
    """Missing/corrupt scripts/episode_N.json still reaches asset fallback."""
    project = {
        "episodes": [{"script_file": "scripts/episode_missing.json"}],
        "scenes": {"S": {"scene_sheet": "scenes/s.png"}},
    }
    url = resolve_project_cover(_mk_manager({}), "proj", project)
    assert url == "/api/v1/files/proj/scenes/s.png"


def test_episode_without_script_file_is_skipped():
    """Episode entries lacking script_file (planning incomplete) are skipped without error."""
    project = {
        "episodes": [{"episode": 1}],
        "characters": {"X": {"character_sheet": "characters/x.png"}},
    }
    url = resolve_project_cover(_mk_manager({}), "proj", project)
    assert url == "/api/v1/files/proj/characters/x.png"


def test_preloaded_scripts_skips_manager_load():
    """If preloaded_scripts covers every episode, never call manager.load_script again.

    list_projects hot-path contract: share script loads with project-summary projection, avoiding separate cover/status JSON parses."""
    project = {
        "episodes": [
            {"script_file": "scripts/episode_1.json"},
            {"script_file": "scripts/episode_2.json"},
        ],
    }
    preloaded = {
        "scripts/episode_1.json": {"segments": []},
        "scripts/episode_2.json": {"segments": [{"generated_assets": {"video_thumbnail": "thumbnails/E2.jpg"}}]},
    }
    mgr = _mk_manager({})  # Empty map: absent preload use, every lookup would raise FileNotFoundError.
    url = resolve_project_cover(mgr, "proj", project, preloaded_scripts=preloaded)
    assert url == "/api/v1/files/proj/thumbnails/E2.jpg"
    mgr.load_script.assert_not_called()


def test_preloaded_scripts_falls_back_to_manager_for_missing_entries():
    """Episodes absent from preloaded_scripts fall back to manager.load_script, preserving best-effort behavior."""
    project = {
        "episodes": [
            {"script_file": "scripts/episode_1.json"},
            {"script_file": "scripts/episode_2.json"},
        ],
    }
    preloaded = {
        "scripts/episode_1.json": {"segments": []},
    }
    # Manager supplies only episode_2, simulating episode 1 preloaded and episode 2 requiring source lookup.
    mgr = _mk_manager(
        {"scripts/episode_2.json": {"segments": [{"generated_assets": {"video_thumbnail": "thumbnails/E2.jpg"}}]}}
    )
    url = resolve_project_cover(mgr, "proj", project, preloaded_scripts=preloaded)
    assert url == "/api/v1/files/proj/thumbnails/E2.jpg"
    # Preloaded episode_1 must not invoke load_script; episode_2 missing from preload falls back once.
    called_files = {call.args[1] for call in mgr.load_script.call_args_list}
    assert called_files == {"scripts/episode_2.json"}


def test_mixed_segments_and_video_units_do_not_shadow_each_other():
    """Pending shell video_units inside storyboard-image-to-video scripts must not hide real segments video_thumbnail / storyboard_image or force scene_sheet fallback."""
    project = {
        "episodes": [{"script_file": "scripts/episode_1.json"}],
        "scenes": {"选秀大殿": {"scene_sheet": "scenes/选秀大殿.png"}},
    }
    scripts = {
        "scripts/episode_1.json": {
            "segments": [
                {"generated_assets": {"storyboard_image": "storyboards/scene_E1S1.png"}},
                {"generated_assets": {"video_thumbnail": "thumbnails/scene_E1S1.jpg"}},
            ],
            "video_units": [
                {"unit_id": "E1U1", "generated_assets": {"status": "pending"}},
                {"unit_id": "E1U2", "generated_assets": {"status": "pending"}},
            ],
        }
    }
    url = resolve_project_cover(_mk_manager(scripts), "proj", project)
    assert url == "/api/v1/files/proj/thumbnails/scene_E1S1.jpg"


@pytest.mark.parametrize(
    "sheet_value",
    [None, "", 0],
)
def test_ignores_falsy_sheet_values(sheet_value):
    """Skip scene_sheet/character_sheet None, empty strings and numeric zero rather than selecting them."""
    project = {
        "episodes": [],
        "scenes": {"S": {"scene_sheet": sheet_value}},
        "characters": {"X": {"character_sheet": "characters/x.png"}},
    }
    url = resolve_project_cover(_mk_manager({}), "proj", project)
    assert url == "/api/v1/files/proj/characters/x.png"


def _real_manager(tmp_path: Path, *, create: str | None = "proj") -> ProjectManager:
    """Real ProjectManager with data root in tmp_path; ``create`` lists project names to prepare."""
    manager = ProjectManager(tmp_path)
    if create is not None:
        manager.create_project(create)
    return manager


def test_cover_url_carries_mtime_version_key_when_file_exists(tmp_path: Path):
    """Existing cover files append ?v=<mtime_ns>; regeneration changes mtime and therefore URL."""
    project = {"scenes": {"酒馆": {"scene_sheet": "scenes/酒馆.png"}}}
    manager = _real_manager(tmp_path)
    sheet = manager.get_project_path("proj") / "scenes" / "酒馆.png"
    sheet.parent.mkdir(exist_ok=True)
    sheet.write_bytes(b"png")
    os.utime(sheet, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))

    first = resolve_project_cover(manager, "proj", project)
    assert first == "/api/v1/files/proj/scenes/酒馆.png?v=1700000000000000000"

    os.utime(sheet, ns=(1_700_000_060_000_000_000, 1_700_000_060_000_000_000))
    assert resolve_project_cover(manager, "proj", project) != first


def test_cover_url_has_no_version_key_when_file_missing(tmp_path: Path):
    """Registered cover paths with missing files omit version keys and pass the unchanged URL to files routes."""
    project = {"scenes": {"酒馆": {"scene_sheet": "scenes/酒馆.png"}}}
    url = resolve_project_cover(_real_manager(tmp_path), "proj", project)
    assert url == "/api/v1/files/proj/scenes/酒馆.png"


@pytest.mark.parametrize("project_name", ["ghost", "../escape"], ids=["missing-project", "traversal-name"])
def test_cover_url_has_no_version_key_when_project_lookup_fails(tmp_path: Path, project_name: str):
    """Missing/invalid project-directory lookup does not raise; return cover URL without a version key."""
    project = {"scenes": {"酒馆": {"scene_sheet": "scenes/酒馆.png"}}}
    url = resolve_project_cover(_real_manager(tmp_path, create=None), project_name, project)
    assert url == f"/api/v1/files/{project_name}/scenes/酒馆.png"
