"""视频生成入口预检 ``require_video_bucket_capability`` 的行为：

- 解析结果缺桶所需能力 / 悬空引用 → ``BadRequestError``（携带 errors 目录 key 与参数）；
- 其余解析失败（未配置任何供应商）→ 放行，不把非能力类失败升级为提交期拒绝。
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.api_errors import BadRequestError
from lib.config.service import ConfigService
from lib.db.base import Base
from server.routers._validators import require_video_bucket_capability


async def _make_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False), engine


@pytest.mark.integration
class TestRequireVideoBucketCapability:
    async def test_missing_r2v_capability_maps_to_bad_request(self, monkeypatch):
        factory, engine = await _make_factory()
        try:
            async with factory() as session:
                await ConfigService(session).set_setting("default_video_backend", "minimax/MiniMax-Hailuo-2.3")
                await session.commit()
            monkeypatch.setattr("lib.db.async_session_factory", factory)
            with pytest.raises(BadRequestError) as exc_info:
                await require_video_bucket_capability({}, "r2v")
        finally:
            await engine.dispose()
        assert exc_info.value.key == "video_capability_missing_r2v"
        assert exc_info.value.params == {"provider": "minimax", "model": "MiniMax-Hailuo-2.3"}

    async def test_project_r2v_bucket_overrides_incapable_default(self, monkeypatch):
        """配置 r2v 桶后参考生视频改用桶内模型：默认模型缺参考图能力也不再拦截。"""
        factory, engine = await _make_factory()
        try:
            async with factory() as session:
                await ConfigService(session).set_setting("default_video_backend", "minimax/MiniMax-Hailuo-2.3")
                await session.commit()
            monkeypatch.setattr("lib.db.async_session_factory", factory)
            await require_video_bucket_capability({"video_provider_r2v": "minimax/S2V-01"}, "r2v")
        finally:
            await engine.dispose()

    async def test_no_provider_configured_passes_through(self, monkeypatch):
        """未配置任何供应商（自动推断失败）→ 放行入队，由 worker 在任务面板暴露。"""
        factory, engine = await _make_factory()
        try:
            monkeypatch.setattr("lib.db.async_session_factory", factory)
            await require_video_bucket_capability({}, "i2v")
        finally:
            await engine.dispose()
