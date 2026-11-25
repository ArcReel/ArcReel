"""脚本及脚本规划共用的增量编辑操作。"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PatchUpdateOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    op: Literal["update"]
    id: str = Field(min_length=1, description="要修改的条目 id（segment_id / scene_id / shot_id / unit_id）")
    fields: dict[str, Any] = Field(min_length=1, description="字段名到新值的映射")


class PatchInsertOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    op: Literal["insert"]
    after_id: str | None = Field(
        default=None,
        min_length=1,
        description="新条目插在这个 id 之后；省略或为 null 时插到最前（空脚本的第一条即如此）",
    )
    item: dict[str, Any] = Field(
        description="新条目的完整内容；id 由系统分配，取本集用过的最大序号之后的号，已删除条目的编号不会再发出"
    )


class PatchRemoveOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    op: Literal["remove"]
    id: str = Field(min_length=1, description="要删除的条目 id")


class PatchMoveOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    op: Literal["move"]
    id: str = Field(min_length=1, description="要移动的条目 id")
    after_id: str | None = Field(
        default=None,
        min_length=1,
        description="移到这个 id 之后；省略或为 null 时移到最前",
    )


class PatchSplitOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    op: Literal["split"]
    id: str = Field(min_length=1, description="要拆分的条目 id；第一段沿用该 id，其余段按新增条目的规则分配新 id")
    parts: list[dict[str, Any]] = Field(min_length=2, description="拆分后的各段完整内容，按顺序排列，至少两段")
