"""固化 agent_runtime_profile/.claude/skills 下 compose_video 脚本的 cwd 路径围栏契约。

约束：
- cwd 必须含 project.json，否则脚本拒绝执行
- compose_video：narration / ad / reference_video 模式给友好错误，不是 KeyError
- compose_video：--output 不能逃逸到 output/ 之外
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = REPO_ROOT / "agent_runtime_profile" / ".claude" / "skills"
COMPOSE_VIDEO = SKILLS_ROOT / "compose-video" / "scripts" / "compose_video.py"

# compose_video.main() 在进入路径围栏前会先 check_ffmpeg；CI 环境若缺 ffmpeg/ffprobe
# 会以 ffmpeg 错误直接退出，让围栏断言无法匹配。统一守护这些测试。
_FFMPEG_AVAILABLE = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
_requires_ffmpeg = pytest.mark.skipif(not _FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe 不可用")


def _run(
    script: Path,
    cwd: Path,
    *args: str,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """以指定 cwd 跑脚本，返回 CompletedProcess。"""
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
        input=stdin,
    )


@pytest.fixture
def fake_project(tmp_path: Path) -> Path:
    """构造一个最小项目目录：project.json + source/。

    模拟 projects/{name}/ 形态。cwd 切到此目录即等价于 agent session cwd。
    """
    # ProjectManager 校验项目标识仅允许英文字母 / 数字 / 中划线，所以不用下划线
    projects_root = tmp_path / "projects"
    project_dir = projects_root / "fake-proj"
    (project_dir / "source").mkdir(parents=True)
    (project_dir / "project.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "title": "fake",
                "content_mode": "narration",
                "generation_mode": "storyboard",
                "characters": {},
                "scenes": {},
                "props": {},
                "episodes": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (project_dir / "source" / "novel.txt").write_text(
        "第一章 春日清晨。\n少年走在山路上。\n" * 200,
        encoding="utf-8",
    )
    return project_dir


# ---------- compose_video.py ----------


def _write_drama_script(project_dir: Path, video_clip_exists: bool = True) -> str:
    """构造一份最小可用的 drama 模式剧本 + 视频文件，返回剧本文件名。"""
    (project_dir / "scripts").mkdir(exist_ok=True)
    (project_dir / "videos").mkdir(exist_ok=True)
    clip_rel = "videos/scene_1.mp4"
    if video_clip_exists:
        (project_dir / clip_rel).write_bytes(b"\x00" * 16)
    script = {
        "novel": {"chapter": "ep1"},
        "scenes": [
            {
                "scene_id": "E1S01",
                "generated_assets": {"video_clip": clip_rel},
            }
        ],
    }
    script_name = "episode_1.json"
    (project_dir / "scripts" / script_name).write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    return f"scripts/{script_name}"


@_requires_ffmpeg
def test_compose_video_rejects_non_project_cwd(tmp_path: Path) -> None:
    result = _run(COMPOSE_VIDEO, tmp_path, "scripts/episode_1.json")
    assert result.returncode != 0
    assert "必须在项目目录内运行" in (result.stdout + result.stderr)


@_requires_ffmpeg
def test_compose_video_rejects_narration_mode(fake_project: Path) -> None:
    """narration 模式（顶层 segments[] 无 scenes[]）应给友好错误，不是 KeyError。"""
    (fake_project / "scripts").mkdir(exist_ok=True)
    (fake_project / "scripts" / "ep_narration.json").write_text(
        json.dumps(
            {
                "novel": {"chapter": "ep1"},
                "generation_mode": "storyboard",
                "segments": [{"segment_id": "G01"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = _run(COMPOSE_VIDEO, fake_project, "scripts/ep_narration.json")
    assert result.returncode != 0
    out = result.stdout + result.stderr
    assert "仅支持 drama 模式" in out
    # 不能出现裸 KeyError
    assert "KeyError" not in out


@_requires_ffmpeg
def test_compose_video_rejects_ad_mode(fake_project: Path) -> None:
    """ad 模式（顶层 shots[] 无 scenes[]）应给友好错误并指引剪映草稿导出，不是 KeyError。"""
    (fake_project / "scripts").mkdir(exist_ok=True)
    (fake_project / "scripts" / "ep_ad.json").write_text(
        json.dumps(
            {
                "content_mode": "ad",
                "shots": [
                    {
                        "shot_id": "E1S1",
                        "section": "hook",
                        "duration_seconds": 4,
                        "voiceover_text": "还在为脱发烦恼吗",
                        "generated_assets": {"video_clip": "videos/shot_E1S1.mp4"},
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = _run(COMPOSE_VIDEO, fake_project, "scripts/ep_ad.json")
    assert result.returncode != 0
    out = result.stdout + result.stderr
    assert "仅支持 drama 模式" in out
    assert "content_mode=ad" in out
    assert "剪映草稿导出" in out
    assert "KeyError" not in out


@_requires_ffmpeg
def test_compose_video_rejects_output_escape(fake_project: Path) -> None:
    """--output 含 ../ 逃逸时应拒绝。"""
    script_arg = _write_drama_script(fake_project, video_clip_exists=True)
    result = _run(
        COMPOSE_VIDEO,
        fake_project,
        script_arg,
        "--output",
        "../../escape.mp4",
    )
    assert result.returncode != 0
    out = result.stdout + result.stderr
    assert "逃逸" in out or "escape" in out.lower()


@_requires_ffmpeg
def test_compose_video_fails_fast_on_missing_music(fake_project: Path) -> None:
    """--music 文件不存在时应立即抛错，不要静默 warning 走完拼接。

    review #8（coderabbit）：自动化场景下静默 warning 容易把失败当成功。
    校验顺序：cwd 检查 → drama 模式检查 → output / music 路径围栏 + 存在性，
    再开始拼接。music 不存在时应 fail-fast。
    """
    script_arg = _write_drama_script(fake_project, video_clip_exists=True)
    # 引用一个项目内但不存在的 BGM 文件
    result = _run(
        COMPOSE_VIDEO,
        fake_project,
        script_arg,
        "--music",
        "missing-bgm.mp3",
    )
    assert result.returncode != 0
    out = result.stdout + result.stderr
    assert "BGM 文件不存在" in out
    # 关键不变量：fail-fast — 不能让脚本进入拼接阶段
    assert "✅ 视频合成完成" not in out


@_requires_ffmpeg
def test_compose_video_rejects_video_clip_escape(fake_project: Path, tmp_path: Path) -> None:
    """剧本里 `generated_assets.video_clip` 走 `..` 逃逸时拒绝（review #12）。

    `project_dir / "../escape.mp4"` 未 resolve 时字面前缀会骗过 is_relative_to。
    resolve 后才能识别为项目外。
    """
    external = tmp_path / "escape.mp4"
    external.write_bytes(b"\x00" * 16)
    # 用相对路径形式触发字面前缀场景：从 project_dir 出发 .. 到 tmp_path
    (fake_project / "scripts").mkdir(exist_ok=True)
    script = {
        "novel": {"chapter": "ep1"},
        "scenes": [
            {
                "scene_id": "E1S01",
                "generated_assets": {"video_clip": "../escape.mp4"},
            }
        ],
    }
    (fake_project / "scripts" / "ep_escape.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    result = _run(COMPOSE_VIDEO, fake_project, "scripts/ep_escape.json")
    assert result.returncode != 0
    out = result.stdout + result.stderr
    assert "视频文件必须位于项目目录内" in out
    assert "✅ 视频合成完成" not in out


@_requires_ffmpeg
def test_compose_video_rejects_video_clip_absolute_outside(fake_project: Path, tmp_path: Path) -> None:
    """剧本里 `generated_assets.video_clip` 是项目外绝对路径时拒绝（review #12）。"""
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"\x00" * 16)
    (fake_project / "scripts").mkdir(exist_ok=True)
    script = {
        "novel": {"chapter": "ep1"},
        "scenes": [
            {
                "scene_id": "E1S01",
                "generated_assets": {"video_clip": str(outside)},
            }
        ],
    }
    (fake_project / "scripts" / "ep_abs.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    result = _run(COMPOSE_VIDEO, fake_project, "scripts/ep_abs.json")
    assert result.returncode != 0
    out = result.stdout + result.stderr
    assert "视频文件必须位于项目目录内" in out


@_requires_ffmpeg
def test_compose_video_rejects_output_symlink(fake_project: Path, tmp_path: Path) -> None:
    """project_dir/output 是符号链接时拒绝（防御 output/ 软链接绕过）。

    与 source/ symlink 拒绝对称：若 output -> /tmp/external，resolve 后
    output_dir 与 output_path 双双落到 /tmp/external，is_relative_to 会
    放行，但产物实际写到了项目目录之外。
    """
    script_arg = _write_drama_script(fake_project, video_clip_exists=True)
    external = tmp_path / "external-output"
    external.mkdir()
    (fake_project / "output").symlink_to(external)

    result = _run(
        COMPOSE_VIDEO,
        fake_project,
        script_arg,
    )
    assert result.returncode != 0
    out = result.stdout + result.stderr
    assert "output/ 不能是符号链接" in out
    assert "✅ 视频合成完成" not in out


@_requires_ffmpeg
def test_compose_video_rejects_music_dir(fake_project: Path) -> None:
    """--music 指向目录时应在校验阶段拒绝（review #9）。"""
    script_arg = _write_drama_script(fake_project, video_clip_exists=True)
    music_dir = fake_project / "bgm-dir"
    music_dir.mkdir()
    result = _run(
        COMPOSE_VIDEO,
        fake_project,
        script_arg,
        "--music",
        "bgm-dir",
    )
    assert result.returncode != 0
    out = result.stdout + result.stderr
    assert "不存在或不是普通文件" in out
    assert "✅ 视频合成完成" not in out


@_requires_ffmpeg
def test_compose_video_rejects_music_outside_project(fake_project: Path, tmp_path: Path) -> None:
    """--music 指向项目外的绝对路径时应拒绝。"""
    script_arg = _write_drama_script(fake_project, video_clip_exists=True)
    outside_music = tmp_path / "outside.mp3"
    outside_music.write_bytes(b"\x00")
    result = _run(
        COMPOSE_VIDEO,
        fake_project,
        script_arg,
        "--music",
        str(outside_music),
    )
    assert result.returncode != 0
    out = result.stdout + result.stderr
    assert "BGM 文件必须位于项目目录内" in out
