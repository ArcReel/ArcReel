"""generate_video.py reference_video 分支单测。

验证 script 是 video_units 时会走 reference 路径（task_type="reference_video"）。
"""

from __future__ import annotations

import json
from importlib import util as _iu
from pathlib import Path

# 允许直接 import skill 脚本
SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "agent_runtime_profile/.claude/skills/generate-video/scripts/generate_video.py"
)
spec = _iu.spec_from_file_location("_gvtest_generate_video", SCRIPT_PATH)
gv = _iu.module_from_spec(spec)
assert spec is not None and spec.loader is not None
spec.loader.exec_module(gv)


def _make_reference_project(tmp_path: Path) -> tuple[Path, str]:
    # 项目名只能含字母、数字和中划线（ProjectManager 限制），不能含下划线
    project_dir = tmp_path / "refproj"
    (project_dir / "videos").mkdir(parents=True)
    (project_dir / "scripts").mkdir()
    (project_dir / "reference_videos").mkdir()

    (project_dir / "project.json").write_text(
        json.dumps(
            {
                "title": "t",
                "content_mode": "narration",
                "generation_mode": "reference_video",
                "characters": {"主角": {"character_sheet": "characters/zhujue.png"}},
                "scenes": {"酒馆": {"scene_sheet": "scenes/jiuguan.png"}},
                "props": {},
                "episodes": [
                    {
                        "episode": 1,
                        "script_file": "scripts/episode_1.json",
                        "generation_mode": "reference_video",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (project_dir / "scripts" / "episode_1.json").write_text(
        json.dumps(
            {
                "episode": 1,
                "content_mode": "reference_video",
                "title": "t",
                "summary": "s",
                "novel": {"title": "t", "chapter": "1"},
                "video_units": [
                    {
                        "unit_id": "E1U1",
                        "shots": [{"duration": 4, "text": "@主角 推门"}],
                        "references": [{"type": "character", "name": "主角"}],
                        "duration_seconds": 4,
                        "duration_override": False,
                        "transition_to_next": "cut",
                        "generated_assets": {"video_clip": None, "status": "pending"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return project_dir, "refproj"


def test_detect_reference_video_script(tmp_path):
    project_dir, _ = _make_reference_project(tmp_path)
    script = json.loads((project_dir / "scripts" / "episode_1.json").read_text())
    assert gv.is_reference_video_script(script) is True


def test_detect_narration_script_not_reference():
    script = {"content_mode": "narration", "segments": [{"segment_id": "E1S1"}]}
    assert gv.is_reference_video_script(script) is False


def test_generate_episode_video_reference_enqueues_reference_tasks(tmp_path, monkeypatch):
    project_dir, project_name = _make_reference_project(tmp_path)
    # from_cwd() 用 cwd.name 作项目名、cwd.parent 作 projects_root
    # 需要 chdir 到 project_dir 本身（即 tmp_path/refproj）
    monkeypatch.chdir(project_dir)

    captured: list[dict] = []

    def fake_batch(*, project_name, specs, on_success, on_failure):
        for spec_ in specs:
            on_success(
                gv.BatchTaskResult(
                    resource_id=spec_.resource_id,
                    task_id="t",
                    status="succeeded",
                    result={"file_path": f"reference_videos/{spec_.resource_id}.mp4"},
                    error=None,
                )
            )
            captured.append(
                {
                    "task_type": spec_.task_type,
                    "resource_id": spec_.resource_id,
                    "payload": spec_.payload,
                }
            )
        return [], []

    monkeypatch.setattr(gv, "batch_enqueue_and_wait_sync", fake_batch)

    # 让 ordered_paths 检查找到已存在的 mp4（实际 batch 已回报成功）
    (project_dir / "reference_videos" / "E1U1.mp4").write_bytes(b"stub")

    gv.generate_episode_video("episode_1.json")

    assert captured == [
        {
            "task_type": "reference_video",
            "resource_id": "E1U1",
            "payload": {"script_file": "episode_1.json"},
        }
    ]
