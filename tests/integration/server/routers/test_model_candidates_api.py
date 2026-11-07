"""Task-bucket candidate-filter API GET /system/config/model-candidates.

Assert external membership only for ready built-in/custom providers.
Bucket truth (registry image declarations/backend video capabilities/endpoint decisions ⊕ overrides)
has separate lib.backends.generation_type_buckets coverage.

dashscope supplies built-in i2v-only/t2v-only/r2v video samples and
t2i+i2i images. Vidu viduq1 is the only still-available factory i2i-only image sample, so image tests also mark vidu ready.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from arcreel_catalog_core import CatalogData
from lib.backends.generation_type_buckets import builtin_model_buckets, custom_model_buckets
from lib.catalog import query as catalog
from lib.catalog.builtin_models import disabled_builtin_models, disabled_without_state, list_builtin_models
from lib.catalog.errors import ModelDiscontinuedError
from lib.catalog.query import factory_snapshot
from lib.config.registry import PROVIDER_REGISTRY
from lib.config.service import ConfigService, ProviderStatus
from lib.db import get_async_session
from lib.db.base import Base
from lib.db.repositories.builtin_provider_model_repo import BuiltinProviderModelRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.generation.task_failure import failure_message_key
from lib.i18n import SUPPORTED_LOCALES, render_message
from lib.i18n import _ as translate_message
from server.auth import CurrentUserInfo, get_current_user
from server.dependencies import get_config_service
from server.routers import system_config as system_config_router
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.catalog_support import (
    install_catalog,
    set_catalog_today,
    with_entry_changes,
    with_entry_facts,
    with_min_app_version,
)
from tests.factories import make_display_names
from tests.fakes import use_project_manager

CANDIDATES_URL = "/api/v1/system/config/model-candidates"


def _custom_option(body: dict, model_id: str) -> str:
    """Extract custom-provider "provider/model" from responses.

    provider_names also includes ready built-ins; select custom- prefixes, never first keys.
    """
    return next(pid for pid in body["provider_names"] if pid.startswith("custom-")) + f"/{model_id}"


# dashscope video models: capabilities come from backend declarations, see module docs.
DS_I2V_ONLY = "dashscope/wan2.7-i2v"  # i2v ✓ / r2v ✗
DS_T2V_ONLY = "dashscope/wan2.7-t2v"  # i2v ✗ / r2v ✗
DS_R2V = "dashscope/wan2.7-r2v"  # i2v ✓ / r2v ✓
# Images: dashscope t2i+i2i and vidu i2i-only samples.
DS_IMAGE_BOTH = "dashscope/qwen-image-2.0"
DS_IMAGE_I2I_ONLY = "vidu/viduq1"
# Bucketless media samples: audio has no buckets.
DS_AUDIO = "dashscope/qwen3-tts-flash"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_mock_svc(
    ready_providers: list[str], model_states=None, settings: dict[str, str] | None = None
) -> ConfigService:
    """Supplied ``model_states`` session factories calculate disabled sets from built-in model tables, including manually enabled rows.

    Global ``settings`` model references supplement hints for models outside current lists.
    """
    ready = set(ready_providers)
    svc = MagicMock(spec=ConfigService)
    svc.get_all_settings = AsyncMock(return_value=dict(settings or {}))

    async def _get_all_providers_status() -> list[ProviderStatus]:
        disabled: dict[str, frozenset[str]] = {}
        if model_states is not None:
            async with model_states() as session:
                disabled = await disabled_builtin_models(session)
        return [
            ProviderStatus(
                name=name,
                display_name=meta.display_name,
                status="ready" if name in ready else "unconfigured",
                media_types=catalog.provider_media_types(name),
                capabilities=catalog.provider_capabilities(name),
                required_keys=list(meta.required_keys),
                credential_count=1 if name in ready else 0,
                # Instances without built-in provider state rows disable discontinued models.
                disabled_models=disabled[name] if model_states is not None else disabled_without_state(name),
            )
            for name, meta in PROVIDER_REGISTRY.items()
        ]

    svc.get_all_providers_status = AsyncMock(side_effect=_get_all_providers_status)
    return svc


@pytest.fixture
def session_factory_and_engine(db_engine, db_factory):
    return db_factory, db_engine


def _make_app(mock_svc: ConfigService, engine, factory) -> FastAPI:
    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield
        await engine.dispose()

    app = FastAPI(lifespan=_lifespan)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.dependency_overrides[get_config_service] = lambda: mock_svc

    async def _override_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_async_session] = _override_session
    app.include_router(system_config_router.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return app


@pytest.fixture
def make_client(session_factory_and_engine):
    factory, engine = session_factory_and_engine

    def _make(
        ready_providers: list[str] | None = None,
        *,
        with_model_states: bool = False,
        settings: dict[str, str] | None = None,
    ) -> TestClient:
        svc = _make_mock_svc(ready_providers or [], factory if with_model_states else None, settings)
        return TestClient(_make_app(svc, engine, factory))

    return _make


async def _seed_custom_models(factory, models: list[dict]) -> None:
    async with factory() as session:
        repo = CustomProviderRepository(session)
        await repo.create_provider(
            display_name="Relay",
            discovery_format="openai",
            base_url="https://relay.test/v1",
            api_key="sk-relay",
            models=models,
        )
        await session.commit()


# ---------------------------------------------------------------------------
# Built-in model bucket filters
# ---------------------------------------------------------------------------


class TestBuiltinBucketFiltering:
    def test_r2v_only_lists_models_with_reference_image_slots(self, make_client):
        with make_client(["dashscope"]) as client:
            body = client.get(CANDIDATES_URL).json()
        r2v = body["video"]["buckets"]["r2v"]
        assert DS_R2V in r2v
        assert DS_I2V_ONLY not in r2v
        assert DS_T2V_ONLY not in r2v

    def test_i2v_excludes_text_only_video_models(self, make_client):
        with make_client(["dashscope"]) as client:
            body = client.get(CANDIDATES_URL).json()
        i2v = body["video"]["buckets"]["i2v"]
        assert DS_I2V_ONLY in i2v
        assert DS_R2V in i2v
        assert DS_T2V_ONLY not in i2v

    def test_image_buckets_split_by_declared_capabilities(self, make_client):
        with make_client(["dashscope", "vidu"]) as client:
            body = client.get(CANDIDATES_URL).json()
        assert DS_IMAGE_BOTH in body["image"]["buckets"]["t2i"]
        assert DS_IMAGE_BOTH in body["image"]["buckets"]["i2i"]
        assert DS_IMAGE_I2I_ONLY in body["image"]["buckets"]["i2i"]
        assert DS_IMAGE_I2I_ONLY not in body["image"]["buckets"]["t2i"]

    def test_default_tier_is_unfiltered(self, make_client):
        with make_client(["dashscope", "vidu"]) as client:
            body = client.get(CANDIDATES_URL).json()
        # Defaults promise no capabilities; models lacking i2v/r2v remain listed.
        assert DS_T2V_ONLY in body["video"]["default"]
        assert DS_IMAGE_I2I_ONLY in body["image"]["default"]

    def test_unconfigured_providers_absent(self, make_client):
        with make_client([]) as client:
            body = client.get(CANDIDATES_URL).json()
        assert body["video"]["default"] == []
        assert body["image"]["default"] == []
        assert body["video"]["buckets"]["r2v"] == []

    def test_media_types_do_not_leak_into_each_other(self, make_client):
        with make_client(["dashscope"]) as client:
            body = client.get(CANDIDATES_URL).json()
        assert DS_R2V not in body["image"]["default"]
        assert DS_IMAGE_BOTH not in body["video"]["default"]

    def test_hidden_models_absent_from_every_list(self, make_client, monkeypatch):
        hidden_id = DS_R2V.split("/", 1)[1]
        install_catalog(monkeypatch, with_entry_changes(factory_snapshot(), "dashscope", hidden_id, discontinued=True))
        with make_client(["dashscope"]) as client:
            body = client.get(CANDIDATES_URL).json()
        assert DS_R2V not in body["video"]["default"]
        assert DS_R2V not in body["video"]["buckets"]["r2v"]
        assert DS_R2V not in body["video"]["buckets"]["i2v"]

    def test_version_gated_models_absent_from_every_list(self, make_client, monkeypatch):
        """Models requiring newer app versions enter neither defaults nor any task buckets."""
        gated_id = DS_R2V.split("/", 1)[1]
        install_catalog(monkeypatch, with_min_app_version(factory_snapshot(), "dashscope", gated_id, "999.0.0"))
        with make_client(["dashscope"]) as client:
            body = client.get(CANDIDATES_URL).json()
        assert DS_I2V_ONLY in body["video"]["default"]
        assert DS_R2V not in body["video"]["default"]
        assert DS_R2V not in body["video"]["buckets"]["r2v"]
        assert DS_R2V not in body["video"]["buckets"]["i2v"]
        assert all(item["option"] != DS_R2V for items in body["video"]["filtered"].values() for item in items)

    async def test_manually_enabled_discontinued_model_stays_out_of_candidates(
        self, make_client, session_factory_and_engine, monkeypatch
    ):
        """Discontinued models enter no candidates even if manually enabled; provider lists still show enabled/discontinued states."""
        factory, _engine = session_factory_and_engine
        model_id = DS_R2V.split("/", 1)[1]
        install_catalog(
            monkeypatch,
            with_entry_changes(factory_snapshot(), "dashscope", model_id, shutdown_date=date(2026, 11, 24)),
        )
        set_catalog_today(monkeypatch, date(2026, 11, 23))
        with make_client(["dashscope"], with_model_states=True) as client:
            async with factory() as session:
                await BuiltinProviderModelRepository(session).set_enabled("dashscope", model_id, True)
                await session.commit()
            before = client.get(CANDIDATES_URL).json()
            set_catalog_today(monkeypatch, date(2026, 11, 24))
            after = client.get(CANDIDATES_URL).json()
            async with factory() as session:
                listed = {item.view.model_id: item for item in await list_builtin_models(session, "dashscope")}

        assert DS_R2V in before["video"]["default"]
        assert DS_R2V in before["video"]["buckets"]["r2v"]
        assert DS_R2V not in after["video"]["default"]
        assert DS_R2V not in after["video"]["buckets"]["r2v"]
        assert DS_R2V not in after["video"]["buckets"]["i2v"]
        assert (listed[model_id].enabled, listed[model_id].view.lifecycle.status) == (True, "discontinued")

    def test_agnes_25_candidates_are_visible_and_legacy_models_are_hidden(self, make_client):
        with make_client(["agnes"]) as client:
            body = client.get(CANDIDATES_URL).json()

        for option in ("agnes/agnes-3.0-flash", "agnes/agnes-2.5-flash", "agnes/agnes-2.5-pro"):
            assert option in body["model_names"]
        assert "agnes/agnes-image-2.5-flash" in body["image"]["default"]
        assert "agnes/agnes-video-2.5" in body["video"]["default"]
        assert "agnes/agnes-video-2.5-flash" in body["video"]["default"]

        all_candidates = {option for media in ("image", "video") for option in body[media]["default"]}
        all_candidates.update(body["model_names"])
        # Factory-discontinued agnes legacy models; agnes-image-2.1-flash remains catalog-available, disabled only by old-user migration.
        assert "agnes/agnes-2.0-flash" not in all_candidates
        assert "agnes/agnes-video-v2.0" not in all_candidates


# ---------------------------------------------------------------------------
# Custom-provider model bucket filters
# ---------------------------------------------------------------------------


class TestCustomProviderBucketFiltering:
    async def test_custom_video_model_enters_buckets_by_endpoint_judgement(
        self, make_client, session_factory_and_engine
    ):
        factory, _engine = session_factory_and_engine
        with make_client([]) as client:
            # openai-video system max_reference_images=1/first_frame=True
            await _seed_custom_models(
                factory,
                [{"model_id": "relay-sora", "display_name": "Sora 2", "endpoint": "openai-video", "is_enabled": True}],
            )
            body = client.get(CANDIDATES_URL).json()
        option = _custom_option(body, "relay-sora")
        assert option in body["video"]["buckets"]["r2v"]
        assert option in body["video"]["buckets"]["i2v"]
        assert option in body["video"]["default"]

    async def test_capability_override_removes_model_from_r2v(self, make_client, session_factory_and_engine):
        factory, _engine = session_factory_and_engine
        with make_client([]) as client:
            await _seed_custom_models(
                factory,
                [
                    {
                        "model_id": "relay-sora",
                        "display_name": "Sora 2",
                        "endpoint": "openai-video",
                        "is_enabled": True,
                        "capability_overrides": {"max_reference_images": 0},
                    }
                ],
            )
            body = client.get(CANDIDATES_URL).json()
        option = _custom_option(body, "relay-sora")
        assert option not in body["video"]["buckets"]["r2v"]
        # Reference-only overrides leave i2v/default tiers unaffected.
        assert option in body["video"]["buckets"]["i2v"]
        assert option in body["video"]["default"]

    async def test_custom_image_model_filtered_by_endpoint_capabilities(self, make_client, session_factory_and_engine):
        factory, _engine = session_factory_and_engine
        with make_client([]) as client:
            # openai-images-edits declares image_to_image only.
            await _seed_custom_models(
                factory,
                [
                    {
                        "model_id": "gpt-image-1",
                        "display_name": "GPT Image",
                        "endpoint": "openai-images-edits",
                        "is_enabled": True,
                    }
                ],
            )
            body = client.get(CANDIDATES_URL).json()
        option = _custom_option(body, "gpt-image-1")
        assert option in body["image"]["buckets"]["i2i"]
        assert option not in body["image"]["buckets"]["t2i"]
        assert option in body["image"]["default"]

    @pytest.mark.parametrize(
        ("endpoint", "model_id", "buckets"),
        [
            # Endpoints implement both, but entries accept image-to-image only.
            ("dashscope-qwen-image", "viduq1", {"i2i"}),
            # Text-to-image-only endpoints/image-to-image-only entries have no mutually satisfied combination.
            ("openai-images-generations", "viduq1", set()),
            # Unmatched entries impose no model-fact constraints; follow endpoint declarations.
            ("dashscope-qwen-image", "relay-image-x", {"t2i", "i2i"}),
        ],
    )
    async def test_custom_image_buckets_need_both_entry_and_endpoint(
        self, make_client, session_factory_and_engine, endpoint, model_id, buckets
    ):
        factory, _engine = session_factory_and_engine
        with make_client([]) as client:
            await _seed_custom_models(
                factory,
                [{"model_id": model_id, "display_name": model_id, "endpoint": endpoint, "is_enabled": True}],
            )
            body = client.get(CANDIDATES_URL).json()
        option = _custom_option(body, model_id)
        assert {bucket for bucket in ("t2i", "i2i") if option in body["image"]["buckets"][bucket]} == buckets
        assert option in body["image"]["default"]

    async def test_custom_model_leaves_every_candidate_list_once_its_entry_is_discontinued(
        self, make_client, session_factory_and_engine, monkeypatch
    ):
        """Discontinued bound custom entries leave defaults/all task buckets, without affecting same-provider peers."""
        factory, _engine = session_factory_and_engine
        model_id = DS_R2V.split("/", 1)[1]
        install_catalog(
            monkeypatch,
            with_entry_changes(factory_snapshot(), "dashscope", model_id, shutdown_date=date(2026, 11, 24)),
        )
        entry = catalog.provider_model_entry("dashscope", model_id, "video")
        assert entry is not None
        set_catalog_today(monkeypatch, date(2026, 11, 23))
        with make_client([]) as client:
            await _seed_custom_models(
                factory,
                [
                    {
                        "model_id": "relay-wan",
                        "display_name": "Relay Wan",
                        "endpoint": "openai-video",
                        "is_enabled": True,
                        "is_default": True,
                        "entry_binding": entry.key.ref,
                    },
                    {
                        "model_id": "relay-video",
                        "display_name": "Relay",
                        "endpoint": "openai-video",
                        "is_enabled": True,
                    },
                ],
            )
            before = client.get(CANDIDATES_URL).json()
            set_catalog_today(monkeypatch, date(2026, 11, 24))
            after = client.get(CANDIDATES_URL).json()
        retired, live = _custom_option(before, "relay-wan"), _custom_option(before, "relay-video")
        assert retired in before["video"]["default"]
        assert retired in before["video"]["buckets"]["i2v"]
        assert retired not in after["video"]["default"]
        assert all(retired not in options for options in after["video"]["buckets"].values())
        assert live in after["video"]["default"]
        assert live in after["video"]["buckets"]["i2v"]

    async def test_disabled_custom_models_absent(self, make_client, session_factory_and_engine):
        factory, _engine = session_factory_and_engine
        with make_client([]) as client:
            await _seed_custom_models(
                factory,
                [{"model_id": "relay-sora", "display_name": "Sora 2", "endpoint": "openai-video", "is_enabled": False}],
            )
            body = client.get(CANDIDATES_URL).json()
        assert body["video"]["default"] == []


# ---------------------------------------------------------------------------
# Bucket-filtered models and reasons
# ---------------------------------------------------------------------------


def _filtered(body: dict, media_type: str, bucket: str) -> dict[str, list[str]]:
    return {item["option"]: item["unmet"] for item in body[media_type]["filtered"][bucket]}


class TestFilteredCandidates:
    async def test_each_bucket_lists_the_default_layer_models_it_filters_out_with_the_unmet_dimension(
        self, make_client, session_factory_and_engine, monkeypatch
    ):
        """Filtered models carry failing dimensions; admitted models and nondefault discontinued models are absent, never falsely labeled capability failures."""
        factory, _engine = session_factory_and_engine
        data = with_entry_facts(factory_snapshot(), "grok", "grok-imagine-image", max_reference_images=0)
        install_catalog(monkeypatch, with_entry_changes(data, "dashscope", "wan2.7-t2v", discontinued=True))
        with make_client(["dashscope", "grok", "vidu"]) as client:
            await _seed_custom_models(
                factory,
                [
                    {
                        "model_id": "relay-image",
                        "display_name": "Relay Image",
                        "endpoint": "openai-images-generations",
                        "is_enabled": True,
                    }
                ],
            )
            body = client.get(CANDIDATES_URL).json()
        relay_image = _custom_option(body, "relay-image")

        i2v, r2v = _filtered(body, "video", "i2v"), _filtered(body, "video", "r2v")
        assert r2v[DS_I2V_ONLY] == ["max_reference_images"]
        assert DS_R2V not in r2v
        assert DS_I2V_ONLY not in i2v
        assert DS_T2V_ONLY not in i2v
        assert DS_T2V_ONLY not in r2v
        t2i, i2i = _filtered(body, "image", "t2i"), _filtered(body, "image", "i2i")
        assert t2i[DS_IMAGE_I2I_ONLY] == ["text_to_image"]
        assert i2i[relay_image] == ["image_to_image"]
        assert i2i["grok/grok-imagine-image"] == ["max_reference_images"]
        assert DS_IMAGE_BOTH not in t2i
        assert DS_IMAGE_BOTH not in i2i
        # Filtered plus listed models exactly reconstruct defaults.
        for media_type, bucket in (("video", "i2v"), ("video", "r2v"), ("image", "t2i"), ("image", "i2i")):
            listed = set(body[media_type]["buckets"][bucket])
            assert listed.isdisjoint(_filtered(body, media_type, bucket))
            assert listed | set(_filtered(body, media_type, bucket)) == set(body[media_type]["default"])


# ---------------------------------------------------------------------------
# Referenced discontinuation hints: lifecycle and one-click replacements
# ---------------------------------------------------------------------------


def _entry_ref(option: str, media_type: str = "video") -> str:
    provider_id, model_id = option.split("/", 1)
    entry = catalog.provider_model_entry(provider_id, model_id, media_type)
    assert entry is not None
    return entry.key.ref


def _retire(monkeypatch, option: str, *, successor: str | None, shutdown: date = date(2026, 11, 24)) -> None:
    """Make ``option`` entries discontinue on 2026-11-24 with ``successor``, treating today as the preceding day."""
    provider_id, model_id = option.split("/", 1)
    data = with_entry_changes(factory_snapshot(), provider_id, model_id, shutdown_date=shutdown, successor=successor)
    install_catalog(monkeypatch, data)
    set_catalog_today(monkeypatch, date(2026, 11, 23))


class TestLifecycleNotices:
    """``lifecycle`` supplies inline reference hints; replacements are same-provider successor-entry models available at that reference location."""

    def test_retiring_model_offers_the_same_provider_successor_in_every_layer_it_qualifies_for(
        self, make_client, monkeypatch
    ):
        successor = _entry_ref(DS_R2V)
        _retire(monkeypatch, DS_I2V_ONLY, successor=successor)
        with make_client(["dashscope"]) as client:
            body = client.get(CANDIDATES_URL).json()
        notice = body["lifecycle"][DS_I2V_ONLY]
        assert notice["status"] == "retiring"
        assert notice["shutdown_date"] == "2026-11-24"
        assert notice["successor"] == successor
        assert notice["successor_name"] == catalog.get_model(*DS_R2V.split("/", 1)).display_name
        assert notice["switch_targets"] == {"default": DS_R2V, "i2v": DS_R2V, "r2v": DS_R2V}
        # Available models have no hints.
        assert DS_R2V not in body["lifecycle"]

    def test_discontinued_model_still_gets_a_notice_though_it_is_no_candidate(self, make_client, monkeypatch):
        _retire(monkeypatch, DS_I2V_ONLY, successor=_entry_ref(DS_R2V), shutdown=date(2026, 11, 1))
        with make_client(["dashscope"]) as client:
            body = client.get(CANDIDATES_URL).json()
        assert DS_I2V_ONLY not in body["video"]["default"]
        notice = body["lifecycle"][DS_I2V_ONLY]
        assert (notice["status"], notice["shutdown_date"]) == ("discontinued", "2026-11-01")
        assert notice["switch_targets"]["default"] == DS_R2V

    @pytest.mark.parametrize("locale", SUPPORTED_LOCALES)
    def test_calling_the_discontinued_model_names_the_successor_as_the_notice_does(
        self, make_client, monkeypatch, locale
    ):
        """Discontinued invocation errors/reference hints name successors consistently by display names, never entry references."""
        successor = _entry_ref(DS_R2V)
        _retire(monkeypatch, DS_I2V_ONLY, successor=successor, shutdown=date(2026, 11, 1))
        with make_client(["dashscope"]) as client:
            notice = client.get(CANDIDATES_URL).json()["lifecycle"][DS_I2V_ONLY]
        provider_id, model_id = DS_I2V_ONLY.split("/", 1)
        with pytest.raises(ModelDiscontinuedError) as excinfo:
            catalog.require_callable(provider_id, model_id, "video")

        exc = excinfo.value
        message = render_message(
            failure_message_key(exc.code, exc.params),
            exc.params,
            lambda key, **params: translate_message(key, locale=locale, **params),
            make_display_names(locale),
        )
        assert notice["successor_name"]
        assert notice["successor_name"] in message
        assert successor not in message

    def test_successor_that_does_not_support_the_bucket_is_offered_only_where_it_qualifies(
        self, make_client, monkeypatch
    ):
        _retire(monkeypatch, DS_I2V_ONLY, successor=_entry_ref(DS_T2V_ONLY))
        with make_client(["dashscope"]) as client:
            notice = client.get(CANDIDATES_URL).json()["lifecycle"][DS_I2V_ONLY]
        assert notice["switch_targets"] == {"default": DS_T2V_ONLY, "i2v": None, "r2v": None}

    def test_successor_on_another_provider_is_named_but_not_offered(self, make_client, monkeypatch):
        ark_video = catalog.default_model("ark", "video")
        assert ark_video is not None
        successor = _entry_ref(f"ark/{ark_video}")
        _retire(monkeypatch, DS_I2V_ONLY, successor=successor)
        with make_client(["dashscope", "ark"]) as client:
            body = client.get(CANDIDATES_URL).json()
        assert f"ark/{ark_video}" in body["video"]["default"]
        notice = body["lifecycle"][DS_I2V_ONLY]
        assert notice["successor"] == successor
        assert notice["successor_name"]
        assert notice["switch_targets"] == {"default": None, "i2v": None, "r2v": None}

    def test_discontinued_successor_is_not_offered(self, make_client, monkeypatch):
        data = with_entry_changes(
            factory_snapshot(),
            "dashscope",
            DS_I2V_ONLY.split("/", 1)[1],
            shutdown_date=date(2026, 11, 24),
            successor=_entry_ref(DS_R2V),
        )
        data = with_entry_changes(data, "dashscope", DS_R2V.split("/", 1)[1], discontinued=True)
        install_catalog(monkeypatch, data)
        set_catalog_today(monkeypatch, date(2026, 11, 23))
        with make_client(["dashscope"]) as client:
            notice = client.get(CANDIDATES_URL).json()["lifecycle"][DS_I2V_ONLY]
        assert notice["switch_targets"] == {"default": None, "i2v": None, "r2v": None}

    async def test_disabled_successor_is_not_offered(self, make_client, session_factory_and_engine, monkeypatch):
        factory, _engine = session_factory_and_engine
        _retire(monkeypatch, DS_I2V_ONLY, successor=_entry_ref(DS_R2V))
        with make_client(["dashscope"], with_model_states=True) as client:
            async with factory() as session:
                await BuiltinProviderModelRepository(session).set_enabled("dashscope", DS_R2V.split("/", 1)[1], False)
                await session.commit()
            notice = client.get(CANDIDATES_URL).json()["lifecycle"][DS_I2V_ONLY]
        assert notice["switch_targets"] == {"default": None, "i2v": None, "r2v": None}

    async def test_custom_model_switches_to_the_row_bound_to_the_successor(
        self, make_client, session_factory_and_engine, monkeypatch
    ):
        """Custom bindings match entries; successors are enabled same-provider rows matching successor entries."""
        factory, _engine = session_factory_and_engine
        successor = _entry_ref(DS_R2V)
        _retire(monkeypatch, DS_I2V_ONLY, successor=successor)
        video = {"endpoint": "openai-video", "display_name": "Relay"}
        with make_client([]) as client:
            await _seed_custom_models(
                factory,
                [
                    {**video, "model_id": "relay-old", "is_enabled": True, "entry_binding": _entry_ref(DS_I2V_ONLY)},
                    {**video, "model_id": "relay-new-off", "is_enabled": False, "entry_binding": successor},
                    {**video, "model_id": "relay-new", "is_enabled": True, "entry_binding": successor},
                ],
            )
            body = client.get(CANDIDATES_URL).json()
        old, new = _custom_option(body, "relay-old"), _custom_option(body, "relay-new")
        notice = body["lifecycle"][old]
        assert notice["status"] == "retiring"
        assert notice["switch_targets"] == {"default": new, "i2v": new, "r2v": new}

    def test_text_models_carry_notices_with_a_default_layer_only(self, make_client, monkeypatch):
        model_id = catalog.default_model("dashscope", "text")
        assert model_id is not None
        _retire(monkeypatch, f"dashscope/{model_id}", successor=None)
        with make_client(["dashscope"]) as client:
            notice = client.get(CANDIDATES_URL).json()["lifecycle"][f"dashscope/{model_id}"]
        assert notice["status"] == "retiring"
        assert (notice["successor"], notice["successor_name"]) == (None, None)
        assert notice["switch_targets"] == {"default": None}


class _Projects:
    """Implement only two reference-scan methods: project names → ``project.json``."""

    def __init__(self, projects: dict[str, dict]):
        self._projects = projects

    def list_projects(self) -> list[str]:
        return list(self._projects)

    def load_project(self, name: str) -> dict:
        return self._projects[name]


def _unlisted(data: CatalogData, option: str) -> CatalogData:
    """Remove ``option`` from provider lists but retain entries, simulating official deletion with local retention."""
    provider_id, model_id = option.split("/", 1)
    listing = data.builtin_providers[provider_id]
    models = tuple(m for m in listing.models if m.api_model_name != model_id)
    return replace(data, builtin_providers={**data.builtin_providers, provider_id: replace(listing, models=models)})


class TestUnlistedReferenceNotices:
    """Settings/projects reference unlisted models: hint through locally retained entries; unmatched discontinued entries get unavailable notices."""

    def test_retained_discontinued_entry_with_successor_gets_the_full_notice(self, make_client, monkeypatch):
        data = with_entry_changes(
            factory_snapshot(),
            "dashscope",
            DS_I2V_ONLY.split("/", 1)[1],
            shutdown_date=date(2026, 10, 22),
            successor=_entry_ref(DS_R2V),
        )
        install_catalog(monkeypatch, _unlisted(data, DS_I2V_ONLY))
        set_catalog_today(monkeypatch, date(2026, 11, 4))
        with make_client(["dashscope"], settings={"default_video_backend_i2v": DS_I2V_ONLY}) as client:
            body = client.get(CANDIDATES_URL).json()
        notice = body["lifecycle"][DS_I2V_ONLY]
        assert (notice["status"], notice["shutdown_date"]) == ("discontinued", "2026-10-22")
        assert notice["successor"] == _entry_ref(DS_R2V)
        assert notice["successor_name"]
        assert notice["switch_targets"] == {"default": DS_R2V, "i2v": DS_R2V, "r2v": DS_R2V}

    def test_retained_discontinued_entry_without_successor_is_named_discontinued(self, make_client, monkeypatch):
        data = with_entry_changes(factory_snapshot(), "dashscope", DS_I2V_ONLY.split("/", 1)[1], discontinued=True)
        install_catalog(monkeypatch, _unlisted(data, DS_I2V_ONLY))
        use_project_manager(monkeypatch, _Projects({"p1": {"title": "P1", "video_provider_i2v": DS_I2V_ONLY}}))
        with make_client(["dashscope"]) as client:
            notice = client.get(CANDIDATES_URL).json()["lifecycle"][DS_I2V_ONLY]
        assert notice == {
            "status": "discontinued",
            "shutdown_date": None,
            "successor": None,
            "successor_name": None,
            "switch_targets": {"default": None, "i2v": None, "r2v": None},
        }

    @pytest.mark.parametrize(
        ("model_name", "has_entry"),
        [
            pytest.param(lambda: "wan-retired-preview", False, id="no-entry"),
            # Name-matched available entries, such as successor aliases, still cannot execute models absent from provider lists.
            pytest.param(lambda: catalog.default_model("ark", "video"), True, id="available-entry"),
        ],
    )
    def test_reference_without_a_discontinued_entry_is_unavailable(
        self, make_client, monkeypatch, model_name, has_entry
    ):
        model_id = model_name()
        assert model_id is not None
        assert catalog.get_model("dashscope", model_id) is None
        assert (catalog.referenced_entry("dashscope", model_id, "video") is not None) is has_entry
        option = f"dashscope/{model_id}"
        assert catalog.get_model("dashscope", model_id) is None
        use_project_manager(monkeypatch, _Projects({"p1": {"video_backend": option}}))
        with make_client(["dashscope"]) as client:
            notice = client.get(CANDIDATES_URL).json()["lifecycle"][option]
        assert notice == {
            "status": "unavailable",
            "shutdown_date": None,
            "successor": None,
            "successor_name": None,
            "switch_targets": {"default": None, "i2v": None, "r2v": None},
        }

    def test_listed_and_custom_references_get_no_extra_notice(self, make_client, monkeypatch):
        use_project_manager(monkeypatch, _Projects({"p1": {"video_backend": "custom-9/relay"}}))
        with make_client(["dashscope"], settings={"default_video_backend": DS_R2V}) as client:
            lifecycle = client.get(CANDIDATES_URL).json()["lifecycle"]
        assert DS_R2V not in lifecycle
        assert "custom-9/relay" not in lifecycle


# ---------------------------------------------------------------------------
# Catalog names: translated providers, entry-supplied model names.
# ---------------------------------------------------------------------------


class TestCatalogDisplayNames:
    def test_builtin_provider_and_model_names_follow_accept_language(self, make_client):
        with make_client(["ark"]) as client:
            zh = client.get(CANDIDATES_URL, headers={"Accept-Language": "zh"}).json()
            en = client.get(CANDIDATES_URL, headers={"Accept-Language": "en"}).json()
        assert zh["provider_names"]["ark"] == "火山方舟"
        assert en["provider_names"]["ark"] == "Volcengine Ark"
        # Model names are language-independent catalog values; read actual catalog models to survive factory updates.
        model_id = catalog.default_model("ark", "text")
        assert model_id is not None
        model = catalog.get_model("ark", model_id)
        assert model is not None
        assert zh["model_names"][f"ark/{model_id}"] == model.display_name
        assert en["model_names"][f"ark/{model_id}"] == model.display_name

    def test_model_names_cover_media_types_without_buckets(self, make_client):
        """model_names never narrows image/video; text/audio dropdowns share translations."""
        with make_client(["dashscope"]) as client:
            body = client.get(CANDIDATES_URL).json()
        assert DS_AUDIO in body["model_names"]

    def test_builtin_model_name_is_the_catalog_display_name(self, make_client):
        with make_client(["openai"]) as client:
            names = client.get(CANDIDATES_URL, headers={"Accept-Language": "en"}).json()["model_names"]
        model = catalog.get_model("openai", "gpt-image-2")
        assert model is not None
        assert names["openai/gpt-image-2"] == model.display_name

    async def test_custom_model_name_is_the_user_authored_one(self, make_client, session_factory_and_engine):
        factory, _engine = session_factory_and_engine
        with make_client([]) as client:
            await _seed_custom_models(
                factory,
                [
                    {
                        "model_id": "relay-sora",
                        "display_name": "我的 Sora",
                        "endpoint": "openai-video",
                        "is_enabled": True,
                    }
                ],
            )
            body = client.get(CANDIDATES_URL, headers={"Accept-Language": "en"}).json()
        assert body["model_names"][_custom_option(body, "relay-sora")] == "我的 Sora"

    def test_unconfigured_provider_absent_from_names(self, make_client):
        with make_client(["ark"]) as client:
            body = client.get(CANDIDATES_URL).json()
        assert "openai" not in body["provider_names"]
        assert not any(k.startswith("openai/") for k in body["model_names"])


# ---------------------------------------------------------------------------
# Direct bucket-truth checks, without HTTP
# ---------------------------------------------------------------------------


class TestBucketJudgement:
    def test_builtin_audio_model_has_no_buckets(self):
        provider_id, model_id = DS_AUDIO.split("/", 1)
        model_info = catalog.get_model(provider_id, model_id)
        assert model_info is not None
        assert builtin_model_buckets(provider_id, model_id, model_info) == frozenset()

    def test_builtin_video_r2v_reads_backend_declaration(self):
        """r2v uses backend max_reference_images only, never catalog facts.

        viduq3-pro lacks Vidu /reference2video whitelisting, so backend declares
        max_reference_images=0; same-provider whitelisted viduq3 declares 7. Neither catalog
        entry has capability flags, yet backends distinguish buckets.
        """
        pro, plain = catalog.get_model("vidu", "viduq3-pro"), catalog.get_model("vidu", "viduq3")
        assert pro is not None
        assert plain is not None
        assert "r2v" not in builtin_model_buckets("vidu", "viduq3-pro", pro)
        assert "r2v" in builtin_model_buckets("vidu", "viduq3", plain)

    @pytest.mark.parametrize(
        ("provider_id", "model_id"),
        [
            ("vidu", "viduq3"),  # Only /reference2video whitelisted; first-frame submissions fail request construction.
            ("dashscope", "happyhorse-1.0-r2v"),
        ],
    )
    def test_builtin_video_i2v_reads_backend_declaration(self, provider_id: str, model_id: str):
        """i2v uses backend first_frame only, never catalog facts.

        Both backend first_frame=False declarations share request construction and accept no first frames; i2v admission
        would let users configure guaranteed failures.
        """
        model_info = catalog.get_model(provider_id, model_id)
        assert model_info is not None
        assert "i2v" not in builtin_model_buckets(provider_id, model_id, model_info)

    def test_unknown_endpoint_yields_no_buckets(self):
        assert custom_model_buckets(endpoint="nope-not-real", model_id="m") == frozenset()

    def test_text_endpoint_yields_no_buckets(self):
        assert custom_model_buckets(endpoint="openai-chat", model_id="gpt-4o") == frozenset()

    def test_chat_image_endpoint_declares_both_image_buckets(self):
        """openai-chat-image/openai-chat share APIs, but image endpoints enter both text/image-to-image buckets."""
        assert custom_model_buckets(endpoint="openai-chat-image", model_id="gemini-3-pro-image") == frozenset(
            {"t2i", "i2i"}
        )

    def test_a_comfyui_model_row_takes_its_buckets_from_the_node_bindings(self):
        """ComfyUI rows derive buckets solely from bindings: first frames i2v, references r2v."""
        from lib.custom_provider.endpoints import comfyui_endpoint_spec
        from tests.factories import comfyui_endpoint_definition

        t2v = comfyui_endpoint_definition()
        i2v = comfyui_endpoint_definition()
        i2v["bindings"]["start_image"] = [{"node": "11", "input": "image", "class_type": "LoadImage"}]
        flf = comfyui_endpoint_definition()
        flf["bindings"]["start_image"] = [{"node": "11", "input": "image", "class_type": "LoadImage"}]
        flf["bindings"]["end_image"] = [{"node": "12", "input": "image", "class_type": "LoadImage"}]
        r2v = comfyui_endpoint_definition()
        r2v["bindings"]["reference_images"] = [{"node": "11", "input": "image", "class_type": "LoadImage"}]

        def buckets(definition: dict) -> frozenset[str]:
            return custom_model_buckets(
                endpoint="ce-7", model_id="my-wan-workflow", endpoint_spec=comfyui_endpoint_spec("ce-7", definition)
            )

        # Text-only: i2v has_image=True requires first-frame slots, absent here.
        assert buckets(t2v) == frozenset()
        assert buckets(i2v) == frozenset({"i2v"})
        # Ending frames are capabilities, not buckets; first/last-frame workflows remain i2v with separate flags.
        assert buckets(flf) == frozenset({"i2v"})
        assert comfyui_endpoint_spec("ce-7", flf).end_image_capable is True
        assert buckets(r2v) == frozenset({"r2v"})

    def test_a_stored_capability_override_does_not_move_a_comfyui_row(self):
        """ComfyUI forbids overrides; synthesis discards dirty legacy values, retaining binding-only buckets."""
        from lib.custom_provider.endpoints import comfyui_endpoint_spec
        from tests.factories import comfyui_endpoint_definition

        spec = comfyui_endpoint_spec("ce-7", comfyui_endpoint_definition())

        assert (
            custom_model_buckets(
                endpoint="ce-7",
                model_id="m",
                capability_overrides={"last_frame": True, "max_reference_audio_count": 4},
                endpoint_spec=spec,
            )
            == frozenset()
        )

    def test_a_comfyui_image_row_takes_its_bucket_from_the_reference_image_binding(self):
        from lib.custom_provider.endpoints import comfyui_endpoint_spec
        from tests.factories import comfyui_endpoint_definition

        t2i = comfyui_endpoint_definition(media_type="image")
        i2i = comfyui_endpoint_definition(media_type="image")
        i2i["bindings"]["reference_images"] = [{"node": "11", "input": "image", "class_type": "LoadImage"}]

        def buckets(definition: dict) -> frozenset[str]:
            return custom_model_buckets(
                endpoint="ce-7", model_id="m", endpoint_spec=comfyui_endpoint_spec("ce-7", definition)
            )

        assert buckets(t2i) == frozenset({"t2i"})
        assert buckets(i2i) == frozenset({"i2i"})
