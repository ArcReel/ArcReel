"""用户修改的条目副本：在当前条目上改显示名与模型事实，其余字段原样带上，不合法的修改被拒绝。"""

import pytest

from arcreel_catalog_core import DurationConstraints, TextFacts, VideoFacts, parse_entry
from lib.catalog.user_entries import EntryEditRejected, edited_entry

_VIDEO = {
    "maker": "acme",
    "name": "clip",
    "media_type": "video",
    "display_name": "Clip",
    "aliases": ["clip-2026"],
    "facts": {"durations": [5, 10], "resolutions": ["720p", "1080p"]},
    "price": {
        "currency": "USD",
        "meters": [
            {
                "meter": "output_seconds",
                "dims": ["resolution"],
                "rows": [{"when": {"resolution": "720p"}, "price": 0.1}],
                "defaults": {"resolution": "720p"},
            }
        ],
    },
}


def test_edit_replaces_given_facts_and_keeps_the_rest():
    current = parse_entry(_VIDEO)

    edited = edited_entry(
        current,
        display_name="  My Clip ",
        facts={"durations": [5, 8, 10], "duration_constraints": {"by_resolution": {"1080p": [5]}}},
    )

    assert edited.display_name == "My Clip"
    assert edited.facts == VideoFacts(
        durations=(5, 8, 10),
        resolutions=("720p", "1080p"),
        duration_constraints=DurationConstraints(by_resolution={"1080p": (5,)}),
    )
    assert (edited.key, edited.aliases, edited.price) == (current.key, current.aliases, current.price)


def test_text_max_output_tokens_can_be_cleared():
    current = parse_entry(
        {
            "maker": "acme",
            "name": "chat",
            "media_type": "text",
            "display_name": "Chat",
            "facts": {"max_output_tokens": 8},
        }
    )

    edited = edited_entry(current, facts={"image_input": True, "max_output_tokens": None})

    assert edited.facts == TextFacts(image_input=True)


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"facts": {"durations": []}}, "facts.durations"),
        (
            {"facts": {"duration_constraints": {"by_resolution": {"4k": [5]}}}},
            "facts.duration_constraints.by_resolution.4k",
        ),
        ({"facts": {"durations": [5, 5]}}, "facts.durations[1]"),
        ({"facts": {"text_to_image": True}}, "facts.text_to_image"),
        ({"display_name": "  "}, "display_name"),
    ],
)
def test_invalid_edits_are_rejected_with_the_field(changes, field):
    with pytest.raises(EntryEditRejected) as excinfo:
        edited_entry(parse_entry(_VIDEO), **changes)

    assert excinfo.value.code == "catalog_entry_invalid"
    assert excinfo.value.params == {"field": field}
