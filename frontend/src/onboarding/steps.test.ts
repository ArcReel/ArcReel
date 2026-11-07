/**
 * Step-outline shape: three lobby steps (welcome/create/settings), two settings steps, return for demo cards,
 * then five demo-workbench steps (Agent → overview → characters/scenes/props → storyboards → export), ending in lobbies.
 *
 * Types catch anchor drift, but only this catches accidentally deleted steps. Closing popovers contain Replay onboarding destinations,
 * which intermediate changes must never displace. Also assert `route`, catching wrong cross-page targets
 * here rather than awaiting `OnboardingTour` integration tests.
 */

import { describe, expect, it } from "vitest";
import type { TFunction } from "i18next";
import { ROUTE_APP_PROJECTS, ROUTE_APP_SETTINGS } from "@/app-routes";
import { ONBOARDING_ANCHORS } from "./anchors";
import { DEMO_PROJECT_NAME, DEMO_SCRIPTED_EPISODE } from "./demo-project";
import { buildTourSteps } from "./steps";

/** Copy is tested elsewhere; return keys unchanged for structure-only assertions. */
const t = ((key: string) => key) as unknown as TFunction<"onboarding">;

const WORKBENCH = `${ROUTE_APP_PROJECTS}/${DEMO_PROJECT_NAME}`;
const EPISODE = `${WORKBENCH}/episodes/${DEMO_SCRIPTED_EPISODE}`;

describe("buildTourSteps", () => {
  it("walks the lobby, settings and the demo workbench, then ends back in the lobby", () => {
    expect(buildTourSteps(t).map((s) => [s.anchor, s.title, s.route])).toEqual([
      [null, "welcome_title", ROUTE_APP_PROJECTS],
      [ONBOARDING_ANCHORS.lobbyCreateProject, "lobby_create_title", ROUTE_APP_PROJECTS],
      [ONBOARDING_ANCHORS.lobbySettings, "lobby_settings_title", ROUTE_APP_PROJECTS],
      [ONBOARDING_ANCHORS.settingsProviders, "settings_providers_title", ROUTE_APP_SETTINGS],
      [ONBOARDING_ANCHORS.settingsAgent, "settings_agent_title", ROUTE_APP_SETTINGS],
      [ONBOARDING_ANCHORS.lobbyDemoCard, "lobby_demo_title", ROUTE_APP_PROJECTS],
      [ONBOARDING_ANCHORS.workbenchAgent, "workbench_agent_title", WORKBENCH],
      [ONBOARDING_ANCHORS.workbenchOverview, "workbench_overview_title", WORKBENCH],
      [ONBOARDING_ANCHORS.workbenchLorebook, "workbench_lorebook_title", `${WORKBENCH}/characters`],
      [ONBOARDING_ANCHORS.workbenchTimeline, "workbench_timeline_title", EPISODE],
      [ONBOARDING_ANCHORS.workbenchExport, "workbench_export_title", EPISODE],
      [null, "finish_title", ROUTE_APP_PROJECTS],
    ]);
  });

  it("declares the settings section for both settings steps so the content pane follows the tour", () => {
    const settingsSteps = buildTourSteps(t).filter((s) => s.route === ROUTE_APP_SETTINGS);

    expect(settingsSteps.map((s) => s.query)).toEqual([{ section: "providers" }, { section: "arcreel-agent" }]);
  });

  it("lands the shot step on the board view, where the anchor is mounted", () => {
    const timeline = buildTourSteps(t).find((s) => s.anchor === ONBOARDING_ANCHORS.workbenchTimeline);

    expect(timeline?.query).toEqual({ view: "board" });
  });

  it("declares the demo card's landing route so the guard can tell 'followed the tour' from 'wandered off'", () => {
    const demoCard = buildTourSteps(t).find((s) => s.anchor === ONBOARDING_ANCHORS.lobbyDemoCard);

    expect(demoCard?.interactive).toBe(true);
    expect(demoCard?.interactiveTarget).toBe(WORKBENCH);
  });

  it("keeps every tour route lowercase — OnboardingTour compares against a lowercased location", () => {
    for (const step of buildTourSteps(t)) {
      expect(step.route ?? "").toEqual((step.route ?? "").toLowerCase());
    }
  });
});
