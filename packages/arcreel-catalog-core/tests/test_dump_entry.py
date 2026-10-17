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
