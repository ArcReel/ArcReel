"""User model for multi-user infrastructure."""

import sqlalchemy as sa
from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False, server_default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=sa.true())
