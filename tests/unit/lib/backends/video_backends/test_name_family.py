"""视频调用通道按名字族认定通道能力：名字的取法、认不得时的统一回落。"""

from __future__ import annotations

import pytest

from arcreel_market_core.video_backend_contract import ReferenceAudioMode
from lib.backends.video_backends import get_registered_backends
from lib.backends.video_backends.name_family import capability_names
from lib.backends.video_backends.registry import video_capabilities_for_model
from lib.catalog import query
from tests.catalog_support import install_catalog, with_entry_changes


def test_names_are_entry_name_and_aliases_when_model_maps_to_an_entry():
    assert capability_names("doubao-seedance-2-0-260128") == ("doubao-seedance-2.0", "doubao-seedance-2-0-260128")


def test_names_fall_back_to_api_model_name_without_an_entry():
    assert capability_names("relay-video-x") == ("relay-video-x",)


@pytest.mark.parametrize("backend_name", sorted(get_registered_backends()))
def test_unrecognized_model_falls_back_to_text_and_first_frame_only(backend_name: str):
    caps = video_capabilities_for_model(backend_name, "acme-video-9")

    assert caps.text_to_video is True
    assert caps.first_frame is True
    assert caps.last_frame is False
    assert caps.max_reference_images == 0
    assert caps.reference_audio_mode is ReferenceAudioMode.NONE
    assert caps.max_reference_audio_count == 0


def test_alias_of_the_mapped_entry_selects_the_family(monkeypatch: pytest.MonkeyPatch):
    data = with_entry_changes(query.factory_snapshot(), "vidu", "viduq3-turbo", aliases=("relay-q3t",))
    install_catalog(monkeypatch, data)

    assert video_capabilities_for_model("vidu", "relay-q3t") == video_capabilities_for_model("vidu", "viduq3-turbo")


def test_relay_spelling_of_an_entry_name_selects_the_family():
    """命名空间前缀与大小写在对应条目时归一化，调用通道按条目的官方模型名认定能力。"""
    caps = video_capabilities_for_model("vidu", "vidu/ViduQ3-Turbo")

    assert caps == video_capabilities_for_model("vidu", "viduq3-turbo")
    assert caps.max_reference_images > 0
