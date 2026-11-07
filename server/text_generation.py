"""Host-independent implementations for ArcReel text-generation tools."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Any, Literal, NamedTuple, cast

from pydantic import BaseModel, ValidationError

from lib.artifacts.artifact_manifest import (
    ArtifactBasis,
)
from lib.artifacts.artifact_provenance import ScriptPlanPromptVariant, build_script_plan_request
from lib.artifacts.formal_write import formal_write_transaction
from lib.backends.providers import CallPurpose
from lib.backends.text_backends.base import DEFAULT_MAX_OUTPUT_TOKENS, TextOutputTruncatedError, TextTaskType
from lib.backends.text_backends.base import TextGenerationRequest as BackendTextGenerationRequest
from lib.backends.text_generator import TextGenerator
from lib.catalog.errors import ModelNotInCatalogError
from lib.config.resolver import ConfigResolver
from lib.episode.episode_ids import describe_episode_for_agent
from lib.episode.episode_paths import (
    SCRIPT_PLAN_FILENAMES,
    episode_drafts_dir,
    episode_source_relpath,
)
from lib.episode.episode_sources import episode_entry
from lib.episode.source_kinds import DEFAULT_SOURCE_KIND
from lib.generation.video_request_facts import (
    VideoRequestFacts,
    VideoRequestFactsError,
    VideoRequestFactsFailure,
    planning_durations,
    require_video_request_facts,
)
from lib.i18n import _ as translate
from lib.infra.async_thread import run_sync_transaction
from lib.infra.content_digest import prefixed_sha256_file
from lib.infra.path_safety import PathTraversalError, safe_join
from lib.infra.schema_guards import is_int, is_str
from lib.infra.text_utils import strip_json_code_fences
from lib.project.asset_types import ASSET_SPECS, BUCKET_KEY, asset_name_comparison_key
from lib.project.project_manager import ProjectManager, is_reference_video_project
from lib.prompts.prompt_builders_reference import build_reference_units_split_prompt
from lib.prompts.prompt_builders_script import build_narration_split_prompt, build_normalize_prompt
from lib.references.reference_catalog import ReferenceCatalog, build_reference_catalog
from lib.script import script_review
from lib.script.draft_quarantine import (
    PROMOTE_TOOL_NAME,
    QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
    QUARANTINE_KIND_PROMPT_AUTHORING,
    QUARANTINE_KIND_SCRIPT_PLAN,
    clear_quarantine,
    quarantine_and_report,
    quarantine_path,
    read_quarantine,
)
from lib.script.draft_violation import DraftViolation, collect_violations
from lib.script.plan_new_assets import (
    NEW_ASSETS_FIELD,
    dedupe_new_assets,
    new_asset_violations,
    planning_project,
    with_new_assets,
)
from lib.script.prompt_authoring_scope import PromptOverwriteRequired, prompt_overwrite_with_text
from lib.script.reference_video.draft_validation import (
    validate_dialogue_load,
    validate_source_text_anchor,
    validate_unit_text,
)
from lib.script.reference_video.script_preview import (
    WARN_REFERENCE_AUDIO_OVERFLOW,
    WARN_SILENT_EPISODE,
    WARN_SILENT_MODEL,
    WARN_SPEAKER_WITHOUT_AUDIO,
    WARN_UNIT_WITHOUT_SCENE,
    derive_utterances,
    derive_voice_bindings,
    unit_lacks_scene_reference,
)
from lib.script.reference_video.text_parser import extract_mentions
from lib.script.reference_video.unit_capabilities import hydrate_reference_units
from lib.script.reference_video.voice_settings import VoiceRenderSettings
from lib.script.script_generator import (
    AdScriptOverwriteRequired,
    AdScriptRejected,
    PromptAuthoringTargetError,
    PromptAuthoringUnitRejected,
    ScriptGenerator,
)
from lib.script.script_models import (
    NarrationScriptPlanDraft,
    build_drama_normalized_script_model,
    build_reference_units_script_plan_model,
)
from lib.script.script_skeleton import resolve_kind_items
from lib.script.storyboard_mentions import render_storyboard_mention_warnings, storyboard_mention_warnings
from lib.speech.speech_composition import admit_script_unit
from lib.speech.speech_rate import project_speech_rate_override
from lib.workflow.operation_admission import (
    AdmissionReason,
    OperationAdmission,
    ad_inputs_present,
    admit_ad_script,
    admit_author_prompts,
    admit_script_plan,
    episode_source_present,
)
from lib.workflow.workflow_state import WorkflowStateService
from server.services.tasks.video_caps import reference_request_facts_lookup, storyboard_request_facts

logger = logging.getLogger(__name__)

# Maximum Agent-added instruction length: excess tokens can become uncontrolled and dilute source processing; reject excess as parameter errors.
# Generous for additional instructions, blocking only pathological inputs; shared by text generation and episode planning.
MAX_INSTRUCTIONS_LEN = 4000

#: Request-model rejection explanation for removed prompt-authoring ``scope`` arguments.
SCOPE_REMOVED_MESSAGE = (
    "scope 参数已取消：generate_episode_script 默认只编写正式脚本中全部待编写的条目；"
    "要覆盖已有提示词的条目，请用 entry_ids 点名这些条目并传 rewrite=true；要整集重做，请重跑脚本规划并重新完成内容确认。"
)


@dataclass(frozen=True, slots=True)
class TextGenerationRequest:
    episode: int
    source: str | None = None
    instructions: str | None = None
    dry_run: bool = False
    #: Prompt-authoring scope; empty means all pending items.
    entry_ids: tuple[str, ...] = ()
    #: Explicitly rewrite every visual layer in scoped items; False fills only missing visual fields.
    rewrite: bool = False
    #: User-approved formal-script fingerprint from ``prompt_overwrite.revision``; whole-script redo uses ``script_overwrite.revision``.
    overwrite_revision: str | None = None
    #: Ad/short-film only: regenerate the entire existing formal script.
    regenerate: bool = False

    def __post_init__(self) -> None:
        if not is_int(self.episode, minimum=1):
            raise ValueError("episode must be a positive integer")
        # JSON-roundtripped queue payload entry_ids are lists: normalize to tuples so tool construction
        # and payload restoration yield the same value without container-type divergence in task-fact comparisons.
        entry_ids = tuple(self.entry_ids)
        if any(not is_str(entry_id) or not entry_id for entry_id in entry_ids):
            raise ValueError("entry_ids must be non-empty strings")
        object.__setattr__(self, "entry_ids", entry_ids)

    def to_payload(self) -> dict[str, object]:
        """Queue-task payload using JSON-native types only.

        ``asdict`` leaves ``entry_ids`` as tuple, whereas database roundtrips return lists. Equality-based
        comparison of requests against running tasks would treat these containers as different, turning
        idempotent submission into task conflict."""
        return {**asdict(self), "entry_ids": list(self.entry_ids)}


@dataclass(frozen=True, slots=True)
class TextGenerationResult:
    message: str
    #: Locale-neutral ``{"key", "params"}`` notices (e.g. visual ``@[名称]`` without reference-image binding),
    #: matching task ``result.warnings`` shape for read-side localization.
    warnings: list[dict[str, Any]] = dataclass_field(default_factory=list)
    #: New assets/derivatives registered by whole ad/short-film generation (``{"type", "name"}``), returned for Web listing.
    new_assets: list[dict[str, str]] = dataclass_field(default_factory=list)
    #: No pending prompt-authoring items and no text-model call; Web instructs selecting overwrite before rewriting.
    no_pending_authoring: bool = False


class TextGenerationError(Exception):
    """Expected refusal from a text-generation handler."""


class UnexpectedTextGenerationError(TextGenerationError):
    """Unexpected generation failure, with the original in ``__cause__`` so callers distinguish provider refusal from retryable task failure."""


class PromptAuthoringInputError(TextGenerationError):
    """Prompt-authoring input failed pre-model preflight and requires changes; carries task-failure problem codes/parameters."""

    def __init__(self, message: str, *, code: str, params: dict[str, Any]) -> None:
        super().__init__(message)
        self.code = code
        self.params = params


class PromptOverwriteRequiredError(TextGenerationError):
    """Explicit prompt rewriting replaces existing visual layers without caller approval; carries the loss manifest (including ``text``)."""

    def __init__(self, message: str, overwrite: dict[str, Any]) -> None:
        super().__init__(message)
        self.overwrite = overwrite


def _prompt_overwrite_error(episode_label: str, exc: PromptOverwriteRequired) -> PromptOverwriteRequiredError:
    overwrite = prompt_overwrite_with_text(exc.overwrite.to_dict(), translate)
    return PromptOverwriteRequiredError(
        f"⚠️ {episode_label} 的显式重写需要用户确认覆盖，本次未调用文本模型、未写入。\n{overwrite['text']}\n"
        "须先把上面的丢失清单原文转述给用户，得到明确同意后，再以 "
        "overwrite_revision=params.prompt_overwrite.revision 重新调用；"
        "正式脚本在此期间又有变化时会按新清单再次拒绝。",
        overwrite,
    )


class AdScriptRejectedError(TextGenerationError):
    """Ad/short-film whole-script output violates constraints: task fails without changing formal scripts, assets, or drafts; carries individual violations."""

    def __init__(self, message: str, problems: Sequence[str]) -> None:
        super().__init__(message)
        self.problems = tuple(problems)


class VideoRequestFactsUnavailableError(TextGenerationError):
    """Video request facts cannot resolve (unsupported model capabilities, missing duration tiers, etc.), preventing text-task continuation; carries the failure object.

    ``failure`` codes, parameters, and repair guidance pass unchanged into tool problems/task failures;
    readers render reasons by problem code."""

    def __init__(self, failure: VideoRequestFactsFailure) -> None:
        super().__init__(video_facts_failure_text(failure))
        self.failure = failure


class ScriptOverwriteRequiredError(TextGenerationError):
    """Content confirmation would overwrite an existing episode script without caller approval; carries removed-item/artifact summaries."""

    def __init__(self, message: str, overwrite: dict[str, Any] | None) -> None:
        super().__init__(message)
        self.overwrite = overwrite


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


def _after_draft_revision_check[**P, T](
    draft_path: Path,
    expected: str | None,
    func: Callable[P, T],
    /,
    *args: P.args,
    **kwargs: P.kwargs,
) -> T:
    """Run draft-revision checks and subsequent writes in one thread call, without yielding the event loop between them."""
    _assert_draft_revision(draft_path, expected)
    return func(*args, **kwargs)


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
) -> None:
    if before_commit is not None:
        before_commit()
    draft_path = quarantine_path(project_path, episode, QUARANTINE_KIND_SCRIPT_PLAN)
    prompt_authoring_path = quarantine_path(project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING)
    formal_path = script_review.official_reference_script_plan_path(project_path, episode)
    pm = ProjectManager.for_project_dir(project_path)
    with (
        pm.file_lock(prompt_authoring_path),
        script_review.script_plan_write_lock(project_path, episode),
        formal_write_transaction(formal_path, prompt_authoring_path, draft_path),
    ):
        script_review.write_script_plan_locked(
            project_path,
            episode,
            content,
            expected_fingerprint=expected_fingerprint,
            basis=basis,
        )
        clear_quarantine(project_path, episode, QUARANTINE_KIND_SCRIPT_PLAN)


def plan_item_ids(path: Path) -> list[str]:
    """Ordered IDs from the on-disk plan, empty when missing/unreadable. Reference-video plans use the ``units`` array key."""
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(content, dict) and "units" in content:
            items, id_field = content["units"], "unit_id"
        else:
            items, id_field, _kind = resolve_kind_items(content)
    except (OSError, ValueError):
        return []
    if not isinstance(items, list):
        return []
    return [item[id_field] for item in items if isinstance(item, dict) and isinstance(item.get(id_field), str)]


async def reissue_plan_item_ids(
    projects: ProjectManager,
    project_name: str,
    episode: int,
    content: dict[str, Any],
    *,
    keep: Collection[str] = (),
) -> None:
    """Issue server-owned item IDs in place before plan persistence; IDs are allocated only, never reused.

    ``keep`` contains IDs previously issued for this plan (formal on-disk plan IDs during promotion).
    Retain nonduplicate IDs from that set so unchanged promotion stays byte-for-byte identical; replace
    all model/draft-invented IDs with newly allocated ones."""
    items, id_field, kind = resolve_kind_items(content)
    if not isinstance(items, list):
        return
    kept: set[str] = set()
    fresh: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = item.get(id_field)
        if isinstance(item_id, str) and item_id in keep and item_id not in kept:
            kept.add(item_id)
        else:
            fresh.append(item)
    if not fresh:
        return
    issued = await run_sync_transaction(projects.reserve_item_ids, project_name, episode, kind, len(fresh))
    for item, item_id in zip(fresh, issued, strict=True):
        item[id_field] = item_id


def commit_single_script_plan(
    project_path: Path,
    episode: int,
    script_plan_path: Path,
    kind: str,
    content: dict[str, Any],
    expected_fingerprint: Any,
    basis: ArtifactBasis | None,
) -> None:
    draft_path = quarantine_path(project_path, episode, kind)
    with (
        script_review.formal_script_plan_lock(project_path, episode, script_plan_path),
        formal_write_transaction(script_plan_path, draft_path),
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


def _quarantine_invalid_script_plan_generation(
    project_path: Path,
    episode: int,
    kind: str,
    content: dict[str, Any],
    violations: list[DraftViolation],
    source: str | None,
    base_fingerprint: str | None,
) -> str:
    with formal_write_transaction(quarantine_path(project_path, episode, kind)):
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


def _project_default_duration(project: dict[str, Any]) -> int | None:
    """Verbatim user-configured project seconds; invalid values count as unconfigured."""
    raw = project.get("default_duration")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return None


def video_facts_failure_text(failure: VideoRequestFactsFailure) -> str:
    """Agent explanation for unresolved video request facts: state cause/next steps by problem code, with code/parameter summaries.

    Codes and parameters are the failure contract, matching preflight and execution."""
    params: dict[str, Any] = failure.parameters()
    return f"❌ {translate(failure.code, **params)}（{failure.summary()}）"


async def fetch_storyboard_durations(
    project: dict[str, Any],
    *,
    config_resolver: ConfigResolver | None = None,
) -> tuple[int | None, list[int]]:
    """Storyboard-route plan ``(default_duration, supported_durations)`` from storyboard-bucket video request facts.

    ``supported_durations`` is narrowed by project-resolution duration coupling (borrow planning tiers for
    endpoint-fixed duration), sharing storyboard preflight/execution results. Full model declarations omit
    resolution↔duration coupling; unfiltered LLM options produce durations execution necessarily rejects.
    Unresolved facts (unconfigured models, missing/invalid tiers, empty narrowing) raise
    :class:`VideoRequestFactsError`, without any tier fallback.

    Treat off-tier ``default_duration`` as None (auto: model selects by pacing). Stored values are verbatim
    user preferences and may fall outside narrowed sets; ``build_normalize_prompt`` fails loudly for such
    defaults, so failing to clear them would turn saved out-of-range preferences into whole-tool hard failures."""
    facts = require_video_request_facts(await storyboard_request_facts(project, config_resolver))
    durations = planning_durations(facts)
    default = _project_default_duration(project)
    return (default if default in durations else None), durations


def _parse_script_plan_json(response_text: str, model: type[BaseModel], *, label: str, top_shape: str) -> dict:
    """Parse/validate structured script_plan responses as dict; fail loudly with ValueError, never returning unchecked content.

    ``model`` is the same dynamic schema built with ``supported_durations`` at the call site (response_schema),
    keeping local/schema validation aligned. Even if backends do not strictly enforce schemas, reject
    out-of-tier durations/missing fields here. Raise rather than retain raw JSON: otherwise unchecked
    content becomes formal script_plan files (downstream only guards outer shape), delaying invalid
    durations/missing-field errors until prompt_authoring or final save_script. Like narration's strict
    _load_narration_script_plan, only schema-validated content becomes persistent script_plan truth."""
    text = strip_json_code_fences(response_text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"{label} JSON 解析失败: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"{label}结构异常：顶层应为对象 {top_shape}")
    try:
        return model.model_validate(data).model_dump()
    except ValidationError as e:
        raise ValueError(f"{label}结构校验失败: {e}") from e


def _parse_normalized_content(response_text: str, model: type[BaseModel]) -> dict:
    """Parse drama script_plan (normalize) responses; see ``_parse_script_plan_json``."""
    return _parse_script_plan_json(response_text, model, label="script_plan 规范化内容", top_shape="{title, scenes}")


def load_novel_source(project_path: Path, source: str | None, *, episode: int) -> str:
    """Read script_plan source: specified source file, otherwise derived episode source; raise ValueError on errors.

    Shared by normalize/split plan tools: fail fast for escaping paths, missing files, and empty content;
    callers wrap messages in tool-error envelopes.

    Default (``source`` None) resolves to ``source/episode_N.txt``, matching ``WorkflowTarget.source``
    and ``ArtifactBasis`` fingerprints. ``source/`` contains originals and episode derivatives; concatenating
    the whole directory sends other episodes too, giving every episode the same source. Missing derivatives
    must not fall back to whole source, also outside this episode; instruct rebuilding with episode planning.

    Besides tool-produced ``source`` values, ``revalidate_reference_script_plan_draft`` passes draft ``meta.source``,
    an Agent-editable JSON field beyond runtime type annotations. Reject non-str/None with ValueError
    before ``safe_join``, which raises ``TypeError`` for nonpaths. All callers catch only ValueError; escaping
    TypeError becomes unhandled 500 during confirmation read-time recalculation instead of the intended
    unable-to-recalculate fallback state."""
    if source is not None and not is_str(source):
        raise ValueError(f"meta.source 类型非法，须为字符串或 null：{source!r}")
    if source == "":
        raise ValueError("源文件路径不能为空")
    resolved = episode_source_relpath(episode) if source is None else source
    try:
        # Default paths derive from episode numbers, not untrusted input, but still use safe_join: derivatives may symlink outside
        # projects, so both branches need one boundary guard without a default-path-only gap.
        source_path = safe_join(project_path, resolved)
    except PathTraversalError as exc:
        raise ValueError(f"路径超出项目目录: {resolved}") from exc
    if not source_path.is_file():
        # Existing nonfiles (e.g. directories) also mean missing source: read_text() raises IsADirectoryError for directories,
        # outside the ValueError universally caught by callers, causing unhandled 500 during content-confirmation
        # read-time recalculation.
        if source is not None:
            raise ValueError(f"未找到源文件: {source_path}")
        raise ValueError(
            f"未找到本集派生源文: {source_path}；请先运行分集规划工具（plan_episodes）按账本派生该文件，再重试"
        )
    novel_text = source_path.read_text(encoding="utf-8")
    if not novel_text.strip():
        raise ValueError("小说原文为空")
    return novel_text


def load_script_plan_source_with_basis(
    project_path: Path,
    source: str | None,
    project: dict[str, Any],
    episode: int,
    expected_variant: ScriptPlanPromptVariant,
) -> tuple[str, dict[str, object], ArtifactBasis]:
    """Freeze the exact source text and project semantics consumed by a script_plan request."""

    novel_text = load_novel_source(project_path, source, episode=episode)
    prompt_inputs, basis = build_script_plan_request(
        novel_text,
        episode=episode,
        project=project,
        expected_variant=expected_variant,
    )
    return novel_text, prompt_inputs, basis


def uses_reference_video_units(project_data: dict[str, Any]) -> bool:
    """Script-plan drafts apply only to non-ad reference-to-video projects."""
    return project_data.get("content_mode", "narration") != "ad" and is_reference_video_project(project_data)


class OperationNotAdmittedError(TextGenerationError):
    """AI-operation structural admission failed; ``reason`` matches the same operation's production-state ``operations`` reason code."""

    def __init__(self, operation: str, admission: OperationAdmission, message: str) -> None:
        super().__init__(message)
        self.operation = operation
        self.reason = admission.reason


def _refusal_text(reason: AdmissionReason | None, episode: int | None, detail: str = "") -> str:
    where = translate("operation_episode", episode=episode) if episode is not None else translate("operation_project")
    code = reason.value if reason is not None else "refused"
    text = f"⏸️ {translate(f'operation_{code}', where=where)}（{code}）。"
    return f"{text}{detail}" if detail else text


def require_admitted(
    operation: str, admission: OperationAdmission, *, episode: int | None = None, detail: str = ""
) -> None:
    """Reject failed admission using production-state reason codes, with wording only paraphrasing them; ``detail`` adds entry-specific handling guidance."""
    if not admission.admitted:
        raise OperationNotAdmittedError(operation, admission, _refusal_text(admission.reason, episode, detail))


def _prompt_authoring_draft_action(project_path: Path, episode: int) -> str:
    """Handling when prompt-authoring drafts exist, according to readability and presence of located violations."""
    path = quarantine_path(project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING)
    draft = read_quarantine(project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING)
    if draft is None:
        action = translate("operation_draft_repair_envelope", tool=PROMOTE_TOOL_NAME)
    elif draft.violations:
        action = translate("operation_draft_repair_violations", tool=PROMOTE_TOOL_NAME)
    else:
        action = translate("operation_draft_finish", tool=PROMOTE_TOOL_NAME)
    return translate("operation_draft_action", path=path.relative_to(project_path).as_posix(), action=action)


def _ad_overwrite_error(
    episode_label: str, overwrite: script_review.FormalScriptOverwrite
) -> ScriptOverwriteRequiredError:
    rendered = script_review.overwrite_with_text(overwrite.to_dict(), translate)
    assert rendered is not None
    return ScriptOverwriteRequiredError(
        f"⚠️ {episode_label} 的整份重做会替换已有的正式脚本，需要用户确认覆盖，本次未调用文本模型、未写入。\n"
        f"{rendered['text']}\n"
        "须先把上面的丢失清单原文转述给用户，得到明确同意后，再以 regenerate=true、"
        "overwrite_revision=params.script_overwrite.revision 重新调用；正式脚本在此期间又有变化时会按新清单再次拒绝。",
        rendered,
    )


def _regenerate_preflight(
    project_path: Path, episode: int, *, content_mode: str | None, formal_present: bool, overwrite_revision: str | None
) -> None:
    """Whole-script redo admission/overwrite confirmation: ad/short-film only; existing formal scripts require approval of their loss manifests."""
    if content_mode != "ad":
        raise TextGenerationError(
            "❌ regenerate 只适用于广告/短片；其他创作类型要整集重做，请重跑脚本规划并重新完成内容确认"
        )
    projects = ProjectManager.for_project_dir(project_path)
    project = projects.load_project(project_path.name)
    admission = admit_ad_script(
        content_mode, formal_script=formal_present, ad_inputs=ad_inputs_present(project), regenerate=True
    )
    require_admitted("generate_script", admission, episode=episode)
    overwrite = script_review.formal_script_overwrite(project_path, project, episode)
    if overwrite is not None and script_review.fingerprint_of_revision(overwrite_revision) != overwrite.fingerprint:
        raise _ad_overwrite_error(describe_episode_for_agent(project, episode), overwrite)


def prompt_authoring_preflight(
    project_path: Path,
    episode: int,
    *,
    entry_ids: Sequence[str] = (),
    rewrite: bool = False,
    overwrite_revision: str | None = None,
    regenerate: bool = False,
) -> None:
    """Reuse production-state formal-script, pending-item, and draft facts, then apply the same admission predicate."""
    projects = ProjectManager.for_project_dir(project_path)
    status = WorkflowStateService(projects).get_status(project_path.name, episode)
    if status.blockers:
        raise TextGenerationError("; ".join(blocker.reason for blocker in status.blockers))
    content = status.content
    content_mode = status.project.content_mode
    formal_present = (
        status.target is not None
        and status.target.episode == episode
        and content is not None
        and content.formal_script == "present"
    )
    if regenerate:
        _regenerate_preflight(
            project_path,
            episode,
            content_mode=content_mode,
            formal_present=formal_present,
            overwrite_revision=overwrite_revision,
        )
        return
    if content_mode == "ad" and not formal_present:
        operation = status.operations.get("generate_script")
        if operation is None:
            raise TextGenerationError(translate("operation_admission_unavailable", episode=episode))
        require_admitted("generate_script", OperationAdmission(**operation.model_dump()), episode=episode)
        return
    admission = admit_author_prompts(
        formal_script=formal_present,
        pending_ids=content.pending_authoring_ids if content is not None else [],
        draft_pending=content is not None
        and any(draft.kind == QUARANTINE_KIND_PROMPT_AUTHORING for draft in content.drafts),
        explicit_ids=entry_ids,
    )
    detail = ""
    if admission.reason is AdmissionReason.PROMPT_AUTHORING_DRAFT_PENDING:
        detail = _prompt_authoring_draft_action(project_path, episode)
    elif admission.reason is AdmissionReason.NO_PENDING_AUTHORING:
        redo = translate("operation_redo_ad" if content_mode == "ad" else "operation_redo_episode")
        detail = f"{translate('operation_rewrite_hint')}{redo}"
    require_admitted("author_prompts", admission, episode=episode, detail=detail)
    if rewrite:
        try:
            ScriptGenerator(project_path).prompt_overwrite(
                episode, entry_ids=entry_ids, rewrite=rewrite, overwrite_revision=overwrite_revision
            )
        except PromptAuthoringTargetError as exc:
            raise TextGenerationError(f"❌ 编写范围无效: {exc}") from exc
        except PromptOverwriteRequired as exc:
            raise _prompt_overwrite_error(
                describe_episode_for_agent(projects.load_project(project_path.name), episode), exc
            ) from exc


def script_plan_preflight(project_path: Path, project: Mapping[str, Any], episode: int, source: str | None) -> None:
    """AI script-planning admission: episode source exists by ledger-recorded provenance. Explicit source files become input, validated during generation."""
    has_source = source is not None or episode_source_present(project_path, episode, episode_entry(project, episode))
    require_admitted(
        "prepare_script_plan",
        admit_script_plan(project.get("content_mode", "narration"), episode_source=has_source),
        episode=episode,
        detail=translate("operation_episode_source_location", path=episode_source_relpath(episode)),
    )


def _load_project_context(projects: ProjectManager, project_name: str) -> tuple[Path, dict[str, Any]]:
    return projects.get_project_path(project_name), projects.load_project(project_name)


def _formal_script_exists(project_path: Path, project: dict[str, Any], episode: int) -> bool:
    return (project_path / "scripts" / script_review.formal_script_filename(project_path, project, episode)).exists()


async def generate_episode_script(
    request: TextGenerationRequest,
    *,
    project_name: str,
    projects: ProjectManager,
    config_resolver: ConfigResolver,
) -> TextGenerationResult:
    episode = request.episode
    instructions = _instructions(request.instructions)
    project_path = await asyncio.to_thread(projects.get_project_path, project_name)
    await asyncio.to_thread(
        prompt_authoring_preflight,
        project_path,
        episode,
        entry_ids=request.entry_ids,
        rewrite=request.rewrite,
        overwrite_revision=request.overwrite_revision,
        regenerate=request.regenerate,
    )

    try:
        if request.dry_run:
            generator = await asyncio.to_thread(
                ScriptGenerator,
                project_path,
                config_resolver=config_resolver,
            )
            prompt = await generator.build_prompt(
                episode, instructions=instructions, entry_ids=request.entry_ids, rewrite=request.rewrite
            )
            return TextGenerationResult(f"DRY RUN — 以下是将发送给文本模型的 Prompt:\n\n{prompt}")

        generator = await ScriptGenerator.create(
            project_path,
            config_resolver=config_resolver,
        )
        # With formal scripts, author entries only; generate entire ad scripts only when none exists.
        formal_existed = await asyncio.to_thread(_formal_script_exists, project_path, generator.project_json, episode)
        rewritten: list[str] = []
        skipped: list[str] = []
        registered: list[dict[str, str]] = []
        result_path = await generator.generate(
            episode=episode,
            instructions=instructions,
            entry_ids=request.entry_ids,
            rewrite=request.rewrite,
            overwrite_revision=request.overwrite_revision,
            rewritten_entry_ids=rewritten,
            skipped_entry_ids=skipped,
            regenerate=request.regenerate,
            registered_assets=registered,
        )
    except AdScriptRejected as exc:
        raise AdScriptRejectedError(
            f"❌ {await _describe(projects, project_name, episode)} 的 AI 生成脚本不合规，本次未写入正式脚本、未登记资产：\n"
            + "\n".join(f"   - {problem}" for problem in exc.problems)
            + "\n   可以带上针对这些问题的附加指令重新生成。",
            exc.problems,
        ) from exc
    except AdScriptOverwriteRequired as exc:
        raise _ad_overwrite_error(await _describe(projects, project_name, episode), exc.overwrite) from exc
    except PromptOverwriteRequired as exc:
        raise _prompt_overwrite_error(await _describe(projects, project_name, episode), exc) from exc
    except PromptAuthoringUnitRejected as exc:
        raise PromptAuthoringInputError(str(exc), code=exc.code, params=exc.params) from exc
    except PromptAuthoringTargetError as exc:
        # Requested entries absent from formal scripts are caller errors: refuse generation rather than internal_error,
        # which would tell Agents to retry parameters certain to fail unchanged.
        raise TextGenerationError(f"❌ 编写范围无效: {exc}") from exc
    except VideoRequestFactsError as exc:
        raise VideoRequestFactsUnavailableError(exc.failure) from exc
    except FileNotFoundError as exc:
        raise TextGenerationError(f"❌ 文件错误: {exc}") from exc
    skipped_note = (
        f"\n   视觉层已齐、补缺未改动的条目: {'、'.join(skipped)}（要覆盖请传 rewrite=true）" if skipped else ""
    )
    if not rewritten and formal_existed and not request.regenerate:
        redo = (
            "要整份重做请传 regenerate=true" if generator.content_mode == "ad" else "要整集重做请重跑脚本规划并重新确认"
        )
        return TextGenerationResult(
            f"✅ {describe_episode_for_agent(generator.project_json, episode)} 没有待编写的条目，"
            f"未调用文本模型，正式脚本未改动: {result_path.relative_to(project_path).as_posix()}{skipped_note}\n"
            f"   要覆盖已有视觉层请用 entry_ids 点名并传 rewrite=true；{redo}。",
            no_pending_authoring=True,
        )
    rewritten_note = "、".join(rewritten) if rewritten else "整份生成"
    summary = (
        f"✅ 剧本生成完成: {result_path.relative_to(project_path).as_posix()}\n"
        f"   本次编写条目: {rewritten_note}{skipped_note}"
    )
    if registered:
        labels = {asset_type: spec.label_zh for asset_type, spec in ASSET_SPECS.items()}
        summary += "\n   本次登记的新资产（待生成）: " + "、".join(
            f"{labels.get(asset['type'], asset['type'])}「{asset['name']}」" for asset in registered
        )
    warnings = await asyncio.to_thread(_rewritten_mention_warnings, projects, project_name, result_path, rewritten)
    summary += _unbound_mentions_note(warnings)
    return TextGenerationResult(summary, warnings, new_assets=registered)


def _rewritten_mention_warnings(
    projects: ProjectManager,
    project_name: str,
    result_path: Path,
    rewritten: Sequence[str],
) -> list[dict[str, Any]]:
    """Unbound-reference-image ``@[名称]`` mentions in rewritten visual descriptions; unreadable scripts must not block receipts."""
    if not rewritten:
        return []
    try:
        project = projects.load_project(project_name)
        script = projects.load_script_readonly(project_name, result_path.name)
    except (OSError, ValueError):
        # Scripts are persisted; receipts must not fail on reread errors (missing files, I/O, invalid JSON).
        return []
    return storyboard_mention_warnings(project, script, unit_ids=rewritten)


def _unbound_mentions_note(warnings: Sequence[Mapping[str, Any]]) -> str:
    return "".join(f"\n   ⚠️ {line}" for line in render_storyboard_mention_warnings(warnings, translate))


# ---------------------------------------------------------------------------
# confirm_script_review
# ---------------------------------------------------------------------------


async def _describe(projects: ProjectManager, project_name: str, episode: int) -> str:
    return describe_episode_for_agent(await asyncio.to_thread(projects.load_project, project_name), episode)


async def confirm_script_review(
    episode: int,
    *,
    overwrite_revision: str | None = None,
    project_name: str,
    projects: ProjectManager,
    config_resolver: ConfigResolver,
) -> TextGenerationResult:
    from server.services.project.script_review import ScriptReviewError, ScriptReviewService

    try:
        state = await ScriptReviewService(projects, config_resolver=config_resolver).confirm(
            project_name, episode, overwrite_revision=overwrite_revision
        )
    except ScriptReviewError as exc:
        if exc.code == "overwrite_required":
            overwrite = script_review.overwrite_with_text(exc.overwrite, translate)
            loss_text = overwrite["text"] if overwrite is not None else ""
            raise ScriptOverwriteRequiredError(
                f"⚠️ {await _describe(projects, project_name, episode)} 需要用户确认覆盖，本次未写入。\n{loss_text}\n"
                "须先把上面的丢失清单原文转述给用户，得到明确同意后，再以 "
                "overwrite_revision=params.script_overwrite.revision 重新确认；"
                "正式脚本在此期间又有变化时会按新清单再次拒绝。",
                overwrite,
            ) from exc
        if exc.problem is not None:
            raise VideoRequestFactsUnavailableError(exc.problem) from exc
        raise TextGenerationError(f"❌ 无法完成 script_plan 内容确认（{exc.code}）：{exc.message or exc.code}") from exc
    return TextGenerationResult(
        f"✅ {await _describe(projects, project_name, episode)} 的 script_plan 已确认并整份转为正式脚本，全部分镜待编写，"
        f"prompt_authoring 视觉生成已放行（status={state['status']}）"
    )


_REFERENCE_LIST_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "drama": (("characters_in_scene", "character"), ("scenes", "scene"), ("props", "prop")),
    "narration": (("characters_in_segment", "character"), ("scenes", "scene"), ("props", "prop")),
}


def _unregistered_reference_violations(
    route: Literal["drama", "narration"],
    item: Mapping[str, Any],
    catalog: ReferenceCatalog,
    *,
    label: str,
    item_index: int,
    item_id: str | None,
) -> list[DraftViolation]:
    """Asset names in one item's reference arrays absent from registration and current additions. Echo model-written originals, not normalized names."""
    violations: list[DraftViolation] = []
    for field, asset_type in _REFERENCE_LIST_FIELDS[route]:
        names = item.get(field) or []
        registered = catalog.reference_names(asset_type)
        bad = sorted({str(name) for name in names if asset_name_comparison_key(str(name)) not in registered})
        if bad:
            violations.append(
                DraftViolation(
                    f"{label} 的 {field} 引用了未登记的资产名: {bad}；"
                    "资产名必须逐字取自已登记资产，或把它列进 new_assets 并给出处理决定",
                    code="unregistered_asset",
                    label=label,
                    item_index=item_index,
                    item_id=item_id,
                )
            )
    return violations


def collect_drama_violations(
    scenes: list[Any], *, project: Mapping[str, Any], new_assets: object
) -> list[DraftViolation]:
    """Drama-plan asset violations: references must be registered/current additions, and handling decisions for additions must resolve."""
    catalog = build_reference_catalog(planning_project(project, new_assets))
    violations: list[DraftViolation] = []
    for index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            continue
        scene_id = scene.get("scene_id")
        scene_id = scene_id if isinstance(scene_id, str) else None
        violations.extend(
            _unregistered_reference_violations(
                "drama", scene, catalog, label=f"scene {scene_id or index + 1}", item_index=index, item_id=scene_id
            )
        )
    violations.extend(new_asset_violations(project, new_assets))
    return violations


# ---------------------------------------------------------------------------
# drama generate_script_plan variant
# ---------------------------------------------------------------------------


async def generate_drama_script_plan(
    request: TextGenerationRequest,
    *,
    project_name: str,
    projects: ProjectManager,
    config_resolver: ConfigResolver,
) -> TextGenerationResult:
    episode = request.episode
    instructions = _instructions(request.instructions)
    project_path, project = await asyncio.to_thread(_load_project_context, projects, project_name)
    try:
        novel_text, prompt_inputs, script_plan_basis = await asyncio.to_thread(
            load_script_plan_source_with_basis,
            project_path,
            request.source,
            project,
            episode,
            "drama",
        )
    except ValueError as exc:
        raise TextGenerationError(f"❌ {exc}") from exc

    try:
        default_duration, supported_durations = await fetch_storyboard_durations(
            project, config_resolver=config_resolver
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
            episode_target_duration=cast(int | None, prompt_inputs["episode_target_duration"]),
            instructions=instructions,
        )

        if request.dry_run:
            return TextGenerationResult(
                f"DRY RUN — 以下是将发送给文本模型的 Prompt:\n\n{prompt}\n\nPrompt 长度: {len(prompt)} 字符"
            )

        draft_path = quarantine_path(project_path, episode, QUARANTINE_KIND_DRAMA_SCRIPT_PLAN)
        script_plan_path = episode_drafts_dir(project_path, episode) / SCRIPT_PLAN_FILENAMES["drama"]
        # ProjectManager construction creates directories; offload to a thread.
        draft_locks = await asyncio.to_thread(ProjectManager.for_project_dir, project_path)
        async with draft_locks.async_file_lock(draft_path):
            draft_baseline, formal_baseline = await asyncio.to_thread(
                _generation_baselines,
                draft_path,
                script_plan_path,
            )
        schema = build_drama_normalized_script_model(supported_durations)
        generator = await TextGenerator.create(
            TextTaskType.SCRIPT, project_name=project_name, purpose=CallPurpose.SCRIPT_GENERATION
        )
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
        content = with_new_assets(content, content[NEW_ASSETS_FIELD])

        violations = collect_drama_violations(raw_scenes, project=project, new_assets=content.get(NEW_ASSETS_FIELD))
        if violations:
            async with draft_locks.async_file_lock(draft_path):
                report = await run_sync_transaction(
                    _after_draft_revision_check,
                    draft_path,
                    draft_baseline,
                    _quarantine_invalid_script_plan_generation,
                    project_path,
                    episode,
                    QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
                    content,
                    violations,
                    request.source,
                    formal_baseline,
                )
            raise TextGenerationError(report)

        await reissue_plan_item_ids(projects, project_name, episode, content)
        async with draft_locks.async_file_lock(draft_path):
            try:
                await run_sync_transaction(
                    _after_draft_revision_check,
                    draft_path,
                    draft_baseline,
                    commit_single_script_plan,
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
                    await run_sync_transaction(
                        _quarantine_formal_generation_conflict,
                        project_path,
                        episode,
                        QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
                        content,
                        request.source,
                        formal_baseline,
                        exc.actual,
                    )
                ) from exc

        return TextGenerationResult(
            drama_script_plan_result_text(script_plan_path.relative_to(project_path), raw_scenes, action="生成")
        )
    except (TextGenerationError, TextOutputTruncatedError, ModelNotInCatalogError):
        raise
    except VideoRequestFactsError as exc:
        raise VideoRequestFactsUnavailableError(exc.failure) from exc
    except Exception as exc:
        raise UnexpectedTextGenerationError(f"generate_script_plan 失败: {exc}") from exc


# ---------------------------------------------------------------------------
# reference-video generate_script_plan variant
# ---------------------------------------------------------------------------


class ReferenceSplitCaps(NamedTuple):
    """Video capabilities for reference splitting: two per-unit tier sets, derived maximum, user preference, and voice-input tier.

    ``reference_durations`` / ``text_durations`` are effective tiers with/without usable reference images;
    ``durations`` is their union for schema enums/prompt options, since either set can be valid. Membership
    is known only after body `@[名称]` mentions hydrate currently available images. All three equal means
    no effective reference-image↔duration coupling at current resolution, as for most models.

    ``voice`` is the voice-input tier derived from the same r2v request-fact evaluation for tolerated voice
    warnings. Separate lookups could make duration/voice notices describe different configuration moments
    for one artifact. Pass the value object, not whole facts: consumers need only voice bits, and passing
    facts would spread field-name coupling."""

    default_duration: int | None
    durations: list[int]
    reference_durations: list[int]
    text_durations: list[int]
    text_problem: VideoRequestFactsFailure | None
    max_duration: int
    max_refs: int | None
    voice: VoiceRenderSettings

    def tiers_for(self, *, has_references: bool) -> list[int]:
        """Effective tiers for this reference state."""
        return self.reference_durations if has_references else self.text_durations


async def fetch_reference_split_caps(
    project: dict[str, Any],
    *,
    config_resolver: ConfigResolver | None = None,
) -> ReferenceSplitCaps:
    """Resolve reference-splitting video capabilities (``ReferenceSplitCaps``) from r2v and i2v request facts.

    Units are generation calls; split-time durations are sent directly to providers. Use sets **narrowed by
    duration coupling** (borrow planning tiers for endpoint-fixed buckets). Without narrowing (Hailuo 1080p
    accepts only 6 seconds), script_plan splits oversized units that prompt_authoring enum schemas reject.

    Narrow separately per unit: reference-image↔duration coupling applies only to requests actually carrying
    images. Treating entire episodes as image-bearing removes valid short tiers from reference-free units.
    Schema/prompt options use both sets' union; actual membership depends on body-mentioned currently
    usable images and is checked per unit after parsing (``collect_reference_flat_violations``).
    ``max_duration`` is therefore the union maximum.

    Unresolved r2v (primary bucket) raises :class:`VideoRequestFactsError` without fallback tiers. Unresolved
    i2v yields empty image-free tiers and unchanged failure in ``text_problem``, reported per i2v unit.
    Treat off-union ``default_duration`` (user configuration drift) as None to avoid contradictory prompts."""
    request_facts = reference_request_facts_lookup(project, config_resolver)
    with_ref_facts = require_video_request_facts(await request_facts("r2v"))
    without_ref_facts = await request_facts("i2v")
    with_refs = planning_durations(with_ref_facts)
    without_refs = planning_durations(without_ref_facts) if isinstance(without_ref_facts, VideoRequestFacts) else []
    unit_durations = sorted(set(with_refs) | set(without_refs))
    default = _project_default_duration(project)
    return ReferenceSplitCaps(
        default_duration=default if default in unit_durations else None,
        durations=unit_durations,
        reference_durations=sorted(set(with_refs)),
        text_durations=sorted(set(without_refs)),
        text_problem=without_ref_facts if isinstance(without_ref_facts, VideoRequestFactsFailure) else None,
        max_duration=max(unit_durations),
        max_refs=with_ref_facts.max_reference_images,
        voice=VoiceRenderSettings.from_request_facts(with_ref_facts),
    )


def _validate_unit_duration_tier(label: str, duration: int, *, has_references: bool, caps: ReferenceSplitCaps) -> None:
    """Check duration against effective tiers based on this unit's currently usable images; off-tier raises ``DraftViolation``.

    Schema enums enforce only the union, so image-bearing units can still choose seconds valid solely
    for image-free units, unavailable at execution. Waiting until enqueue leaves no shared correction path.
    Error messages offer two options (change tier / remove references), matching prompt instruction.

    Raise content violations, not ``ValueError``: Agents can fix these in drafts through the repair loop,
    without discarding and regenerating."""
    if not has_references and caps.text_problem is not None:
        # Image-free bucket model-configuration problems: state cause/next steps by code; Web localizes code/parameters.
        failure = caps.text_problem
        params: dict[str, Any] = failure.parameters()
        raise DraftViolation(
            f"{label}：{translate(failure.code, **params)}（{failure.summary()}）",
            code=failure.code,
            label=label,
            params=params,
        )
    tiers = caps.tiers_for(has_references=has_references)
    if duration in tiers:
        return
    state = "带可用参考图" if has_references else "无可用参考图"
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


def _reference_unit_id(episode: int, index: int, unit_ids: Sequence[str] | None = None) -> str:
    """``unit_id`` of flat-output unit ``index`` (one-based).

    Persisted content, violation reports, and fallback notices must identify the same unit. Separately
    building formats makes numbering changes mispoint reports. Server issues IDs before persistence
    (``ProjectManager.reserve_item_ids``, allocation only without reuse), passed through ``unit_ids``;
    flat drafts awaiting issuance use positional location IDs."""
    if unit_ids is not None:
        return unit_ids[index - 1]
    return f"E{episode}U{index:02d}"


def _reference_unit_label(episode: int, index: int) -> str:
    """Location prefix for violations/notices (``unit E{集}U{两位序号}``)."""
    return f"unit {_reference_unit_id(episode, index)}"


def collect_reference_flat_violations(
    flat_units: list[dict[str, Any]],
    project: dict[str, Any],
    *,
    project_path: Path,
    episode: int,
    novel_text: str,
    caps: ReferenceSplitCaps,
    source_language: str | None,
    new_assets: object,
) -> list[DraftViolation]:
    """Collect every flat script_plan violation per unit without stopping at the first.

    Schemas enforce duration enums/outer shape. Add runtime-capability/registration/source constraints:
    duration in effective reference-state tiers, verbatim source-substring anchors, valid body syntax/asset
    references, and speakable dialogue volume. Collect all bad units at once, avoiding another run after
    each fix merely to discover the next issue.

    Combine duration/body checks: usable body-mentioned images (existing files claimed by manifests, as
    in confirmation/execution) determine tiers. Without parseable body text, tiers cannot be determined;
    reporting both would only restate the same problem.

    Body ``@[名称]`` must be registered or current additions. Validate against project views overlaid with
    additions; separately collect their handling decisions as episode-level violations."""
    # Dialogue-volume speech rates share prompt-side sources: project override first, otherwise language defaults.
    speech_rate_override = project_speech_rate_override(project)
    planning = planning_project(project, new_assets)
    hydrations = hydrate_reference_units(planning, project_path, flat_units)
    violations: list[DraftViolation] = []
    for index, (flat, hydration) in enumerate(zip(flat_units, hydrations, strict=True), start=1):
        label = _reference_unit_label(episode, index)
        duration = flat["duration_seconds"]
        source_text = flat["source_text"]
        text = flat["text"]
        with_reference_images = hydration.hydrated_generation_type == "r2v"

        def _check_text_and_tier(
            la: str = label, tx: str = text, d: int = duration, with_images: bool = with_reference_images
        ) -> None:
            validate_unit_text(la, tx, planning, max_refs=caps.max_refs)
            _validate_unit_duration_tier(la, d, has_references=with_images, caps=caps)

        violations.extend(
            collect_violations(
                [
                    lambda la=label, st=source_text: validate_source_text_anchor(la, st, novel_text),
                    _check_text_and_tier,
                    lambda la=label, tx=text, d=duration: validate_dialogue_load(
                        la, tx, d, source_language, speech_rate_override
                    ),
                ],
                item_index=index - 1,
                item_id=_reference_unit_id(episode, index),
            )
        )
    violations.extend(new_asset_violations(project, new_assets))
    return violations


def build_reference_units_from_flat(
    flat_units: list[dict[str, Any]],
    project: dict[str, Any],
    *,
    episode: int,
    max_refs: int | None,
    unit_ids: Sequence[str],
) -> list[dict]:
    """Derive persisted structured units from validated flat output.

    LLMs author content; machines author structure. ``unit_id`` uses server-issued ``unit_ids`` matching
    flat output one-to-one; persist body text unchanged. Do not persist reference images: resolve by body
    ``@[名称]`` first-occurrence order at execution. Callers must first pass ``collect_reference_flat_violations``;
    reparse bodies here too, ensuring validated text and persisted bodies come from the same parse."""
    units: list[dict] = []
    for index, flat in enumerate(flat_units, start=1):
        unit_id = _reference_unit_id(episode, index, unit_ids)
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


#: Tolerated warnings persisted/presented with artifacts (voice fallback). Other warning keys (unregistered mentions/
#: speakers, syntax misuse) are blocking violations for machine-produced content, not tolerated.
_TOLERATED_VOICE_WARNINGS = (
    WARN_SPEAKER_WITHOUT_AUDIO,
    WARN_REFERENCE_AUDIO_OVERFLOW,
    WARN_SILENT_MODEL,
    WARN_SILENT_EPISODE,
)


@dataclass(frozen=True, slots=True)
class SoftViolation:
    """One soft violation (fallback notice): ``code`` is wording key, ``params`` interpolation values, with draft item index/ID location."""

    code: str
    params: dict[str, Any]
    item_index: int
    item_id: str


def _reference_voice_warnings(
    unit_texts: list[str],
    project: dict[str, Any],
    voice: VoiceRenderSettings,
    *,
    episode: int,
    unit_ids: Sequence[str] | None,
) -> list[SoftViolation]:
    """Derive voice bindings per unit and collect tolerated warnings.

    Never concatenate whole episodes: units are calls and reference-audio limits apply per call. Combining
    would wrongly flag two speakers in each unit as excess. Share ``derive_voice_bindings`` with editor
    preview/execution rendering, keeping all three voice conclusions consistent for one draft.

    Always disable ``requires_reference_image`` here: it matters only with ``speakers_with_reference_image``,
    but splitting has not determined outgoing images. Enabling it without that set labels every speaker
    as lacking an attachable image; that untolerated warning is dropped, swallowing missing-reference-audio
    and excess-segment warnings Agents should see."""
    characters = project.get(BUCKET_KEY["character"]) or {}
    settings = replace(voice, requires_reference_image=False)
    found: list[SoftViolation] = []
    for index, text in enumerate(unit_texts, start=1):
        utterances, _syntax_warnings = derive_utterances(text)
        bindings = derive_voice_bindings(utterances, characters, settings)
        for warning in bindings.warnings:
            key = str(warning["key"])
            if key in _TOLERATED_VOICE_WARNINGS:
                found.append(
                    SoftViolation(key, dict(warning["params"]), index - 1, _reference_unit_id(episode, index, unit_ids))
                )
    return found


def _reference_scene_warnings(
    unit_texts: list[str], project: dict[str, Any], *, episode: int, unit_ids: Sequence[str] | None
) -> list[SoftViolation]:
    """Collect missing-scene-asset-reference notices per unit.

    Like voice fallbacks, these are tolerated: free model-selected locations are not format errors, so persist
    normally. But adjacent indoor/outdoor units may improvise independently; without production-time
    notices, Agents/users discover it only in final output.

    Use ``unit_lacks_scene_reference``, sharing editor-preview warnings: receipts/panels must agree on one body."""
    return [
        SoftViolation(WARN_UNIT_WITHOUT_SCENE, {}, index - 1, _reference_unit_id(episode, index, unit_ids))
        for index, text in enumerate(unit_texts, start=1)
        if unit_lacks_scene_reference(text, project)
    ]


def reference_soft_violations(
    unit_texts: list[str],
    project: dict[str, Any],
    *,
    episode: int,
    voice: VoiceRenderSettings,
    unit_ids: Sequence[str] | None = None,
) -> list[SoftViolation]:
    """All flat-output soft violations (voice fallback + missing scene references), shared by splitting and draft workflows.

    They neither block persistence nor enter violation reports, but every presentation path must yield the
    same conclusions: split/promotion receipts, reports to Agents when splitting/promotion is blocked,
    and Web item display. Keep derivation here so new soft-violation categories reach every path together,
    not just one while others remain silent.

    Keep voice-before-scene ordering, preventing row-order shifts across paths when comparing reports/receipts."""
    return _reference_voice_warnings(
        unit_texts, project, voice, episode=episode, unit_ids=unit_ids
    ) + _reference_scene_warnings(unit_texts, project, episode=episode, unit_ids=unit_ids)


def render_soft_violation_lines(soft_violations: list[SoftViolation]) -> list[str]:
    """Render soft violations as text lines returned to Agents.

    Deduplicate voice fallbacks across units in first-occurrence order; repeating one character's missing
    audio per unit is noise. Do not deduplicate missing scenes: retain unit locations and name every affected
    unit, or Agents cannot locate which ones need changes."""
    seen: set[tuple[str, str]] = set()
    lines: list[str] = []
    for soft in soft_violations:
        rendered = translate(soft.code, **soft.params)
        if soft.code == WARN_UNIT_WITHOUT_SCENE:
            lines.append(f"unit {soft.item_id}：{rendered}")
            continue
        if (soft.code, rendered) in seen:
            continue
        seen.add((soft.code, rendered))
        lines.append(rendered)
    return lines


def soft_violation_entries(soft_violations: list[SoftViolation]) -> list[dict[str, Any]]:
    """Soft violations → structured Web entries; presentation boundaries render ``message`` in request language using ``code`` + ``params``."""
    return [
        {"code": soft.code, "params": soft.params, "item_index": soft.item_index, "item_id": soft.item_id}
        for soft in soft_violations
    ]


def _reference_soft_violation_lines(
    unit_texts: list[str],
    project: dict[str, Any],
    *,
    episode: int,
    voice: VoiceRenderSettings,
    unit_ids: Sequence[str] | None = None,
) -> list[str]:
    """Flat-output soft-violation text lines for split receipts."""
    return render_soft_violation_lines(
        reference_soft_violations(unit_texts, project, episode=episode, voice=voice, unit_ids=unit_ids)
    )


#: Soft-violation handling note when promotion is blocked: drafts still exist; these notices are not repair-required violations.
SOFT_VIOLATION_NOTE_QUARANTINED = "非违约、无需为此重改，草稿仍在场"
#: Soft-violation handling note in successful split/promotion receipts.
SOFT_VIOLATION_NOTE_COMMITTED = "产物已落盘"


def render_soft_violation_section(soft_violations: list[str], *, note: str) -> str:
    """Render soft-violation lines as an appendable fallback-notices section (empty string without any).

    ``note`` explains handling in context: successful receipts have persisted artifacts, while violation
    reports retain drafts. This function exclusively owns headings/item shape, keeping report/receipt
    wording aligned and recognizable to Agents in one format."""
    if not soft_violations:
        return ""
    return f"\n⚠️ 降级提示（不阻断，{note}）:\n" + "\n".join(f"- {line}" for line in soft_violations)


def drama_script_plan_result_text(script_plan_path: Path, scenes: list[dict], *, action: str) -> str:
    """Drama-plan persistence receipt. ``action`` distinguishes generation/promotion; both share statistics
    format with only action wording differing, so Agents read both results in one format."""
    return f"✅ 规范化剧本{action}（结构化内容）已保存: {script_plan_path}\n📊 生成统计: {len(scenes)} 个分镜"


def narration_script_plan_result_text(script_plan_path: Path, segments: list[dict], *, action: str) -> str:
    """Narration-plan persistence receipt; ``action`` distinguishes splitting/promotion, for the same reason as drama."""
    total_chars = sum(len(str(segment.get("novel_text") or "")) for segment in segments)
    total_seconds = sum(int(segment.get("duration_seconds") or 0) for segment in segments)
    break_count = sum(1 for segment in segments if segment.get("segment_break"))
    return (
        f"✅ 旁白/解说分镜{action}（结构化 script_plan）已保存: {script_plan_path}\n"
        f"📊 生成统计: {len(segments)} 个分镜 / {total_chars} 字，"
        f"预计总时长 {total_seconds} 秒；segment_break 标记 {break_count} 个"
    )


def reference_result_text(script_plan_path: Path, units: list[dict], soft_violations: list[str], *, action: str) -> str:
    """Agent summary after successful promotion/splitting: persistence statistics + soft-violation section.

    ``action`` identifies resplitting versus draft promotion into the same formal script_plan file. Without
    that distinction, Agents fixing drafts would receive splitting-saved wording suggesting their edits were replaced by regeneration.

    Soft violations never block persistence but must accompany artifacts. Missing character reference audio
    or scene references degrade audible consistency/visible locations, discoverable only after generation;
    without immediate notices neither Agents nor users know. ``render_soft_violation_section`` shares
    rendering with reports for violation-blocked promotion."""
    total_seconds = sum(int(u.get("duration_seconds") or 0) for u in units)
    max_unit_refs = max(len(extract_mentions(str(u.get("text") or ""))) for u in units)
    text = (
        f"✅ 视频单元{action}（结构化 script_plan）已保存: {script_plan_path}\n"
        f"📊 生成统计: {len(units)} 个 unit，总时长 {total_seconds} 秒；"
        f"单 unit `@` 提及最多 {max_unit_refs} 个"
    )
    return text + render_soft_violation_section(soft_violations, note=SOFT_VIOLATION_NOTE_COMMITTED)


def narration_script_plan_path(project_path: Path, episode: int) -> Path:
    """Formal episode narration script_plan path (``drafts/episode_N/script_plan_segments.json``)."""
    return episode_drafts_dir(project_path, episode) / SCRIPT_PLAN_FILENAMES["narration"]


def _narration_segment_id(segment: dict[str, Any]) -> str | None:
    """Violation-location ``segment_id``; None if absent/blank, leaving only index location."""
    sid = segment.get("segment_id")
    return sid if isinstance(sid, str) and sid.strip() else None


def _narration_segment_label(segment: dict[str, Any], index: int) -> str:
    """Violation-location prefix.

    Missing/blank ``segment_id`` falls back to array index: itself a violation, but reports still need exact
    item locations, or Agents cannot act on the message."""
    sid = segment.get("segment_id")
    return f"segment {sid}" if isinstance(sid, str) and sid.strip() else f"segments[{index}]"


def _normalize_for_coverage(text: str) -> str:
    """Normalize Unicode NFC and collapse whitespace runs to one space, removing only encoding/whitespace differences, not whitespace itself.

    NFC matches source coordinates in ``lib.episode.episode_ledger.normalize_source_text`` and reference
    ``_normalize_for_anchor``. Combining-mark languages (e.g. vi) may have NFD sources and NFC model
    output; without normalization, pure encoding changes become deletion/rewrite violations, producing
    drafts blocking content confirmation and prompt_authoring."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def coverage_source_scope(source: str | None, *, episode: int) -> str:
    """Human-readable source scope for coverage checks, named in violation messages.

    Coverage is equality of concatenated storyboard text and whole source, depending on both bodies and
    source scope. Wrong scope (another episode's source file) fails even verbatim storyboards. Without
    naming scope, Agents repeatedly change bodies despite the problem lying elsewhere."""
    return f"源文件 {source or episode_source_relpath(episode)}"


def _covers_source_verbatim(parts: list[str], source: str) -> bool:
    """Whether storyboards cover *source* fully, verbatim, in order; allow at most one space at boundaries.

    :func:`_normalize_for_coverage` strips endpoint whitespace, so boundary spaces can only be separators.
    Skip greedily without backtracking: storyboards cannot themselves start with spaces competing for that character.

    Do not join per-segment ``re.escape`` with ``" ?"`` into one regex: its size scales with entire sources
    (hundreds of KB). This check runs in splitting, promotion, and confirmation read-time recalculation,
    the latter inside request coroutines; source-sized compilation repeatedly burdens the event loop.
    Cursor scanning implements the same check linearly."""
    cursor = 0
    for index, part in enumerate(parts):
        if index and source.startswith(" ", cursor):
            cursor += 1
        if not source.startswith(part, cursor):
            return False
        cursor += len(part)
    return cursor == len(source)


def collect_narration_violations(
    segments: list[dict[str, Any]],
    *,
    episode: int,
    supported_durations: list[int],
    project: Mapping[str, Any],
    new_assets: object,
    novel_text: str,
    source_scope: str,
) -> list[DraftViolation]:
    """Collect every narration-plan violation per storyboard without stopping at the first.

    References must be registered/current ``new_assets``; unresolved handling decisions add episode-level violations.

    Schemas (``NarrationScriptPlanDraft``) enforce fields/outer shape. Add runtime-capability/registration/
    source constraints: globally unique segment_id, nonblank novel_text, current-tier durations, registered
    asset names, and full verbatim ordered source coverage. Report all bad storyboards together, avoiding
    another run after each fix merely to discover the next issue.

    Raise content violations, not ``ValueError``: Agents can fix drafts through the repair loop, without discarding/regenerating.

    ``source_scope`` describes the source of ``novel_text`` (see :func:`coverage_source_scope`), entering
    coverage messages only. Coverage depends on source scope too; without naming it, reports cannot
    show whether storyboards or scope need fixing."""
    violations: list[DraftViolation] = []

    expected_id = re.compile(rf"E{episode}S\d{{2,}}")
    for index, segment in enumerate(segments):
        segment_id = segment.get("segment_id")
        if not isinstance(segment_id, str) or expected_id.fullmatch(segment_id) is None:
            label = _narration_segment_label(segment, index)
            violations.append(
                DraftViolation(
                    f"{label} 的 segment_id 必须为 E{episode}S## 格式且集号匹配",
                    code="invalid_segment_id",
                    label=label,
                    item_index=index,
                )
            )

    dupes = sorted(str(sid) for sid, count in Counter(s.get("segment_id") for s in segments).items() if count > 1)
    if dupes:
        # Episode-level violation without storyboard ownership; presentation uses aggregate areas.
        violations.append(
            DraftViolation(
                f"segment_id 重复: {dupes}；每个分镜的 id 须全集唯一（prompt_authoring 视觉层按 id 与分镜对齐）",
                code="duplicate_segment_id",
            )
        )

    allowed = {int(d) for d in supported_durations}
    # Registered names come from reference catalogs (same entry as reference ``validate_unit_text``). ``project.json`` names
    # and model-written names may use equivalent Unicode forms; catalogs normalize both comparison coordinates,
    # so form differences do not falsely mark registered assets missing. Fetch catalogs once outside loops, then only look up per storyboard.
    catalog = build_reference_catalog(planning_project(project, new_assets))
    for index, segment in enumerate(segments):
        label = _narration_segment_label(segment, index)

        # Static ``NarrationScriptPlanSegment.novel_text`` ``min_length=1`` checks raw length only; whitespace-only
        # strings (e.g. one space) pass without narration. Coverage normalization via
        # ``_normalize_for_coverage`` makes them empty, consuming no source characters and escaping coverage checks too,
        # yielding duration-bearing storyboards without narration.
        if not str(segment.get("novel_text") or "").strip():
            violations.append(
                DraftViolation(
                    f"{label} 的 novel_text 为空白；每个分镜必须携带逐字取自原文的旁白正文",
                    code="blank_novel_text",
                    label=label,
                    item_index=index,
                    item_id=_narration_segment_id(segment),
                )
            )

        # Static ``NarrationScriptPlanSegment.duration_seconds`` uses the ``ge=1, le=60`` open interval (shared existing storyboard
        # schema, without schema-level enum enforcement), allowing off-tier durations through. Add current-tier membership
        # checks here, matching ``ScriptGenerator._load_narration_script_plan``. Only validated
        # content becomes persisted script_plan truth, preventing invalid durations surfacing only at prompt_authoring/final save_script.
        duration = segment.get("duration_seconds")
        if not isinstance(duration, int) or duration not in allowed:
            violations.append(
                DraftViolation(
                    f"{label} 的时长 {duration} 不在模型档位 {sorted(allowed)} 内；请改取该档位内的时长",
                    code="duration_off_tier",
                    label=label,
                    item_index=index,
                    item_id=_narration_segment_id(segment),
                )
            )

        # Like reference ``validate_unit_text`` registration checks for ``@[名称]``, trust only registered/currently added assets,
        # never model-invented/misspelled names persisted as truth for read-only prompt_authoring visual consumption.
        violations.extend(
            _unregistered_reference_violations(
                "narration",
                segment,
                catalog,
                label=label,
                item_index=index,
                item_id=_narration_segment_id(segment),
            )
        )

    # Boundary whitespace is ambiguous: model split points may consume a source space,
    # leaving it in neither storyboard, or occur at unspaced CJK/punctuation boundaries; concatenated
    # text cannot reliably distinguish them. Allow one optional space only at storyboard boundaries; internal text and all remaining source
    # must match verbatim after whitespace collapse, preventing boundary tolerance masking real internal deletion, rewrites, or lost word spaces.
    # Coverage is whole-set (concatenation versus entire source), without one storyboard location, hence no label.
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
    violations.extend(new_asset_violations(project, new_assets))
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
    project_path, project = await asyncio.to_thread(_load_project_context, projects, project_name)

    try:
        novel_text, prompt_inputs, script_plan_basis = await asyncio.to_thread(
            load_script_plan_source_with_basis,
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
        split_caps = await fetch_reference_split_caps(project, config_resolver=config_resolver)
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
            source_kind=cast(str | None, prompt_inputs["source_kind"]) or DEFAULT_SOURCE_KIND,
            target_language=cast(str, prompt_inputs["target_language"]),
            source_language=cast(str | None, prompt_inputs["source_language"]),
            speech_rate_override=cast(float | None, prompt_inputs["speech_rate_override"]),
            episode_target_duration=cast(int | None, prompt_inputs["episode_target_duration"]),
            episode_outline=cast(dict[str, Any] | None, prompt_inputs["episode_outline"]),
            next_episode_outline=cast(dict[str, Any] | None, prompt_inputs["next_episode_outline"]),
            instructions=instructions,
        )

        if request.dry_run:
            return TextGenerationResult(
                f"DRY RUN — 以下是将发送给文本模型的 Prompt:\n\n{prompt}\n\nPrompt 长度: {len(prompt)} 字符"
            )

        draft_path = quarantine_path(project_path, episode, QUARANTINE_KIND_SCRIPT_PLAN)
        formal_script_plan_path = script_review.official_reference_script_plan_path(project_path, episode)
        # ProjectManager construction creates directories; offload to a thread.
        draft_locks = await asyncio.to_thread(ProjectManager.for_project_dir, project_path)
        async with draft_locks.async_file_lock(draft_path):
            draft_baseline, formal_baseline = await asyncio.to_thread(
                _generation_baselines,
                draft_path,
                formal_script_plan_path,
            )
        schema = build_reference_units_script_plan_model(split_caps.durations)
        generator = await TextGenerator.create(
            TextTaskType.SCRIPT, project_name=project_name, purpose=CallPurpose.SCRIPT_GENERATION
        )
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
        new_assets = dedupe_new_assets(flat[NEW_ASSETS_FIELD])

        violations = await asyncio.to_thread(
            collect_reference_flat_violations,
            flat_units,
            project,
            project_path=project_path,
            episode=episode,
            novel_text=novel_text,
            caps=split_caps,
            source_language=project.get("source_language"),
            new_assets=new_assets,
        )
        planning = planning_project(project, new_assets)
        unit_texts = [flat_unit["text"] for flat_unit in flat_units]
        soft_violations = _reference_soft_violation_lines(unit_texts, planning, episode=episode, voice=split_caps.voice)
        if violations:
            async with draft_locks.async_file_lock(draft_path):
                report = await run_sync_transaction(
                    _after_draft_revision_check,
                    draft_path,
                    draft_baseline,
                    _quarantine_invalid_script_plan_generation,
                    project_path,
                    episode,
                    QUARANTINE_KIND_SCRIPT_PLAN,
                    with_new_assets({"units": flat_units}, new_assets),
                    violations,
                    request.source,
                    formal_baseline,
                )
            # Output-violation paths also include soft notices: drafts remain and Agents edit them this turn; first showing fallbacks at promotion
            # would mean they already repaired with incomplete information. Use the same note as blocked promotion.
            raise TextGenerationError(
                report + render_soft_violation_section(soft_violations, note=SOFT_VIOLATION_NOTE_QUARANTINED)
            )

        # IDs allocate without reuse: issue episode-unused IDs before persistence, locating receipt fallback notices by those IDs.
        unit_ids = await run_sync_transaction(
            projects.reserve_item_ids, project_name, episode, "video_units", len(flat_units)
        )
        raw_units = build_reference_units_from_flat(
            flat_units,
            planning,
            episode=episode,
            max_refs=split_caps.max_refs,
            unit_ids=unit_ids,
        )
        soft_violations = _reference_soft_violation_lines(
            unit_texts, planning, episode=episode, voice=split_caps.voice, unit_ids=unit_ids
        )
        async with draft_locks.async_file_lock(draft_path):
            try:
                await run_sync_transaction(
                    _after_draft_revision_check,
                    draft_path,
                    draft_baseline,
                    _commit_generated_reference_script_plan,
                    project_path,
                    episode,
                    with_new_assets({"units": raw_units}, new_assets),
                    formal_baseline,
                    script_plan_basis,
                    before_commit,
                )
            except script_review.ScriptPlanWriteConflict as exc:
                raise TextGenerationError(
                    await run_sync_transaction(
                        _quarantine_formal_generation_conflict,
                        project_path,
                        episode,
                        QUARANTINE_KIND_SCRIPT_PLAN,
                        with_new_assets({"units": flat_units}, new_assets),
                        request.source,
                        formal_baseline,
                        exc.actual,
                    )
                ) from exc
        return TextGenerationResult(
            reference_result_text(
                script_review.official_reference_script_plan_path(project_path, episode).relative_to(project_path),
                raw_units,
                soft_violations,
                action="拆分",
            )
        )
    except (TextGenerationError, TextOutputTruncatedError, ModelNotInCatalogError):
        raise
    except VideoRequestFactsError as exc:
        raise VideoRequestFactsUnavailableError(exc.failure) from exc
    except Exception as exc:
        raise UnexpectedTextGenerationError(f"generate_script_plan 失败: {exc}") from exc


async def generate_narration_script_plan(
    request: TextGenerationRequest,
    *,
    project_name: str,
    projects: ProjectManager,
    config_resolver: ConfigResolver,
) -> TextGenerationResult:
    episode = request.episode
    instructions = _instructions(request.instructions)
    project_path, project = await asyncio.to_thread(_load_project_context, projects, project_name)

    try:
        novel_text, prompt_inputs, script_plan_basis = await asyncio.to_thread(
            load_script_plan_source_with_basis,
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
        default_duration, supported_durations = await fetch_storyboard_durations(
            project, config_resolver=config_resolver
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
            episode_target_duration=cast(int | None, prompt_inputs["episode_target_duration"]),
            instructions=instructions,
        )

        if request.dry_run:
            return TextGenerationResult(
                f"DRY RUN — 以下是将发送给文本模型的 Prompt:\n\n{prompt}\n\nPrompt 长度: {len(prompt)} 字符"
            )

        draft_path = quarantine_path(project_path, episode, QUARANTINE_KIND_NARRATION_SCRIPT_PLAN)
        script_plan_path = narration_script_plan_path(project_path, episode)
        # ProjectManager construction creates directories; offload to a thread.
        draft_locks = await asyncio.to_thread(ProjectManager.for_project_dir, project_path)
        async with draft_locks.async_file_lock(draft_path):
            draft_baseline, formal_baseline = await asyncio.to_thread(
                _generation_baselines,
                draft_path,
                script_plan_path,
            )
        generator = await TextGenerator.create(
            TextTaskType.SCRIPT, project_name=project_name, purpose=CallPurpose.SCRIPT_GENERATION
        )
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
        content = with_new_assets(content, content[NEW_ASSETS_FIELD])

        violations = collect_narration_violations(
            raw_segments,
            episode=episode,
            supported_durations=supported_durations,
            project=project,
            new_assets=content.get(NEW_ASSETS_FIELD),
            novel_text=novel_text,
            source_scope=coverage_source_scope(request.source, episode=episode),
        )
        if violations:
            async with draft_locks.async_file_lock(draft_path):
                report = await run_sync_transaction(
                    _after_draft_revision_check,
                    draft_path,
                    draft_baseline,
                    _quarantine_invalid_script_plan_generation,
                    project_path,
                    episode,
                    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
                    content,
                    violations,
                    request.source,
                    formal_baseline,
                )
            raise TextGenerationError(report)

        await reissue_plan_item_ids(projects, project_name, episode, content)
        async with draft_locks.async_file_lock(draft_path):
            try:
                await run_sync_transaction(
                    _after_draft_revision_check,
                    draft_path,
                    draft_baseline,
                    commit_single_script_plan,
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
                    await run_sync_transaction(
                        _quarantine_formal_generation_conflict,
                        project_path,
                        episode,
                        QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
                        content,
                        request.source,
                        formal_baseline,
                        exc.actual,
                    )
                ) from exc

        return TextGenerationResult(
            narration_script_plan_result_text(script_plan_path.relative_to(project_path), raw_segments, action="拆分")
        )
    except (TextGenerationError, TextOutputTruncatedError, ModelNotInCatalogError):
        raise
    except VideoRequestFactsError as exc:
        raise VideoRequestFactsUnavailableError(exc.failure) from exc
    except Exception as exc:
        raise UnexpectedTextGenerationError(f"generate_script_plan 失败: {exc}") from exc
