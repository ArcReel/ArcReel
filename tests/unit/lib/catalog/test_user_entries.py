"""用户修改的条目副本：在当前条目上改显示名与模型事实，其余字段原样带上，不合法的修改被拒绝。"""

import pytest

from arcreel_catalog_core import DurationConstraints, TextFacts, VideoFacts, dump_entry, parse_entry
from lib.catalog.user_entries import EntryEditRejected, edited_entry, new_entry

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


_NEW_PRICE = {
    "currency": "CNY",
    "meters": [
        {
            "meter": "output_seconds",
            "dims": ["resolution", "audio"],
            "rows": [
                {"when": {"resolution": "720p", "audio": True}, "price": 0.8},
                {"when": {"resolution": "720p", "audio": False}, "price": 0.5},
            ],
            "defaults": {"resolution": "720p", "audio": True},
        },
        {"meter": "input_images", "dims": [], "rows": [{"when": {}, "price": 0.1}], "free_allowance": 2},
    ],
}


def test_edit_replaces_the_whole_price_table():
    edited = edited_entry(parse_entry(_VIDEO), price=_NEW_PRICE)

    assert dump_entry(edited)["price"] == _NEW_PRICE


def test_price_none_clears_the_price_table_and_unset_keeps_it():
    current = parse_entry(_VIDEO)

    assert edited_entry(current, price=None).price is None
    assert edited_entry(current, display_name="x").price == current.price


@pytest.mark.parametrize(
    ("price", "field"),
    [
        ({"currency": "USD", "meters": []}, "price.meters"),
        ({"currency": "EUR", "meters": _NEW_PRICE["meters"]}, "price.currency"),
        (
            {"currency": "USD", "meters": [{"meter": "characters", "dims": [], "rows": [{"when": {}, "price": 1}]}]},
            "price.meters[0].meter",
        ),
    ],
)
def test_invalid_price_is_rejected_with_the_field(price, field):
    with pytest.raises(EntryEditRejected) as excinfo:
        edited_entry(parse_entry(_VIDEO), price=price)

    assert excinfo.value.params == {"field": field}


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


class TestNewEntry:
    def test_fills_media_type_defaults_and_uses_the_name_as_display_name(self):
        entry = new_entry(maker=" acme ", name=" flux-x ", media_type="image", facts={"text_to_image": True})

        assert (entry.key.ref, entry.display_name, entry.price) == ("acme/flux-x", "flux-x", None)
        assert entry.facts.text_to_image is True
        assert entry.facts.resolutions == ()

    def test_video_needs_durations_and_accepts_a_price(self):
        with pytest.raises(EntryEditRejected) as excinfo:
            new_entry(maker="acme", name="clip-x", media_type="video")
        assert excinfo.value.params == {"field": "facts.durations"}

        entry = new_entry(
            maker="acme",
            name="clip-x",
            media_type="video",
            display_name="Clip X",
            facts={"durations": [5, 10]},
            price=_VIDEO["price"],
        )
        assert entry.display_name == "Clip X"
        assert entry.price is not None

    @pytest.mark.parametrize(
        ("changes", "field"),
        [
            ({"maker": " "}, "maker"),
            ({"name": ""}, "name"),
            ({"maker": "a/b"}, "maker"),
            ({"media_type": "pdf"}, "media_type"),
        ],
    )
    def test_blank_or_malformed_identity_is_rejected(self, changes, field):
        args = {"maker": "acme", "name": "chat-x", "media_type": "text", **changes}
        with pytest.raises(EntryEditRejected) as excinfo:
            new_entry(**args)

        assert (excinfo.value.code, excinfo.value.params) == ("catalog_entry_invalid", {"field": field})

    @pytest.mark.parametrize(
        "name", ["gemini-3-flash-preview", "Google/Gemini-3-Flash-Preview", "gemini-3.flash-preview"]
    )
    def test_name_already_in_the_catalog_is_rejected_even_from_another_maker(self, name):
        with pytest.raises(EntryEditRejected) as excinfo:
            new_entry(maker="my-relay", name=name, media_type="text")

        assert excinfo.value.code == "catalog_entry_exists"

    def test_same_name_of_another_media_type_is_allowed(self):
        assert new_entry(maker="acme", name="gemini-3-flash-preview", media_type="audio").media_type == "audio"
