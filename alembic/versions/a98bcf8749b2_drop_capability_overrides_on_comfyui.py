"""能力覆盖扩到两类维度：已有覆盖按维度映射后保留，ComfyUI 端点上的清除

Revision ID: a98bcf8749b2
Revises: c5f2a8d61e93
Create Date: 2026-10-15 07:00:00.000000

能力覆盖从只覆盖视频通道事实，扩到模型事实与通道事实两类维度（docs/adr/0104）。已有覆盖的键就是
``VideoCapabilities`` 的字段名，每个键即一个通道事实维度，扩展后维度名不变，按维度映射是恒等映射，原样
保留。新增的模型事实维度（``durations``、``text_to_image`` 等）键名与它们不重叠，存量行不受影响。

ComfyUI 端点的能力只从节点绑定推导（docs/adr/0082），不接受能力覆盖：挂在 ComfyUI 端点上的模型行、
以及 ComfyUI 协议供应商下的模型行（两者在写入侧按双向挂接配对，这里两种判据都认，兜住绕过界面的写入），
存量覆盖清除。执行层此前已忽略这些值，清除不改变生成行为。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a98bcf8749b2"
down_revision: str | Sequence[str] | None = "c5f2a8d61e93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE custom_provider_model
            SET capability_overrides = NULL
            WHERE capability_overrides IS NOT NULL
              AND (
                provider_id IN (SELECT id FROM custom_provider WHERE discovery_format = 'comfyui')
                OR endpoint IN (SELECT 'ce-' || CAST(id AS VARCHAR) FROM custom_endpoint WHERE kind = 'comfyui')
              )
            """
        )
    )


def downgrade() -> None:
    # 清除的覆盖在执行层本就不生效，回退不恢复。
    pass
