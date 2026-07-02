"""
script_generator.py - 剧本生成器

读取 Step 1/2 的 Markdown 中间文件，调用文本生成 Backend 生成最终 JSON 剧本
"""

import json
import logging
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from lib.config.registry import PROVIDER_REGISTRY
from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.episode_paths import (
    REFERENCE_VIDEO_STEP1_FILENAME,
    STEP1_FILENAMES,
    STEP1_LEGACY_FILENAMES,
    episode_drafts_dir,
    episode_script_filename,
)
from lib.project_manager import ProjectManager, effective_mode
from lib.prompt_builders_ad import build_ad_prompt
from lib.prompt_builders_reference import build_reference_video_prompt
from lib.prompt_builders_script import (
    build_drama_prompt,
    build_narration_prompt,
    render_drama_content_for_step2,
)
from lib.script_models import (
    AD_TARGET_DURATION_DRIFT_THRESHOLD,
    AdEpisodeScript,
    DramaEpisodeScript,
    DramaVisualScript,
    NarrationEpisodeScript,
    NarrationStep1Draft,
    NarrationVisualEpisodeScript,
    ReferenceVideoScript,
    ad_script_total_duration,
    build_ad_reference_episode_script_model,
    build_episode_script_model,
    build_reference_video_script_model,
    merge_drama_visual_into_scenes,
    script_shape,
)
from lib.text_backends.base import TextGenerationRequest, TextTaskType
from lib.text_generator import TextGenerator
from lib.text_utils import strip_json_code_fences

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

        # ad 剧本骨架唯一（平铺 shots[]），先于 generation_mode 分派：即使
        # reference_video 路径也消费 ad prompt + AdEpisodeScript，不换 video_units 骨架。
        # ad 一键生成不走 step1 中间文件，创作输入是 brief + 产品信息 + target_duration。
        if self.content_mode == "ad":
            prompt, schema = await self._compose_ad(episode, gen_mode)
            return await self._generate_and_save(prompt, schema, episode, output_filename)

        # drama（storyboard / grid）走两段式（见 ADR 0041）：step1 内容已是结构化 JSON，
        # step2 仅出视觉层（image_prompt / video_prompt），后端按 scene_id 合并回 step1 内容、
        # 透传 utterances / source_text 等非视觉字段。reference_video 路径不入此分支（用 video_units）；
        # content_mode 未知（脏值）按 drama 形状处理，与 script_shape 兜底同口径。
        if gen_mode != "reference_video" and self.content_mode != "narration":
            return await self._generate_drama_step2(episode, output_filename)

        caps = await self._fetch_video_capabilities()

        characters = self.project_json.get("characters", {})
        scenes = self.project_json.get("scenes", {})
        props = self.project_json.get("props", {})

        # 解析一次时长能力：reference 据此构造 duration 枚举硬约束 schema；
        # narration 两段式用于校验 step1 各片段时长成员合法（step2 不再产出时长）。
        supported_durations = self._resolve_supported_durations(caps)

        # narration 走两段式：step1 结构化片段透传内容层（novel_text 等），step2 仅产视觉层、
        # 按 segment_id 合并回 step1。非 narration 走单段（step1 markdown 直喂 LLM）。
        narration_step1: list[dict] | None = None

        if gen_mode == "reference_video":
            step1_md = self._load_step1(episode)
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
            schema: type = build_reference_video_script_model(supported_durations)
        else:
            # narration 两段式：step1 透传内容层（novel_text 等），step2 仅产视觉层、按 segment_id 合并回 step1。
            # drama 已在前面经 _generate_drama_step2 早返回；reference 走上面分支，故此 else 必为 narration。
            narration_step1 = self._load_narration_step1(episode, supported_durations)
            prompt = build_narration_prompt(
                project_overview=self.project_json.get("overview", {}),
                style=self.project_json.get("style", ""),
                style_description=self.project_json.get("style_description", ""),
                characters=characters,
                scenes=scenes,
                props=props,
                step1_segments=narration_step1,
                aspect_ratio=self._resolve_aspect_ratio(),
                episode=episode,
                # 输出语言与 step1 同取项目 source_language，避免非中文项目 step1 透传内容与 step2 视觉割裂（同 drama）
                target_language=self.project_json.get("source_language") or "中文",
            )
            # step2 只产视觉层（image_prompt/video_prompt），按 segment_id 对齐 step1 合并；
            # novel_text/时长/break 由 step1 透传，不进 LLM 输出，从工程上根除扩写漂移。
            schema = NarrationVisualEpisodeScript

        return await self._generate_and_save(prompt, schema, episode, output_filename, narration_step1=narration_step1)

    async def _generate_drama_step2(self, episode: int, output_filename: str | None) -> Path:
        """drama 两段式 step2：读 step1 结构化内容 → LLM 仅出视觉层 → 按 scene_id 合并 → 落盘。

        非视觉字段（utterances / source_text / characters_in_scene / 时长 / 边界）一律取自 step1 内容、
        不进 LLM 输出（工程透传，杜绝 Structured Outputs 漂移）；视觉层缺覆盖 / 悬空 scene_id 由
        ``merge_drama_visual_into_scenes`` fail-loud。
        """
        assert self.generator is not None  # generate() 入口已检查
        content = self._load_drama_step1_content(episode)
        raw_scenes = content.get("scenes")
        content_scenes: list = raw_scenes if isinstance(raw_scenes, list) else []

        logger.info("正在生成第 %d 集剧本（drama step2 视觉层）...", episode)
        result = await self.generator.generate(
            TextGenerationRequest(
                prompt=self._build_drama_step2_prompt(content_scenes, episode),
                response_schema=DramaVisualScript,
                max_output_tokens=SCRIPT_MAX_OUTPUT_TOKENS,
            ),
            project_name=self.project_path.name,
        )

        visual_scenes = self._parse_drama_visual(result.text)
        merged_scenes = merge_drama_visual_into_scenes(content_scenes, visual_scenes)

        script_data = {"title": content.get("title") or f"第{episode}集", "scenes": merged_scenes}
        script_data = self._add_metadata(script_data, episode)

        filename = output_filename or episode_script_filename(episode)
        pm = ProjectManager(str(self.project_path.parent))
        output_path = pm.save_script(self.project_path.name, script_data, filename, validate=True)

        self._quality_probe(script_data, episode)
        logger.info("剧本已保存至 %s", output_path)
        return output_path

    def _build_drama_step2_prompt(self, content_scenes: list, episode: int) -> str:
        """构建 drama step2（视觉层）prompt：把 step1 内容渲染为输入，仅求 image_prompt / video_prompt。"""
        return build_drama_prompt(
            project_overview=self.project_json.get("overview", {}),
            style=self.project_json.get("style", ""),
            style_description=self.project_json.get("style_description", ""),
            scenes_content=render_drama_content_for_step2(content_scenes),
            episode=episode,
            aspect_ratio=self._resolve_aspect_ratio(),
            # 输出语言与 step1（normalize）同取项目 source_language，避免非中文项目 step1 内容与 step2 视觉割裂
            target_language=self.project_json.get("source_language") or "中文",
        )

    def _parse_drama_visual(self, response_text: str) -> list[dict]:
        """解析 step2 视觉层 LLM 响应为 scene 视觉 dict 列表（scene_id + image_prompt + video_prompt）。

        校验失败时降级取原始 scenes，由后续 ``merge_drama_visual_into_scenes`` 按覆盖/对齐 fail-loud。
        """
        text = strip_json_code_fences(response_text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"step2 视觉层 JSON 解析失败: {e}")
        try:
            validated = DramaVisualScript.model_validate(data)
            return [s.model_dump() for s in validated.scenes]
        except ValidationError as e:
            logger.warning("step2 视觉层校验警告: %s", e)
            raw = data.get("scenes") if isinstance(data, dict) else None
            return raw if isinstance(raw, list) else []

    async def _generate_and_save(
        self,
        prompt: str,
        schema: type,
        episode: int,
        output_filename: str | None,
        *,
        narration_step1: list[dict] | None = None,
    ) -> Path:
        """调用 TextBackend → 解析校验 → 补元数据 → 经写盘统一入口保存（各内容模式共用尾段）。

        ``narration_step1`` 非 None 时走两段式合并：LLM 输出视觉层，按 segment_id 合并回
        step1 已定结构（novel_text 等透传）；否则走单段解析（reference/drama/ad）。
        """
        assert self.generator is not None  # generate() 入口已检查
        # 调用 TextBackend
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

        # 解析并验证响应
        if narration_step1 is not None:
            visual_data = self._parse_narration_visual(response_text, episode)
            script_data = self._merge_narration_visual(narration_step1, visual_data, episode)
        else:
            script_data = self._parse_response(response_text, episode)

        # 补充元数据
        script_data = self._add_metadata(script_data, episode)

        # 经写盘统一入口保存：整集生成无「改前」，按严格结构校验（等价原 response_schema 的
        # Pydantic 校验），并继承 metadata 重算、加锁、filename↔episode 一致性与 project.json
        # 同步——消除「裸 json.dump 旁路」，使 _write_script_unlocked 成为剧本唯一写入点。
        filename = output_filename or episode_script_filename(episode)
        pm = ProjectManager(str(self.project_path.parent))
        output_path = pm.save_script(self.project_path.name, script_data, filename, validate=True)

        self._quality_probe(script_data, episode)

        logger.info("剧本已保存至 %s", output_path)
        return output_path

    async def _compose_ad(self, episode: int, gen_mode: str) -> tuple[str, type]:
        """ad 分支的 (prompt, response_schema) 构造，generate/build_prompt 共用。

        reference 路径不消费供应商能力（镜头时长为 1-15 自由整数），跳过能力查询；
        storyboard 路径解析一次 supported_durations，prompt 时长枚举与 schema enum 同源。
        """
        if gen_mode == "reference_video":
            supported = None
            schema: type = build_ad_reference_episode_script_model()
        else:
            caps = await self._fetch_video_capabilities()
            supported = self._resolve_supported_durations(caps)
            schema = build_episode_script_model("ad", supported)
        return self._build_ad_prompt(episode, gen_mode, supported), schema

    def _build_ad_prompt(self, episode: int, gen_mode: str, supported: list[int] | None) -> str:
        """构建广告/短片模式 prompt：brief + 产品信息 + 审定配比表，不读 step1 中间文件。

        storyboard 路径把 supported_durations 作为单镜头时长枚举写进 prompt（与
        response_schema 的 enum 同口径）；reference 路径 ``supported`` 为 None（1-15 自由整数）。
        """
        target_duration = self.project_json.get("target_duration")
        if not isinstance(target_duration, int) or isinstance(target_duration, bool) or target_duration <= 0:
            raise ValueError(f"广告/短片项目缺少合法的 target_duration（正整数秒），当前为 {target_duration!r}")
        # 统一 `or` 兜底：project.json 手工编辑时字段可能显式为 null，
        # `.get(key, default)` 拿到 None 会让 prompt 构建在 `.keys()`/`.get()` 上崩溃。
        return build_ad_prompt(
            project_overview=self.project_json.get("overview") or {},
            style=self.project_json.get("style") or "",
            style_description=self.project_json.get("style_description") or "",
            characters=self.project_json.get("characters") or {},
            scenes=self.project_json.get("scenes") or {},
            props=self.project_json.get("props") or {},
            products=self.project_json.get("products") or {},
            brief=self.project_json.get("brief") or "",
            target_duration=target_duration,
            generation_mode=gen_mode,
            supported_durations=supported,
            episode=episode,
            aspect_ratio=self._resolve_aspect_ratio(),
        )

    async def build_prompt(self, episode: int) -> str:
        """
        构建 Prompt（用于 dry-run 模式）

        与 `generate()` 同样先 await `_fetch_video_capabilities()` 解析 caps；
        这样当 `project.json` 不显式声明 `video_backend`（用户依赖全局/系统默认时）也能
        正确派生 supported_durations。caps 失败仍 fallback 到 project.json 自身的 sync 链。
        """
        gen_mode = self._effective_generation_mode(episode)

        # 见 generate() 同位置说明：ad 先于 generation_mode 分派，且不读 step1。
        if self.content_mode == "ad":
            prompt, _schema = await self._compose_ad(episode, gen_mode)
            return prompt

        # drama（storyboard / grid）dry-run 走 step2 视觉层 prompt：读 step1 结构化内容并渲染
        # （见 generate() 的两段式说明）。reference_video / narration 不入此分支。
        if gen_mode != "reference_video" and self.content_mode != "narration":
            content = self._load_drama_step1_content(episode)
            raw_scenes = content.get("scenes")
            content_scenes: list = raw_scenes if isinstance(raw_scenes, list) else []
            return self._build_drama_step2_prompt(content_scenes, episode)

        caps = await self._fetch_video_capabilities()
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
                units_md=self._load_step1(episode),
                supported_durations=self._resolve_supported_durations(caps),
                max_refs=self._resolve_max_refs(caps),
                max_duration=self._resolve_max_duration(caps),
                aspect_ratio=self._resolve_aspect_ratio(),
                episode=episode,
            )
        # narration 两段式：step1 透传内容层（novel_text 等），step2 仅产视觉层。
        # drama / ad 已在前面早返回，reference 走上面分支，故此处必为 narration。
        return build_narration_prompt(
            project_overview=self.project_json.get("overview", {}),
            style=self.project_json.get("style", ""),
            style_description=self.project_json.get("style_description", ""),
            characters=characters,
            scenes=scenes,
            props=props,
            step1_segments=self._load_narration_step1(episode, self._resolve_supported_durations(caps)),
            aspect_ratio=self._resolve_aspect_ratio(),
            episode=episode,
            target_language=self.project_json.get("source_language") or "中文",
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
        """解析项目的 aspect_ratio，向后兼容。narration / ad 默认竖屏（ad 与创建向导默认一致）。"""
        if "aspect_ratio" in self.project_json and isinstance(self.project_json["aspect_ratio"], str):
            return self.project_json["aspect_ratio"]
        return "9:16" if self.content_mode in ("narration", "ad") else "16:9"

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
        """加载 Step 1 中间文件的原始文本（reference_video 的 .md 与 drama 的结构化 .json）。

        每种模式只对应一个期望文件，缺失时显式报错并指明期望路径——不降级改读
        其他模式的中间文件（静默 fallback 会让剧本基于错误模式的中间产物生成）。
        drama 的 step1 是结构化 JSON（内容抽取前移，见 ADR 0041），reference_video 仍为 Markdown。
        narration（storyboard/grid）走结构化两段式，单独经 ``_load_narration_step1`` 读
        ``step1_segments.json``，不进本方法。
        """
        drafts_path = episode_drafts_dir(self.project_path, episode)
        gen_mode = self._effective_generation_mode(episode)
        if gen_mode == "reference_video":
            step1_path = drafts_path / REFERENCE_VIDEO_STEP1_FILENAME
        else:
            # 本方法只服务 drama 及未来其它走 drama 形状两段式的结构化模式（narration 另经
            # _load_narration_step1）；按 content_mode 取登记的结构化文件名，脏值兜底 drama。
            step1_path = drafts_path / STEP1_FILENAMES.get(self.content_mode, STEP1_FILENAMES["drama"])

        if not step1_path.exists():
            raise FileNotFoundError(
                f"未找到 Step 1 中间文件: {step1_path}；"
                f"content_mode={self.content_mode}, generation_mode={gen_mode} 期望该文件，"
                "请先完成本集预处理"
            )

        return step1_path.read_text(encoding="utf-8")

    def _load_narration_step1(self, episode: int, supported_durations: list[int]) -> list[dict]:
        """加载并校验 narration step1 结构化中间文件 ``step1_segments.json``。

        返回逐字 ``novel_text``、时长、``segment_break`` 等内容字段的片段列表（dict），
        供 step2 prompt 渲染与视觉层合并复用——novel_text 由此透传、不经 step2 的 LLM 重出。
        校验：结构合法、segment_id 唯一、``duration_seconds`` ∈ ``supported_durations``
        （duration 约束由原 step2 schema enum 前移到 step1，因 step2 不再产出该字段）。
        仅存在结构化前的旧 ``step1_segments.md`` 时给明确的「重跑拆分」报错——不写
        md→json 迁移器（旧 md 产于结构化中间态引入前、不含手工编辑）。
        """
        drafts_path = episode_drafts_dir(self.project_path, episode)
        narration_json = STEP1_FILENAMES["narration"]
        step1_json = drafts_path / narration_json
        if not step1_json.exists():
            legacy_md = drafts_path / STEP1_LEGACY_FILENAMES["narration"][0]
            if legacy_md.exists():
                raise FileNotFoundError(
                    f"仅找到结构化前的旧拆分表 {legacy_md}，未找到 {step1_json}；"
                    f"请重跑 split-narration-segments 产出结构化 {narration_json}"
                )
            raise FileNotFoundError(
                f"未找到 Step 1 中间文件: {step1_json}；content_mode=narration 期望该文件，请先完成片段拆分"
            )

        try:
            raw = json.loads(step1_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"step1_segments.json 解析失败: {e}")

        try:
            draft = NarrationStep1Draft.model_validate(raw)
        except ValidationError as e:
            raise ValueError(f"step1_segments.json 结构校验失败: {e}")

        segments = [s.model_dump() for s in draft.segments]
        if not segments:
            raise ValueError("step1_segments.json segments 为空")

        ids = [s["segment_id"] for s in segments]
        dupes = sorted(sid for sid, count in Counter(ids).items() if count > 1)
        if dupes:
            raise ValueError(f"step1_segments.json segment_id 重复: {dupes}")

        # _add_metadata 落盘前会把 E\d+ 前缀改写成当前 episode：原始 id 互异但改写后可能相撞
        # （E1S02_1 与 E2S02_1 在 episode=2 都成 E2S02_1）。提前 fail-loud，杜绝重复 id 静默落盘。
        rewritten_ids = [str(_rewrite_episode_prefix(sid, episode)) for sid in ids]
        rewritten_dupes = sorted(sid for sid, count in Counter(rewritten_ids).items() if count > 1)
        if rewritten_dupes:
            raise ValueError(f"step1_segments.json segment_id 改写到 episode={episode} 后重复: {rewritten_dupes}")

        allowed = {int(d) for d in supported_durations}
        bad = sorted({s["duration_seconds"] for s in segments if s["duration_seconds"] not in allowed})
        if bad:
            raise ValueError(f"step1_segments.json duration_seconds 非法（不在 {sorted(allowed)} 内）: {bad}")

        return segments

    def _load_drama_step1_content(self, episode: int) -> dict:
        """加载并解析 drama 的 step1 结构化内容（``step1_normalized_script.json``）。

        返回 ``{title, scenes: [...]}`` dict；缺文件抛 FileNotFoundError（_load_step1）、
        内容非合法 JSON / 顶层非对象 / scenes 非非空列表 / 含非对象场景项 / scene_id 非非空字符串 /
        scene_id 改写到当前集号后重复，均抛 ValueError。各场景的内部字段（utterances / source_text 等）
        由 step2 合并后经 save_script 的结构校验把关，此处只做最外层形状守卫——但 scenes 形状与 scene_id
        须在此 fail-fast，否则坏 step1 会被当成空剧本静默落盘、scene_id 撞键拖到产物文件名 / 资产键才暴露，
        或在 render/merge 阶段抛内部异常而非明确的 step1 校验错误。
        """
        raw = self._load_step1(episode)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"Step 1 内容文件不是合法 JSON（drama step1 应为结构化内容）: {e}")
        if not isinstance(data, dict):
            raise ValueError("Step 1 内容文件结构异常：顶层应为对象 {title, scenes}")
        scenes = data.get("scenes")
        if not isinstance(scenes, list) or not scenes:
            raise ValueError("Step 1 内容文件结构异常：scenes 必须是非空的场景对象数组")
        scene_ids: list[str] = []
        for idx, scene in enumerate(scenes):
            if not isinstance(scene, dict):
                raise ValueError(f"Step 1 内容文件结构异常：scenes[{idx}] 必须是场景对象")
            scene_id = scene.get("scene_id")
            if not isinstance(scene_id, str) or not scene_id:
                raise ValueError(f"Step 1 内容文件结构异常：scenes[{idx}].scene_id 必须是非空字符串")
            scene_ids.append(scene_id)
        # _add_metadata 落盘前会把 E\d+ 前缀改写成当前 episode：原始 id 互异但改写后可能相撞
        # （E1S02_1 与 E2S02_1 在 episode=2 都成 E2S02_1）。提前 fail-loud，杜绝重复 id 静默落盘、
        # 下游产物文件名 / 资产键撞车。与 _load_narration_step1 同口径。
        rewritten_ids = [str(_rewrite_episode_prefix(sid, episode)) for sid in scene_ids]
        rewritten_dupes = sorted(sid for sid, count in Counter(rewritten_ids).items() if count > 1)
        if rewritten_dupes:
            raise ValueError(f"Step 1 内容文件 scene_id 改写到 episode={episode} 后重复: {rewritten_dupes}")
        return data

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
            raise ValueError(f"JSON 解析失败: {e}")

        # Pydantic 验证。ad 先于 generation_mode 判别：骨架唯一，reference 路径仍是 shots[]。
        try:
            if self.content_mode == "ad":
                validated = AdEpisodeScript.model_validate(data)
            elif self._effective_generation_mode(episode) == "reference_video":
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

    def _parse_narration_visual(self, response_text: str, episode: int) -> dict:
        """解析 step2 视觉层 LLM 响应（NarrationVisualEpisodeScript）。

        严格校验 + model_dump：视觉 schema 的 segment 走 ``extra="forbid"``，LLM 若混入
        novel_text 等非视觉字段即拒（而非静默携带进合并覆盖 step1 透传值）；dump 后视觉
        数据只含 title + segment_id + image_prompt / video_prompt，合并阶段不会污染内容层。
        """
        text = strip_json_code_fences(response_text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"step2 视觉层 JSON 解析失败: {e}")
        try:
            validated = NarrationVisualEpisodeScript.model_validate(data)
        except ValidationError as e:
            raise ValueError(f"step2 视觉层结构校验失败: {e}")
        return validated.model_dump()

    def _merge_narration_visual(self, step1_segments: list[dict], visual_data: dict, episode: int) -> dict:
        """把 step2 LLM 的视觉层按 segment_id 合并回 step1 已确认的结构。

        step1 结构（novel_text、时长、segment_break 等内容字段）是单一真相源，逐字透传；
        LLM 只产出视觉层，按 segment_id 对齐合并回各片段——novel_text 永不经 LLM 重出，
        从工程上根除扩写漂移。校验 segment_id 唯一且与 step1 全覆盖：缺、多、重都 fail-loud，
        杜绝顺序错配与漏段。
        """
        visual_segments = visual_data["segments"]

        visual_by_id: dict[str, dict] = {}
        for item in visual_segments:
            sid = item["segment_id"]
            if sid in visual_by_id:
                raise ValueError(f"episode {episode} 视觉层 segment_id 重复: {sid}")
            visual_by_id[sid] = item

        step1_ids = [s["segment_id"] for s in step1_segments]
        step1_id_set = set(step1_ids)
        missing = [sid for sid in step1_ids if sid not in visual_by_id]
        if missing:
            raise ValueError(f"episode {episode} 视觉层缺少 step1 片段: {missing}")
        extra = [sid for sid in visual_by_id if sid not in step1_id_set]
        if extra:
            raise ValueError(f"episode {episode} 视觉层含 step1 未定义的 segment_id: {extra}")

        merged_segments: list[dict] = []
        for s1 in step1_segments:
            sid = s1["segment_id"]
            merged_segments.append({**s1, **visual_by_id[sid]})

        title = visual_data.get("title")
        return {
            "title": title if isinstance(title, str) and title.strip() else f"第{episode}集",
            "segments": merged_segments,
        }

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
        if self.content_mode != "ad" and gen_mode == "reference_video":
            for u in script_data.get("video_units") or []:
                if isinstance(u, dict) and "unit_id" in u:
                    u["unit_id"] = _rewrite_episode_prefix(u.get("unit_id"), ep)
        else:
            # narration/drama/ad 统一按 SCRIPT_SHAPES 查表（ad 骨架唯一，不随生成路径换判别）
            shape = script_shape(self.content_mode)
            for s in script_data.get(shape.items_key) or []:
                if isinstance(s, dict) and shape.id_field in s:
                    s[shape.id_field] = _rewrite_episode_prefix(s.get(shape.id_field), ep)
        # content_mode 严格只是"内容类型"（narration/drama）；reference_video 属于
        # "视频来源"维度，由 generation_mode 表达。
        # 参考视频集必须强制覆盖：ReferenceVideoScript.content_mode 有 Pydantic 默认值
        # "narration"，setdefault 拿不到项目级真值；非参考集 LLM 已在 schema 中产出
        # narration/drama，setdefault 仅作 fallback。
        # ad 剧本骨架唯一、不携带"视频来源"维度：不打 generation_mode 戳——按剧本级
        # generation_mode 分派的消费方（StatusCalculator / enqueue 判别等）会被该戳
        # 误导去找不存在的 video_units。
        if self.content_mode != "ad" and gen_mode == "reference_video":
            script_data["content_mode"] = self.content_mode
            script_data["generation_mode"] = "reference_video"
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

        # 添加时间戳
        now = datetime.now(UTC).isoformat()
        script_data.setdefault("metadata", {})
        script_data["metadata"]["created_at"] = now
        script_data["metadata"]["updated_at"] = now
        script_data["metadata"]["generator"] = self.generator.model if self.generator else "unknown"

        # 计算统计信息（episode 级角色/场景/道具聚合由 StatusCalculator 读时计算）
        if self.content_mode == "ad":
            # shots 无单镜头默认时长偏好，缺失按 0 计（与 StatusCalculator 口径一致）。
            # 校验失败降级保存的原始 dict 里 shots 可能为 null / 含脏条目，求和走稳健口径。
            raw_shots = script_data.get("shots")
            shots = raw_shots if isinstance(raw_shots, list) else []
            script_data["metadata"]["total_shots"] = len(shots)
            script_data["duration_seconds"] = ad_script_total_duration(shots)
        elif gen_mode == "reference_video":
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
            if self.content_mode != "ad" and gen_mode == "reference_video":
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
                # narration/drama/ad 统一按 SCRIPT_SHAPES 查表；ad 骨架唯一，两条生成路径
                # 都按 shots 探针，与 narration/drama 共用 scene/action 的过短判定。
                shape = script_shape(self.content_mode)
                items = script_data.get(shape.items_key) or []
                id_key = shape.id_field
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

            # narration 的 novel_text 现由 step1 透传、step2 不再重出，扩写漂移已从结构上
            # 消除（不存在「LLM 偷偷扩写」的窗口），故不再做 novel_text 漂移探针。

            # ad 总时长偏差观察：剧本总时长应贴近 target_duration，但供应商时长枚举的
            # 量化误差让精确命中不现实。仅 WARN，不阻断/不重试/不推前端。
            if self.content_mode == "ad":
                target = self.project_json.get("target_duration")
                if isinstance(target, int) and not isinstance(target, bool) and target > 0:
                    total = ad_script_total_duration(script_data.get("shots"))
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
