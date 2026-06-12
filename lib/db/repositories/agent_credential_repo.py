"""Agent AI Provider credential Repository."""

from __future__ import annotations

from sqlalchemy import delete, select, update

from lib.db.base import DEFAULT_USER_ID
from lib.db.models.agent_credential import AgentAnthropicCredential
from lib.db.repositories.base import BaseRepository


class AgentCredentialRepository(BaseRepository):
    """Credential CRUD + priority-based ordering + active toggle.

    NOTE: Caller must commit at appropriate boundary. This class only flushes.
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
        discovery_format: str | None = None,
        user_id: str = DEFAULT_USER_ID,
    ) -> AgentAnthropicCredential:
        # Auto-assign priority: new credential gets lowest priority (highest number)
        max_priority = await self._get_max_priority(user_id)
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
            discovery_format=discovery_format,
            is_active=False,
            priority=max_priority + 1,
        )
        self.session.add(cred)
        await self.session.flush()
        return cred

    async def get(self, cred_id: int) -> AgentAnthropicCredential | None:
        stmt = select(AgentAnthropicCredential).where(AgentAnthropicCredential.id == cred_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_user(self, user_id: str = DEFAULT_USER_ID) -> list[AgentAnthropicCredential]:
        """List credentials ordered by priority (lower = higher priority)."""
        stmt = (
            select(AgentAnthropicCredential)
            .where(AgentAnthropicCredential.user_id == user_id)
            .order_by(AgentAnthropicCredential.priority, AgentAnthropicCredential.id)
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

    async def get_ordered(self, user_id: str = DEFAULT_USER_ID) -> list[AgentAnthropicCredential]:
        """Get all credentials ordered by priority for fallback logic."""
        return await self.list_for_user(user_id)

    async def update(self, cred_id: int, **kwargs) -> AgentAnthropicCredential | None:
        cred = await self.get(cred_id)
        if cred is None:
            return None
        for k, v in kwargs.items():
            setattr(cred, k, v)
        await self.session.flush()
        return cred

    async def set_active(self, cred_id: int, user_id: str = DEFAULT_USER_ID) -> None:
        """Toggle active: clear all active for user, then set target.

        Raises:
            ValueError: cred_id not found or doesn't belong to user
        """
        cred = await self.get(cred_id)
        if cred is None or cred.user_id != user_id:
            raise ValueError(f"credential id={cred_id} not found")
        # SQLite partial unique index may violate in same transaction, so clear first
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

    async def reorder(self, priority_list: list[dict[str, int]], user_id: str = DEFAULT_USER_ID) -> None:
        """Reorder credentials by priority.

        Args:
            priority_list: List of {"id": cred_id, "priority": new_priority}
        """
        for item in priority_list:
            await self.session.execute(
                update(AgentAnthropicCredential)
                .where(
                    AgentAnthropicCredential.id == item["id"],
                    AgentAnthropicCredential.user_id == user_id,
                )
                .values(priority=item["priority"])
            )
        await self.session.flush()

    async def delete(self, cred_id: int) -> None:
        """Delete non-active credential. Raises ValueError for active."""
        cred = await self.get(cred_id)
        if cred is None:
            return
        if cred.is_active:
            raise ValueError("cannot delete active credential; activate another first")
        await self.session.execute(delete(AgentAnthropicCredential).where(AgentAnthropicCredential.id == cred_id))
        await self.session.flush()

    async def _get_max_priority(self, user_id: str = DEFAULT_USER_ID) -> int:
        """Get the maximum priority value for a user's credentials."""
        stmt = select(AgentAnthropicCredential.priority).where(
            AgentAnthropicCredential.user_id == user_id
        ).order_by(AgentAnthropicCredential.priority.desc()).limit(1)
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        return row if row is not None else -1
