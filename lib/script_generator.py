"""
script_generator.py - 剧本生成器

读取 Step 1/2 的 Markdown 中间文件，调用文本生成 Backend 生成最终 JSON 剧本
"""

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from lib.config.registry import PROVIDER_REGISTRY
from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.episode_ledger import normalize_source_text
from lib.project_manager import ProjectManager, effective_mode
from lib.prompt_builders_reference import build_reference_video_prompt
from lib.prompt_builders_script import (
    build_drama_prompt,
    build_narration_prompt,
)
from lib.script_models import (
    DramaEpisodeScript,
    NarrationEpisodeScript,
    ReferenceVideoScript,
    build_episode_script_model,
    build_reference_video_script_model,
)
from lib.text_backends.base import TextGenerationRequest, TextTaskType
from lib.text_generator import TextGenerator
from lib.text_metrics import count_reading_units

logger = logging.getLogger(__name__)

# 大型 JSON 剧本输出上限：22+ 场景典型约 14K token，留 2× 安全边际。
# 注意：受各模型硬上限约束（如 doubao-seed-1-8 ~8192），需选择支持 ≥16K 输出的模型。
SCRIPT_MAX_OUTPUT_TOKENS = 32000

# 集号前缀正则：仅匹配 `E{数字}` + 紧随 S/U（segment/scene 用 S，video_unit 用 U），
# 保留后缀（如 `E1S03_2` → `E2S03_2`）。设计契约见 lib/script_models.py。
_EID_PREFIX_RE = re.compile(r"^E\d+(?=[SU])")

# 质量探针阈值：仅捕极端短样本，正常完整描述应远超这些值。
_QUALITY_PROBE_SCENE_MIN_LEN = 40
_QUALITY_PROBE_ACTION_MIN_LEN = 25
_QUALITY_PROBE_SHOT_TEXT_MIN_LEN = 15
_NOVEL_TEXT_DRIFT_THRESHOLD = 0.10


def _rewrite_episode_prefix(rid: object, ep: int) -> object:
    """把 ID 中的 `E\\d+` 前缀强制改写为 `E{ep}`；非字符串或无 E 前缀的原样返回。

    兜底 LLM 在 prompt 已注入集号的情况下仍写错前缀的场景。
    """
    if not isinstance(rid, str):
        return rid
    new_rid, n = _EID_PREFIX_RE.subn(f"E{ep}", rid)
    if n and new_rid != rid:
        logger.warning("episode prefix rewritten: %s → %s", rid, new_rid)
    return new_rid


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

    def _effective_generation_mode(self, episode: int) -> str:
        """按 episode → project → 默认 storyboard 回退解析 generation_mode。"""
        return effective_mode(project=self.project_json, episode=self._episode_entry(episode))

    @staticmethod
    def _entry_outline(entry: dict) -> dict:
        """账本条目的 outline 字段归一化为 dict（缺失/形状异常返回空 dict）。"""
        raw_outline = entry.get("outline")
        return raw_outline if isinstance(raw_outline, dict) else {}

    def _ledger_outline_context(self, episode: int) -> tuple[dict | None, dict | None]:
        """从分集账本条目提取 drama 剧本生成的规划输入：(本集大纲, 下集大纲)。

        大纲 dict 含 title / hook / story_beats / next_episode_teaser。条目无任何
        规划数据（旧式条目，规划工具尚未写入）时对应项为 None，prompt 退回纯中间
        文件输入；末集无下集，第二项为 None。
        """

        def _context(entry: dict) -> dict | None:
            outline = self._entry_outline(entry)
            raw_beats = outline.get("story_beats")
            ctx = {
                "title": entry.get("title"),
                "hook": entry.get("hook"),
                # 非 list 形状（手编损坏）按缺失处理，避免字符串被逐字符渲染进 prompt
                "story_beats": raw_beats if isinstance(raw_beats, list) else [],
                "next_episode_teaser": outline.get("next_episode_teaser"),
            }
            if not ctx["hook"] and not ctx["story_beats"] and not ctx["next_episode_teaser"]:
                return None
            return ctx

        return _context(self._episode_entry(episode)), _context(self._episode_entry(episode + 1))

    @classmethod
    async def create(cls, project_path: str | Path) -> "ScriptGenerator":
        """异步工厂方法，自动从 DB 加载供应商配置创建 TextGenerator。"""
        project_name = Path(project_path).name
        generator = await TextGenerator.create(TextTaskType.SCRIPT, project_name)
        return cls(project_path, generator)

    async def generate(
        self,
        episode: int,
        output_filename: str | None = None,
    ) -> Path:
        """
        异步生成剧集剧本

        Args:
            episode: 剧集编号
            output_filename: 输出文件名，默认 episode_{episode}.json。剧本一律经写盘统一入口写入
                项目 scripts/ 目录，故此参数只决定文件名、不接受目录。

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

        gen_mode = self._effective_generation_mode(episode)
        caps = await self._fetch_video_capabilities()

        step1_md = self._load_step1(episode)

        characters = self.project_json.get("characters", {})
        scenes = self.project_json.get("scenes", {})
        props = self.project_json.get("props", {})

        # 三分支同口径解析一次：作为 prompt 的时长约束文本，并据此构造 duration 枚举硬约束的 schema。
        supported_durations = self._resolve_supported_durations(caps)

        if gen_mode == "reference_video":
            prompt = build_reference_video_prompt(
                project_overview=self.project_json.get("overview", {}),
                style=self.project_json.get("style", ""),
                style_description=self.project_json.get("style_description", ""),
                characters=characters,
                scenes=scenes,
                props=props,
                units_md=step1_md,
                supported_durations=supported_durations,
                max_refs=self._resolve_max_refs(caps),
                max_duration=self._resolve_max_duration(caps),
                aspect_ratio=self._resolve_aspect_ratio(),
                episode=episode,
            )
            # unit 总时长（duration_seconds = 各 shot 之和）枚举约束到 supported_durations：
            # 发给 API 的就是这个总和，源头杜绝非成员值漏到供应商报错。
            schema = build_reference_video_script_model(supported_durations)
        elif self.content_mode == "narration":
            prompt = build_narration_prompt(
                project_overview=self.project_json.get("overview", {}),
                style=self.project_json.get("style", ""),
                style_description=self.project_json.get("style_description", ""),
                characters=characters,
                scenes=scenes,
                props=props,
                segments_md=step1_md,
                supported_durations=supported_durations,
                default_duration=self.project_json.get("default_duration"),
                aspect_ratio=self._resolve_aspect_ratio(),
                episode=episode,
            )
            # duration_seconds 收紧为 supported_durations 的 enum：LLM 结构化输出层即被卡死，
            # 避免生成出执行层 assert_duration_supported 会拒、或漏到供应商 API 报错的非成员时长。
            schema = build_episode_script_model("narration", supported_durations)
        else:
            episode_outline, next_episode_outline = self._ledger_outline_context(episode)
            prompt = build_drama_prompt(
                project_overview=self.project_json.get("overview", {}),
                style=self.project_json.get("style", ""),
                style_description=self.project_json.get("style_description", ""),
                characters=characters,
                scenes=scenes,
                props=props,
                scenes_md=step1_md,
                supported_durations=supported_durations,
                default_duration=self.project_json.get("default_duration"),
                aspect_ratio=self._resolve_aspect_ratio(),
                episode=episode,
                episode_outline=episode_outline,
                next_episode_outline=next_episode_outline,
            )
            schema = build_episode_script_model("drama", supported_durations)

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

        # 7. 经写盘统一入口保存：整集生成无「改前」，按严格结构校验（等价原 response_schema 的
        #    Pydantic 校验），并继承 metadata 重算、加锁、filename↔episode 一致性与 project.json
        #    同步——消除「裸 json.dump 旁路」，使 _write_script_unlocked 成为剧本唯一写入点。
        filename = output_filename or f"episode_{episode}.json"
        pm = ProjectManager(str(self.project_path.parent))
        output_path = pm.save_script(self.project_path.name, script_data, filename, validate=True)

        self._quality_probe(script_data, episode)

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
                episode=episode,
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
                episode=episode,
            )
        else:
            episode_outline, next_episode_outline = self._ledger_outline_context(episode)
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
                episode=episode,
                episode_outline=episode_outline,
                next_episode_outline=next_episode_outline,
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

    def _resolve_max_refs(self, caps: dict | None = None) -> int | None:
        """解析当前视频模型的最大参考图数；caps → project.json.video_backend → registry 两级回退。

        语义约定：仅 None 视为「未声明上限」（上层不在 prompt 写硬性数量约束，且 executor 跳过裁剪）；
        caps 来源的 0 是显式上限（如不接受参考图的 endpoint），会原样下传触发裁剪为 0 张。
        caps 解析失败（DB/migration 故障等）时退到 registry 的 ModelInfo.max_reference_images——
        与 _resolve_supported_durations 同构，避免丢失上限导致后端按多张参考图发出而被上游拒。
        registry 里 0 是字段默认值（图像/文本模型或视频模型未声明），用 truthy 守卫当作未声明跳过。
        """
        if caps:
            cached = caps.get("max_reference_images")
            if cached is not None:
                return int(cached)
        video_backend = self.project_json.get("video_backend")
        if video_backend and isinstance(video_backend, str) and "/" in video_backend:
            provider_id, model_id = video_backend.split("/", 1)
            provider_meta = PROVIDER_REGISTRY.get(provider_id)
            if provider_meta:
                model_info = provider_meta.models.get(model_id)
                if model_info and model_info.max_reference_images:
                    return int(model_info.max_reference_images)
        return None

    def _load_project_json(self) -> dict:
        """加载 project.json"""
        path = self.project_path / "project.json"
        if not path.exists():
            raise FileNotFoundError(f"未找到 project.json: {path}")

        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def _load_step1(self, episode: int) -> str:
        """加载 Step 1 的 Markdown 中间文件。

        每种模式只对应一个期望文件，缺失时显式报错并指明期望路径——不降级改读
        其他模式的中间文件（静默 fallback 会让剧本基于错误模式的中间产物生成）。
        """
        drafts_path = self.project_path / "drafts" / f"episode_{episode}"
        gen_mode = self._effective_generation_mode(episode)
        if gen_mode == "reference_video":
            step1_path = drafts_path / "step1_reference_units.md"
        elif self.content_mode == "narration":
            step1_path = drafts_path / "step1_segments.md"
        else:
            step1_path = drafts_path / "step1_normalized_script.md"

        if not step1_path.exists():
            raise FileNotFoundError(
                f"未找到 Step 1 中间文件: {step1_path}；"
                f"content_mode={self.content_mode}, generation_mode={gen_mode} 期望该文件，"
                "请先完成本集预处理"
            )

        return step1_path.read_text(encoding="utf-8")

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

        # 兜底改写 segment/scene/unit ID 中的 E\d+ 前缀，避免 LLM 写错集号导致文件
        # 名跨集冲突（如 storyboards/scene_E1S01.png 被 E2 重新覆盖）。
        ep = int(episode)
        if gen_mode == "reference_video":
            for u in script_data.get("video_units") or []:
                if isinstance(u, dict) and "unit_id" in u:
                    u["unit_id"] = _rewrite_episode_prefix(u.get("unit_id"), ep)
        elif self.content_mode == "narration":
            for s in script_data.get("segments") or []:
                if isinstance(s, dict) and "segment_id" in s:
                    s["segment_id"] = _rewrite_episode_prefix(s.get("segment_id"), ep)
        else:
            for s in script_data.get("scenes") or []:
                if isinstance(s, dict) and "scene_id" in s:
                    s["scene_id"] = _rewrite_episode_prefix(s.get("scene_id"), ep)
        # content_mode 严格只是"内容类型"（narration/drama）；reference_video 属于
        # "视频来源"维度，由 generation_mode 表达。
        # 参考视频集必须强制覆盖：ReferenceVideoScript.content_mode 有 Pydantic 默认值
        # "narration"，setdefault 拿不到项目级真值；非参考集 LLM 已在 schema 中产出
        # narration/drama，setdefault 仅作 fallback。
        if gen_mode == "reference_video":
            script_data["content_mode"] = self.content_mode
            script_data["generation_mode"] = "reference_video"
        else:
            script_data.setdefault("content_mode", self.content_mode)

        # 集级钩子/下集预告：分集账本是钩子设计的单一真相源，强制以账本值覆盖
        # （LLM 不参与填写，model_dump 只会留下 None 默认值）。账本无规划数据时为 None。
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

        # 添加时间戳
        now = datetime.now(UTC).isoformat()
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

            gen_mode = self._effective_generation_mode(episode)
            if gen_mode == "reference_video":
                for u in script_data.get("video_units") or []:
                    if not isinstance(u, dict):
                        continue
                    uid = str(u.get("unit_id") or "?")
                    for shot in u.get("shots") or []:
                        if not isinstance(shot, dict):
                            continue
                        text = str(shot.get("text") or "")
                        if len(text) < _QUALITY_PROBE_SHOT_TEXT_MIN_LEN:
                            short_ids.append(uid)
            else:
                if self.content_mode == "narration":
                    items = script_data.get("segments") or []
                    id_key = "segment_id"
                else:
                    items = script_data.get("scenes") or []
                    id_key = "scene_id"
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

            # narration 模式 novel_text 漂移观察:LLM 应原文回填,但实测有偷偷扩写。
            # 仅 WARN,不阻断/不重试/不推前端,符合「LLM 出错少数情况轻量不阻塞」原则。
            if self.content_mode == "narration" and gen_mode != "reference_video":
                source_path = self.project_path / "source" / f"episode_{episode}.txt"
                if source_path.is_file():
                    source_lang = self.project_json.get("source_language", "zh")
                    # 归一化边界:LLM 输出几乎必然 NFC,源若为 NFD(越南语 macOS
                    # 文件名等罕见场景)会让 \w word boundary 把组合重音拆词,导致 expected
                    # 偏大触发 false drift。两侧统一过账本归一化函数(NFC + 换行折叠,
                    # 后者对计数无影响)后比较 fair。
                    source_text = normalize_source_text(source_path.read_text(encoding="utf-8"))
                    expected = count_reading_units(source_text, source_lang)
                    actual = sum(
                        count_reading_units(
                            normalize_source_text(str(seg.get("novel_text") or "")),
                            source_lang,
                        )
                        for seg in script_data.get("segments") or []
                        if isinstance(seg, dict)
                    )
                    if expected > 0:
                        delta_ratio = abs(actual - expected) / expected
                        if delta_ratio > _NOVEL_TEXT_DRIFT_THRESHOLD:
                            logger.warning(
                                "episode %d novel_text drift: expected=%d actual=%d delta=%.1f%%",
                                episode,
                                expected,
                                actual,
                                delta_ratio * 100,
                            )
        except Exception as exc:
            logger.warning("episode %d quality probe skipped due to unexpected data shape: %s", episode, exc)
