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
        "import pytest\n"
        "from pytest import fail as bail\n"
        "\n"
        "\n"
        "def test_functional_raises():\n"
        "    pytest.raises(ValueError, int, 'bad')\n"
        "\n"
        "\n"
        "def test_fail_sentinel():\n"
        "    try:\n"
        "        run()\n"
        "    except RuntimeError:\n"
        "        bail('should not raise')\n"
        "\n"
        "\n"
        "def test_bare_raises_is_not_an_assertion():\n"
        "    pytest.raises(ValueError)\n"
        "\n"
        "\n"
        "def test_unrelated_receiver_named_fail():\n"
        "    worker.fail('network')\n",
        encoding="utf-8",
    )

    violations = gate_violations(_audit(tmp_path))

    assert [(v.rule, v.line) for v in violations] == [("NO-ASSERTION", 16), ("NO-ASSERTION", 20)]
    assert "test_bare_raises_is_not_an_assertion" in violations[0].guidance
    assert "test_unrelated_receiver_named_fail" in violations[1].guidance


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


def _dup_lines(result: dict[str, object]) -> list[str]:
    return [f"{v.path}:{v.line}" for v in gate_violations(result) if v.rule == "DUP-BODY"]


def test_identical_bodies_hit_within_a_file_but_not_across_files(tmp_path: Path) -> None:
    tests, _ = _repo(tmp_path)
    body = "    assert helper() == 1\n"
    (tests / "test_same.py").write_text(
        f'def test_a():\n{body}\n\ndef test_b():\n    """只差 docstring 仍是重复。"""\n{body}',
        encoding="utf-8",
    )
    (tests / "test_other.py").write_text(f"def test_b():\n{body}", encoding="utf-8")
    assert _dup_lines(_audit(tmp_path)) == ["tests/test_same.py:5"]


def test_differing_fixtures_or_decorators_spare_identical_bodies(tmp_path: Path) -> None:
    tests, _ = _repo(tmp_path)
    (tests / "test_seams.py").write_text(
        "import pytest\n\n\n"
        "def test_a(sqlite_session):\n    assert run(sqlite_session) == 1\n\n\n"
        "def test_b(pg_session):\n    assert run(pg_session) == 1\n\n\n"
        "@pytest.mark.slow\n"
        "def test_c():\n    assert run() == 1\n\n\n"
        "def test_d():\n    assert run() == 1\n",
        encoding="utf-8",
    )
    assert _dup_lines(_audit(tmp_path)) == []


def test_parametrized_table_containing_a_plain_case_reports_the_contained_one(tmp_path: Path) -> None:
    tests, _ = _repo(tmp_path)
    (tests / "test_table.py").write_text(
        "import pytest\n\n\n"
        'def test_plain(env):\n    assert parse(env, "1") is True\n\n\n'
        '@pytest.mark.parametrize("value", ["1", "true"])\n'
        "def test_aliases(env, value):\n    assert parse(env, value) is True\n\n\n"
        '@pytest.mark.parametrize("other", ["1", "true"])\n'
        "def test_twin_table(env, other):\n    assert parse(env, other) is True\n",
        encoding="utf-8",
    )
    violations = [v for v in gate_violations(_audit(tmp_path)) if v.rule == "DUP-BODY"]
    assert [f"{v.path}:{v.line}" for v in violations] == ["tests/test_table.py:4"]
    assert "value='1'" in violations[0].guidance


def test_parametrized_case_outside_the_table_and_non_literal_values_are_spared(tmp_path: Path) -> None:
    tests, _ = _repo(tmp_path)
    (tests / "test_table_misses.py").write_text(
        "import pytest\n\n\n"
        'def test_absent(env):\n    assert parse(env, "nope") is True\n\n\n'
        '@pytest.mark.parametrize("value", ["1", "true"])\n'
        "def test_aliases(env, value):\n    assert parse(env, value) is True\n\n\n"
        "def test_computed(env):\n    assert parse(env, ALIASES[0]) is True\n\n\n"
        '@pytest.mark.parametrize("value", [ALIASES[0]])\n'
        "def test_dynamic(env, value):\n    assert parse(env, value) is True\n",
        encoding="utf-8",
    )
    assert _dup_lines(_audit(tmp_path)) == []


_DUP_CASE = "    def test_it(self):\n        assert probe() == 1\n"


def test_parameter_shadowed_inside_the_body_is_not_substituted(tmp_path: Path) -> None:
    tests, _ = _repo(tmp_path)
    (tests / "test_shadow.py").write_text(
        "import pytest\n\n\n"
        "def test_plain(env):\n"
        "    assert parse(env, (lambda value: '1')('x')) is True\n\n\n"
        '@pytest.mark.parametrize("value", ["1"])\n'
        "def test_table(env, value):\n"
        "    assert parse(env, (lambda value: value)('x')) is True\n",
        encoding="utf-8",
    )
    assert _dup_lines(_audit(tmp_path)) == []


def test_differing_class_level_setup_spares_identical_bodies(tmp_path: Path) -> None:
    tests, _ = _repo(tmp_path)
    (tests / "test_setup.py").write_text(
        f'class TestOne:\n    def setup_method(self):\n        reset(mode="a")\n\n{_DUP_CASE}\n\n'
        f'class TestTwo:\n    def setup_method(self):\n        reset(mode="b")\n\n{_DUP_CASE}',
        encoding="utf-8",
    )
    assert _dup_lines(_audit(tmp_path)) == []


def test_matching_class_level_setup_lets_identical_bodies_count_as_duplicates(tmp_path: Path) -> None:
    tests, _ = _repo(tmp_path)
    (tests / "test_setup.py").write_text(
        f'class TestOne:\n    """文档不是行为。"""\n\n'
        f'    def setup_method(self):\n        reset(mode="a")\n\n{_DUP_CASE}\n\n'
        f'class TestTwo:\n    def setup_method(self):\n        reset(mode="a")\n\n{_DUP_CASE}',
        encoding="utf-8",
    )
    assert _dup_lines(_audit(tmp_path)) == ["tests/test_setup.py:15"]


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
