"""固化 agent_runtime_profile/.claude/skills 下 4 个脚本的 cwd 路径围栏契约。

约束：
- cwd 必须含 project.json，否则脚本拒绝执行
- split_episode / peek_split_point：source 必须在 cwd/source/ 内
- split_episode：output 不跟随 source.parent，强制落在 cwd/source/
- compose_video：narration / reference_video 模式给友好错误，不是 KeyError
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
SPLIT_EPISODE = SKILLS_ROOT / "manage-project" / "scripts" / "split_episode.py"
PEEK_SPLIT = SKILLS_ROOT / "manage-project" / "scripts" / "peek_split_point.py"
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
        # 写一段足够长的中文文本，保证有有效字符可被 peek_split_point 切到
        "第一章 春日清晨。\n少年走在山路上。\n" * 200,
        encoding="utf-8",
    )
    return project_dir


# ---------- split_episode.py ----------


def test_split_episode_rejects_non_project_cwd(tmp_path: Path) -> None:
    """cwd=projects 根（无 project.json）→ 拒绝。"""
    result = _run(
        SPLIT_EPISODE,
        tmp_path,
        "--source",
        "any/path.txt",
        "--episode",
        "1",
        "--target",
        "100",
        "--anchor",
        "x",
        "--dry-run",
    )
    assert result.returncode != 0
    assert "必须在项目目录内运行" in result.stderr


def test_split_episode_rejects_source_outside_source_dir(fake_project: Path) -> None:
    """传 cwd 内但不在 source/ 子目录的路径 → 拒绝。"""
    # 在项目根放一个 .txt，但不在 source/
    (fake_project / "novel.txt").write_text("x" * 100, encoding="utf-8")
    result = _run(
        SPLIT_EPISODE,
        fake_project,
        "--source",
        "novel.txt",
        "--episode",
        "1",
        "--target",
        "10",
        "--anchor",
        "x",
        "--dry-run",
    )
    assert result.returncode != 0
    assert "源文件必须位于" in result.stderr


def test_split_episode_rejects_absolute_path_outside(fake_project: Path) -> None:
    """传 /etc/passwd 这类绝对路径 → 拒绝（不在 source_dir 内）。"""
    result = _run(
        SPLIT_EPISODE,
        fake_project,
        "--source",
        "/etc/passwd",
        "--episode",
        "1",
        "--target",
        "10",
        "--anchor",
        "x",
        "--dry-run",
    )
    assert result.returncode != 0
    assert "源文件必须位于" in result.stderr


def test_split_episode_accepts_source_in_project(fake_project: Path) -> None:
    """合法路径 source/novel.txt 应通过 cwd 校验，进入业务逻辑。"""
    result = _run(
        SPLIT_EPISODE,
        fake_project,
        "--source",
        "source/novel.txt",
        "--episode",
        "1",
        "--target",
        "100",
        "--anchor",
        "山路",
        "--dry-run",
    )
    # dry-run 走完正常路径就是 returncode=0
    assert result.returncode == 0, result.stderr
    # 关键不变量：不能因 cwd 校验失败
    assert "必须在项目目录内运行" not in result.stderr
    assert "源文件必须位于" not in result.stderr


def test_split_episode_rejects_source_symlink(fake_project: Path, tmp_path: Path) -> None:
    """cwd/source 是软链接时直接拒绝（防御 codex P1 报告的项目外写漏洞）。

    复现：用 tmp_path/external 模拟项目外目录，把 fake_project/source 替换成
    指向 external 的 symlink，再传 --source source/novel.txt——若放行，
    episode_1.txt 会写到 tmp_path/external。
    """
    # 替换 source/ 为指向项目外目录的 symlink
    external = tmp_path / "external"
    external.mkdir()
    (external / "novel.txt").write_text("外部内容" * 50, encoding="utf-8")
    shutil.rmtree(fake_project / "source")
    (fake_project / "source").symlink_to(external)

    result = _run(
        SPLIT_EPISODE,
        fake_project,
        "--source",
        "source/novel.txt",
        "--episode",
        "1",
        "--target",
        "10",
        "--anchor",
        "外部",
        "--dry-run",
    )
    assert result.returncode != 0
    assert "不能是符号链接" in result.stderr


def test_split_episode_rejects_source_dir(fake_project: Path) -> None:
    """--source 指向目录时应在校验阶段拒绝，不进入 read_text() 才崩。"""
    (fake_project / "source" / "subdir").mkdir()
    result = _run(
        SPLIT_EPISODE,
        fake_project,
        "--source",
        "source/subdir",
        "--episode",
        "1",
        "--target",
        "10",
        "--anchor",
        "x",
        "--dry-run",
    )
    assert result.returncode != 0
    assert "不存在或不是普通文件" in result.stderr


def test_split_episode_output_lands_in_source_dir(fake_project: Path) -> None:
    """实际写入时 output 必须落在 cwd/source/，不跟随 source.parent。

    构造 source/novel.txt，确认非 dry-run 模式下 episode_1.txt 和
    _remaining.txt 都生成在 source/ 目录内。
    """
    result = _run(
        SPLIT_EPISODE,
        fake_project,
        "--source",
        "source/novel.txt",
        "--episode",
        "1",
        "--target",
        "100",
        "--anchor",
        "山路",
    )
    assert result.returncode == 0, result.stderr
    assert (fake_project / "source" / "episode_1.txt").is_file()
    assert (fake_project / "source" / "_remaining.txt").is_file()
    # projects 根（fake_project.parent）不应留任何孤儿
    assert not (fake_project.parent / "episode_1.txt").exists()
    assert not (fake_project.parent / "_remaining.txt").exists()


# ---------- peek_split_point.py ----------


def test_peek_split_point_rejects_non_project_cwd(tmp_path: Path) -> None:
    result = _run(PEEK_SPLIT, tmp_path, "--source", "source/x.txt", "--target", "100")
    assert result.returncode != 0
    assert "必须在项目目录内运行" in result.stderr


def test_peek_split_point_rejects_source_outside(fake_project: Path) -> None:
    (fake_project / "novel.txt").write_text("x" * 100, encoding="utf-8")
    result = _run(PEEK_SPLIT, fake_project, "--source", "novel.txt", "--target", "10")
    assert result.returncode != 0
    assert "源文件必须位于" in result.stderr


def test_peek_split_point_rejects_source_symlink(fake_project: Path, tmp_path: Path) -> None:
    """cwd/source 是软链接时拒绝（codex P2：阻止探测项目外文件内容）。"""
    external = tmp_path / "external"
    external.mkdir()
    (external / "novel.txt").write_text("外部内容" * 50, encoding="utf-8")
    shutil.rmtree(fake_project / "source")
    (fake_project / "source").symlink_to(external)

    result = _run(
        PEEK_SPLIT,
        fake_project,
        "--source",
        "source/novel.txt",
        "--target",
        "10",
    )
    assert result.returncode != 0
    assert "不能是符号链接" in result.stderr


def test_peek_split_point_rejects_source_dir(fake_project: Path) -> None:
    """--source 指向目录时应在校验阶段拒绝，不进入 read_text() 才崩。"""
    (fake_project / "source" / "subdir").mkdir()
    result = _run(PEEK_SPLIT, fake_project, "--source", "source/subdir", "--target", "10")
    assert result.returncode != 0
    assert "不存在或不是普通文件" in result.stderr


def test_peek_split_point_accepts_source_in_project(fake_project: Path) -> None:
    result = _run(PEEK_SPLIT, fake_project, "--source", "source/novel.txt", "--target", "100")
    assert result.returncode == 0, result.stderr


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


# ---------- split_episode --episode 正整数校验 ----------


def test_split_episode_rejects_negative_episode(fake_project: Path) -> None:
    """--episode -1 应当被 argparse 直接拒绝（不生成 episode_-1.txt）。"""
    result = _run(
        SPLIT_EPISODE,
        fake_project,
        "--source",
        "source/novel.txt",
        "--episode",
        "-1",
        "--target",
        "100",
        "--anchor",
        "x",
        "--dry-run",
    )
    assert result.returncode != 0
    assert "正整数" in (result.stdout + result.stderr)


def test_split_episode_rejects_zero_episode(fake_project: Path) -> None:
    """--episode 0 同样拒绝。"""
    result = _run(
        SPLIT_EPISODE,
        fake_project,
        "--source",
        "source/novel.txt",
        "--episode",
        "0",
        "--target",
        "100",
        "--anchor",
        "x",
        "--dry-run",
    )
    assert result.returncode != 0
    assert "正整数" in (result.stdout + result.stderr)
