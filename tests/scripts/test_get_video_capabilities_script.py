"""`get_video_capabilities.py` CLI 脚本测试。

通过 subprocess 运行脚本，验证 stdout JSON 字段与退出码。
由于 `lib.env_init.PROJECT_ROOT` 是 hardcode 的 `lib/..`，无法通过 env var 重定向 projects 目录；
测试在真实 `{REPO_ROOT}/projects/` 下创建唯一名项目，测完清理。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    REPO_ROOT
    / "agent_runtime_profile"
    / ".claude"
    / "skills"
    / "manage-project"
    / "scripts"
    / "get_video_capabilities.py"
)
PROJECTS_ROOT = REPO_ROOT / "projects"


@pytest.fixture
def unique_project(request):
    """分配一个不会与真实项目冲突的项目目录，yield 项目名，teardown 删除。"""
    name = f"test-caps-{uuid4().hex[:8]}"
    proj = PROJECTS_ROOT / name

    def _cleanup():
        shutil.rmtree(proj, ignore_errors=True)

    request.addfinalizer(_cleanup)
    return name, proj


def _run(project_name: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--project", project_name],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


def _write_project_json(proj_dir: Path, video_backend: str, default_duration: int | None = None) -> None:
    proj_dir.mkdir(parents=True, exist_ok=True)
    payload: dict = {
        "title": proj_dir.name,
        "video_backend": video_backend,
        "content_mode": "narration",
        "generation_mode": "reference_video",
    }
    if default_duration is not None:
        payload["default_duration"] = default_duration
    (proj_dir / "project.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_script_reports_grok_capabilities(unique_project) -> None:
    name, proj_dir = unique_project
    _write_project_json(proj_dir, "grok/grok-imagine-video", default_duration=5)
    result = _run(name)
    assert result.returncode == 0, f"stderr={result.stderr}"
    data = json.loads(result.stdout)
    assert data["provider_id"] == "grok"
    assert data["model"] == "grok-imagine-video"
    assert data["max_duration"] == 15
    assert data["supported_durations"] == list(range(1, 16))
    assert data["default_duration"] == 5
    assert data["source"] == "registry"


def test_script_reports_veo_capabilities(unique_project) -> None:
    name, proj_dir = unique_project
    _write_project_json(proj_dir, "gemini-aistudio/veo-3.1-generate-preview")
    result = _run(name)
    assert result.returncode == 0, f"stderr={result.stderr}"
    data = json.loads(result.stdout)
    assert data["provider_id"] == "gemini-aistudio"
    assert data["max_duration"] == 8
    assert data["supported_durations"] == [4, 6, 8]
    assert data["default_duration"] is None


def test_script_missing_project_exits_nonzero() -> None:
    result = _run(f"nonexistent-{uuid4().hex[:8]}")
    assert result.returncode != 0
    assert result.stdout == ""
    assert "不存在" in result.stderr or "not found" in result.stderr
