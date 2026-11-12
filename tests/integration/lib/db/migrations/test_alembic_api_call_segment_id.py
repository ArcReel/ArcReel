"""Widen usage IDs without changing existing records or losing their index."""

import pytest
import sqlalchemy as sa

from alembic import command


def test_widen_segment_id_preserves_history_and_refuses_lossy_downgrade(alembic_cfg, migration_revisions):
    revision, parent = migration_revisions("*_widen_api_call_segment_id.py")
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, parent)
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO api_calls (id, user_id, project_name, call_type, model, status, segment_id, "
                    "started_at, created_at, updated_at) VALUES (1, 'default', 'demo', 'audio', 'tts', 'success', "
                    "'E1S01', '2026-11-12 00:00:00', '2026-11-12 00:00:00', '2026-11-12 00:00:00')"
                )
            )
        command.upgrade(cfg, revision)
        assert {index["name"] for index in sa.inspect(engine).get_indexes("api_calls")} >= {"ix_api_calls_segment_id"}
        column = next(
            column for column in sa.inspect(engine).get_columns("api_calls") if column["name"] == "segment_id"
        )
        assert column["type"].length is None
        with engine.begin() as connection:
            assert connection.execute(sa.text("SELECT segment_id FROM api_calls WHERE id = 1")).scalar_one() == "E1S01"
            connection.execute(sa.text("UPDATE api_calls SET segment_id = :id WHERE id = 1"), {"id": "a" * 80})
        with pytest.raises(RuntimeError, match="Cannot narrow"):
            command.downgrade(cfg, parent)
        with engine.begin() as connection:
            assert connection.execute(sa.text("SELECT segment_id FROM api_calls WHERE id = 1")).scalar_one() == "a" * 80
            connection.execute(sa.text("UPDATE api_calls SET segment_id = 'E1S01' WHERE id = 1"))
        command.downgrade(cfg, parent)
        column = next(
            column for column in sa.inspect(engine).get_columns("api_calls") if column["name"] == "segment_id"
        )
        assert column["type"].length == 20
        with engine.begin() as connection:
            assert connection.execute(sa.text("SELECT segment_id FROM api_calls WHERE id = 1")).scalar_one() == "E1S01"
    finally:
        engine.dispose()
