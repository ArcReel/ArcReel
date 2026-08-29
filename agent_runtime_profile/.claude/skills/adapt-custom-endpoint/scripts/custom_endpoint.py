#!/usr/bin/env python3
"""Thin stdlib CLI for ArcReel custom endpoint HTTP APIs."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def _json_file(path: str) -> object:
    with Path(path).open(encoding="utf-8") as source:
        return json.load(source)


def _request(method: str, path: str, payload: object | None = None) -> object:
    base = (os.environ.get("ARCREEL_API_BASE") or "http://127.0.0.1:1241/api/v1").rstrip("/")
    token = os.environ.get("ARCREEL_API_TOKEN", "").strip()
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urlopen(Request(f"{base}{path}", data=data, headers=headers, method=method), timeout=30) as response:
            raw = response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"ArcReel API returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise SystemExit(f"ArcReel API request failed: {exc.reason}") from exc
    if not raw:
        return {"status": "ok"}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        preview = raw[:200].decode("utf-8", errors="replace")
        raise SystemExit(f"ArcReel API returned non-JSON response: {preview}") from exc


def _test_payload(args: argparse.Namespace) -> dict[str, object]:
    payload = {
        "definition": _json_file(args.definition),
        "parameters": _json_file(args.parameters),
    }
    if args.credentials:
        payload["credentials"] = _json_file(args.credentials)
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate")
    validate.add_argument("definition")
    validate.add_argument("--exclude-id", type=int)

    check = commands.add_parser("check-response")
    check.add_argument("definition")
    check.add_argument("--stage", choices=("submit", "poll", "result"), required=True)
    check.add_argument("--response", required=True)

    for name in ("preview-request", "trial-run"):
        command = commands.add_parser(name)
        command.add_argument("definition")
        command.add_argument("--parameters", required=True)
        command.add_argument("--credentials")
        if name == "trial-run":
            command.add_argument("--confirm-cost", action="store_true")

    status = commands.add_parser("trial-status")
    status.add_argument("run_id")

    save = commands.add_parser("save")
    save.add_argument("definition")
    save.add_argument("--endpoint-id", type=int)
    save.add_argument("--confirm-overwrite", action="store_true")
    return parser


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    if args.command == "validate":
        query = f"?{urlencode({'exclude_id': args.exclude_id})}" if args.exclude_id is not None else ""
        result = _request("POST", f"/custom-endpoints/validate{query}", _json_file(args.definition))
    elif args.command == "check-response":
        result = _request(
            "POST",
            "/custom-endpoints/check-response",
            {
                "definition": _json_file(args.definition),
                "stage": args.stage,
                "response_body": _json_file(args.response),
            },
        )
    elif args.command == "preview-request":
        result = _request("POST", "/custom-endpoints/preview-request", _test_payload(args))
    elif args.command == "trial-run":
        if not args.confirm_cost:
            parser.error("trial-run sends a billable provider request; ask the user, then pass --confirm-cost")
        result = _request("POST", "/custom-endpoints/trial-runs", _test_payload(args))
    elif args.command == "trial-status":
        result = _request("GET", f"/custom-endpoints/trial-runs/{args.run_id}")
    else:
        if args.endpoint_id is not None and not args.confirm_overwrite:
            parser.error("overwriting an endpoint requires user approval and --confirm-overwrite")
        path = f"/custom-endpoints/{args.endpoint_id}" if args.endpoint_id is not None else "/custom-endpoints"
        result = _request("PUT" if args.endpoint_id is not None else "POST", path, _json_file(args.definition))
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
