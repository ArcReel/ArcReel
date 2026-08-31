"""能力覆盖的 API 面与展示层同源。

覆盖三件事：配置 API 读写覆盖（含白名单外键剔除、4xx 与整体替换语义）、resolver 返回生效能力、
展示层与执行层出自同一个合成函数。API 侧沿用 test_custom_providers_api.py 的内存 SQLite +
TestClient 范式，同源侧沿用 test_custom_provider_loader.py 的真 repo 装载范式。
"""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.custom_provider import make_provider_id
from lib.custom_provider.capabilities import synthesize_video_capabilities, system_video_capabilities
from lib.custom_provider.loader import load_custom_backend
from lib.db import get_async_session
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import custom_providers
from tests.auth_deps import AUTH_DEPENDENCIES

# 系统判定 last_frame=False、max_reference_images=1 —— 覆盖前后差异可断言
VIDEO_ENDPOINT = "openai-video"
VIDEO_MODEL = "sora-2"

# openai-video 的 delegate 不序列化 end_image（见 _check_capability_overrides 的
# end_image_capable 门槛），last_frame 覆盖为 True 的用例须换一个真正支持尾帧的 endpoint。
# ark-seedance 的 1.0 pro fast 系统判定 last_frame=False（docs/api-docs/providers/ark.md
# 能力表标 "-"），可类比断言覆盖前后差异；不用 seedance-2 系列是因为那两系模型系统判定
# 本身就是 True，无法体现覆盖生效前后的差异。
LAST_FRAME_ENDPOINT = "ark-seedance"
LAST_FRAME_MODEL = "doubao-seedance-1-0-pro-fast-251015"


@pytest.fixture
async def app_session_factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
def app(app_session_factory) -> FastAPI:
    _app = FastAPI()

    async def _override_session():
        async with app_session_factory() as db_session:
            yield db_session

    _app.dependency_overrides[get_async_session] = _override_session
    _app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="test", sub="test", role="admin")
    _app.include_router(custom_providers.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    register_error_handlers(_app)
    return _app


@pytest.fixture
def capability_client(app) -> Generator[TestClient, None, None]:
    with TestClient(app) as c:
        yield c


def _post_provider(capability_client: TestClient, models: list[dict]):
    return capability_client.post(
        "/api/v1/custom-providers",
        json={
            "display_name": "Relay",
            "discovery_format": "openai",
            "base_url": "https://relay.test/v1",
            "api_key": "sk-relay",
            "models": models,
        },
    )


def _create_provider(capability_client: TestClient, models: list[dict]) -> int:
    resp = _post_provider(capability_client, models)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _seed_provider_with_raw_models(app_session_factory, models: list[dict]) -> int:
    """绕过 API 直接落库：模拟手工改库产生的、界面无从产生的覆盖字典。"""
    async with app_session_factory() as db_session:
        repo = CustomProviderRepository(db_session)
        provider = await repo.create_provider(
            display_name="Relay",
            discovery_format="openai",
            base_url="https://relay.test/v1",
            api_key="sk-relay",
            models=models,
        )
        await db_session.commit()
        return provider.id


def _video_model(**overrides) -> dict:
    model = {
        "model_id": VIDEO_MODEL,
        "display_name": "Sora 2",
        "endpoint": VIDEO_ENDPOINT,
        "is_default": True,
        "is_enabled": True,
    }
    model.update(overrides)
    return model


class TestModelListExposesCapabilities:
    """models 列表同时给出系统判定与用户覆盖，设置页平凡合并即可展示。"""

    def test_video_model_reports_system_capabilities(self, capability_client: TestClient):
        pid = _create_provider(capability_client, [_video_model()])

        models = capability_client.get(f"/api/v1/custom-providers/{pid}").json()["models"]
        assert models[0]["system_capabilities"] == {
            "text_to_video": True,
            "first_frame": True,
            "last_frame": False,
            "max_reference_images": 1,
            "reference_audio_mode": "none",
            "max_reference_audio_count": 0,
            "max_reference_audio_total_seconds": None,
            "reference_audio_per_image": False,
            "max_prompt_chars": None,
            "first_frame_ratio_adaptive_only": False,
        }
        assert models[0]["capability_overrides"] is None

    def test_system_capabilities_matches_synthesis_source(self, capability_client: TestClient):
        """判定值不是 API 里另写一份，而是合成函数的系统判定分支。"""
        pid = _create_provider(capability_client, [_video_model()])

        models = capability_client.get(f"/api/v1/custom-providers/{pid}").json()["models"]
        expected = system_video_capabilities(endpoint=VIDEO_ENDPOINT, model_id=VIDEO_MODEL)
        assert models[0]["system_capabilities"] == {
            "text_to_video": expected.text_to_video,
            "first_frame": expected.first_frame,
            "last_frame": expected.last_frame,
            "max_reference_images": expected.max_reference_images,
            "reference_audio_mode": expected.reference_audio_mode.value,
            "max_reference_audio_count": expected.max_reference_audio_count,
            "max_reference_audio_total_seconds": expected.max_reference_audio_total_seconds,
            "reference_audio_per_image": expected.reference_audio_per_image,
            "max_prompt_chars": expected.max_prompt_chars,
            "first_frame_ratio_adaptive_only": expected.first_frame_ratio_adaptive_only,
        }

    def test_non_video_model_has_no_system_capabilities(self, capability_client: TestClient):
        pid = _create_provider(
            capability_client,
            [{"model_id": "dall-e-3", "display_name": "D3", "endpoint": "openai-images", "is_enabled": True}],
        )

        models = capability_client.get(f"/api/v1/custom-providers/{pid}").json()["models"]
        assert models[0]["system_capabilities"] is None

    def test_overrides_round_trip_through_create(self, capability_client: TestClient):
        pid = _create_provider(
            capability_client,
            [
                _video_model(
                    capability_overrides={"last_frame": True}, endpoint=LAST_FRAME_ENDPOINT, model_id=LAST_FRAME_MODEL
                )
            ],
        )

        models = capability_client.get(f"/api/v1/custom-providers/{pid}").json()["models"]
        assert models[0]["capability_overrides"] == {"last_frame": True}
        # 判定值不受覆盖影响：设置页要能同时显示"判定 False / 生效 True"
        assert models[0]["system_capabilities"]["last_frame"] is False

    async def test_stale_incompatible_override_filtered_from_response(
        self, capability_client: TestClient, app_session_factory
    ):
        """存量行 / 非 API 写入可能留下已不兼容的覆盖（如 openai-video 上的 last_frame=True，
        endpoint 不 end_image_capable）：写入侧挡不住这条已落库的数据，回显前须过滤，
        不能让界面呈现"覆盖已生效"而执行层其实静默忽略——原样回显还会让下次普通保存被
        写入校验拒为 422，堵住与该覆盖无关的编辑。"""
        pid = await _seed_provider_with_raw_models(
            app_session_factory,
            [
                {
                    "model_id": VIDEO_MODEL,
                    "display_name": "Sora 2",
                    "endpoint": VIDEO_ENDPOINT,
                    "is_enabled": True,
                    "is_default": True,
                    "capability_overrides": {"last_frame": True},
                }
            ],
        )

        models = capability_client.get(f"/api/v1/custom-providers/{pid}").json()["models"]
        assert models[0]["capability_overrides"] is None

    async def test_stale_incoherent_audio_override_filtered_from_response(
        self, capability_client: TestClient, app_session_factory
    ):
        """存量行的 direct ⊕ 上限 0：两维各自合法，故过得了逐键过滤，但执行层会降级到 none。

        原样回显有两个后果，与 last_frame 那条同源：界面显示"直传音色已生效"而生成其实不带
        音色输入；客户端把它随一次与音频无关的编辑原样回传，会撞上写入侧的同一条不变式，
        把整次保存拒成 422。
        """
        pid = await _seed_provider_with_raw_models(
            app_session_factory,
            [
                {
                    "model_id": LAST_FRAME_MODEL,
                    "display_name": "Seedance",
                    "endpoint": LAST_FRAME_ENDPOINT,
                    "is_enabled": True,
                    "is_default": True,
                    "capability_overrides": {"last_frame": True, "reference_audio_mode": "direct"},
                }
            ],
        )

        models = capability_client.get(f"/api/v1/custom-providers/{pid}").json()["models"]
        # 音频两维整组剔除，与音频无关的 last_frame 覆盖原样保留
        assert models[0]["capability_overrides"] == {"last_frame": True}

    async def test_corrupted_non_dict_override_does_not_500_response(
        self, capability_client: TestClient, app_session_factory
    ):
        """存量行 / 手工 SQL 可能让 JSON 列存了非字典值（字符串、列表等）：执行层的
        synthesize_video_capabilities 按容错设计忽略它，响应边界须同样容错，不能把原值
        直接塞进只接受 dict | None 的 ModelResponse 触发 Pydantic 校验错误，让整个列表/详情
        请求 500——用户也就无法进入设置页清理这条坏值。"""
        pid = await _seed_provider_with_raw_models(
            app_session_factory,
            [
                {
                    "model_id": VIDEO_MODEL,
                    "display_name": "Sora 2",
                    "endpoint": VIDEO_ENDPOINT,
                    "is_enabled": True,
                    "is_default": True,
                    "capability_overrides": "last_frame",
                }
            ],
        )

        resp = capability_client.get(f"/api/v1/custom-providers/{pid}")
        assert resp.status_code == 200
        assert resp.json()["models"][0]["capability_overrides"] is None

    async def test_retired_endpoint_override_does_not_500_response(
        self, capability_client: TestClient, app_session_factory
    ):
        """endpoint 已从注册表下线（升级移除）时，get_endpoint_spec 会抛 ValueError；响应边界
        过滤 last_frame 覆盖时须容错这条查表失败，不能让存量脏配置把列表/详情请求也炸成 500。"""
        pid = await _seed_provider_with_raw_models(
            app_session_factory,
            [
                {
                    "model_id": "retired-model",
                    "display_name": "Retired",
                    "endpoint": "no-such-retired-endpoint",
                    "is_enabled": True,
                    "is_default": True,
                    "capability_overrides": {"last_frame": True},
                }
            ],
        )

        resp = capability_client.get(f"/api/v1/custom-providers/{pid}")
        assert resp.status_code == 200
        assert resp.json()["models"][0]["capability_overrides"] is None

    async def test_unallowlisted_key_dropped_from_response(self, capability_client: TestClient, app_session_factory):
        """DB 遗留的白名单外键（如 first_frame，语义合法但未开放给用户覆盖）不该在回显中
        原样带出：否则界面呈现"覆盖已生效"，而写入侧一保存这条键就会被剔除——GET 与 PUT
        的键集合必须一致，调用方不该额外知道"回显可能含脏键但写回会被剔除"这条隐藏知识。"""
        pid = await _seed_provider_with_raw_models(
            app_session_factory,
            [
                {
                    "model_id": VIDEO_MODEL,
                    "display_name": "Sora 2",
                    "endpoint": VIDEO_ENDPOINT,
                    "is_enabled": True,
                    "is_default": True,
                    "capability_overrides": {"first_frame": False},
                }
            ],
        )

        models = capability_client.get(f"/api/v1/custom-providers/{pid}").json()["models"]
        assert models[0]["capability_overrides"] is None

    async def test_allowlisted_key_kept_when_mixed_with_unallowlisted(
        self, capability_client: TestClient, app_session_factory
    ):
        """混合了白名单内外键的存量行：白名单内键（last_frame）回显不变，白名单外键
        （first_frame）被剔除，回显集合与写入落库集合一致。"""
        pid = await _seed_provider_with_raw_models(
            app_session_factory,
            [
                {
                    "model_id": LAST_FRAME_MODEL,
                    "display_name": "Seedance",
                    "endpoint": LAST_FRAME_ENDPOINT,
                    "is_enabled": True,
                    "is_default": True,
                    "capability_overrides": {"last_frame": True, "first_frame": False},
                }
            ],
        )

        models = capability_client.get(f"/api/v1/custom-providers/{pid}").json()["models"]
        assert models[0]["capability_overrides"] == {"last_frame": True}


class TestSaveDropsUnlistedOverrides:
    """白名单外的键随保存被静默剔除——这是它们唯一的出口。

    这类键只能由手工改库产生：界面既不显示也没有入口能删，写入侧若为此报错，用户会被堵在一个
    自己无法处置的 422 上，连改显示名都保存不了。
    """

    def test_unknown_key_stripped_on_create(self, capability_client: TestClient):
        pid = _create_provider(capability_client, [_video_model(capability_overrides={"no_such_capability": True})])

        models = capability_client.get(f"/api/v1/custom-providers/{pid}").json()["models"]
        assert models[0]["capability_overrides"] is None

    def test_known_but_unallowlisted_key_stripped_on_create(self, capability_client: TestClient):
        """first_frame 是合法 VideoCapabilities 字段，但首批未开放覆盖。"""
        pid = _create_provider(capability_client, [_video_model(capability_overrides={"first_frame": False})])

        models = capability_client.get(f"/api/v1/custom-providers/{pid}").json()["models"]
        assert models[0]["capability_overrides"] is None

    async def test_legacy_key_stripped_while_editing_last_frame(
        self, capability_client: TestClient, app_session_factory
    ):
        """AC：含遗留键的行改动 last_frame 后保存不再 422，落库覆盖字典只剩白名单内的键。"""
        pid = await _seed_provider_with_raw_models(
            app_session_factory,
            [
                {
                    "model_id": LAST_FRAME_MODEL,
                    "display_name": "Seedance",
                    "endpoint": LAST_FRAME_ENDPOINT,
                    "is_enabled": True,
                    "is_default": True,
                    "capability_overrides": {"first_frame": False},
                }
            ],
        )

        resp = capability_client.put(
            f"/api/v1/custom-providers/{pid}/models",
            json={
                "models": [
                    _video_model(
                        endpoint=LAST_FRAME_ENDPOINT,
                        model_id=LAST_FRAME_MODEL,
                        # 表单把 GET 回显的遗留键原样带回，同时把 last_frame 切成「强制开」
                        capability_overrides={"first_frame": False, "last_frame": True},
                    )
                ]
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()[0]["capability_overrides"] == {"last_frame": True}

        models = capability_client.get(f"/api/v1/custom-providers/{pid}").json()["models"]
        assert models[0]["capability_overrides"] == {"last_frame": True}

    async def test_legacy_key_stripped_on_full_update(self, capability_client: TestClient, app_session_factory):
        """同上，覆盖 PUT /{provider_id}（原子更新 provider 元数据 + 模型列表）这条写入路径：
        只改 display_name 时，历史脏值既不该挡住保存，也不该被原样写回去。"""
        pid = await _seed_provider_with_raw_models(
            app_session_factory,
            [
                {
                    "model_id": VIDEO_MODEL,
                    "display_name": "Sora 2",
                    "endpoint": VIDEO_ENDPOINT,
                    "is_enabled": True,
                    "is_default": True,
                    "capability_overrides": {"first_frame": False},
                }
            ],
        )

        resp = capability_client.put(
            f"/api/v1/custom-providers/{pid}",
            json={
                "display_name": "Relay Renamed",
                "base_url": "https://relay.test/v1",
                "models": [_video_model(capability_overrides={"first_frame": False})],
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["display_name"] == "Relay Renamed"
        assert resp.json()["models"][0]["capability_overrides"] is None

    def test_patch_endpoint_absent_from_openapi(self, app):
        """单模型覆盖 PATCH 端点已删除：按 (provider_id, model_id, endpoint) 定位够不着新建
        provider / 新增模型行 / 改过 model_id 的行，覆盖编辑统一走表单的整表保存。"""
        assert not [path for path in app.openapi()["paths"] if "capability-overrides" in path]


class TestSaveValidatesOpenOverrides:
    """白名单内的键仍按 endpoint 与值类型从严校验，报错用户能在界面上处置。"""

    @pytest.mark.parametrize("bad_value", ["true", 1, 0, None, [], {}])
    def test_wrong_value_type_rejected_on_create(self, capability_client: TestClient, bad_value):
        resp = _post_provider(capability_client, [_video_model(capability_overrides={"last_frame": bad_value})])
        assert resp.status_code == 422

    def test_last_frame_rejected_on_endpoint_without_end_image_support(self, capability_client: TestClient):
        """openai-video 的 delegate 不下传 end_image：开启覆盖只会让 UI 宣称支持、执行层仍静默丢帧。"""
        resp = _post_provider(capability_client, [_video_model(capability_overrides={"last_frame": True})])
        assert resp.status_code == 422
        assert "last_frame" in resp.json()["detail"]

    def test_reference_audio_direct_rejected_on_endpoint_without_audio_support(self, capability_client: TestClient):
        """openai-video 的 delegate 不下传 reference_audio_files：开启覆盖只会让 UI 宣称支持
        音色输入，执行层照旧生成随机音色的视频。"""
        resp = _post_provider(
            capability_client, [_video_model(capability_overrides={"reference_audio_mode": "direct"})]
        )
        assert resp.status_code == 422
        assert "reference_audio_mode" in resp.json()["detail"]

    def test_reference_audio_none_accepted_on_endpoint_without_audio_support(self, capability_client: TestClient):
        """守卫只拦「宣称支持」的方向：关掉音色能力无需运输能力背书。"""
        pid = _create_provider(capability_client, [_video_model(capability_overrides={"reference_audio_mode": "none"})])

        models = capability_client.get(f"/api/v1/custom-providers/{pid}").json()["models"]
        assert models[0]["capability_overrides"] == {"reference_audio_mode": "none"}

    def test_reference_audio_direct_accepted_on_audio_capable_endpoint(self, capability_client: TestClient):
        resp = _post_provider(
            capability_client,
            [
                _video_model(
                    endpoint=LAST_FRAME_ENDPOINT,
                    model_id=LAST_FRAME_MODEL,
                    capability_overrides={"reference_audio_mode": "direct", "max_reference_audio_count": 2},
                )
            ],
        )
        assert resp.status_code == 201

    def test_direct_without_positive_count_rejected(self, capability_client: TestClient):
        """合并后不变式：宣称支持音色输入却限 0 段的组合在写入侧就拒。

        放行则执行期只能静默降级，用户拿到的是「最多支持 0 段」这种无从遵循的提示。
        该 endpoint 的系统判定上限为 0，只覆盖模式即凑出该组合。
        """
        resp = _post_provider(
            capability_client,
            [
                _video_model(
                    endpoint=LAST_FRAME_ENDPOINT,
                    model_id=LAST_FRAME_MODEL,
                    capability_overrides={"reference_audio_mode": "direct"},
                )
            ],
        )
        assert resp.status_code == 422
        assert "max_reference_audio_count" in resp.json()["detail"]

    @pytest.mark.parametrize("bad_value", ["DIRECT", "native", True, 1, None])
    def test_invalid_reference_audio_mode_rejected(self, capability_client: TestClient, bad_value):
        """枚举值两侧同判定：写入侧拒的与合成层忽略的必须是同一集合，否则「界面允许、执行反悔」。"""
        resp = _post_provider(
            capability_client,
            [
                _video_model(
                    endpoint=LAST_FRAME_ENDPOINT,
                    model_id=LAST_FRAME_MODEL,
                    capability_overrides={"reference_audio_mode": bad_value},
                )
            ],
        )
        assert resp.status_code == 422

    def test_non_video_endpoint_rejected(self, capability_client: TestClient):
        resp = _post_provider(
            capability_client,
            [
                {
                    "model_id": "dall-e-3",
                    "display_name": "D3",
                    "endpoint": "openai-images",
                    "is_enabled": True,
                    "capability_overrides": {"last_frame": True},
                }
            ],
        )
        assert resp.status_code == 422

    def test_rejected_save_leaves_stored_overrides_intact(self, capability_client: TestClient):
        pid = _create_provider(
            capability_client,
            [
                _video_model(
                    capability_overrides={"last_frame": True},
                    endpoint=LAST_FRAME_ENDPOINT,
                    model_id=LAST_FRAME_MODEL,
                )
            ],
        )

        resp = capability_client.put(
            f"/api/v1/custom-providers/{pid}/models",
            json={
                "models": [
                    _video_model(
                        endpoint=LAST_FRAME_ENDPOINT,
                        model_id=LAST_FRAME_MODEL,
                        capability_overrides={"last_frame": "yes"},
                    )
                ]
            },
        )
        assert resp.status_code == 422

        models = capability_client.get(f"/api/v1/custom-providers/{pid}").json()["models"]
        assert models[0]["capability_overrides"] == {"last_frame": True}


class TestReplaceModelsOverrideSemantics:
    """保存模型列表是整体替换：覆盖必须随列表回传，否则被清空。"""

    def test_overrides_survive_when_resubmitted(self, capability_client: TestClient):
        pid = _create_provider(
            capability_client,
            [
                _video_model(
                    capability_overrides={"last_frame": True},
                    endpoint=LAST_FRAME_ENDPOINT,
                    model_id=LAST_FRAME_MODEL,
                )
            ],
        )

        resp = capability_client.put(
            f"/api/v1/custom-providers/{pid}/models",
            json={
                "models": [
                    _video_model(
                        capability_overrides={"last_frame": True},
                        endpoint=LAST_FRAME_ENDPOINT,
                        model_id=LAST_FRAME_MODEL,
                    )
                ]
            },
        )
        assert resp.status_code == 200
        assert resp.json()[0]["capability_overrides"] == {"last_frame": True}

    def test_overrides_dropped_when_omitted(self, capability_client: TestClient):
        """整体替换语义的直接后果，前端保存模型列表时必须回传覆盖字段。"""
        pid = _create_provider(
            capability_client,
            [
                _video_model(
                    capability_overrides={"last_frame": True},
                    endpoint=LAST_FRAME_ENDPOINT,
                    model_id=LAST_FRAME_MODEL,
                )
            ],
        )

        resp = capability_client.put(
            f"/api/v1/custom-providers/{pid}/models",
            json={"models": [_video_model(endpoint=LAST_FRAME_ENDPOINT, model_id=LAST_FRAME_MODEL)]},
        )
        assert resp.status_code == 200
        assert resp.json()[0]["capability_overrides"] is None

    def test_invalid_override_rejected_on_replace(self, capability_client: TestClient):
        pid = _create_provider(capability_client, [_video_model()])

        resp = capability_client.put(
            f"/api/v1/custom-providers/{pid}/models",
            json={"models": [_video_model(capability_overrides={"last_frame": "yes"})]},
        )
        assert resp.status_code == 422

    def test_invalid_override_rejected_on_create(self, capability_client: TestClient):
        resp = _post_provider(capability_client, [_video_model(capability_overrides={"last_frame": 1})])
        assert resp.status_code == 422

    def test_invalid_override_rejected_on_full_update(self, capability_client: TestClient):
        pid = _create_provider(capability_client, [_video_model()])

        resp = capability_client.put(
            f"/api/v1/custom-providers/{pid}",
            json={
                "display_name": "Relay",
                "base_url": "https://relay.test/v1",
                "models": [_video_model(capability_overrides={"last_frame": "yes"})],
            },
        )
        assert resp.status_code == 422

    def test_override_written_and_read_back_through_full_update(self, capability_client: TestClient):
        """设置页表单的覆盖编辑就走这条整表 PUT：从「跟随判定」改成「强制开」保存后，
        重新加载页面（GET）必须还能读回同一个覆盖，否则用户的选择保存了个寂寞。"""
        pid = _create_provider(
            capability_client,
            [_video_model(endpoint=LAST_FRAME_ENDPOINT, model_id=LAST_FRAME_MODEL)],
        )

        resp = capability_client.put(
            f"/api/v1/custom-providers/{pid}",
            json={
                "display_name": "Relay",
                "base_url": "https://relay.test/v1",
                "models": [
                    _video_model(
                        capability_overrides={"last_frame": True},
                        endpoint=LAST_FRAME_ENDPOINT,
                        model_id=LAST_FRAME_MODEL,
                    )
                ],
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["models"][0]["capability_overrides"] == {"last_frame": True}

        reloaded = capability_client.get(f"/api/v1/custom-providers/{pid}")
        assert reloaded.status_code == 200
        assert reloaded.json()["models"][0]["capability_overrides"] == {"last_frame": True}

    def test_override_cleared_to_null_through_full_update(self, capability_client: TestClient):
        """控件回到「跟随判定」时前端把该键从稀疏字典移除、字典空则整体收敛回 null。
        这条 PUT 必须真的把落库的覆盖清掉，生效值才会重新跟随系统判定。"""
        pid = _create_provider(
            capability_client,
            [
                _video_model(
                    capability_overrides={"last_frame": True},
                    endpoint=LAST_FRAME_ENDPOINT,
                    model_id=LAST_FRAME_MODEL,
                )
            ],
        )

        resp = capability_client.put(
            f"/api/v1/custom-providers/{pid}",
            json={
                "display_name": "Relay",
                "base_url": "https://relay.test/v1",
                "models": [
                    _video_model(
                        capability_overrides=None,
                        endpoint=LAST_FRAME_ENDPOINT,
                        model_id=LAST_FRAME_MODEL,
                    )
                ],
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["models"][0]["capability_overrides"] is None

        reloaded = capability_client.get(f"/api/v1/custom-providers/{pid}")
        assert reloaded.json()["models"][0]["capability_overrides"] is None

    async def test_endpoint_change_validated_even_when_override_dict_unchanged(
        self, capability_client: TestClient, app_session_factory
    ):
        """每行都按提交上来的 (endpoint, 覆盖值) 校验：model_id 与覆盖字典原样不动、只切 endpoint
        时，校验结果天然随新 endpoint 变化（last_frame=True 是否合法依赖 endpoint 的
        end_image_capable），须针对新 endpoint 拒绝。"""
        pid = await _seed_provider_with_raw_models(
            app_session_factory,
            [
                {
                    "model_id": LAST_FRAME_MODEL,
                    "display_name": "Seedance",
                    "endpoint": LAST_FRAME_ENDPOINT,
                    "is_enabled": True,
                    "is_default": True,
                    "capability_overrides": {"last_frame": True},
                }
            ],
        )

        resp = capability_client.put(
            f"/api/v1/custom-providers/{pid}/models",
            json={
                "models": [
                    _video_model(
                        model_id=LAST_FRAME_MODEL,
                        # endpoint 悄悄换成 openai-video（不 end_image_capable），覆盖字典原样不动
                        endpoint=VIDEO_ENDPOINT,
                        capability_overrides={"last_frame": True},
                    )
                ]
            },
        )
        assert resp.status_code == 422


class TestResolverReturnsEffectiveCapabilities:
    """/video-capabilities 走的 resolver 必须回生效能力，且与执行层同源。"""

    @staticmethod
    async def _seed(
        db_session: AsyncSession,
        *,
        overrides: object | None,
        endpoint: str = VIDEO_ENDPOINT,
        model_id: str = VIDEO_MODEL,
    ) -> str:
        repo = CustomProviderRepository(db_session)
        provider = await repo.create_provider(
            display_name="Relay",
            discovery_format="openai",
            base_url="https://relay.test/v1",
            api_key="sk-relay",
            models=[
                {
                    "model_id": model_id,
                    "display_name": "Sora 2",
                    "endpoint": endpoint,
                    "is_enabled": True,
                    "is_default": True,
                    "supported_durations": "[5, 10]",
                    "capability_overrides": overrides,
                }
            ],
        )
        await db_session.commit()
        return make_provider_id(provider.id)

    @staticmethod
    async def _resolve(db_session: AsyncSession, provider_id: str, model_id: str = VIDEO_MODEL) -> dict:
        from lib.config.resolver import ConfigResolver
        from lib.config.service import ConfigService

        factory = async_sessionmaker(bind=db_session.get_bind(), class_=AsyncSession, expire_on_commit=False)  # type: ignore[call-overload]
        resolver = ConfigResolver(factory, _bound_session=db_session)
        return await resolver._resolve_video_caps_for_model(
            ConfigService(db_session), db_session, provider_id, model_id, None
        )

    async def test_custom_model_without_overrides_follows_system(self, db_session: AsyncSession):
        pid = await self._seed(db_session, overrides=None)

        caps = await self._resolve(db_session, pid)
        system = system_video_capabilities(endpoint=VIDEO_ENDPOINT, model_id=VIDEO_MODEL)
        assert caps["last_frame"] is system.last_frame
        assert caps["first_frame"] is system.first_frame
        assert caps["max_reference_images"] == system.max_reference_images

    async def test_override_changes_resolver_output(self, db_session: AsyncSession):
        """AC：对自定义模型写入覆盖后，该接口返回值随之变化。"""
        pid = await self._seed(
            db_session, overrides={"last_frame": True}, endpoint=LAST_FRAME_ENDPOINT, model_id=LAST_FRAME_MODEL
        )

        caps = await self._resolve(db_session, pid, model_id=LAST_FRAME_MODEL)
        assert system_video_capabilities(endpoint=LAST_FRAME_ENDPOINT, model_id=LAST_FRAME_MODEL).last_frame is False
        assert caps["last_frame"] is True

    async def test_override_ignored_when_endpoint_lacks_end_image_support(self, db_session: AsyncSession):
        """openai-video 的 delegate 不下传 end_image：即便存量行/非 API 写入把 last_frame 写成 True，
        resolver 也须回退系统判定，而不是把「合成层宣称支持、执行层静默丢帧」的错误状态当作生效。"""
        pid = await self._seed(db_session, overrides={"last_frame": True})

        caps = await self._resolve(db_session, pid)
        assert caps["last_frame"] is False

    @patch("lib.custom_provider.endpoints.ArkVideoBackend", new=MagicMock())
    async def test_resolver_matches_execution_layer(self, db_session: AsyncSession):
        """展示层与执行层出自同一合成函数：逐字段比对 resolver 与装载出的 backend。"""
        pid = await self._seed(
            db_session, overrides={"last_frame": True}, endpoint=LAST_FRAME_ENDPOINT, model_id=LAST_FRAME_MODEL
        )

        caps = await self._resolve(db_session, pid, model_id=LAST_FRAME_MODEL)
        db_session.expunge_all()
        backend = await load_custom_backend(
            session=db_session, provider_id=pid, model_id=LAST_FRAME_MODEL, media_type="video"
        )

        executed = backend.video_capabilities
        assert caps["first_frame"] is executed.first_frame
        assert caps["last_frame"] is executed.last_frame
        assert caps["max_reference_images"] == executed.max_reference_images
        # 同源的判据：两侧都等于合成函数在同一输入下的返回值
        expected = synthesize_video_capabilities(
            endpoint=LAST_FRAME_ENDPOINT, model_id=LAST_FRAME_MODEL, overrides={"last_frame": True}
        )
        assert executed == expected

    async def test_disabled_model_falls_back_to_default_model(self, db_session: AsyncSession):
        """请求的 model 被禁用时,与执行层同一条回退规则:改用该 provider 的默认启用 video
        model,而不是报错——否则 /video-capabilities 会 4xx,而实际生成请求会静默用默认模型
        成功,展示层与执行层因此漂移。"""
        repo = CustomProviderRepository(db_session)
        provider = await repo.create_provider(
            display_name="Relay",
            discovery_format="openai",
            base_url="https://relay.test/v1",
            api_key="sk-relay",
            models=[
                {
                    "model_id": "disabled-model",
                    "display_name": "Disabled",
                    "endpoint": VIDEO_ENDPOINT,
                    "is_enabled": False,
                    "is_default": False,
                    "supported_durations": "[5, 10]",
                    "capability_overrides": None,
                },
                {
                    "model_id": VIDEO_MODEL,
                    "display_name": "Sora 2",
                    "endpoint": VIDEO_ENDPOINT,
                    "is_enabled": True,
                    "is_default": True,
                    "supported_durations": "[5, 10]",
                    "capability_overrides": None,
                },
            ],
        )
        await db_session.commit()
        pid = make_provider_id(provider.id)

        caps = await self._resolve(db_session, pid, model_id="disabled-model")
        system = system_video_capabilities(endpoint=VIDEO_ENDPOINT, model_id=VIDEO_MODEL)
        assert caps["last_frame"] is system.last_frame
        assert caps["first_frame"] is system.first_frame

    async def test_media_type_mismatch_falls_back_to_default_model(self, db_session: AsyncSession):
        """请求的 model 仍启用,但它的 endpoint 不是 video 类型时,与执行层同样
        回退到该 provider 的默认启用 video model，避免展示层与执行层漂移。"""
        repo = CustomProviderRepository(db_session)
        provider = await repo.create_provider(
            display_name="Relay",
            discovery_format="openai",
            base_url="https://relay.test/v1",
            api_key="sk-relay",
            models=[
                {
                    "model_id": "repurposed-model",
                    "display_name": "Repurposed",
                    "endpoint": "openai-images",
                    "is_enabled": True,
                    "is_default": False,
                    "supported_durations": "[5, 10]",
                    "capability_overrides": None,
                },
                {
                    "model_id": VIDEO_MODEL,
                    "display_name": "Sora 2",
                    "endpoint": VIDEO_ENDPOINT,
                    "is_enabled": True,
                    "is_default": True,
                    "supported_durations": "[5, 10]",
                    "capability_overrides": None,
                },
            ],
        )
        await db_session.commit()
        pid = make_provider_id(provider.id)

        caps = await self._resolve(db_session, pid, model_id="repurposed-model")
        system = system_video_capabilities(endpoint=VIDEO_ENDPOINT, model_id=VIDEO_MODEL)
        assert caps["last_frame"] is system.last_frame
        assert caps["first_frame"] is system.first_frame

    async def test_builtin_boolean_caps_come_from_backend(self, db_session: AsyncSession):
        """内置分支的布尔位来自 backend 纯函数，注册表不存第二份。"""
        from lib.backend_assembly.specs import builtin_video_capabilities_for_model
        from lib.config.resolver import ConfigResolver
        from lib.config.service import ConfigService

        factory = async_sessionmaker(bind=db_session.get_bind(), class_=AsyncSession, expire_on_commit=False)  # type: ignore[call-overload]
        resolver = ConfigResolver(factory, _bound_session=db_session)
        caps = await resolver._resolve_video_caps_for_model(
            ConfigService(db_session), db_session, "openai", "sora-2", None
        )

        backend_caps = builtin_video_capabilities_for_model("openai", "sora-2")
        assert caps["source"] == "registry"
        assert caps["first_frame"] is backend_caps.first_frame
        assert caps["last_frame"] is backend_caps.last_frame


class TestBuiltinBackendsDeclareCapabilityFunction:
    """每个能承载视频模型的内置 provider 都要能被纯函数问出布尔能力位。"""

    def test_every_builtin_video_provider_resolvable(self):
        from lib.backend_assembly.specs import builtin_video_capabilities_for_model
        from lib.config.registry import PROVIDER_REGISTRY
        from lib.video_backends.base import VideoCapabilities

        for provider_id, meta in PROVIDER_REGISTRY.items():
            video_models = [mid for mid, mi in meta.models.items() if mi.media_type == "video"]
            if not video_models:
                continue
            for model_id in video_models:
                caps = builtin_video_capabilities_for_model(provider_id, model_id)
                assert isinstance(caps, VideoCapabilities), f"{provider_id}/{model_id}"

    def test_unknown_backend_name_fails_loud(self):
        from lib.video_backends.registry import video_capabilities_for_model

        with pytest.raises(ValueError, match="Unknown video backend"):
            video_capabilities_for_model("no-such-backend", "m")


class TestVideoCapabilitiesEndpoint:
    """GET /projects/{name}/video-capabilities 的响应形状与覆盖联动。

    其余同源测试打在 resolver 私有方法上，这里补住 HTTP 这一层：两个布尔位真的
    出现在接口响应里，且写入覆盖后接口返回值随之变化（不只是 resolver 内部变了）。
    """

    @staticmethod
    def _client(monkeypatch, app_session_factory, provider_id: str, model_id: str = VIDEO_MODEL) -> TestClient:
        from fastapi import FastAPI

        from lib.config import resolver as resolver_mod
        from server.routers import projects as projects_mod

        class _FakePM:
            def load_project(self, name: str) -> dict:
                return {"name": name, "video_backend": f"{provider_id}/{model_id}"}

        monkeypatch.setattr(projects_mod, "async_session_factory", app_session_factory)
        monkeypatch.setattr(resolver_mod, "get_project_manager", lambda: _FakePM())

        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="t", sub="t", role="admin")
        app.include_router(projects_mod.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        register_error_handlers(app)
        return TestClient(app)

    async def test_endpoint_returns_effective_boolean_caps(self, app_session_factory, monkeypatch):
        async with app_session_factory() as db_session:
            pid = await TestResolverReturnsEffectiveCapabilities._seed(db_session, overrides=None)

        with self._client(monkeypatch, app_session_factory, pid) as capability_client:
            body = capability_client.get("/api/v1/projects/demo/video-capabilities").json()

        system = system_video_capabilities(endpoint=VIDEO_ENDPOINT, model_id=VIDEO_MODEL)
        assert body["first_frame"] is system.first_frame
        assert body["last_frame"] is system.last_frame is False

    async def test_endpoint_follows_written_override(self, app_session_factory, monkeypatch):
        """AC：对自定义模型写入覆盖后，该接口返回值随之变化。"""
        async with app_session_factory() as db_session:
            pid = await TestResolverReturnsEffectiveCapabilities._seed(
                db_session, overrides={"last_frame": True}, endpoint=LAST_FRAME_ENDPOINT, model_id=LAST_FRAME_MODEL
            )

        with self._client(monkeypatch, app_session_factory, pid, model_id=LAST_FRAME_MODEL) as capability_client:
            body = capability_client.get("/api/v1/projects/demo/video-capabilities").json()

        assert system_video_capabilities(endpoint=LAST_FRAME_ENDPOINT, model_id=LAST_FRAME_MODEL).last_frame is False
        assert body["last_frame"] is True
