# Progress Mechanism Redesign Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Redesign the progress mechanism so it accurately reflects the complete workflow (setup → worldbuilding → scripting → production → completed), tracks storyboard/video progress at the episode granularity, and always shows characters/clues.

**Architecture:** Modify `StatusCalculator` to introduce a 5-segment phase enum, episode-level `script_status` field, and a new `calculate_project_status()` method; synchronously update frontend types and `ProjectCard` display logic. The read-time calculation strategy is unchanged, no redundant state is stored.

**Tech Stack:** Python 3.12 / pytest / TypeScript / React 19 / Tailwind CSS 4

**Design Doc:** `docs/plans/2026-03-10-progress-redesign-design.md`

---

## Task 1: Update `calculate_episode_stats()` return structure

**Files:**
- Modify: `lib/status_calculator.py:40-79`
- Test: `tests/test_status_calculator.py`

**Step 1: Write failing tests (new return structure)**

In the `TestStatusCalculator` class in `tests/test_status_calculator.py`, replace the existing `test_calculate_episode_stats_statuses` with:

```python
def test_calculate_episode_stats_statuses(self, tmp_path):
    calc = StatusCalculator(_FakePM(tmp_path, {}, {}))

    # draft: no assets at all
    draft = calc.calculate_episode_stats(
        "demo",
        {"content_mode": "narration", "segments": [{"duration_seconds": 4}]},
    )
    assert draft["status"] == "draft"
    assert draft["storyboards"] == {"total": 1, "completed": 0}
    assert draft["videos"] == {"total": 1, "completed": 0}
    assert draft["scenes_count"] == 1
    assert draft["duration_seconds"] == 4

    # scripted: has script but no storyboard/video assets
    # (script_status is set by enrich_project, not by calculate_episode_stats)
    # status is "draft" when script loads successfully

    # in_production: has storyboard image
    in_prod = calc.calculate_episode_stats(
        "demo",
        {
            "content_mode": "narration",
            "segments": [
                {"generated_assets": {"storyboard_image": "a.png"}, "duration_seconds": 6},
                {"duration_seconds": 4},
            ],
        },
    )
    assert in_prod["status"] == "in_production"
    assert in_prod["storyboards"] == {"total": 2, "completed": 1}
    assert in_prod["videos"] == {"total": 2, "completed": 0}

    # completed: all scenes have video
    completed = calc.calculate_episode_stats(
        "demo",
        {
            "content_mode": "drama",
            "scenes": [
                {"generated_assets": {"video_clip": "a.mp4"}, "duration_seconds": 8},
            ],
        },
    )
    assert completed["status"] == "completed"
    assert completed["storyboards"] == {"total": 1, "completed": 0}
    assert completed["videos"] == {"total": 1, "completed": 1}
```

**Step 2: Run to confirm failure**

```bash
cd .worktrees/progress-redesign
python -m pytest tests/test_status_calculator.py::TestStatusCalculator::test_calculate_episode_stats_statuses -v
```

Expected: FAILED (`storyboards` key does not exist, returns `storyboards_completed`)

**Step 3: Modify `calculate_episode_stats()` implementation**

Change the return value in `lib/status_calculator.py:40-79` from:
```python
return {
    'scenes_count': total,
    'status': status,
    'duration_seconds': ...,
    'storyboards_completed': storyboard_done,
    'videos_completed': video_done
}
```

To:
```python
return {
    'scenes_count': total,
    'status': status,
    'duration_seconds': sum(i.get('duration_seconds', default_duration) for i in items),
    'storyboards': {'total': total, 'completed': storyboard_done},
    'videos': {'total': total, 'completed': video_done},
}
```

Also update the `status` determination (added `scripted` — when there is a script but no assets, `enrich_project` will override it; keep `draft` here):

```python
# Compute status (excluding scripted, which is overridden by enrich_project)
if video_done == total and total > 0:
    status = 'completed'
elif storyboard_done > 0 or video_done > 0:
    status = 'in_production'
else:
    status = 'draft'
```

**Step 4: Run to confirm passing**

```bash
python -m pytest tests/test_status_calculator.py::TestStatusCalculator::test_calculate_episode_stats_statuses -v
```

Expected: PASSED

**Step 5: Run all tests (some tests may reference old fields — record them and continue)**

```bash
python -m pytest tests/test_status_calculator.py -v
```

Record failures; fix them uniformly in Task 2.

**Step 6: Commit**

```bash
git add lib/status_calculator.py tests/test_status_calculator.py
git commit -m "refactor(status): calculate_episode_stats returns storyboards/videos as objects"
```

---

## Task 2: Add `_get_episode_script_status()` + Rewrite phase logic

**Files:**
- Modify: `lib/status_calculator.py`
- Test: `tests/test_status_calculator.py`

Add three methods, replacing `calculate_project_progress()` and the old `calculate_current_phase()`.

**Step 1: Write failing tests (new phase enum + script_status)**

In `TestStatusCalculator`, replace `test_calculate_project_progress_and_phase` with:

```python
def test_get_episode_script_status(self, tmp_path):
    project_root = tmp_path / "projects"
    project_path = project_root / "demo"

    # Case 1: script JSON exists → "generated"
    scripts = {"episode_1.json": {"content_mode": "narration", "segments": []}}
    calc = StatusCalculator(_FakePM(project_root, {}, scripts))
    assert calc._get_episode_script_status("demo", 1, "scripts/episode_1.json") == "generated"

    # Case 2: script does not exist, draft file exists → "segmented"
    draft_dir = project_path / "drafts" / "episode_2"
    draft_dir.mkdir(parents=True)
    (draft_dir / "step1_segments.md").write_text("ok")
    calc2 = StatusCalculator(_FakePM(project_root, {}, {}))
    assert calc2._get_episode_script_status("demo", 2, "scripts/episode_2.json") == "segmented"

    # Case 3: neither exists → "none"
    calc3 = StatusCalculator(_FakePM(project_root, {}, {}))
    assert calc3._get_episode_script_status("demo", 3, "scripts/episode_3.json") == "none"

def test_calculate_current_phase_setup(self, tmp_path):
    calc = StatusCalculator(_FakePM(tmp_path, {}, {}))
    project_no_overview = {}
    assert calc.calculate_current_phase(project_no_overview, []) == "setup"

def test_calculate_current_phase_worldbuilding(self, tmp_path):
    calc = StatusCalculator(_FakePM(tmp_path, {}, {}))
    project = {"overview": {"synopsis": "test"}}
    # No generated scripts at all → worldbuilding
    episodes_stats = [{"script_status": "none"}, {"script_status": "segmented"}]
    assert calc.calculate_current_phase(project, episodes_stats) == "worldbuilding"
    # No episodes → worldbuilding
    assert calc.calculate_current_phase(project, []) == "worldbuilding"

def test_calculate_current_phase_scripting(self, tmp_path):
    calc = StatusCalculator(_FakePM(tmp_path, {}, {}))
    project = {"overview": {"synopsis": "test"}}
    # At least one generated episode, but not all → scripting
    episodes_stats = [
        {"script_status": "generated", "status": "draft"},
        {"script_status": "none"},
    ]
    assert calc.calculate_current_phase(project, episodes_stats) == "scripting"

def test_calculate_current_phase_production_and_completed(self, tmp_path):
    calc = StatusCalculator(_FakePM(tmp_path, {}, {}))
    project = {"overview": {"synopsis": "test"}}
    # All generated, some videos incomplete → production
    episodes_stats = [
        {"script_status": "generated", "status": "in_production"},
        {"script_status": "generated", "status": "draft"},
    ]
    assert calc.calculate_current_phase(project, episodes_stats) == "production"
    # All completed → completed
    episodes_stats_done = [
        {"script_status": "generated", "status": "completed"},
    ]
    assert calc.calculate_current_phase(project, episodes_stats_done) == "completed"

def test_calculate_project_status(self, tmp_path):
    project_root = tmp_path / "projects"
    project_path = project_root / "demo"
    (project_path / "characters").mkdir(parents=True)
    (project_path / "clues").mkdir(parents=True)
    (project_path / "characters" / "A.png").write_bytes(b"ok")
    (project_path / "clues" / "C.png").write_bytes(b"ok")

    project = {
        "overview": {"synopsis": "test"},
        "characters": {"A": {"character_sheet": "characters/A.png"}, "B": {"character_sheet": ""}},
        "clues": {
            "C": {"importance": "major", "clue_sheet": "clues/C.png"},
            "D": {"importance": "minor", "clue_sheet": ""},
        },
        "episodes": [
            {"episode": 1, "script_file": "scripts/episode_1.json"},
        ],
    }
    scripts = {
        "episode_1.json": {
            "content_mode": "narration",
            "segments": [
                {"duration_seconds": 4, "generated_assets": {"storyboard_image": "a.png", "video_clip": "b.mp4"}},
            ],
        }
    }
    calc = StatusCalculator(_FakePM(project_root, project, scripts))
    status = calc.calculate_project_status("demo", project)

    assert status["current_phase"] == "completed"
    assert status["phase_progress"] == 1.0
    assert status["characters"] == {"total": 2, "completed": 1}
    assert status["clues"] == {"total": 1, "completed": 1}
    assert status["episodes_summary"] == {
        "total": 1, "scripted": 1, "in_production": 0, "completed": 1
    }
```

**Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_status_calculator.py -k "test_get_episode_script_status or test_calculate_current_phase or test_calculate_project_status" -v
```

Expected: All FAILED

**Step 3: Implement new methods**

In `lib/status_calculator.py`, add the following methods (replacing `calculate_project_progress` and `calculate_current_phase`):

```python
def _get_episode_script_status(self, project_name: str, episode_num: int, script_file: str) -> str:
    """Determine the script status for a single episode: 'generated' | 'segmented' | 'none'"""
    try:
        self.pm.load_script(project_name, script_file)
        return 'generated'
    except FileNotFoundError:
        project_dir = self.pm.get_project_path(project_name)
        draft_file = project_dir / f'drafts/episode_{episode_num}/step1_segments.md'
        return 'segmented' if draft_file.exists() else 'none'

def calculate_current_phase(self, project: Dict, episodes_stats: List[Dict]) -> str:
    """Infer the current phase from project and episode status"""
    if not project.get('overview'):
        return 'setup'
    if not episodes_stats:
        return 'worldbuilding'
    any_generated = any(s['script_status'] == 'generated' for s in episodes_stats)
    all_generated = all(s['script_status'] == 'generated' for s in episodes_stats)
    if not any_generated:
        return 'worldbuilding'
    if not all_generated:
        return 'scripting'
    all_completed = all(s['status'] == 'completed' for s in episodes_stats)
    return 'completed' if all_completed else 'production'

def _calculate_phase_progress(self, project: Dict, phase: str, episodes_stats: List[Dict]) -> float:
    """Calculate the completion rate of the current phase, 0.0–1.0"""
    if phase == 'setup':
        # Has source file → 0.5; otherwise 0.0 (phase only switches when overview is complete, never reaches 1.0)
        project_dir = self.pm.get_project_path('_placeholder')  # Actual path not needed
        return 0.0  # setup phase does not track source file, simplified
    if phase == 'worldbuilding':
        chars = project.get('characters', {})
        clues_major = [c for c in project.get('clues', {}).values() if c.get('importance') == 'major']
        total = len(chars) + len(clues_major)
        if total == 0:
            return 0.0
        # Requires filesystem check; cannot be obtained from episodes_stats here; return 0.0 as conservative value
        return 0.0
    if phase == 'scripting':
        total = len(episodes_stats)
        if total == 0:
            return 0.0
        done = sum(1 for s in episodes_stats if s['script_status'] == 'generated')
        return done / total
    if phase == 'production':
        total_videos = sum(s.get('videos', {}).get('total', 0) for s in episodes_stats)
        done_videos = sum(s.get('videos', {}).get('completed', 0) for s in episodes_stats)
        return done_videos / total_videos if total_videos > 0 else 0.0
    return 1.0  # completed

def calculate_project_status(self, project_name: str, project: Dict) -> Dict:
    """
    Calculate the overall project status (used for list API).

    Returns:
        ProjectStatus dict: current_phase, phase_progress, characters, clues, episodes_summary
    """
    project_dir = self.pm.get_project_path(project_name)

    # Character statistics
    chars = project.get('characters', {})
    chars_total = len(chars)
    chars_done = sum(
        1 for c in chars.values()
        if c.get('character_sheet') and (project_dir / c['character_sheet']).exists()
    )

    # Clue statistics (all clues, not limited to major)
    clues = project.get('clues', {})
    clues_total = len(clues)
    clues_done = sum(
        1 for c in clues.values()
        if c.get('clue_sheet') and (project_dir / c['clue_sheet']).exists()
    )

    # Per-episode status
    episodes_stats = []
    for ep in project.get('episodes', []):
        script_file = ep.get('script_file', '')
        episode_num = ep.get('episode', 0)
        script_status = self._get_episode_script_status(project_name, episode_num, script_file) if script_file else 'none'

        if script_status == 'generated':
            try:
                script = self.pm.load_script(project_name, script_file)
                ep_stats = self.calculate_episode_stats(project_name, script)
                # If script loads successfully it is "generated"; status is determined by calculate_episode_stats
                # But if there are no assets at all, status should be "scripted" (not "draft")
                if ep_stats['status'] == 'draft':
                    ep_stats['status'] = 'scripted'
                ep_stats['script_status'] = 'generated'
            except FileNotFoundError:
                ep_stats = {'script_status': 'none', 'storyboards': {'total': 0, 'completed': 0},
                            'videos': {'total': 0, 'completed': 0}, 'status': 'draft',
                            'scenes_count': 0, 'duration_seconds': 0}
        else:
            ep_stats = {'script_status': script_status, 'storyboards': {'total': 0, 'completed': 0},
                        'videos': {'total': 0, 'completed': 0}, 'status': 'draft',
                        'scenes_count': 0, 'duration_seconds': 0}
        episodes_stats.append(ep_stats)

    phase = self.calculate_current_phase(project, episodes_stats)
    phase_progress = self._calculate_phase_progress(project, phase, episodes_stats)

    return {
        'current_phase': phase,
        'phase_progress': phase_progress,
        'characters': {'total': chars_total, 'completed': chars_done},
        'clues': {'total': clues_total, 'completed': clues_done},
        'episodes_summary': {
            'total': len(episodes_stats),
            'scripted': sum(1 for s in episodes_stats if s['script_status'] == 'generated'),
            'in_production': sum(1 for s in episodes_stats if s['status'] == 'in_production'),
            'completed': sum(1 for s in episodes_stats if s['status'] == 'completed'),
        }
    }
```

**Important**: Keep the old `calculate_project_progress()` method but mark it as deprecated, in case it is still referenced elsewhere; delete it after Task 3.

**Step 4: Run to confirm passing**

```bash
python -m pytest tests/test_status_calculator.py -k "test_get_episode_script_status or test_calculate_current_phase or test_calculate_project_status" -v
```

Expected: All PASSED

**Step 5: Run all backend tests**

```bash
python -m pytest tests/test_status_calculator.py -v
```

If old tests fail (e.g. `test_calculate_project_progress_and_phase`), fix them to match the new structure.

`test_calculate_project_progress_and_phase` should now verify `calculate_project_status`:
- `status["current_phase"] == "completed"` (not the old `"compose"`)
- `status["characters"] == {"total": 2, "completed": 1}`
- `status["clues"] == {"total": 1, "completed": 1}`

`test_enrich_project_and_enrich_script` should now verify that `enrich_project` outputs new fields.

**Step 6: Commit**

```bash
git add lib/status_calculator.py tests/test_status_calculator.py
git commit -m "feat(status): add worldbuilding/scripting phases and calculate_project_status()"
```

---

## Task 3: Update `enrich_project()` + Delete `calculate_project_progress()`

**Files:**
- Modify: `lib/status_calculator.py:160-198`
- Test: `tests/test_status_calculator.py`

**Step 1: Update `enrich_project()` to inject new fields**

Replace `lib/status_calculator.py:160-198` with:

```python
def enrich_project(self, project_name: str, project: Dict) -> Dict:
    """
    Inject all computed fields into the project data (used for detail API).
    Does not modify the original JSON file; only used for API responses.
    """
    # Calculate per-episode details (inject into episode objects)
    episodes_stats = []
    for ep in project.get('episodes', []):
        script_file = ep.get('script_file', '')
        episode_num = ep.get('episode', 0)
        script_status = self._get_episode_script_status(project_name, episode_num, script_file) if script_file else 'none'

        if script_status == 'generated':
            try:
                script = self.pm.load_script(project_name, script_file)
                ep_stats = self.calculate_episode_stats(project_name, script)
                if ep_stats['status'] == 'draft':
                    ep_stats['status'] = 'scripted'
                ep_stats['script_status'] = 'generated'
            except FileNotFoundError:
                ep_stats = {'script_status': 'none', 'status': 'missing',
                            'storyboards': {'total': 0, 'completed': 0},
                            'videos': {'total': 0, 'completed': 0},
                            'scenes_count': 0, 'duration_seconds': 0}
        else:
            ep_stats = {'script_status': script_status, 'status': 'draft',
                        'storyboards': {'total': 0, 'completed': 0},
                        'videos': {'total': 0, 'completed': 0},
                        'scenes_count': 0, 'duration_seconds': 0}

        ep.update(ep_stats)
        episodes_stats.append(ep_stats)

    # Calculate project status
    project['status'] = self.calculate_project_status(project_name, project)
    return project
```

**Note**: `calculate_project_status` also iterates through episodes internally, creating duplicate traversal. This is acceptable (YAGNI) and will not be optimized for now.

**Step 2: Delete `calculate_project_progress()`**

Delete `lib/status_calculator.py:81-131` (the `calculate_project_progress` method).

**Step 3: Update tests**

Update `test_enrich_project_and_enrich_script`:

```python
def test_enrich_project(self, tmp_path):
    project_root = tmp_path / "projects"
    project_root.mkdir(parents=True)
    project = {
        "overview": {"synopsis": "test"},
        "episodes": [
            {"episode": 1, "script_file": "scripts/episode_1.json"},
            {"episode": 2, "script_file": "scripts/missing.json"},
        ],
        "characters": {},
        "clues": {},
    }
    script = {
        "content_mode": "narration",
        "segments": [
            {
                "segment_id": "E1S01",
                "duration_seconds": 6,
                "characters_in_segment": ["A", "B"],
                "clues_in_segment": ["C"],
                "generated_assets": {},
            }
        ],
    }
    calc = StatusCalculator(_FakePM(project_root, project, {"episode_1.json": script}))

    enriched = calc.enrich_project("demo", {**project, "episodes": [
        {"episode": 1, "script_file": "scripts/episode_1.json"},
        {"episode": 2, "script_file": "scripts/missing.json"},
    ]})

    assert "status" in enriched
    assert enriched["status"]["current_phase"] == "scripting"
    ep1 = enriched["episodes"][0]
    assert ep1["script_status"] == "generated"
    assert ep1["status"] == "scripted"
    assert ep1["scenes_count"] == 1
    assert ep1["storyboards"] == {"total": 1, "completed": 0}
    ep2 = enriched["episodes"][1]
    assert ep2["script_status"] == "none"
    assert ep2["status"] == "draft"
```

**Step 4: Run to confirm passing**

```bash
python -m pytest tests/test_status_calculator.py -v
```

Expected: All PASSED

**Step 5: Run all tests**

```bash
python -m pytest --tb=short -q
```

If other files still reference `calculate_project_progress`, fix them in the next step.

**Step 6: Commit**

```bash
git add lib/status_calculator.py tests/test_status_calculator.py
git commit -m "refactor(status): rewrite enrich_project and remove calculate_project_progress"
```

---

## Task 4: Update `server/routers/projects.py` list endpoint

**Files:**
- Modify: `server/routers/projects.py:204-215`

**Step 1: Update list endpoint**

Change `server/routers/projects.py:204-215` to use `calculate_project_status()`:

```python
# Before
progress = calculator.calculate_project_progress(name)
current_phase = calculator.calculate_current_phase(progress)

projects.append({
    "name": name,
    "title": project.get("title", name),
    "style": project.get("style", ""),
    "thumbnail": thumbnail,
    "progress": progress,
    "current_phase": current_phase
})
```

To:

```python
status = calculator.calculate_project_status(name, project)

projects.append({
    "name": name,
    "title": project.get("title", name),
    "style": project.get("style", ""),
    "thumbnail": thumbnail,
    "status": status,
})
```

Also update the fallback case for missing `project.json` (around line 218-226):

```python
# Before
projects.append({
    ...
    "progress": {},
    "current_phase": status.get("current_stage", "empty")
})

# After
projects.append({
    "name": name,
    "title": name,
    "style": "",
    "thumbnail": None,
    "status": {},
})
```

And the error case (around line 230-238):

```python
projects.append({
    "name": name,
    "title": name,
    "style": "",
    "thumbnail": None,
    "status": {},
    "error": str(e)
})
```

**Step 2: Run all tests**

```bash
python -m pytest --tb=short -q
```

**Step 3: Commit**

```bash
git add server/routers/projects.py
git commit -m "feat(api): projects list returns ProjectStatus instead of ProjectProgress"
```

---

## Task 5: Update frontend type definitions

**Files:**
- Modify: `frontend/src/types/project.ts`

**Step 1: Update type definitions**

Replace the entire `frontend/src/types/project.ts` with:

```typescript
/**
 * Project-related type definitions.
 *
 * Maps to backend models in:
 * - lib/project_manager.py (ProjectOverview, project.json structure)
 * - lib/status_calculator.py (ProjectStatus, EpisodeMeta computed fields)
 * - server/routers/projects.py (ProjectSummary list response)
 */

export interface ProjectOverview {
  synopsis: string;
  genre: string;
  theme: string;
  world_setting: string;
  generated_at?: string;
}

export interface Character {
  description: string;
  character_sheet?: string;
  voice_style?: string;
  reference_image?: string;
}

export interface Clue {
  type: "prop" | "location";
  description: string;
  importance: "major" | "minor";
  clue_sheet?: string;
}

export interface AspectRatio {
  characters?: string;
  clues?: string;
  storyboard?: string;
  video?: string;
}

export interface ProgressCategory {
  total: number;
  completed: number;
}

export interface EpisodesSummary {
  total: number;
  scripted: number;
  in_production: number;
  completed: number;
}

/** Injected by StatusCalculator.calculate_project_status at read time */
export interface ProjectStatus {
  current_phase: "setup" | "worldbuilding" | "scripting" | "production" | "completed";
  phase_progress: number;
  characters: ProgressCategory;
  clues: ProgressCategory;
  episodes_summary: EpisodesSummary;
}

export interface EpisodeMeta {
  episode: number;
  title: string;
  script_file: string;
  /** Injected by StatusCalculator at read time */
  scenes_count?: number;
  /** Injected by StatusCalculator at read time */
  script_status?: "none" | "segmented" | "generated";
  /** Injected by StatusCalculator at read time */
  status?: "draft" | "scripted" | "in_production" | "completed" | "missing";
  /** Injected by StatusCalculator at read time */
  duration_seconds?: number;
  /** Injected by StatusCalculator at read time */
  storyboards?: ProgressCategory;
  /** Injected by StatusCalculator at read time */
  videos?: ProgressCategory;
}

export interface ProjectData {
  title: string;
  content_mode: "narration" | "drama";
  style: string;
  style_image?: string;
  style_description?: string;
  overview?: ProjectOverview;
  aspect_ratio?: AspectRatio;
  episodes: EpisodeMeta[];
  characters: Record<string, Character>;
  clues: Record<string, Clue>;
  /** Injected by StatusCalculator.enrich_project at read time */
  status?: ProjectStatus;
  metadata?: {
    created_at: string;
    updated_at: string;
  };
}

/**
 * Summary shape returned by GET /api/v1/projects (list endpoint).
 *
 * Note: `status` may be an empty object `{}` when the project
 * has no project.json or encounters an error during loading.
 */
export interface ProjectSummary {
  name: string;
  title: string;
  style: string;
  thumbnail: string | null;
  status: ProjectStatus | Record<string, never>;
}

export type ImportConflictPolicy = "prompt" | "rename" | "overwrite";

export interface ImportProjectResponse {
  success: boolean;
  project_name: string;
  project: ProjectData;
  warnings: string[];
  conflict_resolution: "none" | "renamed" | "overwritten";
}
```

**Step 2: Run TypeScript check (there will be errors, fix them in the next step)**

```bash
cd frontend && pnpm typecheck 2>&1 | head -60
```

Record all type errors; fix them in Task 6.

**Step 3: Commit**

```bash
git add frontend/src/types/project.ts
git commit -m "feat(types): replace ProjectProgress with ProjectStatus, update EpisodeMeta"
```

---

## Task 6: Update `ProjectCard` component

**Files:**
- Modify: `frontend/src/components/pages/ProjectsPage.tsx:95-157`

**Step 1: Update `ProjectCard` function**

Replace the `ProjectCard` function in `ProjectsPage.tsx:95-157` with:

```tsx
// ---------------------------------------------------------------------------
// Phase display helpers
// ---------------------------------------------------------------------------

const PHASE_LABELS: Record<string, string> = {
  setup: "Setup",
  worldbuilding: "Worldbuilding",
  scripting: "Scripting",
  production: "In Production",
  completed: "Completed",
};

// ---------------------------------------------------------------------------
// ProjectCard — single project entry
// ---------------------------------------------------------------------------

function ProjectCard({ project }: { project: ProjectSummary }) {
  const [, navigate] = useLocation();
  const status = project.status;
  const hasStatus = status && "current_phase" in status;

  const pct = hasStatus ? Math.round((status as ProjectStatus).phase_progress * 100) : 0;
  const phase = hasStatus ? (status as ProjectStatus).current_phase : "";
  const phaseLabel = PHASE_LABELS[phase] ?? phase;
  const characters = hasStatus ? (status as ProjectStatus).characters : null;
  const clues = hasStatus ? (status as ProjectStatus).clues : null;
  const summary = hasStatus ? (status as ProjectStatus).episodes_summary : null;

  return (
    <button
      type="button"
      onClick={() => navigate(`/app/projects/${project.name}`)}
      className="flex flex-col gap-3 rounded-xl border border-gray-800 bg-gray-900 p-5 text-left transition-colors hover:border-indigo-500/50 hover:bg-gray-800/50 cursor-pointer"
    >
      {/* Thumbnail or placeholder */}
      <div className="aspect-video w-full overflow-hidden rounded-lg bg-gray-800">
        {project.thumbnail ? (
          <img
            src={project.thumbnail}
            alt={project.title}
            className="h-full w-full object-cover"
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-gray-600">
            <FolderOpen className="h-10 w-10" />
          </div>
        )}
      </div>

      {/* Info */}
      <div>
        <h3 className="font-semibold text-gray-100 truncate">{project.title}</h3>
        <p className="text-xs text-gray-500 mt-0.5">
          {project.style || "No style set"}
          {phaseLabel ? ` · ${phaseLabel}` : ""}
        </p>
      </div>

      {/* Progress bar */}
      <div>
        <div className="flex justify-between text-xs text-gray-500 mb-1">
          <span>{phaseLabel || "Progress"}</span>
          <span>{pct}%</span>
        </div>
        <div className="h-1.5 rounded-full bg-gray-800 overflow-hidden">
          <div
            className="h-full rounded-full bg-indigo-600 transition-all"
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>

      {/* Characters & Clues — always shown */}
      {(characters || clues) && (
        <div className="flex gap-3 text-xs text-gray-500">
          {characters && (
            <span>Characters {characters.completed}/{characters.total}</span>
          )}
          {clues && (
            <span>Clues {clues.completed}/{clues.total}</span>
          )}
        </div>
      )}

      {/* Episodes summary */}
      {summary && summary.total > 0 && (
        <div className="text-xs text-gray-500">
          {summary.total} ep{summary.total !== 1 ? "s" : ""}
          {summary.scripted > 0 && ` · ${summary.scripted} scripted`}
          {summary.in_production > 0 && ` · ${summary.in_production} in production`}
          {summary.completed > 0 && ` · ${summary.completed} completed`}
        </div>
      )}
    </button>
  );
}
```

Also add to the import at the top of the file:

```typescript
import type { ProjectSummary, ProjectStatus } from "@/types/project";
```

(If ProjectSummary is already imported, append ProjectStatus)

**Step 2: Update `GlobalHeader.tsx`**

In `frontend/src/components/layout/GlobalHeader.tsx:110`:

```typescript
// Before
const currentPhase = currentProjectData?.status?.current_phase;

// After (type unchanged, path unchanged, no modification needed)
```

Verify: `currentProjectData?.status?.current_phase` — in the new type, `status` is `ProjectStatus` and `current_phase` still exists, no modification needed.

**Step 3: Update `stores.test.ts`**

In `frontend/src/stores/stores.test.ts:147`, change the old `progress`/`current_phase` fields to `status`:

```typescript
// Before
{ name: "demo", title: "Demo", style: "Anime", thumbnail: null, progress: {}, current_phase: "start" }

// After
{ name: "demo", title: "Demo", style: "Anime", thumbnail: null, status: {} }
```

**Step 4: Run TypeScript check**

```bash
cd frontend && pnpm typecheck 2>&1 | head -60
```

Fix all type errors (mainly `project.progress` → `project.status`, `project.current_phase` → `(project.status as ProjectStatus).current_phase`).

**Step 5: Commit**

```bash
git add frontend/src/components/pages/ProjectsPage.tsx frontend/src/components/layout/GlobalHeader.tsx frontend/src/stores/stores.test.ts
git commit -m "feat(ui): update ProjectCard to use new ProjectStatus structure"
```

---

## Task 7: Update frontend tests + full validation

**Files:**
- Modify: `frontend/src/components/pages/ProjectsPage.test.tsx`

**Step 1: Update test fixtures**

Change all `current_phase` / `progress` fields to `status` objects in `ProjectsPage.test.tsx`.

Change the test at lines 54-77 to:

```typescript
vi.spyOn(API, "listProjects").mockResolvedValue({
  projects: [
    {
      name: "demo",
      title: "Demo Project",
      style: "Anime",
      thumbnail: null,
      status: {
        current_phase: "production",
        phase_progress: 0.5,
        characters: { total: 2, completed: 2 },
        clues: { total: 2, completed: 1 },
        episodes_summary: { total: 1, scripted: 1, in_production: 1, completed: 0 },
      },
    },
  ],
});
```

Assert `"50%"` instead of `"42%"` (update expected values to match `phase_progress`).

Change `current_phase: "storyboard"` at lines 103 and 179 to `status: { current_phase: "production", phase_progress: 1.0, characters: {...}, clues: {...}, episodes_summary: {...} }`.

**Step 2: Run frontend tests**

```bash
cd frontend && pnpm test --run 2>&1 | tail -30
```

**Step 3: Run full check**

```bash
cd frontend && pnpm check
```

**Step 4: Run all backend tests**

```bash
cd .worktrees/progress-redesign && python -m pytest --tb=short -q
```

Expected: 438+ passed, 0 failed

**Step 5: Commit**

```bash
git add frontend/src/components/pages/ProjectsPage.test.tsx
git commit -m "test(ui): update ProjectsPage tests to new ProjectStatus structure"
```

---

## Task 8: Final Validation

**Step 1: Backend full tests**

```bash
python -m pytest -v 2>&1 | tail -20
```

**Step 2: Frontend full check**

```bash
cd frontend && pnpm check
```

**Step 3: If all pass, push branch**

```bash
git log --oneline -8
```

Confirm the commit history is clean, then report completion.
