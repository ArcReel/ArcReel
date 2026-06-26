"""
数据验证工具

验证 project.json 和 episode JSON 的数据结构完整性和引用一致性。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from lib.asset_types import ASSET_SPECS, ASSET_TYPES
from lib.episode_ledger import LEDGER_STATUSES, EpisodeOutline, PlanningCursor, SourceRange
from lib.json_io import load_json_or_none
from lib.profile_manifest import VALID_CONTENT_MODES as _VALID_CONTENT_MODES
from lib.project_manager import VALID_SOURCE_KINDS as _VALID_SOURCE_KINDS
from lib.project_manager import effective_mode
from lib.script_models import (
    AD_TARGET_DURATION_DRIFT_THRESHOLD,
    REFERENCE_SHOT_DURATION_RANGE,
    ad_script_total_duration,
)
from lib.speech_rate import estimate_spoken_seconds

#: drama 场景说话量（台词 + 画外音）对场景时长的单向上界容差（比例）。语速估算
#: （``lib.speech_rate`` 单一真相源）是近似值，给 20% 余量只在说话量明显超过画面时长时才
#: warn（长对白塞进固定秒数会说不完 / 语速畸快）。单向上界：只警告「说不完」，不管「说话太少」
#: ——duration 由画面驱动、留白合法，不被此约束反向改写。仅 DataValidator 消费，与 ad 总时长
#: 漂移阈值同量级、同「只 warn 不阻塞」语义。
DRAMA_SPEECH_OVERFLOW_TOLERANCE = 0.20


@dataclass
class ValidationResult:
    """验证结果"""

    valid: bool
    errors: list[str] = field(default_factory=list)  # 错误列表（阻止继续）
    warnings: list[str] = field(default_factory=list)  # 警告列表（仅提示）

    def __str__(self) -> str:
        if self.valid:
            msg = "验证通过"
            if self.warnings:
                msg += f"\n警告 ({len(self.warnings)}):\n" + "\n".join(f"  - {warning}" for warning in self.warnings)
            return msg

        msg = f"验证失败 ({len(self.errors)} 个错误)"
        msg += "\n错误:\n" + "\n".join(f"  - {error}" for error in self.errors)
        if self.warnings:
            msg += f"\n警告 ({len(self.warnings)}):\n" + "\n".join(f"  - {warning}" for warning in self.warnings)
        return msg


def _pydantic_error_summary(exc: ValidationError) -> str:
    """把 ValidationError 压成单行 ``字段: 原因`` 摘要，供 errors 列表内嵌。"""
    return "; ".join(f"{'.'.join(str(part) for part in err['loc']) or '<root>'}: {err['msg']}" for err in exc.errors())


class DataValidator:
    """数据验证器"""

    # content_mode 严格只表达"内容类型"；"视频来源"维度由 generation_mode 字段
    # 表达，通过 project_manager.effective_mode 解析。
    # 合法集真相源在 lib.profile_manifest，避免两处枚举漂移。
    VALID_CONTENT_MODES = set(_VALID_CONTENT_MODES)
    # 源文件性质（novel / screenplay）合法集，真相源在 lib.project_manager（创建写入方），
    # 避免两处枚举漂移。缺省 novel：缺失字段不报错，仅拦截非法值（如 screen_play）。
    VALID_SOURCE_KINDS = set(_VALID_SOURCE_KINDS)
    # 参考生视频路径下单镜头时长区间，真相源在 lib.script_models（与 Shot.duration /
    # ad reference 路径的剧本模型同口径），避免两处枚举漂移。
    VALID_SHOT_DURATION_RANGE = REFERENCE_SHOT_DURATION_RANGE
    ID_PATTERN = re.compile(r"^E\d+S\d+(?:_\d+)?$")
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
        "videos",
        "audio",
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
    ) -> tuple[str | None, str | None]:
        normalized = str(raw_path).strip().replace("\\", "/")
        if not normalized:
            return None, "路径不能为空"

        candidate_paths = [Path(normalized)]
        if default_dir and len(candidate_paths[0].parts) == 1:
            candidate_paths.append(Path(default_dir) / candidate_paths[0])

        project_root = project_dir.resolve()
        seen: set[str] = set()
        for candidate in candidate_paths:
            candidate_key = candidate.as_posix()
            if candidate_key in seen:
                continue
            seen.add(candidate_key)

            try:
                resolved = (project_dir / candidate).resolve(strict=False)
                resolved.relative_to(project_root)
            except ValueError:
                return None, f"引用路径越界: {normalized}"

            if resolved.exists():
                return candidate.as_posix(), None

        if missing_ok:
            return None, None
        return None, f"引用的文件不存在: {normalized}"

    def _validate_local_reference(
        self,
        project_dir: Path,
        value: Any,
        errors: list[str],
        field_name: str,
        *,
        default_dir: str | None = None,
        allow_external: bool = False,
        missing_ok: bool = False,
    ) -> str | None:
        if value in (None, ""):
            return None
        if not isinstance(value, str):
            errors.append(f"{field_name} 必须是字符串")
            return None

        raw_value = value.strip()
        if not raw_value:
            return None

        if self.EXTERNAL_URI_PATTERN.match(raw_value):
            if allow_external:
                return raw_value
            errors.append(f"{field_name} 必须是项目内相对路径: {raw_value}")
            return None

        resolved_path, error = self._resolve_existing_path(
            project_dir,
            raw_value,
            default_dir=default_dir,
            missing_ok=missing_ok,
        )
        if error:
            errors.append(f"{field_name}: {error}")
        return resolved_path

    @staticmethod
    def _validate_episode_ledger_fields(episode: dict[str, Any], prefix: str, errors: list[str]) -> None:
        """分集账本字段的形状校验（全部可缺失 = 旧式条目），形状真相源复用 lib.episode_ledger 模型。"""
        ledger_status = episode.get("ledger_status")
        if ledger_status is not None and ledger_status not in LEDGER_STATUSES:
            errors.append(f"{prefix}: ledger_status 值无效: {ledger_status!r}，必须是 {sorted(LEDGER_STATUSES)}")

        source_range = episode.get("source_range")
        if source_range is not None:
            try:
                SourceRange.model_validate(source_range)
            except ValidationError as exc:
                errors.append(f"{prefix}: source_range 不合法: {_pydantic_error_summary(exc)}")
        if ledger_status == "unanchored" and source_range is not None:
            errors.append(f"{prefix}: unanchored 条目的 source_range 必须为 null（失锚集不持有原文范围）")

        hook = episode.get("hook")
        if hook is not None and not isinstance(hook, str):
            errors.append(f"{prefix}: hook 必须是字符串")

        outline = episode.get("outline")
        if outline is not None:
            try:
                EpisodeOutline.model_validate(outline)
            except ValidationError as exc:
                errors.append(f"{prefix}: outline 不合法: {_pydantic_error_summary(exc)}")

    @staticmethod
    def _validate_ad_project_fields(
        project: dict[str, Any],
        content_mode: Any,
        errors: list[str],
    ) -> None:
        """广告/短片项目的专属字段与恒单集约束。

        target_duration / brief 仅 ad 项目持有；ad 项目不持有 default_duration
        （镜头按目标总时长预算逐个规划，单镜头偏好无意义），episodes 恒为第 1 集单条。
        """
        if content_mode != "ad":
            if project.get("target_duration") is not None:
                errors.append("target_duration 仅广告/短片项目（content_mode=ad）可用")
            if project.get("brief") is not None:
                errors.append("brief 仅广告/短片项目（content_mode=ad）可用")
            return

        target_duration = project.get("target_duration")
        if target_duration is None:
            errors.append("缺少必填字段: target_duration（广告/短片项目的目标总时长，秒）")
        elif not isinstance(target_duration, int) or isinstance(target_duration, bool) or target_duration <= 0:
            errors.append(f"target_duration 值无效: {target_duration!r}，必须为正整数秒")

        brief = project.get("brief")
        if brief is not None and not isinstance(brief, str):
            errors.append("brief 必须是字符串")

        if project.get("default_duration") is not None:
            errors.append("广告/短片项目不持有 default_duration（镜头时长按 target_duration 预算逐镜头规划）")

        episodes = project.get("episodes")
        if not isinstance(episodes, list) or (
            len(episodes) != 1 or not isinstance(episodes[0], dict) or episodes[0].get("episode") != 1
        ):
            errors.append("广告/短片项目 episodes 必须恒为第 1 集单条")

    def _validate_project_payload(
        self,
        project: dict[str, Any],
        errors: list[str],
        warnings: list[str],
    ) -> None:
        if "title" not in project:
            errors.append("缺少必填字段: title")
        elif not isinstance(project["title"], str):
            errors.append("字段类型错误: title 应为字符串")

        content_mode = project.get("content_mode")
        if not content_mode:
            errors.append("缺少必填字段: content_mode")
        elif content_mode not in self.VALID_CONTENT_MODES:
            errors.append(f"content_mode 值无效: '{content_mode}'，必须是 {self.VALID_CONTENT_MODES}")

        # source_kind 缺省 novel：缺失字段（存量项目）放行，仅拦截非法值（如 screen_play）。
        source_kind = project.get("source_kind")
        if source_kind is not None and source_kind not in self.VALID_SOURCE_KINDS:
            errors.append(f"source_kind 值无效: '{source_kind}'，必须是 {self.VALID_SOURCE_KINDS}")

        self._validate_ad_project_fields(project, content_mode, errors)

        if not project.get("style"):
            errors.append("缺少必填字段: style")

        episodes = project.get("episodes", [])
        if not isinstance(episodes, list):
            errors.append("episodes 必须是数组")
        else:
            for index, episode in enumerate(episodes):
                prefix = f"episodes[{index}]"
                if not isinstance(episode, dict):
                    errors.append(f"{prefix}: 数据格式错误，应为对象")
                    continue

                if not isinstance(episode.get("episode"), int):
                    errors.append(f"{prefix}: 缺少必填字段 episode (整数)")
                # title 允许空串：写入方（剧本同步/账本回填）在标题未知时即写 ""，
                # 待用户或智能体后续命名
                if not isinstance(episode.get("title"), str):
                    errors.append(f"{prefix}: 缺少必填字段 title (字符串，可为空)")

                script_file = episode.get("script_file")
                if not script_file:
                    errors.append(f"{prefix}: 缺少必填字段 script_file")
                elif not isinstance(script_file, str):
                    errors.append(f"{prefix}: script_file 必须是字符串")

                self._validate_episode_ledger_fields(episode, prefix, errors)

        planning_cursor = project.get("planning_cursor")
        if planning_cursor is not None:
            try:
                PlanningCursor.model_validate(planning_cursor)
            except ValidationError as exc:
                errors.append(f"planning_cursor 不合法: {_pydantic_error_summary(exc)}")

        characters = project.get("characters", {})
        if isinstance(characters, dict):
            char_extra_fields = ASSET_SPECS["character"].extra_string_fields
            for char_name, char_data in characters.items():
                if not isinstance(char_data, dict):
                    errors.append(f"角色 '{char_name}' 数据格式错误，应为对象")
                    continue
                desc = char_data.get("description")
                if not isinstance(desc, str) or not desc:
                    # 必须是非空字符串：description 是 LLM 直写字段，agent 误传数字/对象
                    # 应在守卫点 fail-loud，否则会作为合法资产落盘、下游消费时才崩
                    errors.append(f"角色 '{char_name}' 缺少必填字段: description（须为非空字符串）")
                for field_name in char_extra_fields:
                    # spec 声明的 extra_string_fields（voice_style / reference_image 等）若存在
                    # 须为字符串（可空），否则下游消费方（如把 reference_image 当路径拼接）
                    # 会运行时崩。None 视为「未设置」放行，非 str 类型 fail-loud。
                    val = char_data.get(field_name)
                    if val is not None and not isinstance(val, str):
                        errors.append(f"角色 '{char_name}'.{field_name} 必须是字符串，当前为 {type(val).__name__}")

        if project.get("clues") is not None:
            errors.append("project.json 含已废弃字段 clues，请等待自动迁移或手动重启服务")

        self._validate_project_catalog(
            project.get("scenes") or {},
            errors,
            field_label="scenes",
            kind_label="场景",
        )
        self._validate_project_catalog(
            project.get("props") or {},
            errors,
            field_label="props",
            kind_label="道具",
        )
        self._validate_project_catalog(
            project.get("products") or {},
            errors,
            field_label="products",
            kind_label="产品",
        )

    def _validate_project_catalog(
        self,
        catalog: Any,
        errors: list[str],
        *,
        field_label: str,
        kind_label: str,
    ) -> None:
        if not isinstance(catalog, dict):
            errors.append(f"{field_label} 必须是对象")
            return
        # scene/prop 的 extra_string_fields 当前均为空 tuple（见 ASSET_SPECS），仍按 spec 取
        # 以保持「validator 跟 spec 同步」——将来给 scenes/props 加 extra 字段时无需改本处。
        asset_type = field_label.rstrip("s")  # "scenes" → "scene"; "products" → "product"
        spec = ASSET_SPECS.get(asset_type)
        extra_fields = spec.extra_string_fields if spec else ()
        extra_list_fields = spec.extra_list_fields if spec else ()
        for name, data in catalog.items():
            if not isinstance(data, dict):
                errors.append(f"{kind_label} '{name}' 数据格式错误，应为对象")
                continue
            desc = data.get("description")
            if not isinstance(desc, str) or not desc:
                # 同 characters：description 须为非空字符串，避免数字/对象被 truthy 判通过
                errors.append(f"{kind_label} '{name}' 缺少必填字段: description（须为非空字符串）")
            for field_name in extra_fields:
                val = data.get(field_name)
                if val is not None and not isinstance(val, str):
                    errors.append(f"{kind_label} '{name}'.{field_name} 必须是字符串，当前为 {type(val).__name__}")
            for field_name in extra_list_fields:
                # spec 声明的 extra_list_fields（reference_images / selling_points 等）若存在
                # 须为字符串列表：下游把元素当路径拼接 / 当文本注入 prompt，混入非 str 会
                # 运行时崩。None 视为「未设置」放行，其余类型 fail-loud。
                val = data.get(field_name)
                if val is None:
                    continue
                if not isinstance(val, list):
                    errors.append(f"{kind_label} '{name}'.{field_name} 必须是字符串列表，当前为 {type(val).__name__}")
                    continue
                for idx, item in enumerate(val):
                    if not isinstance(item, str):
                        errors.append(
                            f"{kind_label} '{name}'.{field_name}[{idx}] 必须是字符串，当前为 {type(item).__name__}"
                        )

    def _validate_segment_refs(
        self,
        prefix: str,
        refs: Any,
        valid_set: set[str],
        errors: list[str],
        warnings: list[str],
        *,
        field_label: str,
        kind_label: str,
    ) -> None:
        if refs is None:
            warnings.append(f"{prefix}: 缺少 {field_label}，将使用默认空数组")
            return
        if not isinstance(refs, list):
            errors.append(f"{prefix}: {field_label} 必须是数组")
            return
        invalid = set(refs) - valid_set
        if invalid:
            errors.append(f"{prefix}: {field_label} 引用了不存在于 project.json 的{kind_label}: {invalid}")

    def validate_project_payload(self, project: dict[str, Any]) -> ValidationResult:
        """对内存中的 project.json dict 做结构校验（不读盘）。

        供写入前校验复用——`patch_project` 在 `update_project` 的 mutation 内 apply 改动后、
        落盘前调用本方法，非法则中止写入，避免「先写后验、失败仍留脏数据」。
        """
        errors: list[str] = []
        warnings: list[str] = []
        self._validate_project_payload(project, errors, warnings)
        return ValidationResult(valid=len(errors) == 0, errors=errors, warnings=warnings)

    def validate_project(self, project_name: str) -> ValidationResult:
        """验证 project.json"""
        return self.validate_project_dir(self.projects_root / project_name)

    def validate_project_dir(self, project_dir: Path) -> ValidationResult:
        """验证指定目录中的 project.json。"""
        errors: list[str] = []
        warnings: list[str] = []

        project_path = Path(project_dir) / "project.json"
        project = load_json_or_none(project_path)
        if project is None:
            return ValidationResult(
                valid=False,
                errors=[f"无法加载 project.json: {project_path}"],
            )

        self._validate_project_payload(project, errors, warnings)
        return ValidationResult(valid=len(errors) == 0, errors=errors, warnings=warnings)

    def _validate_generated_assets(
        self,
        project_dir: Path,
        prefix: str,
        assets: Any,
        errors: list[str],
    ) -> None:
        if assets in (None, ""):
            return
        if not isinstance(assets, dict):
            errors.append(f"{prefix}.generated_assets 必须是对象")
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

    def _validate_segments(
        self,
        segments: list[dict[str, Any]],
        project_characters: set[str],
        project_scenes: set[str],
        project_props: set[str],
        errors: list[str],
        warnings: list[str],
        *,
        project_dir: Path | None = None,
    ) -> None:
        """验证 segments（narration 模式）"""
        if not segments:
            errors.append("segments 数组为空")
            return

        for index, segment in enumerate(segments):
            prefix = f"segments[{index}]"

            segment_id = segment.get("segment_id")
            if not segment_id:
                errors.append(f"{prefix}: 缺少必填字段 segment_id")
            elif not self.ID_PATTERN.match(segment_id):
                errors.append(f"{prefix}: segment_id 格式错误 '{segment_id}'，应为 E{{n}}S{{nn}}")

            duration = segment.get("duration_seconds")
            if duration is None:
                warnings.append(f"{prefix}: 缺少 duration_seconds，将使用默认值 4")
            elif not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
                errors.append(f"{prefix}: duration_seconds 值无效 '{duration}'，必须为正整数")

            if not segment.get("novel_text"):
                errors.append(f"{prefix}: 缺少必填字段 novel_text")

            chars_in_segment = segment.get("characters_in_segment")
            if chars_in_segment is None:
                errors.append(f"{prefix}: 缺少必填字段 characters_in_segment")
            elif not isinstance(chars_in_segment, list):
                errors.append(f"{prefix}: characters_in_segment 必须是数组")
            else:
                invalid = set(chars_in_segment) - project_characters
                if invalid:
                    errors.append(f"{prefix}: characters_in_segment 引用了不存在于 project.json 的角色: {invalid}")

            self._validate_segment_refs(
                prefix,
                segment.get("scenes"),
                project_scenes,
                errors,
                warnings,
                field_label="scenes",
                kind_label="场景",
            )
            self._validate_segment_refs(
                prefix,
                segment.get("props"),
                project_props,
                errors,
                warnings,
                field_label="props",
                kind_label="道具",
            )

            if not segment.get("image_prompt"):
                errors.append(f"{prefix}: 缺少必填字段 image_prompt")
            if not segment.get("video_prompt"):
                errors.append(f"{prefix}: 缺少必填字段 video_prompt")

            if project_dir is not None:
                self._validate_generated_assets(
                    project_dir,
                    prefix,
                    segment.get("generated_assets"),
                    errors,
                )

    def _validate_scenes(
        self,
        scenes: list[dict[str, Any]],
        project_characters: set[str],
        project_scenes: set[str],
        project_props: set[str],
        errors: list[str],
        warnings: list[str],
        *,
        project_dir: Path | None = None,
        language: str | None = None,
    ) -> None:
        """验证 scenes（drama 模式）。

        ``language`` 为项目 ``source_language``（说话量上界 warning 的语速按此取，与字幕派生同口径）。
        """
        if not scenes:
            errors.append("scenes 数组为空")
            return

        for index, scene in enumerate(scenes):
            prefix = f"scenes[{index}]"

            scene_id = scene.get("scene_id")
            if not scene_id:
                errors.append(f"{prefix}: 缺少必填字段 scene_id")
            elif not self.ID_PATTERN.match(scene_id):
                errors.append(f"{prefix}: scene_id 格式错误 '{scene_id}'，应为 E{{n}}S{{nn}}")

            duration = scene.get("duration_seconds")
            if duration is None:
                warnings.append(f"{prefix}: 缺少 duration_seconds，将使用默认值 8")
            elif not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
                errors.append(f"{prefix}: duration_seconds 值无效 '{duration}'，必须为正整数")

            chars_in_scene = scene.get("characters_in_scene")
            if chars_in_scene is None:
                errors.append(f"{prefix}: 缺少必填字段 characters_in_scene")
            elif not isinstance(chars_in_scene, list):
                errors.append(f"{prefix}: characters_in_scene 必须是数组")
            else:
                invalid = set(chars_in_scene) - project_characters
                if invalid:
                    errors.append(f"{prefix}: characters_in_scene 引用了不存在于 project.json 的角色: {invalid}")

            scenes_in_scene = scene.get("scenes")
            if scenes_in_scene is None:
                warnings.append(f"{prefix}: 缺少 scenes，将使用默认空数组")
            elif not isinstance(scenes_in_scene, list):
                errors.append(f"{prefix}: scenes 必须是数组")
            else:
                invalid = set(scenes_in_scene) - project_scenes
                if invalid:
                    errors.append(f"{prefix}: scenes 引用了不存在于 project.json 的场景: {invalid}")

            props_in_scene = scene.get("props")
            if props_in_scene is None:
                warnings.append(f"{prefix}: 缺少 props，将使用默认空数组")
            elif not isinstance(props_in_scene, list):
                errors.append(f"{prefix}: props 必须是数组")
            else:
                invalid = set(props_in_scene) - project_props
                if invalid:
                    errors.append(f"{prefix}: props 引用了不存在于 project.json 的道具: {invalid}")

            # utterances：场景级有序发声序列（取代旧 video_prompt.dialogue + voiceover）。
            # 缺失放行（存量 drama 走读时迁移，旧双字段不在此层校验）；出现则校验结构与
            # kind ⇄ speaker 约束，与上方 characters_in_scene 等同口径（逐项 append、不 raise）。
            self._validate_utterances(scene.get("utterances"), prefix, errors)

            # 说话量（台词 + 画外音）对场景时长的单向上界 warning：估算说话时长超 duration × 容差
            # 时仅提示「说不完」，不阻塞、不改写 duration（duration 由画面驱动）。
            self._warn_scene_speech_overflow(scene, prefix, language, warnings)

            if not scene.get("image_prompt"):
                errors.append(f"{prefix}: 缺少必填字段 image_prompt")
            if not scene.get("video_prompt"):
                errors.append(f"{prefix}: 缺少必填字段 video_prompt")

            if project_dir is not None:
                self._validate_generated_assets(
                    project_dir,
                    prefix,
                    scene.get("generated_assets"),
                    errors,
                )

    @staticmethod
    def _validate_utterances(utterances: Any, prefix: str, errors: list[str]) -> None:
        """校验 drama 场景级 utterances 的结构与 kind ⇄ speaker 约束（缺失放行，存量走读时迁移）。

        每条须为 ``{kind, speaker, text}``：kind ∈ {dialogue, voiceover}、text 非空字符串；
        dialogue 必带非空 speaker、voiceover 不得带 speaker。逐项 append、不 raise。
        """
        if utterances is None:
            return
        if not isinstance(utterances, list):
            errors.append(f"{prefix}: utterances 必须是数组")
            return
        for ui, item in enumerate(utterances):
            uprefix = f"{prefix}: utterances[{ui}]"
            if not isinstance(item, dict):
                errors.append(f"{uprefix} 必须是对象")
                continue
            kind = item.get("kind")
            if kind not in ("dialogue", "voiceover"):
                errors.append(f"{uprefix} kind 必须是 dialogue 或 voiceover")
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                errors.append(f"{uprefix} text 必须是非空字符串")
            speaker = item.get("speaker")
            if speaker is not None and not isinstance(speaker, str):
                # speaker 仅允许字符串或 null（镜像 Utterance.speaker: str | None 的类型约束）：
                # 数字 / 布尔 / 对象等在 Pydantic 层即类型校验失败，这里同口径 fail-loud，避免
                # 非法 shape 在结构校验里静默放行、到 Pydantic 才崩。置 has_speaker=True 表示
                # 「提供了 speaker」，使 dialogue 不再叠报「缺 speaker」（类型错才是根因）。
                errors.append(f"{uprefix} speaker 必须是字符串或 null")
                has_speaker = True
            else:
                # 字符串（空串 / 纯空白按 Utterance._normalize_speaker 同口径归一为「无 speaker」）或 None
                has_speaker = isinstance(speaker, str) and bool(speaker.strip())
            if kind == "dialogue" and not has_speaker:
                errors.append(f"{uprefix} dialogue 必须带非空 speaker")
            elif kind == "voiceover" and has_speaker:
                errors.append(f"{uprefix} voiceover 不得带 speaker")

    @staticmethod
    def _warn_scene_speech_overflow(
        scene: dict[str, Any],
        prefix: str,
        language: str | None,
        warnings: list[str],
    ) -> None:
        """场景说话量（台词 + 画外音）估算时长超 ``duration ×（1 + 容差）`` 时仅 warn（单向上界，不阻塞）。

        说话时长按 ``estimate_spoken_seconds``（语速估算单一真相源）对 utterances 逐条求和，与 drama
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
                spoken += estimate_spoken_seconds(text, language)
        budget = duration * (1 + DRAMA_SPEECH_OVERFLOW_TOLERANCE)
        if spoken > budget:
            warnings.append(
                f"{prefix}: 估算说话时长 {spoken:.1f} 秒超过场景时长 {duration} 秒逾 "
                f"{DRAMA_SPEECH_OVERFLOW_TOLERANCE:.0%}（容差上界 {budget:.1f} 秒），"
                f"长对白可能说不完或语速畸快（仅提示，不阻塞保存）"
            )

    def _validate_shots(
        self,
        shots: list[dict[str, Any]] | Any,
        project_characters: set[str],
        project_scenes: set[str],
        project_props: set[str],
        project_products: set[str],
        errors: list[str],
        warnings: list[str],
        *,
        project_dir: Path | None = None,
        reference_mode: bool = False,
    ) -> None:
        """验证 shots（ad 模式）：平铺镜头列表，口播文案一等，产品按名字引用。

        镜头时长约束按生成路径动态切换：storyboard 路径的成员校验在生成 schema 层
        （supported_durations 枚举，校验器拿不到供应商能力、只把关正整数）；
        ``reference_mode=True`` 时按 1-15 自由整数区间校验（与参考视频 Shot 同口径）。
        """
        if not isinstance(shots, list) or not shots:
            errors.append("ad 剧本缺少 shots 数组或为空")
            return

        low, high = self.VALID_SHOT_DURATION_RANGE
        for index, shot in enumerate(shots):
            prefix = f"shots[{index}]"
            if not isinstance(shot, dict):
                errors.append(f"{prefix}: 必须是对象")
                continue

            shot_id = shot.get("shot_id")
            if not shot_id:
                errors.append(f"{prefix}: 缺少必填字段 shot_id")
            elif not isinstance(shot_id, str) or not self.ID_PATTERN.match(shot_id):
                errors.append(f"{prefix}: shot_id 格式错误 '{shot_id}'，应为 E{{n}}S{{nn}}")

            duration = shot.get("duration_seconds")
            if duration is None:
                warnings.append(f"{prefix}: 缺少 duration_seconds，将按 0 计入总时长")
            elif not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
                errors.append(f"{prefix}: duration_seconds 值无效 '{duration}'，必须为正整数")
            elif reference_mode and not (low <= duration <= high):
                errors.append(
                    f"{prefix}: duration_seconds 值无效 '{duration}'，"
                    f"reference_video 路径必须是 {low}-{high} 之间的整数"
                )

            if "voiceover_text" not in shot:
                errors.append(f"{prefix}: 缺少必填字段 voiceover_text（口播文案，可为空字符串）")
            elif not isinstance(shot.get("voiceover_text"), str):
                errors.append(f"{prefix}: voiceover_text 必须是字符串")

            section = shot.get("section")
            if section is not None and not isinstance(section, str):
                errors.append(f"{prefix}: section 必须是字符串")

            self._validate_segment_refs(
                prefix,
                shot.get("characters_in_shot"),
                project_characters,
                errors,
                warnings,
                field_label="characters_in_shot",
                kind_label="角色",
            )
            self._validate_segment_refs(
                prefix,
                shot.get("scenes"),
                project_scenes,
                errors,
                warnings,
                field_label="scenes",
                kind_label="场景",
            )
            self._validate_segment_refs(
                prefix,
                shot.get("props"),
                project_props,
                errors,
                warnings,
                field_label="props",
                kind_label="道具",
            )
            self._validate_segment_refs(
                prefix,
                shot.get("products_in_shot"),
                project_products,
                errors,
                warnings,
                field_label="products_in_shot",
                kind_label="产品",
            )

            if not shot.get("image_prompt"):
                errors.append(f"{prefix}: 缺少必填字段 image_prompt")
            if not shot.get("video_prompt"):
                errors.append(f"{prefix}: 缺少必填字段 video_prompt")

            if project_dir is not None:
                self._validate_generated_assets(
                    project_dir,
                    prefix,
                    shot.get("generated_assets"),
                    errors,
                )

    @staticmethod
    def _warn_ad_target_duration_drift(
        project: dict[str, Any],
        shots: Any,
        warnings: list[str],
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
                f"剧本总时长 {total} 秒与 target_duration {target} 秒偏差 {delta_ratio:.0%}，"
                f"超过 {AD_TARGET_DURATION_DRIFT_THRESHOLD:.0%} 观察阈值（仅提示，不阻塞保存）"
            )

    def _validate_reference_video_script(
        self,
        video_units: list[dict[str, Any]] | Any,
        project_characters: set[str],
        project_scenes: set[str],
        project_props: set[str],
        errors: list[str],
        warnings: list[str],
        *,
        project_dir: Path | None = None,
    ) -> None:
        """验证 video_units（reference_video 模式）"""
        if not isinstance(video_units, list) or not video_units:
            errors.append("reference_video 脚本缺少 video_units 数组或为空")
            return

        bucket_by_type = {
            "character": project_characters,
            "scene": project_scenes,
            "prop": project_props,
        }

        for index, unit in enumerate(video_units):
            prefix = f"video_units[{index}]"
            if not isinstance(unit, dict):
                errors.append(f"{prefix}: 必须是对象")
                continue

            if not unit.get("unit_id"):
                errors.append(f"{prefix}: 缺少 unit_id")

            shots = unit.get("shots")
            if not isinstance(shots, list) or not shots:
                errors.append(f"{prefix}: shots 必须是非空数组")
            else:
                for si, shot in enumerate(shots):
                    sp = f"{prefix}.shots[{si}]"
                    if not isinstance(shot, dict):
                        errors.append(f"{sp}: 必须是对象")
                        continue
                    duration = shot.get("duration")
                    low, high = self.VALID_SHOT_DURATION_RANGE
                    if not isinstance(duration, int) or duration < low or duration > high:
                        errors.append(f"{sp}: duration 必须是 {low}-{high} 之间的整数")
                    if not isinstance(shot.get("text"), str):
                        errors.append(f"{sp}: text 必须是字符串")

            refs = unit.get("references")
            if refs is None:
                refs = []
            elif not isinstance(refs, list):
                errors.append(f"{prefix}: references 必须是数组")
                refs = []
            for ref in refs:
                if not isinstance(ref, dict):
                    errors.append(f"{prefix}: reference 条目必须是对象")
                    continue
                rtype = ref.get("type")
                rname = ref.get("name")
                if rtype not in ASSET_TYPES:
                    errors.append(f"{prefix}: reference.type 无效: {rtype!r}")
                    continue
                if not isinstance(rname, str) or not rname:
                    errors.append(f"{prefix}: reference.name 必须是非空字符串: {rname!r}")
                    continue
                bucket = bucket_by_type.get(rtype, set())
                if rname not in bucket:
                    errors.append(f"{prefix}: 引用的{rtype} '{rname}' 不在 project.json 对应 bucket 中")

            if project_dir is not None:
                self._validate_generated_assets(
                    project_dir,
                    prefix,
                    unit.get("generated_assets"),
                    errors,
                )

    def _validate_ad_reference_units(
        self,
        units: Any,
        shots: Any,
        registered_names: dict[str, set[str]],
        errors: list[str],
        warnings: list[str],
    ) -> None:
        """验证 ad 参考直出派生索引（reference_units，可缺省）。

        结构形状问题（非对象条目、缺 shot_ids、非法引用类型）报 error；引用层面的
        漂移（shot_id 悬空、引用未注册资产）报 warning——shots 是内容唯一真相，
        镜头删除后索引短暂悬空是合法中间态，重新派生即愈，不应阻塞归档/修复流程。
        """
        if units is None:
            return
        if not isinstance(units, list):
            errors.append("reference_units 必须是数组")
            return

        shot_ids = {s.get("shot_id") for s in shots if isinstance(s, dict)} if isinstance(shots, list) else set()
        for index, unit in enumerate(units):
            prefix = f"reference_units[{index}]"
            if not isinstance(unit, dict):
                errors.append(f"{prefix}: 必须是对象")
                continue

            unit_id = unit.get("unit_id")
            if not unit_id or not isinstance(unit_id, str):
                errors.append(f"{prefix}: 缺少必填字段 unit_id")

            ids = unit.get("shot_ids")
            if not isinstance(ids, list) or not ids:
                errors.append(f"{prefix}: shot_ids 必须是非空数组")
            else:
                dangling = [str(sid) for sid in ids if sid not in shot_ids]
                if dangling:
                    warnings.append(f"{prefix}: 引用的镜头不存在（{', '.join(dangling)}），需重新派生分组")

            refs = unit.get("references")
            if refs is None:
                continue
            if not isinstance(refs, list):
                errors.append(f"{prefix}: references 必须是数组")
                continue
            for ri, ref in enumerate(refs):
                if not isinstance(ref, dict):
                    errors.append(f"{prefix}.references[{ri}]: 必须是对象")
                    continue
                rtype = ref.get("type")
                rname = ref.get("name")
                if rtype not in registered_names:
                    errors.append(f"{prefix}.references[{ri}]: type 无效: {rtype!r}")
                    continue
                if not rname or not isinstance(rname, str):
                    errors.append(f"{prefix}.references[{ri}]: name 必须是非空字符串: {rname!r}")
                    continue
                if rname not in registered_names[rtype]:
                    warnings.append(f"{prefix}.references[{ri}]: 引用的{rtype}「{rname}」未注册，需重新派生分组")

    def _validate_episode_payload(
        self,
        project_dir: Path,
        project: dict[str, Any],
        episode: dict[str, Any],
        errors: list[str],
        warnings: list[str],
    ) -> None:
        project_characters = set(project.get("characters", {}).keys())
        project_scenes = set(project.get("scenes", {}).keys())
        project_props = set(project.get("props", {}).keys())

        if not isinstance(episode.get("episode"), int):
            errors.append("缺少必填字段: episode (整数)")

        if not episode.get("title"):
            errors.append("缺少必填字段: title")

        content_mode = episode.get(
            "content_mode",
            project.get("content_mode", "narration"),
        )

        characters_in_episode = episode.get("characters_in_episode")
        if characters_in_episode is not None:
            warnings.append("characters_in_episode 字段已废弃（改为读时计算），可安全移除")

        if episode.get("scenes_in_episode") is not None:
            warnings.append("scenes_in_episode 字段已废弃（改为读时计算），可安全移除")

        if episode.get("props_in_episode") is not None:
            warnings.append("props_in_episode 字段已废弃（改为读时计算），可安全移除")

        novel = episode.get("novel")
        if novel is not None and not isinstance(novel, dict):
            errors.append("novel 字段必须是对象")

        # drama 说话量上界 warning 的语速按项目 source_language 取（唯一真相源，缺失 / 脏值回退默认语速）。
        source_language = project.get("source_language")
        scene_language = source_language if isinstance(source_language, str) else None

        # "视频来源"维度由 generation_mode 表达；content_mode 决定剧本数据排布
        # （segments / scenes / shots）。ad 剧本骨架唯一、不随生成路径更换：
        # 即使 generation_mode=reference_video 也按 shots 校验（见 docs/adr/0033）。
        is_reference = content_mode != "ad" and effective_mode(project=project, episode=episode) == "reference_video"
        if is_reference:
            self._validate_reference_video_script(
                episode.get("video_units", []),
                project_characters,
                project_scenes,
                project_props,
                errors,
                warnings,
                project_dir=project_dir,
            )
        elif content_mode == "narration":
            self._validate_segments(
                episode.get("segments", []),
                project_characters,
                project_scenes,
                project_props,
                errors,
                warnings,
                project_dir=project_dir,
            )
        elif content_mode == "ad":
            raw_products = project.get("products")
            shots = episode.get("shots", [])
            self._validate_shots(
                shots,
                project_characters,
                project_scenes,
                project_props,
                set(raw_products.keys()) if isinstance(raw_products, dict) else set(),
                errors,
                warnings,
                project_dir=project_dir,
                reference_mode=effective_mode(project=project, episode=episode) == "reference_video",
            )
            self._warn_ad_target_duration_drift(project, shots, warnings)
            self._validate_ad_reference_units(
                episode.get("reference_units"),
                shots,
                {
                    "character": project_characters,
                    "scene": project_scenes,
                    "prop": project_props,
                    "product": set(raw_products.keys()) if isinstance(raw_products, dict) else set(),
                },
                errors,
                warnings,
            )
        else:
            self._validate_scenes(
                episode.get("scenes", []),
                project_characters,
                project_scenes,
                project_props,
                errors,
                warnings,
                project_dir=project_dir,
                language=scene_language,
            )

    def validate_episode(self, project_name: str, episode_file: str) -> ValidationResult:
        """验证 episode JSON"""
        return self.validate_episode_file(self.projects_root / project_name, episode_file)

    def validate_episode_file(
        self,
        project_dir: Path,
        episode_file: str | Path,
    ) -> ValidationResult:
        """验证指定目录中的剧本文件。"""
        errors: list[str] = []
        warnings: list[str] = []

        project_dir = Path(project_dir)
        project_path = project_dir / "project.json"
        project = load_json_or_none(project_path)
        if project is None:
            return ValidationResult(
                valid=False,
                errors=[f"无法加载 project.json: {project_path}"],
            )

        resolved_episode_path, error = self._resolve_existing_path(
            project_dir,
            str(episode_file),
            default_dir="scripts",
        )
        if error or resolved_episode_path is None:
            return ValidationResult(
                valid=False,
                errors=[f"无法加载剧本文件: {project_dir / str(episode_file)}"],
            )

        episode_path = project_dir / resolved_episode_path
        episode = load_json_or_none(episode_path)
        if episode is None:
            return ValidationResult(
                valid=False,
                errors=[f"无法加载剧本文件: {episode_path}"],
            )

        self._validate_episode_payload(project_dir, project, episode, errors, warnings)
        return ValidationResult(valid=len(errors) == 0, errors=errors, warnings=warnings)

    def validate_project_tree(self, project_dir: str | Path) -> ValidationResult:
        """
        验证完整项目目录。

        除 project.json / episode 结构外，还会验证本地文件引用和顶层附加文件。
        """
        project_dir = Path(project_dir)
        project_result = self.validate_project_dir(project_dir)
        errors = list(project_result.errors)
        warnings = list(project_result.warnings)

        project_path = project_dir / "project.json"
        project = load_json_or_none(project_path)
        if project is None:
            return ValidationResult(valid=False, errors=errors, warnings=warnings)

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
                    # 拆分先于剧本存在是设计内状态；路径越界仍照常报错
                    missing_ok=episode_meta.get("ledger_status") is not None,
                )
                if not resolved_path:
                    continue

                episode = load_json_or_none(project_dir / resolved_path)
                if episode is None:
                    errors.append(f"无法加载剧本文件: {project_dir / resolved_path}")
                    continue

                episode_errors: list[str] = []
                episode_warnings: list[str] = []
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
                    warnings.append(f"发现未识别的附加文件/目录: {child.name}")

        return ValidationResult(valid=len(errors) == 0, errors=errors, warnings=warnings)


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
