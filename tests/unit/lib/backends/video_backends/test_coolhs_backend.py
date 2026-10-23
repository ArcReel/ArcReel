"""CoolhsVideoBackend 的请求构造与状态判定。

这一层不联网：假 client 记录发出的请求，断言三件最容易错的事——素材编号顺序、
扩写开关被关掉、排队状态被正确识别为终态/失败态。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from arcreel_market_core.video_backend_contract import VideoGenerationRequest
from lib.backends.video_backends.coolhs import (
    CoolhsRideClosedError,
    CoolhsVideoBackend,
    _failure_message,
    _failure_reason,
    _human_duration,
    _is_failure,
    _is_terminal,
    _job_id_for,
    _phase,
    _resolution_label,
    _ride_refusal,
    _video_url_of,
)


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeHttp:
    """记录调用并按预设回包；只为验请求形状，不模拟网络。"""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append(("POST", url, kwargs))
        return self._responses.pop(0)

    async def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append(("GET", url, kwargs))
        return self._responses.pop(0)

    async def put(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append(("PUT", url, kwargs))
        return self._responses.pop(0)


def _request(**overrides: Any) -> VideoGenerationRequest:
    base: dict[str, Any] = {
        "prompt": "图1 的人物站在图2 的场景里",
        "output_path": Path("/tmp/out.mp4"),
        "duration_seconds": 15,
        "resolution": "720p",
        "aspect_ratio": "9:16",
        "task_id": "task-abc",
        "generate_audio": True,
    }
    base.update(overrides)
    return VideoGenerationRequest(**base)


def test_job_id_is_deterministic_per_task():
    """平台按 id 幂等：同一任务重试必须落同一个 id，否则会重复扣费。"""
    first = _job_id_for(_request(task_id="t-1"))
    second = _job_id_for(_request(task_id="t-1"))
    assert first == second
    assert first != _job_id_for(_request(task_id="t-2"))


def test_job_id_falls_back_to_random_without_task_id():
    """非 worker 路径没有稳定身份，退回随机 id（每次都是新单，但不会撞别人的单）。"""
    assert _job_id_for(_request(task_id=None)) != _job_id_for(_request(task_id=None))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("720p", "720P"), ("1080p", "1080P"), ("480p", "480P"), ("720P", "720P"), (None, "480P"), ("weird", "480P")],
)
def test_resolution_label_is_uppercased(raw, expected):
    """平台要大写档位；小写会被 400 拒。"""
    assert _resolution_label(raw) == expected


def test_phase_reads_nested_wait_block():
    assert _phase({"wait": {"phase": "running"}}) == "running"
    assert _phase({"status": "boarded"}) == ""


@pytest.mark.parametrize("phase", ["done", "failed", "cancelled"])
def test_terminal_phases_end_the_poll(phase):
    assert _is_terminal({"wait": {"phase": phase}}) is True


@pytest.mark.parametrize("phase", ["boarding", "queued", "running"])
def test_active_phases_keep_polling(phase):
    assert _is_terminal({"wait": {"phase": phase}}) is False
    assert _is_failure({"wait": {"phase": phase}}) is False


def test_cancelled_counts_as_failure():
    """取消态额度已退回、不会有成片；归失败才不会让 worker 空等到超时。"""
    assert _is_failure({"wait": {"phase": "cancelled"}}) is True


def test_failure_reason_prefers_platform_detail():
    assert _failure_reason({"wait": {"phase": "failed", "detail": "素材帧率太低"}}) == "素材帧率太低"
    assert _failure_reason({"error": {"message": "额度不足"}}) == "额度不足"
    assert _failure_reason({"wait": {"phase": "failed"}}) is None


def test_video_url_reads_camel_case_field():
    assert _video_url_of({"videoUrl": "https://cdn/x.mp4"}) == "https://cdn/x.mp4"
    assert _video_url_of({}) is None


def test_capabilities_declare_reference_path_and_frames():
    caps = CoolhsVideoBackend.video_capabilities_for_model("wan")
    assert caps.text_to_video is True
    assert caps.first_frame is True
    assert caps.last_frame is True
    assert caps.max_reference_images == 10
    assert caps.max_reference_audio_count == 5


_PRESIGN = {
    "url": "https://upload.coolhs.com/k/x.png?exp=1&sig=abc",
    "key": "wanwan/uploads/x.png",
    "contentType": "image/png",
    "expires": 900,
}


def _presign_chain(count: int) -> list[_FakeResponse]:
    """count 张素材各自要走的 presign → PUT → commit 三连响应。"""
    chain: list[_FakeResponse] = []
    for index in range(count):
        chain.append(_FakeResponse(dict(_PRESIGN)))
        chain.append(_FakeResponse({"ok": True}))
        chain.append(_FakeResponse({"ok": True, "id": f"att-{index}"}))
    return chain


async def test_upload_media_numbers_reference_images_in_order(tmp_path):
    """参考图按顺序取 图1…图N：提示词里的「图N」靠这个编号对齐，错一位人物就换脸。"""
    paths = []
    for index in (1, 2):
        path = tmp_path / f"ref{index}.png"
        path.write_bytes(b"x")
        paths.append(path)
    http = _FakeHttp(_presign_chain(2))
    backend = CoolhsVideoBackend(model="wan", api_key="WAN-TEST")

    media = await backend._upload_media(http, _request(reference_images=paths))

    assert [item["token"] for item in media] == ["图1", "图2"]
    assert [item["role"] for item in media] == ["ref", "ref"]
    # token 在登记那一步传给平台，服务端据此把图号写进附件
    assert http.calls[2][2]["json"]["token"] == "图1"
    assert http.calls[5][2]["json"]["token"] == "图2"


async def test_upload_runs_presign_direct_put_then_commit(tmp_path):
    """新上传流程必须三步齐全且顺序正确：签名 → 直传对象存储 → 登记换 id。"""
    path = tmp_path / "sheet.png"
    path.write_bytes(b"x")
    http = _FakeHttp(_presign_chain(1))
    backend = CoolhsVideoBackend(model="wan", api_key="WAN-TEST")

    media = await backend._upload_media(http, _request(reference_images=[path]))

    methods = [method for method, _url, _kwargs in http.calls]
    assert methods == ["POST", "PUT", "POST"]
    presign, put, commit = http.calls

    assert presign[1].endswith("/api/portal/media/presign")
    assert presign[2]["json"] == {"name": "sheet.png", "size": 1, "kind": "image", "mime": "image/png"}

    # 直传打的是预签名地址，且必须与平台域名不同（文件体不过平台服务器）
    assert put[1] == _PRESIGN["url"]
    assert put[2]["headers"]["Content-Type"] == "image/png"
    # 卡密只属于平台域名，不能跟着文件一起发给对象存储
    assert "X-Wan-Card" not in put[2]["headers"]

    assert commit[1].endswith("/api/portal/media/commit")
    assert commit[2]["json"] == {
        "key": "wanwan/uploads/x.png",
        "name": "sheet.png",
        "kind": "image",
        "mime": "image/png",
        "token": "图1",
    }
    # media[] 只认 commit 返回的附件 id，不是预签名里的 key
    assert media[0]["id"] == "att-0"


async def test_upload_audio_uses_audio_mime_and_skips_token(tmp_path):
    """音频素材：MIME 走 audio/mpeg，token 只属于参考图（音频按顺序编号，不写 token）。"""
    path = tmp_path / "voice.mp3"
    path.write_bytes(b"x")
    http = _FakeHttp(_presign_chain(1))
    backend = CoolhsVideoBackend(model="wan", api_key="WAN-TEST")

    await backend._upload_media(http, _request(reference_audio_files=[path]))

    assert http.calls[0][2]["json"]["mime"] == "audio/mpeg"
    assert "token" not in http.calls[2][2]["json"]


async def test_upload_rejects_a_presign_without_url(tmp_path):
    """预签名没给 url 就立刻失败，别把一次必然被拒的提交发出去。"""
    path = tmp_path / "a.png"
    path.write_bytes(b"x")
    http = _FakeHttp([_FakeResponse({"key": "only-key"})])
    backend = CoolhsVideoBackend(model="wan", api_key="WAN-TEST")

    with pytest.raises(ValueError, match="url/key"):
        await backend._upload_media(http, _request(reference_images=[path]))


async def test_frames_take_priority_and_do_not_consume_image_numbers(tmp_path):
    """首尾帧与参考图不可同现：按首尾帧提交，且帧不占「图N」编号。"""
    start = tmp_path / "start.png"
    start.write_bytes(b"x")
    end = tmp_path / "end.png"
    end.write_bytes(b"x")
    http = _FakeHttp(_presign_chain(2))
    backend = CoolhsVideoBackend(model="wan", api_key="WAN-TEST")

    media = await backend._upload_media(http, _request(start_image=start, end_image=end))

    assert [item["role"] for item in media] == ["first", "last"]
    assert all("token" not in item for item in media)
    # 首尾帧不带 token：正文不引用它们，给了反而让平台当成参考图
    assert "token" not in http.calls[2][2]["json"]
    assert "token" not in http.calls[5][2]["json"]


async def test_submit_disables_prompt_extend_and_carries_media(tmp_path):
    """``promptExtend`` 必须显式关掉：平台默认会扩写，参考图约束被稀释后人物就不像了。"""
    http = _FakeHttp([_FakeResponse({"id": "job-1", "status": "boarded"})])
    backend = CoolhsVideoBackend(model="wan", api_key="WAN-TEST")
    media = [{"id": "m1", "kind": "image", "name": "a.png", "role": "ref", "token": "图1"}]

    job_id = await backend._submit(http, _request(), media)

    assert job_id == "job-1"
    body = http.calls[0][2]["json"]
    assert body["promptExtend"] == 0
    assert body["media"] == media
    assert body["resolution"] == "720P"
    assert body["duration"] == 15
    assert body["audio"] is True
    # 卡密与浏览器 UA 都必须带：Cloudflare 会拒掉非浏览器标识
    headers = http.calls[0][2]["headers"]
    assert headers["X-Wan-Card"] == "WAN-TEST"
    assert "Mozilla/5.0" in headers["User-Agent"]


# ── 班次预检（平台关单时提前失败，不白等 503 重试） ──────────────────────


class _FakeClientCm:
    """把 _FakeHttp 包成 ``async with`` 可用的形态，替掉真实的 httpx client 构造。"""

    def __init__(self, http: _FakeHttp) -> None:
        self._http = http

    async def __aenter__(self) -> _FakeHttp:
        return self._http

    async def __aexit__(self, *exc: Any) -> None:
        return None


def _patch_client(monkeypatch: pytest.MonkeyPatch, http: _FakeHttp) -> None:
    import lib.backends.video_backends.coolhs as coolhs_module

    monkeypatch.setattr(coolhs_module, "artifact_http_client", lambda **_: _FakeClientCm(http))


def test_ride_refusal_explains_a_closed_schedule():
    """关单时给出的原因要能直接贴给用户看：车没开、还在等多久的量化现状、以及没扣费。"""
    reason = _ride_refusal(
        {"open": False, "accepting": False, "boarded": 408, "running": 2065, "label": "这班还没开"}
    )

    assert reason is not None
    assert "这班还没开" in reason
    assert "候车 408 条" in reason
    assert "运行 2065 条" in reason
    assert "未产生扣费" in reason


@pytest.mark.parametrize(
    "state",
    [
        {"accepting": True, "open": True},
        {"accepting": True, "open": False},
        {"open": True},
        {},
        {"accepting": "false"},
        {"accepting": None, "open": None},
    ],
)
def test_ride_refusal_only_blocks_on_an_explicit_closed_flag(state):
    """预检是优化不是闸门：状态含糊一律放行，由提交响应作最终裁决。"""
    assert _ride_refusal(state) is None


def test_ride_refusal_uses_open_when_accepting_is_absent():
    assert _ride_refusal({"open": False, "label": "本班已发车"}) is not None


async def test_closed_ride_fails_before_upload_and_submit(monkeypatch):
    """关键保护：放弃等待时一次上传、一次提交都不能发——那两笔都会白烧额度。"""
    http = _FakeHttp(
        [_FakeResponse({"open": False, "accepting": False, "boarded": 408, "running": 2065, "label": "这班还没开"})]
    )
    _patch_client(monkeypatch, http)
    backend = CoolhsVideoBackend(model="wan", api_key="WAN-TEST", ride_wait_seconds=0)

    with pytest.raises(CoolhsRideClosedError) as excinfo:
        await backend.generate(_request())

    assert [method for method, _url, _kwargs in http.calls] == ["GET"]
    assert http.calls[0][1].endswith("/api/portal/ride")
    assert "未产生扣费" in str(excinfo.value)


@pytest.mark.parametrize(
    "response",
    [
        _FakeResponse({"detail": "服务内部错误（OperationalError）"}, status_code=500),
        _FakeResponse({"unexpected": "shape"}),
    ],
)
async def test_ride_precheck_never_blocks_on_its_own_failure(monkeypatch, response):
    """状态接口自己坏了（5xx / 形状不认识）必须放行：否则整批生成会被一个探针停摆。"""
    backend = CoolhsVideoBackend(model="wan", api_key="WAN-TEST")
    http = _FakeHttp([response])

    await backend._ensure_ride_open(http)  # 不抛即通过

    assert [method for method, _url, _kwargs in http.calls] == ["GET"]


async def test_ride_precheck_survives_a_transport_error(monkeypatch):
    class _Boom:
        async def get(self, *args: Any, **kwargs: Any):
            raise httpx.ConnectTimeout("预检超时")

    await CoolhsVideoBackend(model="wan", api_key="WAN-TEST")._ensure_ride_open(_Boom())


async def test_resume_is_not_gated_by_the_ride_state(monkeypatch):
    """续跑绝不能看班次：那笔额度已经扣了，平台关不关单都得把已排队的片子取回来。"""
    http = _FakeHttp([_FakeResponse({"wait": {"phase": "failed", "detail": "平台取消"}})])
    _patch_client(monkeypatch, http)
    backend = CoolhsVideoBackend(model="wan", api_key="WAN-TEST")

    with pytest.raises(RuntimeError) as excinfo:
        await backend.resume_video("job-9", _request())

    # 第一发就是轮询任务，没有先去问班次
    assert http.calls[0][1].endswith("/api/portal/jobs/job-9")
    assert "平台取消" in str(excinfo.value)
    assert "平台暂停接单" not in str(excinfo.value)


def test_failure_message_carries_the_platform_reason():
    """``is_failed`` 必须返回原因串而不是 bool：否则用户看到的是 ``RuntimeError(True)``。"""
    message = _failure_message({"wait": {"phase": "failed", "detail": "素材帧率太低"}})
    assert message is not None
    assert "素材帧率太低" in message
    assert "failed" in message
    # 非失败态返回 None，轮询才会继续
    assert _failure_message({"wait": {"phase": "running"}}) is None


# ── 关单期自动等班次 ─────────────────────────────────────────────────────


def _closed() -> _FakeResponse:
    return _FakeResponse(
        {"open": False, "accepting": False, "boarded": 405, "running": 1740, "label": "这班还没开"}
    )


def _open() -> _FakeResponse:
    return _FakeResponse({"open": True, "accepting": True, "boarded": 0, "running": 12, "label": "上船"})


def _waiting_backend(http: _FakeHttp, monkeypatch: pytest.MonkeyPatch, *, cap: float = 60.0):
    """造一个「间隔 0 秒、抖动 0、上限 cap」的 backend：等待循环仍走真实代码路径但不等墙钟。"""
    _patch_client(monkeypatch, http)
    backend = CoolhsVideoBackend(model="wan", api_key="WAN-TEST", ride_wait_seconds=cap)
    backend._ride_wait_interval_seconds = 0.0
    backend._ride_wait_jitter = 0.0
    return backend


async def test_ride_wait_keeps_polling_until_the_schedule_opens(monkeypatch):
    """关单不该判失败：反复看班次，一开盘立刻放行。"""
    http = _FakeHttp([_closed(), _closed(), _open()])
    backend = _waiting_backend(http, monkeypatch)

    await backend._await_ride_open(http)

    assert [method for method, _url, _kwargs in http.calls] == ["GET", "GET", "GET"]
    assert all(url.endswith("/api/portal/ride") for _m, url, _k in http.calls)


async def test_ride_wait_gives_up_at_the_cap_and_says_it_cost_nothing(monkeypatch):
    """超上限才放弃，且必须说清「没扣费」——否则用户会以为额度被吃掉了。"""
    http = _FakeHttp([_closed() for _ in range(50)])
    backend = _waiting_backend(http, monkeypatch, cap=0)

    with pytest.raises(CoolhsRideClosedError) as excinfo:
        await backend._await_ride_open(http)

    message = str(excinfo.value)
    assert "未开班" in message
    assert "未产生扣费" in message


async def test_ride_wait_returns_at_once_when_already_open(monkeypatch):
    """开班时只查一次就走，不白睡一个间隔。"""
    http = _FakeHttp([_open()])
    backend = _waiting_backend(http, monkeypatch)

    await backend._await_ride_open(http)

    assert len(http.calls) == 1


async def test_ride_wait_fails_open_when_the_probe_breaks(monkeypatch):
    """等待期间状态接口自己坏了，必须放行交给提交裁决，不然整批任务会一起卡死。"""
    http = _FakeHttp([_FakeResponse({"detail": "OperationalError"}, status_code=500)])
    backend = _waiting_backend(http, monkeypatch)

    await backend._await_ride_open(http)

    assert len(http.calls) == 1



@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "0 秒"), (20, "20 秒"), (300, "5 分钟"), (43200, "12.0 小时")],
)
def test_wait_durations_are_said_in_human_units(seconds, expected):
    """20 秒的等待不能写成「上限 0 小时」——单位跟着量级走。"""
    assert _human_duration(seconds) == expected
