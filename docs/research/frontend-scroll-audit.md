# ArcReel 前端滚动与裁切排查

日期：2026-10-02。范围：`ArcReel/frontend/src`（React 19 + Tailwind v4），只读排查，未修改任何源码。用途：为 shadcn/ui 重构制定统一的滚动容器规则。

数据来源分五组：git 历史考古（history）、静态审计（static）、设置页探针（probe-settings）、工作区探针（probe-workspace）、弹层探针（probe-overlays）。各组条目均已人工复核，被驳回的条目见附录。下文的文件路径默认相对 `frontend/src/`。

## 1. 结论

**这是系统性问题。** 一句话根因：滚动职责没有归属。页面骨架、弹层原语和自动增高 textarea 都没有内建「谁负责滚动、可用高度是多少」的规则，过去一直在出问题的那一层打局部补丁；全局滚动条又在静止时完全透明，使能滚动的区域也看起来像被裁掉。

按当前实例数排序的根因模式（实例编号见第 3 节）：

| 模式 | 当前实例数 | 说明 | 实例 |
|---|---|---|---|
| P3 滚动可见性缺失 | 6 | 滚动条静止透明（4px，alpha 为 0）；文档根滚动条永不显示；嵌套滚动、横向滚动没有任何提示 | G-01、G-02、G-04、W-05、S-03、S-04 |
| P2 弹层原语不内建滚动结构 | 6 | ModalShell 遮罩不可滚、面板不限高；Popover 默认不夹紧高度；没有 Header/Body/Footer 结构；手写弹窗绕开原语 | O-01、O-02、O-03、O-05、O-06、O-07 |
| P1 页面高度链断裂 | 5 | `flex-col` 中放着不能收缩、也不限高的兄弟节点，中间层层 `overflow-hidden`，整条链上没有兜底滚动层；或收缩规则放反，主内容被压扁 | W-01、W-02、W-03、W-04、A-03 |
| P4 用 vh 估算可用高度 | 2 | 子滚动容器里用 `max-h-screen`（100vh）；根布局 `h-screen`，没有 dvh 和可扣减变量 | S-01、G-03 |
| P5 JS 测高过时 | 2 | 至少 4 套手写 `scrollHeight` 测高，只在 onChange 或 value 变化时重算，初始 `overflow-hidden` | A-01、A-02 |
| 其他 | 3 | 绝对定位逃逸出滚动容器、向导切步骤保留滚动位置、`max-w` 类名冲突 | S-02、O-04、O-08 |

严重度分布：high 5 处全部落在 P1、P2、P4（W-01、W-03、W-04、S-01、O-01）。P3 自身多为 medium/low，但它放大所有其他问题：只要两套滚动条规则还在，任何「无法滚动」的报告都可能只是「看不到滚动条」。

用户报告的三个已知案例：

- 剧集页「制作进度」挤掉画布：已复现（W-01），并发现它还是 W-02、W-03、W-04 的加重因素。
- Agent 输入框被裁：已复现（A-01），同类问题覆盖全部自动增高 textarea（A-02）。
- 使用记录表格横向溢出：现有数据下未复现溢出（表格自然宽 644px，容器 830px）。溢出风险成立，被裁的观感来自 G-01，详见 S-03。

关于「宽窗口」：问题主要由**可用高度**而不是宽度触发。2560x1440 下工作区基本正常；1440x900 展开「制作进度」后开始出现；1440x700、1280x720 下最严重。宽屏用户在窗口未最大化、浏览器缩放或开启 Agent 面板时同样会遇到。

## 2. 历史临时修复

考古覆盖 ArcReel 仓 `main` 分支 2026-02 至 2026-10，共 14 个相关提交，全部用 `git show` 核对过。按 `-G 'min-h-0'` 统计有 32 个提交加过 `min-h-0`，其中只有 3 个是标题写明修滚动或裁切的 fix，其余都是功能 PR 顺手补的。

| 提交 | 日期 | 修了什么 | 性质 | 后续 |
|---|---|---|---|---|
| 26da61523 | 2026-02-27 | 引入「滚动条自动隐藏」（styles.css + main.tsx 监听） | 全局设计，但方向错误 | 一直存在；ff9ea3b94（2026-05-07）新增 10px 可见滚动条却未删旧规则，两套叠加 → G-01 |
| 299b58f9f | 2026-02-28 | popover 改 `createPortal` 逃离 overflow 祖先 | 局部补丁 | 被 0eeecf4d3 收编 |
| 0b23aa997 → 65e33d718 → abaad5484 | 2026-04-21 起 | UnitList 内部不能滚、textarea 塌成 0 | 局部补丁，同一 grid 行模板一周内改 3 次 | 全仓唯一的 `min-h-0` 回归测试（UnitList.test.tsx，断言 className，不测行为） |
| 65e33d718 | 2026-04-21 | @ 提及选单被裁，改 FloatingPortal | 局部补丁 | 被 0eeecf4d3 收编 |
| 0eeecf4d3 | 2026-04-21 | 弹层统一为 Popover（floating-ui + portal） | **规则**，唯一沉淀为公共件的一类 | 基本不再复发；f95b4d3ba（2026-05-14）在 ProviderModelSelect 又手写一套 floating 逻辑，绕过规则 |
| 82443964e | 2026-04-27 | 供应商页保存按钮要滚到底；放弃高度链，改 `sticky top-0 max-h-screen` | 局部补丁，并引入新缺陷 | 17fc6fdd1（2026-08-29）把同样写法复制到端点页 → S-01 |
| e38905dba | 2026-05-10 | ShotDetail 三栏不能滚，三栏各补 `h-full` | 局部补丁（只改叶子层） | 父级 ResponsiveDetailGrid 契约未改 |
| 24f18169a | 2026-05-11 | 抽出 ModalShell + GlassModal，统一焦点、Esc、滚动锁 | 半个规则：没管内容区滚动 | 限高靠调用方自传 → O-01、O-02 |
| f95b4d3ba | 2026-05-14 | 模型下拉被祖先裁掉 | 局部补丁（绕过 Popover） | O-06 的固定 `max-h-60` 来源之一 |
| 815b29669 | 2026-06-02 | 端点选择器滚不到底：外层限高 + 内层 `min-h-0 flex-1` | 修法正确，但只修一个弹层 | Popover 未内置该结构 → O-06 |
| c1f0faaac | 2026-06-25 | 长台词被截断，抽出 `useAutoResizeTextarea` | 半个规则：约 9 处在用 | 另有 7 个文件仍手写 `scrollHeight` → A-01、A-02 |
| 1b49714cd | 2026-08-02 | 解析预览滚动上移到 tabpanel | 局部补丁，契约只写在注释里 | 换处复用容易丢 |
| 830e95cd7、d18845b7f | 2026-08-16、2026-10-01 | 插入 WorkflowPanel，后改为步骤清单 | 新组件插入高度链，没人负责高度预算 | → W-01 |
| f952f2cbf → 5fa907de6 | 2026-09-21 | 建立 `.h-app-screen`（`calc(100vh - var(--auth-banner-h))`）视口高度工具类，随功能回滚一并删除 | 唯一一次视口高度规则尝试，已失效 | → G-03 |

共性：

1. 哪一层坏就补哪一层（`min-h-0`、`h-full`、`grid-rows-[minmax(0,1fr)]`），没有人回头确认从根布局到叶子的整条高度链。
2. 绕开高度链改用「自然流 + sticky + 100vh」，修一个问题留一个问题，再被当成模板复制。
3. 职责靠 JSX 注释维系（「父容器须带 overflow-y-auto」），组件接口和类型不表达谁负责滚动。
4. 弹层在有了统一 Popover 后基本不再复发；滚动容器始终没有对应公共件，也没有 lint、测试或文档规范。

复发证据：flex 链缺 `min-h-0` 或缺确定高度（ShotDetail、UnitList 两次、WorkflowPanel、设置页 main）；100vh 估算（ProviderSection → EndpointsSection，auth 提示条暴露 `h-screen`）；看不见滚动条（2026-02 起一直存在，2026-05 叠加第二套规则）。

## 3. 实例清单

编号为本文合并后的编号，括号内为各组原始 id。严重度取复核后的结论。

### 全局

| id | 位置 | 视口 | 症状 | 根因模式 | 文件 | 严重度 |
|---|---|---|---|---|---|---|
| G-01（hist-01、st-01、ps-03、pw-07） | 所有滚动容器（Chromium、Electron、Safari） | 全部 | 滚动条静止时完全透明，悬停也不出现，只在滚动后显示 1.2 秒；用户看不出侧栏、分镜列表、详情栏、StylePicker 等可以滚动，例如 ShotDetail 右栏「重新生成分镜」只露出一半，看起来像被裁掉 | P3。styles.css 在 index.css 之后引入，把滚动条改为 4px、thumb 颜色 `rgba(255,255,255,calc(0.12*var(--scrollbar-opacity)))`，`--scrollbar-opacity` 默认 0，只有 main.tsx 捕获到 scroll 后写入 `data-scrolling` 才变 1。index.css 的 `border:2px solid transparent` 仍然合并生效；`:hover` 规则的 `background-clip:padding-box` 在 4px 轨道上留下 0px 可见宽度 | `css/styles.css:3-43`、`index.css:121-134`、`main.tsx:13-14,30-60` | medium |
| G-02（st-02） | 文档级滚动页面：`/app/projects`、`/app/assets`、Login、NotFound | 全部 | 页面整体纵向滚动条始终不可见，滚动时也不出现 | P3。视口滚动时 scroll 事件 target 是 document，监听器 `instanceof HTMLElement` 守卫直接返回，html 永远拿不到 `data-scrolling` | `main.tsx:38-41`、`ProjectsPage.tsx:964`、`AssetLibraryPage.tsx:187` | medium |
| G-03（hist-11、st-12） | 根布局高度 | 移动端/平板；加全局提示条时全部 | 100vh 高于实际可视区或未扣除提示条，外壳底部落到视口外；桌面壳目前不受影响 | P4。外壳用 `h-screen`，dvh/svh 0 处；项目设置用 `fixed inset-0`，项目列表/资产库用 `min-h-screen` 文档滚动，三种根高度策略并存 | `layout/StudioLayout.tsx:159`、`pages/SystemConfigPage.tsx:153`、`router.tsx:91` | low |
| G-04（st-14） | GridPreviewPanel 横向列表 | Chromium ≥121 | 显示原生细滚动条，与全局风格不一致 | P3。`scrollbar-thin`（`scrollbar-width: thin`）使 Chromium 忽略该元素的 `::-webkit-scrollbar` | `canvas/timeline/GridPreviewPanel.tsx:106` | low |

### 剧集工作台

| id | 位置 | 视口 | 症状 | 根因模式 | 文件 | 严重度 |
|---|---|---|---|---|---|---|
| W-01（hist-12、st-03、pw-01） | 剧集页展开「制作进度」WorkflowPanel（分镜、广告、参考视频项目均受影响） | 1280x720 最严重；1440x700、1280x800 严重；1440x900 明显；2560x1440 基本正常 | 面板展开到 372–419px，下方画布被压到 0；1440x700 下分镜列表只剩 10px 且落在视口外，滚轮和 Tab 都到不了，只能收起面板 | P1。路由容器 `flex h-full flex-col` 中，WorkflowPanel（`border-b px-4 py-2`，展开区 `mt-2 space-y-3`）不限高、不滚动、`min-height:auto` 不收缩；下方画布 `min-h-0 flex-1`；TimelineCanvas、ShotSplitView 各层都是 `overflow-hidden`；StudioLayout 的 main 也是 `overflow-hidden` | `canvas/StudioCanvasRouter.tsx:738-791`、`workflow/WorkflowPanel.tsx:393-394,466`、`canvas/timeline/TimelineCanvas.tsx:257,385,404`、`canvas/timeline/ShotSplitView.tsx:179`、`layout/StudioLayout.tsx:166-167` | high |
| W-02（st-04、pw-04） | 原文审阅页 EpisodeSourceReview（草稿集），以及各画布头部栈 | 1440x700 叠加 W-01 时 | 「本集导览」卡片被裁掉下半截，原文滚动区只剩约 24px 可见；main 溢出 50px 被裁，滚轮够不到 | P1。与 W-01 同一条链：画布内部还有一层不限高的头部栈（EpisodeHeader、tab 栏、Progress、GuideSection）。GuideSection 带 `overflow-hidden`，会收缩但内容被裁而不是滚动。单独出现时 1440x700 不复现 | `canvas/EpisodeSourceReview.tsx:154-190,424-453`、`canvas/timeline/TimelineCanvas.tsx:257-385`、`canvas/grid/GridImageToVideoCanvas.tsx:276,362`、`canvas/reference/ReferenceVideoCanvas.tsx:946-1069` | medium |
| W-03（pw-02） | 参考视频「视频单元」tab 中栏文稿编辑器 | 1440x700 不展开面板即出现；1440x900 展开后出现；2560x1440 正常 | textarea 被压到 24px 甚至消失，高亮层文字溢出并与「对应原文」重叠；中栏不能滚动 | P1（收缩规则放反）。编辑器 `relative min-h-0 flex-1` 可收缩，兄弟 SourceTextReadonly `mt-3 flex-shrink-0`（约 202px）不收缩；外层三级 `flex min-h-0 flex-1 flex-col overflow-hidden`，没有一层退化为滚动 | `canvas/reference/ReferenceVideoCard.tsx:345,397`、`canvas/reference/ReferenceVideoCanvas.tsx:1339,1341,1383` | high |
| W-04（pw-03） | 参考视频右栏 UnitPreviewPanel 视频预览 | 1440x700 不展开面板即出现；1440x900 展开后出现 | 视频卡片被压成 2–21px 的一条线；容器不溢出，滚动也找不回来 | P1（收缩规则放反）。`flex-col overflow-y-auto` 滚动容器的直接子项 `relative aspect-video overflow-hidden` 没有 `shrink-0`；`overflow-hidden` 使其 `min-height:auto` 变为 0，aspect-ratio 挡不住收缩 | `canvas/reference/UnitPreviewPanel.tsx:124,160`、`canvas/reference/ReferenceVideoCanvas.tsx:1511` | high |
| W-05（pw-08） | 剪辑视图 `?view=edit` 时间线轨道 | 1440x900、1440x700 | 轨道内容 1834px 只显示 1109px；横向滚动条在视口下方且透明；字幕片段硬裁，无省略号（有 title） | P3。整页 `overflow-y-auto` 内嵌 `overflow-x-auto`，横向滚动条随页面滚出视口 | `canvas/edit/EditTimelineView.tsx:249`、`canvas/edit/EditTimelineTracks.tsx:68,433,468` | low |

### Agent 面板

| id | 位置 | 视口 | 症状 | 根因模式 | 文件 | 严重度 |
|---|---|---|---|---|---|---|
| A-01（st-05、pw-05、hist-10） | AgentCopilot 输入框 | 全部（拖窄面板、矮窗口必现） | 「交给 Agent」预填、斜杠命令补全或面板变窄后，textarea 停在旧高度，`overflow-y:hidden`，内容被裁且滚轮无效；方向键或输入任意字符后恢复 | P5。只在 onChange 时测高；`setLocalInput`、store 预填、面板宽度变化、窗口 resize 都不重算；初始 class 带 `overflow-hidden`；上限按 `window.innerHeight` 而不是面板可用高度计算 | `copilot/AgentCopilot.tsx:28,250-260,327-332,343-372,647-650` | medium |
| A-02（st-07、hist-10） | 所有自动增高 textarea：AutoTextarea 的 9 个消费方、lorebook 四种卡片、MessageRow 编辑态、PendingQuestionWizard | 改变宽度时 | 窗口或 Agent 面板宽度变化后折行增多，高度不变，最后几行被裁且不可滚（实测 1440 → 800 宽：clientHeight 48 / scrollHeight 64） | P5。4 套以上手写测高只依赖 value 或 onInput，没有 ResizeObserver；统一 `overflow-hidden`。lorebook 卡片缺边框补偿（少 2px，吃掉 padding，不裁文字） | `hooks/useAutoResizeTextarea.ts:8-34`、`ui/AutoTextarea.tsx:43`、`canvas/lorebook/{Scene,Prop,Product,Character}Card.tsx`、`copilot/chat/MessageRow.tsx:169,275`、`copilot/PendingQuestionWizard.tsx:98-102` | medium |
| A-03（pw-06） | AgentCopilot 底部组件堆中的 TodoListPanel | 矮视口（代码推断） | 长 todo 列表 + 待答向导时，消息区先被压到 0，随后 todo 列表被裁且不能滚动 | P1。TodoListPanel 是可收缩的 `overflow-hidden` flex item，展开列表无 max-h、无滚动；底部区域没有共享高度预算。无真实会话，未复现 | `copilot/TodoListPanel.tsx:75`、`copilot/AgentCopilot.tsx:466,523-552`、`copilot/PendingQuestionWizard.tsx:191` | low |

### 设置页

| id | 位置 | 视口 | 症状 | 根因模式 | 文件 | 严重度 |
|---|---|---|---|---|---|---|
| S-01（hist-02、hist-03、st-09、ps-01） | 系统设置 → 调用端点、供应商的二级列表栏 | 1440x700、1280x720 用现有数据即复现；1440x900、2560x1440 暂时正常；增加自定义供应商后供应商栏同样触发 | 右侧详情或表单较长时（正常工作状态），列表最后约 77px（顶栏高度）始终看不到，focus 也带不出来；只有把 main 滚到最底才露出；列表背景和边框在半屏处断开 | P4。main 可视高度为 100vh 减顶栏，二级栏却是 `sticky top-0 max-h-screen self-start overflow-y-auto`，底部永远比 main 多出一个顶栏高度 | `pages/settings/endpoints/EndpointsSection.tsx:536-539`、`pages/ProviderSection.tsx:153-158`、`pages/SystemConfigPage.tsx:153,220,291-292` | high |
| S-02（ps-02） | 系统设置 → 模型选择；使用记录（有记录时） | 全部 | 文档本身变得可滚动（1540/900、1026/800）；在顶栏等不可滚区域滚轮，整个设置外壳上移，底部留下大片空白 | 其他（绝对定位逃逸）。`sr-only` 是 `position:absolute`，main 未定位，其包含块是 main 外的根 `relative flex h-screen flex-col`，main 的 overflow 裁不住；根没有 `overflow-hidden` | `pages/settings/MediaModelSection.tsx:544`、`usage/UsageRecordsCard.tsx:142`、`pages/SystemConfigPage.tsx:153,292` | medium |
| S-03（st-10） | 系统设置 → 使用记录表 | 1440x900 及以下宽度（溢出时） | 9–10 列表格在 `max-w-4xl` 内可能横向溢出，横向滚动条不可见，费用和详情列像被裁掉；状态列中文可能逐字竖排。现有数据未复现溢出 | P3。`overflow-x-auto` 容器无可见滚动条、无渐隐遮罩；状态列 CELL_CLS 缺 `whitespace-nowrap`；4 个 truncate 列上限合计超过 830px | `usage/UsageRecordsCard.tsx:108-109`、`usage/RecordRow.tsx:34,176-196`、`pages/SystemConfigPage.tsx:298` | low |
| S-04（ps-04、po-03） | 项目设置「项目风格」及创建项目向导 step 3 的 StylePicker | 全部；1440x700 下内层占外层 75% | 18 张风格卡放在 420px 内嵌滚动区，只露出 2 行且第 2 行被裁；没有滚动条，被裁边缘像列表自然结束，10 个选项被隐藏；内外两层滚动高度相近，分不清在滚哪层 | P3。长页面里嵌套固定 `max-h-[420px] overflow-y-auto`，叠加 G-01 | `shared/StylePicker.tsx:273`、`pages/ProjectSettingsPage.tsx:750`、`pages/CreateProjectModal.tsx:471` | medium |

### 弹层

| id | 位置 | 视口 | 症状 | 根因模式 | 文件 | 严重度 |
|---|---|---|---|---|---|---|
| O-01（hist-14、st-08、po-01） | 所有未自带 max-h 的 ModalShell/GlassModal 弹窗。已实测 PromptAuthoringDialog（自选多条）、AssetFormModal；代码确认另有约 11 个调用方不限高 | 约 520px 高及以下时主操作不可达（相当于 1440x900 屏 + 150% 缩放）；1440x560 已两头被裁 | 内容高于视口时，居中导致标题和底部按钮同时被推出视口；弹窗内无滚动区，遮罩不可滚，body 已锁滚动，滚轮、键盘都到不了 | P2。遮罩 `fixed inset-0 flex items-center justify-center px-4`，无 py、无 `overflow-y-auto`；dialog 只有 `max-w-[96vw]`，无 max-h；GlassModal 面板固定 `overflow-hidden` | `ui/ModalShell.tsx:113-122`、`ui/GlassModal.tsx:61`、`canvas/shared/PromptAuthoringDialog.tsx:206-208,258`、`assets/AssetFormModal.tsx:93-97,154,287-330`、`ui/ConfirmDialog.tsx:60` | high |
| O-02（po-06、hist-14） | 已限高的弹窗（EndpointImport、UsageRecordDetail、PromptPreview、ArchiveDiagnostics、ShareToMarket、SegmentRefsEdit 等） | 1440x700 | 5 种限高写法、8 种 max-h 取值；整面板滚动的弹窗内容一多，「取消」「导入」和标题跟着滚走 | P2。原语不提供 Header/Body/Footer 结构；只有「内层 wrapper 或 panelClassName 上 flex max-h + flex-col，正文 `min-h-0 flex-1 overflow-y-auto`」两种写法能让 footer 一直可见 | `pages/settings/endpoints/EndpointImportDialog.tsx:107,159`、`usage/UsageRecordDetailModal.tsx:223`、`shared/PromptPreviewButton.tsx:134`、`shared/ArchiveDiagnosticsDialog.tsx:89` | low |
| O-03（po-02） | 创建项目向导 step 1–3 | 1280x720 至 1440x900 均在首屏外；仅 2560x1440 完整显示 | 「下一步」「创建项目」「取消」在可滚动正文末尾，打开时看不到，正文又无滚动条提示 | P2。手写弹窗 `max-h-[92vh] flex flex-col`，头部约 200px；按钮由各 WizardStep 渲染在正文滚动区内，没有固定 footer | `pages/CreateProjectModal.tsx:413,471-505`、`WizardStep1Basics.tsx:279+` | medium |
| O-05（po-05） | 创建项目向导打开时 | 全部 | 在遮罩上滚轮，背后的项目列表跟着滚动 | P2。手写 `fixed inset-0` 弹窗未走 ModalShell，拿不到 body 滚动锁；`inert` 不阻止文档滚动 | `pages/CreateProjectModal.tsx:390-407`、`ui/ModalShell.tsx:51-69` | low |
| O-06（st-11、po-07、hist-08） | Popover/GlassPopover 浮层（分集面板、通知抽屉、DropdownPill、ActionMenu 等）及 ProviderModelSelect | 约 430–550px 高及以下或浏览器放大（潜在风险，未复现截断） | 浮层高度不随可用空间收缩，被视口截断后 `overflow-hidden` 面板内的选项无法到达 | P2。Popover 只在传 `maxHeight` 时启用 size middleware，全仓 3 处传了；GlassPopover 硬加 `overflow-hidden`；各列表写死 `max-h-60`、`max-h-[360px]`、`max-h-[28rem]`；ProviderModelSelect 的 size 只同步宽度 | `ui/Popover.tsx:58-61,90-101`、`ui/GlassPopover.tsx:26`、`ui/DropdownPill.tsx:64-72`、`ui/ProviderModelSelect.tsx:111-128`、`layout/WorkspaceNotificationsDrawer.tsx:126`、`layout/ProjectStatusBar.tsx:302` | low |
| O-07（po-09） | 全局 Toast | 全部 | 长错误信息让 toast 一直向下长（实测 1440x700 下 bottom 1058），视口外文字无法查看；长 URL 或 JSON 还会横向溢出；新 toast 覆盖旧 toast | P2。文本 span 只有 `max-w-sm`，无 max-h、line-clamp、overflow、break-words；store 为单值 | `layout/ToastOverlay.tsx:55-64`、`stores/app-store.ts:217-221` | low |
| O-04（po-04） | 创建项目向导切换步骤 | 1440x700 | 新一步继承上一步的 scrollTop（351、169），标题和首个字段被滚走；与 O-03 叠加后每一步都停在底部 | 其他。三步共用同一个正文节点，没有 key，也不重置 scrollTop | `pages/CreateProjectModal.tsx:471` | low |
| O-08（po-08） | `max-w-2xl` 的 GlassModal：PromptPreview、ArchiveDiagnostics、SourceUpload、EndpointImport、MarketInstall | 越宽越严重（2560 下宽 2458px） | 弹窗撑满 96vw，长文本单行极长 | 其他（非滚动）。ModalShell 固定 `max-w-[96vw]`，Tailwind v4 输出中 `.max-w-2xl` 排在它前面被覆盖；同节点类名冲突，项目无 tailwind-merge | `ui/ModalShell.tsx:113-118`、`ui/GlassModal.tsx:61`、`shared/PromptPreviewButton.tsx:98` | low |

已修复的历史实例（ShotDetail 三栏、UnitList、MentionPicker、EndpointSelect 弹层、ProviderModelSelect 被裁）不计入当前实例，见第 2 节。

## 4. 布局链图

记号：`[断点 X]` 表示该层导致对应实例；`OK` 表示该层链条正确。

### 剧集页（Studio）

```
body.min-h-screen
└ #app-root
  └ div.flex.h-screen.flex-col                       [断点 G-03：100vh，无 dvh]
    ├ GlobalHeader / DemoReadOnlyBanner
    └ div.flex.flex-1.overflow-hidden
      ├ AssetSidebar (flex-col overflow-hidden → flex-1 overflow-y-auto)   OK
      ├ main.flex-1.overflow-hidden                    [断点 W-01/W-02：溢出被裁，非 flex 容器，子节点靠 h-full]
      │ └ div.flex.h-full.flex-col                     (StudioCanvasRouter 路由容器)
      │   ├ WorkflowPanel (border-b px-4 py-2)         [断点 W-01：不限高、不滚动、不收缩]
      │   ├ TextTaskFailureNote / ScriptPlanHost / AdScriptHost / PromptAuthoringHost / EpisodeViewSwitch
      │   └ div.min-h-0.flex-1                         (被压到 0)
      │     ├ TimelineCanvas: flex h-full flex-col overflow-hidden
      │     │ ├ EpisodeHeader / tab 栏 / AdScriptProgress   [断点 W-02：第二层头部栈]
      │     │ └ min-h-0 flex-1 overflow-hidden → ShotSplitView grid overflow-hidden
      │     │   └ ShotList / ShotDetail: h-full min-h-0 overflow-y-auto   (唯一滚动层)
      │     ├ ReferenceVideoCanvas: 三层 flex min-h-0 flex-1 flex-col overflow-hidden
      │     │ ├ 中栏: 编辑器 min-h-0 flex-1 + SourceTextReadonly shrink-0   [断点 W-03：收缩规则放反]
      │     │ └ 右栏 UnitPreviewPanel: flex-col overflow-y-auto
      │     │   └ aspect-video overflow-hidden (无 shrink-0)            [断点 W-04]
      │     ├ EpisodeSourceReview: flex h-full flex-col p-6 (无 overflow)
      │     │ └ GuideSection overflow-hidden + 原文 min-h-0 flex-1 overflow-y-auto   [断点 W-02]
      │     └ EditTimelineView: h-full overflow-y-auto
      │       └ overflow-x-auto 时间线                 [断点 W-05：横向滚动条随页滚出视口]
      └ Agent 面板 (shrink-0 overflow-hidden → AgentCopilot flex h-full flex-col)
        ├ 消息区 flex-1 overflow-y-auto
        ├ PendingQuestionWizard / TodoListPanel overflow-hidden   [断点 A-03]
        └ textarea overflow-hidden (JS 测高)            [断点 A-01]
```

概览、lorebook、分集列表页均为 `h-full overflow-y-auto` 或两栏 `min-h-0 overflow-y-auto`，各视口正常。

### 系统设置

```
div.relative.flex.h-screen.flex-col                  [断点 S-02：sr-only 的包含块落在这里；根无 overflow-hidden]
├ header (shrink-0 sticky, 77px)
└ div.flex.min-h-0.flex-1
  ├ nav.w-[220px].overflow-y-auto                     OK
  └ main.min-w-0.flex-1.overflow-y-auto               [断点 S-02：未定位，裁不住 absolute 后代]
    ├ 普通分区: div.mx-auto.max-w-4xl.px-8            (使用记录表 overflow-x-auto [S-03])
    └ 供应商 / 调用端点: div.flex                      (直接子节点无 h-full)
      ├ nav.sticky.top-0.max-h-screen.self-start.overflow-y-auto   [断点 S-01：100vh > main 可视高度]
      └ 详情 / 表单 (自然流，sticky bottom-0 操作栏)
```

### 项目设置

```
div.fixed.inset-0.z-50.flex.flex-col
├ header
├ div.min-h-0.flex-1.overflow-y-auto                  OK
│ └ StylePicker grid.max-h-[420px].overflow-y-auto    [断点 S-04：长页面内嵌滚动]
└ footer (main 区 pb-24，不遮挡)                       OK
```

### 项目列表 / 资产库

```
body → div.relative.min-h-screen                      (文档滚动)
└ 文档根滚动条                                         [断点 G-02：永不显示]
```

### 弹窗（ModalShell / GlassModal）

```
div.fixed.inset-0.flex.items-center.justify-center.px-4   [断点 O-01：不可滚，无 py]
└ div[role=dialog].relative.max-w-[96vw]                   [断点 O-01：无 max-h；O-08：覆盖 max-w-2xl]
  └ GlassModal panel: arc-glass-panel overflow-hidden      [断点 O-01：内容被裁]
    └ 调用方内容 (限高与滚动各自实现，5 种写法)              [O-02]
```

CreateProjectModal 为手写 `fixed inset-0`：`max-h-[92vh] flex flex-col` → 头部约 200px → `min-h-0 flex-1 overflow-y-auto` 正文（内含按钮 [O-03]，跨步复用 [O-04]），未锁 body 滚动 [O-05]。

## 5. 系统性规则建议（供重构 Spec 采用）

### 5.1 页面外壳契约

1. **只有一种根高度策略。** 所有页面走同一个 AppShell：根 `h-dvh overflow-hidden`，文档本身永不滚动。取消 `h-screen`、`fixed inset-0` 页面、`min-h-screen` 文档滚动三种并存的策略。需要扣除全局提示条时用 CSS 变量（参考已删除的 `--auth-banner-h` 方案），不在组件里写 `100vh` 或 `max-h-screen`。
2. **滚动只发生在指定的滚动区。** 每个页面区域声明唯一的滚动层（第 5.2 节的 ScrollArea）。中间包装层只写 `min-h-0 flex-1`（行方向用 `min-w-0`），不写 `overflow-hidden`；`overflow-hidden` 只用于刻意的视觉裁切（圆角媒体、line-clamp）。
3. **flex/grid 链规则。** 从外壳到滚动区的每个 flex 或 grid 祖先都写 `min-h-0`；grid 行模板用 `minmax(0,1fr)`。滚动区的直接子项一律 `shrink-0`，内容多时让滚动区溢出，而不是把子项压扁（W-04）。
4. **头部区有高度预算。** `flex-col` 中位于主体之上的兄弟节点必须 `shrink-0`，且每个可变高的辅助块（WorkflowPanel、提示条、进度、导览）必须声明 `max-h` 并自带滚动，或改为 Sheet/Popover（W-01、W-02）。禁止在一个 `flex-col` 里放多个无上限的兄弟节点。
5. **主内容有下限。** 编辑器等主内容设 `min-h`，次要内容（只读原文）可以收缩或折叠；可用高度低于下限时由外层滚动区兜底（W-03）。
6. **主从布局各栏独立滚动。** 外层 `flex h-full min-h-0`，每栏 `h-full min-h-0 overflow-y-auto`。禁止在子滚动容器里用 `sticky + max-h-screen` 模拟侧栏（S-01）。
7. **滚动容器必须定位。** 滚动区带 `relative`，使 `sr-only` 等绝对定位后代不能逃逸（S-02）。
8. **长页面内不嵌套滚动区。** 嵌套列表全量展示，或改为 Dialog/Sheet 选择器（S-04）。

### 5.2 统一 ScrollArea 原语

1. **先清场。** 删除 `css/styles.css` 的滚动条规则和 `main.tsx` 的 `data-scrolling` 监听，只保留一套滚动条样式。这一步可以独立先做，用于区分「真的滚不动」和「看不到滚动条」。
2. **静止时可见。** thumb 在静止时必须有可见的不透明度，宽度不低于 6px；横向滚动同理。
3. **实现选择（按「更简更优」优先）：** 推荐原生 `overflow-auto` 加一个薄封装组件 `ScrollArea`，封装 `relative min-h-0 overflow-auto`、方向参数和可选的边缘渐隐遮罩；滚动条样式全局只定义一处。shadcn/Radix ScrollArea（`type="auto"` 或 `"always"`）可作为备选，但它自绘滚动条、需要确定高度的 viewport，复杂度更高，建议只在原生方案在 Electron/macOS 上达不到效果时采用。
4. **样式机制只选一种。** Chromium 121 起，元素一旦设置 `scrollbar-width` 或 `scrollbar-color`，就会忽略该元素的 `::-webkit-scrollbar` 规则（G-04）。标准属性在 macOS 系统设置为「滚动时显示」时是否仍会自动隐藏，以及 `::-webkit-scrollbar` 在 Electron 中的表现，需在桌面壳和 Chrome 中实测后再定〔待确认〕。

### 5.3 弹层规则

1. **Dialog 原语负责限高。** Content 固定 `max-h-[calc(100dvh-2rem)] flex flex-col`；Header、Footer `shrink-0`；Body `min-h-0 flex-1 overflow-y-auto`，是唯一滚动区。或者遮罩本身可滚（`overflow-y-auto` + `grid place-items-center`），不再用 `flex items-center`。调用方不再传 `max-h`。
2. **主操作吸底。** Footer 在 Body 之外，始终可见（O-02、O-03）。可参考现有正确样板：ShareToMarketDialog、MarketSourcesDialog、SegmentRefsEditModal、AssetPickerModal。
3. **多步向导。** 每一步的 Body 用独立 key，或切换时把 scrollTop 重置为 0（O-04）。
4. **禁止手写 `fixed inset-0` 弹窗。** CreateProjectModal、AddCredentialModal、ApiKeysTab、ProjectSettingsPage、NotesDrawer、AgentHandoffHint 统一迁到 Dialog/Sheet，自动获得滚动锁和焦点陷阱（O-05）。
5. **Popover/Combobox 默认夹紧高度。** size middleware 默认启用，把面板夹到 `availableHeight`；面板 `flex flex-col`，内部列表 `min-h-0 flex-1 overflow-y-auto`（即 815b29669 的修法）。各处不再写死 `max-h-60`、`max-h-[360px]`（O-06）。floating-ui 只允许在 Popover 原语中直接引用。
6. **尺寸由原语的变体决定。** 宽度、最大宽度做成原语参数，或引入 `cn`（tailwind-merge）合并类名，避免同节点类名冲突（O-08）。
7. **Toast。** 改为队列；文本 `break-words`，加 `max-h` + 内部滚动或 line-clamp，完整内容可在通知抽屉查看（O-07）。

### 5.4 横向溢出的可视提示

1. 横向滚动区使用 ScrollArea 的横向模式，左右边缘按滚动位置显示渐隐遮罩（JS 根据 scrollLeft 和 ResizeObserver 写 `data-overflow-start/end`，或用 `mask-image`）。
2. 数据表格用 `table-fixed` + colgroup 明确列宽；状态等短标签列 `whitespace-nowrap`；截断列带 `title`（S-03）。
3. 时间线这类横向滚动区放在定高区域内，或让横向滚动条 sticky 在可视区底部，不随页面纵向滚出视口（W-05）。

### 5.5 JS 测量高度的约束

1. 优先用 CSS `field-sizing: content` + `max-h` + `overflow-y-auto` 实现自动增高，不写 JS。Electron（Chromium）可用；Web 端 Safari、Firefox 的支持情况需确认，不支持时回退到下一条〔待确认〕。
2. 需要 JS 时只允许一个 hook：在 `useLayoutEffect` 中随 value 重算，同时用 ResizeObserver 监听宽度；上限按容器可用高度而不是 `window.innerHeight` 计算；`border-box` 下补偿边框；到达上限后切换为 `overflow-y-auto`。
3. textarea 初始 class 不写 `overflow-hidden`。
4. 删除 AgentCopilot、MessageRow、PendingQuestionWizard、四种 lorebook 卡片中的手写 `scrollHeight` 测高，统一到原语（A-01、A-02）。

### 5.6 守卫

现有工具链：`pnpm check` = tsc + ESLint + knip + vitest；无 Playwright，本次探针用 agent-browser。

1. **ESLint（`no-restricted-syntax` 或小型自定义规则）：**
   - 禁止 className 中出现 `h-screen`、`max-h-screen`、`min-h-screen`、`100vh`，只允许在 AppShell 文件中使用 dvh 相关写法。
   - 禁止在 Dialog 原语之外使用 `fixed inset-0`。
   - 禁止在 textarea 原语之外读写 `scrollHeight`。
   - `no-restricted-imports`：`@floating-ui/react` 只允许在 Popover 原语中引用（恢复 0eeecf4d3 的规则）。
   - 禁止在 Dialog 正文和长页面中出现固定 `max-h-[...]` + `overflow-y-auto` 的嵌套滚动区（可先告警）。
2. **vitest：** jsdom 不做布局，只能断言结构（如 Dialog 渲染出 Header/Body/Footer、Body 带滚动类）。作用有限，不应作为主要守卫。
3. **浏览器探针（推荐，复用本次 agent-browser 探针脚本，或引入 Playwright）：** 在固定夹具项目上，按视口矩阵 1280x720、1440x700、1440x900、2560x1440 访问关键路由（剧集页展开/收起「制作进度」、参考视频、设置各分区、主要弹窗），页内脚本断言：
   - 外壳页 `document.scrollingElement.scrollHeight <= innerHeight`（S-02）。
   - 不在白名单（line-clamp、truncate、媒体裁切）中的 `overflow:hidden` 元素满足 `scrollHeight <= clientHeight + 1`（W-01 至 W-04）。
   - 主工作区滚动区高度不低于下限（例如画布 ≥ 200px，具体阈值待定）。
   - 打开的 dialog 的 `getBoundingClientRect()` 位于视口内，主操作按钮可见（O-01、O-03）。
   - 自动增高 textarea 在改变容器宽度后满足 `scrollHeight <= clientHeight` 或 `overflow-y` 为 `auto`（A-01、A-02）。
4. **截图类视觉探针**仅作辅助：agent-browser 的 headless 截图默认隐藏原生滚动条（`--hide-scrollbars`），无法用来验证滚动条可见性；滚动条样式需在有头浏览器或 Electron 中人工确认。

## 6. 附录

### 6.1 被驳回条目

- st-06（AgentCopilot 纵向栈挤掉输入框）：待答向导显示时输入框必然禁用，发送后清空，「向导 + 高输入框」组合实际不会出现；剩余的 TodoListPanel 问题并入 A-03。
- st-13（长页面中的小型嵌套滚动区：StylePicker 以外的工具调用结果、CancelConfirmDialog 等）：均为刻意限高且可滚动到达，加 `overscroll-contain` 反而更糟；唯一问题是滚动条不可见，已由 G-01 覆盖。
- 部分子结论已在复核中修正，未单列：pw-04 中「空白编辑器 textarea `min-h-[280px] flex-1` 撑破 main」仅为代码推断，未复现；st-14 与 po-06 中「GlassPopover/面板 `overflow-hidden` 与 `overflow-y-auto` 同节点只是碰巧生效」不成立，Tailwind v4 按属性确定性排序，`overflow-y` 稳定覆盖 `overflow`；ps-03 以截图中无滚动条为证据无效（headless 截图隐藏滚动条）。
- 工作区探针误报已排除：收起的 Agent 面板（已设 inert）、line-clamp-2 摘要（刻意截断）、input 横向溢出（正常行为）。

### 6.2 截图

多视口截图（共 155 张）与本地盘点的逐条对照留在维护者本地，未随本文公开。

### 6.3 测试限制

- agent-browser headless 环境下真实滚轮固定在 (0,0) 派发，PageDown/End 不触发原生滚动；可达性按 DOM（祖先 overflow、scrollTop）和 `element.focus()` 判断。
- 环境中无自定义供应商、无市场条目、资产库为空、无 Agent 会话，长选项列表、TodoListPanel 叠加、MarketInstallDialog 只做了代码分析。
- G-01 中「4px 轨道上 thumb 可见宽度为 0」来自 CSS 层叠推算与 served CSS 检查，未在有头浏览器中目视确认。
- 全程只读：唯一的确认框点了「取消」，向导中的临时输入未提交，Agent 输入与面板宽度测试后已还原。
