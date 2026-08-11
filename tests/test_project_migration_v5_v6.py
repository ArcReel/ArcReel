"""v5→v6 项目资产共享名称空间迁移。"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import pytest

from lib.project_migrations.v5_to_v6_asset_namespace import migrate_v5_to_v6

pytestmark = pytest.mark.unit


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _asset(description: str, sheet_field: str, sheet: str = "") -> dict:
    return {"description": description, sheet_field: sheet}


def test_migration_assigns_stable_safe_names_and_cascades_everywhere(tmp_path: Path, caplog) -> None:
    project_dir = tmp_path / "demo"
    nfd_cafe = unicodedata.normalize("NFD", "café")
    _write_json(
        project_dir / "project.json",
        {
            "schema_version": 5,
            "characters": {
                "Hero": _asset("character", "character_sheet", "characters/Hero.png"),
                " Trim ": {
                    "description": "trimmed character",
                    "character_sheet": "characters/ Trim .png",
                    "reference_image": "characters/refs/ Trim .jpg",
                    "reference_audio": "characters/refs_audio/ Trim .wav",
                },
            },
            "scenes": {
                "Hero": _asset("scene", "scene_sheet", "scenes/Hero.png"),
                nfd_cafe: _asset("first cafe", "scene_sheet", f"scenes/{nfd_cafe}.png"),
                "café": _asset("second cafe", "scene_sheet", "scenes/café.jpg"),
            },
            "props": {
                "Hero": _asset("conflicting prop", "prop_sheet", "props/Hero.png"),
                "Hero_prop": _asset("reserved suffix", "prop_sheet", "props/Hero_prop.png"),
            },
            "products": {
                "Hero": {
                    **_asset("product", "product_sheet", "products/Hero.png"),
                    "reference_images": ["products/refs/Hero_1.jpg"],
                }
            },
        },
    )
    script = {
        "scenes": [
            {
                "characters_in_scene": ["Hero"],
                "scenes": ["Hero", nfd_cafe],
                "props": ["Hero"],
                "utterances": [{"kind": "dialogue", "speaker": "Hero", "text": "line"}],
            }
        ],
        "shots": [
            {
                "text": "@[Hero] beside @[café]",
                "products_in_shot": ["Hero"],
                "references": [
                    {"type": "scene", "name": "Hero"},
                    {"type": "product", "name": "Hero"},
                ],
            }
        ],
    }
    _write_json(project_dir / "scripts" / "episode_1.json", script)
    _write_json(
        project_dir / "drafts" / "episode_1" / "quarantine.json",
        {"units": [{"text": "@[Hero]", "references": [{"type": "product", "name": "Hero"}]}]},
    )
    for relative in (
        "characters/Hero.png",
        "characters/ Trim .png",
        "characters/refs/ Trim .jpg",
        "characters/refs_audio/ Trim .wav",
        "scenes/Hero.png",
        f"scenes/{nfd_cafe}.png",
        "scenes/café.jpg",
        "props/Hero.png",
        "props/Hero_prop.png",
        "products/Hero.png",
        "products/refs/Hero_1.jpg",
    ):
        path = project_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(relative.encode())
    _write_json(
        project_dir / "versions" / "versions.json",
        {
            "scenes": {
                "Hero": {
                    "current_version": 1,
                    "versions": [{"version": 1, "file": "versions/scenes/Hero_v1_20260101.png"}],
                }
            }
        },
    )
    version_file = project_dir / "versions" / "scenes" / "Hero_v1_20260101.png"
    version_file.parent.mkdir(parents=True, exist_ok=True)
    version_file.write_bytes(b"version")

    migrate_v5_to_v6(project_dir)

    project = _read_json(project_dir / "project.json")
    assert project["schema_version"] == 6
    assert list(project["characters"]) == ["Hero", "Trim"]
    assert list(project["scenes"]) == ["Hero_scene", "café_scene", "café"]
    assert list(project["props"]) == ["Hero_prop_2", "Hero_prop"]
    assert list(project["products"]) == ["Hero_product"]
    assert project["scenes"]["Hero_scene"]["scene_sheet"] == "scenes/Hero_scene.png"
    assert project["characters"]["Trim"]["reference_image"] == "characters/refs/Trim.jpg"
    assert project["characters"]["Trim"]["reference_audio"] == "characters/refs_audio/Trim.wav"
    assert project["products"]["Hero_product"]["reference_images"] == ["products/refs/Hero_product_1.jpg"]
    assert (project_dir / "scenes" / "Hero_scene.png").is_file()
    assert not (project_dir / "scenes" / "Hero.png").exists()
    assert (project_dir / "characters" / "refs" / "Trim.jpg").is_file()
    assert (project_dir / "characters" / "refs_audio" / "Trim.wav").is_file()
    assert (project_dir / "products" / "refs" / "Hero_product_1.jpg").is_file()

    migrated_script = _read_json(project_dir / "scripts" / "episode_1.json")
    scene = migrated_script["scenes"][0]
    assert scene["characters_in_scene"] == ["Hero"]
    assert scene["scenes"] == ["Hero_scene", "café"]
    assert scene["props"] == ["Hero_prop_2"]
    assert scene["utterances"][0]["speaker"] == "Hero"
    shot = migrated_script["shots"][0]
    assert shot["products_in_shot"] == ["Hero_product"]
    assert shot["references"] == [
        {"type": "scene", "name": "Hero_scene"},
        {"type": "product", "name": "Hero_product"},
    ]
    # 无类型 mention 按稳定优先级归 character，不被较低优先级资产抢走。
    assert shot["text"] == "@[Hero] beside @[café]"
    draft = _read_json(project_dir / "drafts" / "episode_1" / "quarantine.json")
    assert draft["units"][0]["references"] == [{"type": "product", "name": "Hero_product"}]
    # 同容器唯一 typed reference 可判定归属，mention 随 product 级联。
    assert draft["units"][0]["text"] == "@[Hero_product]"

    versions = _read_json(project_dir / "versions" / "versions.json")
    assert list(versions["scenes"]) == ["Hero_scene"]
    assert versions["scenes"]["Hero_scene"]["versions"][0]["file"] == ("versions/scenes/Hero_scene_v1_20260101.png")
    assert (project_dir / "versions" / "scenes" / "Hero_scene_v1_20260101.png").is_file()
    assert "无类型" in caplog.text


def test_migration_is_idempotent(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    _write_json(
        project_dir / "project.json",
        {"schema_version": 5, "characters": {}, "scenes": {}, "props": {}, "products": {}},
    )
    migrate_v5_to_v6(project_dir)
    first = (project_dir / "project.json").read_bytes()
    migrate_v5_to_v6(project_dir)
    assert (project_dir / "project.json").read_bytes() == first


def test_migration_failure_leaves_original_tree_untouched(tmp_path: Path, monkeypatch) -> None:
    project_dir = tmp_path / "demo"
    _write_json(
        project_dir / "project.json",
        {
            "schema_version": 5,
            "characters": {"Same": _asset("character", "character_sheet")},
            "scenes": {"Same": _asset("scene", "scene_sheet")},
            "props": {},
            "products": {},
        },
    )
    original = (project_dir / "project.json").read_bytes()

    def fail(_staged: Path) -> None:
        raise RuntimeError("injected failure")

    monkeypatch.setattr(
        "lib.project_migrations.v5_to_v6_asset_namespace._migrate_staged_tree",
        fail,
    )
    with pytest.raises(RuntimeError, match="injected failure"):
        migrate_v5_to_v6(project_dir)

    assert (project_dir / "project.json").read_bytes() == original
    assert not list(tmp_path.glob(".demo.v6-*"))
