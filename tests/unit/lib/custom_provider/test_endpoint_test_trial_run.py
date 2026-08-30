"""测试连接：真发一次生成、记账、写盘、TTL 与取消。

出站一律走 respx，记账走真实 ``Ledger`` + 内存库——本票的判据是「跑的是生产那条路」，用替身
顶掉 backend 或账本就把这条判据换成了「替身被调用过」。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
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


def _mock_pending_run(router) -> asyncio.Event:
    """一直停在 processing 的 run；返回的事件在提交请求发出时置位。

    pending 记账行在发这一枪之前就落了库（``run.api_call_id`` 同点赋值），所以等这次握手
    等到的正是「取消要翻的那一行已经存在」，不必去猜事件循环要让步多少次。
    """
    submitted = asyncio.Event()

    def _create(_request: httpx.Request) -> httpx.Response:
        submitted.set()
        return httpx.Response(200, json={"task_id": "job-42"})

    router.post("https://relay.test/v1/video/create").mock(side_effect=_create)
    router.get("https://relay.test/v1/video/fetch/job-42").mock(
        return_value=httpx.Response(200, json={"status": "processing"})
    )
    return submitted


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

    async def test_submit_retry_keeps_the_successful_submit_response(self, trial_runs: TrialRunManager):
        with capture_http() as router, bounded_poll_clock():
            router.post("https://relay.test/v1/video/create").mock(
                side_effect=[
                    httpx.Response(429, json={"error": "busy"}),
                    httpx.Response(200, json={"task_id": "job-42"}),
                ]
            )
            router.get("https://relay.test/v1/video/fetch/job-42").mock(
                side_effect=[
                    httpx.Response(200, json={"status": "processing"}),
                    httpx.Response(
                        200,
                        json={"status": "completed", "video_url": "https://relay.test/files/job-42.mp4"},
                    ),
                ]
            )
            router.get("https://relay.test/files/job-42.mp4").mock(return_value=httpx.Response(200, content=b"video"))

            started = await trial_runs.start(_target(), PARAMETERS)
            run = await _await_terminal(trial_runs, started.id)

        assert run.status is TrialRunStatus.SUCCEEDED, run.error
        assert run.submit_response == {"task_id": "job-42"}
        assert run.poll_responses == [
            {"status": "processing"},
            {"status": "completed", "video_url": "https://relay.test/files/job-42.mp4"},
        ]

    async def test_result_response_is_not_counted_as_polling(self, trial_runs: TrialRunManager):
        definition = custom_endpoint_definition()
        definition["poll"]["extract"] = {"status": ["$.status"], "result_id": ["$.result_id"]}
        definition["result"] = {
            "method": "GET",
            "url": "{{ base_url }}/v1/video/result/{{ result_id }}",
            "extract": {"video_url": ["$.video_url"]},
        }
        poll_body = {"status": "completed", "result_id": "result-9"}
        result_body = {"video_url": "https://relay.test/files/job-42.mp4"}

        with capture_http() as router, bounded_poll_clock(step=1.0):
            router.post("https://relay.test/v1/video/create").mock(
                return_value=httpx.Response(200, json={"task_id": "job-42"})
            )
            router.get("https://relay.test/v1/video/fetch/job-42").mock(
                return_value=httpx.Response(200, json=poll_body)
            )
            router.get("https://relay.test/v1/video/result/result-9").mock(
                return_value=httpx.Response(200, json=result_body)
            )
            router.get("https://relay.test/files/job-42.mp4").mock(return_value=httpx.Response(200, content=b"video"))

            target = declarative_target(definition, CREDENTIALS, PARAMETERS)
            started = await trial_runs.start(target, PARAMETERS)
            run = await _await_terminal(trial_runs, started.id)

        assert run.status is TrialRunStatus.SUCCEEDED, run.error
        assert run.poll_responses == [poll_body]
        assert run.result_response == result_body
        assert run.extractions["poll"]["status"] == "succeeded"
        assert run.extractions["result"]["video_url"] == result_body["video_url"]

    async def test_terminal_result_is_read_from_disk(self, trial_runs: TrialRunManager):
        with capture_http() as router, bounded_poll_clock():
            _mock_successful_run(router)
            started = await trial_runs.start(_target(), PARAMETERS)
            await _await_terminal(trial_runs, started.id)

        stored = json.loads((trial_runs.root / started.id / "result.json").read_text(encoding="utf-8"))
        assert stored["status"] == "succeeded"
        assert trial_runs.get(started.id).video_url == stored["video_url"]

    async def test_a_declared_capability_violation_is_rejected_before_billing(
        self, trial_runs: TrialRunManager, db_factory: async_sessionmaker, tmp_path: Path
    ):
        """生产路径在记账前跑能力闸；测试连接同一道闸——违约请求一个字节不外发、一分钱不记账。"""
        face = tmp_path / "face.png"
        face.write_bytes(b"png")
        with capture_http() as router, bounded_poll_clock():
            # 夹具定义只声明 first_frame，参考图路径不可用（max_reference_images=0）。
            started = await trial_runs.start(_target(), PARAMETERS, assets={"reference_images": [face]})
            run = await _await_terminal(trial_runs, started.id)

        assert run.status is TrialRunStatus.FAILED
        assert router.calls.call_count == 0
        async with db_factory() as session:
            rows = (await session.execute(select(ApiCall))).scalars().all()
        assert rows == []

    async def test_a_reference_driven_endpoint_passes_the_gate_without_a_first_frame(
        self, tmp_path: Path, trial_runs: TrialRunManager
    ):
        """参考图驱动的端点（如 S2V）没有首帧但带参考图：闸看整份槽位计划，不能判成纯文生。"""
        definition = custom_endpoint_definition(
            inputs={"refs": {"source": "reference_images", "encoding": "data_uri"}},
            capabilities={"text_to_video": False, "max_reference_images": 3},
        )
        definition["submit"]["body"] = {
            "model": "{{ model }}",
            "prompt": "{{ prompt }}",
            "subject_reference": "{{ inputs.refs }}",
        }
        face = tmp_path / "face.png"
        face.write_bytes(b"png")

        with capture_http() as router, bounded_poll_clock():
            _mock_successful_run(router)
            started = await trial_runs.start(
                declarative_target(definition, CREDENTIALS, PARAMETERS),
                PARAMETERS,
                assets={"reference_images": [face]},
            )
            run = await _await_terminal(trial_runs, started.id)

        assert run.status is TrialRunStatus.SUCCEEDED, run.error

    async def test_a_first_frame_request_sends_the_adaptive_ratio_when_the_endpoint_requires_it(
        self, tmp_path: Path, trial_runs: TrialRunManager, db_factory: async_sessionmaker
    ):
        """声明 first_frame_ratio_adaptive_only 的端点只接受 adaptive；账本仍记用户填的比例意图。"""
        definition = custom_endpoint_definition(
            capabilities={"first_frame": True, "first_frame_ratio_adaptive_only": True}
        )
        definition["submit"]["body"]["ratio"] = "{{ aspect_ratio }}"
        frame = tmp_path / "frame.png"
        frame.write_bytes(b"png")

        with capture_http() as router, bounded_poll_clock():
            _mock_successful_run(router)
            submit = router.post("https://relay.test/v1/video/create").mock(
                return_value=httpx.Response(200, json={"task_id": "job-42"})
            )
            started = await trial_runs.start(
                declarative_target(definition, CREDENTIALS, PARAMETERS),
                PARAMETERS,
                assets={"start_image": frame},
            )
            run = await _await_terminal(trial_runs, started.id)

        assert run.status is TrialRunStatus.SUCCEEDED, run.error
        assert json.loads(submit.calls.last.request.content)["ratio"] == "adaptive"
        async with db_factory() as session:
            rows = (await session.execute(select(ApiCall))).scalars().all()
        assert rows[0].aspect_ratio == PARAMETERS.aspect_ratio

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
            submitted = _mock_pending_run(router)
            started = await trial_runs.start(_target(), PARAMETERS)
            await submitted.wait()

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

    async def test_a_failed_settlement_still_frees_the_slot(self, trial_runs: TrialRunManager, monkeypatch):
        """结算抛错时任务已停、task 已移除：名额不还就永远 busy，重试取消也只会 404。"""

        async def _boom(*, call_id: int) -> None:
            raise RuntimeError("db down")

        with capture_http() as router, bounded_poll_clock():
            submitted = _mock_pending_run(router)
            started = await trial_runs.start(_target(), PARAMETERS)
            await submitted.wait()

            monkeypatch.setattr(trial_runs._ledger, "resume_failed", _boom)
            with pytest.raises(RuntimeError):
                await trial_runs.cancel(started.id)

            monkeypatch.undo()
            _mock_successful_run(router)
            retried = await trial_runs.start(_target(), PARAMETERS)
            await _await_terminal(trial_runs, retried.id)

    async def test_a_failed_directory_frees_the_slot(self, trial_runs: TrialRunManager, monkeypatch):
        """建结果目录失败时名额没让出去，后续测试会被永远拒成 busy，而取消又还没有 task 可停。"""

        def _boom(*_args, **_kwargs) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(Path, "mkdir", _boom)
        with pytest.raises(OSError, match="disk full"):
            await trial_runs.start(_target(), PARAMETERS)

        monkeypatch.undo()
        with capture_http() as router, bounded_poll_clock():
            _mock_successful_run(router)
            retried = await trial_runs.start(_target(), PARAMETERS)
            await _await_terminal(trial_runs, retried.id)

    async def test_shutdown_keeps_draining_after_a_failed_settlement(self, trial_runs: TrialRunManager, monkeypatch):
        """一条 run 结算失败不能中断关停：后面的 run 一条都不会停，关库等后续步骤也一起跳过。"""
        settled: list[int] = []
        original = trial_runs._ledger.resume_failed

        async def _boom_once(*, call_id: int) -> None:
            if not settled:
                settled.append(call_id)
                raise RuntimeError("db down")
            settled.append(call_id)
            await original(call_id=call_id)

        with capture_http() as router, bounded_poll_clock():
            submitted = _mock_pending_run(router)
            first = await trial_runs.start(_target(), PARAMETERS, user_id="user-a")
            await submitted.wait()
            submitted.clear()
            second = await trial_runs.start(_target(), PARAMETERS, user_id="user-b")
            await submitted.wait()

            monkeypatch.setattr(trial_runs._ledger, "resume_failed", _boom_once)
            await trial_runs.shutdown()

        assert len(settled) == 2
        assert trial_runs.get(first.id) is None
        assert trial_runs.get(second.id) is None

    async def test_shutdown_settles_every_active_run_as_failed(
        self, trial_runs: TrialRunManager, db_factory: async_sessionmaker
    ):
        """进程关停不能把 pending 记账行留给一个不会回来的进程。"""
        with capture_http() as router, bounded_poll_clock():
            submitted = _mock_pending_run(router)
            started = await trial_runs.start(_target(), PARAMETERS)
            await submitted.wait()

            await trial_runs.shutdown()

        async with db_factory() as session:
            rows = (await session.execute(select(ApiCall))).scalars().all()
        assert [row.status for row in rows] == ["failed"]
        assert trial_runs.get(started.id) is None


class TestRunIdShape:
    """run_id 是调用方传入的路径段：非生成格式的值不许触达盘上路径。"""

    def test_a_traversal_shaped_run_id_reads_nothing(self, trial_runs: TrialRunManager):
        # root 上一级放一份 result.json：``root/../result.json`` 恰好指到它，越界读会命中。
        (trial_runs.root.parent / "result.json").write_text("{}", encoding="utf-8")

        assert trial_runs.get("..") is None
        assert trial_runs.artifact_path("..") is None

    async def test_a_generated_run_id_still_round_trips(self, trial_runs: TrialRunManager):
        with capture_http() as router, bounded_poll_clock():
            _mock_successful_run(router)
            started = await trial_runs.start(_target(), PARAMETERS)
            await _await_terminal(trial_runs, started.id)

        assert trial_runs.get(started.id) is not None


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

    def test_a_result_file_with_ill_typed_fields_reads_as_missing(self, trial_runs: TrialRunManager):
        """坏掉的结果文件走受控失败路径：读接口据此 404，而不是让响应校验在出口炸成 500。"""
        run_id = "0" * 32
        (trial_runs.root / run_id).mkdir(parents=True)
        (trial_runs.root / run_id / "result.json").write_text(
            json.dumps({"id": run_id, "status": "succeeded", "finished_at": "invalid"}), encoding="utf-8"
        )

        assert trial_runs.get(run_id) is None

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
