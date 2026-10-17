"""``python -m arcreel_catalog_core``：退出码即 CI 判定，问题逐行可定位。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from catalog_factories import catalog

from arcreel_catalog_core import CATALOG_FILENAME, MANIFEST_FILENAME
from arcreel_catalog_core.cli import main


def _write_catalog(directory: Path, data: dict) -> None:
    (directory / CATALOG_FILENAME).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_write_manifest_then_check_exits_zero(tmp_path: Path):
    _write_catalog(tmp_path, catalog())

    assert main(["write-manifest", str(tmp_path)]) == 0
    assert main(["check", str(tmp_path)]) == 0
    assert (tmp_path / MANIFEST_FILENAME).exists()


def test_check_failure_prints_one_located_line_per_issue(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    data = catalog()
    _write_catalog(tmp_path, data)
    main(["write-manifest", str(tmp_path)])
    data["builtin_providers"]["dashscope"]["models"][0]["entry"] = "alibaba/qwen-tts-latest"
    _write_catalog(tmp_path, data)
    capsys.readouterr()

    exit_code = main(["check", str(tmp_path)])

    assert exit_code == 1
    located = [line.split(" ")[:2] for line in capsys.readouterr().err.splitlines()]
    assert located == [
        ["manifest.json.sha256:", "[sha256_mismatch]"],
        ["builtin_providers.dashscope.models[0].entry:", "[unknown_entry]"],
    ]
