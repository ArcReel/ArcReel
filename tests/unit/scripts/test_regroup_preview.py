from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from scripts.regroup.preview import (
    ModuleMap,
    build_import_graph,
    contract_violations,
    discover_modules,
    find_cycles,
    pick_test_package,
    rename_contract,
    unwaived_server_edges,
)


def _mapping(
    moves: dict[str, str],
    *,
    packages: dict[str, str] | None = None,
    accepted: dict[str, str] | None = None,
    accepted_server: dict[str, str] | None = None,
):
    return ModuleMap(
        packages=packages or {},
        moves=moves,
        keep=[],
        rulings={},
        accepted_edges=accepted or {},
        route_neutral_sources=[],
        route_specific="lib.route",
        accepted_server_edges=accepted_server or {},
    )


def test_rename_uses_longest_prefix_and_carries_submodules() -> None:
    mapping = _mapping({"lib.pricing": "lib.billing.pricing", "lib.pricing.lookup": "lib.billing.lookup"})

    assert mapping.rename("lib.pricing.types") == "lib.billing.pricing.types"
    assert mapping.rename("lib.pricing.lookup.inner") == "lib.billing.lookup.inner"
    assert mapping.rename("lib.pricing_extra") == "lib.pricing_extra"


def test_import_graph_resolves_relative_submodule_and_deferred_imports(tmp_path: Path) -> None:
    for rel, body in {
        "lib/__init__.py": "",
        "lib/a.py": "from .pkg import b\n\ndef f():\n    import lib.c\n",
        "lib/pkg/__init__.py": "",
        "lib/pkg/b.py": "from lib.c import thing\n",
        "lib/c.py": "thing = 1\n",
        "server/__init__.py": "",
    }.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    graph = build_import_graph(discover_modules(tmp_path))

    assert graph["lib.a"] == {"lib.pkg.b", "lib.c"}
    assert graph["lib.pkg.b"] == {"lib.c"}


def test_import_graph_rejects_relative_import_beyond_top_level(tmp_path: Path) -> None:
    for rel, body in {
        "lib/__init__.py": "",
        "lib/pkg/__init__.py": "",
        "lib/pkg/b.py": "from ... import c\n",
    }.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    with pytest.raises(ValueError, match=r"lib\.pkg\.b"):
        build_import_graph(discover_modules(tmp_path))


def test_package_cycle_needs_registered_waiver() -> None:
    graph = {
        "lib.x.one": {"lib.y.two"},
        "lib.y.two": {"lib.y.three"},
        "lib.y.three": {"lib.x.one"},
    }

    (open_cycle,), stale = find_cycles(graph, _mapping({}))
    assert open_cycle.component == ["lib.x", "lib.y"]
    assert len(open_cycle.unwaived) == 1
    assert stale == []

    (closed,), stale = find_cycles(graph, _mapping({}, accepted={"lib.y -> lib.x": "reason"}))
    assert closed.unwaived == []
    assert closed.breakers == [("lib.y", "lib.x")]

    _, stale = find_cycles(graph, _mapping({}, accepted={"lib.x -> lib.z": "reason"}))
    assert stale == ["lib.x -> lib.z"]

    one_way = {"lib.x.one": set(), "lib.y.two": {"lib.x.one"}}
    findings, stale = find_cycles(one_way, _mapping({}, accepted={"lib.y -> lib.x": "reason"}))
    assert findings == []
    assert stale == ["lib.y -> lib.x"]


def test_core_to_server_edge_needs_registered_waiver() -> None:
    graph = {"lib.worker": {"server.tasks", "lib.util"}, "lib.util": set(), "server.tasks": {"lib.util"}}

    assert unwaived_server_edges(graph, _mapping({})) == ([("lib.worker", "server.tasks")], [])
    assert unwaived_server_edges(graph, _mapping({}, accepted_server={"lib.worker -> server.tasks": "reason"})) == (
        [],
        [],
    )
    assert unwaived_server_edges(graph, _mapping({}, accepted_server={"lib.util -> server.tasks": "reason"})) == (
        [("lib.worker", "server.tasks")],
        ["lib.util -> server.tasks"],
    )


def test_test_placement_tie_prefers_package_sharing_the_file_name_prefix() -> None:
    mapping = _mapping(
        {
            "lib.generation_result": "lib.generation.generation_result",
            "lib.workflow_state": "lib.workflow.workflow_state",
            "lib.asset_types": "lib.project.asset_types",
        }
    )
    tied = Counter({"lib.generation_result": 2, "lib.workflow_state": 2})

    assert pick_test_package(mapping, "test_workflow_action_types", tied) == (
        "lib.workflow",
        "提及 2 次（并列取文件名前缀匹配）",
    )
    assert pick_test_package(mapping, "test_action_types", tied) == ("lib.generation", "提及 2 次（并列取字典序）")
    assert pick_test_package(mapping, "test_workflow_action_types", Counter({"lib.asset_types": 3})) == (
        "lib.project",
        "提及 3 次",
    )


def test_renamed_layers_contract_flags_module_moved_into_lower_layer() -> None:
    contract = {
        "type": "layers",
        "containers": ["lib"],
        "layers": ["high", "low"],
        "ignore_imports": [],
    }
    old_graph = {"lib.high.a": set(), "lib.low.b": set(), "lib.helper": {"lib.high.a"}}
    mapping = _mapping({"lib.helper": "lib.low.helper"})
    new_graph = {"lib.high.a": set(), "lib.low.b": set(), "lib.low.helper": {"lib.high.a"}}

    renamed, _ = rename_contract(contract, mapping)

    assert contract_violations(old_graph, contract) == []
    assert contract_violations(new_graph, renamed) == ["lib.low → lib.high"]


def test_forbidden_contract_honours_direct_only_flag() -> None:
    graph = {"lib.src": {"lib.mid"}, "lib.mid": {"lib.bad"}, "lib.bad": set()}
    contract = {"type": "forbidden", "source_modules": ["lib.src"], "forbidden_modules": ["lib.bad"]}

    assert contract_violations(graph, contract) == ["lib.src → lib.bad"]
    assert contract_violations(graph, {**contract, "allow_indirect_imports": True}) == []
