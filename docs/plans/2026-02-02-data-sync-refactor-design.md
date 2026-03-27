# 数据同步重构设计

## 概述

解决 `project.json` 和 `scripts/episode_N.json` 之间的数据同步问题，采用**混合模式**：
- **写时同步**：核心元数据在写入时自动同步
- **读时计算**：统计字段由 API 实时计算返回

## 问题分析

### 当前问题

1. **写入后不同步**：Agent 使用 Write 工具写入 `episode.json` 后，`project.json` 的 `episodes[]` 没有更新，导致 WebUI 无法显示剧集详情
2. **状态不实时**：进度信息是快照而非实时计算，容易过期

### 根本原因

- Agent 直接使用 Write 工具写 JSON，绕过了 `ProjectManager`
- 统计字段存储在 JSON 中而非实时计算
- 存在冗余的中间层字段（`characters_in_episode`、`clues_in_episode`）

## 架构设计

```
┌─────────────────────────────────────────────────────────────┐
│                        API Layer                             │
│  GET /projects/{name} ──► ProjectService.get_project()       │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                     API Router（修改）                       │
│  - 读取原始数据（ProjectManager）                            │
│  - 注入计算字段（StatusCalculator）                          │
│  - 返回完整响应                                              │
└─────────────────────────────────────────────────────────────┘
         │                              │
         ▼                              ▼
┌─────────────────────┐    ┌─────────────────────────────────┐
│   ProjectManager    │    │      StatusCalculator（新增）    │
│ - 只负责读写 JSON   │    │ - 计算 scenes_count             │
│ - 写时同步元数据    │    │ - 计算 progress.*               │
│ - 不再维护统计字段  │    │ - 计算 current_phase            │
└─────────────────────┘    │ - 计算 duration_seconds          │
                           └─────────────────────────────────┘
```

## 字段分类

### 写时同步字段

| 字段 | 位置 | 说明 |
|------|------|------|
| `episodes[].episode` | project.json | 集数 |
| `episodes[].title` | project.json | 标题，从 episode.json 同步 |
| `episodes[].script_file` | project.json | 剧本路径 |

### 读时计算字段

| 字段 | 位置 | 计算逻辑 |
|------|------|---------|
| `episodes[].scenes_count` | API 响应 | len(scenes/segments) |
| `episodes[].status` | API 响应 | 根据资源状态推断 |
| `status.progress.*` | API 响应 | 遍历资源实时统计 |
| `status.current_phase` | API 响应 | 基于 progress 推断 |
| `metadata.total_scenes` | API 响应 | len(scenes/segments) |
| `metadata.estimated_duration_seconds` | API 响应 | sum(duration_seconds) |

### 删除字段

| 字段 | 位置 | 删除理由 |
|------|------|---------|
| `characters_in_episode` | episode.json | 冗余，可从 scenes 聚合 |
| `clues_in_episode` | episode.json | 冗余，可从 scenes 聚合 |
| `duration_seconds`（顶层） | episode.json | 与 metadata 重复 |
| `status` 对象 | project.json | 改为读时计算 |

## 详细设计

### 1. 新增 `sync_episode_from_script()` 方法

```python
# lib/project_manager.py

def sync_episode_from_script(self, project_name: str, script_filename: str) -> Dict:
    """
    从剧本文件同步集数信息到 project.json

    Agent 写入剧本后必须调用此方法。

    Args:
        project_name: 项目名称
        script_filename: 剧本文件名（如 episode_1.json）

    Returns:
        更新后的 project 字典
    """
    script = self.load_script(project_name, script_filename)
    project = self.load_project(project_name)

    episode_num = script.get('episode', 1)
    episode_title = script.get('title', '')
    script_file = f"scripts/{script_filename}"

    # 查找或创建 episode 条目
    episodes = project.setdefault('episodes', [])
    episode_entry = next((ep for ep in episodes if ep['episode'] == episode_num), None)

    if episode_entry is None:
        episode_entry = {'episode': episode_num}
        episodes.append(episode_entry)

    # 同步核心元数据（不包含统计字段）
    episode_entry['title'] = episode_title
    episode_entry['script_file'] = script_file

    # 排序并保存
    episodes.sort(key=lambda x: x['episode'])
    self.save_project(project_name, project)

    print(f"✅ 已同步剧集信息: Episode {episode_num} - {episode_title}")
    return project
```

### 2. 修改 `save_script()` 方法

```python
# lib/project_manager.py

def save_script(self, project_name: str, script: Dict, filename: str) -> Path:
    # ... 现有保存逻辑 ...

    # 新增：自动同步到 project.json
    if self.project_exists(project_name):
        self.sync_episode_from_script(project_name, filename)

    return output_path
```

### 3. 新增 `StatusCalculator` 类

```python
# lib/status_calculator.py（新增文件）

from pathlib import Path
from typing import Dict, List, Any

from lib.project_manager import ProjectManager


class StatusCalculator:
    """状态和统计字段的实时计算器"""

    def __init__(self, project_manager: ProjectManager):
        self.pm = project_manager

    def calculate_episode_stats(self, project_name: str, script: Dict) -> Dict:
        """计算单个剧集的统计信息"""
        content_mode = script.get('content_mode', 'narration')
        items = script.get('segments' if content_mode == 'narration' else 'scenes', [])

        # 统计资源完成情况
        storyboard_done = sum(
            1 for i in items
            if i.get('generated_assets', {}).get('storyboard_image')
        )
        video_done = sum(
            1 for i in items
            if i.get('generated_assets', {}).get('video_clip')
        )
        total = len(items)

        # 计算状态
        if video_done == total and total > 0:
            status = 'completed'
        elif storyboard_done > 0 or video_done > 0:
            status = 'in_production'
        else:
            status = 'draft'

        return {
            'scenes_count': total,
            'status': status,
            'duration_seconds': sum(i.get('duration_seconds', 4) for i in items),
            'storyboards_completed': storyboard_done,
            'videos_completed': video_done
        }

    def calculate_project_progress(self, project_name: str) -> Dict:
        """计算项目整体进度（实时）"""
        project = self.pm.load_project(project_name)
        project_dir = self.pm.get_project_path(project_name)

        # 角色统计
        chars = project.get('characters', {})
        chars_total = len(chars)
        chars_done = sum(
            1 for c in chars.values()
            if c.get('character_sheet') and (project_dir / c['character_sheet']).exists()
        )

        # 线索统计
        clues = project.get('clues', {})
        clues_total = len([c for c in clues.values() if c.get('importance') == 'major'])
        clues_done = sum(
            1 for c in clues.values()
            if c.get('clue_sheet') and (project_dir / c['clue_sheet']).exists()
        )

        # 分镜/视频统计（遍历所有剧本）
        sb_total, sb_done, vid_total, vid_done = 0, 0, 0, 0

        for ep in project.get('episodes', []):
            script_file = ep.get('script_file', '').replace('scripts/', '')
            if script_file:
                try:
                    script = self.pm.load_script(project_name, script_file)
                    stats = self.calculate_episode_stats(project_name, script)
                    sb_total += stats['scenes_count']
                    vid_total += stats['scenes_count']
                    sb_done += stats['storyboards_completed']
                    vid_done += stats['videos_completed']
                except FileNotFoundError:
                    pass

        return {
            'characters': {'total': chars_total, 'completed': chars_done},
            'clues': {'total': clues_total, 'completed': clues_done},
            'storyboards': {'total': sb_total, 'completed': sb_done},
            'videos': {'total': vid_total, 'completed': vid_done}
        }

    def calculate_current_phase(self, progress: Dict) -> str:
        """根据进度推断当前阶段"""
        vid = progress.get('videos', {})
        sb = progress.get('storyboards', {})
        clues = progress.get('clues', {})
        chars = progress.get('characters', {})

        if vid.get('completed', 0) == vid.get('total', 0) and vid.get('total', 0) > 0:
            return 'compose'
        elif vid.get('completed', 0) > 0:
            return 'video'
        elif sb.get('completed', 0) > 0:
            return 'storyboard'
        elif clues.get('completed', 0) > 0 or clues.get('total', 0) == 0:
            return 'storyboard'
        elif chars.get('completed', 0) > 0:
            return 'clues'
        return 'characters'

    def enrich_project(self, project_name: str, project: Dict) -> Dict:
        """
        为项目数据注入所有计算字段

        Args:
            project_name: 项目名称
            project: 原始项目数据

        Returns:
            注入计算字段后的项目数据
        """
        # 计算整体进度
        progress = self.calculate_project_progress(project_name)
        current_phase = self.calculate_current_phase(progress)

        # 注入 status
        project['status'] = {
            'progress': progress,
            'current_phase': current_phase
        }

        # 为每个 episode 注入计算字段
        for ep in project.get('episodes', []):
            script_file = ep.get('script_file', '').replace('scripts/', '')
            if script_file:
                try:
                    script = self.pm.load_script(project_name, script_file)
                    stats = self.calculate_episode_stats(project_name, script)
                    ep['scenes_count'] = stats['scenes_count']
                    ep['status'] = stats['status']
                    ep['duration_seconds'] = stats['duration_seconds']
                except FileNotFoundError:
                    ep['scenes_count'] = 0
                    ep['status'] = 'missing'
                    ep['duration_seconds'] = 0

        return project

    def enrich_script(self, script: Dict) -> Dict:
        """
        为剧本数据注入计算字段

        Args:
            script: 原始剧本数据

        Returns:
            注入计算字段后的剧本数据
        """
        content_mode = script.get('content_mode', 'narration')
        items = script.get('segments' if content_mode == 'narration' else 'scenes', [])

        total_duration = sum(i.get('duration_seconds', 4) for i in items)

        # 注入 metadata 计算字段
        if 'metadata' not in script:
            script['metadata'] = {}

        script['metadata']['total_scenes'] = len(items)
        script['metadata']['estimated_duration_seconds'] = total_duration

        # 聚合 characters_in_episode 和 clues_in_episode（仅用于 API 响应，不存储）
        chars_set = set()
        clues_set = set()

        char_field = 'characters_in_segment' if content_mode == 'narration' else 'characters_in_scene'
        clue_field = 'clues_in_segment' if content_mode == 'narration' else 'clues_in_scene'

        for item in items:
            chars_set.update(item.get(char_field, []))
            clues_set.update(item.get(clue_field, []))

        script['characters_in_episode'] = sorted(chars_set)
        script['clues_in_episode'] = sorted(clues_set)

        return script
```

### 4. 修改 API Router

```python
# webui/server/routers/projects.py

from lib.status_calculator import StatusCalculator

# 初始化
pm = ProjectManager(project_root / "projects")
calc = StatusCalculator(pm)

@router.get("/projects/{name}")
async def get_project(name: str):
    """获取项目详情（含实时计算字段）"""
    try:
        if not pm.project_exists(name):
            raise HTTPException(status_code=404, detail=f"项目 '{name}' 不存在或未初始化")

        project = pm.load_project(name)

        # 注入计算字段（不写入 JSON）
        project = calc.enrich_project(name, project)

        # 加载所有剧本并注入计算字段
        scripts = {}
        for ep in project.get("episodes", []):
            script_file = ep.get("script_file", "").replace("scripts/", "")
            if script_file:
                try:
                    script = pm.load_script(name, script_file)
                    script = calc.enrich_script(script)
                    scripts[script_file] = script
                except FileNotFoundError:
                    pass

        return {
            "project": project,
            "scripts": scripts
        }
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目 '{name}' 不存在")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

### 5. 修改数据验证器

```python
# lib/data_validator.py - 修改验证逻辑

def validate_episode(self, project_name: str, episode_file: str) -> ValidationResult:
    # ... 现有代码 ...

    # 删除 characters_in_episode 和 clues_in_episode 验证
    # 改为直接验证 scene/segment 级别引用

    project_characters = set(project.get('characters', {}).keys())
    project_clues = set(project.get('clues', {}).keys())

    # 验证 segments 或 scenes
    if content_mode == 'narration':
        self._validate_segments(
            episode.get('segments', []),
            project_characters,  # 直接使用 project 级别
            project_clues,
            errors,
            warnings
        )
    else:
        self._validate_scenes(
            episode.get('scenes', []),
            project_characters,
            project_clues,
            errors,
            warnings
        )
```

### 6. Agent 指令修改

在 `.claude/agents/novel-to-narration-script.md` 和 `.claude/agents/novel-to-storyboard-script.md` 中：

**移除**：
- 生成 `characters_in_episode` 字段的指令
- 生成 `clues_in_episode` 字段的指令
- 生成 `duration_seconds`（顶层）字段的指令

**新增**：
```markdown
### Step 4: 同步剧集信息

剧本写入完成后，**必须**执行以下命令同步剧集信息到 project.json：

\`\`\`bash
python -c "
from lib.project_manager import ProjectManager
pm = ProjectManager('projects')
pm.sync_episode_from_script('{project_name}', 'episode_{n}.json')
"
\`\`\`

此步骤确保 WebUI 能够正确显示剧集列表。
```

## 数据结构变化

### project.json（精简后）

```json
{
  "title": "赡养人类",
  "content_mode": "drama",
  "style": "Anime",
  "episodes": [
    {
      "episode": 1,
      "title": "第一集：委托",
      "script_file": "scripts/episode_1.json"
    }
  ],
  "characters": {
    "滑膛": {
      "description": "职业杀手，二十多岁男性...",
      "voice_style": "低沉冷淡，语速平缓",
      "character_sheet": "characters/滑膛.png"
    }
  },
  "clues": {
    "哥哥飞船": {
      "type": "prop",
      "description": "外星飞船，表面光滑如钝银...",
      "importance": "major",
      "clue_sheet": "clues/哥哥飞船.png"
    }
  },
  "metadata": {
    "created_at": "2025-01-23T00:00:00",
    "updated_at": "2026-01-30T17:59:37.582106"
  },
  "overview": {...}
}
```

### scripts/episode_N.json（精简后）

```json
{
  "novel": {
    "title": "赡养人类",
    "author": "刘慈欣",
    "chapter": "第一集：委托",
    "source_file": "赡养人类.txt"
  },
  "episode": 1,
  "title": "第一集：委托",
  "content_mode": "drama",
  "summary": "...",
  "scenes": [...],
  "metadata": {
    "created_at": "2025-01-23",
    "updated_at": "2026-01-28T12:00:00.000000"
  }
}
```

## 修改文件清单

| 文件 | 修改类型 | 内容 |
|------|---------|------|
| `lib/status_calculator.py` | 新增 | 实时计算统计字段 |
| `lib/project_manager.py` | 修改 | 新增 `sync_episode_from_script()`，`save_script()` 调用同步 |
| `lib/data_validator.py` | 修改 | 移除 episode 级别引用验证，改为直接验证 scene 级别 |
| `webui/server/routers/projects.py` | 修改 | 使用 `StatusCalculator` 注入计算字段 |
| `.claude/agents/novel-to-narration-script.md` | 修改 | 移除冗余字段，添加同步步骤 |
| `.claude/agents/novel-to-storyboard-script.md` | 修改 | 移除冗余字段，添加同步步骤 |
| `CLAUDE.md` | 修改 | 更新数据结构说明 |

## 迁移脚本（可选）

```python
# scripts/migrate_clean_redundant_fields.py

"""清理现有项目中的冗余字段"""

import json
from pathlib import Path

def migrate_project(project_dir: Path):
    # 清理 project.json
    project_file = project_dir / "project.json"
    if project_file.exists():
        with open(project_file, 'r', encoding='utf-8') as f:
            project = json.load(f)

        # 移除 status 对象
        project.pop('status', None)

        # 移除 episodes 中的计算字段
        for ep in project.get('episodes', []):
            ep.pop('scenes_count', None)
            ep.pop('status', None)

        with open(project_file, 'w', encoding='utf-8') as f:
            json.dump(project, f, ensure_ascii=False, indent=2)

    # 清理 scripts/*.json
    scripts_dir = project_dir / "scripts"
    if scripts_dir.exists():
        for script_file in scripts_dir.glob("*.json"):
            with open(script_file, 'r', encoding='utf-8') as f:
                script = json.load(f)

            # 移除冗余字段
            script.pop('characters_in_episode', None)
            script.pop('clues_in_episode', None)
            script.pop('duration_seconds', None)

            if 'metadata' in script:
                script['metadata'].pop('total_scenes', None)
                script['metadata'].pop('estimated_duration_seconds', None)

            with open(script_file, 'w', encoding='utf-8') as f:
                json.dump(script, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    projects_root = Path("projects")
    for project_dir in projects_root.iterdir():
        if project_dir.is_dir() and not project_dir.name.startswith('.'):
            print(f"迁移项目: {project_dir.name}")
            migrate_project(project_dir)
    print("迁移完成")
```

## 实施顺序

1. **新增 `lib/status_calculator.py`** - 无破坏性变更
2. **修改 `lib/project_manager.py`** - 添加同步方法
3. **修改 `webui/server/routers/projects.py`** - 使用计算器
4. **修改 Agent 指令** - 添加同步步骤
5. **运行迁移脚本** - 清理现有数据
6. **修改 `lib/data_validator.py`** - 简化验证逻辑
7. **更新 `CLAUDE.md`** - 文档同步
