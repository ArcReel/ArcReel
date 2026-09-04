from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

from scripts.decorator_census import Tally, census, display_width, is_skipped_function, main, pad, tally_tree

_SOURCE = textwrap.dedent(
    """\
    def plain():
        x = 1
        return x

    @decorate
    def decorated():
        return 1

    class Plain:
        @staticmethod
        def exempt():
            return 1

        @property
        def prop(self):
            return 1

    @dataclass
    class Decorated:
        def method(self):
            return 1

    def outer():
        def inner():
            return 1
        return inner

    @decorate
    def outer_skipped():
        def inner():
            return 1
        return inner
    """
)


def test_tally_counts_decorated_functions_decorated_classes_and_nesting_by_mutmut_rules() -> None:
    """`outer` 里的 `inner` 单独计一个函数，但它的 2 行已在 `outer` 的区间内，行数只算一次。"""
    tally = tally_tree(ast.parse(_SOURCE))

    assert tally == Tally(functions=8, skipped_functions=4, lines=23, skipped_lines=13)
    assert tally.skipped_line_ratio == pytest.approx(13 / 23)


@pytest.mark.parametrize(
    ("decorators", "skipped"),
    [
        ("", False),
        ("@staticmethod\n", False),
        ("@classmethod\n", False),
        ("@staticmethod\n@decorate\n", True),
        ("@functools.cache\n", True),
        ("@tool(name='x')\n", True),
    ],
)
def test_single_staticmethod_or_classmethod_is_the_only_exempt_decorator(decorators: str, skipped: bool) -> None:
    node = ast.parse(f"{decorators}def f():\n    return 1\n").body[0]
    assert isinstance(node, ast.FunctionDef)

    assert is_skipped_function(node) is skipped


def test_census_groups_by_first_level_subdirectory_and_skips_unparsable_files(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    (root / "sub").mkdir(parents=True)
    (root / "top.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (root / "sub" / "a.py").write_text("@d\ndef g():\n    return 1\n", encoding="utf-8")
    (root / "sub" / "broken.py").write_text("def (:\n", encoding="utf-8")

    per_dir, per_file = census(root)

    assert list(per_file) == [(root / "sub" / "a.py").as_posix(), (root / "top.py").as_posix()]
    assert list(per_dir) == [root.as_posix(), (root / "sub").as_posix()]
    assert per_dir == {
        root.as_posix(): Tally(functions=1, skipped_functions=0, lines=2, skipped_lines=0),
        (root / "sub").as_posix(): Tally(functions=1, skipped_functions=1, lines=3, skipped_lines=3),
    }


def test_main_per_file_lists_only_files_at_or_above_min_skip_sorted_by_ratio(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "clean.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (root / "half.py").write_text("def f():\n    return 1\n@d\ndef g():\n    return 1\n", encoding="utf-8")
    (root / "all.py").write_text("@d\ndef g():\n    return 1\n", encoding="utf-8")

    code = main([str(root), "--per-file", "--min-skip", "50"])

    out = capsys.readouterr().out
    assert code == 0
    listed = [line.split()[0] for line in out.splitlines() if line.startswith(str(root))]
    assert listed == [(root / "all.py").as_posix(), (root / "half.py").as_posix()]
    assert "100.0%" in out
    assert " 60.0%" in out


def test_pad_aligns_by_terminal_display_width_so_cjk_labels_line_up_with_ascii_paths() -> None:
    assert display_width("合计") == 4
    assert display_width("lib/a.py") == 8
    assert display_width("café") == 4

    assert pad("合计", 8) == "合计    "
    assert pad("跳过%", 7, align_right=True) == "  跳过%"
    assert pad("lib/a.py", 8) == "lib/a.py"
    assert pad("超出宽度的标签", 4) == "超出宽度的标签"


def test_main_prints_directory_summary_with_total_and_rejects_non_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    Path("pkg/sub").mkdir(parents=True)
    Path("pkg/sub/a.py").write_text("@d\ndef g():\n    return 1\n", encoding="utf-8")

    assert main(["pkg"]) == 0
    out = capsys.readouterr().out
    assert "pkg/sub" in out
    assert "100.0%" in out
    table = [line for line in out.splitlines() if line.startswith(("目录", "pkg", "合计"))]
    assert len(table) == 3
    assert [display_width(line) for line in table] == [display_width(table[0])] * 3

    assert main(["missing"]) == 2
    assert "不是目录" in capsys.readouterr().err
