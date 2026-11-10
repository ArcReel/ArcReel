"""Domain rejections retain their parameters through real accounting and readback."""

import pytest

from arcreel_market_core.video_backend_contract import VideoCapabilityError
from lib.backends.image_errors import ImageCapabilityError
from lib.billing.ledger import Ledger
from lib.db.repositories.usage_repo import UsageRepository
from lib.infra.api_errors import BadRequestError


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
