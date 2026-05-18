# 日志持久化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 ArcReel 加上文件日志落盘（按天轮转、保留 7 天）+ 鉴权后的诊断 zip 下载端点，让单机用户能反馈高质量 bug 报告而不丢失日志。

**Architecture:** `lib/logging_config.py` 在现有 StreamHandler 之外挂一个 `TimedRotatingFileHandler`（默认写到 `app_data_dir()/logs/arcreel.log`）；新增 `server/services/diagnostics.py` 收集脱敏的系统信息；新增 `server/routers/system.py` 提供 `GET /api/v1/system/logs/download` 返回 zip（含日志文件 + diagnostics.txt）；前端在 `AboutSection` 加一个按钮，通过现有 `withAuth` 走授权请求并触发浏览器下载。

**Tech Stack:** Python 3 (logging.handlers / zipfile / tempfile.SpooledTemporaryFile)、FastAPI / StreamingResponse、pytest（`asyncio_mode = "auto"`）、React + TypeScript + i18next + vitest。

**Spec:** `docs/superpowers/specs/2026-05-18-log-persistence-design.md`

---

## Task 1: 文件日志 handler

**Files:**
- Modify: `lib/logging_config.py`
- Test: `tests/test_logging_persistence.py` (新建)

- [ ] **Step 1.1: 写失败测试**

新建 `tests/test_logging_persistence.py`：

```python
"""TimedRotatingFileHandler 注册与降级行为测试。"""

from __future__ import annotations

import importlib
import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _reset_root_logger():
    """每个用例前后清空 root logger handlers，避免污染。"""
    root = logging.getLogger()
    saved = list(root.handlers)
    root.handlers.clear()
    yield
    root.handlers.clear()
    root.handlers.extend(saved)


@pytest.fixture
def isolated_log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("ARCREEL_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.delenv("ARCREEL_LOG_FILE_DISABLED", raising=False)
    return tmp_path / "logs"


def _reload_module():
    from lib import logging_config

    return importlib.reload(logging_config)


def test_file_handler_registered_by_default(isolated_log_dir: Path) -> None:
    cfg = _reload_module()
    cfg.setup_logging()
    root = logging.getLogger()
    file_handlers = [h for h in root.handlers if isinstance(h, TimedRotatingFileHandler)]
    assert len(file_handlers) == 1
    assert Path(file_handlers[0].baseFilename).parent == isolated_log_dir.resolve()


def test_file_handler_disabled_by_env(
    isolated_log_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARCREEL_LOG_FILE_DISABLED", "1")
    cfg = _reload_module()
    cfg.setup_logging()
    root = logging.getLogger()
    assert not any(isinstance(h, TimedRotatingFileHandler) for h in root.handlers)


def test_logs_written_to_file(isolated_log_dir: Path) -> None:
    cfg = _reload_module()
    cfg.setup_logging()
    logging.getLogger("test.persistence").info("hello-arcreel")
    for h in logging.getLogger().handlers:
        h.flush()
    log_file = isolated_log_dir / "arcreel.log"
    assert log_file.exists()
    assert "hello-arcreel" in log_file.read_text(encoding="utf-8")


def test_mkdir_failure_graceful(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "blocked" / "logs"
    monkeypatch.setenv("ARCREEL_LOG_DIR", str(target))
    monkeypatch.delenv("ARCREEL_LOG_FILE_DISABLED", raising=False)

    real_mkdir = Path.mkdir

    def fake_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        if self == target:
            raise PermissionError("simulated read-only fs")
        real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fake_mkdir)

    cfg = _reload_module()
    cfg.setup_logging()  # 不抛
    root = logging.getLogger()
    assert any(isinstance(h, logging.StreamHandler) for h in root.handlers)
    assert not any(isinstance(h, TimedRotatingFileHandler) for h in root.handlers)


def test_idempotent(isolated_log_dir: Path) -> None:
    cfg = _reload_module()
    cfg.setup_logging()
    cfg.setup_logging()
    cfg.setup_logging()
    root = logging.getLogger()
    file_handlers = [h for h in root.handlers if isinstance(h, TimedRotatingFileHandler)]
    assert len(file_handlers) == 1


def test_disabled_env_accepts_aliases(
    isolated_log_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for value in ("1", "true", "TRUE", "yes", "Yes"):
        monkeypatch.setenv("ARCREEL_LOG_FILE_DISABLED", value)
        cfg = _reload_module()
        cfg.setup_logging()
        root = logging.getLogger()
        assert not any(isinstance(h, TimedRotatingFileHandler) for h in root.handlers), value
        root.handlers.clear()
```

- [ ] **Step 1.2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_logging_persistence.py -v`
Expected: 全部 FAIL（当前 `setup_logging()` 不挂 file handler）

- [ ] **Step 1.3: 修改 `lib/logging_config.py`**

完整新内容：

```python
"""统一日志配置。"""

from __future__ import annotations

import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from lib.app_data_dir import app_data_dir
from lib.env_init import PROJECT_ROOT

_HANDLER_ATTR = "_arcreel_logging"
_FILE_HANDLER_ATTR = "_arcreel_file_logging"
_DISABLED_TRUTHY = frozenset({"1", "true", "yes"})


def _file_logging_disabled() -> bool:
    return os.environ.get("ARCREEL_LOG_FILE_DISABLED", "").strip().lower() in _DISABLED_TRUTHY


def _resolve_log_dir() -> Path:
    """日志目录解析：ARCREEL_LOG_DIR > app_data_dir()/logs。

    相对路径基于 PROJECT_ROOT，与 app_data_dir 的策略保持一致。
    """
    raw = os.environ.get("ARCREEL_LOG_DIR", "").strip()
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path
    return app_data_dir() / "logs"


def setup_logging(level: str | None = None) -> None:
    """配置根 logger。

    Args:
        level: 日志级别字符串（DEBUG/INFO/WARNING/ERROR）。
               如未提供，从环境变量 LOG_LEVEL 读取，默认 INFO。
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

    # 文件 handler：默认开启，按天切，保留 7 份。失败不阻塞 stdout。
    if not _file_logging_disabled() and not any(
        getattr(h, _FILE_HANDLER_ATTR, False) for h in root.handlers
    ):
        try:
            log_dir = _resolve_log_dir()
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
```

- [ ] **Step 1.4: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_logging_persistence.py -v`
Expected: 6 passed

- [ ] **Step 1.5: lint + 类型检查**

Run:
```bash
uv run ruff check lib/logging_config.py tests/test_logging_persistence.py
uv run ruff format lib/logging_config.py tests/test_logging_persistence.py
uv run basedpyright lib/logging_config.py tests/test_logging_persistence.py
```
Expected: 0 error / 0 warning

- [ ] **Step 1.6: commit**

```bash
git add lib/logging_config.py tests/test_logging_persistence.py
git commit -m "feat(logging): file handler with daily rotation"
```

---

## Task 2: 诊断信息收集器

**Files:**
- Create: `server/services/diagnostics.py`
- Test: `tests/test_diagnostics_service.py` (新建)

- [ ] **Step 2.1: 写失败测试**

新建 `tests/test_diagnostics_service.py`：

```python
"""diagnostics.collect_diagnostics 行为测试。"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_collect_returns_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path))
    from lib.app_data_dir import _reset_for_tests
    _reset_for_tests()

    from server.services.diagnostics import collect_diagnostics

    text = collect_diagnostics()
    assert isinstance(text, str)
    assert "ArcReel diagnostics" in text or "ArcReel Diagnostics" in text
    assert "App version" in text
    assert "Python" in text
    assert "OS" in text
    assert "Data directory" in text
    assert "Log directory" in text


def test_collect_masks_db_password(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://arcuser:supersecretpassword@db.example.com:5432/arcreel",
    )
    from lib.app_data_dir import _reset_for_tests
    _reset_for_tests()

    from server.services.diagnostics import collect_diagnostics

    text = collect_diagnostics()
    assert "supersecretpassword" not in text
    assert "••" in text  # 出现脱敏标记
    # 数据库名/host 仍可见（用于诊断）
    assert "db.example.com" in text
    assert "arcreel" in text


def test_collect_swallows_field_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path))
    from lib.app_data_dir import _reset_for_tests
    _reset_for_tests()

    import server.services.diagnostics as diag_mod

    def boom() -> str:
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(diag_mod, "_app_version", boom)

    text = diag_mod.collect_diagnostics()
    assert "<unavailable" in text
    # 其他字段仍出现
    assert "Python" in text


def test_collect_returns_log_dir_matching_logging_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log_dir = tmp_path / "custom-logs"
    monkeypatch.setenv("ARCREEL_LOG_DIR", str(log_dir))
    from lib.app_data_dir import _reset_for_tests
    _reset_for_tests()

    from server.services.diagnostics import collect_diagnostics

    text = collect_diagnostics()
    assert str(log_dir) in text
```

- [ ] **Step 2.2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_diagnostics_service.py -v`
Expected: FAIL — module 不存在

- [ ] **Step 2.3: 实现 `server/services/diagnostics.py`**

```python
"""收集脱敏后的系统诊断信息，供 /system/logs/download 打包。"""

from __future__ import annotations

import platform
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse

from lib.app_data_dir import app_data_dir
from lib.logging_config import _resolve_log_dir
from lib.logging_utils import _mask_secret

_UNAVAILABLE = "<unavailable: {exc}>"


def _safe(fn: object, label: str) -> str:
    try:
        return str(fn())  # type: ignore[operator]
    except Exception as exc:
        return _UNAVAILABLE.format(exc=f"{label}: {exc}")


def _app_version() -> str:
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("arcreel")
        except PackageNotFoundError:
            pass
    except Exception:
        pass

    # Fallback: 解析 pyproject.toml
    try:
        import tomllib

        from lib.env_init import PROJECT_ROOT

        with (PROJECT_ROOT / "pyproject.toml").open("rb") as f:
            data = tomllib.load(f)
        return str(data.get("project", {}).get("version", "<unknown>"))
    except Exception:
        return "<unknown>"


def _python_version() -> str:
    return sys.version.replace("\n", " ")


def _os_info() -> str:
    return platform.platform()


def _data_dir() -> str:
    return str(app_data_dir())


def _log_dir() -> str:
    return str(_resolve_log_dir())


def _db_url() -> str:
    import os

    raw = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./projects/.arcreel.db")
    try:
        parsed = urlparse(raw)
        if parsed.username or parsed.password:
            user = _mask_secret(parsed.username) if parsed.username else ""
            host = parsed.hostname or ""
            port = f":{parsed.port}" if parsed.port else ""
            netloc = f"{user}:••@{host}{port}" if parsed.password else f"{user}@{host}{port}"
            return urlunparse(parsed._replace(netloc=netloc))
    except Exception:
        pass
    return raw


def _log_level() -> str:
    import os

    return os.environ.get("LOG_LEVEL", "INFO")


def _sandbox_status() -> str:
    try:
        from server.app import check_sandbox_available

        return "enabled" if check_sandbox_available() else "disabled"
    except Exception as exc:
        return _UNAVAILABLE.format(exc=f"sandbox: {exc}")


def _providers() -> str:
    try:
        from lib.config.registry import PROVIDER_REGISTRY

        ids = sorted(PROVIDER_REGISTRY.keys())
        return ", ".join(ids) if ids else "<none>"
    except Exception as exc:
        return _UNAVAILABLE.format(exc=f"providers: {exc}")


def collect_diagnostics() -> str:
    """返回脱敏的 plain-text 诊断报告。任一字段失败用 <unavailable> 占位，整体不抛。"""
    fields: list[tuple[str, object]] = [
        ("App version", _app_version),
        ("Python", _python_version),
        ("OS", _os_info),
        ("Data directory", _data_dir),
        ("Log directory", _log_dir),
        ("Database URL", _db_url),
        ("Log level", _log_level),
        ("Sandbox", _sandbox_status),
        ("Registered providers", _providers),
        ("Report generated", lambda: datetime.now(timezone.utc).isoformat()),
    ]

    lines = ["ArcReel diagnostics", "=" * 40]
    for label, fn in fields:
        lines.append(f"{label}: {_safe(fn, label)}")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 2.4: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_diagnostics_service.py -v`
Expected: 4 passed

如果 `test_collect_masks_db_password` 失败，确认 `_mask_secret` 对短字符串（如 `arc` 3 字符）的返回是 `••••`（参见 `lib/logging_utils.py:_mask_secret` —— `len <= 8` 时返回 `••••`），所以 "arc" 已被替换。测试断言已写成"supersecret 必须不出现，且原 username 已脱敏"。

- [ ] **Step 2.5: lint + 类型检查**

```bash
uv run ruff check server/services/diagnostics.py tests/test_diagnostics_service.py
uv run ruff format server/services/diagnostics.py tests/test_diagnostics_service.py
uv run basedpyright server/services/diagnostics.py tests/test_diagnostics_service.py
```
Expected: 0 error

- [ ] **Step 2.6: commit**

```bash
git add server/services/diagnostics.py tests/test_diagnostics_service.py
git commit -m "feat(diagnostics): collect masked system info"
```

---

## Task 3: 下载端点 + router 注册

**Files:**
- Create: `server/routers/system.py`
- Modify: `server/app.py`（注册 router）
- Test: `tests/test_system_logs_router.py` (新建)

- [ ] **Step 3.1: 写失败测试**

新建 `tests/test_system_logs_router.py`：

```python
"""GET /api/v1/system/logs/download 行为测试。"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def auth_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")


@pytest.fixture
async def _client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, auth_disabled: None):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setenv("ARCREEL_LOG_DIR", str(log_dir))
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path / "data"))
    from lib.app_data_dir import _reset_for_tests
    _reset_for_tests()

    # 重新载入 app 以让 env 生效
    import importlib

    from server import app as app_module

    importlib.reload(app_module)

    transport = ASGITransport(app=app_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, log_dir


async def test_download_returns_zip(_client) -> None:
    client, log_dir = _client
    (log_dir / "arcreel.log").write_text("test log line\n", encoding="utf-8")
    res = await client.get("/api/v1/system/logs/download")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"
    assert "attachment" in res.headers.get("content-disposition", "")


async def test_zip_contains_diagnostics(_client) -> None:
    client, _log_dir = _client
    res = await client.get("/api/v1/system/logs/download")
    z = zipfile.ZipFile(io.BytesIO(res.content))
    assert "diagnostics.txt" in z.namelist()
    diag = z.read("diagnostics.txt").decode("utf-8")
    assert "App version" in diag


async def test_zip_includes_log_files(_client) -> None:
    client, log_dir = _client
    (log_dir / "arcreel.log").write_text("active log\n", encoding="utf-8")
    (log_dir / "arcreel.log.2026-05-15").write_text("archived log\n", encoding="utf-8")
    res = await client.get("/api/v1/system/logs/download")
    z = zipfile.ZipFile(io.BytesIO(res.content))
    names = z.namelist()
    assert any(n.endswith("arcreel.log") for n in names)
    assert any(n.endswith("arcreel.log.2026-05-15") for n in names)


async def test_empty_logs_dir(_client) -> None:
    client, _ = _client
    res = await client.get("/api/v1/system/logs/download")
    assert res.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(res.content))
    assert z.namelist() == ["diagnostics.txt"]


async def test_oversized_file_skipped(_client) -> None:
    client, log_dir = _client
    big = log_dir / "arcreel.log.2026-05-10"
    # 写一个 101 MB 的稀疏文件（仅记录 size，避免真实磁盘占用）
    with big.open("wb") as f:
        f.seek(101 * 1024 * 1024 - 1)
        f.write(b"\0")

    res = await client.get("/api/v1/system/logs/download")
    z = zipfile.ZipFile(io.BytesIO(res.content))
    diag = z.read("diagnostics.txt").decode("utf-8")
    assert "skipped: too large" in diag
    assert big.name in diag
    assert big.name not in z.namelist()


async def test_download_requires_auth(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_USERNAME", "admin")
    monkeypatch.setenv("AUTH_PASSWORD", "hunter2")
    monkeypatch.setenv("AUTH_TOKEN_SECRET", "test-secret-32-chars-long-xxxxx")
    monkeypatch.setenv("ARCREEL_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path / "data"))
    from lib.app_data_dir import _reset_for_tests
    _reset_for_tests()

    import importlib

    from server import app as app_module

    importlib.reload(app_module)

    transport = ASGITransport(app=app_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/system/logs/download")
        assert res.status_code == 401
```

- [ ] **Step 3.2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_system_logs_router.py -v`
Expected: 全部 FAIL — 端点 404

- [ ] **Step 3.3: 实现 `server/routers/system.py`**

```python
"""系统级端点：诊断日志打包下载。"""

from __future__ import annotations

import logging
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from lib.logging_config import _resolve_log_dir
from server.auth import CurrentUser
from server.services.diagnostics import collect_diagnostics

router = APIRouter()
logger = logging.getLogger(__name__)

_MAX_FILE_BYTES = 100 * 1024 * 1024  # 100 MB
_SPOOL_MAX = 50 * 1024 * 1024  # 50 MB —— 多数日志包都能留在内存
_LOG_GLOB = "arcreel.log*"


@router.get("/system/logs/download")
async def download_logs(_user: CurrentUser) -> StreamingResponse:
    """打包返回 logs/ 目录所有文件 + diagnostics.txt。"""
    log_dir = _resolve_log_dir()
    diagnostics_lines: list[str] = []

    spooled = tempfile.SpooledTemporaryFile(max_size=_SPOOL_MAX)
    with zipfile.ZipFile(spooled, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if log_dir.exists():
            for path in sorted(log_dir.glob(_LOG_GLOB)):
                if not path.is_file():
                    continue
                size = path.stat().st_size
                if size > _MAX_FILE_BYTES:
                    diagnostics_lines.append(
                        f"[skipped: too large: {path.name} ({size} bytes)]"
                    )
                    continue
                zf.write(path, arcname=f"logs/{path.name}")

        diagnostics_text = collect_diagnostics()
        if diagnostics_lines:
            diagnostics_text += "\n" + "\n".join(diagnostics_lines) + "\n"
        zf.writestr("diagnostics.txt", diagnostics_text)

    spooled.seek(0)
    ts = datetime.now().strftime("%Y-%m-%d-%H%M")
    filename = f"arcreel-diagnostics-{ts}.zip"

    def _iter():
        try:
            while chunk := spooled.read(64 * 1024):
                yield chunk
        finally:
            spooled.close()

    return StreamingResponse(
        _iter(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
```

- [ ] **Step 3.4: 注册 router 到 `server/app.py`**

定位到现有 `app.include_router(...)` 集中区域（约 line 508 起），在合适位置插入：

```python
from server.routers import system as system_router  # 在 import 区域
# ...
app.include_router(system_router.router, prefix="/api/v1", tags=["系统"])
```

具体位置：紧跟 `auth_router.router` 之后或同类 router 旁边。

- [ ] **Step 3.5: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_system_logs_router.py -v`
Expected: 6 passed

- [ ] **Step 3.6: 跑整套后端测试确认无回归**

Run: `uv run python -m pytest tests/test_logging_persistence.py tests/test_diagnostics_service.py tests/test_system_logs_router.py -v`
Expected: 全部通过

- [ ] **Step 3.7: lint + 类型检查**

```bash
uv run ruff check server/routers/system.py server/app.py tests/test_system_logs_router.py
uv run ruff format server/routers/system.py server/app.py tests/test_system_logs_router.py
uv run basedpyright server/routers/system.py server/app.py tests/test_system_logs_router.py
```
Expected: 0 error

- [ ] **Step 3.8: commit**

```bash
git add server/routers/system.py server/app.py tests/test_system_logs_router.py
git commit -m "feat(api): GET /system/logs/download returns diagnostics zip"
```

---

## Task 4: 前端 — API 方法 + Settings 按钮 + i18n

**Files:**
- Modify: `frontend/src/api.ts`（加 `downloadDiagnostics()` 方法）
- Modify: `frontend/src/components/pages/settings/AboutSection.tsx`（加按钮）
- Modify: `frontend/src/i18n/zh/dashboard.ts`
- Modify: `frontend/src/i18n/en/dashboard.ts`
- Modify: `frontend/src/i18n/vi/dashboard.ts`

- [ ] **Step 4.1: 三语 i18n key（zh）**

在 `frontend/src/i18n/zh/dashboard.ts` 现有 `about_*` key 之后追加：

```ts
  'diagnostics_section_title': '诊断日志',
  'diagnostics_section_desc': '打包最近 7 天的日志与系统信息（已脱敏 API 密钥），用于反馈 bug。',
  'diagnostics_download': '下载诊断日志',
  'diagnostics_downloading': '打包中…',
  'diagnostics_download_failed': '下载失败：{{error}}',
```

- [ ] **Step 4.2: 三语 i18n key（en）**

在 `frontend/src/i18n/en/dashboard.ts` 同位置追加：

```ts
  'diagnostics_section_title': 'Diagnostic logs',
  'diagnostics_section_desc': 'Bundle the last 7 days of logs and system info (API keys masked) for bug reports.',
  'diagnostics_download': 'Download diagnostic logs',
  'diagnostics_downloading': 'Packaging…',
  'diagnostics_download_failed': 'Download failed: {{error}}',
```

- [ ] **Step 4.3: 三语 i18n key（vi）**

在 `frontend/src/i18n/vi/dashboard.ts` 同位置追加：

```ts
  'diagnostics_section_title': 'Nhật ký chẩn đoán',
  'diagnostics_section_desc': 'Đóng gói nhật ký 7 ngày gần nhất và thông tin hệ thống (API key đã ẩn) để báo lỗi.',
  'diagnostics_download': 'Tải nhật ký chẩn đoán',
  'diagnostics_downloading': 'Đang đóng gói…',
  'diagnostics_download_failed': 'Tải về thất bại: {{error}}',
```

- [ ] **Step 4.4: 加 API 方法**

在 `frontend/src/api.ts` 的 `class API` 内合适位置（参考其他 GET 方法风格）加：

```ts
async downloadDiagnostics(): Promise<{ blob: Blob; filename: string }> {
  const response = await fetch(
    `${API_BASE}/system/logs/download`,
    withAuth({ method: "GET" })
  );
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${await response.text()}`);
  }
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const match = disposition.match(/filename="?([^";]+)"?/);
  const filename = match?.[1] ?? "arcreel-diagnostics.zip";
  const blob = await response.blob();
  return { blob, filename };
}
```

- [ ] **Step 4.5: 在 `AboutSection.tsx` 加按钮**

在 `AboutSection.tsx` 现有版本区块之后，函数 return 内追加一个 section：

```tsx
const [downloading, setDownloading] = useState(false);
const [downloadError, setDownloadError] = useState<string | null>(null);

const handleDownloadDiagnostics = useCallback(async () => {
  setDownloading(true);
  setDownloadError(null);
  try {
    const { blob, filename } = await API.downloadDiagnostics();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (err) {
    setDownloadError(err instanceof Error ? err.message : String(err));
  } finally {
    setDownloading(false);
  }
}, []);
```

JSX 部分（放在版本区块之后）：

```tsx
<section className={CARD_STYLE}>
  <h3 className="text-base font-semibold">{t("diagnostics_section_title")}</h3>
  <p className="text-sm text-neutral-400 mt-1">{t("diagnostics_section_desc")}</p>
  <button
    type="button"
    onClick={handleDownloadDiagnostics}
    disabled={downloading}
    className={GHOST_BTN_LG_CLS + " mt-3"}
  >
    {downloading ? t("diagnostics_downloading") : t("diagnostics_download")}
  </button>
  {downloadError && (
    <p className="text-sm text-red-400 mt-2">
      {t("diagnostics_download_failed", { error: downloadError })}
    </p>
  )}
</section>
```

注意：`useCallback` 已在现有 imports 中；`useState` 也在；新加的 `API` 调用沿用现有 `import { API } from "@/api"`。如果 ESLint 抱怨 hook 必须在最顶层声明，把 `downloading` / `downloadError` / `handleDownloadDiagnostics` 放在现有 hooks 之后、return 之前。

- [ ] **Step 4.6: 前端 lint + typecheck**

```bash
cd frontend && pnpm lint && pnpm check
```
Expected: 0 error。

如果 `pnpm check` 报 i18n 类型不一致（dashboard.ts 之间 key 差异），把缺的 key 补齐即可。

- [ ] **Step 4.7: commit**

```bash
cd ..
git add frontend/src/api.ts frontend/src/components/pages/settings/AboutSection.tsx frontend/src/i18n/zh/dashboard.ts frontend/src/i18n/en/dashboard.ts frontend/src/i18n/vi/dashboard.ts
git commit -m "feat(frontend): download diagnostics button in Settings"
```

---

## Task 5: 环境变量样例 + CHANGELOG

**Files:**
- Modify: `.env.example`
- Modify: `CHANGELOG.md`（如存在；否则跳过）

- [ ] **Step 5.1: 编辑 `.env.example`**

定位现有 `# Logging` 区块（约第 80 行附近，含 `# LOG_LEVEL=INFO`），在 `LOG_LEVEL` 注释下追加：

```bash

# Log file directory (default: $ARCREEL_DATA_DIR/logs)
# Relative paths resolve against PROJECT_ROOT.
# 日志文件目录（默认 $ARCREEL_DATA_DIR/logs），相对路径基于 PROJECT_ROOT。
# ARCREEL_LOG_DIR=

# Disable file logging (default: false). When set to 1/true/yes, logs go only to stdout.
# 关闭文件日志（默认 false）。设为 1/true/yes 时日志仅输出到 stdout。
# ARCREEL_LOG_FILE_DISABLED=
```

- [ ] **Step 5.2: 编辑 `CHANGELOG.md`**

Run: `ls CHANGELOG.md 2>/dev/null && echo exists`

如果存在，在最新「Unreleased」或顶部新区块加一行：

```markdown
### Added
- 日志按天落盘到 `$ARCREEL_DATA_DIR/logs/`，保留 7 天；Settings → 关于页面新增「下载诊断日志」按钮可一键打包日志 + 系统信息 zip
```

如果文件不存在，跳过此步。

- [ ] **Step 5.3: commit**

```bash
git add .env.example CHANGELOG.md
git commit -m "docs(logging): document file logging env vars and changelog"
```

如果 CHANGELOG 不存在，只 add `.env.example`：

```bash
git add .env.example
git commit -m "docs(logging): document file logging env vars"
```

---

## 最终验证

- [ ] **Step F.1: 全量后端测试**

```bash
uv run python -m pytest -q
```
Expected: 全绿，无回归

- [ ] **Step F.2: 全量后端 lint + 类型检查**

```bash
uv run ruff check .
uv run basedpyright
```
Expected: 0 error

- [ ] **Step F.3: 前端 lint + typecheck**

```bash
cd frontend && pnpm lint && pnpm check
```
Expected: 0 error

- [ ] **Step F.4: 手动 smoke test（本地启服务）**

```bash
uv run uvicorn server.app:app --reload --reload-dir server --reload-dir lib --port 1241
```

- 启动后检查 `$ARCREEL_DATA_DIR/logs/arcreel.log` 出现，且包含启动日志
- 在前端登录后打开 Settings → 关于 → 点击「下载诊断日志」，浏览器应直接下载一个 zip
- 解压检查：
  - `logs/arcreel.log` 内容齐全
  - `diagnostics.txt` 字段完整且 secret 已脱敏

- [ ] **Step F.5: 准备 PR（不执行 push）**

```bash
git log --oneline main..HEAD
```
Expected: 看到 5–6 个细粒度 commit。等待用户决定何时 push 和提 PR。

---

## 已知限制

- 单 worker 假设：plan 不处理多 uvicorn worker 同时写同一 log 文件的锁问题。若未来部署需要多 worker，把 `TimedRotatingFileHandler` 换成 `concurrent-log-handler` 即可，对调用方零感知
- 沙箱：日志目录在 `app_data_dir()` 之下已是 bwrap 白名单的写路径，不需额外声明
- Windows：单进程 + 显式 `encoding="utf-8"` 足够；多 worker 不在范围

## 自查清单（实施前确认）

- [ ] spec 中所有「目标」都映射到 Task 1–5
- [ ] spec 中所有「错误处理」场景都对应到 Task 1/2/3 的测试用例
- [ ] spec 中所有「测试」表格条目都在对应 Task 的测试代码块出现
- [ ] 每个 Task 都以 commit 收尾（频繁 commit）
- [ ] 没有 TODO / TBD / "..." 等占位符
