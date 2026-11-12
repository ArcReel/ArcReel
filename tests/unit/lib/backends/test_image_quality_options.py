"""图片画质的两侧求值：条目收录的画质取值，与调用端点是否接受显式画质（docs/adr/0104）。"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace

import pytest

from arcreel_catalog_core import EntryKey, ImageFacts
from lib.backends.image_capabilities import (
    builtin_image_quality_options,
    custom_image_quality_options,
    effective_image_quality,
)
from lib.catalog import query
from lib.config.resolver import project_image_quality
from lib.custom_provider.endpoints import get_endpoint_spec
from tests.catalog_support import install_catalog, with_entry_facts

QUALITIES = ("low", "medium", "high")
GPT_IMAGE_2 = EntryKey("openai", "gpt-image-2", "image")


@pytest.fixture
def gpt_image_with_qualities(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试目录数据里的 gpt-image-2 条目收录画质取值；同时给一个非 OpenAI 兼容通道的图片条目也加上。"""
    data = with_entry_facts(query.factory_snapshot(), "openai", "gpt-image-2", qualities=QUALITIES)
    install_catalog(monkeypatch, with_entry_facts(data, "vidu", "viduq2", qualities=QUALITIES))


@pytest.fixture
def clear_user_entries() -> Iterator[None]:
    yield
    query.install_user_entries(())


@pytest.mark.usefixtures("gpt_image_with_qualities")
class TestBothSides:
    def test_builtin_openai_images_offers_the_entry_values_in_order(self) -> None:
        assert builtin_image_quality_options("openai", "gpt-image-2") == QUALITIES

    def test_builtin_channel_that_does_not_accept_explicit_quality_offers_none(self) -> None:
        assert builtin_image_quality_options("vidu", "viduq2") == ()

    @pytest.mark.parametrize("endpoint", ["openai-images", "openai-images-generations", "openai-images-edits"])
    def test_custom_openai_images_endpoints_offer_the_matched_entry_values(self, endpoint: str) -> None:
        spec = get_endpoint_spec(endpoint)
        assert custom_image_quality_options("gpt-image-2", endpoint_spec=spec) == QUALITIES
        # 条目绑定同样适用：不透明的模型名绑到收录了画质的条目
        assert (
            custom_image_quality_options("relay-x", endpoint_spec=spec, entry_binding="openai/gpt-image-2") == QUALITIES
        )

    @pytest.mark.parametrize("endpoint", ["openrouter-images", "gemini-image", "openai-chat-image"])
    def test_other_custom_endpoints_offer_none_even_when_the_entry_records_qualities(self, endpoint: str) -> None:
        assert custom_image_quality_options("gpt-image-2", endpoint_spec=get_endpoint_spec(endpoint)) == ()

    def test_unmatched_custom_model_offers_none(self) -> None:
        assert custom_image_quality_options("relay-x", endpoint_spec=get_endpoint_spec("openai-images")) == ()


def test_entry_without_qualities_offers_none() -> None:
    """官方数据没有收录画质取值（如尚未同步到新数据）：没有画质选项。"""
    assert builtin_image_quality_options("openai", "gpt-image-2") == ()
    assert custom_image_quality_options("gpt-image-2", endpoint_spec=get_endpoint_spec("openai-images")) == ()


@pytest.mark.usefixtures("gpt_image_with_qualities", "clear_user_entries")
def test_user_copy_without_qualities_wins_over_official_data() -> None:
    """官方数据收录了画质取值、用户副本是在那之前存的：以副本为准，没有画质选项。"""
    official = query.get_entry(GPT_IMAGE_2)
    assert official is not None
    assert isinstance(official.facts, ImageFacts)
    query.install_user_entries([replace(official, facts=replace(official.facts, qualities=()))])

    assert builtin_image_quality_options("openai", "gpt-image-2") == ()
    assert custom_image_quality_options("gpt-image-2", endpoint_spec=get_endpoint_spec("openai-images")) == ()


class TestEffectiveQuality:
    @pytest.mark.parametrize(
        ("stored", "options", "expected"),
        [
            ("low", QUALITIES, "low"),
            # 不在可选值内照发，不做大小写归一
            ("LOW", QUALITIES, "LOW"),
            ("ultra", QUALITIES, "ultra"),
            (None, QUALITIES, None),
            # 没有有效可选值：忽略存值
            ("low", (), None),
            (None, (), None),
        ],
    )
    def test_stored_value_takes_effect_only_with_options(
        self, stored: str | None, options: tuple[str, ...], expected: str | None
    ) -> None:
        assert effective_image_quality(stored, options) == expected


class TestProjectImageQuality:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [("low", "low"), ("  high ", "high"), ("LOW", "LOW"), ("", None), ("   ", None), (3, None), (None, None)],
    )
    def test_reads_the_execution_model_key_and_cleans_like_resolution(self, value: object, expected: str | None):
        project = {"model_settings": {"openai/gpt-image-2": {"resolution": "1K", "quality": value}}}
        assert project_image_quality(project, "openai", "gpt-image-2") == expected

    def test_other_model_keys_and_legacy_table_are_not_read(self) -> None:
        project = {
            "model_settings": {"openai/gpt-image-2.5": {"quality": "low"}},
            "video_model_settings": {"gpt-image-2": {"quality": "low"}},
        }
        assert project_image_quality(project, "openai", "gpt-image-2") is None
        assert project_image_quality({}, "openai", "gpt-image-2") is None
