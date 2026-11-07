/**
 * First-use onboarding outline.
 *
 * Structure (order/anchors/routes) lives here; all copy uses `onboarding` i18n keys, separated
 * so new languages never change structure and reordering never changes translations. Reference `anchors.ts` registries only,
 * never literals. `route` identifies required landing pages (`OnboardingTour` navigates before switching steps);
 * omission requires no particular route.
 *
 * Twelve steps across three pages: lobby welcome → create/settings entries → settings providers
 * → ArcReel Agent → lobby demo cards bridging to workbenches → demo Agent → overview →
 * characters/scenes/props → storyboards → export → closing. Agent panels default expanded and visually prominent, so introduce them first. Highlighted prior entries motivate each navigation:
 * settings icons lead to settings, demo cards lead to workbenches, never unexplained jumps.
 *
 * Directional names such as Providers/ArcReel Agent reuse actual highlighted UI labels,
 * never new concepts, so users can locate destinations.
 *
 * Workbench steps run readonly demos (`demo-project.ts`). Step-6 cards are
 * `interactive`: card clicks or Next enter demos, advancing naturally to the first workbench
 * step (see `OnboardingTour.tsx`). Internal workbench route changes still force navigation through `route`.
 *
 * Closing returns to lobbies: copy asks users to create projects, so land where creation entries exist rather than
 * readonly demos.
 */

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

/** Three demo-workbench landing routes, lowercase for normalized literal `OnboardingTour` comparisons. */
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
      // Settings content follows `section` queries (`SystemConfigPage`), while anchors exist only on sidebar
      // entries; omitting queries would leave content unchanged between steps, with Providers still right
      // while describing Agent.
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
      // Read-only exception: this step navigates into demo workbenches without writing, so permit
      // interaction (tour.ts `interactive` semantics). Demo cards mount only while tours run,
      // immediately unmounting on exit without remaining write entries.
      interactive: true,
      // Card clicks reach demo workbenches/subroutes; treat these destinations as following onboarding and advance
      // to first workbench steps. Other destinations still trigger forced navigation back to lobbies.
      interactiveTarget: DEMO_WORKBENCH,
    },
    {
      anchor: ONBOARDING_ANCHORS.workbenchAgent,
      title: t("workbench_agent_title"),
      body: t("workbench_agent_body"),
      // Agent panels mount on all workbench-shell routes; describe them on overview pages without next-step navigation.
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
      // Episode views live in `view` queries; explicitly target storyboards, whose bodies host anchors.
      query: { [EPISODE_VIEW_PARAM]: "board" },
    },
    {
      anchor: ONBOARDING_ANCHORS.workbenchExport,
      title: t("workbench_export_title"),
      body: t("workbench_export_body"),
      // Headers mount on all workbench routes; remain on previous storyboard canvases to avoid unnecessary navigation.
      route: DEMO_EPISODE,
    },
    { anchor: null, title: t("finish_title"), body: t("finish_body"), route: ROUTE_APP_PROJECTS },
  ];
}
