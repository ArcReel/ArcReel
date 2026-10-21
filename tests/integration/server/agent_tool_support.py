"""Agent 工具测试共享的装配、替身与 helper；``fake_ctx`` 等 fixture 在 ``tests/integration/server/conftest.py``。"""

from __future__ import annotations

import json
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any, overload

from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.db.base import DEFAULT_USER_ID
from lib.generation.generation_queue import GenerationQueue, get_generation_queue
from lib.generation.generation_queue_client import batch_enqueue_and_wait
from lib.generation.generation_result import (
    GenerationBatchResult,
)
from lib.project.project_manager import ProjectManager
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.script.draft_quarantine import (
    QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
    QUARANTINE_KIND_PROMPT_AUTHORING,
    QUARANTINE_KIND_SCRIPT_PLAN,
    quarantine_path,
)
from server.agent_toolset.declaration import ToolDeclaration, invoke_declaration
from server.agent_toolset.envelope import json_value
from server.agent_toolset.toolset import AGENT_TOOLSET
from server.services.project import workflow_planner
from server.tool_runtime import (
    BatchWaiter,
    CallerContext,
    ProjectScope,
    Services,
    TextGenerationResult,
    ToolOutcome,
    ToolProblem,
)
from tests.factories import make_video_request_facts
from tests.fakes import FakeConfigResolver

# ---------------------------------------------------------------------------
# Generation result contract helpers
# ---------------------------------------------------------------------------


def read_generation_result(out: dict[str, Any] | ToolOutcome[Any]) -> GenerationBatchResult:
    """Read the structured contract out of a tool response, never its text."""

    if isinstance(out, ToolOutcome):
        assert isinstance(out.value, dict)
        return GenerationBatchResult.model_validate(out.value["generation_result"])
    return GenerationBatchResult.model_validate(out["generation_result"])


class ToolHarness:
    """一次 Agent 工具调用的装配：会话项目、调用方与 ``Services``；项目管理器、能力解析器与队列可换成替身。

    ``services`` 每次按当前属性重建，用例改了 ``config_resolver`` 等属性后下一次调用即生效。
    """

    def __init__(
        self,
        project_name: str,
        data_root: Path,
        pm: ProjectManager | None = None,
        *,
        config_resolver: ConfigResolver | None = None,
        caller: CallerContext | None = None,
        queue: GenerationQueue | None = None,
    ):
        self.project_name = project_name
        self.data_root = data_root
        self.pm: ProjectManager = pm if pm is not None else ProjectManager(data_root)
        self.config_resolver = config_resolver
        self.caller = caller or CallerContext(user_id=DEFAULT_USER_ID, source="embedded")
        self.queue = queue or get_generation_queue()

    @property
    def project_path(self) -> Path:
        return self.pm.get_project_path(self.project_name)

    @property
    def scope(self) -> ProjectScope:
        return ProjectScope(project_name=self.project_name, data_root=self.data_root)

    @property
    def services(self) -> Services:
        return Services(
            projects=self.pm,
            workflow_planner=workflow_planner.get_workflow_planner(self.pm),
            capabilities=self.config_resolver or ConfigResolver(async_session_factory),
            queue=self.queue,
        )


@overload
async def run_declared_tool[ResultT](
    declaration: ToolDeclaration[Any, ResultT],
    ctx: ToolHarness,
    arguments: dict[str, Any],
    *,
    batch_waiter: BatchWaiter = ...,
    **collaborators: Any,
) -> ToolOutcome[ResultT]: ...


@overload
async def run_declared_tool(
    declaration: str,
    ctx: ToolHarness,
    arguments: dict[str, Any],
    *,
    batch_waiter: BatchWaiter = ...,
    **collaborators: Any,
) -> ToolOutcome[Any]: ...


async def run_declared_tool(
    declaration: ToolDeclaration[Any, Any] | str,
    ctx: ToolHarness,
    arguments: dict[str, Any],
    *,
    batch_waiter: BatchWaiter = batch_enqueue_and_wait,
    **collaborators: Any,
) -> ToolOutcome[Any]:
    """经工具声明的共享入口（两宿主同一入口）调用，拿到 handler 的 ``ToolOutcome``。

    ``declaration`` 可传声明或工具名；内嵌批次等待器随 caller 注入；``collaborators`` 作为 handler 的
    关键字参数注入领域协作者替身。
    """

    if isinstance(declaration, str):
        found = next(item for item in AGENT_TOOLSET if item.name == declaration)
        assert isinstance(found, ToolDeclaration), f"{declaration} 不作用于项目"
        declaration = found
    if collaborators:
        declaration = replace(declaration, handler=partial(declaration.handler, **collaborators))
    caller = replace(ctx.caller, batch_waiter=batch_waiter)
    return await invoke_declaration(declaration, arguments, ctx.scope, caller, ctx.services)


def said(outcome: ToolOutcome[Any]) -> str:
    """结果的可读文本：失败时的 problem detail，文本生成的 message，其余为值的 JSON。"""

    if outcome.problem is not None:
        return outcome.problem.detail
    if isinstance(outcome.value, TextGenerationResult):
        return outcome.value.message
    return json.dumps(json_value(outcome.value), ensure_ascii=False)


def draft_of(outcome: ToolOutcome[Any]) -> dict[str, Any]:
    """草稿工具成功时返回的草稿。"""

    assert outcome.problem is None, outcome.problem
    assert isinstance(outcome.value, dict)
    return outcome.value


def problem_of(outcome: ToolOutcome[Any]) -> ToolProblem:
    assert outcome.problem is not None, outcome.value
    return outcome.problem


async def run_generate_videos(
    ctx: ToolHarness,
    target: dict[str, Any],
    *,
    script: str = "episode_1.json",
    batch_waiter: BatchWaiter = batch_enqueue_and_wait,
    **arguments: Any,
) -> ToolOutcome[Any]:
    """经 ``generate_videos`` 声明入口生成视频，拿到 handler 的 ``ToolOutcome``。"""

    return await run_declared_tool(
        "generate_videos",
        ctx,
        {"script": script, "target": target, **arguments},
        batch_waiter=batch_waiter,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_CLAIMED_BASIS_DIGEST = "sha256-v1:" + "a" * 64


def fake_reference_projection(
    slot_for=None,
    calls: list[str] | None = None,
):
    """Agent 工具测试用的 in-process request projection adapter。"""

    async def _project(*, project, script, unit, options=None, **_kwargs):
        from lib.script.reference_video.request_projection import (
            ReferenceUnitRequestProjector,
            ResolvedReferenceAsset,
            unit_reference_declarations,
        )

        references = unit_reference_declarations(project, unit)
        generation_type = "r2v" if references else "i2v"
        if calls is not None:
            calls.append(generation_type)
        if slot_for is None:
            requested_seconds = int(unit.get("duration_seconds") or 8)
        else:
            requested_seconds = int(slot_for(None, unit).seconds)

        async def _request_facts(generation_type):
            return make_video_request_facts(
                route="reference_video",
                generation_type=generation_type,
                provider_id="fake",
                model_id=f"fake-{generation_type}",
                resolution="1080p",
                supported_durations=(requested_seconds,),
                allowed_durations=(requested_seconds,),
                max_reference_images=9,
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
        return await ReferenceUnitRequestProjector(_request_facts, _Available()).project_current(
            project=project,
            script=script,
            unit=unit,
            resolved_assets=resolved_assets,
            options=options,
        )

    return _project


def fake_caps_resolver(**kwargs: Any) -> Any:
    """构造假能力解析器（见 ``tests.fakes.FakeConfigResolver``）。

    返回值按 ``Any`` 交出：注入点标注的是生产的 ``ConfigResolver``，替身只实现被调到的那几个
    方法，逐个调用点写类型豁免不如在这唯一的构造点交出去。
    """
    return FakeConfigResolver(**kwargs)


def use_fake_caps(fake_ctx: ToolHarness, **kwargs: Any) -> Any:
    """给这个会话装上假能力解析器并返回它。

    ``ToolHarness.config_resolver`` 经 ``Services.capabilities`` 透传给能力 dict 与图像能力的取值器（如
    ``get_video_capabilities`` 的能力载荷、图生图能力闸）。时长档位与声音档不经它：那些读视频请求
    事实，用例用 ``set_video_request_facts`` / ``video_request_facts`` 提供。
    """
    resolver = fake_caps_resolver(**kwargs)
    fake_ctx.config_resolver = resolver
    return resolver


def activate_unbound_project(fake_ctx: ToolHarness, *, generation_mode: str = "storyboard") -> None:
    project = fake_ctx.pm.project_payload
    project.update(
        {
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "content_mode": "narration",
            "generation_mode": generation_mode,
            "episodes": [],
        }
    )
    (fake_ctx.project_path / "project.json").write_text(json.dumps(project), encoding="utf-8")


def reference_video_script(**overrides: Any) -> dict[str, Any]:
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


def use_reference_route(fake_ctx: ToolHarness) -> None:
    """把 fake 项目切到参考生视频——生成模式是项目级事实，剧本不携带戳。"""
    fake_ctx.pm.project_payload["generation_mode"] = "reference_video"


def rv_generator_returning(
    units: list[dict], captured: dict[str, Any] | None = None, *, new_assets: list[dict] | None = None
):
    """构造返回指定扁平 units JSON 的假 TextGenerator.create（可选捕获 task_type / project_name）。"""

    class _FakeGenerator:
        async def generate(self, _request, project_name=None):
            if captured is not None:
                captured["generate_project_name"] = project_name

            class _R:
                text = json.dumps({"units": units, "new_assets": new_assets or []}, ensure_ascii=False)

            return _R()

    async def fake_create(task_type, project_name=None, **kwargs):
        if captured is not None:
            captured["task_type"] = task_type
            captured["create_project_name"] = project_name
            captured["purpose"] = kwargs.get("purpose")
        return _FakeGenerator()

    return fake_create


_RV_NOVEL = "张三在村口等人"


def rv_project(fake_ctx: ToolHarness, generation_mode: str = "reference_video") -> None:
    """把项目声明成参考生视频路径——草稿的拆分 / 晋升 / 阻塞判定都以此为前提。

    盘上的 project.json 与 pm 的内存视图同步：生成入口从盘上读，晋升工具经 ``pm.load_project`` 读。
    """
    fake_ctx.pm.project_payload["content_mode"] = "narration"
    fake_ctx.pm.project_payload["generation_mode"] = generation_mode
    (fake_ctx.project_path / "project.json").write_text(
        json.dumps(fake_ctx.pm.project_payload, ensure_ascii=False),
        encoding="utf-8",
    )


def rv_source(fake_ctx: ToolHarness) -> None:
    rv_project(fake_ctx)
    src = fake_ctx.project_path / "source"
    src.mkdir(parents=True)
    (src / "episode_1.txt").write_text(_RV_NOVEL, encoding="utf-8")


def rv_unit(text: str, *, duration: int = 8, source_text: str = _RV_NOVEL) -> dict:
    """script_plan 的 LLM 产出形状：一层扁平（时长 + 原文锚 + 引用语法正文）。"""
    return {"duration_seconds": duration, "source_text": source_text, "text": text}


def derived_reference_names(fake_ctx: ToolHarness, text: str) -> list[str]:
    """正文 → 参考图名称：读侧的唯一派生入口，落盘不带 references。"""
    from lib.script.reference_video.text_parser import derive_references_from_text

    project = json.loads((fake_ctx.project_path / "project.json").read_text(encoding="utf-8"))
    references, _missing = derive_references_from_text(text, project)
    return [reference.name for reference in references]


def rv_script_plan_path(fake_ctx: ToolHarness):
    return fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_reference_units.json"


async def run_rv_split(
    fake_ctx: ToolHarness, monkeypatch, units: list[dict], *, new_assets: list[dict] | None = None, **caps_kwargs
) -> ToolOutcome[Any]:
    from server import text_generation as mod

    use_fake_caps(fake_ctx, **caps_kwargs)
    monkeypatch.setattr(mod.TextGenerator, "create", rv_generator_returning(units, new_assets=new_assets))
    return await run_declared_tool("generate_script_plan", fake_ctx, {"episode_id": 1})


def rv_quarantine_path(fake_ctx: ToolHarness):
    return quarantine_path(fake_ctx.project_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)


def read_rv_quarantine(fake_ctx: ToolHarness) -> dict:
    return json.loads(rv_quarantine_path(fake_ctx).read_text(encoding="utf-8"))


async def promote_reference_draft(fake_ctx: ToolHarness, **caps_kwargs) -> ToolOutcome[Any]:
    if not (fake_ctx.project_path / "project.json").exists():
        rv_project(fake_ctx)
    use_fake_caps(fake_ctx, **caps_kwargs)
    if rv_quarantine_path(fake_ctx).exists():
        doc_type = "reference_script_plan"
    elif quarantine_path(fake_ctx.project_path, 1, QUARANTINE_KIND_PROMPT_AUTHORING).exists():
        doc_type = "reference_prompt_authoring"
    else:
        doc_type = "reference_script_plan"
    args = {"episode_id": 1, "doc_type": doc_type}
    opened = await run_declared_tool("open_draft", fake_ctx, args)
    revision = draft_of(opened)["revision"] if opened.problem is None else ""
    return await run_declared_tool("promote_draft", fake_ctx, {**args, "base_revision": revision})


def write_rv_script_plan(fake_ctx: ToolHarness, units: list[dict]) -> None:
    """直接铺一份正式 script_plan（模拟上一轮拆分的落盘产物）。"""
    path = rv_script_plan_path(fake_ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"units": units}, ensure_ascii=False), encoding="utf-8")


def rv_saved_unit(text: str, *, unit_id: str = "E1U01", duration: int = 8) -> dict:
    """正式 script_plan 的落盘形状（正文 + 机器派生的 unit_id）。"""
    return {
        "unit_id": unit_id,
        "text": text,
        "duration_seconds": duration,
        "source_text": _RV_NOVEL,
    }


async def open_for_edit(fake_ctx: ToolHarness, **args) -> ToolOutcome[Any]:
    if not (fake_ctx.project_path / "project.json").exists():
        rv_project(fake_ctx)
    return await run_declared_tool(
        "open_draft", fake_ctx, {"episode_id": 1, "doc_type": "reference_script_plan", **args}
    )


def nr_source(fake_ctx: ToolHarness) -> None:
    rv_project(fake_ctx, generation_mode="storyboard")
    src = fake_ctx.project_path / "source"
    src.mkdir(parents=True)
    (src / "episode_1.txt").write_text(_RV_NOVEL, encoding="utf-8")


def nr_generator_returning(
    segments: list[dict], captured: dict[str, Any] | None = None, *, new_assets: list[dict] | None = None
):
    """构造返回指定 segments JSON 的假 TextGenerator.create（可选捕获 task_type / project_name）。"""

    class _FakeGenerator:
        async def generate(self, _request, project_name=None):
            if captured is not None:
                captured["generate_project_name"] = project_name

            class _R:
                text = json.dumps(
                    {"episode": 1, "segments": segments, "new_assets": new_assets or []}, ensure_ascii=False
                )

            return _R()

    async def fake_create(task_type, project_name=None, **kwargs):
        if captured is not None:
            captured["task_type"] = task_type
            captured["create_project_name"] = project_name
            captured["purpose"] = kwargs.get("purpose")
        return _FakeGenerator()

    return fake_create


def nr_segment(segment_id="E1S01", duration=4, novel_text="张三走向村口。", **extra):
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


_DRAMA_NOVEL = "三年后，阿离回到山门。"


def drama_project(fake_ctx: ToolHarness) -> None:
    """把项目声明成 drama + 分镜图生视频，登记 ``drama_scene`` 引用的角色，并铺好源文——正式 script_plan
    的写禁与草稿通道以此为前提。"""
    (fake_ctx.project_path / "project.json").write_text(
        json.dumps(
            {
                "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
                "content_mode": "drama",
                "generation_mode": "storyboard",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    fake_ctx.pm.project_payload["content_mode"] = "drama"
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"
    fake_ctx.pm.project_payload["characters"]["阿离"] = {"description": "少女，青衣"}
    src = fake_ctx.project_path / "source"
    src.mkdir(parents=True, exist_ok=True)
    (src / "episode_1.txt").write_text(_DRAMA_NOVEL, encoding="utf-8")


def drama_scene(**overrides) -> dict:
    scene = {
        "scene_id": "E1S01",
        "duration_seconds": 4,
        "segment_break": False,
        "characters_in_scene": ["阿离"],
        "scenes": [],
        "props": [],
        "scene_description": "阿离站在山门前。",
        "utterances": [{"kind": "dialogue", "speaker": "阿离", "text": "我回来了。"}],
        "source_text": _DRAMA_NOVEL,
    }
    scene.update(overrides)
    return scene


def drama_script_plan_path(fake_ctx: ToolHarness) -> Path:
    return fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_normalized_script.json"


def drama_quarantine_path(fake_ctx: ToolHarness) -> Path:
    return quarantine_path(fake_ctx.project_path, 1, QUARANTINE_KIND_DRAMA_SCRIPT_PLAN)


def write_drama_script_plan(fake_ctx: ToolHarness, scenes: list[dict]) -> None:
    path = drama_script_plan_path(fake_ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"title": "第一集", "scenes": scenes}, ensure_ascii=False), encoding="utf-8")


def read_drama_quarantine(fake_ctx: ToolHarness) -> dict:
    return json.loads(drama_quarantine_path(fake_ctx).read_text(encoding="utf-8"))


async def open_drama_for_edit(fake_ctx: ToolHarness, **args) -> ToolOutcome[Any]:
    return await run_declared_tool("open_draft", fake_ctx, {"episode_id": 1, "doc_type": "drama_script_plan", **args})


async def promote_drama(fake_ctx: ToolHarness, durations=(4, 6, 8)) -> ToolOutcome[Any]:
    use_fake_caps(fake_ctx, supported_durations=durations, default_duration=durations[0])
    args = {"episode_id": 1, "doc_type": "drama_script_plan"}
    opened = await run_declared_tool("open_draft", fake_ctx, args)
    revision = draft_of(opened)["revision"]
    return await run_declared_tool("promote_draft", fake_ctx, {**args, "base_revision": revision})


def nr_script_plan_path(fake_ctx: ToolHarness) -> Path:
    return fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json"


def nr_quarantine_path(fake_ctx: ToolHarness) -> Path:
    return quarantine_path(fake_ctx.project_path, 1, QUARANTINE_KIND_NARRATION_SCRIPT_PLAN)


def read_nr_quarantine(fake_ctx: ToolHarness) -> dict:
    return json.loads(nr_quarantine_path(fake_ctx).read_text(encoding="utf-8"))


def write_nr_script_plan(fake_ctx: ToolHarness, segments: list[dict]) -> None:
    path = nr_script_plan_path(fake_ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"segments": segments}, ensure_ascii=False), encoding="utf-8")


async def open_nr_for_edit(fake_ctx: ToolHarness, **args) -> ToolOutcome[Any]:
    return await run_declared_tool(
        "open_draft", fake_ctx, {"episode_id": 1, "doc_type": "narration_script_plan", **args}
    )


async def promote_nr(fake_ctx: ToolHarness, durations=(4, 6, 8)) -> ToolOutcome[Any]:
    use_fake_caps(fake_ctx, supported_durations=durations, default_duration=durations[0])
    args = {"episode_id": 1, "doc_type": "narration_script_plan"}
    opened = await run_declared_tool("open_draft", fake_ctx, args)
    revision = draft_of(opened)["revision"]
    return await run_declared_tool("promote_draft", fake_ctx, {**args, "base_revision": revision})
