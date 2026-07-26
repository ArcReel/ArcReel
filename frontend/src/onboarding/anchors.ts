/**
 * 引导锚点注册表。
 *
 * 锚点名在这里登记一次，两头都从这里引用：组件挂 `data-onboarding={ONBOARDING_ANCHORS.x}`，
 * 步骤定义写 `anchor: ONBOARDING_ANCHORS.x`。名字漂移因此是 typecheck 错误，而不是运行时
 * 静默丢掉高亮。改动带 `data-onboarding` 的元素时，回来核对本表与对应步骤的文案是否仍成立。
 *
 * | 锚点 | 步 | 指向 |
 * |---|---|---|
 * | `lobby-create-project` | 大厅 2 | 项目大厅顶栏的「新建项目」按钮 |
 * | `lobby-settings` | 大厅 3 | 项目大厅顶栏的设置图标（未配置齐全时带红点） |
 * | `settings-providers` | 设置 4 | 设置页侧栏「供应商」入口 |
 * | `settings-agent` | 设置 5 | 设置页侧栏「智能体」入口 |
 * | `lobby-demo-card` | 大厅 6 | 引导期间注入大厅的演示项目卡（进演示工作台的桥） |
 * | `workbench-overview` | 工作台 7 | 项目概览的项目概述卡 |
 * | `workbench-agent` | 工作台 8 | 演示工作台右侧的助手面板（静态演示对话） |
 * | `workbench-lorebook` | 工作台 9 | 角色集页面的卡片区 |
 * | `workbench-timeline` | 工作台 10 | 剧集分镜画布的镜头主体 |
 * | `workbench-export` | 工作台 11 | 顶栏的导出按钮 |
 *
 * 工作台五步落在演示项目的只读工作台上（见 `demo-project.ts`）。概述/角色集/分镜/导出
 * 的锚点挂在真实工作台组件上而不是演示专用的副本——演示与真实项目共用同一份实现，锚点
 * 因此对两者都成立；`workbench-agent` 是例外，真实助手面板是写路径、演示态不挂载，锚点
 * 挂在演示专用的 `DemoAssistantPanel` 上。
 *
 * 大厅第 1 步与收尾步是居中的气泡，不挂锚点。
 */

export const ONBOARDING_ANCHORS = {
  lobbyCreateProject: "lobby-create-project",
  lobbyDemoCard: "lobby-demo-card",
  lobbySettings: "lobby-settings",
  settingsProviders: "settings-providers",
  settingsAgent: "settings-agent",
  workbenchOverview: "workbench-overview",
  workbenchAgent: "workbench-agent",
  workbenchLorebook: "workbench-lorebook",
  workbenchTimeline: "workbench-timeline",
  workbenchExport: "workbench-export",
} as const;

export type OnboardingAnchor = (typeof ONBOARDING_ANCHORS)[keyof typeof ONBOARDING_ANCHORS];
