"""测试连接产物目录的解析与一次性启动迁移。"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from lib.infra.app_data_dir import app_data_dir
from lib.infra.env_init import PROJECT_ROOT


def resolve_trial_runs_dir() -> Path:
    """测试连接产物目录：PROJECT_ROOT/trial_runs。

    刻意不放在 app_data_dir() 里：app_data_dir() 同时承担 projects_root 的身份，
    project 枚举走的是 `.`/`_` 前缀负向过滤，任何无前缀的兄弟目录都会被当作项目
    暴露给前端。trial_runs 走独立的 PROJECT_ROOT/trial_runs，从源头消除这条歧义
    ——与 resolve_log_dir() 同一处置，理由见 lib/infra/logging_config.py。
    """
    return PROJECT_ROOT / "trial_runs"


def legacy_trial_runs_dir() -> Path:
    """旧路径（app_data_dir()/trial_runs），用于一次性启动迁移。"""
    return app_data_dir() / "trial_runs"


def migrate_legacy_trial_runs_dir() -> None:
    """清掉留在项目根下的测试连接产物目录。

    只删不搬：run 登记表是进程内内存态（见 lib.custom_provider.endpoint_test.trial_run），
    重启后遗留目录没有任何消费者，产物本身也带 24h TTL，搬走只是让过期数据换个
    地方等下一轮清理。

    删除安全性：``trial_runs`` 含下划线，过不了 lib.project.project_manager 的项目名
    校验，应用自身永远建不出也读不到同名项目目录。含 ``project.json`` 只可能是手工
    放置，此时不动（避免静默删除）。

    策略：
    - 新旧路径解析到同一处 → no-op（例如 ARCREEL_DATA_DIR == PROJECT_ROOT）
    - 旧目录不存在 → no-op
    - 旧目录含 project.json → 告警，不动
    - 其余 → 删除
    - 删除时目录已被并发进程清掉（FileNotFoundError）→ no-op
    - 其余 OSError → 告警：失败意味着该目录仍会以伪项目形式出现在前端项目列表
    """
    logger = logging.getLogger(__name__)
    old_dir = legacy_trial_runs_dir()
    new_dir = resolve_trial_runs_dir()
    try:
        if old_dir.resolve() == new_dir.resolve():
            return
        if not old_dir.exists():
            return
        if (old_dir / "project.json").exists():
            logger.warning(
                "legacy trial-run dir %s contains project.json; leaving it in place - "
                "please inspect and delete manually (it shows up as a pseudo-project until then)",
                old_dir,
            )
            return
        shutil.rmtree(old_dir)
        logger.info("removed legacy trial-run dir %s (trial runs now live at %s)", old_dir, new_dir)
    except FileNotFoundError:
        # 另一个进程（多 worker / 共享同一数据根的另一个容器）已经删掉，删除目的
        # 已达成，不当作失败——同 ProjectManager.delete_project_directory 的口径。
        return
    except OSError as exc:
        logger.warning(
            "legacy trial-run dir cleanup FAILED (it may still appear at %s as a pseudo-project; "
            "please delete it manually): %s",
            old_dir,
            exc,
        )
