"""Agent Anthropic 凭证 Repository。"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select, update

from lib.agent.credential_tiers import tier_binding_for
from lib.config.repository import SystemSettingRepository
from lib.db.base import DEFAULT_USER_ID
from lib.db.models.agent_credential import AgentAnthropicCredential
from lib.db.repositories.base import BaseRepository


@dataclass(frozen=True)
class AgentModelPricing:
    """Agent 凭证上一个模型名的计价依据：模型所在档位的条目绑定（「模型厂商/官方模型名」）与 Agent 凭证倍率。"""

    entry_binding: str | None = None
    multiplier: float = 1.0


class AgentCredentialRepository(BaseRepository):
    """凭证 CRUD + active 互斥切换。

    NOTE: 调用方需在合适的边界 commit。本类只 flush，不 commit。
    """

    async def create(
        self,
        *,
        preset_id: str,
        display_name: str,
        base_url: str,
        api_key: str,
        model: str | None = None,
        haiku_model: str | None = None,
        sonnet_model: str | None = None,
        opus_model: str | None = None,
        subagent_model: str | None = None,
        multiplier: float = 1.0,
        user_id: str = DEFAULT_USER_ID,
    ) -> AgentAnthropicCredential:
        cred = AgentAnthropicCredential(
            user_id=user_id,
            preset_id=preset_id,
            display_name=display_name,
            base_url=base_url,
            api_key=api_key,
            model=model,
            haiku_model=haiku_model,
            sonnet_model=sonnet_model,
            opus_model=opus_model,
            subagent_model=subagent_model,
            multiplier=multiplier,
            is_active=False,
        )
        self.session.add(cred)
        await self.session.flush()
        return cred

    async def get(self, cred_id: int) -> AgentAnthropicCredential | None:
        stmt = select(AgentAnthropicCredential).where(AgentAnthropicCredential.id == cred_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_user(self, user_id: str = DEFAULT_USER_ID) -> list[AgentAnthropicCredential]:
        stmt = (
            select(AgentAnthropicCredential)
            .where(AgentAnthropicCredential.user_id == user_id)
            .order_by(AgentAnthropicCredential.id)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def get_active(self, user_id: str = DEFAULT_USER_ID) -> AgentAnthropicCredential | None:
        stmt = select(AgentAnthropicCredential).where(
            AgentAnthropicCredential.user_id == user_id,
            AgentAnthropicCredential.is_active.is_(True),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def resolve_pricing(self, cred_id: int | None, model: str) -> AgentModelPricing:
        """记账时 Agent 凭证上模型名 ``model`` 的计价依据（docs/adr/0102）。

        ``cred_id`` 是会话连接时注入环境变量的那条凭证，不是记账时的生效凭证；没有凭证（回退到系统设置的
        环境变量）或凭证已删除时取默认值。倍率取凭证倍率；条目绑定取 ``model`` 所在档位的绑定，规则见
        :func:`lib.agent.credential_tiers.tier_binding_for`，没有时为空，模型名按名字对应条目。
        """
        if cred_id is None:
            return AgentModelPricing()
        cred = await self.get(cred_id)
        if cred is None:
            return AgentModelPricing()
        binding = None
        if cred.tier_bindings:
            settings = await SystemSettingRepository(self.session).get_all()
            binding = tier_binding_for(cred, settings, model)
        return AgentModelPricing(entry_binding=binding, multiplier=cred.multiplier)

    async def update(self, cred_id: int, **kwargs) -> AgentAnthropicCredential | None:
        cred = await self.get(cred_id)
        if cred is None:
            return None
        for k, v in kwargs.items():
            setattr(cred, k, v)
        await self.session.flush()
        return cred

    async def set_active(self, cred_id: int, user_id: str = DEFAULT_USER_ID) -> None:
        """互斥切 active：先把同 user 全置 False，再把目标置 True。

        Raises:
            ValueError: cred_id 不存在或不属于该 user
        """
        cred = await self.get(cred_id)
        if cred is None or cred.user_id != user_id:
            raise ValueError(f"credential id={cred_id} not found")
        # SQLite 的 partial unique index 在同事务内中间态可能违反，所以先全清再设
        await self.session.execute(
            update(AgentAnthropicCredential)
            .where(
                AgentAnthropicCredential.user_id == user_id,
                AgentAnthropicCredential.is_active.is_(True),
            )
            .values(is_active=False)
        )
        await self.session.flush()
        cred.is_active = True
        await self.session.flush()

    async def delete(self, cred_id: int) -> bool:
        """删除非 active 凭证。

        Returns:
            True: 删除成功；False: 凭证不存在。

        Raises:
            ValueError: 试图删除当前 active 凭证。
        """
        cred = await self.get(cred_id)
        if cred is None:
            return False
        if cred.is_active:
            raise ValueError("cannot delete active credential; activate another first")
        await self.session.execute(delete(AgentAnthropicCredential).where(AgentAnthropicCredential.id == cred_id))
        await self.session.flush()
        return True
