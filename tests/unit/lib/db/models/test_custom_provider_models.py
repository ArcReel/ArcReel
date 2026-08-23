"""Tests for CustomProvider and CustomProviderModel ORM models."""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, select

import lib.db.models  # noqa: F401 — ensure all models registered
from lib.db.models import CustomProvider, CustomProviderModel


class TestCustomProviderTable:
    async def test_table_exists(self, db_engine):
        async with db_engine.connect() as conn:
            table_names = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
        assert "custom_provider" in table_names

    async def test_table_columns(self, db_engine):
        async with db_engine.connect() as conn:
            columns = await conn.run_sync(
                lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("custom_provider")}
            )
        expected = {
            "id",
            "display_name",
            "discovery_format",
            "base_url",
            "api_key",
            "image_max_workers",
            "video_max_workers",
            "audio_max_workers",
            "created_at",
            "updated_at",
        }
        assert columns == expected


class TestCustomProviderRoundTrip:
    async def test_create_and_read_back(self, db_session):
        provider = CustomProvider(
            display_name="My Ollama",
            discovery_format="openai",
            base_url="http://localhost:11434/v1",
            api_key="sk-local-test",
        )
        db_session.add(provider)
        await db_session.commit()

        result = await db_session.execute(select(CustomProvider).where(CustomProvider.display_name == "My Ollama"))
        loaded = result.scalar_one()
        assert loaded.display_name == "My Ollama"
        assert loaded.discovery_format == "openai"
        assert loaded.base_url == "http://localhost:11434/v1"
        assert loaded.api_key == "sk-local-test"
        assert loaded.id is not None
        assert loaded.created_at is not None
        assert loaded.updated_at is not None

    async def test_provider_id_property(self, db_session):
        provider = CustomProvider(
            display_name="Test Provider",
            discovery_format="openai",
            base_url="http://example.com/v1",
            api_key="sk-test",
        )
        db_session.add(provider)
        await db_session.commit()

        result = await db_session.execute(select(CustomProvider).where(CustomProvider.display_name == "Test Provider"))
        loaded = result.scalar_one()
        assert loaded.provider_id == f"custom-{loaded.id}"


class TestCustomProviderModelTable:
    async def test_table_exists(self, db_engine):
        async with db_engine.connect() as conn:
            table_names = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
        assert "custom_provider_model" in table_names

    async def test_table_columns(self, db_engine):
        async with db_engine.connect() as conn:
            columns = await conn.run_sync(
                lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("custom_provider_model")}
            )
        expected = {
            "id",
            "provider_id",
            "model_id",
            "display_name",
            "endpoint",
            "is_default",
            "is_enabled",
            "price_unit",
            "price_input",
            "price_output",
            "currency",
            "supported_durations",
            "resolution",
            "capability_overrides",
            "created_at",
            "updated_at",
        }
        assert columns == expected


class TestCustomProviderModelRoundTrip:
    async def test_create_linked_model(self, db_session):
        """Create a CustomProviderModel linked to a provider and read back."""
        provider = CustomProvider(
            display_name="OpenRouter",
            discovery_format="openai",
            base_url="https://openrouter.ai/api/v1",
            api_key="sk-or-xxx",
        )
        db_session.add(provider)
        await db_session.commit()

        model = CustomProviderModel(
            provider_id=provider.id,
            model_id="anthropic/claude-sonnet-4",
            display_name="Claude Sonnet",
            endpoint="openai-chat",
            is_default=True,
            is_enabled=True,
            price_unit="token",
            price_input=3.0,
            price_output=15.0,
            currency="USD",
        )
        db_session.add(model)
        await db_session.commit()

        result = await db_session.execute(
            select(CustomProviderModel).where(CustomProviderModel.provider_id == provider.id)
        )
        loaded = result.scalar_one()
        assert loaded.model_id == "anthropic/claude-sonnet-4"
        assert loaded.display_name == "Claude Sonnet"
        assert loaded.endpoint == "openai-chat"
        assert loaded.is_default is True
        assert loaded.is_enabled is True
        assert loaded.price_unit == "token"
        assert loaded.price_input == 3.0
        assert loaded.price_output == 15.0
        assert loaded.currency == "USD"
        assert loaded.created_at is not None
        assert loaded.updated_at is not None

    async def test_price_fields_nullable(self, db_session):
        """Price fields should be nullable for local/free providers (e.g., Ollama)."""
        provider = CustomProvider(
            display_name="Local Ollama",
            discovery_format="openai",
            base_url="http://localhost:11434/v1",
            api_key="ollama",
        )
        db_session.add(provider)
        await db_session.commit()

        model = CustomProviderModel(
            provider_id=provider.id,
            model_id="llama3",
            display_name="Llama 3",
            endpoint="openai-chat",
        )
        db_session.add(model)
        await db_session.commit()

        result = await db_session.execute(select(CustomProviderModel).where(CustomProviderModel.model_id == "llama3"))
        loaded = result.scalar_one()
        assert loaded.price_unit is None
        assert loaded.price_input is None
        assert loaded.price_output is None
        assert loaded.currency is None
        assert loaded.is_default is False
        assert loaded.is_enabled is True

    async def test_unique_constraint_provider_model(self, db_session):
        """UniqueConstraint on (provider_id, model_id) should prevent duplicates."""
        provider = CustomProvider(
            display_name="Dup Test",
            discovery_format="openai",
            base_url="http://example.com/v1",
            api_key="sk-test",
        )
        db_session.add(provider)
        await db_session.commit()

        model1 = CustomProviderModel(
            provider_id=provider.id,
            model_id="gpt-4o",
            display_name="GPT-4o",
            endpoint="openai-chat",
        )
        db_session.add(model1)
        await db_session.commit()

        model2 = CustomProviderModel(
            provider_id=provider.id,
            model_id="gpt-4o",
            display_name="GPT-4o Dup",
            endpoint="openai-chat",
        )
        db_session.add(model2)
        with pytest.raises(Exception):  # IntegrityError
            await db_session.commit()
