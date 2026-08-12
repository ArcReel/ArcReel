from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).with_name("render_report.py")
SPEC = importlib.util.spec_from_file_location("afk_render_report", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def analysis_fixture() -> dict[str, Any]:
    return {
        "version": 1,
        "batch": {"title": "测试批次", "summary": "批次已关闭，存在一项建议。"},
        "issues": [{"number": 1, "title": "测试 issue", "state": "merged", "pr": 2}],
        "followups": [
            {
                "id": "FU-01",
                "priority": "P1",
                "title": "统一真相源",
                "summary": "两个模块声明了不同默认值。",
                "evaluations": {
                    "architecture": {"verdict": "支持", "summary": "减少重复策略。"},
                    "product_user": {"verdict": "支持", "summary": "避免行为与承诺不一致。"},
                    "knowledge": {"verdict": "无关", "summary": "无需更新文档。"},
                },
                "conclusion": "P1，建议处理。",
                "sources": ["handoff-1.md", "EV-0001"],
            }
        ],
        "knowledge": {"CONTEXT": [], "ADR": [], "INST": []},
        "pending": [],
    }


class RenderReportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.handoff_dir = self.root / ".afk" / "batch-one"
        self.handoff_dir.mkdir(parents=True)
        ledger = [
            {"ts": "2026-08-11T01:00:00Z", "kind": "decision", "scope": {"issues": [1]}, "detail": "开始"},
            {"ts": "2026-08-11T02:00:00Z", "kind": "closed", "scope": None, "detail": "完成"},
        ]
        (self.root / ".afk" / "batch-one.jsonl").write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in ledger), encoding="utf-8"
        )
        self.handoff = self.handoff_dir / "handoff-1.md"
        self.handoff.write_text("### 审查循环\n\n正文\n", encoding="utf-8")
        self.analysis = self.root / "analysis.json"
        self.analysis.write_text(json.dumps(analysis_fixture(), ensure_ascii=False), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_renders_completed_batch(self) -> None:
        report = MODULE.build_report_data(self.root, "batch-one", self.analysis)
        output = self.root / "report.html"
        MODULE.render_report(report, output)
        html = output.read_text(encoding="utf-8")
        self.assertIn("AFK 复盘", html)
        self.assertIn("handoff-1.md", html)
        self.assertEqual(len(report["operational"]["journey"]), 2)

    def test_rejects_unsafe_batch_id(self) -> None:
        with self.assertRaisesRegex(MODULE.ReportError, "batch-id must match"):
            MODULE.build_report_data(self.root, "../batch-one", self.analysis)

    def test_rejects_unclosed_batch(self) -> None:
        ledger = self.root / ".afk" / "batch-one.jsonl"
        ledger.write_text(json.dumps({"ts": "2026-08-11T01:00:00Z", "kind": "decision"}) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ReportError, "not closed"):
            MODULE.build_report_data(self.root, "batch-one", self.analysis)

    def test_rejects_missing_handoff(self) -> None:
        self.handoff.unlink()
        with self.assertRaisesRegex(MODULE.ReportError, "no handoff files"):
            MODULE.build_report_data(self.root, "batch-one", self.analysis)

    def test_embedded_content_cannot_break_out_of_json_script(self) -> None:
        self.handoff.write_text("<script>alert('handoff')</script>\n", encoding="utf-8")
        data = analysis_fixture()
        data["batch"]["summary"] = "</script><script>alert('analysis')</script>"
        self.analysis.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        output = self.root / "report.html"
        MODULE.render_report(MODULE.build_report_data(self.root, "batch-one", self.analysis), output)
        html = output.read_text(encoding="utf-8")
        self.assertNotIn("<script>alert('handoff')</script>", html)
        self.assertNotIn("</script><script>alert('analysis')</script>", html)
        self.assertIn("\\u003cscript", html)

    def test_rejects_analysis_that_cannot_render(self) -> None:
        data = analysis_fixture()
        data["issues"] = [None]
        self.analysis.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ReportError, r"issues\[0\] must be an object"):
            MODULE.build_report_data(self.root, "batch-one", self.analysis)

        data = analysis_fixture()
        del data["followups"][0]["evaluations"]["architecture"]
        self.analysis.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ReportError, "architecture is required"):
            MODULE.build_report_data(self.root, "batch-one", self.analysis)

        data = analysis_fixture()
        data["followups"][0]["sources"] = ["EV-9999"]
        self.analysis.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ReportError, "source does not resolve"):
            MODULE.build_report_data(self.root, "batch-one", self.analysis)

        data = analysis_fixture()
        data["followups"][0]["sources"] = []
        self.analysis.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ReportError, "sources must not be empty"):
            MODULE.build_report_data(self.root, "batch-one", self.analysis)

        data = analysis_fixture()
        data["batch"]["summary"] = float("nan")
        self.analysis.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ReportError, "non-finite JSON constant"):
            MODULE.build_report_data(self.root, "batch-one", self.analysis)

        data = analysis_fixture()
        data["followups"][0]["id"] = "EV-0001"
        self.analysis.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ReportError, "collides with a ledger event"):
            MODULE.build_report_data(self.root, "batch-one", self.analysis)

        decision = {
            "id": "DEC-01",
            "kind": "搁置",
            "title": "需要决定",
            "context": "两个选项",
            "positions": [
                {"id": "DEC-01-A", "label": "A", "stance": "处理", "reason": "有收益"},
                {"id": "DEC-01-B", "label": "B", "stance": "不处理", "reason": "收益低"},
            ],
            "current_state": "待决定",
            "sources": ["EV-0001"],
        }

        data = analysis_fixture()
        data["pending"] = [decision]
        del decision["positions"][0]["reason"]
        self.analysis.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ReportError, "reason is required"):
            MODULE.build_report_data(self.root, "batch-one", self.analysis)

        data = analysis_fixture()
        data["followups"][0]["id"] = "CONTEXT-EMPTY"
        self.analysis.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ReportError, "reserved by the renderer"):
            MODULE.build_report_data(self.root, "batch-one", self.analysis)

        data = analysis_fixture()
        decision["positions"][0]["reason"] = "有收益"
        decision["positions"][0]["id"] = "EV-0001"
        data["pending"] = [decision]
        self.analysis.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ReportError, "collides with a ledger event"):
            MODULE.build_report_data(self.root, "batch-one", self.analysis)


if __name__ == "__main__":
    unittest.main()
