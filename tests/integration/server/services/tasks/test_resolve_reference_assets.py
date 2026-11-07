"""Tests for resolve_reference_assets."""

from __future__ import annotations

import json
from pathlib import Path

from lib.script.reference_video.request_projection import clamp_reference_assets, resolve_reference_assets
from tests.integration.server.services.tasks.reference_video_tasks_support import (
    load_project_and_unit,
    register_asset_sheet,
    write_project,
)


def _resolved_names(project: dict, proj_dir: Path, text: str) -> list[str]:
    return [asset.path.name for asset in resolve_reference_assets(project, proj_dir, {"text": text})]


def test_resolve_reference_assets_maps_sheets(tmp_path: Path):
    proj_dir = write_project(tmp_path)
    project, unit = load_project_and_unit(proj_dir, "E1U1")
    assert _resolved_names(project, proj_dir, unit["text"]) == ["张三.png", "酒馆.png"]


def test_product_reference_uses_its_sheet_without_type_priority(tmp_path: Path):
    """Products follow ordinary asset rules: if sheets exist, use only sheets and never move products before body mention order."""
    proj_dir = write_project(tmp_path)
    project, _unit = load_project_and_unit(proj_dir, "E1U1")
    products_dir = proj_dir / "products"
    refs_dir = products_dir / "refs"
    refs_dir.mkdir(parents=True)
    image = (proj_dir / "characters" / "张三.png").read_bytes()
    for filename in ("甲-sheet.png", "甲-original.png"):
        target_dir = refs_dir if "original" in filename else products_dir
        (target_dir / filename).write_bytes(image)
    project["products"] = {
        "商品甲": {
            "description": "x",
            "product_sheet": "products/甲-sheet.png",
            "reference_images": ["products/refs/甲-original.png"],
        },
    }
    (proj_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    register_asset_sheet(proj_dir, "product", "商品甲", "products/甲-sheet.png")

    assert _resolved_names(project, proj_dir, "@[张三] 拿起 @[商品甲]") == ["张三.png", "甲-sheet.png"]


def test_clamp_keeps_the_first_mentions_without_type_priority(tmp_path: Path):
    """Over-limit truncation follows body mention order; product sheets never outrank earlier-mentioned asset types."""
    proj_dir = write_project(tmp_path)
    project, _unit = load_project_and_unit(proj_dir, "E1U1")
    products_dir = proj_dir / "products"
    products_dir.mkdir(exist_ok=True)
    image = (proj_dir / "characters" / "张三.png").read_bytes()
    (products_dir / "甲-sheet.png").write_bytes(image)
    project["products"] = {"商品甲": {"description": "x", "product_sheet": "products/甲-sheet.png"}}
    (proj_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    register_asset_sheet(proj_dir, "product", "商品甲", "products/甲-sheet.png")

    entries = list(resolve_reference_assets(project, proj_dir, {"text": "@[张三] 在 @[酒馆] 拿起 @[商品甲]"}))
    clamped = clamp_reference_assets(entries, 2)

    assert [entry.path.name for entry in clamped] == ["张三.png", "酒馆.png"]


def test_product_reference_with_original_only_is_executable(tmp_path: Path):
    """Products lacking standard sheets still use photographed originals as fidelity anchors rather than falsely missing references."""
    proj_dir = write_project(tmp_path)
    project, _unit = load_project_and_unit(proj_dir, "E1U1")
    refs_dir = proj_dir / "products" / "refs"
    refs_dir.mkdir(parents=True)
    (refs_dir / "original.png").write_bytes((proj_dir / "characters" / "张三.png").read_bytes())
    project["products"] = {
        "商品甲": {
            "product_sheet": "",
            "reference_images": ["products/refs/original.png"],
        }
    }

    entries = resolve_reference_assets(project, proj_dir, {"text": "@[商品甲] 出现"})

    assert [entry.path.name for entry in entries] == ["original.png"]
    assert entries[0].kind == "original"


def test_character_with_original_only_resolves_to_nothing(tmp_path: Path):
    """Character originals only generate sheets; never substitute them into video requests (ADR 0073)."""
    proj_dir = write_project(tmp_path)
    project, _ = load_project_and_unit(proj_dir, "E1U1")
    refs_dir = proj_dir / "characters" / "refs"
    refs_dir.mkdir(parents=True)
    (refs_dir / "李四.png").write_bytes((proj_dir / "characters" / "张三.png").read_bytes())
    project["characters"]["李四"] = {
        "description": "x",
        "character_sheet": "",
        "reference_image": "characters/refs/李四.png",
    }
    (proj_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

    assert _resolved_names(project, proj_dir, "@[李四] 推门") == []


def test_resolve_reference_assets_ignores_an_unregistered_mention(tmp_path: Path):
    proj_dir = write_project(tmp_path)
    project, _ = load_project_and_unit(proj_dir, "E1U1")

    assert _resolved_names(project, proj_dir, "@[不存在的道具] 掉在地上") == []


def test_resolve_reference_assets_resolves_nfd_registered_name_by_nfc_mention(tmp_path: Path):
    """Assets registered under NFD keys still resolve NFC names normalized by the parser."""
    import unicodedata

    name_nfc = unicodedata.normalize("NFC", "Hiếu")
    name_nfd = unicodedata.normalize("NFD", "Hiếu")
    assert name_nfc != name_nfd

    proj_dir = write_project(tmp_path)
    project, _ = load_project_and_unit(proj_dir, "E1U1")
    project["characters"][name_nfd] = {"description": "x", "character_sheet": "characters/hieu.png"}
    (proj_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    register_asset_sheet(proj_dir, "character", name_nfd, "characters/hieu.png")

    assert _resolved_names(project, proj_dir, f"@[{name_nfc}] 推门") == ["hieu.png"]


def test_resolve_reference_assets_dedupes_a_repeated_mention(tmp_path: Path):
    """Repeated body mentions consume one reference-image slot, never sending duplicate images."""
    import unicodedata

    name_nfc = unicodedata.normalize("NFC", "Hiếu")
    name_nfd = unicodedata.normalize("NFD", "Hiếu")
    assert name_nfc != name_nfd

    proj_dir = write_project(tmp_path)
    project, _ = load_project_and_unit(proj_dir, "E1U1")
    project["characters"][name_nfc] = {"description": "x", "character_sheet": "characters/hieu.png"}
    (proj_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    register_asset_sheet(proj_dir, "character", name_nfc, "characters/hieu.png")

    assert _resolved_names(project, proj_dir, f"@[{name_nfc}] 推门，@[{name_nfd}] 回头") == ["hieu.png"]


def _register_derivative(proj_dir: Path, owner: str, derivative: str) -> dict:
    """Add a registered character derivative with an asset image, persist and register it in manifests."""
    derivatives_dir = proj_dir / "characters" / "derivatives" / owner
    derivatives_dir.mkdir(parents=True, exist_ok=True)
    (derivatives_dir / f"{derivative}.png").write_bytes((proj_dir / "characters" / f"{owner}.png").read_bytes())
    sheet = f"characters/derivatives/{owner}/{derivative}.png"
    project = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    entry = project["characters"][owner]
    entry.setdefault("derivatives", {})[derivative] = {"description": "变化", "character_sheet": sheet}
    (proj_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    register_asset_sheet(proj_dir, "character", f"{owner}/{derivative}", sheet)
    return project


def test_derivative_reference_injects_the_derivative_sheet(tmp_path: Path):
    proj_dir = write_project(tmp_path)
    project = _register_derivative(proj_dir, "张三", "劲装")

    assert _resolved_names(project, proj_dir, "@[张三/劲装] 推门") == ["劲装.png"]


def test_ontology_and_derivative_together_each_inject_one_sheet(tmp_path: Path):
    """Base and derivative together are unrestricted: distinct names/images each inject once in mention order."""
    proj_dir = write_project(tmp_path)
    project = _register_derivative(proj_dir, "张三", "劲装")

    assert _resolved_names(project, proj_dir, "@[张三] 看着 @[张三/劲装]") == ["张三.png", "劲装.png"]


def test_several_derivatives_of_one_character_each_inject_one_sheet(tmp_path: Path):
    proj_dir = write_project(tmp_path)
    _register_derivative(proj_dir, "张三", "劲装")
    project = _register_derivative(proj_dir, "张三", "兽化")

    assert _resolved_names(project, proj_dir, "@[张三/劲装] 与 @[张三/兽化] 对峙") == ["劲装.png", "兽化.png"]


def test_a_derivative_without_a_sheet_produces_no_candidate(tmp_path: Path):
    """Derivatives without images never fall back to base sheets; admission reports `character: 张三/劲装` (ADR 0073)."""
    proj_dir = write_project(tmp_path)
    project, _ = load_project_and_unit(proj_dir, "E1U1")
    project["characters"]["张三"]["derivatives"] = {"劲装": {"description": "变化", "character_sheet": ""}}

    assert _resolved_names(project, proj_dir, "@[张三/劲装] 推门") == []
