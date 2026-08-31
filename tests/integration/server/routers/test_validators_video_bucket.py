"""视频生成入口预检 ``require_video_bucket_capability`` 的行为：

- 解析结果缺桶所需能力 / 悬空引用 → ``BadRequestError``（携带 errors 目录 key 与参数）；
- 其余解析失败（未配置任何供应商）→ 放行，不把非能力类失败升级为提交期拒绝。
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.api_errors import BadRequestError
from lib.config.service import ConfigService
from server.routers._validators import require_video_bucket_capability


async def _set_default_video_backend(factory: async_sessionmaker[AsyncSession], value: str) -> None:
    async with factory() as session:
        await ConfigService(session).set_setting("default_video_backend", value)
        await session.commit()


class TestRequireVideoBucketCapability:
    async def test_missing_r2v_capability_maps_to_bad_request(self, db_factory, monkeypatch):
        await _set_default_video_backend(db_factory, "minimax/MiniMax-Hailuo-2.3")
        monkeypatch.setattr("lib.db.async_session_factory", db_factory)

        with pytest.raises(BadRequestError) as exc_info:
            await require_video_bucket_capability({}, "r2v")

        assert exc_info.value.key == "video_capability_missing_r2v"
        assert exc_info.value.params == {"provider": "minimax", "model": "MiniMax-Hailuo-2.3"}

    async def test_project_r2v_bucket_overrides_incapable_default(self, db_factory, monkeypatch):
        """配置 r2v 桶后参考生视频改用桶内模型：默认模型缺参考图能力也不再拦截。"""
        await _set_default_video_backend(db_factory, "minimax/MiniMax-Hailuo-2.3")
        monkeypatch.setattr("lib.db.async_session_factory", db_factory)

        # 预检放行即静默返回 None
        assert await require_video_bucket_capability({"video_provider_r2v": "minimax/S2V-01"}, "r2v") is None

    async def test_no_provider_configured_passes_through(self, db_factory, monkeypatch):
        """未配置任何供应商（自动推断失败）→ 放行入队，由 worker 在任务面板暴露。"""
        monkeypatch.setattr("lib.db.async_session_factory", db_factory)

        assert await require_video_bucket_capability({}, "i2v") is None
