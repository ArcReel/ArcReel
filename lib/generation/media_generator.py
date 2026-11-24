"""
MediaGenerator 中间层

封装 GeminiClient + VersionManager，提供"调用方无感"的版本管理。
调用方只需传入 project_path 和 resource_id，版本管理自动完成。

覆盖的资源类型：
- storyboards: 分镜图 (scene_E1S01.png)
- videos: 视频 (scene_E1S01.mp4)
- characters: 角色资产图 (姜月茴.png)
- scenes: 场景资产图 (庙宇.png)
- props: 道具资产图 (玉佩.png)
- grids: 宫格图 (grid_xxx.png)
"""

import asyncio
import hashlib
import logging
import os
import tempfile
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, cast

from PIL import Image

if TYPE_CHECKING:
    from arcreel_market_core.video_backend_contract import VideoAudioMode, VideoGenerationResult
    from lib.asset_library.generation_inputs import ReferenceAssetChoice, ReferenceImageSource
    from lib.backends.audio_backends.base import AudioBackend
    from lib.backends.image_backends.base import ImageBackend
    from lib.config.resolver import ConfigResolver
    from lib.references.reference_compression import CompressedRef, PayloadLimits, ReferenceSpec

from lib.artifacts.version_manager import PaidVersionCommit, VersionManager
from lib.backends.gemini_shared import RateLimiter
from lib.backends.providers import CallPurpose, CallType, require_provider_pair
from lib.billing.ledger import Ledger
from lib.config.service import DEFAULT_VIDEO_POLL_TIMEOUT_SECONDS
from lib.db.base import DEFAULT_USER_ID
from lib.generation.deleted_target import trash_result_if_target_deleted
from lib.generation.video_request_facts import DEFAULT_PLANNED_DURATION_SECONDS
from lib.infra.async_thread import run_noninterruptible_sync, run_sync_transaction
from lib.infra.path_safety import PathTraversalError, safe_join
from lib.project.resource_paths import resource_relative_path
from lib.speech.audio_utils import probe_reference_audio_total_seconds

logger = logging.getLogger(__name__)


def task_media_staging_path(output_path: Path, task_id: str) -> Path:
    """Return the bounded, deterministic formal-output path owned by one task."""

    if not task_id:
        raise ValueError("formal media output requires a task_id")
    token = hashlib.sha256(task_id.encode("utf-8")).hexdigest()
    return output_path.with_name(f".{token}.task-output{output_path.suffix}")


def _is_junction(path: Path) -> bool:
    isjunction = getattr(os.path, "isjunction", None)
    return bool(isjunction is not None and isjunction(path))


def task_video_staging_path(output_path: Path, task_id: str) -> Path:
    """Compatibility name for video task staging."""

    return task_media_staging_path(output_path, task_id)


def task_image_staging_path(output_path: Path, task_id: str) -> Path:
    """Return the bounded staging path for one formal image task."""

    return task_media_staging_path(output_path, task_id)


def _remove_task_staging_path(path: Path) -> None:
    """Remove a task-owned staging entry without traversing links or arbitrary directories."""

    if path.is_symlink():
        path.unlink(missing_ok=True)  # hard-delete: temp -- 生成任务的暂存输出
    elif _is_junction(path):
        path.rmdir()  # hard-delete: temp -- 生成任务的暂存目录
    elif os.path.lexists(path):
        if not path.is_file():
            raise ValueError("formal media staging path is not a regular file")
        path.unlink()  # hard-delete: temp -- 生成任务的暂存输出


_remove_task_video_staging_path = _remove_task_staging_path


def cleanup_staged_video_output(
    project_path: Path,
    resource_type: str,
    resource_id: str,
    task_id: str,
) -> None:
    """Remove the deterministic interrupted output owned by one video task."""

    output_path = safe_join(project_path, resource_relative_path(resource_type, resource_id))
    _remove_task_video_staging_path(task_video_staging_path(output_path, task_id))


async def _discard_staged_output(path: Path | None) -> None:
    """在工作线程里删除 staging 文件，删除结算完再返回。

    只用在收尾与失败路径上：不引入新的取消点，成功路径照常返回结果，失败路径照常传播原异常。
    """

    if path is not None:
        await run_noninterruptible_sync(path.unlink, missing_ok=True)  # hard-delete: temp -- 生成任务的暂存输出


@asynccontextmanager
async def _remove_staged_output_on_error(path: Path | None):
    """Remove a formal-output staging file whenever the guarded operation aborts."""

    try:
        yield
    except BaseException:
        await _discard_staged_output(path)
        raise


def _is_413(exc: BaseException) -> bool:
    """识别请求体超限（HTTP 413）。

    先从异常通用属性提取状态码：``status_code``（OpenAI/xai SDK + 规整后的 vidu/dashscope）/
    ``response.status_code``（httpx.HTTPStatusError）/ ``code``（google-genai APIError）——
    覆盖默认 provider gemini 及各 SDK 类后端，而非只认 httpx（与 lib/backends/video_backends/ark.py 的
    状态码提取口径一致）。状态码缺失时退回短语匹配，但不用裸 "413" 子串——避免被字节数 /
    请求 ID（如 "41300 bytes"）误命中。
    """
    if getattr(getattr(exc, "request", None), "method", None) in {"GET", "HEAD"}:
        return False
    status = (
        getattr(exc, "status_code", None)
        or getattr(getattr(exc, "response", None), "status_code", None)
        or getattr(exc, "code", None)
    )
    try:
        if status is not None and int(status) == 413:
            return True
    except (ValueError, TypeError):
        pass
    msg = str(exc).lower()
    return "request entity too large" in msg or "payload too large" in msg


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


def _project_relative_path(
    project_path: Path,
    value: object,
    reference_sources: Mapping[str, Path] | None = None,
    *,
    external_name_only: bool = False,
) -> str | None:
    """把调用记录里的一个文件路径（输入素材或产物）归一为项目内相对路径（POSIX 分隔符）。

    读侧要拿它在项目里定位文件，且数据根挪位后记录仍须有效，故一律相对项目根；
    冻结副本先按 reference_sources 还原来源。项目外路径默认保留原样，external_name_only 为 True 时只保留文件名。
    非路径值（PIL Image 等）返回 None。
    """
    if not isinstance(value, (str, Path)):
        return None
    path = (reference_sources or {}).get(str(value), Path(value))
    try:
        return path.absolute().relative_to(project_path.absolute()).as_posix()
    except ValueError:
        return path.name if external_name_only else path.as_posix()


def _ledger_inputs(**sections: object) -> dict[str, Any] | None:
    """丢掉空分组后的 ``inputs`` 值；全空时给 None，让记账列留空而不是写一个空对象。"""
    kept = {key: value for key, value in sections.items() if value not in (None, [], {})}
    return kept or None


def _aligned_reference_asset_uris(
    backend: object,
    reference_images: list[Path] | None,
    asset_uris: list[str | None] | None,
    *,
    model: str,
) -> list[str | None] | None:
    """校验与参考图对齐的素材引用；没有任何一项时返回 None，请求照常只发图片内容。

    素材引用只能交给对参考图槽位声明了素材引用的 backend：其余 backend 不读这个字段，会悄悄改发图片内容。
    """
    from lib.backends.provider_assets import REFERENCE_IMAGE_ROLE, provider_asset_roles

    if asset_uris is None or not any(asset_uris):
        return None
    if len(asset_uris) != len(reference_images or []):
        raise ValueError("reference_image_asset_uris must align with reference_images")
    if REFERENCE_IMAGE_ROLE not in provider_asset_roles(backend):
        raise ValueError(f"video backend model {model} does not accept provider asset references")
    return list(asset_uris)


def _merge_result_provenance(version_metadata: dict[str, Any], result: "VideoGenerationResult") -> None:
    """把只有生成过一次才知道的事实并进版本元数据，在落盘之前。

    元数据的其余部分在提交之前就已定稿（它们是请求的一部分），而实发种子与实发 workflow 的指纹
    要到 backend 跑完才存在——ComfyUI 的 ``policy: "random"`` 是提交那一刻现随机的，不写下来这
    一版就再也复现不了。

    ``seed`` 只在 backend 报了实发值时覆盖：多数通道原样回显请求里的那一个，覆盖是恒等；回报了
    与请求不同的那些（供应商自行决定种子），写下来的才是真正生成用的。
    """
    if result.seed is not None:
        version_metadata["seed"] = result.seed
    version_metadata.update(result.provenance or {})


def _collect_result_warnings(warnings: list[dict[str, Any]] | None, result: "VideoGenerationResult") -> None:
    """把 backend 执行期产生的提示并进调用方的收集器，交由它落到任务 ``result.warnings``。

    收集器由调用方传入而不是从这里返回：视频两条路的返回值都是定死的四元组，再加一位会波及每一个
    调用点与一批用例，而调用方本就各自持有一张要落进结果的 warning 列表。
    """
    if warnings is None or not result.warnings:
        return
    warnings.extend(dict(warning) for warning in result.warnings)


def _commit_formal_image_output(
    commit_formal_output: Callable[[Path, Path, Mapping[str, Any]], int],
    staged_output_path: Path,
    output_path: Path,
    metadata: Mapping[str, Any],
) -> int:
    """在工作线程里校验 staging 产物、提交正式图片，提交成功后删除 staging。"""

    if not staged_output_path.is_file():
        raise RuntimeError("image backend completed without a regular staged output file")
    new_version = commit_formal_output(staged_output_path, output_path, metadata)
    if type(new_version) is not int or new_version < 1:
        raise RuntimeError("formal image commit did not return a positive version")
    staged_output_path.unlink(missing_ok=True)  # hard-delete: temp -- 提交后的暂存输出
    return new_version


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
            audio_provider_id: 音频 registry provider_id（旁白配音记账用），与 audio_backend 成对
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
        _remove_task_video_staging_path(staged_output_path)
        return staged_output_path, staged_output_path

    @staticmethod
    def _prepare_image_output(
        output_path: Path,
        *,
        formal_output: bool,
        task_id: str | None,
    ) -> tuple[Path | None, Path]:
        """Return the task staging path that the image backend may write."""

        if not formal_output:
            return None, output_path
        staging_token = task_id or f"inline-{uuid.uuid4().hex}"
        staged_output_path = task_image_staging_path(output_path, staging_token)
        _remove_task_staging_path(staged_output_path)
        return staged_output_path, staged_output_path

    def _prepare_image_target_sync(
        self,
        *,
        resource_type: str,
        resource_id: str,
        formal_output: bool,
        task_id: str | None,
        prompt: str,
        tracked_metadata: Mapping[str, Any],
    ) -> tuple[Path, Path | None, Path]:
        """在工作线程里定位图片输出：建目录、清理本任务 staging、登记未入账的旧文件。"""

        output_path = self._get_output_path(resource_type, resource_id)
        self._ensure_parent_dir(output_path)
        staged_output_path, backend_output_path = self._prepare_image_output(
            output_path,
            formal_output=formal_output,
            task_id=task_id,
        )
        if staged_output_path is None and output_path.exists():
            self.versions.ensure_current_tracked(
                resource_type=resource_type,
                resource_id=resource_id,
                current_file=output_path,
                prompt=prompt,
                **tracked_metadata,
            )
        return output_path, staged_output_path, backend_output_path

    def _prepare_video_target_sync(self, resource_type: str, resource_id: str, *, track_existing: bool) -> Path:
        """在工作线程里定位视频输出并建目录；``track_existing`` 时登记未入账的旧文件。

        这里的 prompt / duration / provider 选项都属于即将发起的新请求，不能写到来源不明的
        legacy current 上；否则新产物被拒绝回滚后，旧视频会冒充新请求档位，后续被错误地当作
        可复用成片。未知事实保持未知，新产物在 add_version 时再登记完整请求元数据。
        """

        output_path = self._get_output_path(resource_type, resource_id)
        self._ensure_parent_dir(output_path)
        if track_existing and output_path.exists():
            self.versions.ensure_current_tracked(
                resource_type=resource_type,
                resource_id=resource_id,
                current_file=output_path,
                prompt="",
            )
        return output_path

    def _prepare_audio_staging_sync(self, resource_id: str) -> tuple[Path, Path]:
        """在工作线程里定位音频输出，并保留同目录唯一的 staging 文件名（不留占位文件）。"""

        output_path = self._get_output_path("audio", resource_id)
        self._ensure_parent_dir(output_path)
        fd, staged_name = tempfile.mkstemp(
            prefix=f".{output_path.stem}.",
            suffix=output_path.suffix,
            dir=output_path.parent,
        )
        os.close(fd)
        staged_path = Path(staged_name)
        staged_path.unlink()  # hard-delete: temp -- mkstemp 占位文件
        return output_path, staged_path

    def _commit_video_output_version_sync(
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
            staged_output_path.unlink(missing_ok=True)  # hard-delete: temp -- 提交后的暂存输出

    async def _commit_video_output_version(
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
        """Run the shared normal/resume disk transaction without blocking the event loop.

        The sync transaction cannot be interrupted safely: if a process-level
        shutdown cancels this coroutine, wait for its thread before propagating so
        the committed selection is never left half-written.
        """

        return await run_noninterruptible_sync(
            self._commit_video_output_version_sync,
            resource_type=resource_type,
            resource_id=resource_id,
            prompt=prompt,
            output_path=output_path,
            staged_output_path=staged_output_path,
            duration_seconds=duration_seconds,
            version_metadata=version_metadata,
            commit_formal_output=commit_formal_output,
        )

    async def _prepare_formal_video_commit(
        self,
        *,
        resource_type: str,
        resource_id: str,
        prompt: str,
        output_path: Path,
        staged_output_path: Path | None,
        duration_seconds: int,
        version_metadata: Mapping[str, Any],
        before_formal_commit: Callable[[Path, int, Mapping[str, Any]], Awaitable[None]] | None,
    ) -> None:
        """Run paid-output validation without losing history when validation aborts."""

        if before_formal_commit is None:
            return
        if staged_output_path is None:
            raise ValueError("before_formal_commit requires formal video output")
        try:
            await before_formal_commit(staged_output_path, duration_seconds, version_metadata)
        except (Exception, asyncio.CancelledError) as failure:
            try:
                await run_noninterruptible_sync(
                    self.versions.commit_staged_paid_version,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    prompt=prompt,
                    staged_file=staged_output_path,
                    current_file=output_path,
                    select_current=False,
                    duration_seconds=duration_seconds,
                    **version_metadata,
                )
            except (Exception, asyncio.CancelledError) as archive_failure:
                failure.add_note(f"paid video history archival also failed: {archive_failure}")
            raise

    async def _trash_if_target_deleted(
        self,
        *,
        resource_type: str,
        resource_id: str,
        staged_file: Path | None,
        task_id: str | None,
        target: tuple[str, str] | None = None,
    ) -> None:
        """队列任务的产物在费用结算后、正式提交前复核目标仍在项目中，已删除时进回收站并抛出。

        见 :mod:`lib.generation.deleted_target`。没有任务身份或暂存文件的直接调用不复核。
        """
        if task_id is None or staged_file is None:
            return
        await run_noninterruptible_sync(
            trash_result_if_target_deleted,
            self.project_path,
            resource_type=resource_type,
            resource_id=resource_id,
            staged_file=staged_file,
            target=target,
        )

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
        except PathTraversalError as exc:
            raise ValueError(f"非法资源 ID: '{resource_id}'") from exc

    def _ensure_parent_dir(self, output_path: Path) -> None:
        """确保输出目录存在"""
        output_path.parent.mkdir(parents=True, exist_ok=True)

    async def _reference_limits(self, provider_id: str | None) -> "PayloadLimits":
        """解析参考上传副本的 PayloadLimits。

        无 config_resolver（零配置场景）→ 保守通用默认，不触 DB。其余情形统一交给
        ConfigResolver.reference_payload_limits：provider_id 为 None 时它内部短路返回 service
        层默认（同样不触 DB），避免在本层再引入第二份默认来源、与配置层漂移。
        """
        from lib.references.reference_compression import PayloadLimits

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
        reference_sources: Mapping[str, Path] | None = None,
    ) -> Any:
        """对参考上传副本做主动压缩 + 预检降档 + 被动 413 兜底，再调用 backend。

        build_and_call 接收按原序合并好的 CompressedRef 列表（1:1 保数、含透传项），构造 provider
        请求并返回 backend.generate 协程。无参考图时直接单次调用、不做降档（T2I/T2V 的 413 与
        参考图无关，不应被误转成 floor 错误）。
        """
        from lib.backends.input_limits import reference_image_sources
        from lib.references.reference_compression import (
            LADDER_STEPS,
            ReferencePayloadFloorError,
            compressed_reference_payload,
        )

        submit_boundary_crossed = False

        async def _call_once(compressed: "list[CompressedRef]") -> Any:
            nonlocal submit_boundary_crossed
            if not submit_boundary_crossed:
                submit_boundary_crossed = True
                if before_submit is not None:
                    await before_submit()
            names = {
                item.path: (reference_sources or {}).get(str(spec.source), spec.source)
                for spec, item in zip(specs, compressed, strict=True)
            }
            with reference_image_sources(names):
                return await build_and_call(compressed)

        if not specs:
            return await _call_once([])

        limits = await self._reference_limits(provider_id)
        step = 0
        while True:
            cm = compressed_reference_payload(specs, limits=limits, start_step=step)
            entered: list[tuple[int, list[CompressedRef]]] = []

            def _enter(cm=cm, entered=entered) -> None:
                entered.append(cm.__enter__())

            try:
                await run_sync_transaction(_enter)
                landed, compressed = entered[0]
                try:
                    return await _call_once(compressed)
                except Exception as e:
                    if not _is_413(e) or (provider_resubmit_is_unsafe is not None and provider_resubmit_is_unsafe()):
                        raise
                    if landed < LADDER_STEPS:
                        step = landed + 1
                        continue
                    raise ReferencePayloadFloorError() from e
            finally:
                if entered:
                    await run_noninterruptible_sync(cm.__exit__, None, None, None)

    async def generate_image_async(
        self,
        prompt: str,
        resource_type: str,
        resource_id: str,
        reference_images=None,
        aspect_ratio: str = "9:16",
        image_size: str | None = None,
        formal_output: bool = False,
        task_id: str | None = None,
        commit_formal_output: Callable[[Path, Path, Mapping[str, Any]], int] | None = None,
        before_submit: Callable[[], Awaitable[None]] | None = None,
        quality: str | None = None,
        reference_sources: Mapping[str, Path] | None = None,
        **version_metadata,
    ) -> tuple[Path, int]:
        """
        异步生成图片（带自动版本管理）

        Args:
            prompt: 图片生成提示词
            resource_type: 资源类型 (storyboards, characters, clues)
            resource_id: 资源 ID (E1S01, 姜月茴, 玉佩)
            reference_images: 参考图片列表
            reference_sources: 冻结图片路径到来源路径的映射，用于使用记录与错误提示
            aspect_ratio: 宽高比，默认 9:16（竖屏）
            image_size: 图片尺寸，默认不传（由 backend/SDK 决定）
            quality: 生效的画质（见 ``ImageGenerationRequest.quality``），默认不传，调用通道按分辨率档位推出
            **version_metadata: 额外元数据

        Returns:
            (output_path, version_number) 元组
        """
        from lib.backends.image_backends.base import ImageGenerationRequest, ReferenceImage

        if formal_output and commit_formal_output is None:
            raise ValueError("formal image output requires an artifact commit callback")
        output_path, staged_output_path, backend_output_path = await run_sync_transaction(
            self._prepare_image_target_sync,
            resource_type=resource_type,
            resource_id=resource_id,
            formal_output=formal_output,
            task_id=task_id,
            prompt=prompt,
            tracked_metadata={"aspect_ratio": aspect_ratio, **version_metadata},
        )

        if self._image_backend is None:
            raise RuntimeError("image_backend not configured")

        ref_images: list[ReferenceImage] = []
        for ref in reference_images or ():
            source = ref.get("image") if isinstance(ref, dict) else ref
            path = os.fspath(source) if isinstance(source, (str, os.PathLike)) else ""
            ref_images.append(ReferenceImage(path=path))

        from lib.backends.image_capabilities import gate_image_request

        await gate_image_request(
            self._image_backend,
            provider_id=cast(str, self._image_provider_id),
            reference_count=len(ref_images),
            reference_paths=((reference_sources or {}).get(str(ref.path), ref.path) for ref in ref_images),
        )

        async with _remove_staged_output_on_error(staged_output_path):
            async with self.ledger.record(
                project_name=self.project_name,
                call_type="image",
                model=self._image_backend.model,
                prompt=prompt,
                resolution=image_size,
                aspect_ratio=aspect_ratio,
                provider=cast(str, self._image_provider_id),
                user_id=self._user_id,
                segment_id=segment_id_for("image", resource_type, resource_id),
                output_path=_project_relative_path(self.project_path, output_path),
                task_id=task_id,
                purpose=CallPurpose.GENERATION_TASK,
                inputs=_ledger_inputs(
                    reference_images=[
                        {"path": rel, "label": None, "role": "array"}
                        for ref in ref_images
                        if (rel := _project_relative_path(self.project_path, ref.path, reference_sources)) is not None
                    ]
                ),
            ) as call:
                from lib.references.reference_compression import ReferenceSpec, RefRole

                image_backend = self._image_backend
                specs = [ReferenceSpec(source=Path(r.path), role=RefRole.ARRAY) for r in ref_images]

                def _call_image(compressed: "list[CompressedRef]"):
                    return image_backend.generate(
                        ImageGenerationRequest(
                            prompt=prompt,
                            output_path=backend_output_path,
                            reference_images=[ReferenceImage(path=str(c.path)) for c in compressed],
                            aspect_ratio=aspect_ratio,
                            image_size=image_size,
                            quality=quality,
                            project_name=self.project_name,
                        )
                    )

                result = await self._run_with_reference_compression(
                    specs=specs,
                    provider_id=self._image_provider_id,
                    build_and_call=_call_image,
                    reference_sources=reference_sources,
                    before_submit=before_submit,
                )
                call.success(result)

            await self._trash_if_target_deleted(
                resource_type=resource_type,
                resource_id=resource_id,
                staged_file=staged_output_path,
                task_id=task_id,
            )
            metadata: dict[str, Any] = {"aspect_ratio": aspect_ratio, **version_metadata}
            if staged_output_path is None:
                new_version = await run_noninterruptible_sync(
                    self.versions.add_version,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    prompt=prompt,
                    source_file=output_path,
                    **metadata,
                )
            else:
                assert commit_formal_output is not None
                new_version = await run_noninterruptible_sync(
                    _commit_formal_image_output,
                    commit_formal_output,
                    staged_output_path,
                    output_path,
                    metadata,
                )

        return output_path, new_version

    async def generate_audio_async(
        self,
        text: str,
        resource_id: str,
        voice: str,
        language_type: str = "Chinese",
        speed: float | None = None,
        task_id: str | None = None,
        before_submit: Callable[[], Awaitable[None]] | None = None,
        before_commit: Callable[[Path], Awaitable[None]] | None = None,
        commit_staged: Callable[[Path, Path], int | PaidVersionCommit] | None = None,
        target_resource: tuple[str, str] | None = None,
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
            task_id: 本次合成所属的生成任务；记账据此回指任务，直接调用（无队列任务）留空
            before_submit: 首次 provider 提交紧前执行一次的异步准入钩子
            target_resource: 产物所属的资源（资源类型, 资源 ID），缺省为产物自身；它已被删除时产物进回收站
            **version_metadata: 额外元数据

        Returns:
            (output_path, version_number) 元组
        """
        from lib.backends.audio_backends.base import AudioSynthesisRequest

        resource_type = "audio"
        if self._audio_backend is None:
            raise RuntimeError("audio_backend not configured")

        output_path, staged_path = await run_sync_transaction(self._prepare_audio_staging_sync, resource_id)

        try:
            async with self.ledger.record(
                project_name=self.project_name,
                call_type="audio",
                model=self._audio_backend.model,
                prompt=text,
                provider=cast(str, self._audio_provider_id),
                user_id=self._user_id,
                segment_id=segment_id_for("audio", resource_type, resource_id),
                output_path=_project_relative_path(self.project_path, output_path),
                task_id=task_id,
                purpose=CallPurpose.GENERATION_TASK,
                inputs=_ledger_inputs(
                    voice=voice,
                    parameters=_ledger_inputs(language_type=language_type, speed=speed),
                ),
            ) as call:
                request = AudioSynthesisRequest(
                    text=text,
                    output_path=staged_path,
                    voice=voice,
                    language_type=language_type,
                    speed=speed,
                )
                if before_submit is not None:
                    await before_submit()
                result = await self._audio_backend.synthesize(request)
                if not await run_noninterruptible_sync(staged_path.is_file):
                    raise RuntimeError("audio backend completed without a regular output file")
                call.success(result)

            await self._trash_if_target_deleted(
                resource_type=resource_type,
                resource_id=resource_id,
                staged_file=staged_path,
                task_id=task_id,
                target=target_resource,
            )
            if before_commit is not None:
                await before_commit(staged_path)
            commit = commit_staged
            selected_current = True
            if commit is None:
                version = await run_noninterruptible_sync(
                    self.versions.commit_staged_version,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    prompt=text,
                    staged_file=staged_path,
                    current_file=output_path,
                    **version_metadata,
                )
            else:
                committed = await run_noninterruptible_sync(commit, staged_path, output_path)
                if isinstance(committed, PaidVersionCommit):
                    version = committed.version
                    selected_current = committed.selected
                else:
                    version = committed
            if selected_current and not await run_noninterruptible_sync(output_path.is_file):
                raise RuntimeError("audio commit completed without a regular formal output file")
            return output_path, version
        finally:
            await _discard_staged_output(staged_path)

    async def choose_reference_image_assets(
        self, sources: "Sequence[ReferenceImageSource]"
    ) -> "tuple[ReferenceAssetChoice | None, ...]":
        """按视频 backend 实际使用的推理凭证，为将要发出的参考图选用供应商素材关联。

        与 ``sources`` 等长同序；可用关联给出素材引用，没有适用关联为 None。适用关联尚未可用或入库失败时抛
        :class:`~lib.asset_library.generation_inputs.ProviderAssetLinkUnavailableError`，不改发图片内容。
        """
        from lib.asset_library.generation_inputs import choose_reference_image_assets
        from lib.db import async_session_factory

        if self._video_backend is None:
            raise RuntimeError("video_backend not configured")
        return await choose_reference_image_assets(
            async_session_factory,
            backend=self._video_backend,
            project_name=self.project_name,
            project_path=self.project_path,
            sources=sources,
        )

    async def generate_video_async(
        self,
        prompt: str,
        resource_type: str,
        resource_id: str,
        start_image: str | Path | Image.Image | None = None,
        end_image: Path | None = None,
        reference_images: list[Path] | None = None,
        reference_image_asset_uris: list[str | None] | None = None,
        reference_audio_files: list[Path] | None = None,
        reference_audio_targets: list[int] | None = None,
        aspect_ratio: str = "9:16",
        duration_seconds: str | int = "8",
        resolution: str | None = None,
        poll_timeout_seconds: int = DEFAULT_VIDEO_POLL_TIMEOUT_SECONDS,
        task_id: str | None = None,
        before_submit: Callable[[], Awaitable[Mapping[str, object] | None]] | None = None,
        formal_output: bool = False,
        before_formal_commit: Callable[[Path, int, Mapping[str, Any]], Awaitable[None]] | None = None,
        commit_formal_output: Callable[[Path, Path, int, Mapping[str, Any]], PaidVersionCommit] | None = None,
        warnings: list[dict[str, Any]] | None = None,
        reference_sources: Mapping[str, Path] | None = None,
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
            reference_image_asset_uris: 与 reference_images 等长同序；第 i 项非 None 时第 i 张参考图以该供应商
                素材引用发出，不上传图片内容，也不进压缩与字节预检。只能交给对参考图槽位声明了素材引用的 backend
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
            warnings: 调用方持有的 warning 收集器；backend 执行期产生的提示就地追加进去，
                随后由调用方落进任务 ``result.warnings``
            reference_sources: 暂存素材路径到来源路径的映射，用于使用记录与错误提示
            **version_metadata: 额外元数据

        Returns:
            (output_path, version_number, video_ref, video_uri) 四元组
        """
        try:
            duration_int = int(float(duration_seconds)) if duration_seconds else DEFAULT_PLANNED_DURATION_SECONDS
        except (ValueError, TypeError):
            duration_int = DEFAULT_PLANNED_DURATION_SECONDS

        output_path = await run_sync_transaction(
            self._prepare_video_target_sync,
            resource_type,
            resource_id,
            track_existing=not formal_output,
        )

        if self._video_backend is None:
            raise RuntimeError("video_backend not configured")

        if not end_image:
            end_image = None

        model_name = self._video_backend.model

        from lib.backends.backend_assembly.specs import effective_builtin_video_capabilities
        from lib.backends.video_frame_slots import (
            gate_video_request,
            plan_frame_slots,
            resolve_first_frame_aspect_ratio,
            resolve_video_capabilities,
        )
        from lib.custom_provider import is_custom_provider

        video_caps = resolve_video_capabilities(
            self._video_backend,
            service_tier=version_metadata.get("service_tier", "default"),
            resolution=resolution,
        )
        if self._video_provider_id is not None and not is_custom_provider(self._video_provider_id):
            video_caps = effective_builtin_video_capabilities(video_caps, self._video_provider_id, model_name)
        reference_audio_total_seconds = (
            await probe_reference_audio_total_seconds(reference_audio_files)
            if reference_audio_files and video_caps.max_reference_audio_total_seconds is not None
            else None
        )
        asset_uris = _aligned_reference_asset_uris(
            self._video_backend, reference_images, reference_image_asset_uris, model=model_name
        )
        slot_plan = plan_frame_slots(
            start_image=start_image,
            end_image=end_image,
            reference_images=reference_images,
        )
        if asset_uris is not None and slot_plan.reference_start_index is not None:
            for offset, uri in enumerate(asset_uris):
                if uri is not None:
                    slot_plan.specs[slot_plan.reference_start_index + offset].provider_asset = True
        gate_video_request(
            caps=video_caps,
            provider=self._video_backend.name,
            model=model_name,
            prompt=prompt,
            has_image=bool(slot_plan.specs),
            has_first_frame=slot_plan.start_index is not None,
            end_image=end_image,
            reference_images=reference_images,
            reference_audio_files=reference_audio_files,
            reference_audio_total_seconds=reference_audio_total_seconds,
        )
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

        staged_output_path, backend_output_path = await run_sync_transaction(
            self._prepare_video_output,
            output_path,
            formal_output=formal_output,
            task_id=task_id,
        )
        if before_formal_commit is not None and staged_output_path is None:
            raise ValueError("before_formal_commit requires formal video output")

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
                provider=cast(str, self._video_provider_id),
                user_id=self._user_id,
                segment_id=segment_id_for("video", resource_type, resource_id),
                output_path=_project_relative_path(self.project_path, output_path),
                task_id=task_id,
                purpose=CallPurpose.GENERATION_TASK,
                inputs=_ledger_inputs(
                    reference_images=[
                        {"path": rel, "label": None, "role": "array"}
                        | ({"provider_asset": uri} if uri is not None else {})
                        for ref, uri in zip(
                            reference_images or [], asset_uris or [None] * len(reference_images or []), strict=True
                        )
                        if (rel := _project_relative_path(self.project_path, ref, reference_sources)) is not None
                    ],
                    start_image=_project_relative_path(self.project_path, start_image, reference_sources),
                    end_image=_project_relative_path(self.project_path, end_image, reference_sources),
                    reference_audio=[
                        rel
                        for audio in (reference_audio_files or [])
                        if (
                            rel := _project_relative_path(
                                self.project_path, audio, reference_sources, external_name_only=True
                            )
                        )
                        is not None
                    ],
                    parameters=_ledger_inputs(service_tier=version_metadata.get("service_tier")),
                ),
            ) as call,
        ):
            from arcreel_market_core.video_backend_contract import VideoGenerationRequest
            from lib.backends.video_backends.registry import backend_metered_usage

            video_backend = self._video_backend
            specs = slot_plan.specs
            start_spec_idx = slot_plan.start_index
            end_spec_idx = slot_plan.end_index
            ref_start_idx = slot_plan.reference_start_index
            provider_resubmit_unsafe = False
            sent_request: list[VideoGenerationRequest] = []

            def _mark_provider_resubmit_unsafe() -> None:
                nonlocal provider_resubmit_unsafe
                provider_resubmit_unsafe = True

            def _call_video(compressed: "list[CompressedRef]"):
                start_arg = compressed[start_spec_idx].path if start_spec_idx is not None else None
                end_arg = compressed[end_spec_idx].path if end_spec_idx is not None else None
                ref_arg = (
                    [c.path for c in compressed[ref_start_idx:]] if ref_start_idx is not None else reference_images
                )
                request = VideoGenerationRequest(
                    prompt=prompt,
                    output_path=backend_output_path,
                    aspect_ratio=request_aspect_ratio,
                    duration_seconds=duration_int,
                    resolution=resolution,
                    start_image=start_arg,
                    end_image=end_arg,
                    reference_images=ref_arg,
                    reference_image_asset_uris=asset_uris,
                    reference_audio_files=reference_audio_files,
                    reference_audio_targets=reference_audio_targets,
                    generate_audio=effective_generate_audio,
                    poll_timeout_seconds=poll_timeout_seconds,
                    project_name=self.project_name,
                    task_id=task_id,
                    on_provider_resubmit_unsafe=_mark_provider_resubmit_unsafe,
                    on_provider_response=lambda _stage, body: self.ledger.record_provider_response(
                        call_id=call.call_id, body=body
                    ),
                    service_tier=version_metadata.get("service_tier", "default"),
                    seed=version_metadata.get("seed"),
                    video_capabilities=video_caps,
                )
                sent_request[:] = [request]
                return video_backend.generate(request)

            async def _before_first_submit() -> None:
                if before_submit is not None:
                    checkpoint_metadata = await before_submit()
                    if checkpoint_metadata is not None:
                        version_metadata.update(checkpoint_metadata)

            try:
                result = await self._run_with_reference_compression(
                    specs=specs,
                    provider_id=self._video_provider_id,
                    build_and_call=_call_video,
                    reference_sources=reference_sources,
                    before_submit=_before_first_submit if before_submit is not None else None,
                    provider_resubmit_is_unsafe=lambda: provider_resubmit_unsafe,
                )
            except BaseException:
                await _discard_staged_output(staged_output_path)
                raise
            video_uri = result.video_uri
            _merge_result_provenance(version_metadata, result)
            _collect_result_warnings(warnings, result)
            call.success(result, usage=backend_metered_usage(video_backend, sent_request[0], result))

        await self._trash_if_target_deleted(
            resource_type=resource_type,
            resource_id=resource_id,
            staged_file=staged_output_path,
            task_id=task_id,
        )
        await self._prepare_formal_video_commit(
            resource_type=resource_type,
            resource_id=resource_id,
            prompt=prompt,
            output_path=output_path,
            staged_output_path=staged_output_path,
            duration_seconds=duration_int,
            version_metadata=version_metadata,
            before_formal_commit=before_formal_commit,
        )

        committed = await self._commit_video_output_version(
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
        submitted_base_url: str | None = None,
        audio_track: "VideoAudioMode | None" = None,
        poll_timeout_seconds: int = DEFAULT_VIDEO_POLL_TIMEOUT_SECONDS,
        formal_output: bool = False,
        before_formal_commit: Callable[[Path, int, Mapping[str, Any]], Awaitable[None]] | None = None,
        commit_formal_output: Callable[[Path, Path, int, Mapping[str, Any]], PaidVersionCommit] | None = None,
        warnings: list[dict[str, Any]] | None = None,
        **version_metadata,
    ) -> tuple[Path, int, Any, str | None]:
        """接续 provider 上已发起的 video job：调 backend.resume_video 而非 generate。

        与 generate_video_async 的差异：
        - 不开记账括号（不落新 pending 行）—— 首次 submit 已记账；ResumeExpired / crash window
          都不应再写 ApiCall（防双重扣费）。待结算的调用行按 ``api_calls.task_id`` 反查，
          经 ledger.resume_success / resume_failed 精准翻 pending → success/failed；
          反查不到（无任务身份或该调用已结算）则 logger.warning 不阻断。
        - resume 成功后总是记录新版本；``formal_output=True`` 时先下载到
          同目录临时文件，再与版本历史一起提交，不提前覆盖 current。
        - prompt / start_image / reference_images 仅用于日志/版本元数据，不影响 provider 端结果。
        - ``submitted_base_url`` 是具名参数而非 version_metadata：它是提交时域名的回放值、
          只喂给 backend 轮询，落进版本元数据会污染 versions.json。

        ``audio_track`` 是检查点冻结的提交路线音轨，续跑不按当前模型覆盖或缺席的参考图重算。

        Returns: (output_path, version_number, video_ref, video_uri) 四元组。
        """
        output_path = await run_sync_transaction(
            self._prepare_video_target_sync,
            resource_type,
            resource_id,
            track_existing=False,
        )

        try:
            duration_int = int(float(duration_seconds)) if duration_seconds else DEFAULT_PLANNED_DURATION_SECONDS
        except (ValueError, TypeError):
            duration_int = DEFAULT_PLANNED_DURATION_SECONDS

        if self._video_backend is None:
            raise RuntimeError("video_backend not configured")

        api_call_id = await self.ledger.pending_call_id_for_task(task_id) if task_id is not None else None

        if self._config is not None:
            configured_generate_audio = await self._config.video_generate_audio(self.project_name)
        else:
            from lib.config.resolver import ConfigResolver

            configured_generate_audio = ConfigResolver._DEFAULT_VIDEO_GENERATE_AUDIO
        effective_generate_audio = version_metadata.get("generate_audio", configured_generate_audio)

        staged_output_path, backend_output_path = await run_sync_transaction(
            self._prepare_video_output,
            output_path,
            formal_output=formal_output,
            task_id=task_id,
        )
        if before_formal_commit is not None and staged_output_path is None:
            raise ValueError("before_formal_commit requires formal video output")

        from arcreel_market_core.video_backend_contract import (
            ResumeExpiredError,
            VideoCapabilities,
            VideoGenerationRequest,
        )
        from lib.backends.video_backends.registry import backend_metered_usage

        request = VideoGenerationRequest(
            prompt=prompt,
            output_path=backend_output_path,
            aspect_ratio=aspect_ratio,
            duration_seconds=duration_int,
            resolution=resolution,
            generate_audio=effective_generate_audio,
            poll_timeout_seconds=poll_timeout_seconds,
            project_name=self.project_name,
            task_id=task_id,
            on_provider_response=(
                (lambda _stage, body: self.ledger.record_provider_response(call_id=api_call_id, body=body))
                if api_call_id is not None
                else None
            ),
            service_tier=version_metadata.get("service_tier", "default"),
            seed=version_metadata.get("seed"),
            submitted_base_url=submitted_base_url,
            video_capabilities=VideoCapabilities(audio_track=audio_track) if audio_track is not None else None,
        )

        try:
            result = await self._video_backend.resume_video(job_id, request)
        except ResumeExpiredError as exc:
            async with _remove_staged_output_on_error(staged_output_path):
                if api_call_id is not None:
                    await self.ledger.resume_failed(call_id=api_call_id, failure=exc)
                raise
        except asyncio.CancelledError:
            await _discard_staged_output(staged_output_path)
            raise
        except Exception:
            logger.exception("resume 失败 (video) task_id=%s job_id=%s", task_id, job_id)
            await _discard_staged_output(staged_output_path)
            raise

        video_ref = None
        video_uri = result.video_uri
        _merge_result_provenance(version_metadata, result)
        _collect_result_warnings(warnings, result)

        if api_call_id is not None:
            async with _remove_staged_output_on_error(staged_output_path):
                await self.ledger.resume_success(
                    call_id=api_call_id,
                    result=result,
                    usage=backend_metered_usage(self._video_backend, request, result),
                )
        else:
            logger.warning(
                "resume 未反查到待结算调用行 task_id=%s job_id=%s",
                task_id,
                job_id,
            )

        await self._trash_if_target_deleted(
            resource_type=resource_type,
            resource_id=resource_id,
            staged_file=staged_output_path,
            task_id=task_id,
        )
        await self._prepare_formal_video_commit(
            resource_type=resource_type,
            resource_id=resource_id,
            prompt=prompt,
            output_path=output_path,
            staged_output_path=staged_output_path,
            duration_seconds=duration_int,
            version_metadata=version_metadata,
            before_formal_commit=before_formal_commit,
        )

        committed = await self._commit_video_output_version(
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
