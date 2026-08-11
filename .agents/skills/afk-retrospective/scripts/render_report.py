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
RESERVED_REPORT_IDS = {f"{key}-EMPTY" for key in KNOWLEDGE_KEYS}


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
    if analysis.get("version") != 1:
        fail("analysis.version must be 1")

    expect_mapping(require(analysis, "batch", "analysis"), "analysis.batch")
    issues = expect_list(require(analysis, "issues", "analysis"), "analysis.issues")
    followups = expect_list(require(analysis, "followups", "analysis"), "analysis.followups")
    knowledge = expect_mapping(require(analysis, "knowledge", "analysis"), "analysis.knowledge")
    pending = expect_list(require(analysis, "pending", "analysis"), "analysis.pending")

    ids: set[str] = set()

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

    for index, value in enumerate(issues):
        expect_mapping(value, f"analysis.issues[{index}]")

    for index, value in enumerate(followups):
        path = f"analysis.followups[{index}]"
        item = expect_mapping(value, path)
        register(require(item, "id", path), f"{path}.id")
        expect_string(require(item, "priority", path), f"{path}.priority")
        evaluations = expect_mapping(require(item, "evaluations", path), f"{path}.evaluations")
        for axis in ("architecture", "product_user", "knowledge"):
            expect_mapping(require(evaluations, axis, f"{path}.evaluations"), f"{path}.evaluations.{axis}")
        validate_sources(item, path)

    for key in KNOWLEDGE_KEYS:
        items = expect_list(require(knowledge, key, "analysis.knowledge"), f"analysis.knowledge.{key}")
        for index, value in enumerate(items):
            item = expect_mapping(value, f"analysis.knowledge.{key}[{index}]")
            register(require(item, "id", f"analysis.knowledge.{key}[{index}]"), f"analysis.knowledge.{key}[{index}].id")
            expect_string(
                require(item, "verdict", f"analysis.knowledge.{key}[{index}]"),
                f"analysis.knowledge.{key}[{index}].verdict",
            )
            validate_sources(item, f"analysis.knowledge.{key}[{index}]")

    for index, value in enumerate(pending):
        path = f"analysis.pending[{index}]"
        item = expect_mapping(value, path)
        register(require(item, "id", path), f"{path}.id")
        validate_sources(item, path)
        positions = expect_list(require(item, "positions", path), f"{path}.positions")
        if len(positions) < 2:
            fail(f"{path}.positions must contain at least two options")
        for option_index, option_value in enumerate(positions):
            option_path = f"{path}.positions[{option_index}]"
            option = expect_mapping(option_value, option_path)
            register(require(option, "id", option_path), f"{option_path}.id")
            for field in ("label", "stance", "reason"):
                expect_string(require(option, field, option_path), f"{option_path}.{field}")

    return analysis


def validate_source_references(analysis: dict[str, Any], available: set[str]) -> None:
    items = list(analysis["followups"]) + list(analysis["pending"])
    for key in KNOWLEDGE_KEYS:
        items.extend(analysis["knowledge"][key])
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
    available_sources.update(source["name"] for source in snapshots)
    available_sources.update(source["path"] for source in snapshots)
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
