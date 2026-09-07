import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.db.models.api_call import ApiCall
from lib.db.models.task import Task
from lib.db.repositories.usage_repo import SettlementInput, UsageRepository
from lib.providers import CallStatus
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import usage
from tests.auth_deps import AUTH_DEPENDENCIES


@pytest.fixture
async def usage_env(db_factory, monkeypatch):
    async with db_factory() as session:
        repo = UsageRepository(session)
        cid1 = await repo.start_call(project_name="demo", call_type="image", model="gemini-3.1-flash-image-preview")
        await repo.finish_call(cid1, status="success", settlement=SettlementInput())
        cid2 = await repo.start_call(project_name="demo", call_type="video", model="veo-3")
        await repo.finish_call(cid2, status="success", settlement=SettlementInput())
        cid3 = await repo.start_call(project_name="demo", call_type="video", model="veo-3")
        await repo.finish_call(cid3, status="success", settlement=SettlementInput())
        cid4 = await repo.start_call(project_name="demo2", call_type="image", model="gemini-3.1-flash-image-preview")
        await repo.finish_call(cid4, status="success", settlement=SettlementInput())
        cid5 = await repo.start_call(
            project_name="demo2", call_type="text", model="doubao-seed-2-0-pro-260215", provider="ark"
        )
        await repo.finish_call(cid5, status="success", settlement=SettlementInput())
        cid6 = await repo.start_call(
            project_name="demo2", call_type="text", model="some-model", provider="not-in-registry"
        )
        await repo.finish_call(cid6, status="success", settlement=SettlementInput())

    monkeypatch.setattr(usage, "async_session_factory", db_factory)

    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(usage.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)

    return TestClient(app)


class TestUsageRouter:
    def test_usage_endpoints(self, usage_env):
        client = usage_env
        stats = client.get("/api/v1/usage/stats?project_name=demo")
        assert stats.status_code == 200
        assert stats.json()["total_count"] == 3

        calls = client.get("/api/v1/usage/calls?page=1&page_size=10")
        assert calls.status_code == 200
        assert calls.json()["page"] == 1
        assert calls.json()["page_size"] == 10
        assert calls.json()["total"] == 6

        selected = client.get(f"/api/v1/usage/calls?call_id={calls.json()['items'][0]['id']}")
        assert selected.status_code == 200
        assert selected.json()["total"] == 1

        projects = client.get("/api/v1/usage/projects")
        assert projects.status_code == 200
        assert set(projects.json()["projects"]) == {"demo", "demo2"}

    @pytest.mark.parametrize(
        ("accept_language", "expected"),
        [("zh", "火山方舟"), ("en", "Volcengine Ark"), ("vi", "Volcengine Ark")],
    )
    def test_grouped_provider_display_name_follows_locale(self, usage_env, accept_language, expected):
        """按供应商分组的用量统计里，内置供应商名跟随请求语言。"""
        resp = usage_env.get(
            "/api/v1/usage/stats?group_by=provider",
            headers={"accept-language": accept_language},
        )
        assert resp.status_code == 200
        names = {s["provider"]: s["display_name"] for s in resp.json()["stats"]}
        assert names["ark"] == expected
        # 译名表里没有的供应商（自定义供应商用用户自填的名字）保留仓储写入的原名。
        assert names["not-in-registry"] == "not-in-registry"


BASE_TIME = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def make_call(**overrides) -> ApiCall:
    """一行 api_calls；未覆写的列取一组不影响筛选的中性值。"""
    fields: dict = {
        "project_name": "demo",
        "call_type": "image",
        "model": "gemini-3.1-flash-image-preview",
        "provider": "gemini",
        "status": CallStatus.SUCCESS,
        "started_at": BASE_TIME,
        "cost_amount": 0.0,
        "currency": "USD",
    }
    fields.update(overrides)
    return ApiCall(**fields)


def build_client(db_factory, monkeypatch) -> TestClient:
    monkeypatch.setattr(usage, "async_session_factory", db_factory)
    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(usage.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return TestClient(app)


@pytest.fixture
async def records_client(db_factory, monkeypatch):
    """五条终态记录 + 一条被任务代表的 pending + 一条无任务的 pending。"""
    async with db_factory() as session:
        session.add(
            Task(
                task_id="t-1",
                project_name="demo",
                task_type="video_generation",
                media_type="video",
                resource_id="S1",
                status="running",
                queued_at=BASE_TIME,
                updated_at=BASE_TIME,
            )
        )
        session.add_all(
            [
                make_call(started_at=BASE_TIME, segment_id="S1", provider="ark", model="doubao", call_type="text"),
                make_call(started_at=BASE_TIME + timedelta(minutes=1), segment_id="S2", status=CallStatus.FAILED),
                make_call(started_at=BASE_TIME + timedelta(minutes=4), segment_id="S3", project_name="demo2"),
                make_call(started_at=BASE_TIME + timedelta(minutes=4), segment_id="S4", status=CallStatus.CANCELLED),
                make_call(
                    started_at=BASE_TIME + timedelta(minutes=4),
                    segment_id="S5",
                    task_id="t-1",
                    call_type="video",
                    prompt="full prompt",
                    inputs={"voice": "v1"},
                    last_provider_response={"raw": "body"},
                ),
                make_call(
                    started_at=BASE_TIME + timedelta(minutes=5),
                    segment_id="hidden",
                    status=CallStatus.PENDING,
                    task_id="t-1",
                ),
                make_call(
                    started_at=BASE_TIME + timedelta(minutes=6), segment_id="taskless", status=CallStatus.PENDING
                ),
            ]
        )
        await session.commit()

    return build_client(db_factory, monkeypatch)


class TestUsageRecordsList:
    def test_orders_newest_first_and_hides_pending_with_task(self, records_client):
        body = records_client.get("/api/v1/usage/records").json()

        assert [item["segment_id"] for item in body["items"]] == ["taskless", "S5", "S4", "S3", "S2", "S1"]
        assert body["total"] == 6
        assert body["next_cursor"] is None

    def test_record_shape_omits_user_id_and_detail_only_fields(self, records_client):
        item = records_client.get("/api/v1/usage/records?segment_id=S5").json()["items"][0]

        assert item["task_id"] == "t-1"
        assert item["task_type"] == "video_generation"
        assert item["media_type"] == "video"
        assert item["started_at"] == "2026-03-01T12:04:00+00:00"
        assert "user_id" not in item
        assert not {"prompt", "inputs", "last_provider_response"} & set(item)

    def test_cursor_pages_without_gap_or_repeat(self, records_client):
        first = records_client.get("/api/v1/usage/records?limit=3").json()
        assert first["next_cursor"]
        second = records_client.get(f"/api/v1/usage/records?limit=3&cursor={first['next_cursor']}").json()

        segments = [item["segment_id"] for item in first["items"] + second["items"]]
        assert segments == ["taskless", "S5", "S4", "S3", "S2", "S1"]
        assert second["total"] == 6
        assert second["next_cursor"] is None

    def test_total_is_filtered_count(self, records_client):
        body = records_client.get("/api/v1/usage/records?project_name=demo&limit=1").json()

        assert len(body["items"]) == 1
        assert body["total"] == 5

    @pytest.mark.parametrize("limit", [0, 201])
    def test_limit_outside_bounds_rejected(self, records_client, limit):
        assert records_client.get(f"/api/v1/usage/records?limit={limit}").status_code == 422

    def test_limit_upper_bound_accepted(self, records_client):
        assert records_client.get("/api/v1/usage/records?limit=200").status_code == 200

    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("status=failed,cancelled", {"S2", "S4"}),
            ("status=pending", {"taskless"}),
            ("media_type=text,video", {"S1", "S5"}),
            ("provider=ark", {"S1"}),
            ("model=doubao", {"S1"}),
            ("segment_id=S2,S3", {"S2", "S3"}),
            ("project_name=demo2", {"S3"}),
        ],
    )
    def test_filter_dimensions(self, records_client, query, expected):
        body = records_client.get(f"/api/v1/usage/records?{query}").json()

        assert {item["segment_id"] for item in body["items"]} == expected
        assert body["total"] == len(expected)

    def test_since_until_half_open_on_started_at(self, records_client):
        body = records_client.get("/api/v1/usage/records?since=2026-03-01T12:01:00Z&until=2026-03-01T12:06:00Z").json()

        assert [item["segment_id"] for item in body["items"]] == ["S5", "S4", "S3", "S2"]

    def test_naive_bounds_are_read_as_utc(self, records_client):
        naive = records_client.get("/api/v1/usage/records?since=2026-03-01T12:04:00").json()
        aware = records_client.get("/api/v1/usage/records?since=2026-03-01T12:04:00%2B00:00").json()

        assert [item["segment_id"] for item in naive["items"]] == ["taskless", "S5", "S4", "S3"]
        assert naive["items"] == aware["items"]

    @pytest.mark.parametrize("query", ["since=0001-01-01T00:00:00%2B08:00", "until=9999-12-31T23:59:59-08:00"])
    def test_unrepresentable_bound_is_rejected(self, records_client, query):
        """带偏移的极端时刻换算到 UTC 会越过 datetime 边界；路由先拒绝，不让它溢出成 500。"""
        response = records_client.get(f"/api/v1/usage/records?{query}")
        assert response.status_code == 422
        assert response.json()["detail"] == "起止时刻超出可表示的时间范围，请检查后重试"

    @pytest.mark.parametrize("cursor", ["not-base64!!", "e30"])
    def test_undecodable_cursor_rejected(self, records_client, cursor):
        assert records_client.get(f"/api/v1/usage/records?cursor={cursor}").status_code == 422

    def test_cursor_instant_overflowing_utc_rejected(self, records_client):
        """游标里的时刻带极端偏移、换算 UTC 越界时同样 422，不让它溢出成 500。"""
        cursor = base64.urlsafe_b64encode(
            json.dumps({"started_at": "0001-01-01T00:00:00+14:00", "id": 1}).encode()
        ).decode()

        assert records_client.get(f"/api/v1/usage/records?cursor={cursor}").status_code == 422

    def test_out_of_range_cursor_id_rejected(self, records_client):
        cursor = base64.urlsafe_b64encode(
            json.dumps({"started_at": BASE_TIME.isoformat(), "id": 10**100}).encode()
        ).decode()

        assert records_client.get(f"/api/v1/usage/records?cursor={cursor}").status_code == 422


class TestUsageRecordDetail:
    def test_detail_adds_prompt_inputs_and_provider_response(self, records_client):
        record_id = records_client.get("/api/v1/usage/records?segment_id=S5").json()["items"][0]["id"]

        detail = records_client.get(f"/api/v1/usage/records/{record_id}").json()

        assert detail["prompt"] == "full prompt"
        assert detail["inputs"] == {"voice": "v1"}
        assert detail["last_provider_response"] == {"raw": "body"}
        assert detail["task_type"] == "video_generation"
        assert "user_id" not in detail

    def test_unknown_id_returns_404(self, records_client):
        assert records_client.get("/api/v1/usage/records/99999").status_code == 404

    def test_response_models_are_declared_in_openapi(self, records_client):
        schema = records_client.get("/openapi.json").json()
        list_ref = schema["paths"]["/api/v1/usage/records"]["get"]["responses"]["200"]["content"]["application/json"][
            "schema"
        ]["$ref"]
        detail_ref = schema["paths"]["/api/v1/usage/records/{record_id}"]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]["$ref"]

        assert list_ref.endswith("UsageRecordPage")
        assert detail_ref.endswith("UsageRecordDetail")
        record = schema["components"]["schemas"]["UsageRecord"]["properties"]
        assert "user_id" not in record
        assert not {"prompt", "inputs", "last_provider_response"} & set(record)
