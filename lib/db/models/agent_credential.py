"""Agent Anthropic credential ORM.

A partial unique index guarantees at most one is_active=True row per user
(the same pattern as ProviderCredential).
"""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Float, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import DEFAULT_USER_ID, Base, TimestampMixin


class AgentAnthropicCredential(TimestampMixin, Base):
    """User-saved Anthropic credentials; switch the active set in one UI action."""

    __tablename__ = "agent_anthropic_credentials"
    __table_args__ = (
        Index("ix_agent_credential_user", "user_id"),
        # At most one is_active=True row per user
        Index(
            "uq_agent_credential_one_active_per_user",
            "user_id",
            unique=True,
            sqlite_where=text("is_active = 1"),
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, default=DEFAULT_USER_ID)
    preset_id: Mapped[str] = mapped_column(String(64), nullable=False)  # "deepseek" | "__custom__" | ...
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    api_key: Mapped[str] = mapped_column(Text, nullable=False)  # Plaintext; read APIs redact with mask_secret
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    haiku_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sonnet_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    opus_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    subagent_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Agent credential multiplier (docs/adr/0100,0102): apply once to each call reference cost using this credential; zero records zero.
    multiplier: Mapped[float] = mapped_column(Float, nullable=False, default=1.0, server_default=text("1"))
    # Tier-to-entry bindings (model vendor/official model name); tiers are in lib.agent.credential_tiers.TIERS.
    # NULL or an absent tier means live model-name lookup; a binding missing from the catalog also falls back to live lookup.
    tier_bindings: Mapped[dict[str, str] | None] = mapped_column(JSON, nullable=True)
