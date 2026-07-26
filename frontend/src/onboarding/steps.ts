/**
 * 首次使用引导的步骤大纲。
 *
 * 结构（顺序、锚点、路由）写在这里，文案全部走 `onboarding` 命名空间的 i18n key —— 两者
 * 分离，加语种不必碰结构，调顺序不必碰翻译。锚点一律引用 `anchors.ts` 的注册表，不写
 * 字面量。`route` 是该步所需落地的页面（`OnboardingTour` 据此在步骤切换前先行导航），
 * 省略表示不要求特定路由。
 *
 * 全程 11 步，跨三个页面：大厅段（欢迎 → 新建项目入口 → 演示卡 → 设置入口）→ 设置页段
 * （供应商 → 智能体）→ 演示工作台段（项目概览 → 角色集 → 剧集分镜 → 导出）→ 收尾。
 *
 * 步骤文案里指路用的名字（「供应商」「角色集」等）一律取被高亮元素在界面上的实际标签，
 * 不另造概念——用户照着文案在界面上找得到，才算指对了路。
 *
 * 工作台段落在演示项目的只读工作台上（`demo-project.ts`），由 `route` 强制导航带入——
 * 用户不需要、也不被要求自己点进去。大厅第 3 步的演示卡仍是 `interactive`，用户主动点
 * 进工作台时引导挂起、回到大厅后从原位续讲（判定见 `OnboardingTour.tsx`）。
 *
 * 收尾步落回大厅：文案让用户「从导入一本小说开始」，落点就该是那个入口所在的页面，而
 * 不是停在只读演示工作台上。
 */

import type { TFunction } from "i18next";
import { ROUTE_APP_PROJECTS, ROUTE_APP_SETTINGS, WORKSPACE_ROUTE_CHARACTERS, WORKSPACE_ROUTE_EPISODES } from "@/app-routes";
import { ONBOARDING_ANCHORS } from "./anchors";
import { DEMO_PROJECT_NAME, DEMO_SCRIPTED_EPISODE } from "./demo-project";
import type { TourStep } from "./tour";

/** 演示工作台的三条落地路由。全小写——`OnboardingTour` 归一化后按字面比对当前路由。 */
const DEMO_WORKBENCH = `${ROUTE_APP_PROJECTS}/${DEMO_PROJECT_NAME}`;
const DEMO_LOREBOOK = `${DEMO_WORKBENCH}/${WORKSPACE_ROUTE_CHARACTERS}`;
const DEMO_EPISODE = `${DEMO_WORKBENCH}/${WORKSPACE_ROUTE_EPISODES}/${DEMO_SCRIPTED_EPISODE}`;

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
      // 点卡片落到演示工作台（含其子路由）。落到这里算「顺着引导走」，引导挂起等用户
      // 回来；落到别处仍按强制导航拽回大厅。
      interactiveTarget: DEMO_WORKBENCH,
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
    {
      anchor: ONBOARDING_ANCHORS.workbenchOverview,
      title: t("workbench_overview_title"),
      body: t("workbench_overview_body"),
      route: DEMO_WORKBENCH,
    },
    {
      anchor: ONBOARDING_ANCHORS.workbenchLorebook,
      title: t("workbench_lorebook_title"),
      body: t("workbench_lorebook_body"),
      route: DEMO_LOREBOOK,
    },
    {
      anchor: ONBOARDING_ANCHORS.workbenchTimeline,
      title: t("workbench_timeline_title"),
      body: t("workbench_timeline_body"),
      route: DEMO_EPISODE,
    },
    {
      anchor: ONBOARDING_ANCHORS.workbenchExport,
      title: t("workbench_export_title"),
      body: t("workbench_export_body"),
      // 顶栏在所有工作台路由上都挂着，留在上一步的分镜画布讲，省掉一次无谓导航。
      route: DEMO_EPISODE,
    },
    { anchor: null, title: t("finish_title"), body: t("finish_body"), route: ROUTE_APP_PROJECTS },
  ];
}
