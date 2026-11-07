"""KlingVideoBackend tests, with respx intercepting transport and no real HTTP.

Cover JWT/Bearer authentication injection, text2video/image2video subpaths, payload construction,
sanitized logs, submit/poll/download end-to-end, persisted provider_job_id/submission domains,
terminal failures, resume without resubmission, resumed 404 expiry, replaying submission domains,
and outbound-destination gates for creation/polling.
"""

from __future__ import annotations

import re
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import NamedTuple

import httpx
import jwt
import pytest
import respx

from arcreel_market_core.video_backend_contract import (
    ResumeExpiredError,
    VideoAudioMode,
    VideoCapabilities,
    VideoCapabilityError,
    VideoGenerationRequest,
)
from lib.backends.artifact_download_guard import ArtifactDestinationRejectedError
from lib.backends.backend_assembly.specs import builtin_model_endpoint
from lib.backends.http_status_errors import ArtifactDownloadError
from lib.backends.providers import PROVIDER_KLING
from lib.backends.video_backends.kling import KLING_BACKENDS, KlingVideoBackend
from lib.backends.video_backends.registry import effective_generate_audio_for_model
from tests.fakes import bounded_poll_clock, captured_provider_job_ids
from tests.http_capture import capture_http, only_request

_SECRET = "s" * 40
_BASE_URL = "https://api-beijing.klingai.com/v1"
_DOWNLOAD_URL = "https://x/final.mp4"


class _KlingRoutes(NamedTuple):
    """Kling outbound traffic: task creation with variable subpaths, polling, and final-video downloads."""

    submit: respx.Route
    poll: respx.Route
    download: respx.Route


@contextmanager
def _kling_api() -> Generator[_KlingRoutes]:
    videos = re.escape(f"{_BASE_URL}/videos")
    with capture_http() as router:
        yield _KlingRoutes(
            submit=router.post(url__regex=rf"^{videos}/[^/]+$"),
            poll=router.get(url__regex=rf"^{videos}/[^/]+/[^/]+$"),
            download=router.get(url__regex=r"^https://x/"),
        )


def _resp(json_body: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=json_body)


def _submit(task_id: str = "t-1") -> dict:
    return {"code": 0, "message": "SUCCEED", "data": {"task_id": task_id, "task_status": "submitted"}}


def _query(status: str, url: str = "", status_msg: str = "") -> dict:
    data: dict = {"task_id": "t-1", "task_status": status, "task_status_msg": status_msg}
    if url:
        data["task_result"] = {"videos": [{"id": "v1", "url": url}]}
    return {"code": 0, "message": "SUCCEED", "data": data}


def _jwt_backend(model: str | None = None) -> KlingVideoBackend:
    return KLING_BACKENDS[builtin_model_endpoint("kling", "video", model or "kling-v2-5-turbo")](
        auth_mode="jwt", access_key="ak-1", secret_key=_SECRET, model=model
    )


def _bearer_backend(model: str | None = None, *, endpoint: str | None = None) -> KlingVideoBackend:
    return KLING_BACKENDS[endpoint or builtin_model_endpoint("kling", "video", model or "kling-v2-5-turbo")](
        auth_mode="bearer", api_key="static-key", model=model
    )


def _effective(model: str, service_tier: str = "std") -> VideoCapabilities:
    """Effective built-in Kling model capabilities by tier: evaluate tier-aware channel declarations and catalog entries together (docs/adr/0104)."""
    from lib.backends.backend_assembly.specs import effective_builtin_video_capabilities

    channel = _jwt_backend(model).video_capabilities_for_tier(service_tier)
    return effective_builtin_video_capabilities(channel, "kling", model)


def _request(tmp_path: Path, **overrides) -> VideoGenerationRequest:
    kwargs: dict = {
        "prompt": "a cat walking",
        "output_path": tmp_path / "out.mp4",
        "duration_seconds": 5,
        "aspect_ratio": "9:16",
    }
    kwargs.update(overrides)
    return VideoGenerationRequest(**kwargs)


class TestConstructionAndCapabilities:
    def test_name_and_default_model(self):
        b = _jwt_backend()
        assert b.name == PROVIDER_KLING
        assert b.model == "kling-v2-5-turbo"

    def test_jwt_missing_credentials_raises(self):
        with pytest.raises(ValueError, match=r"请到系统配置页填写可灵 Kling 的 Access Key 与 Secret Key"):
            KlingVideoBackend(auth_mode="jwt", access_key="ak", secret_key=None)

    def test_bearer_missing_api_key_raises(self):
        with pytest.raises(ValueError, match=r"请填写可灵 Kling 的 API Key"):
            KlingVideoBackend(auth_mode="bearer", api_key=None)

    def test_unknown_auth_mode_raises(self):
        with pytest.raises(ValueError, match=r"未知 Kling auth_mode"):
            KlingVideoBackend(auth_mode="oauth", api_key="k")

    def test_video_capabilities_first_and_last_frame(self):
        caps = _jwt_backend().video_capabilities
        assert caps.first_frame is True
        # turbo last frames work only in pro; default-tier declarations conservatively say False (std submission fails _build_payload).
        assert caps.last_frame is False
        # Multi-image-subject subpath limits are model-independent; turbo reference rejection comes from explicit catalog 0.
        assert caps.max_reference_images == 4
        assert _effective("kling-v2-5-turbo").max_reference_images == 0


class TestVideoCapabilitiesForTier:
    """Narrow last_frame with request context/service_tier, before media_generator forwards end_image."""

    def test_pro_tier_allows_last_frame_for_gated_model(self):
        caps = _jwt_backend().video_capabilities_for_tier("pro")
        assert caps.last_frame is True

    def test_std_tier_still_conservative(self):
        caps = _jwt_backend().video_capabilities_for_tier("std")
        assert caps.last_frame is False

    def test_pro_tier_case_insensitive(self):
        caps = _jwt_backend().video_capabilities_for_tier("PRO")
        assert caps.last_frame is True

    def test_ungated_model_last_frame_unaffected_by_tier(self):
        # kling-v3 last_frame is true across tiers, unaffected by last_frame_requires_pro.
        b = _jwt_backend("kling-v3")
        assert b.video_capabilities_for_tier("std").last_frame is True
        assert b.video_capabilities_for_tier("pro").last_frame is True

    def test_4k_tier_still_conservative(self):
        caps = _jwt_backend().video_capabilities_for_tier("std", resolution="4k")
        assert caps.last_frame is False

    def test_4k_overrides_pro_tier_stays_conservative(self):
        # resolution=4k outranks service_tier, matching _resolve_mode: even with tier=pro,
        # 4k resolves mode "4k", not "pro"; conservatively reject last_frame or disagree with
        # _build_payload fail-loud guards using _resolve_mode.
        caps = _jwt_backend().video_capabilities_for_tier("pro", resolution="4k")
        assert caps.last_frame is False


class TestPerModelCapabilities:
    """Model input capabilities come from entries, combined with channel declarations (four-image subject limit and last-frame tier constraints)."""

    def test_v3_t2v_i2v_no_audio_no_reference(self):
        vc = _effective("kling-v3")
        assert vc.first_frame is True
        assert vc.last_frame is True
        assert vc.max_reference_images == 0

    def test_v3_omni_declares_reference_images(self):
        # Entry allows 7; channel multi-image-subject subpath holds 4, so take the lower limit.
        assert _effective("kling-v3-omni").max_reference_images == 4

    def test_v2_6_declares_generate_audio_no_reference(self):
        assert _effective("kling-v2-6").max_reference_images == 0

    def test_v2_5_turbo_last_frame_opens_only_on_pro_tier(self):
        """Entries allow turbo last frames; older-model pro-only last frames are channel tier constraints. Both must permit them."""
        assert _effective("kling-v2-5-turbo", "std").last_frame is False
        assert _effective("kling-v2-5-turbo", "pro").last_frame is True

    def test_video_o1_i2v_only_with_reference_images(self):
        # Multi-image-subject R2V
        vc = _effective("kling-video-o1")
        assert vc.text_to_video is False
        assert vc.last_frame is True
        assert vc.max_reference_images == 4

    def test_video_o1_pricing_audio_matches_effective_request(self, tmp_path):
        """Estimation’s backend-capability interface and actual-request `_effective_audio` choose the same silent tier for reference models."""
        backend = _jwt_backend("kling-video-o1")
        request = _request(tmp_path, generate_audio=True)

        assert effective_generate_audio_for_model("kling", "kling-video-o1") is False
        assert backend._effective_audio(request, subpath="text2video") is False

    def test_unknown_model_gets_the_channel_declaration(self):
        # bearer passes native model_name: unknown models use channel declarations, never name-guessed capabilities; unmatched last frames are disabled by joint evaluation.
        vc = _bearer_backend("kling-some-passthrough").video_capabilities
        assert vc.text_to_video is True
        assert vc.first_frame is True
        assert vc.max_reference_images == 4

    def test_the_same_endpoint_keeps_its_capabilities_when_the_model_name_changes(self):
        # Selected endpoints determine request shapes; relay model names do not alter channel capabilities.
        registered = _bearer_backend("kling-v3-omni").video_capabilities
        for model in ("vendor/Kling-V3-Omni", "provider:kling-v3-omni", "  provider:kling-v3-omni  "):
            assert _bearer_backend(model, endpoint="kling-video-audio").video_capabilities == registered

    def test_future_version_does_not_inherit_caps_by_substring(self):
        # Unknown names such as kling-v3-omni-pro must not inherit known request shapes through substring matches; use channel declarations.
        unlisted = _bearer_backend("kling-some-passthrough").video_capabilities
        for model in ("kling-v4", "kling-v3-omni-pro"):
            assert _bearer_backend(model).video_capabilities == unlisted


class TestModeAndResolution:
    def test_resolution_4k_maps_to_mode_4k(self, tmp_path):
        _, payload = _jwt_backend("kling-v3")._build_payload(_request(tmp_path, resolution="4k"))
        assert payload["mode"] == "4k"

    def test_resolution_4k_case_insensitive(self, tmp_path):
        _, payload = _jwt_backend("kling-v3-omni")._build_payload(_request(tmp_path, resolution="4K"))
        assert payload["mode"] == "4k"

    def test_4k_overrides_service_tier(self, tmp_path):
        # 4k outranks std/pro, matching per_second_tiered derivation.
        _, payload = _jwt_backend("kling-v3")._build_payload(_request(tmp_path, resolution="4k", service_tier="pro"))
        assert payload["mode"] == "4k"

    def test_non_4k_resolution_keeps_service_tier_mode(self, tmp_path):
        _, payload = _jwt_backend("kling-v3")._build_payload(_request(tmp_path, resolution="1080p", service_tier="pro"))
        assert payload["mode"] == "pro"


class TestAudioGating:
    def test_v2_6_pro_audio_enabled(self, tmp_path):
        _, payload = _jwt_backend("kling-v2-6")._build_payload(
            _request(tmp_path, resolution="1080p", service_tier="pro", generate_audio=True)
        )
        assert payload["sound"] == "on"

    def test_v2_6_std_audio_forced_off(self, tmp_path):
        # Official v2-6 audio requires 1080P; Kling requests this only through mode. 720p resolves std, without 1080P,
        # so disable audio rather than billing audible prices for silent output.
        _, payload = _jwt_backend("kling-v2-6")._build_payload(
            _request(tmp_path, resolution="720p", service_tier="std", generate_audio=True)
        )
        assert "sound" not in payload

    def test_1080p_resolves_pro_mode_and_enables_v2_6_audio(self, tmp_path):
        # 1080p selects pro without service_tier, which production never writes.
        _, payload = _jwt_backend("kling-v2-6")._build_payload(
            _request(tmp_path, resolution="1080p", service_tier="std", generate_audio=True)
        )
        assert payload["mode"] == "pro"
        assert payload["sound"] == "on"

    def test_1080p_metered_as_pro_tier(self, tmp_path):
        usage = KlingVideoBackend.metered_usage("kling-v2-6", _request(tmp_path, resolution="1080p"))
        assert usage.dimensions["resolution"] == "1080p"

    def test_v2_6_audio_gate_ignores_request_resolution(self, tmp_path):
        # request.resolution does not enter payloads except 4k, so cannot determine 1080P output:
        # pro already means 1080P and remains audible even with resolution 720p.
        _, payload = _jwt_backend("kling-v2-6")._build_payload(
            _request(tmp_path, resolution="720p", service_tier="pro", generate_audio=True)
        )
        assert payload["sound"] == "on"

    def test_v2_6_4k_audio_forced_off(self, tmp_path):
        # 4k is v3-only and invalid for v2-6; never admit it as satisfying 1080P and bill audio.
        _, payload = _jwt_backend("kling-v2-6")._build_payload(
            _request(tmp_path, resolution="4k", service_tier="pro", generate_audio=True)
        )
        assert "sound" not in payload

    def test_v3_audio_enabled_without_tier_constraint(self, tmp_path):
        # Official v3 lists no resolution/tier audio constraint; enable requested audio without undocumented downgrades.
        for model in ("kling-v3", "kling-v3-omni"):
            _, payload = _jwt_backend(model)._build_payload(
                _request(tmp_path, resolution="720p", service_tier="std", generate_audio=True)
            )
            assert payload["sound"] == "on"

    def test_audio_capable_model_sends_off_when_not_requested(self, tmp_path):
        # Audio-capable but audio unwanted: explicitly send "off", matching official defaults.
        _, payload = _jwt_backend("kling-v3")._build_payload(_request(tmp_path, generate_audio=False))
        assert payload["sound"] == "off"

    def test_no_audio_model_omits_sound_field(self, tmp_path):
        # Audio-incapable tiers omit sound, avoiding unknown parameters on unsupported endpoints.
        img = tmp_path / "start.png"
        img.write_bytes(b"\x89PNG\r\n")
        for backend in (_jwt_backend(), _jwt_backend("kling-video-o1")):
            _, payload = backend._build_payload(_request(tmp_path, start_image=img, generate_audio=True))
            assert "sound" not in payload


class TestMultiImageSubpath:
    @staticmethod
    def _refs(tmp_path: Path, n: int) -> list[Path]:
        paths: list[Path] = []
        for i in range(n):
            p = tmp_path / f"ref{i}.png"
            p.write_bytes(b"\x89PNG\r\n" + bytes([i]))
            paths.append(p)
        return paths

    def test_reference_images_select_multi_image2video(self, tmp_path):
        refs = self._refs(tmp_path, 2)
        subpath, payload = _jwt_backend("kling-v3-omni")._build_payload(_request(tmp_path, reference_images=refs))
        assert subpath == "multi-image2video"
        # image_list is [{"image": <base64>}], without a single first frame.
        assert isinstance(payload["image_list"], list)
        assert len(payload["image_list"]) == 2
        assert all(set(e) == {"image"} and isinstance(e["image"], str) and e["image"] for e in payload["image_list"])
        assert not payload["image_list"][0]["image"].startswith("data:")
        assert "image" not in payload
        assert "image_tail" not in payload

    def test_multi_image2video_omits_sound(self, tmp_path):
        refs = self._refs(tmp_path, 1)
        _, payload = _jwt_backend("kling-v3-omni")._build_payload(
            _request(tmp_path, reference_images=refs, resolution="1080p", generate_audio=True)
        )
        # Native multi-image2video schema has no audio switch; effective audio must also be False to avoid audible billing.
        assert "sound" not in payload
        assert (
            _jwt_backend("kling-v3-omni")._effective_audio(
                _request(tmp_path, reference_images=refs, resolution="1080p", generate_audio=True),
                subpath="multi-image2video",
            )
            is False
        )

    def test_reference_route_declares_no_audio_switch(self):
        """Capabilities and multi-image2video payloads share one source: reference-to-video truthfully exposes no audio switch.

        Settings disables its audio switch and queue preflight reads this same declaration; separate
        implementations would admit a switch guaranteed discarded at execution, producing silent
        videos despite users enabling audio.
        """
        caps = KLING_BACKENDS["kling-video-audio"].video_capabilities_for_model("kling-v3-omni")
        assert caps.audio_track_for_route("i2v") is VideoAudioMode.CONTROLLABLE
        assert caps.audio_track_for_route("r2v") is VideoAudioMode.ALWAYS_OFF

    def test_tier_aware_capabilities_keep_reference_route_audio_declaration(self):
        """Tier-narrowed queries retain the same audio declaration, preserving subpath distinctions across query paths."""
        caps = _jwt_backend("kling-v3-omni").video_capabilities_for_tier("pro", resolution="1080p")
        assert caps.audio_track_for_route("i2v") is VideoAudioMode.CONTROLLABLE
        assert caps.audio_track_for_route("r2v") is VideoAudioMode.ALWAYS_OFF

    @pytest.mark.parametrize("frame", ["start_image", "end_image"])
    def test_reference_images_with_frames_rejected(self, tmp_path, frame):
        """Multi-image-subject payloads lack first/last-frame fields; reject frame-bearing requests rather than sending references only."""
        refs = self._refs(tmp_path, 1)
        image = tmp_path / "frame.png"
        image.write_bytes(b"\x89PNG\r\n")
        with pytest.raises(VideoCapabilityError) as exc:
            _jwt_backend("kling-video-o1")._build_payload(_request(tmp_path, reference_images=refs, **{frame: image}))
        assert exc.value.code == "video_reference_images_with_frames_unsupported"

    def test_empty_reference_images_falls_through(self, tmp_path):
        subpath, _ = _jwt_backend("kling-v3-omni")._build_payload(_request(tmp_path, reference_images=[]))
        assert subpath == "text2video"

    def test_unreadable_reference_image_raises(self, tmp_path):
        with pytest.raises(VideoCapabilityError) as exc:
            _jwt_backend("kling-v3-omni")._build_payload(
                _request(tmp_path, reference_images=[tmp_path / "missing.png"])
            )
        assert exc.value.code == "video_start_image_unreadable"


class TestCapabilityValidation:
    """Generation defense: incompatible capabilities fail loudly before predictably erroneous but still billable calls."""

    @staticmethod
    def _refs(tmp_path: Path, n: int) -> list[Path]:
        paths: list[Path] = []
        for i in range(n):
            p = tmp_path / f"ref{i}.png"
            p.write_bytes(b"\x89PNG\r\n" + bytes([i]))
            paths.append(p)
        return paths

    def test_reference_images_on_unsupported_model_raises(self, tmp_path):
        # kling-v3 entries reject references; never silently upgrade such requests to multi-image2video.
        with pytest.raises(VideoCapabilityError) as exc:
            _jwt_backend("kling-v3")._build_payload(
                _request(
                    tmp_path,
                    reference_images=self._refs(tmp_path, 1),
                    video_capabilities=_effective("kling-v3"),
                )
            )
        assert exc.value.code == "video_reference_images_unsupported"

    def test_reference_images_over_limit_raises(self, tmp_path):
        # v3-omni limit 4: reject 5 images.
        with pytest.raises(VideoCapabilityError) as exc:
            _jwt_backend("kling-v3-omni")._build_payload(_request(tmp_path, reference_images=self._refs(tmp_path, 5)))
        assert exc.value.code == "video_reference_images_exceeded"
        assert exc.value.params["limit"] == 4
        assert exc.value.params["count"] == 5

    def test_text2video_on_model_without_t2v_raises(self, tmp_path):
        # kling-video-o1 entries reject text-to-video: no first frame/references means rejection, never text2video fallback.
        with pytest.raises(VideoCapabilityError) as exc:
            _jwt_backend("kling-video-o1")._build_payload(
                _request(tmp_path, video_capabilities=_effective("kling-video-o1"))
            )
        assert exc.value.code == "video_capability_missing_t2v"

    def test_reference_at_limit_allowed(self, tmp_path):
        # Exactly 4 references, at the limit, are admitted.
        subpath, payload = _jwt_backend("kling-v3-omni")._build_payload(
            _request(tmp_path, reference_images=self._refs(tmp_path, 4))
        )
        assert subpath == "multi-image2video"
        assert len(payload["image_list"]) == 4


class TestUserRelaxedCapabilities:
    """Requests carrying execution-gate capabilities with user overrides send expanded dimensions normally, without model-profile rejection."""

    @staticmethod
    def _caps(model: str, service_tier: str = "default", **changes) -> VideoCapabilities:
        return replace(_jwt_backend(model).video_capabilities_for_tier(service_tier), **changes)

    def test_relaxed_reference_images_sent_on_model_without_reference(self, tmp_path):
        refs = TestCapabilityValidation._refs(tmp_path, 2)
        subpath, payload = _jwt_backend("kling-v3")._build_payload(
            _request(tmp_path, reference_images=refs, video_capabilities=self._caps("kling-v3", max_reference_images=2))
        )
        assert subpath == "multi-image2video"
        assert len(payload["image_list"]) == 2

    def test_relaxed_reference_limit_sends_all_references(self, tmp_path):
        refs = TestCapabilityValidation._refs(tmp_path, 6)
        _, payload = _jwt_backend("kling-v3-omni")._build_payload(
            _request(
                tmp_path, reference_images=refs, video_capabilities=self._caps("kling-v3-omni", max_reference_images=6)
            )
        )
        assert len(payload["image_list"]) == 6

    def test_relaxed_last_frame_sent_at_std_tier_on_pro_only_model(self, tmp_path):
        first = tmp_path / "first.png"
        last = tmp_path / "last.png"
        first.write_bytes(b"\x89PNG\r\n1")
        last.write_bytes(b"\x89PNG\r\n2")
        _, payload = _jwt_backend("kling-v2-5-turbo")._build_payload(
            _request(
                tmp_path,
                start_image=first,
                end_image=last,
                video_capabilities=self._caps("kling-v2-5-turbo", last_frame=True),
            )
        )
        assert payload["mode"] == "std"
        assert "image_tail" in payload

    def test_relaxed_text_to_video_sent(self, tmp_path):
        subpath, _ = _jwt_backend("kling-video-o1")._build_payload(
            _request(tmp_path, video_capabilities=self._caps("kling-video-o1", text_to_video=True))
        )
        assert subpath == "text2video"

    def test_relaxed_audio_track_sends_sound_and_meters_audio(self, tmp_path):
        request = _request(
            tmp_path,
            generate_audio=True,
            video_capabilities=self._caps("kling-v2-5-turbo", audio_track=VideoAudioMode.CONTROLLABLE),
        )
        _, payload = _jwt_backend("kling-v2-5-turbo")._build_payload(request)
        assert payload["sound"] == "on"
        assert KlingVideoBackend.metered_usage("kling-v2-5-turbo", request).dimensions["audio"] is True

    def test_always_on_audio_track_sends_sound_on_and_meters_audio(self, tmp_path):
        request = _request(
            tmp_path,
            generate_audio=False,
            video_capabilities=self._caps("kling-v2-5-turbo", audio_track=VideoAudioMode.ALWAYS_ON),
        )
        _, payload = _jwt_backend("kling-v2-5-turbo")._build_payload(request)
        assert payload["sound"] == "on"
        assert KlingVideoBackend.metered_usage("kling-v2-5-turbo", request).dimensions["audio"] is True

    def test_narrowed_audio_track_omits_sound_and_meters_silent(self, tmp_path):
        request = _request(
            tmp_path,
            generate_audio=True,
            video_capabilities=self._caps("kling-v3", audio_track=VideoAudioMode.ALWAYS_OFF),
        )
        _, payload = _jwt_backend("kling-v3")._build_payload(request)
        assert "sound" not in payload
        assert KlingVideoBackend.metered_usage("kling-v3", request).dimensions["audio"] is False

    def test_always_on_override_bypasses_1080p_constraint(self, tmp_path):
        """v2-6 std with user always-audible overrides sends sound="on" and meters audio, leaving judgment upstream."""
        request = _request(
            tmp_path,
            resolution="720p",
            service_tier="std",
            generate_audio=True,
            video_capabilities=self._caps("kling-v2-6", "std", audio_track=VideoAudioMode.ALWAYS_ON),
        )
        _, payload = _jwt_backend("kling-v2-6")._build_payload(request)
        assert payload["mode"] == "std"
        assert payload["sound"] == "on"
        assert KlingVideoBackend.metered_usage("kling-v2-6", request).dimensions["audio"] is True

    @pytest.mark.parametrize(
        ("route_audio", "generate_audio"),
        [(VideoAudioMode.ALWAYS_ON, False), (VideoAudioMode.CONTROLLABLE, True)],
    )
    def test_audible_reference_route_override_rejected_before_submit(self, tmp_path, route_audio, generate_audio):
        """Multi-image-subject payloads lack sound; overrides requiring audio reject before submission rather than silently producing silence."""
        refs = TestCapabilityValidation._refs(tmp_path, 1)
        request = _request(
            tmp_path,
            reference_images=refs,
            generate_audio=generate_audio,
            video_capabilities=self._caps("kling-v3-omni", reference_route_audio_track=route_audio),
        )
        with pytest.raises(VideoCapabilityError) as exc:
            _jwt_backend("kling-v3-omni")._build_payload(request)
        assert exc.value.code == "video_reference_route_audio_unsupported"

    def test_controllable_reference_route_override_with_audio_off_sent(self, tmp_path):
        refs = TestCapabilityValidation._refs(tmp_path, 1)
        subpath, payload = _jwt_backend("kling-v3-omni")._build_payload(
            _request(
                tmp_path,
                reference_images=refs,
                generate_audio=False,
                video_capabilities=self._caps("kling-v3-omni", reference_route_audio_track=VideoAudioMode.CONTROLLABLE),
            )
        )
        assert subpath == "multi-image2video"
        assert "sound" not in payload


class TestAuthHeaders:
    def test_jwt_mode_signs_bearer_token(self):
        headers = _jwt_backend()._headers()
        assert headers["Content-Type"] == "application/json"
        token = headers["Authorization"].removeprefix("Bearer ")
        claims = jwt.decode(token, _SECRET, algorithms=["HS256"], options={"verify_exp": False})
        assert claims["iss"] == "ak-1"

    def test_bearer_mode_uses_static_key(self):
        # bearer bypasses JWT management: Authorization holds static keys, not signed tokens.
        headers = _bearer_backend()._headers()
        assert headers["Authorization"] == "Bearer static-key"


class TestPayloadBuilding:
    def test_text2video_no_image(self, tmp_path):
        subpath, payload = _jwt_backend()._build_payload(_request(tmp_path))
        assert subpath == "text2video"
        assert payload["model_name"] == "kling-v2-5-turbo"
        assert payload["mode"] == "std"
        assert payload["duration"] == "5"  # String
        assert payload["aspect_ratio"] == "9:16"
        assert "image" not in payload

    def test_service_tier_pro_maps_to_mode_pro(self, tmp_path):
        _, payload = _jwt_backend()._build_payload(_request(tmp_path, service_tier="pro"))
        assert payload["mode"] == "pro"

    def test_service_tier_default_maps_to_std(self, tmp_path):
        _, payload = _jwt_backend()._build_payload(_request(tmp_path, service_tier="default"))
        assert payload["mode"] == "std"

    def test_image2video_embeds_base64_frame(self, tmp_path):
        img = tmp_path / "first.png"
        img.write_bytes(b"\x89PNG\r\n")
        subpath, payload = _jwt_backend()._build_payload(_request(tmp_path, start_image=img))
        assert subpath == "image2video"
        assert isinstance(payload["image"], str)
        assert payload["image"]
        # Raw base64 without data-URI prefix
        assert not payload["image"].startswith("data:")
        assert "image_tail" not in payload

    def test_image2video_with_end_frame(self, tmp_path):
        # kling-v2-5-turbo first/last frames require pro; explicitly supply service_tier="pro" to admit image_tail.
        first = tmp_path / "first.png"
        last = tmp_path / "last.png"
        first.write_bytes(b"\x89PNG\r\n1")
        last.write_bytes(b"\x89PNG\r\n2")
        _, payload = _jwt_backend()._build_payload(
            _request(tmp_path, start_image=first, end_image=last, service_tier="pro")
        )
        assert "image" in payload
        assert "image_tail" in payload

    def test_image2video_end_frame_rejected_at_std_tier(self, tmp_path):
        # kling-v2-5-turbo first/last frames require pro. In std, including the default without explicit service_tier,
        # official APIs accept image_tail but do not enforce it; fail loudly rather than silently send ineffective requests.
        first = tmp_path / "first.png"
        last = tmp_path / "last.png"
        first.write_bytes(b"\x89PNG\r\n1")
        last.write_bytes(b"\x89PNG\r\n2")
        with pytest.raises(VideoCapabilityError) as exc:
            _jwt_backend()._build_payload(_request(tmp_path, start_image=first, end_image=last))
        assert exc.value.code == "video_last_frame_requires_pro"

    def test_image2video_end_frame_allowed_at_std_tier_for_v3(self, tmp_path):
        # Official kling-v3 first/last frames lack pro-only restrictions; std should be admitted normally.
        first = tmp_path / "first.png"
        last = tmp_path / "last.png"
        first.write_bytes(b"\x89PNG\r\n1")
        last.write_bytes(b"\x89PNG\r\n2")
        _, payload = _jwt_backend("kling-v3")._build_payload(_request(tmp_path, start_image=first, end_image=last))
        assert "image" in payload
        assert "image_tail" in payload

    def test_image2video_empty_end_frame_is_omitted(self, tmp_path):
        # Empty end_image means no last frame; truthiness skips it rather than encoding an empty path and crashing.
        first = tmp_path / "first.png"
        first.write_bytes(b"\x89PNG\r\n1")
        subpath, payload = _jwt_backend()._build_payload(_request(tmp_path, start_image=first, end_image=""))
        assert subpath == "image2video"
        assert "image" in payload
        assert "image_tail" not in payload

    def test_end_frame_without_start_frame_rejected(self, tmp_path):
        """A last frame alone cannot enter text-to-video payloads; reject instead of dropping it."""
        last = tmp_path / "last.png"
        last.write_bytes(b"\x89PNG\r\n2")
        with pytest.raises(VideoCapabilityError) as exc:
            _jwt_backend("kling-v3")._build_payload(_request(tmp_path, end_image=last))
        assert exc.value.code == "video_end_image_requires_start_image"

    def test_end_frame_on_model_without_last_frame_rejected(self, tmp_path):
        first = tmp_path / "first.png"
        last = tmp_path / "last.png"
        first.write_bytes(b"\x89PNG\r\n1")
        last.write_bytes(b"\x89PNG\r\n2")
        with pytest.raises(VideoCapabilityError) as exc:
            # Unmatched models conservatively disable last frames, even though channels implement them.
            _jwt_backend("kling-unlisted-model")._build_payload(
                _request(
                    tmp_path,
                    start_image=first,
                    end_image=last,
                    video_capabilities=_effective("kling-unlisted-model", "pro"),
                    service_tier="pro",
                )
            )
        assert exc.value.code == "video_last_frame_unsupported"

    def test_reference_audio_rejected(self, tmp_path):
        """Kling payloads lack reference-audio fields; reject such requests instead of ignoring audio."""
        audio = tmp_path / "voice.mp3"
        audio.write_bytes(b"ID3")
        with pytest.raises(VideoCapabilityError) as exc:
            _jwt_backend("kling-v3")._build_payload(_request(tmp_path, reference_audio_files=[audio]))
        assert exc.value.code == "video_reference_audio_unsupported"

    def test_unreadable_start_image_raises(self, tmp_path):
        with pytest.raises(VideoCapabilityError) as exc:
            _jwt_backend()._build_payload(_request(tmp_path, start_image=tmp_path / "nope.png"))
        assert exc.value.code == "video_start_image_unreadable"


class TestSafeLogView:
    def test_no_base64_or_prompt_leaks(self, tmp_path):
        img = tmp_path / "f.png"
        img.write_bytes(b"\x89PNG\r\n")
        b = _jwt_backend()
        subpath, payload = b._build_payload(_request(tmp_path, start_image=img))
        view = b._safe_log_view(subpath, payload)
        # Scalars only; do not expand base64/prompts.
        assert view["has_image"] is True
        assert view["prompt_len"] == len("a cat walking")
        assert "image" not in view
        assert "prompt" not in view
        assert all(isinstance(v, (str, int, bool)) for v in view.values())


class TestGenerateHappyPath:
    async def test_submit_poll_download(self, tmp_path):
        with _kling_api() as routes, bounded_poll_clock():
            routes.submit.mock(return_value=_resp(_submit("task-9")))
            routes.poll.mock(
                side_effect=[
                    _resp(_query("processing")),
                    _resp(_query("succeed", url=_DOWNLOAD_URL)),
                ]
            )
            routes.download.mock(return_value=httpx.Response(200, content=b"mp4-bytes"))

            result = await _jwt_backend().generate(_request(tmp_path))

            # text2video submission endpoint
            assert only_request(routes.submit).url.path == "/v1/videos/text2video"
            assert str(only_request(routes.download).url) == _DOWNLOAD_URL

        assert result.provider == PROVIDER_KLING
        assert result.task_id == "task-9"
        assert result.video_uri == _DOWNLOAD_URL
        assert result.generate_audio is False  # turbo has no audio.
        assert result.video_path.read_bytes() == b"mp4-bytes"

    async def test_jwt_injected_on_submit(self, tmp_path):
        with _kling_api() as routes:
            routes.submit.mock(return_value=_resp(_submit()))
            routes.poll.mock(return_value=_resp(_query("succeed", url=_DOWNLOAD_URL)))
            routes.download.mock(return_value=httpx.Response(200, content=b""))

            await _jwt_backend().generate(_request(tmp_path))

            token = only_request(routes.submit).headers["Authorization"].removeprefix("Bearer ")

        claims = jwt.decode(token, _SECRET, algorithms=["HS256"], options={"verify_exp": False})
        assert claims["iss"] == "ak-1"

    async def test_bearer_static_key_on_submit(self, tmp_path):
        with _kling_api() as routes:
            routes.submit.mock(return_value=_resp(_submit()))
            routes.poll.mock(return_value=_resp(_query("succeed", url=_DOWNLOAD_URL)))
            routes.download.mock(return_value=httpx.Response(200, content=b""))

            await _bearer_backend().generate(_request(tmp_path))

            assert only_request(routes.submit).headers["Authorization"] == "Bearer static-key"

    async def test_failed_status_raises(self, tmp_path):
        with _kling_api() as routes:
            routes.submit.mock(return_value=_resp(_submit()))
            routes.poll.mock(return_value=_resp(_query("failed", status_msg="content rejected")))

            with pytest.raises(RuntimeError, match="content rejected"):
                await _jwt_backend().generate(_request(tmp_path))

            assert routes.download.call_count == 0

    async def test_persists_provider_job_id_when_task_id_present(self, tmp_path):
        with _kling_api() as routes, captured_provider_job_ids() as persisted:
            routes.submit.mock(return_value=_resp(_submit("task-x")))
            routes.poll.mock(return_value=_resp(_query("succeed", url=_DOWNLOAD_URL)))
            routes.download.mock(return_value=httpx.Response(200, content=b""))

            await _jwt_backend().generate(_request(tmp_path, task_id="local-task-1"))

        # Persist subpath:task_id:audio_flag; resume restores query endpoints (text2video without first frames) and audio decisions (turbo always 0).
        assert [(r["task_id"], r["job_id"]) for r in persisted] == [("local-task-1", "text2video:task-x:0")]

    async def test_persists_image2video_subpath_in_job_id(self, tmp_path):
        img = tmp_path / "first.png"
        img.write_bytes(b"\x89PNG\r\n")
        with _kling_api() as routes, captured_provider_job_ids() as persisted:
            routes.submit.mock(return_value=_resp(_submit("task-i")))
            routes.poll.mock(return_value=_resp(_query("succeed", url=_DOWNLOAD_URL)))
            routes.download.mock(return_value=httpx.Response(200, content=b""))

            await _jwt_backend().generate(_request(tmp_path, task_id="local-task-2", start_image=img))

        # First frame → encode image2video prefix into job_id so resume queries correct endpoints.
        assert [r["job_id"] for r in persisted] == ["image2video:task-i:0"]


class TestResume:
    async def test_download_failure_can_resume_without_resubmit(self, tmp_path):
        download_ready = False

        def download_response(_request: httpx.Request) -> httpx.Response:
            if download_ready:
                return httpx.Response(200, content=b"resumed")
            return httpx.Response(503, text="cdn unavailable")

        with _kling_api() as routes, bounded_poll_clock(), captured_provider_job_ids() as persisted:
            routes.submit.mock(return_value=_resp(_submit("task-download")))
            routes.poll.mock(return_value=_resp(_query("succeed", url="https://x/r.mp4")))
            routes.download.mock(side_effect=download_response)
            request = _request(tmp_path, task_id="worker-task", poll_timeout_seconds=1800)

            with pytest.raises(ArtifactDownloadError) as caught:
                await _jwt_backend().generate(request)

            assert caught.value.code == "artifact_download_failed"
            download_ready = True
            result = await _jwt_backend().resume_video(persisted[0]["job_id"], request)

            assert routes.submit.call_count == 1

        assert result.video_path.read_bytes() == b"resumed"

    async def test_resume_polls_without_resubmit(self, tmp_path):
        with _kling_api() as routes:
            routes.poll.mock(return_value=_resp(_query("succeed", url="https://x/r.mp4")))
            routes.download.mock(return_value=httpx.Response(200, content=b"resumed"))

            # Persisted text2video prefixes restore query endpoints and bare task_id.
            result = await _jwt_backend().resume_video("text2video:task-resume", _request(tmp_path))

            assert routes.submit.call_count == 0
            assert only_request(routes.poll).url.path == "/v1/videos/text2video/task-resume"

        assert result.task_id == "task-resume"
        assert result.video_uri == "https://x/r.mp4"
        assert result.video_path.read_bytes() == b"resumed"

    async def test_resume_image2video_subpath_from_encoded_job_id(self, tmp_path):
        # Resume requests omit start_image, matching actual restarts; derive subpaths from persisted job_id prefixes,
        # or image2video tasks would incorrectly query text2video and disappear.
        with _kling_api() as routes:
            routes.poll.mock(return_value=_resp(_query("succeed", url="https://x/r.mp4")))
            routes.download.mock(return_value=httpx.Response(200, content=b""))

            result = await _jwt_backend().resume_video("image2video:task-r2", _request(tmp_path))

            assert routes.submit.call_count == 0
            assert only_request(routes.poll).url.path == "/v1/videos/image2video/task-r2"

        assert result.task_id == "task-r2"

    async def test_resume_bare_job_id_falls_back_to_text2video(self, tmp_path):
        # Unknown prefixes (abnormal/legacy data) fall back to text2video, using the entire string as task_id.
        with _kling_api() as routes:
            routes.poll.mock(return_value=_resp(_query("succeed", url="https://x/r.mp4")))
            routes.download.mock(return_value=httpx.Response(200, content=b""))

            result = await _jwt_backend().resume_video("legacy-bare-id", _request(tmp_path))

            assert only_request(routes.poll).url.path == "/v1/videos/text2video/legacy-bare-id"

        assert result.task_id == "legacy-bare-id"

    async def test_resume_multi_image2video_subpath_from_encoded_job_id(self, tmp_path):
        # Resume multi-image-subject tasks using persisted job_id subpaths and multi-image2video queries.
        with _kling_api() as routes:
            routes.poll.mock(return_value=_resp(_query("succeed", url="https://x/r.mp4")))
            routes.download.mock(return_value=httpx.Response(200, content=b""))

            result = await _jwt_backend("kling-v3-omni").resume_video("multi-image2video:task-m", _request(tmp_path))

            assert routes.submit.call_count == 0
            assert only_request(routes.poll).url.path == "/v1/videos/multi-image2video/task-m"

        assert result.task_id == "task-m"

    async def test_resume_404_raises_resume_expired_without_retry(self, tmp_path):
        # Resumed provider task missing: 404 is definitive expiry on first response, never retried as newly submitted/not ready.
        with _kling_api() as routes, bounded_poll_clock():
            routes.poll.mock(return_value=_resp({"code": 1203, "message": "task not found"}, status_code=404))

            with pytest.raises(ResumeExpiredError) as caught:
                await _jwt_backend().resume_video("image2video:task-gone:0", _request(tmp_path))

            assert routes.poll.call_count == 1
            assert routes.download.call_count == 0

        assert caught.value.job_id == "image2video:task-gone:0"
        assert caught.value.provider == PROVIDER_KLING

    async def test_generate_poll_404_is_retried_as_not_ready(self, tmp_path):
        # Fresh-submission polling still treats 404 as query-side unready and continues until terminal state.
        with _kling_api() as routes, bounded_poll_clock():
            routes.submit.mock(return_value=_resp(_submit("task-new")))
            routes.poll.mock(
                side_effect=[
                    _resp({"code": 1203, "message": "task not found"}, status_code=404),
                    _resp(_query("succeed", url=_DOWNLOAD_URL)),
                ]
            )
            routes.download.mock(return_value=httpx.Response(200, content=b"mp4-bytes"))

            result = await _jwt_backend().generate(_request(tmp_path))

            assert routes.poll.call_count == 2

        assert result.video_path.read_bytes() == b"mp4-bytes"


_RELAY_A = "https://relay-a.example.com/v1"
_RELAY_B = "https://relay-b.example.com/v1"


class TestSubmittedBaseUrlReplay:
    """Kling protocol under custom providers persists actual submission domains, polling those domains on resume."""

    async def test_submit_persists_request_base_url(self, tmp_path):
        with capture_http() as router, captured_provider_job_ids() as persisted:
            router.post(f"{_RELAY_A}/videos/text2video").mock(return_value=_resp(_submit("task-r")))
            router.get(f"{_RELAY_A}/videos/text2video/task-r").mock(
                return_value=_resp(_query("succeed", url=_DOWNLOAD_URL))
            )
            router.get(_DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=b""))

            backend = KlingVideoBackend(auth_mode="bearer", api_key="static-key", base_url=_RELAY_A)
            await backend.generate(_request(tmp_path, task_id="local-r"))

        assert [(r["job_id"], r["base_url"]) for r in persisted] == [("text2video:task-r:0", _RELAY_A)]

    async def test_resume_polls_submitted_base_url_after_config_change(self, tmp_path):
        # Submitted to A, config changed to B before resume; task exists only on A.
        with capture_http() as router, bounded_poll_clock():
            submitted = router.get(f"{_RELAY_A}/videos/image2video/task-r").mock(
                return_value=_resp(_query("succeed", url=_DOWNLOAD_URL))
            )
            current = router.get(url__regex=r"^https://relay-b\.example\.com/").mock(
                return_value=_resp({"code": 1203, "message": "task not found"}, status_code=404)
            )
            router.get(_DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=b"resumed"))

            backend = KlingVideoBackend(auth_mode="bearer", api_key="static-key", base_url=_RELAY_B)
            result = await backend.resume_video("image2video:task-r:0", _request(tmp_path, submitted_base_url=_RELAY_A))

            assert only_request(submitted).headers["Authorization"] == "Bearer static-key"
            assert current.call_count == 0

        assert result.task_id == "task-r"
        assert result.video_path.read_bytes() == b"resumed"


class TestOutboundDestinationGuard:
    """Creation/polling share artifact-download outbound gates; link-local/cloud-metadata destinations issue no requests."""

    _LINK_LOCAL_BASE_URL = "http://169.254.169.254/v1"

    async def test_submit_to_link_local_base_url_is_not_sent(self, tmp_path):
        with capture_http() as router:
            submit = router.post(url__regex=r"^http://169\.254\.169\.254/").mock(return_value=_resp(_submit()))

            backend = KlingVideoBackend(auth_mode="bearer", api_key="static-key", base_url=self._LINK_LOCAL_BASE_URL)
            with pytest.raises(ArtifactDestinationRejectedError):
                await backend.generate(_request(tmp_path))

        assert submit.call_count == 0

    async def test_resume_poll_to_link_local_base_url_is_not_sent(self, tmp_path):
        with capture_http() as router, bounded_poll_clock():
            poll = router.get(url__regex=r"^http://169\.254\.169\.254/").mock(
                return_value=_resp(_query("succeed", url=_DOWNLOAD_URL))
            )
            router.get(_DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=b""))

            backend = KlingVideoBackend(auth_mode="bearer", api_key="static-key", base_url=self._LINK_LOCAL_BASE_URL)
            with pytest.raises(ArtifactDestinationRejectedError):
                await backend.resume_video("text2video:task-1:0", _request(tmp_path))

        assert poll.call_count == 0


class TestAudioGatingResult:
    @staticmethod
    @contextmanager
    def _succeeding(task_id: str) -> Generator[_KlingRoutes]:
        """Successful submit, one terminal poll, empty-video download; observe only audio decisions."""
        with _kling_api() as routes:
            routes.submit.mock(return_value=_resp(_submit(task_id)))
            routes.poll.mock(return_value=_resp(_query("succeed", url="https://x/v.mp4")))
            routes.download.mock(return_value=httpx.Response(200, content=b""))
            yield routes

    async def test_v2_6_pro_audio_result_true(self, tmp_path):
        # v2-6 pro (1080P) plus requested audio → result.generate_audio=True, selecting audible downstream pricing.
        with self._succeeding("task-a") as routes:
            result = await _jwt_backend("kling-v2-6").generate(
                _request(tmp_path, resolution="1080p", service_tier="pro", generate_audio=True)
            )
            assert only_request(routes.submit).url.path == "/v1/videos/text2video"

        assert result.generate_audio is True

    async def test_v2_6_std_audio_result_false(self, tmp_path):
        # 720p resolves std without 1080P: results must record silence,
        # or silent videos would bill audible rates (¥1.0/s vs ¥0.8/s).
        with self._succeeding("task-a2"):
            result = await _jwt_backend("kling-v2-6").generate(
                _request(tmp_path, resolution="720p", service_tier="std", generate_audio=True)
            )

        assert result.generate_audio is False

    async def test_v2_6_1080p_audio_result_true(self, tmp_path):
        with self._succeeding("task-a3"):
            result = await _jwt_backend("kling-v2-6").generate(
                _request(tmp_path, resolution="1080p", generate_audio=True)
            )

        assert result.generate_audio is True

    async def test_v3_audio_result_true(self, tmp_path):
        # v3 supports audiovisual output without resolution constraints; requested audio → result.generate_audio=True.
        with self._succeeding("task-b"):
            result = await _jwt_backend("kling-v3").generate(_request(tmp_path, generate_audio=True))

        assert result.generate_audio is True

    async def test_turbo_audio_gated_result_false(self, tmp_path):
        # turbo lacks audio: even requested audio yields result.generate_audio=False and silent pricing.
        with self._succeeding("task-b2"):
            result = await _jwt_backend().generate(_request(tmp_path, resolution="1080p", generate_audio=True))

        assert result.generate_audio is False

    async def test_v3_omni_multi_image_audio_result_false(self, tmp_path):
        # multi-image2video omits sound because native schemas lack it; output is necessarily silent,
        # so result.generate_audio must also be False to avoid audible billing for silence.
        ref = tmp_path / "ref0.png"
        ref.write_bytes(b"\x89PNG\r\n")
        with self._succeeding("task-b3") as routes:
            result = await _jwt_backend("kling-v3-omni").generate(
                _request(tmp_path, reference_images=[ref], resolution="1080p", generate_audio=True)
            )
            assert only_request(routes.submit).url.path == "/v1/videos/multi-image2video"

        assert result.generate_audio is False

    async def test_v2_6_persists_audio_bit_in_job_id(self, tmp_path):
        # Submit-time audio decisions enter job_id (v2-6 pro audio → final :1); resume uses them directly for billing.
        with self._succeeding("task-c"), captured_provider_job_ids() as persisted:
            await _jwt_backend("kling-v2-6").generate(
                _request(tmp_path, task_id="local-a", service_tier="pro", generate_audio=True)
            )

        assert [r["job_id"] for r in persisted] == ["text2video:task-c:1"]

    async def test_resume_reuses_persisted_audio_over_recompute(self, tmp_path):
        # Persisted :1 outranks resume-time recalculation; even with request generate_audio=False,
        # retain submit-time audio to prevent billing drift.
        with _kling_api() as routes:
            routes.poll.mock(return_value=_resp(_query("succeed", url="https://x/r.mp4")))
            routes.download.mock(return_value=httpx.Response(200, content=b""))

            result = await _jwt_backend("kling-v2-6").resume_video(
                "text2video:task-c:1", _request(tmp_path, service_tier="pro", generate_audio=False)
            )

            assert routes.submit.call_count == 0
            # Pin parsing: three-part job_id audio suffixes never leak into task_id; restore subpath/task correctly.
            assert only_request(routes.poll).url.path == "/v1/videos/text2video/task-c"

        assert result.generate_audio is True
        assert result.task_id == "task-c"

    async def test_resume_legacy_job_id_stays_silent(self, tmp_path):
        # Two-part legacy job_ids lacking audio flags always mean silent output; never recalculate as audible using current capability tables.
        with _kling_api() as routes:
            routes.poll.mock(return_value=_resp(_query("succeed", url="https://x/r.mp4")))
            routes.download.mock(return_value=httpx.Response(200, content=b""))

            result = await _jwt_backend("kling-v2-6").resume_video(
                "text2video:task-c", _request(tmp_path, service_tier="pro", generate_audio=True)
            )

            assert routes.submit.call_count == 0
            # Two-part legacy IDs still restore subpath/task correctly, never treating entire IDs as task_id.
            assert only_request(routes.poll).url.path == "/v1/videos/text2video/task-c"

        assert result.generate_audio is False
        assert result.task_id == "task-c"

    async def test_resume_legacy_multi_image_job_id_stays_silent(self, tmp_path):
        # Multi-image-subject output is always silent; resume requests lack references, so only decoded subpaths can establish this.
        with _kling_api() as routes:
            routes.poll.mock(return_value=_resp(_query("succeed", url="https://x/r.mp4")))
            routes.download.mock(return_value=httpx.Response(200, content=b""))

            result = await _jwt_backend("kling-v3-omni").resume_video(
                "multi-image2video:task-d", _request(tmp_path, generate_audio=True)
            )

            assert routes.submit.call_count == 0
            assert only_request(routes.poll).url.path == "/v1/videos/multi-image2video/task-d"

        assert result.generate_audio is False
