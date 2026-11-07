"""lib.backends.vidu_shared credential resolution/probe environment-fallback semantics."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

import httpx
import pytest
import respx

from lib.backends import vidu_shared
from tests.http_capture import capture_http, only_request


class TestResolveViduApiKey:
    def test_explicit_key_wins(self, monkeypatch: pytest.MonkeyPatch):
        # Spec §5.4: even VIDU_API_KEY never supplies fallback; explicit arguments are sole prioritized sources.
        monkeypatch.setenv("VIDU_API_KEY", "from-env")
        assert vidu_shared.resolve_vidu_api_key("explicit") == "explicit"

    def test_env_no_longer_falls_back(self, monkeypatch: pytest.MonkeyPatch):
        """Spec §5.4 removes environment fallback; missing arguments raise even with VIDU_API_KEY set."""
        monkeypatch.setenv("VIDU_API_KEY", "from-env")
        with pytest.raises(ValueError, match="Vidu API Key"):
            vidu_shared.resolve_vidu_api_key(None)

    def test_missing_key_without_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("VIDU_API_KEY", raising=False)
        with pytest.raises(ValueError, match="Vidu API Key"):
            vidu_shared.resolve_vidu_api_key(None)


class TestViduConnectionTestKeyResolution:
    """Missing connection-test api_key raises during resolution, never initiating HTTP."""

    def test_missing_config_key_raises_before_http(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("VIDU_API_KEY", "from-env")

        # No declared routes means real HTTP fails through respx; resolution must fail before outbound requests.
        with capture_http() as router:
            with pytest.raises(ValueError, match="Vidu API Key"):
                vidu_shared.test_vidu_connection({})

            assert router.calls.call_count == 0


@contextmanager
def _probe_route(*, status_code: int, body: str = "") -> Generator[respx.Route]:
    """Transport-level respx captures connection probes.

    Allowlisting uses real response statuses; URL joining/Authorization headers are asserted.
    """
    with capture_http() as router:
        yield router.get(url__regex=r".*/tasks/0/creations").mock(return_value=httpx.Response(status_code, text=body))


class TestViduConnectionTestUrl:
    """Connection probes use numeric task IDs because Vidu parses integers, rejecting others with 400 CODEC."""

    def test_url_uses_numeric_bogus_id(self):
        with _probe_route(status_code=404) as route:
            vidu_shared.test_vidu_connection({"api_key": "vda_test"})

        request = only_request(route)
        assert request.url.path.endswith("/tasks/0/creations")
        assert request.headers["authorization"] == "Token vda_test"

    def test_404_is_success(self):
        """404 means missing task but accepted auth, so allow without errors and verify actual probe requests."""
        with _probe_route(status_code=404) as route:
            assert vidu_shared.test_vidu_connection({"api_key": "vda_test"}) is None

        assert route.call_count == 1

    def test_401_is_invalid_credential(self):
        with _probe_route(status_code=401), pytest.raises(RuntimeError, match="凭证无效"):
            vidu_shared.test_vidu_connection({"api_key": "vda_test"})

    def test_400_is_undecidable(self):
        with _probe_route(status_code=400, body="CODEC parse error"), pytest.raises(RuntimeError, match="无法判定"):
            vidu_shared.test_vidu_connection({"api_key": "vda_test"})


class TestFetchViduTaskErrorRedaction:
    async def test_poll_4xx_message_drops_query_credentials(self):
        # Polling 4xx str(exc) enters task.error_message/logs; query credentials appear in
        # raise_for_status URLs, so sanitize before raising.
        with capture_http() as router:
            router.get(url__regex=r"^https://api\.vidu\.cn/ent/v2/tasks/t-1/creations").mock(
                return_value=httpx.Response(401, json={"detail": "invalid token"})
            )
            async with httpx.AsyncClient(base_url=vidu_shared.VIDU_BASE_URL, params={"api_key": "SECRETKEY"}) as client:
                with pytest.raises(httpx.HTTPStatusError) as excinfo:
                    await vidu_shared.fetch_vidu_task(client, "t-1")

        assert "SECRETKEY" not in str(excinfo.value)
        assert excinfo.value.response.status_code == 401
