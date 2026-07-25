/**
 * 步骤大纲的形状：大厅四步（欢迎 + 三个带锚点的入口）跨页进入设置页两步，后面接收尾气泡。
 *
 * 锚点名漂移由类型拦，步骤被顺手删掉只有这里拦 —— 收尾气泡里带着「重看引导」的去处，
 * 中间任何一段落地时都不该把它挤掉。`route` 一并断言，跨页步骤的导航目标写错也会在
 * 这里被抓到，而不必等到跑 `OnboardingTour` 的集成测试才发现。
 */

import { describe, expect, it } from "vitest";
import type { TFunction } from "i18next";
import { ROUTE_APP_PROJECTS, ROUTE_APP_SETTINGS } from "@/app-routes";
import { ONBOARDING_ANCHORS } from "./anchors";
import { buildTourSteps } from "./steps";

/** 文案在别处测，这里只关心结构，所以把 key 原样返回。 */
const t = ((key: string) => key) as unknown as TFunction<"onboarding">;

describe("buildTourSteps", () => {
  it("walks the lobby, crosses into settings, and ends on a centered wrap-up bubble", () => {
    expect(buildTourSteps(t).map((s) => [s.anchor, s.title, s.route])).toEqual([
      [null, "welcome_title", ROUTE_APP_PROJECTS],
      [ONBOARDING_ANCHORS.lobbyCreateProject, "lobby_create_title", ROUTE_APP_PROJECTS],
      [ONBOARDING_ANCHORS.lobbyDemoCard, "lobby_demo_title", ROUTE_APP_PROJECTS],
      [ONBOARDING_ANCHORS.lobbySettings, "lobby_settings_title", ROUTE_APP_PROJECTS],
      [ONBOARDING_ANCHORS.settingsProviders, "settings_providers_title", ROUTE_APP_SETTINGS],
      [ONBOARDING_ANCHORS.settingsAgent, "settings_agent_title", ROUTE_APP_SETTINGS],
      [null, "finish_title", undefined],
    ]);
  });
});
