import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path


@pytest.fixture
def mock_session_factory():
    """Mock async_session_factory 返回带预设配置的 session。"""
    mock_session = AsyncMock()

    def factory():
        return mock_session

    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    return mock_session, factory


async def test_create_text_client_aistudio_fallback(mock_session_factory):
    """当 Vertex 未配置时，应回退到 AI Studio。"""
    mock_session, factory = mock_session_factory

    configs = {
        "gemini-aistudio": {"api_key": "test-key-123"},
        "gemini-vertex": {},
    }

    with (
        patch("lib.text_client.async_session_factory", factory),
        patch("lib.text_client.ConfigService") as MockConfigService,
        patch("lib.text_client.GeminiClient") as MockGeminiClient,
    ):
        mock_svc = AsyncMock()
        mock_svc.get_all_provider_configs = AsyncMock(return_value=configs)
        MockConfigService.return_value = mock_svc

        mock_client = MagicMock()
        MockGeminiClient.return_value = mock_client

        from lib.text_client import create_text_client
        result = await create_text_client()

        MockGeminiClient.assert_called_once_with(api_key="test-key-123", base_url=None)
        assert result is mock_client


async def test_create_text_client_vertex_priority(mock_session_factory):
    """当 Vertex 配置有效时，应优先使用 Vertex。"""
    mock_session, factory = mock_session_factory

    configs = {
        "gemini-aistudio": {"api_key": "test-key"},
        "gemini-vertex": {"credentials_path": "/some/path.json"},
    }

    with (
        patch("lib.text_client.async_session_factory", factory),
        patch("lib.text_client.ConfigService") as MockConfigService,
        patch("lib.text_client.GeminiClient") as MockGeminiClient,
        patch("lib.text_client.resolve_vertex_credentials_path", return_value=Path("/valid/creds.json")),
    ):
        mock_svc = AsyncMock()
        mock_svc.get_all_provider_configs = AsyncMock(return_value=configs)
        MockConfigService.return_value = mock_svc

        mock_client = MagicMock()
        MockGeminiClient.return_value = mock_client

        from lib.text_client import create_text_client
        result = await create_text_client()

        MockGeminiClient.assert_called_once_with(backend="vertex", gcs_bucket=None)
        assert result is mock_client


async def test_create_text_client_vertex_creds_missing_falls_back(mock_session_factory):
    """当 Vertex credentials_path 在 DB 中有值但文件不存在时，回退到 AI Studio。"""
    mock_session, factory = mock_session_factory

    configs = {
        "gemini-aistudio": {"api_key": "fallback-key"},
        "gemini-vertex": {"credentials_path": "/stale/path.json"},
    }

    with (
        patch("lib.text_client.async_session_factory", factory),
        patch("lib.text_client.ConfigService") as MockConfigService,
        patch("lib.text_client.GeminiClient") as MockGeminiClient,
        patch("lib.text_client.resolve_vertex_credentials_path", return_value=None),
    ):
        mock_svc = AsyncMock()
        mock_svc.get_all_provider_configs = AsyncMock(return_value=configs)
        MockConfigService.return_value = mock_svc

        mock_client = MagicMock()
        MockGeminiClient.return_value = mock_client

        from lib.text_client import create_text_client
        result = await create_text_client()

        MockGeminiClient.assert_called_once_with(api_key="fallback-key", base_url=None)


async def test_create_text_client_no_config_raises(mock_session_factory):
    """当两个供应商都未配置时，应抛出 ValueError。"""
    mock_session, factory = mock_session_factory

    configs = {
        "gemini-aistudio": {},
        "gemini-vertex": {},
    }

    with (
        patch("lib.text_client.async_session_factory", factory),
        patch("lib.text_client.ConfigService") as MockConfigService,
    ):
        mock_svc = AsyncMock()
        mock_svc.get_all_provider_configs = AsyncMock(return_value=configs)
        MockConfigService.return_value = mock_svc

        from lib.text_client import create_text_client
        with pytest.raises(ValueError, match="未配置任何 Gemini 文本生成供应商"):
            await create_text_client()
