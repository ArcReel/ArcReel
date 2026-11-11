"""推荐模型写进用户设置、升级固化与推荐变化的提示（docs/adr/0099、docs/adr/0103）。

升级固化的期望值按升级前版本的解析规则写出：没有默认模型设置时，按注册顺序取第一个有凭证、默认模型可用（在清单里、
不低于最低应用版本、未下线、未被停用）的内置供应商，都没有再落到自定义供应商的默认模型；目录数据取旧出厂快照与
同步数据中数据集版本较新的那份，叠上用户修改过的条目副本。
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.dml import Insert

from arcreel_catalog_core import MEDIA_TYPES, EntryKey
from lib.catalog import lifecycle
from lib.catalog import query as catalog
from lib.catalog.user_entries import edited_entry, save_user_entry
from lib.config import pre_upgrade_recommendations, recommended_models
from lib.config.recommended_models import (
    FROZEN_SETTING_KEY,
    SEEN_KEY_PREFIX,
    RecommendationChange,
    StaleRecommendationError,
    acknowledge_recommendation,
    freeze_effective_defaults,
    recommendation_changes,
    seed_provider_recommendations,
)
from lib.config.resolver import ConfigResolver, NoAvailableProviderError, ProviderModel
from lib.config.service import ConfigService
from lib.db import upgrade_source
from lib.db.models.builtin_provider_model import BuiltinProviderModelState
from lib.db.models.catalog_official_sync import CatalogOfficialSync
from lib.db.models.custom_provider import CustomProvider, CustomProviderModel
from lib.db.repositories.catalog_user_entry_repo import CatalogUserEntryRepository
from lib.db.repositories.credential_repository import CredentialRepository
from tests.catalog_support import (
    install_catalog,
    install_synced_catalog,
    with_dataset_version,
    with_min_app_version,
    with_recommendation,
)

Sessions = async_sessionmaker[AsyncSession]

#: 升级前库里的 alembic revision：0.36.0，以及 0.34.0 与 0.35.0（两版库结构相同）。
REVISION_0_36 = "f6d2a8c4b931"
REVISION_0_34_0_35 = "b5d1e8f3a627"
#: 0.36.0 出厂快照的数据集版本；比它新的同步数据才会被 0.36.0 的实例读到。
SNAPSHOT_0_36 = "2026-11-04T23:26:27Z"
NEWER_THAN_ALL_SNAPSHOTS = "2026-11-08T00:00:00Z"
NEXT_IMAGE = "doubao-seedream-4-5-251128"


async def _configure(sessions: Sessions, provider_id: str) -> None:
    async with sessions() as session:
        await CredentialRepository(session).create(provider=provider_id, name="主 Key", api_key=f"TEST-{provider_id}")
        await session.commit()


async def _settings(sessions: Sessions) -> dict[str, str]:
    async with sessions() as session:
        return await ConfigService(session).get_all_settings()


async def _seen(sessions: Sessions) -> dict[str, str]:
    return {
        key.removeprefix(SEEN_KEY_PREFIX): value
        for key, value in (await _settings(sessions)).items()
        if key.startswith(SEEN_KEY_PREFIX)
    }


async def _mark_frozen(sessions: Sessions) -> None:
    """全新安装的实例：首次启动的固化没有可固化的，只写下完成标记。"""
    await _upgrade(sessions, None)


async def _seed(sessions: Sessions, provider_id: str) -> None:
    async with sessions() as session:
        await seed_provider_recommendations(session, provider_id)
        await session.commit()


async def _changes(sessions: Sessions) -> list[RecommendationChange]:
    async with sessions() as session:
        return await recommendation_changes(session)


async def _capture(
    sessions: Sessions, previous_revision: str | None
) -> pre_upgrade_recommendations.RecommendationTable:
    """升级后首次启动：迁移环境在改写 revision 前记下升级前的 revision，迁移后算定升级前的选择。"""
    async with sessions() as session:
        await session.run_sync(lambda sync: upgrade_source.record(sync.connection(), previous_revision))
        await session.commit()
    async with sessions() as session:
        return await pre_upgrade_recommendations.capture(session)


async def _upgrade(sessions: Sessions, previous_revision: str | None) -> None:
    """升级后首次启动：记下升级前的选择，再固化。"""
    await _capture(sessions, previous_revision)
    async with sessions() as session:
        await freeze_effective_defaults(session)


async def _add_custom_image_default(sessions: Sessions) -> str:
    """自定义供应商上设为默认的图片模型；返回它的供应商 id。"""
    async with sessions() as session:
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
        return provider.provider_id


async def _resolve_image(sessions: Sessions) -> ProviderModel:
    return await ConfigResolver(sessions).resolve_image_backend(None, None, generation_type="t2i")


def _sessions_like(sessions: Sessions, session_class: type[AsyncSession]) -> Sessions:
    """同一个库上、换了会话类的 session factory：在数据库边界上插入握手或故障。"""
    return async_sessionmaker(sessions.kw["bind"], class_=session_class, expire_on_commit=False)


class _WritesFail(AsyncSession):
    """写语句一律失败的会话：模拟固化写库时的一次故障。"""

    async def execute(self, statement, *args, **kwargs):
        if isinstance(statement, Insert):
            raise RuntimeError("write failed")
        return await super().execute(statement, *args, **kwargs)


def _recommended(provider_id: str, media_type: str) -> str:
    model = catalog.default_model(provider_id, media_type)
    assert model is not None
    return model


def _synced(data=None, *, dataset_version: str = NEWER_THAN_ALL_SNAPSHOTS):
    return with_dataset_version(data or catalog.factory_snapshot(), dataset_version)


class TestSeedProviderRecommendations:
    async def test_first_configuration_writes_recommendations_into_empty_defaults(self, session_factory):
        await _mark_frozen(session_factory)
        async with session_factory() as session:
            await ConfigService(session).set_setting("default_image_backend", "openai/gpt-image-2")
            await session.commit()

        await _seed(session_factory, "ark")

        settings = await _settings(session_factory)
        # 已有设置不覆盖，空的媒体类型写入推荐；ark 没有音频推荐，音频不写
        assert settings["default_image_backend"] == "openai/gpt-image-2"
        assert settings["default_text_backend"] == f"ark/{_recommended('ark', 'text')}"
        assert settings["default_video_backend"] == f"ark/{_recommended('ark', 'video')}"
        assert "default_audio_backend" not in settings
        assert await _seen(session_factory) == {
            f"ark:{media}": _recommended("ark", media) for media in ("image", "text", "video")
        }

    async def test_a_second_provider_only_fills_what_is_still_empty(self, session_factory):
        await _mark_frozen(session_factory)
        await _seed(session_factory, "ark")
        await _seed(session_factory, "dashscope")

        settings = await _settings(session_factory)
        assert settings["default_video_backend"].startswith("ark/")
        assert settings["default_audio_backend"] == f"dashscope/{_recommended('dashscope', 'audio')}"

    async def test_unknown_provider_writes_nothing(self, session_factory):
        await _mark_frozen(session_factory)
        before = await _settings(session_factory)
        await _seed(session_factory, "custom-1")
        assert await _settings(session_factory) == before

    async def test_nothing_is_written_before_the_upgrade_freeze_completes(self, session_factory):
        """固化还没完成（例如首次启动时失败）：首次配置不写推荐，免得抢在按升级前口径的固化前面。"""
        await _seed(session_factory, "ark")
        assert await _settings(session_factory) == {}

    async def test_recreating_the_only_credential_keeps_a_pending_recommendation_change(
        self, session_factory, monkeypatch
    ):
        """推荐变化的提示出现后删掉唯一凭证再重建：已知推荐不被覆盖，提示仍在，直到用户自己处理。"""
        await _mark_frozen(session_factory)
        await _configure(session_factory, "ark")
        await _seed(session_factory, "ark")
        before = _recommended("ark", "image")
        install_catalog(monkeypatch, with_recommendation(catalog.factory_snapshot(), "ark", "image", NEXT_IMAGE))
        expected = [RecommendationChange("image", "ark", NEXT_IMAGE, f"ark/{before}")]
        assert await _changes(session_factory) == expected

        await _seed(session_factory, "ark")

        assert await _changes(session_factory) == expected
        assert (await _seen(session_factory))["ark:image"] == before


class TestRecommendationChangesKeepGenerationModels:
    @pytest.fixture(autouse=True)
    async def ark_configured(self, session_factory):
        await _mark_frozen(session_factory)
        await _configure(session_factory, "ark")
        await _seed(session_factory, "ark")

    async def test_changed_recommendation_does_not_change_the_configured_model(self, session_factory, monkeypatch):
        """已配置的用户：目录推荐变化后生成用的模型不变，推荐变化以提示呈现，由用户决定是否切换。"""
        before = _recommended("ark", "image")
        resolver = ConfigResolver(session_factory)
        assert await resolver.resolve_image_backend(None, None, generation_type="t2i") == ProviderModel("ark", before)
        assert await _changes(session_factory) == []

        install_catalog(monkeypatch, with_recommendation(catalog.factory_snapshot(), "ark", "image", NEXT_IMAGE))

        assert await resolver.resolve_image_backend(None, None, generation_type="t2i") == ProviderModel("ark", before)
        assert await _changes(session_factory) == [RecommendationChange("image", "ark", NEXT_IMAGE, f"ark/{before}")]

    async def test_keeping_the_current_model_dismisses_the_notice(self, session_factory, monkeypatch):
        before = _recommended("ark", "image")
        install_catalog(monkeypatch, with_recommendation(catalog.factory_snapshot(), "ark", "image", NEXT_IMAGE))

        async with session_factory() as session:
            await acknowledge_recommendation(session, "ark", "image", NEXT_IMAGE, apply=False)
            await session.commit()

        assert await _changes(session_factory) == []
        assert (await _settings(session_factory))["default_image_backend"] == f"ark/{before}"

    async def test_switching_sets_the_global_default(self, session_factory, monkeypatch):
        install_catalog(monkeypatch, with_recommendation(catalog.factory_snapshot(), "ark", "image", NEXT_IMAGE))

        async with session_factory() as session:
            await acknowledge_recommendation(session, "ark", "image", NEXT_IMAGE, apply=True)
            await session.commit()

        assert await _changes(session_factory) == []
        resolved = await ConfigResolver(session_factory).resolve_image_backend(None, None, generation_type="t2i")
        assert resolved == ProviderModel("ark", NEXT_IMAGE)

    async def test_acknowledging_a_stale_recommendation_changes_nothing(self, session_factory):
        before = await _settings(session_factory)
        async with session_factory() as session:
            with pytest.raises(StaleRecommendationError):
                await acknowledge_recommendation(session, "ark", "image", NEXT_IMAGE, apply=True)
        assert await _settings(session_factory) == before

    async def test_a_model_chosen_by_the_user_is_not_nagged_until_the_recommendation_changes(
        self, session_factory, monkeypatch
    ):
        async with session_factory() as session:
            await ConfigService(session).set_setting("default_image_backend", "ark/doubao-seedream-4-0-250828")
            await session.commit()
        assert await _changes(session_factory) == []

        install_catalog(monkeypatch, with_recommendation(catalog.factory_snapshot(), "ark", "image", NEXT_IMAGE))
        assert [change.recommended_model for change in await _changes(session_factory)] == [NEXT_IMAGE]


async def test_concurrent_acknowledgements_of_two_media_types_are_both_kept(concurrent_session_factory, monkeypatch):
    """同一供应商两个媒体类型的推荐同时变化、各自确认：两次确认都在对方读完之后才写，两条都记下。

    每次确认用独立的连接与事务，握手放在数据库边界（第一条写语句之前）；SQLite 文件库与 PostgreSQL 下都成立，
    不靠进程内的锁。
    """
    sessions = concurrent_session_factory
    await _mark_frozen(sessions)
    await _configure(sessions, "ark")
    await _seed(sessions, "ark")
    next_video = "doubao-seedance-2-0-260128"
    changed = with_recommendation(catalog.factory_snapshot(), "ark", "image", NEXT_IMAGE)
    install_catalog(monkeypatch, with_recommendation(changed, "ark", "video", next_video))
    assert len(await _changes(sessions)) == 2

    both_read = asyncio.Barrier(2)

    class _ReadsBeforeWrites(AsyncSession):
        """执行第一条写语句前等另一个会话也走到这里：两次确认都在对方读完之后才写。"""

        waited = False

        async def execute(self, statement, *args, **kwargs):
            if isinstance(statement, Insert) and not self.waited:
                self.waited = True
                await asyncio.wait_for(both_read.wait(), 5)
            return await super().execute(statement, *args, **kwargs)

    racing = _sessions_like(sessions, _ReadsBeforeWrites)

    async def acknowledge(media_type: str, model: str) -> None:
        async with racing() as session:
            await acknowledge_recommendation(session, "ark", media_type, model, apply=False)
            await session.commit()

    await asyncio.gather(acknowledge("image", NEXT_IMAGE), acknowledge("video", next_video))

    assert await _changes(sessions) == []
    seen = await _seen(sessions)
    assert (seen["ark:image"], seen["ark:video"]) == (NEXT_IMAGE, next_video)


async def test_a_new_recommendation_is_suggested_when_the_default_is_empty(session_factory, monkeypatch):
    """供应商配置时还没有这个媒体类型的推荐，之后才有：全局默认模型为空时提示设为默认。"""
    snapshot = with_recommendation(catalog.factory_snapshot(), "ark", "image", None)
    install_catalog(monkeypatch, snapshot)
    await _mark_frozen(session_factory)
    await _configure(session_factory, "ark")
    await _seed(session_factory, "ark")
    assert "default_image_backend" not in await _settings(session_factory)

    install_catalog(monkeypatch, with_recommendation(snapshot, "ark", "image", NEXT_IMAGE))

    assert await _changes(session_factory) == [RecommendationChange("image", "ark", NEXT_IMAGE, None)]


class TestFreezeEffectiveDefaults:
    async def test_a_recommendation_hidden_from_the_old_version_does_not_take_over(
        self, session_factory, monkeypatch, request
    ):
        """0.36.0 的实例读到的同步数据里，ark 的推荐图片模型要求 0.36.1：旧版跳过 ark、用 DashScope。
        升级到 0.37.0 后 ark 的推荐可用了，固化仍写 DashScope，生成用的模型与升级前一致。"""
        data = with_min_app_version(_synced(), "ark", _recommended("ark", "image"), "0.36.1")
        # 新版本号先于数据装入：目录查询按装入的数据重建索引，用的就是新版本号
        monkeypatch.setattr(catalog, "app_version", lambda: "0.37.0")
        install_synced_catalog(request, data)
        assert catalog.default_model("ark", "image") is not None  # 新版本里 ark 的推荐可用
        await _configure(session_factory, "ark")
        await _configure(session_factory, "dashscope")

        await _upgrade(session_factory, REVISION_0_36)

        resolved = await ConfigResolver(session_factory).resolve_image_backend(None, None, generation_type="t2i")
        assert resolved == ProviderModel("dashscope", _recommended("dashscope", "image"))
        # ark 的其余推荐旧版可用，照常固化
        assert (await _settings(session_factory))["default_video_backend"] == f"ark/{_recommended('ark', 'video')}"

    async def test_the_old_factory_snapshot_is_used_when_no_newer_data_was_synced(
        self, session_factory, monkeypatch, request
    ):
        """没有比旧出厂快照更新的同步数据：旧版读的是自己的出厂快照，不是新镜像带来的快照。"""
        install_catalog(monkeypatch, with_recommendation(catalog.factory_snapshot(), "ark", "image", NEXT_IMAGE))
        install_synced_catalog(request, _synced(dataset_version="2026-11-01T00:00:00Z"))
        await _configure(session_factory, "ark")

        await _upgrade(session_factory, REVISION_0_36)

        # 0.36.0 出厂快照里 ark 的图片默认模型
        assert (await _settings(session_factory))["default_image_backend"] == "ark/doubao-seedream-5-0-pro-260628"

    async def test_the_same_revision_for_two_releases_freezes_only_what_both_agree_on(
        self, session_factory, monkeypatch, request
    ):
        """0.34.0 与 0.35.0 的库结构相同。同步数据比 0.34.0 的快照新、比 0.35.0 的旧：两版读到不同的 ark 图片推荐，
        这一项还原不了，图片留空、提示设为默认；两版一致的视频照常固化。"""
        data = with_recommendation(catalog.factory_snapshot(), "ark", "image", NEXT_IMAGE)
        install_synced_catalog(request, _synced(data, dataset_version="2026-10-28T00:00:00Z"))
        await _configure(session_factory, "ark")

        await _upgrade(session_factory, REVISION_0_34_0_35)

        settings = await _settings(session_factory)
        assert "default_image_backend" not in settings
        assert settings["default_video_backend"] == f"ark/{_recommended('ark', 'video')}"
        with pytest.raises(NoAvailableProviderError):
            await ConfigResolver(session_factory).resolve_image_backend(None, None, generation_type="t2i")
        assert [change.media_type for change in await _changes(session_factory)] == ["image"]

    async def test_a_release_below_the_minimum_version_skips_to_the_next_provider(
        self, session_factory, monkeypatch, request
    ):
        """从 0.34 或 0.35 升级：同步数据里 DashScope 的推荐音频模型要求 0.35.1，两版都跳过它，用 MiniMax。"""
        data = with_min_app_version(_synced(), "dashscope", _recommended("dashscope", "audio"), "0.35.1")
        install_synced_catalog(request, data)
        await _configure(session_factory, "dashscope")
        await _configure(session_factory, "minimax")

        await _upgrade(session_factory, REVISION_0_34_0_35)

        assert (await _settings(session_factory))[
            "default_audio_backend"
        ] == f"minimax/{_recommended('minimax', 'audio')}"

    async def test_a_recommendation_disabled_by_the_user_is_skipped(self, session_factory, monkeypatch, request):
        """用户停用了 ark 的推荐图片模型：旧版自动挑选跳过 ark，落到下一个供应商。"""
        install_synced_catalog(request, _synced())
        await _configure(session_factory, "dashscope")
        await _configure(session_factory, "ark")
        async with session_factory() as session:
            await ConfigService(session).set_setting("default_text_backend", "openai/gpt-5.4-mini")
            session.add(
                BuiltinProviderModelState(provider_id="ark", model_id=_recommended("ark", "image"), enabled=False)
            )
            await session.commit()

        await _upgrade(session_factory, REVISION_0_36)

        settings = await _settings(session_factory)
        assert settings["default_text_backend"] == "openai/gpt-5.4-mini"
        assert settings["default_image_backend"] == f"dashscope/{_recommended('dashscope', 'image')}"
        assert settings["default_video_backend"] == f"ark/{_recommended('ark', 'video')}"
        assert settings["default_audio_backend"] == f"dashscope/{_recommended('dashscope', 'audio')}"

    async def test_frozen_models_no_longer_follow_the_catalog(self, session_factory, monkeypatch, request):
        install_synced_catalog(request, _synced())
        await _configure(session_factory, "gemini-vertex")
        expected = ProviderModel("gemini-vertex", _recommended("gemini-vertex", "video"))

        await _upgrade(session_factory, REVISION_0_36)
        install_synced_catalog(
            request, with_recommendation(_synced(), "gemini-vertex", "video", "veo-3.1-generate-001")
        )

        assert (
            await ConfigResolver(session_factory).resolve_video_backend(None, None, generation_type="i2v") == expected
        )
        # 推荐变了，提示由用户决定
        assert [change.media_type for change in await _changes(session_factory)] == ["video"]

    async def test_an_unknown_previous_revision_freezes_nothing(self, session_factory):
        """升级前的库对不上已知发布版（0.34 之前、或两个发布版之间的预览镜像）：不替用户挑模型，生成报错并提示。"""
        await _configure(session_factory, "ark")

        await _upgrade(session_factory, "e3b81f6c4a27")

        settings = await _settings(session_factory)
        assert not any(settings.get(key) for key in recommended_models.GLOBAL_DEFAULT_KEYS.values())
        assert settings[FROZEN_SETTING_KEY]
        with pytest.raises(NoAvailableProviderError):
            await ConfigResolver(session_factory).resolve_video_backend(None, None, generation_type="i2v")
        assert {change.media_type for change in await _changes(session_factory)} == {"image", "text", "video"}

    @pytest.mark.parametrize("recorded", [True, False], ids=["unknown-revision", "no-record"])
    async def test_an_unrestorable_pick_does_not_fall_back_to_a_custom_default(self, session_factory, recorded):
        """旧版本 Ark 有凭证、另有自定义图片默认模型：旧版本选 Ark。还原不了时不落到自定义默认，生成报错。

        升级前的 revision 对不上已知发布版，或根本没有记下来源，结果一样。
        """
        await _configure(session_factory, "ark")
        await _add_custom_image_default(session_factory)

        if recorded:
            await _upgrade(session_factory, "e3b81f6c4a27")
        else:
            async with session_factory() as session:
                await pre_upgrade_recommendations.capture(session)
            async with session_factory() as session:
                await freeze_effective_defaults(session)

        assert "default_image_backend" not in await _settings(session_factory)
        with pytest.raises(NoAvailableProviderError):
            await _resolve_image(session_factory)

    async def test_stored_sync_data_that_failed_to_load_freezes_nothing(self, session_factory):
        """库里存着同步数据、这次启动没能读入：旧实例读的可能就是它，不拿出厂快照代替，默认模型留空由用户选择。"""
        await _configure(session_factory, "ark")
        async with session_factory() as session:
            session.add(CatalogOfficialSync(id=1, dataset_version=NEWER_THAN_ALL_SNAPSHOTS, catalog=b"{}"))
            await session.commit()

        await _upgrade(session_factory, REVISION_0_36)

        settings = await _settings(session_factory)
        assert not any(settings.get(key) for key in recommended_models.GLOBAL_DEFAULT_KEYS.values())
        assert settings[FROZEN_SETTING_KEY]

    async def test_custom_provider_defaults_are_not_frozen(self, session_factory):
        """旧版自动挑选落到自定义供应商默认模型的，运行时仍按同一规则挑选，不写入用户设置。"""
        provider_id = await _add_custom_image_default(session_factory)

        await _upgrade(session_factory, REVISION_0_36)

        assert "default_image_backend" not in await _settings(session_factory)
        assert await _resolve_image(session_factory) == ProviderModel(provider_id, "image-m")

    async def test_a_failed_freeze_is_retried_and_a_provider_added_meanwhile_does_not_take_over(
        self, session_factory, request
    ):
        """升级前只有 DashScope 有凭证，旧版本选 DashScope。首次固化写库失败，之后用户加了排在前面的 Ark：

        期间首次配置不写推荐，没有默认模型的生成报错，也不落到自定义默认；下次启动重试只应用首次算定的结果，
        固化 DashScope，不重新读凭证。
        """
        install_synced_catalog(request, _synced())
        await _configure(session_factory, "dashscope")
        await _add_custom_image_default(session_factory)
        await _capture(session_factory, REVISION_0_36)

        async with _sessions_like(session_factory, _WritesFail)() as session:
            with pytest.raises(RuntimeError):
                await freeze_effective_defaults(session)
        assert FROZEN_SETTING_KEY not in await _settings(session_factory)

        await _configure(session_factory, "ark")
        await _seed(session_factory, "ark")
        settings = await _settings(session_factory)
        assert not any(settings.get(key) for key in recommended_models.GLOBAL_DEFAULT_KEYS.values())
        with pytest.raises(NoAvailableProviderError):
            await _resolve_image(session_factory)

        async with session_factory() as session:
            await freeze_effective_defaults(session)

        assert await _resolve_image(session_factory) == ProviderModel("dashscope", _recommended("dashscope", "image"))
        settings = await _settings(session_factory)
        assert settings["default_video_backend"] == f"dashscope/{_recommended('dashscope', 'video')}"
        # Ark 是升级后才加的：不记它的已知推荐，空着的媒体类型照常提示
        assert not any(key.startswith(f"{SEEN_KEY_PREFIX}ark:") for key in settings)

    async def test_runs_only_once(self, session_factory):
        await _upgrade(session_factory, REVISION_0_36)
        assert (await _settings(session_factory))[FROZEN_SETTING_KEY]

        await _configure(session_factory, "ark")
        async with session_factory() as session:
            await freeze_effective_defaults(session)
        settings = await _settings(session_factory)
        assert not any(settings.get(key) for key in recommended_models.GLOBAL_DEFAULT_KEYS.values())

    async def test_the_pre_upgrade_recommendations_are_computed_once(self, session_factory, request):
        """升级前的选择只在首次启动时算：之后的官方同步换掉同步数据、用户加了凭证，读回的仍是升级前的那一份。"""
        install_synced_catalog(request, _synced())
        first = await _capture(session_factory, REVISION_0_36)
        before = _recommended("ark", "image")

        install_synced_catalog(request, with_recommendation(_synced(), "ark", "image", NEXT_IMAGE))
        await _configure(session_factory, "ark")
        async with session_factory() as session:
            again = await pre_upgrade_recommendations.capture(session)

        assert again == first
        assert again.model("ark", "image") == before
        assert again.auto_pick("image").kind == "fallback"

    @pytest.mark.parametrize("synced", [False, True], ids=["old-snapshot", "synced-data"])
    async def test_a_user_copy_that_marks_the_default_discontinued_is_skipped(self, session_factory, request, synced):
        """用户改过 Ark 推荐图片模型的条目，副本带着官方当时给的关停日期（已过）：旧版本按已下线跳过 Ark，
        选 DashScope。固化与之一致，不固化已下线的模型。"""
        if synced:
            install_synced_catalog(request, _synced())
        key = EntryKey("bytedance", "doubao-seedream-5-0-pro", "image")
        entry = catalog.get_entry(key)
        assert entry is not None
        copy = edited_entry(
            replace(entry, shutdown_date=lifecycle.utc_today() - timedelta(days=1)), display_name="My image"
        )
        async with session_factory() as session:
            await save_user_entry(session, copy)
            await session.commit()
        await _configure(session_factory, "ark")
        await _configure(session_factory, "dashscope")

        await _upgrade(session_factory, REVISION_0_36)

        assert await _resolve_image(session_factory) == ProviderModel("dashscope", _recommended("dashscope", "image"))

    async def test_an_unreadable_user_copy_of_the_default_cannot_be_restored(self, session_factory):
        """Ark 推荐图片模型的条目有读不出的用户副本：旧版本是否跳过它判断不了，图片留空、生成报错。"""
        async with session_factory() as session:
            await CatalogUserEntryRepository(session).put(
                EntryKey("bytedance", "doubao-seedream-5-0-pro", "image"), {"maker": "bytedance"}
            )
            await session.commit()
        await _configure(session_factory, "ark")
        await _configure(session_factory, "dashscope")

        await _upgrade(session_factory, REVISION_0_36)

        settings = await _settings(session_factory)
        assert "default_image_backend" not in settings
        assert settings["default_video_backend"] == f"ark/{_recommended('ark', 'video')}"
        with pytest.raises(NoAvailableProviderError):
            await _resolve_image(session_factory)

    @pytest.mark.parametrize("unreadable", ["video", "image"])
    async def test_an_unreadable_copy_is_matched_by_the_media_type_of_the_default(
        self, session_factory, request, unreadable
    ):
        """Kling 的 ``kling-v3-omni`` 同名列出图片与视频两个模型。同步数据把它设为视频推荐时，只有视频条目读不出的
        副本让视频还原不了；图片条目读不出的副本不影响视频。"""
        install_synced_catalog(request, with_recommendation(_synced(), "kling", "video", "kling-v3-omni"))
        async with session_factory() as session:
            await CatalogUserEntryRepository(session).put(
                EntryKey("kuaishou", "kling-v3-omni", unreadable), {"maker": "kuaishou"}
            )
            await session.commit()
        await _configure(session_factory, "kling")

        await _upgrade(session_factory, REVISION_0_36)

        video = (await _settings(session_factory)).get("default_video_backend")
        assert video == (None if unreadable == "video" else "kling/kling-v3-omni")


async def test_a_fresh_install_has_nothing_to_freeze_and_seeds_on_first_configuration(session_factory):
    """全新的库（升级前没有 revision）：首次启动只写完成标记，之后首次配置供应商写入推荐。"""
    await _upgrade(session_factory, None)
    settings = await _settings(session_factory)
    assert settings[FROZEN_SETTING_KEY]
    assert not any(settings.get(key) for key in recommended_models.GLOBAL_DEFAULT_KEYS.values())

    await _configure(session_factory, "ark")
    await _seed(session_factory, "ark")

    assert (await _settings(session_factory))["default_video_backend"] == f"ark/{_recommended('ark', 'video')}"


async def test_import_time_recommendations_skip_models_disabled_by_the_user(session_factory):
    """归档导入按此刻的推荐补全裸供应商：被停用的推荐不算，裸供应商原样保留，与运行时报错的口径一致。"""
    disabled = _recommended("ark", "video")
    async with session_factory() as session:
        session.add(BuiltinProviderModelState(provider_id="ark", model_id=disabled, enabled=False))
        await session.commit()
        table = await pre_upgrade_recommendations.current_recommendations(session)

    assert table.model("ark", "video") is None
    assert table.model("ark", "image") == _recommended("ark", "image")


def test_global_default_keys_cover_every_media_type() -> None:
    assert set(recommended_models.GLOBAL_DEFAULT_KEYS) == set(MEDIA_TYPES)
