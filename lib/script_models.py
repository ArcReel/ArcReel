"""
script_models.py - 剧本数据模型

使用 Pydantic 定义剧本的数据结构，用于：
1. Gemini API 的 response_schema（Structured Outputs）
2. 输出验证
"""

from typing import Literal

from pydantic import BaseModel, Field

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
    video_clip: str | None = Field(default=None, description="视频片段路径")
    video_uri: str | None = Field(default=None, description="视频 URI")
    status: Literal["pending", "storyboard_ready", "completed"] = Field(default="pending", description="生成状态")


# ============ 说书模式（Narration） ============


class NarrationSegment(BaseModel):
    """说书模式的片段"""

    segment_id: str = Field(description="片段 ID，格式 E{集}S{序号} 或 E{集}S{序号}_{子序号}")
    episode: int = Field(description="所属剧集")
    duration_seconds: Literal[4, 6, 8] = Field(description="片段时长（秒）")
    segment_break: bool = Field(default=False, description="是否为场景切换点")
    novel_text: str = Field(description="小说原文（必须原样保留，用于后期配音）")
    characters_in_segment: list[str] = Field(description="出场角色名称列表")
    clues_in_segment: list[str] = Field(default_factory=list, description="出场线索名称列表")
    image_prompt: ImagePrompt = Field(description="分镜图生成提示词")
    video_prompt: VideoPrompt = Field(description="视频生成提示词")
    transition_to_next: Literal["cut", "fade", "dissolve"] = Field(default="cut", description="转场类型")
    note: str | None = Field(default=None, description="用户备注（不参与生成）")
    generated_assets: GeneratedAssets = Field(default_factory=GeneratedAssets, description="生成资源状态")


class NovelInfo(BaseModel):
    """小说来源信息"""

    title: str = Field(description="小说标题")
    chapter: str = Field(description="章节名称")


class NarrationEpisodeScript(BaseModel):
    """说书模式剧集脚本"""

    episode: int = Field(description="剧集编号")
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
    duration_seconds: Literal[4, 6, 8] = Field(default=8, description="场景时长（秒）")
    segment_break: bool = Field(default=False, description="是否为场景切换点")
    scene_type: str = Field(default="剧情", description="场景类型")
    characters_in_scene: list[str] = Field(description="出场角色名称列表")
    clues_in_scene: list[str] = Field(default_factory=list, description="出场线索名称列表")
    image_prompt: ImagePrompt = Field(description="分镜图生成提示词（16:9 横屏）")
    video_prompt: VideoPrompt = Field(description="视频生成提示词")
    transition_to_next: Literal["cut", "fade", "dissolve"] = Field(default="cut", description="转场类型")
    note: str | None = Field(default=None, description="用户备注（不参与生成）")
    generated_assets: GeneratedAssets = Field(default_factory=GeneratedAssets, description="生成资源状态")


class DramaEpisodeScript(BaseModel):
    """剧集动画模式剧集脚本"""

    episode: int = Field(description="剧集编号")
    title: str = Field(description="剧集标题")
    content_mode: Literal["drama"] = Field(default="drama", description="内容模式")
    duration_seconds: int = Field(default=0, description="总时长（秒）")
    summary: str = Field(description="剧集摘要")
    novel: NovelInfo = Field(description="小说来源信息")
    scenes: list[DramaScene] = Field(description="场景列表")
