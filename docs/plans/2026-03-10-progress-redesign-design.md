# 进度机制重新设计

**日期**：2026-03-10
**状态**：已批准，待实现

---

## 背景与问题

当前进度机制存在以下问题：

| 问题 | 现状 | 目标 |
|------|------|------|
| 剧本创作阶段缺失 | 源文件上传、概述生成、分集规划、JSON 剧本生成均未追踪 | 纳入进度 |
| 阶段关系不准确 | 角色/线索视为两个顺序阶段 | 独立的 worldbuilding 阶段（并行） |
| 分镜/视频粒度错误 | 按项目汇总（所有集的总和） | 按集独立计算 |
| 阶段推断逻辑错误 | 仅由数量比例推断当前阶段 | 基于实际工作流状态机 |
| 人物/线索隐藏 | production 阶段后不显示 | 始终展示（后续剧集可能追加） |

---

## 设计目标

进度机制的核心目标：**帮助用户快速了解当前项目状态，明确下一步该做什么。**

---

## 数据模型

### 项目级状态（快速一览）

```python
class ProjectStatus:
    current_phase: Literal["setup", "worldbuilding", "scripting", "production", "completed"]
    phase_progress: float   # 0.0–1.0，当前阶段完成率
    characters: CategoryProgress   # { total: int, completed: int }
    clues: CategoryProgress        # { total: int, completed: int }
    episodes_summary: EpisodesSummary
    # {
    #     total: int,
    #     scripted: int,        # script_status == "generated" 的集数
    #     in_production: int,   # status == "in_production" 的集数
    #     completed: int        # status == "completed" 的集数
    # }
```

### 集级状态（详细明细）

```python
class EpisodeMeta:
    script_status: Literal["none", "segmented", "generated"]
    # none      = 无任何剧本文件
    # segmented = drafts/episode_N/step1_segments.md 存在
    # generated = scripts/episode_N.json 存在

    storyboards: CategoryProgress   # { total: int, completed: int }
    videos: CategoryProgress        # { total: int, completed: int }
    status: Literal["draft", "scripted", "in_production", "completed"]
    scenes_count: int
    duration_seconds: int
```

### 阶段定义

| 阶段 | 英文值 | 判断条件 | `phase_progress` 含义 |
|------|--------|---------|----------------------|
| 准备中 | `setup` | 无 overview | 有源文件 → 0.5，无 → 0.0 |
| 世界观 | `worldbuilding` | 有 overview，无任何集的剧本 JSON | `(角色完成 + 线索完成) / (角色总数 + 线索总数)` |
| 剧本创作 | `scripting` | 有至少一集剧本，但未全部完成 | `已生成剧本的集数 / 总集数` |
| 制作中 | `production` | 所有集剧本完成，制作中 | `已完成视频数 / 总视频数（跨所有集）` |
| 已完成 | `completed` | 所有视频均已完成 | `1.0` |

**注意**：角色和线索在所有阶段始终显示，因为后续剧集制作时可能追加新人物/线索。

---

## 后端计算逻辑

### `calculate_episode_stats()` 改动

```python
# 之前返回（两个扁平字段）
{
    "storyboards_completed": int,
    "videos_completed": int,
    "scenes_count": int,
    "status": str,
    "duration_seconds": int,
}

# 之后返回
{
    "script_status": "none" | "segmented" | "generated",   # 新增
    "storyboards": { "total": int, "completed": int },      # 结构变更
    "videos": { "total": int, "completed": int },           # 结构变更
    "status": "draft" | "scripted" | "in_production" | "completed",
    "scenes_count": int,
    "duration_seconds": int,
}
```

`script_status` 判断逻辑：
- `generated`：`scripts/episode_N.json` 文件存在
- `segmented`：`drafts/episode_N/step1_segments.md` 文件存在（拆分完成，未生成 JSON）
- `none`：以上都不存在

`status` 判断逻辑：
- `completed`：`videos.completed == videos.total > 0`
- `in_production`：`storyboards.completed > 0 || videos.completed > 0`
- `scripted`：`script_status == "generated"`（有剧本但无任何生成资源）
- `draft`：其他情况

### `calculate_current_phase()` 重写

```python
def calculate_current_phase(project, episodes_stats: list[dict]) -> str:
    if not project.get("overview"):
        return "setup"

    if not episodes_stats:
        return "worldbuilding"

    all_generated = all(s["script_status"] == "generated" for s in episodes_stats)
    if not all_generated:
        # 有至少一集有 JSON 剧本则进入 scripting，否则仍是 worldbuilding
        any_generated = any(s["script_status"] == "generated" for s in episodes_stats)
        return "scripting" if any_generated else "worldbuilding"

    all_completed = all(s["status"] == "completed" for s in episodes_stats)
    return "completed" if all_completed else "production"
```

### `enrich_project()` 更新流程

```python
def enrich_project(project_name, project):
    # 1. 计算每集明细（集级状态），注入到每个 episode 对象
    episodes_stats = []
    for ep in project["episodes"]:
        if ep.get("script_file"):
            stats = self.calculate_episode_stats(project_name, ep)
        else:
            stats = { "script_status": "none", "storyboards": {"total":0,"completed":0},
                      "videos": {"total":0,"completed":0}, "status": "draft",
                      "scenes_count": 0, "duration_seconds": 0 }
        ep.update(stats)
        episodes_stats.append(stats)

    # 2. 计算项目汇总
    phase = self.calculate_current_phase(project, episodes_stats)
    phase_progress = self._calculate_phase_progress(project, phase, episodes_stats)
    chars = self._calculate_characters_progress(project_name, project)
    clues = self._calculate_clues_progress(project_name, project)

    project["status"] = {
        "current_phase": phase,
        "phase_progress": phase_progress,
        "characters": chars,
        "clues": clues,
        "episodes_summary": {
            "total": len(episodes_stats),
            "scripted": sum(1 for s in episodes_stats if s["script_status"] == "generated"),
            "in_production": sum(1 for s in episodes_stats if s["status"] == "in_production"),
            "completed": sum(1 for s in episodes_stats if s["status"] == "completed"),
        }
    }
```

---

## 前端展示

### 类型变更（`frontend/src/types/project.ts`）

```typescript
// 废弃 ProjectProgress，改为 ProjectStatus
interface ProjectStatus {
  current_phase: "setup" | "worldbuilding" | "scripting" | "production" | "completed";
  phase_progress: number;       // 0.0–1.0
  characters: ProgressCategory;
  clues: ProgressCategory;
  episodes_summary: {
    total: number;
    scripted: number;
    in_production: number;
    completed: number;
  };
}

interface EpisodeMeta {
  script_status: "none" | "segmented" | "generated";  // 新增
  storyboards: ProgressCategory;  // 原 storyboards_completed 改为对象
  videos: ProgressCategory;       // 原 videos_completed 改为对象
  status: "draft" | "scripted" | "in_production" | "completed";
  scenes_count?: number;
  duration_seconds?: number;
}

// ProgressCategory 不变
interface ProgressCategory {
  total: number;
  completed: number;
}
```

### `ProjectCard` 展示（`ProjectsPage.tsx`）

```
┌─────────────────────────────────────┐
│ 项目标题                             │
│ 风格 · 制作中                        │
│                                      │
│ ████████████░░░  62%                 │  ← phase_progress
│ 制作中                                │  ← current_phase 友好名称
│                                      │
│ 人物 3/5  ·  线索 2/4                │  ← 始终显示
│ 3集  ·  2集剧本完成  ·  1集制作中    │  ← episodes_summary
└─────────────────────────────────────┘
```

阶段友好名称映射：
| `current_phase` | 显示文字 |
|----------------|---------|
| `setup` | 准备中 |
| `worldbuilding` | 世界观 |
| `scripting` | 剧本创作 |
| `production` | 制作中 |
| `completed` | 已完成 |

### `AssetSidebar` 集状态点

数据来源字段路径更新：
- `ep.storyboards_completed` → `ep.storyboards.completed`
- `ep.videos_completed` → `ep.videos.completed`

逻辑不变，沿用现有状态色点。

---

## 受影响文件

| 文件 | 改动类型 |
|------|---------|
| `lib/status_calculator.py` | 核心重写 |
| `lib/script_models.py` | 更新 `EpisodeMeta` 类型定义 |
| `tests/test_status_calculator.py` | 更新现有测试 + 新增用例 |
| `frontend/src/types/project.ts` | 类型更新 |
| `frontend/src/components/pages/ProjectsPage.tsx` | `ProjectCard` 展示逻辑 |
| `frontend/src/components/layout/AssetSidebar.tsx` | 字段引用更新 |
| 其他引用旧 `progress.*` 字段的组件 | 字段路径更新 |
