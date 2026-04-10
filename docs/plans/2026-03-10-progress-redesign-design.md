# Progress Mechanism Redesign

**Date**: 2026-03-10
**Status**: Approved, pending implementation

---

## Background and Problems

The current progress mechanism has the following problems:

| Problem | Current State | Goal |
|------|------|------|
| Scripting phase missing | Source file upload, overview generation, episode planning, JSON script generation are all untracked | Include in progress |
| Phase relationship inaccurate | Characters/clues treated as two sequential phases | Independent worldbuilding phase (parallel) |
| Storyboard/video granularity wrong | Aggregated by project (sum of all episodes) | Calculated independently per episode |
| Phase inference logic wrong | Only infers current phase from quantity ratio | Based on actual workflow state machine |
| Characters/clues hidden | Not shown after production phase | Always shown (subsequent episodes may add more) |

---

## Design Goal

The core goal of the progress mechanism: **Help users quickly understand the current project state and clearly identify the next step.**

---

## Data Model

### Project-level Status (Quick Overview)

```python
class ProjectStatus:
    current_phase: Literal["setup", "worldbuilding", "scripting", "production", "completed"]
    phase_progress: float   # 0.0–1.0, completion rate of current phase
    characters: CategoryProgress   # { total: int, completed: int }
    clues: CategoryProgress        # { total: int, completed: int }
    episodes_summary: EpisodesSummary
    # {
    #     total: int,
    #     scripted: int,        # number of episodes with script_status == "generated"
    #     in_production: int,   # number of episodes with status == "in_production"
    #     completed: int        # number of episodes with status == "completed"
    # }
```

### Episode-level Status (Detailed Breakdown)

```python
class EpisodeMeta:
    script_status: Literal["none", "segmented", "generated"]
    # none      = no script files at all
    # segmented = drafts/episode_N/step1_segments.md exists
    # generated = scripts/episode_N.json exists

    storyboards: CategoryProgress   # { total: int, completed: int }
    videos: CategoryProgress        # { total: int, completed: int }
    status: Literal["draft", "scripted", "in_production", "completed"]
    scenes_count: int
    duration_seconds: int
```

### Phase Definitions

| Phase | Value | Condition | `phase_progress` Meaning |
|------|--------|---------|----------------------|
| Setup | `setup` | No overview | Has source file → 0.5, none → 0.0 |
| Worldbuilding | `worldbuilding` | Has overview, no episode script JSON | `(chars_completed + clues_completed) / (chars_total + clues_total)` |
| Scripting | `scripting` | At least one episode has a script, but not all | `episodes_with_script / total_episodes` |
| Production | `production` | All episode scripts complete, in production | `completed_videos / total_videos (across all episodes)` |
| Completed | `completed` | All videos completed | `1.0` |

**Note**: Characters and clues are always shown in all phases, because subsequent episode production may add new characters/clues.

---

## Backend Calculation Logic

### `calculate_episode_stats()` Changes

```python
# Before (two flat fields)
{
    "storyboards_completed": int,
    "videos_completed": int,
    "scenes_count": int,
    "status": str,
    "duration_seconds": int,
}

# After
{
    "script_status": "none" | "segmented" | "generated",   # new
    "storyboards": { "total": int, "completed": int },      # structure change
    "videos": { "total": int, "completed": int },           # structure change
    "status": "draft" | "scripted" | "in_production" | "completed",
    "scenes_count": int,
    "duration_seconds": int,
}
```

`script_status` determination logic:
- `generated`: `scripts/episode_N.json` file exists
- `segmented`: `drafts/episode_N/step1_segments.md` file exists (segmentation complete, JSON not yet generated)
- `none`: neither of the above exists

`status` determination logic:
- `completed`: `videos.completed == videos.total > 0`
- `in_production`: `storyboards.completed > 0 || videos.completed > 0`
- `scripted`: `script_status == "generated"` (has script but no generated assets)
- `draft`: all other cases

### `calculate_current_phase()` Rewrite

```python
def calculate_current_phase(project, episodes_stats: list[dict]) -> str:
    if not project.get("overview"):
        return "setup"

    if not episodes_stats:
        return "worldbuilding"

    all_generated = all(s["script_status"] == "generated" for s in episodes_stats)
    if not all_generated:
        # If at least one episode has a JSON script, enter scripting; otherwise still worldbuilding
        any_generated = any(s["script_status"] == "generated" for s in episodes_stats)
        return "scripting" if any_generated else "worldbuilding"

    all_completed = all(s["status"] == "completed" for s in episodes_stats)
    return "completed" if all_completed else "production"
```

### `enrich_project()` Updated Flow

```python
def enrich_project(project_name, project):
    # 1. Calculate per-episode details (episode-level status), inject into each episode object
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

    # 2. Calculate project summary
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

## Frontend Display

### Type Changes (`frontend/src/types/project.ts`)

```typescript
// Deprecate ProjectProgress, replace with ProjectStatus
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
  script_status: "none" | "segmented" | "generated";  // new
  storyboards: ProgressCategory;  // formerly storyboards_completed, now an object
  videos: ProgressCategory;       // formerly videos_completed, now an object
  status: "draft" | "scripted" | "in_production" | "completed";
  scenes_count?: number;
  duration_seconds?: number;
}

// ProgressCategory unchanged
interface ProgressCategory {
  total: number;
  completed: number;
}
```

### `ProjectCard` Display (`ProjectsPage.tsx`)

```
┌─────────────────────────────────────┐
│ Project Title                        │
│ Style · In Production                │
│                                      │
│ ████████████░░░  62%                 │  ← phase_progress
│ In Production                        │  ← current_phase friendly name
│                                      │
│ Characters 3/5  ·  Clues 2/4        │  ← always shown
│ 3 eps  ·  2 scripted  ·  1 in prod  │  ← episodes_summary
└─────────────────────────────────────┘
```

Phase friendly name mapping:
| `current_phase` | Display Text |
|----------------|---------|
| `setup` | Setup |
| `worldbuilding` | Worldbuilding |
| `scripting` | Scripting |
| `production` | In Production |
| `completed` | Completed |

### `AssetSidebar` Episode Status Dots

Source field path updates:
- `ep.storyboards_completed` → `ep.storyboards.completed`
- `ep.videos_completed` → `ep.videos.completed`

Logic unchanged, reuse existing status color dots.

---

## Affected Files

| File | Change Type |
|------|---------|
| `lib/status_calculator.py` | Core rewrite |
| `lib/script_models.py` | Update `EpisodeMeta` type definition |
| `tests/test_status_calculator.py` | Update existing tests + add new cases |
| `frontend/src/types/project.ts` | Type updates |
| `frontend/src/components/pages/ProjectsPage.tsx` | `ProjectCard` display logic |
| `frontend/src/components/layout/AssetSidebar.tsx` | Field reference updates |
| Other components referencing old `progress.*` fields | Field path updates |
