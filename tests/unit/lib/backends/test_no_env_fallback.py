"""Missing api_key always raises across provider backends, without environment fallback.

Spec §5.4.
"""

from __future__ import annotations

import pytest


def test_ark_shared_no_api_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    from lib.backends.ark_shared import resolve_ark_api_key

    with pytest.raises(ValueError, match="Ark API Key"):
        resolve_ark_api_key(None)


def test_ark_shared_ignores_env_when_api_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even existing ARK_API_KEY must never supply fallback."""
    monkeypatch.setenv("ARK_API_KEY", "should-be-ignored")
    from lib.backends.ark_shared import resolve_ark_api_key

    with pytest.raises(ValueError, match="Ark API Key"):
        resolve_ark_api_key(None)


def test_grok_shared_no_api_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    from lib.backends.grok_shared import resolve_grok_api_key

    with pytest.raises(ValueError, match="xAI API Key"):
        resolve_grok_api_key(None)


def test_vidu_shared_no_api_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VIDU_API_KEY", raising=False)
    from lib.backends.vidu_shared import resolve_vidu_api_key

    with pytest.raises(ValueError, match="Vidu API Key"):
        resolve_vidu_api_key(None)
