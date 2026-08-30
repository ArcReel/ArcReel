"""公开 custom endpoint skill 的 HTTP CLI 契约。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from lib import PROJECT_ROOT

SCRIPT = (
    PROJECT_ROOT
    / "agent_runtime_profile"
    / ".claude"
    / "skills"
    / "adapt-custom-endpoint"
    / "scripts"
    / "custom_endpoint.py"
)


def test_cli_completes_the_definition_test_and_save_flow(tmp_path: Path) -> None:
    requests: list[tuple[str, str, str | None, object]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            size = int(self.headers.get("content-length", "0"))
            body = json.loads(self.rfile.read(size))
            requests.append(("POST", self.path, self.headers.get("authorization"), body))
            payload = {
                "/api/v1/custom-endpoints/validate": {"errors": [], "warnings": [], "duplicates": []},
                "/api/v1/custom-endpoints/check-response": {"stage": "submit", "extracted": {"task_id": "t1"}},
                "/api/v1/custom-endpoints/preview-request": {"submit": {"method": "POST"}},
                "/api/v1/custom-endpoints/trial-runs": {"id": "run-1", "status": "running"},
                "/api/v1/custom-endpoints": {"id": 7, "key": "ce-7"},
            }[self.path]
            encoded = json.dumps(payload).encode()
            self.send_response(
                201 if self.path.endswith("trial-runs") or self.path.endswith("custom-endpoints") else 200
            )
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:  # noqa: N802
            requests.append(("GET", self.path, self.headers.get("authorization"), None))
            encoded = json.dumps({"id": "run-1", "status": "succeeded"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        definition = tmp_path / "definition.json"
        parameters = tmp_path / "parameters.json"
        credentials = tmp_path / "credentials.json"
        response = tmp_path / "response.json"
        definition.write_text('{"kind":"declarative"}', encoding="utf-8")
        parameters.write_text('{"model":"demo","prompt":"hello"}', encoding="utf-8")
        credentials.write_text('{"base_url":"https://provider.example","api_key":"secret"}', encoding="utf-8")
        response.write_text('{"task_id":"t1"}', encoding="utf-8")
        env = {
            **os.environ,
            "ARCREEL_API_BASE": f"http://127.0.0.1:{server.server_port}/api/v1",
            "ARCREEL_API_TOKEN": "arc-or-session-token",
        }

        commands = [
            ["validate", str(definition)],
            ["check-response", str(definition), "--stage", "submit", "--response", str(response)],
            [
                "preview-request",
                str(definition),
                "--parameters",
                str(parameters),
                "--credentials",
                str(credentials),
            ],
            [
                "trial-run",
                str(definition),
                "--parameters",
                str(parameters),
                "--credentials",
                str(credentials),
                "--confirm-cost",
            ],
            ["trial-status", "run-1"],
            ["save", str(definition)],
        ]
        for args in commands:
            result = subprocess.run(
                [sys.executable, str(SCRIPT), *args],
                env=env,
                text=True,
                capture_output=True,
                timeout=10,
            )
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert [path for _method, path, _auth, _body in requests] == [
        "/api/v1/custom-endpoints/validate",
        "/api/v1/custom-endpoints/check-response",
        "/api/v1/custom-endpoints/preview-request",
        "/api/v1/custom-endpoints/trial-runs",
        "/api/v1/custom-endpoints/trial-runs/run-1",
        "/api/v1/custom-endpoints",
    ]
    assert all(auth == "Bearer arc-or-session-token" for _method, _path, auth, _body in requests)
    assert requests[1][3] == {
        "definition": {"kind": "declarative"},
        "stage": "submit",
        "response_body": {"task_id": "t1"},
    }
    assert requests[-1][3] == {"kind": "declarative"}


def test_cli_requires_explicit_confirmation_for_cost_and_overwrite(tmp_path: Path) -> None:
    definition = tmp_path / "definition.json"
    parameters = tmp_path / "parameters.json"
    definition.write_text("{}", encoding="utf-8")
    parameters.write_text('{"model":"demo"}', encoding="utf-8")

    trial = subprocess.run(
        [sys.executable, str(SCRIPT), "trial-run", str(definition), "--parameters", str(parameters)],
        text=True,
        capture_output=True,
    )
    overwrite = subprocess.run(
        [sys.executable, str(SCRIPT), "save", str(definition), "--endpoint-id", "7"],
        text=True,
        capture_output=True,
    )

    assert trial.returncode != 0
    assert "--confirm-cost" in trial.stderr
    assert overwrite.returncode != 0
    assert "--confirm-overwrite" in overwrite.stderr


def test_cli_sends_endpoint_assets_with_the_shared_multipart_field_names(tmp_path: Path) -> None:
    received: list[tuple[str, bytes]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            size = int(self.headers.get("content-length", "0"))
            received.append((self.headers["content-type"], self.rfile.read(size)))
            encoded = b'{"id":"run-1","status":"running"}'
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        definition = tmp_path / "definition.json"
        parameters = tmp_path / "parameters.json"
        definition.write_text('{"kind":"declarative"}', encoding="utf-8")
        parameters.write_text('{"model":"demo"}', encoding="utf-8")
        assets = {
            "start": tmp_path / "start.png",
            "end": tmp_path / "end.png",
            "ref1": tmp_path / "ref-1.png",
            "ref2": tmp_path / "ref-2.png",
            "audio": tmp_path / "voice.wav",
        }
        for name, path in assets.items():
            path.write_bytes(name.encode())
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "trial-run",
                str(definition),
                "--parameters",
                str(parameters),
                "--start-image",
                str(assets["start"]),
                "--end-image",
                str(assets["end"]),
                "--reference-images",
                str(assets["ref1"]),
                "--reference-images",
                str(assets["ref2"]),
                "--reference-audio-files",
                str(assets["audio"]),
                "--confirm-cost",
            ],
            env={**os.environ, "ARCREEL_API_BASE": f"http://127.0.0.1:{server.server_port}/api/v1"},
            text=True,
            capture_output=True,
            timeout=10,
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert result.returncode == 0, result.stderr
    content_type, body = received[0]
    assert content_type.startswith("multipart/form-data; boundary=")
    assert body.count(b'name="payload"') == 1
    assert body.count(b'name="start_image"') == 1
    assert body.count(b'name="end_image"') == 1
    assert body.count(b'name="reference_images"') == 2
    assert body.count(b'name="reference_audio_files"') == 1


@pytest.mark.parametrize(
    ("body", "fragment"),
    [(b"<html>Bad Gateway</html>", "Bad Gateway"), (b"not utf-8 \xff\xff", "not utf-8")],
    ids=["html", "invalid-utf8"],
)
def test_cli_reports_non_json_response_without_traceback(tmp_path: Path, body: bytes, fragment: str) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            size = int(self.headers.get("content-length", "0"))
            self.rfile.read(size)
            encoded = body
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        definition = tmp_path / "definition.json"
        definition.write_text('{"kind":"declarative"}', encoding="utf-8")
        env = {**os.environ, "ARCREEL_API_BASE": f"http://127.0.0.1:{server.server_port}/api/v1"}
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "validate", str(definition)],
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert result.returncode != 0
    assert "non-JSON" in result.stderr
    assert fragment in result.stderr
    assert "Traceback" not in result.stderr
