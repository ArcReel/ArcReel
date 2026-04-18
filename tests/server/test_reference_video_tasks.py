from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.reference_video.errors import MissingReferenceError
from server.services.reference_video_tasks import (
    _load_unit_context,
    _resolve_unit_references,
)


def _write_project(tmp_path: Path) -> Path:
    project = {
        "title": "T",
        "content_mode": "reference_video",
        "generation_mode": "reference_video",
        "style": "s",
        "characters": {"张三": {"description": "x", "character_sheet": "characters/张三.png"}},
        "scenes": {"酒馆": {"description": "x", "scene_sheet": "scenes/酒馆.png"}},
        "props": {},
        "episodes": [{"episode": 1, "title": "E1", "script_file": "scripts/episode_1.json"}],
    }
    script = {
        "episode": 1,
        "title": "E1",
        "content_mode": "reference_video",
        "summary": "x",
        "novel": {"title": "t", "chapter": "c"},
        "duration_seconds": 8,
        "video_units": [
            {
                "unit_id": "E1U1",
                "shots": [{"duration": 3, "text": "Shot 1 (3s): @张三 推门"}],
                "references": [
                    {"type": "character", "name": "张三"},
                    {"type": "scene", "name": "酒馆"},
                ],
                "duration_seconds": 3,
                "duration_override": False,
                "transition_to_next": "cut",
                "note": None,
                "generated_assets": {
                    "storyboard_image": None,
                    "storyboard_last_image": None,
                    "grid_id": None,
                    "grid_cell_index": None,
                    "video_clip": None,
                    "video_uri": None,
                    "status": "pending",
                },
            },
        ],
    }
    proj_dir = tmp_path / "demo"
    proj_dir.mkdir()
    (proj_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    (proj_dir / "scripts").mkdir()
    (proj_dir / "scripts" / "episode_1.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    (proj_dir / "characters").mkdir()
    (proj_dir / "characters" / "张三.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (proj_dir / "scenes").mkdir()
    (proj_dir / "scenes" / "酒馆.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return proj_dir


def test_load_unit_context_returns_project_and_unit(tmp_path: Path):
    proj_dir = _write_project(tmp_path)
    project, script, unit = _load_unit_context(
        project_path=proj_dir,
        script_file="scripts/episode_1.json",
        unit_id="E1U1",
    )
    assert project["title"] == "T"
    assert script["episode"] == 1
    assert unit["unit_id"] == "E1U1"


def test_load_unit_context_unknown_unit_raises(tmp_path: Path):
    proj_dir = _write_project(tmp_path)
    with pytest.raises(ValueError, match="unit not found"):
        _load_unit_context(
            project_path=proj_dir,
            script_file="scripts/episode_1.json",
            unit_id="E9U9",
        )


def test_resolve_unit_references_maps_sheets(tmp_path: Path):
    proj_dir = _write_project(tmp_path)
    project, _, unit = _load_unit_context(
        project_path=proj_dir,
        script_file="scripts/episode_1.json",
        unit_id="E1U1",
    )
    resolved = _resolve_unit_references(project, proj_dir, unit["references"])
    assert [p.name for p in resolved] == ["张三.png", "酒馆.png"]


def test_resolve_unit_references_missing_sheet_raises(tmp_path: Path):
    proj_dir = _write_project(tmp_path)
    project, _, unit = _load_unit_context(
        project_path=proj_dir,
        script_file="scripts/episode_1.json",
        unit_id="E1U1",
    )
    # 删掉 character sheet，模拟未生成的情况
    (proj_dir / "characters" / "张三.png").unlink()
    with pytest.raises(MissingReferenceError) as excinfo:
        _resolve_unit_references(project, proj_dir, unit["references"])
    assert ("character", "张三") in excinfo.value.missing


def test_resolve_unit_references_unknown_name_raises(tmp_path: Path):
    proj_dir = _write_project(tmp_path)
    project, _, _ = _load_unit_context(
        project_path=proj_dir,
        script_file="scripts/episode_1.json",
        unit_id="E1U1",
    )
    bad_refs = [{"type": "prop", "name": "不存在的道具"}]
    with pytest.raises(MissingReferenceError) as excinfo:
        _resolve_unit_references(project, proj_dir, bad_refs)
    assert ("prop", "不存在的道具") in excinfo.value.missing


from server.services.reference_video_tasks import (  # noqa: E402
    _apply_provider_constraints,
    _compress_references_to_tempfiles,
    _render_unit_prompt,
)


def _make_png_bytes() -> bytes:
    import io

    from PIL import Image

    img = Image.new("RGB", (3000, 2000), color=(200, 100, 50))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_compress_references_returns_temp_paths(tmp_path: Path):
    src = tmp_path / "big.png"
    src.write_bytes(_make_png_bytes())
    temps = _compress_references_to_tempfiles([src, src])
    try:
        assert len(temps) == 2
        for p in temps:
            assert p.exists()
            assert p.stat().st_size > 0
    finally:
        for p in temps:
            p.unlink(missing_ok=True)


def test_compress_references_empty_input(tmp_path: Path):
    assert _compress_references_to_tempfiles([]) == []


def test_render_unit_prompt_replaces_mentions_in_order():
    unit = {
        "shots": [
            {"duration": 3, "text": "Shot 1 (3s): @张三 推门"},
            {"duration": 5, "text": "Shot 2 (5s): 对面的 @张三 抬眼，背景是 @酒馆"},
        ],
        "references": [
            {"type": "character", "name": "张三"},
            {"type": "scene", "name": "酒馆"},
        ],
    }
    rendered = _render_unit_prompt(unit)
    assert "[图1]" in rendered
    assert "[图2]" in rendered
    assert "@张三" not in rendered
    # Shot header 保留
    assert "Shot 1 (3s):" in rendered
    assert "Shot 2 (5s):" in rendered


def test_apply_provider_constraints_veo_clamps_duration_and_refs():
    refs = [Path(f"/tmp/ref{i}.png") for i in range(5)]
    new_refs, new_duration, warnings = _apply_provider_constraints(
        provider="gemini",
        model="veo-3.1-generate-preview",
        references=refs,
        duration_seconds=12,
    )
    assert len(new_refs) == 3
    assert new_duration == 8
    assert any("ref_duration_exceeded" in w["key"] for w in warnings)
    assert any("ref_too_many_images" in w["key"] for w in warnings)


def test_apply_provider_constraints_sora_single_ref():
    refs = [Path(f"/tmp/ref{i}.png") for i in range(3)]
    new_refs, _, warnings = _apply_provider_constraints(
        provider="openai",
        model="sora-2",
        references=refs,
        duration_seconds=8,
    )
    assert len(new_refs) == 1
    assert any("ref_sora_single_ref" in w["key"] for w in warnings)


def test_apply_provider_constraints_ark_keeps_nine():
    refs = [Path(f"/tmp/ref{i}.png") for i in range(9)]
    new_refs, new_duration, warnings = _apply_provider_constraints(
        provider="ark",
        model="doubao-seedance-2-0-260128",
        references=refs,
        duration_seconds=12,
    )
    assert len(new_refs) == 9
    assert new_duration == 12
    assert warnings == []
