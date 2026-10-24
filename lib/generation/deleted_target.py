"""生成完成时目标已删除：结果不写回原路径、不写版本记录，直接成为项目回收站中的一条删除记录。

生成任务执行期间，创作者或 Agent 可能删掉了它的目标资源（资产、分镜条目、视频单元、宫格等）。
:class:`~lib.generation.media_generator.MediaGenerator` 在费用结算之后、正式提交之前调用
:func:`trash_result_if_target_deleted` 复核一次；目标已不在项目中时，暂存产物按原路径记进项目回收站，
抛出 :class:`GenerationTargetDeletedError` 跳过提交。任务入口接住它，以 :attr:`~GenerationTargetDeletedError.result`
作为任务结果正常结束，费用记录不受影响，被删的内容也不会复活。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lib.artifacts.formal_write import project_metadata_lock
from lib.project import project_trash
from lib.project.reference_index import ReferenceIndex
from lib.project.resource_paths import resource_relative_path

__all__ = [
    "GENERATION_RESULT_KIND",
    "TARGET_DELETED_NOTE",
    "GenerationTargetDeletedError",
    "trash_result_if_target_deleted",
]

#: 删除记录的对象类型：一份生成结果。
GENERATION_RESULT_KIND = "generation_result"
#: 删除记录的标注「生成完成时目标已删除」，前端据此改写发起方文案。
TARGET_DELETED_NOTE = "target_deleted_during_generation"


class GenerationTargetDeletedError(Exception):
    """生成结果已进入项目回收站。这不是失败：任务以 :attr:`result` 正常结束。"""

    def __init__(self, record: project_trash.TrashRecord, *, resource_type: str, resource_id: str) -> None:
        super().__init__(f"generation target deleted: {resource_type}/{resource_id}")
        self.record = record
        self.result: dict[str, Any] = {
            "resource_type": resource_type,
            "resource_id": resource_id,
            "file_path": None,
            "target_deleted": True,
            "trash_record_id": record.id,
        }


def trash_result_if_target_deleted(
    project_dir: Path,
    *,
    resource_type: str,
    resource_id: str,
    staged_file: Path,
    target: tuple[str, str] | None = None,
) -> None:
    """``target``（缺省为产物自身的资源）已不在项目中时，把 ``staged_file`` 记进回收站并抛出。

    记录里的文件路径是产物本该落下的原路径，恢复时按回收站的规则加回。
    """
    target_type, target_id = target or (resource_type, resource_id)
    # shortcut: 复核与随后的正式提交分属两次加锁（项目锁不可重入，提交路径先取剧本锁再取项目锁），
    # 两者之间的删除仍按各提交路径原有的目标缺失处理；需要严格原子时把复核并进各提交路径的锁内。
    with project_metadata_lock(project_dir):
        if ReferenceIndex.build(project_dir).contains_resource(target_type, target_id):
            return
        rel = resource_relative_path(resource_type, resource_id)
        record = project_trash.record_deletion(
            project_dir,
            {
                "kind": GENERATION_RESULT_KIND,
                "path": rel,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "note": TARGET_DELETED_NOTE,
            },
            [rel],
            sources={rel: staged_file},
        )
    raise GenerationTargetDeletedError(record, resource_type=resource_type, resource_id=resource_id)
