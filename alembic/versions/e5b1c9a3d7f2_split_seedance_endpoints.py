"""方舟 Seedance 端点按代际拆成三个，已有模型回填为升级前实际使用的那一代

Revision ID: e5b1c9a3d7f2
Revises: c4e8a1f7b3d2
Create Date: 2026-11-04 18:00:00.000000

``ark-seedance`` 拆成 ``ark-seedance-1``、``ark-seedance-2``、``ark-seedance-2-5``：三代共用创建任务与轮询接口，请求体
（是否带 ``service_tier``、首帧比例约束）、计量（视频 token 像素表、服务档）与素材引用契约不同。拆分前通道按名字认定
代际，拆分后由模型挂接的端点决定。

回填复现升级前通道实际依据的输入：模型对应条目的名字与 API 模型名，第一个写明 2.5 / 2.0 的名字决定代际，都没写明
时为 1.x。对应条目取条目绑定（``entry_binding`` 的官方模型名部分），没有绑定时按名字对应到的条目与 API 模型名同代
（按名字对应要求两者归一化后相同，代际写法也就相同），所以只看 API 模型名。这样：

- 请求体与升级前一致：升级前只要任一名字属于 2.x 就不带 ``service_tier``，回填后同样落到 2.x 端点；
- 靠条目绑定识别的不透明 ID（如 ``ep-...`` 绑定 Seedance 2.0）回填为绑定条目的那一代。

改写两处：自定义供应商挂 ``ark-seedance`` 的模型，与内置方舟、方舟 Agent Plan 下记了 ``ark-seedance`` 的用户添加
模型。升级前提交、检查点冻结了 ``ark-seedance`` 的任务不改写，续跑时按协议等价映射到后继端点
（``lib.backends.resume_guard.RETIRED_ENDPOINT_SUCCESSORS``）。名字规则在此冻结一份字面量，不 import 应用代码：迁移是
时点快照，日后选端点的规则改了也不该改写已迁移过的行。

降级把三个端点改回 ``ark-seedance``。降级后，升级期间提交的任务检查点里的新端点名不再被认得，续跑会被拒绝。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5b1c9a3d7f2"
down_revision: str | Sequence[str] | None = "c4e8a1f7b3d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_ENDPOINT = "ark-seedance"
_NEW_ENDPOINTS = ("ark-seedance-1", "ark-seedance-2", "ark-seedance-2-5")
_ARK_PROVIDERS = ("ark", "ark-agent-plan")


def _generation_endpoint(name: str) -> str | None:
    """升级前通道认定 Seedance 代际的名字规则（``_is_seedance_2_5`` / ``_is_seedance_2``，原样抄录）。"""
    lowered = name.lower()
    if "seedance-2-5" in lowered or "seedance-2.5" in lowered:
        return "ark-seedance-2-5"
    if "seedance-2-0" in lowered or "seedance-2.0" in lowered:
        return "ark-seedance-2"
    return None


def _endpoint_for(model_id: str, entry_binding: str | None) -> str:
    names = [entry_binding.rpartition("/")[2]] if entry_binding else []
    for name in (*names, model_id):
        endpoint = _generation_endpoint(name)
        if endpoint is not None:
            return endpoint
    return "ark-seedance-1"


def _tables() -> tuple[sa.TableClause, sa.TableClause]:
    custom = sa.table(
        "custom_provider_model",
        sa.column("id", sa.Integer),
        sa.column("model_id", sa.String),
        sa.column("endpoint", sa.String),
        sa.column("entry_binding", sa.String),
    )
    builtin = sa.table(
        "builtin_provider_model",
        sa.column("id", sa.Integer),
        sa.column("provider_id", sa.String),
        sa.column("model_id", sa.String),
        sa.column("endpoint", sa.String),
        sa.column("entry_binding", sa.String),
    )
    return custom, builtin


def upgrade() -> None:
    connection = op.get_bind()
    custom, builtin = _tables()
    custom_rows = connection.execute(
        sa.select(custom.c.id, custom.c.model_id, custom.c.entry_binding).where(custom.c.endpoint == _OLD_ENDPOINT)
    ).all()
    for row_id, model_id, entry_binding in custom_rows:
        connection.execute(
            sa.update(custom).where(custom.c.id == row_id).values(endpoint=_endpoint_for(model_id, entry_binding))
        )
    builtin_rows = connection.execute(
        sa.select(builtin.c.id, builtin.c.model_id, builtin.c.entry_binding).where(
            builtin.c.endpoint == _OLD_ENDPOINT, builtin.c.provider_id.in_(_ARK_PROVIDERS)
        )
    ).all()
    for row_id, model_id, entry_binding in builtin_rows:
        connection.execute(
            sa.update(builtin).where(builtin.c.id == row_id).values(endpoint=_endpoint_for(model_id, entry_binding))
        )


def downgrade() -> None:
    connection = op.get_bind()
    custom, builtin = _tables()
    connection.execute(sa.update(custom).where(custom.c.endpoint.in_(_NEW_ENDPOINTS)).values(endpoint=_OLD_ENDPOINT))
    connection.execute(
        sa.update(builtin)
        .where(builtin.c.endpoint.in_(_NEW_ENDPOINTS), builtin.c.provider_id.in_(_ARK_PROVIDERS))
        .values(endpoint=_OLD_ENDPOINT)
    )
