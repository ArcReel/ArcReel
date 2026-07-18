"""CustomProviderRepository 测试。"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from lib.db.base import Base
from lib.db.repositories.custom_provider_repo import CustomProviderPrice, CustomProviderRepository


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


class TestProviderCRUD:
    async def test_create_provider_without_models(self, session: AsyncSession):
        repo = CustomProviderRepository(session)
        provider = await repo.create_provider(
            display_name="My OpenAI",
            discovery_format="openai",
            base_url="https://api.openai.com/v1",
            api_key="sk-test-123",
        )
        await session.flush()
        assert provider.id is not None
        assert provider.display_name == "My OpenAI"
        assert provider.discovery_format == "openai"
        assert provider.base_url == "https://api.openai.com/v1"
        assert provider.api_key == "sk-test-123"

    async def test_create_provider_with_models(self, session: AsyncSession):
        repo = CustomProviderRepository(session)
        models = [
            {
                "model_id": "gpt-4o",
                "display_name": "GPT-4o",
                "endpoint": "openai-chat",
                "is_default": True,
                "is_enabled": True,
            },
            {
                "model_id": "dall-e-3",
                "display_name": "DALL-E 3",
                "endpoint": "openai-images",
                "is_default": True,
                "is_enabled": True,
                "price_unit": "image",
                "price_input": 0.04,
                "currency": "USD",
            },
        ]
        provider = await repo.create_provider(
            display_name="My OpenAI",
            discovery_format="openai",
            base_url="https://api.openai.com/v1",
            api_key="sk-test-123",
            models=models,
        )
        await session.flush()

        result = await repo.list_models(provider.id)
        assert len(result) == 2
        assert result[0].model_id == "gpt-4o"
        assert result[1].model_id == "dall-e-3"
        assert result[1].price_unit == "image"
        assert result[1].price_input == 0.04

    async def test_get_provider(self, session: AsyncSession):
        repo = CustomProviderRepository(session)
        created = await repo.create_provider(
            display_name="Test",
            discovery_format="openai",
            base_url="https://example.com",
            api_key="key",
        )
        await session.flush()
        found = await repo.get_provider(created.id)
        assert found is not None
        assert found.display_name == "Test"

    async def test_get_provider_returns_none(self, session: AsyncSession):
        repo = CustomProviderRepository(session)
        assert await repo.get_provider(999) is None

    async def test_list_providers(self, session: AsyncSession):
        repo = CustomProviderRepository(session)
        await repo.create_provider(
            display_name="Provider A",
            discovery_format="openai",
            base_url="https://a.com",
            api_key="key-a",
        )
        await repo.create_provider(
            display_name="Provider B",
            discovery_format="google",
            base_url="https://b.com",
            api_key="key-b",
        )
        await session.flush()
        providers = await repo.list_providers()
        assert len(providers) == 2
        assert providers[0].display_name == "Provider A"
        assert providers[1].display_name == "Provider B"

    async def test_update_provider(self, session: AsyncSession):
        repo = CustomProviderRepository(session)
        p = await repo.create_provider(
            display_name="Old Name",
            discovery_format="openai",
            base_url="https://old.com",
            api_key="old-key",
        )
        await session.flush()

        await repo.update_provider(p.id, display_name="New Name", api_key="new-key")
        await session.flush()

        updated = await repo.get_provider(p.id)
        assert updated is not None
        assert updated.display_name == "New Name"
        assert updated.api_key == "new-key"
        assert updated.base_url == "https://old.com"  # unchanged

    async def test_update_provider_nonexistent(self, session: AsyncSession):
        repo = CustomProviderRepository(session)
        result = await repo.update_provider(999, display_name="Nope")
        assert result is None

    async def test_delete_provider_cascades_to_models(self, session: AsyncSession):
        repo = CustomProviderRepository(session)
        p = await repo.create_provider(
            display_name="ToDelete",
            discovery_format="openai",
            base_url="https://del.com",
            api_key="key",
            models=[
                {
                    "model_id": "m1",
                    "display_name": "Model 1",
                    "endpoint": "openai-chat",
                },
                {
                    "model_id": "m2",
                    "display_name": "Model 2",
                    "endpoint": "openai-images",
                },
            ],
        )
        await session.flush()

        await repo.delete_provider(p.id)
        await session.flush()

        assert await repo.get_provider(p.id) is None
        assert await repo.list_models(p.id) == []

    async def test_delete_provider_nonexistent(self, session: AsyncSession):
        repo = CustomProviderRepository(session)
        # Should not raise
        await repo.delete_provider(999)


class TestConcurrencyColumns:
    async def test_create_without_workers_defaults_to_null(self, session: AsyncSession):
        repo = CustomProviderRepository(session)
        p = await repo.create_provider(
            display_name="P",
            discovery_format="openai",
            base_url="https://x",
            api_key="k",
        )
        await session.flush()
        got = await repo.get_provider(p.id)
        assert got is not None
        assert got.image_max_workers is None
        assert got.video_max_workers is None
        assert got.audio_max_workers is None

    async def test_create_with_workers_round_trip(self, session: AsyncSession):
        repo = CustomProviderRepository(session)
        p = await repo.create_provider(
            display_name="P",
            discovery_format="openai",
            base_url="https://x",
            api_key="k",
            image_max_workers=2,
            video_max_workers=7,
            audio_max_workers=1,
        )
        await session.flush()
        got = await repo.get_provider(p.id)
        assert got is not None
        assert got.image_max_workers == 2
        assert got.video_max_workers == 7
        assert got.audio_max_workers == 1

    async def test_update_workers_including_clear_to_null(self, session: AsyncSession):
        repo = CustomProviderRepository(session)
        p = await repo.create_provider(
            display_name="P",
            discovery_format="openai",
            base_url="https://x",
            api_key="k",
            image_max_workers=5,
        )
        await session.flush()

        await repo.update_provider(p.id, image_max_workers=None, video_max_workers=4)
        await session.flush()

        got = await repo.get_provider(p.id)
        assert got is not None
        assert got.image_max_workers is None
        assert got.video_max_workers == 4

    @pytest.mark.parametrize("field", ["image_max_workers", "video_max_workers", "audio_max_workers"])
    @pytest.mark.parametrize("value", [-1, 0])
    async def test_create_non_positive_workers_rejected_by_check_constraint(
        self, session: AsyncSession, field: str, value: int
    ):
        """DB 层 CHECK 约束拦截 0 与负值，repo 直写也无法绕过（create_provider 内部 flush 即触发）。"""
        from sqlalchemy.exc import IntegrityError

        repo = CustomProviderRepository(session)
        with pytest.raises(IntegrityError):
            await repo.create_provider(
                display_name="P",
                discovery_format="openai",
                base_url="https://x",
                api_key="k",
                **{field: value},
            )


class TestModelManagement:
    async def _make_provider(self, repo: CustomProviderRepository, session: AsyncSession) -> int:
        p = await repo.create_provider(
            display_name="TestProvider",
            discovery_format="openai",
            base_url="https://example.com",
            api_key="key",
        )
        await session.flush()
        return p.id

    async def test_list_models_empty(self, session: AsyncSession):
        repo = CustomProviderRepository(session)
        pid = await self._make_provider(repo, session)
        assert await repo.list_models(pid) == []

    async def test_replace_models(self, session: AsyncSession):
        repo = CustomProviderRepository(session)
        p = await repo.create_provider(
            display_name="TestProvider",
            discovery_format="openai",
            base_url="https://example.com",
            api_key="key",
            models=[
                {"model_id": "old-model", "display_name": "Old", "endpoint": "openai-chat"},
            ],
        )
        await session.flush()

        new_models = [
            {"model_id": "new-1", "display_name": "New 1", "endpoint": "openai-chat", "is_default": True},
            {"model_id": "new-2", "display_name": "New 2", "endpoint": "openai-images"},
        ]
        await repo.replace_models(p.id, new_models)
        await session.flush()

        models = await repo.list_models(p.id)
        assert len(models) == 2
        model_ids = [m.model_id for m in models]
        assert "old-model" not in model_ids
        assert "new-1" in model_ids
        assert "new-2" in model_ids

    async def test_replace_models_with_empty_list(self, session: AsyncSession):
        repo = CustomProviderRepository(session)
        p = await repo.create_provider(
            display_name="TestProvider",
            discovery_format="openai",
            base_url="https://example.com",
            api_key="key",
            models=[
                {"model_id": "m1", "display_name": "M1", "endpoint": "openai-chat"},
            ],
        )
        await session.flush()

        await repo.replace_models(p.id, [])
        await session.flush()

        assert await repo.list_models(p.id) == []

    async def test_update_model(self, session: AsyncSession):
        repo = CustomProviderRepository(session)
        p = await repo.create_provider(
            display_name="TestProvider",
            discovery_format="openai",
            base_url="https://example.com",
            api_key="key",
            models=[
                {
                    "model_id": "gpt-4o",
                    "display_name": "GPT-4o",
                    "endpoint": "openai-chat",
                    "price_unit": "token",
                    "price_input": 0.01,
                    "price_output": 0.03,
                    "currency": "USD",
                },
            ],
        )
        await session.flush()

        models = await repo.list_models(p.id)
        model = models[0]

        await repo.update_model(model.id, price_input=0.005, price_output=0.015)
        await session.flush()

        updated_models = await repo.list_models(p.id)
        assert updated_models[0].price_input == 0.005
        assert updated_models[0].price_output == 0.015
        assert updated_models[0].display_name == "GPT-4o"  # unchanged

    async def test_update_model_nonexistent(self, session: AsyncSession):
        repo = CustomProviderRepository(session)
        result = await repo.update_model(999, display_name="Nope")
        assert result is None

    async def test_delete_model(self, session: AsyncSession):
        repo = CustomProviderRepository(session)
        p = await repo.create_provider(
            display_name="TestProvider",
            discovery_format="openai",
            base_url="https://example.com",
            api_key="key",
            models=[
                {"model_id": "m1", "display_name": "M1", "endpoint": "openai-chat"},
                {"model_id": "m2", "display_name": "M2", "endpoint": "openai-chat"},
            ],
        )
        await session.flush()

        models = await repo.list_models(p.id)
        await repo.delete_model(models[0].id)
        await session.flush()

        remaining = await repo.list_models(p.id)
        assert len(remaining) == 1
        assert remaining[0].model_id == "m2"

    async def test_delete_model_nonexistent(self, session: AsyncSession):
        repo = CustomProviderRepository(session)
        # Should not raise
        await repo.delete_model(999)

    async def test_list_enabled_models_by_media_type(self, session: AsyncSession):
        repo = CustomProviderRepository(session)
        await repo.create_provider(
            display_name="Provider1",
            discovery_format="openai",
            base_url="https://p1.com",
            api_key="key1",
            models=[
                {"model_id": "text-1", "display_name": "Text 1", "endpoint": "openai-chat", "is_enabled": True},
                {"model_id": "img-1", "display_name": "Img 1", "endpoint": "openai-images", "is_enabled": True},
                {"model_id": "text-off", "display_name": "Text Off", "endpoint": "openai-chat", "is_enabled": False},
            ],
        )
        await repo.create_provider(
            display_name="Provider2",
            discovery_format="openai",
            base_url="https://p2.com",
            api_key="key2",
            models=[
                {"model_id": "text-2", "display_name": "Text 2", "endpoint": "openai-chat", "is_enabled": True},
                {"model_id": "vid-1", "display_name": "Vid 1", "endpoint": "newapi-video", "is_enabled": True},
            ],
        )
        await session.flush()

        text_models = await repo.list_enabled_models_by_media_type("text")
        assert len(text_models) == 2
        text_ids = {m.model_id for m in text_models}
        assert text_ids == {"text-1", "text-2"}

        image_models = await repo.list_enabled_models_by_media_type("image")
        assert len(image_models) == 1
        assert image_models[0].model_id == "img-1"

        video_models = await repo.list_enabled_models_by_media_type("video")
        assert len(video_models) == 1
        assert video_models[0].model_id == "vid-1"

    async def test_list_enabled_models_by_media_type_empty(self, session: AsyncSession):
        repo = CustomProviderRepository(session)
        result = await repo.list_enabled_models_by_media_type("text")
        assert result == []

    async def test_get_default_model(self, session: AsyncSession):
        repo = CustomProviderRepository(session)
        p = await repo.create_provider(
            display_name="TestProvider",
            discovery_format="openai",
            base_url="https://example.com",
            api_key="key",
            models=[
                {
                    "model_id": "m1",
                    "display_name": "M1",
                    "endpoint": "openai-chat",
                    "is_default": False,
                    "is_enabled": True,
                },
                {
                    "model_id": "m2",
                    "display_name": "M2",
                    "endpoint": "openai-chat",
                    "is_default": True,
                    "is_enabled": True,
                },
                {
                    "model_id": "m3",
                    "display_name": "M3",
                    "endpoint": "openai-images",
                    "is_default": True,
                    "is_enabled": True,
                },
            ],
        )
        await session.flush()

        default_text = await repo.get_default_model(p.id, "text")
        assert default_text is not None
        assert default_text.model_id == "m2"

        default_image = await repo.get_default_model(p.id, "image")
        assert default_image is not None
        assert default_image.model_id == "m3"

    async def test_get_default_model_returns_none_when_no_default(self, session: AsyncSession):
        repo = CustomProviderRepository(session)
        p = await repo.create_provider(
            display_name="TestProvider",
            discovery_format="openai",
            base_url="https://example.com",
            api_key="key",
            models=[
                {
                    "model_id": "m1",
                    "display_name": "M1",
                    "endpoint": "openai-chat",
                    "is_default": False,
                    "is_enabled": True,
                },
            ],
        )
        await session.flush()

        assert await repo.get_default_model(p.id, "text") is None

    async def test_get_default_model_ignores_disabled(self, session: AsyncSession):
        repo = CustomProviderRepository(session)
        p = await repo.create_provider(
            display_name="TestProvider",
            discovery_format="openai",
            base_url="https://example.com",
            api_key="key",
            models=[
                {
                    "model_id": "m1",
                    "display_name": "M1",
                    "endpoint": "openai-chat",
                    "is_default": True,
                    "is_enabled": False,
                },
            ],
        )
        await session.flush()

        assert await repo.get_default_model(p.id, "text") is None

    async def test_get_default_model_nonexistent_provider(self, session: AsyncSession):
        repo = CustomProviderRepository(session)
        assert await repo.get_default_model(999, "text") is None


class TestResolvePrice:
    """resolve_price：记账与预估共用的价格取数，全部降级路径归为无价、绝不抛错。"""

    async def _provider_with_model(self, repo: CustomProviderRepository, session: AsyncSession, **model_over) -> str:
        model = {
            "model_id": "m1",
            "display_name": "M1",
            "endpoint": "openai-chat",
            "is_enabled": True,
            "price_unit": "token",
            "price_input": 2.0,
            "price_output": 4.0,
            "currency": "CNY",
        }
        model.update(model_over)
        p = await repo.create_provider(
            display_name="P",
            discovery_format="openai",
            base_url="https://x",
            api_key="k",
            models=[model],
        )
        await session.flush()
        return f"custom-{p.id}"

    async def test_non_custom_provider_returns_no_price(self, session: AsyncSession):
        repo = CustomProviderRepository(session)
        assert await repo.resolve_price("gemini-aistudio", "gemini-3-flash-preview") == CustomProviderPrice()

    async def test_valid_custom_provider_returns_declared_price(self, session: AsyncSession):
        repo = CustomProviderRepository(session)
        provider = await self._provider_with_model(repo, session)
        assert await repo.resolve_price(provider, "m1") == CustomProviderPrice(2.0, 4.0, "CNY")

    async def test_missing_model_returns_no_price(self, session: AsyncSession):
        repo = CustomProviderRepository(session)
        provider = await self._provider_with_model(repo, session)
        assert await repo.resolve_price(provider, "ghost") == CustomProviderPrice()

    async def test_malformed_id_degrades_to_no_price(self, session: AsyncSession):
        """畸形 custom- id（parse_provider_id 抛 ValueError）降级为无价，不抛错。"""
        repo = CustomProviderRepository(session)
        assert await repo.resolve_price("custom-abc/ghost", "m1") == CustomProviderPrice()

    async def test_query_exception_degrades_to_no_price(self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch):
        """底层查询异常（如 DB 瞬时不可用）降级为无价，不冒泡。"""
        repo = CustomProviderRepository(session)
        provider = await self._provider_with_model(repo, session)

        async def _boom(*_args, **_kwargs):
            raise RuntimeError("db down")

        monkeypatch.setattr(repo, "get_model_by_ids", _boom)
        assert await repo.resolve_price(provider, "m1") == CustomProviderPrice()

    async def test_disabled_model_still_priced(self, session: AsyncSession):
        """刻意豁免 enabled 校验：停用模型仍按其声明价取价（记账按实际调用的模型计费）。"""
        repo = CustomProviderRepository(session)
        provider = await self._provider_with_model(repo, session, is_enabled=False)
        assert await repo.resolve_price(provider, "m1") == CustomProviderPrice(2.0, 4.0, "CNY")


@pytest.mark.asyncio
async def test_list_enabled_models_by_media_type_uses_endpoint(session):
    """list_enabled_models_by_media_type 应按 endpoint 推算 media_type 过滤。"""
    repo = CustomProviderRepository(session)
    await repo.create_provider(
        display_name="P",
        discovery_format="openai",
        base_url="https://x",
        api_key="k",
        models=[
            {
                "model_id": "gpt-4o",
                "display_name": "gpt-4o",
                "endpoint": "openai-chat",
                "is_default": False,
                "is_enabled": True,
                "price_unit": None,
                "price_input": None,
                "price_output": None,
                "currency": None,
                "supported_durations": None,
                "resolution": None,
            },
            {
                "model_id": "kling-2",
                "display_name": "kling-2",
                "endpoint": "newapi-video",
                "is_default": False,
                "is_enabled": True,
                "price_unit": None,
                "price_input": None,
                "price_output": None,
                "currency": None,
                "supported_durations": None,
                "resolution": None,
            },
        ],
    )
    await session.commit()

    text_models = await repo.list_enabled_models_by_media_type("text")
    assert {m.model_id for m in text_models} == {"gpt-4o"}
    video_models = await repo.list_enabled_models_by_media_type("video")
    assert {m.model_id for m in video_models} == {"kling-2"}
