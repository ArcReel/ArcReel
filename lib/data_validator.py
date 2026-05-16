"""
数据验证工具

验证 project.json 和 episode JSON 的数据结构完整性和引用一致性。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lib.asset_types import ASSET_TYPES
from lib.json_io import load_json_or_none
from lib.project_manager import effective_mode


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


class DataValidator:
    """数据验证器"""

    # content_mode 严格只表达"内容类型"（issue #542）。"视频来源"维度由
    # project_manager.effective_mode（基于 generation_mode 字段）独立表达；旧数据
    # 里把 reference_video 错塞到 content_mode 的情况由 ProjectManager 写时迁移。
    VALID_CONTENT_MODES = {"narration", "drama"}
    VALID_SCENE_TYPES = {"剧情", "空镜"}
    VALID_SHOT_DURATION_RANGE = (1, 15)
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
        "reference_videos",
        "storyboards",
        "videos",
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
        )
        if error:
            errors.append(f"{field_name}: {error}")
        return resolved_path

    def _validate_project_payload(
        self,
        project: dict[str, Any],
        errors: list[str],
        warnings: list[str],
    ) -> None:
        if not project.get("title"):
            errors.append("缺少必填字段: title")

        content_mode = project.get("content_mode")
        if not content_mode:
            errors.append("缺少必填字段: content_mode")
        elif content_mode not in self.VALID_CONTENT_MODES:
            errors.append(f"content_mode 值无效: '{content_mode}'，必须是 {self.VALID_CONTENT_MODES}")

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
                if not episode.get("title"):
                    errors.append(f"{prefix}: 缺少必填字段 title")

                script_file = episode.get("script_file")
                if not script_file:
                    errors.append(f"{prefix}: 缺少必填字段 script_file")
                elif not isinstance(script_file, str):
                    errors.append(f"{prefix}: script_file 必须是字符串")

        characters = project.get("characters", {})
        if isinstance(characters, dict):
            for char_name, char_data in characters.items():
                if not isinstance(char_data, dict):
                    errors.append(f"角色 '{char_name}' 数据格式错误，应为对象")
                    continue
                if not char_data.get("description"):
                    errors.append(f"角色 '{char_name}' 缺少必填字段: description")

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
        for name, data in catalog.items():
            if not isinstance(data, dict):
                errors.append(f"{kind_label} '{name}' 数据格式错误，应为对象")
                continue
            if not data.get("description"):
                errors.append(f"{kind_label} '{name}' 缺少必填字段: description")

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
    ) -> None:
        """验证 scenes（drama 模式）"""
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

            scene_type = scene.get("scene_type")
            if not scene_type:
                errors.append(f"{prefix}: 缺少必填字段 scene_type")
            elif scene_type not in self.VALID_SCENE_TYPES:
                errors.append(f"{prefix}: scene_type 值无效 '{scene_type}'，必须是 {self.VALID_SCENE_TYPES}")

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

        # "视频来源"维度由 generation_mode 表达；content_mode 只决定 narration vs drama
        # 之间如何排布数据（segments vs scenes）。issue #542
        is_reference = effective_mode(project=project, episode=episode) == "reference_video"
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
        else:
            self._validate_scenes(
                episode.get("scenes", []),
                project_characters,
                project_scenes,
                project_props,
                errors,
                warnings,
                project_dir=project_dir,
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
