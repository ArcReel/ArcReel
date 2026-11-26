#!/usr/bin/env python3
"""启动、查询与回收本地隔离的 ArcReel 验证实例。

    uv run python scripts/local_instance.py start --port 18159
    uv run python scripts/local_instance.py status --data-dir <DATA_DIR>
    uv run python scripts/local_instance.py stop --data-dir <DATA_DIR>

后端在同一端口托管 frontend/dist，默认使用独立临时数据目录、关闭 .env 加载并剥离父进程供应商密钥。
"""

from __future__ import annotations

import argparse
import contextlib
import ipaddress
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import psutil

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIST_INDEX = Path("frontend/dist/index.html")
SENTINEL_NAME = ".arcreel-local-instance"
STATE_FILENAME = "instance.json"
LOG_FILENAME = "backend.log"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_WAIT_TIMEOUT = 60.0
DEFAULT_STOP_TIMEOUT = 10.0


def tool_env(base_env: Mapping[str, str] | None = None) -> dict[str, str]:
    source = os.environ if base_env is None else base_env
    keys = ("PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL")
    return {key: source[key] for key in keys if key in source}


def prepare_data_dir(data_dir: Path | None = None) -> Path:
    """创建或校验带实例哨兵的数据目录，拒绝非空且无哨兵的外部目录。"""
    if data_dir is None:
        target = Path(tempfile.mkdtemp(prefix="arcreel-local-instance-")).resolve()
    else:
        target = data_dir.expanduser().resolve()
        target.mkdir(parents=True, exist_ok=True)
    sentinel = target / SENTINEL_NAME
    if not sentinel.is_file():
        if any(target.iterdir()):
            raise RuntimeError(f"数据目录 {target} 非空且缺少实例哨兵 {SENTINEL_NAME}，拒绝使用")
        sentinel.write_text("arcreel-local-instance\n", encoding="utf-8")
    return target


def cleanup_data_dir(data_dir: Path) -> None:
    """校验实例哨兵后递归清理数据目录。"""
    target = data_dir.expanduser().resolve()
    if not target.exists():
        return
    sentinel = target / SENTINEL_NAME
    if not sentinel.is_file():
        raise RuntimeError(f"数据目录 {target} 缺少实例哨兵 {SENTINEL_NAME}，拒绝删除")
    shutil.rmtree(target)


def is_port_free(port: int, host: str = DEFAULT_HOST) -> bool:
    """检查指定回环地址与端口是否已无监听且可重新绑定。"""
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
        probe.settimeout(0.2)
        if probe.connect_ex((host, port)) == 0:
            return False
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
        return True


def pick_port(port: int | None = None, host: str = DEFAULT_HOST) -> int:
    """校验显式给定的空闲端口，或由内核分配空闲端口。"""
    if port is not None and not is_port_free(port, host):
        raise RuntimeError(f"端口 {host}:{port} 已被占用")
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, 0 if port is None else port))
        except OSError as exc:
            raise RuntimeError(f"端口 {host}:{port} 无法绑定：{exc}") from exc
        return int(sock.getsockname()[1])


def build_backend_env(
    data_dir: Path,
    *,
    auth_enabled: bool = False,
    username: str = "admin",
    password: str | None = None,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """构造隔离的后端进程环境变量，移除父进程数据库与供应商密钥。"""
    env = tool_env(base_env)
    env.update(
        ARCREEL_DATA_DIR=str(data_dir / "data"),
        HOME=str(data_dir),
        USERPROFILE=str(data_dir),
        CLAUDE_CONFIG_DIR=str(data_dir / ".claude"),
        ARCREEL_SKIP_LEGACY_LOG_MIGRATION="1",
        PYTHON_DOTENV_DISABLED="1",
        ARCREEL_SKIP_CATALOG_SYNC="1",
        AUTH_ENABLED="true" if auth_enabled else "false",
    )
    if auth_enabled:
        env["AUTH_USERNAME"] = username
        env["AUTH_PASSWORD"] = password or "arcreel-local-verify"
    else:
        env.pop("AUTH_USERNAME", None)
        env.pop("AUTH_PASSWORD", None)
    return env


def ensure_frontend_dist(
    root: Path = ROOT,
    *,
    skip_build: bool = False,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> Path:
    """构建或校验 frontend/dist/index.html。"""
    index_file = root / FRONTEND_DIST_INDEX
    if not skip_build:
        pnpm = shutil.which("pnpm") or "pnpm"
        completed = runner(
            (pnpm, "--dir", str(root / "frontend"), "exec", "vite", "build"),
            cwd=root,
            env=tool_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(f"构建 frontend/dist 失败：\n{detail}")
    if not index_file.is_file():
        raise RuntimeError(f"缺少前端构建产物 {index_file}，请去掉 --skip-build 构建前端")
    return index_file


def fetch_health(
    base_url: str,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
    timeout: float = 2.0,
) -> dict[str, Any] | None:
    """请求 /health，返回 status=ok 的响应字典；未就绪时返回 None。"""
    try:
        with opener(f"{base_url}/health", timeout=timeout) as response:
            if getattr(response, "status", 200) != 200:
                return None
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    if isinstance(payload, dict) and payload.get("status") == "ok":
        return payload
    return None


def wait_until_healthy(
    base_url: str,
    *,
    process: Any | None = None,
    timeout: float = DEFAULT_WAIT_TIMEOUT,
    interval: float = 0.25,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    """轮询 /health 直到服务就绪、进程提前退出或超时。"""
    deadline = clock() + timeout
    while True:
        payload = fetch_health(base_url, opener=opener)
        if process is not None and process.poll() is not None:
            raise RuntimeError(f"后端进程提前退出，退出码 {process.returncode}")
        if payload is not None:
            return payload
        if clock() >= deadline:
            raise TimeoutError(f"等待 {base_url}/health 就绪超时（{timeout}s）")
        sleeper(interval)


def resolve_state_file(data_dir: Path | None = None, state_file: Path | None = None) -> Path:
    """解析实例状态文件路径。"""
    if state_file is not None:
        return state_file.expanduser().resolve()
    if data_dir is not None:
        return data_dir.expanduser().resolve() / STATE_FILENAME
    raise ValueError("需要指定 --data-dir 或 --state-file")


def read_log_tail(log_file: Path, max_lines: int = 40) -> str:
    """读取后端日志末尾用于启动失败诊断。"""
    try:
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-max_lines:])


def owned_process(state: dict[str, Any]) -> psutil.Process | None:
    if "created_at" not in state:
        raise RuntimeError("实例状态缺少进程身份，拒绝操作")
    try:
        process = psutil.Process(int(state["pid"]))
        if process.create_time() == state["created_at"] and process.status() != psutil.STATUS_ZOMBIE:
            return process
    except psutil.NoSuchProcess:
        pass
    return None


def start_instance(
    *,
    root: Path = ROOT,
    host: str = DEFAULT_HOST,
    port: int | None = None,
    data_dir: Path | None = None,
    state_file: Path | None = None,
    auth_enabled: bool = False,
    username: str = "admin",
    password: str | None = None,
    skip_build: bool = False,
    wait_timeout: float = DEFAULT_WAIT_TIMEOUT,
    keep_on_failure: bool = False,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    popen: Callable[..., Any] = subprocess.Popen,
    opener: Callable[..., Any] = urllib.request.urlopen,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    port_picker: Callable[[int | None, str], int] = pick_port,
) -> dict[str, Any]:
    """启动本地隔离实例，等待 /health 就绪并写入状态文件。"""
    if host == "localhost":
        host = DEFAULT_HOST
    if not ipaddress.IPv4Address(host).is_loopback:
        raise ValueError("本地验证实例仅支持 IPv4 回环地址")
    ensure_frontend_dist(root, skip_build=skip_build, runner=runner)
    chosen_port = port_picker(port, host)
    new_dir = data_dir is None or not data_dir.expanduser().exists()
    target_dir = prepare_data_dir(data_dir)
    target_state = resolve_state_file(target_dir, state_file)
    log_file = target_dir / LOG_FILENAME
    env = build_backend_env(
        target_dir,
        auth_enabled=auth_enabled,
        username=username,
        password=password,
    )
    base_url = f"http://{host}:{chosen_port}"
    default_state = target_dir / STATE_FILENAME
    reserved: list[Path] = []
    process = None
    try:
        for path in dict.fromkeys((default_state, target_state)):
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8"):
                reserved.append(path)
        with log_file.open("w", encoding="utf-8") as log_handle:
            process = popen(
                (sys.executable, "-m", "uvicorn", "server.app:app", "--host", host, "--port", str(chosen_port)),
                cwd=root,
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        created_at = psutil.Process(process.pid).create_time()
        wait_until_healthy(base_url, process=process, timeout=wait_timeout, clock=clock, sleeper=sleeper, opener=opener)
        state = {
            "pid": int(process.pid),
            "created_at": created_at,
            "host": host,
            "port": chosen_port,
            "base_url": base_url,
            "data_dir": str(target_dir),
            "log_file": str(log_file),
            "state_file": str(target_state),
            "auth_enabled": auth_enabled,
            "username": username if auth_enabled else None,
        }
        for path in reserved:
            path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except BaseException as exc:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        tail = read_log_tail(log_file) if process is not None else ""
        for path in reversed(reserved):
            path.unlink(missing_ok=True)
        if not keep_on_failure and (data_dir is None or (new_dir and default_state in reserved)):
            cleanup_data_dir(target_dir)
        if isinstance(exc, Exception):
            raise RuntimeError(f"启动本地实例失败：{exc}\n{tail}") from exc
        raise
    return state


def load_instance_state(data_dir: Path | None = None, state_file: Path | None = None) -> tuple[Path, dict[str, Any]]:
    """读取实例状态文件并返回 (状态文件路径, 状态字典)。"""
    target_state = resolve_state_file(data_dir, state_file)
    if not target_state.is_file():
        raise FileNotFoundError(f"找不到实例状态文件：{target_state}")
    payload = json.loads(target_state.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"实例状态文件格式无效：{target_state}")
    return target_state, payload


def instance_status(
    *,
    data_dir: Path | None = None,
    state_file: Path | None = None,
    opener: Callable[..., Any] = urllib.request.urlopen,
    port_checker: Callable[[int, str], bool] = is_port_free,
) -> dict[str, Any]:
    """查询实例进程存活、端口监听与 /health 状态。"""
    _, state = load_instance_state(data_dir, state_file)
    host = str(state.get("host") or DEFAULT_HOST)
    port = int(state["port"])
    base_url = str(state["base_url"])
    alive = owned_process(state) is not None
    health = fetch_health(base_url, opener=opener) if alive else None
    return {
        **state,
        "process_alive": alive,
        "healthy": health is not None,
        "port_free": port_checker(port, host),
    }


def stop_instance(
    *,
    data_dir: Path | None = None,
    state_file: Path | None = None,
    keep_data_dir: bool = False,
    timeout: float = DEFAULT_STOP_TIMEOUT,
    interval: float = 0.1,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    port_checker: Callable[[int, str], bool] = is_port_free,
) -> dict[str, Any]:
    """停止实例进程、确认端口释放并按需清理带哨兵的数据目录。"""
    _, state = load_instance_state(data_dir, state_file)
    pid = int(state["pid"])
    host = str(state.get("host") or DEFAULT_HOST)
    port = int(state["port"])
    target_dir = Path(str(state["data_dir"])).expanduser().resolve()

    canonical_state = target_dir / STATE_FILENAME
    if not (target_dir / SENTINEL_NAME).is_file() or load_instance_state(target_dir)[1] != state:
        raise RuntimeError("实例状态与数据目录不一致")
    process = owned_process(state)
    if process is not None:
        with contextlib.suppress(psutil.NoSuchProcess):
            process.terminate()
            try:
                process.wait(timeout=timeout)
            except psutil.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

    port_deadline = clock() + timeout
    while not port_checker(port, host) and clock() < port_deadline:
        sleeper(interval)
    port_free = port_checker(port, host)
    alive = owned_process(state) is not None
    if alive or not port_free:
        raise RuntimeError(f"实例未干净释放：pid={pid} alive={alive}，port={host}:{port} free={port_free}")

    if not keep_data_dir:
        cleanup_data_dir(target_dir)
    else:
        canonical_state.unlink()
    external_state = Path(state["state_file"])
    if (
        external_state != canonical_state
        and external_state.is_file()
        and load_instance_state(state_file=external_state)[1] == state
    ):
        external_state.unlink()

    return {
        "stopped": True,
        "pid": pid,
        "host": host,
        "port": port,
        "port_free": port_free,
        "data_dir": str(target_dir),
        "data_dir_removed": not keep_data_dir and not target_dir.exists(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="构建或校验 frontend/dist，启动本地隔离实例并等待 /health 就绪")
    start.add_argument("--host", default=DEFAULT_HOST, help="本地 IPv4 回环地址（默认 127.0.0.1）")
    start.add_argument("--port", type=int, help="监听端口；省略时自动分配空闲端口")
    start.add_argument("--data-dir", type=Path, help="隔离数据目录；省略时自动创建临时目录")
    start.add_argument("--state-file", type=Path, help="实例状态 JSON 输出路径（默认 <data-dir>/instance.json）")
    start.add_argument("--auth", action="store_true", help="开启登录认证（默认关闭认证）")
    start.add_argument("--username", default="admin", help="开启认证时的登录用户名（默认 admin）")
    start.add_argument("--password", help="开启认证时的登录密码（默认 arcreel-local-verify）")
    start.add_argument("--skip-build", action="store_true", help="复用现有 frontend/dist，跳过 Vite 构建")
    start.add_argument(
        "--wait-timeout",
        type=float,
        default=DEFAULT_WAIT_TIMEOUT,
        help="等待 /health 就绪的超时秒数（默认 60）",
    )
    start.add_argument("--keep-on-failure", action="store_true", help="启动失败时保留数据目录与日志供排查")

    status = sub.add_parser("status", help="检查本地隔离实例的进程、端口与 /health 状态")
    status_target = status.add_mutually_exclusive_group(required=True)
    status_target.add_argument("--data-dir", type=Path, help="实例数据目录")
    status_target.add_argument("--state-file", type=Path, help="实例状态文件路径")

    stop = sub.add_parser("stop", help="停止本地隔离实例、校验端口释放并清理临时数据目录")
    stop_target = stop.add_mutually_exclusive_group(required=True)
    stop_target.add_argument("--data-dir", type=Path, help="实例数据目录")
    stop_target.add_argument("--state-file", type=Path, help="实例状态文件路径")
    stop.add_argument("--keep-data-dir", action="store_true", help="停止进程但保留数据目录与日志")
    stop.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_STOP_TIMEOUT,
        help="等待进程退出与端口释放的超时秒数（默认 10）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "start":
            result = start_instance(
                host=args.host,
                port=args.port,
                data_dir=args.data_dir,
                state_file=args.state_file,
                auth_enabled=args.auth,
                username=args.username,
                password=args.password,
                skip_build=args.skip_build,
                wait_timeout=args.wait_timeout,
                keep_on_failure=args.keep_on_failure,
            )
            print(json.dumps(result, ensure_ascii=False))
            return 0
        if args.command == "status":
            result = instance_status(data_dir=args.data_dir, state_file=args.state_file)
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result["healthy"] else 1
        result = stop_instance(
            data_dir=args.data_dir,
            state_file=args.state_file,
            keep_data_dir=args.keep_data_dir,
            timeout=args.timeout,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (FileNotFoundError, RuntimeError, TimeoutError, ValueError) as exc:
        print(f"[local_instance] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
