
import type { TFunction } from "i18next";
import {
  ROUTE_APP_PROJECTS,
  ROUTE_APP_SETTINGS,
  EPISODE_VIEW_PARAM,
  WORKSPACE_ROUTE_CHARACTERS,
  WORKSPACE_ROUTE_EPISODES,
  type SettingsSection,
} from "@/app-routes";
import { ONBOARDING_ANCHORS } from "./anchors";
import { DEMO_PROJECT_NAME, DEMO_SCRIPTED_EPISODE } from "./demo-project";
import type { TourStep } from "./tour";

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
      query: { section: "providers" satisfies SettingsSection },
    },
    {
      anchor: ONBOARDING_ANCHORS.settingsAgent,
      title: t("settings_agent_title"),
      body: t("settings_agent_body"),
      route: ROUTE_APP_SETTINGS,
      query: { section: "arcreel-agent" satisfies SettingsSection },
    },
    {
      anchor: ONBOARDING_ANCHORS.lobbyDemoCard,
      title: t("lobby_demo_title"),
      body: t("lobby_demo_body"),
      route: ROUTE_APP_PROJECTS,
      interactive: true,
      interactiveTarget: DEMO_WORKBENCH,
    },
    {
      anchor: ONBOARDING_ANCHORS.workbenchAgent,
      title: t("workbench_agent_title"),
      body: t("workbench_agent_body"),
      route: DEMO_WORKBENCH,
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
      query: { [EPISODE_VIEW_PARAM]: "board" },
    },
    {
      anchor: ONBOARDING_ANCHORS.workbenchExport,
      title: t("workbench_export_title"),
      body: t("workbench_export_body"),
      route: DEMO_EPISODE,
    },
    { anchor: null, title: t("finish_title"), body: t("finish_body"), route: ROUTE_APP_PROJECTS },
  ];
}
