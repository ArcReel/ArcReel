"""server.agent_runtime.sdk_tools 测试共享的替身与 helper；目录级 fixture 在 conftest.py。"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from lib.draft_quarantine import (
    QUARANTINE_KIND_STEP1,
    quarantine_path,
)
from lib.generation_result import (
    GenerationBatchResult,
)
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from server.agent_runtime.sdk_tools._context import ToolContext
from server.agent_runtime.sdk_tools.text_generation import (
    open_step1_for_edit_tool,
    split_reference_video_units_tool,
    validate_and_promote_draft_tool,
)
from tests.fakes import fake_reference_caps_fetcher

# ---------------------------------------------------------------------------
# Generation result contract helpers
# ---------------------------------------------------------------------------


async def _fake_scene_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
    """Stand in for the queue: every scene spec lands its canonical mp4."""

    from lib.generation_queue_client import BatchTaskResult

    return [
        BatchTaskResult(
            resource_id=spec.resource_id,
            task_id="t1",
            status="succeeded",
            result={"file_path": f"videos/scene_{spec.resource_id}.mp4"},
        )
        for spec in specs
    ], []


def _generation_result(out: dict[str, Any]) -> GenerationBatchResult:
    """Read the structured contract out of a tool response, never its text."""

    return GenerationBatchResult.model_validate(out["generation_result"])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_CLAIMED_BASIS_DIGEST = "sha256-v1:" + "a" * 64


class _FakePM:
    def __init__(self, project_name: str, project_dir: Path):
        self._project_name = project_name
        self._project_dir = project_dir
        self.project_payload: dict[str, Any] = {
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "content_mode": "drama",
            "generation_mode": "storyboard",
            "source_kind": "novel",
            "source_language": "中文",
            "overview": {},
            "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
            "characters": {"张三": {"description": "主角"}, "李四": {"description": ""}},
            "scenes": {"村口": {"description": "黄昏的村口"}},
            "props": {},
            "products": {"保温杯": {"description": "不锈钢保温杯", "reference_images": [], "selling_points": []}},
            "style": "anime",
            "style_description": "soft pastel",
        }
        self.script_payload: dict[str, Any] = {
            "content_mode": "narration",
            "episode": 1,
            "segments": [
                {
                    "segment_id": "E1S01",
                    "image_prompt": "村口黄昏",
                    "novel_text": "黄昏时分，风吹过村口。",
                    "video_prompt": {"action": "镜头平移", "camera_motion": "Pan", "ambiance_audio": "风声"},
                    "duration_seconds": 4,
                    "generated_assets": {"storyboard_image": "storyboards/scene_E1S01.png"},
                },
            ],
        }

    def get_project_path(self, _name: str) -> Path:
        return self._project_dir

    def _mirror(self, script_filename: str | None = None) -> None:
        """把内存态落盘并重建产物清单：清单是读取已生成产物的唯一口径。

        清单按当下的项目与剧本重新激活（生产的补录路径），随后补回那些夹具不重建来源凭据的
        产物声明。用例故意构造的畸形条目激活不了，留空清单即可——工具侧本来就该按「产物不
        可用」逐条拒收。
        """

        (self._project_dir / "project.json").write_text(
            json.dumps(self.project_payload, ensure_ascii=False), encoding="utf-8"
        )
        filename = script_filename or self._canonical_script_filename()
        if filename is not None:
            scripts_dir = self._project_dir / "scripts"
            scripts_dir.mkdir(exist_ok=True)
            (scripts_dir / Path(filename).name).write_text(
                json.dumps(self.script_payload, ensure_ascii=False), encoding="utf-8"
            )
        from lib.artifact_activation import activate_artifact_target_state

        try:
            activate_artifact_target_state(self._project_dir, bump_schema=False)
        except Exception:
            # 用例故意构造的畸形项目/剧本激活不了；此处吞掉异常让清单留空，
            # 被测工具随后按「产物不可用」逐条拒收，这正是这些用例要断言的路径。
            pass
        self._register_claims(filename)

    def _canonical_script_filename(self) -> str | None:
        episode = self.script_payload.get("episode")
        return f"episode_{episode}.json" if isinstance(episode, bool) is False and isinstance(episode, int) else None

    def _script_episode(self, script_filename: str | None) -> int | None:
        """剧本身份取自身字段，缺字段时按规范文件名兜底——与生产的解析口径一致。"""

        episode = self.script_payload.get("episode")
        if isinstance(episode, int) and not isinstance(episode, bool) and episode >= 1:
            return episode
        match = re.fullmatch(r"episode_(\d+)\.json", Path(script_filename).name) if script_filename else None
        return int(match.group(1)) if match else None

    def _register_claims(self, script_filename: str | None = None) -> None:
        """把剧本已登记的产物补进清单：生产里它们在产出那一刻就登记过。

        激活能从来源凭据重建的条目以激活结果为准，这里只兜住夹具不重建凭据的那些
        （付费媒体的版本记录、缺 image_prompt 的历史分镜）。
        """

        from lib.artifact_manifest import (
            ArtifactKey,
            ArtifactManifest,
            ArtifactManifestEntry,
            ProjectArtifactManifestAdapter,
        )

        adapter = ProjectArtifactManifestAdapter(self._project_dir)
        manifest = ArtifactManifest(adapter)
        recorded: dict[Any, str] = {}
        episode = self._script_episode(script_filename)
        if episode is not None:
            items = next(
                (
                    self.script_payload[field]
                    for field in ("segments", "scenes", "video_units")
                    if field in self.script_payload
                ),
                [],
            )
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict) or item.get("needs_replan") is True:
                    continue
                resource_id = next(
                    (
                        str(item[field])
                        for field in ("segment_id", "scene_id", "unit_id")
                        if isinstance(item.get(field), str) and item.get(field)
                    ),
                    "",
                )
                assets = item.get("generated_assets")
                if not resource_id or not isinstance(assets, dict):
                    continue
                for field, key_factory in (
                    ("storyboard_image", ArtifactKey.episode_storyboard),
                    ("narration_audio", ArtifactKey.episode_audio),
                    ("video_clip", ArtifactKey.episode_video),
                ):
                    artifact_path = assets.get(field)
                    if not isinstance(artifact_path, str) or not artifact_path:
                        continue
                    absolute = (self._project_dir / artifact_path).resolve()
                    if not absolute.is_file() or not absolute.is_relative_to(self._project_dir.resolve()):
                        continue
                    recorded[key_factory(episode, resource_id)] = artifact_path
        known = set(adapter.snapshot_entries())
        for key, artifact_path in recorded.items():
            if key in known:
                continue
            manifest.register_entry_transactionally(
                key,
                ArtifactManifestEntry(artifact_path=artifact_path, basis_digest=_CLAIMED_BASIS_DIGEST),
            )

    def load_project(self, _name: str) -> dict[str, Any]:
        self._mirror()
        return self.project_payload

    def load_script(self, _name: str, filename: str) -> dict[str, Any]:
        self._mirror(filename)
        return self.script_payload

    def project_exists(self, _name: str) -> bool:
        return True

    def get_pending_characters(self, _name: str) -> list[dict[str, Any]]:
        return [
            {"name": "张三", "description": "主角描述"},
            {"name": "李四", "description": ""},
        ]

    def get_pending_project_scenes(self, _name: str) -> list[dict[str, Any]]:
        return [{"name": "村口", "description": "黄昏村口"}]

    def get_pending_project_props(self, _name: str) -> list[dict[str, Any]]:
        return []

    def get_pending_project_products(self, _name: str) -> list[dict[str, Any]]:
        return [{"name": "保温杯", "description": "不锈钢保温杯"}]


def _fake_reference_projection(
    slot_for=None,
    calls: list[str] | None = None,
    *,
    current_tts_duration_seconds: float | None = None,
):
    """Agent 工具测试用的 in-process request projection adapter。"""

    async def _project(*, project, script, unit, options=None, **_kwargs):
        from lib.reference_video.request_projection import (
            ProviderProjectionCandidate,
            ReferenceUnitRequestProjector,
            ResolvedReferenceAsset,
            unit_reference_declarations,
        )

        references = unit_reference_declarations(project, unit)
        capability = "r2v" if references else "i2v"
        if calls is not None:
            calls.append(capability)
        if slot_for is None:
            requested_seconds = int(unit.get("duration_seconds") or 8)
        else:
            requested_seconds = int(slot_for(None, unit).seconds)

        class _Capabilities:
            async def resolve_candidate(self, project, capability):
                del project
                return ProviderProjectionCandidate(
                    capability=capability,
                    provider_id="fake",
                    model_id=f"fake-{capability}",
                    supported_durations=(requested_seconds,),
                    max_reference_images=9,
                    resolution="1080p",
                    generate_audio=True,
                    requested_generate_audio=True,
                    has_audio_track=True,
                    audio_switch_controllable=True,
                )

        class _Available:
            def is_available(self, asset):
                del asset
                return True

        resolved_assets = [
            ResolvedReferenceAsset(path=Path(f"{reference.type}/{reference.name}.png"), reference=reference)
            for reference in references
        ]
        if options is not None and current_tts_duration_seconds is not None:
            options = replace(options, current_tts_duration_seconds=current_tts_duration_seconds)
        return await ReferenceUnitRequestProjector(_Capabilities(), _Available()).project_current(
            project=project,
            script=script,
            unit=unit,
            resolved_assets=resolved_assets,
            options=options,
        )

    return _project


async def _call(tool_obj, args: dict[str, Any]) -> dict[str, Any]:
    """调工具处理器；工具声明为必填的交付方式在未点名时补成后期配音。

    绝大多数视频用例的主题不是旁白交付，逐个写死这一项只会让它们看起来在断言交付方式。
    补齐条件取工具自己的 schema，新增视频工具无需在测试侧再登记一次。
    专门验证该必填契约的用例直接调 ``tool_obj.handler``，不经过这里。
    """

    schema = tool_obj.input_schema
    required = schema.get("required", ()) if isinstance(schema, dict) else ()
    if "narration_delivery" in required and "narration_delivery" not in args:
        args = {**args, "narration_delivery": "post_production"}
    return await tool_obj.handler(args)


def _activate_unbound_project(fake_ctx: ToolContext, *, generation_mode: str = "storyboard") -> None:
    project = fake_ctx.pm.project_payload  # type: ignore[attr-defined]
    project.update(
        {
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "content_mode": "narration",
            "generation_mode": generation_mode,
            "episodes": [],
        }
    )
    (fake_ctx.project_path / "project.json").write_text(json.dumps(project), encoding="utf-8")


def _reference_video_script(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "content_mode": "narration",
        "episode": 1,
        "video_units": [
            {
                "unit_id": "E1U1",
                "text": "@张三 推门",
                "duration_seconds": 5,
            }
        ],
    }
    payload.update(overrides)
    return payload


def _use_reference_route(fake_ctx: ToolContext) -> None:
    """把 fake 项目切到参考生视频——生成模式是项目级事实，剧本不携带戳。"""
    fake_ctx.pm.project_payload["generation_mode"] = "reference_video"  # type: ignore[attr-defined]


def _rv_generator_returning(units: list[dict], captured: dict[str, Any] | None = None):
    """构造返回指定扁平 units JSON 的假 TextGenerator.create（可选捕获 task_type / project_name）。"""

    class _FakeGenerator:
        async def generate(self, _request, project_name=None):
            if captured is not None:
                captured["generate_project_name"] = project_name

            class _R:
                text = json.dumps({"units": units}, ensure_ascii=False)

            return _R()

    async def fake_create(task_type, project_name=None):
        if captured is not None:
            captured["task_type"] = task_type
            captured["create_project_name"] = project_name
        return _FakeGenerator()

    return fake_create


_RV_NOVEL = "张三在村口等人"


def _rv_project(fake_ctx: ToolContext, generation_mode: str = "reference_video") -> None:
    """把项目声明成参考生视频路径——草稿的拆分 / 晋升 / 阻塞判定都以此为前提。

    盘上的 project.json 与 pm 的内存视图同步：生成入口从盘上读，晋升工具经 ``pm.load_project`` 读。
    """
    (fake_ctx.project_path / "project.json").write_text(
        json.dumps({"content_mode": "narration", "generation_mode": generation_mode}, ensure_ascii=False),
        encoding="utf-8",
    )
    fake_ctx.pm.project_payload["content_mode"] = "narration"  # pyright: ignore[reportAttributeAccessIssue]
    fake_ctx.pm.project_payload["generation_mode"] = generation_mode  # pyright: ignore[reportAttributeAccessIssue]


def _rv_source(fake_ctx: ToolContext) -> None:
    _rv_project(fake_ctx)
    src = fake_ctx.project_path / "source"
    src.mkdir(parents=True)
    (src / "episode_1.txt").write_text(_RV_NOVEL, encoding="utf-8")


def _rv_unit(text: str, *, duration: int = 8, source_text: str = _RV_NOVEL) -> dict:
    """step1 的 LLM 产出形状：一层扁平（时长 + 原文锚 + 引用语法正文）。"""
    return {"duration_seconds": duration, "source_text": source_text, "text": text}


def _derived_reference_names(fake_ctx: ToolContext, text: str) -> list[str]:
    """正文 → 参考图名称：读侧的唯一派生入口，落盘不带 references。"""
    from lib.reference_video.text_parser import derive_references_from_text

    project = json.loads((fake_ctx.project_path / "project.json").read_text(encoding="utf-8"))
    references, _missing = derive_references_from_text(text, project)
    return [reference.name for reference in references]


def _rv_step1_path(fake_ctx: ToolContext):
    return fake_ctx.project_path / "drafts" / "episode_1" / "step1_reference_units.json"


async def _run_rv_split(fake_ctx: ToolContext, monkeypatch, units: list[dict], **caps_kwargs) -> dict:
    from server.agent_runtime.sdk_tools import text_generation as mod

    monkeypatch.setattr(mod, "_fetch_reference_caps_with_fallback", fake_reference_caps_fetcher(**caps_kwargs))
    monkeypatch.setattr(mod.TextGenerator, "create", _rv_generator_returning(units))
    return await _call(split_reference_video_units_tool(fake_ctx), {"episode": 1})


def _rv_quarantine_path(fake_ctx: ToolContext):
    return quarantine_path(fake_ctx.project_path, 1, QUARANTINE_KIND_STEP1)


def _read_rv_quarantine(fake_ctx: ToolContext) -> dict:
    return json.loads(_rv_quarantine_path(fake_ctx).read_text(encoding="utf-8"))


async def _promote(fake_ctx: ToolContext, monkeypatch, **caps_kwargs) -> dict:
    from server.agent_runtime.sdk_tools import text_generation as mod

    if not (fake_ctx.project_path / "project.json").exists():
        _rv_project(fake_ctx)
    monkeypatch.setattr(mod, "_fetch_reference_caps_with_fallback", fake_reference_caps_fetcher(**caps_kwargs))
    return await _call(validate_and_promote_draft_tool(fake_ctx), {"episode": 1})


def _write_rv_step1(fake_ctx: ToolContext, units: list[dict]) -> None:
    """直接铺一份正式 step1（模拟上一轮拆分的落盘产物）。"""
    path = _rv_step1_path(fake_ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"units": units}, ensure_ascii=False), encoding="utf-8")


def _rv_saved_unit(text: str, *, unit_id: str = "E1U01", duration: int = 8) -> dict:
    """正式 step1 的落盘形状（正文 + 机器派生的 unit_id）。"""
    return {
        "unit_id": unit_id,
        "text": text,
        "duration_seconds": duration,
        "source_text": _RV_NOVEL,
    }


async def _open_for_edit(fake_ctx: ToolContext, **args) -> dict:
    if not (fake_ctx.project_path / "project.json").exists():
        _rv_project(fake_ctx)
    return await _call(open_step1_for_edit_tool(fake_ctx), {"episode": 1, **args})


def _nr_caps(default=4, durations=(4, 6, 8)):
    async def fake_caps(_p, _episode=None):
        return default, list(durations)

    return fake_caps


def _nr_project(fake_ctx: ToolContext) -> None:
    _rv_project(fake_ctx, generation_mode="storyboard")


def _nr_source(fake_ctx: ToolContext) -> None:
    _nr_project(fake_ctx)
    src = fake_ctx.project_path / "source"
    src.mkdir(parents=True)
    (src / "episode_1.txt").write_text(_RV_NOVEL, encoding="utf-8")


def _nr_generator_returning(segments: list[dict], captured: dict[str, Any] | None = None):
    """构造返回指定 segments JSON 的假 TextGenerator.create（可选捕获 task_type / project_name）。"""

    class _FakeGenerator:
        async def generate(self, _request, project_name=None):
            if captured is not None:
                captured["generate_project_name"] = project_name

            class _R:
                text = json.dumps({"episode": 1, "segments": segments}, ensure_ascii=False)

            return _R()

    async def fake_create(task_type, project_name=None):
        if captured is not None:
            captured["task_type"] = task_type
            captured["create_project_name"] = project_name
        return _FakeGenerator()

    return fake_create


def _nr_segment(segment_id="E1S01", duration=4, novel_text="张三走向村口。", **extra):
    seg = {
        "segment_id": segment_id,
        "novel_text": novel_text,
        "duration_seconds": duration,
        "segment_break": False,
        "characters_in_segment": [],
        "scenes": [],
        "props": [],
    }
    seg.update(extra)
    return seg
