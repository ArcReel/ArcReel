"""测试连接：真发一次生成、记账、写盘、TTL 与取消。

出站一律走 respx，记账走真实 ``Ledger`` + 内存库——本票的判据是「跑的是生产那条路」，用替身
顶掉 backend 或账本就把这条判据换成了「替身被调用过」。
"""

from __future__ import annotations

import json
import os
import time
import types
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from lib.custom_provider.endpoint_test import (
    TRIAL_RUN_TTL_SECONDS,
    EndpointTestCredentials,
    EndpointTestParameters,
    TrialRunBusyError,
    TrialRunManager,
    TrialRunStatus,
    TrialRunTarget,
    declarative_target,
    provider_from_base_url,
)
from lib.db.models.api_call import ApiCall
from lib.ledger import Ledger
from tests.factories import custom_endpoint_definition
from tests.fakes import bounded_poll_clock
from tests.http_capture import capture_http

PARAMETERS = EndpointTestParameters(model="video-x", prompt="纸船顺流而下", duration_seconds=5)
CREDENTIALS = EndpointTestCredentials(base_url="https://relay.test", api_key="sk-secret-key-1234")


@pytest.fixture()
def trial_runs(tmp_path: Path, db_factory: async_sessionmaker) -> TrialRunManager:
    return TrialRunManager(
        root=tmp_path / "trial_runs",
        ledger=Ledger(session_factory=db_factory),
        read_poll_timeout=_fixed_timeout,
    )


async def _fixed_timeout() -> int:
    return 600


def _target():
    return declarative_target(custom_endpoint_definition(), CREDENTIALS, PARAMETERS)


def _mock_successful_run(router) -> None:
    router.post("https://relay.test/v1/video/create").mock(return_value=httpx.Response(200, json={"task_id": "job-42"}))
    router.get("https://relay.test/v1/video/fetch/job-42").mock(
        side_effect=[
            httpx.Response(200, json={"status": "processing"}),
            httpx.Response(200, json={"status": "completed", "video_url": "https://relay.test/files/job-42.mp4"}),
        ]
    )
    router.get("https://relay.test/files/job-42.mp4").mock(return_value=httpx.Response(200, content=b"video"))


async def _await_terminal(trial_runs: TrialRunManager, run_id: str):
    """等后台 run 停下并取回终态。"""
    run = await trial_runs.wait(run_id)
    assert run is not None and run.status in (TrialRunStatus.SUCCEEDED, TrialRunStatus.FAILED)
    return run


@types.coroutine
def _tick():
    """让出一次事件循环。

    不用 ``asyncio.sleep(0)``：``bounded_poll_clock`` 把 ``asyncio.sleep`` 整个换成了不挂起的
    替身，在它的作用域里 sleep 一次也不会让后台任务往前走。
    """
    yield


async def _await_ledger_row(trial_runs: TrialRunManager, run_id: str, *, tries: int = 2000):
    """等到 pending 记账行落库——取消要断言的正是那一行被翻成 failed。"""
    for _ in range(tries):
        run = trial_runs.get(run_id)
        if run is not None and run.api_call_id is not None:
            return run
        await _tick()
    raise AssertionError("测试连接未在预期内落下记账行")


class TestTrialRun:
    async def test_runs_the_production_path_to_a_terminal_state(self, trial_runs: TrialRunManager):
        with capture_http() as router, bounded_poll_clock():
            _mock_successful_run(router)
            started = await trial_runs.start(_target(), PARAMETERS)
            run = await _await_terminal(trial_runs, started.id)

        assert run.status is TrialRunStatus.SUCCEEDED
        assert run.video_url == "https://relay.test/files/job-42.mp4"
        assert trial_runs.artifact_path(run.id) is not None
        assert trial_runs.artifact_path(run.id).read_bytes() == b"video"

    async def test_records_the_call_in_the_ledger(self, trial_runs: TrialRunManager, db_factory: async_sessionmaker):
        with capture_http() as router, bounded_poll_clock():
            _mock_successful_run(router)
            started = await trial_runs.start(_target(), PARAMETERS)
            run = await _await_terminal(trial_runs, started.id)

        async with db_factory() as session:
            rows = (await session.execute(select(ApiCall))).scalars().all()
        assert len(rows) == 1
        assert rows[0].id == run.api_call_id
        assert rows[0].status == "success"
        assert rows[0].call_type == "video"
        # 内联凭证没有供应商身份，落账用 base_url 的 host，账单上才认得出这笔钱花在哪。
        assert rows[0].provider == "relay.test"

    async def test_result_carries_the_rendered_request_responses_and_extractions(self, trial_runs: TrialRunManager):
        with capture_http() as router, bounded_poll_clock():
            _mock_successful_run(router)
            started = await trial_runs.start(
                _target(), PARAMETERS, request_preview={"method": "POST", "url": "https://relay.test/v1/video/create"}
            )
            run = await _await_terminal(trial_runs, started.id)

        assert run.request == {"method": "POST", "url": "https://relay.test/v1/video/create"}
        assert run.submit_response == {"task_id": "job-42"}
        assert len(run.poll_responses) == 2
        assert run.extractions["submit"]["task_id"] == "job-42"
        assert run.extractions["poll"]["status"] == "succeeded"

    async def test_terminal_result_is_read_from_disk(self, trial_runs: TrialRunManager):
        with capture_http() as router, bounded_poll_clock():
            _mock_successful_run(router)
            started = await trial_runs.start(_target(), PARAMETERS)
            await _await_terminal(trial_runs, started.id)

        stored = json.loads((trial_runs.root / started.id / "result.json").read_text(encoding="utf-8"))
        assert stored["status"] == "succeeded"
        assert trial_runs.get(started.id).video_url == stored["video_url"]

    async def test_a_provider_failure_lands_as_a_failed_run(self, trial_runs: TrialRunManager):
        with capture_http() as router, bounded_poll_clock():
            router.post("https://relay.test/v1/video/create").mock(
                return_value=httpx.Response(200, json={"error": {"message": "quota exceeded"}})
            )
            started = await trial_runs.start(_target(), PARAMETERS)
            run = await _await_terminal(trial_runs, started.id)

        assert run.status is TrialRunStatus.FAILED
        assert "quota exceeded" in (run.error or "")

    async def test_a_backend_that_cannot_be_built_is_not_billed(
        self, trial_runs: TrialRunManager, db_factory: async_sessionmaker
    ):
        """装配失败时一个字节都没发出去，账本上就不该多一条调用。"""

        async def _explode():
            raise ValueError("自定义供应商 custom-9 不存在")

        target = TrialRunTarget(provider="custom-9", model="video-x", build_backend=_explode)
        started = await trial_runs.start(target, PARAMETERS)
        run = await _await_terminal(trial_runs, started.id)

        assert run.status is TrialRunStatus.FAILED
        assert run.api_call_id is None
        async with db_factory() as session:
            assert (await session.execute(select(ApiCall))).scalars().all() == []


class TestConcurrencyAndCancel:
    async def test_a_second_run_for_the_same_user_is_refused(self, trial_runs: TrialRunManager):
        with capture_http() as router, bounded_poll_clock():
            _mock_successful_run(router)
            first = await trial_runs.start(_target(), PARAMETERS)
            with pytest.raises(TrialRunBusyError):
                await trial_runs.start(_target(), PARAMETERS)
            await _await_terminal(trial_runs, first.id)

    async def test_a_new_run_is_allowed_after_the_previous_one_finishes(self, trial_runs: TrialRunManager):
        with capture_http() as router, bounded_poll_clock():
            _mock_successful_run(router)
            first = await trial_runs.start(_target(), PARAMETERS)
            await _await_terminal(trial_runs, first.id)
            _mock_successful_run(router)
            second = await trial_runs.start(_target(), PARAMETERS)
            await _await_terminal(trial_runs, second.id)

        assert second.id != first.id

    async def test_cancel_settles_the_call_as_failed_and_frees_the_slot(
        self, trial_runs: TrialRunManager, db_factory: async_sessionmaker
    ):
        with capture_http() as router, bounded_poll_clock():
            router.post("https://relay.test/v1/video/create").mock(
                return_value=httpx.Response(200, json={"task_id": "job-42"})
            )
            router.get("https://relay.test/v1/video/fetch/job-42").mock(
                return_value=httpx.Response(200, json={"status": "processing"})
            )
            started = await trial_runs.start(_target(), PARAMETERS)
            await _await_ledger_row(trial_runs, started.id)

            assert await trial_runs.cancel(started.id) is True
            # 取消后名额立刻让出：用户改完参数就能再发一次。
            _mock_successful_run(router)
            retried = await trial_runs.start(_target(), PARAMETERS)
            await _await_terminal(trial_runs, retried.id)

        async with db_factory() as session:
            rows = (await session.execute(select(ApiCall).order_by(ApiCall.id))).scalars().all()
        assert [row.status for row in rows] == ["failed", "success"]
        # 取消的 run 不留结果文件，读接口据此 404。
        assert trial_runs.get(started.id) is None

    async def test_cancelling_an_unknown_run_reports_nothing_to_cancel(self, trial_runs: TrialRunManager):
        assert await trial_runs.cancel("no-such-run") is False


class TestRetention:
    def test_results_older_than_the_ttl_are_purged(self, trial_runs: TrialRunManager):
        stale = trial_runs.root / "stale"
        stale.mkdir(parents=True)
        (stale / "result.json").write_text("{}", encoding="utf-8")
        fresh = trial_runs.root / "fresh"
        fresh.mkdir(parents=True)

        removed = trial_runs.purge_expired(now=time.time() + TRIAL_RUN_TTL_SECONDS + 1)

        assert removed == 2
        assert not stale.exists()

    async def test_a_result_past_the_ttl_is_no_longer_readable(self, trial_runs: TrialRunManager):
        """读接口自己判过期：一台长时间没有新 run 的服务器上，清理不会被触发。"""
        with capture_http() as router, bounded_poll_clock():
            _mock_successful_run(router)
            started = await trial_runs.start(_target(), PARAMETERS)
            await _await_terminal(trial_runs, started.id)

        assert trial_runs.get(started.id) is not None
        aged = time.time() - TRIAL_RUN_TTL_SECONDS - 1
        os.utime(trial_runs.root / started.id, (aged, aged))

        assert trial_runs.get(started.id) is None
        assert trial_runs.artifact_path(started.id) is None

    def test_a_result_within_the_ttl_survives(self, trial_runs: TrialRunManager):
        recent = trial_runs.root / "recent"
        recent.mkdir(parents=True)

        assert trial_runs.purge_expired() == 0
        assert recent.exists()

    def test_provider_falls_back_to_the_host_of_the_base_url(self):
        assert provider_from_base_url("https://relay.test/v1") == "relay.test"
