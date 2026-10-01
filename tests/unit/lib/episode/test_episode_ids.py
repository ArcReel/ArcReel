"""集名的派生：空标题的集按当下播出位置成文，插集或调序后跟随位置。"""

from functools import partial

import pytest

from lib.episode.episode_ids import episode_display_name, episode_file_label
from lib.i18n import _
from lib.script.script_document import build_materialized_script


def _project(*entries: tuple[int, str]) -> dict:
    return {
        "title": "Demo",
        "content_mode": "narration",
        "episodes": [{"episode": episode, "title": title} for episode, title in entries],
    }


def test_untitled_episode_name_follows_its_air_position() -> None:
    project = _project((1, "开篇"), (2, ""))
    assert episode_display_name(project, 2, _) == "第 2 集"

    project["episodes"].insert(0, {"episode": 3, "title": "前传"})

    assert episode_display_name(project, 2, _) == "第 3 集"
    assert episode_display_name(project, 1, _) == "开篇"


def test_episode_outside_the_ledger_is_untitled() -> None:
    assert episode_display_name(_project((1, "")), 9, _) == "未命名集"


@pytest.mark.parametrize(
    ("locale", "expected"),
    [("zh", "02_第 2 集"), ("en", "02_Episode 2"), ("vi", "02_Tập 2")],
)
def test_file_label_derives_an_empty_title_in_the_user_language(locale: str, expected: str) -> None:
    project = _project((1, "开篇"), (2, "  "))

    assert episode_file_label(project, 2, partial(_, locale=locale)) == expected
    assert episode_file_label(project, 1, partial(_, locale=locale)) == "01_开篇"
    assert episode_file_label(project, 9, partial(_, locale=locale)) is None


def test_materialized_script_leaves_an_empty_episode_title_empty() -> None:
    script = build_materialized_script(
        _project((1, "")),
        1,
        plan_kind="narration",
        plan_entries=[{"segment_id": "E1S01", "novel_text": "风吹过旷野。", "duration_seconds": 4}],
        title=None,
    )

    assert script["title"] == ""
    assert script["novel"]["chapter"] == ""
