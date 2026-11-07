"""Observe whole-manifest/version-history parsing counts, identifying each by top-level shape at JSON decoding boundaries."""

from __future__ import annotations

import json
from typing import Any

import pytest

from lib.project.resource_paths import RESOURCE_TYPES

_MANIFEST_TOP_LEVEL_FIELDS = frozenset({"entries", "hash_algorithm", "schema_version"})
_VERSIONS_TOP_LEVEL_FIELDS = frozenset(RESOURCE_TYPES)


def count_manifest_parses(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Count decoded text whose top level is exactly the three-field manifest object; exclude artifact identity keys, project files, and other decodes."""

    return _count_parses(monkeypatch, _MANIFEST_TOP_LEVEL_FIELDS)


def count_versions_parses(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Count decoded text whose top level is exactly version-history resource-type buckets; exclude other decodes."""

    return _count_parses(monkeypatch, _VERSIONS_TOP_LEVEL_FIELDS)


def _count_parses(monkeypatch: pytest.MonkeyPatch, top_level_fields: frozenset[str]) -> dict[str, int]:
    counts = {"parses": 0}
    original_loads = json.loads

    def _counted_loads(text: str | bytes, *args: Any, **kwargs: Any) -> Any:
        payload = original_loads(text, *args, **kwargs)
        if isinstance(payload, dict) and payload.keys() == top_level_fields:
            counts["parses"] += 1
        return payload

    monkeypatch.setattr(json, "loads", _counted_loads)
    return counts
