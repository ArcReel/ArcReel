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

哪个改动首次用到某个原语，就由它执行 `pnpm exec shadcn add <组件>`，并一起提交 CLI 引入的依赖。不预装暂时用不到的原语：未使用的原语文件和依赖仍由 knip 报告。CLI 统一通过 `pnpm exec` 调用，以使用 `package.json` 锁定的 `shadcn` 版本；组件源码来自线上 registry，锁定版本不能固定组件内容，因此对已安装组件不使用 `--overwrite`，以免覆盖本地修改。

### 生成文件按自有代码处理，有意改动在改动处写一行注释说明原因

生成文件照常接受全部 lint 规则，不对 `components/ui/` 整体放宽。lint 报出真问题时修改代码；确认是误报的，用行内 `eslint-disable-next-line <规则> -- 理由` 豁免。为满足本仓库规范而改变行为或样式时（例如给 Dialog 增加限高的 Body、把遮罩改为不透明），在改动处写一行注释说明原因，便于合并上游时区分本地改动与上游代码。单纯的 lint 修复不加注释。

### 照搬 Radix 版示例前，逐个实测 Base UI 的行为差异

shadcn 文档与社区示例多数基于 Radix，Base UI 版本有几处差异，类型检查发现不了：

- `DropdownMenuLabel` 必须放在 `DropdownMenuGroup` 内，否则运行时整页崩溃。
- `modal` 模式的 Popover，其 Popup 内必须有 Close。
- Portal 会多渲染一层 `<div>`，依赖 DOM 层级的选择器和样式要随之调整。

这些约束在包装层或调用处遵守。上表之外的组件第一次使用时，在浏览器里实测键盘、焦点和关闭行为后再推广。

### 原语出现缺陷或 major 升级时，用 `add --diff` 对照上游后手动合并

Dependabot 只升级 npm 包，不会重新生成 `components/ui/*.tsx`。`@base-ui/react` 与 `shadcn` 的 minor、patch 升级归入 `shadcn-stack` 分组；major 升级单独开 PR，在同一个 PR 中对已安装组件逐个运行 `pnpm exec shadcn add <组件> --diff`，手动合并上游改动并保留本地注释说明的改动。遇到原语缺陷时，先用 `add --diff` 确认上游是否已经修复。不定期全量跟进。

### 区域重做完成后，把目录登记进 `eslint.config.js` 的 `REWORKED_FILES`

`@shadcn/lint` 的样式规则与滚动、响应式守卫（禁止视口高度、原语之外的 `fixed inset-0` 与读写 `scrollHeight`、业务组件的视口断点前缀）只对 `REWORKED_FILES` 中的 glob 生效。重做某个区域的改动把该区域的目录加进列表，并让这些文件零报告；交付结束时，列表替换为 `src/**`。

需要放宽时，在 PR 描述中逐条列出，由审查判断：

- 新增的组件变体、`@shadcn/lint` 的 `allow` 或 `contracts` 条目。
- 登记进 `VIEWPORT_BREAKPOINT_ALLOWLIST` 的文件。只有外壳切换标准档与紧凑档、弹层宽度等确需按视口判断的场景可以登记；外壳内的组件一律使用容器查询。
