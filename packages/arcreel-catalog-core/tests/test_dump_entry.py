"""条目写回 JSON：用户修改存下的条目副本经 ``dump_entry`` 落库，再经 ``parse_entry`` 读回，必须原样还原。"""

from __future__ import annotations

import json

import pytest
from catalog_factories import amphibious_image_entry, audio_entry, text_entry, video_entry

from arcreel_catalog_core import check_entry, dump_entry, parse_entry


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
        (
            amphibious_image_entry,
            {
                "output_token_bases": {"high": 48},
                "output_token_scaled_bases": {
                    "low": {"base": 16, "multiplier": 4.2},
                    "medium": {"base": 24, "multiplier": 3.3},
                },
            },
        ),
        (amphibious_image_entry, {"max_reference_images": 0, "image_to_image": False}),
        (amphibious_image_entry, {"qualities": ["standard", "hd"]}),
        (
            amphibious_image_entry,
            {
                "qualities": ["low", "medium", "high"],
                "output_token_bases": {"high": 48},
                "output_token_scaled_bases": {
                    "low": {"base": 16, "multiplier": 4.2},
                    "medium": {"base": 24, "multiplier": 3.3},
                },
            },
        ),
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


@pytest.mark.parametrize("qualities", [None, []], ids=["null", "空数组"])
def test_dump_omits_unrecorded_image_qualities(qualities: object):
    raw = amphibious_image_entry()
    raw["facts"]["qualities"] = qualities

    assert dump_entry(parse_entry(raw))["facts"] == amphibious_image_entry()["facts"]


def test_dump_preserves_lifecycle_fields():
    raw = text_entry()
    raw.update(release_date="2025-11-18", shutdown_date="2026-12-11", successor="google/gemini-3.5-pro")

    entry = parse_entry(raw)
    dumped = json.loads(json.dumps(dump_entry(entry)))

    assert (dumped["release_date"], dumped["shutdown_date"], dumped["successor"]) == (
        "2025-11-18",
        "2026-12-11",
        "google/gemini-3.5-pro",
    )
    assert parse_entry(dumped) == entry


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
