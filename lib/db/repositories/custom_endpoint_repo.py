"""自定义调用端点仓储：定义本体 CRUD 与模型行引用查询。"""

from __future__ import annotations

from typing import Any, NamedTuple

from sqlalchemy import delete, select

from lib.db.models.custom_endpoint import CustomEndpoint
from lib.db.models.custom_provider import CustomProvider, CustomProviderModel
from lib.db.repositories.base import BaseRepository


class EndpointReference(NamedTuple):
    """一条引用该调用端点的模型行，连同它所属的供应商。"""

    provider_id: int
    provider_display_name: str
    model_id: str
    model_display_name: str


class CustomEndpointRepository(BaseRepository):
    """``custom_endpoint`` 表的读写。

    镜像列（``kind`` / ``schema_version`` / ``media_type`` / ``display_name``）由调用方在写入
    前从 ``definition`` 派生后一并传入，本层不解析定义：派生规则属格式层，落在
    ``lib.custom_provider.endpoint_resolution``，仓储只负责持久化。
    """

    async def create(
        self,
        *,
        definition: dict[str, Any],
        kind: str,
        schema_version: str,
        media_type: str,
        display_name: str,
    ) -> CustomEndpoint:
        endpoint = CustomEndpoint(
            definition=definition,
            kind=kind,
            schema_version=schema_version,
            media_type=media_type,
            display_name=display_name,
        )
        self.session.add(endpoint)
        await self.session.flush()  # 取回自增 id：端点键 ce-<id> 由它派生
        return endpoint

    async def get(self, endpoint_id: int) -> CustomEndpoint | None:
        stmt = select(CustomEndpoint).where(CustomEndpoint.id == endpoint_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(self) -> list[CustomEndpoint]:
        stmt = select(CustomEndpoint).order_by(CustomEndpoint.id)
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def update(
        self,
        endpoint_id: int,
        *,
        definition: dict[str, Any],
        kind: str,
        schema_version: str,
        media_type: str,
        display_name: str,
    ) -> CustomEndpoint | None:
        """原地整份替换定义与镜像列，保留 id（即端点键）与模型行挂接。不存在时返回 None。"""
        endpoint = await self.get(endpoint_id)
        if endpoint is None:
            return None
        endpoint.definition = definition
        endpoint.kind = kind
        endpoint.schema_version = schema_version
        endpoint.media_type = media_type
        endpoint.display_name = display_name
        await self.session.flush()
        return endpoint

    async def delete(self, endpoint_id: int) -> None:
        await self.session.execute(delete(CustomEndpoint).where(CustomEndpoint.id == endpoint_id))
        await self.session.flush()

    # ── 引用完整性 ────────────────────────────────────────────────

    async def list_references(self, endpoint_key: str) -> list[EndpointReference]:
        """按 ``endpoint`` 键字面量列出引用它的模型行，供 409 响应说明「谁还在用」。

        引用完整性靠本查询而非 FK：409 要回引用清单，FK 约束只能抛一个 IntegrityError，
        而删除路径本就要先查引用（docs/adr/0067）。
        """
        stmt = (
            select(
                CustomProvider.id,
                CustomProvider.display_name,
                CustomProviderModel.model_id,
                CustomProviderModel.display_name,
            )
            .join(CustomProvider, CustomProvider.id == CustomProviderModel.provider_id)
            .where(CustomProviderModel.endpoint == endpoint_key)
            .order_by(CustomProviderModel.provider_id, CustomProviderModel.id)
        )
        result = await self.session.execute(stmt)
        return [EndpointReference(*row) for row in result.all()]
