"""v5→v6 项目资产共享名称空间迁移。"""

from __future__ import annotations

import json
import os
import unicodedata
from pathlib import Path

import pytest

from lib.project_migrations.v5_to_v6_asset_namespace import migrate_v5_to_v6

pytestmark = pytest.mark.integration


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
        {
            "units": [
                {"text": "@[Hero]", "references": [{"type": "product", "name": "Hero"}]},
                {
                    "references": [{"type": "scene", "name": "Hero"}],
                    "shots": [{"text": "@[Hero] in view"}],
                },
            ]
        },
    )
    for name in ("step1_normalized_script.md", "step1_segments.md", "step1_reference_units.md"):
        path = project_dir / "drafts" / "episode_1" / name
        path.write_text("镜头1：@[ Trim ] 入场", encoding="utf-8")
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
    assert draft["units"][1]["references"] == [{"type": "scene", "name": "Hero_scene"}]
    assert draft["units"][1]["shots"][0]["text"] == "@[Hero_scene] in view"
    for name in ("step1_normalized_script.md", "step1_segments.md", "step1_reference_units.md"):
        assert (project_dir / "drafts" / "episode_1" / name).read_text(encoding="utf-8") == "镜头1：@[Trim] 入场"

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


def test_migration_reserves_retained_history_names(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    _write_json(
        project_dir / "project.json",
        {
            "schema_version": 5,
            "characters": {"Hero": _asset("character", "character_sheet")},
            "scenes": {"Hero": _asset("scene", "scene_sheet")},
            "props": {},
            "products": {},
        },
    )
    _write_json(
        project_dir / "versions" / "versions.json",
        {
            "scenes": {
                "Hero": {
                    "current_version": 1,
                    "versions": [{"version": 1, "file": "versions/scenes/Hero_v1_active.png"}],
                },
                "Hero_scene": {
                    "current_version": 1,
                    "versions": [{"version": 1, "file": "versions/scenes/Hero_scene_v1_retained.png"}],
                },
            }
        },
    )
    for name in ("Hero_v1_active.png", "Hero_scene_v1_retained.png"):
        path = project_dir / "versions" / "scenes" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())

    migrate_v5_to_v6(project_dir)

    project = _read_json(project_dir / "project.json")
    assert list(project["scenes"]) == ["Hero_scene_2"]
    versions = _read_json(project_dir / "versions" / "versions.json")["scenes"]
    assert set(versions) == {"Hero_scene", "Hero_scene_2"}
    assert versions["Hero_scene_2"]["versions"][0]["file"] == "versions/scenes/Hero_scene_2_v1_active.png"
    assert (project_dir / "versions" / "scenes" / "Hero_scene_2_v1_active.png").is_file()
    assert (project_dir / "versions" / "scenes" / "Hero_scene_v1_retained.png").is_file()


def test_migration_skips_suffix_occupied_by_orphan_media(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    _write_json(
        project_dir / "project.json",
        {
            "schema_version": 5,
            "characters": {"Hero": _asset("character", "character_sheet")},
            "scenes": {"Hero": _asset("scene", "scene_sheet", "scenes/Hero.png")},
            "props": {},
            "products": {},
        },
    )
    scenes = project_dir / "scenes"
    scenes.mkdir()
    (scenes / "Hero.png").write_bytes(b"active")
    (scenes / "Hero_scene.png").write_bytes(b"orphan")

    migrate_v5_to_v6(project_dir)

    project = _read_json(project_dir / "project.json")
    assert list(project["scenes"]) == ["Hero_scene_2"]
    assert project["scenes"]["Hero_scene_2"]["scene_sheet"] == "scenes/Hero_scene_2.png"
    assert (scenes / "Hero_scene_2.png").read_bytes() == b"active"
    assert (scenes / "Hero_scene.png").read_bytes() == b"orphan"


@pytest.mark.parametrize("path_kind", ["absolute", "traversal"])
def test_migration_rejects_version_snapshot_paths_outside_project(tmp_path: Path, path_kind: str) -> None:
    project_dir = tmp_path / "demo"
    external = tmp_path / "outside" / "Hero_v1.png"
    external.parent.mkdir()
    external.write_bytes(b"external-version")
    declared_path = str(external) if path_kind == "absolute" else "../outside/Hero_v1.png"
    _write_json(
        project_dir / "project.json",
        {
            "schema_version": 5,
            "characters": {"Hero": _asset("character", "character_sheet")},
            "scenes": {"Hero": _asset("scene", "scene_sheet")},
            "props": {},
            "products": {},
        },
    )
    _write_json(
        project_dir / "versions" / "versions.json",
        {
            "scenes": {
                "Hero": {
                    "current_version": 1,
                    "versions": [{"version": 1, "file": declared_path}],
                }
            }
        },
    )
    original_project = (project_dir / "project.json").read_bytes()
    original_versions = (project_dir / "versions" / "versions.json").read_bytes()

    with pytest.raises(ValueError, match="版本快照路径"):
        migrate_v5_to_v6(project_dir)

    assert external.read_bytes() == b"external-version"
    assert (project_dir / "project.json").read_bytes() == original_project
    assert (project_dir / "versions" / "versions.json").read_bytes() == original_versions
    assert not list(tmp_path.glob(".demo.v6-*"))


def test_migration_uses_declared_owner_for_ambiguous_product_reference(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    _write_json(
        project_dir / "project.json",
        {
            "schema_version": 5,
            "characters": {"Hero": _asset("character", "character_sheet")},
            "scenes": {},
            "props": {},
            "products": {
                "Hero": {
                    **_asset("product", "product_sheet"),
                    "reference_images": ["products/refs/Hero_1.jpg"],
                },
                "Hero_1": {
                    **_asset("second product", "product_sheet"),
                    "reference_images": ["products/refs/Hero_1_1.jpg"],
                },
            },
        },
    )
    refs = project_dir / "products" / "refs"
    refs.mkdir(parents=True)
    (refs / "Hero_1.jpg").write_bytes(b"hero")
    (refs / "Hero_1_1.jpg").write_bytes(b"hero-1")

    migrate_v5_to_v6(project_dir)

    project = _read_json(project_dir / "project.json")
    assert project["products"]["Hero_product"]["reference_images"] == ["products/refs/Hero_product_1.jpg"]
    assert project["products"]["Hero_1"]["reference_images"] == ["products/refs/Hero_1_1.jpg"]
    assert (refs / "Hero_product_1.jpg").read_bytes() == b"hero"
    assert (refs / "Hero_1_1.jpg").read_bytes() == b"hero-1"


def test_migration_preserves_distinct_nfd_and_nfc_declared_media(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    nfd_cafe = unicodedata.normalize("NFD", "café")
    nfc_cafe = unicodedata.normalize("NFC", "café")
    _write_json(
        project_dir / "project.json",
        {
            "schema_version": 5,
            "characters": {},
            "scenes": {
                nfd_cafe: _asset("decomposed", "scene_sheet", f"scenes/{nfd_cafe}.png"),
                nfc_cafe: _asset("composed", "scene_sheet", f"scenes/{nfc_cafe}.png"),
            },
            "props": {},
            "products": {},
        },
    )
    scenes = project_dir / "scenes"
    scenes.mkdir()
    (scenes / f"{nfd_cafe}.png").write_bytes(b"nfd")
    (scenes / f"{nfc_cafe}.png").write_bytes(b"nfc")
    if len(list(scenes.iterdir())) != 2:
        pytest.skip("当前文件系统不区分 NFD/NFC 文件名")

    migrate_v5_to_v6(project_dir)

    project = _read_json(project_dir / "project.json")
    assert list(project["scenes"]) == ["café_scene", "café"]
    assert project["scenes"]["café_scene"]["scene_sheet"] == "scenes/café_scene.png"
    assert project["scenes"]["café"]["scene_sheet"] == "scenes/café.png"
    assert (scenes / "café_scene.png").read_bytes() == b"nfd"
    assert (scenes / "café.png").read_bytes() == b"nfc"


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


def test_directory_swap_failure_restores_original_tree(tmp_path: Path, monkeypatch) -> None:
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
    real_replace = os.replace
    failed = False

    def fail_install(source: str | Path, destination: str | Path) -> None:
        nonlocal failed
        source_path = Path(source)
        destination_path = Path(destination)
        if not failed and destination_path == project_dir and "rollback" not in source_path.name:
            failed = True
            raise OSError("injected staging install failure")
        real_replace(source, destination)

    monkeypatch.setattr("lib.project_migrations.v5_to_v6_asset_namespace.os.replace", fail_install)

    with pytest.raises(OSError, match="injected staging install failure"):
        migrate_v5_to_v6(project_dir)

    assert failed is True
    assert (project_dir / "project.json").read_bytes() == original
    assert not list(tmp_path.glob(".demo.v6-*"))


def test_migration_preserves_broken_symlinks(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    _write_json(
        project_dir / "project.json",
        {"schema_version": 5, "characters": {}, "scenes": {}, "props": {}, "products": {}},
    )
    link = project_dir / "CLAUDE.md"
    try:
        link.symlink_to("missing-runtime-profile.md")
    except OSError as exc:
        pytest.skip(f"当前平台不允许创建符号链接: {exc}")

    migrate_v5_to_v6(project_dir)

    assert link.is_symlink()
    assert os.readlink(link) == "missing-runtime-profile.md"


def test_migration_rejects_symlinked_asset_directory_without_touching_target(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    _write_json(
        project_dir / "project.json",
        {
            "schema_version": 5,
            "characters": {"Hero": _asset("character", "character_sheet")},
            "scenes": {"Hero": _asset("scene", "scene_sheet", "scenes/Hero.png")},
            "props": {},
            "products": {},
        },
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    external_sheet = outside / "Hero.png"
    external_sheet.write_bytes(b"external-scene")
    scenes_link = project_dir / "scenes"
    try:
        scenes_link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"当前平台不允许创建目录符号链接: {exc}")
    original_project = (project_dir / "project.json").read_bytes()

    with pytest.raises(ValueError, match="迁移写入路径不得为符号链接"):
        migrate_v5_to_v6(project_dir)

    assert external_sheet.read_bytes() == b"external-scene"
    assert not (outside / "Hero_scene.png").exists()
    assert scenes_link.is_symlink()
    assert (project_dir / "project.json").read_bytes() == original_project
    assert not list(tmp_path.glob(".demo.v6-*"))


@pytest.mark.parametrize("write_root", ["scripts", "drafts", "versions"])
def test_migration_rejects_symlinked_non_asset_write_roots(tmp_path: Path, write_root: str) -> None:
    project_dir = tmp_path / "demo"
    _write_json(
        project_dir / "project.json",
        {
            "schema_version": 5,
            "characters": {"Hero": _asset("character", "character_sheet")},
            "scenes": {"Hero": _asset("scene", "scene_sheet")},
            "props": {},
            "products": {},
        },
    )
    outside = tmp_path / f"outside-{write_root}"
    outside.mkdir()
    marker = outside / "marker.txt"
    marker.write_text("external", encoding="utf-8")
    link = project_dir / write_root
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"当前平台不允许创建目录符号链接: {exc}")
    original_project = (project_dir / "project.json").read_bytes()

    with pytest.raises(ValueError, match="迁移写入路径不得为符号链接"):
        migrate_v5_to_v6(project_dir)

    assert marker.read_text(encoding="utf-8") == "external"
    assert link.is_symlink()
    assert (project_dir / "project.json").read_bytes() == original_project
    assert not list(tmp_path.glob(".demo.v6-*"))


def test_migration_rejects_symlinked_draft_write_target(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    _write_json(
        project_dir / "project.json",
        {
            "schema_version": 5,
            "characters": {" Trim ": _asset("character", "character_sheet")},
            "scenes": {},
            "props": {},
            "products": {},
        },
    )
    outside = tmp_path / "outside-draft.md"
    outside.write_text("镜头1：@[ Trim ] 入场", encoding="utf-8")
    draft = project_dir / "drafts" / "episode_1" / "step1_normalized_script.md"
    draft.parent.mkdir(parents=True)
    try:
        draft.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"当前平台不允许创建符号链接: {exc}")
    original_project = (project_dir / "project.json").read_bytes()

    with pytest.raises(ValueError, match="迁移写入路径不得为符号链接"):
        migrate_v5_to_v6(project_dir)

    assert outside.read_text(encoding="utf-8") == "镜头1：@[ Trim ] 入场"
    assert draft.is_symlink()
    assert (project_dir / "project.json").read_bytes() == original_project
    assert not list(tmp_path.glob(".demo.v6-*"))
