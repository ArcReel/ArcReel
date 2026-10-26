"""Install the versioned local adapter after checking its existing runtime dependencies."""

import argparse
import hashlib
import json
import shutil
import subprocess
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

SOURCE = Path(__file__).resolve().parent


def install(root):
    lock = json.loads((SOURCE / "runtime-lock.json").read_text(encoding="utf-8"))
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("Git is required to verify the installed runtime revision.")
    revision = subprocess.check_output(
        [git, "rev-parse", "HEAD"], cwd=root / "user/runtimes/ltx-2-mlx", text=True
    ).strip()
    if not revision.startswith(lock["runtime_commit"]):
        raise RuntimeError("The installed MLX runtime has changed; qualify its CLI before installing this adapter.")
    for relative, expected in lock["dependencies"].items():
        if hashlib.sha256((root / relative).read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"Runtime dependency changed: {relative}. Requalify it before installing.")
    with urllib.request.urlopen("http://127.0.0.1:8188/queue", timeout=5) as response:
        queue = json.load(response)
    if queue["queue_running"] or queue["queue_pending"]:
        raise RuntimeError("Wait for the render queue to become idle before installing.")
    backup = root / "user/adapter-backups" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    files = {
        "custom_nodes/mac_quality_video/__init__.py": SOURCE / "mac_quality_video/__init__.py",
        "custom_nodes/mac_quality_video/runner.py": SOURCE / "mac_quality_video/runner.py",
        "user/comfyui_quality_benchmark.py": SOURCE / "comfyui_quality_benchmark.py",
    }
    installed = {}
    for relative, source in files.items():
        destination = root / relative
        if destination.exists():
            saved = backup / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, saved)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        installed[relative] = hashlib.sha256(destination.read_bytes()).hexdigest()
    (root / "user/local-adapter-install.json").write_text(
        json.dumps({"backup": str(backup), "runtime": lock, "files": installed}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Installed adapter. Backup: {backup}")
    print("Restart idle ComfyUI, then import local-ltx-endpoint.json and set supported durations to [2, 4, 5].")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comfy-root", required=True, type=Path)
    install(parser.parse_args().comfy_root.resolve())
