---
paths:
  - "frontend/src/**"
---

# 前端

## 资源占用与入队

### 随资源占用禁用的控件，在打开时和提交时校验占用态，并同步禁用兄弟控件

编辑、重生成、上传、入库、版本恢复这类随资源占用而禁用的控件，新增或改动时完成三项检查：

1. 弹窗或面板打开时校验当前占用态。
2. 提交时用 `frontend/src/stores/tasks-store.ts` 的 `isResourceBusy(kind, projectName, resourceId)` 复核最新占用态。打开之后占用态可能已经变化，只在打开时校验会留下竞态窗口。
3. 同一资源卡片上的兄弟控件同步绑定占用态。

占用态不只来自队列任务：卡片自身发出的在途写请求（保存中、上传中、改名中）由组件本地 state 承载，`isResourceBusy` 读不到它们，本地 state 同样参与这三项检查。

确认框打开后占用发生变化时，一键破坏性确认（删除、合并、改名的确认按钮）实时禁用；输入表单保持可编辑，由提交时的复核说明原因。

## 首次使用引导

### 改动带 `data-onboarding` 的元素，引导链路随之核对

引导高亮点靠元素上的 `data-onboarding` 属性定位：锚点名登记在 `frontend/src/onboarding/anchors.ts`，步骤大纲在 `steps.ts`，文案在 `frontend/src/i18n/*/onboarding.ts`。锚点名由 typecheck 校验，`anchors.test.tsx` 只校验已登记锚点在挂载场景下存在。下面三项没有任何编译期或测试约束，出错时引导只在运行期降级为居中气泡，或把用户指向界面上不存在的名称：

- **属性仍在，且元素仍无条件渲染。** 挂载点落进条件分支（空态才渲染、数据就绪才渲染、某个 tab 激活才渲染），该步在常见路径上就找不到锚点：引导不中止，等待 `ANCHOR_WAIT_MS` 后降级为居中气泡，只在 console 留一条 warn。需要迁移时，移到同一屏内无条件挂载的容器上，并同步 `anchors.ts` 中该条目的说明。
- **步骤文案描述的仍是这个元素。** 元素承载的功能、字段或按钮可用条件变了，对应步骤正文各语言一并修改。
- **文案里的入口名与界面标签一致。** 步骤提到的侧栏项、tab、按钮一律用用户在界面上看到的标签；标签改名时同步各语言文案。

删除带锚点的元素时整条链一起清理：`anchors.ts` 的条目、`steps.ts` 的步骤、各语言文案 key、`steps.test.ts` 与 `anchors.test.tsx` 的断言。

## 原语与 shadcn 生成文件

### `components/ui/` 中偏离上游的有意改动，在改动处写一行注释说明原因

为满足本仓库规范而改变原语的行为或样式时（例如给 Dialog 增加限高的 Body、把遮罩改为不透明），在改动处写一行注释说明原因。原语升级时要用 `pnpm exec shadcn add <组件> --diff` 对照上游手动合并，没有注释的本地改动无法和上游代码区分，合并时容易被冲掉。这类注释解释的是偏离上游的原因，不按删除历史描述类注释的通用规则处理。

单纯的 lint 修复不加注释；确认是误报的，用行内 `eslint-disable-next-line <规则> -- 理由` 豁免。

### 代码类输入框用 `Input` 与 `Textarea` 的 `mono`，不手写等宽字体

`mono` 只管字体，`variant` 只管外观，两者可以同时使用。`InputGroupInput` 与 `InputGroupTextarea` 透传 `mono`。不要在输入框或它的外层元素上写 `font-mono`：外层的字体经继承进入输入框，`@shadcn/lint` 查不到，规范也就无从核对。

适用 `mono` 的输入框：

- JSON、代码与请求体编辑区：端点的 JSON 视图、请求体模板、导入时粘贴的定义、测试时粘贴的响应、Agent 记忆的 Markdown 原文。
- 模型 ID。
- 令牌：API Key、密钥这类凭据输入框。
- 请求路径与模板：端点的请求、查询、取件地址模板，请求头名称与内容，变量名，取值路径，状态值。

保持比例字体的输入框：

- 接口地址（URL），如供应商与端点的默认接口地址、代理地址。不需要拼写检查的值写 `spellCheck={false}`，不用换字体来表达。
- 名称、版本、提示词等正文。登录密码不是令牌，同样保持比例字体。

### 带候选的文本输入：只能选候选项用 Combobox，允许填写候选之外的值用 Base UI Autocomplete

Base UI 的 Combobox 只接受候选项，不能提交候选之外的文字；模型 ID 这类网关列表常常不全、必须允许自由填写的字段，用 `@base-ui/react/autocomplete`。shadcn 的 base-nova registry 没有 Autocomplete，参考 `components/agent/ModelIdField` 的写法：输入框用 `components/ui/input-group`，弹层表面沿用 `bg-popover`、`shadow-overlay` 与 `z-overlay`。

### 放宽样式与布局守卫时，在 PR 描述中逐条列出放宽项

`frontend/src/` 下除测试以外的源码都受 `@shadcn/lint` 样式规则、滚动与响应式守卫和类名守卫约束（定义在 `frontend/eslint.config.js`）。每条放宽都让一类写法在登记范围内重新合法，在 diff 里却只是配置中的一行，审查容易放过。因此在 PR 描述中逐条列出，由审查判断理由是否成立：

- 新增的组件变体，`@shadcn/lint` 的 `allow` 或 `contracts` 条目。
- 登记进 `VIEWPORT_BREAKPOINT_ALLOWLIST` 的文件。只放行外壳切换标准档与紧凑档、弹层宽度这类确需按视口判断的场景；外壳内组件的可用宽度随侧栏与分栏变化，一律用容器查询。
- 新增或扩大 `restrictSyntax(...)` 豁免的配置块。

配置块的组合方式（flat config 对 `no-restricted-syntax` 整体替换选项）见配置文件头注释，由 `src/lint-class-guards.test.ts` 校验。

### Sheet、DropdownMenu、Command、Chart 的本仓库用法

- **Sheet**：左右两侧默认宽度为 `w-md`。调整右侧宽度写 `data-[side=right]:w-*`，直接写 `w-*` 会被原语里带 `data-[side=right]:` 前缀的默认宽度盖住。
- **DropdownMenu**：删除等不可逆的菜单项用 `DropdownMenuItem` 的 `variant="destructive"`。
- **Command**（常放在 Popover 里做可搜索的选择列表）：`CommandList` 是 listbox，传入本地化 `label` 覆盖默认英文名称，里面只放 `CommandEmpty`、`CommandGroup`、`CommandItem`。分隔线、加载中和加载失败的提示放在 `CommandList` 之外，否则 axe 报 `aria-required-children`；不随搜索词过滤的固定入口（如「全部项目」）写成 Command 之外的 `Button` 或 `Link`。Popover 打开时用 `initialFocus` 把焦点交给 `CommandInput`。
- **Chart**：图表外层包一层 `role="img"`，以图表名称作 `aria-label`，紧随其后放一张 `sr-only` 表格列出同样的数据，读屏用户才拿得到图表里的数值（先例：`components/usage/UsageTrendChart.tsx`）。系列颜色引用 `index.css` 的 token，提示内容的表面与弹层一致（`bg-popover`、`shadow-overlay`）。

## 颜色与文字层级

### 颜色只用 `index.css` 的语义 token，深浅用透明度修饰表达

原始色值和内联颜色由 `@shadcn/lint` 的 `no-raw-colors`、`no-inline-styles` 拦截。以下几点 lint 判断不了，由审查核对：

- 浅底、描边、选中态写成基色加透明度修饰（`bg-primary/15`、`border-border/50`），不为某种深浅新增变体 token：变体 token 会让同一语义出现多个近似色，旧色板的变体色已按这一原则删除。CSS 中需要透明度时写 `color-mix(in oklab, var(--primary) 15%, transparent)`。
- 表单控件的描边用 `border-input`，不加透明度修饰：`--input` 在浅色与深色下对页面底、卡片和弹层都不低于 3:1（WCAG 1.4.11），半透明会让对比度随底色漂移。填充可以用 `bg-input/12` 这类透明度，不要把控件填充调到让占位符低于 4.5:1。卡片、分隔线等装饰性描边仍用 `border`。
- 语义不混用：危险操作用 `destructive`，琥珀色 `warn` 只表示警告与过期。
- 文字只分 `foreground`、`subtle-foreground`、`muted-foreground` 三档。`muted-foreground` 上不再叠加透明度或 `opacity`：它在页面底色上的对比度浅色与深色下都只略高于 5:1，再降就低于 WCAG AA 要求的 4.5:1。
- 按集、按视频单元、按资产类型着色时用身份色 token（`episode`、`unit-clip`、`unit-narration`、`asset-*`），色相和配色经 `episodeHue`、`unitHue`、`assetColor` 取值，不在调用处另算颜色。

## 弹层与提示

### Dialog、Sheet、AlertDialog 按 Header、Body、Footer 组合，长度不定的内容只放 Body

弹层由原语限高。`DialogHeader`、`DialogFooter` 固定在两端，`DialogBody`（Sheet、AlertDialog 中为 `SheetBody`、`AlertDialogBody`）是唯一的滚动区。错误信息、影响说明、长列表这类长度不定的内容如果放在 Header 或 Footer 里，矮窗口或较长译文会把它们连同主操作一起裁掉，而且滚动不到，因此一律放进 Body。

- 宽度用 `size` 选择，调用处不写 `max-w-*`。多步向导用 `size="wizard"`，各步骤共用一个 `DialogBody`，进入新步骤时把它的 `scrollTop` 置 0。
- 每个弹层都有 Title；没有可见标题时给 Title 加 `sr-only`，否则读屏无法播报弹层名称。
- Body 里没有可聚焦元素、内容又可能超高时，给 Body 加 `tabIndex={0}`、`role="region"` 与 `aria-label`，否则键盘无法滚动。
- Footer 已有「关闭」按钮时，Content 传 `showCloseButton={false}`，避免出现两个关闭入口。

### 不可逆操作先确认：弹层用 AlertDialog，列表行内原地确认；可撤销操作直接执行并在提示里提供「撤销」

AlertDialog 打开时焦点落在「取消」上，误按 Enter 不会执行操作。确认按钮用 `variant="destructive"`；提交中禁用两个按钮，并在 `onOpenChange` 中忽略关闭请求，Esc 不会在请求未完成时关掉对话框。

列表行内的轻量对象（如托盘里的排队消息）原地确认：该行换成提示文字、`variant="destructive"` 的确认按钮与 `outline` 的「取消」，焦点落在「取消」按钮上，Esc 或取消退出确认并把焦点交还触发它的按钮，焦点离开确认区域也退出确认。先例是 `QueuedMessageTray` 的删除。

移除、移动、隐藏这类可以恢复的操作不弹确认，执行后调用 `pushToast(text, tone, { action: { label: t("common:undo"), onClick } })`。点击「撤销」会执行回调并关闭这条提示。

提示按钮也可用于打开刚完成的结果：耗时操作（如导入项目）完成时用户已经离开发起页，就不强行跳转，改推带「打开项目」这类按钮的提示。「已离开」在 await 之后复核卸载时 abort 的 `signal.aborted` 判断，发起的写入请求本身不接这个 signal。已离开时每个结果分支（成功、失败、需确认）都要有提示，不能因为对话框随页面卸载而静默丢失。参考实现：`frontend/src/components/pages/lobby/useProjectImport.tsx`。

## 按钮

### 按钮使用 `components/ui/button` 的 `Button`，按操作语义选择变体

- `default`：区域内的主操作，同一区域最多一个。
- `outline`：次要操作，如「取消」「重新渲染」。
- `ghost`：工具栏按钮与图标按钮。
- `destructive`：删除、覆盖等不可逆操作。
- `secondary`、`link`：低强调操作与行内链接式操作。

尺寸用 `size`：`xs`、`sm`、`default`、`lg`，只有图标时用 `icon`、`icon-xs`、`icon-sm`、`icon-lg`。按钮内的图标加 `data-icon="inline-start"` 或 `data-icon="inline-end"`，不写尺寸 class。`Button` 没有 loading 属性；加载时禁用按钮，并把前置图标换成带 `animate-spin` 的 `Loader2`。

整行条目（列表行、集目录行）、缩略图触发器与拖放区不是操作按钮，用原生 `<button>` 配 `focus-ring`：换成 `Button` 要在调用处改写它的样式，`no-restyle` 不允许。外观像按钮的导航入口用 `Link` 套 `buttonVariants`，不用 `Button` 调用 `navigate`：读屏按链接播报，也能在新标签页打开。

## 滚动与横向溢出

### 滚动区的高度沿 flex 链传下来，会出现滚动条的主栏预留槽位

文档本身不滚动，滚动只发生在外壳指定的容器里：侧栏、限宽与铺满档的外壳主体、全出血区段的各栏。滚动容器必须是定位元素、不用视口高度定高，这两点由 lint 守卫；文档滚动和内容被裁切由 e2e 溢出探针在已有场景上兜底，新组件和未覆盖的宽度仍靠审查。审查新写或改动的滚动区时确认两点：

- **高度沿 flex 链传下来。** 从外壳到滚动容器之间的每一层 flex 子项都写 `min-h-0`（横向是 `min-w-0`），全出血区段的根节点写 `flex min-h-0 flex-1`。缺一层时，flex 子项的最小高度等于内容高度，滚动容器被内容撑开而不滚动，超出部分被外壳裁掉、滚动不到。不用 `sticky` 模拟独立滚动的栏。
- **会随内容出现滚动条的主栏写 `[scrollbar-gutter:stable]`。** 外壳主体已经写了；全出血区段、画布窄断点下新增的主滚动栏同样要写。不写时，内容变长出现滚动条，整栏会横向跳动（#3067）。

放行：弹层里的短列表、不与内容区并排的小型滚动区可以不预留槽位。

### 横向放不下的内容按类型处理：长串折行、条目滚动加渐隐、表格固定列宽、单行文字截断

只写了 `overflow-y-auto` 的滚动区（弹层 Body、画布各栏、Agent 面板里的托盘），内容宽于容器时会被浏览器变成横向滚动，而不是裁切或报错，按钮行和长串会被挤到视野之外。e2e 溢出探针只覆盖已有场景，新组件、长文本和窄宽度要靠审查。新增或改动文字、按钮行与列表时，按类型确认它在最窄的受支持宽度下（如 Agent 面板最小宽度）放得下：

- **长串**（URL、无空格的 token、旧数据里的长台词）：写 `wrap-break-word` 这类折行类。`whitespace-pre-wrap` 不会强制断开长串。
- **按钮行、确认行**：允许折行或收窄，不写不可收缩的固定组（#3139）。
- **可横向滚动的条目**（标签、缩略图带）：容器写 `overflow-x-auto scroll-fade-x`，被裁掉的一侧显示渐隐。
- **表格**：用 `table-fixed` 和 `<colgroup>` 给状态、时间、操作这类短列固定宽度，并加 `whitespace-nowrap`；主文字列的单元格用 `TruncatedText`。
- **单行文字**（名称、路径、模型 ID）：用 `components/shared/TruncatedText`，父级写 `min-w-0`。不用 `title` 属性代替，键盘用户看不到它。放在按钮等可聚焦元素里时传 `focusable={false}`，全文由外层的可访问名称提供。带编号的名称把编号作为不截断的前缀单独渲染。

确需横向滚动的区域写 `overflow-x-auto` 声明，探针按这个类名识别；未声明的横向滚动报为缺陷。

## 编辑单元与保存

### 编辑单元有未保存修改时，生成经 `unit.saveAndGenerate`，保存成功后才生成

编辑单元的结构（字段与动作的划分、保存栏位置）见 `website/docs/dev/architecture.md`「编辑单元与离开拦截」。生成读取的是已保存内容，并按输入计费。编辑单元有未保存修改时，生成按钮写「保存并生成」（`common:save_and_generate`），点击时调用 `unit.saveAndGenerate(generate, { confirm })`：先 `discard()` 会丢掉创作者的修改，直接提交生成会按旧输入生成并计费（#3045、#3049）。`confirm` 是「重新生成会让下游失效」这类确认：取消时什么都不保存，保存失败时不生成。离开拦截兜不住这里，因为生成不会触发离开。

放行条件：没有未保存修改时直接生成。

### 离开与切换交给离开拦截，不自建未保存确认框或 `beforeunload`

`AppRoutes` 根部的 `LeaveGuardProvider` 是唯一的离开拦截，机制见架构文档「编辑单元与离开拦截」。页面自建的确认框或 `beforeunload` 会与它重复弹出，或在它放行之后再拦一次；漏接拦截则让未保存修改随跳转或卸载静默丢失。这类遗漏在新增切换动作和自管表单时反复出现（#3040、#3044、#3049、#3052、#3264）。审查改动可编辑内容或切换动作的 diff 时逐项确认：

- **自管表单如实登记。** 不经 `useEditUnit` 的表单调用 `useLeaveGuard({ dirty, saving, save, discard })`。`save` 如实返回是否成功：总返回 `true` 会让保存失败的修改随跳转丢失。`saving` 如实反映保存或创建请求在途：漏报时，「放弃修改」会放走已发出的保存，「保存并离开」会重复提交。
- **不经路由的切换包进 `useConfirmLeave()`。** 分镜、记忆文件、资产的上一个与下一个、关闭 Sheet 都算；被包住的动作要同步完成切换。含多步的切换（先切视图再选中）放进同一个动作，离开拦截只保留最近一次请求。
- **二次确认的动作包住确认，不包住打开。** 删除、移除时包住确认框里的确认动作；包住打开确认框会让「先放弃修改再取消删除」白丢修改。可能失败的动作返回 `Promise<boolean>`，如实报告结果，修改保留到动作成功才丢弃。
- **即时提交的字段**除 `useLeaveGuard` 外，还用 `useRetainWhile` 参与外部移除保留。
- **非用户发起的切换不弹拦截。** Agent 改动带来的自动定位、跳转先查 `useHasUnsavedChanges()`，有未保存修改时直接略过。
- **跳转经 wouter。** 不直接调用 `window.history`，也不改写 `window.location`，否则跳转绕过拦截。

放行：不会卸载编辑单元的跳转（同一编辑单元的分页切换）在登记时用 `allowNavigation(to)` 放行；整页刷新前的 `beforeunload` 由 `utils/lazy-component.ts` 统一处理。

### 依赖新数据的后续动作只在刷新结算为 `success` 后提供

写入后的刷新经 `refreshAfterWrite`（结构见架构文档「项目数据刷新」）。撤销、切到新条目、定位到新建的集、「成功」提示这类依赖新数据的后续动作，只在结算值为 `success` 后提供；刷新失败时只留刷新失败的提示，不同时报告成功，否则创作者看到成功提示却停在旧数据上。参考 `frontend/src/components/canvas/episodes/CreateEpisodeDialog.tsx`。

放行条件：关闭编辑框、离开已删除条目的页面这类写入本身的收尾。

## 异步：取消与补拉

处理「await 之后数据可能已过期」时，按场景采用下面几种机制，不靠手工传播闭包标志。

### 跨函数边界的异步链用 AbortSignal 取消

调用链一旦跨出单个函数（effect 调用异步函数、异步函数里再发请求），取消就通过 AbortSignal 传播。`cancelled` 闭包标志只拦得住所在函数自己的 await，传不到被调函数里：被调函数 await 之后照常写 store、建 SSE 连接，迟到的响应就会覆盖新状态。

- API 层方法接受 `options?: { signal?: AbortSignal }` 并原样传给 `fetch`。网络 await 点被 abort 后自动 reject。
- 非网络 await 点之后、执行副作用（写 store、建 SSE 连接等）之前复核 `signal.aborted`，覆盖「abort 发生在响应已 resolve 之后」的窗口。
- 接管方轮换 controller：新一轮加载先 `abort()` 上一个 controller，再新建。
- 收尾（如 loading 复位）由接管方负责。被 abort 方在 `finally` 中先检查 `signal.aborted`，已作废就不动共享状态，否则会打断接管方正在进行的加载。

放行条件：所有 await 都在同一个函数内、不需要把取消传给被调函数时（如 `frontend/src/router.tsx` 的配置状态轮询），`cancelled` 标志可以保留。用户触发、不属于数据加载的一次性动作（如导出），离开页面后允许继续完成，不在本条范围内。

参考实现：`frontend/src/hooks/useAssistantSession.ts`（init 自动选择与 `loadSession` 加载链）。

### 作废一次性事件触发的请求，必须补拉

由一次性事件（SSE 通知、用户确认）触发的在途请求被 abort 后，这个事件已经消费掉，数据不会再来一次。作废方（或接管方）在 abort 之后自己发起一轮拉取补上缺口，否则界面永远停在旧数据。「取消」与「补偿」成对出现：diff 里只有 abort 没有补拉，就要问这份数据之后由谁带回来。

参考实现：`frontend/src/hooks/useScriptReviewDraft.ts`（采纳写入时作废在途拉取并补拉）。

### 切换作用域时，旧作用域的交互态整体作废，异步写入只在代号仍有效时落地

交互态（确认请求、在途标记、错误提示、分页游标与总数）属于某个作用域（项目、会话、视图）时，把它们收进一个带代号的会话：切换作用域时换新代号并整体重置，每个异步写入落地前核对自己发出时的代号仍是当前代号。只 abort 一个请求或只清空其中几个字段，切换后才返回的其他写入与漏清的字段会把旧作用域的结果带进新作用域：在一个项目里确认，取消的是另一个项目的任务；切换视图后，旧视图的游标被发给另一个接口；切换项目后仍显示上一个会话的错误。几条并列的请求源写同一组共享字段时，共用一个作废边界（同一个刷新协调器或同一个代号），不各自作废。

参考实现：`frontend/src/components/usage/use-task-cancellation.ts`（确认会话的 `gen`）、`frontend/src/stores/usage-records-store.ts`（两个视图共用一个分页协调器）。

## 异步：主动跳过的补偿

### 跳过的补偿要记账，保护窗口关闭时补做

保护条件成立时主动跳过一次补偿动作（补拉、对账、收尾切换），被跳过的补偿不会自己再发生。跳过方按触发它的 key 记下来，保护窗口关闭的一侧负责补做；补做放在 `finally` 这类同时覆盖成功与失败的位置，否则失败路径上保护窗口永不关闭，记下的补偿永远不会执行。

### 保护状态按完整作用域分桶，可叠加的用计数

跨请求存活的保护状态（在途计数、跳过标志）按语义上的完整作用域（请求、项目、会话）分桶。少一个维度，不同操作就共享一个桶：并行的另一次操作解除了本次的保护，或者别的作用域上迟迟不结束的操作一直挡住本该发生的补偿。会并发叠加的状态用计数，不用布尔。

参考实现：`frontend/src/hooks/useAssistantSession.ts` 的 `deletingCurrentRef`（项目 × 会话计数分桶）与 `deferredRefreshRef`（记录跳过的列表补拉，删除收尾后补做）。
