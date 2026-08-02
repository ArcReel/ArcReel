"""step1→step2 审核 gate 的纯逻辑：适用性判定、step1 路径、内容指纹、审核状态派生。

gate 横跨两处消费：SDK 工具（``generate_episode_script`` 的 step2 阻塞 enforcement）与 web
router / service（结构化中间态审阅 / 编辑 / 确认）。本模块只做不依赖 ProjectManager 的纯计算
（读 step1 文件 + project dict），令两处共用同一真相源。

真值只存「确认指纹」于 project.json ``episodes[i].step1_review``；pending / confirmed 由读时
比对 live step1 内容指纹派生（沿 StatusCalculator「能算不存」哲学）。因此重跑 normalize、agent
改写 step1、web 手改 step1 都会让指纹漂移、自动重新待审，无需 hook 各异的 step1 写入路径
（narration step1 由 subagent Write 落盘、无 Python chokepoint）。

适用范围（拥有结构化 step1 中间态的三条内容/视觉两段式路径）：
- drama / narration 的图生 / 宫格路径：step1_normalized_script.json / step1_segments.json；
- reference_video 路径（跨 narration / drama content_mode）：step1_reference_units.json。
三者的 step1 变体由 ``step1_kind`` 统一判定（reference_video 按 effective_mode 优先）。ad 无 step1，
不纳入 gate。
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal

from lib.episode_paths import (
    REFERENCE_VIDEO_STEP1_FILENAME,
    STEP1_FILENAMES,
    episode_drafts_dir,
    episode_script_relpath,
)
from lib.json_io import atomic_write_json
from lib.project_manager import find_episode, is_reference_video_episode
from lib.reference_video.duration_migration import migrate_unit_durations
from lib.reference_video.quarantine import QUARANTINE_KIND_STEP1, quarantine_path

logger = logging.getLogger(__name__)

#: 审核状态：not_applicable=该集不走 gate；no_step1=适用但 step1 未产出；
#: pending_review=step1 已产出但未经确认（或确认后内容又变）→ 阻塞 step2；confirmed=已确认放行。
ReviewStatus = Literal["not_applicable", "no_step1", "pending_review", "confirmed"]

#: 确认记录在 episode 条目上的字段名：``{"fingerprint": str, "confirmed_at": ISO8601}``。
REVIEW_FIELD = "step1_review"

#: step1 变体：drama / narration（按 content_mode）+ reference_video（按 effective_mode，跨 content_mode）。
#: 决定 step1 文件名与结构校验模型；三者共用同一审核 gate。
Step1Kind = Literal["drama", "narration", "reference_video"]


def step1_kind(project: dict[str, Any], episode: int) -> Step1Kind | None:
    """该集 step1 变体；无结构化 step1 中间态（如 ad）时返回 None。

    reference_video 是 generation_mode 维度、跨 content_mode（narration / drama 均可），按
    effective_mode 优先判定；否则按 content_mode 落 drama / narration。content_mode 非
    STEP1_FILENAMES 成员（ad）即无 step1，reference_video 亦不适用。
    """
    content_mode = project.get("content_mode")
    if content_mode not in STEP1_FILENAMES:
        return None
    if is_reference_video_episode(project, episode):
        return "reference_video"
    return content_mode  # "drama" | "narration"（STEP1_FILENAMES 成员）


def is_applicable(project: dict[str, Any], episode: int) -> bool:
    """gate 是否适用于该集：拥有结构化 step1 变体（drama / narration / reference_video）。"""
    return step1_kind(project, episode) is not None


def step1_path(project_path: Path, project: dict[str, Any], episode: int) -> Path | None:
    """该集结构化 step1 中间态文件路径；不适用 gate 时返回 None。"""
    kind = step1_kind(project, episode)
    if kind is None:
        return None
    filename = REFERENCE_VIDEO_STEP1_FILENAME if kind == "reference_video" else STEP1_FILENAMES[kind]
    return episode_drafts_dir(project_path, episode) / filename


def step1_quarantine_path(project_path: Path, project: dict[str, Any], episode: int) -> Path | None:
    """该集 step1 隔离草稿的路径（仅 reference_video 变体有隔离草稿）；不适用时返回 None。

    只回路径、不判存在性——存在性判断在 ``step1_quarantined``，两者分开是为了让调用方在
    需要报错文案时能拿到路径。
    """
    if step1_kind(project, episode) != "reference_video":
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
    path: Path,
    content: object,
    *,
    episode: int,
    update_project: Callable[[Callable[[dict[str, Any]], None]], dict[str, Any]],
    supported_durations: Sequence[int] | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    """对已读入内存的 step1 草稿就地做一次性时长收编迁移并回写；返回 ``(最新 project, warnings)``。

    调用方须已持有该文件的排他锁（``ProjectManager.file_lock``）——迁移回写与 Web 端保存 /
    重拆分写盘共享同一把 per-path 锁。未发生迁移时不回写，返回 ``(None, [])``。

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
        logger.warning("step1 草稿 %s 时长收编迁移: %s", path.name, message)
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

    atomic_write_json(path, content)
    return updated, warnings
