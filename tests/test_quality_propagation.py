"""quality 参数传递链单元测试。

覆盖范围：
1. ImageGenerationResult 接受并存储 quality 字段
2. ImageGenerationResult quality 默认为 None
3. OpenAIImageBackend._save_and_return 在结果中填充 quality
4. UsageTracker.finish_call 透传 quality 到 UsageRepository
5. UsageRepository.finish_call 透传 quality 到 CostCalculator
"""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1 & 2: ImageGenerationResult dataclass
# ---------------------------------------------------------------------------


class TestImageGenerationResultQualityField:
    def test_quality_defaults_to_none(self):
        from lib.image_backends.base import ImageGenerationResult

        result = ImageGenerationResult(
            image_path=Path("/tmp/img.png"),
            provider="openai",
            model="gpt-image-1.5",
        )
        assert result.quality is None

    def test_quality_can_be_set(self):
        from lib.image_backends.base import ImageGenerationResult

        for quality_value in ("low", "medium", "high"):
            result = ImageGenerationResult(
                image_path=Path("/tmp/img.png"),
                provider="openai",
                model="gpt-image-1.5",
                quality=quality_value,
            )
            assert result.quality == quality_value

    def test_quality_accepts_none_explicitly(self):
        from lib.image_backends.base import ImageGenerationResult

        result = ImageGenerationResult(
            image_path=Path("/tmp/img.png"),
            provider="openai",
            model="gpt-image-1.5",
            quality=None,
        )
        assert result.quality is None


# ---------------------------------------------------------------------------
# 3: OpenAIImageBackend._save_and_return fills quality
# ---------------------------------------------------------------------------


def _make_mock_image_response(b64_data: str = "aW1hZ2VfZGF0YQ=="):
    """构造 mock ImagesResponse。"""
    datum = MagicMock()
    datum.b64_json = b64_data
    response = MagicMock()
    response.data = [datum]
    return response


class TestOpenAIImageBackendQuality:
    async def test_generate_result_contains_quality(self, tmp_path: Path):
        """generate() 返回的 ImageGenerationResult 应包含正确的 quality 值。"""
        b64_data = base64.b64encode(b"fake-png").decode()
        mock_client = AsyncMock()
        mock_client.images.generate = AsyncMock(return_value=_make_mock_image_response(b64_data))

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.image_backends.openai import OpenAIImageBackend
            from lib.image_backends.base import ImageGenerationRequest

            backend = OpenAIImageBackend(api_key="test-key")
            output_path = tmp_path / "out.png"
            request = ImageGenerationRequest(
                prompt="test",
                output_path=output_path,
                image_size="1K",
            )
            result = await backend.generate(request)

        assert result.quality == "medium"

    async def test_quality_propagated_for_all_sizes(self, tmp_path: Path):
        """所有 image_size 值都应正确映射到 quality 字段。"""
        b64_data = base64.b64encode(b"img").decode()
        mock_client = AsyncMock()
        mock_client.images.generate = AsyncMock(return_value=_make_mock_image_response(b64_data))

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.image_backends.openai import OpenAIImageBackend
            from lib.image_backends.base import ImageGenerationRequest

            backend = OpenAIImageBackend(api_key="test-key")

            expected = {
                "512PX": "low",
                "1K": "medium",
                "2K": "high",
                "4K": "high",
            }
            for img_size, expected_quality in expected.items():
                output_path = tmp_path / f"out_{img_size}.png"
                request = ImageGenerationRequest(
                    prompt="test",
                    output_path=output_path,
                    image_size=img_size,
                )
                result = await backend.generate(request)
                assert result.quality == expected_quality, f"image_size={img_size}"

    async def test_unknown_size_defaults_to_medium(self, tmp_path: Path):
        """未知 image_size 应 fallback 到 'medium'。"""
        b64_data = base64.b64encode(b"img").decode()
        mock_client = AsyncMock()
        mock_client.images.generate = AsyncMock(return_value=_make_mock_image_response(b64_data))

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.image_backends.openai import OpenAIImageBackend
            from lib.image_backends.base import ImageGenerationRequest

            backend = OpenAIImageBackend(api_key="test-key")
            output_path = tmp_path / "out_unknown.png"
            request = ImageGenerationRequest(
                prompt="test",
                output_path=output_path,
                image_size="UNKNOWN",
            )
            result = await backend.generate(request)

        assert result.quality == "medium"


# ---------------------------------------------------------------------------
# 4: UsageTracker.finish_call 透传 quality
# ---------------------------------------------------------------------------


class TestUsageTrackerQualityPropagation:
    async def test_finish_call_passes_quality_to_repo(self):
        """UsageTracker.finish_call 应将 quality 传递给 UsageRepository.finish_call。"""
        mock_repo = AsyncMock()
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("lib.usage_tracker.UsageRepository") as MockRepo, patch(
            "lib.usage_tracker.safe_session_factory", return_value=mock_session
        ):
            MockRepo.return_value = mock_repo

            from lib.usage_tracker import UsageTracker

            tracker = UsageTracker()
            await tracker.finish_call(
                call_id=42,
                status="success",
                output_path="/tmp/img.png",
                quality="high",
            )

        mock_repo.finish_call.assert_awaited_once()
        call_kwargs = mock_repo.finish_call.call_args[1]
        assert call_kwargs.get("quality") == "high"

    async def test_finish_call_quality_defaults_none(self):
        """未传 quality 时，UsageTracker 应传 quality=None 给 repo。"""
        mock_repo = AsyncMock()
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("lib.usage_tracker.UsageRepository") as MockRepo, patch(
            "lib.usage_tracker.safe_session_factory", return_value=mock_session
        ):
            MockRepo.return_value = mock_repo

            from lib.usage_tracker import UsageTracker

            tracker = UsageTracker()
            await tracker.finish_call(call_id=1, status="success")

        call_kwargs = mock_repo.finish_call.call_args[1]
        assert call_kwargs.get("quality") is None


# ---------------------------------------------------------------------------
# 5: UsageRepository.finish_call 透传 quality 到 CostCalculator
# ---------------------------------------------------------------------------


class TestUsageRepositoryQualityToCostCalculator:
    async def test_quality_passed_to_calculate_cost(self):
        """UsageRepository.finish_call 应将 quality 传给 CostCalculator.calculate_cost。"""
        from lib.db.models.api_call import ApiCall
        from lib.providers import PROVIDER_OPENAI

        mock_row = MagicMock(spec=ApiCall)
        mock_row.id = 1
        mock_row.provider = PROVIDER_OPENAI
        mock_row.call_type = "image"
        mock_row.model = "gpt-image-1.5"
        mock_row.resolution = "1K"
        mock_row.duration_seconds = None
        mock_row.generate_audio = True
        mock_row.currency = "USD"
        mock_row.started_at = MagicMock()
        mock_row.started_at.__sub__ = MagicMock(return_value=MagicMock(total_seconds=lambda: 1.0))

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_row

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        with patch("lib.db.repositories.usage_repo.cost_calculator") as mock_calc:
            mock_calc.calculate_cost.return_value = (0.02, "USD")

            from lib.db.repositories.usage_repo import UsageRepository

            repo = UsageRepository(mock_session)
            await repo.finish_call(
                1,
                status="success",
                quality="high",
            )

        mock_calc.calculate_cost.assert_called_once()
        call_kwargs = mock_calc.calculate_cost.call_args[1]
        assert call_kwargs.get("quality") == "high"

    async def test_quality_none_when_not_provided(self):
        """未传 quality 时，CostCalculator 应收到 quality=None。"""
        from lib.db.models.api_call import ApiCall
        from lib.providers import PROVIDER_OPENAI

        mock_row = MagicMock(spec=ApiCall)
        mock_row.id = 1
        mock_row.provider = PROVIDER_OPENAI
        mock_row.call_type = "image"
        mock_row.model = "gpt-image-1.5"
        mock_row.resolution = "1K"
        mock_row.duration_seconds = None
        mock_row.generate_audio = True
        mock_row.currency = "USD"
        mock_row.started_at = MagicMock()
        mock_row.started_at.__sub__ = MagicMock(return_value=MagicMock(total_seconds=lambda: 1.0))

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_row

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        with patch("lib.db.repositories.usage_repo.cost_calculator") as mock_calc:
            mock_calc.calculate_cost.return_value = (0.02, "USD")

            from lib.db.repositories.usage_repo import UsageRepository

            repo = UsageRepository(mock_session)
            await repo.finish_call(1, status="success")

        call_kwargs = mock_calc.calculate_cost.call_args[1]
        assert call_kwargs.get("quality") is None
