"""迁移测试的共享 alembic 配置。"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config

_PROJECT_ROOT = Path(__file__).resolve().parents[5]


@pytest.fixture
def alembic_cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Config, Path]:
    """指向仓库 alembic 脚本的 Config，与本用例独占的临时 sqlite 库路径。

    刻意空构造而不传 ``alembic.ini``：``env.py`` 在 ``config_file_name`` 为 None 时
    跳过 ``fileConfig()``，否则 alembic.ini 的 logging section 会重置 root logger、
    连带清掉 pytest caplog 的 handler。``lib/db/__init__.py`` 的 ``init_db()`` 同理。
    库位置经 ``DATABASE_URL`` 传给 ``env.py``。
    """
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    cfg = Config()
    cfg.set_main_option("script_location", str(_PROJECT_ROOT / "alembic"))
    return cfg, db_path
