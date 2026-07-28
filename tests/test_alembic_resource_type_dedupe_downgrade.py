"""Alembic 迁移 e167b56a3e79（tasks.resource_type + 去重索引）的升级/降级回归测试。

降级时若存在跨 resource_type 撞键的活动任务（升级后允许并存，降级要恢复的窄索引不允许），
需先软取消其中较晚入队的一条，否则重建唯一索引会因约束冲突失败。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command


@pytest.fixture
def alembic_cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    """指向项目 alembic 脚本，DB 用临时 sqlite（通过 DATABASE_URL，env.py 会读取）。

    刻意不传 alembic.ini 路径：env.py 在 config.config_file_name 为 None 时跳过
    fileConfig() 调用，避免 alembic.ini 的 logging section 在测试中重置 root
    logger 把 pytest caplog 的 handler 清掉。
    """
    repo_root = Path(__file__).resolve().parent.parent
    cfg = Config()
    cfg.set_main_option("script_location", str(repo_root / "alembic"))
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    cfg.attributes["_test_db_path"] = str(db_path)
    return cfg


@pytest.fixture
def migration_revisions() -> tuple[str, str]:
    """读出本迁移的 (revision, down_revision)，便于按名锁定。"""
    repo_root = Path(__file__).resolve().parent.parent
    versions_dir = repo_root / "alembic" / "versions"
    matches = list(versions_dir.glob("*_add_resource_type_column_to_tasks_for_.py"))
    assert len(matches) == 1, f"找到 {len(matches)} 个迁移文件，期望 1"
    text = matches[0].read_text(encoding="utf-8")
    revision: str | None = None
    down_revision: str | None = None
    for line in text.splitlines():
        if line.startswith("revision: str ="):
            revision = line.split("=")[1].strip().strip('"').strip("'")
        elif line.startswith("down_revision:"):
            down_revision = line.split("=")[1].strip().strip('"').strip("'")
    if not revision or not down_revision:
        raise RuntimeError("未在迁移文件中找到 revision / down_revision")
    return revision, down_revision


def _insert_task(
    conn: sa.Connection,
    *,
    task_id: str,
    resource_type: str,
    resource_id: str,
    queued_at: str,
    status: str = "queued",
) -> None:
    conn.execute(
        sa.text(
            """
            INSERT INTO tasks
                (task_id, project_name, task_type, media_type, resource_id, resource_type,
                 status, source, queued_at, updated_at)
            VALUES
                (:task_id, 'demo', 'image_edit', 'image', :resource_id, :resource_type,
                 :status, 'webui', :queued_at, :queued_at)
            """
        ),
        {
            "task_id": task_id,
            "resource_id": resource_id,
            "resource_type": resource_type,
            "status": status,
            "queued_at": queued_at,
        },
    )


def test_downgrade_collapses_conflicting_active_tasks(alembic_cfg: Config, migration_revisions: tuple[str, str]):
    """升级后允许并存的跨 resource_type 同名活动任务，降级前应被软取消到唯一一条。"""
    revision_id, parent_revision_id = migration_revisions
    command.upgrade(alembic_cfg, revision_id)

    db_path = alembic_cfg.attributes["_test_db_path"]
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            # 角色「玉佩」与道具「玉佩」在同一项目下并存的活动 image_edit 任务：
            # project_name / task_type / resource_id / script_file 全部相同，仅 resource_type 不同。
            _insert_task(
                conn,
                task_id="task-a",
                resource_type="character",
                resource_id="玉佩",
                queued_at="2026-07-16 10:00:00",
            )
            _insert_task(
                conn,
                task_id="task-b",
                resource_type="prop",
                resource_id="玉佩",
                queued_at="2026-07-16 10:00:01",
            )

        # 降级前该数据在新（含 resource_type）索引下合法共存
        with engine.begin() as conn:
            statuses = {row[0]: row[1] for row in conn.execute(sa.text("SELECT task_id, status FROM tasks")).fetchall()}
        assert statuses == {"task-a": "queued", "task-b": "queued"}

        command.downgrade(alembic_cfg, parent_revision_id)

        with engine.begin() as conn:
            rows = {
                row[0]: row
                for row in conn.execute(
                    sa.text("SELECT task_id, status, cancelled_by, error_message FROM tasks")
                ).fetchall()
            }

        # 较早入队的一条保留原状态，较晚的一条被软取消（非硬删除）
        assert rows["task-a"][1] == "queued"
        assert rows["task-a"][2] is None
        assert rows["task-b"][1] == "cancelled"
        assert rows["task-b"][2] == "system"
        assert rows["task-b"][3] is not None

        # 窄索引已恢复且无 resource_type 列
        with engine.begin() as conn:
            columns = {row[1] for row in conn.execute(sa.text("PRAGMA table_info(tasks)")).fetchall()}
        assert "resource_type" not in columns
    finally:
        engine.dispose()

    # 重新升级不应因残留数据报错
    command.upgrade(alembic_cfg, revision_id)


def test_downgrade_without_conflict_is_noop_for_active_tasks(alembic_cfg: Config, migration_revisions: tuple[str, str]):
    """无跨 resource_type 撞键时，降级不应改动任何活动任务的状态。"""
    revision_id, parent_revision_id = migration_revisions
    command.upgrade(alembic_cfg, revision_id)

    db_path = alembic_cfg.attributes["_test_db_path"]
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            _insert_task(
                conn,
                task_id="task-a",
                resource_type="character",
                resource_id="Alice",
                queued_at="2026-07-16 10:00:00",
            )
            _insert_task(
                conn,
                task_id="task-b",
                resource_type="prop",
                resource_id="玉佩",
                queued_at="2026-07-16 10:00:01",
            )

        command.downgrade(alembic_cfg, parent_revision_id)

        with engine.begin() as conn:
            statuses = {row[0]: row[1] for row in conn.execute(sa.text("SELECT task_id, status FROM tasks")).fetchall()}
        assert statuses == {"task-a": "queued", "task-b": "queued"}
    finally:
        engine.dispose()
