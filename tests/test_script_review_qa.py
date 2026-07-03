from pathlib import Path

import pytest

import lib.script_review as script_review
from lib.json_io import atomic_write_json
from lib.project_manager import ProjectManager
from server.services.script_review import ScriptReviewError, ScriptReviewService


def _drama_step1(
    *,
    props: list[str] | None = None,
    scene_description: str | None = None,
    duration_seconds: int = 8,
) -> dict:
    return {
        "title": "第一集",
        "scenes": [
            {
                "scene_id": "E1S01",
                "duration_seconds": duration_seconds,
                "segment_break": False,
                "characters_in_scene": ["阿离"],
                "scenes": ["屋檐"],
                "props": props if props is not None else ["信纸"],
                "scene_description": scene_description
                if scene_description is not None
                else "突然，阿离在屋檐下拆开信纸，脸色骤变！",
                "utterances": [{"kind": "dialogue", "speaker": "阿离", "text": "真相？"}],
                "source_text": "突然，阿离拆开信纸，发现真相？",
            }
        ],
    }


def _make_project(tmp_path: Path) -> ProjectManager:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "drama")
    pm.add_character("demo", "阿离", "少女")
    pm.add_project_scene("demo", "屋檐", "雨夜屋檐")
    pm.add_prop("demo", "信纸", "关键证据")
    pm.add_episode("demo", 1, "第一集", "scripts/episode_1.json")
    pm.update_project("demo", _complete_asset_sheets)
    return pm


def _complete_asset_sheets(project: dict) -> None:
    project.setdefault("characters", {}).setdefault("阿离", {})["character_sheet"] = "characters/ali.png"
    project.setdefault("scenes", {}).setdefault("屋檐", {})["scene_sheet"] = "scenes/eaves.png"
    project.setdefault("props", {}).setdefault("信纸", {})["prop_sheet"] = "props/letter.png"


def _write_step1(pm: ProjectManager, content: dict) -> None:
    drafts = pm.get_project_path("demo") / "drafts" / "episode_1"
    drafts.mkdir(parents=True, exist_ok=True)
    atomic_write_json(drafts / "step1_normalized_script.json", content)


def _write_step2(pm: ProjectManager) -> None:
    scripts = pm.get_project_path("demo") / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    atomic_write_json(scripts / "episode_1.json", {"title": "第一集", "scenes": []})


def test_get_state_includes_derived_qa_fields(tmp_path: Path) -> None:
    pm = _make_project(tmp_path)
    _write_step1(pm, _drama_step1())
    state = ScriptReviewService(pm).get_state("demo", 1)
    assert state["qa_findings"] == []
    assert state["qa_summary"] == {
        "info_count": 0,
        "warn_count": 0,
        "block_count": 0,
        "gate_status": "clear",
        "top_codes": [],
    }
    assert state["qa_gate_status"] == "clear"


def test_confirm_blocks_deterministic_qa_findings_without_writing_fingerprint(tmp_path: Path) -> None:
    pm = _make_project(tmp_path)
    _write_step1(pm, _drama_step1(props=["信纸", "玉佩"]))
    svc = ScriptReviewService(pm)

    state = svc.get_state("demo", 1)
    assert state["qa_gate_status"] == "blocked"
    assert state["qa_summary"]["block_count"] == 1

    with pytest.raises(ScriptReviewError) as exc:
        svc.confirm("demo", 1)
    assert exc.value.code == "qa_gate_blocked"
    payload = exc.value.payload
    assert payload is not None
    assert payload["code"] == "qa_gate_blocked"
    assert payload["qa_summary"]["block_count"] == 1
    assert "step1_review" not in pm.load_project("demo")["episodes"][0]


def test_confirm_blocks_unsupported_duration_without_writing_fingerprint(tmp_path: Path) -> None:
    pm = _make_project(tmp_path)
    pm.update_project("demo", lambda project: project.update({"_supported_durations": [4, 6, 8]}))
    _write_step1(pm, _drama_step1(duration_seconds=7))
    svc = ScriptReviewService(pm)

    with pytest.raises(ScriptReviewError) as exc:
        svc.confirm("demo", 1)

    assert exc.value.code == "qa_gate_blocked"
    payload = exc.value.payload
    assert payload is not None
    assert payload["qa_summary"]["block_count"] == 1
    assert payload["qa_findings"][0]["code"] == "unsupported_duration"
    assert "step1_review" not in pm.load_project("demo")["episodes"][0]


def test_registered_asset_without_sheet_warns_but_does_not_block_confirm(tmp_path: Path) -> None:
    pm = _make_project(tmp_path)
    pm.update_project("demo", lambda project: project["props"]["信纸"].update({"prop_sheet": ""}))
    _write_step1(pm, _drama_step1())
    svc = ScriptReviewService(pm)

    state = svc.get_state("demo", 1)
    assert state["qa_gate_status"] == "warning"
    assert any(f["code"] == "missing_prop_sheet" for f in state["qa_findings"])
    assert svc.confirm("demo", 1)["status"] == "confirmed"


def test_warn_only_findings_do_not_block_confirm(tmp_path: Path) -> None:
    pm = _make_project(tmp_path)
    content = _drama_step1(scene_description="阿离站在屋檐下。")
    content["scenes"][0]["source_text"] = "阿离站在屋檐下。"
    _write_step1(pm, content)
    svc = ScriptReviewService(pm)
    state = svc.get_state("demo", 1)
    assert state["qa_gate_status"] == "warning"
    assert state["qa_summary"]["warn_count"] >= 1

    confirmed = svc.confirm("demo", 1)
    assert confirmed["status"] == "confirmed"
    assert confirmed["qa_summary"]["block_count"] == 0


def test_grandfathered_confirmed_state_remains_honored_with_qa_fields(tmp_path: Path) -> None:
    pm = _make_project(tmp_path)
    _write_step1(pm, _drama_step1())
    _write_step2(pm)
    state = ScriptReviewService(pm).get_state("demo", 1)
    assert state["status"] == "confirmed"
    assert state["qa_gate_status"] == "clear"
    assert "step1_review" not in pm.load_project("demo")["episodes"][0]


def test_grandfathered_confirmed_state_still_surfaces_blocking_qa(tmp_path: Path) -> None:
    pm = _make_project(tmp_path)
    _write_step1(pm, _drama_step1(props=["信纸", "玉佩"]))
    _write_step2(pm)
    project_path = pm.get_project_path("demo")

    state = ScriptReviewService(pm).get_state("demo", 1)

    assert state["status"] == "confirmed"
    assert state["qa_gate_status"] == "blocked"
    assert state["qa_summary"]["block_count"] == 1
    assert script_review.gate_blocks_step2(project_path, pm.load_project("demo"), 1) is True
    assert "step1_review" not in pm.load_project("demo")["episodes"][0]
