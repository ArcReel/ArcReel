# shadcn/ui 底座选型：Radix 还是 Base UI，以及 style 预设

> 状态：调研完成，结论供地图 [#2959](https://github.com/ArcReel/ArcReel/issues/2959) 汇总。
> 关联：[#2960](https://github.com/ArcReel/ArcReel/issues/2960)（本票）。
> 数据日期：2026-10-02。shadcn/ui 的事实来自官方文档与 changelog（Context7 与 `shadcn-ui/ui` 仓库源文件）；Base UI 与 Radix 的事实来自各自官方文档和 GitHub 源码；版本、发布日期与周下载量来自 npm registry（`npm view` 与 `api.npmjs.org`）；仓库活跃度来自 GitHub REST / Search API（`gh api`）；体积来自本机 Vite 8.2.1 实测（见 §5）。

## 结论一句话

**推荐 Base UI 作为底层、Nova 作为 style 预设（`components.json` 中写 `"style": "base-nova"`）。** 理由：Base UI 已是 shadcn/ui 的默认底层，新组件优先或独占在 Base UI 上发布；本项目必需的 Combobox 在 Radix 路线下也由 Base UI 实现，选 Radix 会同时背两套底层，体积却并不更小；Base UI 基于本项目已在使用的 Floating UI，维护节奏也明显更快。Nova 是八个预设中唯一同时满足「紧凑」与「正文不小于 13px」的一个。

## 结论速览

| 问题 | 一行结论 |
|---|---|
| 组件覆盖 | 票面列出的 14 个组件，Radix 与 Base UI 两个底层都有 shadcn 实现。差异在于：Radix 底层的 Combobox 直接 import `@base-ui/react`；Toast 只有 Base UI 版本；两者的 Command 都依赖 `cmdk`，而 `cmdk` 依赖 `@radix-ui/react-dialog`。 |
| React 19 / Tailwind v4 / Vite 8 | 两者都兼容。peerDependencies 都包含 React 19；shadcn 的 Tailwind v4 支持与底层无关；本机用 Vite 8.2.1 构建两者都成功。 |
| 焦点与可访问性 | 两者都遵循 WAI-ARIA APG，都提供焦点陷阱、焦点归还和 Esc 关闭。Base UI 用 `initialFocus` / `finalFocus` 和带 `reason` 的 `onOpenChange` 统一了关闭拦截；Radix 用 `onOpenAutoFocus` 和多个 `on*Outside` 回调。 |
| portal 与层叠 | 两者默认都 portal 到 `body`，都支持 `container`。模态时 Radix 把 `body.style.pointerEvents` 设为 `none`，Base UI 改用内部的全屏 backdrop。driver.js 的遮罩 z-index 为 10000，弹出框为 1000000000，都高于 shadcn 弹层的 `z-50`，选哪个底层都不改变这一关系。 |
| 包体积 | 只看 8 个基础原语，Radix 比 Base UI 小约 25 KB（gzip）。加上本项目必需的 Combobox 后，两条路线相差约 2 KB（Base UI 134.9 KB，Radix 132.9 KB，均含 React 运行时）。 |
| 维护活跃度 | Base UI 从 1.0.0（2025-12-11）到 1.8.0（2026-09-04）几乎每月发一个 minor 版本，2026-06 以来合并 PR 599 个；Radix 的最后一次 commit 在 2026-07-31，1.7.0 停在 RC 阶段，同期合并 PR 101 个。 |
| 预设 | 推荐 Nova（默认按钮 `h-8`、正文 `text-sm`、对话框 `p-4`）。备选 Rhea（同样紧凑，但几何形状更圆润）。Mira 与 Lyra 的正文是 `text-xs`（12px），低于地图约定的 13px；Vega、Maia、Luma、Sera 的密度不适合创作工具界面。 |
| 迁移成本 | 自研原语与 `@headlessui/react`、`@floating-ui/react` 全部可以由 Base UI 对应组件替换，迁移完成后这两个依赖可以移除。由于本项目不是从 Radix 版 shadcn 迁过来的，选 Base UI 不会产生 Radix → Base 的二次迁移。 |

## 1. 环境事实（仓库核实，只读）

- `frontend/package.json` 与 `frontend/pnpm-lock.yaml`：`react@19.3.0`、`react-dom@19.3.0`（specifier `^19.2.8`）、`tailwindcss@4.3.3`、`@tailwindcss/vite`、`vite@8.3.1`（specifier `^8.2.1`）、`typescript@6.0.3`、`@headlessui/react@2.2.10`、`@floating-ui/react@0.27.20`、`driver.js@1.8.0`、`lucide-react`、`framer-motion`。仓库中没有 `components.json`，也没有直接依赖 `radix-ui` 或 `@base-ui/react`。
- 锁文件中已经有 `@base-ui/react@1.0.0` 和十余个 `@radix-ui/react-*` 包，它们都是通过 `@lobehub/icons` → `@lobehub/ui` 引入的传递依赖，不是本项目直接使用的依赖。
- `frontend/src/components/ui/` 中的自研原语及其用法：
  - `Popover.tsx`：基于 `@floating-ui/react` 的 `useFloating`、`FloatingPortal`、`flip`、`shift`、`size`、`useDismiss`，z-index 取自 `utils/ui-layers.ts`。
  - `ModalShell.tsx`：`createPortal` 到 `document.body`，配合自研的 `useFocusTrap`、`useEscapeClose` 和 body 滚动锁引用计数。`GlassModal` 是在 `ModalShell` 外面套的视觉皮肤。
  - `ModelCombobox.tsx`：基于 `@headlessui/react` 的 `Combobox`，`anchor="bottom start"`，允许输入自由文本。
  - `ProviderModelSelect.tsx` 也直接使用 `@floating-ui/react`。
  - `ImageLightbox`、`CreateProjectModal`、`VersionTimeMachine` 各自调用 `createPortal`。
- `frontend/src/utils/ui-layers.ts`：`assistantLocalPopover: z-20`、`workspaceFloating: z-30`、`workspacePopover: z-40`、`modal: z-50`、`toast: z-60`。
- `frontend/src/onboarding/tour.ts`：引导启动时给 `document.body` 的直接子节点（driver.js 自己的节点除外）打 `inert`；在捕获阶段拦截除 `Tab` 以外的所有 `keydown`；`interactive` 步只为目标元素开一条通路。注释说明这样做是因为 `ModalShell` 等对话框是 `#app-root` 的兄弟节点。
- `frontend/src/components/ui/Popover.tsx` 与 shadcn 的 `popover.tsx` 只差大小写，在大小写不敏感的文件系统（macOS 默认 APFS）上会冲突。这是票面已知事实，与底层选择无关。

## 2. shadcn/ui 的现状

### 2.1 底层与预设的时间线

- **2025-12-12，`npx shadcn create`**：首次允许在 Radix 与 Base UI 之间选择底层，并推出 Vega、Nova、Maia、Lyra、Mira 五个 style。官方说明「We rebuilt every component for Base UI, keeping the same abstraction」，从远程 registry 拉取组件时 CLI 会自动识别底层并做对应转换。—— [changelog 2025-12](https://ui.shadcn.com/docs/changelog/2025-12-shadcn-create)
- **2026-01**：所有组件补齐了 Base UI 的独立文档。—— [changelog 2026-01 Base UI](https://ui.shadcn.com/docs/changelog/2026-01-base-ui)
- **2026-03-31 Luma、2026-04-16 Sera、2026-05-26 Rhea**：新增三个 style，两个底层都可以使用。Rhea 的定位是「A more compact Luma… Built for focused product interfaces」，并解释了为什么不改 `--spacing`：`--spacing` 是乘数，修改后 `p-2`、`w-4` 等工具类在全站的含义都会变。—— [Luma](https://ui.shadcn.com/docs/changelog/2026-03-luma)、[Sera](https://ui.shadcn.com/docs/changelog/2026-04-sera)、[Rhea](https://ui.shadcn.com/docs/changelog/2026-05-rhea)
- **2026-07-02，Base UI 成为默认底层**：`npx shadcn init` 默认选 Base UI，文档默认打开 Base UI 标签页。官方给出的理由是 Base UI 已到 1.6.0、周下载量超过 600 万，shadcn/create 上新建项目选 Base UI 与选 Radix 的比例约为 2 比 1。Radix「is not being deprecated」，每次更新和新组件都会同时发布到两个底层，「unless a component only exists in Base UI」。对新项目，官方推荐 Base UI；需要 Radix 时使用 `npx shadcn init -b radix`。—— [changelog 2026-07 Base UI default](https://ui.shadcn.com/docs/changelog/2026-07-base-ui-default)
- **2026-07-17，React Aria 成为第三个底层**（`--base aria`），可以与全部八个 style 搭配。—— [changelog 2026-07 React Aria](https://ui.shadcn.com/docs/changelog/2026-07-react-aria)
- **2026-07-23，Toast 组件**：「available for Base UI projects」，也就是只有 Base UI 版本。—— [changelog 2026-07 Toast](https://ui.shadcn.com/docs/changelog/2026-07-toast)
- `components.json` 的 `style` 字段以底层为前缀：`radix-<style>`、`base-<style>`、`aria-<style>`。—— [`packages/shadcn/src/preset/preset.ts`](https://github.com/shadcn-ui/ui/blob/main/packages/shadcn/src/preset/preset.ts)、[`apps/v4/registry/config.ts`](https://github.com/shadcn-ui/ui/blob/main/apps/v4/registry/config.ts)

### 2.2 迁移方向是单向的

shadcn 官方只提供 Radix → Base UI 的迁移 skill（`migrate-radix-to-base`），它会逐个组件改写，并在 `.migration/` 中为每个组件生成报告。机械性改动会自动修复（例如 `asChild` 改为 `render`），行为差异只标记、不自动修补。官方没有提供反方向的迁移工具。—— [changelog 2026-07 Base UI default](https://ui.shadcn.com/docs/changelog/2026-07-base-ui-default)、[`skills/migrate-radix-to-base/SKILL.md`](https://github.com/shadcn-ui/ui/blob/main/skills/migrate-radix-to-base/SKILL.md)

对本项目而言：现在选 Radix，等于选了一条官方已经铺好「迁出」路径的底层。

### 2.3 Tailwind v4 / React 19 / Vite

shadcn 支持 Tailwind v4 与 React 19：使用 `@theme inline`、OKLCH 颜色、`data-slot` 属性，去掉了 `forwardRef`；动画库从 `tailwindcss-animate` 换成 `tw-animate-css`。这些都与底层选择无关。—— [Tailwind v4](https://ui.shadcn.com/docs/tailwind-v4)

Vite 项目通过 `@tailwindcss/vite` 插件和 `@` 路径别名接入，本项目已有这两项。—— [Installation: Vite](https://ui.shadcn.com/docs/installation/vite)

## 3. 组件覆盖

shadcn 仓库中三个底层的 `ui/` 目录（[base](https://github.com/shadcn-ui/ui/tree/main/apps/v4/registry/bases/base/ui)、[radix](https://github.com/shadcn-ui/ui/tree/main/apps/v4/registry/bases/radix/ui)、[aria](https://github.com/shadcn-ui/ui/tree/main/apps/v4/registry/bases/aria/ui)）都包含票面列出的 14 个组件。依赖来自各自的 `_registry.ts`（[base](https://github.com/shadcn-ui/ui/blob/main/apps/v4/registry/bases/base/ui/_registry.ts)、[radix](https://github.com/shadcn-ui/ui/blob/main/apps/v4/registry/bases/radix/ui/_registry.ts)）。

| 组件 | Base UI 底层 | Radix 底层 | 备注 |
|---|---|---|---|
| Dialog / AlertDialog / Sheet | `@base-ui/react` | `radix-ui` | Base UI 的 AlertDialog 始终是模态，默认不响应外部点击关闭。 |
| Popover / DropdownMenu / Select / Tooltip / Tabs / ScrollArea | `@base-ui/react` | `radix-ui` | — |
| **Combobox** | `@base-ui/react` | **`@base-ui/react`** | Radix 底层的 [`combobox.tsx`](https://github.com/shadcn-ui/ui/blob/main/apps/v4/registry/bases/radix/ui/combobox.tsx) 第一行就是 `import { Combobox as ComboboxPrimitive } from "@base-ui/react"`，因为 Radix 本身没有 Combobox。 |
| Command | `cmdk`，外层套本底层的 Dialog | 同左 | `cmdk@1.1.1`（2025-08 之后没有新版本）依赖 `@radix-ui/react-dialog`，所以 Base UI 底层用 Command 时也会引入一个 Radix 包。 |
| Resizable | `react-resizable-panels` | 同左 | 与底层无关。 |
| Sonner | `sonner`、`next-themes` | 同左 | 与底层无关。 |
| Chart | `recharts@3.8.0` | 同左 | 与底层无关。 |
| Drawer | `@base-ui/react`（Base UI 自带 Drawer） | `vaul` | 不在票面清单中，供参考。 |
| Toast | `@base-ui/react` | 无 | 地图已约定使用 Sonner，这一行只说明组件可能只在 Base UI 上提供。 |

两个库自身导出的原语（`@base-ui/react@1.8.0` 与 `radix-ui@1.6.7` 安装后的目录与 `index.d.ts`）：

- Base UI 有而 Radix 没有的：`autocomplete`、`combobox`、`drawer`、`number-field`、`otp-field`、`meter`。两者都有 Toast，但 shadcn 只封装了 Base UI 的版本。
- Radix 有、Base UI 没有同名独立原语的：`AspectRatio`、`HoverCard`（Base UI 的对应组件是 `preview-card`）、`Label`（Base UI 用 `Field.Label`）、`AccessibleIcon`、`VisuallyHidden`。shadcn 的 Base UI 底层同样提供 aspect-ratio、hover-card、label 组件（见上文目录）。

## 4. 兼容性、焦点、portal 与层叠

### 4.1 React 19 / Tailwind v4 / Vite 8

- `@base-ui/react@1.8.0` 的 peerDependencies：`react` 与 `react-dom` 为 `^17 || ^18 || ^19`，直接依赖为 `@floating-ui/react-dom`、`@floating-ui/utils`、`@base-ui/utils`、`use-sync-external-store`。—— [npm @base-ui/react](https://www.npmjs.com/package/@base-ui/react)
- `radix-ui@1.6.7` 的 peerDependencies：`react` 为 `^16.8 || ^17.0 || ^18.0 || ^19.0 || ^19.0.0-rc`。—— [npm radix-ui](https://www.npmjs.com/package/radix-ui)
- 本机用 Vite 8.2.1 + `@vitejs/plugin-react` 6.0.5 + React 19.2.8 构建 §5 的各个入口，全部成功。
- Base UI 依赖 `@floating-ui/react-dom`，并在 `@base-ui/react/floating-ui-react` 内置了一份改造过的 Floating UI React 层，与本项目 `Popover.tsx` 使用的定位模型（`flip`、`shift`、`size`、`autoUpdate`）属于同一家族。

### 4.2 焦点管理与键盘

- Base UI：组件遵循 WAI-ARIA Authoring Practices，自动处理 ARIA 属性、键盘导航和焦点管理；焦点的视觉样式由开发者通过 `:focus-visible` 负责。—— [Base UI Accessibility](https://base-ui.com/react/overview/accessibility)
- Base UI 的 Dialog Popup 通过 `initialFocus` / `finalFocus` 控制打开和关闭时的焦点。两者都接受 `boolean`、ref，或一个接收交互类型（`mouse` / `touch` / `pen` / `keyboard`）的函数。默认打开时聚焦第一个可 Tab 的元素，关闭时把焦点还给触发器。—— [Base UI Dialog](https://base-ui.com/react/components/dialog)
- Base UI 的 Popover 与 Dialog 的 `modal` 都接受 `boolean | 'trap-focus'`。`true` 表示锁定滚动并屏蔽外部指针交互；`'trap-focus'` 只捕获焦点，不锁滚动、不屏蔽外部。Popover 默认 `false`，Dialog 默认 `true`。Popover 设为 `true` 时，Popup 内必须有一个 `Popover.Close`（可以是 `sr-only`）。—— [Base UI Popover](https://base-ui.com/react/components/popover)、[`migrate-radix-to-base/overlays.md`](https://github.com/shadcn-ui/ui/blob/main/skills/migrate-radix-to-base/overlays.md)
- Radix 的 Dialog 用 `onOpenAutoFocus` / `onCloseAutoFocus` 管理焦点，用 `onEscapeKeyDown`、`onPointerDownOutside`、`onInteractOutside` 分别拦截不同的关闭方式。—— [Radix Dialog](https://www.radix-ui.com/primitives/docs/components/dialog)
- Base UI 把这些回调合并进 `onOpenChange(open, eventDetails)`：`eventDetails.reason` 取值包括 `'escape-key'`、`'outside-press'`、`'focus-out'`、`'trigger-press'` 等，调用 `eventDetails.cancel()` 可以阻止关闭。—— [`overlays.md`](https://github.com/shadcn-ui/ui/blob/main/skills/migrate-radix-to-base/overlays.md)
- 已知的默认行为差异：Radix 的 AlertDialog 打开时默认聚焦「取消」按钮，Base UI 默认聚焦第一个可 Tab 元素。要保持「取消」优先，需要传 `initialFocus={cancelRef}`。—— 同上

对本项目而言，两者都能取代自研的 `useFocusTrap` 和 `useEscapeClose`。Base UI 的 `reason` + `cancel()` 只需要一个入口，适合在新手引导期间统一拦截弹层关闭。

### 4.3 portal 与层叠顺序

- 两者默认都 portal 到 `document.body`，都支持 `container`。Base UI 的 `container` 还接受 `ShadowRoot` 和 RefObject。Base UI 的 Portal 会额外渲染一个 `<div>` 包裹层，Radix 不会。—— [Base UI Dialog](https://base-ui.com/react/components/dialog)、[Radix Alert Dialog](https://www.radix-ui.com/primitives/docs/components/alert-dialog)、[`overlays.md`](https://github.com/shadcn-ui/ui/blob/main/skills/migrate-radix-to-base/overlays.md)
- Base UI 要求在应用根元素上设置 `isolation: isolate`，建立独立的层叠上下文，防止页面内的局部 z-index 压过 portal 出去的弹层。—— [Base UI Quick start](https://base-ui.com/react/overview/quick-start)。本项目的根元素是 `index.html` 中的 `#app-root`，目前没有设置 `isolation`。
- shadcn 的 Base UI 版组件在 Positioner 上写 `isolate z-50`，在 Overlay 上写 `fixed inset-0 isolate z-50`。—— [`bases/base/ui/popover.tsx`](https://github.com/shadcn-ui/ui/blob/main/apps/v4/registry/bases/base/ui/popover.tsx)、[`bases/base/ui/dialog.tsx`](https://github.com/shadcn-ui/ui/blob/main/apps/v4/registry/bases/base/ui/dialog.tsx)
- 模态时屏蔽外部指针的方式不同：
  - Radix 的 `DismissableLayer` 在 `disableOutsidePointerEvents` 为真时写入 `ownerDocument.body.style.pointerEvents = 'none'`，卸载时再还原；Dialog 还通过 `aria-hidden` 包的 `hideOthers` 和 `react-remove-scroll` 隐藏兄弟节点、锁定滚动。—— [`dismissable-layer.tsx`](https://github.com/radix-ui/primitives/blob/main/packages/react/dismissable-layer/src/dismissable-layer.tsx)、[`dialog.tsx`](https://github.com/radix-ui/primitives/blob/main/packages/react/dialog/src/dialog.tsx)
  - 这条路径有过「关闭后 body 仍是 `pointer-events: none`」的缺陷报告：[#1241](https://github.com/radix-ui/primitives/issues/1241)（2022 年关闭）、[#3645](https://github.com/radix-ui/primitives/issues/3645)（2025-08 提出，2026-06-01 关闭）。
  - Base UI 用一个 `position: fixed; inset: 0` 的内部 backdrop（`InternalBackdrop`）拦截外部指针，不修改 `body` 的样式；兄弟节点通过改造自 `aria-hidden` 的 `markOthers` 设置 `aria-hidden`。—— [`InternalBackdrop.tsx`](https://github.com/mui/base-ui/blob/master/packages/react/src/utils/InternalBackdrop.tsx)、[`markOthers.ts`](https://github.com/mui/base-ui/blob/master/packages/react/src/floating-ui-react/utils/markOthers.ts)

### 4.4 与 driver.js 引导层共存

driver.js 1.8.0 的事实（来自发布包 `dist/driver.css` 与 `dist/driver.js.mjs`，[npm driver.js](https://www.npmjs.com/package/driver.js)）：

- 遮罩 SVG 的内联样式是 `zIndex: 10000`。
- `.driver-popover` 的 `z-index` 是 `1000000000`。
- 引导激活时，`.driver-active *{pointer-events:none}` 屏蔽所有元素，只有 `.driver-active-element` 及其后代、`.driver-popover` 及其后代恢复为 `pointer-events:auto`。

由此得出的结论：

1. **层叠关系不受底层影响。** shadcn 弹层统一使用 `z-50`，driver.js 始终在最上层。地图约定的 z-index token 只需要把「引导层」定义在 Toast 之上，并与 driver.js 的这两个数值对齐。
2. **portal 出去的弹层在引导期间不可点击。** 无论选哪个底层，弹层都是 `body` 的子节点，不在 `.driver-active-element` 之内。现有的 `interactive` 步只点击导航锚点、不打开弹层，所以目前不受影响。如果将来某一步需要在弹层内操作，需要通过 `container` 把弹层 portal 到目标元素内部，或者为该弹层单独补一条 `pointer-events:auto` 规则。
3. **`tour.ts` 的 `inert` 快照方案继续有效。** 两个底层的弹层都是 `body` 的直接子节点（Base UI 多一层包裹 `<div>`），与现在的 `ModalShell` 位置相同。
4. **Base UI 的 `onOpenChange` 带 `reason`。** 引导期间如果需要阻止弹层被外部点击或 Esc 关闭，可以在 shadcn 包装层统一判断 `reason` 并调用 `cancel()`，不必像 Radix 那样逐个组件接入多个 `on*Outside` 回调。
5. **待原型验证：** 引导启动时如果已有一个模态 Dialog 打开，两个底层的焦点陷阱都可能与 driver.js 的 Tab 焦点陷阱和 `inert` 产生交互，这一点没有找到一手资料可以确认。建议在原型票中实测：先打开一个对话框，再启动引导，然后按 Tab 和 Esc。

## 5. 包体积（本机实测）

方法：在 `/tmp` 下新建一个独立的 Vite 8.2.1 项目（`@vitejs/plugin-react` 6.0.5、React 19.2.8），每个入口只渲染最小组合，生产构建合并为单个 chunk，再用 `gzip -9` 统计大小。测量只覆盖原语本身，不包含 shadcn 包装层和样式（两条路线的包装层大致相同）。

| 入口 | 内容 | gzip（字节） | 比 React 基线多出 |
|---|---|---:|---:|
| baseline | 只有 `react-dom/client` | 58,815 | — |
| radix | Dialog、AlertDialog、Popover、DropdownMenu、Select、Tooltip、Tabs、ScrollArea | 99,123 | +40.3 KB |
| base | 上面 8 个原语的 Base UI 对应组件 | 124,278 | +65.5 KB |
| combobox | 只有 Base UI Combobox | 100,212 | +41.4 KB |
| **radix + combobox** | Radix 路线的实际组合（shadcn 的 Radix 版 Combobox 使用 Base UI） | **132,921** | **+74.1 KB** |
| **base + combobox** | Base UI 路线的实际组合 | **134,875** | **+76.1 KB** |
| current | 现状中的 headlessui Combobox 加 floating-ui Popover | 104,627 | +45.8 KB |

解读：

- 只看 8 个基础原语，Radix 小约 25 KB。但本项目必须保留 Combobox（`ModelCombobox`），Radix 路线也要引入 Base UI，最终两条路线只差约 2 KB。
- 迁移完成后，`@headlessui/react` 和 `@floating-ui/react` 可以移除，抵消一部分新增体积。
- 两条路线使用 Command 时都会额外引入 `cmdk` 和 `@radix-ui/react-dialog`。

## 6. 维护活跃度

| 指标 | Base UI（`mui/base-ui`） | Radix（`radix-ui/primitives`） |
|---|---|---|
| 最新稳定版 | 1.8.0，2026-09-04 | `radix-ui` 1.6.7，2026-07-24；之后只有 1.7.0 的 RC 版本，最后一个发布于 2026-07-31 |
| 稳定版节奏 | 1.0.0（2025-12-11）、1.1.0（01-15）、1.2.0（02-12）、1.3.0（03-12）、1.4.0（04-13）、1.5.0（05-19）、1.6.0（06-18）、1.7.0（08-04）、1.8.0（09-04） | — |
| 仓库最后一次 push | 2026-10-02 | 2026-08-08；最后一次 commit 为 2026-07-31 |
| 2026-06-01 以来新开 / 关闭的 issue | 180 / 176 | 46 / 472（其中 74 个以 not planned 关闭，属于集中清理） |
| 2026-06-01 以来合并的 PR | 599 | 101 |
| 周下载量（2026-09-24 至 09-30） | `@base-ui/react` 18,367,942 | `radix-ui` 17,824,695；`@radix-ui/react-dialog` 88,118,452 |
| Stars | 11k | 19k |

来源：[npm @base-ui/react](https://www.npmjs.com/package/@base-ui/react)、[npm radix-ui](https://www.npmjs.com/package/radix-ui)、[Base UI releases](https://github.com/mui/base-ui/releases)、[Radix commits](https://github.com/radix-ui/primitives/commits/main)、npm downloads API、GitHub Search API。

Radix 的独立包（`@radix-ui/react-dialog` 等）下载量仍远高于 Base UI，存量生态更大；但 Base UI 的单一入口包已与 `radix-ui` 单一入口包持平，而且发版与合并频率高出数倍。

## 7. style 预设

shadcn 的 style 不只是主题：它会改写组件代码中的几何形状、间距和结构。—— [changelog 2025-12](https://ui.shadcn.com/docs/changelog/2025-12-shadcn-create)

下表的关键工具类摘自各 style 的源 CSS（[`apps/v4/registry/styles/style-<name>.css`](https://github.com/shadcn-ui/ui/tree/main/apps/v4/registry/styles)）。默认搭配来自 [`registry/config.ts`](https://github.com/shadcn-ui/ui/blob/main/apps/v4/registry/config.ts)，图标和字体都可以在 create 中单独更换。

| style | 官方描述 | 默认图标 / 字体 | 按钮默认高度 / 字号 | 输入框 | 下拉项 | 对话框内边距 | 圆角倾向 |
|---|---|---|---|---|---|---|---|
| Vega | classic shadcn/ui look | Lucide / Inter | `h-9` / `text-sm` | `h-9` | `px-2 py-1.5 text-sm` | `p-6 gap-6` | `rounded-md` |
| **Nova** | Reduced padding and margins for compact layouts | Lucide / Geist | **`h-8` / `text-sm`** | `h-8` | `px-1.5 text-sm` | **`p-4 gap-4`** | `rounded-lg` |
| Mira | Compact. Made for dense interfaces | Hugeicons / Inter | `h-7` / **`text-xs`** | `h-7 text-sm` | `min-h-7 text-xs` | `p-4 gap-4` | `rounded-md` |
| Lyra | Boxy and sharp. Pairs well with mono fonts | Tabler / JetBrains Mono | `h-8` / **`text-xs`** | `h-8 text-xs` | `text-xs` | `p-4 gap-4` | `rounded-none` |
| Maia | Soft and rounded, with generous spacing | Hugeicons / Figtree | `h-9` / `text-sm` | `h-9` | `px-3 text-sm` | `p-6 gap-6` | `rounded-4xl` |
| Luma | Rounded geometry. Soft elevation. Breathable layouts | Lucide / Inter | `h-9` / `text-sm` | `h-9` | `px-3 text-sm` | `p-6 gap-6` | `rounded-4xl` |
| Rhea | A more compact Luma… focused product interfaces | Lucide / Inter | `h-8` / `text-sm` | `h-8` | `min-h-7 py-1.5 text-sm` | `p-6 gap-6` | `rounded-2xl` |
| Sera | Minimal. Editorial. Typographic. Uppercase | Lucide / Noto Sans + Playfair Display | `h-10` / `text-xs uppercase` | `h-10 px-0` | `text-xs uppercase` | `p-6 gap-6` | `rounded-none` |

对照地图 Notes 中的通用标准（最小字号 11px，正文不小于 13px，点击目标不小于 24px），并参考现状：组件中大量使用 `text-[10px]` 到 `text-[12.5px]` 的硬编码字号。

- **Nova 最合适**：控件高 32px，密度比 Vega 高；正文 `text-sm`（14px）满足 13px 下限；`rounded-lg` 的几何形状中性，便于保持现有暗色风格。默认图标是 Lucide，与现有的 `lucide-react` 一致。
- **Rhea 是备选**：密度与 Nova 相当，但圆角是 `rounded-2xl`，卡片带阴影，对话框内边距为 `p-6`，视觉上偏柔和。如果视觉方向票倾向于更柔和的风格，可以改选 Rhea。
- **Mira 与 Lyra 排除**：两者的按钮、下拉项、卡片和 Select 正文都是 `text-xs`（12px），低于 13px 下限。要满足标准就得在所有组件里覆盖字号，等于放弃预设本身。Lyra 的 `rounded-none` 与等宽字体搭配也偏离现有风格。
- **Vega、Maia、Luma、Sera 排除**：Vega 的控件是 `h-9`、对话框 `p-6`，密度较低；Maia 和 Luma 间距宽松，属于营销或消费类倾向；Sera 是编辑排版风格，全大写、`h-10`。

采用任何预设都需要做的本地化调整（与底层无关）：

- 预设中的弹层动画使用 `zoom-in-95`、`slide-in-from-*-2`，按钮使用 `active:not-aria-[haspopup]:translate-y-px`（见各 style CSS 中的 `.cn-dialog-content`、`.cn-popover-content`、`.cn-button`）。地图约定去掉位移、缩放和旋转，只保留不超过 200ms 的透明度过渡，因此初始化后要删掉这些类。
- 字体按地图约定自托管，可以继续使用现有的 Inter 与 JetBrains Mono，不必采用 Nova 默认的 Geist。

## 8. 从现有原语迁移的成本

下表的引用数是在 `frontend/src` 中 grep 组件名得到的文件数，包含定义文件自身，不含测试，只用于估算量级。

| 现有实现 | 目标（Base UI 底层） | 约引用文件数 | 说明 |
|---|---|---:|---|
| `ModalShell`、`GlassModal`、`useFocusTrap`、`useEscapeClose`、body 滚动锁 | `Dialog`、`Sheet` | 34 | 焦点陷阱、Esc 和滚动锁都由底层接管，自研 hook 可以删除。必须提供 accessible name（`labelledBy` 或 `ariaLabel`），对应 `DialogTitle`。 |
| `ConfirmDialog` | `AlertDialog` | 48（与下两行合计） | 需要用 `initialFocus` 指定默认聚焦按钮。地图约定不可逆删除使用 AlertDialog。 |
| `DropdownPill`、`ActionMenu` | `DropdownMenu`、`Select` | 同上 | 按语义拆分：动作列表用 Menu，取值用 Select。 |
| `Popover`、`GlassPopover`（`@floating-ui/react`） | `Popover` | 18 | 定位模型同属 Floating UI，`flip`、`shift`、`size` 对应 Positioner 的 `collisionAvoidance` 等属性。先处理 `Popover.tsx` 与 `popover.tsx` 的大小写冲突（见风险 4）。 |
| `ModelCombobox`（`@headlessui/react`） | `Combobox`，或 Base UI 的 `Autocomplete` | 少量 | 现有实现允许输入自由文本，Base UI 的 Autocomplete 更贴近这种语义；shadcn 目前只封装了 Combobox，Autocomplete 需要自己包装。迁移后可以移除 `@headlessui/react`。 |
| `ProviderModelSelect`（`@floating-ui/react`） | `Select` 或 `Combobox` | 少量 | 迁移后可以移除 `@floating-ui/react`。 |
| `ImageLightbox`、`CreateProjectModal`、`VersionTimeMachine` 中的 `createPortal` | `Dialog` | 3 | — |
| `utils/ui-layers.ts` | z-index token | — | 按地图约定统一定义，覆盖弹层、引导层和 Toast。 |

选 Base UI 还是 Radix，对上面的迁移工作量影响不大。真正的差别在于后续成本：选 Radix 后，新组件可能只在 Base UI 上发布（例如 Toast），Combobox 也会混用 Base UI；将来如果想统一到 Base UI，还要再走一次官方的 Radix → Base 迁移。选 Base UI 则只需要迁移一次。

API 风格方面，Base UI 用 `render` 属性取代 Radix 的 `asChild`；弹出类组件拆为 `Portal > Positioner > Popup` 三层；状态属性使用 `data-open` / `data-closed` 与 `data-starting-style` / `data-ending-style`。—— [`migrate-radix-to-base`](https://github.com/shadcn-ui/ui/blob/main/skills/migrate-radix-to-base/SKILL.md)。本项目从自研原语直接迁到 Base UI，不存在 `asChild` 的存量代码。

## 9. 推荐与理由

**推荐：Base UI 底层 + Nova 预设，初始化命令为 `npx shadcn@latest init --base base`，在 create 中选择 Nova，`components.json` 中的 `style` 为 `base-nova`。**

1. **与上游方向一致。** 自 2026-07-02 起，Base UI 是 shadcn 的默认底层和官方推荐的新项目底层。新组件可能只在 Base UI 上提供（Toast 已经是这样），官方也只提供 Radix → Base 方向的迁移工具。
2. **Radix 路线无法避免 Base UI。** shadcn 的 Radix 版 Combobox 直接依赖 `@base-ui/react`，而本项目必须保留 Combobox，选 Radix 会形成两套底层并存，焦点、portal 和关闭语义各有一套。
3. **体积没有差别。** 加上 Combobox 后，两条路线相差约 2 KB（gzip）。
4. **维护更活跃。** Base UI 几乎每月发布一个 minor 版本，同期合并 PR 约为 Radix 的 6 倍；Radix 的 1.7.0 自 2026-07 起一直停在 RC。
5. **契合现有技术栈与引导层。** Base UI 基于本项目已在使用的 Floating UI；模态时不修改 `body` 样式；`onOpenChange` 的 `reason` + `cancel()` 只需要一个入口，便于在新手引导期间统一拦截弹层关闭。
6. **Nova 满足地图的密度与字号约束。** 控件 32px、正文 14px、对话框 `p-4`，是八个预设中唯一同时满足「紧凑」与「正文不小于 13px」的一个。

不推荐 React Aria 底层：它在 2026-07 才加入 shadcn；本项目没有使用 React Aria Components 的存量代码；状态选择器与另外两个底层不同，社区示例和第三方 registry 主要针对 Base UI 和 Radix。

## 10. 风险与缓解

1. **Base UI 稳定版历史较短。** 1.0.0 发布于 2025-12-11，至今约 10 个月，第三方示例和 registry 的存量仍以 Radix 为主。缓解：shadcn 声称从远程 registry 拉取组件时会自动转换到当前底层；原型阶段需要实测本项目所需的每个组件。
2. **需要在根元素上设置 `isolation: isolate`。** 这是 Base UI 的要求，要加在 `#app-root` 上，并在定义 z-index token 时一起验证。
3. **默认行为差异。** Popover 的 `modal={true}` 要求 Popup 内有一个 `Popover.Close`；AlertDialog 默认聚焦第一个可 Tab 元素；Portal 会多渲染一层 `<div>`。这些需要在 shadcn 包装层统一处理，并写进组件规范。
4. **`Popover.tsx` 与 `popover.tsx` 的大小写冲突。** 在 macOS 上，`git mv` 改大小写需要经过一个中间文件名。按地图约定先把业务组件迁出 `components/ui/`，或者先给自研的 `Popover` 改名，再执行 `shadcn add popover`。
5. **Command 仍会引入 Radix 包。** `cmdk` 依赖 `@radix-ui/react-dialog`。如果不需要 Command（命令面板），可以不安装；需要时接受这个传递依赖。
6. **预设自带的动效违反地图约定。** 预设中的 `zoom` / `slide` / `translate` 类需要在初始化后统一删除，否则违反「减少动态效果」的约定。
7. **待验证：模态焦点陷阱与 driver.js 的交互。** 已在 §4.4 第 5 条列出，留给原型票实测。
