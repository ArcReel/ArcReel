# 前端原语与测试打桩

安装或第一次使用 `components/ui/` 的原语、给原语组件写测试、给出站调用打桩时读本文。原语用法的审查规则在 `docs/standards/frontend.md`。

## 安装原语

哪个改动首次用到某个原语，就由它执行 `pnpm exec shadcn add <组件>`（使用 `package.json` 锁定的 `shadcn` 版本），并一起提交 CLI 引入的依赖。暂时用不到的原语不预装，knip 会报告未使用的文件和依赖。需要统一改造的成套原语（如各类弹层）可以先于使用方安装，未被引用的文件在 `knip.jsonc` 的 `ignore` 中临时登记，注释写明由首个使用方删除该条。已安装的组件不用 `--overwrite`：组件源码来自线上 registry，锁定版本固定不了组件内容，覆盖会冲掉本地改动。

## Base UI 与 Radix 的差异

shadcn 文档和社区示例大多基于 Radix，本仓库的原语底层是 Base UI（`components.json` 的 `style` 为 `base-nova`）。以下差异类型检查发现不了：

- `modal` 模式的 Popover，Popup 内必须有 Close。
- Portal 会多渲染一层 `<div>`，依赖 DOM 层级的选择器和样式要随之调整。
- Combobox、Select 的弹层在点击后异步打开，测试中用 `findByRole("listbox")` 或 `findByRole("option")` 等待。Combobox 的搜索框在弹层内，触发按钮带 `role="combobox"`。
- Checkbox、Radio 渲染为带角色的 `span`，禁用态是 `aria-disabled="true"`，`toBeDisabled()` 不成立。
- Collapsible 收起时卸载面板内容：先点开触发按钮再查询面板里的元素，展开状态看触发按钮的 `aria-expanded`。
- Slider 的滑块在 jsdom 中按角色查不到，需要角色测试的单值滑杆保留原生 `<input type="range">`。

上面没有列出的组件，第一次使用时先在浏览器里实测键盘、焦点和关闭行为。

## 原语样式

`no-restyle` 只放行布局类名：间距、边框、配色写在原语外的一层普通元素上，原语自身只用 margin 与它隔开。折叠触发器写成 `<CollapsibleTrigger render={<Button variant="ghost" size="sm" />}>`，展开图标用 `group-aria-expanded/button:rotate-90` 旋转。

`render` 传入 `Button` 等原语组件时，它上面的类名按该组件的契约核对；传入原生元素或 `Link` 等非原语组件时，按外层原语核对。自身不带样式的触发器与 `Collapsible` 登记在 `eslint.config.js` 的 `UNSTYLED_PRIMITIVES`，不限类别；其余原语仍只放行布局类名。

## 测试打桩

新增出站调用经 `API` class，测试才能在这一层打桩：`vi.spyOn(API, method)`。`api.ts` 本体的测试用手写 fetch / Response stub。单元测试与页面级套件（回放录制替身，见 `docs/standards/testing.md`）都不引入 msw。SSE 用 `src/test/fakeSseStream.ts` 的 `FakeSseStream`，由 `API.openProjectEventStream` / `API.openAssistantEntriesStream` 的 spy 返回；`openSseStream` 与 `api.ts` 的流式封装用 `src/test/fakeSseFetch.ts`。
