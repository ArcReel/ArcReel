from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from lib.asset_inventory import AssetInventoryInvalidRequest, AssetInventoryRevisionConflict, complete_asset_inventory
from lib.db.repositories.asset_alias_repo import AssetAliasRepository
from lib.db.repositories.asset_repo import AssetRepository
from lib.project_manager import ProjectManager
from lib.source_revision import SourceScope, compute_source_revision
from server.agent_runtime.sdk_tools._context import ToolContext
from server.agent_runtime.sdk_tools.asset_inventory import complete_asset_inventory_tool
from server.agent_runtime.sdk_tools.global_assets import list_global_assets_tool


def _make_project(tmp_path: Path) -> tuple[ProjectManager, Path]:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "", "narration")
    project_path = pm.get_project_path("demo")
    (project_path / "source" / "novel.txt").write_text("最初的原文", encoding="utf-8")
    return pm, project_path


@pytest.mark.integration
def test_complete_inventory_accepts_three_empty_buckets_and_persists_scope(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path)
    project = pm.load_project("demo")
    expected = compute_source_revision(project_path, project, SourceScope(kind="all")).revision
    assert expected is not None

    completed = complete_asset_inventory(pm, "demo", SourceScope(kind="all"), expected)

    assert completed.counts == {"characters": 0, "scenes": 0, "props": 0}
    marker = pm.load_project("demo")["workflow"]["asset_inventory"]
    assert marker["scope"] == {"kind": "all", "files": []}
    assert marker["source_revision"] == expected
    assert marker["completed_at"].endswith("+00:00")


@pytest.mark.integration
def test_revision_conflict_does_not_partially_write_inventory_marker(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path)
    project = pm.load_project("demo")
    stale = compute_source_revision(project_path, project, SourceScope(kind="all")).revision
    assert stale is not None
    (project_path / "source" / "novel.txt").write_text("修改后的原文", encoding="utf-8")

    with pytest.raises(AssetInventoryRevisionConflict) as raised:
        complete_asset_inventory(pm, "demo", SourceScope(kind="all"), stale)

    assert raised.value.actual_revision != stale
    assert "workflow" not in pm.load_project("demo")


@pytest.mark.integration
def test_revision_conflict_does_not_partially_write_extracted_assets(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path)
    project = pm.load_project("demo")
    stale = compute_source_revision(project_path, project, SourceScope(kind="all")).revision
    assert stale is not None
    (project_path / "source" / "novel.txt").write_text("修改后的原文", encoding="utf-8")

    with pytest.raises(AssetInventoryRevisionConflict):
        complete_asset_inventory(
            pm,
            "demo",
            SourceScope(kind="all"),
            stale,
            {"characters": {"阿青": {"description": "青衣少女", "voice_style": "清亮"}}},
        )

    saved = pm.load_project("demo")
    assert "阿青" not in saved["characters"]
    assert "workflow" not in saved


@pytest.mark.integration
def test_source_mutation_is_serialized_with_inventory_revision_commit(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path)
    source_path = project_path / "source" / "novel.txt"
    expected = compute_source_revision(project_path, pm.load_project("demo"), SourceScope(kind="all")).revision
    assert expected is not None
    writer_locked = Event()
    allow_write = Event()
    completion_started = Event()

    def _write_source() -> None:
        with pm.locked_source_mutation("demo"):
            writer_locked.set()
            allow_write.wait(timeout=5)
            source_path.write_text("并发修改后的原文", encoding="utf-8")

    def _complete_inventory() -> None:
        completion_started.set()
        complete_asset_inventory(pm, "demo", SourceScope(kind="all"), expected)

    with ThreadPoolExecutor(max_workers=2) as executor:
        writer = executor.submit(_write_source)
        assert writer_locked.wait(timeout=2)
        completion = executor.submit(_complete_inventory)
        assert completion_started.wait(timeout=2)
        assert not completion.done()
        allow_write.set()
        writer.result(timeout=2)
        with pytest.raises(AssetInventoryRevisionConflict):
            completion.result(timeout=2)

    saved = pm.load_project("demo")
    assert "workflow" not in saved


@pytest.mark.integration
def test_extracted_assets_and_marker_commit_together(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path)
    expected = compute_source_revision(project_path, pm.load_project("demo"), SourceScope(kind="all")).revision
    assert expected is not None

    completed = complete_asset_inventory(
        pm,
        "demo",
        SourceScope(kind="all"),
        expected,
        {
            "characters": {"阿青": {"description": "青衣少女", "voice_style": "清亮"}},
            "scenes": {"竹林": {"description": "雨后竹林"}},
            "props": {},
        },
    )

    saved = pm.load_project("demo")
    assert saved["characters"]["阿青"]["voice_style"] == "清亮"
    assert saved["scenes"]["竹林"]["description"] == "雨后竹林"
    assert saved["workflow"]["asset_inventory"]["source_revision"] == expected
    assert completed.counts == {"characters": 1, "scenes": 1, "props": 0}


@pytest.mark.integration
async def test_inventory_tool_records_one_exact_same_type_global_match(
    tmp_path: Path,
    db_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm, project_path = _make_project(tmp_path)
    async with db_factory() as session:
        global_character = await AssetRepository(session).create(
            type="character",
            name="鳄鱼爸爸",
            description="全局角色",
        )
        global_scene = await AssetRepository(session).create(
            type="scene",
            name="鳄鱼爸爸",
            description="同名但不同类型",
        )
        await session.commit()
        character_id = global_character.id
        scene_id = global_scene.id
    monkeypatch.setattr("server.agent_runtime.sdk_tools.asset_inventory.async_session_factory", db_factory)
    expected = compute_source_revision(project_path, pm.load_project("demo"), SourceScope(kind="all")).revision
    assert expected is not None
    tool = complete_asset_inventory_tool(ToolContext(project_name="demo", projects_root=tmp_path / "projects", pm=pm))

    result = await tool.handler(
        {
            "scope": {"kind": "all", "files": []},
            "expected_source_revision": expected,
            "entries": {
                "characters": {
                    "鳄鱼爸爸": {
                        "description": "项目角色",
                        "voice_style": "沉稳",
                        "matched_global_asset_id": "agent-forged-id",
                    }
                },
                "scenes": {},
                "props": {},
            },
        }
    )

    assert "is_error" not in result
    character = pm.load_project("demo")["characters"]["鳄鱼爸爸"]
    assert character["matched_global_asset_id"] == character_id
    assert character["global_asset_id"] == character_id
    assert character["global_asset_image_usage"] == "main"
    assert character["global_asset_voice_source"] == "none"
    assert character["matched_global_asset_id"] != scene_id


@pytest.mark.integration
async def test_inventory_matching_prefers_canonical_name_and_rejects_ambiguous_alias(
    tmp_path: Path,
    db_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm, project_path = _make_project(tmp_path)
    async with db_factory() as session:
        asset_repo = AssetRepository(session)
        alias_repo = AssetAliasRepository(session)
        croco_dad = await asset_repo.create(type="character", name="布爸")
        await alias_repo.create(asset_id=croco_dad.id, alias="Benny Stone", origin="catalog")
        await alias_repo.create(asset_id=croco_dad.id, alias="鳄鱼爸爸", origin="catalog")
        canonical_croco_dad = await asset_repo.create(type="character", name="鳄鱼爸爸")
        first_ambiguous = await asset_repo.create(type="character", name="甲")
        second_ambiguous = await asset_repo.create(type="character", name="乙")
        await alias_repo.create(asset_id=first_ambiguous.id, alias="共同别名", origin="local")
        await alias_repo.create(asset_id=second_ambiguous.id, alias="共同别名", origin="local")
        await session.commit()
        croco_dad_id = croco_dad.id
        canonical_croco_dad_id = canonical_croco_dad.id

    monkeypatch.setattr("server.agent_runtime.sdk_tools.asset_inventory.async_session_factory", db_factory)
    expected = compute_source_revision(project_path, pm.load_project("demo"), SourceScope(kind="all")).revision
    assert expected is not None
    tool = complete_asset_inventory_tool(ToolContext(project_name="demo", projects_root=tmp_path / "projects", pm=pm))

    result = await tool.handler(
        {
            "scope": {"kind": "all", "files": []},
            "expected_source_revision": expected,
            "entries": {
                "characters": {
                    "Benny Stone": {"description": "唯一别名"},
                    "鳄鱼爸爸": {"description": "正式名称优先"},
                    "共同别名": {"description": "歧义别名", "matched_global_asset_id": "forged"},
                },
                "scenes": {},
                "props": {},
            },
        }
    )

    assert "is_error" not in result
    characters = pm.load_project("demo")["characters"]
    assert characters["Benny Stone"]["matched_global_asset_id"] == croco_dad_id
    assert characters["鳄鱼爸爸"]["matched_global_asset_id"] == canonical_croco_dad_id
    assert "matched_global_asset_id" not in characters["共同别名"]


@pytest.mark.integration
async def test_global_asset_context_tool_returns_compact_grouped_assets(
    tmp_path: Path,
    db_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with db_factory() as session:
        asset = await AssetRepository(session).create(
            type="prop",
            name="景泰蓝花瓶",
            description="蓝色铜胎珐琅器",
        )
        await AssetAliasRepository(session).create(asset_id=asset.id, alias="珐琅花瓶", origin="local")
        await session.commit()
    monkeypatch.setattr("server.agent_runtime.sdk_tools.global_assets.async_session_factory", db_factory)
    tool = list_global_assets_tool(ToolContext(project_name="demo", projects_root=tmp_path / "projects"))

    result = await tool.handler({})

    body = json.loads(result["content"][0]["text"])
    assert body["props"][0]["name"] == "景泰蓝花瓶"
    assert body["props"][0]["description"] == "蓝色铜胎珐琅器"
    assert body["props"][0]["aliases"] == ["珐琅花瓶"]
    assert body["props"][0]["id"]
    assert body["props"][0]["image_path"] is None
    assert body["characters"] == []
    assert body["scenes"] == []


@pytest.mark.integration
def test_scoped_completion_keeps_explicit_partial_scope(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path)
    project = pm.load_project("demo")
    scope = SourceScope(kind="files", files=["source/novel.txt"])
    expected = compute_source_revision(project_path, project, scope).revision
    assert expected is not None

    complete_asset_inventory(pm, "demo", scope, expected)

    marker = pm.load_project("demo")["workflow"]["asset_inventory"]
    assert marker["scope"] == {"kind": "files", "files": ["source/novel.txt"]}


@pytest.mark.integration
def test_complete_inventory_rejects_non_string_expected_revision(tmp_path: Path) -> None:
    pm, _project_path = _make_project(tmp_path)

    with pytest.raises(AssetInventoryInvalidRequest):
        complete_asset_inventory(pm, "demo", SourceScope(kind="all"), None)

    with pytest.raises(AssetInventoryInvalidRequest):
        complete_asset_inventory(pm, "demo", SourceScope(kind="all"), "sha256-v1:not-a-digest")


@pytest.mark.integration
async def test_complete_inventory_mcp_returns_machine_readable_result_and_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm, project_path = _make_project(tmp_path)
    ctx = ToolContext(project_name="demo", projects_root=tmp_path / "projects", pm=pm)
    tool = complete_asset_inventory_tool(ctx)
    offloads: list[tuple[object, tuple[object, ...]]] = []

    async def _to_thread(fn, *args):
        offloads.append((fn, args))
        return fn(*args)

    monkeypatch.setattr("server.agent_runtime.sdk_tools.asset_inventory.asyncio.to_thread", _to_thread)
    expected = compute_source_revision(project_path, pm.load_project("demo"), SourceScope(kind="all")).revision
    assert expected is not None

    success = await tool.handler({"scope": {"kind": "all", "files": []}, "expected_source_revision": expected})
    body = json.loads(success["content"][0]["text"])
    assert body == {
        "counts": {"characters": 0, "props": 0, "scenes": 0},
        "scope": {"files": [], "kind": "all"},
        "source_revision": expected,
        "voice_references": [],
    }

    (project_path / "source" / "novel.txt").write_text("又一次变化", encoding="utf-8")
    conflict = await tool.handler({"scope": {"kind": "all", "files": []}, "expected_source_revision": expected})
    conflict_body = json.loads(conflict["content"][0]["text"])
    assert conflict["is_error"] is True
    assert conflict_body["error"] == "source_revision_conflict"
    assert conflict_body["expected_source_revision"] == expected
    assert conflict_body["actual_source_revision"] != expected
    assert len(offloads) == 2


@pytest.mark.integration
async def test_complete_inventory_mcp_distinguishes_invalid_request_from_broken_workflow(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path)
    ctx = ToolContext(project_name="demo", projects_root=tmp_path / "projects", pm=pm)
    tool = complete_asset_inventory_tool(ctx)

    invalid = await tool.handler({"scope": {"kind": "all", "files": []}, "expected_source_revision": "not-a-revision"})
    assert json.loads(invalid["content"][0]["text"])["error"] == "invalid_request"

    expected = compute_source_revision(
        project_path,
        pm.load_project("demo"),
        SourceScope(kind="all"),
    ).revision
    assert expected is not None
    pm.update_project("demo", lambda project: project.update(workflow="broken"))

    unavailable = await tool.handler({"scope": {"kind": "all", "files": []}, "expected_source_revision": expected})
    assert json.loads(unavailable["content"][0]["text"])["error"] == "inventory_unavailable"
