---
globs:
  - "frontend/src/components/**"
  - "frontend/src/onboarding/**"
---

# 引导锚点防腐

首次使用引导的高亮点靠元素上的 `data-onboarding` 属性定位：锚点名登记在
`frontend/src/onboarding/anchors.ts`，步骤大纲在 `steps.ts`，文案在
`frontend/src/i18n/{zh,en,vi}/onboarding.ts`。锚点名本身有 typecheck 兜底，但「属性还挂在
页面上、元素还讲得通、文案还指得准」这三件事没有编译期约束——属性被删、挂载点挪进条件
分支、界面标签改名，typecheck 与现有测试都不报错，引导只在运行期降级成居中气泡或把用户
指向界面上不存在的名字。

改动带 `data-onboarding` 的元素时，连带核对：

- **属性还在，且元素仍无条件渲染。** 挂载点落到条件分支下（空态才渲染、数据到位才渲染、
  某个 tab 激活才渲染）等于让该步在常见路径上找不到锚点：引导不中止，等满 `ANCHOR_WAIT_MS`
  后降级成居中气泡，只在 console 留一条 warn。要挪就挪到同一屏内无条件挂载的容器上，并同步
  `anchors.ts` 注册表里那一行的说明。
- **步骤文案讲的还是这个元素。** 元素承载的功能变了、字段增删了、按钮的可用条件变了，对应
  步骤的正文跟着改，三语一起。
- **文案里指路的名字与界面标签一致。** 步骤提到的入口名（侧栏项、tab、按钮）一律取用户在
  界面上看到的那个标签，不另造概念；标签改名时同步改三语文案。

删除挂着锚点的元素时，把整条链一并处理：`anchors.ts` 的条目、`steps.ts` 的步骤、三语文案
key、`steps.test.ts` 与 `anchors.test.tsx` 的断言，不留孤儿锚点。
