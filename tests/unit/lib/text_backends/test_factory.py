"""Text backend factory tests.

工厂构造已收口到 assemble_backend（media_type=text）：文本工厂只解析 provider/model，构造经统一缝
下沉到 ProviderSpec 表。逐 provider 的构造参数表由 test_backend_assembly_specs.py 覆盖，这里只保工厂
自身的两条契约：解析层给出的 provider/model/凭证真到达 spec 闭包；返回的 provider_id 取解析层 id
（而非 backend 注册名），计费归因据此单一真相。
"""

import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.text_backends.base import TextTaskType
from lib.text_backends.factory import create_text_backend_for_task
from tests.fakes import captured_backend_construction


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


async def test_resolved_provider_model_and_credentials_reach_the_spec():
    mock_resolver = _make_mock_resolver(
        text_backend_for_task=("ark", "doubao-seed-2-0-lite-260215"),
        provider_config={"api_key": "ark-key"},
    )

    with (
        patch("lib.text_backends.factory.ConfigResolver", return_value=mock_resolver),
        captured_backend_construction() as built,
    ):
        await create_text_backend_for_task(TextTaskType.OVERVIEW, "my-project")

    assert built == [
        {
            "media": "text",
            "backend": "ark",
            "kwargs": {
                "model": "doubao-seed-2-0-lite-260215",
                "api_key": "ark-key",
                "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            },
        }
    ]


@pytest.mark.parametrize(
    ("provider_id", "model", "credentials", "registry_backend"),
    [
        # 解析层 id 与 backend 注册名不同的两族：gemini 双 id 共用 "gemini" 注册名；
        # ark-agent-plan 复用 ArkTextBackend（name 报 "ark"）。记账须取解析层 id。
        ("gemini-aistudio", "gemini-3-flash-preview", {"api_key": "test-key", "base_url": ""}, "gemini"),
        ("gemini-vertex", "gemini-3-flash-preview", {"gcs_bucket": "my-bucket"}, "gemini"),
        ("ark-agent-plan", "doubao-seed-2.0-lite", {"api_key": "ark-plan-key"}, "ark-agent-plan"),
    ],
)
async def test_returns_resolver_provider_id_not_registry_backend_name(
    provider_id: str, model: str, credentials: dict, registry_backend: str
):
    mock_resolver = _make_mock_resolver(
        text_backend_for_task=(provider_id, model),
        provider_config=credentials,
    )

    with (
        patch("lib.text_backends.factory.ConfigResolver", return_value=mock_resolver),
        captured_backend_construction() as built,
    ):
        _backend, returned_id = await create_text_backend_for_task(TextTaskType.SCRIPT)

    assert returned_id == provider_id
    assert [r["backend"] for r in built] == [registry_backend]
