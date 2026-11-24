"""出厂快照：随代码打包、过校验器，且与注册表登记的供应商一级代码事实对得上。

出厂快照由发版 PR 刷新成目录数据仓库的最新数据，这里只核对与具体模型和取值无关的约定：快照能通过校验、
能被目录查询完整读出，快照里的内置供应商就是注册表登记的那些，供应商在某个媒体类型下有模型时，代码里登记了
这一媒体类型的调用通道。另用已发布模型验证装配、能力和计量的实际调用路径。
"""

import base64
from datetime import datetime
from pathlib import Path

import pytest

from arcreel_catalog_core import CATALOG_FILENAME, MANIFEST_FILENAME, check_catalog_files, dump_entry, parse_entry
from arcreel_market_core.video_backend_contract import VideoGenerationRequest
from lib.backends.backend_assembly import assemble_backend
from lib.backends.backend_assembly.specs import builtin_video_metered_usage, get_provider_spec
from lib.backends.image_capabilities import backend_reference_image_limit
from lib.backends.text_backends.base import TextCapability, TextTaskType
from lib.catalog import query as catalog
from lib.catalog.query import SNAPSHOT_DIR, factory_snapshot
from lib.config.registry import PROVIDER_REGISTRY
from lib.config.resolver import ConfigResolver
from lib.config.service import ConfigService
from lib.generation.video_request_facts import (
    ExecutionVideoIdentity,
    VideoRequestFacts,
    end_frame_duration_failure,
    evaluate_video_request_facts,
)
from tests.catalog_support import set_catalog_today
from tests.factories import image_bytes
from tests.fakes import MP4_BYTES, captured_vertex_credentials
from tests.http_capture import capture_http, request_json


@pytest.fixture(autouse=True)
def read_factory_snapshot(monkeypatch):
    """根 conftest 让目录查询读测试目录数据；本文件换回出厂快照。模块导入早于任何替换，``factory_snapshot`` 是原函数。"""
    monkeypatch.setattr(catalog, "factory_snapshot", factory_snapshot)
    set_catalog_today(monkeypatch, datetime.fromisoformat(factory_snapshot().dataset_version).date())


def test_snapshot_passes_build_side_check():
    manifest = (SNAPSHOT_DIR / MANIFEST_FILENAME).read_bytes()
    data = (SNAPSHOT_DIR / CATALOG_FILENAME).read_bytes()

    assert check_catalog_files(manifest, data) == []
    assert catalog.factory_snapshot().dataset_version


def test_snapshot_lists_exactly_the_registry_providers():
    assert set(catalog.factory_snapshot().builtin_providers) == set(PROVIDER_REGISTRY)


@pytest.mark.parametrize("provider_id", list(PROVIDER_REGISTRY))
def test_every_listed_model_resolves_to_an_entry(provider_id: str):
    listing = catalog.factory_snapshot().builtin_providers[provider_id]
    assert len(catalog.list_models(provider_id)) == len(listing.models)


@pytest.mark.parametrize("provider_id", list(PROVIDER_REGISTRY))
def test_every_listed_media_type_has_a_call_channel(provider_id: str):
    for media_type in catalog.provider_media_types(provider_id):
        assert get_provider_spec(provider_id, media_type).provider_id == provider_id


def test_every_listed_model_with_a_price_table_is_priceable():
    data = catalog.factory_snapshot()
    unpriceable = [
        f"{provider_id}/{model.api_model_name}"
        for provider_id, listing in data.builtin_providers.items()
        for model in listing.models
        if (entry := data.entry_of(model)) is not None and entry.price is not None and not entry.price.is_priceable
    ]
    assert unpriceable == []


def test_every_entry_survives_a_user_copy_round_trip():
    for entry in catalog.factory_snapshot().entries:
        assert parse_entry(dump_entry(entry)) == entry, entry.key


def test_reset_for_tests_reloads_snapshot_and_index():
    before = catalog.factory_snapshot()
    provider_id = next(pid for pid in PROVIDER_REGISTRY if catalog.list_models(pid))
    models = catalog.list_models(provider_id)

    catalog.reset_for_tests()

    assert catalog.factory_snapshot() is not before
    assert catalog.list_models(provider_id) == models


async def test_vertex_recommendation_uses_interactions_with_frame_limits_and_token_metering(db_factory, tmp_path: Path):
    provider = "gemini-vertex"
    model = catalog.default_model(provider, "video")
    assert model == "gemini-omni-1.2-flash"
    credentials = tmp_path / "vertex.json"
    credentials.write_text('{"project_id": "factory-project"}', encoding="utf-8")
    start, end = tmp_path / "start.png", tmp_path / "end.png"
    start.write_bytes(image_bytes(1))
    end.write_bytes(image_bytes(2))
    async with db_factory() as session:
        await ConfigService(session).set_provider_config(provider, "credentials_path", str(credentials))
        await session.commit()

    resolver = ConfigResolver(db_factory)
    with captured_vertex_credentials() as load:
        load.return_value.token = "test-token"
        load.return_value.valid = True
        load.return_value.quota_project_id = None
        backend = await assemble_backend(provider_id=provider, media_type="video", model_id=model, resolver=resolver)

    facts = await evaluate_video_request_facts(
        {},
        route="storyboard",
        generation_type="i2v",
        identity=ExecutionVideoIdentity(provider, backend.model),
        resolver=resolver,
        uses_end_frame=True,
    )
    assert isinstance(facts, VideoRequestFacts)
    assert facts.supported_durations == tuple(range(3, 16))
    assert facts.allowed_durations == tuple(range(3, 11))
    failure = end_frame_duration_failure(facts, 11)
    assert failure is not None
    assert failure.code == "video_duration_end_frame_not_supported"

    request = VideoGenerationRequest(
        prompt="p", output_path=tmp_path / "out.mp4", duration_seconds=10, start_image=start, end_image=end
    )
    with capture_http() as transport:
        submit = transport.post(
            "https://aiplatform.googleapis.com/v1beta1/projects/factory-project/locations/global/interactions"
        ).respond(
            200,
            json={
                "id": "int-1",
                "status": "completed",
                "steps": [
                    {
                        "type": "model_output",
                        "content": [
                            {"type": "video", "mime_type": "video/mp4", "data": base64.b64encode(MP4_BYTES).decode()}
                        ],
                    }
                ],
                "usage": {
                    "total_output_tokens": 45000,
                    "output_tokens_by_modality": [{"modality": "video", "tokens": 42000}],
                },
            },
        )
        result = await backend.generate(request)

    body = request_json(submit.calls.last.request)
    assert body["model"] == model
    assert body["generation_config"]["video_config"]["task"] == "image_to_video"
    assert body["response_format"]["duration"] == "10s"
    assert [part["type"] for part in body["input"][0]["content"]] == ["image", "image", "text"]
    assert request.output_path.read_bytes() == MP4_BYTES
    assert builtin_video_metered_usage(provider, model, request).quantities["video_tokens"] == 57920
    assert builtin_video_metered_usage(provider, model, request, result).quantities["video_tokens"] == 42000


async def test_agent_plan_recommended_image_and_speech_models_assemble(db_factory):
    provider = "ark-agent-plan"
    async with db_factory() as session:
        await ConfigService(session).set_provider_config(provider, "api_key", "test-key")
        await session.commit()

    resolver = ConfigResolver(db_factory)
    for media_type, expected in (("image", "doubao-seedream-5.0-flash"), ("audio", "seed-tts-2.0")):
        backend = await assemble_backend(
            provider_id=provider,
            media_type=media_type,
            model_id=catalog.default_model(provider, media_type),
            resolver=resolver,
        )
        assert backend.model == expected


async def test_dashscope_recommendations_expose_vision_and_ten_reference_images(db_factory):
    provider = "dashscope"
    text_model = catalog.get_model(provider, "qwen3.8-flash", media_type="text")
    assert catalog.default_model(provider, "text") == "qwen3.8-flash"
    assert text_model is not None
    assert text_model.max_output_tokens == 131072

    async with db_factory() as session:
        config = ConfigService(session)
        await config.set_provider_config(provider, "api_key", "test-key")
        await config.set_setting("default_text_backend", f"{provider}/qwen3.8-flash")
        await session.commit()

    resolver = ConfigResolver(db_factory)
    text_provider, text_model_id = await resolver.text_backend_for_task(TextTaskType.STYLE_ANALYSIS)
    assert (text_provider, text_model_id) == (provider, "qwen3.8-flash")
    text_backend = await assemble_backend(
        provider_id=text_provider, media_type="text", model_id=text_model_id, resolver=resolver
    )
    assert TextCapability.VISION in text_backend.capabilities

    backend = await assemble_backend(
        provider_id=provider,
        media_type="image",
        model_id=catalog.default_model(provider, "image"),
        resolver=resolver,
    )
    assert backend.model == "qwen-image-2.1-pro"
    assert (await backend_reference_image_limit(backend, provider_id=provider)).maximum == 10
