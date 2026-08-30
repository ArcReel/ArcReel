"""自定义 backend DB 装载（lib.custom_provider.loader）单测：内存 SQLite + 真 CustomProviderRepository。

查 provider、校验 model（存在 / 启用 / endpoint 推算 media_type 相符）、失效回退默认、委托现成
create_custom_backend（ENDPOINT_REGISTRY 不改）。镜像 test_custom_provider_repo.py 的内存 DB 范式，不 mock repo。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from lib.custom_provider import make_provider_id
from lib.custom_provider.backends import CustomImageBackend
from lib.custom_provider.capabilities import system_video_capabilities
from lib.custom_provider.loader import load_custom_backend
from lib.db.repositories.custom_provider_repo import CustomProviderRepository


async def _seed(db_session, *, models: list[dict]) -> str:
    repo = CustomProviderRepository(db_session)
    # display_name 列 NOT NULL：缺省补 model_id 作显示名
    for m in models:
        m.setdefault("display_name", m["model_id"])
    provider = await repo.create_provider(
        display_name="Relay",
        discovery_format="openai",
        base_url="https://relay.test/v1",
        api_key="sk-relay",
        models=models,
    )
    await db_session.commit()
    return make_provider_id(provider.id)


class TestLoadCustomBackend:
    @patch("lib.custom_provider.endpoints.OpenAIImageBackend")
    async def test_resolves_named_model_and_delegates(self, mock_cls, db_session):
        pid = await _seed(
            db_session,
            models=[{"model_id": "dall-e-3", "endpoint": "openai-images", "is_enabled": True}],
        )
        result = await load_custom_backend(session=db_session, provider_id=pid, model_id="dall-e-3", media_type="image")
        assert isinstance(result, CustomImageBackend)
        assert result.model == "dall-e-3"
        mock_cls.assert_called_once_with(api_key="sk-relay", base_url="https://relay.test/v1", model="dall-e-3")

    @patch("lib.custom_provider.endpoints.OpenAIImageBackend")
    async def test_falls_back_to_default_when_model_disabled(self, mock_cls, db_session):
        # 请求的 model 已禁用 → 回退到该 media_type 的默认启用 model
        pid = await _seed(
            db_session,
            models=[
                {"model_id": "disabled-m", "endpoint": "openai-images", "is_enabled": False},
                {"model_id": "active-m", "endpoint": "openai-images", "is_enabled": True, "is_default": True},
            ],
        )
        result = await load_custom_backend(
            session=db_session, provider_id=pid, model_id="disabled-m", media_type="image"
        )
        assert result.model == "active-m"

    async def test_provider_not_found_fails_loud(self, db_session):
        with pytest.raises(ValueError, match="不存在"):
            await load_custom_backend(
                session=db_session, provider_id=make_provider_id(999), model_id="x", media_type="image"
            )

    async def test_no_default_model_for_media_fails_loud(self, db_session):
        # 只有 image model，请求 video → 无默认 video model
        pid = await _seed(
            db_session,
            models=[{"model_id": "dall-e-3", "endpoint": "openai-images", "is_enabled": True}],
        )
        with pytest.raises(ValueError, match="没有默认"):
            await load_custom_backend(session=db_session, provider_id=pid, model_id=None, media_type="video")


class TestVideoCapabilityOverridesReachExecution:
    """DB 的 capability_overrides 必须在装载出的 backend 上生效——执行层门控读的就是这里。"""

    @staticmethod
    async def _load_video_backend(
        db_session, *, overrides: object | None, endpoint: str = "openai-video", model_id: str = "sora-2"
    ):
        pid = await _seed(
            db_session,
            models=[
                {
                    "model_id": model_id,
                    "endpoint": endpoint,
                    "is_enabled": True,
                    "is_default": True,
                    "capability_overrides": overrides,
                }
            ],
        )
        # 清 identity map，逼装载重新 SELECT：覆盖字典要真经过 JSON 编解码往返才算验到 DB 语义
        db_session.expunge_all()
        return await load_custom_backend(session=db_session, provider_id=pid, model_id=model_id, media_type="video")

    @patch("lib.custom_provider.endpoints.OpenAIVideoBackend", new=MagicMock())
    async def test_null_overrides_follow_system_judgement(self, db_session):
        backend = await self._load_video_backend(db_session, overrides=None)
        assert backend.video_capabilities == system_video_capabilities(endpoint="openai-video", model_id="sora-2")

    @patch("lib.custom_provider.endpoints.ArkVideoBackend", new=MagicMock())
    async def test_override_forces_capability_on(self, db_session):
        # 系统判定 last_frame=False；覆盖强制开启后执行层看到 True，且不再转发被包装 backend
        # endpoint 须支持尾帧（end_image_capable）覆盖才会生效，openai-video 不满足此前提
        backend = await self._load_video_backend(
            db_session, overrides={"last_frame": True}, endpoint="ark-seedance", model_id="seedance-1-pro"
        )
        assert backend.video_capabilities.last_frame is True
        assert backend.video_capabilities.max_reference_images == 0

    @patch("lib.custom_provider.endpoints.OpenAIVideoBackend", new=MagicMock())
    async def test_override_forces_capability_off(self, db_session):
        backend = await self._load_video_backend(db_session, overrides={"max_reference_images": 0})
        assert backend.video_capabilities.max_reference_images == 0
        assert backend.video_capabilities.first_frame is True

    @patch("lib.custom_provider.endpoints.ArkVideoBackend", new=MagicMock())
    async def test_unknown_key_in_stored_overrides_does_not_break_loading(self, db_session):
        # 存量行可能带已下线的键，装载不得失败，该维度按系统判定走
        backend = await self._load_video_backend(
            db_session,
            overrides={"retired_dimension": True, "last_frame": True},
            endpoint="ark-seedance",
            model_id="seedance-1-pro",
        )
        assert backend.video_capabilities.last_frame is True

    @patch("lib.custom_provider.endpoints.OpenAIImageBackend", new=MagicMock())
    async def test_non_video_backend_untouched_by_overrides(self, db_session):
        pid = await _seed(
            db_session,
            models=[
                {
                    "model_id": "dall-e-3",
                    "endpoint": "openai-images",
                    "is_enabled": True,
                    "capability_overrides": {"last_frame": True},
                }
            ],
        )
        result = await load_custom_backend(session=db_session, provider_id=pid, model_id="dall-e-3", media_type="image")
        assert isinstance(result, CustomImageBackend)
