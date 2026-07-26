/**
 * 步骤大纲的形状：大厅三步（欢迎 + 新建项目 + 设置入口）跨页进设置页两步，回大厅讲演示卡，
 * 再进演示工作台五步，最后落回大厅收尾。
 *
 * 锚点名漂移由类型拦，步骤被顺手删掉只有这里拦 —— 收尾气泡里带着「重看引导」的去处，
 * 中间任何一段落地时都不该把它挤掉。`route` 一并断言，跨页步骤的导航目标写错也会在
 * 这里被抓到，而不必等到跑 `OnboardingTour` 的集成测试才发现。
 */

import { describe, expect, it } from "vitest";
import type { TFunction } from "i18next";
import { ROUTE_APP_PROJECTS, ROUTE_APP_SETTINGS } from "@/app-routes";
import { ONBOARDING_ANCHORS } from "./anchors";
import { DEMO_PROJECT_NAME, DEMO_SCRIPTED_EPISODE } from "./demo-project";
import { buildTourSteps } from "./steps";

/** 文案在别处测，这里只关心结构，所以把 key 原样返回。 */
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
      [ONBOARDING_ANCHORS.workbenchOverview, "workbench_overview_title", WORKBENCH],
      [ONBOARDING_ANCHORS.workbenchAgent, "workbench_agent_title", WORKBENCH],
      [ONBOARDING_ANCHORS.workbenchLorebook, "workbench_lorebook_title", `${WORKBENCH}/characters`],
      [ONBOARDING_ANCHORS.workbenchTimeline, "workbench_timeline_title", EPISODE],
      [ONBOARDING_ANCHORS.workbenchExport, "workbench_export_title", EPISODE],
      [null, "finish_title", ROUTE_APP_PROJECTS],
    ]);
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
