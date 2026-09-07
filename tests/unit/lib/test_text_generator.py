"""Tests for TextGenerator wrapper."""

from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.db.models.api_call import ApiCall
from lib.ledger import Ledger
from lib.providers import CallPurpose
from lib.text_backends.base import TextGenerationRequest, TextGenerationResult
from lib.text_generator import TextGenerator


@dataclass
class _Wired:
    """记账写侧注入 Ledger，读侧直查 api_calls 行，共享同一内存库。"""

    ledger: Ledger
    session_factory: async_sessionmaker[AsyncSession]

    async def stored_calls(self, project_name: str | None = None) -> list[ApiCall]:
        """按 id 升序读回落库的 api_calls 行。"""
        stmt = select(ApiCall).order_by(ApiCall.id)
        if project_name is not None:
            stmt = stmt.where(ApiCall.project_name == project_name)
        async with self.session_factory() as session:
            return list((await session.execute(stmt)).scalars().all())


@pytest.fixture
async def wired(db_factory):
    return _Wired(ledger=Ledger(session_factory=db_factory), session_factory=db_factory)


def _make_backend(provider="gemini", model="gemini-3-flash-preview"):
    backend = AsyncMock()
    backend.name = provider
    backend.model = model
    backend.generate = AsyncMock(
        return_value=TextGenerationResult(
            text="生成的文本",
            provider=provider,
            model=model,
            input_tokens=100,
            output_tokens=50,
        )
    )
    return backend


class TestTextGenerator:
    async def test_generate_records_usage_on_success(self, wired):
        # backend.name 为裸 "gemini"，记账须取解析层 provider_id（"gemini-aistudio"）。
        backend = _make_backend()
        gen = TextGenerator(backend, wired.ledger, "gemini-aistudio", purpose=CallPurpose.SCRIPT_GENERATION)

        result = await gen.generate(
            TextGenerationRequest(prompt="测试"),
            project_name="demo",
        )

        assert result.text == "生成的文本"
        assert result.input_tokens == 100
        assert result.output_tokens == 50

        calls = await wired.stored_calls("demo")
        assert len(calls) == 1
        item = calls[0]
        assert item.call_type == "text"
        assert item.status == "success"
        assert item.input_tokens == 100
        assert item.output_tokens == 50
        assert item.provider == "gemini-aistudio"
        assert item.cost_amount == pytest.approx((100 * 0.50 + 50 * 3.00) / 1_000_000)

    async def test_generate_records_usage_on_failure(self, wired):
        backend = _make_backend()
        backend.generate = AsyncMock(side_effect=RuntimeError("API 超时"))
        gen = TextGenerator(backend, wired.ledger, "gemini-aistudio", purpose=CallPurpose.SCRIPT_GENERATION)

        with pytest.raises(RuntimeError, match="API 超时"):
            await gen.generate(
                TextGenerationRequest(prompt="测试"),
                project_name="demo",
            )

        calls = await wired.stored_calls("demo")
        assert len(calls) == 1
        item = calls[0]
        assert item.status == "failed"
        assert item.cost_amount == 0.0
        assert "API 超时" in item.error_message

    async def test_generate_without_project_name(self, wired):
        backend = _make_backend()
        gen = TextGenerator(backend, wired.ledger, "gemini-aistudio", purpose=CallPurpose.SCRIPT_GENERATION)

        result = await gen.generate(TextGenerationRequest(prompt="工具箱调用"))

        assert result.text == "生成的文本"
        calls = await wired.stored_calls()
        assert len(calls) == 1
        item = calls[0]
        assert item.project_name == ""
