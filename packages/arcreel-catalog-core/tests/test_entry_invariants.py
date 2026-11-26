import json
from collections.abc import Callable
from copy import deepcopy
from datetime import date
from typing import Any

import pytest
from catalog_factories import amphibious_image_entry, audio_entry, text_entry, video_entry
from hypothesis import given
from hypothesis import strategies as st

from arcreel_catalog_core import check_entry, dump_entry, parse_entry


@pytest.mark.invariant
@pytest.mark.parametrize("factory", [text_entry, amphibious_image_entry, video_entry, audio_entry])
@given(
    release=st.none() | st.dates(max_value=date(2025, 12, 31)),
    shutdown=st.none() | st.dates(min_value=date(2026, 1, 1)),
    successor=st.booleans(),
    discontinued=st.booleans(),
    include_price=st.booleans(),
    tier_bound=st.integers(1, 200_000),
    default_resolution=st.sampled_from(("720p", "1080p")),
    rate_cents=st.integers(0, 500),
    allowance=st.none() | st.integers(0, 10),
    explicit_empty=st.booleans(),
)
def test_entry_json_round_trip(
    factory: Callable[[], dict[str, Any]],
    release: date | None,
    shutdown: date | None,
    successor: bool,
    discontinued: bool,
    include_price: bool,
    tier_bound: int,
    default_resolution: str,
    rate_cents: int,
    allowance: int | None,
    explicit_empty: bool,
) -> None:
    raw = factory()
    if release:
        raw["release_date"] = release.isoformat()
    if shutdown:
        raw["shutdown_date"] = shutdown.isoformat()
    if successor:
        raw["successor"] = "acme/next"
    raw["discontinued"] = discontinued
    if include_price:
        for meter in raw["price"]["meters"]:
            for index, row in enumerate(meter["rows"]):
                if row["when"].get("context_tier", 0) > 0:
                    row["when"]["context_tier"] = tier_bound
                row["price"] = (rate_cents + index) / 100
            if "defaults" in meter:
                meter["defaults"]["resolution"] = default_resolution
            if allowance is not None:
                meter["free_allowance"] = allowance
    else:
        del raw["price"]
    if explicit_empty:
        raw["aliases"] = []

    expected = deepcopy(raw)
    expected.setdefault("facts", {})
    if not discontinued:
        del expected["discontinued"]
    if explicit_empty:
        del expected["aliases"]

    entry = parse_entry(raw)
    dumped = json.loads(json.dumps(dump_entry(entry)))
    assert dumped == expected
    assert check_entry(dumped) == []
    assert parse_entry(dumped) == entry
