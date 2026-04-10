# Episodes Review-First Workspace Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement a review-first episodes/scenes experience where users manually click a scene video thumbnail to load a large in-page player with storyboard companion, while preserving existing edit/generate actions.

**Architecture:** Keep all backend APIs unchanged and implement the feature entirely in `workspace-page.js` with local React state and pure helper functions. Introduce a top review panel and compact 5-column scene grid, then route click-to-review interactions through deterministic utility functions so state invalidation and error handling are testable. Use server-render and pure-function tests under `node:test` to keep feedback fast and YAGNI.

**Tech Stack:** React 18 + htm templates, Tailwind utility classes, existing primitives (`Card`, `Button`, `Badge`, `EmptyState`), Node built-in test runner (`node:test`) with `react-dom/server`.

---

### Task 1: Preflight in isolated worktree

**Files:**
- Create: none
- Modify: none
- Test: none

**Step 1: Create isolated worktree/branch (@superpowers:using-git-worktrees)**

```bash
git worktree add ../ArcReel-review-first -b codex/episodes-review-first
```

**Step 2: Install/confirm frontend dependencies in worktree**

```bash
npm install
```

Run in: `../ArcReel-review-first/frontend`

**Step 3: Run current frontend baseline tests**

```bash
node --test frontend/tests/landing-page.test.mjs
node --test frontend/tests/assistant-question-wizard.test.mjs
node --test frontend/tests/assistant-message-area-wizard.test.mjs
node --test frontend/tests/app-shell-floating-button.test.mjs
```

Expected: PASS before feature changes.

**Step 4: Commit preflight notes (optional if no file changes)**

```bash
git status --short
```

Expected: clean tree or only intentional bootstrap changes.

### Task 2: Add failing tests for review-selection utilities (TDD)

**Files:**
- Create: `frontend/tests/workspace-review-selection.test.mjs`
- Modify: none
- Test: `frontend/tests/workspace-review-selection.test.mjs`

**Step 1: Write the failing test**

```javascript
import test from "node:test";
import assert from "node:assert/strict";

import {
    buildReviewTargetFromSelection,
    getSafeReviewSelection,
} from "../src/react/pages/workspace-page.js";

const scripts = {
    "episode_1.json": {
        content_mode: "drama",
        scenes: [
            {
                scene_id: "E1S01",
                duration_seconds: 6,
                generated_assets: {
                    storyboard_image: "storyboards/scene_E1S01.png",
                    video_clip: "videos/scene_E1S01.mp4",
                    status: "completed",
                },
            },
            {
                scene_id: "E1S02",
                duration_seconds: 6,
                generated_assets: {
                    storyboard_image: "storyboards/scene_E1S02.png",
                },
            },
        ],
    },
};

test("buildReviewTargetFromSelection should return media paths for valid selection", () => {
    const selectedReview = { scriptFile: "episode_1.json", itemId: "E1S01" };
    const target = buildReviewTargetFromSelection(scripts, selectedReview, {});

    assert.equal(target.itemId, "E1S01");
    assert.equal(target.videoPath, "videos/scene_E1S01.mp4");
    assert.equal(target.storyboardPath, "storyboards/scene_E1S01.png");
});

test("buildReviewTargetFromSelection should return null for missing item", () => {
    const selectedReview = { scriptFile: "episode_1.json", itemId: "E1S99" };
    const target = buildReviewTargetFromSelection(scripts, selectedReview, {});
    assert.equal(target, null);
});

test("getSafeReviewSelection should clear invalid or non-playable selection", () => {
    assert.equal(
        getSafeReviewSelection(scripts, { scriptFile: "episode_1.json", itemId: "E1S02" }, {}),
        null
    );
});
```

**Step 2: Run test to verify it fails**

Run: `node --test frontend/tests/workspace-review-selection.test.mjs`  
Expected: FAIL because helper exports do not exist yet.

### Task 3: Implement minimal review-selection utilities

**Files:**
- Modify: `frontend/src/react/pages/workspace-page.js:58-205`
- Test: `frontend/tests/workspace-review-selection.test.mjs`

**Step 1: Write minimal implementation**

```javascript
export function buildReviewTargetFromSelection(currentScripts, selectedReview, uploadedStoryboardMap = {}) {
    if (!selectedReview?.scriptFile || !selectedReview?.itemId) {
        return null;
    }

    const script = currentScripts?.[selectedReview.scriptFile];
    if (!script) {
        return null;
    }

    const isNarration = script.content_mode === "narration" && Array.isArray(script.segments);
    const items = isNarration ? script.segments || [] : script.scenes || [];
    const idField = isNarration ? "segment_id" : "scene_id";
    const item = items.find((entry) => entry[idField] === selectedReview.itemId);
    if (!item) {
        return null;
    }

    const assets = item.generated_assets || {};
    const key = itemKey(selectedReview.scriptFile, selectedReview.itemId);

    return {
        scriptFile: selectedReview.scriptFile,
        itemId: selectedReview.itemId,
        item,
        videoPath: assets.video_clip || "",
        storyboardPath: assets.storyboard_image || uploadedStoryboardMap[key] || "",
        status: assets.status || "pending",
        duration: item.duration_seconds || 4,
    };
}

export function getSafeReviewSelection(currentScripts, selectedReview, uploadedStoryboardMap = {}) {
    const target = buildReviewTargetFromSelection(currentScripts, selectedReview, uploadedStoryboardMap);
    if (!target?.videoPath) {
        return null;
    }
    return selectedReview;
}
```

**Step 2: Run test to verify it passes**

Run: `node --test frontend/tests/workspace-review-selection.test.mjs`  
Expected: PASS.

**Step 3: Commit**

```bash
git add frontend/src/react/pages/workspace-page.js frontend/tests/workspace-review-selection.test.mjs
git commit -m "feat(workspace): add review selection helpers"
```

### Task 4: Add failing render tests for review panel and 5-column scene grid

**Files:**
- Create: `frontend/tests/workspace-review-panel.test.mjs`
- Modify: none
- Test: `frontend/tests/workspace-review-panel.test.mjs`

**Step 1: Write the failing test**

```javascript
import test from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { ProjectEpisodes } from "../src/react/pages/workspace-page.js";

globalThis.window = {
    API: {
        getFileUrl: (_projectName, path) => `/files/${path}`,
    },
};

function renderEpisodes(extraProps = {}) {
    const baseProps = {
        currentProjectData: {
            episodes: [{ episode: 1, title: "Episode 1", script_file: "scripts/episode_1.json" }],
        },
        currentProjectName: "demo",
        currentScripts: {
            "episode_1.json": {
                content_mode: "drama",
                scenes: [
                    {
                        scene_id: "E1S01",
                        duration_seconds: 6,
                        generated_assets: {
                            storyboard_image: "storyboards/scene_E1S01.png",
                            video_clip: "videos/scene_E1S01.mp4",
                            status: "completed",
                        },
                    },
                ],
            },
        },
        draftsByEpisode: {},
        itemDrafts: {},
        uploadedStoryboardMap: {},
        selectedReview: null,
        reviewMediaError: "",
        onSelectReview: () => {},
        onReviewMediaError: () => {},
        onItemDraftChange: () => {},
        onOpenDraftEditor: () => {},
        onSaveItem: () => {},
        onGenerateStoryboard: () => {},
        onGenerateVideo: () => {},
        onUploadStoryboard: () => {},
        onOpenPreview: () => {},
        busy: false,
    };

    return renderToStaticMarkup(React.createElement(ProjectEpisodes, { ...baseProps, ...extraProps }));
}

test("ProjectEpisodes should render review empty state when no selection", () => {
    const html = renderEpisodes();
    assert.ok(html.includes("Click any scene's video thumbnail to start reviewing"));
});

test("ProjectEpisodes should use compact 5-column scene grid", () => {
    const html = renderEpisodes();
    assert.ok(html.includes("xl:grid-cols-5"));
});
```

**Step 2: Run test to verify it fails**

Run: `node --test frontend/tests/workspace-review-panel.test.mjs`  
Expected: FAIL because `ProjectEpisodes` is not exported and review panel/grid classes are absent.

### Task 5: Implement review-first UI and wiring

**Files:**
- Modify: `frontend/src/react/pages/workspace-page.js:773-1114`
- Test: `frontend/tests/workspace-review-panel.test.mjs`

**Step 1: Export `ProjectEpisodes` and add review panel component**

```javascript
function EpisodeReviewPanel({
    reviewTarget,
    reviewMediaError,
    currentProjectName,
    onReviewMediaError,
    onGenerateVideo,
}) {
    if (!reviewTarget) {
        return html`
            <article className="rounded-2xl border border-white/10 bg-white/5 p-4">
                <p className="text-sm text-slate-300">Click any scene's video thumbnail to start reviewing</p>
            </article>
        `;
    }

    const videoUrl = resolveFileUrl(currentProjectName, reviewTarget.videoPath);
    const storyboardUrl = resolveFileUrl(currentProjectName, reviewTarget.storyboardPath);

    return html`
        <article className="rounded-2xl border border-neon-400/20 bg-white/5 p-3">
            <div className="grid xl:grid-cols-[2fr_1fr] gap-3">
                <div className="rounded-xl border border-white/10 bg-black/50 p-2">
                    <video
                        src=${videoUrl}
                        controls
                        className="w-full aspect-video rounded-lg bg-black object-contain"
                        onError=${() => onReviewMediaError("Video failed to load; you can retry generation")}
                    ></video>
                </div>
                <div className="space-y-2">
                    <div className="rounded-xl border border-white/10 bg-ink-900/60 p-2">
                        ${storyboardUrl
                            ? html`<img src=${storyboardUrl} alt=${`${reviewTarget.itemId} storyboard`} className="w-full aspect-video rounded-lg object-cover" />`
                            : html`<div className="w-full aspect-video rounded-lg bg-ink-950/70 flex items-center justify-center text-xs text-slate-500">No storyboard</div>`}
                    </div>
                    <p className="text-xs text-slate-300">${reviewTarget.itemId} · ${reviewTarget.duration}s · ${reviewTarget.status}</p>
                    ${reviewMediaError
                        ? html`<div className="text-xs text-red-300">${reviewMediaError}</div>`
                        : null}
                </div>
            </div>
        </article>
    `;
}
```

**Step 2: Add state in `WorkspacePage` and pass props into `ProjectEpisodes`**

```javascript
const [selectedReview, setSelectedReview] = useState(null);
const [reviewMediaError, setReviewMediaError] = useState("");

useEffect(() => {
    setSelectedReview(null);
    setReviewMediaError("");
}, [currentProjectName]);

useEffect(() => {
    setSelectedReview((prev) => getSafeReviewSelection(currentScripts, prev, uploadedStoryboardMap));
}, [currentScripts, uploadedStoryboardMap]);
```

And pass into tab content:

```javascript
<ProjectEpisodes
    ...
    selectedReview={selectedReview}
    reviewMediaError={reviewMediaError}
    onSelectReview={handlers.onSelectReview}
    onReviewMediaError={handlers.onReviewMediaError}
/>
```

**Step 3: Convert scene list to compact 5-column grid and thumbnail click selection**

```javascript
<div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-2">
    {items.map((item) => {
        const isSelected = selectedReview?.scriptFile === scriptFile && selectedReview?.itemId === itemId;
        return html`
            <article className=${cn("rounded-xl border p-2 bg-ink-900/50", isSelected ? "border-neon-400/60 ring-1 ring-neon-400/50" : "border-white/10")}>
                <button
                    type="button"
                    onClick=${() => onSelectReview(scriptFile, itemId)}
                    className="w-full rounded-lg border border-white/10 overflow-hidden bg-ink-950/60"
                >
                    ${videoUrl
                        ? html`<img src=${storyboardUrl || videoPosterFallback} alt=${`${itemId} preview`} className="w-full aspect-video object-cover" />`
                        : html`<div className="w-full aspect-video flex items-center justify-center text-xs text-slate-500">No video</div>`}
                </button>
                <div className="mt-2 flex items-center justify-between text-[11px] text-slate-300">
                    <span>${itemId}</span>
                    <span>${draft.duration || item.duration_seconds || 4}s</span>
                </div>
            </article>
        `;
    })}
</div>
```

**Step 4: Run tests**

Run: `node --test frontend/tests/workspace-review-panel.test.mjs`  
Expected: PASS.

**Step 5: Commit**

```bash
git add frontend/src/react/pages/workspace-page.js frontend/tests/workspace-review-panel.test.mjs
git commit -m "feat(workspace): add review-first panel and compact scene grid"
```

### Task 6: Add failing tests for selection invalidation and missing-video guard

**Files:**
- Modify: `frontend/tests/workspace-review-selection.test.mjs`
- Modify: `frontend/src/react/pages/workspace-page.js`
- Test: `frontend/tests/workspace-review-selection.test.mjs`

**Step 1: Extend failing tests**

```javascript
import { getReviewSelectionResult } from "../src/react/pages/workspace-page.js";

test("getReviewSelectionResult should reject items without video", () => {
    const result = getReviewSelectionResult(scripts, { scriptFile: "episode_1.json", itemId: "E1S02" }, {});
    assert.equal(result.ok, false);
    assert.equal(result.error, "This scene has no playable video yet");
});

test("getSafeReviewSelection should clear selection when script data is removed", () => {
    const selected = { scriptFile: "episode_1.json", itemId: "E1S01" };
    assert.equal(getSafeReviewSelection({}, selected, {}), null);
});
```

**Step 2: Run test to verify it fails**

Run: `node --test frontend/tests/workspace-review-selection.test.mjs`  
Expected: FAIL because `getReviewSelectionResult` is not implemented.

### Task 7: Implement missing-video guard + error messaging flow

**Files:**
- Modify: `frontend/src/react/pages/workspace-page.js:1116-2065`
- Test: `frontend/tests/workspace-review-selection.test.mjs`

**Step 1: Add deterministic selection result helper**

```javascript
export function getReviewSelectionResult(currentScripts, selectedReview, uploadedStoryboardMap = {}) {
    const target = buildReviewTargetFromSelection(currentScripts, selectedReview, uploadedStoryboardMap);
    if (!target) {
        return { ok: false, error: "Cannot find the corresponding segment/scene", target: null };
    }
    if (!target.videoPath) {
        return { ok: false, error: "This scene has no playable video yet", target };
    }
    return { ok: true, error: "", target };
}
```

**Step 2: Use helper in `onSelectReview` handler**

```javascript
const onSelectReview = useCallback((scriptFile, itemId) => {
    const result = getReviewSelectionResult(currentScripts, { scriptFile, itemId }, uploadedStoryboardMap);
    if (!result.ok) {
        notify(result.error, "error");
        return;
    }
    setReviewMediaError("");
    setSelectedReview({ scriptFile, itemId });
}, [currentScripts, uploadedStoryboardMap, notify]);
```

**Step 3: Run test to verify it passes**

Run: `node --test frontend/tests/workspace-review-selection.test.mjs`  
Expected: PASS.

**Step 4: Commit**

```bash
git add frontend/src/react/pages/workspace-page.js frontend/tests/workspace-review-selection.test.mjs
git commit -m "fix(workspace): guard review selection when video is missing"
```

### Task 8: Full verification and regression sweep (@superpowers:verification-before-completion)

**Files:**
- Modify: none
- Test:
  - `frontend/tests/workspace-review-selection.test.mjs`
  - `frontend/tests/workspace-review-panel.test.mjs`
  - `frontend/tests/assistant-question-wizard.test.mjs`
  - `frontend/tests/assistant-message-area-wizard.test.mjs`
  - `frontend/tests/landing-page.test.mjs`
  - `frontend/tests/app-shell-floating-button.test.mjs`

**Step 1: Run targeted new tests**

```bash
node --test frontend/tests/workspace-review-selection.test.mjs
node --test frontend/tests/workspace-review-panel.test.mjs
```

Expected: PASS.

**Step 2: Run existing frontend regression tests**

```bash
node --test frontend/tests/assistant-question-wizard.test.mjs
node --test frontend/tests/assistant-message-area-wizard.test.mjs
node --test frontend/tests/landing-page.test.mjs
node --test frontend/tests/app-shell-floating-button.test.mjs
```

Expected: PASS.

**Step 3: Run production build**

```bash
npm run build
```

Run in: `frontend`  
Expected: build succeeds without new warnings.

**Step 4: Final commit**

```bash
git add frontend/src/react/pages/workspace-page.js frontend/tests/workspace-review-selection.test.mjs frontend/tests/workspace-review-panel.test.mjs
git commit -m "feat(workspace): deliver review-first episodes scene workflow"
```

