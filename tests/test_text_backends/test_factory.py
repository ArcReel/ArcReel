"""Text backend factory tests.

工厂构造已收口到 assemble_backend（media_type=text）：文本工厂只解析
provider/model，构造经统一缝下沉到 ProviderSpec 表。这些测试 mock 文本 registry 的 create_backend
（spec 闭包最终调它），断言各内置文本 provider 的构造参数与 provider_name 计费归因透传零变化。
"""

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
        patch("lib.text_backends.registry.create_backend") as mock_create,
    ):
        mock_backend = MagicMock()
        mock_create.return_value = mock_backend

        result = await create_text_backend_for_task(TextTaskType.SCRIPT)

        # aistudio：base_url 无条件透传用户值（含空串），由 backend 内部处理
        mock_create.assert_called_once_with(
            "gemini",
            model="gemini-3-flash-preview",
            api_key="test-key",
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
        patch("lib.text_backends.registry.create_backend") as mock_create,
    ):
        mock_backend = MagicMock()
        mock_create.return_value = mock_backend

        result = await create_text_backend_for_task(TextTaskType.OVERVIEW, "my-project")

        # ark：用户未配 base_url → 回落 registry default
        mock_create.assert_called_once_with(
            "ark",
            model="doubao-seed-2-0-lite-260215",
            api_key="ark-key",
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
        patch("lib.text_backends.registry.create_backend") as mock_create,
    ):
        mock_backend = MagicMock()
        mock_create.return_value = mock_backend

        await create_text_backend_for_task(TextTaskType.OVERVIEW, "my-project")

        mock_create.assert_called_once_with(
            "ark-agent-plan",
            model="doubao-seed-2.0-lite",
            api_key="ark-plan-key",
            base_url="https://ark.cn-beijing.volces.com/api/plan/v3",
        )


async def test_user_base_url_overrides_default_for_ark_agent_plan():
    mock_resolver = _make_mock_resolver(
        text_backend_for_task=("ark-agent-plan", "doubao-seed-2.0-lite"),
        provider_config={"api_key": "k", "base_url": "https://custom.example.com/v9"},
    )

    with (
        patch("lib.text_backends.factory.ConfigResolver", return_value=mock_resolver),
        patch("lib.text_backends.registry.create_backend") as mock_create,
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
        patch("lib.text_backends.registry.create_backend") as mock_create,
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


async def test_creates_grok_backend_omits_base_url_when_unset():
    """grok 无 registry default 且用户未配 → 不传 base_url（GrokTextBackend 不接受该参数）。"""
    mock_resolver = _make_mock_resolver(
        text_backend_for_task=("grok", "grok-4"),
        provider_config={"api_key": "grok-key"},
    )

    with (
        patch("lib.text_backends.factory.ConfigResolver", return_value=mock_resolver),
        patch("lib.text_backends.registry.create_backend") as mock_create,
    ):
        await create_text_backend_for_task(TextTaskType.SCRIPT)
        mock_create.assert_called_once_with("grok", model="grok-4", api_key="grok-key")


async def test_creates_openai_backend_passes_user_base_url():
    """openai：base_url 无条件透传用户值（无 registry default，允许自定义 endpoint）。"""
    mock_resolver = _make_mock_resolver(
        text_backend_for_task=("openai", "gpt-5"),
        provider_config={"api_key": "oa-key", "base_url": "https://relay.example.com/v1"},
    )

    with (
        patch("lib.text_backends.factory.ConfigResolver", return_value=mock_resolver),
        patch("lib.text_backends.registry.create_backend") as mock_create,
    ):
        await create_text_backend_for_task(TextTaskType.SCRIPT)
        mock_create.assert_called_once_with(
            "openai",
            model="gpt-5",
            api_key="oa-key",
            base_url="https://relay.example.com/v1",
        )


async def test_creates_dashscope_backend_derives_base_url_and_passes_provider_name():
    """dashscope 文本走 OpenAI 兼容：从 host 派生 /compatible-mode/v1，并透传 provider_name 计费归因。"""
    mock_resolver = _make_mock_resolver(
        text_backend_for_task=("dashscope", "qwen-max"),
        provider_config={"api_key": "ds-key"},
    )

    with (
        patch("lib.text_backends.factory.ConfigResolver", return_value=mock_resolver),
        patch("lib.text_backends.registry.create_backend") as mock_create,
    ):
        await create_text_backend_for_task(TextTaskType.SCRIPT)
        mock_create.assert_called_once_with(
            "openai",
            model="qwen-max",
            api_key="ds-key",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            provider_name="dashscope",
        )


async def test_creates_minimax_backend_derives_base_url_and_passes_provider_name():
    """minimax 文本走 OpenAI 兼容：单 /v1 base，并透传 provider_name 计费归因。"""
    mock_resolver = _make_mock_resolver(
        text_backend_for_task=("minimax", "minimax-text-01"),
        provider_config={"api_key": "mm-key"},
    )

    with (
        patch("lib.text_backends.factory.ConfigResolver", return_value=mock_resolver),
        patch("lib.text_backends.registry.create_backend") as mock_create,
    ):
        await create_text_backend_for_task(TextTaskType.SCRIPT)
        mock_create.assert_called_once_with(
            "openai",
            model="minimax-text-01",
            api_key="mm-key",
            base_url="https://api.minimaxi.com/v1",
            provider_name="minimax",
        )
