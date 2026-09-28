"""市场仓语言边界保留可操作的诊断、迁移指引与汇总。"""

import json
from pathlib import Path

import pytest

from scripts.market import main
from tests.factories import custom_endpoint_definition


@pytest.mark.parametrize(
    ("locale_args", "written", "passed", "reason", "check_failed", "generate_failed"),
    [
        (
            [],
            "已写入 {path}（1 个条目）",
            "市场源校验通过：{directory}",
            "轮询间隔与超时是运行时策略，不进定义",
            "市场源校验未通过，共 1 个问题",
            "无法生成索引，共 1 个问题",
        ),
        (
            ["--locale", "en"],
            "Wrote {path} (1 entries)",
            "Market source check passed: {directory}",
            "polling interval and timeout are runtime policy, not part of a definition",
            "Market source check failed with 1 issue(s)",
            "Cannot generate the index: 1 issue(s)",
        ),
        (
            ["--locale", "vi"],
            "Đã ghi {path} (1 mục)",
            "Kiểm tra nguồn chợ đạt: {directory}",
            "chu kỳ và thời gian chờ khi hỏi trạng thái là chính sách thời gian chạy, không nằm trong định nghĩa",
            "Kiểm tra nguồn chợ không đạt: 1 vấn đề",
            "Không tạo được chỉ mục: 1 vấn đề",
        ),
    ],
    ids=["default-zh", "en", "vi"],
)
def test_workflow_reports_localized_diagnostics_and_summaries(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    locale_args: list[str],
    written: str,
    passed: str,
    reason: str,
    check_failed: str,
    generate_failed: str,
):
    directory = tmp_path / "endpoints" / "demo"
    directory.mkdir(parents=True)
    definition = custom_endpoint_definition()
    definition["meta"] = {"name": "Demo", "author": "ArcReel", "version": "1.0.0"}
    path = directory / "definition.json"
    path.write_text(json.dumps(definition), encoding="utf-8")

    assert main([*locale_args, "generate", str(tmp_path)]) == 0
    assert capsys.readouterr().out == written.format(path=tmp_path / "arcreel-market.json") + "\n"
    assert main([*locale_args, "check", str(tmp_path)]) == 0
    assert capsys.readouterr().out == passed.format(directory=tmp_path) + "\n"

    definition["poll"]["interval_seconds"] = 5
    path.write_text(json.dumps(definition), encoding="utf-8")
    for command, summary in [("check", check_failed), ("generate", generate_failed)]:
        assert main([*locale_args, command, str(tmp_path)]) == 1
        lines = capsys.readouterr().err.splitlines()
        assert lines[0].startswith("endpoints/demo/definition.json:poll: [definition_invalid]")
        assert reason in lines[0]
        assert lines[-1] == summary


def test_workflow_explains_slug_constraints(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    directory = tmp_path / "endpoints" / "Bad_Slug"
    directory.mkdir(parents=True)
    definition = custom_endpoint_definition()
    definition["meta"] = {"name": "Demo", "author": "ArcReel", "version": "1.0.0"}
    (directory / "definition.json").write_text(json.dumps(definition), encoding="utf-8")

    assert main(["generate", str(tmp_path)]) == 1
    assert capsys.readouterr().err == (
        "arcreel-market.json:entries[0].slug: [slug_invalid] "
        "slug「Bad_Slug」不合规：只允许小写字母、数字与连字符，以字母或数字开头，最长 64 个字符\n"
        "无法生成索引，共 1 个问题\n"
    )
