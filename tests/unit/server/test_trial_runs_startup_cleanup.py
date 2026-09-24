"""Lifespan 必须在启动时清掉旧位置的测试连接产物目录。

判据是端到端可观察的结果（目录消失），不是「某个函数被调用过」：这一步的价值全在
「升级时真跑一次」，接线漏了就等于没修。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from lib.infra import app_data_dir as app_data_dir_mod
from lib.infra import trial_runs_dir as trial_runs_dir_mod


@pytest.fixture
def isolated_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """数据根与 PROJECT_ROOT 分别钉到 tmp，使新旧路径都落在 tmp 内。"""
    data_root = tmp_path / "projects"
    project_root = tmp_path / "repo"
    data_root.mkdir()
    project_root.mkdir()
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(data_root))
    monkeypatch.setattr(trial_runs_dir_mod, "PROJECT_ROOT", project_root)
    app_data_dir_mod.reset_for_tests()
    yield data_root
    app_data_dir_mod.reset_for_tests()


@pytest.mark.asyncio
async def test_lifespan_removes_legacy_trial_runs_dir(isolated_roots: Path) -> None:
    legacy = isolated_roots / "trial_runs"
    (legacy / "017c7b84c52b437ebbfc330ef048503e").mkdir(parents=True)
    (legacy / "017c7b84c52b437ebbfc330ef048503e" / "result.json").write_text("{}", encoding="utf-8")

    # 只关心启动清理这一步：lifespan 其余的长跑副作用（worker、http client、事件服务）
    # 全部替换掉，与 test_session_store_startup_migration.py 同一套替身组合。
    with (
        patch("server.app.migrate_local_transcripts_to_store", new=AsyncMock(return_value={"imported": 0})),
        patch("server.app.init_db", new=AsyncMock(return_value=None)),
        patch(
            "server.app.run_project_migrations",
            return_value=type("M", (), {"migrated": [], "failed": [], "skipped": []})(),
        ),
        patch("server.app.cleanup_stale_backups"),
        patch("server.app.app_data_dir", return_value=isolated_roots),
        patch("server.app.startup_http_client", new=AsyncMock(return_value=None)),
        patch("server.app.shutdown_http_client", new=AsyncMock(return_value=None)),
        patch("server.app.create_generation_worker") as worker_factory,
        patch("server.app.assistant.assistant_service") as svc_mock,
        patch("server.app.ProjectEventService") as pes_factory,
        patch("server.app.close_db", new=AsyncMock(return_value=None)),
    ):
        worker_factory.return_value = type("W", (), {"start": AsyncMock(), "stop": AsyncMock()})()
        svc_mock.startup = AsyncMock()
        svc_mock.session_manager = type(
            "S", (), {"start_patrol": lambda self: None, "stop_patrol": lambda self: None}
        )()
        pes_factory.return_value = type("P", (), {"start": AsyncMock(), "shutdown": AsyncMock()})()

        from server.app import app, lifespan

        async with lifespan(app):
            pass

    assert not legacy.exists()
