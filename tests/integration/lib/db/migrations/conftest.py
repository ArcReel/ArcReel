"""迁移测试的共享 alembic 配置。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from alembic.config import Config

_PROJECT_ROOT = Path(__file__).resolve().parents[5]


@pytest.fixture
def migration_revisions() -> Callable[[str], tuple[str, str]]:
    """按文件名 glob 取出某个迁移脚本的 ``(revision, down_revision)``。

    用例据此把「升到父版本 → 造数据 → 升到本版本」串起来，而不写死 revision 串——迁移脚本
    重排或补票时，写死的 id 会静默指向另一条迁移。
    """

    def _revisions(filename_glob: str) -> tuple[str, str]:
        matches = list((_PROJECT_ROOT / "alembic" / "versions").glob(filename_glob))
        assert len(matches) == 1, f"{filename_glob} 匹配到 {len(matches)} 个迁移文件，期望 1"
        revision: str | None = None
        down_revision: str | None = None
        for line in matches[0].read_text(encoding="utf-8").splitlines():
            if line.startswith("revision: str ="):
                revision = line.split("=")[1].strip().strip('"').strip("'")
            elif line.startswith("down_revision:"):
                down_revision = line.split("=")[1].strip().strip('"').strip("'")
        if not revision or not down_revision:
            raise RuntimeError(f"未在 {matches[0].name} 中找到 revision / down_revision")
        return revision, down_revision

    return _revisions


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
