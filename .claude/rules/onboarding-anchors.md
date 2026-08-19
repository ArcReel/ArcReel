---
paths:
  - "frontend/src/components/**"
  - "frontend/src/onboarding/**"
  - "frontend/src/i18n/*/onboarding.ts"
---

# 引导锚点一致性

首次使用引导的高亮点靠元素上的 `data-onboarding` 属性定位：锚点名登记在
`frontend/src/onboarding/anchors.ts`，步骤大纲在 `steps.ts`，文案在
`frontend/src/i18n/{zh,en,vi}/onboarding.ts`。锚点名本身有 typecheck 兜底，但「属性仍存在于
页面、元素语义仍成立、文案指向仍准确」这三件事没有编译期约束——属性被删、挂载点移入条件
分支、界面标签改名，typecheck 与现有测试都不报错，引导只在运行期降级为居中气泡或将用户
指向界面上不存在的名称。

改动带 `data-onboarding` 的元素时，一并核对：

- **属性仍在，且元素仍无条件渲染。** 挂载点落入条件分支（空态才渲染、数据就绪才渲染、
  某个 tab 激活才渲染）会使该步在常见路径上找不到锚点：引导不中止，等待 `ANCHOR_WAIT_MS`
  后降级为居中气泡，仅在 console 输出一条 warn。如需迁移，应移到同一屏内无条件挂载的容器上，
  并同步 `anchors.ts` 注册表中对应条目的说明。
- **步骤文案描述的仍是这个元素。** 元素承载的功能、字段、按钮可用条件发生变化时，对应
  步骤的正文须同步修改，三语一并更新。
- **文案中指引的入口名称与界面标签一致。** 步骤提到的入口名（侧栏项、tab、按钮）一律使用
  用户在界面上看到的标签，不另造概念；标签改名时同步修改三语文案。

删除带锚点的元素时，将整条链一并处理：`anchors.ts` 的条目、`steps.ts` 的步骤、三语文案
key、`steps.test.ts` 与 `anchors.test.tsx` 的断言，不留下孤立锚点。
