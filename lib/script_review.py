"""step1→step2 审核 gate 的纯逻辑：适用性判定、step1 路径、内容指纹、审核状态派生。

gate 横跨两处消费：SDK 工具（``generate_episode_script`` 的 step2 阻塞 enforcement）与 web
router / service（结构化中间态审阅 / 编辑 / 确认）。本模块只做不依赖 ProjectManager 的纯计算
（读 step1 文件 + project dict），令两处共用同一真相源。

真值只存「确认指纹」于 project.json ``episodes[i].step1_review``；pending / confirmed 由读时
比对 live step1 内容指纹派生（沿 StatusCalculator「能算不存」哲学）。因此重跑 normalize、agent
改写 step1、web 手改 step1 都会让指纹漂移、自动重新待审，无需 hook 各异的 step1 写入路径
（narration step1 由 subagent Write 落盘、无 Python chokepoint）。

适用范围：content_mode ∈ {drama, narration} 且 effective_mode 非 reference_video——即拥有结构化
step1 中间态（drama 的 step1_normalized_script.json / narration 的 step1_segments.json）的两条
内容/视觉两段式路径。ad 无 step1、reference_video 的 step1 是自由文本 md（非结构化中间态），
均不纳入 gate，与「drama 与 narration 共用此机制」一致。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from lib.episode_paths import STEP1_FILENAMES, episode_drafts_dir, episode_script_relpath
from lib.project_manager import effective_mode

#: 审核状态：not_applicable=该集不走 gate；no_step1=适用但 step1 未产出；
#: pending_review=step1 已产出但未经确认（或确认后内容又变）→ 阻塞 step2；confirmed=已确认放行。
ReviewStatus = Literal["not_applicable", "no_step1", "pending_review", "confirmed"]

#: 确认记录在 episode 条目上的字段名：``{"fingerprint": str, "confirmed_at": ISO8601}``。
REVIEW_FIELD = "step1_review"


def find_episode(project: dict[str, Any], episode: int) -> dict[str, Any] | None:
    """返回 project.json ``episodes[]`` 中 ``episode == N`` 的条目，缺失则 None。"""
    for ep in project.get("episodes") or []:
        if ep.get("episode") == episode:
            return ep
    return None


def is_applicable(project: dict[str, Any], episode: int) -> bool:
    """gate 是否适用于该集：content_mode ∈ {drama, narration} 且 effective_mode 非 reference_video。"""
    if project.get("content_mode") not in STEP1_FILENAMES:
        return False
    return effective_mode(project=project, episode=find_episode(project, episode) or {}) != "reference_video"


def step1_path(project_path: Path, project: dict[str, Any], episode: int) -> Path | None:
    """该集结构化 step1 中间态文件路径；不适用 gate 时返回 None。"""
    if not is_applicable(project, episode):
        return None
    # is_applicable 为真 ⇒ content_mode ∈ STEP1_FILENAMES；索引取值（非 .get）避免 Any | None。
    return episode_drafts_dir(project_path, episode) / STEP1_FILENAMES[project["content_mode"]]


def content_fingerprint(path: Path) -> str | None:
    """step1 内容指纹：合法 JSON 取规范化 dump 的 sha256（键序 / 空白重排不改指纹、语义变更才改），
    非 JSON 退化为原始字节 sha256；文件不存在（FileNotFoundError）时 None。

    只把「文件不存在」降级为 None（→ no_step1、gate 放行）；权限不足、目录占位、短暂 I/O 等其它
    OSError 一律向上抛，避免把真实文件系统故障静默当成「step1 未产出」而误放行 step2。"""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    try:
        canonical = json.dumps(json.loads(raw), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except (ValueError, UnicodeDecodeError):
        return hashlib.sha256(raw).hexdigest()
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
