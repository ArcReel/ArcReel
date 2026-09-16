"""``python -m lib.market`` 命令行：退出码即 CI 判定，诊断逐行可定位。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.i18n import MESSAGES, SUPPORTED_LOCALES
from lib.market import INDEX_FILENAME, MarketIssueCode, message_key
from lib.market.cli import main
from tests.factories import custom_endpoint_definition


def _source_with_entry(root: Path, *, slug: str = "demo", **meta: str) -> Path:
    directory = root / "endpoints" / slug
    directory.mkdir(parents=True)
    definition = custom_endpoint_definition()
    definition["meta"] = {"name": "演示视频", "author": "ArcReel", "version": "1.0.0", **meta}
    (directory / "definition.json").write_text(json.dumps(definition, ensure_ascii=False), encoding="utf-8")
    return root


def test_generate_then_check_round_trip_exits_zero(tmp_path: Path):
    source = _source_with_entry(tmp_path)

    assert main(["generate", str(source), "--name", "测试市场"]) == 0
    assert main(["check", str(source)]) == 0
    assert json.loads((source / INDEX_FILENAME).read_text(encoding="utf-8"))["name"] == "测试市场"


def test_check_failure_prints_one_located_line_per_issue(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    source = _source_with_entry(tmp_path)
    main(["generate", str(source)])
    index = json.loads((source / INDEX_FILENAME).read_text(encoding="utf-8"))
    index["entries"][0]["version"] = "9.9.9"
    (source / INDEX_FILENAME).write_text(json.dumps(index), encoding="utf-8")
    capsys.readouterr()

    exit_code = main(["--locale", "en", "check", str(source)])

    lines = capsys.readouterr().err.splitlines()
    assert exit_code == 1
    assert lines[0] == (
        'arcreel-market.json:entries[0].version: [projection_mismatch] Index field version is "9.9.9", '
        'which differs from "1.0.0" in the definition meta'
    )
    assert lines[-1] == "Market source check failed with 1 issue(s)"


def test_generate_dry_run_prints_without_writing(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    source = _source_with_entry(tmp_path)

    assert main(["generate", str(source), "--dry-run"]) == 0

    assert json.loads(capsys.readouterr().out)["entries"][0]["slug"] == "demo"
    assert not (source / INDEX_FILENAME).exists()


def test_generate_failure_exits_nonzero(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    (tmp_path / "endpoints" / "demo").mkdir(parents=True)

    assert main(["generate", str(tmp_path)]) == 1
    assert "[file_missing]" in capsys.readouterr().err


def test_generate_rejects_output_that_would_fail_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    source = _source_with_entry(tmp_path, slug="Bad_Slug")

    assert main(["generate", str(source)]) == 1
    assert "[slug_invalid]" in capsys.readouterr().err
    assert not (source / INDEX_FILENAME).exists()


def test_every_issue_code_reads_as_prose_in_every_locale():
    for code in MarketIssueCode:
        for locale in SUPPORTED_LOCALES:
            assert message_key(code) in MESSAGES[locale], f"{message_key(code)} 缺 {locale} 文案"
