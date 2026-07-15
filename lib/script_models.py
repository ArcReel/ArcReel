"""
script_models.py - 剧本数据模型

使用 Pydantic 定义剧本的数据结构，用于：
1. Gemini API 的 response_schema（Structured Outputs）
2. 输出验证
"""

import logging
from typing import Annotated, ClassVar, Literal, get_args

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, create_model, model_validator
from pydantic.json_schema import SkipJsonSchema

from lib.script_skeleton import resolve_declared_kind

# 所有剧本模型默认禁止额外字段:agent 的 `patch_episode_script` 通过 `_set_nested` 允许在
# dict 上凭空创建叶子(为了让 agent 补 LLM 漏写的 optional 字段);若 Pydantic 走默认
# `extra="ignore"`,任何 typo / hallucinated 字段都会被静默丢,但 dict 已被 atomic_write_json
# 持久化,JSON 文件里垃圾字段长存,「不更坏」error-set diff 永远抓不到(before/after Pydantic
# 都 ignore → 两边 errors 集合相同 → new_errors=∅ → 放行)。`extra="forbid"` 让 Pydantic
# 在 typo 写入后明确把它列为新 ValidationError,「不更坏」就能挡下。
# ScriptGenerator 路径(LLM 输出走 model_validate + model_dump)也会被这层保护:LLM 在
# Structured Outputs 下不太会产出额外字段,产出即 hallucination,拒比静默丢更安全。
_STRICT_CONFIG = ConfigDict(extra="forbid")

# ============ 枚举类型定义 ============

ShotType = Literal[
    "Extreme Close-up",
    "Close-up",
    "Medium Close-up",
    "Medium Shot",
    "Medium Long Shot",
    "Long Shot",
    "Extreme Long Shot",
    "Over-the-shoulder",
    "Point-of-view",
]

# 取值须为供应商官方运镜词表承认的写法（对齐 MiniMax Hailuo [command] 指令表与阿里万相
# 基础/高级运镜表的运动类条目），下游按原文插值进视频 prompt，不做二次翻译。
CameraMotion = Literal[
    "Static",
    "Pan Left",
    "Pan Right",
    "Tilt Up",
    "Tilt Down",
    "Zoom In",
    "Zoom Out",
    "Push In",
    "Pull Out",
    "Truck Left",
    "Truck Right",
    "Pedestal Up",
    "Pedestal Down",
    "Orbit",
    "Tracking Shot",
    "Shake",
]

TransitionType = Literal[
    "cut",
    "fade",
    "dissolve",
]

logger = logging.getLogger(__name__)


def _canon_enum_key(value: str) -> str:
    """枚举漂移归一键：下划线/连字符折叠为空格、多空格合一、casefold。"""
    return " ".join(value.replace("_", " ").replace("-", " ").split()).casefold()


# schema 的 enum 只有在供应商执行约束解码时才是硬约束；代理网关/OpenAI 兼容通道丢弃
# wire 级结构化参数时，模型会把枚举写成大写/小写蛇形（MEDIUM_SHOT / medium_shot）
# 甚至词表外值（wide_shot / dolly_in，均为线上实测值）。机械归一（大小写/
# 分隔符）把风格漂移拉回词表；词表外值不做语义近义映射（语义映射永远穷举不全），
# 一律降级为中性默认值并 warn——这两个字段下游只作生成 prompt 的文本插值，
# 单镜头词汇漂移不值得让整集剧本生成失败。
_DEFAULT_SHOT_TYPE: ShotType = "Medium Shot"
_DEFAULT_CAMERA_MOTION: CameraMotion = "Static"

_SHOT_TYPE_BY_KEY: dict[str, str] = {_canon_enum_key(v): v for v in get_args(ShotType)}
_CAMERA_MOTION_BY_KEY: dict[str, str] = {_canon_enum_key(v): v for v in get_args(CameraMotion)}


def _normalize_shot_type(value: object) -> object:
    if not isinstance(value, str):
        return value
    hit = _SHOT_TYPE_BY_KEY.get(_canon_enum_key(value))
    if hit is not None:
        return hit
    logger.warning("shot_type 枚举漂移无法归一，降级为 %s: %r", _DEFAULT_SHOT_TYPE, value)
    return _DEFAULT_SHOT_TYPE


def _normalize_camera_motion(value: object) -> object:
    if not isinstance(value, str):
        return value
    hit = _CAMERA_MOTION_BY_KEY.get(_canon_enum_key(value))
    if hit is not None:
        return hit
    logger.warning("camera_motion 枚举漂移无法归一，降级为 %s: %r", _DEFAULT_CAMERA_MOTION, value)
    return _DEFAULT_CAMERA_MOTION


def _none_to_empty_list(value: object) -> object:
    """漂移容错：非约束解码通道可能把可选列表字段写成 null 而非省略。"""
    return [] if value is None else value


class Dialogue(BaseModel):
    """对话条目"""

    model_config = _STRICT_CONFIG

    speaker: str = Field(description="说话人名称")
    line: str = Field(description="对话内容")


class Composition(BaseModel):
    """构图信息"""

    model_config = _STRICT_CONFIG

    shot_type: Annotated[ShotType, BeforeValidator(_normalize_shot_type)] = Field(description="镜头类型")
    lighting: str = Field(description="光线描述")
    ambiance: str = Field(description="整体氛围")


class ImagePrompt(BaseModel):
    """分镜图生成 Prompt"""

    model_config = _STRICT_CONFIG

    scene: str = Field(description="画面静态描述；动态内容由 video_prompt.action 承载")
    composition: Composition = Field(description="构图信息")


class _VideoPromptCore(BaseModel):
    """video_prompt 的画面层公共字段（动作 / 运镜 / 环境音）；dialogue 由具体变体决定是否携带。"""

    model_config = _STRICT_CONFIG

    action: str = Field(description="该镜头时长内的动作描述；镜头运动由 camera_motion 承载")
    camera_motion: Annotated[CameraMotion, BeforeValidator(_normalize_camera_motion)] = Field(description="镜头运动")
    ambiance_audio: str = Field(description="环境音效（画内音）")


class VideoPrompt(_VideoPromptCore):
    """narration / ad 视频生成 Prompt：含角色对话 dialogue。

    drama 不用本模型——其台词迁入场景级 ``DramaScene.utterances``，video_prompt 用无-dialogue 的
    ``DramaVideoPrompt`` 变体（见 ADR 0040）。
    """

    dialogue: Annotated[list[Dialogue], BeforeValidator(_none_to_empty_list)] = Field(
        default_factory=list, description="对话列表，仅当原文有引号对话时填写"
    )


class DramaVideoPrompt(_VideoPromptCore):
    """drama 视频生成 Prompt：无 dialogue（口播统一迁入场景级 ``DramaScene.utterances``）。

    ``extra="forbid"`` 下任何残留的 ``dialogue`` 键会被 ``DramaScene`` 读时迁移先行剥离。
    """


class GeneratedAssets(BaseModel):
    """生成资源状态（初始化为空）"""

    model_config = _STRICT_CONFIG

    storyboard_image: str | None = Field(default=None, description="分镜图路径")
    storyboard_last_image: str | None = Field(default=None, description="分镜图最后一帧路径")
    grid_id: str | None = Field(default=None, description="关联的网格图生成 ID")
    grid_cell_index: int | None = Field(default=None, description="在网格图中的单元格索引")
    video_clip: str | None = Field(default=None, description="视频片段路径")
    # video_thumbnail 由 reference_video_tasks / generation_tasks 在视频生成后通过
    # lib.thumbnail.extract_video_thumbnail 抽帧落盘,写到 ga["video_thumbnail"];
    # 漏声明的话 extra="forbid" 会让「不更坏」检测到 extra_forbidden 差集,拒整集写盘。
    video_thumbnail: str | None = Field(default=None, description="视频缩略图路径")
    video_uri: str | None = Field(default=None, description="视频 URI")
    # narration_audio 由 TTS 任务（generation_tasks.execute_tts_task）在合成后写回，
    # 显式声明使其通过 extra="forbid" + 「不更坏」守卫；仅说书 segment 写入，drama/refvideo 恒 None。
    narration_audio: str | None = Field(default=None, description="旁白音频路径")
    status: Literal["pending", "storyboard_ready", "completed"] = Field(default="pending", description="生成状态")


# ============ 说书模式（Narration） ============


class NarrationSegment(BaseModel):
    """说书模式的片段

    注意：不设独立 `episode` 字段。集号已经编码在 `segment_id`（格式 E{集}S{序号}）中，
    与 `DramaScene.scene_id` / `ReferenceVideoUnit.unit_id` 保持一致。避免 AI 在每个
    segment 上重复生成集号造成幻觉污染（详见 `NarrationEpisodeScript` docstring）。
    """

    model_config = _STRICT_CONFIG

    # 已废弃但存量 JSON 里可能残留的字段:在 extra="forbid" 拒绝之前显式 pop 掉。
    # clues_in_segment 是 v0→v1 migration 删除的字段(lib/project_migrations/
    # v0_to_v1_clues_to_scenes_props.py),archive 流程通过 project_archive.py 已 pop,
    # 但若直接 NarrationSegment.model_validate(legacy_dict) 调用(_guard_no_worse lenient
    # 包装外)需要这里兜底,与 DramaScene.LEGACY_DROPPED_FIELDS 同模式。
    LEGACY_DROPPED_FIELDS: ClassVar[frozenset[str]] = frozenset({"clues_in_segment"})

    @model_validator(mode="before")
    @classmethod
    def _strip_legacy_fields(cls, data: object) -> object:
        if isinstance(data, dict):
            for k in cls.LEGACY_DROPPED_FIELDS:
                data.pop(k, None)
        return data

    segment_id: str = Field(description="片段 ID，格式 E{集}S{序号} 或 E{集}S{序号}_{子序号}")
    duration_seconds: int = Field(ge=1, le=60, description="片段时长（秒）")
    segment_break: bool = Field(default=False, description="是否为场景切换点")
    novel_text: str = Field(description="小说原文（必须原样保留，用于后期配音）")
    characters_in_segment: list[str] = Field(description="出场角色名称列表")
    scenes: list[str] = Field(default_factory=list, description="出场场景名称列表")
    props: list[str] = Field(default_factory=list, description="出场道具名称列表")
    image_prompt: ImagePrompt = Field(description="分镜图生成提示词")
    video_prompt: VideoPrompt = Field(description="视频生成提示词")
    # transition_to_next 由 _add_metadata default + 用户 PATCH 路径(projects.py UpdateSegmentRequest)管理;
    # LLM 无 prompt 引导,隐藏避免乱填污染剪映/compose-video 合成
    transition_to_next: SkipJsonSchema[TransitionType] = Field(default="cut", description="转场类型")
    # 以下字段对 LLM 隐藏（SkipJsonSchema）：note 是人工备注、generated_assets 是 post-LLM 运行时状态。
    # 仍保留在 Pydantic 模型里以便存储 / 校验，但不出现在 response_schema 中，避免 LLM 填污染数据。
    note: SkipJsonSchema[str | None] = Field(default=None, description="用户备注（不参与生成）")
    generated_assets: SkipJsonSchema[GeneratedAssets] = Field(
        default_factory=GeneratedAssets, description="生成资源状态"
    )


class NovelInfo(BaseModel):
    """小说来源信息

    title/chapter 都带 default,以便 SkipJsonSchema[NovelInfo] 的 default_factory=NovelInfo 构造。
    真实值由 ``ScriptGenerator._add_metadata`` setdefault 注入(项目 title + ``f"第N集"``);
    LLM 不再被引导填写,避免虚构章节名污染 compose-video 的输出 mp4 文件命名。
    """

    model_config = _STRICT_CONFIG

    title: str = Field(default="", description="小说标题")
    chapter: str = Field(default="", description="章节名称")


class NarrationEpisodeScript(BaseModel):
    """说书模式剧集脚本

    注意：`episode` 字段不在 schema 中。CLI 参数 `--episode N` 是集号的唯一真相源，
    由 `ScriptGenerator._add_metadata` 写入。不让 AI 生成该字段，避免幻觉写错集号
    进而污染 project.json（曾导致 episode_10.json 内部 episode=1 覆盖第 1 集条目）。

    顶层**不**走 ``extra="forbid"``:``episode`` / ``metadata`` / ``generation_mode`` 等
    字段由运行时注入(``_add_metadata`` / ``_write_script_unlocked``)而非 schema 内字段,
    顶层 forbid 会让现有写盘流程崩。typo 防护靠子模型(VideoPrompt / ImagePrompt /
    NarrationSegment 等)的 ``extra="forbid"`` 在嵌套字段路径上挡。
    """

    title: str = Field(description="剧集标题")
    # content_mode 由 _add_metadata setdefault 注入项目级真值;Literal 单值让 LLM 写无意义
    content_mode: SkipJsonSchema[Literal["narration"]] = Field(default="narration", description="内容模式")
    # 顶层 duration_seconds 由 ScriptGenerator._add_metadata 求各段之和重算，LLM 填的值会被覆盖；隐藏避免冗余。
    duration_seconds: SkipJsonSchema[int] = Field(default=0, description="总时长（秒）")
    # novel 由 _add_metadata 注入 {项目 title, f"第N集"};compose-video 用 chapter 作输出文件名,LLM 自由发挥反而不可预测
    novel: SkipJsonSchema[NovelInfo] = Field(default_factory=NovelInfo, description="小说来源信息")
    # hook / next_episode_teaser 由 _add_metadata 从分集账本注入（账本是钩子设计的
    # 单一真相源，LLM 不参与填写）；账本无规划数据时为 null。
    hook: SkipJsonSchema[str | None] = Field(default=None, description="集尾钩子（来自分集账本）")
    next_episode_teaser: SkipJsonSchema[str | None] = Field(default=None, description="下集预告语（来自分集账本）")
    segments: list[NarrationSegment] = Field(description="片段列表")


# ============ 说书 step1 结构化中间态 / step2 视觉层 ============
#
# 两段式职责切分：step1（片段拆分）产出内容层（逐字 novel_text + 片段边界 + 时长），
# step2（generate-script）只产出视觉层（image_prompt / video_prompt），按 segment_id
# 合并回 step1 已确认结构。novel_text 永不经 step2 的 LLM 重出 → 消除扩写漂移。


class NarrationStep1Segment(BaseModel):
    """说书 step1（片段拆分）产出的结构化片段：内容层。

    只承载 step1 已定的内容字段：片段边界（segment_id / segment_break）、逐字 novel_text、
    时长。视觉层（image_prompt / video_prompt）由 step2 生成后按 segment_id 合并进来。
    characters_in_segment / scenes / props 由 step1 登记（内容层是资产引用的单一真相源）：
    step2 视觉层 schema 不含资产字段、只读消费、不补登记不改写，故三者必填——无资产须显式写 []，
    缺字段即 fail-loud，杜绝把漏登记静默吞成空数组。合并后落到同一 NarrationSegment。
    """

    model_config = _STRICT_CONFIG

    segment_id: str = Field(min_length=1, description="片段 ID，格式 E{集}S{序号}")
    novel_text: str = Field(min_length=1, description="小说原文（逐字保留，用于配音与透传）")
    duration_seconds: int = Field(ge=1, le=60, description="片段时长（秒）")
    segment_break: bool = Field(default=False, description="是否为场景切换点")
    characters_in_segment: list[str] = Field(description="出场角色名称列表；无则显式写 []")
    scenes: list[str] = Field(description="出场场景名称列表；无则显式写 []")
    props: list[str] = Field(description="出场道具名称列表；无则显式写 []")


class NarrationStep1Draft(BaseModel):
    """说书 step1 结构化中间态（``drafts/episode_N/step1_segments.json`` 的 schema）。

    顶层容忍附加字段（如 ``episode`` 头）：片段拆分由 subagent 经 Write 产出、非结构化输出
    强约束，读时按本模型校验。
    """

    model_config = ConfigDict(extra="ignore")

    segments: list[NarrationStep1Segment] = Field(description="片段列表")


class NarrationVisualSegment(BaseModel):
    """step2（generate-script）按 segment_id 产出的视觉层。

    LLM 只产视觉字段（image_prompt / video_prompt）+ 对齐锚 segment_id；novel_text、时长、
    segment_break、characters_in_segment / scenes / props 等非视觉字段由 step1 已定、经后端
    按 segment_id 合并——不进 LLM 输出，从工程上杜绝其经 Structured Outputs 漂移。
    ``extra="forbid"`` 兜底：非结构化输出后端若混入 novel_text 等字段，校验即拒、不静默覆盖。
    """

    model_config = _STRICT_CONFIG

    segment_id: str = Field(min_length=1, description="对齐锚：必须取自 step1 片段表，逐一对应、不增不减")
    image_prompt: ImagePrompt = Field(description="分镜图生成提示词")
    video_prompt: VideoPrompt = Field(description="视频生成提示词")


class NarrationVisualEpisodeScript(BaseModel):
    """step2 视觉层的 LLM ``response_schema``：剧集标题 + 各片段视觉层。

    顶层不走 ``extra="forbid"``（与 NarrationEpisodeScript 同口径）；逐片段视觉层由
    NarrationVisualSegment 的 ``extra="forbid"`` 在嵌套路径上挡 typo / 漂移。
    """

    title: str = Field(description="剧集标题")
    segments: list[NarrationVisualSegment] = Field(description="各片段的视觉层，按 segment_id 一一对齐 step1")


# ============ 剧集动画模式（Drama） ============


UtteranceKind = Literal["dialogue", "voiceover"]


class Utterance(BaseModel):
    """drama 场景级有序发声条目：插入顺序即幕内时序（台词与画外音的先后）。

    判别式联合 ``{kind, speaker, text}``，``kind`` 决定下游路由与 ``kind ⇄ speaker`` 约束：
    - ``dialogue``：角色台词，必带非空 ``speaker``，进视频 YAML 交供应商出口型音轨；
    - ``voiceover``：无说话人的旁白解说，``speaker`` 必为 ``None``，不作视频提示词（留给字幕 / TTS）。

    取显式 ``kind`` 而非「speaker 有无隐式判别」：与 ``ReferenceResource.type`` 既有判别式风格一致、
    LLM 结构化输出更稳（见 ADR 0040）。
    """

    model_config = _STRICT_CONFIG

    kind: UtteranceKind = Field(description="发声类型：dialogue=角色台词、voiceover=无说话人画外音")
    speaker: str | None = Field(default=None, description="说话角色名；dialogue 必填非空、voiceover 必须为 null")
    text: str = Field(description="发声内容原文，逐字保留")

    @model_validator(mode="before")
    @classmethod
    def _normalize_speaker(cls, data: object) -> object:
        # 空串 / 纯空白 speaker 归一为 None：voiceover 的「无说话人」既可写 null 也可写 ""，统一到
        # None 后由下方 kind ⇄ speaker 校验裁决（dialogue 的空 speaker 因此被判非法）。
        if isinstance(data, dict):
            speaker = data.get("speaker")
            if isinstance(speaker, str) and not speaker.strip():
                data = {**data, "speaker": None}
        return data

    @model_validator(mode="after")
    def _check_kind_speaker(self) -> "Utterance":
        if self.kind == "dialogue":
            if not self.speaker:
                raise ValueError("dialogue utterance 必须带非空 speaker")
        elif self.speaker is not None:
            raise ValueError("voiceover utterance 不得带 speaker")
        return self


class DramaScene(BaseModel):
    """剧集动画模式的场景"""

    model_config = _STRICT_CONFIG

    # 已废弃但存量 JSON 里可能残留的字段:在 extra="forbid" 拒绝之前显式 pop 掉,
    # 与「未知字段(typo / hallucination)一律拒」并存——前者是已知 deprecated,
    # 后者才是 forbid 想挡的真问题。新增 deprecate 字段时把名字加到这个集合。
    # - scene_type:main #644 删的场景类型字段
    # - clues_in_scene:v0→v1 migration 删的线索字段(同 NarrationSegment.clues_in_segment)
    LEGACY_DROPPED_FIELDS: ClassVar[frozenset[str]] = frozenset({"scene_type", "clues_in_scene"})

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy(cls, data: object) -> object:
        """读时迁移：剥离已废弃字段，并把旧口播双字段（``video_prompt.dialogue`` + ``voiceover``）
        合成为有序 ``utterances``。

        判据「无 utterances 键」= 存量数据：合成时 dialogue 段在前、voiceover 段在后（旧数据无交错
        信息，确定性 best-effort、不假装还原），并剥离 ``voiceover`` 与 ``video_prompt.dialogue`` 使
        ``DramaVideoPrompt`` 的 ``extra="forbid"`` 不报错。缺说话人的旧台词归为无说话人 voiceover
        （保内容、不编造 speaker）。新数据（``utterances`` 已在）走快路径、不改写。不就地改调用方 dict。
        """
        if not isinstance(data, dict):
            return data
        legacy_present = any(k in data for k in cls.LEGACY_DROPPED_FIELDS)
        needs_spoken_migration = "utterances" not in data
        if not legacy_present and not needs_spoken_migration:
            return data
        data = dict(data)
        for k in cls.LEGACY_DROPPED_FIELDS:
            data.pop(k, None)
        if needs_spoken_migration:
            data["utterances"] = cls._synthesize_utterances(data)
            data.pop("voiceover", None)
            video_prompt = data.get("video_prompt")
            if isinstance(video_prompt, dict) and "dialogue" in video_prompt:
                data["video_prompt"] = {k: v for k, v in video_prompt.items() if k != "dialogue"}
        return data

    @staticmethod
    def _synthesize_utterances(scene: dict[str, object]) -> list[dict[str, object]]:
        """从旧 ``video_prompt.dialogue`` + 场景 ``voiceover`` 合成有序 utterances（dialogue 段在前）。"""
        utterances: list[dict[str, object]] = []
        video_prompt = scene.get("video_prompt")
        if isinstance(video_prompt, dict):
            dialogue = video_prompt.get("dialogue")
            if isinstance(dialogue, list):
                for entry in dialogue:
                    if not isinstance(entry, dict):
                        continue
                    text = str(entry.get("line") or "").strip()
                    if not text:
                        continue
                    speaker = str(entry.get("speaker") or "").strip()
                    if speaker:
                        utterances.append({"kind": "dialogue", "speaker": speaker, "text": text})
                    else:
                        # 旧台词缺说话人 → 归为无说话人 voiceover（best-effort，保内容、不编造 speaker）
                        utterances.append({"kind": "voiceover", "speaker": None, "text": text})
        voiceover = scene.get("voiceover")
        if isinstance(voiceover, list):
            for line in voiceover:
                if isinstance(line, str) and line.strip():
                    utterances.append({"kind": "voiceover", "speaker": None, "text": line.strip()})
        return utterances

    scene_id: str = Field(min_length=1, description="场景 ID，格式 E{集}S{序号} 或 E{集}S{序号}_{子序号}")
    duration_seconds: int = Field(default=8, ge=1, le=60, description="场景时长（秒）")
    segment_break: bool = Field(default=False, description="是否为场景切换点")
    characters_in_scene: list[str] = Field(description="出场角色名称列表")
    scenes: list[str] = Field(default_factory=list, description="出场场景名称列表")
    props: list[str] = Field(default_factory=list, description="出场道具名称列表")
    image_prompt: ImagePrompt = Field(description="分镜图生成提示词")
    # drama 用无-dialogue 变体：台词迁入下方 utterances，video_prompt 只承载画面动作 / 运镜 / 环境音。
    video_prompt: DramaVideoPrompt = Field(description="视频生成提示词")
    # 场景级有序发声序列，取代旧 video_prompt.dialogue（角色台词）与场景 voiceover（画外音）双字段：
    # dialogue/voiceover 条目按时序排在同一列表，插入顺序即幕内先后（见 ADR 0040）。
    utterances: list[Utterance] = Field(
        default_factory=list,
        description="场景级有序发声序列：角色台词（dialogue）与画外音（voiceover）按时序排列",
    )
    # 逐字原文摘录（追溯锚，类比说书 novel_text，但纯作追溯、不被朗读、不出音、best-effort）。
    # 由 step1（内容抽取）填入，step2（视觉）透传不改；存量数据缺失时默认空串（不更坏守卫放行）。
    source_text: str = Field(default="", description="逐字原文摘录（追溯锚，不朗读、不出音，best-effort）")
    # 见 NarrationSegment.transition_to_next 说明
    transition_to_next: SkipJsonSchema[TransitionType] = Field(default="cut", description="转场类型")
    # 见 NarrationSegment 同名字段说明。
    note: SkipJsonSchema[str | None] = Field(default=None, description="用户备注（不参与生成）")
    generated_assets: SkipJsonSchema[GeneratedAssets] = Field(
        default_factory=GeneratedAssets, description="生成资源状态"
    )


class DramaEpisodeScript(BaseModel):
    """剧集动画模式剧集脚本

    注意：`episode` 字段不在 schema 中，集号由 CLI 真相源通过 `_add_metadata` 写入。
    详见 `NarrationEpisodeScript` docstring。顶层不走 ``extra="forbid"`` 同理。
    """

    title: str = Field(description="剧集标题")
    # 见 NarrationEpisodeScript.content_mode 说明
    content_mode: SkipJsonSchema[Literal["drama"]] = Field(default="drama", description="内容模式")
    # 见 NarrationEpisodeScript.duration_seconds 说明。
    duration_seconds: SkipJsonSchema[int] = Field(default=0, description="总时长（秒）")
    # 见 NarrationEpisodeScript.novel 说明
    novel: SkipJsonSchema[NovelInfo] = Field(default_factory=NovelInfo, description="小说来源信息")
    # 见 NarrationEpisodeScript 同名字段说明。
    hook: SkipJsonSchema[str | None] = Field(default=None, description="集尾钩子（来自分集账本）")
    next_episode_teaser: SkipJsonSchema[str | None] = Field(default=None, description="下集预告语（来自分集账本）")
    scenes: list[DramaScene] = Field(description="场景列表")


# ============ 剧集动画两段式：step1 内容 / step2 视觉（见 ADR 0041） ============
#
# 内容抽取前移到 step1：场景边界、characters/scenes/props、utterances（逐字口播）、source_text
# （逐字原文锚）、scene_description（视觉改编自由文本）一次定稿。step2 只生成视觉层
# （image_prompt / video_prompt），LLM 输出 schema 仅含 scene_id（对齐锚）+ 视觉字段——
# 非视觉字段不进 LLM 输出，从工程上杜绝其经 Structured Outputs 漂移，由后端按 scene_id
# 合并回 step1 已定内容（merge_drama_visual_into_scenes）。


class DramaSceneContent(BaseModel):
    """step1（normalize）产出的场景内容层：除视觉层（image_prompt / video_prompt）外的全部字段。

    作为 step2 视觉生成、以及后续 web 审阅 / 编辑的结构化中间态契约（落盘于
    ``drafts/episode_N/step1_normalized_script.json``，外层为 ``DramaNormalizedScript``）。
    三个文本字段职责严格区分、不可混填：

    - ``scene_description``：**视觉改编自由文本**——只承载画面可见内容（角色动作、神态、环境、光影），
      供 step2 生成 image_prompt / video_prompt 作画面基底；**不内嵌任何口播**，允许相对原文创作改编
      （丢失 / 漂移可容忍，非保真字段）。
    - ``utterances``：**逐字口播**——场景内"说出来的话"的有序序列（台词 dialogue 带 speaker、画外音
      voiceover 无 speaker），下游字幕 / TTS 的单一真相源，step2 透传不改、不重识别。
    - ``source_text``：**逐字原文追溯锚**——本场景所源自的原文片段摘录，供人工对照、失真定位、单场景
      重生成；不被朗读、不出音，与 utterances 分属两事（utterances 是发声、source_text 是溯源）。
    """

    model_config = _STRICT_CONFIG

    scene_id: str = Field(min_length=1, description="场景 ID，格式 E{集}S{序号} 或 E{集}S{序号}_{子序号}")
    duration_seconds: int = Field(default=8, ge=1, le=60, description="场景时长（秒）")
    segment_break: bool = Field(default=False, description="是否为场景切换点")
    characters_in_scene: list[str] = Field(description="出场角色名称列表")
    scenes: list[str] = Field(default_factory=list, description="出场场景名称列表")
    props: list[str] = Field(default_factory=list, description="出场道具名称列表")
    scene_description: str = Field(description="场景视觉改编描述（自由文本，仅承载视觉内容，供 step2 生成视觉层）")
    utterances: list[Utterance] = Field(
        default_factory=list,
        description="场景级有序发声序列：角色台词（dialogue）与画外音（voiceover）按时序排列，逐字保留",
    )
    source_text: str = Field(default="", description="逐字原文摘录（追溯锚，不朗读、不出音，best-effort）")


class DramaNormalizedScript(BaseModel):
    """step1 规范化剧本：场景内容列表。作为 step2 视觉生成与后续 web 审阅 / 编辑的唯一基底。

    顶层不走 ``extra="forbid"``（同 ``DramaEpisodeScript``）：避免落盘时附带的运行时字段触发拒绝。
    """

    title: str = Field(description="剧集标题")
    scenes: list[DramaSceneContent] = Field(description="场景内容列表")


class DramaSceneVisual(BaseModel):
    """step2（generate-script）产出的场景视觉层：仅 scene_id（对齐锚）+ 视觉字段。

    ``scene_id`` 必须等于 step1 已定场景的 scene_id，后端按它（非列表顺序）合并回内容层。
    """

    model_config = _STRICT_CONFIG

    scene_id: str = Field(min_length=1, description="对齐锚：必须等于 step1 已定场景的 scene_id")
    image_prompt: ImagePrompt = Field(description="分镜图生成提示词")
    video_prompt: DramaVideoPrompt = Field(description="视频生成提示词（无 dialogue，口播在 step1 utterances）")


class DramaVisualScript(BaseModel):
    """step2 视觉层剧本：各场景视觉字段（按 scene_id 与 step1 内容对齐）。

    顶层不走 ``extra="forbid"`` 同 ``DramaNormalizedScript``。``title`` 可选，最终标题取自 step1 内容。
    """

    title: str = Field(default="", description="剧集标题（可选，最终以 step1 内容为准）")
    scenes: list[DramaSceneVisual] = Field(description="各场景视觉层（按 scene_id 对齐 step1 内容）")


class DramaVisualMergeError(ValueError):
    """step2 视觉层与 step1 内容层按 scene_id 合并失败（缺覆盖 / 悬空 / 重复 scene_id）。"""


#: 合并后从内容层剔除的、不属于最终 ``DramaScene`` 的 step1-only 字段。
_DRAMA_CONTENT_ONLY_FIELDS = frozenset({"scene_description"})


def merge_drama_visual_into_scenes(
    content_scenes: list[dict[str, object]],
    visual_scenes: list[dict[str, object]],
) -> list[dict[str, object]]:
    """把 step2 视觉层按 ``scene_id`` 合并回 step1 内容层，产出最终 ``DramaScene`` dict 列表。

    工程透传（见 ADR 0041）：非视觉字段（utterances / source_text / characters_in_scene 等）一律取自
    step1 内容、不受 step2 影响；视觉字段（image_prompt / video_prompt）取自 step2。按 ``scene_id``
    对齐（非列表顺序），并校验 scene_id 两侧唯一与全覆盖——内容缺视觉、视觉悬空、内容或视觉重复
    scene_id 均抛 ``DramaVisualMergeError``（内容侧重复会让两个场景共用同一视觉、并在下游产物文件名
    上撞键，故同样 fail-loud）。结果顺序沿用内容层。不就地修改入参。
    """
    visual_by_id: dict[str, dict[str, object]] = {}
    for visual in visual_scenes:
        # 类型注解为 dict，但 _parse_drama_visual 校验失败降级会返回含非 dict 条目的原始列表，
        # 运行时未必成立——此守卫把脏条目转成 DramaVisualMergeError，而非后续 .get() 的 AttributeError。
        if not isinstance(visual, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise DramaVisualMergeError(f"step2 视觉层条目必须是对象: {visual!r}")
        sid = visual.get("scene_id")
        if not isinstance(sid, str) or not sid:
            raise DramaVisualMergeError(f"step2 视觉层条目缺少 scene_id: {visual!r}")
        if sid in visual_by_id:
            raise DramaVisualMergeError(f"step2 视觉层 scene_id 重复: {sid}")
        visual_by_id[sid] = visual

    merged: list[dict[str, object]] = []
    content_ids: set[str] = set()
    for content in content_scenes:
        # 同上：内容层条目运行时未必是 dict（坏 step1 / 降级输入），守卫转 fail-loud。
        if not isinstance(content, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise DramaVisualMergeError(f"step1 内容层条目必须是对象: {content!r}")
        sid = content.get("scene_id")
        if not isinstance(sid, str) or not sid:
            raise DramaVisualMergeError(f"step1 内容层条目缺少 scene_id: {content!r}")
        if sid in content_ids:
            raise DramaVisualMergeError(f"step1 内容层 scene_id 重复: {sid}")
        content_ids.add(sid)
        visual = visual_by_id.get(sid)
        if visual is None:
            raise DramaVisualMergeError(f"step1 场景 {sid} 缺少对应的 step2 视觉层")
        # _parse_drama_visual 校验失败降级会回原始 scenes，其中可能有只含 scene_id、缺视觉字段的半成品；
        # 在合并阶段 fail-loud，避免写入 None 后绕过 DramaVisualMergeError、拖到 save_script 才以通用异常失败。
        if "image_prompt" not in visual or "video_prompt" not in visual:
            raise DramaVisualMergeError(f"step2 视觉层场景 {sid} 缺少必要的视觉字段")
        scene = {k: v for k, v in content.items() if k not in _DRAMA_CONTENT_ONLY_FIELDS}
        scene["image_prompt"] = visual["image_prompt"]
        scene["video_prompt"] = visual["video_prompt"]
        merged.append(scene)

    orphans = set(visual_by_id) - content_ids
    if orphans:
        raise DramaVisualMergeError(f"step2 视觉层存在 step1 内容中不存在的 scene_id: {sorted(orphans)}")

    return merged


# ============ 广告/短片模式（Ad） ============


class AdShot(BaseModel):
    """广告/短片模式的镜头——平铺 shots[] 的最小单元。

    ``section`` 是带货框架段落标签（hook/pain_point/product_reveal/selling_point/
    demo/trust/price_promo/cta 八值引导，不硬枚举，留给 prompt 资产约束）；
    ``voiceover_text`` 是一等口播文案，字幕导出与后续 TTS 的单一来源。产品按名字
    引用 ``products_in_shot``（对应 project.json 的 products bucket），氛围镜头该列表为空。
    """

    model_config = _STRICT_CONFIG

    shot_id: str = Field(description="镜头 ID，格式 E{集}S{序号} 或 E{集}S{序号}_{子序号}")
    section: str = Field(
        description="带货框架段落标签（如 hook/pain_point/product_reveal/selling_point/demo/trust/price_promo/cta）"
    )
    duration_seconds: int = Field(ge=1, le=60, description="镜头时长（秒）")
    voiceover_text: str = Field(description="口播文案（必须完整可照稿配音，可为空字符串）")
    characters_in_shot: list[str] = Field(default_factory=list, description="出场角色名称列表")
    scenes: list[str] = Field(default_factory=list, description="出场场景名称列表")
    props: list[str] = Field(default_factory=list, description="出场道具名称列表")
    products_in_shot: list[str] = Field(default_factory=list, description="出场产品名称列表，非空即产品镜头")
    image_prompt: ImagePrompt = Field(description="分镜图生成提示词")
    video_prompt: VideoPrompt = Field(description="视频生成提示词")
    # 见 NarrationSegment.transition_to_next 说明
    transition_to_next: SkipJsonSchema[TransitionType] = Field(default="cut", description="转场类型")
    # 见 NarrationSegment 同名字段说明。
    note: SkipJsonSchema[str | None] = Field(default=None, description="用户备注（不参与生成）")
    generated_assets: SkipJsonSchema[GeneratedAssets] = Field(
        default_factory=GeneratedAssets, description="生成资源状态"
    )


class AdUnitReference(BaseModel):
    """ad 派生分组的参考条目——比 ``ReferenceResource`` 多 ``product`` 类型。

    产品镜头沿用注入二元规则（见 docs/adr/0034）：产品参考全量进入 unit
    参考集且排序绝对优先，故索引条目须能表达 product 类型。
    """

    model_config = _STRICT_CONFIG

    type: Literal["product", "character", "scene", "prop"] = Field(description="引用的资源类型")
    name: str = Field(description="资产名称，必须在 project.json 对应 bucket 中已注册")


class AdReferenceUnit(BaseModel):
    """ad + reference_video 路径的派生分组索引条目。

    轻量索引仅引用 shot_id 与参考集，不复制镜头内容（shots 是内容唯一真相，
    见 docs/adr/0033）；``generated_assets`` 是 unit 级运行时状态（产物文件按
    unit_id 命名），由生成 finalize 写回。索引由 ``lib.reference_video.ad_units``
    的派生分组器从 shots 重算，shot_id 引用完整性不在结构层校验——镜头删除后
    索引短暂悬空是合法的中间态（重新派生即愈），结构层若拒绝会反过来阻塞镜头编辑。
    """

    model_config = _STRICT_CONFIG

    unit_id: str = Field(description="格式 E{集}U{序号}")
    shot_ids: list[str] = Field(min_length=1, max_length=4, description="成员镜头 ID（连续、1-4 个）")
    references: list[AdUnitReference] = Field(default_factory=list, description="继承的参考集，产品在前")
    generated_assets: GeneratedAssets = Field(default_factory=GeneratedAssets, description="生成资源状态")


class AdEpisodeScript(BaseModel):
    """广告/短片模式剧集脚本（恒单集，剧本即第 1 集脚本文件）。

    注意：`episode` 字段不在 schema 中，集号由 CLI 真相源通过 `_add_metadata` 写入。
    详见 `NarrationEpisodeScript` docstring。顶层不走 ``extra="forbid"`` 同理。
    """

    title: str = Field(description="短片标题")
    # 见 NarrationEpisodeScript.content_mode 说明
    content_mode: SkipJsonSchema[Literal["ad"]] = Field(default="ad", description="内容模式")
    # 见 NarrationEpisodeScript.duration_seconds 说明。
    duration_seconds: SkipJsonSchema[int] = Field(default=0, description="总时长（秒）")
    # 见 NarrationEpisodeScript.novel 说明
    novel: SkipJsonSchema[NovelInfo] = Field(default_factory=NovelInfo, description="小说来源信息")
    shots: list[AdShot] = Field(description="镜头列表")
    # reference_video 路径的派生分组索引，由分组器派生注入而非 LLM 生成；
    # None 表示尚未派生。重新生成剧本时随 LLM 输出重建为 None（shots 已变，索引须重派生）。
    reference_units: SkipJsonSchema[list[AdReferenceUnit] | None] = Field(
        default=None, description="参考直出派生分组索引"
    )


# ============ 参考生视频模式（Reference Video） ============

#: 参考生视频路径下单镜头时长的合法区间（秒）。短切节奏赖此成立：
#: 不按供应商 supported_durations 枚举，而是 1-15 自由整数。
#: ``Shot.duration``、ad + reference 路径的 ``AdShot.duration_seconds`` 与
#: ``DataValidator`` 共用此真相源。
REFERENCE_SHOT_DURATION_RANGE: tuple[int, int] = (1, 15)

#: ad 剧本总时长 vs 项目 target_duration 的偏差观察阈值（比例）。供应商时长枚举
#: （如 [4,6,8]）的量化误差让总和难精确命中目标，阈值放宽只捕明显跑偏；超阈值
#: 仅 warn（生成端 logger、校验端 warnings 列表），不阻塞保存、不推前端。
#: ``ScriptGenerator`` 与 ``DataValidator`` 共用此真相源。
AD_TARGET_DURATION_DRIFT_THRESHOLD = 0.20


def ad_shot_duration_seconds(shot: object) -> int:
    """ad 单镜头时长（秒）的脏数据归一口径：非 dict 条目、非正整数时长
    （bool 按 int 子类排除）一律按 0 计、不抛。

    求和观察（``ad_script_total_duration``）、派生分组与剪映字幕对齐共用此
    单一真相源，避免三处各自维护同一判定。
    """
    if not isinstance(shot, dict):
        return 0
    value = shot.get("duration_seconds")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return 0


def ad_script_total_duration(shots: object) -> int:
    """ad 剧本 shots 总时长（秒）。

    与 target_duration 偏差观察的求和口径单一真相源（``ScriptGenerator`` 探针与
    ``DataValidator`` 共用）：脏数据按 0 计、不抛（见 ``ad_shot_duration_seconds``）——
    求和服务于"仅 warn"的轻量观察与 metadata 统计，对降级保存的原始 dict 也要稳健。
    """
    if not isinstance(shots, list):
        return 0
    return sum(ad_shot_duration_seconds(shot) for shot in shots)


class Shot(BaseModel):
    """参考视频单元内的一个镜头。"""

    model_config = _STRICT_CONFIG

    duration: int = Field(
        ge=REFERENCE_SHOT_DURATION_RANGE[0],
        le=REFERENCE_SHOT_DURATION_RANGE[1],
        description="该镜头时长（秒）",
    )
    text: str = Field(description="镜头描述，可包含 @[角色]/@[场景]/@[道具] 引用")


class ReferenceResource(BaseModel):
    """参考图引用——只存名称 + 类型，具体路径从 project.json 对应 bucket 读时解析。"""

    model_config = _STRICT_CONFIG

    type: Literal["character", "scene", "prop"] = Field(description="引用的资源类型")
    name: str = Field(description="角色/场景/道具名称，必须在 project.json 对应 bucket 中已注册")


class ReferenceVideoUnit(BaseModel):
    """参考视频单元——一个视频文件的最小生成粒度。"""

    model_config = _STRICT_CONFIG

    unit_id: str = Field(description="格式 E{集}U{序号}")
    shots: list[Shot] = Field(min_length=1, max_length=4, description="1-4 个 shot")
    references: list[ReferenceResource] = Field(
        default_factory=list,
        description="按顺序决定 [图N] 编号",
    )
    duration_seconds: int = Field(description="派生字段：所有 shot 时长之和")
    # duration_override / transition_to_next / note / generated_assets 均为 UI / runtime / 人工字段，对 LLM 隐藏。
    duration_override: SkipJsonSchema[bool] = Field(default=False, description="true 时停止自动派生")
    transition_to_next: SkipJsonSchema[TransitionType] = Field(default="cut", description="转场类型")
    note: SkipJsonSchema[str | None] = Field(default=None, description="用户备注")
    generated_assets: SkipJsonSchema[GeneratedAssets] = Field(
        default_factory=GeneratedAssets, description="生成资源状态"
    )

    @model_validator(mode="after")
    def _check_duration_consistency(self) -> "ReferenceVideoUnit":
        if not self.duration_override:
            expected = sum(s.duration for s in self.shots)
            if self.duration_seconds != expected:
                raise ValueError(
                    f"duration_seconds ({self.duration_seconds}) 与 shots 总时长 ({expected}) 不符；"
                    "如需手动指定请置 duration_override=True"
                )
        return self


class ReferenceVideoScript(BaseModel):
    """参考生视频模式剧集脚本。

    注意：`episode` 字段不在 schema 中，集号由 CLI 真相源通过 `_add_metadata` 写入。
    详见 `NarrationEpisodeScript` docstring。顶层不走 ``extra="forbid"`` 同理。

    ``content_mode`` 仅承担"内容类型"维度（narration/drama），"视频来源"维度由
    ``generation_mode = "reference_video"`` 表达。两字段都对 LLM 隐藏，由
    ``ScriptGenerator._add_metadata`` 按项目级配置注入。
    """

    title: str = Field(description="剧集标题")
    # 对 LLM 隐藏：参考视频模式下这两个字段都由 _add_metadata 注入。
    content_mode: SkipJsonSchema[Literal["narration", "drama"]] = Field(
        default="narration", description="内容类型（narration/drama），参考视频模式实际不区分"
    )
    generation_mode: SkipJsonSchema[Literal["reference_video"]] = Field(
        default="reference_video", description="生成模式，固定 reference_video"
    )
    # 见 NarrationEpisodeScript.duration_seconds 说明。
    duration_seconds: SkipJsonSchema[int] = Field(default=0, description="总时长（秒）")
    # 见 NarrationEpisodeScript.novel 说明
    novel: SkipJsonSchema[NovelInfo] = Field(default_factory=NovelInfo, description="小说来源信息")
    # 见 NarrationEpisodeScript 同名字段说明。
    hook: SkipJsonSchema[str | None] = Field(default=None, description="集尾钩子（来自分集账本）")
    next_episode_teaser: SkipJsonSchema[str | None] = Field(default=None, description="下集预告语（来自分集账本）")
    video_units: list[ReferenceVideoUnit] = Field(description="视频单元列表")


# ============ duration 枚举硬约束（按视频模型能力动态构造剧本 schema） ============


def _coerce_digit_string(value: object) -> object:
    """机械强转：纯数字字符串 → int，其余原样透传交给 ``Literal`` 校验。

    Gemini ``responseSchema`` 通道的 ``enum`` 仅支持字符串，整数时长枚举在 wire 层转为
    字符串枚举（``["4","6","8"]``，见 ``lib/text_backends/gemini.py``），约束解码下模型
    输出 ``"4"``；此处恢复 int，使复验与解析两侧的 ``Literal[4,6,8]`` 照常命中。
    """
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return value


def _duration_literal(supported_durations: list[int]) -> object:
    """把 supported_durations 去重排序后构造成数字字符串可强转的 ``Literal[...]``。

    多值在 ``model_json_schema()`` 里渲染为 JSON-schema ``enum``、单值渲染为 ``const``，两者都是硬约束。
    与 ``ConfigResolver`` 同口径用 ``int(d)`` 归一（见 ``lib/config/resolver.py`` custom 分支）。空集抛 ValueError。
    """
    values = tuple(sorted({int(d) for d in supported_durations}))
    if not values:
        raise ValueError("supported_durations 为空，无法构造 duration 枚举约束")
    return Annotated[Literal[values], BeforeValidator(_coerce_digit_string)]


def _constrained_duration_item(item_base: type[BaseModel], duration_type: object, description: str) -> type[BaseModel]:
    """在 ``item_base`` 上把 ``duration_seconds`` 收紧为 ``duration_type``（三工厂共用的字段约束骨架）。"""
    return create_model(
        item_base.__name__,
        __base__=item_base,
        duration_seconds=(duration_type, Field(description=description)),
    )


def build_episode_script_model(content_mode: str, supported_durations: list[int]) -> type[BaseModel]:
    """构造 ``duration_seconds`` 被 ``supported_durations`` 枚举硬约束的剧集脚本模型。

    NarrationSegment / DramaScene 静态定义里 ``duration_seconds`` 是 ``Field(ge=1, le=60)`` 的开区间，
    LLM 在此区间内挑个非成员值（如模型支持 [4,6,8] 却写 5/7）能过 Pydantic、却会在执行层
    ``assert_duration_supported`` 处晚失败、甚至漏到供应商 API 报错。这里按当前视频模型的
    ``supported_durations`` 把该字段收紧为 ``Literal[*supported_durations]``：
    - 在 response_schema（结构化输出）里渲染为 JSON-schema ``enum``（单值时为 ``const``）→ LLM 生成层即被卡死；
    - ``model_validate`` 时强制成员校验。

    服务 narration / drama / ad 三种内容模式：骨架种类经规范解析
    （``resolve_declared_kind``，未知/缺失 content_mode fail-loud 抛 ``ValueError``，不落 drama
    兜底），kind → 模型的映射留本地（行为不进注册表）。reference_video 不经此路：其 API 消费的是
    ``unit.duration_seconds``（各 shot 之和），与单 shot 枚举不对应，沿用静态 ``ReferenceVideoScript``。
    """
    duration_type = _duration_literal(supported_durations)
    # storyboard schema 生成不涉 reference 路径，generation_mode 传 None（narration→segments、
    # drama→scenes、ad→shots）；未知 content_mode 在此抛 ValueError。
    kind = resolve_declared_kind(content_mode, None)
    if kind == "segments":
        segment = _constrained_duration_item(
            NarrationSegment, duration_type, "片段时长（秒），必须取 supported_durations 中的值"
        )
        return create_model(
            "NarrationEpisodeScript",
            __base__=NarrationEpisodeScript,
            segments=(list[segment], Field(description="片段列表")),
        )
    if kind == "shots":
        return _ad_episode_model(duration_type, "镜头时长（秒），必须取 supported_durations 中的值")
    scene = _constrained_duration_item(DramaScene, duration_type, "场景时长（秒），必须取 supported_durations 中的值")
    return create_model(
        "DramaEpisodeScript",
        __base__=DramaEpisodeScript,
        scenes=(list[scene], Field(description="场景列表")),
    )


def build_drama_normalized_script_model(supported_durations: list[int]) -> type[BaseModel]:
    """构造 step1 规范化剧本模型，``duration_seconds`` 被 ``supported_durations`` 枚举硬约束。

    内容抽取前移后由 step1 决定场景时长，故 duration 枚举约束加在内容层 ``DramaSceneContent`` 上
    （与 ``build_episode_script_model`` 同口径，渲染为 response_schema 的 enum / const）；step2 视觉层
    不含 duration，沿用静态 ``DramaVisualScript``。
    """
    scene = _constrained_duration_item(
        DramaSceneContent,
        _duration_literal(supported_durations),
        "场景时长（秒），必须取 supported_durations 中的值",
    )
    return create_model(
        "DramaNormalizedScript",
        __base__=DramaNormalizedScript,
        scenes=(list[scene], Field(description="场景内容列表")),
    )


def _ad_episode_model(duration_type: object, description: str) -> type[BaseModel]:
    """ad 剧集脚本的动态包装骨架：两条生成路径共用，仅 ``duration_seconds`` 约束类型不同。"""
    shot = _constrained_duration_item(AdShot, duration_type, description)
    return create_model(
        "AdEpisodeScript",
        __base__=AdEpisodeScript,
        shots=(list[shot], Field(description="镜头列表")),
    )


def build_ad_reference_episode_script_model() -> type[BaseModel]:
    """构造 ad + reference_video 路径的剧集脚本模型：镜头时长 1-15 自由整数。

    ad 剧本骨架唯一、不随生成路径更换（仍是平铺 ``shots[]``），只有
    ``duration_seconds`` 的值约束随路径切换：storyboard 路径走
    ``build_episode_script_model("ad", supported_durations)`` 的枚举硬约束；
    reference 路径不受供应商 supported_durations 限制（参考直达按 unit 聚合
    对接供应商 API），按 ``REFERENCE_SHOT_DURATION_RANGE`` 收紧为自由整数区间，
    与 ``Shot.duration`` 同口径。
    """
    low, high = REFERENCE_SHOT_DURATION_RANGE
    return _ad_episode_model(
        Annotated[int, Field(ge=low, le=high)],
        f"镜头时长（秒），{low}-{high} 间整数任选",
    )


def build_reference_video_script_model(supported_durations: list[int]) -> type[BaseModel]:
    """构造 unit 总时长被 ``supported_durations`` 枚举硬约束的参考视频剧集模型。

    参考视频模式发给供应商 API 的是 ``unit.duration_seconds``（各 shot 时长之和），而非单个 shot——
    单 shot 只是同一段 clip 内的时间编排。故约束加在 ``ReferenceVideoUnit.duration_seconds`` 这个
    派生字段上：``Literal[*supported_durations]`` 在 response_schema 里渲染为 ``enum``（单值时为 ``const``，LLM 可见），
    叠加 ``ReferenceVideoUnit`` 既有的 ``duration_seconds == sum(shots)`` 一致性校验器，等价于强制
    「各 shot 之和 ∈ supported_durations」。``Shot.duration`` 仍保留 1-15 的合理性上限、不要求单 shot 成员。
    """
    unit = _constrained_duration_item(
        ReferenceVideoUnit,
        _duration_literal(supported_durations),
        "所有 shot 时长之和，必须取 supported_durations 中的值",
    )
    return create_model(
        "ReferenceVideoScript",
        __base__=ReferenceVideoScript,
        video_units=(list[unit], Field(description="视频单元列表")),
    )
