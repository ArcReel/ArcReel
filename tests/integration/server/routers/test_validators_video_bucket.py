"""Video-entry ``require_video_bucket_capability`` preflight:

- Missing required capabilities/dangling references → ``BadRequestError`` with error-catalog keys/parameters;
- Unlisted referenced models → unchanged ``ModelNotInCatalogError``, app handlers return 400;
- Other resolution failures, no providers configured → admit, never escalate noncapability failures into submission rejection.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.catalog.errors import ModelNotInCatalogError
from lib.config.service import ConfigService
from lib.infra.api_errors import BadRequestError
from server.routers._validators import require_video_bucket_capability
from tests.fakes import bind_session_factory


async def _set_default_video_backend(factory: async_sessionmaker[AsyncSession], value: str) -> None:
    async with factory() as session:
        await ConfigService(session).set_setting("default_video_backend", value)
        await session.commit()


class TestRequireVideoBucketCapability:
    async def test_missing_r2v_capability_maps_to_bad_request(self, db_factory, monkeypatch):
        await _set_default_video_backend(db_factory, "minimax/MiniMax-Hailuo-2.3")
        bind_session_factory(monkeypatch, db_factory)

        with pytest.raises(BadRequestError) as exc_info:
            await require_video_bucket_capability({}, "r2v")

        assert exc_info.value.key == "video_capability_missing_r2v"
        assert exc_info.value.params == {"provider": "minimax", "model": "MiniMax-Hailuo-2.3"}

    async def test_project_r2v_bucket_overrides_incapable_default(self, db_factory, monkeypatch):
        """Configured r2v buckets use bucket models; incapable defaults no longer block reference video."""
        await _set_default_video_backend(db_factory, "minimax/MiniMax-Hailuo-2.3")
        bind_session_factory(monkeypatch, db_factory)

        # Admitted preflight silently returns None.
        assert (
            await require_video_bucket_capability({"video_provider_r2v": "dashscope/happyhorse-1.1-r2v"}, "r2v") is None
        )

    async def test_no_provider_configured_passes_through(self, db_factory, monkeypatch):
        """No providers/failed automatic inference admits enqueue; workers expose failures in task panels."""
        bind_session_factory(monkeypatch, db_factory)

        assert await require_video_bucket_capability({}, "i2v") is None

    async def test_bare_builtin_provider_without_video_model_is_rejected(self, db_factory, monkeypatch):
        """Bare built-in project providers without default catalog video models reject at submission with reselection guidance, never enqueue."""
        bind_session_factory(monkeypatch, db_factory)

        with pytest.raises(ModelNotInCatalogError) as exc_info:
            await require_video_bucket_capability({"video_backend": "openai"}, "i2v")

        assert (exc_info.value.provider_id, exc_info.value.media_type) == ("openai", "video")
