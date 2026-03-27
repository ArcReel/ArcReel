"""Text backend factory tests."""
from unittest.mock import AsyncMock, patch, MagicMock

from lib.text_backends.base import TextTaskType
from lib.text_backends.factory import create_text_backend_for_task


async def test_creates_gemini_aistudio_backend():
    mock_resolver = MagicMock()
    mock_resolver.text_backend_for_task = AsyncMock(return_value=("gemini-aistudio", "gemini-3-flash-preview"))
    mock_resolver.provider_config = AsyncMock(return_value={"api_key": "test-key", "base_url": ""})

    with patch("lib.text_backends.factory.ConfigResolver", return_value=mock_resolver), \
         patch("lib.text_backends.factory.create_backend") as mock_create:
        mock_backend = MagicMock()
        mock_create.return_value = mock_backend

        result = await create_text_backend_for_task(TextTaskType.SCRIPT)

        mock_create.assert_called_once_with(
            "gemini", api_key="test-key", model="gemini-3-flash-preview", base_url="",
        )
        assert result is mock_backend


async def test_creates_ark_backend():
    mock_resolver = MagicMock()
    mock_resolver.text_backend_for_task = AsyncMock(return_value=("ark", "doubao-seed-2-0-lite-260215"))
    mock_resolver.provider_config = AsyncMock(return_value={"api_key": "ark-key"})

    with patch("lib.text_backends.factory.ConfigResolver", return_value=mock_resolver), \
         patch("lib.text_backends.factory.create_backend") as mock_create:
        mock_backend = MagicMock()
        mock_create.return_value = mock_backend

        result = await create_text_backend_for_task(TextTaskType.OVERVIEW, "my-project")

        mock_create.assert_called_once_with(
            "ark", api_key="ark-key", model="doubao-seed-2-0-lite-260215",
        )
        assert result is mock_backend


async def test_creates_vertex_backend():
    mock_resolver = MagicMock()
    mock_resolver.text_backend_for_task = AsyncMock(return_value=("gemini-vertex", "gemini-3-flash-preview"))
    mock_resolver.provider_config = AsyncMock(return_value={"gcs_bucket": "my-bucket"})

    with patch("lib.text_backends.factory.ConfigResolver", return_value=mock_resolver), \
         patch("lib.text_backends.factory.create_backend") as mock_create:
        mock_backend = MagicMock()
        mock_create.return_value = mock_backend

        result = await create_text_backend_for_task(TextTaskType.STYLE_ANALYSIS)

        mock_create.assert_called_once_with(
            "gemini", model="gemini-3-flash-preview", backend="vertex", gcs_bucket="my-bucket",
        )
        assert result is mock_backend
