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


def test_record_attribute_counts_as_double_only_when_its_owner_is_a_double(tmp_path: Path) -> None:
    tests, _ = _repo(tmp_path)
    (tests / "test_records.py").write_text(
        "def test_domain_result(fake_dep):\n"
        "    result = compute()\n"
        "    assert result.called\n"
        "    assert result.call_count == 2\n"
        "\n"
        "\n"
        "def test_double_record(mocker):\n"
        "    client = mocker.patch('svc.client')\n"
        "    run()\n"
        "    assert client.send.called\n",
        encoding="utf-8",
    )

    violations = gate_violations(_audit(tmp_path))

    assert [(v.rule, v.line) for v in violations] == [("DOUBLE-ONLY", 7)]
    assert "test_double_record" in violations[0].guidance


def test_dunder_test_false_opts_a_class_out_of_the_scan(tmp_path: Path) -> None:
    tests, _ = _repo(tmp_path)
    (tests / "test_optout.py").write_text(
        "import unittest\n"
        "\n"
        "\n"
        "class TestSupport:\n"
        "    __test__ = False\n"
        "\n"
        "    def test_helper(self):\n"
        "        value = 1\n"
        "\n"
        "\n"
        "class AbstractCase(unittest.TestCase):\n"
        "    __test__ = False\n"
        "\n"
        "    def test_shared(self):\n"
        "        value = 1\n",
        encoding="utf-8",
    )

    assert gate_violations(_audit(tmp_path)) == []


def test_functional_pytest_assertions_count_as_assertions(tmp_path: Path) -> None:
    tests, _ = _repo(tmp_path)
    (tests / "test_functional.py").write_text(
        "def test_functional_raises():\n"
        "    pytest.raises(ValueError, int, 'bad')\n"
        "\n"
        "\n"
        "def test_fail_sentinel():\n"
        "    try:\n"
        "        run()\n"
        "    except RuntimeError:\n"
        "        pytest.fail('should not raise')\n"
        "\n"
        "\n"
        "def test_bare_raises_is_not_an_assertion():\n"
        "    pytest.raises(ValueError)\n",
        encoding="utf-8",
    )

    violations = gate_violations(_audit(tmp_path))

    assert [(v.rule, v.line) for v in violations] == [("NO-ASSERTION", 12)]
    assert "test_bare_raises_is_not_an_assertion" in violations[0].guidance


def test_class_scan_follows_pytest_collection_rules(tmp_path: Path) -> None:
    tests, _ = _repo(tmp_path)
    (tests / "test_client.py").write_text(
        "import unittest\n"
        "\n"
        "\n"
        "class FakeClient:\n"
        "    def test_connection(self):\n"
        "        return True\n"
        "\n"
        "\n"
        "class TestClient:\n"
        "    def test_silent(self):\n"
        "        value = 1\n"
        "\n"
        "\n"
        "class CheckBehavior(unittest.TestCase):\n"
        "    def test_also_silent(self):\n"
        "        value = 1\n"
        "\n"
        "\n"
        "class CheckAsync(unittest.IsolatedAsyncioTestCase):\n"
        "    async def test_async_silent(self):\n"
        "        value = 1\n"
        "\n"
        "\n"
        "class TestWithInit:\n"
        "    def __init__(self):\n"
        "        self.value = 1\n"
        "\n"
        "    def test_not_collected(self):\n"
        "        value = 1\n"
        "\n"
        "\n"
        "class LegacyCase(unittest.TestCase):\n"
        "    def __init__(self, method_name='runTest'):\n"
        "        super().__init__(method_name)\n"
        "\n"
        "    def test_still_collected(self):\n"
        "        value = 1\n",
        encoding="utf-8",
    )

    violations = gate_violations(_audit(tmp_path))

    assert [(v.rule, v.line) for v in violations] == [
        ("NO-ASSERTION", 10),
        ("NO-ASSERTION", 15),
        ("NO-ASSERTION", 20),
        ("NO-ASSERTION", 36),
    ]
    assert "TestClient::test_silent" in violations[0].guidance
    assert "CheckBehavior::test_also_silent" in violations[1].guidance
    assert "CheckAsync::test_async_silent" in violations[2].guidance
    assert "LegacyCase::test_still_collected" in violations[3].guidance


def test_unittest_ancestry_resolves_aliases_and_in_module_inheritance(tmp_path: Path) -> None:
    tests, _ = _repo(tmp_path)
    (tests / "test_ancestry.py").write_text(
        "import unittest as ut\n"
        "from unittest import TestCase as Base\n"
        "from other import TestCase as Unrelated\n"
        "\n"
        "\n"
        "class AliasedCase(Base):\n"
        "    def test_via_alias(self):\n"
        "        value = 1\n"
        "\n"
        "\n"
        "class ModuleAliasedCase(ut.IsolatedAsyncioTestCase):\n"
        "    async def test_via_module_alias(self):\n"
        "        value = 1\n"
        "\n"
        "\n"
        "class IndirectCase(AliasedCase):\n"
        "    def test_via_ancestor(self):\n"
        "        value = 1\n"
        "\n"
        "\n"
        "class Impostor(Unrelated):\n"
        "    def test_not_a_unittest_case(self):\n"
        "        value = 1\n",
        encoding="utf-8",
    )

    violations = gate_violations(_audit(tmp_path))

    assert [(v.rule, v.line) for v in violations] == [
        ("NO-ASSERTION", 7),
        ("NO-ASSERTION", 12),
        ("NO-ASSERTION", 17),
    ]
    assert "AliasedCase::test_via_alias" in violations[0].guidance
    assert "ModuleAliasedCase::test_via_module_alias" in violations[1].guidance
    assert "IndirectCase::test_via_ancestor" in violations[2].guidance


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
