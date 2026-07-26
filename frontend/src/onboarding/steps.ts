/**
 * 首次使用引导的步骤大纲。
 *
 * 结构（顺序、锚点、路由）写在这里，文案全部走 `onboarding` 命名空间的 i18n key —— 两者
 * 分离，加语种不必碰结构，调顺序不必碰翻译。锚点一律引用 `anchors.ts` 的注册表，不写
 * 字面量。`route` 是该步所需落地的页面（`OnboardingTour` 据此在步骤切换前先行导航），
 * 省略表示不要求特定路由。
 *
 * 全程 12 步，跨三个页面：大厅段（欢迎 → 新建项目入口 → 设置入口）→ 设置页段（供应商
 * → 智能体）→ 回大厅（演示卡，进工作台的桥）→ 演示工作台段（项目概览 → 智能体 →
 * 角色/场景/道具 → 剧集分镜 → 导出）→ 收尾。每一次换页都由上一步高亮的入口提供动机：
 * 设置图标讲完进设置页，演示卡讲完进工作台，不存在没有来路的跳转。
 *
 * 步骤文案里指路用的名字（「供应商」「智能体」等）一律取被高亮元素在界面上的实际标签，
 * 不另造概念——用户照着文案在界面上找得到，才算指对了路。
 *
 * 工作台段落在演示项目的只读工作台上（`demo-project.ts`）。第 6 步的演示卡是
 * `interactive`：用户点卡片本身或点「下一步」都会进入演示工作台，引导顺势推进到工作台
 * 首步（判定见 `OnboardingTour.tsx`）；工作台段内部的换路由仍由 `route` 强制导航驱动。
 *
 * 收尾步落回大厅：文案让用户新建项目开始制作，落点就该是新建入口所在的页面，而不是停
 * 在只读演示工作台上。
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
      // 设置页的内容区由 `section` 查询参数驱动（`SystemConfigPage`），锚点只在侧栏
      // 入口上——不声明查询参数的话，两步之间内容区不会跟着切，讲智能体时右边还摆着
      // 供应商。取值须与 `SystemConfigPage` 的 SettingsSection id 一致。
      query: { section: "providers" },
    },
    {
      anchor: ONBOARDING_ANCHORS.settingsAgent,
      title: t("settings_agent_title"),
      body: t("settings_agent_body"),
      route: ROUTE_APP_SETTINGS,
      query: { section: "agent" },
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
      // 点卡片落到演示工作台（含其子路由）。落到这里算「顺着引导走」，引导顺势推进
      // 到下一步（工作台首步）；落到别处仍按强制导航拽回大厅。
      interactiveTarget: DEMO_WORKBENCH,
    },
    {
      anchor: ONBOARDING_ANCHORS.workbenchOverview,
      title: t("workbench_overview_title"),
      body: t("workbench_overview_body"),
      route: DEMO_WORKBENCH,
    },
    {
      anchor: ONBOARDING_ANCHORS.workbenchAgent,
      title: t("workbench_agent_title"),
      body: t("workbench_agent_body"),
      // 智能体面板挂在工作台布局壳上，所有工作台路由都渲染；留在概览页讲，省一次导航。
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
