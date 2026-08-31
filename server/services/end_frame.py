"""分镜尾帧快照的设置与清除服务层。

尾帧是分镜的用户意图持久属性（``end_frame_image``），不是运行时产出，故不进
``generated_assets``。设置的两条通道（上传任意图片 / 指定项目内已有图片的相对路径）
在这里汇成同一落点：归一为 PNG 后快照复制到 ``end_frames/scene_{id}.png``，剧本条目
只存该固定相对路径。源图与快照就此彻底解耦——源图重生成、版本回滚、删除都动不到
已定尾帧，结构上不存在悬空引用。

设置写快照文件 + 写字段、清除删快照文件 + 置空字段，各自整段落在同一把剧本锁
（`ProjectManager.locked_script`）临界区内完成，与对方互斥——不会出现一方写完文件、
对方抢在字段写回前把文件删掉的交错，结构上杜绝悬空引用。临界区内因目标分镜
mid-flight 被删除而失败时跳过整段写回，不留半截状态。

`locked_script` 在锁内完成剧本持久化，校验/落盘异常向调用方传出前锁已释放，故文件
系统副作用（写快照 / 删快照）不能靠锁外的 try/except 直接回滚——那样回滚本身会跟同
一间隙内另一个成功落盘的并发请求打架。补偿改为重新过锁并按「本分镜的操作代次是否仍是
失败前那一次」做条件回滚（见 `_restore_after_persist_failure`），只撤销自己的效果、不
覆盖旁人已落盘的成果。代次而非文件内容——内容比对有退化值问题：并发的另一次清除同样会
让文件落到「不存在」，与「无人接手」的期望内容（None）无法区分，会把并发清除的结果误判
为无人接手，回滚出不该存在的孤儿文件。代次在临界区内随每次进入自增，无论那次操作最终
成功与否，只要有人在本次失败到回滚重新过锁之间进入过临界区，代次必然前移，据此可靠判定
是否跳过。孤儿快照文件（如进程在文件写完、锁释放前被杀）仍可能残留，无害，与 storyboards
现状一致，不做清理机制。

代次键须按 `ProjectManager.normalize_script_filename` 归一化剧本文件名——`episode_1.json`
与 `scripts/episode_1.json` 是同一剧本的合法别名，不归一的话两个别名各自生成一把代次键，
用不同别名操作同一分镜的并发请求就互相看不见。归一化调用 `ProjectManager` 的公开入口而非
在本文件复刻其内部规则，避免上游改动（如支持新别名形式）时两处静默漂移、代次键重新分裂。

回滚基线（`old_bytes`）须在取得剧本锁之后、mutation 之前读取，不能在锁外预读——锁外预读的话，
若本次操作前还有一次并发写入抢先成功落盘，读到的就是比"操作前"更早的陈旧内容，失败时会用
它覆盖那次已成功的写入，即便代次判定本身没有问题。

标记"可补偿"（`entered`）须在基线读取*成功之后*才置位，不能在读之前——读之前置位的话，
读基线本身失败（权限 / 临时 IO 错误）时 `old_bytes` 仍是初始的 `None`，补偿会把"读失败"
误判成"目标文件原本不存在"，进而 unlink 一个从未被本次操作触碰过的既有快照。

操作代次的推进（`_advance_shot_generation`）须放在分镜匹配成功、紧邻首次文件 mutation
之前，不能在此之前就推进——提前推进的话，一次因分镜未找到而空手退出的请求也会让代次
前移，导致同一时间段内另一个真正持久化失败、正在等待重新过锁补偿的请求，把这次空手
退出误判成"已被接管、自身状态自洽"而跳过回滚，把它自己失败前的半截效果留在磁盘上。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path

from lib.image_utils import normalize_storyboard_upload
from lib.json_io import domain_error_on_value_error
from lib.path_safety import PathTraversalError, safe_join
from lib.project_change_hints import project_change_source
from lib.project_manager import ProjectManager, get_project_manager, is_reference_video_project
from lib.resource_paths import END_FRAME_RESOURCE_TYPE, resource_relative_path
from lib.storyboard_sequence import find_storyboard_item, get_storyboard_items
from server.services.upload_finalize import UPLOAD_IMAGE_MAX_BYTES, write_bytes_atomic

logger = logging.getLogger(__name__)

_ShotKey = tuple[str, str, str]
_generation_lock = threading.Lock()
_shot_generations: dict[_ShotKey, int] = {}


def _current_shot_generation(key: _ShotKey) -> int:
    with _generation_lock:
        return _shot_generations.get(key, 0)


def _advance_shot_generation(key: _ShotKey) -> int:
    """推进该分镜的操作代次，返回新值。在临界区内、mutation 之前调用——无论后续
    是否失败，都代表「一次操作进入过临界区」，供失败一方的补偿回滚据此判断有无被接手。
    """
    with _generation_lock:
        generation = _shot_generations.get(key, 0) + 1
        _shot_generations[key] = generation
        return generation


class EndFrameError(Exception):
    """尾帧操作的领域错误。路由层按 (status_code, key, params) 翻译为 HTTP 响应。"""

    def __init__(self, key: str, *, status_code: int = 400, **params: object):
        super().__init__(key)
        self.key = key
        self.status_code = status_code
        self.params = params


def _locate_shot(project_name: str, script_file: str, shot_id: str) -> Path:
    """校验该分镜可设尾帧，返回项目绝对路径；不可设时抛领域错误。

    参考生视频路径无首尾帧概念，一律拒绝。该判定按 project.json 的生成模式
    （``is_reference_video_project``）作出，不看剧本级 ``generation_mode`` 戳——ad 创作类型的
    剧本骨架不携带该戳（见 ``script_generator``），只看剧本会放过「ad + 参考生视频」组合，让用户
    设下一个生成时永不被消费的尾帧。各创作类型共用这一口径。
    """
    manager = get_project_manager()
    try:
        project_path = manager.get_project_path(project_name)
    except FileNotFoundError as exc:
        raise EndFrameError("project_not_found", status_code=404, name=project_name) from exc
    except ValueError as exc:
        raise EndFrameError("invalid_project_name", status_code=400, name=project_name) from exc

    # 剧本文件损坏（JSON 语法错误或非 UTF-8 字节）不能误判为「非法 script_file」，
    # 交由 _translated_errors 的兜底分支收口为 500
    with domain_error_on_value_error(
        lambda _exc: EndFrameError("invalid_script_file", status_code=400, name=script_file),
        extra_passthrough=(UnicodeDecodeError,),
    ):
        script = manager.load_script(project_name, script_file)

    try:
        project = manager.load_project(project_name)
    except FileNotFoundError as exc:
        raise EndFrameError("project_not_found", status_code=404, name=project_name) from exc
    if is_reference_video_project(project):
        raise EndFrameError("end_frame_reference_video_unsupported")

    items, id_field, _, _, _ = get_storyboard_items(script)
    if find_storyboard_item(items, id_field, shot_id) is None:
        raise EndFrameError("segment_not_found", status_code=404, id=shot_id)
    return project_path


def _snapshot_target(project_path: Path, shot_id: str) -> tuple[Path, str]:
    """返回尾帧快照的 (绝对路径, 项目内相对路径)；shot_id 拼出的路径越界时抛领域错误。"""
    relative = resource_relative_path(END_FRAME_RESOURCE_TYPE, shot_id)
    try:
        target = safe_join(project_path, relative)
    except PathTraversalError as exc:
        raise EndFrameError("invalid_resource_id", resource_id=shot_id) from exc
    return target, relative


class _RollbackDone(Exception):
    """哨兵：标记补偿分支已跑完，用于跳过 `locked_script` 退出时的正常剧本写回。

    `locked_script` 基于 `@contextmanager`：`with` 体内无论正常落出还是 `return`，生成器都
    会在 `yield` 之后继续跑 `_write_script_unlocked`。补偿只改快照文件，从不读改 `script`，
    正常落出会触发一次多余落盘（附带更新 `metadata["updated_at"]`、触发项目同步与变更事件，
    失败时还会被这里的 `except Exception` 误记成"回滚失败"）。抛这个哨兵异常换生成器在
    `yield` 处直接重新抛出，绕过写回；下面的 `except _RollbackDone: pass` 吞掉它。
    """


def _restore_after_persist_failure(
    manager: ProjectManager,
    project_name: str,
    script_file: str,
    target: Path,
    old_bytes: bytes | None,
    key: _ShotKey,
    expected_generation: int,
) -> None:
    """剧本持久化失败后的补偿：在新的一次剧本锁临界区内条件回滚快照文件。

    补偿本身必须重新过锁——`locked_script` 在锁内完成持久化，异常向上传播前锁已释放，
    若在锁外直接按调用方持有的旧字节回写，会跟同一间隙内另一个成功落盘的并发请求打架。
    回滚前用 `expected_generation` 复核该分镜的操作代次是否仍是本次失败前那一次：仍是
    则说明这段时间无人进入过临界区，安全回滚；已前移则说明并发操作已经接管（不论其
    自身成败），当前状态可能已自洽，回滚只会用陈旧字节覆盖它的成果，直接跳过。
    """
    try:
        with manager.locked_script(project_name, script_file):
            if _current_shot_generation(key) != expected_generation:
                raise _RollbackDone
            if old_bytes is not None:
                write_bytes_atomic(old_bytes, target)
            else:
                target.unlink(missing_ok=True)
            raise _RollbackDone
    except _RollbackDone:
        pass
    except Exception:
        logger.exception(
            "尾帧快照回滚失败：project=%s script=%s target=%s（原持久化异常见上一条 traceback）",
            project_name,
            script_file,
            target,
        )


def _write_snapshot_and_field(
    project_name: str,
    script_file: str,
    shot_id: str,
    target: Path,
    png_bytes: bytes,
    relative: str,
) -> None:
    """在剧本锁临界区内完成「写快照文件 + 写字段」，与清除操作互斥。

    剧本持久化失败（校验/落盘异常）时把快照文件还原成操作前状态——否则字段写入
    虽被回滚，快照文件的旧内容已被静默替换，调用方收到失败响应却看不出内容已丢失。
    读旧字节须在取得剧本锁之后、写入之前——锁外预读的话，若排在本次操作之前还有一次
    并发写入抢先成功落盘，预读到的就是比"操作前"更早的陈旧内容，失败时会用它把那次
    已成功的写入覆盖掉。
    """
    manager = get_project_manager()
    key = (project_name, ProjectManager.normalize_script_filename(script_file), shot_id)
    generation = 0
    old_bytes: bytes | None = None
    entered = False
    try:
        with project_change_source("webui"), manager.locked_script(project_name, script_file) as script:
            old_bytes = target.read_bytes() if target.exists() else None
            items, id_field, _, _, _ = get_storyboard_items(script)
            matched = find_storyboard_item(items, id_field, shot_id)
            if matched is None:
                raise EndFrameError("segment_not_found", status_code=404, id=shot_id)
            generation = _advance_shot_generation(key)
            entered = True
            write_bytes_atomic(png_bytes, target)
            matched[0]["end_frame_image"] = relative
    except EndFrameError:
        raise
    except BaseException:
        if entered:
            _restore_after_persist_failure(manager, project_name, script_file, target, old_bytes, key, generation)
        raise


def _clear_snapshot_and_field(project_name: str, script_file: str, shot_id: str, target: Path) -> None:
    """在剧本锁临界区内完成「置空字段 + 删快照文件」，与设置操作互斥。

    剧本持久化失败时把已删除的快照文件恢复——否则字段清空虽被回滚（仍指向原路径），
    快照文件已被删除，重新落回悬空引用。读旧字节须在取得剧本锁之后、删除之前，理由
    同 `_write_snapshot_and_field`。
    """
    manager = get_project_manager()
    key = (project_name, ProjectManager.normalize_script_filename(script_file), shot_id)
    generation = 0
    old_bytes: bytes | None = None
    entered = False
    try:
        with project_change_source("webui"), manager.locked_script(project_name, script_file) as script:
            old_bytes = target.read_bytes() if target.exists() else None
            items, id_field, _, _, _ = get_storyboard_items(script)
            matched = find_storyboard_item(items, id_field, shot_id)
            if matched is None:
                raise EndFrameError("segment_not_found", status_code=404, id=shot_id)
            generation = _advance_shot_generation(key)
            entered = True
            matched[0]["end_frame_image"] = None
            target.unlink(missing_ok=True)
    except EndFrameError:
        raise
    except BaseException:
        if entered:
            _restore_after_persist_failure(manager, project_name, script_file, target, old_bytes, key, generation)
        raise


def read_project_image(project_path: Path, source_path: str) -> bytes:
    """读取项目内已有图片的字节；路径越界、文件缺失或超限时抛领域错误。

    不限定来源子目录——分镜图 / 角色 / 场景 / 宫格切图都可直接选用，越界防护
    由 ``safe_join`` 统一负责（与 data_validator 的路径字段校验同口径）。

    读取按上传通道同一上限（``UPLOAD_IMAGE_MAX_BYTES``）有界读取，不整读入内存——
    项目内合法存在的大文件（如视频）被当作选图源时不至于无界内存占用。
    """
    normalized = source_path.strip().replace("\\", "/")
    if not normalized:
        raise EndFrameError("invalid_end_frame_source", path=source_path)
    try:
        resolved = safe_join(project_path, normalized)
    except PathTraversalError as exc:
        raise EndFrameError("invalid_end_frame_source", path=source_path) from exc
    if not resolved.is_file():
        raise EndFrameError("end_frame_source_not_found", status_code=404, path=normalized)
    with resolved.open("rb") as f:
        content = f.read(UPLOAD_IMAGE_MAX_BYTES + 1)
    if len(content) > UPLOAD_IMAGE_MAX_BYTES:
        raise EndFrameError(
            "end_frame_source_too_large", max_mb=UPLOAD_IMAGE_MAX_BYTES // (1024 * 1024), path=normalized
        )
    return content


async def _apply_snapshot(
    *,
    project_path: Path,
    project_name: str,
    script_file: str,
    shot_id: str,
    content: bytes,
) -> str:
    """两条设置通道的共同落点：归一为 PNG → 写固定路径 → 写回字段，返回相对路径。

    换图即原地覆盖同一路径：字段值不变，前端靠资产指纹 cache-bust。
    """
    target, relative = _snapshot_target(project_path, shot_id)

    try:
        png_bytes = await asyncio.to_thread(normalize_storyboard_upload, content)
    except ValueError as exc:
        raise EndFrameError("invalid_image_file") from exc

    await asyncio.to_thread(_write_snapshot_and_field, project_name, script_file, shot_id, target, png_bytes, relative)
    return relative


async def set_end_frame_from_bytes(
    *,
    project_name: str,
    script_file: str,
    shot_id: str,
    content: bytes,
) -> str:
    """上传通道：把上传的图片字节落成该分镜的尾帧快照。"""
    project_path = await asyncio.to_thread(_locate_shot, project_name, script_file, shot_id)
    return await _apply_snapshot(
        project_path=project_path,
        project_name=project_name,
        script_file=script_file,
        shot_id=shot_id,
        content=content,
    )


async def set_end_frame_from_project_image(
    *,
    project_name: str,
    script_file: str,
    shot_id: str,
    source_path: str,
) -> str:
    """项目内选图通道：读源图字节后走与上传完全相同的归一 + 快照落点。"""
    project_path = await asyncio.to_thread(_locate_shot, project_name, script_file, shot_id)
    content = await asyncio.to_thread(read_project_image, project_path, source_path)
    return await _apply_snapshot(
        project_path=project_path,
        project_name=project_name,
        script_file=script_file,
        shot_id=shot_id,
        content=content,
    )


async def clear_end_frame(*, project_name: str, script_file: str, shot_id: str) -> None:
    """清除分镜尾帧：在同一剧本锁临界区内把字段置空并删快照文件，与设置操作互斥。"""
    project_path = await asyncio.to_thread(_locate_shot, project_name, script_file, shot_id)
    target, _ = _snapshot_target(project_path, shot_id)
    await asyncio.to_thread(_clear_snapshot_and_field, project_name, script_file, shot_id, target)
