"""Custom provider repository."""

from __future__ import annotations

from sqlalchemy import delete, select

from lib.db.models.custom_provider import CustomProvider, CustomProviderModel
from lib.db.repositories.base import BaseRepository


class CustomProviderRepository(BaseRepository):
    """自定义供应商 + 模型 CRUD。"""

    # ── Provider CRUD ──────────────────────────────────────────────

    async def create_provider(
        self,
        display_name: str,
        api_format: str,
        base_url: str,
        api_key: str,
        models: list[dict] | None = None,
    ) -> CustomProvider:
        """创建供应商，可选同时创建模型列表。"""
        provider = CustomProvider(
            display_name=display_name,
            api_format=api_format,
            base_url=base_url,
            api_key=api_key,
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
        """删除供应商及其所有模型。"""
        # 先删模型
        await self.session.execute(
            delete(CustomProviderModel).where(CustomProviderModel.provider_id == provider_id)
        )
        # 再删供应商
        await self.session.execute(
            delete(CustomProvider).where(CustomProvider.id == provider_id)
        )
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
        await self.session.execute(
            delete(CustomProviderModel).where(CustomProviderModel.provider_id == provider_id)
        )
        new_models = []
        for m in models:
            model = CustomProviderModel(provider_id=provider_id, **m)
            self.session.add(model)
            new_models.append(model)
        await self.session.flush()
        return new_models

    async def update_model(self, model_id: int, **kwargs) -> CustomProviderModel | None:
        """更新模型字段。返回更新后的对象，若不存在返回 None。"""
        stmt = select(CustomProviderModel).where(CustomProviderModel.id == model_id)
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            return None
        for key, value in kwargs.items():
            setattr(model, key, value)
        return model

    async def delete_model(self, model_id: int) -> None:
        """删除单个模型。"""
        await self.session.execute(
            delete(CustomProviderModel).where(CustomProviderModel.id == model_id)
        )
        await self.session.flush()

    async def list_enabled_models_by_media_type(self, media_type: str) -> list[CustomProviderModel]:
        """跨所有供应商获取指定媒体类型的已启用模型。"""
        stmt = (
            select(CustomProviderModel)
            .where(
                CustomProviderModel.media_type == media_type,
                CustomProviderModel.is_enabled == True,  # noqa: E712
            )
            .order_by(CustomProviderModel.id)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def get_default_model(self, provider_id: int, media_type: str) -> CustomProviderModel | None:
        """获取指定供应商 + 媒体类型的默认已启用模型。"""
        stmt = select(CustomProviderModel).where(
            CustomProviderModel.provider_id == provider_id,
            CustomProviderModel.media_type == media_type,
            CustomProviderModel.is_default == True,  # noqa: E712
            CustomProviderModel.is_enabled == True,  # noqa: E712
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
