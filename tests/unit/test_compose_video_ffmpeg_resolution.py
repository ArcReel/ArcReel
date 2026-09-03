"""compose_video.py 的 ffmpeg / ffprobe 跨平台定位与子进程 UTF-8 解码契约。

覆盖：

- `resolve_ffmpeg_tools`：PATH 命中、PATH 未命中但回退位命中、全部未命中报错三分支；
  替身经关键字参数 seam（`which` / `is_executable` / `candidate_dirs`）注入
- `candidate_ffmpeg_dirs`：按平台取模板、`%VAR%` 展开、变量缺失整条丢弃、去重
- 脚本内 `subprocess.run` 唯一且强制 `encoding="utf-8", errors="replace"`
"""

from __future__ import annotations

import ast
import importlib.util
import shutil
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = (
    REPO_ROOT / "agent_runtime_profile" / ".claude" / "skills" / "compose-video" / "scripts" / "compose_video.py"
)

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_module():
    """以独立模块名加载脚本，避免和别处的 compose_video 冲突。"""
    spec = importlib.util.spec_from_file_location("_compose_video_ffmpeg_under_test", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compose_video = _load_module()

_FFMPEG_AVAILABLE = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


@pytest.fixture(autouse=True)
def reset_compose_video_ffmpeg_cache() -> Iterator[None]:
    compose_video.reset_ffmpeg_tools_for_tests()
    yield
    compose_video.reset_ffmpeg_tools_for_tests()


def _which_hit(mapping: dict[str, str]) -> Callable[[str], str | None]:
    """PATH 替身：按名字返回路径，未收录返回 None。"""
    return lambda name: mapping.get(name)


def _never_executable(_path: Path) -> bool:
    return False


# ---------------------------------------------------------------------------
# resolve_ffmpeg_tools
# ---------------------------------------------------------------------------


class TestResolveFfmpegTools:
    def test_path_hit_returns_which_results(self) -> None:
        """PATH 命中时直接采用 which 的结果，不再探测回退位。"""
        ffmpeg, ffprobe = compose_video.resolve_ffmpeg_tools(
            which=_which_hit({"ffmpeg": "/usr/bin/ffmpeg", "ffprobe": "/usr/bin/ffprobe"}),
            is_executable=_never_executable,
            candidate_dirs=(Path("/nonexistent-fallback"),),
            platform="linux",
        )
        assert (ffmpeg, ffprobe) == ("/usr/bin/ffmpeg", "/usr/bin/ffprobe")

    def test_fallback_dir_hit_when_path_misses(self, tmp_path: Path) -> None:
        """PATH 未命中时，从探测目录里取到可执行文件的绝对路径。"""
        bin_dir = tmp_path / "ffmpeg" / "bin"
        bin_dir.mkdir(parents=True)
        executables = {bin_dir / "ffmpeg", bin_dir / "ffprobe"}
        for path in executables:
            path.write_text("", encoding="utf-8")

        ffmpeg, ffprobe = compose_video.resolve_ffmpeg_tools(
            which=lambda _name: None,
            is_executable=lambda path: path in executables,
            candidate_dirs=(tmp_path / "absent", bin_dir),
            platform="linux",
        )
        assert ffmpeg == str(bin_dir / "ffmpeg")
        assert ffprobe == str(bin_dir / "ffprobe")

    def test_windows_fallback_matches_exe_suffix(self, tmp_path: Path) -> None:
        """win32 下优先匹配 .exe 后缀的可执行文件。"""
        executables = {tmp_path / "ffmpeg.exe", tmp_path / "ffprobe.exe"}

        ffmpeg, ffprobe = compose_video.resolve_ffmpeg_tools(
            which=lambda _name: None,
            is_executable=lambda path: path in executables,
            candidate_dirs=(tmp_path,),
            platform="win32",
        )
        assert ffmpeg == str(tmp_path / "ffmpeg.exe")
        assert ffprobe == str(tmp_path / "ffprobe.exe")

    def test_all_missing_raises_with_install_hints(self, tmp_path: Path) -> None:
        """全部未命中时报错要同时给出已探测目录与各平台安装提示。"""
        with pytest.raises(RuntimeError) as excinfo:
            compose_video.resolve_ffmpeg_tools(
                which=lambda _name: None,
                is_executable=_never_executable,
                candidate_dirs=(tmp_path / "nowhere",),
                platform="linux",
            )

        message = str(excinfo.value)
        assert "ffmpeg" in message
        assert str(tmp_path / "nowhere") in message
        for hint in compose_video.FFMPEG_INSTALL_HINTS:
            assert hint in message

    def test_missing_ffprobe_alone_still_raises(self, tmp_path: Path) -> None:
        """只有 ffmpeg 可用不算满足，错误须点名缺失的 ffprobe。"""
        with pytest.raises(RuntimeError, match="ffprobe"):
            compose_video.resolve_ffmpeg_tools(
                which=_which_hit({"ffmpeg": "/usr/bin/ffmpeg"}),
                is_executable=_never_executable,
                candidate_dirs=(tmp_path,),
                platform="linux",
            )


# ---------------------------------------------------------------------------
# candidate_ffmpeg_dirs
# ---------------------------------------------------------------------------


class TestCandidateFfmpegDirs:
    def test_windows_expands_env_templates(self) -> None:
        dirs = compose_video.candidate_ffmpeg_dirs(
            platform="win32",
            environ={
                "LOCALAPPDATA": r"C:\Users\u\AppData\Local",
                "ProgramFiles": r"C:\Program Files",
                "ProgramFiles(x86)": r"C:\Program Files (x86)",
            },
        )
        rendered = {str(path) for path in dirs}
        assert any(r"C:\Users\u\AppData\Local" in item for item in rendered)
        assert any("ffmpeg" in item for item in rendered)

    def test_missing_env_var_drops_that_candidate(self) -> None:
        """%VAR% 缺失时整条模板作废，不能生成含空段的垃圾路径。"""
        dirs = compose_video.candidate_ffmpeg_dirs(platform="win32", environ={})
        rendered = [str(path) for path in dirs]
        assert rendered, "无环境变量时仍应保留字面量安装位"
        assert all("%" not in item for item in rendered)
        assert not any(item.startswith(("\\", "/")) and "AppData" in item for item in rendered)

    def test_unknown_platform_uses_default_dirs(self) -> None:
        dirs = compose_video.candidate_ffmpeg_dirs(platform="freebsd14", environ={})
        assert tuple(str(path) for path in dirs) == compose_video.FFMPEG_DEFAULT_FALLBACK_DIRS

    def test_result_is_deduplicated(self) -> None:
        dirs = compose_video.candidate_ffmpeg_dirs(
            platform="win32",
            environ={
                "LOCALAPPDATA": r"C:\ffmpeg",
                "ProgramFiles": r"C:\ffmpeg",
                "ProgramFiles(x86)": r"C:\ffmpeg",
            },
        )
        assert len(dirs) == len(set(dirs))


# ---------------------------------------------------------------------------
# 子进程解码
# ---------------------------------------------------------------------------


def _subprocess_run_calls() -> list[ast.Call]:
    tree = ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
    ]


class TestSubprocessDecoding:
    def test_single_subprocess_entry_point(self) -> None:
        """脚本内只允许一处 subprocess.run，保证解码与工具解析都走同一入口。"""
        assert len(_subprocess_run_calls()) == 1

    def test_subprocess_run_forces_utf8(self) -> None:
        """text=True 会按系统 locale（中文 Windows 为 GBK）解码，必须显式指定 UTF-8。"""
        for call in _subprocess_run_calls():
            keywords = {kw.arg: kw.value for kw in call.keywords}
            assert "text" not in keywords
            assert isinstance(keywords["encoding"], ast.Constant)
            assert keywords["encoding"].value == "utf-8"
            assert isinstance(keywords["errors"], ast.Constant)
            assert keywords["errors"].value == "replace"

    def test_subprocess_run_takes_resolved_command(self) -> None:
        """命令必须先过 resolved_command，不能把裸工具名交给 PATH。"""
        (call,) = _subprocess_run_calls()
        first_arg = call.args[0]
        assert isinstance(first_arg, ast.Call)
        assert isinstance(first_arg.func, ast.Name)
        assert first_arg.func.id == "resolved_command"

    def test_resolved_command_passes_through_non_tool_commands(self) -> None:
        cmd = [sys.executable, "-c", "pass"]
        assert compose_video.resolved_command(cmd) == cmd

    def test_empty_command_rejected(self) -> None:
        with pytest.raises(ValueError, match="命令不能为空"):
            compose_video.resolved_command([])

    @pytest.mark.skipif(not _FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe 不可用")
    def test_resolved_command_substitutes_tool_name(self) -> None:
        ffmpeg_path, ffprobe_path = compose_video.resolve_ffmpeg_tools()
        assert compose_video.resolved_command(["ffmpeg", "-version"]) == [ffmpeg_path, "-version"]
        assert compose_video.resolved_command(["ffprobe", "-version"]) == [ffprobe_path, "-version"]
        assert Path(ffmpeg_path).is_absolute()
