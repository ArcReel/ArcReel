from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.mutmut_compare import EXIT_FAILED, EXIT_INVALID, EXIT_OK, compare, load_meta, main, render

_BASELINE = {
    "lib.a.x_f__mutmut_1": 1,
    "lib.a.x_f__mutmut_2": 0,
    "lib.a.x_f__mutmut_3": 36,
    "lib.a.x_f__mutmut_4": 0,
    "lib.a.x_f__mutmut_5": 0,
    "lib.b.x_g__mutmut_1": 1,
}


def _write_meta(root: Path, name: str, codes: dict[str, int | None], hashes: dict[str, str] | None = None) -> None:
    """按真实 .meta 的形状落盘：每个 mutant 对应的函数都有哈希，未指定的用占位值。"""
    root.mkdir(parents=True, exist_ok=True)
    derived = {key.partition("__mutmut_")[0].rpartition(".")[2]: "h" for key in codes}
    payload = {"exit_code_by_key": codes, "hash_by_function_name": {**derived, **(hashes or {})}}
    (root / name).write_text(json.dumps(payload), encoding="utf-8")


def test_all_three_layers_pass_and_incidental_kills_are_only_recorded() -> None:
    current = {**_BASELINE, "lib.a.x_f__mutmut_2": 1, "lib.a.x_f__mutmut_4": 1}

    report = compare(_BASELINE, current, reworked=["lib.a.x_f__mutmut_2"])

    assert report.passed
    assert report.reworked_killed == ["lib.a.x_f__mutmut_2"]
    assert report.baseline_killed_total == 2
    assert report.regressed == []
    assert report.untouched_total == 3
    assert report.untouched_killed == ["lib.a.x_f__mutmut_4"]
    assert report.equivalent_killed == []


def test_reworked_mutant_still_surviving_fails_and_timeouts_go_to_recheck() -> None:
    current = {**_BASELINE, "lib.a.x_f__mutmut_2": 0, "lib.a.x_f__mutmut_4": -24}

    report = compare(_BASELINE, current, reworked=["lib.a.x_f__mutmut_2", "lib.a.x_f__mutmut_4"])

    assert not report.passed
    assert report.reworked_survived == ["lib.a.x_f__mutmut_2"]
    assert report.reworked_recheck == [("lib.a.x_f__mutmut_4", -24)]


def test_baseline_killed_turning_survived_is_regression_but_timeout_is_recheck() -> None:
    current = {**_BASELINE, "lib.a.x_f__mutmut_1": 0, "lib.b.x_g__mutmut_1": 36, "lib.a.x_f__mutmut_2": 1}

    report = compare(_BASELINE, current, reworked=["lib.a.x_f__mutmut_2"])

    assert not report.passed
    assert report.regressed == ["lib.a.x_f__mutmut_1"]
    assert report.baseline_killed_recheck == [("lib.b.x_g__mutmut_1", 36)]


def test_baseline_killed_timeout_alone_blocks_the_verdict_until_rechecked() -> None:
    current = {**_BASELINE, "lib.a.x_f__mutmut_2": 1, "lib.b.x_g__mutmut_1": -24}

    report = compare(_BASELINE, current, reworked=["lib.a.x_f__mutmut_2"])

    assert not report.passed
    assert report.pending_recheck
    assert report.regressed == []
    assert report.baseline_killed_recheck == [("lib.b.x_g__mutmut_1", -24)]
    assert render(report).splitlines()[-1].startswith("验收未完成")


def test_killed_equivalent_mutant_invalidates_the_round() -> None:
    current = {**_BASELINE, "lib.a.x_f__mutmut_2": 1, "lib.a.x_f__mutmut_5": 1}

    report = compare(_BASELINE, current, reworked=["lib.a.x_f__mutmut_2"], equivalent=["lib.a.x_f__mutmut_5"])

    assert not report.passed
    assert report.untouched_killed == ["lib.a.x_f__mutmut_5"]
    assert report.equivalent_killed == ["lib.a.x_f__mutmut_5"]
    assert "整轮作废" in render(report)


@pytest.mark.parametrize(
    ("current", "reworked", "equivalent", "expected_fragment"),
    [
        ({**_BASELINE, "lib.c.x_h__mutmut_1": 1}, ["lib.a.x_f__mutmut_2"], [], "本轮独有 lib.c.x_h__mutmut_1"),
        (dict(_BASELINE), ["lib.zzz__mutmut_9"], [], "不在基线中：lib.zzz__mutmut_9"),
        (dict(_BASELINE), ["lib.a.x_f__mutmut_1"], [], "在基线已是 killed"),
        (dict(_BASELINE), ["lib.a.x_f__mutmut_2"], ["lib.a.x_f__mutmut_2"], "既在改造名单又在等价变异体名单"),
        (dict(_BASELINE), [], [], "改造名单为空"),
    ],
)
def test_inconsistent_inputs_make_the_comparison_invalid(
    current: dict[str, int | None], reworked: list[str], equivalent: list[str], expected_fragment: str
) -> None:
    report = compare(_BASELINE, current, reworked=reworked, equivalent=equivalent)

    assert not report.passed
    assert any(expected_fragment in line for line in report.invalid)
    assert report.reworked_killed == []


def test_changed_function_hash_invalidates_even_when_mutant_names_match() -> None:
    hashes_before = {"lib.a.x_f": "h1", "lib.b.x_g": "h2"}
    hashes_after = {"lib.a.x_f": "h1-changed", "lib.b.x_g": "h2"}

    report = compare(
        _BASELINE,
        {**_BASELINE, "lib.a.x_f__mutmut_2": 1},
        reworked=["lib.a.x_f__mutmut_2"],
        baseline_hashes=hashes_before,
        current_hashes=hashes_after,
    )

    assert not report.passed
    assert report.invalid == [
        "基线与本轮有 1 个函数源码哈希不同：mutant 名可能没变但语义已变，先重建基线",
        "  源码已变 lib.a.x_f",
    ]


def test_mismatched_function_hash_sets_invalidate_the_comparison() -> None:
    report = compare(
        _BASELINE,
        {**_BASELINE, "lib.a.x_f__mutmut_2": 1},
        reworked=["lib.a.x_f__mutmut_2"],
        baseline_hashes={"lib.a.x_f": "h1", "lib.b.x_g": "h2"},
        current_hashes={"lib.a.x_f": "h1"},
    )

    assert not report.passed
    assert report.invalid == [
        "基线与本轮的函数哈希集合不一致（基线独有 1，本轮独有 0）：源码变了或 .meta 不完整，先重建基线",
        "  基线独有哈希 lib.b.x_g",
    ]


def test_load_meta_rejects_meta_without_hash_for_every_mutant(tmp_path: Path) -> None:
    payload = {"exit_code_by_key": {"lib.a.x_f__mutmut_1": 1}}
    (tmp_path / "a.py.meta").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match=r"缺少 exit_code_by_key 或 hash_by_function_name"):
        load_meta(tmp_path)

    payload["hash_by_function_name"] = {"x_other": "h1"}
    (tmp_path / "a.py.meta").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match=r"hash_by_function_name 缺少 mutant 对应的函数：\['x_f'\]"):
        load_meta(tmp_path)


def test_load_meta_merges_meta_files_recursively_and_rejects_duplicates(tmp_path: Path) -> None:
    _write_meta(tmp_path / "lib", "a.py.meta", {"lib.a.x_f__mutmut_1": 1, "lib.a.x_f__mutmut_2": None}, {"x_f": "h1"})
    _write_meta(tmp_path / "server", "b.py.meta", {"server.b.x_g__mutmut_1": 0}, {"x_g": "h2"})

    snapshot = load_meta(tmp_path)

    assert snapshot.exit_codes == {
        "lib.a.x_f__mutmut_1": 1,
        "lib.a.x_f__mutmut_2": None,
        "server.b.x_g__mutmut_1": 0,
    }
    assert snapshot.function_hashes == {"lib.a.x_f": "h1", "server.b.x_g": "h2"}

    _write_meta(tmp_path / "dup", "a.py.meta", {"lib.a.x_f__mutmut_1": 1})
    with pytest.raises(ValueError, match=r"lib\.a\.x_f__mutmut_1 在 .* 下出现多次"):
        load_meta(tmp_path)


def test_load_meta_rejects_meta_file_mixing_modules(tmp_path: Path) -> None:
    _write_meta(tmp_path, "mixed.py.meta", {"lib.a.x_f__mutmut_1": 1, "lib.b.x_g__mutmut_1": 0}, {"x_f": "h1"})

    with pytest.raises(ValueError, match=r"混有多个模块的 mutant：\['lib\.a', 'lib\.b'\]"):
        load_meta(tmp_path)


def _write_round(root: Path, codes: dict[str, int | None], hashes: dict[str, str] | None = None) -> None:
    """按真实 mutmut 的布局落盘：一个源模块一份 .meta。"""
    for module in {key.partition("__mutmut_")[0].rpartition(".")[0] for key in codes}:
        module_codes = {key: code for key, code in codes.items() if key.startswith(f"{module}.")}
        module_hashes = {
            func.rpartition(".")[2]: digest for func, digest in (hashes or {}).items() if func.startswith(f"{module}.")
        }
        _write_meta(root, f"{module}.py.meta", module_codes, module_hashes)


def test_main_exit_codes_follow_verdict(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_round(tmp_path / "baseline", _BASELINE)
    _write_round(tmp_path / "current", {**_BASELINE, "lib.a.x_f__mutmut_2": 1})
    reworked = tmp_path / "reworked.txt"
    reworked.write_text("# 本次改造\nlib.a.x_f__mutmut_2\n\n", encoding="utf-8")
    args = ["--baseline", str(tmp_path / "baseline"), "--current", str(tmp_path / "current"), "--reworked"]

    assert main([*args, str(reworked)]) == EXIT_OK
    assert "验收通过" in capsys.readouterr().out

    reworked.write_text("lib.a.x_f__mutmut_2\nlib.a.x_f__mutmut_4\n", encoding="utf-8")
    assert main([*args, str(reworked)]) == EXIT_FAILED
    assert "改造后仍存活" in capsys.readouterr().out

    reworked.write_text("lib.a.x_f__mutmut_1\n", encoding="utf-8")
    assert main([*args, str(reworked)]) == EXIT_INVALID

    reworked.write_text("lib.a.x_f__mutmut_2\n", encoding="utf-8")
    _write_round(tmp_path / "baseline", _BASELINE, {"lib.a.x_f": "h1"})
    _write_round(tmp_path / "current", {**_BASELINE, "lib.a.x_f__mutmut_2": 1}, {"lib.a.x_f": "h1-changed"})
    assert main([*args, str(reworked)]) == EXIT_INVALID
    assert "源码已变 lib.a.x_f" in capsys.readouterr().out

    assert main([*args, str(tmp_path / "missing.txt")]) == EXIT_INVALID
    assert "读取输入失败" in capsys.readouterr().err
