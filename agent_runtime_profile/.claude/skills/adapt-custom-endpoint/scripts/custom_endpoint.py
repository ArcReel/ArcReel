#!/usr/bin/env python3
"""Thin stdlib CLI for ArcReel custom endpoint HTTP APIs."""

from __future__ import annotations

import argparse
import ipaddress
import json
import mimetypes
import os
import secrets
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


def _validated_url(value: str, source: str) -> str:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise SystemExit(f"Invalid ArcReel URL in {source}: {exc}") from exc
    if not host or parsed.scheme not in {"http", "https"}:
        raise SystemExit(f"ArcReel URL in {source} must include an HTTP(S) scheme and host")
    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    if port == 0:
        raise SystemExit(f"ArcReel URL in {source} must use a valid port")
    if parsed.scheme != "https" and not loopback:
        raise SystemExit(f"ArcReel URL in {source} must use HTTPS unless the host is loopback")
    return value.rstrip("/")


def _connection() -> tuple[str, str]:
    if base := os.environ.get("ARCREEL_API_BASE", "").strip():
        return _validated_url(base, "ARCREEL_API_BASE"), os.environ.get("ARCREEL_API_TOKEN", "").strip()

    settings_path = Path.cwd() / ".arcreel" / "settings.json"
    try:
        settings = _json_file(str(settings_path))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SystemExit(f"Cannot read ArcReel settings {settings_path}: {exc}") from exc
    if not isinstance(settings, dict):
        raise SystemExit(f"ArcReel settings must be a JSON object: {settings_path}")
    mcp_url = settings.get("mcp_url")
    api_key = settings.get("api_key")
    if not isinstance(mcp_url, str) or not mcp_url.rstrip("/").endswith("/mcp"):
        raise SystemExit(f"ArcReel settings mcp_url must end with /mcp: {settings_path}")
    if not isinstance(api_key, str) or not api_key.startswith("arc-"):
        raise SystemExit(f"ArcReel settings api_key must start with arc-: {settings_path}")
    mcp_url = _validated_url(mcp_url, str(settings_path))
    return f"{mcp_url.removesuffix('/mcp')}/api/v1", api_key


def _json_file(path: str) -> object:
    with Path(path).open(encoding="utf-8") as source:
        return json.load(source)


def _multipart(payload: object, files: list[tuple[str, Path]]) -> tuple[bytes, str]:
    boundary = f"arcreel-{secrets.token_hex(16)}"
    chunks = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="payload"\r\n'
        "Content-Type: application/json; charset=utf-8\r\n\r\n".encode(),
        json.dumps(payload, ensure_ascii=False).encode(),
        b"\r\n",
    ]
    for field, path in files:
        filename = path.name.replace('"', "'").replace("\r", "").replace("\n", "")
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise SystemExit(f"Cannot read asset file {path}: {exc}") from exc
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        chunks.extend(
            [
                f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; '
                f'filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n'.encode(),
                content,
                b"\r\n",
            ]
        )
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), boundary


def _request(
    method: str,
    path: str,
    payload: object | None = None,
    files: list[tuple[str, Path]] | None = None,
) -> object:
    base, token = _connection()
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        if files:
            data, boundary = _multipart(payload, files)
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        else:
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


def _asset_files(args: argparse.Namespace) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = [
        (field, Path(path)) for field in ("start_image", "end_image") if (path := getattr(args, field))
    ]
    for field in ("reference_images", "reference_audio_files"):
        files.extend((field, Path(path)) for path in getattr(args, field))
    return files


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
        command.add_argument("--start-image")
        command.add_argument("--end-image")
        command.add_argument("--reference-images", action="append", default=[])
        command.add_argument("--reference-audio-files", action="append", default=[])
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
        result = _request("POST", "/custom-endpoints/preview-request", _test_payload(args), _asset_files(args))
    elif args.command == "trial-run":
        if not args.confirm_cost:
            parser.error("trial-run sends a billable provider request; ask the user, then pass --confirm-cost")
        result = _request("POST", "/custom-endpoints/trial-runs", _test_payload(args), _asset_files(args))
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
