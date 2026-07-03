from lib.short_drama_qa import evaluate_short_drama_qa


def _project() -> dict:
    return {
        "content_mode": "drama",
        "characters": {"阿离": {"character_sheet": "characters/ali.png"}},
        "scenes": {"屋檐": {"scene_sheet": "scenes/eaves.png"}},
        "props": {"信纸": {"prop_sheet": "props/letter.png"}},
    }


def _drama_content(**overrides) -> dict:
    scene = {
        "scene_id": "E1S01",
        "duration_seconds": 8,
        "segment_break": False,
        "characters_in_scene": ["阿离"],
        "scenes": ["屋檐"],
        "props": ["信纸"],
        "scene_description": "突然，阿离在屋檐下拆开信纸，脸色骤变！",
        "utterances": [],
        "source_text": "突然，阿离拆开信纸，发现真相？",
    }
    scene.update(overrides)
    return {"title": "第一集", "scenes": [scene]}


def test_minimal_valid_fixture_has_no_blocks() -> None:
    result = evaluate_short_drama_qa(_project(), _drama_content())
    assert result["qa_summary"]["block_count"] == 0
    assert result["qa_gate_status"] in {"clear", "warning"}


def test_missing_registered_asset_is_block() -> None:
    content = _drama_content(props=["信纸", "玉佩"])
    result = evaluate_short_drama_qa(_project(), content)
    assert result["qa_gate_status"] == "blocked"
    assert result["qa_summary"]["block_count"] == 1
    finding = result["qa_findings"][0]
    assert finding["code"] == "missing_prop_reference"
    assert finding["severity"] == "block"
    assert "玉佩" in finding.get("evidence", "")


def test_registered_asset_without_sheet_is_warn_only() -> None:
    project = _project()
    project["props"]["信纸"]["prop_sheet"] = ""

    result = evaluate_short_drama_qa(project, _drama_content())

    assert result["qa_summary"]["block_count"] == 0
    assert any(f["code"] == "missing_prop_sheet" and f["severity"] == "warn" for f in result["qa_findings"])


def test_unsupported_duration_is_block_when_capability_is_detectable() -> None:
    project = {**_project(), "_supported_durations": [4, 6, 8]}

    result = evaluate_short_drama_qa(project, _drama_content(duration_seconds=7))

    assert result["qa_gate_status"] == "blocked"
    assert any(f["code"] == "unsupported_duration" and f["severity"] == "block" for f in result["qa_findings"])


def test_empty_scene_description_is_block() -> None:
    result = evaluate_short_drama_qa(_project(), _drama_content(scene_description="  "))
    assert any(f["code"] == "empty_scene_description" for f in result["qa_findings"])
    assert result["qa_gate_status"] == "blocked"


def test_creative_quality_warns_without_blocking() -> None:
    content = _drama_content(scene_description="阿离站在屋檐下。", source_text="阿离站在屋檐下。")
    result = evaluate_short_drama_qa(_project(), content)
    assert result["qa_summary"]["block_count"] == 0
    assert result["qa_summary"]["warn_count"] >= 1
    assert result["qa_gate_status"] == "warning"
    assert "weak_opening_hook" in result["qa_summary"]["top_codes"]


def test_narration_uses_step1_content_only() -> None:
    project = {
        "content_mode": "narration",
        "characters": {"裴与": {"character_sheet": "characters/peiyu.png"}},
        "scenes": {},
        "props": {},
    }
    content = {
        "episode": 1,
        "segments": [
            {
                "segment_id": "E1S01",
                "novel_text": "裴与突然回头，真相浮出水面！",
                "duration_seconds": 6,
                "segment_break": True,
                "characters_in_segment": ["裴与"],
                "scenes": [],
                "props": [],
            }
        ],
    }
    result = evaluate_short_drama_qa(project, content)
    assert result["qa_summary"]["block_count"] == 0
    assert result["qa_summary"]["gate_status"] == result["qa_gate_status"]
