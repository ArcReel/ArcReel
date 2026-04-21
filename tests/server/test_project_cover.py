"""项目封面选择器单测：验证 fallback 链的优先级与鲁棒性。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from server.services.project_cover import resolve_project_cover


def _mk_manager(scripts_by_file: dict[str, dict]) -> MagicMock:
    """构造 fake ProjectManager，load_script 按文件名查表返回；缺失则抛 FileNotFoundError。"""
    mgr = MagicMock()

    def _load_script(_project_name: str, filename: str) -> dict:
        if filename in scripts_by_file:
            return scripts_by_file[filename]
        raise FileNotFoundError(filename)

    mgr.load_script.side_effect = _load_script
    return mgr


def test_returns_video_thumbnail_when_present_in_reference_mode():
    """reference 模式已生成视频：命中 video_thumbnail 最高优先级。"""
    project = {
        "episodes": [{"script_file": "scripts/episode_1.json"}],
        "scenes": {"S": {"scene_sheet": "scenes/s.png"}},
    }
    scripts = {
        "scripts/episode_1.json": {
            "video_units": [
                {"generated_assets": {"video_thumbnail": "reference_videos/thumbnails/E1U1.jpg"}},
            ]
        }
    }
    url = resolve_project_cover(_mk_manager(scripts), "proj", project)
    assert url == "/api/v1/files/proj/reference_videos/thumbnails/E1U1.jpg"


def test_returns_video_thumbnail_in_storyboard_mode():
    """storyboard 模式：segments 分支同样能命中 video_thumbnail。"""
    project = {"episodes": [{"script_file": "scripts/episode_1.json"}]}
    scripts = {
        "scripts/episode_1.json": {
            "segments": [
                {"generated_assets": {"video_thumbnail": "thumbnails/scene_E1S1.jpg"}},
            ]
        }
    }
    url = resolve_project_cover(_mk_manager(scripts), "proj", project)
    assert url == "/api/v1/files/proj/thumbnails/scene_E1S1.jpg"


def test_video_thumbnail_beats_storyboard_image_across_all_episodes():
    """只要任意一集有 video_thumbnail，胜过第一集的 storyboard_image ——
    分两趟扫的关键合同（episode 顺序不锁死优先级）。"""
    project = {
        "episodes": [
            {"script_file": "scripts/episode_1.json"},
            {"script_file": "scripts/episode_2.json"},
        ]
    }
    scripts = {
        "scripts/episode_1.json": {
            "segments": [{"generated_assets": {"storyboard_image": "storyboards/scene_E1S1_first.png"}}]
        },
        "scripts/episode_2.json": {
            "segments": [{"generated_assets": {"video_thumbnail": "thumbnails/scene_E2S1.jpg"}}]
        },
    }
    url = resolve_project_cover(_mk_manager(scripts), "proj", project)
    assert url == "/api/v1/files/proj/thumbnails/scene_E2S1.jpg"


def test_falls_back_to_storyboard_image_when_no_video_thumbnail():
    project = {"episodes": [{"script_file": "scripts/episode_1.json"}]}
    scripts = {
        "scripts/episode_1.json": {
            "segments": [
                {"generated_assets": {"storyboard_image": "storyboards/scene_E1S1_first.png"}},
            ]
        }
    }
    url = resolve_project_cover(_mk_manager(scripts), "proj", project)
    assert url == "/api/v1/files/proj/storyboards/scene_E1S1_first.png"


def test_reference_mode_without_generated_assets_falls_back_to_scene_sheet():
    """参考模式未生成任何视频：用第一张场景参考图当封面（核心 fix 场景）。"""
    project = {
        "episodes": [{"script_file": "scripts/episode_1.json"}],
        "scenes": {"酒馆": {"scene_sheet": "scenes/酒馆.png"}},
        "characters": {"张三": {"character_sheet": "characters/张三.png"}},
    }
    scripts = {"scripts/episode_1.json": {"video_units": [{"generated_assets": {"status": "pending"}}]}}
    url = resolve_project_cover(_mk_manager(scripts), "proj", project)
    # scene 优先于 character
    assert url == "/api/v1/files/proj/scenes/酒馆.png"


def test_falls_back_to_character_sheet_when_no_scenes():
    project = {
        "episodes": [],
        "characters": {"张三": {"character_sheet": "characters/张三.png"}},
    }
    url = resolve_project_cover(_mk_manager({}), "proj", project)
    assert url == "/api/v1/files/proj/characters/张三.png"


def test_returns_none_for_empty_project():
    url = resolve_project_cover(_mk_manager({}), "proj", {})
    assert url is None


def test_missing_script_file_does_not_break_fallback():
    """scripts/episode_N.json 缺失 / 损坏时仍应走到资产 fallback。"""
    project = {
        "episodes": [{"script_file": "scripts/episode_missing.json"}],
        "scenes": {"S": {"scene_sheet": "scenes/s.png"}},
    }
    url = resolve_project_cover(_mk_manager({}), "proj", project)
    assert url == "/api/v1/files/proj/scenes/s.png"


def test_episode_without_script_file_is_skipped():
    """episode 条目里没 script_file 键（预处理未完成）：跳过即可，不报错。"""
    project = {
        "episodes": [{"episode": 1}],
        "characters": {"X": {"character_sheet": "characters/x.png"}},
    }
    url = resolve_project_cover(_mk_manager({}), "proj", project)
    assert url == "/api/v1/files/proj/characters/x.png"


def test_preloaded_scripts_skips_manager_load():
    """传入 preloaded_scripts 且覆盖所有 episode 时，不应再调用 manager.load_script。

    这是 list_projects 的 hot-path 合同：与 calculate_project_status 共用一份剧本加载，
    避免 cover + status 两次 JSON 解析。"""
    project = {
        "episodes": [
            {"script_file": "scripts/episode_1.json"},
            {"script_file": "scripts/episode_2.json"},
        ],
    }
    preloaded = {
        "scripts/episode_1.json": {"segments": []},
        "scripts/episode_2.json": {"segments": [{"generated_assets": {"video_thumbnail": "thumbnails/E2.jpg"}}]},
    }
    mgr = _mk_manager({})  # 空 map：若 cover 不走预加载，会全部 FileNotFoundError
    url = resolve_project_cover(mgr, "proj", project, preloaded_scripts=preloaded)
    assert url == "/api/v1/files/proj/thumbnails/E2.jpg"
    mgr.load_script.assert_not_called()


def test_preloaded_scripts_falls_back_to_manager_for_missing_entries():
    """preloaded_scripts 未覆盖的集：回退 manager.load_script，保持"尽力而为"的合同。"""
    project = {
        "episodes": [
            {"script_file": "scripts/episode_1.json"},
            {"script_file": "scripts/episode_2.json"},
        ],
    }
    preloaded = {
        "scripts/episode_1.json": {"segments": []},
    }
    # manager 仅提供 episode_2：模拟"1 集已预加载，2 集需回源"。
    mgr = _mk_manager(
        {"scripts/episode_2.json": {"segments": [{"generated_assets": {"video_thumbnail": "thumbnails/E2.jpg"}}]}}
    )
    url = resolve_project_cover(mgr, "proj", project, preloaded_scripts=preloaded)
    assert url == "/api/v1/files/proj/thumbnails/E2.jpg"
    # 预加载命中 episode_1：不应触发其 load_script；episode_2 missing from preload：回源一次。
    called_files = {call.args[1] for call in mgr.load_script.call_args_list}
    assert called_files == {"scripts/episode_2.json"}


@pytest.mark.parametrize(
    "sheet_value",
    [None, "", 0],
)
def test_ignores_falsy_sheet_values(sheet_value):
    """scene_sheet/character_sheet 可能是 None/空串/数字 0，都应被跳过不误选。"""
    project = {
        "episodes": [],
        "scenes": {"S": {"scene_sheet": sheet_value}},
        "characters": {"X": {"character_sheet": "characters/x.png"}},
    }
    url = resolve_project_cover(_mk_manager({}), "proj", project)
    assert url == "/api/v1/files/proj/characters/x.png"
