import pytest
from pydantic import ValidationError

from lib.script_models import ReferenceResource, Shot


def test_shot_valid():
    s = Shot(duration=5, text="中远景，主角推门进酒馆")
    assert s.duration == 5
    assert "酒馆" in s.text


def test_shot_duration_range():
    with pytest.raises(ValidationError):
        Shot(duration=0, text="x")
    with pytest.raises(ValidationError):
        Shot(duration=16, text="x")


def test_reference_resource_valid_types():
    for t in ("character", "scene", "prop"):
        r = ReferenceResource(type=t, name="张三")
        assert r.type == t


def test_reference_resource_rejects_clue():
    with pytest.raises(ValidationError):
        ReferenceResource(type="clue", name="张三")
