import json
import subprocess
import sys
from pathlib import Path

import pytest

from lib.project.project_manager import ProjectManager
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.workflow.workflow_state import WorkflowStateService


@pytest.mark.parametrize("distribution", ["complete", "tail", "stale"])
def test_perf_project_distribution(tmp_path: Path, distribution: str):
    subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.perf.bench",
            "build",
            "--data-root",
            str(tmp_path),
            "--episodes",
            "2",
            "--assets",
            "3",
            "--units",
            "2",
            "--sheet-kb",
            "1",
            "--video-kb",
            "1",
            "--distribution",
            distribution,
            "--no-racy-wait",
        ],
        check=True,
    )
    pm = ProjectManager(tmp_path)
    project = json.loads((pm.get_project_path("demo") / "project.json").read_bytes())
    assert project["default_text_backend"] == "ark/doubao-seed-2-1-lite-260915"
    assert project["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION
    service = WorkflowStateService(pm)
    first = service.get_status("demo", episode=1)
    second = service.get_status("demo", episode=2)
    assert first.content.episode_complete
    if distribution == "complete":
        assert second.content.episode_complete
    elif distribution == "tail":
        assert second.artifacts["videos"]["missing_ids"] == ["E2U2"]
    else:
        assert second.artifacts["videos"]["stale_ids"] == ["E2U1"]
