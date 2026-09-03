"""引用准入结论 → 服务端契约：路由的领域异常与整批的逐目标生成问题。

判定本身在 :mod:`lib.reference_admission`；本模块只把同一份结论翻成两种回执形状，
让单条提交与整批准入不会因为回执形状不同而各自实现一遍判定。
"""

from __future__ import annotations

from collections.abc import Iterable

from lib.api_errors import BadRequestError
from lib.generation_result import GenerationAction, GenerationProblem
from lib.reference_admission import (
    SHEET_MISSING_CODE,
    UNREGISTERED_REFERENCE_CODE,
    ReferenceAdmission,
    admit_storyboard_items,
)
from lib.reference_catalog import build_reference_catalog


def require_admitted_storyboard_references(project: dict, items: Iterable[object]) -> None:
    """分镜路线生成入口预检：引用未登记、或角色 / 场景 / 道具没有资产图即拒绝。

    分镜图、宫格图与图生视频的单条提交共用本函数，参考生视频的同一判定在
    :class:`lib.reference_video.request_projection.ReferenceUnitRequestProjector` 里。
    此前这些引用被静默丢弃：用户付费拿到一张少了角色的分镜图，或一段参考不上人的视频，
    看不出是哪个名字出的问题。

    两条轴分别报出：未登记要去登记资产或改名字，无资产图要去把资产图生成出来，动作不同，
    合成一句提示指不出该做哪件事。传入多条时一次报全缺口，用户不必逐次提交才看全。
    """

    admission = admit_storyboard_items(build_reference_catalog(project), items)
    if admission.unregistered:
        raise BadRequestError(UNREGISTERED_REFERENCE_CODE, missing_text=admission.unregistered_text())
    if admission.without_sheet:
        raise BadRequestError(SHEET_MISSING_CODE, missing_text=admission.without_sheet_text())


def reference_admission_problems(admission: ReferenceAdmission, *, unit_id: str) -> tuple[GenerationProblem, ...]:
    """把引用准入的缺口抬进逐目标的生成问题契约。

    与单条提交的 ``require_admitted_storyboard_references`` 判定同源，只是整批不能短路：
    逐目标记名后用户一次就能看全该改哪几条，而不是修一条、提交一次、再撞下一条。
    """

    problems: list[GenerationProblem] = []
    if admission.unregistered:
        problems.append(
            GenerationProblem(
                code=UNREGISTERED_REFERENCE_CODE,
                detail=f"引用了未登记的资产名: {admission.unregistered_text()}",
                action=GenerationAction.GENERATE_DEPENDENCY,
                params={"unit_id": unit_id, "missing_text": admission.unregistered_text()},
            )
        )
    if admission.without_sheet:
        problems.append(
            GenerationProblem(
                code=SHEET_MISSING_CODE,
                detail=f"引用的资产没有资产图: {admission.without_sheet_text()}",
                action=GenerationAction.GENERATE_DEPENDENCY,
                params={"unit_id": unit_id, "missing_text": admission.without_sheet_text()},
            )
        )
    return tuple(problems)
