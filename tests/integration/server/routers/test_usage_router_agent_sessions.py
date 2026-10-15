"""使用记录的「生成调用」与「Agent 会话」两个视图：记录过滤、会话聚合、明细、汇总与供应商显示名。"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import null

from lib.backends.providers import CallPurpose, CallStatus
from lib.db.models.agent_credential import AgentAnthropicCredential
from lib.db.models.api_call import ApiCall
from lib.db.models.session import AgentSession

T0 = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)

# 夹具里唯一的 Agent 凭证是库里的第一条；另一个供应商 id 指向一条已删除的凭证。
LIVE_PROVIDER = "agent-1"
DELETED_PROVIDER = "agent-101"


def generation_call(**overrides) -> ApiCall:
    fields: dict = {
        "project_name": "demo",
        "call_type": "image",
        "model": "imagen-4",
        "provider": "gemini-aistudio",
        "status": CallStatus.SUCCESS,
        "purpose": CallPurpose.GENERATION_TASK,
        "started_at": T0,
        "cost_amount": 1.0,
        "currency": "USD",
    }
    fields.update(overrides)
    return ApiCall(**fields)


def agent_call(session_id: str, minutes: int, model: str, provider: str, **overrides) -> ApiCall:
    """一轮里某个模型的一条 Agent 记录；同一轮的几条记录共用 session_id、提示词与写入时间。"""
    fields: dict = {
        "project_name": "demo",
        "call_type": "text",
        "model": model,
        "provider": provider,
        "status": CallStatus.SUCCESS,
        "purpose": CallPurpose.ASSISTANT_SESSION,
        "session_id": session_id,
        "prompt": f"第 {minutes} 分钟的提问",
        "started_at": T0 + timedelta(minutes=minutes),
        "cost_amount": 0.5,
        "currency": "USD",
        "input_tokens": 1000,
        "output_tokens": 200,
        "cache_read_tokens": 600,
        "cache_write_5m_tokens": 100,
        "cache_write_1h_tokens": 50,
    }
    fields.update(overrides)
    return ApiCall(**fields)


@pytest.fixture
async def agent_client(db_factory, usage_app):
    """同一时段内：生成调用、两个 Agent 会话（其中一个含未定价记录）、凭证已删除的会话、历史 anthropic 会话。"""
    async with db_factory() as session:
        credential = AgentAnthropicCredential(
            preset_id="deepseek", display_name="我的 DeepSeek", base_url="https://x.test", api_key="sk-x"
        )
        session.add(credential)
        await session.flush()
        assert f"agent-{credential.id}" == LIVE_PROVIDER
        live = LIVE_PROVIDER
        deleted = DELETED_PROVIDER

        session.add_all(
            [
                AgentSession(id="a", sdk_session_id="sess-a", project_name="demo", title="分镜讨论"),
                AgentSession(id="b", sdk_session_id="sess-b", project_name="demo2", title=""),
                AgentSession(id="o", sdk_session_id="sess-old", project_name="demo", title="旧会话"),
            ]
        )
        session.add_all(
            [
                generation_call(segment_id="S1"),
                generation_call(segment_id="S2", status=CallStatus.FAILED, started_at=T0 + timedelta(minutes=1)),
                # 会话 a：第 1 轮两个模型（同一时刻），第 5 轮一个
                agent_call("sess-a", 1, "deepseek-chat", live, cost_amount=0.5),
                agent_call(
                    "sess-a", 1, "deepseek-reasoner", live, cost_amount=0.25, input_tokens=400, output_tokens=100
                ),
                agent_call("sess-a", 5, "deepseek-chat", live, cost_amount=1.0, prompt="x" * 400),
                # 会话 b：含一条未定价记录；另一条 CNY
                agent_call(
                    "sess-b", 10, "glm-5", live, project_name="demo2", cost_amount=null(), unpriced_reason="no_entry"
                ),
                agent_call("sess-b", 11, "deepseek-chat", live, project_name="demo2", cost_amount=2.0, currency="CNY"),
                # 凭证已删除、会话元数据也已删除
                agent_call("sess-gone", 20, "kimi-k2", deleted, cost_amount=0.0),
                # 历史记录：以 anthropic 落库
                agent_call("sess-old", 30, "claude-sonnet-4-5", "anthropic", cost_amount=0.3),
                # 失败的 Agent 轮次：不进「需要关注」
                agent_call("sess-a", 6, "deepseek-chat", live, status=CallStatus.FAILED),
                agent_call("sess-a", 7, "deepseek-chat", live, status=CallStatus.FAILED),
                agent_call("sess-a", 8, "deepseek-chat", live, status=CallStatus.FAILED),
            ]
        )
        await session.commit()

    return TestClient(usage_app(db_factory))


class TestViews:
    def test_generation_view_is_default_and_has_no_agent_records(self, agent_client):
        items = agent_client.get("/api/v1/usage/records").json()["items"]

        assert {item["purpose"] for item in items} == {CallPurpose.GENERATION_TASK}

    def test_agent_view_lists_only_agent_records(self, agent_client):
        page = agent_client.get("/api/v1/usage/records", params={"view": "agent"}).json()

        assert page["total"] == 10
        assert {item["purpose"] for item in page["items"]} == {CallPurpose.ASSISTANT_SESSION}


class TestSessionList:
    def test_one_row_per_session_newest_turn_first(self, agent_client):
        page = agent_client.get("/api/v1/usage/sessions").json()

        assert page["total"] == 4
        assert [item["session_id"] for item in page["items"]] == ["sess-old", "sess-gone", "sess-b", "sess-a"]

    def test_row_carries_title_models_tokens_and_priced_cost(self, agent_client):
        sessions = {item["session_id"]: item for item in agent_client.get("/api/v1/usage/sessions").json()["items"]}

        a = sessions["sess-a"]
        assert a["title"] == "分镜讨论"
        assert a["session_deleted"] is False
        assert a["project_name"] == "demo"
        assert a["models"] == ["deepseek-chat", "deepseek-reasoner"]
        assert a["calls"] == 6
        assert a["input_tokens"] == 1000 + 400 + 1000 + 3 * 1000
        assert a["output_tokens"] == 200 + 100 + 200 + 3 * 200
        assert a["cost"] == {"USD": pytest.approx(0.5 + 0.25 + 1.0 + 3 * 0.5)}
        assert a["unpriced_calls"] == 0
        assert a["first_started_at"] == "2026-03-01T12:01:00+00:00"
        assert a["last_started_at"] == "2026-03-01T12:08:00+00:00"

    def test_session_with_unpriced_record_is_flagged_and_excludes_it_from_cost(self, agent_client):
        b = next(
            item
            for item in agent_client.get("/api/v1/usage/sessions").json()["items"]
            if item["session_id"] == "sess-b"
        )

        assert b["unpriced_calls"] == 1
        assert b["cost"] == {"CNY": 2.0}
        assert b["models"] == ["glm-5", "deepseek-chat"]

    def test_deleted_session_has_no_title_and_zero_cost_is_kept(self, agent_client):
        gone = next(
            item
            for item in agent_client.get("/api/v1/usage/sessions").json()["items"]
            if item["session_id"] == "sess-gone"
        )

        assert gone["session_deleted"] is True
        assert gone["title"] is None
        assert gone["cost"] == {"USD": 0.0}

    def test_historical_anthropic_records_aggregate_by_session_too(self, agent_client):
        old = next(
            item
            for item in agent_client.get("/api/v1/usage/sessions").json()["items"]
            if item["session_id"] == "sess-old"
        )

        assert old["models"] == ["claude-sonnet-4-5"]
        assert old["title"] == "旧会话"

    def test_cursor_pagination_walks_every_session_once(self, agent_client):
        seen: list[str] = []
        cursor = None
        for _ in range(4):
            params = {"limit": 3, **({"cursor": cursor} if cursor else {})}
            page = agent_client.get("/api/v1/usage/sessions", params=params).json()
            seen.extend(item["session_id"] for item in page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break

        assert seen == ["sess-old", "sess-gone", "sess-b", "sess-a"]

    def test_shares_the_record_filters(self, agent_client):
        def ids(**params) -> list[str]:
            return [
                item["session_id"] for item in agent_client.get("/api/v1/usage/sessions", params=params).json()["items"]
            ]

        live = LIVE_PROVIDER
        assert ids(project_name="demo2") == ["sess-b"]
        assert ids(provider=live) == ["sess-b", "sess-a"]
        assert ids(model="kimi-k2") == ["sess-gone"]
        assert ids(since=(T0 + timedelta(minutes=15)).isoformat()) == ["sess-old", "sess-gone"]

    def test_invalid_cursor_is_rejected(self, agent_client):
        assert agent_client.get("/api/v1/usage/sessions", params={"cursor": "nope"}).status_code == 422


class TestSessionDetail:
    def test_records_in_time_order_with_cache_tokens_and_cost(self, agent_client):
        detail = agent_client.get("/api/v1/usage/sessions/sess-a").json()

        assert detail["session_id"] == "sess-a"
        items = detail["items"]
        assert [item["started_at"][11:16] for item in items] == ["12:01", "12:01", "12:05", "12:06", "12:07", "12:08"]
        assert items[0]["model"] == "deepseek-chat"
        assert items[0]["cache_read_tokens"] == 600
        assert items[0]["cache_write_5m_tokens"] == 100
        assert items[0]["cache_write_1h_tokens"] == 50
        assert items[0]["cost_amount"] == 0.5

    def test_long_prompt_is_cut_for_the_message_column(self, agent_client):
        items = agent_client.get("/api/v1/usage/sessions/sess-a").json()["items"]

        assert items[2]["prompt"].endswith("…")
        assert len(items[2]["prompt"]) < 400

    def test_unpriced_record_carries_reason(self, agent_client):
        items = agent_client.get("/api/v1/usage/sessions/sess-b").json()["items"]

        assert items[0]["cost_amount"] is None
        assert items[0]["unpriced_reason"] == "no_entry"

    def test_generation_records_never_form_a_session(self, agent_client):
        assert agent_client.get("/api/v1/usage/sessions/unknown").status_code == 404


class TestSummary:
    def test_totals_include_agent_and_breakdown_lists_agent_sessions_as_one_item(self, agent_client):
        summary = agent_client.get("/api/v1/usage/summary").json()

        assert summary["kpi"]["calls"] == 12
        rows = summary["breakdown"]["provider"]["rows"]
        agent_rows = [row for row in rows if row["agent"]]
        assert len(agent_rows) == 1
        assert agent_rows[0]["calls"] == 10
        assert {row["provider"] for row in rows if not row["agent"]} == {"gemini-aistudio"}
        assert all(row["agent"] for row in summary["breakdown"]["model"]["rows"] if row["model"] != "imagen-4")

    async def test_attention_counts_generation_calls_only(self, db_factory, usage_app):
        async with db_factory() as session:
            session.add_all(
                [
                    generation_call(segment_id=f"S{index}", started_at=T0 + timedelta(minutes=index))
                    for index in range(10)
                ]
                # 只有 Agent 在失败：算进去时 agent-1 的失败率与连续失败都会触发
                + [
                    agent_call(
                        "sess-x", 20 + index, "deepseek-chat", "agent-1", status=CallStatus.FAILED, segment_id="S1"
                    )
                    for index in range(6)
                ]
            )
            await session.commit()

        summary = TestClient(usage_app(db_factory)).get("/api/v1/usage/summary").json()

        assert summary["kpi"]["failed"] == 6
        assert summary["attention"] == []

    def test_filter_options_follow_the_view(self, agent_client):
        live, deleted = LIVE_PROVIDER, DELETED_PROVIDER

        generation = agent_client.get("/api/v1/usage/summary").json()["filter_options"]
        agent = agent_client.get("/api/v1/usage/summary", params={"view": "agent"}).json()["filter_options"]

        assert [option["provider"] for option in generation["providers"]] == ["gemini-aistudio"]
        labels = {option["provider"]: option["label"] for option in agent["providers"]}
        assert set(labels) == {live, deleted, "anthropic"}
        assert labels[live] == "我的 DeepSeek"
        assert labels["anthropic"] == "Anthropic"
        assert {option["model"] for option in agent["models"]} >= {"deepseek-chat", "kimi-k2"}
        assert "imagen-4" not in {option["model"] for option in agent["models"]}


class TestDeletedAgentProvider:
    @pytest.mark.parametrize(
        ("locale", "expected"),
        [("zh", "已删除的 Agent 供应商"), ("en", "Deleted Agent provider"), ("vi", "Nhà cung cấp Agent đã bị xóa")],
    )
    def test_deleted_credential_shows_the_generic_name_in_the_request_language(self, agent_client, locale, expected):
        deleted = DELETED_PROVIDER

        agent = agent_client.get(
            "/api/v1/usage/summary", params={"view": "agent"}, headers={"Accept-Language": locale}
        ).json()["filter_options"]

        assert {option["provider"]: option["label"] for option in agent["providers"]}[deleted] == expected
