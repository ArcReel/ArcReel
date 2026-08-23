"""Shared fixtures for text backend tests."""

from unittest.mock import patch

import pytest


@pytest.fixture
def sync_to_thread():
    """Patch asyncio.to_thread to run synchronously for testing."""
    with patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn(*a, **kw)):
        yield
