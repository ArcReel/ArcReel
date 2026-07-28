# a11y PR 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 issue #292 的四类 a11y 修复：focus-ring 统一、textarea label 关联、抽 `<ProgressBar>`、file input `aria-label`。

**Architecture:** 在 Tailwind v4 的 `@utility` 指令里定义 `focus-ring` token，全局替换等价的手写串；抽出可复用的 `<ProgressBar>` 组件统一 progressbar 的 ARIA 语义；为 textarea 和 file input 补齐 label/aria-label 关联。

**Tech Stack:** React 19, TypeScript, Tailwind CSS 4, Vitest 4 + @testing-library/react, i18next (zh/en)。

**Spec:** `docs/superpowers/specs/2026-04-13-a11y-focus-ring-pr2-design.md`

## 前置背景（Executor 必读）

代码库里**已存在 `.focus-ring` CSS class**（定义在 `frontend/src/css/studio.css:10-12`，规格 `ring-indigo-500/40`），并已被 11 处调用（AssetSidebar、PreprocessingView、TimelineCanvas）。本 PR 把载体从 CSS class 迁移到 Tailwind v4 `@utility`（规格 `ring-indigo-500` 不透明）——调用点 class 名保持 `focus-ring` 不变，但焦点态会从 40% 透明变为实色（**附带改善**，无需调整调用点）。

**Tailwind v4 `@utility` 语法**：在 `index.css` 用 `@utility <name> { @apply ... }`，之后可在 className 里直接写 `<name>` 作为 Tailwind utility（与 `.class` CSS 等价，但参与 Tailwind 的变体链和 purge）。

---

## 文件结构

### Create（新建）

- `frontend/src/components/ui/ProgressBar.tsx` — 可复用进度条组件（`role=progressbar` + ARIA 值 + 可选 label）
- `frontend/src/components/ui/ProgressBar.test.tsx` — 组件单测

### Modify（修改）

- `frontend/src/index.css` — 新增 `@utility focus-ring` 定义
- `frontend/src/css/studio.css` — 删除旧 `.focus-ring` CSS 规则（line 9-12）
- `frontend/src/components/ui/AutoTextarea.tsx` — 新增 `id?: string` prop 透传
- `frontend/src/components/canvas/OverviewCanvas.tsx` — 删 `focusRing` JS 常量；替换 focus 串；progressbar 用新组件；textarea 补 label；file input 补 aria-label
- `frontend/src/components/pages/CredentialList.tsx` — 删 `focusRing` JS 常量；替换 focus 串；file input 补 aria-label
- `frontend/src/components/pages/ProjectsPage.tsx` — progressbar 用新组件；file input 补 aria-label
- `frontend/src/components/copilot/TodoListPanel.tsx` — progressbar 用新组件
- `frontend/src/components/copilot/AgentCopilot.tsx` — textarea 补 label；file input 补 aria-label；focus 串替换
- `frontend/src/components/layout/AssetSidebar.tsx` — file input 补 aria-label
- `frontend/src/components/pages/CreateProjectModal.tsx` — file input 补 aria-label
- `frontend/src/components/canvas/WelcomeCanvas.tsx` — 2 处 file input 补 aria-label
- `frontend/src/components/canvas/lorebook/CharacterCard.tsx` — textarea 补 label；file input 补 aria-label
- `frontend/src/components/canvas/lorebook/AddCharacterForm.tsx` — textarea 补 label；file input 补 aria-label
- 其他含 `focus-visible:ring-indigo-500` / `focus:outline-none` 的文件：按 grep 清单批量替换（见 Task 4/5）
- `frontend/src/components/canvas/SourceFileViewer.tsx` / `ClueCard.tsx` / `AddClueForm.tsx` / `timeline/PreprocessingView.tsx` / `timeline/SegmentCard.tsx` — textarea 补 label
- `frontend/src/i18n/zh/dashboard.ts` / `frontend/src/i18n/en/dashboard.ts` — 新增 file input `aria-label` 对应 key（按需）
- `frontend/package.json` — 修正 `--max-warnings` 数字（若下降）

---

## Task 1: 迁移 focus-ring token 载体

**Files:**
- Modify: `frontend/src/index.css`
- Modify: `frontend/src/css/studio.css:9-12`

### Rationale
老 `.focus-ring`（`studio.css`）用 `ring-indigo-500/40`（40% 透明）；新 `@utility focus-ring` 用 `ring-indigo-500`（实色），焦点更明显。迁移后调用点 class 名不变（`focus-ring`），这 11 处天然获得视觉改善。

- [ ] **Step 1.1: 在 `index.css` 末尾添加 `@utility focus-ring`**

在 `frontend/src/index.css` 末尾（`@theme` 块之后）新增：

```css

/* 统一焦点指示（a11y）—— 所有可聚焦元素共用；比旧 .focus-ring（studio.css）的 ring-indigo-500/40 更明显 */
@utility focus-ring {
  @apply focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500;
}
```

- [ ] **Step 1.2: 从 `studio.css` 删除旧 `.focus-ring` 规则**

删除 `frontend/src/css/studio.css` 的第 9-12 行：

```css
/* 统一焦点指示 — 键盘导航可见性 */
.focus-ring {
  @apply focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/40;
}
```

- [ ] **Step 1.3: 启动 dev 手测一个使用点**

Run: `cd frontend && pnpm dev`

在浏览器打开 `http://127.0.0.1:5173`，Tab 键聚焦到任何 `focus-ring` class 调用点（如左侧 AssetSidebar 的按钮），确认：
- Expected: 出现不透明的 indigo-500 焦点环（比原先 /40 透明版明显）
- 无 console error/warning

停止 dev 进程。

- [ ] **Step 1.4: 运行构建验证**

Run: `cd frontend && pnpm build`
Expected: 构建成功，无 CSS 相关错误。

- [ ] **Step 1.5: 提交**

```bash
git add frontend/src/index.css frontend/src/css/studio.css
git commit -m "refactor(frontend): migrate .focus-ring from studio.css to @utility in index.css"
```

---

## Task 2: 新建 `<ProgressBar>` 组件 + 单测

**Files:**
- Create: `frontend/src/components/ui/ProgressBar.tsx`
- Create: `frontend/src/components/ui/ProgressBar.test.tsx`

### Rationale
TDD：先写测试断言 ARIA 属性与 clamp 行为，再写组件实现。

- [ ] **Step 2.1: 写失败测试**

Create `frontend/src/components/ui/ProgressBar.test.tsx`:

```tsx
import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { ProgressBar } from "./ProgressBar";

describe("ProgressBar", () => {
  it("sets role=progressbar with aria-value attributes", () => {
    const { getByRole } = render(<ProgressBar value={45} />);
    const bar = getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "45");
    expect(bar).toHaveAttribute("aria-valuemin", "0");
    expect(bar).toHaveAttribute("aria-valuemax", "100");
  });

  it("honors custom min/max", () => {
    const { getByRole } = render(<ProgressBar value={7} min={0} max={10} />);
    const bar = getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "7");
    expect(bar).toHaveAttribute("aria-valuemax", "10");
  });

  it("clamps value below min to min", () => {
    const { getByRole } = render(<ProgressBar value={-5} />);
    expect(getByRole("progressbar")).toHaveAttribute("aria-valuenow", "0");
  });

  it("clamps value above max to max", () => {
    const { getByRole } = render(<ProgressBar value={150} />);
    expect(getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
  });

  it("renders bar width as percentage of (value - min) / (max - min)", () => {
    const { getByRole } = render(<ProgressBar value={25} />);
    const bar = getByRole("progressbar");
    const fill = bar.firstElementChild as HTMLElement;
    expect(fill.style.width).toBe("25%");
  });

  it("applies aria-label when label prop provided", () => {
    const { getByRole } = render(<ProgressBar value={10} label="Upload progress" />);
    expect(getByRole("progressbar")).toHaveAttribute("aria-label", "Upload progress");
  });

  it("merges className onto wrapper and barClassName onto fill", () => {
    const { getByRole } = render(
      <ProgressBar value={50} className="h-2" barClassName="bg-emerald-500" />,
    );
    const bar = getByRole("progressbar");
    expect(bar.className).toContain("h-2");
    expect((bar.firstElementChild as HTMLElement).className).toContain("bg-emerald-500");
  });
});
```

- [ ] **Step 2.2: 运行测试确认失败**

Run: `cd frontend && pnpm test ProgressBar`
Expected: FAIL with "Cannot find module './ProgressBar'"

- [ ] **Step 2.3: 写组件实现**

Create `frontend/src/components/ui/ProgressBar.tsx`:

```tsx
import { clsx } from "clsx";

interface ProgressBarProps {
  value: number;
  label?: string;
  min?: number;
  max?: number;
  className?: string;
  barClassName?: string;
}

function clamp(value: number, min: number, max: number): number {
  if (Number.isNaN(value)) return min;
  return Math.min(Math.max(value, min), max);
}

/** Accessible progress bar — role=progressbar + aria-value*; visual styling via className / barClassName. */
export function ProgressBar({
  value,
  label,
  min = 0,
  max = 100,
  className,
  barClassName,
}: ProgressBarProps) {
  const clamped = clamp(value, min, max);
  const pct = max === min ? 0 : ((clamped - min) / (max - min)) * 100;

  return (
    <div
      role="progressbar"
      aria-valuenow={clamped}
      aria-valuemin={min}
      aria-valuemax={max}
      aria-label={label}
      className={clsx(
        "h-1.5 w-full overflow-hidden rounded-full bg-gray-800",
        className,
      )}
    >
      <div
        className={clsx(
          "h-full rounded-full bg-indigo-500 transition-[width]",
          barClassName,
        )}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}
```

**Notes:**
- `clsx` 已在项目依赖中（lockfile 可见），可直接 import
- clamp 内显式处理 `NaN`，避免 `aria-valuenow="NaN"` 污染辅助技术
- 不设默认 `aria-label`——三个调用点各自外部有可见 label（PoM / 章节标题），满足 "accessible name" 要求

- [ ] **Step 2.4: 运行测试确认通过**

Run: `cd frontend && pnpm test ProgressBar`
Expected: 7 tests pass。

- [ ] **Step 2.5: typecheck**

Run: `cd frontend && pnpm typecheck`
Expected: 无错误。

- [ ] **Step 2.6: 提交**

```bash
git add frontend/src/components/ui/ProgressBar.tsx frontend/src/components/ui/ProgressBar.test.tsx
git commit -m "feat(frontend): add accessible <ProgressBar> component"
```

---

## Task 3: 替换 3 处 div-based 进度条为 `<ProgressBar>`

**Files:**
- Modify: `frontend/src/components/pages/ProjectsPage.tsx:87-99`
- Modify: `frontend/src/components/copilot/TodoListPanel.tsx:97-108`
- Modify: `frontend/src/components/canvas/OverviewCanvas.tsx:352-374`

- [ ] **Step 3.1: 替换 `ProjectsPage.tsx`**

Before (line 87-99):
```tsx
{/* Progress bar */}
<div>
  <div className="flex justify-between text-xs text-gray-500 mb-1">
    <span>{phaseLabel || t("dashboard:progress")}</span>
    <span>{pct}%</span>
  </div>
  <div className="h-1.5 rounded-full bg-gray-800 overflow-hidden">
    <div
      className="h-full rounded-full bg-indigo-600 transition-all"
      style={{ width: `${pct}%` }}
    />
  </div>
</div>
```

After:
```tsx
{/* Progress bar */}
<div>
  <div className="flex justify-between text-xs text-gray-500 mb-1">
    <span>{phaseLabel || t("dashboard:progress")}</span>
    <span>{pct}%</span>
  </div>
  <ProgressBar value={pct} barClassName="bg-indigo-600 transition-all" />
</div>
```

Add import at top:
```tsx
import { ProgressBar } from "@/components/ui/ProgressBar";
```

Verify the file's existing imports to confirm path alias `@/` is used; if not, use relative path.

- [ ] **Step 3.2: 替换 `TodoListPanel.tsx`**

Before (line 97-108):
```tsx
{/* Progress bar + count */}
<div className="flex items-center gap-2 shrink-0">
  <div className="h-1 w-16 rounded-full bg-white/10 overflow-hidden">
    <div
      className="h-full rounded-full bg-emerald-500 transition-all duration-500 ease-out"
      style={{ width: `${progressPercent}%` }}
    />
  </div>
  <span className="text-[10px] tabular-nums text-slate-500">
    {completedCount}/{total}
  </span>
</div>
```

After:
```tsx
{/* Progress bar + count */}
<div className="flex items-center gap-2 shrink-0">
  <ProgressBar
    value={progressPercent}
    className="h-1 w-16 bg-white/10"
    barClassName="bg-emerald-500 transition-all duration-500 ease-out"
  />
  <span className="text-[10px] tabular-nums text-slate-500">
    {completedCount}/{total}
  </span>
</div>
```

Add import.

- [ ] **Step 3.3: 替换 `OverviewCanvas.tsx`**

Before (line 362-373):
```tsx
<div
  className="h-1.5 overflow-hidden rounded-full bg-gray-800"
  role="progressbar"
  aria-valuenow={pct}
  aria-valuemin={0}
  aria-valuemax={100}
>
  <div
    className="h-full rounded-full bg-indigo-500"
    style={{ width: `${pct}%` }}
  />
</div>
```

After:
```tsx
<ProgressBar value={pct} />
```

Add import (if not already present in OverviewCanvas; it may already exist for other ui imports).

- [ ] **Step 3.4: 本地验证**

Run: `cd frontend && pnpm check`
Expected: typecheck + lint + test 全过。

手动在浏览器检查一处进度条（如 TodoListPanel），确认视觉无回归。

- [ ] **Step 3.5: 提交**

```bash
git add frontend/src/components/pages/ProjectsPage.tsx \
        frontend/src/components/copilot/TodoListPanel.tsx \
        frontend/src/components/canvas/OverviewCanvas.tsx
git commit -m "refactor(frontend): use <ProgressBar> for 3 div-based progress bars"
```

---

## Task 4: 替换 `focus:outline-none`（无配套）21 处 → `focus-ring`

**Files (14 个):**
- `frontend/src/components/pages/AgentConfigTab.tsx` (1)
- `frontend/src/components/pages/CredentialList.tsx` (1)
- `frontend/src/components/pages/ProviderDetail.tsx` (3)
- `frontend/src/components/canvas/timeline/GridPreviewPanel.tsx` (1)
- `frontend/src/components/ui/CompactInput.tsx` (1)
- `frontend/src/components/ui/PreviewableImageFrame.tsx` (1)
- `frontend/src/components/canvas/timeline/DialogueListEditor.tsx` (2)
- `frontend/src/components/pages/settings/UsageStatsSection.tsx` (1)
- `frontend/src/components/ui/AutoTextarea.tsx` (1)
- `frontend/src/components/canvas/OverviewCanvas.tsx` (2)
- `frontend/src/components/canvas/lorebook/CharacterCard.tsx` (2)
- `frontend/src/components/pages/settings/CustomProviderForm.tsx` (3)
- `frontend/src/components/canvas/timeline/SegmentCard.tsx` (1)
- `frontend/src/components/canvas/lorebook/ClueCard.tsx` (1)

### 替换规则
对每个出现点，判断周围是否已有 `focus-visible:ring-*` 配套：

- **无配套**：`focus:outline-none` → `focus-ring`（同时删掉这个 `focus:outline-none`）
- **已有 `focus-visible:ring-*` 配套**：跳过（归 Task 5 处理）
- **`focus:border-indigo-500`**（改 border 色而非 ring）：保留不动，这不是 focus-ring 语义

Example（AutoTextarea:39 原文）:
```tsx
className={`w-full resize-none ... focus:border-indigo-500 focus:outline-none ${className ?? ""}`}
```
After:
```tsx
className={`w-full resize-none ... focus:border-indigo-500 focus-ring ${className ?? ""}`}
```

（`focus:border-indigo-500` 保留，只是把孤立的 `focus:outline-none` 替换）

- [ ] **Step 4.1: 逐文件处理**

对上述 14 个文件，每个文件用 Edit 工具完成替换。建议流程：
1. Read 文件（找到 `focus:outline-none` 所在行）
2. 确认周围无 `focus-visible:ring-*`
3. Edit 替换

处理 batch 策略：5-7 个文件为一 batch，batch 间运行 `pnpm lint` 验证不引入语法错误。

- [ ] **Step 4.2: grep 校验**

Run:
```bash
cd frontend && grep -rn "focus:outline-none" src --include="*.tsx" --include="*.ts" | grep -v "\.test\." | wc -l
```
Expected: 0

（若仍有匹配，重新审视：是否是 Task 5 归属的"有配套"场景？若是，留给 Task 5）

- [ ] **Step 4.3: 运行 check**

Run: `cd frontend && pnpm check`
Expected: 全过。

- [ ] **Step 4.4: 提交**

```bash
git add -u frontend/src
git commit -m "refactor(frontend): replace focus:outline-none with focus-ring utility (21 occurrences)"
```

---

## Task 5: 替换手写 `focus-visible:ring-indigo-500`（等价）37 处 → `focus-ring`

**Files (13 个):**
- `frontend/src/css/studio.css` (1 — 可能已在 Task 1 删除)
- `frontend/src/components/pages/AgentConfigTab.tsx` (5)
- `frontend/src/components/pages/SystemConfigPage.tsx` (3)
- `frontend/src/components/pages/ProjectSettingsPage.tsx` (5)
- `frontend/src/components/pages/CredentialList.tsx` (1)
- `frontend/src/components/pages/ProviderDetail.tsx` (4)
- `frontend/src/components/pages/settings/CustomProviderDetail.tsx` (4)
- `frontend/src/components/pages/settings/MediaModelSection.tsx` (2)
- `frontend/src/components/pages/settings/UsageStatsSection.tsx` (2)
- `frontend/src/components/pages/settings/CustomProviderForm.tsx` (4)
- `frontend/src/components/ui/ProviderModelSelect.tsx` (1)
- `frontend/src/components/canvas/OverviewCanvas.tsx` (2)
- `frontend/src/components/canvas/timeline/SegmentCard.tsx` (3)

### 替换规则（只做等价替换）

| 原串 | 替换为 |
|---|---|
| `focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500`（核心） | `focus-ring`，保留附加修饰（如 `focus-visible:ring-offset-*`） |
| `focus-visible:ring-indigo-500/60`（半透明变体，视觉近似等价） | `focus-ring` |
| `focus-visible:ring-indigo-400`（**非等价颜色**） | **保留不动**，PR 2 不统一 |
| `focus-visible:ring-indigo-500/40`（差异明显） | **保留不动**，需要独立视觉工单 |

### 边界处理
- 对每处，Read 文件 → 判断是等价/非等价 → Edit
- 当串包含 `focus:outline-none + focus-visible:ring-2 focus-visible:ring-indigo-500` 组合（Task 4 跳过的"有配套"场景）时，**本 Task 一并处理**：整串替换为 `focus-ring`

Example:
```tsx
// Before
"focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2 focus-visible:ring-offset-gray-950"
// After
"focus-ring focus-visible:ring-offset-2 focus-visible:ring-offset-gray-950"
```

- [ ] **Step 5.1: 逐文件处理**

对每个文件 Read → 判断每处是否等价 → Edit。完成一组 5 个文件后运行 `pnpm lint` 验证。

- [ ] **Step 5.2: grep 校验**

Run:
```bash
cd frontend && grep -rn "focus-visible:ring-indigo-500[^/]" src --include="*.tsx" --include="*.ts" | grep -v "\.test\." | wc -l
```
Expected: 0（或仅剩 `indigo-500/40` 等非等价保留项——逐条 review，确认保留是刻意决策）

Run:
```bash
grep -rn "focus-visible:ring-indigo-500/60" src --include="*.tsx" --include="*.ts" | grep -v "\.test\." | wc -l
```
Expected: 0

- [ ] **Step 5.3: 运行 check**

Run: `cd frontend && pnpm check`
Expected: 全过。

- [ ] **Step 5.4: 浏览器烟测**

`cd frontend && pnpm dev`，打开至少 3 个不同页面（Projects、Settings、Overview），Tab 键走一圈，确认焦点态可见且视觉一致。

- [ ] **Step 5.5: 提交**

```bash
git add -u frontend/src
git commit -m "refactor(frontend): replace equivalent focus-visible ring strings with focus-ring utility"
```

---

## Task 6: 删除 2 个 JS `focusRing` 常量

**Files:**
- Modify: `frontend/src/components/canvas/OverviewCanvas.tsx:164`
- Modify: `frontend/src/components/pages/CredentialList.tsx:16`

### Rationale
Task 4/5 完成后，JS 常量拼接的 `${focusRing}` 可以改为类名里直接写 `focus-ring`，常量定义就能删除。

- [ ] **Step 6.1: 处理 `OverviewCanvas.tsx`**

1. Read 文件
2. 删除 line 164 的常量定义：
   ```tsx
   const focusRing = "focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-1 focus-visible:ring-offset-gray-900";
   ```
3. 对所有 `${focusRing}` 用法（line 196、217、227、277、318 等），替换为字面量 `focus-ring`，并保留附加的 ring-offset 修饰（如 `focus-visible:ring-offset-1 focus-visible:ring-offset-gray-900`）

Example:
```tsx
// Before
className={`... disabled:cursor-not-allowed disabled:opacity-50 ${focusRing}`}
// After
className="... disabled:cursor-not-allowed disabled:opacity-50 focus-ring focus-visible:ring-offset-1 focus-visible:ring-offset-gray-900"
```

注意：原常量内含 `ring-offset-*`，需保留在字面量里。

- [ ] **Step 6.2: 处理 `CredentialList.tsx`**

同理，删除 line 16 的：
```tsx
const focusRing = "focus-visible:ring-2 focus-visible:ring-indigo-500/60 focus-visible:outline-none";
```

原常量用的是 `/60` 透明度，与 `@utility focus-ring`（实色）不完全等价。**本 PR 按"删除 JS 常量、改用 utility"处理**（视觉变化同 Task 1 所述的 11 处，属于附带改善）。所有 `${focusRing}` → `focus-ring`。

- [ ] **Step 6.3: grep 校验**

Run:
```bash
grep -rn "focusRing" frontend/src --include="*.tsx" | grep -v "\.test\." | wc -l
```
Expected: 0

- [ ] **Step 6.4: 运行 check**

Run: `cd frontend && pnpm check`
Expected: 全过。

- [ ] **Step 6.5: 提交**

```bash
git add -u frontend/src
git commit -m "refactor(frontend): inline focus-ring utility, drop focusRing JS constants"
```

---

## Task 7: `AutoTextarea` 新增 `id` prop

**Files:**
- Modify: `frontend/src/components/ui/AutoTextarea.tsx`

- [ ] **Step 7.1: 新增 `id` prop**

After Task 4 应用，`AutoTextarea.tsx` 当前约是：

```tsx
interface AutoTextareaProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
}

export function AutoTextarea({
  value,
  onChange,
  placeholder,
  className,
}: AutoTextareaProps) {
  // ...
  return (
    <textarea
      ref={ref}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onInput={resize}
      placeholder={placeholder}
      rows={2}
      className={`... focus:border-indigo-500 focus-ring ${className ?? ""}`}
    />
  );
}
```

修改为：

```tsx
interface AutoTextareaProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
  id?: string;
  "aria-labelledby"?: string;
}

export function AutoTextarea({
  value,
  onChange,
  placeholder,
  className,
  id,
  "aria-labelledby": ariaLabelledBy,
}: AutoTextareaProps) {
  // ... 原 useRef / resize 保持不变
  return (
    <textarea
      ref={ref}
      id={id}
      aria-labelledby={ariaLabelledBy}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onInput={resize}
      placeholder={placeholder}
      rows={2}
      className={`... focus:border-indigo-500 focus-ring ${className ?? ""}`}
    />
  );
}
```

- [ ] **Step 7.2: typecheck**

Run: `cd frontend && pnpm typecheck`
Expected: 无错误（`id` / `aria-labelledby` 为可选，现有调用者不受影响）。

- [ ] **Step 7.3: 运行测试**

Run: `cd frontend && pnpm test`
Expected: 全过。

- [ ] **Step 7.4: 提交**

```bash
git add frontend/src/components/ui/AutoTextarea.tsx
git commit -m "feat(frontend): add optional id/aria-labelledby props to AutoTextarea"
```

---

## Task 8: 业务 textarea 补 `<label htmlFor>` 关联

**Files (10 个 `<textarea>` 位置，9 个业务点 + 1 个 AutoTextarea 基础组件已在 Task 7 处理):**

- `frontend/src/components/copilot/AgentCopilot.tsx:442`
- `frontend/src/components/canvas/OverviewCanvas.tsx:257`
- `frontend/src/components/canvas/SourceFileViewer.tsx:146`
- `frontend/src/components/canvas/lorebook/ClueCard.tsx:157`
- `frontend/src/components/canvas/lorebook/AddCharacterForm.tsx:108`
- `frontend/src/components/canvas/lorebook/AddClueForm.tsx:110`
- `frontend/src/components/canvas/lorebook/CharacterCard.tsx:261`
- `frontend/src/components/canvas/timeline/PreprocessingView.tsx:133`
- `frontend/src/components/canvas/timeline/SegmentCard.tsx:298`

### 通用模式

对每个 textarea：

1. Read 上下文（找到 textarea 附近的可见标题 / 说明文字，可能是 `<span>`、`<div>` 或 heading）
2. 判断现状：
   - **无任何标签**：在 textarea 前新增 `<label htmlFor={id}>` + i18n key
   - **有 `<span>` / `<div>` 作标签**：改 tag 为 `<label htmlFor={id}>`（保留原样式类）
   - **已有 `<label htmlFor>` 关联**（读屏软件已能用）：**跳过**
3. 用 `const id = useId();` 生成 id，传给 textarea

### Example pattern

Before (SegmentCard.tsx 附近示意):
```tsx
<span className="text-xs text-gray-400">备注</span>
<textarea
  className="..."
  value={note}
  onChange={(e) => setNote(e.target.value)}
/>
```

After:
```tsx
const noteId = useId();
// ...
<label htmlFor={noteId} className="text-xs text-gray-400">{t("note_label")}</label>
<textarea
  id={noteId}
  className="..."
  value={note}
  onChange={(e) => setNote(e.target.value)}
/>
```

若标题已经是 i18n 字符串（通过 `t(...)` 渲染），**不新增 i18n key**；仅把承载元素从 span/div 改为 label + `htmlFor`。

- [ ] **Step 8.1: 逐文件处理（9 处）**

对上述 9 个位置按通用模式处理：
- 必要时从 `react` import `useId`
- 若需新的 i18n key，在对应 namespace 的 zh/en 文件同步添加（文本完全一致的前提下跳过）

- [ ] **Step 8.2: i18n 一致性校验**

Run: `cd /Users/pollochen/MyProjects/ArcReel/.worktrees/fix/a11y-focus-ring-292 && uv run pytest tests/test_i18n_consistency.py -v`
Expected: PASS

- [ ] **Step 8.3: typecheck + test**

Run: `cd frontend && pnpm check`
Expected: 全过；改动的文件若有 `.test.tsx`（如 lorebook 目录下），测试应仍通过（label 添加后 `getByLabelText` 查询更容易成功）。

- [ ] **Step 8.4: 提交**

```bash
git add -u frontend/src
git commit -m "a11y(frontend): associate <label htmlFor> with all business textareas"
```

---

## Task 9: File input `aria-label`（9 文件 10 input）

**Files (9 个生产文件):**
- `frontend/src/components/pages/ProjectsPage.tsx:321` — 项目导入 zip
- `frontend/src/components/pages/CredentialList.tsx:357` — JSON 凭据文件
- `frontend/src/components/pages/CreateProjectModal.tsx:351` — 风格参考图
- `frontend/src/components/layout/AssetSidebar.tsx:260` — 素材文件上传
- `frontend/src/components/copilot/AgentCopilot.tsx:497` — 对话附件（多图）
- `frontend/src/components/canvas/OverviewCanvas.tsx:238` — 风格参考图
- `frontend/src/components/canvas/WelcomeCanvas.tsx:185` — 脚本文件上传
- `frontend/src/components/canvas/WelcomeCanvas.tsx:220` — 脚本文件上传（第二处）
- `frontend/src/components/canvas/lorebook/CharacterCard.tsx:252` — 角色参考图
- `frontend/src/components/canvas/lorebook/AddCharacterForm.tsx:183` — 新角色参考图

- [ ] **Step 9.1: 确认新增 i18n key 清单**

每处 file input 的 `aria-label` 应贴合上下文。按场景列 key（在 `i18n/{zh,en}/dashboard.ts`，已有 `upload_style_ref_aria` 可复用于两处风格图）：

| 文件 | 建议 key | zh | en |
|---|---|---|---|
| ProjectsPage | `import_project_file_aria` | 导入项目 ZIP 文件 | Import project ZIP file |
| CredentialList | `import_credential_file_aria` | 导入 JSON 凭据文件 | Import JSON credential file |
| CreateProjectModal | `upload_style_ref_aria` (已存在) | 上传风格参考图 | Upload style reference image |
| AssetSidebar | `upload_asset_file_aria` | 上传素材文件 | Upload asset file |
| AgentCopilot | `upload_attachment_aria` | 上传附件图片 | Upload attachment image |
| OverviewCanvas | `upload_style_ref_aria` (已存在) | 上传风格参考图 | Upload style reference image |
| WelcomeCanvas (×2) | `upload_script_file_aria` | 上传脚本文件 | Upload script file |
| CharacterCard | `upload_character_ref_aria` | 上传角色参考图 | Upload character reference image |
| AddCharacterForm | `upload_character_ref_aria` (同上) | 上传角色参考图 | Upload character reference image |

先检查 `frontend/src/i18n/zh/dashboard.ts` 是否已有这些 key，按实际添加缺失项。

- [ ] **Step 9.2: 添加 i18n key**

按上表把缺失的 key 添加到 `frontend/src/i18n/zh/dashboard.ts` 和 `frontend/src/i18n/en/dashboard.ts`（两端 key 必须一致）。

- [ ] **Step 9.3: 为每个 file input 加 `aria-label`**

Example:
```tsx
// Before
<input ref={fileRef} type="file" accept=".json,application/json" className="hidden" onChange={...} />

// After
<input
  ref={fileRef}
  type="file"
  accept=".json,application/json"
  aria-label={t("import_credential_file_aria")}
  className="hidden"
  onChange={...}
/>
```

对 9 个文件逐一 Edit 添加（注意 `WelcomeCanvas` 有两处）。

- [ ] **Step 9.4: grep 校验**

Run:
```bash
# 所有生产 file input 应有 aria-label
for f in $(grep -l 'type="file"' frontend/src --include="*.tsx" -r | grep -v "\.test\."); do
  if ! grep -A 5 'type="file"' "$f" | grep -q "aria-label"; then
    echo "MISSING aria-label in $f"
  fi
done
```
Expected: 无 MISSING 输出。

- [ ] **Step 9.5: i18n + typecheck + test**

Run:
```bash
uv run pytest tests/test_i18n_consistency.py -v
cd frontend && pnpm check
```
Expected: 全过。

- [ ] **Step 9.6: 提交**

```bash
git add -u frontend/src
git commit -m "a11y(frontend): add aria-label to hidden file inputs (9 files)"
```

---

## Task 10: 最终验证 + 更新 `--max-warnings`

**Files:**
- Modify: `frontend/package.json`（按实际 warning 数）

- [ ] **Step 10.1: 最终 lint 数字**

Run: `cd frontend && pnpm lint 2>&1 | tail -3`
Expected: `✖ N problems (0 errors, N warnings)` — 记下 N

- [ ] **Step 10.2: 更新 `package.json`**

若 N < 212，把 `package.json` 里 `"lint": "eslint . --max-warnings=212"` 的 212 改为 N。
若 N = 211 或 212，保持 212 不变（允许 +1 浮动空间）。
若 N > 212，回头审视哪些 warning 是 PR 2 引入的并修掉（不允许升）。

- [ ] **Step 10.3: 运行完整 check**

Run: `cd frontend && pnpm check`
Expected: 全过。

- [ ] **Step 10.4: i18n 一致性**

Run: `cd /Users/pollochen/MyProjects/ArcReel/.worktrees/fix/a11y-focus-ring-292 && uv run pytest tests/test_i18n_consistency.py -v`
Expected: PASS

- [ ] **Step 10.5: grep 校验 acceptance 标准**

```bash
# 1. focus:outline-none 全部替换（Task 4 + 嵌入 Task 5）
grep -rn "focus:outline-none" frontend/src --include="*.tsx" --include="*.ts" | grep -v "\.test\." | wc -l
# Expected: 0

# 2. focusRing JS 常量全部删除
grep -rn "focusRing" frontend/src --include="*.tsx" | grep -v "\.test\." | wc -l
# Expected: 0

# 3. studio.css 的 .focus-ring 已删除
grep -n "focus-ring" frontend/src/css/studio.css
# Expected: 无输出

# 4. 生产 file input 全部有 aria-label
for f in $(grep -l 'type="file"' frontend/src --include="*.tsx" -r | grep -v "\.test\."); do
  if ! grep -A 5 'type="file"' "$f" | grep -q "aria-label"; then
    echo "MISSING aria-label in $f"
  fi
done
# Expected: 无 MISSING 输出

# 5. 等价的手写 focus-visible:ring-indigo-500（非 /40 等变体）已清理
grep -rn "focus-visible:ring-indigo-500\b" frontend/src --include="*.tsx" --include="*.ts" | grep -v "\.test\." | grep -v "/40"
# Expected: 无输出（或仅剩经审视确认保留的非等价项）
```

- [ ] **Step 10.6: 浏览器终验**

`cd frontend && pnpm dev`，在 Chrome devtools Accessibility 面板逐页检查：
- Projects 页面：进度条节点应显示 `role=progressbar`、`aria-valuenow`；导入按钮的 file input 应有 accessible name
- Overview 页面：风格图上传、textarea、进度条
- Settings：凭据导入 JSON 文件按钮
- Copilot 面板：附件 file input

Expected: 全部通过 axe-core 基础检查（若装了插件）。

- [ ] **Step 10.7: 提交 baseline 更新（若有变化）**

```bash
# 仅当 Step 10.2 修改了 package.json
git add frontend/package.json
git commit -m "chore(frontend): lower --max-warnings baseline to <N>"
```

- [ ] **Step 10.8: 推送并开 PR**

```bash
git push -u origin fix/a11y-focus-ring-292
gh pr create --title "fix(frontend): unify focus-ring + a11y attrs on textarea/progressbar/file input (#292)" \
  --body "$(cat <<'EOF'
## Summary

Closes #292 (parent #219 PR 2 of 3). Implements the four themed a11y fixes:

- Unify `focus-ring` token via Tailwind v4 `@utility` in `index.css`; replace 21 `focus:outline-none` + 37 equivalent hand-written `focus-visible:ring-indigo-500` strings; remove 2 JS `focusRing` constants and old `studio.css .focus-ring`
- Add `<label htmlFor>` association to 9 business textareas; AutoTextarea gains optional `id` / `aria-labelledby` props
- Extract reusable `<ProgressBar>` component (role=progressbar + aria-value*); replace 3 div-based progress bars
- Add `aria-label` to 9 hidden `<input type="file">` (WelcomeCanvas has 2)

See spec: `docs/superpowers/specs/2026-04-13-a11y-focus-ring-pr2-design.md`

## Non-goals (deferred to PR 3)

- no-autofocus / no-static-element-interactions / click-events-have-key-events / media-has-caption / typed-linting (180+ warnings)
- ConfirmDialog / `confirm()` replacement (separate issue)

## Warning count

`--max-warnings` is <unchanged|reduced from 212 to N>. The four fixes have **no intersection** with the 5 rules in `MIGRATION_WARN_RULES_A11Y`, so warning count is largely unchanged by design — the improvement is in real a11y semantics, not lint numbers.

## Test plan

- [ ] `pnpm check` passes
- [ ] `uv run pytest tests/test_i18n_consistency.py` passes
- [ ] Tab-through Projects / Overview / Settings / Copilot — focus ring visible
- [ ] DevTools a11y: progressbar nodes have aria-value*, file inputs have names
EOF
)"
```

---

## Self-Review Checklist（Executor 开工前读一遍）

- Task 1 → spec「focus-ring token 实现」：✓ 载体 `@utility`、规格 `ring-indigo-500`
- Task 2 → spec「`<ProgressBar>` 接口」：✓ prop 签名匹配、clamp 行为、ARIA 属性
- Task 3 → spec「抽 ProgressBar 组件」3 处替换：✓
- Task 4 + Task 5 → spec「替换规则」：✓ 等价替换、保留非等价颜色
- Task 6 → spec「删 2 个 JS 常量」：✓ `OverviewCanvas:164` + `CredentialList:16`
- Task 7 → spec「AutoTextarea 改造」：✓ `id` prop 透传
- Task 8 → spec「textarea label 关联」：✓ useId + htmlFor
- Task 9 → spec「File input 清单」：✓ 9 文件（注意 WelcomeCanvas 两处）
- Task 10 → spec「警告数治理」「验收标准」：✓ 保持或降 baseline，不升
