# Toast 与持久通知拆分 · 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `useAppStore` 中把瞬时 toast 与持久 drawer 通知解耦成 `pushToast` / `pushWorkspaceNotification` / `pushNotification` 三个入口，并按规则迁移全部现有调用点。

**Architecture:** `pushToast` 的副作用（强制写入 `workspaceNotifications`）是 bug 根因。收窄 `pushToast` 签名只写 `toast`；新增 `pushNotification` 作为组合便利函数（内部调 `pushToast + pushWorkspaceNotification`）；`pushWorkspaceNotification` 保持不动。调用点按「后台任务失败 → `pushNotification`；其余 → `pushToast`」规则迁移。

**Tech Stack:** React + zustand（`frontend/src/stores/app-store.ts`），vitest（单测）。

**Spec:** `docs/superpowers/specs/2026-04-23-toast-notification-split-design.md`

**Preflight check（全仓零 `pushToast(_, _, { target })` 调用点）**：此次签名收窄不会使任何现有调用点编译失败，每个调用点的迁移都是语义决策，不是类型修复。

---

## Task 1：Store API 重构 + 单测

**Files:**
- Modify: `frontend/src/stores/app-store.ts`
- Modify: `frontend/src/stores/stores.test.ts`

- [ ] **Step 1：改写 stores.test.ts 既有 `pushToast("hello")` 块 + 新增三断言**

位置：`frontend/src/stores/stores.test.ts:73-81`（"hello" 区段）。把现有 block：

```ts
app.pushToast("hello");
expect(useAppStore.getState().toast?.text).toBe("hello");
expect(useAppStore.getState().toast?.tone).toBe("info");
expect(useAppStore.getState().workspaceNotifications[0]).toEqual(
  expect.objectContaining({
    text: "hello",
    tone: "info",
  }),
);
app.clearToast();
expect(useAppStore.getState().toast).toBeNull();
```

替换为：

```ts
// pushToast 只写 toast，不再副作用写入 workspaceNotifications（issue #351 根因回归）
app.pushToast("hello");
expect(useAppStore.getState().toast?.text).toBe("hello");
expect(useAppStore.getState().toast?.tone).toBe("info");
expect(useAppStore.getState().workspaceNotifications).toHaveLength(0);
app.clearToast();
expect(useAppStore.getState().toast).toBeNull();

// pushNotification 同时写两者，tone 与 target 正确传递
app.pushNotification("task failed", "error", {
  target: { type: "segment", id: "S1", route: "/episodes/1" },
});
expect(useAppStore.getState().toast).toEqual(
  expect.objectContaining({ text: "task failed", tone: "error" }),
);
expect(useAppStore.getState().workspaceNotifications[0]).toEqual(
  expect.objectContaining({
    text: "task failed",
    tone: "error",
    target: expect.objectContaining({ id: "S1" }),
  }),
);
app.clearToast();
useAppStore.setState({ workspaceNotifications: [] });
```

保持下方（原 L85 起）`pushWorkspaceNotification` 测试段不变——这正好覆盖"pushWorkspaceNotification 只写 drawer、不写 toast"：它已经先 `clearToast()`，并在 block 内不再断言 toast 被写入。补一条显式断言以防回归：

```ts
// pushWorkspaceNotification 只写 drawer，不触动 toast
app.pushWorkspaceNotification({
  text: "AI 刚更新了角色「hero」，点击查看",
  target: {
    type: "character",
    id: "hero",
    route: "/characters",
  },
});
expect(useAppStore.getState().toast).toBeNull();  // 新增
const notification = useAppStore.getState().workspaceNotifications[0];
expect(notification.target?.id).toBe("hero");
```

- [ ] **Step 2：运行测试，应失败**

Run: `pnpm -C frontend exec vitest run src/stores/stores.test.ts`
Expected: FAIL。`pushNotification is not a function`；且 `pushToast("hello")` 后 `workspaceNotifications` 长度为 1（当前 bug 行为）导致新断言 `toHaveLength(0)` 失败。

- [ ] **Step 3：改写 `frontend/src/stores/app-store.ts`**

1. 删除 `ToastOptions` 接口（第 16-18 行）
2. 在文件顶部的 type imports 下方、`MAX_WORKSPACE_NOTIFICATIONS` 常量之前，插入规则注释：

```ts
/**
 * 通知系统分工规则（issue #351）：
 *
 * - pushToast(text, tone)
 *     用于：用户主动操作的即时反馈。
 *     典型：表单保存/校验、导入/删除/切换/上传成功、scroll target 未找到、
 *          后台任务提交成功回执（task_submitted）、轻量错误提示。
 *
 * - pushWorkspaceNotification({ text, tone, target })
 *     用于：后台异步产生的事件，用户可能不在当前页。
 *     典型：SSE 单条事件留痕（如 agent_update_scene）。
 *
 * - pushNotification(text, tone, options?)
 *     用于：用户需要后续回看的重要结果。
 *     典型：后台任务失败（剪映/ZIP 导出失败、项目 regenerate 失败、
 *          参考生视频失败、storyboard/video/character/scene/prop 生成失败）、
 *          SSE grouped_notification。
 *
 * 判断口诀：
 *   1. 用户现在不在场 → 需要持久
 *   2. 后台任务"失败"需要留痕排查 → 需要持久
 *   3. 其余 → 仅 toast
 *
 * pushToast 不接受 { persist: true } 之类逃生门，强制调用点三选一，意图显式。
 */
```

3. 修改 `AppState` 接口中 `pushToast` 与新增 `pushNotification`：

```ts
// Toast
toast: Toast | null;
pushToast: (text: string, tone?: Toast["tone"]) => void;
pushNotification: (
  text: string,
  tone?: Toast["tone"],
  options?: { target?: WorkspaceNotificationTarget | null },
) => void;
clearToast: () => void;
workspaceNotifications: WorkspaceNotification[];
pushWorkspaceNotification: (input: WorkspaceNotificationInput) => void;
// ... 其余不变
```

4. 修改 `pushToast` 实现（当前 L116-127），去掉对 `workspaceNotifications` 的副作用与 `options` 参数：

```ts
pushToast: (text, tone = "info") =>
  set({
    toast: { id: `${Date.now()}-${Math.random()}`, text, tone },
  }),
```

5. 新增 `pushNotification` 实现，紧跟 `pushToast` 之后、`clearToast` 之前：

```ts
pushNotification: (text, tone = "info", options) => {
  get().pushToast(text, tone);
  get().pushWorkspaceNotification({
    text,
    tone,
    target: options?.target ?? null,
  });
},
```

（`get` 已在 `create<AppState>((set, get) => ({...}))` 签名中。）

- [ ] **Step 4：运行测试，应通过**

Run: `pnpm -C frontend exec vitest run src/stores/stores.test.ts`
Expected: PASS。

- [ ] **Step 5：确认 typecheck 不挂**

Run: `pnpm -C frontend exec tsc --noEmit`
Expected: 无错误。由于全仓无 3-arg `pushToast` 调用，签名收窄不触发 fallout。

- [ ] **Step 6：Commit**

```bash
git add frontend/src/stores/app-store.ts frontend/src/stores/stores.test.ts
git commit -m "refactor(app-store): toast 与持久通知入口解耦 (#351)

pushToast 只写 toast，新增 pushNotification 组合便利，pushWorkspaceNotification 不变。
store 头部写入分工规则注释。"
```

---

## Task 2：迁移 GlobalHeader（剪映 / ZIP 导出失败）

**Files:**
- Modify: `frontend/src/components/layout/GlobalHeader.tsx`

- [ ] **Step 1：替换 L194 剪映导出失败**

原（L194）：
```tsx
useAppStore.getState().pushToast(t("dashboard:jianying_export_failed", { message: errMsg(err) }), "error");
```

改为：
```tsx
useAppStore.getState().pushNotification(t("dashboard:jianying_export_failed", { message: errMsg(err) }), "error");
```

- [ ] **Step 2：替换 L221-223 ZIP 导出失败**

原：
```tsx
useAppStore
  .getState()
  .pushToast(t("dashboard:export_failed", { message: errMsg(err) }), "error");
```

改为：
```tsx
useAppStore
  .getState()
  .pushNotification(t("dashboard:export_failed", { message: errMsg(err) }), "error");
```

L192、L213、L218 成功/警告三处保持 `pushToast` 不动。

- [ ] **Step 3：跑前端 check**

Run: `pnpm -C frontend check`
Expected: PASS（typecheck + test）。`GlobalHeader.test.tsx:113` 用的是 `pushWorkspaceNotification`，不受影响。

- [ ] **Step 4：Commit**

```bash
git add frontend/src/components/layout/GlobalHeader.tsx
git commit -m "refactor(global-header): 剪映/ZIP 导出失败改用 pushNotification (#351)"
```

---

## Task 3：迁移 OverviewCanvas（regenerate 失败）

**Files:**
- Modify: `frontend/src/components/canvas/OverviewCanvas.tsx`

- [ ] **Step 1：替换 L120-122 regenerate 失败**

原：
```tsx
useAppStore
  .getState()
  .pushToast(`${tRef.current("regenerate_failed")}${errMsg(err)}`, "error");
```

改为：
```tsx
useAppStore
  .getState()
  .pushNotification(`${tRef.current("regenerate_failed")}${errMsg(err)}`, "error");
```

L80 素材导入 toast 与 L118 regenerate 成功保持 `pushToast`。

- [ ] **Step 2：跑前端 check**

Run: `pnpm -C frontend check`
Expected: PASS。

- [ ] **Step 3：Commit**

```bash
git add frontend/src/components/canvas/OverviewCanvas.tsx
git commit -m "refactor(overview-canvas): regenerate 失败改用 pushNotification (#351)"
```

---

## Task 4：迁移 StudioCanvasRouter（所有 `_failed` 分支）

**Files:**
- Modify: `frontend/src/components/canvas/StudioCanvasRouter.tsx`

把以下 13 处 `pushToast(...,"error")` 整体替换为 `pushNotification(...,"error")`。全部保留其他参数与文本不变，**只换方法名**。

- [ ] **Step 1：逐行替换**

| 行 | key |
|---|---|
| L159 | `update_prompt_failed` |
| L176 | `generate_storyboard_failed` |
| L194 | `generate_video_failed` |
| L230 | `update_character_failed` |
| L246 | `submit_failed`（character） |
| L271 | `add_failed`（character） |
| L283 | `update_scene_failed` |
| L293 | `submit_failed`（scene） |
| L304 | `add_failed`（scene） |
| L316 | `update_prop_failed` |
| L326 | `submit_failed`（prop） |
| L337 | `add_failed`（prop） |
| L348 | `grid_generation_failed` |

示例（L159）：
```tsx
// before
useAppStore.getState().pushToast(tRef.current("update_prompt_failed", { message: errMsg(err) }), "error");
// after
useAppStore.getState().pushNotification(tRef.current("update_prompt_failed", { message: errMsg(err) }), "error");
```

**保持不动的**：task_submitted（L174/192/244/291/324/335）、character/scene/prop `_added` 与 `_updated`（L228/269/302）、grid 成功（L346）——全部继续用 `pushToast`。

- [ ] **Step 2：核对替换数量**

Run: `grep -n "pushNotification" frontend/src/components/canvas/StudioCanvasRouter.tsx | wc -l`
Expected: `13`。

Run: `grep -cn 'pushToast(.*, *"error")' frontend/src/components/canvas/StudioCanvasRouter.tsx`
Expected: `0`。

- [ ] **Step 3：跑前端 check**

Run: `pnpm -C frontend check`
Expected: PASS。

- [ ] **Step 4：Commit**

```bash
git add frontend/src/components/canvas/StudioCanvasRouter.tsx
git commit -m "refactor(studio-canvas): 生成失败路径改用 pushNotification (#351)"
```

---

## Task 5：迁移 ReferenceVideoCanvas（后台任务失败 polling）

**Files:**
- Modify: `frontend/src/components/canvas/reference/ReferenceVideoCanvas.tsx`

- [ ] **Step 1：替换 L134 task 轮询检测到失败的 toast**

原（L128-141 片段，焦点是 L134）：
```tsx
if (tk.status === "failed" && before !== undefined && before !== "failed") {
  useAppStore.getState().pushToast(
    t("reference_generation_task_failed", {
      unitId: tk.resource_id,
      reason: tk.error_message ?? t("reference_status_failed"),
    }),
    "error",
  );
}
```

改为（仅方法名改为 `pushNotification`）：
```tsx
if (tk.status === "failed" && before !== undefined && before !== "failed") {
  useAppStore.getState().pushNotification(
    t("reference_generation_task_failed", {
      unitId: tk.resource_id,
      reason: tk.error_message ?? t("reference_status_failed"),
    }),
    "error",
  );
}
```

- [ ] **Step 2：确认 L56 `toastError` 工具与 L186 queued/deduped 保持不动**

`toastError`（L52-57）服务于 `handleAdd` / `handleGenerate` 的 POST 即时失败，属于用户操作即时反馈 → 继续用 `pushToast`。
L186 `reference_generate_queued` / `reference_generate_deduped` 是 POST 成功反馈 → 继续用 `pushToast`。

核对：`grep -n "pushToast\|pushNotification" frontend/src/components/canvas/reference/ReferenceVideoCanvas.tsx`
Expected：两处 `pushToast`（L56 `toastError` + L186 queued/deduped）+ 一处 `pushNotification`（L134）。

- [ ] **Step 3：跑前端 check**

Run: `pnpm -C frontend check`
Expected: PASS。

- [ ] **Step 4：Commit**

```bash
git add frontend/src/components/canvas/reference/ReferenceVideoCanvas.tsx
git commit -m "refactor(reference-video): 任务轮询检测到失败改用 pushNotification (#351)"
```

---

## Task 6：迁移 useProjectEventsSSE（同步失败 + grouped_notification）

**Files:**
- Modify: `frontend/src/hooks/useProjectEventsSSE.ts`

注意：L250 的 `grouped_notification` 在当前代码里依赖的是 `pushToast` 的 bug 副作用写入 drawer。Store API 收敛后它只会出 toast 不再入 drawer——必须迁移到 `pushNotification` 才能保持既有的 toast + 持久行为。

- [ ] **Step 1：替换 L174 同步失败**

原：
```ts
pushToast(`同步项目变更失败: ${errMsg(err)}`, "warning");
```

改为：
```ts
pushNotification(`同步项目变更失败: ${errMsg(err)}`, "warning");
```

- [ ] **Step 2：替换 L250 grouped_notification**

原（L245-252 片段）：
```ts
if (payload.source !== "webui") {
  for (const group of groupedChanges) {
    if (!hasImportantChanges(group)) {
      continue;
    }
    pushToast(formatGroupedNotificationText(group), "success");
  }
}
```

改为：
```ts
if (payload.source !== "webui") {
  for (const group of groupedChanges) {
    if (!hasImportantChanges(group)) {
      continue;
    }
    pushNotification(formatGroupedNotificationText(group), "success");
  }
}
```

- [ ] **Step 3：导入与 selector 更新**

在 hook 顶部现有的 `const pushToast = useAppStore((s) => s.pushToast);` 附近（约 L122-123）新增：

```ts
const pushNotification = useAppStore((s) => s.pushNotification);
```

并把 `pushNotification` 加入 useEffect 的依赖数组（约 L335-342）。检查：
- `pushToast` 若在 hook 内不再被使用，同时删除该 selector 与依赖；
- 若仍在别处使用（例如 SSE 连接失败也用 pushToast？），grep 核对后保留。

Run: `grep -n "pushToast\|pushNotification" frontend/src/hooks/useProjectEventsSSE.ts`
按实际使用裁剪 selector 和依赖数组，避免 eslint-react-hooks 抱怨。

- [ ] **Step 4：跑前端 check**

Run: `pnpm -C frontend check`
Expected: PASS。

- [ ] **Step 5：Commit**

```bash
git add frontend/src/hooks/useProjectEventsSSE.ts
git commit -m "refactor(project-events): grouped_notification 与同步失败改用 pushNotification (#351)

前者过去依赖 pushToast 的 bug 副作用写入 drawer，store 解耦后需显式 pushNotification 才能保持持久化。"
```

---

## Task 7：最终验证

**Files:** 无修改（仅验证）。

- [ ] **Step 1：grep 确认 pushNotification 使用位点**

Run:
```bash
grep -rn "pushNotification" frontend/src --include="*.ts" --include="*.tsx" | grep -v test
```
Expected 行数 ≈ 19（1 store 定义 + 1 GlobalHeader + 1 OverviewCanvas + 13 StudioCanvasRouter + 1 ReferenceVideoCanvas + 2 useProjectEventsSSE）。允许偏差 ±2。

- [ ] **Step 2：grep 确认没有 pushToast(..., { target })**

Run:
```bash
grep -rnE "pushToast\([^)]*,[^)]*,\s*\{\s*target" frontend/src --include="*.ts" --include="*.tsx"
```
Expected: 无输出。

- [ ] **Step 3：全套 check + build**

```bash
pnpm -C frontend check
pnpm -C frontend build
```
Expected: PASS / 构建成功。

- [ ] **Step 4：手工验证清单**

启动 dev（后端 + 前端），按顺序过：
1. 编辑剧本保存（PreprocessingView）→ toast 出现，drawer **不新增** ✓
2. 切换剧集模式（EpisodeModeSwitcher）→ toast，drawer 不新增 ✓
3. 触发剪映导出失败（断网或改坏 API key）→ toast + drawer 均留痕 ✓
4. 触发项目 regenerate 失败 → toast + drawer 均留痕 ✓
5. 后台 SSE grouped_notification（等 agent 改动落盘）→ toast + drawer 均留痕 ✓

如任一 broken，回到对应 Task 复查。

- [ ] **Step 5：若 Step 4 全部通过，无需再 commit（本任务无文件改动）**

至此 issue #351 DoD 三项全部达成。

---

## Self-Review 备忘

本计划对齐 spec 的三项验收：
1. **API 解耦** — Task 1 完成。
2. **分工规则** — Task 1 注释 + spec 固化。
3. **调用点迁移** — Task 2-6 覆盖 spec §迁移映射的所有具名位点；其余调用点保留 `pushToast` 语义不动（Task 7 Step 2 兜底）。

**回归防护**：Task 1 的三条断言直接覆盖 issue 根因（`pushToast` 不再写 drawer）+ 两条组合函数语义。
