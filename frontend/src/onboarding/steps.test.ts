/**
 * 步骤大纲的形状：大厅四步（欢迎 + 三个带锚点的入口）后面接收尾气泡。
 *
 * 锚点名漂移由类型拦，步骤被顺手删掉只有这里拦 —— 收尾气泡里带着「重看引导」的去处，
 * 中间任何一段落地时都不该把它挤掉。
 */

import { describe, expect, it } from "vitest";
import type { TFunction } from "i18next";
import { ONBOARDING_ANCHORS } from "./anchors";
import { buildTourSteps } from "./steps";

/** 文案在别处测，这里只关心结构，所以把 key 原样返回。 */
const t = ((key: string) => key) as unknown as TFunction<"onboarding">;

describe("buildTourSteps", () => {
  it("walks the lobby and ends on a centered wrap-up bubble", () => {
    expect(buildTourSteps(t).map((s) => [s.anchor, s.title])).toEqual([
      [null, "welcome_title"],
      [ONBOARDING_ANCHORS.lobbyCreateProject, "lobby_create_title"],
      [ONBOARDING_ANCHORS.lobbyDemoCard, "lobby_demo_title"],
      [ONBOARDING_ANCHORS.lobbySettings, "lobby_settings_title"],
      [null, "finish_title"],
    ]);
  });
});
