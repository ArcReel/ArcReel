#!/usr/bin/env python3
"""Anthropic→llama.cpp compatibility proxy for the ArcReel agent.

Why this exists
---------------
ArcReel's agent runs on the Claude Agent SDK, which spawns the Claude Code CLI.
The CLI (v2.1.x) emits ``system`` / ``developer`` messages *inside* the
``messages`` array, and it does so **after** the first user turn (it uses
mid-conversation system turns for context/reminder updates). The captured
request looks like::

    {
      "messages": [
        {"role": "user",    "content": [...]},   # index 0
        {"role": "system",  "content": [...]}    # index 1  <-- system AFTER user
      ],
      "system": [...]
    }

llama.cpp's ``server_chat_convert_anthropic_to_oai`` preserves that order, and
the Qwen3 chat template only merges *leading* system messages — any
``system``/``developer`` message that is not in the leading contiguous block
hits ``raise_exception('System message must be at the beginning.')`` and the
server returns HTTP 500.

This proxy sits between ArcReel and llama.cpp. On every ``/v1/messages``
request it moves all ``system``/``developer`` messages to the front of the
``messages`` array (content and relative order preserved), then forwards the
request to llama.cpp unchanged. Everything else (streaming, tools, other
endpoints) passes through untouched.

Usage
-----
    .venv\\Scripts\\python.exe scripts\\llama_anthropic_proxy.py

Defaults: listen on ``127.0.0.1:8081``, forward to ``127.0.0.1:8080``.
Override with ``--listen`` / ``--upstream`` (``host:port``) or the
``LLAMA_PROXY_LISTEN`` / ``LLAMA_PROXY_UPSTREAM`` env vars.

Then point ArcReel's agent credential **Base URL** at the proxy instead of
llama.cpp directly (e.g. ``http://127.0.0.1:8081`` instead of
``http://127.0.0.1:8080``). The model name stays the same.

Stdlib only — no third-party dependencies.
"""

from __future__ import annotations

import argparse
import http.client
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("llama-anthropic-proxy")

_SYS_ROLES = ("system", "developer")


def _split_host_port(value: str, default_port: int) -> tuple[str, int]:
    host, _, port = value.rpartition(":")
    if not host:
        host, port = "127.0.0.1", str(default_port)
    return host, int(port or default_port)


def _needs_reorder(messages: list) -> bool:
    """True if any system/developer message appears after a non-system message."""
    seen_non_system = False
    for m in messages:
        role = m.get("role") if isinstance(m, dict) else None
        if role in _SYS_ROLES:
            if seen_non_system:
                return True
        else:
            seen_non_system = True
    return False


def _reorder_messages(body: bytes) -> tuple[bytes, bool]:
    """Move system/developer messages to the front of ``messages``.

    Returns (new_body, changed). Passes the original bytes through when the
    body is not JSON, has no ``messages`` list, or is already in a valid order.
    """
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return body, False
    if not isinstance(data, dict):
        return body, False
    messages = data.get("messages")
    if not isinstance(messages, list):
        return body, False
    if not _needs_reorder(messages):
        return body, False
    sys_msgs = [m for m in messages if isinstance(m, dict) and m.get("role") in _SYS_ROLES]
    rest = [m for m in messages if not (isinstance(m, dict) and m.get("role") in _SYS_ROLES)]
    data["messages"] = sys_msgs + rest
    return json.dumps(data, ensure_ascii=False).encode("utf-8"), True


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "LlamaAnthropicProxy/1.0"
    upstream_host: str = "127.0.0.1"
    upstream_port: int = 8080

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0) or 0)
        return self.rfile.read(length) if length > 0 else b""

    def _handle(self) -> None:
        path = self.path
        body = self._read_body()

        # Only /v1/messages (any query string) carries the Anthropic shape we
        # need to fix; everything else is forwarded verbatim.
        changed = False
        if path.split("?", 1)[0] == "/v1/messages" and body:
            body, changed = _reorder_messages(body)

        fwd_headers = {
            k: v for k, v in self.headers.items() if k.lower() not in ("host", "content-length", "connection")
        }
        fwd_headers["Content-Length"] = str(len(body))

        try:
            conn = http.client.HTTPConnection(self.upstream_host, self.upstream_port, timeout=600)
            conn.request(self.command, path, body=body, headers=fwd_headers)
            resp = conn.getresponse()
        except (OSError, TimeoutError) as e:
            log.error("upstream error for %s %s: %r", self.command, path, e)
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"upstream unreachable: {e!r}"}).encode())
            return

        is_chunked = resp.headers.get("Transfer-Encoding", "").lower() == "chunked"
        self.send_response(resp.status)
        for k, v in resp.getheaders():
            lk = k.lower()
            if lk == "connection":
                continue
            if lk == "transfer-encoding":
                continue  # re-framed below
            if lk == "content-length" and is_chunked:
                continue  # a chunked body has no fixed length
            self.send_header(k, v)
        if is_chunked:
            self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        # Stream the body back (SSE for streaming completions, JSON otherwise).
        # http.client de-chunks on read, so a chunked upstream must be re-framed as
        # chunked here — forwarding a length-less, non-chunked body would make the
        # client wait for the connection to close (keep-alive keeps it open → timeout).
        try:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                if is_chunked:
                    self.wfile.write(b"%x\r\n" % len(chunk))
                    self.wfile.write(chunk)
                    self.wfile.write(b"\r\n")
                else:
                    self.wfile.write(chunk)
                self.wfile.flush()
            if is_chunked:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            conn.close()

        log.info("%s %s -> %s (reordered=%s)", self.command, path, resp.status, changed)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _handle

    def log_message(self, format: str, *args: object) -> None:  # silence default per-request logging
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--listen",
        default=os.environ.get("LLAMA_PROXY_LISTEN", "127.0.0.1:8081"),
        help="host:port to listen on (default 127.0.0.1:8081)",
    )
    parser.add_argument(
        "--upstream",
        default=os.environ.get("LLAMA_PROXY_UPSTREAM", "127.0.0.1:8080"),
        help="host:port of llama.cpp (default 127.0.0.1:8080)",
    )
    args = parser.parse_args()

    listen_host, listen_port = _split_host_port(args.listen, 8081)
    up_host, up_port = _split_host_port(args.upstream, 8080)

    handler = type(
        "_BoundHandler",
        (_Handler,),
        {"upstream_host": up_host, "upstream_port": up_port},
    )
    server = ThreadingHTTPServer((listen_host, listen_port), handler)
    server.daemon_threads = True
    log.info("listening on http://%s:%d -> upstream http://%s:%d", listen_host, listen_port, up_host, up_port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
