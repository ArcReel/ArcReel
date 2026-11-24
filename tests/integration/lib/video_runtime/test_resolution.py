import pytest

from arcreel_market_core.video_backend_contract import ResumeEndpointChangedError
from lib.config.resolver import ConfigResolver, ProviderModel, VideoBucketCapabilityError
from lib.video_runtime.assembly import assemble_video
from lib.video_runtime.resolution import VideoRuntime
from tests.factories import seed_endpoint_fixed_video_model


async def test_missing_custom_selection_only_falls_back_for_default(db_factory):
    pair = await seed_endpoint_fixed_video_model(db_factory)
    provider, model = pair.split("/")
    runtime = VideoRuntime(ConfigResolver(db_factory), capture_generation=lambda: 42)
    missing = ProviderModel(provider, "missing")
    target = await runtime.default({"video_backend": provider + "/missing"})
    assert target.generation == 42
    assert target.snapshot.model_id == model
    assert (await assemble_video(target.spec)).model == model
    with pytest.raises(ValueError, match="custom model not found"):
        await runtime.materialized(missing)
    with pytest.raises(VideoBucketCapabilityError):
        await runtime.bucket({"video_provider_r2v": pair}, "r2v")
    assert (await runtime.materialized(ProviderModel(provider, model))).spec == target.spec
    selected = await runtime.bucket({"video_provider_i2v": pair, "video_backend": "custom-999/missing"}, "i2v")
    assert selected.spec == target.spec


async def test_retired_builtin_only_resumes_on_compatible_protocol(db_factory, monkeypatch):
    from datetime import date

    from lib.catalog import query as catalog
    from lib.catalog.errors import ModelDiscontinuedError
    from tests.catalog_support import install_catalog, with_entry_changes

    identity = ProviderModel("ark", "doubao-seedance-2-0-260128")
    pair = f"{identity.provider_id}/{identity.model_id}"
    install_catalog(
        monkeypatch,
        with_entry_changes(
            catalog.factory_snapshot(), identity.provider_id, identity.model_id, shutdown_date=date(2000, 1, 1)
        ),
    )
    runtime = VideoRuntime(ConfigResolver(db_factory))
    with pytest.raises(ModelDiscontinuedError):
        await runtime.bucket({"video_provider_i2v": pair}, "i2v")
    with pytest.raises(ModelDiscontinuedError):
        await runtime.materialized(identity)
    for previous in (None, "builtin:ark-seedance", "builtin:ark-seedance-2"):
        resumed = await runtime.submitted(identity, previous, job_id="paid-task")
        assert resumed.endpoint_guard == "builtin:ark-seedance-2"
    with pytest.raises(ResumeEndpointChangedError) as error:
        await runtime.submitted(identity, "builtin:openai-video", job_id="paid-task")
    assert error.value.job_id == "paid-task"


async def test_custom_default_rejects_discontinued_even_if_disabled(db_factory, monkeypatch):
    from datetime import date

    from lib.catalog import query as catalog
    from lib.catalog.errors import ModelDiscontinuedError
    from lib.custom_provider import parse_provider_id
    from lib.db.models.custom_provider import CustomProviderModel
    from tests.catalog_support import install_catalog, with_entry_changes

    provider, default = (await seed_endpoint_fixed_video_model(db_factory)).split("/")
    retired = "doubao-seedance-2-0-260128"
    install_catalog(
        monkeypatch, with_entry_changes(catalog.factory_snapshot(), "ark", retired, shutdown_date=date(2000, 1, 1))
    )
    async with db_factory.begin() as session:
        for model in ("disabled", retired):
            session.add(
                CustomProviderModel(
                    provider_id=parse_provider_id(provider),
                    model_id=model,
                    display_name=model,
                    is_enabled=False,
                    endpoint="newapi-video",
                )
            )
    runtime = VideoRuntime(ConfigResolver(db_factory))
    assert (await runtime.default({"video_backend": f"{provider}/disabled"})).snapshot.model_id == default
    with pytest.raises(ModelDiscontinuedError):
        await runtime.default({"video_backend": f"{provider}/{retired}"})


async def test_captured_workflow_and_resolution_survive_config_change(db_factory, db_engine, monkeypatch):
    from collections.abc import Mapping
    from dataclasses import fields, is_dataclass

    from sqlalchemy import event, update

    from lib.catalog import query as catalog
    from lib.db.models.custom_endpoint import CustomEndpoint
    from lib.db.models.custom_provider import CustomProvider, CustomProviderModel

    provider, model = (await seed_endpoint_fixed_video_model(db_factory)).split("/")
    async with db_factory.begin() as session:
        await session.execute(update(CustomProviderModel).values(resolution="720p"))
        await session.execute(update(CustomProvider).values(api_key="private-test-key"))

    reads = []

    def observe(*args):
        reads.append("sql")

    def generation():
        assert not reads
        return 7

    event.listen(db_engine.sync_engine, "before_cursor_execute", observe)
    try:
        target = await VideoRuntime(ConfigResolver(db_factory), capture_generation=generation).materialized(
            ProviderModel(provider, model)
        )
    finally:
        event.remove(db_engine.sync_engine, "before_cursor_execute", observe)
    assert reads
    assert target.generation == 7
    assert target.snapshot.default_resolution == "720p"

    def check_public(value):
        assert not callable(value)
        assert not isinstance(value, (dict, list, set))
        if is_dataclass(value):
            for f in fields(value):
                check_public(getattr(value, f.name))
        elif isinstance(value, Mapping):
            for child in value.values():
                check_public(child)
        elif isinstance(value, (tuple, frozenset)):
            for child in value:
                check_public(child)

    check_public(target.snapshot)
    assert "private-test-key" not in repr(target.snapshot)
    async with db_factory.begin() as session:
        await session.execute(update(CustomEndpoint).values(definition={}))
        await session.execute(update(CustomProvider).values(base_url="", api_key=""))
        await session.execute(update(CustomProviderModel).values(resolution="1080p"))

    def unexpected(*args, **kwargs):
        pytest.fail("assembly read the database or catalog")

    monkeypatch.setattr(catalog, "find_entry", unexpected)
    monkeypatch.setattr(catalog, "provider_model_view", unexpected)
    event.listen(db_engine.sync_engine, "before_cursor_execute", unexpected)
    try:
        backend = await assemble_video(target.spec)
        assert backend.model == model
        assert backend.video_capabilities.first_frame
        assert backend.endpoint_guard == target.endpoint_guard
    finally:
        event.remove(db_engine.sync_engine, "before_cursor_execute", unexpected)


@pytest.mark.parametrize(
    ("provider", "model"),
    [("minimax", "MiniMax-H3"), ("dashscope", "happyhorse-1.0-i2v"), ("gemini-aistudio", "veo-3.1-generate-preview")],
)
async def test_builtin_assembly_only_consumes_frozen_spec(provider, model, db_factory, db_engine, monkeypatch):
    from sqlalchemy import event

    from lib.catalog import query as catalog
    from lib.config.service import ConfigService
    from lib.video_runtime import builtin

    async with db_factory.begin() as session:
        await ConfigService(session).set_provider_config(provider, "api_key", "test-key-4010")
    target = await VideoRuntime(ConfigResolver(db_factory)).submitted(
        ProviderModel(provider, model), None, job_id="paid-task"
    )
    assert target.snapshot.always_bill_audio == (provider == "gemini-aistudio")
    if target.snapshot.always_bill_audio:
        assert target.snapshot.audio("i2v", requested=False).billed

    def unexpected(*args, **kwargs):
        pytest.fail("assembly read live provider data")

    for key in ("find_entry", "provider_model_view", "catalog_data", "provider_model_entry", "get_model"):
        monkeypatch.setattr(catalog, key, unexpected)
    monkeypatch.setattr(builtin, "builtin_definitions", unexpected)
    event.listen(db_engine.sync_engine, "before_cursor_execute", unexpected)
    try:
        assert (await assemble_video(target.spec)).model == model
    finally:
        event.remove(db_engine.sync_engine, "before_cursor_execute", unexpected)


async def test_automatic_bucket_selection_skips_incompatible_custom_default(db_factory):
    from lib.config.recommended_models import FROZEN_SETTING_KEY
    from lib.config.service import ConfigService

    first = await seed_endpoint_fixed_video_model(db_factory)
    second = await seed_endpoint_fixed_video_model(db_factory, reference_images=True)
    async with db_factory.begin() as session:
        await ConfigService(session).set_setting(FROZEN_SETTING_KEY, "[]")
    runtime = VideoRuntime(ConfigResolver(db_factory))
    assert (await runtime.default({})).snapshot.provider_id == first.split("/")[0]
    assert (await runtime.bucket({}, "r2v")).snapshot.provider_id == second.split("/")[0]


async def test_unlisted_custom_model_is_looked_up_only_once(db_factory, monkeypatch):
    from sqlalchemy import update

    from lib.catalog import query as catalog
    from lib.db.models.custom_provider import CustomProviderModel

    provider, model = (await seed_endpoint_fixed_video_model(db_factory)).split("/")
    async with db_factory.begin() as session:
        await session.execute(update(CustomProviderModel).values(endpoint="dashscope-video"))
    lookups = []
    find_entry = catalog.find_entry

    def record(name, **kwargs):
        lookups.append(name)
        return find_entry(name, **kwargs)

    monkeypatch.setattr(catalog, "find_entry", record)
    target = await VideoRuntime(ConfigResolver(db_factory)).materialized(ProviderModel(provider, model))
    assert target.snapshot.model_id == model
    assert lookups == [model]
