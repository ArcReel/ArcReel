# Generate Script 实现方案

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 使用 Gemini-3-Flash-Preview 生成 JSON 剧本，替代现有 Agent 流程的 Step 3

**Architecture:** 核心逻辑在 `lib/script_generator.py`，CLI 入口在 `.claude/skills/generate-script/scripts/generate_script.py`。使用 Pydantic 定义数据模型并验证输出，借鉴 Storycraft 的 Prompt 工程技巧。

**Tech Stack:** Python 3.10+, Pydantic, google-genai SDK

**Design Doc:** `docs/plans/2026-02-05-generate-script-design.md`

---

## Task 1: Pydantic 模型定义

**Files:**
- Create: `lib/script_models.py`

**Step 1: 创建共享模型文件**

```python
"""
script_models.py - 剧本数据模型

使用 Pydantic 定义剧本的数据结构，用于：
1. Gemini API 的 response_schema（Structured Outputs）
2. 输出验证
"""

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


# ============ 说书模式（Narration） ============

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


class NovelInfo(BaseModel):
    """小说来源信息"""
    title: str = Field(description="小说标题")
    chapter: str = Field(description="章节名称")
    source_file: str = Field(description="源文件路径")


class NarrationEpisodeScript(BaseModel):
    """说书模式剧集脚本"""
    episode: int = Field(description="剧集编号")
    title: str = Field(description="剧集标题")
    content_mode: Literal["narration"] = Field(default="narration", description="内容模式")
    duration_seconds: int = Field(default=0, description="总时长（秒）")
    summary: str = Field(description="剧集摘要")
    novel: NovelInfo = Field(description="小说来源信息")
    characters_in_episode: List[str] = Field(description="本集出场角色列表")
    clues_in_episode: List[str] = Field(description="本集出场线索列表")
    segments: List[NarrationSegment] = Field(description="片段列表")


# ============ 剧集动画模式（Drama） ============

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
    duration_seconds: int = Field(default=0, description="总时长（秒）")
    summary: str = Field(description="剧集摘要")
    novel: NovelInfo = Field(description="小说来源信息")
    characters_in_episode: List[str] = Field(description="本集出场角色列表")
    clues_in_episode: List[str] = Field(description="本集出场线索列表")
    scenes: List[DramaScene] = Field(description="场景列表")
```

**Step 2: 验证模型可以生成 JSON Schema**

Run: `cd /Users/pollochen/Documents/ai-anime/.worktrees/feature-generate-script && python -c "from lib.script_models import NarrationEpisodeScript; print(NarrationEpisodeScript.model_json_schema())"`

Expected: 输出 JSON Schema，无错误

**Step 3: Commit**

```bash
git add lib/script_models.py
git commit -m "feat: add Pydantic models for script generation"
```

---

## Task 2: Prompt 构建函数

**Files:**
- Create: `lib/prompt_builders_script.py`

**Step 1: 创建 Prompt 构建模块**

```python
"""
prompt_builders_script.py - 剧本生成 Prompt 构建器

借鉴 Storycraft 的 Prompt 工程技巧：
1. XML 标签分隔上下文
2. 明确的字段描述和约束
3. 可选值列表约束输出
"""

from typing import Dict, List


def _format_character_names(characters: Dict) -> str:
    """格式化角色列表"""
    lines = []
    for name in characters.keys():
        lines.append(f"- {name}")
    return "\n".join(lines)


def _format_clue_names(clues: Dict) -> str:
    """格式化线索列表"""
    lines = []
    for name in clues.keys():
        lines.append(f"- {name}")
    return "\n".join(lines)


def build_narration_prompt(
    project_overview: Dict,
    style: str,
    style_description: str,
    characters: Dict,
    clues: Dict,
    segments_md: str,
) -> str:
    """
    构建说书模式的 Prompt
    
    Args:
        project_overview: 项目概述（synopsis, genre, theme, world_setting）
        style: 视觉风格标签
        style_description: 风格描述
        characters: 角色字典（仅用于提取名称列表）
        clues: 线索字典（仅用于提取名称列表）
        segments_md: Step 1 的 Markdown 内容
        
    Returns:
        构建好的 Prompt 字符串
    """
    character_names = list(characters.keys())
    clue_names = list(clues.keys())
    
    prompt = f"""你的任务是为短视频生成分镜剧本。请仔细遵循以下指示：

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


def build_drama_prompt(
    project_overview: Dict,
    style: str,
    style_description: str,
    characters: Dict,
    clues: Dict,
    scenes_md: str,
) -> str:
    """
    构建剧集动画模式的 Prompt
    
    Args:
        project_overview: 项目概述
        style: 视觉风格标签
        style_description: 风格描述
        characters: 角色字典
        clues: 线索字典
        scenes_md: Step 1 的 Markdown 内容
        
    Returns:
        构建好的 Prompt 字符串
    """
    character_names = list(characters.keys())
    clue_names = list(clues.keys())
    
    prompt = f"""你的任务是为剧集动画生成分镜剧本。请仔细遵循以下指示：

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

**Step 2: 验证 Prompt 构建函数**

Run: `cd /Users/pollochen/Documents/ai-anime/.worktrees/feature-generate-script && python -c "from lib.prompt_builders_script import build_narration_prompt; print(build_narration_prompt({}, 'test', 'test', {'角色A': {}}, {'线索A': {}}, 'test')[:200])"`

Expected: 输出 Prompt 前 200 字符，无错误

**Step 3: Commit**

```bash
git add lib/prompt_builders_script.py
git commit -m "feat: add prompt builders for script generation"
```

---

## Task 3: ScriptGenerator 核心类

**Files:**
- Create: `lib/script_generator.py`

**Step 1: 创建 ScriptGenerator 类**

```python
"""
script_generator.py - 剧本生成器

读取 Step 1/2 的 Markdown 中间文件，调用 Gemini 生成最终 JSON 剧本
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

from pydantic import ValidationError

from lib.gemini_client import GeminiClient
from lib.script_models import (
    NarrationEpisodeScript,
    DramaEpisodeScript,
)
from lib.prompt_builders_script import (
    build_narration_prompt,
    build_drama_prompt,
)


class ScriptGenerator:
    """
    剧本生成器
    
    读取 Step 1/2 的 Markdown 中间文件，调用 Gemini 生成最终 JSON 剧本
    """
    
    MODEL = "gemini-2.5-flash-preview-05-20"
    
    def __init__(self, project_path: Union[str, Path]):
        """
        初始化生成器
        
        Args:
            project_path: 项目目录路径，如 projects/test0205
        """
        self.project_path = Path(project_path)
        self.client = GeminiClient()
        
        # 加载 project.json
        self.project_json = self._load_project_json()
        self.content_mode = self.project_json.get('content_mode', 'narration')
    
    def generate(
        self,
        episode: int,
        output_path: Optional[Path] = None,
    ) -> Path:
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
        
        # 2. 提取角色和线索（从 project.json）
        characters = self.project_json.get('characters', {})
        clues = self.project_json.get('clues', {})
        
        # 3. 构建 Prompt
        if self.content_mode == 'narration':
            prompt = build_narration_prompt(
                project_overview=self.project_json.get('overview', {}),
                style=self.project_json.get('style', ''),
                style_description=self.project_json.get('style_description', ''),
                characters=characters,
                clues=clues,
                segments_md=step1_md,
            )
            schema = NarrationEpisodeScript.model_json_schema()
        else:
            prompt = build_drama_prompt(
                project_overview=self.project_json.get('overview', {}),
                style=self.project_json.get('style', ''),
                style_description=self.project_json.get('style_description', ''),
                characters=characters,
                clues=clues,
                scenes_md=step1_md,
            )
            schema = DramaEpisodeScript.model_json_schema()
        
        # 4. 调用 Gemini API
        print(f"📝 正在生成第 {episode} 集剧本...")
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
        if output_path is None:
            output_path = self.project_path / 'scripts' / f'episode_{episode}.json'
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(script_data, f, ensure_ascii=False, indent=2)
        
        print(f"✓ 剧本已保存至 {output_path}")
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
        characters = self.project_json.get('characters', {})
        clues = self.project_json.get('clues', {})
        
        if self.content_mode == 'narration':
            return build_narration_prompt(
                project_overview=self.project_json.get('overview', {}),
                style=self.project_json.get('style', ''),
                style_description=self.project_json.get('style_description', ''),
                characters=characters,
                clues=clues,
                segments_md=step1_md,
            )
        else:
            return build_drama_prompt(
                project_overview=self.project_json.get('overview', {}),
                style=self.project_json.get('style', ''),
                style_description=self.project_json.get('style_description', ''),
                characters=characters,
                clues=clues,
                scenes_md=step1_md,
            )
    
    def _load_project_json(self) -> dict:
        """加载 project.json"""
        path = self.project_path / 'project.json'
        if not path.exists():
            raise FileNotFoundError(f"未找到 project.json: {path}")
        
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def _load_step1(self, episode: int) -> str:
        """加载 Step 1 的 Markdown 文件"""
        path = self.project_path / 'drafts' / f'episode_{episode}' / 'step1_segments.md'
        if not path.exists():
            raise FileNotFoundError(f"未找到 Step 1 文件: {path}")
        
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    
    def _parse_response(self, response_text: str, episode: int) -> dict:
        """
        解析并验证 Gemini 响应
        
        Args:
            response_text: API 返回的 JSON 文本
            episode: 剧集编号
            
        Returns:
            验证后的剧本数据字典
        """
        # 清理可能的 markdown 包装
        text = response_text.strip()
        if text.startswith('```json'):
            text = text[7:]
        if text.startswith('```'):
            text = text[3:]
        if text.endswith('```'):
            text = text[:-3]
        text = text.strip()
        
        # 解析 JSON
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON 解析失败: {e}")
        
        # Pydantic 验证
        try:
            if self.content_mode == 'narration':
                validated = NarrationEpisodeScript.model_validate(data)
            else:
                validated = DramaEpisodeScript.model_validate(data)
            return validated.model_dump()
        except ValidationError as e:
            print(f"⚠️ 数据验证警告: {e}")
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
        script_data.setdefault('episode', episode)
        script_data.setdefault('content_mode', self.content_mode)
        
        # 添加小说信息
        if 'novel' not in script_data:
            script_data['novel'] = {
                'title': self.project_json.get('title', ''),
                'chapter': f'第{episode}集',
                'source_file': '',
            }
        
        # 添加时间戳
        now = datetime.now().isoformat()
        script_data.setdefault('metadata', {})
        script_data['metadata']['created_at'] = now
        script_data['metadata']['updated_at'] = now
        script_data['metadata']['generator'] = self.MODEL
        
        # 计算统计信息
        if self.content_mode == 'narration':
            segments = script_data.get('segments', [])
            script_data['metadata']['total_segments'] = len(segments)
            script_data['duration_seconds'] = sum(
                s.get('duration_seconds', 4) for s in segments
            )
        else:
            scenes = script_data.get('scenes', [])
            script_data['metadata']['total_scenes'] = len(scenes)
            script_data['duration_seconds'] = sum(
                s.get('duration_seconds', 8) for s in scenes
            )
        
        return script_data
```

**Step 2: 验证 ScriptGenerator 初始化**

Run: `cd /Users/pollochen/Documents/ai-anime/.worktrees/feature-generate-script && python -c "from lib.script_generator import ScriptGenerator; g = ScriptGenerator('projects/test0205'); print(g.content_mode)"`

Expected: 输出 `narration`

**Step 3: Commit**

```bash
git add lib/script_generator.py
git commit -m "feat: add ScriptGenerator class"
```

---

## Task 4: CLI 入口脚本

**Files:**
- Create: `.claude/skills/generate-script/scripts/generate_script.py`

**Step 1: 创建目录结构**

Run: `mkdir -p /Users/pollochen/Documents/ai-anime/.worktrees/feature-generate-script/.claude/skills/generate-script/scripts`

**Step 2: 创建 CLI 脚本**

```python
#!/usr/bin/env python3
"""
generate_script.py - 使用 Gemini 生成 JSON 剧本

用法:
    python generate_script.py <project_name> --episode <N>
    python generate_script.py <project_name> --episode <N> --output <path>
    python generate_script.py <project_name> --episode <N> --dry-run
    
示例:
    python generate_script.py test0205 --episode 1
    python generate_script.py 赡养人类 --episode 1 --output scripts/ep1.json
"""

import argparse
import sys
from pathlib import Path

# 添加项目根目录到路径
PROJECT_ROOT = Path(__file__).resolve().parents[4]  # .claude/skills/generate-script/scripts -> root
sys.path.insert(0, str(PROJECT_ROOT))

from lib.script_generator import ScriptGenerator


def main():
    parser = argparse.ArgumentParser(
        description='使用 Gemini 生成 JSON 剧本',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    %(prog)s test0205 --episode 1
    %(prog)s 赡养人类 --episode 1 --output scripts/ep1.json
    %(prog)s test0205 --episode 1 --dry-run
        """
    )
    
    parser.add_argument(
        'project',
        type=str,
        help='项目名称（projects/ 下的目录名）'
    )
    
    parser.add_argument(
        '--episode', '-e',
        type=int,
        required=True,
        help='剧集编号'
    )
    
    parser.add_argument(
        '--output', '-o',
        type=str,
        default=None,
        help='输出文件路径（默认: scripts/episode_N.json）'
    )
    
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='仅显示 Prompt，不实际调用 API'
    )
    
    args = parser.parse_args()
    
    # 构建项目路径
    project_path = PROJECT_ROOT / 'projects' / args.project
    
    if not project_path.exists():
        print(f"❌ 项目不存在: {project_path}")
        sys.exit(1)
    
    # 检查中间文件是否存在
    drafts_path = project_path / 'drafts' / f'episode_{args.episode}'
    step1_path = drafts_path / 'step1_segments.md'
    
    if not step1_path.exists():
        print(f"❌ 未找到 Step 1 文件: {step1_path}")
        print("   请先完成片段拆分（Step 1）")
        sys.exit(1)
    
    try:
        generator = ScriptGenerator(project_path)
        
        if args.dry_run:
            # 仅显示 Prompt
            print("=" * 60)
            print("DRY RUN - 以下是将发送给 Gemini 的 Prompt:")
            print("=" * 60)
            prompt = generator.build_prompt(args.episode)
            print(prompt)
            print("=" * 60)
            return
        
        # 实际生成
        output_path = Path(args.output) if args.output else None
        result_path = generator.generate(
            episode=args.episode,
            output_path=output_path,
        )
        
        print(f"\n✅ 剧本生成完成: {result_path}")
        
    except FileNotFoundError as e:
        print(f"❌ 文件错误: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ 生成失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
```

**Step 3: 添加执行权限**

Run: `chmod +x /Users/pollochen/Documents/ai-anime/.worktrees/feature-generate-script/.claude/skills/generate-script/scripts/generate_script.py`

**Step 4: 验证 CLI 帮助信息**

Run: `cd /Users/pollochen/Documents/ai-anime/.worktrees/feature-generate-script && python .claude/skills/generate-script/scripts/generate_script.py --help`

Expected: 显示帮助信息

**Step 5: Commit**

```bash
git add .claude/skills/generate-script/scripts/generate_script.py
git commit -m "feat: add CLI entry point for script generation"
```

---

## Task 5: SKILL.md

**Files:**
- Create: `.claude/skills/generate-script/SKILL.md`

**Step 1: 创建 SKILL.md**

```markdown
---
name: generate-script
description: 使用 Gemini API 生成 JSON 剧本。使用场景：(1) 用户运行 /generate-script 命令，(2) 已完成 Step 1/2 需要生成最终剧本，(3) 用户想用 Gemini 替代 Claude 生成剧本。读取 step1_segments.md 和 project.json，调用 gemini-2.5-flash-preview-05-20 生成符合 Pydantic 模型的 JSON 剧本。
---

# generate-script

使用 Gemini API 生成 JSON 剧本，替代现有 Agent 流程的 Step 3。

## 前置条件

1. 项目目录下存在 `project.json`（包含 style、overview、characters、clues）
2. 已完成 Step 1：`drafts/episode_N/step1_segments.md`
3. 已完成 Step 2：角色和线索已写入 `project.json`

## 用法

```bash
# 生成指定剧集的剧本
python .claude/skills/generate-script/scripts/generate_script.py <project> --episode <N>

# 指定输出路径
python .claude/skills/generate-script/scripts/generate_script.py <project> --episode <N> --output <path>

# 预览 Prompt（不实际调用 API）
python .claude/skills/generate-script/scripts/generate_script.py <project> --episode <N> --dry-run
```

## 示例

```bash
# 生成 test0205 项目第 1 集的剧本
python .claude/skills/generate-script/scripts/generate_script.py test0205 --episode 1

# 预览将发送给 Gemini 的 Prompt
python .claude/skills/generate-script/scripts/generate_script.py test0205 --episode 1 --dry-run
```

## 输出

生成的 JSON 文件保存至 `projects/<project>/scripts/episode_N.json`

## 支持的模式

- **narration**（说书模式）：9:16 竖屏，保留原文到 novel_text
- **drama**（剧集动画模式）：16:9 横屏，场景改编
```

**Step 2: Commit**

```bash
git add .claude/skills/generate-script/SKILL.md
git commit -m "feat: add SKILL.md for generate-script"
```

---

## Task 6: 集成测试

**Files:**
- Test: `projects/test0205`

**Step 1: 运行 dry-run 测试**

Run: `cd /Users/pollochen/Documents/ai-anime/.worktrees/feature-generate-script && python .claude/skills/generate-script/scripts/generate_script.py test0205 --episode 1 --dry-run`

Expected: 显示完整的 Prompt，包含 overview、style、characters、clues、segments

**Step 2: 运行实际生成测试**

Run: `cd /Users/pollochen/Documents/ai-anime/.worktrees/feature-generate-script && python .claude/skills/generate-script/scripts/generate_script.py test0205 --episode 1`

Expected: 生成 `projects/test0205/scripts/episode_1.json`，包含所有片段

**Step 3: 验证生成的 JSON 结构**

Run: `cd /Users/pollochen/Documents/ai-anime/.worktrees/feature-generate-script && python -c "import json; d=json.load(open('projects/test0205/scripts/episode_1.json')); print(f'segments: {len(d.get(\"segments\", []))}'); print(f'mode: {d.get(\"content_mode\")}')"` 

Expected: 显示片段数量和模式

**Step 4: 最终 Commit**

```bash
git add -A
git commit -m "test: verify script generation with test0205"
```

---

## 完成检查清单

- [ ] Task 1: Pydantic 模型定义 (`lib/script_models.py`)
- [ ] Task 2: Prompt 构建函数 (`lib/prompt_builders_script.py`)
- [ ] Task 3: ScriptGenerator 核心类 (`lib/script_generator.py`)
- [ ] Task 4: CLI 入口脚本 (`.claude/skills/generate-script/scripts/generate_script.py`)
- [ ] Task 5: SKILL.md (`.claude/skills/generate-script/SKILL.md`)
- [ ] Task 6: 集成测试
