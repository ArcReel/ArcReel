from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.mutmut_compare import EXIT_FAILED, EXIT_INVALID, EXIT_OK, compare, load_exit_codes, main, render

_BASELINE = {
    "lib.a.x_f__mutmut_1": 1,
    "lib.a.x_f__mutmut_2": 0,
    "lib.a.x_f__mutmut_3": 36,
    "lib.a.x_f__mutmut_4": 0,
    "lib.a.x_f__mutmut_5": 0,
    "lib.b.x_g__mutmut_1": 1,
}


def _write_meta(root: Path, name: str, codes: dict[str, int | None]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    payload = {"exit_code_by_key": codes, "hash_by_function_name": {}}
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
    ],
)
def test_inconsistent_inputs_make_the_comparison_invalid(
    current: dict[str, int | None], reworked: list[str], equivalent: list[str], expected_fragment: str
) -> None:
    report = compare(_BASELINE, current, reworked=reworked, equivalent=equivalent)

    assert not report.passed
    assert any(expected_fragment in line for line in report.invalid)
    assert report.reworked_killed == []


def test_load_exit_codes_merges_meta_files_recursively_and_rejects_duplicates(tmp_path: Path) -> None:
    _write_meta(tmp_path / "lib", "a.py.meta", {"lib.a.x_f__mutmut_1": 1, "lib.a.x_f__mutmut_2": None})
    _write_meta(tmp_path / "server", "b.py.meta", {"server.b.x_g__mutmut_1": 0})

    assert load_exit_codes(tmp_path) == {
        "lib.a.x_f__mutmut_1": 1,
        "lib.a.x_f__mutmut_2": None,
        "server.b.x_g__mutmut_1": 0,
    }

    _write_meta(tmp_path / "dup", "a.py.meta", {"lib.a.x_f__mutmut_1": 1})
    with pytest.raises(ValueError, match=r"lib\.a\.x_f__mutmut_1 在 .* 下出现多次"):
        load_exit_codes(tmp_path)


def test_main_exit_codes_follow_verdict(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_meta(tmp_path / "baseline", "a.py.meta", _BASELINE)
    _write_meta(tmp_path / "current", "a.py.meta", {**_BASELINE, "lib.a.x_f__mutmut_2": 1})
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

    assert main([*args, str(tmp_path / "missing.txt")]) == EXIT_INVALID
    assert "读取输入失败" in capsys.readouterr().err
