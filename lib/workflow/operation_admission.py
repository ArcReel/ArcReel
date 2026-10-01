"""AI 操作的结构准入：每个操作一份谓词，制作状态与操作入口调用同一份（见 ``docs/adr/0091``）。

准入属于 AI 操作，不属于内容，也不属于流水线位置：每个谓词只检查该操作自己的输入是否成立。
界面上的置灰原因、Agent 被拒的理由与建议下一步的依据都取自同一份结论，理由码是稳定的闭集。

这里只算**结构准入**：只读项目目录里已落盘的内容，不报价、不问供应商、不求视频请求事实。
需要这些外部事实的**提交准入**仍在预览与提交时计算（整批准入见 ``docs/adr/0061``）。
生成入口的引用准入维持 ``docs/adr/0073``，由 ``lib.references.reference_admission`` 承担。

谓词的输入是事实而不是路径：同一条事实由本模块的事实函数统一取得，制作状态与入口各自读盘时
经同一个事实函数，不各写一份「有没有源文」的判据。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from lib.episode.episode_ledger import SourceDoc
from lib.episode.episode_paths import episode_source_path
from lib.episode.episode_sources import SourceOrigin, episode_source_origin
from lib.script.script_models import PENDING_AUTHORING_FIELD
from lib.script.script_skeleton import SKELETONS


class AdmissionState(StrEnum):
    """一个 AI 操作此刻的准入结论。"""

    ADMITTED = "admitted"
    REFUSED = "refused"
    #: 该操作对此类项目不适用（如广告/短片没有分集规划），不是缺了什么输入。
    NOT_APPLICABLE = "not_applicable"


class AdmissionReason(StrEnum):
    """不能执行的理由码闭集；界面按码取文案，Agent 按码转述。"""

    NOT_APPLICABLE = "operation_not_applicable"
    WHOLE_SOURCE_MISSING = "whole_source_missing"
    EPISODE_SOURCE_MISSING = "episode_source_missing"
    FORMAL_SCRIPT_MISSING = "formal_script_missing"
    FORMAL_SCRIPT_EXISTS = "formal_script_exists"
    NO_PENDING_AUTHORING = "no_pending_authoring"
    PROMPT_AUTHORING_DRAFT_PENDING = "prompt_authoring_draft_pending"
    AD_INPUTS_MISSING = "ad_brief_and_products_missing"


class OperationAdmission(BaseModel):
    """一个 AI 操作的结构准入结论：放行时 ``reason`` 为空，其余两态必带理由码。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: AdmissionState
    reason: AdmissionReason | None = None

    @property
    def admitted(self) -> bool:
        return self.state is AdmissionState.ADMITTED


ADMITTED = OperationAdmission(state=AdmissionState.ADMITTED)
NOT_APPLICABLE = OperationAdmission(state=AdmissionState.NOT_APPLICABLE, reason=AdmissionReason.NOT_APPLICABLE)


def _refused(reason: AdmissionReason) -> OperationAdmission:
    return OperationAdmission(state=AdmissionState.REFUSED, reason=reason)


# ---------------------------------------------------------------------------
# 事实函数：制作状态与操作入口取同一条事实的唯一口径
# ---------------------------------------------------------------------------


def whole_source_present(docs: Iterable[SourceDoc]) -> bool:
    """有可供分集规划的整本源文：项目登记的整本源文文件里有非空白的原文。

    ``docs`` 取自 :func:`lib.episode.episode_sources.discover_sources`；未登记的文件不算。
    """
    return any(doc.text.strip() for doc in docs)


def episode_source_present(project_path: Path, episode: int, entry: Mapping[str, Any] | None) -> bool:
    """本集有非空白的集原文：账本记为切出集或自带原文，且集文件 ``source/episode_N.txt`` 非空白。

    不在账本里（``entry`` 为 None）或账本记为无原文的集，即使 ``source/`` 里恰好有同名文件也不算。
    """
    if entry is None or episode_source_origin(entry) is SourceOrigin.NONE:
        return False
    path = episode_source_path(project_path, episode)
    if path.is_symlink() or not path.is_file():
        return False
    try:
        return bool(path.read_text(encoding="utf-8").strip())
    except (OSError, UnicodeDecodeError):
        return False


def ad_inputs_present(project: Mapping[str, Any]) -> bool:
    """广告/短片的创作灵感与商品至少填了一项。"""
    brief = project.get("brief")
    products = project.get("products")
    return (isinstance(brief, str) and bool(brief.strip())) or (isinstance(products, Mapping) and bool(products))


def pending_authoring_entry_ids(items: Sequence[Any], kind: str | None) -> list[str]:
    """正式脚本里带待编写标记的条目 id，按脚本顺序；四种骨架都由提示词编写补写。"""
    if kind not in SKELETONS:
        return []
    id_field = SKELETONS[kind].id_field
    return [
        str(item[id_field])
        for item in items
        if isinstance(item, Mapping)
        and isinstance(item.get(id_field), str)
        and item[id_field]
        and item.get(PENDING_AUTHORING_FIELD) is True
    ]


# ---------------------------------------------------------------------------
# 准入谓词：每个 AI 操作一份
# ---------------------------------------------------------------------------


def admit_plan_episodes(content_mode: object, *, whole_source: bool) -> OperationAdmission:
    """AI 分集规划：有整本源文。"""
    if content_mode == "ad":
        return NOT_APPLICABLE
    return ADMITTED if whole_source else _refused(AdmissionReason.WHOLE_SOURCE_MISSING)


def admit_script_plan(content_mode: object, *, episode_source: bool) -> OperationAdmission:
    """AI 规划脚本：本集有集原文。"""
    if content_mode == "ad":
        return NOT_APPLICABLE
    return ADMITTED if episode_source else _refused(AdmissionReason.EPISODE_SOURCE_MISSING)


def admit_author_prompts(
    *,
    formal_script: bool,
    pending_ids: Sequence[str],
    draft_pending: bool,
    explicit_ids: Sequence[str] = (),
) -> OperationAdmission:
    """提示词编写：正式脚本中有待编写条目；点名补缺或重写的条目不看待编写标记。

    编写自身的草稿在场时先处置草稿，编写结果无处落地。
    """
    if draft_pending:
        return _refused(AdmissionReason.PROMPT_AUTHORING_DRAFT_PENDING)
    if not formal_script:
        return _refused(AdmissionReason.FORMAL_SCRIPT_MISSING)
    if pending_ids or explicit_ids:
        return ADMITTED
    return _refused(AdmissionReason.NO_PENDING_AUTHORING)


def admit_ad_script(content_mode: object, *, formal_script: bool, ad_inputs: bool) -> OperationAdmission:
    """广告/短片 AI 生成脚本：创作灵感与商品至少一项；已有正式脚本时不整份生成。"""
    if content_mode != "ad":
        return NOT_APPLICABLE
    if formal_script:
        return _refused(AdmissionReason.FORMAL_SCRIPT_EXISTS)
    return ADMITTED if ad_inputs else _refused(AdmissionReason.AD_INPUTS_MISSING)


__all__ = [
    "ADMITTED",
    "NOT_APPLICABLE",
    "AdmissionReason",
    "AdmissionState",
    "OperationAdmission",
    "ad_inputs_present",
    "admit_ad_script",
    "admit_author_prompts",
    "admit_plan_episodes",
    "admit_script_plan",
    "episode_source_present",
    "pending_authoring_entry_ids",
    "whole_source_present",
]
