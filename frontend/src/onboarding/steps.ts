/**
 * 首次使用引导的步骤大纲。
 *
 * 结构（顺序、锚点、路由）写在这里，文案全部走 `onboarding` 命名空间的 i18n key —— 两者
 * 分离，加语种不必碰结构，调顺序不必碰翻译。锚点一律引用 `anchors.ts` 的注册表，不写
 * 字面量。`route` 是该步所需落地的页面（`OnboardingTour` 据此在步骤切换前先行导航），
 * 省略表示不要求特定路由。
 *
 * 覆盖大厅段（欢迎 → 新建项目入口 → 演示卡 → 设置入口）与设置页段（媒体供应商 → Agent
 * 配置），末尾接收尾气泡。工作台段的步骤留待后续段落插在设置页段与收尾之间。
 */

import type { TFunction } from "i18next";
import { ROUTE_APP_PROJECTS, ROUTE_APP_SETTINGS } from "@/app-routes";
import { ONBOARDING_ANCHORS } from "./anchors";
import type { TourStep } from "./tour";

export function buildTourSteps(t: TFunction<"onboarding">): TourStep[] {
  return [
    { anchor: null, title: t("welcome_title"), body: t("welcome_body"), route: ROUTE_APP_PROJECTS },
    {
      anchor: ONBOARDING_ANCHORS.lobbyCreateProject,
      title: t("lobby_create_title"),
      body: t("lobby_create_body"),
      route: ROUTE_APP_PROJECTS,
    },
    {
      anchor: ONBOARDING_ANCHORS.lobbyDemoCard,
      title: t("lobby_demo_title"),
      body: t("lobby_demo_body"),
      route: ROUTE_APP_PROJECTS,
      // 全程只读的例外：这一步的落点动作是导航进演示工作台，不是写操作，因此开放
      // 交互（见 tour.ts 的 `interactive` 语义）；演示卡本身仅在引导运行期间挂载，
      // 退出后随即卸载，不留可写入口。
      interactive: true,
    },
    {
      anchor: ONBOARDING_ANCHORS.lobbySettings,
      title: t("lobby_settings_title"),
      body: t("lobby_settings_body"),
      route: ROUTE_APP_PROJECTS,
    },
    {
      anchor: ONBOARDING_ANCHORS.settingsProviders,
      title: t("settings_providers_title"),
      body: t("settings_providers_body"),
      route: ROUTE_APP_SETTINGS,
    },
    {
      anchor: ONBOARDING_ANCHORS.settingsAgent,
      title: t("settings_agent_title"),
      body: t("settings_agent_body"),
      route: ROUTE_APP_SETTINGS,
    },
    { anchor: null, title: t("finish_title"), body: t("finish_body") },
  ];
}
