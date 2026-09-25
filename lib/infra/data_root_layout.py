"""数据根布局：数据根下每一类条目位置的唯一来源。

数据根（``app_data_dir()``）是一次部署存放运行数据的目录，项目只是其中一类数据。
代码中其它地方不自行拼接数据根下的条目，也不从项目目录反推数据根，一律经
:class:`DataRootLayout` 取位置（ADR 0088）。

当前布局下项目目录就是数据根；Vertex 凭证尚在数据根之外。

「什么是项目」只由 :func:`is_project_dir` 回答：名字符合项目名规则、并且带 ``project.json``
的目录。数据根里的其它条目一概不是项目。

除 :func:`is_project_dir` 与 :func:`list_project_dirs` 外零 I/O：只派生路径，不检查存在、不建目录。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from lib.agent.agent_memory_paths import MEMORY_DIRNAME, is_valid_memory_user_id
from lib.infra.app_data_dir import app_data_dir

PROJECT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")
PROJECT_FILENAME = "project.json"


def is_project_dir(path: Path) -> bool:
    """``path`` 是不是一个项目：名字符合 :data:`PROJECT_NAME_PATTERN`、并且带 ``project.json`` 的目录。"""
    return bool(PROJECT_NAME_PATTERN.fullmatch(path.name)) and path.is_dir() and (path / PROJECT_FILENAME).is_file()


def list_project_dirs(projects_dir: Path) -> list[Path]:
    """项目目录下的全部项目，按名字排序；项目目录不存在时为空。"""
    try:
        children = sorted(projects_dir.iterdir())
    except FileNotFoundError:
        return []
    return [child for child in children if is_project_dir(child)]


@dataclass(frozen=True)
class DataRootLayout:
    """一个数据根的布局：给出其下各类条目的位置。"""

    root: Path

    @classmethod
    def current(cls) -> DataRootLayout:
        """按当前配置解析的数据根的布局。"""
        return cls(app_data_dir())

    @classmethod
    def for_project_dir(cls, project_dir: Path) -> DataRootLayout:
        """由一个项目目录求其所在数据根的布局（Agent skill 脚本以项目目录为 cwd 运行）。"""
        return cls(Path(project_dir).parent)

    @property
    def projects_dir(self) -> Path:
        """项目目录：各项目以 ``<项目目录>/<项目名>/`` 存放。"""
        return self.root

    @property
    def global_assets_dir(self) -> Path:
        """全局资产库；资产记录里的路径以数据根为基准，前缀即本目录名。"""
        return self.root / "global_assets"

    @property
    def users_dir(self) -> Path:
        """各用户数据的根：``<users_dir>/<user_id>/`` 下放该用户的记忆等。"""
        return self.root / "users"

    def user_memory_dir(self, user_id: str) -> Path:
        """用户记忆目录。

        ``user_id`` 直接构成目录名，须是单个路径段（见 ``is_valid_memory_user_id``），
        否则派生出的目录会逃出数据根，抛 ``ValueError``。
        """
        if not is_valid_memory_user_id(user_id):
            raise ValueError(f"user_id 必须是单个路径段，不能为空或含路径分隔符 / 驱动器冒号 / NUL：{user_id!r}")
        return self.users_dir / user_id / MEMORY_DIRNAME

    @property
    def sqlite_db_path(self) -> Path:
        """未设置 ``DATABASE_URL`` 时的默认 SQLite 主文件；``-wal`` / ``-shm`` 与之同目录同前缀。"""
        return self.root / "arcreel.db"

    @property
    def system_config_json_path(self) -> Path:
        """旧版系统配置文件，只作一次性导入源。"""
        return self.root / ".system_config.json"

    @property
    def log_dir(self) -> Path:
        """文件日志目录。"""
        return self.root / "logs"

    @property
    def vertex_keys_dir(self) -> Path:
        """Vertex 凭证目录，目前位于数据根的上一级。"""
        return self.root.parent / "vertex_keys"

    def vertex_credential_path(self, credential_id: int) -> Path:
        """按凭证 id 上传的 Vertex 凭证文件。"""
        return self.vertex_keys_dir / f"vertex_cred_{credential_id}.json"

    @property
    def trial_runs_dir(self) -> Path:
        """端点「测试连接」的产物目录。"""
        return self.root / "trial_runs"

    @property
    def runtime_dir(self) -> Path:
        """进程内部状态：生成准入锁、迁移完成标记、迁移错误日志等。"""
        return self.root / "runtime"

    @property
    def legacy_sqlite_db_path(self) -> Path:
        """旧布局的默认 SQLite 主文件；解析默认数据库 URL 时改名为 :attr:`sqlite_db_path`。"""
        return self.root / ".arcreel.db"

    @property
    def legacy_internal_dir(self) -> Path:
        """旧布局的数据根内部目录；其下 ``users/`` 由布局迁移搬进 :attr:`users_dir`。"""
        return self.root / ".arcreel"

    @property
    def generation_admission_locks_dir(self) -> Path:
        """生成准入锁目录。"""
        return self.runtime_dir / "generation-admission-locks"

    @property
    def session_import_marker_path(self) -> Path:
        """本地 SDK 会话导入完成标记。"""
        return self.runtime_dir / "session-store-import.done"

    @property
    def project_migration_error_log_path(self) -> Path:
        """项目 schema 迁移的错误日志。"""
        return self.runtime_dir / "project-migration-errors.log"
