"""MiniMaxVideoBackend 单元测试（mock httpx，异步两步取 URL，不打真实 HTTP）。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.providers import PROVIDER_MINIMAX
from lib.video_backends.base import VideoCapability, VideoCapabilityError, VideoGenerationRequest
from lib.video_backends.minimax import MiniMaxVideoBackend


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


def _backend(model: str = "MiniMax-Hailuo-2.3") -> MiniMaxVideoBackend:
    return MiniMaxVideoBackend(api_key="sk-test", model=model)


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
        assert b.model == "MiniMax-Hailuo-2.3"

    def test_hailuo_supports_t2v_and_i2v(self):
        caps = _backend("MiniMax-Hailuo-2.3").capabilities
        assert VideoCapability.TEXT_TO_VIDEO in caps
        assert VideoCapability.IMAGE_TO_VIDEO in caps

    def test_fast_supports_only_i2v(self):
        caps = _backend("MiniMax-Hailuo-2.3-Fast").capabilities
        assert VideoCapability.TEXT_TO_VIDEO not in caps
        assert VideoCapability.IMAGE_TO_VIDEO in caps

    def test_video_capabilities_first_frame(self):
        assert _backend().video_capabilities.first_frame is True

    def test_missing_api_key_raises(self):
        with pytest.raises(ValueError):
            MiniMaxVideoBackend(api_key=None)


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
        assert caps.reference_images is True
        assert caps.max_reference_images == 1
        # S2V-01 仅 subject_reference 驱动，不接受首帧图。
        assert caps.first_frame is False

    def test_capability_set_excludes_t2v_and_i2v(self):
        caps = _backend("S2V-01").capabilities
        assert VideoCapability.TEXT_TO_VIDEO not in caps
        assert VideoCapability.IMAGE_TO_VIDEO not in caps

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
            patch("lib.video_backends.minimax.persist_provider_job_id", new=AsyncMock()) as persist,
        ):
            await _backend().generate(_request(tmp_path, task_id="local-task-1"))
        persist.assert_awaited_once()
        assert persist.await_args is not None
        assert persist.await_args.args[1] == "task-x"


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
