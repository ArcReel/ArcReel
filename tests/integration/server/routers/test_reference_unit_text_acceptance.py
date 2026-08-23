"""参考生视频单元正文的贯穿验收：v8 项目 → v9 迁移 → 两条写路径 → 真实生成请求投影。

覆盖一条完整的缝：磁盘上的旧形状（``shots[]`` + ``references[]``）迁到正文形态后，REST 与
Agent 剧本工具读写的是同一份正文，而参考图集合按正文里 ``@[名称]`` 的首次提及顺序在投影时
派生——落盘里没有任何一份可以与正文分叉的引用列表。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.episode_paths import REFERENCE_VIDEO_STEP1_FILENAME, episode_drafts_dir
from lib.project_manager import ProjectManager
from lib.project_migrations.runner import migrate_project_dir
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.reference_video.script_preview import WARN_UNREGISTERED_MENTION
from server.agent_runtime.sdk_tools._context import ToolContext
from server.agent_runtime.sdk_tools.patch_script import (
    get_episode_script_revision_tool,
    patch_episode_script_tool,
)
from server.auth import CurrentUserInfo, get_current_user
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.fakes import fake_reference_request_projector

_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x04\x00\x00\x00\x04"
    b"\x08\x02\x00\x00\x00&\x93\t)\x00\x00\x00\x13IDATx\x9cc<\x91b\xc4\x00"
    b"\x03Lp\x16^\x0e\x00E\xf6\x01f\xac\xf5\x15\xfa\x00\x00\x00\x00IEND\xaeB`\x82"
)

_SCRIPT_FILE = "episode_1.json"
_UNIT_ID = "E1U1"
_UNIT_DURATION = 8
#: 供应商能力替身的档位含单元编排时长，投影因此不产生改档确认类问题。
_SUPPORTED_DURATIONS = (4, 8, 12)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


class _Acceptance:
    """一个已迁到 v9 的参考生视频项目，连同它的 REST 客户端与 Agent 工具上下文。"""

    def __init__(
        self,
        *,
        client: TestClient,
        pm: ProjectManager,
        project_dir: Path,
        tool_ctx: ToolContext,
    ) -> None:
        self.client = client
        self.pm = pm
        self.project_dir = project_dir
        self.tool_ctx = tool_ctx

    def script_on_disk(self) -> dict[str, Any]:
        return _read_json(self.project_dir / "scripts" / _SCRIPT_FILE)

    def unit_on_disk(self, unit_id: str = _UNIT_ID) -> dict[str, Any]:
        return next(u for u in self.script_on_disk()["video_units"] if u["unit_id"] == unit_id)

    def rest_units(self) -> list[dict[str, Any]]:
        resp = self.client.get("/api/v1/projects/demo/reference-videos/episodes/1/units")
        assert resp.status_code == 200, resp.text
        return resp.json()["units"]

    def patch_body_over_rest(self, text: str, unit_id: str = _UNIT_ID) -> dict[str, Any]:
        resp = self.client.patch(
            f"/api/v1/projects/demo/reference-videos/episodes/1/units/{unit_id}",
            json={"prompt": text},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["unit"]

    async def patch_body_over_agent_tool(self, text: str, unit_id: str = _UNIT_ID) -> dict[str, Any]:
        revision = await get_episode_script_revision_tool(self.tool_ctx).handler({"script": _SCRIPT_FILE})
        output = await patch_episode_script_tool(self.tool_ctx).handler(
            {
                "script": _SCRIPT_FILE,
                "expected_revision": revision["revision"],
                "operations": [{"op": "update", "id": unit_id, "fields": {"text": text}}],
            }
        )
        assert not output.get("is_error"), output
        return output

    async def project_request(self, unit_id: str = _UNIT_ID):
        """按磁盘上的当前正文投影一次真实生成请求（仅替换供应商能力查询）。"""
        projector = fake_reference_request_projector(durations=_SUPPORTED_DURATIONS)
        return await projector(
            project=self.pm.load_project("demo"),
            script=self.script_on_disk(),
            unit=self.unit_on_disk(unit_id),
            project_path=self.project_dir,
        )

    def preview_warnings(self, text: str) -> list[dict[str, Any]]:
        resp = self.client.post(
            "/api/v1/projects/demo/reference-videos/episodes/1/script-preview",
            json={"prompt": text},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["warnings"]


def _v8_unit(unit_id: str, *, texts: list[str], references: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "unit_id": unit_id,
        "duration_seconds": _UNIT_DURATION,
        "transition_to_next": "cut",
        "shots": [{"shot_id": f"{unit_id}S{i + 1}", "text": text} for i, text in enumerate(texts)],
        "references": references,
        "generated_assets": {"status": "pending", "video_clip": None},
    }


@pytest.fixture
def acceptance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Acceptance:
    projects_root = tmp_path / "projects"
    pm = ProjectManager(str(projects_root))
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "写实", "narration")
    project_dir = pm.get_project_path("demo")

    for relative in (
        "characters/阿离.png",
        "characters/陆沉.png",
        "products/手环_正面.png",
        "products/手环_侧面.png",
    ):
        path = project_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_TINY_PNG)

    def _seed(project: dict[str, Any]) -> None:
        project["generation_mode"] = "reference_video"
        project["characters"] = {
            "阿离": {"description": "主角", "character_sheet": "characters/阿离.png"},
            "陆沉": {"description": "旧友", "character_sheet": "characters/陆沉.png"},
        }
        project["products"] = {
            # 无资产图的商品：执行期注入它登记的全部原图，且不因类型排到最前。
            "灵犀手环": {
                "description": "银色腕表",
                "reference_images": ["products/手环_正面.png", "products/手环_侧面.png"],
            },
        }
        project["episodes"] = [{"episode": 1, "title": "雨夜", "script_file": f"scripts/{_SCRIPT_FILE}"}]
        # 迁移入口按落盘版本决定是否改写，故把项目退回旧形态再跑整条链。
        project["schema_version"] = 8

    pm.update_project("demo", _seed)

    _write_json(
        project_dir / "scripts" / _SCRIPT_FILE,
        {
            "episode": 1,
            "title": "雨夜",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "summary": "雨夜相遇",
            "novel": {"title": "N", "chapter": "1"},
            "video_units": [
                _v8_unit(
                    _UNIT_ID,
                    texts=["镜头1：@[阿离] 推开门", "镜头2：雨落在石板上"],
                    references=[{"type": "character", "name": "阿离"}],
                ),
            ],
        },
    )
    _write_json(
        episode_drafts_dir(project_dir, 1) / REFERENCE_VIDEO_STEP1_FILENAME,
        {
            "episode": 1,
            "units": [
                {
                    "unit_id": _UNIT_ID,
                    "duration_seconds": _UNIT_DURATION,
                    "shots": [{"text": "草稿上半"}, {"text": "草稿下半"}],
                    "references": [{"type": "character", "name": "阿离"}],
                }
            ],
        },
    )

    assert migrate_project_dir(project_dir) is True

    from server.routers import reference_videos as router_mod

    monkeypatch.setattr(router_mod, "get_project_manager", lambda: pm)
    monkeypatch.setattr(
        router_mod,
        "project_reference_unit_request",
        fake_reference_request_projector(durations=_SUPPORTED_DURATIONS),
    )

    async def _fake_caps(*_args: object, **_kwargs: object) -> dict[str, Any]:
        return {"voice_consistency": "soft", "requested_generate_audio": True, "max_reference_audio_count": 2}

    monkeypatch.setattr(router_mod, "project_video_caps", _fake_caps)

    app = FastAPI()
    app.include_router(router_mod.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="u1", sub="test", role="admin")

    return _Acceptance(
        client=TestClient(app),
        pm=pm,
        project_dir=project_dir,
        tool_ctx=ToolContext(project_name="demo", projects_root=projects_root, pm=pm),
    )


def test_migrated_body_is_the_only_shape_rest_serves(acceptance: _Acceptance) -> None:
    """迁移后剧本与草稿都只剩正文，REST 读到的正是磁盘上那一段。"""
    project = _read_json(acceptance.project_dir / "project.json")
    assert project["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION

    unit = acceptance.unit_on_disk()
    assert unit["text"] == "镜头1：@[阿离] 推开门\n镜头2：雨落在石板上"
    assert "shots" not in unit
    assert "references" not in unit

    draft = _read_json(episode_drafts_dir(acceptance.project_dir, 1) / REFERENCE_VIDEO_STEP1_FILENAME)
    assert draft["units"][0]["text"] == "草稿上半\n草稿下半"
    assert "shots" not in draft["units"][0]

    served = acceptance.rest_units()
    assert [u["text"] for u in served] == [unit["text"]]
    assert all("references" not in u and "shots" not in u for u in served)


async def test_rest_and_agent_tool_write_the_same_body(acceptance: _Acceptance) -> None:
    """两条写路径读写同一份正文：后写的覆盖先写的，落盘与 REST 始终同形。"""
    rest_body = "@[陆沉] 收伞进门"
    patched = acceptance.patch_body_over_rest(rest_body)
    assert patched["text"] == rest_body
    assert acceptance.unit_on_disk()["text"] == rest_body

    agent_body = "@[阿离] 抬眼看向 @[陆沉]"
    await acceptance.patch_body_over_agent_tool(agent_body)
    assert acceptance.unit_on_disk()["text"] == agent_body
    assert [u["text"] for u in acceptance.rest_units()] == [agent_body]

    projection = await acceptance.project_request()
    assert [(ref.type, ref.name) for ref in projection.declared_references] == [
        ("character", "阿离"),
        ("character", "陆沉"),
    ]


_BODY_CASES = [
    pytest.param(
        "@[阿离] 独自站在窗前",
        [("character", "阿离")],
        ["阿离.png"],
        id="single-character",
    ),
    pytest.param(
        "@[陆沉] 递出伞，@[阿离] 接过后 @[陆沉] 转身",
        [("character", "陆沉"), ("character", "阿离")],
        ["陆沉.png", "阿离.png"],
        id="multi-character-first-mention-order",
    ),
    pytest.param(
        "@[阿离] 抬起手腕，@[灵犀手环] 的表盘亮起",
        [("character", "阿离"), ("product", "灵犀手环")],
        ["阿离.png", "手环_正面.png", "手环_侧面.png"],
        id="product-images-follow-mention-order",
    ),
    pytest.param(
        "@[阿离]{你终于来了} 门外 @[陆沉] 撑着伞",
        [("character", "陆沉")],
        ["陆沉.png"],
        id="speaker-slot-binds-voice-only",
    ),
    pytest.param(
        "@[无名氏] 从巷口走过，@[阿离] 回头",
        [("character", "阿离")],
        ["阿离.png"],
        id="unresolved-mention-warns-only",
    ),
    pytest.param(
        "雨落在石板上，远处传来打更声",
        [],
        [],
        id="no-mention-stays-empty",
    ),
]


@pytest.mark.parametrize(("body", "expected_references", "expected_images"), _BODY_CASES)
async def test_body_shape_drives_the_reference_images_of_the_real_request(
    acceptance: _Acceptance,
    body: str,
    expected_references: list[tuple[str, str]],
    expected_images: list[str],
) -> None:
    acceptance.patch_body_over_rest(body)

    projection = await acceptance.project_request()

    assert [(ref.type, ref.name) for ref in projection.declared_references] == expected_references
    assert [asset.path.name for asset in projection.request_assets] == expected_images
    assert projection.hydrated_capability == ("r2v" if expected_images else "i2v")
    assert [problem.code for problem in projection.problems if problem.blocking] == []


async def test_unresolved_mention_only_warns_and_still_generates(acceptance: _Acceptance) -> None:
    """未登记的 ``@[名称]`` 不产生参考图、不阻断，其余提及照常解析。"""
    body = "@[无名氏] 从巷口走过，@[阿离] 回头"
    acceptance.patch_body_over_rest(body)

    warnings = acceptance.preview_warnings(body)
    assert [w["key"] for w in warnings] == [WARN_UNREGISTERED_MENTION]

    projection = await acceptance.project_request()
    assert projection.problems == ()
    assert [asset.path.name for asset in projection.request_assets] == ["阿离.png"]
