"""step1→step2 审核 gate 的服务层与纯逻辑测试。

只测外部可观察行为：审核状态流转（step1 产出 → pending → 阻塞 → 确认 → confirmed → 放行）、
适用范围、内容编辑后重新待审、结构校验。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib import script_review
from lib.json_io import atomic_write_json
from lib.project_manager import ProjectManager
from lib.reference_video.draft_validation import DraftViolation
from lib.reference_video.quarantine import (
    QUARANTINE_KIND_STEP1,
    QUARANTINE_KIND_STEP2,
    clear_quarantine,
    quarantine_path,
    write_quarantine,
)
from server.services.script_review import ScriptReviewError, ScriptReviewService


def _drama_step1() -> dict:
    return {
        "title": "第一集",
        "scenes": [
            {
                "scene_id": "E1S01",
                "duration_seconds": 8,
                "segment_break": False,
                "characters_in_scene": ["阿离"],
                "scenes": [],
                "props": [],
                "scene_description": "雨夜，阿离立于屋檐下",
                "utterances": [
                    {"kind": "voiceover", "speaker": None, "text": "三年后。"},
                    {"kind": "dialogue", "speaker": "阿离", "text": "你终于回来了。"},
                ],
                "source_text": "三年后，阿离立于屋檐下，轻声道：你终于回来了。",
            }
        ],
    }


def _narration_step1() -> dict:
    return {
        "episode": 1,
        "segments": [
            {
                "segment_id": "E1S01",
                "novel_text": "裴与出征后的第二年，送回一个襁褓中的婴儿。",
                "duration_seconds": 6,
                "segment_break": False,
                "characters_in_segment": ["裴与"],
                "scenes": [],
                "props": [],
            }
        ],
    }


def _rv_step1() -> dict:
    """reference_video step1 结构化中间态（``step1_reference_units.json`` 形状）。

    references 由 shot 文本 ``@[名称]`` 机械派生（此处预填与文本一致的期望值）。
    """
    return {
        "units": [
            {
                "unit_id": "E1U01",
                "shots": [
                    {"text": "@[阿离] 立于屋檐下，望向雨幕。"},
                    {"text": "@[裴与] 策马自远方而来。"},
                ],
                "references": [
                    {"type": "character", "name": "阿离"},
                    {"type": "character", "name": "裴与"},
                ],
                "duration_seconds": 8,
            }
        ],
    }


def _stub_video_caps(
    monkeypatch: pytest.MonkeyPatch,
    supported_durations: list[int] | None,
    *,
    provider_id: str = "custom-acme",
    model: str = "acme-video",
) -> None:
    """替身审阅门的视频能力查询，按给定档位表作答。

    档位表经 caps 注入而非项目字段：caps（DB 驱动的能力查询）是自定义供应商唯一的档位来源，
    也是审阅门实际走的那条路径。``supported_durations`` 为 None 时返回空 caps，等价于
    「解析不到型号」。默认身份取自定义供应商——它不在 ``PROVIDER_REGISTRY``，不带联动约束，
    档位表因而原样生效。
    """
    from server.services import script_review as mod

    async def _fake_caps(_project, _episode=None):
        if supported_durations is None:
            return {}
        return {"provider_id": provider_id, "model": model, "supported_durations": list(supported_durations)}

    monkeypatch.setattr(mod, "resolve_video_caps", _fake_caps)


@pytest.fixture(autouse=True)
def _unresolvable_video_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    """本模块默认让能力查询解析不到型号：不碰 DB，也不让系统级默认模型的档位漂进断言。

    需要具体档位表的用例用 ``_stub_video_caps`` 就地覆盖。
    """
    _stub_video_caps(monkeypatch, None)


def _make_project(
    tmp_path: Path,
    content_mode: str,
    *,
    generation_mode: str | None = None,
) -> ProjectManager:
    """建测试项目；档位表另经 ``_stub_video_caps`` 注入。"""
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", content_mode)
    pm.add_character("demo", "阿离", "少女")
    pm.add_character("demo", "裴与", "将军")
    pm.add_episode("demo", 1, "第一集", "scripts/episode_1.json")
    if generation_mode is not None:

        def _set_mode(p: dict) -> None:
            p["generation_mode"] = generation_mode

        pm.update_project("demo", _set_mode)
    return pm


def _write_step1(pm: ProjectManager, content_mode: str, content: dict) -> Path:
    filename = "step1_normalized_script.json" if content_mode == "drama" else "step1_segments.json"
    drafts = pm.get_project_path("demo") / "drafts" / "episode_1"
    drafts.mkdir(parents=True, exist_ok=True)
    path = drafts / filename
    atomic_write_json(path, content)
    return path


def _write_rv_step1(pm: ProjectManager, content: dict) -> Path:
    """写出 reference_video 的结构化 step1（``step1_reference_units.json``）。"""
    drafts = pm.get_project_path("demo") / "drafts" / "episode_1"
    drafts.mkdir(parents=True, exist_ok=True)
    path = drafts / "step1_reference_units.json"
    atomic_write_json(path, content)
    return path


@pytest.mark.integration
def test_content_fingerprint_of_data_matches_path_based_fingerprint(tmp_path: Path):
    """对同一份内容，从已解析对象取的指纹须与对文件路径取的指纹相同——两者共用同一套
    规范化逻辑，调用方才能安全地用前者替代"读入内存后再对路径复核一次"的二次读盘。
    """
    path = tmp_path / "content.json"
    data = {"b": 2, "a": 1, "nested": {"z": [3, 2, 1]}}
    atomic_write_json(path, data)

    assert script_review.content_fingerprint_of_data(data) == script_review.content_fingerprint(path)


def _write_step2(pm: ProjectManager) -> Path:
    """写出 step2 产物（生成的剧本 JSON），模拟「已产 step2」。"""
    scripts = pm.get_project_path("demo") / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    path = scripts / "episode_1.json"
    atomic_write_json(path, {"title": "第一集", "scenes": []})
    return path


def _make_manual_split_project(tmp_path: Path, content_mode: str) -> ProjectManager:
    """手动预拆分场景：绕过分集规划器，``episodes[]`` 账本为空，仅有派生 source/episode_N.txt。"""
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", content_mode)
    return pm


def _write_source_text(pm: ProjectManager, filename: str, text: str) -> Path:
    source_dir = pm.get_project_path("demo") / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    path = source_dir / filename
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 状态流转（drama）
# ---------------------------------------------------------------------------


class TestDramaGateFlow:
    async def test_no_step1_then_pending_then_confirmed(self, tmp_path):
        pm = _make_project(tmp_path, "drama")
        svc = ScriptReviewService(pm)

        # step1 未产出
        assert (await svc.get_state("demo", 1))["status"] == "no_step1"

        # step1 产出 → 可审中间态、阻塞
        _write_step1(pm, "drama", _drama_step1())
        state = await svc.get_state("demo", 1)
        assert state["status"] == "pending_review"
        assert state["content"]["scenes"][0]["scene_id"] == "E1S01"
        assert state["content"]["scenes"][0]["utterances"][1]["speaker"] == "阿离"
        project_path = pm.get_project_path("demo")
        project = pm.load_project("demo")
        assert script_review.gate_blocks_step2(project_path, project, 1) is True

        # 确认 → 放行
        confirmed = await svc.confirm("demo", 1)
        assert confirmed["status"] == "confirmed"
        assert confirmed["confirmed_at"]
        project = pm.load_project("demo")
        assert script_review.gate_blocks_step2(project_path, project, 1) is False

    async def test_editing_step1_after_confirm_repends(self, tmp_path):
        pm = _make_project(tmp_path, "drama")
        svc = ScriptReviewService(pm)
        _write_step1(pm, "drama", _drama_step1())
        await svc.confirm("demo", 1)
        assert (await svc.get_state("demo", 1))["status"] == "confirmed"

        # 内容变更（指纹漂移）→ 自动重新待审
        edited = _drama_step1()
        edited["scenes"][0]["utterances"][1]["text"] = "你怎么才回来。"
        await svc.save_content("demo", 1, edited)

        state = await svc.get_state("demo", 1)
        assert state["status"] == "pending_review"
        assert state["content"]["scenes"][0]["utterances"][1]["text"] == "你怎么才回来。"

    async def test_whitespace_reformat_keeps_confirmed(self, tmp_path):
        """纯键序 / 空白重排不改语义 → 指纹不变、保持 confirmed。"""
        pm = _make_project(tmp_path, "drama")
        svc = ScriptReviewService(pm)
        path = _write_step1(pm, "drama", _drama_step1())
        await svc.confirm("demo", 1)

        # 同内容、不同缩进 / 键序重写
        path.write_text(json.dumps(_drama_step1(), ensure_ascii=False, indent=4), encoding="utf-8")
        assert (await svc.get_state("demo", 1))["status"] == "confirmed"


# ---------------------------------------------------------------------------
# 状态流转（narration，共用同一 gate）
# ---------------------------------------------------------------------------


class TestNarrationGateFlow:
    async def test_pending_then_confirm(self, tmp_path):
        pm = _make_project(tmp_path, "narration")
        svc = ScriptReviewService(pm)
        _write_step1(pm, "narration", _narration_step1())

        state = await svc.get_state("demo", 1)
        assert state["status"] == "pending_review"
        assert state["content"]["segments"][0]["novel_text"].startswith("裴与")

        assert (await svc.confirm("demo", 1))["status"] == "confirmed"

    async def test_edit_novel_text_repends(self, tmp_path):
        pm = _make_project(tmp_path, "narration")
        svc = ScriptReviewService(pm)
        _write_step1(pm, "narration", _narration_step1())
        await svc.confirm("demo", 1)

        edited = _narration_step1()
        edited["segments"][0]["novel_text"] = "裴与出征后的第三年。"
        await svc.save_content("demo", 1, edited)
        assert (await svc.get_state("demo", 1))["status"] == "pending_review"


# ---------------------------------------------------------------------------
# 状态流转（reference_video，跨 content_mode 共用同一 gate）
# ---------------------------------------------------------------------------


class TestReferenceVideoGateFlow:
    async def test_no_step1_then_pending_then_confirmed(self, tmp_path):
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = ScriptReviewService(pm)
        project_path = pm.get_project_path("demo")

        # step1 未产出
        assert (await svc.get_state("demo", 1))["status"] == "no_step1"

        # step1 产出 → 可审中间态、阻塞 step2
        _write_rv_step1(pm, _rv_step1())
        state = await svc.get_state("demo", 1)
        assert state["status"] == "pending_review"
        assert state["content"]["units"][0]["unit_id"] == "E1U01"
        assert state["content"]["units"][0]["shots"][0]["text"].startswith("@[阿离]")
        assert script_review.gate_blocks_step2(project_path, pm.load_project("demo"), 1) is True

        # 确认 → 放行
        confirmed = await svc.confirm("demo", 1)
        assert confirmed["status"] == "confirmed"
        assert confirmed["confirmed_at"]
        assert script_review.gate_blocks_step2(project_path, pm.load_project("demo"), 1) is False

    async def test_editing_shot_text_repends_and_rederives_references(self, tmp_path):
        """编辑 shot 文本 → 重新待审；references 随正文 @ 引用机械重派生（不采用入参陈旧值）。"""
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        pm.add_scenes_batch("demo", {"屋檐": {"description": "雨夜屋檐"}})
        svc = ScriptReviewService(pm)
        _write_rv_step1(pm, _rv_step1())
        await svc.confirm("demo", 1)
        assert (await svc.get_state("demo", 1))["status"] == "confirmed"

        # 正文引用从 @[裴与] 改为 @[屋檐]，同时故意保留陈旧 references（应被重派生覆盖）。
        edited = _rv_step1()
        edited["units"][0]["shots"][1]["text"] = "镜头扫过 @[屋檐]。"
        await svc.save_content("demo", 1, edited)

        state = await svc.get_state("demo", 1)
        assert state["status"] == "pending_review"
        refs = state["content"]["units"][0]["references"]
        assert [(r["type"], r["name"]) for r in refs] == [("character", "阿离"), ("scene", "屋檐")]

    async def test_quarantined_step1_blocks_confirm_and_step2(self, tmp_path):
        """隔离草稿在场 → 确认被拒、step2 被阻塞，即使正式 step1 早已确认过。

        隔离态与「正式 step1 的内容指纹」是两件事：重拆分违约时正式文件原封不动，只看指纹
        会把该集判成 confirmed 并放行，用户看到的却是上一版内容。
        """
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = ScriptReviewService(pm)
        project_path = pm.get_project_path("demo")
        _write_rv_step1(pm, _rv_step1())
        await svc.confirm("demo", 1)
        assert (await svc.get_state("demo", 1))["status"] == "confirmed"

        write_quarantine(
            project_path,
            1,
            QUARANTINE_KIND_STEP1,
            content={"units": []},
            violations=[DraftViolation("坏", code="empty_text", label="unit E1U01")],
        )

        assert (await svc.get_state("demo", 1))["status"] == "pending_review"
        assert script_review.gate_blocks_step2(project_path, pm.load_project("demo"), 1) is True
        with pytest.raises(ScriptReviewError) as exc:
            await svc.confirm("demo", 1)
        assert exc.value.code == "quarantined"

        # 草稿清除后回到既有的指纹判定（内容未变，确认仍然有效）
        clear_quarantine(project_path, 1, QUARANTINE_KIND_STEP1)
        assert (await svc.get_state("demo", 1))["status"] == "confirmed"

    def test_quarantine_not_applicable_to_non_reference_paths(self, tmp_path):
        """drama / narration 无隔离草稿概念：路径解析返回 None，状态判定不受影响。"""
        pm = _make_project(tmp_path, "drama")
        project_path = pm.get_project_path("demo")
        assert script_review.step1_quarantine_path(project_path, pm.load_project("demo"), 1) is None
        assert script_review.step1_quarantined(project_path, pm.load_project("demo"), 1) is False

    async def test_confirm_rejects_unit_duration_out_of_range(self, tmp_path):
        """损坏的 step1（unit 时长越界）→ 确认被结构校验拒绝，不放行 step2。"""
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = ScriptReviewService(pm)
        bad = _rv_step1()
        bad["units"][0]["duration_seconds"] = 9999  # 超出 unit 时长的结构合理性区间
        _write_rv_step1(pm, bad)
        with pytest.raises(ScriptReviewError) as exc:
            await svc.confirm("demo", 1)
        assert exc.value.code == "invalid_content"

    async def test_confirm_rederives_references_when_step1_edited_outside_save_content(self, tmp_path):
        """confirm 前直改 step1 文件（绕过 save_content，如 agent Write/Edit 直改 drafts/）→
        确认时按当前正文重派生 references 并落盘，不放行陈旧引用。"""
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        pm.add_scenes_batch("demo", {"屋檐": {"description": "雨夜屋檐"}})
        svc = ScriptReviewService(pm)

        # 直改正文引用为 @[屋檐]，但故意保留旧 references（模拟绕过 save_content 的直写）。
        stale = _rv_step1()
        stale["units"][0]["shots"][1]["text"] = "镜头扫过 @[屋檐]。"
        path = _write_rv_step1(pm, stale)

        confirmed = await svc.confirm("demo", 1)
        assert confirmed["status"] == "confirmed"
        refs = confirmed["content"]["units"][0]["references"]
        assert [(r["type"], r["name"]) for r in refs] == [("character", "阿离"), ("scene", "屋檐")]

        # 落盘内容也已更新（confirm 记录的指纹对应重派生后的内容，非编辑前的陈旧版本）。
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert [(r["type"], r["name"]) for r in on_disk["units"][0]["references"]] == [
            ("character", "阿离"),
            ("scene", "屋檐"),
        ]

    @pytest.mark.integration
    async def test_reference_duration_tiers_narrows_raw_set_by_resolution_constraint(self, tmp_path, monkeypatch):
        """gate 下拉的档位须按分辨率联动约束收窄，与 step2 落盘前的校验同一把尺。

        Veo 3.1 项目未配置分辨率时按兜底档位（1080p）算，该档位只接受 8 秒；不收窄的话
        get_state 暴露的档位表会让用户选中 4/6 秒，save + confirm 都不拦，直到 step2
        ``_assert_reference_step1_ready`` 才硬拒——用户已确认过的内容变成付完钱才失败。
        """
        from server.services import script_review as mod

        _stub_video_caps(monkeypatch, [4, 6, 8])
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = ScriptReviewService(pm)

        async def _fake_caps(_project, _episode=None):
            return {
                "provider_id": "gemini-aistudio",
                "model": "veo-3.1-generate-preview",
                "supported_durations": [4, 6, 8],
            }

        monkeypatch.setattr(mod, "resolve_video_caps", _fake_caps)
        tiers = await svc.get_reference_duration_tiers("demo", 1)
        assert tiers == {"with_references": [8], "without_references": [8]}

    @pytest.mark.integration
    async def test_reference_duration_tiers_none_when_caps_and_raw_both_unresolved(self, tmp_path, monkeypatch):
        """caps 解析失败、且 registry 身份也拿不到时为 None，
        呈现层退回未收窄的 ``supported_durations``（同 clamp 的回退口径）。

        项目完全未配置视频型号也不代表 caps 会失败——``resolve_video_caps`` 内部的
        ``ConfigResolver`` 有自己的系统级默认模型回退，多数「未配置」项目其实仍解析得到
        caps（见 ``test_reference_duration_tiers_uses_caps_for_custom_provider`` 的姊妹场景）。
        这里显式让 caps 解析异常，模拟两条来源都失效的真正拿不到档位表的情形。
        """
        from server.services import script_review as mod

        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = ScriptReviewService(pm)

        async def _raise(_project, _episode=None):
            raise RuntimeError("video_capabilities backend unreachable")

        monkeypatch.setattr(mod, "resolve_video_caps", _raise)
        assert await svc.get_reference_duration_tiers("demo", 1) is None

    @pytest.mark.integration
    async def test_reference_duration_tiers_none_for_non_reference_video_episode(self, tmp_path, monkeypatch):
        """非 reference_video 变体不做 caps 解析、直接 None——判据是方法自身的
        step1_kind，不能靠调用方按 get_state.supported_durations 是否非 None 短路
        （那个信号对自定义供应商项目恒为 None，会让方法永远没机会跑）。"""
        from server.services import script_review as mod

        pm = _make_project(tmp_path, "drama")  # generation_mode 缺省，非 reference_video
        svc = ScriptReviewService(pm)

        async def _fake_caps(_project, _episode=None):
            return {"provider_id": "custom-acme", "model": "acme-video", "supported_durations": [5, 10]}

        monkeypatch.setattr(mod, "resolve_video_caps", _fake_caps)
        assert await svc.get_reference_duration_tiers("demo", 1) is None

    @pytest.mark.integration
    async def test_reference_duration_tiers_uses_caps_for_custom_provider(self, tmp_path, monkeypatch):
        """自定义供应商（``custom-`` 前缀）不在 ``PROVIDER_REGISTRY``：档位表唯一来源是 caps
        （DB 驱动的能力查询）。caps 必须先于 ``resolve_raw_supported_durations`` 解析，否则
        raw 会因取不到而提前返回 None，永远不会用上 caps 本能给出的答案。
        """
        from server.services import script_review as mod

        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = ScriptReviewService(pm)

        async def _fake_caps(_project, _episode=None):
            return {"provider_id": "custom-acme", "model": "acme-video", "supported_durations": [5, 10]}

        monkeypatch.setattr(mod, "resolve_video_caps", _fake_caps)
        tiers = await svc.get_reference_duration_tiers("demo", 1)
        # 自定义供应商不在 registry，reference_unit_duration_tiers 查不到联动约束，两套档位
        # 都退回 caps 给出的原始集合。
        assert tiers == {"with_references": [5, 10], "without_references": [5, 10]}


class TestReferenceVideoStep1Migration:
    """存量 step1 草稿（per-shot 时长）在 gate 侧的一次性收编迁移。"""

    pytestmark = pytest.mark.integration

    @staticmethod
    def _legacy_step1() -> dict:
        """收编前形状：时长挂在各 shot 上，unit 无 duration_seconds。"""
        legacy = _rv_step1()
        del legacy["units"][0]["duration_seconds"]
        legacy["units"][0]["shots"][0]["duration"] = 5
        legacy["units"][0]["shots"][1]["duration"] = 3
        return legacy

    async def test_migration_takes_slot_so_step2_never_sees_a_non_member_duration(self, tmp_path, monkeypatch):
        """审阅门迁移落盘的秒数必是档位成员，不能只是「落在结构区间内」。

        迁移幂等一次性、谁先跑谁定终局，而正常产品流程是先开审阅门再生成：审阅门若按结构
        区间落一个非档位秒数，step2 的枚举 schema 随后硬拒，用户在 gate 里看不出问题也改不动。
        故审阅门与生成侧取同一份档位表。
        """
        _stub_video_caps(monkeypatch, [4, 8, 12])
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = ScriptReviewService(pm)
        legacy = self._legacy_step1()
        # 求和 10s：落在结构区间内，但不是档位成员——只做结构 clamp 时会原样固化。
        legacy["units"][0]["shots"][0]["duration"] = 6
        legacy["units"][0]["shots"][1]["duration"] = 4
        path = _write_rv_step1(pm, legacy)

        assert (await svc.get_state("demo", 1))["content"]["units"][0]["duration_seconds"] == 12
        assert json.loads(path.read_text(encoding="utf-8"))["units"][0]["duration_seconds"] == 12

    async def test_custom_provider_draft_migration_takes_slot_from_caps(self, tmp_path, monkeypatch):
        """自定义供应商（``custom-`` 前缀）不在 ``PROVIDER_REGISTRY``：档位表只有 caps 给得出。

        审阅门若不解析 caps，这类项目的读时收编只能退回结构区间 clamp——落盘的秒数不是档位
        成员，step2 的枚举 schema 随后硬拒。``supported_durations`` 也要一并带出真实档位，
        面板的可选项才与收编到的值同源。
        """
        _stub_video_caps(monkeypatch, [5, 10], provider_id="custom-acme", model="acme-video")
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = ScriptReviewService(pm)
        legacy = self._legacy_step1()
        # 求和 7s：结构区间内，但不是 [5, 10] 的成员。
        legacy["units"][0]["shots"][0]["duration"] = 4
        legacy["units"][0]["shots"][1]["duration"] = 3
        path = _write_rv_step1(pm, legacy)

        state = await svc.get_state("demo", 1)
        assert state["content"]["units"][0]["duration_seconds"] == 10
        assert state["supported_durations"] == [5, 10]
        assert json.loads(path.read_text(encoding="utf-8"))["units"][0]["duration_seconds"] == 10

    async def test_custom_provider_direct_confirm_takes_slot_from_caps(self, tmp_path, monkeypatch):
        """agent / API 绕过 get_state 直接 confirm 时同样按 caps 档位收编——两个入口口径不一致
        的话，先跑的那个会把非档位秒数固化到盘上（迁移幂等一次性）。"""
        _stub_video_caps(monkeypatch, [5, 10])
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = ScriptReviewService(pm)
        legacy = self._legacy_step1()
        legacy["units"][0]["shots"][0]["duration"] = 4
        legacy["units"][0]["shots"][1]["duration"] = 3
        _write_rv_step1(pm, legacy)

        state = await svc.confirm("demo", 1)
        assert state["status"] == "confirmed"
        assert state["content"]["units"][0]["duration_seconds"] == 10

    async def test_builtin_provider_falls_back_to_registry_when_caps_unavailable(self, tmp_path, monkeypatch):
        """内建供应商在 caps 解析失败时仍按 registry 声明的档位收编，不因缺 caps 退到结构 clamp。"""
        from server.services import script_review as mod

        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")

        def _set_backend(p: dict) -> None:
            p["video_backend"] = "gemini-aistudio/veo-3.1-generate-preview"

        pm.update_project("demo", _set_backend)

        async def _raise(_project, _episode=None):
            raise RuntimeError("video_capabilities backend unreachable")

        monkeypatch.setattr(mod, "resolve_video_caps", _raise)
        svc = ScriptReviewService(pm)
        legacy = self._legacy_step1()
        # 求和 7s：结构区间内，但不是 registry 档位 [4, 6, 8] 的成员。
        legacy["units"][0]["shots"][0]["duration"] = 4
        legacy["units"][0]["shots"][1]["duration"] = 3
        _write_rv_step1(pm, legacy)

        state = await svc.get_state("demo", 1)
        assert state["supported_durations"] == [4, 6, 8]
        assert state["content"]["units"][0]["duration_seconds"] == 8

    async def test_migration_falls_back_to_structural_clamp_without_video_backend(self, tmp_path):
        """项目未配置可解析的视频型号：档位表取不到，退回结构区间 clamp 而非阻断草稿加载。"""
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = ScriptReviewService(pm)
        legacy = self._legacy_step1()
        legacy["units"][0]["shots"][0]["duration"] = 6
        legacy["units"][0]["shots"][1]["duration"] = 4

        _write_rv_step1(pm, legacy)
        assert (await svc.get_state("demo", 1))["content"]["units"][0]["duration_seconds"] == 10

    async def test_legacy_draft_is_migrated_on_read_and_written_back(self, tmp_path):
        """读状态即收编：unit 拿到求和时长、shot 不再带时长，且一次落盘、二次读不再改写。"""
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = ScriptReviewService(pm)
        path = _write_rv_step1(pm, self._legacy_step1())

        unit = (await svc.get_state("demo", 1))["content"]["units"][0]
        assert unit["duration_seconds"] == 8
        assert all("duration" not in s for s in unit["shots"])

        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk["units"][0]["duration_seconds"] == 8
        assert all("duration" not in s for s in on_disk["units"][0]["shots"])

        # 幂等：二次读不再改写落盘内容。
        before = path.read_bytes()
        await svc.get_state("demo", 1)
        assert path.read_bytes() == before

    async def test_legacy_draft_can_be_confirmed_and_saved(self, tmp_path):
        """收编后存量草稿在 gate 里可确认、可保存——迁移前两者都撞结构校验（unit 缺必填时长）。"""
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = ScriptReviewService(pm)
        _write_rv_step1(pm, self._legacy_step1())

        confirmed = await svc.confirm("demo", 1)
        assert confirmed["status"] == "confirmed"

        edited = confirmed["content"]
        edited["units"][0]["shots"][0]["text"] = "@[阿离] 收伞。"
        assert (await svc.save_content("demo", 1, edited))["status"] == "pending_review"

    async def test_confirm_survives_migration_without_reopening_review(self, tmp_path):
        """迁移是机械收编、不是内容编辑：已确认的分集不因加载被回退到待审。"""
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = ScriptReviewService(pm)
        legacy = self._legacy_step1()
        path = _write_rv_step1(pm, legacy)

        # 先在收编前的内容上记录确认指纹（模拟升级前已通过审核的分集）。
        def _confirm_legacy(p: dict) -> None:
            script_review.apply_confirmation(p, 1, script_review.content_fingerprint(path), "2026-01-01T00:00:00Z")

        pm.update_project("demo", _confirm_legacy)
        assert (await svc.get_state("demo", 1))["status"] == "confirmed"

        state = await svc.get_state("demo", 1)
        assert state["status"] == "confirmed"
        assert state["confirmed_at"] == "2026-01-01T00:00:00Z"
        assert state["content"]["units"][0]["duration_seconds"] == 8

    async def test_confirm_reopens_review_when_migration_clamps_duration(self, tmp_path, monkeypatch):
        """迁移带 warnings（时长被 clamp 改写）不是纯格式收编：已确认分集须退回待审，
        不能像纯结构收编那样平移确认——clamp 后的秒数不是用户确认时看到的值。
        """
        _stub_video_caps(monkeypatch, [4, 8, 12])
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = ScriptReviewService(pm)
        legacy = self._legacy_step1()
        # 求和 90s 超出最大档位（12s），迁移会取档改写并记 warning。
        legacy["units"][0]["shots"][0]["duration"] = 60
        legacy["units"][0]["shots"][1]["duration"] = 30
        path = _write_rv_step1(pm, legacy)

        def _confirm_legacy(p: dict) -> None:
            script_review.apply_confirmation(p, 1, script_review.content_fingerprint(path), "2026-01-01T00:00:00Z")

        pm.update_project("demo", _confirm_legacy)

        state = await svc.get_state("demo", 1)
        assert state["status"] == "pending_review"
        assert state["content"]["units"][0]["duration_seconds"] == 12

    async def test_clamping_migration_reopens_review_for_grandfathered_episode(self, tmp_path, monkeypatch):
        """从未存过确认指纹、靠 grandfather 判据（step2 已存在）放行的存量集：迁移 clamp
        改写时长后须退回待审——迁移幂等落盘，重试不再产生 warnings，不落失配标记的话
        后续生成会静默采用用户从未过目的取值。
        """
        _stub_video_caps(monkeypatch, [4, 8, 12])
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = ScriptReviewService(pm)
        legacy = self._legacy_step1()
        legacy["units"][0]["shots"][0]["duration"] = 60
        legacy["units"][0]["shots"][1]["duration"] = 30
        _write_rv_step1(pm, legacy)
        _write_step2(pm)

        state = await svc.get_state("demo", 1)
        assert state["status"] == "pending_review"
        # 幂等重读不会把状态放回 grandfather 放行：失配标记已持久化。
        assert (await svc.get_state("demo", 1))["status"] == "pending_review"

    async def test_clamping_migration_marker_survives_interrupted_project_write(self, tmp_path, monkeypatch):
        """迁移是「project 失配标记 + 草稿」两次写：project 那次失败后重试仍须收敛到待审。

        草稿先落盘则重试判 changed=False、标记再也补不上，grandfather 存量集会带着被 clamp
        的时长停在 confirmed；标记先落盘时草稿仍是迁移前内容，重试重跑迁移即自愈。
        """
        _stub_video_caps(monkeypatch, [4, 8, 12])
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = ScriptReviewService(pm)
        legacy = self._legacy_step1()
        legacy["units"][0]["shots"][0]["duration"] = 60
        legacy["units"][0]["shots"][1]["duration"] = 30
        _write_rv_step1(pm, legacy)
        _write_step2(pm)

        original_update = pm.update_project
        failed: list[int] = []

        def _fail_first_project_write(*args, **kwargs):
            if not failed:
                failed.append(1)
                raise OSError("project write interrupted")
            return original_update(*args, **kwargs)

        monkeypatch.setattr(pm, "update_project", _fail_first_project_write)

        with pytest.raises(OSError):
            await svc.get_state("demo", 1)

        monkeypatch.setattr(pm, "update_project", original_update)
        assert (await svc.get_state("demo", 1))["status"] == "pending_review"

    async def test_confirm_direct_call_confirms_migrated_content(self, tmp_path, monkeypatch):
        """agent / API 可能绕过 get_state 直接调用 confirm：迁移在 confirm 内部触发并 clamp
        时（枚举外 clamp + warning 的宽容口径），confirm 按迁移后的落盘内容确认放行。
        """
        _stub_video_caps(monkeypatch, [4, 8, 12])
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = ScriptReviewService(pm)
        legacy = self._legacy_step1()
        legacy["units"][0]["shots"][0]["duration"] = 60
        legacy["units"][0]["shots"][1]["duration"] = 30
        _write_rv_step1(pm, legacy)

        state = await svc.confirm("demo", 1)
        assert state["status"] == "confirmed"
        assert state["content"]["units"][0]["duration_seconds"] == 12

    async def test_confirmation_carry_uses_written_content_not_post_write_reread(self, tmp_path, monkeypatch):
        """迁移写回后平移确认指纹须用刚写入的内容直接算，不能再读一次磁盘——写回与该次读取
        之间若有并发编辑落下，读到的会是并发内容的指纹，把确认记录错误地平移到一份未经审阅
        的内容上。
        """
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = ScriptReviewService(pm)
        legacy = self._legacy_step1()
        path = _write_rv_step1(pm, legacy)
        before = script_review.content_fingerprint(path)

        def _confirm_legacy(p: dict) -> None:
            script_review.apply_confirmation(p, 1, before, "2026-01-01T00:00:00Z")

        pm.update_project("demo", _confirm_legacy)

        concurrent_edit = self._legacy_step1()
        concurrent_edit["units"][0]["shots"][0]["text"] = "并发编辑：紧随迁移写回落盘。"

        written: list[dict] = []
        original_write = script_review.atomic_write_json

        def _write_then_concurrent_edit(target_path, data):
            written.append(json.loads(json.dumps(data)))
            original_write(target_path, data)
            atomic_write_json(path, concurrent_edit)

        monkeypatch.setattr(script_review, "atomic_write_json", _write_then_concurrent_edit)

        await svc.get_state("demo", 1)

        migrated_fingerprint = script_review.content_fingerprint_of_data(written[0])
        concurrent_fingerprint = script_review.content_fingerprint_of_data(concurrent_edit)
        stored = script_review.stored_review(pm.load_project("demo"), 1)

        assert stored["confirmed_at"] == "2026-01-01T00:00:00Z"
        assert stored["fingerprint"] == migrated_fingerprint
        assert stored["fingerprint"] != concurrent_fingerprint

    async def test_migration_carries_confirmation_that_lands_after_project_snapshot_loaded(self, tmp_path, monkeypatch):
        """get_state 在迁移前加载的 project 快照此后不再刷新：若确认发生在这份快照加载
        之后、迁移写回完成之前，携带确认的判断不能依赖这份陈旧快照——那样会把刚发生的
        确认误判成"未确认"而跳过搬移，永久丢失它（迁移幂等，往后重试也补不回来）。
        """
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = ScriptReviewService(pm)
        legacy = self._legacy_step1()
        path = _write_rv_step1(pm, legacy)
        before = script_review.content_fingerprint(path)

        original_migrate = script_review.migrate_unit_durations

        def _migrate_with_concurrent_confirm(units, **kwargs):
            # 模拟另一请求的 confirm() 在 get_state 加载 project 快照之后、迁移完成之前落下确认。
            def _confirm_legacy(p: dict) -> None:
                script_review.apply_confirmation(p, 1, before, "2026-01-01T00:00:00Z")

            pm.update_project("demo", _confirm_legacy)
            return original_migrate(units, **kwargs)

        monkeypatch.setattr(script_review, "migrate_unit_durations", _migrate_with_concurrent_confirm)

        state = await svc.get_state("demo", 1)
        assert state["status"] == "confirmed"

    async def test_migration_does_not_confirm_an_unconfirmed_episode(self, tmp_path):
        """指纹本就对不上（step1 确实改过）时不平移确认记录，照常按待审处理。"""
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = ScriptReviewService(pm)
        _write_rv_step1(pm, self._legacy_step1())

        def _stale_confirm(p: dict) -> None:
            script_review.apply_confirmation(p, 1, "0" * 64, "2026-01-01T00:00:00Z")

        pm.update_project("demo", _stale_confirm)
        assert (await svc.get_state("demo", 1))["status"] == "pending_review"


class TestReferenceVideoStep2Enforcement:
    async def test_generate_blocked_then_confirm_tool_unblocks(self, tmp_path):
        """agent 路径：rv 的 step1 未确认时 step2 阻塞，confirm_script_review 工具确认后放行。"""
        from server.agent_runtime.sdk_tools._context import ToolContext
        from server.agent_runtime.sdk_tools.text_generation import (
            confirm_script_review_tool,
            generate_episode_script_tool,
        )

        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        _write_rv_step1(pm, _rv_step1())
        project_path = pm.get_project_path("demo")
        assert script_review.gate_blocks_step2(project_path, pm.load_project("demo"), 1) is True

        ctx = ToolContext(project_name="demo", projects_root=tmp_path / "projects", pm=pm)
        blocked = await generate_episode_script_tool(ctx).handler({"episode": 1})
        assert blocked.get("is_error") is True
        assert "阻塞" in blocked["content"][0]["text"]

        result = await confirm_script_review_tool(ctx).handler({"episode": 1})
        assert result.get("is_error") is not True
        assert script_review.gate_blocks_step2(project_path, pm.load_project("demo"), 1) is False


# ---------------------------------------------------------------------------
# 适用范围：drama / narration / reference_video 纳入 gate；ad 不纳入
# ---------------------------------------------------------------------------


class TestApplicability:
    async def test_reference_video_applicable(self, tmp_path):
        """reference_video（跨 content_mode）纳入 gate，step1 变体判为 reference_video。"""
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        project = pm.load_project("demo")
        assert script_review.step1_kind(project) == "reference_video"
        assert script_review.is_applicable(project) is True
        # 未产 step1 → no_step1（区别于 ad 的 not_applicable）。
        assert (await ScriptReviewService(pm).get_state("demo", 1))["status"] == "no_step1"

    async def test_ad_not_applicable(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("addemo")
        pm.create_project_metadata("addemo", "Ad", "Anime", "ad")
        svc = ScriptReviewService(pm)
        assert script_review.step1_kind(svc.pm.load_project("addemo")) is None
        assert (await svc.get_state("addemo", 1))["status"] == "not_applicable"


# ---------------------------------------------------------------------------
# 编辑校验 + 确认前置错误
# ---------------------------------------------------------------------------


class TestErrors:
    async def test_save_invalid_content_rejected(self, tmp_path):
        pm = _make_project(tmp_path, "drama")
        svc = ScriptReviewService(pm)
        _write_step1(pm, "drama", _drama_step1())

        bad = _drama_step1()
        # dialogue 缺 speaker → kind ⇄ speaker 约束失败
        bad["scenes"][0]["utterances"][1] = {"kind": "dialogue", "speaker": None, "text": "无人"}
        with pytest.raises(ScriptReviewError) as exc:
            await svc.save_content("demo", 1, bad)
        assert exc.value.code == "invalid_content"

    async def test_confirm_without_step1_rejected(self, tmp_path):
        pm = _make_project(tmp_path, "drama")
        svc = ScriptReviewService(pm)
        with pytest.raises(ScriptReviewError) as exc:
            await svc.confirm("demo", 1)
        assert exc.value.code == "no_step1"

    async def test_save_not_applicable_rejected(self, tmp_path):
        pm = _make_project(tmp_path, "ad")  # ad 无结构化 step1，gate 不适用
        svc = ScriptReviewService(pm)
        with pytest.raises(ScriptReviewError) as exc:
            await svc.save_content("demo", 1, _drama_step1())
        assert exc.value.code == "not_applicable"

    async def test_get_state_unregistered_episode_rejected(self, tmp_path):
        """适用 gate 但分集未登记 project.json → episode_not_found（而非误报 no_step1）。"""
        pm = _make_project(tmp_path, "drama")  # 仅登记第 1 集
        svc = ScriptReviewService(pm)
        with pytest.raises(ScriptReviewError) as exc:
            await svc.get_state("demo", 99)
        assert exc.value.code == "episode_not_found"

    async def test_save_unregistered_episode_writes_no_orphan(self, tmp_path):
        """给未登记分集保存 → episode_not_found，且不落 drafts/episode_99 孤儿 step1 文件。"""
        pm = _make_project(tmp_path, "drama")
        svc = ScriptReviewService(pm)
        with pytest.raises(ScriptReviewError) as exc:
            await svc.save_content("demo", 99, _drama_step1())
        assert exc.value.code == "episode_not_found"
        orphan = pm.get_project_path("demo") / "drafts" / "episode_99" / "step1_normalized_script.json"
        assert not orphan.exists()

    async def test_save_with_stale_fingerprint_conflicts_reference_video(self, tmp_path):
        """rv 并发编辑：保存携带的基线指纹与盘上现值不一致（编辑期间另一方已保存）→ conflict、
        不落盘不覆盖；拿最新指纹（等价于刷新合并后）重试放行。"""
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = ScriptReviewService(pm)
        path = _write_rv_step1(pm, _rv_step1())
        stale = (await svc.get_state("demo", 1))["fingerprint"]

        # 另一编辑方先保存（内容变化 → 指纹漂移）
        other = _rv_step1()
        other["units"][0]["shots"][0]["text"] = "@[阿离] 转身离开。"
        await svc.save_content("demo", 1, other)
        before = path.read_text(encoding="utf-8")

        mine = _rv_step1()
        mine["units"][0]["shots"][1]["text"] = "@[裴与] 下马。"
        with pytest.raises(ScriptReviewError) as exc:
            await svc.save_content("demo", 1, mine, stale)
        assert exc.value.code == "conflict"
        assert path.read_text(encoding="utf-8") == before

        fresh = (await svc.get_state("demo", 1))["fingerprint"]
        state = await svc.save_content("demo", 1, mine, fresh)
        assert state["status"] == "pending_review"
        assert json.loads(path.read_text(encoding="utf-8"))["units"][0]["shots"][1]["text"] == "@[裴与] 下马。"

    async def test_save_with_stale_fingerprint_conflicts_drama(self, tmp_path):
        """drama/narration 的 web 保存同样受基线比对保护：同一个 conflict 错误码。"""
        pm = _make_project(tmp_path, "drama")
        svc = ScriptReviewService(pm)
        path = _write_step1(pm, "drama", _drama_step1())
        stale = (await svc.get_state("demo", 1))["fingerprint"]

        other = _drama_step1()
        other["title"] = "另一方改的标题"
        await svc.save_content("demo", 1, other)
        before = path.read_text(encoding="utf-8")

        with pytest.raises(ScriptReviewError) as exc:
            await svc.save_content("demo", 1, _drama_step1(), stale)
        assert exc.value.code == "conflict"
        assert path.read_text(encoding="utf-8") == before

    async def test_save_without_fingerprint_skips_baseline_check(self, tmp_path):
        """不带基线指纹的直连调用维持原语义：不比对、直接落盘。"""
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = ScriptReviewService(pm)
        _write_rv_step1(pm, _rv_step1())
        other = _rv_step1()
        other["units"][0]["shots"][0]["text"] = "@[阿离] 转身离开。"
        await svc.save_content("demo", 1, other)

        state = await svc.save_content("demo", 1, _rv_step1())
        assert state["status"] == "pending_review"

    async def test_rv_save_clears_stale_step2_quarantine_on_change(self, tmp_path):
        """web 保存改了 step1 内容 → 在场的 step2 隔离草稿作废（其保结构 diff 以旧 step1 为
        基底）；内容未变的保存不清。与 agent 侧写盘同一出口、同一语义。"""
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = ScriptReviewService(pm)
        project_path = pm.get_project_path("demo")
        _write_rv_step1(pm, _rv_step1())
        # 先经一次保存把归一化形状（含模型默认字段）落盘，"内容未变"的比较才有同一基准
        await svc.save_content("demo", 1, _rv_step1())
        step2_q = quarantine_path(project_path, 1, QUARANTINE_KIND_STEP2)

        write_quarantine(project_path, 1, QUARANTINE_KIND_STEP2, content={"units": [{"text": "旧基底"}]}, violations=[])
        await svc.save_content("demo", 1, _rv_step1())  # 内容未变（校验/重派生结果与盘上一致）
        assert step2_q.exists()

        edited = _rv_step1()
        edited["units"][0]["shots"][0]["text"] = "@[阿离] 转身离开。"
        await svc.save_content("demo", 1, edited)
        assert not step2_q.exists()


# ---------------------------------------------------------------------------
# 单一写盘出口（lib.script_review.write_step1_locked）
# ---------------------------------------------------------------------------


class TestStep1WriteStore:
    def _project_path(self, tmp_path: Path) -> Path:
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        return pm.get_project_path("demo")

    def test_conflict_on_stale_baseline_keeps_file(self, tmp_path: Path):
        project_path = self._project_path(tmp_path)
        with script_review.step1_write_lock(project_path, 1):
            script_review.write_step1_locked(project_path, 1, {"units": [{"v": 1}]})
        stale = "0" * 64

        with pytest.raises(script_review.Step1WriteConflict) as exc:
            with script_review.step1_write_lock(project_path, 1):
                script_review.write_step1_locked(project_path, 1, {"units": [{"v": 2}]}, expected_fingerprint=stale)

        assert exc.value.expected == stale
        assert exc.value.actual == script_review.content_fingerprint_of_data({"units": [{"v": 1}]})
        assert exc.value.current_content == {"units": [{"v": 1}]}
        path = script_review.official_reference_step1_path(project_path, 1)
        assert json.loads(path.read_text(encoding="utf-8")) == {"units": [{"v": 1}]}

    def test_matching_baseline_and_none_baseline_write(self, tmp_path: Path):
        """基线一致放行；``None`` 基线表示「取基线时文件不存在」，首写同样放行。"""
        project_path = self._project_path(tmp_path)
        with script_review.step1_write_lock(project_path, 1):
            script_review.write_step1_locked(project_path, 1, {"units": [{"v": 1}]}, expected_fingerprint=None)
        current = script_review.content_fingerprint(script_review.official_reference_step1_path(project_path, 1))
        with script_review.step1_write_lock(project_path, 1):
            script_review.write_step1_locked(project_path, 1, {"units": [{"v": 2}]}, expected_fingerprint=current)
        path = script_review.official_reference_step1_path(project_path, 1)
        assert json.loads(path.read_text(encoding="utf-8")) == {"units": [{"v": 2}]}

    def test_none_baseline_conflicts_when_file_appeared(self, tmp_path: Path):
        """基线 None（取基线时无正式文件）而写盘前文件已被另一方写出 → 冲突，不覆盖。"""
        project_path = self._project_path(tmp_path)
        with script_review.step1_write_lock(project_path, 1):
            script_review.write_step1_locked(project_path, 1, {"units": [{"v": 1}]})
        with pytest.raises(script_review.Step1WriteConflict):
            with script_review.step1_write_lock(project_path, 1):
                script_review.write_step1_locked(project_path, 1, {"units": [{"v": 2}]}, expected_fingerprint=None)

    def test_step2_quarantine_cleared_only_on_change(self, tmp_path: Path):
        project_path = self._project_path(tmp_path)
        step2_q = quarantine_path(project_path, 1, QUARANTINE_KIND_STEP2)
        with script_review.step1_write_lock(project_path, 1):
            script_review.write_step1_locked(project_path, 1, {"units": [{"v": 1}]})

        # 内容未变 → 不清
        write_quarantine(project_path, 1, QUARANTINE_KIND_STEP2, content={"units": [{"text": "基底"}]}, violations=[])
        with script_review.step1_write_lock(project_path, 1):
            assert script_review.write_step1_locked(project_path, 1, {"units": [{"v": 1}]}) is False
        assert step2_q.exists()

        # 内容变了 → 清
        with script_review.step1_write_lock(project_path, 1):
            assert script_review.write_step1_locked(project_path, 1, {"units": [{"v": 2}]}) is True
        assert not step2_q.exists()

        # 迁移回写按机械收编处理：内容变了也不清
        write_quarantine(project_path, 1, QUARANTINE_KIND_STEP2, content={"units": [{"text": "基底"}]}, violations=[])
        with script_review.step1_write_lock(project_path, 1):
            script_review.write_step1_locked(project_path, 1, {"units": [{"v": 3}]}, clear_step2_quarantine=False)
        assert step2_q.exists()

    async def test_confirm_corrupt_step1_rejected(self, tmp_path):
        """step1 文件损坏（非法 JSON，但 content_fingerprint 仍产哈希）→ 确认被结构校验拒绝。"""
        pm = _make_project(tmp_path, "drama")
        svc = ScriptReviewService(pm)
        path = _write_step1(pm, "drama", _drama_step1())
        path.write_bytes(b"\x00\x01 not json at all {")
        with pytest.raises(ScriptReviewError) as exc:
            await svc.confirm("demo", 1)
        assert exc.value.code == "invalid_content"


# ---------------------------------------------------------------------------
# step2 工具阻塞 enforcement：pending 时 generate_episode_script 拒绝
# ---------------------------------------------------------------------------


class TestStep2Enforcement:
    async def test_generate_blocked_when_pending(self, tmp_path):
        from server.agent_runtime.sdk_tools._context import ToolContext
        from server.agent_runtime.sdk_tools.text_generation import generate_episode_script_tool

        pm = _make_project(tmp_path, "drama")
        _write_step1(pm, "drama", _drama_step1())

        ctx = ToolContext(project_name="demo", projects_root=tmp_path / "projects", pm=pm)
        tool = generate_episode_script_tool(ctx)
        result = await tool.handler({"episode": 1})

        assert result.get("is_error") is True
        text = result["content"][0]["text"]
        assert "step1" in text and "阻塞" in text

    async def test_confirm_tool_unblocks_step2(self, tmp_path):
        """agent 路径：confirm_script_review 工具确认后，gate 放行（既有 step1→step2 不被破坏）。"""
        from server.agent_runtime.sdk_tools._context import ToolContext
        from server.agent_runtime.sdk_tools.text_generation import confirm_script_review_tool

        pm = _make_project(tmp_path, "drama")
        _write_step1(pm, "drama", _drama_step1())
        project_path = pm.get_project_path("demo")
        assert script_review.gate_blocks_step2(project_path, pm.load_project("demo"), 1) is True

        ctx = ToolContext(project_name="demo", projects_root=tmp_path / "projects", pm=pm)
        result = await confirm_script_review_tool(ctx).handler({"episode": 1})

        assert result.get("is_error") is not True
        assert script_review.gate_blocks_step2(project_path, pm.load_project("demo"), 1) is False


# ---------------------------------------------------------------------------
# 存量穷举：{step1 有无 × step2 有无 × step1_review 有无} 的 gate 派生态
# ---------------------------------------------------------------------------


class TestLegacyEnumeration:
    async def test_no_step1(self, tmp_path):
        pm = _make_project(tmp_path, "drama")
        assert (await ScriptReviewService(pm).get_state("demo", 1))["status"] == "no_step1"

    async def test_step1_no_step2_no_review_pending(self, tmp_path):
        """feature 后首次产 step1（未产 step2、无确认）→ 待审、阻塞。"""
        pm = _make_project(tmp_path, "drama")
        _write_step1(pm, "drama", _drama_step1())
        assert (await ScriptReviewService(pm).get_state("demo", 1))["status"] == "pending_review"

    async def test_step1_step2_no_review_grandfathered_confirmed(self, tmp_path):
        """存量项目（已产 step1 + step2、无 step1_review 字段）→ grandfather 放行，不阻塞重跑。"""
        pm = _make_project(tmp_path, "drama")
        _write_step1(pm, "drama", _drama_step1())
        _write_step2(pm)
        project_path = pm.get_project_path("demo")
        assert (await ScriptReviewService(pm).get_state("demo", 1))["status"] == "confirmed"
        assert script_review.gate_blocks_step2(project_path, pm.load_project("demo"), 1) is False

    async def test_step1_step2_review_matching_confirmed(self, tmp_path):
        pm = _make_project(tmp_path, "drama")
        _write_step1(pm, "drama", _drama_step1())
        _write_step2(pm)
        await ScriptReviewService(pm).confirm("demo", 1)
        assert (await ScriptReviewService(pm).get_state("demo", 1))["status"] == "confirmed"

    async def test_step1_step2_review_mismatch_pending(self, tmp_path):
        """已确认后 step1 又被改（即便 step2 在）→ 重新待审，指纹优先于 grandfather。"""
        pm = _make_project(tmp_path, "drama")
        _write_step1(pm, "drama", _drama_step1())
        _write_step2(pm)
        await ScriptReviewService(pm).confirm("demo", 1)
        edited = _drama_step1()
        edited["scenes"][0]["source_text"] = "改写后的原文锚"
        await ScriptReviewService(pm).save_content("demo", 1, edited)
        assert (await ScriptReviewService(pm).get_state("demo", 1))["status"] == "pending_review"


# ---------------------------------------------------------------------------
# 手动预拆分自愈：episodes[] 账本为空但 source/episode_N.txt 派生文件已存在时，
# _require_episode 自愈补建条目而非直接判死锁。
# ---------------------------------------------------------------------------


class TestManualSplitSelfHeal:
    async def test_get_state_self_heals_orphan_without_source_range(self, tmp_path):
        """孤儿派生文件 → 自愈登记条目（不写 source_range），get_state 不再 episode_not_found。"""
        pm = _make_manual_split_project(tmp_path, "narration")
        _write_source_text(pm, "episode_1.txt", "裴与出征后的第二年。")
        _write_step1(pm, "narration", _narration_step1())

        state = await ScriptReviewService(pm).get_state("demo", 1)
        assert state["status"] == "pending_review"

        ep = script_review.find_episode(pm.load_project("demo"), 1)
        assert ep is not None
        assert ep["ledger_status"] == "consumed"  # 已有 step1 中间文件
        assert "source_range" not in ep

    async def test_confirm_self_heals_and_unblocks_step2(self, tmp_path):
        """confirm（web 与 agent 工具共用同一 service）在空账本下不再 episode_not_found，且放行 step2。"""
        pm = _make_manual_split_project(tmp_path, "drama")
        _write_source_text(pm, "episode_1.txt", "任意派生内容")
        _write_step1(pm, "drama", _drama_step1())

        confirmed = await ScriptReviewService(pm).confirm("demo", 1)
        assert confirmed["status"] == "confirmed"

        project_path = pm.get_project_path("demo")
        assert script_review.gate_blocks_step2(project_path, pm.load_project("demo"), 1) is False

    async def test_self_heal_never_anchors_even_when_source_text_matches(self, tmp_path):
        """派生文件内容即使能在原文中精确匹配，自愈也只登记不锚定：位置记录只由规划工具写入。"""
        pm = _make_manual_split_project(tmp_path, "narration")
        original = "裴与出征后的第二年，送回一个襁褓中的婴儿。后续内容在此。"
        _write_source_text(pm, "novel.txt", original)
        _write_source_text(pm, "episode_1.txt", "裴与出征后的第二年，送回一个襁褓中的婴儿。")

        await ScriptReviewService(pm).get_state("demo", 1)

        ep = script_review.find_episode(pm.load_project("demo"), 1)
        assert ep is not None
        assert "source_range" not in ep

    async def test_self_heal_registers_all_orphans_not_just_requested(self, tmp_path):
        """自愈一次登记账本中所有孤儿集号的派生文件，不只是当前请求的那一集。"""
        pm = _make_manual_split_project(tmp_path, "narration")
        _write_source_text(pm, "episode_1.txt", "第一集内容")
        _write_source_text(pm, "episode_2.txt", "第二集内容")

        await ScriptReviewService(pm).get_state("demo", 1)

        project = pm.load_project("demo")
        assert script_review.find_episode(project, 1) is not None
        assert script_review.find_episode(project, 2) is not None

    async def test_self_heal_preserves_existing_ledger_status_entries(self, tmp_path):
        """已带 ledger_status 的条目（规划工具写入）不因其他集号的自愈触发被改写。"""
        pm = _make_manual_split_project(tmp_path, "narration")
        pm.add_episode("demo", 1, "第一集", "scripts/episode_1.json")

        def _mark_planned(p: dict) -> None:
            ep = next(e for e in p["episodes"] if e["episode"] == 1)
            ep["ledger_status"] = "planned"
            ep["source_range"] = {"source_file": "source/novel.txt", "start": 0, "end": 5}

        pm.update_project("demo", _mark_planned)
        _write_source_text(pm, "episode_2.txt", "第二集派生内容")

        # 触发对孤儿集（episode 2）的自愈请求，不涉及 episode 1。
        await ScriptReviewService(pm).get_state("demo", 2)

        project = pm.load_project("demo")
        ep1 = script_review.find_episode(project, 1)
        assert ep1 is not None
        assert ep1["ledger_status"] == "planned"
        assert ep1["source_range"] == {"source_file": "source/novel.txt", "start": 0, "end": 5}
        assert script_review.find_episode(project, 2) is not None

    async def test_self_heal_does_not_apply_when_derivative_file_missing(self, tmp_path):
        """账本为空且该集派生文件也不存在（真正缺失的集号）→ 仍抛 episode_not_found，不自愈。"""
        pm = _make_manual_split_project(tmp_path, "narration")
        with pytest.raises(ScriptReviewError) as exc:
            await ScriptReviewService(pm).get_state("demo", 1)
        assert exc.value.code == "episode_not_found"
        assert pm.load_project("demo")["episodes"] == []

    async def test_self_heal_idempotent_no_duplicate_entries(self, tmp_path):
        """重复触发自愈（同集反复读状态）不产生重复集号条目，也不重复改写已登记条目。"""
        pm = _make_manual_split_project(tmp_path, "narration")
        _write_source_text(pm, "episode_1.txt", "第一集派生内容")

        svc = ScriptReviewService(pm)
        await svc.get_state("demo", 1)
        first = script_review.find_episode(pm.load_project("demo"), 1)

        await svc.get_state("demo", 1)
        await svc.get_state("demo", 1)

        project = pm.load_project("demo")
        matches = [e for e in project["episodes"] if e.get("episode") == 1]
        assert len(matches) == 1
        assert matches[0] == first
