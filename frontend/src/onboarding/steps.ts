/**
 * 首次使用引导的步骤大纲。
 *
 * 结构（顺序、锚点）写在这里，文案全部走 `onboarding` 命名空间的 i18n key —— 两者分离，
 * 加语种不必碰结构，调顺序不必碰翻译。
 *
 * 当前是最小闭环：开场 + 收尾两步，都是居中气泡（`anchor: null`）。后续段落在中间插入
 * 带锚点的步骤。
 */

import type { TFunction } from "i18next";
import type { TourStep } from "./tour";

export function buildTourSteps(t: TFunction<"onboarding">): TourStep[] {
  return [
    { anchor: null, title: t("welcome_title"), body: t("welcome_body") },
    { anchor: null, title: t("finish_title"), body: t("finish_body") },
  ];
}
