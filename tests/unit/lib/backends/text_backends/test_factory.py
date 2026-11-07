"""Text backend factory tests.

Construction converges on assemble_backend (media_type=text): factories resolve providers/models
and delegate ProviderSpec closures. test_backend_assembly_specs.py covers provider arguments;
retain only factory contracts here: resolved providers/models/credentials reach spec closures,
and returned provider_id uses resolver IDs rather than backend registry names, solely governing billing attribution.
"""

import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.backends.text_backends.base import TextTaskType
from lib.backends.text_backends.factory import create_text_backend_for_task
from tests.fakes import captured_backend_construction


def _make_mock_resolver(**async_methods):
    """Create mocked resolvers with session() context managers."""
    mock = MagicMock()
    for name, return_value in async_methods.items():
        setattr(mock, name, AsyncMock(return_value=return_value))

    @contextlib.asynccontextmanager
    async def _session():
        yield mock

    mock.session = _session
    return mock


async def test_resolved_provider_model_and_credentials_reach_the_spec():
    mock_resolver = _make_mock_resolver(
        text_backend_for_task=("ark", "doubao-seed-2-1-lite-260915"),
        provider_config_with_credential=({"api_key": "ark-key"}, None),
    )

    with (
        patch("lib.backends.text_backends.factory.ConfigResolver", return_value=mock_resolver),
        captured_backend_construction() as built,
    ):
        await create_text_backend_for_task(TextTaskType.OVERVIEW, "my-project")

    assert built == [
        {
            "media": "text",
            "backend": "ark",
            "kwargs": {
                "model": "doubao-seed-2-1-lite-260915",
                "api_key": "ark-key",
                "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            },
        }
    ]


@pytest.mark.parametrize(
    ("provider_id", "model", "credentials", "registry_backend"),
    [
        # Resolver IDs differ from registry names for gemini dual IDs sharing gemini, and
        # ark-agent-plan sharing ArkTextBackend with name ark; accounting uses resolver IDs.
        ("gemini-aistudio", "gemini-3-flash-preview", {"api_key": "test-key", "base_url": ""}, "gemini"),
        ("gemini-vertex", "gemini-3-flash-preview", {"gcs_bucket": "my-bucket"}, "gemini"),
        ("ark-agent-plan", "doubao-seed-2.1-lite", {"api_key": "ark-plan-key"}, "ark-agent-plan"),
    ],
)
async def test_returns_resolver_provider_id_not_registry_backend_name(
    provider_id: str, model: str, credentials: dict, registry_backend: str
):
    mock_resolver = _make_mock_resolver(
        text_backend_for_task=(provider_id, model),
        provider_config_with_credential=(credentials, None),
    )

    with (
        patch("lib.backends.text_backends.factory.ConfigResolver", return_value=mock_resolver),
        captured_backend_construction() as built,
    ):
        _backend, returned_id = await create_text_backend_for_task(TextTaskType.SCRIPT)

    assert returned_id == provider_id
    assert [r["backend"] for r in built] == [registry_backend]
