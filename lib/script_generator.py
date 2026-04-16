"""
script_generator.py - 剧本生成器

读取 Step 1/2 的 Markdown 中间文件，调用文本生成 Backend 生成最终 JSON 剧本
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from lib.config.registry import PROVIDER_REGISTRY
from lib.prompt_builders_script import (
    build_drama_prompt,
    build_narration_prompt,
)
from lib.script_models import (
    DramaEpisodeScript,
    NarrationEpisodeScript,
)
from lib.text_backends.base import TextGenerationRequest, TextTaskType
from lib.text_generator import TextGenerator

logger = logging.getLogger(__name__)

# 大型 JSON 剧本输出上限：22+ 场景典型约 14K token，留 2× 安全边际。
# 注意：受各模型硬上限约束（如 doubao-seed-1-8 ~8192），需选择支持 ≥16K 输出的模型。
SCRIPT_MAX_OUTPUT_TOKENS = 32000


class ScriptGenerator:
    """
    剧本生成器

    读取 Step 1/2 的 Markdown 中间文件，调用 TextBackend 生成最终 JSON 剧本
    """

    def __init__(self, project_path: str | Path, generator: Optional["TextGenerator"] = None):
        """
        初始化生成器

        Args:
            project_path: 项目目录路径，如 projects/test0205
            generator: TextGenerator 实例（可选）。若为 None 则仅支持 build_prompt() dry-run。
        """
        self.project_path = Path(project_path)
        self.generator = generator

        # 加载 project.json
        self.project_json = self._load_project_json()
        self.content_mode = self.project_json.get("content_mode", "narration")

    @classmethod
    async def create(cls, project_path: str | Path) -> "ScriptGenerator":
        """异步工厂方法，自动从 DB 加载供应商配置创建 TextGenerator。"""
        project_name = Path(project_path).name
        generator = await TextGenerator.create(TextTaskType.SCRIPT, project_name)
        return cls(project_path, generator)

    async def generate(
        self,
        episode: int,
        output_path: Path | None = None,
    ) -> Path:
        """
        异步生成剧集剧本

        Args:
            episode: 剧集编号
            output_path: 输出路径，默认为 scripts/episode_{episode}.json

        Returns:
            生成的 JSON 文件路径
        """
        if self.generator is None:
            raise RuntimeError("TextGenerator 未初始化，请使用 ScriptGenerator.create() 工厂方法")

        # 1. 加载中间文件
        step1_md = self._load_step1(episode)

        # 2. 提取角色、场景、道具（从 project.json）
        characters = self.project_json.get("characters", {})
        scenes = self.project_json.get("scenes", {})
        props = self.project_json.get("props", {})

        # 3. 构建 Prompt
        if self.content_mode == "narration":
            prompt = build_narration_prompt(
                project_overview=self.project_json.get("overview", {}),
                style=self.project_json.get("style", ""),
                style_description=self.project_json.get("style_description", ""),
                characters=characters,
                scenes=scenes,
                props=props,
                segments_md=step1_md,
                supported_durations=self._resolve_supported_durations(),
                default_duration=self.project_json.get("default_duration"),
                aspect_ratio=self._resolve_aspect_ratio(),
            )
            schema = NarrationEpisodeScript
        else:
            prompt = build_drama_prompt(
                project_overview=self.project_json.get("overview", {}),
                style=self.project_json.get("style", ""),
                style_description=self.project_json.get("style_description", ""),
                characters=characters,
                scenes=scenes,
                props=props,
                scenes_md=step1_md,
                supported_durations=self._resolve_supported_durations(),
                default_duration=self.project_json.get("default_duration"),
                aspect_ratio=self._resolve_aspect_ratio(),
            )
            schema = DramaEpisodeScript

        # 4. 调用 TextBackend
        logger.info("正在生成第 %d 集剧本...", episode)
        project_name = self.project_path.name
        result = await self.generator.generate(
            TextGenerationRequest(
                prompt=prompt,
                response_schema=schema,
                max_output_tokens=SCRIPT_MAX_OUTPUT_TOKENS,
            ),
            project_name=project_name,
        )
        response_text = result.text

        # 5. 解析并验证响应
        script_data = self._parse_response(response_text, episode)

        # 6. 补充元数据
        script_data = self._add_metadata(script_data, episode)

        # 7. 保存文件
        if output_path is None:
            output_path = self.project_path / "scripts" / f"episode_{episode}.json"

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(script_data, f, ensure_ascii=False, indent=2)

        logger.info("剧本已保存至 %s", output_path)
        return output_path

    def build_prompt(self, episode: int) -> str:
        """
        构建 Prompt（用于 dry-run 模式）

        Args:
            episode: 剧集编号

        Returns:
            构建好的 Prompt 字符串
        """
        step1_md = self._load_step1(episode)
        characters = self.project_json.get("characters", {})
        scenes = self.project_json.get("scenes", {})
        props = self.project_json.get("props", {})

        if self.content_mode == "narration":
            return build_narration_prompt(
                project_overview=self.project_json.get("overview", {}),
                style=self.project_json.get("style", ""),
                style_description=self.project_json.get("style_description", ""),
                characters=characters,
                scenes=scenes,
                props=props,
                segments_md=step1_md,
                supported_durations=self._resolve_supported_durations(),
                default_duration=self.project_json.get("default_duration"),
                aspect_ratio=self._resolve_aspect_ratio(),
            )
        else:
            return build_drama_prompt(
                project_overview=self.project_json.get("overview", {}),
                style=self.project_json.get("style", ""),
                style_description=self.project_json.get("style_description", ""),
                characters=characters,
                scenes=scenes,
                props=props,
                scenes_md=step1_md,
                supported_durations=self._resolve_supported_durations(),
                default_duration=self.project_json.get("default_duration"),
                aspect_ratio=self._resolve_aspect_ratio(),
            )

    def _resolve_supported_durations(self) -> list[int] | None:
        """从项目配置或 registry 解析当前视频模型支持的时长列表。"""
        durations = self.project_json.get("_supported_durations")
        if durations and isinstance(durations, list):
            return durations
        video_backend = self.project_json.get("video_backend")
        if video_backend and isinstance(video_backend, str) and "/" in video_backend:
            provider_id, model_id = video_backend.split("/", 1)
            provider_meta = PROVIDER_REGISTRY.get(provider_id)
            if provider_meta:
                model_info = provider_meta.models.get(model_id)
                if model_info and model_info.supported_durations:
                    return list(model_info.supported_durations)
        return None

    def _resolve_aspect_ratio(self) -> str:
        """解析项目的 aspect_ratio，向后兼容。"""
        if "aspect_ratio" in self.project_json and isinstance(self.project_json["aspect_ratio"], str):
            return self.project_json["aspect_ratio"]
        return "9:16" if self.content_mode == "narration" else "16:9"

    def _load_project_json(self) -> dict:
        """加载 project.json"""
        path = self.project_path / "project.json"
        if not path.exists():
            raise FileNotFoundError(f"未找到 project.json: {path}")

        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def _load_step1(self, episode: int) -> str:
        """加载 Step 1 的 Markdown 文件，支持两种文件命名"""
        drafts_path = self.project_path / "drafts" / f"episode_{episode}"
        if self.content_mode == "narration":
            primary_path = drafts_path / "step1_segments.md"
            fallback_path = drafts_path / "step1_normalized_script.md"
        else:
            primary_path = drafts_path / "step1_normalized_script.md"
            fallback_path = drafts_path / "step1_segments.md"

        if not primary_path.exists():
            if fallback_path.exists():
                logger.warning("未找到 Step 1 文件: %s，改用 %s", primary_path, fallback_path)
                primary_path = fallback_path
            else:
                raise FileNotFoundError(f"未找到 Step 1 文件: {primary_path}")

        with open(primary_path, encoding="utf-8") as f:
            return f.read()

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
        text = response_text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        # 解析 JSON
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON 解析失败: {e}")

        # Pydantic 验证
        try:
            if self.content_mode == "narration":
                validated = NarrationEpisodeScript.model_validate(data)
            else:
                validated = DramaEpisodeScript.model_validate(data)
            return validated.model_dump()
        except ValidationError as e:
            logger.warning("数据验证警告: %s", e)
            # 返回原始数据，允许部分不符合 schema
            return data

    def _add_metadata(self, script_data: dict, episode: int) -> dict:
        """
        补充剧本元数据

        Args:
            script_data: 剧本数据
            episode: 剧集编号

        Returns:
            补充元数据后的剧本数据
        """
        # 确保基本字段存在
        script_data.setdefault("episode", episode)
        script_data.setdefault("content_mode", self.content_mode)

        # 添加小说信息
        if "novel" not in script_data:
            script_data["novel"] = {
                "title": self.project_json.get("title", ""),
                "chapter": f"第{episode}集",
            }
        # 剥离已废弃的 source_file（AI 可能虚构）
        novel = script_data.get("novel")
        if isinstance(novel, dict):
            novel.pop("source_file", None)

        # 添加时间戳
        now = datetime.now().isoformat()
        script_data.setdefault("metadata", {})
        script_data["metadata"]["created_at"] = now
        script_data["metadata"]["updated_at"] = now
        script_data["metadata"]["generator"] = self.generator.model if self.generator else "unknown"

        # 计算统计信息（episode 级角色/场景/道具聚合由 StatusCalculator 读时计算）
        if self.content_mode == "narration":
            segments = script_data.get("segments", [])
            script_data["metadata"]["total_segments"] = len(segments)
            script_data["duration_seconds"] = sum(int(s.get("duration_seconds", 4)) for s in segments)
        else:
            scenes = script_data.get("scenes", [])
            script_data["metadata"]["total_scenes"] = len(scenes)
            script_data["duration_seconds"] = sum(int(s.get("duration_seconds", 8)) for s in scenes)

        # 剥离废弃的 episode 级聚合字段（改为读时计算）
        script_data.pop("characters_in_episode", None)
        script_data.pop("clues_in_episode", None)

        return script_data
