import { describe, expect, it } from "vitest";
import type { TFunction } from "i18next";
import enWorkflow from "@/i18n/en/workflow";
import viWorkflow from "@/i18n/vi/workflow";
import zhWorkflow from "@/i18n/zh/workflow";
import { admissionUnitViews, problemViews } from "./problem-views";

const CATALOGS = { en: enWorkflow, vi: viWorkflow, zh: zhWorkflow } as const;

function translatorFor(catalog: Record<string, string>) {
  return ((key: string, options?: { defaultValue?: string }) =>
    catalog[key] ?? options?.defaultValue ?? key) as unknown as TFunction<"workflow">;
}

/** 计划步骤里会出现的问题码：脚本结构的发声准入、项目数据升级，以及视频请求事实。 */
const PLAN_PROBLEM_CODES = [
  "mixed_speech",
  "needs_replan",
  "parse_failed",
  "empty_speaker",
  "project_migration_failed",
  "video_capability_unavailable",
  "video_supported_durations_missing",
  "video_supported_durations_invalid",
  "video_supported_durations_incompatible",
  "reference_capability_unavailable",
  "reference_supported_durations_missing",
  "reference_supported_durations_invalid",
  "reference_supported_durations_incompatible",
];

describe("计划提示的本地化", () => {
  it.each(Object.keys(CATALOGS))("%s 下每个问题码都有自己的译文", (locale) => {
    const catalog: Record<string, string> = CATALOGS[locale as keyof typeof CATALOGS];
    const t = translatorFor(catalog);
    const views = problemViews(
      t,
      PLAN_PROBLEM_CODES.map((code) => ({ code, action: "fix_input", unit_id: "E1S01", params: {} })),
    );
    const fallback = catalog.problem_fallback_fix_input;
    expect(views.filter((view) => view.summary === fallback).map((view) => view.key)).toEqual([]);
  });

  it("不认识的问题码按交回的动作给通用原因，不露出问题码", () => {
    const t = translatorFor(zhWorkflow);
    const [known, unknown] = problemViews(t, [
      { code: "provider_rejected_xyz", action: "configure_provider", unit_id: "E1S03", params: {} },
      { code: "provider_rejected_xyz", action: "brand_new_action", params: {} },
    ]);
    expect(known.summary).toBe("当前的模型配置不支持这次生成。");
    expect(known.unitId).toBe("E1S03");
    expect(unknown.summary).toBe("这个单元暂时不能生成。");
  });

  it("计划里的准入缺口没有本地化文案时按问题码陈述，不展示服务端原文", () => {
    const t = translatorFor(zhWorkflow);
    const [view] = admissionUnitViews(
      t,
      [
        {
          unit_id: "E1S02",
          admitted: false,
          problems: [
            { code: "mixed_speech", detail: "发声准入未通过：{\"reason\": \"character_and_narrator_mixed\"}", action: "replan_unit" },
          ],
        },
      ],
      (value) => `${value}s`,
    );
    expect(view.unitId).toBe("E1S02");
    expect(view.summary).toBe("这个单元同时有角色台词和旁白，需要拆开。");
    expect(view.detail).toBeNull();
  });
});
