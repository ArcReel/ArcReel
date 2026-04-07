"""Text backend factory tests."""

import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

from lib.text_backends.base import TextTaskType
from lib.text_backends.factory import create_text_backend_for_task


def _make_mock_resolver(**async_methods):
    """创建带 session() 上下文管理器的 mock resolver。"""
    mock = MagicMock()
    for name, return_value in async_methods.items():
        setattr(mock, name, AsyncMock(return_value=return_value))

    @contextlib.asynccontextmanager
    async def _session():
        yield mock

    mock.session = _session
    return mock


async def test_creates_gemini_aistudio_backend():
    mock_resolver = _make_mock_resolver(
        text_backend_for_task=("gemini-aistudio", "gemini-3-flash-preview"),
        provider_config={"api_key": "test-key", "base_url": ""},
    )

    with (
        patch("lib.text_backends.factory.ConfigResolver", return_value=mock_resolver),
        patch("lib.text_backends.factory.create_backend") as mock_create,
    ):
        mock_backend = MagicMock()
        mock_create.return_value = mock_backend

        result = await create_text_backend_for_task(TextTaskType.SCRIPT)

        mock_create.assert_called_once_with(
            "gemini",
            api_key="test-key",
            model="gemini-3-flash-preview",
            base_url="",
        )
        assert result is mock_backend


async def test_creates_ark_backend():
    mock_resolver = _make_mock_resolver(
        text_backend_for_task=("ark", "doubao-seed-2-0-lite-260215"),
        provider_config={"api_key": "ark-key"},
    )

    with (
        patch("lib.text_backends.factory.ConfigResolver", return_value=mock_resolver),
        patch("lib.text_backends.factory.create_backend") as mock_create,
    ):
        mock_backend = MagicMock()
        mock_create.return_value = mock_backend

        result = await create_text_backend_for_task(TextTaskType.OVERVIEW, "my-project")

        mock_create.assert_called_once_with(
            "ark",
            api_key="ark-key",
            model="doubao-seed-2-0-lite-260215",
        )
        assert result is mock_backend


async def test_creates_vertex_backend():
    mock_resolver = _make_mock_resolver(
        text_backend_for_task=("gemini-vertex", "gemini-3-flash-preview"),
        provider_config={"gcs_bucket": "my-bucket"},
    )

    with (
        patch("lib.text_backends.factory.ConfigResolver", return_value=mock_resolver),
        patch("lib.text_backends.factory.create_backend") as mock_create,
    ):
        mock_backend = MagicMock()
        mock_create.return_value = mock_backend

        result = await create_text_backend_for_task(TextTaskType.STYLE_ANALYSIS)

        mock_create.assert_called_once_with(
            "gemini",
            model="gemini-3-flash-preview",
            backend="vertex",
            gcs_bucket="my-bucket",
        )
        assert result is mock_backend
