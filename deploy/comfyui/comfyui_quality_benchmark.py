"""Keep existing local render scripts on the same runner used by the ComfyUI node."""

import importlib.util
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "custom_nodes/mac_quality_video/runner.py"
spec = importlib.util.spec_from_file_location("mac_quality_runner", path)
if spec is None or spec.loader is None:
    raise ImportError("Install the versioned Mac renderer adapter first.")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
run = runner.run
