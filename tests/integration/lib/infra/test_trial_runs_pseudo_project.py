"""回归：旧位置的测试连接产物目录会以前端伪项目的形式出现，启动迁移必须清掉它。

真实文件系统 + 真实 ProjectManager 枚举：判据落在「项目列表里还有没有它」这条用户可见
症状上，而不是「迁移函数被调用过」。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from lib.infra import app_data_dir as app_data_dir_mod
from lib.infra import trial_runs_dir as trial_runs_dir_mod
from lib.project.project_manager import ProjectManager


@pytest.fixture
def projects_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(root))
    app_data_dir_mod.reset_for_tests()
    yield root
    app_data_dir_mod.reset_for_tests()


def test_legacy_trial_runs_dir_disappears_from_project_list(
    projects_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = projects_root / "trial_runs"
    (legacy / "017c7b84c52b437ebbfc330ef048503e").mkdir(parents=True)
    (legacy / "017c7b84c52b437ebbfc330ef048503e" / "result.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(trial_runs_dir_mod, "PROJECT_ROOT", projects_root.parent / "repo")

    manager = ProjectManager(projects_root)
    # 报障现场：它在项目列表里，且前端会以 i18n「未命名项目」兜底显示
    assert "trial_runs" in manager.list_projects()

    trial_runs_dir_mod.migrate_legacy_trial_runs_dir()

    assert "trial_runs" not in manager.list_projects()
    assert not legacy.exists()
