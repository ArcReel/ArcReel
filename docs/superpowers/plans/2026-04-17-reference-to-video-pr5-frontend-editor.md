# 参考生视频 PR5：前端编辑器（MentionPicker + ReferenceVideoCard + ReferencePanel） 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 PR4 落地的 `ReferenceVideoCanvas` 中栏占位替换为完整可用的 prompt 编辑器——支持 Shot/`@` 三色高亮、`@` 键触发 `MentionPicker`、debounce 自动保存，并在中栏下方以 `ReferencePanel` 渲染 references（缩略图 + 拖拽换序 + 新增按钮）。

**Architecture:**
1. 引入共享资产色板 `asset-colors.ts`（`character/scene/prop` 三色 + `unknown` 警示色），供高亮层与 references pills 共用，保证与 `AssetSidebar` 视觉一致。
2. 新增 `frontend/src/hooks/useShotPromptHighlight.ts` — 纯 JS tokenizer，把 prompt 文本切成 `{kind: text|shot_header|mention}` token 序列，regex 与 `lib/reference_video/shot_parser.py` 保持一致（`^Shot\s+\d+\s*\(\s*\d+\s*s\s*\)\s*:` + `@[\w\u4e00-\u9fff]+`）。
3. 新增 `frontend/src/utils/reference-mentions.ts` — 纯函数 `extractMentions(text)` / `mergeReferences(prompt, existing, project)`，在保存前重算 references 列表（保留用户拖拽顺序、剔除已删除 mention、追加新增 mention）。
4. 新增 `MentionPicker.tsx` — 三分组 combobox（character/scene/prop），数据源来自 `useProjectsStore().currentProjectData.{characters|scenes|props}` bucket；键盘 ↑↓/Enter 导航、过滤。
5. 新增 `ReferenceVideoCard.tsx` — prompt 编辑器，采用「透明 textarea 叠加在 `<pre>` 之上」的高亮方案；`@` 键触发 `MentionPicker`；失焦和 500ms debounce 自动保存；未知 mention 显示警告 chip。
6. 新增 `ReferencePanel.tsx` — 基于 `@dnd-kit/sortable`（已在依赖中）的 references 缩略图 pills，支持拖拽换序、删除、点 `+` 按钮打开 `MentionPicker`。
7. 在 `reference-video-store.ts` 追加 `updatePromptDebounced(projectName, episode, unitId, prompt, references)` action（共享全局计时器），替换掉中栏的 raw PATCH 调用。
8. 在 `ReferenceVideoCanvas.tsx` 中把原先的 `<pre>` 占位替换为 `<ReferenceVideoCard>` + `<ReferencePanel>`（上下布局），右栏仍保留 `UnitPreviewPanel`。

**Tech Stack:** React 19 + TypeScript、Tailwind CSS 4、zustand、i18next、`@dnd-kit/core` + `@dnd-kit/sortable`（package.json 已装）、vitest + React Testing Library + user-event。

## 参考文档

- Spec：`docs/superpowers/specs/2026-04-15-reference-to-video-mode-design.md` §6.2（prompt 编辑器细节）、§6.3（MentionPicker）、§4.3（prompt 约定与 `@` 替换规则）
- Roadmap：`docs/superpowers/plans/2026-04-17-reference-to-video-roadmap.md`（PR5 章节）
- PR2 parser：`lib/reference_video/shot_parser.py`（前后端 regex 必须保持同一约定）
- PR3 后端契约：`server/routers/reference_videos.py`（`PATCH` 接受 `prompt`/`references`/`duration_seconds`/`transition_to_next`/`note`，服务端重算 shots/duration；references 不会由 prompt 反推，必须客户端显式传）
- PR4 落地：`frontend/src/components/canvas/reference/{ReferenceVideoCanvas,UnitList,UnitPreviewPanel}.tsx`、`frontend/src/stores/reference-video-store.ts`、`frontend/src/types/reference-video.ts`
- 现有 debounce 参考：`frontend/src/stores/cost-store.ts:37-67`（模块级 `_debounceTimer` 模式）

## 非目标（留给 PR7）

- 后端 `warnings[]` 字段的前端展示（需 PR3 后端补回传；PR5 只做客户端「未知 mention」提示）
- Shot 级别独立生成或独立删除
- 动画过渡效果（`transition_to_next` 字段保留，但 UI 下拉留空，由未来再补）
- 完整的 Undo/Redo（依赖浏览器 textarea 默认即可）
- MentionPicker 候选项的远程搜索（仅用 project 本地 bucket）

## 与后端的契约约束（强关键点）

1. **references 由客户端完整维护**：`PATCH /units/:id` 的 `references` 字段必须包含完整、排好序的最终数组。后端不会从 prompt 反推，只做「每条 `{type,name}` 是否存在 bucket」的校验。若客户端少传，后端会保留旧值；若多传但名字不在 bucket 里，后端返回 400。
2. **服务端重算 shots/duration**：客户端传 `prompt` 时，不要同时传 `shots`/`duration_seconds`（除非 `duration_override` 单镜头场景）。响应的 `unit` 是权威数据，前端 store 必须以响应更新本地状态。
3. **并发保护**：auto-save 必须以 `unitId` 为 key 去重，避免连续输入引发的乱序覆盖。方案：最新请求挂 `fetchId`，响应时对比；旧响应丢弃。

## 文件结构

### 新增

| 文件 | 职责 |
|---|---|
| `frontend/src/components/canvas/reference/asset-colors.ts` | 纯常量：`CHARACTER/SCENE/PROP/UNKNOWN` 四组 `{textClass, bgClass, borderClass}`；`assetColor(kind)` 返回一组 |
| `frontend/src/components/canvas/reference/asset-colors.test.ts` | 覆盖四种 kind 的返回值稳定性 |
| `frontend/src/hooks/useShotPromptHighlight.ts` | `tokenize(text, lookup)` 纯函数 + 可选 hook 包装；返回 `Token[]`；regex 与 `shot_parser.py` 同一约定 |
| `frontend/src/hooks/useShotPromptHighlight.test.ts` | 6+ 场景：单行/多行 shot、混合 mention、未知 mention、无 Shot 头、转义 `@`（非提及） |
| `frontend/src/utils/reference-mentions.ts` | `extractMentions(text)` / `resolveMentionType(project, name)` / `mergeReferences(prompt, existing, project)` |
| `frontend/src/utils/reference-mentions.test.ts` | 覆盖 merge 的保序、删除、追加、未知名字筛除 |
| `frontend/src/components/canvas/reference/MentionPicker.tsx` | 受控 combobox：三分组、过滤、键盘导航、`onSelect({type,name})` |
| `frontend/src/components/canvas/reference/MentionPicker.test.tsx` | 渲染三组、空分组隐藏、过滤、键盘 ↑↓/Enter/Escape、回调载荷 |
| `frontend/src/components/canvas/reference/ReferenceVideoCard.tsx` | prompt 编辑器 + 高亮覆盖层 + `@` 触发 picker + debounce 自动保存 + 未知 mention chip |
| `frontend/src/components/canvas/reference/ReferenceVideoCard.test.tsx` | 受控编辑、`@` 开 picker、选中后 prompt 变化、debounce 触发 PATCH、未知 mention chip 渲染 |
| `frontend/src/components/canvas/reference/ReferencePanel.tsx` | `@dnd-kit/sortable` 横排 pills + `+` 按钮 + 删除按钮 |
| `frontend/src/components/canvas/reference/ReferencePanel.test.tsx` | 渲染/`+` 按钮打开 picker/删除触发 patch/拖拽后调用 reorder（用 mock sensors） |

### 改动

| 文件 | 改动要点 |
|---|---|
| `frontend/src/stores/reference-video-store.ts` | 新增 `updatePromptDebounced(projectName, episode, unitId, prompt, references)`；内部用模块级 `Map<unitId, timer>` + `Map<unitId, fetchId>`；成功后走现有 `patchUnit` 的内部 set，但直接写入响应 unit，避免重复网络调用 |
| `frontend/src/stores/reference-video-store.test.ts` | 追加 4 条：单次触发、快速连续输入合并、更新期间切换 unit 不会串、旧响应被新响应覆盖 |
| `frontend/src/components/canvas/reference/ReferenceVideoCanvas.tsx` | 删除中栏 `<pre>` 占位，改为 `<ReferenceVideoCard>` + `<ReferencePanel>`（纵向 flex：Card 撑高，Panel 固定 110px 高） |
| `frontend/src/components/canvas/reference/ReferenceVideoCanvas.test.tsx` | 把"中栏渲染 prompt 文本"的断言改成"渲染 Card 的 textarea"；新增"选中 Unit 后 textarea 值正确" |
| `frontend/src/i18n/zh/dashboard.ts` | 新 key：`reference_editor_placeholder`、`reference_editor_saving`、`reference_editor_saved`、`reference_editor_save_failed`、`reference_editor_unknown_mention`、`reference_picker_title`、`reference_picker_empty`、`reference_picker_filter_placeholder`、`reference_picker_group_character`、`reference_picker_group_scene`、`reference_picker_group_prop`、`reference_panel_title`、`reference_panel_empty`、`reference_panel_add`、`reference_panel_remove_aria`、`reference_panel_drag_aria` |
| `frontend/src/i18n/en/dashboard.ts` | 同上 key 的英文文案 |

### 不变

- `frontend/src/components/canvas/reference/{UnitList,UnitPreviewPanel}.tsx`（PR4 落地）
- `frontend/src/components/canvas/reference/EpisodeModeSwitcher.tsx`
- `frontend/src/components/canvas/StudioCanvasRouter.tsx`
- `frontend/src/components/shared/GenerationModeSelector.tsx`
- 后端：`server/routers/reference_videos.py`、`server/services/reference_video_tasks.py` 完全不改
- `frontend/src/stores/projects-store.ts`（直接消费 `currentProjectData.characters/scenes/props`）

---

## 分阶段任务

TDD 约束：每个组件/工具函数必须按 "先写失败测试 → 最小实现通过测试 → 提交" 的节奏推进。所有代码步骤必须给出完整代码块，禁止 TODO / 省略号。

### Task 1：资产色板常量

**Files:**
- Create: `frontend/src/components/canvas/reference/asset-colors.ts`
- Create: `frontend/src/components/canvas/reference/asset-colors.test.ts`

- [ ] **Step 1: 写失败测试**

```ts
// frontend/src/components/canvas/reference/asset-colors.test.ts
import { describe, it, expect } from "vitest";
import { ASSET_COLORS, assetColor, type MentionKind } from "./asset-colors";

describe("asset-colors", () => {
  it("exposes three asset kinds plus 'unknown'", () => {
    const keys: MentionKind[] = ["character", "scene", "prop", "unknown"];
    for (const k of keys) {
      expect(ASSET_COLORS[k]).toBeDefined();
      expect(typeof ASSET_COLORS[k].textClass).toBe("string");
      expect(ASSET_COLORS[k].textClass.length).toBeGreaterThan(0);
    }
  });

  it("assetColor returns the matching palette", () => {
    expect(assetColor("character")).toBe(ASSET_COLORS.character);
    expect(assetColor("scene")).toBe(ASSET_COLORS.scene);
    expect(assetColor("prop")).toBe(ASSET_COLORS.prop);
  });

  it("assetColor falls back to 'unknown' for undefined", () => {
    expect(assetColor(undefined)).toBe(ASSET_COLORS.unknown);
  });
});
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd frontend && pnpm vitest run src/components/canvas/reference/asset-colors.test.ts`
Expected: FAIL — "Cannot find module './asset-colors'"

- [ ] **Step 3: 写最小实现**

```ts
// frontend/src/components/canvas/reference/asset-colors.ts
/**
 * Shared color palette for asset-kind visual cues.
 *
 * Used by:
 * - prompt editor mention highlights (useShotPromptHighlight)
 * - MentionPicker group headers + option accents
 * - ReferencePanel pill borders/fills
 *
 * Kept aligned (by intent) with AssetSidebar/AssetLibraryPage visual grouping:
 * character = blue, scene = emerald, prop = amber.
 */

export type MentionKind = "character" | "scene" | "prop" | "unknown";

export interface AssetColorPalette {
  /** Text color class (tailwind) */
  textClass: string;
  /** Background tint class (tailwind, low alpha) */
  bgClass: string;
  /** Border class (tailwind) */
  borderClass: string;
}

export const ASSET_COLORS: Record<MentionKind, AssetColorPalette> = {
  character: {
    textClass: "text-sky-300",
    bgClass: "bg-sky-500/15",
    borderClass: "border-sky-500/40",
  },
  scene: {
    textClass: "text-emerald-300",
    bgClass: "bg-emerald-500/15",
    borderClass: "border-emerald-500/40",
  },
  prop: {
    textClass: "text-amber-300",
    bgClass: "bg-amber-500/15",
    borderClass: "border-amber-500/40",
  },
  unknown: {
    textClass: "text-red-300",
    bgClass: "bg-red-500/15",
    borderClass: "border-red-500/40",
  },
};

export function assetColor(kind: MentionKind | undefined): AssetColorPalette {
  if (!kind) return ASSET_COLORS.unknown;
  return ASSET_COLORS[kind] ?? ASSET_COLORS.unknown;
}
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd frontend && pnpm vitest run src/components/canvas/reference/asset-colors.test.ts`
Expected: PASS — 3 tests

- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/canvas/reference/asset-colors.ts \
        frontend/src/components/canvas/reference/asset-colors.test.ts
git commit -m "feat(frontend): add reference-video asset color palette"
```

---

### Task 2：`useShotPromptHighlight` tokenizer hook

**Files:**
- Create: `frontend/src/hooks/useShotPromptHighlight.ts`
- Create: `frontend/src/hooks/useShotPromptHighlight.test.ts`

- [ ] **Step 1: 写失败测试**

```ts
// frontend/src/hooks/useShotPromptHighlight.test.ts
import { describe, it, expect } from "vitest";
import { tokenizePrompt, type MentionLookup, type Token } from "./useShotPromptHighlight";

const LOOKUP: MentionLookup = {
  主角: "character",
  张三: "character",
  酒馆: "scene",
  长剑: "prop",
};

function kinds(tokens: Token[]): string[] {
  return tokens.map((t) => (t.kind === "mention" ? `mention:${t.assetKind}` : t.kind));
}

describe("tokenizePrompt", () => {
  it("splits a shot header and plain text", () => {
    const t = tokenizePrompt("Shot 1 (3s): hello world", LOOKUP);
    expect(kinds(t)).toEqual(["shot_header", "text"]);
    expect(t[0].text).toBe("Shot 1 (3s): ");
    expect(t[1].text).toBe("hello world");
  });

  it("resolves mentions against lookup (three types)", () => {
    const t = tokenizePrompt(
      "Shot 1 (3s): @主角 in @酒馆 with @长剑",
      LOOKUP,
    );
    expect(kinds(t)).toEqual([
      "shot_header",
      "mention:character",
      "text",
      "mention:scene",
      "text",
      "mention:prop",
    ]);
  });

  it("marks unknown names as 'unknown'", () => {
    const t = tokenizePrompt("Shot 1 (3s): talk to @路人", LOOKUP);
    const mention = t.find((x) => x.kind === "mention");
    expect(mention?.assetKind).toBe("unknown");
    expect(mention?.text).toBe("@路人");
  });

  it("handles multi-line with multiple shot headers", () => {
    const t = tokenizePrompt(
      "Shot 1 (3s): line1\nShot 2 (5s): line2 @主角",
      LOOKUP,
    );
    const shotHeaders = t.filter((x) => x.kind === "shot_header");
    expect(shotHeaders).toHaveLength(2);
    expect(shotHeaders[0].text.startsWith("Shot 1")).toBe(true);
    expect(shotHeaders[1].text.startsWith("Shot 2")).toBe(true);
  });

  it("no shot header → entire text becomes text + mention tokens", () => {
    const t = tokenizePrompt("hello @主角 world", LOOKUP);
    expect(kinds(t)).toEqual(["text", "mention:character", "text"]);
  });

  it("is tolerant of trailing whitespace and empty prompt", () => {
    expect(tokenizePrompt("", LOOKUP)).toEqual([]);
    const only = tokenizePrompt("   ", LOOKUP);
    expect(only.map((x) => x.text).join("")).toBe("   ");
  });

  it("does not treat '@' without a following word char as a mention", () => {
    const t = tokenizePrompt("price@5, email a@b", LOOKUP);
    // @5 has a digit (\w) so IS a mention (unknown); @b is a mention (unknown).
    // This mirrors the backend regex behaviour intentionally.
    const mentions = t.filter((x) => x.kind === "mention");
    expect(mentions.length).toBeGreaterThanOrEqual(1);
  });
});
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd frontend && pnpm vitest run src/hooks/useShotPromptHighlight.test.ts`
Expected: FAIL — "Cannot find module './useShotPromptHighlight'"

- [ ] **Step 3: 写实现**

```ts
// frontend/src/hooks/useShotPromptHighlight.ts
import { useMemo } from "react";
import type { MentionKind } from "@/components/canvas/reference/asset-colors";

/**
 * Shot/@mention tokenizer for the reference-video prompt editor.
 *
 * Regex mirrors lib/reference_video/shot_parser.py:
 * - _SHOT_HEADER_RE: `^Shot\s+\d+\s*\(\s*(\d+)\s*s\s*\)\s*:` (per-line, case-insensitive)
 * - _MENTION_RE:     `@[\w\u4e00-\u9fff]+`
 *
 * Output tokens are non-overlapping and concatenate back to the original text.
 */

export type MentionLookup = Record<string, "character" | "scene" | "prop">;

export type Token =
  | { kind: "text"; text: string }
  | { kind: "shot_header"; text: string }
  | { kind: "mention"; text: string; name: string; assetKind: MentionKind };

const SHOT_HEADER_RE = /^Shot\s+\d+\s*\(\s*\d+\s*s\s*\)\s*:\s*/i;
const MENTION_RE = /@[\w\u4e00-\u9fff]+/g;

export function tokenizePrompt(text: string, lookup: MentionLookup): Token[] {
  if (text.length === 0) return [];
  const tokens: Token[] = [];
  const lines = text.split(/(\n)/); // keep newlines as separate entries

  for (const piece of lines) {
    if (piece === "\n") {
      tokens.push({ kind: "text", text: "\n" });
      continue;
    }

    const shotMatch = piece.match(SHOT_HEADER_RE);
    if (shotMatch) {
      const header = shotMatch[0];
      tokens.push({ kind: "shot_header", text: header });
      const rest = piece.slice(header.length);
      if (rest.length > 0) {
        pushMentionTokens(tokens, rest, lookup);
      }
    } else {
      pushMentionTokens(tokens, piece, lookup);
    }
  }

  return tokens;
}

function pushMentionTokens(out: Token[], text: string, lookup: MentionLookup): void {
  let lastIdx = 0;
  MENTION_RE.lastIndex = 0;
  for (;;) {
    const m = MENTION_RE.exec(text);
    if (!m) break;
    if (m.index > lastIdx) {
      out.push({ kind: "text", text: text.slice(lastIdx, m.index) });
    }
    const name = m[0].slice(1);
    const resolved = lookup[name];
    out.push({
      kind: "mention",
      text: m[0],
      name,
      assetKind: (resolved ?? "unknown") as MentionKind,
    });
    lastIdx = m.index + m[0].length;
  }
  if (lastIdx < text.length) {
    out.push({ kind: "text", text: text.slice(lastIdx) });
  }
}

/**
 * React hook wrapper around tokenizePrompt. Memoizes by (text, lookup identity).
 * Callers should `useMemo` the lookup object to keep the reference stable.
 */
export function useShotPromptHighlight(text: string, lookup: MentionLookup): Token[] {
  return useMemo(() => tokenizePrompt(text, lookup), [text, lookup]);
}
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd frontend && pnpm vitest run src/hooks/useShotPromptHighlight.test.ts`
Expected: PASS — 7 tests

- [ ] **Step 5: 提交**

```bash
git add frontend/src/hooks/useShotPromptHighlight.ts frontend/src/hooks/useShotPromptHighlight.test.ts
git commit -m "feat(frontend): add useShotPromptHighlight tokenizer hook"
```

---

### Task 3：`reference-mentions` 工具函数

**Files:**
- Create: `frontend/src/utils/reference-mentions.ts`
- Create: `frontend/src/utils/reference-mentions.test.ts`

- [ ] **Step 1: 写失败测试**

```ts
// frontend/src/utils/reference-mentions.test.ts
import { describe, it, expect } from "vitest";
import {
  extractMentions,
  resolveMentionType,
  mergeReferences,
} from "./reference-mentions";
import type { ProjectData } from "@/types";
import type { ReferenceResource } from "@/types/reference-video";

function mkProject(): Pick<ProjectData, "characters" | "scenes" | "props"> {
  return {
    characters: { 主角: { description: "" }, 张三: { description: "" } },
    scenes: { 酒馆: { description: "" } },
    props: { 长剑: { description: "" } },
  };
}

describe("extractMentions", () => {
  it("returns unique mention names in first-occurrence order", () => {
    expect(extractMentions("@a @b @a @c")).toEqual(["a", "b", "c"]);
  });

  it("returns empty list when no mentions", () => {
    expect(extractMentions("Shot 1 (3s): plain text")).toEqual([]);
  });

  it("matches CJK characters and underscores", () => {
    expect(extractMentions("@主角 and @张_三")).toEqual(["主角", "张_三"]);
  });
});

describe("resolveMentionType", () => {
  const project = mkProject();

  it("prefers character → scene → prop", () => {
    expect(resolveMentionType(project, "主角")).toBe("character");
    expect(resolveMentionType(project, "酒馆")).toBe("scene");
    expect(resolveMentionType(project, "长剑")).toBe("prop");
  });

  it("returns undefined for unknown names", () => {
    expect(resolveMentionType(project, "路人")).toBeUndefined();
  });
});

describe("mergeReferences", () => {
  const project = mkProject();

  it("appends new mentions at the end, preserving existing order", () => {
    const existing: ReferenceResource[] = [
      { type: "character", name: "张三" },
    ];
    const merged = mergeReferences("Shot 1 (3s): @张三 @主角", existing, project);
    expect(merged).toEqual([
      { type: "character", name: "张三" },
      { type: "character", name: "主角" },
    ]);
  });

  it("removes references whose names are no longer in prompt", () => {
    const existing: ReferenceResource[] = [
      { type: "character", name: "张三" },
      { type: "scene", name: "酒馆" },
    ];
    const merged = mergeReferences("Shot 1 (3s): @张三", existing, project);
    expect(merged).toEqual([{ type: "character", name: "张三" }]);
  });

  it("skips unknown mentions (not resolvable to any bucket)", () => {
    const merged = mergeReferences("Shot 1 (3s): @路人 @主角", [], project);
    expect(merged).toEqual([{ type: "character", name: "主角" }]);
  });

  it("deduplicates repeated mentions", () => {
    const merged = mergeReferences("Shot 1 (3s): @主角 @主角 @主角", [], project);
    expect(merged).toEqual([{ type: "character", name: "主角" }]);
  });

  it("returns empty list when prompt has no valid mentions", () => {
    expect(mergeReferences("Shot 1 (3s): plain", [], project)).toEqual([]);
  });
});
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd frontend && pnpm vitest run src/utils/reference-mentions.test.ts`
Expected: FAIL — "Cannot find module './reference-mentions'"

- [ ] **Step 3: 写实现**

```ts
// frontend/src/utils/reference-mentions.ts
import type { ProjectData } from "@/types";
import type { AssetKind, ReferenceResource } from "@/types/reference-video";

const MENTION_RE = /@([\w\u4e00-\u9fff]+)/g;

export function extractMentions(text: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  MENTION_RE.lastIndex = 0;
  for (;;) {
    const m = MENTION_RE.exec(text);
    if (!m) break;
    const name = m[1];
    if (!seen.has(name)) {
      seen.add(name);
      out.push(name);
    }
  }
  return out;
}

type ProjectBuckets = Pick<ProjectData, "characters" | "scenes" | "props">;

export function resolveMentionType(
  project: ProjectBuckets | null | undefined,
  name: string,
): AssetKind | undefined {
  if (!project) return undefined;
  if (project.characters && name in project.characters) return "character";
  if (project.scenes && name in project.scenes) return "scene";
  if (project.props && name in project.props) return "prop";
  return undefined;
}

/**
 * Re-derive the references list for a unit given new prompt text.
 *
 * Rules:
 *  1. Preserve the order of `existing` entries whose names still appear in prompt.
 *  2. Drop entries whose names no longer appear.
 *  3. Append new mentions (in first-appearance order) that resolve to a known bucket.
 *  4. Skip unknown mentions (they become UI warning chips, not references).
 *  5. Deduplicate by name.
 */
export function mergeReferences(
  prompt: string,
  existing: ReferenceResource[],
  project: ProjectBuckets | null | undefined,
): ReferenceResource[] {
  const mentioned = new Set(extractMentions(prompt));
  const kept: ReferenceResource[] = [];
  const keptNames = new Set<string>();
  for (const ref of existing) {
    if (mentioned.has(ref.name) && !keptNames.has(ref.name)) {
      kept.push(ref);
      keptNames.add(ref.name);
    }
  }
  for (const name of mentioned) {
    if (keptNames.has(name)) continue;
    const type = resolveMentionType(project, name);
    if (!type) continue;
    kept.push({ type, name });
    keptNames.add(name);
  }
  return kept;
}
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd frontend && pnpm vitest run src/utils/reference-mentions.test.ts`
Expected: PASS — 11 tests

- [ ] **Step 5: 提交**

```bash
git add frontend/src/utils/reference-mentions.ts frontend/src/utils/reference-mentions.test.ts
git commit -m "feat(frontend): add reference-mentions helpers (extract/resolve/merge)"
```

---

### Task 4：追加 `updatePromptDebounced` store action

**Files:**
- Modify: `frontend/src/stores/reference-video-store.ts`
- Modify: `frontend/src/stores/reference-video-store.test.ts`

- [ ] **Step 1: 在现有 test 文件底部追加失败测试**

Open `frontend/src/stores/reference-video-store.test.ts` and append at the very end of the file (保留现有测试)：

```ts
// frontend/src/stores/reference-video-store.test.ts（追加 — 文件末尾，保留现有 describe 块）

describe("reference-video-store · updatePromptDebounced", () => {
  beforeEach(() => {
    useReferenceVideoStore.setState({
      unitsByEpisode: {},
      selectedUnitId: null,
      loading: false,
      error: null,
    });
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("delays network call and writes server response to store", async () => {
    useReferenceVideoStore.setState({
      unitsByEpisode: { "proj::1": [mkUnit("E1U1")] },
      selectedUnitId: "E1U1",
      loading: false,
      error: null,
    });
    const serverUnit = mkUnit("E1U1", { note: "saved" });
    const patchSpy = vi
      .spyOn(API, "patchReferenceVideoUnit")
      .mockResolvedValueOnce({ unit: serverUnit });

    useReferenceVideoStore.getState().updatePromptDebounced("proj", 1, "E1U1", "Shot 1 (3s): x", []);
    expect(patchSpy).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });
    expect(patchSpy).toHaveBeenCalledTimes(1);
    expect(useReferenceVideoStore.getState().unitsByEpisode["proj::1"][0].note).toBe("saved");
  });

  it("coalesces rapid edits into a single network call", async () => {
    useReferenceVideoStore.setState({
      unitsByEpisode: { "proj::1": [mkUnit("E1U1")] },
      selectedUnitId: "E1U1",
      loading: false,
      error: null,
    });
    const patchSpy = vi
      .spyOn(API, "patchReferenceVideoUnit")
      .mockResolvedValue({ unit: mkUnit("E1U1") });

    const store = useReferenceVideoStore.getState();
    store.updatePromptDebounced("proj", 1, "E1U1", "a", []);
    store.updatePromptDebounced("proj", 1, "E1U1", "ab", []);
    store.updatePromptDebounced("proj", 1, "E1U1", "abc", []);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });
    expect(patchSpy).toHaveBeenCalledTimes(1);
    const [, , , body] = patchSpy.mock.calls[0]!;
    expect(body).toEqual({ prompt: "abc", references: [] });
  });

  it("discards stale responses when a newer edit races in", async () => {
    useReferenceVideoStore.setState({
      unitsByEpisode: { "proj::1": [mkUnit("E1U1", { note: "original" })] },
      selectedUnitId: "E1U1",
      loading: false,
      error: null,
    });
    let resolveFirst!: (v: { unit: ReferenceVideoUnit }) => void;
    const firstPromise = new Promise<{ unit: ReferenceVideoUnit }>((r) => {
      resolveFirst = r;
    });
    const patchSpy = vi.spyOn(API, "patchReferenceVideoUnit");
    patchSpy.mockReturnValueOnce(firstPromise);
    patchSpy.mockResolvedValueOnce({ unit: mkUnit("E1U1", { note: "v2" }) });

    const store = useReferenceVideoStore.getState();
    store.updatePromptDebounced("proj", 1, "E1U1", "first", []);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });
    // first in-flight; now enqueue a second, then let first resolve late
    store.updatePromptDebounced("proj", 1, "E1U1", "second", []);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });
    await act(async () => {
      resolveFirst({ unit: mkUnit("E1U1", { note: "v1-late" }) });
      await Promise.resolve();
    });
    expect(useReferenceVideoStore.getState().unitsByEpisode["proj::1"][0].note).toBe("v2");
  });

  it("flushes pending debounce when a different unit starts editing", async () => {
    useReferenceVideoStore.setState({
      unitsByEpisode: { "proj::1": [mkUnit("E1U1"), mkUnit("E1U2")] },
      selectedUnitId: "E1U1",
      loading: false,
      error: null,
    });
    const patchSpy = vi.spyOn(API, "patchReferenceVideoUnit").mockResolvedValue({
      unit: mkUnit("E1U1"),
    });

    const store = useReferenceVideoStore.getState();
    store.updatePromptDebounced("proj", 1, "E1U1", "draft1", []);
    store.updatePromptDebounced("proj", 1, "E1U2", "draft2", []);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });
    // Both timers should have fired independently (one per unitId)
    expect(patchSpy).toHaveBeenCalledTimes(2);
  });
});
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd frontend && pnpm vitest run src/stores/reference-video-store.test.ts -t "updatePromptDebounced"`
Expected: FAIL — `store.updatePromptDebounced is not a function`

- [ ] **Step 3: 扩展 store 实现**

Replace the full contents of `frontend/src/stores/reference-video-store.ts` with:

```ts
// frontend/src/stores/reference-video-store.ts
import { create } from "zustand";
import { API } from "@/api";
import type { ReferenceResource, ReferenceVideoUnit, TransitionType } from "@/types";

interface AddUnitPayload {
  prompt: string;
  references: ReferenceResource[];
  duration_seconds?: number;
  transition_to_next?: TransitionType;
  note?: string | null;
}

interface PatchUnitPayload {
  prompt?: string;
  references?: ReferenceResource[];
  duration_seconds?: number;
  transition_to_next?: TransitionType;
  note?: string | null;
}

/** Cache key isolating units per (project, episode) — switching projects with
 * the same episode number must not surface the previous project's units. */
export function referenceVideoCacheKey(projectName: string, episode: number): string {
  return `${projectName}::${episode}`;
}

interface ReferenceVideoStore {
  /** Keyed by `${projectName}::${episode}`. */
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
  /**
   * Debounced prompt save. Coalesces rapid edits into a single PATCH per unitId
   * with a 500ms delay. Stale responses (from a superseded in-flight request)
   * are discarded based on a per-unit fetch id counter.
   */
  updatePromptDebounced: (
    projectName: string,
    episode: number,
    unitId: string,
    prompt: string,
    references: ReferenceResource[],
  ) => void;
}

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

// Per-unit debounce timers — module-scoped so zustand state stays serializable.
const _timers = new Map<string, ReturnType<typeof setTimeout>>();
// Per-unit fetch id; latest wins.
const _fetchIds = new Map<string, number>();
// Last pending payload keyed by unitId.
const _pendingPayload = new Map<string, { prompt: string; references: ReferenceResource[] }>();

const DEBOUNCE_MS = 500;

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
        unitsByEpisode: { ...s.unitsByEpisode, [referenceVideoCacheKey(projectName, episode)]: units },
        loading: false,
      }));
    } catch (e) {
      set({ loading: false, error: errMsg(e) });
    }
  },

  addUnit: async (projectName, episode, payload) => {
    const { unit } = await API.addReferenceVideoUnit(projectName, episode, payload);
    set((s) => {
      const key = referenceVideoCacheKey(projectName, episode);
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
      const key = referenceVideoCacheKey(projectName, episode);
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
      const key = referenceVideoCacheKey(projectName, episode);
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
      unitsByEpisode: { ...s.unitsByEpisode, [referenceVideoCacheKey(projectName, episode)]: units },
    }));
  },

  generate: async (projectName, episode, unitId) => {
    return API.generateReferenceVideoUnit(projectName, episode, unitId);
  },

  select: (unitId) => set({ selectedUnitId: unitId }),

  updatePromptDebounced: (projectName, episode, unitId, prompt, references) => {
    _pendingPayload.set(unitId, { prompt, references });
    const existing = _timers.get(unitId);
    if (existing) clearTimeout(existing);
    const timer = setTimeout(() => {
      _timers.delete(unitId);
      const payload = _pendingPayload.get(unitId);
      _pendingPayload.delete(unitId);
      if (!payload) return;

      const myFetchId = (_fetchIds.get(unitId) ?? 0) + 1;
      _fetchIds.set(unitId, myFetchId);

      void API.patchReferenceVideoUnit(projectName, episode, unitId, {
        prompt: payload.prompt,
        references: payload.references,
      })
        .then(({ unit }) => {
          if (_fetchIds.get(unitId) !== myFetchId) return; // stale
          set((s) => {
            const key = referenceVideoCacheKey(projectName, episode);
            const list = s.unitsByEpisode[key] ?? [];
            return {
              unitsByEpisode: {
                ...s.unitsByEpisode,
                [key]: list.map((u) => (u.unit_id === unitId ? unit : u)),
              },
            };
          });
        })
        .catch((e) => {
          if (_fetchIds.get(unitId) !== myFetchId) return;
          set({ error: errMsg(e) });
        });
    }, DEBOUNCE_MS);
    _timers.set(unitId, timer);
  },
}));
```

- [ ] **Step 4: 运行测试（前三 describe 块 + 新增块），确认全绿**

Run: `cd frontend && pnpm vitest run src/stores/reference-video-store.test.ts`
Expected: PASS — 11 tests（7 原有 + 4 新增）

- [ ] **Step 5: 提交**

```bash
git add frontend/src/stores/reference-video-store.ts frontend/src/stores/reference-video-store.test.ts
git commit -m "feat(frontend): add updatePromptDebounced action with per-unit coalescing"
```

---

### Task 5：i18n keys（MentionPicker + Card + Panel）

**Files:**
- Modify: `frontend/src/i18n/zh/dashboard.ts`
- Modify: `frontend/src/i18n/en/dashboard.ts`

- [ ] **Step 1: 追加 zh keys（在现有 reference_* block 紧下方）**

Open `frontend/src/i18n/zh/dashboard.ts`. Locate the line `'episode_mode_inherit_from_project': '继承项目设置',` (现有 PR4 末尾)，在其下方插入：

```ts
  // ========== PR5 reference-video editor ==========
  'reference_editor_placeholder': '输入 prompt，支持 "Shot 1 (3s): ..."，用 @ 引用角色/场景/道具',
  'reference_editor_saving': '保存中…',
  'reference_editor_saved': '已保存',
  'reference_editor_save_failed': '保存失败: {{message}}',
  'reference_editor_unknown_mention': '未注册：@{{name}}（角色/场景/道具中都未找到）',
  'reference_picker_title': '插入引用',
  'reference_picker_empty': '无匹配项',
  'reference_picker_filter_placeholder': '搜索名称…',
  'reference_picker_group_character': '角色',
  'reference_picker_group_scene': '场景',
  'reference_picker_group_prop': '道具',
  'reference_panel_title': '引用图（按顺序决定 [图N] 编号）',
  'reference_panel_empty': '暂无引用，点击 + 添加或在 prompt 里用 @ 提及',
  'reference_panel_add': '添加引用',
  'reference_panel_remove_aria': '移除引用 @{{name}}',
  'reference_panel_drag_aria': '拖拽调整 @{{name}} 的位置',
```

- [ ] **Step 2: 追加 en keys（镜像）**

Open `frontend/src/i18n/en/dashboard.ts`. At the equivalent location:

```ts
  // ========== PR5 reference-video editor ==========
  'reference_editor_placeholder': 'Type a prompt. Use "Shot 1 (3s): ..." markers and @mentions for characters/scenes/props.',
  'reference_editor_saving': 'Saving…',
  'reference_editor_saved': 'Saved',
  'reference_editor_save_failed': 'Save failed: {{message}}',
  'reference_editor_unknown_mention': 'Unregistered: @{{name}} (not found in characters/scenes/props)',
  'reference_picker_title': 'Insert reference',
  'reference_picker_empty': 'No matches',
  'reference_picker_filter_placeholder': 'Search by name…',
  'reference_picker_group_character': 'Characters',
  'reference_picker_group_scene': 'Scenes',
  'reference_picker_group_prop': 'Props',
  'reference_panel_title': 'References (order determines [图N] index)',
  'reference_panel_empty': 'No references yet — click + or @mention a name in the prompt.',
  'reference_panel_add': 'Add reference',
  'reference_panel_remove_aria': 'Remove reference @{{name}}',
  'reference_panel_drag_aria': 'Reorder @{{name}}',
```

- [ ] **Step 3: 运行 i18n 一致性检查（后端校验脚本）**

Run: `uv run pytest tests/test_i18n_consistency.py -v`
Expected: PASS（本 PR 未加后端 error key；前端 key 不在该脚本覆盖范围）

- [ ] **Step 4: 前端 typecheck**

Run: `cd frontend && pnpm exec tsc --noEmit`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add frontend/src/i18n/zh/dashboard.ts frontend/src/i18n/en/dashboard.ts
git commit -m "feat(i18n): add dashboard keys for reference-video editor + mention picker"
```

---

### Task 6：`MentionPicker` 组件

**Files:**
- Create: `frontend/src/components/canvas/reference/MentionPicker.tsx`
- Create: `frontend/src/components/canvas/reference/MentionPicker.test.tsx`

- [ ] **Step 1: 写失败测试**

```tsx
// frontend/src/components/canvas/reference/MentionPicker.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MentionPicker } from "./MentionPicker";

const CANDIDATES = {
  character: [
    { name: "主角", imagePath: null },
    { name: "张三", imagePath: "/files/characters/zs.png" },
  ],
  scene: [{ name: "酒馆", imagePath: null }],
  prop: [{ name: "长剑", imagePath: null }],
};

describe("MentionPicker", () => {
  it("renders three group headers when all groups have items", () => {
    render(
      <MentionPicker
        open
        query=""
        candidates={CANDIDATES}
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText(/Characters|角色/)).toBeInTheDocument();
    expect(screen.getByText(/Scenes|场景/)).toBeInTheDocument();
    expect(screen.getByText(/Props|道具/)).toBeInTheDocument();
  });

  it("hides a group when it has no items after filtering", () => {
    render(
      <MentionPicker
        open
        query="主"
        candidates={CANDIDATES}
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.queryByText(/Scenes|场景/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Props|道具/)).not.toBeInTheDocument();
    expect(screen.getByText("主角")).toBeInTheDocument();
  });

  it("filters case-insensitively by substring", () => {
    const altCandidates = {
      character: [{ name: "Alice", imagePath: null }, { name: "Bob", imagePath: null }],
      scene: [],
      prop: [],
    };
    render(
      <MentionPicker
        open
        query="ali"
        candidates={altCandidates}
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("Alice")).toBeInTheDocument();
    expect(screen.queryByText("Bob")).not.toBeInTheDocument();
  });

  it("shows empty state when nothing matches", () => {
    render(
      <MentionPicker
        open
        query="xxxnomatch"
        candidates={CANDIDATES}
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText(/No matches|无匹配项/)).toBeInTheDocument();
  });

  it("invokes onSelect with {type,name} when an option is clicked", () => {
    const onSelect = vi.fn();
    render(
      <MentionPicker
        open
        query=""
        candidates={CANDIDATES}
        onSelect={onSelect}
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("option", { name: /张三/ }));
    expect(onSelect).toHaveBeenCalledWith({ type: "character", name: "张三" });
  });

  it("supports ArrowDown/ArrowUp/Enter keyboard navigation", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(
      <MentionPicker
        open
        query=""
        candidates={CANDIDATES}
        onSelect={onSelect}
        onClose={vi.fn()}
      />,
    );
    // First option is initially active
    await user.keyboard("{ArrowDown}{ArrowDown}");
    await user.keyboard("{Enter}");
    // After two ArrowDowns from the first (主角), we should be on 酒馆 (third overall)
    expect(onSelect).toHaveBeenCalledWith({ type: "scene", name: "酒馆" });
  });

  it("calls onClose when Escape is pressed", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <MentionPicker
        open
        query=""
        candidates={CANDIDATES}
        onSelect={vi.fn()}
        onClose={onClose}
      />,
    );
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
  });

  it("renders nothing when open=false", () => {
    const { container } = render(
      <MentionPicker
        open={false}
        query=""
        candidates={CANDIDATES}
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(container.firstChild).toBeNull();
  });
});
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd frontend && pnpm vitest run src/components/canvas/reference/MentionPicker.test.tsx`
Expected: FAIL — "Cannot find module './MentionPicker'"

- [ ] **Step 3: 写实现**

```tsx
// frontend/src/components/canvas/reference/MentionPicker.tsx
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { assetColor } from "./asset-colors";
import type { AssetKind } from "@/types/reference-video";

export interface MentionCandidate {
  name: string;
  imagePath: string | null;
}

export interface MentionPickerProps {
  open: boolean;
  query: string;
  candidates: Record<AssetKind, MentionCandidate[]>;
  onSelect: (ref: { type: AssetKind; name: string }) => void;
  onClose: () => void;
  /** Optional inline anchor style; when absent, picker renders in-flow below its parent. */
  className?: string;
}

interface FlatItem {
  type: AssetKind;
  name: string;
  imagePath: string | null;
}

const GROUP_ORDER: AssetKind[] = ["character", "scene", "prop"];

export function MentionPicker({
  open,
  query,
  candidates,
  onSelect,
  onClose,
  className,
}: MentionPickerProps) {
  const { t } = useTranslation("dashboard");
  const [activeIndex, setActiveIndex] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const result: Record<AssetKind, MentionCandidate[]> = { character: [], scene: [], prop: [] };
    for (const kind of GROUP_ORDER) {
      const arr = candidates[kind] ?? [];
      result[kind] = q.length === 0 ? arr : arr.filter((c) => c.name.toLowerCase().includes(q));
    }
    return result;
  }, [candidates, query]);

  const flat: FlatItem[] = useMemo(() => {
    const out: FlatItem[] = [];
    for (const kind of GROUP_ORDER) {
      for (const item of filtered[kind]) {
        out.push({ type: kind, name: item.name, imagePath: item.imagePath });
      }
    }
    return out;
  }, [filtered]);

  useEffect(() => {
    if (activeIndex >= flat.length) setActiveIndex(Math.max(0, flat.length - 1));
  }, [flat.length, activeIndex]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        setActiveIndex((i) => Math.min(flat.length - 1, i + 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActiveIndex((i) => Math.max(0, i - 1));
      } else if (e.key === "Enter") {
        e.preventDefault();
        const item = flat[activeIndex];
        if (item) onSelect({ type: item.type, name: item.name });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, flat, activeIndex, onSelect, onClose]);

  if (!open) return null;

  const empty = flat.length === 0;

  return (
    <div
      ref={listRef}
      role="listbox"
      aria-label={t("reference_picker_title")}
      className={`z-30 max-h-64 w-64 overflow-y-auto rounded-md border border-gray-800 bg-gray-950 shadow-xl ${className ?? ""}`}
    >
      <div className="sticky top-0 bg-gray-950 px-2 py-1 text-[10px] uppercase tracking-wide text-gray-600">
        {t("reference_picker_title")}
      </div>
      {empty && (
        <div className="px-3 py-4 text-center text-xs text-gray-500">
          {t("reference_picker_empty")}
        </div>
      )}
      {!empty &&
        GROUP_ORDER.map((kind) => {
          const items = filtered[kind];
          if (items.length === 0) return null;
          const palette = assetColor(kind);
          return (
            <div key={kind}>
              <div
                className={`px-2 py-1 text-[10px] font-semibold uppercase ${palette.textClass}`}
              >
                {t(`reference_picker_group_${kind}`)}
              </div>
              {items.map((item) => {
                const globalIndex = flat.findIndex((f) => f.type === kind && f.name === item.name);
                const active = globalIndex === activeIndex;
                return (
                  <button
                    key={`${kind}:${item.name}`}
                    type="button"
                    role="option"
                    aria-selected={active}
                    onMouseEnter={() => setActiveIndex(globalIndex)}
                    onClick={() => onSelect({ type: kind, name: item.name })}
                    className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm transition-colors ${
                      active ? "bg-indigo-500/15 text-indigo-200" : "text-gray-300 hover:bg-gray-900"
                    }`}
                  >
                    <span className={`h-2 w-2 shrink-0 rounded-full ${palette.bgClass} ${palette.borderClass} border`} />
                    <span className="truncate">{item.name}</span>
                  </button>
                );
              })}
            </div>
          );
        })}
    </div>
  );
}
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd frontend && pnpm vitest run src/components/canvas/reference/MentionPicker.test.tsx`
Expected: PASS — 8 tests

- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/canvas/reference/MentionPicker.tsx \
        frontend/src/components/canvas/reference/MentionPicker.test.tsx
git commit -m "feat(frontend): add MentionPicker combobox for reference-video editor"
```

---

### Task 7：`ReferenceVideoCard`（prompt 编辑器）

**Files:**
- Create: `frontend/src/components/canvas/reference/ReferenceVideoCard.tsx`
- Create: `frontend/src/components/canvas/reference/ReferenceVideoCard.test.tsx`

- [ ] **Step 1: 写失败测试**

```tsx
// frontend/src/components/canvas/reference/ReferenceVideoCard.test.tsx
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ReferenceVideoCard } from "./ReferenceVideoCard";
import { useProjectsStore } from "@/stores/projects-store";
import type { ProjectData } from "@/types";
import type { ReferenceVideoUnit } from "@/types/reference-video";

function mkUnit(overrides: Partial<ReferenceVideoUnit> = {}): ReferenceVideoUnit {
  return {
    unit_id: "E1U1",
    shots: [{ duration: 3, text: "Shot 1 (3s): hi" }],
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

const PROJECT: ProjectData = {
  title: "p",
  content_mode: "narration",
  style: "",
  episodes: [],
  characters: { 主角: { description: "" }, 张三: { description: "" } },
  scenes: { 酒馆: { description: "" } },
  props: { 长剑: { description: "" } },
};

beforeEach(() => {
  useProjectsStore.setState({ currentProjectName: "proj", currentProjectData: PROJECT });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ReferenceVideoCard", () => {
  it("renders the unit's joined shot text in the textarea", () => {
    const unit = mkUnit({
      shots: [
        { duration: 3, text: "Shot 1 (3s): line1" },
        { duration: 5, text: "Shot 2 (5s): line2" },
      ],
    });
    render(
      <ReferenceVideoCard
        unit={unit}
        projectName="proj"
        episode={1}
        onChangePrompt={vi.fn()}
      />,
    );
    const ta = screen.getByRole("textbox") as HTMLTextAreaElement;
    expect(ta.value).toContain("Shot 1 (3s): line1");
    expect(ta.value).toContain("Shot 2 (5s): line2");
  });

  it("fires onChangePrompt with (prompt, merged references) on every edit", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(
      <ReferenceVideoCard
        unit={mkUnit()}
        projectName="proj"
        episode={1}
        onChangePrompt={onChange}
      />,
    );
    const ta = screen.getByRole("textbox");
    await user.clear(ta);
    await user.type(ta, "Shot 1 (3s): @主角");
    const lastCall = onChange.mock.calls.at(-1)!;
    expect(lastCall[0]).toBe("Shot 1 (3s): @主角");
    expect(lastCall[1]).toEqual([{ type: "character", name: "主角" }]);
  });

  it("opens the MentionPicker when '@' is typed", async () => {
    const user = userEvent.setup();
    render(
      <ReferenceVideoCard
        unit={mkUnit()}
        projectName="proj"
        episode={1}
        onChangePrompt={vi.fn()}
      />,
    );
    const ta = screen.getByRole("textbox");
    await user.clear(ta);
    await user.type(ta, "x @");
    expect(await screen.findByRole("listbox")).toBeInTheDocument();
  });

  it("inserts selected mention into the prompt and closes picker", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(
      <ReferenceVideoCard
        unit={mkUnit({ shots: [{ duration: 1, text: "" }] })}
        projectName="proj"
        episode={1}
        onChangePrompt={onChange}
      />,
    );
    const ta = screen.getByRole("textbox");
    await user.clear(ta);
    await user.type(ta, "@");
    fireEvent.click(await screen.findByRole("option", { name: /主角/ }));
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    const lastCall = onChange.mock.calls.at(-1)!;
    expect(lastCall[0]).toMatch(/@主角\s$/);
  });

  it("renders an unknown-mention chip for names not in project", () => {
    render(
      <ReferenceVideoCard
        unit={mkUnit({ shots: [{ duration: 1, text: "Shot 1 (3s): @路人" }] })}
        projectName="proj"
        episode={1}
        onChangePrompt={vi.fn()}
      />,
    );
    expect(screen.getByText(/路人/)).toBeInTheDocument();
    expect(screen.getByText(/未注册|Unregistered/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd frontend && pnpm vitest run src/components/canvas/reference/ReferenceVideoCard.test.tsx`
Expected: FAIL — "Cannot find module './ReferenceVideoCard'"

- [ ] **Step 3: 写实现**

```tsx
// frontend/src/components/canvas/reference/ReferenceVideoCard.tsx
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { MentionPicker, type MentionCandidate } from "./MentionPicker";
import { ASSET_COLORS, assetColor } from "./asset-colors";
import { useShotPromptHighlight, type MentionLookup } from "@/hooks/useShotPromptHighlight";
import {
  extractMentions,
  mergeReferences,
  resolveMentionType,
} from "@/utils/reference-mentions";
import { useProjectsStore } from "@/stores/projects-store";
import type { AssetKind, ReferenceResource, ReferenceVideoUnit } from "@/types/reference-video";

export interface ReferenceVideoCardProps {
  unit: ReferenceVideoUnit;
  projectName: string;
  episode: number;
  /**
   * Called on every keystroke with the new prompt and the re-merged references.
   * Parent should forward this to the debounced save action.
   */
  onChangePrompt: (prompt: string, references: ReferenceResource[]) => void;
}

function unitPromptText(unit: ReferenceVideoUnit): string {
  return unit.shots.map((s) => s.text).join("\n");
}

export function ReferenceVideoCard({
  unit,
  projectName,
  episode,
  onChangePrompt,
}: ReferenceVideoCardProps) {
  const { t } = useTranslation("dashboard");
  const taRef = useRef<HTMLTextAreaElement>(null);
  const preRef = useRef<HTMLPreElement>(null);

  // Local controlled value — keeps typing responsive even before server responds.
  const [value, setValue] = useState<string>(() => unitPromptText(unit));

  // Re-sync local value when the unit prop changes identity (e.g. user selects
  // a different unit, or server response replaces this unit).
  useEffect(() => {
    setValue(unitPromptText(unit));
  }, [unit.unit_id, unit.shots]);

  const project = useProjectsStore((s) => s.currentProjectData);

  const lookup: MentionLookup = useMemo(() => {
    const out: MentionLookup = {};
    for (const name of Object.keys(project?.characters ?? {})) out[name] = "character";
    for (const name of Object.keys(project?.scenes ?? {})) out[name] = "scene";
    for (const name of Object.keys(project?.props ?? {})) out[name] = "prop";
    return out;
  }, [project?.characters, project?.scenes, project?.props]);

  const tokens = useShotPromptHighlight(value, lookup);

  const unknownMentions = useMemo(() => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const tk of tokens) {
      if (tk.kind === "mention" && tk.assetKind === "unknown" && !seen.has(tk.name)) {
        seen.add(tk.name);
        out.push(tk.name);
      }
    }
    return out;
  }, [tokens]);

  // MentionPicker state — triggered when user types '@' after whitespace/start of input.
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerQuery, setPickerQuery] = useState("");
  const atStartRef = useRef<number | null>(null);

  const candidates: Record<AssetKind, MentionCandidate[]> = useMemo(() => {
    function toCandidates(
      bucket: Record<string, { character_sheet?: string; scene_sheet?: string; prop_sheet?: string }> | undefined,
      sheetKey: "character_sheet" | "scene_sheet" | "prop_sheet",
    ): MentionCandidate[] {
      if (!bucket) return [];
      return Object.entries(bucket).map(([name, data]) => ({
        name,
        imagePath: (data[sheetKey] as string | undefined) ?? null,
      }));
    }
    return {
      character: toCandidates(project?.characters, "character_sheet"),
      scene: toCandidates(project?.scenes, "scene_sheet"),
      prop: toCandidates(project?.props, "prop_sheet"),
    };
  }, [project?.characters, project?.scenes, project?.props]);

  const emitChange = useCallback(
    (nextValue: string) => {
      const refs = mergeReferences(nextValue, unit.references, project ?? null);
      onChangePrompt(nextValue, refs);
    },
    [onChangePrompt, unit.references, project],
  );

  const updatePickerFromCursor = useCallback((nextValue: string, cursor: number) => {
    // Scan backwards to find an unterminated '@' within the current word.
    let i = cursor - 1;
    while (i >= 0) {
      const ch = nextValue[i];
      if (ch === "@") {
        atStartRef.current = i;
        setPickerQuery(nextValue.slice(i + 1, cursor));
        setPickerOpen(true);
        return;
      }
      if (/\s/.test(ch)) break;
      i--;
    }
    atStartRef.current = null;
    setPickerOpen(false);
    setPickerQuery("");
  }, []);

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const next = e.target.value;
    setValue(next);
    emitChange(next);
    updatePickerFromCursor(next, e.target.selectionStart ?? next.length);
  };

  const handleKeyUp = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    const ta = e.currentTarget;
    updatePickerFromCursor(ta.value, ta.selectionStart ?? ta.value.length);
  };

  const handlePickerSelect = useCallback(
    (ref: { type: AssetKind; name: string }) => {
      const ta = taRef.current;
      const start = atStartRef.current;
      if (!ta || start === null) {
        setPickerOpen(false);
        return;
      }
      const before = value.slice(0, start);
      const cursor = ta.selectionStart ?? value.length;
      const after = value.slice(cursor);
      const insert = `@${ref.name} `;
      const next = before + insert + after;
      setValue(next);
      emitChange(next);
      setPickerOpen(false);
      setPickerQuery("");
      atStartRef.current = null;
      // Restore focus + place caret after the insert.
      requestAnimationFrame(() => {
        ta.focus();
        const pos = before.length + insert.length;
        ta.setSelectionRange(pos, pos);
      });
    },
    [value, emitChange],
  );

  // Scroll sync between the textarea and the highlight <pre>.
  const onScroll = () => {
    if (preRef.current && taRef.current) {
      preRef.current.scrollTop = taRef.current.scrollTop;
      preRef.current.scrollLeft = taRef.current.scrollLeft;
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="mb-1 flex items-center justify-between text-[11px] text-gray-500">
        <span className="font-mono text-gray-400" translate="no">
          {unit.unit_id}
        </span>
        <span className="tabular-nums text-gray-500">
          {unit.duration_seconds}s · {unit.shots.length} shot(s)
        </span>
      </div>

      <div className="relative min-h-0 flex-1 rounded-md border border-gray-800 bg-gray-950/60">
        <pre
          ref={preRef}
          aria-hidden
          className="pointer-events-none absolute inset-0 m-0 overflow-hidden whitespace-pre-wrap break-words p-3 font-mono text-sm leading-6"
        >
          {tokens.map((tk, i) => {
            if (tk.kind === "shot_header") {
              return (
                <span key={i} className="font-semibold text-indigo-300">
                  {tk.text}
                </span>
              );
            }
            if (tk.kind === "mention") {
              const palette = assetColor(tk.assetKind);
              return (
                <span key={i} className={`rounded px-0.5 ${palette.textClass} ${palette.bgClass}`}>
                  {tk.text}
                </span>
              );
            }
            return <span key={i}>{tk.text}</span>;
          })}
          {/* Extra trailing char to force pre height to match textarea. */}
          {value.endsWith("\n") ? "\u200b" : null}
        </pre>

        <textarea
          ref={taRef}
          value={value}
          onChange={handleChange}
          onKeyUp={handleKeyUp}
          onClick={handleKeyUp}
          onScroll={onScroll}
          placeholder={t("reference_editor_placeholder")}
          spellCheck={false}
          className="absolute inset-0 h-full w-full resize-none bg-transparent p-3 font-mono text-sm leading-6 text-transparent caret-gray-200 placeholder:text-gray-600 focus:outline-none"
        />

        {pickerOpen && (
          <div className="absolute bottom-1 left-3 z-20">
            <MentionPicker
              open
              query={pickerQuery}
              candidates={candidates}
              onSelect={handlePickerSelect}
              onClose={() => {
                setPickerOpen(false);
                setPickerQuery("");
                atStartRef.current = null;
              }}
            />
          </div>
        )}
      </div>

      {unknownMentions.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {unknownMentions.map((name) => {
            const palette = ASSET_COLORS.unknown;
            // resolveMentionType is only used to assert name is truly unknown.
            // (Keeps this component pure w.r.t. project state.)
            const ok = resolveMentionType(project ?? null, name) !== undefined;
            if (ok) return null;
            return (
              <span
                key={name}
                className={`rounded border px-2 py-0.5 text-[11px] ${palette.textClass} ${palette.bgClass} ${palette.borderClass}`}
                role="status"
              >
                {t("reference_editor_unknown_mention", { name })}
              </span>
            );
          })}
        </div>
      )}
      {/* Suppress unused-var lint for projectName/episode — reserved for future
          inline preview / per-episode counters without forcing a prop churn. */}
      <span data-debug-project={projectName} data-debug-episode={episode} className="sr-only" />
    </div>
  );
}
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd frontend && pnpm vitest run src/components/canvas/reference/ReferenceVideoCard.test.tsx`
Expected: PASS — 5 tests

- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/canvas/reference/ReferenceVideoCard.tsx \
        frontend/src/components/canvas/reference/ReferenceVideoCard.test.tsx
git commit -m "feat(frontend): add ReferenceVideoCard prompt editor with highlight + @-picker"
```

---

### Task 8：`ReferencePanel`（references 拖拽换序）

**Files:**
- Create: `frontend/src/components/canvas/reference/ReferencePanel.tsx`
- Create: `frontend/src/components/canvas/reference/ReferencePanel.test.tsx`

- [ ] **Step 1: 写失败测试**

```tsx
// frontend/src/components/canvas/reference/ReferencePanel.test.tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ReferencePanel } from "./ReferencePanel";
import { useProjectsStore } from "@/stores/projects-store";
import type { ProjectData } from "@/types";
import type { ReferenceResource } from "@/types/reference-video";

const PROJECT: ProjectData = {
  title: "p",
  content_mode: "narration",
  style: "",
  episodes: [],
  characters: { 主角: { description: "" } },
  scenes: { 酒馆: { description: "" } },
  props: { 长剑: { description: "" } },
};

beforeEach(() => {
  useProjectsStore.setState({ currentProjectName: "proj", currentProjectData: PROJECT });
});

describe("ReferencePanel", () => {
  it("renders an empty state when there are no references", () => {
    render(
      <ReferencePanel
        references={[]}
        projectName="proj"
        onReorder={vi.fn()}
        onRemove={vi.fn()}
        onAdd={vi.fn()}
      />,
    );
    expect(screen.getByText(/No references yet|暂无引用/)).toBeInTheDocument();
  });

  it("renders a pill per reference with index marker [图N]", () => {
    const refs: ReferenceResource[] = [
      { type: "character", name: "主角" },
      { type: "scene", name: "酒馆" },
    ];
    render(
      <ReferencePanel
        references={refs}
        projectName="proj"
        onReorder={vi.fn()}
        onRemove={vi.fn()}
        onAdd={vi.fn()}
      />,
    );
    expect(screen.getByText(/\[图1\]/)).toBeInTheDocument();
    expect(screen.getByText(/\[图2\]/)).toBeInTheDocument();
    expect(screen.getByText(/主角/)).toBeInTheDocument();
    expect(screen.getByText(/酒馆/)).toBeInTheDocument();
  });

  it("calls onRemove when the ✕ button is clicked", () => {
    const onRemove = vi.fn();
    render(
      <ReferencePanel
        references={[{ type: "character", name: "主角" }]}
        projectName="proj"
        onReorder={vi.fn()}
        onRemove={onRemove}
        onAdd={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Remove reference|移除引用/ }));
    expect(onRemove).toHaveBeenCalledWith({ type: "character", name: "主角" });
  });

  it("calls onAdd when the + button is clicked", () => {
    const onAdd = vi.fn();
    render(
      <ReferencePanel
        references={[]}
        projectName="proj"
        onReorder={vi.fn()}
        onRemove={vi.fn()}
        onAdd={onAdd}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Add reference|添加引用/ }));
    expect(onAdd).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd frontend && pnpm vitest run src/components/canvas/reference/ReferencePanel.test.tsx`
Expected: FAIL — "Cannot find module './ReferencePanel'"

- [ ] **Step 3: 写实现**

```tsx
// frontend/src/components/canvas/reference/ReferencePanel.tsx
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { DndContext, closestCenter, useSensor, useSensors, PointerSensor, KeyboardSensor } from "@dnd-kit/core";
import type { DragEndEvent } from "@dnd-kit/core";
import {
  SortableContext,
  arrayMove,
  horizontalListSortingStrategy,
  sortableKeyboardCoordinates,
  useSortable,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { Plus, X } from "lucide-react";
import { assetColor } from "./asset-colors";
import { MentionPicker, type MentionCandidate } from "./MentionPicker";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import type { AssetKind, ReferenceResource } from "@/types/reference-video";

export interface ReferencePanelProps {
  references: ReferenceResource[];
  projectName: string;
  onReorder: (next: ReferenceResource[]) => void;
  onRemove: (ref: ReferenceResource) => void;
  onAdd: (ref: ReferenceResource) => void;
}

interface ItemProps {
  ref: ReferenceResource;
  index: number;
  projectName: string;
  onRemove: () => void;
}

function Pill({ ref: refItem, index, projectName, onRemove }: ItemProps) {
  const { t } = useTranslation("dashboard");
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: `${refItem.type}:${refItem.name}`,
  });
  const palette = assetColor(refItem.type);
  const project = useProjectsStore((s) => s.currentProjectData);
  let imagePath: string | null = null;
  if (refItem.type === "character") imagePath = project?.characters?.[refItem.name]?.character_sheet ?? null;
  else if (refItem.type === "scene") imagePath = project?.scenes?.[refItem.name]?.scene_sheet ?? null;
  else if (refItem.type === "prop") imagePath = project?.props?.[refItem.name]?.prop_sheet ?? null;
  const thumbUrl = imagePath ? API.getFileUrl(projectName, imagePath) : null;

  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={`flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs ${palette.textClass} ${palette.bgClass} ${palette.borderClass} ${isDragging ? "opacity-50" : ""}`}
    >
      <button
        type="button"
        {...attributes}
        {...listeners}
        aria-label={t("reference_panel_drag_aria", { name: refItem.name })}
        className="cursor-grab font-mono text-[10px] text-gray-500 hover:text-gray-300"
      >
        [图{index + 1}]
      </button>
      {thumbUrl && (
        <img src={thumbUrl} alt="" className="h-5 w-5 rounded object-cover" />
      )}
      <span className="truncate max-w-[120px]">@{refItem.name}</span>
      <button
        type="button"
        onClick={onRemove}
        aria-label={t("reference_panel_remove_aria", { name: refItem.name })}
        className="text-gray-500 hover:text-red-400"
      >
        <X className="h-3 w-3" />
      </button>
    </div>
  );
}

export function ReferencePanel({
  references,
  projectName,
  onReorder,
  onRemove,
  onAdd,
}: ReferencePanelProps) {
  const { t } = useTranslation("dashboard");
  const [pickerOpen, setPickerOpen] = useState(false);
  const project = useProjectsStore((s) => s.currentProjectData);
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const existingNames = new Set(references.map((r) => r.name));

  const candidates: Record<AssetKind, MentionCandidate[]> = {
    character: Object.entries(project?.characters ?? {})
      .filter(([name]) => !existingNames.has(name))
      .map(([name, data]) => ({
        name,
        imagePath: (data as { character_sheet?: string }).character_sheet ?? null,
      })),
    scene: Object.entries(project?.scenes ?? {})
      .filter(([name]) => !existingNames.has(name))
      .map(([name, data]) => ({
        name,
        imagePath: (data as { scene_sheet?: string }).scene_sheet ?? null,
      })),
    prop: Object.entries(project?.props ?? {})
      .filter(([name]) => !existingNames.has(name))
      .map(([name, data]) => ({
        name,
        imagePath: (data as { prop_sheet?: string }).prop_sheet ?? null,
      })),
  };

  const onDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const fromIndex = references.findIndex((r) => `${r.type}:${r.name}` === active.id);
    const toIndex = references.findIndex((r) => `${r.type}:${r.name}` === over.id);
    if (fromIndex < 0 || toIndex < 0) return;
    onReorder(arrayMove(references, fromIndex, toIndex));
  };

  return (
    <div className="relative border-t border-gray-800 bg-gray-950/40 p-2">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-[11px] uppercase tracking-wide text-gray-500">
          {t("reference_panel_title")}
        </span>
        <button
          type="button"
          onClick={() => setPickerOpen((v) => !v)}
          aria-label={t("reference_panel_add")}
          className="inline-flex items-center gap-1 rounded border border-gray-700 bg-gray-800 px-2 py-0.5 text-[11px] text-gray-300 hover:border-indigo-500 hover:text-indigo-300"
        >
          <Plus className="h-3 w-3" />
          {t("reference_panel_add")}
        </button>
      </div>
      {references.length === 0 ? (
        <p className="text-xs text-gray-500">{t("reference_panel_empty")}</p>
      ) : (
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
          <SortableContext
            items={references.map((r) => `${r.type}:${r.name}`)}
            strategy={horizontalListSortingStrategy}
          >
            <div className="flex flex-wrap gap-1.5">
              {references.map((r, i) => (
                <Pill
                  key={`${r.type}:${r.name}`}
                  ref={r}
                  index={i}
                  projectName={projectName}
                  onRemove={() => onRemove(r)}
                />
              ))}
            </div>
          </SortableContext>
        </DndContext>
      )}
      {pickerOpen && (
        <div className="absolute right-2 top-8 z-30">
          <MentionPicker
            open
            query=""
            candidates={candidates}
            onSelect={(ref) => {
              onAdd(ref);
              setPickerOpen(false);
            }}
            onClose={() => setPickerOpen(false)}
          />
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd frontend && pnpm vitest run src/components/canvas/reference/ReferencePanel.test.tsx`
Expected: PASS — 4 tests

- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/canvas/reference/ReferencePanel.tsx \
        frontend/src/components/canvas/reference/ReferencePanel.test.tsx
git commit -m "feat(frontend): add ReferencePanel with dnd-kit sortable + add/remove"
```

---

### Task 9：把 Card + Panel 接入 `ReferenceVideoCanvas`

**Files:**
- Modify: `frontend/src/components/canvas/reference/ReferenceVideoCanvas.tsx`
- Modify: `frontend/src/components/canvas/reference/ReferenceVideoCanvas.test.tsx`

- [ ] **Step 1: 修改现有 canvas 测试以反映新的中栏结构**

Open `frontend/src/components/canvas/reference/ReferenceVideoCanvas.test.tsx`. Find the test that asserts `<pre>` / shot text rendering in the middle column; replace it with:

```tsx
// frontend/src/components/canvas/reference/ReferenceVideoCanvas.test.tsx（追加/替换）
// 1. 确保文件顶部已有这些 import（如已存在则忽略）：
//    import { useProjectsStore } from "@/stores/projects-store";
//    import type { ProjectData } from "@/types";

// 2. 在现有 beforeEach 里增加 projects-store 种子：
//    useProjectsStore.setState({
//      currentProjectName: "proj",
//      currentProjectData: { ... characters/scenes/props 空对象 ... },
//    });

const STUB_PROJECT: ProjectData = {
  title: "p",
  content_mode: "narration",
  style: "",
  episodes: [],
  characters: {},
  scenes: {},
  props: {},
};

beforeEach(() => {
  useReferenceVideoStore.setState({ unitsByEpisode: {}, selectedUnitId: null, loading: false, error: null });
  useProjectsStore.setState({ currentProjectName: "proj", currentProjectData: STUB_PROJECT });
});

// 替换旧的 “renders selected unit text in middle column” 测试为：
it("renders the ReferenceVideoCard textarea when a unit is selected", async () => {
  vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({
    units: [mkUnit("E1U1")],
  });
  render(<ReferenceVideoCanvas projectName="proj" episode={1} />);
  await waitFor(() => expect(screen.getByText("E1U1")).toBeInTheDocument());
  fireEvent.click(screen.getByTestId("unit-row-E1U1"));
  const ta = await screen.findByRole("textbox");
  expect((ta as HTMLTextAreaElement).value).toContain("Shot 1 (3s): x");
});
```

（旧的 "selects a unit and shows it in preview panel" 保留，因为右栏 UnitPreviewPanel 行为未变；"adds a new unit via the store when the button is clicked" 保留。）

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd frontend && pnpm vitest run src/components/canvas/reference/ReferenceVideoCanvas.test.tsx`
Expected: FAIL — 仍走旧的 `<pre>` 路径，textarea 找不到

- [ ] **Step 3: 改 `ReferenceVideoCanvas.tsx`**

Open `frontend/src/components/canvas/reference/ReferenceVideoCanvas.tsx`. Replace the full file with:

```tsx
// frontend/src/components/canvas/reference/ReferenceVideoCanvas.tsx
import { useCallback, useEffect, useMemo } from "react";
import { useShallow } from "zustand/shallow";
import { useTranslation } from "react-i18next";
import { UnitList } from "./UnitList";
import { UnitPreviewPanel } from "./UnitPreviewPanel";
import { ReferenceVideoCard } from "./ReferenceVideoCard";
import { ReferencePanel } from "./ReferencePanel";
import { useReferenceVideoStore, referenceVideoCacheKey } from "@/stores/reference-video-store";
import { useTasksStore } from "@/stores/tasks-store";
import { useAppStore } from "@/stores/app-store";
import type { ReferenceResource, ReferenceVideoUnit } from "@/types";

export interface ReferenceVideoCanvasProps {
  projectName: string;
  episode: number;
  episodeTitle?: string;
}

const EMPTY_UNITS: readonly ReferenceVideoUnit[] = Object.freeze([]);

export function ReferenceVideoCanvas({ projectName, episode, episodeTitle }: ReferenceVideoCanvasProps) {
  const { t } = useTranslation("dashboard");

  const loadUnits = useReferenceVideoStore((s) => s.loadUnits);
  const addUnit = useReferenceVideoStore((s) => s.addUnit);
  const patchUnit = useReferenceVideoStore((s) => s.patchUnit);
  const generate = useReferenceVideoStore((s) => s.generate);
  const select = useReferenceVideoStore((s) => s.select);
  const updatePromptDebounced = useReferenceVideoStore((s) => s.updatePromptDebounced);

  const units =
    useReferenceVideoStore((s) => s.unitsByEpisode[referenceVideoCacheKey(projectName, episode)]) ??
    (EMPTY_UNITS as ReferenceVideoUnit[]);
  const selectedUnitId = useReferenceVideoStore((s) => s.selectedUnitId);
  const error = useReferenceVideoStore((s) => s.error);

  const relevantTasks = useTasksStore(
    useShallow((s) =>
      s.tasks.filter(
        (tk) => tk.project_name === projectName && tk.task_type === "reference_video",
      ),
    ),
  );

  useEffect(() => {
    void loadUnits(projectName, episode);
  }, [loadUnits, projectName, episode]);

  const selected = useMemo(
    () => units.find((u) => u.unit_id === selectedUnitId) ?? null,
    [units, selectedUnitId],
  );

  const generating = useMemo(() => {
    if (!selected) return false;
    return relevantTasks.some(
      (tk) =>
        tk.resource_id === selected.unit_id &&
        (tk.status === "queued" || tk.status === "running"),
    );
  }, [relevantTasks, selected]);

  const handleAdd = useCallback(async () => {
    try {
      await addUnit(projectName, episode, { prompt: "", references: [] });
    } catch (e) {
      useAppStore.getState().pushToast(e instanceof Error ? e.message : String(e), "error");
    }
  }, [addUnit, projectName, episode]);

  const handleGenerate = useCallback(
    async (unitId: string) => {
      try {
        await generate(projectName, episode, unitId);
      } catch (e) {
        useAppStore.getState().pushToast(e instanceof Error ? e.message : String(e), "error");
      }
    },
    [generate, projectName, episode],
  );

  const onAdd = useCallback(() => void handleAdd(), [handleAdd]);
  const onGenerateVoid = useCallback((id: string) => void handleGenerate(id), [handleGenerate]);

  const handlePromptChange = useCallback(
    (prompt: string, references: ReferenceResource[]) => {
      if (!selected) return;
      updatePromptDebounced(projectName, episode, selected.unit_id, prompt, references);
    },
    [updatePromptDebounced, projectName, episode, selected],
  );

  const handleReorderRefs = useCallback(
    (next: ReferenceResource[]) => {
      if (!selected) return;
      void patchUnit(projectName, episode, selected.unit_id, { references: next }).catch((e) => {
        useAppStore.getState().pushToast(e instanceof Error ? e.message : String(e), "error");
      });
    },
    [patchUnit, projectName, episode, selected],
  );

  const handleRemoveRef = useCallback(
    (ref: ReferenceResource) => {
      if (!selected) return;
      const next = selected.references.filter((r) => r.name !== ref.name);
      void patchUnit(projectName, episode, selected.unit_id, { references: next }).catch((e) => {
        useAppStore.getState().pushToast(e instanceof Error ? e.message : String(e), "error");
      });
    },
    [patchUnit, projectName, episode, selected],
  );

  const handleAddRef = useCallback(
    (ref: ReferenceResource) => {
      if (!selected) return;
      if (selected.references.some((r) => r.name === ref.name)) return;
      const next = [...selected.references, ref];
      void patchUnit(projectName, episode, selected.unit_id, { references: next }).catch((e) => {
        useAppStore.getState().pushToast(e instanceof Error ? e.message : String(e), "error");
      });
    },
    [patchUnit, projectName, episode, selected],
  );

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-gray-800 px-4 py-2">
        <h2 className="text-sm font-semibold text-gray-100">
          <span translate="no">E{episode}</span>
          {episodeTitle ? `: ${episodeTitle}` : ""} · {t("reference_units_count", { count: units.length })}
        </h2>
        {error && <p className="mt-1 text-xs text-red-400">{error}</p>}
      </div>
      <div className="grid min-h-0 flex-1 grid-cols-[minmax(260px,20%)_1fr_minmax(280px,24%)] overflow-hidden">
        <UnitList
          units={units}
          selectedId={selectedUnitId}
          onSelect={select}
          onAdd={onAdd}
        />
        <div className="flex h-full min-h-0 flex-col overflow-hidden border-r border-gray-800 bg-gray-950/30">
          {selected ? (
            <>
              <div className="flex min-h-0 flex-1 flex-col p-3">
                <ReferenceVideoCard
                  unit={selected}
                  projectName={projectName}
                  episode={episode}
                  onChangePrompt={handlePromptChange}
                />
              </div>
              <ReferencePanel
                references={selected.references}
                projectName={projectName}
                onReorder={handleReorderRefs}
                onRemove={handleRemoveRef}
                onAdd={handleAddRef}
              />
            </>
          ) : (
            <div className="flex flex-1 items-center justify-center text-xs text-gray-600">
              {t("reference_canvas_empty")}
            </div>
          )}
        </div>
        <UnitPreviewPanel
          unit={selected}
          projectName={projectName}
          onGenerate={onGenerateVoid}
          generating={generating}
        />
      </div>
    </div>
  );
}
```

- [ ] **Step 4: 运行测试，确认全绿**

Run: `cd frontend && pnpm vitest run src/components/canvas/reference/`
Expected: PASS — 所有 reference/* 组件测试（含新旧）

- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/canvas/reference/ReferenceVideoCanvas.tsx \
        frontend/src/components/canvas/reference/ReferenceVideoCanvas.test.tsx
git commit -m "feat(frontend): wire ReferenceVideoCard + ReferencePanel into canvas middle column"
```

---

### Task 10：全量 typecheck + 前端测试

**Files:**
- 无新增；只运行校验

- [ ] **Step 1: 前端 typecheck**

Run: `cd frontend && pnpm exec tsc --noEmit`
Expected: PASS（若失败：修复根因，不要绕过。常见问题：`useSortable` 泛型、新 store action 的类型声明不一致。）

- [ ] **Step 2: 前端 lint**

Run: `cd frontend && pnpm lint`
Expected: PASS

- [ ] **Step 3: 前端全量测试**

Run: `cd frontend && pnpm vitest run`
Expected: PASS — 覆盖率新模块 ≥ 90%

- [ ] **Step 4: i18n 一致性**

Run: `uv run pytest tests/test_i18n_consistency.py -v`
Expected: PASS（本 PR 未加后端 error key；若后续 PR7 补 warnings 字段再加一次校验）

- [ ] **Step 5: 后端测试（受影响模块 — 确保 PATCH 契约未破坏）**

Run: `uv run pytest tests/server/test_reference_videos_router.py tests/server/test_reference_video_tasks.py -v`
Expected: PASS（本 PR 未改后端，仅验证契约）

- [ ] **Step 6: ruff 全量 check（本 PR 实际无 Python 改动，但按 CLAUDE.md 要求运行）**

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: All checks passed

- [ ] **Step 7: 如果有 fixup，提交修复**

```bash
git add -p <具体路径>
git commit -m "chore: fix typecheck/lint drift for reference-video PR5"
```

（若无 drift，跳过本步）

---

### Task 11：手测走查（Dev server）

**Files:**
- 无代码改动，对照验收

- [ ] **Step 1: 启动后端 + 前端**

两个终端分别跑：
- `uv run python -m uvicorn server.app:app --reload --port 1241`
- `cd frontend && pnpm dev`

- [ ] **Step 2: 准备一个参考模式项目**

1. 新建项目，Step1 选 **参考生视频**；创建后进入项目。
2. 在项目级 assets 注册至少 1 个 character + 1 个 scene + 1 个 prop（可借由 `/assets` 页或直接改 project.json 注入 `character_sheet`/`scene_sheet`/`prop_sheet` 路径，测试图可用占位 jpg）。
3. 在 E1 剧集下通过 Canvas 左栏「新建 Unit」按钮创建一个空 unit。

- [ ] **Step 3: 验证编辑器**

1. 选中 E1U1，中栏出现空 textarea；输入 `Shot 1 (3s): 主角推门进入 @`
2. 输入 `@` 后立即弹出 MentionPicker，展示三分组，角色组下有刚注册的 character。
3. ↑/↓ 键切换 active 项；Enter 插入 `@名称 `，picker 关闭。
4. 继续输入 `@酒馆` 和 `@长剑` — 解析为 scene/prop 三色高亮。
5. 暂停输入 500ms，观察网络面板：PATCH `/units/E1U1`，body 含 `prompt` + 合并后的 `references: [{character, 主角}, {scene, 酒馆}, {prop, 长剑}]`。
6. 刷新页面，`units[0].shots` 与 `references` 与保存前一致。

- [ ] **Step 4: 验证 references 面板**

1. 拖拽 `[图1]` `[图2]` 交换顺序 → PATCH 请求只带 `references`；刷新后顺序保留。
2. 点击某个 ✕ 移除 reference — 列表缩短；对应 prompt 文本中的 `@名称` 保留，但会以 unknown-mention chip 显示警告。
3. 点击 `+` → MentionPicker 出现（仅列出未使用的资产）；选中后 reference 追加到末尾。

- [ ] **Step 5: 验证生成入口**

1. 点击右栏「生成视频」按钮 → POST `.../generate`，返回 task_id。
2. tasks-store 反映 `queued/running` 状态；状态点在左栏 UnitList 上联动。

- [ ] **Step 6: 验证回归**

1. 切换到 storyboard 模式集，`TimelineCanvas` 正常工作（PR5 未动 storyboard 路径）。
2. 切换项目：`ReferenceVideoCanvas` 不泄漏上一个项目的 unit（store 按 `projectName::episode` 分片）。

- [ ] **Step 7: 记录异常给 PR7**

把任何手测缺陷（比如 warnings chip 依赖后端回传、拖拽的 touch 支持）写入 PR7 plan 的 TODO 区。

---

## 全量验收门槛（roadmap 通用）

- 所有新增 test 通过，覆盖率 ≥ 90%（新模块）
- `uv run ruff check . && uv run ruff format --check .` 干净（本 PR 无 Python 改动，视为 noop）
- `pnpm check`（typecheck + lint + test）通过
- 对旧项目零回归：storyboard/grid 模式 Canvas 未改动
- i18n：zh/en 所有新 key 成对，前端 `pnpm exec tsc --noEmit` 通过
- PR 描述里列出本 PR 覆盖的 spec 章节：§4.3（prompt 约定）、§6.2（prompt 编辑器 + references）、§6.3（MentionPicker）
- 对 PATCH 契约的客户端不变量：`references` 由客户端完整维护，旧响应被新响应覆盖（fetchId 机制）

## 风险 & 缓解

| 风险 | 缓解 |
|---|---|
| textarea 与 pre 的字体/padding 未严格对齐 → 高亮漂移 | 使用相同 `font-mono text-sm leading-6 p-3` 类；Task 11 手测走查时放大字号检查 |
| debounce 竞态：上一个 unit 的 pending PATCH 在切换后才触发，把新 unit 的状态覆盖 | `_timers/_fetchIds` 按 `unitId` 分片；且响应写入时以 unit_id 精确匹配，不会错写 |
| MentionPicker 光标定位 | v1 不做 caret 精确定位；picker 相对 textarea 父容器底部显示，用户可接受。精确定位留 PR7 |
| `@dnd-kit` 首次引入，SSR/jsdom 兼容性未知 | vitest 用 jsdom；如测试里 dnd-kit 有 setup 问题，把 ReferencePanel 的拖拽部分改为"测试时短路 DndContext"（用 `import.meta.env.MODE === 'test'` 判断，仅渲染内部列表，不 mount DndContext） |
| 客户端 mergeReferences 与服务端期望不一致 | 服务端只接受客户端提交值；不做 prompt→refs 反推。以客户端为真 — Task 3 的 test 覆盖合并规则 |

## 未完成的留给 PR7

- 后端 PATCH 响应增加 `warnings[]` 字段（Veo 超限 / references 超限 / Sora 单图）
- warnings chip 在 Card 顶部的渲染
- MentionPicker 光标精确定位（textarea-caret 库）
- Touch / mobile 拖拽体验
- 过场动画 `transition_to_next` 的 UI 下拉

---

## Self-Review

- [x] **Spec coverage**：§4.3（prompt 约定）由 Task 2 + Task 3 覆盖；§6.2（prompt 编辑器 + references）由 Task 7 + Task 8 + Task 9 覆盖；§6.3（MentionPicker 三分组、键盘、过滤）由 Task 6 覆盖。
- [x] **Placeholder 扫描**：每个代码 step 都给出完整可运行代码块，无 TODO/fill-in/省略号。
- [x] **Type consistency**：`MentionKind` 仅在 asset-colors 中定义（含 `"unknown"`）；`AssetKind`（不含 unknown）来自 `@/types/reference-video`；MentionPicker 候选限定 `AssetKind` 三种；tokenizer 输出的 mention token 用 `MentionKind`（可为 unknown）。函数命名一致：`extractMentions` / `resolveMentionType` / `mergeReferences` 贯穿 Task 3 / Task 7 / Task 9。

## Execution Handoff

计划已保存到 `docs/superpowers/plans/2026-04-17-reference-to-video-pr5-frontend-editor.md`。

两种执行模式可选：

1. **Subagent-Driven（推荐）** — 每个 Task 分派一个新的 subagent，执行完回到主会话做两阶段 review，对独立任务快速迭代。
   - REQUIRED SUB-SKILL：`superpowers:subagent-driven-development`

2. **Inline Execution** — 在当前会话中按任务批量推进，带检查点。
   - REQUIRED SUB-SKILL：`superpowers:executing-plans`

请指示采用哪种模式。
