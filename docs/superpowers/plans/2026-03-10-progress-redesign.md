# 进度机制重新设计 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 重新设计进度机制，使其准确反映完整工作流（setup → worldbuilding → scripting → production → completed），并按集粒度追踪分镜/视频进度，角色/线索始终展示。

**Architecture:** 修改 `StatusCalculator` 引入 5 段阶段枚举、集级 `script_status` 字段、以及新的 `calculate_project_status()` 方法；同步更新前端类型和 `ProjectCard` 展示逻辑。读时计算策略不变，不存储冗余状态。

**Tech Stack:** Python 3.12 / pytest / TypeScript / React 19 / Tailwind CSS 4

**Design Doc:** `docs/plans/2026-03-10-progress-redesign-design.md`

---

## Task 1: 更新 `calculate_episode_stats()` 返回结构

**Files:**
- Modify: `lib/status_calculator.py:40-79`
- Test: `tests/test_status_calculator.py`

**Step 1: 写失败测试（新返回结构）**

在 `tests/test_status_calculator.py` 的 `TestStatusCalculator` 类中，将现有的 `test_calculate_episode_stats_statuses` 替换为：

```python
def test_calculate_episode_stats_statuses(self, tmp_path):
    calc = StatusCalculator(_FakePM(tmp_path, {}, {}))

    # draft：无任何资源
    draft = calc.calculate_episode_stats(
        "demo",
        {"content_mode": "narration", "segments": [{"duration_seconds": 4}]},
    )
    assert draft["status"] == "draft"
    assert draft["storyboards"] == {"total": 1, "completed": 0}
    assert draft["videos"] == {"total": 1, "completed": 0}
    assert draft["scenes_count"] == 1
    assert draft["duration_seconds"] == 4

    # scripted：有剧本但无任何分镜/视频资源
    # （script_status 由 enrich_project 设置，calculate_episode_stats 不设置）
    # draft 状态在加载脚本成功时是 "draft"

    # in_production：有分镜图
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

    # completed：所有场景有视频
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

**Step 2: 运行确认失败**

```bash
cd .worktrees/progress-redesign
python -m pytest tests/test_status_calculator.py::TestStatusCalculator::test_calculate_episode_stats_statuses -v
```

期望：FAILED（`storyboards` key 不存在，返回的是 `storyboards_completed`）

**Step 3: 修改 `calculate_episode_stats()` 实现**

将 `lib/status_calculator.py:40-79` 中的返回值从：
```python
return {
    'scenes_count': total,
    'status': status,
    'duration_seconds': ...,
    'storyboards_completed': storyboard_done,
    'videos_completed': video_done
}
```

改为：
```python
return {
    'scenes_count': total,
    'status': status,
    'duration_seconds': sum(i.get('duration_seconds', default_duration) for i in items),
    'storyboards': {'total': total, 'completed': storyboard_done},
    'videos': {'total': total, 'completed': video_done},
}
```

同时更新 `status` 判断（新增 `scripted` — 有剧本无资源时由 `enrich_project` 覆盖，此处保留 `draft`）：

```python
# 计算状态（不含 scripted，由 enrich_project 覆盖）
if video_done == total and total > 0:
    status = 'completed'
elif storyboard_done > 0 or video_done > 0:
    status = 'in_production'
else:
    status = 'draft'
```

**Step 4: 运行确认通过**

```bash
python -m pytest tests/test_status_calculator.py::TestStatusCalculator::test_calculate_episode_stats_statuses -v
```

期望：PASSED

**Step 5: 运行全部测试（可能有其他测试引用旧字段，先记录再继续）**

```bash
python -m pytest tests/test_status_calculator.py -v
```

记录失败项，Task 2 统一修复。

**Step 6: Commit**

```bash
git add lib/status_calculator.py tests/test_status_calculator.py
git commit -m "refactor(status): calculate_episode_stats returns storyboards/videos as objects"
```

---

## Task 2: 新增 `_get_episode_script_status()` + 重写阶段逻辑

**Files:**
- Modify: `lib/status_calculator.py`
- Test: `tests/test_status_calculator.py`

新增三个方法，替换 `calculate_project_progress()` 和旧的 `calculate_current_phase()`。

**Step 1: 写失败测试（新阶段枚举 + script_status）**

在 `TestStatusCalculator` 中，将 `test_calculate_project_progress_and_phase` 替换为：

```python
def test_get_episode_script_status(self, tmp_path):
    project_root = tmp_path / "projects"
    project_path = project_root / "demo"

    # Case 1: 脚本 JSON 存在 → "generated"
    scripts = {"episode_1.json": {"content_mode": "narration", "segments": []}}
    calc = StatusCalculator(_FakePM(project_root, {}, scripts))
    assert calc._get_episode_script_status("demo", 1, "scripts/episode_1.json") == "generated"

    # Case 2: 脚本不存在，draft 文件存在 → "segmented"
    draft_dir = project_path / "drafts" / "episode_2"
    draft_dir.mkdir(parents=True)
    (draft_dir / "step1_segments.md").write_text("ok")
    calc2 = StatusCalculator(_FakePM(project_root, {}, {}))
    assert calc2._get_episode_script_status("demo", 2, "scripts/episode_2.json") == "segmented"

    # Case 3: 两者都不存在 → "none"
    calc3 = StatusCalculator(_FakePM(project_root, {}, {}))
    assert calc3._get_episode_script_status("demo", 3, "scripts/episode_3.json") == "none"

def test_calculate_current_phase_setup(self, tmp_path):
    calc = StatusCalculator(_FakePM(tmp_path, {}, {}))
    project_no_overview = {}
    assert calc.calculate_current_phase(project_no_overview, []) == "setup"

def test_calculate_current_phase_worldbuilding(self, tmp_path):
    calc = StatusCalculator(_FakePM(tmp_path, {}, {}))
    project = {"overview": {"synopsis": "test"}}
    # 无任何 generated 脚本 → worldbuilding
    episodes_stats = [{"script_status": "none"}, {"script_status": "segmented"}]
    assert calc.calculate_current_phase(project, episodes_stats) == "worldbuilding"
    # 无集 → worldbuilding
    assert calc.calculate_current_phase(project, []) == "worldbuilding"

def test_calculate_current_phase_scripting(self, tmp_path):
    calc = StatusCalculator(_FakePM(tmp_path, {}, {}))
    project = {"overview": {"synopsis": "test"}}
    # 有至少一集 generated，但未全部 → scripting
    episodes_stats = [
        {"script_status": "generated", "status": "draft"},
        {"script_status": "none"},
    ]
    assert calc.calculate_current_phase(project, episodes_stats) == "scripting"

def test_calculate_current_phase_production_and_completed(self, tmp_path):
    calc = StatusCalculator(_FakePM(tmp_path, {}, {}))
    project = {"overview": {"synopsis": "test"}}
    # 全部 generated，有未完成视频 → production
    episodes_stats = [
        {"script_status": "generated", "status": "in_production"},
        {"script_status": "generated", "status": "draft"},
    ]
    assert calc.calculate_current_phase(project, episodes_stats) == "production"
    # 全部 completed → completed
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

**Step 2: 运行确认失败**

```bash
python -m pytest tests/test_status_calculator.py -k "test_get_episode_script_status or test_calculate_current_phase or test_calculate_project_status" -v
```

期望：全部 FAILED

**Step 3: 实现新方法**

在 `lib/status_calculator.py` 中，添加以下方法（替换 `calculate_project_progress` 和 `calculate_current_phase`）：

```python
def _get_episode_script_status(self, project_name: str, episode_num: int, script_file: str) -> str:
    """判断单集剧本状态: 'generated' | 'segmented' | 'none'"""
    try:
        self.pm.load_script(project_name, script_file)
        return 'generated'
    except FileNotFoundError:
        project_dir = self.pm.get_project_path(project_name)
        draft_file = project_dir / f'drafts/episode_{episode_num}/step1_segments.md'
        return 'segmented' if draft_file.exists() else 'none'

def calculate_current_phase(self, project: Dict, episodes_stats: List[Dict]) -> str:
    """根据项目和集状态推断当前阶段"""
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
    """计算当前阶段完成率 0.0–1.0"""
    if phase == 'setup':
        # 有源文件 → 0.5；否则 0.0（概述完成才切换阶段，不会到 1.0）
        project_dir = self.pm.get_project_path('_placeholder')  # 不用实际路径
        return 0.0  # setup 阶段不关注源文件，简化处理
    if phase == 'worldbuilding':
        chars = project.get('characters', {})
        clues_major = [c for c in project.get('clues', {}).values() if c.get('importance') == 'major']
        total = len(chars) + len(clues_major)
        if total == 0:
            return 0.0
        # 需要文件系统检查，此处通过 episodes_stats 无法得到，返回 0.0 作为保守值
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
    计算项目整体状态（用于列表 API）。

    Returns:
        ProjectStatus 字典：current_phase, phase_progress, characters, clues, episodes_summary
    """
    project_dir = self.pm.get_project_path(project_name)

    # 角色统计
    chars = project.get('characters', {})
    chars_total = len(chars)
    chars_done = sum(
        1 for c in chars.values()
        if c.get('character_sheet') and (project_dir / c['character_sheet']).exists()
    )

    # 线索统计（所有线索，不限 major）
    clues = project.get('clues', {})
    clues_total = len(clues)
    clues_done = sum(
        1 for c in clues.values()
        if c.get('clue_sheet') and (project_dir / c['clue_sheet']).exists()
    )

    # 每集状态
    episodes_stats = []
    for ep in project.get('episodes', []):
        script_file = ep.get('script_file', '')
        episode_num = ep.get('episode', 0)
        script_status = self._get_episode_script_status(project_name, episode_num, script_file) if script_file else 'none'

        if script_status == 'generated':
            try:
                script = self.pm.load_script(project_name, script_file)
                ep_stats = self.calculate_episode_stats(project_name, script)
                # script 能加载说明是 generated，状态由 calculate_episode_stats 决定
                # 但若无任何资源，状态应为 scripted（不是 draft）
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

**重要**：旧的 `calculate_project_progress()` 方法保留但标记为 deprecated，以防其他地方仍有引用；在 Task 3 后再删除。

**Step 4: 运行确认通过**

```bash
python -m pytest tests/test_status_calculator.py -k "test_get_episode_script_status or test_calculate_current_phase or test_calculate_project_status" -v
```

期望：全部 PASSED

**Step 5: 运行全部后端测试**

```bash
python -m pytest tests/test_status_calculator.py -v
```

若有旧测试失败（如 `test_calculate_project_progress_and_phase`），按新结构修复。

`test_calculate_project_progress_and_phase` 改为验证 `calculate_project_status`：
- `status["current_phase"] == "completed"`（不是旧的 `"compose"`）
- `status["characters"] == {"total": 2, "completed": 1}`
- `status["clues"] == {"total": 1, "completed": 1}`

`test_enrich_project_and_enrich_script` 改为验证 `enrich_project` 输出新字段。

**Step 6: Commit**

```bash
git add lib/status_calculator.py tests/test_status_calculator.py
git commit -m "feat(status): add worldbuilding/scripting phases and calculate_project_status()"
```

---

## Task 3: 更新 `enrich_project()` + 删除 `calculate_project_progress()`

**Files:**
- Modify: `lib/status_calculator.py:160-198`
- Test: `tests/test_status_calculator.py`

**Step 1: 更新 `enrich_project()` 注入新字段**

将 `lib/status_calculator.py:160-198` 替换为：

```python
def enrich_project(self, project_name: str, project: Dict) -> Dict:
    """
    为项目数据注入所有计算字段（用于详情 API）。
    不修改原始 JSON 文件，仅用于 API 响应。
    """
    # 计算每集明细（注入到 episode 对象）
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

    # 计算项目状态
    project['status'] = self.calculate_project_status(project_name, project)
    return project
```

**注意**：`calculate_project_status` 内部也会遍历 episodes，存在重复遍历。这是可接受的（YAGNI），暂不优化。

**Step 2: 删除 `calculate_project_progress()`**

删除 `lib/status_calculator.py:81-131`（`calculate_project_progress` 方法）。

**Step 3: 更新测试**

更新 `test_enrich_project_and_enrich_script`：

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

**Step 4: 运行确认通过**

```bash
python -m pytest tests/test_status_calculator.py -v
```

期望：全部 PASSED

**Step 5: 运行全量测试**

```bash
python -m pytest --tb=short -q
```

若有其他文件仍引用 `calculate_project_progress`，下一步修复。

**Step 6: Commit**

```bash
git add lib/status_calculator.py tests/test_status_calculator.py
git commit -m "refactor(status): rewrite enrich_project and remove calculate_project_progress"
```

---

## Task 4: 更新 `server/routers/projects.py` 列表端点

**Files:**
- Modify: `server/routers/projects.py:204-215`

**Step 1: 更新列表端点**

将 `server/routers/projects.py:204-215` 改为使用 `calculate_project_status()`：

```python
# 之前
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

改为：

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

同时更新没有 `project.json` 的降级情况（约 line 218-226）：

```python
# 之前
projects.append({
    ...
    "progress": {},
    "current_phase": status.get("current_stage", "empty")
})

# 之后
projects.append({
    "name": name,
    "title": name,
    "style": "",
    "thumbnail": None,
    "status": {},
})
```

以及 error 情况（约 line 230-238）：

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

**Step 2: 运行全量测试**

```bash
python -m pytest --tb=short -q
```

**Step 3: Commit**

```bash
git add server/routers/projects.py
git commit -m "feat(api): projects list returns ProjectStatus instead of ProjectProgress"
```

---

## Task 5: 更新前端类型定义

**Files:**
- Modify: `frontend/src/types/project.ts`

**Step 1: 更新类型定义**

将 `frontend/src/types/project.ts` 全文替换为：

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

**Step 2: 运行 TypeScript 检查（会有错误，下一步修复）**

```bash
cd frontend && pnpm typecheck 2>&1 | head -60
```

记录所有类型错误，Task 6 修复。

**Step 3: Commit**

```bash
git add frontend/src/types/project.ts
git commit -m "feat(types): replace ProjectProgress with ProjectStatus, update EpisodeMeta"
```

---

## Task 6: 更新 `ProjectCard` 组件

**Files:**
- Modify: `frontend/src/components/pages/ProjectsPage.tsx:95-157`

**Step 1: 更新 `ProjectCard` 函数**

将 `ProjectsPage.tsx:95-157` 中的 `ProjectCard` 函数替换为：

```tsx
// ---------------------------------------------------------------------------
// Phase display helpers
// ---------------------------------------------------------------------------

const PHASE_LABELS: Record<string, string> = {
  setup: "准备中",
  worldbuilding: "世界观",
  scripting: "剧本创作",
  production: "制作中",
  completed: "已完成",
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
          {project.style || "未设置风格"}
          {phaseLabel ? ` · ${phaseLabel}` : ""}
        </p>
      </div>

      {/* Progress bar */}
      <div>
        <div className="flex justify-between text-xs text-gray-500 mb-1">
          <span>{phaseLabel || "进度"}</span>
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
            <span>角色 {characters.completed}/{characters.total}</span>
          )}
          {clues && (
            <span>线索 {clues.completed}/{clues.total}</span>
          )}
        </div>
      )}

      {/* Episodes summary */}
      {summary && summary.total > 0 && (
        <div className="text-xs text-gray-500">
          {summary.total} 集
          {summary.scripted > 0 && ` · ${summary.scripted} 集剧本完成`}
          {summary.in_production > 0 && ` · ${summary.in_production} 集制作中`}
          {summary.completed > 0 && ` · ${summary.completed} 集已完成`}
        </div>
      )}
    </button>
  );
}
```

同时在文件顶部 import 处加入：

```typescript
import type { ProjectSummary, ProjectStatus } from "@/types/project";
```

（若已有 import ProjectSummary，追加 ProjectStatus）

**Step 2: 更新 `GlobalHeader.tsx`**

`frontend/src/components/layout/GlobalHeader.tsx:110` 中：

```typescript
// 之前
const currentPhase = currentProjectData?.status?.current_phase;

// 之后（类型不变，路径不变，无需修改）
```

检查一下：`currentProjectData?.status?.current_phase` — 在新类型中 `status` 是 `ProjectStatus`，`current_phase` 仍存在，无需修改。

**Step 3: 更新 `stores.test.ts`**

`frontend/src/stores/stores.test.ts:147`，将旧 `progress`/`current_phase` 字段改为 `status`：

```typescript
// 之前
{ name: "demo", title: "Demo", style: "Anime", thumbnail: null, progress: {}, current_phase: "start" }

// 之后
{ name: "demo", title: "Demo", style: "Anime", thumbnail: null, status: {} }
```

**Step 4: 运行 TypeScript 检查**

```bash
cd frontend && pnpm typecheck 2>&1 | head -60
```

修复所有类型错误（主要是 `project.progress` → `project.status`，`project.current_phase` → `(project.status as ProjectStatus).current_phase`）。

**Step 5: Commit**

```bash
git add frontend/src/components/pages/ProjectsPage.tsx frontend/src/components/layout/GlobalHeader.tsx frontend/src/stores/stores.test.ts
git commit -m "feat(ui): update ProjectCard to use new ProjectStatus structure"
```

---

## Task 7: 更新前端测试 + 全量验证

**Files:**
- Modify: `frontend/src/components/pages/ProjectsPage.test.tsx`

**Step 1: 更新测试 fixture**

将 `ProjectsPage.test.tsx` 中所有 `current_phase` / `progress` 字段改为 `status` 对象。

Line 54-77 的测试改为：

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

断言 `"50%"` 而不是 `"42%"`（相应修改期望值以匹配 `phase_progress`）。

Line 103 和 line 179 的 `current_phase: "storyboard"` 改为 `status: { current_phase: "production", phase_progress: 1.0, characters: {...}, clues: {...}, episodes_summary: {...} }`。

**Step 2: 运行前端测试**

```bash
cd frontend && pnpm test --run 2>&1 | tail -30
```

**Step 3: 运行完整检查**

```bash
cd frontend && pnpm check
```

**Step 4: 运行全量后端测试**

```bash
cd .worktrees/progress-redesign && python -m pytest --tb=short -q
```

期望：438+ passed, 0 failed

**Step 5: Commit**

```bash
git add frontend/src/components/pages/ProjectsPage.test.tsx
git commit -m "test(ui): update ProjectsPage tests to new ProjectStatus structure"
```

---

## Task 8: 最终验证

**Step 1: 后端全量测试**

```bash
python -m pytest -v 2>&1 | tail -20
```

**Step 2: 前端全量检查**

```bash
cd frontend && pnpm check
```

**Step 3: 如全部通过，推送分支**

```bash
git log --oneline -8
```

确认提交历史清晰，然后报告完成。
