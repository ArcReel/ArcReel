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
        "version": 2,
        "batch": {
            "title": "测试批次",
            "top_recommendation": {"id": "FU-01", "reason": "触发路径已验证且改动范围可控。"},
        },
        "issues": [{"number": 1, "title": "测试 issue", "state": "merged", "pr": 2}],
        "followups": [
            {
                "id": "FU-01",
                "recommendation": "强烈建议",
                "title": "统一真相源",
                "body_markdown": "两个模块声明了不同默认值。\n\n```mermaid\nflowchart LR\nA --> B\n```",
                "sources": ["handoff-1.md", "EV-0001"],
            }
        ],
        "knowledge_reviewed": ["CONTEXT", "ADR", "INST"],
        "knowledge": [],
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
        self.second_handoff = self.handoff_dir / "handoff-2.md"
        self.second_handoff.write_text("### 第二份交接\n\n另一份正文\n", encoding="utf-8")
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
        self.assertNotIn("批次结论", html)
        self.assertIn("handoff-1.md", html)
        self.assertIn("body_markdown", html)
        self.assertIn("mermaid.run", html)
        self.assertIn("timeline-toggle", html)
        self.assertIn('id="timeline-axis"', html)
        self.assertNotIn('id="density"', html)
        self.assertIn("validTimes.length > 1 && t0 !== t1", html)
        self.assertIn('decision: "决策"', html)
        self.assertIn('closed: "收口"', html)
        self.assertIn("evidence-ledger", html)
        self.assertIn('id="handoff-select"', html)
        self.assertIn("handoff-2.md", html)
        self.assertIn(".evidence-pane[hidden] { display: none; }", html)
        self.assertNotIn("team-lead 综合建议", html)
        self.assertIn("Top recommendation", html)
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
        self.second_handoff.unlink()
        with self.assertRaisesRegex(MODULE.ReportError, "no handoff files"):
            MODULE.build_report_data(self.root, "batch-one", self.analysis)

    def test_embedded_content_cannot_break_out_of_json_script(self) -> None:
        self.handoff.write_text("<script>alert('handoff')</script>\n", encoding="utf-8")
        data = analysis_fixture()
        data["batch"]["title"] = "</script><script>alert('analysis')</script>"
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
        del data["followups"][0]["body_markdown"]
        self.analysis.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ReportError, "body_markdown is required"):
            MODULE.build_report_data(self.root, "batch-one", self.analysis)

        data = analysis_fixture()
        data["version"] = 1
        self.analysis.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ReportError, "analysis.version must be 2"):
            MODULE.build_report_data(self.root, "batch-one", self.analysis)

        data = analysis_fixture()
        data["followups"][0]["recommendation"] = "P1"
        self.analysis.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ReportError, "recommendation must be one of"):
            MODULE.build_report_data(self.root, "batch-one", self.analysis)

        data = analysis_fixture()
        del data["batch"]["top_recommendation"]
        self.analysis.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ReportError, "top_recommendation must be an object"):
            MODULE.build_report_data(self.root, "batch-one", self.analysis)

        data = analysis_fixture()
        data["batch"]["top_recommendation"]["id"] = "DEC-01"
        self.analysis.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ReportError, "must reference an actionable"):
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
        data["batch"]["title"] = float("nan")
        self.analysis.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ReportError, "non-finite JSON constant"):
            MODULE.build_report_data(self.root, "batch-one", self.analysis)

        data = analysis_fixture()
        data["followups"][0]["id"] = "EV-0001"
        data["batch"]["top_recommendation"]["id"] = "EV-0001"
        self.analysis.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ReportError, "collides with a ledger event"):
            MODULE.build_report_data(self.root, "batch-one", self.analysis)

        decision = {
            "id": "DEC-01",
            "kind": "搁置",
            "title": "需要决定",
            "body_markdown": "两个选项",
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

    def test_accepts_markdown_body_and_structured_decision_options(self) -> None:
        data = analysis_fixture()
        data["pending"] = [
            {
                "id": "DEC-01",
                "kind": "搁置",
                "title": "需要决定",
                "body_markdown": "## 背景\n\n选择一个方案。",
                "positions": [
                    {"id": "DEC-01-A", "label": "A", "stance": "处理", "reason": "有收益"},
                    {"id": "DEC-01-B", "label": "B", "stance": "不处理", "reason": "收益低"},
                ],
                "current_state": "待决定",
                "sources": ["EV-0001"],
            }
        ]
        self.analysis.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        report = MODULE.build_report_data(self.root, "batch-one", self.analysis)

        self.assertEqual(report["pending"][0]["positions"][1]["id"], "DEC-01-B")

    def test_allows_no_action_report_without_top_recommendation(self) -> None:
        data = analysis_fixture()
        data["followups"][0]["recommendation"] = "无需处理"
        del data["batch"]["top_recommendation"]
        self.analysis.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        report = MODULE.build_report_data(self.root, "batch-one", self.analysis)

        self.assertNotIn("top_recommendation", report["batch"])

    def test_top_recommendation_can_reference_actionable_knowledge(self) -> None:
        data = analysis_fixture()
        data["followups"] = []
        data["knowledge"] = [
            {
                "id": "INST-01",
                "target": "INST",
                "action": "create",
                "recommendation": "强烈建议",
                "title": "补充交付检查",
                "body_markdown": "现有 agent instructions 缺少可检查的完成条件。",
                "sources": ["handoff-1.md"],
            }
        ]
        data["batch"]["top_recommendation"] = {"id": "INST-01", "reason": "能减少重复返工。"}
        self.analysis.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        report = MODULE.build_report_data(self.root, "batch-one", self.analysis)

        self.assertEqual(report["batch"]["top_recommendation"]["id"], "INST-01")

        data["knowledge"][0]["action"] = "supersede"
        self.analysis.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ReportError, "action must be one of for INST"):
            MODULE.build_report_data(self.root, "batch-one", self.analysis)

    def test_validates_knowledge_review_and_action_invariants(self) -> None:
        data = analysis_fixture()
        data["knowledge_reviewed"] = ["CONTEXT", "ADR"]
        self.analysis.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ReportError, "must contain each target exactly once"):
            MODULE.build_report_data(self.root, "batch-one", self.analysis)

        data = analysis_fixture()
        data["knowledge"] = [
            {
                "id": "ADR-01",
                "target": "ADR",
                "action": "supersede",
                "recommendation": "强烈建议",
                "title": "取代旧决策",
                "body_markdown": "核心决策已经改变。",
                "sources": ["handoff-1.md"],
            }
        ]
        self.analysis.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ReportError, "target_ref is required"):
            MODULE.build_report_data(self.root, "batch-one", self.analysis)

        data["knowledge"][0]["target_ref"] = "docs/adr/0001-old-decision.md"
        data["batch"]["top_recommendation"] = {"id": "ADR-01", "reason": "核心决策已经改变。"}
        self.analysis.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        report = MODULE.build_report_data(self.root, "batch-one", self.analysis)
        self.assertEqual(report["knowledge"][0]["action"], "supersede")

        data["knowledge"][0]["action"] = "create"
        self.analysis.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ReportError, "target_ref must be omitted"):
            MODULE.build_report_data(self.root, "batch-one", self.analysis)

        del data["knowledge"][0]["target_ref"]
        data["knowledge"][0]["action"] = "none"
        self.analysis.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ReportError, "action none must pair"):
            MODULE.build_report_data(self.root, "batch-one", self.analysis)

    def test_rejects_ledger_snapshot_as_candidate_source(self) -> None:
        data = analysis_fixture()
        data["followups"][0]["sources"] = ["batch-one.jsonl"]
        self.analysis.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        with self.assertRaisesRegex(MODULE.ReportError, "source does not resolve"):
            MODULE.build_report_data(self.root, "batch-one", self.analysis)


if __name__ == "__main__":
    unittest.main()
