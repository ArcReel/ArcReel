"""Domain rejections retain their parameters through real accounting and readback."""

import json

import pytest

from arcreel_market_core.validation_messages import ValidationMessage
from arcreel_market_core.video_backend_contract import VideoCapabilityError
from lib.backends.image_backends.base import ImageOutputFormatError, ImageOutputMissingError
from lib.backends.image_errors import ImageCapabilityError
from lib.backends.provider_assets import ProviderInputImageRejectedError
from lib.billing.ledger import Ledger
from lib.custom_provider.comfyui.failures import ComfyuiError
from lib.custom_provider.declarative_backend import DeclarativeRuntimeError
from lib.db.repositories.usage_repo import UsageRepository
from lib.generation.task_failure import bound_reason, parse_failure
from lib.generation.task_failure_encoding import encode_task_failure_message
from lib.infra.api_errors import BadRequestError
from lib.references.reference_compression import ReferencePayloadFloorError


@pytest.mark.parametrize(
    ("media_type", "exc", "code", "params"),
    [
        (
            "audio",
            BadRequestError("narration_tts_speed_out_of_range", minimum=0.7, maximum=1.5, speed=0.5),
            "narration_tts_speed_out_of_range",
            {"minimum": 0.7, "maximum": 1.5, "speed": 0.5},
        ),
        (
            "image",
            ImageCapabilityError(code="image_reference_images_exceeded", model="m", limit=1, count=2),
            "image_reference_images_exceeded",
            {"model": "m", "limit": 1, "count": 2},
        ),
        (
            "video",
            VideoCapabilityError(code="video_duration_not_supported", duration=7, supported="5, 10"),
            "video_duration_not_supported",
            {"duration": 7, "supported": "5, 10"},
        ),
    ],
)
async def test_domain_failure_survives_ledger_and_repository(db_factory, media_type, exc, code, params):
    ledger = Ledger(session_factory=db_factory)
    with pytest.raises(type(exc)) as caught:
        async with ledger.record(project_name="demo", call_type=media_type, model="m", provider="grok"):
            raise exc

    assert caught.value is exc
    async with db_factory() as session:
        repository = UsageRepository(session)
        page = await repository.list_records()
        assert len(page["items"]) == 1
        record = await repository.get_record(page["items"][0]["id"])
    assert record is not None
    assert record["status"] == "failed"
    assert record["error_message"] == code
    assert record["error_code"] == code
    assert record["error_params"] == params


@pytest.mark.parametrize(
    "exc",
    [
        ReferencePayloadFloorError(),
        ProviderInputImageRejectedError(
            provider_code="image_rejected", provider_reason=None, reference_index=None, sent_as_asset=False
        ),
        DeclarativeRuntimeError(
            "declarative_template_render_failed",
            detail=ValidationMessage("val_ce_undeclared_variable", {"name": "x" * 6000}),
        ),
        ImageOutputMissingError(model="m"),
        ImageOutputFormatError(model="m", media_type="image/svg+xml"),
        DeclarativeRuntimeError("declarative_response_extract_failed", detail="missing output"),
        DeclarativeRuntimeError("declarative_response_extract_failed", detail='远端"\\' * 5000),
        ComfyuiError("comfyui_upload_failed", detail="upload refused"),
    ],
)
async def test_domain_failure_record_matches_task_encoding(db_factory, exc):
    ledger = Ledger(session_factory=db_factory)
    with pytest.raises(type(exc)):
        async with ledger.record(project_name="demo", call_type="image", model="m", provider="openrouter"):
            raise exc
    async with db_factory() as session:
        repository = UsageRepository(session)
        page = await repository.list_records()
        row = await repository.get_record(page["items"][0]["id"])
    assert row is not None
    expected = parse_failure(bound_reason(encode_task_failure_message(exc), 2000))
    assert expected is not None
    assert (row["error_code"], row["error_params"]) == expected
    assert len(f"[{row['error_code']}] " + json.dumps(row["error_params"], ensure_ascii=False, sort_keys=True)) <= 2000
    assert row["error_message"] == str(exc)[:500]
