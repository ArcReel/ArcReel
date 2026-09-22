"""Custom provider repository."""

from __future__ import annotations

import logging
from typing import NamedTuple

from sqlalchemy import String, cast, delete, literal, or_, select

from lib.custom_provider import CUSTOM_ENDPOINT_KEY_PREFIX, is_custom_provider, parse_provider_id
from lib.db.models.custom_endpoint import CustomEndpoint
from lib.db.models.custom_provider import CustomProvider, CustomProviderModel
from lib.db.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


def _media_type_endpoint_filter(media_type: str):
    """按媒体类型筛 endpoint：内置键查表得到，自定义端点读它自己那一行的镜像列。

    自定义端点的键系统分配、不在内置注册表里，媒体类型写在 ``custom_endpoint.media_type``
    这个镜像列上（``docs/adr/0067``），一份 workflow 产图还是产视频由定义自己说了算、从键上
    推不出来。故按 ``ce-<id>`` 与镜像列匹配，而不是按键前缀一律记作视频。
    """
    from lib.custom_provider.endpoints import ENDPOINT_KEYS_BY_MEDIA_TYPE

    builtin = CustomProviderModel.endpoint.in_(ENDPOINT_KEYS_BY_MEDIA_TYPE.get(media_type, ()))
    custom = select(literal(CUSTOM_ENDPOINT_KEY_PREFIX).concat(cast(CustomEndpoint.id, String))).where(
        CustomEndpoint.media_type == media_type
    )
    return or_(builtin, CustomProviderModel.endpoint.in_(custom))


class CustomProviderPrice(NamedTuple):
    """自定义供应商价格三元组，作为 ``calculate_cost`` 的 ``custom_price_*`` 入参来源。

    三字段全 ``None`` 表示无自定义价格：预置供应商 / 畸形 provider id / 查询异常 / 查无模型
    均归为此语义，``calculate_cost`` 据此对自定义供应商缺价时计为 0。
    """

    price_input: float | None = None
    price_output: float | None = None
    currency: str | None = None


_NO_PRICE = CustomProviderPrice()


class CustomProviderRepository(BaseRepository):
    """自定义供应商 + 模型 CRUD。"""

    # ── Provider CRUD ──────────────────────────────────────────────

    async def create_provider(
        self,
        display_name: str,
        discovery_format: str,
        base_url: str,
        api_key: str,
        models: list[dict] | None = None,
        *,
        image_max_workers: int | None = None,
        video_max_workers: int | None = None,
        audio_max_workers: int | None = None,
    ) -> CustomProvider:
        """创建供应商，可选同时创建模型列表。"""
        provider = CustomProvider(
            display_name=display_name,
            discovery_format=discovery_format,
            base_url=base_url,
            api_key=api_key,
            image_max_workers=image_max_workers,
            video_max_workers=video_max_workers,
            audio_max_workers=audio_max_workers,
        )
        self.session.add(provider)
        await self.session.flush()  # 获取 provider.id

        if models:
            for m in models:
                model = CustomProviderModel(provider_id=provider.id, **m)
                self.session.add(model)
            await self.session.flush()

        return provider

    async def get_provider(self, provider_id: int) -> CustomProvider | None:
        stmt = select(CustomProvider).where(CustomProvider.id == provider_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_providers(self) -> list[CustomProvider]:
        stmt = select(CustomProvider).order_by(CustomProvider.id)
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def update_provider(self, provider_id: int, **kwargs) -> CustomProvider | None:
        """更新供应商字段。返回更新后的对象，若不存在返回 None。"""
        provider = await self.get_provider(provider_id)
        if provider is None:
            return None
        for key, value in kwargs.items():
            setattr(provider, key, value)
        return provider

    async def delete_provider(self, provider_id: int) -> None:
        """删除供应商及其所有模型。

        显式删除模型而非依赖 FK CASCADE：级联只在开启 foreign_keys pragma 的连接上生效，
        应用引擎虽统一开启（engine.py），脚本或外部工具直连时不保证；显式删除不依赖连接配置。
        """
        await self.session.execute(delete(CustomProviderModel).where(CustomProviderModel.provider_id == provider_id))
        await self.session.execute(delete(CustomProvider).where(CustomProvider.id == provider_id))
        await self.session.flush()

    # ── Model management ──────────────────────────────────────────

    async def list_models(self, provider_id: int) -> list[CustomProviderModel]:
        stmt = (
            select(CustomProviderModel)
            .where(CustomProviderModel.provider_id == provider_id)
            .order_by(CustomProviderModel.id)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def replace_models(self, provider_id: int, models: list[dict]) -> list[CustomProviderModel]:
        """删除旧模型，插入新列表。返回新创建的模型。"""
        await self.session.execute(delete(CustomProviderModel).where(CustomProviderModel.provider_id == provider_id))
        new_models = []
        for m in models:
            model = CustomProviderModel(provider_id=provider_id, **m)
            self.session.add(model)
            new_models.append(model)
        await self.session.flush()
        return new_models

    async def delete_model(self, model_id: int) -> None:
        """删除单个模型。"""
        await self.session.execute(delete(CustomProviderModel).where(CustomProviderModel.id == model_id))
        await self.session.flush()

    async def list_all_enabled_models(self) -> list[CustomProviderModel]:
        """跨所有供应商获取全部已启用模型。"""
        stmt = (
            select(CustomProviderModel)
            .where(CustomProviderModel.is_enabled == True)  # noqa: E712
            .order_by(CustomProviderModel.provider_id, CustomProviderModel.id)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def list_providers_with_models(self) -> list[tuple[CustomProvider, list[CustomProviderModel]]]:
        """获取所有供应商及其模型，仅 2 次查询。"""
        providers = await self.list_providers()
        if not providers:
            return []
        provider_ids = [p.id for p in providers]
        stmt = (
            select(CustomProviderModel)
            .where(CustomProviderModel.provider_id.in_(provider_ids))
            .order_by(CustomProviderModel.provider_id, CustomProviderModel.id)
        )
        result = await self.session.execute(stmt)
        all_models = list(result.scalars())

        models_by_provider: dict[int, list[CustomProviderModel]] = {p.id: [] for p in providers}
        for m in all_models:
            models_by_provider.setdefault(m.provider_id, []).append(m)
        return [(p, models_by_provider.get(p.id, [])) for p in providers]

    async def list_enabled_models_by_media_type(self, media_type: str) -> list[CustomProviderModel]:
        """跨所有供应商获取指定媒体类型的已启用模型。

        媒体类型按 :func:`_media_type_endpoint_filter` 判：内置键查表，``ce-`` 行读自己那一行的
        镜像列——一份 ComfyUI workflow 产图还是产视频从键上推不出来。
        """
        stmt = (
            select(CustomProviderModel)
            .where(
                _media_type_endpoint_filter(media_type),
                CustomProviderModel.is_enabled == True,  # noqa: E712
            )
            .order_by(CustomProviderModel.id)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def get_model_by_ids(self, provider_id: int, model_id: str) -> CustomProviderModel | None:
        """根据供应商 ID 和模型 ID 获取模型。"""
        stmt = select(CustomProviderModel).where(
            CustomProviderModel.provider_id == provider_id,
            CustomProviderModel.model_id == model_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def resolve_price(self, provider: str, model: str) -> CustomProviderPrice:
        """解析自定义供应商的声明价格，供记账与预估两侧共用，杜绝同源复刻的口径漂移。

        非自定义供应商 / 畸形 provider id / 查询异常 / 查无模型均降级为无价（三字段 ``None``），
        绝不抛错——调用方以此驱动 ``calculate_cost`` 的 ``custom_price_*``，缺价时费用计为 0。

        刻意不做 enabled / media_type 校验（区别于 ``lib/custom_provider/loader.load_custom_backend``
        的模型解析回退）：记账须按实际调用的那个模型的声明价计费，与该模型当前是否启用无关——
        在此过滤 ``is_enabled`` 会让对"事后被停用模型"的历史调用丢价、错计为 0；预估侧同理，
        停用模型应回落到执行期实际使用的默认模型价，而非 0。enabled / media_type 回退属模型解析层
        （loader）职责、在价格取数上游发生，两侧共用同一"按声明取价、不过滤"口径以保持一致。
        """
        if not is_custom_provider(provider):
            return _NO_PRICE
        try:
            # SAVEPOINT 隔离：查询异常只回滚到此处，不污染调用方（如 usage_repo._settle）
            # 随后在同一 session 上执行的结算 UPDATE/commit。
            async with self.session.begin_nested():
                price_model = await self.get_model_by_ids(parse_provider_id(provider), model or "")
        except Exception:
            logger.debug("自定义供应商价格查询失败 provider=%s model=%s", provider, model, exc_info=True)
            return _NO_PRICE
        if price_model is None:
            return _NO_PRICE
        return CustomProviderPrice(price_model.price_input, price_model.price_output, price_model.currency)

    async def list_default_models(self, provider_id: int, media_type: str) -> list[CustomProviderModel]:
        """指定供应商 + 媒体类型的全部默认已启用模型，按 id 升序。

        媒体类型的判法同 :meth:`list_enabled_models_by_media_type`。

        返回列表而非单行：image 的默认按任务类型桶分槽，保存期的唯一性检查按 image 能力集判互斥
        （t2i 与 i2i 各设一个默认是放行的），同一媒体类型下因此可以有多行同时是默认。按桶挑出
        唯一那一行由 ``lib.custom_provider.default_models`` 负责，取数层不做选择。
        """
        stmt = (
            select(CustomProviderModel)
            .where(
                CustomProviderModel.provider_id == provider_id,
                _media_type_endpoint_filter(media_type),
                CustomProviderModel.is_default == True,  # noqa: E712
                CustomProviderModel.is_enabled == True,  # noqa: E712
            )
            .order_by(CustomProviderModel.id)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())
