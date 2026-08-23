from __future__ import annotations

from pathlib import Path

from scripts.audit_tests import FILE_LINE_LIMIT, gate_violations, main, run

_HEALTHY_TEST = "def test_a():\n    value = 1\n    assert value == 1\n"


def _repo(tmp_path: Path) -> tuple[Path, Path]:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "conftest.py").write_text("", encoding="utf-8")
    frontend_src = tmp_path / "frontend" / "src"
    frontend_src.mkdir(parents=True)
    return tests, frontend_src


def _audit(tmp_path: Path) -> dict[str, object]:
    return run(tmp_path, tmp_path / "tests", top=10, frontend_src=tmp_path / "frontend" / "src")


def _rules(result: dict[str, object]) -> list[tuple[str, str]]:
    return [(v.rule, v.path) for v in gate_violations(result)]


def test_split_suffix_hits_backend_and_frontend_but_spares_substring_matches(tmp_path: Path) -> None:
    tests, frontend_src = _repo(tmp_path)
    (tests / "test_thing_more.py").write_text(_HEALTHY_TEST, encoding="utf-8")
    (tests / "test_usage_extraction.py").write_text(_HEALTHY_TEST, encoding="utf-8")
    (frontend_src / "Widget_full.test.tsx").write_text("// nothing\n", encoding="utf-8")
    (frontend_src / "Widget.drama.test.tsx").write_text("// nothing\n", encoding="utf-8")
    (frontend_src / "Widget.drama_more.test.tsx").write_text("// nothing\n", encoding="utf-8")

    assert _rules(_audit(tmp_path)) == [
        ("NAME-SPLIT", "frontend/src/Widget.drama_more.test.tsx"),
        ("NAME-SPLIT", "frontend/src/Widget_full.test.tsx"),
        ("NAME-SPLIT", "tests/test_thing_more.py"),
    ]


def test_line_limit_burns_at_threshold_exceeded(tmp_path: Path) -> None:
    tests, frontend_src = _repo(tmp_path)
    body = "\n".join(f"# {i}" for i in range(FILE_LINE_LIMIT))
    (tests / "test_at_limit.py").write_text(body + "\n", encoding="utf-8")
    (frontend_src / "Over.test.ts").write_text(body + "\n# one more\n", encoding="utf-8")

    assert _rules(_audit(tmp_path)) == [("SIZE-LIMIT", "frontend/src/Over.test.ts")]


def test_frontend_tests_directory_is_rejected(tmp_path: Path) -> None:
    _, frontend_src = _repo(tmp_path)
    nested = frontend_src / "components" / "__tests__"
    nested.mkdir(parents=True)
    (nested / "Widget.test.tsx").write_text("// nothing\n", encoding="utf-8")

    assert _rules(_audit(tmp_path)) == [("FE-TESTS-DIR", "frontend/src/components/__tests__/Widget.test.tsx")]


def test_zero_assertion_case_is_reported_with_its_line(tmp_path: Path) -> None:
    tests, _ = _repo(tmp_path)
    (tests / "test_silent.py").write_text("def test_nothing():\n    value = 1\n", encoding="utf-8")

    violations = gate_violations(_audit(tmp_path))

    assert [(v.rule, v.path, v.line) for v in violations] == [("NO-ASSERTION", "tests/test_silent.py", 1)]
    assert "test_nothing" in violations[0].guidance


def test_unparsable_file_is_reported_at_its_syntax_error_line(tmp_path: Path, capsys) -> None:
    tests, _ = _repo(tmp_path)
    (tests / "test_broken.py").write_text("def test_a():\n    assert (1 ==\n", encoding="utf-8")

    violations = gate_violations(_audit(tmp_path))

    assert [(v.rule, v.path, v.line) for v in violations] == [("PARSE-FAIL", "tests/test_broken.py", 2)]
    assert main(["--root", str(tmp_path), "--check"]) == 1
    assert "PARSE-FAIL tests/test_broken.py:2" in capsys.readouterr().out


def test_check_exits_nonzero_on_violation_and_zero_when_clean(tmp_path: Path, capsys) -> None:
    tests, _ = _repo(tmp_path)
    dirty = tests / "test_thing_more.py"
    dirty.write_text(_HEALTHY_TEST, encoding="utf-8")

    assert main(["--root", str(tmp_path), "--check"]) == 1
    assert "NAME-SPLIT tests/test_thing_more.py:1" in capsys.readouterr().out

    dirty.rename(tests / "test_thing_lifecycle.py")

    assert main(["--root", str(tmp_path), "--check"]) == 0
    assert "闸门通过：0 处违规" in capsys.readouterr().out
