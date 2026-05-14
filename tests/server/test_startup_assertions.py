"""启动断言测试 — 父进程 env 不得含 provider 密钥。"""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib.config.env_keys import PROVIDER_SECRET_KEYS
from server.app import (
    assert_no_provider_secrets_in_environ,
    check_sandbox_available,
    detect_docker_environment,
)


def _bwrap_probe_stub(returncode: int = 0, stderr: bytes = b""):
    """构造 subprocess.run 替身，用于桩 bwrap 试跑结果。"""

    def _stub(cmd, *args, **kwargs):  # noqa: ANN001 - 测试替身，宽松签名
        assert cmd[0] == "bwrap"
        # probe 必须用 unshare-user + unshare-net 才能在启动期捕获两类典型失败：
        # userns 创建被拒 / loopback 配置被拒（缺 NET_ADMIN）。
        assert "--unshare-user" in cmd
        assert "--unshare-net" in cmd
        return SimpleNamespace(returncode=returncode, stderr=stderr, stdout=b"")

    return _stub


# 复用生产代码（assert_no_provider_secrets_in_environ）所基于的同一份真相源，
# 避免测试与运行时密钥清单漂移。sorted() 让 parametrize 测试 ID 稳定。
_SECRET_KEYS_SORTED = sorted(PROVIDER_SECRET_KEYS)


def _clear_secret_envs(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in PROVIDER_SECRET_KEYS:
        monkeypatch.delenv(k, raising=False)


def test_clean_environ_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_secret_envs(monkeypatch)
    assert_no_provider_secrets_in_environ()  # no raise


@pytest.mark.parametrize("leaked_key", _SECRET_KEYS_SORTED)
def test_any_single_secret_triggers_raise(monkeypatch: pytest.MonkeyPatch, leaked_key: str) -> None:
    _clear_secret_envs(monkeypatch)
    monkeypatch.setenv(leaked_key, "leaked-value")
    with pytest.raises(RuntimeError, match="SECURITY"):
        assert_no_provider_secrets_in_environ()


def test_empty_string_value_not_treated_as_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    """空字符串不算泄漏（os.environ.pop 后 SDK 子进程会跳过空值）。"""
    _clear_secret_envs(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    assert_no_provider_secrets_in_environ()  # 空值不 raise


def test_sandbox_available_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/sandbox-exec" if name == "sandbox-exec" else None)
    assert check_sandbox_available() is True


def test_sandbox_missing_macos_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr("shutil.which", lambda _name: None)
    with pytest.raises(RuntimeError, match="SANDBOX_UNAVAILABLE"):
        check_sandbox_available()


def _linux_which_stub(present: set[str]):
    """构造 shutil.which 替身：仅 present 集合内的 binary 视为已安装。"""

    def _stub(name: str):
        return f"/usr/bin/{name}" if name in present else None

    return _stub


def test_sandbox_available_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr("shutil.which", _linux_which_stub({"bwrap", "socat"}))
    monkeypatch.setattr("server.app.subprocess.run", _bwrap_probe_stub(returncode=0))
    assert check_sandbox_available() is True


def test_sandbox_missing_linux_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr("shutil.which", _linux_which_stub(set()))
    with pytest.raises(RuntimeError, match="bwrap, socat"):
        check_sandbox_available()


def test_sandbox_missing_socat_only_linux_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """官方 sandboxing.md 明文要求 socat 同装（网络代理需要）。"""
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr("shutil.which", _linux_which_stub({"bwrap"}))
    with pytest.raises(RuntimeError, match="missing in PATH: socat"):
        check_sandbox_available()


def _patch_sysctls(
    monkeypatch: pytest.MonkeyPatch,
    *,
    apparmor_userns: str | None = None,
    userns_clone: str | None = None,
    max_user_ns: str | None = None,
) -> None:
    """桩三条 host sysctl 读取，模拟不同的失败拓扑。

    None 表示该 sysctl 在测试系统上不存在（read_text 抛 OSError），
    与 mac / 老内核行为一致。
    """

    def _stub(self: Path, *args, **kwargs):  # noqa: ANN001 - 测试替身
        mapping = {
            "/proc/sys/kernel/apparmor_restrict_unprivileged_userns": apparmor_userns,
            "/proc/sys/kernel/unprivileged_userns_clone": userns_clone,
            "/proc/sys/user/max_user_namespaces": max_user_ns,
        }
        val = mapping.get(str(self))
        if val is None:
            raise OSError("simulated missing sysctl")
        return val

    monkeypatch.setattr("server.app.Path.read_text", _stub)


def test_sandbox_bwrap_probe_apparmor_userns_diagnoses_ubuntu_2404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ubuntu 24.04 默认 kernel.apparmor_restrict_unprivileged_userns=1 →
    异常信息必须把"在 host 上关 sysctl"作为根因路径透传，因为这是 PR #534
    实测线上 Oracle Cloud Ubuntu 24.04 撞到的真根因，docker compose 改不动。"""
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr("shutil.which", _linux_which_stub({"bwrap", "socat"}))
    monkeypatch.setattr(
        "server.app.subprocess.run",
        _bwrap_probe_stub(
            returncode=1,
            stderr=b"bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted",
        ),
    )
    _patch_sysctls(monkeypatch, apparmor_userns="1", userns_clone="1", max_user_ns="95499")
    with pytest.raises(RuntimeError, match="SANDBOX_BWRAP_BROKEN") as exc_info:
        check_sandbox_available()
    msg = str(exc_info.value)
    assert "Ubuntu 24.04" in msg
    assert "apparmor_restrict_unprivileged_userns=0" in msg
    assert "60-arcreel-bwrap.conf" in msg


def test_sandbox_bwrap_probe_userns_clone_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """老内核 / 强化系统：kernel.unprivileged_userns_clone=0 → 给老 sysctl 修复路径。"""
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr("shutil.which", _linux_which_stub({"bwrap", "socat"}))
    monkeypatch.setattr(
        "server.app.subprocess.run",
        _bwrap_probe_stub(
            returncode=1,
            stderr=b"bwrap: Creating new namespace failed",
        ),
    )
    _patch_sysctls(monkeypatch, userns_clone="0")
    with pytest.raises(RuntimeError, match="SANDBOX_BWRAP_BROKEN") as exc_info:
        check_sandbox_available()
    msg = str(exc_info.value)
    assert "unprivileged_userns_clone=1" in msg


def test_sandbox_bwrap_probe_fallback_container_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    """sysctl 都正常但 bwrap 仍跑不起来 → 兜底给出 docker compose 修复建议
    （seccomp/apparmor unconfined + NET_ADMIN）。"""
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr("shutil.which", _linux_which_stub({"bwrap", "socat"}))
    monkeypatch.setattr(
        "server.app.subprocess.run",
        _bwrap_probe_stub(returncode=1, stderr=b"bwrap: some other error"),
    )
    _patch_sysctls(monkeypatch, apparmor_userns="0", userns_clone="1", max_user_ns="15000")
    with pytest.raises(RuntimeError, match="SANDBOX_BWRAP_BROKEN") as exc_info:
        check_sandbox_available()
    msg = str(exc_info.value)
    assert "seccomp:unconfined" in msg
    assert "NET_ADMIN" in msg
    assert "Ubuntu 24.04" not in msg  # 未命中 apparmor sysctl，不应误导


def test_sandbox_bwrap_probe_oserror_linux_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """subprocess.run 抛 OSError / TimeoutExpired 时也要包成 SANDBOX_BWRAP_BROKEN。"""
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr("shutil.which", _linux_which_stub({"bwrap", "socat"}))

    def _raises(*args, **kwargs):  # noqa: ANN001 - 测试替身
        raise subprocess.TimeoutExpired(cmd=["bwrap"], timeout=5)

    monkeypatch.setattr("server.app.subprocess.run", _raises)
    with pytest.raises(RuntimeError, match="SANDBOX_BWRAP_BROKEN"):
        check_sandbox_available()


def test_sandbox_windows_warns_not_raises(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Windows 上 SDK 不支持 sandbox：返回 False + warning，不 raise。"""
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    with caplog.at_level("WARNING", logger="server.app"):
        result = check_sandbox_available()
    assert result is False
    assert any("SANDBOX_UNSUPPORTED" in record.message for record in caplog.records)


def test_detect_docker_via_dockerenv(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    fake_dockerenv = tmp_path / ".dockerenv"
    fake_dockerenv.touch()
    monkeypatch.setattr("server.app._DOCKERENV_PATH", fake_dockerenv)
    monkeypatch.setattr("server.app._CGROUP_PATH", tmp_path / "nonexistent")
    assert detect_docker_environment() is True


def test_detect_docker_via_cgroup(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    fake_cgroup = tmp_path / "cgroup"
    fake_cgroup.write_text("12:cpu:/docker/abc123\n")
    monkeypatch.setattr("server.app._DOCKERENV_PATH", tmp_path / "nope")
    monkeypatch.setattr("server.app._CGROUP_PATH", fake_cgroup)
    assert detect_docker_environment() is True


def test_detect_no_docker(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr("server.app._DOCKERENV_PATH", tmp_path / "nope")
    monkeypatch.setattr("server.app._CGROUP_PATH", tmp_path / "also_nope")
    assert detect_docker_environment() is False
