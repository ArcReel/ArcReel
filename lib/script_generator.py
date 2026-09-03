"""
script_generator.py - 剧本生成器

读取脚本规划结构化中间文件，调用文本生成 Backend 生成最终 JSON 剧本
"""

import asyncio
import hashlib
import json
import logging
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional, cast

from pydantic import BaseModel, TypeAdapter, ValidationError
from sqlalchemy.exc import SQLAlchemyError

from lib.artifact_activation import (
    ArtifactInputClaim,
    active_artifact_currency_resolver,
    assert_current_artifact_input_claims_usable,
    resolve_usable_artifact_input_claim,
)
from lib.artifact_manifest import ArtifactBasisDescriptor, ArtifactEntryRekeyReceipt, ArtifactKey
from lib.artifact_provenance import (
    build_ad_episode_script_basis,
    build_episode_script_basis,
    project_ad_episode_script_inputs,
)
from lib.async_thread import run_sync_transaction
from lib.backend_assembly.specs import builtin_video_capabilities_for_model
from lib.config.registry import PROVIDER_REGISTRY
from lib.config.resolver import (
    ConfigResolver,
    VideoBucketCapabilityError,
    constrain_durations_for_project,
    project_video_backend_ids,
    resolve_raw_supported_durations,
)
from lib.content_digest import sha256_file
from lib.db import async_session_factory
from lib.draft_quarantine import (
    PROMOTE_TOOL_NAME,
    QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
    QUARANTINE_KIND_PROMPT_AUTHORING,
    QUARANTINE_KIND_SCRIPT_PLAN,
    clear_quarantine,
    draft_revision,
    quarantine_and_report,
    quarantine_path,
    read_quarantine,
)
from lib.episode_paths import (
    REFERENCE_VIDEO_SCRIPT_PLAN_FILENAME,
    REFERENCE_VIDEO_SCRIPT_PLAN_LEGACY_FILENAME,
    SCRIPT_PLAN_FILENAMES,
    SCRIPT_PLAN_LEGACY_FILENAMES,
    episode_drafts_dir,
    episode_script_filename,
)
from lib.formal_write import FormalWriteReceipt
from lib.project_manager import ProjectManager, ScriptWriteConflict
from lib.prompt_builders_ad import build_ad_prompt, build_ad_reference_prompt
from lib.prompt_builders_reference import build_reference_video_prompt
from lib.prompt_builders_script import (
    append_user_instructions,
    build_drama_prompt,
    build_narration_prompt,
    render_drama_content_for_prompt_authoring,
)
from lib.reference_video.draft_validation import (
    DraftViolation,
    DraftViolations,
    assert_dialogue_preserved,
    validate_dialogue_load,
    validate_unit_text,
    violation_items,
)
from lib.reference_video.duration_slots import resolve_duration_slot
from lib.reference_video.text_parser import extract_mentions
from lib.script_models import (
    AD_TARGET_DURATION_DRIFT_THRESHOLD,
    AdEpisodeScript,
    AdReferenceFlatScript,
    DramaEpisodeScript,
    DramaSceneContent,
    DramaVisualScript,
    NarrationEpisodeScript,
    NarrationScriptPlanDraft,
    NarrationVisualEpisodeScript,
    ReferencePromptAuthoringFlatScript,
    ReferenceScriptPlanDraft,
    ReferenceVideoScript,
    build_episode_script_model,
    merge_drama_visual_into_scenes,
    script_duration_total,
)
from lib.script_plan_entries import (
    ScriptEntryCurrency,
    ScriptPlanEntryError,
    ScriptPlanKind,
    entry_id_field,
    evaluate_entry_currency,
    plan_entry_revisions,
    plan_variant,
    resolve_rewrite_ids,
    script_entries_by_id,
    splice_entries,
)
from lib.script_review import (
    SCRIPT_PLAN_REVISION_FIELD,
    content_fingerprint,
    content_fingerprint_of_data,
    gate_blocks_prompt_authoring,
    migrate_script_plan_draft_in_place,
)
from lib.script_skeleton import resolve_declared_kind, resolve_kind_items, rewrite_episode_prefix
from lib.speech_composition import admit_script_unit, require_script_unit_admitted, video_unit_replan_problems
from lib.speech_rate import project_speech_rate_override
from lib.text_backends.base import DEFAULT_MAX_OUTPUT_TOKENS, TextGenerationRequest, TextTaskType
from lib.text_generator import TextGenerator
from lib.text_utils import strip_json_code_fences

logger = logging.getLogger(__name__)


class _UnsetExpectedFingerprint:
    pass


_UNSET_EXPECTED_FINGERPRINT = _UnsetExpectedFingerprint()

# drama script_plan 时长的归一化口径：与 DramaSceneContent.duration_seconds（非 strict int）同一套，
# 避免校验侧与落盘侧对 "4" / 4.0 这类取值判断不一致。默认值也取字段声明，不另写字面量。
_DURATION_ADAPTER = TypeAdapter(int)
_DRAMA_DEFAULT_DURATION = DramaSceneContent.model_fields["duration_seconds"].default

#: dry-run 在本次没有条目要重写时的回答：此时真实运行不会调用文本模型，也就没有 prompt 可预览。
_NO_ENTRY_TO_REWRITE_NOTE = "本次没有需要重写的条目：脚本规划与现有剧本逐条一致，运行时不会调用文本模型。"

# 质量探针阈值：仅捕极端短样本，正常完整描述应远超这些值。
_QUALITY_PROBE_SCENE_MIN_LEN = 40
_QUALITY_PROBE_ACTION_MIN_LEN = 25
_QUALITY_PROBE_UNIT_TEXT_MIN_LEN = 15

# 骨架种类 → 响应校验模型。模型类属上层依赖、不进 SKELETONS 窄表，映射留本地。
# 键与 SKELETONS 逐一对应；新增第五种骨架时穷尽性断言逐个报红。
_KIND_PARSE_SCHEMA: dict[str, type[BaseModel]] = {
    "segments": NarrationEpisodeScript,
    "scenes": DramaEpisodeScript,
    "shots": AdEpisodeScript,
    "video_units": ReferenceVideoScript,
}


def _units_use_references(units: list[Any] | None) -> bool | None:
    """本集 script_plan 是否存在带 ``@[名称]`` 提及的 unit；``units`` 为 None（非参考生视频路径）时返回 None。

    None 的语义是「交给下游按生成模式近似判定」，与「确定不带参考图」的 False 区分开。
    参考生视频路径允许通用 unit 不带任何引用，执行层与调用通道都只在实际带图时施加
    「参考图↔时长」约束——整集都无引用时按模式一刀切会收掉本可申请的档位。
    """
    if units is None:
        return None
    return any(extract_mentions(str(u.get("text") or "")) for u in units if isinstance(u, dict))


@dataclass(frozen=True, slots=True)
class PromptAuthoringScope:
    """一次提示词编写的重写范围：哪些条目要重出视觉层，其余条目从哪份旧剧本原样沿用。

    ``entries_to_rewrite`` 是**脚本规划条目**（未改写集号前缀），供 prompt 渲染与既有的按 id /
    按位合并复用；``plan_revisions`` 与 ``existing`` 用改写后的落盘 id 为键，供装配阶段对齐。
    """

    plan_kind: ScriptPlanKind
    plan_revisions: dict[str, str]
    existing: dict[str, dict]
    existing_title: str | None
    rewrite_ids: tuple[str, ...]
    entries_to_rewrite: list[dict]
    currency: ScriptEntryCurrency

    @property
    def items_key(self) -> str:
        """该变体在剧本 dict 里的条目数组键。"""
        return plan_variant(self.plan_kind).skeleton_kind


class ScriptGenerator:
    """
    剧本生成器

    读取脚本规划 / 提示词编写的 Markdown 中间文件，调用 TextBackend 生成最终 JSON 剧本
    """

    # 类属性缺省，绕过 __init__ 构造的实例同样读到 None；解析时按需回退到生产 ConfigResolver。
    config_resolver: ConfigResolver | None = None

    def __init__(
        self,
        project_path: str | Path,
        generator: Optional["TextGenerator"] = None,
        *,
        config_resolver: ConfigResolver | None = None,
    ):
        """
        初始化生成器

        Args:
            project_path: 项目目录路径，如 projects/test0205
            generator: TextGenerator 实例（可选）。若为 None 则仅支持 build_prompt() dry-run。
            config_resolver: 能力解析器；缺省时解析期构造连接生产数据库的 ConfigResolver。
        """
        self.project_path = Path(project_path)
        self.generator = generator
        self.config_resolver = config_resolver
        self._script_plan_revision: str | None = None
        self._artifact_basis: ArtifactBasisDescriptor | None = None
        self._script_plan_input_claim: ArtifactInputClaim | None = None

        # 加载 project.json
        self.project_json = self._load_project_json()
        self.content_mode = self.project_json.get("content_mode", "narration")

    @property
    def generation_mode(self) -> str | None:
        """项目生成模式（project.json 顶层字段）：创建即定、之后不可变，不随集号变化。"""
        return self.project_json.get("generation_mode")

    def _episode_entry(self, episode: int) -> dict:
        """按集号取 project.json episodes 条目；缺失返回空 dict。"""
        return next(
            (
                ep
                for ep in (self.project_json.get("episodes") or [])
                if isinstance(ep, dict) and ep.get("episode") == episode
            ),
            {},
        )

    @staticmethod
    def _entry_outline(entry: dict) -> dict:
        """账本条目的 outline 字段归一化为 dict（缺失/形状异常返回空 dict）。"""
        raw_outline = entry.get("outline")
        return raw_outline if isinstance(raw_outline, dict) else {}

    @classmethod
    async def create(
        cls,
        project_path: str | Path,
        *,
        config_resolver: ConfigResolver | None = None,
    ) -> "ScriptGenerator":
        """异步工厂方法，自动从 DB 加载供应商配置创建 TextGenerator。"""
        project_name = Path(project_path).name
        generator = await TextGenerator.create(TextTaskType.SCRIPT, project_name)
        return await asyncio.to_thread(
            cls,
            project_path,
            generator,
            config_resolver=config_resolver,
        )

    def _resolve_prompt_authoring_scope(
        self,
        episode: int,
        filename: str,
        *,
        plan_kind: ScriptPlanKind,
        plan_entries: list[dict],
        scope: str | Iterable[str] | None,
    ) -> PromptAuthoringScope:
        """按条目比对脚本规划与现有剧本，定出本次要重写视觉层的条目。

        现有剧本不存在（首次生成）时全部条目都是新增，退化为整集生成；存量剧本的条目没有
        条目指纹，按剧本 metadata 记录的整集脚本规划指纹是否仍等于当前值回退判定
        （见 ``evaluate_entry_currency``），因而不会因为升级本身被误报失效。
        """
        plan_revisions = plan_entry_revisions(plan_kind, plan_entries, episode=episode)
        pm = ProjectManager(str(self.project_path.parent))
        try:
            existing_script = pm.load_script_readonly(self.project_path.name, filename)
        except (FileNotFoundError, ValueError):
            # 剧本不存在，或磁盘上那份读不成 dict：两者都没有可沿用的条目，按整集生成处置。
            existing_script = None
        if existing_script is None:
            currency = evaluate_entry_currency(
                plan_kind, script={}, plan_revisions=plan_revisions, legacy_entries_current=False
            )
            existing: dict[str, dict] = {}
            existing_title = None
        else:
            metadata = existing_script.get("metadata")
            recorded = metadata.get(SCRIPT_PLAN_REVISION_FIELD) if isinstance(metadata, Mapping) else None
            currency = evaluate_entry_currency(
                plan_kind,
                script=existing_script,
                plan_revisions=plan_revisions,
                legacy_entries_current=(
                    self._script_plan_revision is not None and recorded == self._script_plan_revision
                ),
            )
            existing = script_entries_by_id(plan_kind, existing_script)
            raw_title = existing_script.get("title")
            existing_title = raw_title if isinstance(raw_title, str) and raw_title.strip() else None

        rewrite_ids = resolve_rewrite_ids(scope, currency)
        selected = set(rewrite_ids)
        # 点名重写时漏掉一个新增条目，装配阶段才会发现它既没被重写、旧剧本里也没有——那时
        # 文本模型已经调用并计费。在调用之前拦下，并且说清该怎么改这次调用。
        uncovered = [entry_id for entry_id in plan_revisions if entry_id not in selected and entry_id not in existing]
        if uncovered:
            raise ScriptPlanEntryError(
                f"脚本规划新增的条目不在本次重写范围内，剧本里也还没有它们: {uncovered}；"
                "请把它们一并列入 entry_ids，或改用默认范围（只重写失配与新增条目）"
            )
        entries_to_rewrite = [
            entry
            for entry in plan_entries
            if str(rewrite_episode_prefix(entry.get(entry_id_field(plan_kind)), episode)) in selected
        ]
        return PromptAuthoringScope(
            plan_kind=plan_kind,
            plan_revisions=plan_revisions,
            existing=existing,
            existing_title=existing_title,
            rewrite_ids=rewrite_ids,
            entries_to_rewrite=entries_to_rewrite,
            currency=currency,
        )

    def _assemble_script(
        self,
        script_data: dict,
        scope: PromptAuthoringScope,
    ) -> dict:
        """把本次重写的条目与沿用的旧条目按脚本规划顺序装配回剧本，并盖上条目内容指纹。"""
        rewritten = script_data.get(scope.items_key)
        script_data[scope.items_key] = splice_entries(
            scope.plan_kind,
            plan_revisions=scope.plan_revisions,
            rewritten=rewritten if isinstance(rewritten, list) else [],
            existing=scope.existing,
        )
        return script_data

    async def _save_without_rewrite(
        self,
        episode: int,
        filename: str,
        scope: PromptAuthoringScope,
        *,
        title: str | None,
        cancellation_file_receipts: list[FormalWriteReceipt] | None,
        cancellation_manifest_receipts: list[ArtifactEntryRekeyReceipt] | None,
    ) -> Path:
        """没有条目需要重写时的落盘路径：不调用文本模型，只按脚本规划的顺序与集合装配旧条目。

        仍然落盘而非直接返回：条目可能被删除或调换顺序，剧本要跟随；条目指纹也要在此补齐，
        存量剧本经此一次即带上条目级口径。
        """
        script_data: dict[str, Any] = {
            "title": title or scope.existing_title or f"第{episode}集",
            scope.items_key: [],
        }
        script_data = self._add_metadata(script_data, episode)
        script_data = self._assemble_script(script_data, scope)
        pm = ProjectManager(str(self.project_path.parent))
        formal_baseline = await asyncio.to_thread(content_fingerprint, self.project_path / "scripts" / filename)
        output_path = await run_sync_transaction(
            pm.save_script,
            self.project_path.name,
            script_data,
            filename,
            validate=True,
            artifact_basis=self._artifact_basis,
            expected_fingerprint=formal_baseline,
            cancellation_file_receipts=cancellation_file_receipts,
            cancellation_manifest_receipts=cancellation_manifest_receipts,
        )
        logger.info("第 %d 集无失效条目，剧本按脚本规划顺序装配后已保存至 %s", episode, output_path)
        return output_path

    async def _scope_or_save_without_rewrite(
        self,
        episode: int,
        filename: str,
        *,
        plan_kind: ScriptPlanKind,
        plan_entries: list[dict],
        scope: str | Iterable[str] | None,
        rewritten_entry_ids: list[str] | None,
        title: str | None,
        before_save: Callable[[], None] | None = None,
        cancellation_file_receipts: list[FormalWriteReceipt] | None,
        cancellation_manifest_receipts: list[ArtifactEntryRekeyReceipt] | None,
    ) -> tuple[PromptAuthoringScope, Path | None]:
        """解析重写范围，无条目可重写时直接免调用落盘。

        返回的 Path 非 None 即代表落盘已在此处走完——调用方原样返回它，不再构造 prompt。
        ``before_save`` 是该变体在免调用落盘前仍要过的准入断言（走文本模型的那条路径上另有一次）。
        """
        authoring_scope = self._resolve_prompt_authoring_scope(
            episode,
            filename,
            plan_kind=plan_kind,
            plan_entries=plan_entries,
            scope=scope,
        )
        if rewritten_entry_ids is not None:
            rewritten_entry_ids[:] = authoring_scope.rewrite_ids
        if authoring_scope.entries_to_rewrite:
            return authoring_scope, None
        if before_save is not None:
            before_save()
        output_path = await self._save_without_rewrite(
            episode,
            filename,
            authoring_scope,
            title=title,
            cancellation_file_receipts=cancellation_file_receipts,
            cancellation_manifest_receipts=cancellation_manifest_receipts,
        )
        return authoring_scope, output_path

    def _dry_run_entries_to_rewrite(
        self,
        episode: int,
        *,
        plan_kind: ScriptPlanKind,
        plan_entries: list[dict],
        scope: str | Iterable[str] | None,
    ) -> list[dict] | None:
        """dry-run 侧的重写范围；None 表示没有条目要重写，调用方改回预览说明。

        dry-run 恒以默认文件名为增量基准：它不落盘，也就没有 ``output_filename`` 可言。
        """
        authoring_scope = self._resolve_prompt_authoring_scope(
            episode,
            episode_script_filename(episode),
            plan_kind=plan_kind,
            plan_entries=plan_entries,
            scope=scope,
        )
        return authoring_scope.entries_to_rewrite or None

    async def generate(
        self,
        episode: int,
        output_filename: str | None = None,
        *,
        instructions: str | None = None,
        scope: str | Iterable[str] | None = None,
        rewritten_entry_ids: list[str] | None = None,
        before_quarantine_commit: Callable[[], None] | None = None,
        cancellation_file_receipts: list[FormalWriteReceipt] | None = None,
        cancellation_manifest_receipts: list[ArtifactEntryRekeyReceipt] | None = None,
    ) -> Path:
        """
        异步生成剧集剧本

        Args:
            episode: 剧集编号
            output_filename: 输出文件名，默认 episode_{episode}.json。剧本一律经写盘统一入口写入
                项目 scripts/ 目录，故此参数只决定文件名、不接受目录。
            instructions: 用户输入的附加指令原文；非空时以中性「附加指令」分节追加到
                prompt 末尾（遵循强度由正文表达），所有 content_mode / 生成模式同口径。
            scope: 本次重写视觉层的条目范围。``None`` / ``"stale"``（默认）只重写内容失配与新增
                的条目，其余条目连同视觉层与用户字段原样沿用；``"all"`` 整集重写；条目 id 列表
                只重写指定条目，其中任一 id 不在当前脚本规划内即报错、不落盘。ad 无脚本规划，
                该参数不适用。
            rewritten_entry_ids: 可选收集器；非 None 时就地填入本次实际重写视觉层的条目 id
                （脚本规划顺序），供调用方在回执里列出。与 ``cancellation_*_receipts`` 同一种
                出参形态——``generate`` 的返回值是产物路径，附带事实经收集器带出，调用方不必
                向生成器索取运行期状态。

        Returns:
            生成的 JSON 文件路径
        """
        if self.generator is None:
            raise RuntimeError("TextGenerator 未初始化，请使用 ScriptGenerator.create() 工厂方法")

        # 兑现 docstring 的「只决定文件名、不接受目录」契约:写盘咽喉 _safe_subpath 能挡绝对
        # 路径与 path traversal,但不会挡子目录(`subdir/x.json` 拼出的 realpath 仍在 scripts/
        # 内,会让剧本写到 scripts/subdir/x.json,偏离扁平布局)。在公开 API 入口 fail-fast 拒,
        # 既兑现契约也避免跑完整套生成流程才撞到错。
        # 显式拒 `\\`:POSIX 上 Path 不当其为分隔符,但 Windows 上是;按跨平台兼容做防御。
        # 空字符串 "" 也显式拒:Path("").name == "" 等于 output_filename 会过前两条,
        # 带空 filename 流到 save_script 在写盘阶段才崩;入口 fail-fast 才不撕裂时机。
        if output_filename is not None and (
            not output_filename or Path(output_filename).name != output_filename or "\\" in output_filename
        ):
            raise ValueError(f"output_filename 只接受纯文件名，不允许目录或路径分隔符: {output_filename!r}")

        self._script_plan_revision = None
        self._artifact_basis = None
        self._script_plan_input_claim = None
        gen_mode = self.generation_mode

        # ad 两种生成模式都一键生成、不走 script_plan；参考生视频直接产出自包含 video_units。
        if self.content_mode == "ad":
            prompt, schema = await self._compose_ad(episode, gen_mode)
            prompt = append_user_instructions(prompt, instructions)
            self._freeze_ad_artifact_basis(episode)
            return await self._generate_and_save(
                prompt,
                schema,
                episode,
                output_filename,
                cancellation_file_receipts=cancellation_file_receipts,
                cancellation_manifest_receipts=cancellation_manifest_receipts,
            )

        # 剧情演绎的分镜图生视频（含宫格装配）走两段式（见 ADR 0041）：script_plan 内容已是结构化 JSON，
        # prompt_authoring 仅出视觉层（image_prompt / video_prompt），后端按 scene_id 合并回 script_plan 内容、
        # 透传 utterances / source_text 等非视觉字段。reference_video 路径不入此分支（用 video_units）；
        # content_mode 非 narration（drama 或脏值）走 prompt_authoring drama 形状。
        if gen_mode != "reference_video" and self.content_mode != "narration":
            return await self._generate_drama_prompt_authoring(
                episode,
                output_filename,
                gen_mode=gen_mode,
                instructions=instructions,
                scope=scope,
                rewritten_entry_ids=rewritten_entry_ids,
                cancellation_file_receipts=cancellation_file_receipts,
                cancellation_manifest_receipts=cancellation_manifest_receipts,
            )

        caps = await self._fetch_video_capabilities()

        characters = self.project_json.get("characters")
        characters = characters if isinstance(characters, dict) else {}
        scenes = self.project_json.get("scenes")
        scenes = scenes if isinstance(scenes, dict) else {}
        props = self.project_json.get("props")
        props = props if isinstance(props, dict) else {}

        # 参考生视频路径先读 script_plan：本集是否真的带参考图决定要不要施加「参考图↔时长」约束，
        # 故此处先按未收窄的全集校验 unit 时长，收窄后的集合在下方按引用情况解析。
        script_plan_units = None
        if gen_mode == "reference_video":
            script_plan_units = await run_sync_transaction(
                self._load_reference_script_plan,
                episode,
                self._resolve_raw_supported_durations(caps),
            )

        # 解析一次时长能力：reference 据此构造 duration 枚举硬约束 schema；
        # narration 两段式用于校验 script_plan 各分镜时长成员合法（prompt_authoring 不再产出时长）。
        supported_durations = self._resolve_supported_durations(
            caps, gen_mode=gen_mode, uses_reference_images=_units_use_references(script_plan_units)
        )

        # narration 走两段式：script_plan 结构化分镜透传内容层（novel_text 等），prompt_authoring 仅产视觉层、
        # 按 segment_id 合并回 script_plan。非 narration 走单段（script_plan markdown 直喂 LLM）。
        narration_script_plan: list[dict] | None = None

        authoring_scope: PromptAuthoringScope | None = None
        filename = output_filename or episode_script_filename(episode)
        if script_plan_units is not None:
            units_for_admission = script_plan_units
            authoring_scope, saved_path = await self._scope_or_save_without_rewrite(
                episode,
                filename,
                plan_kind="reference_video",
                plan_entries=script_plan_units,
                scope=scope,
                rewritten_entry_ids=rewritten_entry_ids,
                title=None,
                before_save=lambda: self._assert_reference_script_plan_ready(
                    units_for_admission, caps=caps, gen_mode=gen_mode
                ),
                cancellation_file_receipts=cancellation_file_receipts,
                cancellation_manifest_receipts=cancellation_manifest_receipts,
            )
            if saved_path is not None:
                return saved_path
            prompt = build_reference_video_prompt(
                project_overview=self.project_json.get("overview", {}),
                style=self.project_json.get("style", ""),
                style_description=self.project_json.get("style_description", ""),
                characters=characters,
                scenes=scenes,
                props=props,
                script_plan_units=authoring_scope.entries_to_rewrite,
                max_refs=self._resolve_max_refs(caps),
                aspect_ratio=self._resolve_aspect_ratio(),
                episode=episode,
                target_language=self.project_json.get("source_language") or "中文",
            )
            # prompt_authoring 只产引用语法正文：unit_id / 时长机械沿用 script_plan，参考图执行期从正文派生，
            # 不进 LLM 输出——没让模型写的字段就没有漂移可校验，故此处无需按能力收窄的动态 schema。
            schema: type = ReferencePromptAuthoringFlatScript
        else:
            # narration 两段式：script_plan 透传内容层（novel_text 等），prompt_authoring 仅产视觉层、按 segment_id 合并回 script_plan。
            # drama 已在前面经 _generate_drama_prompt_authoring 早返回；reference 走上面分支，故此 else 必为 narration。
            narration_script_plan = self._load_narration_script_plan(episode, supported_durations)
            authoring_scope, saved_path = await self._scope_or_save_without_rewrite(
                episode,
                filename,
                plan_kind="narration",
                plan_entries=narration_script_plan,
                scope=scope,
                rewritten_entry_ids=rewritten_entry_ids,
                title=None,
                cancellation_file_receipts=cancellation_file_receipts,
                cancellation_manifest_receipts=cancellation_manifest_receipts,
            )
            if saved_path is not None:
                return saved_path
            narration_script_plan = authoring_scope.entries_to_rewrite
            prompt = build_narration_prompt(
                project_overview=self.project_json.get("overview", {}),
                style=self.project_json.get("style", ""),
                style_description=self.project_json.get("style_description", ""),
                characters=characters,
                scenes=scenes,
                props=props,
                script_plan_segments=narration_script_plan,
                aspect_ratio=self._resolve_aspect_ratio(),
                episode=episode,
                # 输出语言与 script_plan 同取项目 source_language，避免非中文项目 script_plan 透传内容与 prompt_authoring 视觉割裂（同 drama）
                target_language=self.project_json.get("source_language") or "中文",
            )
            # prompt_authoring 只产视觉层（image_prompt/video_prompt），按 segment_id 对齐 script_plan 合并；
            # novel_text/时长/break 由 script_plan 透传，不进 LLM 输出，从工程上根除扩写漂移。
            schema = NarrationVisualEpisodeScript

        # unit 时长的单一真相是 script_plan 完成内容确认时的值：schema 只把 duration_seconds 枚举约束到
        # supported_durations 成员，不会把它钉死在某个具体 unit 已确认的档位上，LLM 因而能在
        # 合法档位间自由改写——按 unit_id 机械传回 script_plan 确认值，杜绝该字段被 prompt_authoring 静默漂移。
        #
        # 这里只传未取档的原始确认值：取档按哪套档位算取决于「这个 unit 最终是否带参考图」，
        # 而正文里的 `@[名称]` 由 LLM 在 prompt_authoring 输出时决定、可能与 script_plan 的不同。取档统一放在
        # _add_metadata，按落地后的最终正文逐 unit 重算。
        prompt = append_user_instructions(prompt, instructions)

        reference_unit_durations = None
        if script_plan_units is not None:
            assert authoring_scope is not None  # reference 路径必已解析重写范围
            self._assert_reference_script_plan_ready(script_plan_units, caps=caps, gen_mode=gen_mode)
            # 只对本次重写的 unit 施加「时长回传 script_plan 确认值」与取档校验：未重写的 unit
            # 不经 LLM，没有可漂移的输出，重复判它只会让一次与本轮无关的档位变化阻断生成。
            script_plan_units = authoring_scope.entries_to_rewrite
            reference_unit_durations = {
                str(rewrite_episode_prefix(u["unit_id"], episode)): u["duration_seconds"] for u in script_plan_units
            }

        return await self._generate_and_save(
            prompt,
            schema,
            episode,
            output_filename,
            authoring_scope=authoring_scope,
            narration_script_plan=narration_script_plan,
            reference_script_plan=script_plan_units,
            reference_max_refs=self._resolve_max_refs(caps) if script_plan_units is not None else None,
            reference_unit_durations=reference_unit_durations,
            caps=caps if script_plan_units is not None else None,
            before_quarantine_commit=before_quarantine_commit,
            cancellation_file_receipts=cancellation_file_receipts,
            cancellation_manifest_receipts=cancellation_manifest_receipts,
        )

    async def _generate_drama_prompt_authoring(
        self,
        episode: int,
        output_filename: str | None,
        *,
        gen_mode: str | None,
        instructions: str | None = None,
        scope: str | Iterable[str] | None = None,
        rewritten_entry_ids: list[str] | None = None,
        cancellation_file_receipts: list[FormalWriteReceipt] | None = None,
        cancellation_manifest_receipts: list[ArtifactEntryRekeyReceipt] | None = None,
    ) -> Path:
        """drama 两段式 prompt_authoring：读 script_plan 结构化内容 → LLM 仅出视觉层 → 按 scene_id 合并 → 落盘。

        非视觉字段（utterances / source_text / characters_in_scene / 时长 / 边界）一律取自 script_plan 内容、
        不进 LLM 输出（工程透传，杜绝 Structured Outputs 漂移）；视觉层缺覆盖 / 悬空 scene_id 由
        ``merge_drama_visual_into_scenes`` fail-loud。

        增量合并：只有 ``scope`` 选中的分镜进 prompt 与 LLM 输出，其余分镜连同视觉层、``note``、
        ``end_frame_image``、``generated_assets`` 从旧剧本原样沿用，装配顺序取脚本规划。
        """
        assert self.generator is not None  # generate() 入口已检查
        content = self._load_drama_script_plan_content(episode)
        raw_scenes = content.get("scenes")
        content_scenes: list = raw_scenes if isinstance(raw_scenes, list) else []
        for scene in content_scenes:
            require_script_unit_admitted("scenes", scene)
        await self._assert_drama_script_plan_durations(content_scenes, episode=episode, gen_mode=gen_mode)
        filename = output_filename or episode_script_filename(episode)
        title = content.get("title") if isinstance(content.get("title"), str) else None
        authoring_scope, saved_path = await self._scope_or_save_without_rewrite(
            episode,
            filename,
            plan_kind="drama",
            plan_entries=content_scenes,
            scope=scope,
            rewritten_entry_ids=rewritten_entry_ids,
            title=title,
            cancellation_file_receipts=cancellation_file_receipts,
            cancellation_manifest_receipts=cancellation_manifest_receipts,
        )
        if saved_path is not None:
            return saved_path
        formal_baseline = await asyncio.to_thread(content_fingerprint, self.project_path / "scripts" / filename)

        logger.info(
            "正在生成第 %d 集剧本（drama prompt_authoring 视觉层，重写 %d/%d 个分镜）...",
            episode,
            len(authoring_scope.entries_to_rewrite),
            len(authoring_scope.plan_revisions),
        )
        result = await self._generate_text(
            TextGenerationRequest(
                prompt=append_user_instructions(
                    self._build_drama_prompt_authoring_prompt(authoring_scope.entries_to_rewrite, episode),
                    instructions,
                ),
                response_schema=DramaVisualScript,
                max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
            )
        )

        visual_scenes = self._parse_drama_visual(result.text)
        merged_scenes = merge_drama_visual_into_scenes(authoring_scope.entries_to_rewrite, visual_scenes)

        script_data = {"title": title or f"第{episode}集", "scenes": merged_scenes}
        script_data = self._add_metadata(script_data, episode)
        script_data = self._assemble_script(script_data, authoring_scope)

        pm = ProjectManager(str(self.project_path.parent))
        output_path = pm.save_script(
            self.project_path.name,
            script_data,
            filename,
            validate=True,
            artifact_basis=self._artifact_basis,
            expected_fingerprint=formal_baseline,
            cancellation_file_receipts=cancellation_file_receipts,
            cancellation_manifest_receipts=cancellation_manifest_receipts,
        )

        self._quality_probe(script_data, episode)
        logger.info("剧本已保存至 %s", output_path)
        return output_path

    async def _assert_drama_script_plan_durations(
        self, content_scenes: list, *, episode: int, gen_mode: str | None
    ) -> None:
        """校验 drama script_plan 已定分镜时长在当前能力集合内，越界 fail-loud。

        与 narration（``_load_narration_script_plan``）、reference_video（``_load_reference_script_plan``）
        对称：drama 的时长同样由 script_plan 定稿、prompt_authoring 只出视觉层并原样透传，而落盘前的静态校验只
        要求正整数。缺这道校验时，script_plan 在某个分辨率下拆好、随后项目切到约束更严的分辨率再跑
        prompt_authoring，越界时长会一路存进剧本，直到视频入队才被拒。

        取值按**最终 schema 的归一化口径**（``TypeAdapter(int)``，即 ``DramaSceneContent``
        的非 strict ``int`` 字段所用的那套）而非 ``isinstance(..., int)``：后者会把 ``"4"``
        与 ``4.0`` 整个跳过，而它们会被归一成 4 存进剧本，等于给越界值开了一条绕路。缺键与显式
        ``null`` 同取该字段的声明默认值——不填不代表不校验，落盘时补的正是这个默认值。归一化
        失败（如 ``"abc"``）不在此报错，交给落盘前的静态校验统一 fail-loud。
        """
        supported = self._resolve_supported_durations(await self._fetch_video_capabilities(), gen_mode=gen_mode)
        allowed = {int(d) for d in supported}
        seen: set[int] = set()
        for scene in content_scenes:
            if not isinstance(scene, dict):
                continue
            raw = scene.get("duration_seconds")
            try:
                seen.add(_DURATION_ADAPTER.validate_python(_DRAMA_DEFAULT_DURATION if raw is None else raw))
            except ValidationError:
                continue
        bad = sorted(seen - allowed)
        if bad:
            raise ValueError(
                f"script_plan 已定分镜时长非法（不在 {sorted(allowed)} 内）: {bad}；"
                "当前分辨率与型号下这些时长不可用，请调用 generate_script_plan 按当前能力规范化"
            )

    def _build_drama_prompt_authoring_prompt(self, content_scenes: list, episode: int) -> str:
        """构建 drama prompt_authoring（视觉层）prompt：把 script_plan 内容渲染为输入，仅求 image_prompt / video_prompt。"""
        characters = self.project_json.get("characters")
        characters = characters if isinstance(characters, dict) else {}
        scenes = self.project_json.get("scenes")
        scenes = scenes if isinstance(scenes, dict) else {}
        props = self.project_json.get("props")
        props = props if isinstance(props, dict) else {}
        return build_drama_prompt(
            project_overview=self.project_json.get("overview", {}),
            style=self.project_json.get("style", ""),
            style_description=self.project_json.get("style_description", ""),
            scenes_content=render_drama_content_for_prompt_authoring(content_scenes),
            episode=episode,
            aspect_ratio=self._resolve_aspect_ratio(),
            # 输出语言与 script_plan（normalize）同取项目 source_language，避免非中文项目 script_plan 内容与 prompt_authoring 视觉割裂
            target_language=self.project_json.get("source_language") or "中文",
            characters=characters,
            scenes=scenes,
            props=props,
        )

    def _parse_drama_visual(self, response_text: str) -> list[dict]:
        """解析 prompt_authoring 视觉层 LLM 响应为 scene 视觉 dict 列表（scene_id + image_prompt + video_prompt）。

        校验失败时降级取原始 scenes，由后续 ``merge_drama_visual_into_scenes`` 按覆盖/对齐 fail-loud。
        """
        text = strip_json_code_fences(response_text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"prompt_authoring 视觉层 JSON 解析失败: {e}") from e
        try:
            validated = DramaVisualScript.model_validate(data)
            return [s.model_dump() for s in validated.scenes]
        except ValidationError as e:
            logger.warning("prompt_authoring 视觉层校验警告: %s", e)
            raw = data.get("scenes") if isinstance(data, dict) else None
            return raw if isinstance(raw, list) else []

    async def _generate_and_save(
        self,
        prompt: str,
        schema: type,
        episode: int,
        output_filename: str | None,
        *,
        authoring_scope: PromptAuthoringScope | None = None,
        narration_script_plan: list[dict] | None = None,
        reference_script_plan: list[dict] | None = None,
        reference_max_refs: int | None = None,
        reference_unit_durations: dict[str, int] | None = None,
        caps: dict | None = None,
        before_quarantine_commit: Callable[[], None] | None = None,
        cancellation_file_receipts: list[FormalWriteReceipt] | None = None,
        cancellation_manifest_receipts: list[ArtifactEntryRekeyReceipt] | None = None,
    ) -> Path:
        """调用 TextBackend → 解析校验 → 补元数据 → 经写盘统一入口保存（各创作类型共用尾段）。

        ``narration_script_plan`` 非 None 时走两段式合并：LLM 输出视觉层，按 segment_id 合并回
        script_plan 已定结构（novel_text 等透传）；``reference_script_plan`` 非 None 时走参考路径的保结构
        合并（LLM 只出引用语法正文，见 ``_merge_reference_visual``）；两者皆 None 时走单段解析
        （drama/ad）。``reference_unit_durations`` 非 None 时（reference_video 路径）按 unit_id
        机械覆盖 ``duration_seconds``（取档用最终输出正文的提及状态重算，见 ``_add_metadata``）；
        ``caps`` 可一并传入，为 None 时 ``_add_metadata`` 仍按 caps → registry 两级回退解析每个
        unit 的生效档位，不会因此跳过取档校验。

        ``authoring_scope`` 非 None 时（narration / reference 两段式）本次只重写它选中的条目，
        补完元数据后与旧剧本里未变的条目按脚本规划顺序装配回整份剧本；ad 路径为 None，整份
        产出即最终剧本。
        """
        assert self.generator is not None  # generate() 入口已检查
        filename = output_filename or episode_script_filename(episode)
        formal_baseline = await asyncio.to_thread(content_fingerprint, self.project_path / "scripts" / filename)
        prompt_authoring_draft_baseline = (
            await asyncio.to_thread(self._reference_prompt_authoring_draft_revision, episode)
            if reference_script_plan is not None
            else None
        )
        if prompt_authoring_draft_baseline is not None:
            raise DraftViolation(
                "reference prompt_authoring 草稿待处置；正式生成已中止，请先晋升或丢弃现有草稿",
                code="draft_revision_conflict",
            )
        # 调用 TextBackend
        logger.info("正在生成第 %d 集剧本...", episode)
        result = await self._generate_text(
            TextGenerationRequest(
                prompt=prompt,
                response_schema=schema,
                max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
            )
        )
        response_text = result.text

        # 解析并验证响应
        if narration_script_plan is not None:
            visual_data = self._parse_narration_visual(response_text, episode)
            script_data = self._merge_narration_visual(narration_script_plan, visual_data, episode)
        elif reference_script_plan is not None:
            # 违约不丢弃：把这次已付费的展开连同逐条报告落待修复草稿，由 Agent 修复后经
            # promote_reference_prompt_authoring_draft 重判晋升。重抽既烧钱又不收敛——同一个模型对同一份
            # script_plan 大概率再犯同一类错。
            try:
                script_data = self._merge_reference_visual(
                    reference_script_plan, response_text, episode, max_refs=reference_max_refs
                )
            except DraftViolation as exc:
                raise await run_sync_transaction(
                    self._quarantine_reference_prompt_authoring,
                    episode,
                    response_text,
                    exc,
                    base_fingerprint=formal_baseline,
                    expected_draft_revision=prompt_authoring_draft_baseline,
                    before_commit=before_quarantine_commit,
                ) from exc
        else:
            script_data = (
                self._parse_ad_reference_response(response_text, episode)
                if self.content_mode == "ad" and self.generation_mode == "reference_video"
                else self._parse_response(response_text, episode)
            )

        # 补充元数据。reference 路径同样走草稿保护：_add_metadata 按落地后的最终正文重算
        # 生效档位，一个新增 / 去掉了 `@` 引用的 unit 要到合并之后才判出档——不接住的话，这份
        # 已付费产出只存在于内存里，错误却让调用方重新生成。
        try:
            script_data = self._add_metadata(
                script_data, episode, reference_unit_durations=reference_unit_durations, caps=caps
            )
        except DraftViolation as exc:
            if reference_script_plan is None:
                raise
            raise await run_sync_transaction(
                self._quarantine_reference_prompt_authoring,
                episode,
                response_text,
                exc,
                base_fingerprint=formal_baseline,
                expected_draft_revision=prompt_authoring_draft_baseline,
                before_commit=before_quarantine_commit,
            ) from exc

        # 装配：本次重写的条目与旧剧本里未变的条目按脚本规划顺序合并成整份剧本。放在
        # _add_metadata 之后——元数据重算（集号前缀改写、时长回传、needs_replan 重判）只该
        # 作用于本轮产出，未变条目原样过路才谈得上逐字节不变。
        if authoring_scope is not None:
            script_data = self._assemble_script(script_data, authoring_scope)

        # 经写盘统一入口保存：整集生成无「改前」，按严格结构校验（等价原 response_schema 的
        # Pydantic 校验），并继承 metadata 重算、加锁、filename↔episode 一致性与 project.json
        # 同步——消除「裸 json.dump 旁路」，使 _write_script_unlocked 成为剧本唯一写入点。
        pm = ProjectManager(str(self.project_path.parent))
        try:
            if reference_script_plan is not None:
                output_path = await run_sync_transaction(
                    self._save_reference_prompt_authoring_if_draft_unchanged,
                    episode,
                    prompt_authoring_draft_baseline,
                    script_data,
                    filename,
                    formal_baseline,
                    cancellation_file_receipts,
                    cancellation_manifest_receipts,
                )
            else:
                output_path = await run_sync_transaction(
                    pm.save_script,
                    self.project_path.name,
                    script_data,
                    filename,
                    validate=True,
                    artifact_basis=self._artifact_basis,
                    expected_fingerprint=formal_baseline,
                    cancellation_file_receipts=cancellation_file_receipts,
                    cancellation_manifest_receipts=cancellation_manifest_receipts,
                )
        except ScriptWriteConflict as exc:
            if reference_script_plan is None:
                raise
            raise await run_sync_transaction(
                self._quarantine_reference_prompt_authoring,
                episode,
                response_text,
                DraftViolation(
                    "正式剧本在模型生成期间已变化；本次生成结果已保留为 prompt_authoring 草稿，请合并最新正式内容后再晋升",
                    code="formal_revision_conflict",
                ),
                base_fingerprint=formal_baseline,
                expected_draft_revision=prompt_authoring_draft_baseline,
                before_commit=before_quarantine_commit,
            ) from exc

        self._quality_probe(script_data, episode)

        logger.info("剧本已保存至 %s", output_path)
        return output_path

    async def _compose_ad(self, episode: int, gen_mode: str | None) -> tuple[str, type]:
        """ad 分支的 (prompt, response_schema) 构造，generate/build_prompt 共用。

        reference 路径不消费供应商能力（unit 编排时长不按供应商档位量化），跳过能力查询；
        storyboard 路径解析一次 supported_durations，prompt 时长枚举与 schema enum 同源。
        """
        if gen_mode == "reference_video":
            supported = None
            schema: type = AdReferenceFlatScript
        else:
            caps = await self._fetch_video_capabilities()
            supported = self._resolve_supported_durations(caps, gen_mode=gen_mode)
            schema = build_episode_script_model("ad", supported)
        return self._build_ad_prompt(episode, gen_mode, supported), schema

    def _build_ad_prompt(self, episode: int, gen_mode: str | None, supported: list[int] | None) -> str:
        """构建广告/短片 prompt：brief + 商品信息 + 审定配比表，不读 script_plan 中间文件。

        storyboard 路径把 supported_durations 作为单分镜时长枚举写进 prompt；参考生视频
        直接输出统一引用语法 video unit，八段式只作为内容规划而不持久化。
        """
        direct_inputs = project_ad_episode_script_inputs(episode, project=self.project_json)
        common: dict[str, Any] = {
            "project_overview": cast(dict[str, Any], direct_inputs["overview"]),
            "style": direct_inputs["style"],
            "style_description": direct_inputs["style_description"],
            "characters": cast(dict[str, Any], direct_inputs["characters"]),
            "scenes": cast(dict[str, Any], direct_inputs["scenes"]),
            "props": cast(dict[str, Any], direct_inputs["props"]),
            "products": cast(dict[str, Any], direct_inputs["products"]),
            "brief": direct_inputs["brief"],
            "target_duration": direct_inputs["target_duration"],
            "episode": direct_inputs["episode"],
            "aspect_ratio": direct_inputs["aspect_ratio"],
            "target_language": direct_inputs["target_language"],
        }
        if gen_mode == "reference_video":
            return build_ad_reference_prompt(**common)
        return build_ad_prompt(
            **common,
            generation_mode=gen_mode,
            supported_durations=supported,
            speech_rate_override=cast(float | None, direct_inputs["speech_rate_override"]),
        )

    async def build_prompt(
        self, episode: int, *, instructions: str | None = None, scope: str | Iterable[str] | None = None
    ) -> str:
        """
        构建 Prompt（用于 dry-run 模式）

        与 `generate()` 同样先 await `_fetch_video_capabilities()` 解析 caps；
        这样当 `project.json` 不显式声明 `video_backend`（用户依赖全局/系统默认时）也能
        正确派生 supported_durations。caps 失败仍 fallback 到 project.json 自身的 sync 链。
        ``instructions`` 的注入口径与 `generate()` 一致（中性「附加指令」分节追加末尾）。

        ``scope`` 的口径与 `generate()` 同一份：dry-run 要回答的是「这次运行会发出什么」，
        渲染整份脚本规划而实际只重写失效条目，会把一次增量重写说成整集重写。没有条目要重写时
        本方法回答那句事实，而不是渲染一份不会被发出的空 prompt。
        """
        gen_mode = self.generation_mode

        # 见 generate() 同位置说明：ad 先于 generation_mode 分派，且不读 script_plan。
        if self.content_mode == "ad":
            prompt, _schema = await self._compose_ad(episode, gen_mode)
            return append_user_instructions(prompt, instructions)

        # 剧情演绎的分镜图生视频（含宫格装配）dry-run 走 prompt_authoring 视觉层 prompt：读 script_plan 结构化内容并渲染
        # （见 generate() 的两段式说明）。reference_video / narration 不入此分支。
        if gen_mode != "reference_video" and self.content_mode != "narration":
            content = self._load_drama_script_plan_content(episode)
            raw_scenes = content.get("scenes")
            content_scenes: list = raw_scenes if isinstance(raw_scenes, list) else []
            drama_entries = self._dry_run_entries_to_rewrite(
                episode, plan_kind="drama", plan_entries=content_scenes, scope=scope
            )
            if drama_entries is None:
                return _NO_ENTRY_TO_REWRITE_NOTE
            return append_user_instructions(
                self._build_drama_prompt_authoring_prompt(drama_entries, episode), instructions
            )

        caps = await self._fetch_video_capabilities()
        characters = self.project_json.get("characters")
        characters = characters if isinstance(characters, dict) else {}
        scenes = self.project_json.get("scenes")
        scenes = scenes if isinstance(scenes, dict) else {}
        props = self.project_json.get("props")
        props = props if isinstance(props, dict) else {}

        if gen_mode == "reference_video":
            # unit 时长按全集校验（见 generate() 同位置说明）；prompt_authoring 不产出时长，prompt
            # 只需参考图上限。
            script_plan_units = await run_sync_transaction(
                self._load_reference_script_plan,
                episode,
                self._resolve_raw_supported_durations(caps),
            )
            entries_to_rewrite = self._dry_run_entries_to_rewrite(
                episode, plan_kind="reference_video", plan_entries=script_plan_units, scope=scope
            )
            if entries_to_rewrite is None:
                return _NO_ENTRY_TO_REWRITE_NOTE
            script_plan_units = entries_to_rewrite
            prompt = build_reference_video_prompt(
                project_overview=self.project_json.get("overview", {}),
                style=self.project_json.get("style", ""),
                style_description=self.project_json.get("style_description", ""),
                characters=characters,
                scenes=scenes,
                props=props,
                script_plan_units=script_plan_units,
                max_refs=self._resolve_max_refs(caps),
                aspect_ratio=self._resolve_aspect_ratio(),
                episode=episode,
                target_language=self.project_json.get("source_language") or "中文",
            )
            return append_user_instructions(prompt, instructions)
        # narration 两段式：script_plan 透传内容层（novel_text 等），prompt_authoring 仅产视觉层。
        # drama / ad 已在前面早返回，reference 走上面分支，故此处必为 narration。
        narration_entries = self._dry_run_entries_to_rewrite(
            episode,
            plan_kind="narration",
            plan_entries=self._load_narration_script_plan(
                episode, self._resolve_supported_durations(caps, gen_mode=gen_mode)
            ),
            scope=scope,
        )
        if narration_entries is None:
            return _NO_ENTRY_TO_REWRITE_NOTE
        prompt = build_narration_prompt(
            project_overview=self.project_json.get("overview", {}),
            style=self.project_json.get("style", ""),
            style_description=self.project_json.get("style_description", ""),
            characters=characters,
            scenes=scenes,
            props=props,
            script_plan_segments=narration_entries,
            aspect_ratio=self._resolve_aspect_ratio(),
            episode=episode,
            target_language=self.project_json.get("source_language") or "中文",
        )
        return append_user_instructions(prompt, instructions)

    async def _fetch_video_capabilities(self) -> dict | None:
        """从 ConfigResolver 解析视频模型能力；失败时返 None，由 _resolve_* fallback 到 project.json 直读。

        使用 `video_capabilities_for_project` 传入已加载的 project.json，不再按 `self.project_path.name`
        重新全局加载——避免 ScriptGenerator 在非标准路径（如测试 tmp_path）实例化时目录名与
        全局项目碰撞读到错误能力。定桶按项目 ``generation_mode``，与 ``_resolve_supported_durations``
        收窄所用的 ``gen_mode`` 同口径。

        宽松捕获：除 ValueError 外，DB 未 migration / 连接失败等 SQLAlchemy 异常也走 fallback，
        保证在缺能力元数据的环境（如裸 CI 测试容器）中 generate() 仍能跑通。

        任务类型桶解析闸的报错例外，原样上抛：那是配置指向的模型缺该桶所需能力或引用已失效
        （``docs/adr/0054``），fallback 会拿项目默认模型的档位去写剧本，写出来的时长 / 参考图
        数量执行期照样被拒。报错带 code 与修复指引，比先写一份必败的剧本更省事。
        """
        resolver = self.config_resolver or ConfigResolver(async_session_factory)
        try:
            return await resolver.video_capabilities_for_project(self.project_json)
        except VideoBucketCapabilityError:
            raise
        except (ValueError, SQLAlchemyError) as exc:
            logger.info("video_capabilities 解析失败，将走 project.json fallback：%s", exc)
            return None

    def _resolve_backend_ids(self, caps: dict | None) -> tuple[str | None, str | None]:
        """当前视频模型身份：caps → project.json 自报身份；都拿不到为 (None, None)。

        联动约束按型号声明查，故身份要与时长的来源同一个模型：caps 在手时以它为准
        （后端留空走全局默认、或存值已不在注册表被 resolver 回退时，实际生效的是 caps 里的），
        否则退到 project.json 按 generation_mode 定桶取的身份（``project_video_backend_ids``，
        与时长的 fallback 链同一层）。
        """
        if caps and caps.get("provider_id") and caps.get("model"):
            return str(caps["provider_id"]), str(caps["model"])
        ids = project_video_backend_ids(self.project_json)
        return ids if ids is not None else (None, None)

    def _resolve_supported_durations(
        self, caps: dict | None = None, *, gen_mode: str | None, uses_reference_images: bool | None = None
    ) -> list[int]:
        """从 caps → registry 两级解析，再按联动约束收窄；都拿不到抛 ValueError。

        收窄发生在交给 prompt / 动态 schema 之前：``supported_durations`` 是型号的时长全集，
        不含「分辨率↔时长」「参考图↔时长」两条联动约束。不收窄的话 Veo 项目（兜底分辨率即
        1080p）的剧本会产出 4/6 秒分镜，到视频入队时才被 backend 拒，用户已无统一纠正入口。

        ``uses_reference_images`` 由调用方按本集 script_plan 的实际引用情况传入；缺省退回按生成模式
        判定（见 ``constrain_durations_for_project``）。
        """
        raw = self._resolve_raw_supported_durations(caps)
        provider_id, model_id = self._resolve_backend_ids(caps)
        return constrain_durations_for_project(
            self.project_json,
            raw,
            provider_id=provider_id,
            model_id=model_id,
            generation_mode=gen_mode,
            uses_reference_images=uses_reference_images,
        )

    def _unit_duration_off_every_tier(
        self, duration: int, *, caps: dict | None, gen_mode: str | None
    ) -> list[int] | None:
        """时长在带图与不带图两种档位下都出局时返回带图档位，任一合法则返回 None。

        prompt_authoring 可以给 unit 增删 `@[名称]` 提及，只在其中一种状态下出局的时长仍可能落地合法——
        提前判死会拦掉本会成功的生成。两种状态都出局才是与参考图无关的必然失败
        （模型或分辨率配置变化所致），可以在付费调用前拦下。
        """
        for has_references in (True, False):
            tiers = self._unit_duration_off_tier(duration, has_references=has_references, caps=caps, gen_mode=gen_mode)
            if tiers is None:
                return None
        return self._resolve_supported_durations(caps, gen_mode=gen_mode, uses_reference_images=True)

    def _unit_duration_off_tier(
        self, duration: int, *, has_references: bool, caps: dict | None, gen_mode: str | None
    ) -> list[int] | None:
        """时长落在该 unit 生效档位之外时返回该档位集，落在内则返回 None。

        生效档位逐 unit 算：分辨率与参考图两条联动约束都只对实际带图的 unit 生效，整集一刀切
        会收掉无引用 unit 本可申请的档位。档位不可解析时按无约束处理，交执行期 backend 兜底。
        """
        tiers = self._resolve_supported_durations(caps, gen_mode=gen_mode, uses_reference_images=has_references)
        if not tiers:
            return None
        return None if resolve_duration_slot(duration, tiers).seconds == duration else tiers

    def _resolve_raw_supported_durations(self, caps: dict | None) -> list[int]:
        """收窄前的时长全集：委托共享解析器，取不到时抛 ValueError。

        本路径的下游是 prompt 与动态枚举 schema，缺档位就无从生成，故把解析器的 None 提升为
        异常；其余入口（内容确认 / 归档导入）对 None 的处置是退回结构 clamp，不共用这道提升。
        """
        durations = resolve_raw_supported_durations(self.project_json, caps)
        if durations is None:
            raise ValueError(
                f"supported_durations 无法解析：caps={bool(caps)}, "
                f"video_backend={self.project_json.get('video_backend')!r}；请确保 model 配置完整"
            )
        return durations

    def _resolve_max_duration(
        self, caps: dict | None = None, *, gen_mode: str | None, uses_reference_images: bool | None = None
    ) -> int | None:
        """单次视频生成最长秒数；派生自 max(收窄后的 supported_durations)。

        取收窄后的集合而非 caps 自带的 ``max_duration``：该值是全集最大值，参考生视频下
        它是 unit 总时长上限，若不随联动约束收窄，script_plan 会拆出总时长超标的 unit，prompt_authoring 的
        枚举 schema 再把它判非法——上限与枚举必须描述同一个收窄后的集合。
        """
        try:
            durations = self._resolve_supported_durations(
                caps, gen_mode=gen_mode, uses_reference_images=uses_reference_images
            )
        except ValueError:
            return None
        return max(durations)

    def _resolve_aspect_ratio(self) -> str:
        """解析项目的 aspect_ratio，向后兼容。narration / ad 默认竖屏（ad 与创建向导默认一致）。"""
        if "aspect_ratio" in self.project_json and isinstance(self.project_json["aspect_ratio"], str):
            return self.project_json["aspect_ratio"]
        return "9:16" if self.content_mode in ("narration", "ad") else "16:9"

    def _resolve_max_refs(self, caps: dict | None = None) -> int | None:
        """解析当前视频模型的最大参考图数；caps → project.json 自报身份 → registry 两级回退。

        语义约定：仅 None 视为「未声明上限」（上层不在 prompt 写硬性数量约束，且 executor 跳过裁剪）；
        caps 来源的 0 是显式上限（如不接受参考图的 endpoint），会原样下传触发裁剪为 0 张。
        caps 解析失败（DB/migration 故障等）时退到 project.json 按 generation_mode 定桶取的身份
        （``project_video_backend_ids``）直查 backend 声明——与 _resolve_supported_durations
        同构，避免丢失上限导致后端按多张参考图发出而被上游拒。
        上限的唯一声明处是 backend（执行期构造请求的一方），registry ModelInfo 不声明该值。
        注册表身份仍要查——backend 的 caps 函数不都校验 model 存在性与
        media_type，对任意 id 返回静态能力。0 在这条降级路径上按未声明处理（下传 0 会把降级前
        本可申请的参考图整批裁掉，而执行期仍有 backend 校验兜底）。
        """
        if caps:
            cached = caps.get("max_reference_images")
            if cached is not None:
                return int(cached)
        ids = project_video_backend_ids(self.project_json)
        if ids is not None:
            provider_id, model_id = ids
            provider_meta = PROVIDER_REGISTRY.get(provider_id)
            model_info = provider_meta.models.get(model_id) if provider_meta else None
            if model_info is not None and model_info.media_type == "video":
                try:
                    backend_caps = builtin_video_capabilities_for_model(provider_id, model_id)
                except ValueError:
                    return None
                if backend_caps.max_reference_images:
                    return int(backend_caps.max_reference_images)
        return None

    def _load_project_json(self) -> dict:
        """加载 project.json"""
        path = self.project_path / "project.json"
        if not path.exists():
            raise FileNotFoundError(f"未找到 project.json: {path}")

        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def _freeze_script_plan_artifact_basis(self, script_plan_content: object) -> None:
        """Freeze the authoritative script_plan basis before the provider call."""
        basis = build_episode_script_basis(script_plan_content, project=self.project_json)
        self._artifact_basis = ArtifactBasisDescriptor.from_basis(basis)

    def _freeze_script_plan_input_claim(self, episode: int, script_plan_path: Path, *, content_digest: str) -> None:
        """Bind parsed script_plan bytes to their formal identity for provider admission."""

        artifact_path = script_plan_path.relative_to(self.project_path).as_posix()
        claim = resolve_usable_artifact_input_claim(
            resolver=active_artifact_currency_resolver(self.project_path, self.project_json),
            key=ArtifactKey.episode_script_plan(episode),
            artifact_path=artifact_path,
            content_digest=content_digest,
        )
        if claim is None:
            raise ValueError(f"formal script_plan artifact is not registered: {artifact_path}")
        self._script_plan_input_claim = claim

    async def _generate_text(self, request: TextGenerationRequest) -> Any:
        """Call the paid text provider after one shared formal-input recheck."""

        assert self.generator is not None  # generate() 入口已检查
        if self._script_plan_input_claim is not None:
            await asyncio.to_thread(
                assert_current_artifact_input_claims_usable,
                self.project_path,
                (self._script_plan_input_claim,),
            )
        return await self.generator.generate(request, project_name=self.project_path.name)

    def _freeze_ad_artifact_basis(self, episode: int) -> None:
        """Freeze the ad-specific canonical basis before the provider call."""
        basis = build_ad_episode_script_basis(episode, project=self.project_json)
        self._artifact_basis = ArtifactBasisDescriptor.from_basis(basis)

    def _load_script_plan(self, episode: int) -> str:
        """加载 drama 形状两段式的脚本规划结构化中间文件原始文本。

        每种模式只对应一个期望文件，缺失时显式报错并指明期望路径——不降级改读
        其他模式的中间文件（静默 fallback 会让剧本基于错误模式的中间产物生成）。
        本方法只服务 drama 形状的两段式结构化内容；narration 另经 ``_load_narration_script_plan``、
        reference_video 另经 ``_load_reference_script_plan``。
        """
        drafts_path = episode_drafts_dir(self.project_path, episode)
        # 按 content_mode 取登记的结构化文件名，脏值兜底 drama。
        script_plan_path = drafts_path / SCRIPT_PLAN_FILENAMES.get(self.content_mode, SCRIPT_PLAN_FILENAMES["drama"])

        if not script_plan_path.exists():
            raise FileNotFoundError(
                f"未找到脚本规划中间文件: {script_plan_path}；content_mode={self.content_mode} 期望该文件，请先完成本集脚本规划"
            )

        raw = script_plan_path.read_bytes()
        text = raw.decode("utf-8")
        self._freeze_script_plan_input_claim(episode, script_plan_path, content_digest=hashlib.sha256(raw).hexdigest())
        return text

    def _load_reference_script_plan(
        self,
        episode: int,
        supported_durations: list[int],
        *,
        _prompt_authoring_lock_held: bool = False,
    ) -> list[dict]:
        """加载并校验 reference_video script_plan 结构化中间文件 ``script_plan_reference_units.json``。

        返回 unit dict 列表（unit_id / text / duration_seconds），供 prompt_authoring prompt 渲染
        （``render_reference_units_for_prompt_authoring``）作唯一基底——prompt_authoring 不解析自由文本。
        校验：结构合法（``ReferenceScriptPlanDraft``）、units 非空、unit_id 唯一、
        unit ``duration_seconds`` ∈ ``supported_durations``（与拆分工具的 response_schema 同口径，
        防手工编辑漂移出非法时长）。仅存在结构化前的旧 ``script_plan_reference_units.md`` 时给
        明确的「重跑拆分」报错——不写 md→json 迁移器（旧 md 产于结构化中间态引入前，
        与 narration 同决策）。
        """
        drafts_path = episode_drafts_dir(self.project_path, episode)
        script_plan_json = drafts_path / REFERENCE_VIDEO_SCRIPT_PLAN_FILENAME
        # 待修复草稿在场时不生成：正式文件此刻仍是上一版（或不存在），拿它跑 prompt_authoring 等于把一份
        # 待处置的违约产出静默换成旧内容。内容确认已在工具入口按同一判据阻塞；脚本、测试等
        # 直连调用会绕过工具入口，因此在此重复守卫。
        quarantine = quarantine_path(self.project_path, episode, QUARANTINE_KIND_SCRIPT_PLAN)
        if quarantine.exists():
            raise ValueError(
                f"第 {episode} 集有待修复草稿（{quarantine}），prompt_authoring 生成已中止；"
                f"请先修改该草稿并经 {PROMOTE_TOOL_NAME} 晋升为正式 script_plan"
            )
        if not script_plan_json.exists():
            legacy_md = drafts_path / REFERENCE_VIDEO_SCRIPT_PLAN_LEGACY_FILENAME
            if legacy_md.exists():
                raise FileNotFoundError(
                    f"仅找到结构化前的旧拆分表 {legacy_md}，未找到 {script_plan_json}；"
                    f"请调用 generate_script_plan 产出结构化 {REFERENCE_VIDEO_SCRIPT_PLAN_FILENAME}"
                )
            raise FileNotFoundError(
                f"未找到脚本规划中间文件: {script_plan_json}；generation_mode=reference_video 期望该文件，"
                "请先完成 video_unit 拆分"
            )

        pm = ProjectManager(str(self.project_path.parent))
        # 与 server.services.script_review / save_content 共享同一把 per-path 锁：
        # 迁移的读改写与 Web 端保存、重拆分写盘相互互斥。
        prompt_authoring_path = quarantine_path(self.project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING)
        prompt_authoring_lock = nullcontext() if _prompt_authoring_lock_held else pm.file_lock(prompt_authoring_path)
        with prompt_authoring_lock, pm.file_lock(script_plan_json):
            # 顺序不变量：内容确认的判定在更早的 prompt_authoring 工具入口完成，迁移在其后运行且可能
            # 改写时长。先记下迁移前的放行状态，供迁移后判断放行依据是否已失效。放行状态与
            # 草稿在同一临界区内读取，两者才描述同一时刻——锁外读则并发的保存/确认会让它
            # 描述另一份草稿的内容确认结果。
            gate_passed_before = not gate_blocks_prompt_authoring(
                self.project_path, pm.load_project(self.project_path.name), episode
            )
            try:
                raw = json.loads(script_plan_json.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                raise ValueError(f"script_plan_reference_units.json 解析失败: {e}") from e

            # 存量草稿的 per-shot 时长一次性收编到 unit 级并回写落盘（二次加载不再触发）。
            # 此处持有模型档位，收编结果直接取档，与下方的枚举校验对齐。
            migrated_project, migration_warnings = migrate_script_plan_draft_in_place(
                self.project_path,
                raw,
                episode=episode,
                update_project=lambda mutate: pm.update_project(self.project_path.name, mutate),
                supported_durations=supported_durations,
            )
            self._script_plan_revision = content_fingerprint_of_data(raw)
            self._freeze_script_plan_artifact_basis(raw)
            self._freeze_script_plan_input_claim(
                episode,
                script_plan_json,
                content_digest=sha256_file(script_plan_json),
            )

        # 迁移带 warnings 说明 clamp 改写了实际秒数，那是内容变更、内容确认随之失效。而放行
        # 依据是改写前的状态：不在此处补判，生成就会拿着用户从未过目的秒数走完付费的
        # prompt_authoring，落盘之后才在下次加载被拦下。
        if (
            migration_warnings
            and migrated_project is not None
            and gate_passed_before
            and gate_blocks_prompt_authoring(self.project_path, migrated_project, episode)
        ):
            raise ValueError(
                f"第 {episode} 集 script_plan 时长已按当前模型档位收编改写（"
                + "；".join(warning.render() for warning in migration_warnings)
                + "），改写后的内容尚未完成内容确认，prompt_authoring 生成已中止；"
                "请在 Web 端完成本集 script_plan 的内容确认后重新生成"
            )

        try:
            draft = ReferenceScriptPlanDraft.model_validate(raw)
        except ValidationError as e:
            raise ValueError(f"script_plan_reference_units.json 结构校验失败: {e}") from e

        units = [u.model_dump() for u in draft.units]
        if not units:
            raise ValueError("script_plan_reference_units.json units 为空")

        ids = [u["unit_id"] for u in units]
        dupes = sorted(uid for uid, count in Counter(ids).items() if count > 1)
        if dupes:
            raise ValueError(f"script_plan_reference_units.json unit_id 重复: {dupes}")

        # _add_metadata 落盘前会把 E\d+ 前缀改写成当前 episode：原始 id 互异但改写后可能相撞
        # （E1U01 与 E2U01 在 episode=2 都成 E2U01）。提前 fail-loud，杜绝重复 id 静默落盘。
        # 与 _load_narration_script_plan / _load_drama_script_plan_content 同口径。
        rewritten_ids = [str(rewrite_episode_prefix(uid, episode)) for uid in ids]
        rewritten_dupes = sorted(uid for uid, count in Counter(rewritten_ids).items() if count > 1)
        if rewritten_dupes:
            raise ValueError(
                f"script_plan_reference_units.json unit_id 改写到 episode={episode} 后重复: {rewritten_dupes}"
            )

        allowed = {int(d) for d in supported_durations}
        bad = sorted({u["duration_seconds"] for u in units if u["duration_seconds"] not in allowed})
        if bad:
            raise ValueError(f"script_plan_reference_units.json unit 时长非法（不在 {sorted(allowed)} 内）: {bad}")

        return units

    def _load_narration_script_plan(self, episode: int, supported_durations: list[int]) -> list[dict]:
        """加载并校验 narration script_plan 结构化中间文件 ``script_plan_segments.json``。

        返回逐字 ``novel_text``、时长、``segment_break`` 等内容字段的分镜列表（dict），
        供 prompt_authoring prompt 渲染与视觉层合并复用——novel_text 由此透传、不经 prompt_authoring 的 LLM 重出。
        校验：结构合法、segment_id 唯一、``duration_seconds`` ∈ ``supported_durations``
        （duration 约束由原 prompt_authoring schema enum 前移到 script_plan，因 prompt_authoring 不再产出该字段）。
        仅存在结构化前的旧 ``script_plan_segments.md`` 时给明确的「重跑拆分」报错——不写
        md→json 迁移器（旧 md 产于结构化中间态引入前、不含手工编辑）。
        """
        # 草稿在场时不生成：正式文件此刻仍是上一版（或不存在），拿它跑 prompt_authoring 等于把一份
        # 待处置的产出静默换成旧内容。内容确认已在工具入口按同一判据阻塞，这里是直连调用
        # （脚本 / 测试 / 未来的其它入口）的兜底，与另两条路线同口径。
        quarantine = quarantine_path(self.project_path, episode, QUARANTINE_KIND_NARRATION_SCRIPT_PLAN)
        if quarantine.exists():
            raise ValueError(
                f"第 {episode} 集 script_plan 有草稿待处置（{quarantine}），prompt_authoring 生成已中止；"
                f"请先修改该草稿并经 {PROMOTE_TOOL_NAME} 晋升为正式 script_plan"
            )
        drafts_path = episode_drafts_dir(self.project_path, episode)
        narration_json = SCRIPT_PLAN_FILENAMES["narration"]
        script_plan_json = drafts_path / narration_json
        if not script_plan_json.exists():
            legacy_md = drafts_path / SCRIPT_PLAN_LEGACY_FILENAMES["narration"][0]
            if legacy_md.exists():
                raise FileNotFoundError(
                    f"仅找到结构化前的旧拆分表 {legacy_md}，未找到 {script_plan_json}；"
                    f"请调用 generate_script_plan 产出结构化 {narration_json}"
                )
            raise FileNotFoundError(
                f"未找到脚本规划中间文件: {script_plan_json}；content_mode=narration 期望该文件，请先完成分镜拆分"
            )

        raw_bytes = script_plan_json.read_bytes()
        try:
            raw = json.loads(raw_bytes.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"script_plan_segments.json 解析失败: {e}") from e
        self._script_plan_revision = content_fingerprint_of_data(raw)
        self._freeze_script_plan_artifact_basis(raw)
        self._freeze_script_plan_input_claim(
            episode,
            script_plan_json,
            content_digest=hashlib.sha256(raw_bytes).hexdigest(),
        )

        try:
            draft = NarrationScriptPlanDraft.model_validate(raw)
        except ValidationError as e:
            raise ValueError(f"script_plan_segments.json 结构校验失败: {e}") from e

        segments = [s.model_dump() for s in draft.segments]
        if not segments:
            raise ValueError("script_plan_segments.json segments 为空")

        ids = [s["segment_id"] for s in segments]
        dupes = sorted(sid for sid, count in Counter(ids).items() if count > 1)
        if dupes:
            raise ValueError(f"script_plan_segments.json segment_id 重复: {dupes}")

        # _add_metadata 落盘前会把 E\d+ 前缀改写成当前 episode：原始 id 互异但改写后可能相撞
        # （E1S02_1 与 E2S02_1 在 episode=2 都成 E2S02_1）。提前 fail-loud，杜绝重复 id 静默落盘。
        rewritten_ids = [str(rewrite_episode_prefix(sid, episode)) for sid in ids]
        rewritten_dupes = sorted(sid for sid, count in Counter(rewritten_ids).items() if count > 1)
        if rewritten_dupes:
            raise ValueError(f"script_plan_segments.json segment_id 改写到 episode={episode} 后重复: {rewritten_dupes}")

        allowed = {int(d) for d in supported_durations}
        bad = sorted({s["duration_seconds"] for s in segments if s["duration_seconds"] not in allowed})
        if bad:
            raise ValueError(f"script_plan_segments.json duration_seconds 非法（不在 {sorted(allowed)} 内）: {bad}")

        return segments

    def _load_drama_script_plan_content(self, episode: int) -> dict:
        """加载并解析 drama 的 script_plan 结构化内容（``script_plan_normalized_script.json``）。

        返回 ``{title, scenes: [...]}`` dict；缺文件抛 FileNotFoundError（_load_script_plan）、
        内容非合法 JSON / 顶层非对象 / scenes 非非空列表 / 含非对象分镜项 / scene_id 非非空字符串 /
        scene_id 改写到当前集号后重复，均抛 ValueError。各分镜的内部字段（utterances / source_text 等）
        由 prompt_authoring 合并后经 save_script 的结构校验把关，此处只做最外层形状守卫——但 scenes 形状与 scene_id
        须在此 fail-fast，否则坏 script_plan 会被当成空剧本静默落盘、scene_id 撞键拖到产物文件名 / 资产键才暴露，
        或在 render/merge 阶段抛内部异常而非明确的 script_plan 校验错误。
        """
        # 待修复草稿在场时不生成：正式文件此刻仍是上一版（或不存在），拿它跑 prompt_authoring 等于把一份
        # 待处置的产出静默换成旧内容。内容确认已在工具入口按同一判据阻塞；脚本、测试等
        # 直连调用会绕过工具入口，因此在此重复守卫，与参考生视频同口径。
        quarantine = quarantine_path(self.project_path, episode, QUARANTINE_KIND_DRAMA_SCRIPT_PLAN)
        if quarantine.exists():
            raise ValueError(
                f"第 {episode} 集有待修复草稿（{quarantine}），prompt_authoring 生成已中止；"
                f"请先修改该草稿并经 {PROMOTE_TOOL_NAME} 晋升为正式 script_plan"
            )
        raw = self._load_script_plan(episode)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"脚本规划内容文件不是合法 JSON（drama script_plan 应为结构化内容）: {e}") from e
        if not isinstance(data, dict):
            raise ValueError("脚本规划内容文件结构异常：顶层应为对象 {title, scenes}")
        self._script_plan_revision = content_fingerprint_of_data(data)
        self._freeze_script_plan_artifact_basis(data)
        scenes = data.get("scenes")
        if not isinstance(scenes, list) or not scenes:
            raise ValueError("脚本规划内容文件结构异常：scenes 必须是非空的分镜对象数组")
        scene_ids: list[str] = []
        for idx, scene in enumerate(scenes):
            if not isinstance(scene, dict):
                raise ValueError(f"脚本规划内容文件结构异常：scenes[{idx}] 必须是分镜对象")
            scene_id = scene.get("scene_id")
            if not isinstance(scene_id, str) or not scene_id:
                raise ValueError(f"脚本规划内容文件结构异常：scenes[{idx}].scene_id 必须是非空字符串")
            scene_ids.append(scene_id)
        # _add_metadata 落盘前会把 E\d+ 前缀改写成当前 episode：原始 id 互异但改写后可能相撞
        # （E1S02_1 与 E2S02_1 在 episode=2 都成 E2S02_1）。提前 fail-loud，杜绝重复 id 静默落盘、
        # 下游产物文件名 / 资产键撞车。与 _load_narration_script_plan 同口径。
        rewritten_ids = [str(rewrite_episode_prefix(sid, episode)) for sid in scene_ids]
        rewritten_dupes = sorted(sid for sid, count in Counter(rewritten_ids).items() if count > 1)
        if rewritten_dupes:
            raise ValueError(f"脚本规划内容文件 scene_id 改写到 episode={episode} 后重复: {rewritten_dupes}")
        return data

    def _assert_reference_script_plan_ready(
        self, script_plan_units: list[dict], *, caps: dict | None, gen_mode: str | None
    ) -> None:
        """prompt_authoring 落盘前对 script_plan 现值的全部预判：时长档位仍生效 + 正文按机器口径合法。

        产出路径（付费调用前）与晋升路径（待修复草稿重判前）共用这一份：晋升期间用户可能在 Web
        端改过 script_plan，两处口径若分叉，就会出现「晋升放行、下次生成被拒」或反过来的死角。
        """
        for unit in script_plan_units:
            # 必然失败的已确认时长在付费调用之前拦下：script_plan 加载用的是未收窄的档位全集，
            # 联动约束收窄后它可能已出局。放到 _add_metadata 才拦，TextBackend 的费用已经产生。
            off_tiers = self._unit_duration_off_every_tier(unit["duration_seconds"], caps=caps, gen_mode=gen_mode)
            if off_tiers is not None:
                raise ValueError(
                    f"unit {unit['unit_id']} 已确认时长 {unit['duration_seconds']}s 不在当前生效档位 "
                    f"{sorted(set(off_tiers))} 内；通常是模型或分辨率配置变化让档位收窄导致，"
                    "请调整配置回原档位，或重新拆分该集 script_plan 并重新完成内容确认"
                )
        # prompt_authoring 的产出是 script_plan 正文逐字保留 + 画面展开，script_plan 正文里的语法违约必然原样复现在
        # prompt_authoring 产出上。编辑器侧保存只做结构校验、语法问题仅出 warning（人写的文本有作者意图
        # 要保护），因此手工编辑过的 script_plan 可能带着未登记的 @[名称] 或描述行里的花括号进到这里
        # ——不在调用前判，就会付完 prompt_authoring 的钱才失败，且错误指向 prompt_authoring「改坏了」，而真正要改的
        # 是 script_plan。故在此按同一把尺预判 script_plan 正文，违约时指名 script_plan。
        self._assert_reference_script_plan_text_valid(script_plan_units, max_refs=self._resolve_max_refs(caps))

    def _assert_reference_script_plan_text_valid(self, script_plan_units: list[dict], *, max_refs: int | None) -> None:
        """按机器产物的严格口径预判 script_plan 各 unit 正文，违约时把定位与出路指回 script_plan。

        与 ``_merge_reference_visual`` 用的是同一个 ``validate_unit_text``：同一把尺量两处，
        避免「script_plan 放行、prompt_authoring 必拒」的死角。此处只判、不取派生结果——参考图是执行期从正文
        派生的，落盘物只有正文本身。

        台词口播时长同样在此复判：拆分工具只在产出当时判过一次，内容确认时改短 unit 时长或
        补写台词都能绕开它，而 prompt_authoring 逐字保留台词、之后再无口播量校验——不复判就会让念不完的
        unit 一路落盘。
        """
        source_language = self.project_json.get("source_language")
        speech_rate_override = project_speech_rate_override(self.project_json)
        for unit in script_plan_units:
            label = f"script_plan 的 unit {unit['unit_id']}"
            text = str(unit.get("text") or "")
            try:
                validate_unit_text(
                    label,
                    text,
                    self.project_json,
                    unit_id=str(unit["unit_id"]),
                    max_refs=max_refs,
                )
                validate_dialogue_load(
                    label, text, int(unit["duration_seconds"]), source_language, speech_rate_override
                )
            except DraftViolation as exc:
                enriched = [
                    DraftViolation(
                        f"{item}；这段正文来自 script_plan（拆分产出或手工编辑），prompt_authoring 会逐字保留它，"
                        "请先在 Web 端修正该 unit 的 script_plan 正文或时长并重新完成内容确认",
                        code=item.code,
                        label=label,
                        line=item.line,
                        locations=item.locations,
                        reason=item.reason,
                        action=item.action,
                    )
                    for item in violation_items(exc)
                ]
                if len(enriched) == 1:
                    raise enriched[0] from exc
                raise DraftViolations(enriched) from exc

    def _merge_reference_visual(
        self,
        script_plan_units: list[dict],
        response_text: str,
        episode: int,
        *,
        max_refs: int | None,
    ) -> dict:
        """参考路径 prompt_authoring 合并：LLM 只出引用语法正文，其余字段机械沿用 script_plan / 从正文派生。

        保结构 diff 在此落地——unit 数与顺序、台词规范行逐字都由 script_plan 定稿，
        prompt_authoring 只允许把画面描述写详细。任一项被改动即 fail-loud（``DraftViolation``），不静默
        接受：台词配不上画面时正确的出路是回到 script_plan 重拆，而不是让 prompt_authoring 自行改词。

        逐 unit 的违约收齐后一次抛出（``DraftViolations``），供调用方把整份产出连同报告落到
        待修复草稿——单条抛出会让 Agent 每修一个 unit 就要重跑一次付费的展开。

        ``unit_id`` / ``duration_seconds`` 直接取 script_plan 的值，参考图不落盘、执行期再从正文
        派生——LLM 没写这些字段，也就没有对不上的可能。
        """
        text = strip_json_code_fences(response_text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON 解析失败: {e}") from e
        # title 缺失/空白兜底须在校验之前：title 仅展示用、用户可改，非约束解码通道下模型
        # 整字段漏写不该让一次已付费的展开失败（与 _parse_response 的兜底同口径）。
        if isinstance(data, dict):
            raw_title = data.get("title")
            if not (isinstance(raw_title, str) and raw_title.strip()):
                data["title"] = f"第{episode}集"
        try:
            flat = ReferencePromptAuthoringFlatScript.model_validate(data)
        except ValidationError as e:
            raise ValueError(f"prompt_authoring 提示词编写结构校验失败: {e}") from e

        if len(flat.units) != len(script_plan_units):
            raise DraftViolation(
                f"prompt_authoring 产出的 unit 数（{len(flat.units)}）与 script_plan 已确认的（{len(script_plan_units)}）不一致；"
                "prompt_authoring 只做提示词编写，不得合并、拆分或增删 unit",
                code="unit_count_changed",
            )

        video_units: list[dict] = []
        violations: list[DraftViolation] = []
        for script_plan_unit, flat_unit in zip(script_plan_units, flat.units, strict=True):
            label = f"unit {script_plan_unit['unit_id']}"
            script_plan_text = str(script_plan_unit.get("text") or "")
            # 逐 unit 收集而非首个违约即抛：报告要覆盖所有坏 unit，Agent 一轮就能看全要改什么。
            # 一个 unit 内部仍是首个违约即停——正文解析不出时，后续判定都建立在同一个问题上。
            try:
                validate_unit_text(label, flat_unit.text, self.project_json, max_refs=max_refs)
                assert_dialogue_preserved(label, script_plan_text, flat_unit.text)
            except DraftViolation as exc:
                violations.extend(violation_items(exc))
                continue
            video_units.append(
                {
                    "unit_id": script_plan_unit["unit_id"],
                    "text": flat_unit.text,
                    "duration_seconds": script_plan_unit["duration_seconds"],
                }
            )

        if violations:
            raise DraftViolations(violations)
        return ReferenceVideoScript.model_validate({"title": flat.title, "video_units": video_units}).model_dump()

    def _prompt_authoring_flat_content(self, response_text: str, episode: int) -> dict:
        """把 prompt_authoring 响应还原成待修复草稿要装的扁平形状 ``{title, units: [{text}]}``。

        与 ``_merge_reference_visual`` 的解析前置（去代码围栏 → title 兜底 → schema 校验）
        逐步同口径：待修复草稿装的必须是「schema 已过、只是内容违约」的那份产物，否则 Agent
        改的正文与合并时读的正文形状不同。
        """
        data = json.loads(strip_json_code_fences(response_text))
        if isinstance(data, dict):
            raw_title = data.get("title")
            if not (isinstance(raw_title, str) and raw_title.strip()):
                data["title"] = f"第{episode}集"
        return ReferencePromptAuthoringFlatScript.model_validate(data).model_dump()

    def _quarantine_reference_prompt_authoring(
        self,
        episode: int,
        response_text: str,
        exc: DraftViolation,
        *,
        base_fingerprint: str | _UnsetExpectedFingerprint | None = _UNSET_EXPECTED_FINGERPRINT,
        expected_draft_revision: str | None,
        before_commit: Callable[[], None] | None = None,
    ) -> DraftViolation:
        """把违约的 prompt_authoring 产出与报告落待修复草稿，返回携带报告的违约异常（由调用方抛出）。

        返回而不是自己抛：调用点用 ``raise ... from exc`` 保留原始违约链，异常在此被构造却在
        彼处抛出会让 traceback 指向本函数而非合并逻辑。
        """
        draft_path = quarantine_path(self.project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING)
        formal_path = self.project_path / "scripts" / episode_script_filename(episode)
        pm = ProjectManager(str(self.project_path.parent))
        with pm.file_lock(draft_path), pm.file_lock(formal_path):
            current = read_quarantine(self.project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING)
            actual_draft_revision = draft_revision(current) if current is not None else None
            if actual_draft_revision != expected_draft_revision:
                return DraftViolation(
                    "reference prompt_authoring 草稿在模型生成期间已变化；本次生成结果未覆盖并发编辑，请先处置现有草稿",
                    code="draft_revision_conflict",
                )
            if before_commit is not None:
                before_commit()
            report = quarantine_and_report(
                self.project_path,
                episode,
                QUARANTINE_KIND_PROMPT_AUTHORING,
                content=self._prompt_authoring_flat_content(response_text, episode),
                violations=violation_items(exc),
                meta={
                    "base_fingerprint": (
                        content_fingerprint(formal_path)
                        if isinstance(base_fingerprint, _UnsetExpectedFingerprint)
                        else base_fingerprint
                    )
                },
            )
        return DraftViolation(report, code="quarantined")

    def _reference_prompt_authoring_draft_revision(self, episode: int) -> str | None:
        path = quarantine_path(self.project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING)
        with ProjectManager(str(self.project_path.parent)).file_lock(path):
            draft = read_quarantine(self.project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING)
            return draft_revision(draft) if draft is not None else None

    def _save_reference_prompt_authoring_if_draft_unchanged(
        self,
        episode: int,
        expected_draft_revision: str | None,
        script_data: dict[str, Any],
        filename: str,
        formal_baseline: str | None,
        cancellation_file_receipts: list[FormalWriteReceipt] | None,
        cancellation_manifest_receipts: list[ArtifactEntryRekeyReceipt] | None,
    ) -> Path:
        draft_path = quarantine_path(self.project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING)
        pm = ProjectManager(str(self.project_path.parent))
        with pm.file_lock(draft_path):
            current = read_quarantine(self.project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING)
            actual_draft_revision = draft_revision(current) if current is not None else None
            if actual_draft_revision != expected_draft_revision:
                raise DraftViolation(
                    "reference prompt_authoring 草稿在模型生成期间已变化；本次生成结果未覆盖并发编辑，请先处置现有草稿",
                    code="draft_revision_conflict",
                )
            return pm.save_script(
                self.project_path.name,
                script_data,
                filename,
                validate=True,
                artifact_basis=self._artifact_basis,
                expected_fingerprint=formal_baseline,
                cancellation_file_receipts=cancellation_file_receipts,
                cancellation_manifest_receipts=cancellation_manifest_receipts,
            )

    def _promote_reference_prompt_authoring_draft_sync(
        self,
        episode: int,
        caps: dict | None,
        output_filename: str | None = None,
        *,
        expected_fingerprint: str | _UnsetExpectedFingerprint | None = _UNSET_EXPECTED_FINGERPRINT,
        _prompt_authoring_lock_held: bool = False,
    ) -> Path:
        draft_path = quarantine_path(self.project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING)
        pm = ProjectManager(str(self.project_path.parent))
        prompt_authoring_lock = nullcontext() if _prompt_authoring_lock_held else pm.file_lock(draft_path)
        with prompt_authoring_lock:
            return self._promote_reference_prompt_authoring_draft_locked_sync(
                episode,
                caps,
                output_filename,
                expected_fingerprint=expected_fingerprint,
            )

    def _promote_reference_prompt_authoring_draft_locked_sync(
        self,
        episode: int,
        caps: dict | None,
        output_filename: str | None = None,
        *,
        expected_fingerprint: str | _UnsetExpectedFingerprint | None = _UNSET_EXPECTED_FINGERPRINT,
    ) -> Path:
        draft = read_quarantine(self.project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING)
        if draft is None:
            raise FileNotFoundError(
                f"第 {episode} 集没有可晋升的 prompt_authoring 待修复草稿"
                f"（{quarantine_path(self.project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING)} 缺失或内容不是合法信封）"
            )

        script_plan_units = self._load_reference_script_plan(
            episode,
            self._resolve_raw_supported_durations(caps),
            _prompt_authoring_lock_held=True,
        )
        # 与产出路径同一份 script_plan 预判：草稿在场期间 Web 端可能改过 script_plan（编辑器对人写正文只出
        # warning），不复判就会让改短时长后念不完的台词、或未登记的 @[名称] 借晋升一路落盘。
        self._assert_reference_script_plan_ready(script_plan_units, caps=caps, gen_mode="reference_video")
        max_refs = self._resolve_max_refs(caps)
        try:
            script_data = self._merge_reference_visual(
                script_plan_units, json.dumps(draft.content), episode, max_refs=max_refs
            )
            # _add_metadata 一并纳入：它按落地后的最终正文重算生效档位，草稿里新增 /
            # 去掉一个 `@` 引用就会在合并之后才判出档，留在 try 之外会让晋升在这一类上退回
            # 「报错但草稿不刷新」。
            script_data = self._add_metadata(
                script_data,
                episode,
                reference_unit_durations={
                    str(rewrite_episode_prefix(u["unit_id"], episode)): u["duration_seconds"] for u in script_plan_units
                },
                caps=caps,
            )
        except DraftViolation as exc:
            raise DraftViolation(
                quarantine_and_report(
                    self.project_path,
                    episode,
                    QUARANTINE_KIND_PROMPT_AUTHORING,
                    content=draft.content,
                    violations=violation_items(exc),
                    meta=draft.meta,
                ),
                code="quarantined",
            ) from exc
        except ValueError as exc:
            # schema 层（DraftViolation 是 ValueError 子类，故须排在前）同样只回报告：这条路上
            # 内容是 Agent 手写的，没有 backend 可重试，与 script_plan 晋升的 schema_invalid 同口径。
            raise DraftViolation(
                quarantine_and_report(
                    self.project_path,
                    episode,
                    QUARANTINE_KIND_PROMPT_AUTHORING,
                    content=draft.content,
                    violations=[
                        DraftViolation(
                            f"待修复草稿的 content 不符合 prompt_authoring 产出结构：{exc}",
                            code="schema_invalid",
                        )
                    ],
                    meta=draft.meta,
                ),
                code="quarantined",
            ) from exc

        filename = output_filename or episode_script_filename(episode)
        pm = ProjectManager(str(self.project_path.parent))
        if isinstance(expected_fingerprint, _UnsetExpectedFingerprint):
            if "base_fingerprint" not in draft.meta:
                raise DraftViolation(
                    "reference prompt_authoring 草稿缺少生成时的正式剧本基线 base_fingerprint；无法安全晋升",
                    code="formal_revision_missing",
                )
            resolved_expected_fingerprint = cast(str | None, draft.meta["base_fingerprint"])
        else:
            resolved_expected_fingerprint = expected_fingerprint
        output_path = pm.save_script(
            self.project_path.name,
            script_data,
            filename,
            validate=True,
            artifact_basis=self._artifact_basis,
            expected_fingerprint=resolved_expected_fingerprint,
        )
        # 落盘成功后才清草稿：写盘失败时草稿还在，重试晋升即可，不会两头皆空。
        clear_quarantine(self.project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING)
        self._quality_probe(script_data, episode)
        return output_path

    async def promote_reference_prompt_authoring_draft(
        self,
        episode: int,
        output_filename: str | None = None,
        *,
        expected_fingerprint: str | _UnsetExpectedFingerprint | None = _UNSET_EXPECTED_FINGERPRINT,
        _prompt_authoring_lock_held: bool = False,
    ) -> Path:
        """按产出时那套校验器全量重判 prompt_authoring 待修复草稿，通过则晋升为正式剧本并清除草稿。

        重判用的是 ``_merge_reference_visual`` 本身，不是它的简化副本：晋升口径与产出口径必须
        同一份代码，否则「晋升时放行、下次生成时被拒」这类分叉会重新出现。script_plan 一并重读——
        草稿在场期间用户可能在内容确认界面改过 script_plan，保结构 diff 要对着现值判。

        仍有违约时刷新草稿里的报告快照后抛出（``DraftViolation``），草稿留在原地供继续修改；
        无收敛轮次上限。
        """
        caps = await self._fetch_video_capabilities()
        return await run_sync_transaction(
            self._promote_reference_prompt_authoring_draft_sync,
            episode,
            caps,
            output_filename,
            expected_fingerprint=expected_fingerprint,
            _prompt_authoring_lock_held=_prompt_authoring_lock_held,
        )

    def _parse_response(self, response_text: str, episode: int) -> dict:
        """
        解析并验证 TextBackend 响应

        Args:
            response_text: API 返回的 JSON 文本
            episode: 剧集编号

        Returns:
            验证后的剧本数据字典
        """
        # 清理可能的 markdown 包装
        text = strip_json_code_fences(response_text)

        # 解析 JSON
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON 解析失败: {e}") from e

        # title 缺失/空白兜底：非约束解码通道下模型可能整字段漏写。title 仅展示用、
        # 用户可改，不值得让整集生成失败；与 _merge_narration_visual 的兜底同口径。
        if isinstance(data, dict):
            title = data.get("title")
            if not (isinstance(title, str) and title.strip()):
                data["title"] = f"第{episode}集"

        # 校验模型经规范解析定骨架种类（分镜图生视频按创作类型，参考生视频统一 video_units），
        # kind→模型映射留本地（模型属上层依赖，不进 SKELETONS 窄表）。
        kind = resolve_declared_kind(self.content_mode, self.generation_mode)
        schema = _KIND_PARSE_SCHEMA[kind]
        try:
            return schema.model_validate(data).model_dump()
        except ValidationError as e:
            logger.warning("数据验证警告: %s", e)
            # 返回原始数据，允许部分不符合 schema
            return data

    def _parse_ad_reference_response(self, response_text: str, episode: int) -> dict:
        """把广告/短片的参考生视频的扁平 LLM 输出机械提升为自包含 ``video_units``。"""
        text = strip_json_code_fences(response_text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"广告参考剧本 JSON 解析失败: {exc}") from exc
        try:
            flat = AdReferenceFlatScript.model_validate(data)
        except ValidationError as exc:
            raise ValueError(f"广告参考剧本结构校验失败: {exc}") from exc

        units: list[dict] = []
        for ordinal, source in enumerate(flat.units, start=1):
            unit_id = f"E{episode}U{ordinal}"
            try:
                validate_unit_text(
                    f"unit {unit_id}",
                    source.text,
                    self.project_json,
                    max_refs=None,
                )
            except DraftViolations as exc:
                if not exc.items or any(
                    item.code not in {"mixed_speech", "empty_speaker", "parse_failed"} for item in exc.items
                ):
                    raise
            unit: dict = {
                "unit_id": unit_id,
                "text": source.text,
                "duration_seconds": source.duration_seconds,
                "transition_to_next": "cut",
                "note": None,
                "generated_assets": {},
            }
            if video_unit_replan_problems(unit):
                unit["needs_replan"] = True
            units.append(unit)

        return ReferenceVideoScript.model_validate(
            {"title": flat.title or f"第{episode}集", "content_mode": "ad", "video_units": units}
        ).model_dump()

    def _parse_narration_visual(self, response_text: str, episode: int) -> dict:
        """解析 prompt_authoring 视觉层 LLM 响应（NarrationVisualEpisodeScript）。

        严格校验 + model_dump：视觉 schema 的 segment 走 ``extra="forbid"``，LLM 若混入
        novel_text 等非视觉字段即拒（而非静默携带进合并覆盖 script_plan 透传值）；dump 后视觉
        数据只含 title + segment_id + image_prompt / video_prompt，合并阶段不会污染内容层。
        """
        text = strip_json_code_fences(response_text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"prompt_authoring 视觉层 JSON 解析失败: {e}") from e
        try:
            validated = NarrationVisualEpisodeScript.model_validate(data)
        except ValidationError as e:
            raise ValueError(f"prompt_authoring 视觉层结构校验失败: {e}") from e
        return validated.model_dump()

    def _merge_narration_visual(self, script_plan_segments: list[dict], visual_data: dict, episode: int) -> dict:
        """把 prompt_authoring LLM 的视觉层按 segment_id 合并回 script_plan 已确认的结构。

        script_plan 结构（novel_text、时长、segment_break 等内容字段）是单一真相源，逐字透传；
        LLM 只产出视觉层，按 segment_id 对齐合并回各分镜——novel_text 永不经 LLM 重出，
        从工程上根除扩写漂移。校验 segment_id 唯一且与 script_plan 全覆盖：缺、多、重都 fail-loud，
        杜绝顺序错配与漏段。
        """
        visual_segments = visual_data["segments"]

        visual_by_id: dict[str, dict] = {}
        for item in visual_segments:
            sid = item["segment_id"]
            if sid in visual_by_id:
                raise ValueError(f"episode {episode} 视觉层 segment_id 重复: {sid}")
            visual_by_id[sid] = item

        script_plan_ids = [s["segment_id"] for s in script_plan_segments]
        script_plan_id_set = set(script_plan_ids)
        missing = [sid for sid in script_plan_ids if sid not in visual_by_id]
        if missing:
            raise ValueError(f"episode {episode} 视觉层缺少 script_plan 分镜: {missing}")
        extra = [sid for sid in visual_by_id if sid not in script_plan_id_set]
        if extra:
            raise ValueError(f"episode {episode} 视觉层含 script_plan 未定义的 segment_id: {extra}")

        merged_segments: list[dict] = []
        for s1 in script_plan_segments:
            sid = s1["segment_id"]
            merged_segments.append({**s1, **visual_by_id[sid]})

        title = visual_data.get("title")
        return {
            "title": title if isinstance(title, str) and title.strip() else f"第{episode}集",
            "segments": merged_segments,
        }

    def _add_metadata(
        self,
        script_data: dict,
        episode: int,
        *,
        reference_unit_durations: dict[str, int] | None = None,
        caps: dict | None = None,
    ) -> dict:
        """
        补充剧本元数据

        Args:
            script_data: 剧本数据
            episode: 剧集编号
            reference_unit_durations: reference_video 路径按 unit_id（改写后）机械覆盖 LLM
                输出的 unit 时长——script_plan 确认的原始值，未经取档；取档按下方逐 unit 重算，
                见 ``generate`` 内的构造处注释
            caps: 逐 unit 解析生效档位的能力值；为 None 时按 caps → registry 两级回退解析，
                不跳过取档校验

        Returns:
            补充元数据后的剧本数据
        """
        gen_mode = self.generation_mode
        # CLI 参数 --episode 是集号唯一真相源。schema 已从 AI 输出中移除 episode 字段，
        # 这里负责落盘前补上。
        script_data["episode"] = int(episode)

        # 兜底改写 segment/scene/unit ID 中的 E\d+ 前缀，避免 LLM 写错集号导致文件
        # 名跨集冲突（如 storyboards/scene_E1S01.png 被 E2 重新覆盖）。
        ep = int(episode)
        # segment/scene/shot/unit ID 前缀统一经规范解析定骨架 + resolve_kind_items 查条目数组
        # 与 id 字段改写（参考生视频三种 content_mode 均映射到 video_units，无需按生成模式分支）。
        # self.content_mode 为项目级校验值，解析不会 fail-loud。
        kind = resolve_declared_kind(self.content_mode, gen_mode)
        raw_rewrite_items, id_field, _kind = resolve_kind_items(script_data, kind=kind)
        # 校验失败降级保存的原始 dict 里该数组可能为非列表脏值（LLM 误写标量），
        # `... or []` 只挡 falsy、挡不住真值标量，isinstance 守卫避免 `for` 迭代崩溃。
        rewritten_output_ids: list[str] = []
        for s in raw_rewrite_items if isinstance(raw_rewrite_items, list) else []:
            if isinstance(s, dict) and id_field in s:
                s[id_field] = rewrite_episode_prefix(s.get(id_field), ep)
                if reference_unit_durations is not None:
                    rewritten_output_ids.append(str(s[id_field]))

        for item in raw_rewrite_items if isinstance(raw_rewrite_items, list) else []:
            if not isinstance(item, dict):
                continue
            admission = admit_script_unit(kind, item, ignore_marker=True)
            if admission.allowed:
                item.pop("needs_replan", None)
            else:
                item["needs_replan"] = True

        if reference_unit_durations is not None:
            # unit_id 集合须与 script_plan 完全一致才覆盖时长：LLM 漏写某个已确认 unit、或输出
            # script_plan 之外的陌生 unit_id，都说明输出与 script_plan 基底脱节，覆盖时长掩盖不了这个
            # 更根本的问题——与 drama 两段式合并（DramaVisualMergeError）同一套 fail-loud 口径。
            dupes = sorted(uid for uid, count in Counter(rewritten_output_ids).items() if count > 1)
            if dupes:
                raise ValueError(f"reference_video 输出 unit_id 重复: {dupes}")
            missing = sorted(set(reference_unit_durations) - set(rewritten_output_ids))
            if missing:
                raise ValueError(f"reference_video 输出缺少 script_plan 已确认的 unit_id: {missing}")
            unknown = sorted(set(rewritten_output_ids) - set(reference_unit_durations))
            if unknown:
                raise ValueError(f"reference_video 输出包含 script_plan 之外的未知 unit_id: {unknown}")

            for s in raw_rewrite_items if isinstance(raw_rewrite_items, list) else []:
                if not (isinstance(s, dict) and id_field in s):
                    continue
                target_duration = reference_unit_durations[s[id_field]]
                # 取档按这个 unit 最终落地的正文算，不是 script_plan 拆分时的状态：正文里的
                # `@[名称]` 由 LLM 在 prompt_authoring 输出时决定，可能与 script_plan 的不同。caps 为 None
                # 也不短路——_resolve_supported_durations 自带 caps → registry 两级回退。
                unit_tiers = self._unit_duration_off_tier(
                    target_duration,
                    has_references=bool(extract_mentions(str(s.get("text") or ""))),
                    caps=caps,
                    gen_mode=gen_mode,
                )
                if unit_tiers is not None:
                    # 生效档位收窄到已确认值之外：不静默取档改写——用户审阅通过的时长/费用不被
                    # 换成从未过目的值落盘。抛内容违约（而非裸 ValueError）让 reference 路径把这
                    # 份已付费产出落待修复草稿：成因通常是该次生成给这个 unit 新增/去掉了 `@` 引用，
                    # 改一改草稿正文的引用即可修好，不该退回丢弃重抽。
                    raise DraftViolation(
                        f"unit {s[id_field]} 已确认时长 {target_duration}s 不在当前生效档位 "
                        f"{sorted(set(unit_tiers))} 内；通常是该次生成给该 unit 新增/去掉了引用"
                        "导致，请调整该 unit 正文里的 `@` 引用使其回到该档位；若引用本就该是这样，"
                        "说明模型能力已变化，需要重新拆分该集 script_plan",
                        code="duration_off_tier",
                        label=f"unit {s[id_field]}",
                    )
                if s.get("duration_seconds") != target_duration:
                    logger.warning(
                        "unit %s 时长与 script_plan 确认值不一致（LLM 输出 %s，已按 script_plan 确认值 %s 覆盖）",
                        s[id_field],
                        s.get("duration_seconds"),
                        target_duration,
                    )
                s["duration_seconds"] = target_duration
        # content_mode 严格只是"内容类型"（narration/drama/ad）；"视频来源"维度是项目级事实，
        # 剧本不落盘任何生成模式标记——生成分派一律读项目生成模式。
        # 参考生视频剧本必须强制覆盖：ReferenceVideoScript.content_mode 有 Pydantic 默认值
        # "narration"，setdefault 拿不到项目级真值；非参考集 LLM 已在 schema 中产出
        # narration/drama，setdefault 仅作 fallback。
        if self.content_mode != "ad" and gen_mode == "reference_video":
            script_data["content_mode"] = self.content_mode
        else:
            script_data.setdefault("content_mode", self.content_mode)

        # 集级钩子/下集预告：分集账本是钩子设计的单一真相源，强制以账本值覆盖
        # （LLM 不参与填写，model_dump 只会留下 None 默认值）。账本无规划数据时为 None。
        # ad 恒单集、无分集账本概念，剧本模型也不持有这两个字段，跳过注入。
        if self.content_mode != "ad":
            entry = self._episode_entry(ep)
            script_data["hook"] = entry.get("hook")
            script_data["next_episode_teaser"] = self._entry_outline(entry).get("next_episode_teaser")

        # 添加小说信息
        # 注意守卫语义：novel 字段已 SkipJsonSchema 隐藏，但 default_factory=NovelInfo
        # 让 model_dump 输出必带 {"title":"","chapter":""} 占位。所以判 "key 是否存在"
        # 无法捕获真实"未注入"状态，必须按内容判：title/chapter 任一为空就重注入。
        novel = script_data.get("novel")
        if not isinstance(novel, dict) or not novel.get("title") or not novel.get("chapter"):
            script_data["novel"] = {
                "title": self.project_json.get("title", ""),
                "chapter": f"第{episode}集",
            }
        # 剥离已废弃的 source_file（AI 可能虚构）
        novel = script_data.get("novel")
        if isinstance(novel, dict):
            novel.pop("source_file", None)

        # 剥离剧本级 generation_mode：生成模式的真相源是 project.json，剧本不留标记。
        # 校验失败时 script_data 是后端原样返回的 dict（未经模型过滤），存量剧本重生成也会
        # 把旧值带进来——不在此处删就会随写盘回到磁盘上。
        script_data.pop("generation_mode", None)

        # 添加时间戳
        now = datetime.now(UTC).isoformat()
        script_data.setdefault("metadata", {})
        script_data["metadata"]["created_at"] = now
        script_data["metadata"]["updated_at"] = now
        script_data["metadata"]["generator"] = self.generator.model if self.generator else "unknown"
        script_plan_revision = getattr(self, "_script_plan_revision", None)
        if script_plan_revision is not None:
            script_data["metadata"][SCRIPT_PLAN_REVISION_FIELD] = script_plan_revision

        # 剥离废弃的 episode 级聚合字段：条目数、总时长与角色/场景/道具聚合都是从剧本正文
        # 逐读即得的派生值，由项目摘要读时计算，落盘一份只会与正文漂移。
        script_data["metadata"].pop("total_scenes", None)
        script_data["metadata"].pop("estimated_duration_seconds", None)
        script_data.pop("duration_seconds", None)
        script_data.pop("characters_in_episode", None)
        script_data.pop("clues_in_episode", None)

        return script_data

    def _quality_probe(self, script_data: dict, episode: int) -> None:
        """落盘后的轻量质量探针：仅日志，不阻断、不重试。

        统计极端短样本（scene/action/shot text 字符数低于阈值），定位"内容
        过短"风险。阈值仅捕"明显异常"，正常完整描述应远超这些值。
        外层 try/except 兜底：当 _parse_response 在校验失败时返回 raw dict、
        其中嵌套字段类型不符合 schema 时（如 image_prompt 是字符串），
        探针只 warning 不阻断 generate。
        """
        try:
            short_ids: list[str] = []

            # 骨架经规范解析统一判别、条目数组与 id 字段查 resolve_kind_items（同 _add_metadata
            # id 改写处置）。video_units 的过短样本落在 unit 正文，与 narration/drama/ad 平铺条目的
            # image_prompt/video_prompt 探针数据形状不同——结构分支按 kind 显式区分、非骨架分派。
            kind = resolve_declared_kind(self.content_mode, self.generation_mode)
            raw_items, id_key, _kind = resolve_kind_items(script_data, kind=kind)
            # 降级保存的原始 dict 里数组可能为非列表脏值；`... or []` 挡不住真值标量，
            # isinstance 守卫避免 `for` 迭代崩溃（外层 try/except 会吞异常但会误跳过整段探针）。
            items = raw_items if isinstance(raw_items, list) else []
            if kind == "video_units":
                for u in items:
                    if not isinstance(u, dict):
                        continue
                    uid = str(u.get(id_key) or "?")
                    if len(str(u.get("text") or "")) < _QUALITY_PROBE_UNIT_TEXT_MIN_LEN:
                        short_ids.append(uid)
            else:
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    iid = str(item.get(id_key) or "?")
                    img_p = item.get("image_prompt")
                    vid_p = item.get("video_prompt")
                    img_p = img_p if isinstance(img_p, dict) else {}
                    vid_p = vid_p if isinstance(vid_p, dict) else {}
                    scene = str(img_p.get("scene") or "")
                    action = str(vid_p.get("action") or "")
                    if len(scene) < _QUALITY_PROBE_SCENE_MIN_LEN or len(action) < _QUALITY_PROBE_ACTION_MIN_LEN:
                        short_ids.append(iid)

            if short_ids:
                logger.warning(
                    "episode %d quality probe: short=%s",
                    episode,
                    sorted(set(short_ids)),
                )

            # narration 的 novel_text 现由 script_plan 透传、prompt_authoring 不再重出，扩写漂移已从结构上
            # 消除（不存在「LLM 偷偷扩写」的窗口），故不再做 novel_text 漂移探针。

            # ad 总时长偏差观察：剧本总时长应贴近 target_duration，但供应商时长枚举的
            # 量化误差让精确命中不现实。仅 WARN，不阻断/不重试/不推前端。
            if self.content_mode == "ad":
                target = self.project_json.get("target_duration")
                if isinstance(target, int) and not isinstance(target, bool) and target > 0:
                    total = script_duration_total(kind, items)
                    delta_ratio = abs(total - target) / target
                    if delta_ratio > AD_TARGET_DURATION_DRIFT_THRESHOLD:
                        logger.warning(
                            "episode %d target_duration drift: target=%d actual=%d delta=%.1f%%",
                            episode,
                            target,
                            total,
                            delta_ratio * 100,
                        )
        except Exception as exc:
            logger.warning("episode %d quality probe skipped due to unexpected data shape: %s", episode, exc)
