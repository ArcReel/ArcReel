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
from sqlalchemy.exc import SQLAlchemyError

from lib.config.registry import PROVIDER_REGISTRY
from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.project_manager import ProjectManager, effective_mode
from lib.prompt_builders_reference import build_reference_video_prompt
from lib.prompt_builders_script import (
    build_drama_prompt,
    build_narration_prompt,
)
from lib.reference_video.limits import (
    DEFAULT_MAX_REFS,
    PROVIDER_MAX_REFS,
    normalize_provider_id,
)
from lib.script_models import (
    DramaEpisodeScript,
    NarrationEpisodeScript,
    ReferenceVideoScript,
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

    def _effective_generation_mode(self, episode: int) -> str:
        """按 Spec §4.6 解析集级 → 项目级 generation_mode，未知值回退 storyboard。"""
        episode_dict = next(
            (ep for ep in (self.project_json.get("episodes") or []) if ep.get("episode") == episode),
            {},
        )
        return effective_mode(project=self.project_json, episode=episode_dict)

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

        gen_mode = self._effective_generation_mode(episode)
        caps = await self._fetch_video_capabilities()

        step1_md = self._load_step1(episode)

        characters = self.project_json.get("characters", {})
        scenes = self.project_json.get("scenes", {})
        props = self.project_json.get("props", {})

        if gen_mode == "reference_video":
            prompt = build_reference_video_prompt(
                project_overview=self.project_json.get("overview", {}),
                style=self.project_json.get("style", ""),
                style_description=self.project_json.get("style_description", ""),
                characters=characters,
                scenes=scenes,
                props=props,
                units_md=step1_md,
                supported_durations=self._resolve_supported_durations(caps),
                max_refs=self._resolve_max_refs(caps),
                max_duration=self._resolve_max_duration(caps),
                aspect_ratio=self._resolve_aspect_ratio(),
            )
            schema = ReferenceVideoScript
        elif self.content_mode == "narration":
            prompt = build_narration_prompt(
                project_overview=self.project_json.get("overview", {}),
                style=self.project_json.get("style", ""),
                style_description=self.project_json.get("style_description", ""),
                characters=characters,
                scenes=scenes,
                props=props,
                segments_md=step1_md,
                supported_durations=self._resolve_supported_durations(caps),
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
                supported_durations=self._resolve_supported_durations(caps),
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

    async def build_prompt(self, episode: int) -> str:
        """
        构建 Prompt（用于 dry-run 模式）

        与 `generate()` 同样先 await `_fetch_video_capabilities()` 解析 caps；
        这样当 `project.json` 不显式声明 `video_backend`（用户依赖全局/系统默认时）也能
        正确派生 supported_durations。caps 失败仍 fallback 到 project.json 自身的 sync 链。
        """
        gen_mode = self._effective_generation_mode(episode)
        caps = await self._fetch_video_capabilities()
        step1_md = self._load_step1(episode)
        characters = self.project_json.get("characters", {})
        scenes = self.project_json.get("scenes", {})
        props = self.project_json.get("props", {})

        if gen_mode == "reference_video":
            return build_reference_video_prompt(
                project_overview=self.project_json.get("overview", {}),
                style=self.project_json.get("style", ""),
                style_description=self.project_json.get("style_description", ""),
                characters=characters,
                scenes=scenes,
                props=props,
                units_md=step1_md,
                supported_durations=self._resolve_supported_durations(caps),
                max_refs=self._resolve_max_refs(caps),
                max_duration=self._resolve_max_duration(caps),
                aspect_ratio=self._resolve_aspect_ratio(),
            )
        elif self.content_mode == "narration":
            return build_narration_prompt(
                project_overview=self.project_json.get("overview", {}),
                style=self.project_json.get("style", ""),
                style_description=self.project_json.get("style_description", ""),
                characters=characters,
                scenes=scenes,
                props=props,
                segments_md=step1_md,
                supported_durations=self._resolve_supported_durations(caps),
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
                supported_durations=self._resolve_supported_durations(caps),
                default_duration=self.project_json.get("default_duration"),
                aspect_ratio=self._resolve_aspect_ratio(),
            )

    async def _fetch_video_capabilities(self) -> dict | None:
        """从 ConfigResolver 解析视频模型能力；失败时返 None，由 _resolve_* fallback 到 project.json 直读。

        使用 `video_capabilities_for_project` 传入已加载的 project.json，不再按 `self.project_path.name`
        重新全局加载——避免 ScriptGenerator 在非标准路径（如测试 tmp_path）实例化时目录名与
        全局项目碰撞读到错误能力。

        宽松捕获：除 ValueError 外，DB 未 migration / 连接失败等 SQLAlchemy 异常也走 fallback，
        保证在缺能力元数据的环境（如裸 CI 测试容器）中 generate() 仍能跑通。
        """
        resolver = ConfigResolver(async_session_factory)
        try:
            return await resolver.video_capabilities_for_project(self.project_json)
        except (ValueError, SQLAlchemyError) as exc:
            logger.info("video_capabilities 解析失败，将走 project.json fallback：%s", exc)
            return None

    def _resolve_supported_durations(self, caps: dict | None = None) -> list[int]:
        """从 caps → project.json → registry 三级解析；都拿不到抛 ValueError。"""
        if caps and caps.get("supported_durations"):
            return list(caps["supported_durations"])
        durations = self.project_json.get("_supported_durations")
        if durations and isinstance(durations, list):
            return list(durations)
        video_backend = self.project_json.get("video_backend")
        if video_backend and isinstance(video_backend, str) and "/" in video_backend:
            provider_id, model_id = video_backend.split("/", 1)
            provider_meta = PROVIDER_REGISTRY.get(provider_id)
            if provider_meta:
                model_info = provider_meta.models.get(model_id)
                if model_info and model_info.supported_durations:
                    return list(model_info.supported_durations)
        raise ValueError(
            f"supported_durations 无法解析：caps={bool(caps)}, video_backend={video_backend!r}；请确保 model 配置完整"
        )

    def _resolve_max_duration(self, caps: dict | None = None) -> int | None:
        """单次视频生成最长秒数；派生自 max(supported_durations)。"""
        if caps and caps.get("max_duration") is not None:
            return int(caps["max_duration"])
        try:
            durations = self._resolve_supported_durations(caps)
        except ValueError:
            return None
        return max(durations)

    def _resolve_aspect_ratio(self) -> str:
        """解析项目的 aspect_ratio，向后兼容。"""
        if "aspect_ratio" in self.project_json and isinstance(self.project_json["aspect_ratio"], str):
            return self.project_json["aspect_ratio"]
        return "9:16" if self.content_mode == "narration" else "16:9"

    def _resolve_max_refs(self, caps: dict | None = None) -> int:
        """按 provider 粗粒度解析最大参考图数。数值来源：`lib.reference_video.limits`。"""
        if caps:
            cached = caps.get("max_reference_images")
            if cached is not None:
                return int(cached)
        video_backend = self.project_json.get("video_backend") or ""
        raw_provider = video_backend.split("/", 1)[0] if "/" in video_backend else ""
        provider_id = normalize_provider_id(raw_provider)
        return PROVIDER_MAX_REFS.get(provider_id, DEFAULT_MAX_REFS)

    def _load_project_json(self) -> dict:
        """加载 project.json。

        直接读取（非 ProjectManager 路径），但应用 ``_migrate_legacy_content_mode``
        把旧的 ``content_mode == "reference_video"`` 拆成两条独立维度，避免下游
        ``self.content_mode`` 取到无效值（issue #542）。
        """
        path = self.project_path / "project.json"
        if not path.exists():
            raise FileNotFoundError(f"未找到 project.json: {path}")

        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        ProjectManager._migrate_legacy_content_mode(data)
        return data

    def _load_step1(self, episode: int) -> str:
        """加载 Step 1 的 Markdown 文件，支持两种文件命名"""
        drafts_path = self.project_path / "drafts" / f"episode_{episode}"
        gen_mode = self._effective_generation_mode(episode)
        if gen_mode == "reference_video":
            primary_path = drafts_path / "step1_reference_units.md"
            fallback_path = None
        elif self.content_mode == "narration":
            primary_path = drafts_path / "step1_segments.md"
            fallback_path = drafts_path / "step1_normalized_script.md"
        else:
            primary_path = drafts_path / "step1_normalized_script.md"
            fallback_path = drafts_path / "step1_segments.md"

        if not primary_path.exists():
            if fallback_path is not None and fallback_path.exists():
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
            if self._effective_generation_mode(episode) == "reference_video":
                validated = ReferenceVideoScript.model_validate(data)
            elif self.content_mode == "narration":
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
        gen_mode = self._effective_generation_mode(episode)
        # CLI 参数 --episode 是集号唯一真相源。schema 已从 AI 输出中移除 episode 字段，
        # 这里负责落盘前补上。
        script_data["episode"] = int(episode)
        # content_mode 严格只是"内容类型"（narration/drama）；reference_video 属于
        # "视频来源"维度，由 generation_mode 表达（issue #542）。
        # 参考视频集必须强制覆盖：ReferenceVideoScript.content_mode 有 Pydantic 默认值
        # "narration"，setdefault 拿不到项目级真值；非参考集 LLM 已在 schema 中产出
        # narration/drama，setdefault 仅作 fallback。
        if gen_mode == "reference_video":
            script_data["content_mode"] = self.content_mode
            script_data["generation_mode"] = "reference_video"
        else:
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
        if gen_mode == "reference_video":
            units = script_data.get("video_units", [])
            script_data["metadata"]["total_units"] = len(units)
            script_data["duration_seconds"] = sum(int(u.get("duration_seconds", 0)) for u in units)
        elif self.content_mode == "narration":
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
