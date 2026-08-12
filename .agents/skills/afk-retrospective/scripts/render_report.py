#!/usr/bin/env python3
"""Render one completed AFK batch and its analysis as a standalone HTML report."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Never

SKILL_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = SKILL_ROOT / "assets" / "report.html"
BATCH_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
REPORT_ID_RE = re.compile(r"^\S+$")
KNOWLEDGE_KEYS = ("CONTEXT", "ADR", "INST")
RESERVED_REPORT_IDS = {"BATCH-OVERVIEW", *(f"{key}-EMPTY" for key in KNOWLEDGE_KEYS)}
ISSUE_STATES = ("merged", "shelved", "not_started", "done")
RECOMMENDATIONS = ("强烈建议", "值得探索", "推测性", "无需处理")
KNOWLEDGE_ACTIONS = {
    "CONTEXT": ("create", "revise", "retire", "none"),
    "ADR": ("create", "revise", "retire", "supersede", "none"),
    "INST": ("create", "revise", "retire", "none"),
}
TARGET_REF_ACTIONS = ("revise", "retire", "supersede")


class ReportError(ValueError):
    """Input cannot produce a report."""


def fail(message: str) -> Never:
    raise ReportError(message)


def expect_mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"{path} must be an object")
    return value


def expect_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        fail(f"{path} must be an array")
    return value


def expect_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        fail(f"{path} must be a non-empty string")
    return value


def require(mapping: dict[str, Any], key: str, path: str) -> Any:
    if key not in mapping:
        fail(f"{path}.{key} is required")
    return mapping[key]


def validate_analysis(value: Any) -> dict[str, Any]:
    analysis = expect_mapping(value, "analysis")
    if analysis.get("version") != 2:
        fail("analysis.version must be 2")

    batch = expect_mapping(require(analysis, "batch", "analysis"), "analysis.batch")
    expect_string(require(batch, "title", "analysis.batch"), "analysis.batch.title")
    issues = expect_list(require(analysis, "issues", "analysis"), "analysis.issues")
    followups = expect_list(require(analysis, "followups", "analysis"), "analysis.followups")
    knowledge_reviewed = expect_list(require(analysis, "knowledge_reviewed", "analysis"), "analysis.knowledge_reviewed")
    reviewed_targets = [
        expect_string(target, f"analysis.knowledge_reviewed[{index}]")
        for index, target in enumerate(knowledge_reviewed)
    ]
    if len(reviewed_targets) != len(set(reviewed_targets)) or set(reviewed_targets) != set(KNOWLEDGE_KEYS):
        fail(f"analysis.knowledge_reviewed must contain each target exactly once: {', '.join(KNOWLEDGE_KEYS)}")
    knowledge = expect_list(require(analysis, "knowledge", "analysis"), "analysis.knowledge")
    pending = expect_list(require(analysis, "pending", "analysis"), "analysis.pending")

    ids: set[str] = set()
    actionable_ids: set[str] = set()

    def validate_sources(item: dict[str, Any], path: str) -> None:
        sources = expect_list(require(item, "sources", path), f"{path}.sources")
        if not sources:
            fail(f"{path}.sources must not be empty")
        for index, source in enumerate(sources):
            expect_string(source, f"{path}.sources[{index}]")

    def register(raw_id: Any, path: str) -> None:
        if not isinstance(raw_id, str) or not REPORT_ID_RE.fullmatch(raw_id):
            fail(f"{path} must be a non-empty token")
        if raw_id in RESERVED_REPORT_IDS:
            fail(f"report id is reserved by the renderer: {raw_id}")
        if raw_id in ids:
            fail(f"duplicate report id: {raw_id}")
        ids.add(raw_id)

    def validate_recommendation(item: dict[str, Any], path: str) -> str:
        recommendation = expect_string(require(item, "recommendation", path), f"{path}.recommendation")
        if recommendation not in RECOMMENDATIONS:
            fail(f"{path}.recommendation must be one of: {', '.join(RECOMMENDATIONS)}")
        return recommendation

    for index, value in enumerate(issues):
        path = f"analysis.issues[{index}]"
        item = expect_mapping(value, path)
        require(item, "number", path)
        expect_string(require(item, "title", path), f"{path}.title")
        state = expect_string(require(item, "state", path), f"{path}.state")
        if state not in ISSUE_STATES:
            fail(f"{path}.state must be one of: {', '.join(ISSUE_STATES)}")

    for index, value in enumerate(followups):
        path = f"analysis.followups[{index}]"
        item = expect_mapping(value, path)
        report_id = require(item, "id", path)
        register(report_id, f"{path}.id")
        recommendation = validate_recommendation(item, path)
        if recommendation != "无需处理":
            actionable_ids.add(report_id)
        expect_string(require(item, "title", path), f"{path}.title")
        expect_string(require(item, "body_markdown", path), f"{path}.body_markdown")
        validate_sources(item, path)

    for index, value in enumerate(knowledge):
        path = f"analysis.knowledge[{index}]"
        item = expect_mapping(value, path)
        report_id = require(item, "id", path)
        register(report_id, f"{path}.id")
        target = expect_string(require(item, "target", path), f"{path}.target")
        if target not in KNOWLEDGE_ACTIONS:
            fail(f"{path}.target must be one of: {', '.join(KNOWLEDGE_KEYS)}")
        action = expect_string(require(item, "action", path), f"{path}.action")
        if action not in KNOWLEDGE_ACTIONS[target]:
            fail(f"{path}.action must be one of for {target}: {', '.join(KNOWLEDGE_ACTIONS[target])}")
        recommendation = validate_recommendation(item, path)
        if (action == "none") != (recommendation == "无需处理"):
            fail(f"{path}.action none must pair with recommendation 无需处理")
        if action in TARGET_REF_ACTIONS:
            expect_string(require(item, "target_ref", path), f"{path}.target_ref")
        elif "target_ref" in item:
            fail(f"{path}.target_ref must be omitted for action {action}")
        if action != "none":
            actionable_ids.add(report_id)
        expect_string(require(item, "title", path), f"{path}.title")
        expect_string(require(item, "body_markdown", path), f"{path}.body_markdown")
        validate_sources(item, path)

    for index, value in enumerate(pending):
        path = f"analysis.pending[{index}]"
        item = expect_mapping(value, path)
        decision_id = expect_string(require(item, "id", path), f"{path}.id")
        register(decision_id, f"{path}.id")
        for field in ("kind", "title", "body_markdown", "current_state"):
            expect_string(require(item, field, path), f"{path}.{field}")
        validate_sources(item, path)
        positions = expect_list(require(item, "positions", path), f"{path}.positions")
        if len(positions) < 2:
            fail(f"{path}.positions must contain at least two options")
        for option_index, option_value in enumerate(positions):
            option_path = f"{path}.positions[{option_index}]"
            option = expect_mapping(option_value, option_path)
            option_id = expect_string(require(option, "id", option_path), f"{option_path}.id")
            if not re.fullmatch(rf"{re.escape(decision_id)}-[A-Z]+", option_id):
                fail(f"{option_path}.id must use decision prefix {decision_id}- and an uppercase suffix")
            register(option_id, f"{option_path}.id")
            for field in ("label", "stance", "reason"):
                expect_string(require(option, field, option_path), f"{option_path}.{field}")

    top_value = batch.get("top_recommendation")
    if actionable_ids:
        top = expect_mapping(top_value, "analysis.batch.top_recommendation")
        top_id = expect_string(
            require(top, "id", "analysis.batch.top_recommendation"), "analysis.batch.top_recommendation.id"
        )
        expect_string(
            require(top, "reason", "analysis.batch.top_recommendation"),
            "analysis.batch.top_recommendation.reason",
        )
        if top_id not in actionable_ids:
            fail("analysis.batch.top_recommendation.id must reference an actionable follow-up or knowledge item")
    elif top_value is not None:
        fail("analysis.batch.top_recommendation must be omitted when there are no actionable items")

    return analysis


def validate_source_references(analysis: dict[str, Any], available: set[str]) -> None:
    items = list(analysis["followups"]) + list(analysis["knowledge"]) + list(analysis["pending"])
    event_ids = {source for source in available if source.startswith("EV-")}
    report_ids = [item["id"] for item in items]
    report_ids.extend(option["id"] for item in analysis["pending"] for option in item["positions"])
    for report_id in report_ids:
        if report_id in event_ids:
            fail(f"report id collides with a ledger event id: {report_id}")
    for item in items:
        for source in item.get("sources", []):
            if source not in available:
                fail(f"source does not resolve to this batch: {source}")


def load_json(path: Path) -> Any:
    def reject_constant(value: str) -> Never:
        fail(f"analysis contains a non-finite JSON constant: {value}")

    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant)
    except FileNotFoundError:
        fail(f"analysis file not found: {path}")
    except json.JSONDecodeError as exc:
        fail(f"analysis is not valid JSON: {exc}")


def load_ledger(path: Path) -> tuple[list[dict[str, Any]], str]:
    try:
        raw_text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        fail(f"ledger not found: {path}")
    raw_lines = raw_text.splitlines()
    if not raw_lines:
        fail(f"ledger is empty: {path}")

    events: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(raw_lines, start=1):
        try:
            event = expect_mapping(json.loads(raw_line), f"ledger line {line_number}")
        except json.JSONDecodeError as exc:
            fail(f"ledger line {line_number} is invalid JSON: {exc}")
        event["_line"] = line_number
        event["_raw"] = raw_line
        event["kind"] = event.get("kind") or "decision"
        expect_string(event["kind"], f"ledger line {line_number}.kind")
        events.append(event)
    if events[-1].get("kind") != "closed":
        fail("the latest ledger event is not closed")
    return events, raw_text if raw_text.endswith("\n") else raw_text + "\n"


def build_report_data(repo_root: Path, batch_id: str, analysis_path: Path) -> dict[str, Any]:
    if not BATCH_ID_RE.fullmatch(batch_id):
        fail(f"batch-id must match {BATCH_ID_RE.pattern}")

    analysis = validate_analysis(load_json(analysis_path))
    ledger_path = repo_root / ".afk" / f"{batch_id}.jsonl"
    handoff_dir = repo_root / ".afk" / batch_id
    events, ledger_text = load_ledger(ledger_path)
    handoff_paths = sorted(handoff_dir.glob("handoff-*.md")) if handoff_dir.is_dir() else []
    if not handoff_paths:
        fail(f"no handoff files found in {handoff_dir}")

    snapshots = [
        {
            "kind": "ledger",
            "name": ledger_path.name,
            "path": ledger_path.relative_to(repo_root).as_posix(),
            "content": ledger_text,
        }
    ]
    snapshots.extend(
        {
            "kind": "handoff",
            "name": path.name,
            "path": path.relative_to(repo_root).as_posix(),
            "content": path.read_text(encoding="utf-8"),
        }
        for path in handoff_paths
    )
    available_sources = {f"EV-{event['_line']:04d}" for event in events}
    handoff_sources = [source for source in snapshots if source["kind"] == "handoff"]
    available_sources.update(source["name"] for source in handoff_sources)
    available_sources.update(source["path"] for source in handoff_sources)
    validate_source_references(analysis, available_sources)

    report = dict(analysis)
    report["operational"] = {
        "batch_id": batch_id,
        "started_at": events[0].get("ts", ""),
        "closed_at": events[-1].get("ts", ""),
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "journey": [
            {
                "id": f"EV-{event['_line']:04d}",
                "ts": event.get("ts", ""),
                "kind": event.get("kind", "decision"),
                "issue": event.get("issue"),
                "pr": event.get("pr"),
                "detail": event.get("detail", ""),
                "line": event["_line"],
                "raw": event["_raw"],
            }
            for event in events
        ],
        "snapshots": snapshots,
    }
    return report


def render_report(report: dict[str, Any], output_path: Path) -> None:
    try:
        template = TEMPLATE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        fail(f"report template not found: {TEMPLATE_PATH}")
    marker = "__AFK_REPORT_DATA__"
    if template.count(marker) != 1:
        fail(f"report template must contain exactly one {marker} marker")
    payload = json.dumps(report, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(template.replace(marker, payload), encoding="utf-8", newline="\n")


def default_output(repo_root: Path, batch_id: str) -> Path:
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    return repo_root / ".afk" / "reports" / batch_id / f"{timestamp}.html"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        repo_root = args.repo_root.resolve()
        report = build_report_data(repo_root, args.batch_id, args.analysis.resolve())
        output_path = args.output.resolve() if args.output else default_output(repo_root, args.batch_id)
        render_report(report, output_path)
    except ReportError as exc:
        print(f"AFK_RETROSPECTIVE_ERROR: {exc}", file=sys.stderr)
        return 2
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
