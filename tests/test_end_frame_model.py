"""``end_frame_image`` 的数据模型、PATCH 白名单与 data_validator 校验测试。

三骨架（narration 片段 / drama 场景 / ad 镜头）同构持有该字段：默认空、参与
Python 端序列化与校验、对 LLM 隐藏。写入通道只有尾帧专用端点——通用剧本 PATCH
刻意不放行该字段，否则原样写值会绕过快照复制、重开悬空引用与越界路径的口子。
"""

from io import BytesIO

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from lib.data_validator import DataValidator
from lib.project_manager import ProjectManager
from lib.script_models import (
    AdShot,
    Composition,
    DramaScene,
    DramaVideoPrompt,
    ImagePrompt,
    NarrationSegment,
    VideoPrompt,
)
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import projects as projects_router

END_FRAME_REL = "end_frames/scene_E1S01.png"


def _image_prompt() -> ImagePrompt:
    return ImagePrompt(
        scene="场景",
        composition=Composition(shot_type="Medium Shot", lighting="暖光", ambiance="薄雾"),
    )


def _narration_segment(**overrides) -> NarrationSegment:
    return NarrationSegment(
        segment_id="E1S01",
        duration_seconds=4,
        novel_text="原文",
        characters_in_segment=[],
        image_prompt=_image_prompt(),
        video_prompt=VideoPrompt(action="转身", camera_motion="Static", ambiance_audio="风声"),
        **overrides,
    )


def _drama_scene(**overrides) -> DramaScene:
    return DramaScene(
        scene_id="E1S01",
        characters_in_scene=[],
        image_prompt=_image_prompt(),
        video_prompt=DramaVideoPrompt(action="转身", camera_motion="Static", ambiance_audio="风声"),
        **overrides,
    )


def _ad_shot(**overrides) -> AdShot:
    return AdShot(
        shot_id="E1S01",
        section="hook",
        duration_seconds=3,
        voiceover_text="开场口播",
        image_prompt=_image_prompt(),
        video_prompt=VideoPrompt(action="转身", camera_motion="Static", ambiance_audio="风声"),
        **overrides,
    )


_BUILDERS = pytest.mark.parametrize(
    "build", [_narration_segment, _drama_scene, _ad_shot], ids=["narration", "drama", "ad"]
)


class TestEndFrameImageField:
    @_BUILDERS
    def test_defaults_to_none(self, build) -> None:
        assert build().end_frame_image is None

    @_BUILDERS
    def test_round_trips_through_serialization(self, build) -> None:
        item = build(end_frame_image=END_FRAME_REL)
        dumped = item.model_dump()
        assert dumped["end_frame_image"] == END_FRAME_REL
        assert type(item).model_validate(dumped).end_frame_image == END_FRAME_REL

    @_BUILDERS
    def test_absent_key_loads_as_none(self, build) -> None:
        """存量剧本没有该键：extra=forbid 下仍须放行并落回默认空。"""
        model = type(build())
        payload = build().model_dump()
        payload.pop("end_frame_image")
        assert model.model_validate(payload).end_frame_image is None


def _seed_project(tmp_path, content_mode: str) -> ProjectManager:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", content_mode)
    if content_mode == "narration":
        script = {"segments": [{"segment_id": "E1S01", "novel_text": "t", "duration_seconds": 5}]}
    elif content_mode == "drama":
        script = {"scenes": [{"scene_id": "E1S01", "duration_seconds": 5}]}
    else:
        script = {"shots": [{"shot_id": "E1S01", "section": "hook", "voiceover_text": "v", "duration_seconds": 3}]}
    pm.save_script(
        "demo",
        {"episode": 1, "title": "E1", "content_mode": content_mode, **script},
        "episode_1.json",
        validate=False,
    )
    return pm


def _projects_client(pm: ProjectManager, monkeypatch) -> TestClient:
    monkeypatch.setattr(projects_router, "get_project_manager", lambda: pm)
    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(projects_router.router, prefix="/api/v1")
    return TestClient(app)


class TestGenericPatchIgnoresEndFrame:
    """通用剧本 PATCH 白名单不含 end_frame_image：设置尾帧只能走专用端点。"""

    @pytest.mark.parametrize(
        ("content_mode", "path", "items_key", "id_key", "flat_body"),
        [
            # narration 走显式 Pydantic 请求体（字段级白名单），drama/ad 走 updates 字典白名单
            ("narration", "segments", "segments", "segment_id", True),
            ("drama", "script-scenes", "scenes", "scene_id", False),
            ("ad", "script-shots", "shots", "shot_id", False),
        ],
    )
    def test_patch_does_not_write_end_frame_image(
        self, tmp_path, monkeypatch, content_mode, path, items_key, id_key, flat_body
    ):
        pm = _seed_project(tmp_path, content_mode)
        client = _projects_client(pm, monkeypatch)

        # 白名单内的 note 一并提交：它生效即证明 PATCH 确实执行了，end_frame_image 是被单独忽略
        updates = {"end_frame_image": "../../../etc/passwd", "note": "备注"}
        body = (
            {"script_file": "episode_1.json", **updates}
            if flat_body
            else {
                "script_file": "episode_1.json",
                "updates": updates,
            }
        )
        resp = client.patch(f"/api/v1/projects/demo/{path}/E1S01", json=body)
        assert resp.status_code == 200, resp.text

        item = pm.load_script("demo", "episode_1.json")[items_key][0]
        assert item[id_key] == "E1S01"
        assert item["note"] == "备注"
        assert item.get("end_frame_image") is None


def _write_png(path, size=(8, 8)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = BytesIO()
    Image.new("RGB", size, (255, 0, 0)).save(buf, format="PNG")
    path.write_bytes(buf.getvalue())


class TestDataValidatorEndFramePath:
    @pytest.fixture
    def project(self, tmp_path):
        pm = _seed_project(tmp_path, "narration")
        return pm, DataValidator(projects_root=str(pm.projects_root))

    def _validate_with(self, pm, validator, end_frame_value):
        script = pm.load_script("demo", "episode_1.json")
        script["segments"][0]["end_frame_image"] = end_frame_value
        pm.save_script("demo", script, "episode_1.json", validate=False)
        return validator.validate_episode("demo", "episode_1.json")

    def test_existing_snapshot_passes(self, project):
        pm, validator = project
        _write_png(pm.get_project_path("demo") / END_FRAME_REL)
        result = self._validate_with(pm, validator, END_FRAME_REL)
        assert not [e for e in result.errors if "end_frame_image" in e]

    def test_missing_snapshot_is_error(self, project):
        pm, validator = project
        result = self._validate_with(pm, validator, END_FRAME_REL)
        assert [e for e in result.errors if "end_frame_image" in e]
        assert not result.valid

    def test_traversal_path_is_error(self, project):
        pm, validator = project
        result = self._validate_with(pm, validator, "../../../etc/passwd")
        assert [e for e in result.errors if "end_frame_image" in e and "越界" in e]
        assert not result.valid

    def test_null_is_accepted(self, project):
        pm, validator = project
        result = self._validate_with(pm, validator, None)
        assert not [e for e in result.errors if "end_frame_image" in e]

    def test_path_outside_end_frames_dir_is_error(self, project):
        """指向 end_frames/ 之外目录内确实存在的文件也须拒绝——字段只认服务层写出的快照路径，
        否则等于绕过快照复制直接引用源图，重新引入源图耦合。"""
        pm, validator = project
        _write_png(pm.get_project_path("demo") / "storyboards/scene_E1S02.png")
        result = self._validate_with(pm, validator, "storyboards/scene_E1S02.png")
        assert [e for e in result.errors if "end_frame_image" in e]
        assert not result.valid

    def test_dot_dot_escaping_end_frames_prefix_is_error(self, project):
        """`end_frames/../storyboards/x.png` 表面以 end_frames 前缀开头，实际逃出该目录，同样拒绝。"""
        pm, validator = project
        _write_png(pm.get_project_path("demo") / "storyboards/scene_E1S02.png")
        result = self._validate_with(pm, validator, "end_frames/../storyboards/scene_E1S02.png")
        assert [e for e in result.errors if "end_frame_image" in e]
        assert not result.valid

    def test_directory_reference_is_error(self, project):
        """指向 end_frames/ 下确实存在的目录须拒绝——校验只认普通文件，不认目录。"""
        pm, validator = project
        (pm.get_project_path("demo") / "end_frames" / "scene_E1S01.png").mkdir(parents=True)
        result = self._validate_with(pm, validator, END_FRAME_REL)
        assert [e for e in result.errors if "end_frame_image" in e]
        assert not result.valid
