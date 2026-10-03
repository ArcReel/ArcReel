---
paths:
  - "frontend/src/**"
---

# 前端 UI

## 资源占用与入队

### 随资源占用禁用的控件，在打开时和提交时校验占用态，并同步禁用兄弟控件

编辑、重生成、上传、入库、版本恢复这类随资源占用而禁用的控件，新增或改动时完成三项检查：

1. 弹窗或面板打开时校验当前占用态。
2. 提交时用 `frontend/src/stores/tasks-store.ts` 的 `isResourceBusy(kind, projectName, resourceId)` 复核最新占用态。打开之后占用态可能已经变化，只在打开时校验会留下竞态窗口。
3. 同一资源卡片上的兄弟控件同步绑定占用态。

占用态不只来自队列任务：卡片自身发出的在途写请求（保存中、上传中、改名中）由组件本地 state 承载，`isResourceBusy` 读不到它们，本地 state 同样参与这三项检查。

### 新增入队类 API 方法时，把方法名登记进 `frontend/eslint.config.js` 的 `RESTRICT_ENQUEUE`

生成类入队统一经 `frontend/src/actions/` 的动作函数，由它们封装 API 调用、乐观标记占用与去重提示；组件直接调用入队类 API 会漏掉占用标记，用户可以对同一资源重复入队。ESLint 的 `no-restricted-syntax` 只按 `RESTRICT_ENQUEUE` 中登记的方法名拦截直接调用，未登记的新方法不受拦截。

## 首次使用引导

### 改动带 `data-onboarding` 的元素，引导链路随之核对

引导高亮点靠元素上的 `data-onboarding` 属性定位：锚点名登记在 `frontend/src/onboarding/anchors.ts`，步骤大纲在 `steps.ts`，文案在 `frontend/src/i18n/*/onboarding.ts`。锚点名由 typecheck 校验，`anchors.test.tsx` 只校验已登记锚点在挂载场景下存在。下面三项没有任何编译期或测试约束，出错时引导只在运行期降级为居中气泡，或把用户指向界面上不存在的名称：

- **属性仍在，且元素仍无条件渲染。** 挂载点落进条件分支（空态才渲染、数据就绪才渲染、某个 tab 激活才渲染），该步在常见路径上就找不到锚点：引导不中止，等待 `ANCHOR_WAIT_MS` 后降级为居中气泡，只在 console 留一条 warn。需要迁移时，移到同一屏内无条件挂载的容器上，并同步 `anchors.ts` 中该条目的说明。
- **步骤文案描述的仍是这个元素。** 元素承载的功能、字段或按钮可用条件变了，对应步骤正文各语言一并修改。
- **文案里的入口名与界面标签一致。** 步骤提到的侧栏项、tab、按钮一律用用户在界面上看到的标签；标签改名时同步各语言文案。

删除带锚点的元素时整条链一起清理：`anchors.ts` 的条目、`steps.ts` 的步骤、各语言文案 key、`steps.test.ts` 与 `anchors.test.tsx` 的断言。

## 原语与 shadcn 生成文件

### `components/ui/` 只放 shadcn 原语，业务组件放进所属业务目录

`frontend/src/components/ui/` 只存放用 shadcn CLI 安装的原语（`components.json` 的 `style` 为 `base-nova`，底层是 Base UI）。只要组件知道业务类型、调用 API 或读写 store，就放进使用它的业务目录；多个区域共用的放进 `components/shared/`。knip 对 `src/components/ui/*.tsx` 的未使用导出豁免，就是按「这里全是成套导出的原语」设计的；业务组件混进来后，它真正未使用的导出也会被一起放过。

`components/legacy/` 存放等待替换的自研旧原语（`GlassModal`、`ConfirmDialog`、`FloatingPopover`、旧按钮等），干净交付时连同测试一起删除。已重做区域不再引用 `components/legacy/`；其他位置新写弹层、菜单、按钮时直接使用 `components/ui/` 的原语。

### 原语按需安装：首次用到的改动执行 `pnpm exec shadcn add`，不使用 `--overwrite`

哪个改动首次用到某个原语，就由它执行 `pnpm exec shadcn add <组件>`，并一起提交 CLI 引入的依赖。不预装暂时用不到的原语：未使用的原语文件和依赖仍由 knip 报告。例外是需要统一改造的成套原语（如各类弹层）：可以先于使用方安装并改好，未被引用的文件在 `knip.jsonc` 的 `ignore` 中临时登记，注释写明由首个使用方删除该条目。CLI 统一通过 `pnpm exec` 调用，以使用 `package.json` 锁定的 `shadcn` 版本；组件源码来自线上 registry，锁定版本不能固定组件内容，因此对已安装组件不使用 `--overwrite`，以免覆盖本地修改。

### 生成文件按自有代码处理，有意改动在改动处写一行注释说明原因

生成文件照常接受全部 lint 规则，不对 `components/ui/` 整体放宽。lint 报出真问题时修改代码；确认是误报的，用行内 `eslint-disable-next-line <规则> -- 理由` 豁免。为满足本仓库规范而改变行为或样式时（例如给 Dialog 增加限高的 Body、把遮罩改为不透明），在改动处写一行注释说明原因，便于合并上游时区分本地改动与上游代码。单纯的 lint 修复不加注释。

### 照搬 Radix 版示例前，逐个实测 Base UI 的行为差异

shadcn 文档与社区示例多数基于 Radix，Base UI 版本有几处差异，类型检查发现不了：

- `DropdownMenuLabel` 必须放在 `DropdownMenuGroup` 内，否则运行时整页崩溃。本仓库的包装层已不导出 `DropdownMenuLabel`，分组标题通过 `DropdownMenuGroup` 的 `label` 属性传入。
- `modal` 模式的 Popover，其 Popup 内必须有 Close。
- Portal 会多渲染一层 `<div>`，依赖 DOM 层级的选择器和样式要随之调整。
- Combobox、Select 的弹层在点击后异步打开，测试点击触发器后用 `findByRole("listbox")` 或 `findByRole("option")` 等待。Combobox 的搜索框放在弹层内，触发按钮本身带 `role="combobox"`。
- Checkbox、Radio 渲染为带角色的 `span`，禁用态体现为 `aria-disabled="true"`，`toBeDisabled()` 断言不成立。
- Collapsible 收起时卸载面板内容，测试先点开触发按钮，再查询面板里的元素，展开状态看触发按钮的 `aria-expanded`。
- Slider 的滑块在 jsdom 中不可见，按角色查不到；需要角色测试的单值滑杆保留原生 `<input type="range">`。

这些约束在包装层或调用处遵守。上表之外的组件第一次使用时，在浏览器里实测键盘、焦点和关闭行为后再推广。

### 原语只接受布局类名，折叠触发器经 `render` 渲染为 `Button`

`@shadcn/lint` 的 `no-restyle` 只放行布局类名：间距、边框、配色写在原语外的一层普通元素上，原语自身只用 margin 与它隔开。Collapsible 自身没有样式，触发器写成 `<CollapsibleTrigger render={<Button variant="ghost" size="sm" />}>`，展开图标用 `group-aria-expanded/button:rotate-90` 旋转。

### 原语出现缺陷或 major 升级时，用 `add --diff` 对照上游后手动合并

Dependabot 只升级 npm 包，不会重新生成 `components/ui/*.tsx`。`@base-ui/react` 与 `shadcn` 的 minor、patch 升级归入 `shadcn-stack` 分组；major 升级单独开 PR，在同一个 PR 中对已安装组件逐个运行 `pnpm exec shadcn add <组件> --diff`，手动合并上游改动并保留本地注释说明的改动。遇到原语缺陷时，先用 `add --diff` 确认上游是否已经修复。不定期全量跟进。

### 多行输入框使用 `components/ui/textarea` 的 `Textarea`，不手写测高

`Textarea` 用 CSS `field-sizing: content` 随内容撑高；浏览器不支持时回退到 JS 测量，在值变化、输入和宽度变化时重算。默认高度上限是 `max-h-[40cqh]`，即最近的尺寸容器高度的 40%；没有尺寸容器时按视口高度计算。超出上限后在框内滚动。需要其他上限时，在调用处用 `max-h-*` 覆盖。不要在业务组件里读写 `scrollHeight` 或在 `onInput` 里设置高度。

### 带候选的文本输入：只能选候选项用 Combobox，允许填写候选之外的值用 Base UI Autocomplete

Base UI 的 Combobox 只接受候选项，不能提交候选之外的文字；模型 ID 这类网关列表常常不全、必须允许自由填写的字段，用 `@base-ui/react/autocomplete`。shadcn 的 base-nova registry 没有 Autocomplete，参考 `components/agent/ModelIdField` 的写法：输入框用 `components/ui/input-group`，弹层表面沿用 `bg-popover`、`shadow-overlay` 与 `z-overlay`。

### 区域重做完成后，把目录登记进 `eslint.config.js` 的 `REWORKED_FILES`

`@shadcn/lint` 的样式规则与滚动、响应式守卫（禁止视口高度、原语之外的 `fixed inset-0` 与读写 `scrollHeight`、业务组件的视口断点前缀）只对 `REWORKED_FILES` 中的 glob 生效。重做某个区域的改动把该区域的目录加进列表，并让这些文件零报告；交付结束时，列表替换为 `src/**`。

需要放宽时，在 PR 描述中逐条列出，由审查判断：

- 新增的组件变体、`@shadcn/lint` 的 `allow` 或 `contracts` 条目。
- 登记进 `VIEWPORT_BREAKPOINT_ALLOWLIST` 的文件。只有外壳切换标准档与紧凑档、弹层宽度等确需按视口判断的场景可以登记；外壳内的组件一律使用容器查询。

## 颜色与文字层级

### 颜色只用 `index.css` 的语义 token，深浅用透明度修饰表达

颜色 token 沿用 shadcn 命名（`primary`、`destructive`、`border`、`input`、`muted-foreground` 等），另有状态色 `good`、`warn` 和文字中间档 `subtle-foreground`。浅底、描边、选中态写成基色加透明度修饰（`bg-primary/15`、`border-border/50`），不为某种深浅另设变体 token；变体 token 会让同一语义出现多个近似色，旧色板中的变体色已按这一原则删除。危险操作用 `destructive`，琥珀色 `warn` 只表示警告与过期。内联样式和 CSS 引用 `:root` 中的原始变量（`var(--primary)`），需要透明度时写 `color-mix(in oklab, var(--primary) 15%, transparent)`。

文字只分三档：`foreground`、`subtle-foreground`、`muted-foreground`。正文不在 `muted-foreground` 上再叠加透明度或 `opacity`：它在页面底色上的对比度是 5.7:1，再降低就达不到 WCAG AA 要求的 4.5:1。

旧 token 名已由 `frontend/scripts/token-codemod.ts` 一次改完。合并仍在使用旧 token 名的分支后，对这些文件重跑脚本，用法见脚本头部注释。

## 弹层与提示

### Dialog、Sheet、AlertDialog 按 Header、Body、Footer 组合，只有 Body 滚动

弹层内容由原语限高，不超过视口高度减去 2rem。`DialogHeader`、`DialogFooter` 固定在两端，`DialogBody`（Sheet、AlertDialog 中对应 `SheetBody`、`AlertDialogBody`）是唯一的滚动区。主操作放在 Footer，窗口再矮也不会被滚出视野。Body 预留滚动条槽位，内容变长出现滚动条时不会横向跳动。

- 宽度用 `size` 选择：Dialog 有 `sm`、`default`、`lg`、`xl`，AlertDialog 有 `sm`、`default`、`lg`。调用处不写 `max-w-*`。
- Body 自带内边距。内部的纵向间距写在 Body 里的一层 `flex flex-col gap-*` 包裹元素上，不写在 Body 上；后者会被 `@shadcn/lint` 的 `no-restyle` 报告。
- 每个弹层都要有 Title。没有可见标题时，给 Title 加 `sr-only`。
- 关闭按钮由 Content 的 `showCloseButton` 渲染；Footer 已有「关闭」按钮时，传 `showCloseButton={false}` 去掉右上角的那个。

### 不可逆操作用 AlertDialog 确认，可撤销操作直接执行并在提示里提供「撤销」

AlertDialog 打开时焦点落在「取消」上，误按 Enter 不会执行操作。确认按钮用 `variant="destructive"`；提交中禁用两个按钮，并在 `onOpenChange` 中忽略关闭请求，Esc 不会在请求未完成时关掉对话框。

移除、移动、隐藏这类可以恢复的操作不弹确认，执行后调用 `pushToast(text, tone, { action: { label: t("common:undo"), onClick } })`。点击「撤销」会执行回调并关闭这条提示。

### 全局提示统一用 `useAppStore` 的 `pushToast`

提示由 `ToastOverlay` 转交 `components/ui/toast` 的 Base UI 队列显示，位置在顶部居中，同时最多显示 3 条，5 秒后自动消失，指针悬停或键盘聚焦时暂停计时。错误提示由读屏立即播报。不要另建提示组件，也不要直接调用 `components/ui/toast` 的 `toast.add`。提示、工作区通知与持久告警的分流规则见 `frontend/src/stores/app-store.ts` 中 `pushToast` 的说明。

### Sheet、Popover、DropdownMenu 的本仓库用法

- **Sheet**：左右两侧默认宽度为 `w-md`。调整右侧宽度时写 `data-[side=right]:w-*`，直接写 `w-*` 会被原语里带 `data-[side=right]:` 前缀的默认宽度盖住。Sheet 同样按 Header、Body、Footer 组合。
- **Popover**：高度不超过触发点到视口边缘的可用空间（`max-h-(--available-height)`），内容超出时在 Popup 内滚动。
- **DropdownMenu**：分组标题传给 `DropdownMenuGroup` 的 `label`；删除等不可逆的菜单项用 `variant="destructive"`。

### 层级只用 z-index token，调用处不写 `z-*`

弹层（Dialog、Sheet、AlertDialog、Popover、菜单、Tooltip）使用 `z-overlay`，提示使用 `z-toast`，首次使用引导使用 `z-onboarding`，三者依次升高，原语已经自带。`#app-root` 是独立的层叠上下文（`isolation: isolate`），挂在 `body` 上的 Portal 总在应用之上，应用内部的 `z-*` 不必与弹层比较大小。

### 弹层表面不透明，不使用背景模糊

遮罩用 `bg-scrim`，弹层表面用 `bg-popover` 加 `shadow-overlay`。不加 `backdrop-blur-*`，也不把表面改成半透明：半透明表面的文字对比度会随背后的画面变化，背景模糊在大面积重绘时开销也大。

## 按钮

### 按钮使用 `components/ui/button` 的 `Button`，按操作语义选择变体

- `default`：区域内的主操作，同一区域最多一个。
- `outline`：次要操作，如「取消」「重新渲染」。
- `ghost`：工具栏按钮与图标按钮。
- `destructive`：删除、覆盖等不可逆操作。
- `secondary`、`link`：低强调操作与行内链接式操作。

尺寸用 `size`：`xs`、`sm`、`default`、`lg`，只有图标时用 `icon`、`icon-xs`、`icon-sm`、`icon-lg`。按钮内的图标加 `data-icon="inline-start"` 或 `data-icon="inline-end"`，不写尺寸 class。`Button` 没有 loading 属性；加载时禁用按钮，并把前置图标换成带 `animate-spin` 的 `Loader2`。

`components/legacy/` 的 `PrimaryButton`、`SecondaryButton`、`ModalCloseButton`，以及 `.arc-btn-primary`、`.arc-btn-secondary` 与 `ACCENT_BUTTON_STYLE`，已改为与 `Button` 外观一致的过渡封装，只供未重做的区域使用，新代码不再引用。

## 图表

### 图表使用 `components/ui/chart` 的 shadcn Chart（Recharts），并提供表格替代

图表外层包一层 `role="img"` 并以图表名称作 `aria-label`，紧随其后放一张 `sr-only` 的表格，列出同样的数据。系列颜色引用 `index.css` 的 token（如 `var(--primary)`、`var(--media-image)`），图例与提示里的色块用对应的 `bg-*` 类，不写十六进制色值。提示内容自己渲染，表面与弹层一致（`bg-popover`、`shadow-overlay`）。

## 动效

### 动效时长不超过 300ms，时长与缓动只用 token

时长用 `duration-fast`（150ms）和 `duration-base`（200ms），缓动用 `ease-emphasized`。弹层进出场使用原语自带的 tw-animate-css `animate-in`、`animate-out`。持续运行状态的指示点用 `animate-breath`。不写 `duration-300` 以上的动效。

### 减少动态效果由 `index.css` 的全局规则统一处理，组件不单独适配

开启「减少动态效果」时，全局规则把 tw-animate-css 的位移、缩放、旋转与模糊归零，过渡只保留透明度与颜色属性，其他 keyframes 动画（包括 `animate-spin`、`animate-breath`）直接停在终态。组件不需要再写 `motion-safe:` 或 `motion-reduce:`。新增带位移或缩放的动效时，在减少动态效果下确认它只剩淡入淡出。

## 页面外壳与滚动

### 页面级视图使用 `components/shared/page-shell` 的外壳，按内容选择容器档位

全局设置、项目设置、项目大厅与资产库由 `PageShell` 组合 `PageHeader`（56px 顶栏，「返回 | 标题 | 副标题」靠左，页面级动作靠右）和 `PageSidebar`（贴左的分组导航）。外壳是唯一按视口切换标准档与紧凑档的地方：以 `xl` 为界，侧栏 224 / 200px，内容内边距 32 / 24px。区段内部不再写视口断点前缀，内容列是名为 `page` 的尺寸容器，按 `@md/page:` 这类容器查询响应宽度。

`tier` 按内容选择，同一页面的不同区段可以不同：

- `constrained`（限宽）：内容列最大 760px，紧贴侧栏靠左，外壳主体滚动并提供内边距。用于表单与设置项。
- `full`（铺满）：不设宽度上限，其余与限宽相同。用于列表、网格与表格。
- `bleed`（全出血）：外壳主体不滚动、不加内边距，区段占满剩余高度自己分栏，每栏各自滚动。用于主从布局与编辑器。

区段不再自己写页面级的 `max-w-*`、`mx-auto` 或外层内边距，这些由档位提供。

### 滚动只发生在外壳指定的容器里，文档本身不滚动

外壳根节点是 `relative h-dvh overflow-hidden`，滚动只发生在侧栏、外壳主体（限宽与铺满档）和全出血区段的各栏。e2e 区域场景在全部验收视口上运行溢出探针，文档出现滚动，或内容被裁切且滚动不到，场景就会失败。新写或改动滚动区域时遵守四条：

- **滚动容器是定位元素。** 写 `overflow-y-auto` 的元素同时写 `relative`。`sr-only` 等绝对定位的子元素以最近的定位祖先为包含块；滚动容器不是定位元素时，这些子元素会按自己在滚动内容里的位置撑高外层，造成文档滚动。
- **高度沿 flex 链传下来。** 从外壳到滚动容器之间的每一层 flex 子项写 `min-h-0`（横向是 `min-w-0`），否则 flex 子项的最小高度等于内容高度，滚动容器不会出现滚动，而是被内容撑开。
- **不用视口高度定高。** 不写 `h-screen`、`max-h-screen`、`vh` 单位，也不用 `sticky` 加 `max-h-screen` 模拟独立滚动的栏。全出血区段的根节点写 `flex min-h-0 flex-1`，各栏写 `overflow-y-auto`。
- **主体滚动区预留滚动条槽位。** 外壳主体已写 `[scrollbar-gutter:stable]`，内容变长出现滚动条时不会横向跳动；全出血区段内会随内容出现滚动条的主栏同样写上。

### 保存栏经 `PageShellFooter` 放进外壳底行

限宽与铺满档在外壳主体下方有一行固定的底行，只覆盖内容区，不随主体滚动。区段用 `<PageShellFooter><SaveBar unit={unit} /></PageShellFooter>` 把保存栏渲染进去，底行内层与内容列同宽、同起点；区段没有渲染保存栏时底行不显示。全出血档没有这一行，保存栏放在区段详情栏的底部、滚动区之外。`SaveBar` 本身不带边框与背景，由所在的行提供。

### 全出血的主从布局使用 `components/shared/master-detail` 的二级栏与详情栏

供应商、调用端点与 Agent 记忆这类「列表 + 详情」区段，根节点写 `flex min-h-0 min-w-0 flex-1`，左侧放 `SecondaryRail`，右侧放 `DetailPane`：

- **`SecondaryRail`**：传 `groups`（每组 `{ id, label, items, action?, emptyText? }`）与 `activeId`。条目是 `{ id, label, description?, icon, href }`：第二行 `description` 写状态或数量，选中经 `href` 走路由，离开拦截因此覆盖切换。`action` 是组末尾的动作条目（如「添加自定义供应商」），不计入 Tab 上的数量。二级栏按 `@container/page` 的宽度切换形态：内容区不窄于 64rem 时是 264px 的两行条目，多组时顶部是 Tab，每次只列一组；更窄时收为 56px 的图标栏，悬停或聚焦显示名称，多组上下叠放。没有手动切换过时 Tab 跟随选中项所在的组。只有一组时不显示 Tab。
- **`DetailPane`**：分 `header`、正文与 `footer` 三段，只有正文滚动。设置类详情的 `SaveBar` 放进 `footer`，常驻底部。正文不带内边距，表单通常写 `max-w-190 px-6 py-6`，此时保存栏写 `max-w-178`，与表单列同宽、同起点。
- 选中项换了就整栏重建：给详情组件传 `key`，上一项的未保存修改、在途请求与加载状态不会带到下一项。

两种形态都在 DOM 里，靠容器查询只显示其中一种，被隐藏的一份不进入可访问树。jsdom 不计算样式，Vitest 中两份都可见：按条目查询时限定在 `getByRole("tabpanel")` 内，或用 `getAllBy*`。

### 「返回」使用 `useReturnTo`，设置页深链使用 `settingsSectionPath`

路由根部的 `useTrackReturnTo` 把最近停留的应用页面（含查询串）记在 `sessionStorage`。全局设置与资产库之间往来不改变返回目标，只做重定向的入口也不记录。页面的「返回」按钮调用 `useReturnTo()` 取得的回调，回到进入之前的页面；没有记录时回到项目大厅。入口按钮和深链不需要各自记录来源。`useReturnTo` 只用于顶层路由上的页面：嵌套路由里的 `navigate` 以嵌套路径为基准。

指向全局设置某个分区的链接用 `app-routes.ts` 的 `settingsSectionPath(section, params)` 生成，分区名由 `SettingsSection` 类型校验，分区改名时 typecheck 会报出所有旧链接。在嵌套路由里跳转时在前面加 `~`，例如 `` `~${settingsSectionPath("usage")}` ``。

供应商与调用端点之间的跳转用两个专用函数，不手拼查询参数：

- `providerSettingsPath(target)`：`{ preset: id }` 选中预置供应商（`provider=`）；`{ custom: id, model? }` 选中自定义供应商，带 `model` 时定位到这个模型（`custom=`、`model=`）；`{ newCustom: { endpoint?, baseUrl? } }` 打开预填的新建表单（`custom=new`、`endpoint=`、`base_url=`）。
- `endpointSettingsPath(endpointKey, { fromCustomProvider? })`：选中端点（`endpoint=`）。从自定义供应商跳来时传 `fromCustomProvider`，写成 `from=<供应商 id>`；端点页据此在顶部显示「返回『供应商名』」，返回地址是 `providerSettingsPath({ custom: from })`。

### 配置问题提示在问题所属分区，不做全局横幅

`config-status-store` 的每条问题带所属分区，`useSectionConfigIssues(section)` 取某个分区要提示的问题。全局设置的侧栏只在该分区的条目上显示警告标记，提示用 `settings/ConfigIssueNotice`：全出血分区由设置页放在分区顶部，限宽分区放在自己的页头说明之后。其他分区里与问题相关的空状态，用 `settingsSectionPath` 链接到所属分区，不重复列出问题。「内嵌 Agent 未配置」只在「ArcReel Agent」分区提示，不计入 `isComplete`，大厅与顶栏的红点不因它亮起。

## 横向溢出与截断

### 横向放不下的内容按类型处理：滚动加边缘渐隐、表格固定列宽、单行文字截断

- **可横向滚动的条目**（标签、缩略图带）：容器写 `overflow-x-auto scroll-fade-x`，被裁掉的一侧显示渐隐，提示还有内容。
- **表格**：用 `table-fixed` 和 `<colgroup>` 给状态、时间、操作这类短列固定宽度，短列内容加 `whitespace-nowrap`；剩余宽度留给主文字列，主文字列的单元格用 `TruncatedText`。使用 `components/ui/table` 时，`TableHead`、`TableCell` 不接受颜色、字体与内边距 class（`@shadcn/lint` 的 `no-restyle`），这些写在单元格内的包裹元素或 `TruncatedText` 的 `className` 上。
- **单行文字**（名称、路径、模型 ID）：用 `components/shared/TruncatedText`。被截断时可以用键盘聚焦，悬停或聚焦时显示全文；没有截断时不进入 Tab 顺序。放进 flex 或表格单元格时，父级需要允许收缩（`min-w-0`）。不用 `title` 属性代替，键盘用户看不到它。放在按钮等可聚焦元素里时传 `focusable={false}`，避免出现嵌套的可聚焦元素；全文由外层元素的可访问名称提供。
- **带编号的名称**（分镜号加集名）：编号作为不截断的前缀单独渲染，只截断后面的名称。

## 滚动条

### 滚动条始终可见，样式只在 `index.css` 中定义

滚动条宽 10px，可见滑块 6px，不自动隐藏。组件不写 `scrollbar-*` 或 `::-webkit-scrollbar` 样式：Chromium 121 起，元素一旦设置 `scrollbar-width` 或 `scrollbar-color` 就忽略 `::-webkit-scrollbar`，局部改写会让该元素退回浏览器默认样式。标准属性只通过 `@supports not selector(::-webkit-scrollbar)` 提供给 Firefox。

## 编辑单元与保存

### 可编辑内容接入 `useEditUnit`：字段进入未保存修改，动作立即执行

一个设置视图或一块画布内容是一个编辑单元（见 `CONTEXT.md`），用 `components/shared/edit-unit/useEditUnit` 持有已保存内容与未保存修改。字段（描述、提示词、时长、引用，设置表单里的开关与下拉）一律写进 `unit.setValue`，由单元统一保存或放弃；上传、生成、改名、删除、排序这类动作不进入未保存修改，立即执行。同一视图里回车即存、松手即存与手动保存混用时，创作者分不清哪些修改已经生效。

- 已保存内容经 `source` 传入，加载结果与之后的推送都从这里进来。不要在 effect 里把本地状态重置成服务端数据：没有未保存修改时 hook 直接采用新内容，有修改时保留修改，并在保存栏或提示条上标出「此内容已被 Agent 更新」。
- `save(value, savedValue)` 提交修改，返回保存后的内容，失败时抛错。错误显示在保存栏或提示条上；保存成功不弹提示。
- 设置表单用常驻的 `SaveBar`，放在表单滚动区之外的底部：限宽与铺满档经 `PageShellFooter` 放进外壳底行，全出血档放在详情栏底部。画布内容用 `UnsavedChangesBar`，放在所属内容下方。不再另设「编辑模式」开关。
- 有未保存修改时，生成按钮写「保存并生成」（`common:save_and_generate`），点击时调用 `unit.saveAndGenerate(generate, { confirm })`。`confirm` 是「重新生成会让下游失效」这类确认：取消时什么都不保存，保存失败时不生成。
- 界面文案与代码命名不单称「草稿」，这个词已指待修复草稿与可编辑草稿。

### 离开与切换交给离开拦截，不自建未保存确认框或 `beforeunload`

`AppRoutes` 根部的 `LeaveGuardProvider` 是唯一的离开拦截。它经 wouter 的 `aroundNav` 拦截全部应用内跳转（`navigate`、`Link`、`Redirect`），截住浏览器的前进与后退，并在关闭标签页或刷新时请求浏览器原生提示。对话框的三个按钮是「继续编辑 / 放弃修改 / 保存并离开」；保存失败时留在原处，错误由编辑单元自己显示。页面自建的确认框和 `beforeunload` 会与它重复弹出，或在它放行之后再拦一次。

- `useEditUnit` 挂载期间自动登记。自行管理表单状态的页面调用 `useLeaveGuard({ dirty, saving, save, discard })` 登记，`save` 必须如实返回是否保存成功：总是返回 `true` 会让保存失败的修改随跳转丢失。`saving` 如实反映保存请求在途：离开拦截据此等保存落定再判断，否则「放弃修改」会放走已经发出的保存，「保存并离开」会重复提交。
- 选中项记在 URL 里、经路由跳转切换的（设置分区、供应商），路由拦截已经覆盖。不经路由的切换（分镜、记忆文件、资产的上一个与下一个、关闭 Sheet）用 `useConfirmLeave()` 包住切换动作，被包住的动作要同步完成切换。第三个按钮需要别的文案时传 `saveLabel`，如切换分镜时传 `t("common:save_and_switch")`。
- 不会卸载编辑单元的跳转（如同一编辑单元的分页切换），在登记时用 `allowNavigation(to)` 放行。
- 页面跳转一律经 wouter，不直接调用 `window.history` 或改写 `window.location`：绕过 wouter 的跳转不经过拦截。
