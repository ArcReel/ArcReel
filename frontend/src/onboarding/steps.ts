/**
 * 首次使用引导的步骤大纲。
 *
 * 结构（顺序、锚点）写在这里，文案全部走 `onboarding` 命名空间的 i18n key —— 两者分离，
 * 加语种不必碰结构，调顺序不必碰翻译。锚点一律引用 `anchors.ts` 的注册表，不写字面量。
 *
 * 当前覆盖大厅段：欢迎（居中气泡）→ 新建项目入口 → 演示卡 → 设置入口，末尾接收尾气泡。
 * 设置页段与工作台段的步骤插在设置入口与收尾之间。
 */

import type { TFunction } from "i18next";
import { ONBOARDING_ANCHORS } from "./anchors";
import type { TourStep } from "./tour";

export function buildTourSteps(t: TFunction<"onboarding">): TourStep[] {
  return [
    { anchor: null, title: t("welcome_title"), body: t("welcome_body") },
    {
      anchor: ONBOARDING_ANCHORS.lobbyCreateProject,
      title: t("lobby_create_title"),
      body: t("lobby_create_body"),
    },
    {
      anchor: ONBOARDING_ANCHORS.lobbyDemoCard,
      title: t("lobby_demo_title"),
      body: t("lobby_demo_body"),
    },
    {
      anchor: ONBOARDING_ANCHORS.lobbySettings,
      title: t("lobby_settings_title"),
      body: t("lobby_settings_body"),
    },
    { anchor: null, title: t("finish_title"), body: t("finish_body") },
  ];
}
