"""自定义 backend DB 装载（lib.custom_provider.loader）单测：内存 SQLite + 真 CustomProviderRepository。

查 provider、校验 model（存在 / 启用 / endpoint 推算 media_type 相符）、失效回退默认、委托现成
create_custom_backend（ENDPOINT_REGISTRY 不改）。镜像 test_custom_provider_repo.py 的内存 DB 范式，不 mock repo。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.custom_provider import make_provider_id
from lib.custom_provider.backends import CustomImageBackend
from lib.custom_provider.loader import load_custom_backend
from lib.db.base import Base
from lib.db.repositories.custom_provider_repo import CustomProviderRepository


@pytest.fixture()
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _seed(session, *, models: list[dict]) -> str:
    repo = CustomProviderRepository(session)
    # display_name 列 NOT NULL：缺省补 model_id 作显示名
    for m in models:
        m.setdefault("display_name", m["model_id"])
    provider = await repo.create_provider(
        display_name="Relay",
        discovery_format="openai",
        base_url="https://relay.test/v1",
        api_key="sk-relay",
        models=models,
    )
    await session.commit()
    return make_provider_id(provider.id)


class TestLoadCustomBackend:
    @patch("lib.custom_provider.endpoints.OpenAIImageBackend")
    async def test_resolves_named_model_and_delegates(self, mock_cls, session):
        pid = await _seed(
            session,
            models=[{"model_id": "dall-e-3", "endpoint": "openai-images", "is_enabled": True}],
        )
        result = await load_custom_backend(session=session, provider_id=pid, model_id="dall-e-3", media_type="image")
        assert isinstance(result, CustomImageBackend)
        assert result.model == "dall-e-3"
        mock_cls.assert_called_once_with(api_key="sk-relay", base_url="https://relay.test/v1", model="dall-e-3")

    @patch("lib.custom_provider.endpoints.OpenAIImageBackend")
    async def test_falls_back_to_default_when_model_disabled(self, mock_cls, session):
        # 请求的 model 已禁用 → 回退到该 media_type 的默认启用 model
        pid = await _seed(
            session,
            models=[
                {"model_id": "disabled-m", "endpoint": "openai-images", "is_enabled": False},
                {"model_id": "active-m", "endpoint": "openai-images", "is_enabled": True, "is_default": True},
            ],
        )
        result = await load_custom_backend(session=session, provider_id=pid, model_id="disabled-m", media_type="image")
        assert result.model == "active-m"

    async def test_provider_not_found_fails_loud(self, session):
        with pytest.raises(ValueError, match="不存在"):
            await load_custom_backend(
                session=session, provider_id=make_provider_id(999), model_id="x", media_type="image"
            )

    async def test_no_default_model_for_media_fails_loud(self, session):
        # 只有 image model，请求 video → 无默认 video model
        pid = await _seed(
            session,
            models=[{"model_id": "dall-e-3", "endpoint": "openai-images", "is_enabled": True}],
        )
        with pytest.raises(ValueError, match="没有默认"):
            await load_custom_backend(session=session, provider_id=pid, model_id=None, media_type="video")
