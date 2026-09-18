"""社交分发服务：选版口径、上游载荷与拒绝路径。

出站请求由 respx 在 transport 层捕获（真客户端真序列化），投递到磁盘的那个文件是否就是
读模型选中的那一版，靠比对 multipart 里的文件字节断言。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from lib.api_errors import UnprocessableError
from lib.project_manager import ProjectManager
from lib.social_publish import UploadPostClient
from lib.speech_artifact_provenance import RenditionVariant
from lib.speech_presentation import RawPresentationMedia, materialize_raw_video_presentation
from server.services.presentation_read_model import MaterializedPresentation, PresentationUnavailableError
from server.services.social_publish import SocialPublishService, UploadPostCredentials
from tests.factories import make_test_video
from tests.http_capture import capture_http, only_request

_BASE_URL = "https://upload.example/api"
_CREDENTIALS = UploadPostCredentials(api_key="secret-key", profile="studio", base_url=_BASE_URL)


def _project(tmp_path: Path) -> tuple[ProjectManager, Path]:
    root = tmp_path / "projects"
    path = root / "demo"
    path.mkdir(parents=True)
    (path / "project.json").write_text(
        json.dumps(
            {
                "title": "测试项目",
                "content_mode": "narration",
                "generation_mode": "storyboard",
                "aspect_ratio": "9:16",
                "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return ProjectManager(root), path


def _presentation(project_path: Path, video: Path) -> MaterializedPresentation:
    digest = f"sha256-v1:{hashlib.sha256(video.read_bytes()).hexdigest()}"
    return MaterializedPresentation(
        episode=1,
        resource_type="videos",
        script_file="episode_1.json",
        transition_to_next="cut",
        presentation=materialize_raw_video_presentation(
            unit_id="E1S01",
            video=RawPresentationMedia(
                artifact_path=video.relative_to(project_path).as_posix(),
                version=1,
                selection="current",
                content_digest=digest,
                actual_duration_seconds=1.0,
            ),
        ),
        subtitle_artifact_path=None,
        presentation_artifact_path=None,
    )


class _Reader:
    """按公开构造参数注入的读模型替身；记录选版参数供断言。"""

    def __init__(self, value: MaterializedPresentation | None = None, error: Exception | None = None) -> None:
        self.value = value
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def materialize_unit(
        self,
        *,
        project_name: str,
        resource_type: str,
        resource_id: str,
        variant: RenditionVariant,
        video_version: int | None = None,
        audio_version: int | None = None,
    ) -> MaterializedPresentation:
        self.calls.append(
            {
                "project_name": project_name,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "variant": variant,
                "video_version": video_version,
                "audio_version": audio_version,
            }
        )
        if self.error is not None:
            raise self.error
        assert self.value is not None
        return self.value


def _service(project_manager: ProjectManager, reader: _Reader, http: httpx.AsyncClient) -> SocialPublishService:
    return SocialPublishService(
        project_manager,
        presentation_reader=reader,
        client_factory=lambda credentials: UploadPostClient(
            api_key=credentials.api_key, base_url=credentials.base_url, http_client=http
        ),
    )


async def test_publish_sends_the_selected_video_bytes_and_forwards_version_selection(tmp_path: Path) -> None:
    pm, project_path = _project(tmp_path)
    video = project_path / "versions" / "videos" / "E1S01_v3.mp4"
    make_test_video(video)
    reader = _Reader(_presentation(project_path, video))

    async with httpx.AsyncClient() as http:
        with capture_http() as router:
            route = router.post(f"{_BASE_URL}/upload").mock(
                return_value=httpx.Response(
                    200, json={"success": True, "request_id": "arcreel-x", "total_platforms": 1}
                )
            )

            submission = await _service(pm, reader, http).publish_unit(
                _CREDENTIALS,
                project_name="demo",
                resource_type="videos",
                resource_id="E1S01",
                variant="post_production",
                platforms=("tiktok",),
                title="第一章",
                video_version=3,
            )

    assert reader.calls == [
        {
            "project_name": "demo",
            "resource_type": "videos",
            "resource_id": "E1S01",
            "variant": "post_production",
            "video_version": 3,
            "audio_version": None,
        }
    ]
    body = only_request(route).content
    assert video.read_bytes() in body
    assert b'name="user"\r\n\r\nstudio' in body
    assert b'name="external_id"\r\n\r\ndemo:E1S01' in body
    # 回执报的是本端发出去的那个 id，不是上游回传的：幂等键用的就是它
    assert f'name="request_id"\r\n\r\n{submission.request_id}'.encode() in body


async def test_publish_generates_a_server_side_request_id(tmp_path: Path) -> None:
    """回执丢失时还能凭它查状态，因此不能交给客户端生成。"""
    pm, project_path = _project(tmp_path)
    video = project_path / "versions" / "videos" / "E1S01_v1.mp4"
    make_test_video(video)
    reader = _Reader(_presentation(project_path, video))

    async with httpx.AsyncClient() as http:
        with capture_http() as router:
            route = router.post(f"{_BASE_URL}/upload").mock(
                return_value=httpx.Response(200, json={"success": True, "request_id": "arcreel-y"})
            )

            await _service(pm, reader, http).publish_unit(
                _CREDENTIALS,
                project_name="demo",
                resource_type="videos",
                resource_id="E1S01",
                variant="post_production",
                platforms=("tiktok",),
                title="第一章",
            )

    request = only_request(route)
    sent_id = request.content.split(b'name="request_id"\r\n\r\n')[1].split(b"\r\n")[0].decode()
    assert sent_id.startswith("arcreel-")
    assert request.headers["idempotency-key"] == sent_id


async def test_publish_reuses_a_caller_supplied_request_id_as_idempotency_key(tmp_path: Path) -> None:
    """重试带同一个 id 才不会重复发布：上传可能已被上游受理而响应丢在半路。"""
    pm, project_path = _project(tmp_path)
    video = project_path / "versions" / "videos" / "E1S01_v1.mp4"
    make_test_video(video)
    reader = _Reader(_presentation(project_path, video))

    async with httpx.AsyncClient() as http:
        with capture_http() as router:
            route = router.post(f"{_BASE_URL}/upload").mock(return_value=httpx.Response(200, json={"success": True}))

            submission = await _service(pm, reader, http).publish_unit(
                _CREDENTIALS,
                project_name="demo",
                resource_type="videos",
                resource_id="E1S01",
                variant="post_production",
                platforms=("tiktok",),
                title="第一章",
                request_id="arcreel-retryme01",
            )

    request = only_request(route)
    assert b'name="request_id"\r\n\r\narcreel-retryme01' in request.content
    assert request.headers["idempotency-key"] == "arcreel-retryme01"
    assert submission.request_id == "arcreel-retryme01"


@pytest.mark.parametrize(
    "request_id",
    ["", "sin-prefijo", "arcreel-short", "arcreel-con espacio", "arcreel-salto\nlinea"],
)
async def test_publish_refuses_a_malformed_request_id(tmp_path: Path, request_id: str) -> None:
    """它会原样进请求头，形状不受控就等于让调用方往头里塞任意内容。"""
    pm, project_path = _project(tmp_path)
    video = project_path / "versions" / "videos" / "E1S01_v1.mp4"
    make_test_video(video)
    reader = _Reader(_presentation(project_path, video))

    async with httpx.AsyncClient() as http:
        with pytest.raises(UnprocessableError) as excinfo:
            await _service(pm, reader, http).publish_unit(
                _CREDENTIALS,
                project_name="demo",
                resource_type="videos",
                resource_id="E1S01",
                variant="post_production",
                platforms=("tiktok",),
                title="第一章",
                request_id=request_id,
            )

    assert excinfo.value.key == "social_publish_request_id_invalid"
    assert reader.calls == []


async def test_publish_deduplicates_platforms_preserving_order(tmp_path: Path) -> None:
    pm, project_path = _project(tmp_path)
    video = project_path / "versions" / "videos" / "E1S01_v1.mp4"
    make_test_video(video)
    reader = _Reader(_presentation(project_path, video))

    async with httpx.AsyncClient() as http:
        with capture_http() as router:
            route = router.post(f"{_BASE_URL}/upload").mock(
                return_value=httpx.Response(200, json={"success": True, "request_id": "arcreel-z"})
            )

            await _service(pm, reader, http).publish_unit(
                _CREDENTIALS,
                project_name="demo",
                resource_type="videos",
                resource_id="E1S01",
                variant="post_production",
                platforms=("tiktok", "youtube", "tiktok"),
                title="第一章",
            )

    body = only_request(route).content.decode("utf-8", errors="replace")
    assert body.count('name="platform[]"') == 2
    assert body.index("tiktok") < body.index("youtube")


async def test_publish_refuses_a_platform_that_does_not_accept_video(tmp_path: Path) -> None:
    pm, project_path = _project(tmp_path)
    video = project_path / "versions" / "videos" / "E1S01_v1.mp4"
    make_test_video(video)
    reader = _Reader(_presentation(project_path, video))

    async with httpx.AsyncClient() as http:
        with pytest.raises(UnprocessableError) as excinfo:
            await _service(pm, reader, http).publish_unit(
                _CREDENTIALS,
                project_name="demo",
                resource_type="videos",
                resource_id="E1S01",
                variant="post_production",
                platforms=("reddit",),
                title="第一章",
            )

    assert excinfo.value.key == "social_publish_platform_unsupported"
    assert excinfo.value.params == {"platform": "reddit"}
    # 平台不合法时读模型不该被惊动：校验在取成片之前完成
    assert reader.calls == []


async def test_publish_refuses_an_empty_title(tmp_path: Path) -> None:
    pm, project_path = _project(tmp_path)
    video = project_path / "versions" / "videos" / "E1S01_v1.mp4"
    make_test_video(video)
    reader = _Reader(_presentation(project_path, video))

    async with httpx.AsyncClient() as http:
        with pytest.raises(UnprocessableError) as excinfo:
            await _service(pm, reader, http).publish_unit(
                _CREDENTIALS,
                project_name="demo",
                resource_type="videos",
                resource_id="E1S01",
                variant="post_production",
                platforms=("tiktok",),
                title="   ",
            )

    assert excinfo.value.key == "social_publish_title_required"


async def test_publish_surfaces_unavailable_selection_instead_of_uploading(tmp_path: Path) -> None:
    pm, _ = _project(tmp_path)
    reader = _Reader(error=PresentationUnavailableError("selected presentation media is unavailable"))

    async with httpx.AsyncClient() as http:
        with pytest.raises(PresentationUnavailableError):
            await _service(pm, reader, http).publish_unit(
                _CREDENTIALS,
                project_name="demo",
                resource_type="videos",
                resource_id="E1S01",
                variant="post_production",
                platforms=("tiktok",),
                title="第一章",
            )


async def test_publish_refuses_when_the_selected_file_is_gone(tmp_path: Path) -> None:
    """读模型仍指向旧路径、文件已被清理：退化成「选中的媒体不可用」，不是 500。"""
    pm, project_path = _project(tmp_path)
    video = project_path / "versions" / "videos" / "E1S01_v1.mp4"
    make_test_video(video)
    reader = _Reader(_presentation(project_path, video))
    video.unlink()

    async with httpx.AsyncClient() as http:
        with pytest.raises(PresentationUnavailableError):
            await _service(pm, reader, http).publish_unit(
                _CREDENTIALS,
                project_name="demo",
                resource_type="videos",
                resource_id="E1S01",
                variant="post_production",
                platforms=("tiktok",),
                title="第一章",
            )


async def test_fetch_progress_reports_terminal_state(tmp_path: Path) -> None:
    pm, _ = _project(tmp_path)

    async with httpx.AsyncClient() as http:
        with capture_http() as router:
            router.get(f"{_BASE_URL}/uploadposts/status").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "request_id": "arcreel-x",
                        "status": "completed",
                        "completed": 1,
                        "total": 1,
                        "results": [
                            {"platform": "tiktok", "status": "completed", "success": True, "url": "https://tiktok/1"}
                        ],
                    },
                )
            )

            progress = await _service(pm, _Reader(), http).fetch_progress(_CREDENTIALS, request_id="arcreel-x")

    assert progress.terminal is True
    assert progress.outcomes[0].url == "https://tiktok/1"
