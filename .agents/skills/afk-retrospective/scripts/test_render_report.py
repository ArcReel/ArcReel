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
        "batch": {
            "title": "测试批次",
            "summary": "批次已关闭，存在一项建议。",
            "headline_ids": ["FU-01"],
            "counts": {"merged": 1, "shelved": 0, "not_started": 0, "cleanup_filed": 0},
        },
        "issues": [
            {
                "number": 1,
                "title": "测试 issue",
                "state": "merged",
                "pr": 2,
                "stages": [{"name": "实现", "status": "done", "rounds": None}],
            }
        ],
        "followups": [
            {
                "id": "FU-01",
                "candidate_ids": ["CAND-001"],
                "priority": "P1",
                "title": "转义 <script> 标签",
                "summary": "原始内容不能改变报告 DOM。",
                "status": "已验证",
                "origin": {"issues": [1], "prs": [2], "where": "handoff-1"},
                "evaluations": {
                    "architecture": {"verdict": "支持", "summary": "locality 更好"},
                    "product_user": {"verdict": "支持", "summary": "报告可读"},
                    "knowledge": {"verdict": "无关", "summary": "无文档候选"},
                },
                "conclusion": "P1，单独处理。",
                "evidence": [{"kind": "handoff", "ref": "handoff-1.md", "text": "<audio>"}],
            }
        ],
        "gates": [
            {"key": "CONTEXT", "label": "CONTEXT.md", "rule": "术语形状", "empty_note": "无", "items": []},
            {"key": "ADR", "label": "docs/adr/", "rule": "三门", "empty_note": "无", "items": []},
            {"key": "INST", "label": "agent instructions", "rule": "返工预防", "empty_note": "无", "items": []},
        ],
        "pending": [],
        "pushbacks": [],
        "reply_defaults": ["FU-01"],
    }


def pending_fixture() -> dict[str, Any]:
    return {
        "id": "DEC-01",
        "candidate_ids": ["CAND-002"],
        "kind": "搁置",
        "title": "是否继续",
        "context": "两个方向都成立，需要用户决定。",
        "positions": [
            {"id": "DEC-01-A", "label": "继续", "stance": "继续处理", "reason": "收益明确"},
            {"id": "DEC-01-B", "label": "搁置", "stance": "暂不处理", "reason": "成本更高"},
        ],
        "current_state": "保持搁置",
        "origin_issues": [1],
        "evidence": [],
    }


class RenderReportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / ".afk" / "batch-one").mkdir(parents=True)
        ledger = [
            {
                "ts": "2026-08-11T01:00:00Z",
                "kind": "decision",
                "issue": None,
                "pr": None,
                "scope": {"issues": [1]},
                "detail": "开始",
            },
            {
                "ts": "2026-08-11T02:00:00Z",
                "kind": "closed",
                "issue": None,
                "pr": None,
                "scope": None,
                "detail": "完成",
            },
        ]
        (self.root / ".afk" / "batch-one.jsonl").write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in ledger), encoding="utf-8"
        )
        (self.root / ".afk" / "batch-one" / "handoff-1.md").write_text(
            "### 审查循环\n\n`<script>alert(1)</script>`\n", encoding="utf-8"
        )
        self.analysis = self.root / "analysis.json"
        self.analysis.write_text(json.dumps(analysis_fixture(), ensure_ascii=False), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_renders_closed_segment_and_escapes_embedded_json(self) -> None:
        report = MODULE.build_report_data(self.root, "batch-one", self.analysis)
        output = self.root / "report.html"
        MODULE.render_report(report, output)
        html = output.read_text(encoding="utf-8")
        self.assertIn("AFK 复盘", html)
        self.assertIn("\\u003cscript\\u003e", html)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("handoff-1.md", html)

    def test_rejects_unclosed_batch(self) -> None:
        ledger = self.root / ".afk" / "batch-one.jsonl"
        ledger.write_text(
            json.dumps({"ts": "2026-08-11T01:00:00Z", "kind": "decision", "detail": "开始"}) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(MODULE.ReportError, "not closed"):
            MODULE.build_report_data(self.root, "batch-one", self.analysis)

    def test_rejects_unknown_headline_id(self) -> None:
        data = analysis_fixture()
        data["batch"]["headline_ids"] = ["FU-99"]
        with self.assertRaisesRegex(MODULE.ReportError, "unknown id"):
            MODULE.validate_analysis(data)

    def test_rejects_nonnumeric_pushback_pr(self) -> None:
        data = analysis_fixture()
        data["pushbacks"] = [
            {
                "pr": "<img src=x onerror=alert(1)>",
                "reviewer": "Codex",
                "topic": "validation",
                "ruling": "rejected",
                "reason": "invalid input",
            }
        ]
        with self.assertRaisesRegex(MODULE.ReportError, "positive integer or null"):
            MODULE.validate_analysis(data)

    def test_rejects_nonnumeric_issue_pr(self) -> None:
        data = analysis_fixture()
        data["issues"][0]["pr"] = "two"
        with self.assertRaisesRegex(MODULE.ReportError, "positive integer or null"):
            MODULE.validate_analysis(data)

    def test_rejects_invalid_candidate_id(self) -> None:
        data = analysis_fixture()
        data["followups"][0]["candidate_ids"] = ["candidate-1"]
        with self.assertRaisesRegex(MODULE.ReportError, "must match"):
            MODULE.validate_analysis(data)

    def test_rejects_duplicate_candidate_id_within_item(self) -> None:
        data = analysis_fixture()
        data["followups"][0]["candidate_ids"] = ["CAND-001", "CAND-001"]
        with self.assertRaisesRegex(MODULE.ReportError, "duplicate candidate id"):
            MODULE.validate_analysis(data)

    def test_accepts_decision_options(self) -> None:
        data = analysis_fixture()
        data["pending"] = [pending_fixture()]
        MODULE.validate_analysis(data)

    def test_rejects_decision_with_one_option(self) -> None:
        data = analysis_fixture()
        pending = pending_fixture()
        pending["positions"] = pending["positions"][:1]
        data["pending"] = [pending]
        with self.assertRaisesRegex(MODULE.ReportError, "at least two options"):
            MODULE.validate_analysis(data)

    def test_rejects_option_id_from_another_decision(self) -> None:
        data = analysis_fixture()
        pending = pending_fixture()
        pending["positions"][0]["id"] = "DEC-02-A"
        data["pending"] = [pending]
        with self.assertRaisesRegex(MODULE.ReportError, "DEC-01-<OPTION>"):
            MODULE.validate_analysis(data)

    def test_rejects_duplicate_decision_option_id(self) -> None:
        data = analysis_fixture()
        pending = pending_fixture()
        pending["positions"][1]["id"] = "DEC-01-A"
        data["pending"] = [pending]
        with self.assertRaisesRegex(MODULE.ReportError, "duplicate decision option id"):
            MODULE.validate_analysis(data)

    def test_uses_only_latest_closed_segment(self) -> None:
        ledger = self.root / ".afk" / "batch-one.jsonl"
        events = [
            {"ts": "2026-08-10T01:00:00Z", "kind": "decision", "detail": "旧批次"},
            {"ts": "2026-08-10T02:00:00Z", "kind": "closed", "detail": "旧批次结束"},
            {"ts": "2026-08-11T01:00:00Z", "kind": "decision", "detail": "当前批次"},
            {"ts": "2026-08-11T02:00:00Z", "kind": "closed", "detail": "当前批次结束"},
        ]
        ledger.write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in events),
            encoding="utf-8",
        )
        report = MODULE.build_report_data(self.root, "batch-one", self.analysis)
        snapshot = report["operational"]["snapshots"][0]["content"]
        self.assertIn("当前批次", snapshot)
        self.assertNotIn("旧批次", snapshot)
        self.assertEqual(report["operational"]["started_at"], "2026-08-11T01:00:00Z")


if __name__ == "__main__":
    unittest.main()
