#!/usr/bin/env python3
"""Render one completed AFK batch and its analysis as a standalone HTML report."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Never

SKILL_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = SKILL_ROOT / "assets" / "report.html"
BATCH_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
CANDIDATE_ID_RE = re.compile(r"^CAND-[0-9]{3,}$")
POSITION_SUFFIX_RE = re.compile(r"^[A-Z][A-Z0-9]*$")
REPORT_ID_PATTERNS = {
    "FU-": re.compile(r"^FU-[0-9]{2,}$"),
    "DEC-": re.compile(r"^DEC-[0-9]{2,}$"),
    "CTX-": re.compile(r"^CTX-[0-9]{2,}$"),
    "ADR-": re.compile(r"^ADR-[0-9]{2,}$"),
    "INST-": re.compile(r"^INST-[0-9]{2,}$"),
}
FOLLOWUP_PRIORITIES = {"P0", "P1", "P2", "无需处理"}
ISSUE_STATES = {"merged", "shelved", "not_started", "done"}
STAGE_STATES = {"done", "halted", "skipped"}
GATE_VERDICTS = {
    "CONTEXT": {"应更新", "无需更新"},
    "ADR": {"应记录", "无需记录"},
    "INST": {"应更新", "无需更新"},
}


class ReportError(ValueError):
    """Input cannot produce a trustworthy report."""


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


def expect_string(value: Any, path: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        fail(f"{path} must be a non-empty string")
    return value


def require(mapping: dict[str, Any], key: str, path: str) -> Any:
    if key not in mapping:
        fail(f"{path}.{key} is required")
    return mapping[key]


def validate_optional_positive_int(mapping: dict[str, Any], key: str, path: str) -> None:
    if key not in mapping or mapping[key] is None:
        return
    value = mapping[key]
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        fail(f"{path}.{key} must be a positive integer or null")


def validate_evidence(value: Any, path: str) -> None:
    for index, item_value in enumerate(expect_list(value, path)):
        item = expect_mapping(item_value, f"{path}[{index}]")
        for key in ("kind", "ref", "text"):
            expect_string(require(item, key, f"{path}[{index}]"), f"{path}[{index}].{key}")


def validate_candidate_ids(value: Any, path: str) -> None:
    candidate_ids = expect_list(value, path)
    if not candidate_ids:
        fail(f"{path} must not be empty")
    seen: set[str] = set()
    for index, raw_id in enumerate(candidate_ids):
        candidate_id = expect_string(raw_id, f"{path}[{index}]")
        if not CANDIDATE_ID_RE.fullmatch(candidate_id):
            fail(f"{path}[{index}] must match {CANDIDATE_ID_RE.pattern}")
        if candidate_id in seen:
            fail(f"duplicate candidate id in {path}: {candidate_id}")
        seen.add(candidate_id)


def validate_analysis(value: Any) -> dict[str, Any]:
    analysis = expect_mapping(value, "analysis")
    if analysis.get("version") != 1:
        fail("analysis.version must be 1")

    batch = expect_mapping(require(analysis, "batch", "analysis"), "analysis.batch")
    expect_string(require(batch, "title", "analysis.batch"), "analysis.batch.title")
    expect_string(require(batch, "summary", "analysis.batch"), "analysis.batch.summary")
    headline_ids = expect_list(require(batch, "headline_ids", "analysis.batch"), "analysis.batch.headline_ids")
    for index, item in enumerate(headline_ids):
        expect_string(item, f"analysis.batch.headline_ids[{index}]")
    counts = expect_mapping(require(batch, "counts", "analysis.batch"), "analysis.batch.counts")
    for key in ("merged", "shelved", "not_started", "cleanup_filed"):
        number = require(counts, key, "analysis.batch.counts")
        if not isinstance(number, int) or isinstance(number, bool) or number < 0:
            fail(f"analysis.batch.counts.{key} must be a non-negative integer")

    issues = expect_list(require(analysis, "issues", "analysis"), "analysis.issues")
    for index, item_value in enumerate(issues):
        path = f"analysis.issues[{index}]"
        item = expect_mapping(item_value, path)
        number = require(item, "number", path)
        if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
            fail(f"{path}.number must be a positive integer")
        validate_optional_positive_int(item, "pr", path)
        expect_string(require(item, "title", path), f"{path}.title")
        state = expect_string(require(item, "state", path), f"{path}.state")
        if state not in ISSUE_STATES:
            fail(f"{path}.state must be one of {sorted(ISSUE_STATES)}")
        for stage_index, stage_value in enumerate(expect_list(require(item, "stages", path), f"{path}.stages")):
            stage_path = f"{path}.stages[{stage_index}]"
            stage = expect_mapping(stage_value, stage_path)
            expect_string(require(stage, "name", stage_path), f"{stage_path}.name")
            status = expect_string(require(stage, "status", stage_path), f"{stage_path}.status")
            if status not in STAGE_STATES:
                fail(f"{stage_path}.status must be one of {sorted(STAGE_STATES)}")

    ids: set[str] = set()
    selectable_ids: set[str] = set()

    def register_id(raw_id: Any, path: str, prefix: str) -> str:
        item_id = expect_string(raw_id, path)
        pattern = REPORT_ID_PATTERNS[prefix]
        if not pattern.fullmatch(item_id):
            fail(f"{path} must match {pattern.pattern}")
        if item_id in ids:
            fail(f"duplicate report id: {item_id}")
        ids.add(item_id)
        return item_id

    followups = expect_list(require(analysis, "followups", "analysis"), "analysis.followups")
    for index, item_value in enumerate(followups):
        path = f"analysis.followups[{index}]"
        item = expect_mapping(item_value, path)
        item_id = register_id(require(item, "id", path), f"{path}.id", "FU-")
        selectable_ids.add(item_id)
        validate_candidate_ids(require(item, "candidate_ids", path), f"{path}.candidate_ids")
        priority = expect_string(require(item, "priority", path), f"{path}.priority")
        if priority not in FOLLOWUP_PRIORITIES:
            fail(f"{path}.priority must be one of {sorted(FOLLOWUP_PRIORITIES)}")
        for key in ("title", "summary", "status", "conclusion"):
            expect_string(require(item, key, path), f"{path}.{key}")
        origin = expect_mapping(require(item, "origin", path), f"{path}.origin")
        expect_list(require(origin, "issues", f"{path}.origin"), f"{path}.origin.issues")
        expect_list(require(origin, "prs", f"{path}.origin"), f"{path}.origin.prs")
        expect_string(require(origin, "where", f"{path}.origin"), f"{path}.origin.where")
        evaluations = expect_mapping(require(item, "evaluations", path), f"{path}.evaluations")
        for axis in ("architecture", "product_user", "knowledge"):
            evaluation = expect_mapping(require(evaluations, axis, f"{path}.evaluations"), f"{path}.evaluations.{axis}")
            expect_string(
                require(evaluation, "verdict", f"{path}.evaluations.{axis}"), f"{path}.evaluations.{axis}.verdict"
            )
            expect_string(
                require(evaluation, "summary", f"{path}.evaluations.{axis}"), f"{path}.evaluations.{axis}.summary"
            )
        validate_evidence(require(item, "evidence", path), f"{path}.evidence")

    gates = expect_list(require(analysis, "gates", "analysis"), "analysis.gates")
    seen_gates: set[str] = set()
    for gate_index, gate_value in enumerate(gates):
        gate_path = f"analysis.gates[{gate_index}]"
        gate = expect_mapping(gate_value, gate_path)
        key = expect_string(require(gate, "key", gate_path), f"{gate_path}.key")
        if key not in GATE_VERDICTS or key in seen_gates:
            fail(f"{gate_path}.key must be a unique CONTEXT, ADR, or INST")
        seen_gates.add(key)
        expect_string(require(gate, "label", gate_path), f"{gate_path}.label")
        expect_string(require(gate, "rule", gate_path), f"{gate_path}.rule")
        expect_string(require(gate, "empty_note", gate_path), f"{gate_path}.empty_note", allow_empty=True)
        for item_index, item_value in enumerate(expect_list(require(gate, "items", gate_path), f"{gate_path}.items")):
            item_path = f"{gate_path}.items[{item_index}]"
            item = expect_mapping(item_value, item_path)
            item_id = register_id(
                require(item, "id", item_path),
                f"{item_path}.id",
                {"CONTEXT": "CTX-", "ADR": "ADR-", "INST": "INST-"}[key],
            )
            selectable_ids.add(item_id)
            validate_candidate_ids(require(item, "candidate_ids", item_path), f"{item_path}.candidate_ids")
            verdict = expect_string(require(item, "verdict", item_path), f"{item_path}.verdict")
            if verdict not in GATE_VERDICTS[key]:
                fail(f"{item_path}.verdict is invalid for {key}")
            for text_key in ("title", "summary"):
                expect_string(require(item, text_key, item_path), f"{item_path}.{text_key}")
            for check_index, check_value in enumerate(
                expect_list(require(item, "checks", item_path), f"{item_path}.checks")
            ):
                check_path = f"{item_path}.checks[{check_index}]"
                check = expect_mapping(check_value, check_path)
                expect_string(require(check, "label", check_path), f"{check_path}.label")
                if not isinstance(require(check, "passed", check_path), bool):
                    fail(f"{check_path}.passed must be boolean")
                expect_string(require(check, "reason", check_path), f"{check_path}.reason")
            validate_evidence(require(item, "evidence", item_path), f"{item_path}.evidence")
    if seen_gates != set(GATE_VERDICTS):
        fail("analysis.gates must contain CONTEXT, ADR, and INST exactly once")

    pending = expect_list(require(analysis, "pending", "analysis"), "analysis.pending")
    position_ids: set[str] = set()
    for index, item_value in enumerate(pending):
        path = f"analysis.pending[{index}]"
        item = expect_mapping(item_value, path)
        item_id = register_id(require(item, "id", path), f"{path}.id", "DEC-")
        validate_candidate_ids(require(item, "candidate_ids", path), f"{path}.candidate_ids")
        for key in ("kind", "title", "context", "current_state"):
            expect_string(require(item, key, path), f"{path}.{key}")
        positions = expect_list(require(item, "positions", path), f"{path}.positions")
        if len(positions) < 2:
            fail(f"{path}.positions must contain at least two options")
        for position_index, position_value in enumerate(positions):
            position_path = f"{path}.positions[{position_index}]"
            position = expect_mapping(position_value, position_path)
            position_id = expect_string(require(position, "id", position_path), f"{position_path}.id")
            prefix = f"{item_id}-"
            suffix = position_id.removeprefix(prefix)
            if not position_id.startswith(prefix) or not POSITION_SUFFIX_RE.fullmatch(suffix):
                fail(f"{position_path}.id must use {item_id}-<OPTION>")
            if position_id in position_ids:
                fail(f"duplicate decision option id: {position_id}")
            position_ids.add(position_id)
            for key in ("label", "stance", "reason"):
                expect_string(require(position, key, position_path), f"{position_path}.{key}")
        validate_evidence(require(item, "evidence", path), f"{path}.evidence")

    for index, item_value in enumerate(expect_list(require(analysis, "pushbacks", "analysis"), "analysis.pushbacks")):
        path = f"analysis.pushbacks[{index}]"
        item = expect_mapping(item_value, path)
        validate_optional_positive_int(item, "pr", path)
        for key in ("reviewer", "topic", "ruling", "reason"):
            expect_string(require(item, key, path), f"{path}.{key}")

    for index, item_id in enumerate(headline_ids):
        if item_id not in ids:
            fail(f"analysis.batch.headline_ids[{index}] refers to unknown id {item_id}")
    for index, item_id in enumerate(
        expect_list(require(analysis, "reply_defaults", "analysis"), "analysis.reply_defaults")
    ):
        if item_id not in selectable_ids:
            fail(f"analysis.reply_defaults[{index}] refers to a non-selectable id {item_id}")

    return analysis


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"analysis file not found: {path}")
    except json.JSONDecodeError as exc:
        fail(f"analysis is not valid JSON: {exc}")


def load_ledger_segment(path: Path) -> tuple[list[dict[str, Any]], str]:
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        fail(f"ledger not found: {path}")
    if not raw_lines:
        fail(f"ledger is empty: {path}")
    events: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(raw_lines, start=1):
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            fail(f"ledger line {line_number} is invalid JSON: {exc}")
        if not isinstance(event, dict):
            fail(f"ledger line {line_number} must be an object")
        event["_line"] = line_number
        event["_raw"] = raw_line
        events.append(event)
    if events[-1].get("kind") != "closed":
        fail("the latest ledger event is not closed")
    previous_closed = max(
        (index for index, event in enumerate(events[:-1]) if event.get("kind") == "closed"), default=-1
    )
    segment = events[previous_closed + 1 :]
    segment_text = "\n".join(raw_lines[previous_closed + 1 :]) + "\n"
    return segment, segment_text


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def relative_label(path: Path, repo_root: Path) -> str:
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return str(path)


def build_report_data(repo_root: Path, batch_id: str, analysis_path: Path) -> dict[str, Any]:
    if not BATCH_ID_RE.fullmatch(batch_id):
        fail(f"batch-id must match {BATCH_ID_RE.pattern}")
    analysis = validate_analysis(load_json(analysis_path))
    ledger_path = repo_root / ".afk" / f"{batch_id}.jsonl"
    handoff_dir = repo_root / ".afk" / batch_id
    events, ledger_text = load_ledger_segment(ledger_path)
    handoff_paths = sorted(handoff_dir.glob("handoff-*.md")) if handoff_dir.is_dir() else []
    if not handoff_paths:
        fail(f"no handoff files found in {handoff_dir}")

    snapshots = [
        {
            "kind": "ledger",
            "path": relative_label(ledger_path, repo_root),
            "sha256": sha256_text(ledger_text),
            "content": ledger_text,
        }
    ]
    for path in handoff_paths:
        content = path.read_text(encoding="utf-8")
        snapshots.append(
            {
                "kind": "handoff",
                "path": relative_label(path, repo_root),
                "sha256": sha256_text(content),
                "content": content,
            }
        )

    report = deepcopy(analysis)
    report["operational"] = {
        "batch_id": batch_id,
        "ledger_path": relative_label(ledger_path, repo_root),
        "handoff_path": relative_label(handoff_dir, repo_root),
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
    html = template.replace(marker, payload)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(html)
        os.replace(temp_path, output_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


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
