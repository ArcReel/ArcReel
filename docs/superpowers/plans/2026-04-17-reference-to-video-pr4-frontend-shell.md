# 参考生视频 PR4：前端框架（模式选择器 + Canvas 骨架）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让用户能在 UI 里切换到「参考生视频」模式，并看到基础 `ReferenceVideoCanvas` 三栏骨架（unit 列表 + 中栏占位 + 右栏预览占位），不含 prompt 编辑与 `@` 提及 — 后者在 PR5 补齐。

**Architecture:**
1. 新增共享组件 `GenerationModeSelector`（三选：图生视频 / 宫格生视频 / 参考生视频），同时用于项目新建向导 Step1 与 `ProjectSettingsPage`，对应的 `ProjectData.generation_mode` 扩展为 `"storyboard" | "grid" | "reference_video"`（兼容旧值 `"single"` → 显示为 `storyboard`）。
2. 新增 `EpisodeModeSwitcher`（集级分段控制），写 `episodes[i].generation_mode`；配合 `StudioCanvasRouter` 在 `/episodes/:id` 路由按 `effective_mode` 切换到 `TimelineCanvas` 或新 `ReferenceVideoCanvas`。
3. 新增 `ReferenceVideoCanvas` 三栏骨架：左栏 `UnitList`（列表 + 状态点 + prompt 预览）、中栏 placeholder（编辑器留空卡位，PR5 接入）、右栏 `UnitPreviewPanel`（视频/元数据）；数据层由 `reference-video-store` (zustand) + `API.referenceVideos.*` 新增方法承载。
4. 后端 `StatusCalculator` 增加 `reference_video` 分支：按 `video_units[*].generated_assets.video_clip` 统计 `videos.completed/total`，并把 `units_count` 注入 episode 级状态。

**Tech Stack:** React 19 + TypeScript、zustand、wouter、Tailwind CSS 4、vitest + React Testing Library、i18next；后端改动仅 `lib/status_calculator.py` + 测试。

## 参考文档

- Spec：`docs/superpowers/specs/2026-04-15-reference-to-video-mode-design.md`（§4.6 effective_mode、§6.1 模式选择器、§6.2 Canvas 三栏布局、§6.4 StatusCalculator 与 i18n）
- Roadmap：`docs/superpowers/plans/2026-04-17-reference-to-video-roadmap.md`（PR4 章节）
- PR2 数据模型：`lib/script_models.py`（已落地 `ReferenceVideoScript` / `ReferenceVideoUnit` / `Shot`）、`lib/asset_types.py`（`ASSET_TYPES` / `BUCKET_KEY` / `SHEET_KEY`）
- PR3 后端：`server/routers/reference_videos.py`（6 个端点）、`lib/project_manager.py:effective_mode()`

## 非目标（留给 PR5）

- prompt 编辑器（Shot/`@` 高亮、debounce 保存、`MentionPicker`）
- references 面板拖拽换序
- `ReferenceVideoCard` 的三色高亮/警告 chip
- PR4 只建好骨架 + mock 渲染 + 切换路由，数据读出 + 状态点 + 删除/新建 unit 入口保留但样式不完善

## 前后端契约概要（用于 API 封装）

| Method | Path | Body/Response |
|---|---|---|
| GET  | `/api/v1/projects/:name/reference-videos/episodes/:ep/units` | `{ units: Unit[] }` |
| POST | `/api/v1/projects/:name/reference-videos/episodes/:ep/units` | `{ prompt, references, duration_seconds?, transition_to_next?, note? }` → `{ unit }` |
| PATCH | `/api/v1/projects/:name/reference-videos/episodes/:ep/units/:id` | `{ prompt?, references?, duration_seconds?, transition_to_next?, note? }` → `{ unit }` |
| DELETE | `/api/v1/projects/:name/reference-videos/episodes/:ep/units/:id` | 204 |
| POST | `/api/v1/projects/:name/reference-videos/episodes/:ep/units/reorder` | `{ unit_ids: string[] }` → `{ units }` |
| POST | `/api/v1/projects/:name/reference-videos/episodes/:ep/units/:id/generate` | 202 → `{ task_id, deduped }` |

Unit DTO 字段（对齐 `server/routers/reference_videos.py:_build_unit_dict`）：
```ts
type Shot = { duration: number; text: string };
type ReferenceResource = { type: "character" | "scene" | "prop"; name: string };
type GenerationStatus = "pending" | "running" | "ready" | "failed";
type ReferenceVideoUnit = {
  unit_id: string;                          // "E{episode}U{index}"
  shots: Shot[];
  references: ReferenceResource[];
  duration_seconds: number;
  duration_override: boolean;
  transition_to_next: "cut" | "fade" | "dissolve";
  note: string | null;
  generated_assets: {
    storyboard_image: string | null;
    storyboard_last_image: string | null;
    grid_id: string | null;
    grid_cell_index: number | null;
    video_clip: string | null;
    video_uri: string | null;
    status: GenerationStatus;
  };
};
```

## 文件结构

### 新增

| 文件 | 职责 |
|---|---|
| `frontend/src/components/shared/GenerationModeSelector.tsx` | 受控三选按钮 + 描述文案；同时服务项目级与集级使用；带 `size: "lg" \| "sm"` prop |
| `frontend/src/components/shared/GenerationModeSelector.test.tsx` | 渲染/点击切换/键盘导航/`disabledModes` prop |
| `frontend/src/components/canvas/EpisodeModeSwitcher.tsx` | 集级分段控制；受控组件；调用 `API.updateProject()` 更新 `episodes[].generation_mode` |
| `frontend/src/components/canvas/EpisodeModeSwitcher.test.tsx` | 继承项目级的初始值；切换后 PATCH 请求体正确 |
| `frontend/src/components/canvas/reference/ReferenceVideoCanvas.tsx` | 三栏布局 + `useReferenceVideoStore` 订阅 + unit 选择状态 |
| `frontend/src/components/canvas/reference/ReferenceVideoCanvas.test.tsx` | 加载 / 空态 / 选择 unit 切换右栏；Mock store 驱动 |
| `frontend/src/components/canvas/reference/UnitList.tsx` | 左栏：状态点 + unit_id + duration + prompt 前两行 + references pills |
| `frontend/src/components/canvas/reference/UnitList.test.tsx` | render / 选中高亮 / "新建 Unit" 按钮回调 |
| `frontend/src/components/canvas/reference/UnitPreviewPanel.tsx` | 右栏：视频 `<video>` 或占位、`generate` 按钮、版本信息 |
| `frontend/src/components/canvas/reference/UnitPreviewPanel.test.tsx` | pending/ready 两种态的按钮行为 |
| `frontend/src/types/reference-video.ts` | TS 类型：`Shot` / `ReferenceResource` / `ReferenceVideoUnit` / `ReferenceVideoScript` / `GenerationStatus` |
| `frontend/src/stores/reference-video-store.ts` | zustand store：`unitsByEpisode` / `selectedUnitId` / `loadUnits` / `addUnit` / `patchUnit` / `deleteUnit` / `reorderUnits` / `generate` |
| `frontend/src/stores/reference-video-store.test.ts` | store 各 action 的成功/错误路径 |
| `tests/lib/test_status_calculator_reference.py` | `reference_video` 分支的 episode 统计 |

### 改动

| 文件 | 改动要点 |
|---|---|
| `frontend/src/types/project.ts` | `ProjectData.generation_mode` 扩展为 `"storyboard" \| "grid" \| "reference_video" \| "single"`（`single` 仅兼容旧数据）；`EpisodeMeta` 增 `generation_mode?`、`units_count?`（StatusCalculator 注入） |
| `frontend/src/api.ts` | 新增 `CreateProjectPayload.generation_mode` 扩展到三值；新增静态方法 `listReferenceVideoUnits` / `addReferenceVideoUnit` / `patchReferenceVideoUnit` / `deleteReferenceVideoUnit` / `reorderReferenceVideoUnits` / `generateReferenceVideoUnit` |
| `frontend/src/components/pages/create-project/WizardStep1Basics.tsx` | `WizardStep1Value.generationMode` 类型切到新枚举；用 `GenerationModeSelector` 替换旧 radio；默认值 `storyboard` |
| `frontend/src/components/pages/CreateProjectModal.tsx` | 默认 `generationMode: "storyboard"`；`handleSubmit` 透传新值（兼容后端接受字符串） |
| `frontend/src/components/pages/CreateProjectModal.test.tsx` | 更新 fixture 里的 `generation_mode: "storyboard"` |
| `frontend/src/components/pages/ProjectSettingsPage.tsx` | 用 `GenerationModeSelector` 替换旧 radio；`gm` 初始值兼容 `"single"` → `"storyboard"` |
| `frontend/src/components/pages/ProjectSettingsPage.test.tsx` | 覆盖参考模式切换 dirty 状态 |
| `frontend/src/components/canvas/timeline/TimelineCanvas.tsx` | `isGridMode` 改为基于 `effective_mode` 计算（受 episode 覆盖） |
| `frontend/src/components/canvas/StudioCanvasRouter.tsx` | `/episodes/:id` 路由按 `effective_mode(projectData, episode)` 选 `TimelineCanvas` 或 `ReferenceVideoCanvas`；在 toolbar 渲染 `EpisodeModeSwitcher` |
| `frontend/src/utils/generation-mode.ts` (新) | 纯工具函数 `effectiveMode(project, episode)` + `normalizeMode(value)`（旧 `"single"` → `"storyboard"`） |
| `frontend/src/utils/generation-mode.test.ts` (新) | 继承 / 回退 / 旧值归一 |
| `frontend/src/i18n/zh/dashboard.ts` | 新 key：`mode_storyboard`、`mode_grid`、`mode_reference_video`、`mode_storyboard_desc`、`mode_grid_desc`、`mode_reference_video_desc`、`reference_canvas_empty`、`reference_unit_list_title`、`reference_unit_new`、`reference_preview_empty`、`reference_preview_generate`、`reference_preview_generating`、`reference_units_count`、`reference_status_pending`、`reference_status_running`、`reference_status_ready`、`reference_status_failed` |
| `frontend/src/i18n/en/dashboard.ts` | 与 zh 同一批 key 英文文案 |
| `lib/status_calculator.py` | 新增 `_calculate_reference_video_stats(script)`，`enrich_project` / `_build_episodes_stats` 按 `effective_mode` 分派 |

### 不变

- PR2 的 `lib/script_models.py`、`lib/reference_video/shot_parser.py` 不动
- PR3 的 `server/routers/reference_videos.py`、`server/services/reference_video_tasks.py` 不动
- `frontend/src/stores/assets-store.ts`（MentionPicker 在 PR5 再消费）
- `AssetSidebar`、`AssetLibraryPage`、`lib/asset_types.py`

---

## 分阶段任务

TDD 约束：每个组件/工具函数必须按 "先写失败测试 → 最小实现通过测试 → 提交" 的节奏推进。所有代码步骤必须给出完整代码块，禁止 TODO / 省略号。

### Task 1：生成模式工具函数（前端）

**Files:**
- Create: `frontend/src/utils/generation-mode.ts`
- Test: `frontend/src/utils/generation-mode.test.ts`

- [ ] **Step 1: 写失败测试**

```ts
// frontend/src/utils/generation-mode.test.ts
import { describe, it, expect } from "vitest";
import { effectiveMode, normalizeMode, type GenerationMode } from "./generation-mode";

describe("normalizeMode", () => {
  it("maps legacy 'single' to 'storyboard'", () => {
    expect(normalizeMode("single")).toBe("storyboard");
  });
  it("keeps canonical values", () => {
    for (const m of ["storyboard", "grid", "reference_video"] as GenerationMode[]) {
      expect(normalizeMode(m)).toBe(m);
    }
  });
  it("returns 'storyboard' for undefined/null/unknown", () => {
    expect(normalizeMode(undefined)).toBe("storyboard");
    expect(normalizeMode(null)).toBe("storyboard");
    expect(normalizeMode("weird")).toBe("storyboard");
  });
});

describe("effectiveMode", () => {
  it("prefers episode.generation_mode over project.generation_mode", () => {
    expect(effectiveMode({ generation_mode: "grid" }, { generation_mode: "reference_video" }))
      .toBe("reference_video");
  });
  it("falls back to project mode if episode has none", () => {
    expect(effectiveMode({ generation_mode: "reference_video" }, {})).toBe("reference_video");
  });
  it("falls back to 'storyboard' when both missing", () => {
    expect(effectiveMode({}, {})).toBe("storyboard");
  });
  it("normalizes legacy 'single' on both levels", () => {
    expect(effectiveMode({ generation_mode: "single" }, {})).toBe("storyboard");
    expect(effectiveMode({}, { generation_mode: "single" })).toBe("storyboard");
  });
});
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd frontend && pnpm vitest run src/utils/generation-mode.test.ts`
Expected: FAIL — "Cannot find module './generation-mode'"

- [ ] **Step 3: 写最小实现**

```ts
// frontend/src/utils/generation-mode.ts
/**
 * Generation mode helpers — mirrors lib/project_manager.py:effective_mode().
 *
 * Canonical values: "storyboard" | "grid" | "reference_video".
 * Legacy value "single" (old projects) is normalized to "storyboard".
 */

export type GenerationMode = "storyboard" | "grid" | "reference_video";

const CANONICAL: readonly GenerationMode[] = ["storyboard", "grid", "reference_video"];

export function normalizeMode(value: unknown): GenerationMode {
  if (value === "single") return "storyboard";
  if (typeof value === "string" && (CANONICAL as readonly string[]).includes(value)) {
    return value as GenerationMode;
  }
  return "storyboard";
}

export function effectiveMode(
  project: { generation_mode?: string | null } | null | undefined,
  episode: { generation_mode?: string | null } | null | undefined,
): GenerationMode {
  const ep = episode?.generation_mode;
  if (typeof ep === "string") {
    const normalized = normalizeMode(ep);
    // only respect episode override if it's a valid mode string
    if (ep === "single" || (CANONICAL as readonly string[]).includes(ep)) return normalized;
  }
  const proj = project?.generation_mode;
  if (typeof proj === "string") {
    const normalized = normalizeMode(proj);
    if (proj === "single" || (CANONICAL as readonly string[]).includes(proj)) return normalized;
  }
  return "storyboard";
}
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd frontend && pnpm vitest run src/utils/generation-mode.test.ts`
Expected: PASS — 7 tests

- [ ] **Step 5: 提交**

```bash
git add frontend/src/utils/generation-mode.ts frontend/src/utils/generation-mode.test.ts
git commit -m "feat(frontend): add generation-mode helpers for reference_video PR4"
```

---

### Task 2：扩展前端类型定义

**Files:**
- Modify: `frontend/src/types/project.ts`
- Create: `frontend/src/types/reference-video.ts`
- Modify: `frontend/src/types/index.ts`

- [ ] **Step 1: 扩展 ProjectData / EpisodeMeta**

Open `frontend/src/types/project.ts:65-110` and change:

```ts
// frontend/src/types/project.ts（修改片段）
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
  /** Injected by StatusCalculator at read time (reference_video mode only) */
  units_count?: number;
  /** Optional episode-level override; falls back to project.generation_mode */
  generation_mode?: "storyboard" | "grid" | "reference_video";
}
```

And the `ProjectData.generation_mode` field:

```ts
// frontend/src/types/project.ts:102（修改片段）
  /** Canonical values: storyboard | grid | reference_video. "single" is legacy-only. */
  generation_mode?: "storyboard" | "grid" | "reference_video" | "single";
```

- [ ] **Step 2: 新增 reference-video 类型文件**

```ts
// frontend/src/types/reference-video.ts
/**
 * Reference-to-video unit types — mirrors lib/script_models.py Pydantic models.
 *
 * One "unit" produces one rendered video clip. Each unit may contain 1-4 shots.
 */

export type AssetKind = "character" | "scene" | "prop";

export interface Shot {
  /** 1-15s per shot */
  duration: number;
  /** Raw prompt text including @mentions */
  text: string;
}

export interface ReferenceResource {
  type: AssetKind;
  /** Must already exist in project.json {characters|scenes|props} bucket */
  name: string;
}

export type UnitStatus = "pending" | "running" | "ready" | "failed";

export interface UnitGeneratedAssets {
  storyboard_image: string | null;
  storyboard_last_image: string | null;
  grid_id: string | null;
  grid_cell_index: number | null;
  video_clip: string | null;
  video_uri: string | null;
  status: UnitStatus;
}

export interface ReferenceVideoUnit {
  /** Format: "E{episode}U{index}" */
  unit_id: string;
  shots: Shot[];
  /** Ordered — position defines [图N] index in the final prompt */
  references: ReferenceResource[];
  /** Sum of shots[].duration; server-derived */
  duration_seconds: number;
  /** True when prompt has no Shot markers and user set duration manually */
  duration_override: boolean;
  transition_to_next: "cut" | "fade" | "dissolve";
  note: string | null;
  generated_assets: UnitGeneratedAssets;
}

export interface ReferenceVideoScript {
  episode: number;
  title: string;
  content_mode: "reference_video";
  duration_seconds: number;
  summary: string;
  schema_version?: number;
  novel: { title: string; chapter: string };
  video_units: ReferenceVideoUnit[];
}
```

- [ ] **Step 3: 在 types barrel 中 re-export**

Open `frontend/src/types/index.ts` and add (append):

```ts
export * from "./reference-video";
```

- [ ] **Step 4: 运行 typecheck**

Run: `cd frontend && pnpm exec tsc --noEmit`
Expected: PASS (任何老代码用 `generation_mode === "single"` 的字符串比较仍可编译，因为 `"single"` 仍在联合类型中)

- [ ] **Step 5: 提交**

```bash
git add frontend/src/types/project.ts frontend/src/types/reference-video.ts frontend/src/types/index.ts
git commit -m "feat(frontend): add reference-video types and extend ProjectData.generation_mode"
```

---

### Task 3：API 层新增 reference-video 端点封装

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/api.test.ts`

- [ ] **Step 1: 写失败测试**

Open `frontend/src/api.test.ts` and append a new describe block (保留现有测试；末尾追加)：

```ts
// frontend/src/api.test.ts（追加）
import type { ReferenceVideoUnit } from "@/types";

describe("API.referenceVideos", () => {
  const fetchMock = vi.spyOn(globalThis, "fetch");

  beforeEach(() => {
    fetchMock.mockReset();
  });

  afterAll(() => {
    fetchMock.mockRestore();
  });

  const mkUnit = (id: string): ReferenceVideoUnit => ({
    unit_id: id,
    shots: [{ duration: 3, text: "Shot 1 (3s): test" }],
    references: [],
    duration_seconds: 3,
    duration_override: false,
    transition_to_next: "cut",
    note: null,
    generated_assets: {
      storyboard_image: null,
      storyboard_last_image: null,
      grid_id: null,
      grid_cell_index: null,
      video_clip: null,
      video_uri: null,
      status: "pending",
    },
  });

  it("listReferenceVideoUnits calls GET /reference-videos/episodes/:ep/units", async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ units: [mkUnit("E1U1")] }), { status: 200 }));
    const res = await API.listReferenceVideoUnits("proj", 1);
    expect(res.units).toHaveLength(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/projects/proj/reference-videos/episodes/1/units",
      expect.objectContaining({ method: undefined }),
    );
  });

  it("addReferenceVideoUnit posts the prompt payload", async () => {
    const unit = mkUnit("E1U2");
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ unit }), { status: 201 }));
    const res = await API.addReferenceVideoUnit("proj", 1, { prompt: "Shot 1 (3s): hi", references: [] });
    expect(res.unit.unit_id).toBe("E1U2");
    const [, init] = fetchMock.mock.calls[0]!;
    expect(init!.method).toBe("POST");
    const body = JSON.parse(init!.body as string) as { prompt: string };
    expect(body.prompt).toBe("Shot 1 (3s): hi");
  });

  it("reorderReferenceVideoUnits sends ordered ids", async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ units: [] }), { status: 200 }));
    await API.reorderReferenceVideoUnits("proj", 1, ["E1U2", "E1U1"]);
    const body = JSON.parse(fetchMock.mock.calls[0]![1]!.body as string) as { unit_ids: string[] };
    expect(body.unit_ids).toEqual(["E1U2", "E1U1"]);
  });

  it("generateReferenceVideoUnit returns task id", async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ task_id: "t-1", deduped: false }), { status: 202 }));
    const res = await API.generateReferenceVideoUnit("proj", 1, "E1U1");
    expect(res.task_id).toBe("t-1");
  });

  it("deleteReferenceVideoUnit returns void on 204", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await expect(API.deleteReferenceVideoUnit("proj", 1, "E1U1")).resolves.toBeUndefined();
  });
});
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd frontend && pnpm vitest run src/api.test.ts -t "referenceVideos"`
Expected: FAIL — `API.listReferenceVideoUnits is not a function`

- [ ] **Step 3: 实现 API 方法**

Open `frontend/src/api.ts` and append inside the `API` class, before the final closing brace (line ~1649)：

```ts
// frontend/src/api.ts（追加到 class API {} 内部、最末端 `}` 之前）

  // ==================== Reference-to-Video API ====================

  /** List reference-video units for an episode. */
  static async listReferenceVideoUnits(
    projectName: string,
    episode: number,
  ): Promise<{ units: import("@/types").ReferenceVideoUnit[] }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/reference-videos/episodes/${episode}/units`,
    );
  }

  /** Create a new reference-video unit. */
  static async addReferenceVideoUnit(
    projectName: string,
    episode: number,
    payload: {
      prompt: string;
      references: import("@/types").ReferenceResource[];
      duration_seconds?: number;
      transition_to_next?: "cut" | "fade" | "dissolve";
      note?: string | null;
    },
  ): Promise<{ unit: import("@/types").ReferenceVideoUnit }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/reference-videos/episodes/${episode}/units`,
      { method: "POST", body: JSON.stringify(payload) },
    );
  }

  /** Patch prompt/references/duration/transition/note on an existing unit. */
  static async patchReferenceVideoUnit(
    projectName: string,
    episode: number,
    unitId: string,
    patch: {
      prompt?: string;
      references?: import("@/types").ReferenceResource[];
      duration_seconds?: number;
      transition_to_next?: "cut" | "fade" | "dissolve";
      note?: string | null;
    },
  ): Promise<{ unit: import("@/types").ReferenceVideoUnit }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/reference-videos/episodes/${episode}/units/${encodeURIComponent(unitId)}`,
      { method: "PATCH", body: JSON.stringify(patch) },
    );
  }

  /** Delete a unit. Returns void on 204. */
  static async deleteReferenceVideoUnit(
    projectName: string,
    episode: number,
    unitId: string,
  ): Promise<void> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/reference-videos/episodes/${episode}/units/${encodeURIComponent(unitId)}`,
      { method: "DELETE" },
    );
  }

  /** Reorder units by providing the full ordered unit_id list. */
  static async reorderReferenceVideoUnits(
    projectName: string,
    episode: number,
    unitIds: string[],
  ): Promise<{ units: import("@/types").ReferenceVideoUnit[] }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/reference-videos/episodes/${episode}/units/reorder`,
      { method: "POST", body: JSON.stringify({ unit_ids: unitIds }) },
    );
  }

  /** Enqueue generation; returns 202 with task_id. */
  static async generateReferenceVideoUnit(
    projectName: string,
    episode: number,
    unitId: string,
  ): Promise<{ task_id: string; deduped: boolean }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/reference-videos/episodes/${episode}/units/${encodeURIComponent(unitId)}/generate`,
      { method: "POST" },
    );
  }
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd frontend && pnpm vitest run src/api.test.ts -t "referenceVideos"`
Expected: PASS — 5 tests

- [ ] **Step 5: 提交**

```bash
git add frontend/src/api.ts frontend/src/api.test.ts
git commit -m "feat(frontend): add reference-video API client methods"
```

---

### Task 4：reference-video zustand store

**Files:**
- Create: `frontend/src/stores/reference-video-store.ts`
- Create: `frontend/src/stores/reference-video-store.test.ts`

- [ ] **Step 1: 写失败测试**

```ts
// frontend/src/stores/reference-video-store.test.ts
import { beforeEach, describe, expect, it, vi, afterEach } from "vitest";
import { act } from "@testing-library/react";
import { useReferenceVideoStore } from "./reference-video-store";
import { API } from "@/api";
import type { ReferenceVideoUnit } from "@/types";

function mkUnit(id: string, overrides: Partial<ReferenceVideoUnit> = {}): ReferenceVideoUnit {
  return {
    unit_id: id,
    shots: [{ duration: 3, text: "Shot 1 (3s): x" }],
    references: [],
    duration_seconds: 3,
    duration_override: false,
    transition_to_next: "cut",
    note: null,
    generated_assets: {
      storyboard_image: null,
      storyboard_last_image: null,
      grid_id: null,
      grid_cell_index: null,
      video_clip: null,
      video_uri: null,
      status: "pending",
    },
    ...overrides,
  };
}

describe("reference-video-store", () => {
  beforeEach(() => {
    useReferenceVideoStore.setState({
      unitsByEpisode: {},
      selectedUnitId: null,
      loading: false,
      error: null,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loadUnits populates unitsByEpisode and clears loading", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValueOnce({
      units: [mkUnit("E1U1"), mkUnit("E1U2")],
    });

    await act(async () => {
      await useReferenceVideoStore.getState().loadUnits("proj", 1);
    });

    const state = useReferenceVideoStore.getState();
    expect(state.unitsByEpisode["1"]).toHaveLength(2);
    expect(state.loading).toBe(false);
    expect(state.error).toBeNull();
  });

  it("loadUnits captures error and clears loading", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockRejectedValueOnce(new Error("boom"));

    await act(async () => {
      await useReferenceVideoStore.getState().loadUnits("proj", 1);
    });

    const state = useReferenceVideoStore.getState();
    expect(state.error).toBe("boom");
    expect(state.loading).toBe(false);
  });

  it("addUnit appends unit and selects it", async () => {
    vi.spyOn(API, "addReferenceVideoUnit").mockResolvedValueOnce({ unit: mkUnit("E1U3") });

    await act(async () => {
      await useReferenceVideoStore.getState().addUnit("proj", 1, {
        prompt: "Shot 1 (3s): new",
        references: [],
      });
    });

    const state = useReferenceVideoStore.getState();
    expect(state.unitsByEpisode["1"]).toEqual([expect.objectContaining({ unit_id: "E1U3" })]);
    expect(state.selectedUnitId).toBe("E1U3");
  });

  it("patchUnit replaces the unit returned by server", async () => {
    useReferenceVideoStore.setState({
      unitsByEpisode: { "1": [mkUnit("E1U1")] },
      selectedUnitId: "E1U1",
      loading: false,
      error: null,
    });
    vi.spyOn(API, "patchReferenceVideoUnit").mockResolvedValueOnce({
      unit: mkUnit("E1U1", { note: "updated" }),
    });

    await act(async () => {
      await useReferenceVideoStore.getState().patchUnit("proj", 1, "E1U1", { note: "updated" });
    });

    expect(useReferenceVideoStore.getState().unitsByEpisode["1"][0].note).toBe("updated");
  });

  it("deleteUnit removes unit and clears selection if it was selected", async () => {
    useReferenceVideoStore.setState({
      unitsByEpisode: { "1": [mkUnit("E1U1"), mkUnit("E1U2")] },
      selectedUnitId: "E1U1",
      loading: false,
      error: null,
    });
    vi.spyOn(API, "deleteReferenceVideoUnit").mockResolvedValueOnce(undefined);

    await act(async () => {
      await useReferenceVideoStore.getState().deleteUnit("proj", 1, "E1U1");
    });

    const state = useReferenceVideoStore.getState();
    expect(state.unitsByEpisode["1"].map((u) => u.unit_id)).toEqual(["E1U2"]);
    expect(state.selectedUnitId).toBeNull();
  });

  it("reorderUnits replaces episode array with server response", async () => {
    const reordered = [mkUnit("E1U2"), mkUnit("E1U1")];
    vi.spyOn(API, "reorderReferenceVideoUnits").mockResolvedValueOnce({ units: reordered });

    await act(async () => {
      await useReferenceVideoStore.getState().reorderUnits("proj", 1, ["E1U2", "E1U1"]);
    });

    expect(useReferenceVideoStore.getState().unitsByEpisode["1"].map((u) => u.unit_id))
      .toEqual(["E1U2", "E1U1"]);
  });

  it("select sets selectedUnitId", () => {
    useReferenceVideoStore.getState().select("E1U7");
    expect(useReferenceVideoStore.getState().selectedUnitId).toBe("E1U7");
  });
});
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd frontend && pnpm vitest run src/stores/reference-video-store.test.ts`
Expected: FAIL — "Cannot find module './reference-video-store'"

- [ ] **Step 3: 实现 store**

```ts
// frontend/src/stores/reference-video-store.ts
import { create } from "zustand";
import { API } from "@/api";
import type { ReferenceResource, ReferenceVideoUnit } from "@/types";

interface AddUnitPayload {
  prompt: string;
  references: ReferenceResource[];
  duration_seconds?: number;
  transition_to_next?: "cut" | "fade" | "dissolve";
  note?: string | null;
}

interface PatchUnitPayload {
  prompt?: string;
  references?: ReferenceResource[];
  duration_seconds?: number;
  transition_to_next?: "cut" | "fade" | "dissolve";
  note?: string | null;
}

interface ReferenceVideoStore {
  /** Keyed by episode number (as string). */
  unitsByEpisode: Record<string, ReferenceVideoUnit[]>;
  selectedUnitId: string | null;
  loading: boolean;
  error: string | null;

  loadUnits: (projectName: string, episode: number) => Promise<void>;
  addUnit: (projectName: string, episode: number, payload: AddUnitPayload) => Promise<ReferenceVideoUnit>;
  patchUnit: (projectName: string, episode: number, unitId: string, patch: PatchUnitPayload) => Promise<ReferenceVideoUnit>;
  deleteUnit: (projectName: string, episode: number, unitId: string) => Promise<void>;
  reorderUnits: (projectName: string, episode: number, unitIds: string[]) => Promise<void>;
  generate: (projectName: string, episode: number, unitId: string) => Promise<{ task_id: string; deduped: boolean }>;
  select: (unitId: string | null) => void;
}

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export const useReferenceVideoStore = create<ReferenceVideoStore>((set, get) => ({
  unitsByEpisode: {},
  selectedUnitId: null,
  loading: false,
  error: null,

  loadUnits: async (projectName, episode) => {
    set({ loading: true, error: null });
    try {
      const { units } = await API.listReferenceVideoUnits(projectName, episode);
      set((s) => ({
        unitsByEpisode: { ...s.unitsByEpisode, [String(episode)]: units },
        loading: false,
      }));
    } catch (e) {
      set({ loading: false, error: errMsg(e) });
    }
  },

  addUnit: async (projectName, episode, payload) => {
    const { unit } = await API.addReferenceVideoUnit(projectName, episode, payload);
    set((s) => {
      const key = String(episode);
      const list = s.unitsByEpisode[key] ?? [];
      return {
        unitsByEpisode: { ...s.unitsByEpisode, [key]: [...list, unit] },
        selectedUnitId: unit.unit_id,
      };
    });
    return unit;
  },

  patchUnit: async (projectName, episode, unitId, patch) => {
    const { unit } = await API.patchReferenceVideoUnit(projectName, episode, unitId, patch);
    set((s) => {
      const key = String(episode);
      const list = s.unitsByEpisode[key] ?? [];
      return {
        unitsByEpisode: {
          ...s.unitsByEpisode,
          [key]: list.map((u) => (u.unit_id === unitId ? unit : u)),
        },
      };
    });
    return unit;
  },

  deleteUnit: async (projectName, episode, unitId) => {
    await API.deleteReferenceVideoUnit(projectName, episode, unitId);
    set((s) => {
      const key = String(episode);
      const list = s.unitsByEpisode[key] ?? [];
      return {
        unitsByEpisode: { ...s.unitsByEpisode, [key]: list.filter((u) => u.unit_id !== unitId) },
        selectedUnitId: s.selectedUnitId === unitId ? null : s.selectedUnitId,
      };
    });
  },

  reorderUnits: async (projectName, episode, unitIds) => {
    const { units } = await API.reorderReferenceVideoUnits(projectName, episode, unitIds);
    set((s) => ({
      unitsByEpisode: { ...s.unitsByEpisode, [String(episode)]: units },
    }));
  },

  generate: async (projectName, episode, unitId) => {
    return API.generateReferenceVideoUnit(projectName, episode, unitId);
  },

  select: (unitId) => set({ selectedUnitId: unitId }),
}));
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd frontend && pnpm vitest run src/stores/reference-video-store.test.ts`
Expected: PASS — 7 tests

- [ ] **Step 5: 提交**

```bash
git add frontend/src/stores/reference-video-store.ts frontend/src/stores/reference-video-store.test.ts
git commit -m "feat(frontend): add reference-video zustand store"
```

---

### Task 5：新增 `GenerationModeSelector` 共享组件

**Files:**
- Create: `frontend/src/components/shared/GenerationModeSelector.tsx`
- Create: `frontend/src/components/shared/GenerationModeSelector.test.tsx`
- Modify: `frontend/src/i18n/zh/dashboard.ts`、`frontend/src/i18n/en/dashboard.ts`

- [ ] **Step 1: 追加 i18n key（zh）**

Open `frontend/src/i18n/zh/dashboard.ts` and insert after the current `generation_mode_desc` line (around line 474)：

```ts
// frontend/src/i18n/zh/dashboard.ts（追加到 generation_mode_desc 下方）
  'mode_storyboard': '图生视频',
  'mode_grid': '宫格生视频',
  'mode_reference_video': '参考生视频',
  'mode_storyboard_desc': '先逐张生成分镜图，再图生视频，适合精细控制。',
  'mode_grid_desc': '按段落批量生成宫格图，画风统一，适合量产。',
  'mode_reference_video_desc': '跳过分镜，直接用角色/场景/道具参考图多镜头生成视频。',
  'reference_canvas_empty': '尚未创建任何 Unit',
  'reference_unit_list_title': 'Video Units',
  'reference_unit_new': '新建 Unit',
  'reference_preview_empty': '选中左侧 Unit 查看预览',
  'reference_preview_generate': '生成视频',
  'reference_preview_generating': '生成中…',
  'reference_units_count': '{{count}} 个 Unit',
  'reference_status_pending': '未生成',
  'reference_status_running': '生成中',
  'reference_status_ready': '已就绪',
  'reference_status_failed': '失败',
  'episode_mode_switcher_label': '集级生成模式',
  'episode_mode_inherit_from_project': '继承项目设置',
```

- [ ] **Step 2: 追加 i18n key（en，文案镜像）**

Open `frontend/src/i18n/en/dashboard.ts` and insert after `generation_mode_desc`：

```ts
  'mode_storyboard': 'Image-to-Video',
  'mode_grid': 'Grid-to-Video',
  'mode_reference_video': 'Reference-to-Video',
  'mode_storyboard_desc': 'Generate storyboard frames first, then per-frame video. Best for fine control.',
  'mode_grid_desc': 'Batch grid images per paragraph. Consistent style for high-volume output.',
  'mode_reference_video_desc': 'Skip storyboards; use character/scene/prop reference images for multi-shot videos.',
  'reference_canvas_empty': 'No units yet',
  'reference_unit_list_title': 'Video Units',
  'reference_unit_new': 'New Unit',
  'reference_preview_empty': 'Select a unit to preview',
  'reference_preview_generate': 'Generate video',
  'reference_preview_generating': 'Generating…',
  'reference_units_count': '{{count}} units',
  'reference_status_pending': 'Pending',
  'reference_status_running': 'Running',
  'reference_status_ready': 'Ready',
  'reference_status_failed': 'Failed',
  'episode_mode_switcher_label': 'Episode generation mode',
  'episode_mode_inherit_from_project': 'Inherit from project',
```

- [ ] **Step 3: 写组件失败测试**

```tsx
// frontend/src/components/shared/GenerationModeSelector.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { GenerationModeSelector } from "./GenerationModeSelector";

function setup(overrides: Partial<React.ComponentProps<typeof GenerationModeSelector>> = {}) {
  const onChange = vi.fn();
  const utils = render(
    <GenerationModeSelector value="storyboard" onChange={onChange} {...overrides} />,
  );
  return { ...utils, onChange };
}

describe("GenerationModeSelector", () => {
  it("renders three mode options by default", () => {
    setup();
    expect(screen.getByRole("radio", { name: /Image-to-Video|图生视频/ })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Grid-to-Video|宫格生视频/ })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Reference-to-Video|参考生视频/ })).toBeInTheDocument();
  });

  it("marks the current value as checked", () => {
    setup({ value: "reference_video" });
    const refRadio = screen.getByRole("radio", { name: /Reference-to-Video|参考生视频/ }) as HTMLInputElement;
    expect(refRadio.checked).toBe(true);
  });

  it("emits onChange with canonical value when clicked", () => {
    const { onChange } = setup();
    fireEvent.click(screen.getByRole("radio", { name: /Grid-to-Video|宫格生视频/ }));
    expect(onChange).toHaveBeenCalledWith("grid");
  });

  it("shows the description text for the selected mode", () => {
    setup({ value: "reference_video" });
    // Description text uses the mode_reference_video_desc key
    expect(
      screen.getByText(/Skip storyboards|跳过分镜/),
    ).toBeInTheDocument();
  });

  it("disables modes passed in disabledModes", () => {
    setup({ disabledModes: ["reference_video"] });
    const ref = screen.getByRole("radio", { name: /Reference-to-Video|参考生视频/ }) as HTMLInputElement;
    expect(ref.disabled).toBe(true);
  });
});
```

- [ ] **Step 4: 运行测试，确认失败**

Run: `cd frontend && pnpm vitest run src/components/shared/GenerationModeSelector.test.tsx`
Expected: FAIL — 'Cannot find module "./GenerationModeSelector"'

- [ ] **Step 5: 实现组件**

```tsx
// frontend/src/components/shared/GenerationModeSelector.tsx
import { useTranslation } from "react-i18next";
import type { GenerationMode } from "@/utils/generation-mode";

export interface GenerationModeSelectorProps {
  value: GenerationMode;
  onChange: (next: GenerationMode) => void;
  /** Modes to disable (e.g. if a provider cannot support reference_video). */
  disabledModes?: GenerationMode[];
  /** "lg" for wizard/settings (with description), "sm" for toolbars. */
  size?: "lg" | "sm";
  /** Optional name to differentiate multiple selectors on the same page. */
  name?: string;
}

const MODES: GenerationMode[] = ["storyboard", "grid", "reference_video"];

export function GenerationModeSelector({
  value,
  onChange,
  disabledModes = [],
  size = "lg",
  name = "generationMode",
}: GenerationModeSelectorProps) {
  const { t } = useTranslation("dashboard");

  const labelFor = (m: GenerationMode): string =>
    m === "storyboard"
      ? t("mode_storyboard")
      : m === "grid"
        ? t("mode_grid")
        : t("mode_reference_video");

  const descFor = (m: GenerationMode): string =>
    m === "storyboard"
      ? t("mode_storyboard_desc")
      : m === "grid"
        ? t("mode_grid_desc")
        : t("mode_reference_video_desc");

  return (
    <div className="space-y-2">
      <div
        role="radiogroup"
        aria-label={t("generation_mode")}
        className={size === "sm" ? "inline-flex gap-1" : "flex gap-3"}
      >
        {MODES.map((m) => {
          const disabled = disabledModes.includes(m);
          const selected = value === m;
          const baseClass = size === "sm"
            ? "cursor-pointer rounded-md border px-3 py-1 text-xs transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-indigo-500"
            : "flex-1 cursor-pointer rounded-lg border px-3 py-2 text-center text-sm transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-indigo-500";
          const stateClass = disabled
            ? "border-gray-800 bg-gray-900 text-gray-600 cursor-not-allowed"
            : selected
              ? "border-indigo-500 bg-indigo-500/10 text-indigo-300"
              : "border-gray-700 bg-gray-800 text-gray-400 hover:border-gray-600";
          return (
            <label key={m} className={`${baseClass} ${stateClass}`}>
              <input
                type="radio"
                name={name}
                value={m}
                checked={selected}
                disabled={disabled}
                onChange={() => onChange(m)}
                className="sr-only"
              />
              {labelFor(m)}
            </label>
          );
        })}
      </div>
      {size === "lg" && (
        <p className="text-xs text-gray-500">{descFor(value)}</p>
      )}
    </div>
  );
}
```

- [ ] **Step 6: 运行测试，确认通过**

Run: `cd frontend && pnpm vitest run src/components/shared/GenerationModeSelector.test.tsx`
Expected: PASS — 5 tests

- [ ] **Step 7: 提交**

```bash
git add frontend/src/components/shared/GenerationModeSelector.tsx \
        frontend/src/components/shared/GenerationModeSelector.test.tsx \
        frontend/src/i18n/zh/dashboard.ts frontend/src/i18n/en/dashboard.ts
git commit -m "feat(frontend): add GenerationModeSelector shared component + i18n"
```

---

### Task 6：接入 WizardStep1Basics 与 CreateProjectModal

**Files:**
- Modify: `frontend/src/components/pages/create-project/WizardStep1Basics.tsx`
- Modify: `frontend/src/components/pages/CreateProjectModal.tsx`
- Modify: `frontend/src/components/pages/CreateProjectModal.test.tsx`
- Modify: `frontend/src/components/pages/create-project/WizardStep1Basics.test.tsx`

- [ ] **Step 1: 修改 `WizardStep1Value` 类型并替换 radios**

Replace lines 4-9 of `frontend/src/components/pages/create-project/WizardStep1Basics.tsx`:

```ts
// frontend/src/components/pages/create-project/WizardStep1Basics.tsx:4-9（修改）
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { GenerationModeSelector } from "@/components/shared/GenerationModeSelector";
import type { GenerationMode } from "@/utils/generation-mode";

export interface WizardStep1Value {
  title: string;
  contentMode: "narration" | "drama";
  aspectRatio: "9:16" | "16:9";
  generationMode: GenerationMode;
}
```

Replace the current "Generation Mode" block (lines 133-165) with:

```tsx
// frontend/src/components/pages/create-project/WizardStep1Basics.tsx:133-165（替换）
      {/* Generation Mode */}
      <div>
        <label className="block text-sm font-medium text-gray-400 mb-0.5">
          {t("dashboard:generation_mode")}
        </label>
        <GenerationModeSelector
          value={value.generationMode}
          onChange={(next) => onChange({ ...value, generationMode: next })}
        />
      </div>
```

- [ ] **Step 2: 更新 CreateProjectModal 默认值 + 透传**

Open `frontend/src/components/pages/CreateProjectModal.tsx`. Find where default `generationMode` is set (search for `generationMode: "single"` or similar). Replace with:

```tsx
// frontend/src/components/pages/CreateProjectModal.tsx（默认 state，替换旧 "single"）
generationMode: "storyboard",
```

And in `handleSubmit` where `generation_mode: basics.generationMode` is sent (line ~184), keep the same key but the value will now be `"storyboard" | "grid" | "reference_video"` — server accepts any string (see `server/routers/projects.py`).

- [ ] **Step 3: 更新测试 fixture**

Replace in `frontend/src/components/pages/CreateProjectModal.test.tsx` line 142 (or wherever fixture is):

```ts
// frontend/src/components/pages/CreateProjectModal.test.tsx（替换）
generation_mode: "storyboard",
```

Add to `frontend/src/components/pages/create-project/WizardStep1Basics.test.tsx` a new test covering the reference_video option:

```tsx
// frontend/src/components/pages/create-project/WizardStep1Basics.test.tsx（追加）
import { fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";

it("switches generation mode to reference_video", () => {
  const onChange = vi.fn();
  render(
    <WizardStep1Basics
      value={{ title: "t", contentMode: "narration", aspectRatio: "9:16", generationMode: "storyboard" }}
      onChange={onChange}
      onNext={() => {}}
      onCancel={() => {}}
    />,
  );
  fireEvent.click(screen.getByRole("radio", { name: /Reference-to-Video|参考生视频/ }));
  expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ generationMode: "reference_video" }));
});
```

- [ ] **Step 4: 运行相关测试**

Run: `cd frontend && pnpm vitest run src/components/pages/create-project/WizardStep1Basics.test.tsx src/components/pages/CreateProjectModal.test.tsx`
Expected: PASS — 旧测试 + 新测试

- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/pages/create-project/WizardStep1Basics.tsx \
        frontend/src/components/pages/create-project/WizardStep1Basics.test.tsx \
        frontend/src/components/pages/CreateProjectModal.tsx \
        frontend/src/components/pages/CreateProjectModal.test.tsx
git commit -m "feat(frontend): wire GenerationModeSelector into new-project wizard"
```

---

### Task 7：接入 ProjectSettingsPage

**Files:**
- Modify: `frontend/src/components/pages/ProjectSettingsPage.tsx`
- Modify: `frontend/src/components/pages/ProjectSettingsPage.test.tsx`

- [ ] **Step 1: 替换 generationMode state 类型与解析**

Open `frontend/src/components/pages/ProjectSettingsPage.tsx`. Near the top, find `const [generationMode, setGenerationMode] = useState<"single" | "grid">("single");` (if exists) or inline `useState` with that type — replace with:

```tsx
// frontend/src/components/pages/ProjectSettingsPage.tsx（imports + state）
import { GenerationModeSelector } from "@/components/shared/GenerationModeSelector";
import { normalizeMode, type GenerationMode } from "@/utils/generation-mode";

// ...
const [generationMode, setGenerationMode] = useState<GenerationMode>("storyboard");
```

Replace line 130 (loadProject):

```tsx
// frontend/src/components/pages/ProjectSettingsPage.tsx:130（替换）
const gm = normalizeMode(project.generation_mode);
```

Replace lines 409-442 (Generation mode block) with:

```tsx
// frontend/src/components/pages/ProjectSettingsPage.tsx:409-442（替换）
            {/* Generation mode */}
            <div className="rounded-xl border border-gray-800 bg-gray-950/40 p-4">
              <fieldset>
                <legend className="mb-1 text-sm font-medium text-gray-100">{t("generation_mode")}</legend>
                <GenerationModeSelector
                  value={generationMode}
                  onChange={setGenerationMode}
                />
              </fieldset>
            </div>
```

- [ ] **Step 2: 更新已有测试 + 追加参考模式 case**

Add to `frontend/src/components/pages/ProjectSettingsPage.test.tsx`:

```tsx
// frontend/src/components/pages/ProjectSettingsPage.test.tsx（追加 — 与既有文件的 import 合并）
import userEvent from "@testing-library/user-event";
import { screen } from "@testing-library/react";

it("switches generation_mode to reference_video and marks dirty", async () => {
  // Reuse the existing render helper that mounts the page with mocked API.getProject
  // returning { project: { ..., generation_mode: "storyboard" }, ... }.
  // The helper already exists in this file — reuse it verbatim; only the assertions below are new.
  const user = userEvent.setup();
  const refRadio = await screen.findByRole("radio", { name: /Reference-to-Video|参考生视频/ });
  await user.click(refRadio);
  expect((refRadio as HTMLInputElement).checked).toBe(true);
  // The save button should now be enabled
  const saveBtn = screen.getByRole("button", { name: /Save|保存/ });
  expect(saveBtn).not.toBeDisabled();
});
```

- [ ] **Step 3: 运行测试**

Run: `cd frontend && pnpm vitest run src/components/pages/ProjectSettingsPage.test.tsx`
Expected: PASS

- [ ] **Step 4: 提交**

```bash
git add frontend/src/components/pages/ProjectSettingsPage.tsx frontend/src/components/pages/ProjectSettingsPage.test.tsx
git commit -m "feat(frontend): wire GenerationModeSelector into project settings"
```

---

### Task 8：`EpisodeModeSwitcher`（集级分段控制）

**Files:**
- Create: `frontend/src/components/canvas/EpisodeModeSwitcher.tsx`
- Create: `frontend/src/components/canvas/EpisodeModeSwitcher.test.tsx`

- [ ] **Step 1: 写失败测试**

```tsx
// frontend/src/components/canvas/EpisodeModeSwitcher.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { EpisodeModeSwitcher } from "./EpisodeModeSwitcher";

describe("EpisodeModeSwitcher", () => {
  it("shows project-level mode when episode has none (inherited)", () => {
    render(
      <EpisodeModeSwitcher
        projectMode="reference_video"
        episodeMode={undefined}
        onChange={vi.fn()}
      />,
    );
    const radio = screen.getByRole("radio", { name: /Reference-to-Video|参考生视频/ }) as HTMLInputElement;
    expect(radio.checked).toBe(true);
  });

  it("uses episode-level override when set", () => {
    render(
      <EpisodeModeSwitcher
        projectMode="storyboard"
        episodeMode="grid"
        onChange={vi.fn()}
      />,
    );
    const gridRadio = screen.getByRole("radio", { name: /Grid-to-Video|宫格生视频/ }) as HTMLInputElement;
    expect(gridRadio.checked).toBe(true);
  });

  it("calls onChange with the selected mode when clicked", () => {
    const onChange = vi.fn();
    render(
      <EpisodeModeSwitcher
        projectMode="storyboard"
        episodeMode={undefined}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("radio", { name: /Reference-to-Video|参考生视频/ }));
    expect(onChange).toHaveBeenCalledWith("reference_video");
  });
});
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd frontend && pnpm vitest run src/components/canvas/EpisodeModeSwitcher.test.tsx`
Expected: FAIL

- [ ] **Step 3: 实现**

```tsx
// frontend/src/components/canvas/EpisodeModeSwitcher.tsx
import { useTranslation } from "react-i18next";
import { GenerationModeSelector } from "@/components/shared/GenerationModeSelector";
import { normalizeMode, type GenerationMode } from "@/utils/generation-mode";

export interface EpisodeModeSwitcherProps {
  /** Project-level mode, used as fallback when episode has no override. */
  projectMode: GenerationMode;
  /** Current episode-level override; undefined = inherit from project. */
  episodeMode: GenerationMode | undefined;
  /** Called with the new mode. Parent should PATCH the episode override. */
  onChange: (next: GenerationMode) => void;
}

export function EpisodeModeSwitcher({ projectMode, episodeMode, onChange }: EpisodeModeSwitcherProps) {
  const { t } = useTranslation("dashboard");
  const effective = normalizeMode(episodeMode ?? projectMode);

  return (
    <div className="flex items-center gap-2 text-xs text-gray-500">
      <span aria-label={t("episode_mode_switcher_label")}>{t("episode_mode_switcher_label")}:</span>
      <GenerationModeSelector
        value={effective}
        onChange={onChange}
        size="sm"
        name="episodeMode"
      />
      {!episodeMode && (
        <span className="text-gray-600">({t("episode_mode_inherit_from_project")})</span>
      )}
    </div>
  );
}
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd frontend && pnpm vitest run src/components/canvas/EpisodeModeSwitcher.test.tsx`
Expected: PASS — 3 tests

- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/canvas/EpisodeModeSwitcher.tsx frontend/src/components/canvas/EpisodeModeSwitcher.test.tsx
git commit -m "feat(frontend): add EpisodeModeSwitcher component"
```

---

### Task 9：`UnitList` 左栏组件

**Files:**
- Create: `frontend/src/components/canvas/reference/UnitList.tsx`
- Create: `frontend/src/components/canvas/reference/UnitList.test.tsx`

- [ ] **Step 1: 写失败测试**

```tsx
// frontend/src/components/canvas/reference/UnitList.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { UnitList } from "./UnitList";
import type { ReferenceVideoUnit } from "@/types";

function mkUnit(id: string, overrides: Partial<ReferenceVideoUnit> = {}): ReferenceVideoUnit {
  return {
    unit_id: id,
    shots: [{ duration: 3, text: "Shot 1 (3s): enter the pub" }],
    references: [{ type: "character", name: "张三" }],
    duration_seconds: 3,
    duration_override: false,
    transition_to_next: "cut",
    note: null,
    generated_assets: {
      storyboard_image: null,
      storyboard_last_image: null,
      grid_id: null,
      grid_cell_index: null,
      video_clip: null,
      video_uri: null,
      status: "pending",
    },
    ...overrides,
  };
}

describe("UnitList", () => {
  it("renders empty state when no units", () => {
    render(<UnitList units={[]} selectedId={null} onSelect={vi.fn()} onAdd={vi.fn()} />);
    expect(screen.getByText(/No units yet|尚未创建任何 Unit/)).toBeInTheDocument();
  });

  it("renders a row per unit with id, duration and prompt preview", () => {
    render(
      <UnitList
        units={[mkUnit("E1U1"), mkUnit("E1U2", { duration_seconds: 8 })]}
        selectedId={null}
        onSelect={vi.fn()}
        onAdd={vi.fn()}
      />,
    );
    expect(screen.getByText("E1U1")).toBeInTheDocument();
    expect(screen.getByText("E1U2")).toBeInTheDocument();
    expect(screen.getAllByText(/enter the pub/)).toHaveLength(2);
  });

  it("highlights the selected unit", () => {
    render(
      <UnitList
        units={[mkUnit("E1U1"), mkUnit("E1U2")]}
        selectedId="E1U2"
        onSelect={vi.fn()}
        onAdd={vi.fn()}
      />,
    );
    expect(screen.getByTestId("unit-row-E1U2")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("unit-row-E1U1")).toHaveAttribute("aria-selected", "false");
  });

  it("calls onSelect when a row is clicked", () => {
    const onSelect = vi.fn();
    render(
      <UnitList units={[mkUnit("E1U1")]} selectedId={null} onSelect={onSelect} onAdd={vi.fn()} />,
    );
    fireEvent.click(screen.getByTestId("unit-row-E1U1"));
    expect(onSelect).toHaveBeenCalledWith("E1U1");
  });

  it("calls onAdd when the 'new unit' button is clicked", () => {
    const onAdd = vi.fn();
    render(<UnitList units={[]} selectedId={null} onSelect={vi.fn()} onAdd={onAdd} />);
    fireEvent.click(screen.getByRole("button", { name: /New Unit|新建 Unit/ }));
    expect(onAdd).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd frontend && pnpm vitest run src/components/canvas/reference/UnitList.test.tsx`
Expected: FAIL

- [ ] **Step 3: 实现**

```tsx
// frontend/src/components/canvas/reference/UnitList.tsx
import { useTranslation } from "react-i18next";
import { Plus } from "lucide-react";
import type { ReferenceVideoUnit, UnitStatus } from "@/types";

export interface UnitListProps {
  units: ReferenceVideoUnit[];
  selectedId: string | null;
  onSelect: (unitId: string) => void;
  onAdd: () => void;
}

const STATUS_COLOR: Record<UnitStatus, string> = {
  pending: "bg-gray-500",
  running: "bg-amber-500 animate-pulse",
  ready: "bg-emerald-500",
  failed: "bg-red-500",
};

function promptPreview(unit: ReferenceVideoUnit): string {
  const text = unit.shots.map((s) => s.text).join("\n");
  const lines = text.split("\n").slice(0, 2);
  const joined = lines.join(" · ");
  return joined.length > 120 ? `${joined.slice(0, 117)}…` : joined;
}

export function UnitList({ units, selectedId, onSelect, onAdd }: UnitListProps) {
  const { t } = useTranslation("dashboard");

  return (
    <div className="flex h-full flex-col border-r border-gray-800 bg-gray-950/50">
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-800">
        <span className="text-sm font-medium text-gray-200">{t("reference_unit_list_title")}</span>
        <button
          type="button"
          onClick={onAdd}
          className="inline-flex items-center gap-1 rounded-md border border-gray-700 bg-gray-800 px-2 py-1 text-xs text-gray-300 hover:border-indigo-500 hover:text-indigo-300"
        >
          <Plus className="h-3 w-3" />
          {t("reference_unit_new")}
        </button>
      </div>
      {units.length === 0 ? (
        <div className="flex flex-1 items-center justify-center p-6 text-sm text-gray-500">
          {t("reference_canvas_empty")}
        </div>
      ) : (
        <ul className="flex-1 overflow-y-auto">
          {units.map((u) => {
            const status = u.generated_assets.status;
            const selected = u.unit_id === selectedId;
            return (
              <li
                key={u.unit_id}
                data-testid={`unit-row-${u.unit_id}`}
                role="option"
                aria-selected={selected}
                tabIndex={0}
                onClick={() => onSelect(u.unit_id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onSelect(u.unit_id);
                  }
                }}
                className={`cursor-pointer border-b border-gray-900 px-3 py-2 text-sm transition-colors ${
                  selected ? "bg-indigo-500/15 text-indigo-200" : "text-gray-300 hover:bg-gray-900"
                }`}
              >
                <div className="flex items-center gap-2">
                  <span
                    aria-label={t(`reference_status_${status}`)}
                    className={`h-2 w-2 rounded-full ${STATUS_COLOR[status]}`}
                  />
                  <span className="font-mono text-xs text-gray-400">{u.unit_id}</span>
                  <span className="ml-auto text-xs text-gray-500 tabular-nums">{u.duration_seconds}s</span>
                </div>
                <p className="mt-1 line-clamp-2 text-xs text-gray-500">{promptPreview(u)}</p>
                {u.references.length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {u.references.map((r, idx) => (
                      <span
                        key={`${r.type}-${r.name}-${idx}`}
                        className="rounded bg-gray-800 px-1.5 py-0.5 text-[10px] text-gray-400"
                      >
                        @{r.name}
                      </span>
                    ))}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd frontend && pnpm vitest run src/components/canvas/reference/UnitList.test.tsx`
Expected: PASS — 5 tests

- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/canvas/reference/UnitList.tsx frontend/src/components/canvas/reference/UnitList.test.tsx
git commit -m "feat(frontend): add UnitList left-pane component for reference canvas"
```

---

### Task 10：`UnitPreviewPanel` 右栏组件

**Files:**
- Create: `frontend/src/components/canvas/reference/UnitPreviewPanel.tsx`
- Create: `frontend/src/components/canvas/reference/UnitPreviewPanel.test.tsx`

- [ ] **Step 1: 写失败测试**

```tsx
// frontend/src/components/canvas/reference/UnitPreviewPanel.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { UnitPreviewPanel } from "./UnitPreviewPanel";
import type { ReferenceVideoUnit } from "@/types";

function mkUnit(overrides: Partial<ReferenceVideoUnit> = {}): ReferenceVideoUnit {
  return {
    unit_id: "E1U1",
    shots: [{ duration: 3, text: "Shot 1 (3s): x" }],
    references: [],
    duration_seconds: 3,
    duration_override: false,
    transition_to_next: "cut",
    note: null,
    generated_assets: {
      storyboard_image: null,
      storyboard_last_image: null,
      grid_id: null,
      grid_cell_index: null,
      video_clip: null,
      video_uri: null,
      status: "pending",
    },
    ...overrides,
  };
}

describe("UnitPreviewPanel", () => {
  it("shows placeholder when no unit is selected", () => {
    render(<UnitPreviewPanel unit={null} onGenerate={vi.fn()} generating={false} />);
    expect(screen.getByText(/Select a unit|选中左侧 Unit/)).toBeInTheDocument();
  });

  it("shows generate button for pending unit", () => {
    const onGenerate = vi.fn();
    render(<UnitPreviewPanel unit={mkUnit()} onGenerate={onGenerate} generating={false} />);
    const btn = screen.getByRole("button", { name: /Generate video|生成视频/ });
    fireEvent.click(btn);
    expect(onGenerate).toHaveBeenCalled();
  });

  it("disables button and shows generating label while running", () => {
    render(<UnitPreviewPanel unit={mkUnit({ generated_assets: { ...mkUnit().generated_assets, status: "running" } })} onGenerate={vi.fn()} generating={true} />);
    const btn = screen.getByRole("button");
    expect(btn).toBeDisabled();
    expect(screen.getByText(/Generating|生成中/)).toBeInTheDocument();
  });

  it("renders <video> when video_clip is present", () => {
    const unit = mkUnit({
      generated_assets: {
        ...mkUnit().generated_assets,
        status: "ready",
        video_clip: "reference_videos/E1U1.mp4",
      },
    });
    const { container } = render(
      <UnitPreviewPanel unit={unit} onGenerate={vi.fn()} generating={false} projectName="proj" />,
    );
    expect(container.querySelector("video")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd frontend && pnpm vitest run src/components/canvas/reference/UnitPreviewPanel.test.tsx`
Expected: FAIL

- [ ] **Step 3: 实现**

```tsx
// frontend/src/components/canvas/reference/UnitPreviewPanel.tsx
import { useTranslation } from "react-i18next";
import { Loader2, Sparkles } from "lucide-react";
import { API } from "@/api";
import type { ReferenceVideoUnit } from "@/types";

export interface UnitPreviewPanelProps {
  unit: ReferenceVideoUnit | null;
  projectName?: string;
  onGenerate: (unitId: string) => void;
  /** External signal (e.g. a queued/running task for this unit). */
  generating: boolean;
}

export function UnitPreviewPanel({ unit, projectName, onGenerate, generating }: UnitPreviewPanelProps) {
  const { t } = useTranslation("dashboard");

  if (!unit) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-sm text-gray-500">
        {t("reference_preview_empty")}
      </div>
    );
  }

  const clip = unit.generated_assets.video_clip;
  const videoUrl =
    clip && projectName ? API.getFileUrl(projectName, clip) : null;

  const busy = generating || unit.generated_assets.status === "running";

  return (
    <div className="flex h-full flex-col gap-3 p-3">
      <div className="aspect-video w-full overflow-hidden rounded-lg border border-gray-800 bg-black">
        {videoUrl ? (
          <video src={videoUrl} controls className="h-full w-full object-contain" />
        ) : (
          <div className="flex h-full items-center justify-center text-xs text-gray-600">
            {t("reference_preview_empty")}
          </div>
        )}
      </div>

      <dl className="grid grid-cols-2 gap-1 text-xs text-gray-500">
        <dt>Unit</dt>
        <dd className="font-mono text-gray-300">{unit.unit_id}</dd>
        <dt>Duration</dt>
        <dd className="tabular-nums text-gray-300">{unit.duration_seconds}s</dd>
        <dt>Shots</dt>
        <dd className="text-gray-300">{unit.shots.length}</dd>
        <dt>References</dt>
        <dd className="text-gray-300">{unit.references.length}</dd>
      </dl>

      <button
        type="button"
        onClick={() => onGenerate(unit.unit_id)}
        disabled={busy}
        className={`inline-flex items-center justify-center gap-1.5 rounded-lg border px-4 py-2 text-sm font-medium transition-colors ${
          busy
            ? "border-blue-700 text-blue-400 opacity-70 cursor-not-allowed"
            : "border-blue-600 text-blue-400 hover:bg-blue-600/10"
        }`}
      >
        {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
        {busy ? t("reference_preview_generating") : t("reference_preview_generate")}
      </button>
    </div>
  );
}
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd frontend && pnpm vitest run src/components/canvas/reference/UnitPreviewPanel.test.tsx`
Expected: PASS — 4 tests

- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/canvas/reference/UnitPreviewPanel.tsx frontend/src/components/canvas/reference/UnitPreviewPanel.test.tsx
git commit -m "feat(frontend): add UnitPreviewPanel right-pane component"
```

---

### Task 11：`ReferenceVideoCanvas` 三栏骨架

**Files:**
- Create: `frontend/src/components/canvas/reference/ReferenceVideoCanvas.tsx`
- Create: `frontend/src/components/canvas/reference/ReferenceVideoCanvas.test.tsx`

- [ ] **Step 1: 写失败测试**

```tsx
// frontend/src/components/canvas/reference/ReferenceVideoCanvas.test.tsx
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { ReferenceVideoCanvas } from "./ReferenceVideoCanvas";
import { useReferenceVideoStore } from "@/stores/reference-video-store";
import { API } from "@/api";
import type { ReferenceVideoUnit } from "@/types";

function mkUnit(id: string): ReferenceVideoUnit {
  return {
    unit_id: id,
    shots: [{ duration: 3, text: "Shot 1 (3s): x" }],
    references: [],
    duration_seconds: 3,
    duration_override: false,
    transition_to_next: "cut",
    note: null,
    generated_assets: {
      storyboard_image: null,
      storyboard_last_image: null,
      grid_id: null,
      grid_cell_index: null,
      video_clip: null,
      video_uri: null,
      status: "pending",
    },
  };
}

describe("ReferenceVideoCanvas", () => {
  beforeEach(() => {
    useReferenceVideoStore.setState({ unitsByEpisode: {}, selectedUnitId: null, loading: false, error: null });
  });
  afterEach(() => vi.restoreAllMocks());

  it("loads units on mount and renders the list", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1"), mkUnit("E1U2")] });
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    await waitFor(() => expect(screen.getByText("E1U1")).toBeInTheDocument());
    expect(screen.getByText("E1U2")).toBeInTheDocument();
  });

  it("selects a unit and shows it in preview panel", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [mkUnit("E1U1")] });
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    await waitFor(() => expect(screen.getByText("E1U1")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("unit-row-E1U1"));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Generate video|生成视频/ })).toBeInTheDocument();
    });
  });

  it("adds a new unit via the store when the button is clicked", async () => {
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [] });
    const addSpy = vi.spyOn(API, "addReferenceVideoUnit").mockResolvedValue({ unit: mkUnit("E1U1") });
    render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /New Unit|新建 Unit/ })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: /New Unit|新建 Unit/ }));
    await waitFor(() => expect(addSpy).toHaveBeenCalled());
  });
});
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd frontend && pnpm vitest run src/components/canvas/reference/ReferenceVideoCanvas.test.tsx`
Expected: FAIL

- [ ] **Step 3: 实现**

```tsx
// frontend/src/components/canvas/reference/ReferenceVideoCanvas.tsx
import { useEffect, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { UnitList } from "./UnitList";
import { UnitPreviewPanel } from "./UnitPreviewPanel";
import { useReferenceVideoStore } from "@/stores/reference-video-store";
import { useTasksStore } from "@/stores/tasks-store";
import { useAppStore } from "@/stores/app-store";

export interface ReferenceVideoCanvasProps {
  projectName: string;
  episode: number;
  episodeTitle?: string;
}

export function ReferenceVideoCanvas({ projectName, episode, episodeTitle }: ReferenceVideoCanvasProps) {
  const { t } = useTranslation("dashboard");

  const loadUnits = useReferenceVideoStore((s) => s.loadUnits);
  const addUnit = useReferenceVideoStore((s) => s.addUnit);
  const generate = useReferenceVideoStore((s) => s.generate);
  const select = useReferenceVideoStore((s) => s.select);
  const units = useReferenceVideoStore((s) => s.unitsByEpisode[String(episode)] ?? []);
  const selectedUnitId = useReferenceVideoStore((s) => s.selectedUnitId);
  const error = useReferenceVideoStore((s) => s.error);

  const tasks = useTasksStore((s) => s.tasks);

  useEffect(() => {
    void loadUnits(projectName, episode);
  }, [loadUnits, projectName, episode]);

  const selected = useMemo(
    () => units.find((u) => u.unit_id === selectedUnitId) ?? null,
    [units, selectedUnitId],
  );

  const generating = useMemo(() => {
    if (!selected) return false;
    return tasks.some(
      (tk) =>
        tk.project_name === projectName &&
        tk.task_type === "reference_video" &&
        tk.resource_id === selected.unit_id &&
        (tk.status === "queued" || tk.status === "running"),
    );
  }, [tasks, projectName, selected]);

  const handleAdd = async () => {
    try {
      await addUnit(projectName, episode, { prompt: "", references: [] });
    } catch (e) {
      useAppStore.getState().pushToast(e instanceof Error ? e.message : String(e), "error");
    }
  };

  const handleGenerate = async (unitId: string) => {
    try {
      await generate(projectName, episode, unitId);
    } catch (e) {
      useAppStore.getState().pushToast(e instanceof Error ? e.message : String(e), "error");
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-gray-800 px-4 py-2">
        <h2 className="text-sm font-semibold text-gray-100">
          E{episode}
          {episodeTitle ? `: ${episodeTitle}` : ""} · {t("reference_units_count", { count: units.length })}
        </h2>
        {error && <p className="mt-1 text-xs text-red-400">{error}</p>}
      </div>
      <div className="grid flex-1 grid-cols-[minmax(260px,20%)_1fr_minmax(280px,24%)] overflow-hidden">
        <UnitList
          units={units}
          selectedId={selectedUnitId}
          onSelect={select}
          onAdd={() => void handleAdd()}
        />
        <div className="flex h-full items-center justify-center border-r border-gray-800 bg-gray-950/30 p-6 text-xs text-gray-600">
          {/* PR5 will render the prompt editor + MentionPicker here. */}
          {selected
            ? selected.shots.map((s, i) => (
                <pre key={i} className="whitespace-pre-wrap text-left text-gray-400">
                  {s.text}
                </pre>
              ))
            : t("reference_canvas_empty")}
        </div>
        <UnitPreviewPanel
          unit={selected}
          projectName={projectName}
          onGenerate={(id) => void handleGenerate(id)}
          generating={generating}
        />
      </div>
    </div>
  );
}
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd frontend && pnpm vitest run src/components/canvas/reference/ReferenceVideoCanvas.test.tsx`
Expected: PASS — 3 tests

- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/canvas/reference/ReferenceVideoCanvas.tsx frontend/src/components/canvas/reference/ReferenceVideoCanvas.test.tsx
git commit -m "feat(frontend): add ReferenceVideoCanvas three-pane skeleton"
```

---

### Task 12：接入 `StudioCanvasRouter`（按 effective_mode 分派）+ `TimelineCanvas` 修正

**Files:**
- Modify: `frontend/src/components/canvas/StudioCanvasRouter.tsx`
- Modify: `frontend/src/components/canvas/timeline/TimelineCanvas.tsx`

- [ ] **Step 1: TimelineCanvas 按 effective_mode 判断 grid 模式**

Open `frontend/src/components/canvas/timeline/TimelineCanvas.tsx:188` (the `isGridMode` line) and replace with:

```tsx
// frontend/src/components/canvas/timeline/TimelineCanvas.tsx:188（替换）
import { effectiveMode } from "@/utils/generation-mode";  // add to top-level imports

// ...
const currentEpisodeMeta = projectData?.episodes?.find((e) => e.episode === episode);
const isGridMode = effectiveMode(projectData, currentEpisodeMeta) === "grid";
```

- [ ] **Step 2: StudioCanvasRouter 分派 + toolbar 集成**

Open `frontend/src/components/canvas/StudioCanvasRouter.tsx`. Add imports:

```tsx
import { ReferenceVideoCanvas } from "./reference/ReferenceVideoCanvas";
import { EpisodeModeSwitcher } from "./EpisodeModeSwitcher";
import { effectiveMode, normalizeMode, type GenerationMode } from "@/utils/generation-mode";
```

Add callback for episode-level mode PATCH near the other handlers (before `handleRestoreAsset`):

```tsx
// frontend/src/components/canvas/StudioCanvasRouter.tsx（追加）
const handleEpisodeModeChange = useCallback(
  async (epNum: number, next: GenerationMode) => {
    if (!currentProjectName || !currentProjectData) return;
    const episodes = (currentProjectData.episodes ?? []).map((e) =>
      e.episode === epNum ? { ...e, generation_mode: next } : e,
    );
    try {
      await API.updateProject(currentProjectName, { episodes });
      await refreshProject();
    } catch (err) {
      useAppStore.getState().pushToast(
        tRef.current("update_failed", { message: (err as Error).message }),
        "error",
      );
    }
  },
  [currentProjectName, currentProjectData, refreshProject],
);
```

Replace the `<Route path="/episodes/:episodeId">` block with:

```tsx
// frontend/src/components/canvas/StudioCanvasRouter.tsx（替换整个 episodes/:episodeId 路由）
<Route path="/episodes/:episodeId">
  {(params) => {
    const epNum = parseInt(params.episodeId, 10);
    const episode = currentProjectData?.episodes?.find((e) => e.episode === epNum);
    const scriptFile = episode?.script_file?.replace(/^scripts\//, "");
    const script = scriptFile ? (currentScripts[scriptFile] ?? null) : null;
    const mode = effectiveMode(currentProjectData, episode);
    const projectMode = normalizeMode(currentProjectData?.generation_mode);
    const episodeOverride = episode?.generation_mode
      ? normalizeMode(episode.generation_mode)
      : undefined;
    const hasDraft =
      episode?.script_status === "segmented" || episode?.script_status === "generated";

    return (
      <div className="flex h-full flex-col">
        <div className="border-b border-gray-800 px-4 py-2">
          <EpisodeModeSwitcher
            projectMode={projectMode}
            episodeMode={episodeOverride}
            onChange={(next) => void handleEpisodeModeChange(epNum, next)}
          />
        </div>
        <div className="min-h-0 flex-1">
          {mode === "reference_video" ? (
            <ReferenceVideoCanvas
              key={epNum}
              projectName={currentProjectName}
              episode={epNum}
              episodeTitle={episode?.title}
            />
          ) : (
            <TimelineCanvas
              key={epNum}
              projectName={currentProjectName}
              episode={epNum}
              episodeTitle={episode?.title}
              hasDraft={hasDraft}
              episodeScript={script}
              scriptFile={scriptFile ?? undefined}
              projectData={currentProjectData}
              durationOptions={durationOptions}
              onUpdatePrompt={voidPromise(handleUpdatePrompt)}
              onGenerateStoryboard={voidPromise(handleGenerateStoryboard)}
              onGenerateVideo={voidPromise(handleGenerateVideo)}
              onGenerateGrid={voidPromise(handleGenerateGrid)}
              onRestoreStoryboard={handleRestoreAsset}
              onRestoreVideo={handleRestoreAsset}
            />
          )}
        </div>
      </div>
    );
  }}
</Route>
```

- [ ] **Step 3: 运行 typecheck + 相关测试**

Run: `cd frontend && pnpm exec tsc --noEmit && pnpm vitest run src/components/canvas/timeline/ src/components/canvas/reference/`
Expected: PASS — 旧 TimelineCanvas 测试未破坏，新 Reference Canvas 测试通过

- [ ] **Step 4: 提交**

```bash
git add frontend/src/components/canvas/StudioCanvasRouter.tsx frontend/src/components/canvas/timeline/TimelineCanvas.tsx
git commit -m "feat(frontend): dispatch canvas by effective_mode and show EpisodeModeSwitcher"
```

---

### Task 13：后端 `StatusCalculator` 增加 reference_video 分支

**Files:**
- Modify: `lib/status_calculator.py`
- Create: `tests/lib/test_status_calculator_reference.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/lib/test_status_calculator_reference.py
"""Ensure StatusCalculator computes episode stats for reference_video scripts."""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.project_manager import ProjectManager
from lib.status_calculator import StatusCalculator


@pytest.fixture
def pm(tmp_path: Path) -> ProjectManager:
    return ProjectManager(tmp_path)


def _mk_reference_script(units_total: int, units_done: int) -> dict:
    units = []
    for i in range(units_total):
        has_video = i < units_done
        units.append(
            {
                "unit_id": f"E1U{i + 1}",
                "shots": [{"duration": 3, "text": f"Shot 1 (3s): u{i}"}],
                "references": [],
                "duration_seconds": 3,
                "duration_override": False,
                "transition_to_next": "cut",
                "note": None,
                "generated_assets": {
                    "storyboard_image": None,
                    "storyboard_last_image": None,
                    "grid_id": None,
                    "grid_cell_index": None,
                    "video_clip": f"reference_videos/E1U{i + 1}.mp4" if has_video else None,
                    "video_uri": None,
                    "status": "ready" if has_video else "pending",
                },
            }
        )
    return {
        "episode": 1,
        "title": "E1",
        "content_mode": "reference_video",
        "duration_seconds": 0,
        "summary": "",
        "novel": {"title": "t", "chapter": "c"},
        "video_units": units,
    }


def test_calculate_episode_stats_reference_video_all_ready(pm: ProjectManager) -> None:
    calc = StatusCalculator(pm)
    stats = calc.calculate_episode_stats("proj", _mk_reference_script(units_total=3, units_done=3))
    assert stats["status"] == "completed"
    assert stats["units_count"] == 3
    assert stats["videos"] == {"total": 3, "completed": 3}
    # storyboards stays zeroed — reference mode does not produce storyboards
    assert stats["storyboards"] == {"total": 3, "completed": 0}
    assert stats["duration_seconds"] == 9


def test_calculate_episode_stats_reference_video_partial(pm: ProjectManager) -> None:
    calc = StatusCalculator(pm)
    stats = calc.calculate_episode_stats("proj", _mk_reference_script(units_total=3, units_done=1))
    assert stats["status"] == "in_production"
    assert stats["videos"] == {"total": 3, "completed": 1}


def test_calculate_episode_stats_reference_video_empty_draft(pm: ProjectManager) -> None:
    calc = StatusCalculator(pm)
    stats = calc.calculate_episode_stats("proj", _mk_reference_script(units_total=0, units_done=0))
    assert stats["status"] == "draft"
    assert stats["units_count"] == 0
    assert stats["duration_seconds"] == 0
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/lib/test_status_calculator_reference.py -v`
Expected: FAIL — `status` 为 `draft` 但其他断言会出错（当前 `_select_content_mode_and_items` 不识别 `reference_video`）

- [ ] **Step 3: 修改 `StatusCalculator`**

Open `lib/status_calculator.py`. Modify `_select_content_mode_and_items` to also recognize `reference_video` (return raw units) and modify `calculate_episode_stats` to branch on content_mode:

```python
# lib/status_calculator.py:27-40（扩展 _select_content_mode_and_items）
    @classmethod
    def _select_content_mode_and_items(cls, script: dict) -> tuple[str, list[dict]]:
        content_mode = script.get("content_mode")
        if content_mode == "reference_video" and isinstance(script.get("video_units"), list):
            return "reference_video", script.get("video_units", [])
        if content_mode in {"narration", "drama"}:
            if content_mode == "narration" and isinstance(script.get("segments"), list):
                return "narration", script.get("segments", [])
            if content_mode == "drama" and isinstance(script.get("scenes"), list):
                return "drama", script.get("scenes", [])

        if isinstance(script.get("video_units"), list):
            return "reference_video", script.get("video_units", [])
        if isinstance(script.get("segments"), list):
            return "narration", script.get("segments", [])
        if isinstance(script.get("scenes"), list):
            return "drama", script.get("scenes", [])

        return ("narration" if content_mode not in {"narration", "drama"} else content_mode), []
```

Replace `calculate_episode_stats` (lines ~42-75) with:

```python
# lib/status_calculator.py:42-75（替换）
    def calculate_episode_stats(self, project_name: str, script: dict) -> dict:
        """计算单集的统计信息 — 按 content_mode 分派。"""
        content_mode, items = self._select_content_mode_and_items(script)

        if content_mode == "reference_video":
            return self._calculate_reference_video_stats(items)

        default_duration = 4 if content_mode == "narration" else 8
        storyboard_done = sum(1 for i in items if i.get("generated_assets", {}).get("storyboard_image"))
        video_done = sum(1 for i in items if i.get("generated_assets", {}).get("video_clip"))
        total = len(items)

        if video_done == total and total > 0:
            status = "completed"
        elif storyboard_done > 0 or video_done > 0:
            status = "in_production"
        else:
            status = "draft"

        return {
            "scenes_count": total,
            "status": status,
            "duration_seconds": sum(i.get("duration_seconds", default_duration) for i in items),
            "storyboards": {"total": total, "completed": storyboard_done},
            "videos": {"total": total, "completed": video_done},
        }

    @staticmethod
    def _calculate_reference_video_stats(units: list[dict]) -> dict:
        """Reference-video scripts are scored by video_units[].generated_assets.video_clip."""
        total = len(units)
        video_done = sum(1 for u in units if u.get("generated_assets", {}).get("video_clip"))

        if total == 0:
            status = "draft"
        elif video_done == total:
            status = "completed"
        elif video_done > 0:
            status = "in_production"
        else:
            status = "draft"

        return {
            "scenes_count": total,
            "units_count": total,
            "status": status,
            "duration_seconds": sum(u.get("duration_seconds", 0) for u in units),
            "storyboards": {"total": total, "completed": 0},
            "videos": {"total": total, "completed": video_done},
        }
```

- [ ] **Step 4: 运行测试，确认通过 + 回归**

Run: `uv run pytest tests/lib/test_status_calculator_reference.py tests/lib/test_status_calculator.py -v`
Expected: PASS — 3 new + 所有旧测试全绿

- [ ] **Step 5: ruff 格式化**

Run: `uv run ruff check lib/status_calculator.py tests/lib/test_status_calculator_reference.py && uv run ruff format lib/status_calculator.py tests/lib/test_status_calculator_reference.py`
Expected: All checks passed / formatted

- [ ] **Step 6: 提交**

```bash
git add lib/status_calculator.py tests/lib/test_status_calculator_reference.py
git commit -m "feat(backend): add reference_video branch to StatusCalculator"
```

---

### Task 14：i18n 一致性 + 全量 check

**Files:**
- 无新增文件，仅运行校验

- [ ] **Step 1: 后端 i18n 一致性**

Run: `uv run pytest tests/test_i18n_consistency.py -v`
Expected: PASS — 若存在 key 漂移，增补对齐

- [ ] **Step 2: 前端 typecheck + 全量 test**

Run: `cd frontend && pnpm check`
Expected: PASS — typecheck 干净、所有 vitest 绿

- [ ] **Step 3: 后端完整测试（受影响模块）**

Run: `uv run pytest tests/lib/ tests/server/test_reference_videos_router.py -v`
Expected: PASS

- [ ] **Step 4: ruff 整体检查**

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: All checks passed

- [ ] **Step 5: 若有任何失败，修复根因而非绕过**

按 CLAUDE.md 要求：CI 覆盖率 ≥80%；若覆盖率下降，补齐分支测试（参考 `tests/lib/test_status_calculator_reference.py` 已添加的三条用例）。

- [ ] **Step 6: 若步骤 1-4 有 fixup，提交**

```bash
# 仅在步骤 1-4 产生需要修复的改动时
git add -p <具体文件>
git commit -m "chore: fix i18n/typecheck for reference_video PR4"
```

---

### Task 15：手测走查（Dev server）

**Files:**
- 无代码改动，对照验收

- [ ] **Step 1: 启动后端 + 前端**

Run (在两个终端)：
- `uv run python -m uvicorn server.app:app --reload --port 1241`
- `cd frontend && pnpm dev`

- [ ] **Step 2: 走查路径**

1. 访问 `http://localhost:5173/projects`，点击 **新建项目**。
2. 在 Step1 看到三模式选择器，选中 **参考生视频**，描述区出现"跳过分镜…"。
3. 创建项目后进入 `/app/projects/<name>`。
4. 打开项目设置页，Generation mode 区域显示新三选控件；切换到 **图生视频**、保存；刷新后仍保持。
5. 切回 **参考生视频**，进入 E1 剧集。若已有 reference 剧本：
   - 顶部 toolbar 有 `EpisodeModeSwitcher`，默认继承项目模式。
   - 中栏显示"PR5 再接入编辑器"的占位，右栏显示占位或现有 video_clip。
   - 点击左栏"新建 Unit"：store 发送 POST，列表新增 `E1UN`。
   - 点击生成按钮：触发 POST `.../generate`，返回 task_id，tasks store 反映状态。
6. 退回到 storyboard 模式：`TimelineCanvas` 正常渲染（未被破坏）。

- [ ] **Step 3: 记录任何异常到下一轮 PR5 的 notes**

若发现 PR5 依赖的补丁（如 MentionPicker 需要的色板 token 缺失），写入 `docs/superpowers/plans/2026-04-17-reference-to-video-pr5-frontend-editor.md` 的 TODO 区（PR5 plan 尚未写，跳过本步）。

---

## 全量验收门槛（roadmap 通用）

- 所有新增 test 通过，覆盖率 ≥ 90%（新模块）
- `uv run ruff check . && uv run ruff format .` 干净
- `pnpm check` (typecheck + test) 通过
- 对旧项目零回归：`effective_mode()` 缺省回退 `storyboard`；`TimelineCanvas` 按 `effective_mode` 判断 `isGridMode`，未破坏旧行为
- i18n key zh/en 成对添加（`test_i18n_consistency.py` 不报错）
- PR 描述里列出本 PR 覆盖的 spec 章节：§4.6、§6.1、§6.2（骨架）、§6.4

## 未完成的留给 PR5

- `MentionPicker`（combobox + 三分组 + 键盘导航）
- `ReferenceVideoCard`（prompt 编辑器 + Shot/`@` 高亮 + debounce 自动保存）
- `ReferencePanel`（缩略图 + 拖拽换序 + `+` 按钮）
- `useShotPromptHighlight` tokenizer（基于 `lib/reference_video/shot_parser.py` 同一套 regex 约定）
- `warnings` chip（缺图、Veo 超限、references 超限）
- 三色色板：`character-*` / `scene-*` / `prop-*`（与 `AssetSidebar` 一致）

本 PR 只铺路，不走上面这些能力。
