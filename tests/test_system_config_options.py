"""
Tests for _build_options() custom provider model merging.

Verifies that enabled custom provider models are included in the options
lists returned by the system config endpoint.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from lib.config.service import ConfigService, ProviderStatus
from lib.db.base import Base
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from server.routers.system_config import _build_options

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s, factory
    await engine.dispose()


def _make_mock_svc(ready_providers: list[str] | None = None) -> ConfigService:
    """Create a minimal mock ConfigService."""
    svc = MagicMock(spec=ConfigService)
    ready = set(ready_providers or [])

    async def _get_all_providers_status():
        from lib.config.registry import PROVIDER_REGISTRY

        return [
            ProviderStatus(
                name=name,
                display_name=meta.display_name,
                description=meta.description,
                status="ready" if name in ready else "unconfigured",
                media_types=list(meta.media_types),
                capabilities=list(meta.capabilities),
                required_keys=list(meta.required_keys),
                configured_keys=list(meta.required_keys) if name in ready else [],
                missing_keys=[] if name in ready else list(meta.required_keys),
            )
            for name, meta in PROVIDER_REGISTRY.items()
        ]

    svc.get_all_providers_status = AsyncMock(side_effect=_get_all_providers_status)
    return svc


# ---------------------------------------------------------------------------
# Tests: _build_options includes custom models
# ---------------------------------------------------------------------------


class TestBuildOptionsCustomModels:
    async def test_includes_enabled_text_model(self, session):
        db_session, factory = session
        repo = CustomProviderRepository(db_session)
        provider = await repo.create_provider(
            display_name="My LLM",
            api_format="openai",
            base_url="https://api.example.com/v1",
            api_key="sk-test",
            models=[
                {
                    "model_id": "gpt-4o",
                    "display_name": "GPT-4o",
                    "media_type": "text",
                    "is_default": True,
                    "is_enabled": True,
                }
            ],
        )
        await db_session.commit()

        mock_svc = _make_mock_svc()
        options = await _build_options(mock_svc, db_session)

        expected = f"custom-{provider.id}/gpt-4o"
        assert expected in options["text_backends"]
        assert expected not in options["image_backends"]
        assert expected not in options["video_backends"]

    async def test_includes_enabled_image_model(self, session):
        db_session, factory = session
        repo = CustomProviderRepository(db_session)
        provider = await repo.create_provider(
            display_name="My Image Provider",
            api_format="openai",
            base_url="https://api.example.com/v1",
            api_key="sk-test",
            models=[
                {
                    "model_id": "dall-e-3",
                    "display_name": "DALL-E 3",
                    "media_type": "image",
                    "is_default": True,
                    "is_enabled": True,
                }
            ],
        )
        await db_session.commit()

        mock_svc = _make_mock_svc()
        options = await _build_options(mock_svc, db_session)

        expected = f"custom-{provider.id}/dall-e-3"
        assert expected in options["image_backends"]

    async def test_includes_enabled_video_model(self, session):
        db_session, factory = session
        repo = CustomProviderRepository(db_session)
        provider = await repo.create_provider(
            display_name="My Video Provider",
            api_format="openai",
            base_url="https://api.example.com/v1",
            api_key="sk-test",
            models=[
                {
                    "model_id": "sora-preview",
                    "display_name": "Sora Preview",
                    "media_type": "video",
                    "is_default": True,
                    "is_enabled": True,
                }
            ],
        )
        await db_session.commit()

        mock_svc = _make_mock_svc()
        options = await _build_options(mock_svc, db_session)

        expected = f"custom-{provider.id}/sora-preview"
        assert expected in options["video_backends"]

    async def test_excludes_disabled_model(self, session):
        db_session, factory = session
        repo = CustomProviderRepository(db_session)
        provider = await repo.create_provider(
            display_name="My Provider",
            api_format="openai",
            base_url="https://api.example.com/v1",
            api_key="sk-test",
            models=[
                {
                    "model_id": "disabled-model",
                    "display_name": "Disabled",
                    "media_type": "text",
                    "is_default": False,
                    "is_enabled": False,
                }
            ],
        )
        await db_session.commit()

        mock_svc = _make_mock_svc()
        options = await _build_options(mock_svc, db_session)

        excluded = f"custom-{provider.id}/disabled-model"
        assert excluded not in options["text_backends"]

    async def test_multiple_providers_and_models(self, session):
        db_session, factory = session
        repo = CustomProviderRepository(db_session)
        p1 = await repo.create_provider(
            display_name="Provider A",
            api_format="openai",
            base_url="https://a.example.com/v1",
            api_key="sk-a",
            models=[
                {
                    "model_id": "model-text",
                    "display_name": "Text Model",
                    "media_type": "text",
                    "is_default": True,
                    "is_enabled": True,
                }
            ],
        )
        p2 = await repo.create_provider(
            display_name="Provider B",
            api_format="openai",
            base_url="https://b.example.com/v1",
            api_key="sk-b",
            models=[
                {
                    "model_id": "model-image",
                    "display_name": "Image Model",
                    "media_type": "image",
                    "is_default": True,
                    "is_enabled": True,
                },
                {
                    "model_id": "model-disabled",
                    "display_name": "Disabled",
                    "media_type": "image",
                    "is_default": False,
                    "is_enabled": False,
                },
            ],
        )
        await db_session.commit()

        mock_svc = _make_mock_svc()
        options = await _build_options(mock_svc, db_session)

        assert f"custom-{p1.id}/model-text" in options["text_backends"]
        assert f"custom-{p2.id}/model-image" in options["image_backends"]
        assert f"custom-{p2.id}/model-disabled" not in options["image_backends"]

    async def test_no_custom_providers_returns_empty_custom_section(self, session):
        """When no custom providers exist, only preset backends are included."""
        db_session, _factory = session

        mock_svc = _make_mock_svc(ready_providers=[])
        options = await _build_options(mock_svc, db_session)

        # No custom- entries
        for key in ("video_backends", "image_backends", "text_backends"):
            assert not any(v.startswith("custom-") for v in options[key])

    async def test_exception_in_custom_providers_is_nonfatal(self):
        """If the DB query raises, _build_options still returns preset backends."""
        mock_svc = _make_mock_svc(ready_providers=[])

        # Use a mock session that makes repo queries raise
        mock_session = MagicMock(spec=AsyncSession)
        mock_session.execute = AsyncMock(side_effect=RuntimeError("db unavailable"))

        options = await _build_options(mock_svc, mock_session)

        # Should still return valid dict with empty lists (no ready preset providers)
        assert "video_backends" in options
        assert "image_backends" in options
        assert "text_backends" in options

    async def test_preset_providers_still_included_alongside_custom(self, session):
        """Preset ready providers + custom models both appear in options."""
        db_session, factory = session
        repo = CustomProviderRepository(db_session)
        provider = await repo.create_provider(
            display_name="Custom Text",
            api_format="openai",
            base_url="https://api.example.com/v1",
            api_key="sk-test",
            models=[
                {
                    "model_id": "my-text-model",
                    "display_name": "My Text Model",
                    "media_type": "text",
                    "is_default": True,
                    "is_enabled": True,
                }
            ],
        )
        await db_session.commit()

        # gemini-aistudio as a ready preset provider
        mock_svc = _make_mock_svc(ready_providers=["gemini-aistudio"])
        options = await _build_options(mock_svc, db_session)

        # Preset models present
        assert any("gemini-aistudio/" in v for v in options["video_backends"])
        # Custom model also present
        assert f"custom-{provider.id}/my-text-model" in options["text_backends"]


# ---------------------------------------------------------------------------
# Tests: _build_options returns provider_names
# ---------------------------------------------------------------------------


class TestBuildOptionsProviderNames:
    async def test_returns_provider_names_for_custom_providers(self, session):
        """provider_names should include the ID→display_name mapping for custom providers."""
        db_session, factory = session
        repo = CustomProviderRepository(db_session)
        provider = await repo.create_provider(
            display_name="My LLM Service",
            api_format="openai",
            base_url="https://api.example.com/v1",
            api_key="sk-test",
            models=[
                {
                    "model_id": "gpt-4o",
                    "display_name": "GPT-4o",
                    "media_type": "text",
                    "is_default": True,
                    "is_enabled": True,
                }
            ],
        )
        await db_session.commit()

        mock_svc = _make_mock_svc()
        options = await _build_options(mock_svc, db_session)

        assert "provider_names" in options
        assert options["provider_names"][f"custom-{provider.id}"] == "My LLM Service"

    async def test_multiple_providers_all_have_names(self, session):
        db_session, factory = session
        repo = CustomProviderRepository(db_session)
        p1 = await repo.create_provider(
            display_name="Provider A",
            api_format="openai",
            base_url="https://a.example.com/v1",
            api_key="sk-a",
            models=[
                {
                    "model_id": "model-a",
                    "display_name": "Model A",
                    "media_type": "text",
                    "is_default": True,
                    "is_enabled": True,
                }
            ],
        )
        p2 = await repo.create_provider(
            display_name="Provider B",
            api_format="google",
            base_url="https://b.example.com",
            api_key="sk-b",
            models=[
                {
                    "model_id": "model-b",
                    "display_name": "Model B",
                    "media_type": "image",
                    "is_default": True,
                    "is_enabled": True,
                }
            ],
        )
        await db_session.commit()

        mock_svc = _make_mock_svc()
        options = await _build_options(mock_svc, db_session)

        assert options["provider_names"][f"custom-{p1.id}"] == "Provider A"
        assert options["provider_names"][f"custom-{p2.id}"] == "Provider B"

    async def test_empty_provider_names_when_no_custom_providers(self, session):
        db_session, _factory = session
        mock_svc = _make_mock_svc()
        options = await _build_options(mock_svc, db_session)

        assert options["provider_names"] == {}

    async def test_disabled_models_provider_not_in_names(self, session):
        """If all models of a provider are disabled, it should not appear in provider_names."""
        db_session, factory = session
        repo = CustomProviderRepository(db_session)
        await repo.create_provider(
            display_name="All Disabled",
            api_format="openai",
            base_url="https://api.example.com/v1",
            api_key="sk-test",
            models=[
                {
                    "model_id": "disabled-model",
                    "display_name": "Disabled",
                    "media_type": "text",
                    "is_default": False,
                    "is_enabled": False,
                }
            ],
        )
        await db_session.commit()

        mock_svc = _make_mock_svc()
        options = await _build_options(mock_svc, db_session)

        assert options["provider_names"] == {}
