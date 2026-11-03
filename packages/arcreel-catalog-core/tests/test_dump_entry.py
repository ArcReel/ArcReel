"""条目写回 JSON：用户修改存下的条目副本经 ``dump_entry`` 落库，再经 ``parse_entry`` 读回，必须原样还原。"""

from __future__ import annotations

import json

import pytest
from catalog_factories import amphibious_image_entry, audio_entry, text_entry, video_entry

from arcreel_catalog_core import CatalogFormatError, check_entry, dump_entry, parse_entry


@pytest.mark.parametrize("factory", [text_entry, video_entry, amphibious_image_entry, audio_entry])
def test_dumped_entry_parses_back_to_the_same_entry(factory):
    entry = parse_entry(factory())

    dumped = dump_entry(entry)

    assert parse_entry(json.loads(json.dumps(dumped))) == entry
    assert check_entry(dumped) == []


def test_dump_keeps_price_table_details():
    dumped = dump_entry(parse_entry(video_entry()))

    assert dumped["price"] == video_entry()["price"]
    assert dump_entry(parse_entry(audio_entry()))["price"] == audio_entry()["price"]


@pytest.mark.parametrize("factory", [amphibious_image_entry, video_entry])
def test_dump_does_not_invent_unrecorded_model_facts(factory):
    raw = factory()

    assert dump_entry(parse_entry(raw))["facts"] == raw["facts"]


@pytest.mark.parametrize(
    ("factory", "additions"),
    [
        (
            amphibious_image_entry,
            {"max_reference_images": 16, "output_token_bases": {"low": 16, "medium": 48, "high": 96}},
        ),
        (amphibious_image_entry, {"max_reference_images": 0, "image_to_image": False}),
        (
            video_entry,
            {
                "max_reference_images": 9,
                "text_to_video": True,
                "first_frame": True,
                "last_frame": False,
                "reference_audio": True,
                "max_reference_audio_count": 3,
                "max_reference_audio_total_seconds": 15.5,
            },
        ),
        (
            video_entry,
            {
                "max_reference_images": 0,
                "text_to_video": False,
                "first_frame": False,
                "last_frame": False,
                "reference_audio": False,
                "max_reference_audio_count": 0,
                "max_reference_audio_total_seconds": 0,
            },
        ),
    ],
)
def test_dump_preserves_optional_model_facts(factory, additions):
    raw = factory()
    raw["facts"].update(additions)

    entry = parse_entry(raw)
    dumped = json.loads(json.dumps(dump_entry(entry)))

    assert dumped["facts"] == raw["facts"]
    assert parse_entry(dumped) == entry
    assert check_entry(dumped) == []


@pytest.mark.parametrize(
    ("factory", "additions"),
    [
        (amphibious_image_entry, {"max_reference_images": True}),
        (amphibious_image_entry, {"max_reference_images": -1}),
        (amphibious_image_entry, {"output_token_bases": {"high": 0}}),
        (amphibious_image_entry, {"output_token_bases": {"high": None}}),
        (video_entry, {"first_frame": 1}),
        (video_entry, {"max_reference_audio_count": 1.5}),
        (video_entry, {"max_reference_audio_total_seconds": True}),
    ],
)
def test_optional_model_facts_reject_invalid_types_and_counts(factory, additions):
    raw = factory()
    raw["facts"].update(additions)

    with pytest.raises(CatalogFormatError):
        parse_entry(raw)


@pytest.mark.parametrize("value", [-1, float("inf"), float("nan")])
def test_reference_audio_duration_must_be_nonnegative_and_finite_in_published_data(value):
    raw = video_entry()
    raw["facts"]["max_reference_audio_total_seconds"] = value

    assert any(issue.path == "facts.max_reference_audio_total_seconds" for issue in check_entry(raw))


def test_dump_omits_empty_optional_fields():
    raw = {
        "maker": "acme",
        "name": "plain",
        "media_type": "text",
        "display_name": "Plain",
        "facts": {},
    }

    assert dump_entry(parse_entry(raw)) == {
        "maker": "acme",
        "name": "plain",
        "media_type": "text",
        "display_name": "Plain",
        "facts": {"image_input": False, "structured_output": False},
    }
