"""内容确认覆盖清单的丢失清单文本：服务端唯一成文处，Web 确认框与 Agent 回执共用。"""

from __future__ import annotations

import pytest

from lib.i18n import SUPPORTED_LOCALES
from lib.i18n import _ as i18n_message
from lib.script.script_review import (
    FormalScriptOverwrite,
    OverwrittenScriptEntry,
    overwrite_with_text,
    render_overwrite_loss_text,
)


def _entry(entry_id: str, **overrides: object) -> OverwrittenScriptEntry:
    fields: dict = {
        "entry_id": entry_id,
        "has_storyboard": False,
        "has_video": False,
        "has_narration_audio": False,
        "has_end_frame": False,
        "grid_id": None,
    }
    fields.update(overrides)
    return OverwrittenScriptEntry(**fields)


def test_counts_cover_voice_end_frame_and_grid_membership():
    overwrite = FormalScriptOverwrite(
        fingerprint="sha256-v1:x",
        entries=(
            _entry("E1S01", has_storyboard=True, has_video=True, has_narration_audio=True, grid_id="grid_a"),
            _entry("E1S02", has_end_frame=True, grid_id="grid_a"),
            _entry("E1S03", grid_id="grid_b"),
        ),
    ).to_dict()

    assert overwrite["storyboard_count"] == 1
    assert overwrite["video_count"] == 1
    assert overwrite["narration_audio_count"] == 1
    assert overwrite["end_frame_count"] == 1
    assert overwrite["grid_member_count"] == 3
    assert overwrite["grid_count"] == 2


def test_text_lists_every_lost_kind_and_the_entries_holding_them():
    overwrite = FormalScriptOverwrite(
        fingerprint="sha256-v1:x",
        entries=(
            _entry(
                "E1S01",
                has_storyboard=True,
                has_video=True,
                has_narration_audio=True,
                has_end_frame=True,
                grid_id="grid_a",
            ),
            _entry("E1S02"),
        ),
    ).to_dict()

    text = render_overwrite_loss_text(overwrite, i18n_message)

    assert text.splitlines() == [
        "本集已有正式脚本，确认会整份覆盖它：现有 2 条脚本条目全部移除，时间线上手改的台词与提示词一并丢弃。",
        "以下内容无法在项目内恢复：分镜图 1 张、视频 1 段、配音 1 段、尾帧 1 张、"
        "宫格归属 1 处（涉及 1 张联合图，联合图随之失去登记）。",
        "新脚本中沿用相同编号的条目，其旧版本历史一并清理，不能再回滚。",
        "将被移除的脚本条目：E1S01（分镜图、视频、配音、尾帧、宫格）、E1S02",
    ]


def test_text_without_products_still_warns_about_history():
    overwrite = FormalScriptOverwrite(fingerprint="sha256-v1:x", entries=(_entry("E1S01"),)).to_dict()

    assert render_overwrite_loss_text(overwrite, i18n_message).splitlines() == [
        "本集已有正式脚本，确认会整份覆盖它：现有 1 条脚本条目全部移除，时间线上手改的台词与提示词一并丢弃。",
        "新脚本中沿用相同编号的条目，其旧版本历史一并清理，不能再回滚。",
        "将被移除的脚本条目：E1S01",
    ]


def test_overwrite_with_text_passes_none_through_and_keeps_the_token():
    assert overwrite_with_text(None, i18n_message) is None

    overwrite = FormalScriptOverwrite(fingerprint="sha256-v1:x", entries=(_entry("E1S01"),)).to_dict()
    rendered = overwrite_with_text(overwrite, i18n_message)

    assert rendered is not None
    assert rendered["revision"] == "sha256-v1:x"
    assert rendered["text"] == render_overwrite_loss_text(overwrite, i18n_message)


@pytest.mark.parametrize("locale", SUPPORTED_LOCALES)
def test_every_locale_renders_a_complete_text(locale: str):
    overwrite = FormalScriptOverwrite(
        fingerprint="sha256-v1:x",
        entries=(
            _entry(
                "E1S01",
                has_storyboard=True,
                has_video=True,
                has_narration_audio=True,
                has_end_frame=True,
                grid_id="grid_a",
            ),
        ),
    ).to_dict()

    text = render_overwrite_loss_text(overwrite, lambda key, **kwargs: i18n_message(key, locale=locale, **kwargs))

    assert "script_overwrite_" not in text
    assert "{" not in text
    assert "E1S01" in text
