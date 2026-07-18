"""step1→step2 web 审核 gate 的服务层：审阅状态读取、结构化中间态编辑、确认动作。

纯 gate 逻辑（适用性 / 指纹 / 状态派生）在 ``lib.script_review``；本层叠加 ProjectManager
持久化（确认指纹落 project.json ``episodes[i].step1_review``）与结构化内容的 Pydantic 校验、落盘。

确认触发 step2 的语义是「放行」而非「服务端 launcher」：step2（剧本视觉生成）由 agent 的
``generate_episode_script`` 工具执行，本服务只负责把审核状态翻到 confirmed；该工具读时经
``lib.script_review.gate_blocks_step2`` 校验，pending 时拒绝、confirmed 后放行。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from lib import script_review
from lib.episode_ledger import backfill_episode_ledger, discover_episode_files
from lib.json_io import atomic_write_json, load_json_or_none
from lib.project_manager import ProjectManager
from lib.reference_video import rederive_unit_references
from lib.script_models import DramaNormalizedScript, NarrationStep1Draft, ReferenceStep1Draft

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
        分集规划器、手动预拆分上传的存量场景），先用 ``backfill_episode_ledger`` 自愈补建条目
        再重新校验，而非直接判死锁——手动预拆分与账本为空同时出现时，唯一的登记来源就是这次
        自愈，不做即无法登记、也无法确认。派生文件也不存在时（真正缺失的集号）不自愈，直接抛出。
        """
        if script_review.find_episode(project, episode) is not None:
            return project
        project_path = self.pm.get_project_path(project_name)
        if episode not in discover_episode_files(project_path):
            raise ScriptReviewError("episode_not_found")
        project = self._backfill_ledger(project_name)
        if script_review.find_episode(project, episode) is None:
            raise ScriptReviewError("episode_not_found")
        return project

    def _backfill_ledger(self, project_name: str) -> dict[str, Any]:
        """在项目锁内运行一次 ``backfill_episode_ledger`` 并落盘，返回自愈后的 project。

        落盘走 ``ProjectManager.update_project`` 的锁内 read-modify-write，不绕锁直写
        project.json。``backfill_episode_ledger`` 是不修改入参的纯函数，返回新 dict；
        这里在回调内把结果拷回被就地修改的 ``p``，桥接纯函数输出与 update_project 的
        原地修改约定。已带 ``ledger_status`` 的条目在纯函数内部即被跳过，重复触发不会
        重写既有条目或产生重复集号。
        """
        project_path = self.pm.get_project_path(project_name)

        def _mutate(p: dict[str, Any]) -> None:
            healed = backfill_episode_ledger(project_path, p)
            p.clear()
            p.update(healed)

        return self.pm.update_project(project_name, _mutate)

    def get_state(self, project_name: str, episode: int) -> dict[str, Any]:
        """返回该集审核状态 + 结构化中间态内容（供 web 渲染）。

        ``content`` 为解析后的结构化 step1（drama: {title, scenes[]}；narration: {segments[]}；
        reference_video: {units[]}）；不适用 gate 或 step1 缺失 / 损坏时为 None。
        """
        project = self.pm.load_project(project_name)
        project_path = self.pm.get_project_path(project_name)
        path = script_review.step1_path(project_path, project, episode)
        if path is not None:
            # 适用 gate（drama / narration 非 reference_video）才要求分集已登记；
            # not_applicable（ad / reference_video）与分集存在性无关，保持原样返回。
            project = self._require_episode(project_name, project, episode)
        fingerprint = script_review.content_fingerprint(path) if path is not None else None
        return {
            "episode": episode,
            "content_mode": project.get("content_mode"),
            "status": script_review.review_status(project_path, project, episode),
            "fingerprint": fingerprint,
            "confirmed_at": script_review.stored_review(project, episode).get("confirmed_at"),
            "content": _read_json(path) if path is not None else None,
        }

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
        atomic_write_json(path, validated)
        return self.get_state(project_name, episode)

    def confirm(self, project_name: str, episode: int) -> dict[str, Any]:
        """把该集审核状态翻到 confirmed（记录当前 step1 内容指纹），放行 step2。

        无 step1 / 不适用 / 集条目缺失 / step1 内容结构非法时抛 ScriptReviewError，由 router 映射 4xx。
        """
        project = self.pm.load_project(project_name)
        project_path = self.pm.get_project_path(project_name)
        path = script_review.step1_path(project_path, project, episode)
        if path is None:
            raise ScriptReviewError("not_applicable")
        project = self._require_episode(project_name, project, episode)
        fingerprint = script_review.content_fingerprint(path)
        if fingerprint is None:
            raise ScriptReviewError("no_step1")
        # 确认前按 step1 变体模型校验 step1 结构：content_fingerprint 对非法 JSON / 任意字节
        # 也会产出哈希，仅凭 fingerprint 非空会把损坏草稿确认放行、拖到 step2 才暴露；此处拒绝。
        kind, model = self._resolve_step1_model(project, episode)
        try:
            validated = model.model_validate(_read_json(path))
        except ValidationError as exc:
            raise ScriptReviewError("invalid_content", str(exc)) from exc

        if kind == "reference_video":
            # references 是从 shot 正文机械派生的字段（同 save_content）。agent / 人工可能绕过
            # save_content 直改 step1 文件正文后直接调用本方法确认：若不在此重派生，references
            # 会带着与新正文不符的陈旧引用被确认放行，step2 仍用旧 [图N] 映射生成。重派生后落盘，
            # 指纹改按落盘后的内容算，确认记录与实际 step1 内容一致。
            dumped = validated.model_dump()
            rederive_unit_references(dumped["units"], project)
            atomic_write_json(path, dumped)
            fingerprint = script_review.content_fingerprint(path)
            if fingerprint is None:
                raise ScriptReviewError("no_step1")

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
