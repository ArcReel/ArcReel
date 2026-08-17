"""step1→step2 审核 gate 的核心逻辑：适用性判定、step1 路径、内容指纹、审核状态派生，
以及参考生视频正式 step1 的单一写盘出口（``step1_write_lock`` / ``write_step1_locked``）。

gate 横跨两处消费：SDK 工具（``generate_episode_script`` 的 step2 阻塞 enforcement）与 web
router / service（结构化中间态审阅 / 编辑 / 确认）。状态派生只依赖 step1 文件 + project dict
的纯计算；写盘出口另持 ``ProjectManager.file_lock`` 的 per-path 锁，四条写路径（Web 端保存、
重拆分、晋升、迁移回写）全部汇入，锁、乐观并发比对与 step2 隔离草稿清理只存在一处。

真值只存「确认指纹」于 project.json ``episodes[i].step1_review``；pending / confirmed 由读时
比对 live step1 内容指纹派生（沿 StatusCalculator「能算不存」哲学）。因此重跑 normalize、agent
改写 step1、web 手改 step1 都会让指纹漂移、自动重新待审，无需 hook 各异的 step1 写入路径
（narration step1 由 subagent Write 落盘、无 Python chokepoint）。

适用范围（拥有结构化 step1 中间态的三条内容/视觉两段式路径）：
- drama / narration 的图生 / 宫格路径：step1_normalized_script.json / step1_segments.json；
- reference_video 路径（跨 narration / drama content_mode）：step1_reference_units.json。
三者的 step1 变体由 ``step1_kind`` 统一判定（reference_video 按项目生成路线优先）。ad 无 step1，
不纳入 gate。
"""

from __future__ import annotations

import enum
import hashlib
import json
import logging
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from lib.episode_paths import (
    REFERENCE_VIDEO_STEP1_FILENAME,
    STEP1_FILENAMES,
    episode_drafts_dir,
    episode_script_relpath,
)
from lib.formal_write import formal_write_transaction, project_metadata_lock
from lib.json_io import atomic_write_json, load_json_or_none
from lib.project_manager import ProjectManager, find_episode, is_reference_video_project
from lib.project_schema import project_schema_is_current
from lib.reference_video.duration_migration import migrate_unit_durations
from lib.draft_quarantine import (
    QUARANTINE_KIND_STEP1,
    QUARANTINE_KIND_STEP2,
    clear_quarantine,
    quarantine_path,
)
from lib.validation_messages import ValidationMessage

if TYPE_CHECKING:
    from lib.artifact_manifest import ArtifactBasis

logger = logging.getLogger(__name__)

#: 审核状态：not_applicable=该集不走 gate；no_step1=适用但 step1 未产出；
#: pending_review=step1 已产出但未经确认（或确认后内容又变）→ 阻塞 step2；confirmed=已确认放行。
ReviewStatus = Literal["not_applicable", "no_step1", "pending_review", "confirmed"]

#: 确认记录在 episode 条目上的字段名：``{"fingerprint": str, "confirmed_at": ISO8601}``。
REVIEW_FIELD = "step1_review"

#: stale 账本条目记录重规划提交时旧 step1 的内容指纹；live 指纹变化即证明 step1 已按新账本重建。
STALE_STEP1_REVISION_FIELD = "stale_step1_revision"

#: stale 分集的 step1 重建完成事实。指纹可能与旧内容相同，不能仅以内容变化推断是否执行过重建。
STALE_STEP1_REBUILT_REVISION_FIELD = "stale_step1_rebuilt_revision"

#: 最终剧本 metadata 记录其实际消费的 step1 内容指纹；workflow status 用它识别 step1
#: 重新确认后仍残留的旧剧本，避免仅凭「文件存在」误判 step2 已完成。
SCRIPT_STEP1_REVISION_FIELD = "step1_revision"

#: step1 变体：drama / narration（按 content_mode）+ reference_video（按项目生成路线，跨 content_mode）。
#: 决定 step1 文件名与结构校验模型；三者共用同一审核 gate。
Step1Kind = Literal["drama", "narration", "reference_video"]


def step1_kind(project: dict[str, Any]) -> Step1Kind | None:
    """项目的 step1 变体；无结构化 step1 中间态（如 ad）时返回 None。

    reference_video 是 generation_mode 维度、跨 content_mode（narration / drama 均可），按项目
    生成路线优先判定；否则按 content_mode 落 drama / narration。content_mode 非
    STEP1_FILENAMES 成员（ad）即无 step1，reference_video 亦不适用。变体由项目两轴唯一决定，
    不随集号变化。
    """
    content_mode = project.get("content_mode")
    if content_mode not in STEP1_FILENAMES:
        return None
    if is_reference_video_project(project):
        return "reference_video"
    return content_mode  # "drama" | "narration"（STEP1_FILENAMES 成员）


def is_applicable(project: dict[str, Any]) -> bool:
    """gate 是否适用于该项目：拥有结构化 step1 变体（drama / narration / reference_video）。"""
    return step1_kind(project) is not None


def step1_path(project_path: Path, project: dict[str, Any], episode: int) -> Path | None:
    """该集结构化 step1 中间态文件路径；不适用 gate 时返回 None。"""
    kind = step1_kind(project)
    if kind is None:
        return None
    filename = REFERENCE_VIDEO_STEP1_FILENAME if kind == "reference_video" else STEP1_FILENAMES[kind]
    return episode_drafts_dir(project_path, episode) / filename


def step1_quarantine_path(project_path: Path, project: dict[str, Any], episode: int) -> Path | None:
    """该集 step1 隔离草稿的路径（仅 reference_video 变体有隔离草稿）；不适用时返回 None。

    只回路径、不判存在性——存在性判断在 ``step1_quarantined``，两者分开是为了让调用方在
    需要报错文案时能拿到路径。
    """
    if step1_kind(project) != "reference_video":
        return None
    return quarantine_path(project_path, episode, QUARANTINE_KIND_STEP1)


def step1_quarantined(project_path: Path, project: dict[str, Any], episode: int) -> bool:
    """该集 step1 是否有违约产物被隔离——gate 与 step2 的阻塞判据。

    隔离态与「正式 step1 的内容指纹」是两件事：重新拆分违约时正式文件原封不动，指纹照旧
    等于已确认值，只看指纹会把该集判成 confirmed 并放行 step2——用户看到的是上一版内容，
    而 agent 手里那份刚产出的正文还躺在隔离草稿里没人处置。故隔离态独立阻塞。
    """
    path = step1_quarantine_path(project_path, project, episode)
    return path is not None and path.exists()


def content_fingerprint_of_data(data: object) -> str:
    """已解析 JSON 对象的指纹：规范化 dump（键序 / 空白重排不改指纹）的 sha256。

    供调用方对已读入内存的对象直接取指纹（如迁移前 snapshot），不经二次磁盘读取——指纹须
    对应调用方手里这份内容本身。与 ``content_fingerprint`` 的 JSON 分支同一套规范化逻辑，
    仅入参从路径换成已解析对象，故对同一份内容两者取值相同。
    """
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def content_fingerprint(path: Path) -> str | None:
    """step1 内容指纹：合法 JSON 取规范化 dump 的 sha256（键序 / 空白重排不改指纹、语义变更才改），
    非 JSON 退化为原始字节 sha256；文件不存在（FileNotFoundError）时 None。

    只把「文件不存在」降级为 None（→ no_step1、gate 放行）；权限不足、目录占位、短暂 I/O 等其它
    OSError 一律向上抛，避免把真实文件系统故障静默当成「step1 未产出」而误放行 step2。

    对已读入内存的对象取指纹（如迁移前 snapshot）用 ``content_fingerprint_of_data``，不要对同一
    文件再调一次本函数——两次独立读取之间的并发写入会被此函数的第二次读取吞掉，见其 docstring。
    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    try:
        parsed = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return hashlib.sha256(raw).hexdigest()
    return content_fingerprint_of_data(parsed)


class _UncheckedFingerprint(enum.Enum):
    """``write_step1_locked`` 的 ``expected_fingerprint`` 哨兵类型：区分「不做基线比对」与
    「基线是 None（写入方取基线时正式文件不存在）」——两者都要能表达，``None`` 只够表达后者。"""

    TOKEN = 0


#: 「不做基线比对」哨兵：写入方没有基线可言（重拆分整份覆盖、同临界区读改写）时传它。
UNCHECKED_FINGERPRINT = _UncheckedFingerprint.TOKEN


class Step1WriteConflict(Exception):
    """正式 step1 的乐观并发冲突：写入方的基线指纹与盘上现值不一致。

    携带盘上现值（``current_content``，非法 JSON 时 None）与两侧指纹，供编辑方渲染冲突报告、
    对照最新内容合并——单一写盘出口在锁内比对后抛出，后写方收到本异常而非静默覆盖先写方。
    """

    def __init__(self, *, expected: str | None, actual: str | None, current_content: dict[str, Any] | None):
        super().__init__(f"step1 并发冲突：基线指纹 {expected}，盘上现值指纹 {actual}")
        self.expected = expected
        self.actual = actual
        self.current_content = current_content


class Step1RebuildCompletionError(ValueError):
    """A stale step1 rebuild cannot be recorded against the current ledger state."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


def complete_stale_step1_rebuild(
    pm: ProjectManager,
    project_name: str,
    episode: int,
    expected_stale_revision: str | None,
) -> str:
    """Record preprocessing completion for a stale entry, including byte-identical rebuilds."""
    if isinstance(episode, bool) or episode < 1:
        raise Step1RebuildCompletionError("invalid_episode", "episode must be a positive integer")

    project_path = pm.get_project_path(project_name)
    committed: dict[str, str] = {}

    def _commit(project: dict[str, Any]) -> None:
        entry = find_episode(project, episode)
        if entry is None or entry.get("ledger_status") != "stale":
            raise Step1RebuildCompletionError("not_stale", "episode is not awaiting a stale step1 rebuild")
        if STALE_STEP1_REVISION_FIELD not in entry:
            raise Step1RebuildCompletionError("missing_baseline", "stale episode has no rebuild baseline")
        if entry.get(STALE_STEP1_REVISION_FIELD) != expected_stale_revision:
            raise Step1RebuildCompletionError(
                "baseline_conflict", "stale step1 baseline changed; refresh workflow status"
            )
        path = step1_path(project_path, project, episode)
        revision = content_fingerprint(path) if path is not None else None
        if revision is None:
            raise Step1RebuildCompletionError("step1_missing", "rebuilt step1 file is missing")
        entry[STALE_STEP1_REBUILT_REVISION_FIELD] = revision
        committed["revision"] = revision

    pm.update_project(project_name, _commit)
    return committed["revision"]


def assert_base_fingerprint(path: Path, expected: str | None | _UncheckedFingerprint) -> None:
    """乐观并发比对：``expected`` 与 ``path`` 盘上现值不一致时抛 ``Step1WriteConflict``、不落盘。

    ``expected`` 是写入方取基线时的文件指纹（``None`` 表示彼时文件不存在），
    ``UNCHECKED_FINGERPRINT`` 跳过比对。三个 step1 变体共用本函数，比对语义只存在这一处。

    调用方须已持该路径的排他锁：比对与随后的写盘不在同一临界区内，比对就只是一次过期读。
    """
    if isinstance(expected, _UncheckedFingerprint):
        return
    actual = content_fingerprint(path)
    if expected == actual:
        return
    current = load_json_or_none(path)
    raise Step1WriteConflict(
        expected=expected,
        actual=actual,
        current_content=current if isinstance(current, dict) else None,
    )


def official_reference_step1_path(project_path: Path, episode: int) -> Path:
    """该集参考生视频正式 step1 的路径（``drafts/episode_N/step1_reference_units.json``）。

    与 ``step1_path`` 的区别：后者按项目变体判定文件名、不适用时 None；本函数是 rv 写盘出口
    的路径真相源，不依赖 project dict。"""
    return episode_drafts_dir(project_path, episode) / REFERENCE_VIDEO_STEP1_FILENAME


@contextmanager
def step1_write_lock(project_path: Path, episode: int) -> Iterator[Path]:
    """参考生视频正式 step1 的写临界区：建目录 + per-path 排他锁，yield 正式文件路径。

    与迁移读改写、Web 端保存、重拆分 / 晋升共用同一把 ``ProjectManager.file_lock``（per-path，
    进程间排他、不可重入）。凡要「读正式文件后据此写盘或写隔离草稿」的操作都应整段包在本
    临界区内——读与写拆开在锁外各做一次，就是并发覆盖窗口。
    """
    drafts_dir = episode_drafts_dir(project_path, episode)
    drafts_dir.mkdir(parents=True, exist_ok=True)
    path = official_reference_step1_path(project_path, episode)
    pm = ProjectManager(str(project_path.parent))
    with pm.file_lock(path):
        yield path


@contextmanager
def formal_step1_write_transaction(
    project_path: Path,
    episode: int,
    *paths: Path,
    basis: ArtifactBasis | None = None,
) -> Iterator[None]:
    """Commit formal step1 files and their active Manifest claim as one unit.

    Callers own the canonical per-path lock.  Every Python write path for a
    drama, narration, or reference-video step1 enters this context so a
    successful write refreshes the same typed claim, while registration
    failure restores every supplied formal file byte-for-byte.
    """

    with project_metadata_lock(project_path), formal_write_transaction(*paths):
        yield
        from lib.artifact_activation import (
            register_current_artifact,
            register_current_artifact_if_provable,
        )
        from lib.artifact_manifest import ArtifactKey

        project_file = project_path / "project.json"
        try:
            project = json.loads(project_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            logger.warning("cannot inspect project schema after formal step1 write: %s", exc)
            project = None
        if isinstance(project, dict) and project_schema_is_current(project):
            # A successful no-op write can still repair a missing claim after a
            # temporarily unavailable source made activation skip this target.
            key = ArtifactKey.episode_step1(episode)
            if basis is None:
                register_current_artifact_if_provable(project_path, key)
            else:
                if not paths:
                    raise ValueError("a frozen step1 basis requires its formal artifact path")
                register_current_artifact(
                    project_path,
                    key,
                    artifact_path=paths[0].relative_to(project_path).as_posix(),
                    basis=basis,
                )


def write_step1_json(
    project_path: Path,
    episode: int,
    path: Path,
    content: object,
    *,
    basis: ArtifactBasis | None = None,
) -> None:
    """Atomically write a structured step1 through its canonical lock and claim seam."""

    path.parent.mkdir(parents=True, exist_ok=True)
    pm = ProjectManager(str(project_path.parent))
    with pm.file_lock(path), formal_step1_write_transaction(project_path, episode, path, basis=basis):
        atomic_write_json(path, content)


def delete_step1_file(project_path: Path, episode: int, path: Path) -> bool:
    """Delete a formal step1 and forget its active claim through the same transaction."""

    pm = ProjectManager(str(project_path.parent))
    with pm.file_lock(path):
        if not path.exists():
            return False
        with formal_step1_write_transaction(project_path, episode, path):
            path.unlink()
    return True


def write_step1_locked(
    project_path: Path,
    episode: int,
    content: dict[str, Any],
    *,
    expected_fingerprint: str | None | _UncheckedFingerprint = UNCHECKED_FINGERPRINT,
    clear_step2_quarantine: bool = True,
    basis: ArtifactBasis | None = None,
) -> bool:
    """参考生视频正式 step1 的**单一写盘出口**：基线比对（OCC）→ 原子写 → 内容变化时清 step2
    隔离草稿。返回内容是否发生变化。

    调用方须已持有该文件的排他锁（``step1_write_lock``，或同一路径的 ``ProjectManager.file_lock``
    ——锁不可重入，已在临界区内的调用方不能再套 ``step1_write_lock``）。四条写路径（Web 端
    保存、重拆分、晋升、迁移回写）全部汇入本函数，写盘语义只存在这一处。

    ``expected_fingerprint`` 是写入方取基线时的正式文件指纹（``None`` 表示彼时文件不存在）；
    与盘上现值不一致时抛 ``Step1WriteConflict``、不落盘——后写方拿冲突报告去合并，先写方的
    内容不被静默覆盖。传 ``UNCHECKED_FINGERPRINT`` 跳过比对：重拆分是刻意的整份重建，
    同临界区读改写（迁移、确认）则读写之间本就无并发窗口。

    step2 隔离草稿的保结构 diff 以旧 step1 为基底，step1 真的变了才作废它；迁移回写是机械
    格式收编、不是内容编辑，调用方传 ``clear_step2_quarantine=False`` 保留 step2 草稿。
    """
    path = official_reference_step1_path(project_path, episode)
    assert_base_fingerprint(path, expected_fingerprint)
    previous = load_json_or_none(path)
    changed = previous != content
    quarantine = quarantine_path(project_path, episode, QUARANTINE_KIND_STEP2)
    with formal_step1_write_transaction(project_path, episode, path, quarantine, basis=basis):
        atomic_write_json(path, content)
        if changed and clear_step2_quarantine:
            clear_quarantine(project_path, episode, QUARANTINE_KIND_STEP2)
    return changed


def stored_review(project: dict[str, Any], episode: int) -> dict[str, Any]:
    """该集已存的确认记录（``episodes[i].step1_review``），缺失或形状坏时返回空 dict。"""
    ep = find_episode(project, episode)
    review = ep.get(REVIEW_FIELD) if ep else None
    return review if isinstance(review, dict) else {}


def step2_generated(project_path: Path, project: dict[str, Any], episode: int) -> bool:
    """该集 step2 产物（生成的剧本 JSON）是否已存在——存量 grandfather 判据。

    取自 episode 条目的 ``script_file``（缺省回退约定路径 ``scripts/episode_N.json``，与
    ScriptGenerator 固定写出口径一致）。
    """
    ep = find_episode(project, episode) or {}
    script_file = ep.get("script_file") or episode_script_relpath(episode)
    return (project_path / script_file).exists()


def review_status(project_path: Path, project: dict[str, Any], episode: int) -> ReviewStatus:
    """派生该集审核状态。

    穷举 {step1 有无 × step2 有无 × step1_review 有无}：
    - 无 step1（或 gate 不适用）：not_applicable / no_step1；
    - 有确认指纹：与 live step1 内容指纹一致 → confirmed，不一致（step1 改过）→ pending_review；
    - 无确认指纹（存量 / 首次）：已产 step2（存量项目升级前已通过该集）→ grandfather 放行 confirmed，
      避免新 gate 无谓阻塞存量 step2 重跑；未产 step2（feature 后首次产 step1）→ pending_review 待确认。
    """
    path = step1_path(project_path, project, episode)
    if path is None:
        return "not_applicable"
    # 隔离草稿在场先于指纹判定：违约产物尚未处置，无论正式文件是缺失、旧版还是已确认，
    # 该集都还没有一份「可放行」的 step1。判 pending_review 而非新增状态——gate 的消费方
    # （阻塞 step2、web 状态展示）要的正是「未放行」这一位，加状态会波及全部消费点。
    if step1_quarantined(project_path, project, episode):
        return "pending_review"
    live = content_fingerprint(path)
    if live is None:
        return "no_step1"
    stored_fingerprint = stored_review(project, episode).get("fingerprint")
    if stored_fingerprint is not None:
        return "confirmed" if stored_fingerprint == live else "pending_review"
    # 无确认指纹（存量 / 首次）：用 step2 产物是否已存在做 grandfather 判据。
    # 过渡态局限：存量集没有指纹基线，无法区分「step1 未动」与「step1 已重拆但未确认」——
    # 只要旧 step2 文件仍在，重拆后的 step1 也会被放行、不重新拦审。这是「不无谓阻塞存量重跑」的
    # 取舍代价，且自愈：用户或 agent 首次确认后即写入指纹，此后走上面的指纹分支、gate 全程生效。
    return "confirmed" if step2_generated(project_path, project, episode) else "pending_review"


def gate_blocks_step2(project_path: Path, project: dict[str, Any], episode: int) -> bool:
    """step2 是否应被 gate 阻塞——仅 pending_review 阻塞；not_applicable / no_step1 / confirmed 放行。

    no_step1 不在此阻塞：step2 入口对缺 step1 另有「未找到 Step 1 文件」的早返提示，
    本 gate 只负责「step1 在但未确认」这一道。
    """
    return review_status(project_path, project, episode) == "pending_review"


def apply_confirmation(project: dict[str, Any], episode: int, fingerprint: str, confirmed_at: str) -> bool:
    """就地把确认记录写入 project ``episodes[i].step1_review``；集条目不存在返回 False。

    供 service 层在 ProjectManager.update_project 的 RMW 回调内调用，确认指纹的持久化 shape
    单一真相源在此。
    """
    ep = find_episode(project, episode)
    if ep is None:
        return False
    ep[REVIEW_FIELD] = {"fingerprint": fingerprint, "confirmed_at": confirmed_at}
    return True


def carry_confirmation_through_migration(project: dict[str, Any], episode: int, before: str, after: str) -> bool:
    """存量 step1 草稿的时长收编迁移是机械格式收编、不是内容编辑，但回写会让内容指纹漂移。

    若该集确认指纹恰好记的是迁移前内容（``before``），就把它平移到迁移后的值（``after``），
    避免一个早已确认的分集仅因被迁移回写就退回待审；指纹本就对不上（step1 在迁移外确实被
    改过）时不动，返回 False，照常按待审处理。

    仅适用于迁移无 warnings 的情形（纯格式收编，时长取值未被 clamp 改写）：调用方须在迁移
    产生 warnings 时跳过本函数，让确认照常失效——那种情形下 ``after`` 携带的时长已不是用户
    确认时看到的值，平移确认等于替用户默许了一次未经审阅的内容变更。

    供调用方在 ``ProjectManager.update_project`` 的锁内回调中调用（确保比对的是加锁后重读的
    最新 project）——``server/services/script_review.py`` 与 ``lib/script_generator.py`` 的两处
    迁移写回入口共用本函数，不各自重复这段判断。
    """
    stored = stored_review(project, episode)
    if stored.get("fingerprint") != before:
        return False
    apply_confirmation(project, episode, after, str(stored.get("confirmed_at") or ""))
    return True


def migrate_step1_draft_in_place(
    project_path: Path,
    content: object,
    *,
    episode: int,
    update_project: Callable[[Callable[[dict[str, Any]], None]], dict[str, Any]],
    supported_durations: Sequence[int] | None = None,
) -> tuple[dict[str, Any] | None, list[ValidationMessage]]:
    """对已读入内存的 step1 草稿就地做一次性时长收编迁移并回写；返回 ``(最新 project, warnings)``。

    调用方须已持有该文件的排他锁（``step1_write_lock`` / 同路径 ``ProjectManager.file_lock``）
    ——回写经单一写盘出口 ``write_step1_locked``，与 Web 端保存 / 重拆分写盘同一把 per-path
    锁。迁移是机械格式收编、不是内容编辑，不作废 step2 隔离草稿；同临界区读改写也无并发
    窗口，不做基线比对。未发生迁移时不回写，返回 ``(None, [])``。

    迁移多数情况下是机械格式收编，回写会让内容指纹漂移：经 ``update_project`` 在锁内把该集
    确认指纹平移到迁移后的值（``carry_confirmation_through_migration``），避免已确认分集仅因
    被加载就退回待审。``warnings`` 非空说明迁移按档位 / 结构区间 clamp 改写了实际时长取值——
    那是内容变更，不平移确认，已确认分集经指纹比对照常退回待审；从未存过指纹、靠 grandfather
    判据（step2 产物已存在）放行的存量集则显式记下迁移前内容的指纹，使其同样失配退回待审。
    该标记的持久化不区分 dry-run 与真实生成：迁移幂等落盘后重试不再产生 warnings，只有落盘
    的标记能保证后续生成仍被审阅门拦下。

    project 侧的确认标记先落盘、草稿后落盘：两次写之间中断时草稿仍是迁移前内容，下次加载
    重跑迁移即自愈。反序则草稿已丢失旧字段、重跑判 ``changed=False``，标记永久缺失——靠
    grandfather 判据放行的存量集会带着被 clamp 的时长停在 confirmed，绕过审阅门。

    ``supported_durations`` 给定时（step2 加载侧持有模型档位）收编结果直接取档；缺省
    （web gate 侧，能力解析是 async + DB、同步拿不到档位）只做结构区间 clamp。

    ``lib.script_generator.ScriptGenerator`` 与 ``server.services.script_review`` 的两处
    step1 读取入口共用本函数，迁移与确认平移的判断不各自重复。
    """
    if not isinstance(content, dict):
        return None, []
    before = content_fingerprint_of_data(content)
    changed, warnings = migrate_unit_durations(content.get("units"), supported_durations=supported_durations)
    for message in warnings:
        logger.warning("step1 草稿 %s 时长收编迁移: %s", REFERENCE_VIDEO_STEP1_FILENAME, message.render())
    if not changed:
        return None, []

    if warnings:

        def _invalidate_grandfathered(p: dict[str, Any]) -> None:
            # 已存过指纹的分集不插手：确认的是迁移前内容时指纹已自然失配，确认的是别的
            # 内容时 review_status 本就判 pending_review。
            stored = stored_review(p, episode)
            if not stored.get("fingerprint"):
                apply_confirmation(p, episode, before, str(stored.get("confirmed_at") or ""))

        updated = update_project(_invalidate_grandfathered)
    else:
        after = content_fingerprint_of_data(content)

        def _carry(p: dict[str, Any]) -> None:
            carry_confirmation_through_migration(p, episode, before, after)

        updated = update_project(_carry)

    write_step1_locked(project_path, episode, content, clear_step2_quarantine=False)
    return updated, warnings
