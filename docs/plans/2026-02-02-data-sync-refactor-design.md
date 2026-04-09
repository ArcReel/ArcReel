# Data Sync Refactor Design

## Overview

Resolves the data synchronization problem between `project.json` and `scripts/episode_N.json` using a **hybrid mode**:
- **Write-time sync**: core metadata is automatically synced when written
- **Read-time computation**: statistical fields are computed and returned in real time by the API

## Problem Analysis

### Current Issues

1. **No sync after write**: After the Agent writes `episode.json` using the Write tool, the `episodes[]` array in `project.json` is not updated, so the WebUI cannot display episode details.
2. **Status not real-time**: Progress information is a snapshot rather than computed in real time, and becomes stale easily.

### Root Causes

- The Agent writes JSON directly using the Write tool, bypassing `ProjectManager`
- Statistical fields are stored in JSON instead of being computed in real time
- Redundant intermediate layer fields exist (`characters_in_episode`, `clues_in_episode`)

## Architecture Design

```
┌─────────────────────────────────────────────────────────────┐
│                        API Layer                             │
│  GET /projects/{name} ──► ProjectService.get_project()       │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                     API Router (modified)                    │
│  - Read raw data (ProjectManager)                            │
│  - Inject computed fields (StatusCalculator)                 │
│  - Return complete response                                  │
└─────────────────────────────────────────────────────────────┘
         │                              │
         ▼                              ▼
┌─────────────────────┐    ┌─────────────────────────────────┐
│   ProjectManager    │    │      StatusCalculator (new)     │
│ - Read/write JSON   │    │ - Compute scenes_count          │
│ - Sync metadata on  │    │ - Compute progress.*            │
│   write             │    │ - Compute current_phase         │
│ - No longer manages │    │ - Compute duration_seconds      │
│   statistical fields│    │                                 │
└─────────────────────┘    └─────────────────────────────────┘
```

## Field Classification

### Write-time Sync Fields

| Field | Location | Description |
|-------|----------|-------------|
| `episodes[].episode` | project.json | Episode number |
| `episodes[].title` | project.json | Title, synced from episode.json |
| `episodes[].script_file` | project.json | Script file path |

### Read-time Computed Fields

| Field | Location | Computation Logic |
|-------|----------|-------------------|
| `episodes[].scenes_count` | API response | len(scenes/segments) |
| `episodes[].status` | API response | Inferred from resource states |
| `status.progress.*` | API response | Real-time traversal of resources |
| `status.current_phase` | API response | Inferred from progress |
| `metadata.total_scenes` | API response | len(scenes/segments) |
| `metadata.estimated_duration_seconds` | API response | sum(duration_seconds) |

### Removed Fields

| Field | Location | Reason for Removal |
|-------|----------|--------------------|
| `characters_in_episode` | episode.json | Redundant; can be aggregated from scenes |
| `clues_in_episode` | episode.json | Redundant; can be aggregated from scenes |
| `duration_seconds` (top-level) | episode.json | Duplicates metadata |
| `status` object | project.json | Changed to read-time computation |

## Detailed Design

### 1. Add `sync_episode_from_script()` Method

```python
# lib/project_manager.py

def sync_episode_from_script(self, project_name: str, script_filename: str) -> Dict:
    """
    Sync episode information from a script file to project.json.

    Must be called after the Agent writes a script.

    Args:
        project_name: Project name
        script_filename: Script filename (e.g. episode_1.json)

    Returns:
        Updated project dictionary
    """
    script = self.load_script(project_name, script_filename)
    project = self.load_project(project_name)

    episode_num = script.get('episode', 1)
    episode_title = script.get('title', '')
    script_file = f"scripts/{script_filename}"

    # Find or create the episode entry
    episodes = project.setdefault('episodes', [])
    episode_entry = next((ep for ep in episodes if ep['episode'] == episode_num), None)

    if episode_entry is None:
        episode_entry = {'episode': episode_num}
        episodes.append(episode_entry)

    # Sync core metadata (excludes statistical fields)
    episode_entry['title'] = episode_title
    episode_entry['script_file'] = script_file

    # Sort and save
    episodes.sort(key=lambda x: x['episode'])
    self.save_project(project_name, project)

    print(f"Synced episode info: Episode {episode_num} - {episode_title}")
    return project
```

### 2. Modify `save_script()` Method

```python
# lib/project_manager.py

def save_script(self, project_name: str, script: Dict, filename: str) -> Path:
    # ... existing save logic ...

    # New: automatically sync to project.json
    if self.project_exists(project_name):
        self.sync_episode_from_script(project_name, filename)

    return output_path
```

### 3. Add `StatusCalculator` Class

```python
# lib/status_calculator.py (new file)

from pathlib import Path
from typing import Dict, List, Any

from lib.project_manager import ProjectManager


class StatusCalculator:
    """Real-time calculator for status and statistical fields."""

    def __init__(self, project_manager: ProjectManager):
        self.pm = project_manager

    def calculate_episode_stats(self, project_name: str, script: Dict) -> Dict:
        """Calculate statistics for a single episode."""
        content_mode = script.get('content_mode', 'narration')
        items = script.get('segments' if content_mode == 'narration' else 'scenes', [])

        # Count completed resources
        storyboard_done = sum(
            1 for i in items
            if i.get('generated_assets', {}).get('storyboard_image')
        )
        video_done = sum(
            1 for i in items
            if i.get('generated_assets', {}).get('video_clip')
        )
        total = len(items)

        # Compute status
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
        """Calculate overall project progress (real-time)."""
        project = self.pm.load_project(project_name)
        project_dir = self.pm.get_project_path(project_name)

        # Character statistics
        chars = project.get('characters', {})
        chars_total = len(chars)
        chars_done = sum(
            1 for c in chars.values()
            if c.get('character_sheet') and (project_dir / c['character_sheet']).exists()
        )

        # ... (remaining calculation logic)

    def enrich_project(self, project_name: str, project: Dict) -> Dict:
        """
        Inject computed fields into project data.

        Args:
            project_name: Project name
            project: Raw project data

        Returns:
            Project data with computed fields injected
        """
        # ... (implementation)

        # Inject computed fields for each episode
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
        Inject computed fields into script data.

        Args:
            script: Raw script data

        Returns:
            Script data with computed fields injected
        """
        content_mode = script.get('content_mode', 'narration')
        items = script.get('segments' if content_mode == 'narration' else 'scenes', [])

        total_duration = sum(i.get('duration_seconds', 4) for i in items)

        # Inject metadata computed fields
        if 'metadata' not in script:
            script['metadata'] = {}

        script['metadata']['total_scenes'] = len(items)
        script['metadata']['estimated_duration_seconds'] = total_duration

        # Aggregate characters_in_episode and clues_in_episode (for API response only; not stored)
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

### 4. Modify API Router

```python
# webui/server/routers/projects.py

from lib.status_calculator import StatusCalculator

# Initialize
pm = ProjectManager(project_root / "projects")
calc = StatusCalculator(pm)

@router.get("/projects/{name}")
async def get_project(name: str):
    """Get project details (including real-time computed fields)."""
    try:
        if not pm.project_exists(name):
            raise HTTPException(status_code=404, detail=f"Project '{name}' does not exist or is not initialized")

        project = pm.load_project(name)

        # Inject computed fields (does not write to JSON)
        project = calc.enrich_project(name, project)

        # Load all scripts and inject computed fields
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
        raise HTTPException(status_code=404, detail=f"Project '{name}' does not exist")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

### 5. Modify Data Validator

```python
# lib/data_validator.py - modify validation logic

def validate_episode(self, project_name: str, episode_file: str) -> ValidationResult:
    # ... existing code ...

    # Remove characters_in_episode and clues_in_episode validation
    # Directly validate scene/segment-level references instead

    project_characters = set(project.get('characters', {}).keys())
    project_clues = set(project.get('clues', {}).keys())

    # Validate segments or scenes
    if content_mode == 'narration':
        self._validate_segments(
            episode.get('segments', []),
            project_characters,  # Use project-level directly
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

### 6. Agent Instruction Updates

In `.claude/agents/novel-to-narration-script.md` and `.claude/agents/novel-to-storyboard-script.md`:

**Remove**:
- Instructions for generating the `characters_in_episode` field
- Instructions for generating the `clues_in_episode` field
- Instructions for generating the top-level `duration_seconds` field

**Add**:
```markdown
### Step 4: Sync Episode Information

After the script is written, **you must** run the following command to sync episode information to project.json:

\`\`\`bash
python -c "
from lib.project_manager import ProjectManager
pm = ProjectManager('projects')
pm.sync_episode_from_script('{project_name}', 'episode_{n}.json')
"
\`\`\`

This step ensures the WebUI can correctly display the episode list.
```

## Data Structure Changes

### project.json (simplified)

```json
{
  "title": "Supporting Humanity",
  "content_mode": "drama",
  "style": "Anime",
  "episodes": [
    {
      "episode": 1,
      "title": "Episode 1: The Commission",
      "script_file": "scripts/episode_1.json"
    }
  ],
  "characters": {
    "Huatang": {
      "description": "Professional assassin, male in his twenties...",
      "voice_style": "Low and cold, steady pace",
      "character_sheet": "characters/Huatang.png"
    }
  },
  "clues": {
    "Brothers Spaceship": {
      "type": "prop",
      "description": "Alien spacecraft with a smooth, dull-silver surface...",
      "importance": "major",
      "clue_sheet": "clues/brothers_spaceship.png"
    }
  },
  "metadata": {
    "created_at": "2025-01-23T00:00:00",
    "updated_at": "2026-01-30T17:59:37.582106"
  },
  "overview": {}
}
```

### scripts/episode_N.json (simplified)

```json
{
  "novel": {
    "title": "Supporting Humanity",
    "author": "Liu Cixin",
    "chapter": "Episode 1: The Commission",
    "source_file": "supporting_humanity.txt"
  },
  "episode": 1,
  "title": "Episode 1: The Commission",
  "content_mode": "drama",
  "summary": "...",
  "scenes": [],
  "metadata": {
    "created_at": "2025-01-23",
    "updated_at": "2026-01-28T12:00:00.000000"
  }
}
```

## Files to Modify

| File | Change Type | Content |
|------|-------------|---------|
| `lib/status_calculator.py` | New | Real-time computation of statistical fields |
| `lib/project_manager.py` | Modify | Add `sync_episode_from_script()`; `save_script()` calls sync |
| `lib/data_validator.py` | Modify | Remove episode-level reference validation; directly validate scene level |
| `webui/server/routers/projects.py` | Modify | Use `StatusCalculator` to inject computed fields |
| `.claude/agents/novel-to-narration-script.md` | Modify | Remove redundant fields, add sync step |
| `.claude/agents/novel-to-storyboard-script.md` | Modify | Remove redundant fields, add sync step |
| `CLAUDE.md` | Modify | Update data structure documentation |

## Migration Script (Optional)

```python
# scripts/migrate_clean_redundant_fields.py

"""Remove redundant fields from existing projects."""

import json
from pathlib import Path

def migrate_project(project_dir: Path):
    # Clean project.json
    project_file = project_dir / "project.json"
    if project_file.exists():
        with open(project_file, 'r', encoding='utf-8') as f:
            project = json.load(f)

        # Remove status object
        project.pop('status', None)

        # Remove computed fields from episodes
        for ep in project.get('episodes', []):
            ep.pop('scenes_count', None)
            ep.pop('status', None)

        with open(project_file, 'w', encoding='utf-8') as f:
            json.dump(project, f, ensure_ascii=False, indent=2)

    # Clean scripts/*.json
    scripts_dir = project_dir / "scripts"
    if scripts_dir.exists():
        for script_file in scripts_dir.glob("*.json"):
            with open(script_file, 'r', encoding='utf-8') as f:
                script = json.load(f)

            # Remove redundant fields
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
            print(f"Migrating project: {project_dir.name}")
            migrate_project(project_dir)
    print("Migration complete")
```

## Implementation Order

1. **Add `lib/status_calculator.py`** — no breaking changes
2. **Modify `lib/project_manager.py`** — add sync method
3. **Modify `webui/server/routers/projects.py`** — use calculator
4. **Modify Agent instructions** — add sync step
5. **Run migration script** — clean up existing data
6. **Modify `lib/data_validator.py`** — simplify validation logic
7. **Update `CLAUDE.md`** — sync documentation
