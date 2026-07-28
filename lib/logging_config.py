"""统一日志配置。"""

from __future__ import annotations

import logging
import os
import shutil
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from lib.app_data_dir import app_data_dir
from lib.env_init import PROJECT_ROOT

_HANDLER_ATTR = "_arcreel_logging"
_FILE_HANDLER_ATTR = "_arcreel_file_logging"
_DISABLED_TRUTHY = frozenset({"1", "true", "yes"})


def _file_logging_disabled() -> bool:
    return os.environ.get("ARCREEL_LOG_FILE_DISABLED", "").strip().lower() in _DISABLED_TRUTHY


def resolve_log_dir() -> Path:
    """日志目录解析：ARCREEL_LOG_DIR > PROJECT_ROOT/logs。

    相对路径基于 PROJECT_ROOT。

    日志目录刻意不放在 app_data_dir() 里：app_data_dir() 同时承担 projects_root
    的身份，project 枚举走的是 `.`/`_` 前缀负向过滤，任何无前缀的兄弟目录都会被
    当作项目暴露给前端。logs 走独立的 PROJECT_ROOT/logs，从源头消除这条歧义。
    """
    raw = os.environ.get("ARCREEL_LOG_DIR", "").strip()
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path
    return PROJECT_ROOT / "logs"


def legacy_log_dir() -> Path:
    """旧默认路径（app_data_dir()/logs），用于一次性启动迁移。"""
    return app_data_dir() / "logs"


def migrate_legacy_log_dir() -> None:
    """将旧默认位置的日志迁到新位置；只在 ARCREEL_LOG_DIR 未显式覆盖时进行。

    策略：
    - 用户显式设了 ARCREEL_LOG_DIR → 不动（用户已自主决定路径）
    - 新旧路径解析到同一处 → no-op（例如 ARCREEL_DATA_DIR == PROJECT_ROOT）
    - 旧目录不存在 → no-op
    - 新目录不存在，或存在但为空 → 用 shutil.move 平移旧→新（跨设备自动
      fallback 到 copy+unlink，专门覆盖 docker bind-mount 等典型升级路径）。
      "存在但为空" 分支专门照顾 docker-compose 的 ``./logs:/app/logs`` 挂载——
      容器启动时宿主机 ``./logs`` 会被预创建为空目录，否则升级路径下旧 logs
      会一直留在 projects/logs 下被当成伪项目枚举
    - 新目录存在且非空 → 警告，不动（避免静默覆盖；让用户自己处置）
    - OSError 升级为 ERROR：迁移失败常意味着 logs 仍卡在旧路径下，会以
      伪项目形式出现在 UI，operator 必须看到这条
    """
    if os.environ.get("ARCREEL_LOG_DIR", "").strip():
        return

    logger = logging.getLogger(__name__)
    old_dir = legacy_log_dir()
    new_dir = resolve_log_dir()
    try:
        if old_dir.resolve() == new_dir.resolve():
            return
        if not old_dir.exists():
            return
        if new_dir.exists():
            if any(new_dir.iterdir()):
                logger.warning(
                    "legacy log dir %s and new log dir %s both have content; leaving both in place — please move/delete manually",
                    old_dir,
                    new_dir,
                )
                return
            # new_dir 存在但为空 —— 这是 docker bind-mount 的典型形态：
            # ``./logs:/app/logs`` 让 docker 启动时把宿主机 ./logs 创建为空
            # 目录，容器内 /app/logs 是挂载点。不能对挂载点本身 rmdir
            # （会报 EBUSY/Device or resource busy），所以逐项搬运 old_dir
            # 的内容到 new_dir，最后再删已清空的 old_dir。每项独立 shutil.move
            # 在跨设备时仍会 fallback 到 copy+unlink。
            for entry in old_dir.iterdir():
                shutil.move(entry, new_dir / entry.name)
            old_dir.rmdir()
        else:
            new_dir.parent.mkdir(parents=True, exist_ok=True)
            # shutil.move 在 src/dst 同设备时走 os.rename，跨设备时降级到 copytree + rmtree。
            # 这正是 docker bind-mount 升级路径下 os.replace 报 EXDEV 的解药。
            shutil.move(old_dir, new_dir)
        logger.info("migrated legacy log dir %s -> %s", old_dir, new_dir)
    except OSError as exc:
        logger.error(
            "legacy log dir migration FAILED (logs may still appear at %s as a pseudo-project; "
            "please move %s -> %s manually): %s",
            old_dir,
            old_dir,
            new_dir,
            exc,
        )


def setup_logging(level: str | None = None, *, file: bool = True) -> None:
    """配置根 logger。

    Args:
        level: 日志级别字符串（DEBUG/INFO/WARNING/ERROR）。
               如未提供，从环境变量 LOG_LEVEL 读取，默认 INFO。
        file: 是否挂 TimedRotatingFileHandler。模块导入期传 False 推迟到
              lifespan 之后挂——避免 import 期 mkdir 新 logs/ 污染开发树，
              也让 migrate_legacy_log_dir() 能在新目录还不存在时 rename。
    """
    if level is None:
        level = os.environ.get("LOG_LEVEL", "INFO")

    numeric_level = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(numeric_level)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 幂等：避免重复添加 stream handler
    if not any(getattr(h, _HANDLER_ATTR, False) for h in root.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        setattr(handler, _HANDLER_ATTR, True)
        root.addHandler(handler)

    if file:
        attach_file_handler(formatter)

    # 统一 uvicorn 的日志格式，避免两种格式并存
    for name in ("uvicorn", "uvicorn.error"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True

    # 禁用 uvicorn.access：请求日志由 app.py 的 middleware 统一处理
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers.clear()
    access_logger.disabled = True

    # 抑制 aiosqlite 的 DEBUG 噪音（每次 SQL 操作都会输出两行日志）
    logging.getLogger("aiosqlite").setLevel(max(numeric_level, logging.INFO))


def attach_file_handler(formatter: logging.Formatter | None = None) -> None:
    """为 root logger 挂 TimedRotatingFileHandler（默认开启，按天切，保留 7 份）。

    幂等：已挂则直接返回。被 setup_logging 调用，也可在 lifespan 内
    单独触发——后者用于先跑 migrate_legacy_log_dir() 平移旧目录，再挂
    file handler 以避免新目录被提前创建堵掉 rename。
    """
    if _file_logging_disabled():
        return

    root = logging.getLogger()
    if any(getattr(h, _FILE_HANDLER_ATTR, False) for h in root.handlers):
        return

    if formatter is None:
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    try:
        log_dir = resolve_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = TimedRotatingFileHandler(
            filename=str(log_dir / "arcreel.log"),
            when="midnight",
            backupCount=7,
            encoding="utf-8",
            utc=False,
        )
        file_handler.setFormatter(formatter)
        setattr(file_handler, _FILE_HANDLER_ATTR, True)
        root.addHandler(file_handler)
    except Exception as exc:
        logging.getLogger(__name__).warning("file logging disabled: %s", exc)
