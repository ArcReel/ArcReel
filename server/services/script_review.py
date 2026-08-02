"""step1→step2 web 审核 gate 的服务层：审阅状态读取、结构化中间态编辑、确认动作。

纯 gate 逻辑（适用性 / 指纹 / 状态派生）在 ``lib.script_review``；本层叠加 ProjectManager
持久化（确认指纹落 project.json ``episodes[i].step1_review``）与结构化内容的 Pydantic 校验、落盘。

确认触发 step2 的语义是「放行」而非「服务端 launcher」：step2（剧本视觉生成）由 agent 的
``generate_episode_script`` 工具执行，本服务只负责把审核状态翻到 confirmed；该工具读时经
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
from lib.episode_ledger import discover_episode_files, register_orphan_episode_entries
from lib.json_io import atomic_write_json, load_json_or_none
from lib.project_manager import ProjectManager
from lib.reference_video import rederive_unit_references
from lib.reference_video.quarantine import QUARANTINE_KIND_STEP1, read_quarantine, violation_entries
from lib.script_models import DramaNormalizedScript, NarrationStep1Draft, ReferenceStep1Draft
from server.agent_runtime.sdk_tools._context import reference_unit_duration_tiers, resolve_video_caps

logger = logging.getLogger(__name__)

#: 结构化 step1 中间态的校验模型（按 step1 变体 ``script_review.step1_kind``）。编辑保存按此做结构校验：
#: drama 为内容层 DramaNormalizedScript（utterances / source_text / scene_description），
#: narration 为 NarrationStep1Draft（结构化 novel_text 片段），reference_video 为 ReferenceStep1Draft
#: （units → shots + 派生 references）。
_STEP1_CONTENT_MODEL: dict[str, type[BaseModel]] = {
    "drama": DramaNormalizedScript,
    "narration": NarrationStep1Draft,
    "reference_video": ReferenceStep1Draft,
}


class ScriptReviewError(Exception):
    """gate 操作的领域错误。``code`` 供 router 映射 HTTP 状态与 i18n key；``message`` 为技术细节。"""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message


class ScriptReviewService:
    """封装 step1→step2 审核 gate 的读写。router 与测试经此操作 gate，不直接碰文件 / project.json。"""

    def __init__(self, pm: ProjectManager):
        self.pm = pm

    def _resolve_step1_model(self, project: dict[str, Any], episode: int) -> tuple[str, type[BaseModel]]:
        """该集 step1 变体 + 结构校验模型；不适用 gate（无结构化 step1）时抛 not_applicable。

        变体判定单一真相源在 ``script_review.step1_kind``（reference_video 按 effective_mode 优先，
        跨 content_mode）；本层据此选 Pydantic 模型。返回变体名供 rv 保存时的 references 重派生分支。
        """
        kind = script_review.step1_kind(project, episode)
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

    def _read_step1_migrated(
        self, project_name: str, project: dict[str, Any], episode: int, path: Path
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """读结构化 step1，并对参考生视频草稿做一次性时长收编迁移；返回 ``(内容, 最新 project)``。

        草稿是 gate 的三个入口（读状态 / 保存 / 确认）唯一的内容来源，而收编后的
        ``ReferenceStep1Unit`` 要求 unit 级 ``duration_seconds``、``Shot`` 不接受多余字段：
        存量草稿若不在读时迁移，保存与确认都会撞结构校验，用户在 gate 里既改不了也确认不了。
        剧集脚本的迁移在 ``ProjectManager.load_script``，草稿的在这里，两处共用同一迁移器。

        档位表走 ``resolve_raw_supported_durations`` 的同步回退链（project.json → registry），
        与 step2 生成侧同源：迁移幂等一次性、谁先跑谁定终局，两侧口径不一致时先跑的审阅门会
        把落在结构区间内但非档位成员的秒数固化到盘上，step2 的枚举 schema 随后硬拒，用户在
        gate 里既看不出问题也改不动。解析不到（项目未配置视频型号）时传 None 退回结构区间
        clamp——缺配置不该阻断草稿加载，档位偏移仍由执行时取档兜底。

        调用方须已持有 ``self.pm.file_lock(path)``——本函数只做读改写，不自行加锁，避免与
        调用方（如 ``confirm``）已持有的同一把锁发生同线程二次获取的死锁。
        """
        content = _read_json(path)
        if content is None or script_review.step1_kind(project, episode) != "reference_video":
            return content, project

        updated, _warnings = script_review.migrate_step1_draft_in_place(
            path,
            content,
            episode=episode,
            update_project=lambda mutate: self.pm.update_project(project_name, mutate),
            supported_durations=resolve_raw_supported_durations(project),
        )
        return content, updated or project

    def get_state(self, project_name: str, episode: int) -> dict[str, Any]:
        """返回该集审核状态 + 结构化中间态内容（供 web 渲染）。

        ``content`` 为解析后的结构化 step1（drama: {title, scenes[]}；narration: {segments[]}；
        reference_video: {units[]}）；不适用 gate 或 step1 缺失 / 损坏时为 None。

        ``supported_durations`` 只在 reference_video 变体下非 None：unit 时长是档位枚举而非
        自由秒数，web 侧要按它渲染选择项。取值走与读时迁移同一个 ``resolve_raw_supported_durations``
        （project.json → registry 同步回退链），两处不同源的话，gate 里能选的档位会与迁移
        收编到的档位不一致。项目未配置视频型号而解析不到时为 None，呈现层退回只读秒数。
        """
        project = self.pm.load_project(project_name)
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
                content, project = self._read_step1_migrated(project_name, project, episode, path)
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
            "supported_durations": (
                resolve_raw_supported_durations(project)
                if script_review.step1_kind(project, episode) == "reference_video"
                else None
            ),
        }

    async def get_quarantine_info(self, project_name: str, episode: int) -> dict[str, Any] | None:
        """reference_video 变体的隔离草稿信息（供 web 审核 gate 呈现违约行内锚定），不适用 /
        无隔离草稿时 None。

        读时按产出时那套校验器全量重算（``revalidate_reference_step1_draft``，晋升工具同一份
        代码），不信任草稿里 ``violations`` 的上一轮快照——隔离期间源文或模型配置可能已变，
        报告要对现值负责。``get_state`` 保持同步（无需 await 视频能力解析），故本方法独立于
        它，由 router 在同一次请求内合并两者的返回。

        ``content`` 校验通过时返回收编后的扁平产出（时长已按当前档位收编），未通过时返回草稿
        原样内容 + 违约列表，供呈现层原样展示 agent 手改的那份文本。meta 被改坏以致无从重算
        时，把「无法重算」本身作为一条违约返回，而不是退回草稿里那份上一轮快照——报告一律
        对现值负责，读时重算失败也是现值的一部分。

        项目 / 隔离草稿的同步文件读取经 ``asyncio.to_thread`` 卸到线程——本方法整体是
        ``async``（内部要 ``await`` 重算里的能力解析），若前半截同步 I/O 直接跑在事件循环上，
        源文越大越占用循环时间，拖慢并发的其它请求；GET 端点已经把 ``get_state`` 包了
        ``asyncio.to_thread``，这里保持同一纪律。
        """
        project = await asyncio.to_thread(self.pm.load_project, project_name)
        if script_review.step1_kind(project, episode) != "reference_video":
            return None
        project_path = self.pm.get_project_path(project_name)
        quarantine_path = script_review.step1_quarantine_path(project_path, project, episode)
        if quarantine_path is None or not quarantine_path.exists():
            return None
        draft = await asyncio.to_thread(read_quarantine, project_path, episode, QUARANTINE_KIND_STEP1)
        if draft is None:
            # 文件存在但信封形状坏（非法 JSON / 顶层非对象 / content 非对象）：`read_quarantine`
            # 按「无隔离草稿」返回 None 是它自己的读取口径（供 confirm 的存在性判断照常阻塞），
            # 但呈现层不能照单全收——那会让面板看起来「干净」，实际隔离草稿仍在阻塞确认。
            return {
                "content": None,
                "violations": [
                    {
                        "code": "quarantine_unreadable",
                        "label": "",
                        "message": "隔离草稿文件已损坏或格式不符，无法解析",
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
            logger.warning("隔离草稿重算失败 project=%s episode=%s：%s", project_name, episode, exc)
            return {
                "content": draft.content,
                "violations": [
                    {
                        "code": "quarantine_unreadable",
                        "label": "",
                        "message": "隔离草稿的产出上下文缺失或损坏，无法重新校验",
                        "line": None,
                    }
                ],
            }
        content = draft.content if revalidation.schema_failed else {"units": revalidation.flat_units}
        return {"content": content, "violations": violation_entries(revalidation.violations)}

    async def get_reference_duration_tiers(self, project_name: str, episode: int) -> dict[str, list[int]] | None:
        """reference_video 变体逐 unit 生效时长档位：``{with_references, without_references}``。

        与 ``get_state.supported_durations``（``resolve_raw_supported_durations`` 的结构区间
        全集，供 ``_read_step1_migrated`` 的存量草稿 clamp）不同源：那个决定「这个秒数结构上
        合不合法」，这个决定「现在选它，``_assert_reference_step1_ready`` 会不会接受」——分辨率
        / 参考图联动约束只影响后者。clamp 保持用全集，避免收窄后把结构合法但当前档位表之外
        的存量秒数误判非法；这里单独给下拉提供收窄后的可选项，同一把尺来自
        ``reference_unit_duration_tiers``（拆分工具校验用的同一份）。无法解析到型号时返回
        None，呈现层退回未收窄的 ``supported_durations``。

        非 reference_video 变体直接返回 None，不做 caps 解析——调用方（router）按此方法自身
        的 step1_kind 判断决定是否调用，不再依赖 ``get_state.supported_durations`` 是否非
        None 这个同步信号：那个信号在自定义供应商（``custom-`` 前缀，caps 是唯一档位来源，
        同步路径拿不到）下恒为 None，会让本方法永远没机会跑，越档校验随之失效。

        caps 先解析：``resolve_raw_supported_durations`` 的三级回退链（caps → project.json
        ``_supported_durations`` → registry）里，registry 那一级查不到自定义供应商，存量
        项目也不落 ``_supported_durations``——caps 是自定义供应商唯一能拿到时长表的来源
        （DB 驱动的能力查询）。caps 若排在 raw 之后解析，自定义供应商项目会在 caps 都没试
        就因 raw 为 None 提前退出，面板退回只读秒数、越档校验也随之失效，即便 caps 原本能
        给出正确答案。
        """
        project = self.pm.load_project(project_name)
        if script_review.step1_kind(project, episode) != "reference_video":
            return None
        try:
            caps = await resolve_video_caps(project)
        except Exception as exc:  # noqa: BLE001 - best-effort：解析失败时收窄回退为不收窄，不阻塞面板
            logger.warning("video_capabilities 解析异常，时长档下拉退回未收窄集合 project=%s：%s", project_name, exc)
            caps = {}
        raw = resolve_raw_supported_durations(project, caps)
        if raw is None:
            return None
        with_refs, without_refs = reference_unit_duration_tiers(project, caps, raw)
        return {"with_references": sorted(set(with_refs)), "without_references": sorted(set(without_refs))}

    def save_content(self, project_name: str, episode: int, content: object) -> dict[str, Any]:
        """校验并落盘编辑后的结构化中间态（手动或 agent 编辑后回写），返回最新状态（重新待审）。

        内容变更使指纹漂移，``get_state`` 据此自动回到 pending_review——保存即重新需要确认。
        """
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
            raise ScriptReviewError("invalid_content", str(exc)) from exc
        if kind == "reference_video":
            # references 是从 shot 文本机械派生的字段：编辑正文后随之重派生，避免正文与 references
            # 漂移（step2 会用陈旧 [图N] 映射生成）。机械变换、不校验能力上限（同 drama / narration 只结构校验）。
            rederive_unit_references(validated["units"], project)
        path.parent.mkdir(parents=True, exist_ok=True)
        # 与 _read_step1_migrated 共享同一把 per-path 锁：保存与迁移的读改写相互互斥。
        with self.pm.file_lock(path):
            atomic_write_json(path, validated)
        return self.get_state(project_name, episode)

    def confirm(self, project_name: str, episode: int) -> dict[str, Any]:
        """把该集审核状态翻到 confirmed（记录当前 step1 内容指纹），放行 step2。

        无 step1 / 不适用 / 集条目缺失 / step1 内容结构非法 / 有违约产物待处置（隔离草稿在场）时
        抛 ScriptReviewError，由 router 映射 4xx。
        """
        project = self.pm.load_project(project_name)
        project_path = self.pm.get_project_path(project_name)
        path = script_review.step1_path(project_path, project, episode)
        if path is None:
            raise ScriptReviewError("not_applicable")
        project = self._require_episode(project_name, project, episode)
        # 隔离草稿在场时拒绝确认：正式 step1 此刻仍是上一版（或不存在），确认它等于替用户
        # 认可一份他没看过的内容，而刚产出的那份违约正文还在隔离草稿里等 agent 处置。校验
        # 口径与生成侧同一把尺——晋升工具用的正是产出时那套校验器，这里只判「是否还在隔离」。
        quarantine = script_review.step1_quarantine_path(project_path, project, episode)
        if quarantine is not None and quarantine.exists():
            raise ScriptReviewError("quarantined", f"step1 违约产物待处置: {quarantine}")
        # 存量草稿先做时长收编再校验：agent / 直连调用可能不经 get_state 就确认。同一把
        # per-path 锁覆盖读改写全程（含下方 reference_video 分支自己的落盘），避免中途被
        # save_content 或迁移的写入插队——_read_step1_migrated 要求调用方已持锁。
        with self.pm.file_lock(path):
            content, project = self._read_step1_migrated(project_name, project, episode, path)
            if content is None and not path.exists():
                raise ScriptReviewError("no_step1")
            # 确认前按 step1 变体模型校验 step1 结构：content 为 None（非法 JSON / 非对象）同样会
            # 在此被 model_validate 拒绝为 invalid_content，不会被仅凭「文件存在」放行。
            kind, model = self._resolve_step1_model(project, episode)
            try:
                validated = model.model_validate(content)
            except ValidationError as exc:
                raise ScriptReviewError("invalid_content", str(exc)) from exc

            if kind == "reference_video":
                # references 是从 shot 正文机械派生的字段（同 save_content）：agent / 人工可能绕过
                # save_content 直改 step1 正文后直接确认，故确认前重派生并落盘。指纹按刚写盘的
                # 这份对象直接算，确认记录与实际写入内容一致。
                dumped = validated.model_dump()
                rederive_unit_references(dumped["units"], project)
                atomic_write_json(path, dumped)
                fingerprint = script_review.content_fingerprint_of_data(dumped)
            else:
                # 指纹从校验通过的 content 派生，不二次读盘：确认记录须对应这里校验过的内容。
                fingerprint = script_review.content_fingerprint_of_data(content)

        confirmed_at = datetime.now(UTC).isoformat()

        def _mutate(p: dict[str, Any]) -> None:
            if not script_review.apply_confirmation(p, episode, fingerprint, confirmed_at):
                raise ScriptReviewError("episode_not_found")

        self.pm.update_project(project_name, _mutate)
        return self.get_state(project_name, episode)


def _read_json(path: Path) -> dict[str, Any] | None:
    """读取并解析结构化 step1 文件；缺失 / 非法 JSON / 非对象时返回 None（状态另由指纹派生兜底）。

    容错读取复用 ``lib.json_io.load_json_or_none``（OSError / JSON / 编码错误归 None），与项目
    其余 JSON 读取同口径；本函数再叠加「顶层须为对象」守卫，非对象同样返回 None。
    """
    data = load_json_or_none(path)
    return data if isinstance(data, dict) else None
