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
 * | `lobby-demo-card` | 大厅 3 | 引导期间注入大厅的演示项目卡 |
 * | `lobby-settings` | 大厅 4 | 项目大厅顶栏的设置图标（未配置齐全时带红点） |
 * | `settings-providers` | 设置 5 | 设置页侧栏「媒体供应商」入口 |
 * | `settings-agent` | 设置 6 | 设置页侧栏「Agent 配置」入口 |
 *
 * 大厅第 1 步与收尾步是居中的气泡，不挂锚点。
 */

export const ONBOARDING_ANCHORS = {
  lobbyCreateProject: "lobby-create-project",
  lobbyDemoCard: "lobby-demo-card",
  lobbySettings: "lobby-settings",
  settingsProviders: "settings-providers",
  settingsAgent: "settings-agent",
} as const;

export type OnboardingAnchor = (typeof ONBOARDING_ANCHORS)[keyof typeof ONBOARDING_ANCHORS];
