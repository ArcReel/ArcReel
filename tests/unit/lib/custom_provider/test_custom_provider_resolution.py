"""测试 CustomProviderModel.endpoint 字段及 resolver 的 fail-loud 行为。"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.db.models.custom_provider import CustomProvider, CustomProviderModel


@pytest.mark.asyncio
async def test_resolution_column_accepts_none_and_string(db_session: AsyncSession):
    provider = CustomProvider(
        display_name="X",
        discovery_format="openai",
        base_url="https://api.x.ai",
        api_key="k",
    )
    db_session.add(provider)
    await db_session.flush()

    m_without = CustomProviderModel(
        provider_id=provider.id,
        model_id="m1",
        display_name="M1",
        endpoint="openai-images",
        is_default=False,
        is_enabled=True,
        resolution=None,
    )
    m_with = CustomProviderModel(
        provider_id=provider.id,
        model_id="m2",
        display_name="M2",
        endpoint="newapi-video",
        is_default=False,
        is_enabled=True,
        resolution="1080p",
    )
    db_session.add_all([m_without, m_with])
    await db_session.flush()

    assert m_without.resolution is None
    assert m_with.resolution == "1080p"


@pytest.mark.asyncio
async def test_endpoint_field_stores_openai_chat(db_session: AsyncSession):
    """text 模型使用 openai-chat endpoint。"""
    provider = CustomProvider(
        display_name="TextProv",
        discovery_format="openai",
        base_url="https://api.example.com",
        api_key="k",
    )
    db_session.add(provider)
    await db_session.flush()

    model = CustomProviderModel(
        provider_id=provider.id,
        model_id="gpt-4o",
        display_name="GPT-4o",
        endpoint="openai-chat",
        is_default=True,
        is_enabled=True,
    )
    db_session.add(model)
    await db_session.flush()

    assert model.endpoint == "openai-chat"


@pytest.mark.asyncio
async def test_endpoint_field_stores_gemini_image(db_session: AsyncSession):
    """Google 图像模型使用 gemini-image endpoint。"""
    provider = CustomProvider(
        display_name="GoogleProv",
        discovery_format="google",
        base_url="https://generativelanguage.googleapis.com",
        api_key="k",
    )
    db_session.add(provider)
    await db_session.flush()

    model = CustomProviderModel(
        provider_id=provider.id,
        model_id="imagen-3.0-generate-002",
        display_name="Imagen 3",
        endpoint="gemini-image",
        is_default=False,
        is_enabled=True,
    )
    db_session.add(model)
    await db_session.flush()

    assert model.endpoint == "gemini-image"


@pytest.mark.asyncio
async def test_video_capabilities_endpoint_mismatch_raises(db_session: AsyncSession):
    """配 endpoint=openai-chat 但被当作 video_backend 使用 → 任务类型桶解析闸判悬空引用并报错。

    与执行路径同一条口径：media_type 不符的引用已不可兑现，报错让用户重选，不静默换成该供应商
    的其它 model（``docs/adr/0054``）。"""
    from lib.config.resolver import ConfigResolver, VideoBucketCapabilityError

    provider = CustomProvider(
        display_name="MismatchProv",
        discovery_format="openai",
        base_url="https://api.example.com",
        api_key="k",
    )
    db_session.add(provider)
    await db_session.flush()

    model = CustomProviderModel(
        provider_id=provider.id,
        model_id="gpt-4o",
        display_name="GPT-4o",
        endpoint="openai-chat",  # text endpoint, not video
        is_default=True,
        is_enabled=True,
        capability_overrides={"durations": [5, 10]},
    )
    db_session.add(model)
    await db_session.flush()

    from lib.custom_provider import make_provider_id

    provider_id_str = make_provider_id(provider.id)
    # project.json 中 video_backend 指向这个 text-only 模型
    project = {"video_backend": f"{provider_id_str}/gpt-4o"}

    factory = async_sessionmaker(bind=db_session.bind, class_=AsyncSession, expire_on_commit=False)

    from lib.config.service import ConfigService

    svc = ConfigService(db_session)
    resolver = ConfigResolver(factory, _bound_session=db_session)

    with pytest.raises(VideoBucketCapabilityError) as excinfo:
        await resolver._resolve_video_capabilities_from_project(svc, db_session, project)
    assert excinfo.value.code == "video_capability_reference_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "model_id", "expected_max_refs", "generation_mode"),
    [
        # 显式 int：直接用 endpoint cap（行为零变化）
        ("openai-video", "vid-model", 1, "storyboard"),
        ("newapi-video", "vid-model", 0, "storyboard"),
        # 未声明（None）：按 model_id 调 backend 纯 caps 函数读上限，不构造 backend
        ("ark-seedance", "doubao-seedance-2-0", 9, "storyboard"),
        ("ark-seedance", "doubao-seedance-1-0", 0, "storyboard"),  # 非 seedance-2 → 0，证明纯函数仍按 model 分支
        ("vidu-video", "viduq3-turbo", 7, "storyboard"),
        # MiniMax 声明式端点：S2V-01 单脸参考 → 1；海螺系列走首帧无参考 → 0。S2V-01 无首帧能力，
        # 只能落 r2v 桶，项目须是参考生视频才解析得到它
        ("minimax-s2v-01", "S2V-01", 1, "reference_video"),
        ("minimax-hailuo-v1", "MiniMax-Hailuo-2.3", 0, "storyboard"),
    ],
)
async def test_custom_video_max_reference_images_from_endpoint(
    db_session: AsyncSession, endpoint: str, model_id: str, expected_max_refs: int, generation_mode: str
):
    """custom 视频 model 的 max_reference_images 经 ENDPOINT_REGISTRY 派生：endpoint cap
    为显式 int 时直接用；为 None 时按 model_id 调 backend 纯 caps 函数读上限，均不静默落默认值。

    perf 回归护栏：无论哪条路径都断言 `CustomProviderRepository.get_provider` 未被调用——
    旧 fallthrough 会多查一次 provider 行 + 构造 backend，新路径纯函数解析不应触发。"""
    from unittest.mock import patch

    from lib.config.resolver import ConfigResolver
    from lib.config.service import ConfigService
    from lib.custom_provider import make_provider_id
    from lib.db.repositories.custom_provider_repo import CustomProviderRepository

    provider = CustomProvider(
        display_name="VideoProv",
        discovery_format="openai",
        base_url="https://api.example.com",
        api_key="k",
    )
    db_session.add(provider)
    await db_session.flush()

    model = CustomProviderModel(
        provider_id=provider.id,
        model_id=model_id,
        display_name="Vid Model",
        endpoint=endpoint,
        is_default=True,
        is_enabled=True,
        capability_overrides={"durations": [5, 10]},
    )
    db_session.add(model)
    await db_session.flush()

    provider_id_str = make_provider_id(provider.id)
    project = {"video_backend": f"{provider_id_str}/{model_id}", "generation_mode": generation_mode}

    factory = async_sessionmaker(bind=db_session.bind, class_=AsyncSession, expire_on_commit=False)
    svc = ConfigService(db_session)
    resolver = ConfigResolver(factory, _bound_session=db_session)

    with patch.object(CustomProviderRepository, "get_provider") as mock_get_provider:
        caps = await resolver._resolve_video_capabilities_from_project(svc, db_session, project)
    mock_get_provider.assert_not_called()
    assert caps["source"] == "custom"
    assert caps["max_reference_images"] == expected_max_refs


@pytest.mark.asyncio
async def test_custom_video_caps_resolved_without_api_key(db_session: AsyncSession):
    """endpoint cap=None 的 ark-seedance 在 api_key 为空时仍解析出上限（=9）且**不抛**。

    旧路径构造 ArkVideoBackend 会因空 key 抛 ValueError（与延迟解析的 vidu 行为不对称）；
    纯 caps 函数不依赖 client，故对称放行。"""
    from lib.config.resolver import ConfigResolver
    from lib.config.service import ConfigService
    from lib.custom_provider import make_provider_id

    provider = CustomProvider(
        display_name="VideoProv",
        discovery_format="openai",
        base_url="https://api.example.com",
        api_key="",
    )
    db_session.add(provider)
    await db_session.flush()

    model = CustomProviderModel(
        provider_id=provider.id,
        model_id="doubao-seedance-2-0",
        display_name="Vid Model",
        endpoint="ark-seedance",
        is_default=True,
        is_enabled=True,
        capability_overrides={"durations": [5, 10]},
    )
    db_session.add(model)
    await db_session.flush()

    provider_id_str = make_provider_id(provider.id)
    project = {"video_backend": f"{provider_id_str}/doubao-seedance-2-0"}

    factory = async_sessionmaker(bind=db_session.bind, class_=AsyncSession, expire_on_commit=False)
    svc = ConfigService(db_session)
    resolver = ConfigResolver(factory, _bound_session=db_session)

    caps = await resolver._resolve_video_capabilities_from_project(svc, db_session, project)
    assert caps["max_reference_images"] == 9


@pytest.mark.asyncio
async def test_custom_video_max_refs_missing_caps_fn_raises(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    """endpoint cap=None 且 video_caps_for_model 也缺失（misconfig）→ 任务类型桶解析闸报悬空引用。

    EndpointSpec 是 frozen dataclass，用 dataclasses.replace 造 misconfig spec 再 setitem 临时替换
    （module-load 不变式仅 import 期生效，不拦运行时 monkeypatch）。"""
    import dataclasses

    from lib.config.resolver import ConfigResolver, VideoBucketCapabilityError
    from lib.config.service import ConfigService
    from lib.custom_provider import make_provider_id
    from lib.custom_provider.endpoints import ENDPOINT_REGISTRY

    bad_spec = dataclasses.replace(
        ENDPOINT_REGISTRY["ark-seedance"], video_max_reference_images=None, video_caps_for_model=None
    )
    monkeypatch.setitem(ENDPOINT_REGISTRY, "ark-seedance", bad_spec)

    provider = CustomProvider(
        display_name="VideoProv",
        discovery_format="openai",
        base_url="https://api.example.com",
        api_key="k",
    )
    db_session.add(provider)
    await db_session.flush()

    model = CustomProviderModel(
        provider_id=provider.id,
        model_id="doubao-seedance-2-0",
        display_name="Vid Model",
        endpoint="ark-seedance",
        is_default=True,
        is_enabled=True,
        capability_overrides={"durations": [5, 10]},
    )
    db_session.add(model)
    await db_session.flush()

    provider_id_str = make_provider_id(provider.id)
    project = {"video_backend": f"{provider_id_str}/doubao-seedance-2-0"}

    factory = async_sessionmaker(bind=db_session.bind, class_=AsyncSession, expire_on_commit=False)
    svc = ConfigService(db_session)
    resolver = ConfigResolver(factory, _bound_session=db_session)

    with pytest.raises(VideoBucketCapabilityError) as excinfo:
        await resolver._resolve_video_capabilities_from_project(svc, db_session, project)
    assert excinfo.value.code == "video_capability_reference_unavailable"
    # 具体成因留在异常链上供日志定位，不进面向用户的报错文案
    assert "declares neither video_max_reference_images" in str(excinfo.value.__cause__)


@pytest.mark.asyncio
async def test_custom_video_max_refs_negative_caps_raises(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    """endpoint cap=None，caps 函数返回负数 → raise ValueError（不静默下传坏值）。"""
    import dataclasses

    from arcreel_market_core.video_backend_contract import VideoCapabilities
    from lib.config.resolver import ConfigResolver, VideoBucketCapabilityError
    from lib.config.service import ConfigService
    from lib.custom_provider import make_provider_id
    from lib.custom_provider.endpoints import ENDPOINT_REGISTRY

    def _negative_caps(model: str) -> VideoCapabilities:
        return VideoCapabilities(max_reference_images=-1)

    bad_spec = dataclasses.replace(ENDPOINT_REGISTRY["ark-seedance"], video_caps_for_model=_negative_caps)
    monkeypatch.setitem(ENDPOINT_REGISTRY, "ark-seedance", bad_spec)

    provider = CustomProvider(
        display_name="VideoProv",
        discovery_format="openai",
        base_url="https://api.example.com",
        api_key="k",
    )
    db_session.add(provider)
    await db_session.flush()

    model = CustomProviderModel(
        provider_id=provider.id,
        model_id="doubao-seedance-2-0",
        display_name="Vid Model",
        endpoint="ark-seedance",
        is_default=True,
        is_enabled=True,
        capability_overrides={"durations": [5, 10]},
    )
    db_session.add(model)
    await db_session.flush()

    provider_id_str = make_provider_id(provider.id)
    project = {"video_backend": f"{provider_id_str}/doubao-seedance-2-0"}

    factory = async_sessionmaker(bind=db_session.bind, class_=AsyncSession, expire_on_commit=False)
    svc = ConfigService(db_session)
    resolver = ConfigResolver(factory, _bound_session=db_session)

    with pytest.raises(VideoBucketCapabilityError) as excinfo:
        await resolver._resolve_video_capabilities_from_project(svc, db_session, project)
    assert excinfo.value.code == "video_capability_reference_unavailable"
    assert "invalid backend max_reference_images" in str(excinfo.value.__cause__)


async def _seed_image_provider(db_session: AsyncSession, *, endpoint: str, model_id: str) -> str:
    from lib.custom_provider import make_provider_id

    provider = CustomProvider(
        display_name="ImageProv",
        discovery_format="openai",
        base_url="https://api.example.com",
        api_key="k",
    )
    db_session.add(provider)
    await db_session.flush()
    db_session.add(
        CustomProviderModel(
            provider_id=provider.id,
            model_id=model_id,
            display_name=model_id,
            endpoint=endpoint,
            is_default=True,
            is_enabled=True,
        )
    )
    await db_session.flush()
    return make_provider_id(provider.id)


@pytest.mark.asyncio
async def test_image_bucket_gate_rejects_model_lacking_the_bucket(db_session: AsyncSession):
    """i2i 桶配了只声明 t2i 的自定义模型 → 解析期即报错，不静默换模型也不等到执行期。"""
    from lib.config.resolver import ConfigResolver, ImageBucketCapabilityError

    provider_id_str = await _seed_image_provider(db_session, endpoint="openai-images-generations", model_id="t2i-m")
    project = {"image_provider_i2i": f"{provider_id_str}/t2i-m"}

    factory = async_sessionmaker(bind=db_session.bind, class_=AsyncSession, expire_on_commit=False)
    resolver = ConfigResolver(factory, _bound_session=db_session)

    with pytest.raises(ImageBucketCapabilityError) as excinfo:
        await resolver.resolve_image_backend(project, None, generation_type="i2i")
    assert excinfo.value.code == "image_capability_missing_i2i"
    assert excinfo.value.params == {"provider": provider_id_str, "model": "t2i-m"}


@pytest.mark.asyncio
async def test_image_bucket_gate_passes_model_declaring_the_bucket(db_session: AsyncSession):
    from lib.config.resolver import ConfigResolver

    provider_id_str = await _seed_image_provider(db_session, endpoint="openai-images-edits", model_id="i2i-m")
    project = {"image_provider_i2i": f"{provider_id_str}/i2i-m"}

    factory = async_sessionmaker(bind=db_session.bind, class_=AsyncSession, expire_on_commit=False)
    resolver = ConfigResolver(factory, _bound_session=db_session)

    resolved = await resolver.resolve_image_backend(project, None, generation_type="i2i")
    assert (resolved.provider_id, resolved.model_id) == (provider_id_str, "i2i-m")


@pytest.mark.asyncio
async def test_image_bucket_gate_leaves_disabled_reference_to_the_default_fallback(db_session: AsyncSession):
    """引用的模型已禁用 → 不在解析闸报错，留给按桶过滤的默认回退处理。"""
    from lib.config.resolver import ConfigResolver

    provider_id_str = await _seed_image_provider(db_session, endpoint="openai-images-generations", model_id="t2i-m")
    model = (await db_session.execute(select(CustomProviderModel))).scalar_one()
    model.is_enabled = False
    await db_session.flush()
    project = {"image_provider_i2i": f"{provider_id_str}/t2i-m"}

    factory = async_sessionmaker(bind=db_session.bind, class_=AsyncSession, expire_on_commit=False)
    resolver = ConfigResolver(factory, _bound_session=db_session)

    resolved = await resolver.resolve_image_backend(project, None, generation_type="i2i")
    assert (resolved.provider_id, resolved.model_id) == (provider_id_str, "t2i-m")


# ── 参考生视频家族装配：按 N 建 {max_reference_images: model_id} 索引 ──


def _slot_video_endpoint(key: str, slots: int, *, first_frame: bool = False):
    """造一个 video endpoint spec：video_caps_for_model 恒返回指定槽数的能力。

    R2V 候选用 first_frame=False、slots>0；i2v 用 first_frame=True、slots=0（R2V 能力位不满足）。
    """
    import dataclasses

    from arcreel_market_core.video_backend_contract import VideoCapabilities
    from lib.custom_provider.endpoints import ENDPOINT_REGISTRY

    def _caps(_model_id: str) -> VideoCapabilities:
        return VideoCapabilities(max_reference_images=slots, first_frame=first_frame)

    return dataclasses.replace(
        ENDPOINT_REGISTRY["ark-seedance"],
        key=key,
        video_max_reference_images=None,
        video_caps_for_model=_caps,
    )


@pytest.mark.asyncio
async def test_reference_video_family_indexes_by_slot_count_and_excludes_i2v(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """装配 I1..I9（真实 model_id）+ 一个 i2v 首帧模型，family 恰为 {1..9} 且排除 i2v。

    I3 索引到无 `_shot` 的真实 model_id——端到端验证真相源不是字符串模板。
    """
    from lib.custom_provider import make_provider_id, parse_provider_id
    from lib.custom_provider.endpoints import ENDPOINT_REGISTRY
    from lib.custom_provider.reference_video_model_selection import resolve_reference_video_family

    real_model_ids = {
        1: "HONGYU-MinimaxH3-I1-A1-First_shot-AutoPrompt",
        2: "HONGYU-MinimaxH3-I2-A2-First_shot-AutoPrompt",
        3: "HONGYU-MinimaxH3-I3-A3-First-AutoPrompt",  # 无 _shot
        4: "HONGYU-MinimaxH3-I4-A3-First_shot-AutoPrompt",
        5: "HONGYU-MinimaxH3-I5-A3-First_shot-AutoPrompt",
        6: "HONGYU-MinimaxH3-I6-A3-First_shot-AutoPrompt",
        7: "HONGYU-MinimaxH3-I7-A3-First_shot-AutoPrompt",
        8: "HONGYU-MinimaxH3-I8-A3-First_shot-AutoPrompt",
        9: "HONGYU-MinimaxH3-I9-A3-First_shot-AutoPrompt",
    }

    for n in range(1, 10):
        monkeypatch.setitem(ENDPOINT_REGISTRY, f"test-i{n}", _slot_video_endpoint(f"test-i{n}", n))
    monkeypatch.setitem(ENDPOINT_REGISTRY, "test-i2v", _slot_video_endpoint("test-i2v", 0, first_frame=True))

    provider = CustomProvider(
        display_name="HONGYU",
        discovery_format="openai",
        base_url="https://api.example.com",
        api_key="k",
    )
    db_session.add(provider)
    await db_session.flush()

    for n in range(1, 10):
        db_session.add(
            CustomProviderModel(
                provider_id=provider.id,
                model_id=real_model_ids[n],
                display_name=real_model_ids[n],
                endpoint=f"test-i{n}",
                is_default=(n == 3),
                is_enabled=True,
            )
        )
    db_session.add(
        CustomProviderModel(
            provider_id=provider.id,
            model_id="HONYU_MinimaxH3_First_frome",
            display_name="i2v",
            endpoint="test-i2v",
            is_default=False,
            is_enabled=True,
        )
    )
    await db_session.flush()

    db_pid = parse_provider_id(make_provider_id(provider.id))
    family = await resolve_reference_video_family(db_session, db_pid)

    assert family == real_model_ids
    assert family[3] == "HONGYU-MinimaxH3-I3-A3-First-AutoPrompt"
    assert "_shot" not in family[3]
    assert "HONYU_MinimaxH3_First_frome" not in family.values()


@pytest.mark.asyncio
async def test_reference_video_family_skips_disabled_models(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    """禁用的候选不进 family——该 N 在选择时会 fail loud。"""
    from lib.custom_provider import make_provider_id, parse_provider_id
    from lib.custom_provider.endpoints import ENDPOINT_REGISTRY
    from lib.custom_provider.reference_video_model_selection import resolve_reference_video_family

    monkeypatch.setitem(ENDPOINT_REGISTRY, "test-i3", _slot_video_endpoint("test-i3", 3))
    monkeypatch.setitem(ENDPOINT_REGISTRY, "test-i5", _slot_video_endpoint("test-i5", 5))

    provider = CustomProvider(
        display_name="HONGYU",
        discovery_format="openai",
        base_url="https://api.example.com",
        api_key="k",
    )
    db_session.add(provider)
    await db_session.flush()
    db_session.add_all(
        [
            CustomProviderModel(
                provider_id=provider.id,
                model_id="m-i3",
                display_name="i3",
                endpoint="test-i3",
                is_default=True,
                is_enabled=True,
            ),
            CustomProviderModel(
                provider_id=provider.id,
                model_id="m-i5",
                display_name="i5",
                endpoint="test-i5",
                is_default=False,
                is_enabled=False,  # 禁用
            ),
        ]
    )
    await db_session.flush()

    db_pid = parse_provider_id(make_provider_id(provider.id))
    family = await resolve_reference_video_family(db_session, db_pid)
    assert family == {3: "m-i3"}


@pytest.mark.asyncio
async def test_reference_video_family_tie_break_prefers_default_then_lexicographic(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """同一 N 多个候选：is_default 优先，其次 model_id 字典序升序，不依赖查询 id 顺序。"""
    from lib.custom_provider import make_provider_id, parse_provider_id
    from lib.custom_provider.endpoints import ENDPOINT_REGISTRY
    from lib.custom_provider.reference_video_model_selection import resolve_reference_video_family

    monkeypatch.setitem(ENDPOINT_REGISTRY, "test-i3a", _slot_video_endpoint("test-i3a", 3))
    monkeypatch.setitem(ENDPOINT_REGISTRY, "test-i3b", _slot_video_endpoint("test-i3b", 3))

    provider = CustomProvider(
        display_name="HONGYU",
        discovery_format="openai",
        base_url="https://api.example.com",
        api_key="k",
    )
    db_session.add(provider)
    await db_session.flush()
    # 先插入字典序更小、非默认的候选（id 更小），再插入 default 候选：
    # 验证 default 覆盖 id 顺序而非被先到者占位。
    db_session.add_all(
        [
            CustomProviderModel(
                provider_id=provider.id,
                model_id="zzz-non-default",
                display_name="z",
                endpoint="test-i3a",
                is_default=False,
                is_enabled=True,
            ),
            CustomProviderModel(
                provider_id=provider.id,
                model_id="aaa-default",
                display_name="a",
                endpoint="test-i3b",
                is_default=True,
                is_enabled=True,
            ),
        ]
    )
    await db_session.flush()

    db_pid = parse_provider_id(make_provider_id(provider.id))
    family = await resolve_reference_video_family(db_session, db_pid)
    assert family == {3: "aaa-default"}
