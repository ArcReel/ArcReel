"""Shared fake / stub objects for tests.

Only objects used across multiple test files belong here.
Single-file fakes stay in their respective test modules.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from collections.abc import AsyncIterator, Callable, Generator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

from instructor.core import InstructorRetryException

if TYPE_CHECKING:
    from lib.media_generator import MediaGenerator
    from lib.version_manager import PaidVersionCommit


_NO_SDK_MESSAGES: tuple[dict[str, Any], ...] = ()


async def empty_sdk_response_stream() -> AsyncIterator[dict[str, Any]]:
    """不产出任何消息即结束的 SDK 响应流。

    fake client 需要「空响应流」时返回本函数的结果；直接写 ``if False: yield`` 会留下
    静态不可达的分支。
    """
    for message in _NO_SDK_MESSAGES:
        yield message


class FakeProjectAssetMutationMixin:
    """Share production-shaped asset mutation contracts across router fakes."""

    expected_delete_asset_table: str | None = None

    def load_project(self, project_name: str) -> dict[str, Any]:
        raise NotImplementedError

    def update_project(self, project_name: str, mutate_fn: Callable[[dict], None]) -> Any:
        raise NotImplementedError

    def update_asset_entry(
        self,
        asset_type: str,
        project_name: str,
        name: str,
        mutate_fn: Callable[[dict], None],
    ) -> dict[str, Any]:
        from lib.asset_types import ASSET_SPECS, resolve_asset_key

        spec = ASSET_SPECS[asset_type]
        result: dict[str, Any] = {}

        def _mutate(project: dict) -> None:
            bucket = project.get(spec.bucket_key) or {}
            key = resolve_asset_key(bucket, name)
            if key is None:
                raise KeyError(name)
            entry = bucket[key]
            mutate_fn(entry)
            result.update(entry)

        self.update_project(project_name, _mutate)
        return result

    def delete_asset(self, project_name: str, table: str, name: str) -> dict[str, Any]:
        from lib.asset_types import resolve_asset_key

        if self.expected_delete_asset_table is not None:
            assert table == self.expected_delete_asset_table
        project = self.load_project(project_name)
        bucket = project.get(table) or {}
        key = resolve_asset_key(bucket, name)
        if key is None:
            raise KeyError(name)
        del bucket[key]
        return project


def persist_fake_script(project_path: Path, script_file: object, script: object) -> None:
    """Mirror an in-memory fake script through the production scripts directory."""

    normalized = str(script_file).replace("\\", "/").removeprefix("scripts/")
    target = project_path / "scripts" / normalized
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")


def select_formal_video(
    generator: MediaGenerator,
    *,
    resource_type: str = "reference_videos",
    resource_id: str = "E1U1",
    prompt: str = "",
) -> Callable[[Path, Path, int, Mapping[str, Any]], PaidVersionCommit]:
    """Build the minimal paid-video formal commit callback used by generator tests."""

    def _commit(
        staged_file: Path,
        current_file: Path,
        duration_seconds: int,
        version_metadata: Mapping[str, Any],
    ) -> PaidVersionCommit:
        return generator.versions.commit_staged_paid_version(
            resource_type=resource_type,
            resource_id=resource_id,
            prompt=prompt,
            staged_file=staged_file,
            current_file=current_file,
            select_current=True,
            duration_seconds=duration_seconds,
            **version_metadata,
        )

    return _commit


class FakeSDKClient:
    """Fake Claude Agent SDK client for SessionActor / SessionManager tests.

    支持：
    - `async with`：`__aenter__` 记录 connect 的 current_task，`__aexit__` 记录 disconnect
    - `method_tasks`: dict[str, list[asyncio.Task]] 记录每个方法被调用时的 task
    - `messages` 初始化参数：`receive_response` 依次 yield 的初始消息
    - `receive_response` 默认在 yield `type="result"` 后结束；
    - `block_forever=True` 时，仅在 `interrupt()` 注入 None sentinel 后才结束（用于测试 interrupt 中断 query 的场景）
    - `interrupt_message`：`interrupt()` 被调用时注入给 `receive_response` 的最后一条消息
    - `connect_error`：`__aenter__` 时抛出的异常，用于模拟连接失败
    """

    def __init__(
        self,
        messages=None,
        *,
        block_forever: bool = False,
        interrupt_message: dict | None = None,
        connect_error: Exception | None = None,
    ):
        self._initial_messages = list(messages) if messages else []
        self._block_forever = block_forever
        self._interrupt_message = interrupt_message
        self._connect_error = connect_error
        self._pending_messages: asyncio.Queue[dict | None] = asyncio.Queue()
        self.method_tasks: dict[str, list[asyncio.Task]] = {}
        self.sent_queries: list = []
        self.interrupted = False
        self.disconnected = False

    def _record(self, method: str) -> None:
        self.method_tasks.setdefault(method, []).append(asyncio.current_task())

    async def __aenter__(self):
        self._record("connect")
        if self._connect_error is not None:
            raise self._connect_error
        for msg in self._initial_messages:
            await self._pending_messages.put(msg)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._record("disconnect")
        self.disconnected = True
        return False

    async def query(self, prompt, session_id: str = "default") -> None:
        self._record("query")
        self.sent_queries.append(prompt)

    async def interrupt(self) -> None:
        self._record("interrupt")
        self.interrupted = True
        if self._interrupt_message is not None:
            await self._pending_messages.put(self._interrupt_message)
        # 告知 receive_response "可以停止了"
        await self._pending_messages.put(None)  # sentinel

    async def receive_response(self):
        self._record("receive_response")
        while True:
            msg = await self._pending_messages.get()
            if msg is None:
                return
            yield msg
            if msg.get("type") == "result" and not self._block_forever:
                return

    def push_message(self, msg: dict) -> None:
        """测试辅助：运行中往消息流注入一条消息。"""
        self._pending_messages.put_nowait(msg)

    # 向后兼容：保留原方法签名（旧测试仍使用 `await client.connect()` / `await client.disconnect()`）
    async def connect(self) -> None:
        self._record("connect")
        if self._connect_error is not None:
            raise self._connect_error

    async def disconnect(self) -> None:
        self._record("disconnect")
        self.disconnected = True


async def build_managed_with_actor(
    *,
    session_id: str = "s1",
    project_name: str = "demo",
    status: str = "idle",
    messages: list[dict] | None = None,
    block_forever: bool = False,
    on_message_hook=None,
):
    """测试辅助：围绕 FakeSDKClient 创建 SessionActor + ManagedSession，并启动 actor。

    返回 (managed, actor, client)。测试完成后调用 `await managed.send_disconnect()`
    清理，或由调用方自行管理生命周期。
    """
    from contextlib import asynccontextmanager

    from server.agent_runtime.session_actor import SessionActor
    from server.agent_runtime.session_manager import ManagedSession

    client = FakeSDKClient(messages=messages, block_forever=block_forever)

    @asynccontextmanager
    async def _factory_cm():
        async with client as c:
            yield c

    managed_ref: list = [None]

    def _on_message(msg):
        m = managed_ref[0]
        if m is None:
            return
        if on_message_hook is not None:
            on_message_hook(m, msg)
        else:
            m._on_actor_message(msg)

    actor = SessionActor(client_factory=_factory_cm, on_message=_on_message)
    managed = ManagedSession(
        session_id=session_id,
        actor=actor,
        status=status,
        project_name=project_name,
    )
    managed_ref[0] = managed
    await actor.start()
    return managed, actor, client


from lib.image_backends.base import ImageCapability, ImageGenerationRequest, ImageGenerationResult


class FakeImageBackend:
    """Fake image backend for testing."""

    def __init__(self, *, provider: str = "fake", model: str = "fake-model"):
        self._provider = provider
        self._model = model

    @property
    def name(self) -> str:
        return self._provider

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        # Minimal valid PNG (1x1 pixel)
        request.output_path.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
            b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
            b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        return ImageGenerationResult(
            image_path=request.output_path,
            provider=self._provider,
            model=self._model,
        )


class FakeReferenceCapabilityProjection:
    """Configurable provider capability adapter for reference projection tests."""

    def __init__(
        self,
        *,
        durations: tuple[int, ...],
        provider_id: str = "fake",
        model_id: str = "fake-model",
        max_reference_images: int | None = 9,
        text_to_video: bool = True,
    ) -> None:
        self.durations = durations
        self.provider_id = provider_id
        self.model_id = model_id
        self.max_reference_images = max_reference_images
        self.text_to_video = text_to_video

    async def resolve_candidate(self, project: dict, capability):
        from lib.reference_video.request_projection import ProviderProjectionCandidate

        del project
        return ProviderProjectionCandidate(
            capability=capability,
            provider_id=self.provider_id,
            model_id=self.model_id,
            supported_durations=self.durations,
            max_reference_images=self.max_reference_images,
            resolution="1080p",
            generate_audio=True,
            requested_generate_audio=True,
            has_audio_track=True,
            audio_switch_controllable=True,
            text_to_video=self.text_to_video,
        )


def fake_reference_request_projector(
    *,
    durations: tuple[int, ...] | None = None,
    provider_id: str = "fake",
    model_id: str = "fake-model",
    max_reference_images: int | None = 9,
    text_to_video: bool = True,
    capabilities: FakeReferenceCapabilityProjection | None = None,
):
    """构造使用真实资产水合与投影规则、仅替换 provider 能力查询的 async 测试入口。"""

    from lib.reference_video.request_projection import (
        FilesystemReferenceAssets,
        ReferenceRequestOptions,
        ReferenceUnitRequestProjection,
        ReferenceUnitRequestProjector,
        resolve_reference_assets,
    )

    if capabilities is not None:
        if (
            durations is not None
            or provider_id != "fake"
            or model_id != "fake-model"
            or max_reference_images != 9
            or text_to_video is not True
        ):
            raise ValueError("capabilities cannot be combined with candidate construction fields")
        projection_capabilities = capabilities
    else:
        if durations is None:
            raise ValueError("durations are required when capabilities are not supplied")
        projection_capabilities = FakeReferenceCapabilityProjection(
            durations=durations,
            provider_id=provider_id,
            model_id=model_id,
            max_reference_images=max_reference_images,
            text_to_video=text_to_video,
        )

    async def _project(
        *,
        project: dict,
        script: dict,
        unit: dict,
        project_path: Path,
        options: ReferenceRequestOptions | None = None,
        **_kwargs: object,
    ) -> ReferenceUnitRequestProjection:
        return await ReferenceUnitRequestProjector(
            projection_capabilities,
            FilesystemReferenceAssets(project_path),
        ).project_current(
            project=project,
            script=script,
            unit=unit,
            resolved_assets=resolve_reference_assets(project, project_path, unit),
            options=options,
        )

    return _project


class FakeConfigResolver:
    """能力解析器 seam 的手写替身：按桶回答视频能力，不触碰配置库。

    生产侧凡接 ``config_resolver`` 关键字的入口（``ToolContext``、``MediaGenerator``、
    ``resolve_video_caps`` / ``fetch_video_caps`` 及 ``text_generation`` 的几个取值器）都可注入本类，替代对这些取值器
    本身的整体替换——被替换掉的取值器里有软回退、联动约束收窄与声音档派生，那些才是用例要
    保护的行为。

    ``by_capability`` 给按桶分叉的路径用（参考生视频的无引用 unit 走 i2v 桶）：键是
    ``VideoCapability`` 字面量，值是覆盖在基础能力上的字段。``error`` / ``generate_audio_error``
    让软回退分支不必再 patch 就能触发。
    """

    def __init__(
        self,
        *,
        supported_durations: tuple[int, ...] | list[int] = (4, 6, 8),
        default_duration: int | None = 4,
        provider_id: str = "fake",
        model: str = "fake-video",
        max_reference_images: int | None = 3,
        max_reference_audio_count: int = 0,
        reference_audio_per_image: bool = False,
        generate_audio: bool = True,
        requested_generate_audio: bool = True,
        voice_consistency: str = "soft",
        by_capability: Mapping[str, Mapping[str, Any]] | None = None,
        capability_errors: Mapping[str, BaseException] | None = None,
        error: BaseException | None = None,
        generate_audio_error: BaseException | None = None,
        image_backend: tuple[str, str] = ("fake", "fake-image"),
        image_backend_error: BaseException | None = None,
        reference_payload_limits: tuple[int, int] | None = None,
        **extra: Any,
    ) -> None:
        self._base: dict[str, Any] = {
            "provider_id": provider_id,
            "model": model,
            "supported_durations": list(supported_durations),
            "max_duration": max(supported_durations) if supported_durations else 0,
            "max_reference_images": max_reference_images,
            "max_reference_audio_count": max_reference_audio_count,
            "reference_audio_per_image": reference_audio_per_image,
            "generate_audio": generate_audio,
            "requested_generate_audio": requested_generate_audio,
            "voice_consistency": voice_consistency,
            "source": "registry",
            "default_duration": default_duration,
            **extra,
        }
        self._by_capability = {key: dict(value) for key, value in (by_capability or {}).items()}
        self._capability_errors = dict(capability_errors or {})
        self._error = error
        self._generate_audio_error = generate_audio_error
        self._image_backend = image_backend
        self._image_backend_error = image_backend_error
        self._reference_payload_limits = reference_payload_limits
        self.capability_calls: list[str | None] = []
        self.project_names: list[str | None] = []
        self.project_payloads: list[dict[str, Any]] = []
        self.image_capability_calls: list[str | None] = []
        self.generate_audio_calls: list[dict[str, Any] | None] = []
        self.generate_audio_project_names: list[str | None] = []
        self.reference_limits_calls: list[str | None] = []

    def caps_for(self, capability: str | None = None) -> dict[str, Any]:
        """该桶的能力 dict（与生产返回同形），供用例直接对照期望。"""
        caps = dict(self._base)
        caps.update(self._by_capability.get(capability or "", {}))
        durations = caps.get("supported_durations") or []
        caps["max_duration"] = max(durations) if durations else 0
        return caps

    async def video_capabilities(self, project_name: str | None = None) -> dict[str, Any]:
        self.project_names.append(project_name)
        return self._resolve(None)

    async def video_capabilities_for_project(
        self,
        project: dict[str, Any],
        *,
        capability: str | None = None,
    ) -> dict[str, Any]:
        self.project_payloads.append(project)
        return self._resolve(capability)

    async def resolve_resolution(self, project: dict[str, Any], provider_id: str, model_id: str) -> str:
        del project, provider_id, model_id
        return "1080p"

    async def resolve_image_backend(
        self,
        project: dict[str, Any] | None,
        payload: dict[str, Any] | None = None,
        *,
        capability: str | None = None,
    ) -> Any:
        """解析图像供应商；``image_backend_error`` 给「项目槽位解析不出可用供应商」那条路径。"""
        from lib.config.resolver import ProviderModel

        del project, payload
        self.image_capability_calls.append(capability)
        if self._image_backend_error is not None:
            raise self._image_backend_error
        return ProviderModel(*self._image_backend)

    async def video_generate_audio_for_project(self, project: dict[str, Any] | None) -> bool:
        self.generate_audio_calls.append(project)
        if self._generate_audio_error is not None:
            raise self._generate_audio_error
        return bool(self._base["requested_generate_audio"])

    async def video_generate_audio(self, project_name: str | None = None) -> bool:
        """生产同名读点（按项目名）：与 ``video_generate_audio_for_project`` 共享同一份配置值。

        两个读点各记各的入参（本读点收项目名、按 project 的那个收 project dict），
        record 列表不合并，免得用例的等值断言被另一条路径的调用串味。
        """
        self.generate_audio_project_names.append(project_name)
        if self._generate_audio_error is not None:
            raise self._generate_audio_error
        return bool(self._base["requested_generate_audio"])

    async def reference_payload_limits(self, provider_id: str | None = None) -> tuple[int, int]:
        """参考图载荷限额（总量, 单张）；未显式配置时与生产同取 service 层保守默认。"""
        self.reference_limits_calls.append(provider_id)
        if self._reference_payload_limits is not None:
            return self._reference_payload_limits
        from lib.config.service import (
            _DEFAULT_REFERENCE_SINGLE_MAX_BYTES,
            _DEFAULT_REFERENCE_TOTAL_MAX_BYTES,
        )

        return _DEFAULT_REFERENCE_TOTAL_MAX_BYTES, _DEFAULT_REFERENCE_SINGLE_MAX_BYTES

    def _resolve(self, capability: str | None) -> dict[str, Any]:
        self.capability_calls.append(capability)
        if self._error is not None:
            raise self._error
        bucket_error = self._capability_errors.get(capability or "")
        if bucket_error is not None:
            raise bucket_error
        return self.caps_for(capability)


def instructor_api_call_exhausted(cause: Exception) -> InstructorRetryException:
    """构造「API 调用失败」形态的 Instructor 异常，供结构化输出降级链的判据测试使用。

    API 调用本身抛的异常（参数被拒、瞬态 5xx、连接错误）会中断档内重试循环、被包成
    ``InstructorRetryException``，原异常只挂在 ``__cause__`` 上。降级链的判据要认的正是这个
    形态，拿裸 API 异常做桩会测出生产里不存在的路径。此处 ``failed_attempts`` 为空表示这一档
    一次都没走到解析；先解析失败若干次再折在 API 上的混合形态由测试模块自行构造。形态本身由
    ``TestInstructorExceptionShape`` 对真实 Instructor 钉住。
    """
    exc = InstructorRetryException(
        str(cause),
        last_completion=None,
        n_attempts=1,
        total_usage=0,
        failed_attempts=[],
    )
    exc.__cause__ = cause
    return exc


@contextmanager
def bounded_poll_clock(step: float = 30.0):
    """轮询与重试等待的唯一替身入口：sleep 不真等，每读一次表推进 step 秒。

    ``retry_async`` 的退避与 ``poll_with_retry`` 的轮询间隔都经 ``lib.retry`` 的
    ``SystemClock`` 落到这两个符号上，压缩等待无需触碰 ``_compute_wait`` 等私有符号。

    终态判定失灵时（把已就绪的任务当成"仍在跑"），真实时钟下 sleep 被 mock 掉的轮询会以近乎
    为零的真实耗时空转到天荒地老——测试表现为挂起而不是失败。假表让这类缺陷在几十次轮询内
    撞上 ``max_wait`` 抛 ``TimeoutError``，红得快且可读。
    """
    clock = itertools.count(0.0, step)
    with (
        patch("lib.retry.asyncio.sleep", new_callable=AsyncMock),
        patch("lib.retry.time.monotonic", side_effect=lambda: next(clock)),
    ):
        yield


@contextmanager
def captured_provider_job_ids() -> Generator[list[dict[str, Any]]]:
    """provider_job_id 写回的手写替身：收下写回参数，不落 DB。

    ``persist_provider_job_id`` 是 backend 的 DB 边界，各提交-轮询型 backend 的测试只关心
    「写回了什么」。产出按参数名归档的记录列表，断言落在记录内容上而不是替身的调用对象上；
    非 worker 路径（``task_id=None``）跳过写回时列表保持为空。
    """
    records: list[dict[str, Any]] = []

    async def _record(
        task_id: str,
        job_id: str,
        *,
        provider: str,
        endpoint: str | None = None,
        base_url: str | None = None,
    ) -> None:
        records.append(
            {
                "task_id": task_id,
                "job_id": job_id,
                "provider": provider,
                "endpoint": endpoint,
                "base_url": base_url,
            }
        )

    with patch("lib.video_backends.base.persist_provider_job_id", _record):
        yield records


@contextmanager
def captured_ark_clients(module: str, client: Any = None) -> Generator[list[dict[str, Any]]]:
    """create_ark_client 的记录器：收下建客户端的参数，回给定（或空）客户端替身。

    Ark 系三个后端（文本 / 图像 / 视频）各自从自己的模块引用这个工厂，模块路径由调用方给出。
    base_url 归一化、鉴权透传的断言落在记录的构造参数上，而不是替身的调用对象。
    """
    from unittest.mock import MagicMock

    created: list[dict[str, Any]] = []
    instance = MagicMock() if client is None else client

    def _create(**kwargs: Any) -> Any:
        created.append(kwargs)
        return instance

    with patch(f"{module}.create_ark_client", _create):
        yield created


@contextmanager
def captured_openai_clients(client: Any = None) -> Generator[list[dict[str, Any]]]:
    """AsyncOpenAI 构造的记录器：收下建客户端的参数，回给定（或空）客户端替身。

    OpenAI 兼容族（openai / agnes 文本、openai 图像与视频、openai TTS、dashscope 与 minimax
    视频）都经 ``lib.openai_shared`` 取这个 SDK 入口，构造参数就是该边界上的契约：鉴权、
    base_url 归一化、超时。断言落在记录的构造参数上，而不是替身的调用对象。
    """
    from unittest.mock import AsyncMock

    created: list[dict[str, Any]] = []
    instance = AsyncMock() if client is None else client

    def _create(**kwargs: Any) -> Any:
        created.append(kwargs)
        return instance

    with patch("lib.openai_shared.AsyncOpenAI", _create):
        yield created


@contextmanager
def captured_backend_construction() -> Generator[list[dict[str, Any]]]:
    """四个后端 registry 的构造记录器：工厂换成只记参数的哑后端，不建 SDK 客户端。

    装配层（``ProviderSpec.build_backend``、``lib.text_backends.factory``）的产出就是
    「往哪个 media registry、用什么后端名、什么构造参数建后端」，真实后端要凭证要网络。
    按名逐个换工厂（保留键集合，``get_registered_backends`` 的读者不受影响），记录列表让
    断言落在构造参数本身；未注册名照旧由 ``create_backend`` fail-loud。
    """
    from lib.audio_backends import registry as audio_registry
    from lib.image_backends import registry as image_registry
    from lib.text_backends import registry as text_registry
    from lib.video_backends import registry as video_registry

    records: list[dict[str, Any]] = []
    factories: dict[str, dict[str, Any]] = {
        "text": text_registry._BACKEND_FACTORIES,
        "image": image_registry._BACKEND_FACTORIES,
        "video": video_registry._BACKEND_FACTORIES,
        "audio": audio_registry._BACKEND_FACTORIES,
    }

    def _recorder(media: str, name: str) -> Callable[..., Any]:
        def _build(**kwargs: Any) -> Any:
            records.append({"media": media, "backend": name, "kwargs": kwargs})
            return object()

        return _build

    saved = {media: dict(table) for media, table in factories.items()}
    for media, table in factories.items():
        for name in list(table):
            table[name] = _recorder(media, name)
    try:
        yield records
    finally:
        for media, table in factories.items():
            table.clear()
            table.update(saved[media])
