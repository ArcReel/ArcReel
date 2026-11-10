"""推荐模型写进用户设置、升级固化与推荐变化的提示（docs/adr/0099、docs/adr/0103）。"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.catalog import query as catalog
from lib.config import recommended_models
from lib.config.recommended_models import (
    SEEN_SETTING_KEY,
    RecommendationChange,
    StaleRecommendationError,
    acknowledge_recommendation,
    freeze_effective_defaults,
    recommendation_changes,
    seed_provider_recommendations,
)
from lib.config.resolver import ConfigResolver, ProviderModel
from lib.config.service import ConfigService
from lib.db.models.builtin_provider_model import BuiltinProviderModelState
from lib.db.models.custom_provider import CustomProvider, CustomProviderModel
from lib.db.repositories.credential_repository import CredentialRepository
from tests.catalog_support import install_catalog, with_recommendation

Sessions = async_sessionmaker[AsyncSession]


async def _configure(sessions: Sessions, provider_id: str) -> None:
    async with sessions() as session:
        await CredentialRepository(session).create(provider=provider_id, name="主 Key", api_key=f"TEST-{provider_id}")
        await session.commit()


async def _settings(sessions: Sessions) -> dict[str, str]:
    async with sessions() as session:
        return await ConfigService(session).get_all_settings()


async def _seed(sessions: Sessions, provider_id: str) -> None:
    async with sessions() as session:
        await seed_provider_recommendations(session, provider_id)
        await session.commit()


async def _changes(sessions: Sessions) -> list[RecommendationChange]:
    async with sessions() as session:
        return await recommendation_changes(session)


def _recommended(provider_id: str, media_type: str) -> str:
    model = catalog.default_model(provider_id, media_type)
    assert model is not None
    return model


class TestSeedProviderRecommendations:
    async def test_first_configuration_writes_recommendations_into_empty_defaults(self, db_factory):
        async with db_factory() as session:
            await ConfigService(session).set_setting("default_image_backend", "openai/gpt-image-2")
            await session.commit()

        await _seed(db_factory, "ark")

        settings = await _settings(db_factory)
        # 已有设置不覆盖，空的媒体类型写入推荐；ark 没有音频推荐，音频不写
        assert settings["default_image_backend"] == "openai/gpt-image-2"
        assert settings["default_text_backend"] == f"ark/{_recommended('ark', 'text')}"
        assert settings["default_video_backend"] == f"ark/{_recommended('ark', 'video')}"
        assert "default_audio_backend" not in settings
        assert json.loads(settings[SEEN_SETTING_KEY]) == {
            "ark": {media: _recommended("ark", media) for media in ("image", "text", "video")}
        }

    async def test_a_second_provider_only_fills_what_is_still_empty(self, db_factory):
        await _seed(db_factory, "ark")
        await _seed(db_factory, "dashscope")

        settings = await _settings(db_factory)
        assert settings["default_video_backend"].startswith("ark/")
        assert settings["default_audio_backend"] == f"dashscope/{_recommended('dashscope', 'audio')}"

    async def test_unknown_provider_writes_nothing(self, db_factory):
        await _seed(db_factory, "custom-1")
        assert await _settings(db_factory) == {}


class TestRecommendationChangesKeepGenerationModels:
    async def test_changed_recommendation_does_not_change_the_configured_model(self, db_factory, monkeypatch):
        """已配置的用户：目录推荐变化后生成用的模型不变，推荐变化以提示呈现，由用户决定是否切换。"""
        await _configure(db_factory, "ark")
        await _seed(db_factory, "ark")
        before = _recommended("ark", "image")
        resolver = ConfigResolver(db_factory)
        assert await resolver.resolve_image_backend(None, None, generation_type="t2i") == ProviderModel("ark", before)
        assert await _changes(db_factory) == []

        install_catalog(
            monkeypatch, with_recommendation(catalog.factory_snapshot(), "ark", "image", "doubao-seedream-4-5-251128")
        )

        assert await resolver.resolve_image_backend(None, None, generation_type="t2i") == ProviderModel("ark", before)
        assert await _changes(db_factory) == [
            RecommendationChange("image", "ark", "doubao-seedream-4-5-251128", f"ark/{before}")
        ]

    async def test_keeping_the_current_model_dismisses_the_notice(self, db_factory, monkeypatch):
        await _configure(db_factory, "ark")
        await _seed(db_factory, "ark")
        before = _recommended("ark", "image")
        install_catalog(
            monkeypatch, with_recommendation(catalog.factory_snapshot(), "ark", "image", "doubao-seedream-4-5-251128")
        )

        async with db_factory() as session:
            await acknowledge_recommendation(session, "ark", "image", "doubao-seedream-4-5-251128", apply=False)
            await session.commit()

        assert await _changes(db_factory) == []
        assert (await _settings(db_factory))["default_image_backend"] == f"ark/{before}"

    async def test_switching_sets_the_global_default(self, db_factory, monkeypatch):
        await _configure(db_factory, "ark")
        await _seed(db_factory, "ark")
        install_catalog(
            monkeypatch, with_recommendation(catalog.factory_snapshot(), "ark", "image", "doubao-seedream-4-5-251128")
        )

        async with db_factory() as session:
            await acknowledge_recommendation(session, "ark", "image", "doubao-seedream-4-5-251128", apply=True)
            await session.commit()

        assert await _changes(db_factory) == []
        resolved = await ConfigResolver(db_factory).resolve_image_backend(None, None, generation_type="t2i")
        assert resolved == ProviderModel("ark", "doubao-seedream-4-5-251128")

    async def test_acknowledging_a_stale_recommendation_changes_nothing(self, db_factory):
        await _seed(db_factory, "ark")
        before = await _settings(db_factory)
        async with db_factory() as session:
            with pytest.raises(StaleRecommendationError):
                await acknowledge_recommendation(session, "ark", "image", "doubao-seedream-4-5-251128", apply=True)
        assert await _settings(db_factory) == before

    async def test_a_model_chosen_by_the_user_is_not_nagged_until_the_recommendation_changes(
        self, db_factory, monkeypatch
    ):
        await _configure(db_factory, "ark")
        await _seed(db_factory, "ark")
        async with db_factory() as session:
            await ConfigService(session).set_setting("default_image_backend", "ark/doubao-seedream-4-0-250828")
            await session.commit()
        assert await _changes(db_factory) == []

        install_catalog(
            monkeypatch, with_recommendation(catalog.factory_snapshot(), "ark", "image", "doubao-seedream-4-5-251128")
        )
        assert [change.recommended_model for change in await _changes(db_factory)] == ["doubao-seedream-4-5-251128"]

    async def test_a_new_recommendation_is_suggested_when_the_default_is_empty(self, db_factory, monkeypatch):
        """供应商配置时还没有这个媒体类型的推荐，之后才有：全局默认模型为空时提示设为默认。"""
        snapshot = with_recommendation(catalog.factory_snapshot(), "ark", "image", None)
        install_catalog(monkeypatch, snapshot)
        await _configure(db_factory, "ark")
        await _seed(db_factory, "ark")
        assert "default_image_backend" not in await _settings(db_factory)

        install_catalog(monkeypatch, with_recommendation(snapshot, "ark", "image", "doubao-seedream-4-5-251128"))

        assert await _changes(db_factory) == [RecommendationChange("image", "ark", "doubao-seedream-4-5-251128", None)]


class TestFreezeEffectiveDefaults:
    async def test_freezes_what_the_old_automatic_pick_would_choose(self, db_factory):
        """没有默认模型设置的媒体类型，写入按注册顺序第一个有凭证、推荐可用的内置供应商的推荐模型。"""
        await _configure(db_factory, "dashscope")
        await _configure(db_factory, "ark")
        async with db_factory() as session:
            await ConfigService(session).set_setting("default_text_backend", "openai/gpt-5.4-mini")
            # 用户停用了 ark 的推荐图片模型：旧版自动挑选跳过 ark，落到下一个供应商
            session.add(
                BuiltinProviderModelState(provider_id="ark", model_id=_recommended("ark", "image"), enabled=False)
            )
            await session.commit()

        async with db_factory() as session:
            await freeze_effective_defaults(session)

        settings = await _settings(db_factory)
        assert settings["default_text_backend"] == "openai/gpt-5.4-mini"
        assert settings["default_image_backend"] == f"dashscope/{_recommended('dashscope', 'image')}"
        assert settings["default_video_backend"] == f"ark/{_recommended('ark', 'video')}"
        assert settings["default_audio_backend"] == f"dashscope/{_recommended('dashscope', 'audio')}"
        assert set(json.loads(settings[SEEN_SETTING_KEY])) == {"ark", "dashscope"}

    async def test_generation_models_are_the_same_before_and_after_the_upgrade(self, db_factory, monkeypatch):
        """升级前依赖自动挑选的实例：固化后各媒体类型解析出的模型与固化前目录给出的推荐一致，之后不再随目录变化。"""
        await _configure(db_factory, "gemini-vertex")
        expected = {
            media: ProviderModel("gemini-vertex", _recommended("gemini-vertex", media)) for media in ("image", "video")
        }
        async with db_factory() as session:
            await freeze_effective_defaults(session)

        install_catalog(
            monkeypatch,
            with_recommendation(catalog.factory_snapshot(), "gemini-vertex", "video", "veo-3.1-generate-001"),
        )
        resolver = ConfigResolver(db_factory)
        assert await resolver.resolve_image_backend(None, None, generation_type="t2i") == expected["image"]
        assert await resolver.resolve_video_backend(None, None, generation_type="i2v") == expected["video"]

    async def test_custom_provider_defaults_are_not_frozen(self, db_factory):
        """旧版自动挑选落到自定义供应商默认模型的，运行时仍按同一规则挑选，不写入用户设置。"""
        async with db_factory() as session:
            provider = CustomProvider(
                display_name="Relay", discovery_format="openai", base_url="https://example.com", api_key="k"
            )
            session.add(provider)
            await session.flush()
            session.add(
                CustomProviderModel(
                    provider_id=provider.id,
                    model_id="image-m",
                    display_name="Image",
                    endpoint="openai-images-generations",
                    is_default=True,
                    is_enabled=True,
                )
            )
            await session.commit()
            provider_id = provider.provider_id

        async with db_factory() as session:
            await freeze_effective_defaults(session)

        settings = await _settings(db_factory)
        assert "default_image_backend" not in settings
        resolved = await ConfigResolver(db_factory).resolve_image_backend(None, None, generation_type="t2i")
        assert resolved == ProviderModel(provider_id, "image-m")

    async def test_runs_only_once(self, db_factory):
        async with db_factory() as session:
            await freeze_effective_defaults(session)
        assert (await _settings(db_factory))[SEEN_SETTING_KEY] == "{}"

        await _configure(db_factory, "ark")
        async with db_factory() as session:
            await freeze_effective_defaults(session)
        assert set(await _settings(db_factory)) == {SEEN_SETTING_KEY}


def test_global_default_keys_cover_every_media_type() -> None:
    from arcreel_catalog_core import MEDIA_TYPES

    assert set(recommended_models.GLOBAL_DEFAULT_KEYS) == set(MEDIA_TYPES)
