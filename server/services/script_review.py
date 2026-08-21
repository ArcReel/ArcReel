"""step1→step2 web 内容确认的服务层：确认状态读取、结构化中间态编辑、确认动作。

纯 gate 逻辑（适用性 / 指纹 / 状态派生）在 ``lib.script_review``；本层叠加 ProjectManager
持久化（确认指纹落 project.json ``episodes[i].step1_review``）与结构化内容的 Pydantic 校验、落盘。

确认触发 step2 的语义是「放行」而非「服务端 launcher」：step2（剧本视觉生成）由 Agent 的
``generate_episode_script`` 工具执行，本服务只负责把内容确认状态翻到 confirmed；该工具读时经
``lib.script_review.gate_blocks_step2`` 校验，pending 时拒绝、confirmed 后放行。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from lib import script_review
from lib.config.resolver import resolve_raw_supported_durations
from lib.draft_quarantine import QUARANTINE_KIND_STEP1, read_quarantine, violation_entries
from lib.episode_ledger import discover_episode_files, register_orphan_episode_entries
from lib.json_io import load_json_or_none
from lib.project_manager import ProjectManager
from lib.script_models import DramaNormalizedScript, NarrationStep1Draft, ReferenceStep1Draft
from lib.speech_composition import SpeechAdmission, admit_script_unit
from server.agent_runtime.sdk_tools._context import reference_unit_duration_tiers, resolve_video_caps

logger = logging.getLogger(__name__)

#: 结构化 step1 中间态的校验模型（按 step1 变体 ``script_review.step1_kind``）。编辑保存按此做结构校验：
#: drama 为内容层 DramaNormalizedScript（utterances / source_text / scene_description），
#: narration 为 NarrationStep1Draft（结构化 novel_text 分镜），reference_video 为 ReferenceStep1Draft
#: （units → 正文 + 编排时长）。
_STEP1_CONTENT_MODEL: dict[str, type[BaseModel]] = {
    "drama": DramaNormalizedScript,
    "narration": NarrationStep1Draft,
    "reference_video": ReferenceStep1Draft,
}


class ScriptReviewError(Exception):
    """gate 操作的领域错误。``code`` 供 router 映射 HTTP 状态与 i18n key；``message`` 为技术细节。"""

    def __init__(self, code: str, message: str = "", *, admission: SpeechAdmission | None = None):
        super().__init__(message or code)
        self.code = code
        self.message = message
        self.admission = admission


def _require_changed_speech_admitted(kind: str, previous: object, candidate: object) -> None:
    """Reject only new or edited speech content, leaving legacy mixed metadata editable."""

    if kind == "drama":
        root, skeleton, id_field, speech_fields = "scenes", "scenes", "scene_id", ("utterances",)
    elif kind == "reference_video":
        root, skeleton, id_field, speech_fields = "units", "video_units", "unit_id", ("text",)
    else:
        return
    if not isinstance(candidate, dict) or not isinstance(candidate.get(root), list):
        return
    previous_units = previous.get(root) if isinstance(previous, dict) else None
    previous_by_id = {
        unit.get(id_field): unit
        for unit in previous_units or []
        if isinstance(unit, dict) and isinstance(unit.get(id_field), str)
    }
    for unit in candidate[root]:
        if not isinstance(unit, dict) or not isinstance(unit.get(id_field), str):
            continue
        old = previous_by_id.get(unit[id_field])
        speech_changed = old is None or any(old.get(field) != unit.get(field) for field in speech_fields)
        if not speech_changed:
            if isinstance(old, dict) and old.get("needs_replan") is True:
                unit["needs_replan"] = True
            continue
        admission = admit_script_unit(skeleton, unit, ignore_marker=True)
        if not admission.allowed:
            raise ScriptReviewError("speech_admission", admission=admission)
        unit.pop("needs_replan", None)


class ScriptReviewService:
    """封装 step1→step2 内容确认的读写。router 与测试经此操作 gate，不直接碰文件 / project.json。"""

    def __init__(self, pm: ProjectManager):
        self.pm = pm

    def _resolve_step1_model(self, project: dict[str, Any], episode: int) -> tuple[str, type[BaseModel]]:
        """该集 step1 变体 + 结构校验模型；不适用 gate（无结构化 step1）时抛 not_applicable。

        变体判定单一真相源在 ``script_review.step1_kind``（reference_video 按项目生成模式优先，
        跨 content_mode）；本层据此选 Pydantic 模型。返回变体名供调用方按变体分流。
        """
        kind = script_review.step1_kind(project)
        if kind is None:
            raise ScriptReviewError("not_applicable")
        return kind, _STEP1_CONTENT_MODEL[kind]

    def _require_episode(self, project_name: str, project: dict[str, Any], episode: int) -> dict[str, Any]:
        """gate 适用时校验该集已在 project.json ``episodes[]`` 登记，返回（必要时已自愈的）project。

        与 ``confirm`` 的写入前置一致：避免 ``get_state`` 把未登记分集误报成 no_step1、
        ``save_content`` 给未登记分集写出永远无法与 project.json 关联的孤儿 step1 文件。

        条目缺失时不立即拒绝：若该集的派生文件 ``source/episode_N.txt`` 实际存在（用户绕过
        分集规划器、手动预拆分上传的存量场景），先用 ``register_orphan_episode_entries`` 自愈
        补建条目再重新校验，而非直接判死锁——手动预拆分与账本为空同时出现时，唯一的登记来源
        就是这次自愈，不做即无法登记、也无法确认。派生文件也不存在时（真正缺失的集号）不自愈，
        直接抛出。补建出的条目没有位置记录（source_range），消费链路照常，重新规划须先走一次
        全量重置。
        """
        if script_review.find_episode(project, episode) is not None:
            return project
        project_path = self.pm.get_project_path(project_name)
        if episode not in discover_episode_files(project_path):
            raise ScriptReviewError("episode_not_found")
        project = self._register_orphan_episodes(project_name)
        if script_review.find_episode(project, episode) is None:
            raise ScriptReviewError("episode_not_found")
        return project

    def _register_orphan_episodes(self, project_name: str) -> dict[str, Any]:
        """在项目锁内运行一次 ``register_orphan_episode_entries`` 并落盘，返回自愈后的 project。

        落盘走 ``ProjectManager.update_project`` 的锁内 read-modify-write，不绕锁直写
        project.json。``register_orphan_episode_entries`` 是不修改入参的纯函数，返回新 dict；
        这里在回调内把结果拷回被就地修改的 ``p``，桥接纯函数输出与 update_project 的
        原地修改约定。已登记的集号在纯函数内部即被跳过，重复触发不会重写既有条目或产生
        重复集号。
        """
        project_path = self.pm.get_project_path(project_name)

        def _mutate(p: dict[str, Any]) -> None:
            healed = register_orphan_episode_entries(project_path, p)
            p.clear()
            p.update(healed)

        return self.pm.update_project(project_name, _mutate)

    async def _resolve_caps_best_effort(self, project_name: str, project: dict[str, Any]) -> dict:
        """视频能力查询，解析失败时退回空 caps 而非冒穿。

        缺 caps 只是让下游退到 registry / 不收窄这两个既有降级口径，而解析异常直接冒穿会让用户
        连草稿都加载不了。档位表与档位收窄两条链共用本方法，降级语义因此不会在两处各自漂移。
        """
        try:
            return await resolve_video_caps(project)
        except Exception as exc:  # noqa: BLE001 - best-effort：解析失败退回空 caps，不阻断 gate
            logger.warning(
                "video_capabilities 解析异常，内容确认退回不带 caps 的解析 project=%s：%s", project_name, exc
            )
            return {}

    async def _resolve_supported_durations(self, project_name: str, project: dict[str, Any]) -> list[int] | None:
        """收窄前的时长档位全集；非 reference_video 变体或解析不到型号时 None。

        caps 先解析、再交 ``resolve_raw_supported_durations``：registry 那一级只收录内建供应商，
        自定义供应商（``custom-`` 前缀）的档位表只有 caps（DB 驱动的能力查询）给得出。不带 caps
        调用会让这类项目恒为 None——读时迁移退回结构区间 clamp、gate 面板也拿不到可选档位，
        存量草稿的收编对其整体失效。

        caps 解析失败（DB / 迁移故障）时退回不带 caps 的 registry 解析，不阻断 gate（降级见
        ``_resolve_caps_best_effort``）：缺档位表只是回到结构区间 clamp。

        调用方须在取 ``self.pm.file_lock`` **之前** await 本方法：那把锁是阻塞式文件锁，跨
        await 持有会连带把事件循环上的其它协程挡在锁外。
        """
        if script_review.step1_kind(project) != "reference_video":
            return None
        caps = await self._resolve_caps_best_effort(project_name, project)
        return resolve_raw_supported_durations(project, caps)

    def _read_step1_migrated(
        self,
        project_name: str,
        project: dict[str, Any],
        episode: int,
        path: Path,
        project_path: Path,
        supported_durations: list[int] | None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """读结构化 step1，并对参考生视频草稿做一次性时长收编迁移；返回 ``(内容, 最新 project)``。

        草稿是 gate 的三个入口（读状态 / 保存 / 确认）唯一的内容来源，而收编后的
        ``ReferenceStep1Unit`` 要求 unit 级 ``duration_seconds`` 且不接受多余字段：存量草稿若不在
        读时迁移，保存与确认都会撞结构校验，用户在 gate 里既改不了也确认不了。
        剧集脚本的迁移在 ``ProjectManager.load_script``，草稿的在这里，两处共用同一迁移器。

        ``supported_durations`` 由调用方经 ``_resolve_supported_durations`` 解析后传入（本函数
        跑在锁内，不能自行 await），与 step2 生成侧同源：迁移幂等一次性、谁先跑谁定终局，两侧
        口径不一致时先跑的内容确认会把落在结构区间内但非档位成员的秒数固化到盘上，step2 的枚举
        schema 随后硬拒，用户在 gate 里既看不出问题也改不动。为 None（项目未配置视频型号）时
        退回结构区间 clamp——缺配置不该阻断草稿加载，档位偏移仍由执行时取档兜底。

        调用方须已持有 ``self.pm.file_lock(path)``——本函数只做读改写，不自行加锁，避免与
        调用方（如 ``confirm``）已持有的同一把锁发生同线程二次获取的死锁。
        """
        content = _read_json(path)
        if content is None or script_review.step1_kind(project) != "reference_video":
            return content, project

        updated, _warnings = script_review.migrate_step1_draft_in_place(
            project_path,
            content,
            episode=episode,
            update_project=lambda mutate: self.pm.update_project(project_name, mutate),
            supported_durations=supported_durations,
        )
        return content, updated or project

    async def get_state(self, project_name: str, episode: int) -> dict[str, Any]:
        """返回该集内容确认状态 + 结构化中间态内容（供 web 渲染）。

        ``content`` 为解析后的结构化 step1（drama: {title, scenes[]}；narration: {segments[]}；
        reference_video: {units[]}）；不适用 gate 或 step1 缺失 / 损坏时为 None。

        ``supported_durations`` 只在 reference_video 变体下非 None：unit 时长是档位枚举而非
        自由秒数，web 侧要按它渲染选择项。取值与读时迁移同源（同一次
        ``_resolve_supported_durations``），两处不同源的话，gate 里能选的档位会与迁移收编到的
        档位不一致。项目未配置视频型号而解析不到时为 None，呈现层退回只读秒数。

        档位解析先于落盘读写完成，其余同步 I/O 整段卸到线程：``file_lock`` 是阻塞式文件锁，
        不能跨 await 持有，而档位解析本身要 await 视频能力查询。
        """
        project = await asyncio.to_thread(self.pm.load_project, project_name)
        supported_durations = await self._resolve_supported_durations(project_name, project)
        return await asyncio.to_thread(self._get_state_sync, project_name, project, episode, supported_durations)

    def _get_state_sync(
        self,
        project_name: str,
        project: dict[str, Any],
        episode: int,
        supported_durations: list[int] | None,
    ) -> dict[str, Any]:
        """``get_state`` 的同步主体：分集自愈、读时迁移、状态与指纹派生，全部在一个线程内完成。

        ``project`` 与 ``supported_durations`` 由 ``get_state`` 预先取好传入——迁移不改动视频
        型号配置，档位表在迁移前后描述的是同一件事，无需在锁内重算。
        """
        project_path = self.pm.get_project_path(project_name)
        path = script_review.step1_path(project_path, project, episode)
        if path is not None:
            # 适用 gate（drama / narration 非 reference_video）才要求分集已登记；
            # not_applicable（ad / reference_video）与分集存在性无关，保持原样返回。
            project = self._require_episode(project_name, project, episode)
        content: dict[str, Any] | None = None
        fingerprint: str | None = None
        if path is not None:
            # 迁移可能回写 step1 与确认记录；指纹与状态在同一把锁内、迁移之后取，三者才据
            # 同一份落盘内容派生——锁外取则并发 save_content 会让指纹描述另一份内容。
            with self.pm.file_lock(path):
                content, project = self._read_step1_migrated(
                    project_name, project, episode, path, project_path, supported_durations
                )
                fingerprint = script_review.content_fingerprint(path)
                status = script_review.review_status(project_path, project, episode)
        else:
            status = script_review.review_status(project_path, project, episode)
        return {
            "episode": episode,
            "content_mode": project.get("content_mode"),
            "status": status,
            "fingerprint": fingerprint,
            "confirmed_at": script_review.stored_review(project, episode).get("confirmed_at"),
            "content": content,
            "supported_durations": supported_durations,
        }

    async def get_quarantine_info(self, project_name: str, episode: int) -> dict[str, Any] | None:
        """reference_video 变体的草稿信息（供 web 内容确认呈现违约行内锚定），不适用 /
        无草稿时 None。

        读时按产出时那套校验器全量重算（``revalidate_reference_step1_draft``，晋升工具同一份
        代码），不信任草稿里 ``violations`` 的上一轮快照——草稿在场期间源文或模型配置可能已变，
        报告要对现值负责。本方法与 ``get_state`` 各自读盘、互不依赖，由 router 在同一次请求内
        合并两者的返回。

        ``content`` 校验通过时返回收编后的扁平产出（时长已按当前档位收编），未通过时返回草稿
        原样内容 + 违约列表，供呈现层原样展示 Agent 手改的那份文本。meta 被改坏以致无从重算
        时，把「无法重算」本身作为一条违约返回，而不是退回草稿里那份上一轮快照——报告一律
        对现值负责，读时重算失败也是现值的一部分。

        项目 / 草稿的同步文件读取经 ``asyncio.to_thread`` 卸到线程——本方法整体是
        ``async``（内部要 ``await`` 重算里的能力解析），若前半截同步 I/O 直接跑在事件循环上，
        源文越大越占用循环时间，拖慢并发的其它请求；``get_state`` 把同步主体整段卸到线程，
        这里保持同一纪律。
        """
        project = await asyncio.to_thread(self.pm.load_project, project_name)
        if script_review.step1_kind(project) != "reference_video":
            return None
        project_path = self.pm.get_project_path(project_name)
        quarantine_path = script_review.step1_quarantine_path(project_path, project, episode)
        if quarantine_path is None or not quarantine_path.exists():
            return None
        draft = await asyncio.to_thread(read_quarantine, project_path, episode, QUARANTINE_KIND_STEP1)
        if draft is None:
            if not quarantine_path.exists():
                # 存在性检查与读取之间的窗口内，Agent 的晋升/重拆分工具把文件清掉了（正式内容
                # 已写入、待处置草稿已清除）：不是信封损坏，是这次读跨越了「清除」那一刻，按「无
                # 草稿」处理，不误报损坏。
                return None
            # 文件存在但信封形状坏（非法 JSON / 顶层非对象 / content 非对象）：`read_quarantine`
            # 按「无草稿」返回 None 是它自己的读取口径（供 confirm 的存在性判断照常阻塞），
            # 但呈现层不能照单全收——那会让面板看起来「干净」，实际草稿仍在阻塞确认。
            return {
                "content": None,
                "violations": [
                    {
                        "code": "quarantine_unreadable",
                        "label": "",
                        "message": "草稿文件已损坏或格式不符，无法解析",
                        "line": None,
                    }
                ],
            }
        # 延迟导入避免模块级循环依赖：text_generation.py 内部已对本模块做同样的函数级延迟导入
        # （构造 ScriptReviewService 供 quarantine 相关工具复用），两处顶层互相导入会成环。
        from server.agent_runtime.sdk_tools.text_generation import revalidate_reference_step1_draft

        try:
            revalidation = await revalidate_reference_step1_draft(project_path, project, episode, draft)
        except ValueError as exc:
            # meta.source 缺失等草稿被改坏的情形：把重算失败本身报成一条无 unit 归属的违约，
            # 呈现层落聚合区。gate 不崩，用户也不会看到一份与现值脱钩的旧报告。异常文本含
            # draft.path，只记日志、不回传给调用方——面向用户的 message 不能带内部路径。
            logger.warning("草稿重算失败 project=%s episode=%s：%s", project_name, episode, exc)
            return {
                "content": draft.content,
                "violations": [
                    {
                        "code": "quarantine_unreadable",
                        "label": "",
                        "message": "草稿的产出上下文缺失或损坏，无法重新校验",
                        "line": None,
                    }
                ],
            }
        content = draft.content if revalidation.schema_failed else {"units": revalidation.flat_units}
        return {"content": content, "violations": violation_entries(revalidation.violations)}

    async def get_reference_duration_tiers(self, project_name: str, episode: int) -> dict[str, list[int]] | None:
        """reference_video 变体逐 unit 生效时长档位：``{with_references, without_references}``。

        与 ``get_state.supported_durations``（收窄前的结构区间全集，供 ``_read_step1_migrated``
        的存量草稿 clamp）取自同一份 ``resolve_raw_supported_durations`` 结果，但用途不同源：
        那个决定「这个秒数结构上合不合法」，这个决定「现在选它，``_assert_reference_step1_ready``
        会不会接受」——分辨率 / 参考图联动约束只影响后者。clamp 保持用全集，避免收窄后把结构
        合法但当前档位表之外的存量秒数误判非法；这里单独给下拉提供收窄后的可选项，同一把尺
        来自 ``reference_unit_duration_tiers``（拆分工具校验用的同一份）。无法解析到型号时返回
        None，呈现层退回未收窄的 ``supported_durations``。

        非 reference_video 变体直接返回 None，不做 caps 解析——调用方（router）按此方法自身
        的 step1_kind 判断决定是否调用，不拿 ``get_state.supported_durations`` 是否非 None
        当短路条件：那是另一个方法的返回值，两者各自判 step1_kind，不互相依赖。

        caps 与收窄用的模型身份在本方法内单独解析：``reference_unit_duration_tiers`` 要按
        caps 里的 provider/model 求联动约束，光有档位表不够；解析失败的降级与档位表那条链
        共用 ``_resolve_caps_best_effort``，缺 caps 时收窄回退为不收窄。

        项目读取同 ``get_quarantine_info`` 卸到线程——本方法同样是 ``async`` 且由请求协程
        直接 ``await``，同步的 ``project.json`` 读取直接跑在事件循环上会阻塞并发的其它请求。
        """
        project = await asyncio.to_thread(self.pm.load_project, project_name)
        if script_review.step1_kind(project) != "reference_video":
            return None
        caps = await self._resolve_caps_best_effort(project_name, project)
        raw = resolve_raw_supported_durations(project, caps)
        if raw is None:
            return None
        with_refs, without_refs = await reference_unit_duration_tiers(project, caps, raw)
        return {"with_references": sorted(set(with_refs)), "without_references": sorted(set(without_refs))}

    async def save_content(
        self, project_name: str, episode: int, content: object, base_fingerprint: str | None = None
    ) -> dict[str, Any]:
        """校验并落盘编辑后的结构化中间态（手动或 Agent 编辑后回写），返回最新状态（重新等待确认）。

        内容变更使指纹漂移，``get_state`` 据此自动回到 pending_review——保存即重新需要确认。

        ``base_fingerprint`` 是编辑方读取内容（``get_state``）时拿到的指纹：给定时在锁内与盘上
        现值比对，不一致（编辑期间另一写入方已改过 step1）抛 ``conflict``、不落盘——后写方拿
        409 冲突提示去刷新合并，先写方的内容不被静默覆盖。``None`` 不比对（无基线的直连调用）。

        落盘本身全同步（不做时长收编，无需档位表），整段卸到线程；随后的 ``get_state`` 自行
        解析档位表，保存的响应与 GET 首次加载因此同源。
        """
        await asyncio.to_thread(self._save_content_sync, project_name, episode, content, base_fingerprint)
        return await self.get_state(project_name, episode)

    def _save_content_sync(
        self, project_name: str, episode: int, content: object, base_fingerprint: str | None
    ) -> None:
        """``save_content`` 的同步主体：结构校验、基线比对、落盘。"""
        project = self.pm.load_project(project_name)
        project_path = self.pm.get_project_path(project_name)
        path = script_review.step1_path(project_path, project, episode)
        if path is None:
            raise ScriptReviewError("not_applicable")
        project = self._require_episode(project_name, project, episode)
        kind, model = self._resolve_step1_model(project, episode)
        try:
            validated = model.model_validate(content).model_dump()
        except ValidationError as exc:
            _require_changed_speech_admitted(kind, _read_json(path), content)
            raise ScriptReviewError("invalid_content", str(exc)) from exc
        if kind == "drama":
            for scene in validated["scenes"]:
                if scene.get("needs_replan") is not True:
                    scene.pop("needs_replan", None)
        # 入参的 None 表示「调用方无基线、不比对」；比对语义里的 None 另有含义（取基线时文件不存在），
        # 两者在此一次性转换，三个变体共用同一个 expected。
        expected = base_fingerprint if base_fingerprint is not None else script_review.UNCHECKED_FINGERPRINT
        try:
            if kind == "reference_video":
                # 写盘经单一出口：锁、基线比对、step2 草稿清理都在 write_step1_locked 一处。
                with script_review.step1_write_lock(project_path, episode):
                    _require_changed_speech_admitted(kind, _read_json(path), validated)
                    script_review.write_step1_locked(project_path, episode, validated, expected_fingerprint=expected)
            else:
                # 与 _read_step1_migrated 共享同一把 per-path 锁：保存与迁移的读改写相互互斥。
                # 基线比对同 rv 走 assert_base_fingerprint，两者的冲突判据不分叉；比对先于
                # 台词准入判定，让基线过期的保存拿到 conflict 而不是一条它改不动的准入意见。
                # 比对既已在此做过，落盘出口不再重复比对（默认 UNCHECKED）。
                with script_review.formal_step1_lock(project_path, episode, path):
                    script_review.assert_base_fingerprint(path, expected)
                    _require_changed_speech_admitted(kind, _read_json(path), validated)
                    script_review.write_formal_step1_locked(project_path, episode, path, validated)
        except script_review.Step1WriteConflict as exc:
            raise ScriptReviewError("conflict", str(exc)) from exc

    async def confirm(self, project_name: str, episode: int) -> dict[str, Any]:
        """把该集内容确认状态翻到 confirmed（记录当前 step1 内容指纹），放行 step2。

        无 step1 / 不适用 / 集条目缺失 / step1 内容结构非法 / 有草稿待处置时
        抛 ScriptReviewError，由 router 映射 4xx。

        档位表先于加锁解析（同 ``get_state``）：确认路径同样要对存量草稿做一次读时收编，收编
        用的档位表须与 gate 面板呈现的那份同源，否则确认会把面板上选不到的秒数固化到盘上。
        """
        project = await asyncio.to_thread(self.pm.load_project, project_name)
        supported_durations = await self._resolve_supported_durations(project_name, project)
        await asyncio.to_thread(self._confirm_sync, project_name, project, episode, supported_durations)
        return await self.get_state(project_name, episode)

    def _confirm_sync(
        self,
        project_name: str,
        project: dict[str, Any],
        episode: int,
        supported_durations: list[int] | None,
    ) -> None:
        """``confirm`` 的同步主体：待处置草稿校验、读时收编、结构校验、指纹落盘。"""
        project_path = self.pm.get_project_path(project_name)
        path = script_review.step1_path(project_path, project, episode)
        if path is None:
            raise ScriptReviewError("not_applicable")
        project = self._require_episode(project_name, project, episode)
        # 草稿在场时拒绝确认：正式 step1 此刻仍是上一版（或不存在），确认它等于替用户
        # 认可一份他没看过的内容，而刚产出的那份违约正文还在草稿里等 Agent 处置。校验
        # 口径与生成侧同一把尺——晋升工具用的正是产出时那套校验器，这里只判待处置草稿是否在场。
        quarantine = script_review.step1_quarantine_path(project_path, project, episode)
        if quarantine is not None and quarantine.exists():
            raise ScriptReviewError("quarantined", f"step1 草稿待处置: {quarantine}")
        # 存量草稿先做时长收编再校验：Agent / 直连调用可能不经 get_state 就确认。同一把
        # per-path 锁覆盖读改写全程（含下方 reference_video 分支自己的落盘），避免中途被
        # save_content 或迁移的写入插队——_read_step1_migrated 要求调用方已持锁。
        with self.pm.file_lock(path):
            content, project = self._read_step1_migrated(
                project_name, project, episode, path, project_path, supported_durations
            )
            if content is None and not path.exists():
                raise ScriptReviewError("no_step1")
            # 确认前按 step1 变体模型校验 step1 结构：content 为 None（非法 JSON / 非对象）同样会
            # 在此被 model_validate 拒绝为 invalid_content，不会被仅凭「文件存在」放行。
            kind, model = self._resolve_step1_model(project, episode)
            try:
                validated = model.model_validate(content)
            except ValidationError as exc:
                raise ScriptReviewError("invalid_content", str(exc)) from exc

            marked_shape = {
                "drama": ("scenes", "scenes"),
                "reference_video": ("units", "video_units"),
            }.get(kind)
            if marked_shape is not None:
                root, skeleton = marked_shape
                for unit in validated.model_dump()[root]:
                    if unit.get("needs_replan") is not True:
                        continue
                    admission = admit_script_unit(skeleton, unit)
                    raise ScriptReviewError("speech_admission", admission=admission)

            if kind == "reference_video":
                # 指纹按刚写盘的这份对象直接算，确认记录与实际写入内容一致。
                dumped = validated.model_dump()
                for unit in dumped["units"]:
                    admission = admit_script_unit("video_units", unit)
                    if not admission.allowed:
                        raise ScriptReviewError("speech_admission", admission=admission)
                # 单一写盘出口（已持同一把 per-path 锁，不再套 step1_write_lock）；同临界区
                # 读改写无并发窗口，不做基线比对。内容真的变了时，step2 草稿的基底随之
                # 失效，由出口按变更清理。
                script_review.write_step1_locked(project_path, episode, dumped)
                fingerprint = script_review.content_fingerprint_of_data(dumped)
            else:
                # 指纹从校验通过的 content 派生，不二次读盘：确认记录须对应这里校验过的内容。
                fingerprint = script_review.content_fingerprint_of_data(content)

        confirmed_at = datetime.now(UTC).isoformat()

        def _mutate(p: dict[str, Any]) -> None:
            if not script_review.apply_confirmation(p, episode, fingerprint, confirmed_at):
                raise ScriptReviewError("episode_not_found")

        self.pm.update_project(project_name, _mutate)


def _read_json(path: Path) -> dict[str, Any] | None:
    """读取并解析结构化 step1 文件；缺失 / 非法 JSON / 非对象时返回 None（状态另由指纹派生兜底）。

    容错读取复用 ``lib.json_io.load_json_or_none``（OSError / JSON / 编码错误归 None），与项目
    其余 JSON 读取同口径；本函数再叠加「顶层须为对象」守卫，非对象同样返回 None。
    """
    data = load_json_or_none(path)
    return data if isinstance(data, dict) else None
