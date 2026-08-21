"""
数据验证工具

验证 project.json 和 episode JSON 的数据结构完整性和引用一致性。

产出的 errors / warnings 是 locale-neutral 的 ``ValidationMessage``（key + params），由各消费
边界渲染：归档 router 按请求语言渲染，Agent 与 CLI 走默认语言。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from lib.asset_types import (
    ASSET_SPECS,
    asset_name_comparison_key,
    normalize_asset_name,
    project_asset_name_conflicts,
)
from lib.episode_ledger import (
    LEDGER_STATUSES,
    EpisodeOutline,
    PlanningCursor,
    SourceRange,
    parse_positive_episode_num,
)
from lib.json_io import load_json_or_none
from lib.path_safety import PathTraversalError, safe_join
from lib.profile_manifest import VALID_CONTENT_MODES as _VALID_CONTENT_MODES
from lib.project_manager import VALID_GENERATION_MODES as _VALID_GENERATION_MODES
from lib.project_manager import VALID_SOURCE_KINDS as _VALID_SOURCE_KINDS
from lib.script_models import (
    AD_TARGET_DURATION_DRIFT_THRESHOLD,
    REFERENCE_UNIT_DURATION_RANGE,
    ad_script_total_duration,
    resolve_content_mode,
)
from lib.script_skeleton import (
    STORYBOARD_ITEM_ID_PATTERN,
    SkeletonRouteMismatchError,
    ensure_route_skeleton,
    resolve_declared_kind,
    resolve_script_kind,
)
from lib.speech_rate import (
    MAX_SPEECH_RATE_UPS,
    MIN_SPEECH_RATE_UPS,
    SPEECH_RATE_FIELD,
    estimate_spoken_seconds,
    is_valid_speech_rate,
    project_speech_rate_override,
)
from lib.validation_messages import MessageJoin, MessagePart, MessageRef, ValidationMessage, ValidationResult

__all__ = [
    "DataValidator",
    "validate_episode",
    "validate_project",
]

#: 剧情演绎场景说话量（台词 + 画外音）对场景时长的单向上界容差（比例）。语速估算
#: （``lib.speech_rate`` 单一真相源）是近似值，给 20% 余量只在说话量明显超过画面时长时才
#: warn（长对白塞进固定秒数会说不完 / 语速畸快）。单向上界：只警告「说不完」，不管「说话太少」
#: ——duration 由画面驱动、留白合法，不被此约束反向改写。仅 DataValidator 消费，与 ad 总时长
#: 漂移阈值同量级、同「只 warn 不阻塞」语义。
DRAMA_SPEECH_OVERFLOW_TOLERANCE = 0.20

logger = logging.getLogger(__name__)


def _m(key: str, **params: Any) -> ValidationMessage:
    """构造一条校验消息（key + params），产出点不做语言渲染。"""
    return ValidationMessage(key, params)


def _asset(asset_type: str) -> MessageRef:
    """资产类别名词的嵌套翻译键，复用 ``asset_type_*`` 一份词表。"""
    return MessageRef(f"asset_type_{asset_type}")


def _allowed(values: Iterable[str]) -> str:
    """合法取值列表的稳定文本。集合直接代入会走 ``repr``，字符串 hash 随机化下逐进程变序。"""
    return ", ".join(sorted(values))


def _pydantic_error_summary(exc: ValidationError) -> MessageJoin:
    """把 ValidationError 压成 ``字段: 原因`` 摘要片段，供 errors 列表内嵌。

    自定义校验器把失败原因以翻译键抛出（见 ``lib.episode_ledger``），这里还原成
    ``MessageRef`` 让原因跟随请求语言；Pydantic 内置报错无可翻译结构，原样并入。
    """
    parts: list[MessagePart] = []
    for err in exc.errors():
        location = ".".join(str(part) for part in err["loc"]) or "<root>"
        reason = _custom_error_key(err)
        if reason:
            parts.append(MessageJoin((f"{location}: ", MessageRef(reason)), separator=""))
        else:
            parts.append(f"{location}: {err['msg']}")
    return MessageJoin(tuple(parts))


def _custom_error_key(err: Mapping[str, Any]) -> str | None:
    """自定义校验器抛出的翻译键；内置报错或未登记文案返回 None。"""
    if err.get("type") != "value_error":
        return None
    candidate = str(err.get("ctx", {}).get("error", ""))
    return candidate if candidate.startswith("val_") else None


def _is_parseable_iso_timestamp(value: str) -> bool:
    """校验字符串能否被解析为 ISO8601 时间点，而不仅仅是「是字符串」。

    前端 ``computeVoiceLegacyNotice`` 用 ``new Date(iso).getTime()`` 解析这类字段做时刻
    比较，解析失败得到 ``NaN``——NaN 参与的比较恒为 false，会让横幅静默失效而非报错，
    比字段类型错误更隐蔽，值本身合法性因此也须校验。
    """
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


class DataValidator:
    """数据验证器"""

    # content_mode 严格只表达"内容类型"；"视频来源"维度由项目级 generation_mode 字段表达。
    # 合法集真相源在 lib.profile_manifest，避免两处枚举漂移。
    VALID_CONTENT_MODES = set(_VALID_CONTENT_MODES)
    # 源文件性质（novel / screenplay）合法集，真相源在 lib.project_manager（创建写入方），
    # 避免两处枚举漂移。缺省 novel：缺失字段不报错，仅拦截非法值（如 screen_play）。
    VALID_SOURCE_KINDS = set(_VALID_SOURCE_KINDS)
    # 生成模式合法集（storyboard / reference_video），真相源在 lib.project_manager（创建写入方），
    # 避免两处枚举漂移。必填：存量项目由 v4→v5 迁移补写显式值，缺失即非法。
    VALID_GENERATION_MODES = set(_VALID_GENERATION_MODES)
    # 参考生视频 unit 时长的结构合理性区间，真相源同上（档位成员校验依赖运行时模型能力，
    # 不在归档层做）。
    VALID_UNIT_DURATION_RANGE = REFERENCE_UNIT_DURATION_RANGE
    ID_PATTERN = STORYBOARD_ITEM_ID_PATTERN
    EXTERNAL_URI_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
    ALLOWED_ROOT_ENTRIES = {
        "project.json",
        "style_reference.png",
        "style_reference.jpg",
        "style_reference.jpeg",
        "style_reference.webp",
        "source",
        "scripts",
        "drafts",
        "characters",
        "scenes",
        "props",
        "products",
        "reference_videos",
        "storyboards",
        "end_frames",
        "videos",
        "audio",
        "subtitles",
        "presentations",
        "thumbnails",
        "output",
        "versions",
        "grids",
    }

    def __init__(self, projects_root: str | Path | None = None):
        """
        初始化验证器

        Args:
            projects_root: 项目根目录；默认走 ``app_data_dir()``
                （兼顾 ``ARCREEL_DATA_DIR`` / ``AI_ANIME_PROJECTS`` env）。
        """
        if projects_root is None:
            from lib.app_data_dir import app_data_dir

            self.projects_root = app_data_dir()
        else:
            self.projects_root = Path(projects_root)

    @staticmethod
    def _is_hidden_path(path: Path) -> bool:
        return any(part.startswith(".") or part == "__MACOSX" for part in path.parts)

    def _resolve_existing_path(
        self,
        project_dir: Path,
        raw_path: str,
        *,
        default_dir: str | None = None,
        missing_ok: bool = False,
        confine_to_default_dir: bool = False,
    ) -> tuple[str | None, tuple[str, dict[str, Any]] | None]:
        """返回 ``(相对路径, 失败原因)``；失败原因是 ``(key, params)``，``field`` 由调用方补齐。"""
        normalized = str(raw_path).strip().replace("\\", "/")
        if not normalized:
            return None, ("val_path_empty", {})

        candidate_paths = [Path(normalized)]
        if default_dir and len(candidate_paths[0].parts) == 1:
            candidate_paths.append(Path(default_dir) / candidate_paths[0])

        confine_root = (
            safe_join(project_dir, default_dir, allow_base=True) if confine_to_default_dir and default_dir else None
        )

        seen: set[str] = set()
        saw_out_of_confine = False
        for candidate in candidate_paths:
            candidate_key = candidate.as_posix()
            if candidate_key in seen:
                continue
            seen.add(candidate_key)

            try:
                resolved = safe_join(project_dir, candidate)
            except PathTraversalError:
                return None, ("val_path_traversal", {"path": normalized})

            if confine_root is not None:
                try:
                    resolved.relative_to(confine_root)
                except ValueError:
                    saw_out_of_confine = True
                    continue

            if resolved.is_file():
                return candidate.as_posix(), None

        if saw_out_of_confine:
            return None, ("val_path_outside_dir", {"dir": default_dir, "path": normalized})
        if missing_ok:
            return None, None
        return None, ("val_path_missing", {"path": normalized})

    def _validate_local_reference(
        self,
        project_dir: Path,
        value: Any,
        errors: list[ValidationMessage],
        field_name: str,
        *,
        default_dir: str | None = None,
        allow_external: bool = False,
        missing_ok: bool = False,
        confine_to_default_dir: bool = False,
    ) -> str | None:
        if value in (None, ""):
            return None
        if not isinstance(value, str):
            errors.append(_m("val_field_must_be_string", field=field_name))
            return None

        raw_value = value.strip()
        if not raw_value:
            return None

        if self.EXTERNAL_URI_PATTERN.match(raw_value):
            if allow_external:
                return raw_value
            errors.append(_m("val_path_must_be_relative", field=field_name, path=raw_value))
            return None

        resolved_path, error = self._resolve_existing_path(
            project_dir,
            raw_value,
            default_dir=default_dir,
            missing_ok=missing_ok,
            confine_to_default_dir=confine_to_default_dir,
        )
        if error:
            key, params = error
            errors.append(ValidationMessage(key, {**params, "field": field_name}))
        return resolved_path

    @staticmethod
    def _validate_episode_ledger_fields(episode: dict[str, Any], prefix: str, errors: list[ValidationMessage]) -> None:
        """分集账本字段的形状校验（全部可缺失 = 该集无位置记录），形状真相源复用 lib.episode_ledger 模型。

        ``ledger_status`` 只校验类型：它是咨询性的消费状态，位置真相在 ``source_range``。
        当前状态集之外的取值按「无状态」容忍不报错——存量 project.json 可能留有历史版本
        写入的、现已废弃的状态值，为此把老项目整体判成损坏得不偿失。
        """
        ledger_status = episode.get("ledger_status")
        if ledger_status is not None and not isinstance(ledger_status, str):
            errors.append(_m("val_ledger_status_type", prefix=prefix, value=repr(ledger_status)))
        elif isinstance(ledger_status, str) and ledger_status not in LEDGER_STATUSES:
            # 容忍放行、只留排查线索：分不清是存量遗留值还是手编拼写错误，不拿它判项目损坏
            logger.debug("%s: ledger_status 取值 %r 不在当前状态集内，按无状态处理", prefix, ledger_status)

        source_range = episode.get("source_range")
        if source_range is not None:
            try:
                SourceRange.model_validate(source_range)
            except ValidationError as exc:
                errors.append(
                    _m("val_field_invalid", field=f"{prefix}: source_range", detail=_pydantic_error_summary(exc))
                )

        hook = episode.get("hook")
        if hook is not None and not isinstance(hook, str):
            errors.append(_m("val_field_must_be_string", field=f"{prefix}: hook"))

        outline = episode.get("outline")
        if outline is not None:
            try:
                EpisodeOutline.model_validate(outline)
            except ValidationError as exc:
                errors.append(_m("val_field_invalid", field=f"{prefix}: outline", detail=_pydantic_error_summary(exc)))

    @staticmethod
    def _validate_ad_project_fields(
        project: dict[str, Any],
        content_mode: Any,
        errors: list[ValidationMessage],
    ) -> None:
        """广告/短片项目的专属字段与恒单集约束。

        target_duration / brief 仅 ad 项目持有；ad 项目不持有 default_duration
        （镜头按目标总时长预算逐个规划，单镜头偏好无意义），episodes 恒为第 1 集单条。
        """
        if content_mode != "ad":
            if project.get("target_duration") is not None:
                errors.append(_m("val_ad_only_field", field="target_duration"))
            if project.get("brief") is not None:
                errors.append(_m("val_ad_only_field", field="brief"))
            return

        target_duration = project.get("target_duration")
        if target_duration is None:
            errors.append(_m("val_ad_missing_target_duration"))
        elif not isinstance(target_duration, int) or isinstance(target_duration, bool) or target_duration <= 0:
            errors.append(_m("val_ad_target_duration_invalid", value=repr(target_duration)))

        brief = project.get("brief")
        if brief is not None and not isinstance(brief, str):
            errors.append(_m("val_field_must_be_string", field="brief"))

        if project.get("default_duration") is not None:
            errors.append(_m("val_ad_no_default_duration"))

        if project.get("grid_storyboard") is True:
            errors.append(_m("val_ad_no_grid_storyboard"))

        episodes = project.get("episodes")
        if not isinstance(episodes, list) or (
            len(episodes) != 1 or not isinstance(episodes[0], dict) or episodes[0].get("episode") != 1
        ):
            errors.append(_m("val_ad_episodes_single"))

    def _validate_project_payload(
        self,
        project: dict[str, Any],
        errors: list[ValidationMessage],
        warnings: list[ValidationMessage],
    ) -> None:
        if "title" not in project:
            errors.append(_m("val_missing_field", field="title"))
        elif not isinstance(project["title"], str):
            errors.append(_m("val_field_type_string", field="title"))

        content_mode = project.get("content_mode")
        if not content_mode:
            errors.append(_m("val_missing_field", field="content_mode"))
        elif not isinstance(content_mode, str) or content_mode not in self.VALID_CONTENT_MODES:
            errors.append(
                _m("val_content_mode_invalid", value=content_mode, allowed=_allowed(self.VALID_CONTENT_MODES))
            )

        # source_kind 缺省 novel：缺失字段（存量项目）放行，仅拦截非法值（如 screen_play）。
        source_kind = project.get("source_kind")
        if source_kind is not None and (not isinstance(source_kind, str) or source_kind not in self.VALID_SOURCE_KINDS):
            errors.append(_m("val_source_kind_invalid", value=source_kind, allowed=_allowed(self.VALID_SOURCE_KINDS)))

        # 生成模式必填二值：存量项目由 v4→v5 迁移补写显式值（含 grid 重编码），无缺省语义
        generation_mode = project.get("generation_mode")
        if not generation_mode:
            errors.append(_m("val_missing_field", field="generation_mode"))
        elif not isinstance(generation_mode, str) or generation_mode not in self.VALID_GENERATION_MODES:
            errors.append(
                _m("val_generation_mode_invalid", value=generation_mode, allowed=_allowed(self.VALID_GENERATION_MODES))
            )

        grid_storyboard = project.get("grid_storyboard")
        if grid_storyboard is not None and not isinstance(grid_storyboard, bool):
            errors.append(_m("val_field_type_bool", field="grid_storyboard"))

        # 项目级语速覆盖（阅读单位 / 秒）：可选字段，缺省即回退语言默认；出现即须落在硬区间内。
        speech_rate = project.get(SPEECH_RATE_FIELD)
        if speech_rate is not None:
            if isinstance(speech_rate, bool) or not isinstance(speech_rate, (int, float)):
                errors.append(_m("val_field_type_number", field=SPEECH_RATE_FIELD))
            elif not is_valid_speech_rate(speech_rate):
                errors.append(
                    _m(
                        "val_speech_rate_out_of_range",
                        field=SPEECH_RATE_FIELD,
                        value=speech_rate,
                        min=MIN_SPEECH_RATE_UPS,
                        max=MAX_SPEECH_RATE_UPS,
                    )
                )

        self._validate_ad_project_fields(project, content_mode, errors)

        if not project.get("style"):
            errors.append(_m("val_missing_field", field="style"))

        episodes = project.get("episodes", [])
        if not isinstance(episodes, list):
            errors.append(_m("val_field_must_be_array", field="episodes"))
        else:
            for index, episode in enumerate(episodes):
                prefix = f"episodes[{index}]"
                if not isinstance(episode, dict):
                    errors.append(_m("val_item_format_object", prefix=prefix))
                    continue

                episode_num = episode.get("episode")
                if not isinstance(episode_num, int) or isinstance(episode_num, bool):
                    errors.append(_m("val_episode_missing_num_at", prefix=prefix))
                # title 允许空串：写入方（剧本同步/孤儿条目登记）在标题未知时即写 ""，
                # 待用户或 Agent 后续命名
                if not isinstance(episode.get("title"), str):
                    errors.append(_m("val_episode_missing_title_at", prefix=prefix))

                script_file = episode.get("script_file")
                if not script_file:
                    errors.append(_m("val_missing_field_at", prefix=prefix, field="script_file"))
                elif not isinstance(script_file, str):
                    errors.append(_m("val_field_must_be_string", field=f"{prefix}: script_file"))

                self._validate_episode_ledger_fields(episode, prefix, errors)

        planning_cursor = project.get("planning_cursor")
        if planning_cursor is not None:
            try:
                PlanningCursor.model_validate(planning_cursor)
            except ValidationError as exc:
                errors.append(_m("val_field_invalid", field="planning_cursor", detail=_pydantic_error_summary(exc)))

        for first, duplicate in project_asset_name_conflicts(project):
            errors.append(
                _m(
                    "val_asset_name_duplicate",
                    first_type=_asset(first.asset_type),
                    first_name=first.name,
                    duplicate_type=_asset(duplicate.asset_type),
                    duplicate_name=duplicate.name,
                )
            )

        characters = project.get("characters", {})
        if isinstance(characters, dict):
            char_extra_fields = ASSET_SPECS["character"].extra_string_fields
            for char_name, char_data in characters.items():
                if not isinstance(char_data, dict):
                    errors.append(_m("val_asset_format_object", asset_type=_asset("character"), name=char_name))
                    continue
                desc = char_data.get("description")
                if not isinstance(desc, str) or not desc:
                    # 必须是非空字符串：description 是 LLM 直写字段，Agent 误传数字/对象
                    # 应在守卫点 fail-loud，否则会作为合法资产落盘、下游消费时才崩
                    errors.append(_m("val_asset_missing_description", asset_type=_asset("character"), name=char_name))
                for field_name in char_extra_fields:
                    # spec 声明的 extra_string_fields（voice_style / reference_image 等）若存在
                    # 须为字符串（可空），否则下游消费方（如把 reference_image 当路径拼接）
                    # 会运行时崩。None 视为「未设置」放行，非 str 类型 fail-loud。
                    val = char_data.get(field_name)
                    if val is not None and not isinstance(val, str):
                        errors.append(
                            _m(
                                "val_asset_field_must_be_string",
                                asset_type=_asset("character"),
                                name=char_name,
                                field=field_name,
                                actual=type(val).__name__,
                            )
                        )
                    elif field_name == "voice_notice_dismissed_at" and val and not _is_parseable_iso_timestamp(val):
                        errors.append(
                            _m(
                                "val_asset_field_bad_timestamp",
                                asset_type=_asset("character"),
                                name=char_name,
                                field=field_name,
                                value=repr(val),
                            )
                        )
                # voice_updated_at 不在 extra_string_fields 里（系统专用戳字段，故意不开放
                # 通用 PATCH 覆写），但仍需校验类型与值：外部编辑/导入的 project.json 若把它写成
                # 非字符串或不可解析的字符串，会在前端 computeVoiceLegacyNotice 的日期解析处
                # 静默产生 NaN。
                voice_updated_at = char_data.get("voice_updated_at")
                if voice_updated_at is not None and not isinstance(voice_updated_at, str):
                    errors.append(
                        _m(
                            "val_asset_field_must_be_string",
                            asset_type=_asset("character"),
                            name=char_name,
                            field="voice_updated_at",
                            actual=type(voice_updated_at).__name__,
                        )
                    )
                elif voice_updated_at and not _is_parseable_iso_timestamp(voice_updated_at):
                    errors.append(
                        _m(
                            "val_asset_field_bad_timestamp",
                            asset_type=_asset("character"),
                            name=char_name,
                            field="voice_updated_at",
                            value=repr(voice_updated_at),
                        )
                    )

        if project.get("clues") is not None:
            errors.append(_m("val_deprecated_clues"))

        self._validate_project_catalog(project.get("scenes") or {}, errors, field_label="scenes")
        self._validate_project_catalog(project.get("props") or {}, errors, field_label="props")
        self._validate_project_catalog(project.get("products") or {}, errors, field_label="products")

    def _validate_project_catalog(
        self,
        catalog: Any,
        errors: list[ValidationMessage],
        *,
        field_label: str,
    ) -> None:
        if not isinstance(catalog, dict):
            errors.append(_m("val_field_must_be_object", field=field_label))
            return
        # scene/prop 的 extra_string_fields 当前均为空 tuple（见 ASSET_SPECS），仍按 spec 取
        # 以保持「validator 跟 spec 同步」——将来给 scenes/props 加 extra 字段时无需改本处。
        asset_type = field_label.rstrip("s")  # "scenes" → "scene"; "products" → "product"
        spec = ASSET_SPECS.get(asset_type)
        extra_fields = spec.extra_string_fields if spec else ()
        extra_list_fields = spec.extra_list_fields if spec else ()
        kind = _asset(asset_type)
        for name, data in catalog.items():
            if not isinstance(data, dict):
                errors.append(_m("val_asset_format_object", asset_type=kind, name=name))
                continue
            desc = data.get("description")
            if not isinstance(desc, str) or not desc:
                # 同 characters：description 须为非空字符串，避免数字/对象被 truthy 判通过
                errors.append(_m("val_asset_missing_description", asset_type=kind, name=name))
            for field_name in extra_fields:
                val = data.get(field_name)
                if val is not None and not isinstance(val, str):
                    errors.append(
                        _m(
                            "val_asset_field_must_be_string",
                            asset_type=kind,
                            name=name,
                            field=field_name,
                            actual=type(val).__name__,
                        )
                    )
            for field_name in extra_list_fields:
                # spec 声明的 extra_list_fields（reference_images / selling_points 等）若存在
                # 须为字符串列表：下游把元素当路径拼接 / 当文本注入 prompt，混入非 str 会
                # 运行时崩。None 视为「未设置」放行，其余类型 fail-loud。
                val = data.get(field_name)
                if val is None:
                    continue
                if not isinstance(val, list):
                    errors.append(
                        _m(
                            "val_asset_field_must_be_string_list",
                            asset_type=kind,
                            name=name,
                            field=field_name,
                            actual=type(val).__name__,
                        )
                    )
                    continue
                for idx, item in enumerate(val):
                    if not isinstance(item, str):
                        errors.append(
                            _m(
                                "val_asset_field_item_must_be_string",
                                asset_type=kind,
                                name=name,
                                field=field_name,
                                index=idx,
                                actual=type(item).__name__,
                            )
                        )

    def _unregistered_refs(self, refs: list[Any], valid_set: set[str]) -> list[Any]:
        """按 ``lib.asset_types`` 的比对坐标系（NFC）挑出未登记的资产引用。

        剧本里的名字与 project.json 的资产 key 可以是 NFC/NFD 中的任一形态（登记闸口落
        NFC，存量剧本与桶均不迁移），两侧归一后才判得准；下游各收集器同样归一后索引，
        校验层与收集层因此对同一份数据给出一致结论。资产引用的判等一律经此，不在各字段
        处按裸字符串做集合差。"""
        normalized_valid = {normalize_asset_name(v) for v in valid_set}
        return [r for r in refs if not isinstance(r, str) or asset_name_comparison_key(r) not in normalized_valid]

    def _validate_segment_refs(
        self,
        prefix: str,
        refs: Any,
        valid_set: set[str],
        errors: list[ValidationMessage],
        warnings: list[ValidationMessage],
        *,
        field_label: str,
        asset_type: str,
    ) -> None:
        """校验可缺省的镜头资产引用字段：缺失给 warning，非数组或引用未登记给 error。"""
        if refs is None:
            warnings.append(_m("val_missing_defaults_empty_array", prefix=prefix, field=field_label))
            return
        if not isinstance(refs, list):
            errors.append(_m("val_field_must_be_array", field=f"{prefix}: {field_label}"))
            return
        invalid = self._unregistered_refs(refs, valid_set)
        if invalid:
            errors.append(
                _m(
                    "val_refs_unregistered",
                    prefix=prefix,
                    field=field_label,
                    asset_type=_asset(asset_type),
                    names=invalid,
                )
            )

    def validate_project_payload(self, project: dict[str, Any]) -> ValidationResult:
        """对内存中的 project.json dict 做结构校验（不读盘）。

        供写入前校验复用——`patch_project` 在 `update_project` 的 mutation 内 apply 改动后、
        落盘前调用本方法，非法则中止写入，避免「先写后验、失败仍留脏数据」。
        """
        errors: list[ValidationMessage] = []
        warnings: list[ValidationMessage] = []
        self._validate_project_payload(project, errors, warnings)
        return ValidationResult(valid=len(errors) == 0, error_messages=errors, warning_messages=warnings)

    def validate_asset_definitions(self, project: dict[str, Any]) -> ValidationResult:
        """Validate the asset-entry subset of an in-memory project payload."""
        result = self.validate_project_payload(project)
        errors = [message for message in result.error_messages if message.key.startswith("val_asset_")]
        return ValidationResult(valid=not errors, error_messages=errors)

    def validate_project(self, project_name: str) -> ValidationResult:
        """验证 project.json"""
        return self.validate_project_dir(self.projects_root / project_name)

    def validate_project_dir(self, project_dir: Path) -> ValidationResult:
        """验证指定目录中的 project.json。"""
        errors: list[ValidationMessage] = []
        warnings: list[ValidationMessage] = []

        project_path = Path(project_dir) / "project.json"
        project = load_json_or_none(project_path)
        if project is None:
            return ValidationResult(
                valid=False,
                error_messages=[_m("val_cannot_load_project_json", path=project_path)],
            )

        self._validate_project_payload(project, errors, warnings)
        return ValidationResult(valid=len(errors) == 0, error_messages=errors, warning_messages=warnings)

    def _validate_generated_assets(
        self,
        project_dir: Path,
        prefix: str,
        assets: Any,
        errors: list[ValidationMessage],
    ) -> None:
        if assets in (None, ""):
            return
        if not isinstance(assets, dict):
            errors.append(_m("val_field_must_be_object", field=f"{prefix}.generated_assets"))
            return

        self._validate_local_reference(
            project_dir,
            assets.get("storyboard_image"),
            errors,
            f"{prefix}.generated_assets.storyboard_image",
            default_dir="storyboards",
        )
        self._validate_local_reference(
            project_dir,
            assets.get("storyboard_last_image"),
            errors,
            f"{prefix}.generated_assets.storyboard_last_image",
            default_dir="storyboards",
        )
        self._validate_local_reference(
            project_dir,
            assets.get("video_clip"),
            errors,
            f"{prefix}.generated_assets.video_clip",
            default_dir="videos",
        )
        self._validate_local_reference(
            project_dir,
            assets.get("video_uri"),
            errors,
            f"{prefix}.generated_assets.video_uri",
            default_dir="videos",
            allow_external=True,
        )
        self._validate_local_reference(
            project_dir,
            assets.get("narration_audio"),
            errors,
            f"{prefix}.generated_assets.narration_audio",
            default_dir="audio",
        )

        # video_generated_at 是前端 computeVoiceLegacyNotice 按时刻比较视频生成时间与
        # 角色声音更新时间的依据；非字符串或不可解析的字符串会在该处日期解析得到 NaN，
        # NaN 参与的比较恒为 false，判定因此静默失效而非报错。
        video_generated_at = assets.get("video_generated_at")
        field_name = f"{prefix}.generated_assets.video_generated_at"
        if video_generated_at is not None and not isinstance(video_generated_at, str):
            errors.append(
                _m("val_field_must_be_string_typed", field=field_name, actual=type(video_generated_at).__name__)
            )
        elif video_generated_at and not _is_parseable_iso_timestamp(video_generated_at):
            errors.append(_m("val_field_bad_timestamp", field=field_name, value=repr(video_generated_at)))

    def _validate_end_frame_image(
        self,
        project_dir: Path,
        prefix: str,
        item: dict[str, Any],
        errors: list[ValidationMessage],
    ) -> None:
        """校验镜头条目的尾帧快照路径：越界、缺失、目录外均报 error（缺失即悬空引用，不容忍）。

        与 generated_assets 内的路径字段不同：尾帧字段只接受服务层写出的 `end_frames/`
        快照路径（`confine_to_default_dir`），不接受指向 storyboards/scripts 等其它目录
        内恰好存在的文件——否则字段等于绕过快照复制直接引用源图，废掉解耦设计。
        """
        self._validate_local_reference(
            project_dir,
            item.get("end_frame_image"),
            errors,
            f"{prefix}.end_frame_image",
            default_dir="end_frames",
            confine_to_default_dir=True,
        )

    def _validate_segments(
        self,
        segments: list[dict[str, Any]] | Any,
        project_characters: set[str],
        project_scenes: set[str],
        project_props: set[str],
        errors: list[ValidationMessage],
        warnings: list[ValidationMessage],
        *,
        project_dir: Path | None = None,
    ) -> None:
        """验证 segments（旁白/解说）"""
        if not isinstance(segments, list):
            errors.append(_m("val_field_must_be_array", field="segments"))
            return
        if not segments:
            errors.append(_m("val_array_empty", field="segments"))
            return

        for index, segment in enumerate(segments):
            prefix = f"segments[{index}]"
            if not isinstance(segment, dict):
                errors.append(_m("val_item_must_be_object", prefix=prefix))
                continue

            segment_id = segment.get("segment_id")
            if not segment_id:
                errors.append(_m("val_missing_field_at", prefix=prefix, field="segment_id"))
            elif not self.ID_PATTERN.match(segment_id):
                errors.append(_m("val_id_format", prefix=prefix, field="segment_id", value=segment_id))

            duration = segment.get("duration_seconds")
            if duration is None:
                warnings.append(_m("val_missing_duration_default", prefix=prefix, default=4))
            elif not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
                errors.append(_m("val_duration_invalid", prefix=prefix, value=duration))

            if not segment.get("novel_text"):
                errors.append(_m("val_missing_field_at", prefix=prefix, field="novel_text"))

            chars_in_segment = segment.get("characters_in_segment")
            if chars_in_segment is None:
                errors.append(_m("val_missing_field_at", prefix=prefix, field="characters_in_segment"))
            elif not isinstance(chars_in_segment, list):
                errors.append(_m("val_field_must_be_array", field=f"{prefix}: characters_in_segment"))
            else:
                invalid = self._unregistered_refs(chars_in_segment, project_characters)
                if invalid:
                    errors.append(
                        _m(
                            "val_refs_unregistered",
                            prefix=prefix,
                            field="characters_in_segment",
                            asset_type=_asset("character"),
                            names=invalid,
                        )
                    )

            self._validate_segment_refs(
                prefix,
                segment.get("scenes"),
                project_scenes,
                errors,
                warnings,
                field_label="scenes",
                asset_type="scene",
            )
            self._validate_segment_refs(
                prefix,
                segment.get("props"),
                project_props,
                errors,
                warnings,
                field_label="props",
                asset_type="prop",
            )

            if not segment.get("image_prompt"):
                errors.append(_m("val_missing_field_at", prefix=prefix, field="image_prompt"))
            if not segment.get("video_prompt"):
                errors.append(_m("val_missing_field_at", prefix=prefix, field="video_prompt"))

            if project_dir is not None:
                self._validate_generated_assets(
                    project_dir,
                    prefix,
                    segment.get("generated_assets"),
                    errors,
                )
                self._validate_end_frame_image(project_dir, prefix, segment, errors)

    def _validate_scenes(
        self,
        scenes: list[dict[str, Any]] | Any,
        project_characters: set[str],
        project_scenes: set[str],
        project_props: set[str],
        errors: list[ValidationMessage],
        warnings: list[ValidationMessage],
        *,
        project_dir: Path | None = None,
        language: str | None = None,
        speech_rate_override: float | None = None,
    ) -> None:
        """验证 scenes（剧情演绎）。

        ``language`` 为项目 ``source_language``（说话量上界 warning 的语速按此取，与字幕派生同口径）；
        ``speech_rate_override`` 为项目级语速覆盖，None 即回退语言默认。
        """
        if not isinstance(scenes, list):
            errors.append(_m("val_field_must_be_array", field="scenes"))
            return
        if not scenes:
            errors.append(_m("val_array_empty", field="scenes"))
            return

        for index, scene in enumerate(scenes):
            prefix = f"scenes[{index}]"
            if not isinstance(scene, dict):
                errors.append(_m("val_item_must_be_object", prefix=prefix))
                continue

            scene_id = scene.get("scene_id")
            if not scene_id:
                errors.append(_m("val_missing_field_at", prefix=prefix, field="scene_id"))
            elif not self.ID_PATTERN.match(scene_id):
                errors.append(_m("val_id_format", prefix=prefix, field="scene_id", value=scene_id))

            duration = scene.get("duration_seconds")
            if duration is None:
                warnings.append(_m("val_missing_duration_default", prefix=prefix, default=8))
            elif not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
                errors.append(_m("val_duration_invalid", prefix=prefix, value=duration))

            chars_in_scene = scene.get("characters_in_scene")
            if chars_in_scene is None:
                errors.append(_m("val_missing_field_at", prefix=prefix, field="characters_in_scene"))
            elif not isinstance(chars_in_scene, list):
                errors.append(_m("val_field_must_be_array", field=f"{prefix}: characters_in_scene"))
            else:
                invalid = self._unregistered_refs(chars_in_scene, project_characters)
                if invalid:
                    errors.append(
                        _m(
                            "val_refs_unregistered",
                            prefix=prefix,
                            field="characters_in_scene",
                            asset_type=_asset("character"),
                            names=invalid,
                        )
                    )

            self._validate_segment_refs(
                prefix,
                scene.get("scenes"),
                project_scenes,
                errors,
                warnings,
                field_label="scenes",
                asset_type="scene",
            )
            self._validate_segment_refs(
                prefix,
                scene.get("props"),
                project_props,
                errors,
                warnings,
                field_label="props",
                asset_type="prop",
            )

            # utterances：场景级有序发声序列（取代旧 video_prompt.dialogue + voiceover）。
            # 缺失放行（存量剧情演绎走读时迁移，旧双字段不在此层校验）；出现则校验结构与
            # kind ⇄ speaker 约束，与上方 characters_in_scene 等同口径（逐项 append、不 raise）。
            self._validate_utterances(scene.get("utterances"), prefix, errors)

            # 说话量（台词 + 画外音）对场景时长的单向上界 warning：估算说话时长超 duration × 容差
            # 时仅提示「说不完」，不阻塞、不改写 duration（duration 由画面驱动）。
            self._warn_scene_speech_overflow(scene, prefix, language, speech_rate_override, warnings)

            # source_text：逐字原文锚（best-effort，由 step1 内容抽取填入、step2 透传）。镜像共享模型
            # 的 source_text: str（extra=forbid 下显式 null 同样被拒）——键存在则须为字符串，显式 null
            # 一并拒绝；键缺失放行（默认空串，存量数据无此字段）。用 in 判定以区分缺失与显式 null。
            if "source_text" in scene and not isinstance(scene["source_text"], str):
                errors.append(_m("val_field_must_be_string", field=f"{prefix}: source_text"))

            if not scene.get("image_prompt"):
                errors.append(_m("val_missing_field_at", prefix=prefix, field="image_prompt"))
            if not scene.get("video_prompt"):
                errors.append(_m("val_missing_field_at", prefix=prefix, field="video_prompt"))

            if project_dir is not None:
                self._validate_generated_assets(
                    project_dir,
                    prefix,
                    scene.get("generated_assets"),
                    errors,
                )
                self._validate_end_frame_image(project_dir, prefix, scene, errors)

    @staticmethod
    def _validate_utterances(utterances: Any, prefix: str, errors: list[ValidationMessage]) -> None:
        """校验剧情演绎场景级 utterances 的结构与 kind ⇄ speaker 约束（缺失放行，存量走读时迁移）。

        每条须为 ``{kind, speaker, text}``：kind ∈ {dialogue, voiceover}、text 非空字符串；
        dialogue 必带非空 speaker、voiceover 不得带 speaker。逐项 append、不 raise。
        """
        if utterances is None:
            return
        if not isinstance(utterances, list):
            errors.append(_m("val_field_must_be_array", field=f"{prefix}: utterances"))
            return
        for ui, item in enumerate(utterances):
            uprefix = f"{prefix}: utterances[{ui}]"
            if not isinstance(item, dict):
                errors.append(_m("val_utterance_must_be_object", prefix=uprefix))
                continue
            kind = item.get("kind")
            if kind not in ("dialogue", "voiceover"):
                errors.append(_m("val_utterance_kind_invalid", prefix=uprefix))
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                errors.append(_m("val_utterance_text_invalid", prefix=uprefix))
            speaker = item.get("speaker")
            if speaker is not None and not isinstance(speaker, str):
                # speaker 仅允许字符串或 null（镜像 Utterance.speaker: str | None 的类型约束）：
                # 数字 / 布尔 / 对象等在 Pydantic 层即类型校验失败，这里同口径 fail-loud，避免
                # 非法 shape 在结构校验里静默放行、到 Pydantic 才崩。置 has_speaker=True 表示
                # 「提供了 speaker」，使 dialogue 不再叠报「缺 speaker」（类型错才是根因）。
                errors.append(_m("val_utterance_speaker_type", prefix=uprefix))
                has_speaker = True
            else:
                # 字符串（空串 / 纯空白按 Utterance._normalize_speaker 同口径归一为「无 speaker」）或 None
                has_speaker = isinstance(speaker, str) and bool(speaker.strip())
            if kind == "dialogue" and not has_speaker:
                errors.append(_m("val_utterance_dialogue_speaker", prefix=uprefix))
            elif kind == "voiceover" and has_speaker:
                errors.append(_m("val_utterance_voiceover_speaker", prefix=uprefix))

    @staticmethod
    def _warn_scene_speech_overflow(
        scene: dict[str, Any],
        prefix: str,
        language: str | None,
        speech_rate_override: float | None,
        warnings: list[ValidationMessage],
    ) -> None:
        """场景说话量（台词 + 画外音）估算时长超 ``duration ×（1 + 容差）`` 时仅 warn（单向上界，不阻塞）。

        说话时长按 ``estimate_spoken_seconds``（语速估算单一真相源）对 utterances 逐条求和，与剧情演绎
        字幕 span 派生同口径；空 / 纯空白 / 脏数据条目估 0 秒。duration 仍由画面驱动、不被此约束反向
        改写。单向：只警告「说不完」（估算超上界），不管「说话太少」。duration 非正整数、无 utterances
        时跳过——与 ad 总时长漂移那条同样「只 warn 不阻塞」、不推前端。
        """
        duration = scene.get("duration_seconds")
        if not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
            return
        utterances = scene.get("utterances")
        if not isinstance(utterances, list):
            return
        spoken = 0.0
        for item in utterances:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str):
                spoken += estimate_spoken_seconds(text, language, speech_rate_override)
        budget = duration * (1 + DRAMA_SPEECH_OVERFLOW_TOLERANCE)
        if spoken > budget:
            warnings.append(
                _m(
                    "val_scene_speech_overflow",
                    prefix=prefix,
                    spoken=spoken,
                    duration=duration,
                    tolerance=DRAMA_SPEECH_OVERFLOW_TOLERANCE,
                    budget=budget,
                )
            )

    def _validate_shots(
        self,
        shots: list[dict[str, Any]] | Any,
        project_characters: set[str],
        project_scenes: set[str],
        project_props: set[str],
        project_products: set[str],
        errors: list[ValidationMessage],
        warnings: list[ValidationMessage],
        *,
        project_dir: Path | None = None,
    ) -> None:
        """验证 shots（广告/短片）：平铺分镜列表，口播文案一等，商品按名字引用。

        storyboard 路径的时长成员校验在生成 schema 层（supported_durations 枚举，校验器
        拿不到供应商能力、只把关正整数）。参考生视频使用 ``video_units``，不经过本函数。

        资产引用一律按 NFC 归一比对（见 ``_validate_segment_refs``），与两条生成路径的
        各收集器同口径。
        """
        if not isinstance(shots, list) or not shots:
            errors.append(_m("val_ad_shots_missing"))
            return

        for index, shot in enumerate(shots):
            prefix = f"shots[{index}]"
            if not isinstance(shot, dict):
                errors.append(_m("val_item_must_be_object", prefix=prefix))
                continue

            shot_id = shot.get("shot_id")
            if not shot_id:
                errors.append(_m("val_missing_field_at", prefix=prefix, field="shot_id"))
            elif not isinstance(shot_id, str) or not self.ID_PATTERN.match(shot_id):
                errors.append(_m("val_id_format", prefix=prefix, field="shot_id", value=shot_id))

            duration = shot.get("duration_seconds")
            if duration is None:
                warnings.append(_m("val_shot_duration_missing_zero", prefix=prefix))
            elif not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
                errors.append(_m("val_duration_invalid", prefix=prefix, value=duration))

            if "voiceover_text" not in shot:
                errors.append(_m("val_shot_missing_voiceover_text", prefix=prefix))
            elif not isinstance(shot.get("voiceover_text"), str):
                errors.append(_m("val_field_must_be_string", field=f"{prefix}: voiceover_text"))

            section = shot.get("section")
            if section is not None and not isinstance(section, str):
                errors.append(_m("val_field_must_be_string", field=f"{prefix}: section"))

            self._validate_segment_refs(
                prefix,
                shot.get("characters_in_shot"),
                project_characters,
                errors,
                warnings,
                field_label="characters_in_shot",
                asset_type="character",
            )
            self._validate_segment_refs(
                prefix,
                shot.get("scenes"),
                project_scenes,
                errors,
                warnings,
                field_label="scenes",
                asset_type="scene",
            )
            self._validate_segment_refs(
                prefix,
                shot.get("props"),
                project_props,
                errors,
                warnings,
                field_label="props",
                asset_type="prop",
            )
            self._validate_segment_refs(
                prefix,
                shot.get("products_in_shot"),
                project_products,
                errors,
                warnings,
                field_label="products_in_shot",
                asset_type="product",
            )

            if not shot.get("image_prompt"):
                errors.append(_m("val_missing_field_at", prefix=prefix, field="image_prompt"))
            if not shot.get("video_prompt"):
                errors.append(_m("val_missing_field_at", prefix=prefix, field="video_prompt"))

            if project_dir is not None:
                self._validate_generated_assets(
                    project_dir,
                    prefix,
                    shot.get("generated_assets"),
                    errors,
                )
                self._validate_end_frame_image(project_dir, prefix, shot, errors)

    @staticmethod
    def _warn_ad_target_duration_drift(
        project: dict[str, Any],
        shots: Any,
        warnings: list[ValidationMessage],
    ) -> None:
        """ad 剧本总时长偏离 target_duration 超阈值仅 warn，不阻塞（轻量观察，不推前端）。"""
        target = project.get("target_duration")
        if not isinstance(target, int) or isinstance(target, bool) or target <= 0:
            return
        total = ad_script_total_duration(shots)
        if total <= 0:
            return
        delta_ratio = abs(total - target) / target
        if delta_ratio > AD_TARGET_DURATION_DRIFT_THRESHOLD:
            warnings.append(
                _m(
                    "val_ad_duration_drift",
                    total=total,
                    target=target,
                    delta=delta_ratio,
                    threshold=AD_TARGET_DURATION_DRIFT_THRESHOLD,
                )
            )

    @staticmethod
    def _check_unit_id_unique(
        unit_id: Any,
        seen_unit_ids: set[str],
        prefix: str,
        errors: list[ValidationMessage],
        *,
        missing_key: str,
    ) -> None:
        """校验单个 unit_id 的存在性与唯一性。

        统一校验 ``video_units`` 的 unit_id 存在性与唯一性。
        """
        if not unit_id or not isinstance(unit_id, str):
            errors.append(_m(missing_key, prefix=prefix))
            return
        if unit_id in seen_unit_ids:
            errors.append(_m("val_unit_id_duplicate", prefix=prefix, value=unit_id))
        seen_unit_ids.add(unit_id)

    def _validate_reference_video_script(
        self,
        video_units: list[dict[str, Any]] | Any,
        errors: list[ValidationMessage],
        warnings: list[ValidationMessage],
        *,
        project_dir: Path | None = None,
    ) -> None:
        """验证 video_units（参考生视频）"""
        if not isinstance(video_units, list) or not video_units:
            errors.append(_m("val_video_units_missing"))
            return

        seen_unit_ids: set[str] = set()

        for index, unit in enumerate(video_units):
            prefix = f"video_units[{index}]"
            if not isinstance(unit, dict):
                errors.append(_m("val_item_must_be_object", prefix=prefix))
                continue

            self._check_unit_id_unique(
                unit.get("unit_id"),
                seen_unit_ids,
                prefix,
                errors,
                missing_key="val_unit_id_missing",
            )

            duration = unit.get("duration_seconds")
            needs_replan = unit.get("needs_replan", False)
            if not isinstance(needs_replan, bool):
                errors.append(_m("val_field_type_bool", field=f"{prefix}: needs_replan"))
            text = unit.get("text")
            if not isinstance(text, str):
                errors.append(_m("val_field_must_be_string", field=f"{prefix}: text"))
            elif not text.strip() and needs_replan is not True:
                errors.append(_m("val_field_must_be_nonempty_string", field=f"{prefix}: text"))

            low, high = self.VALID_UNIT_DURATION_RANGE
            blank_shell = needs_replan is True and (not isinstance(text, str) or not text.strip())
            valid_duration = False
            if isinstance(duration, int) and not isinstance(duration, bool):
                valid_duration = duration == 0 if blank_shell else low <= duration <= high
            if not valid_duration:
                errors.append(_m("val_unit_duration_range", prefix=prefix, low=low, high=high))

            # 正文里未登记的 `@[名称]` 不在此报错：参考图是执行期派生物，未解析的提及只在
            # 渲染与预览侧发一条非阻断 warning（见 ADR 0064），校验器不把它升级成结构错误。

            if project_dir is not None:
                self._validate_generated_assets(
                    project_dir,
                    prefix,
                    unit.get("generated_assets"),
                    errors,
                )

    def _validate_episode_payload(
        self,
        project_dir: Path,
        project: dict[str, Any],
        episode: dict[str, Any],
        errors: list[ValidationMessage],
        warnings: list[ValidationMessage],
        *,
        validate_artifacts: bool = True,
        validate_route: bool = True,
    ) -> None:
        project_characters = set(project.get("characters", {}).keys())
        project_scenes = set(project.get("scenes", {}).keys())
        project_props = set(project.get("props", {}).keys())

        if not isinstance(episode.get("episode"), int):
            errors.append(_m("val_episode_missing_num"))

        if not episode.get("title"):
            errors.append(_m("val_missing_field", field="title"))

        content_mode = resolve_content_mode(episode, project)

        for deprecated_field in ("characters_in_episode", "scenes_in_episode", "props_in_episode"):
            if episode.get(deprecated_field) is not None:
                warnings.append(_m("val_deprecated_field_removable", field=deprecated_field))

        novel = episode.get("novel")
        if novel is not None and not isinstance(novel, dict):
            errors.append(_m("val_novel_must_be_object"))

        # 剧情演绎说话量上界 warning 的语速从 lib.speech_rate 唯一真相源取：项目级覆盖优先，
        # 否则按项目 source_language 的语言默认（缺失 / 脏值回退默认语速）。
        source_language = project.get("source_language")
        scene_language = source_language if isinstance(source_language, str) else None
        scene_speech_rate = project_speech_rate_override(project)

        # "视频来源"维度是项目级事实（generation_mode），剧本不携带；骨架种类统一由
        # resolve_declared_kind 解析。广告/短片的参考生视频使用 video_units，生成模式为
        # `storyboard` 时使用 shots。
        gen_mode = project.get("generation_mode")
        try:
            kind = resolve_declared_kind(content_mode, gen_mode)
        except ValueError:
            # content_mode 存在但非法（遗留/脏数据）：resolve_declared_kind 对此 fail-loud
            # 抛错，但 validator 的契约是把脏数据报告成结构化错误而非让异常传播出去。跳过依赖
            # 骨架种类的后续检查——没有合法 kind 就无从判断该读 segments/scenes/shots 中哪个。
            errors.append(
                _m("val_content_mode_invalid", value=content_mode, allowed=_allowed(self.VALID_CONTENT_MODES))
            )
            return
        if validate_route:
            try:
                # 闸门放行族内历史形态（narration 数据落 scenes 键）并返回剧本的实际骨架，后续按
                # 该实际种类分派——用声明种类会让这类剧本按不存在的 segments 空读，数据一条都不校验。
                kind = ensure_route_skeleton(episode, content_mode, gen_mode)
            except SkeletonRouteMismatchError as exc:
                # 失配剧本（骨架与项目生成模式跨族）：生成模式要求的数组根本不在剧本里，
                # 逐字段报"缺少 segments"会把成因埋掉——直接给结构结论与重拆指引，并跳过后续
                # 按骨架的检查。同一闸门在生成入口拒绝生成，此处只是把同一事实报告出来。
                errors.append(exc.to_validation_message())
                return
        else:
            # 编辑/查看流程必须能修正生成模式失配的存量剧本；引用校验按磁盘实际骨架分派，
            # 生成入口仍通过默认开启的生成模式闸门拒绝将这类剧本投入生产。
            kind = resolve_script_kind(episode)
        artifact_root = project_dir if validate_artifacts else None
        if kind == "video_units":
            self._validate_reference_video_script(
                episode.get("video_units", []),
                errors,
                warnings,
                project_dir=artifact_root,
            )
        elif kind == "segments":
            self._validate_segments(
                episode.get("segments", []),
                project_characters,
                project_scenes,
                project_props,
                errors,
                warnings,
                project_dir=artifact_root,
            )
        elif kind == "shots":
            shots = episode.get("shots", [])
            raw_products = project.get("products")
            self._validate_shots(
                shots,
                project_characters,
                project_scenes,
                project_props,
                set(raw_products.keys()) if isinstance(raw_products, dict) else set(),
                errors,
                warnings,
                project_dir=artifact_root,
            )
            self._warn_ad_target_duration_drift(project, shots, warnings)
        elif kind == "scenes":
            self._validate_scenes(
                episode.get("scenes", []),
                project_characters,
                project_scenes,
                project_props,
                errors,
                warnings,
                project_dir=artifact_root,
                language=scene_language,
                speech_rate_override=scene_speech_rate,
            )
        else:  # pragma: no cover - resolve_declared_kind 只回四种骨架；第五种未处置即报错
            raise ValueError(f"未处置的骨架种类: {kind!r}")

    def validate_episode(self, project_name: str, episode_file: str) -> ValidationResult:
        """验证 episode JSON"""
        return self.validate_episode_file(self.projects_root / project_name, episode_file)

    def validate_episode_payload(
        self,
        project_dir: Path,
        project: dict[str, Any],
        episode: dict[str, Any],
        *,
        validate_artifacts: bool = True,
        validate_route: bool = True,
    ) -> ValidationResult:
        """Validate an already loaded episode without reading or rewriting project files.

        Callers that classify artifact readiness separately can disable filesystem artifact
        checks while retaining the shared episode structure and reference validation. Editing
        flows can also disable the generation-mode gate while validating references against
        the supplied episode's actual skeleton.
        """
        errors: list[ValidationMessage] = []
        warnings: list[ValidationMessage] = []
        self._validate_episode_payload(
            Path(project_dir),
            project,
            episode,
            errors,
            warnings,
            validate_artifacts=validate_artifacts,
            validate_route=validate_route,
        )
        return ValidationResult(valid=len(errors) == 0, error_messages=errors, warning_messages=warnings)

    def validate_episode_file(
        self,
        project_dir: Path,
        episode_file: str | Path,
    ) -> ValidationResult:
        """验证指定目录中的剧本文件。"""
        project_dir = Path(project_dir)
        project_path = project_dir / "project.json"
        project = load_json_or_none(project_path)
        if project is None:
            return ValidationResult(
                valid=False,
                error_messages=[_m("val_cannot_load_project_json", path=project_path)],
            )

        resolved_episode_path, error = self._resolve_existing_path(
            project_dir,
            str(episode_file),
            default_dir="scripts",
        )
        if error or resolved_episode_path is None:
            return ValidationResult(
                valid=False,
                error_messages=[_m("val_cannot_load_script", path=project_dir / str(episode_file))],
            )

        episode_path = project_dir / resolved_episode_path
        episode = load_json_or_none(episode_path)
        if episode is None:
            return ValidationResult(
                valid=False,
                error_messages=[_m("val_cannot_load_script", path=episode_path)],
            )

        return self.validate_episode_payload(project_dir, project, episode)

    def validate_project_tree(self, project_dir: str | Path) -> ValidationResult:
        """
        验证完整项目目录。

        除 project.json / episode 结构外，还会验证本地文件引用和顶层附加文件。
        """
        project_dir = Path(project_dir)
        project_result = self.validate_project_dir(project_dir)
        errors = list(project_result.error_messages)
        warnings = list(project_result.warning_messages)

        project_path = project_dir / "project.json"
        project = load_json_or_none(project_path)
        if project is None:
            return ValidationResult(valid=False, error_messages=errors, warning_messages=warnings)

        self._validate_local_reference(
            project_dir,
            project.get("style_image"),
            errors,
            "project.style_image",
        )

        characters = project.get("characters", {})
        if isinstance(characters, dict):
            for char_name, char_data in characters.items():
                if not isinstance(char_data, dict):
                    continue
                self._validate_local_reference(
                    project_dir,
                    char_data.get("character_sheet"),
                    errors,
                    f"characters[{char_name}].character_sheet",
                    default_dir="characters",
                )
                self._validate_local_reference(
                    project_dir,
                    char_data.get("reference_image"),
                    errors,
                    f"characters[{char_name}].reference_image",
                    default_dir="characters/refs",
                )
                self._validate_local_reference(
                    project_dir,
                    char_data.get("reference_audio"),
                    errors,
                    f"characters[{char_name}].reference_audio",
                    default_dir="characters/refs_audio",
                )

        scenes_dict = project.get("scenes", {})
        if isinstance(scenes_dict, dict):
            for scene_name, scene_data in scenes_dict.items():
                if not isinstance(scene_data, dict):
                    continue
                self._validate_local_reference(
                    project_dir,
                    scene_data.get("scene_sheet"),
                    errors,
                    f"scenes[{scene_name}].scene_sheet",
                    default_dir="scenes",
                )

        props_dict = project.get("props", {})
        if isinstance(props_dict, dict):
            for prop_name, prop_data in props_dict.items():
                if not isinstance(prop_data, dict):
                    continue
                self._validate_local_reference(
                    project_dir,
                    prop_data.get("prop_sheet"),
                    errors,
                    f"props[{prop_name}].prop_sheet",
                    default_dir="props",
                )

        episodes = project.get("episodes", [])
        if isinstance(episodes, list):
            for index, episode_meta in enumerate(episodes):
                if not isinstance(episode_meta, dict):
                    continue

                script_file = episode_meta.get("script_file")
                if not isinstance(script_file, str) or not script_file.strip():
                    continue

                resolved_path = self._validate_local_reference(
                    project_dir,
                    script_file,
                    errors,
                    f"episodes[{index}].script_file",
                    default_dir="scripts",
                    # 账本条目的 script_file 是前瞻性契约（剧本生成时回填真实值），
                    # 拆分先于剧本存在是设计内状态；路径越界仍照常报错。ledger_status 不
                    # 参与判定——v2→v3 迁移不再回填该字段，老项目升级后的条目可能永远
                    # 没有 ledger_status，形状合法（集号可解析为正整数）即视为正常账本条目
                    missing_ok=parse_positive_episode_num(episode_meta.get("episode")) is not None,
                )
                if not resolved_path:
                    continue

                episode = load_json_or_none(project_dir / resolved_path)
                if episode is None:
                    errors.append(_m("val_cannot_load_script", path=project_dir / resolved_path))
                    continue

                episode_errors: list[ValidationMessage] = []
                episode_warnings: list[ValidationMessage] = []
                self._validate_episode_payload(
                    project_dir,
                    project,
                    episode,
                    episode_errors,
                    episode_warnings,
                )
                errors.extend(episode_errors)
                warnings.extend(episode_warnings)

        if project_dir.exists():
            for child in sorted(project_dir.iterdir(), key=lambda item: item.name):
                if self._is_hidden_path(Path(child.name)):
                    continue
                if child.name not in self.ALLOWED_ROOT_ENTRIES:
                    warnings.append(_m("val_unrecognized_entry", name=child.name))

        return ValidationResult(valid=len(errors) == 0, error_messages=errors, warning_messages=warnings)


def validate_project(
    project_name: str,
    projects_root: str | None = None,
) -> ValidationResult:
    """验证 project.json"""
    validator = DataValidator(projects_root)
    return validator.validate_project(project_name)


def validate_episode(
    project_name: str,
    episode_file: str,
    projects_root: str | None = None,
) -> ValidationResult:
    """验证 episode JSON"""
    validator = DataValidator(projects_root)
    return validator.validate_episode(project_name, episode_file)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python data_validator.py <project_name> [episode_file]")
        print("  验证 project.json: python data_validator.py my_project")
        print("  验证 episode JSON: python data_validator.py my_project episode_1.json")
        sys.exit(1)

    project_name = sys.argv[1]

    if len(sys.argv) >= 3:
        episode_file = sys.argv[2]
        result = validate_episode(project_name, episode_file)
        print(f"验证 {project_name}/scripts/{episode_file}:")
    else:
        result = validate_project(project_name)
        print(f"验证 {project_name}/project.json:")

    print(result)
    sys.exit(0 if result.valid else 1)
