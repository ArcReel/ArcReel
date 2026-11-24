import asyncio
import os
import signal
import sqlite3
import sys
from contextlib import AsyncExitStack

import httpx
import pytest


@pytest.mark.local_port
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX SIGTERM replay")
async def test_one_sigterm_ends_three_streams_and_drains_only_the_running_task(tmp_path, unused_tcp_port):
    database = tmp_path / "tasks.db"
    data_root = tmp_path / "data"

    def states():
        with sqlite3.connect(database) as connection:
            return dict(connection.execute("select resource_id, status from tasks"))

    log_path = tmp_path / "server.log"
    with log_path.open("w") as log:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import sys, uvicorn; "
            "uvicorn.Server(uvicorn.Config('tests.integration.server.shutdown_app:create_app', "
            "factory=True, port=int(sys.argv[1]), timeout_graceful_shutdown=10)).run()",
            str(unused_tcp_port),
            env={
                **os.environ,
                "DATABASE_URL": f"sqlite+aiosqlite:///{database}",
                "ARCREEL_DATA_DIR": str(data_root),
                "AUTH_ENABLED": "false",
                "LISTEN_HOST": "127.0.0.1",
                "PYTHON_DOTENV_DISABLED": "1",
            },
            stdin=asyncio.subprocess.PIPE,
            stdout=log,
            stderr=log,
        )
        try:
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{unused_tcp_port}", trust_env=False) as client:
                async with asyncio.timeout(45):
                    while True:
                        assert proc.returncode is None, log_path.read_text()
                        try:
                            if (await client.get("/health")).is_success and (await asyncio.to_thread(states)).get(
                                "first"
                            ) == "running":
                                break
                        except httpx.ConnectError:
                            pass
                        await asyncio.sleep(0.05)
                assert await asyncio.to_thread(states) == {"first": "running", "second": "queued"}
                key = (await client.post("/api/v1/api-keys", json={"name": "shutdown"})).json()["key"]
                paths = [
                    ("/api/v1/projects/demo/events/stream", "snapshot"),
                    ("/api/v1/projects/demo/assistant/sessions/session/entries/stream", "status"),
                    ("/mcp", None),
                ]
                async with AsyncExitStack() as streams:
                    readers = []
                    for path, first_event in paths:
                        response = await streams.enter_async_context(
                            client.stream(
                                "GET", path, headers={"Accept": "text/event-stream", "Authorization": f"Bearer {key}"}
                            )
                        )
                        response.raise_for_status()
                        assert response.headers["content-type"].startswith("text/event-stream")
                        reader = response.aiter_lines()
                        if first_event:
                            async for line in reader:
                                if line == f"event: {first_event}":
                                    break
                            else:
                                pytest.fail(f"{path} ended during handshake")
                        readers.append(reader)
                    proc.send_signal(signal.SIGTERM)
                    async with asyncio.timeout(4):
                        for reader in readers:
                            async for _ in reader:
                                pass
                assert proc.returncode is None
                assert proc.stdin is not None
                proc.stdin.write(b"1")
                await proc.stdin.drain()
                assert await asyncio.wait_for(proc.wait(), 15) == -signal.SIGTERM, log_path.read_text()
            assert (data_root / "stopped").exists(), log_path.read_text()
            assert await asyncio.to_thread(states) == {"first": "succeeded", "second": "queued"}
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
