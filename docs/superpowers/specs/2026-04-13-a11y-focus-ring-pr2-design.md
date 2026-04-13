# a11y 主题修复：focus-ring + textarea label + progressbar ARIA + file input aria-label（PR 2）

- **日期**：2026-04-13
- **对应 Issue**：#292（parent #219）
- **依赖**：PR 1 #290 已合并（ESLint + jsx-a11y 基础设施）
- **PR 标题**（建议）：`fix(frontend): unify focus-ring + a11y attrs on textarea/progressbar/file input`
- **作者**：Pollo

## 背景

PR 1（#290）已落地 ESLint + jsx-a11y 基础设施与 baseline ratchet 机制，本 PR 承接 issue #219 的主题修复，范围在 issue #292 明确界定。切分原则延续 PR 1 spec：本 PR 修"视觉 / 语义主题"，PR 3 做"纯 lint 扫尾"。

## 范围

### 做的事（四类）

1. **focus-ring token 统一**
   - `frontend/src/index.css` 新增 `@utility focus-ring`
   - 替换 **21 处** `focus:outline-none`（无配套的）跨 14 个文件
   - 替换 **37 处** 手写 `focus-visible:ring-indigo-*`（**仅等价的**）跨 13 个文件
   - 删 `frontend/src/components/canvas/OverviewCanvas.tsx:164` 的 `focusRing` JS 常量
   - 删 `frontend/src/components/pages/CredentialList.tsx:16` 的 `focusRing` JS 常量
   - 删 `frontend/src/css/studio.css:10` 的 `.focus-ring` CSS 规则

2. **textarea label 关联**
   - `AutoTextarea` 基础组件增加可选 `id` 透传 prop
   - 业务处（`VideoPromptEditor` / `ImagePromptEditor` / `SegmentCard` 等使用 `<textarea>` 的位置）改显式 `<label htmlFor={id}>` + `const id = useId()`

3. **抽 `<ProgressBar>` 组件**（`frontend/src/components/ui/ProgressBar.tsx`）
   - 内置 `role="progressbar"` + `aria-valuenow/min/max` + 可选 `aria-label`
   - 替换 3 处 div-based 进度条：`ProjectsPage:96` / `TodoListPanel:102` / `OverviewCanvas:371`

4. **隐藏 file input `aria-label`**
   - 9 个生产文件的 `<input type="file">` 补 `aria-label`（内容走 i18n）
   - 文件清单见下方「File input 清单」

### 不做的事

- ❌ `no-autofocus`（4 处 autoFocus prop）→ PR 3
- ❌ `no-static-element-interactions` / `click-events-have-key-events` / `no-noninteractive-element-interactions`（div-as-button 改 `<button>`）→ PR 3
- ❌ `media-has-caption`（`<audio>/<video>` 加 `<track>`）→ PR 3
- ❌ typed-linting warning（`no-unsafe-*` / `no-floating-promises` 等约 180+ 条）→ PR 3
- ❌ 任何非「等价」的手写 focus ring（例：`ring-indigo-400` / `ring-indigo-500/60`）→ 由未来独立视觉统一工单处理
- ❌ `confirm()` / `<ConfirmDialog>` → 独立工单
- ❌ `MIGRATION_WARN_RULES_A11Y` 条目删除（PR 2 修的四类问题与现有 5 条 rule **无交集**，详见下方「警告数治理」）

## 技术决策

### focus-ring token 实现

`frontend/src/index.css` 新增：

```css
@utility focus-ring {
  @apply focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500;
}
```

视觉规格与 PR 1 spec 锁定的方案 B 一致（不含 `ring-offset-*`）。

### 替换规则

仅做**等价字符串替换**，不做视觉重设计：

| 原串 | 替换后 |
|---|---|
| `focus:outline-none`（单独，无 `focus-visible:ring-*` 配套） | `focus-ring` |
| `focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500`（core，可带无冲突修饰） | `focus-ring`，保留后续修饰 |
| `focus-visible:ring-indigo-500/60`（与 token 视觉等价的透明度/色调变体） | `focus-ring` |
| `focus-visible:ring-indigo-400`（非等价颜色） | **保留不动**，PR 2 不统一 |
| `input:focus:border-indigo-500`（改 border 色而非 ring） | **保留不动**，非 focus-ring 语义 |

### `AutoTextarea` 改造

新增 `id?: string` prop（透传给内部 `<textarea>`）。调用方：

```tsx
const id = useId();
return (
  <div>
    <label htmlFor={id} className="...">{t("...")}</label>
    <AutoTextarea id={id} ... />
  </div>
);
```

### `<ProgressBar>` 接口

```tsx
interface ProgressBarProps {
  value: number;              // 当前值（最终会 clamp 到 [min, max]）
  label?: string;             // aria-label；不传则由调用方保证外部有可见 label 关联
  min?: number;               // 默认 0
  max?: number;               // 默认 100
  className?: string;         // 外壳额外样式
  barClassName?: string;      // 填充条额外样式（颜色等）
}
```

渲染：

```tsx
<div role="progressbar"
     aria-valuenow={clamped}
     aria-valuemin={min ?? 0}
     aria-valuemax={max ?? 100}
     aria-label={label}
     className={clsx("h-1.5 w-full overflow-hidden rounded-full bg-gray-800", className)}>
  <div className={clsx("h-full rounded-full bg-indigo-500 transition-[width]", barClassName)}
       style={{ width: `${pct}%` }} />
</div>
```

三处调用方保留各自颜色/高度差异通过 `className` / `barClassName` 透传。

### File input 清单

9 个生产文件的 `<input type="file">`（grep 核实，排除 `*.test.*`）：

- `src/components/pages/ProjectsPage.tsx`
- `src/components/pages/CredentialList.tsx`
- `src/components/pages/CreateProjectModal.tsx`
- `src/components/layout/AssetSidebar.tsx`
- `src/components/copilot/AgentCopilot.tsx`
- `src/components/canvas/lorebook/CharacterCard.tsx`
- `src/components/canvas/lorebook/AddCharacterForm.tsx`
- `src/components/canvas/WelcomeCanvas.tsx`
- `src/components/canvas/OverviewCanvas.tsx`

每处依语义补 `aria-label`，内容走 i18n（新增对应 zh/en key）。

### 警告数治理

PR 2 修的四类问题与 `MIGRATION_WARN_RULES_A11Y` 当前 5 条 rule（`click-events-have-key-events` / `media-has-caption` / `no-autofocus` / `no-noninteractive-element-interactions` / `no-static-element-interactions`）**无交集**：

- focus-ring 替换不触发任何 jsx-a11y rule
- 补 label + `htmlFor` 也不触发（`label-has-associated-control` 本来就通过——原代码没有 `<label>` 元素，规则不检查）
- `<ProgressBar>` 抽组件是重构，不引入/减少 warning
- file input 加 `aria-label` 不触发新 warning

**结论**：PR 2 修完后 `--max-warnings` 数字**基本不变**（预计仍为 211 左右）。`MIGRATION_WARN_RULES_A11Y` 不删条目。在 spec 和 CR description 明确说明，避免"既然是 a11y PR 为什么 warning 数不降"的疑问。

这同时修正了 issue #292 文案里"同步从 `MIGRATION_WARN_RULES_A11Y` 删除已清零的 rule 条目"的描述——该描述在核实后不适用于本 PR。

如果在修改过程中顺带触发 `--fix` 自动修复了附近一些 warning（如 `no-unused-vars`），对应 warning 数自然下降，直接更新 `package.json` 的 `--max-warnings` 为实测数字即可——**只允许降不允许升**。

## 实施顺序（建议）

1. 新增 `@utility focus-ring` 到 `frontend/src/index.css`
2. 新增 `<ProgressBar>` 组件 + 单测，替换 3 处调用点
3. 批量替换 `focus:outline-none` 与手写 `focus-visible:ring-indigo-500[/变体]` → `focus-ring`
4. 删除两个 JS `focusRing` 常量 + `studio.css` 里的 `.focus-ring`
5. `AutoTextarea` 新增 `id` prop；业务处补 `<label htmlFor>` + `useId()`
6. File input 补 `aria-label`（含 i18n key）
7. 本地 `pnpm check` 全绿
8. 更新 `package.json` 的 `--max-warnings` 为实测 warning 数（若无变化保持 212，若下降则下调）

## 验收标准

1. ✅ `pnpm lint` 返回 0，warning 数 ≤ `--max-warnings`，且**不高于 PR 2 开工前的 211**
2. ✅ `pnpm check` 返回 0（typecheck + lint + test）
3. ✅ `<ProgressBar>` 单元测试：ARIA 属性断言 + value 边界（<min、>max、NaN 等）+ 基础快照
4. ✅ grep 校验：`focus:outline-none` 在 `src/**/*.tsx` 中 0 次匹配
5. ✅ grep 校验：`focusRing` 在 `src/**/*.tsx` 中 0 次匹配
6. ✅ grep 校验：`studio.css` 中 `.focus-ring` 不存在
7. ✅ grep 校验：`<input type="file"` 在生产代码里所有匹配均有同兄弟 `aria-label`
8. ✅ 业务 textarea 处全部有 `<label htmlFor>` 关联（人工 diff 审视）
9. ✅ i18n key 新增项在 `zh` / `en` 两端齐全（`test_i18n_consistency.py` 通过）
10. ✅ GitHub Actions `frontend-tests` job 全绿

## 风险与缓解

| # | 风险 | 缓解 |
|---|------|------|
| 1 | "等价替换"边界判断失误，导致视觉微变 | 替换规则写死在 spec，Review 时对照；保留非等价颜色（`ring-indigo-400` 等）不动 |
| 2 | `@utility focus-ring` 与 Tailwind v4 行为不符（指令名冲突或组合顺序问题） | 先在 `index.css` 加 utility 并在一处替换本地 smoke test；浏览器手测焦点态可见后再批量 |
| 3 | `AutoTextarea` 新增 `id` prop 破坏现有调用方 | `id` 为可选 prop；不传 id 的旧调用维持原行为 |
| 4 | `<ProgressBar>` 三处调用颜色/动效差异无法通过 `className` 覆盖 | 实施时若发现 class 冲突，允许在 `ProgressBar` 增加 minimal 样式 prop（如 `height`），避免下沉重写 |
| 5 | file input `aria-label` 文案在不同场景语义不一致 | 每处独立一个 i18n key（不共用），按调用场景命名 |
| 6 | 改动 warning 数不降，CR 可能质疑"这个 PR 干了什么" | spec 「警告数治理」章节明确解释 + CR description 复述 |
| 7 | 实施过程中发现 issue 清单外的 a11y 问题（范围内四类） | 一并修，不拆单独 PR；在 CR description 标注；不扩到四类之外 |

## 非目标

- 视觉统一（非等价颜色、`ring-offset`、focus border 色等）
- jsx-a11y 剩余 5 条 rule 清零
- typed-linting 修复
- `<ConfirmDialog>` 组件 / `confirm()` 替换
- ESLint / Tailwind 版本升级
- 修改 PR 1 spec 决策（focus-ring token 视觉规格、`@utility` 载体、`<ProgressBar>` 抽象等）

## Brainstorming 过程记录

本次 brainstorming 的关键确认：

| 决策维度 | 选项 | 备注 |
|---|---|---|
| PR 2 范围扩展 | A → 只修 issue #292 的四类，不扩 | 保持"主题 vs 扫尾"切分清晰，PR 3 处理剩余 |
| `MIGRATION_WARN_RULES_A11Y` 条目处理 | 不删（与 PR 2 范围无交集） | 修正 issue 文案描述 |
| `--max-warnings` 数字 | 保持或实测下调（不升） | 预计保持 211/212 |
| file input 文件清单 | issue 列 9 个组件，实测 10 个文件 | 以实测为准，多出的一并修 |
| focus-ring 等价边界 | 非等价颜色（`ring-indigo-400` 等）保留不动 | 避免 PR 2 掺入视觉设计决策 |
