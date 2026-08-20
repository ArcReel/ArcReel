import { describe, it, expect } from "vitest";
import enWorkflow from "@/i18n/en/workflow";
import viWorkflow from "@/i18n/vi/workflow";
import zhWorkflow from "@/i18n/zh/workflow";
import { WORKFLOW_ACTION_TYPES } from "@/types/workflow";

const CATALOGS = { en: enWorkflow, vi: viWorkflow, zh: zhWorkflow } as const;

describe("下一步动作的文案覆盖", () => {
  it.each(Object.keys(CATALOGS))("%s 为每个受控动作备了文案", (locale) => {
    const catalog: Record<string, string> = CATALOGS[locale as keyof typeof CATALOGS];
    const missing = WORKFLOW_ACTION_TYPES.filter((action) => !catalog[`action_${action}`]);
    expect(missing).toEqual([]);
  });

  it("登记过的动作一个都不落到「未知动作」兜底文案上", () => {
    // action_unknown 是运行时防线：后端先于前端上线新动作时，它保证这一步不被整个吞掉。
    // 但闭集里已登记的动作不得靠它充数。
    expect(zhWorkflow.action_unknown).toBeTruthy();
    const fallingBack = WORKFLOW_ACTION_TYPES.filter((action) => !(`action_${action}` in zhWorkflow));
    expect(fallingBack).toEqual([]);
  });
});
