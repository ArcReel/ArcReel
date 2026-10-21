"""Agent 会话的用量记账：FakeSDKClient 发出 CLI 原始帧，真实 SessionManager、Ledger 与数据库落账。

每轮每个模型一条供应商调用记录，按请求从 token 与模型目录计价（docs/adr/0102）。CLI 的累计值语义（``modelUsage``
在一个连接内跨轮累计、恢复会话时带着此前的花费、会话重置后清零）与「``modelUsage`` 的键是映射后的真实模型名」
用构造的帧序列固定在这里。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from arcreel_catalog_core import CatalogEntry, Meter, PriceRow, PriceTable, TextFacts
from lib.db.models.api_call import ApiCall
from lib.db.repositories.agent_credential_repo import AgentCredentialRepository
from lib.infra.data_root_layout import DataRootLayout
from lib.project.project_manager import ProjectManager
from server.agent_runtime.event_log import EventLogStore
from server.agent_runtime.options_assembler import load_provider_env_overrides
from server.agent_runtime.service import AssistantService
from server.agent_runtime.session_manager import SessionManager
from server.agent_runtime.session_store import SessionMetaStore
from tests.catalog_support import catalog_of, install_catalog
from tests.fakes import (
    FakeSDKClient,
    ScriptedFrame,
    assistant_frame,
    bind_session_factory,
    command_lifecycle_frame,
    result_frame,
    system_frame,
    use_sdk_client,
)

SDK_ID = "sdk-billing-1"

_TIER = ("context_tier",)


def _meter(meter: str, *rows: tuple[dict[str, object], float], dims: tuple[str, ...] = ()) -> Meter:
    return Meter(meter=meter, dims=dims, rows=tuple(PriceRow(when=when, price=price) for when, price in rows))


def _tiered(meter: str, low: float, high: float) -> Meter:
    """总输入超过 20 万 token 的请求按高档计价。"""
    return _meter(meter, ({"context_tier": 0}, low), ({"context_tier": 200_000}, high), dims=_TIER)


#: 主模型：按单次请求的输入总量分两档，USD / 百万 token。
_MAIN = CatalogEntry(
    maker="acme",
    name="main-model",
    media_type="text",
    display_name="Main",
    facts=TextFacts(),
    price=PriceTable(
        currency="USD",
        meters=(
            _tiered("uncached_input_tokens", 2.0, 4.0),
            _tiered("cache_read_tokens", 0.2, 0.4),
            _tiered("cache_write_5m_tokens", 2.5, 5.0),
            _tiered("cache_write_1h_tokens", 4.0, 8.0),
            _tiered("output_tokens", 10.0, 15.0),
        ),
    ),
)

#: 子智能体模型：不分档。
_SUB = CatalogEntry(
    maker="acme",
    name="sub-model",
    media_type="text",
    display_name="Sub",
    facts=TextFacts(),
    price=PriceTable(
        currency="USD",
        meters=(
            _meter("uncached_input_tokens", ({}, 1.0)),
            _meter("cache_read_tokens", ({}, 0.1)),
            _meter("cache_write_5m_tokens", ({}, 1.25)),
            _meter("cache_write_1h_tokens", ({}, 2.0)),
            _meter("output_tokens", ({}, 5.0)),
        ),
    ),
)


@pytest.fixture
def acme_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    install_catalog(monkeypatch, catalog_of((_MAIN, _SUB), {}))


def _request(
    message_id: str,
    *,
    model: str = "main-model",
    parent_tool_use_id: str | None = None,
    input_tokens: int = 0,
    cache_read: int = 0,
    write_5m: int = 0,
    write_1h: int = 0,
    output: int = 0,
    split: bool = True,
) -> dict[str, Any]:
    """一次 API 请求的 assistant 帧，``usage`` 原样转发 Messages API 的形状。``split`` 为假时只给缓存写的合计。"""
    usage: dict[str, Any] = {
        "input_tokens": input_tokens,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": write_5m + write_1h,
        "output_tokens": output,
    }
    if split:
        usage["cache_creation"] = {"ephemeral_5m_input_tokens": write_5m, "ephemeral_1h_input_tokens": write_1h}
    return assistant_frame(
        {"type": "text", "text": "…"},
        session_id=SDK_ID,
        model=model,
        message_id=message_id,
        parent_tool_use_id=parent_tool_use_id,
        usage=usage,
    )


def _totals(input_tokens: int = 0, output: int = 0, cache_read: int = 0, cache_write: int = 0) -> dict[str, Any]:
    """``modelUsage`` 的一项：这个连接到本轮为止的累计值。"""
    return {
        "inputTokens": input_tokens,
        "outputTokens": output,
        "cacheReadInputTokens": cache_read,
        "cacheCreationInputTokens": cache_write,
        "webSearchRequests": 0,
        "costUSD": 99.0,
    }


def _result(uuid: str, model_usage: dict[str, Any], **fields: Any) -> dict[str, Any]:
    # total_cost_usd 与 costUSD 故意给出离谱的值：记账不采用 SDK 回报的费用。
    return result_frame(session_id=SDK_ID, uuid=uuid, modelUsage=model_usage, total_cost_usd=123.0, **fields)


def _idle() -> dict[str, Any]:
    return system_frame("session_state_changed", state="idle", session_id=SDK_ID)


def _started() -> Callable[[FakeSDKClient], dict[str, Any]]:
    """CLI 开始处理最近送入的那条消息。"""
    return lambda client: command_lifecycle_frame(client.sent_messages[-1]["uuid"], "started", session_id=SDK_ID)


class _Harness:
    """真实 AssistantService + SessionManager；会话连接时经生产的凭证注入读当前生效的 Agent 凭证。"""

    def __init__(
        self, service: AssistantService, factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.service = service
        self.manager = service.session_manager
        self.factory = factory
        #: 下一次连接拿到的 CLI 替身，连接之后是这个会话正在用的替身。
        self.client: FakeSDKClient | None = None

        async def _build_options(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
            return SimpleNamespace(env=await load_provider_env_overrides())

        monkeypatch.setattr(self.manager, "_build_options", _build_options)
        use_sdk_client(monkeypatch, lambda options: self.client)

    async def new_session(self, prompt: str, *frames: ScriptedFrame) -> None:
        """新建会话并跑完第一轮。"""
        self.client = FakeSDKClient(frames=[system_frame("init", session_id=SDK_ID), *frames, _idle()])
        await self.service.send_or_create("demo", prompt)
        await self.wait_settled()

    async def resume(self, prompt: str, *frames: ScriptedFrame) -> None:
        """会话被回收后发消息：冷启动恢复会话，跑完这一轮。"""
        await self.manager.close_session(SDK_ID)
        self.client = FakeSDKClient(frames=[_started(), *frames, _idle()])
        await self.service.send_or_create("demo", prompt, session_id=SDK_ID)
        await self.wait_settled()

    async def turn(self, prompt: str, *frames: ScriptedFrame) -> None:
        """在已连接的会话上再跑一轮。"""
        assert self.client is not None
        await self.service.send_or_create("demo", prompt, session_id=SDK_ID)
        for frame in (_started(), *frames, _idle()):
            self.client.push_frame(frame)
        await self.wait_settled()

    async def wait_settled(self) -> None:
        async with asyncio.timeout(5):
            while True:
                managed = self.manager.sessions.get(SDK_ID)
                if managed is not None and managed.status != "running":
                    break
                await asyncio.sleep(0.01)
        assert managed.status in ("completed", "interrupted")

    async def rows(self) -> list[ApiCall]:
        async with self.factory() as session:
            return list((await session.execute(select(ApiCall).order_by(ApiCall.id))).scalars())

    async def add_credential(self, name: str, *, active: bool = False) -> int:
        async with self.factory() as session:
            repo = AgentCredentialRepository(session)
            cred = await repo.create(preset_id="__custom__", display_name=name, base_url="https://gw", api_key="k")
            if active:
                await repo.set_active(cred.id)
            await session.commit()
            return cred.id

    async def set_multiplier(self, cred_id: int, multiplier: float) -> None:
        async with self.factory() as session:
            await AgentCredentialRepository(session).update(cred_id, multiplier=multiplier)
            await session.commit()

    async def activate(self, cred_id: int) -> None:
        async with self.factory() as session:
            await AgentCredentialRepository(session).set_active(cred_id)
            await session.commit()


@pytest.fixture
async def harness(tmp_path, file_db_factory, monkeypatch: pytest.MonkeyPatch) -> Any:
    # 生产的凭证注入经 lib.db 的全局工厂读凭证，这里换成用例的数据库。
    bind_session_factory(monkeypatch, file_db_factory)
    manager = SessionManager(
        project_root=tmp_path,
        meta_store=SessionMetaStore(session_factory=file_db_factory),
        event_log_store=EventLogStore(session_factory=file_db_factory),
    )
    (DataRootLayout(tmp_path).projects_dir / "demo").mkdir(parents=True)
    service = AssistantService(project_root=tmp_path)
    service.pm = ProjectManager(tmp_path)
    service.session_manager = manager
    service.meta_store = manager.meta_store
    service.event_log_store = manager.event_log_store
    harness = _Harness(service, file_db_factory, monkeypatch)
    harness.credential_id = await harness.add_credential("网关 A", active=True)
    try:
        yield harness
    finally:
        await manager.close_session(SDK_ID)


def _by_model(rows: list[ApiCall]) -> dict[str, ApiCall]:
    return {row.model: row for row in rows}


@pytest.mark.usefixtures("acme_catalog")
class TestPricing:
    async def test_each_model_gets_one_row_and_frames_of_one_request_count_once(self, harness: Any) -> None:
        await harness.new_session(
            "写分镜",
            # 同一请求的两帧：第一帧的 output 只是占位值
            _request("msg-1", input_tokens=1_000, cache_read=10_000, write_5m=2_000, write_1h=1_000, output=1),
            _request("msg-1", input_tokens=1_000, cache_read=10_000, write_5m=2_000, write_1h=1_000, output=500),
            # 子智能体的请求；网关只给缓存写合计
            _request(
                "msg-2",
                model="sub-model",
                parent_tool_use_id="tu-1",
                input_tokens=3_000,
                write_5m=400,
                output=200,
                split=False,
            ),
            _request("msg-3", input_tokens=500, cache_read=14_000, output=100),
            # modelUsage 的键是映射后的真实模型名，与 assistant 帧的 model 一致：累计值等于流内合计，没有差额
            _result(
                "r-1",
                {
                    "main-model": _totals(1_500, 600, cache_read=24_000, cache_write=3_000),
                    "sub-model": _totals(3_000, 200, cache_write=400),
                },
            ),
        )

        rows = await harness.rows()
        assert len(rows) == 2
        main, sub = _by_model(rows)["main-model"], _by_model(rows)["sub-model"]
        assert (main.input_tokens, main.output_tokens, main.cache_read_tokens) == (28_500, 600, 24_000)
        assert (main.cache_write_5m_tokens, main.cache_write_1h_tokens) == (2_000, 1_000)
        # msg-1：1000×2 + 10000×0.2 + 2000×2.5 + 1000×4 + 500×10；msg-3：500×2 + 14000×0.2 + 100×10
        assert main.cost_amount == pytest.approx((18_000 + 4_800) / 1e6)
        assert (sub.input_tokens, sub.output_tokens, sub.cache_write_5m_tokens, sub.cache_write_1h_tokens) == (
            3_400,
            200,
            400,
            0,
        )
        assert sub.cost_amount == pytest.approx((3_000 * 1 + 400 * 1.25 + 200 * 5) / 1e6)
        for row in rows:
            assert (row.status, row.currency, row.purpose, row.call_type) == (
                "success",
                "USD",
                "assistant_session",
                "text",
            )
            assert (row.session_id, row.prompt, row.provider) == (SDK_ID, "写分镜", f"agent-{harness.credential_id}")
        assert main.started_at == sub.started_at

    async def test_each_request_takes_the_price_of_its_own_context_tier(self, harness: Any) -> None:
        await harness.new_session(
            "写分镜",
            _request("msg-1", input_tokens=150_000, output=1_000),
            _request("msg-2", input_tokens=50_000, cache_read=160_000, output=1_000),
            _result("r-1", {"main-model": _totals(200_000, 2_000, cache_read=160_000)}),
        )

        (row,) = await harness.rows()
        # msg-1 总输入 15 万落低档；msg-2 总输入 21 万落高档
        low = 150_000 * 2 + 1_000 * 10
        high = 50_000 * 4 + 160_000 * 0.4 + 1_000 * 15
        assert row.cost_amount == pytest.approx((low + high) / 1e6)

    async def test_usage_missing_from_the_stream_is_billed_as_one_more_request(self, harness: Any) -> None:
        await harness.new_session(
            "写分镜",
            _request("msg-1", input_tokens=1_000, output=100),
            # 压缩等流外调用：主模型多出 21 万输入与 5000 输出，另有一个流里没出现过的模型
            _result(
                "r-1",
                {"main-model": _totals(211_000, 5_100), "sub-model": _totals(2_000, 100)},
            ),
        )

        rows = _by_model(await harness.rows())
        main, sub = rows["main-model"], rows["sub-model"]
        assert (main.input_tokens, main.output_tokens) == (211_000, 5_100)
        # 差额按一次请求计价，总输入 21 万落高档
        assert main.cost_amount == pytest.approx((1_000 * 2 + 100 * 10 + 210_000 * 4 + 5_000 * 15) / 1e6)
        assert (sub.input_tokens, sub.output_tokens) == (2_000, 100)
        assert sub.cost_amount == pytest.approx((2_000 * 1 + 100 * 5) / 1e6)

    async def test_a_model_without_an_entry_keeps_its_tokens_and_is_unpriced(self, harness: Any) -> None:
        await harness.new_session(
            "写分镜",
            _request("msg-1", model="mystery-model", input_tokens=1_000, cache_read=500, output=100),
            _result("r-1", {"mystery-model": _totals(1_000, 100, cache_read=500)}),
        )

        (row,) = await harness.rows()
        assert (row.model, row.input_tokens, row.output_tokens, row.cache_read_tokens) == (
            "mystery-model",
            1_500,
            100,
            500,
        )
        assert (row.cost_amount, row.unpriced_reason, row.status) == (None, "no_entry", "success")


class TestDifferentialBaseline:
    @pytest.mark.usefixtures("acme_catalog")
    async def test_each_turn_bills_its_increment_and_the_first_turn_after_resume_has_no_baseline(
        self, harness: Any
    ) -> None:
        await harness.new_session(
            "第一轮",
            _request("msg-1", input_tokens=1_000, output=100),
            _result("r-1", {"main-model": _totals(1_000, 100)}),
        )
        # 第二轮另有 500 输入出现在流外
        await harness.turn(
            "第二轮",
            _request("msg-2", input_tokens=2_000, output=200),
            _result("r-2", {"main-model": _totals(3_500, 300)}),
        )
        # 恢复会话：CLI 带着此前的累计值，另有 400 输入出现在流外，这一轮不计差额
        await harness.resume(
            "第三轮",
            _request("msg-3", input_tokens=1_000, output=100),
            _result("r-3", {"main-model": _totals(4_900, 400)}),
        )
        await harness.turn(
            "第四轮",
            _request("msg-4", input_tokens=1_000, output=100),
            _result("r-4", {"main-model": _totals(6_500, 500)}),
        )

        rows = await harness.rows()
        assert [(row.prompt, row.input_tokens, row.output_tokens) for row in rows] == [
            ("第一轮", 1_000, 100),
            ("第二轮", 2_500, 200),
            ("第三轮", 1_000, 100),
            ("第四轮", 1_600, 100),
        ]
        assert [row.cost_amount for row in rows] == [
            pytest.approx(3_000 / 1e6),
            pytest.approx(7_000 / 1e6),
            pytest.approx(3_000 / 1e6),
            pytest.approx(4_200 / 1e6),
        ]

    @pytest.mark.usefixtures("acme_catalog")
    async def test_a_conversation_reset_starts_the_baseline_from_zero(self, harness: Any) -> None:
        await harness.new_session(
            "第一轮",
            _request("msg-1", input_tokens=1_000, output=100),
            _result("r-1", {"main-model": _totals(1_000, 100)}),
        )
        reset = {"type": "conversation_reset", "new_conversation_id": "conv-2", "uuid": "reset-1", "session_id": SDK_ID}
        harness.client.push_frame(reset)
        # 重置后累计值从零开始：流内 1500 输入，流外另有 300
        await harness.turn(
            "第二轮",
            _request("msg-2", input_tokens=1_500, output=150),
            _result("r-2", {"main-model": _totals(1_800, 150)}),
        )

        rows = await harness.rows()
        assert [(row.input_tokens, row.output_tokens) for row in rows] == [(1_000, 100), (1_800, 150)]
        assert rows[1].cost_amount == pytest.approx((1_800 * 2 + 150 * 10) / 1e6)

    @pytest.mark.usefixtures("acme_catalog")
    async def test_an_interrupted_turn_is_cancelled_and_keeps_its_cost(self, harness: Any) -> None:
        await harness.new_session(
            "第一轮",
            _request("msg-1", input_tokens=1_000, output=100),
            _result("r-1", {"main-model": _totals(1_000, 100)}),
        )
        assert harness.client is not None
        await harness.service.send_or_create("demo", "第二轮", session_id=SDK_ID)
        await harness.manager.interrupt_session(SDK_ID)
        # 中断时这次请求只收到占位的 output；CLI 按已收到的部分记进累计值
        for frame in (
            _started(),
            _request("msg-2", input_tokens=1_000, output=1),
            _result(
                "r-2",
                {"main-model": _totals(2_000, 140)},
                subtype="error_during_execution",
                is_error=True,
                terminal_reason="aborted_streaming",
            ),
            _idle(),
        ):
            harness.client.push_frame(frame)
        await harness.wait_settled()

        rows = await harness.rows()
        assert rows[-1].status == "cancelled"
        assert (rows[-1].input_tokens, rows[-1].output_tokens) == (1_000, 40)
        assert rows[-1].cost_amount == pytest.approx((1_000 * 2 + 40 * 10) / 1e6)

    @pytest.mark.usefixtures("acme_catalog")
    async def test_a_retried_stream_bills_both_attempts(self, harness: Any) -> None:
        await harness.new_session(
            "写分镜",
            # 流式中途失败的那次尝试只出现过开头一帧；重试是另一个请求。CLI 把失败尝试已收到的 30 个输出也记进累计值
            _request("msg-1", input_tokens=1_000, output=1),
            _request("msg-2", input_tokens=1_000, output=300),
            _result("r-1", {"main-model": _totals(2_000, 330)}),
        )

        (row,) = await harness.rows()
        assert (row.input_tokens, row.output_tokens) == (2_000, 330)
        assert row.cost_amount == pytest.approx((2_000 * 2 + 330 * 10) / 1e6)


class TestSessionCredential:
    @pytest.mark.usefixtures("acme_catalog")
    async def test_turns_are_billed_to_the_credential_injected_when_the_session_connected(self, harness: Any) -> None:
        first = harness.credential_id
        second = await harness.add_credential("网关 B")
        await harness.set_multiplier(second, 0.5)
        await harness.new_session(
            "第一轮",
            _request("msg-1", input_tokens=1_000, output=100),
            _result("r-1", {"main-model": _totals(1_000, 100)}),
        )
        await harness.activate(second)
        await harness.turn(
            "第二轮",
            _request("msg-2", input_tokens=1_000, output=100),
            _result("r-2", {"main-model": _totals(2_000, 200)}),
        )
        # 恢复会话的冷启动重新注入环境变量，之后的轮次按新的生效凭证计价
        await harness.resume(
            "第三轮",
            _request("msg-3", input_tokens=1_000, output=100),
            _result("r-3", {"main-model": _totals(3_000, 300)}),
        )

        rows = await harness.rows()
        assert [row.provider for row in rows] == [f"agent-{first}", f"agent-{first}", f"agent-{second}"]
        # 倍率同样取会话连接时注入的凭证
        cost = (1_000 * 2 + 100 * 10) / 1e6
        assert [row.cost_amount for row in rows] == pytest.approx([cost, cost, cost * 0.5])

    @pytest.mark.usefixtures("acme_catalog")
    async def test_changing_the_multiplier_mid_session_keeps_the_rate_taken_when_the_session_connected(
        self, harness: Any
    ) -> None:
        await harness.new_session(
            "第一轮",
            _request("msg-1", input_tokens=1_000, output=100),
            _result("r-1", {"main-model": _totals(1_000, 100)}),
        )
        await harness.set_multiplier(harness.credential_id, 0.5)
        await harness.turn(
            "第二轮",
            _request("msg-2", input_tokens=1_000, output=100),
            _result("r-2", {"main-model": _totals(2_000, 200)}),
        )
        # 恢复会话的冷启动重新取下凭证，之后的轮次才按新倍率计价
        await harness.resume(
            "第三轮",
            _request("msg-3", input_tokens=1_000, output=100),
            _result("r-3", {"main-model": _totals(3_000, 300)}),
        )

        cost = (1_000 * 2 + 100 * 10) / 1e6
        assert [row.cost_amount for row in await harness.rows()] == pytest.approx([cost, cost, cost * 0.5])

    @pytest.mark.usefixtures("acme_catalog")
    async def test_a_credential_with_multiplier_zero_records_zero_without_looking_up_prices(self, harness: Any) -> None:
        await harness.set_multiplier(harness.credential_id, 0)
        await harness.new_session(
            "写分镜",
            _request("msg-1", input_tokens=1_000, output=100),
            _request("msg-2", model="mystery-model", parent_tool_use_id="tu-1", input_tokens=1_000, output=100),
            _result("r-1", {"main-model": _totals(1_000, 100), "mystery-model": _totals(1_000, 100)}),
        )

        rows = _by_model(await harness.rows())
        # 对应不到条目的模型同样记 0，不记为未定价；token 照常记下
        assert {model: (row.cost_amount, row.unpriced_reason, row.input_tokens) for model, row in rows.items()} == {
            "main-model": (0, None, 1_000),
            "mystery-model": (0, None, 1_000),
        }

    async def test_claude_models_of_the_official_preset_are_priced_from_the_catalog(self, harness: Any) -> None:
        await harness.new_session(
            "写分镜",
            _request(
                "msg-1",
                model="claude-opus-5-5",
                input_tokens=1_000,
                cache_read=2_000,
                write_5m=1_000,
                write_1h=1_000,
                output=500,
            ),
            # 响应体里的模型名带日期后缀，modelUsage 的键是请求用的别名：二者对应到同一条目，记成一行
            _request(
                "msg-2", model="claude-haiku-4-5-20251001", parent_tool_use_id="tu-1", input_tokens=1_000, output=100
            ),
            _result(
                "r-1",
                {
                    "claude-opus-5-5": _totals(1_000, 500, cache_read=2_000, cache_write=2_000),
                    "claude-haiku-4-5": _totals(1_000, 100),
                },
            ),
        )

        rows = _by_model(await harness.rows())
        assert set(rows) == {"claude-opus-5-5", "claude-haiku-4-5-20251001"}
        opus = rows["claude-opus-5-5"]
        assert (opus.cost_amount, opus.currency) == (
            pytest.approx((1_000 * 4 + 2_000 * 0.2 + 1_000 * 5 + 1_000 * 8 + 500 * 20) / 1e6),
            "USD",
        )
        assert rows["claude-haiku-4-5-20251001"].cost_amount == pytest.approx((1_000 * 1 + 100 * 5) / 1e6)


class TestUsageRecordsView:
    """记账写出的 Agent 行经使用记录接口读出：写侧与会话视图、汇总之间只靠 ``purpose`` 与 ``session_id`` 衔接。"""

    @pytest.mark.usefixtures("acme_catalog")
    async def test_billed_turns_read_back_as_one_session_with_priced_total_and_unpriced_flag(
        self, harness: Any, usage_app: Callable[[async_sessionmaker[AsyncSession]], FastAPI]
    ) -> None:
        await harness.new_session(
            "写分镜",
            _request("msg-1", input_tokens=1_000, cache_read=2_000, write_5m=300, write_1h=200, output=100),
            _request("msg-2", model="mystery-model", parent_tool_use_id="tu-1", input_tokens=400, output=50),
            _result("r-1", {"main-model": _totals(1_000, 100, cache_read=2_000, cache_write=500)}),
        )
        await harness.turn(
            "再改一版",
            _request("msg-3", input_tokens=500, output=20),
            _result("r-2", {"main-model": _totals(1_500, 120, cache_read=2_000, cache_write=500)}),
        )

        transport = httpx.ASGITransport(app=usage_app(harness.factory))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            generation = (await client.get("/api/v1/usage/records")).json()
            sessions = (await client.get("/api/v1/usage/sessions")).json()
            detail = (await client.get(f"/api/v1/usage/sessions/{SDK_ID}")).json()
            summary = (await client.get("/api/v1/usage/summary")).json()
            agent_options = (await client.get("/api/v1/usage/summary", params={"view": "agent"})).json()

        assert generation["items"] == []
        (row,) = sessions["items"]
        main_turn_1 = (1_000 * 2 + 2_000 * 0.2 + 300 * 2.5 + 200 * 4 + 100 * 10) / 1e6
        main_turn_2 = (500 * 2 + 20 * 10) / 1e6
        assert (row["session_id"], row["session_deleted"], row["project_name"]) == (SDK_ID, False, "demo")
        assert row["models"] == ["main-model", "mystery-model"]
        assert (row["calls"], row["unpriced_calls"]) == (3, 1)
        # 会话用量的输入是总输入：未缓存 + 缓存读 + 两档缓存写
        assert (row["input_tokens"], row["output_tokens"]) == (3_500 + 400 + 500, 100 + 50 + 20)
        assert row["cost"] == {"USD": pytest.approx(main_turn_1 + main_turn_2)}

        items = detail["items"]
        assert [(item["prompt"], item["model"]) for item in items] == [
            ("写分镜", "main-model"),
            ("写分镜", "mystery-model"),
            ("再改一版", "main-model"),
        ]
        first = items[0]
        assert (first["input_tokens"], first["cache_read_tokens"]) == (3_500, 2_000)
        assert (first["cache_write_5m_tokens"], first["cache_write_1h_tokens"]) == (300, 200)
        assert (items[1]["cost_amount"], items[1]["unpriced_reason"]) == (None, "no_entry")

        # 合计含 Agent，构成里 Agent 会话合成一项；Agent 视图的供应商选项显示凭证名
        assert summary["kpi"]["calls"] == 3
        (agent_item,) = summary["breakdown"]["provider"]["rows"]
        assert (agent_item["agent"], agent_item["calls"]) == (True, 3)
        assert [(option["provider"], option["label"]) for option in agent_options["filter_options"]["providers"]] == [
            (f"agent-{harness.credential_id}", "网关 A")
        ]
