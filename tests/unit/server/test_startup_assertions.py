"""Startup assertions: parent-process environment must contain no provider secrets."""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib.config.env_keys import PROVIDER_SECRET_KEYS
from server.app import (
    _log_profile_sync_outcome,
    assert_no_provider_secrets_in_environ,
    check_sandbox_available,
    detect_docker_environment,
)


def _bwrap_probe_stub(returncode: int = 0, stderr: bytes = b""):
    """Build subprocess.run substitutes for bwrap probe outcomes."""

    def _stub(cmd, *args, **kwargs):
        assert cmd[0] == "bwrap"
        # Probes require unshare-user, unshare-net, and unshare-pid to detect three startup failures:
        # denied userns creation, denied loopback configuration (missing NET_ADMIN), and denied PID namespaces.
        # Missing any assertion would let accidentally weakened future probes evade detection.
        assert "--unshare-user" in cmd
        assert "--unshare-net" in cmd
        assert "--unshare-pid" in cmd
        # Pin critical probe kwargs determining nonhanging startup and diagnosable stderr:
        # without timeout, hung bwrap blocks startup forever; without capture_output, real bwrap stderr
        # cannot diagnose deployment failures. check=False routes nonzero returncodes through
        # our SANDBOX_BWRAP_BROKEN wrapper rather than CalledProcessError, which would prevent
        # _diagnose_bwrap_failure from running.
        assert kwargs["timeout"] == 5
        assert kwargs["capture_output"] is True
        assert kwargs["check"] is False
        return SimpleNamespace(returncode=returncode, stderr=stderr, stdout=b"")

    return _stub


# Share production assert_no_provider_secrets_in_environ’s source of truth to prevent
# runtime/test secret-list drift; sorted() stabilizes parametrized IDs.
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
    """Empty strings are not leaks; SDK subprocesses skip empty values after os.environ.pop."""
    _clear_secret_envs(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    assert_no_provider_secrets_in_environ()  # Empty values do not raise.


def test_claude_code_oauth_token_does_not_trigger_startup_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Claude login state clears from session environments but is not startup provider-secret material."""
    _clear_secret_envs(monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "user-login-token")
    assert_no_provider_secrets_in_environ()


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
    """Build shutil.which substitutes treating only present-set binaries as installed."""

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
    """Linux sandboxes also require socat for network proxies.

    https://code.claude.com/docs/en/sandboxing#set-up-linux-and-wsl2
    """
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
    """Stub three host sysctl reads to simulate failure topologies.

    None means absent sysctl with read_text OSError, matching mac/old kernels.
    """

    def _stub(self: Path, *args, **kwargs):
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
    """Ubuntu 24.04 kernel.apparmor_restrict_unprivileged_userns=1 must expose host-side sysctl
    repair as the root-cause path: PR #534 confirmed this on Oracle Cloud Ubuntu 24.04,
    beyond docker compose’s ability to change."""
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
    """Old kernels/hardened systems with kernel.unprivileged_userns_clone=0 receive legacy-sysctl repairs."""
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
    """Normal sysctls but failing bwrap fall back to docker compose repairs:
    seccomp/apparmor unconfined plus NET_ADMIN."""
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
    assert "Ubuntu 24.04" not in msg  # Unmatched apparmor sysctls must not mislead.


@pytest.mark.parametrize(
    "exc",
    [
        subprocess.TimeoutExpired(cmd=["bwrap"], timeout=5),
        OSError("simulated bwrap exec failure"),
    ],
    ids=["timeout", "oserror"],
)
def test_sandbox_bwrap_probe_exec_failure_linux_raises(
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
) -> None:
    """Wrap subprocess.run OSError/TimeoutExpired as SANDBOX_BWRAP_BROKEN. Production catches
    ``except (OSError, subprocess.TimeoutExpired)``; exercise both classes, since timeout-only
    coverage would stay green if OSError disappeared from the tuple."""
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr("shutil.which", _linux_which_stub({"bwrap", "socat"}))

    def _raises(*args, **kwargs):
        raise exc

    monkeypatch.setattr("server.app.subprocess.run", _raises)
    with pytest.raises(RuntimeError, match="SANDBOX_BWRAP_BROKEN"):
        check_sandbox_available()


def test_sandbox_windows_warns_not_raises(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Windows SDK lacks sandbox support: return False plus warning, never raise."""
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    with caplog.at_level("WARNING", logger="server.app"):
        result = check_sandbox_available()
    assert result is False
    assert any("SANDBOX_UNSUPPORTED" in record.message for record in caplog.records)


def test_detect_docker_via_dockerenv(tmp_path) -> None:
    fake_dockerenv = tmp_path / ".dockerenv"
    fake_dockerenv.touch()
    assert detect_docker_environment(dockerenv_path=fake_dockerenv, cgroup_path=tmp_path / "nonexistent") is True


def test_detect_docker_via_cgroup(tmp_path) -> None:
    fake_cgroup = tmp_path / "cgroup"
    fake_cgroup.write_text("12:cpu:/docker/abc123\n")
    assert detect_docker_environment(dockerenv_path=tmp_path / "nope", cgroup_path=fake_cgroup) is True


def test_detect_no_docker(tmp_path) -> None:
    assert detect_docker_environment(dockerenv_path=tmp_path / "nope", cgroup_path=tmp_path / "also_nope") is False


# bool subclasses int, so ``isinstance(True, int) and True > 0`` is true; these three tests
# prevent naive isinstance startup logging from labeling abort signals as completed syncs.
def test_log_profile_sync_outcome_aborted_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stats = {
        "created": 0,
        "repaired": 0,
        "skipped": 0,
        "errors": 0,
        "failed_projects": 0,
        "aborted": True,
    }
    with caplog.at_level("WARNING", logger="server.app"):
        _log_profile_sync_outcome(stats)
    assert any("物化已中止" in r.message for r in caplog.records)
    assert not any("同步完成" in r.message for r in caplog.records)


def test_log_profile_sync_outcome_success_counts_logs_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stats = {
        "created": 3,
        "repaired": 0,
        "skipped": 0,
        "errors": 0,
        "failed_projects": 0,
        "aborted": False,
    }
    with caplog.at_level("INFO", logger="server.app"):
        _log_profile_sync_outcome(stats)
    assert any("物化完成" in r.message for r in caplog.records)


def test_log_profile_sync_outcome_all_zero_is_silent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No abort with all-zero counts emits no logs, avoiding one line on every empty startup."""
    stats = {
        "created": 0,
        "repaired": 0,
        "skipped": 0,
        "errors": 0,
        "failed_projects": 0,
        "aborted": False,
    }
    with caplog.at_level("INFO", logger="server.app"):
        _log_profile_sync_outcome(stats)
    assert caplog.records == []
