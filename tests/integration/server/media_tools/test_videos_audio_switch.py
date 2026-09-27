"""Agent 视频入队路径上的音频开关预检。

WebUI 提交入口拒绝的配置（成片恒有声的模型 + 关闭音频），从 Agent 入队同样要被拒——放行会让
编排层按无声路径裁掉全部音色约束，用户拿到失去音色约束的有声成片。分镜图生视频复用
``server.services.tasks.video_caps``，参考生视频由公共 request projection 承载相同判据。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.config.resolver import ConfigResolver
from lib.config.service import ConfigService
from lib.generation.generation_queue_client import TaskSpec
from lib.generation.generation_result import GenerationSelectionMode
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.script.reference_video.request_projection import ReferenceRequestOptions
from lib.script.reference_video.text_parser import extract_mentions
from server.services.admission import video_batch_admission as admission_mod
from server.services.admission.video_batch_admission import admit_reference_video_batch
from server.services.tasks.video_caps import assert_audio_switch_supported
from server.tool_runtime import ToolOutcome
from tests.factories import make_video_request_facts
from tests.integration.server.agent_tool_support import (
    ToolHarness,
    read_generation_result,
    run_generate_videos,
)

_ALWAYS_AUDIBLE = "dashscope/wan2.7-i2v"
_CONTROLLABLE = "ark/doubao-seedance-2-0-260128"


_EPISODE_1 = {"scope": "episode", "episode": 1}


def _admission_codes(out: ToolOutcome[Any]) -> dict[str, list[str]]:
    assert isinstance(out.value, dict)
    return {
        unit["unit_id"]: [problem["code"] for problem in unit["problems"]]
        for unit in out.value["batch_admission"]["units"]
    }


async def _seed_settings(factory: async_sessionmaker[AsyncSession], **settings: str) -> None:
    """把系统设置写进共享 DB fixture 的库里。"""
    async with factory() as session:
        svc = ConfigService(session)
        for key, value in settings.items():
            await svc.set_setting(key, value)
        await session.commit()


def _unit_spec(unit: dict[str, Any]) -> TaskSpec:
    """可入队 unit 的替身 spec：只用于让逐桶去重判定「这条要入队」。"""
    return TaskSpec.from_request(
        task_type="video",
        media_type="video",
        resource_id=str(unit["unit_id"]),
        prompt="镜头",
        script_file="episode_1.json",
    )


class TestAssertAudioSwitchSupported:
    async def test_always_audible_model_with_audio_off_names_provider_and_model(self, db_factory, monkeypatch):
        await _seed_settings(db_factory, default_video_backend=_ALWAYS_AUDIBLE, video_generate_audio="false")
        monkeypatch.setattr("lib.db.async_session_factory", db_factory)

        with pytest.raises(ValueError, match=r"成片恒有声，无法关闭音频") as exc_info:
            await assert_audio_switch_supported({}, "i2v")

        assert "dashscope/wan2.7-i2v" in str(exc_info.value)

    async def test_controllable_model_keeps_the_off_setting(self, db_factory, monkeypatch):
        await _seed_settings(db_factory, default_video_backend=_CONTROLLABLE, video_generate_audio="false")
        monkeypatch.setattr("lib.db.async_session_factory", db_factory)

        await assert_audio_switch_supported({}, "i2v")

        # 闸门放行后关音设置原样生效，没有被改写成有声
        assert await ConfigResolver(db_factory).video_generate_audio_for_project({}) is False


class TestStoryboardRouteGate:
    """分镜图生视频：闸门与创作类型无关，但只在确有任务要入队时才拦。"""

    async def test_gate_is_content_mode_agnostic(self, tmp_path, monkeypatch):
        seen: list[str] = []

        async def _reject(_project, generation_type, **_kwargs):
            seen.append(generation_type)
            raise ValueError("成片恒有声")

        monkeypatch.setattr(admission_mod, "assert_audio_switch_supported", _reject)
        conflict = await admission_mod.audio_switch_conflict({"generation_mode": "storyboard"})
        assert conflict == "成片恒有声"
        assert seen == ["i2v"]

    async def test_voice_characters_resolve_independently_of_the_gate(self, tmp_path, monkeypatch):
        async def _not_silent(_project):
            return False

        monkeypatch.setattr(admission_mod, "resolve_project_is_silent", _not_silent)
        project = {"generation_mode": "storyboard", "characters": {"张三": {"description": "主角"}}}
        assert await admission_mod.resolve_voice_context(project, "drama") == project["characters"]


class TestReferenceRouteGate:
    """参考生视频：按本批真正要入队的 unit 调用公共 request projection。"""

    @staticmethod
    def _stub_current_state(monkeypatch) -> None:
        async def _no_active(**_kwargs):
            return []

        async def _passthrough_options(*, options, **_kwargs):
            return options

        monkeypatch.setattr(admission_mod, "get_active_tasks_for_resources", _no_active)
        monkeypatch.setattr(admission_mod, "prepare_current_reference_video_request_options", _passthrough_options)

    async def test_projects_each_pending_unit_and_skips_done_units(self, tmp_path, monkeypatch):
        seen: list[str] = []

        class _Projection:
            unit_id = "test"
            blocking_problems: tuple[object, ...] = ()
            cost = None
            planned_duration = 8
            request_duration = None
            current_visual_duration = None

            def to_advisory_payload(self):
                return {"allowed": True, "unit_id": "test", "problems": []}

        async def _record(*, unit, **_kwargs):
            # 替身只需产出可预期的分桶信号：本用例的 project 为空，未登记名经生产侧的
            # unit_reference_declarations 会被全部滤掉，故此处按正文提及直接判。
            seen.append("r2v" if extract_mentions(str(unit.get("text") or "")) else "i2v")
            return _Projection()

        self._stub_current_state(monkeypatch)
        monkeypatch.setattr(admission_mod, "project_reference_unit_request", _record)
        units = [
            {"unit_id": "E1U1", "text": "@[张三] 推门"},
            {"unit_id": "E1U2", "text": "@[李四] 举杯"},
            {"unit_id": "E1U3", "text": "空镜：长街"},
        ]
        await admit_reference_video_batch(
            project_name="demo",
            project={},
            project_path=tmp_path,
            script={"video_units": units},
            script_file="episode_1.json",
            units=units,
            request_options=ReferenceRequestOptions(),
            operation="generate_video",
            selection=GenerationSelectionMode.MISSING_ONLY,
            spec_check=_unit_spec,
        )
        assert seen == ["r2v", "r2v", "i2v"]

    async def test_units_that_cannot_be_enqueued_do_not_trigger_projection(self, tmp_path, monkeypatch):
        """不可入队的 unit 不该触发解析：它本就不会被生成，判定停在更早的拒绝上。"""
        called = False

        async def _record(**_kwargs):
            nonlocal called
            called = True

        def _reject(_unit):
            raise ValueError("正文为空")

        self._stub_current_state(monkeypatch)
        monkeypatch.setattr(admission_mod, "project_reference_unit_request", _record)
        admission = await admit_reference_video_batch(
            project_name="demo",
            project={},
            project_path=tmp_path,
            script={"video_units": []},
            script_file="episode_1.json",
            units=[{"unit_id": "E1U1", "text": ""}],
            request_options=ReferenceRequestOptions(),
            operation="generate_video",
            selection=GenerationSelectionMode.MISSING_ONLY,
            spec_check=_reject,
        )
        assert called is False
        assert not admission.admitted


class _EpisodePM:
    """整集工具够用的 pm 替身：一集一个 segment，分镜图有无由调用方决定。

    项目按生产形态构造：当前 schema、剧本在 episodes 账本里绑定，已落盘的分镜图在构造时
    经清单激活登记——清单是读取已生成产物的唯一口径。构造之后用例会往内存剧本里塞畸形
    条目或改写提示词验证工具侧的逐条拒收，那些改动不回写磁盘，清单保持这份干净基线。
    """

    def __init__(self, project_dir: Path, *, with_storyboard: bool) -> None:
        self._project_dir = project_dir
        item: dict[str, Any] = {
            "segment_id": "E1S01",
            "novel_text": "镜头缓缓扫过原野。",
            "image_prompt": "原野远景",
            "video_prompt": "镜头平移",
        }
        if with_storyboard:
            item["generated_assets"] = {"storyboard_image": "storyboards/scene_E1S01.png"}
        self.script_payload: dict[str, Any] = {"content_mode": "narration", "episode": 1, "segments": [item]}
        self.project_payload: dict[str, Any] = {
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
        }
        self._mirror()

    def get_project_path(self, _name: str) -> Path:
        return self._project_dir

    def _mirror(self) -> None:
        """把基线项目落盘并激活产物清单，等价于生产的迁移补录。"""

        from lib.artifacts.artifact_activation import activate_artifact_target_state

        self._project_dir.mkdir(parents=True, exist_ok=True)
        (self._project_dir / "project.json").write_text(
            json.dumps(self.project_payload, ensure_ascii=False), encoding="utf-8"
        )
        scripts_dir = self._project_dir / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        (scripts_dir / "episode_1.json").write_text(
            json.dumps(self.script_payload, ensure_ascii=False), encoding="utf-8"
        )
        activate_artifact_target_state(self._project_dir, bump_schema=False)

    def load_project(self, _name: str) -> dict[str, Any]:
        return self.project_payload

    def load_script(self, _name: str, _filename: str) -> dict[str, Any]:
        return self.script_payload


class TestStoryboardGateSkipsEmptyBatches:
    """没有任务要入队时不触发闸门：存量的关闭音频配置不该把一次空转变成报错。"""

    async def test_all_items_filtered_out_still_fails_without_consulting_the_gate(self, tmp_path, monkeypatch):
        """全部条目缺分镜图时报的应是逐 ID 的输入不可用，而不是音频开关冲突。"""
        project_dir = tmp_path / "demo"
        (project_dir / "storyboards").mkdir(parents=True)
        (project_dir / "storyboards" / "scene_E1S01.png").write_bytes(b"")
        ctx = ToolHarness(project_name="demo", data_root=tmp_path, pm=_EpisodePM(project_dir, with_storyboard=False))
        rejected: list[str] = []

        async def _reject(_project, generation_type, **_kwargs):
            rejected.append(generation_type)
            raise ValueError("成片恒有声")

        monkeypatch.setattr(admission_mod, "assert_audio_switch_supported", _reject)
        enqueue = AsyncMock(return_value=([], []))

        out = await run_generate_videos(ctx, _EPISODE_1, batch_waiter=enqueue)

        assert rejected == []
        result = read_generation_result(out)
        assert result.blocked == ["E1S01"]
        assert [item.problem.code for item in result.items if item.problem is not None] == [
            "generation_unit_input_unusable"
        ]


class TestStoryboardGateEntersAdmission:
    """音频开关冲突与其它缺口一起在建任务之前报全，不留到确认之后才报。"""

    @pytest.fixture(autouse=True)
    def _no_active_tasks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """准入要查活跃任务：单元测试不碰真实数据库，一律按「没有活跃任务」作答。"""

        async def _none(**_kwargs):
            return []

        monkeypatch.setattr(admission_mod, "get_active_tasks_for_resources", _none)

    def _ctx(self, tmp_path: Path) -> ToolHarness:
        project_dir = tmp_path / "demo"
        (project_dir / "storyboards").mkdir(parents=True)
        (project_dir / "storyboards" / "scene_E1S01.png").write_bytes(b"")
        return ToolHarness(
            project_name="demo",
            data_root=tmp_path,
            pm=_EpisodePM(project_dir, with_storyboard=True),
        )

    async def test_audio_switch_conflict_is_reported_as_a_blocked_admission(
        self, tmp_path, set_admission_video_request_facts
    ):
        enqueue = AsyncMock(return_value=([], []))
        set_admission_video_request_facts(make_video_request_facts(requested_generate_audio=False))

        out = await run_generate_videos(self._ctx(tmp_path), _EPISODE_1, batch_waiter=enqueue)

        assert out.value["batch_admission"]["decision"] == "blocked"
        enqueue.assert_not_awaited()
        assert _admission_codes(out) == {"E1S01": ["video_audio_switch_not_supported"]}

    async def test_a_blank_prompt_is_refused_per_unit(self, tmp_path, monkeypatch):
        """空白提示词构造不出 TaskSpec：该条目带自己的问题码进结论，不把整批打成通用报错。"""

        async def _allow(_project, _generation_type, **_kwargs):
            return None

        enqueue = AsyncMock(return_value=([], []))
        monkeypatch.setattr(admission_mod, "assert_audio_switch_supported", _allow)
        ctx = self._ctx(tmp_path)
        ctx.pm.script_payload["segments"][0]["video_prompt"] = "   "

        out = await run_generate_videos(ctx, _EPISODE_1, batch_waiter=enqueue)

        enqueue.assert_not_awaited()
        assert out.value["batch_admission"]["decision"] == "blocked"
        assert _admission_codes(out) == {"E1S01": ["generation_unit_request_invalid"]}

    async def test_the_audio_conflict_joins_the_other_problems_of_the_same_unit(
        self, tmp_path, set_admission_video_request_facts
    ):
        """音频冲突与同一单元的其它缺口写进同一张票：用户一次看全，不必改一条撞一条。"""
        enqueue = AsyncMock(return_value=([], []))
        set_admission_video_request_facts(make_video_request_facts(requested_generate_audio=False))
        ctx = self._ctx(tmp_path)
        ctx.pm.script_payload["segments"][0]["characters_in_segment"] = ["无名氏"]

        out = await run_generate_videos(ctx, _EPISODE_1, batch_waiter=enqueue)

        enqueue.assert_not_awaited()
        assert _admission_codes(out) == {"E1S01": ["reference_asset_unregistered", "video_audio_switch_not_supported"]}
