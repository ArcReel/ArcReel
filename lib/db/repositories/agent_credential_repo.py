"""Agent Anthropic 凭证 Repository。"""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import delete, select, update

from lib.agent.credential_tiers import Tier
from lib.db.base import DEFAULT_USER_ID
from lib.db.models.agent_credential import AgentAnthropicCredential
from lib.db.repositories.base import BaseRepository


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
        tier_bindings: Mapping[Tier, str] | None = None,
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
            tier_bindings=dict(tier_bindings) if tier_bindings else None,
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

    async def update(self, cred_id: int, **kwargs) -> AgentAnthropicCredential | None:
        cred = await self.get(cred_id)
        if cred is None:
            return None
        for k, v in kwargs.items():
            setattr(cred, k, v)
        await self.session.flush()
        return cred

    async def merge_tier_bindings(
        self, cred_id: int, changes: Mapping[Tier, str | None]
    ) -> AgentAnthropicCredential | None:
        """按档位合并条目绑定：值为 ``None`` 的档位解除绑定，其余档位保持不变。凭证不存在时返回 ``None``。"""
        cred = await self.get(cred_id)
        if cred is None:
            return None
        merged = dict(cred.tier_bindings or {})
        for tier, ref in changes.items():
            if ref is None:
                merged.pop(tier, None)
            else:
                merged[tier] = ref
        # JSON 列不跟踪原地修改，整体换成新字典；全部解除时存 NULL，与「没有绑定」同一形状。
        cred.tier_bindings = merged or None
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
