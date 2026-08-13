"""
MediaGenerator 中间层

封装 GeminiClient + VersionManager，提供"调用方无感"的版本管理。
调用方只需传入 project_path 和 resource_id，版本管理自动完成。

覆盖的资源类型：
- storyboards: 分镜图 (scene_E1S01.png)
- videos: 视频 (scene_E1S01.mp4)
- characters: 角色设计图 (姜月茴.png)
- scenes: 场景设计图 (庙宇.png)
- props: 道具设计图 (玉佩.png)
- grids: 宫格图 (grid_xxx.png)
"""

import asyncio
import hashlib
import logging
import os
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, cast

from PIL import Image

if TYPE_CHECKING:
    from lib.audio_backends.base import AudioBackend
    from lib.config.resolver import ConfigResolver
    from lib.image_backends.base import ImageBackend
    from lib.reference_compression import CompressedRef, PayloadLimits, ReferenceSpec

from lib.audio_utils import probe_reference_audio_total_seconds
from lib.db.base import DEFAULT_USER_ID
from lib.gemini_shared import RateLimiter
from lib.ledger import Ledger
from lib.path_safety import PathTraversalError, safe_join
from lib.providers import CallType, require_provider_pair
from lib.resource_paths import resource_relative_path
from lib.version_manager import PaidVersionCommit, VersionManager

logger = logging.getLogger(__name__)


def task_video_staging_path(output_path: Path, task_id: str) -> Path:
    """Return the bounded, deterministic formal-output path owned by one task."""

    if not isinstance(task_id, str) or not task_id:
        raise ValueError("formal video output requires a task_id")
    token = hashlib.sha256(task_id.encode("utf-8")).hexdigest()
    return output_path.with_name(f".{token}.task-output{output_path.suffix}")


def _is_junction(path: Path) -> bool:
    isjunction = getattr(os.path, "isjunction", None)
    return bool(isjunction is not None and isjunction(path))


def _remove_task_video_staging_path(path: Path) -> None:
    """Remove a task-owned staging entry without traversing links or arbitrary directories."""

    if path.is_symlink():
        path.unlink(missing_ok=True)
    elif _is_junction(path):
        path.rmdir()
    elif os.path.lexists(path):
        if not path.is_file():
            raise ValueError("formal video staging path is not a regular file")
        path.unlink()


def cleanup_staged_video_output(
    project_path: Path,
    resource_type: str,
    resource_id: str,
    task_id: str,
) -> None:
    """Remove the deterministic interrupted output owned by one video task."""

    output_path = safe_join(project_path, resource_relative_path(resource_type, resource_id))
    _remove_task_video_staging_path(task_video_staging_path(output_path, task_id))


@asynccontextmanager
async def _remove_staged_output_on_error(path: Path | None):
    """Remove a formal-output staging file whenever the guarded operation aborts."""

    try:
        yield
    except BaseException:
        if path is not None:
            path.unlink(missing_ok=True)
        raise


def _is_413(exc: BaseException) -> bool:
    """识别请求体超限（HTTP 413）。

    先从异常通用属性提取状态码：``status_code``（OpenAI/xai SDK + 规整后的 vidu/dashscope）/
    ``response.status_code``（httpx.HTTPStatusError）/ ``code``（google-genai APIError）——
    覆盖默认 provider gemini 及各 SDK 类后端，而非只认 httpx（与 lib/video_backends/ark.py 的
    状态码提取口径一致）。状态码缺失时退回短语匹配，但不用裸 "413" 子串——避免被字节数 /
    请求 ID（如 "41300 bytes"）误命中。
    """
    status = (
        getattr(exc, "status_code", None)
        or getattr(getattr(exc, "response", None), "status_code", None)
        or getattr(exc, "code", None)
    )
    # 防御性 int 转换：个别 SDK / mock 可能把状态码给成字符串 "413"，
    # 直接 ``== 413`` 会恒 False；非数字状态码落回下方短语匹配。
    try:
        if status is not None and int(status) == 413:
            return True
    except (ValueError, TypeError):
        pass
    msg = str(exc).lower()
    return "request entity too large" in msg or "payload too large" in msg


# 记账 segment_id 判定：resource_id 可否作 segment_id 按 call_type 各自的 resource_type
# 白名单收敛于此单点，image/video/audio 三条记账路径均经由它。
_SEGMENT_ID_ALLOWED_RESOURCE_TYPES: dict[CallType, frozenset[str]] = {
    "image": frozenset({"storyboards", "videos", "grids"}),
    "video": frozenset({"storyboards", "videos", "reference_videos"}),
}


def segment_id_for(call_type: CallType, resource_type: str, resource_id: str) -> str | None:
    """按记账调用类型与资源类型判定 segment_id；audio 无白名单，无条件透传。"""
    if call_type == "audio":
        return resource_id
    allowed = _SEGMENT_ID_ALLOWED_RESOURCE_TYPES.get(call_type)
    if allowed is None:
        raise ValueError(f"unknown ledger channel: {call_type!r}")
    return resource_id if resource_type in allowed else None


class MediaGenerator:
    """
    媒体生成器中间层

    封装 GeminiClient + VersionManager，提供自动版本管理。
    """

    def __init__(
        self,
        project_path: Path,
        rate_limiter: RateLimiter | None = None,
        image_backend: Optional["ImageBackend"] = None,
        video_backend=None,
        audio_backend: Optional["AudioBackend"] = None,
        *,
        config_resolver: Optional["ConfigResolver"] = None,
        user_id: str = DEFAULT_USER_ID,
        image_provider_id: str | None = None,
        video_provider_id: str | None = None,
        audio_provider_id: str | None = None,
    ):
        """
        初始化 MediaGenerator

        Args:
            project_path: 项目根目录路径
            rate_limiter: 可选的限流器实例
            image_backend: 可选的 ImageBackend 实例（用于图片生成）
            video_backend: 可选的 VideoBackend 实例（用于视频生成）
            audio_backend: 可选的 AudioBackend 实例（用于语音合成）
            config_resolver: ConfigResolver 实例，用于运行时读取配置
            user_id: 用户 ID
            image_provider_id: 图像 registry provider_id。既是解析参考图压缩 per-provider 上限的
                依据，也是该 lane 记账 provider 的单一真相源。须为 registry id（如 "gemini-aistudio"），
                非 backend.name；与 image_backend 成对提供，缺一即抛
            video_provider_id: 视频 registry provider_id（同上，I2V/R2V 与视频记账用）
            audio_provider_id: 音频 registry provider_id（旁白 TTS 记账用），与 audio_backend 成对
        """
        require_provider_pair("image", image_backend, image_provider_id)
        require_provider_pair("video", video_backend, video_provider_id)
        require_provider_pair("audio", audio_backend, audio_provider_id)

        self.project_path = Path(project_path)
        self.project_name = self.project_path.name
        self._rate_limiter = rate_limiter
        self._image_backend = image_backend
        self._video_backend = video_backend
        self._audio_backend = audio_backend
        self._config = config_resolver
        self._user_id = user_id
        self._image_provider_id = image_provider_id
        self._video_provider_id = video_provider_id
        self._audio_provider_id = audio_provider_id
        self.versions = VersionManager(project_path)

        # 初始化记账账本（使用全局 async session factory）
        self.ledger = Ledger()

    @staticmethod
    def _prepare_video_output(
        output_path: Path,
        *,
        formal_output: bool,
        task_id: str | None,
    ) -> tuple[Path | None, Path]:
        """Return an optional same-directory staging path and the path the backend must write."""

        if not formal_output:
            return None, output_path
        if task_id is None:
            raise ValueError("formal video output requires a task_id")
        staged_output_path = task_video_staging_path(output_path, task_id)
        # A process can die after the backend starts writing. Reusing one task-addressable path bounds crash residue
        # and removes the prior attempt before normal generation or resume writes the same provider result again.
        _remove_task_video_staging_path(staged_output_path)
        return staged_output_path, staged_output_path

    def _commit_video_output_version(
        self,
        *,
        resource_type: str,
        resource_id: str,
        prompt: str,
        output_path: Path,
        staged_output_path: Path | None,
        duration_seconds: int,
        version_metadata: Mapping[str, Any],
        commit_formal_output: Callable[[Path, Path, int, Mapping[str, Any]], PaidVersionCommit] | None = None,
    ) -> PaidVersionCommit:
        """Register a normal or resumed video through one identical formal-output commit path."""

        if staged_output_path is None:
            return PaidVersionCommit(
                version=self.versions.add_version(
                    resource_type=resource_type,
                    resource_id=resource_id,
                    prompt=prompt,
                    source_file=output_path,
                    duration_seconds=duration_seconds,
                    **version_metadata,
                ),
                selected=True,
            )
        try:
            if commit_formal_output is not None:
                return commit_formal_output(
                    staged_output_path,
                    output_path,
                    duration_seconds,
                    version_metadata,
                )
            self.versions.commit_staged_paid_version(
                resource_type=resource_type,
                resource_id=resource_id,
                prompt=prompt,
                staged_file=staged_output_path,
                current_file=output_path,
                select_current=False,
                duration_seconds=duration_seconds,
                **version_metadata,
            )
            raise RuntimeError("formal video output requires an artifact commit callback")
        finally:
            staged_output_path.unlink(missing_ok=True)

    @staticmethod
    def _sync(coro):
        """Run an async coroutine from synchronous code (e.g. inside to_thread)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result()
        return asyncio.run(coro)

    def _get_output_path(self, resource_type: str, resource_id: str) -> Path:
        """
        根据资源类型和 ID 推断输出路径

        Args:
            resource_type: 资源类型 (storyboards, videos, characters, clues)
            resource_id: 资源 ID (E1S01, 姜月茴, 玉佩)

        Returns:
            输出文件的绝对路径
        """
        relative_path = resource_relative_path(resource_type, resource_id)
        try:
            return safe_join(self.project_path, relative_path)
        except PathTraversalError:
            raise ValueError(f"非法资源 ID: '{resource_id}'")

    def _ensure_parent_dir(self, output_path: Path) -> None:
        """确保输出目录存在"""
        output_path.parent.mkdir(parents=True, exist_ok=True)

    async def _reference_limits(self, provider_id: str | None) -> "PayloadLimits":
        """解析参考上传副本的 PayloadLimits。

        无 config_resolver（零配置场景）→ 保守通用默认，不触 DB。其余情形统一交给
        ConfigResolver.reference_payload_limits：provider_id 为 None 时它内部短路返回 service
        层默认（同样不触 DB），避免在本层再引入第二份默认来源、与配置层漂移。
        """
        from lib.reference_compression import PayloadLimits

        if self._config is None:
            return PayloadLimits()
        total, single = await self._config.reference_payload_limits(provider_id)
        return PayloadLimits(total_max_bytes=total, single_max_bytes=single)

    async def _run_with_reference_compression(
        self,
        *,
        specs: "list[ReferenceSpec]",
        provider_id: str | None,
        build_and_call: "Callable[[list[CompressedRef]], Awaitable[Any]]",
        before_submit: Callable[[], Awaitable[None]] | None = None,
        provider_resubmit_is_unsafe: Callable[[], bool] | None = None,
    ) -> Any:
        """对参考上传副本做主动压缩 + 预检降档 + 被动 413 兜底，再调用 backend。

        build_and_call 接收按原序合并好的 CompressedRef 列表（1:1 保数、含透传项），构造 provider
        请求并返回 backend.generate 协程。无参考图时直接单次调用、不做降档（T2I/T2V 的 413 与
        参考图无关，不应被误转成 floor 错误）。
        """
        from lib.reference_compression import (
            LADDER_STEPS,
            ReferencePayloadFloorError,
            compressed_reference_payload,
        )

        submit_boundary_crossed = False

        async def _call_once(compressed: "list[CompressedRef]") -> Any:
            nonlocal submit_boundary_crossed
            if not submit_boundary_crossed:
                # Mark before awaiting so cancellation/failure cannot cause a later retry to rewrite an immutable
                # checkpoint. The caller must abort the whole request when this hook fails.
                submit_boundary_crossed = True
                if before_submit is not None:
                    await before_submit()
            return await build_and_call(compressed)

        if not specs:
            return await _call_once([])

        limits = await self._reference_limits(provider_id)
        step = 0
        while True:
            # 压缩是 CPU 密集的 PIL 解码/编码 + 写盘，放进线程避免阻塞 worker 事件循环
            # （心跳 / SSE / 另一并发通道）。手动驱动上下文管理器：__enter__（含压缩）走线程，
            # __exit__（清理临时目录，轻量）留在循环里。预检 floor 在 __enter__ 内抛出，此时
            # 尚未进入 try，临时目录也未创建（select_ladder_step 先于写盘），无需清理、直接冒泡。
            cm = compressed_reference_payload(specs, limits=limits, start_step=step)
            landed, compressed = await asyncio.to_thread(cm.__enter__)
            try:
                return await _call_once(compressed)
            except Exception as e:
                if not _is_413(e) or (provider_resubmit_is_unsafe is not None and provider_resubmit_is_unsafe()):
                    raise
                # 从「实际落定档位 landed」续档，而非请求值 step——主动预检可能已因字节超限
                # 降到 landed>step，必须 landed+1 才严格更小、保证降档单调。
                if landed < LADDER_STEPS:
                    step = landed + 1
                    continue
                # 已在地板仍 413 → 耗尽 → 用户可见硬错误（保 413 cause）
                raise ReferencePayloadFloorError() from e
            finally:
                cm.__exit__(None, None, None)

    def generate_image(
        self,
        prompt: str,
        resource_type: str,
        resource_id: str,
        reference_images=None,
        aspect_ratio: str = "9:16",
        image_size: str | None = None,
        **version_metadata,
    ) -> tuple[Path, int]:
        """
        生成图片（带自动版本管理，同步包装）

        Args:
            prompt: 图片生成提示词
            resource_type: 资源类型 (storyboards, characters, clues)
            resource_id: 资源 ID (E1S01, 姜月茴, 玉佩)
            reference_images: 参考图片列表
            aspect_ratio: 宽高比，默认 9:16（竖屏）
            image_size: 图片尺寸，默认不传（由 backend/SDK 决定）
            **version_metadata: 额外元数据

        Returns:
            (output_path, version_number) 元组
        """
        return self._sync(
            self.generate_image_async(
                prompt=prompt,
                resource_type=resource_type,
                resource_id=resource_id,
                reference_images=reference_images,
                aspect_ratio=aspect_ratio,
                image_size=image_size,
                **version_metadata,
            )
        )

    async def generate_image_async(
        self,
        prompt: str,
        resource_type: str,
        resource_id: str,
        reference_images=None,
        aspect_ratio: str = "9:16",
        image_size: str | None = None,
        **version_metadata,
    ) -> tuple[Path, int]:
        """
        异步生成图片（带自动版本管理）

        Args:
            prompt: 图片生成提示词
            resource_type: 资源类型 (storyboards, characters, clues)
            resource_id: 资源 ID (E1S01, 姜月茴, 玉佩)
            reference_images: 参考图片列表
            aspect_ratio: 宽高比，默认 9:16（竖屏）
            image_size: 图片尺寸，默认不传（由 backend/SDK 决定）
            **version_metadata: 额外元数据

        Returns:
            (output_path, version_number) 元组
        """
        from lib.image_backends.base import ImageGenerationRequest, ReferenceImage

        output_path = self._get_output_path(resource_type, resource_id)
        self._ensure_parent_dir(output_path)

        # 1. 若已存在，确保旧文件被记录
        if output_path.exists():
            self.versions.ensure_current_tracked(
                resource_type=resource_type,
                resource_id=resource_id,
                current_file=output_path,
                prompt=prompt,
                aspect_ratio=aspect_ratio,
                **version_metadata,
            )

        if self._image_backend is None:
            raise RuntimeError("image_backend not configured")

        # 先归一化 reference_images，PIL 等不支持的类型在此被丢弃，
        # 因此 capability 判定要基于归一化后的结果，避免「传了无效引用图」被
        # 误判为 I2I 后又落到 T2I 调用，造成 image_capability_missing_i2i 误报。
        from lib.image_backends.base import ImageCapability, ImageCapabilityError

        ref_images: list[ReferenceImage] = []
        if reference_images:
            for ref in reference_images:
                if isinstance(ref, dict):
                    img_val = ref.get("image", "")
                    ref_images.append(
                        ReferenceImage(
                            path=str(img_val),
                            label=str(ref.get("label", "")),
                        )
                    )
                elif hasattr(ref, "__fspath__") or isinstance(ref, (str, Path)):
                    ref_images.append(ReferenceImage(path=str(ref)))
                # PIL Image 等不支持的类型忽略

        # Capability gating：上层 resolver 应当已经选到对的 backend，
        # 这里是兜底（防御调用方手工拼 backend 或配置漂移）。
        needed = ImageCapability.IMAGE_TO_IMAGE if ref_images else ImageCapability.TEXT_TO_IMAGE
        if needed not in self._image_backend.capabilities:
            raise ImageCapabilityError(
                "image_capability_missing_i2i"
                if needed == ImageCapability.IMAGE_TO_IMAGE
                else "image_capability_missing_t2i",
                provider=self._image_backend.name,
                model=self._image_backend.model,
            )

        # 2. 记账括号：进入落 pending，成功以 call.success(result) 递交 backend 结果对象，
        #    Exception 自动翻 failed 后重抛，CancelledError 穿透留 pending。
        async with self.ledger.record(
            project_name=self.project_name,
            call_type="image",
            model=self._image_backend.model,
            prompt=prompt,
            resolution=image_size,
            aspect_ratio=aspect_ratio,
            # 记账 provider 取解析层 provider_id；成对不变量保证 backend 非 None 时 provider_id 亦非 None。
            provider=cast(str, self._image_provider_id),
            user_id=self._user_id,
            segment_id=segment_id_for("image", resource_type, resource_id),
            output_path=str(output_path),
        ) as call:
            from lib.reference_compression import ReferenceSpec, RefRole

            image_backend = self._image_backend
            # 所有图像参考图都走数组角色（完整基线 + 降档梯子 + 字节预算）。
            specs = [ReferenceSpec(source=Path(r.path), label=r.label, role=RefRole.ARRAY) for r in ref_images]

            def _call_image(compressed: "list[CompressedRef]"):
                return image_backend.generate(
                    ImageGenerationRequest(
                        prompt=prompt,
                        output_path=output_path,
                        reference_images=[ReferenceImage(path=str(c.path), label=c.label) for c in compressed],
                        aspect_ratio=aspect_ratio,
                        image_size=image_size,
                        project_name=self.project_name,
                    )
                )

            result = await self._run_with_reference_compression(
                specs=specs,
                provider_id=self._image_provider_id,
                build_and_call=_call_image,
            )
            call.success(result)

        # 5. 记录新版本
        new_version = self.versions.add_version(
            resource_type=resource_type,
            resource_id=resource_id,
            prompt=prompt,
            source_file=output_path,
            aspect_ratio=aspect_ratio,
            **version_metadata,
        )

        return output_path, new_version

    async def generate_audio_async(
        self,
        text: str,
        resource_id: str,
        voice: str,
        language_type: str = "Chinese",
        speed: float | None = None,
        before_commit: Callable[[Path], Awaitable[None]] | None = None,
        commit_staged: Callable[[Path, Path], int | PaidVersionCommit] | None = None,
        **version_metadata,
    ) -> tuple[Path, int]:
        """
        异步合成语音（带自动版本管理）

        与图片/视频不同，TTS 后端是同步调用（无 submit-poll-resume），逻辑最简。

        Args:
            text: 待合成文本（旁白原文）
            resource_id: 资源 ID（segment，如 E1S01）
            voice: 音色（如 Cherry）
            language_type: 语种，默认 Chinese
            speed: 语速预留（同步模型忽略）
            **version_metadata: 额外元数据

        Returns:
            (output_path, version_number) 元组
        """
        from lib.audio_backends.base import AudioSynthesisRequest

        resource_type = "audio"
        output_path = self._get_output_path(resource_type, resource_id)
        self._ensure_parent_dir(output_path)

        if self._audio_backend is None:
            raise RuntimeError("audio_backend not configured")

        # 后端只写同目录唯一 staging 文件。只有同步合成完整成功且确实产出普通文件后，
        # 才以原子 replace 提升为 canonical；失败或取消均不会触碰旧付费音频。
        fd, staged_name = tempfile.mkstemp(
            prefix=f".{output_path.stem}.",
            suffix=output_path.suffix,
            dir=output_path.parent,
        )
        os.close(fd)
        staged_path = Path(staged_name)
        staged_path.unlink()

        # audio 合成字符数 → 计费 token 的语义转写已收进 ledger union 分发（_settlement_from_result），
        # 此处仅把 backend 结果对象递交给 call.success。
        try:
            async with self.ledger.record(
                project_name=self.project_name,
                call_type="audio",
                model=self._audio_backend.model,
                prompt=text,
                # 记账 provider 取解析层 provider_id；成对不变量保证 backend 非 None 时 provider_id 亦非 None。
                provider=cast(str, self._audio_provider_id),
                user_id=self._user_id,
                segment_id=segment_id_for("audio", resource_type, resource_id),
                output_path=str(output_path),
            ) as call:
                request = AudioSynthesisRequest(
                    text=text,
                    output_path=staged_path,
                    voice=voice,
                    language_type=language_type,
                    speed=speed,
                )
                result = await self._audio_backend.synthesize(request)
                if not staged_path.is_file():
                    raise RuntimeError("audio backend completed without a regular output file")
                call.success(result)

            if before_commit is not None:
                await before_commit(staged_path)
            commit = commit_staged
            selected_current = True
            if commit is None:
                version = self.versions.commit_staged_version(
                    resource_type=resource_type,
                    resource_id=resource_id,
                    prompt=text,
                    staged_file=staged_path,
                    current_file=output_path,
                    **version_metadata,
                )
            else:
                committed = commit(staged_path, output_path)
                if isinstance(committed, PaidVersionCommit):
                    version = committed.version
                    selected_current = committed.selected
                else:
                    version = committed
            if selected_current and not output_path.is_file():
                raise RuntimeError("audio commit completed without a regular formal output file")
            return output_path, version
        finally:
            staged_path.unlink(missing_ok=True)

    def generate_video(
        self,
        prompt: str,
        resource_type: str,
        resource_id: str,
        start_image: str | Path | Image.Image | None = None,
        end_image: Path | None = None,
        reference_images: list[Path] | None = None,
        reference_audio_files: list[Path] | None = None,
        reference_audio_targets: list[int] | None = None,
        aspect_ratio: str = "9:16",
        duration_seconds: str | int = "8",
        resolution: str | None = None,
        **version_metadata,
    ) -> tuple[Path, int, Any, str | None]:
        """
        生成视频（带自动版本管理，同步包装）

        Args:
            prompt: 视频生成提示词（含统一文本化的反向提示词，由 prompt_builders 在上游拼好）
            resource_type: 资源类型 (videos)
            resource_id: 资源 ID (E1S01)
            start_image: 起始帧图片（image-to-video 模式）
            end_image: 结束帧图片（first_last 模式）
            reference_images: 参考图片列表（multi-reference 模式）
            reference_audio_files: 参考音频列表（音色复刻），顺序即 prompt 中「音频N」的指认顺序
            reference_audio_targets: 与 reference_audio_files 等长同序，第 i 项是该段音频对应
                reference_images 的下标（0-based）；仅 backend 声明 reference_audio_per_image
                时读取，见 VideoGenerationRequest.reference_audio_targets
            aspect_ratio: 宽高比，默认 9:16（竖屏）
            duration_seconds: 视频时长，可选 "4", "6", "8"
            resolution: 分辨率，默认不传（由 backend/SDK 决定）
            **version_metadata: 额外元数据

        Returns:
            (output_path, version_number, video_ref, video_uri) 四元组
        """
        return self._sync(
            self.generate_video_async(
                prompt=prompt,
                resource_type=resource_type,
                resource_id=resource_id,
                start_image=start_image,
                end_image=end_image,
                reference_images=reference_images,
                reference_audio_files=reference_audio_files,
                reference_audio_targets=reference_audio_targets,
                aspect_ratio=aspect_ratio,
                duration_seconds=duration_seconds,
                resolution=resolution,
                **version_metadata,
            )
        )

    async def generate_video_async(
        self,
        prompt: str,
        resource_type: str,
        resource_id: str,
        start_image: str | Path | Image.Image | None = None,
        end_image: Path | None = None,
        reference_images: list[Path] | None = None,
        reference_audio_files: list[Path] | None = None,
        reference_audio_targets: list[int] | None = None,
        aspect_ratio: str = "9:16",
        duration_seconds: str | int = "8",
        resolution: str | None = None,
        task_id: str | None = None,
        before_submit: Callable[[int], Awaitable[Mapping[str, object] | None]] | None = None,
        formal_output: bool = False,
        before_formal_commit: Callable[[Path, int, Mapping[str, Any]], Awaitable[None]] | None = None,
        commit_formal_output: Callable[[Path, Path, int, Mapping[str, Any]], PaidVersionCommit] | None = None,
        **version_metadata,
    ) -> tuple[Path, int, Any, str | None]:
        """
        异步生成视频（带自动版本管理）

        Args:
            prompt: 视频生成提示词（含统一文本化的反向提示词，由 prompt_builders 在上游拼好）
            resource_type: 资源类型 (videos)
            resource_id: 资源 ID (E1S01)
            start_image: 起始帧图片（image-to-video 模式）
            end_image: 结束帧图片（first_last 模式）
            reference_images: 参考图片列表（multi-reference 模式）
            reference_audio_files: 参考音频列表（音色复刻），顺序即 prompt 中「音频N」的指认顺序
            reference_audio_targets: 与 reference_audio_files 等长同序，第 i 项是该段音频对应
                reference_images 的下标（0-based）；仅 backend 声明 reference_audio_per_image
                时读取，见 VideoGenerationRequest.reference_audio_targets
            aspect_ratio: 宽高比，默认 9:16（竖屏）
            duration_seconds: 视频时长，可选 "4", "6", "8"
            resolution: 分辨率，默认不传（由 backend/SDK 决定）
            before_submit: 首次 provider 提交紧前执行一次的异步持久化钩子；
                返回值并入当次版本元数据
            formal_output: 将 provider 产物先写入同目录临时文件，成功后再与版本历史一起提交
            **version_metadata: 额外元数据

        Returns:
            (output_path, version_number, video_ref, video_uri) 四元组
        """
        output_path = self._get_output_path(resource_type, resource_id)
        self._ensure_parent_dir(output_path)

        # 先把 duration 归一为 int：上游可能传 "8.0" 浮点字符串，直接 int("8.0") 会 ValueError
        # 走兜底分支静默掉真实值（"10.0" 会被吞成 8）。先 float() 再 int() 保留语义。
        # 提前到所有 ensure_current_tracked / add_version / VideoGenerationRequest 之前，
        # 让版本元数据与 provider 请求里的 duration_seconds 类型一致（都是 int），
        # 避免 versions.json 落字符串而 ApiCall 落 int 的类型漂移。
        try:
            duration_int = int(float(duration_seconds)) if duration_seconds else 8
        except (ValueError, TypeError):
            duration_int = 8

        # 1. 若已存在，确保旧文件被记录。这里的 prompt / duration / provider 选项都属于即将
        # 发起的新请求，不能写到来源不明的 legacy current 上；否则新产物被拒绝回滚后，旧视频
        # 会冒充新请求档位，后续被错误地当作可复用成片。未知事实保持未知，新产物在 add_version
        # 时再登记完整请求元数据。
        if output_path.exists() and not formal_output:
            self.versions.ensure_current_tracked(
                resource_type=resource_type,
                resource_id=resource_id,
                current_file=output_path,
                prompt="",
            )

        if self._video_backend is None:
            raise RuntimeError("video_backend not configured")

        # 空串 end_image 归一为 None：遗留/直接 Python 调用者可能以 "" 表示无尾帧
        # （kling _build_payload 的真值判断锁定了这个兼容语义，见
        # tests/test_kling_video_backend.py::test_image2video_empty_end_frame_is_omitted）。
        # 下方 gating 用 is not None 判空，"" 若不归一会被误判成真尾帧。
        if not end_image:
            end_image = None

        model_name = self._video_backend.model

        # 能力校验与槽位组装先于记账括号：能力不被支持时硬失败要"不扣费"，
        # 在括号内抛虽也不结算，却会留一条 failed ApiCall 行；两者均无副作用，前置最干净。
        from lib.video_frame_slots import (
            gate_video_request,
            plan_frame_slots,
            resolve_first_frame_aspect_ratio,
            resolve_video_capabilities,
        )

        # prompt 长度校验对每个请求都适用（不像尾帧/参考图/参考音频那样可选），故能力查询不再
        # 按可选路径惰性触发。查询是纯读后端声明的同步调用，没有 I/O 开销。
        video_caps = resolve_video_capabilities(
            self._video_backend,
            service_tier=version_metadata.get("service_tier", "default"),
            resolution=resolution,
        )
        # 总时长校验需要读音频元数据，只能在这层（拿得到文件路径）探测好再传给纯函数的
        # gate_video_request；探测失败（ffprobe 不可用等）按 None 传入，由其按既有降级口径跳过
        # 该项校验，不阻断请求。仅当 caps 声明了总时长约束才探测——未声明该约束的后端
        # （如 wan2.7）不必为每个请求多付一轮 ffprobe 子进程开销。
        reference_audio_total_seconds = (
            await probe_reference_audio_total_seconds(reference_audio_files)
            if reference_audio_files
            and video_caps is not None
            and video_caps.max_reference_audio_total_seconds is not None
            else None
        )
        gate_video_request(
            caps=video_caps,
            provider=self._video_backend.name,
            model=model_name,
            prompt=prompt,
            end_image=end_image,
            reference_images=reference_images,
            reference_audio_files=reference_audio_files,
            reference_audio_total_seconds=reference_audio_total_seconds,
        )
        slot_plan = plan_frame_slots(
            start_image=start_image,
            end_image=end_image,
            reference_images=reference_images,
        )
        # 仅声明 first_frame_ratio_adaptive_only 的后端受影响；下发值与调用方持有的原始
        # aspect_ratio（记账、分镜图生成沿用）分离，不回写覆盖上游变量。
        request_aspect_ratio = resolve_first_frame_aspect_ratio(
            caps=video_caps,
            aspect_ratio=aspect_ratio,
            has_first_frame=slot_plan.start_index is not None,
        )

        if self._config is not None:
            configured_generate_audio = await self._config.video_generate_audio(self.project_name)
        else:
            from lib.config.resolver import ConfigResolver

            configured_generate_audio = ConfigResolver._DEFAULT_VIDEO_GENERATE_AUDIO
        effective_generate_audio = version_metadata.get("generate_audio", configured_generate_audio)

        staged_output_path, backend_output_path = self._prepare_video_output(
            output_path,
            formal_output=formal_output,
            task_id=task_id,
        )
        if before_formal_commit is not None and staged_output_path is None:
            raise ValueError("before_formal_commit requires formal video output")

        # video 实际计费时长（result.duration_seconds）覆盖请求时长的语义转写已收进 ledger union
        # 分发（_settlement_from_result），此处仅递交 backend 结果对象。
        video_ref = None
        video_uri: str | None = None
        async with (
            _remove_staged_output_on_error(staged_output_path),
            self.ledger.record(
                project_name=self.project_name,
                call_type="video",
                model=model_name,
                prompt=prompt,
                resolution=resolution,
                duration_seconds=duration_int,
                aspect_ratio=aspect_ratio,
                generate_audio=effective_generate_audio,
                # 记账 provider 取解析层 provider_id；成对不变量保证 backend 非 None 时 provider_id 亦非 None。
                provider=cast(str, self._video_provider_id),
                user_id=self._user_id,
                segment_id=segment_id_for("video", resource_type, resource_id),
                service_tier=version_metadata.get("service_tier", "default"),
                output_path=str(output_path),
            ) as call,
        ):
            # 拿到 call_id 后立即写入 task.payload["api_call_id"]，让 worker 崩溃重启后 resume
            # 路径能精准翻这条 pending ApiCall 行（而不是按 segment_id+LIMIT 1 模糊匹配）。
            # fail-fast 抛异常会被记账括号翻 pending → failed 后再重抛，避免留下永久 pending
            # 账目（ADR 0007）；放在 backend 调用前是必须的。
            if task_id is not None:
                from lib.video_backends.base import persist_api_call_id

                await persist_api_call_id(task_id, call.call_id)

            from lib.video_backends.base import VideoGenerationRequest

            video_backend = self._video_backend
            # FRAME（start/end 帧，永不缩尺寸）+ ARRAY（参考数组，完整梯子）按已知序位组织成
            # specs（见 lib/video_frame_slots.py），压缩后按 index 还原回三个请求字段。
            specs = slot_plan.specs
            start_spec_idx = slot_plan.start_index
            end_spec_idx = slot_plan.end_index
            ref_start_idx = slot_plan.reference_start_index
            provider_resubmit_unsafe = False

            def _mark_provider_resubmit_unsafe() -> None:
                nonlocal provider_resubmit_unsafe
                provider_resubmit_unsafe = True

            def _call_video(compressed: "list[CompressedRef]"):
                start_arg = compressed[start_spec_idx].path if start_spec_idx is not None else None
                end_arg = compressed[end_spec_idx].path if end_spec_idx is not None else None
                # 数组参考图恒在 specs 末段（append start/end 之后），故 [ref_start_idx:] 精确取它们；
                # 无可压缩数组项时回落原 reference_images（保留 None / [] 语义）。
                ref_arg = (
                    [c.path for c in compressed[ref_start_idx:]] if ref_start_idx is not None else reference_images
                )
                return video_backend.generate(
                    VideoGenerationRequest(
                        prompt=prompt,
                        output_path=backend_output_path,
                        aspect_ratio=request_aspect_ratio,
                        duration_seconds=duration_int,
                        resolution=resolution,
                        start_image=start_arg,
                        end_image=end_arg,
                        reference_images=ref_arg,
                        # 音频不进压缩器（specs 只收图片），故直接透传原列表：顺序即 prompt
                        # 「音频N」的指认顺序，任何重排都会把 A 角色的音色安到 B 角色头上。
                        reference_audio_files=reference_audio_files,
                        # 数组参考图压缩保序（不改数量、不重排），故调用方按未压缩的
                        # reference_images 下标算出的 targets 对压缩后的 ref_arg 同样有效。
                        reference_audio_targets=reference_audio_targets,
                        generate_audio=effective_generate_audio,
                        project_name=self.project_name,
                        task_id=task_id,
                        on_provider_resubmit_unsafe=_mark_provider_resubmit_unsafe,
                        service_tier=version_metadata.get("service_tier", "default"),
                        seed=version_metadata.get("seed"),
                    )
                )

            async def _before_first_submit() -> None:
                if before_submit is not None:
                    checkpoint_metadata = await before_submit(call.call_id)
                    if checkpoint_metadata is not None:
                        version_metadata.update(checkpoint_metadata)

            try:
                result = await self._run_with_reference_compression(
                    specs=specs,
                    provider_id=self._video_provider_id,
                    build_and_call=_call_video,
                    before_submit=_before_first_submit if before_submit is not None else None,
                    provider_resubmit_is_unsafe=lambda: provider_resubmit_unsafe,
                )
            except BaseException:
                if staged_output_path is not None:
                    staged_output_path.unlink(missing_ok=True)
                raise
            video_uri = result.video_uri
            call.success(result)

        if before_formal_commit is not None:
            assert staged_output_path is not None
            await before_formal_commit(staged_output_path, duration_int, version_metadata)

        # 5. 记录新版本
        committed = self._commit_video_output_version(
            resource_type=resource_type,
            resource_id=resource_id,
            prompt=prompt,
            output_path=output_path,
            staged_output_path=staged_output_path,
            duration_seconds=duration_int,
            version_metadata=version_metadata,
            commit_formal_output=commit_formal_output,
        )

        return output_path, committed.version, video_ref, video_uri

    async def resume_video_async(
        self,
        *,
        job_id: str,
        resource_type: str,
        resource_id: str,
        prompt: str = "",
        aspect_ratio: str = "9:16",
        duration_seconds: str | int = "8",
        resolution: str | None = None,
        task_id: str | None = None,
        api_call_id: int | None = None,
        submitted_base_url: str | None = None,
        formal_output: bool = False,
        before_formal_commit: Callable[[Path, int, Mapping[str, Any]], Awaitable[None]] | None = None,
        commit_formal_output: Callable[[Path, Path, int, Mapping[str, Any]], PaidVersionCommit] | None = None,
        **version_metadata,
    ) -> tuple[Path, int, Any, str | None]:
        """接续 provider 上已发起的 video job：调 backend.resume_video 而非 generate。

        与 generate_video_async 的差异：
        - 不开记账括号（不落新 pending 行）—— 首次 submit 已记账；ResumeExpired / crash window
          都不应再写 ApiCall（防双重扣费）。caller 透传 ``api_call_id`` 时经 ledger.resume_success
          / resume_failed 按 call_id 精准翻 pending → success/failed；不透传则 logger.warning 不阻断。
        - resume 成功后总是记录新版本；``formal_output=True`` 时先下载到
          同目录临时文件，再与版本历史一起提交，不提前覆盖 current。
        - prompt / start_image / reference_images 仅用于日志/版本元数据，不影响 provider 端结果。
        - ``submitted_base_url`` 是具名参数而非 version_metadata：它是提交时域名的回放值、
          只喂给 backend 轮询，落进版本元数据会污染 versions.json。

        Returns: (output_path, version_number, video_ref, video_uri) 四元组。
        """
        output_path = self._get_output_path(resource_type, resource_id)
        self._ensure_parent_dir(output_path)

        # 先把 duration 归一为 int：上游可能传 "8.0" 浮点字符串，直接 int("8.0") 会 ValueError
        # 走兜底分支静默掉真实值（"10.0" 会被吞成 8）。先 float() 再 int() 保留语义。
        # 提前到 VideoGenerationRequest / add_version 之前，让版本元数据
        # 与 provider 请求里的 duration_seconds 类型一致（都是 int，避免 versions.json 落字符串）。
        try:
            duration_int = int(float(duration_seconds)) if duration_seconds else 8
        except (ValueError, TypeError):
            duration_int = 8

        if self._video_backend is None:
            raise RuntimeError("video_backend not configured")

        if self._config is not None:
            configured_generate_audio = await self._config.video_generate_audio(self.project_name)
        else:
            from lib.config.resolver import ConfigResolver

            configured_generate_audio = ConfigResolver._DEFAULT_VIDEO_GENERATE_AUDIO
        effective_generate_audio = version_metadata.get("generate_audio", configured_generate_audio)

        staged_output_path, backend_output_path = self._prepare_video_output(
            output_path,
            formal_output=formal_output,
            task_id=task_id,
        )
        if before_formal_commit is not None and staged_output_path is None:
            raise ValueError("before_formal_commit requires formal video output")

        from lib.video_backends.base import ResumeExpiredError, VideoGenerationRequest

        request = VideoGenerationRequest(
            prompt=prompt,
            output_path=backend_output_path,
            aspect_ratio=aspect_ratio,
            duration_seconds=duration_int,
            resolution=resolution,
            generate_audio=effective_generate_audio,
            project_name=self.project_name,
            task_id=task_id,
            service_tier=version_metadata.get("service_tier", "default"),
            seed=version_metadata.get("seed"),
            submitted_base_url=submitted_base_url,
        )

        try:
            result = await self._video_backend.resume_video(job_id, request)
        except ResumeExpiredError:
            # Pending ApiCall 翻 failed 而不是留 pending：让 /api/v1/usage 报表不堆积无终态行；
            # cost_amount=0 不增加计费（resume 不重扣，符合 "不主动扣费" 红线）。
            # finalize 失败时不吞异常，让 worker finally 走 mark_failed 兜底，避免 ApiCall
            # 永久卡 pending 导致 usage 报表/补账缺口（与 persist_api_call_id 的 fail-fast 一致）。
            async with _remove_staged_output_on_error(staged_output_path):
                if api_call_id is not None:
                    await self.ledger.resume_failed(call_id=api_call_id)
                raise
        except asyncio.CancelledError:
            if staged_output_path is not None:
                staged_output_path.unlink(missing_ok=True)
            raise
        except Exception:
            logger.exception("resume 失败 (video) task_id=%s job_id=%s", task_id, job_id)
            if staged_output_path is not None:
                staged_output_path.unlink(missing_ok=True)
            raise

        video_ref = None
        video_uri = result.video_uri

        # Resume 成功：精准翻 pending → success。ledger.resume_success 收 backend 结果对象，
        # 与视频通道成功分支同源做 union 分发（usage_tokens / generate_audio / 实际计费时长），
        # cost 由 repo 按 ApiCall 行字段自动算——与 generate 路径记账等价，避免视频已生成但账本
        # 永久漏记。service_tier 由 caller 透传（ApiCall 模型无该列），让非 default 档位按真实档
        # 计费。finalize 自身异常不吞，交 worker finally 兜底；WHERE status='pending' 保护幂等性。
        if api_call_id is not None:
            async with _remove_staged_output_on_error(staged_output_path):
                await self.ledger.resume_success(
                    call_id=api_call_id,
                    result=result,
                    service_tier=version_metadata.get("service_tier", "default"),
                )
        else:
            logger.warning(
                "resume 缺 api_call_id task_id=%s job_id=%s (旧任务未持久化 payload)",
                task_id,
                job_id,
            )

        # backend.resume_video 已下载新视频并覆盖 output_path，必须 bump 一个新版本号：
        # - versions.json 空时（submit→poll 中崩）add_version 直接登记 v1，避免下游 versions[-1] IndexError；
        # - versions.json 已有 v_n（覆盖式重新生成）时 add_version 登记 v_(n+1)，避免 output_path
        #   被新内容覆盖却仍报旧版本号导致 versions.json 与磁盘文件错位。
        if before_formal_commit is not None:
            assert staged_output_path is not None
            await before_formal_commit(staged_output_path, duration_int, version_metadata)

        committed = self._commit_video_output_version(
            resource_type=resource_type,
            resource_id=resource_id,
            prompt=prompt,
            output_path=output_path,
            staged_output_path=staged_output_path,
            duration_seconds=duration_int,
            version_metadata=version_metadata,
            commit_formal_output=commit_formal_output,
        )

        return output_path, committed.version, video_ref, video_uri
