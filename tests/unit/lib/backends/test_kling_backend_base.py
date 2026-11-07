"""KlingBackendBase scaffolding: both media backends share auth/credentials/submit-poll source,
defined only on the base rather than copied into subclasses."""

from __future__ import annotations

import inspect

import httpx
import jwt
import pytest

from lib.backends.image_backends.kling import KlingImageBackend
from lib.backends.kling_backend_base import KlingBackendBase
from lib.backends.video_backends.kling import KlingVideoBackend
from tests.http_capture import capture_http

_SECRET = "s" * 40


class TestSharedBaseLineage:
    def test_both_backends_subclass_shared_base(self):
        assert issubclass(KlingImageBackend, KlingBackendBase)
        assert issubclass(KlingVideoBackend, KlingBackendBase)


class TestSingleSourceScaffolding:
    def test_headers_defined_only_on_base(self):
        # _headers exists only on the base, without copied subclass overrides.
        assert "_headers" in vars(KlingBackendBase)
        assert "_headers" not in vars(KlingImageBackend)
        assert "_headers" not in vars(KlingVideoBackend)

    def test_credential_dispatch_defined_only_on_base(self):
        # Base __init__ owns credential dispatch/URL normalization; subclasses only finish media-specific assembly.
        # _jwt/_static_api_key bind instance attributes, absent from class __dict__; vars() checks would be vacuous.
        # Inspect source instead: subclasses delegate super() without credential-dispatch copies, detected by these names.
        for subclass in (KlingImageBackend, KlingVideoBackend):
            init_src = inspect.getsource(subclass.__init__)
            assert "super().__init__(" in init_src
            assert "_jwt" not in init_src
            assert "_static_api_key" not in init_src
        # name/model are base class descriptors present in __dict__; subclasses never override them.
        assert "name" in vars(KlingBackendBase)
        assert "model" in vars(KlingBackendBase)
        assert "name" not in vars(KlingImageBackend)
        assert "name" not in vars(KlingVideoBackend)
        assert "model" not in vars(KlingImageBackend)
        assert "model" not in vars(KlingVideoBackend)

    def test_submit_and_poll_skeleton_on_base(self):
        # Base provides submit/poll retry scaffolding.
        assert "_submit_task" in vars(KlingBackendBase)
        assert "_poll_query" in vars(KlingBackendBase)
        assert "_poll_until_terminal" in vars(KlingBackendBase)


class TestDualModeAuthViaBase:
    def test_jwt_mode_signs_bearer_token(self):
        backend = KlingImageBackend(auth_mode="jwt", access_key="ak-1", secret_key=_SECRET)
        headers = backend._headers()
        token = headers["Authorization"].removeprefix("Bearer ")
        claims = jwt.decode(token, _SECRET, algorithms=["HS256"], options={"verify_exp": False})
        assert claims["iss"] == "ak-1"

    def test_bearer_mode_uses_static_key(self):
        backend = KlingVideoBackend(auth_mode="bearer", api_key="static-key")
        assert backend._headers()["Authorization"] == "Bearer static-key"

    def test_unknown_auth_mode_raises_from_base(self):
        with pytest.raises(ValueError, match=r"未知 Kling auth_mode"):
            KlingImageBackend(auth_mode="oauth", api_key="k")


class TestPollErrorRedaction:
    async def test_poll_4xx_message_drops_query_credentials(self):
        # Polling 4xx str(exc) enters task.error_message/logs; raise_for_status URLs contain query credentials,
        # requiring sanitization before raising.
        backend = KlingVideoBackend(auth_mode="bearer", api_key="static-key", base_url="https://kling.example/v1")
        with capture_http() as router:
            router.get(url__regex=r"^https://kling\.example/v1/tasks/job-1").mock(
                return_value=httpx.Response(403, json={"message": "forbidden"})
            )
            async with httpx.AsyncClient(params={"api_key": "SECRETKEY"}) as client:
                with pytest.raises(httpx.HTTPStatusError) as excinfo:
                    await backend._poll_query(client, "tasks/job-1")

        assert "SECRETKEY" not in str(excinfo.value)
        assert excinfo.value.response.status_code == 403
