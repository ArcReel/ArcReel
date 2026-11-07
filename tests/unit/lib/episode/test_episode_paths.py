"""Behavioral tests for script_plan / episode path sources of truth.

Public contracts only: structured script_plan filenames, legacy .md acceptance boundaries, episode
script paths, and single-registration coverage of new content_mode across gates/web/Agent writes.
"""

from __future__ import annotations

from pathlib import Path

from lib.episode import episode_paths
from lib.script import script_review
from server.routers import files


def test_script_plan_filename_by_content_mode():
    assert episode_paths.script_plan_filename("drama") == "script_plan_normalized_script.json"
    assert episode_paths.script_plan_filename("narration") == "script_plan_segments.json"
    # ad does not use structured script_plan.
    assert episode_paths.script_plan_filename("ad") is None
    assert episode_paths.script_plan_filename("unknown") is None


def test_script_plan_read_candidates_includes_legacy_md():
    assert episode_paths.script_plan_read_candidates("narration") == (
        "script_plan_segments.json",
        "script_plan_segments.md",
    )
    assert episode_paths.script_plan_read_candidates("drama") == (
        "script_plan_normalized_script.json",
        "script_plan_normalized_script.md",
    )
    assert episode_paths.script_plan_read_candidates("ad") == ()


def test_episode_script_paths():
    assert episode_paths.episode_script_filename(3) == "episode_3.json"
    assert episode_paths.episode_script_relpath(3) == "scripts/episode_3.json"


def test_episode_drafts_dir():
    assert episode_paths.episode_drafts_dir(Path("/p"), 2) == Path("/p/drafts/episode_2")


def test_new_content_mode_registered_once_covers_gate_and_web(monkeypatch, tmp_path):
    """One new SCRIPT_PLAN_FILENAMES mode must automatically align gate paths and web stage files.

    Project summaries derive episode ``script_status`` from script_plan/formal-script artifact state,
    probing this same gate path; no fourth candidate-filename table needs synchronization.
    """
    monkeypatch.setitem(episode_paths.SCRIPT_PLAN_FILENAMES, "docudrama", "script_plan_docu.json")

    # Content confirmation: script_plan_path uses the registered structured filename.
    project = {"content_mode": "docudrama", "episodes": [{"episode": 1}]}
    gate_path = script_review.script_plan_path(tmp_path, project, 1)
    assert gate_path is not None
    assert gate_path == tmp_path / "drafts" / "episode_1" / "script_plan_docu.json"

    # Web draft I/O: _stage_files returns the same filename.
    assert files._stage_files("docudrama") == {"script_plan": "script_plan_docu.json"}


def test_ad_has_no_structured_script_plan_across_web_and_gate(tmp_path):
    """ad has no structured script_plan: empty web stage mapping and None gate path."""
    # Web drafts: ad returns empty mappings, never drama filenames; ad precedes generation_mode,
    # so reference_video markers still do not create script_plan.
    assert files._stage_files("ad") == {}
    assert files._stage_files("ad", generation_mode="reference_video") == {}
    # Gate: no structured ad script_plan to probe, so episode script progress cannot become segmented.
    assert script_review.script_plan_path(tmp_path, {"content_mode": "ad", "episodes": [{"episode": 1}]}, 1) is None


def test_gate_only_json_and_web_also_md():
    """Gates accept only structured .json; web reads also accept legacy .md, an existing distinction (ADR 0041)."""
    # Gate registry contains no .md.
    assert all(name.endswith(".json") for name in episode_paths.SCRIPT_PLAN_FILENAMES.values())
    # Web candidates include legacy .md.
    assert "script_plan_segments.md" in episode_paths.script_plan_read_candidates("narration")
    assert "script_plan_normalized_script.md" in episode_paths.script_plan_read_candidates("drama")


def test_script_plan_path_follows_reference_video_across_content_modes(tmp_path):
    """rv generation_mode crosses content_mode: narration/drama gate paths use rv-specific structured
    filenames instead of content_mode names; episode artifact-state progress probes the same path.
    """
    for content_mode in ("narration", "drama"):
        project = {"content_mode": content_mode, "generation_mode": "reference_video", "episodes": [{"episode": 1}]}
        assert script_review.script_plan_path(tmp_path, project, 1) == (
            episode_paths.episode_drafts_dir(tmp_path, 1) / episode_paths.REFERENCE_VIDEO_SCRIPT_PLAN_FILENAME
        )
        # Failed first split validation produces drafts only, never formal files; separate draft probing
        # still marks the episode segmented rather than returning to source review.
        assert script_review.script_plan_quarantine_path(tmp_path, project, 1) == (
            episode_paths.episode_drafts_dir(tmp_path, 1)
            / episode_paths.REFERENCE_VIDEO_SCRIPT_PLAN_QUARANTINE_FILENAME
        )

    # Non-rv projects retain content_mode filenames unchanged.
    assert script_review.script_plan_path(tmp_path, {"content_mode": "narration", "episodes": [{"episode": 1}]}, 1) == (
        episode_paths.episode_drafts_dir(tmp_path, 1) / episode_paths.script_plan_filename("narration")
    )
    # ad precedes generation_mode: even rv has no structured script_plan, matching gate/web behavior above.
    assert (
        script_review.script_plan_path(
            tmp_path,
            {"content_mode": "ad", "generation_mode": "reference_video", "episodes": [{"episode": 1}]},
            1,
        )
        is None
    )
