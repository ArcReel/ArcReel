"""
script_generator.py - 剧本生成器

读取脚本规划结构化中间文件，调用文本生成 Backend 生成最终 JSON 剧本
"""

import asyncio
import copy
import functools
import hashlib
import json
import logging
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional, cast

from pydantic import BaseModel, TypeAdapter, ValidationError

from lib.artifacts.artifact_activation import (
    ArtifactInputClaim,
    active_artifact_currency_resolver,
    assert_current_artifact_input_claims_usable,
    resolve_usable_artifact_input_claim,
)
from lib.artifacts.artifact_manifest import ArtifactBasisDescriptor, ArtifactKey
from lib.artifacts.artifact_provenance import (
    build_ad_episode_script_basis,
    project_ad_episode_script_inputs,
)
from lib.backends.providers import CallPurpose
from lib.backends.text_backends.base import TextGenerationRequest, TextTaskType
from lib.backends.text_generator import TextGenerator
from lib.config.resolver import ConfigResolver, VideoGenerationType, video_bucket_for_generation_mode
from lib.db import async_session_factory
from lib.episode.episode_ids import episode_title
from lib.episode.episode_paths import (
    REFERENCE_VIDEO_SCRIPT_PLAN_FILENAME,
    REFERENCE_VIDEO_SCRIPT_PLAN_LEGACY_FILENAME,
    SCRIPT_PLAN_FILENAMES,
    SCRIPT_PLAN_LEGACY_FILENAMES,
    episode_drafts_dir,
)
from lib.generation.video_request_facts import (
    VideoRequestFacts,
    VideoRequestFactsError,
    VideoRequestFactsFailure,
    VideoRoute,
    plan_capability_inputs,
    planning_durations,
    reference_migration_durations,
    require_video_request_facts,
)
from lib.i18n import _ as translate
from lib.infra.async_thread import run_noninterruptible_async, run_noninterruptible_sync, run_sync_transaction
from lib.infra.content_digest import sha256_file
from lib.infra.text_utils import strip_json_code_fences
from lib.project.asset_types import asset_name_comparison_key
from lib.project.item_id_history import issue_landing_item_ids
from lib.project.project_manager import ProjectManager, ScriptWriteConflict
from lib.prompts.prompt_builders_ad import (
    build_ad_prompt,
    build_ad_reference_prompt,
    build_ad_reference_prompt_authoring_prompt,
    build_ad_shot_prompt_authoring_prompt,
)
from lib.prompts.prompt_builders_reference import build_reference_video_prompt
from lib.prompts.prompt_builders_script import (
    build_drama_prompt,
    build_narration_prompt,
    render_drama_content_for_prompt_authoring,
)
from lib.script.item_excerpt import find_item
from lib.script.item_ids import item_id_number
from lib.script.plan_new_assets import (
    NEW_ASSETS_FIELD,
    NewAssetResolution,
    NewAssetsError,
    UnregisteredReferencesError,
    planning_project,
    resolve_new_assets,
    unregistered_references,
)
from lib.script.prompt_authoring_scope import (
    PromptAuthoringSelection,
    PromptOverwrite,
    PromptOverwriteRequired,
    select_prompt_authoring,
)
from lib.script.reference_video.draft_validation import (
    DraftViolation,
    DraftViolations,
    assert_dialogue_preserved,
    dialogue_speakers,
    dialogue_spoken_seconds,
    validate_dialogue_load,
    validate_unit_text,
    violation_items,
)
from lib.script.reference_video.duration_slots import resolve_duration_slot
from lib.script.reference_video.request_projection import ReferenceUnitHydration
from lib.script.reference_video.unit_capabilities import hydrate_reference_units
from lib.script.reference_video.voice_settings import VoiceRenderSettings
from lib.script.script_document import (
    build_materialized_script,
    episode_ledger_entry,
    finish_script_document,
    ledger_outline,
    prepare_script_entries,
)
from lib.script.script_models import (
    AD_TARGET_DURATION_DRIFT_THRESHOLD,
    AdEpisodeScript,
    AdReferenceFlatScript,
    AdVisualScript,
    AuthoredReferenceUnit,
    DramaEpisodeScript,
    DramaSceneContent,
    DramaVisualScript,
    NarrationEpisodeScript,
    NarrationScriptPlanDraft,
    NarrationVisualEpisodeScript,
    ReferenceAuthoringResponse,
    ReferencePromptAuthoringFlatScript,
    ReferenceScriptPlanDraft,
    ReferenceVideoScript,
    build_episode_script_model,
    script_duration_total,
    with_new_assets_field,
)
from lib.script.script_plan_entries import ScriptPlanKind, entry_id_field, plan_variant
from lib.script.script_review import (
    FormalScriptOverwrite,
    ScriptPlanWriteConflict,
    content_fingerprint,
    content_fingerprint_of_data,
    fingerprint_of_revision,
    formal_script_filename,
    formal_script_overwrite,
    formal_script_plan_lock,
    migrate_script_plan_draft_in_place,
    script_plan_path,
)
from lib.script.script_skeleton import resolve_declared_kind, resolve_kind_items, rewrite_episode_prefix
from lib.script.script_structure_validator import validate_script_structure
from lib.speech.speech_composition import require_script_unit_admitted
from lib.speech.speech_rate import project_speech_rate_override
from lib.video_runtime.resolution import read_video_request_facts

logger = logging.getLogger(__name__)

_DURATION_ADAPTER = TypeAdapter(int)
_DRAMA_DEFAULT_DURATION = DramaSceneContent.model_fields["duration_seconds"].default

_NO_ENTRY_TO_AUTHOR_NOTE = (
    "本次没有要编写的条目：运行时不会调用文本模型；要覆盖已有视觉层，请用 entry_ids 点名条目并传 rewrite=true。"
)

_AD_UNIT_REPLAN_CODES = frozenset({"mixed_speech", "empty_speaker", "parse_failed"})

_VISUAL_RESPONSE_SCHEMA: dict[str, type[BaseModel]] = {
    "segments": NarrationVisualEpisodeScript,
    "scenes": DramaVisualScript,
    "shots": AdVisualScript,
}

_QUALITY_PROBE_SCENE_MIN_LEN = 40
_QUALITY_PROBE_ACTION_MIN_LEN = 25
_QUALITY_PROBE_UNIT_TEXT_MIN_LEN = 15

_KIND_PARSE_SCHEMA: dict[str, type[BaseModel]] = {
    "segments": NarrationEpisodeScript,
    "scenes": DramaEpisodeScript,
    "shots": AdEpisodeScript,
    "video_units": ReferenceVideoScript,
}


class PromptAuthoringTargetError(ValueError):
    """提示词编写的对象无效：该集尚无正式脚本，或 ``entry_ids`` 点名的条目不在正式脚本里。"""


class ReferenceAuthoringFailed(ValueError):
    def __init__(self, failed_units: list[dict[str, str]]) -> None:
        self.failed_units = failed_units
        super().__init__(
            "；".join(
                f"{unit['unit_id']}: {unit['message']} {unit.get('abort_reason', '')}".rstrip() for unit in failed_units
            )
        )


class PromptAuthoringUnitRejected(ValueError):
    """提示词编写调用文本模型前，正式脚本里待编写单元的现值没有通过预判。

    这是输入问题：编写会逐字保留单元台词，重试同一份输入必然再次失败，出路是在时间线上修改该单元。
    ``code`` 与 ``params`` 是任务失败的问题码与参数，界面按问题码本地化；``params["position"]`` 是该单元
    在本集视频单元里的位置（从 1 起），界面按位置称呼单元。消息面向 Agent，按单元 ID 定位。
    """

    def __init__(self, message: str, *, code: str, params: dict[str, Any]) -> None:
        super().__init__(message)
        self.code = code
        self.params = params


class UnitDurationOffTier(ValueError):
    """待编写单元的现时长不在该单元所落参考桶的生效档位内（通常是模型、分辨率或参考图状态变化让档位收窄）。

    ``unit_id`` / ``duration_seconds`` / ``tiers`` 供调用方按自己的口径转成面向用户的提示；消息面向 Agent。
    ``position`` 是单元在所判那份内容里的界面位置（从 1 起）；判的是正式剧本时为 None，调用方按正式剧本自行定位。
    """

    def __init__(
        self, message: str, *, unit_id: str, duration_seconds: int, tiers: list[int], position: int | None = None
    ) -> None:
        super().__init__(message)
        self.unit_id = unit_id
        self.duration_seconds = duration_seconds
        self.tiers = tiers
        self.position = position


class AdScriptRejected(ValueError):
    """广告/短片整份生成的产出违约：不写正式脚本、不登记资产。

    ``problems`` 逐条说明违约，供任务失败原因展示；用户可以补充附加指令后重新生成。
    """

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = tuple(problems)
        super().__init__("；".join(self.problems))


class AdScriptOverwriteRequired(ValueError):
    """整份重做会替换已有的正式脚本，而调用方未认可覆盖；携带丢失清单。"""

    def __init__(self, overwrite: FormalScriptOverwrite) -> None:
        self.overwrite = overwrite
        super().__init__("整份重做会替换已有的正式脚本，需要用户确认覆盖")


@dataclass(frozen=True, slots=True)
class PromptAuthoringTargets:
    """一次提示词编写的对象：正式脚本快照，与本次要补视觉层的条目（按剧本顺序）。"""

    kind: str
    id_field: str
    script: dict[str, Any]
    entries: tuple[dict[str, Any], ...]
    selection: PromptAuthoringSelection

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(str(entry[self.id_field]) for entry in self.entries)

    def fields_of(self, entry_id: str) -> tuple[str, ...]:
        """该条目本次写回的视觉层字段。"""
        return next(entry.fields for entry in self.selection.entries if entry.entry_id == entry_id)

    @property
    def items(self) -> list[Any]:
        """正式脚本里该骨架的全部条目，供 prompt 渲染前后文。"""
        return cast(list[Any], self.script[self.kind])


@dataclass(frozen=True, slots=True)
class ScriptPlanMaterializationReceipt:
    """内容确认把脚本规划整份转为正式剧本的回执。"""

    episode: int
    script_filename: str
    entry_ids: tuple[str, ...]
    removed: tuple[str, ...]


class ScriptPlanNotFoundError(FileNotFoundError):
    """该集的脚本规划文件缺失（含仅剩结构化前旧拆分表）：三条路线的规划加载器统一抛此类型。

    仍是 ``FileNotFoundError`` 的子类，按缺文件处理的调用方不受影响；需要把「缺规划」与
    「缺项目 / 缺其他文件」区分开的调用方按本类型捕获。
    """


@dataclass(frozen=True)
class PlanningVideoFacts:
    """剧本规划读取的视频请求事实：本路线各任务类型桶的读侧求值结果。

    分镜路线只有项目生成模式所落的那一个桶；参考生视频路线求 r2v（带可用参考图）与 i2v（无图）
    两桶，单元按可用参考图定桶后各读自己那一桶。失败对象原样保存，到需要成功事实的检查点才抛出
    :class:`VideoRequestFactsError`，问题码与预检、执行同族。
    """

    by_bucket: Mapping[VideoGenerationType, VideoRequestFacts | VideoRequestFactsFailure]

    def result(self, generation_type: VideoGenerationType) -> VideoRequestFacts | VideoRequestFactsFailure:
        return self.by_bucket[generation_type]

    def require(self, generation_type: VideoGenerationType) -> VideoRequestFacts:
        """该桶的成功事实；解析不出即抛 :class:`VideoRequestFactsError`。"""
        return require_video_request_facts(self.result(generation_type))

    def planning_durations(self, generation_type: VideoGenerationType) -> list[int]:
        """该桶规划可选的时长档位（端点固定时借规划档位）；解析不出即抛。"""
        return planning_durations(self.require(generation_type))


def _require_overwrite_acknowledged(
    targets: PromptAuthoringTargets, baseline: str | None, overwrite_revision: str | None
) -> None:
    """显式重写会替换已有视觉层内容时，覆盖令牌须与读取目标时的正式剧本指纹一致。"""
    overwritten = targets.selection.overwritten
    if overwritten and (overwrite_revision is None or fingerprint_of_revision(overwrite_revision) != baseline):
        raise PromptOverwriteRequired(PromptOverwrite(fingerprint=baseline, entries=overwritten))


class ScriptGenerator:
    """
    剧本生成器

    提示词编写按正式剧本补写待编写条目；内容确认时把脚本规划机械转为正式剧本；ad 尚无正式剧本时整份生成
    """

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
        self._script_plan_fingerprint: str | None = None
        self._script_plan_new_assets: object = None
        self._artifact_basis: ArtifactBasisDescriptor | None = None
        self._script_plan_input_claim: ArtifactInputClaim | None = None

        self.project_json = self._load_project_json()
        self.content_mode = self.project_json.get("content_mode", "narration")

    @property
    def generation_mode(self) -> str | None:
        """项目生成模式（project.json 顶层字段）：创建即定、之后不可变，不随集号变化。"""
        return self.project_json.get("generation_mode")

    def _episode_entry(self, episode: int) -> dict:
        """按集号取 project.json episodes 条目；缺失返回空 dict。"""
        return episode_ledger_entry(self.project_json, episode)

    @staticmethod
    def _entry_outline(entry: dict) -> dict:
        """账本条目的 outline 字段归一化为 dict（缺失/形状异常返回空 dict）。"""
        return ledger_outline(entry)

    @classmethod
    async def create(
        cls,
        project_path: str | Path,
        *,
        config_resolver: ConfigResolver | None = None,
    ) -> "ScriptGenerator":
        """异步工厂方法，自动从 DB 加载供应商配置创建 TextGenerator。"""
        project_name = Path(project_path).name
        generator = await TextGenerator.create(TextTaskType.SCRIPT, project_name, purpose=CallPurpose.SCRIPT_GENERATION)
        return await asyncio.to_thread(
            cls,
            project_path,
            generator,
            config_resolver=config_resolver,
        )

    def _formal_script_baseline(self, episode: int, output_filename: str | None) -> tuple[str, str | None]:
        """该集正式剧本的文件名与写入基线指纹（文件不存在时指纹为 None）。"""
        filename = output_filename or formal_script_filename(self.project_path, self.project_json, episode)
        return filename, content_fingerprint(self.project_path / "scripts" / filename)

    def _issue_landing_item_ids(self, script_data: dict[str, Any]) -> None:
        """落盘前由服务端给生成产出的条目发号（见 ``item_id_history.issue_landing_item_ids``）；须在线程里调用。"""
        pm = ProjectManager.for_project_dir(self.project_path)
        issue_landing_item_ids(self.project_path, pm.load_project(self.project_path.name), script_data)

    def _save_script(self, script_data: dict[str, Any], filename: str, **kwargs: Any) -> Path:
        """经写盘统一入口保存剧本；ProjectManager 构造时会建目录，须在线程里调用。"""
        pm = ProjectManager.for_project_dir(self.project_path)
        return pm.save_script(self.project_path.name, script_data, filename, **kwargs)

    def _load_prompt_authoring_targets(
        self,
        episode: int,
        filename: str,
        entry_ids: Iterable[str] | None,
        *,
        rewrite: bool = False,
    ) -> PromptAuthoringTargets | None:
        """读正式脚本并定出本次编写的条目；正式脚本不存在时返回 None。

        ``entry_ids`` 为空时范围是全部带待编写标记的条目，非空时是这些条目，其中任一 id 不在正式脚本里
        即抛 ``PromptAuthoringTargetError``。范围内的条目按补缺或显式重写选出（见
        ``select_prompt_authoring``）。不读脚本规划。
        """
        resolver = active_artifact_currency_resolver(self.project_path, self.project_json)
        if (
            resolver.resolve_usable_entry(ArtifactKey.episode_script(episode), artifact_path=f"scripts/{filename}")
            is None
        ):
            return None
        pm = ProjectManager.for_project_dir(self.project_path)
        try:
            script = pm.load_script_readonly(self.project_path.name, filename)
        except FileNotFoundError:
            return None
        kind = resolve_declared_kind(self.content_mode, self.generation_mode)
        raw_items, id_field, _kind = resolve_kind_items(script, kind=kind)
        if not isinstance(raw_items, list):
            raise ValueError(f"集（id={episode}）正式脚本的 {kind} 不是条目数组，无法编写提示词")
        items = [item for item in cast(list[Any], raw_items) if isinstance(item, dict) and id_field in item]
        requested = tuple(dict.fromkeys(entry_ids or ()))
        known = {str(item[id_field]) for item in items}
        unknown = [entry_id for entry_id in requested if entry_id not in known]
        if unknown:
            raise PromptAuthoringTargetError(f"entry_ids 不在集（id={episode}）正式脚本内: {unknown}")
        selection = select_prompt_authoring(items, kind=kind, id_field=id_field, entry_ids=requested, rewrite=rewrite)
        selected = {entry.entry_id for entry in selection.entries}
        entries = tuple(item for item in items if str(item[id_field]) in selected)
        return PromptAuthoringTargets(kind=kind, id_field=id_field, script=script, entries=entries, selection=selection)

    async def generate(
        self,
        episode: int,
        output_filename: str | None = None,
        *,
        instructions: str | None = None,
        entry_ids: Iterable[str] | None = None,
        rewrite: bool = False,
        overwrite_revision: str | None = None,
        rewritten_entry_ids: list[str] | None = None,
        skipped_entry_ids: list[str] | None = None,
        failed_units: list[dict[str, str]] | None = None,
        regenerate: bool = False,
        registered_assets: list[dict[str, str]] | None = None,
    ) -> Path:
        """
        为正式剧本补写视觉层（提示词编写）；ad 项目尚无正式剧本、或显式整份重做时整份生成。

        输入是正式剧本自身的内容字段，不读脚本规划。本次编写的条目只覆盖视觉层（参考生视频
        单元改写正文）并清除待编写标记，内容字段与用户字段原样保留；其余条目逐字节不变。
        没有要编写的条目时不调用文本模型、不写盘。

        Args:
            episode: 剧集编号
            output_filename: 输出文件名，默认 episode_{episode}.json。剧本一律经写盘统一入口写入
                项目 scripts/ 目录，故此参数只决定文件名、不接受目录。
            instructions: 用户输入的附加指令原文；非空时以中性「附加指令」分节追加到
                prompt 末尾（遵循强度由正文表达），所有 content_mode / 生成模式同口径。
            entry_ids: 本次编写的范围；为空时是全部待编写条目。任一 id 不在正式剧本内即报错、不落盘。
                点名只划定范围：补缺时视觉层已齐的条目照常跳过。
            rewrite: 显式重写范围内条目的全部视觉层；为 False 时补缺，只写缺失的视觉层字段。
            overwrite_revision: 调用方认可覆盖的正式剧本指纹（取自丢失清单的 ``revision``）。显式重写
                会替换已有视觉层内容时，缺失或与当前正式剧本不符即抛 ``PromptOverwriteRequired``、不调用模型。
            rewritten_entry_ids: 可选收集器；非 None 时就地填入本次编写的条目 id（剧本顺序），
                供调用方在回执里列出。ad 整份生成时保持为空。
            skipped_entry_ids: 可选收集器；非 None 时就地填入范围内因视觉层已齐而未编写的条目 id。
            regenerate: 仅 ad：整份重做，替换已有的正式剧本。已有正式剧本时，``overwrite_revision`` 须等于
                其丢失清单的 ``revision``，否则抛 ``AdScriptOverwriteRequired``、不调用模型。
            registered_assets: 可选收集器；ad 整份生成时就地填入本次登记的新资产与衍生
                （``{"type", "name"}``，衍生名写作 ``本体/衍生``）。

        Returns:
            正式剧本 JSON 文件路径
        """
        if self.generator is None:
            raise RuntimeError("TextGenerator 未初始化，请使用 ScriptGenerator.create() 工厂方法")

        if output_filename is not None and (
            not output_filename or Path(output_filename).name != output_filename or "\\" in output_filename
        ):
            raise ValueError(f"output_filename 只接受纯文件名，不允许目录或路径分隔符: {output_filename!r}")

        self._script_plan_fingerprint = None
        self._artifact_basis = None
        self._script_plan_input_claim = None
        gen_mode = self.generation_mode
        filename, formal_baseline = await asyncio.to_thread(self._formal_script_baseline, episode, output_filename)
        if regenerate:
            if self.content_mode != "ad":
                raise PromptAuthoringTargetError("整份重做只适用于广告/短片；其他创作类型请重跑脚本规划并重新确认")
            if entry_ids or rewrite:
                raise PromptAuthoringTargetError("整份重做不能与 entry_ids / rewrite 同时使用")
            overwrite = await asyncio.to_thread(formal_script_overwrite, self.project_path, self.project_json, episode)
            if overwrite is not None and fingerprint_of_revision(overwrite_revision) != overwrite.fingerprint:
                raise AdScriptOverwriteRequired(overwrite)
            prompt, schema = await self._compose_ad(episode, gen_mode, instructions)
            return await self._generate_and_save(
                prompt,
                schema,
                episode,
                output_filename,
                previous=overwrite,
                registered_assets=registered_assets,
            )
        targets = await asyncio.to_thread(
            functools.partial(self._load_prompt_authoring_targets, episode, filename, entry_ids, rewrite=rewrite)
        )
        if targets is None:
            if self.content_mode != "ad":
                raise PromptAuthoringTargetError(
                    f"集（id={episode}）尚无正式脚本：请先完成脚本规划并在 Web 端完成内容确认，确认即生成正式脚本"
                )
            if entry_ids:
                raise PromptAuthoringTargetError(f"集（id={episode}）尚无正式脚本，entry_ids 无从对应")
            prompt, schema = await self._compose_ad(episode, gen_mode, instructions)
            return await self._generate_and_save(
                prompt,
                schema,
                episode,
                output_filename,
                registered_assets=registered_assets,
            )

        _require_overwrite_acknowledged(targets, formal_baseline, overwrite_revision)
        if failed_units is not None:
            failed_units.clear()
        if rewritten_entry_ids is not None:
            rewritten_entry_ids[:] = () if targets.kind == "video_units" and self.content_mode != "ad" else targets.ids
        if skipped_entry_ids is not None:
            skipped_entry_ids[:] = targets.selection.skipped
        output_path = self.project_path / "scripts" / filename
        if not targets.entries:
            logger.info("第 %d 集没有待编写条目，未调用文本模型", episode)
            return output_path

        if targets.kind == "video_units":
            if self.content_mode == "ad":
                return await self._author_ad_reference_units(
                    episode,
                    filename,
                    targets,
                    formal_baseline=formal_baseline,
                    instructions=instructions,
                )
            return await self._author_reference_units(
                episode,
                filename,
                targets,
                formal_baseline=formal_baseline,
                instructions=instructions,
                written_ids=rewritten_entry_ids,
                failed_units=failed_units,
            )

        if targets.kind == "scenes":
            for scene in targets.entries:
                require_script_unit_admitted("scenes", scene, ignore_marker=True)
        logger.info(
            "正在为第 %d 集编写提示词（%s，%d/%d 个条目）...",
            episode,
            targets.kind,
            len(targets.entries),
            len(targets.items),
        )
        result = await self._generate_text(
            TextGenerationRequest(
                prompt=await self._build_visual_prompt(episode, targets, instructions),
                response_schema=_VISUAL_RESPONSE_SCHEMA[targets.kind],
            )
        )
        authored = self._merge_visual_layer(targets, self._parse_visual_layer(result.text, targets), episode)
        script_data = self._authored_script(episode, targets, authored)
        saved_path = await run_sync_transaction(
            self._save_script,
            script_data,
            filename,
            validate=True,
            expected_fingerprint=formal_baseline,
        )
        self._quality_probe(script_data, episode)
        logger.info("剧本已保存至 %s", saved_path)
        return saved_path

    async def _build_visual_prompt(
        self, episode: int, targets: PromptAuthoringTargets, instructions: str | None
    ) -> str:
        """分镜类条目（segments / scenes / shots）的视觉层 prompt：输入是正式剧本里这些条目的内容字段。"""
        entries = list(targets.entries)
        if targets.kind == "scenes":
            return self._build_drama_prompt_authoring_prompt(entries, episode, instructions)
        if targets.kind == "shots":
            return self._build_ad_shot_prompt_authoring_prompt(episode, targets, instructions)
        return build_narration_prompt(
            project_overview=self.project_json.get("overview", {}),
            style=self.project_json.get("style", ""),
            style_description=self.project_json.get("style_description", ""),
            characters=self._project_bucket("characters"),
            scenes=self._project_bucket("scenes"),
            props=self._project_bucket("props"),
            script_plan_segments=entries,
            aspect_ratio=self._resolve_aspect_ratio(),
            episode=episode,
            target_language=self.project_json.get("source_language") or "中文",
            instructions=instructions,
        )

    def _project_bucket(self, key: str) -> dict:
        bucket = self.project_json.get(key)
        return bucket if isinstance(bucket, dict) else {}

    def _parse_visual_layer(self, response_text: str, targets: PromptAuthoringTargets) -> list[dict]:
        """按骨架种类严格校验视觉层响应，返回逐条目视觉层 dict（id + image_prompt + video_prompt）。"""
        text = strip_json_code_fences(response_text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"提示词编写视觉层 JSON 解析失败: {e}") from e
        try:
            validated = _VISUAL_RESPONSE_SCHEMA[targets.kind].model_validate(data)
        except ValidationError as e:
            raise ValueError(f"提示词编写视觉层结构校验失败: {e}") from e
        return cast(list[dict], validated.model_dump()[targets.kind])

    @staticmethod
    def _merge_visual_layer(targets: PromptAuthoringTargets, visual_items: list[dict], episode: int) -> list[dict]:
        """把视觉层按条目 id 写回正式剧本条目：只写本次选出的视觉层字段，其余字段原样保留。

        视觉层须与本次编写的条目一一对应：缺、多、重都 fail-loud，杜绝错配与漏写。
        """
        id_field = targets.id_field
        visual_by_id: dict[str, dict] = {}
        for item in visual_items:
            entry_id = str(item[id_field])
            if entry_id in visual_by_id:
                raise ValueError(f"集（id={episode}）视觉层 {id_field} 重复: {entry_id}")
            visual_by_id[entry_id] = item
        missing = [entry_id for entry_id in targets.ids if entry_id not in visual_by_id]
        if missing:
            raise ValueError(f"集（id={episode}）视觉层缺少本次编写的条目: {missing}")
        extra = sorted(set(visual_by_id) - set(targets.ids))
        if extra:
            raise ValueError(f"集（id={episode}）视觉层含本次编写范围之外的 {id_field}: {extra}")
        return [
            {
                **entry,
                **{
                    field: visual_by_id[str(entry[id_field])][field]
                    for field in targets.fields_of(str(entry[id_field]))
                },
            }
            for entry in targets.entries
        ]

    def _authored_script(
        self,
        episode: int,
        targets: PromptAuthoringTargets,
        authored: list[dict],
        *,
        reference_unit_durations: dict[str, int] | None = None,
        facts: PlanningVideoFacts | None = None,
    ) -> dict[str, Any]:
        """把本次编写的条目按 id 放回正式剧本快照，返回待落盘的整份剧本。

        条目级元数据（清除待编写标记、needs_replan 重判、参考单元取档校验）只作用于本次编写的
        条目；剧本级字段沿用快照，metadata 只刷新 ``updated_at`` 与 ``generator``。
        """
        finished = self._add_metadata(
            {targets.kind: authored},
            episode,
            reference_unit_durations=reference_unit_durations,
            facts=facts,
        )[targets.kind]
        replacements: dict[str, dict] = {}
        for entry_id, item in zip(targets.ids, finished, strict=True):
            item[targets.id_field] = entry_id
            replacements[entry_id] = item
        script = dict(targets.script)
        script[targets.kind] = [
            replacements.get(str(item.get(targets.id_field)), item) if isinstance(item, dict) else item
            for item in targets.items
        ]
        raw_metadata = script.get("metadata")
        metadata = dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {}
        metadata["updated_at"] = datetime.now(UTC).isoformat()
        metadata["generator"] = self.generator.model if self.generator else "unknown"
        script["metadata"] = metadata
        return script

    async def _load_plan_entries_for_materialization(self, episode: int) -> tuple[ScriptPlanKind, list[dict]]:
        """按项目路线读脚本规划并过物化的准入断言，返回 (变体, 条目)。

        三条路线各自的加载器负责结构校验与规划指纹 / 输入登记的冻结；时长档位与
        发声准入按当前视频能力判定。参考生视频单元正文不按机器口径预判：单元以待编写落盘，正文由之后的
        提示词编写改写并在那里校验。
        """
        gen_mode = self.generation_mode
        if self.content_mode == "ad":
            raise ValueError("广告/短片项目没有脚本规划步骤，不适用内容确认转换")
        if gen_mode == "reference_video":
            facts = await self._fetch_video_request_facts()
            units = await run_sync_transaction(
                self._load_reference_script_plan, episode, self._reference_migration_durations(facts)
            )
            await asyncio.to_thread(functools.partial(self._assert_reference_script_plan_durations, units, facts=facts))
            return "reference_video", units
        if self.content_mode != "narration":
            content = await asyncio.to_thread(self._load_drama_script_plan_content, episode)
            raw_scenes = content.get("scenes")
            scenes: list = raw_scenes if isinstance(raw_scenes, list) else []
            for scene in scenes:
                require_script_unit_admitted("scenes", scene)
            await self._assert_drama_script_plan_durations(scenes)
            return "drama", scenes
        supported = self._storyboard_planning_durations(await self._fetch_video_request_facts())
        return "narration", await asyncio.to_thread(self._load_narration_script_plan, episode, supported)

    async def materialize_script_plan(
        self,
        episode: int,
        *,
        expected_plan_revision: str,
        expected_script_fingerprint: str | None,
        project_update: Callable[[dict[str, Any]], None],
        validate_locked: Callable[[dict[str, Any], Path], None] | None = None,
    ) -> ScriptPlanMaterializationReceipt:
        """内容确认时把脚本规划整份转为正式剧本：旧剧本（若有）整份被替换，不沿用任何条目。

        条目编号在落盘时一律另发新号，编号只分配、不复用（``docs/adr/0090``）。

        投影见 ``lib.script.script_document.build_materialized_script``。``expected_plan_revision`` 是确认记录的脚本规划指纹，加载到的
        规划不是这份即抛 ``ScriptPlanWriteConflict``；``expected_script_fingerprint`` 是调用方认可覆盖时
        看到的正式剧本指纹（无剧本为 None），落盘时不匹配抛 ``ScriptWriteConflict``。旧剧本的条目名下产物
        随本次写入撤登记，历史版本与当前媒体文件移进项目回收站。``project_update`` 与剧本在同一写事务内修改
        project.json，确认记录借此与正式剧本一起落盘。
        """
        self._script_plan_fingerprint = None
        self._script_plan_new_assets = None
        self._artifact_basis = None
        self._script_plan_input_claim = None
        plan_kind, plan_entries = await self._load_plan_entries_for_materialization(episode)
        loaded_revision = cast(str | None, self._script_plan_fingerprint)
        if loaded_revision != expected_plan_revision:
            raise ScriptPlanWriteConflict(expected=expected_plan_revision, actual=loaded_revision, current_content=None)
        new_assets = resolve_new_assets(self.project_json, self._script_plan_new_assets)
        plan_entries = new_assets.rewrite_entries(plan_entries)
        registered_project = copy.deepcopy(self.project_json)
        new_assets.apply_to_project(registered_project)
        unregistered = unregistered_references(registered_project, plan_entries, id_field=entry_id_field(plan_kind))
        if unregistered:
            raise UnregisteredReferencesError(unregistered)

        plan_path = script_plan_path(self.project_path, self.project_json, episode)
        if plan_path is None:
            raise FileNotFoundError(f"集（id={episode}）不适用脚本规划")

        def update_project(project: dict[str, Any]) -> None:
            if validate_locked is not None:
                validate_locked(project, plan_path)
            if resolve_new_assets(project, self._script_plan_new_assets) != new_assets:
                raise ValueError("本集新增资产的处理结果已变化，请刷新后重新确认")
            new_assets.apply_to_project(project)
            project_update(project)

        filename = await asyncio.to_thread(formal_script_filename, self.project_path, self.project_json, episode)
        script_data = build_materialized_script(
            self.project_json, episode, plan_kind=plan_kind, plan_entries=plan_entries, title=None
        )
        items_key = plan_variant(plan_kind).skeleton_kind
        id_field = entry_id_field(plan_kind)
        previous = await asyncio.to_thread(formal_script_overwrite, self.project_path, self.project_json, episode)
        previous_ids = tuple(entry.entry_id for entry in previous.entries) if previous is not None else ()
        claim = self._script_plan_input_claim

        def _commit() -> None:
            with formal_script_plan_lock(self.project_path, episode, plan_path):
                current_revision = content_fingerprint(plan_path)
                if current_revision != expected_plan_revision:
                    raise ScriptPlanWriteConflict(
                        expected=expected_plan_revision, actual=current_revision, current_content=None
                    )
                if claim is not None:
                    assert_current_artifact_input_claims_usable(self.project_path, (claim,))
                self._issue_landing_item_ids(script_data)
                self._save_script(
                    script_data,
                    filename,
                    validate=True,
                    expected_fingerprint=expected_script_fingerprint,
                    project_update=update_project,
                )

        await run_sync_transaction(_commit)
        entry_ids = tuple(str(item[id_field]) for item in script_data[items_key])
        removed = tuple(entry_id for entry_id in previous_ids if entry_id not in entry_ids)
        logger.info(
            "第 %d 集已在内容确认时按脚本规划整份转为正式剧本（%d 条，移出旧条目 %d 条）",
            episode,
            len(entry_ids),
            len(removed),
        )
        return ScriptPlanMaterializationReceipt(
            episode=episode, script_filename=filename, entry_ids=entry_ids, removed=removed
        )

    async def _assert_drama_script_plan_durations(self, content_scenes: list) -> None:
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
        supported = self._storyboard_planning_durations(await self._fetch_video_request_facts())
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

    def _build_drama_prompt_authoring_prompt(
        self, content_scenes: list, episode: int, instructions: str | None = None
    ) -> str:
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
            target_language=self.project_json.get("source_language") or "中文",
            characters=characters,
            scenes=scenes,
            props=props,
            instructions=instructions,
        )

    async def _generate_and_save(
        self,
        prompt: str,
        schema: type,
        episode: int,
        output_filename: str | None,
        *,
        previous: FormalScriptOverwrite | None = None,
        registered_assets: list[dict[str, str]] | None = None,
    ) -> Path:
        """ad 整份生成的尾段：调用 TextBackend → 解析校验 → 落实新增资产 → 补元数据 → 经写盘统一入口保存。

        产出违约（解析不出、结构不合、引用落不到资产上、新增资产的处理决定解析不出、条目为空）一律抛
        ``AdScriptRejected``：不写正式脚本、不登记资产、不落草稿。``needs_replan`` 单元照常写入。
        ``previous`` 是用户认可覆盖的旧正式剧本：以它的指纹为写入基线，旧条目名下的产物随写入撤登记。
        条目编号在落盘时由服务端另发（编号只分配、不复用）。
        """
        assert self.generator is not None
        if previous is None:
            filename, formal_baseline = await asyncio.to_thread(self._formal_script_baseline, episode, output_filename)
        else:
            filename = output_filename or await asyncio.to_thread(
                formal_script_filename, self.project_path, self.project_json, episode
            )
            formal_baseline = previous.fingerprint
        logger.info("正在生成第 %d 集剧本...", episode)
        result = await self._generate_text(
            TextGenerationRequest(
                prompt=prompt,
                response_schema=schema,
            )
        )
        response_text = result.text
        if self.generation_mode == "reference_video":
            script_data, new_assets = self._parse_ad_reference_response(response_text, episode)
        else:
            script_data, new_assets = self._parse_ad_storyboard_response(response_text, episode)
        items, id_field, _kind = resolve_kind_items(script_data)
        if not isinstance(items, list) or not items:
            raise AdScriptRejected([f"第 {episode} 集的 AI 生成脚本没有任何条目"])
        registered_project = self._settle_ad_new_assets(script_data, new_assets, id_field)
        script_data = self._add_metadata(script_data, episode)
        structure = validate_script_structure(script_data)
        if not structure.valid:
            raise AdScriptRejected(structure.errors)
        self._freeze_ad_artifact_basis(episode, registered_project)

        def _issue_and_save() -> Path:
            self._issue_landing_item_ids(script_data)
            return self._save_script(
                script_data,
                filename,
                validate=True,
                artifact_basis=self._artifact_basis,
                expected_fingerprint=formal_baseline,
                project_update=new_assets.apply_to_project,
            )

        output_path = await run_sync_transaction(_issue_and_save)
        if registered_assets is not None:
            registered_assets[:] = new_assets.registered()
        self._quality_probe(script_data, episode)
        logger.info("剧本已保存至 %s", output_path)
        return output_path

    def _settle_ad_new_assets(
        self, script_data: dict[str, Any], new_assets: NewAssetResolution, id_field: str
    ) -> dict[str, Any]:
        """按处理决定改写剧本条目里的引用，返回登记本次新增资产后的项目视图。

        改写后的引用须已登记或是本次登记的新增资产 / 衍生；商品不在新增范围内，``products_in_shot``
        只认已登记商品。有落不到资产上的引用即抛 ``AdScriptRejected``。
        """
        items, _id_field, kind = resolve_kind_items(script_data)
        if not all(isinstance(item, dict) for item in items):
            raise AdScriptRejected([f"{kind} 里有不是对象的条目"])
        entries = new_assets.rewrite_entries(items)
        script_data[kind] = entries
        registered_project = copy.deepcopy(self.project_json)
        new_assets.apply_to_project(registered_project)
        problems = [
            f"{reference.item_id} 引用了未登记、也不在本次新增资产里的名字：{'、'.join(reference.names)}"
            for reference in unregistered_references(registered_project, entries, id_field=id_field)
        ]
        products = registered_project.get("products")
        known_products = (
            {asset_name_comparison_key(str(name)) for name in products} if isinstance(products, Mapping) else set()
        )
        for entry in entries:
            named = entry.get("products_in_shot")
            unknown = [
                name
                for name in (named if isinstance(named, list) else ())
                if isinstance(name, str) and asset_name_comparison_key(name) not in known_products
            ]
            if unknown:
                problems.append(f"{entry.get(id_field)} 引用了未登记的商品：{'、'.join(unknown)}")
        if problems:
            raise AdScriptRejected(problems)
        return registered_project

    async def _compose_ad(self, episode: int, gen_mode: str | None, instructions: str | None) -> tuple[str, type]:
        """ad 分支的 (prompt, response_schema) 构造，generate/build_prompt 共用。

        reference 路径不消费供应商能力（unit 编排时长不按供应商档位量化），跳过能力查询；
        storyboard 路径解析一次 supported_durations，prompt 时长枚举与 schema enum 同源。
        """
        if gen_mode == "reference_video":
            supported = None
            schema: type = AdReferenceFlatScript
        else:
            supported = self._storyboard_planning_durations(await self._fetch_video_request_facts())
            schema = with_new_assets_field(build_episode_script_model("ad", supported))
        return self._build_ad_prompt(episode, gen_mode, supported, instructions), schema

    def _build_ad_prompt(
        self, episode: int, gen_mode: str | None, supported: list[int] | None, instructions: str | None
    ) -> str:
        """构建广告/短片 prompt：brief + 商品信息 + 审定配比表，不读 script_plan 中间文件。

        storyboard 路径把 supported_durations 作为单分镜时长枚举写进 prompt；参考生视频
        直接输出统一引用语法 video unit，八段式只作为内容规划而不持久化。
        """
        direct_inputs = project_ad_episode_script_inputs(episode, project=self.project_json)
        common = {
            **self._ad_prompt_common(episode, instructions),
            "target_duration": direct_inputs["target_duration"],
            "asset_registry": {
                key: bucket if isinstance(bucket := self.project_json.get(key), dict) else {}
                for key in ("characters", "scenes", "props")
            },
        }
        if gen_mode == "reference_video":
            return build_ad_reference_prompt(**common)
        return build_ad_prompt(
            **common,
            generation_mode=gen_mode,
            supported_durations=supported,
            speech_rate_override=cast(float | None, direct_inputs["speech_rate_override"]),
        )

    def prompt_overwrite(
        self,
        episode: int,
        *,
        entry_ids: Iterable[str] | None = None,
        rewrite: bool = False,
        overwrite_revision: str | None = None,
    ) -> None:
        """提交前的覆盖预检：与 ``generate`` 同一套对象选择，未认可的覆盖抛 ``PromptOverwriteRequired``。

        点名的条目不在正式剧本里同样抛 ``PromptAuthoringTargetError``；尚无正式剧本时不检查。
        """
        filename = formal_script_filename(self.project_path, self.project_json, episode)
        baseline = content_fingerprint(self.project_path / "scripts" / filename)
        targets = self._load_prompt_authoring_targets(episode, filename, entry_ids, rewrite=rewrite)
        if targets is not None:
            _require_overwrite_acknowledged(targets, baseline, overwrite_revision)

    async def build_prompt(
        self,
        episode: int,
        *,
        instructions: str | None = None,
        entry_ids: Iterable[str] | None = None,
        rewrite: bool = False,
    ) -> str:
        """
        构建 Prompt（用于 dry-run 模式）

        与 `generate()` 同一套对象选择：ad 尚无正式剧本时渲染整份生成 prompt，否则只渲染本次
        要编写的条目（``entry_ids`` 或全部待编写条目）；没有要编写的条目时回答那句事实，而不是
        渲染一份不会被发出的空 prompt。``instructions`` 的注入口径与 `generate()` 一致。
        dry-run 恒以该集绑定的正式剧本为准：它不落盘，也就没有 ``output_filename`` 可言。
        """

        def load_targets() -> PromptAuthoringTargets | None:
            filename = formal_script_filename(self.project_path, self.project_json, episode)
            return self._load_prompt_authoring_targets(episode, filename, entry_ids, rewrite=rewrite)

        targets = await asyncio.to_thread(load_targets)
        if targets is None:
            if self.content_mode != "ad":
                raise PromptAuthoringTargetError(
                    f"集（id={episode}）尚无正式脚本：请先完成脚本规划并在 Web 端完成内容确认，确认即生成正式脚本"
                )
            if entry_ids:
                raise PromptAuthoringTargetError(f"集（id={episode}）尚无正式脚本，entry_ids 无从对应")
            prompt, _schema = await self._compose_ad(episode, self.generation_mode, instructions)
            return prompt
        if not targets.entries:
            return _NO_ENTRY_TO_AUTHOR_NOTE
        if targets.kind != "video_units":
            return await self._build_visual_prompt(episode, targets, instructions)
        if self.content_mode == "ad":
            return self._build_ad_reference_prompt_authoring_prompt(episode, targets, instructions)
        facts = await self._fetch_video_request_facts()
        return self._build_reference_prompt_authoring_prompt(episode, targets, facts, instructions)

    @property
    def _storyboard_bucket(self) -> VideoGenerationType:
        """分镜路线（旁白 / 剧情 / 广告分镜）按项目生成模式所落的任务类型桶。"""
        return video_bucket_for_generation_mode(self.generation_mode)

    async def _fetch_video_request_facts(self) -> PlanningVideoFacts:
        """按路线逐桶求值读侧视频请求事实，与预检、执行同一份收窄结果。

        身份按当前配置解析（经桶能力闸），解析不出的桶保存失败对象而不抛出、不回退到项目自报
        身份或 registry：退到别的来源写出的时长与参考图数量，执行期照样被同一道闸拒掉，报错带
        问题码与修复指引，比先写一份必败的剧本更省事。参考生视频路线求 r2v 与 i2v 两桶，单元按
        可用参考图定桶后各读自己那一桶。
        """
        resolver = self.config_resolver or ConfigResolver(async_session_factory)
        route: VideoRoute
        buckets: tuple[VideoGenerationType, ...]
        if self.generation_mode == "reference_video":
            route, buckets = "reference_video", ("r2v", "i2v")
        else:
            route, buckets = "storyboard", (self._storyboard_bucket,)
        by_bucket: dict[VideoGenerationType, VideoRequestFacts | VideoRequestFactsFailure] = {}
        for bucket in buckets:
            by_bucket[bucket] = await read_video_request_facts(
                self.project_json,
                route=route,
                generation_type=bucket,
                resolver=resolver,
            )
        return PlanningVideoFacts(by_bucket=by_bucket)

    def _storyboard_planning_durations(self, facts: PlanningVideoFacts) -> list[int]:
        """分镜路线规划可选的时长档位：交给 prompt / 动态 schema 之前已按分辨率收窄。

        ``supported_durations`` 是型号的时长全集，不含「分辨率↔时长」联动约束；不收窄的话设了
        1080p 的 Veo 项目的剧本会产出 4/6 秒分镜，到视频入队时才被 backend 拒，用户已无统一纠正
        入口。解析不出即抛 :class:`VideoRequestFactsError`。
        """
        return facts.planning_durations(self._storyboard_bucket)

    def _unit_duration_off_every_tier(
        self, duration: int, *, facts: PlanningVideoFacts, generation_type: VideoGenerationType
    ) -> list[int] | None:
        """时长在带图与不带图两桶档位下都出局时返回该单元所落桶的档位，任一合法则返回 None。

        prompt_authoring 可以给 unit 增删 `@[名称]` 提及，只在其中一桶出局的时长仍可能落地合法——
        提前判死会拦掉本会成功的生成。两桶都出局才是与参考图无关的必然失败
        （模型或分辨率配置变化所致），可以在付费调用前拦下。解析不出的桶按出局计。
        """
        for candidate in ("r2v", "i2v"):
            if self._unit_duration_off_tier(duration, facts=facts, generation_type=candidate) is None:
                return None
        return facts.planning_durations(generation_type)

    def _unit_duration_off_tier(
        self, duration: int, *, facts: PlanningVideoFacts, generation_type: VideoGenerationType
    ) -> list[int] | None:
        """时长落在该桶生效档位之外时返回该档位集，落在内则返回 None。

        生效档位逐 unit 算：单元此刻有可用参考图走 r2v，没有走 i2v；该桶事实不可解析时返回空档位。
        """
        result = facts.result(generation_type)
        if not isinstance(result, VideoRequestFacts):
            return []
        tiers = planning_durations(result)
        return None if resolve_duration_slot(duration, tiers).seconds == duration else tiers

    def _hydrate_reference_units(self, units: Sequence[dict]) -> tuple[ReferenceUnitHydration, ...]:
        """按执行侧同款判据（文件存在且产物清单认领）逐单元水合声明引用，得出各单元此刻所落的桶。

        与内容确认面板、整批准入同一份判据：正文带 ``@`` 但引用缺图或图未被清单认领的单元落 i2v。
        """
        return hydrate_reference_units(self.project_json, self.project_path, units)

    @staticmethod
    def _reference_migration_durations(facts: PlanningVideoFacts) -> list[int] | None:
        """结构收编用的时长全集：r2v 与 i2v 两桶声明全集的并集；任一桶解析不出时为 None，只做结构收编。"""
        return reference_migration_durations(facts.result("r2v"), facts.result("i2v"))

    def _resolve_aspect_ratio(self) -> str:
        """解析项目的 aspect_ratio，向后兼容。narration / ad 默认竖屏（ad 与创建向导默认一致）。"""
        if "aspect_ratio" in self.project_json and isinstance(self.project_json["aspect_ratio"], str):
            return self.project_json["aspect_ratio"]
        return "9:16" if self.content_mode in ("narration", "ad") else "16:9"

    @staticmethod
    def _resolve_max_refs(facts: PlanningVideoFacts) -> int | None:
        """带可用参考图的单元（r2v 桶）每请求可携带的参考图上限，取自该桶的视频请求事实。

        语义约定：None 为「未收录」（条目与通道都没有声明，上层不在 prompt 写硬性数量约束，且 executor 跳过裁剪）；
        0 为不接受参考图（这类模型进不了 r2v 桶），executor 同样不裁剪，交给执行时的校验拒绝。r2v 桶解析不出时
        按未声明处理——带图单元本身会在各检查点因事实缺失被拦下，不在此另起来源。
        """
        result = facts.result("r2v")
        return result.max_reference_images if isinstance(result, VideoRequestFacts) else None

    def _load_project_json(self) -> dict:
        """加载 project.json"""
        path = self.project_path / "project.json"
        if not path.exists():
            raise FileNotFoundError(f"未找到 project.json: {path}")

        with open(path, encoding="utf-8") as f:
            return json.load(f)

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

    async def _generate_text(self, request: TextGenerationRequest, *, require_complete: bool = False) -> Any:
        """Call the paid text provider after one shared formal-input recheck."""

        assert self.generator is not None
        if self._script_plan_input_claim is not None:
            await asyncio.to_thread(
                assert_current_artifact_input_claims_usable,
                self.project_path,
                (self._script_plan_input_claim,),
            )
        if require_complete:
            return await self.generator.generate(request, project_name=self.project_path.name, require_complete=True)
        return await self.generator.generate(request, project_name=self.project_path.name)

    def _freeze_ad_artifact_basis(self, episode: int, project: Mapping[str, Any]) -> None:
        """Freeze the ad-specific canonical basis from the project the generated script is written against."""
        basis = build_ad_episode_script_basis(episode, project=project)
        self._artifact_basis = ArtifactBasisDescriptor.from_basis(basis)

    def _load_script_plan(self, episode: int) -> str:
        """加载 drama 形状两段式的脚本规划结构化中间文件原始文本。

        每种模式只对应一个期望文件，缺失时显式报错并指明期望路径——不降级改读
        其他模式的中间文件（静默 fallback 会让剧本基于错误模式的中间产物生成）。
        本方法只服务 drama 形状的两段式结构化内容；narration 另经 ``_load_narration_script_plan``、
        reference_video 另经 ``_load_reference_script_plan``。
        """
        drafts_path = episode_drafts_dir(self.project_path, episode)
        script_plan_path = drafts_path / SCRIPT_PLAN_FILENAMES.get(self.content_mode, SCRIPT_PLAN_FILENAMES["drama"])

        if not script_plan_path.exists():
            raise ScriptPlanNotFoundError(
                f"未找到脚本规划中间文件: {script_plan_path}；content_mode={self.content_mode} 期望该文件，请先完成本集脚本规划"
            )

        raw = script_plan_path.read_bytes()
        text = raw.decode("utf-8")
        self._freeze_script_plan_input_claim(episode, script_plan_path, content_digest=hashlib.sha256(raw).hexdigest())
        return text

    def _load_reference_script_plan(
        self,
        episode: int,
        supported_durations: list[int] | None,
    ) -> list[dict]:
        """加载并校验 reference_video script_plan 结构化中间文件 ``script_plan_reference_units.json``。

        返回 unit dict 列表（unit_id / text / duration_seconds / source_text），供内容确认整份转为正式剧本。
        校验：结构合法（``ReferenceScriptPlanDraft``）、units 非空、unit_id 唯一、
        ``supported_durations`` 只用于存量结构收编，时长准入随后按逐单元事实校验。
        仅存在结构化前的旧 ``script_plan_reference_units.md`` 时给
        明确的「重跑拆分」报错——不写 md→json 迁移器（旧 md 产于结构化中间态引入前，
        与 narration 同决策）。
        """
        drafts_path = episode_drafts_dir(self.project_path, episode)
        script_plan_json = drafts_path / REFERENCE_VIDEO_SCRIPT_PLAN_FILENAME
        if not script_plan_json.exists():
            legacy_md = drafts_path / REFERENCE_VIDEO_SCRIPT_PLAN_LEGACY_FILENAME
            if legacy_md.exists():
                raise ScriptPlanNotFoundError(
                    f"仅找到结构化前的旧拆分表 {legacy_md}，未找到 {script_plan_json}；"
                    f"请调用 generate_script_plan 产出结构化 {REFERENCE_VIDEO_SCRIPT_PLAN_FILENAME}"
                )
            raise ScriptPlanNotFoundError(
                f"未找到脚本规划中间文件: {script_plan_json}；generation_mode=reference_video 期望该文件，"
                "请先完成 video_unit 拆分"
            )

        pm = ProjectManager.for_project_dir(self.project_path)
        with pm.file_lock(script_plan_json):
            try:
                raw = json.loads(script_plan_json.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                raise ValueError(f"script_plan_reference_units.json 解析失败: {e}") from e

            migrate_script_plan_draft_in_place(
                self.project_path,
                raw,
                episode=episode,
                update_project=lambda mutate: pm.update_project(self.project_path.name, mutate),
                supported_durations=supported_durations,
            )
            self._script_plan_fingerprint = content_fingerprint_of_data(raw)
            self._script_plan_new_assets = raw.get(NEW_ASSETS_FIELD) if isinstance(raw, dict) else None
            self._freeze_script_plan_input_claim(
                episode,
                script_plan_json,
                content_digest=sha256_file(script_plan_json),
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

        rewritten_ids = [str(rewrite_episode_prefix(uid, episode)) for uid in ids]
        rewritten_dupes = sorted(uid for uid, count in Counter(rewritten_ids).items() if count > 1)
        if rewritten_dupes:
            raise ValueError(
                f"script_plan_reference_units.json unit_id 改写到 episode={episode} 后重复: {rewritten_dupes}"
            )

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
        drafts_path = episode_drafts_dir(self.project_path, episode)
        narration_json = SCRIPT_PLAN_FILENAMES["narration"]
        script_plan_json = drafts_path / narration_json
        if not script_plan_json.exists():
            legacy_md = drafts_path / SCRIPT_PLAN_LEGACY_FILENAMES["narration"][0]
            if legacy_md.exists():
                raise ScriptPlanNotFoundError(
                    f"仅找到结构化前的旧拆分表 {legacy_md}，未找到 {script_plan_json}；"
                    f"请调用 generate_script_plan 产出结构化 {narration_json}"
                )
            raise ScriptPlanNotFoundError(
                f"未找到脚本规划中间文件: {script_plan_json}；content_mode=narration 期望该文件，请先完成分镜拆分"
            )

        raw_bytes = script_plan_json.read_bytes()
        try:
            raw = json.loads(raw_bytes.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"script_plan_segments.json 解析失败: {e}") from e
        self._script_plan_fingerprint = content_fingerprint_of_data(raw)
        self._script_plan_new_assets = raw.get(NEW_ASSETS_FIELD) if isinstance(raw, dict) else None
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
        raw = self._load_script_plan(episode)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"脚本规划内容文件不是合法 JSON（drama script_plan 应为结构化内容）: {e}") from e
        if not isinstance(data, dict):
            raise ValueError("脚本规划内容文件结构异常：顶层应为对象 {title, scenes}")
        self._script_plan_fingerprint = content_fingerprint_of_data(data)
        self._script_plan_new_assets = data.get(NEW_ASSETS_FIELD)
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
        rewritten_ids = [str(rewrite_episode_prefix(sid, episode)) for sid in scene_ids]
        rewritten_dupes = sorted(sid for sid, count in Counter(rewritten_ids).items() if count > 1)
        if rewritten_dupes:
            raise ValueError(f"脚本规划内容文件 scene_id 改写到 episode={episode} 后重复: {rewritten_dupes}")
        return data

    def _assert_reference_script_plan_durations(
        self, script_plan_units: list[dict], *, facts: PlanningVideoFacts
    ) -> None:
        """转为正式剧本前判脚本规划已确认的单元时长仍在当前生效档位内；正文由之后的提示词编写改写并校验。

        单元按此刻可用的参考图定桶（无引用、引用缺图或图未被清单认领落 i2v），须有所落桶的事实。越档时抛
        ``UnitDurationOffTier``，按脚本规划里的单元位置称呼（与内容确认面板的「视频单元 N」同口径），不带内部单元 ID。
        """
        hydrations = self._hydrate_reference_units(script_plan_units)
        for position, (unit, hydration) in enumerate(zip(script_plan_units, hydrations, strict=True), start=1):
            bucket = hydration.hydrated_generation_type
            facts.require(bucket)
            duration = int(unit["duration_seconds"])
            off_tiers = self._unit_duration_off_every_tier(duration, facts=facts, generation_type=bucket)
            if off_tiers is not None:
                tiers = sorted(set(off_tiers))
                raise UnitDurationOffTier(
                    f"视频单元 {position} 已确认时长 {duration}s 不在当前生效档位 {tiers} 内；"
                    "通常是模型或分辨率配置变化让档位收窄导致，请调整配置回原档位，"
                    "或在脚本规划里把该单元时长改到档位内（也可重新拆分该集 script_plan）后重新完成内容确认",
                    unit_id=str(unit["unit_id"]),
                    duration_seconds=duration,
                    tiers=tiers,
                    position=position,
                )

    def _assert_reference_units_authorable(self, units: list[dict], *, facts: PlanningVideoFacts) -> None:
        """付费编写前验证所选单元的时长档位和正文。"""
        for unit, hydration in zip(units, self._hydrate_reference_units(units), strict=True):
            bucket = hydration.hydrated_generation_type
            facts.require(bucket)
            duration = int(unit["duration_seconds"])
            off_tiers = self._unit_duration_off_every_tier(duration, facts=facts, generation_type=bucket)
            if off_tiers is not None:
                tiers = sorted(set(off_tiers))
                raise UnitDurationOffTier(
                    f"unit {unit['unit_id']} 时长 {duration}s 不在当前生效档位 {tiers} 内；"
                    "通常是模型或分辨率配置变化让档位收窄导致，请调整配置回原档位，或在时间线上把该单元时长改到档位内",
                    unit_id=str(unit["unit_id"]),
                    duration_seconds=duration,
                    tiers=tiers,
                )
        self._assert_reference_unit_text_valid(units, max_refs=self._resolve_max_refs(facts))

    def _unit_rejection(
        self, targets: PromptAuthoringTargets, exc: DraftViolation
    ) -> PromptAuthoringUnitRejected | None:
        """把正式脚本单元的预判违约转成任务失败的问题码：台词念不完带上估算秒数，其余违约只指出单元。

        违约定位不到正式脚本里的单元时返回 None。
        """
        first = violation_items(exc)[0]
        found = find_item(targets.script, first.item_id) if first.item_id else None
        if found is None:
            return None
        position, unit = found
        params: dict[str, Any] = {"unit_id": first.item_id, "position": position}
        if first.code == "dialogue_overload":
            spoken = dialogue_spoken_seconds(
                str(unit.get("text") or ""),
                self.project_json.get("source_language"),
                project_speech_rate_override(self.project_json),
            )
            params.update(spoken_seconds=f"{spoken:.1f}", duration_seconds=int(unit["duration_seconds"]))
            return PromptAuthoringUnitRejected(str(exc), code="prompt_authoring_dialogue_overload", params=params)
        return PromptAuthoringUnitRejected(str(exc), code="prompt_authoring_unit_invalid", params=params)

    @staticmethod
    def _duration_rejection(
        targets: PromptAuthoringTargets, exc: UnitDurationOffTier
    ) -> PromptAuthoringUnitRejected | None:
        """单元时长越出生效档位转成修改输入：按界面位置称呼单元，带上现时长与可选档位。定位不到时返回 None。"""
        found = find_item(targets.script, exc.unit_id)
        if found is None:
            return None
        params: dict[str, Any] = {
            "unit_id": exc.unit_id,
            "position": found[0],
            "duration_seconds": exc.duration_seconds,
            "tiers": " / ".join(str(tier) for tier in exc.tiers),
        }
        return PromptAuthoringUnitRejected(str(exc), code="prompt_authoring_duration_off_tier", params=params)

    def _assert_reference_unit_text_valid(self, units: list[dict], *, max_refs: int | None) -> None:
        """按机器产物的严格口径预判正式脚本各 unit 正文，违约时把定位与出路指回时间线。

        与 ``_merge_reference_visual`` 用的是同一个 ``validate_unit_text``：同一把尺量两处，
        避免「script_plan 放行、prompt_authoring 必拒」的死角。此处只判、不取派生结果——参考图是执行期从正文
        派生的，落盘物只有正文本身。

        台词口播时长同样在此复判：拆分工具只在产出当时判过一次，内容确认时改短 unit 时长或
        补写台词都能绕开它，而 prompt_authoring 逐字保留台词、之后再无口播量校验——不复判就会让念不完的
        unit 一路落盘。
        """
        source_language = self.project_json.get("source_language")
        speech_rate_override = project_speech_rate_override(self.project_json)
        hint = "这段正文来自正式脚本，提示词编写会逐字保留其中的台词，请先在时间线上修正该单元的正文或时长"
        for unit in units:
            label = f"正式脚本 的 unit {unit['unit_id']}"
            text = str(unit.get("text") or "")
            try:
                validate_unit_text(
                    label,
                    text,
                    self.project_json,
                    unit_id=str(unit["unit_id"]),
                    max_refs=max_refs,
                    tolerated_speakers=dialogue_speakers(text),
                )
                validate_dialogue_load(
                    label, text, int(unit["duration_seconds"]), source_language, speech_rate_override
                )
            except DraftViolation as exc:
                enriched = [
                    DraftViolation(
                        f"{item}；{hint}",
                        code=item.code,
                        label=label,
                        line=item.line,
                        locations=item.locations,
                        reason=item.reason,
                        action=item.action,
                        item_id=str(unit["unit_id"]),
                        params=item.params,
                    )
                    for item in violation_items(exc)
                ]
                if len(enriched) == 1:
                    raise enriched[0] from exc
                raise DraftViolations(enriched) from exc

    def _build_reference_prompt_authoring_prompt(
        self, episode: int, targets: PromptAuthoringTargets, facts: PlanningVideoFacts, instructions: str | None
    ) -> str:
        return build_reference_video_prompt(
            project_overview=self.project_json.get("overview", {}),
            style=self.project_json.get("style", ""),
            style_description=self.project_json.get("style_description", ""),
            characters=self._project_bucket("characters"),
            scenes=self._project_bucket("scenes"),
            props=self._project_bucket("props"),
            script_plan_units=list(targets.entries),
            max_refs=self._resolve_max_refs(facts),
            aspect_ratio=self._resolve_aspect_ratio(),
            episode=episode,
            target_language=self.project_json.get("source_language") or "中文",
            instructions=instructions,
        )

    @staticmethod
    def _unit_durations(targets: PromptAuthoringTargets) -> dict[str, int]:
        """待编写单元的时长：编写不改时长，取档校验按落地正文对着这份值重算。"""
        return {str(unit["unit_id"]): int(unit["duration_seconds"]) for unit in targets.entries}

    async def _author_reference_units(
        self,
        episode: int,
        filename: str,
        targets: PromptAuthoringTargets,
        *,
        formal_baseline: str | None,
        instructions: str | None,
        written_ids: list[str] | None,
        failed_units: list[dict[str, str]] | None,
    ) -> Path:
        facts = await self._fetch_video_request_facts()
        capability_inputs = plan_capability_inputs(self.project_json)
        units = list(targets.entries)
        path = self.project_path / "scripts" / filename

        def preflight() -> None:
            ProjectManager.require_filename_episode_consistency(targets.script, filename)
            structure = validate_script_structure(targets.script)
            if not structure.valid:
                raise PromptAuthoringTargetError("；".join(structure.errors))
            ids = [unit["unit_id"] for unit in targets.items]
            if len(set(ids)) != len(ids) or any(
                not uid.startswith(f"E{episode}U") or item_id_number(uid, episode) is None for uid in ids
            ):
                raise PromptAuthoringTargetError("正式脚本的 unit_id 重复或格式错误")
            try:
                self._assert_reference_units_authorable(units, facts=facts)
            except DraftViolation as exc:
                rejection = self._unit_rejection(targets, exc)
                if rejection is None:
                    raise
                raise rejection from exc
            except UnitDurationOffTier as exc:
                rejection = self._duration_rejection(targets, exc)
                if rejection is None:
                    raise
                raise rejection from exc

        await asyncio.to_thread(preflight)
        written: set[str] = set()
        failures: dict[str, dict[str, str]] = {}
        round_targets = targets
        reason = ""
        conflict = "formal_revision_conflict: 正式脚本已变化，已停止写回"
        caps_changed = "视频模型配置已变化，已停止写回，请重新发起编写"

        def require_current_capabilities(project: dict[str, Any]) -> None:
            if plan_capability_inputs(project) != capability_inputs:
                raise PromptAuthoringTargetError(caps_changed)

        def validate_response(
            selected: PromptAuthoringTargets, response: str
        ) -> tuple[dict[str, dict], dict[str, dict[str, str]]]:
            require_current_capabilities(self._load_project_json())
            return self._reference_response_units(selected, response, facts)

        try:
            for attempt in range(2):
                if await asyncio.to_thread(content_fingerprint, path) != formal_baseline:
                    reason = conflict
                    break
                require_current_capabilities(await asyncio.to_thread(self._load_project_json))
                prompt = self._build_reference_prompt_authoring_prompt(episode, round_targets, facts, instructions)
                if attempt:
                    feedback = [failures[uid] for uid in round_targets.ids]
                    prompt += "\n请修正以下单元：" + json.dumps(feedback, ensure_ascii=False)
                result = await self._generate_text(
                    TextGenerationRequest(
                        prompt=prompt,
                        response_schema=ReferenceAuthoringResponse,
                    ),
                    require_complete=True,
                )

                if await run_noninterruptible_async(self._fetch_video_request_facts()) != facts:
                    raise PromptAuthoringTargetError(caps_changed)
                accepted, round_failures = await run_noninterruptible_sync(
                    validate_response, round_targets, result.text
                )
                failures.update(round_failures)
                if not accepted and await run_noninterruptible_sync(content_fingerprint, path) != formal_baseline:
                    reason = conflict
                    break
                if accepted:
                    script, formal_baseline = await run_noninterruptible_sync(
                        self._commit_reference_units,
                        round_targets,
                        accepted,
                        filename,
                        formal_baseline,
                        episode,
                        require_current_capabilities,
                    )
                    written.update(accepted)
                    for uid in accepted:
                        failures.pop(uid, None)
                    round_targets = replace(round_targets, script=script)
                retry_entries = tuple(
                    entry
                    for entry in round_targets.entries
                    if (failure := round_failures.get(entry["unit_id"]))
                    and failure["code"] != "invalid_response"
                    and "abort_reason" not in failure
                )
                if not retry_entries:
                    break
                round_targets = replace(round_targets, entries=retry_entries)
        except ScriptWriteConflict:
            reason = conflict
        except Exception as exc:
            if not written:
                raise
            logger.warning("第 %d 集提示词补跑中止", episode, exc_info=True)
            reason = f"{type(exc).__name__}: {exc}"

        if reason:
            for uid in targets.ids:
                if uid not in written:
                    failure = failures.setdefault(
                        uid, {"unit_id": uid, "code": "formal_revision_conflict", "message": "未写入"}
                    )
                    failure["abort_reason"] = reason
        for failure in failures.values():
            failure["message"] += "；请用 entry_ids 点名重试（重写需重新认可覆盖），或用 patch_episode_script 修改"
        if written_ids is not None:
            written_ids[:] = [uid for uid in targets.ids if uid in written]
        remaining = [failures[uid] for uid in targets.ids if uid in failures]
        if failed_units is not None:
            failed_units[:] = remaining
        if not written:
            raise ReferenceAuthoringFailed(remaining)
        return path

    def _commit_reference_units(
        self,
        targets: PromptAuthoringTargets,
        accepted: dict[str, dict],
        filename: str,
        baseline: str | None,
        episode: int,
        check_project: Callable[[dict[str, Any]], None],
    ) -> tuple[dict, str]:
        script = dict(targets.script)
        script["video_units"] = [accepted.get(item["unit_id"], item) for item in targets.items]
        script["metadata"] = {
            **(script.get("metadata") or {}),
            "generator": self.generator.model if self.generator else "unknown",
        }
        self._save_script(script, filename, expected_fingerprint=baseline, project_update=check_project)
        self._quality_probe(script, episode)
        return script, content_fingerprint_of_data(script)

    def _reference_response_units(
        self, targets: PromptAuthoringTargets, response: str, facts: PlanningVideoFacts
    ) -> tuple[dict[str, dict], dict[str, dict[str, str]]]:
        try:
            data = json.loads(strip_json_code_fences(response))
        except ValueError:
            data = None
        rows = data.get("units") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return {}, {
                uid: {"unit_id": uid, "code": "invalid_response", "message": "模型未返回单元列表"}
                for uid in targets.ids
            }
        by_id: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("unit_id"), str):
                by_id[row["unit_id"]].append(row)
        candidates = []
        failures = {}
        for unit in targets.entries:
            uid = unit["unit_id"]
            matches = by_id[uid]
            try:
                if len(matches) != 1:
                    code = "duplicate_unit" if matches else "missing_unit"
                    raise DraftViolation("模型重复返回单元" if matches else "模型漏返单元", code=code)
                candidate = AuthoredReferenceUnit.model_validate(matches[0])
                candidates.append((unit, {**unit, "text": candidate.text}))
            except (ValidationError, DraftViolation) as exc:
                failures[uid] = {
                    "unit_id": uid,
                    "code": exc.code if isinstance(exc, DraftViolation) else "schema_invalid",
                    "message": str(exc),
                }
        project = self._load_project_json()
        hydrations = hydrate_reference_units(project, self.project_path, (merged for _, merged in candidates))
        accepted = {}
        for (unit, merged), hydration in zip(candidates, hydrations, strict=True):
            uid = unit["unit_id"]
            try:
                bucket = hydration.hydrated_generation_type
                caps = facts.require(bucket)
                label = f"unit {uid}"
                validate_unit_text(
                    label,
                    merged["text"],
                    project,
                    max_refs=self._resolve_max_refs(facts),
                    max_speakers=VoiceRenderSettings.from_request_facts(caps).planning_speaker_limit,
                    tolerated_speakers=dialogue_speakers(unit["text"]),
                )
                assert_dialogue_preserved(label, unit["text"], merged["text"])
                tiers = self._unit_duration_off_tier(unit["duration_seconds"], facts=facts, generation_type=bucket)
                if tiers is not None:
                    allowed_tiers = sorted(set(tiers))
                    raise DraftViolation(
                        f"{label} 时长不在当前生效档位 {allowed_tiers} 内",
                        code="duration_off_tier",
                        label=label,
                        params={"label": label, "duration": unit["duration_seconds"], "tiers": allowed_tiers},
                    )
                merged.pop("pending_authoring", None)
                merged.pop("needs_replan", None)
                accepted[uid] = merged
            except VideoRequestFactsError as exc:
                failures[uid] = {
                    "unit_id": uid,
                    "code": exc.code,
                    "message": exc.failure.summary(),
                    "abort_reason": "视频模型能力不可用",
                }
            except DraftViolation as exc:
                failures[uid] = {
                    "unit_id": uid,
                    "code": exc.code,
                    "message": str(exc),
                }
                if exc.code == "speakers_over_limit" and not self._reference_dialogue_fits_a_bucket(unit, facts):
                    failures[uid]["abort_reason"] = "原台词的说话人数和单元时长无法同时满足可用视频模型的要求"
        return accepted, failures

    def _reference_dialogue_fits_a_bucket(self, unit: dict, facts: PlanningVideoFacts) -> bool:
        speaker_count = len(dialogue_speakers(unit["text"]))
        for bucket, capability in facts.by_bucket.items():
            if not isinstance(capability, VideoRequestFacts):
                continue
            limit = VoiceRenderSettings.from_request_facts(capability).planning_speaker_limit
            if (limit is None or speaker_count <= limit) and self._unit_duration_off_tier(
                unit["duration_seconds"], facts=facts, generation_type=bucket
            ) is None:
                return True
        return False

    def _parse_unit_texts(self, response_text: str, episode: int) -> ReferencePromptAuthoringFlatScript:
        text = strip_json_code_fences(response_text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON 解析失败: {e}") from e
        if isinstance(data, dict):
            raw_title = data.get("title")
            if not (isinstance(raw_title, str) and raw_title.strip()):
                data["title"] = episode_title(self.project_json, episode)
        try:
            return ReferencePromptAuthoringFlatScript.model_validate(data)
        except ValidationError as e:
            raise ValueError(f"prompt_authoring 提示词编写结构校验失败: {e}") from e

    async def _author_ad_reference_units(
        self,
        episode: int,
        filename: str,
        targets: PromptAuthoringTargets,
        *,
        formal_baseline: str | None,
        instructions: str | None,
    ) -> Path:
        """广告/短片参考生视频的提示词编写：按 brief、商品信息与前后单元写出待编写单元的正文。

        时长沿用单元现值（ad 单元编排时长不按供应商档位量化）；单元里已有的台词逐字保留。
        违约直接报错。
        """
        logger.info(
            "正在为第 %d 集编写提示词（ad video_units，%d/%d 个单元）...",
            episode,
            len(targets.entries),
            len(targets.items),
        )
        result = await self._generate_text(
            TextGenerationRequest(
                prompt=self._build_ad_reference_prompt_authoring_prompt(episode, targets, instructions),
                response_schema=ReferencePromptAuthoringFlatScript,
            )
        )
        flat = self._parse_unit_texts(result.text, episode)
        if len(flat.units) != len(targets.entries):
            raise ValueError(
                f"提示词编写产出的 unit 数（{len(flat.units)}）与待编写单元数（{len(targets.entries)}）不一致"
            )
        authored: list[dict] = []
        problems: list[str] = []
        for unit, flat_unit in zip(targets.entries, flat.units, strict=True):
            label = f"unit {unit['unit_id']}"
            try:
                assert_dialogue_preserved(label, str(unit.get("text") or ""), flat_unit.text)
                validate_unit_text(label, flat_unit.text, self.project_json, max_refs=None)
            except DraftViolation as exc:
                blocking = [item for item in violation_items(exc) if item.code not in _AD_UNIT_REPLAN_CODES]
                if blocking:
                    problems.extend(str(item) for item in blocking)
                    continue
            authored.append({**unit, "text": flat_unit.text})
        if problems:
            raise ValueError("提示词编写产出的单元正文不合规：" + "；".join(problems))
        script_data = self._authored_script(episode, targets, authored)
        output_path = await run_sync_transaction(
            self._save_script,
            script_data,
            filename,
            validate=True,
            expected_fingerprint=formal_baseline,
        )
        self._quality_probe(script_data, episode)
        logger.info("剧本已保存至 %s", output_path)
        return output_path

    def _ad_prompt_common(self, episode: int, instructions: str | None) -> dict[str, Any]:
        """ad 两类 prompt（整份生成与提示词编写）共用的持久输入槽位，与产物依据同源。"""
        direct_inputs = project_ad_episode_script_inputs(episode, project=self.project_json)
        return {
            "project_overview": cast(dict[str, Any], direct_inputs["overview"]),
            "style": direct_inputs["style"],
            "style_description": direct_inputs["style_description"],
            "characters": cast(dict[str, Any], direct_inputs["characters"]),
            "scenes": cast(dict[str, Any], direct_inputs["scenes"]),
            "props": cast(dict[str, Any], direct_inputs["props"]),
            "products": cast(dict[str, Any], direct_inputs["products"]),
            "brief": direct_inputs["brief"],
            "episode": direct_inputs["episode"],
            "aspect_ratio": direct_inputs["aspect_ratio"],
            "target_language": direct_inputs["target_language"],
            "instructions": instructions,
        }

    def _build_ad_shot_prompt_authoring_prompt(
        self, episode: int, targets: PromptAuthoringTargets, instructions: str | None
    ) -> str:
        return build_ad_shot_prompt_authoring_prompt(
            **self._ad_prompt_common(episode, instructions),
            shots=[item for item in targets.items if isinstance(item, dict)],
            target_ids=targets.ids,
        )

    def _build_ad_reference_prompt_authoring_prompt(
        self, episode: int, targets: PromptAuthoringTargets, instructions: str | None
    ) -> str:
        return build_ad_reference_prompt_authoring_prompt(
            **self._ad_prompt_common(episode, instructions),
            units=[item for item in targets.items if isinstance(item, dict)],
            target_ids=targets.ids,
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
        text = strip_json_code_fences(response_text)

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON 解析失败: {e}") from e
        if not isinstance(data, dict):
            raise ValueError(f"JSON 顶层须是对象，实际是 {type(data).__name__}")

        title = data.get("title")
        if not (isinstance(title, str) and title.strip()):
            data["title"] = episode_title(self.project_json, episode)

        kind = resolve_declared_kind(self.content_mode, self.generation_mode)
        schema = _KIND_PARSE_SCHEMA[kind]
        try:
            parsed = schema.model_validate(data).model_dump()
        except ValidationError as e:
            logger.warning("数据验证警告: %s", e)
            return data
        if NEW_ASSETS_FIELD in data:
            parsed[NEW_ASSETS_FIELD] = data[NEW_ASSETS_FIELD]
        return parsed

    def _resolve_ad_new_assets(self, raw_items: object) -> NewAssetResolution:
        """按项目现状解析整份生成带出的新增资产；处理决定解析不出即违约。"""
        try:
            return resolve_new_assets(self.project_json, raw_items)
        except NewAssetsError as exc:
            raise AdScriptRejected([problem.message for problem in exc.problems]) from exc

    def _parse_ad_storyboard_response(self, response_text: str, episode: int) -> tuple[dict, NewAssetResolution]:
        """解析广告/短片分镜整份生成的输出：剧本与本次新增资产的处理决定。

        集标题取分集账本（可为空），模型给出的标题不采用。
        """
        try:
            data = self._parse_response(response_text, episode)
        except ValueError as exc:
            raise AdScriptRejected([str(exc)]) from exc
        data["title"] = episode_title(self.project_json, episode)
        raw_new_assets = data.pop(NEW_ASSETS_FIELD, None)
        return data, self._resolve_ad_new_assets(raw_new_assets)

    def _parse_ad_reference_response(self, response_text: str, episode: int) -> tuple[dict, NewAssetResolution]:
        """把广告/短片的参考生视频的扁平 LLM 输出机械提升为自包含 ``video_units``，并解析本次新增资产。

        正文里的 ``@[名称]`` 须已登记或在本次新增项中：校验对着叠加了新增项的项目视图判。发声归属问题
        由 ``needs_replan`` 承接，其余违约一并收齐后拒绝。集标题取分集账本（可为空），模型给出的标题不采用。
        """
        text = strip_json_code_fences(response_text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AdScriptRejected([f"广告参考剧本 JSON 解析失败: {exc}"]) from exc
        try:
            flat = AdReferenceFlatScript.model_validate(data)
        except ValidationError as exc:
            raise AdScriptRejected([f"广告参考剧本结构校验失败: {exc}"]) from exc
        raw_new_assets = [item.model_dump() for item in flat.new_assets]
        planning = planning_project(self.project_json, raw_new_assets)
        problems: list[str] = []
        for ordinal, source in enumerate(flat.units, start=1):
            try:
                validate_unit_text(f"unit E{episode}U{ordinal}", source.text, planning, max_refs=None)
            except DraftViolation as exc:
                problems.extend(str(item) for item in violation_items(exc) if item.code not in _AD_UNIT_REPLAN_CODES)
        new_assets = self._resolve_ad_new_assets(raw_new_assets)
        if problems:
            raise AdScriptRejected(problems)

        units: list[dict] = []
        for ordinal, source in enumerate(flat.units, start=1):
            unit: dict = {
                "unit_id": f"E{episode}U{ordinal}",
                "text": source.text,
                "duration_seconds": source.duration_seconds,
                "note": None,
                "generated_assets": {},
            }
            units.append(unit)

        script = ReferenceVideoScript.model_validate(
            {
                "title": episode_title(self.project_json, episode),
                "content_mode": "ad",
                "video_units": units,
            }
        ).model_dump()
        return script, new_assets

    def _add_metadata(
        self,
        script_data: dict,
        episode: int,
        *,
        reference_unit_durations: dict[str, int] | None = None,
        facts: PlanningVideoFacts | None = None,
    ) -> dict:
        """
        补充剧本元数据

        Args:
            script_data: 剧本数据
            episode: 剧集编号
            reference_unit_durations: reference_video 路径按 unit_id（改写后）机械覆盖 LLM
                输出的 unit 时长——script_plan 确认的原始值，未经取档；取档按下方逐 unit 重算，
                见 ``generate`` 内的构造处注释
            facts: 逐 unit 取生效档位的视频请求事实；给了 ``reference_unit_durations`` 就必须给，
                取档校验不跳过

        Returns:
            补充元数据后的剧本数据
        """
        gen_mode = self.generation_mode
        rewritten_output_ids = prepare_script_entries(script_data, project=self.project_json, episode=episode)

        if reference_unit_durations is not None:
            dupes = sorted(uid for uid, count in Counter(rewritten_output_ids).items() if count > 1)
            if dupes:
                raise ValueError(f"reference_video 输出 unit_id 重复: {dupes}")
            missing = sorted(set(reference_unit_durations) - set(rewritten_output_ids))
            if missing:
                raise ValueError(f"reference_video 输出缺少 script_plan 已确认的 unit_id: {missing}")
            unknown = sorted(set(rewritten_output_ids) - set(reference_unit_durations))
            if unknown:
                raise ValueError(f"reference_video 输出包含 script_plan 之外的未知 unit_id: {unknown}")

            raw_rewrite_items, id_field, _kind = resolve_kind_items(
                script_data, kind=resolve_declared_kind(self.content_mode, gen_mode)
            )
            authored = [
                s
                for s in (raw_rewrite_items if isinstance(raw_rewrite_items, list) else [])
                if isinstance(s, dict) and id_field in s
            ]
            if facts is None:
                raise ValueError("reference_video 取档校验需要视频请求事实")
            for s, hydration in zip(authored, self._hydrate_reference_units(authored), strict=True):
                target_duration = reference_unit_durations[s[id_field]]
                bucket = hydration.hydrated_generation_type
                try:
                    facts.require(bucket)
                except VideoRequestFactsError as exc:
                    params: dict[str, Any] = exc.params
                    raise DraftViolation(
                        f"unit {s[id_field]}：{translate(exc.code, **params)}（{exc.failure.summary()}）",
                        code=exc.code,
                        label=f"unit {s[id_field]}",
                        item_id=str(s[id_field]),
                        params=params,
                    ) from exc
                unit_tiers = self._unit_duration_off_tier(target_duration, facts=facts, generation_type=bucket)
                if unit_tiers is not None:
                    raise DraftViolation(
                        f"unit {s[id_field]} 已确认时长 {target_duration}s 不在当前生效档位 "
                        f"{sorted(set(unit_tiers))} 内；通常是该次生成给该 unit 新增/去掉了引用"
                        "导致，请调整该 unit 正文里的 `@` 引用使其回到该档位；若引用本就该是这样，"
                        "说明模型能力已变化，需要重新拆分该集 script_plan",
                        code="duration_off_tier",
                        label=f"unit {s[id_field]}",
                        item_id=str(s[id_field]),
                        params={
                            "label": f"unit {s[id_field]}",
                            "duration": target_duration,
                            "tiers": sorted(set(unit_tiers)),
                        },
                    )
                if s.get("duration_seconds") != target_duration:
                    logger.warning(
                        "unit %s 时长与 script_plan 确认值不一致（LLM 输出 %s，已按 script_plan 确认值 %s 覆盖）",
                        s[id_field],
                        s.get("duration_seconds"),
                        target_duration,
                    )
                s["duration_seconds"] = target_duration
        return finish_script_document(
            script_data,
            project=self.project_json,
            episode=episode,
            generator=self.generator.model if self.generator else "unknown",
        )

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

            kind = resolve_declared_kind(self.content_mode, self.generation_mode)
            raw_items, id_key, _kind = resolve_kind_items(script_data, kind=kind)
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
