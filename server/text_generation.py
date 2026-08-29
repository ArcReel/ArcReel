"""Host-independent implementations for ArcReel text-generation tools."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
from collections import Counter
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, NamedTuple, cast

from pydantic import BaseModel, ValidationError

from lib import script_review
from lib.artifact_manifest import (
    ArtifactBasis,
    ArtifactEntryRekeyReceipt,
    ArtifactKey,
    ProjectArtifactManifestAdapter,
)
from lib.artifact_provenance import ScriptPlanPromptVariant, build_script_plan_request
from lib.artifact_registration import ArtifactRegistrationReceipt
from lib.asset_types import BUCKET_KEY, asset_name_comparison_key, normalize_asset_bucket
from lib.async_thread import run_noninterruptible_sync, run_sync_transaction
from lib.config.resolver import ConfigResolver
from lib.content_digest import prefixed_sha256_file
from lib.custom_provider.duration_presets import DEFAULT_FALLBACK
from lib.db import async_session_factory
from lib.draft_quarantine import (
    PROMOTE_TOOL_NAME,
    QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
    QUARANTINE_KIND_PROMPT_AUTHORING,
    QUARANTINE_KIND_SCRIPT_PLAN,
    clear_quarantine,
    quarantine_and_report,
    quarantine_exists,
    quarantine_path,
    read_quarantine,
)
from lib.draft_violation import DraftViolation, collect_violations
from lib.episode_paths import (
    REFERENCE_VIDEO_SCRIPT_PLAN_FILENAME,
    REFERENCE_VIDEO_SCRIPT_PLAN_LEGACY_FILENAME,
    SCRIPT_PLAN_FILENAMES,
    SCRIPT_PLAN_LEGACY_FILENAMES,
    episode_drafts_dir,
)
from lib.formal_write import FormalWriteReceipt, formal_write_transaction, project_metadata_lock
from lib.i18n import _ as translate
from lib.path_safety import PathTraversalError, safe_join
from lib.project_manager import ProjectManager, is_reference_video_project
from lib.prompt_builders_reference import build_reference_units_split_prompt
from lib.prompt_builders_script import append_user_instructions, build_narration_split_prompt, build_normalize_prompt
from lib.reference_video.draft_validation import (
    validate_dialogue_load,
    validate_source_text_anchor,
    validate_unit_text,
)
from lib.reference_video.script_preview import (
    WARN_REFERENCE_AUDIO_OVERFLOW,
    WARN_SILENT_EPISODE,
    WARN_SILENT_MODEL,
    WARN_SPEAKER_WITHOUT_AUDIO,
    WARN_UNIT_WITHOUT_SCENE,
    derive_utterances,
    derive_voice_bindings,
    unit_lacks_scene_reference,
)
from lib.reference_video.text_parser import extract_mentions
from lib.reference_video.voice_settings import VoiceRenderSettings
from lib.script_generator import ScriptGenerator
from lib.script_models import (
    NarrationScriptPlanDraft,
    build_drama_normalized_script_model,
    build_reference_units_script_plan_model,
)
from lib.script_plan_entries import SCOPE_ALL, SCOPE_STALE, ScriptPlanEntryError
from lib.speech_composition import admit_script_unit
from lib.speech_rate import project_speech_rate_override
from lib.text_backends.base import DEFAULT_MAX_OUTPUT_TOKENS, TextTaskType
from lib.text_backends.base import TextGenerationRequest as BackendTextGenerationRequest
from lib.text_generator import TextGenerator
from lib.text_utils import strip_json_code_fences
from server.services.video_caps import (
    constrained_caps_durations,
    reference_unit_duration_tiers,
    resolve_video_caps,
)

logger = logging.getLogger(__name__)

MAX_INSTRUCTIONS_LEN = 4000


@dataclass(frozen=True, slots=True)
class TextGenerationRequest:
    episode: int
    source: str | None = None
    instructions: str | None = None
    dry_run: bool = False
    #: 提示词编写的重写范围：``"stale"``（默认）只重写内容失配与新增的条目，``"all"`` 整集重写。
    scope: str = SCOPE_STALE
    #: 只重写这些条目；非空时即为本次范围，与 ``scope="all"`` 互斥（同时给出即请求自相矛盾）。
    entry_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.episode, bool) or not isinstance(self.episode, int) or self.episode < 1:
            raise ValueError("episode must be a positive integer")
        if self.scope not in {SCOPE_ALL, SCOPE_STALE}:
            raise ValueError(f"scope must be {SCOPE_ALL!r} or {SCOPE_STALE!r}")
        # 队列 payload 经 JSON 往返后 entry_ids 是 list：在此归一为 tuple，让「从工具入口构造」
        # 与「从 payload 还原」两条路径得到同一个值，任务事实比对才不会因容器类型分叉。
        entry_ids = tuple(self.entry_ids)
        if any(not isinstance(entry_id, str) or not entry_id for entry_id in entry_ids):
            raise ValueError("entry_ids must be non-empty strings")
        if entry_ids and self.scope == SCOPE_ALL:
            raise ValueError("entry_ids 与 scope='all' 互斥：要么整集重写，要么只重写指定条目")
        object.__setattr__(self, "entry_ids", entry_ids)

    @property
    def authoring_scope(self) -> str | tuple[str, ...]:
        """传给 ``ScriptGenerator.generate`` 的重写范围。"""
        return self.entry_ids or self.scope

    def to_payload(self) -> dict[str, object]:
        """队列任务 payload：只用 JSON 原生类型。

        ``asdict`` 会把 ``entry_ids`` 留成 tuple，而从数据库读回来的同一份 payload 是 list；
        「本次请求与在跑任务是否同一件事」的比对按值相等判定，两种容器类型会让同一个请求
        判成不同，把幂等提交变成一次任务冲突。
        """
        return {**asdict(self), "entry_ids": list(self.entry_ids)}


@dataclass(frozen=True, slots=True)
class TextGenerationResult:
    message: str


class CompensableTextGenerationResult(TextGenerationResult):
    """Text result carrying runtime-only cancellation compensation."""

    __slots__ = ("_cancel_compensation", "payload")

    def __init__(
        self,
        message: str,
        cancel_compensation: Callable[[], None],
        *,
        payload: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        object.__setattr__(self, "_cancel_compensation", cancel_compensation)
        object.__setattr__(self, "payload", payload)

    def compensate_cancelled(self) -> None:
        self._cancel_compensation()


@dataclass(frozen=True, slots=True)
class _ScriptPlanCancellationReceipt:
    project_path: Path
    lock_paths: tuple[Path, ...]
    files: FormalWriteReceipt
    manifest: ArtifactRegistrationReceipt

    def compensate_cancelled(self) -> None:
        self._compensate(self.lock_paths)

    def compensate_cancelled_while_draft_locked(self) -> None:
        self._compensate(self.lock_paths[1:])

    def _compensate(self, lock_paths: tuple[Path, ...]) -> None:
        pm = ProjectManager(str(self.project_path.parent))
        with ExitStack() as locks:
            for path in lock_paths:
                locks.enter_context(pm.file_lock(path))
            with project_metadata_lock(self.project_path):
                adapter = self.manifest.adapter
                key = self.manifest.key
                if adapter is None or key is None:
                    raise RuntimeError("script_plan cancellation receipt has no Manifest target")
                if self.manifest.changed and adapter.get_entry(key) != self.manifest.registered:
                    return
                if not self.files.compensate_cancelled():
                    return
                self.manifest.compensate_cancelled()


@dataclass(frozen=True, slots=True)
class _EpisodeScriptCancellationReceipt:
    project_path: Path
    episode: int
    files: FormalWriteReceipt
    manifest: ArtifactEntryRekeyReceipt

    def compensate_cancelled(self) -> None:
        script_path = self.project_path / "scripts" / f"episode_{self.episode}.json"
        pm = ProjectManager(str(self.project_path.parent))
        with pm.file_lock(script_path), project_metadata_lock(self.project_path):
            if not self.manifest.matches_current() or not self.files.matches_current():
                return
            if not self.files.compensate_cancelled():
                return
            self.manifest.compensate()


async def _run_compensable_script_plan_commit(
    commit: Callable[..., None],
    /,
    *args: Any,
) -> _ScriptPlanCancellationReceipt:
    receipts: list[_ScriptPlanCancellationReceipt] = []
    try:
        await run_sync_transaction(commit, *args, receipts)
    except asyncio.CancelledError:
        if receipts:
            await run_noninterruptible_sync(receipts[0].compensate_cancelled_while_draft_locked)
        raise
    if len(receipts) != 1:
        raise RuntimeError("script_plan commit did not return cancellation state")
    return receipts[0]


async def _run_compensable_quarantine(
    project_path: Path,
    episode: int,
    kind: str,
    content: dict[str, Any],
    violations: list[DraftViolation],
    source: str | None,
    base_fingerprint: str | None,
) -> str:
    receipts: list[FormalWriteReceipt] = []
    try:
        report = await run_sync_transaction(
            _quarantine_invalid_script_plan_generation,
            project_path,
            episode,
            kind,
            content,
            violations,
            source,
            base_fingerprint,
            receipts,
        )
    except asyncio.CancelledError:
        if receipts:
            await run_noninterruptible_sync(receipts[0].compensate_cancelled)
        raise
    if len(receipts) != 1:
        raise RuntimeError("quarantine write did not return cancellation state")
    return report


class TextGenerationError(Exception):
    """Expected refusal from a text-generation handler."""


def _draft_file_revision(path: Path) -> str | None:
    try:
        return prefixed_sha256_file(path)
    except FileNotFoundError:
        return None


def _generation_baselines(
    draft_path: Path,
    formal_path: Path,
) -> tuple[str | None, str | None]:
    return _draft_file_revision(draft_path), script_review.content_fingerprint(formal_path)


def _assert_draft_revision(path: Path, expected: str | None) -> None:
    actual = _draft_file_revision(path)
    if actual != expected:
        raise TextGenerationError(
            f"draft_revision_conflict: draft changed during generation; expected {expected}, actual {actual}"
        )


def _quarantine_formal_generation_conflict(
    project_path: Path,
    episode: int,
    kind: str,
    content: dict[str, Any],
    source: str | None,
    expected: str | None,
    actual: str | None,
) -> str:
    return quarantine_and_report(
        project_path,
        episode,
        kind,
        content=content,
        violations=[
            DraftViolation(
                f"正式内容在模型生成期间已变化（expected {expected}, actual {actual}）；"
                "本次生成结果已保留为草稿，请合并最新正式内容后再晋升",
                code="formal_revision_conflict",
            )
        ],
        meta={"source": source, "base_fingerprint": expected},
    )


def _commit_generated_reference_script_plan(
    project_path: Path,
    episode: int,
    content: dict[str, Any],
    expected_fingerprint: str | None,
    basis: ArtifactBasis,
    before_commit: Callable[[], None] | None = None,
    cancellation_receipts: list[_ScriptPlanCancellationReceipt] | None = None,
) -> None:
    if before_commit is not None:
        before_commit()
    draft_path = quarantine_path(project_path, episode, QUARANTINE_KIND_SCRIPT_PLAN)
    prompt_authoring_path = quarantine_path(project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING)
    formal_path = script_review.official_reference_script_plan_path(project_path, episode)
    adapter = ProjectArtifactManifestAdapter(project_path)
    key = ArtifactKey.episode_script_plan(episode)
    pm = ProjectManager(str(project_path.parent))
    file_receipts: list[FormalWriteReceipt] = []
    with pm.file_lock(prompt_authoring_path), script_review.script_plan_write_lock(project_path, episode):
        previous = adapter.get_entry(key)
        with formal_write_transaction(
            formal_path,
            prompt_authoring_path,
            draft_path,
            cancellation_receipts=file_receipts,
        ):
            script_review.write_script_plan_locked(
                project_path,
                episode,
                content,
                expected_fingerprint=expected_fingerprint,
                basis=basis,
            )
            clear_quarantine(project_path, episode, QUARANTINE_KIND_SCRIPT_PLAN)
        registered = adapter.get_entry(key)
    if cancellation_receipts is None:
        return
    cancellation_receipts.append(
        _ScriptPlanCancellationReceipt(
            project_path=project_path,
            lock_paths=(draft_path, prompt_authoring_path, formal_path),
            files=file_receipts[0],
            manifest=ArtifactRegistrationReceipt(
                adapter=adapter,
                key=key,
                registered=registered,
                previous=previous,
                changed=registered != previous,
            ),
        )
    )


def _commit_single_script_plan(
    project_path: Path,
    episode: int,
    script_plan_path: Path,
    kind: str,
    content: dict[str, Any],
    expected_fingerprint: Any,
    basis: ArtifactBasis | None,
    cancellation_receipts: list[_ScriptPlanCancellationReceipt] | None = None,
) -> None:
    draft_path = quarantine_path(project_path, episode, kind)
    adapter = ProjectArtifactManifestAdapter(project_path)
    key = ArtifactKey.episode_script_plan(episode)
    file_receipts: list[FormalWriteReceipt] = []
    with script_review.formal_script_plan_lock(project_path, episode, script_plan_path):
        previous = adapter.get_entry(key)
        with formal_write_transaction(
            script_plan_path,
            draft_path,
            cancellation_receipts=file_receipts,
        ):
            script_review.write_formal_script_plan_locked(
                project_path,
                episode,
                script_plan_path,
                content,
                expected_fingerprint=expected_fingerprint,
                basis=basis,
            )
            clear_quarantine(project_path, episode, kind)
        registered = adapter.get_entry(key)
    if cancellation_receipts is None:
        return
    cancellation_receipts.append(
        _ScriptPlanCancellationReceipt(
            project_path=project_path,
            lock_paths=(draft_path, script_plan_path),
            files=file_receipts[0],
            manifest=ArtifactRegistrationReceipt(
                adapter=adapter,
                key=key,
                registered=registered,
                previous=previous,
                changed=registered != previous,
            ),
        )
    )


def _quarantine_invalid_script_plan_generation(
    project_path: Path,
    episode: int,
    kind: str,
    content: dict[str, Any],
    violations: list[DraftViolation],
    source: str | None,
    base_fingerprint: str | None,
    cancellation_receipts: list[FormalWriteReceipt] | None = None,
) -> str:
    with formal_write_transaction(
        quarantine_path(project_path, episode, kind),
        cancellation_receipts=cancellation_receipts,
    ):
        return quarantine_and_report(
            project_path,
            episode,
            kind,
            content=content,
            violations=violations,
            meta={"source": source or None, "base_fingerprint": base_fingerprint},
        )


def _instructions(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TextGenerationError("❌ 参数错误：instructions 必须是文本")
    if len(value) > MAX_INSTRUCTIONS_LEN:
        raise TextGenerationError(
            f"❌ 参数错误：instructions 过长（{len(value)} 字符，上限 {MAX_INSTRUCTIONS_LEN}），请精简后重试"
        )
    return value.strip() or None


async def fetch_video_caps(
    project: dict[str, Any],
    *,
    generation_mode: str | None = None,
    config_resolver: ConfigResolver | None = None,
) -> tuple[int | None, list[int]]:
    if config_resolver is None:
        caps = await resolve_video_caps(project)
    else:
        caps = await resolve_video_caps(project, config_resolver=config_resolver)
    durations = [int(duration) for duration in caps.get("supported_durations") or []]
    durations = constrained_caps_durations(project, caps, durations, generation_mode=generation_mode)
    default = caps.get("default_duration")
    return (int(default) if isinstance(default, int | float) else None), durations


def _parse_script_plan_json(response_text: str, model: type[BaseModel], *, label: str, top_shape: str) -> dict:
    """解析并校验 script_plan 结构化响应为 dict；校验失败 fail-loud 抛 ValueError，不返回未校验内容。

    ``model`` 取自调用处用 ``supported_durations`` 构造的同一份动态 schema（即 response_schema），
    令本地校验与 response_schema 同口径：即使 backend 未严格执行 schema，超出 supported_durations
    的时长、缺字段也在此被拦截。校验失败抛错而非降级保留原始 JSON——否则未校验内容会被当成正式
    script_plan 文件落盘（下游读取仅守最外层形状、放行），把非法时长 / 缺字段拖到 prompt_authoring 或最终
    save_script 才暴露。与 narration 的 _load_narration_script_plan 严格读取同口径：只有经 schema
    校验的内容才成为持久化的 script_plan 真值源。
    """
    text = strip_json_code_fences(response_text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"{label} JSON 解析失败: {e}")
    if not isinstance(data, dict):
        raise ValueError(f"{label}结构异常：顶层应为对象 {top_shape}")
    try:
        return model.model_validate(data).model_dump()
    except ValidationError as e:
        raise ValueError(f"{label}结构校验失败: {e}") from e


def _parse_normalized_content(response_text: str, model: type[BaseModel]) -> dict:
    """drama script_plan（normalize）响应解析：见 ``_parse_script_plan_json``。"""
    return _parse_script_plan_json(response_text, model, label="script_plan 规范化内容", top_shape="{title, scenes}")


def _load_novel_source(project_path: Path, source: str | None) -> str:
    """读取 script_plan 工具的源文：指定 source 文件或 ``source/`` 目录全部文本；异常情况抛 ValueError。

    normalize / split 两类 script_plan 工具共用：路径越界、文件缺失、目录为空、内容为空均 fail-fast，
    调用方把消息包装为工具错误信封。

    ``source`` 除工具自己产出外，也会被 ``revalidate_reference_script_plan_draft`` 传入草稿的
    ``meta.source``——那是 Agent 可编辑的 JSON 字段，类型标注管不住运行时值。非 str/None 时
    直接抛 ValueError 而非让它落进 ``safe_join``：那里对非路径类型是 ``TypeError``，本函数
    的调用方一律只接 ValueError，放行 TypeError 会在内容确认的读时重算里变成未处理的
    500，而不是「无法重算」这个本该有的降级态。
    """
    if source is not None and not isinstance(source, str):
        raise ValueError(f"meta.source 类型非法，须为字符串或 null：{source!r}")
    if source:
        try:
            source_path = safe_join(project_path, source)
        except PathTraversalError as exc:
            raise ValueError(f"路径超出项目目录: {source}") from exc
        if not source_path.is_file():
            # 存在但不是文件（如指向目录）同样按「未找到源文件」处理：直接 read_text() 对目录
            # 会抛 IsADirectoryError，落进本函数调用方一律只接的 ValueError 之外，在内容确认的
            # 读时重算里会变成未处理的 500。
            raise ValueError(f"未找到源文件: {source_path}")
        novel_text = source_path.read_text(encoding="utf-8")
    else:
        source_dir = project_path / "source"
        if not source_dir.exists() or not any(source_dir.iterdir()):
            raise ValueError(f"source/ 目录为空或不存在: {source_dir}")
        texts = [
            f.read_text(encoding="utf-8")
            for f in sorted(source_dir.iterdir())
            if f.is_file() and f.suffix in (".txt", ".md", ".text")
        ]
        novel_text = "\n\n".join(texts)
    if not novel_text.strip():
        raise ValueError("小说原文为空")
    return novel_text


def _load_script_plan_source_with_basis(
    project_path: Path,
    source: str | None,
    project: dict[str, Any],
    episode: int,
    expected_variant: ScriptPlanPromptVariant,
) -> tuple[str, dict[str, object], ArtifactBasis]:
    """Freeze the exact source text and project semantics consumed by a script_plan request."""

    novel_text = _load_novel_source(project_path, source)
    prompt_inputs, basis = build_script_plan_request(
        novel_text,
        episode=episode,
        project=project,
        expected_variant=expected_variant,
    )
    return novel_text, prompt_inputs, basis


def _uses_reference_video_units(project_data: dict[str, Any]) -> bool:
    """项目是否产出视频单元——草稿只在这条路径上有意义。

    ad 的 unit 是广告分镜的派生索引、无 script_plan 拆分，即使走参考生视频也不在此列。
    """
    if project_data.get("content_mode", "narration") == "ad":
        return False
    return is_reference_video_project(project_data)


def _prompt_authoring_blocking_quarantine_kinds(project_data: dict[str, Any]) -> tuple[str, ...]:
    """该项目上会阻塞 prompt_authoring 的草稿来源。

    只返回项目当前生成模式对应的草稿来源。其他生成模式的遗留草稿没有当前写入方负责清理，
    若参与判定会把该集永久卡死。参考生视频的 prompt_authoring 提示词编写自身也有草稿位，故比其它变体
    多一个来源。
    """
    if _uses_reference_video_units(project_data):
        return (QUARANTINE_KIND_SCRIPT_PLAN, QUARANTINE_KIND_PROMPT_AUTHORING)
    kind = script_review.script_plan_quarantine_kind(project_data)
    return (kind,) if kind is not None else ()


def _resolve_script_plan_path(
    project_path: Path, episode: int, project_data: dict[str, Any]
) -> tuple[Path, str] | None:
    """Return (script_plan_md path, hint text for missing-file error)；ad 一键生成不依赖 script_plan，返回 None。"""
    content_mode = project_data.get("content_mode", "narration")
    if content_mode == "ad":
        # ad 创作输入是 project.json 的 brief + 商品信息 + target_duration，
        # ScriptGenerator 的 ad 分支不读 drafts/ 中间文件。
        return None
    generation_mode = project_data.get("generation_mode")
    drafts_path = episode_drafts_dir(project_path, episode)
    if generation_mode == "reference_video":
        # reference_video 生成需结构化 script_plan JSON；仅存旧版 .md 时给出与
        # ScriptGenerator._load_reference_script_plan 一致的重拆迁移提示，而非笼统的缺文件错误。
        rv_json = drafts_path / REFERENCE_VIDEO_SCRIPT_PLAN_FILENAME
        if not rv_json.exists() and (drafts_path / REFERENCE_VIDEO_SCRIPT_PLAN_LEGACY_FILENAME).exists():
            return rv_json, (
                f"调用 generate_script_plan 把旧 {REFERENCE_VIDEO_SCRIPT_PLAN_LEGACY_FILENAME} "
                f"重新拆分为结构化 {REFERENCE_VIDEO_SCRIPT_PLAN_FILENAME}"
            )
        return rv_json, "generate_script_plan tool"
    if content_mode != "narration" and content_mode in SCRIPT_PLAN_FILENAMES:
        # SCRIPT_PLAN_FILENAMES 中除 narration 外的模式走两段式结构化 JSON（见 ADR 0041）。
        # narration 虽也在 SCRIPT_PLAN_FILENAMES，但另有旧 .md 迁移提示分支，需先排除。
        return drafts_path / SCRIPT_PLAN_FILENAMES[content_mode], "generate_script_plan tool"
    # narration 生成需结构化 script_plan JSON；仅存旧版 .md 时给出与
    # ScriptGenerator._load_narration_script_plan 一致的重切迁移提示，而非笼统的缺文件错误。
    narration_json = SCRIPT_PLAN_FILENAMES["narration"]
    narration_legacy_md = SCRIPT_PLAN_LEGACY_FILENAMES["narration"][0]
    script_plan_json = drafts_path / narration_json
    if not script_plan_json.exists() and (drafts_path / narration_legacy_md).exists():
        return (
            script_plan_json,
            f"调用 generate_script_plan 把旧 {narration_legacy_md} 重新拆分为结构化 {narration_json}",
        )
    return script_plan_json, "generate_script_plan tool"


def _episode_generation_preflight(project_path: Path, episode: int, *, enforce_review_gate: bool) -> None:
    try:
        project_data = json.loads((project_path / "project.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        project_data = {}

    for kind in _prompt_authoring_blocking_quarantine_kinds(project_data):
        if quarantine_exists(project_path, episode, kind):
            path = quarantine_path(project_path, episode, kind)
            draft = read_quarantine(project_path, episode, kind)
            if draft is None:
                action = f"请修复草稿信封，再调用 {PROMOTE_TOOL_NAME} 校验晋升。"
            elif draft.violations:
                action = f"请按草稿内 violations 的定位修改 content，再调用 {PROMOTE_TOOL_NAME} 晋升。"
            else:
                action = f"这是可编辑草稿；请保留已有修改，再调用 {PROMOTE_TOOL_NAME} 校验晋升。"
            raise TextGenerationError(f"⏸️ 本集有草稿待处置（{path}），prompt_authoring 视觉生成已中止。{action}")

    script_plan = _resolve_script_plan_path(project_path, episode, project_data)
    if script_plan is not None:
        script_plan_path, hint = script_plan
        if not script_plan_path.exists():
            raise TextGenerationError(f"❌ 未找到脚本规划文件: {script_plan_path}\n   请先完成 {hint}")

    if enforce_review_gate and script_review.gate_blocks_prompt_authoring(project_path, project_data, episode):
        raise TextGenerationError(
            "⏸️ script_plan 结构化中间态尚未完成内容确认，prompt_authoring 视觉生成被阻塞。"
            "请在 Web 端审阅并确认本集 script_plan 内容后再生成剧本。"
        )


async def generate_episode_script(
    request: TextGenerationRequest,
    *,
    project_name: str,
    projects: ProjectManager,
    config_resolver: ConfigResolver,
) -> TextGenerationResult:
    episode = request.episode
    instructions = _instructions(request.instructions)
    project_path = projects.get_project_path(project_name)
    await asyncio.to_thread(
        _episode_generation_preflight,
        project_path,
        episode,
        enforce_review_gate=not request.dry_run,
    )

    try:
        if request.dry_run:
            generator = await asyncio.to_thread(
                ScriptGenerator,
                project_path,
                config_resolver=config_resolver,
            )
            prompt = await generator.build_prompt(episode, instructions=instructions, scope=request.authoring_scope)
            return TextGenerationResult(f"DRY RUN — 以下是将发送给文本模型的 Prompt:\n\n{prompt}")

        generator = await ScriptGenerator.create(
            project_path,
            config_resolver=config_resolver,
        )
        file_receipts: list[FormalWriteReceipt] = []
        manifest_receipts: list[ArtifactEntryRekeyReceipt] = []
        rewritten: list[str] = []
        try:
            result_path = await generator.generate(
                episode=episode,
                instructions=instructions,
                scope=request.authoring_scope,
                rewritten_entry_ids=rewritten,
                cancellation_file_receipts=file_receipts,
                cancellation_manifest_receipts=manifest_receipts,
            )
        except asyncio.CancelledError:
            if len(file_receipts) == len(manifest_receipts) == 1:
                receipt = _EpisodeScriptCancellationReceipt(
                    project_path,
                    episode,
                    file_receipts[0],
                    manifest_receipts[0],
                )
                await run_noninterruptible_sync(receipt.compensate_cancelled)
            raise
    except ScriptPlanEntryError as exc:
        # 点名的条目不在当前脚本规划内是调用方的错：报「拒绝生成」而不是让它冒成 internal_error，
        # 后者会引导 Agent 原样重试同一份必然失败的参数。
        raise TextGenerationError(f"❌ 重写范围无效: {exc}") from exc
    except FileNotFoundError as exc:
        raise TextGenerationError(f"❌ 文件错误: {exc}") from exc
    rewritten_note = "、".join(rewritten) if rewritten else "无（其余条目原样保留）"
    summary = f"✅ 剧本生成完成: {result_path}\n   本次重写条目: {rewritten_note}"
    if not file_receipts and not manifest_receipts:
        return TextGenerationResult(summary)
    if len(file_receipts) != 1 or len(manifest_receipts) != 1:
        raise RuntimeError("episode script commit did not return cancellation state")
    receipt = _EpisodeScriptCancellationReceipt(project_path, episode, file_receipts[0], manifest_receipts[0])
    return CompensableTextGenerationResult(summary, receipt.compensate_cancelled)


# ---------------------------------------------------------------------------
# confirm_script_review
# ---------------------------------------------------------------------------


async def confirm_script_review(
    episode: int,
    *,
    project_name: str,
    projects: ProjectManager,
    config_resolver: ConfigResolver,
) -> TextGenerationResult:
    from server.services.script_review import ScriptReviewError, ScriptReviewService

    try:
        state = await ScriptReviewService(projects, config_resolver=config_resolver).confirm(project_name, episode)
    except ScriptReviewError as exc:
        raise TextGenerationError(f"❌ 无法完成 script_plan 内容确认（{exc.code}）：{exc.message or exc.code}") from exc
    return TextGenerationResult(
        f"✅ 第 {episode} 集 script_plan 已确认，prompt_authoring 视觉生成已放行（status={state['status']}）"
    )


# ---------------------------------------------------------------------------
# drama generate_script_plan variant
# ---------------------------------------------------------------------------


async def _fetch_caps_with_fallback(
    project: dict[str, Any],
    episode: int,
    *,
    config_resolver: ConfigResolver | None = None,
) -> tuple[int | None, list[int]]:
    """Script normalization is best-effort: prompt生成 不该被能力查询失败堵住。

    Soft-fallbacks to ``duration_presets.DEFAULT_FALLBACK`` so the LLM still
    receives a usable duration constraint set if the resolver hiccups —— 与
    自定义供应商写入层的保守默认同一真相源，避免软回退口径含供应商未必支持的时长。

    时长已按项目分辨率经联动约束收窄。参考图约束不在此施加：走参考生视频的项目 script_plan 用
    参考生视频变体（见 ``_fetch_reference_caps_with_fallback``），本 helper
    服务的 drama normalize / narration 拆分两个工具按分工不服务该路径。

    ``default_duration`` 非返回集合成员时按 None 处理（即回到「auto」档，由模型按内容节奏选）：
    项目存的是用户配置的原样值，收窄后（或软回退到 ``DEFAULT_FALLBACK`` 后）它可能落在集合外，
    而 ``build_normalize_prompt`` 对非成员 default 是 fail-loud 的——不归 None 会把「已保存的
    越界默认时长」变成整个工具的硬失败。与 ``_fetch_reference_caps_with_fallback`` 同口径。
    """
    try:
        if config_resolver is None:
            default_int, durations = await fetch_video_caps(project, generation_mode=None)
        else:
            default_int, durations = await fetch_video_caps(
                project,
                generation_mode=None,
                config_resolver=config_resolver,
            )
    except (FileNotFoundError, ValueError) as exc:
        logger.info("video_capabilities 不可解析，使用 fallback %s：%s", DEFAULT_FALLBACK, exc)
        return None, list(DEFAULT_FALLBACK)
    except Exception as exc:  # noqa: BLE001
        logger.warning("video_capabilities 查询异常，使用 fallback %s：%s", DEFAULT_FALLBACK, exc)
        return None, list(DEFAULT_FALLBACK)
    if not durations:
        durations = list(DEFAULT_FALLBACK)
    if default_int is not None and default_int not in durations:
        default_int = None
    return default_int, durations


async def generate_drama_script_plan(
    request: TextGenerationRequest,
    *,
    project_name: str,
    projects: ProjectManager,
    config_resolver: ConfigResolver,
) -> TextGenerationResult:
    episode = request.episode
    instructions = _instructions(request.instructions)
    project_path = projects.get_project_path(project_name)
    project = await asyncio.to_thread(projects.load_project_readonly, project_name)
    try:
        novel_text, prompt_inputs, script_plan_basis = await asyncio.to_thread(
            _load_script_plan_source_with_basis,
            project_path,
            request.source,
            project,
            episode,
            "drama",
        )
    except ValueError as exc:
        raise TextGenerationError(f"❌ {exc}") from exc

    try:
        default_duration, supported_durations = await _fetch_caps_with_fallback(
            project,
            episode,
            config_resolver=config_resolver,
        )
        prompt = build_normalize_prompt(
            novel_text=novel_text,
            project_overview=cast(dict[str, Any], prompt_inputs["project_overview"]),
            style=cast(str, prompt_inputs["style"]),
            characters=cast(dict[str, Any], prompt_inputs["characters"]),
            scenes=cast(dict[str, Any], prompt_inputs["scenes"]),
            props=cast(dict[str, Any], prompt_inputs["props"]),
            default_duration=default_duration,
            supported_durations=supported_durations,
            episode=episode,
            source_kind=cast(str, prompt_inputs["source_kind"]),
            episode_outline=cast(dict[str, Any] | None, prompt_inputs["episode_outline"]),
            next_episode_outline=cast(dict[str, Any] | None, prompt_inputs["next_episode_outline"]),
            target_language=cast(str, prompt_inputs["target_language"]),
            source_language=cast(str | None, prompt_inputs["source_language"]),
            speech_rate_override=cast(float | None, prompt_inputs["speech_rate_override"]),
        )
        prompt = append_user_instructions(prompt, instructions)

        if request.dry_run:
            return TextGenerationResult(
                f"DRY RUN — 以下是将发送给文本模型的 Prompt:\n\n{prompt}\n\nPrompt 长度: {len(prompt)} 字符"
            )

        draft_path = quarantine_path(project_path, episode, QUARANTINE_KIND_DRAMA_SCRIPT_PLAN)
        script_plan_path = episode_drafts_dir(project_path, episode) / SCRIPT_PLAN_FILENAMES["drama"]
        async with ProjectManager(str(project_path.parent)).async_file_lock(draft_path):
            draft_baseline, formal_baseline = await asyncio.to_thread(
                _generation_baselines,
                draft_path,
                script_plan_path,
            )
        schema = build_drama_normalized_script_model(supported_durations)
        generator = await TextGenerator.create(TextTaskType.SCRIPT, project_name=project_name)
        result = await generator.generate(
            BackendTextGenerationRequest(
                prompt=prompt,
                response_schema=schema,
                max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
            ),
            project_name=project_name,
        )
        content = _parse_normalized_content(result.text, schema)
        raw_scenes = content.get("scenes")
        if not isinstance(raw_scenes, list) or not raw_scenes:
            raise ValueError("script_plan 规范化内容结构异常：scenes 必须是非空的分镜对象数组")
        for scene in raw_scenes:
            admission = admit_script_unit("scenes", scene, ignore_marker=True)
            if admission.allowed:
                scene.pop("needs_replan", None)
            else:
                scene["needs_replan"] = True

        async with ProjectManager(str(project_path.parent)).async_file_lock(draft_path):
            _assert_draft_revision(draft_path, draft_baseline)
            try:
                cancellation_receipt = await _run_compensable_script_plan_commit(
                    _commit_single_script_plan,
                    project_path,
                    episode,
                    script_plan_path,
                    QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
                    content,
                    formal_baseline,
                    script_plan_basis,
                )
            except script_review.ScriptPlanWriteConflict as exc:
                raise TextGenerationError(
                    _quarantine_formal_generation_conflict(
                        project_path,
                        episode,
                        QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
                        content,
                        request.source,
                        formal_baseline,
                        exc.actual,
                    )
                ) from exc

        return CompensableTextGenerationResult(
            f"✅ 规范化剧本（结构化内容）已保存: {script_plan_path}\n📊 生成统计: {len(raw_scenes)} 个分镜",
            cancellation_receipt.compensate_cancelled,
        )
    except TextGenerationError:
        raise
    except Exception as exc:
        raise TextGenerationError(f"generate_script_plan 失败: {exc}") from exc


# ---------------------------------------------------------------------------
# reference-video generate_script_plan variant
# ---------------------------------------------------------------------------


class ReferenceSplitCaps(NamedTuple):
    """rv 拆分用的视频能力：两套逐 unit 档位 + 派生上限 + 用户偏好 + 声音输入档。

    ``reference_durations`` / ``text_durations`` 是带 / 不带 ``@`` 引用的 unit 各自的生效档位，
    ``durations`` 是二者的并集——schema 枚举与 prompt 候选集合取并集，因为落在任一套内的时长都
    可能合法；归属哪一套要等正文里的 `@[名称]` 提及确定后才知道。三者相等即该型号在当前分辨率下未声明
    生效的「参考图↔时长」联动约束，多数型号如此。

    ``voice`` 是同一次能力解析派生出的声音输入档，供声音相关的容忍 warning 消费——与时长档位同源
    于这一次解析，分两次查会让同一份产物的档位与声音提示描述不同时刻的配置。能力解析故障回退时
    档位相关的几位落到 ``VoiceRenderSettings`` 的字段默认，唯 ``requested_generate_audio`` 仍带着
    本集的无声意图（该位不依赖能力接口，回退分支独立解析后写回，见
    ``_fetch_reference_caps_with_fallback``）。携带值对象而非原始能力 dict：下游只需要声音那几位，
    穿一整个 dict 过接口会把能力 key 名耦合扩散到消费侧。
    """

    default_duration: int | None
    durations: list[int]
    reference_durations: list[int]
    text_durations: list[int]
    max_duration: int
    max_refs: int | None
    voice: VoiceRenderSettings

    def tiers_for(self, *, has_references: bool) -> list[int]:
        """该引用状态下的生效档位。"""
        return self.reference_durations if has_references else self.text_durations


async def _fetch_reference_caps_with_fallback(
    project: dict[str, Any],
    episode: int,
    *,
    config_resolver: ConfigResolver | None = None,
) -> ReferenceSplitCaps:
    """解析 rv 拆分所需的视频能力（见 ``ReferenceSplitCaps``）。

    与 ``_fetch_caps_with_fallback`` 同口径 best-effort：resolver 故障时回退
    ``duration_presets.DEFAULT_FALLBACK``、``max_refs`` 视为未声明。

    unit 是一次生成调用的单元，拆分阶段定的时长就是真正发给供应商的那个值，故档位取**经时长
    联动约束收窄后**的集合：不收窄的话（海螺 1080p 只接受 6 秒）script_plan 会按全集拆出超标的 unit，
    prompt_authoring 的枚举 schema 再把它判非法。

    收窄逐 unit 分两套（``reference_unit_duration_tiers``）：「参考图↔时长」约束只对真的带参考图
    的请求生效，整集一律按带图收窄会把无引用 unit 本可申请的短档也收掉。schema 枚举与 prompt
    候选取两套的并集——落在任一套内的时长都可能合法，具体归属由该 unit 正文里的 `@[名称]`
    提及决定，在正文解析之后逐 unit 判（见 ``_collect_reference_flat_violations``）。
    ``max_duration`` 随之是并集的最大值。
    ``default_duration`` 非并集成员（用户配置漂移）按 None 处理，避免 prompt 自相矛盾。
    """
    try:
        if config_resolver is None:
            caps = await resolve_video_caps(project)
        else:
            caps = await resolve_video_caps(project, config_resolver=config_resolver)
    except Exception as exc:  # noqa: BLE001
        logger.warning("video_capabilities 查询异常，使用 fallback %s：%s", DEFAULT_FALLBACK, exc)
        caps = {}
        # requested_generate_audio 不依赖能力接口（见 generation_context.py 同名字段注释），
        # 能力解析失败也不能连带丢失，否则本该报的 WARN_SILENT_EPISODE 会静默消失。
        try:
            resolver = config_resolver or ConfigResolver(async_session_factory)
            caps["requested_generate_audio"] = await resolver.video_generate_audio_for_project(project)
        except Exception as inner_exc:  # noqa: BLE001
            # 与其余能力字段的「不明时不额外收紧」相反：这里不明时收紧到 False——静默丢掉
            # 一次声音提示，好过在双重解析失败时把用户的无声意图错读成有声。
            logger.warning("video_generate_audio 独立解析也失败，声音提示按无声降级：%s", inner_exc)
            caps["requested_generate_audio"] = False
    durations = [int(d) for d in caps.get("supported_durations") or []]
    if not durations:
        durations = list(DEFAULT_FALLBACK)
    with_refs, without_refs = await reference_unit_duration_tiers(
        project,
        caps,
        durations,
        config_resolver=config_resolver,
    )
    unit_durations = sorted(set(with_refs) | set(without_refs))
    max_duration = max(unit_durations)
    raw_refs = caps.get("max_reference_images")
    max_refs = int(raw_refs) if isinstance(raw_refs, int | float) else None
    raw_default = caps.get("default_duration")
    default = int(raw_default) if isinstance(raw_default, int | float) else None
    if default is not None and default not in unit_durations:
        default = None
    return ReferenceSplitCaps(
        default_duration=default,
        durations=unit_durations,
        reference_durations=sorted(set(with_refs)),
        text_durations=sorted(set(without_refs)),
        max_duration=max_duration,
        max_refs=max_refs,
        voice=VoiceRenderSettings.from_caps(caps),
    )


def _validate_unit_duration_tier(label: str, duration: int, *, has_references: bool, caps: ReferenceSplitCaps) -> None:
    """按该 unit 的引用状态判时长是否落在生效档位内，出档抛 ``DraftViolation``。

    schema 的枚举卡的是两套档位的并集，一个带引用的 unit 因此仍可能取到只有无引用 unit 才
    合法的秒数——那样的 unit 执行期申请不到，等到入队才失败已无统一纠正入口。错误消息给出
    两条出路（换档位 / 去引用），与 prompt 里的教学同一口径。

    抛的是内容违约而非 ``ValueError``：这一类同样是 Agent 改一改草稿就能修好的，走草稿
    的修复闭环，不该退回丢弃重抽。
    """
    tiers = caps.tiers_for(has_references=has_references)
    if duration in tiers:
        return
    state = "带 `@` 资产引用" if has_references else "无 `@` 资产引用"
    remedy = (
        "；请改取该档位内的时长，或把次要资产融入描述文字、不用 `@` 引用"
        if has_references
        else "；请改取该档位内的时长"
    )
    raise DraftViolation(
        f"{label} 时长 {duration}s 不在{state}的 unit 的生效档位 {tiers} 内{remedy}",
        code="duration_off_tier",
        label=label,
    )


def _reference_unit_id(episode: int, index: int) -> str:
    """扁平产出第 ``index`` 个 unit（1 起）的 ``unit_id``。

    落盘内容、违约报告与降级提示三者指的必须是同一个 unit：各自拼一遍格式串时，改一处编号
    口径就会让报告指偏到另一个 unit。
    """
    return f"E{episode}U{index:02d}"


def _reference_unit_label(episode: int, index: int) -> str:
    """违约 / 提示条目的定位前缀（``unit E{集}U{两位序号}``）。"""
    return f"unit {_reference_unit_id(episode, index)}"


def _collect_reference_flat_violations(
    flat_units: list[dict[str, Any]],
    project: dict[str, Any],
    *,
    episode: int,
    novel_text: str,
    caps: ReferenceSplitCaps,
    source_language: str | None,
) -> list[DraftViolation]:
    """逐 unit 收齐 script_plan 扁平产出的全部违约（不在首个违约处中断）。

    schema 已卡死时长枚举与外层形状；此处补依赖运行时能力值 / 项目登记表 / 源文的约束——
    时长落在该 unit 引用状态对应的生效档位内、原文锚是源文逐字子串、正文语法与资产引用合法、
    台词量念得完。收齐而非首个即抛：报告要能一次列全所有坏 unit，否则 Agent 每修一处就要再跑
    一轮才知道下一处。

    时长档位与正文合并为一个入口：适用哪套档位取决于该 unit 正文里有没有 `@[名称]` 提及——
    正文解析不出时无从判档位，此时报出的也只会是同一个问题的另一种说法。
    """
    # 台词口播量的语速与 prompt 侧同源：项目级覆盖优先，否则按语言默认。
    speech_rate_override = project_speech_rate_override(project)
    violations: list[DraftViolation] = []
    for index, flat in enumerate(flat_units, start=1):
        label = _reference_unit_label(episode, index)
        duration = flat["duration_seconds"]
        source_text = flat["source_text"]
        text = flat["text"]

        def _check_text_and_tier(la: str = label, tx: str = text, d: int = duration) -> None:
            refs = validate_unit_text(la, tx, project, max_refs=caps.max_refs)
            _validate_unit_duration_tier(la, d, has_references=bool(refs), caps=caps)

        violations.extend(
            collect_violations(
                [
                    lambda la=label, st=source_text: validate_source_text_anchor(la, st, novel_text),
                    _check_text_and_tier,
                    lambda la=label, tx=text, d=duration: validate_dialogue_load(
                        la, tx, d, source_language, speech_rate_override
                    ),
                ]
            )
        )
    return violations


def _build_reference_units_from_flat(
    flat_units: list[dict[str, Any]],
    project: dict[str, Any],
    *,
    episode: int,
    max_refs: int | None,
) -> list[dict]:
    """把已校验通过的扁平产出派生为落盘的结构化 unit 表。

    LLM 只写内容，机器写结构：``unit_id`` 按数组序号编号，正文原样落盘。参考图不落盘——
    执行期再按正文 ``@[名称]`` 的首现顺序解析。调用方须先经
    ``_collect_reference_flat_violations`` 确认无违约；此处仍复判一次正文，让「校验看到的
    文本」与「落盘的正文」出自同一次解析。
    """
    units: list[dict] = []
    for index, flat in enumerate(flat_units, start=1):
        unit_id = _reference_unit_id(episode, index)
        validate_unit_text(f"unit {unit_id}", flat["text"], project, max_refs=max_refs)
        units.append(
            {
                "unit_id": unit_id,
                "text": flat["text"],
                "duration_seconds": flat["duration_seconds"],
                "source_text": flat["source_text"],
            }
        )
    return units


#: 落盘照常、只随产物呈现的容忍 warning（声音降级）。其余 warning 键（未登记 mention /
#: 说话人、语法误用）在机器产物这条路上是阻断违约，不走容忍分支。
_TOLERATED_VOICE_WARNINGS = (
    WARN_SPEAKER_WITHOUT_AUDIO,
    WARN_REFERENCE_AUDIO_OVERFLOW,
    WARN_SILENT_MODEL,
    WARN_SILENT_EPISODE,
)


def _reference_voice_warning_lines(
    unit_texts: list[str], project: dict[str, Any], voice: VoiceRenderSettings
) -> list[str]:
    """逐 unit 派生声音绑定，取容忍类 warning 的渲染文本（跨 unit 去重、保持首现顺序）。

    逐 unit 而非把全集正文拼起来判：unit 就是一次生成调用，参考音频段数上限按调用计——拼起来
    判会把「每个 unit 各两个说话人」误报成超限。与编辑器预览、执行期渲染共用
    ``derive_voice_bindings``，三处对同一份文稿给出的声音结论因此不会分叉。

    ``requires_reference_image`` 在本处一律关掉：该位的判定要配 ``speakers_with_reference_image``
    才有意义，而拆分阶段的 unit 尚未确定随请求发出的参考图。开着而不给图集合，等于把每个说话人
    都判成「无画面可挂」，那条 warning 又不在容忍列表内会被丢弃——结果是「未设参考音频」「超出
    段数上限」这些该让 Agent 看见的提示反被吞掉。
    """
    characters = project.get(BUCKET_KEY["character"]) or {}
    settings = replace(voice, requires_reference_image=False)
    seen: set[tuple[str, str]] = set()
    lines: list[str] = []
    for text in unit_texts:
        utterances, _syntax_warnings = derive_utterances(text)
        bindings = derive_voice_bindings(utterances, characters, settings)
        for warning in bindings.warnings:
            key = str(warning["key"])
            if key not in _TOLERATED_VOICE_WARNINGS:
                continue
            rendered = translate(key, **warning["params"])
            if (key, rendered) in seen:
                continue
            seen.add((key, rendered))
            lines.append(rendered)
    return lines


def _reference_scene_warning_lines(unit_texts: list[str], project: dict[str, Any], *, episode: int) -> list[str]:
    """逐 unit 取「未引用任何场景资产」的提示，带 unit 定位。

    与声音降级同属容忍类：地点由模型自由决定不是格式错误，正文照常落盘；但室内外交替的相邻
    unit 会各自发挥，不在产出当时说，Agent 与用户都要等看到成片才发现。

    判据取自 ``unit_lacks_scene_reference``，与编辑器预览的同名 warning 共用一个出口——同一份
    正文在回执与面板上必须给出同一个结论。

    不跨 unit 去重：每个未引用场景的 unit 都要各自被指名，合并成一条 Agent 无从定位要改哪几个。
    """
    message = translate(WARN_UNIT_WITHOUT_SCENE)
    return [
        f"{_reference_unit_label(episode, index)}：{message}"
        for index, text in enumerate(unit_texts, start=1)
        if unit_lacks_scene_reference(text, project)
    ]


def _reference_result_text(script_plan_path: Path, units: list[dict], warning_lines: list[str], *, action: str) -> str:
    """晋升 / 拆分成功后回给 Agent 的摘要：落盘统计 + 容忍类 warning。

    ``action`` 点明这份正式 script_plan 是重新拆分还是草稿晋升来的：两条路都写同一个文件，摘要不分
    的话，Agent 修完草稿会收到一句「拆分已保存」，读起来像它的修改被一次重抽覆盖了。

    warning 不阻断落盘，但必须随产物呈现——「角色没配参考音频」「本单元没引用场景」这类降级只在
    生成后才听得出来、看得出来，不在产出当时说，Agent 与用户都不会知道声音一致性或画面地点已经
    打了折。
    """
    total_seconds = sum(int(u.get("duration_seconds") or 0) for u in units)
    max_unit_refs = max(len(extract_mentions(str(u.get("text") or ""))) for u in units)
    text = (
        f"✅ 视频单元{action}（结构化 script_plan）已保存: {script_plan_path}\n"
        f"📊 生成统计: {len(units)} 个 unit，总时长 {total_seconds} 秒；"
        f"单 unit `@` 提及最多 {max_unit_refs} 个"
    )
    if warning_lines:
        text += "\n⚠️ 降级提示（不阻断，产物已落盘）:\n" + "\n".join(f"- {line}" for line in warning_lines)
    return text


def _narration_script_plan_path(project_path: Path, episode: int) -> Path:
    """该集正式 narration script_plan 的路径（``drafts/episode_N/script_plan_segments.json``）。"""
    return episode_drafts_dir(project_path, episode) / SCRIPT_PLAN_FILENAMES["narration"]


def _narration_segment_label(segment: dict[str, Any], index: int) -> str:
    """违约条目的定位前缀。

    ``segment_id`` 缺失或空白时退回数组下标：那本身就是一条违约，但报告仍要能指到具体哪一项，
    否则 Agent 拿到的是一条无处下手的消息。
    """
    sid = segment.get("segment_id")
    return f"segment {sid}" if isinstance(sid, str) and sid.strip() else f"segments[{index}]"


def _normalize_for_coverage(text: str) -> str:
    """Unicode NFC 归一后把连续空白折叠为单个空格，只消除编码与空白差异，不删除空白本身。

    NFC 与 ``lib.episode_ledger.normalize_source_text`` 定义的源文坐标系一致，也与参考生视频
    ``_normalize_for_anchor`` 同口径：带组合附加符的语种（如 vi）源文可能以 NFD 落盘、模型
    回写 NFC，不归一会把纯编码形式差异判成删字改字，而覆盖违约会落成草稿、堵住内容确认
    确认与 prompt_authoring 生成。
    """
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def _coverage_source_scope(source: str | None) -> str:
    """覆盖判定所依据的源文范围的人话描述，供违约消息指名。

    覆盖判定是「分镜拼接 == 整份源文」的全等式，判定结果因此既取决于分镜正文、也取决于源文
    范围本身：范围取宽了（如按整个 ``source/`` 判一集的分镜表），分镜一个字没改也判不过。不把
    范围写进消息，Agent 只会反复去改分镜正文，而问题不在那里。
    """
    return f"源文件 {source}" if source else "整个 source/ 目录（未指定单个源文件）"


def _covers_source_verbatim(parts: list[str], source: str) -> bool:
    """各分镜是否按序、逐字、完整覆盖 *source*；分镜交界处允许至多一个空格。

    分镜两端的空白已由 :func:`_normalize_for_coverage` 剥掉，故交界处的一个空格只可能是分隔符，
    贪心跳过即可、无须回溯：分镜自身不可能以空格开头去争这个字符。

    不用「逐段 ``re.escape`` 后以 ``" ?"`` 拼成一条正则」：那样 pattern 长度与整篇源文同阶（可达
    数百 KB），而本判定在拆分、晋升与内容确认的读时重算三处各跑一次，内容确认那次还在请求
    协程里——每次都按源文规模编译一条正则，代价压在事件循环上。游标扫描是同一判定的线性写法。
    """
    cursor = 0
    for index, part in enumerate(parts):
        if index and source.startswith(" ", cursor):
            cursor += 1
        if not source.startswith(part, cursor):
            return False
        cursor += len(part)
    return cursor == len(source)


def _collect_narration_violations(
    segments: list[dict[str, Any]],
    *,
    episode: int,
    supported_durations: list[int],
    characters: dict[str, Any],
    scenes: dict[str, Any],
    props: dict[str, Any],
    novel_text: str,
    source_scope: str,
) -> list[DraftViolation]:
    """逐分镜收齐 narration script_plan 产出的全部违约（不在首个违约处中断）。

    schema（``NarrationScriptPlanDraft``）已卡死字段与外层形状；此处补依赖运行时能力值 / 项目登记表 /
    源文的约束——segment_id 全集唯一、novel_text 非空白、时长落在当前档位内、资产名已登记、
    各分镜正文按序逐字完整覆盖源文。收齐而非首个即抛：报告要一次列全所有坏分镜，否则 agent
    每修一处就要再跑一轮才知道下一处。

    抛的是内容违约而非 ``ValueError``：这些都是 Agent 改一改草稿就能修好的，走草稿的修复
    闭环，不该退回丢弃重抽。

    ``source_scope`` 是 ``novel_text`` 那份文本的来源描述（见 :func:`_coverage_source_scope`），
    只落进覆盖违约的消息里：覆盖判定同时取决于源文范围，范围本身不写出来就没法从报告里判断
    该改分镜还是该改范围。
    """
    violations: list[DraftViolation] = []

    expected_id = re.compile(rf"E{episode}S\d{{2}}")
    for index, segment in enumerate(segments):
        segment_id = segment.get("segment_id")
        if not isinstance(segment_id, str) or expected_id.fullmatch(segment_id) is None:
            label = _narration_segment_label(segment, index)
            violations.append(
                DraftViolation(
                    f"{label} 的 segment_id 必须为 E{episode}S## 格式且集号匹配",
                    code="invalid_segment_id",
                    label=label,
                )
            )

    dupes = sorted(str(sid) for sid, count in Counter(s.get("segment_id") for s in segments).items() if count > 1)
    if dupes:
        # 集级违约，无单分镜归属：呈现层落聚合区。
        violations.append(
            DraftViolation(
                f"segment_id 重复: {dupes}；每个分镜的 id 须全集唯一（prompt_authoring 视觉层按 id 与分镜对齐）",
                code="duplicate_segment_id",
            )
        )

    allowed = {int(d) for d in supported_durations}
    # 资产表的 key 先归一到比对坐标系（与 rv 侧 ``validate_unit_text`` 同一处理）：``project.json``
    # 里的名字与模型写回的名字可能是同一名称的不同 Unicode 形式，两侧不同形会把一个已登记的资产
    # 判成未登记。归一在循环外做一次，逐分镜只查表。
    registered = {
        field: normalize_asset_bucket(bucket)
        for field, bucket in (("characters_in_segment", characters), ("scenes", scenes), ("props", props))
    }
    for index, segment in enumerate(segments):
        label = _narration_segment_label(segment, index)

        # 静态 ``NarrationScriptPlanSegment.novel_text`` 的 ``min_length=1`` 只校验原始字符串长度，纯
        # 空白（如单个空格）能满足该约束却不携带任何旁白内容；此类分镜在覆盖校验中经
        # ``_normalize_for_coverage`` 折叠为空字符串后不消耗任何字符，覆盖校验同样拦不住，会
        # 落成「有时长但无旁白」的哑分镜。
        if not str(segment.get("novel_text") or "").strip():
            violations.append(
                DraftViolation(
                    f"{label} 的 novel_text 为空白；每个分镜必须携带逐字取自原文的旁白正文",
                    code="blank_novel_text",
                    label=label,
                )
            )

        # 静态 ``NarrationScriptPlanSegment.duration_seconds`` 是 ``ge=1, le=60`` 的开区间（复用既有分镜
        # schema，不在 schema 层枚举硬约束），故超出当前档位的时长能过 schema 校验；此处按现值
        # 档位补成员校验，与 ``ScriptGenerator._load_narration_script_plan`` 同口径——只有经此校验的
        # 内容才写盘成为 script_plan 真值源，杜绝把非法时长拖到 prompt_authoring / 最终 save_script 才暴露。
        duration = segment.get("duration_seconds")
        if not isinstance(duration, int) or duration not in allowed:
            violations.append(
                DraftViolation(
                    f"{label} 的时长 {duration} 不在模型档位 {sorted(allowed)} 内；请改取该档位内的时长",
                    code="duration_off_tier",
                    label=label,
                )
            )

        # 与 rv 侧 ``validate_unit_text`` 对 ``@[名称]`` 的登记校验同口径：只信登记过的资产名，
        # 不允许模型发明或拼错的名称被当真值写盘、被 prompt_authoring 视觉层只读消费。报告里回显模型写的
        # 原名而非归一形式——它要在自己的草稿里找到这个字符串才改得动。
        for field, bucket in registered.items():
            names = segment.get(field) or []
            bad = sorted({str(name) for name in names if asset_name_comparison_key(str(name)) not in bucket})
            if bad:
                violations.append(
                    DraftViolation(
                        f"{label} 的 {field} 引用了未登记的资产名: {bad}；"
                        "资产名必须逐字取自 project.json 三张表，或先在 project.json 登记该资产",
                        code="unregistered_asset",
                        label=label,
                    )
                )

    # 分镜边界处的空白存在与否天然歧义——模型选择的切分点可能落在源文空格上（该空格被切分本身
    # 「消耗」，不落在任一分镜自身文本里），也可能落在无空格的 CJK / 标点邻接处，两者从拼接后的
    # 字符串本身无法可靠区分。因此仅在分镜交界处允许可选的单个空格；分镜自身文本内部与源文其余
    # 部分一律要求折叠后逐字相等，不能让边界宽容掩盖分镜内部真实的删减、改写或词间空格丢失。
    # 判定是全集级的（拼接 vs 整篇源文），没有单分镜归属，故不带 label。
    parts = [_normalize_for_coverage(str(s.get("novel_text") or "")) for s in segments]
    if not _covers_source_verbatim(parts, _normalize_for_coverage(novel_text)):
        violations.append(
            DraftViolation(
                "各分镜的 novel_text 未按序、逐字、完整覆盖小说原文（存在删减、改写或重排）；"
                "分镜正文须原样复制原文、不要转述，且按序拼接后即是整篇原文。"
                f"本次判定依据的源文范围：{source_scope}",
                code="novel_text_coverage",
            )
        )
    return violations


async def generate_reference_script_plan(
    request: TextGenerationRequest,
    *,
    project_name: str,
    projects: ProjectManager,
    config_resolver: ConfigResolver,
    before_commit: Callable[[], None] | None = None,
) -> TextGenerationResult:
    episode = request.episode
    instructions = _instructions(request.instructions)
    project_path = projects.get_project_path(project_name)
    project = await asyncio.to_thread(projects.load_project_readonly, project_name)

    try:
        novel_text, prompt_inputs, script_plan_basis = await asyncio.to_thread(
            _load_script_plan_source_with_basis,
            project_path,
            request.source,
            project,
            episode,
            "reference_video",
        )
    except ValueError as exc:
        raise TextGenerationError(f"❌ {exc}") from exc

    try:
        characters = cast(dict[str, Any], prompt_inputs["characters"])
        scenes = cast(dict[str, Any], prompt_inputs["scenes"])
        props = cast(dict[str, Any], prompt_inputs["props"])
        split_caps = await _fetch_reference_caps_with_fallback(
            project,
            episode,
            config_resolver=config_resolver,
        )
        prompt = build_reference_units_split_prompt(
            novel_text=novel_text,
            project_overview=cast(dict[str, Any], prompt_inputs["project_overview"]),
            characters=characters,
            scenes=scenes,
            props=props,
            supported_durations=split_caps.durations,
            reference_supported_durations=split_caps.reference_durations,
            text_supported_durations=split_caps.text_durations,
            max_duration=split_caps.max_duration,
            max_reference_images=split_caps.max_refs,
            default_duration=split_caps.default_duration,
            episode=episode,
            target_language=cast(str, prompt_inputs["target_language"]),
            source_language=cast(str | None, prompt_inputs["source_language"]),
            speech_rate_override=cast(float | None, prompt_inputs["speech_rate_override"]),
            episode_outline=cast(dict[str, Any] | None, prompt_inputs["episode_outline"]),
            next_episode_outline=cast(dict[str, Any] | None, prompt_inputs["next_episode_outline"]),
        )
        prompt = append_user_instructions(prompt, instructions)

        if request.dry_run:
            return TextGenerationResult(
                f"DRY RUN — 以下是将发送给文本模型的 Prompt:\n\n{prompt}\n\nPrompt 长度: {len(prompt)} 字符"
            )

        draft_path = quarantine_path(project_path, episode, QUARANTINE_KIND_SCRIPT_PLAN)
        formal_script_plan_path = script_review.official_reference_script_plan_path(project_path, episode)
        async with ProjectManager(str(project_path.parent)).async_file_lock(draft_path):
            draft_baseline, formal_baseline = await asyncio.to_thread(
                _generation_baselines,
                draft_path,
                formal_script_plan_path,
            )
        schema = build_reference_units_script_plan_model(split_caps.durations)
        generator = await TextGenerator.create(TextTaskType.SCRIPT, project_name=project_name)
        result = await generator.generate(
            BackendTextGenerationRequest(
                prompt=prompt,
                response_schema=schema,
                max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
            ),
            project_name=project_name,
        )
        flat = _parse_script_plan_json(result.text, schema, label="script_plan 拆分内容", top_shape="{units}")
        flat_units = flat.get("units")
        if not isinstance(flat_units, list) or not flat_units:
            raise ValueError("script_plan 拆分内容结构异常：units 必须是非空的 unit 对象数组")

        violations = _collect_reference_flat_violations(
            flat_units,
            project,
            episode=episode,
            novel_text=novel_text,
            caps=split_caps,
            source_language=project.get("source_language"),
        )
        if violations:
            async with ProjectManager(str(project_path.parent)).async_file_lock(draft_path):
                _assert_draft_revision(draft_path, draft_baseline)
                report = await _run_compensable_quarantine(
                    project_path,
                    episode,
                    QUARANTINE_KIND_SCRIPT_PLAN,
                    {"units": flat_units},
                    violations,
                    request.source,
                    formal_baseline,
                )
            raise TextGenerationError(report)

        raw_units = _build_reference_units_from_flat(
            flat_units,
            project,
            episode=episode,
            max_refs=split_caps.max_refs,
        )
        async with ProjectManager(str(project_path.parent)).async_file_lock(draft_path):
            _assert_draft_revision(draft_path, draft_baseline)
            try:
                cancellation_receipt = await _run_compensable_script_plan_commit(
                    _commit_generated_reference_script_plan,
                    project_path,
                    episode,
                    {"units": raw_units},
                    formal_baseline,
                    script_plan_basis,
                    before_commit,
                )
            except script_review.ScriptPlanWriteConflict as exc:
                raise TextGenerationError(
                    _quarantine_formal_generation_conflict(
                        project_path,
                        episode,
                        QUARANTINE_KIND_SCRIPT_PLAN,
                        {"units": flat_units},
                        request.source,
                        formal_baseline,
                        exc.actual,
                    )
                ) from exc
        unit_texts = [flat_unit["text"] for flat_unit in flat_units]
        warning_lines = _reference_voice_warning_lines(
            unit_texts, project, split_caps.voice
        ) + _reference_scene_warning_lines(unit_texts, project, episode=episode)
        return CompensableTextGenerationResult(
            _reference_result_text(
                script_review.official_reference_script_plan_path(project_path, episode),
                raw_units,
                warning_lines,
                action="拆分",
            ),
            cancellation_receipt.compensate_cancelled,
        )
    except TextGenerationError:
        raise
    except Exception as exc:
        raise TextGenerationError(f"generate_script_plan 失败: {exc}") from exc


async def generate_narration_script_plan(
    request: TextGenerationRequest,
    *,
    project_name: str,
    projects: ProjectManager,
    config_resolver: ConfigResolver,
) -> TextGenerationResult:
    episode = request.episode
    instructions = _instructions(request.instructions)
    project_path = projects.get_project_path(project_name)
    project = await asyncio.to_thread(projects.load_project_readonly, project_name)

    try:
        novel_text, prompt_inputs, script_plan_basis = await asyncio.to_thread(
            _load_script_plan_source_with_basis,
            project_path,
            request.source,
            project,
            episode,
            "narration",
        )
    except ValueError as exc:
        raise TextGenerationError(f"❌ {exc}") from exc

    try:
        characters = cast(dict[str, Any], prompt_inputs["characters"])
        scenes = cast(dict[str, Any], prompt_inputs["scenes"])
        props = cast(dict[str, Any], prompt_inputs["props"])
        default_duration, supported_durations = await _fetch_caps_with_fallback(
            project,
            episode,
            config_resolver=config_resolver,
        )
        prompt = build_narration_split_prompt(
            novel_text=novel_text,
            project_overview=cast(dict[str, Any], prompt_inputs["project_overview"]),
            characters=characters,
            scenes=scenes,
            props=props,
            default_duration=default_duration,
            supported_durations=supported_durations,
            episode=episode,
            target_language=cast(str, prompt_inputs["target_language"]),
        )
        prompt = append_user_instructions(prompt, instructions)

        if request.dry_run:
            return TextGenerationResult(
                f"DRY RUN — 以下是将发送给文本模型的 Prompt:\n\n{prompt}\n\nPrompt 长度: {len(prompt)} 字符"
            )

        draft_path = quarantine_path(project_path, episode, QUARANTINE_KIND_NARRATION_SCRIPT_PLAN)
        script_plan_path = _narration_script_plan_path(project_path, episode)
        async with ProjectManager(str(project_path.parent)).async_file_lock(draft_path):
            draft_baseline, formal_baseline = await asyncio.to_thread(
                _generation_baselines,
                draft_path,
                script_plan_path,
            )
        generator = await TextGenerator.create(TextTaskType.SCRIPT, project_name=project_name)
        result = await generator.generate(
            BackendTextGenerationRequest(
                prompt=prompt,
                response_schema=NarrationScriptPlanDraft,
                max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
            ),
            project_name=project_name,
        )
        content = _parse_script_plan_json(
            result.text,
            NarrationScriptPlanDraft,
            label="script_plan 拆分内容",
            top_shape="{segments}",
        )
        raw_segments = content.get("segments")
        if not isinstance(raw_segments, list) or not raw_segments:
            raise ValueError("script_plan 拆分内容结构异常：segments 必须是非空的分镜对象数组")

        violations = _collect_narration_violations(
            raw_segments,
            episode=episode,
            supported_durations=supported_durations,
            characters=characters,
            scenes=scenes,
            props=props,
            novel_text=novel_text,
            source_scope=_coverage_source_scope(request.source),
        )
        if violations:
            async with ProjectManager(str(project_path.parent)).async_file_lock(draft_path):
                _assert_draft_revision(draft_path, draft_baseline)
                report = await _run_compensable_quarantine(
                    project_path,
                    episode,
                    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
                    content,
                    violations,
                    request.source,
                    formal_baseline,
                )
            raise TextGenerationError(report)

        async with ProjectManager(str(project_path.parent)).async_file_lock(draft_path):
            _assert_draft_revision(draft_path, draft_baseline)
            try:
                cancellation_receipt = await _run_compensable_script_plan_commit(
                    _commit_single_script_plan,
                    project_path,
                    episode,
                    script_plan_path,
                    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
                    content,
                    formal_baseline,
                    script_plan_basis,
                )
            except script_review.ScriptPlanWriteConflict as exc:
                raise TextGenerationError(
                    _quarantine_formal_generation_conflict(
                        project_path,
                        episode,
                        QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
                        content,
                        request.source,
                        formal_baseline,
                        exc.actual,
                    )
                ) from exc

        total_chars = sum(len(str(segment.get("novel_text") or "")) for segment in raw_segments)
        total_seconds = sum(int(segment.get("duration_seconds") or 0) for segment in raw_segments)
        break_count = sum(1 for segment in raw_segments if segment.get("segment_break"))
        return CompensableTextGenerationResult(
            f"✅ 旁白/解说分镜拆分（结构化 script_plan）已保存: {script_plan_path}\n"
            f"📊 生成统计: {len(raw_segments)} 个分镜 / {total_chars} 字，"
            f"预计总时长 {total_seconds} 秒；segment_break 标记 {break_count} 个",
            cancellation_receipt.compensate_cancelled,
        )
    except TextGenerationError:
        raise
    except Exception as exc:
        raise TextGenerationError(f"generate_script_plan 失败: {exc}") from exc
