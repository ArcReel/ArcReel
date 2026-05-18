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
            base_url="https://ark.cn-beijing.volces.com/api/v3",
        )
        assert result is mock_backend


async def test_creates_ark_agent_plan_backend_uses_plan_endpoint():
    """ark-agent-plan 必须把 default_base_url=/api/plan/v3 透传到 backend，
    否则文本生成会被 ArkTextBackend 默认的 /api/v3 拉到错误的套餐网关。"""
    mock_resolver = _make_mock_resolver(
        text_backend_for_task=("ark-agent-plan", "doubao-seed-2.0-lite"),
        provider_config={"api_key": "ark-plan-key"},
    )

    with (
        patch("lib.text_backends.factory.ConfigResolver", return_value=mock_resolver),
        patch("lib.text_backends.factory.create_backend") as mock_create,
    ):
        mock_backend = MagicMock()
        mock_create.return_value = mock_backend

        await create_text_backend_for_task(TextTaskType.OVERVIEW, "my-project")

        mock_create.assert_called_once_with(
            "ark-agent-plan",
            api_key="ark-plan-key",
            model="doubao-seed-2.0-lite",
            base_url="https://ark.cn-beijing.volces.com/api/plan/v3",
        )


async def test_user_base_url_overrides_default_for_ark_agent_plan():
    mock_resolver = _make_mock_resolver(
        text_backend_for_task=("ark-agent-plan", "doubao-seed-2.0-lite"),
        provider_config={"api_key": "k", "base_url": "https://custom.example.com/v9"},
    )

    with (
        patch("lib.text_backends.factory.ConfigResolver", return_value=mock_resolver),
        patch("lib.text_backends.factory.create_backend") as mock_create,
    ):
        await create_text_backend_for_task(TextTaskType.OVERVIEW, "my-project")
        assert mock_create.call_args.kwargs["base_url"] == "https://custom.example.com/v9"


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
