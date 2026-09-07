"""`GET /usage/summary` 的路由缝：KPI 口径、时区切天、构成表、需要关注与筛选候选值。"""

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import update

from lib.db.models.api_call import ApiCall
from lib.db.repositories.usage_repo import SettlementInput, UsageRepository
from lib.providers import CallStatus
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import usage
from tests.auth_deps import AUTH_DEPENDENCIES

DAY = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


async def seed_call(
    session,
    *,
    started_at: datetime = DAY,
    project_name: str = "alpha",
    media_type: str = "image",
    provider: str = "gemini",
    model: str = "flash",
    status: CallStatus = CallStatus.SUCCESS,
    cost: float = 0.0,
    currency: str = "USD",
    segment_id: str | None = None,
    error_code: str | None = None,
) -> int:
    """经仓储落一条真实调用行；``started_at`` 与 ``error_code`` 事后按行更新。"""
    repo = UsageRepository(session)
    call_id = await repo.start_call(
        project_name=project_name,
        call_type=media_type,
        model=model,
        provider=provider,
        segment_id=segment_id,
    )
    if status is not CallStatus.PENDING:
        await repo.finish_call(
            call_id,
            status=status,
            settlement=SettlementInput(cost_amount=cost, currency=currency),
        )
    await session.execute(
        update(ApiCall).where(ApiCall.id == call_id).values(started_at=started_at, error_code=error_code)
    )
    await session.commit()
    return call_id


@pytest.fixture
def summary_client(db_factory, monkeypatch) -> TestClient:
    monkeypatch.setattr(usage, "async_session_factory", db_factory)
    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(usage.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return TestClient(app)


class TestSummaryKpi:
    async def test_kpi_counts_terminal_calls_and_skips_pending(self, summary_client, db_factory):
        """调用次数 = 成功 + 失败 + 已取消；pending 不进聚合，失败行的费用照样计入。"""
        async with db_factory() as session:
            await seed_call(session, status=CallStatus.SUCCESS, cost=1.0)
            await seed_call(session, status=CallStatus.FAILED, cost=0.5)
            await seed_call(session, status=CallStatus.CANCELLED)
            await seed_call(session, status=CallStatus.PENDING)

        kpi = summary_client.get("/api/v1/usage/summary").json()["kpi"]
        assert kpi == {
            "calls": 3,
            "success": 1,
            "failed": 1,
            "cancelled": 1,
            "success_rate": 0.5,
            "cost": {"USD": 1.5},
        }

    async def test_success_rate_is_null_without_decided_calls(self, summary_client, db_factory):
        """只有取消时成功率分母为 0，返回 null 而不是 0。"""
        async with db_factory() as session:
            await seed_call(session, status=CallStatus.CANCELLED)

        kpi = summary_client.get("/api/v1/usage/summary").json()["kpi"]
        assert kpi["calls"] == 1
        assert kpi["success_rate"] is None

    async def test_status_query_is_ignored(self, summary_client, db_factory):
        """记录表的状态筛选带上来时被忽略：汇总口径不按状态再切。"""
        async with db_factory() as session:
            await seed_call(session, status=CallStatus.SUCCESS)
            await seed_call(session, status=CallStatus.FAILED)

        assert summary_client.get("/api/v1/usage/summary?status=failed").json() == (
            summary_client.get("/api/v1/usage/summary").json()
        )

    async def test_filters_narrow_the_aggregate(self, summary_client, db_factory):
        """项目 / 供应商 / 模型 / 媒体类型四个筛选都作用于聚合。"""
        async with db_factory() as session:
            await seed_call(session, project_name="alpha", provider="ark", model="seed", media_type="text")
            await seed_call(session, project_name="beta", provider="ark", model="seed", media_type="text")
            await seed_call(session, project_name="alpha", provider="gemini", model="flash", media_type="image")

        body = summary_client.get("/api/v1/usage/summary?project_name=alpha&provider=ark&model=seed&media_type=text")
        assert body.json()["kpi"]["calls"] == 1


class TestSummaryDailyBuckets:
    @pytest.mark.parametrize(
        ("tz", "expected"),
        [("UTC", "2026-03-01"), ("Asia/Shanghai", "2026-03-02"), ("America/New_York", "2026-03-01")],
    )
    async def test_local_day_follows_timezone(self, summary_client, db_factory, tz, expected):
        """跨 UTC 日界的调用按 tz 落在本地日。"""
        async with db_factory() as session:
            await seed_call(session, started_at=datetime(2026, 3, 1, 23, 30, tzinfo=UTC))

        body = summary_client.get(f"/api/v1/usage/summary?tz={tz}").json()
        assert [bucket["date"] for bucket in body["daily"]] == [expected]
        assert body["range"] == {"since": expected, "until": expected}

    async def test_gaps_are_filled_with_zero_buckets(self, summary_client, db_factory):
        """首尾之间没有调用的日子补零桶，趋势不跳日。"""
        async with db_factory() as session:
            await seed_call(session, started_at=datetime(2026, 3, 1, 1, 0, tzinfo=UTC))
            await seed_call(session, started_at=datetime(2026, 3, 4, 1, 0, tzinfo=UTC))

        body = summary_client.get("/api/v1/usage/summary").json()
        assert [bucket["date"] for bucket in body["daily"]] == [
            "2026-03-01",
            "2026-03-02",
            "2026-03-03",
            "2026-03-04",
        ]
        assert [bucket["success"] for bucket in body["daily"]] == [1, 0, 0, 1]
        assert body["daily"][1]["cost_by_media_type"] == {"image": 0.0, "video": 0.0, "text": 0.0, "audio": 0.0}

    async def test_range_is_null_without_records(self, summary_client):
        """没有可聚合的记录时 range 为 null、趋势为空。"""
        body = summary_client.get("/api/v1/usage/summary").json()
        assert body["range"] is None
        assert body["daily"] == []
        assert body["kpi"]["calls"] == 0

    async def test_explicit_range_bounds_the_buckets(self, summary_client, db_factory):
        """传 since / until 时按半开区间取行并从 since 起补桶。"""
        async with db_factory() as session:
            await seed_call(session, started_at=datetime(2026, 3, 1, 1, 0, tzinfo=UTC))
            await seed_call(session, started_at=datetime(2026, 3, 5, 1, 0, tzinfo=UTC))

        body = summary_client.get("/api/v1/usage/summary?since=2026-03-03T00:00:00Z&until=2026-03-06T00:00:00Z").json()
        assert body["kpi"]["calls"] == 1
        assert body["range"] == {"since": "2026-03-03", "until": "2026-03-05"}

    async def test_offset_bounds_are_compared_as_utc_instants(self, summary_client, db_factory):
        async with db_factory() as session:
            await seed_call(session, started_at=datetime(2026, 3, 1, 16, 30, tzinfo=UTC))

        body = summary_client.get(
            "/api/v1/usage/summary",
            params={
                "since": "2026-03-02T00:00:00+08:00",
                "until": "2026-03-02T01:00:00+08:00",
                "tz": "Asia/Shanghai",
            },
        ).json()
        assert body["kpi"]["calls"] == 1
        assert body["range"] == {"since": "2026-03-02", "until": "2026-03-02"}

    async def test_daily_cost_splits_by_media_type_in_primary_currency(self, summary_client, db_factory):
        """日桶费用按媒体类型分列，只统计主币种，不做汇率折算。"""
        async with db_factory() as session:
            await seed_call(session, media_type="image", cost=2.0, currency="USD")
            await seed_call(session, media_type="video", cost=1.0, currency="USD")
            await seed_call(session, media_type="text", cost=9.0, currency="CNY")

        body = summary_client.get("/api/v1/usage/summary").json()
        assert body["primary_currency"] == "CNY"
        assert body["daily"][0]["cost_by_media_type"] == {
            "image": 0.0,
            "video": 0.0,
            "text": 9.0,
            "audio": 0.0,
        }

    async def test_unknown_timezone_is_rejected(self, summary_client):
        response = summary_client.get("/api/v1/usage/summary?tz=Mars/Olympus")
        assert response.status_code == 422
        assert response.json()["detail"] == "未知的 IANA 时区名，请检查 tz 参数"

    async def test_unknown_timezone_message_follows_request_locale(self, summary_client):
        response = summary_client.get("/api/v1/usage/summary?tz=Mars/Olympus", headers={"Accept-Language": "en"})
        assert response.status_code == 422
        assert response.json()["detail"] == "Unknown IANA time zone name; check the tz parameter"

    async def test_oversized_window_is_rejected(self, summary_client, db_factory):
        """since / until 展开的日桶超过上限时 422，而不是同步铺出几百万个零桶。"""
        async with db_factory() as session:
            await seed_call(session)

        response = summary_client.get("/api/v1/usage/summary?since=1900-01-01T00:00:00Z&until=2100-01-01T00:00:00Z")
        assert response.status_code == 422
        assert response.json()["detail"] == "统计时间范围过大，请缩小起止时刻后重试"

    def test_unrepresentable_instant_is_rejected(self, summary_client):
        """落在可表示区间之外的时刻 422：半开右端减一微秒不能在 datetime 的下界上溢出。"""
        response = summary_client.get("/api/v1/usage/summary?until=0001-01-01T00:00:00Z")
        assert response.status_code == 422
        assert response.json()["detail"] == "起止时刻超出可表示的时间范围，请检查后重试"


class TestSummaryCurrency:
    async def test_primary_currency_is_the_largest_and_cost_stays_split(self, summary_client, db_factory):
        async with db_factory() as session:
            await seed_call(session, cost=1.0, currency="USD")
            await seed_call(session, cost=3.0, currency="CNY")

        body = summary_client.get("/api/v1/usage/summary").json()
        assert body["primary_currency"] == "CNY"
        assert body["kpi"]["cost"] == {"CNY": 3.0, "USD": 1.0}

    async def test_ties_prefer_usd(self, summary_client, db_factory):
        async with db_factory() as session:
            await seed_call(session, cost=2.0, currency="USD")
            await seed_call(session, cost=2.0, currency="CNY")

        assert summary_client.get("/api/v1/usage/summary").json()["primary_currency"] == "USD"

    async def test_ties_without_usd_fall_back_to_alphabetical(self, summary_client, db_factory):
        async with db_factory() as session:
            await seed_call(session, cost=2.0, currency="EUR")
            await seed_call(session, cost=2.0, currency="CNY")

        assert summary_client.get("/api/v1/usage/summary").json()["primary_currency"] == "CNY"

    async def test_near_equal_amounts_are_not_treated_as_a_tie(self, summary_client, db_factory):
        async with db_factory() as session:
            await seed_call(session, cost=1.0000004, currency="USD")
            await seed_call(session, cost=1.00000049, currency="CNY")

        assert summary_client.get("/api/v1/usage/summary").json()["primary_currency"] == "CNY"


class TestSummaryBreakdown:
    async def test_three_dimensions_with_model_keyed_by_provider_and_model(self, summary_client, db_factory):
        """同名模型分属两个供应商时是两行；项目与供应商各自成维。"""
        async with db_factory() as session:
            await seed_call(session, project_name="alpha", provider="ark", model="shared")
            await seed_call(session, project_name="alpha", provider="ark", model="shared")
            await seed_call(session, project_name="beta", provider="gemini", model="shared")

        breakdown = summary_client.get("/api/v1/usage/summary").json()["breakdown"]
        assert [(row["project_name"], row["calls"]) for row in breakdown["project"]["rows"]] == [
            ("alpha", 2),
            ("beta", 1),
        ]
        assert [(row["provider"], row["calls"]) for row in breakdown["provider"]["rows"]] == [("ark", 2), ("gemini", 1)]
        assert [(row["provider"], row["model"], row["calls"]) for row in breakdown["model"]["rows"]] == [
            ("ark", "shared", 2),
            ("gemini", "shared", 1),
        ]
        assert breakdown["project"]["other"] is None

    async def test_rows_beyond_fifty_are_merged_into_other(self, summary_client, db_factory):
        """构成表每维最多 50 行，余量并入列表外的 other。"""
        async with db_factory() as session:
            for index in range(52):
                await seed_call(session, project_name=f"p{index:03d}", cost=1.0)

        breakdown = summary_client.get("/api/v1/usage/summary").json()["breakdown"]["project"]
        assert len(breakdown["rows"]) == 50
        assert breakdown["other"] == {
            "groups": 2,
            "calls": 2,
            "success": 2,
            "failed": 0,
            "cancelled": 0,
            "success_rate": 1.0,
            "cost": {"USD": 2.0},
        }


class TestSummaryFilterOptions:
    async def test_options_come_from_the_whole_table(self, summary_client, db_factory):
        """筛选候选值取全表 distinct，不随本次筛选变化。"""
        async with db_factory() as session:
            await seed_call(session, project_name="alpha", provider="ark", model="seed")
            await seed_call(session, project_name="beta", provider="gemini", model="flash")

        options = summary_client.get("/api/v1/usage/summary?project_name=alpha").json()["filter_options"]
        assert options["projects"] == ["alpha", "beta"]
        assert options["models"] == [
            {"provider": "ark", "model": "seed"},
            {"provider": "gemini", "model": "flash"},
        ]

    @pytest.mark.parametrize(("accept_language", "expected"), [("zh", "火山方舟"), ("en", "Volcengine Ark")])
    async def test_provider_label_follows_locale_and_falls_back_to_id(
        self, summary_client, db_factory, accept_language, expected
    ):
        """供应商显示名从目录解析并跟随请求语言；目录里没有的回退 id。"""
        async with db_factory() as session:
            await seed_call(session, provider="ark", model="seed")
            await seed_call(session, provider="not-in-registry", model="whatever")

        options = summary_client.get("/api/v1/usage/summary", headers={"accept-language": accept_language}).json()[
            "filter_options"
        ]
        labels = {item["provider"]: item["label"] for item in options["providers"]}
        assert labels == {"ark": expected, "not-in-registry": "not-in-registry"}


class TestSummaryAttention:
    async def test_provider_trigger_suppresses_its_models(self, summary_client, db_factory):
        """供应商级失败率偏高时只报供应商，不再重复报它下面的模型。"""
        async with db_factory() as session:
            for _ in range(5):
                await seed_call(session, provider="flaky", model="only", status=CallStatus.FAILED)
            for _ in range(15):
                await seed_call(session, provider="steady", model="fine", status=CallStatus.SUCCESS)

        attention = summary_client.get("/api/v1/usage/summary").json()["attention"]
        assert [(item["provider"], item["model"]) for item in attention] == [("flaky", None)]
        assert attention[0]["failed"] == 5
        assert attention[0]["failure_rate"] == 1.0

    async def test_model_is_reported_when_its_provider_is_not(self, summary_client, db_factory):
        """供应商整体不越线、其下单个模型越线时报模型。"""
        async with db_factory() as session:
            for _ in range(15):
                await seed_call(session, provider="mixed", model="good", status=CallStatus.SUCCESS)
            for _ in range(5):
                await seed_call(session, provider="mixed", model="bad", status=CallStatus.FAILED)
            for _ in range(15):
                await seed_call(session, provider="steady", model="fine", status=CallStatus.SUCCESS)

        attention = summary_client.get("/api/v1/usage/summary").json()["attention"]
        assert [(item["provider"], item["model"]) for item in attention] == [("mixed", "bad")]

    async def test_single_failure_never_triggers(self, summary_client, db_factory):
        """只失败一次不报：Wilson 下界再高也过不了 failed ≥ 2 这道门槛。"""
        async with db_factory() as session:
            await seed_call(session, provider="flaky", model="only", status=CallStatus.FAILED)
            for _ in range(30):
                await seed_call(session, provider="steady", model="fine", status=CallStatus.SUCCESS)

        assert summary_client.get("/api/v1/usage/summary").json()["attention"] == []

    async def test_consecutive_failures_report_the_trailing_run(self, summary_client, db_factory):
        """末尾连续失败 ≥ 2 才报，取消不打断也不计数，之后有成功就不报。"""
        base = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)

        async def failure_chain(session, segment_id, statuses):
            for offset, status in enumerate(statuses):
                await seed_call(
                    session,
                    started_at=base.replace(hour=offset),
                    status=status,
                    segment_id=segment_id,
                    error_code="provider_timeout" if status is CallStatus.FAILED else None,
                )

        async with db_factory() as session:
            await failure_chain(session, "seg-tail", [CallStatus.FAILED, CallStatus.FAILED])
            await failure_chain(session, "seg-recovered", [CallStatus.FAILED, CallStatus.FAILED, CallStatus.SUCCESS])
            await failure_chain(session, "seg-cancelled", [CallStatus.FAILED, CallStatus.CANCELLED, CallStatus.FAILED])
            await failure_chain(session, "seg-single", [CallStatus.SUCCESS, CallStatus.FAILED])
            await failure_chain(session, None, [CallStatus.FAILED, CallStatus.FAILED])

        attention = summary_client.get("/api/v1/usage/summary").json()["attention"]
        runs = {item["segment_id"]: item for item in attention if item["type"] == "consecutive_failures"}
        assert set(runs) == {"seg-tail", "seg-cancelled"}
        assert runs["seg-cancelled"]["count"] == 2
        assert runs["seg-cancelled"]["first_failed_at"] == "2026-03-01T00:00:00+00:00"
        assert runs["seg-cancelled"]["last_failed_at"] == "2026-03-01T02:00:00+00:00"
        assert runs["seg-cancelled"]["last_error_code"] == "provider_timeout"
        assert runs["seg-tail"]["media_type"] == "image"
        assert runs["seg-tail"]["project_name"] == "alpha"
