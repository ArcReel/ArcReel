"""V2VideoGenerationsBackend 纯函数单测（请求体映射 / 状态归一 / 多路径提取）。

只测外部可观察行为与纯函数，不跑真实 HTTP。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.video_backends.base import VideoGenerationRequest
from lib.video_backends.v2_video_generations import (
    _TASK_ID_PATHS,
    _VIDEO_URL_PATHS,
    _dig,
    _extract_failure,
    _first_str_by_paths,
    _normalize_root,
    build_request_body,
    normalize_status,
)


def _req(tmp_path: Path, **kwargs) -> VideoGenerationRequest:
    base = {"prompt": "a cat", "output_path": tmp_path / "out.mp4"}
    base.update(kwargs)
    return VideoGenerationRequest(**base)


def _write_img(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_bytes(b"\x89PNG\r\n\x1a\n fake bytes")
    return p


class TestNormalizeStatus:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            # aimlapi 官方枚举
            ("queued", "queued"),
            ("generating", "running"),
            ("completed", "succeeded"),
            ("error", "failed"),
            # 跨厂商同义词（流派 C 路由到多家时底层串可能透传）
            ("succeed", "succeeded"),  # Kling
            ("Success", "succeeded"),  # MiniMax 首字母大写
            ("Fail", "failed"),
            ("expired", "failed"),
            ("canceled", "failed"),
            ("in_progress", "running"),  # Sora
            ("Processing", "running"),  # Kling
            ("PENDING", "queued"),  # DashScope 全大写
            ("Queueing", "queued"),  # MiniMax
            ("submitted", "queued"),  # Kling
            ("  COMPLETED  ", "succeeded"),  # 大小写 + 空白
            # 未知 / 非字符串 → 当 running 继续轮询
            ("weird-status", "running"),
            (None, "running"),
            (99, "running"),
        ],
    )
    def test_normalize(self, raw, expected):
        assert normalize_status(raw) == expected


class TestDig:
    def test_walks_dict_and_list_index(self):
        payload = {"data": {"task_result": {"videos": [{"url": "u0"}, {"url": "u1"}]}}}
        assert _dig(payload, ("data", "task_result", "videos", 0, "url")) == "u0"

    def test_missing_key_returns_none(self):
        assert _dig({"a": 1}, ("a", "b")) is None

    def test_list_index_out_of_range_returns_none(self):
        assert _dig({"v": []}, ("v", 0)) is None

    def test_type_mismatch_returns_none(self):
        assert _dig({"v": "str"}, ("v", 0)) is None  # 期望 list 实为 str


class TestVideoUrlExtraction:
    @pytest.mark.parametrize(
        "payload,expected",
        [
            ({"id": "g", "status": "completed", "video": {"url": "https://cdn/v.mp4"}}, "https://cdn/v.mp4"),
            ({"assets": {"video": "https://a/v.mp4"}}, "https://a/v.mp4"),
            ({"output": {"video_url": "https://w/v.mp4"}}, "https://w/v.mp4"),
            ({"content": {"video_url": "https://s/v.mp4"}}, "https://s/v.mp4"),
            ({"data": {"task_result": {"videos": [{"url": "https://k/v.mp4"}]}}}, "https://k/v.mp4"),
            ({"url": "https://n/v.mp4"}, "https://n/v.mp4"),
        ],
    )
    def test_extracts_first_match(self, payload, expected):
        assert _first_str_by_paths(payload, _VIDEO_URL_PATHS) == expected

    def test_priority_video_url_wins_over_bare_url(self):
        payload = {"video": {"url": "https://primary/v.mp4"}, "url": "https://fallback/v.mp4"}
        assert _first_str_by_paths(payload, _VIDEO_URL_PATHS) == "https://primary/v.mp4"

    def test_all_miss_returns_none(self):
        assert _first_str_by_paths({"foo": "bar"}, _VIDEO_URL_PATHS) is None

    def test_empty_string_skipped(self):
        payload = {"video": {"url": "   "}, "url": "https://fallback/v.mp4"}
        assert _first_str_by_paths(payload, _VIDEO_URL_PATHS) == "https://fallback/v.mp4"


class TestTaskIdExtraction:
    @pytest.mark.parametrize(
        "payload,expected",
        [
            ({"id": "gen_1"}, "gen_1"),
            ({"task_id": "t1"}, "t1"),
            ({"data": {"task_id": "d1"}}, "d1"),
            ({"request_id": "r1"}, "r1"),
            ({"data": {"taskId": "dt1"}}, "dt1"),
            ({"id": 123}, "123"),  # int 容忍并 str 化
        ],
    )
    def test_extracts(self, payload, expected):
        assert _first_str_by_paths(payload, _TASK_ID_PATHS) == expected

    def test_priority_id_wins(self):
        assert _first_str_by_paths({"id": "primary", "task_id": "secondary"}, _TASK_ID_PATHS) == "primary"


class TestBuildRequestBody:
    def test_text_to_video_minimal(self, tmp_path):
        body = build_request_body("kling-v2", _req(tmp_path, duration_seconds=8))
        assert body == {"model": "kling-v2", "prompt": "a cat", "duration": 8}

    def test_includes_seed_and_resolution(self, tmp_path):
        body = build_request_body("m", _req(tmp_path, seed=42, resolution="720p"))
        assert body["seed"] == 42
        assert body["resolution"] == "720p"

    def test_start_image_to_image_url(self, tmp_path):
        img = _write_img(tmp_path, "start.png")
        body = build_request_body("m", _req(tmp_path, start_image=img))
        assert body["image_url"].startswith("data:image/png;base64,")

    def test_end_image_to_last_image_url(self, tmp_path):
        start = _write_img(tmp_path, "start.png")
        end = _write_img(tmp_path, "end.png")
        body = build_request_body("m", _req(tmp_path, start_image=start, end_image=end))
        assert body["last_image_url"].startswith("data:image/png;base64,")

    def test_reference_images_to_image_urls(self, tmp_path):
        refs = [_write_img(tmp_path, "r1.png"), _write_img(tmp_path, "r2.png")]
        body = build_request_body("m", _req(tmp_path, reference_images=refs))
        assert isinstance(body["image_urls"], list)
        assert len(body["image_urls"]) == 2
        assert all(u.startswith("data:image/png;base64,") for u in body["image_urls"])

    def test_missing_image_file_omitted(self, tmp_path):
        body = build_request_body("m", _req(tmp_path, start_image=tmp_path / "nope.png"))
        assert "image_url" not in body


class TestExtractFailure:
    def test_succeeded_returns_none(self):
        assert _extract_failure({"status": "completed", "video": {"url": "u"}}) is None

    def test_running_returns_none(self):
        assert _extract_failure({"status": "generating"}) is None

    def test_error_dict_message(self):
        msg = _extract_failure({"status": "error", "error": {"message": "boom", "name": "E"}})
        assert msg is not None and "boom" in msg

    def test_error_string(self):
        msg = _extract_failure({"status": "failed", "error": "explicit reason"})
        assert msg is not None and "explicit reason" in msg

    def test_error_without_detail(self):
        msg = _extract_failure({"status": "error"})
        assert msg is not None and "unknown" in msg


class TestNormalizeRoot:
    @pytest.mark.parametrize(
        "base_url,expected",
        [
            ("https://api.aimlapi.com", "https://api.aimlapi.com"),
            ("https://api.aimlapi.com/", "https://api.aimlapi.com"),
            ("https://api.aimlapi.com/v1", "https://api.aimlapi.com"),
            ("https://api.aimlapi.com/v2", "https://api.aimlapi.com"),
            ("https://api.aimlapi.com/v1beta", "https://api.aimlapi.com"),
        ],
    )
    def test_strips_version_suffix(self, base_url, expected):
        assert _normalize_root(base_url) == expected
