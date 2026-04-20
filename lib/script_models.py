"""
script_models.py - 剧本数据模型

使用 Pydantic 定义剧本的数据结构，用于：
1. Gemini API 的 response_schema（Structured Outputs）
2. 输出验证
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

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

CameraMotion = Literal[
    "Static",
    "Pan Left",
    "Pan Right",
    "Tilt Up",
    "Tilt Down",
    "Zoom In",
    "Zoom Out",
    "Tracking Shot",
]

TransitionType = Literal[
    "cut",
    "fade",
    "dissolve",
]


class Dialogue(BaseModel):
    """对话条目"""

    speaker: str = Field(description="说话人名称")
    line: str = Field(description="对话内容")


class Composition(BaseModel):
    """构图信息"""

    shot_type: ShotType = Field(description="镜头类型")
    lighting: str = Field(description="光线描述，包含光源、方向和氛围")
    ambiance: str = Field(description="整体氛围，与情绪基调匹配")


class ImagePrompt(BaseModel):
    """分镜图生成 Prompt"""

    scene: str = Field(description="场景描述：角色位置、表情、动作、环境细节")
    composition: Composition = Field(description="构图信息")


class VideoPrompt(BaseModel):
    """视频生成 Prompt"""

    action: str = Field(description="动作描述：角色在该片段内的具体动作")
    camera_motion: CameraMotion = Field(description="镜头运动")
    ambiance_audio: str = Field(description="环境音效：仅描述场景内的声音，禁止 BGM")
    dialogue: list[Dialogue] = Field(default_factory=list, description="对话列表，仅当原文有引号对话时填写")


class GeneratedAssets(BaseModel):
    """生成资源状态（初始化为空）"""

    storyboard_image: str | None = Field(default=None, description="分镜图路径")
    storyboard_last_image: str | None = Field(default=None, description="分镜图最后一帧路径")
    grid_id: str | None = Field(default=None, description="关联的网格图生成 ID")
    grid_cell_index: int | None = Field(default=None, description="在网格图中的单元格索引")
    video_clip: str | None = Field(default=None, description="视频片段路径")
    video_uri: str | None = Field(default=None, description="视频 URI")
    status: Literal["pending", "storyboard_ready", "completed"] = Field(default="pending", description="生成状态")


# ============ 说书模式（Narration） ============


class NarrationSegment(BaseModel):
    """说书模式的片段

    注意：不设独立 `episode` 字段。集号已经编码在 `segment_id`（格式 E{集}S{序号}）中，
    与 `DramaScene.scene_id` / `ReferenceVideoUnit.unit_id` 保持一致。避免 AI 在每个
    segment 上重复生成集号造成幻觉污染（详见 `NarrationEpisodeScript` docstring）。
    """

    segment_id: str = Field(description="片段 ID，格式 E{集}S{序号} 或 E{集}S{序号}_{子序号}")
    duration_seconds: int = Field(ge=1, le=60, description="片段时长（秒）")
    segment_break: bool = Field(default=False, description="是否为场景切换点")
    novel_text: str = Field(description="小说原文（必须原样保留，用于后期配音）")
    characters_in_segment: list[str] = Field(description="出场角色名称列表")
    scenes: list[str] = Field(default_factory=list, description="出场场景名称列表")
    props: list[str] = Field(default_factory=list, description="出场道具名称列表")
    image_prompt: ImagePrompt = Field(description="分镜图生成提示词")
    video_prompt: VideoPrompt = Field(description="视频生成提示词")
    transition_to_next: TransitionType = Field(default="cut", description="转场类型")
    note: str | None = Field(default=None, description="用户备注（不参与生成）")
    generated_assets: GeneratedAssets = Field(default_factory=GeneratedAssets, description="生成资源状态")


class NovelInfo(BaseModel):
    """小说来源信息"""

    title: str = Field(description="小说标题")
    chapter: str = Field(description="章节名称")


class NarrationEpisodeScript(BaseModel):
    """说书模式剧集脚本

    注意：`episode` 字段不在 schema 中。CLI 参数 `--episode N` 是集号的唯一真相源，
    由 `ScriptGenerator._add_metadata` 写入。不让 AI 生成该字段，避免幻觉写错集号
    进而污染 project.json（曾导致 episode_10.json 内部 episode=1 覆盖第 1 集条目）。
    """

    title: str = Field(description="剧集标题")
    content_mode: Literal["narration"] = Field(default="narration", description="内容模式")
    duration_seconds: int = Field(default=0, description="总时长（秒）")
    summary: str = Field(description="剧集摘要")
    novel: NovelInfo = Field(description="小说来源信息")
    segments: list[NarrationSegment] = Field(description="片段列表")


# ============ 剧集动画模式（Drama） ============


class DramaScene(BaseModel):
    """剧集动画模式的场景"""

    scene_id: str = Field(description="场景 ID，格式 E{集}S{序号} 或 E{集}S{序号}_{子序号}")
    duration_seconds: int = Field(default=8, ge=1, le=60, description="场景时长（秒）")
    segment_break: bool = Field(default=False, description="是否为场景切换点")
    scene_type: str = Field(default="剧情", description="场景类型")
    characters_in_scene: list[str] = Field(description="出场角色名称列表")
    scenes: list[str] = Field(default_factory=list, description="出场场景名称列表")
    props: list[str] = Field(default_factory=list, description="出场道具名称列表")
    image_prompt: ImagePrompt = Field(description="分镜图生成提示词")
    video_prompt: VideoPrompt = Field(description="视频生成提示词")
    transition_to_next: TransitionType = Field(default="cut", description="转场类型")
    note: str | None = Field(default=None, description="用户备注（不参与生成）")
    generated_assets: GeneratedAssets = Field(default_factory=GeneratedAssets, description="生成资源状态")


class DramaEpisodeScript(BaseModel):
    """剧集动画模式剧集脚本

    注意：`episode` 字段不在 schema 中，集号由 CLI 真相源通过 `_add_metadata` 写入。
    详见 `NarrationEpisodeScript` docstring。
    """

    title: str = Field(description="剧集标题")
    content_mode: Literal["drama"] = Field(default="drama", description="内容模式")
    duration_seconds: int = Field(default=0, description="总时长（秒）")
    summary: str = Field(description="剧集摘要")
    novel: NovelInfo = Field(description="小说来源信息")
    scenes: list[DramaScene] = Field(description="场景列表")


# ============ 参考生视频模式（Reference Video） ============


class Shot(BaseModel):
    """参考视频单元内的一个镜头。"""

    duration: int = Field(ge=1, le=15, description="该镜头时长（秒）")
    text: str = Field(description="镜头描述，可包含 @角色/@场景/@道具 引用")


class ReferenceResource(BaseModel):
    """参考图引用——只存名称 + 类型，具体路径从 project.json 对应 bucket 读时解析。"""

    type: Literal["character", "scene", "prop"] = Field(description="引用的资源类型")
    name: str = Field(description="角色/场景/道具名称，必须在 project.json 对应 bucket 中已注册")


class ReferenceVideoUnit(BaseModel):
    """参考视频单元——一个视频文件的最小生成粒度。"""

    unit_id: str = Field(description="格式 E{集}U{序号}")
    shots: list[Shot] = Field(min_length=1, max_length=4, description="1-4 个 shot")
    references: list[ReferenceResource] = Field(
        default_factory=list,
        description="按顺序决定 [图N] 编号",
    )
    duration_seconds: int = Field(description="派生字段：所有 shot 时长之和")
    duration_override: bool = Field(default=False, description="true 时停止自动派生")
    transition_to_next: TransitionType = Field(default="cut", description="转场类型")
    note: str | None = Field(default=None, description="用户备注")
    generated_assets: GeneratedAssets = Field(default_factory=GeneratedAssets, description="生成资源状态")

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
    详见 `NarrationEpisodeScript` docstring。
    """

    title: str = Field(description="剧集标题")
    content_mode: Literal["reference_video"] = Field(default="reference_video", description="内容模式")
    duration_seconds: int = Field(default=0, description="总时长（秒）")
    summary: str = Field(description="剧集摘要")
    novel: NovelInfo = Field(description="小说来源信息")
    video_units: list[ReferenceVideoUnit] = Field(description="视频单元列表")
