"""回归：_discover_anthropic 在 base_url 带 anthropic 子路径时也走根 + /v1/models。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_discover_strips_anthropic_suffix() -> None:
    from lib.custom_provider.discovery import _discover_anthropic

    fake_response = MagicMock()
    fake_response.json.return_value = {"data": [{"id": "claude-x", "display_name": "X"}]}
    fake_response.raise_for_status = MagicMock()

    fake_client = MagicMock()
    fake_client.get = AsyncMock(return_value=fake_response)

    with patch("lib.custom_provider.discovery.get_http_client", return_value=fake_client):
        models = await _discover_anthropic("https://api.deepseek.com/anthropic", "sk")
    fake_client.get.assert_awaited_once()
    called_url = fake_client.get.await_args.args[0]
    assert called_url == "https://api.deepseek.com/v1/models"
    assert models[0]["model_id"] == "claude-x"


@pytest.mark.asyncio
async def test_discover_keeps_root_when_no_suffix() -> None:
    from lib.custom_provider.discovery import _discover_anthropic

    fake_response = MagicMock()
    fake_response.json.return_value = {"data": []}
    fake_response.raise_for_status = MagicMock()
    fake_client = MagicMock()
    fake_client.get = AsyncMock(return_value=fake_response)

    with patch("lib.custom_provider.discovery.get_http_client", return_value=fake_client):
        await _discover_anthropic("https://api.anthropic.com", "sk")
    called_url = fake_client.get.await_args.args[0]
    assert called_url == "https://api.anthropic.com/v1/models"
