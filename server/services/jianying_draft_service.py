"""剪映草稿导出服务

将 ArcReel 单集已生成的视频片段导出为剪映草稿 ZIP。
使用 pyJianYingDraft 库生成 draft_content.json，
后处理路径替换使草稿指向用户本地剪映目录。
"""

import json
import logging
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import pyJianYingDraft as draft
from pyJianYingDraft import (
    AudioMaterial,
    AudioSegment,
    ClipSettings,
    TextBorder,
    TextSegment,
    TextShadow,
    TextStyle,
    TrackType,
    TransitionType,
    VideoMaterial,
    VideoSegment,
    trange,
)

# transition_to_next schema 值 → 剪映 TransitionType。"cut" 不挂转场。
_TRANSITION_MAP: dict[str, TransitionType] = {
    "fade": TransitionType.闪黑,
    "dissolve": TransitionType.叠化,
}

# content_mode → 字幕文案源字段。narration 按朗读原文、ad 按每镜头口播文案；
# 未注册的模式（drama / 未知脏值）不挂字幕轨。
_SUBTITLE_TEXT_FIELDS: dict[str, str] = {
    "narration": "novel_text",
    "ad": "voiceover_text",
}

from lib.path_safety import safe_resolve
from lib.project_manager import ProjectManager
from lib.script_models import script_shape

logger = logging.getLogger(__name__)


def _script_content_mode(script: dict) -> str:
    """读取剧本 content_mode；非字符串脏值归一为空串（落 drama 形状、无字幕轨）。

    旧实现对任意非 "narration" 值都按等值比较降级到 drama 路径；归一后
    dict 成员判定（``script_shape`` / ``_SUBTITLE_TEXT_FIELDS``）不会因
    不可哈希的脏值抛 TypeError，降级行为与历史一致。
    """
    value = script.get("content_mode", "narration")
    return value if isinstance(value, str) else ""


class JianyingDraftService:
    """剪映草稿导出服务"""

    def __init__(self, project_manager: ProjectManager):
        self.pm = project_manager

    # ------------------------------------------------------------------
    # 内部方法：数据提取
    # ------------------------------------------------------------------

    def _find_episode_script(self, project_name: str, project: dict, episode: int) -> tuple[dict, str]:
        """定位指定集的剧本文件，返回 (script_dict, filename)"""
        episodes = project.get("episodes", [])
        ep_entry = next((e for e in episodes if e.get("episode") == episode), None)
        if ep_entry is None:
            raise FileNotFoundError(f"第 {episode} 集不存在")

        script_file = ep_entry.get("script_file", "")
        filename = Path(script_file).name
        script_data = self.pm.load_script(project_name, filename)
        return script_data, filename

    def _collect_video_clips(self, script: dict, project_dir: Path) -> list[dict[str, Any]]:
        """从剧本中提取已完成视频的片段列表

        分镜列表与 id 字段按 ``script_shape`` 分派（narration→segments、drama→scenes、
        ad→shots，未知模式沿用 drama 形状兜底）；字幕文案按 ``_SUBTITLE_TEXT_FIELDS``
        取各模式的文案源字段，归一到 ``subtitle_text``。
        """
        content_mode = _script_content_mode(script)
        shape = script_shape(content_mode)
        items = script.get(shape.items_key, [])
        subtitle_field = _SUBTITLE_TEXT_FIELDS.get(content_mode)

        clips = []
        for item in items:
            assets = item.get("generated_assets") or {}
            video_clip = assets.get("video_clip")
            if not video_clip:
                continue

            abs_path = safe_resolve(project_dir, video_clip)
            if abs_path is None:
                logger.warning("video_clip 不可用（越界或文件不存在），已跳过: %s", video_clip)
                continue

            # 字幕文案只接受字符串：手编剧本写入数字/列表等脏值时按缺失处理，
            # 不让单镜头脏数据把整次导出带崩（TextSegment 对非 str 序列化即抛错）
            subtitle_value = item.get(subtitle_field) if subtitle_field else None

            clips.append(
                {
                    "id": item.get(shape.id_field, ""),
                    "duration_seconds": item.get("duration_seconds", 8),
                    "video_clip": video_clip,
                    "abs_path": abs_path,
                    "subtitle_text": subtitle_value if isinstance(subtitle_value, str) else "",
                    "transition_to_next": item.get("transition_to_next", "cut"),
                    "narration_audio_abs": safe_resolve(project_dir, assets.get("narration_audio")),
                }
            )

        return clips

    def _resolve_canvas_size(self, project: dict, first_video_path: Path | None = None) -> tuple[int, int]:
        """根据项目 aspect_ratio 确定画布尺寸，缺失时从首个视频自动检测"""
        ar = project.get("aspect_ratio")
        aspect = ar if isinstance(ar, str) else (ar.get("video") if isinstance(ar, dict) else None)
        if aspect is None and first_video_path is not None:
            mat = VideoMaterial(str(first_video_path))
            aspect = "9:16" if mat.height > mat.width else "16:9"
        if aspect == "9:16":
            return 1080, 1920
        return 1920, 1080

    @staticmethod
    def _stage_file(src: Path, staging_dir: Path) -> Path:
        """将素材文件硬链接（失败时复制）到暂存区，返回暂存路径

        暂存区为扁平目录：来源文件同名时自动改名，避免覆盖已暂存的素材。
        同一来源的去重由调用方按源路径判定（不依赖 inode 比较，FAT/exFAT 等
        无稳定文件 ID 的文件系统上 samefile 会误判）。
        """
        dst = staging_dir / src.name
        rename_index = 1
        while dst.exists():
            dst = staging_dir / f"{src.stem}_{rename_index}{src.suffix}"
            rename_index += 1
        try:
            dst.hardlink_to(src)
        except OSError:
            shutil.copy2(src, dst)
        return dst

    # ------------------------------------------------------------------
    # 内部方法：草稿生成
    # ------------------------------------------------------------------

    def _generate_draft(
        self,
        *,
        draft_dir: Path,
        draft_name: str,
        clips: list[dict],
        width: int,
        height: int,
        content_mode: str,
    ) -> None:
        """使用 pyJianYingDraft 在 draft_dir 中生成草稿文件"""
        draft_dir.parent.mkdir(parents=True, exist_ok=True)
        folder = draft.DraftFolder(str(draft_dir.parent))
        script_file = folder.create_draft(draft_name, width=width, height=height, allow_replace=True)

        # 视频轨
        script_file.add_track(TrackType.video)

        # 字幕轨：仅注册了字幕文案源字段的模式（narration/ad）生成；drama 无字幕轨
        has_subtitle = content_mode in _SUBTITLE_TEXT_FIELDS
        text_style: TextStyle | None = None
        text_border: TextBorder | None = None
        text_shadow: TextShadow | None = None
        subtitle_position: ClipSettings | None = None
        is_portrait = height > width
        if has_subtitle:
            script_file.add_track(TrackType.text, "字幕")
            text_style = TextStyle(
                size=12.0 if is_portrait else 8.0,
                color=(1.0, 1.0, 1.0),
                align=1,
                bold=True,
                auto_wrapping=True,
                max_line_width=0.82 if is_portrait else 0.6,
            )
            text_border = TextBorder(
                color=(0.0, 0.0, 0.0),
                width=30.0,
            )
            text_shadow = TextShadow(
                color=(0.0, 0.0, 0.0),
                alpha=0.7,
                diffuse=8.0,
                distance=3.0,
                angle=-45.0,
            )
            subtitle_position = ClipSettings(
                transform_y=-0.75 if is_portrait else -0.8,
            )

        # 逐片段添加
        offset_us = 0
        last_index = len(clips) - 1
        narration_placements: list[tuple[int, str]] = []
        for index, clip in enumerate(clips):
            # 预读实际视频时长
            material = VideoMaterial(clip["local_path"])
            actual_duration_us = material.duration

            # 视频片段
            video_seg = VideoSegment(
                material,
                trange(offset_us, actual_duration_us),
            )

            # 转场：剪映约定挂在前一段上，因此最后一段不挂；cut 不挂。
            if index < last_index:
                transition_type = _TRANSITION_MAP.get(clip.get("transition_to_next", "cut"))
                if transition_type is not None:
                    video_seg.add_transition(transition_type)

            script_file.add_segment(video_seg)

            # 字幕片段
            if has_subtitle and clip.get("subtitle_text"):
                text_seg = TextSegment(
                    text=clip["subtitle_text"],
                    timerange=trange(offset_us, actual_duration_us),
                    style=text_style,
                    border=text_border,
                    shadow=text_shadow,
                    clip_settings=subtitle_position,
                )
                script_file.add_segment(text_seg)

            # 旁白音频：记录摆放位置（按视频片段 offset），统一在视频排布完成后添加
            narration_audio_local = clip.get("narration_audio_local")
            if narration_audio_local:
                narration_placements.append((offset_us, narration_audio_local))

            offset_us += actual_duration_us

        # 旁白素材：先解析全部音频文件，不可解析（截断/空文件等）的跳过不报错
        narration_materials: list[tuple[int, AudioMaterial]] = []
        for start_us, audio_path in narration_placements:
            try:
                narration_materials.append((start_us, AudioMaterial(audio_path)))
            except Exception as exc:
                # 解析失败不阻断导出：文件占用/损坏/底层库自定义异常均按跳过处理
                logger.warning("旁白音频无法解析，已跳过: %s (%s)", audio_path, exc)

        # 旁白音频段：时长取音频文件真实时长，不与视频对齐；
        # 仅当超长音频会与下一段旁白重叠时收口到其起点，保证草稿可导出（用户在剪映手动精调）
        narration_track_added = False
        for material_index, (start_us, audio_material) in enumerate(narration_materials):
            duration_us = audio_material.duration
            if material_index + 1 < len(narration_materials):
                window_us = narration_materials[material_index + 1][0] - start_us
                if duration_us > window_us:
                    logger.warning("旁白音频长过下一段起点，已收口: %s", audio_material.path)
                    duration_us = window_us
            if duration_us <= 0:
                logger.warning("旁白音频有效时长不足，已跳过: %s", audio_material.path)
                continue
            # 音轨仅在确有有效片段时创建，避免全部被过滤后留下空轨
            if not narration_track_added:
                script_file.add_track(TrackType.audio, "旁白")
                narration_track_added = True
            audio_seg = AudioSegment(audio_material, trange(start_us, duration_us))
            script_file.add_segment(audio_seg, "旁白")

        script_file.save()

    def _replace_paths_in_draft(self, *, json_path: Path, tmp_prefix: str, target_prefix: str) -> None:
        """JSON 安全地替换 draft_content.json 中的临时路径"""
        real = os.path.realpath(json_path)
        tmp = os.path.realpath(tempfile.gettempdir()) + os.sep
        if not real.startswith(tmp):
            raise ValueError(f"路径越界，拒绝写入: {real}")

        with open(real, encoding="utf-8") as f:  # noqa: PTH123
            data = json.load(f)

        def _walk(obj: Any) -> Any:
            if isinstance(obj, str) and tmp_prefix in obj:
                return obj.replace(tmp_prefix, target_prefix)
            if isinstance(obj, dict):
                return {k: _walk(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_walk(v) for v in obj]
            return obj

        data = _walk(data)
        with open(real, "w", encoding="utf-8") as f:  # noqa: PTH123
            json.dump(data, f, ensure_ascii=False)

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------

    def export_episode_draft(
        self,
        project_name: str,
        episode: int,
        draft_path: str,
        *,
        use_draft_info_name: bool = True,
    ) -> Path:
        """
        导出指定集的剪映草稿 ZIP。

        Returns:
            ZIP 文件路径（临时文件，调用方负责清理）

        Raises:
            FileNotFoundError: 项目或剧本不存在
            ValueError: 无可导出的视频片段
        """
        project = self.pm.load_project(project_name)
        project_dir = self.pm.get_project_path(project_name)

        # 1. 定位剧本
        script_data, _ = self._find_episode_script(project_name, project, episode)

        # 2. 收集已完成视频
        content_mode = _script_content_mode(script_data)
        clips = self._collect_video_clips(script_data, project_dir)
        if not clips:
            raise ValueError(f"第 {episode} 集没有已完成的视频片段，请先生成视频")

        # 3. 画布尺寸（项目未设 aspect_ratio 时从首个视频自动检测）
        width, height = self._resolve_canvas_size(project, clips[0]["abs_path"])

        # 4. 创建临时目录 + 复制素材到暂存区
        raw_title = project.get("title")
        if not isinstance(raw_title, str) or not raw_title.strip():
            raw_title = project_name
        safe_title = raw_title.replace("/", "_").replace("\\", "_").replace("..", "_")
        # ad 恒单集且界面不暴露「集」概念，草稿名直接用项目标题
        draft_name = safe_title if content_mode == "ad" else f"{safe_title}_第{episode}集"
        # 消毒后可能只剩 pathlib 会丢弃的空段（如标题为 "."）：塌缩的草稿目录会让
        # create_draft(allow_replace=True) 把 rmtree 落到上层临时目录，这里回退项目名兜底
        if not draft_name.replace(".", "").strip():
            draft_name = project_name
        tmp_dir = Path(tempfile.mkdtemp(prefix="arcreel_jy_"))
        try:
            staging_dir = tmp_dir / "staging"
            staging_dir.mkdir()

            # 同一来源文件（safe_resolve 已规范化路径）只暂存一次，多段引用共享同一暂存副本
            staged_by_src: dict[Path, Path] = {}
            project_root = project_dir.resolve()

            def stage_once(src: Path) -> str:
                if src not in staged_by_src:
                    # 暂存前重校验：收集与暂存之间文件可能被替换（如换成越界 symlink）
                    resolved = src.resolve()
                    if not (resolved.is_relative_to(project_root) and resolved.is_file()):
                        raise ValueError(f"路径越界，拒绝导出: {src}")
                    staged_by_src[src] = self._stage_file(resolved, staging_dir)
                return str(staged_by_src[src])

            local_clips = []
            for clip in clips:
                local_clip = {**clip, "local_path": stage_once(clip["abs_path"])}
                audio_src = clip.get("narration_audio_abs")
                if audio_src:
                    local_clip["narration_audio_local"] = stage_once(audio_src)
                local_clips.append(local_clip)

            # 5. 生成草稿（create_draft 会重建 draft_dir；草稿放独立父目录下，
            # 避免草稿名与暂存区等临时目录同级重名时被 allow_replace 误删）
            draft_dir = tmp_dir / "draft" / draft_name
            self._generate_draft(
                draft_dir=draft_dir,
                draft_name=draft_name,
                clips=local_clips,
                width=width,
                height=height,
                content_mode=content_mode,
            )

            # 6. 将素材移入草稿目录（暂存区内容即全部已暂存素材）
            assets_dir = draft_dir / "assets"
            assets_dir.mkdir(exist_ok=True)
            # normpath + startswith 做越界守卫：纯字符串规范化，不触发文件系统访问，
            # 且是静态分析可识别的收敛模式（resolve/is_relative_to 不被识别）
            assets_root = os.path.normpath(assets_dir)
            for staged in staging_dir.iterdir():
                dest = os.path.normpath(os.path.join(assets_root, staged.name))
                if not dest.startswith(assets_root + os.sep):
                    raise ValueError(f"路径越界，拒绝写入: {dest}")
                shutil.move(str(staged), dest)

            # 7. 路径后处理：staging 路径 → 用户本地路径
            draft_content_path = draft_dir / "draft_content.json"
            self._replace_paths_in_draft(
                json_path=draft_content_path,
                tmp_prefix=str(staging_dir),
                target_prefix=f"{draft_path}/{draft_name}/assets",
            )

            # 8. 剪映 6+ 使用 draft_info.json，低版本使用 draft_content.json
            if use_draft_info_name:
                draft_content_path.rename(draft_dir / "draft_info.json")

            # 9. 打包 ZIP
            zip_path = tmp_dir / f"{draft_name}.zip"
            video_suffixes = {".mp4", ".webm", ".mov", ".avi", ".mkv"}
            with zipfile.ZipFile(zip_path, "w") as zf:
                for file in draft_dir.rglob("*"):
                    if file.is_file():
                        arcname = f"{draft_name}/{file.relative_to(draft_dir)}"
                        compress = zipfile.ZIP_STORED if file.suffix.lower() in video_suffixes else zipfile.ZIP_DEFLATED
                        zf.write(file, arcname, compress_type=compress)

            return zip_path
        except Exception:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise
