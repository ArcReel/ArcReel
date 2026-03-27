# 设计文档：使用 Gemini 生成 JSON 剧本

## 概述

创建一个脚本，使用 `gemini-3-flash-preview` 生成 JSON 剧本，替代现有 Agent 流程的最后一步（Step 3）。

### 背景

现有的 `novel-to-narration-script` 和 `novel-to-storyboard-script` Agent 使用三步流程：
1. **Step 1**: 拆分片段/场景（输出 `step1_segments.md`）
2. **Step 2**: 角色表/线索表（输出 `step2_character_clue_tables.md`）
3. **Step 3**: 生成 JSON 剧本 ← **本脚本替代此步骤**

### 目标

- 使用 Gemini-3-Flash-Preview 生成 JSON 剧本
- 借鉴 Storycraft 的 Prompt 工程技巧
- 使用 Pydantic 确保输出格式符合规范
- 支持说书模式（narration）和剧集动画模式（drama）

---

## 架构设计

### 文件结构

```
lib/
├── script_generator.py      # 核心逻辑：Prompt 构建 + Gemini 调用 + Pydantic 模型
├── gemini_client.py         # 现有：已有 generate_text() 方法
└── ...

.claude/skills/
└── generate-script/
    ├── SKILL.md             # Skill 说明文档（使用 skill-creator 创建）
    └── scripts/
        └── generate_script.py  # CLI 入口
```

### 数据流

```
step1_segments.md + step2_character_clue_tables.md + project.json
                            ↓
                    ScriptGenerator
                            ↓
                    构建 Prompt
                            ↓
              Gemini API (gemini-3-flash-preview)
                            ↓
                    Pydantic 验证
                            ↓
                scripts/episode_N.json
```

---

## Pydantic 模型定义

### 共享模型

```python
from pydantic import BaseModel, Field
from typing import List, Optional, Literal

class Dialogue(BaseModel):
    """对话条目"""
    speaker: str = Field(description="说话人名称")
    line: str = Field(description="对话内容")

class Composition(BaseModel):
    """构图信息"""
    shot_type: str = Field(description="镜头类型，如 Medium Shot, Close-up, Long Shot")
    lighting: str = Field(description="光线描述，包含光源、方向和氛围")
    ambiance: str = Field(description="整体氛围，与情绪基调匹配")

class ImagePrompt(BaseModel):
    """分镜图生成 Prompt"""
    scene: str = Field(description="场景描述：角色位置、表情、动作、环境细节")
    composition: Composition = Field(description="构图信息")

class VideoPrompt(BaseModel):
    """视频生成 Prompt"""
    action: str = Field(description="动作描述：角色在该片段内的具体动作")
    camera_motion: str = Field(description="镜头运动：Static, Pan Left/Right, Zoom In/Out, Tracking Shot 等")
    ambiance_audio: str = Field(description="环境音效：仅描述场景内的声音，禁止 BGM")
    dialogue: List[Dialogue] = Field(default_factory=list, description="对话列表，仅当原文有引号对话时填写")

class GeneratedAssets(BaseModel):
    """生成资源状态（初始化为空）"""
    storyboard_image: Optional[str] = Field(default=None, description="分镜图路径")
    video_clip: Optional[str] = Field(default=None, description="视频片段路径")
    video_uri: Optional[str] = Field(default=None, description="视频 URI")
    status: Literal["pending", "storyboard_ready", "completed"] = Field(default="pending", description="生成状态")
```

### 说书模式（Narration）

```python
class NarrationSegment(BaseModel):
    """说书模式的片段"""
    segment_id: str = Field(description="片段 ID，格式 E{集}S{序号}")
    episode: int = Field(description="所属剧集")
    duration_seconds: Literal[4, 6, 8] = Field(description="片段时长（秒）")
    segment_break: bool = Field(default=False, description="是否为场景切换点")
    novel_text: str = Field(description="小说原文（必须原样保留，用于后期配音）")
    characters_in_segment: List[str] = Field(description="出场角色名称列表")
    clues_in_segment: List[str] = Field(default_factory=list, description="出场线索名称列表")
    image_prompt: ImagePrompt = Field(description="分镜图生成提示词")
    video_prompt: VideoPrompt = Field(description="视频生成提示词")
    transition_to_next: Literal["cut", "fade", "dissolve"] = Field(default="cut", description="转场类型")
    generated_assets: GeneratedAssets = Field(default_factory=GeneratedAssets, description="生成资源状态")

class NarrationEpisodeScript(BaseModel):
    """说书模式剧集脚本"""
    episode: int = Field(description="剧集编号")
    title: str = Field(description="剧集标题")
    content_mode: Literal["narration"] = Field(default="narration", description="内容模式")
    duration_seconds: int = Field(default=0, description="总时长（秒）")
    summary: str = Field(description="剧集摘要")
    novel: dict = Field(description="小说来源信息")
    characters_in_episode: List[str] = Field(description="本集出场角色列表")
    clues_in_episode: List[str] = Field(description="本集出场线索列表")
    segments: List[NarrationSegment] = Field(description="片段列表")
```

### 剧集动画模式（Drama）

```python
class DramaScene(BaseModel):
    """剧集动画模式的场景"""
    scene_id: str = Field(description="场景 ID，格式 E{集}S{序号}")
    duration_seconds: Literal[4, 6, 8] = Field(default=8, description="场景时长（秒）")
    segment_break: bool = Field(default=False, description="是否为场景切换点")
    scene_type: str = Field(default="剧情", description="场景类型")
    characters_in_scene: List[str] = Field(description="出场角色名称列表")
    clues_in_scene: List[str] = Field(default_factory=list, description="出场线索名称列表")
    image_prompt: ImagePrompt = Field(description="分镜图生成提示词（16:9 横屏）")
    video_prompt: VideoPrompt = Field(description="视频生成提示词")
    transition_to_next: Literal["cut", "fade", "dissolve"] = Field(default="cut", description="转场类型")
    generated_assets: GeneratedAssets = Field(default_factory=GeneratedAssets, description="生成资源状态")

class DramaEpisodeScript(BaseModel):
    """剧集动画模式剧集脚本"""
    episode: int = Field(description="剧集编号")
    title: str = Field(description="剧集标题")
    content_mode: Literal["drama"] = Field(default="drama", description="内容模式")
    summary: str = Field(description="剧集摘要")
    novel: dict = Field(description="小说来源信息")
    characters_in_episode: List[str] = Field(description="本集出场角色列表")
    clues_in_episode: List[str] = Field(description="本集出场线索列表")
    scenes: List[DramaScene] = Field(description="场景列表")
```

---

## Prompt 设计

借鉴 Storycraft 的 Prompt 工程技巧：
1. XML 标签分隔上下文
2. 明确的字段描述和约束
3. 可选值列表约束输出

### 说书模式 Prompt

```python
def build_narration_prompt(
    project_overview: dict,
    style: str,
    style_description: str,
    characters: dict,
    clues: dict,
    segments_md: str,
) -> str:
    character_names = list(characters.keys())
    clue_names = list(clues.keys())
    
    prompt = f"""
你的任务是为短视频生成分镜剧本。请仔细遵循以下指示：

1. 你将获得故事概述、视觉风格、角色列表、线索列表，以及已拆分的小说片段。

2. 为每个片段生成：
   - image_prompt：第一帧的图像生成提示词
   - video_prompt：动作和音效的视频生成提示词

<overview>
{project_overview.get('synopsis', '')}

题材类型：{project_overview.get('genre', '')}
核心主题：{project_overview.get('theme', '')}
世界观设定：{project_overview.get('world_setting', '')}
</overview>

<style>
风格：{style}
描述：{style_description}
</style>

<characters>
{_format_character_names(characters)}
</characters>

<clues>
{_format_clue_names(clues)}
</clues>

<segments>
{segments_md}
</segments>

segments 为片段拆分表，每行是一个片段，包含：
- 片段 ID：格式为 E{{集数}}S{{序号}}
- 小说原文：必须原样保留到 novel_text 字段
- 时长：4、6 或 8 秒
- 是否有对话：用于判断是否需要填写 video_prompt.dialogue
- 是否为 segment_break：场景切换点，需设置 segment_break 为 true

3. 为每个片段生成时，遵循以下规则：

a. **novel_text**：原样复制小说原文，不做任何修改。

b. **characters_in_segment**：列出本片段中出场的角色名称。
   - 可选值：[{', '.join(character_names)}]
   - 仅包含明确提及或明显暗示的角色

c. **clues_in_segment**：列出本片段中涉及的线索名称。
   - 可选值：[{', '.join(clue_names)}]
   - 仅包含明确提及或明显暗示的线索

d. **image_prompt**：生成包含以下字段的对象：
   - scene：描述具体场景——角色位置、表情、动作、环境细节。要具体、可视化。一段话。
   - composition：
     - shot_type：镜头类型（Close-up、Medium Shot、Medium Long Shot、Long Shot 等）
     - lighting：描述光源、方向和氛围
     - ambiance：整体氛围，与情绪基调匹配

e. **video_prompt**：生成包含以下字段的对象：
   - action：精确描述该时长内发生的动作。具体描述运动细节。
   - camera_motion：Static、Pan Left、Pan Right、Tilt Up、Tilt Down、Zoom In、Zoom Out、Tracking Shot
   - ambiance_audio：仅描述场景内的声音。禁止出现音乐或 BGM。
   - dialogue：{{speaker, line}} 数组。仅当原文有引号对话时填写。

f. **segment_break**：如果在片段表中标记为"是"，则设为 true。

g. **duration_seconds**：使用片段表中的时长（4、6 或 8）。

h. **transition_to_next**：默认为 "cut"。

4. 输出格式为包含所有片段的 JSON 数组。

目标：创建生动、视觉一致的分镜提示词，用于指导 AI 图像和视频生成。保持创意、具体，并忠于原文。
"""
    return prompt
```

### 剧集动画模式 Prompt

```python
def build_drama_prompt(
    project_overview: dict,
    style: str,
    style_description: str,
    characters: dict,
    clues: dict,
    scenes_md: str,
) -> str:
    character_names = list(characters.keys())
    clue_names = list(clues.keys())
    
    prompt = f"""
你的任务是为剧集动画生成分镜剧本。请仔细遵循以下指示：

1. 你将获得故事概述、视觉风格、角色列表、线索列表，以及已拆分的场景列表。

2. 为每个场景生成：
   - image_prompt：第一帧的图像生成提示词
   - video_prompt：动作和音效的视频生成提示词

<overview>
{project_overview.get('synopsis', '')}

题材类型：{project_overview.get('genre', '')}
核心主题：{project_overview.get('theme', '')}
世界观设定：{project_overview.get('world_setting', '')}
</overview>

<style>
风格：{style}
描述：{style_description}
</style>

<characters>
{_format_character_names(characters)}
</characters>

<clues>
{_format_clue_names(clues)}
</clues>

<scenes>
{scenes_md}
</scenes>

scenes 为场景拆分表，每行是一个场景，包含：
- 场景 ID：格式为 E{{集数}}S{{序号}}
- 场景描述：剧本改编后的场景内容
- 时长：4、6 或 8 秒（默认 8 秒）
- 场景类型：剧情、动作、对话等
- 是否为 segment_break：场景切换点，需设置 segment_break 为 true

3. 为每个场景生成时，遵循以下规则：

a. **characters_in_scene**：列出本场景中出场的角色名称。
   - 可选值：[{', '.join(character_names)}]
   - 仅包含明确提及或明显暗示的角色

b. **clues_in_scene**：列出本场景中涉及的线索名称。
   - 可选值：[{', '.join(clue_names)}]
   - 仅包含明确提及或明显暗示的线索

c. **image_prompt**：生成包含以下字段的对象：
   - scene：描述具体场景——角色位置、表情、动作、环境细节。要具体、可视化。一段话。16:9 横屏构图。
   - composition：
     - shot_type：镜头类型（Close-up、Medium Shot、Medium Long Shot、Long Shot 等）
     - lighting：描述光源、方向和氛围
     - ambiance：整体氛围，与情绪基调匹配

d. **video_prompt**：生成包含以下字段的对象：
   - action：精确描述该时长内发生的动作。具体描述运动细节。
   - camera_motion：Static、Pan Left、Pan Right、Tilt Up、Tilt Down、Zoom In、Zoom Out、Tracking Shot
   - ambiance_audio：仅描述场景内的声音。禁止出现音乐或 BGM。
   - dialogue：{{speaker, line}} 数组。包含角色对话。

e. **segment_break**：如果在场景表中标记为"是"，则设为 true。

f. **duration_seconds**：使用场景表中的时长（4、6 或 8），默认为 8。

g. **scene_type**：使用场景表中的场景类型，默认为"剧情"。

h. **transition_to_next**：默认为 "cut"。

4. 输出格式为包含所有场景的 JSON 数组。

目标：创建生动、视觉一致的分镜提示词，用于指导 AI 图像和视频生成。保持创意、具体，适合 16:9 横屏动画呈现。
"""
    return prompt
```

---

## ScriptGenerator 类

```python
class ScriptGenerator:
    """
    剧本生成器
    
    读取 Step 1/2 的 Markdown 中间文件，调用 Gemini 生成最终 JSON 剧本
    """
    
    MODEL = "gemini-3-flash-preview"
    
    def __init__(self, project_path: Union[str, Path]):
        """
        初始化生成器
        
        Args:
            project_path: 项目目录路径，如 projects/test0205
        """
        self.project_path = Path(project_path)
        self.client = GeminiClient()
        self.project_json = self._load_project_json()
        self.content_mode = self.project_json.get('content_mode', 'narration')
    
    def generate(self, episode: int, output_path: Optional[Path] = None) -> Path:
        """
        生成剧集剧本
        
        Args:
            episode: 剧集编号
            output_path: 输出路径，默认为 scripts/episode_{episode}.json
            
        Returns:
            生成的 JSON 文件路径
        """
        # 1. 加载中间文件
        step1_md = self._load_step1(episode)
        step2_md = self._load_step2(episode)
        
        # 2. 提取角色和线索
        characters = self.project_json.get('characters', {})
        clues = self.project_json.get('clues', {})
        
        # 3. 构建 Prompt
        if self.content_mode == 'narration':
            prompt = build_narration_prompt(...)
            schema = NarrationEpisodeScript.model_json_schema()
        else:
            prompt = build_drama_prompt(...)
            schema = DramaEpisodeScript.model_json_schema()
        
        # 4. 调用 Gemini API
        response_text = self.client.generate_text(
            prompt=prompt,
            model=self.MODEL,
            response_schema=schema,
        )
        
        # 5. 解析并验证响应
        script_data = self._parse_response(response_text, episode)
        
        # 6. 补充元数据
        script_data = self._add_metadata(script_data, episode)
        
        # 7. 保存文件
        # ...
```

---

## CLI 入口

```bash
# 生成指定剧集的剧本
python .claude/skills/generate-script/scripts/generate_script.py <project> --episode <N>

# 指定输出路径
python .claude/skills/generate-script/scripts/generate_script.py <project> --episode <N> --output <path>

# 预览 Prompt（不实际调用 API）
python .claude/skills/generate-script/scripts/generate_script.py <project> --episode <N> --dry-run
```

---

## 两种模式对比

| 维度 | Narration（说书模式） | Drama（剧集动画模式） |
|------|----------------------|---------------------|
| 数据单位 | segment（片段） | scene（场景） |
| 画面比例 | 9:16 竖屏 | 16:9 横屏 |
| 默认时长 | 4 秒 | 8 秒 |
| novel_text | 必须保留原文 | 无此字段 |
| dialogue | 仅当原文有对话 | 包含改编后的对话 |

---

## 实现计划

1. **创建 Skill**：使用 `skill-creator` 创建 `generate-script` skill
2. **实现核心逻辑**：`lib/script_generator.py`
   - Pydantic 模型定义
   - Prompt 构建函数
   - ScriptGenerator 类
3. **实现 CLI 入口**：`.claude/skills/generate-script/scripts/generate_script.py`
4. **测试验证**：使用现有项目 `test0205` 进行测试
