import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.local_instance import ROOT, build_backend_env, pick_port, wait_until_healthy


@pytest.mark.local_port
@pytest.mark.parametrize("module", ["scripts.e2e_record_backend", "frontend.e2e.live.backend"])
def test_temporary_backend_preserves_checkout_logs(module: str, tmp_path: Path):
    checkout = tmp_path / "checkout"
    shutil.copytree(ROOT / "lib", checkout / "lib", ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("alembic", "alembic.ini", "agent_runtime_profile", "pyproject.toml"):
        (checkout / name).symlink_to(ROOT / name)
    sentinel = checkout / "logs" / "existing.log"
    sentinel.parent.mkdir()
    sentinel.write_bytes(b"existing log\n")
    env = build_backend_env(tmp_path, auth_enabled=False)
    env.pop("ARCREEL_SKIP_LEGACY_LOG_MIGRATION")
    env["PYTHONPATH"] = str(ROOT)
    port = pick_port()
    with subprocess.Popen([sys.executable, "-m", module, "--port", str(port)], cwd=checkout, env=env) as backend:
        try:
            wait_until_healthy(f"http://127.0.0.1:{port}", process=backend)
        finally:
            backend.terminate()
            try:
                backend.wait(timeout=15)
            except subprocess.TimeoutExpired:
                backend.kill()
                raise
    assert sentinel.read_bytes() == b"existing log\n"
