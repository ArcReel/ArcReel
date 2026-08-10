"""MiniMaxVideoBackend 单元测试（mock httpx，异步两步取 URL，不打真实 HTTP）。"""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.providers import PROVIDER_MINIMAX
from lib.video_backends.base import ReferenceAudioMode, VideoCapabilityError, VideoGenerationRequest
from lib.video_backends.minimax import MiniMaxVideoBackend, _safe_body_for_log
from lib.video_frame_slots import resolve_first_frame_aspect_ratio

pytestmark = pytest.mark.unit


def _resp(json_body: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock()
    return resp


def _submit(task_id: str = "t-1") -> dict:
    return {"task_id": task_id, "base_resp": {"status_code": 0, "status_msg": "success"}}


def _query(status: str, file_id: str = "", base_resp: dict | None = None) -> dict:
    body: dict = {
        "task_id": "t-1",
        "status": status,
        "base_resp": base_resp or {"status_code": 0, "status_msg": "success"},
    }
    if file_id:
        body["file_id"] = file_id
    return body


def _retrieve(url: str = "https://x/o.mp4") -> dict:
    return {"file": {"file_id": "f-1", "download_url": url}, "base_resp": {"status_code": 0}}


def _client(*, post=None, get=None) -> AsyncMock:
    c = AsyncMock()
    if post is not None:
        c.post = post
    if get is not None:
        c.get = get
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=None)
    return c


def _v2_query(status: str, *, url: str = "", error: str = "") -> dict:
    task: dict = {"id": "h3-task", "model": "MiniMax-H3", "status": status}
    if url:
        task["content"] = {"url": url}
    if error:
        task["error"] = error
    return {"task": task}


def _backend(model: str = "MiniMax-Hailuo-2.3") -> MiniMaxVideoBackend:
    return MiniMaxVideoBackend(api_key="sk-test", model=model)


def _h3() -> MiniMaxVideoBackend:
    return _backend("MiniMax-H3")


def _png(path: Path) -> Path:
    path.write_bytes(b"\x89PNG\r\n")
    return path


def _wav(path: Path) -> Path:
    path.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
    return path


def _request(tmp_path: Path, **overrides) -> VideoGenerationRequest:
    kwargs: dict = {
        "prompt": "a cat",
        "output_path": tmp_path / "out.mp4",
        "duration_seconds": 6,
        "resolution": "768p",
    }
    kwargs.update(overrides)
    return VideoGenerationRequest(**kwargs)


class TestConstructionAndCapabilities:
    def test_name_and_default_model(self):
        b = MiniMaxVideoBackend(api_key="k")
        assert b.name == PROVIDER_MINIMAX
        assert b.model == "MiniMax-H3"

    def test_video_capabilities_first_frame(self):
        assert _backend().video_capabilities.first_frame is True

    def test_missing_api_key_raises(self):
        with pytest.raises(ValueError):
            MiniMaxVideoBackend(api_key=None)

    @pytest.mark.parametrize("model", ["minimax-h3", "MINIMAX-H3", "proxy/minimax-h3"])
    def test_h3_dispatch_case_and_namespace_insensitive(self, tmp_path, model):
        # 中转站发现层（infer_endpoint）按大小写不敏感的 "minimax-h3" 子串把这些变体路由到
        # minimax-video；派发/能力声明须用同一判定，否则会出现"发现路由对了，派发却落回
        # v1"的裂缝——本用例锁定两处用的是同一谓词。
        b = _backend(model)
        payload = b._build_payload(_request(tmp_path))
        assert "content" in payload  # v2 payload 形态，v1 是扁平 dict 无此键
        assert b.video_capabilities.max_reference_images == 9


class TestPayloadAndCapabilityGating:
    def test_fast_t2v_rejected(self, tmp_path):
        # 2.3-Fast 无首帧（文生视频意图）→ 能力拒绝
        b = _backend("MiniMax-Hailuo-2.3-Fast")
        with pytest.raises(VideoCapabilityError) as exc:
            b._build_payload(_request(tmp_path, start_image=None))
        assert exc.value.code == "video_capability_missing_t2v"

    def test_hailuo_t2v_allowed(self, tmp_path):
        payload = _backend("MiniMax-Hailuo-2.3")._build_payload(_request(tmp_path, start_image=None))
        assert payload["model"] == "MiniMax-Hailuo-2.3"
        assert payload["resolution"] == "768P"
        assert payload["duration"] == 6
        assert "first_frame_image" not in payload

    def test_1080p_6s_allowed(self, tmp_path):
        payload = _backend()._build_payload(_request(tmp_path, resolution="1080p", duration_seconds=6))
        assert payload["resolution"] == "1080P"

    def test_1080p_10s_rejected(self, tmp_path):
        with pytest.raises(VideoCapabilityError) as exc:
            _backend()._build_payload(_request(tmp_path, resolution="1080p", duration_seconds=10))
        assert exc.value.code == "video_resolution_duration_unsupported"

    def test_768p_10s_allowed(self, tmp_path):
        payload = _backend()._build_payload(_request(tmp_path, resolution="768p", duration_seconds=10))
        assert payload["resolution"] == "768P"
        assert payload["duration"] == 10

    def test_unknown_resolution_rejected(self, tmp_path):
        with pytest.raises(VideoCapabilityError) as exc:
            _backend()._build_payload(_request(tmp_path, resolution="540p", duration_seconds=6))
        assert exc.value.code == "video_resolution_duration_unsupported"
        # params 会原样进 en/vi 文案，空集合兜底必须语言中性，否则非中文界面里露出中文。
        assert exc.value.params["supported"] == "-"

    def test_i2v_embeds_first_frame_data_uri(self, tmp_path):
        img = tmp_path / "first.png"
        img.write_bytes(b"\x89PNG\r\n")
        payload = _backend()._build_payload(_request(tmp_path, start_image=img))
        assert payload["first_frame_image"].startswith("data:image/png;base64,")

    def test_i2v_missing_first_frame_file_raises(self, tmp_path):
        with pytest.raises(VideoCapabilityError) as exc:
            _backend()._build_payload(_request(tmp_path, start_image=tmp_path / "nope.png"))
        assert exc.value.code == "video_start_image_unreadable"


class TestS2V01SubjectReference:
    """S2V-01 单脸参考生视频（R2V）：reference_images[0] → subject_reference 单脸结构。"""

    def test_caps_reference_single_no_first_frame(self):
        caps = _backend("S2V-01").video_capabilities
        assert caps.max_reference_images == 1
        # S2V-01 仅 subject_reference 驱动，不接受首帧图。
        assert caps.first_frame is False

    def test_payload_maps_reference_to_subject_reference(self, tmp_path):
        face = tmp_path / "face.png"
        face.write_bytes(b"\x89PNG\r\n")
        payload = _backend("S2V-01")._build_payload(_request(tmp_path, reference_images=[face]))
        assert payload["model"] == "S2V-01"
        assert payload["prompt"] == "a cat"
        subject = payload["subject_reference"]
        assert isinstance(subject, list) and len(subject) == 1
        assert subject[0]["type"] == "character"
        assert isinstance(subject[0]["image"], list) and len(subject[0]["image"]) == 1
        assert subject[0]["image"][0].startswith("data:image/png;base64,")
        # S2V-01 不接受 resolution/duration/first_frame_image。
        assert "resolution" not in payload
        assert "duration" not in payload
        assert "first_frame_image" not in payload

    def test_takes_only_first_reference(self, tmp_path):
        # 编排层已按 max_reference_images=1 裁剪；backend 防御性仅取首张。
        face1 = tmp_path / "face1.png"
        face2 = tmp_path / "face2.png"
        face1.write_bytes(b"\x89PNG\r\n1")
        face2.write_bytes(b"\x89PNG\r\n2")
        payload = _backend("S2V-01")._build_payload(_request(tmp_path, reference_images=[face1, face2]))
        assert len(payload["subject_reference"][0]["image"]) == 1

    def test_missing_reference_raises(self, tmp_path):
        b = _backend("S2V-01")
        with pytest.raises(VideoCapabilityError) as exc:
            b._build_payload(_request(tmp_path, reference_images=None))
        assert exc.value.code == "video_reference_images_required"

    def test_unreadable_reference_raises(self, tmp_path):
        b = _backend("S2V-01")
        with pytest.raises(VideoCapabilityError) as exc:
            b._build_payload(_request(tmp_path, reference_images=[tmp_path / "nope.png"]))
        assert exc.value.code == "video_reference_images_unreadable"

    async def test_generate_two_step_via_subject_reference(self, tmp_path):
        face = tmp_path / "face.png"
        face.write_bytes(b"\x89PNG\r\n")
        captured: dict = {}

        async def _post(url, json, headers):
            captured["json"] = json
            return _resp(_submit("s2v-task"))

        post = AsyncMock(side_effect=_post)
        get = AsyncMock(side_effect=[_resp(_query("Success", file_id="f")), _resp(_retrieve("https://x/s2v.mp4"))])
        client = _client(post=post, get=get)
        with (
            patch("lib.video_backends.minimax.httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.minimax.MINIMAX_VIDEO_POLL_INTERVAL_SECONDS", 0),
            patch("lib.video_backends.minimax.download_video", new=AsyncMock()),
        ):
            result = await _backend("S2V-01").generate(_request(tmp_path, reference_images=[face]))

        assert result.video_uri == "https://x/s2v.mp4"
        assert captured["json"]["model"] == "S2V-01"
        assert captured["json"]["subject_reference"][0]["type"] == "character"


class TestGenerateHappyPath:
    async def test_two_step_url_extraction(self, tmp_path):
        post = AsyncMock(return_value=_resp(_submit("task-9")))
        get = AsyncMock(
            side_effect=[
                _resp(_query("Processing")),
                _resp(_query("Success", file_id="file-9")),
                _resp(_retrieve("https://x/final.mp4")),
            ]
        )
        client = _client(post=post, get=get)
        with (
            patch("lib.video_backends.minimax.httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.minimax.MINIMAX_VIDEO_POLL_INTERVAL_SECONDS", 0),
            patch("lib.video_backends.minimax.download_video", new=AsyncMock()) as dl,
        ):
            result = await _backend().generate(_request(tmp_path, duration_seconds=10))

        assert result.provider == PROVIDER_MINIMAX
        assert result.task_id == "task-9"
        assert result.video_uri == "https://x/final.mp4"
        assert result.duration_seconds == 10
        dl.assert_awaited_once()
        # submit + 2 query + 1 retrieve
        assert get.await_count == 3

    async def test_fail_status_raises(self, tmp_path):
        post = AsyncMock(return_value=_resp(_submit()))
        get = AsyncMock(return_value=_resp(_query("Fail", base_resp={"status_code": 2013, "status_msg": "invalid"})))
        client = _client(post=post, get=get)
        with (
            patch("lib.video_backends.minimax.httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.minimax.MINIMAX_VIDEO_POLL_INTERVAL_SECONDS", 0),
            patch("lib.video_backends.minimax.download_video", new=AsyncMock()),
        ):
            with pytest.raises(RuntimeError, match="2013"):
                await _backend().generate(_request(tmp_path))

    async def test_persists_provider_job_id_when_task_id_present(self, tmp_path):
        post = AsyncMock(return_value=_resp(_submit("task-x")))
        get = AsyncMock(side_effect=[_resp(_query("Success", file_id="f")), _resp(_retrieve())])
        client = _client(post=post, get=get)
        with (
            patch("lib.video_backends.minimax.httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.minimax.MINIMAX_VIDEO_POLL_INTERVAL_SECONDS", 0),
            patch("lib.video_backends.minimax.download_video", new=AsyncMock()),
            patch("lib.video_backends.base.persist_provider_job_id", new=AsyncMock()) as persist,
        ):
            await _backend().generate(_request(tmp_path, task_id="local-task-1"))
        persist.assert_awaited_once()
        assert persist.await_args is not None
        assert persist.await_args.args[1] == "task-x"


class TestH3V2Capabilities:
    """H3 走 v2 多模态端点：能力声明按官方《创建视频生成任务 (V2)》逐维度锁定。"""

    def test_declared_capabilities(self):
        caps = MiniMaxVideoBackend.video_capabilities_for_model("MiniMax-H3")
        assert caps.first_frame is True
        assert caps.last_frame is True
        assert caps.max_reference_images == 9
        assert caps.reference_audio_mode is ReferenceAudioMode.DIRECT
        assert caps.max_reference_audio_count == 3
        assert caps.max_reference_audio_total_seconds == 15.0
        assert caps.max_prompt_chars == 7000
        assert caps.first_frame_ratio_adaptive_only is True

    def test_first_frame_ratio_resolves_to_adaptive(self):
        """不只断言声明位为真：走共享施加逻辑核实首帧任务实际拿到 adaptive。"""
        caps = MiniMaxVideoBackend.video_capabilities_for_model("MiniMax-H3")
        assert resolve_first_frame_aspect_ratio(caps=caps, aspect_ratio="16:9", has_first_frame=True) == "adaptive"
        assert resolve_first_frame_aspect_ratio(caps=caps, aspect_ratio="16:9", has_first_frame=False) == "16:9"

    def test_hailuo_capabilities_unchanged(self):
        caps = MiniMaxVideoBackend.video_capabilities_for_model("MiniMax-Hailuo-2.3")
        assert caps.first_frame is True
        assert caps.last_frame is False
        assert caps.max_reference_images == 0
        assert caps.first_frame_ratio_adaptive_only is False

    def test_base_url_uses_v2(self):
        assert _h3()._base_url.endswith("/v2")
        assert _backend()._base_url.endswith("/v1")


class TestH3V2Payload:
    def test_t2v_single_text_item(self, tmp_path):
        payload = _h3()._build_payload(_request(tmp_path, resolution="2k", duration_seconds=15, aspect_ratio="16:9"))
        assert payload["model"] == "MiniMax-H3"
        assert payload["resolution"] == "2K"
        assert payload["duration"] == 15
        assert payload["ratio"] == "16:9"
        assert payload["content"] == [{"type": "text", "text": "a cat"}]

    def test_i2v_first_and_last_frame_roles(self, tmp_path):
        first = _png(tmp_path / "first.png")
        last = _png(tmp_path / "last.png")
        payload = _h3()._build_payload(
            _request(tmp_path, start_image=first, end_image=last, aspect_ratio="adaptive", duration_seconds=4)
        )
        roles = [item.get("role") for item in payload["content"]]
        assert roles == [None, "first_frame", "last_frame"]
        assert payload["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
        assert payload["ratio"] == "adaptive"

    def test_r2v_reference_images_and_audio_keep_order(self, tmp_path):
        # role/type 断言之外逐项比对 data URI：素材顺序即 prompt 里「音频N」等指认编号的依据，
        # 集合/全量断言不会捕获反转，需按内容钉死输入与条目的对应关系。
        refs = [_png(tmp_path / f"ref{i}.png") for i in range(3)]
        audios = [_wav(tmp_path / f"a{i}.wav") for i in range(2)]
        payload = _h3()._build_payload(_request(tmp_path, reference_images=refs, reference_audio_files=audios))
        items = payload["content"]
        assert [item["type"] for item in items] == ["text"] + ["image_url"] * 3 + ["audio_url"] * 2
        assert [item["role"] for item in items[1:4]] == ["reference_image"] * 3
        assert [item["role"] for item in items[4:]] == ["reference_audio"] * 2
        expected_images = [f"data:image/png;base64,{base64.b64encode(p.read_bytes()).decode()}" for p in refs]
        assert [item["image_url"]["url"] for item in items[1:4]] == expected_images
        expected_audios = [f"data:audio/wav;base64,{base64.b64encode(p.read_bytes()).decode()}" for p in audios]
        assert [item["audio_url"]["url"] for item in items[4:]] == expected_audios

    def test_frames_and_references_are_mutually_exclusive(self, tmp_path):
        with pytest.raises(VideoCapabilityError) as exc:
            _h3()._build_payload(
                _request(tmp_path, start_image=_png(tmp_path / "f.png"), reference_images=[_png(tmp_path / "r.png")])
            )
        assert exc.value.code == "video_reference_images_with_frames_unsupported"

    @pytest.mark.parametrize("frame_field", ["start_image", "end_image"])
    def test_frames_and_reference_audio_are_mutually_exclusive(self, tmp_path, frame_field):
        # 参考音频同属参考生视频维度，与首/尾帧（任一）混合同样应在本地被拒，而非发出注定
        # 被上游拒绝的请求（官方口径：图生视频与多模态参考生视频互斥）。end_image 单独给出时
        # 互斥校验先于「尾帧须配首帧」校验命中，两个帧字段都要覆盖，回归才捕得到。
        frame = _png(tmp_path / f"{frame_field}.png")
        with pytest.raises(VideoCapabilityError) as exc:
            _h3()._build_payload(
                _request(
                    tmp_path,
                    reference_audio_files=[_wav(tmp_path / "a.wav")],
                    **{frame_field: frame},
                )
            )
        assert exc.value.code == "video_reference_images_with_frames_unsupported"

    def test_last_frame_without_first_frame_rejected(self, tmp_path):
        with pytest.raises(VideoCapabilityError) as exc:
            _h3()._build_payload(_request(tmp_path, end_image=_png(tmp_path / "l.png")))
        assert exc.value.code == "video_end_image_requires_start_image"

    @pytest.mark.parametrize(("resolution", "duration"), [("1080p", 6), ("768p", 3), ("2k", 16)])
    def test_out_of_range_specs_rejected(self, tmp_path, resolution, duration):
        with pytest.raises(VideoCapabilityError) as exc:
            _h3()._build_payload(_request(tmp_path, resolution=resolution, duration_seconds=duration))
        assert exc.value.code == "video_resolution_duration_unsupported"

    def test_output_specs_follow_registry_declaration(self, tmp_path):
        # 分辨率档与时长的真相源在 registry：改声明即改放行范围，backend 不另存一份。
        stub = MagicMock(resolutions=["4k"], supported_durations=[7])
        with patch("lib.video_backends.minimax.model_info_for", return_value=stub):
            assert _h3()._build_payload(_request(tmp_path, resolution="4k", duration_seconds=7))["duration"] == 7
            with pytest.raises(VideoCapabilityError) as exc:
                _h3()._build_payload(_request(tmp_path, resolution="768p", duration_seconds=6))
        assert exc.value.code == "video_resolution_duration_unsupported"

    def test_unreadable_first_frame_raises(self, tmp_path):
        with pytest.raises(VideoCapabilityError) as exc:
            _h3()._build_payload(_request(tmp_path, start_image=tmp_path / "nope.png"))
        assert exc.value.code == "video_start_image_unreadable"

    def test_unsupported_audio_format_raises(self, tmp_path):
        bad = tmp_path / "a.ogg"
        bad.write_bytes(b"OggS")
        with pytest.raises(VideoCapabilityError) as exc:
            _h3()._build_payload(_request(tmp_path, reference_audio_files=[bad]))
        assert exc.value.code == "video_reference_audio_format_unsupported"

    def test_safe_log_view_folds_content(self, tmp_path):
        payload = _h3()._build_payload(_request(tmp_path, start_image=_png(tmp_path / "f.png")))
        view = _safe_body_for_log(payload)
        assert view["content"] == "<1 image_url, 1 text>"
        assert view["prompt"] == "a cat"
        assert "base64" not in str(view)


class TestH3V2Generate:
    async def test_single_step_url_extraction(self, tmp_path):
        captured: dict = {}

        async def _post(url, json, headers):
            captured["url"] = url
            captured["json"] = json
            return _resp(_submit("h3-task"))

        async def _get(url, params=None, headers=None):
            captured["query_url"] = url
            captured["query_params"] = params
            return _resp(_v2_query("succeeded", url="https://x/h3.mp4"))

        client = _client(post=AsyncMock(side_effect=_post), get=AsyncMock(side_effect=_get))
        with (
            patch("lib.video_backends.minimax.httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.minimax.MINIMAX_VIDEO_POLL_INTERVAL_SECONDS", 0),
            patch("lib.video_backends.minimax.download_video", new=AsyncMock()) as dl,
        ):
            result = await _h3().generate(_request(tmp_path, duration_seconds=5))

        assert captured["url"].endswith("/v2/video_generation")
        # v2 把 task_id 放路径段，且成功响应直接带下载地址，无 files/retrieve 这一步。
        assert captured["query_url"].endswith("/v2/query/video_generation/h3-task")
        assert captured["query_params"] is None
        assert result.video_uri == "https://x/h3.mp4"
        assert result.task_id == "h3-task"
        dl.assert_awaited_once()

    async def test_running_status_keeps_polling(self, tmp_path):
        get = AsyncMock(side_effect=[_resp(_v2_query("running")), _resp(_v2_query("succeeded", url="https://x/o.mp4"))])
        client = _client(post=AsyncMock(return_value=_resp(_submit())), get=get)
        with (
            patch("lib.video_backends.minimax.httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.minimax.MINIMAX_VIDEO_POLL_INTERVAL_SECONDS", 0),
            patch("lib.video_backends.minimax.download_video", new=AsyncMock()),
        ):
            result = await _h3().generate(_request(tmp_path))
        assert get.await_count == 2
        assert result.video_uri == "https://x/o.mp4"

    @pytest.mark.parametrize("status", ["failed", "cancelled"])
    async def test_terminal_failure_statuses_raise(self, tmp_path, status):
        client = _client(
            post=AsyncMock(return_value=_resp(_submit())),
            get=AsyncMock(return_value=_resp(_v2_query(status, error="quota exhausted"))),
        )
        with (
            patch("lib.video_backends.minimax.httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.minimax.MINIMAX_VIDEO_POLL_INTERVAL_SECONDS", 0),
            patch("lib.video_backends.minimax.download_video", new=AsyncMock()),
        ):
            with pytest.raises(RuntimeError, match="quota exhausted"):
                await _h3().generate(_request(tmp_path))

    async def test_resume_polls_v2_endpoint(self, tmp_path):
        post = AsyncMock()  # must NOT be called
        get = AsyncMock(return_value=_resp(_v2_query("succeeded", url="https://x/r2.mp4")))
        client = _client(post=post, get=get)
        with (
            patch("lib.video_backends.minimax.httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.minimax.MINIMAX_VIDEO_POLL_INTERVAL_SECONDS", 0),
            patch("lib.video_backends.minimax.download_video", new=AsyncMock()),
        ):
            result = await _h3().resume_video("h3-resume", _request(tmp_path))

        post.assert_not_called()
        assert get.await_args is not None
        assert get.await_args.args[0].endswith("/v2/query/video_generation/h3-resume")
        assert result.video_uri == "https://x/r2.mp4"


class TestResume:
    async def test_resume_polls_without_resubmit(self, tmp_path):
        post = AsyncMock()  # must NOT be called
        get = AsyncMock(side_effect=[_resp(_query("Success", file_id="f-r")), _resp(_retrieve("https://x/r.mp4"))])
        client = _client(post=post, get=get)
        with (
            patch("lib.video_backends.minimax.httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.minimax.MINIMAX_VIDEO_POLL_INTERVAL_SECONDS", 0),
            patch("lib.video_backends.minimax.download_video", new=AsyncMock()) as dl,
        ):
            result = await _backend().resume_video("task-resume", _request(tmp_path))

        post.assert_not_called()
        assert result.task_id == "task-resume"
        assert result.video_uri == "https://x/r.mp4"
        dl.assert_awaited_once()
